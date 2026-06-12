## Purpose

Clock component for maritime simulation: a `ComponentSpec`-conforming `ClockSpec` describing crystal oscillator characteristics, and a `Clock` runtime component tracking accumulated time offset due to drift. M1 ships zero drift/power; M2 populates from crystal datasheets and adds sync-mechanism components.

## Requirements

### Requirement: Clock Spec Conforms to Component Spec Protocol
The system SHALL provide a `ClockSpec` frozen dataclass with
`kind: ClassVar[str] = "clock"`, `drift_ppm: float`, and
`avg_power_mw: float`. The type SHALL satisfy the `ComponentSpec`
protocol defined in `maritime-platform-profile` (runtime-checkable
via `isinstance`). Construction SHALL reject `drift_ppm < 0` and
`avg_power_mw < 0`. The type SHALL be immutable.

#### Scenario: Valid ClockSpec constructs and conforms to ComponentSpec
- **WHEN** `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` is constructed
- **THEN** `spec.kind == "clock"`
- **AND** `spec.drift_ppm == 0.0`
- **AND** `spec.avg_power_mw == 0.0`
- **AND** `isinstance(spec, ComponentSpec)` is `True`

#### Scenario: Non-zero drift_ppm is accepted
- **WHEN** `ClockSpec(drift_ppm=20.0, avg_power_mw=0.5)` is constructed
- **THEN** construction succeeds with the provided values
- **AND** the resulting spec is immutable (mutation raises `FrozenInstanceError`)

#### Scenario: Negative drift_ppm is rejected
- **WHEN** `ClockSpec(drift_ppm=-0.1, avg_power_mw=0.0)` is constructed
- **THEN** construction raises `ValueError` naming the field and value

#### Scenario: Negative avg_power_mw is rejected
- **WHEN** `ClockSpec(drift_ppm=0.0, avg_power_mw=-1.0)` is constructed
- **THEN** construction raises `ValueError` naming the field and value

### Requirement: Clock Runtime Component Advances Accumulated Offset
The system SHALL provide a `Clock` runtime component holding a
`spec: ClockSpec` and a mutable `_accumulated_offset_sec: float`
initialized to `0.0`. The `advance(dt_sec: float) -> None` method
SHALL add `dt_sec * spec.drift_ppm * 1e-6` to
`_accumulated_offset_sec`. The method SHALL reject `dt_sec < 0`
with `ValueError` (time moves forward).

#### Scenario: Zero-drift advance is a no-op
- **WHEN** a `Clock` is constructed with `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` and `clock.advance(60.0)` is called
- **THEN** `clock._accumulated_offset_sec == 0.0`

#### Scenario: Non-zero drift accumulates linearly
- **WHEN** a `Clock` is constructed with `ClockSpec(drift_ppm=10.0, avg_power_mw=0.0)` and `clock.advance(100.0)` is called
- **THEN** `clock._accumulated_offset_sec == 100.0 * 10.0 * 1e-6` (i.e., `0.001`)

#### Scenario: Repeated advances accumulate
- **WHEN** `clock.advance(30.0)` is called three times on a `Clock` with `drift_ppm=10.0`
- **THEN** `clock._accumulated_offset_sec == 90.0 * 10.0 * 1e-6` (i.e., `0.0009`)

#### Scenario: Negative dt_sec is rejected
- **WHEN** `clock.advance(-1.0)` is called
- **THEN** `ValueError` is raised

### Requirement: Wall Clock Readout
The `Clock.wall_time(true_sec: float) -> float` method SHALL return
`true_sec + _accumulated_offset_sec`. The method SHALL NOT mutate any
state. For any `true_sec >= 0` and any clock constructed with
`drift_ppm=0.0` and then advanced by any finite non-negative `dt_sec`
sequence, `wall_time(true_sec)` SHALL equal `true_sec` exactly.

#### Scenario: Zero-drift clock returns identity wall time after no advances
- **WHEN** a `Clock(spec=ClockSpec(drift_ppm=0.0, avg_power_mw=0.0))` is constructed and `clock.wall_time(100.0)` is called
- **THEN** the return value equals `100.0` exactly

