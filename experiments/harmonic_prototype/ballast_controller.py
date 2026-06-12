"""Greedy-myopic ballast depth controller for station-keeping.

Takes a `KnowledgeSource` (truth in Phase A; progressively degraded
variants in Phase B). At each control decision:

  1. Read current estimate at the node's current (lat, lon) at each
     available depth, at the decision time.
  2. Forward-project position over the lookahead window if that depth
     were held.
  3. Pick the depth whose projected position minimises distance to
     station.

Greedy-myopic: no MPC, no rollout with dynamics, no trajectory planning.
Good enough for prototype — the question is whether ANY depth-switching
scheme can station-keep under various knowledge qualities. If greedy
works even moderately well, more sophisticated controllers can do at
least that well.

The lookahead uses the estimated current at the decision instant (not
integrated through time-varying current over the window). This is
consistent with "greedy-myopic" — the controller assumes the current at
each depth is what it sees right now, and picks the depth best aligned
back toward the station.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np  # type: ignore[import-not-found]

from process_noise import (  # type: ignore[import-not-found]
    ProcessNoiseConfig,
    sigma_pos_growth_rate_per_axis_vec,
)
from truth_field import distance_m, lat_lon_step_from_velocity  # type: ignore[import-not-found]


def _jax_mpc_enabled() -> bool:
    """The MPC's per-substep rollout runs through the JAX/XLA kernel in
    `mpc_rollout_jax.rollout_interval` by default — that's the path the
    GPU acceleration was built for and the path GPU-memory tuning
    (N_PROCS=8 ceiling, XLA platform allocator) was sized against. Set
    `FLEET_USE_JAX_MPC=0` to fall back to the numpy reference rollout
    (parity-tested in `_test_mpc_rollout_jax_parity.py`); useful for
    environments without jax/CUDA installed or for debugging."""
    return os.environ.get("FLEET_USE_JAX_MPC", "1") == "1"


class KnowledgeSource(Protocol):
    """Anything that can answer 'what's the current at (lat, lon, depth, t)?'."""

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        """Return (u_ms, v_ms). Out-of-domain is expected to return (nan, nan)."""
        ...


@dataclass
class StationKeeper:
    """Greedy depth-picker for station-keeping.

    station_lat, station_lon: target point.
    available_depths_m: depths the ballast can select.
    lookahead_sec: how far ahead to project when scoring each depth.
    knowledge: estimation backend (truth, smoothed, prior, PF...).
    thrust_v_max_ms: if > 0, the controller also chooses a thrust vector
      (analytic optimum) of magnitude ≤ V_max to add to the ambient
      current. 0 disables active control → pure ballast-only (Phase A
      behaviour). Models an active fin or glider-class thruster.
    """

    station_lat: float
    station_lon: float
    available_depths_m: list[float]
    lookahead_sec: float
    knowledge: KnowledgeSource
    thrust_v_max_ms: float = 0.0

    def choose_depth(
        self,
        lat: float, lon: float, t_sec: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
    ) -> tuple[float, dict[float, float]]:
        """Backward-compatible wrapper: returns just the best depth + scores,
        discarding the thrust vector even when V_max > 0. Callers that need
        the thrust should use `choose_action` instead."""
        best_d, _, scores = self.choose_action(
            lat, lon, t_sec, perceived_lat, perceived_lon,
        )
        return best_d, scores

    def choose_action(
        self,
        lat: float, lon: float, t_sec: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
    ) -> tuple[float, tuple[float, float], dict[float, float]]:
        """Pick (depth, thrust_uv) minimising projected |pos − station|.

        For each depth, the optimal thrust is the vector that would carry
        the node exactly to the station over the lookahead — clipped to
        the V_max sphere. Returns (best_depth, best_thrust_uv, scores).
        scores are the best achievable distance at each depth (post-thrust).
        """
        px = lat if perceived_lat is None else perceived_lat
        py = lon if perceived_lon is None else perceived_lon

        # Desired velocity that would land us exactly at the station over
        # the lookahead window. Expressed in m/s east / m/s north.
        from truth_field import EARTH_R_M  # type: ignore[import-not-found]
        cos_lat = np.cos(np.deg2rad(px))
        dlat_target_m = (self.station_lat - px) * EARTH_R_M
        dlon_target_m = (self.station_lon - py) * EARTH_R_M * cos_lat
        u_target = dlon_target_m / self.lookahead_sec
        v_target = dlat_target_m / self.lookahead_sec

        scores: dict[float, float] = {}
        thrusts: dict[float, tuple[float, float]] = {}
        for d in self.available_depths_m:
            u_c, v_c = self.knowledge.get_current_at(px, py, d, t_sec)
            if not (np.isfinite(u_c) and np.isfinite(v_c)):
                scores[d] = float("nan")
                thrusts[d] = (0.0, 0.0)
                continue
            # Required thrust to exactly cancel "drift away from station".
            ut_req = u_target - u_c
            vt_req = v_target - v_c
            mag = float(np.hypot(ut_req, vt_req))
            if mag <= self.thrust_v_max_ms or self.thrust_v_max_ms <= 0.0:
                # Cap at V_max. When V_max=0, thrust=(0,0) and behaviour
                # reduces to the original depth-only controller.
                if self.thrust_v_max_ms <= 0.0:
                    thrust = (0.0, 0.0)
                else:
                    thrust = (ut_req, vt_req)
            else:
                scale = self.thrust_v_max_ms / mag
                thrust = (ut_req * scale, vt_req * scale)
            # Projected position under (current + thrust).
            u_net = u_c + thrust[0]
            v_net = v_c + thrust[1]
            dlat, dlon = lat_lon_step_from_velocity(u_net, v_net, px,
                                                     self.lookahead_sec)
            proj_lat = px + dlat
            proj_lon = py + dlon
            scores[d] = distance_m(proj_lat, proj_lon,
                                    self.station_lat, self.station_lon)
            thrusts[d] = thrust

        valid = {d: s for d, s in scores.items() if np.isfinite(s)}
        if not valid:
            return (self.available_depths_m[0], (0.0, 0.0), scores)
        best_d = min(valid, key=lambda d: valid[d])
        return (best_d, thrusts[best_d], scores)


