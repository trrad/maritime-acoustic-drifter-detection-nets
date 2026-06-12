## 1. Typed Observation Records — Schema

- [x] 1.1 Define six frozen dataclasses in `rtl/vectors/maritime/scenario_schema.py`: `GPSObservation`, `IMUObservation`, `BaroObservation`, `MagObservation`, `BathyProbeObservation`, `LoraTOAObservation`. Each carries `t_sec: float`, `node_id: str`, sensor-specific fields, and sigma fields with unit-suffixed names. Each implements `__post_init__` validation per spec D7.
      (rtl/vectors/maritime/scenario_schema.py)
- [x] 1.2 Define `Observation: TypeAlias = GPSObservation | IMUObservation | BaroObservation | MagObservation | BathyProbeObservation | LoraTOAObservation`. Update `ObservationTickView.observations` to `tuple[Observation, ...]`.
      (rtl/vectors/maritime/scenario_schema.py)
- [x] 1.3 Update `TruthTickView.observations` to `tuple[Observation, ...]` (in `scenario_truth_schema.py`); imports `Observation` from `scenario_schema`.
      (rtl/vectors/maritime/scenario_truth_schema.py)
- [x] 1.4 Remove the legacy `ObservationRecord` class. Tests/consumers that referenced it must be updated.

## 2. Typed Observation Records — Tests

- [x] 2.1 Each typed record constructs successfully with valid fields and rejects violating inputs (per `__post_init__` rules in spec D7).
      (tests/maritime/test_scenario_schema.py)
- [x] 2.2 `LoraTOAObservation` rejects `partner_id == node_id` with `ValueError`.
      (tests/maritime/test_scenario_schema.py)
- [x] 2.3 `IMUObservation` carries separate `accel_noise_sigma_ms2` and `gyro_noise_sigma_rad_s`; no joint `noise_sigma` field.
      (tests/maritime/test_scenario_schema.py)
- [x] 2.4 No typed record has a `unit` or `noise_unit` field — units live in field-name suffixes.
      (tests/maritime/test_scenario_schema.py)
- [x] 2.5 `Observation` union exhaustiveness — a `match` statement with one case per member exits cleanly under pyright strict (verified by a test file that pyright is configured to check; test asserts the function returns the expected tag for each input).
      (tests/maritime/test_scenario_schema.py)

## 3. JSONL Discriminant — Reader Dispatch

- [x] 3.1 `_parse_observations` in `_scenario_parse.py` discriminates on `record["type"]`; constructs the matching typed record; raises `ValueError` on unknown discriminant naming the offending value.
      (rtl/vectors/maritime/_scenario_parse.py)
- [x] 3.2 Reader rejects legacy v1.0 records that have `"sensor"` + `"value"` instead of `"type"` + per-sensor fields — `ValueError`. (No silent migration.)
      (rtl/vectors/maritime/_scenario_parse.py)

## 4. JSONL Discriminant — Tests

- [x] 4.1 Reader parses each known type discriminant into the matching record class — one test case per sensor type.
      (tests/maritime/test_scenario_schema.py)
- [x] 4.2 Reader on a tick record with `{"type": "sonar", ...}` raises `ValueError` naming `"sonar"`.
      (tests/maritime/test_scenario_schema.py)
- [x] 4.3 Reader on a tick record with the legacy `{"sensor": "gps", "value": [...], ...}` shape raises `ValueError`.
      (tests/maritime/test_scenario_schema.py)

## 5. Generator — Conversion

- [x] 5.1 `gen_maritime_scenario.py` per-sensor obs emit converts each `Measurement` to the matching typed `Observation`, dispatching on the sensor instance's class. For IMU specifically, the conversion reads both sigmas from `sensor.spec.noise_sigma` (accel) and `sensor.spec.noise_sigma_secondary` (gyro). For LoRa, the conversion populates `partner_id` from the pair loop (already in scope).
      (rtl/vectors/maritime/gen_maritime_scenario.py)
- [x] 5.2 Header / link records unchanged (only obs records get the typed treatment in this change).

## 6. Generator — Tests

- [x] 6.1 Generated scenario's obs records all carry `"type"` discriminants; no record has the legacy `"sensor"` + `"value"` keys.
      (tests/maritime/test_scenario_gen.py)
- [x] 6.2 LoRa obs records carry `"partner_id"` matching the other end of the corresponding link record.
      (tests/maritime/test_scenario_gen.py)
- [x] 6.3 IMU obs records carry both `accel_noise_sigma_ms2` and `gyro_noise_sigma_rad_s`, each equal to the producing sensor's spec field.
      (tests/maritime/test_scenario_gen.py)
- [x] 6.4 Existing test 12.7 (ObservationRecord content preservation) is rewritten as per-sensor checks — each sensor's typed record carries the spec-derived sigma(s) and field values.
      (tests/maritime/test_scenario_gen.py)

## 7. IMU Sensor — Dual Sigma

- [x] 7.1 `SensorSpec` gains `noise_sigma_secondary: float | None = None` field with the validation rules in maritime-sensors spec.
      (rtl/vectors/maritime/platform_profile.py)
- [x] 7.2 `IMUSensor.sample` applies `spec.noise_sigma` to accel channels and `spec.noise_sigma_secondary` to gyro channels.
      (rtl/vectors/maritime/sensors.py)
- [x] 7.3 Bundled IMU `SensorSpec` in `platform_profile.py` populates `noise_sigma_secondary=0.01` (matching current single-sigma value as starting point).
      (rtl/vectors/maritime/platform_profile.py)

## 8. IMU Sensor — Tests

- [x] 8.1 IMU sample applies independent sigmas to accel vs gyro channels (empirical std test per spec scenario "Accel and gyro sigmas are independent").
      (tests/maritime/test_sensors.py)
- [x] 8.2 `SensorSpec(name="imu", noise_sigma=0.01)` without `noise_sigma_secondary` raises `ValueError`.
      (tests/maritime/test_platform_profile.py)
- [x] 8.3 Negative `noise_sigma_secondary` raises `ValueError`.
      (tests/maritime/test_platform_profile.py)

## 9. Golden Trace

- [x] 9.1 Regenerate `tests/maritime/golden_trace/m1_tiny.jsonl` — same `--seed`, `--bbox`, `--created-at` as before; new bytes due to schema change.
      (tests/maritime/golden_trace/m1_tiny.jsonl, regenerated via tests/maritime/regenerate_golden_trace.py)
- [x] 9.2 `test_cli_output_matches_golden_trace` continues to pass against the new fixture.
      (tests/maritime/test_scenario_gen.py)

## 10. Verification

- [x] 10.1 `uv run pytest tests/maritime/` passes with zero failures.
- [x] 10.2 `uv run lint-imports` exits zero.
- [x] 10.3 `openspec validate maritime-typed-observations --strict` passes.
- [x] 10.4 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and pre-existing pre-maritime `rtl/vectors/*.py` files.
- [x] 10.5 Pyright strict on the changed modules — no new diagnostics.
