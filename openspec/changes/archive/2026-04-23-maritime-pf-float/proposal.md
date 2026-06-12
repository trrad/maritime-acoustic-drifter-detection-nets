## Why

The scenario generator will soon be producing JSONL streams, but nothing
consumes them yet. M1 needs a float64 reference PF that:

- Runs at realistic dimensionality (15 / 21 / 25 D per node class)
- Consumes scenarios through `ScenarioReader` only (never truth)
- Produces estimate records that the dashboard and later validation tooling
  can read
- Provides the reference implementation the LNS8-delta PF (M2) will be
  benchmarked against

Three distinct things to keep separate:
- **Truth**: the one real value for each node at each tick, produced by
  `propagate_truth` and stored in the scenario's truth records.
- **Per-node PF estimate**: the output of a single node's PF running on its
  own observations. One `PFFloat` instance per node, no cross-node sharing.
  This is what this change produces.
- **Fleet-level estimate**: a post-hoc reconciliation across nodes. Not M1.

What we are deliberately **not** asserting in the spec:

- **No RMSE thresholds as spec requirements.** Earlier drafts asserted
  arbitrary per-class convergence bounds (anchors < 100 m, etc.). Those
  numbers were intuition-based, not grounded in measurement or operational
  need. Per AGENTS.md "no unprincipled numeric thresholds in specs," the
  PF emits a measurement report (`pf_summary.json`) with per-class RMSE
  and ESS aggregates that we inspect. Binding thresholds get established
  later, once grounded.

What we are doing differently from the earlier draft:

- **Truth separation via AST, not regex.** Earlier draft had a test scanning
  `pf_float.py` source text for the substring `"ScenarioTruthReader"`. That's
  regex-level enforcement, exactly the anti-pattern AGENTS.md calls out.
  Replaced with the `import-linter` contract delivered by
  `project-infra-import-linter` — an AST-based import-graph check that runs
  in CI.
- **No `focus_node_ids` concept in the schema.** Main estimate stream emits
  `mean + cov_diag + n_effective` for all nodes, every tick. Particle clouds
  are a sidecar stream with configurable thinning — scales to 100-1000 node
  fleets without a "pick 3 nodes to be privileged" hack.
- **Typed `ParticleStreamReader` / `ParticleStreamWriter`.** JSONL backing
  today, binary backing swappable later without touching `pf_float.py` or
  dashboard.

## What Changes

- Introduce `rtl/vectors/maritime/pf_float.py` — bootstrap (SIR) particle
  filter: predict / weight / resample / estimate, vectorized across particles.
  Per-node PF instance; no fleet-level fusion. Accepts observation types
  only (pyright strict rejects passing truth types).
- Introduce `rtl/vectors/maritime/pf_estimates_schema.py` — the main
  estimate stream contract (`mean + cov_diag + n_effective` per node per
  tick) AND the particle sidecar contract (`ParticleStreamReader` /
  `ParticleStreamWriter` with a JSONL implementation in M1). No
  `focus_node_ids` field anywhere.
- Introduce `rtl/vectors/maritime/run_pf_float.py` — CLI accepting
  `--scenario`, `--out` (main stream), `--particles-out` (optional sidecar
  path), `--thin-ticks N` (default 1), `--thin-particles K` (default 50),
  `--thin-nodes IDS` (default all), `--no-particles` (disables sidecar),
  `--n-particles` (default 500). Default behavior: emit main stream; emit
  particle sidecar with `thin_ticks=1, thin_particles=50, thin_nodes=all`
  if `--particles-out` given.
- Emit a companion `pf_summary.json` alongside the estimate stream
  containing per-class RMSE aggregates (measured, not asserted), ESS
  trajectory stats, and a completion flag. The summary is a measurement
  report for human review, not a spec assertion.
- Observation likelihood functions for the six M1 sensors: GPS (Gaussian
  on position), IMU (Gaussian on 6-tuple), baro (Gaussian on depth via
  hydrostatic inversion), mag (Gaussian on heading with wrap-aware
  distance), bathy probe (Gaussian on onboard-map depth; particles on
  land get zero weight), LoRa TOA **to anchors only** (Gaussian on
  range).
- Drifter-to-drifter LoRa TOA observations are recorded in the scenario
  but NOT consumed by the M1 PF. Fleet coordination is M2+.
- Particles stored as `(n_particles, state_dim)` float64 numpy array.
  Default `n_particles = 500`. Systematic resampling every tick.
