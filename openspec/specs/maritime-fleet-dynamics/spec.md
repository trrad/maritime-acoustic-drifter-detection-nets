## Purpose

Composed node type, blueprint factories, capability helpers, truth propagation, and fleet factory for maritime simulation. Implements the M1 physics component specs and the fixed 4-phase tick ordering. Depends on `maritime-platform-profile` (profiles, component specs) and `maritime-state-layout` (state vector layouts).

## Requirements

### Requirement: M1 Physics Component Specs
The system SHALL provide frozen dataclass types for each M1 physics
component, all conforming to the `ComponentSpec` protocol (`kind: str`,
`avg_power_mw: float`). The M1 types SHALL be:

- `MooredPoseSpec` — `kind = "moored_pose"`, with
  `anchor_lat_deg: float`, `anchor_lon_deg: float`, `anchor_depth_m: float`,
  `avg_power_mw: float = 0.0`.
- `DriftingSurfacePoseSpec` — `kind = "drifting_surface_pose"`,
  `avg_power_mw: float = 0.0`.
- `BallastDriftingPoseSpec` — `kind = "ballast_drifting_pose"`,
  `avg_power_mw: float = 0.0`.
- `BallastSpec` — `kind = "ballast_pump"`, with `capacity_ml: float`,
  `pump_rate_ml_per_s: float`, `avg_power_mw: float`.
- `SatelliteUplinkSpec` — `kind = "satellite_uplink"`, with
  `duty_cycle: float`, `avg_power_mw: float`.

Each SHALL reject physically nonsensical values at construction:
non-positive capacities, duty cycles outside `[0, 1]`, negative power.

#### Scenario: BallastSpec constructs with positive values
- **WHEN** `BallastSpec(capacity_ml=30.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0)` is constructed
- **THEN** all field accesses return the provided values
- **AND** `spec.kind == "ballast_pump"`

#### Scenario: BallastSpec rejects non-positive capacity
- **WHEN** `BallastSpec(capacity_ml=0.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0)` is constructed
- **THEN** construction raises `ValueError`

#### Scenario: SatelliteUplinkSpec rejects duty cycle outside [0, 1]
- **WHEN** `SatelliteUplinkSpec(duty_cycle=1.5, avg_power_mw=10.0)` is constructed
- **THEN** construction raises `ValueError`

#### Scenario: Every M1 spec type conforms to the ComponentSpec protocol
- **WHEN** any of `MooredPoseSpec`, `DriftingSurfacePoseSpec`, `BallastDriftingPoseSpec`, `BallastSpec`, `SatelliteUplinkSpec` is inspected
- **THEN** every instance passes `isinstance(spec, ComponentSpec)` (the runtime-checkable protocol from `maritime-platform-profile`)

### Requirement: Composed Node Type
The system SHALL provide a single `Node` frozen dataclass with fields
`node_id: str`, `profile: NodeProfile`, `layout: StateLayout`,
`state: numpy.ndarray`, and `components: Mapping[str, object]` — a
mapping from component kind (string) to the runtime component instance.
There SHALL NOT be distinct subclasses per blueprint. Construction
SHALL enforce: `state.shape == (layout.state_dim,)`,
`profile.state_dim == layout.state_dim`, `state` contains no NaN or
infinite values, and every key in `components` matches the `kind` of
some entry in `profile.components`.

#### Scenario: Valid Node constructs successfully
- **WHEN** a `Node` is constructed with `PURE_DRIFTER_PROFILE`,
  `PURE_DRIFTER_LAYOUT`, a shape-(15,) finite state array, and a
  components mapping whose keys match a subset of
  `PURE_DRIFTER_PROFILE.components` kinds
- **THEN** construction succeeds
- **AND** the node is immutable
- **AND** all field accesses return the provided values

#### Scenario: State shape mismatch is rejected
- **WHEN** a `Node` is constructed with a `state` array whose shape does
  not match `(layout.state_dim,)`
- **THEN** construction raises `ValueError` naming the shape mismatch

