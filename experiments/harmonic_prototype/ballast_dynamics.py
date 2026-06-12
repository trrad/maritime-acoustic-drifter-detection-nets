"""Minimal ballast-drifter physics for prototype station-keeping tests.

Models a float-class node that:
  - sits at a depth it tries to reach (depth_setpoint),
  - transitions toward the setpoint at capped vertical speed w_z_max,
  - advects horizontally at each step with the current at the *new*
    actual depth after the vertical move.

First-order pump convergence — no buoyancy-engine settling, no overshoot,
no finite-capacity tank. The goal is to characterise steering authority,
not to simulate a specific float model. w_z_max = 0.1 m/s is a reasonable
default for a mission-scale buoyancy engine (~8 min for a 50 m depth change).

No state_layout integration, no JSONL, no link to rtl/vectors/maritime.
Pure prototype dataclass + step function.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from truth_field import lat_lon_step_from_velocity  # type: ignore[import-not-found]


# (t_sec, lat, lon, depth_m) -> (u_ms, v_ms).
# We pass the depth so the caller can snap to the nearest grid level.
HorizontalCurrentAt = Callable[[float, float, float, float], tuple[float, float]]


@dataclass(frozen=True)
class BallastState:
    lat: float
    lon: float
    depth_m: float
    depth_setpoint_m: float


def step(
    state: BallastState,
    t_sec: float,
    dt_sec: float,
    current_at: HorizontalCurrentAt,
    w_z_max_ms: float = 0.1,
    advection_scale: float = 1.0,
    glide_uv_ms: tuple[float, float] = (0.0, 0.0),
    thrust_uv_ms: tuple[float, float] = (0.0, 0.0),
    n_substeps: int = 10,
) -> BallastState:
    """Advance the ballast state by dt_sec.

    Internally subdivides into `n_substeps` smaller integration steps
    (default 10 → 60 s sub-resolution for the typical dt_sec=600 s
    mission tick). Each substep advances depth toward setpoint by
    `w_z_max * sub_dt` (or arrives), samples current at the
    intermediate (lat, lon, depth, t_midpoint), and advects horizontally.

    The substep model captures three integration regimes the previous
    single-shot model collapsed:
      - **Vertical-shear integration during transit.** A 5 m → 50 m
        switch in one 600 s step traverses 30+ m through the water
        column at w_z_max = 0.1 m/s; horizontal currents at intermediate
        depths can differ from the end depth by several cm/s. The
        single-shot model sampled at end-depth only.
      - **Time evolution within the step.** Tides advance ~0.04 rad
        per minute (M2); 600 s is 0.24 rad. Single-shot sampled at
        t_sec start.
      - **Horizontal field gradient as drifter moves.** A drifter
        advecting at 10 cm/s covers 60 m in 600 s; field gradients
        on km scales matter at 100 m granularity.

    The trajectory predictor in `TrajectoryStationKeeper` calls this
    function to roll forward — predictor IS the dynamics, by
    construction. With perfect knowledge of `current_at` over the
    lookahead, the predictor's "best depth" choice is exactly optimal
    for the executed trajectory.

    Control inputs (all optional; defaults reduce to the pure-ballast case):
      - advection_scale α ∈ [α_min, 1.0]: multiplies ambient current.
        α=1 → node matches local flow; α<1 → passive-drag node slips
        relative to flow.
      - glide_uv_ms: horizontal velocity (u, v) m/s added to motion
        ONLY while depth is actively changing. Models glider wing-
        generated thrust during transit; zero once arrived at setpoint.
      - thrust_uv_ms: unconditional thrust vector. Not used in the
        passive/glider work but retained for completeness.

    NaN-safety: if `current_at` returns NaN at any substep, horizontal
    advection for that substep is skipped (the node can't pretend to
    move through water it doesn't know about); depth still advances.
    Subsequent substeps are attempted normally.
    """
    if n_substeps < 1:
        raise ValueError(f"n_substeps must be ≥ 1, got {n_substeps}")
    sub_dt = dt_sec / n_substeps
    cur = state
    for k in range(n_substeps):
        t_sub = t_sec + (k + 0.5) * sub_dt   # midpoint of this substep
        cur = _step_atomic(
            cur, t_sub, sub_dt, current_at, w_z_max_ms,
            advection_scale, glide_uv_ms, thrust_uv_ms,
        )
    return cur


def _step_atomic(
    state: BallastState,
    t_sec: float,
    dt_sec: float,
    current_at: HorizontalCurrentAt,
    w_z_max_ms: float,
    advection_scale: float,
    glide_uv_ms: tuple[float, float],
    thrust_uv_ms: tuple[float, float],
) -> BallastState:
    """One physically-faithful sub-resolution integration step.

    Caller is expected to size dt_sec small enough that the linearised
    update — depth advances toward setpoint, then horizontal current
    sampled at end depth + start position + given t_sec, applied for
    full dt_sec — is a reasonable approximation. `step()` does this
    by subdividing.
    """
    dz_desired = state.depth_setpoint_m - state.depth_m
    dz_max = w_z_max_ms * dt_sec
    if dz_max <= 0:
        new_depth = state.depth_m
        transition_frac = 0.0
    elif abs(dz_desired) <= dz_max:
        new_depth = state.depth_setpoint_m
        transition_frac = abs(dz_desired) / dz_max
    else:
        new_depth = state.depth_m + (dz_max if dz_desired > 0 else -dz_max)
        transition_frac = 1.0

    u, v = current_at(t_sec, state.lat, state.lon, new_depth)
    if u != u or v != v:
        return replace(state, depth_m=new_depth)

    u_scaled = u * advection_scale
    v_scaled = v * advection_scale
    u_net = (u_scaled
             + glide_uv_ms[0] * transition_frac
             + thrust_uv_ms[0])
    v_net = (v_scaled
             + glide_uv_ms[1] * transition_frac
             + thrust_uv_ms[1])
    dlat, dlon = lat_lon_step_from_velocity(u_net, v_net, state.lat, dt_sec)
    return BallastState(
        lat=state.lat + dlat,
        lon=state.lon + dlon,
        depth_m=new_depth,
        depth_setpoint_m=state.depth_setpoint_m,
    )


def set_setpoint(state: BallastState, new_setpoint_m: float) -> BallastState:
    """Return a new state with only the depth setpoint changed."""
    return replace(state, depth_setpoint_m=new_setpoint_m)
