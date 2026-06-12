"""Contract tests for sensors module.

Tests for Measurement dataclass.
"""

import dataclasses

import pytest
import numpy as np


def _make_anchor_node(rng, state=None):
    """Build an anchor node for testing."""
    from rtl.vectors.maritime.fleet import make_anchor
    from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
    from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT
    if state is None:
        state = np.zeros(ANCHOR_LAYOUT.state_dim)
    return make_anchor(ANCHOR_PROFILE, state, rng)


def _make_pure_drifter_node(rng, state=None):
    """Build a pure drifter node for testing."""
    from rtl.vectors.maritime.fleet import make_pure_drifter
    from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
    from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
    if state is None:
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
    return make_pure_drifter(PURE_DRIFTER_PROFILE, state, rng)


def _make_ballast_drifter_node(rng, state=None):
    """Build a ballast drifter node for testing."""
    from rtl.vectors.maritime.fleet import make_ballast_drifter
    from rtl.vectors.maritime.platform_profile import BALLAST_DRIFTER_PROFILE
    from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
    if state is None:
        state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
    return make_ballast_drifter(BALLAST_DRIFTER_PROFILE, state, rng)


def _make_node_with_sensor(rng, state, layout, base_profile, sensor_spec):
    """Build a node whose profile contains the given sensor_spec.

    If the base_profile already declares a sensor with the same name (which all
    bundled M1 profiles now do for the full sensor suite), the override replaces
    the existing one so callers can pick custom noise / rate values for the test.
    Otherwise the sensor is appended.
    """
    from rtl.vectors.maritime.platform_profile import NodeProfile
    from rtl.vectors.maritime.fleet import Node
    from rtl.vectors.maritime.clock import Clock, ClockSpec

    base_other_sensors = tuple(s for s in base_profile.sensors if s.name != sensor_spec.name)
    base_same_named_sensor = next((s for s in base_profile.sensors if s.name == sensor_spec.name), None)
    power_delta = sensor_spec.avg_power_mw - (base_same_named_sensor.avg_power_mw if base_same_named_sensor is not None else 0.0)

    profile = NodeProfile(
        class_name=base_profile.class_name,
        state_dim=base_profile.state_dim,
        sensors=base_other_sensors + (sensor_spec,),
        comms=base_profile.comms,
        compute=base_profile.compute,
        total_power_budget_mw=base_profile.total_power_budget_mw + power_delta,
        components=base_profile.components,
    )
    clock = Clock(spec=ClockSpec(drift_ppm=0.0, avg_power_mw=0.0))
    components: dict[str, object] = {}
    for spec in profile.components:
        components[spec.kind] = spec
    components["clock"] = clock
    return Node(node_id="test_node", profile=profile, layout=layout, state=state, components=components)


class TestMeasurementImmutable:
    """Tests for Measurement immutability and field access."""

    def test_measurement_immutable(self) -> None:
        """Measurement with valid fields is immutable: FrozenInstanceError on mutation, all fields round-trip correctly."""
        from rtl.vectors.maritime.sensors import Measurement, VALID_SENSOR_NAMES

        m = Measurement(
            t_sec=123.45,
            node_id="node-001",
            sensor_name="gps",
            value=(36.75, -122.0),
            unit="deg",
            noise_sigma=0.01,
        )

        # Verify all fields round-trip correctly
        assert m.t_sec == 123.45
        assert m.node_id == "node-001"
        assert m.sensor_name == "gps"
        assert m.value == (36.75, -122.0)
        assert m.unit == "deg"
        assert m.noise_sigma == 0.01

        # Verify immutability for each field
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.t_sec = 200.0  # type: ignore[misc]

        with pytest.raises(dataclasses.FrozenInstanceError):
            m.node_id = "node-002"  # type: ignore[misc]

        with pytest.raises(dataclasses.FrozenInstanceError):
            m.sensor_name = "imu"  # type: ignore[misc]

        with pytest.raises(dataclasses.FrozenInstanceError):
            m.value = (1.0,)  # type: ignore[misc]

        with pytest.raises(dataclasses.FrozenInstanceError):
            m.unit = "m"  # type: ignore[misc]

        with pytest.raises(dataclasses.FrozenInstanceError):
            m.noise_sigma = 0.1  # type: ignore[misc]

    def test_valid_sensor_names_accepted(self) -> None:
        """All valid sensor names are accepted: 'gps', 'imu', 'baro', 'mag', 'lora_toa', 'bathy_probe'."""
        from rtl.vectors.maritime.sensors import Measurement, VALID_SENSOR_NAMES

        valid_names = ["gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"]

        for name in valid_names:
            m = Measurement(
                t_sec=0.0,
                node_id="node-001",
                sensor_name=name,
                value=(1.0,),
                unit="unit",
                noise_sigma=0.1,
            )
            assert m.sensor_name == name
            assert name in VALID_SENSOR_NAMES


