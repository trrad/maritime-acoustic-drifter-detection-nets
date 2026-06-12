## 1. Truth Dynamics — Tests

- [x] 1.1 Write failing substance test: truth drifter residual `state[3]` sequence over 1000 ticks has sample stddev in `[0.5, 1.5] * DRIFTER_VEL_PERTURBATION_MS` and lag-1 autocorrelation `|r| < 0.2`. Pin the "per-tick independent sampling, not RW" contract.
      (tests/maritime/test_dynamics.py::test_drifter_velocity_residual_is_tick_independent)
- [x] 1.2 Write failing substance test: under zero perturbation (temporarily patch `DRIFTER_VEL_PERTURBATION_MS` to 0.0) and zero position noise, pure-drifter position after 10 ticks at `(0.2, 0) m/s` equals `(120.0, 0.0)` within 0.1 m, and `state[3]` stays exactly 0 every tick.
      (tests/maritime/test_dynamics.py::test_drifter_pure_advection_under_zero_perturbation)
- [x] 1.3 Write failing substance test: over a 12-hour run (720 ticks at 60 s) on a drifting-surface pose, `max(|state[3]|)` < `5 * DRIFTER_VEL_PERTURBATION_MS`. Guards against regression to the RW model.
      (tests/maritime/test_dynamics.py::test_drifter_residual_bounded_over_12h)

## 2. Truth Dynamics — Implementation

- [x] 2.1 Replace the velocity random-walk update in the `DRIFTING_SURFACE_POSE` and `BALLAST_DRIFTING_POSE` branches of `propagate_truth` with per-tick Gaussian sampling. Introduce `DRIFTER_VEL_PERTURBATION_MS = 0.02`. Retire `VEL_PROCESS_NOISE_MS_PER_SQRT_S`. Keep the position update formula `(vel + current) * dt + pos_noise` unchanged.
      (rtl/vectors/maritime/dynamics.py)
- [x] 2.2 Audit consumers of `VEL_PROCESS_NOISE_MS_PER_SQRT_S` — remove or update any import. If a test references it, fail the test build loudly rather than silently substituting.
      (rtl/vectors/maritime/, tests/maritime/)

## 3. PF Predict — Tests

- [x] 3.1 Write failing substance test: with climatology `var_vx=0.04, var_vy=0.01` and `floor=0.02`, one predict tick yields particle-vx sample stddev in `[sqrt(0.04) + 0.02 ± margin]` and mean ≈ 0.0 (within `3σ/sqrt(n)`).
      (tests/maritime/test_pf_float_predict.py::test_particle_velocity_sampling_sigma_matches_climatology_plus_floor)
- [x] 3.2 Write failing substance test: climatology `var=0` everywhere, `floor=0.02` → predict yields particle-vx stddev in `[0.5, 1.5] * 0.02`, not a collapsed single value.
      (tests/maritime/test_pf_float_predict.py::test_particle_velocity_floor_prevents_collapse_on_zero_variance_climatology)
- [x] 3.3 Write failing substance test: over 100 predict ticks, per-particle `|vx|` max is bounded by `5 * (sqrt(var_vx) + floor)` and particle-mean `vx` stays within `3σ/sqrt(n)` of 0 at each tick (no RW accumulation).
      (tests/maritime/test_pf_float_predict.py::test_particle_velocity_residual_stays_bounded_over_100_ticks)
- [x] 3.4 Write failing substance test: pure-drifter PF with matching climatology `(0.2, 0)` and zero observations, tracks a truth pure drifter advected at 0.2 m/s eastward within a climatology-std envelope after 10 ticks.
      (tests/maritime/test_pf_float_predict.py::test_pf_mean_tracks_truth_under_matched_climatology_zero_obs)

## 4. PF Predict — Implementation

- [x] 4.1 Rework `PFFloat._advect_horizontal_and_velocity` — drop the RW update of `vx, vy`; sample per-tick from climatology-variance-plus-floor Gaussian. Preserve the `vel_noise` draw at the top of `predict` (RNG stream-order determinism).
      (rtl/vectors/maritime/pf_float.py)
- [x] 4.2 Change `PFFloatConfig.process_noise_vel_ms_per_sqrt_s` default from 0.05 to 0.02 and update its docstring to describe the new "per-tick sampling σ floor" semantic.
      (rtl/vectors/maritime/pf_float.py)

## 5. Test Rework (Existing Tests Assuming RW Semantics)

- [x] 5.1 Inventory every test that imports `VEL_PROCESS_NOISE_MS_PER_SQRT_S` or asserts on velocity-RW behavior. Rewrite each to match the new per-tick-sampling semantic OR drop if the behavior it pinned is no longer meaningful. Report the list to the human before dropping tests.
      (tests/maritime/)

## 6. Verification

- [x] 6.1 `uv run pytest tests/maritime/ --no-header -q` — full suite green after test rework.
- [x] 6.2 `uv run lint-imports` — clean.
- [x] 6.3 `uv run pyright rtl/vectors/maritime/ tests/maritime/` — no new type errors.
- [x] 6.4 `openspec validate maritime-velocity-model --strict` — change artifacts validate.
- [x] 6.5 End-to-end smoke: gen a 1-hour scenario at dt=60, mean-flow-east=0.2, with all sensors; run PF with default config; verify per-class RMSE in `pf_summary.json` shows (a) pure-drifter position RMSE bounded (< 100 m median over 1 h if 2-anchor geometry is in range — probabilistic end-to-end milestone) and (b) no velocity-RW runaway visible in the dashboard trail.

## 7. Follow-Up (NOT part of this change's apply)

- [ ] 7.1 Separate commit: regenerate golden trace under the new dynamics. Commit message: `maritime: regenerate golden trace after velocity-model change (cf. maritime-velocity-model)`.
- [ ] 7.2 Separate manual dashboard check. Confirm drifter trails look physical post-change.
