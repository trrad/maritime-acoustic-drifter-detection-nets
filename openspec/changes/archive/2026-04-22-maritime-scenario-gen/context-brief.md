# Context Brief: maritime-scenario-gen

## Purpose

Integration point for M1. Composes all prior maritime changes into a
deterministic CLI that writes JSONL scenarios. Ships three delta specs:

- `maritime-scenario-schema` (ADDED): observation-only types and
  `ScenarioReader` in `scenario_schema.py`.
- `maritime-scenario-truth-schema` (ADDED): truth types and
  `ScenarioTruthReader` in a separate module (`scenario_truth_schema.py`)
  that PF code cannot import without tripping the import-linter
  contract.
- `maritime-scenario-gen` (ADDED): the producer CLI.

The module split delivers truth separation at the AST / import-graph
level rather than by prose convention.

## Implementation Status

All 74 tasks complete. 341 maritime tests green. lint-imports clean.

### Post-apply notes

- Sensors use `truth_map` (not `onboard_map`) in SensorEnv. The onboard
  map is serialized as a sidecar pickle file for PF consumption.
- Golden trace uses `--duration-hours 0.0019` (7 ticks) instead of the
  spec's 0.25 hours (900 ticks). With 10 nodes × 45 LoRa pairs per tick,
  each tick is ~13 KB, so 900 ticks = ~11 MB — far exceeding the 100 KB
  fixture cap. The spec's "900 ticks × 3 nodes" assumed a 3-node fleet
  but `--nodes` requires 10 in M1. The 7-tick fixture still catches
  tick-loop output changes.
- Two-hour test fixture uses a small bbox (~330 m) to keep all nodes
  within LoRa range (10 km max).

## Key Decisions

- **Physical module split** (not naming convention). Observation types
  in `scenario_schema.py`; truth types in `scenario_truth_schema.py`.
  Shared parsing logic in an internal `_scenario_parse.py` helper
  imported by both.
- **Import-linter integration**: this change creates the two modules;
  the contract forbidding `scenario_truth_schema` in PF source modules
  is registered in `pyproject.toml` by `maritime-pf-float` (uses the
  tooling installed by `project-infra-import-linter`).
- **JSONL format**, newline-delimited. Header record (line 1) +
  tick records. Tick records carry per-node `nodes` truth state —
  observation-reader strips this; truth-reader decodes it. Header
  carries both `duration_sec` and `dt_sec` so readers can interpret
  tick spacing without depending on CLI flags.
- **Default time params reflect operational scale**:
  `--dt-sec 60.0` (1-minute steps), `--duration-hours 24.0`
  (one day). Multi-day drifter deployments and LoRa cycles measured
  in hours don't need per-second precision. Fine-resolution opt-in
  (`--dt-sec 1.0`) for TDMA/acoustic-TDOA tuning.
- **Deterministic from seed + time params** — byte-identical output
  for identical `(seed, bbox, duration_hours, dt_sec, nodes)`.
  Changing any one of these changes the file. Single top-level RNG;
  sub-generators derived per subsystem.
- **Golden trace fixture** committed at `tests/maritime/golden_trace/m1_tiny.jsonl`
  (< 100 KB); regeneration script rebuilds it intentionally.
- **CLI `main()` is a linear sequence** — no framework, no hidden
  autodiscovery. Each step is named; readers can follow the pipeline.
- **Observation records self-describing** — each carries `noise_sigma`
  and `unit` so consumers don't need to load profiles.
- **LoRa links recorded per attempt** (success / dropped /
  out_of_range), with successful attempts also producing `lora_toa`
  observation records. Errors are explicit — `status` is one of the
  three documented values; unknown statuses raise `ValueError` at
  parse time.
- **Fixed fleet composition** (`--nodes 10` required; other values
  rejected with a clear error). The flag is a sanity check; scale
  evolution comes later.

## Tasks

1–2. Schema constants + header
3–4. Tick record structure + observation types (obs-only, scenario_schema.py)
5–6. ScenarioReader + __init__ export discipline
6A–6B. Truth types in scenario_truth_schema.py (separate module)
7–8. Golden trace helper
9. CLI contract
10. Seed reproducibility
11. Fleet composition in header
12. Tick loop / duration / sensor rate limits
13. LoRa link recording
14. Node truth (via ScenarioTruthReader)
15. Onboard map sidecar + header path reference
16. Generator implementation (linear main)
17. Golden trace fixture + regenerator
18. Verification (module boundary check included)

## Files Affected

- `rtl/vectors/maritime/scenario_schema.py` (new — observation types +
  `ScenarioReader`)
- `rtl/vectors/maritime/scenario_truth_schema.py` (new — truth types +
  `ScenarioTruthReader`)
- `rtl/vectors/maritime/_scenario_parse.py` (new — internal shared
  parsing helper)
- `rtl/vectors/maritime/gen_maritime_scenario.py` (new — generator CLI)
- `rtl/vectors/maritime/__init__.py` (MODIFIED — exports
  `ScenarioReader`, NOT `ScenarioTruthReader`)
- `tests/maritime/test_scenario_schema.py` (new)
- `tests/maritime/test_scenario_truth_schema.py` (new)
- `tests/maritime/test_scenario_gen.py` (new)
- `tests/maritime/golden_trace/m1_tiny.jsonl` (new — committed fixture)
- `tests/maritime/regenerate_golden_trace.py` (new — regen script)

## Spec Pointers

- `maritime-scenario-schema` → Requirement: Versioned JSONL Schema,
  Requirement: Header Record Structure, Requirement: Tick Record
  Structure, Requirement: ObservationRecord Structure, Requirement:
  LoraLinkRecord Structure, Requirement: ScenarioReader Strips Truth,
  Requirement: Golden Trace Comparison Helper
  openspec/changes/maritime-scenario-gen/specs/maritime-scenario-schema/spec.md

- `maritime-scenario-truth-schema` → Requirement: Truth Schema Module
  Location, Requirement: TruthTickView Structure, Requirement:
  ScenarioTruthReader Contract, Requirement: Truth Reader Consumers
  Are Explicit
  openspec/changes/maritime-scenario-gen/specs/maritime-scenario-truth-schema/spec.md

- `maritime-scenario-gen` → Requirement: CLI Invocation, Requirement:
  Seed Reproducibility, Requirement: Fleet Composition, Requirement:
  Tick Count Matches Duration, Requirement: Sensor Firing Respects
  SensorSpec, Requirement: LoRa Links Recorded Per Attempt,
  Requirement: Node Truth Recorded in Tick, Requirement: Onboard Map
  Reference in Header, Requirement: Golden Trace Committed and
  Regenerable
  openspec/changes/maritime-scenario-gen/specs/maritime-scenario-gen/spec.md