#### Scenario: Profile/layout state_dim mismatch is rejected
- **WHEN** a `Node` is constructed with a profile whose `state_dim`
  differs from `layout.state_dim`
- **THEN** construction raises `ValueError`

#### Scenario: Non-finite state is rejected
- **WHEN** a `Node` is constructed with a state array containing NaN
- **THEN** construction raises `ValueError`
- **AND** the same for infinite values

#### Scenario: Component mapping key not in profile is rejected
- **WHEN** a `Node` is constructed with `components={"ballast_pump": ...}`
  but `profile.components` contains no spec with `kind == "ballast_pump"`
- **THEN** construction raises `ValueError` naming the unexpected kind

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

### Requirement: Capability Utility Helpers
The system SHALL provide module-level utility functions that read
component presence as ground truth (not any redundant profile flag):

- `has_pump(node) -> bool` returns `"ballast_pump" in node.components`.
- `is_moored(node) -> bool` returns `"moored_pose" in node.components`.
- `has_satellite_uplink(node) -> bool` returns
  `"satellite_uplink" in node.components`.

The functions SHALL be consistent with component presence: for any node
built via a blueprint factory, the helper's value SHALL match the
declared blueprint (pure drifters report `False` for all three;
ballast drifters report `True` only for `has_pump`; anchors report
`True` only for `is_moored` and `has_satellite_uplink`).

#### Scenario: Pure drifter utility helpers all return False
- **WHEN** `has_pump`, `is_moored`, `has_satellite_uplink` are called on a node built via `make_pure_drifter`
- **THEN** all three return `False`

#### Scenario: Ballast drifter has pump but is not moored
- **WHEN** the three helpers are called on a node built via `make_ballast_drifter`
- **THEN** `has_pump` returns `True`, `is_moored` returns `False`, `has_satellite_uplink` returns `False`

#### Scenario: Anchor is moored with satellite uplink, no pump
- **WHEN** the three helpers are called on a node built via `make_anchor`
- **THEN** `has_pump` returns `False`, `is_moored` returns `True`, `has_satellite_uplink` returns `True`

### Requirement: Fixed 4-Phase Tick Ordering
The system SHALL provide a `propagate_truth(node, dt_sec, env, rng)`
function. The `env: PhysicsEnv` carries `current_field: CurrentField`,
`t_sec: float`, `enu_origin_lat_deg: float`, `enu_origin_lon_deg: float`
— the ENU origin is required so the truth current can be sampled at
the node's actual geographic location (not at a hard-coded `(0, 0)`).
The function executes the following sequence per tick:

0. **snapshot** — copy `layout.slice("velocity")` into
   `layout.slice("prev_velocity")` and `layout.slice("heading")` into
   `layout.slice("prev_heading")` so the IMU sensor can finite-
   difference per tick.
0b. **current sampling** — convert the node's ENU position
    (`state[0:2]`) to lat/lon using `env.enu_origin_*`, call
    `env.current_field.velocity_at(node_lat_deg, node_lon_deg, env.t_sec)`,
    and write the returned `(vx, vy)` into
    `layout.slice("surface_current")`. This makes the truth current at
    the node's location visible to truth consumers (dashboard,
    validation harness via `ScenarioTruthReader`) and ensures that
    spatial current structure (eddies, tides) actually influences
    each node's advection.
1. **pump** — if `"ballast_pump"` is in `node.components`, advance pump
   state. In M1 this phase is a no-op; the depth setpoint does not
   change.
2. **pose** — dispatch on which pose component is present
   (`moored_pose` / `drifting_surface_pose` / `ballast_drifting_pose`)
   and integrate position and heading using the `(vx, vy)` sampled in
   step 0b (so advection uses the current at the node's actual
   position, not at `(0, 0)`).
3. **imu_biases** — gyro and accel bias random walks for the six bias
   slots in the layout.
4. **clock** — if `"clock"` is in `node.components`, call
   `node.components["clock"].advance(dt_sec)`. In M1 this is a zero-offset
   identity clock stub; real clock components are delivered by
   `maritime-clock-model`.