#### Scenario: Zero-drift clock returns identity wall time after many advances
- **WHEN** a zero-drift `Clock` is advanced by `dt_sec=60.0` one hundred times and then `clock.wall_time(7200.0)` is called
- **THEN** the return value equals `7200.0` exactly

#### Scenario: Non-zero drift produces offset wall time
- **WHEN** a `Clock(spec=ClockSpec(drift_ppm=10.0, avg_power_mw=0.0))` is advanced by `dt_sec=1000.0` once and then `clock.wall_time(1000.0)` is called
- **THEN** the return value equals `1000.0 + 0.01` (i.e., `1000.01`)

#### Scenario: wall_time does not mutate state
- **WHEN** `clock.wall_time(t)` is called any number of times between two advance calls
- **THEN** the return value is stable and `_accumulated_offset_sec` is unchanged between `wall_time` calls

### Requirement: Blueprint Factories Instantiate Clock From Profile
Each blueprint factory (`make_anchor`, `make_ballast_drifter`, `make_pure_drifter`) SHALL instantiate a `Clock` runtime component from the `ClockSpec` in the profile's `components` tuple and place it at `node.components["clock"]`. A blueprint factory called with a profile
whose `components` tuple does not contain a `ClockSpec` SHALL raise
`ValueError` naming the missing component kind. There SHALL NOT be a
standalone clock factory (e.g., `make_clock(class_name, seed,
realistic)`) — clock composition happens inside blueprint factories,
consistent with every other component.

#### Scenario: make_anchor attaches a Clock from the profile's ClockSpec
- **WHEN** `make_anchor(ANCHOR_PROFILE, initial_state, rng)` is called on a profile whose components tuple includes a `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)`
- **THEN** the returned node has `"clock" in node.components`
- **AND** `node.components["clock"]` is a `Clock` instance whose `spec` matches the `ClockSpec` from the profile
- **AND** `node.components["clock"]._accumulated_offset_sec == 0.0`

#### Scenario: make_ballast_drifter attaches a Clock from the profile's ClockSpec
- **WHEN** `make_ballast_drifter(BALLAST_DRIFTER_PROFILE, initial_state, rng)` is called
- **THEN** `node.components["clock"]` is a `Clock` instance with `spec` matching the profile's `ClockSpec`

#### Scenario: make_pure_drifter attaches a Clock from the profile's ClockSpec
- **WHEN** `make_pure_drifter(PURE_DRIFTER_PROFILE, initial_state, rng)` is called
- **THEN** `node.components["clock"]` is a `Clock` instance with `spec` matching the profile's `ClockSpec`

#### Scenario: Blueprint factory rejects a profile without a ClockSpec
- **WHEN** any blueprint factory is called with a profile whose components tuple does not contain a `ClockSpec`
- **THEN** `ValueError` is raised naming the missing `"clock"` component kind

### Requirement: Propagate Truth Advances Clock In Phase 4
`propagate_truth` SHALL invoke `node.components["clock"].advance(dt_sec)`
exactly once per tick, in phase 4 (after pump, pose, and imu_biases
phases). The clock's internal state SHALL be the only mutation caused
by phase 4; no state-vector dimension SHALL be written.

#### Scenario: Clock advance is called once per propagate_truth call
- **WHEN** `propagate_truth(node, dt_sec=1.0, env, rng)` is called on a node that carries a `Clock` component whose `spec.drift_ppm` is non-zero (for observability)
- **THEN** `node.components["clock"]._accumulated_offset_sec` increases by exactly `dt_sec * spec.drift_ppm * 1e-6` relative to before the call

#### Scenario: State vector is not mutated by clock phase
- **WHEN** `propagate_truth` is called on a node whose state is known and whose clock is advanced as part of the tick
- **THEN** the state-array deltas match the pump+pose+imu_biases phases only; no extra modification comes from the clock phase
