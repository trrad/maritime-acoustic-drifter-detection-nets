## ADDED Requirements

### Requirement: Sensor Capability Envelope
The system SHALL provide a `SensorSpec` type that encodes one sensor's capability envelope: name, observed state dimension, noise standard deviation (with unit label), maximum update rate, duty cycle, and average power. The type SHALL be immutable after construction. Construction SHALL reject physically nonsensical values.

#### Scenario: Valid sensor spec constructs successfully
- **WHEN** a `SensorSpec` is constructed with `name="gps"`, `observed_dim=0`, `noise_sigma=1.5`, `noise_unit="m"`, `max_rate_hz=1.0`, `duty_cycle=0.01`, `avg_power_mw=10.0`
- **THEN** all field accesses return the provided values
- **AND** attempting to mutate any field raises an error (immutable)

#### Scenario: Negative noise_sigma is rejected
- **WHEN** a `SensorSpec` is constructed with `noise_sigma=-0.1`
- **THEN** construction raises `ValueError` citing the field name and the offending value

#### Scenario: Duty cycle outside [0, 1] is rejected
- **WHEN** a `SensorSpec` is constructed with `duty_cycle=1.5`
- **THEN** construction raises `ValueError`
- **AND** when constructed with `duty_cycle=-0.01`, construction raises `ValueError`

#### Scenario: Zero or negative max_rate_hz is rejected
- **WHEN** a `SensorSpec` is constructed with `max_rate_hz=0`
- **THEN** construction raises `ValueError`

### Requirement: Comms Capability Envelope
The system SHALL provide a `CommsProfile` type that encodes the node's LoRa TDMA comms capability: per-slot window length, full TDMA frame length, realistic maximum range, ranging sigma, per-slot packet bit capacity, packet loss rate, and average power. Construction SHALL reject slot lengths exceeding the TDMA frame, non-positive ranges or powers, and packet loss rates outside [0, 1].

#### Scenario: Valid comms profile constructs successfully
- **WHEN** a `CommsProfile` is constructed with `slot_length_sec=0.05`, `tdma_period_sec=3600`, `max_range_m=15000`, `ranging_sigma_m=20`, `packet_bits=256`, `packet_loss_rate=0.1`, `avg_power_mw=0.22`
- **THEN** all field accesses return the provided values

#### Scenario: Slot length exceeds TDMA period is rejected
- **WHEN** a `CommsProfile` is constructed with `slot_length_sec=10.0, tdma_period_sec=5.0`
- **THEN** construction raises `ValueError`

#### Scenario: Non-positive range is rejected
- **WHEN** a `CommsProfile` is constructed with `max_range_m=0`
- **THEN** construction raises `ValueError`

#### Scenario: Packet loss rate outside [0, 1] is rejected
- **WHEN** a `CommsProfile` is constructed with `packet_loss_rate=-0.01`
- **THEN** construction raises `ValueError`
- **AND** when constructed with `packet_loss_rate=1.5`, construction raises `ValueError`

### Requirement: Compute Budget Fits Clock and Update Rate
The system SHALL provide a `ComputeBudget` type encoding FPGA clock frequency (MHz), cycles per PF step, PF update rate (Hz), a headroom factor, and average power. Construction SHALL reject budgets where `cycles_per_step * pf_update_rate_hz > clock_mhz * 1e6 * headroom`, because such a budget cannot meet its declared update rate.

#### Scenario: Budget within clock capacity is accepted
- **WHEN** a `ComputeBudget` is constructed with `clock_mhz=6`, `cycles_per_step=33000`, `pf_update_rate_hz=1.0`, `headroom=0.8`
- **THEN** construction succeeds and all fields are accessible
- **AND** `cycles_per_step * pf_update_rate_hz` (33 000) is less than `clock_mhz * 1e6 * headroom` (4 800 000)

#### Scenario: Budget exceeding capacity is rejected
- **WHEN** a `ComputeBudget` is constructed with `clock_mhz=1`, `cycles_per_step=2_000_000`, `pf_update_rate_hz=1.0`, `headroom=0.8`
- **THEN** construction raises `ValueError` naming the capacity overshoot

#### Scenario: Non-positive clock is rejected
- **WHEN** a `ComputeBudget` is constructed with `clock_mhz=0`
- **THEN** construction raises `ValueError`

#### Scenario: Headroom outside (0, 1] is rejected
- **WHEN** a `ComputeBudget` is constructed with `headroom=0`
- **THEN** construction raises `ValueError`
- **AND** when constructed with `headroom=1.5`, construction raises `ValueError`

### Requirement: Node Profile Composes Capabilities
The system SHALL provide a `NodeProfile` type composing `class_name`, `state_dim`, an ordered collection of `SensorSpec` values, a `CommsProfile`, a `ComputeBudget`, `total_power_budget_mw`, and the discrete-tier capability flags `has_pump`, `ballast_capacity_ml`, `is_moored`, and `has_satellite_uplink`. The profile SHALL be immutable. Construction SHALL reject profiles where sensors share a name, where `state_dim` is non-positive, where the sum of sensor, comms, and compute average powers exceeds `total_power_budget_mw`, where `has_pump=False` but `ballast_capacity_ml > 0`, or where `ballast_capacity_ml` is negative.

#### Scenario: Valid node profile constructs successfully
- **WHEN** a `NodeProfile` is constructed with unique sensor names, positive state_dim, and total sensor+comms+compute average power ≤ `total_power_budget_mw`
- **THEN** construction succeeds
- **AND** `profile.total_avg_power_mw` equals the sum of sensor, comms, and compute average powers