The function SHALL be pure with respect to the state array — it SHALL
return a new `numpy.ndarray`; it SHALL NOT mutate the input node's
state. The clock component's internal wall-time counter MAY mutate;
no state-vector dimension is modified through clock advance.

#### Scenario: propagate_truth is deterministic for identical inputs
- **WHEN** `propagate_truth(node, dt_sec, env, rng)` is called twice with identical inputs and two identically-seeded RNGs
- **THEN** the two returned state arrays are element-wise equal

#### Scenario: Input node state is not mutated
- **WHEN** `propagate_truth` is called with a node
- **THEN** the input node's `state` array is byte-identical after the call

#### Scenario: Output shape matches input
- **WHEN** `propagate_truth` is called with a node whose state has shape `(N,)`
- **THEN** the returned array has shape `(N,)`

#### Scenario: Output contains no NaN
- **WHEN** `propagate_truth` is called with a valid finite input
- **THEN** the returned state contains no NaN or infinite values

#### Scenario: Phase order is pump, pose, imu_biases, clock
- **WHEN** a node with all four phase components is advanced and the effect of each phase can be isolated (e.g., pump changes depth setpoint observably, pose reads that setpoint)
- **THEN** a phase's effect is visible to later phases, not earlier ones — demonstrating the documented order

#### Scenario: Snapshot captures pre-tick velocity
- **WHEN** `propagate_truth` is called on a node whose `layout.slice("velocity")` holds `v` and whose `layout.slice("prev_velocity")` holds any stale sentinel
- **THEN** the returned state's `prev_velocity` slice equals `v` (the input velocity, pre-tick)

#### Scenario: Snapshot captures pre-tick heading
- **WHEN** `propagate_truth` is called on a node whose `layout.slice("heading")` holds `h` and whose `layout.slice("prev_heading")` holds any stale sentinel
- **THEN** the returned state's `prev_heading` slice equals `h` (the input heading, pre-tick)

#### Scenario: Current field is queried at the node's lat/lon, not (0, 0)
- **WHEN** `propagate_truth` is called on two nodes whose ENU positions map (via `env.enu_origin_*`) to different lat/lon
- **THEN** `env.current_field.velocity_at(lat, lon, t_sec)` is invoked with the two distinct (lat, lon) pairs — not with `(0.0, 0.0)` for both

#### Scenario: Sampled surface current is written into truth state
- **WHEN** `propagate_truth` is called with a `current_field` whose `velocity_at` returns `(vx, vy)` at the node's lat/lon
- **THEN** the returned state's `layout.slice("surface_current")` equals `(vx, vy)`

### Requirement: Moored Nodes Do Not Advect
Nodes with a `moored_pose` component SHALL retain their position
(`layout.slice("position")`) and velocity (`layout.slice("velocity")`)
across propagation ticks, element-wise equal to input, regardless of
current field value. Heading and IMU bias dimensions SHALL continue
to evolve with process noise — moored does not mean frozen, only
position-invariant.

#### Scenario: Anchor position is invariant under advection
- **WHEN** `propagate_truth` is called on a node built via `make_anchor` with initial position `(100, 200, 0)` and a nonzero current at that position
- **THEN** the returned state's position indices equal `(100, 200, 0)` element-wise

#### Scenario: Anchor velocity stays zero
- **WHEN** `propagate_truth` is called on a moored node with initial velocity `(0, 0, 0)`
- **THEN** the returned state's velocity indices equal `(0, 0, 0)` element-wise

#### Scenario: Anchor heading still evolves under process noise
- **WHEN** `propagate_truth` is called repeatedly on a moored node over 100 ticks with nonzero heading process noise
- **THEN** the heading value changes between ticks

### Requirement: Pure Drifters Stay on the Surface
Nodes with a `drifting_surface_pose` component SHALL remain at the
surface — the depth dimension (position index 2) SHALL equal exactly
`0.0` after every `propagate_truth` call, regardless of vertical
velocity or process noise. East and north position components SHALL
evolve normally under advection and noise.

#### Scenario: Pure drifter depth is pinned to zero
- **WHEN** `propagate_truth` is called repeatedly on a pure-drifter node with `vz = 0.5 m/s` over 60 ticks of 1 s
- **THEN** every returned state has `state[2] == 0.0`

