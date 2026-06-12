## Context

`maritime-platform-profile` established capability envelopes as typed data
with boolean flags (`has_pump`, `is_moored`, `has_satellite_uplink`) and a
specialized numeric field (`ballast_capacity_ml`). Dynamics propagation,
node identity, and state-vector layout are the next three load-bearing
pieces before any scenario can run. They are tightly coupled — a node's
physics depends on which components it has, a component has a spec type
that lives on the profile, state indexing is driven by the layout — so
they land together.

During design review we concluded that the flags-on-profile + subclass-per-
blueprint pattern was the wrong shape:

- Flags are redundant with component presence. A node that has a
  `ballast_pump` component IS a pumping node; a separate `has_pump: bool`
  flag is a second source of truth that can drift.
- Subclass hierarchies (`PureDrifterNode` / `BallastDrifterNode` /
  `AnchorNode`) fight composition. If a future blueprint is
  "ballast-capable anchor" or "surface-only ballast drifter," the
  hierarchy gets ugly. Composition makes new blueprints trivial — just
  a new factory.
- The user's collaboration preference (see AGENTS.md "Composition over
  inheritance"): use one composed type + factories + utility helpers.

So this change replaces the three-class design with one `Node` type
composing a `components: Mapping[str, object]` mapping, three factory
functions per blueprint, utility helpers that read component presence,
and a fixed 4-phase tick ordering in `propagate_truth`.

The 6 D POC collapsed all of this into `gen_pf_scenario.py`. Maritime has
three node blueprints and multi-depth currents, so collapsing doesn't
scale: the scenario generator will have enough responsibility without
also owning per-blueprint physics.

## Goals / Non-Goals

**Goals:**
- A single authoritative definition of state-vector indices per node
  blueprint. Every module that touches raw state indexes through
  `StateLayout`, never with bare integers.
- One `Node` type, composing components. Capabilities are intrinsic
  because components are intrinsic — a drifter factory doesn't attach
  a `BallastPump` component; a query asking "does this node have a pump?"
  reads component presence, not a separate flag.
- Factory functions per blueprint (`make_anchor`, `make_ballast_drifter`,
  `make_pure_drifter`) that assemble the correct component set for the
  blueprint and reject misconfigured profiles at construction.
- Utility helpers (`has_pump`, `is_moored`, `has_satellite_uplink`) as
  thin wrappers around `kind in node.components`. The profile's boolean
  flags are gone; helpers replace them for call-site ergonomics.
- A `propagate_truth` pure function with a fixed, documented 4-phase
  tick order: pump → pose → imu_biases → clock. Each phase dispatches on
  component presence.
- `make_m1_fleet(seed, bbox)` returning the 10-node M1 composition so
  the scenario generator just calls it rather than reinventing placement.

**Non-Goals:**
- Sensor emission (owned by `maritime-sensors`).
- Clock semantics (owned by `maritime-clock-model`). This change takes
  an abstract clock component via the components mapping; the M1 stub
  is a zero-offset identity clock. Real clock components land with
  `maritime-clock-model`.
- Map-aware land exclusion during propagation. Dynamics doesn't check
  `is_on_land` — scenario generator rejects fleet placements that land
  on coast.
- Ballast control policy. The `BallastPump` component's advance is a
  no-op in M1 (depth stays fixed); actual depth cycling is M2.
- Parallel-node fleet advancement. `propagate_truth` operates on one
  node at a time; callers iterate.

## Decisions

### D1: Three delta specs in one change

**Choice:** `specs/maritime-state-layout/spec.md` (ADDED),
`specs/maritime-fleet-dynamics/spec.md` (ADDED), and
`specs/maritime-platform-profile/spec.md` (MODIFIED — drops the four flag
fields from `NodeProfile`, adds `ComponentSpec` protocol + `components`
tuple).

