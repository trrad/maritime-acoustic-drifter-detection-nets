## Why

`PFFloat._imu_log_likelihood` computes predicted linear acceleration via the finite-difference `(vx - prev_vx) / dt + accel_bias` (and analogously for vy, vz). It relies on `prev_vx`, `prev_vy`, `prev_vz`, `prev_heading` holding the previous tick's velocity/heading values. Truth-side `propagate_truth` at `dynamics.py:47-48` shifts `velocity → prev_velocity` and `heading → prev_heading` at the top of every tick. The PF's `predict` has no such shift — `prev_vx` etc. stay at whatever value they were sampled at during `_sample_initial_particles`, forever.

Consequence: the PF's IMU acceleration prediction is a finite difference between the latest velocity sample and a *random initial draw*. This is structurally wrong regardless of whether `_imu_log_likelihood` is ultimately used for weighting (a decision deferred to post-realistic-regime work). Fixing it here lands the right structural shape before any larger changes confound the baseline.

## What Changes

- **Production code**: `PFFloat.predict` mirrors the truth-side shift — at the top of the method, copy `(vx, vy, vz, heading) → (prev_vx, prev_vy, prev_vz, prev_heading)` BEFORE the velocity/heading updates that happen later in the method. One-shot numpy slice assignment, vectorized over particles.
- **Spec**: ADD Requirement `Predict Shifts Previous-Velocity And Previous-Heading Slots` in `maritime-pf-float`, with a substance scenario that constructs a PF, takes a snapshot of `(vx, prev_vx)`, calls `predict(dt)`, and asserts the new `prev_vx` equals the pre-predict `vx` (for every particle).
- **Test**: new substance test in `tests/maritime/test_pf_float_predict.py`.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `maritime-pf-float`: predict-stage shift invariant.

## Impact

- One production file edit (`rtl/vectors/maritime/pf_float.py` — 4 lines added at top of `predict`).
- One new substance test.
- One new spec requirement.
- No CLI change, no schema change, no import contract change.
- No behavioral change for scenarios whose PF doesn't use IMU weighting. Scenarios that do use IMU weighting will see numerically different particle weights (the IMU likelihood's `(vx - prev_vx)/dt` term now uses a real finite difference instead of a stale random draw). Expected direction: unchanged on runs where IMU weighting produces ESS ≈ 1 already (both broken and fixed produce catastrophic ESS collapse — root cause is the bias-subspace-orthogonality problem discussed in prior sessions, not this shift). No regression expected; correctness improves.
