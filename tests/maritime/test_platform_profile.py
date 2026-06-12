"""Contract tests for platform_profile module.

Tests for SensorSpec dataclass and CapabilityViolation exception.
"""

import dataclasses
from dataclasses import dataclass
from typing import ClassVar

import pytest


def _make_sensor(name: str = "test_sensor", avg_power_mw: float = 10.0):
    """Create a valid SensorSpec for use in tests.

    For ``name == "imu"`` automatically populates ``noise_sigma_secondary``
    to satisfy the IMU-specific requirement that the gyro sigma be set.
    """
    from rtl.vectors.maritime.platform_profile import SensorSpec

    return SensorSpec(
        name=name,
        observed_dim=0,
        noise_sigma=1.5,
        noise_unit="m",
        max_rate_hz=1.0,
        duty_cycle=0.01,
        avg_power_mw=avg_power_mw,
        noise_sigma_secondary=0.01 if name == "imu" else None,
    )


def _make_comms(avg_power_mw: float = 0.22):
    """Create a valid CommsProfile for use in tests."""
    from rtl.vectors.maritime.platform_profile import CommsProfile

    return CommsProfile(
        slot_length_sec=0.05,
        tdma_period_sec=3600,
        max_range_m=15000,
        ranging_sigma_m=20,
        packet_bits=256,
        packet_loss_rate=0.1,
        avg_power_mw=avg_power_mw
    )


def _make_compute(avg_power_mw: float = 0.0):
    """Create a valid ComputeBudget for use in tests."""
    from rtl.vectors.maritime.platform_profile import ComputeBudget

    return ComputeBudget(
        clock_mhz=6.0,
        cycles_per_step=1000,
        pf_update_rate_hz=1.0,
        avg_power_mw=avg_power_mw
    )


class TestSensorSpec:
    """Tests for SensorSpec dataclass."""

    def test_valid_construction_round_trip(self) -> None:
        """Construct with valid parameters and verify all field values.

        Assert all field accesses return the provided values.
        """
        from rtl.vectors.maritime.platform_profile import SensorSpec

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=10.0
        )

        assert spec.name == "gps"
        assert spec.observed_dim == 0
        assert spec.noise_sigma == 1.5
        assert spec.noise_unit == "m"
        assert spec.max_rate_hz == 1.0
        assert spec.duty_cycle == 0.01
        assert spec.avg_power_mw == 10.0

    def test_immutable(self) -> None:
        """Attempting to mutate any field raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=10.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "imu"  # type: ignore[misc]

    def test_negative_noise_sigma_rejected(self) -> None:
        """Constructing with negative noise_sigma raises ValueError.

        The error message must contain "noise_sigma".
        """
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError) as exc_info:
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=-0.1,
                noise_unit="m",
                max_rate_hz=1.0,
                duty_cycle=0.01,
                avg_power_mw=10.0
            )

        assert "noise_sigma" in str(exc_info.value)

    def test_duty_cycle_above_one_rejected(self) -> None:
        """Constructing with duty_cycle > 1 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=1.5,
                noise_unit="m",
                max_rate_hz=1.0,
                duty_cycle=1.5,
                avg_power_mw=10.0
            )

    def test_duty_cycle_below_zero_rejected(self) -> None:
        """Constructing with duty_cycle < 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=1.5,
                noise_unit="m",
                max_rate_hz=1.0,
                duty_cycle=-0.01,
                avg_power_mw=10.0
            )

    def test_zero_max_rate_hz_rejected(self) -> None:
        """Constructing with max_rate_hz = 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=1.5,
                noise_unit="m",
                max_rate_hz=0.0,
                duty_cycle=0.01,
                avg_power_mw=10.0
            )

    def test_negative_max_rate_hz_rejected(self) -> None:
        """Constructing with negative max_rate_hz raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=1.5,
                noise_unit="m",
                max_rate_hz=-1.0,
                duty_cycle=0.01,
                avg_power_mw=10.0
            )

    def test_negative_avg_power_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError) as exc_info:
            SensorSpec(
                name="gps",
                observed_dim=0,
                noise_sigma=1.5,
                noise_unit="m",
                max_rate_hz=1.0,
                duty_cycle=0.01,
                avg_power_mw=-1.0
            )

        assert "avg_power_mw" in str(exc_info.value)

    def test_non_imu_defaults_secondary_sigma_to_none(self) -> None:
        """Non-IMU SensorSpec defaults noise_sigma_secondary to None."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=10.0,
        )
        assert spec.noise_sigma_secondary is None

    def test_imu_requires_secondary_sigma(self) -> None:
        """SensorSpec(name='imu') without noise_sigma_secondary raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError) as exc_info:
            SensorSpec(
                name="imu",
                observed_dim=0,
                noise_sigma=0.01,
                noise_unit="m/s^2;rad/s",
                max_rate_hz=1.0,
                duty_cycle=0.01,
                avg_power_mw=0.5,
            )
        assert "noise_sigma_secondary" in str(exc_info.value)

    def test_negative_secondary_sigma_rejected(self) -> None:
        """Negative noise_sigma_secondary raises ValueError."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="imu",
                observed_dim=0,
                noise_sigma=0.01,
                noise_unit="m/s^2;rad/s",
                max_rate_hz=1.0,
                duty_cycle=0.01,
                avg_power_mw=0.5,
                noise_sigma_secondary=-0.001,
            )

    def test_zero_secondary_sigma_rejected(self) -> None:
        """Zero noise_sigma_secondary raises ValueError (must be strictly positive)."""
        from rtl.vectors.maritime.platform_profile import SensorSpec

        with pytest.raises(ValueError):
            SensorSpec(
                name="imu",
                observed_dim=0,
                noise_sigma=0.01,
                noise_unit="m/s^2;rad/s",
                max_rate_hz=1.0,
                duty_cycle=0.01,
                avg_power_mw=0.5,
                noise_sigma_secondary=0.0,
            )


