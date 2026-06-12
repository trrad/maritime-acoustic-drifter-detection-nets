"""Reduced-rank bias-field state + per-particle Kalman update.

Phase 2.1 v1.1 basis: station-relative 2D grid × depth × 2 components, with
a **dense Matérn (exponential) spatial covariance per depth** as the prior.
This replaces the diagonal-prior version that produced 1.5–3× magnitude
overshoot in single-leg observations (see
`docs/reference/noise_model_boundary_review_2026-04-24.md` and the
2026-04-25 architecture review). The dense prior is what allows a leg
observation that touches a few cells via dwell to update spatially-
correlated cells through the prior covariance — the canonical fix for
bias-aware DA per Dee 2005, Lindgren-Rue-Lindström 2011 SPDE-Matérn.

State per particle:
  mean_u, mean_v          (N, D, Y, X)            m/s
  cov_u,  cov_v           (N, D, Y·X, Y·X)        block-diagonal per depth
  dwell                   (N, D, Y, X)            seconds this leg
  x_start_lat/lon         (N,)                    leg-start position
  prior_disp_east/north   (N,)                    integrated prior×dt this leg

Observation per leg (per particle, per component):
  y_obs = (tri_pos_end − x_start − prior_disp)         [analytical, b̂-independent]
  H = dwell flattened over (D, Y·X)
  innov = y_obs − Σ_d (H_d · mean_d)
  K_d = P_d H_d / S, where S = Σ_d (H_d · P_d · H_d) + σ_obs²
  mean_d ← mean_d + K_d · innov                        (per depth)
  P_d    ← P_d − outer(K_d, H_d · P_d)                 (rank-1 reduction per depth)

Vertical structure: each depth slab has its own dense covariance and
mean. Cross-depth correlation is dropped (block-diagonal in depth) — v1.1
keeps the simplicity of v1's depth indexing while fixing the spatial
prior. v3 layered-physics decomposition with shared-across-depth fields
per component is queued for after this fix lands.

OU temporal evolution (between legs): mean ← γ · mean; cov ← γ²·cov +
(1−γ²)·P_∞ where γ = exp(−Δt/τ_slow). Maintains the slow drift in b̂
across the mission and inflates posterior variance toward the prior
between observations.

Posterior-variance gate: `posterior_var_at(lat, lon, depth)` returns the
ensemble-mean diagonal posterior variance at a query point. Controllers
should fall back to the clean prior when this exceeds a threshold (i.e.
when this drifter has not yet observed enough to trust the local b̂).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np  # type: ignore[import-not-found]

from truth_field import EARTH_R_M  # type: ignore[import-not-found]


@dataclass(frozen=True)
class GridBiasBasis:
    """Station-relative 2D grid × depth basis.

    depth_centers_m aligns with the station's `available_depths_m`; the
    nearest entry is selected for each sample (no vertical interpolation).
    """

    station_lat: float
    station_lon: float
    depth_centers_m: tuple[float, ...]
    n_cells: int = 8
    cell_size_m: float = 2000.0

    @property
    def n_depths(self) -> int:
        return len(self.depth_centers_m)

    @property
    def half_extent_m(self) -> float:
        return 0.5 * self.n_cells * self.cell_size_m

    def indices(
        self, lats: np.ndarray, lons: np.ndarray, depth_m: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return `(d, y, x, inside)` arrays, each shape `lats.shape`."""
        cos_lat = float(np.cos(np.deg2rad(self.station_lat)))
        dy_m = (lats - self.station_lat) * EARTH_R_M
        dx_m = (lons - self.station_lon) * EARTH_R_M * cos_lat
        half = self.half_extent_m
        inside = (np.abs(dx_m) < half) & (np.abs(dy_m) < half)
        yi = np.clip(((dy_m + half) / self.cell_size_m).astype(int),
                      0, self.n_cells - 1)
        xi = np.clip(((dx_m + half) / self.cell_size_m).astype(int),
                      0, self.n_cells - 1)
        depth_centers = np.asarray(self.depth_centers_m)
        di = int(np.argmin(np.abs(depth_centers - depth_m)))
        di_arr = np.full(lats.shape, di, dtype=int)
        return di_arr, yi, xi, inside

    def cell_centres_relative_m(self) -> np.ndarray:
        """Return cell-centre coordinates relative to station, shape (Y·X, 2).

        Order matches `flatten` of (Y, X) arrays — row-major. Coordinate is
        (east, north) in metres.
        """
        half = self.half_extent_m
        # Cell centre offsets from station in metres
        e_offsets = np.arange(self.n_cells) * self.cell_size_m - half + 0.5 * self.cell_size_m
        n_offsets = np.arange(self.n_cells) * self.cell_size_m - half + 0.5 * self.cell_size_m
        # Meshgrid: y first (rows), x second (cols), to match (Y, X) flatten order
        nn, ee = np.meshgrid(n_offsets, e_offsets, indexing="ij")
        return np.stack([ee.ravel(), nn.ravel()], axis=-1)


