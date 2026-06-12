"""Synthetic submesoscale perturbation field for the reality-gap test.

SalishSeaCast is a 500 m mesoscale hindcast. Real ocean currents have
structure at submesoscale (O(10m–2km)) that the model doesn't resolve:
internal-tide patchiness, wind-driven surface micro-variability, small
eddies and fronts. For station-keeping, this unresolved energy acts as
a perturbation the controller can't anticipate.

This module generates a spatially+temporally correlated noise field
that we add to the mesoscale truth. The controller keeps using only
the mesoscale (its "knowledge"); the dynamics see mesoscale + noise.
Sweeping the noise amplitude quantifies the reality-gap sensitivity.

Noise model: OU-like via Gaussian smoothing of white noise. Spatial σ
sets the decorrelation length; temporal σ the decorrelation time. After
smoothing, the array is renormalised so the pointwise RMS matches the
requested target amplitude. Independent realisations per depth (no
vertical coherence claim).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]
from scipy.interpolate import RegularGridInterpolator  # type: ignore[import-not-found]
from scipy.ndimage import gaussian_filter  # type: ignore[import-not-found]


# Concurrency knob for the noise-field gaussian_filter draws. Each
# `_build_stationary_field_padded` call does 2 component draws (u, v),
# and `build_layered_noise_field` chains 6 such calls — all
# independent. scipy.ndimage's correlate1d C-extension releases the
# GIL during the kernel, so threading these builds parallelizes the
# expensive gaussian_filter wall.
#
# Default cap of 4 keeps oversubscription bounded when this code runs
# inside a multiprocessing.Pool worker (typical: 12-16 workers ×
# in-thread parallelism). Override via FLEET_NOISE_BUILD_THREADS env
# var when running in a single-process configuration where higher
# parallelism is appropriate.
def _noise_build_thread_workers() -> int:
    return max(1, int(os.environ.get("FLEET_NOISE_BUILD_THREADS", "4")))


@dataclass(frozen=True)
class SubmesoscaleField:
    """A pre-computed additive perturbation to truth currents.

    `interps`: per-depth (u, v) RegularGridInterpolator over
      (times_sec, lat_axis, lon_axis). NaN outside bounds.
    """

    interps: dict[float, tuple[RegularGridInterpolator, RegularGridInterpolator]]
    times_sec: np.ndarray
    lat_axis: np.ndarray
    lon_axis: np.ndarray
    target_sigma_ms: float

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        """Additive (u, v) perturbation at the nearest available depth."""
        keys = list(self.interps.keys())
        k = min(keys, key=lambda d: abs(d - depth_m))
        u_i, v_i = self.interps[k]
        u = float(u_i((t_sec, lat, lon)))
        v = float(v_i((t_sec, lat, lon)))
        # NaN → zero perturbation (the field doesn't degrade truth
        # beyond the bbox — the truth interpolator will also NaN there
        # and dynamics will skip that step).
        if not np.isfinite(u):
            u = 0.0
        if not np.isfinite(v):
            v = 0.0
        return u, v


def build_submesoscale_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
    target_sigma_ms: float,
    spatial_sigma_cells: float = 2.0,
    temporal_sigma_hours: float = 3.0,
    seed: int = 42,
) -> SubmesoscaleField:
    """Generate a smoothed-white-noise perturbation field at the requested
    depths and matching the cached dataset's (time, grid) axes.

    spatial_sigma_cells=2 at 500m grid → ~1 km e-folding length
    temporal_sigma_hours=3 → decorrelates over ~6 h (order of a tidal half-cycle)
    target_sigma_ms is the pointwise RMS after smoothing.

    A single 2-D+time noise realisation is shared across all depths so
    truth's vertical shear is preserved. Unresolved submesoscale
    structure (eddies, fronts, plume intrusions) is 3-D coherent on the
    O(10-100 m) vertical scale we sample here; per-depth independent
    draws would scramble truth's shear at σ-comparable magnitudes, and
    any depth-based controller would be operating on nonsense.
    """
    if target_sigma_ms <= 0.0:
        # Zero amplitude: return a trivial all-zeros interpolator.
        times_sec = ((ds["time"].values - ds["time"].values[0]) /
                     np.timedelta64(1, "s")).astype(float)
        lat_axis = bbox_lats_grid.mean(axis=1)
        lon_axis = bbox_lons_grid.mean(axis=0)
        flip_lat = lat_axis[0] > lat_axis[-1]
        flip_lon = lon_axis[0] > lon_axis[-1]
        if flip_lat:
            lat_axis = lat_axis[::-1]
        if flip_lon:
            lon_axis = lon_axis[::-1]
        nt = times_sec.size
        ny = lat_axis.size
        nx = lon_axis.size
        zeros = np.zeros((nt, ny, nx), dtype=float)
        interps = {}
        for d in target_depths_m:
            u_i = RegularGridInterpolator(
                (times_sec, lat_axis, lon_axis), zeros,
                bounds_error=False, fill_value=np.nan,
            )
            v_i = RegularGridInterpolator(
                (times_sec, lat_axis, lon_axis), zeros,
                bounds_error=False, fill_value=np.nan,
            )
            interps[d] = (u_i, v_i)
        return SubmesoscaleField(
            interps=interps, times_sec=times_sec,
            lat_axis=lat_axis, lon_axis=lon_axis,
            target_sigma_ms=0.0,
        )

    time_values = ds["time"].values
    t0 = time_values[0]
    times_sec = ((time_values - t0) / np.timedelta64(1, "s")).astype(float)
    lat_axis = bbox_lats_grid.mean(axis=1)
    lon_axis = bbox_lons_grid.mean(axis=0)
    flip_lat = lat_axis[0] > lat_axis[-1]
    flip_lon = lon_axis[0] > lon_axis[-1]
    if flip_lat:
        lat_axis = lat_axis[::-1]
    if flip_lon:
        lon_axis = lon_axis[::-1]

    nt = times_sec.size
    ny = lat_axis.size
    nx = lon_axis.size

    rng = np.random.default_rng(seed)

    def _draw_noise() -> tuple[np.ndarray, np.ndarray]:
        u = rng.standard_normal(size=(nt, ny, nx)).astype(np.float32)
        v = rng.standard_normal(size=(nt, ny, nx)).astype(np.float32)
        # Spatial smoothing per timestep.
        for ti in range(nt):
            u[ti] = gaussian_filter(u[ti], sigma=spatial_sigma_cells, mode="nearest")
            v[ti] = gaussian_filter(v[ti], sigma=spatial_sigma_cells, mode="nearest")
        # Temporal smoothing along axis 0.
        u = gaussian_filter(u, sigma=(temporal_sigma_hours, 0, 0), mode="nearest")
        v = gaussian_filter(v, sigma=(temporal_sigma_hours, 0, 0), mode="nearest")
        # Renormalise to target RMS.
        u_rms = float(np.sqrt(np.mean(u ** 2)))
        v_rms = float(np.sqrt(np.mean(v ** 2)))
        if u_rms > 0:
            u *= (target_sigma_ms / u_rms)
        if v_rms > 0:
            v *= (target_sigma_ms / v_rms)
        return u, v

    # Single 2-D+time draw reused across all depths: truth's vertical
    # shear is preserved; the operator's forecast error has the same
    # horizontal value at every depth.
    u_shared, v_shared = _draw_noise()
    interps: dict[float, tuple[RegularGridInterpolator, RegularGridInterpolator]] = {}
    for d in target_depths_m:
        u_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), u_shared,
            bounds_error=False, fill_value=np.nan,
        )
        v_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), v_shared,
            bounds_error=False, fill_value=np.nan,
        )
        interps[d] = (u_i, v_i)

    return SubmesoscaleField(
        interps=interps, times_sec=times_sec,
        lat_axis=lat_axis, lon_axis=lon_axis,
        target_sigma_ms=target_sigma_ms,
    )


def build_multiscale_noise_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
    sigma_fast_ms: float,       # "chop" — short-correlation, unlearnable
    sigma_slow_ms: float,       # persistent bias — long-correlation, learnable
    spatial_sigma_cells_fast: float = 1.0,
    temporal_sigma_hours_fast: float = 2.0,
    spatial_sigma_cells_slow: float = 4.0,
    temporal_sigma_hours_slow: float = 18.0,
    seed: int = 42,
) -> SubmesoscaleField:
    """Build a noise field that's the sum of two correlated processes:
    a short-timescale 'chop' and a long-timescale 'persistent bias'.

    Total RMS = sqrt(σ_fast² + σ_slow²). A bias-learning PF can recover
    the slow component but not the fast; so the post-learning residual
    σ is roughly σ_fast. This is the physically-honest model of
    operational forecast error. Each component uses a depth-coherent
    2-D+time realisation (`build_submesoscale_field`), preserving
    truth's vertical shear.

    Superseded for the Phase 2 canonical sweep by
    `build_layered_noise_field` (coh + surf·exp(-z/L_z) + white), which
    is physically correct on the vertical axis: upper-ocean forecast
    error is surface-intensified, not depth-coherent. Kept for scripts
    14, 18–21 which depend on the flat-σ interface.
    """
    fast = build_submesoscale_field(
        ds, bbox_lats_grid, bbox_lons_grid, target_depths_m,
        target_sigma_ms=sigma_fast_ms,
        spatial_sigma_cells=spatial_sigma_cells_fast,
        temporal_sigma_hours=temporal_sigma_hours_fast,
        seed=seed,
    )
    slow = build_submesoscale_field(
        ds, bbox_lats_grid, bbox_lons_grid, target_depths_m,
        target_sigma_ms=sigma_slow_ms,
        spatial_sigma_cells=spatial_sigma_cells_slow,
        temporal_sigma_hours=temporal_sigma_hours_slow,
        seed=seed + 1,
    )

    # Sum the two fields by sampling at each grid point and rebuilding
    # the interpolators. Simplest to just wrap in a compound sampler.
    @dataclass(frozen=True)
    class _Compound:
        fast: SubmesoscaleField
        slow: SubmesoscaleField

        def sample(self, lat, lon, depth_m, t_sec):
            uf, vf = self.fast.sample(lat, lon, depth_m, t_sec)
            us, vs = self.slow.sample(lat, lon, depth_m, t_sec)
            return uf + us, vf + vs

    # SubmesoscaleField has .sample via .interps; we return an object
    # that conforms to the same duck-type (has .sample). Caller can use
    # .sample just as for a real SubmesoscaleField.
    return _Compound(fast=fast, slow=slow)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Physically-structured layered noise (Phase 2.1, M1)
#
# Five-component decomposition matching distinct upper-ocean forecast-error
# mechanisms in central Strait of Georgia:
#
#   noise(x, y, z, t) = coh(x, y, t)                       — barotropic/tide residual
#                     + plume(x, y, t) · Π_plume(z)        — buoyant slab, sharp base
#                     + submeso_wind(x, y, t) · exp(-z/L_z_surf)
#                                                          — submesoscale + Ekman slab
#                     + inertial_u(x, y, t) · exp(-z/L_z_inr)
#                     + white(x, y, z, t)                  — unlearnable small-scale
#
# Each component has its own (σ, σ_s, σ_t) and vertical profile — none of
# them is a good approximation of the others. See
# `docs/reference/noise_model_design.md` §3 and the domain-practitioner
# review at `docs/reference/noise_model_boundary_review_2026-04-24.md`
# (oceanographer § Severity 1) for why lumping plume/wind/submeso/inertial
# into one `surf·exp(-z/L_z)` is physically wrong.
#
# The component list is FIXED (not a generic registry) — v1 of the
# layered physics. Enrichment (anisotropic submeso, separate baroclinic-
# tide vs estuarine residual, wind-direction-coupled plume advection) is
# deferred to a v2 refactor once the v3 latent bias prior is designed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StationaryField:
    """A single-component 2-D + time stationary Gaussian random field,
    built via pad-with-independent-noise + Gaussian filter + crop at
    3σ. Pre-renormalised to `target_sigma_ms` RMS (per-component).

    Separate (u, v) interpolators — independent draws. NaN outside the
    sim-safe interior (callers should treat NaN as zero at the bbox
    edge).
    """
    u_interp: RegularGridInterpolator
    v_interp: RegularGridInterpolator
    target_sigma_ms: float

    def sample(self, lat: float, lon: float, t_sec: float
                ) -> tuple[float, float]:
        u = float(self.u_interp((t_sec, lat, lon)))
        v = float(self.v_interp((t_sec, lat, lon)))
        if not np.isfinite(u):
            u = 0.0
        if not np.isfinite(v):
            v = 0.0
        return u, v

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized sample at N points, all at the same time."""
        N = lats.size
        pts = np.column_stack([
            np.full(N, t_sec, dtype=np.float64),
            lats, lons,
        ])
        u = np.asarray(self.u_interp(pts))
        v = np.asarray(self.v_interp(pts))
        # NaN-safety: out-of-domain → 0 (matches scalar sample()).
        u = np.where(np.isfinite(u), u, 0.0)
        v = np.where(np.isfinite(v), v, 0.0)
        return u, v