#### Scenario: Pure drifter east and north still advect
- **WHEN** `propagate_truth` is called on a pure-drifter node in a current field returning `(0.1, 0.0) m/s` over 60 s at 1 Hz
- **THEN** the final east position differs from the initial east position by at least 5 m and at most 7 m (6 m nominal + noise)

### Requirement: Ballast-Drifting Nodes Advect Horizontally
Nodes with a `ballast_drifting_pose` component SHALL advect
horizontally — east and north positions SHALL integrate from the sum
of node velocity and current field velocity at the node's position
over `dt_sec`. In the zero-process-noise limit with constant current,
the 60 s displacement SHALL equal `(vel + current) * 60` within 0.1 m.
The depth dimension MAY evolve in M2 (driven by pump advance); in M1
it SHALL remain at its initial value because the pump phase is a
no-op.

#### Scenario: Zero-noise constant-current advection
- **WHEN** `propagate_truth` is called 60 times at 1 Hz on a ballast-drifting node with initial velocity `(0, 0, 0)` in a constant current field of `(0.1, 0)` and process-noise RNG disabled
- **THEN** the final east position differs from initial by `6.0 m ± 0.1`

#### Scenario: Advection respects current direction
- **WHEN** a ballast-drifting node starts at `(0, 0, 0)` with zero velocity and zero process noise, in a constant current field of `(0, 0.2)`
- **THEN** after 30 s at 1 Hz the north position is `6.0 m ± 0.1` and the east position is unchanged

#### Scenario: M1 ballast depth is held constant
- **WHEN** `propagate_truth` is called repeatedly on a ballast-drifting node in M1 (pump phase is a no-op)
- **THEN** the depth dimension is unchanged between ticks

### Requirement: IMU Bias Random Walk
Gyro and accel bias dimensions SHALL evolve as zero-mean Gaussian
random walks with module-level noise constants. Bias values SHALL
remain finite and SHALL NOT be clamped to zero or to an arbitrary
bound during propagation.

#### Scenario: Bias evolves with process noise
- **WHEN** `propagate_truth` is called 1000 times at `dt_sec=1.0` on a node whose initial bias values are all zero, using a seeded RNG
- **THEN** the standard deviation of each bias dimension across the 1000-step trajectory is between 50% and 200% of the expected random-walk std (`noise_per_sqrt_s * sqrt(1000)`)

#### Scenario: Bias is not clamped
- **WHEN** `propagate_truth` is called repeatedly and bias values grow to non-trivial magnitudes
- **THEN** no bias dimension is reset to zero or clipped

### Requirement: Heading Wrapping
The heading dimension SHALL be kept in the range `[0, 360)` after
propagation. Negative values or values ≥ 360 SHALL be wrapped modulo
360.

#### Scenario: Heading wraps after multiple revolutions
- **WHEN** `propagate_truth` is called with a state whose heading is 350 deg and the gyro bias drives it past 360 over several ticks
- **THEN** every returned heading is in `[0, 360)`

### Requirement: Passive Drifter Velocity Is Per-Tick Sampled, Not Random-Walked
`propagate_truth` SHALL re-sample the drifter velocity residual from
a zero-mean Gaussian each tick for both `DRIFTING_SURFACE_POSE` and
`BALLAST_DRIFTING_POSE` branches, rather than adding a random-walk
increment to the previous tick's velocity. The per-tick sampling σ
SHALL equal a module-level constant `DRIFTER_VEL_PERTURBATION_MS`
(default 0.02 m/s), applied as the stddev of an independent normal
sample per tick to the `(vx, vy)` slots (indices 3–4). `vz` (index 5)
SHALL remain untouched in M1: pure drifters pin depth at 0 and
ballast drifters pin depth at its initial value (pump is `pass`), so
vz has no physical role and must not accumulate tick-uncorrelated
noise. The legacy random-walk scale constant
`VEL_PROCESS_NOISE_MS_PER_SQRT_S` is retired.

