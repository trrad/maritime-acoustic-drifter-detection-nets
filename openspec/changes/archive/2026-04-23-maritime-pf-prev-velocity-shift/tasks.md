## 1. Contract Test

- [x] 1.1 Write failing substance test `test_predict_shifts_prev_velocity_and_prev_heading` in `tests/maritime/test_pf_float_predict.py`. Construct a `PFFloat` with initial particles whose `(vx, vy, vz)` ≠ `(prev_vx, prev_vy, prev_vz)` and `heading` ≠ `prev_heading` (seed the initial cov_diag on those slots so they diverge). Snapshot particle array; call `predict(dt_sec=60.0)`; assert `prev_*` after == `*` from snapshot, all particles, float-exact equality.
      (tests/maritime/test_pf_float_predict.py)
- [x] 1.2 Write failing substance test `test_prev_velocity_tracks_one_tick_lag_across_multiple_predicts` in the same file. Run 5 predict ticks. At each tick, snapshot before and after. Assert `prev_vx` at tick N equals post-predict `vx` at tick N-1 (i.e. one-tick lag).
      (tests/maritime/test_pf_float_predict.py)

## 2. Implementation

- [x] 2.1 In `PFFloat.predict` at the top (after `sqrt_dt` computation but before any velocity/heading write), add vectorized shift:
      ```
      particles[:, idx.prev_vx] = particles[:, idx.vx]
      particles[:, idx.prev_vy] = particles[:, idx.vy]
      particles[:, idx.prev_vz] = particles[:, idx.vz]
      particles[:, idx.prev_heading] = particles[:, idx.heading]
      ```
      Single-line comment pointing to `dynamics.py:47-48` as the truth-side mirror.
      (rtl/vectors/maritime/pf_float.py)

## 3. Verification

- [x] 3.1 `uv run pytest tests/maritime/ --no-header -q` — full suite green (new tests pass, existing tests unchanged).
- [x] 3.2 `uv run lint-imports` — clean (no import-contract change expected).
- [x] 3.3 `uv run pyright rtl/vectors/maritime/pf_float.py tests/maritime/test_pf_float_predict.py` — no new errors.
- [x] 3.4 `openspec validate maritime-pf-prev-velocity-shift --strict` — artifacts validate.
- [x] 3.5 Confirm the 4-line production change matches design D1 (vectorized, top-of-predict, no per-particle loop).