def _build_stationary_field_padded(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    *,
    target_sigma_ms: float,
    spatial_sigma_cells: float,
    temporal_sigma_hours: float,
    seed: int,
) -> _StationaryField:
    """Build a stationary 2-D + time Gaussian random field with
    option-B boundary handling.

    Algorithm (per independent draw for u, v):
      1. Allocate a *padded* white-noise cube of shape
         (nt + 2·m_t, ny + 2·m_y, nx + 2·m_x), where
         m_* = ceil(3 · σ_*). The pad is fresh independent
         standard-normal noise (NOT reflect/zero), so the filter
         never sees a clamp-repeat or mirror artefact.
      2. Run `gaussian_filter(..., mode="constant", cval=0.0)` over
         the padded cube. With the pad, the kernel never reaches the
         true boundary — the interior is statistically identical to
         an infinite-domain filtered field (to kernel-truncation
         tolerance ≈ exp(-4.5) ≈ 1%).
      3. Crop back to the original (nt, ny, nx) extent.
      4. Renormalise the cropped interior to hit `target_sigma_ms`.

    Returns u/v interpolators over (times_sec, lat_axis, lon_axis)
    with NaN outside — matching the legacy `SubmesoscaleField` shape.
    This is the v1 physically-correct replacement for the
    mode="nearest"-on-native-cube construction.
    """
    time_values = ds["time"].values
    t0 = time_values[0]
    times_sec = ((time_values - t0) / np.timedelta64(1, "s")).astype(float)
    lat_axis = bbox_lats_grid.mean(axis=1)
    lon_axis = bbox_lons_grid.mean(axis=0)
    flip_lat = lat_axis[0] > lat_axis[-1]
    flip_lon = lon_axis[0] > lon_axis[-1]
    if flip_lat:
        lat_axis = lat_axis[::-1]
    if flip_lon:
        lon_axis = lon_axis[::-1]

    nt = times_sec.size
    ny = lat_axis.size
    nx = lon_axis.size

    if target_sigma_ms <= 0.0:
        zeros = np.zeros((nt, ny, nx), dtype=np.float32)
        u_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), zeros,
            bounds_error=False, fill_value=np.nan,
        )
        v_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), zeros,
            bounds_error=False, fill_value=np.nan,
        )
        return _StationaryField(u_interp=u_i, v_interp=v_i, target_sigma_ms=0.0)

    # Pad by 3σ on each side — kernel truncation ≈ 1% past 3σ. 1 h per
    # time step, 1 cell per spatial step.
    m_t = int(np.ceil(3.0 * temporal_sigma_hours))
    m_y = int(np.ceil(3.0 * spatial_sigma_cells))
    m_x = int(np.ceil(3.0 * spatial_sigma_cells))

    rng = np.random.default_rng(seed)

    # Pre-generate both white-noise cubes SERIALLY so the rng stream is
    # consumed in a fixed order — keeps results bit-identical to the
    # pre-threading implementation. Only the (independent, GIL-releasing)
    # gaussian_filter + crop + normalize step runs in parallel below.
    padded_u = rng.standard_normal(
        size=(nt + 2 * m_t, ny + 2 * m_y, nx + 2 * m_x),
    ).astype(np.float32)
    padded_v = rng.standard_normal(
        size=(nt + 2 * m_t, ny + 2 * m_y, nx + 2 * m_x),
    ).astype(np.float32)

    def _filter_crop_norm(padded: np.ndarray) -> np.ndarray:
        smoothed = gaussian_filter(
            padded,
            sigma=(temporal_sigma_hours, spatial_sigma_cells, spatial_sigma_cells),
            mode="constant", cval=0.0,
        )
        cropped = smoothed[m_t:m_t + nt, m_y:m_y + ny, m_x:m_x + nx].copy()
        rms = float(np.sqrt(np.mean(cropped.astype(np.float64) ** 2)))
        if rms > 0:
            cropped *= (target_sigma_ms / rms)
        return cropped

    n_workers = min(2, _noise_build_thread_workers())
    if n_workers >= 2:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fu = ex.submit(_filter_crop_norm, padded_u)
            fv = ex.submit(_filter_crop_norm, padded_v)
            u_cube = fu.result()
            v_cube = fv.result()
    else:
        u_cube = _filter_crop_norm(padded_u)
        v_cube = _filter_crop_norm(padded_v)

    u_i = RegularGridInterpolator(
        (times_sec, lat_axis, lon_axis), u_cube,
        bounds_error=False, fill_value=np.nan,
    )
    v_i = RegularGridInterpolator(
        (times_sec, lat_axis, lon_axis), v_cube,
        bounds_error=False, fill_value=np.nan,
    )
    return _StationaryField(u_interp=u_i, v_interp=v_i,
                             target_sigma_ms=target_sigma_ms)