class TestCapabilityViolation:
    """Tests for CapabilityViolation exception."""

    def test_importable_and_raisable(self) -> None:
        """Import CapabilityViolation, raise with structured fields, verify fields and message."""
        from rtl.vectors.maritime.platform_profile import CapabilityViolation

        exc = CapabilityViolation(node_class="drifter", sensor_name="gps", reason="not available")
        with pytest.raises(CapabilityViolation) as exc_info:
            raise exc

        assert exc_info.value.node_class == "drifter"
        assert exc_info.value.sensor_name == "gps"
        assert exc_info.value.reason == "not available"
        assert "gps" in str(exc_info.value)
        assert "drifter" in str(exc_info.value)

    def test_subclasses_exception(self) -> None:
        """Assert CapabilityViolation is a subclass of Exception."""
        from rtl.vectors.maritime.platform_profile import CapabilityViolation

        assert issubclass(CapabilityViolation, Exception)


class TestCommsProfile:
    """Tests for CommsProfile dataclass."""

    def test_valid_construction_round_trip(self) -> None:
        """Construct with valid parameters and verify all field values.

        Assert all field accesses return the provided values.
        """
        from rtl.vectors.maritime.platform_profile import CommsProfile

        profile = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20,
            packet_bits=256,
            packet_loss_rate=0.1,
            avg_power_mw=0.22
        )

        assert profile.slot_length_sec == 0.05
        assert profile.tdma_period_sec == 3600
        assert profile.max_range_m == 15000
        assert profile.ranging_sigma_m == 20
        assert profile.packet_bits == 256
        assert profile.packet_loss_rate == 0.1
        assert profile.avg_power_mw == 0.22

    def test_immutable(self) -> None:
        """Attempting to mutate any field raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import CommsProfile

        profile = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20,
            packet_bits=256,
            packet_loss_rate=0.1,
            avg_power_mw=0.22
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.slot_length_sec = 0.1  # type: ignore[misc]

    def test_slot_exceeds_tdma_rejected(self) -> None:
        """Constructing with slot_length_sec > tdma_period_sec raises ValueError."""
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError):
            CommsProfile(
                slot_length_sec=10.0,
                tdma_period_sec=5.0,
                max_range_m=15000,
                ranging_sigma_m=20,
                packet_bits=256,
                packet_loss_rate=0.1,
                avg_power_mw=0.22
            )

    def test_non_positive_max_range_rejected(self) -> None:
        """Constructing with max_range_m = 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError):
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=0,
                ranging_sigma_m=20,
                packet_bits=256,
                packet_loss_rate=0.1,
                avg_power_mw=0.22
            )

    def test_packet_loss_rate_below_zero_rejected(self) -> None:
        """Constructing with packet_loss_rate < 0 raises ValueError.

        The error message must contain "packet_loss_rate".
        """
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError) as exc_info:
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=15000,
                ranging_sigma_m=20,
                packet_bits=256,
                packet_loss_rate=-0.01,
                avg_power_mw=0.22
            )

        assert "packet_loss_rate" in str(exc_info.value)

    def test_packet_loss_rate_above_one_rejected(self) -> None:
        """Constructing with packet_loss_rate > 1 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError):
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=15000,
                ranging_sigma_m=20,
                packet_bits=256,
                packet_loss_rate=1.5,
                avg_power_mw=0.22
            )

    def test_negative_ranging_sigma_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError) as exc_info:
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=15000,
                ranging_sigma_m=-1.0,
                packet_bits=256,
                packet_loss_rate=0.1,
                avg_power_mw=0.22
            )

        assert "ranging_sigma_m" in str(exc_info.value)

    def test_negative_packet_bits_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError) as exc_info:
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=15000,
                ranging_sigma_m=20,
                packet_bits=-1,
                packet_loss_rate=0.1,
                avg_power_mw=0.22
            )

        assert "packet_bits" in str(exc_info.value)

    def test_negative_avg_power_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import CommsProfile

        with pytest.raises(ValueError) as exc_info:
            CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=15000,
                ranging_sigma_m=20,
                packet_bits=256,
                packet_loss_rate=0.1,
                avg_power_mw=-1.0
            )

        assert "avg_power_mw" in str(exc_info.value)


class TestComputeBudget:
    """Tests for ComputeBudget dataclass."""

    def test_budget_within_capacity(self) -> None:
        """Construct with budget within capacity and verify inequality holds.

        Uses clock_mhz=6, cycles_per_step=33000, pf_update_rate_hz=1.0, headroom=0.8.
        Assert cycles_per_step * pf_update_rate_hz (33000) is less than
        clock_mhz * 1e6 * headroom (4800000).
        """
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        budget = ComputeBudget(
            clock_mhz=6.0,
            cycles_per_step=33000,
            pf_update_rate_hz=1.0,
            headroom=0.8
        )

        required_cycles_per_sec = budget.cycles_per_step * budget.pf_update_rate_hz
        available_cycles_per_sec = budget.clock_mhz * 1e6 * budget.headroom

        assert required_cycles_per_sec == 33000
        assert available_cycles_per_sec == 4800000.0
        assert required_cycles_per_sec < available_cycles_per_sec

    def test_budget_exceeding_capacity_rejected(self) -> None:
        """Constructing with budget exceeding capacity raises ValueError.

        Uses clock_mhz=1, cycles_per_step=2_000_000, pf_update_rate_hz=1.0, headroom=0.8.
        The error message must mention the capacity overshoot.
        """
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        with pytest.raises(ValueError) as exc_info:
            ComputeBudget(
                clock_mhz=1.0,
                cycles_per_step=2_000_000,
                pf_update_rate_hz=1.0,
                headroom=0.8
            )

        error_msg = str(exc_info.value)
        assert "2_000_000" in error_msg or "2000000" in error_msg

    def test_non_positive_clock_rejected(self) -> None:
        """Constructing with clock_mhz = 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        with pytest.raises(ValueError):
            ComputeBudget(
                clock_mhz=0.0,
                cycles_per_step=1000,
                pf_update_rate_hz=1.0
            )

    def test_headroom_zero_rejected(self) -> None:
        """Constructing with headroom = 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        with pytest.raises(ValueError):
            ComputeBudget(
                clock_mhz=6.0,
                cycles_per_step=1000,
                pf_update_rate_hz=1.0,
                headroom=0.0
            )

    def test_headroom_above_one_rejected(self) -> None:
        """Constructing with headroom = 1.5 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        with pytest.raises(ValueError):
            ComputeBudget(
                clock_mhz=6.0,
                cycles_per_step=1000,
                pf_update_rate_hz=1.0,
                headroom=1.5
            )

    def test_default_headroom(self) -> None:
        """Construct with default headroom and verify it equals 0.8."""
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        budget = ComputeBudget(
            clock_mhz=6.0,
            cycles_per_step=1000,
            pf_update_rate_hz=1.0
        )

        assert budget.headroom == 0.8

    def test_default_avg_power(self) -> None:
        """Construct with default avg_power_mw and verify it equals 0.0."""
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        budget = ComputeBudget(
            clock_mhz=6.0,
            cycles_per_step=1000,
            pf_update_rate_hz=1.0
        )

        assert budget.avg_power_mw == 0.0

    def test_negative_avg_power_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import ComputeBudget

        with pytest.raises(ValueError) as exc_info:
            ComputeBudget(
                clock_mhz=6.0,
                cycles_per_step=1000,
                pf_update_rate_hz=1.0,
                avg_power_mw=-1.0
            )

        assert "avg_power_mw" in str(exc_info.value)