The velocity state slot (`state[3:5]`) still carries the drifter
velocity residual above the current-field mean; the position update
formula `state[0] += (state[3] + current_vx) * dt + pos_noise[0]`
(and its `y` counterpart) is unchanged. What changes is the residual
evolution: each tick's residual is independent of last tick's,
bounded by the perturbation σ.

This matches the physical model of a passive drifter at 60 s tick
resolution: turbulent / wind / internal-wave perturbations around
the mean current are uncorrelated at that scale. Under the retired
RW model, the residual's stddev integrated to ~1 m/s over 12 h —
unphysical. Under the per-tick sampling model, the residual stays
bounded by `3 * DRIFTER_VEL_PERTURBATION_MS ≈ 0.06 m/s` on every
tick, indefinitely.

#### Scenario: Truth drifter residual is independent tick-to-tick
- **WHEN** `propagate_truth` is called 1000 times at `dt_sec=60.0` on a `DRIFTING_SURFACE_POSE` node in a constant-current field, with a seeded RNG
- **THEN** the sequence of `state[3]` values across the 1000 ticks has sample stddev in the range `[0.5 * DRIFTER_VEL_PERTURBATION_MS, 1.5 * DRIFTER_VEL_PERTURBATION_MS]`
- **AND** the lag-1 autocorrelation of the `state[3]` sequence is below 0.2 in absolute value (tick-uncorrelated within finite-sample noise)

#### Scenario: Truth drifter position advects with current under zero perturbation
- **WHEN** `propagate_truth` is called 10 times at `dt_sec=60.0` on a pure-drifter node starting at `(0, 0, 0)` with initial velocity `(0, 0, 0)`, in a constant current field returning `(0.2, 0.0) m/s`, with `DRIFTER_VEL_PERTURBATION_MS` temporarily monkey-patched to 0.0 (deterministic fixture), zero `POS_PROCESS_NOISE_M_PER_SQRT_S`
- **THEN** the final east position equals `0.2 * 10 * 60 == 120.0 m` within 0.1 m (tight tolerance, no RW contribution)
- **AND** the final north position is within 0.1 m of its starting value
- **AND** every tick's `state[3]` equals exactly `0.0` (no residual accumulation)

#### Scenario: Over a 12-hour run, the residual stays bounded
- **WHEN** `propagate_truth` is called `12 * 3600 / 60 == 720` times at `dt_sec=60.0` on a `DRIFTING_SURFACE_POSE` node in a constant-current field
- **THEN** the sequence's `max(|state[3]|)` across all 720 ticks is less than `5 * DRIFTER_VEL_PERTURBATION_MS` (≈ 0.1 m/s) — NOT the ~1 m/s that the retired RW model would have produced

### Requirement: M1 Fleet Factory
The system SHALL provide a `make_m1_fleet(seed, bbox, *, lora_period_sec=None, gps_period_sec=None)`
function that returns a tuple of exactly 10 `Node` instances: 2 built
via `make_anchor`, 4 via `make_ballast_drifter`, and 4 via
`make_pure_drifter`. Given identical `seed`, `bbox`, and cadence
kwargs, two calls SHALL produce byte-identical output. All initial
positions SHALL be strictly within the provided `bbox`. Anchor ENU
positions SHALL be deterministic and independent of the seed; drifter
positions SHALL be pseudo-random and seed-dependent. All 10 `node_id`
values SHALL be distinct.

The `lora_period_sec` and `gps_period_sec` keyword arguments are
optional overrides that clone the bundled platform profiles with
replaced cadences before node construction:

- `lora_period_sec=X` SHALL produce a fleet in which every node's
  `profile.comms.tdma_period_sec == X` and every node's `lora_toa`
  sensor spec (where present) has `max_rate_hz == 1/X`.
- `gps_period_sec=X` SHALL produce a fleet in which every anchor's
  `gps` sensor has `max_rate_hz == 1/X`; drifter nodes (which have no
  GPS sensor in M1) are unaffected.
- `None` (the default) SHALL preserve the bundled M1 profile values
  exactly — no cloning, no mutation.