@dataclass(frozen=True)
class _InertialField:
    """Near-inertial wind-response field.

    Physics: at latitude φ the inertial frequency is f = 2 · Ω · sin(φ),
    period T_f = 2π/|f|. At 49°N, T_f ≈ 16.5 h. A wind burst injects a
    surface-trapped rotating velocity that decays via exp(-z / L_z) and
    rotates clockwise in the northern hemisphere.

    Representation: two independent stationary amplitude fields
    (c1, c2) — slowly varying across bbox and time (wind events are
    ~20 km, ~24 h). At sample time t the instantaneous velocity is:

        u(x,y,z,t) = [c1(x,y,t) · cos(f·t) + c2(x,y,t) · sin(f·t)] · exp(-z/L_z)
        v(x,y,z,t) = [-c1(x,y,t) · sin(f·t) + c2(x,y,t) · cos(f·t)] · exp(-z/L_z)

    (NH clockwise convention — negative angular velocity in math
    convention, handled by the sign of the sin terms.) Variance is
    stationary in t (var(c1)=var(c2)=σ² ⇒ var(u)=var(v)=σ² for all t)
    and E[u·v]=0 — isotropic rotation. Temporal autocorrelation
    E[u(t1)·u(t2)] ≈ σ² · cos(f·(t2-t1)) on timescales shorter than
    the amplitude decorrelation, which is exactly the near-inertial
    signature.
    """
    c1: _StationaryField
    c2: _StationaryField
    f_rad_per_sec: float
    L_z_m: float
    sigma_ms: float

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        c1u, c1v = self.c1.sample(lat, lon, t_sec)
        c2u, c2v = self.c2.sample(lat, lon, t_sec)
        # Apply the rotation to (c1u, c2u) → u  and (c1v, c2v) → v. Each
        # velocity component gets its own independent realisation so that
        # the rotation preserves isotropic (u, v) variance.
        cos_ft = float(np.cos(self.f_rad_per_sec * t_sec))
        sin_ft = float(np.sin(self.f_rad_per_sec * t_sec))
        u = c1u * cos_ft + c2u * sin_ft
        v = -c1v * sin_ft + c2v * cos_ft
        decay = float(np.exp(-max(depth_m, 0.0) / self.L_z_m))
        return u * decay, v * decay

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         depths: np.ndarray, t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized inertial sample. Same per-time rotation across all
        points; per-point depth-decay applied at the end."""
        c1u, c1v = self.c1.sample_batched(lats, lons, t_sec)
        c2u, c2v = self.c2.sample_batched(lats, lons, t_sec)
        cos_ft = float(np.cos(self.f_rad_per_sec * t_sec))
        sin_ft = float(np.sin(self.f_rad_per_sec * t_sec))
        u = c1u * cos_ft + c2u * sin_ft
        v = -c1v * sin_ft + c2v * cos_ft
        decay = np.exp(-np.maximum(depths, 0.0) / self.L_z_m)
        return u * decay, v * decay


def _profile_plume(depth_m: float, base_m: float, width_m: float) -> float:
    """Tanh plume profile — 1.0 at surface, ~0 below `base_m`, with a
    `width_m` transition zone. Matches Kastner 2018's plume observation:
    sharp halocline base, not an exponential tail."""
    return 0.5 * (1.0 - float(np.tanh((depth_m - base_m) / max(width_m, 0.1))))


# Earth rotation rate, rad/s (sidereal):
_EARTH_OMEGA_RAD_PER_SEC = 7.2921159e-5


def _inertial_frequency_rad_per_sec(latitude_deg: float) -> float:
    """NH clockwise inertial freq f = 2·Ω·sin(φ)."""
    return float(2.0 * _EARTH_OMEGA_RAD_PER_SEC
                 * np.sin(np.deg2rad(latitude_deg)))


@dataclass(frozen=True)
class LayeredNoiseField:
    """Physically-structured upper-ocean forecast-error field.

    Five independent additive components, each with its own (σ, σ_s,
    σ_t) and vertical profile. See module docstring and
    `docs/reference/noise_model_design.md` §3.

    Duck-types as `SubmesoscaleField` / `_Compound` via `.sample(lat,
    lon, z, t)` so the `RealCurrents` wrapper drops it in unchanged.
    """

    coh: _StationaryField
    plume: _StationaryField
    submeso_wind: _StationaryField
    inertial: _InertialField
    white: _StationaryField

    sigma_coh_ms: float
    sigma_plume_ms: float
    sigma_submeso_ms: float
    sigma_inertial_ms: float
    sigma_white_ms: float

    L_z_surf_m: float
    L_z_inertial_m: float
    plume_base_m: float
    plume_width_m: float

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        z = max(depth_m, 0.0)
        # 1) depth-coherent barotropic / baroclinic-tide residual
        uc, vc = self.coh.sample(lat, lon, t_sec)
        # 2) buoyant plume slab, tanh vertical profile
        up, vp = self.plume.sample(lat, lon, t_sec)
        p_z = _profile_plume(z, self.plume_base_m, self.plume_width_m)
        # 3) submesoscale + Ekman wind-slab, exp vertical profile
        us, vs = self.submeso_wind.sample(lat, lon, t_sec)
        surf_z = float(np.exp(-z / max(self.L_z_surf_m, 1e-6)))
        # 4) near-inertial rotating slab, exp vertical profile
        ui, vi = self.inertial.sample(lat, lon, z, t_sec)
        # 5) unlearnable small-scale white (no vertical structure)
        uw, vw = self.white.sample(lat, lon, t_sec)
        u = uc + p_z * up + surf_z * us + ui + uw
        v = vc + p_z * vp + surf_z * vs + vi + vw
        return u, v

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         depths: np.ndarray, t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized layered-noise sample at N points. Each point has
        its own depth; per-component RGI calls are batched over all
        N points (one call per component instead of N).
        """
        z = np.maximum(depths, 0.0)
        uc, vc = self.coh.sample_batched(lats, lons, t_sec)
        up, vp = self.plume.sample_batched(lats, lons, t_sec)
        p_z = 0.5 * (1.0 - np.tanh(
            (z - self.plume_base_m) / max(self.plume_width_m, 0.1)
        ))
        us, vs = self.submeso_wind.sample_batched(lats, lons, t_sec)
        surf_z = np.exp(-z / max(self.L_z_surf_m, 1e-6))
        ui, vi = self.inertial.sample_batched(lats, lons, depths, t_sec)
        uw, vw = self.white.sample_batched(lats, lons, t_sec)
        u = uc + p_z * up + surf_z * us + ui + uw
        v = vc + p_z * vp + surf_z * vs + vi + vw
        return u, v

    def surface_rms_ms(self) -> float:
        """Expected per-component RMS at z=0 from independent-Gaussian sum."""
        return float(np.sqrt(
            self.sigma_coh_ms ** 2
            + self.sigma_plume_ms ** 2       # p_z(0) ≈ 1 for base > 0
            + self.sigma_submeso_ms ** 2
            + self.sigma_inertial_ms ** 2
            + self.sigma_white_ms ** 2
        ))

    def deep_rms_ms(self) -> float:
        """Expected per-component RMS far below all surface-trapped layers."""
        return float(np.sqrt(
            self.sigma_coh_ms ** 2 + self.sigma_white_ms ** 2
        ))


