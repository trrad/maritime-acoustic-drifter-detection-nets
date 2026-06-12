## Context

Three PF substance fixes have already landed in `rtl/vectors/maritime/pf_float.py`
(see commit `20f7eda`) but are not yet pinned in the standing specs:

1. **Step order** — the `step(...)` convenience method now runs
   `predict → weight → estimate → resample` (estimate before resample,
   not after). The existing standing-spec scenario describes the old
   order. Rationale: after systematic resampling, ESS is trivially
   equal to `n_particles`; the diagnostically-useful pre-resample ESS
   is what downstream consumers (dashboard, sweep harness) want.

2. **LoRa TOA likelihood 2D geometry** — the LoRa likelihood now
   evaluates range against the 2D horizontal distance between particle
   and anchor, matching the truth-side `LoRaTOASensor.sample` forward
   model (which assumes non-negligible differential depth would be
   unusable for range-only TOA). Before, it mismatched, giving bias
   even on perfect particles.

3. **`n_effective` is pre-resample** — a direct consequence of (1); the
   `PFEstimateRecord.n_effective` field now carries the ESS computed
   after `weight` and before `resample`. The existing
   `maritime-pf-estimate-schema` spec describes the field but says
   nothing about "pre-" or "post-"; this change clarifies.

A fourth fix — the **M1 ballast-depth invariant in predict** — is not
yet in code. In M1 the truth-side ballast drifter's pump is `pass`
(the `KIND_BALLAST_PUMP` branch at `dynamics.py:63-64` is a no-op),
and the `KIND_BALLAST_DRIFTING_POSE` branch (`dynamics.py:87-100`)
does not write `state[2]` (depth) — so truth-side depth is held
constant by construction. The PF predict, by contrast, advances
ballast particle depth as `depth += vz * dt + pos_noise[2]`
(`pf_float.py:319-321`), which lets the depth belief diverge from
truth across a 12 h run. The ballast-specific baro bug (256 km vs.
23 km LoRa-only) is an emergent consequence of this divergence +
baro's tight depth-pressure coupling. Pinning ballast depth constant
in PF predict is the minimum fix that restores the truth/PF symmetry
for M1; it is independent of the velocity-model and IMU fixes
scheduled for later stages.

## Goals / Non-Goals

**Goals:**
- Lock the three already-landed PF behaviors in the `maritime-pf-float`
  delta spec with substance (not shape) scenarios.
- Clarify `n_effective` semantics in the `maritime-pf-estimate-schema`
  standing spec so a fresh reader knows which ESS they are getting.
- Land the PF-side ballast depth invariant and pin it with a
  substance regression test.

**Non-Goals:**
- Re-architecting PF velocity (scheduled in Stage 3 —
  `maritime-velocity-model`).
- Fixing the IMU likelihood (scheduled in Stage 4 —
  `maritime-imu-likelihood-fix`).
- Resolving 2-anchor ambiguity (Stage 5).
- Changing any CLI flag, on-disk schema field, import-linter contract,
  or dashboard payload.

## Decisions

### D1. Estimate before resample in `step(...)`
The already-landed order is `predict → weight → estimate → resample`.
The delta spec MODIFIES the single `step performs predict → weight →
resample → estimate` scenario under `Requirement: Bootstrap Particle
Filter Pipeline` to reflect the new order.

Rationale: post-resample ESS equals `n_particles` by construction and
carries no information. Pre-resample ESS is the only ESS that varies
with observation informativeness — the dashboard already treats it as
a degeneracy indicator.

Alternative considered: keep the old order and compute pre-resample
ESS as a side effect of `weight`. Rejected because the `estimate`
phase is the natural home for all summary statistics (mean,
covariance, ESS), and splitting ESS computation away from the other
summary stats would complicate the dashboard payload assembly.

### D2. LoRa likelihood matches truth-side 2D geometry
The delta spec ADDS `Requirement: LoRa TOA range likelihood matches
2D truth sensor` with a substance scenario: a particle placed at
exact horizontal truth (`east, north` equal) but with a non-trivial
`depth` value scores log-likelihood 0 against a noiseless range obs
equal to the truth-side 2D distance.

Test already exists at
`tests/maritime/test_pf_float_weight.py::test_lora_likelihood_matches_truth_side_2d_range_definition`.