class TestMeasurementValidation:
    """Tests for Measurement validation in __post_init__."""

    def test_measurement_rejects_invalid_sensor_name(self) -> None:
        """Measurement construction with invalid sensor_name raises ValueError."""
        from rtl.vectors.maritime.sensors import Measurement

        invalid_names = ["bogus", "GPS", "gps_", "tof", ""]

        for name in invalid_names:
            with pytest.raises(ValueError):
                Measurement(
                    t_sec=0.0,
                    node_id="node-001",
                    sensor_name=name,
                    value=(1.0,),
                    unit="unit",
                    noise_sigma=0.1,
                )

    def test_measurement_rejects_empty_value(self) -> None:
        """Measurement construction with empty value tuple raises ValueError."""
        from rtl.vectors.maritime.sensors import Measurement

        with pytest.raises(ValueError):
            Measurement(
                t_sec=0.0,
                node_id="node-001",
                sensor_name="gps",
                value=(),
                unit="unit",
                noise_sigma=0.1,
            )


class TestMeasurementValueContract:
    """Tests for Measurement value tuple contract."""

    def test_measurement_value_is_always_tuple(self) -> None:
        """Measurement value is always a tuple: scalar (len 1), multi-value (len 2+), never bare float."""
        from rtl.vectors.maritime.sensors import Measurement

        # Scalar measurement: single value in tuple
        m_scalar = Measurement(
            t_sec=0.0,
            node_id="node-001",
            sensor_name="baro",
            value=(1013.25,),
            unit="hPa",
            noise_sigma=0.5,
        )
        assert isinstance(m_scalar.value, tuple)
        assert len(m_scalar.value) == 1
        assert m_scalar.value == (1013.25,)

        # GPS measurement: two values in tuple
        m_gps = Measurement(
            t_sec=0.0,
            node_id="node-001",
            sensor_name="gps",
            value=(36.75, -122.0),
            unit="deg",
            noise_sigma=0.01,
        )
        assert isinstance(m_gps.value, tuple)
        assert len(m_gps.value) == 2
        assert m_gps.value == (36.75, -122.0)

        # Multi-value measurement: three values in tuple
        m_multi = Measurement(
            t_sec=0.0,
            node_id="node-001",
            sensor_name="imu",
            value=(0.1, 0.2, 0.3),
            unit="m/s^2",
            noise_sigma=0.05,
        )
        assert isinstance(m_multi.value, tuple)
        assert len(m_multi.value) == 3
        assert m_multi.value == (0.1, 0.2, 0.3)


class TestModuleInterface:
    """Tests for module-level interface requirements."""

    def test_valid_sensor_names_exported(self) -> None:
        """VALID_SENSOR_NAMES is exported as frozenset with expected values."""
        from rtl.vectors.maritime.sensors import VALID_SENSOR_NAMES

        assert isinstance(VALID_SENSOR_NAMES, frozenset)
        expected = {"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"}
        assert VALID_SENSOR_NAMES == expected


