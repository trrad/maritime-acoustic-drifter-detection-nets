## 1. PF Estimate Schema Constants and Header — Tests

- [x] 1.1 `PF_ESTIMATE_SCHEMA_VERSION == "1.0"`; supported-versions frozenset contains `"1.0"`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 1.2 Valid header decodes into `PFEstimateHeader`; fields round-trip
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 1.3 Unknown `schema_version` raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 1.4 Non-positive `n_particles` rejected with `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 1.5 Header has no `focus_node_ids` attribute; has `node_ids` tuple covering the fleet
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 1.6 Header echoes CLI inputs + PF configuration — after invoking the CLI with `--scenario /tmp/s.jsonl --out /tmp/e.jsonl --n-particles 500`, `PFEstimateReader(out).header()` has `scenario_path` identifying the source scenario, `scenario_seed` from the source's header, `n_particles == 500`, `pf_impl == "float64_bootstrap"`, and `node_ids` membership matching the source scenario's fleet.
      (tests/maritime/test_pf_float.py — uses a generated scenario + CLI invocation)

## 2. PF Estimate Schema Constants and Header — Implementation

- [x] 2.1 `PF_ESTIMATE_SCHEMA_VERSION`, `SUPPORTED_PF_ESTIMATE_VERSIONS`, and `PFEstimateHeader` frozen dataclass with `__post_init__` validation (version in set, `n_particles > 0`, node_ids non-empty)
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 3. PFEstimateRecord — Tests

- [x] 3.1 Record has no `particles` or `weights` attribute — particle-level data is in the sidecar, not the main stream
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 3.2 Negative `cov_diag` entry raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 3.3 `n_effective` outside `(0, n_particles]` raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 3.4 `mean` length ≠ `cov_diag` length raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)

## 4. PFEstimateRecord — Implementation

- [x] 4.1 `PFEstimateRecord` frozen dataclass with `__post_init__` enforcing non-negative `cov_diag`, positive `n_effective` ≤ expected bound, equal `mean`/`cov_diag` lengths
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 5. PFEstimateReader — Tests

- [x] 5.1 Reader yields `PFEstimateRecord` instances (not dicts) — typed consumption
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 5.2 `reader.header().scenario_seed` matches the source scenario seed
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 5.3 Unknown `schema_version` on open raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)

## 6. PFEstimateReader — Implementation

- [x] 6.1 `PFEstimateReader(path)` with `header()` and `__iter__`; validates header on open
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 7. Particle Sidecar Header and Record — Tests

- [x] 7.1 Sidecar header has `thin_ticks >= 1`, `thin_particles >= 1`, `thin_particles <= n_particles_full`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 7.2 `thin_nodes=None` means all nodes (interpreted by reader as "no subset restriction")
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 7.3 Particle record shape matches header's `thin_particles × state_dim` — mismatch raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 7.4 Particle record weights sum to 1 ± 1e-6
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 7.5 Unknown `schema_version` in sidecar raises `ValueError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 7.6 Sidecar header echoes CLI configuration — after invoking the CLI with `--particles-out /tmp/p.jsonl --n-particles 500 --thin-ticks 10 --thin-particles 50 --thin-nodes n01,n05`, `ParticleStreamReader(p).header()` has `parent_estimate_path` identifying the main output, `scenario_seed` from the source scenario, `n_particles_full == 500`, `thin_ticks == 10`, `thin_particles == 50`, `thin_nodes == ("n01", "n05")`.
      (tests/maritime/test_pf_float.py — uses a generated scenario + CLI invocation)

## 8. Particle Sidecar Header and Record — Implementation

- [x] 8.1 `PFEstimateHeader_Particles` frozen dataclass with `__post_init__` validation
      (rtl/vectors/maritime/pf_estimates_schema.py)
- [x] 8.2 `ParticleRecord` frozen dataclass with shape/weight-sum validation in `__post_init__`
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 9. ParticleStreamReader — Tests

- [x] 9.1 Reader yields `ParticleRecord` instances (not dicts)
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 9.2 `reader.node_ids_present()` returns the frozenset of node_ids appearing at least once
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 9.3 Empty sidecar (header only, zero records) yields zero records and `node_ids_present()` returns `frozenset()`
      (tests/maritime/test_pf_estimates_schema.py)

