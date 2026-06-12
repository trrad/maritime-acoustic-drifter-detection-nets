## 1. Schema Constants and Header — Tests

- [x] 1.1 `SCHEMA_VERSION == "1.0"`; `SUPPORTED_SCHEMA_VERSIONS` contains `"1.0"`
      (tests/maritime/test_scenario_schema.py)

- [x] 1.2 Valid header decodes successfully — all required fields present, `ScenarioHeader` returned
      (tests/maritime/test_scenario_schema.py)

- [x] 1.3 Header with bbox inversion (lat_south > lat_north) raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

- [x] 1.4 Header with `duration_sec <= 0` raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

- [x] 1.5 Unknown schema_version raises `ValueError` naming the version and the supported set
      (tests/maritime/test_scenario_schema.py)

- [x] 1.6 Missing header (first line is tick record) raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

## 2. Schema Constants and Header — Implementation

- [x] 2.1 `SCHEMA_VERSION`, `SUPPORTED_SCHEMA_VERSIONS` module constants; `ScenarioHeader` frozen dataclass with `__post_init__` validation
      (rtl/vectors/maritime/scenario_schema.py)

## 3. Tick and Observation Records — Tests

- [x] 3.1 Valid tick record decodes with expected `t`, `t_sec`, observation count, link count
      (tests/maritime/test_scenario_schema.py)

- [x] 3.2 Tick missing `t_sec` raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

- [x] 3.3 Valid `ObservationRecord` decodes with matching fields; unknown `sensor` raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

- [x] 3.4 `LoraLinkRecord` — successful link has `range_m`, dropped/out_of_range have `range_m is None`; success without range raises `ValueError`
      (tests/maritime/test_scenario_schema.py)

## 4. Observation Types — Implementation

- [x] 4.1 `ObservationRecord`, `LoraLinkRecord`, `ObservationTickView` frozen dataclasses with `__post_init__` validation — observation types ONLY; no truth types in this module
      (rtl/vectors/maritime/scenario_schema.py)

## 5. ScenarioReader (observation-only) — Tests

- [x] 5.1 `ScenarioReader` yields `ObservationTickView`; no yielded view has `node_truth`, `truth`, or `nodes` attributes; attribute access raises `AttributeError`
      (tests/maritime/test_scenario_schema.py)
- [x] 5.2 `ScenarioReader` yields typed views, not raw dicts
      (tests/maritime/test_scenario_schema.py)
- [x] 5.3 `scenario_schema.py` module defines no `ScenarioTruthReader` or `TruthTickView` — `from rtl.vectors.maritime.scenario_schema import ScenarioTruthReader` raises `ImportError`
      (tests/maritime/test_scenario_schema.py)

## 6. ScenarioReader (observation-only) — Implementation

- [x] 6.1 `ScenarioReader` with `header()` and `__iter__` returning `ObservationTickView`; strips `nodes` field before yielding
      (rtl/vectors/maritime/scenario_schema.py)
- [x] 6.2 `rtl/vectors/maritime/__init__.py` exports `ScenarioReader` but NOT `ScenarioTruthReader` (truth reader is not re-exported)
      (rtl/vectors/maritime/__init__.py)

## 6A. Truth Types (separate module) — Tests

- [x] 6A.1 `rtl/vectors/maritime/scenario_truth_schema.py` exists and defines `TruthTickView` and `ScenarioTruthReader`
      (tests/maritime/test_scenario_truth_schema.py)
- [x] 6A.2 `TruthTickView` frozen dataclass imports `ObservationRecord` and `LoraLinkRecord` from `scenario_schema` (not redefined)
      (tests/maritime/test_scenario_truth_schema.py)
- [x] 6A.3 `ScenarioTruthReader` yields `TruthTickView` with populated `node_truth` (per-node ndarrays with correct shape)
      (tests/maritime/test_scenario_truth_schema.py)
- [x] 6A.4 `ScenarioTruthReader.header()` returns a `scenario_schema.ScenarioHeader` (shared type, no duplicate)
      (tests/maritime/test_scenario_truth_schema.py)
- [x] 6A.5 Reader rejects unknown schema version with `ValueError`
      (tests/maritime/test_scenario_truth_schema.py)
- [x] 6A.6 `from rtl.vectors.maritime import ScenarioTruthReader` raises `ImportError` (not re-exported); `from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader` succeeds
      (tests/maritime/test_scenario_truth_schema.py)

## 6B. Truth Types — Implementation

