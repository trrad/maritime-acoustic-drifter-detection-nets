## Why

All eight prior M1 changes deliver types and samplers. None of them run. `maritime-scenario-gen` is the integration point — the CLI-driven tick loop that composes platform profiles, state layouts, node classes, truth propagation, clock stubs, current fields, regional maps, and sensor observations into a JSONL stream that PFs and the dashboard consume.

Two orthogonal concerns ship in this one change:

1. **Scenario schema** — the versioned JSONL contract. Every PF implementation and every validation harness reads from this schema. If the producer and consumer drift apart, the pipeline silently breaks. A standing spec for the schema, plus a `ScenarioReader` that enforces truth separation (never yielding truth keys to PF code), is the only way to keep the pipeline honest as it evolves.

2. **Scenario generator** — the CLI + tick loop that writes the schema. Deterministic from seed (Tim's earlier ask that we dropped from `dev-infra` and re-parked here), parameterized by bbox and duration, composed from the bundled M1 profiles and map factories.

Both concerns are tightly coupled — the generator is the schema's only producer in M1 — so they ship in one change. They land as two delta specs because the schema is what downstream changes (PFs, dashboard, validation) will cite, and it deserves its own standing spec.

## What Changes

- Introduce `rtl/vectors/maritime/scenario_schema.py` — observation-only
  types: `ScenarioHeader`, `ObservationRecord`, `LoraLinkRecord`,
  `ObservationTickView`, and the `ScenarioReader(path)` iterator. Schema
  version constants live here. **No truth types in this module.**
- Introduce `rtl/vectors/maritime/scenario_truth_schema.py` — truth
  types in a separate module that PF code cannot import: `TruthTickView`,
  `ScenarioTruthReader`. Physical module split rather than "both readers
  in one file with a naming convention" — PF code that tries to import
  `ScenarioTruthReader` from `scenario_schema` fails at import time;
  any import from `scenario_truth_schema` is caught by the import-linter
  contract delivered by `project-infra-import-linter`.
- Introduce `rtl/vectors/maritime/gen_maritime_scenario.py` — CLI
  (`--seed`, `--nodes`, `--duration-hours` (default 24.0), `--dt-sec`
  (default 60.0), `--bbox`, `--out`) that builds the fleet (which
  already carries per-node `Clock` runtime components via the blueprint
  factories from `maritime-clock-model`), constructs truth and onboard
  maps, synthesizes the current field, and writes JSONL. Default `dt-sec = 60.0` reflects
  operational scale (multi-day drifter deployments, LoRa cycles in
  hours); override to `--dt-sec 1.0` or finer when specifically
  tuning TDMA slot alignment or acoustic TDOA. Byte-identical output
  for identical arguments (seed, bbox, duration, dt, nodes). The
  `--nodes` flag is a sanity check — fleet composition is fixed at
  2 anchors + 4 ballast + 4 pure = 10 in M1; non-10 values rejected
  explicitly.
- JSONL schema v1.0: one header record at line 1 (schema_version,
  bbox, fleet composition, `node_classes` mapping `node_id` to its
  class-name string, `duration_sec`, `dt_sec`), then one tick
  record per line containing node truth state, all observations
  produced this tick, and active LoRa links. `node_classes` lets
  downstream consumers (e.g., the dashboard) pick per-class icons
  or colors without relying on an implicit `node_ids` ordering
  convention.
- Observation records include a per-node-clock timestamp and the
  producing sensor's `noise_sigma` so PF implementations get
  everything they need without consulting profile data directly.
- Bundle a committed golden-trace fixture (`tests/maritime/golden_trace/m1_tiny.jsonl`)
  — 15-minute fine-resolution run (`--duration-hours 0.25 --dt-sec 1.0`,
  3 nodes) for fast CI regression. Any change that alters the tick
  loop must re-bless the fixture intentionally.
- Provide a `regenerate_golden_trace.py` CLI helper in `tests/maritime/` to rebuild the fixture when a bless is warranted.
- **No PF, no dashboard, no acoustic event model.** Those are their own changes.

## Capabilities

### New Capabilities

- `maritime-scenario-schema`: Observation-only JSONL scenario format.
  Defines `schema_version`, header record, tick record structure,
  observation subrecord, LoRa link subrecord, and `ScenarioReader` (the
  observation-only iterator that never yields truth state). Lives in
  `rtl/vectors/maritime/scenario_schema.py`. Every downstream consumer
  of observation data reads through `ScenarioReader` — no raw JSON
  parsing.
- `maritime-scenario-truth-schema`: Truth-access schema module for
  validation tooling and the dashboard. Defines `TruthTickView` and
  `ScenarioTruthReader`. Lives in
  `rtl/vectors/maritime/scenario_truth_schema.py` — a separate Python
  module that PF code cannot import without tripping the
  import-linter contract owned by `project-infra-import-linter`
  (contract entry landed by `maritime-pf-float`). Truth reader
  consumers (dashboard, M2 validation harness) explicitly import
  from this module, documenting their intent to read truth.
- `maritime-scenario-gen`: Deterministic CLI-driven scenario generator.
  Composes the M1 fleet (which carries per-node `Clock` components via
  its blueprint factories), truth/onboard maps, synthetic current field,
  and sensor samplers into a tick loop that writes JSONL conforming to
  `maritime-scenario-schema` v1.0 (and readable by both reader
  modules). Byte-identical output for identical `(seed, bbox, duration)`
  tuples. Ships a committed golden trace + regenerator script.

### Modified Capabilities

(none)

## Impact

- **New files**: `rtl/vectors/maritime/scenario_schema.py`, `rtl/vectors/maritime/gen_maritime_scenario.py`, `tests/maritime/test_scenario_schema.py`, `tests/maritime/test_scenario_gen.py`, `tests/maritime/golden_trace/m1_tiny.jsonl`, `tests/maritime/regenerate_golden_trace.py`.
- **Dependencies on earlier changes**: all of `maritime-platform-profile`, `maritime-state-layout`, `maritime-fleet-dynamics`, `maritime-clock-model`, `maritime-current-fields`, `maritime-map-payload`, `maritime-sensors`, `maritime-geo`. This is the integration point.
- **Downstream consumers**: `maritime-pf-float` reads scenarios via `ScenarioReader`; `maritime-dashboard` reads via `ScenarioReader` plus a parallel PF estimate stream; `maritime-validate` (M2) uses `ScenarioTruthReader` to compare truth against estimates.
- **Frozen baseline**: untouched.
- **Simulation integrity charter**: delivers truth separation (ScenarioReader never yields truth keys — forward-contract row from the charter table) and pulls together Level 0 physics, Level 1 sensors, Level 2 comms semantics (via LoRa sensor), Level 4 onboard map (via `make_onboard_map`). Clock stubs are the zero-offset Level 2 placeholder.
