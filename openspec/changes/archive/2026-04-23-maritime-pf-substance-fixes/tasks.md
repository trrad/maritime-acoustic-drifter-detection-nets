## 1. Spec Lock-In (Already-Landed Code)

- [x] 1.1 Confirm `tests/maritime/test_pf_float_weight.py::test_lora_likelihood_matches_truth_side_2d_range_definition` passes against current `pf_float.py` — substance regression for `Requirement: LoRa TOA Range Likelihood Matches 2D Truth Sensor`
      (tests/maritime/test_pf_float_weight.py)
- [x] 1.2 Confirm `tests/maritime/test_pf_float_resample_estimate.py::test_step_n_effective_is_pre_resample` passes against current `pf_float.py::step` — substance regression for `Requirement: PFEstimateRecord.n_effective Is Pre-Resample`
      (tests/maritime/test_pf_float_resample_estimate.py)
- [x] 1.3 Confirm the `step performs predict → weight → estimate → resample` behavior in `pf_float.py::step` — inspect the method body and verify the four calls are ordered `predict`, `weight`, `estimate`, `resample`
      (rtl/vectors/maritime/pf_float.py)

## 2. Ballast Depth Invariant — Tests

- [x] 2.1 Write failing substance test: ballast-drifter `predict` holds every particle's `depth` constant across 10 ticks given non-zero `vz`, non-zero `pos_noise`, and a 42.0 m initial depth. Include an RNG-draw-count guard (count `Generator.normal` calls via a spy wrapper or by inspecting the `Generator.bit_generator.state` before / after) to pin the seeded-determinism contract.
      (tests/maritime/test_pf_float_predict.py::test_ballast_depth_invariant_across_predict_ticks)

## 3. Ballast Depth Invariant — Implementation

- [x] 3.1 Pin ballast depth in `PFFloat.predict` — in the `_CLASS_BALLAST_DRIFTER` branch of the predict step, remove the `particles[:, idx.depth] += particles[:, idx.vz] * dt_sec + pos_noise[:, 2]` update; replace with a no-op (no write to `idx.depth`) so depth stays at its initialization value. Preserve the `pos_noise` draw at the top of `predict` so RNG stream order is unchanged.
      (rtl/vectors/maritime/pf_float.py)

## 4. Verification

- [x] 4.1 `uv run pytest tests/maritime/ --no-header -q` — full suite green (498+ tests pass including the new depth-invariant test).
- [x] 4.2 `uv run lint-imports` — PF truth-separation contract clean (no regression from the depth-pin edit).
- [x] 4.3 `uv run pyright rtl/vectors/maritime/ tests/maritime/` — type-check clean.
- [x] 4.4 `openspec validate maritime-pf-substance-fixes --strict` — change artifacts validate.
- [x] 4.5 End-to-end smoke: regenerate one short scenario via `gen_maritime_scenario.py` (3 nodes, 60 s, all sensors) and run `run_pf_float.py` against it — confirm ballast-drifter mean `depth` across 60 s is constant to within float tolerance in the resulting `pf_estimates.jsonl`.