- [x] 6B.1 Create `rtl/vectors/maritime/scenario_truth_schema.py` with `TruthTickView` frozen dataclass and `ScenarioTruthReader` class; share parsing helper via `_scenario_parse.py` internal module
      (rtl/vectors/maritime/scenario_truth_schema.py, rtl/vectors/maritime/_scenario_parse.py)
- [x] 6B.2 `ScenarioTruthReader` decodes `nodes` into `node_truth` ndarrays; imports `ObservationRecord`, `LoraLinkRecord`, `ScenarioHeader` from `scenario_schema`
      (rtl/vectors/maritime/scenario_truth_schema.py)

## 7. Golden Trace Helper — Tests

- [x] 7.1 Identical files match — `assert_golden_trace_matches` returns None
      (tests/maritime/test_scenario_schema.py)

- [x] 7.2 Single-byte difference raises `AssertionError` with a unified diff in the message
      (tests/maritime/test_scenario_schema.py)

## 8. Golden Trace Helper — Implementation

- [x] 8.1 `assert_golden_trace_matches(produced_path, golden_path)` function — byte-level comparison, unified diff on mismatch (use `difflib.unified_diff`)
      (rtl/vectors/maritime/scenario_schema.py)

## 9. CLI Contract — Tests

- [x] 9.1 CLI with valid args exits 0 and produces a parseable scenario file
      (tests/maritime/test_scenario_gen.py)

- [x] 9.2 CLI with `--nodes 5` exits non-zero; stderr contains a clear error
      (tests/maritime/test_scenario_gen.py)

- [x] 9.3 CLI with missing `--out` exits non-zero; stderr names the missing flag
      (tests/maritime/test_scenario_gen.py)

- [x] 9.4 Header echoes CLI `--bbox` and `--seed` verbatim — after invoking the CLI with `--seed 42 --bbox 48.4,-123.8,49.2,-123.2`, `ScenarioReader(out).header().bbox == (48.4, -123.8, 49.2, -123.2)` and `header.seed == 42`
      (tests/maritime/test_scenario_gen.py)

- [x] 9.5 Changing `--seed` changes `header.seed` — invoking twice with `--seed 42` and `--seed 43` yields files whose parsed headers have `seed == 42` and `seed == 43` respectively
      (tests/maritime/test_scenario_gen.py)

## 10. Seed Reproducibility — Tests

- [x] 10.1 Same-seed invocations produce byte-identical files
      (tests/maritime/test_scenario_gen.py)

- [x] 10.2 Different-seed invocations produce files that differ in at least one byte
      (tests/maritime/test_scenario_gen.py)

- [x] 10.3 CLI output with golden-trace args matches committed golden trace byte-for-byte
      (tests/maritime/test_scenario_gen.py)

## 11. Fleet Composition — Tests

- [x] 11.1 Header declares `fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}`; `len(node_ids) == 10`
      (tests/maritime/test_scenario_gen.py)

- [x] 11.2 Node IDs in deterministic order (anchors first, then ballast drifters, then pure drifters)
      (tests/maritime/test_scenario_gen.py)

- [x] 11.3 `header.anchor_positions` has one entry per anchor node_id; each `(lat, lon)` equals the corresponding `MooredPoseSpec.anchor_lat_deg` / `anchor_lon_deg` from that anchor's profile
      (tests/maritime/test_scenario_gen.py)

- [x] 11.4 `header.anchor_positions` keys equal exactly the anchor slice of `header.node_ids` (no missing anchor, no non-anchor)
      (tests/maritime/test_scenario_gen.py)

- [x] 11.5 Header parsing rejects a header whose `anchor_positions` is missing an anchor key or contains a non-anchor key — `ValueError`
      (tests/maritime/test_scenario_schema.py)

- [x] 11.6 `header.node_classes` reflects the actual node class of every fleet member — `header.node_classes[node_id] == node.profile.class_name` for every node in the fleet; counts per class equal `header.fleet_composition`; the anchor-valued keys equal `header.anchor_positions.keys()`
      (tests/maritime/test_scenario_gen.py)

- [x] 11.7 Header parsing rejects malformed `node_classes`: missing a `node_id`, containing an extraneous key not in `node_ids`, or having per-class counts that disagree with `fleet_composition` — `ValueError` in each case
      (tests/maritime/test_scenario_schema.py)

## 12. Tick Loop — Tests

- [x] 12.1 60 s scenario produces 60 tick records (plus 1 header = 61 lines)
      (tests/maritime/test_scenario_gen.py)

