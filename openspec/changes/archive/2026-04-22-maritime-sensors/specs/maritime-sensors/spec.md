## ADDED Requirements

### Requirement: Measurement Record
The system SHALL provide a `Measurement` frozen dataclass composing `t_sec: float`, `node_id: str`, `sensor_name: str`, `value: tuple[float, ...]`, `unit: str`, and `noise_sigma: float`. Every produced measurement SHALL have `sensor_name` equal to one of the six M1 vocabulary strings (`"gps"`, `"imu"`, `"baro"`, `"mag"`, `"lora_toa"`, `"bathy_probe"`). `noise_sigma` SHALL equal the producing sensor's `SensorSpec.noise_sigma`. `value` SHALL always be a tuple (scalar sensors use a length-1 tuple).

#### Scenario: Measurement is immutable
- **WHEN** a `Measurement` is constructed
- **THEN** attempting to mutate any field raises an error

#### Scenario: Sensor name is from the declared vocabulary
- **WHEN** any sensor's `sample` method returns a `Measurement`
- **THEN** the `sensor_name` field is one of `{"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"}`

#### Scenario: Noise sigma matches spec
- **WHEN** a sensor with `SensorSpec.noise_sigma = 1.5` produces a `Measurement`
- **THEN** `measurement.noise_sigma == 1.5`

### Requirement: Sensor Protocol
The system SHALL provide a `Sensor` protocol with `name` (str), `spec` (SensorSpec), `should_sample(t_sec, last_fire_sec)` (bool), and `sample(node, env, t_sec, rng)` (`Measurement | None`) members. Any class that implements these members SHALL satisfy the protocol without inheritance.

#### Scenario: Structural subtyping
- **WHEN** a class implements `name`, `spec`, `should_sample`, and `sample` with matching signatures
- **THEN** it satisfies the `Sensor` protocol under `isinstance` with `runtime_checkable` or under static type-checking

### Requirement: Capability Envelope Enforcement
Every sensor's `sample` method SHALL raise `CapabilityViolation` when called on a node whose `profile.sensor(self.name)` raises `KeyError`. The exception SHALL carry structured fields: `node_class` set to `node.profile.class_name`, `sensor_name` set to `self.name`, and `reason` describing the violation.

#### Scenario: GPS on a drifter raises CapabilityViolation
- **WHEN** `GPSSensor(...).sample(pure_drifter_node, env, t_sec, rng)` is called
- **THEN** `CapabilityViolation` is raised
- **AND** `exc.sensor_name == "gps"` and `exc.node_class == "pure_drifter"`