class TestGPS:
    """Tests for GPSSensor implementation."""

    def test_gps_measurement_fields(self, make_rng) -> None:
        """GPSSensor produces Measurement with sensor_name='gps', unit='deg', value length 2, noise_sigma matching spec."""
        from rtl.vectors.maritime.platform_profile import SensorSpec
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        from rtl.vectors.maritime.map_payload import RegionalMap

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="deg",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        rng = make_rng(seed=42)
        node = _make_anchor_node(rng)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "gps"
        assert measurement.unit == "deg"
        assert len(measurement.value) == 2
        assert measurement.noise_sigma == spec.noise_sigma

    def test_gps_within_3sigma_of_truth(self, make_rng) -> None:
        """GPS measurement is within 3σ of truth position with sigma converted from meters to degrees at node latitude."""
        from rtl.vectors.maritime.platform_profile import SensorSpec
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        import numpy as np

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        rng = make_rng(seed=123)
        node = _make_anchor_node(rng)
        enu_lat = 36.5
        enu_lon = -122.0
        env = SensorEnv(enu_origin_lat_deg=enu_lat, enu_origin_lon_deg=enu_lon, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        lat_meas, lon_meas = measurement.value

        sigma_meters = spec.noise_sigma
        meters_per_degree_lat = 111320.0
        lat_rad = enu_lat * np.pi / 180.0
        meters_per_degree_lon = 111320.0 * np.cos(lat_rad)

        sigma_lat_deg = sigma_meters / meters_per_degree_lat
        sigma_lon_deg = sigma_meters / meters_per_degree_lon

        assert abs(lat_meas - enu_lat) <= 3.0 * sigma_lat_deg
        assert abs(lon_meas - enu_lon) <= 3.0 * sigma_lon_deg

    def test_gps_capability_violation_on_drifter(self, make_rng) -> None:
        """GPSSensor.sample on pure_drifter node raises CapabilityViolation."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CapabilityViolation
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        from rtl.vectors.maritime.map_payload import RegionalMap

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        rng = make_rng(seed=456)
        node = _make_pure_drifter_node(rng)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        with pytest.raises(CapabilityViolation) as exc_info:
            sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert exc_info.value.sensor_name == "gps"
        assert exc_info.value.node_class == "pure_drifter"


class TestIMU:
    """Tests for IMUSensor implementation."""

    def test_imu_value_length_and_name(self, make_rng) -> None:
        """IMUSensor produces value of length 6 and sensor_name='imu'."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import IMUSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="imu",
            observed_dim=0,
            noise_sigma=0.01,
            noise_unit="m/s^2;rad/s",
            max_rate_hz=100.0,
            duty_cycle=1.0,
            avg_power_mw=0.5,
            noise_sigma_secondary=0.01,
        )
        sensor = IMUSensor(spec)
        rng = make_rng(seed=42)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "imu"
        assert len(measurement.value) == 6

    def test_imu_accel_reflects_bias(self, make_rng) -> None:
        """With velocity == prev_velocity (no tick-on-tick change), truth_accel == 0; measurement equals bias + noise."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import IMUSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="imu",
            observed_dim=0,
            noise_sigma=0.01,
            noise_unit="m/s^2;rad/s",
            max_rate_hz=100.0,
            duty_cycle=1.0,
            avg_power_mw=0.5,
            noise_sigma_secondary=0.01,
        )
        sensor = IMUSensor(spec)
        rng = make_rng(seed=123)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[12] = 0.5  # accel_bx = 0.5 m/s^2
        # velocity (state[3:6]) == prev_velocity (state[15:18]) == zeros → truth_accel == 0
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        accel_x = measurement.value[0]
        truth_accel_x = 0.0  # (vx - prev_vx) / dt = 0 when velocity unchanged
        expected_accel_x = truth_accel_x + 0.5
        assert abs(accel_x - expected_accel_x) <= 3.0 * spec.noise_sigma

    def test_imu_accel_reflects_velocity_delta(self, make_rng) -> None:
        """With current velocity differing from prev_velocity, accel channel reports (v - v_prev)/dt + bias (within 3σ)."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import IMUSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="imu",
            observed_dim=0,
            noise_sigma=0.01,
            noise_unit="m/s^2;rad/s",
            max_rate_hz=100.0,
            duty_cycle=1.0,
            avg_power_mw=0.5,
            noise_sigma_secondary=0.01,
        )
        sensor = IMUSensor(spec)
        rng = make_rng(seed=7)

        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[3] = 0.6          # vx = 0.6 m/s now
        state[15] = 0.4         # prev_vx = 0.4 m/s one tick ago
        state[12] = 0.1         # accel_bx bias = 0.1 m/s^2
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        dt_sec = 0.5
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=dt_sec, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        accel_x = measurement.value[0]
        truth_accel_x = (0.6 - 0.4) / dt_sec  # 0.4 m/s^2
        expected_accel_x = truth_accel_x + 0.1
        assert abs(accel_x - expected_accel_x) <= 3.0 * spec.noise_sigma

    def test_imu_gyro_reflects_bias(self, make_rng) -> None:
        """Node with gyro_b = (0, 0, 0.1) produces gyro_z within 3σ of (truth_gyro_z + 0.1)."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import IMUSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="imu",
            observed_dim=0,
            noise_sigma=0.01,
            noise_unit="m/s^2;rad/s",
            max_rate_hz=100.0,
            duty_cycle=1.0,
            avg_power_mw=0.5,
            noise_sigma_secondary=0.01,
        )
        sensor = IMUSensor(spec)
        rng = make_rng(seed=456)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[11] = 0.1
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        gyro_z = measurement.value[5]
        # heading == prev_heading == 0.0 → truth_heading_rate = 0 → truth_gyro_z = 0 rad/s
        truth_gyro_z = 0.0
        expected_gyro_z = truth_gyro_z + 0.1
        gyro_sigma = spec.noise_sigma_secondary
        assert gyro_sigma is not None
        assert abs(gyro_z - expected_gyro_z) <= 3.0 * gyro_sigma

    def test_imu_accel_and_gyro_sigmas_are_independent(self, make_rng) -> None:
        """With large accel sigma and small gyro sigma, the empirical std of accel_x
        approaches the accel sigma and the empirical std of gyro_z approaches the gyro sigma
        — confirming the two channels apply different sigmas (per maritime-sensors spec)."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import IMUSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        accel_sigma = 0.1
        gyro_sigma = 0.001
        spec = SensorSpec(
            name="imu",
            observed_dim=0,
            noise_sigma=accel_sigma,
            noise_unit="m/s^2;rad/s",
            max_rate_hz=100.0,
            duty_cycle=1.0,
            avg_power_mw=0.5,
            noise_sigma_secondary=gyro_sigma,
        )
        sensor = IMUSensor(spec)
        rng = make_rng(seed=2026)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        n = 4000
        accel_x_samples = np.empty(n)
        gyro_z_samples = np.empty(n)
        for i in range(n):
            m = sensor.sample(node, env, t_sec=float(i), rng=rng)
            assert m is not None
            accel_x_samples[i] = m.value[0]
            gyro_z_samples[i] = m.value[5]

        # Empirical std should match the corresponding sigma to within ~5%.
        assert abs(float(np.std(accel_x_samples)) - accel_sigma) / accel_sigma < 0.05
        assert abs(float(np.std(gyro_z_samples)) - gyro_sigma) / gyro_sigma < 0.05


