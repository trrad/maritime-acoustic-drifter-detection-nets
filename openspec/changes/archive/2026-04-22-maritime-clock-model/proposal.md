## Why

Acoustic TDOA triangulation requires ~1 ms time sync across nodes —
flagged as Key Technical Risk #1 in the buoy design doc. The JSONL
schema carries `t_sec` with no specification of clock resolution,
per-node offset, or drift. `maritime-fleet-dynamics` establishes
composition-via-components (`NodeProfile.components`,
`Node.components`, `propagate_truth` phase 4 dispatch on
`node.components["clock"].advance(dt_sec)`), and `maritime-sensors`
already consumes `wall_time(t_sec)` per-node to timestamp every
`Measurement`. Without a clock component that plugs into those
contracts, both consumers are stranded; `maritime-sensors` currently
papers over the gap with an out-of-band `SensorEnv.clock_by_node_id`
mapping that bypasses `node.components`.

M1 does not need realistic offsets or drift. It does need the **shape**
that lets M2 activate realistic drift accumulation and sync-event
correction by **swapping parameter values and adding sync-mechanism
components**, not by rewriting the protocol. The parameterized
`offset_sec`/`drift_ppm` closed-form model in the prior draft of this
change forced an M2 rewrite because it exposed the *result* of
drift-over-time as a static attribute on the clock. This change
replaces that shape with a crystal-drift accumulator that conforms to
the `ComponentSpec` protocol and advances per tick.

## What Changes

- Add `rtl/vectors/maritime/clock.py` — a single `ClockSpec` frozen
  dataclass conforming to the `ComponentSpec` protocol
  (`kind = "clock"`, `avg_power_mw`, plus `drift_ppm` describing the
  crystal oscillator), and a runtime `Clock` component carrying
  internal accumulated-offset state with `advance(dt_sec)` and
  `wall_time(true_sec) -> float` methods.
- **M1 semantics:** bundled profiles carry `ClockSpec(drift_ppm=0.0,
  avg_power_mw=0.0)`. `advance` accumulates `dt * drift_ppm * 1e-6`
  into the internal offset; with `drift_ppm=0` this is a no-op.
  `wall_time(true_sec) = true_sec + accumulated_offset_sec` therefore
  equals `true_sec` exactly — zero offset is **emergent**, not a
  contract. M2 populates non-zero `drift_ppm` from crystal datasheets
  and adds sync-mechanism components (GPS-PPS, LoRa-TDMA frame) as
  separate `ComponentSpec` types that periodically call
  `clock.correct(residual)`; no change to this module's protocol.
- Blueprint factories (`make_anchor`, `make_ballast_drifter`,
  `make_pure_drifter` in `fleet.py`) instantiate a `Clock` runtime
  component from the profile's `ClockSpec` and place it at
  `node.components["clock"]`. No standalone `make_clock(node_class,
  seed, realistic)` factory.
- **MODIFIED delta on `maritime-platform-profile`:** the bundled
  profile constants (`ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`,
  `PURE_DRIFTER_PROFILE`) SHALL include a `ClockSpec(drift_ppm=0.0,
  avg_power_mw=0.0)` in their `components` tuples.
- **Cross-change coordination with `maritime-sensors`:** sensor
  timestamping SHALL read from `node.components["clock"].wall_time(t_sec)`
  on the owning node, not from an out-of-band mapping. The
  `SensorEnv.clock_by_node_id` field is dropped. That coordination
  change is tracked as a separate finding against `maritime-sensors`
  in the ongoing spec audit; the tasks in this change create the
  infrastructure `maritime-sensors` migrates to.

## Capabilities

### New Capabilities

- `maritime-clock-model`: `ClockSpec` (ComponentSpec-conforming),
  `Clock` runtime component with `advance` + `wall_time`, and the
  discipline that each bundled profile carries a clock in its
  components tuple. M1 ships zero-drift parameters so `wall_time(t)`
  emerges as `t` exactly; M2 swaps parameters and adds sync
  mechanisms without touching this module's protocol.

### Modified Capabilities

- `maritime-platform-profile`: the `Bundled M1 Fleet Profiles`
  requirement is further amended (after the `maritime-fleet-dynamics`
  MODIFIED delta lands) so each bundled profile's `components` tuple
  includes a `ClockSpec` with `drift_ppm=0.0, avg_power_mw=0.0`.
- `maritime-fleet-dynamics`: the `Blueprint Factories` requirement is
  refined so each factory (a) requires a `ClockSpec` in the profile's
  `components` tuple, and (b) places a freshly-constructed
  `Clock(spec=...)` at `node.components["clock"]` rather than storing
  the `ClockSpec` directly. Stateless component specs continue to be
  stored as their own runtime components; only `ClockSpec` is wrapped
  in M1. This formalizes the spec-vs-runtime distinction that M2
  stateful components (e.g., a `Ballast` wrapping `BallastSpec`) will
  inherit.

## Impact

- **New files**: `rtl/vectors/maritime/clock.py`,
  `tests/maritime/test_clock.py`
- **Modified specs**: `maritime-platform-profile` standing spec's
  bundled-profiles requirement (clock component added);
  `maritime-fleet-dynamics` standing spec's blueprint-factories
  requirement (ClockSpec required, Clock wraps ClockSpec at runtime).
- **Dependencies on earlier changes**: `maritime-fleet-dynamics`
  (provides `ComponentSpec`, `Node.components` mapping, phase-4
  dispatch in `propagate_truth`) must land before this change's
  implementation tasks run.
- **Downstream consumers**: `maritime-sensors` (migrates from
  `SensorEnv.clock_by_node_id` to `node.components["clock"].wall_time`),
  `maritime-scenario-gen` (wires clocks through blueprint factories
  rather than a separate `make_clock` call).
- **No dependency on numeric values**: no `offset_sec`, `drift_ppm`,
  or residual ranges are asserted in this change's requirements. M1
  ships `drift_ppm=0.0` as the bundled-profile parameter; M2 populates
  real values grounded in crystal datasheets when the sync-mechanism
  components land.
