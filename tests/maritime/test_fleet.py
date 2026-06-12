"""Contract tests for fleet module.

Tests for M1 physics component spec dataclasses.
"""

import dataclasses

import numpy as np
import pytest


class TestM1ComponentSpecs:
    """Tests for M1 physics component specifications."""

    def test_moored_pose_spec_construction(self) -> None:
        """MooredPoseSpec constructs with anchor parameters and exposes kind.

        All instances conform to ComponentSpec protocol.
        """
        from rtl.vectors.maritime.platform_profile import ComponentSpec, MooredPoseSpec

        spec = MooredPoseSpec(
            anchor_lat_deg=45.5,
            anchor_lon_deg=-122.5,
            anchor_depth_m=100.0,
            avg_power_mw=0.0
        )

        assert spec.kind == "moored_pose"
        assert spec.anchor_lat_deg == 45.5
        assert spec.anchor_lon_deg == -122.5
        assert spec.anchor_depth_m == 100.0
        assert spec.avg_power_mw == 0.0
        assert isinstance(spec, ComponentSpec)

    def test_drifting_surface_pose_spec_construction(self) -> None:
        """DriftingSurfacePoseSpec constructs with no extra parameters.

        All instances conform to ComponentSpec protocol.
        """
        from rtl.vectors.maritime.platform_profile import ComponentSpec, DriftingSurfacePoseSpec

        spec = DriftingSurfacePoseSpec()

        assert spec.kind == "drifting_surface_pose"
        assert spec.avg_power_mw == 0.0
        assert isinstance(spec, ComponentSpec)

    def test_ballast_drifting_pose_spec_construction(self) -> None:
        """BallastDriftingPoseSpec constructs with no extra parameters.

        All instances conform to ComponentSpec protocol.
        """
        from rtl.vectors.maritime.platform_profile import BallastDriftingPoseSpec, ComponentSpec

        spec = BallastDriftingPoseSpec()

        assert spec.kind == "ballast_drifting_pose"
        assert spec.avg_power_mw == 0.0
        assert isinstance(spec, ComponentSpec)

    def test_ballast_spec_valid_construction(self) -> None:
        """BallastSpec constructs with valid parameters.

        All instances conform to ComponentSpec protocol.
        """
        from rtl.vectors.maritime.platform_profile import BallastSpec, ComponentSpec

        spec = BallastSpec(
            capacity_ml=50.0,
            pump_rate_ml_per_s=10.0,
            avg_power_mw=5.0
        )

        assert spec.kind == "ballast_pump"
        assert spec.capacity_ml == 50.0
        assert spec.pump_rate_ml_per_s == 10.0
        assert spec.avg_power_mw == 5.0
        assert isinstance(spec, ComponentSpec)

    def test_ballast_spec_negative_capacity_rejected(self) -> None:
        """BallastSpec rejects capacity_ml <= 0."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        with pytest.raises(ValueError) as exc_info:
            BallastSpec(
                capacity_ml=-1.0,
                pump_rate_ml_per_s=10.0,
                avg_power_mw=5.0
            )

        assert "capacity_ml" in str(exc_info.value)

    def test_ballast_spec_zero_capacity_rejected(self) -> None:
        """BallastSpec rejects capacity_ml = 0."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        with pytest.raises(ValueError) as exc_info:
            BallastSpec(
                capacity_ml=0.0,
                pump_rate_ml_per_s=10.0,
                avg_power_mw=5.0
            )

        assert "capacity_ml" in str(exc_info.value)

    def test_ballast_spec_negative_pump_rate_rejected(self) -> None:
        """BallastSpec rejects pump_rate_ml_per_s <= 0."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        with pytest.raises(ValueError) as exc_info:
            BallastSpec(
                capacity_ml=50.0,
                pump_rate_ml_per_s=-1.0,
                avg_power_mw=5.0
            )

        assert "pump_rate_ml_per_s" in str(exc_info.value)

    def test_ballast_spec_zero_pump_rate_rejected(self) -> None:
        """BallastSpec rejects pump_rate_ml_per_s = 0."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        with pytest.raises(ValueError) as exc_info:
            BallastSpec(
                capacity_ml=50.0,
                pump_rate_ml_per_s=0.0,
                avg_power_mw=5.0
            )

        assert "pump_rate_ml_per_s" in str(exc_info.value)

    def test_ballast_spec_negative_power_rejected(self) -> None:
        """BallastSpec rejects avg_power_mw < 0."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        with pytest.raises(ValueError) as exc_info:
            BallastSpec(
                capacity_ml=50.0,
                pump_rate_ml_per_s=10.0,
                avg_power_mw=-1.0
            )

        assert "avg_power_mw" in str(exc_info.value)

    def test_satellite_uplink_spec_valid_construction(self) -> None:
        """SatelliteUplinkSpec constructs with valid parameters.

        All instances conform to ComponentSpec protocol.
        """
        from rtl.vectors.maritime.platform_profile import SatelliteUplinkSpec
        from rtl.vectors.maritime.platform_profile import ComponentSpec

        spec = SatelliteUplinkSpec(
            duty_cycle=0.1,
            avg_power_mw=50.0
        )

        assert spec.kind == "satellite_uplink"
        assert spec.duty_cycle == 0.1
        assert spec.avg_power_mw == 50.0
        assert isinstance(spec, ComponentSpec)

    def test_satellite_uplink_spec_duty_cycle_above_one_rejected(self) -> None:
        """SatelliteUplinkSpec rejects duty_cycle > 1."""
        from rtl.vectors.maritime.platform_profile import SatelliteUplinkSpec

        with pytest.raises(ValueError) as exc_info:
            SatelliteUplinkSpec(
                duty_cycle=1.5,
                avg_power_mw=50.0
            )

        assert "duty_cycle" in str(exc_info.value)

    def test_satellite_uplink_spec_duty_cycle_below_zero_rejected(self) -> None:
        """SatelliteUplinkSpec rejects duty_cycle < 0."""
        from rtl.vectors.maritime.platform_profile import SatelliteUplinkSpec

        with pytest.raises(ValueError) as exc_info:
            SatelliteUplinkSpec(
                duty_cycle=-0.1,
                avg_power_mw=50.0
            )

        assert "duty_cycle" in str(exc_info.value)

    def test_satellite_uplink_spec_negative_power_rejected(self) -> None:
        """SatelliteUplinkSpec rejects avg_power_mw < 0."""
        from rtl.vectors.maritime.platform_profile import SatelliteUplinkSpec

        with pytest.raises(ValueError) as exc_info:
            SatelliteUplinkSpec(
                duty_cycle=0.1,
                avg_power_mw=-1.0
            )

        assert "avg_power_mw" in str(exc_info.value)

    def test_moored_pose_spec_immutable(self) -> None:
        """MooredPoseSpec is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import MooredPoseSpec

        spec = MooredPoseSpec(
            anchor_lat_deg=45.5,
            anchor_lon_deg=-122.5,
            anchor_depth_m=100.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.anchor_lat_deg = 46.0  # type: ignore[misc]

    def test_drifting_surface_pose_spec_immutable(self) -> None:
        """DriftingSurfacePoseSpec is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import DriftingSurfacePoseSpec

        spec = DriftingSurfacePoseSpec()

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.avg_power_mw = 1.0  # type: ignore[misc]

    def test_ballast_drifting_pose_spec_immutable(self) -> None:
        """BallastDriftingPoseSpec is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import BallastDriftingPoseSpec

        spec = BallastDriftingPoseSpec()

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.avg_power_mw = 1.0  # type: ignore[misc]

    def test_ballast_spec_immutable(self) -> None:
        """BallastSpec is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import BallastSpec

        spec = BallastSpec(
            capacity_ml=50.0,
            pump_rate_ml_per_s=10.0,
            avg_power_mw=5.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.capacity_ml = 100.0  # type: ignore[misc]

    def test_satellite_uplink_spec_immutable(self) -> None:
        """SatelliteUplinkSpec is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import SatelliteUplinkSpec

        spec = SatelliteUplinkSpec(
            duty_cycle=0.1,
            avg_power_mw=50.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.duty_cycle = 0.2  # type: ignore[misc]


class TestComposedNode:
    """Tests for composed Node dataclass."""

    def test_node_construction_succeeds(self, make_rng):
        """Valid Node(node_id, profile, layout, state, components) constructs and is immutable."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=PURE_DRIFTER_PROFILE,
            layout=PURE_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        assert node.node_id == "test_node"
        assert node.profile is PURE_DRIFTER_PROFILE
        assert node.layout is PURE_DRIFTER_LAYOUT
        np.testing.assert_array_equal(node.state, initial_state)
        assert node.components is components

    def test_node_immutable(self, make_rng):
        """Node is immutable — mutation raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=PURE_DRIFTER_PROFILE,
            layout=PURE_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            node.node_id = "new_id"  # type: ignore[misc]

    def test_node_state_shape_mismatch_rejected(self):
        """State-shape mismatch rejected — state shape (N,) with layout.state_dim != N raises ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim + 1)
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        with pytest.raises(ValueError):
            Node(
                node_id="test_node",
                profile=PURE_DRIFTER_PROFILE,
                layout=PURE_DRIFTER_LAYOUT,
                state=initial_state,
                components=components
            )

    def test_node_profile_layout_state_dim_mismatch_rejected(self):
        """Profile/layout state_dim mismatch rejected — raises ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE, ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        with pytest.raises(ValueError):
            Node(
                node_id="test_node",
                profile=PURE_DRIFTER_PROFILE,
                layout=ANCHOR_LAYOUT,
                state=initial_state,
                components=components
            )

    def test_node_state_with_nan_rejected(self):
        """State containing NaN values rejected at construction — ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        initial_state[0] = np.nan
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        with pytest.raises(ValueError):
            Node(
                node_id="test_node",
                profile=PURE_DRIFTER_PROFILE,
                layout=PURE_DRIFTER_LAYOUT,
                state=initial_state,
                components=components
            )

    def test_node_state_with_inf_rejected(self):
        """State containing infinite values rejected at construction — ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        initial_state[0] = np.inf
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        with pytest.raises(ValueError):
            Node(
                node_id="test_node",
                profile=PURE_DRIFTER_PROFILE,
                layout=PURE_DRIFTER_LAYOUT,
                state=initial_state,
                components=components
            )

    def test_node_component_key_not_in_profile_rejected(self):
        """Component mapping key not in profile.components kinds is rejected — raises ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        components: dict[str, object] = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}
        components["invalid_kind"] = "invalid_component"

        with pytest.raises(ValueError):
            Node(
                node_id="test_node",
                profile=PURE_DRIFTER_PROFILE,
                layout=PURE_DRIFTER_LAYOUT,
                state=initial_state,
                components=components
            )


class TestBlueprintFactories:
    """Tests for blueprint factory functions."""

    def test_make_anchor_succeeds(self, make_rng):
        """make_anchor returns a Node with is_moored True, has_satellite_uplink True, has_pump False."""
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import (
            Node,
            make_anchor,
            is_moored,
            has_satellite_uplink,
            has_pump,
        )

        rng = make_rng()
        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)
        node = make_anchor(ANCHOR_PROFILE, initial_state, rng)

        assert isinstance(node, Node)
        assert is_moored(node)
        assert has_satellite_uplink(node)
        assert not has_pump(node)

    def test_make_anchor_rejects_profile_missing_moored_pose(self, make_rng):
        """make_anchor rejects a profile missing moored_pose with ValueError."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_anchor

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_anchor(PURE_DRIFTER_PROFILE, initial_state, rng)

    def test_make_anchor_rejects_profile_missing_satellite_uplink(self, make_rng):
        """make_anchor rejects a profile missing satellite_uplink with ValueError."""
        from rtl.vectors.maritime.platform_profile import NodeProfile, MooredPoseSpec
        from rtl.vectors.maritime.platform_profile import CommsProfile, ComputeBudget, _LORA_COMMS
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import make_anchor

        profile = NodeProfile(
            class_name="test_anchor_no_uplink",
            state_dim=ANCHOR_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=73000, pf_update_rate_hz=1.0, avg_power_mw=0.5),
            total_power_budget_mw=50.0,
            components=(MooredPoseSpec(anchor_lat_deg=0.0, anchor_lon_deg=0.0, anchor_depth_m=0.0),)
        )

        rng = make_rng()
        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_anchor(profile, initial_state, rng)

    def test_make_ballast_drifter_succeeds(self, make_rng):
        """make_ballast_drifter returns a Node with has_pump True, is_moored False."""
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import (
            Node,
            make_ballast_drifter,
            is_moored,
            has_satellite_uplink,
            has_pump,
        )

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        node = make_ballast_drifter(BALLAST_DRIFTER_PROFILE, initial_state, rng)

        assert isinstance(node, Node)
        assert has_pump(node)
        assert not is_moored(node)
        assert not has_satellite_uplink(node)

    def test_make_ballast_drifter_rejects_profile_with_moored_pose(self, make_rng):
        """make_ballast_drifter rejects profiles with moored_pose component — ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            MooredPoseSpec,
            BallastSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_ballast_drifter

        profile = NodeProfile(
            class_name="test_ballast_with_moored",
            state_dim=BALLAST_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=50000, pf_update_rate_hz=1.0, avg_power_mw=0.15),
            total_power_budget_mw=5.0,
            components=(
                MooredPoseSpec(anchor_lat_deg=0.0, anchor_lon_deg=0.0, anchor_depth_m=0.0),
                BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0),
            ),
        )

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_ballast_drifter(profile, initial_state, rng)

    def test_make_ballast_drifter_rejects_profile_with_satellite_uplink(self, make_rng):
        """make_ballast_drifter rejects profiles with satellite_uplink component — ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            BallastDriftingPoseSpec,
            BallastSpec,
            SatelliteUplinkSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_ballast_drifter

        profile = NodeProfile(
            class_name="test_ballast_with_uplink",
            state_dim=BALLAST_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=50000, pf_update_rate_hz=1.0, avg_power_mw=0.15),
            total_power_budget_mw=20.0,
            components=(
                BallastDriftingPoseSpec(),
                BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0),
                SatelliteUplinkSpec(duty_cycle=0.01, avg_power_mw=15.0),
            ),
        )

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_ballast_drifter(profile, initial_state, rng)

    def test_make_pure_drifter_succeeds(self, make_rng):
        """make_pure_drifter returns a Node with all three helpers returning False."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import (
            Node,
            make_pure_drifter,
            is_moored,
            has_satellite_uplink,
            has_pump,
        )

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = make_pure_drifter(PURE_DRIFTER_PROFILE, initial_state, rng)

        assert isinstance(node, Node)
        assert not has_pump(node)
        assert not is_moored(node)
        assert not has_satellite_uplink(node)

    def test_make_pure_drifter_rejects_profile_with_ballast_pump(self, make_rng):
        """make_pure_drifter rejects profiles containing ballast_pump — ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            DriftingSurfacePoseSpec,
            BallastSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_pure_drifter

        profile = NodeProfile(
            class_name="test_pure_with_pump",
            state_dim=PURE_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=33000, pf_update_rate_hz=1.0, avg_power_mw=0.09),
            total_power_budget_mw=3.0,
            components=(
                DriftingSurfacePoseSpec(),
                BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0),
            ),
        )

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_pure_drifter(profile, initial_state, rng)

    def test_make_pure_drifter_rejects_profile_with_moored_pose(self, make_rng):
        """make_pure_drifter rejects profiles containing moored_pose — ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            MooredPoseSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_pure_drifter

        profile = NodeProfile(
            class_name="test_pure_with_moored",
            state_dim=PURE_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=33000, pf_update_rate_hz=1.0, avg_power_mw=0.09),
            total_power_budget_mw=3.0,
            components=(MooredPoseSpec(anchor_lat_deg=0.0, anchor_lon_deg=0.0, anchor_depth_m=0.0),),
        )

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_pure_drifter(profile, initial_state, rng)

    def test_make_pure_drifter_rejects_profile_with_satellite_uplink(self, make_rng):
        """make_pure_drifter rejects profiles containing satellite_uplink — ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            DriftingSurfacePoseSpec,
            SatelliteUplinkSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_pure_drifter

        profile = NodeProfile(
            class_name="test_pure_with_uplink",
            state_dim=PURE_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=33000, pf_update_rate_hz=1.0, avg_power_mw=0.09),
            total_power_budget_mw=20.0,
            components=(
                DriftingSurfacePoseSpec(),
                SatelliteUplinkSpec(duty_cycle=0.01, avg_power_mw=15.0),
            ),
        )

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_pure_drifter(profile, initial_state, rng)