**Why:** State layout earns its own standing spec because downstream
capabilities cite it (every PF, every sensor, every dashboard render).
Fleet dynamics is narrower. Platform profile is amended because the
composition pattern requires dropping the boolean flags that every
bundled-profile scenario currently asserts against; that amendment is
scoped to this change, not a standalone pre-req, because the motivation
for the flag drop IS the components-based composition this change
introduces.

### D2: State layout as a frozen dataclass of named fields with units

**Choice:** `StateLayout` holds a tuple of `StateField(name, unit, description)`.
The index of a field is its position in the tuple. Accessors:
`layout.index_of("heading_deg")`, `layout.name_at(6)`, `layout.slice("position")`.

**Why:** Fields have units. `IntEnum` loses unit info. A named tuple or
frozen dataclass carries metadata naturally. `slice()` for named groups
(`"position"`, `"velocity"`, `"imu_bias"`) is ergonomic for vectorized
operations.

### D3: Canonical state layout per blueprint (unchanged from prior draft)

```
Pure drifter (15 D):
  [0..2]   position      (east_m, north_m, depth_m)
  [3..5]   velocity      (vx_ms, vy_ms, vz_ms)
  [6]      heading       (heading_deg)
  [7..8]   surface_current (cur_vx_ms, cur_vy_ms)
  [9..14]  imu_bias      (gyro_bx..bz, accel_bx..bz)

Ballast drifter (21 D):
  [0..14]  pure-drifter layout
  [15..16] deep_current  (deep_vx_ms, deep_vy_ms)
  [17..20] neighbor_range (r1..r4 m)

Anchor (25 D):
  [0..20]  ballast-drifter layout
  [21..24] neighbor_range (r5..r8 m)
```

Nested by extension. Heading always at index 6. Position always at
`slice(0, 3)`. Unused neighbor slots carry sentinel (NaN) and are weighted
out by the PF.

### D4: Single `Node` type, composed of components

**Choice:** One frozen dataclass `Node` with fields `node_id: str`,
`profile: NodeProfile`, `layout: StateLayout`, `state: numpy.ndarray`,
`components: Mapping[str, object]` — kind string → runtime component
instance. No subclass hierarchy.

**Why:** Composition aligns with the AGENTS.md preference. A `Node` is
defined by what it has, not by what class it is. Mixed or experimental
blueprints are trivial — compose a different component set. The
capability question ("does this node have a pump?") reduces to
`"ballast_pump" in node.components` — single source of truth.

**Construction invariants** (in `__post_init__`):
- `state.shape == (layout.state_dim,)`
- `profile.state_dim == layout.state_dim`
- `state` contains no NaN
- `components` keys match a subset of `profile.components` `kind` values
  (runtime can't have components the profile didn't declare)

### D5: Blueprint factories

**Choice:** Three factory functions `make_anchor(profile, initial_state, ...)`,
`make_ballast_drifter(...)`, `make_pure_drifter(...)`. Each asserts that
the profile matches the blueprint (anchor profile must have
`moored_pose` + `satellite_uplink` in its component specs; ballast
drifter must have `ballast_pump` + `ballast_drifting_pose`; pure drifter
must have `drifting_surface_pose` and must NOT have `ballast_pump` or
`satellite_uplink`). Each instantiates the runtime components from the
profile specs and returns a `Node`.

**Why:** Factory-per-blueprint makes the right-shaped node by
construction. Asserting at the factory surfaces misconfiguration early
and loudly.

**Alternative considered:** a single `make_node(profile)` factory that
inspects profile components and figures out the blueprint. Rejected as
too clever — blueprint is a label carried explicitly via `profile.class_name`
and the factory function name, which makes the pipeline's composition
readable at the call site in `make_m1_fleet`.

### D6: Utility helpers over boolean flags

**Choice:** Module-level functions
`has_pump(node) -> bool = "ballast_pump" in node.components`,
`is_moored(node) -> bool = "moored_pose" in node.components`,
`has_satellite_uplink(node) -> bool = "satellite_uplink" in node.components`.

