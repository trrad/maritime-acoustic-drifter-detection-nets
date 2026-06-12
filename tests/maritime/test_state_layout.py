"""Contract tests for state_layout module.

Tests for StateField and StateLayout dataclasses.
"""

import dataclasses

import pytest


def _make_field(name: str = "test_field", unit: str = "m", description: str = "test field"):
    """Create a valid StateField for use in tests."""
    from rtl.vectors.maritime.state_layout import StateField

    return StateField(
        name=name,
        unit=unit,
        description=description
    )


def _make_fields(n: int = 15) -> tuple:
    """Create a tuple of n valid StateField instances for use in tests."""
    from rtl.vectors.maritime.state_layout import StateField

    return tuple(
        StateField(name=f"field_{i}", unit="m", description=f"field {i}")
        for i in range(n)
    )


def _make_layout(n: int = 15, groups: dict | None = None):
    """Create a valid StateLayout for use in tests."""
    from rtl.vectors.maritime.state_layout import StateLayout

    fields = _make_fields(n)
    if groups is None:
        if n == 15:
            groups = {
                "group1": slice(0, 5),
                "group2": slice(5, 10),
                "group3": slice(10, 15),
            }
        elif n == 5:
            groups = {
                "group1": slice(0, 2),
                "group2": slice(2, 5),
            }
        else:
            groups = {}

    return StateLayout(
        class_name="test_node",
        fields=fields,
        groups=groups
    )