class TestNodeProfile:
    """Tests for NodeProfile dataclass."""

    def test_valid_construction_round_trip(self) -> None:
        """Construct with valid parameters and verify all field values.

        Assert all field accesses return the provided values and total_avg_power_mw
        equals sum of sensor + comms + compute average powers.
        """
        from rtl.vectors.maritime.platform_profile import NodeProfile

        sensor = _make_sensor(name="gps", avg_power_mw=10.0)
        comms = _make_comms(avg_power_mw=0.22)
        compute = _make_compute(avg_power_mw=0.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(sensor,),
            comms=comms,
            compute=compute,
            total_power_budget_mw=100.0
        )

        assert profile.class_name == "test_node"
        assert profile.state_dim == 10
        assert profile.sensors == (sensor,)
        assert profile.comms == comms
        assert profile.compute == compute
        assert profile.total_power_budget_mw == 100.0
        assert profile.total_avg_power_mw == 10.22

    def test_immutable(self) -> None:
        """Attempting to mutate any field raises FrozenInstanceError."""
        from rtl.vectors.maritime.platform_profile import NodeProfile

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(_make_sensor(),),
            comms=_make_comms(),
            compute=_make_compute(),
            total_power_budget_mw=100.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.class_name = "other_node"  # type: ignore[misc]

    def test_duplicate_sensor_names_rejected(self) -> None:
        """Constructing with duplicate sensor names raises ValueError.

        The error message must contain the duplicated name.
        """
        from rtl.vectors.maritime.platform_profile import NodeProfile

        sensor1 = _make_sensor(name="imu", avg_power_mw=5.0)
        sensor2 = _make_sensor(name="imu", avg_power_mw=3.0)

        with pytest.raises(ValueError) as exc_info:
            NodeProfile(
                class_name="test_node",
                state_dim=10,
                sensors=(sensor1, sensor2),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=100.0
            )

        assert "imu" in str(exc_info.value)

    def test_power_overshoot_rejected(self) -> None:
        """Constructing with total power less than required raises ValueError.

        The error message must mention the overshoot.
        """
        from rtl.vectors.maritime.platform_profile import NodeProfile

        sensor = _make_sensor(avg_power_mw=10.0)

        with pytest.raises(ValueError) as exc_info:
            NodeProfile(
                class_name="test_node",
                state_dim=10,
                sensors=(sensor,),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=5.0
            )

        error_msg = str(exc_info.value)
        assert "10.0" in error_msg or "10" in error_msg

    def test_sensor_lookup(self) -> None:
        """Lookup sensor by name returns correct spec or raises KeyError."""
        from rtl.vectors.maritime.platform_profile import NodeProfile

        gps_sensor = _make_sensor(name="gps", avg_power_mw=10.0)
        imu_sensor = _make_sensor(name="imu", avg_power_mw=5.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(gps_sensor, imu_sensor),
            comms=_make_comms(avg_power_mw=0.0),
            compute=_make_compute(avg_power_mw=0.0),
            total_power_budget_mw=100.0
        )

        assert profile.sensor("gps") is gps_sensor
        assert profile.sensor("imu") is imu_sensor

        with pytest.raises(KeyError):
            profile.sensor("nonexistent")

    def test_non_positive_state_dim_rejected(self) -> None:
        """Constructing with state_dim = 0 raises ValueError."""
        from rtl.vectors.maritime.platform_profile import NodeProfile

        with pytest.raises(ValueError):
            NodeProfile(
                class_name="test_node",
                state_dim=0,
                sensors=(_make_sensor(avg_power_mw=0.0),),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=100.0
            )

    def test_non_positive_power_budget_rejected(self) -> None:
        from rtl.vectors.maritime.platform_profile import NodeProfile

        with pytest.raises(ValueError) as exc_info:
            NodeProfile(
                class_name="test_node",
                state_dim=10,
                sensors=(_make_sensor(avg_power_mw=0.0),),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=0.0
            )

        assert "total_power_budget_mw" in str(exc_info.value)

    def test_total_sensor_power_mw(self) -> None:
        """Verify total_sensor_power_mw returns sum of all sensor avg_power_mw."""
        from rtl.vectors.maritime.platform_profile import NodeProfile

        sensor1 = _make_sensor(name="gps", avg_power_mw=10.0)
        sensor2 = _make_sensor(name="imu", avg_power_mw=5.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(sensor1, sensor2),
            comms=_make_comms(avg_power_mw=0.0),
            compute=_make_compute(avg_power_mw=0.0),
            total_power_budget_mw=100.0
        )

        assert profile.total_sensor_power_mw == 15.0

    def test_rejects_dropped_boolean_flags(self) -> None:
        """NodeProfile rejects has_pump, is_moored, has_satellite_uplink, ballast_capacity_ml.

        Passing any of these dropped keyword arguments raises TypeError.
        """
        from rtl.vectors.maritime.platform_profile import NodeProfile

        base_kwargs = {
            "class_name": "test_node",
            "state_dim": 10,
            "sensors": (_make_sensor(avg_power_mw=0.0),),
            "comms": _make_comms(avg_power_mw=0.0),
            "compute": _make_compute(avg_power_mw=0.0),
            "total_power_budget_mw": 100.0,
        }

        with pytest.raises(TypeError):
            NodeProfile(**base_kwargs, has_pump=False)  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            NodeProfile(**base_kwargs, is_moored=False)  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            NodeProfile(**base_kwargs, has_satellite_uplink=False)  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            NodeProfile(**base_kwargs, ballast_capacity_ml=0.0)  # type: ignore[call-arg]

    def test_no_dropped_attributes(self) -> None:
        """NodeProfile has no has_pump, is_moored, has_satellite_uplink, ballast_capacity_ml.

        Attempting to access these attributes raises AttributeError.
        """
        from rtl.vectors.maritime.platform_profile import NodeProfile

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(_make_sensor(avg_power_mw=0.0),),
            comms=_make_comms(avg_power_mw=0.0),
            compute=_make_compute(avg_power_mw=0.0),
            total_power_budget_mw=100.0
        )

        with pytest.raises(AttributeError):
            profile.has_pump  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            profile.is_moored  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            profile.has_satellite_uplink  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            profile.ballast_capacity_ml  # type: ignore[attr-defined]

    def test_accepts_components_tuple(self) -> None:
        """NodeProfile accepts components tuple and exposes it as immutable."""
        from rtl.vectors.maritime.platform_profile import (
            BallastDriftingPoseSpec,
            BallastSpec,
            NodeProfile,
        )

        component1 = BallastDriftingPoseSpec()
        component2 = BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(),
            comms=_make_comms(avg_power_mw=0.0),
            compute=_make_compute(avg_power_mw=0.0),
            total_power_budget_mw=100.0,
            components=(component1, component2)
        )

        assert profile.components == (component1, component2)
        assert isinstance(profile.components, tuple)

    def test_duplicate_component_kinds_rejected(self) -> None:
        """Duplicate component kinds in components tuple raise ValueError."""
        from rtl.vectors.maritime.platform_profile import (
            BallastDriftingPoseSpec,
            NodeProfile,
        )

        with pytest.raises(ValueError) as exc_info:
            NodeProfile(
                class_name="test_node",
                state_dim=10,
                sensors=(),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=100.0,
                components=(BallastDriftingPoseSpec(), BallastDriftingPoseSpec())
            )

        assert "ballast_drifting_pose" in str(exc_info.value)

    def test_component_power_counts_toward_total(self) -> None:
        """Component avg_power_mw sum counts toward total_avg_power_mw."""
        from rtl.vectors.maritime.platform_profile import (
            BallastDriftingPoseSpec,
            BallastSpec,
            NodeProfile,
        )

        component = BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(),
            comms=_make_comms(avg_power_mw=0.22),
            compute=_make_compute(avg_power_mw=0.5),
            total_power_budget_mw=100.0,
            components=(component,)
        )

        assert abs(profile.total_avg_power_mw - 2.72) < 1e-9

    def test_component_power_overshoot_rejected(self) -> None:
        """Component power overshoot of total_power_budget_mw is rejected."""
        from rtl.vectors.maritime.platform_profile import (
            BallastSpec,
            NodeProfile,
        )

        component = BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=200.0)

        with pytest.raises(ValueError) as exc_info:
            NodeProfile(
                class_name="test_node",
                state_dim=10,
                sensors=(),
                comms=_make_comms(avg_power_mw=0.0),
                compute=_make_compute(avg_power_mw=0.0),
                total_power_budget_mw=100.0,
                components=(component,)
            )

        assert "power budget" in str(exc_info.value).lower()

    def test_component_accessor(self) -> None:
        """profile.component(kind) returns matching spec; KeyError for unknown kind."""
        from rtl.vectors.maritime.platform_profile import (
            BallastDriftingPoseSpec,
            BallastSpec,
            NodeProfile,
        )

        component1 = BallastDriftingPoseSpec()
        component2 = BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0)

        profile = NodeProfile(
            class_name="test_node",
            state_dim=10,
            sensors=(),
            comms=_make_comms(avg_power_mw=0.0),
            compute=_make_compute(avg_power_mw=0.0),
            total_power_budget_mw=100.0,
            components=(component1, component2)
        )

        assert profile.component("ballast_drifting_pose") is component1
        assert profile.component("ballast_pump") is component2

        with pytest.raises(KeyError):
            profile.component("unknown_kind")


