## Purpose

Frozen dataclasses describing maritime node capabilities (sensors, comms, compute, power) and bundled M1 fleet profiles. Pure data — no methods touching `CurrentField` or `RegionalMap`.

## Requirements

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

### Requirement: Component Spec Protocol
The system SHALL provide a `ComponentSpec` protocol (via
`typing.Protocol`, `runtime_checkable`) with two required attributes:
`kind: str` — a discriminator identifying the component type, e.g.,
`"ballast_pump"`, `"moored_pose"`, `"drifting_surface_pose"`,
`"ballast_drifting_pose"`, `"satellite_uplink"` — and
`avg_power_mw: float` — the component's contribution to the profile's
power budget. Any frozen dataclass exposing these attributes SHALL
conform to the protocol. Concrete component spec types (e.g.,
`BallastSpec`, `MooredPoseSpec`) carry additional component-specific
fields and SHALL be defined by the change that introduces them; the
base protocol does not enumerate concrete types.

#### Scenario: Frozen dataclass with kind and avg_power_mw conforms
- **WHEN** a frozen dataclass is defined with `kind: ClassVar[str] = "ballast_pump"` and an `avg_power_mw: float` field, plus any additional component-specific fields
- **THEN** an instance of that dataclass satisfies `isinstance(spec, ComponentSpec)`

#### Scenario: Class missing kind attribute does not conform
- **WHEN** a class is defined with `avg_power_mw: float` but no `kind` attribute
- **THEN** instances of that class do NOT satisfy `isinstance(spec, ComponentSpec)`

#### Scenario: Class with kind but no avg_power_mw does not conform
- **WHEN** a class is defined with `kind: str` but no `avg_power_mw` attribute
- **THEN** instances of that class do NOT satisfy `isinstance(spec, ComponentSpec)`

### Requirement: Node Profile Composes Capabilities
The system SHALL provide a `NodeProfile` type composing `class_name`,
`state_dim`, an ordered tuple of `SensorSpec` values, a `CommsProfile`,
a `ComputeBudget`, `total_power_budget_mw`, and a
`components: tuple[ComponentSpec, ...]` field carrying the node's
physics/hardware components (poses, pumps, clocks, uplinks — anything
whose presence parameterizes capability). The profile SHALL be
immutable. Construction SHALL reject profiles where sensors share a
name, where components share a `kind`, where `state_dim` is
non-positive, or where the sum of sensor, comms, compute, and component
average powers exceeds `total_power_budget_mw`. Capability queries on
a constructed profile SHALL read through `profile.sensor(name)` and
`profile.component(kind)` accessors; no boolean capability flags are
carried on the profile directly.

The boolean fields `has_pump`, `is_moored`, `has_satellite_uplink`
and the numeric field `ballast_capacity_ml` that existed in prior
versions of this requirement ARE REMOVED from `NodeProfile`. Their
semantic content migrates to component-presence queries:
- `has_pump` → `profile.component("ballast_pump")` resolves vs. raises
  `KeyError`.
- `is_moored` → `profile.component("moored_pose")` resolves.
- `has_satellite_uplink` → `profile.component("satellite_uplink")`
  resolves.
- `ballast_capacity_ml` → `BallastSpec.capacity_ml` on the pump
  component (defined in `maritime-fleet-dynamics`).

Call-site utility helpers (`has_pump(node)`, `is_moored(node)`,
`has_satellite_uplink(node)`) are defined in `maritime-fleet-dynamics`
for ergonomic access on `Node` instances at runtime.

#### Scenario: Valid node profile constructs successfully
- **WHEN** a `NodeProfile` is constructed with unique sensor names, unique component kinds, positive `state_dim`, and total sensor + comms + compute + component average power ≤ `total_power_budget_mw`
- **THEN** construction succeeds
- **AND** `profile.total_avg_power_mw` equals the sum of sensor, comms, compute, and component average powers