def _build_matern_cov(
    cell_centres_m: np.ndarray,   # (Y·X, 2)
    sigma: float,
    l_corr_m: float,
    matern_nu: float = 0.5,
) -> np.ndarray:
    """Build a dense isotropic Matérn covariance matrix on the cell centres.

    `nu = 0.5` → exponential kernel: `P[i,j] = σ² · exp(-r/L_c)`.
    `nu = 1.5` → smoother Matérn kernel: `P[i,j] = σ² · (1 + √3 r/L) · exp(-√3 r/L)`.

    Default exponential because we want the prior's roughness to roughly
    match the underlying noise (Matérn-1/2 = OU process spatially).
    """
    diffs = cell_centres_m[:, None, :] - cell_centres_m[None, :, :]
    dist = np.sqrt(np.sum(diffs ** 2, axis=-1))
    if matern_nu == 0.5:
        return sigma ** 2 * np.exp(-dist / max(l_corr_m, 1.0))
    if matern_nu == 1.5:
        scaled = np.sqrt(3.0) * dist / max(l_corr_m, 1.0)
        return sigma ** 2 * (1.0 + scaled) * np.exp(-scaled)
    raise ValueError(f"Unsupported matern_nu={matern_nu}")


@dataclass
class BiasFieldState:
    """Per-particle bias means + dense Matérn covariance + leg accumulators."""

    mean_u: np.ndarray              # (N, D, Y, X)
    mean_v: np.ndarray
    cov_u: np.ndarray               # (N, D, Y·X, Y·X)  block-diagonal in depth
    cov_v: np.ndarray
    dwell: np.ndarray               # (N, D, Y, X)
    # Stationary prior covariance (P_∞), shared across particles + depths,
    # used by OU evolution for variance inflation.
    cov_prior: np.ndarray           # (Y·X, Y·X)
    # Per-particle leg-state accumulators for the analytical Kalman observation.
    x_start_lat: np.ndarray         # (N,)
    x_start_lon: np.ndarray         # (N,)
    prior_disp_east: np.ndarray     # (N,) integrated prior u × dt over leg
    prior_disp_north: np.ndarray    # (N,) integrated prior v × dt over leg
    # Per-particle scalar (T, S) bias offset state (Step 2.2). Absorbs
    # the basin-coherent tracer bias so the CTD likelihood compares
    # particles UP TO the bias estimate rather than against a clean
    # prior. Updated per CTD tick via a scalar Kalman; see
    # `kalman_update_tracer_offset`.
    bias_T_offset: np.ndarray       # (N,) °C
    bias_S_offset: np.ndarray       # (N,) g/kg
    P_T_offset: np.ndarray          # (N,) (°C)²
    P_S_offset: np.ndarray          # (N,) (g/kg)²

    @staticmethod
    def init(
        n: int, basis: GridBiasBasis, sigma_bias_init_ms: float,
        *, l_corr_m: float = 5000.0, matern_nu: float = 0.5,
        sigma_T_offset_init_c: float = 0.5,
        sigma_S_offset_init_psu: float = 1.0,
    ) -> "BiasFieldState":
        """Initialise per-particle bias state.

        `l_corr_m` — Matérn correlation length (default 5 km, matching the
        slow-component spatial scale in the layered noise design).
        `matern_nu` — kernel smoothness (0.5 = exponential, 1.5 = smoother).
        `sigma_T_offset_init_c`, `sigma_S_offset_init_psu` — prior 1-σ
        on the per-particle scalar (T, S) bias offset. Defaults cover the
        Soontiens-reported SoG basin range (T: 0.2-0.5 °C, S: 0.3-0.7 g/kg)
        with margin so the prior doesn't truncate the truth.
        """
        n_d = basis.n_depths
        n_y = basis.n_cells
        n_x = basis.n_cells
        n_cells_flat = n_y * n_x
        cell_centres = basis.cell_centres_relative_m()
        cov_prior = _build_matern_cov(cell_centres, sigma_bias_init_ms,
                                       l_corr_m, matern_nu)
        # Broadcast to (N, D, Y·X, Y·X), copy so each particle has its own.
        cov_full = np.broadcast_to(cov_prior, (n, n_d, n_cells_flat, n_cells_flat)).copy()
        return BiasFieldState(
            mean_u=np.zeros((n, n_d, n_y, n_x)),
            mean_v=np.zeros((n, n_d, n_y, n_x)),
            cov_u=cov_full,
            cov_v=cov_full.copy(),
            dwell=np.zeros((n, n_d, n_y, n_x)),
            cov_prior=cov_prior,
            x_start_lat=np.zeros(n),
            x_start_lon=np.zeros(n),
            prior_disp_east=np.zeros(n),
            prior_disp_north=np.zeros(n),
            bias_T_offset=np.zeros(n),
            bias_S_offset=np.zeros(n),
            P_T_offset=np.full(n, sigma_T_offset_init_c ** 2),
            P_S_offset=np.full(n, sigma_S_offset_init_psu ** 2),
        )

    @property
    def n(self) -> int:
        return int(self.mean_u.shape[0])

    @property
    def n_depths(self) -> int:
        return int(self.mean_u.shape[1])

    @property
    def n_cells_flat(self) -> int:
        return int(self.mean_u.shape[2] * self.mean_u.shape[3])

    def reset_dwell(self) -> None:
        self.dwell.fill(0.0)

    def reset_leg_accumulators(self, x_start_lat: np.ndarray,
                                 x_start_lon: np.ndarray) -> None:
        """Snapshot leg-start positions, zero dwell + prior_disp.

        Called when a submerged leg starts (just after surface-dwell exit
        in the experiment loop). All per-particle.
        """
        self.x_start_lat = x_start_lat.copy()
        self.x_start_lon = x_start_lon.copy()
        self.prior_disp_east.fill(0.0)
        self.prior_disp_north.fill(0.0)
        self.dwell.fill(0.0)

    def accumulate_prior_disp(self, prior_u: np.ndarray, prior_v: np.ndarray,
                                dt_sec: float) -> None:
        """Increment per-particle prior_disp by (prior_velocity × dt).

        `prior_u`, `prior_v` are per-particle prior-velocity samples at
        the SHADOW (b̂-independent) trajectory's current position. Called
        every submerged tick.
        """
        self.prior_disp_east += prior_u * dt_sec
        self.prior_disp_north += prior_v * dt_sec

    def gather(self, idx: np.ndarray) -> None:
        """Reorder per-particle state by index (for resample). Per Storvik
        2002, this preserves bias-state lineages through resample without
        re-applying observations."""
        self.mean_u = self.mean_u[idx].copy()
        self.mean_v = self.mean_v[idx].copy()
        self.cov_u = self.cov_u[idx].copy()
        self.cov_v = self.cov_v[idx].copy()
        self.dwell = self.dwell[idx].copy()
        self.x_start_lat = self.x_start_lat[idx].copy()
        self.x_start_lon = self.x_start_lon[idx].copy()
        self.prior_disp_east = self.prior_disp_east[idx].copy()
        self.prior_disp_north = self.prior_disp_north[idx].copy()
        self.bias_T_offset = self.bias_T_offset[idx].copy()
        self.bias_S_offset = self.bias_S_offset[idx].copy()
        self.P_T_offset = self.P_T_offset[idx].copy()
        self.P_S_offset = self.P_S_offset[idx].copy()

    def lookup_and_accumulate(
        self, basis: GridBiasBasis,
        lats: np.ndarray, lons: np.ndarray, depth_m: float,
        dt_sec: float, *, accumulate: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (u, v) bias correction per particle at the current depth
        and position. If `accumulate` is True, also increment dwell for
        particles inside the patch.

        IMPORTANT: `lats`/`lons` here should be the SHADOW trajectory's
        positions (the b̂-independent dead-reckoning). Using the real PF
        position would re-introduce the H ↔ b̂ dependency that breaks the
        RBPF correctness argument (Schön/Gustafsson/Nordlund 2005).

        Particles outside the patch receive zero bias and contribute no
        dwell. `accumulate=False` is used for surface-dwell ticks: the
        bias estimate is still returned (so the real PF advects with
        prior + b̂), but dwell is not incremented.
        """
        d, y, x, inside = basis.indices(lats, lons, depth_m)
        pi = np.arange(lats.size)
        u = np.where(inside, self.mean_u[pi, d, y, x], 0.0)
        v = np.where(inside, self.mean_v[pi, d, y, x], 0.0)
        if accumulate and inside.any():
            pi_in = pi[inside]
            d_in = d[inside]
            y_in = y[inside]
            x_in = x[inside]
            self.dwell[pi_in, d_in, y_in, x_in] += dt_sec
        return u, v

    def kalman_update_leg(
        self,
        innovation_east_m: np.ndarray,   # (N,) y_obs_east per particle
        innovation_north_m: np.ndarray,  # (N,) y_obs_north per particle
        sigma_obs_m: float | np.ndarray,
    ) -> None:
        """Per-particle dense-covariance Kalman update at leg end.

        Observation per particle, per component:
            y_obs[i] = tri_pos_end[i] − x_start[i] − prior_disp[i]
            H[i]    = dwell[i, :, :, :] flattened over (D, Y·X)
            innov[i] = y_obs[i] − Σ_d (H_d[i] · mean_d[i])
            S[i]   = Σ_d (H_d[i] · cov_d[i] · H_d[i]) + σ_obs²
            K_d[i] = cov_d[i] · H_d[i] / S[i]
            mean_d[i] += K_d[i] · innov[i]
            cov_d[i]  −= outer(K_d[i], (H_d[i] · cov_d[i]))

        With the Matérn prior, K_d[i] is non-zero across all spatially-
        correlated cells (not just the cells with non-zero dwell), so the
        leg residual gets distributed across the patch rather than
        concentrating in the few cells the particle visited.

        Caller passes the analytical observation (y_obs, NOT raw innovation
        from `tri − pf.lats`) — this decouples the bias-Kalman from the
        position PF's CTD reweighting.
        """
        n_p = self.n
        n_d = self.n_depths
        n_flat = self.n_cells_flat

        # Reshape dwell to (N, D, Y·X) for vector form.
        H = self.dwell.reshape(n_p, n_d, n_flat)
        m_u = self.mean_u.reshape(n_p, n_d, n_flat)
        m_v = self.mean_v.reshape(n_p, n_d, n_flat)

        # Per-depth P @ H^T: (N, D, Y·X)
        PH_u = np.einsum('ndij,ndj->ndi', self.cov_u, H)
        PH_v = np.einsum('ndij,ndj->ndi', self.cov_v, H)

        # Per-depth H · P · H = scalar per (N, D)
        HPH_u = np.einsum('ndi,ndi->nd', H, PH_u)
        HPH_v = np.einsum('ndi,ndi->nd', H, PH_v)

        # Total H P H^T per particle (block-diagonal P) + obs noise.
        S_u = np.sum(HPH_u, axis=1) + sigma_obs_m ** 2  # (N,)
        S_v = np.sum(HPH_v, axis=1) + sigma_obs_m ** 2

        # Predicted observation H · b̂ summed over depths: (N,)
        Hb_u = np.einsum('ndi,ndi->n', H, m_u)
        Hb_v = np.einsum('ndi,ndi->n', H, m_v)

        # Innovation: difference between y_obs and prediction.
        r_u = innovation_east_m - Hb_u
        r_v = innovation_north_m - Hb_v

        # Kalman gain per depth: K_d = P_d H_d / S, shape (N, D, Y·X)
        K_u = PH_u / np.maximum(S_u, 1e-12)[:, None, None]
        K_v = PH_v / np.maximum(S_v, 1e-12)[:, None, None]

        # Mean update: mean_d += K_d · scalar_r
        m_u += K_u * r_u[:, None, None]
        m_v += K_v * r_v[:, None, None]
        self.mean_u = m_u.reshape(self.mean_u.shape)
        self.mean_v = m_v.reshape(self.mean_v.shape)

        # Cov update: cov_d -= outer(K_d, PH_d)  (rank-1 per depth per particle)
        self.cov_u -= np.einsum('ndi,ndj->ndij', K_u, PH_u)
        self.cov_v -= np.einsum('ndi,ndj->ndij', K_v, PH_v)

    def kalman_update_tracer_offset(
        self,
        z_T: float, z_S: float,
        T_pp: np.ndarray, S_pp: np.ndarray,
        sigma_T_obs_per_part: np.ndarray,
        sigma_S_obs_per_part: np.ndarray,
    ) -> None:
        """Per-particle scalar Kalman update on (T, S) bias offset.

        Model: y = T_pp(particle) + bias_T_offset + ε_T,
               ε_T ~ N(0, σ_T_obs²) per particle.
        Innovation per particle is `z - T_pp - bias_T_offset_prev`.

        `sigma_*_obs_per_part` is the per-particle effective observation
        noise — instrument σ + spatial fluctuation that's not in the bias
        state, depth-attenuated. The same σ is used for the PF likelihood
        upstream of this call.
        """
        # T channel
        r_T = z_T - T_pp - self.bias_T_offset
        S_T_total = self.P_T_offset + sigma_T_obs_per_part ** 2
        K_T = self.P_T_offset / np.maximum(S_T_total, 1e-12)
        self.bias_T_offset = self.bias_T_offset + K_T * r_T
        self.P_T_offset = (1.0 - K_T) * self.P_T_offset
        # S channel
        r_S = z_S - S_pp - self.bias_S_offset
        S_S_total = self.P_S_offset + sigma_S_obs_per_part ** 2
        K_S = self.P_S_offset / np.maximum(S_S_total, 1e-12)
        self.bias_S_offset = self.bias_S_offset + K_S * r_S
        self.P_S_offset = (1.0 - K_S) * self.P_S_offset

    def ou_evolve_tracer_offset(
        self, dt_sec: float, tau_sec: float,
        sigma_T_inf_sq: float, sigma_S_inf_sq: float,
    ) -> None:
        """OU temporal evolution of the scalar (T, S) bias offset state
        between observations. Mirrors `ou_evolve` (the spatial-Matérn
        version) but for the per-particle scalar offsets.

        For OU process db = −(1/τ) b dt + dW with stationary cov σ²_∞:
            mean ← γ · mean
            cov  ← γ²·cov + (1−γ²)·σ²_∞
        γ = exp(−dt / τ)

        Without this, `kalman_update_tracer_offset` shrinks `P_T_offset`
        / `P_S_offset` monotonically; after dozens of CTD updates the
        likelihood σ floor collapses to instrument+fluctuation alone,
        making the filter believe its bias estimate is more accurate
        than the underlying physics actually allow. With inflation,
        between observations the variance regrows toward the prior
        steady-state σ²_∞, keeping the CTD likelihood honest.
        """
        if dt_sec <= 0 or tau_sec <= 0:
            return
        gamma = float(np.exp(-dt_sec / tau_sec))
        gamma_sq = gamma ** 2
        self.bias_T_offset *= gamma
        self.bias_S_offset *= gamma
        self.P_T_offset = gamma_sq * self.P_T_offset + (1.0 - gamma_sq) * sigma_T_inf_sq
        self.P_S_offset = gamma_sq * self.P_S_offset + (1.0 - gamma_sq) * sigma_S_inf_sq

    def ou_evolve(self, dt_sec: float, tau_sec: float) -> None:
        """OU temporal evolution of b̂ between observations.

        For an OU process db = −(1/τ) b dt + dW with stationary cov P_∞:
          mean ← γ · mean
          cov  ← γ²·cov + (1−γ²)·P_∞
        where γ = exp(−dt / τ).

        Per Lindgren-Rue-Lindström 2011 §3.5 for Matérn space-time.
        Applied per-particle, per-depth, with the shared P_∞ from init.
        """
        if dt_sec <= 0 or tau_sec <= 0:
            return
        gamma = float(np.exp(-dt_sec / tau_sec))
        gamma_sq = gamma ** 2
        self.mean_u *= gamma
        self.mean_v *= gamma
        # cov ← γ²·cov + (1−γ²)·P_∞ broadcast over (N, D)
        self.cov_u = gamma_sq * self.cov_u + (1.0 - gamma_sq) * self.cov_prior
        self.cov_v = gamma_sq * self.cov_v + (1.0 - gamma_sq) * self.cov_prior

    def posterior_var_at(
        self, basis: GridBiasBasis, lat: float, lon: float, depth_m: float,
    ) -> float:
        """Ensemble-mean posterior variance at a query point.

        Used by the controller's posterior-variance gate to detect cells
        the drifter has not adequately observed. The returned value is
        averaged over particles AND over (u, v) components — a scalar
        proxy for "how confident is this drifter's bias estimate here?"
        """
        d, y, x, inside = basis.indices(
            np.asarray([lat]), np.asarray([lon]), depth_m,
        )
        if not bool(inside[0]):
            # Outside patch — return prior variance (the diagonal element
            # of cov_prior, which is σ_init²).
            return float(self.cov_prior[0, 0])
        di = int(d[0])
        yi = int(y[0])
        xi = int(x[0])
        flat_i = yi * basis.n_cells + xi
        # Per-particle diagonal var at (di, flat_i): cov[i, di, flat_i, flat_i]
        var_u_per_particle = self.cov_u[:, di, flat_i, flat_i]
        var_v_per_particle = self.cov_v[:, di, flat_i, flat_i]
        return float(0.5 * (var_u_per_particle.mean() + var_v_per_particle.mean()))

    def learned_summary(self, var_init_ms2: float,
                         learn_thresh: float = 0.5) -> dict[str, float]:
        """Summary stats over visited cells.

        A cell is 'learned' if its posterior variance has dropped below
        `learn_thresh × var_init`. Variance is read from the diagonal of
        the dense per-depth covariance.
        """
        thresh = var_init_ms2 * learn_thresh
        # Extract diagonal variances from cov_u, cov_v: shape (N, D, Y·X)
        diag_u = np.diagonal(self.cov_u, axis1=2, axis2=3)
        diag_v = np.diagonal(self.cov_v, axis1=2, axis2=3)
        learned_u = diag_u < thresh
        learned_v = diag_v < thresh
        any_learned = learned_u | learned_v
        n_slots = 2 * diag_u.size
        n_learned = int(learned_u.sum() + learned_v.sum())
        frac = n_learned / n_slots if n_slots else 0.0
        # Magnitude over flattened (Y·X) cells: |b̂| = √(u² + v²)
        m_u_flat = self.mean_u.reshape(self.n, self.n_depths, -1)
        m_v_flat = self.mean_v.reshape(self.n, self.n_depths, -1)
        mag = np.sqrt(m_u_flat ** 2 + m_v_flat ** 2)
        if any_learned.any():
            mean_mag = float(mag[any_learned].mean())
            max_mag = float(mag[any_learned].max())
            learned_vars = np.concatenate([
                diag_u[learned_u].ravel(),
                diag_v[learned_v].ravel(),
            ])
            mean_var = float(learned_vars.mean()) if learned_vars.size else 0.0
        else:
            mean_mag = 0.0
            max_mag = 0.0
            mean_var = 0.0
        return {
            "learned_fraction": frac,
            "mean_learned_mag_ms": mean_mag,
            "max_learned_mag_ms": max_mag,
            "mean_learned_var_ms2": mean_var,
        }