## 10. ParticleStreamReader — Implementation

- [x] 10.1 `ParticleStreamReader(path)` with `header()`, `__iter__`, `node_ids_present()`
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 11. ParticleStreamWriter — Tests

- [x] 11.1 JSONL writer round-trip — writing header + 3 records, reading back yields identical field values
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 11.2 Writing a record before the header raises `RuntimeError`
      (tests/maritime/test_pf_estimates_schema.py)
- [x] 11.3 `close()` is idempotent — calling twice does not raise
      (tests/maritime/test_pf_estimates_schema.py)

## 12. ParticleStreamWriter — Implementation

- [x] 12.1 `ParticleStreamWriter` abstract interface (Protocol) with `write_header`, `write_record`, `close`
      (rtl/vectors/maritime/pf_estimates_schema.py)
- [x] 12.2 `make_jsonl_particle_writer(path)` factory returning a JSONL-backed implementation
      (rtl/vectors/maritime/pf_estimates_schema.py)

## 13. PFFloat Construction — Tests

- [x] 13.1 Construction with valid layout, initial mean + cov_diag, onboard map, anchor positions, config, rng succeeds
      (tests/maritime/test_pf_float.py)
- [x] 13.2 Initial mean shape mismatch rejected with `ValueError`
      (tests/maritime/test_pf_float.py)
- [x] 13.3 Negative cov_diag entry rejected with `ValueError`
      (tests/maritime/test_pf_float.py)
- [x] 13.4 Particle array initialized shape `(n_particles, state_dim)` with all-finite values
      (tests/maritime/test_pf_float.py)

## 14. PFFloat Construction — Implementation