def build_layered_noise_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    *,
    # Per-component RMS amplitudes (m/s). Defaults are central-SoG
    # April nominal, picked so total surface RMS ≈ 8 cm/s matches the
    # Halverson 2018 drifter-vs-CODAR anchor (central basin, away from
    # plume / tidal passes).
    sigma_coh_ms: float = 0.04,
    sigma_plume_ms: float = 0.02,
    sigma_submeso_ms: float = 0.05,
    sigma_inertial_ms: float = 0.04,
    sigma_white_ms: float = 0.015,
    # Plume vertical structure: tanh base at 5 m, 2 m transition.
    # Kastner 2018 plume is 0.5–10 m thick with a sharp halocline.
    plume_base_m: float = 5.0,
    plume_width_m: float = 2.0,
    # Surface-trapped e-fold depth: April central-SoG mixed-layer
    # depth (winter-deep, pre-freshet) is 15–25 m — use 20 m.
    L_z_surf_m: float = 20.0,
    L_z_inertial_m: float = 20.0,
    # Per-component spatial/temporal scales.
    coh_sigma_s_cells: float = 10.0,       # 5 km (barotropic residual)
    coh_sigma_t_hours: float = 36.0,
    plume_sigma_s_cells: float = 4.0,      # 2 km (plume fronts narrow)
    plume_sigma_t_hours: float = 24.0,
    submeso_sigma_s_cells: float = 10.0,   # 5 km (submeso + Ekman)
    submeso_sigma_t_hours: float = 12.0,
    inertial_sigma_s_cells: float = 40.0,  # 20 km (wind-event regional)
    inertial_sigma_t_hours: float = 24.0,
    white_sigma_s_cells: float = 2.0,      # 1 km
    white_sigma_t_hours: float = 3.0,
    # Reference latitude for inertial frequency.
    inertial_latitude_deg: float = 49.3,
    seed: int = 42,
) -> LayeredNoiseField:
    """Build a five-component layered noise field with option-B
    (pad-with-independent-noise + filter + crop) boundary handling.

    Each component is an independent draw keyed off `seed + N`; the
    inertial component needs two independent amplitude fields so it
    consumes two offsets. Total seed offsets used: 0 (coh), 1 (plume),
    2 (submeso), 3 (inertial.c1), 4 (inertial.c2), 5 (white).
    """
    # All 6 component builds are independent (distinct seeds, distinct
    # output cubes). Dispatch via ThreadPoolExecutor — combined with
    # the in-build u/v threading, scipy.ndimage's GIL-releasing C
    # kernels run truly in parallel up to ~12 way concurrency.
    component_specs = [
        ("coh", sigma_coh_ms, coh_sigma_s_cells, coh_sigma_t_hours, seed + 0),
        ("plume", sigma_plume_ms, plume_sigma_s_cells, plume_sigma_t_hours,
         seed + 1),
        ("submeso", sigma_submeso_ms, submeso_sigma_s_cells,
         submeso_sigma_t_hours, seed + 2),
        ("inertial_c1", sigma_inertial_ms, inertial_sigma_s_cells,
         inertial_sigma_t_hours, seed + 3),
        ("inertial_c2", sigma_inertial_ms, inertial_sigma_s_cells,
         inertial_sigma_t_hours, seed + 4),
        ("white", sigma_white_ms, white_sigma_s_cells, white_sigma_t_hours,
         seed + 5),
    ]

    def _build_one(spec):
        _, target_sigma, sigma_s, sigma_t, comp_seed = spec
        return _build_stationary_field_padded(
            ds, bbox_lats_grid, bbox_lons_grid,
            target_sigma_ms=target_sigma,
            spatial_sigma_cells=sigma_s,
            temporal_sigma_hours=sigma_t,
            seed=comp_seed,
        )

    n_workers = min(len(component_specs), _noise_build_thread_workers())
    if n_workers >= 2:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            built = list(ex.map(_build_one, component_specs))
    else:
        built = [_build_one(s) for s in component_specs]
    coh, plume, submeso_wind, inertial_c1, inertial_c2, white = built
    inertial = _InertialField(
        c1=inertial_c1, c2=inertial_c2,
        f_rad_per_sec=_inertial_frequency_rad_per_sec(inertial_latitude_deg),
        L_z_m=L_z_inertial_m, sigma_ms=sigma_inertial_ms,
    )
    return LayeredNoiseField(
        coh=coh, plume=plume, submeso_wind=submeso_wind,
        inertial=inertial, white=white,
        sigma_coh_ms=sigma_coh_ms, sigma_plume_ms=sigma_plume_ms,
        sigma_submeso_ms=sigma_submeso_ms,
        sigma_inertial_ms=sigma_inertial_ms,
        sigma_white_ms=sigma_white_ms,
        L_z_surf_m=L_z_surf_m, L_z_inertial_m=L_z_inertial_m,
        plume_base_m=plume_base_m, plume_width_m=plume_width_m,
    )


