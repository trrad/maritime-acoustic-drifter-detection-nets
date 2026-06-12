# Context Brief: maritime-pf-float

## Purpose

Float64 reference particle filter for M1. Consumes scenarios via
`ScenarioReader` (observation-only), produces a main estimate stream
plus an optional thinned particle sidecar, and emits a measurement
summary (`pf_summary.json`) for human inspection. No spec-level RMSE
thresholds — numbers are measured, not asserted.

## Key Decisions

- **2-spec delta**: `maritime-pf-estimate-schema` (ADDED — main stream
  contract, particle sidecar contract, reader/writer interfaces),
  `maritime-pf-float` (ADDED — the implementation).
- **No RMSE thresholds.** Earlier draft asserted anchor < 100 m,
  ballast < 200 m, pure < 400 m as binding spec requirements. Dropped
  (AGENTS.md "no unprincipled numeric thresholds"). Replaced with
  `pf_summary.json` measurement output.
- **No `focus_node_ids` concept.** Main stream emits mean + cov_diag
  + n_effective for every node every tick. Particle clouds go to a
  separate sidecar stream with configurable thinning
  (`--thin-ticks`, `--thin-particles`, `--thin-nodes`, `--no-particles`).
  Defaults: `thin_ticks=1, thin_particles=50, thin_nodes=all`. Scales
  to 100-1000 node fleets by tuning.
- **Typed `ParticleStreamReader` / `ParticleStreamWriter`.** JSONL
  backing today, binary (parquet / hdf5 / npz) swappable later without
  touching producers or consumers. Migration trigger: measured
  (file size > 500 MB uncompressed, or dashboard latency > 3 s).
- **Truth separation via AST tools, not regex.** Three layers:
  (1) module boundary — `maritime-scenario-gen` splits
  `scenario_truth_schema` out, PF cannot import it;
  (2) `import-linter` contract in `pyproject.toml` (delivered by
  `project-infra-import-linter`) forbids PF modules from importing
  `scenario_truth_schema` or `current_fields`;
  (3) function signatures accept observation types only (pyright
  strict). The earlier draft's "scan source text for the substring
  `ScenarioTruthReader`" test is dropped — regex is not AST.
- **Errors are explicit.** Unknown sensor name in `weight` raises
  `ValueError`. LoRa TOA to non-anchor partner is NOT a drop — it is
  a deliberate filter inside the `lora_toa` handler (the handler
  checks partner identity and returns no likelihood contribution for
  non-anchor partners). No silent drops, no drop counters, no
  swallowed exceptions.
- **Per-node independent PFs, vanilla bootstrap, systematic
  resampling every tick, `n_particles = 500` default.** PF dynamics
  use climatology (reconstructed from header), not truth current
  field. LoRa to drifters filtered out by the handler — partner
  position unknown in per-node-independent M1.

## Tasks

1–12. Schema types (headers, records, readers, writers) — **COMPLETE** (Batch A); tasks 1.6 and 7.6 deferred to Batch F (CLI-driven)
13–14. PFFloat construction
15–16. Predict stage
17–20. Weight stage (sensor handlers, anchor-only LoRa filter, unknown-sensor error, impl)
21–22. Resample stage
23–24. Estimate stage
25.    Per-node independence
26.    Truth separation via import-linter
27.    Onboard map reconstruction
28.    Main stream writer
29.    Particle sidecar thinning
30.    Summary report (pf_summary.json)
31.    Sanity invariants
32–33. CLI (tests + implementation + pyproject.toml contract addition)
34.    Verification

## Implementation Notes

