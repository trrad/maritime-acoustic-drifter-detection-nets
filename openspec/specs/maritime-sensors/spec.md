## Purpose

Typed sensor observations for the M1 fleet. Defines the `Sensor` protocol, the `Measurement` record type, five single-measurement sensor classes (GPS, IMU, baro, mag, bathy probe), and one multi-measurement class (LoRa TOA ranging). Every sensor enforces its capability envelope via `CapabilityViolation` on profile mismatch, applies noise from `SensorSpec.noise_sigma`, and (where relevant) respects range ceilings, packet loss, and TDMA scheduling from `CommsProfile`.

## Requirements

### Requirement: Measurement Record
The system SHALL provide a `Measurement` frozen dataclass composing
`t_sec: float`, `node_id: str`, `sensor_name: str`, `value: tuple[float, ...]`,
`unit: str`, and `noise_sigma: float`. Every produced measurement SHALL
have `sensor_name` equal to one of the six M1 vocabulary strings (`"gps"`,
`"imu"`, `"baro"`, `"mag"`, `"lora_toa"`, `"bathy_probe"`).
`Measurement.noise_sigma` SHALL equal the producing sensor's primary
`SensorSpec.noise_sigma` field (for IMU, this is the accel-channel
sigma in m/s²; the gyro-channel sigma is exposed via the typed
`IMUObservation` record per `maritime-scenario-schema`, not via
`Measurement`). `value` SHALL always be a tuple (scalar sensors use a
length-1 tuple). `Measurement` is the sensor-module-internal shape;
the conversion to typed `Observation` records (per
`maritime-scenario-schema`) happens at the generator boundary, where
the per-sensor `SensorSpec` (and any IMU-specific second sigma) is
in scope.

#### Scenario: Measurement is immutable
- **WHEN** a `Measurement` is constructed
- **THEN** attempting to mutate any field raises an error

#### Scenario: Sensor name is from the declared vocabulary
- **WHEN** any sensor's `sample` method returns a `Measurement`
- **THEN** the `sensor_name` field is one of `{"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"}`

#### Scenario: Measurement carries primary sigma
- **WHEN** any sensor with `SensorSpec.noise_sigma = 1.5` produces a `Measurement`
- **THEN** `measurement.noise_sigma == 1.5`

### Requirement: Sensor Protocol
The system SHALL provide a `Sensor` protocol with `name` (str), `spec` (SensorSpec), `should_sample(t_sec, last_fire_sec)` (bool), and `sample(node, env, t_sec, rng)` (`Measurement | None`) members. Any class that implements these members SHALL satisfy the protocol without inheritance.

#### Scenario: Structural subtyping
- **WHEN** a class implements `name`, `spec`, `should_sample`, and `sample` with matching signatures
- **THEN** it satisfies the `Sensor` protocol under `isinstance` with `runtime_checkable` or under static type-checking

### Requirement: Sensor Environment Carries Scenario Context
The system SHALL provide a `SensorEnv` frozen dataclass carrying scenario-level context available to every sensor's `sample` call:

- `enu_origin_lat_deg: float` and `enu_origin_lon_deg: float` — the geographic anchor of the ENU frame that node state positions (`state[0:2]`) are expressed in. The scenario generator SHALL populate these from the scenario bbox; sensors SHALL read them unconditionally when converting ENU meters to lat/lon (GPS) or to a map query point (BathyProbe).
- `dt_sec: float` — the generator's tick duration in seconds. Required so that the IMU can compute truth acceleration and yaw rate as per-tick finite differences. Construction SHALL reject `dt_sec <= 0`.
- `regional_map: RegionalMap | None` — the truth regional map. Required by BathyProbe; optional for sensors that don't consult bathymetry / coastline.
- `fleet: tuple[Node, ...] | None` — the full fleet, required for LoraTOA's `sample_all_pairs`; optional otherwise.

Sensors SHALL NOT reach into profile components to recover the ENU origin (e.g., reading `MooredPoseSpec.anchor_lat_deg` as a proxy). The ENU origin is scenario-level, not per-node, and is owned by `SensorEnv`.

#### Scenario: SensorEnv requires enu_origin and dt_sec
- **WHEN** a `SensorEnv` is constructed with `enu_origin_lat_deg=36.5`, `enu_origin_lon_deg=-122.0`, `dt_sec=1.0`
- **THEN** construction succeeds and the fields are readable on the returned instance