class TestUtilityHelpers:
    """Tests for utility helper functions."""

    def test_has_pump_returns_true_for_ballast_pump(self, make_rng):
        """has_pump returns True iff 'ballast_pump' in node.components."""
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node, has_pump

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in BALLAST_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=BALLAST_DRIFTER_PROFILE,
            layout=BALLAST_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        assert has_pump(node)

    def test_has_pump_returns_false_for_no_ballast_pump(self, make_rng):
        """has_pump returns False when 'ballast_pump' not in node.components."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node, has_pump

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in PURE_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=PURE_DRIFTER_PROFILE,
            layout=PURE_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        assert not has_pump(node)

    def test_is_moored_returns_true_for_moored_pose(self, make_rng):
        """is_moored returns True iff 'moored_pose' in node.components."""
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import Node, is_moored

        rng = make_rng()
        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in ANCHOR_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=ANCHOR_PROFILE,
            layout=ANCHOR_LAYOUT,
            state=initial_state,
            components=components
        )

        assert is_moored(node)

    def test_is_moored_returns_false_for_no_moored_pose(self, make_rng):
        """is_moored returns False when 'moored_pose' not in node.components."""
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node, is_moored

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in BALLAST_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=BALLAST_DRIFTER_PROFILE,
            layout=BALLAST_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        assert not is_moored(node)

    def test_has_satellite_uplink_returns_true_for_satellite_uplink(self, make_rng):
        """has_satellite_uplink returns True iff 'satellite_uplink' in node.components."""
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import Node, has_satellite_uplink

        rng = make_rng()
        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in ANCHOR_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=ANCHOR_PROFILE,
            layout=ANCHOR_LAYOUT,
            state=initial_state,
            components=components
        )

        assert has_satellite_uplink(node)

    def test_has_satellite_uplink_returns_false_for_no_satellite_uplink(self, make_rng):
        """has_satellite_uplink returns False when 'satellite_uplink' not in node.components."""
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node, has_satellite_uplink

        rng = make_rng()
        initial_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        components = {spec.kind: spec for spec in BALLAST_DRIFTER_PROFILE.components}

        node = Node(
            node_id="test_node",
            profile=BALLAST_DRIFTER_PROFILE,
            layout=BALLAST_DRIFTER_LAYOUT,
            state=initial_state,
            components=components
        )

        assert not has_satellite_uplink(node)


class TestM1FleetFactory:
    """Tests for M1 fleet factory function."""

    def test_m1_fleet_composition(self):
        """make_m1_fleet(42, bbox) returns 10 nodes with 2 moored, 4 pumped, 4 neither."""
        from rtl.vectors.maritime.fleet import (
            make_m1_fleet,
            is_moored,
            has_pump,
        )

        bbox = (36.0, -122.5, 36.5, -122.0)
        fleet = make_m1_fleet(42, bbox)

        assert len(fleet) == 10

        moored_count = sum(1 for node in fleet if is_moored(node))
        pumped_count = sum(1 for node in fleet if has_pump(node))

        assert moored_count == 2
        assert pumped_count == 4
        assert 10 - moored_count - pumped_count == 4

    def test_m1_fleet_determinism(self):
        """Two calls with identical args produce byte-identical node IDs, profiles, layouts, initial states."""
        from rtl.vectors.maritime.fleet import make_m1_fleet

        bbox = (36.0, -122.5, 36.5, -122.0)
        fleet1 = make_m1_fleet(42, bbox)
        fleet2 = make_m1_fleet(42, bbox)

        assert len(fleet1) == len(fleet2) == 10

        for node1, node2 in zip(fleet1, fleet2):
            assert node1.node_id == node2.node_id
            assert node1.profile == node2.profile
            assert node1.layout is node2.layout
            assert np.array_equal(node1.state, node2.state)

    def test_m1_fleet_different_seed_changes_drifters_not_anchors(self):
        """Different seed produces different drifter positions but identical anchor positions."""
        from rtl.vectors.maritime.fleet import make_m1_fleet, is_moored

        bbox = (36.0, -122.5, 36.5, -122.0)
        fleet1 = make_m1_fleet(42, bbox)
        fleet2 = make_m1_fleet(99, bbox)

        anchors1 = [node for node in fleet1 if is_moored(node)]
        anchors2 = [node for node in fleet2 if is_moored(node)]
        drifters1 = [node for node in fleet1 if not is_moored(node)]
        drifters2 = [node for node in fleet2 if not is_moored(node)]

        assert len(anchors1) == len(anchors2) == 2
        assert len(drifters1) == len(drifters2) == 8

        for anchor1, anchor2 in zip(anchors1, anchors2):
            assert np.array_equal(anchor1.state, anchor2.state)

        drifter_states_differ = any(
            not np.array_equal(d1.state, d2.state) for d1, d2 in zip(drifters1, drifters2)
        )
        assert drifter_states_differ

    def test_m1_fleet_positions_inside_bbox(self):
        """All positions strictly inside bbox."""
        from rtl.vectors.maritime.fleet import make_m1_fleet
        from rtl.vectors.maritime.coords import haversine_m

        bbox = (36.0, -122.5, 36.5, -122.0)
        min_lat, min_lon, max_lat, max_lon = bbox

        fleet = make_m1_fleet(42, bbox)

        for node in fleet:
            east_m = node.state[0]
            north_m = node.state[1]

            assert np.isfinite(east_m)
            assert np.isfinite(north_m)

            east_range = haversine_m(min_lat, min_lon, min_lat, max_lon)
            north_range = haversine_m(min_lat, min_lon, max_lat, min_lon)

            assert 0 <= east_m <= east_range
            assert 0 <= north_m <= north_range

    def test_m1_fleet_distinct_node_ids(self):
        """All 10 node IDs distinct."""
        from rtl.vectors.maritime.fleet import make_m1_fleet

        bbox = (36.0, -122.5, 36.5, -122.0)
        fleet = make_m1_fleet(42, bbox)

        node_ids = [node.node_id for node in fleet]
        assert len(node_ids) == len(set(node_ids))

    def test_m1_fleet_anchors_carry_bbox_derived_coords(self):
        """Each anchor's MooredPoseSpec carries real bbox-corner lat/lon, not the placeholder (0, 0)."""
        from rtl.vectors.maritime.fleet import make_m1_fleet, is_moored
        from rtl.vectors.maritime.platform_profile import MooredPoseSpec
        from typing import cast

        bbox = (36.0, -122.5, 36.5, -122.0)
        fleet = make_m1_fleet(42, bbox)

        anchors = [node for node in fleet if is_moored(node)]
        assert len(anchors) == 2

        anchor1_pose = cast(MooredPoseSpec, anchors[0].profile.component("moored_pose"))
        anchor2_pose = cast(MooredPoseSpec, anchors[1].profile.component("moored_pose"))

        assert (anchor1_pose.anchor_lat_deg, anchor1_pose.anchor_lon_deg) == (36.0, -122.5)
        assert (anchor2_pose.anchor_lat_deg, anchor2_pose.anchor_lon_deg) == (36.5, -122.0)
        assert anchor1_pose.anchor_lat_deg != anchor2_pose.anchor_lat_deg
        assert anchor1_pose.anchor_lon_deg != anchor2_pose.anchor_lon_deg


