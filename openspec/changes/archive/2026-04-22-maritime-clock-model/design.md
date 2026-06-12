## Context

The maritime simulation has a global "true time" (seconds from
simulation start) that governs physics propagation. Each node
experiences this time through its own local clock — a crystal
oscillator that drifts at a hardware-dependent rate and is
periodically re-synchronized by some mechanism (GPS PPS for anchors,
LoRa-TDMA frame boundary for everyone else). The clock's readings
affect:

1. **Sensor timestamps** — every `Measurement.t_sec` is computed as
   `node.components["clock"].wall_time(true_sec)`. A node's
   local-clock readings are what the JSONL captures; the PF fuses
   measurements from different nodes without assuming their clocks
   agree.
2. **LoRa TOA** — range measurements use transmit and receive clocks
   on different nodes, so any skew between them becomes an apparent
   ranging error.
3. **Acoustic TDOA (M2+)** — event timestamps across nodes differ by
   relative clock offsets. A 1 ms offset ≈ 1.5 m positioning error
   for sound in water.

At M1, acoustics are not used and there is no fleet-coordinated PF
fusion yet. The clock's M1 job is purely architectural:

- Provide a `ComponentSpec`-conforming clock type so blueprint
  factories can attach one to every node (anchors and drifters).
- Provide an `advance(dt_sec)` + `wall_time(true_sec)` runtime
  component so `propagate_truth` phase 4 has something to dispatch
  to, and so `maritime-sensors` has a single-truth source for
  `wall_time`.
- Ship bundled-profile parameters (zero drift) such that
  `wall_time(t) == t` emerges from the model, not from a
  closed-form-result contract.

This change deliberately does **not** ship the sync-mechanism
abstraction. Sync events (GPS-PPS discipline; LoRa-TDMA frame
alignment) are new `ComponentSpec` types that land with M2 realistic
clocks. Keeping M1 narrow to "crystal drift accumulator, zero drift"
avoids committing to a sync-mechanism shape before M2 dictates it.

## Goals / Non-Goals

**Goals:**
- `ClockSpec` frozen dataclass conforming to `ComponentSpec`:
  `kind: ClassVar[str] = "clock"`, `drift_ppm: float`,
  `avg_power_mw: float`. Immutable. `__post_init__` rejects
  `drift_ppm < 0` and `avg_power_mw < 0`.
- `Clock` runtime component: holds a `spec: ClockSpec` and a mutable
  `_accumulated_offset_sec: float`. Exposes `advance(dt_sec)` which
  adds `dt_sec * spec.drift_ppm * 1e-6` to the accumulator, and
  `wall_time(true_sec)` which returns
  `true_sec + _accumulated_offset_sec`.
- Bundled profiles include a `ClockSpec(drift_ppm=0.0,
  avg_power_mw=0.0)` in their `components` tuple. Expressed as a
  MODIFIED delta on `maritime-platform-profile`'s bundled-profiles
  requirement.
- Blueprint factories instantiate a `Clock` runtime component from
  the profile's `ClockSpec` and place it at `node.components["clock"]`.
- `propagate_truth` phase 4 calls
  `node.components["clock"].advance(dt_sec)` exactly once per tick.
  Covered by `maritime-fleet-dynamics` integration test; this change
  supplies the runtime component that makes the dispatch work.
- M1 test contract: for any node built from a bundled profile and
  any `true_sec >= 0`, after any sequence of `advance(dt)` calls the
  node's `clock.wall_time(true_sec) == true_sec` exactly (zero-drift
  emergence).

**Non-Goals:**
- Sync mechanism components (`GpsPpsSyncSpec`, `LoraFrameSyncSpec`).
  M2 work; own `ComponentSpec` types; own `advance` dispatch.
- Non-zero `drift_ppm` values. M1 bundled profiles ship `0.0`. M2
  populates real values grounded in crystal datasheets.
- Correction method on `Clock` (`correct(new_offset)`). M1 has no
  sync events, so no correction call sites exist. Adding the method
  now would be premature; it lands with the sync-mechanism
  components in M2.
- Temperature-dependent drift. M3+.
- Clock power draw beyond `avg_power_mw` on the spec. Real clock
  hardware (TCXO with ~0.1 µA standby) has a well-defined power
  number from the datasheet; M2 replaces the M1 `0.0` placeholder
  with that number. M1 carries `0.0` to avoid burning budget on an
  ungrounded figure.

## Decisions

### D1: Clock as a `ComponentSpec` (skeuomorphic composition)

**Choice:** `ClockSpec` is a frozen dataclass with `kind: ClassVar[str] = "clock"`, `drift_ppm: float`, and `avg_power_mw: float`. It conforms to the `runtime_checkable` `ComponentSpec` protocol defined in `maritime-platform-profile`. Each bundled profile lists a `ClockSpec` in its `components` tuple; the runtime `Clock` component is instantiated by the blueprint factory and lives at `node.components["clock"]`.

