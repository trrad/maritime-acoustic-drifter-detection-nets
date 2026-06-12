"""State field and layout definitions for maritime platform state vectors.

Provides dataclasses for describing individual state fields and the complete
layout of a platform's state vector, including named field groups.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StateField:
    """Description of a single field in a state vector.

    Provides name, unit, and description metadata for a state dimension.
    """

    name: str
    unit: str
    description: str


@dataclass(frozen=True, slots=True)
class StateLayout:
    """Complete layout description for a platform's state vector.

    Defines the fields in the state vector, their order, and named groups
    of consecutive fields (slices into the state vector).
    """

    class_name: str
    fields: tuple[StateField, ...]
    groups: Mapping[str, slice]

    def __post_init__(self) -> None:
        field_names = [field.name for field in self.fields]
        unique_names = set(field_names)
        if len(field_names) != len(unique_names):
            duplicates = [name for name in unique_names if field_names.count(name) > 1]
            raise ValueError(f"Duplicate field names: {', '.join(duplicates)}")

        dim = self.state_dim
        for group_name, group_slice in self.groups.items():
            start = group_slice.start if group_slice.start is not None else 0
            stop = group_slice.stop if group_slice.stop is not None else dim
            if start < 0 or stop > dim or start > stop:
                raise ValueError(
                    f"Group '{group_name}' slice {group_slice} is outside state range [0, {dim})"
                )

    @property
    def state_dim(self) -> int:
        return len(self.fields)

    def index_of(self, field_name: str) -> int:
        for i, field in enumerate(self.fields):
            if field.name == field_name:
                return i
        raise KeyError(f"Field '{field_name}' not found in layout")

    def name_at(self, index: int) -> str:
        if index < 0 or index >= self.state_dim:
            raise IndexError(f"Index {index} out of range [0, {self.state_dim})")
        return self.fields[index].name

    def slice(self, group_name: str) -> slice:
        if group_name not in self.groups:
            raise KeyError(f"Group '{group_name}' not found in layout")
        return self.groups[group_name]


def _pure_drifter_fields() -> tuple[StateField, ...]:
    pos = tuple(StateField(n, u, d) for n, u, d in [
        ("east_m", "m", "east position"), ("north_m", "m", "north position"), ("depth_m", "m", "depth"),
    ])
    vel = tuple(StateField(n, u, d) for n, u, d in [
        ("vx_ms", "m/s", "east velocity"), ("vy_ms", "m/s", "north velocity"), ("vz_ms", "m/s", "vertical velocity"),
    ])
    heading = (StateField("heading_deg", "deg", "compass heading"),)
    surf = tuple(StateField(n, u, d) for n, u, d in [
        ("cur_vx_ms", "m/s", "surface current east"), ("cur_vy_ms", "m/s", "surface current north"),
    ])
    imu = tuple(StateField(n, u, d) for n, u, d in [
        ("gyro_bx_deg_s", "deg/s", "gyro x bias"), ("gyro_by_deg_s", "deg/s", "gyro y bias"), ("gyro_bz_deg_s", "deg/s", "gyro z bias"),
        ("accel_bx_ms2", "m/s^2", "accel x bias"), ("accel_by_ms2", "m/s^2", "accel y bias"), ("accel_bz_ms2", "m/s^2", "accel z bias"),
    ])
    prev_vel = tuple(StateField(n, u, d) for n, u, d in [
        ("prev_vx_ms", "m/s", "east velocity at prior tick"),
        ("prev_vy_ms", "m/s", "north velocity at prior tick"),
        ("prev_vz_ms", "m/s", "vertical velocity at prior tick"),
    ])
    prev_heading = (StateField("prev_heading_deg", "deg", "compass heading at prior tick"),)
    return pos + vel + heading + surf + imu + prev_vel + prev_heading


def _ballast_drifter_fields() -> tuple[StateField, ...]:
    base = _pure_drifter_fields()
    deep = tuple(StateField(n, u, d) for n, u, d in [
        ("deep_vx_ms", "m/s", "deep current east"), ("deep_vy_ms", "m/s", "deep current north"),
    ])
    return base + deep


def _anchor_fields() -> tuple[StateField, ...]:
    return _ballast_drifter_fields()


PURE_DRIFTER_LAYOUT = StateLayout(
    class_name="pure_drifter",
    fields=_pure_drifter_fields(),
    groups={
        "position": slice(0, 3),
        "velocity": slice(3, 6),
        "heading": slice(6, 7),
        "surface_current": slice(7, 9),
        "imu_bias": slice(9, 15),
        "prev_velocity": slice(15, 18),
        "prev_heading": slice(18, 19),
    },
)

BALLAST_DRIFTER_LAYOUT = StateLayout(
    class_name="ballast_drifter",
    fields=_ballast_drifter_fields(),
    groups={
        "position": slice(0, 3),
        "velocity": slice(3, 6),
        "heading": slice(6, 7),
        "surface_current": slice(7, 9),
        "imu_bias": slice(9, 15),
        "prev_velocity": slice(15, 18),
        "prev_heading": slice(18, 19),
        "deep_current": slice(19, 21),
    },
)

ANCHOR_LAYOUT = StateLayout(
    class_name="anchor",
    fields=_anchor_fields(),
    groups={
        "position": slice(0, 3),
        "velocity": slice(3, 6),
        "heading": slice(6, 7),
        "surface_current": slice(7, 9),
        "imu_bias": slice(9, 15),
        "prev_velocity": slice(15, 18),
        "prev_heading": slice(18, 19),
        "deep_current": slice(19, 21),
    },
)

ALL_M1_LAYOUTS = (PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT)