#### Scenario: GPS on an anchor succeeds
- **WHEN** `GPSSensor(...).sample(anchor_node, env, t_sec, rng)` is called with a valid env
- **THEN** a `Measurement` is returned (or `None` per the sensor's own rules)
- **AND** `CapabilityViolation` is not raised

### Requirement: Periodic Sample Scheduling
Single-measurement sensors SHALL implement `should_sample(t_sec, last_fire_sec)` such that it returns `True` iff `t_sec - last_fire_sec >= 1.0 / spec.max_rate_hz`. Calling `sample` does not itself enforce the rate limit — the caller is responsible for checking `should_sample` before calling `sample`. (Exception: `LoraTOASensor` uses a TDMA schedule instead of a simple rate check.)

#### Scenario: should_sample returns True when interval elapsed
- **WHEN** `sensor.should_sample(t_sec=1.0, last_fire_sec=0.0)` is called on a sensor with `max_rate_hz=1.0`
- **THEN** the return value is `True`

#### Scenario: should_sample returns False when interval not elapsed
- **WHEN** `sensor.should_sample(t_sec=0.5, last_fire_sec=0.0)` is called on a sensor with `max_rate_hz=1.0`
- **THEN** the return value is `False`

### Requirement: Timestamp via Node Clock
Every produced `Measurement.t_sec` SHALL be computed as `node.components["clock"].wall_time(t_sec)`. If `"clock"` is absent from `node.components`, the sensor SHALL raise `KeyError` (no silent fallback to the global tick time).

#### Scenario: Measurement timestamp uses node clock
- **WHEN** a sensor is sampled on a node whose `node.components["clock"].wall_time(t_sec)` returns `t_sec + 0.010`
- **THEN** the returned `Measurement.t_sec` equals `t_sec + 0.010`

#### Scenario: Missing clock raises KeyError
- **WHEN** `sensor.sample(node, env, ...)` is called and `"clock"` is not a key in `node.components`
- **THEN** `KeyError` is raised

### Requirement: Noise Applied via Injected RNG
Every sensor SHALL apply observation noise by drawing from the injected `rng` with σ = `SensorSpec.noise_sigma`. No sensor SHALL use global `numpy.random` functions or module-level state. Given the same `rng` state, identical (node, env, t_sec) inputs SHALL produce byte-identical `Measurement` outputs.

#### Scenario: Deterministic output from seeded RNG
- **WHEN** the same sensor is sampled twice with identical node, env, t_sec, and two RNGs seeded identically
- **THEN** the two returned `Measurement` values are element-wise equal

#### Scenario: Different RNG seeds produce different noise
- **WHEN** the same sensor is sampled with identical node, env, t_sec, and two differently-seeded RNGs
- **THEN** the returned measurements' `value` tuples differ in at least one element

### Requirement: GPSSensor
The system SHALL provide a `GPSSensor` class whose `sample` produces a `Measurement` with `sensor_name="gps"` and `value=(lat_deg, lon_deg)` computed from the node's truth position plus Gaussian noise (σ in meters converted to degrees at the node's latitude). `unit="deg"`. GPS SHALL raise `CapabilityViolation` on any node whose profile does not include a `"gps"` sensor.

#### Scenario: GPS measurement matches truth plus noise
- **WHEN** `GPSSensor` is sampled on an `AnchorNode` at truth position `(36.75, -122.0)` with RNG seeded deterministically
- **THEN** the returned measurement's value is within 3σ of the truth position (σ converted from meters to degrees at ~36.75 lat)

#### Scenario: GPS on drifter raises CapabilityViolation
- **WHEN** `GPSSensor.sample(...)` is called on a `PureDrifterNode`
- **THEN** `CapabilityViolation` is raised

### Requirement: IMUSensor
The system SHALL provide an `IMUSensor` class whose `sample` produces a `Measurement` with `sensor_name="imu"` and `value` of length 6 (accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z). The accel component SHALL reflect the node's truth linear acceleration (numerically differenced per tick) plus the node's accel bias plus Gaussian noise; the gyro component SHALL reflect the node's truth angular rate plus gyro bias plus Gaussian noise. IMU is present on all three M1 node classes.

#### Scenario: IMU measurement has 6-element value
- **WHEN** `IMUSensor.sample(...)` is called on any node class
- **THEN** the returned measurement's `value` is a tuple of length 6

#### Scenario: IMU reflects accel and gyro bias
- **WHEN** `IMUSensor.sample(...)` is called on a node with nonzero accel bias
- **THEN** the accel portion of the returned `value` differs from the noise-free truth acceleration by approximately the bias (to within the noise σ)

### Requirement: BaroSensor
The system SHALL provide a `BaroSensor` class whose `sample` produces a `Measurement` with `sensor_name="baro"`, `value=(pressure_pa,)`, and `unit="Pa"`. The measurement SHALL be computed from the node's truth depth plus sea-level reference pressure (101 325 Pa) + 10^4 Pa/m depth scaling plus Gaussian noise.

#### Scenario: Baro at surface returns near-sea-level pressure
- **WHEN** `BaroSensor.sample(...)` is called on a node at depth 0 m
- **THEN** the returned pressure is within 3σ of 101 325 Pa

#### Scenario: Baro at depth reflects hydrostatic pressure
- **WHEN** `BaroSensor.sample(...)` is called on a node at depth 10 m
- **THEN** the returned pressure is within 3σ of 101 325 + 100 000 = 201 325 Pa

### Requirement: MagSensor
The system SHALL provide a `MagSensor` class whose `sample` produces a `Measurement` with `sensor_name="mag"`, `value=(heading_deg,)`, and `unit="deg"`. The measurement SHALL be computed from the node's truth heading plus Gaussian noise. The returned heading SHALL be in `[0, 360)`.