- PF dynamics model uses climatology-based advection (sourced from
  the onboard map loaded via `ScenarioReader(path).onboard_map()` —
  the sidecar-backed accessor defined by `maritime-scenario-gen`),
  not the truth current field. The mismatch is operational realism.
- Truth separation enforcement is via:
  1. Module type signatures — `PFFloat` functions accept only
     `ObservationRecord` / `ObservationTickView` types (pyright strict).
     This is what keeps truth out of `PFFloat` even when other modules
     (such as `run_pf_float.py`) read it for their own reporting.
  2. Import-linter contract (delivered by `project-infra-import-linter`) —
     forbids the library module `rtl.vectors.maritime.pf_float` from
     importing `scenario_truth_schema` (to be split out in
     `maritime-scenario-gen`) and `current_fields`. `run_pf_float.py`
     is intentionally NOT in the contract's `source_modules` — it is
     the final reporting layer that owns `pf_summary.json` (per-class
     RMSE aggregates), and RMSE needs truth. The operational boundary
     is "the node-level algorithm cannot see truth"; a workstation
     orchestrator that runs the algorithm and measures it against
     truth afterwards is allowed.
  3. Onboard map sourcing — PF consumes the onboard map via
     `ScenarioReader(path).onboard_map()` (sidecar-backed accessor
     defined by `maritime-scenario-gen`). The PF never calls
     `make_onboard_map` and never receives the truth map.

## Capabilities

### New Capabilities

- `maritime-pf-estimate-schema`: Versioned JSONL format for PF output
  streams. Defines the main estimate stream (`mean + cov_diag +
  n_effective` per node per tick) and the particle sidecar stream
  (`(t, node_id, particles, weights)` records with thinning). Provides
  `PFEstimateReader` (main), `ParticleStreamReader` / `ParticleStreamWriter`
  (sidecar). No `focus_node_ids` concept.
- `maritime-pf-float`: Bootstrap float64 particle filter. Defines the
  predict / weight / resample / estimate pipeline, the six observation
  likelihood functions (LoRa TOA restricted to anchor references in M1),
  the CLI with thinning knobs, and `pf_summary.json` measurement output.
  Runs as one independent PF per node; no fleet-level fusion. Consumes
  `ScenarioReader` only.

### Modified Capabilities

(none directly — `maritime-scenario-gen` will split `scenario_truth_schema`
as a separate module in its own change; `project-infra-import-linter`
delivers the AST enforcement tooling this change relies on)

## Impact

- **New files**: `rtl/vectors/maritime/pf_float.py`,
  `rtl/vectors/maritime/pf_estimates_schema.py`,
  `rtl/vectors/maritime/run_pf_float.py`,
  `tests/maritime/test_pf_float.py`,
  `tests/maritime/test_pf_estimates_schema.py`.
- **Changes to `pyproject.toml`**: appends a new import-linter contract
  entry to `[[tool.importlinter.contracts]]` — "PF library does not
  access truth" — listing `rtl.vectors.maritime.pf_float` as the sole
  source module and `scenario_truth_schema`, `current_fields` as
  forbidden. `run_pf_float.py` is intentionally exempt (owns the final
  reporting layer and reads truth via `ScenarioTruthReader` for the
  `pf_summary.json` RMSE aggregates). Requires
  `project-infra-import-linter` to have landed first.
- **Dependencies on earlier changes**: `project-infra-import-linter`
  (import-linter tooling); all M1 changes through
  `maritime-scenario-gen` (consumes `ScenarioReader`, loads the
  pre-built onboard map via `ScenarioReader(path).onboard_map()`);
  `maritime-fleet-dynamics` (imports `StateLayout` to interpret
  estimates); `maritime-typed-observations` (consumes the typed
  `Observation` union, including `LoraTOAObservation.partner_id`).
- **Downstream consumers**: `maritime-dashboard` overlays the estimate
  stream + particle sidecar; M2's `maritime-validate` compares against
  truth (via `ScenarioTruthReader`); M2's `maritime-pf-lns8-delta` ports
  this reference to LNS8 arithmetic.
- **Frozen baseline**: untouched.
- **Simulation integrity charter**: honors truth separation via module
  boundaries + import-linter contract + type signatures. PF dynamics
  diverges from truth dynamics (climatology vs. real field) — this is
  the operational-realism model.
