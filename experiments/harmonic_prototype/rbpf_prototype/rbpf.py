"""Position-only RBPF for v1.

State = (lat, lon) per particle, uniform weights at init. For v2 we'll
extend each particle with a reduced-rank bias-field coefficient vector
(Rao-Blackwellised out — linear-Gaussian conditional update per particle).

Uses filterpy.monte_carlo for battle-tested resampling; the rest is
thin numpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np  # type: ignore[import-not-found]
from filterpy.monte_carlo import systematic_resample  # type: ignore[import-not-found]

from process_noise import (  # type: ignore[import-not-found]
    ProcessNoiseConfig,
    inertial_profile,
    ou_step,
    plume_profile,
    submeso_profile,
)
from truth_field import EARTH_R_M  # type: ignore[import-not-found]


# Callable type: (lat, lon, depth_m, t_sec) -> (u, v). Used in predict to
# advect particles. Pass the prior-source's get_current_at here.
CurrentAt = Callable[[float, float, float, float], tuple[float, float]]

# Vectorized companion: (lats, lons, depths, t_sec) -> (u_arr, v_arr).
# When provided to `PositionRBPF.predict`/`sample_currents_at_particles`,
# field samples are issued as one batched RGI call instead of a Python
# loop over `n_particles`. Out-of-bounds points should be NaN; callers
# zero them downstream.
CurrentAtBatched = Callable[
    [np.ndarray, np.ndarray, np.ndarray, float],
    tuple[np.ndarray, np.ndarray],
]


# Names of the OU per-component velocity-amplitude states carried per
# particle. Each is shape (N, 2) for (u, v). `inertial_c1` and
# `inertial_c2` together represent the rotating-amplitude near-inertial
# velocity per particle (instantaneous v = c1·cos(ft) + c2·sin(ft) per
# direction times the surface-trap profile).
_PN_COMPONENTS = (
    "coh", "plume", "submeso", "inertial_c1", "inertial_c2", "white",
)


@dataclass
class PositionRBPF:
    """Bootstrap particle filter over (lat, lon) with parallel shadow trajectory.

    Invariants: `weights` are probabilities (sum to 1).

    **Shadow trajectory**: alongside the real per-particle (lats, lons),
    each particle carries (shadow_lats, shadow_lons) that advect with
    `prior + process_noise` ONLY — no bias correction (b̂), no CTD
    reweight, no LoRa reweight. The shadow is used by the bias-Kalman to
    compute observations that don't depend on b̂ (the H-depends-on-b̂ RBPF
    correctness violation flagged by the 2026-04-25 stats review).

    On resample, both real and shadow positions are gathered by the same
    index — they always represent the same particle identity.

    The shadow's process noise samples are the SAME as the real particle's
    (same RNG draws) — so when b̂ ≈ 0 the shadow ≈ real PF, and the
    shadow's divergence from real reflects the cumulative effect of b̂
    advection in the real PF and reweighting.
    """

    lats: np.ndarray             # (N,) real position
    lons: np.ndarray
    weights: np.ndarray          # (N,)
    shadow_lats: np.ndarray      # (N,) b̂-independent dead-reckoning
    shadow_lons: np.ndarray
    # Per-component OU velocity-amplitude state. Each entry shape (N, 2)
    # for (u-amplitude, v-amplitude). Empty dict = iid_legacy mode (the
    # legacy `process_noise_ms` path is used in `predict`). Component
    # names listed in `_PN_COMPONENTS`.
    pn_state: dict[str, np.ndarray] = field(default_factory=dict)

    @staticmethod
    def init(mean_lat: float, mean_lon: float, sigma_m: float,
             n: int, rng: np.random.Generator,
             process_noise_cfg: ProcessNoiseConfig | None = None,
             ) -> "PositionRBPF":
        """Initialise N particles at (mean_lat, mean_lon) ± sigma_m.

        When `process_noise_cfg` is provided, the per-particle OU state
        is drawn from each component's stationary N(0, σ_c²) so the
        first predict step is already "warmed up". When None, the PF
        runs in iid_legacy mode and `predict` consumes `process_noise_ms`.
        """
        cos_lat = float(np.cos(np.deg2rad(mean_lat)))
        dlat = rng.normal(0.0, sigma_m / EARTH_R_M, n)
        dlon = rng.normal(0.0, sigma_m / (EARTH_R_M * cos_lat), n)
        lats = mean_lat + dlat
        lons = mean_lon + dlon
        pn_state: dict[str, np.ndarray] = {}
        if process_noise_cfg is not None:
            cfg = process_noise_cfg
            sigma_per = {
                "coh": cfg.sigma_coh_ms,
                "plume": cfg.sigma_plume_ms,
                "submeso": cfg.sigma_submeso_ms,
                "inertial_c1": cfg.sigma_inertial_ms,
                "inertial_c2": cfg.sigma_inertial_ms,
                "white": cfg.sigma_white_ms,
            }
            for name in _PN_COMPONENTS:
                pn_state[name] = (sigma_per[name]
                                   * rng.standard_normal(size=(n, 2)))
        return PositionRBPF(
            lats=lats,
            lons=lons,
            weights=np.full(n, 1.0 / n),
            # Shadow starts at the same init as real (LoRa fix at deploy
            # is shared truth observation; both PFs are anchored there).
            shadow_lats=lats.copy(),
            shadow_lons=lons.copy(),
            pn_state=pn_state,
        )

    @property
    def n(self) -> int:
        return len(self.lats)

    def mean(self) -> tuple[float, float]:
        return (float(np.sum(self.lats * self.weights)),
                float(np.sum(self.lons * self.weights)))

    def cov_m(self, bias=None, basis=None) -> np.ndarray:
        """Weighted 2×2 position covariance in meters (lat→y, lon→x).

        When bias context (`bias`, `basis`) is provided, adds the
        per-particle Matérn bias-state contribution to position
        uncertainty as a **path integral** over the actual trajectory:

            Var[∫ b(x(τ)) dτ] = Σ_d Σ_ij D_d[i] D_d[j] cov_d[i, j]

        where D_d[i] is the per-particle dwell time in cell (d, i) since
        the last LoRa fix (in seconds; `bias.dwell` is auto-zeroed at
        fixes per `bias_field.py:reset_leg_accumulators`), and cov_d is
        the full Matérn covariance INCLUDING off-diagonals (spatial
        correlations between cells the particle integrated through).

        This propagates the integrated bias-velocity variance the
        particle has ACTUALLY been advecting with — uses the cells the
        drifter visited at the depths it visited them, with the
        post-Kalman cov at dwell-cells (small, observed) and prior cov
        at unvisited cells (zero dwell weight, contributes nothing).
        Self-times via dwell — no Δt_since_lora parameter needed.

        With no bias context (default): returns ensemble spread only.
        """
        ml, mo = self.mean()
        cos_lat = float(np.cos(np.deg2rad(ml)))
        dy = (self.lats - ml) * EARTH_R_M
        dx = (self.lons - mo) * EARTH_R_M * cos_lat
        vx = float(np.sum(self.weights * dx * dx))
        vy = float(np.sum(self.weights * dy * dy))
        vxy = float(np.sum(self.weights * dx * dy))
        base = np.array([[vx, vxy], [vxy, vy]])
        if bias is None or basis is None:
            return base
        n_p, n_d, n_y, n_x = bias.dwell.shape
        n_flat = n_y * n_x
        D = bias.dwell.reshape(n_p, n_d, n_flat)   # (N, D, Y·X) sec
        # Per-particle path-integral variance per axis:
        # Σ_d D_d^T cov_d D_d. Units: s · (m/s)² · s = m².
        qf_u = np.einsum('ndi,ndij,ndj->n', D, bias.cov_u, D)
        qf_v = np.einsum('ndi,ndij,ndj->n', D, bias.cov_v, D)
        var_u = float(np.sum(self.weights * qf_u))
        var_v = float(np.sum(self.weights * qf_v))
        # cov entries [[east, ...], [..., north]]: var_u (east) → vx.
        return base + np.array([[var_u, 0.0], [0.0, var_v]])

    def posterior_std_m(self, bias=None, basis=None) -> float:
        """1-σ position uncertainty. With bias context: includes
        per-particle Matérn bias-state path-integral contribution. See
        `cov_m`. Without: ensemble spread only (legacy behavior)."""
        c = self.cov_m(bias, basis)
        return float(np.sqrt(0.5 * (c[0, 0] + c[1, 1])))

    def ess(self) -> float:
        return float(1.0 / np.sum(self.weights**2))

    # --- predict / update / resample ------------------------------------

    def predict(self, dt_sec: float, t_sec: float, depth_m: float,
                current_at: CurrentAt,
                process_noise_ms: float = 0.0,
                rng: np.random.Generator | None = None,
                extra_u_ms: np.ndarray | None = None,
                extra_v_ms: np.ndarray | None = None,
                shadow_prior_us: np.ndarray | None = None,
                shadow_prior_vs: np.ndarray | None = None,
                process_noise_cfg: ProcessNoiseConfig | None = None,
                current_at_batched: "CurrentAtBatched | None" = None,
                ) -> tuple[np.ndarray, np.ndarray]:
        """Advect every particle by `dt_sec` using the current predicted
        by `current_at` at that particle's (lat, lon, depth, t).

        Real particle (lats, lons) advects with `prior + extra_*_ms +
        process_noise`. SHADOW particle (shadow_lats, shadow_lons)
        advects with `prior + process_noise` ONLY — no extra_*. Same
        process-noise draw used for both so they're correlated; shadow
        diverges from real only by the cumulative b̂ contribution
        (extra_*_ms) and any reweight-driven resample selection.

        **Process-noise model.** Two modes:
          - `process_noise_cfg` provided AND `pn_state` populated:
            OU-integrated per-component model. Each tick, every per-
            component amplitude state evolves via the exact OU update
            (η ← γ·η + σ·√(1-γ²)·z); the per-particle perturbation
            is the depth-weighted sum across components (inertial
            includes the cos(ft)/sin(ft) rotation).
          - Otherwise (default): legacy iid mode — i.i.d. N(0, σ²) per
            tick using `process_noise_ms`. Preserved for ablations and
            backward-compat with experiments that don't wire OU.

        Returns the per-particle (prior_u, prior_v) sampled at the SHADOW
        position — which is what the bias-Kalman should accumulate as
        prior_disp (the b̂-independent prior integral). When `shadow_prior_us`
        and `shadow_prior_vs` are passed in, those are used directly
        (lets the experiment loop reuse a single prior_at sample for
        both shadow advection and prior_disp accumulation).
        """
        # Pre-evolve the OU state (one shared step for the whole tick).
        # Each component's draw is independent across particles — captures
        # the per-particle hypothesis "this could be the noise the node
        # actually saw". Both real and shadow advect under the SAME
        # post-evolve sample so they remain correlated as required by
        # the bias-Kalman shadow contract.
        pert_u: np.ndarray | None = None
        pert_v: np.ndarray | None = None
        if process_noise_cfg is not None and self.pn_state and rng is not None:
            cfg = process_noise_cfg
            # Step every component.
            self.pn_state["coh"] = ou_step(
                self.pn_state["coh"], cfg.sigma_coh_ms, cfg.tau_coh_sec,
                dt_sec, rng,
            )
            self.pn_state["plume"] = ou_step(
                self.pn_state["plume"], cfg.sigma_plume_ms, cfg.tau_plume_sec,
                dt_sec, rng,
            )
            self.pn_state["submeso"] = ou_step(
                self.pn_state["submeso"], cfg.sigma_submeso_ms,
                cfg.tau_submeso_sec, dt_sec, rng,
            )
            self.pn_state["inertial_c1"] = ou_step(
                self.pn_state["inertial_c1"], cfg.sigma_inertial_ms,
                cfg.tau_inertial_sec, dt_sec, rng,
            )
            self.pn_state["inertial_c2"] = ou_step(
                self.pn_state["inertial_c2"], cfg.sigma_inertial_ms,
                cfg.tau_inertial_sec, dt_sec, rng,
            )
            self.pn_state["white"] = ou_step(
                self.pn_state["white"], cfg.sigma_white_ms, cfg.tau_white_sec,
                dt_sec, rng,
            )
            # Vertical-profile factors (scalar at this depth).
            p_z = float(plume_profile(depth_m, cfg))
            s_z = float(submeso_profile(depth_m, cfg))
            i_z = float(inertial_profile(depth_m, cfg))
            cos_ft = float(np.cos(cfg.f_rad_per_sec * t_sec))
            sin_ft = float(np.sin(cfg.f_rad_per_sec * t_sec))
            c1 = self.pn_state["inertial_c1"]
            c2 = self.pn_state["inertial_c2"]
            inertial_u = i_z * (c1[:, 0] * cos_ft + c2[:, 0] * sin_ft)
            inertial_v = i_z * (-c1[:, 1] * sin_ft + c2[:, 1] * cos_ft)
            pert_u = (
                self.pn_state["coh"][:, 0]
                + p_z * self.pn_state["plume"][:, 0]
                + s_z * self.pn_state["submeso"][:, 0]
                + inertial_u
                + self.pn_state["white"][:, 0]
            )
            pert_v = (
                self.pn_state["coh"][:, 1]
                + p_z * self.pn_state["plume"][:, 1]
                + s_z * self.pn_state["submeso"][:, 1]
                + inertial_v
                + self.pn_state["white"][:, 1]
            )

        # Sample prior at SHADOW positions (b̂-independent). Caller may
        # pre-supply (shadow_prior_us, shadow_prior_vs) to share one
        # `current_at` sample with downstream `bias.accumulate_prior_disp`.
        N = self.n
        depths_arr = np.full(N, depth_m, dtype=np.float64)
        if shadow_prior_us is None or shadow_prior_vs is None:
            if current_at_batched is not None:
                u_s_arr, v_s_arr = current_at_batched(
                    self.shadow_lats, self.shadow_lons, depths_arr, t_sec,
                )
                u_s_arr = np.where(np.isfinite(u_s_arr), u_s_arr, 0.0)
                v_s_arr = np.where(np.isfinite(v_s_arr), v_s_arr, 0.0)
            else:
                u_s_arr = np.zeros(N)
                v_s_arr = np.zeros(N)
                for i in range(N):
                    u_s, v_s = current_at(
                        float(self.shadow_lats[i]), float(self.shadow_lons[i]),
                        depth_m, t_sec,
                    )
                    u_s_arr[i] = u_s if np.isfinite(u_s) else 0.0
                    v_s_arr[i] = v_s if np.isfinite(v_s) else 0.0
        else:
            u_s_arr = np.asarray(shadow_prior_us, dtype=np.float64)
            v_s_arr = np.asarray(shadow_prior_vs, dtype=np.float64)
        # Output buffer is the SHADOW's prior sample BEFORE noise — what
        # the bias-Kalman accumulates as `prior_disp`.
        out_prior_u = u_s_arr.copy()
        out_prior_v = v_s_arr.copy()

        # Sample prior at REAL positions (for real-PF advection).
        if current_at_batched is not None:
            u_r_arr, v_r_arr = current_at_batched(
                self.lats, self.lons, depths_arr, t_sec,
            )
            u_r_arr = np.where(np.isfinite(u_r_arr), u_r_arr, 0.0)
            v_r_arr = np.where(np.isfinite(v_r_arr), v_r_arr, 0.0)
        else:
            u_r_arr = np.zeros(N)
            v_r_arr = np.zeros(N)
            for i in range(N):
                u_r, v_r = current_at(
                    float(self.lats[i]), float(self.lons[i]), depth_m, t_sec,
                )
                u_r_arr[i] = u_r if np.isfinite(u_r) else 0.0
                v_r_arr[i] = v_r if np.isfinite(v_r) else 0.0

        if extra_u_ms is not None:
            u_r_arr = u_r_arr + np.asarray(extra_u_ms, dtype=np.float64)
        if extra_v_ms is not None:
            v_r_arr = v_r_arr + np.asarray(extra_v_ms, dtype=np.float64)

        if pert_u is not None and pert_v is not None:
            # OU-integrated mode: per-particle perturbation (same draw for
            # real and shadow → bias-Kalman shadow contract holds).
            u_r_arr = u_r_arr + pert_u
            v_r_arr = v_r_arr + pert_v
            u_s_arr = u_s_arr + pert_u
            v_s_arr = v_s_arr + pert_v
        elif process_noise_ms > 0 and rng is not None:
            # Legacy iid mode: same draw for shadow and real per particle.
            noise_u = rng.normal(0.0, process_noise_ms, size=N)
            noise_v = rng.normal(0.0, process_noise_ms, size=N)
            u_r_arr = u_r_arr + noise_u
            v_r_arr = v_r_arr + noise_v
            u_s_arr = u_s_arr + noise_u
            v_s_arr = v_s_arr + noise_v

        # Vectorized lat/lon step. Equivalent to lat_lon_step_from_velocity
        # per particle (small-angle, per-particle cos(lat)).
        cos_lat_r = np.cos(np.deg2rad(self.lats))
        cos_lat_s = np.cos(np.deg2rad(self.shadow_lats))
        self.lats = self.lats + (v_r_arr * dt_sec) / EARTH_R_M
        self.lons = self.lons + (u_r_arr * dt_sec) / (EARTH_R_M * cos_lat_r)
        self.shadow_lats = self.shadow_lats + (v_s_arr * dt_sec) / EARTH_R_M
        self.shadow_lons = self.shadow_lons + (u_s_arr * dt_sec) / (
            EARTH_R_M * cos_lat_s)
        return out_prior_u, out_prior_v

    def reweight(self, log_likelihoods: np.ndarray) -> None:
        """Multiply weights by exp(log_likelihoods), renormalize
        numerically safely."""
        log_w = np.log(np.maximum(self.weights, 1e-300)) + log_likelihoods
        log_w -= log_w.max()
        w = np.exp(log_w)
        s = w.sum()
        if s > 0:
            self.weights = w / s

    def maybe_resample(self, rng: np.random.Generator | None = None,
                        ess_ratio: float = 0.5) -> np.ndarray | None:
        """Resample if ESS falls below `ess_ratio * n`; return the gather
        index so the caller can reorder auxiliary per-particle state
        (e.g., per-particle bias field). Returns None when no resample
        happened.

        BOTH real and shadow positions are gathered by the same idx —
        each particle slot keeps its real and shadow as a paired identity.
        Per-particle bias state (b̂, P, dwell, x_start, prior_disp) MUST
        also be gathered by the same idx by the caller to maintain
        Storvik consistency.
        """
        _ = rng  # systematic_resample uses its own internal RNG
        if self.ess() >= ess_ratio * self.n:
            return None
        idx = systematic_resample(self.weights)
        self.lats = self.lats[idx].copy()
        self.lons = self.lons[idx].copy()
        self.shadow_lats = self.shadow_lats[idx].copy()
        self.shadow_lons = self.shadow_lons[idx].copy()
        # Gather OU state by the same idx — surviving particles inherit
        # their parents' per-component noise-amplitude states. Storvik
        # consistency (lineage preserved through resample).
        for name in list(self.pn_state.keys()):
            self.pn_state[name] = self.pn_state[name][idx].copy()
        self.weights = np.full(self.n, 1.0 / self.n)
        return idx

    def sample_currents_at_particles(
        self, depth_m: float, t_sec: float, current_at: CurrentAt,
        current_at_batched: "CurrentAtBatched | None" = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (u, v) predicted by `current_at` at each particle's
        (lat, lon, depth, t). Used by RelativeFlowSensor's per-particle
        log-likelihood. When `current_at_batched` is provided, issues
        one batched RGI call instead of N scalar calls."""
        if current_at_batched is not None:
            depths_arr = np.full(self.n, depth_m, dtype=np.float64)
            us, vs = current_at_batched(
                self.lats, self.lons, depths_arr, t_sec,
            )
            us = np.where(np.isfinite(us), us, 0.0)
            vs = np.where(np.isfinite(vs), vs, 0.0)
            return np.asarray(us, dtype=np.float64), np.asarray(vs, dtype=np.float64)
        us = np.zeros(self.n)
        vs = np.zeros(self.n)
        for i in range(self.n):
            u, v = current_at(self.lats[i], self.lons[i], depth_m, t_sec)
            us[i] = u if np.isfinite(u) else 0.0
            vs[i] = v if np.isfinite(v) else 0.0
        return us, vs