# ---------------------------------------------------------------------------
# Trajectory-integrating station keeper (Step 3a, 2026-04-26).
#
# Replaces the single-point Euler scoring of `StationKeeper.choose_action`
# with a forward-rollout of the actual `ballast_dynamics.step()` function.
# The predictor IS the dynamics — same code path, no separate physics
# model. Under perfect knowledge of the field, the predictor's "best
# depth" is by construction optimal for the executed trajectory (modulo
# the fact that the controller commits for a single decision interval
# and re-decides; that's the next architectural layer, real MPC).
#
# Earlier integrator versions used a separate substep model that
# diverged from the dynamics; with perfect info it produced WORSE
# trajectories than single-point Euler at 7/8 sites in the
# site-authority diagnostic (2026-04-26). The fix was to (a) make
# `step()` itself physically faithful via internal substepping, and
# (b) have the predictor literally call `step()` instead of
# reimplementing.
#
# Posterior-aware scoring (consume `(b̂_mean, P)` via CVaR or chance-
# constrained) layers on top once real MPC is in place.
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryStationKeeper:
    """Forward-rollout depth-picker for station-keeping.

    Scores each candidate depth by:
      1. Holding that depth as the setpoint for the lookahead window.
      2. Forward-rolling the dynamics via `ballast_dynamics.step()` in
         `dt_sec`-sized chunks (default 600 s, matching the mission
         tick), accumulating distance to station each step.
      3. Score = mean distance over the rollout.

    Passive ballast only (no thrust). When a thrust-bearing body lands,
    scoring requires solving an MPC over the trajectory; raise rather
    than silently degrade.

    `dt_sec` default 600 s matches the typical mission tick. The
    `step()` function internally sub-resolves each call for physical
    faithfulness; no separate substep parameter on the keeper.
    """

    station_lat: float
    station_lon: float
    available_depths_m: list[float]
    lookahead_sec: float
    knowledge: KnowledgeSource
    w_z_max_ms: float = 0.1
    dt_sec: float = 600.0

    def choose_depth(
        self,
        lat: float, lon: float, t_sec: float,
        current_depth_m: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
    ) -> tuple[float, dict[float, float]]:
        """Pick depth minimising mean-distance over the forward rollout.

        `current_depth_m` is REQUIRED — initial state of the rollout.
        No default; if you don't know the current depth you can't roll
        forward correctly.

        Returns `(best_depth, scores)` where scores[d] is the rollout's
        mean distance to station for candidate depth d.
        """
        px = lat if perceived_lat is None else perceived_lat
        py = lon if perceived_lon is None else perceived_lon
        if not np.isfinite(current_depth_m):
            raise ValueError(
                f"TrajectoryStationKeeper.choose_depth requires finite "
                f"current_depth_m, got {current_depth_m}"
            )

        scores: dict[float, float] = {}
        for d in self.available_depths_m:
            scores[d] = self._score_rollout(
                px, py, t_sec, current_depth_m, d,
            )
        valid = {d: s for d, s in scores.items() if np.isfinite(s)}
        if not valid:
            return self.available_depths_m[0], scores
        best_d = min(valid, key=lambda d: valid[d])
        return best_d, scores

    def _score_rollout(
        self,
        px: float, py: float, t_sec: float,
        current_depth_m: float,
        target_depth_m: float,
    ) -> float:
        """Roll forward dynamics under candidate depth setpoint; return
        mean distance to station over the rollout.

        Predictor IS dynamics — calls `ballast_dynamics.step()` directly.
        This guarantees that under perfect knowledge of the field, the
        predicted trajectory equals the executed trajectory (over the
        next dt_sec, i.e. one step; over the lookahead this is the
        trajectory that WOULD execute if the controller held the depth
        for the full lookahead).
        """
        # Local import to avoid circular dependency (ballast_dynamics
        # imports from truth_field, controller imports from
        # ballast_dynamics for the rollout).
        from ballast_dynamics import (  # type: ignore[import-not-found]
            BallastState, step,
        )

        n_steps = int(self.lookahead_sec / self.dt_sec)
        if n_steps < 1:
            n_steps = 1
        cur = BallastState(
            lat=px, lon=py,
            depth_m=current_depth_m,
            depth_setpoint_m=target_depth_m,
        )
        # `current_at` shape mismatch between the controller's
        # KnowledgeSource (lat, lon, depth, t) and the dynamics'
        # HorizontalCurrentAt (t, lat, lon, depth). Wrap.
        knowledge = self.knowledge

        def cur_for_dynamics(t, lat, lon, depth_m):
            return knowledge.get_current_at(lat, lon, depth_m, t)

        dist_accum = 0.0
        n_dist = 0
        t = t_sec
        for _ in range(n_steps):
            cur = step(cur, t, self.dt_sec, cur_for_dynamics,
                        w_z_max_ms=self.w_z_max_ms)
            t += self.dt_sec
            d_to_station = distance_m(
                cur.lat, cur.lon, self.station_lat, self.station_lon,
            )
            if not np.isfinite(d_to_station):
                return float("nan")
            dist_accum += d_to_station
            n_dist += 1
        return dist_accum / max(n_dist, 1)


# ---------------------------------------------------------------------------
# Receding-horizon MPC station keeper (Step 3b, 2026-04-26).
#
# Plans a SEQUENCE of depth setpoints over a multi-interval horizon,
# scores via forward-rollout mean distance, returns the first setpoint,
# replans on the next call. Classic receding-horizon MPC (Mayne,
# Rawlings, Diehl 2017 §2.4).
#
# Search: vectorized BEAM SEARCH over partial setpoint sequences. At
# each horizon step, expand each surviving partial sequence by all K
# depth options (B → B*K candidates), roll forward one decision
# interval via `ballast_dynamics.step()`, score by accumulated mean
# distance, keep the top `beam_width` candidates. Beam search is
# suboptimal in general (can prune the global optimum) but at
# beam_width=200 captures essentially all of brute-force at h=6 and
# makes h=8 / h=12 tractable.
#
# When `beam_width >= K^horizon_n`, no pruning ever occurs and the
# search is exact (brute force as the special case of beam search).
#
# Predictor IS dynamics: rollouts use the same physics model
# (`step()`'s substep cadence), so under perfect knowledge the
# predicted trajectory equals the executed trajectory. Requires the
# knowledge source to implement `get_current_at_batched` for vectorized
# RGI calls.
# ---------------------------------------------------------------------------