class TestStateField:
    """Tests for StateField dataclass."""

    def test_valid_construction_round_trip(self) -> None:
        """Construct with valid parameters and verify all field values.

        Assert all field accesses return the provided values.
        """
        from rtl.vectors.maritime.state_layout import StateField

        field = StateField(
            name="position_x",
            unit="m",
            description="X position in easting"
        )

        assert field.name == "position_x"
        assert field.unit == "m"
        assert field.description == "X position in easting"

    def test_immutable(self) -> None:
        """Attempting to mutate any field raises FrozenInstanceError."""
        from rtl.vectors.maritime.state_layout import StateField

        field = StateField(
            name="position_x",
            unit="m",
            description="X position in easting"
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            field.name = "position_y"  # type: ignore[misc]

    def test_hashability(self) -> None:
        """Two StateField values with identical fields compare equal and share a hash.

        Can be used in a set or dict.
        """
        from rtl.vectors.maritime.state_layout import StateField

        field1 = StateField(
            name="position_x",
            unit="m",
            description="X position in easting"
        )
        field2 = StateField(
            name="position_x",
            unit="m",
            description="X position in easting"
        )
        field3 = StateField(
            name="position_y",
            unit="m",
            description="Y position in northing"
        )

        assert field1 == field2
        assert field1 != field3
        assert hash(field1) == hash(field2)

        field_set = {field1, field2, field3}
        assert len(field_set) == 2

        field_dict = {field1: "value1", field3: "value3"}
        assert field_dict[field2] == "value1"


class TestStateLayout:
    """Tests for StateLayout dataclass."""

    def test_state_dim_equals_field_count(self) -> None:
        """state_dim equals the length of the fields tuple."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        assert layout.state_dim == 15

        layout_5 = _make_layout(n=5)
        assert layout_5.state_dim == 5

    def test_duplicate_field_names_rejected(self) -> None:
        """Constructing with duplicate field names raises ValueError.

        The error message must contain the duplicated name.
        """
        from rtl.vectors.maritime.state_layout import StateField, StateLayout

        field1 = StateField(name="position", unit="m", description="Position")
        field2 = StateField(name="velocity", unit="m/s", description="Velocity")
        field3 = StateField(name="position", unit="m", description="Duplicate position")

        with pytest.raises(ValueError) as exc_info:
            StateLayout(
                class_name="test_node",
                fields=(field1, field2, field3),
                groups={}
            )

        assert "position" in str(exc_info.value)

    def test_group_slice_outside_state_range_rejected(self) -> None:
        """Constructing with group slice outside state range raises ValueError."""
        from rtl.vectors.maritime.state_layout import StateLayout

        fields = _make_fields(n=15)

        with pytest.raises(ValueError) as exc_info:
            StateLayout(
                class_name="test_node",
                fields=fields,
                groups={
                    "group1": slice(0, 5),
                    "group2": slice(5, 10),
                    "group3": slice(12, 20),
                }
            )

        assert "slice" in str(exc_info.value).lower() or "20" in str(exc_info.value)

        with pytest.raises(ValueError):
            StateLayout(
                class_name="test_node",
                fields=fields,
                groups={
                    "group1": slice(-5, 5),
                }
            )

    def test_empty_group_slice_allowed(self) -> None:
        """Group slice with start == stop (empty slice) is allowed."""
        from rtl.vectors.maritime.state_layout import StateLayout

        fields = _make_fields(n=15)

        layout = StateLayout(
            class_name="test_node",
            fields=fields,
            groups={
                "empty_group": slice(0, 0),
                "group1": slice(0, 5),
            }
        )

        assert layout.slice("empty_group") == slice(0, 0)

    def test_index_of_returns_correct_position(self) -> None:
        """index_of returns correct position for field name."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        assert layout.index_of("field_0") == 0
        assert layout.index_of("field_7") == 7
        assert layout.index_of("field_14") == 14

    def test_index_of_raises_keyerror_for_unknown_name(self) -> None:
        """index_of raises KeyError for unknown field names."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        with pytest.raises(KeyError) as exc_info:
            layout.index_of("unknown_field")

        assert "unknown_field" in str(exc_info.value)

    def test_name_at_returns_correct_name(self) -> None:
        """name_at returns correct name for valid index."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        assert layout.name_at(0) == "field_0"
        assert layout.name_at(7) == "field_7"
        assert layout.name_at(14) == "field_14"

    def test_name_at_raises_indexerror_out_of_range(self) -> None:
        """name_at raises IndexError for out-of-range indices."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        with pytest.raises(IndexError):
            layout.name_at(-1)

        with pytest.raises(IndexError):
            layout.name_at(15)

        with pytest.raises(IndexError):
            layout.name_at(100)

    def test_slice_returns_correct_slice_object(self) -> None:
        """slice returns correct slice object for group name."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(
            n=15,
            groups={
                "position": slice(0, 3),
                "velocity": slice(3, 6),
                "acceleration": slice(6, 9),
            }
        )

        assert layout.slice("position") == slice(0, 3)
        assert layout.slice("velocity") == slice(3, 6)
        assert layout.slice("acceleration") == slice(6, 9)

    def test_slice_raises_keyerror_for_unknown_group(self) -> None:
        """slice raises KeyError for unknown group names."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        with pytest.raises(KeyError) as exc_info:
            layout.slice("unknown_group")

        assert "unknown_group" in str(exc_info.value)

    def test_immutable(self) -> None:
        """Attempting to mutate any field raises FrozenInstanceError."""
        from rtl.vectors.maritime.state_layout import StateLayout

        layout = _make_layout(n=15)

        with pytest.raises(dataclasses.FrozenInstanceError):
            layout.class_name = "other_node"  # type: ignore[misc]


class TestBundledM1Layouts:
    """Tests for bundled M1 platform state layout constants."""

    def test_layout_dimensions_match_design(self) -> None:
        """state_dim matches truth layout: pure drifter 19, ballast drifter 21 (adds deep_current), anchor 21 (same as ballast; neighbor_range moved to PF-estimate schema)."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        assert PURE_DRIFTER_LAYOUT.state_dim == 19
        assert BALLAST_DRIFTER_LAYOUT.state_dim == 21
        assert ANCHOR_LAYOUT.state_dim == 21

    def test_position_group_always_at_slice_0_3(self) -> None:
        """Position group is at slice(0, 3) across all bundled layouts."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        assert PURE_DRIFTER_LAYOUT.slice("position") == slice(0, 3)
        assert BALLAST_DRIFTER_LAYOUT.slice("position") == slice(0, 3)
        assert ANCHOR_LAYOUT.slice("position") == slice(0, 3)

    def test_heading_field_always_at_index_6(self) -> None:
        """Heading field is at index 6 across all bundled layouts."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        assert PURE_DRIFTER_LAYOUT.index_of("heading_deg") == 6
        assert BALLAST_DRIFTER_LAYOUT.index_of("heading_deg") == 6
        assert ANCHOR_LAYOUT.index_of("heading_deg") == 6

    def test_imu_bias_group_has_6_fields_in_all_layouts(self) -> None:
        """IMU bias group contains 6 fields across all bundled layouts."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        imu_bias_slice_pure = PURE_DRIFTER_LAYOUT.slice("imu_bias")
        imu_bias_slice_ballast = BALLAST_DRIFTER_LAYOUT.slice("imu_bias")
        imu_bias_slice_anchor = ANCHOR_LAYOUT.slice("imu_bias")

        assert imu_bias_slice_pure.stop - imu_bias_slice_pure.start == 6
        assert imu_bias_slice_ballast.stop - imu_bias_slice_ballast.start == 6
        assert imu_bias_slice_anchor.stop - imu_bias_slice_anchor.start == 6

    def test_deep_current_group_present_in_ballast_not_pure(self) -> None:
        """Ballast drifter has deep_current group; pure drifter raises KeyError."""
        from rtl.vectors.maritime.state_layout import (
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        deep_current_slice = BALLAST_DRIFTER_LAYOUT.slice("deep_current")
        assert deep_current_slice.stop - deep_current_slice.start == 2

        with pytest.raises(KeyError):
            PURE_DRIFTER_LAYOUT.slice("deep_current")

    def test_no_neighbor_range_in_truth_state(self) -> None:
        """The truth state layout does not carry neighbor_range. Truth ranges are deterministic functions of truth positions, computed directly by truth consumers (ScenarioTruthReader); they are not a separate state dimension. Node-side range estimates live in the PF-estimate schema and are fed by noisy LoraTOASensor observations — a distinct observation path."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        for layout in (PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT):
            with pytest.raises(KeyError):
                layout.slice("neighbor_range")

    def test_prev_velocity_group_three_slots_in_all_layouts(self) -> None:
        """Every bundled layout has a prev_velocity slice of length 3, at slice(15, 18)."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        for layout in (PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT):
            prev_vel_slice = layout.slice("prev_velocity")
            assert prev_vel_slice == slice(15, 18)

    def test_prev_heading_group_one_slot_in_all_layouts(self) -> None:
        """Every bundled layout has a prev_heading slice of length 1, at slice(18, 19)."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        for layout in (PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT):
            prev_heading_slice = layout.slice("prev_heading")
            assert prev_heading_slice == slice(18, 19)

    def test_unit_conventions_match_design(self) -> None:
        """Units match design for all field types."""
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        for layout in (PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT):
            assert layout.fields[layout.index_of("east_m")].unit == "m"
            assert layout.fields[layout.index_of("north_m")].unit == "m"
            assert layout.fields[layout.index_of("depth_m")].unit == "m"

            assert layout.fields[layout.index_of("vx_ms")].unit == "m/s"
            assert layout.fields[layout.index_of("vy_ms")].unit == "m/s"
            assert layout.fields[layout.index_of("vz_ms")].unit == "m/s"

            assert layout.fields[layout.index_of("heading_deg")].unit == "deg"

            assert layout.fields[layout.index_of("cur_vx_ms")].unit == "m/s"
            assert layout.fields[layout.index_of("cur_vy_ms")].unit == "m/s"

            assert layout.fields[layout.index_of("gyro_bx_deg_s")].unit == "deg/s"
            assert layout.fields[layout.index_of("gyro_by_deg_s")].unit == "deg/s"
            assert layout.fields[layout.index_of("gyro_bz_deg_s")].unit == "deg/s"

            assert layout.fields[layout.index_of("accel_bx_ms2")].unit == "m/s^2"
            assert layout.fields[layout.index_of("accel_by_ms2")].unit == "m/s^2"
            assert layout.fields[layout.index_of("accel_bz_ms2")].unit == "m/s^2"

        assert BALLAST_DRIFTER_LAYOUT.fields[BALLAST_DRIFTER_LAYOUT.index_of("deep_vx_ms")].unit == "m/s"
        assert BALLAST_DRIFTER_LAYOUT.fields[BALLAST_DRIFTER_LAYOUT.index_of("deep_vy_ms")].unit == "m/s"

        assert ANCHOR_LAYOUT.fields[ANCHOR_LAYOUT.index_of("deep_vx_ms")].unit == "m/s"
        assert ANCHOR_LAYOUT.fields[ANCHOR_LAYOUT.index_of("deep_vy_ms")].unit == "m/s"

        # Neighbor range fields intentionally removed from truth state. Truth
        # range between two nodes is a deterministic function of their truth
        # positions and is computed directly by truth consumers reading
        # `ScenarioTruthReader`; it does not need a state slot. The node-side
        # PF observes noisy ranges via LoraTOASensor (a separate, observation-
        # only path) and maintains its own range estimates in the PF-estimate
        # schema.