@dataclass(frozen=True)
class _StationaryScalarTimeSeries:
    """1-D smoothed Gaussian time series, drop-in for `_StationaryField`
    when the spatial coherence length exceeds the simulation domain.

    For σ_s ≫ bbox the 3-D cube is redundant — at any given time the
    field is approximately constant in space, so all the spatial
    structure the cube would carry decorrelates over distances larger
    than we ever sample. Modelling only the temporal axis preserves the
    physics (the basin-DC mode varies in time) at kilobytes-not-GB.

    Caller responsibility: only use this when σ_s really does exceed
    the bbox. Otherwise the spatial-uniform assumption is wrong.
    """

    series: np.ndarray             # (nt,) pre-renormalised to target_sigma
    times_sec: np.ndarray          # (nt,) ascending
    target_sigma: float

    def sample_at_time(self, t_sec: float) -> float:
        """1-D linear interp into the smoothed series; out-of-bounds → 0."""
        if t_sec < self.times_sec[0] or t_sec > self.times_sec[-1]:
            return 0.0
        return float(np.interp(t_sec, self.times_sec, self.series))


def _build_scalar_time_series_padded(
    ds: xr.Dataset, *,
    target_sigma: float,
    temporal_sigma_hours: float,
    seed: int,
) -> _StationaryScalarTimeSeries:
    """Build a 1-D smoothed Gaussian time series with option-B (pad +
    filter + crop) boundary handling — same construction as the 3-D
    cube, just in one dimension."""
    time_values = ds["time"].values
    t0 = time_values[0]
    times_sec = ((time_values - t0) / np.timedelta64(1, "s")).astype(float)
    nt = times_sec.size
    if target_sigma <= 0.0:
        return _StationaryScalarTimeSeries(
            series=np.zeros(nt, dtype=np.float32),
            times_sec=times_sec, target_sigma=0.0,
        )
    m_t = int(np.ceil(3.0 * temporal_sigma_hours))
    rng = np.random.default_rng(seed)
    padded = rng.standard_normal(size=nt + 2 * m_t).astype(np.float32)
    smoothed = gaussian_filter(
        padded, sigma=temporal_sigma_hours, mode="constant", cval=0.0,
    )
    cropped = smoothed[m_t:m_t + nt].copy()
    rms = float(np.sqrt(np.mean(cropped.astype(np.float64) ** 2)))
    if rms > 0:
        cropped *= (target_sigma / rms)
    return _StationaryScalarTimeSeries(
        series=cropped, times_sec=times_sec, target_sigma=target_sigma,
    )


