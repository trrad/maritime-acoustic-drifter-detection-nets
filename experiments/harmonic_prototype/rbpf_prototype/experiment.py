"""Experiment runner. One-station driver that wires dynamics, prior,
RBPF, sensors, surfacing policy, controller, and (optionally) the v2
reduced-rank bias-field learner. Returns trajectory + metrics.

Designed to be called from a sweep driver at the top level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

import numpy as np  # type: ignore[import-not-found]

from ballast_controller import (  # type: ignore[import-not-found]
    MPCStationKeeper,
    TrajectoryStationKeeper,
)
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from process_noise import ProcessNoiseConfig  # type: ignore[import-not-found]
from truth_field import EARTH_R_M, distance_m  # type: ignore[import-not-found]

from .bias_field import BiasFieldState, GridBiasBasis
from .rbpf import PositionRBPF
from .sensors import CTDSensor, LoRaRangeSensor, RelativeFlowSensor
from .surfacing import SurfacingPolicy


def _compute_sigma_obs_per_particle(
    cfg: "BiasConfig",
    dwell: np.ndarray,                    # (N, D, Y, X)
    depth_centers_m: np.ndarray,          # (D,)
    leg_duration_sec: float,
    sigma_x_start_m: float,
    sigma_lora_end_m_override: float | None = None,
) -> np.ndarray:
    """Per-particle σ_obs at leg-end (Dee 2005 §3 decomposition).

    Decomposition principle: σ_obs is the noise from layered-noise
    components the **bias state cannot structurally represent** plus
    instrument noise. Components that ARE in the state's representational
    capacity (matched spatial scale to Matérn-grid l_corr=5km on 16km
    patch) belong in the bias state's prior σ, NOT in σ_obs — putting
    them in both double-counts and suppresses Kalman gain.

    State-representable (in PRIOR, not here):
      - coh   (5 km, basin) — l_corr matches Matérn
      - plume (2 km, depth-trapped) — marginally; included
      - submeso (5 km, surface-decay) — l_corr matches Matérn

    NOT state-representable (here in σ_obs):
      - inertial (20 km, basin-scale rotating) — exceeds 16 km patch
      - white (sub-cell scale)
    """
    # Per-particle time at each depth slab.
    time_at_d = dwell.sum(axis=(2, 3))                  # (N, D)
    total_t = np.maximum(time_at_d.sum(axis=1), 1.0)    # (N,)
    w_d = time_at_d / total_t[:, None]                   # (N, D)
    # Surface-trap squared-attenuation factor, dwell-weighted.
    L_z = cfg.L_z_surf_m
    atten_sq = np.exp(-2.0 * np.asarray(depth_centers_m) / max(L_z, 1e-6))
    surf_atten_sq = (w_d * atten_sq[None, :]).sum(axis=1)   # (N,)
    T = leg_duration_sec

    def ou_int_var(sigma_sq, tau_sec):
        return sigma_sq * 2.0 * tau_sec * (
            T - tau_sec * (1.0 - np.exp(-T / max(tau_sec, 1e-6)))
        )
    # White (depth-coherent, sub-cell scale; not state-representable).
    var_white = ou_int_var(
        cfg.sigma_white_ms ** 2, cfg.tau_white_h * 3600.0
    )
    # Inertial (basin-scale rotating; 20 km l_corr exceeds 16 km patch
    # → not state-representable). Rotating at f, coherent fraction
    # sinc(πT/T_f); surface-trapped same L_z as submeso.
    f_period = cfg.f_inertial_period_h * 3600.0
    sinc_factor = float(np.sinc(T / max(f_period, 1e-6)))
    var_inertial = surf_atten_sq * (
        cfg.sigma_inertial_ms ** 2 * (T * sinc_factor) ** 2
    )
    # NOTE: submeso is INTENTIONALLY OMITTED here — its 5 km l_corr
    # matches the Matérn-grid's representation, so it belongs in the
    # bias state's prior σ. Including it here was double-counting that
    # suppressed Kalman gain (cov shrunk only ~10% over a mission).
    # LoRa fix + per-particle x_start. The override path lets the caller
    # pass the per-fix HDOP-derived σ_at_fix from `trilaterate_lora`
    # rather than `cfg.sigma_lora_end_m`, so the observation budget
    # reflects actual anchor geometry instead of a flat assumption.
    sigma_lora_end = (
        sigma_lora_end_m_override if sigma_lora_end_m_override is not None
        else cfg.sigma_lora_end_m
    )
    var_other = sigma_lora_end ** 2 + sigma_x_start_m ** 2
    sigma_obs = np.sqrt(var_white + var_inertial + var_other)
    floor = max(cfg.sigma_obs_floor_m, 0.0)
    return np.maximum(sigma_obs, floor)


def trilaterate_lora(
    anchors: list[tuple[float, float]],
    ranges: list[float],
    ref_lat: float, ref_lon: float,
    sigma_per_anchor_m: float = 0.0,
) -> tuple[float, float, float]:
    """Multilateration of N≥3 noisy range measurements to anchors at
    known (lat, lon). Solves |p_i − x|² = r_i² linearised in local
    east/north metres relative to `(ref_lat, ref_lon)`, then converts
    back to (lat, lon). 2-D only; assumes the node is near the surface
    so depth contributes negligibly to range.

    Out-of-range observations should be NaN-coded by the caller (e.g.,
    `LoRaRangeSensor.sample` does this for anchors beyond `max_range_m`);
    this function filters NaN and requires ≥3 finite ranges. Returns
    `(NaN, NaN, NaN)` when fewer than 3 valid observations are present
    (= "no fix this tick"), so the caller can skip the PF reweight /
    bias-Kalman update path naturally and let σ_pos grow until the
    next tick with sufficient anchor coverage.

    `sigma_per_anchor_m`: per-range observation σ. When > 0, the
    returned `sigma_at_fix_m` = sigma_per_anchor_m × HDOP, where HDOP
    is the per-axis Geometric Dilution of Precision derived from the
    actual anchor geometry at the fix:

        H[i, :] = (x̂ − p_i) / ‖x̂ − p_i‖           (unit vectors)
        cov_pos = σ_per_anchor² · (Hᵀ H)⁻¹         (m²)
        sigma_at_fix = √(½ (cov_pos[0,0] + cov_pos[1,1]))   (m)

    Without this, a flat hard-coded σ_at_fix would mis-budget the
    bias-Kalman observation noise when anchor geometry yields HDOP ≫ 1
    at edge-of-bbox drifters.

    `sigma_per_anchor_m=0` returns `sigma_at_fix=0.0` (legacy
    callers that only need the position fix). New callers should
    always pass the sensor's `sigma_m`.

    Despite the historical name, works with any N≥3 anchors via LSQ
    multilateration; the `trilaterate_*` name is preserved for caller
    compatibility with the 3-anchor magic-buoy era.

    Matches the REMUS / HUGIN / Argo practice of using the direct
    position fix at surface rather than feeding ranges through the PF
    reweight — see Paull et al. 2014 J. Oceanic Eng. for the rationale.
    """
    arr_anchors = np.asarray(anchors, dtype=float)
    arr_ranges = np.asarray(ranges, dtype=float)
    valid = np.isfinite(arr_ranges)
    n_valid = int(valid.sum())
    if n_valid < 3:
        return float("nan"), float("nan"), float("nan")
    anchors_v = arr_anchors[valid]
    ranges_v = arr_ranges[valid]

    cos_lat = float(np.cos(np.deg2rad(ref_lat)))
    # Local ENU projection: x = east, y = north.
    p = np.column_stack([
        (anchors_v[:, 1] - ref_lon) * EARTH_R_M * cos_lat,
        (anchors_v[:, 0] - ref_lat) * EARTH_R_M,
    ])  # (N_valid, 2)
    r = ranges_v
    p0 = p[0]
    r0 = r[0]
    # 2·(p_i − p_0)ᵀ x = r_0² − r_i² + |p_i|² − |p_0|²
    A = 2.0 * (p[1:] - p0)
    b = (r0 ** 2 - r[1:] ** 2) + np.sum(p[1:] ** 2, axis=1) - np.sum(p0 ** 2)
    x_enu, *_ = np.linalg.lstsq(A, b, rcond=None)
    east_m, north_m = float(x_enu[0]), float(x_enu[1])
    out_lat = ref_lat + north_m / EARTH_R_M
    out_lon = ref_lon + east_m / (EARTH_R_M * cos_lat)

    if sigma_per_anchor_m > 0.0:
        # Geometry matrix at the LSQ position estimate (linearisation
        # of the range observation w.r.t. drifter position).
        x_pos = np.array([east_m, north_m])
        diff = x_pos - p                               # (N_valid, 2)
        dist = np.linalg.norm(diff, axis=1)            # (N_valid,)
        H = diff / np.maximum(dist, 1e-3)[:, None]     # (N_valid, 2)
        try:
            cov_pos = np.linalg.inv(H.T @ H) * (sigma_per_anchor_m ** 2)
            sigma_at_fix_m = float(np.sqrt(
                0.5 * (cov_pos[0, 0] + cov_pos[1, 1])
            ))
        except np.linalg.LinAlgError:
            # Degenerate geometry (collinear anchors, etc.) — return
            # NaN σ so the caller can decide; a degenerate-geometry
            # fix is still a position estimate but its σ is
            # mathematically unbounded.
            sigma_at_fix_m = float("nan")
    else:
        sigma_at_fix_m = 0.0

    return out_lat, out_lon, sigma_at_fix_m


class LiveBiasKnowledge:
    """`KnowledgeSource`-compatible wrapper that returns
    `nemo_prior(lat, lon, depth, t) + ensemble_mean_bias(cell)` —
    GATED on posterior variance.

    The posterior-variance gate falls back to the clean prior when this
    drifter has not adequately observed the queried cell (per-mission
    inference is local; cells outside the dwelled region keep prior-mean
    ≈ 0 with full prior variance, and using that as if it were a
    posterior misleads the controller). Threshold defaults to half the
    initial prior variance — i.e. the bias estimate must have at least
    halved its uncertainty before being trusted.
    """

    def __init__(self, nemo_prior: object, pf: "PositionRBPF",
                  bias: "BiasFieldState", basis: "GridBiasBasis",
                  posterior_var_gate_ratio: float = 0.5) -> None:
        self.nemo_prior = nemo_prior
        self.pf = pf
        self.bias = bias
        self.basis = basis
        # Variance threshold (m/s)² below which the bias is "trusted".
        # cov_prior[0,0] is the diagonal entry of the Matérn prior at
        # any cell — same for all cells given isotropic kernel.
        prior_var = float(self.bias.cov_prior[0, 0])
        self.posterior_var_gate = posterior_var_gate_ratio * prior_var
        # Per-cell stats cache. Built by `precompute_for_decision()` (called
        # by MPCStationKeeper at the top of each planning call); read by
        # `get_current_at_batched` instead of re-computing per-query.
        # `None` = not built / stale; refresh by calling precompute again.
        self._cache_ens_u: np.ndarray | None = None
        self._cache_ens_v: np.ndarray | None = None
        self._cache_total_var_u: np.ndarray | None = None
        self._cache_total_var_v: np.ndarray | None = None
        # Posterior draws cache (Step 2 posterior-CVaR). Each entry has
        # shape (N_draws, D, Y, X). Built by `precompute_posterior_draws()`
        # before MPC scoring under `mpc_scoring="posterior_cvar"`.
        self._cache_draws_u: np.ndarray | None = None
        self._cache_draws_v: np.ndarray | None = None

    def precompute_for_decision(self) -> None:
        """Pre-compute per-cell ensemble-mean and total-variance arrays
        for the current bias state. Amortises the per-particle reductions
        across all queries within one MPC planning decision.

        Total variance per cell = between-particle disagreement on the
        ensemble mean + within-particle posterior variance (law of total
        variance). Same semantic as the inline computation in
        `get_current_at_batched`.

        Cache lifetime: one MPC decision. Caller (the keeper) is
        responsible for re-invoking this between decisions; bias-state
        mutations (Kalman update, OU evolve, gather, lookup_and_accumulate)
        between decisions silently invalidate the cache, but they don't
        happen mid-`choose_depth` so we don't track that.
        """
        w = self.pf.weights
        # Ensemble mean per cell: (D, Y, X)
        ens_u = np.einsum('ndyx,n->dyx', self.bias.mean_u, w)
        ens_v = np.einsum('ndyx,n->dyx', self.bias.mean_v, w)
        # Between-particle variance per cell: (D, Y, X)
        diff_u = self.bias.mean_u - ens_u[None, :, :, :]
        diff_v = self.bias.mean_v - ens_v[None, :, :, :]
        between_u = np.einsum('ndyx,n->dyx', diff_u ** 2, w)
        between_v = np.einsum('ndyx,n->dyx', diff_v ** 2, w)
        # Within-particle posterior variance: (D, Y, X). Diagonal of the
        # dense per-depth covariance, weighted by particle weights.
        cov_u_diag = np.einsum('ndii->ndi', self.bias.cov_u)  # (N, D, Y·X)
        cov_v_diag = np.einsum('ndii->ndi', self.bias.cov_v)
        n_y = self.basis.n_cells
        within_u = np.einsum('ndi,n->di', cov_u_diag, w).reshape(
            -1, n_y, n_y)
        within_v = np.einsum('ndi,n->di', cov_v_diag, w).reshape(
            -1, n_y, n_y)
        self._cache_ens_u = ens_u
        self._cache_ens_v = ens_v
        self._cache_total_var_u = between_u + within_u
        self._cache_total_var_v = between_v + within_v

    def precompute_posterior_draws(
        self, n_draws: int, rng: np.random.Generator,
    ) -> None:
        """Pre-sample N draws of the per-cell bias field for posterior-CVaR
        scoring. Each draw represents a plausible bias-field realisation
        weighted by PF particle weights.

        Per-particle Cholesky of the dense per-depth covariance:
            L[n, d] = chol(cov[n, d, :, :])
        Sample per particle, per draw:
            sample[k, n, d, :] = mean[n, d, :] + L[n, d] @ z[k, n, d, :]
        Aggregate via PF weights:
            draw[k, d, :] = Σ_n w[n] · sample[k, n, d, :]

        Result: `self._cache_draws_u/v` shape `(n_draws, D, Y, X)`.
        Cost: dominated by the per-particle Cholesky (O(N · D · Y·X³)) —
        for N=500, D=5, Y·X=64 this is ~6.5M ops, ~50 ms. Sampling is
        ~25M ops per draw, well under the per-substep RGI cost.

        Cache lifetime: one MPC planning decision. Same lifetime as
        `precompute_for_decision`; both are invalidated by intervening
        bias-state mutations between calls.
        """
        if n_draws <= 0:
            self._cache_draws_u = None
            self._cache_draws_v = None
            return
        N = self.pf.n
        D = self.bias.n_depths
        n_y = self.basis.n_cells
        n_flat = n_y * n_y
        w = self.pf.weights
        # Per-particle Cholesky factors. cov shape (N, D, n_flat, n_flat).
        # Numerical safety: jitter the diagonal slightly when needed (a
        # particle that converged tightly to one cell can produce a
        # degenerate cov for the un-observed cells; the jitter keeps
        # cholesky stable without altering the leading principal block.
        jitter = 1e-12
        cov_u = self.bias.cov_u + jitter * np.eye(n_flat)[None, None, :, :]
        cov_v = self.bias.cov_v + jitter * np.eye(n_flat)[None, None, :, :]
        L_u = np.linalg.cholesky(cov_u)   # (N, D, n_flat, n_flat)
        L_v = np.linalg.cholesky(cov_v)
        # Per-particle mean reshaped flat (N, D, n_flat).
        mean_u_flat = self.bias.mean_u.reshape(N, D, n_flat)
        mean_v_flat = self.bias.mean_v.reshape(N, D, n_flat)
        # Sample N_draws fresh standard normal draws per (particle, depth):
        # z shape (n_draws, N, D, n_flat). Per-(k, n, d) the sample is
        # mean[n, d] + L[n, d] @ z[k, n, d].
        z_u = rng.standard_normal(size=(n_draws, N, D, n_flat))
        z_v = rng.standard_normal(size=(n_draws, N, D, n_flat))
        # Per-particle samples: einsum over the trailing matrix axis.
        # 'kndi,knij->kndj' isn't right shape — use 'ndij,kndj->kndi'.
        # L @ z^T: per-particle, per-draw, the L matrix multiplied by z.
        per_part_u = mean_u_flat[None, :, :, :] + np.einsum(
            'ndij,kndj->kndi', L_u, z_u,
        )
        per_part_v = mean_v_flat[None, :, :, :] + np.einsum(
            'ndij,kndj->kndi', L_v, z_v,
        )
        # Weighted aggregate over particles → per-draw bias field.
        draws_u = np.einsum('kndi,n->kdi', per_part_u, w)
        draws_v = np.einsum('kndi,n->kdi', per_part_v, w)
        # Reshape (n_draws, D, n_flat) → (n_draws, D, Y, X).
        self._cache_draws_u = draws_u.reshape(n_draws, D, n_y, n_y)
        self._cache_draws_v = draws_v.reshape(n_draws, D, n_y, n_y)

    def get_current_at_batched_draw(
        self, lats: np.ndarray, lons: np.ndarray,
        depths: np.ndarray, t_sec: float, draw_idx: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized N-point query of `prior + draw_idx-th bias sample`.

        Mirrors `get_current_at_batched` but uses the pre-computed
        posterior-draw bias field at draw `draw_idx` instead of the
        ensemble-mean. Falls back to clean prior when the cell is
        outside the basis patch (no bias correction outside) — note
        we do NOT apply the posterior-variance gate here, since the
        whole point of CVaR scoring is to reason about tail outcomes
        including high-variance cells.

        Requires `precompute_posterior_draws` to have been called.
        """
        if self._cache_draws_u is None or self._cache_draws_v is None:
            raise RuntimeError(
                "get_current_at_batched_draw requires "
                "precompute_posterior_draws() first"
            )
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        depths = np.asarray(depths, dtype=float)
        if hasattr(self.nemo_prior, "sample_batched"):
            u, v = self.nemo_prior.sample_batched(lats, lons, depths, t_sec)  # type: ignore[attr-defined]
        else:
            N = lats.size
            u = np.empty(N)
            v = np.empty(N)
            for k in range(N):
                u[k], v[k] = self.nemo_prior.get_current_at(  # type: ignore[attr-defined]
                    float(lats[k]), float(lons[k]),
                    float(depths[k]), t_sec,
                )
        u = np.asarray(u, dtype=float)
        v = np.asarray(v, dtype=float)
        finite = np.isfinite(u) & np.isfinite(v)
        depth_centers = np.asarray(self.basis.depth_centers_m)
        di_per_pt = np.argmin(np.abs(depth_centers[None, :] - depths[:, None]),
                               axis=1)
        for slab_idx in range(depth_centers.size):
            mask = (di_per_pt == slab_idx) & finite
            if not mask.any():
                continue
            di = slab_idx
            _, yi_arr, xi_arr, inside = self.basis.indices(
                lats[mask], lons[mask], float(depth_centers[slab_idx]),
            )
            if not inside.any():
                continue
            mask_idx = np.flatnonzero(mask)
            sub_idx = mask_idx[inside]
            yi = yi_arr[inside].astype(int)
            xi = xi_arr[inside].astype(int)
            u[sub_idx] = u[sub_idx] + self._cache_draws_u[draw_idx, di, yi, xi]
            v[sub_idx] = v[sub_idx] + self._cache_draws_v[draw_idx, di, yi, xi]
        u = np.where(finite, u, np.nan)
        v = np.where(finite, v, np.nan)
        return u, v

    def get_current_at(self, lat: float, lon: float,
                        depth_m: float, t_sec: float,
                        ) -> tuple[float, float]:
        u, v = self.nemo_prior.get_current_at(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(u) and np.isfinite(v)):
            return float("nan"), float("nan")
        lat_arr = np.asarray([lat], dtype=float)
        lon_arr = np.asarray([lon], dtype=float)
        d, y, x, inside = self.basis.indices(lat_arr, lon_arr, depth_m)
        if not bool(inside[0]):
            return u, v
        di, yi, xi = int(d[0]), int(y[0]), int(x[0])
        # Posterior-variance gate: trust the ensemble-mean bias only when
        # the TOTAL variance of the ensemble estimate at this cell is
        # below threshold. Total variance = between-particle disagreement
        # + within-particle posterior (law of total variance). The earlier
        # gate used only within-particle variance, which silently approves
        # cells where particles agree internally but disagree across the
        # ensemble — exactly the regime where the reported ensemble mean
        # is the noisy average of confident contradicting values.
        flat_i = yi * self.basis.n_cells + xi
        w = self.pf.weights
        ens_u = float(np.sum(self.bias.mean_u[:, di, yi, xi] * w))
        ens_v = float(np.sum(self.bias.mean_v[:, di, yi, xi] * w))
        between_u = float(np.sum(
            (self.bias.mean_u[:, di, yi, xi] - ens_u) ** 2 * w))
        between_v = float(np.sum(
            (self.bias.mean_v[:, di, yi, xi] - ens_v) ** 2 * w))
        within_u = float(np.sum(self.bias.cov_u[:, di, flat_i, flat_i] * w))
        within_v = float(np.sum(self.bias.cov_v[:, di, flat_i, flat_i] * w))
        total_var_u = between_u + within_u
        total_var_v = between_v + within_v
        if max(total_var_u, total_var_v) > self.posterior_var_gate:
            return u, v
        return u + ens_u, v + ens_v

    def get_current_at_batched(
        self, lats: np.ndarray, lons: np.ndarray,
        depths: np.ndarray, t_sec: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized N-point query of `prior + ensemble_mean_bias`,
        gated cell-by-cell on the TOTAL variance of the ensemble estimate
        (between-particle disagreement + within-particle posterior, per
        the law of total variance). Falls back to the prior at any cell
        whose total variance hasn't dropped below the gate threshold.

        Required by the MPC keeper's vectorized rollout. Same semantics
        as the scalar `get_current_at`.
        """
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        depths = np.asarray(depths, dtype=float)
        # Prior lookup, vectorized when the underlying prior supports it.
        if hasattr(self.nemo_prior, "sample_batched"):
            u, v = self.nemo_prior.sample_batched(lats, lons, depths, t_sec)  # type: ignore[attr-defined]
        else:
            N = lats.size
            u = np.empty(N)
            v = np.empty(N)
            for k in range(N):
                u[k], v[k] = self.nemo_prior.get_current_at(  # type: ignore[attr-defined]
                    float(lats[k]), float(lons[k]),
                    float(depths[k]), t_sec,
                )
        u = np.asarray(u, dtype=float)
        v = np.asarray(v, dtype=float)
        finite = np.isfinite(u) & np.isfinite(v)
        # Bias addition is point-wise; depth indexing per point. The
        # basis supports vector lats/lons but a scalar depth — call it
        # per unique depth slab to amortise the per-call overhead.
        depth_centers = np.asarray(self.basis.depth_centers_m)
        di_per_pt = np.argmin(np.abs(depth_centers[None, :] - depths[:, None]),
                               axis=1)
        n_cells_side = self.basis.n_cells
        w = self.pf.weights
        for slab_idx in range(depth_centers.size):
            mask = (di_per_pt == slab_idx) & finite
            if not mask.any():
                continue
            di = slab_idx
            _, yi_arr, xi_arr, inside = self.basis.indices(
                lats[mask], lons[mask], float(depth_centers[slab_idx]),
            )
            if not inside.any():
                continue
            mask_idx = np.flatnonzero(mask)
            sub_idx = mask_idx[inside]
            yi = yi_arr[inside].astype(int)
            xi = xi_arr[inside].astype(int)
            # Read pre-computed per-cell stats if the cache is populated
            # (typical MPC path); fall back to per-query reductions when
            # the cache is None (scalar / one-shot callers).
            if self._cache_ens_u is not None:
                ens_u_all = self._cache_ens_u[di, yi, xi]
                ens_v_all = self._cache_ens_v[di, yi, xi]                  # type: ignore[index]
                total_var_u = self._cache_total_var_u[di, yi, xi]          # type: ignore[index]
                total_var_v = self._cache_total_var_v[di, yi, xi]          # type: ignore[index]
            else:
                flat_i = yi * n_cells_side + xi
                mu_per_part_u = self.bias.mean_u[:, di, yi, xi]
                mu_per_part_v = self.bias.mean_v[:, di, yi, xi]
                ens_u_all = (mu_per_part_u * w[:, None]).sum(axis=0)
                ens_v_all = (mu_per_part_v * w[:, None]).sum(axis=0)
                between_u = ((mu_per_part_u - ens_u_all[None, :]) ** 2
                             * w[:, None]).sum(axis=0)
                between_v = ((mu_per_part_v - ens_v_all[None, :]) ** 2
                             * w[:, None]).sum(axis=0)
                within_u = (self.bias.cov_u[:, di, flat_i, flat_i]
                            * w[:, None]).sum(axis=0)
                within_v = (self.bias.cov_v[:, di, flat_i, flat_i]
                            * w[:, None]).sum(axis=0)
                total_var_u = between_u + within_u
                total_var_v = between_v + within_v
            cell_trusted = (np.maximum(total_var_u, total_var_v)
                            <= self.posterior_var_gate)
            trust_idx = sub_idx[cell_trusted]
            if trust_idx.size == 0:
                continue
            u[trust_idx] = u[trust_idx] + ens_u_all[cell_trusted]
            v[trust_idx] = v[trust_idx] + ens_v_all[cell_trusted]
        # NaN propagation: where prior was non-finite, leave NaN so the
        # MPC's alive-mask catches it (matches the scalar return
        # semantics of the per-point method).
        u = np.where(finite, u, np.nan)
        v = np.where(finite, v, np.nan)
        return u, v


@dataclass
class StationConfig:
    lat: float
    lon: float
    envelope_m: float = 3000.0
    available_depths_m: list[float] = field(
        default_factory=lambda: [0.5, 5.0, 10.0, 20.0, 50.0])


@dataclass
class SimConfig:
    run_hours: int = 72
    dt_sec: float = 600.0                # 10-min tick
    control_cadence_sec: float = 1800.0  # 30-min depth decisions
    lookahead_sec: float = 1800.0
    w_z_max_ms: float = 0.1
    initial_depth_m: float = 10.0
    surface_dwell_h: float = 0.5         # dwell at 0.5m per surface event
    lora_cadence_sec: float = 60.0       # how often ranges fire while at surface
    # Controller selection. MPC is the default — strictly better than
    # greedy under perfect info per the site-authority sweep (closes
    # ~42% of greedy → physics-floor gap at h=24). The trajectory keeper
    # remains as a fallback for harnesses that explicitly want greedy.
    controller: Literal["trajectory", "mpc"] = "mpc"
    mpc_horizon_n: int = 12              # 6 h plan = M2 half-cycle
    mpc_beam_width: int = 200            # site-authority sweep showed
                                          # b=200 closes ~all of brute h=6
    # Process-noise model. `ou_integrated` (default) uses the per-component
    # OU model in `process_noise.py`, mirroring `LayeredNoiseField` so the
    # PF predict's per-tick velocity variance matches truth in expectation.
    # `iid_legacy` uses the legacy `PFConfig.process_noise_ms`-driven
    # i.i.d. perturbation — kept for ablation against the OU model.
    process_noise_model: Literal["ou_integrated", "iid_legacy"] = "ou_integrated"
    # MPC objective: score_per_tick = α · d² + β · σ_pos² + λ · CVaR(d²)
    # + γ · CVaR(σ_pos²). Default β=λ=γ=0 reduces to mean-d² (slight
    # change from current mean-distance, harmless and defensible — see
    # plan §"σ_pos rollout in MPC").
    mpc_objective_alpha: float = 1.0
    mpc_objective_beta: float = 0.0
    mpc_objective_lambda: float = 1.0   # CVaR(d²) weight (used only under posterior_cvar)
    mpc_objective_gamma: float = 0.0    # CVaR(σ_pos²) weight (off by default)
    # Posterior-aware CVaR scoring. Requires bias_cfg to be set;
    # raises in run_one_station otherwise. Default is `posterior_cvar`
    # because that's the most-advanced controller and the intended
    # production setting for any arm with a bias posterior. Configs
    # without a bias state (e.g., the no_learn baseline) must opt in
    # to `ensemble_mean` explicitly — fail-loud per integrity charter.
    mpc_scoring: Literal["ensemble_mean", "posterior_cvar"] = "posterior_cvar"
    mpc_n_posterior_draws: int = 5
    mpc_cvar_alpha: float = 0.10


@dataclass
class SensorConfig:
    lora: LoRaRangeSensor
    flow: Optional[RelativeFlowSensor] = None  # None = flow sensor disabled
    ctd: Optional[CTDSensor] = None            # None = CTD disabled


@dataclass
class PFConfig:
    n_particles: int = 500
    init_sigma_m: float = 20.0
    ess_resample_ratio: float = 0.5
    process_noise_ms: float = 0.0   # per-tick velocity perturbation stddev (m/s)
                                     # — only used when SimConfig.process_noise_model
                                     # == "iid_legacy". Ignored under "ou_integrated".
    # Per-component OU process-noise model (see `process_noise.py`). When
    # None and `SimConfig.process_noise_model == "ou_integrated"`,
    # `run_one_station` constructs the default ProcessNoiseConfig
    # (matching `build_layered_noise_field` defaults).
    process_noise_cfg: Optional[ProcessNoiseConfig] = None
    # Surface-entry validation-gated reinit. When the PF posterior mean
    # disagrees with the LoRa trilateration by more than
    # `reinit_threshold_m`, replace particles with N(tri_pos,
    # reinit_sigma_m²). Defeats tight-cluster degeneracy from
    # deterministic dead-reckoning.
    reinit_threshold_m: float = 300.0
    reinit_sigma_m: float = 50.0


@dataclass
class BiasConfig:
    """v1.1 dense-Matérn bias-field learner.

    `sigma_bias_init_ms` — prior 1-σ per cell. Default matches the
    learnable-component amplitude in the layered noise model
    (√(σ_coh² + σ_plume² + σ_submeso² + σ_inertial²) ≈ 7.8 cm/s at
    σ_fc = 8 cm/s).

    `l_corr_m` — Matérn spatial correlation length for the prior P_∞.
    Default 5 km matches the slow-component spatial scale (σ_s = 10
    cells × 500 m grid in the layered noise design). The dense prior
    spreads a leg's residual across spatially-correlated cells via the
    Kalman gain — this is what fixes the diagonal-prior overshoot
    flagged by the 2026-04-25 stats review.

    `matern_nu` — kernel smoothness. 0.5 = exponential (rough, OU-like);
    1.5 = smoother. Use 0.5 to match the underlying noise's roughness.

    `tau_ou_sec` — temporal correlation time for the bias state's OU
    evolution between observations. Default 36 h matches the slow
    component's τ in the layered noise design.

    Per-leg σ_obs decomposition per Dee 2005 §3:
        σ_obs² = σ_white_int² + σ_submeso_int²(z) + σ_inertial_int²(z)
                 + σ_lora_end² + σ_x_start²
    Surface-trapped components (submeso, inertial) attenuate as
    `exp(-z/L_z_surf)`; the per-leg σ_obs is computed at the dwell-
    weighted mean depth of each particle. Defaults below mirror the
    layered velocity-noise model's component values.
    """
    n_cells: int = 8
    cell_size_m: float = 2000.0
    sigma_bias_init_ms: float = 0.078
    l_corr_m: float = 5000.0
    matern_nu: float = 0.5
    tau_ou_sec: float = 36.0 * 3600.0
    posterior_var_gate_ratio: float = 0.5
    # Unlearnable noise components used to compute σ_obs per leg.
    sigma_white_ms: float = 0.015
    tau_white_h: float = 3.0
    sigma_submeso_ms: float = 0.05
    tau_submeso_h: float = 12.0
    sigma_inertial_ms: float = 0.04
    f_inertial_period_h: float = 16.5         # 49°N
    L_z_surf_m: float = 20.0                  # surface-trap e-fold
    sigma_lora_end_m: float = 20.0            # LoRa fix uncertainty
    # Conservative floor on σ_obs in metres — guards against tiny
    # observations when the dwell-weighted attenuation drives all the
    # surface-trapped components to ~0 (e.g., a leg spent entirely
    # at z=50 m). Sub-floor σ_obs would over-trust observations.
    sigma_obs_floor_m: float = 100.0
    # Scalar (T, S) bias offset state: prior 1-σ. Wide enough to cover
    # Soontiens 2017 SoG basin range with margin (T: 0.2-0.5 °C,
    # S: 0.3-0.7 g/kg). The PF + ensemble-mean estimate converges within
    # the first few CTD ticks once particles are concentrated.
    sigma_T_offset_init_c: float = 0.5
    sigma_S_offset_init_psu: float = 1.0
    # Spatial-fluctuation σ around the basin offset — what the bias
    # state DOESN'T model (plume + white at the typical operating depth).
    # Inflates the CTD likelihood σ to avoid the "clean prior == truth"
    # over-discrimination pathology. Depth-averaging here is a v1
    # simplification; depth-aware version is queued.
    sigma_T_fluct_c: float = 0.1
    sigma_S_fluct_psu: float = 0.15


@dataclass
class ExperimentResult:
    lats: np.ndarray
    lons: np.ndarray
    depths: np.ndarray
    dists_m: np.ndarray
    pf_mean_lats: np.ndarray
    pf_mean_lons: np.ndarray
    pf_err_m: np.ndarray
    pf_std_m: np.ndarray
    # Per-tick filtered position covariance (n_steps+1, 2, 2) in m².
    # Lat→y, lon→x (in local-ENU after the cos_lat conversion done by
    # `PositionRBPF.cov_m`). Consumed by the RTS smoother to produce the
    # retroactive σ_pos used for acoustic-event TDOA reconstruction.
    pf_cov_m: np.ndarray
    at_surface_mask: np.ndarray
    # True at ticks where a LoRa trilateration fix was applied. The RTS
    # smoother treats these as observation events; surrounding ticks
    # benefit from backward propagation of fix information.
    lora_fix_mask: np.ndarray
    surface_events: int
    lora_updates: int
    flow_updates: int
    bias_updates: int = 0
    bias_learned_fraction: float = 0.0
    bias_mean_learned_mag_ms: float = 0.0
    bias_max_learned_mag_ms: float = 0.0
    bias_mean_learned_var_ms2: float = 0.0
    ctd_updates: int = 0
    # Final ensemble-mean (T, S) bias offset estimate — convergence
    # check for the Step 2.2 tracer-bias state.
    bias_T_offset_final_c: float = 0.0
    bias_S_offset_final_psu: float = 0.0
    # Mean of the MPC's predicted σ_pos at the rollout horizon, averaged
    # across all decision calls. Used by the calibration diagnostic to
    # cross-validate against observed pf_err over the same window.
    # NaN when the controller doesn't predict σ_pos (TrajectoryStationKeeper).
    predicted_sigma_pos_horizon_mean: float = float("nan")

    def ctrl_mean_m(self) -> float:
        return float(np.nanmean(self.dists_m))

    def ctrl_max_m(self) -> float:
        return float(np.nanmax(self.dists_m))

    def envelope_frac(self, envelope_m: float) -> float:
        return float((self.dists_m <= envelope_m).mean())

    # --- pf_err / pf_std metrics (acoustic-event TDOA framing) ---
    # The drifter's job in the fleet is to be a TDOA sensor with tight
    # localization at event time — pf_err is the primary metric, with
    # pf_std as the self-reported uncertainty consumed by downstream
    # fleet-localization. Targets per `23_acoustic_detection.py`: ~100 m
    # triangulation RMSE for small-vessel localization, with the
    # per-node σ_pos budget depending on N (≲100 m for N=3, looser at
    # higher N — see figures/26_acoustic_detection.png).

    def pf_err_mean_m(self) -> float:
        return float(np.nanmean(self.pf_err_m))

    def pf_err_max_m(self) -> float:
        return float(np.nanmax(self.pf_err_m))

    def pf_err_p95_m(self) -> float:
        return float(np.nanpercentile(self.pf_err_m, 95))

    def pf_err_frac_over(self, threshold_m: float) -> float:
        """Fraction of mission time where actual pf_err exceeds threshold.
        For a drifter contributing to fleet TDOA, time spent above the
        per-node σ_pos budget is time spent contributing low-quality
        observations to the triangulation."""
        return float((self.pf_err_m > threshold_m).mean())

    def pf_err_at_event_time_p95(
        self, n_events: int = 50, seed: int = 0,
    ) -> float:
        """P95 of pf_err evaluated only at randomly-sampled
        SUBMERGED-LEG ticks — a proxy for the deployment metric
        "σ_pos at acoustic-event timestamps". Excludes surface-dwell
        ticks where the LoRa fix trivially anchors the cluster.

        N event timestamps are sampled uniformly from the submerged-leg
        ticks (deterministic given seed). Returns p95 of pf_err at those
        ticks. NaN if no submerged ticks exist.

        Note: this is the FILTER's pferr at the event tick. The real
        deployment metric uses an RTS-SMOOTHED pferr (which back-projects
        from later observations). Without the smoother (queued post-M2),
        this is the closest available approximation.
        """
        import numpy as np
        submerged = ~self.at_surface_mask
        sub_idx = np.flatnonzero(submerged)
        if sub_idx.size == 0:
            return float("nan")
        rng = np.random.default_rng(seed)
        n_sample = min(n_events, sub_idx.size)
        chosen = rng.choice(sub_idx, size=n_sample, replace=False)
        return float(np.nanpercentile(self.pf_err_m[chosen], 95))

    def pf_err_at_event_time_mean(
        self, n_events: int = 50, seed: int = 0,
    ) -> float:
        """Companion to `pf_err_at_event_time_p95` — mean pferr at the
        same sampled submerged-leg ticks."""
        import numpy as np
        submerged = ~self.at_surface_mask
        sub_idx = np.flatnonzero(submerged)
        if sub_idx.size == 0:
            return float("nan")
        rng = np.random.default_rng(seed)
        n_sample = min(n_events, sub_idx.size)
        chosen = rng.choice(sub_idx, size=n_sample, replace=False)
        return float(np.nanmean(self.pf_err_m[chosen]))

    def pf_std_mean_m(self) -> float:
        return float(np.nanmean(self.pf_std_m))

    def pf_std_p95_m(self) -> float:
        return float(np.nanpercentile(self.pf_std_m, 95))

    def pf_calibration_ratio(self) -> float:
        """Ratio of √mean(pf_err²) to √mean(pf_std²). For a calibrated
        2-D Gaussian PF the expected ratio is ≈ 1 (since pf_std is
        defined as √(var_x + var_y) so √E[pf_err²] / √E[pf_std²] = 1
        when truly Gaussian and isotropic). Ratio > 1 means filter
        over-confident (reports tight σ but actually drifts wider);
        < 1 means filter under-confident."""
        err_rms = float(np.sqrt(np.nanmean(self.pf_err_m ** 2)))
        std_rms = float(np.sqrt(np.nanmean(self.pf_std_m ** 2)))
        if std_rms <= 0:
            return float("nan")
        return err_rms / std_rms


@dataclass
class Experiment:
    """Bundle of everything needed to run one station.

    `truth` must have `sample(lat, lon, depth, t) -> (u, v)` and
    `prior` must have `get_current_at(lat, lon, depth, t) -> (u, v)`.

    `tracer_truth` and `tracer_prior`, if provided, give (T, S) — the
    sensor sees the truth, the PF reweights against the prior. Both
    must have `sample(lat, lon, depth, t) -> (T, S)`. Required when
    `sensor.ctd` is set; ignored otherwise.

    `bias_cfg=None` runs the Phase 1 no-learn baseline (prior only in
    predict, no Kalman update on surface). Providing a `BiasConfig`
    activates the v2 RBPF.
    """
    station: StationConfig
    sim: SimConfig
    sensor: SensorConfig
    pf_cfg: PFConfig
    truth: object
    prior: object
    surfacing: SurfacingPolicy
    bias_cfg: Optional[BiasConfig] = None
    tracer_truth: Optional[object] = None    # samples (T, S) for the sensor's truth obs
    tracer_prior: Optional[object] = None    # samples (T, S) for PF particle predictions
    # Optional perfect-info ceiling: when set, the controller's
    # `keeper.knowledge` is replaced with this object AFTER the normal
    # `LiveBiasKnowledge` wrap, isolating "what would the controller do
    # if it had truth currents while observer/PF stay unchanged?". The
    # PF's predict step still uses `prior + b̂`; only the controller's
    # lookahead consumes this. Must implement `get_current_at` and
    # `get_current_at_batched` (see `PerfectKnowledge`).
    controller_knowledge_override: Optional[object] = None


def run_one_station(
    exp: Experiment, seed: int = 0,
    tick_recorder: Optional[Callable[[float, BallastState, "PositionRBPF",
                                       Optional["BiasFieldState"]], None]] = None,
) -> ExperimentResult:
    """Run one station mission. `tick_recorder`, if provided, is called
    once per tick with (t_sec, state, pf, bias) — used by diagnostics
    to record per-tick ESS, bias offsets, etc., without requiring those
    in ExperimentResult permanently. None disables (default)."""
    rng = np.random.default_rng(seed)
    s = exp.station
    cfg = exp.sim

    # Resolve the process-noise model. Under "ou_integrated", construct
    # a default ProcessNoiseConfig if the user didn't pin one — defaults
    # mirror build_layered_noise_field so the PF predict's per-tick
    # variance matches truth in expectation.
    if cfg.process_noise_model == "ou_integrated":
        pn_cfg = (exp.pf_cfg.process_noise_cfg
                   if exp.pf_cfg.process_noise_cfg is not None
                   else ProcessNoiseConfig())
    else:
        pn_cfg = None

    # Posterior-CVaR requires a bias posterior to draw from. Raise
    # explicitly when bias_cfg is None (silent fallback violates the
    # integrity charter — the user's request was not actually executed).
    if cfg.mpc_scoring == "posterior_cvar" and exp.bias_cfg is None:
        raise ValueError(
            "mpc_scoring='posterior_cvar' requires bias_cfg to be set "
            "(no posterior to draw from when bias_cfg is None). Use "
            "mpc_scoring='ensemble_mean' for no-bias configurations."
        )
    cvar_active = (cfg.mpc_scoring == "posterior_cvar")

    keeper: TrajectoryStationKeeper | MPCStationKeeper
    if cfg.controller == "mpc":
        keeper = MPCStationKeeper(
            station_lat=s.lat, station_lon=s.lon,
            available_depths_m=s.available_depths_m,
            horizon_n=cfg.mpc_horizon_n,
            decision_interval_sec=cfg.control_cadence_sec,
            knowledge=exp.prior,  # type: ignore[arg-type]
            beam_width=cfg.mpc_beam_width,
            w_z_max_ms=cfg.w_z_max_ms,
            dt_sec=cfg.dt_sec,
            process_noise_cfg=pn_cfg,
            sigma_lora_m=exp.sensor.lora.sigma_m,
            surface_threshold_m=exp.sensor.lora.max_depth_m,
            objective_alpha=cfg.mpc_objective_alpha,
            objective_beta=cfg.mpc_objective_beta,
            objective_lambda=(cfg.mpc_objective_lambda
                               if cvar_active else 0.0),
            objective_gamma=(cfg.mpc_objective_gamma
                              if cvar_active else 0.0),
            posterior_cvar_enabled=cvar_active,
            n_posterior_draws=cfg.mpc_n_posterior_draws,
            cvar_alpha=cfg.mpc_cvar_alpha,
            posterior_rng_seed=seed,
        )
    else:
        keeper = TrajectoryStationKeeper(
            station_lat=s.lat, station_lon=s.lon,
            available_depths_m=s.available_depths_m,
            lookahead_sec=cfg.lookahead_sec,
            knowledge=exp.prior,  # type: ignore[arg-type]
            w_z_max_ms=cfg.w_z_max_ms,
            dt_sec=cfg.dt_sec,
        )

    def dyn_current(t_sec, lat, lon, depth_m):
        return exp.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]

    def prior_current(lat, lon, depth_m, t_sec):
        return exp.prior.get_current_at(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]

    # Vectorized companion for `pf.predict`/`sample_currents_at_particles`.
    # Routes the per-particle prior-current sample through one batched RGI
    # call per tick instead of N scalar calls. Falls back to None when the
    # prior doesn't expose `sample_batched` — pf.predict's scalar loop is
    # still correct, just slower.
    if hasattr(exp.prior, "sample_batched"):
        def prior_current_batched(lats_arr, lons_arr, depths_arr, t_sec):
            return exp.prior.sample_batched(  # type: ignore[attr-defined]
                lats_arr, lons_arr, depths_arr, t_sec,
            )
    else:
        prior_current_batched = None  # type: ignore[assignment]
    # Tracer prior — for per-particle CTD predict. The TracerField
    # implements `sample_batched`; older tracer impls without it fall
    # back to the scalar loop.
    if (exp.tracer_prior is not None
        and hasattr(exp.tracer_prior, "sample_batched")):
        def tracer_prior_batched(lats_arr, lons_arr, depths_arr, t_sec):
            return exp.tracer_prior.sample_batched(  # type: ignore[attr-defined]
                lats_arr, lons_arr, depths_arr, t_sec,
            )
    else:
        tracer_prior_batched = None  # type: ignore[assignment]

    # PF initialized at the station with a small prior std. In real
    # deployment the node knows its deployment position exactly, so this
    # is realistic (deployment at surface with GPS ground truth).
    pf = PositionRBPF.init(s.lat, s.lon, exp.pf_cfg.init_sigma_m,
                            exp.pf_cfg.n_particles, rng,
                            process_noise_cfg=pn_cfg)

    # v2 bias-field state.
    bias: BiasFieldState | None = None
    basis: GridBiasBasis | None = None
    if exp.bias_cfg is not None:
        basis = GridBiasBasis(
            station_lat=s.lat, station_lon=s.lon,
            depth_centers_m=tuple(s.available_depths_m),
            n_cells=exp.bias_cfg.n_cells,
            cell_size_m=exp.bias_cfg.cell_size_m,
        )
        bias = BiasFieldState.init(
            pf.n, basis, exp.bias_cfg.sigma_bias_init_ms,
            l_corr_m=exp.bias_cfg.l_corr_m,
            matern_nu=exp.bias_cfg.matern_nu,
            sigma_T_offset_init_c=exp.bias_cfg.sigma_T_offset_init_c,
            sigma_S_offset_init_psu=exp.bias_cfg.sigma_S_offset_init_psu,
        )
        # Initialise per-particle x_start at the deployment position (LoRa fix).
        bias.x_start_lat = np.full(pf.n, s.lat)
        bias.x_start_lon = np.full(pf.n, s.lon)
        # Controller consumes prior + ensemble-mean learned bias, gated
        # on posterior variance (avoid using prior-mean dressed as posterior).
        keeper.knowledge = LiveBiasKnowledge(  # type: ignore[attr-defined]
            nemo_prior=exp.prior, pf=pf, bias=bias, basis=basis,
            posterior_var_gate_ratio=exp.bias_cfg.posterior_var_gate_ratio,
        )

    if exp.controller_knowledge_override is not None:
        keeper.knowledge = exp.controller_knowledge_override  # type: ignore[attr-defined]

    state = BallastState(
        lat=s.lat, lon=s.lon,
        depth_m=cfg.initial_depth_m, depth_setpoint_m=cfg.initial_depth_m,
    )
    n_steps = int(cfg.run_hours * 3600 / cfg.dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    pf_mean_lats = np.zeros(n_steps + 1)
    pf_mean_lons = np.zeros(n_steps + 1)
    pf_err_m = np.zeros(n_steps + 1)
    pf_std_m = np.zeros(n_steps + 1)
    pf_cov_m = np.zeros((n_steps + 1, 2, 2))   # per-tick filtered cov
                                                # (m², lat→y, lon→x), used by
                                                # RTS smoother for retroactive
                                                # σ_pos at acoustic-event times.
                                                # Includes bias path-integral
                                                # contribution via dwell × Matérn cov.
    at_surface_mask = np.zeros(n_steps + 1, dtype=bool)
    lora_fix_mask = np.zeros(n_steps + 1, dtype=bool)  # ticks where a LoRa
                                                        # trilateration update
                                                        # fired (RTS smoother
                                                        # treats these as
                                                        # observation events).

    lats[0], lons[0], depths[0] = state.lat, state.lon, state.depth_m
    pml0, pmo0 = pf.mean()
    pf_mean_lats[0], pf_mean_lons[0] = pml0, pmo0
    pf_err_m[0] = distance_m(state.lat, state.lon, pml0, pmo0)
    # Bias-augmented σ via dwell-quadratic-form path integral (rbpf.py:cov_m).
    # At t=0 dwell is zero → bias contribution is zero → matches ensemble.
    pf_std_m[0] = pf.posterior_std_m(bias, basis)
    pf_cov_m[0] = pf.cov_m(bias, basis)

    t_sec = 0.0
    last_decision = -cfg.control_cadence_sec
    last_lora_attempt_t = -cfg.lora_cadence_sec
    last_flow_update = -1e18
    last_surface_t = 0.0   # assume init at surface
    in_surface_dwell = False
    surface_dwell_end_t = -1.0
    surface_events = 0
    lora_updates = 0
    flow_updates = 0
    bias_updates = 0
    ctd_updates = 0
    last_ctd_update = -1e18
    # Time of last bias-Kalman update for OU evolution. Initialised at
    # t=0 since b̂ posterior is at prior at deploy.
    last_bias_update_t = 0.0
    # Time of last successful position-anchor (LoRa fix or deploy GPS).
    # Distinct from `last_lora_attempt_t`: the latter ticks on every
    # cadence-aligned attempt regardless of outcome (used to rate-limit
    # retries when <3 anchors are in range), whereas this one only
    # advances on a valid fix and feeds the MPC σ_pos rollout's
    # anchor-time. Initialised to 0 since the first anchor is at deploy.
    last_position_anchor_t = 0.0
    # Accumulator for the MPC's predicted σ_pos at the chosen plan's
    # final horizon — averaged across all planning calls in the mission
    # for the calibration diagnostic.
    predicted_sigma_pos_sum = 0.0
    predicted_sigma_pos_n = 0

    # True while the node is currently in an unobserved submerged leg;
    # flipped to False when the leg's Kalman update has been applied at
    # the next surfacing. Initially False: the node starts at surface
    # (GPS-accurate init, no leg to close out).
    leg_active = False

    for i in range(n_steps):
        # --- Surfacing policy ---
        if in_surface_dwell:
            if t_sec >= surface_dwell_end_t:
                in_surface_dwell = False
                # Force MPC replan on the next sub-block: dwell just ended,
                # the LoRa fix from this surface event re-anchored the PF
                # cluster (large info gain). At a 30-min decision cadence
                # the wait was ≤30 min — tolerable. At 2-hour cadence it
                # would be up to 2h holding the pre-surface depth, which
                # bleeds station-keeping. Setting last_decision to a large
                # negative ensures `t_sec - last_decision >= control_cadence_sec`
                # fires unconditionally on the next pass.
                last_decision = -1e18
            else:
                state = set_setpoint(state, 0.5)

        if not in_surface_dwell:
            tsu = t_sec - last_surface_t
            # Honest σ via dwell-quadratic-form path integral. The end-
            # of-previous-tick recording already populated `pf_std_m[i]`
            # via cov_m(bias, basis); the bias state hasn't mutated since,
            # so reading the cached value is exact, not an approximation.
            # Saves one heavy einsum per tick (cov_m ≈ 42 ms × 144 ticks).
            _sd_sigma = float(pf_std_m[i])
            if exp.surfacing.should_surface(t_sec, tsu, _sd_sigma):
                in_surface_dwell = True
                surface_dwell_end_t = t_sec + cfg.surface_dwell_h * 3600.0
                state = set_setpoint(state, 0.5)
                surface_events += 1
                last_surface_t = t_sec
            elif t_sec - last_decision >= cfg.control_cadence_sec - 1e-6:
                pml, pmo = pf.mean()
                if isinstance(keeper, MPCStationKeeper):
                    t_since_anchor = max(t_sec - last_position_anchor_t, 0.0)
                    pred_t = exp.surfacing.predicted_next_surface_time_sec(
                        t_sec, last_surface_t,
                    )
                    # Empirical surface-rate estimate λ = events / elapsed
                    # for the σ rollout's hazard-dilution term. Robust to
                    # all SurfacingPolicy types — captures the true
                    # frequency for uncertainty-gated and event-driven
                    # policies whose deadline-based prediction is
                    # systematically pessimistic. Need ≥2 surfaces
                    # observed before the rate is meaningful; until then,
                    # leave None and rely on next_surface_time_sec only.
                    hazard_rate: float | None = None
                    if surface_events >= 2 and t_sec > 1.0:
                        hazard_rate = surface_events / float(t_sec)
                    chosen, _ = keeper.choose_depth(
                        state.lat, state.lon, t_sec,
                        current_depth_m=state.depth_m,
                        perceived_lat=pml, perceived_lon=pmo,
                        # Same cached value as `_sd_sigma` above —
                        # `pf_std_m[i]` was set at end of tick i-1 and
                        # the bias state hasn't mutated since.
                        sigma_pos_init_m=float(pf_std_m[i]),
                        t_since_last_anchor_sec=t_since_anchor,
                        next_surface_time_sec=pred_t,
                        surface_hazard_rate_per_sec=hazard_rate,
                    )
                    if np.isfinite(keeper.last_predicted_sigma_pos_horizon_m):
                        predicted_sigma_pos_sum += (
                            keeper.last_predicted_sigma_pos_horizon_m
                        )
                        predicted_sigma_pos_n += 1
                else:
                    chosen, _ = keeper.choose_depth(
                        state.lat, state.lon, t_sec,
                        current_depth_m=state.depth_m,
                        perceived_lat=pml, perceived_lon=pmo,
                    )
                state = set_setpoint(state, chosen)
                last_decision = t_sec

        # --- Advance truth ---
        state = step(state, t_sec, cfg.dt_sec,
                      current_at=dyn_current, w_z_max_ms=cfg.w_z_max_ms)

        # --- PF predict (with optional per-particle bias correction) ---
        # Bias accumulation uses the SHADOW trajectory (b̂-independent
        # dead-reckoning), not the real PF positions, so H = dwell stays
        # independent of b̂ — RBPF correctness per Schön/Gustafsson/
        # Nordlund 2005. The real PF advects with prior + b̂ (so it
        # tracks reality with the controller's current bias estimate);
        # the shadow advects with prior alone.
        in_leg_this_tick = state.depth_m > exp.sensor.lora.max_depth_m
        extra_u_ms: np.ndarray | None = None
        extra_v_ms: np.ndarray | None = None
        if bias is not None and basis is not None:
            # Bias lookup at SHADOW position (input to real-PF advection).
            extra_u_ms, extra_v_ms = bias.lookup_and_accumulate(
                basis, pf.shadow_lats, pf.shadow_lons, state.depth_m, cfg.dt_sec,
                accumulate=in_leg_this_tick,
            )
            if in_leg_this_tick:
                leg_active = True
        # Predict both real and shadow with same process noise.
        # Returns (shadow_prior_u, shadow_prior_v) per particle that the
        # bias-Kalman uses to accumulate prior_disp along the shadow.
        # Under "ou_integrated" the per-component OU state evolves and
        # drives the perturbation; under "iid_legacy" the legacy
        # process_noise_ms scalar is used.
        shadow_prior_u, shadow_prior_v = pf.predict(
            cfg.dt_sec, t_sec, state.depth_m, prior_current,
            extra_u_ms=extra_u_ms, extra_v_ms=extra_v_ms,
            process_noise_ms=exp.pf_cfg.process_noise_ms, rng=rng,
            process_noise_cfg=pn_cfg,
            current_at_batched=prior_current_batched,
        )
        # Accumulate prior_disp along shadow trajectory (only during
        # actual submerged-leg ticks; surface-dwell ticks don't count
        # for the leg observation).
        if bias is not None and in_leg_this_tick:
            bias.accumulate_prior_disp(shadow_prior_u, shadow_prior_v, cfg.dt_sec)

        t_sec += cfg.dt_sec

        # --- Flow sensor observation ---
        if (exp.sensor.flow is not None
            and t_sec - last_flow_update >= exp.sensor.flow.cadence_sec - 1e-6):
            # Only fire when node is not rapidly transitioning in depth.
            dz_expected = state.depth_setpoint_m - state.depth_m
            if abs(dz_expected) < 1.0:  # node is stable at setpoint
                u_truth, v_truth = exp.truth.sample(  # type: ignore[attr-defined]
                    state.lat, state.lon, state.depth_m, t_sec)
                if np.isfinite(u_truth) and np.isfinite(v_truth):
                    z_u, z_v = exp.sensor.flow.sample(u_truth, v_truth, rng)
                    us, vs = pf.sample_currents_at_particles(
                        state.depth_m, t_sec, prior_current,
                        current_at_batched=prior_current_batched,
                    )
                    logL = exp.sensor.flow.log_likelihood_per_particle(
                        us, vs, z_u, z_v)
                    pf.reweight(logL)
                    idx = pf.maybe_resample(rng, exp.pf_cfg.ess_resample_ratio)
                    if idx is not None and bias is not None:
                        bias.gather(idx)
                    last_flow_update = t_sec
                    flow_updates += 1

        # --- CTD observation (every submerged tick, no transition gating) ---
        # T/S are scalar properties of the water; vertical motion does
        # not compromise the reading the way it does for current
        # sensors. Fires whenever the node is below the LoRa surface
        # threshold and tracer truth/prior are wired up.
        is_submerged = state.depth_m > exp.sensor.lora.max_depth_m
        if (exp.sensor.ctd is not None
            and exp.tracer_truth is not None
            and exp.tracer_prior is not None
            and is_submerged
            and t_sec - last_ctd_update >= exp.sensor.ctd.cadence_sec - 1e-6):
            T_truth, S_truth = exp.tracer_truth.sample(  # type: ignore[attr-defined]
                state.lat, state.lon, state.depth_m, t_sec)
            if np.isfinite(T_truth) and np.isfinite(S_truth):
                z_T, z_S = exp.sensor.ctd.sample(T_truth, S_truth, rng)
                # Per-particle (T, S) prediction at each particle's
                # (lat, lon, depth, t). Vectorized when the tracer prior
                # exposes `sample_batched` — one RGI call instead of N.
                if tracer_prior_batched is not None:
                    depths_arr = np.full(pf.n, state.depth_m,
                                           dtype=np.float64)
                    T_pp, S_pp = tracer_prior_batched(
                        pf.lats, pf.lons, depths_arr, t_sec,
                    )
                    T_pp = np.asarray(T_pp, dtype=np.float64)
                    S_pp = np.asarray(S_pp, dtype=np.float64)
                else:
                    T_pp = np.zeros(pf.n)
                    S_pp = np.zeros(pf.n)
                    for k in range(pf.n):
                        T_pp[k], S_pp[k] = exp.tracer_prior.sample(  # type: ignore[attr-defined]
                            pf.lats[k], pf.lons[k], state.depth_m, t_sec)
                # Replace NaN with truth value to make likelihood neutral
                # at out-of-bounds particles (they're already going to
                # be killed by the truth-out-of-bounds dynamics).
                T_pp = np.where(np.isfinite(T_pp), T_pp, z_T)
                S_pp = np.where(np.isfinite(S_pp), S_pp, z_S)
                # Bias-aware likelihood. Each particle predicts the CTD
                # reading as `T_pp + bias_T_offset[k]`; the residual is
                # what the sensor sees minus that prediction. The
                # likelihood σ inflates the instrument noise by the
                # particle's posterior bias variance + the
                # spatial-fluctuation σ that's not in the bias state.
                # Without this, the bias-blind likelihood (Step 2.1
                # data) pulls particles to spurious positions where
                # prior gradients accidentally cancel the basin offset.
                if bias is not None and exp.bias_cfg is not None:
                    bcfg = exp.bias_cfg
                    sigma_T_obs_sq = (
                        exp.sensor.ctd.sigma_T_c ** 2
                        + bias.P_T_offset
                        + bcfg.sigma_T_fluct_c ** 2
                    )                                                # (N,)
                    sigma_S_obs_sq = (
                        exp.sensor.ctd.sigma_S_psu ** 2
                        + bias.P_S_offset
                        + bcfg.sigma_S_fluct_psu ** 2
                    )                                                # (N,)
                    r_T = z_T - T_pp - bias.bias_T_offset
                    r_S = z_S - S_pp - bias.bias_S_offset
                    logL = -0.5 * (
                        r_T ** 2 / sigma_T_obs_sq
                        + r_S ** 2 / sigma_S_obs_sq
                        + np.log(sigma_T_obs_sq)
                        + np.log(sigma_S_obs_sq)
                    )
                else:
                    logL = exp.sensor.ctd.log_likelihood_per_particle(
                        T_pp, S_pp, z_T, z_S)
                pf.reweight(logL)
                idx = pf.maybe_resample(rng, exp.pf_cfg.ess_resample_ratio)
                if idx is not None and bias is not None:
                    bias.gather(idx)
                # Per-particle Kalman update on bias offset using the
                # SAME σ_obs as the likelihood. After resample, surviving
                # particles inherit their parents' bias estimates and
                # then take a step toward the residual.
                if bias is not None and exp.bias_cfg is not None:
                    bcfg2 = exp.bias_cfg
                    # OU inflation between updates — restores the
                    # likelihood σ floor to a fluctuation-honest value
                    # before this update. Without this, P_T/P_S_offset
                    # shrink monotonically and the filter falsely
                    # believes its bias estimate is exact (driving the
                    # calib > 1 over-confidence in grid+ctd configs).
                    if last_ctd_update > -1e17:
                        dt_since = t_sec - last_ctd_update
                        bias.ou_evolve_tracer_offset(
                            dt_sec=float(dt_since),
                            tau_sec=bcfg2.tau_ou_sec,
                            sigma_T_inf_sq=bcfg2.sigma_T_offset_init_c ** 2,
                            sigma_S_inf_sq=bcfg2.sigma_S_offset_init_psu ** 2,
                        )
                    bias.kalman_update_tracer_offset(
                        z_T=float(z_T), z_S=float(z_S),
                        T_pp=T_pp, S_pp=S_pp,
                        sigma_T_obs_per_part=np.sqrt(sigma_T_obs_sq),
                        sigma_S_obs_per_part=np.sqrt(sigma_S_obs_sq),
                    )
                last_ctd_update = t_sec
                ctd_updates += 1

        # --- LoRa ranging ---
        now_at_surface = state.depth_m <= exp.sensor.lora.max_depth_m
        at_surface_mask[i + 1] = now_at_surface
        if (now_at_surface
            and t_sec - last_lora_attempt_t >= cfg.lora_cadence_sec - 1e-6):
            # Fire a LoRa range observation and multilaterate to a direct
            # position fix. σ_at_fix is now geometry-derived
            # (σ_per_anchor × HDOP) — varies across the bbox depending on
            # the drifter's position relative to the fixed anchor cluster.
            # Out-of-range anchors return NaN ranges; the multilaterater
            # filters them and returns NaN tuple if <3 valid anchors,
            # at which point we skip the fix entirely (PF inflates σ_pos
            # until next valid coverage).
            z_list = exp.sensor.lora.sample(state.lat, state.lon, rng)
            tri_lat, tri_lon, tri_sigma = trilaterate_lora(
                list(exp.sensor.lora.anchors), z_list, state.lat, state.lon,
                sigma_per_anchor_m=exp.sensor.lora.sigma_m,
            )
            have_fix = (np.isfinite(tri_lat)
                        and np.isfinite(tri_lon)
                        and np.isfinite(tri_sigma))
            # Always tick cadence so we don't retry every step on
            # repeated <3-anchor failures.
            last_lora_attempt_t = t_sec
            if have_fix:
                # Leg-end Kalman update on bias. Runs ONCE per submerged leg,
                # on the first LoRa fire after resurfacing.
                #
                # Analytical observation (b̂-independent):
                #   y_obs[i] = tri_pos − x_start[i] − prior_disp[i]
                # where x_start[i] is the particle's position at leg-start
                # (snapshotted from the SHADOW trajectory, which equals the
                # real PF after the previous LoRa fix) and prior_disp[i] is
                # the accumulated ∫ prior_velocity dt along the SHADOW
                # trajectory during this leg. This decouples the bias-Kalman
                # observation from the position PF's CTD/LoRa reweighting,
                # fixing the cannibalisation bug surfaced 2026-04-25.
                if (bias is not None and basis is not None and leg_active
                    and exp.bias_cfg is not None):
                    obs_cfg = exp.bias_cfg
                    # OU evolution from last update to now: shrinks mean
                    # toward 0 and inflates cov toward prior P_∞ —
                    # captures the slow drift of the true bias field at
                    # τ_slow.
                    leg_duration = t_sec - last_bias_update_t
                    bias.ou_evolve(leg_duration, obs_cfg.tau_ou_sec)
                    cos_lat = float(np.cos(np.deg2rad(state.lat)))
                    # y_obs (per particle, per component): tri_pos minus
                    # x_start minus prior-only integrated displacement.
                    y_obs_east = ((tri_lon - bias.x_start_lon) * EARTH_R_M * cos_lat
                                  - bias.prior_disp_east)
                    y_obs_north = ((tri_lat - bias.x_start_lat) * EARTH_R_M
                                   - bias.prior_disp_north)
                    # Per-leg σ_obs decomposition (Dee 2005 §3) +
                    # geometry-derived LoRa-fix σ via override. Per-fix
                    # σ_at_fix from the multilaterater (σ_per_anchor ×
                    # HDOP) lets the observation budget track geometry —
                    # bbox-edge drifters with HDOP~3 see σ_obs ~3× larger
                    # than centroid drifters with HDOP~1.
                    sigma_obs_per_part = _compute_sigma_obs_per_particle(
                        obs_cfg, bias.dwell,
                        np.asarray(basis.depth_centers_m, dtype=float),
                        leg_duration_sec=leg_duration,
                        # σ on (x_start + prior_disp) for the bias-Kalman
                        # observation-noise budget. Uses ENSEMBLE-ONLY σ
                        # (process noise + LoRa fix history) — NOT the
                        # bias-augmented σ. Otherwise we double-count: the
                        # bias-state's posterior cov is in HPH (the Kalman's
                        # signal term) AND would be in σ_obs (the Kalman's
                        # noise term). That double-count suppressed Kalman
                        # gain and kept posterior cov stuck near prior.
                        sigma_x_start_m=pf.posterior_std_m(),
                        sigma_lora_end_m_override=tri_sigma,
                    )
                    bias.kalman_update_leg(
                        y_obs_east, y_obs_north, sigma_obs_per_part,
                    )
                    last_bias_update_t = t_sec
                    # Reset leg accumulators for the next leg, anchored at
                    # the current shadow position (which after the LoRa
                    # event will be re-anchored via reinit below).
                    leg_active = False
                    bias_updates += 1

                # Validation-gated PF reinit at surface. A ballistic-
                # submerged-leg PF can drift its cluster kilometres off
                # truth while remaining internally tight (PFstd~10 m);
                # when that cluster surfaces the LoRa reweight is
                # degenerate (every particle is equally wrong). Replace
                # particles around the multilateration fix whenever the
                # cluster mean disagrees by more than
                # `reinit_threshold_m`. Matches REMUS/HUGIN's GPS-surface
                # reset pattern (Paull 2014). Reinit σ now respects the
                # geometry-derived σ_at_fix (use whichever is larger so
                # bbox-edge drifters with degenerate geometry get a
                # wider scatter that reflects the actual fix uncertainty).
                cos_lat = float(np.cos(np.deg2rad(state.lat)))
                pf_mean_lat, pf_mean_lon = pf.mean()
                gap_m = distance_m(pf_mean_lat, pf_mean_lon, tri_lat, tri_lon)
                if gap_m > exp.pf_cfg.reinit_threshold_m:
                    sigma_m = max(exp.pf_cfg.reinit_sigma_m, tri_sigma)
                    dlat = rng.normal(0.0, sigma_m / EARTH_R_M, pf.n)
                    dlon = rng.normal(0.0, sigma_m / (EARTH_R_M * cos_lat), pf.n)
                    pf.lats = tri_lat + dlat
                    pf.lons = tri_lon + dlon
                    # Re-anchor the SHADOW trajectory at the LoRa fix too —
                    # both PFs share the truth-anchor at the surface event.
                    pf.shadow_lats = tri_lat + dlat.copy()
                    pf.shadow_lons = tri_lon + dlon.copy()
                    pf.weights = np.full(pf.n, 1.0 / pf.n)

                # Standard LoRa reweight + resample. After a reinit the
                # cluster is already around truth with σ_reinit spread;
                # this tightens it further to the ranging σ.
                # log_likelihood_per_particle skips NaN observations
                # (out-of-range anchors), so only in-range anchors
                # contribute to the reweight.
                logL = exp.sensor.lora.log_likelihood_per_particle(
                    pf.lats, pf.lons, z_list)
                pf.reweight(logL)
                idx = pf.maybe_resample(rng, exp.pf_cfg.ess_resample_ratio)
                if idx is not None and bias is not None:
                    bias.gather(idx)
                last_position_anchor_t = t_sec
                lora_updates += 1
                lora_fix_mask[i + 1] = True

                # Snapshot leg-start state for the NEXT submerged leg's
                # bias-Kalman observation. After this LoRa fix:
                #   x_start = current shadow position (anchored to truth)
                #   prior_disp = 0
                #   dwell = 0
                # This is the cleanest re-anchor — every leg's
                # observation references the LoRa fix that started it.
                if bias is not None:
                    bias.reset_leg_accumulators(
                        pf.shadow_lats.copy(), pf.shadow_lons.copy(),
                    )

        # --- Record ---
        lats[i + 1], lons[i + 1], depths[i + 1] = state.lat, state.lon, state.depth_m
        ml, mo = pf.mean()
        pf_mean_lats[i + 1], pf_mean_lons[i + 1] = ml, mo
        pf_err_m[i + 1] = distance_m(state.lat, state.lon, ml, mo)
        # Honest σ via dwell-quadratic-form path integral over the leg.
        # `bias.dwell` is auto-zeroed at LoRa fixes per
        # `reset_leg_accumulators`, so cov_m reflects the integrated
        # bias variance along the trajectory since the last fix.
        # Compute cov once and derive std from it (posterior_std_m would
        # otherwise call cov_m a second time with the same inputs).
        c_post = pf.cov_m(bias, basis)
        pf_cov_m[i + 1] = c_post
        pf_std_m[i + 1] = float(np.sqrt(0.5 * (c_post[0, 0] + c_post[1, 1])))

        # Diagnostic hook (optional). Called per tick AFTER the state
        # advance so the recorder sees the post-tick ESS, pferr, σ.
        if tick_recorder is not None:
            tick_recorder(t_sec, state, pf, bias)  # type: ignore[misc]

    dists = np.array([distance_m(la, lo, s.lat, s.lon)
                       for la, lo in zip(lats, lons)])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        dists = (np.where(valid, dists, dists[last[-1]]) if len(last) > 0
                  else np.full_like(dists, np.inf))

    bias_summary: dict[str, float] = {
        "learned_fraction": 0.0,
        "mean_learned_mag_ms": 0.0,
        "max_learned_mag_ms": 0.0,
        "mean_learned_var_ms2": 0.0,
    }
    bias_T_final = 0.0
    bias_S_final = 0.0
    if bias is not None and exp.bias_cfg is not None:
        bias_summary = bias.learned_summary(
            var_init_ms2=exp.bias_cfg.sigma_bias_init_ms ** 2,
        )
        # Ensemble-mean (T, S) bias-offset estimate — should converge
        # toward the truth's `mean_S_coh_psu` / `mean_T_coh_c` as CTD
        # ticks accumulate.
        w = pf.weights
        bias_T_final = float(np.sum(bias.bias_T_offset * w))
        bias_S_final = float(np.sum(bias.bias_S_offset * w))

    pred_sigma_horizon_mean = (
        predicted_sigma_pos_sum / predicted_sigma_pos_n
        if predicted_sigma_pos_n > 0 else float("nan")
    )

    return ExperimentResult(
        lats=lats, lons=lons, depths=depths, dists_m=dists,
        pf_mean_lats=pf_mean_lats, pf_mean_lons=pf_mean_lons,
        pf_err_m=pf_err_m, pf_std_m=pf_std_m,
        pf_cov_m=pf_cov_m,
        at_surface_mask=at_surface_mask,
        lora_fix_mask=lora_fix_mask,
        surface_events=surface_events,
        lora_updates=lora_updates,
        flow_updates=flow_updates,
        bias_updates=bias_updates,
        bias_learned_fraction=bias_summary["learned_fraction"],
        bias_mean_learned_mag_ms=bias_summary["mean_learned_mag_ms"],
        bias_max_learned_mag_ms=bias_summary["max_learned_mag_ms"],
        bias_mean_learned_var_ms2=bias_summary["mean_learned_var_ms2"],
        bias_T_offset_final_c=bias_T_final,
        bias_S_offset_final_psu=bias_S_final,
        ctd_updates=ctd_updates,
        predicted_sigma_pos_horizon_mean=pred_sigma_horizon_mean,
    )
