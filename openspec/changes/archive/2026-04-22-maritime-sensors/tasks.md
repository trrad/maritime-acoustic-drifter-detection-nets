## 1. Measurement Record — Tests

- [x] 1.1 Measurement is immutable — mutation attempts raise `FrozenInstanceError`; fields round-trip
      (tests/maritime/test_sensors.py)

- [x] 1.2 Rejected sensor_name values — constructing with a name outside the M1 vocabulary raises `ValueError`
      (tests/maritime/test_sensors.py)

- [x] 1.3 Value is always a tuple (scalar sensor's value has len 1)
      (tests/maritime/test_sensors.py)

## 2. Measurement Record — Implementation

- [x] 2.1 `Measurement` frozen dataclass with `t_sec`, `node_id`, `sensor_name`, `value: tuple[float, ...]`, `unit`, `noise_sigma`; `__post_init__` enforces `sensor_name in VALID_SENSOR_NAMES` and `len(value) >= 1`
      (rtl/vectors/maritime/sensors.py)

## 3. Sensor Protocol and SensorEnv — Tests

- [x] 3.1 Structural subtyping — a minimal test class with `name`, `spec`, `should_sample`, `sample` satisfies the `Sensor` protocol under `isinstance` (`@runtime_checkable`)
      (tests/maritime/test_sensors.py)

- [x] 3.2 `SensorEnv` is constructible with all fields defaulted (no required arguments); `regional_map` and `fleet` default to `None`
      (tests/maritime/test_sensors.py)

## 4. Sensor Protocol and SensorEnv — Implementation

- [x] 4.1 `Sensor` protocol with `@runtime_checkable`, methods as specified
      (rtl/vectors/maritime/sensors.py)

- [x] 4.2 `SensorEnv` frozen dataclass with `regional_map: RegionalMap | None = None`, `fleet: tuple[Node, ...] | None = None` (no clock mapping — clocks are read from `node.components["clock"]`)
      (rtl/vectors/maritime/sensors.py)

## 5. Periodic Scheduling — Tests

- [x] 5.1 `should_sample(t_sec=1.0, last_fire_sec=0.0)` returns `True` for a sensor with `max_rate_hz=1.0`
      (tests/maritime/test_sensors.py)

- [x] 5.2 `should_sample(t_sec=0.5, last_fire_sec=0.0)` returns `False` for a sensor with `max_rate_hz=1.0`
      (tests/maritime/test_sensors.py)

- [x] 5.3 `should_sample` uses `>=` boundary — `t_sec=1.0, last_fire_sec=0.0, max_rate=1.0` is `True`
      (tests/maritime/test_sensors.py)

## 6. Capability Enforcement — Tests

- [x] 6.1 GPS on `PureDrifterNode` raises `CapabilityViolation`; message contains `"gps"` and the class name
      (tests/maritime/test_sensors.py)

- [x] 6.2 GPS on `AnchorNode` does not raise `CapabilityViolation`
      (tests/maritime/test_sensors.py)

- [x] 6.3 Every single-measurement sensor raises `CapabilityViolation` when the node's profile lacks its SensorSpec — parametrized test across all five
      (tests/maritime/test_sensors.py)

## 7. Timestamp via Node Clock — Tests

- [x] 7.1 Sensor timestamp uses clock — a clock returning `wall_time(t) = t + 0.010` yields `Measurement.t_sec = global_t + 0.010`
      (tests/maritime/test_sensors.py)

- [x] 7.2 Missing `"clock"` key in `node.components` raises `KeyError` when any sensor samples
      (tests/maritime/test_sensors.py)

## 8. Noise and Determinism — Tests

- [x] 8.1 Identical seeded RNGs produce identical `Measurement` values for the same (node, env, t_sec)
      (tests/maritime/test_sensors.py)

- [x] 8.2 Different seeds produce different value tuples (element-wise differ in at least one component)
      (tests/maritime/test_sensors.py)

- [x] 8.3 No sensor calls `numpy.random` globals — verified by monkey-patching `numpy.random.default_rng` and asserting it is not invoked during sampling
      (tests/maritime/test_sensors.py)

## 9. GPSSensor — Tests

- [x] 9.1 GPS produces `sensor_name="gps"`, `unit="deg"`, `value` length 2, `noise_sigma` from spec
      (tests/maritime/test_sensors.py)

- [x] 9.2 GPS measurement is within 3σ of truth position — σ converted from meters to degrees at node latitude
      (tests/maritime/test_sensors.py)

- [x] 9.3 GPS raises `CapabilityViolation` on non-anchor nodes (already covered by 6.1 generically; explicit test for GPS specifically)
      (tests/maritime/test_sensors.py)

## 10. IMUSensor — Tests

- [x] 10.1 IMU produces `value` of length 6; `sensor_name="imu"`
      (tests/maritime/test_sensors.py)

- [x] 10.2 IMU accel reflects accel bias — node with `accel_b = (0.5, 0, 0)` produces accel_x within 3σ of `(truth_accel_x + 0.5)`
      (tests/maritime/test_sensors.py)

- [x] 10.3 IMU gyro reflects gyro bias — node with `gyro_b = (0, 0, 0.1)` produces gyro_z within 3σ of `(truth_gyro_z + 0.1)`
      (tests/maritime/test_sensors.py)

## 11. BaroSensor — Tests

- [x] 11.1 Baro at depth 0 returns pressure within 3σ of 101 325 Pa; `unit="Pa"`, `sensor_name="baro"`
      (tests/maritime/test_sensors.py)

- [x] 11.2 Baro at depth 10 m returns pressure within 3σ of 201 325 Pa (hydrostatic: 10⁴ Pa/m)
      (tests/maritime/test_sensors.py)

## 12. MagSensor — Tests

- [x] 12.1 Mag with truth heading 45 deg returns value within 3σ of 45 deg
      (tests/maritime/test_sensors.py)

- [x] 12.2 Mag wraps to [0, 360) — truth heading 359 deg with positive noise exceeding 360 wraps cleanly
      (tests/maritime/test_sensors.py)

- [x] 12.3 Mag produces `sensor_name="mag"`, `unit="deg"`, `value` length 1
      (tests/maritime/test_sensors.py)

## 13. BathyProbeSensor — Tests

- [x] 13.1 Bathy at offshore position returns depth within 3σ of truth bathymetry
      (tests/maritime/test_sensors.py)

- [x] 13.2 Bathy on land returns `None` (no measurement)
      (tests/maritime/test_sensors.py)

- [x] 13.3 Missing `env.regional_map` raises `ValueError` naming the missing field
      (tests/maritime/test_sensors.py)

- [x] 13.4 Bathy produces `sensor_name="bathy_probe"`, `unit="m"`, `value` length 1 (when not on land)
      (tests/maritime/test_sensors.py)

## 14. LoraTOASensor — Tests

- [x] 14.1 Out-of-range pair returns `None`
- [x] 14.2 Drop rate within tolerance
- [x] 14.3 Successful pair sample is truth + noise
- [x] 14.4 Successful measurement has correct fields
- [x] 14.5 `sample_all_pairs` returns only successful measurements
- [x] 14.6 TDMA scheduling

## 15. GPSSensor — Implementation

- [x] 15.1 `GPSSensor(spec)` class; `name` returns `"gps"`; `sample` produces `(lat, lon)` with noise σ converted from meters to degrees at node latitude
      (rtl/vectors/maritime/sensors.py)

## 16. IMUSensor — Implementation

- [x] 16.1 `IMUSensor(spec)` class; `sample` produces 6-tuple from truth accel + bias + noise and truth gyro + bias + noise
      (rtl/vectors/maritime/sensors.py)

## 17. BaroSensor — Implementation

- [x] 17.1 `BaroSensor(spec)` class; `sample` produces pressure via hydrostatic formula `101325 + 10000 * depth_m + noise`
      (rtl/vectors/maritime/sensors.py)

## 18. MagSensor — Implementation

- [x] 18.1 `MagSensor(spec)` class; `sample` produces heading + noise wrapped to `[0, 360)`
      (rtl/vectors/maritime/sensors.py)

## 19. BathyProbeSensor — Implementation

- [x] 19.1 `BathyProbeSensor(spec)` class; `sample` queries `env.regional_map.depth_at(lat, lon)`, returns `None` on NaN (on land), else `(depth + noise,)`; raises `ValueError` if `env.regional_map is None`
      (rtl/vectors/maritime/sensors.py)

## 20. LoraTOASensor — Implementation

- [x] 20.1 `LoraTOASensor(spec, comms)` class; `sample_pair` enforces range → drop → noisy distance sequence; `sample_all_pairs` iterates fleet and returns successful measurements; `should_sample` uses TDMA period
      (rtl/vectors/maritime/sensors.py)

## 21. Verification

- [x] 21.1 `uv run pytest tests/maritime/test_sensors.py` passes with zero failures
- [x] 21.2 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and existing `rtl/vectors/*.py` files
- [x] 21.3 Module imports cleanly — `uv run python -c "from rtl.vectors.maritime.sensors import Measurement, Sensor, SensorEnv, GPSSensor, IMUSensor, BaroSensor, MagSensor, BathyProbeSensor, LoraTOASensor"` exits 0
- [x] 21.4 `openspec validate maritime-sensors --strict` passes