For the two anchors, the factory SHALL construct distinct per-anchor
profiles via `make_anchor_profile(anchor_lat_deg, anchor_lon_deg)`
(see `maritime-platform-profile` Requirement: Anchor Profile Factory),
placing the first anchor at the bbox's south-west corner
(`min_lat`, `min_lon`) and the second at the north-east corner
(`max_lat`, `max_lon`). The two anchors' `MooredPoseSpec` components
SHALL therefore carry different `anchor_lat_deg` / `anchor_lon_deg`
values, so that consumers of `ScenarioHeader.anchor_positions` (the
PF's LoRa-TOA anchor-based localization in particular) see real bbox-
derived mooring coordinates rather than the placeholder `(0.0, 0.0)`
that `ANCHOR_PROFILE` carries as a template.

Coastline-aware placement (rejecting positions on land) is explicitly
NOT this factory's responsibility — that is handled by
`maritime-scenario-gen`, which has the `RegionalMap` loaded.

#### Scenario: Fleet composition
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8))` is called
- **THEN** the returned tuple has length 10
- **AND** exactly 2 elements satisfy `is_moored(node)` (the anchors)
- **AND** exactly 4 elements satisfy `has_pump(node)` (the ballast drifters)
- **AND** the remaining 4 elements satisfy neither (pure drifters)

#### Scenario: Determinism across calls
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` is called twice with identical arguments
- **THEN** the two returned fleets have identical node IDs, profiles, layouts, and initial states

#### Scenario: Different seed produces different drifter positions
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` and `make_m1_fleet(seed=43, bbox=...)` are called with the same bbox
- **THEN** at least one drifter position differs between the two fleets
- **AND** both anchor positions are identical across the two fleets

#### Scenario: All initial positions are strictly inside bbox
- **WHEN** `make_m1_fleet(seed, bbox)` is called with any valid seed and bbox
- **THEN** every node's initial position is strictly within bbox (not on the boundary)

#### Scenario: Unique node IDs
- **WHEN** `make_m1_fleet(seed, bbox)` is called
- **THEN** all 10 nodes have distinct `node_id` values

#### Scenario: Anchors carry bbox-corner mooring coordinates
- **WHEN** `make_m1_fleet(seed, bbox=(min_lat, min_lon, max_lat, max_lon))` is called
- **THEN** the first anchor's `profile.component("moored_pose").anchor_lat_deg == min_lat` and `anchor_lon_deg == min_lon`
- **AND** the second anchor's `profile.component("moored_pose").anchor_lat_deg == max_lat` and `anchor_lon_deg == max_lon`
- **AND** the two anchors' `anchor_lat_deg` values are not equal to each other
- **AND** the two anchors' `anchor_lon_deg` values are not equal to each other

#### Scenario: lora_period_sec override applies uniformly to every node
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8), lora_period_sec=60.0)` is called
- **THEN** every returned node's `profile.comms.tdma_period_sec == 60.0`
- **AND** for every node whose profile has a `lora_toa` sensor, that sensor's `max_rate_hz == 1.0 / 60.0` within float tolerance
- **AND** the bundled profile constants `BALLAST_DRIFTER_PROFILE`, `PURE_DRIFTER_PROFILE`, and `ANCHOR_PROFILE` (the module-level singletons) remain unmutated (clone-with-override, not in-place edit)

#### Scenario: gps_period_sec override applies to anchors
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8), gps_period_sec=60.0)` is called
- **THEN** every anchor node's `gps` sensor spec has `max_rate_hz == 1.0 / 60.0` within float tolerance
- **AND** the four ballast-drifter and four pure-drifter nodes are unaffected (they do not carry a GPS sensor in M1)

#### Scenario: Default kwargs preserve bundled profiles byte-identically
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` is called with no cadence kwargs
- **THEN** every node's `profile.comms.tdma_period_sec` equals the bundled `BALLAST_DRIFTER_PROFILE.comms.tdma_period_sec` (3600.0 s in M1)
- **AND** the factory does NOT clone profiles when no override is requested (bundled singletons flow through unchanged)
