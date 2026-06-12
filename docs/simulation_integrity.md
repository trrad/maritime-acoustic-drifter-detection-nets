# Simulation Integrity Charter

> Honesty guardrails for the maritime scenario harness. Enforcement is the
> point — prose is the index to the enforcement, not the enforcement itself.

## Why This Matters

The maritime simulation is load-bearing for two credibility gates:

1. **21D PF validation** — proving LNS8-delta holds at drifter-class dimensionality.
2. **Proof-of-mission** — acoustic detection + TDOA with realistic sensor and comms models.

If the simulation gives the PF unrealistic advantages (perfect time sync,
truth-map access, unlimited sensor rates), every downstream result is suspect.
The simulation must be honest enough that passing it means something.

## Three Principles

### 1. Skeuomorphic composition

Each node is a faithful digital twin of its physical counterpart.
Capabilities are not runtime flags on a config or checks inside an
"is this allowed?" guard — they are the **presence of a physical
component**, and a node has what it has because the factory built it
that way. Some concrete examples:

- A pure drifter cannot produce a GPS observation because its profile's
  `components` tuple contains no `GPSSensor` spec; the factory doesn't
  attach one; calling for a GPS reading isn't blocked by a check, the
  method isn't there to call.
- A ballast drifter has pump constraints (capacity, pump rate, power)
  because it has a `BallastSpec` component; the pump's parameters aren't
  a dictionary lookup on some central registry, they're fields on the
  spec the ballast factory attached.
- An anchor has accurate time because its component set includes GPS
  PPS hardware (delivered by `maritime-clock-model`'s `AnchorClock`
  component); drifters have looser time because their component set
  includes a different clock component.

The engine (`propagate_truth`, sensor-sampling loops) calls components
in a fixed documented order. Each component's advance/sample logic
decides what it can do given the current state; the engine does not
grant or deny capabilities at runtime, and the scenario generator does
not reach into a node and enable a sensor it didn't come with. The
single decision point is the blueprint factory — `make_anchor`,
`make_ballast_drifter`, `make_pure_drifter`. After factory, a node's
capabilities are physical facts, not negotiable.

The pattern: one `Node` type composing components; factories per
blueprint; utility helpers (`has_pump(node)`, `is_moored(node)`,
`has_satellite_uplink(node)`) read component presence. No class
hierarchy, no boolean flags on the profile. See AGENTS.md
("Composition over inheritance") for the general principle.

### 2. Truth separation

The PF never reads truth data. Enforcement is the load-bearing element — not
a note telling developers to behave. Three layers, each AST-based or type-based:

- **Physical module split.** `scenario_schema.py` contains observation types
  (`ScenarioReader`, `ObservationTickView`). `scenario_truth_schema.py`
  contains truth types (`ScenarioTruthReader`, `TruthTickView`). PF code
  cannot access truth because truth lives in a module PF cannot import.
- **Import contract.** `import-linter` with a forbidden-import contract:
  modules matching `rtl.vectors.maritime.pf_*` and `rtl.vectors.maritime.run_pf_*`
  cannot import `scenario_truth_schema` or `current_fields`. Runs in CI.
- **Type signatures.** PF functions accept observation types only; pyright
  strict catches truth leaks at authoring time.

The dashboard is explicitly outside the contract's source list — it
legitimately visualizes truth alongside estimates, and that's disclosed at
the import site.

### 3. Generator vs engine split

**Scenario generator** configures and instantiates the world. **Simulation
engine** runs the tick loop. Generator owns *what exists*; engine owns
*what happens*. Milestone 1 collapses both into a single CLI; the split
activates at M2 when a second producer (HYCOM field) or a second engine mode
is needed.

## Enforcement Matrix

Each integrity concern is either enforced by a mechanism that makes the
failure mode impossible, or it is an explicit gap. No prose rule lives
without its enforcement — gaps are visible as gaps.