**Why:** Every other physics component on a node is a
`ComponentSpec`-conforming frozen dataclass composed into the
profile. The clock is no different — a node that has a clock is
a node that carries a `ClockSpec` in its components. Capability
queries like "does this node have a clock?" reduce to
`"clock" in node.components`, which is the same pattern
`has_pump`/`is_moored` use. Going out-of-band (runtime
mapping, parallel factory, subclass hierarchy) duplicates the
source of truth and fights the composition charter.

### D2: Protocol is `advance(dt_sec)` + `wall_time(true_sec)`, no attribute contract

**Choice:** The `Clock` runtime exposes exactly two methods:
- `advance(dt_sec: float) -> None` — mutates internal state.
- `wall_time(true_sec: float) -> float` — pure read.

`offset_sec` and `drift_ppm` are not public attributes of the clock runtime. `drift_ppm` lives on the spec; the accumulated offset is internal state; neither is a named protocol attribute that external code depends on.

**Why:** Sensors only consume `wall_time`. Phase 4 of `propagate_truth` only calls `advance`. That's the entire external surface. Exposing `offset_sec`/`drift_ppm` as part of the protocol (as the prior draft did) committed downstream modules to a closed-form-result shape; M2's sync-event corrections can't be faithfully modeled when `offset_sec` is a read-only constant. With the minimal surface, M2 can add `correct(new_offset)` as an internal method called by sync-mechanism components; no consumer's signature changes.

### D3: `wall_time(t) == t` is emergent from `drift_ppm == 0`, not a named contract

**Choice:** The spec's test requirements state: "for any `true_sec >= 0`, after any sequence of `advance(dt)` calls on a clock with `drift_ppm=0.0`, `wall_time(true_sec) == true_sec` exactly." No requirement says `wall_time(t)` must return `t` unconditionally.

**Why:** The integrity-charter test ("set drift_ppm=0 → does wall_time == true_sec emerge?") passes only if the model is actually simulating the mechanism. Writing "wall_time(t) SHALL equal t" as a standalone requirement re-parameterizes the result. M2 with non-zero drift must produce non-identity wall_time; a named "wall_time == t" requirement would have to be deleted, evidencing the abstraction change. The emergent framing carries through M1 → M2 without modification.

### D4: Single `ClockSpec` in M1; sync mechanisms are separate `ComponentSpec` types in M2

**Choice:** M1 has one clock spec class. Hardware sync variants (GPS-PPS vs LoRa-TDMA-frame) are not modeled at M1.