#### Scenario: Duplicate sensor names are rejected
- **WHEN** a `NodeProfile` is constructed with two `SensorSpec` values both named `"imu"`
- **THEN** construction raises `ValueError` naming the duplicated sensor name

#### Scenario: Duplicate component kinds are rejected
- **WHEN** a `NodeProfile` is constructed with two `ComponentSpec` values having the same `kind`
- **THEN** construction raises `ValueError` naming the duplicated component kind

#### Scenario: Power budget overshoot is rejected
- **WHEN** a `NodeProfile` is constructed where sensor + comms + compute + component average powers sum to 10 mW but `total_power_budget_mw` is 5 mW
- **THEN** construction raises `ValueError` naming the overshoot

#### Scenario: Sensor lookup by name
- **WHEN** `profile.sensor("gps")` is called on a profile that includes a `"gps"` sensor
- **THEN** the matching `SensorSpec` is returned
- **AND** `profile.sensor("nonexistent")` raises `KeyError`

#### Scenario: Component lookup by kind
- **WHEN** `profile.component("ballast_pump")` is called on a profile that includes a ballast-pump component
- **THEN** the matching `ComponentSpec` is returned
- **AND** `profile.component("nonexistent")` raises `KeyError`

#### Scenario: Profile has no has_pump attribute
- **WHEN** a constructed `NodeProfile` is inspected for a `has_pump` attribute
- **THEN** the attribute does NOT exist (access raises `AttributeError`)
- **AND** the same holds for `is_moored`, `has_satellite_uplink`, and `ballast_capacity_ml`

### Requirement: Bundled M1 Fleet Profiles
The system SHALL export three module-level `NodeProfile` constants for
the M1 fleet: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, and
`PURE_DRIFTER_PROFILE`. Profile `state_dim` values SHALL match
`maritime-state-layout`: pure drifter 19, ballast drifter 21,
anchor 21. (These reflect the post-Tier-3-correction shapes: base
dims + 4-slot per-tick snapshot for prev-velocity/prev-heading;
neighbor_range slots have been removed from the truth layout since
truth ranges are deterministic functions of truth positions.)

`ANCHOR_PROFILE` SHALL serve as a capability template — its
`MooredPoseSpec` carries placeholder `anchor_lat_deg=0.0`,
`anchor_lon_deg=0.0`, `anchor_depth_m=0.0`. Real deployments SHALL
construct per-anchor profiles via the `make_anchor_profile` factory
(Requirement: Anchor Profile Factory) so that each anchor's mooring
coordinates reflect its actual deployment location.

Blueprint distinctions SHALL be expressed through the `components`
tuple rather than through boolean flags. Every bundled profile SHALL
carry a clock component (a `ClockSpec` from `maritime-clock-model`)
with `kind == "clock"`:

- `ANCHOR_PROFILE.components` SHALL include a spec with
  `kind == "moored_pose"`, a spec with `kind == "satellite_uplink"`,
  and a spec with `kind == "clock"`, and SHALL NOT include
  `"ballast_pump"` or `"ballast_drifting_pose"` or
  `"drifting_surface_pose"`.
- `BALLAST_DRIFTER_PROFILE.components` SHALL include a spec with
  `kind == "ballast_pump"` (carrying its `capacity_ml`,
  `pump_rate_ml_per_s`, and `avg_power_mw`), a spec with
  `kind == "ballast_drifting_pose"`, and a spec with
  `kind == "clock"`, and SHALL NOT include `"moored_pose"`,
  `"satellite_uplink"`, or `"drifting_surface_pose"`.
- `PURE_DRIFTER_PROFILE.components` SHALL include a spec with
  `kind == "drifting_surface_pose"` and a spec with
  `kind == "clock"`, and SHALL NOT include `"ballast_pump"`,
  `"moored_pose"`, `"satellite_uplink"`, or
  `"ballast_drifting_pose"`.