@dataclass(frozen=True)
class LayeredTracerNoiseField:
    """Layered (T, S) bias field, calibrated to Soontiens 2017 SoG biases.

    Five components per realisation:
      - `coh_S`, `coh_T`: 1-D time series (σ_s=basin >> bbox, modelled as
         spatially constant). Carry the systematic basin offset that
         Soontiens reports — bias = `mean_*_coh + coh_*(t)`, where the
         mean is the systematic SoG sign (S typically -0.3..-0.7 g/kg,
         T typically +0.2..+0.5 °C) and `coh_*(t)` is the fluctuation
         around that mean on a multi-day timescale.
      - `plume_S`: 3-D cube, plume·tanh(z) profile (σ_s=plume scale,
         τ=1 day). Soontiens Fig 9 plume-front structure.
      - `white_S`, `white_T`: 3-D cubes, cell-scale anti-degeneracy floor
         (σ_s=cell, τ=hour). Without these the prior == truth in any
         cell where the smooth components happen to vanish.

    The plume-front + DC-offset interpretation is consistent with
    Soontiens' RMS-vs-mean reporting: the systematic offset is the mean,
    the σs are spatial fluctuation around the mean.

    `.sample(lat, lon, depth, t) -> (T_noise, S_noise)`, mirroring
    `TracerField.sample` so a `_RealTracer` wrapper can add the two.
    """

    coh_S: _StationaryScalarTimeSeries  # τ=7 d series; spatially constant
    plume_S: _StationaryField           # σ=0.3 g/kg, τ=1 d, plume·tanh(z)
    white_S: _StationaryField           # σ=0.1 g/kg, τ=1 h, cell scale
    coh_T: _StationaryScalarTimeSeries  # τ=7 d series; spatially constant
    white_T: _StationaryField           # σ=0.05 °C, τ=1 h, cell scale

    # Systematic Soontiens offsets — drawn once per simulation, define
    # the DC component of the bias the fleet aggregation should converge
    # toward in deployment.
    mean_S_coh_psu: float
    mean_T_coh_c: float

    sigma_S_coh_psu: float
    sigma_S_plume_psu: float
    sigma_S_white_psu: float
    sigma_T_coh_c: float
    sigma_T_white_c: float

    plume_base_m: float
    plume_width_m: float

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        """Return `(T_noise, S_noise)` at the query point. Out-of-domain
        per-component values default to zero; the systematic DC offsets
        are still applied so an off-domain query for the basin-mean bias
        returns the offset, not zero."""
        z = max(depth_m, 0.0)
        # Salinity: DC offset + basin time series + plume + white.
        s_coh = self.coh_S.sample_at_time(t_sec)
        s_plume, _ = self.plume_S.sample(lat, lon, t_sec)
        s_white, _ = self.white_S.sample(lat, lon, t_sec)
        p_z = _profile_plume(z, self.plume_base_m, self.plume_width_m)
        S_n = self.mean_S_coh_psu + s_coh + p_z * s_plume + s_white
        # Temperature: DC offset + basin time series + white.
        t_coh = self.coh_T.sample_at_time(t_sec)
        t_white, _ = self.white_T.sample(lat, lon, t_sec)
        T_n = self.mean_T_coh_c + t_coh + t_white
        return T_n, S_n

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         depths: np.ndarray, t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized N-point sample. Returns `(T_arr, S_arr)`."""
        z = np.maximum(depths, 0.0)
        # coh_* values are scalar at this t — broadcast across N points.
        s_coh_t = self.coh_S.sample_at_time(t_sec)
        s_plume, _ = self.plume_S.sample_batched(lats, lons, t_sec)
        s_white, _ = self.white_S.sample_batched(lats, lons, t_sec)
        p_z = 0.5 * (1.0 - np.tanh(
            (z - self.plume_base_m) / max(self.plume_width_m, 0.1)
        ))
        S_n = self.mean_S_coh_psu + s_coh_t + p_z * s_plume + s_white
        t_coh_t = self.coh_T.sample_at_time(t_sec)
        t_white, _ = self.white_T.sample_batched(lats, lons, t_sec)
        T_n = self.mean_T_coh_c + t_coh_t + t_white
        return T_n, S_n

    def surface_rms_S_psu(self) -> float:
        """Expected RMS of the spatial fluctuation at the surface
        (excludes the systematic DC offset, which is not a fluctuation).
        At z≈0 the plume profile ≈1."""
        return float(np.sqrt(
            self.sigma_S_coh_psu ** 2
            + self.sigma_S_plume_psu ** 2
            + self.sigma_S_white_psu ** 2
        ))

    def deep_rms_S_psu(self) -> float:
        """Expected fluctuation RMS far below the plume (plume profile ≈0)."""
        return float(np.sqrt(
            self.sigma_S_coh_psu ** 2 + self.sigma_S_white_psu ** 2
        ))

    def rms_T_c(self) -> float:
        """Expected fluctuation RMS for T (no vertical structure on T)."""
        return float(np.sqrt(
            self.sigma_T_coh_c ** 2 + self.sigma_T_white_c ** 2
        ))


def build_layered_tracer_noise_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    *,
    # Systematic basin-mean offsets per Soontiens 2017 Table 1.
    # SoG salinity is systematically too fresh in NEMO (-0.29..-0.67 g/kg);
    # temperature is systematically too warm (+0.24..+0.48 °C). These
    # are signed DC offsets, NOT fluctuation magnitudes.
    mean_S_coh_psu: float = -0.4,
    mean_T_coh_c: float = 0.35,
    # Per-component fluctuation RMS (around the offsets above).
    # Surface fluctuation RMS_S = √(0.5² + 0.3² + 0.1²) = 0.59 g/kg.
    sigma_S_coh_psu: float = 0.5,
    sigma_S_plume_psu: float = 0.3,
    sigma_S_white_psu: float = 0.1,
    sigma_T_coh_c: float = 0.4,
    sigma_T_white_c: float = 0.05,
    # Plume vertical profile (Kastner 2018 sharp halocline base).
    plume_base_m: float = 5.0,
    plume_width_m: float = 2.0,
    # Per-component temporal scales. coh: basin-mean drift ~7 days
    # (Soontiens reports approximate stationarity over 40-day NEMO runs).
    # plume: 1-day (river-discharge-driven plume-front timescale).
    # white: 1-hour (anti-degeneracy floor).
    sigma_t_coh_h: float = 168.0,         # 7 days, Soontiens-faithful
    sigma_t_plume_h: float = 24.0,        # 1 day
    sigma_t_white_h: float = 1.0,         # 1 hour
    # Per-component spatial scales (cells at 500 m grid). coh has no
    # spatial structure (modelled as a 1-D time series — see
    # `_StationaryScalarTimeSeries` for when this is valid).
    sigma_s_plume_cells: float = 10.0,    # 5 km plume scale
    sigma_s_white_cells: float = 1.0,     # 500 m anti-degeneracy floor
    seed: int = 42,
) -> LayeredTracerNoiseField:
    """Build the layered tracer-bias noise field.

    Calibration source: `references/soontiens2017.pdf`. Table 1 gives
    per-station mean S and T biases (systematic, signed); Fig 9 motivates
    the plume_S component for upper-50 m freshening near the river plume.

    The coh components are 1-D time series — appropriate when the
    basin-coherence length (≥30-50 km in SoG) exceeds the bbox extent
    (e.g., the central-SoG 22×33 km bbox). Switching to a 3-D cube would
    be needed only if the bbox grew to span multiple basin-mean modes.

    Seed offsets used: 0 (coh_S), 1 (plume_S), 2 (white_S),
    3 (coh_T), 4 (white_T).
    """
    coh_S = _build_scalar_time_series_padded(
        ds, target_sigma=sigma_S_coh_psu,
        temporal_sigma_hours=sigma_t_coh_h, seed=seed + 0,
    )
    plume_S = _build_stationary_field_padded(
        ds, bbox_lats_grid, bbox_lons_grid,
        target_sigma_ms=sigma_S_plume_psu,
        spatial_sigma_cells=sigma_s_plume_cells,
        temporal_sigma_hours=sigma_t_plume_h,
        seed=seed + 1,
    )
    white_S = _build_stationary_field_padded(
        ds, bbox_lats_grid, bbox_lons_grid,
        target_sigma_ms=sigma_S_white_psu,
        spatial_sigma_cells=sigma_s_white_cells,
        temporal_sigma_hours=sigma_t_white_h,
        seed=seed + 2,
    )
    coh_T = _build_scalar_time_series_padded(
        ds, target_sigma=sigma_T_coh_c,
        temporal_sigma_hours=sigma_t_coh_h, seed=seed + 3,
    )
    white_T = _build_stationary_field_padded(
        ds, bbox_lats_grid, bbox_lons_grid,
        target_sigma_ms=sigma_T_white_c,
        spatial_sigma_cells=sigma_s_white_cells,
        temporal_sigma_hours=sigma_t_white_h,
        seed=seed + 4,
    )
    return LayeredTracerNoiseField(
        coh_S=coh_S, plume_S=plume_S, white_S=white_S,
        coh_T=coh_T, white_T=white_T,
        mean_S_coh_psu=mean_S_coh_psu,
        mean_T_coh_c=mean_T_coh_c,
        sigma_S_coh_psu=sigma_S_coh_psu,
        sigma_S_plume_psu=sigma_S_plume_psu,
        sigma_S_white_psu=sigma_S_white_psu,
        sigma_T_coh_c=sigma_T_coh_c,
        sigma_T_white_c=sigma_T_white_c,
        plume_base_m=plume_base_m, plume_width_m=plume_width_m,
    )