**Why:** The real hardware distinction between anchors and drifters is the sync mechanism: anchors have GNSS hardware producing a 1 Hz PPS edge; drifters rely on LoRa-TDMA frame boundaries for cooperative sync. At M1 no scenario exercises sync events (drift is zero, so there's nothing to correct), so the distinction has no observable effect. Baking two near-duplicate clock classes into M1 would be speculative; M2 gets to choose the sync-mechanism factoring based on what the realistic-drift scenarios actually need.

Rejected alternative: two clock classes at M1 (`GpsPpsClockSpec`, `LoraFrameClockSpec`). Would encode the sync-mechanism split into the clock type itself, coupling "what crystal do I have" with "how do I sync." Cleaner factoring is: the crystal (this change) and the sync mechanism (M2) are separate components, both composed onto the node. An anchor at M2 carries `[ClockSpec(drift_ppm=0.5), GpsPpsSyncSpec(residual=30e-9, period=1.0)]`; a drifter carries `[ClockSpec(drift_ppm=20), LoraFrameSyncSpec(residual=1e-3, period=3600.0)]`. M1 just has `[ClockSpec(drift_ppm=0.0)]`.

### D5: No standalone clock factory; blueprint factories handle composition

**Choice:** Drop the prior draft's `make_clock(node_class: str, seed: int, realistic: bool)`. Blueprint factories (`make_anchor`, `make_ballast_drifter`, `make_pure_drifter` in `fleet.py`) instantiate the `Clock` runtime component from the `ClockSpec` in the profile's components tuple, at the same point they instantiate other runtime components.

**Why:** A parallel factory that takes a string class name and a `realistic` flag sits outside the blueprint-factory composition path. Scenario generators would have to call both `make_anchor(...)` and `make_clock(...)` and stitch the clock into the node by hand, duplicating responsibility. Blueprint factories already know the profile's components; wiring the clock there keeps composition in one place. `realistic=True/False` as a mode flag is also dropped — M1 bundled profiles carry `drift_ppm=0.0`; M2 bundled profiles carry real values. The parameter is the switch.

### D6: Bundled-profile clock parameters are `drift_ppm=0.0`, `avg_power_mw=0.0` in M1

**Choice:** `ANCHOR_PROFILE.components`, `BALLAST_DRIFTER_PROFILE.components`, and `PURE_DRIFTER_PROFILE.components` each include `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)`. Expressed as a MODIFIED delta on `maritime-platform-profile`'s bundled-profiles requirement.

**Why:** Zero values produce a wall_time identity mapping, which is what M1 scenarios assume. They also make clear that the parameters are placeholders — M2 will replace them with crystal-datasheet numbers. Using plausible-looking non-zero M1 values (e.g., `drift_ppm=0.1`) would contaminate M1 output with ungrounded "realism" that hides real bugs. Using `drift_ppm=0.0` means any clock-related test failure is a code bug, not a tolerance issue.

### D7: Cross-change coordination — sensors migrate to `node.components["clock"]`

**Choice:** `maritime-sensors`'s current design (`SensorEnv.clock_by_node_id: Mapping[str, NodeClock]`) is an out-of-band access path that predates this clock-model redesign. As part of the ongoing spec audit, `maritime-sensors` will be updated to call `node.components["clock"].wall_time(t_sec)` directly and drop the mapping. That is a separate change to the `maritime-sensors` artifacts, not a task inside this change, but this change's proposal names the migration so the coordination is visible.

**Why:** A single source of truth for the clock (composition-via-components) is the charter's forward contract. Two access paths are the failure mode the charter calls out. Fixing the clock-model shape without fixing the sensor access path would leave the inconsistency shipped.

## Risks / Trade-offs

- **[Risk] `maritime-sensors` audit lands separately** → Until the sensors migration completes, `maritime-sensors` has a design.md that contradicts the clock access pattern declared here. Mitigation: the audit finding on sensors is surfaced as the next audit item after clock-model; the two changes land together or in short sequence. Until then, this change's proposal calls out the migration in writing.
- **[Risk] `advance` mutates internal state, breaking the "pure tick" discipline** → `propagate_truth` is pure w.r.t. the state array but `maritime-fleet-dynamics` already allows the clock component to mutate internal wall-time state (design.md:197-200 of fleet-dynamics). This change inherits that carve-out, adds no new mutation surface.
- **[Risk] No sync mechanism means M1 cannot demonstrate TDOA-style clock skew** → Accepted. M1 scope doesn't include acoustics or TDOA; sync-mechanism components land with the M2 clock work.

## Key Type Contracts

```python
# clock.py — spec (frozen) + runtime component
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class ClockSpec:
    """Crystal oscillator + rest-of-the-clock hardware envelope.

    Conforms to ComponentSpec (kind + avg_power_mw). M1 bundled
    profiles ship drift_ppm=0.0 and avg_power_mw=0.0; M2 populates
    from crystal datasheets and adds sync-mechanism components
    separately.
    """
    kind: ClassVar[str] = "clock"
    drift_ppm: float
    avg_power_mw: float

    def __post_init__(self) -> None:
        if self.drift_ppm < 0:
            raise ValueError(f"drift_ppm must be non-negative, got {self.drift_ppm}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be non-negative, got {self.avg_power_mw}")


@dataclass
class Clock:
    """Runtime clock component. Mutates _accumulated_offset_sec via
    advance(); wall_time() is a pure read."""
    spec: ClockSpec
    _accumulated_offset_sec: float = 0.0

    def advance(self, dt_sec: float) -> None:
        self._accumulated_offset_sec += dt_sec * self.spec.drift_ppm * 1e-6

    def wall_time(self, true_sec: float) -> float:
        return true_sec + self._accumulated_offset_sec
```

```python
# fleet.py — blueprint factories instantiate Clock from profile
def make_anchor(profile, initial_state, rng) -> Node:
    # ... validate profile includes moored_pose, satellite_uplink, clock ...
    components = {
        "moored_pose": MooredPose(spec=profile.component("moored_pose")),
        "satellite_uplink": SatelliteUplink(spec=profile.component("satellite_uplink")),
        "clock": Clock(spec=profile.component("clock")),
    }
    return Node(..., components=components)
```

```python
# dynamics.py — propagate_truth phase 4 (unchanged from fleet-dynamics)
def propagate_truth(node, dt_sec, env, rng):
    # ... pump (no-op M1) ...
    # ... pose dispatch ...
    # ... imu bias random walk ...
    clock = node.components.get(KIND_CLOCK)
    if clock is not None:
        clock.advance(dt_sec)
    return new_state
```

## Integrity-Charter Mapping

- **Skeuomorphic composition** — `ClockSpec` is a component spec;
  `Clock` is composed into `node.components`; the `"clock"` presence
  check is the only capability test. No boolean flag on the profile,
  no class hierarchy, no parallel factory.
- **Parameter-value evolution across milestones** — M1 → M2 is a
  parameter swap (`drift_ppm: 0.0 → datasheet value`) plus addition
  of new `ComponentSpec` types for sync mechanisms. This module's
  protocol is unchanged.
- **Single clock access path** — after the `maritime-sensors`
  coordination change, every consumer reads from
  `node.components["clock"].wall_time(t_sec)`. No
  `clock_by_node_id` mapping.