@dataclass
class MPCStationKeeper:
    """Receding-horizon MPC depth-picker via vectorized beam search.

    Plans `horizon_n` decision intervals ahead. Each interval, holds
    one depth setpoint for `decision_interval_sec`. Scores by mean
    distance to station over the full rollout. At each horizon step,
    keeps the top `beam_width` partial sequences by score; expands
    each by `len(available_depths_m)` next-depth options; rolls forward
    one interval; prunes back to `beam_width`.

    `beam_width >= K^horizon_n` (K = number of depth options) yields
    exact brute-force search (no pruning).

    Knowledge source must implement `get_current_at_batched(lats, lons,
    depths, t_sec) -> (u_arr, v_arr)`. Per-substep RGI overhead is
    O(K · beam_width) per knowledge call, amortised across all
    surviving partial sequences in one numpy operation.
    """

    station_lat: float
    station_lon: float
    available_depths_m: list[float]
    horizon_n: int
    decision_interval_sec: float
    knowledge: KnowledgeSource
    beam_width: int
    w_z_max_ms: float = 0.1
    dt_sec: float = 600.0
    # Process-noise model used to grow σ_pos² along the rollout. None
    # disables σ_pos tracking (β must be 0 in that case; objective
    # reduces to mean-distance² scoring). Defaults match the simulator's
    # LayeredNoiseField via `process_noise.ProcessNoiseConfig` defaults.
    process_noise_cfg: ProcessNoiseConfig | None = None
    # LoRa fix uncertainty (m). At surface ticks the σ_pos² is updated
    # by a Kalman fusion with this measurement uncertainty.
    sigma_lora_m: float = 20.0
    # Surface threshold (m). Depths at or below this are treated as
    # surface — σ_pos² shrinks via LoRa Kalman update.
    surface_threshold_m: float = 1.0
    # MPC scoring weights: per-tick score = α·d² + β·σ_pos² + λ·CVaR(d²)
    # + γ·CVaR(σ_pos²). β/λ/γ = 0 by default reduces to mean-distance²
    # over a single (ensemble-mean) trajectory.
    objective_alpha: float = 1.0
    objective_beta: float = 0.0
    objective_lambda: float = 0.0   # CVaR(d²) weight
    objective_gamma: float = 0.0    # CVaR(σ_pos²) weight
    # Posterior-CVaR scoring mode. When True, the rollout per beam is
    # repeated under N posterior draws and per-beam scores aggregate
    # `mean(d²)` and `CVaR(d²)` across draws. Requires the knowledge
    # source to implement `precompute_posterior_draws(n_draws, rng)` and
    # `get_current_at_batched_draw(lats, lons, depths, t_sec, draw_idx)`.
    posterior_cvar_enabled: bool = False
    n_posterior_draws: int = 5
    cvar_alpha: float = 0.10        # tail fraction
    posterior_rng_seed: int = 0
    # Diagnostic: mean σ_pos at the chosen plan's final horizon, set on
    # each `choose_depth` call. Read by run_one_station for the
    # calibration diagnostic. NaN until first call.
    last_predicted_sigma_pos_horizon_m: float = field(default=float("nan"),
                                                       init=False, repr=False)
    # JAX-path lazy state. Built on first call to `_rollout_interval_jax`
    # (FLEET_USE_JAX_MPC=1) and reused thereafter. Stored as `object`
    # to avoid importing jax/mpc_rollout_jax at module load time.
    _jax_bundle: object | None = field(default=None, init=False, repr=False)
    _jax_interval_fn: object | None = field(default=None, init=False,
                                             repr=False)
    # Surfacing is owned by an external SurfacingPolicy — MPC does not
    # plan surface as an action. Depths ≤ `surface_threshold_m` are
    # filtered out of the depth ladder at construction (see
    # `_post_init__` below): the controller picks ONLY among submerged
    # depths. This is the architectural separation:
    #   surfacing  ↔ when (deployment-tunable SurfacingPolicy)
    #   MPC depth  ↔ what depth between surfaces (this keeper)
    # MPC's σ_pos rollout still shrinks σ via LoRa Kalman if the caller
    # provides `next_surface_time_sec`, so σ predictions track reality
    # — but MPC has no authority over WHEN that surface happens.

    def __post_init__(self) -> None:
        # The depth ladder MPC actually uses is submerged-only; surface
        # depths are owned by SurfacingPolicy. Cache it here so the
        # public `available_depths_m` field retains exactly what the
        # caller passed in (no silent mutation).
        submerged = [
            d for d in self.available_depths_m
            if d > self.surface_threshold_m
        ]
        if not submerged:
            raise ValueError(
                "MPCStationKeeper.available_depths_m has no submerged "
                "options after filtering at surface_threshold_m="
                f"{self.surface_threshold_m}: "
                f"original={list(self.available_depths_m)}"
            )
        # `submerged_depths_m` is the internal-use list. Stored via
        # object.__setattr__ since dataclass fields can't add new ones
        # post-init without `field(init=False)` declaration.
        object.__setattr__(self, "_submerged_depths_m", submerged)

    @property
    def submerged_depths_m(self) -> list[float]:
        """Depths MPC actually plans over (surface filtered)."""
        return self._submerged_depths_m  # type: ignore[no-any-return]

    def choose_depth(
        self,
        lat: float, lon: float, t_sec: float,
        current_depth_m: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
        sigma_pos_init_m: float = 0.0,
        t_since_last_anchor_sec: float = 0.0,
        next_surface_time_sec: float | None = None,
        surface_hazard_rate_per_sec: float | None = None,
    ) -> tuple[float, dict[float, float]]:
        """Plan via beam search, execute first interval.

        Returns `(first_setpoint, scores)` where `scores` maps each
        first-depth in `available_depths_m` to the BEST surviving
        score whose sequence started with that depth (∞ if pruned).

        `sigma_pos_init_m` seeds the σ_pos² rollout state with the PF's
        current posterior σ_pos. `t_since_last_anchor_sec` is the time
        elapsed since the most recent position observation (LoRa fix);
        the OU growth rate uses this as the "elapsed time" reference
        so per-substep increments are correctly ballistic vs diffusive
        depending on time-since-anchor relative to component τ.

        `next_surface_time_sec` is the SurfacingPolicy's predicted next
        surface event (absolute t_sec). MPC's σ rollout applies a LoRa
        Kalman update and resets the per-beam time-since-anchor at
        that tick. None disables that single-event mechanism.

        `surface_hazard_rate_per_sec` is an EMPIRICAL surface rate λ
        (events / sec). When provided, σ² evolves with an extra sink
        term `−λ·(σ² − σ_lora²)` per substep — the expected continuous
        dilution from a Poisson-rate surface schedule. Necessary for
        unpredictable policies (uncertainty-gated, event-triggered)
        where `next_surface_time_sec` alone (the conservative max-gap
        deadline) over-predicts σ growth by 5-10×. None disables.
        """
        from truth_field import EARTH_R_M  # type: ignore[import-not-found]

        px = lat if perceived_lat is None else perceived_lat
        py = lon if perceived_lon is None else perceived_lon
        if not np.isfinite(current_depth_m):
            raise ValueError(
                f"MPCStationKeeper.choose_depth requires finite "
                f"current_depth_m, got {current_depth_m}"
            )
        if not hasattr(self.knowledge, "get_current_at_batched"):
            raise NotImplementedError(
                "MPCStationKeeper requires knowledge source with "
                "get_current_at_batched(lats, lons, depths, t_sec); "
                "use TrajectoryStationKeeper for scalar-only knowledge."
            )
        # Knowledge sources that maintain per-cell ensemble stats
        # (LiveBiasKnowledge) can pre-compute them once at the top of
        # the planning call instead of paying the per-particle reduction
        # on every rollout query. PerfectKnowledge has no such stats —
        # the hasattr check makes this opt-in.
        if hasattr(self.knowledge, "precompute_for_decision"):
            self.knowledge.precompute_for_decision()  # type: ignore[attr-defined]

        depth_options_full = np.asarray(self.submerged_depths_m,
                                          dtype=np.float64)
        n_dt_per_interval = max(int(self.decision_interval_sec
                                      / self.dt_sec), 1)
        n_dyn_substeps = 10
        sub_dt = self.dt_sec / n_dyn_substeps
        dz_per_substep = self.w_z_max_ms * sub_dt
        cos_station = float(np.cos(np.deg2rad(self.station_lat)))

        # Posterior-CVaR setup. `n_eval` rollouts per candidate against
        # `n_eval` independent bias-field draws; certainty-equivalent
        # mode collapses to n_eval=1 with the ensemble-mean knowledge
        # path (no draws). When CVaR is on, the knowledge source must
        # implement precompute_posterior_draws + get_current_at_batched_draw.
        if self.posterior_cvar_enabled:
            if not hasattr(self.knowledge, "precompute_posterior_draws"):
                raise NotImplementedError(
                    "posterior_cvar_enabled requires knowledge source with "
                    "precompute_posterior_draws + get_current_at_batched_draw"
                )
            cvar_rng = np.random.default_rng(self.posterior_rng_seed)
            self.knowledge.precompute_posterior_draws(  # type: ignore[attr-defined]
                self.n_posterior_draws, cvar_rng,
            )
            n_eval = int(self.n_posterior_draws)
        else:
            n_eval = 1

        # JAX-path setup. Cache the flag once per plan so the per-
        # interval branch doesn't re-read the env var × 12 times.
        # `_jax_prepare_plan` builds the device-resident bundle on
        # first call and device_puts the bias draws once per plan
        # (stable input across all intervals).
        use_jax = (_jax_mpc_enabled()
                   and self.process_noise_cfg is not None)
        if use_jax:
            draws_u_j, draws_v_j = self._jax_prepare_plan()
        else:
            draws_u_j = draws_v_j = None

        # Initial beam: 1 candidate at the start state. Per-eval (draw)
        # rollout state is replicated across the n_eval axis.
        lats_b = np.full((1, n_eval), px, dtype=np.float64)
        lons_b = np.full((1, n_eval), py, dtype=np.float64)
        depths_b = np.full((1, n_eval), current_depth_m, dtype=np.float64)
        sigma_pos_sq_b = np.full(
            (1, n_eval), max(sigma_pos_init_m, 0.0) ** 2, dtype=np.float64,
        )
        # Per-tick d² accumulator per (beam, eval).
        d_sq_sum_b = np.zeros((1, n_eval), dtype=np.float64)
        first_value_b = np.array([np.nan], dtype=np.float64)
        alive_b = np.ones((1, n_eval), dtype=bool)
        n_samples = 0
        t = t_sec
        sigma_lora_sq = float(self.sigma_lora_m) ** 2
        # Per-beam time-since-anchor, in sec. Initialised to the caller-
        # supplied value (time since the LAST real LoRa fix at decision
        # time). Reset to 0 inside the rollout when the planned surface
        # event fires — see `applied_lora_b` below.
        t_since_anchor_b = np.full(
            (1,), float(t_since_last_anchor_sec), dtype=np.float64,
        )
        # Track whether the planned surface event has been applied for
        # each beam. Once applied, σ² has been collapsed to LoRa σ² and
        # t_since_anchor reset; subsequent substeps grow from t=0 again.
        applied_lora_b = np.zeros((1,), dtype=bool)
        # Resolve the next surface event. Caller passes absolute t_sec;
        # we convert to a "trip" — sub-step counter at which the Kalman
        # fires. If None, no surface event modeled (legacy ballistic
        # growth path).
        next_surface_t = (float(next_surface_time_sec)
                           if next_surface_time_sec is not None
                           else float("inf"))

        # Surfacing is owned by an external SurfacingPolicy — MPC has no
        # mesh-slot gating, no surface-as-action. The depth ladder is
        # already filtered to submerged-only in __post_init__; surfacing
        # appears in MPC only as the Kalman update at `next_surface_t`.
        depth_options = depth_options_full
        K = depth_options.size

        for interval_idx in range(self.horizon_n):
            # Expand each beam by K options. Per-eval rollout state is
            # replicated across all K children.
            B = lats_b.shape[0]
            new_setpoints = np.tile(depth_options, B)        # (B*K,)
            new_setpoint_idx = np.tile(np.arange(K), B)      # (B*K,)
            # New beams: shape (B*K, n_eval). np.repeat along axis=0.
            lats_e = np.repeat(lats_b, K, axis=0)
            lons_e = np.repeat(lons_b, K, axis=0)
            depths_e = np.repeat(depths_b, K, axis=0)
            sigma_pos_sq_e = np.repeat(sigma_pos_sq_b, K, axis=0)
            d_sq_sum_e = np.repeat(d_sq_sum_b, K, axis=0)
            alive_e = np.repeat(alive_b, K, axis=0)
            t_since_anchor_e = np.repeat(t_since_anchor_b, K)
            applied_lora_e = np.repeat(applied_lora_b, K)
            if interval_idx == 0:
                first_value_e = new_setpoints.copy()
            else:
                first_value_e = np.repeat(first_value_b, K)

            # Roll forward ONE decision interval for all expanded
            # candidates × n_eval. Per-substep depth is shared across
            # n_eval (the depth path is deterministic given the action);
            # bias differs per eval (different posterior draws).
            #
            if use_jax:
                (lats_e, lons_e, depths_e, sigma_pos_sq_e, d_sq_sum_e,
                 alive_e, t_since_anchor_e, applied_lora_e) = (
                    self._rollout_interval_jax(
                        lats_e, lons_e, depths_e, sigma_pos_sq_e,
                        d_sq_sum_e, alive_e, t_since_anchor_e,
                        applied_lora_e,
                        new_setpoints=new_setpoints, t0=t,
                        draws_u_j=draws_u_j, draws_v_j=draws_v_j,
                        n_dt_per_interval=n_dt_per_interval,
                        n_dyn_substeps=n_dyn_substeps,
                        sub_dt=sub_dt, dz_per_substep=dz_per_substep,
                        cos_station=cos_station,
                        sigma_lora_sq=sigma_lora_sq,
                        next_surface_t=next_surface_t,
                        hazard_rate=(surface_hazard_rate_per_sec
                                       if surface_hazard_rate_per_sec is not None
                                       else 0.0),
                    )
                )
                t += n_dt_per_interval * self.dt_sec
                n_samples += n_dt_per_interval
            else:
                for _tick in range(n_dt_per_interval):
                    for sub_idx in range(n_dyn_substeps):
                        # Depth ramp: same setpoint across all n_eval.
                        dz = new_setpoints[:, None] - depths_e
                        abs_dz = np.abs(dz)
                        step_dz = np.where(
                            abs_dz <= dz_per_substep,
                            dz,
                            np.sign(dz) * dz_per_substep,
                        )
                        depths_e = depths_e + step_dz
                        t_mid = t + (sub_idx + 0.5) * sub_dt
                        # Knowledge query, optionally per draw.
                        BK = lats_e.shape[0]
                        u_e = np.empty((BK, n_eval))
                        v_e = np.empty((BK, n_eval))
                        if self.posterior_cvar_enabled:
                            for ei in range(n_eval):
                                u_e[:, ei], v_e[:, ei] = (
                                    self.knowledge.get_current_at_batched_draw(  # type: ignore[attr-defined]
                                        lats_e[:, ei], lons_e[:, ei],
                                        depths_e[:, ei], t_mid, ei,
                                    )
                                )
                        else:
                            u_, v_ = self.knowledge.get_current_at_batched(  # type: ignore[attr-defined]
                                lats_e[:, 0], lons_e[:, 0], depths_e[:, 0], t_mid,
                            )
                            u_e[:, 0] = u_
                            v_e[:, 0] = v_
                        bad = ~(np.isfinite(u_e) & np.isfinite(v_e))
                        alive_e = alive_e & ~bad
                        u_e = np.where(bad, 0.0, u_e)
                        v_e = np.where(bad, 0.0, v_e)
                        cos_lat = np.cos(np.deg2rad(lats_e))
                        lats_e = lats_e + (v_e * sub_dt) / EARTH_R_M
                        lons_e = lons_e + (u_e * sub_dt) / (EARTH_R_M * cos_lat)
                        # σ_pos evolution per substep — same for all n_eval
                        # (the OU process-noise model is per-particle in PF
                        # but per-beam here; CVaR over draws captures the
                        # bias-field uncertainty, σ_pos captures the per-
                        # particle noise spread).
                        # σ_pos evolution per substep. Per-beam state:
                        #   `t_since_anchor_e` (sec) drives the OU growth rate
                        #   `sigma_pos_sq_e` accumulates per substep
                        # Reset to 0 + Kalman fusion when the substep crosses
                        # `next_surface_t` — only once per beam.
                        if self.process_noise_cfg is not None:
                            t_mid_anchor_e = t_since_anchor_e + 0.5 * sub_dt
                            rate_per_beam = sigma_pos_growth_rate_per_axis_vec(
                                new_setpoints, t_mid_anchor_e,
                                self.process_noise_cfg,
                            )
                            # OU growth.
                            sigma_pos_sq_e = (
                                sigma_pos_sq_e + rate_per_beam[:, None] * sub_dt
                            )
                            # Hazard-rate surface dilution: continuous-time
                            # expected sink toward σ_lora² for unpredictable
                            # surfacing schedules. dσ²/dt += -λ·(σ²−σ_lora²).
                            if surface_hazard_rate_per_sec is not None:
                                lam = float(surface_hazard_rate_per_sec)
                                if lam > 0:
                                    sigma_pos_sq_e = (
                                        sigma_pos_sq_e
                                        - lam * sub_dt
                                          * (sigma_pos_sq_e - sigma_lora_sq)
                                    )
                            t_since_anchor_e = t_since_anchor_e + sub_dt

                            # Planned-surface Kalman fusion at next_surface_t.
                            substep_end_t = t + (sub_idx + 1) * sub_dt
                            if (substep_end_t >= next_surface_t
                                and not applied_lora_e.all()):
                                apply_mask = ~applied_lora_e
                                if apply_mask.any():
                                    sigma_pos_sq_e[apply_mask] = (
                                        (sigma_pos_sq_e[apply_mask]
                                         * sigma_lora_sq)
                                        / np.maximum(
                                            sigma_pos_sq_e[apply_mask]
                                            + sigma_lora_sq,
                                            1e-12,
                                        )
                                    )
                                    t_since_anchor_e[apply_mask] = 0.0
                                    applied_lora_e[apply_mask] = True
                    t += self.dt_sec
                    d_lat_m = (lats_e - self.station_lat) * EARTH_R_M
                    d_lon_m = (lons_e - self.station_lon) * EARTH_R_M * cos_station
                    d_sq = d_lat_m ** 2 + d_lon_m ** 2
                    d_sq_sum_e = d_sq_sum_e + d_sq
                    n_samples += 1

            # Prune to beam_width — score aggregates over n_eval.
            mean_d_sq_e = d_sq_sum_e / max(n_samples, 1)        # (B*K, n_eval)
            mean_sigma_sq_e = sigma_pos_sq_e                    # (B*K, n_eval)
            mean_d_sq_avg = mean_d_sq_e.mean(axis=1)            # (B*K,)
            mean_sigma_sq_avg = mean_sigma_sq_e.mean(axis=1)    # (B*K,)
            if n_eval >= 2 and (self.objective_lambda > 0
                                  or self.objective_gamma > 0):
                # Tail mean (CVaR): sort across eval axis ascending, take
                # the top tail-fraction. With small n_eval, "tail" is the
                # max; with larger N, the mean of the top φ fraction.
                k_tail = max(1, int(np.ceil(self.cvar_alpha * n_eval)))
                # Sort each row's d_sq descending, take top k_tail mean.
                d_sq_sorted = np.sort(mean_d_sq_e, axis=1)[:, ::-1]
                cvar_d_sq = d_sq_sorted[:, :k_tail].mean(axis=1)
                sigma_sorted = np.sort(mean_sigma_sq_e, axis=1)[:, ::-1]
                cvar_sigma_sq = sigma_sorted[:, :k_tail].mean(axis=1)
            else:
                cvar_d_sq = mean_d_sq_avg
                cvar_sigma_sq = mean_sigma_sq_avg
            score_e = (
                self.objective_alpha * mean_d_sq_avg
                + self.objective_beta * mean_sigma_sq_avg
                + self.objective_lambda * cvar_d_sq
                + self.objective_gamma * cvar_sigma_sq
            )
            # Alive over evals: a beam survives if at least one eval is
            # alive (be permissive — no-finite eval means infinite cost,
            # naturally pruned by the score).
            beam_alive = alive_e.any(axis=1)
            score_e = np.where(beam_alive, score_e, np.inf)
            if score_e.size > self.beam_width:
                keep_idx = np.argpartition(
                    score_e, self.beam_width,
                )[:self.beam_width]
            else:
                keep_idx = np.arange(score_e.size)
            lats_b = lats_e[keep_idx]
            lons_b = lons_e[keep_idx]
            depths_b = depths_e[keep_idx]
            sigma_pos_sq_b = sigma_pos_sq_e[keep_idx]
            d_sq_sum_b = d_sq_sum_e[keep_idx]
            first_value_b = first_value_e[keep_idx]
            alive_b = alive_e[keep_idx]
            t_since_anchor_b = t_since_anchor_e[keep_idx]
            applied_lora_b = applied_lora_e[keep_idx]

        # Final scoring + pick best first action.
        mean_d_sq_b = d_sq_sum_b / max(n_samples, 1)
        mean_sigma_sq_b = sigma_pos_sq_b
        mean_d_sq_avg = mean_d_sq_b.mean(axis=1)
        mean_sigma_sq_avg = mean_sigma_sq_b.mean(axis=1)
        if n_eval >= 2 and (self.objective_lambda > 0
                              or self.objective_gamma > 0):
            k_tail = max(1, int(np.ceil(self.cvar_alpha * n_eval)))
            d_sq_sorted = np.sort(mean_d_sq_b, axis=1)[:, ::-1]
            cvar_d_sq = d_sq_sorted[:, :k_tail].mean(axis=1)
            sigma_sorted = np.sort(mean_sigma_sq_b, axis=1)[:, ::-1]
            cvar_sigma_sq = sigma_sorted[:, :k_tail].mean(axis=1)
        else:
            cvar_d_sq = mean_d_sq_avg
            cvar_sigma_sq = mean_sigma_sq_avg
        final_score = (
            self.objective_alpha * mean_d_sq_avg
            + self.objective_beta * mean_sigma_sq_avg
            + self.objective_lambda * cvar_d_sq
            + self.objective_gamma * cvar_sigma_sq
        )
        beam_alive = alive_b.any(axis=1)
        final_score = np.where(beam_alive, final_score, np.inf)
        finite = np.isfinite(final_score)
        if finite.any():
            self.last_predicted_sigma_pos_horizon_m = float(
                np.sqrt(np.mean(mean_sigma_sq_avg[finite]))
            )
        else:
            self.last_predicted_sigma_pos_horizon_m = float("nan")
        # Per-first-action best score, for the legacy `scores` return dict.
        best_per_first: dict[float, float] = {
            float(d): float("inf") for d in depth_options_full
        }
        for d in depth_options_full:
            mask = (first_value_b == d) & beam_alive
            if mask.any():
                best_per_first[float(d)] = float(final_score[mask].min())
        if not finite.any():
            return float(depth_options_full[0]), best_per_first
        best_idx = int(np.argmin(np.where(finite, final_score, np.inf)))
        chosen_value = float(first_value_b[best_idx])
        return chosen_value, best_per_first

    def _jax_prepare_plan(self) -> tuple:
        """Per-plan JAX setup: lazy bundle build (first call only) +
        device_put posterior draws once. Returns (draws_u_j, draws_v_j)
        for the rollout-interval calls within this plan. Reading the
        bias-cache fields from `LiveBiasKnowledge` is intentional —
        path-A integration treats them as a stable per-plan input;
        path-D will move this through a public `get_bias_draws()` API.
        """
        import mpc_rollout_jax as mr   # type: ignore[import-not-found]
        import jax.numpy as jnp        # type: ignore[import-not-found]

        if self._jax_bundle is None:
            knowledge = self.knowledge
            tf = knowledge.nemo_prior.nemo  # type: ignore[attr-defined]
            basis = knowledge.basis           # type: ignore[attr-defined]
            self._jax_bundle = mr.build_bundle(
                tf, basis, self.process_noise_cfg,
            )
            self._jax_interval_fn = mr.get_compiled_interval()

        knowledge = self.knowledge
        if self.posterior_cvar_enabled:
            draws_u_np = knowledge._cache_draws_u   # type: ignore[attr-defined]
            draws_v_np = knowledge._cache_draws_v   # type: ignore[attr-defined]
        else:
            draws_u_np = knowledge._cache_ens_u[None, :, :, :]  # type: ignore[attr-defined]
            draws_v_np = knowledge._cache_ens_v[None, :, :, :]  # type: ignore[attr-defined]
        return (
            jnp.asarray(draws_u_np, dtype=jnp.float32),
            jnp.asarray(draws_v_np, dtype=jnp.float32),
        )

    def _rollout_interval_jax(
        self,
        lats_e: np.ndarray, lons_e: np.ndarray, depths_e: np.ndarray,
        sigma_pos_sq_e: np.ndarray, d_sq_sum_e: np.ndarray,
        alive_e: np.ndarray, t_since_anchor_e: np.ndarray,
        applied_lora_e: np.ndarray,
        *,
        new_setpoints: np.ndarray, t0: float,
        draws_u_j: object, draws_v_j: object,
        n_dt_per_interval: int, n_dyn_substeps: int,
        sub_dt: float, dz_per_substep: float,
        cos_station: float,
        sigma_lora_sq: float, next_surface_t: float,
        hazard_rate: float,
    ) -> tuple:
        """JAX-jitted single-interval rollout (n_ticks × n_substeps).
        `draws_u_j`/`draws_v_j` are device-resident jnp arrays prepared
        once per plan via `_jax_prepare_plan`. State arrays stay f32
        across intervals (downstream score+prune in `choose_depth` is
        f32-safe; mask arrays stay bool)."""
        import mpc_rollout_jax as mr   # type: ignore[import-not-found]
        import jax.numpy as jnp        # type: ignore[import-not-found]

        state_np = {
            "lats": lats_e, "lons": lons_e, "depths": depths_e,
            "sigma_pos_sq": sigma_pos_sq_e, "d_sq_sum": d_sq_sum_e,
            "alive": alive_e, "t_since_anchor": t_since_anchor_e,
            "applied_lora": applied_lora_e,
        }
        state_j = mr.state_to_jnp(state_np)
        out_j = self._jax_interval_fn(  # type: ignore[misc]
            state_j, bundle=self._jax_bundle,
            setpoints=jnp.asarray(new_setpoints, dtype=jnp.float32),
            t0=float(t0),
            draws_u=draws_u_j, draws_v=draws_v_j,
            n_ticks=int(n_dt_per_interval),
            n_substeps=int(n_dyn_substeps),
            sub_dt=float(sub_dt),
            dz_per_substep=float(dz_per_substep),
            station_lat=float(self.station_lat),
            station_lon=float(self.station_lon),
            cos_station=float(cos_station),
            sigma_lora_sq=float(sigma_lora_sq),
            next_surface_t=float(next_surface_t),
            hazard_rate=float(hazard_rate),
        )
        out_np = mr.state_to_np(out_j)
        return (
            out_np["lats"], out_np["lons"], out_np["depths"],
            out_np["sigma_pos_sq"], out_np["d_sq_sum"],
            out_np["alive"].astype(bool),
            out_np["t_since_anchor"],
            out_np["applied_lora"].astype(bool),
        )


