"""Maritime fleet data structures and factory functions.

Provides the composed Node type, blueprint factory functions for M1 node types,
and utility helpers for component capability queries.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace as dc_replace
from typing import cast

import numpy as np

from rtl.vectors.maritime.platform_profile import (
    BallastDriftingPoseSpec,
    BallastSpec,
    DriftingSurfacePoseSpec,
    MooredPoseSpec,
    SatelliteUplinkSpec,
    NodeProfile,
    BALLAST_DRIFTER_PROFILE,
    PURE_DRIFTER_PROFILE,
    make_anchor_profile,
)
from rtl.vectors.maritime.state_layout import (
    StateLayout,
    ANCHOR_LAYOUT,
    BALLAST_DRIFTER_LAYOUT,
    PURE_DRIFTER_LAYOUT,
)
from rtl.vectors.maritime.coords import haversine_m
from rtl.vectors.maritime.clock import Clock, ClockSpec


KIND_BALLAST_PUMP = "ballast_pump"
KIND_MOORED_POSE = "moored_pose"
KIND_DRIFTING_SURFACE_POSE = "drifting_surface_pose"
KIND_BALLAST_DRIFTING_POSE = "ballast_drifting_pose"
KIND_SATELLITE_UPLINK = "satellite_uplink"
KIND_CLOCK = "clock"


@dataclass(frozen=True)
class Node:
    """Composed maritime platform node.

    Aggregates a profile (capabilities), layout (state structure), current state,
    and runtime components into a single immutable container.
    """

    node_id: str
    profile: NodeProfile
    layout: StateLayout
    state: np.ndarray
    components: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.state.shape != (self.layout.state_dim,):
            raise ValueError(
                f"State shape {self.state.shape} does not match layout state_dim {self.layout.state_dim}"
            )
        if self.profile.state_dim != self.layout.state_dim:
            raise ValueError(
                f"Profile state_dim {self.profile.state_dim} does not match layout state_dim {self.layout.state_dim}"
            )
        if not np.all(np.isfinite(self.state)):
            raise ValueError("State contains NaN or infinite values")
        declared_kinds = {spec.kind for spec in self.profile.components}
        for key in self.components:
            if key not in declared_kinds:
                raise ValueError(
                    f"Component kind '{key}' not declared in profile components"
                )


def has_pump(node: Node) -> bool:
    return KIND_BALLAST_PUMP in node.components


def is_moored(node: Node) -> bool:
    return KIND_MOORED_POSE in node.components


def has_satellite_uplink(node: Node) -> bool:
    return KIND_SATELLITE_UPLINK in node.components


def _build_runtime_components(profile: NodeProfile) -> dict[str, object]:
    return {spec.kind: spec for spec in profile.components}


def _require_component(profile: NodeProfile, kind: str) -> None:
    if kind not in {spec.kind for spec in profile.components}:
        raise ValueError(f"Profile missing required component: {kind}")


def _forbid_component(profile: NodeProfile, kind: str) -> None:
    if kind in {spec.kind for spec in profile.components}:
        raise ValueError(f"Profile must not contain component: {kind}")


def make_anchor(profile: NodeProfile, initial_state: np.ndarray, rng: np.random.Generator) -> Node:
    _require_component(profile, KIND_MOORED_POSE)
    _require_component(profile, KIND_SATELLITE_UPLINK)
    _require_component(profile, KIND_CLOCK)
    components = _build_runtime_components(profile)
    components[KIND_CLOCK] = Clock(spec=cast(ClockSpec, profile.component(KIND_CLOCK)))
    node_id = f"{profile.class_name}_{rng.integers(0, 2**32):08x}"
    return Node(
        node_id=node_id,
        profile=profile,
        layout=ANCHOR_LAYOUT,
        state=initial_state,
        components=components,
    )


def make_ballast_drifter(profile: NodeProfile, initial_state: np.ndarray, rng: np.random.Generator) -> Node:
    _forbid_component(profile, KIND_MOORED_POSE)
    _forbid_component(profile, KIND_SATELLITE_UPLINK)
    _require_component(profile, KIND_BALLAST_DRIFTING_POSE)
    _require_component(profile, KIND_BALLAST_PUMP)
    _require_component(profile, KIND_CLOCK)
    components = _build_runtime_components(profile)
    components[KIND_CLOCK] = Clock(spec=cast(ClockSpec, profile.component(KIND_CLOCK)))
    node_id = f"{profile.class_name}_{rng.integers(0, 2**32):08x}"
    return Node(
        node_id=node_id,
        profile=profile,
        layout=BALLAST_DRIFTER_LAYOUT,
        state=initial_state,
        components=components,
    )


def make_pure_drifter(profile: NodeProfile, initial_state: np.ndarray, rng: np.random.Generator) -> Node:
    _forbid_component(profile, KIND_BALLAST_PUMP)
    _forbid_component(profile, KIND_MOORED_POSE)
    _forbid_component(profile, KIND_SATELLITE_UPLINK)
    _require_component(profile, KIND_DRIFTING_SURFACE_POSE)
    _require_component(profile, KIND_CLOCK)
    components = _build_runtime_components(profile)
    components[KIND_CLOCK] = Clock(spec=cast(ClockSpec, profile.component(KIND_CLOCK)))
    node_id = f"{profile.class_name}_{rng.integers(0, 2**32):08x}"
    return Node(
        node_id=node_id,
        profile=profile,
        layout=PURE_DRIFTER_LAYOUT,
        state=initial_state,
        components=components,
    )


def _random_position_inside(rng: np.random.Generator, east_range: float, north_range: float) -> np.ndarray:
    margin = 0.001
    east = rng.uniform(margin, east_range - margin)
    north = rng.uniform(margin, north_range - margin)
    return np.array([east, north, 0.0])


def _build_initial_state(layout: StateLayout, position: np.ndarray) -> np.ndarray:
    state = np.zeros(layout.state_dim)
    pos_slice = layout.slice("position")
    state[pos_slice] = position
    vel_slice = layout.slice("velocity")
    prev_vel_slice = layout.slice("prev_velocity")
    state[prev_vel_slice] = state[vel_slice]
    heading_slice = layout.slice("heading")
    prev_heading_slice = layout.slice("prev_heading")
    state[prev_heading_slice] = state[heading_slice]
    return state


def _apply_cadence_overrides(
    profile: NodeProfile,
    lora_period_sec: float | None,
    gps_period_sec: float | None,
) -> NodeProfile:
    """Return a clone of ``profile`` with LoRa and/or GPS cadences overridden.

    When both args are ``None``, returns the input unchanged. Otherwise
    replaces ``profile.comms.tdma_period_sec`` (if LoRa overridden) and the
    matching sensor's ``max_rate_hz`` for ``lora_toa`` / ``gps`` (if their
    cadences are overridden). Any sensors not named ``lora_toa`` or ``gps``
    pass through untouched.
    """
    if lora_period_sec is None and gps_period_sec is None:
        return profile

    new_comms = profile.comms
    if lora_period_sec is not None:
        new_comms = dc_replace(profile.comms, tdma_period_sec=float(lora_period_sec))

    new_sensors = []
    for spec in profile.sensors:
        if spec.name == "lora_toa" and lora_period_sec is not None:
            new_sensors.append(dc_replace(spec, max_rate_hz=1.0 / float(lora_period_sec)))
        elif spec.name == "gps" and gps_period_sec is not None:
            new_sensors.append(dc_replace(spec, max_rate_hz=1.0 / float(gps_period_sec)))
        else:
            new_sensors.append(spec)

    return dc_replace(profile, comms=new_comms, sensors=tuple(new_sensors))


def make_m1_fleet(
    seed: int,
    bbox: tuple[float, float, float, float],
    *,
    lora_period_sec: float | None = None,
    gps_period_sec: float | None = None,
) -> tuple[Node, ...]:
    """Build the M1 fleet.

    Optional keyword overrides ``lora_period_sec`` and ``gps_period_sec`` clone
    the bundled profiles with the new cadences before constructing each node.
    Defaults (``None``) preserve the bundled M1 profile values exactly.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    rng = np.random.default_rng(seed)

    east_range = haversine_m(min_lat, min_lon, min_lat, max_lon)
    north_range = haversine_m(min_lat, min_lon, max_lat, min_lon)

    anchor_enu_positions = [
        np.array([0.0, 0.0, 0.0]),
        np.array([east_range, north_range, 0.0]),
    ]
    anchor_lat_lons = [
        (min_lat, min_lon),
        (max_lat, max_lon),
    ]

    nodes = []

    for enu_pos, (anchor_lat, anchor_lon) in zip(anchor_enu_positions, anchor_lat_lons):
        anchor_profile = make_anchor_profile(
            anchor_lat_deg=anchor_lat,
            anchor_lon_deg=anchor_lon,
            anchor_depth_m=0.0,
        )
        anchor_profile = _apply_cadence_overrides(
            anchor_profile, lora_period_sec, gps_period_sec
        )
        initial_state = _build_initial_state(ANCHOR_LAYOUT, enu_pos)
        node = make_anchor(anchor_profile, initial_state, rng)
        nodes.append(node)

    ballast_profile = _apply_cadence_overrides(
        BALLAST_DRIFTER_PROFILE, lora_period_sec, gps_period_sec
    )
    pure_profile = _apply_cadence_overrides(
        PURE_DRIFTER_PROFILE, lora_period_sec, gps_period_sec
    )

    for _ in range(4):
        pos = _random_position_inside(rng, east_range, north_range)
        initial_state = _build_initial_state(BALLAST_DRIFTER_LAYOUT, pos)
        node = make_ballast_drifter(ballast_profile, initial_state, rng)
        nodes.append(node)

    for _ in range(4):
        pos = _random_position_inside(rng, east_range, north_range)
        initial_state = _build_initial_state(PURE_DRIFTER_LAYOUT, pos)
        node = make_pure_drifter(pure_profile, initial_state, rng)
        nodes.append(node)

    return tuple(nodes)


__all__ = [
    "MooredPoseSpec",
    "DriftingSurfacePoseSpec",
    "BallastDriftingPoseSpec",
    "BallastSpec",
    "SatelliteUplinkSpec",
    "Node",
    "make_anchor",
    "make_ballast_drifter",
    "make_pure_drifter",
    "make_m1_fleet",
    "has_pump",
    "is_moored",
    "has_satellite_uplink",
    "KIND_BALLAST_PUMP",
    "KIND_MOORED_POSE",
    "KIND_DRIFTING_SURFACE_POSE",
    "KIND_BALLAST_DRIFTING_POSE",
    "KIND_SATELLITE_UPLINK",
    "KIND_CLOCK",
]