**Why:** Call-site ergonomics for common queries. Single source of
truth (component presence). Any new capability query is a new helper
— no profile field changes required.

### D7: `propagate_truth` is a pure function with fixed 4-phase order

**Choice:** `propagate_truth(node, dt_sec, env, rng) -> numpy.ndarray`
executes a documented fixed sequence:

1. **pump** — if `"ballast_pump"` in components, advance pump state
   (updates depth setpoint; M1 no-op).
2. **pose** — dispatch on which pose component is present
   (`moored_pose` / `drifting_surface_pose` / `ballast_drifting_pose`)
   and integrate position + heading together. Pose-merging (position and
   heading in one phase) is deliberate: on ballast drifters the drogue
   couples horizontal drift to vertical depth through shear, and keeping
   them in one phase lets both read a consistent environment sample.
3. **imu_biases** — gyro/accel bias random walk. Applies to every node
   that has IMU bias slots in its layout (all M1 blueprints).
4. **clock** — advance local time. Calls `node.components["clock"].advance(dt_sec)`
   if present; zero-offset stub in M1 (real clock components land with
   `maritime-clock-model`).

The function is pure w.r.t. state: returns a new `numpy.ndarray`; does
not mutate the input. Clock advance is the one exception — clock
components have an `advance(dt)` method that mutates internal clock
state (wall-time counter); but state-vector dimensions are untouched.

**Why:** One named sequence beats implicit ordering. Each phase is
named; readers can follow the pipeline. Dispatch on component presence
is the composition pattern applied to physics.

**Process noise constants** (module-level, tunable):
- Position: 0.01 m/√s
- Velocity: 0.005 m/s/√s
- Heading: 0.1 deg/√s
- Gyro bias: 0.0001 deg/s/√s (ICM-42688-P datasheet)
- Accel bias: 0.001 m/s²/√s

### D8: Fleet factory — deterministic placement from seed

`make_m1_fleet(seed, bbox) -> tuple[Node, ...]` returns 10 nodes
(2 anchors at fixed bbox-relative corners; 4 ballast drifters and 4 pure
drifters placed via seed-driven uniform random inside the bbox).
Anchors are seed-independent; drifters are seed-dependent. The factory
enforces only that positions are strictly inside the bbox — it does not
approximate coastline exclusion. Rejecting on-land placements is
`maritime-scenario-gen`'s responsibility (it has the `RegionalMap`
loaded).

Earlier drafts used a "2 km from any edge" margin as a rough offshore
heuristic. That was arbitrary (why 2 km?) and ineffective (doesn't
actually guarantee offshore when the bbox straddles a coast). Dropped
per AGENTS.md "no unprincipled numeric thresholds in specs."

### D9: `ComponentSpec` protocol and concrete M1 specs

**Choice:** `ComponentSpec` is a `typing.Protocol` with
`kind: str` and `avg_power_mw: float`. Concrete M1 specs:

- `MooredPoseSpec(kind="moored_pose", anchor_lat_deg, anchor_lon_deg,
  anchor_depth_m, avg_power_mw=0.0)`
- `DriftingSurfacePoseSpec(kind="drifting_surface_pose", avg_power_mw=0.0)`
- `BallastDriftingPoseSpec(kind="ballast_drifting_pose", avg_power_mw=0.0)`
- `BallastSpec(kind="ballast_pump", capacity_ml, pump_rate_ml_per_s,
  avg_power_mw)`
- `SatelliteUplinkSpec(kind="satellite_uplink", duty_cycle, avg_power_mw)`

**Why:** Each spec is a frozen dataclass implementing a minimal protocol.
Adding a new component type (e.g., a drogue spec, a current-shear sensor)
requires only a new frozen dataclass — no `NodeProfile` structural
change. The profile's `components: tuple[ComponentSpec, ...]` is the
uniform field.

### D10: Runtime components are stateless helpers for M1

