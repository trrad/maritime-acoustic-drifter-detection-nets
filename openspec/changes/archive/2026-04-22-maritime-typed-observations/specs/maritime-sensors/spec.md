## MODIFIED Requirements

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

#### Scenario: Measurement carries primary sigma
- **WHEN** any sensor with `SensorSpec.noise_sigma = 1.5` produces a `Measurement`
- **THEN** `measurement.noise_sigma == 1.5`

#### Scenario: Measurement sensor_name vocabulary
- **WHEN** any sensor produces a `Measurement`
- **THEN** the `sensor_name` field is one of `{"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"}`

## ADDED Requirements

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