#### Scenario: Duplicate sensor names are rejected
- **WHEN** a `NodeProfile` is constructed with two `SensorSpec` values both named `"imu"`
- **THEN** construction raises `ValueError` naming the duplicated sensor name

#### Scenario: Power budget overshoot is rejected
- **WHEN** a `NodeProfile` is constructed where sensor + comms + compute average powers sum to 10 mW but `total_power_budget_mw` is 5 mW
- **THEN** construction raises `ValueError` naming the overshoot

#### Scenario: Sensor lookup by name
- **WHEN** `profile.sensor("gps")` is called on a profile that includes a `"gps"` sensor
- **THEN** the matching `SensorSpec` is returned
- **AND** `profile.sensor("nonexistent")` raises `KeyError`

#### Scenario: Pump-ballast consistency is enforced
- **WHEN** a `NodeProfile` is constructed with `has_pump=False` and `ballast_capacity_ml=30.0`
- **THEN** construction raises `ValueError` naming the pump/ballast inconsistency
- **AND** construction with `has_pump=False, ballast_capacity_ml=0.0` succeeds
- **AND** construction with `has_pump=True, ballast_capacity_ml=30.0` succeeds

#### Scenario: Negative ballast capacity is rejected
- **WHEN** a `NodeProfile` is constructed with `ballast_capacity_ml=-1.0`
- **THEN** construction raises `ValueError`

### Requirement: Bundled M1 Fleet Profiles
The system SHALL export three module-level `NodeProfile` constants for the M1 Monterey Bay fleet: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, and `PURE_DRIFTER_PROFILE`. The profile numbers SHALL be consistent with `docs/maritime_buoy_design.md`: anchor `state_dim=25`, ballast drifter `state_dim=21`, pure drifter `state_dim=15`. Active ballast pump presence (`has_pump`) SHALL be the discriminator between the two non-anchor classes: `BALLAST_DRIFTER_PROFILE.has_pump == True`, `PURE_DRIFTER_PROFILE.has_pump == False`. The anchor profile SHALL be the only profile with `is_moored == True` and `has_satellite_uplink == True`. Each profile SHALL construct successfully (i.e., its own invariants hold). A pure drifter profile SHALL NOT include a `"gps"` sensor. An anchor profile SHALL include a `"gps"` sensor. A ballast drifter profile SHALL NOT include a `"gps"` sensor.

#### Scenario: State dimensions match the design doc
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.state_dim == 25`
- **AND** `BALLAST_DRIFTER_PROFILE.state_dim == 21`
- **AND** `PURE_DRIFTER_PROFILE.state_dim == 15`

#### Scenario: Pure drifter has no GPS sensor
- **WHEN** `PURE_DRIFTER_PROFILE.sensors` is inspected
- **THEN** no `SensorSpec` in the tuple has `name == "gps"`

#### Scenario: Anchor has a GPS sensor
- **WHEN** `ANCHOR_PROFILE.sensors` is inspected
- **THEN** exactly one `SensorSpec` has `name == "gps"`

#### Scenario: Ballast drifter has no GPS sensor
- **WHEN** `BALLAST_DRIFTER_PROFILE.sensors` is inspected
- **THEN** no `SensorSpec` in the tuple has `name == "gps"`

#### Scenario: Pump discriminator distinguishes non-anchor classes
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.has_pump == False`
- **AND** `BALLAST_DRIFTER_PROFILE.has_pump == True` with `ballast_capacity_ml > 0`
- **AND** `PURE_DRIFTER_PROFILE.has_pump == False` with `ballast_capacity_ml == 0.0`

#### Scenario: Anchor is the only moored, satellite-equipped profile
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.is_moored == True` and `ANCHOR_PROFILE.has_satellite_uplink == True`
- **AND** `BALLAST_DRIFTER_PROFILE.is_moored == False` and `BALLAST_DRIFTER_PROFILE.has_satellite_uplink == False`
- **AND** `PURE_DRIFTER_PROFILE.is_moored == False` and `PURE_DRIFTER_PROFILE.has_satellite_uplink == False`

#### Scenario: Total average power is within the declared budget
- **WHEN** each bundled profile is inspected
- **THEN** `profile.total_avg_power_mw <= profile.total_power_budget_mw`

#### Scenario: Pure drifter power budget is under 2 mW
- **WHEN** `PURE_DRIFTER_PROFILE.total_power_budget_mw` is inspected
- **THEN** the value is at most 2.0

#### Scenario: Ballast drifter power budget is under 5 mW
- **WHEN** `BALLAST_DRIFTER_PROFILE.total_power_budget_mw` is inspected
- **THEN** the value is at most 5.0

#### Scenario: Anchor power budget is under 50 mW
- **WHEN** `ANCHOR_PROFILE.total_power_budget_mw` is inspected
- **THEN** the value is at most 50.0

### Requirement: Capability Violation Exception
The system SHALL provide a `CapabilityViolation` exception type in the `platform_profile` module. The exception SHALL inherit from `Exception`. It SHALL be raisable with a message string naming the violated profile and the violating behavior. This module SHALL NOT raise `CapabilityViolation` itself; the exception exists for downstream modules (sensor, comms, PF) to raise at runtime.

#### Scenario: Exception is importable and raisable
- **WHEN** `from rtl.vectors.maritime.platform_profile import CapabilityViolation` is executed
- **THEN** `CapabilityViolation` is available
- **AND** `raise CapabilityViolation("drifter has no GPS")` raises an exception whose string form contains the message

#### Scenario: Exception inherits from Exception
- **WHEN** `CapabilityViolation` is inspected
- **THEN** it is a subclass of `Exception`
