## MODIFIED Requirements

### Requirement: Blueprint Factories
The system SHALL provide three factory functions —
`make_anchor(profile, initial_state, rng)`,
`make_ballast_drifter(profile, initial_state, rng)`, and
`make_pure_drifter(profile, initial_state, rng)` — each returning a
`Node`. Each factory SHALL reject profiles that don't match the blueprint:

- `make_anchor` SHALL require `profile.components` to include a
  `moored_pose` spec, a `satellite_uplink` spec, and a `ClockSpec`, and
  SHALL require a GPS sensor in `profile.sensors`.
- `make_ballast_drifter` SHALL require a `ballast_drifting_pose` spec,
  a `ballast_pump` spec, and a `ClockSpec`, SHALL forbid a `moored_pose`
  spec, SHALL forbid a `satellite_uplink` spec, and SHALL forbid a GPS
  sensor.
- `make_pure_drifter` SHALL require a `drifting_surface_pose` spec and
  a `ClockSpec`, SHALL forbid `ballast_pump`, `moored_pose`, and
  `satellite_uplink` specs, and SHALL forbid a GPS sensor.

Each factory SHALL build the runtime `components` mapping by placing,
for each `spec` in `profile.components`:

- A freshly-constructed `Clock(spec=spec)` at `node.components["clock"]`
  when `spec` is a `ClockSpec`. The `Clock` wrapper carries the
  tick-evolving accumulated offset; it is the only M1 component whose
  runtime value differs from its spec.
- The `spec` itself at `node.components[spec.kind]` for every other M1
  component kind — these are stateless configuration holders and serve
  as their own runtime components.

M2 components that pair static configuration with tick-evolving state
(e.g., a stateful `Ballast` wrapping `BallastSpec` once pump dynamics
activate) will follow the `Clock` pattern; stateless specs will
continue to be stored directly.

#### Scenario: make_anchor succeeds with ANCHOR_PROFILE
- **WHEN** `make_anchor(ANCHOR_PROFILE, initial_state, rng)` is called with a valid shape-(25,) state
- **THEN** the returned `Node` has `is_moored(node) == True`
- **AND** `has_satellite_uplink(node) == True`
- **AND** `has_pump(node) == False`
- **AND** `"clock" in node.components`

#### Scenario: make_anchor rejects a profile missing moored_pose
- **WHEN** `make_anchor` is called with a profile that omits the `moored_pose` spec from its components tuple
- **THEN** `ValueError` is raised naming the missing component kind

#### Scenario: make_anchor rejects a profile missing ClockSpec
- **WHEN** `make_anchor` is called with a profile whose `components` tuple contains no `ClockSpec`
- **THEN** `ValueError` is raised naming the missing `"clock"` component kind

#### Scenario: make_ballast_drifter succeeds with BALLAST_DRIFTER_PROFILE
- **WHEN** `make_ballast_drifter(BALLAST_DRIFTER_PROFILE, initial_state, rng)` is called
- **THEN** the returned `Node` has `has_pump(node) == True`
- **AND** `is_moored(node) == False`
- **AND** `has_satellite_uplink(node) == False`
- **AND** `"clock" in node.components`

#### Scenario: make_ballast_drifter rejects a profile with moored_pose
- **WHEN** `make_ballast_drifter` is called with a profile containing both `ballast_drifting_pose` and `moored_pose`
- **THEN** `ValueError` is raised naming the conflict

#### Scenario: make_ballast_drifter rejects a profile missing ClockSpec
- **WHEN** `make_ballast_drifter` is called with a profile whose `components` tuple contains no `ClockSpec`
- **THEN** `ValueError` is raised naming the missing `"clock"` component kind

#### Scenario: make_pure_drifter succeeds with PURE_DRIFTER_PROFILE
- **WHEN** `make_pure_drifter(PURE_DRIFTER_PROFILE, initial_state, rng)` is called
- **THEN** the returned `Node` has `has_pump(node) == False`
- **AND** `is_moored(node) == False`
- **AND** `has_satellite_uplink(node) == False`
- **AND** `"clock" in node.components`

#### Scenario: make_pure_drifter rejects a pumped profile
- **WHEN** `make_pure_drifter` is called with a profile containing a `ballast_pump` spec
- **THEN** `ValueError` is raised naming the extra component

#### Scenario: make_pure_drifter rejects a profile missing ClockSpec
- **WHEN** `make_pure_drifter` is called with a profile whose `components` tuple contains no `ClockSpec`
- **THEN** `ValueError` is raised naming the missing `"clock"` component kind

#### Scenario: Clock runtime wraps ClockSpec
- **WHEN** any blueprint factory is called with a profile carrying `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` in its `components` tuple
- **THEN** `node.components["clock"]` is a `Clock` instance (not the `ClockSpec` itself)
- **AND** `node.components["clock"].spec` is the `ClockSpec` instance from `profile.components`

#### Scenario: Stateless specs are stored directly as runtime components
- **WHEN** any blueprint factory builds a node whose profile includes a stateless spec (e.g., `MooredPoseSpec`, `BallastSpec`)
- **THEN** `node.components[spec.kind]` is the `spec` instance itself (identity, not a wrapper)