#### Scenario: SensorEnv rejects non-positive dt_sec
- **WHEN** a `SensorEnv` is constructed with `dt_sec=0.0` (or any negative value)
- **THEN** construction raises `ValueError`

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
The system SHALL provide a `GPSSensor` class whose `sample` produces a `Measurement` with `sensor_name="gps"` and `value=(lat_deg, lon_deg)` computed from the node's ENU position (`state[0:2]`) converted to lat/lon about the ENU origin in `SensorEnv` (`enu_origin_lat_deg`, `enu_origin_lon_deg`), plus Gaussian noise (σ in meters converted to degrees at the resulting latitude). `unit="deg"`. GPS SHALL raise `CapabilityViolation` on any node whose profile does not include a `"gps"` sensor. GPS SHALL NOT read `MooredPoseSpec` to recover an ENU reference — the reference is always `env.enu_origin_*`.

#### Scenario: GPS measurement matches truth plus noise about the env ENU origin
- **WHEN** `GPSSensor` is sampled on an anchor node at `state[0:2] = (0, 0)` with `env.enu_origin_lat_deg=36.5, env.enu_origin_lon_deg=-122.0`, RNG seeded deterministically
- **THEN** the returned measurement's `value` is within 3σ of `(36.5, -122.0)` (σ converted from meters to degrees at ~36.5 lat)

#### Scenario: GPS on drifter raises CapabilityViolation
- **WHEN** `GPSSensor.sample(...)` is called on a `PureDrifterNode`
- **THEN** `CapabilityViolation` is raised

### Requirement: IMUSensor
The system SHALL provide an `IMUSensor` class whose `sample` produces a
`Measurement` with `sensor_name="imu"`, `unit="m/s^2;rad/s"`, and `value`
of length 6 (accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z). Truth
signals SHALL be derived from the per-tick state snapshot captured by
`propagate_truth`:

- Truth linear acceleration SHALL be computed as
  `(velocity - prev_velocity) / env.dt_sec`.
- Truth yaw (z-axis) angular rate SHALL be computed as the shortest-arc
  heading difference divided by `env.dt_sec`.
- Truth roll-rate and pitch-rate channels (gyro_x, gyro_y) SHALL be zero
  in M1.

The accel channels (`accel_x`, `accel_y`, `accel_z`) SHALL each add their
corresponding bias and independent Gaussian noise with σ =
`SensorSpec.noise_sigma` (interpreted as accel-channel sigma in m/s² for
IMU sensors). The gyro channels (`gyro_x`, `gyro_y`, `gyro_z`) SHALL each
add their corresponding bias and independent Gaussian noise with σ =
`SensorSpec.noise_sigma_secondary` (gyro-channel sigma in rad/s, IMU-only
field; non-IMU `SensorSpec` instances SHALL leave it as `None`). The
two-sigma split reflects the physical reality that accelerometer and
gyroscope noise have different units (m/s² vs. rad/s) and different
real-world characteristics; previous single-sigma application was a
shape limitation, not a calibration choice. IMU SHALL raise
`CapabilityViolation` on any node whose profile does not include an
`"imu"` sensor. IMU `SensorSpec` whose `noise_sigma_secondary` is
`None` SHALL raise `ValueError` at construction (the gyro sigma is
required for IMU sensors).

#### Scenario: IMU measurement has 6-element value and fixed unit string
- **WHEN** `IMUSensor.sample(...)` is called on any node class whose profile carries an imu sensor
- **THEN** the returned measurement's `value` is a tuple of length 6
- **AND** the `unit` string is `"m/s^2;rad/s"`

#### Scenario: Accel channel reports velocity delta divided by dt_sec
- **WHEN** `IMUSensor.sample(...)` is called on a node whose `velocity = (0.6, 0, 0)` and `prev_velocity = (0.4, 0, 0)` with `env.dt_sec = 0.5` and zero bias
- **THEN** the reported accel_x is within `3 * SensorSpec.noise_sigma` of `(0.6 - 0.4) / 0.5 = 0.4` m/s²

#### Scenario: Accel channel reflects bias when velocity is unchanged
- **WHEN** `IMUSensor.sample(...)` is called on a node with `velocity == prev_velocity` (truth accel = 0) and `accel_bx = 0.5` m/s²
- **THEN** the reported accel_x is within `3 * SensorSpec.noise_sigma` of `0 + 0.5 = 0.5` m/s²

#### Scenario: Gyro z channel reports heading rate from snapshot
- **WHEN** `IMUSensor.sample(...)` is called on a node with `heading = 10°`, `prev_heading = 0°`, `env.dt_sec = 1.0`, zero bias
- **THEN** the reported gyro_z is within `3 * SensorSpec.noise_sigma_secondary` of `(10° * π / 180) / 1.0 ≈ 0.1745` rad/s

