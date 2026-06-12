## MODIFIED Requirements

### Requirement: Bundled M1 Fleet Profiles
The system SHALL export three module-level `NodeProfile` constants for
the M1 fleet: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, and
`PURE_DRIFTER_PROFILE`. Profile `state_dim` values SHALL match
`docs/maritime_buoy_design.md`: anchor 25, ballast drifter 21, pure
drifter 15.

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

Sensor composition SHALL satisfy: the anchor profile SHALL include a
GPS sensor; the ballast drifter and pure drifter profiles SHALL NOT.
Each profile SHALL construct successfully (its own invariants hold).

#### Scenario: State dimensions match the design doc
- **WHEN** the three bundled profiles are inspected
- **THEN** `ANCHOR_PROFILE.state_dim == 25`
- **AND** `BALLAST_DRIFTER_PROFILE.state_dim == 21`
- **AND** `PURE_DRIFTER_PROFILE.state_dim == 15`

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