class TestBaro:
    """Tests for BaroSensor implementation."""

    def test_baro_surface_pressure(self, make_rng) -> None:
        """Baro at depth 0 returns pressure within 3σ of 101325 Pa."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng = make_rng(seed=789)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "baro"
        assert measurement.unit == "Pa"
        assert len(measurement.value) == 1
        pressure = measurement.value[0]
        assert abs(pressure - 101325.0) <= 3.0 * spec.noise_sigma

    def test_baro_depth_pressure(self, make_rng) -> None:
        """Baro at depth 10m returns pressure within 3σ of 201325 Pa."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng = make_rng(seed=1011)
        state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        state[2] = 10.0
        node = _make_node_with_sensor(rng, state, BALLAST_DRIFTER_LAYOUT, BALLAST_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        pressure = measurement.value[0]
        expected_pressure = 101325.0 + 10000.0 * 10.0
        assert abs(pressure - expected_pressure) <= 3.0 * spec.noise_sigma


class TestMag:
    """Tests for MagSensor implementation."""

    def test_mag_heading_45(self, make_rng) -> None:
        """Mag with truth heading 45 deg returns value within 3σ of 45."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import MagSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="mag",
            observed_dim=0,
            noise_sigma=1.0,
            noise_unit="deg",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.2,
        )
        sensor = MagSensor(spec)
        rng = make_rng(seed=1213)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[6] = 45.0
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        heading = measurement.value[0]
        assert abs(heading - 45.0) <= 3.0 * spec.noise_sigma

    def test_mag_wraps_360(self, make_rng) -> None:
        """Mag wraps to [0, 360). Truth heading 359 deg, measurement in [0, 360)."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import MagSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="mag",
            observed_dim=0,
            noise_sigma=2.0,
            noise_unit="deg",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.2,
        )
        sensor = MagSensor(spec)
        rng = make_rng(seed=1415)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[6] = 359.0
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        heading = measurement.value[0]
        assert 0.0 <= heading < 360.0

    def test_mag_fields(self, make_rng) -> None:
        """Mag produces sensor_name='mag', unit='deg', value length 1."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import MagSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="mag",
            observed_dim=0,
            noise_sigma=1.0,
            noise_unit="deg",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.2,
        )
        sensor = MagSensor(spec)
        rng = make_rng(seed=1617)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        state[6] = 90.0
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "mag"
        assert measurement.unit == "deg"
        assert len(measurement.value) == 1


def _make_test_regional_map():
    """Build a minimal RegionalMap with known bathymetry for testing."""
    from rtl.vectors.maritime.map_payload import RegionalMap, BathymetryGrid, ClimatologyGrid
    lats = np.array([36.0, 37.0])
    lons = np.array([-122.0, -121.0])
    depths = np.array([[500.0, 600.0], [700.0, 800.0]])
    bathy = BathymetryGrid(lats=lats, lons=lons, depths_m=depths)
    clim = ClimatologyGrid(
        lats=lats, lons=lons,
        mean_vx_ms=np.zeros((2, 2)), mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)), var_vy_ms2=np.ones((2, 2)),
    )
    return RegionalMap(bathymetry=bathy, land_polygons=[], shipping_lanes=[], climatology=clim)


def _make_regional_map_with_land():
    """Build a RegionalMap with a land polygon covering a specific area for on-land testing."""
    from rtl.vectors.maritime.map_payload import RegionalMap, BathymetryGrid, ClimatologyGrid
    lats = np.array([36.0, 37.0])
    lons = np.array([-122.0, -121.0])
    depths = np.array([[500.0, 600.0], [700.0, 800.0]])
    bathy = BathymetryGrid(lats=lats, lons=lons, depths_m=depths)
    clim = ClimatologyGrid(
        lats=lats, lons=lons,
        mean_vx_ms=np.zeros((2, 2)), mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)), var_vy_ms2=np.ones((2, 2)),
    )
    land_poly = np.array([[-122.5, 36.4], [-121.5, 36.4], [-121.5, 36.6], [-122.5, 36.6], [-122.5, 36.4]])
    return RegionalMap(bathymetry=bathy, land_polygons=[land_poly], shipping_lanes=[], climatology=clim)


class TestBathyProbe:
    """Tests for BathyProbeSensor implementation."""

    def test_bathy_offshore_depth(self, make_rng) -> None:
        """Bathy probe at offshore point returns depth within 3σ of truth."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BathyProbeSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.coords import enu_to_latlon

        spec = SensorSpec(
            name="bathy_probe",
            observed_dim=0,
            noise_sigma=5.0,
            noise_unit="m",
            max_rate_hz=0.1,
            duty_cycle=0.01,
            avg_power_mw=0.3,
        )
        sensor = BathyProbeSensor(spec)
        rng = make_rng(seed=1819)

        map = _make_test_regional_map()

        enu_lat, enu_lon = 36.5, -122.0
        query_lat_array, query_lon_array = enu_to_latlon(0.0, 0.0, enu_lat, enu_lon)
        query_lat = float(query_lat_array)
        query_lon = float(query_lon_array)
        true_depth = map.bathymetry.at(query_lat, query_lon)

        state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, BALLAST_DRIFTER_LAYOUT, BALLAST_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=enu_lat, enu_origin_lon_deg=enu_lon, dt_sec=1.0, regional_map=map, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "bathy_probe"
        assert measurement.unit == "m"
        assert len(measurement.value) == 1
        depth = measurement.value[0]
        assert abs(depth - true_depth) <= 3.0 * spec.noise_sigma

    def test_bathy_on_land_returns_none(self, make_rng) -> None:
        """Bathy probe on land returns None."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BathyProbeSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT
        from rtl.vectors.maritime.coords import enu_to_latlon

        spec = SensorSpec(
            name="bathy_probe",
            observed_dim=0,
            noise_sigma=5.0,
            noise_unit="m",
            max_rate_hz=0.1,
            duty_cycle=0.01,
            avg_power_mw=0.3,
        )
        sensor = BathyProbeSensor(spec)
        rng = make_rng(seed=2021)
        
        map = _make_regional_map_with_land()
        
        state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, BALLAST_DRIFTER_LAYOUT, BALLAST_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=map, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert measurement is None

    def test_bathy_missing_regional_map_raises(self, make_rng) -> None:
        """Bathy probe requires regional_map in env."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, BALLAST_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BathyProbeSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import BALLAST_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="bathy_probe",
            observed_dim=0,
            noise_sigma=5.0,
            noise_unit="m",
            max_rate_hz=0.1,
            duty_cycle=0.01,
            avg_power_mw=0.3,
        )
        sensor = BathyProbeSensor(spec)
        rng = make_rng(seed=2223)
        
        state = np.zeros(BALLAST_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, BALLAST_DRIFTER_LAYOUT, BALLAST_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        with pytest.raises(ValueError) as exc_info:
            sensor.sample(node, env, t_sec=10.0, rng=rng)
        
        assert "regional_map" in str(exc_info.value).lower()


class TestLoraTOA:
    """Tests for LoraTOASensor implementation."""

    def test_lora_out_of_range_returns_none(self, make_rng) -> None:
        """Out-of-range pair returns None."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa",
            observed_dim=0,
            noise_sigma=20.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20.0,
            packet_bits=256,
            packet_loss_rate=0.0,
            avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)
        rng = make_rng(seed=2425)
        
        state1 = np.zeros(ANCHOR_LAYOUT.state_dim)
        node1 = make_anchor(ANCHOR_PROFILE, state1, rng)
        
        state2 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state2[0] = 20000.0
        node2 = make_anchor(ANCHOR_PROFILE, state2, rng)
        
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample_pair(node1, node2, env, t_sec=10.0, rng=rng)

        assert measurement is None

    def test_lora_drop_rate(self, make_rng) -> None:
        """Drop rate applied: None fraction within 0.1 ± 0.02."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa",
            observed_dim=0,
            noise_sigma=20.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20.0,
            packet_bits=256,
            packet_loss_rate=0.1,
            avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)
        
        state1 = np.zeros(ANCHOR_LAYOUT.state_dim)
        node1 = make_anchor(ANCHOR_PROFILE, state1, make_rng(seed=1))
        
        state2 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state2[0] = 1000.0
        node2 = make_anchor(ANCHOR_PROFILE, state2, make_rng(seed=2))
        
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        none_count = 0
        n_samples = 10000
        
        for i in range(n_samples):
            rng = make_rng(seed=2627 + i)
            measurement = sensor.sample_pair(node1, node2, env, t_sec=10.0, rng=rng)
            if measurement is None:
                none_count += 1
        
        drop_fraction = none_count / n_samples
        assert 0.08 <= drop_fraction <= 0.12

    def test_lora_in_range_truth_plus_noise(self, make_rng) -> None:
        """In-range successful sample reflects truth plus noise."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa",
            observed_dim=0,
            noise_sigma=20.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20.0,
            packet_bits=256,
            packet_loss_rate=0.0,
            avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)
        rng = make_rng(seed=2829)
        
        state1 = np.zeros(ANCHOR_LAYOUT.state_dim)
        node1 = make_anchor(ANCHOR_PROFILE, state1, rng)
        
        state2 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state2[0] = 1000.0
        node2 = make_anchor(ANCHOR_PROFILE, state2, rng)
        
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample_pair(node1, node2, env, t_sec=10.0, rng=rng)

        assert measurement is not None
        assert measurement.sensor_name == "lora_toa"
        assert measurement.unit == "m"
        assert len(measurement.value) == 1
        assert measurement.noise_sigma == comms.ranging_sigma_m
        range_m = measurement.value[0]
        assert abs(range_m - 1000.0) <= 3.0 * comms.ranging_sigma_m

    def test_lora_sample_all_pairs(self, make_rng) -> None:
        """sample_all_pairs aggregates successful pair samples."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa",
            observed_dim=0,
            noise_sigma=20.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20.0,
            packet_bits=256,
            packet_loss_rate=0.0,
            avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)
        rng = make_rng(seed=3031)
        
        state_self = np.zeros(ANCHOR_LAYOUT.state_dim)
        node_self = make_anchor(ANCHOR_PROFILE, state_self, rng)
        
        fleet = []
        states = []
        
        state1 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state1[0] = 20000.0
        states.append(state1)
        
        state2 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state2[0] = 25000.0
        states.append(state2)
        
        state3 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state3[0] = 1000.0
        states.append(state3)
        
        state4 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state4[0] = 1500.0
        states.append(state4)
        
        state5 = np.zeros(ANCHOR_LAYOUT.state_dim)
        state5[0] = 2000.0
        states.append(state5)
        
        for i, state in enumerate(states):
            node = make_anchor(ANCHOR_PROFILE, state, make_rng(seed=4000 + i))
            fleet.append(node)
        
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=tuple(fleet))

        measurements = sensor.sample_all_pairs(node_self, env, t_sec=10.0, rng=rng)

        assert len(measurements) == 3
        for m in measurements:
            assert m.sensor_name == "lora_toa"

    def test_lora_tdma_scheduling(self, make_rng) -> None:
        """LoraTOASensor enforces TDMA scheduling."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile
        from rtl.vectors.maritime.sensors import LoraTOASensor

        spec = SensorSpec(
            name="lora_toa",
            observed_dim=0,
            noise_sigma=20.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=0.01,
            avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05,
            tdma_period_sec=3600,
            max_range_m=15000,
            ranging_sigma_m=20.0,
            packet_bits=256,
            packet_loss_rate=0.0,
            avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)

        assert sensor.should_sample(t_sec=3600.0, last_fire_sec=0.0) is True
        assert sensor.should_sample(t_sec=1800.0, last_fire_sec=0.0) is False

    def test_sample_link_success_emits_two_measurements(self, make_rng) -> None:
        """In-range successful sample_link returns LoraLinkOutcome with status=success and 2 measurements (one per node end), both carrying the same noisy_range."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import LoraTOASensor, LoraLinkOutcome, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa", observed_dim=0, noise_sigma=20.0, noise_unit="m",
            max_rate_hz=1.0, duty_cycle=0.01, avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05, tdma_period_sec=3600, max_range_m=15000,
            ranging_sigma_m=20.0, packet_bits=256, packet_loss_rate=0.0, avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)

        state_a = np.zeros(ANCHOR_LAYOUT.state_dim)
        node_a = make_anchor(ANCHOR_PROFILE, state_a, make_rng(seed=10))
        state_b = np.zeros(ANCHOR_LAYOUT.state_dim)
        state_b[0] = 1000.0
        node_b = make_anchor(ANCHOR_PROFILE, state_b, make_rng(seed=11))

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0)
        outcome = sensor.sample_link(node_a, node_b, env, t_sec=10.0, rng=make_rng(seed=12))

        assert isinstance(outcome, LoraLinkOutcome)
        assert outcome.status == "success"
        assert outcome.range_m is not None
        assert len(outcome.measurements) == 2
        assert outcome.measurements[0].node_id == node_a.node_id
        assert outcome.measurements[1].node_id == node_b.node_id
        assert outcome.measurements[0].value[0] == outcome.range_m
        assert outcome.measurements[1].value[0] == outcome.range_m
        assert abs(outcome.range_m - 1000.0) <= 3.0 * comms.ranging_sigma_m

    def test_sample_link_out_of_range(self, make_rng) -> None:
        """Out-of-range pair returns outcome with status=out_of_range, range_m=None, no measurements."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa", observed_dim=0, noise_sigma=20.0, noise_unit="m",
            max_rate_hz=1.0, duty_cycle=0.01, avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05, tdma_period_sec=3600, max_range_m=15000,
            ranging_sigma_m=20.0, packet_bits=256, packet_loss_rate=0.0, avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)

        state_a = np.zeros(ANCHOR_LAYOUT.state_dim)
        node_a = make_anchor(ANCHOR_PROFILE, state_a, make_rng(seed=20))
        state_b = np.zeros(ANCHOR_LAYOUT.state_dim)
        state_b[0] = 20000.0
        node_b = make_anchor(ANCHOR_PROFILE, state_b, make_rng(seed=21))

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0)
        outcome = sensor.sample_link(node_a, node_b, env, t_sec=10.0, rng=make_rng(seed=22))

        assert outcome.status == "out_of_range"
        assert outcome.range_m is None
        assert outcome.measurements == ()

    def test_sample_link_dropped(self, make_rng) -> None:
        """Packet loss yields outcome with status=dropped, range_m=None, no measurements."""
        from rtl.vectors.maritime.platform_profile import SensorSpec, CommsProfile, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import LoraTOASensor, SensorEnv
        from rtl.vectors.maritime.fleet import make_anchor
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="lora_toa", observed_dim=0, noise_sigma=20.0, noise_unit="m",
            max_rate_hz=1.0, duty_cycle=0.01, avg_power_mw=0.22,
        )
        comms = CommsProfile(
            slot_length_sec=0.05, tdma_period_sec=3600, max_range_m=15000,
            ranging_sigma_m=20.0, packet_bits=256, packet_loss_rate=1.0, avg_power_mw=0.22,
        )
        sensor = LoraTOASensor(spec, comms)

        state_a = np.zeros(ANCHOR_LAYOUT.state_dim)
        node_a = make_anchor(ANCHOR_PROFILE, state_a, make_rng(seed=30))
        state_b = np.zeros(ANCHOR_LAYOUT.state_dim)
        state_b[0] = 1000.0
        node_b = make_anchor(ANCHOR_PROFILE, state_b, make_rng(seed=31))

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0)
        outcome = sensor.sample_link(node_a, node_b, env, t_sec=10.0, rng=make_rng(seed=32))

        assert outcome.status == "dropped"
        assert outcome.range_m is None
        assert outcome.measurements == ()

    def test_lora_link_outcome_construction_invariants(self) -> None:
        """LoraLinkOutcome __post_init__ rejects inconsistent combinations."""
        from rtl.vectors.maritime.sensors import LoraLinkOutcome, Measurement

        with pytest.raises(ValueError, match="success"):
            LoraLinkOutcome(status="success", range_m=None, measurements=())

        m = Measurement(t_sec=0.0, node_id="n00", sensor_name="lora_toa", value=(100.0,), unit="m", noise_sigma=20.0)
        with pytest.raises(ValueError, match="must have 2 measurements"):
            LoraLinkOutcome(status="success", range_m=100.0, measurements=(m,))

        with pytest.raises(ValueError, match="dropped"):
            LoraLinkOutcome(status="dropped", range_m=100.0, measurements=())

        with pytest.raises(ValueError, match="out_of_range"):
            LoraLinkOutcome(status="out_of_range", range_m=None, measurements=(m, m))

        with pytest.raises(ValueError, match="Invalid status"):
            LoraLinkOutcome(status="bogus", range_m=None, measurements=())


class TestSensorProtocol:
    """Tests for Sensor protocol conformance."""

    def test_structural_subtyping(self) -> None:
        from dataclasses import dataclass
        from rtl.vectors.maritime.platform_profile import SensorSpec
        from rtl.vectors.maritime.sensors import Sensor

        @dataclass
        class _DummySensor:
            _spec: SensorSpec

            @property
            def name(self) -> str:
                return "gps"

            @property
            def spec(self) -> SensorSpec:
                return self._spec

            def should_sample(self, t_sec, last_fire_sec):
                return True

            def sample(self, node, env, t_sec, rng):
                return None

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.0,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        dummy = _DummySensor(spec)
        assert isinstance(dummy, Sensor)

    def test_sensor_env_default_constructible(self) -> None:
        from rtl.vectors.maritime.sensors import SensorEnv

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0)
        assert env.regional_map is None
        assert env.fleet is None


class TestPeriodicScheduling:
    """Tests for periodic sample scheduling behavior."""

    def test_should_sample_true_at_interval(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        result = sensor.should_sample(t_sec=1.0, last_fire_sec=0.0)
        assert result is True

    def test_should_sample_false_before_interval(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        result = sensor.should_sample(t_sec=0.5, last_fire_sec=0.0)
        assert result is False

    def test_should_sample_boundary_inclusive(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, ANCHOR_PROFILE
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        result = sensor.should_sample(t_sec=1.0, last_fire_sec=0.0)
        assert result is True


class TestCapabilityEnforcement:
    """Tests for capability envelope enforcement."""

    def test_gps_on_drifter_raises_violation(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, CapabilityViolation
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        rng = make_rng(seed=42)
        node = _make_pure_drifter_node(rng)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        with pytest.raises(CapabilityViolation) as exc_info:
            sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert exc_info.value.sensor_name == "gps"
        assert exc_info.value.node_class == "pure_drifter"

    def test_gps_on_anchor_succeeds(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, CapabilityViolation
        from rtl.vectors.maritime.sensors import GPSSensor, SensorEnv

        spec = SensorSpec(
            name="gps",
            observed_dim=0,
            noise_sigma=1.5,
            noise_unit="m",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
        )
        sensor = GPSSensor(spec)
        rng = make_rng(seed=42)
        node = _make_anchor_node(rng)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)
        assert measurement is not None

    @pytest.mark.parametrize("sensor_name", ["gps", "imu", "baro", "mag", "bathy_probe"])
    def test_all_single_measurement_sensors_raise_violation(self, sensor_name, make_rng) -> None:
        """Every single-measurement sensor raises CapabilityViolation when the node's profile doesn't declare it. Bundled M1 profiles now carry most sensors, so this test constructs a sensor-less profile to exercise the capability-violation path for each sensor type."""
        import numpy as np
        from rtl.vectors.maritime.platform_profile import (
            SensorSpec,
            CapabilityViolation,
            NodeProfile,
            CommsProfile,
            ComputeBudget,
            DriftingSurfacePoseSpec,
            ClockSpec,
        )
        from rtl.vectors.maritime.sensors import GPSSensor, IMUSensor, BaroSensor, MagSensor, BathyProbeSensor, SensorEnv
        from rtl.vectors.maritime.fleet import Node, make_pure_drifter
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        sensor_classes = {
            "gps": GPSSensor,
            "imu": IMUSensor,
            "baro": BaroSensor,
            "mag": MagSensor,
            "bathy_probe": BathyProbeSensor,
        }

        spec = SensorSpec(
            name=sensor_name,
            observed_dim=0,
            noise_sigma=1.0,
            noise_unit="unit",
            max_rate_hz=1.0,
            duty_cycle=1.0,
            avg_power_mw=0.0,
            noise_sigma_secondary=0.01 if sensor_name == "imu" else None,
        )
        sensor_class = sensor_classes[sensor_name]
        sensor = sensor_class(spec)
        rng = make_rng(seed=42)

        sensorless_profile = NodeProfile(
            class_name="pure_drifter",
            state_dim=PURE_DRIFTER_LAYOUT.state_dim,
            sensors=(),
            comms=CommsProfile(
                slot_length_sec=0.05,
                tdma_period_sec=3600,
                max_range_m=10000,
                ranging_sigma_m=20.0,
                packet_bits=256,
                packet_loss_rate=0.1,
                avg_power_mw=0.22,
            ),
            compute=ComputeBudget(
                clock_mhz=12.0,
                cycles_per_step=33000,
                pf_update_rate_hz=1.0,
                headroom=0.8,
                avg_power_mw=0.09,
            ),
            total_power_budget_mw=2.0,
            components=(DriftingSurfacePoseSpec(), ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)),
        )
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = make_pure_drifter(sensorless_profile, initial_state, rng)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        with pytest.raises(CapabilityViolation) as exc_info:
            sensor.sample(node, env, t_sec=10.0, rng=rng)

        assert exc_info.value.sensor_name == sensor_name
        assert exc_info.value.node_class == "pure_drifter"


class TestTimestampViaClock:
    """Tests for timestamp computation via node clock."""

    def test_timestamp_uses_clock(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.clock import Clock, ClockSpec
        from rtl.vectors.maritime.fleet import Node

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng = make_rng(seed=42)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)

        clock = Clock(spec=ClockSpec(drift_ppm=10.0, avg_power_mw=0.0))
        clock.advance(1.0)
        components = dict(node.components)
        components["clock"] = clock

        node = Node(
            node_id=node.node_id,
            profile=node.profile,
            layout=node.layout,
            state=node.state,
            components=components,
        )

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement = sensor.sample(node, env, t_sec=10.0, rng=rng)

        expected_t_sec = 10.0 + 0.00001
        assert measurement.t_sec == expected_t_sec

    def test_missing_clock_raises_keyerror(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT
        from rtl.vectors.maritime.fleet import Node

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng = make_rng(seed=42)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)

        components = {k: v for k, v in node.components.items() if k != "clock"}

        node = Node(
            node_id=node.node_id,
            profile=node.profile,
            layout=node.layout,
            state=node.state,
            components=components,
        )

        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        with pytest.raises(KeyError):
            sensor.sample(node, env, t_sec=10.0, rng=rng)


class TestNoiseAndDeterminism:
    """Tests for noise application and determinism."""

    def test_identical_seeds_identical_measurements(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng1 = make_rng(seed=42)
        rng2 = make_rng(seed=42)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng1, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement1 = sensor.sample(node, env, t_sec=10.0, rng=rng1)
        measurement2 = sensor.sample(node, env, t_sec=10.0, rng=rng2)

        assert measurement1.value == measurement2.value

    def test_different_seeds_different_values(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng1 = make_rng(seed=42)
        rng2 = make_rng(seed=123)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng1, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        measurement1 = sensor.sample(node, env, t_sec=10.0, rng=rng1)
        measurement2 = sensor.sample(node, env, t_sec=10.0, rng=rng2)

        assert measurement1.value != measurement2.value

    def test_no_global_numpy_random(self, make_rng) -> None:
        from rtl.vectors.maritime.platform_profile import SensorSpec, PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.sensors import BaroSensor, SensorEnv
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        spec = SensorSpec(
            name="baro",
            observed_dim=0,
            noise_sigma=100.0,
            noise_unit="Pa",
            max_rate_hz=10.0,
            duty_cycle=1.0,
            avg_power_mw=0.3,
        )
        sensor = BaroSensor(spec)
        rng = make_rng(seed=42)
        state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        node = _make_node_with_sensor(rng, state, PURE_DRIFTER_LAYOUT, PURE_DRIFTER_PROFILE, spec)
        env = SensorEnv(enu_origin_lat_deg=36.5, enu_origin_lon_deg=-122.0, dt_sec=1.0, regional_map=None, fleet=None)

        original = np.random.default_rng
        calls = []

        def tracking_rng(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)

        np.random.default_rng = tracking_rng

        try:
            sensor.sample(node, env, t_sec=10.0, rng=rng)
            assert len(calls) == 0, "Sensor called numpy.random.default_rng"
        finally:
            np.random.default_rng = original
