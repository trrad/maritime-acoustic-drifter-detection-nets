"""Shared truth-field interpolator factory + advection helpers.

Extracted from `08_drifter_trajectories.py` so Phase A (station-keeping
upper bound) and Phase B (degraded-knowledge sweep) both use the same
truth-interp path.

All coordinates are geographic:
  - lat_deg, lon_deg: WGS84 degrees
  - depth_m: positive down, meters
  - t_sec: seconds since the truth dataset's t0 (ds.time[0])
Velocities are m/s (u = eastward, v = northward).

The interpolator treats the SalishSeaCast NEMO curvilinear grid as
pseudo-regular (row-mean lat, column-mean lon) — accurate to O(meters)
over a 20×20 km bbox, good enough for prototype work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]
from scipy.interpolate import RegularGridInterpolator  # type: ignore[import-not-found]


EARTH_R_M = 111_320.0  # meters per degree latitude (and per degree longitude × cos(lat))


@dataclass(frozen=True)
class DepthInterp:
    """One target depth's (u, v) interpolators + the actual grid level we snapped to."""
    u: RegularGridInterpolator
    v: RegularGridInterpolator
    actual_depth_m: float


@dataclass(frozen=True)
class TruthField:
    """A per-depth interpolator bundle over a single truth dataset.

    `depths_m` is the list of target depths requested by the caller;
    each maps to the nearest SalishSeaCast grid level via `interps`.
    `times_sec` is the shared time axis (seconds since t0).
    `t0` is the first timestamp as np.datetime64.
    """
    interps: dict[float, DepthInterp]
    times_sec: np.ndarray
    t0: np.datetime64
    lat_axis: np.ndarray  # ascending, 1D
    lon_axis: np.ndarray  # ascending, 1D

    @property
    def depths_m(self) -> list[float]:
        return sorted(self.interps.keys())

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        """Sample (u, v) at the nearest available depth. Out-of-bounds → (nan, nan)."""
        interp = self._nearest(depth_m)
        u = float(interp.u((t_sec, lat, lon)))
        v = float(interp.v((t_sec, lat, lon)))
        return u, v

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         depths: np.ndarray, t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized sample at N points each with its own depth.

        Each point snaps to the nearest available depth slab; one
        RegularGridInterpolator call per slab amortises the per-call
        overhead across all points routed to that slab. With ~5 slabs
        and N >> 5 this is O(slabs) RGI calls instead of O(N).
        """
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        depths = np.asarray(depths, dtype=np.float64)
        N = lats.size
        u_arr = np.full(N, np.nan)
        v_arr = np.full(N, np.nan)
        # Assign each point to its nearest slab.
        depth_keys = np.array(sorted(self.interps.keys()), dtype=np.float64)
        slab_idx = np.argmin(np.abs(depth_keys[None, :] - depths[:, None]),
                              axis=1)
        for k_idx, key in enumerate(depth_keys):
            mask = (slab_idx == k_idx)
            if not mask.any():
                continue
            n_k = int(mask.sum())
            pts = np.column_stack([
                np.full(n_k, t_sec, dtype=np.float64),
                lats[mask], lons[mask],
            ])
            interp = self.interps[float(key)]
            u_arr[mask] = interp.u(pts)
            v_arr[mask] = interp.v(pts)
        return u_arr, v_arr

    def _nearest(self, depth_m: float) -> DepthInterp:
        keys = list(self.interps.keys())
        k = min(keys, key=lambda d: abs(d - depth_m))
        return self.interps[k]


@dataclass(frozen=True)
class TracerInterp:
    """One target depth's (T, S) interpolators + the actual grid level snapped to."""
    temp_c: RegularGridInterpolator
    sal_psu: RegularGridInterpolator
    actual_depth_m: float