class TestBundledProfiles:
    """Tests for bundled M1 fleet profile constants."""

    def test_state_dims(self) -> None:
        """Verify state dimensions match expected values."""
        from rtl.vectors.maritime.platform_profile import (
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        assert ANCHOR_PROFILE.state_dim == 21
        assert BALLAST_DRIFTER_PROFILE.state_dim == 21
        assert PURE_DRIFTER_PROFILE.state_dim == 19

    def test_gps_sensor_presence(self) -> None:
        """Verify GPS presence: anchor has it, others do not."""
        from rtl.vectors.maritime.platform_profile import (
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        gps_sensors = [s for s in ANCHOR_PROFILE.sensors if s.name == "gps"]
        assert len(gps_sensors) == 1
        assert gps_sensors[0].name == "gps"

        gps_sensors = [s for s in BALLAST_DRIFTER_PROFILE.sensors if s.name == "gps"]
        assert len(gps_sensors) == 0

        gps_sensors = [s for s in PURE_DRIFTER_PROFILE.sensors if s.name == "gps"]
        assert len(gps_sensors) == 0

    def test_power_budget_ceilings(self) -> None:
        """Verify power budgets respect design ceilings and power consumption."""
        from rtl.vectors.maritime.platform_profile import (
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        assert PURE_DRIFTER_PROFILE.total_power_budget_mw <= 2.0
        assert BALLAST_DRIFTER_PROFILE.total_power_budget_mw <= 5.0
        assert ANCHOR_PROFILE.total_power_budget_mw <= 50.0

        assert PURE_DRIFTER_PROFILE.total_avg_power_mw <= PURE_DRIFTER_PROFILE.total_power_budget_mw
        assert BALLAST_DRIFTER_PROFILE.total_avg_power_mw <= BALLAST_DRIFTER_PROFILE.total_power_budget_mw
        assert ANCHOR_PROFILE.total_avg_power_mw <= ANCHOR_PROFILE.total_power_budget_mw

    def test_all_m1_profiles_order(self) -> None:
        """Verify ALL_M1_PROFILES tuple order and contents."""
        from rtl.vectors.maritime.platform_profile import (
            ALL_M1_PROFILES,
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        assert len(ALL_M1_PROFILES) == 3
        assert ALL_M1_PROFILES[0] is ANCHOR_PROFILE
        assert ALL_M1_PROFILES[1] is BALLAST_DRIFTER_PROFILE
        assert ALL_M1_PROFILES[2] is PURE_DRIFTER_PROFILE

    def test_round_trip_via_replace(self) -> None:
        """Verify dataclasses.replace works on all bundled profiles."""
        import dataclasses
        from rtl.vectors.maritime.platform_profile import (
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        for profile in [ANCHOR_PROFILE, BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE]:
            replaced = dataclasses.replace(profile)
            assert replaced == profile

    def test_anchor_components(self) -> None:
        """ANCHOR_PROFILE.components contains moored_pose and satellite_uplink only."""
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE

        component_kinds = [c.kind for c in ANCHOR_PROFILE.components]

        assert "moored_pose" in component_kinds
        assert "satellite_uplink" in component_kinds
        assert "ballast_pump" not in component_kinds
        assert "drifting_surface_pose" not in component_kinds
        assert "ballast_drifting_pose" not in component_kinds

    def test_ballast_drifter_components(self) -> None:
        """BALLAST_DRIFTER_PROFILE.components contains ballast_pump and ballast_drifting_pose only."""
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE

        component_kinds = [c.kind for c in BALLAST_DRIFTER_PROFILE.components]

        assert "ballast_pump" in component_kinds
        assert "ballast_drifting_pose" in component_kinds
        assert "moored_pose" not in component_kinds
        assert "satellite_uplink" not in component_kinds
        assert "drifting_surface_pose" not in component_kinds

    def test_pure_drifter_components(self) -> None:
        """PURE_DRIFTER_PROFILE.components contains drifting_surface_pose."""
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE

        component_kinds = [c.kind for c in PURE_DRIFTER_PROFILE.components]

        assert "drifting_surface_pose" in component_kinds
        assert "moored_pose" not in component_kinds
        assert "satellite_uplink" not in component_kinds
        assert "ballast_pump" not in component_kinds
        assert "ballast_drifting_pose" not in component_kinds


class TestComponentSpec:
    """Tests for ComponentSpec protocol."""

    def test_frozen_dataclass_with_kind_and_power_conforms(self) -> None:
        """A frozen dataclass with kind and avg_power_mw satisfies isinstance check."""
        from rtl.vectors.maritime.platform_profile import ComponentSpec

        @dataclass(frozen=True, slots=True)
        class TestComponentSpecImpl:
            kind: ClassVar[str] = "test_component"
            avg_power_mw: float

        spec = TestComponentSpecImpl(avg_power_mw=5.0)
        assert isinstance(spec, ComponentSpec)
        assert spec.avg_power_mw == 5.0

    def test_class_lacking_kind_or_power_fails_isinstance(self) -> None:
        """A class lacking either kind or avg_power_mw fails the isinstance check."""
        from rtl.vectors.maritime.platform_profile import ComponentSpec

        @dataclass(frozen=True, slots=True)
        class OnlyKind:
            kind: ClassVar[str] = "only_kind"

        @dataclass(frozen=True, slots=True)
        class OnlyPower:
            avg_power_mw: float

        only_kind = OnlyKind()
        only_power = OnlyPower(avg_power_mw=5.0)

        assert not isinstance(only_kind, ComponentSpec)
        assert not isinstance(only_power, ComponentSpec)


class TestBundledProfileClock:
    """Tests for ClockSpec in bundled M1 profiles."""

    def test_anchor_profile_has_clock(self):
        """ANCHOR_PROFILE.component('clock') returns ClockSpec with drift_ppm==0.0, avg_power_mw==0.0"""
        from rtl.vectors.maritime.clock import ClockSpec
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE

        clock = ANCHOR_PROFILE.component("clock")
        assert isinstance(clock, ClockSpec)
        assert clock.drift_ppm == 0.0
        assert clock.avg_power_mw == 0.0

    def test_ballast_drifter_profile_has_clock(self):
        """BALLAST_DRIFTER_PROFILE has zero-drift zero-power ClockSpec"""
        from rtl.vectors.maritime.clock import ClockSpec
        from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE

        clock = BALLAST_DRIFTER_PROFILE.component("clock")
        assert isinstance(clock, ClockSpec)
        assert clock.drift_ppm == 0.0
        assert clock.avg_power_mw == 0.0

    def test_pure_drifter_profile_has_clock(self):
        """PURE_DRIFTER_PROFILE has zero-drift zero-power ClockSpec"""
        from rtl.vectors.maritime.clock import ClockSpec
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE

        clock = PURE_DRIFTER_PROFILE.component("clock")
        assert isinstance(clock, ClockSpec)
        assert clock.drift_ppm == 0.0
        assert clock.avg_power_mw == 0.0

    def test_power_budget_still_satisfied(self):
        """Each bundled profile still satisfies power budget with clock's 0.0 mW"""
        from rtl.vectors.maritime.platform_profile import (
            ANCHOR_PROFILE,
            BALLAST_DRIFTER_PROFILE,
            PURE_DRIFTER_PROFILE,
        )

        assert ANCHOR_PROFILE.total_avg_power_mw <= ANCHOR_PROFILE.total_power_budget_mw
        assert BALLAST_DRIFTER_PROFILE.total_avg_power_mw <= BALLAST_DRIFTER_PROFILE.total_power_budget_mw
        assert PURE_DRIFTER_PROFILE.total_avg_power_mw <= PURE_DRIFTER_PROFILE.total_power_budget_mw