- [x] 14.1 `PFFloat.__init__(node_id, layout, initial_state_mean, initial_state_cov_diag, onboard_map, anchor_positions, enu_origin_lat_deg, enu_origin_lon_deg, config, rng)` — initializes Gaussian particle cloud, uniform weights. (Signature extended from design.md by adding ENU origin params required by predict's `enu_to_latlon` call.)
      (rtl/vectors/maritime/pf_float.py)

## 15. Predict Stage — Tests

- [x] 15.1 Predict advances particles via climatology current + velocity + process noise — particles move in the expected direction
      (tests/maritime/test_pf_float.py)
- [x] 15.2 Predict is deterministic for a given RNG state
      (tests/maritime/test_pf_float.py)
- [x] 15.3 Predict preserves particle count
      (tests/maritime/test_pf_float.py)
- [x] 15.4 Anchor-class PF predict does not move position (moored)
      (tests/maritime/test_pf_float.py)
- [x] 15.5 Pure-drifter PF predict holds depth at 0
      (tests/maritime/test_pf_float.py)

- [x] 15.6 Predict call path never references truth current field — AST walk of `PFFloat.predict` and any helper it transitively calls within `pf_float.py` contains no `current_fields.CurrentField`, no `.velocity_at(...)` on a truth-field type, and no deferred import of `rtl.vectors.maritime.current_fields`. Guards against a local/deferred import that `lint-imports` wouldn't catch.
      (tests/maritime/test_pf_float.py)

- [x] 15.7 Predict-mean drift tracks climatology when process noise is muted — with an onboard-map climatology of `(vx=0.2, vy=0.0)` at the drifter's start position and process noise overridden to zero, 10 ticks of `predict(dt_sec=60)` advance the particle-mean east position by ≈ 120 m and the north position by ≈ 0 m, within integration tolerance.
      (tests/maritime/test_pf_float.py)

## 16. Predict Stage — Implementation

- [x] 16.1 `PFFloat.predict(dt_sec)` — vectorized particle advection dispatched by class-aware position model (anchor fixed, pure drifter surface-only, ballast drifter horizontally advected); injects process noise per `PFFloatConfig`
      (rtl/vectors/maritime/pf_float.py)

## 17. Weight Stage — Sensor Handlers — Tests

- [x] 17.1 GPS observation on anchor narrows position posterior — particle mean drawn toward GPS reading
      (tests/maritime/test_pf_float.py)
- [x] 17.2 Bathy likelihood zeroes particles on land (onboard map `is_on_land == True`)
      (tests/maritime/test_pf_float.py)
- [x] 17.3 Mag heading likelihood wraps angular distance at 0/360
      (tests/maritime/test_pf_float.py)
- [x] 17.4 Baro observation updates depth posterior via hydrostatic inversion
      (tests/maritime/test_pf_float.py)
- [x] 17.5 IMU observation updates accel/gyro bias posterior
      (tests/maritime/test_pf_float.py)
- [x] 17.6 LoRa TOA to anchor narrows range posterior — Gaussian range likelihood centered on anchor position
      (tests/maritime/test_pf_float.py)
- [x] 17.7 Weight stage is vectorized — no Python-level `for i in range(n_particles)` in the source of the four stage functions
      (tests/maritime/test_pf_float.py)

## 18. Weight Stage — Anchor-Only LoRa Filter — Tests

- [x] 18.1 LoRa TOA with anchor partner updates weights (filter passes)
      (tests/maritime/test_pf_float.py)
- [x] 18.2 LoRa TOA with non-anchor partner leaves weights unchanged — the handler returns no likelihood contribution
      (tests/maritime/test_pf_float.py)
- [x] 18.3 Non-anchor LoRa does not raise, does not log a drop, does not maintain a drop counter — filter is the M1 path by design
      (tests/maritime/test_pf_float.py)

## 19. Weight Stage — Unknown Sensor Is an Explicit Error — Tests

- [x] 19.1 Observation with unknown `sensor` name (e.g. `"sonar"`) causes `weight` to raise `ValueError` naming the offending sensor
      (tests/maritime/test_pf_float.py)
- [x] 19.2 All six recognized sensor names (`gps`, `imu`, `baro`, `mag`, `bathy_probe`, `lora_toa`) dispatch without error
      (tests/maritime/test_pf_float.py)

## 20. Weight Stage — Implementation

- [x] 20.1 `PFFloat.weight(observations)` dispatches by `observation.sensor`; raises `ValueError` for unknown sensor names; LoRa TOA handler internally filters on anchor partner (no weight update for non-anchor partners, no drop counter)
      (rtl/vectors/maritime/pf_float.py)

## 21. Resample Stage — Tests

- [x] 21.1 Post-resample weights are uniform (`1 / n_particles`)
      (tests/maritime/test_pf_float.py)
- [x] 21.2 Resample preserves particle count
      (tests/maritime/test_pf_float.py)
- [x] 21.3 Systematic resampling — with a known RNG state + known weights, output is deterministic
      (tests/maritime/test_pf_float.py)

## 22. Resample Stage — Implementation

- [x] 22.1 `PFFloat.resample()` — systematic resampling (cumulative sum + uniform offset); replaces particles; resets weights to uniform
      (rtl/vectors/maritime/pf_float.py)

## 23. Estimate Stage — Tests

- [x] 23.1 `mean` is weighted average; `cov_diag` is weighted variance
      (tests/maritime/test_pf_float.py)
- [x] 23.2 `n_effective == 1 / sum(weights^2)`
      (tests/maritime/test_pf_float.py)
- [x] 23.3 `step(dt_sec, observations, t, t_sec)` returns `PFEstimateRecord` with correct `t`, `t_sec`, `node_id`
      (tests/maritime/test_pf_float.py)
- [x] 23.4 Estimate record has no `particles` or `weights` attribute (those live in the sidecar)
      (tests/maritime/test_pf_float.py)

## 24. Estimate Stage — Implementation

- [x] 24.1 `PFFloat.estimate()` — weighted mean/cov + ESS; returns `PFEstimateRecord`
      (rtl/vectors/maritime/pf_float.py)
- [x] 24.2 `PFFloat.step(dt, observations, t, t_sec)` — wraps predict + weight + resample + estimate
      (rtl/vectors/maritime/pf_float.py)

## 25. Per-Node Independence — Tests

- [x] 25.1 Two `PFFloat` instances with identical init + RNG + observations produce identical particle arrays across ticks
      (tests/maritime/test_pf_float.py)
- [x] 25.2 Modifying one instance's particles does not affect another's
      (tests/maritime/test_pf_float.py)

## 26. Truth Separation via import-linter — Tests

- [x] 26.1 `pyproject.toml` contains an import-linter contract (named "PF library does not access truth" or equivalent) listing `rtl.vectors.maritime.pf_float` as the SOLE entry in `source_modules`, with `rtl.vectors.maritime.scenario_truth_schema` and `rtl.vectors.maritime.current_fields` in `forbidden_modules`. `rtl.vectors.maritime.run_pf_float` SHALL NOT appear in `source_modules` — the CLI is the final reporting layer and is permitted to use `ScenarioTruthReader` for RMSE computation in `pf_summary.json`. Contract uses `allow_indirect_imports = true` since the PF imports `RegionalMap` from `map_payload`, which itself imports `CurrentField` for type hints — the operational invariant is "pf_float.py does not name a truth symbol in its own source," not "the import graph cannot reach truth."
      (tests/maritime/test_pf_float.py — reads pyproject.toml and inspects the contract config)
- [x] 26.2 `uv run lint-imports` exits zero on the project as-implemented (no violations in the final state)
      (tests/maritime/test_pf_float.py — subprocess `uv run lint-imports`)
- [x] 26.3 Type-level check — `PFFloat.step`'s signature does not accept `ScenarioTruthReader`, `TruthTickView`, or `CurrentField` types (inspected via `typing.get_type_hints`). This is what keeps truth out of `PFFloat` even when `run_pf_float.py` reads it for the summary.
      (tests/maritime/test_pf_float.py)
- [x] 26.4 `run_pf_float.py` is allowed to import `ScenarioTruthReader` — after the CLI imports it and `uv run lint-imports` runs, the command exits zero. Test asserts the import is present (for summary computation) AND that no PF pipeline call site in `run_pf_float.py` passes a truth argument into `PFFloat` methods (pyright strict would reject; covered by 26.3 on the `PFFloat` side).
      (tests/maritime/test_pf_float.py — DEFERRED to Batch F since run_pf_float.py is built then)

## 27. Onboard Map From Scenario Reader — Tests

- [x] 27.1 PF constructed with `onboard_map=ScenarioReader(path).onboard_map()` uses the sidecar map's bathymetry for bathy_probe likelihood
      (tests/maritime/test_pf_float.py — DEFERRED to Batch F since it requires generating a real scenario)

- [x] 27.2 `pf_float.py` does not import `make_onboard_map` — AST walk catches both `from ... import make_onboard_map` and `map_payload.make_onboard_map` attribute access. (Note: import-linter cannot enforce function-level granularity in the `forbidden_modules` contract; the AST check is the binding test.)
      (tests/maritime/test_pf_float.py)

## 28. Main Stream Writer — Tests

- [x] 28.1 Main stream emits one `PFEstimateRecord` per `(tick, node_id)` for every node — no focus-node subset
      (tests/maritime/test_pf_float.py)
- [x] 28.2 Main stream records have no `particles` or `weights` fields
      (tests/maritime/test_pf_float.py)

## 29. Particle Sidecar Thinning — Tests

- [x] 29.1 Default thinning (`thin_ticks=1, thin_particles=50, thin_nodes=all`) with a 10-node 900-tick scenario produces 9000 particle records, each with `len(particles) == 50`
      (tests/maritime/test_pf_float.py)
- [x] 29.2 `--thin-ticks 10` reduces cadence — records only for `tick % 10 == 0`; header records `thin_ticks == 10`
      (tests/maritime/test_pf_float.py)
- [x] 29.3 `--thin-nodes n01,n05` — every particle record has `node_id in {"n01", "n05"}`; header records the tuple
      (tests/maritime/test_pf_float.py)
- [x] 29.4 `--no-particles` — no sidecar file is written; main stream and summary still written
      (tests/maritime/test_pf_float.py)
- [x] 29.5 Thinning filters AND — `--thin-ticks 5 --thin-nodes n01` produces records only for `(tick % 5 == 0) AND (node_id == "n01")`
      (tests/maritime/test_pf_float.py)

## 30. Summary Report (pf_summary.json) — Tests

- [x] 30.1 Summary file is written alongside main stream by default
      (tests/maritime/test_pf_float.py)
- [x] 30.2 Summary contains per-class RMSE aggregates (median, mean, p95) for `anchor`, `ballast_drifter`, `pure_drifter`
      (tests/maritime/test_pf_float.py)
- [x] 30.3 Summary contains per-node ESS stats (mean, min, max)
      (tests/maritime/test_pf_float.py)
- [x] 30.4 Summary contains `completed: true` when the run completes cleanly
      (tests/maritime/test_pf_float.py)
- [x] 30.5 Summary values are finite; no test asserts them against a specific threshold — measurement, not assertion
      (tests/maritime/test_pf_float.py)

## 31. Sanity Invariants — Tests

- [x] 31.1 Every estimate record's `mean` entries are finite (no NaN, no inf)
      (tests/maritime/test_pf_float.py)
- [x] 31.2 Every estimate record's `cov_diag` entries are ≥ 0 and finite
      (tests/maritime/test_pf_float.py)
- [x] 31.3 Every estimate record's `n_effective > 0`
      (tests/maritime/test_pf_float.py)
- [x] 31.4 Run completes with exit code 0 on a valid scenario
      (tests/maritime/test_pf_float.py)

## 32. CLI — Tests

- [x] 32.1 CLI with `--scenario` + `--out` (defaults elsewhere) exits 0 and produces a valid main estimate file and `pf_summary.json`
      (tests/maritime/test_pf_float.py)
- [x] 32.2 CLI rejects scenarios with an unsupported `schema_version` — exits nonzero, stderr names the version mismatch
      (tests/maritime/test_pf_float.py)
- [x] 32.3 Legacy `--focus-nodes` is not an accepted flag — CLI exits nonzero with an "unrecognized argument" error naming `--focus-nodes`
      (tests/maritime/test_pf_float.py)
- [x] 32.4 `--no-particles` writes only the main stream and summary; no sidecar
      (tests/maritime/test_pf_float.py)
- [x] 32.5 `--particles-out <path>` with default thinning produces a sidecar at the specified path
      (tests/maritime/test_pf_float.py)
- [x] 32.6 `--thin-ticks`, `--thin-particles`, `--thin-nodes` compose with AND as specified
      (tests/maritime/test_pf_float.py)

## 33. CLI — Implementation

- [x] 33.1 `run_pf_float.py` — argparse CLI with flags per spec; opens `ScenarioReader` (never `ScenarioTruthReader`); loads onboard map via `ScenarioReader(path).onboard_map()`; builds per-node `PFFloat` instances; drives tick loop; writes main stream, sidecar (if enabled), and summary JSON
      (rtl/vectors/maritime/run_pf_float.py)
- [x] 33.2 Add `pyproject.toml` import-linter contract: `name = "PF library does not access truth"`, `type = "forbidden"`, `source_modules = ["rtl.vectors.maritime.pf_float"]` (library only — `run_pf_float` is the final reporting layer and may use `ScenarioTruthReader` for the `pf_summary.json` RMSE aggregates), `forbidden_modules = ["rtl.vectors.maritime.scenario_truth_schema", "rtl.vectors.maritime.current_fields"]`
      (pyproject.toml)

## 34. Verification

- [x] 34.1 `uv run pytest tests/maritime/test_pf_estimates_schema.py tests/maritime/test_pf_float.py` passes with zero failures
- [x] 34.2 `uv run lint-imports` exits zero — no forbidden imports in `pf_float.py` or `run_pf_float.py`
- [x] 34.3 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and pre-existing `rtl/vectors/*.py` files
- [x] 34.4 Module imports cleanly — `uv run python -c "from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig; from rtl.vectors.maritime.pf_estimates_schema import PFEstimateReader, PFEstimateRecord, PFEstimateHeader, ParticleStreamReader, ParticleStreamWriter, ParticleRecord, PFEstimateHeader_Particles, make_jsonl_particle_writer"` exits 0
- [x] 34.5 `openspec validate maritime-pf-float --strict` passes
- [x] 34.6 End-to-end smoke — generate a 60 s scenario, run PF with defaults, open main stream + sidecar + summary, verify record counts match expected (ticks × fleet size for main; ticks × fleet size × thin_particles for sidecar at default thinning)