# ---------------------------------------------------------------------------
# Phase A convenience: a KnowledgeSource that wraps a TruthField directly.
# ---------------------------------------------------------------------------

@dataclass
class PerfectKnowledge:
    """KnowledgeSource that returns truth currents, no degradation."""

    truth: "object"  # truth_field.TruthField — kept loose to avoid cyclic import styling.

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        return self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]

    def get_current_at_batched(
        self, lats: np.ndarray, lons: np.ndarray,
        depths: np.ndarray, t_sec: float,
    ):
        """Vectorized N-point sample. Required by MPCStationKeeper's
        vectorized rollout path. The wrapped truth must implement
        `sample_batched`."""
        return self.truth.sample_batched(lats, lons, depths, t_sec)  # type: ignore[attr-defined]

    # --- Posterior-CVaR API (degenerate; truth has no uncertainty) ---
    # MPC's posterior_cvar path expects `precompute_posterior_draws` and
    # `get_current_at_batched_draw`. For PerfectKnowledge, "draws" are
    # all identical to truth: there is no bias posterior to sample.
    # Implementing as no-ops keeps the perfect_info arm on the same
    # MPC code path as grid+ctd (apples-to-apples controller), with
    # CVaR over degenerate draws collapsing mathematically to
    # ensemble_mean.
    def precompute_posterior_draws(
        self, n_draws: int, rng: np.random.Generator,
    ) -> None:
        # Truth has no posterior — store n_draws so the rollout knows
        # how many "draws" to evaluate (all identical).
        self._n_posterior_draws = n_draws

    def get_current_at_batched_draw(
        self, lats: np.ndarray, lons: np.ndarray,
        depths: np.ndarray, t_sec: float, draw_idx: int,
    ):
        """Returns truth currents regardless of `draw_idx` — degenerate
        posterior, all draws identical. CVaR over identical draws =
        single-realisation cost, mathematically equivalent to
        ensemble_mean scoring (no tail to penalise)."""
        return self.truth.sample_batched(lats, lons, depths, t_sec)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Passive-drag keeper (option A): controller picks (depth, alpha) where