class TestClockAttachment:
    """Tests for Clock component attachment by factory functions."""

    def test_make_anchor_attaches_clock(self, make_rng):
        """make_anchor returns node with 'clock' in components; Clock instance; spec matches profile; offset == 0.0"""
        from rtl.vectors.maritime.clock import Clock
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
        from rtl.vectors.maritime.fleet import make_anchor

        rng = make_rng()
        initial_state = np.zeros(ANCHOR_LAYOUT.state_dim)
        node = make_anchor(ANCHOR_PROFILE, initial_state, rng)

        assert "clock" in node.components
        assert isinstance(node.components["clock"], Clock)
        assert node.components["clock"].spec is ANCHOR_PROFILE.component("clock")
        assert node.components["clock"]._accumulated_offset_sec == 0.0

    def test_make_ballast_and_pure_drifter_attach_clock(self, make_rng):
        """Both make_ballast_drifter and make_pure_drifter attach Clock from profile's ClockSpec"""
        from rtl.vectors.maritime.clock import Clock
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT, PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_ballast_drifter, make_pure_drifter

        rng = make_rng()

        # Test ballast drifter
        ballast_state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        ballast_node = make_ballast_drifter(BALLAST_DRIFTER_PROFILE, ballast_state, rng)
        assert "clock" in ballast_node.components
        assert isinstance(ballast_node.components["clock"], Clock)

        # Test pure drifter
        pure_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        pure_node = make_pure_drifter(PURE_DRIFTER_PROFILE, pure_state, rng)
        assert "clock" in pure_node.components
        assert isinstance(pure_node.components["clock"], Clock)

    def test_factory_rejects_profile_without_clock(self, make_rng):
        """Blueprint factory raises ValueError when profile lacks ClockSpec"""
        from rtl.vectors.maritime.platform_profile import (
            NodeProfile,
            DriftingSurfacePoseSpec,
            CommsProfile,
            ComputeBudget,
            _LORA_COMMS,
        )
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import make_pure_drifter

        profile = NodeProfile(
            class_name="test_no_clock",
            state_dim=PURE_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=_LORA_COMMS,
            compute=ComputeBudget(clock_mhz=12.0, cycles_per_step=33000, pf_update_rate_hz=1.0, avg_power_mw=0.09),
            total_power_budget_mw=2.0,
            components=(DriftingSurfacePoseSpec(),)
        )

        rng = make_rng()
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)

        with pytest.raises(ValueError):
            make_pure_drifter(profile, initial_state, rng)