#### Scenario: Mag heading matches truth within noise
- **WHEN** `MagSensor.sample(...)` is called on a node with truth heading 45 deg
- **THEN** the returned heading is within 3σ of 45 deg (wrapped to [0, 360))

#### Scenario: Mag heading wraps
- **WHEN** `MagSensor.sample(...)` is called on a node with truth heading 359 deg and the noise sample pushes the raw reading past 360
- **THEN** the returned heading is in `[0, 360)`

### Requirement: BathyProbeSensor Returns None on Land
The system SHALL provide a `BathyProbeSensor` class whose `sample` produces a `Measurement` with `sensor_name="bathy_probe"`, `value=(depth_m,)`, and `unit="m"`. If the truth `regional_map.depth_at(lat, lon)` returns NaN (node is on land), `sample` SHALL return `None`. Otherwise, the returned depth SHALL be the true depth value plus Gaussian noise with σ from the `SensorSpec`.

#### Scenario: Bathy probe at offshore point returns positive depth
- **WHEN** `BathyProbeSensor.sample(...)` is called on a node at a known-offshore position with bathymetric depth 500 m
- **THEN** the returned measurement's value is within 3σ of 500 m

#### Scenario: Bathy probe on land returns None
- **WHEN** `BathyProbeSensor.sample(...)` is called on a node at a position where `regional_map.is_on_land` is True
- **THEN** `sample` returns `None`

#### Scenario: Bathy probe requires regional_map in env
- **WHEN** `BathyProbeSensor.sample(...)` is called with `env.regional_map is None`
- **THEN** `sample` raises `ValueError` naming the missing `regional_map`

### Requirement: LoraTOASensor Range and Drop
The system SHALL provide a `LoraTOASensor` class with a `sample_pair(self_node, neighbor_node, env, t_sec, rng)` method producing a `Measurement | None` and a `sample_all_pairs(self_node, env, t_sec, rng)` method producing `tuple[Measurement, ...]`. Inside `sample_pair`:
1. If the true great-circle distance between `self_node` and `neighbor_node` exceeds `comms.max_range_m`, return `None`.
2. Else if `rng.uniform(0, 1) < comms.packet_loss_rate`, return `None` (packet dropped).
3. Else return a `Measurement` with `sensor_name="lora_toa"`, `value=(range_m,)`, `unit="m"`, `noise_sigma=comms.ranging_sigma_m`, where `range_m` is the true distance plus Gaussian noise (σ = `comms.ranging_sigma_m`).

#### Scenario: Out-of-range pair returns None
- **WHEN** `sample_pair` is called on two nodes 20 km apart with `comms.max_range_m = 15000`
- **THEN** the return value is `None`

#### Scenario: Drop rate applied
- **WHEN** `sample_pair` is called 10 000 times on in-range nodes with `comms.packet_loss_rate = 0.1` and independent RNG draws
- **THEN** the fraction of `None` returns is within `0.1 ± 0.02`

#### Scenario: In-range successful sample reflects truth plus noise
- **WHEN** `sample_pair` is called on two nodes 1000 m apart (within range, with drop-rate 0) with σ = 20 m and an unseeded RNG
- **THEN** the returned measurement's range value is within 3σ (60 m) of 1000 m

#### Scenario: sample_all_pairs aggregates successful pair samples
- **WHEN** `sample_all_pairs` is called on a self node with 5 fleet-mate neighbors
- **AND** 2 are out of range, 1 is dropped, 2 succeed
- **THEN** the returned tuple has length 2
- **AND** both measurements have `sensor_name="lora_toa"`

#### Scenario: LoraTOASensor enforces TDMA scheduling
- **WHEN** `should_sample(t_sec, last_fire_sec)` is called on a `LoraTOASensor` bound to a `CommsProfile` with `tdma_period_sec=3600`
- **AND** `t_sec - last_fire_sec = 3600`
- **THEN** the return value is `True`
- **AND** when the elapsed time is `1800`, the return value is `False`