# alpha ∈ [alpha_min, 1.0] scales advection magnitude. Models retractable
# drogue / variable-area drag surface. Cannot steer — only slow.
# ---------------------------------------------------------------------------

@dataclass
class DragKeeper:
    station_lat: float
    station_lon: float
    available_depths_m: list[float]
    lookahead_sec: float
    knowledge: KnowledgeSource
    alpha_min: float = 0.5       # 0.5 = halve drift magnitude at best
    alpha_n_levels: int = 4      # includes both endpoints

    def choose_action(
        self,
        lat: float, lon: float, t_sec: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
    ) -> tuple[float, float, dict[tuple[float, float], float]]:
        """Return (best_depth, best_alpha, scores_by_(depth, alpha))."""
        px = lat if perceived_lat is None else perceived_lat
        py = lon if perceived_lon is None else perceived_lon
        alphas = np.linspace(self.alpha_min, 1.0, self.alpha_n_levels).tolist()
        scores: dict[tuple[float, float], float] = {}
        for d in self.available_depths_m:
            u, v = self.knowledge.get_current_at(px, py, d, t_sec)
            if not (np.isfinite(u) and np.isfinite(v)):
                for a in alphas:
                    scores[(d, a)] = float("nan")
                continue
            for a in alphas:
                dlat, dlon = lat_lon_step_from_velocity(
                    u * a, v * a, px, self.lookahead_sec,
                )
                proj_lat = px + dlat
                proj_lon = py + dlon
                scores[(d, a)] = distance_m(proj_lat, proj_lon,
                                             self.station_lat, self.station_lon)
        valid = {k: s for k, s in scores.items() if np.isfinite(s)}
        if not valid:
            return self.available_depths_m[0], 1.0, scores
        best_key = min(valid, key=lambda k: valid[k])
        return best_key[0], best_key[1], scores