- [x] 12.2 Tick `t` values are `0..N-1` with no gaps, `t_sec` strictly increasing
      (tests/maritime/test_scenario_gen.py)

- [x] 12.3 GPS on anchor fires at most every 300 s (no two consecutive `gps` observations closer in `t_sec`)
      (tests/maritime/test_scenario_gen.py)

- [x] 12.4 Continuous sensor's effective minimum interval is `max(1.0/max_rate_hz, dt_sec)` — sensor can't fire faster than the tick rate even if its datasheet permits
      (tests/maritime/test_scenario_gen.py)

- [x] 12.5 Every profile-declared sensor produces at least one observation in a 2-hour (`--duration-hours 2 --dt-sec 60`) run — for every `node_id` and every `sensor_name` in that node's `profile.sensors`, the observation stream contains at least one matching record; no observation record has a `sensor` name absent from the owning profile. Share a session-scoped fixture with 12.6, 12.7, 12.8, and 13.3 (all run against the default-bundled-fleet 2-hour trace). 14.3 and 14.4 use distinct current-field setups (eddy / uniform) and their own short-horizon fixtures.
      (tests/maritime/test_scenario_gen.py)

- [x] 12.6 Each node class contributes its declared sensor suite — grouping the 2-hour observation stream by (class from `header.node_classes`, sensor) yields exactly `{gps, imu, baro, mag, lora_toa}` for anchor, and `{imu, baro, mag, bathy_probe, lora_toa}` for both ballast_drifter and pure_drifter
      (tests/maritime/test_scenario_gen.py)

- [x] 12.7 ObservationRecord preserves producing sensor's `noise_sigma` and `unit` — for every observation, `record.noise_sigma` equals `profile.sensor(sensor_name).noise_sigma` (single-measurement sensors) or `profile.comms.ranging_sigma_m` (`lora_toa`); `record.unit` matches the sensor-type-declared string
      (tests/maritime/test_scenario_gen.py)

- [x] 12.8 GPS observation value is within `3 * sigma_deg` of the anchor's surveyed `MooredPoseSpec` position, where `sigma_deg` is the sensor's `noise_sigma` converted from metres to degrees at the anchor's latitude — confirms end-to-end value preservation
      (tests/maritime/test_scenario_gen.py)

## 13. LoRa Links — Tests

- [x] 13.1 TDMA cycle ticks emit 45 link records (one per pair); non-cycle ticks emit 0. On cycle ticks, count of `lora_toa` observations equals `2 *` count of `success`-status link records (one obs per node end of every successful pair, both carrying the same `noisy_range`)
      (tests/maritime/test_scenario_gen.py)

- [x] 13.2 Out-of-range pair yields only a link record (status `out_of_range`, `range_m is None`); no observation
      (tests/maritime/test_scenario_gen.py)

- [x] 13.3 Successful link `range_m` matches the ENU planar distance (`sqrt(Δeast² + Δnorth²)`) between `node_a` and `node_b` truth positions within `3 * comms.ranging_sigma_m`; for the same tick, both `lora_toa` observations (one per end) have `value[0] == link.range_m` exactly (per design D9, both ends derive range from the same RTT)
      (tests/maritime/test_scenario_gen.py)

## 14. Node Truth — Tests

- [x] 14.1 `ScenarioTruthReader` (from `scenario_truth_schema`) yields `node_truth` for all 10 node_ids every tick; each array matches `layout.state_dim`
      (tests/maritime/test_scenario_gen.py)
- [x] 14.2 `ScenarioReader` (from `scenario_schema`) on the same file yields views with no truth attribute
      (tests/maritime/test_scenario_gen.py)
- [x] 14.3 Truth `surface_current` slice reflects the current field at each node's position — with a `SyntheticEddyField` that has at least one eddy inside the bbox, two anchors at distinct bbox corners have differing `surface_current` values at some tick, and every node's `surface_current` slice equals `field.velocity_at(node_lat, node_lon, t_sec)` within float tolerance
      (tests/maritime/test_scenario_gen.py)
- [x] 14.4 Truth position advances under uniform current — with `(vx=0.1, vy=0)` uniform current, `--dt-sec 60 --duration-hours 0.1` (6 ticks), a pure drifter's east-position slot at tick 5 has advanced by ≈ `0.1 * 5 * 60 = 30 m` relative to tick 0 (within 3× per-step process-noise sigma), and the north-position slot has advanced by ≈ 0 m
      (tests/maritime/test_scenario_gen.py)