@dataclass(frozen=True)
class TracerField:
    """A per-depth (temperature, salinity) interpolator bundle — the
    tracer-space sibling of `TruthField`. Populated from a dataset
    carrying `temp_c` and `sal_psu` on the same (time, depth, gridY,
    gridX) structure as the velocity fields (SalishSeaCast's
    `ubcSSg3DPhysicsFields1hV21-11`).
    """
    interps: dict[float, TracerInterp]
    times_sec: np.ndarray
    t0: np.datetime64
    lat_axis: np.ndarray
    lon_axis: np.ndarray

    @property
    def depths_m(self) -> list[float]:
        return sorted(self.interps.keys())

    def sample(self, lat: float, lon: float, depth_m: float, t_sec: float
                ) -> tuple[float, float]:
        """Sample (T, S) at the nearest available depth. Out-of-bounds → (nan, nan)."""
        interp = self._nearest(depth_m)
        t = float(interp.temp_c((t_sec, lat, lon)))
        s = float(interp.sal_psu((t_sec, lat, lon)))
        return t, s

    def sample_batched(self, lats: np.ndarray, lons: np.ndarray,
                         depths: np.ndarray, t_sec: float
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized (T, S) sample at N points each with its own depth.

        Mirrors `TruthField.sample_batched`: snaps each point to its
        nearest available depth slab and issues one RGI call per slab.
        Returns (T_arr, S_arr); out-of-bounds points are NaN.
        """
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        depths = np.asarray(depths, dtype=np.float64)
        N = lats.size
        T_arr = np.full(N, np.nan)
        S_arr = np.full(N, np.nan)
        depth_keys = np.array(sorted(self.interps.keys()), dtype=np.float64)
        slab_idx = np.argmin(np.abs(depth_keys[None, :] - depths[:, None]),
                              axis=1)
        for k_idx, key in enumerate(depth_keys):
            mask = (slab_idx == k_idx)
            if not mask.any():
                continue
            n_k = int(mask.sum())
            pts = np.column_stack([
                np.full(n_k, t_sec, dtype=np.float64),
                lats[mask], lons[mask],
            ])
            interp = self.interps[float(key)]
            T_arr[mask] = interp.temp_c(pts)
            S_arr[mask] = interp.sal_psu(pts)
        return T_arr, S_arr

    def sample_salinity_gradient_ms(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
        *, dlat_m: float = 500.0, dlon_m: float = 500.0,
    ) -> tuple[float, float]:
        """Finite-difference ∂S/∂x (east, g·kg⁻¹ / m) and ∂S/∂y (north,
        g·kg⁻¹ / m) at (lat, lon, depth, t) by central difference at
        `dlat_m` / `dlon_m` separation. Used by the v3 bias learner to
        convert a salinity residual into a plume-offset observation
        with the right Jacobian — see `docs/reference/ctd_sensor_model.md`
        §3 and the Phase 2.1 plan §"Bias-structure inference roadmap"."""
        interp = self._nearest(depth_m)
        cos_lat = float(np.cos(np.deg2rad(lat)))
        # ∂S/∂y (north, positive lat).
        dlat = dlat_m / EARTH_R_M
        s_n = float(interp.sal_psu((t_sec, lat + dlat, lon)))
        s_s = float(interp.sal_psu((t_sec, lat - dlat, lon)))
        # ∂S/∂x (east, positive lon).
        dlon = dlon_m / (EARTH_R_M * cos_lat)
        s_e = float(interp.sal_psu((t_sec, lat, lon + dlon)))
        s_w = float(interp.sal_psu((t_sec, lat, lon - dlon)))
        dSdx = (s_e - s_w) / (2 * dlon_m)
        dSdy = (s_n - s_s) / (2 * dlat_m)
        return dSdx, dSdy

    def _nearest(self, depth_m: float) -> TracerInterp:
        keys = list(self.interps.keys())
        k = min(keys, key=lambda d: abs(d - depth_m))
        return self.interps[k]


def build_truth_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
) -> TruthField:
    """Construct a TruthField from a cached SalishSeaCast bbox dataset.

    ds: xarray with dims (time, depth, gridY, gridX) and vars u_ms, v_ms.
    bbox_lats_grid, bbox_lons_grid: 2D (gridY, gridX) arrays of geographic
      coords for this bbox (from `bbox_latlon_arrays`).
    target_depths_m: list of target depths; each is snapped to the nearest
      SalishSeaCast level.

    Returns a TruthField with per-depth (u, v) RegularGridInterpolator
    over (times_sec, lat_axis, lon_axis). fill_value=nan on out-of-bounds.
    """
    depth_values = ds["depth"].values
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

    interps: dict[float, DepthInterp] = {}
    for target in target_depths_m:
        k = int(np.argmin(np.abs(depth_values - target)))
        actual = float(depth_values[k])
        u_cube = ds["u_ms"].isel(depth=k).values
        v_cube = ds["v_ms"].isel(depth=k).values
        if flip_lat:
            u_cube = u_cube[:, ::-1, :]
            v_cube = v_cube[:, ::-1, :]
        if flip_lon:
            u_cube = u_cube[:, :, ::-1]
            v_cube = v_cube[:, :, ::-1]
        u_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), u_cube,
            bounds_error=False, fill_value=np.nan,
        )
        v_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), v_cube,
            bounds_error=False, fill_value=np.nan,
        )
        interps[target] = DepthInterp(u=u_i, v=v_i, actual_depth_m=actual)

    return TruthField(
        interps=interps,
        times_sec=times_sec,
        t0=t0,
        lat_axis=lat_axis,
        lon_axis=lon_axis,
    )


def build_tracer_field(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
) -> TracerField:
    """Construct a `TracerField` from a SalishSeaCast bbox dataset
    carrying `sal_psu` and `temp_c`. Mirrors `build_truth_field`'s
    flip + snap-to-grid logic exactly so the same (lat, lon, t)
    coordinates index into truth and tracer consistently.
    """
    if "sal_psu" not in ds or "temp_c" not in ds:
        raise ValueError(
            "build_tracer_field requires `sal_psu` and `temp_c` in the "
            "input dataset; fetch with "
            "`fetch_bbox_months(..., include_tracers=True)`."
        )
    depth_values = ds["depth"].values
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

    interps: dict[float, TracerInterp] = {}
    for target in target_depths_m:
        k = int(np.argmin(np.abs(depth_values - target)))
        actual = float(depth_values[k])
        t_cube = ds["temp_c"].isel(depth=k).values
        s_cube = ds["sal_psu"].isel(depth=k).values
        if flip_lat:
            t_cube = t_cube[:, ::-1, :]
            s_cube = s_cube[:, ::-1, :]
        if flip_lon:
            t_cube = t_cube[:, :, ::-1]
            s_cube = s_cube[:, :, ::-1]
        t_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), t_cube,
            bounds_error=False, fill_value=np.nan,
        )
        s_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), s_cube,
            bounds_error=False, fill_value=np.nan,
        )
        interps[target] = TracerInterp(temp_c=t_i, sal_psu=s_i,
                                         actual_depth_m=actual)

    return TracerField(
        interps=interps,
        times_sec=times_sec,
        t0=t0,
        lat_axis=lat_axis,
        lon_axis=lon_axis,
    )


# ---------------------------------------------------------------------------
# Small geodesy helpers (shared)
# ---------------------------------------------------------------------------

def lat_lon_step_from_velocity(
    u_ms: float, v_ms: float, lat_deg: float, dt_sec: float,
) -> tuple[float, float]:
    """Convert (u, v) m/s over dt into (Δlat_deg, Δlon_deg). Small-angle."""
    dlat = (v_ms * dt_sec) / EARTH_R_M
    dlon = (u_ms * dt_sec) / (EARTH_R_M * np.cos(np.deg2rad(lat_deg)))
    return dlat, dlon


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance (meters). Small-scale; good enough for < 100 km."""
    cos_lat = np.cos(np.deg2rad(0.5 * (lat1 + lat2)))
    dlat_m = (lat1 - lat2) * EARTH_R_M
    dlon_m = (lon1 - lon2) * EARTH_R_M * cos_lat
    return float(np.sqrt(dlat_m**2 + dlon_m**2))


# ---------------------------------------------------------------------------
# RK4 Lagrangian advection (shared)
# ---------------------------------------------------------------------------

VelocityAt = Callable[[float, float, float], tuple[float, float]]
# (t_sec, lat, lon) -> (u_ms, v_ms)


def rk4_advect_step(
    lat: float, lon: float, t_sec: float, dt_sec: float,
    velocity_at: VelocityAt,
) -> tuple[float, float] | None:
    """One RK4 step. Returns (new_lat, new_lon), or None if any stage NaNs out."""
    u1, v1 = velocity_at(t_sec, lat, lon)
    if not (np.isfinite(u1) and np.isfinite(v1)):
        return None
    dlat1, dlon1 = lat_lon_step_from_velocity(u1, v1, lat, dt_sec / 2)

    u2, v2 = velocity_at(t_sec + dt_sec / 2, lat + dlat1, lon + dlon1)
    if not (np.isfinite(u2) and np.isfinite(v2)):
        return None
    dlat2, dlon2 = lat_lon_step_from_velocity(u2, v2, lat, dt_sec / 2)

    u3, v3 = velocity_at(t_sec + dt_sec / 2, lat + dlat2, lon + dlon2)
    if not (np.isfinite(u3) and np.isfinite(v3)):
        return None
    dlat3, dlon3 = lat_lon_step_from_velocity(u3, v3, lat, dt_sec)

    u4, v4 = velocity_at(t_sec + dt_sec, lat + dlat3, lon + dlon3)
    if not (np.isfinite(u4) and np.isfinite(v4)):
        return None

    u_avg = (u1 + 2 * u2 + 2 * u3 + u4) / 6
    v_avg = (v1 + 2 * v2 + 2 * v3 + v4) / 6
    dlat, dlon = lat_lon_step_from_velocity(u_avg, v_avg, lat, dt_sec)
    return lat + dlat, lon + dlon
