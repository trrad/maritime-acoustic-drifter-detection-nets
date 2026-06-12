## Purpose

Frozen dataclasses describing state vector layouts for maritime node types. Provides `StateField` and `StateLayout` types plus bundled M1 layouts for the three node blueprints. Pure data — no methods touching `CurrentField`, `RegionalMap`, or `Node`.

## Requirements

### Requirement: State Field Descriptor
The system SHALL provide a `StateField` type encoding one entry in a state vector: a `name` string, a `unit` string (e.g., `"m"`, `"m/s"`, `"deg"`), and a free-text `description`. The type SHALL be immutable and hashable.

#### Scenario: Valid state field constructs successfully
- **WHEN** a `StateField` is constructed with `name="heading_deg"`, `unit="deg"`, `description="compass heading of the node"`
- **THEN** all field accesses return the provided values
- **AND** mutation attempts raise an error (immutable)

#### Scenario: State field is hashable
- **WHEN** two `StateField` values with identical `name`, `unit`, and `description` are constructed
- **THEN** they compare equal
- **AND** they have the same hash

### Requirement: State Layout Structure
The system SHALL provide a `StateLayout` type composing a `class_name` string, an ordered tuple of `StateField` values, and a mapping of group names to slices. The layout SHALL be immutable. The `state_dim` property SHALL equal the length of the fields tuple. Construction SHALL reject layouts with duplicate field names, with group slices outside `[0, state_dim)`, or with groups whose slice ranges overlap inconsistently with the field tuple.

#### Scenario: state_dim equals field count
- **WHEN** a `StateLayout` is constructed with 19 `StateField` values
- **THEN** `layout.state_dim == 19`

#### Scenario: Duplicate field names are rejected
- **WHEN** a `StateLayout` is constructed with two `StateField` values both named `"heading_deg"`
- **THEN** construction raises `ValueError` naming the duplicated field

#### Scenario: Group slice outside state range is rejected
- **WHEN** a `StateLayout` is constructed with 15 fields and a group `"extended"` mapped to `slice(12, 20)`
- **THEN** construction raises `ValueError` citing the out-of-range slice

### Requirement: Previous-Tick Snapshot Fields
Each bundled layout SHALL include a `"prev_velocity"` group (3 m/s fields) and a `"prev_heading"` group (1 deg field). These fields SHALL hold the velocity and heading values captured at the start of each `propagate_truth` tick, before the pump / pose / imu_biases / clock phases mutate velocity or heading. The IMU sensor computes truth linear acceleration as `(velocity - prev_velocity) / dt_sec` and truth yaw rate as the shortest-arc angular difference `((heading - prev_heading + 180) % 360 - 180) / dt_sec`. Gyro x/y channels have no truth signal in M1 (state carries no roll/pitch rate); they report bias + noise only.

#### Scenario: Snapshot groups present in every bundled layout
- **WHEN** any of `PURE_DRIFTER_LAYOUT`, `BALLAST_DRIFTER_LAYOUT`, `ANCHOR_LAYOUT` is inspected
- **THEN** `layout.slice("prev_velocity")` returns a slice of length 3
- **AND** `layout.slice("prev_heading")` returns a slice of length 1

#### Scenario: Snapshot field units match source groups
- **WHEN** the fields in the `"prev_velocity"` and `"prev_heading"` groups are inspected
- **THEN** prev-velocity fields have `unit == "m/s"` (matching `"velocity"`)
- **AND** prev-heading field has `unit == "deg"` (matching `"heading"`)

### Requirement: State Layout Accessors
The `StateLayout` SHALL provide `index_of(field_name)`, `name_at(index)`, and `slice(group_name)` methods. `index_of` SHALL return the integer position of a named field or raise `KeyError`. `name_at` SHALL return the name of the field at a given index or raise `IndexError`. `slice` SHALL return the `slice` object for a named group or raise `KeyError`.

#### Scenario: Lookup by field name
- **WHEN** `layout.index_of("heading_deg")` is called on a layout where `"heading_deg"` is at position 6
- **THEN** the return value is 6

#### Scenario: Lookup of unknown field raises KeyError
- **WHEN** `layout.index_of("nonexistent_dim")` is called
- **THEN** `KeyError` is raised

#### Scenario: Name at index
- **WHEN** `layout.name_at(6)` is called on a layout where index 6 holds `"heading_deg"`
- **THEN** the return value is `"heading_deg"`

#### Scenario: Name at out-of-range index raises IndexError
- **WHEN** `layout.name_at(100)` is called on a 15 D layout
- **THEN** `IndexError` is raised

#### Scenario: Group slice lookup
- **WHEN** `layout.slice("position")` is called on a layout with a `"position"` group mapped to `slice(0, 3)`
- **THEN** the return value is `slice(0, 3)`