def test_make_m1_fleet_cadence_override_applies_to_all_nodes():
    """Cadence kwargs clone bundled profiles uniformly — every node sees
    the override, bundled singletons remain unmutated. Guards against
    the bug where an override silently applies to only a subset of node
    classes.
    """
    from rtl.vectors.maritime.fleet import make_m1_fleet, is_moored
    from rtl.vectors.maritime.platform_profile import (
        ANCHOR_PROFILE,
        BALLAST_DRIFTER_PROFILE,
        PURE_DRIFTER_PROFILE,
    )

    bbox = (36.0, -122.5, 36.5, -122.0)
    override_period = 60.0

    bundled_ballast_tdma = BALLAST_DRIFTER_PROFILE.comms.tdma_period_sec
    bundled_pure_tdma = PURE_DRIFTER_PROFILE.comms.tdma_period_sec
    bundled_anchor_tdma = ANCHOR_PROFILE.comms.tdma_period_sec

    fleet = make_m1_fleet(
        42,
        bbox,
        lora_period_sec=override_period,
        gps_period_sec=override_period,
    )

    for node in fleet:
        assert node.profile.comms.tdma_period_sec == override_period, (
            f"{node.node_id}: tdma_period_sec={node.profile.comms.tdma_period_sec}"
        )

    for node in fleet:
        for spec in node.profile.sensors:
            if spec.name == "lora_toa":
                assert spec.max_rate_hz == pytest.approx(1.0 / override_period)

    for node in fleet:
        gps_specs = [spec for spec in node.profile.sensors if spec.name == "gps"]
        if is_moored(node):
            assert len(gps_specs) == 1
            assert gps_specs[0].max_rate_hz == pytest.approx(1.0 / override_period)
        else:
            assert len(gps_specs) == 0

    assert BALLAST_DRIFTER_PROFILE.comms.tdma_period_sec == bundled_ballast_tdma
    assert PURE_DRIFTER_PROFILE.comms.tdma_period_sec == bundled_pure_tdma
    assert ANCHOR_PROFILE.comms.tdma_period_sec == bundled_anchor_tdma


def test_make_m1_fleet_default_kwargs_preserve_bundled_tdma():
    """No cadence kwargs → every node's comms.tdma_period_sec equals the
    bundled profile singleton's value (no cloning, no mutation path).
    """
    from rtl.vectors.maritime.fleet import make_m1_fleet
    from rtl.vectors.maritime.platform_profile import (
        ANCHOR_PROFILE,
        BALLAST_DRIFTER_PROFILE,
        PURE_DRIFTER_PROFILE,
    )

    bbox = (36.0, -122.5, 36.5, -122.0)
    fleet = make_m1_fleet(42, bbox)

    for node in fleet:
        cls = node.profile.class_name
        if cls == "ballast_drifter":
            assert node.profile.comms.tdma_period_sec == BALLAST_DRIFTER_PROFILE.comms.tdma_period_sec
        elif cls == "pure_drifter":
            assert node.profile.comms.tdma_period_sec == PURE_DRIFTER_PROFILE.comms.tdma_period_sec
        elif cls == "anchor":
            assert node.profile.comms.tdma_period_sec == ANCHOR_PROFILE.comms.tdma_period_sec