#### Scenario: Gyro z handles heading wrap
- **WHEN** `IMUSensor.sample(...)` is called on a node with `heading = 1°` and `prev_heading = 359°` (wrap forward through 0)
- **THEN** the reported gyro_z reflects a positive shortest-arc rotation rate (heading delta is `+2°`, not `−358°`)

#### Scenario: Gyro x/y channels report bias + noise only (no truth rate in M1)
- **WHEN** `IMUSensor.sample(...)` is called on a node with `gyro_bx = 0.1 rad/s` and `gyro_by = -0.05 rad/s`
- **THEN** gyro_x is within `3 * SensorSpec.noise_sigma_secondary` of `0.1` and gyro_y is within `3 * SensorSpec.noise_sigma_secondary` of `−0.05`

#### Scenario: Accel and gyro sigmas are independent
- **WHEN** an `IMUSensor` is constructed with `SensorSpec(noise_sigma=0.1, noise_sigma_secondary=0.001, ...)` (large accel sigma, small gyro sigma) and `IMUSensor.sample` is called many times on a node with zero bias and zero truth motion
- **THEN** the empirical standard deviation of `accel_x` over the samples approaches `0.1` m/s²
- **AND** the empirical standard deviation of `gyro_z` over the samples approaches `0.001` rad/s

#### Scenario: IMU SensorSpec without secondary sigma is rejected
- **WHEN** a `SensorSpec(name="imu", noise_sigma=0.01, ...)` is constructed without setting `noise_sigma_secondary`
- **THEN** the spec construction (or the IMU sensor construction that consumes it) raises `ValueError` naming the missing field

### Requirement: SensorSpec Carries Optional Secondary Sigma for Multi-Channel Sensors
`SensorSpec` SHALL gain an optional field
`noise_sigma_secondary: float | None = None` to accommodate sensors
whose channels have different units (currently IMU's accel m/s² vs.
gyro rad/s). The field SHALL be `None` for non-IMU sensors and SHALL
be a positive float for IMU `SensorSpec` instances. Construction SHALL
reject `noise_sigma_secondary` values that are negative or zero (when
not `None`). `SensorSpec.__post_init__` SHALL also reject IMU specs
(`name == "imu"`) whose `noise_sigma_secondary` is `None`.

#### Scenario: Non-IMU SensorSpec defaults secondary sigma to None
- **WHEN** `SensorSpec(name="gps", noise_sigma=1.5, ..., noise_sigma_secondary=None)` is constructed
- **THEN** the spec is returned with `noise_sigma_secondary is None`

#### Scenario: IMU SensorSpec requires secondary sigma
- **WHEN** `SensorSpec(name="imu", noise_sigma=0.01, ...)` is constructed without `noise_sigma_secondary`
- **THEN** `ValueError` is raised naming `noise_sigma_secondary` as required for IMU

#### Scenario: Negative secondary sigma rejected
- **WHEN** `SensorSpec(..., noise_sigma_secondary=-0.001, ...)` is constructed
- **THEN** `ValueError` is raised

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
The system SHALL provide a `BathyProbeSensor` class whose `sample` produces a `Measurement` with `sensor_name="bathy_probe"`, `value=(depth_m,)`, and `unit="m"`. The node's ENU position SHALL be converted to lat/lon using `env.enu_origin_lat_deg`, `env.enu_origin_lon_deg` — the same convention as `GPSSensor`. BathyProbe SHALL NOT reach into `MooredPoseSpec` or fall back to a bathymetry-bbox midpoint when the node has no moored pose; the ENU origin is always taken from `SensorEnv`. If the truth `regional_map.depth_at(lat, lon)` returns NaN (node is on land), `sample` SHALL return `None`. Otherwise, the returned depth SHALL be the true depth value plus Gaussian noise with σ from the `SensorSpec`.

#### Scenario: Bathy probe at offshore point returns positive depth
- **WHEN** `BathyProbeSensor.sample(...)` is called on a node at a known-offshore ENU position with the env ENU origin matching the scenario's frame, producing a bathymetric depth of 500 m
- **THEN** the returned measurement's value is within 3σ of 500 m

#### Scenario: Bathy probe on land returns None
- **WHEN** `BathyProbeSensor.sample(...)` is called on a node at an ENU position whose lat/lon (via `env.enu_origin_*`) satisfies `regional_map.is_on_land == True`
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