# ---------------------------------------------------------------------------
# Glider-transition keeper (option B): controller picks (depth_setpoint,
# glide_heading). Glide speed is fixed hardware parameter; thrust is
# delivered only during depth transitions. The optimal heading is
# analytically the direction toward the station, modulated by ambient
# drift at the target depth.
# ---------------------------------------------------------------------------

@dataclass
class GliderKeeper:
    station_lat: float
    station_lon: float
    available_depths_m: list[float]
    lookahead_sec: float
    knowledge: KnowledgeSource
    glide_v_ms: float = 0.10     # typical Slocum-class glide speed ~10-25 cm/s
    w_z_max_ms: float = 0.1      # ballast vertical speed (sets transit time)

    def choose_action(
        self,
        lat: float, lon: float, t_sec: float,
        current_depth_m: float,
        perceived_lat: float | None = None,
        perceived_lon: float | None = None,
    ) -> tuple[float, tuple[float, float], dict[float, float]]:
        """Return (best_depth, best_glide_uv, scores_by_depth).

        For each candidate depth, computes the analytically-optimal
        glide heading that minimises projected distance to station,
        accounting for the fraction of lookahead spent in transition.
        A depth pick of current_depth gives zero transition → no glide.
        """
        from truth_field import EARTH_R_M  # type: ignore[import-not-found]
        px = lat if perceived_lat is None else perceived_lat
        py = lon if perceived_lon is None else perceived_lon
        cos_lat = np.cos(np.deg2rad(px))
        target_d_lat_m = (self.station_lat - px) * EARTH_R_M
        target_d_lon_m = (self.station_lon - py) * EARTH_R_M * cos_lat

        scores: dict[float, float] = {}
        glides: dict[float, tuple[float, float]] = {}
        for d in self.available_depths_m:
            u, v = self.knowledge.get_current_at(px, py, d, t_sec)
            if not (np.isfinite(u) and np.isfinite(v)):
                scores[d] = float("nan")
                glides[d] = (0.0, 0.0)
                continue

            # Transit time from current_depth → d at w_z_max.
            if self.w_z_max_ms > 0:
                transit_s = abs(d - current_depth_m) / self.w_z_max_ms
            else:
                transit_s = 0.0
            tau_eff = min(transit_s, self.lookahead_sec)
            glide_capacity_m = self.glide_v_ms * tau_eff

            # Ambient displacement over the full lookahead at target depth.
            ambient_dlon_m = u * self.lookahead_sec
            ambient_dlat_m = v * self.lookahead_sec

            # Glide vector needed to exactly cancel residual drift toward
            # station. If within glide capacity, use it; else cap.
            desired_u = (target_d_lon_m - ambient_dlon_m) / max(tau_eff, 1e-6)
            desired_v = (target_d_lat_m - ambient_dlat_m) / max(tau_eff, 1e-6)
            desired_mag = float(np.hypot(desired_u, desired_v))
            if glide_capacity_m <= 0 or desired_mag <= 0:
                glide = (0.0, 0.0)
            elif desired_mag <= self.glide_v_ms:
                glide = (desired_u, desired_v)
            else:
                scale = self.glide_v_ms / desired_mag
                glide = (desired_u * scale, desired_v * scale)

            # Projected displacement with chosen glide.
            dlon_total = ambient_dlon_m + glide[0] * tau_eff
            dlat_total = ambient_dlat_m + glide[1] * tau_eff
            proj_lat = px + dlat_total / EARTH_R_M
            proj_lon = py + dlon_total / (EARTH_R_M * cos_lat)
            scores[d] = distance_m(proj_lat, proj_lon,
                                    self.station_lat, self.station_lon)
            glides[d] = glide

        valid = {d: s for d, s in scores.items() if np.isfinite(s)}
        if not valid:
            return self.available_depths_m[0], (0.0, 0.0), scores
        best_d = min(valid, key=lambda d: valid[d])
        return best_d, glides[best_d], scores
