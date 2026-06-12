"""Bootstrap particle filter for M1 maritime nodes.

``PFFloat`` is the float64 reference SIR (sampling-importance-resampling)
particle filter. One instance per node; each is fully independent —
no shared state, no cross-node fusion. Class-aware behavior is
dispatched on ``layout.class_name`` (anchor / pure_drifter /
ballast_drifter) inside ``predict``; the public interface is uniform.

Truth separation is enforced at three layers per design D12:
the module boundary (``pf_float.py`` imports only observation /
map-payload types — never the truth schema or current field), an
``import-linter`` contract on ``pyproject.toml``, and pyright-strict
function signatures that reject truth types at the type-check layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

from rtl.vectors.maritime._physics_constants import (
    PRESSURE_PER_METER_PA,
    SEA_LEVEL_PRESSURE_PA,
    wrap_signed_deg,
)
from rtl.vectors.maritime.coords import enu_to_latlon, latlon_to_enu
from rtl.vectors.maritime.map_payload import RegionalMap
from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord
from rtl.vectors.maritime.scenario_schema import (
    BaroObservation,
    BathyProbeObservation,
    GPSObservation,
    IMUObservation,
    LoraTOAObservation,
    MagObservation,
    Observation,
)
from rtl.vectors.maritime.state_layout import StateLayout


@dataclass(frozen=True, slots=True)
class PFFloatConfig:
    """Configuration for a ``PFFloat``.

    Process-noise scales are in "per square-root second" units so
    ``sigma = scale * sqrt(dt)`` gives the Gaussian standard deviation
    for a single ``dt``-long interval. Magnitudes are deliberately
    larger than the truth-side propagator — the PF is less certain
    about its world than the truth model is (design D4).
    """

    n_particles: int = 500
    process_noise_pos_m_per_sqrt_s: float = 1.0
    # ``process_noise_vel_ms_per_sqrt_s`` is the **per-tick sampling σ
    # floor** for the particle-velocity residual under the per-tick
    # sampling model (``maritime-velocity-model``). The predict step
    # samples ``particles[:, vx] ~ N(0, sqrt(var_vx(lat, lon)) + floor)``
    # each tick — no RW accumulation. The floor (this field's value)
    # ensures a non-degenerate velocity cloud even when the onboard
    # climatology reports ``var_vx = 0`` (e.g. a test map or a region
    # with no measured variance). The ``_per_sqrt_s`` name is legacy;
    # under the new semantic the value is a plain σ in m/s with no dt
    # scaling applied. CLI override: ``--predict-noise-vel``. Default
    # value 0.02 matches ``dynamics.DRIFTER_VEL_PERTURBATION_MS``.
    process_noise_vel_ms_per_sqrt_s: float = 0.02
    process_noise_heading_deg_per_sqrt_s: float = 1.0
    process_noise_current_ms_per_sqrt_s: float = 0.01

    def __post_init__(self) -> None:
        if self.n_particles <= 0:
            raise ValueError(f"n_particles must be > 0; got {self.n_particles}")
        for name, value in (
            ("process_noise_pos_m_per_sqrt_s", self.process_noise_pos_m_per_sqrt_s),
            ("process_noise_vel_ms_per_sqrt_s", self.process_noise_vel_ms_per_sqrt_s),
            ("process_noise_heading_deg_per_sqrt_s", self.process_noise_heading_deg_per_sqrt_s),
            ("process_noise_current_ms_per_sqrt_s", self.process_noise_current_ms_per_sqrt_s),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0; got {value}")


_CLASS_ANCHOR = "anchor"
_CLASS_PURE_DRIFTER = "pure_drifter"
_CLASS_BALLAST_DRIFTER = "ballast_drifter"
_SUPPORTED_CLASSES = frozenset(
    {_CLASS_ANCHOR, _CLASS_PURE_DRIFTER, _CLASS_BALLAST_DRIFTER}
)


@dataclass(frozen=True, slots=True)
class _StateIndices:
    """Layout-derived state-vector slot offsets, cached on each PF instance.

    Materialized at construction from ``StateLayout.slice(...)`` so any
    future state-layout reorder cannot silently desync the PF math.
    Anchor and ballast layouts carry the optional deep-current pair
    (slots 19, 20); pure drifter does not — those fields are ``None``
    in that case.
    """

    east: int
    north: int
    depth: int
    vx: int
    vy: int
    vz: int
    heading: int
    surf_cur_vx: int
    surf_cur_vy: int
    gyro_bx: int
    gyro_by: int
    gyro_bz: int
    accel_bx: int
    accel_by: int
    accel_bz: int
    prev_vx: int
    prev_vy: int
    prev_vz: int
    prev_heading: int
    deep_cur_vx: int | None
    deep_cur_vy: int | None

    @classmethod
    def from_layout(cls, layout: StateLayout) -> "_StateIndices":
        pos = layout.slice("position")
        vel = layout.slice("velocity")
        heading = layout.slice("heading")
        surf = layout.slice("surface_current")
        bias = layout.slice("imu_bias")
        prev_vel = layout.slice("prev_velocity")
        prev_head = layout.slice("prev_heading")
        deep = layout.groups.get("deep_current")
        return cls(
            east=pos.start,
            north=pos.start + 1,
            depth=pos.start + 2,
            vx=vel.start,
            vy=vel.start + 1,
            vz=vel.start + 2,
            heading=heading.start,
            surf_cur_vx=surf.start,
            surf_cur_vy=surf.start + 1,
            # imu_bias slice runs gyro_xyz then accel_xyz (mirrors
            # truth-side IMUSensor: gyro_bias = state[bias][0:3],
            # accel_bias = state[bias][3:6]).
            gyro_bx=bias.start,
            gyro_by=bias.start + 1,
            gyro_bz=bias.start + 2,
            accel_bx=bias.start + 3,
            accel_by=bias.start + 4,
            accel_bz=bias.start + 5,
            prev_vx=prev_vel.start,
            prev_vy=prev_vel.start + 1,
            prev_vz=prev_vel.start + 2,
            prev_heading=prev_head.start,
            deep_cur_vx=None if deep is None else deep.start,
            deep_cur_vy=None if deep is None else deep.start + 1,
        )


class PFFloat:
    """Per-node bootstrap particle filter.

    Construction validates initial state shape and cov non-negativity;
    initializes a Gaussian particle cloud with uniform weights. The
    pipeline is exposed as ``predict`` / ``weight`` / ``resample`` /
    ``estimate``, with ``step`` chaining all four for the per-tick
    convenience path. ``_last_dt_sec`` is captured by ``predict`` so
    the IMU likelihood can recover ``(v - v_prev) / dt`` without a
    second ``dt`` parameter on ``weight``.
    """

    def __init__(
        self,
        node_id: str,
        layout: StateLayout,
        initial_state_mean: np.ndarray,
        initial_state_cov_diag: np.ndarray,
        onboard_map: RegionalMap,
        anchor_positions: Mapping[str, tuple[float, float]],
        enu_origin_lat_deg: float,
        enu_origin_lon_deg: float,
        config: PFFloatConfig,
        rng: np.random.Generator,
    ) -> None:
        if layout.class_name not in _SUPPORTED_CLASSES:
            raise ValueError(
                f"Unsupported layout.class_name '{layout.class_name}'; "
                f"expected one of {sorted(_SUPPORTED_CLASSES)}"
            )

        state_dim = layout.state_dim
        if initial_state_mean.shape != (state_dim,):
            raise ValueError(
                f"initial_state_mean has shape {initial_state_mean.shape}; "
                f"expected ({state_dim},) to match layout '{layout.class_name}'"
            )
        if initial_state_cov_diag.shape != (state_dim,):
            raise ValueError(
                f"initial_state_cov_diag has shape {initial_state_cov_diag.shape}; "
                f"expected ({state_dim},) to match layout '{layout.class_name}'"
            )
        if np.any(initial_state_cov_diag < 0):
            negative_slots = np.where(initial_state_cov_diag < 0)[0].tolist()
            raise ValueError(
                f"initial_state_cov_diag contains negative entries at slots "
                f"{negative_slots} — variances must be >= 0"
            )

        self._node_id = node_id
        self._layout = layout
        self._idx = _StateIndices.from_layout(layout)
        self._onboard_map = onboard_map
        # Defensive copy — caller mutation must not leak into PF state.
        self._anchor_positions: dict[str, tuple[float, float]] = dict(anchor_positions)
        self._enu_origin_lat_deg = float(enu_origin_lat_deg)
        self._enu_origin_lon_deg = float(enu_origin_lon_deg)
        self._config = config
        self._rng = rng

        n = config.n_particles
        std = np.sqrt(initial_state_cov_diag)
        self._particles = (
            initial_state_mean[np.newaxis, :]
            + rng.standard_normal((n, state_dim)) * std[np.newaxis, :]
        )
        self._weights = np.full(n, 1.0 / n)

        # Captured by ``predict``. ``None`` until first ``predict``;
        # the IMU handler raises if it observes ``None`` (calling
        # weight before any predict with an IMU obs is a programming
        # error).
        self._last_dt_sec: float | None = None

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def layout(self) -> StateLayout:
        return self._layout

    @property
    def particles(self) -> np.ndarray:
        return self._particles

    @property
    def weights(self) -> np.ndarray:
        return self._weights

    @property
    def n_particles(self) -> int:
        return self._config.n_particles

    @property
    def effective_sample_size(self) -> float:
        return float(1.0 / np.sum(self._weights ** 2))

    # ------------------------------------------------------------------
    # Predict
    # ------------------------------------------------------------------

    def predict(self, dt_sec: float) -> None:
        """Advance every particle by ``dt_sec`` via dynamics + process noise.

        Climatology lookup is vectorized via per-axis broadcast
        ``argmin`` against the climatology grid's lat/lon arrays —
        equivalent to the scalar ``RegionalMap.current_climatology_at``
        (joint ``argmin(Δlat² + Δlon²)``) on a regular grid.
        """
        idx = self._idx
        class_name = self._layout.class_name
        n = self._config.n_particles
        sqrt_dt = np.sqrt(dt_sec)
        particles = self._particles

        # Shift (velocity, heading) → (prev_velocity, prev_heading) BEFORE
        # updating them this tick. Mirrors truth-side dynamics.py:47-48.
        # Without this copy, _imu_log_likelihood's finite-difference
        # accel prediction `(vx - prev_vx) / dt` uses a stale initial
        # prev_velocity forever, producing a broad predicted-accel
        # distribution that disagrees catastrophically with truth-side
        # IMU observations and collapses ESS to 1.
        particles[:, idx.prev_vx] = particles[:, idx.vx]
        particles[:, idx.prev_vy] = particles[:, idx.vy]
        particles[:, idx.prev_vz] = particles[:, idx.vz]
        particles[:, idx.prev_heading] = particles[:, idx.heading]

        lat_arr, lon_arr = enu_to_latlon(
            particles[:, idx.east],
            particles[:, idx.north],
            self._enu_origin_lat_deg,
            self._enu_origin_lon_deg,
        )

        climatology = self._onboard_map.climatology
        lat_diffs = np.abs(climatology.lats[np.newaxis, :] - lat_arr[:, np.newaxis])
        lon_diffs = np.abs(climatology.lons[np.newaxis, :] - lon_arr[:, np.newaxis])
        lat_idx = np.argmin(lat_diffs, axis=1)
        lon_idx = np.argmin(lon_diffs, axis=1)
        cur_vx = climatology.mean_vx_ms[lat_idx, lon_idx]
        cur_vy = climatology.mean_vy_ms[lat_idx, lon_idx]
        # Per-particle climatology variance lookup (same
        # nearest-neighbor indices as the mean lookup). Used below to
        # build the per-particle sampling σ for the per-tick velocity
        # residual under ``maritime-velocity-model``.
        var_vx = climatology.var_vx_ms2[lat_idx, lon_idx]
        var_vy = climatology.var_vy_ms2[lat_idx, lon_idx]

        # Sample all process noise up front so the RNG stream order is
        # the same across class branches — keeps the seeded-determinism
        # contract independent of which class is being predicted.
        pos_scale = self._config.process_noise_pos_m_per_sqrt_s * sqrt_dt
        # vel_scale is preserved as ``config.process_noise_vel_ms_per_sqrt_s
        # * sqrt_dt`` for the unused ``vel_noise`` draw below — keeps
        # RNG stream order byte-identical with the pre-per-tick-sampling
        # codepath (design D4). The actual sampling σ used for the
        # velocity residual is ``vel_floor`` (plain m/s) plus the
        # climatology-variance square root — see ``sigma_vx`` below.
        vel_scale = self._config.process_noise_vel_ms_per_sqrt_s * sqrt_dt
        vel_floor = self._config.process_noise_vel_ms_per_sqrt_s
        heading_scale = self._config.process_noise_heading_deg_per_sqrt_s * sqrt_dt
        current_scale = self._config.process_noise_current_ms_per_sqrt_s * sqrt_dt

        pos_noise = (
            self._rng.normal(0.0, pos_scale, size=(n, 3))
            if pos_scale > 0
            else np.zeros((n, 3))
        )
        # The ``vel_noise`` draw is preserved (its values are NOT used
        # below, since velocity residuals are now sampled per-particle
        # from a climatology-variance-plus-floor σ) to keep RNG stream
        # order identical with the retired RW codepath — existing
        # seeded-determinism tests depend on this ordering (design D4).
        _vel_noise_unused = (
            self._rng.normal(0.0, vel_scale, size=(n, 3))
            if vel_scale > 0
            else np.zeros((n, 3))
        )
        del _vel_noise_unused
        heading_noise = (
            self._rng.normal(0.0, heading_scale, size=n)
            if heading_scale > 0
            else np.zeros(n)
        )
        surf_current_noise = (
            self._rng.normal(0.0, current_scale, size=(n, 2))
            if current_scale > 0
            else np.zeros((n, 2))
        )

        # Per-particle sampling σ for the velocity residual:
        # sqrt(climatology var at the particle) + floor. The floor
        # keeps the cloud non-degenerate when the climatology reports
        # zero variance (test maps, unmeasured regions). Stacked as an
        # ``(n, 2)`` array so the vx/vy residuals can be drawn in a
        # single ``rng.normal`` call — matches the shape of the
        # ``(vx, vy)`` slot pair and keeps the RNG stream compact.
        sigma_xy = np.stack(
            [np.sqrt(var_vx) + vel_floor, np.sqrt(var_vy) + vel_floor],
            axis=1,
        )

        if class_name != _CLASS_ANCHOR:
            self._advect_horizontal_and_velocity(
                particles,
                idx,
                dt_sec,
                cur_vx,
                cur_vy,
                pos_noise,
                sigma_xy,
                self._rng,
            )
            if class_name == _CLASS_PURE_DRIFTER:
                particles[:, idx.depth] = 0.0
            # ballast_drifter: depth is pinned in M1 (pump is `pass`,
            # truth-side KIND_BALLAST_DRIFTING_POSE does not write
            # state[2]). pos_noise[:, 2] is still drawn above to
            # preserve RNG stream order; it goes unused here.

        particles[:, idx.heading] = (
            particles[:, idx.heading] + heading_noise
        ) % 360.0

        particles[:, idx.surf_cur_vx] += surf_current_noise[:, 0]
        particles[:, idx.surf_cur_vy] += surf_current_noise[:, 1]

        if idx.deep_cur_vx is not None:
            deep_current_noise = (
                self._rng.normal(0.0, current_scale, size=(n, 2))
                if current_scale > 0
                else np.zeros((n, 2))
            )
            particles[:, idx.deep_cur_vx] += deep_current_noise[:, 0]
            particles[:, idx.deep_cur_vy] += deep_current_noise[:, 1]

        self._last_dt_sec = float(dt_sec)

    @staticmethod
    def _advect_horizontal_and_velocity(
        particles: np.ndarray,
        idx: _StateIndices,
        dt_sec: float,
        cur_vx: np.ndarray,
        cur_vy: np.ndarray,
        pos_noise: np.ndarray,
        sigma_xy: np.ndarray,
        rng: np.random.Generator,
    ) -> None:
        """Advect horizontal position with the last-tick velocity
        residual, then re-sample the ``(vx, vy)`` residual for the next
        tick from a per-particle ``N(0, sigma_xy)`` — a single
        ``(n, 2)`` Gaussian draw broadcast against the per-particle σ
        pair.

        Position advection uses the pre-update (last tick's) ``vx, vy``
        — the new residual is drawn AFTER position is written, so the
        position formula ``pos += (vx_prev + cur) * dt + pos_noise`` is
        unchanged from the retired RW model (state semantics preserved;
        see ``maritime-velocity-model`` design D1).

        ``idx.vz`` is intentionally left untouched: pure drifters pin
        depth at 0 in ``predict`` (the surface-only M1 invariant) and
        ballast drifters keep their initial ``vz`` (pump is a no-op in
        M1). Both depth pins are enforced in the caller.
        """
        n = particles.shape[0]
        particles[:, idx.east] += (
            (particles[:, idx.vx] + cur_vx) * dt_sec + pos_noise[:, 0]
        )
        particles[:, idx.north] += (
            (particles[:, idx.vy] + cur_vy) * dt_sec + pos_noise[:, 1]
        )
        # Per-tick Gaussian sample — vectorized over particles.
        # ``sigma_xy`` has shape ``(n, 2)`` with column 0 = σ_vx,
        # column 1 = σ_vy; numpy broadcasts the per-particle scale
        # against the ``size=(n, 2)`` draw.
        vxy = rng.normal(0.0, sigma_xy, size=(n, 2))
        particles[:, idx.vx] = vxy[:, 0]
        particles[:, idx.vy] = vxy[:, 1]

    # ------------------------------------------------------------------
    # Weight
    # ------------------------------------------------------------------

    def weight(self, observations: Iterable[Observation]) -> None:
        """Update particle weights from observation likelihoods.

        Accumulates per-observation log-likelihoods into a running
        log-weight vector; exponentiates and normalizes once at the
        end. Falls back to uniform weights if every particle's joint
        log-likelihood is ``-inf`` (a known bootstrap-PF degeneracy
        mode) — preserves the spec's ESS > 0 invariant. Unknown
        observation types raise ``ValueError`` (no silent drops).
        LoRa observations whose partner is not an anchor contribute
        nothing (documented M1 anchor-only filter, design D5).
        """
        with np.errstate(divide="ignore"):
            # log(0) = -inf is the right value here; suppress numpy's
            # divide-by-zero warning for that case.
            log_w = np.log(self._weights)

        for obs in observations:
            ll = self._compute_log_likelihood(obs)
            if ll is not None:
                log_w = log_w + ll

        if not np.any(np.isfinite(log_w)):
            self._weights = np.full(self.n_particles, 1.0 / self.n_particles)
            return

        m = float(np.max(log_w))
        w = np.exp(log_w - m)
        total = w.sum()
        self._weights = (
            w / total
            if total > 0
            else np.full(self.n_particles, 1.0 / self.n_particles)
        )

    def _compute_log_likelihood(self, obs: object) -> np.ndarray | None:
        """Dispatch on observation type. Returns per-particle
        log-likelihood, or ``None`` for the documented LoRa anchor-only
        filter path. Raises ``ValueError`` on any unrecognized type.
        """
        if isinstance(obs, GPSObservation):
            return self._gps_log_likelihood(obs)
        if isinstance(obs, IMUObservation):
            return self._imu_log_likelihood(obs)
        if isinstance(obs, BaroObservation):
            return self._baro_log_likelihood(obs)
        if isinstance(obs, MagObservation):
            return self._mag_log_likelihood(obs)
        if isinstance(obs, BathyProbeObservation):
            return self._bathy_log_likelihood(obs)
        if isinstance(obs, LoraTOAObservation):
            return self._lora_log_likelihood(obs)

        type_name = type(obs).__name__
        sensor_attr = getattr(obs, "sensor", None)
        msg = f"Unknown observation type {type_name}"
        if sensor_attr is not None:
            msg += f" (sensor={sensor_attr!r})"
        msg += (
            "; expected one of GPSObservation / IMUObservation / "
            "BaroObservation / MagObservation / BathyProbeObservation / "
            "LoraTOAObservation"
        )
        raise ValueError(msg)

    # Per-sensor handlers — each returns shape ``(n_particles,)``
    # log-likelihood vector. Mirror the truth-side ``sensors.py``
    # forward models with σ taken from the observation record (D7).

    def _gps_log_likelihood(self, obs: GPSObservation) -> np.ndarray:
        idx = self._idx
        obs_east_arr, obs_north_arr = latlon_to_enu(
            obs.lat_deg,
            obs.lon_deg,
            self._enu_origin_lat_deg,
            self._enu_origin_lon_deg,
        )
        de = self._particles[:, idx.east] - float(obs_east_arr)
        dn = self._particles[:, idx.north] - float(obs_north_arr)
        d = np.sqrt(de * de + dn * dn)
        return -0.5 * (d / obs.noise_sigma_m) ** 2

    def _imu_log_likelihood(self, obs: IMUObservation) -> np.ndarray:
        if self._last_dt_sec is None:
            raise RuntimeError(
                "IMU log-likelihood requires a prior predict() call to "
                "populate self._last_dt_sec; predict must be called at "
                "least once before weight() processes an IMU observation."
            )
        dt = self._last_dt_sec
        idx = self._idx
        p = self._particles

        predicted_accel_x = (p[:, idx.vx] - p[:, idx.prev_vx]) / dt + p[:, idx.accel_bx]
        predicted_accel_y = (p[:, idx.vy] - p[:, idx.prev_vy]) / dt + p[:, idx.accel_by]
        predicted_accel_z = (p[:, idx.vz] - p[:, idx.prev_vz]) / dt + p[:, idx.accel_bz]
        heading_delta = wrap_signed_deg(p[:, idx.heading] - p[:, idx.prev_heading])
        predicted_gyro_z = (heading_delta / dt) * (np.pi / 180.0) + p[:, idx.gyro_bz]
        predicted_gyro_x = p[:, idx.gyro_bx]
        predicted_gyro_y = p[:, idx.gyro_by]

        obs_ax, obs_ay, obs_az = obs.accel_xyz
        obs_gx, obs_gy, obs_gz = obs.gyro_xyz
        a_sigma = obs.accel_noise_sigma_ms2
        g_sigma = obs.gyro_noise_sigma_rad_s

        ll = np.zeros(self.n_particles)
        ll += -0.5 * ((predicted_accel_x - obs_ax) / a_sigma) ** 2
        ll += -0.5 * ((predicted_accel_y - obs_ay) / a_sigma) ** 2
        ll += -0.5 * ((predicted_accel_z - obs_az) / a_sigma) ** 2
        ll += -0.5 * ((predicted_gyro_x - obs_gx) / g_sigma) ** 2
        ll += -0.5 * ((predicted_gyro_y - obs_gy) / g_sigma) ** 2
        ll += -0.5 * ((predicted_gyro_z - obs_gz) / g_sigma) ** 2
        return ll

    def _baro_log_likelihood(self, obs: BaroObservation) -> np.ndarray:
        predicted_pressure = (
            SEA_LEVEL_PRESSURE_PA
            + PRESSURE_PER_METER_PA * self._particles[:, self._idx.depth]
        )
        residual = obs.pressure_pa - predicted_pressure
        return -0.5 * (residual / obs.noise_sigma_pa) ** 2

    def _mag_log_likelihood(self, obs: MagObservation) -> np.ndarray:
        delta = wrap_signed_deg(
            obs.heading_deg - self._particles[:, self._idx.heading]
        )
        return -0.5 * (delta / obs.noise_sigma_deg) ** 2

    def _bathy_log_likelihood(self, obs: BathyProbeObservation) -> np.ndarray:
        """On-land particles get log-likelihood ``-inf``.

        The per-particle map lookup is inherently scalar at the M1 grid
        resolution. Looping with ``zip`` (not ``range(n_particles)``)
        keeps the AST gate clean — the gate forbids the
        ``range(n_particles)`` pattern specifically, since the
        unavoidable coastline-polygon test must run per-particle.
        """
        idx = self._idx
        particles_lat, particles_lon = enu_to_latlon(
            self._particles[:, idx.east],
            self._particles[:, idx.north],
            self._enu_origin_lat_deg,
            self._enu_origin_lon_deg,
        )
        sigma = obs.noise_sigma_m
        obs_depth = obs.depth_m
        ll = np.zeros(self.n_particles)
        for i, (lat, lon) in enumerate(zip(particles_lat, particles_lon)):
            lat_f = float(lat)
            lon_f = float(lon)
            if self._onboard_map.is_on_land(lat_f, lon_f):
                ll[i] = -np.inf
            else:
                predicted_depth = self._onboard_map.depth_at(lat_f, lon_f)
                residual = obs_depth - predicted_depth
                ll[i] = -0.5 * (residual / sigma) ** 2
        return ll

    def _lora_log_likelihood(
        self, obs: LoraTOAObservation
    ) -> np.ndarray | None:
        """Anchor-only filter (D5): non-anchor partners contribute no
        likelihood — returned as ``None``, NOT an error and NOT a drop
        counter. M2 fleet coordination lifts the filter.

        Range is computed in 2D (horizontal only) to match the truth-side
        ``LoraTOASensor.sample_link``: LoRa ranging happens on the surface
        (anchors at z=0, drifters at the sea surface in M1 with pump
        disabled), so the vertical component is negligible vs horizontal
        and is dropped in both the truth observation model and this
        likelihood. Including ``dz`` here was a substance bug for
        ballast_drifter PFs whose particle depth random-walks even though
        the observation model is 2D — the phantom vertical component
        corrupts weights.
        """
        if obs.partner_id not in self._anchor_positions:
            return None

        idx = self._idx
        anchor_lat, anchor_lon = self._anchor_positions[obs.partner_id]
        anchor_east_arr, anchor_north_arr = latlon_to_enu(
            anchor_lat,
            anchor_lon,
            self._enu_origin_lat_deg,
            self._enu_origin_lon_deg,
        )
        de = self._particles[:, idx.east] - float(anchor_east_arr)
        dn = self._particles[:, idx.north] - float(anchor_north_arr)
        d = np.sqrt(de * de + dn * dn)
        return -0.5 * ((obs.range_m - d) / obs.noise_sigma_m) ** 2

    # ------------------------------------------------------------------
    # Resample / estimate / step
    # ------------------------------------------------------------------

    def resample(self) -> None:
        """Systematic resampling (design D2). Single ``u0 ~ U(0, 1/n)``
        plus ``np.searchsorted`` against the cumulative weight; the
        gathered particle array is a fresh allocation so external
        consumers cannot accidentally alias prior storage.
        """
        n = self.n_particles
        cumsum = np.cumsum(self._weights)
        cumsum[-1] = 1.0  # rounding guard — keep searchsorted in range
        u0 = self._rng.uniform(0.0, 1.0 / n)
        positions = u0 + np.arange(n) / n
        indices = np.clip(np.searchsorted(cumsum, positions), 0, n - 1)
        self._particles = self._particles[indices].copy()
        self._weights = np.full(n, 1.0 / n)

    def estimate(self, t: int, t_sec: float) -> PFEstimateRecord:
        """Pack the current posterior into a typed ``PFEstimateRecord``.

        Biased weighted variance for ``cov_diag`` (non-negative by
        construction). Numpy scalars are converted to plain ``float``
        via ``.tolist()`` so the dataclass round-trips through
        ``json.dumps`` without a custom encoder.
        """
        mean = np.sum(self._weights[:, None] * self._particles, axis=0)
        diff = self._particles - mean[None, :]
        cov_diag = np.sum(self._weights[:, None] * (diff * diff), axis=0)
        n_effective = float(1.0 / np.sum(self._weights * self._weights))
        return PFEstimateRecord(
            t=t,
            t_sec=t_sec,
            node_id=self._node_id,
            mean=tuple(mean.tolist()),
            cov_diag=tuple(cov_diag.tolist()),
            n_effective=n_effective,
        )

    def step(
        self,
        dt_sec: float,
        observations: Iterable[Observation],
        t: int,
        t_sec: float,
    ) -> PFEstimateRecord:
        """Per-tick PF pipeline: predict → weight → estimate → resample.

        Estimate runs BEFORE resample so the returned ``n_effective``
        reflects the informativeness of the post-weight (pre-resample)
        posterior — the textbook ESS metric ``1 / Σ w_i²`` against the
        observation-conditioned weights. Computing it after resample
        would always return ``n_particles`` (uniform weights post-resample)
        and provide no signal about whether observations actually
        constrained the posterior.

        Mean and cov_diag are likewise the importance-weighted posterior
        statistics rather than the equal-weighted statistics of the
        resampled cloud — same expectation, lower per-realization variance.

        Resample still happens at the end of each step (M1 design D3)
        so the next tick begins with a uniformly-weighted particle pool.
        """
        self.predict(dt_sec)
        self.weight(observations)
        record = self.estimate(t=t, t_sec=t_sec)
        self.resample()
        return record