Every bundled profile's `ClockSpec` SHALL have `drift_ppm == 0.0` and
`avg_power_mw == 0.0` in M1. These parameter values produce an
identity wall-time readout (see `maritime-clock-model` Requirement:
Wall Clock Readout) and reflect that M2 will supply real
crystal-datasheet values when sync-mechanism components land.

Sensor composition SHALL satisfy the following class-by-class
suites, so that the scenario generator can exercise every declared
sensor on every node class without runtime capability violations:

- `ANCHOR_PROFILE.sensors` SHALL include: `"gps"`, `"imu"`, `"baro"`,
  `"mag"`, `"lora_toa"`. (No `"bathy_probe"` — the anchor is fixed
  in place and does not use map-aided bathymetry navigation.)
- `BALLAST_DRIFTER_PROFILE.sensors` SHALL include: `"imu"`, `"baro"`,
  `"mag"`, `"bathy_probe"`, `"lora_toa"`. (No `"gps"` — the ballast
  drifter loses GPS lock when submerged and relies on LoRa TOA +
  bathymetry for localization.)
- `PURE_DRIFTER_PROFILE.sensors` SHALL include: `"imu"`, `"baro"`,
  `"mag"`, `"bathy_probe"`, `"lora_toa"`. (No `"gps"` — the pure
  drifter is the simplest low-power class and skips GPS to stay
  under its 2 mW budget.)

Each profile SHALL construct successfully (its own invariants hold,
including the power-budget-fits check with the full sensor suite
accounted for).

