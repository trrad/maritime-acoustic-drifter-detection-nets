## Why

`maritime-platform-profile` pins *what a node class can do* as typed data.
What's still missing:

1. **State layout canon** — the 15 / 21 / 25 D state vectors in the buoy
   design doc are prose. Indices leak into every downstream consumer
   (scenario generator, sensors, PFs, validation) and silently drift.
   `NodeProfile.state_dim` is a scalar; it doesn't say which index is
   `heading_deg` vs. `cur_vx_ms`. Without a single authoritative mapping,
   downstream code re-invents it and breaks in subtle ways.

2. **Skeuomorphic composition** — the charter's forward contract is
   "capabilities are intrinsic properties, not external flags." A node's
   identity should be expressed by the **physical components it composes**,
   not by boolean flags on a profile or by a class hierarchy that one could
   sidestep. Pure composition: one `Node` type, a tuple of components
   (`MooredPoseSpec`, `BallastSpec`, `SatelliteUplinkSpec`, ...) supplied
   by the blueprint factory, and utility helpers (`has_pump(node)`) that
   read component presence as ground truth.

3. **Truth propagation** — the physics tick, with an explicit 4-phase
   order (`pump → pose → imu_biases → clock`) and per-phase dispatch on
   which components the node carries. Currently a "Level 0: Physics Truth"
   bullet in the integrity charter with no implementation.

This change also **drops** the boolean capability flags
(`has_pump`, `is_moored`, `has_satellite_uplink`) and the numeric field
`ballast_capacity_ml` from `NodeProfile`. They are redundant with component
presence and fight the "one source of truth" principle. The MODIFIED
delta on `maritime-platform-profile` removes them from the profile base
and introduces a `ComponentSpec` protocol + a `components` tuple.

## What Changes

- Introduce `rtl/vectors/maritime/state_layout.py` — canonical index→meaning
  mapping per node class, with `StateField` (name/unit/description) and
  `StateLayout` (frozen dataclasses), plus three bundled layouts
  (`PURE_DRIFTER_LAYOUT` 15 D, `BALLAST_DRIFTER_LAYOUT` 21 D,
  `ANCHOR_LAYOUT` 25 D). Every module that reads or writes state vectors
  imports from here.
- Introduce `rtl/vectors/maritime/fleet.py` — **single** `Node` frozen
  dataclass composing `node_id`, `profile`, `layout`, `state`, and a
  `components: Mapping[str, object]` runtime mapping. No subclass
  hierarchy. Blueprint factories `make_anchor(...)`,
  `make_ballast_drifter(...)`, `make_pure_drifter(...)` build the right
  composition. Utility helpers `has_pump(node)`, `is_moored(node)`,
  `has_satellite_uplink(node)` read component presence.
- Introduce M1 physics component specs: `MooredPoseSpec`,
  `DriftingSurfacePoseSpec`, `BallastDriftingPoseSpec`, `BallastSpec`
  (pump parameters — `capacity_ml`, `pump_rate_ml_per_s`), and
  `SatelliteUplinkSpec`. Each is a frozen dataclass implementing the
  `ComponentSpec` protocol (added to `platform_profile.py`).
- Introduce `rtl/vectors/maritime/dynamics.py` —
  `propagate_truth(node, dt_sec, env, rng)` executes a fixed 4-phase
  walk: (1) pump advance (if `ballast_pump` in components), (2) pose
  update (moored / drifting-surface / ballast-drifting depending on which
  pose component is present), (3) IMU bias random walk, (4) clock advance.
  Pure function — returns a new state array; no mutation.
- Introduce `make_m1_fleet(seed, bbox)` — deterministic fleet factory
  returning the 10-node M1 composition (2 anchors + 4 ballast + 4 pure).
- **MODIFY `maritime-platform-profile` standing spec:** drop `has_pump`,
  `is_moored`, `has_satellite_uplink`, and `ballast_capacity_ml` from
  `NodeProfile`. Add `components: tuple[ComponentSpec, ...]`. Add
  `ComponentSpec` protocol (`kind: str`, `avg_power_mw: float`). Rewrite
  scenarios that read the dropped flags to read component presence
  instead.
- Cross-consistency tests: every layout has
  `len(fields) == state_dim of matching profile`; every node built from
  a blueprint factory passes both the profile's and the layout's own
  invariants; `has_pump(node) == ("ballast_pump" in node.components)`.
- **No scenario generation, no sensor emission, no PF.** Those are
  `maritime-sensors` + `maritime-scenario-gen` + `maritime-pf-float`.

## Capabilities

### New Capabilities

- `maritime-state-layout`: Canonical state-vector index↔field mapping per
  node class. Single source of truth — every module that reads or writes
  state vectors imports from here.
- `maritime-fleet-dynamics`: Composed `Node` type (one type, components
  tuple), blueprint factories (`make_anchor` / `make_ballast_drifter` /
  `make_pure_drifter`), M1 physics component specs, utility helpers
  replacing boolean capability flags, and `propagate_truth` with a fixed
  4-phase tick order (pump → pose → imu_biases → clock). Includes
  `make_m1_fleet` factory.

### Modified Capabilities

- `maritime-platform-profile`: drop boolean capability flags
  (`has_pump`, `is_moored`, `has_satellite_uplink`) and
  `ballast_capacity_ml` from `NodeProfile`. Add `ComponentSpec` protocol
  and `components: tuple[ComponentSpec, ...]` field. Rewrite bundled-profile
  scenarios to read component presence, not flags.

## Impact

- **New files**: `rtl/vectors/maritime/state_layout.py`,
  `rtl/vectors/maritime/fleet.py`, `rtl/vectors/maritime/dynamics.py`,
  `tests/maritime/test_state_layout.py`, `tests/maritime/test_fleet.py`,
  `tests/maritime/test_dynamics.py`.
- **Modified files**: `rtl/vectors/maritime/platform_profile.py` — adds
  `ComponentSpec` protocol, drops removed flags from `NodeProfile`, adds
  `components` tuple, rewrites bundled profile constants.
- **Dependencies on earlier changes**: `maritime-platform-profile`
  (amended by this change's MODIFIED delta), `maritime-current-fields`
  (for `CurrentField` protocol), `maritime-geo` (for lat/lon↔ENU
  conversion).
- **Downstream consumers**: `maritime-sensors` (reads state via
  `StateLayout`, inspects `node.components` for physical presence),
  `maritime-scenario-gen` (wires fleet + dynamics + sensors + clocks
  into the tick loop), `maritime-pf-float` (imports `StateLayout` to
  interpret estimates), `maritime-dashboard` (renders per-dim trails).
- **Frozen baseline**: untouched.
- **Simulation integrity charter**: delivers the "skeuomorphic
  composition" principle via the `Node` + `components` pattern, and the
  Level 0 physics truth implementation via `propagate_truth`.