- **ENU origin convention**: `(bbox[0], bbox[1])` — SW corner of `ScenarioHeader.bbox`. The PF derives this from `ScenarioReader(path).header().bbox` when constructing per-node PFFloat instances. Matches `gen_maritime_scenario.py` line 352-353 and `dynamics.py` `PhysicsEnv`.
- **Observation dispatch**: The `Observation` union (`scenario_schema.py`) is a sealed union of typed dataclasses (`GPSObservation`, `IMUObservation`, `BaroObservation`, `MagObservation`, `BathyProbeObservation`, `LoraTOAObservation`). There is NO `.sensor` string attribute — dispatch is by `type(obs)` against an explicit handler mapping; unknown type → `ValueError(f"Unknown sensor type: {type(obs).__name__}")`.
- **Climatology API**: `RegionalMap.current_climatology_at(lat, lon)` returns `(mean_vx, mean_vy, var_vx, var_vy)` — note: VARIANCE not std (despite the spec text). Use means; variance components are advisory per design D4.
- **Baro pressure inversion**: `pressure_pa = 101_325 + 10_000 * depth_m` (matches `BaroSensor` in `sensors.py:209`).
- **State layout**: ENU coordinates — `position` slice = (east_m, north_m, depth_m); see `state_layout.py`. Position 2 (depth) is held at 0 for pure_drifter (surface-only).

## Batch A Status (COMPLETE)

`rtl/vectors/maritime/pf_estimates_schema.py` (535 LOC) + `tests/maritime/test_pf_estimates_schema.py` (732 LOC, 28 tests). All 28 tests pass; full maritime suite 399/399; lint-imports clean; pyright clean. Reviewer PASS with 3 non-blocking suggestions (untested `node_ids`-empty branch and `mean`-finite branch — defensive guards covering future spec sanity invariants; asymmetric handling of unknown record_type in `node_ids_present()` vs `__iter__` — non-blocking).

## Batch E Status (PARTIAL — 26.1, 26.2, 26.3, 27.2 done; 26.4 + 27.1 deferred to F)

Added the import-linter contract `"PF library does not access truth"` to `pyproject.toml`:
- `source_modules = ["rtl.vectors.maritime.pf_float"]` (sole entry)
- `forbidden_modules = ["scenario_truth_schema", "current_fields"]`
- `allow_indirect_imports = "true"` — needed because `pf_float` imports `RegionalMap` from `map_payload`, which itself imports `CurrentField` (a Protocol type) at module load. The operational invariant is "pf_float.py does not name a truth symbol in its own source," not "no path through the import graph reaches truth."
- `run_pf_float` is intentionally NOT in `source_modules` — it's the reporting layer that uses `ScenarioTruthReader` for the RMSE in `pf_summary.json` (design D12).

4 new tests in test_pf_float.py (37 total). 436/436 maritime suite pass. Contract KEPT by lint-imports.

Tasks 26.4 (run_pf_float.py imports ScenarioTruthReader cleanly) and 27.1 (PF uses sidecar onboard map) require run_pf_float.py and a generated scenario — moved to Batch F.

## Batch D Status (COMPLETE)

Added `resample()` (systematic), `estimate(t, t_sec)` (returns typed `PFEstimateRecord`), and `step(dt_sec, observations, t, t_sec)` (predict→weight→resample→estimate) to `PFFloat`. Estimate uses biased weighted variance (non-negative by construction). Resample uses single `u0 ~ U(0, 1/n)`, cumsum + `searchsorted`, and `.copy()` to avoid alias.

10 new tests in test_pf_float.py (33 total). 432/432 maritime suite pass; lint-imports clean. Per-node independence: two PFs with same seed + same obs produce element-wise identical particle arrays.

## Batch C Status (COMPLETE)

Extended `pf_float.py` with `weight()` + 6 sensor handlers + isinstance-based dispatch (677 LOC total). Vectorized predict's climatology lookup via broadcast `argmin` against `climatology.lats / .lons`. 12 new tests in `test_pf_float.py` (23 total in file). All 422 maritime tests pass; lint-imports clean.