**Choice:** Runtime components for M1 are simple dataclasses mirroring
their specs (e.g., `BallastPump` holds a `spec: BallastSpec`). The
physics advance functions in `dynamics.py` dispatch on component kind
and call the appropriate logic. Clock components are the exception —
they carry mutable wall-time state.

**Why:** Avoid over-engineering. An abstract `Component` Protocol with
`.advance(node, dt, env, rng)` is defensible for extensibility, but for
M1 the dispatch is small and explicit. We can formalize the `Component`
runtime protocol in M2 when a second engine mode or a plugin-style
component registration emerges.

## Risks / Trade-offs

- **[Risk] Composition with components + factories introduces more
  dataclasses than the three-class alternative** → Accepted. The
  readability and extensibility wins pay for it; factory functions are
  small.
- **[Risk] Component-kind string typos silently produce missing
  capabilities (`has_pump(node)` returns False for a mistyped
  `"ballist_pump"`)** → Mitigation: factories assert expected kinds;
  utility helpers use module-level `KIND_BALLAST_PUMP = "ballast_pump"`
  constants so typos are compile-time errors.
- **[Risk] Layout indices lock in decisions before PFs validate them** →
  Mitigation: layout is defined with room at the end (NaN slots for
  unused neighbor ranges); extensions add at tail and don't shift.
- **[Trade-off] Dropping profile boolean flags is a MODIFIED delta on an
  archived standing spec** → Accepted. The MODIFIED-Requirements block
  replaces the Node Profile Composes Capabilities requirement and the
  Bundled M1 Fleet Profiles requirement. Documented in the spec deltas
  with full updated content.

## Key Type Contracts