| Concern | Enforced by | Where | Status |
|---|---|---|---|
| PF cannot read truth | Module split + import-linter forbidden contract + typed signatures | `scenario_schema.py`, `scenario_truth_schema.py`, `pyproject.toml` | planned (maritime-scenario-gen + project-infra-import-linter) |
| PF dynamics uses climatology, not truth current field | import-linter contract forbids PF from importing `current_fields` | `pyproject.toml` | planned (project-infra-import-linter + maritime-pf-float) |
| Drifter cannot produce GPS observations | No `GPSSensor` in drifter `profile.components`; factory doesn't attach one | `fleet.py` factory | planned (maritime-fleet-dynamics) |
| Drifter has no pump DOF | No `BallastSpec` in drifter `profile.components`; no `BallastPump` component | `fleet.py`, `dynamics.py` | planned (maritime-fleet-dynamics) |
| Anchor truth position is immovable | `MooredPose` component has no-op advection | `fleet.py`, `dynamics.py` | planned (maritime-fleet-dynamics) |
| Sensor σ matches datasheet | `SensorSpec` construction invariant + bundled profile values | `platform_profile.py` | implemented (maritime-platform-profile) |
| Sensor cannot fire above `max_rate_hz` | `should_sample` dispatch driven by `SensorSpec.max_rate_hz` | `sensors.py` | planned (maritime-sensors) |
| LoRa range ≤ `max_range_m` | Range ceiling in `LoraTOASensor.sample` (out-of-range returns no measurement) | `sensors.py` | planned (maritime-sensors) |
| LoRa ranging σ ≥ SX1262 hardware floor | `CommsProfile` construction invariant | `platform_profile.py` | implemented |
| Compute budget fits clock × rate | `ComputeBudget.__post_init__` invariant | `platform_profile.py` | implemented |
| Profile power totals ≤ power budget | `NodeProfile.__post_init__` invariant | `platform_profile.py` | implemented |
| Bathymetry non-negative at construction | Construction invariant on map grid | `map_payload.py` | planned (maritime-map-payload) |
| Coastline point-on-land detection | Polygon test (not coordinate whitelist) | `geo/coastline.py` | implemented (maritime-geo) |
| Great-circle distance accuracy | Tolerance test against surveyed pairs | `tests/maritime/test_geo.py` | implemented (maritime-geo) |
| Golden trace byte-identical regression | Committed fixture + explicit re-bless script | `tests/maritime/golden_trace/` | planned (maritime-scenario-gen) |
| Scenario seed determinism | Single RNG at generator entry; subsystems derive via `Generator.integers` | `gen_maritime_scenario.py` | planned (maritime-scenario-gen) |
| Schema version gating on read | `ScenarioReader.header()` raises on unknown version | `scenario_schema.py` | planned (maritime-scenario-gen) |
| Onboard map ≠ truth map | `make_onboard_map` applies fidelity reduction; truth uses truth map, PF reconstructs onboard from header config | `map_payload.py`, `scenario_schema.py` | planned (maritime-map-payload + maritime-scenario-gen) |
| Node cannot pass through land | `propagate_truth` rejects post-advection positions that fall on land | `dynamics.py` test | **gap** — not yet covered by M1 change set |
| Acoustic TDOA time sync ≤ 1 ms | Per-class clock model with offset/drift bounds | `clock.py` | **M2** — maritime-clock-model (M1 zero-offset stub) |
| LNS8 cycle budget fit | Cycle-count assertion against `NodeProfile.compute` | (M3) | **deferred** — M3 LNS8-delta change |
| LNS8 weight kernel doesn't silently underflow | Quantization-aware divergence check vs float64 oracle | (M3) | **deferred** — M3 |
| Biofouling / sensor drift / battery depletion / temp-dependent clock drift | (not modeled) | — | **gap** — known abstraction, M3+ |
| Current-field validation against real data | Argo float trajectories (subsurface drift ground truth) + HYCOM reanalysis (modeled depth-resolved currents) for a chosen validation bbox | (M2/M3) | **deferred** — validation region not yet picked; cheapest path for "real drift at depth" ground truth |

Rows marked **gap** are honesty debts the charter is explicit about. Each becomes planned work when the underlying change is scoped.

## Phasing

This charter describes the **target architecture**. Enforcement rows
accumulate across milestones:

- **M1** — composition-based nodes, truth-separation tooling (module split +
  import contract), golden trace, schema versioning, map fidelity reduction,
  zero-offset clock stubs, float64 reference PF.
- **M2** — realistic clock offsets + drift, acoustic propagation + TDOA,
  generator/engine module split, fleet-coordinated PF, drifter-to-drifter
  LoRa range fusion, acoustic event model.
- **M3** — LNS8-delta PF with compute-budget assertions, HYCOM current field,
  biofouling / sensor drift modeling, FPGA-in-the-loop testbench.
  Validation region selection + Argo float trajectory ground-truth
  comparison lands here. Candidate regions with dense surface +
  subsurface current instrumentation exist but no choice is made yet;
  candidate operational EEZs (per `docs/maritime_buoy_design.md`) have
  less dense scientific coverage but are the eventual deployment
  targets. Region picked when M2 HYCOM integration lands.

## Enforcement Over Instruction

This charter's pattern — each integrity concern paired with a mechanism — is
an application of the project's general collaboration principle: enforce
constraints with tools, not prompts. Types, linters, hooks, and import
contracts are the real defenses. Prose rules ("don't do X") are advisory and
drift under pressure; mechanism-backed rules don't. See
AGENTS.md ("Enforcement over instruction") for the general principle.

If a row in the Enforcement Matrix has no mechanism, it has no enforcement,
and any prose describing the failure mode is advisory at best. Add the
mechanism, or mark the gap.