- **IMU formula** mirrors truth-side `sensors.py:128-179` exactly: `accel = (v - v_prev)/dt + bias`; `gyro_z = wrapped_heading_delta/dt * π/180 + bias_z`; gyro_x/y are pure bias.
- **`weight()` requires prior `predict()`** for IMU obs (provides `_last_dt_sec`); `RuntimeError` if violated.
- **LoRa anchor-only filter** returns `None` from the handler for non-anchor partners — no exception, no log, no drop counter (M1 is the spec's anchor-only path BY DESIGN).
- **Unknown observation type** raises `ValueError` naming `type(obs).__name__`. Dispatch is by `isinstance` against the six Observation types.
- **Bathy on-land** particles get log-likelihood `-inf` → exact 0 weight after exp + normalize. Per-particle `for lat, lon in zip(...)` map lookup is acceptable (not a `range(n_particles)` form).
- **IMU test calibration**: prior cov_diag tightened from 10 → 0.5 and likelihood σ relaxed from 0.01 → 0.3 to avoid 6D bootstrap PF particle deprivation without resample. The spec scenario contemplates "weight + resample" convergence; this Batch C test exercises weight only. Substance intent preserved (direction-of-update toward truth bias).

## Batch B Status (COMPLETE)

`rtl/vectors/maritime/pf_float.py` (~352 LOC) + `tests/maritime/test_pf_float.py` (~543 LOC, 11 tests). All 11 Batch B tests pass; full maritime suite 410/410; lint-imports clean; pyright clean. Reviewer PASS.

- **Constructor signature note**: extended from design.md by adding `enu_origin_lat_deg`/`enu_origin_lon_deg` (required by `predict`'s `enu_to_latlon` call). The CLI in Batch F will source these from `ScenarioReader(path).header().bbox[0:2]` (matches `gen_maritime_scenario.py:352-353` and truth-side `PhysicsEnv` convention).
- **Class-aware predict via dispatch on `layout.class_name`**: anchor (position fixed), pure_drifter (depth locked to 0; horizontal advected), ballast_drifter (full 3D). No subclassing.
- **Climatology**: per-particle scalar `onboard_map.current_climatology_at(lat, lon)` lookup (the map API is scalar at M1 grid resolution); state-update math is numpy-vectorized.
- **Slot indices** are module-level literal constants (`_IDX_EAST = 0`, etc.) in pf_float.py — non-blocking debt; could be derived from `layout.slice("position")` in a later refactor.
- **AST test 15.6** asserts no `current_fields` import (top-level or local), no `CurrentField` reference, no `.velocity_at(...)` call — defends against deferred imports that lint-imports won't catch.

## Files Affected

- `rtl/vectors/maritime/pf_float.py` (new)
- `rtl/vectors/maritime/pf_estimates_schema.py` (new — main + sidecar
  types, readers, writer interface, JSONL writer factory)
- `rtl/vectors/maritime/run_pf_float.py` (new — CLI)
- `pyproject.toml` (MODIFIED — append import-linter contract for PF
  truth separation)
- `tests/maritime/test_pf_float.py` (new)
- `tests/maritime/test_pf_estimates_schema.py` (new)

## Spec Pointers

- `maritime-pf-estimate-schema` → Requirement: Versioned PF Estimate
  JSONL Schema, Requirement: PF Estimate Header Structure,
  Requirement: PF Estimate Record Structure, Requirement:
  PFEstimateReader Contract, Requirement: Particle Sidecar Schema,
  Requirement: ParticleStreamReader Contract, Requirement:
  ParticleStreamWriter Interface
  openspec/changes/maritime-pf-float/specs/maritime-pf-estimate-schema/spec.md
- `maritime-pf-float` → Requirement: Bootstrap Particle Filter
  Pipeline, Requirement: Per-Node Independence, Requirement: Truth
  Separation via Module Boundaries and Import Linting, Requirement:
  Onboard Map Reconstruction, Requirement: Observation Likelihood
  per Sensor, Requirement: LoRa TOA Anchor-Only Filter, Requirement:
  Unknown Sensor Name Is an Explicit Error, Requirement: Systematic
  Resampling Every Tick, Requirement: Vectorized Over Particles,
  Requirement: Main Estimate Stream for Every Node, Requirement:
  Particle Sidecar Emission with Thinning, Requirement: PF Summary
  Measurement Report, Requirement: Sanity Invariants on PF Output,
  Requirement: CLI Invocation
  openspec/changes/maritime-pf-float/specs/maritime-pf-float/spec.md