### D3. `n_effective` is pre-resample — documented in two specs
Both `maritime-pf-float` (producer) and `maritime-pf-estimate-schema`
(consumer contract) get the clarifying requirement. Dual-spec coverage
because the value is written by the PF but read by the dashboard /
sweep harness via the schema contract — a reader of only the schema
shouldn't have to chase source to know what semantics `n_effective`
carries.

Test already exists at
`tests/maritime/test_pf_float_resample_estimate.py::test_step_n_effective_is_pre_resample`.

### D4. Ballast depth is pinned in PF predict
The delta spec ADDS `Requirement: M1 Ballast Depth Invariant in Predict`.
Code change: `rtl/vectors/maritime/pf_float.py` predict branch for
`_CLASS_BALLAST_DRIFTER` replaces the current
`particles[:, idx.depth] += particles[:, idx.vz] * dt_sec + pos_noise[:, 2]`
with `particles[:, idx.depth] = particles[:, idx.depth]` — i.e. an
explicit no-op that mirrors the truth-side M1 invariant.

RNG-stream-order consideration: `pos_noise` is still sampled at the
top of `predict` regardless of class (see the class-branch-independent
comment at `pf_float.py:283-285`). Pinning depth does NOT change the
RNG draw count — it only stops using the `pos_noise[:, 2]` slice for
the depth axis. Existing seeded-determinism tests remain valid.

Alternative considered: gate depth evolution on an M1 config flag
(`"m1_pump_is_pass"`) so the code works for both M1 and M2. Rejected
because M1 and M2 will have distinct class names (`ballast_drifter`
vs. a future `active_ballast_drifter`), not a runtime flag. M2 will
reintroduce depth evolution under a new class.

### D5. Substance scenarios only
Every new `### Requirement:` below gets at least one scenario that
exercises content, not structure. The existing standing-spec
scenarios (`step performs ...`) were shape-ish — the delta either
modifies them into substance-testable form or layers substance
scenarios on top.

## Key Type Contracts

- `Requirement: Bootstrap Particle Filter Pipeline` → no type change;
  just the step-order contract on `PFFloat.step(...) -> PFEstimateRecord`.
  The `PFEstimateRecord.n_effective` field (already typed as
  `float`) now carries pre-resample ESS by contract.
- `Requirement: LoRa TOA range likelihood matches 2D truth sensor` →
  constrains the behavior of the `_lora_log_likelihood` helper; no
  public signature change.
- `Requirement: M1 Ballast Depth Invariant in Predict` → constrains
  the behavior of `PFFloat.predict(dt_sec: float) -> None` for the
  `_CLASS_BALLAST_DRIFTER` case; no signature change.
- `maritime-pf-estimate-schema` `n_effective: float` field — invariant
  clarified to `0 < n_effective <= n_particles` AND "value is the
  pre-resample ESS".

## Risks / Trade-offs

- [Ballast depth now drifts from truth under mis-initialization] →
  If the initial particle cloud is seeded with a non-zero depth, the
  pin holds that wrong depth forever. Mitigation: M1 `PFInitRecord`
  already initializes ballast particles at the surveyed ballast depth
  (via `_sample_initial_particles`); the pin is a no-op on correct
  init and a correctness guard on mis-init. A separate init-correctness
  test already exists in `test_pf_float_construct.py`.
- [Spec drift vs. pre-resample ESS diagnostic] → Downstream consumers
  that rely on post-resample ESS would break. Mitigation: none
  needed — there are no such consumers in the repo today (grep for
  `n_effective`: only the dashboard and sweep harness, both expect
  pre-resample already).
- [RNG stream order surprise from depth-pin] → Explicit design note
  (D4): pin does not change the `pos_noise` draw count, only its use.
  Existing seeded-determinism tests catch any regression.

## Migration Plan

- Land the delta spec + the one-line PF code change + the ballast
  depth substance test in a single apply step.
- `/opsx:verify` asserts the three already-existing regression tests
  still pass and the new depth-invariant test passes.
- `/opsx:sync` promotes delta requirements into
  `openspec/specs/maritime-pf-float/spec.md` and
  `openspec/specs/maritime-pf-estimate-schema/spec.md`.

## Open Questions

None. All four fixes are scoped; the one open architectural question
(does velocity remain a state dim?) belongs to Stage 3.