#### Scenario: State dimensions match the design doc
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.state_dim == 21`
- **AND** `BALLAST_DRIFTER_PROFILE.state_dim == 21`
- **AND** `PURE_DRIFTER_PROFILE.state_dim == 19`

#### Scenario: Anchor profile has moored_pose, satellite_uplink, and clock components
- **WHEN** `ANCHOR_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "moored_pose"`
- **AND** exactly one spec has `kind == "satellite_uplink"`
- **AND** exactly one spec has `kind == "clock"`
- **AND** no spec has `kind == "ballast_pump"`
- **AND** no spec has `kind == "drifting_surface_pose"`
- **AND** no spec has `kind == "ballast_drifting_pose"`

#### Scenario: Ballast drifter profile has pump, ballast_drifting_pose, and clock components
- **WHEN** `BALLAST_DRIFTER_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "ballast_pump"`
- **AND** exactly one spec has `kind == "ballast_drifting_pose"`
- **AND** exactly one spec has `kind == "clock"`
- **AND** no spec has `kind == "moored_pose"`
- **AND** no spec has `kind == "satellite_uplink"`
- **AND** no spec has `kind == "drifting_surface_pose"`

#### Scenario: Pure drifter profile has drifting_surface_pose and clock components
- **WHEN** `PURE_DRIFTER_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "drifting_surface_pose"`
- **AND** exactly one spec has `kind == "clock"`
- **AND** no spec has `kind == "ballast_pump"`
- **AND** no spec has `kind == "moored_pose"`
- **AND** no spec has `kind == "satellite_uplink"`
- **AND** no spec has `kind == "ballast_drifting_pose"`

#### Scenario: Bundled profile ClockSpec carries zero drift and zero power in M1
- **WHEN** each bundled profile's `ClockSpec` is inspected (`profile.component("clock")`)
- **THEN** `spec.drift_ppm == 0.0`
- **AND** `spec.avg_power_mw == 0.0`

#### Scenario: Anchor carries the full sensor suite except bathy_probe
- **WHEN** `ANCHOR_PROFILE.sensors` is inspected
- **THEN** sensor `name`s include `"gps"`, `"imu"`, `"baro"`, `"mag"`, `"lora_toa"`
- **AND** no spec has `name == "bathy_probe"`

#### Scenario: Ballast drifter carries the full sensor suite except GPS
- **WHEN** `BALLAST_DRIFTER_PROFILE.sensors` is inspected
- **THEN** sensor `name`s include `"imu"`, `"baro"`, `"mag"`, `"bathy_probe"`, `"lora_toa"`
- **AND** no spec has `name == "gps"`

#### Scenario: Pure drifter carries the full sensor suite except GPS
- **WHEN** `PURE_DRIFTER_PROFILE.sensors` is inspected
- **THEN** sensor `name`s include `"imu"`, `"baro"`, `"mag"`, `"bathy_probe"`, `"lora_toa"`
- **AND** no spec has `name == "gps"`

#### Scenario: Anchor has a GPS sensor
- **WHEN** `ANCHOR_PROFILE.sensors` is inspected
- **THEN** exactly one `SensorSpec` has `name == "gps"`

#### Scenario: Ballast drifter has no GPS sensor
- **WHEN** `BALLAST_DRIFTER_PROFILE.sensors` is inspected
- **THEN** no `SensorSpec` in the tuple has `name == "gps"`

#### Scenario: Total average power is within each profile's declared budget
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

### Requirement: Anchor Profile Factory
The system SHALL provide a `make_anchor_profile(anchor_lat_deg: float, anchor_lon_deg: float, anchor_depth_m: float = 0.0) -> NodeProfile` factory that returns a freshly-constructed anchor-class profile (same shape as `ANCHOR_PROFILE`) whose `MooredPoseSpec` carries the provided mooring coordinates. Each call SHALL return a new `NodeProfile` instance (not a shared module-level constant) so that per-anchor mooring coords can differ within a fleet. The returned profile SHALL satisfy all `NodeProfile` invariants (sensor uniqueness, component-kind uniqueness, power-budget sum), SHALL include GPS in its `sensors`, and SHALL include a `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` component.

#### Scenario: Factory returns a profile with the requested mooring coordinates
- **WHEN** `make_anchor_profile(anchor_lat_deg=36.5, anchor_lon_deg=-122.0)` is called
- **THEN** the returned profile's `MooredPoseSpec` has `anchor_lat_deg == 36.5` and `anchor_lon_deg == -122.0`

#### Scenario: Factory returns distinct instances per call
- **WHEN** `make_anchor_profile(lat, lon)` is called twice with the same arguments
- **THEN** the two returned profiles compare equal but `profile1 is not profile2`

#### Scenario: Distinct anchors can carry distinct mooring coordinates
- **WHEN** two profiles are constructed via `make_anchor_profile(36.0, -122.5)` and `make_anchor_profile(36.5, -122.0)`
- **THEN** their respective `MooredPoseSpec` components have different `anchor_lat_deg` and `anchor_lon_deg` values

#### Scenario: Factory output carries GPS + clock + state_dim invariants
- **WHEN** `make_anchor_profile(lat, lon)` is called with any valid coords
- **THEN** the returned profile's `sensors` includes a GPS spec
- **AND** its `components` includes a `ClockSpec` with `drift_ppm == 0.0` and `avg_power_mw == 0.0`
- **AND** its `state_dim == 29`

### Requirement: Capability Violation Exception
The system SHALL provide a `CapabilityViolation` exception type in the `platform_profile` module. The exception SHALL inherit from `Exception`. It SHALL accept keyword-only arguments `node_class: str`, `sensor_name: str`, and `reason: str`, storing them as attributes. The string representation SHALL include the sensor name and node class. This module SHALL NOT raise `CapabilityViolation` itself; the exception exists for downstream modules (sensor, comms, PF) to raise at runtime.

#### Scenario: Exception carries structured diagnostic fields
- **WHEN** `CapabilityViolation(node_class="drifter", sensor_name="gps", reason="not available")` is raised
- **THEN** `exc.node_class == "drifter"` and `exc.sensor_name == "gps"` and `exc.reason == "not available"`
- **AND** `str(exc)` contains `"gps"` and `"drifter"`

#### Scenario: Exception inherits from Exception
- **WHEN** `CapabilityViolation` is inspected
- **THEN** it is a subclass of `Exception`