```python
# state_layout.py (unchanged from prior design)
@dataclass(frozen=True, slots=True)
class StateField:
    name: str
    unit: str
    description: str

@dataclass(frozen=True, slots=True)
class StateLayout:
    class_name: str
    fields: tuple[StateField, ...]
    groups: Mapping[str, slice]

    @property
    def state_dim(self) -> int: ...
    def index_of(self, field_name: str) -> int: ...
    def name_at(self, index: int) -> str: ...
    def slice(self, group_name: str) -> slice: ...

PURE_DRIFTER_LAYOUT: StateLayout
BALLAST_DRIFTER_LAYOUT: StateLayout
ANCHOR_LAYOUT: StateLayout

# platform_profile.py (MODIFIED — adds ComponentSpec, drops flags)
@runtime_checkable
class ComponentSpec(Protocol):
    kind: str
    avg_power_mw: float

@dataclass(frozen=True, slots=True)
class NodeProfile:
    class_name: str
    state_dim: int
    sensors: tuple[SensorSpec, ...]
    comms: CommsProfile
    compute: ComputeBudget
    total_power_budget_mw: float
    components: tuple[ComponentSpec, ...]

    # DROPPED: has_pump, is_moored, has_satellite_uplink, ballast_capacity_ml

    def sensor(self, name: str) -> SensorSpec: ...
    def component(self, kind: str) -> ComponentSpec: ...   # NEW — lookup by kind

# fleet.py — M1 physics component specs
@dataclass(frozen=True, slots=True)
class MooredPoseSpec:
    kind: ClassVar[str] = "moored_pose"
    anchor_lat_deg: float
    anchor_lon_deg: float
    anchor_depth_m: float
    avg_power_mw: float = 0.0

@dataclass(frozen=True, slots=True)
class DriftingSurfacePoseSpec:
    kind: ClassVar[str] = "drifting_surface_pose"
    avg_power_mw: float = 0.0

@dataclass(frozen=True, slots=True)
class BallastDriftingPoseSpec:
    kind: ClassVar[str] = "ballast_drifting_pose"
    avg_power_mw: float = 0.0

@dataclass(frozen=True, slots=True)
class BallastSpec:
    kind: ClassVar[str] = "ballast_pump"
    capacity_ml: float
    pump_rate_ml_per_s: float
    avg_power_mw: float

@dataclass(frozen=True, slots=True)
class SatelliteUplinkSpec:
    kind: ClassVar[str] = "satellite_uplink"
    duty_cycle: float
    avg_power_mw: float

# fleet.py — Node + factories + helpers
@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    profile: NodeProfile
    layout: StateLayout
    state: numpy.ndarray
    components: Mapping[str, object]     # kind -> runtime component

def make_anchor(profile: NodeProfile, initial_state: numpy.ndarray, rng: numpy.random.Generator) -> Node: ...
def make_ballast_drifter(profile: NodeProfile, initial_state: numpy.ndarray, rng: numpy.random.Generator) -> Node: ...
def make_pure_drifter(profile: NodeProfile, initial_state: numpy.ndarray, rng: numpy.random.Generator) -> Node: ...

def make_m1_fleet(seed: int, bbox: BBox) -> tuple[Node, ...]: ...

def has_pump(node: Node) -> bool: ...
def is_moored(node: Node) -> bool: ...
def has_satellite_uplink(node: Node) -> bool: ...

# dynamics.py
# Kind-name module constants (single source for dispatch strings)
KIND_BALLAST_PUMP: str = "ballast_pump"
KIND_MOORED_POSE: str = "moored_pose"
KIND_DRIFTING_SURFACE_POSE: str = "drifting_surface_pose"
KIND_BALLAST_DRIFTING_POSE: str = "ballast_drifting_pose"
KIND_CLOCK: str = "clock"

# Process-noise module constants (tunable)
POS_PROCESS_NOISE_M_PER_SQRT_S: float
VEL_PROCESS_NOISE_MS_PER_SQRT_S: float
HEADING_PROCESS_NOISE_DEG_PER_SQRT_S: float
GYRO_BIAS_PROCESS_NOISE_DEG_S_PER_SQRT_S: float
ACCEL_BIAS_PROCESS_NOISE_MS2_PER_SQRT_S: float

def propagate_truth(
    node: Node,
    dt_sec: float,
    env: PhysicsEnv,
    rng: numpy.random.Generator,
) -> numpy.ndarray:
    """Fixed 4-phase tick: pump → pose → imu_biases → clock.
    Pure w.r.t. state — returns a new ndarray."""

@dataclass(frozen=True, slots=True)
class PhysicsEnv:
    current_field: CurrentField
    t_sec: float                        # global truth time
```

Construction invariants (in `__post_init__`):
- `StateLayout`: fields non-empty, no duplicate names, group slices
  within `[0, state_dim)`.
- `Node`: `state.shape == (layout.state_dim,)`,
  `profile.state_dim == layout.state_dim`, `state` finite,
  `components` keys ⊆ `{spec.kind for spec in profile.components}`.
- `NodeProfile`: sensors unique by name, components unique by kind,
  sum of average powers ≤ `total_power_budget_mw`.
- `BallastSpec`: `capacity_ml > 0`, `pump_rate_ml_per_s > 0`, `avg_power_mw >= 0`.
- `SatelliteUplinkSpec`: `0 <= duty_cycle <= 1`, `avg_power_mw >= 0`.

## Integrity-Charter Mapping

- **Skeuomorphic composition** — one `Node` type, components as
  ground truth, factories per blueprint, utility helpers over flags.
  Directly delivers the "capabilities are intrinsic" forward contract.
- **Level 0 physics truth** — `propagate_truth` with fixed 4-phase
  order, dispatch on component presence. Moored poses don't advect;
  surface poses stay at `depth = 0`; ballast poses are horizontally
  advected with depth fixed in M1 (pump advance is a no-op in M1).
- **Truth separation** — `propagate_truth` reads only state +
  `PhysicsEnv` (current field, global time) + RNG. No observation or PF
  data. No onboard map.
- **State layout canon** — `maritime-state-layout` is the standing spec
  every downstream module cites to justify state-indexing choices.