## 15. Onboard Map Sidecar — Tests

- [x] 15.1 Header carries `onboard_map_path` naming the sidecar file relative to the scenario file's directory; the file exists on disk after generation
      (tests/maritime/test_scenario_gen.py)

- [x] 15.2 `ScenarioReader(path).onboard_map()` loads a `RegionalMap` whose bathymetry, coastline, and climatology fields match the onboard map the generator built (structural equality)
      (tests/maritime/test_scenario_gen.py)

- [x] 15.3 `ScenarioReader(path).onboard_map()` raises `FileNotFoundError` naming the expected sidecar when the sidecar file is absent
      (tests/maritime/test_scenario_gen.py)

- [x] 15.4 `ScenarioTruthReader(path).onboard_map()` returns a `RegionalMap` structurally equal to `ScenarioReader(path).onboard_map()`
      (tests/maritime/test_scenario_gen.py)

## 16. Scenario Generator — Implementation

- [x] 16.1 `gen_maritime_scenario.py` CLI with `argparse` — `--seed`, `--bbox` (parsed via argparse `type=parse_bbox`), `--duration-hours` (default 24.0), `--dt-sec` (default 60.0), `--nodes`, `--out`, `--created-at` (optional ISO 8601; default = wall-clock now); `--nodes != 10`, non-positive `--duration-hours`, non-positive `--dt-sec`, and malformed `--bbox` all rejected with explicit errors (not silent clamping)
      (rtl/vectors/maritime/gen_maritime_scenario.py)

- [x] 16.2 `main()` — linear sequence: parse args → build fleet (blueprint factories attach per-node `Clock` components) → truth map → onboard map → current field → climatology → per-node sensor instances → write header → tick loop → close
      (rtl/vectors/maritime/gen_maritime_scenario.py)

- [x] 16.3 RNG discipline — single top-level `numpy.random.Generator` from seed; sub-generators derived via `default_rng(parent.integers(...))` for each subsystem (fleet factory, dynamics noise, sensor noise, onboard map, climatology, LoRa drops); seeding order stable across code changes
      (rtl/vectors/maritime/gen_maritime_scenario.py)

- [x] 16.4 Header record written as line 1; tick records written one per line; output file has trailing newline after final record
      (rtl/vectors/maritime/gen_maritime_scenario.py)

## 17. Golden Trace Fixture and Regeneration

- [x] 17.1 Committed fixture `tests/maritime/golden_trace/m1_tiny.jsonl` under 15 MB; seed 42, small bbox (`48.6,-123.5,48.603,-123.497` — small enough to keep all 10 nodes within the 10 km LoRa range), `--duration-hours 0.25 --dt-sec 1.0` (15-minute fine-resolution run, 900 ticks × 10 nodes; the 100 KB cap from earlier drafts assumed a 3-node fleet, which `--nodes 10` rules out, and 10×10 = O(N²) LoRa pairs push the file size up). The fixture pins `--created-at 2026-04-22T00:00:00+00:00` so byte-identity holds across runs.
      (tests/maritime/golden_trace/m1_tiny.jsonl)

- [x] 17.2 `tests/maritime/regenerate_golden_trace.py` — re-invokes the CLI with the documented parameters (including the pinned `--created-at`) and writes to the fixture path; prints a confirmation message
      (tests/maritime/regenerate_golden_trace.py)

## 18. Verification

- [x] 18.1 `uv run pytest tests/maritime/test_scenario_schema.py tests/maritime/test_scenario_truth_schema.py tests/maritime/test_scenario_gen.py` passes with zero failures
- [x] 18.2 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and pre-existing `rtl/vectors/*.py` files
- [x] 18.3 End-to-end smoke — `uv run python rtl/vectors/maritime/gen_maritime_scenario.py --seed 42 --bbox 48.6,-123.5,48.9,-123.1 --duration-hours 0.25 --dt-sec 1.0 --nodes 10 --out /tmp/smoke.jsonl` exits 0 (15-minute fine-resolution run for fast CI); `ScenarioReader('/tmp/smoke.jsonl').header().schema_version == "1.0"`; both `ScenarioReader` and `ScenarioTruthReader` open the same file
- [x] 18.4 `openspec validate maritime-scenario-gen --strict` passes
- [x] 18.5 Golden trace matches — CLI with documented parameters produces output byte-identical to committed fixture
- [x] 18.6 Module boundary check — `from rtl.vectors.maritime.scenario_schema import ScenarioTruthReader` raises `ImportError`; `from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader` succeeds
