## ADDED Requirements

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

## MODIFIED Requirements

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
the M1 fleet: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`,
and `PURE_DRIFTER_PROFILE`. Profile `state_dim` values SHALL match
`docs/maritime_buoy_design.md`: anchor 25, ballast drifter 21, pure
drifter 15.

Blueprint distinctions SHALL be expressed through the `components`
tuple rather than through boolean flags:

- `ANCHOR_PROFILE.components` SHALL include a spec with
  `kind == "moored_pose"` and a spec with `kind == "satellite_uplink"`,
  and SHALL NOT include `"ballast_pump"` or
  `"ballast_drifting_pose"` or `"drifting_surface_pose"`.
- `BALLAST_DRIFTER_PROFILE.components` SHALL include a spec with
  `kind == "ballast_pump"` (carrying its `capacity_ml`,
  `pump_rate_ml_per_s`, and `avg_power_mw`) and a spec with
  `kind == "ballast_drifting_pose"`, and SHALL NOT include
  `"moored_pose"`, `"satellite_uplink"`, or `"drifting_surface_pose"`.
- `PURE_DRIFTER_PROFILE.components` SHALL include a spec with
  `kind == "drifting_surface_pose"`, and SHALL NOT include
  `"ballast_pump"`, `"moored_pose"`, `"satellite_uplink"`, or
  `"ballast_drifting_pose"`.

Sensor composition SHALL satisfy: the anchor profile SHALL include a
GPS sensor; the ballast drifter and pure drifter profiles SHALL NOT.
Each profile SHALL construct successfully (its own invariants hold).

#### Scenario: State dimensions match the design doc
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.state_dim == 25`
- **AND** `BALLAST_DRIFTER_PROFILE.state_dim == 21`
- **AND** `PURE_DRIFTER_PROFILE.state_dim == 15`

#### Scenario: Anchor profile has moored_pose and satellite_uplink components
- **WHEN** `ANCHOR_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "moored_pose"`
- **AND** exactly one spec has `kind == "satellite_uplink"`
- **AND** no spec has `kind == "ballast_pump"`
- **AND** no spec has `kind == "drifting_surface_pose"`
- **AND** no spec has `kind == "ballast_drifting_pose"`

#### Scenario: Ballast drifter profile has pump and ballast_drifting_pose components
- **WHEN** `BALLAST_DRIFTER_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "ballast_pump"`
- **AND** exactly one spec has `kind == "ballast_drifting_pose"`
- **AND** no spec has `kind == "moored_pose"`
- **AND** no spec has `kind == "satellite_uplink"`
- **AND** no spec has `kind == "drifting_surface_pose"`

#### Scenario: Pure drifter profile has only drifting_surface_pose physics component
- **WHEN** `PURE_DRIFTER_PROFILE.components` is inspected
- **THEN** exactly one spec has `kind == "drifting_surface_pose"`
- **AND** no spec has `kind == "ballast_pump"`
- **AND** no spec has `kind == "moored_pose"`
- **AND** no spec has `kind == "satellite_uplink"`
- **AND** no spec has `kind == "ballast_drifting_pose"`

#### Scenario: Pure drifter has no GPS sensor
- **WHEN** `PURE_DRIFTER_PROFILE.sensors` is inspected
- **THEN** no `SensorSpec` in the tuple has `name == "gps"`

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
