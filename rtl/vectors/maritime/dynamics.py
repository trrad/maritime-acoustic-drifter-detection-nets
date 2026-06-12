"""Truth propagation dynamics for maritime platforms.

Implements the 4-phase physics update for M1: pump, pose, imu_biases, clock.
"""

from dataclasses import dataclass
from typing import cast

import numpy as np

from rtl.vectors.maritime.fleet import Node
from rtl.vectors.maritime.current_fields import CurrentField
from rtl.vectors.maritime.clock import Clock
from rtl.vectors.maritime.coords import enu_to_latlon


KIND_BALLAST_PUMP = "ballast_pump"
KIND_MOORED_POSE = "moored_pose"
KIND_DRIFTING_SURFACE_POSE = "drifting_surface_pose"
KIND_BALLAST_DRIFTING_POSE = "ballast_drifting_pose"
KIND_CLOCK = "clock"


POS_PROCESS_NOISE_M_PER_SQRT_S = 0.01
HEADING_PROCESS_NOISE_DEG_PER_SQRT_S = 0.1
GYRO_BIAS_PROCESS_NOISE_DEG_S_PER_SQRT_S = 0.0001
ACCEL_BIAS_PROCESS_NOISE_MS2_PER_SQRT_S = 0.001

# Standard deviation (m/s) of the passive-drifter velocity residual
# drawn independently each tick. Replaces the retired random-walk scale
# ``VEL_PROCESS_NOISE_MS_PER_SQRT_S``. Per-tick sampling semantic: each
# tick's residual is an independent ``N(0, DRIFTER_VEL_PERTURBATION_MS)``
# sample with no accumulation across ticks, so the residual stays
# bounded by ``3 * DRIFTER_VEL_PERTURBATION_MS`` indefinitely. Scale is
# picked so the residual envelope (~0.06 m/s at 3σ) is small compared
# to typical current magnitudes (0.1-0.5 m/s); see design doc D2.
DRIFTER_VEL_PERTURBATION_MS: float = 0.02


@dataclass(frozen=True, slots=True)
class PhysicsEnv:
    current_field: CurrentField
    t_sec: float
    enu_origin_lat_deg: float = 0.0
    enu_origin_lon_deg: float = 0.0


def propagate_truth(node: Node, dt_sec: float, env: PhysicsEnv, rng: np.random.Generator) -> np.ndarray:
    new_state = node.state.copy()
    sqrt_dt = np.sqrt(dt_sec)

    prev_velocity_slice = node.layout.slice("prev_velocity")
    velocity_slice = node.layout.slice("velocity")
    prev_heading_slice = node.layout.slice("prev_heading")
    heading_slice = node.layout.slice("heading")
    new_state[prev_velocity_slice] = new_state[velocity_slice]
    new_state[prev_heading_slice] = new_state[heading_slice]

    east_m = float(new_state[0])
    north_m = float(new_state[1])
    node_lat_array, node_lon_array = enu_to_latlon(
        east_m, north_m, env.enu_origin_lat_deg, env.enu_origin_lon_deg
    )
    node_lat_deg = float(node_lat_array)
    node_lon_deg = float(node_lon_array)

    current_vx, current_vy = env.current_field.velocity_at(node_lat_deg, node_lon_deg, env.t_sec)

    surface_current_slice = node.layout.slice("surface_current")
    new_state[surface_current_slice] = np.array([current_vx, current_vy])

    if KIND_BALLAST_PUMP in node.components:
        pass

    if KIND_MOORED_POSE in node.components:
        heading_idx = 6
        heading_noise = rng.normal(0.0, HEADING_PROCESS_NOISE_DEG_PER_SQRT_S * sqrt_dt)
        new_state[heading_idx] = (new_state[heading_idx] + heading_noise) % 360.0

    elif KIND_DRIFTING_SURFACE_POSE in node.components:
        pos_noise = rng.normal(0.0, POS_PROCESS_NOISE_M_PER_SQRT_S * sqrt_dt, size=3)

        # Position uses *last tick's* residual (new_state[3:5] still
        # holds pre-tick values at this point); the new residual is
        # sampled AFTER advection below. Position formula is unchanged
        # from the retired RW model — state semantics preserved.
        new_state[0] += (new_state[3] + current_vx) * dt_sec + pos_noise[0]
        new_state[1] += (new_state[4] + current_vy) * dt_sec + pos_noise[1]
        new_state[2] = 0.0

        # Per-tick sampling: residual is an independent draw each tick,
        # not an RW increment. See ``DRIFTER_VEL_PERTURBATION_MS`` and
        # design doc D1/D2 (``maritime-velocity-model``). vz (index 5)
        # is intentionally left untouched — M1 drifters pin depth
        # (pure: state[2]=0 above; ballast: pump is pass) so vz has no
        # physical role and must not accumulate tick-uncorrelated noise
        # (design D4/D6: state-dim preserved but not evolved).
        new_state[3:5] = rng.normal(0.0, DRIFTER_VEL_PERTURBATION_MS, size=2)

        heading_idx = 6
        heading_noise = rng.normal(0.0, HEADING_PROCESS_NOISE_DEG_PER_SQRT_S * sqrt_dt)
        new_state[heading_idx] = (new_state[heading_idx] + heading_noise) % 360.0

    elif KIND_BALLAST_DRIFTING_POSE in node.components:
        pos_noise = rng.normal(0.0, POS_PROCESS_NOISE_M_PER_SQRT_S * sqrt_dt, size=3)

        new_state[0] += (new_state[3] + current_vx) * dt_sec + pos_noise[0]
        new_state[1] += (new_state[4] + current_vy) * dt_sec + pos_noise[1]

        # Per-tick sampling: see KIND_DRIFTING_SURFACE_POSE branch above.
        new_state[3:5] = rng.normal(0.0, DRIFTER_VEL_PERTURBATION_MS, size=2)

        heading_idx = 6
        heading_noise = rng.normal(0.0, HEADING_PROCESS_NOISE_DEG_PER_SQRT_S * sqrt_dt)
        new_state[heading_idx] = (new_state[heading_idx] + heading_noise) % 360.0

    gyro_bias_noise = rng.normal(0.0, GYRO_BIAS_PROCESS_NOISE_DEG_S_PER_SQRT_S * sqrt_dt, size=3)
    accel_bias_noise = rng.normal(0.0, ACCEL_BIAS_PROCESS_NOISE_MS2_PER_SQRT_S * sqrt_dt, size=3)

    new_state[9:12] += gyro_bias_noise
    new_state[12:15] += accel_bias_noise

    if KIND_CLOCK in node.components:
        cast(Clock, node.components["clock"]).advance(dt_sec)

    return new_state
