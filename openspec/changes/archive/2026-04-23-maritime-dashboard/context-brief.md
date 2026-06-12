# Context Brief: maritime-dashboard

## Purpose

Visual validation layer for M1. Renders the scenario (truth +
observations) and the PF estimates side by side on a single HTML+Canvas
page. Catches the sim bugs numeric tolerances miss: nodes drifting
through coastlines, estimate trails leading truth by 180°, LoRa links
flickering implausibly. Closes the MVP loop — without it, the
pipeline's correctness depends entirely on numerical asserts.

## Key Decisions

- **Imports `ScenarioTruthReader` from
  `rtl.vectors.maritime.scenario_truth_schema`** — the dedicated
  truth-access module. Dashboard is an allowed truth consumer per the
  charter; import-linter contract registered by `maritime-pf-float`
  forbids PF modules from importing truth but does not include the
  dashboard.
- **No `focus_node_ids` concept.** Particle clouds come from the
  optional sidecar stream (`ParticleStreamReader`). Dashboard offers
  per-node drill-down toggles for every node that appears in the
  sidecar (discovered via `reader.node_ids_present()`). If no sidecar
  is loaded, drill-down UI is hidden; main rendering proceeds.
- **CLI flags**: `--scenario <path>`, `--estimates <path>`,
  `--particles <path>` (optional), `--port <int>` (default 8911),
  `--no-open`.
- **Explicit errors**: missing required file (scenario, estimates)
  exits nonzero with a named-file error. Missing optional particles
  file warns on stderr (loud, not silent) and proceeds without
  drill-down.
- **Canvas + vanilla JS**, no external JS libs. HTML/JS heredoc inside
  the Python script. Data inlined as JSON; no runtime fetch; pan/zoom
  in ~60 lines of JS.
- **No RMSE calculation, no pass/fail indicators** — visualization is
  orthogonal to judgment. M2 `maritime-validate` owns judgment.
- **Truth via `ScenarioTruthReader`** for per-tick node truth state.
  PF main stream via `PFEstimateReader` for mean/cov trails. Optional
  `ParticleStreamReader` for drill-down particle clouds.

## Tasks

1. CLI Contract — Tests (incl. optional particles handling)
2. HTML Inlining — Tests
3. Truth Reader Usage — Tests (imports from scenario_truth_schema)
4. No External Dependencies — Tests
5. Rendering Code Structure — Tests (incl. per-node drill-down
   population from sidecar)
6. CLI and HTML Rendering — Implementation
7. Canvas Rendering — Implementation
8. UI Interaction — Implementation
9. Smoke Test Harness — Implementation
10. Manual Verification (with + without particles)
11. Verification

## Files Affected

- `experiments/12_maritime_dashboard.py` (new)
- `tests/maritime/test_dashboard.py` (new)

## Spec Pointers

- `maritime-dashboard` → Requirement: CLI Invocation, Requirement:
  Single HTML Page with Inlined Data, Requirement: Dashboard Is an
  Allowed Truth Consumer, Requirement: No External JS Dependencies,
  Requirement: Coastline Rendered as Canvas Polygons, Requirement:
  Per-Class Node Icons, Requirement: Truth and Estimate Trails,
  Requirement: LoRa Link Rendering, Requirement: Particle Drill-Down
  from Sidecar, Requirement: Time Slider Scrubs All Layers Together,
  Requirement: Pan and Zoom, Requirement: Dashboard Smoke Test
  openspec/changes/maritime-dashboard/specs/maritime-dashboard/spec.md