#### Scenario: Unknown group raises KeyError
- **WHEN** `layout.slice("nonexistent_group")` is called
- **THEN** `KeyError` is raised

### Requirement: Bundled M1 Layouts
The system SHALL export three module-level `StateLayout` constants: `PURE_DRIFTER_LAYOUT` (state_dim 19), `BALLAST_DRIFTER_LAYOUT` (state_dim 21), and `ANCHOR_LAYOUT` (state_dim 21). All three layouts SHALL define the following named groups at consistent indices: `"position"` (3 fields), `"velocity"` (3 fields), `"heading"` (1 field), `"surface_current"` (2 fields), `"imu_bias"` (6 fields), `"prev_velocity"` (3 fields), `"prev_heading"` (1 field). The ballast drifter and anchor layouts SHALL additionally define `"deep_current"` (2 fields). The `"prev_velocity"` and `"prev_heading"` groups SHALL carry the per-tick snapshot of velocity and heading captured at the start of each `propagate_truth` call so that the IMU sensor can compute truth acceleration and heading rate via finite difference over one tick. The truth state layout SHALL NOT include any range-to-neighbor fields — truth pair-wise ranges are deterministic functions of node truth positions and are computed directly by truth consumers (via `ScenarioTruthReader`); node-side range estimates live in the PF-estimate schema (`maritime-pf-estimate-schema`) and are fed by noisy `LoraTOASensor` observations along the separate observation path. The state-dim values SHALL match the corresponding `NodeProfile` constants in `maritime-platform-profile`: pure drifter 19, ballast drifter 21, anchor 21.

#### Scenario: Layout dimensions match profiles
- **WHEN** the three bundled layouts are inspected alongside the three bundled profiles
- **THEN** `PURE_DRIFTER_LAYOUT.state_dim == PURE_DRIFTER_PROFILE.state_dim == 19`
- **AND** `BALLAST_DRIFTER_LAYOUT.state_dim == BALLAST_DRIFTER_PROFILE.state_dim == 21`
- **AND** `ANCHOR_LAYOUT.state_dim == ANCHOR_PROFILE.state_dim == 21`

#### Scenario: Truth state has no neighbor_range group
- **WHEN** any of the three bundled layouts is asked for `layout.slice("neighbor_range")`
- **THEN** `KeyError` is raised

#### Scenario: Position group is always at indices 0-2
- **WHEN** `layout.slice("position")` is called on any of the three bundled layouts
- **THEN** the return value is `slice(0, 3)`

#### Scenario: Heading is always at index 6
- **WHEN** `layout.index_of("heading_deg")` is called on any of the three bundled layouts
- **THEN** the return value is 6

#### Scenario: IMU bias group is 6 fields
- **WHEN** `layout.slice("imu_bias")` is called on any of the three bundled layouts
- **THEN** the slice covers exactly 6 indices

#### Scenario: Prev-velocity group is three fields at slice(15, 18) in all layouts
- **WHEN** `layout.slice("prev_velocity")` is called on any of the three bundled layouts
- **THEN** the return value is `slice(15, 18)`

#### Scenario: Prev-heading group is a single field at slice(18, 19) in all layouts
- **WHEN** `layout.slice("prev_heading")` is called on any of the three bundled layouts
- **THEN** the return value is `slice(18, 19)`

#### Scenario: Ballast drifter has deep_current group
- **WHEN** `BALLAST_DRIFTER_LAYOUT.slice("deep_current")` is called
- **THEN** the slice covers exactly 2 indices

#### Scenario: Pure drifter has no deep_current group
- **WHEN** `PURE_DRIFTER_LAYOUT.slice("deep_current")` is called
- **THEN** `KeyError` is raised


### Requirement: Layout Unit Labels
Every field in every bundled layout SHALL have a non-empty `unit` string. Position fields SHALL use `"m"`, velocity fields SHALL use `"m/s"`, heading SHALL use `"deg"`, current fields SHALL use `"m/s"`, neighbor range fields SHALL use `"m"`, gyro bias fields SHALL use `"deg/s"`, accel bias fields SHALL use `"m/s^2"`.

#### Scenario: All bundled layouts have populated unit labels
- **WHEN** the three bundled layouts are inspected
- **THEN** every `StateField.unit` in every layout is a non-empty string

#### Scenario: Position fields use meters
- **WHEN** any field in the `"position"` group of any bundled layout is inspected
- **THEN** its `unit` is `"m"`

#### Scenario: Velocity fields use meters per second
- **WHEN** any field in the `"velocity"` group of any bundled layout is inspected
- **THEN** its `unit` is `"m/s"`

#### Scenario: Heading uses degrees
- **WHEN** the field at `layout.index_of("heading_deg")` is inspected for any bundled layout
- **THEN** its `unit` is `"deg"`
