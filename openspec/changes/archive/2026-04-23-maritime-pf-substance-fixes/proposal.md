## Why

An exploratory debugging session using the maritime dashboard surfaced a
stack of PF substance bugs; three fixes have already landed in code (see
commit `20f7eda`) but the standing specs drift from the behavior that
the code now enforces. A fourth fix (M1 ballast-depth invariant in
predict) is small, independent, and tightly coupled to the same PF
predict step, so it batches here. This change locks the contract for
all four in delta specs with substance scenarios — not shape-only —
and lands the one remaining code change.

## What Changes

- MODIFY `Bootstrap Particle Filter Pipeline` step-order scenario from
  `predict → weight → resample → estimate` to
  `predict → weight → estimate → resample`. The already-landed code
  enforces this so that `PFEstimateRecord.n_effective` is the
  pre-resample ESS (diagnostic), not the trivially-equal-to-N value
  produced after systematic resampling.
- ADD `LoRa TOA range likelihood matches 2D truth sensor` requirement.
  Existing substance regression test lives at
  `tests/maritime/test_pf_float_weight.py::test_lora_likelihood_matches_truth_side_2d_range_definition`.
- ADD `PFEstimateRecord.n_effective is pre-resample` requirement.
  Existing substance regression test lives at
  `tests/maritime/test_pf_float_resample_estimate.py::test_step_n_effective_is_pre_resample`.
  A clarifying note on `n_effective` semantics also lands in the
  `maritime-pf-estimate-schema` standing spec.
- ADD `M1 Ballast Depth Invariant in Predict` requirement. **New code
  change**: `rtl/vectors/maritime/pf_float.py` will pin ballast
  particle depth constant across predict ticks (mirrors the truth-side
  `dynamics.py` M1 pump-is-pass invariant). New substance test lands
  at `tests/maritime/test_pf_float_predict.py::test_ballast_depth_invariant_across_predict_ticks`.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `maritime-pf-float`: step-order clarification; LoRa 2D-range likelihood
  substance contract; `n_effective` pre-resample contract; M1 ballast
  depth invariant in predict.
- `maritime-pf-estimate-schema`: clarify `n_effective` semantics
  (pre-resample ESS, bounded `0 < n_effective ≤ n_particles`).

## Impact

- `rtl/vectors/maritime/pf_float.py` — ballast-drifter branch of the
  predict step (~lines 316–321).
- `tests/maritime/test_pf_float_predict.py` — one new substance test.
- Standing specs for `maritime-pf-float` and `maritime-pf-estimate-schema`
  pick up the delta during `/opsx:sync`.
- No changes to CLI surface, on-disk schema, import-linter contracts,
  or dashboard payload shape.
