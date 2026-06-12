## Why

Both the truth dynamics and the PF predict currently evolve drifter
velocity as a **random walk** independent of the current field:

- **Truth:** `dynamics.py:25` uses `VEL_PROCESS_NOISE_MS_PER_SQRT_S = 0.005`;
  the truth drifter's `state[3:5]` accumulates this increment every
  tick, integrating to σ_v ≈ 0.6 m/s over 12 h — unphysical for a
  passive drifter whose velocity in reality is dominated by the local
  current.
- **PF:** `pf_float.py::_advect_horizontal_and_velocity` adds
  `vel_noise[:, 0:2]` to the particle `(vx, vy)` slot every tick,
  which compounds indefinitely. Over 12 h the PF's belief about
  drifter velocity degrades into a noise bubble with no physical
  grounding.

The state semantics are unchanged by this fix — particle / truth
`(vx, vy)` continues to carry the **velocity residual above the
climatology / truth-current-field mean**, and position advection
continues to compute `pos += (vx + cur_vx) * dt + pos_noise`.
What changes is the evolution of the residual itself: the residual
is re-sampled each tick from a zero-mean Gaussian with a tight,
climatology-variance-scaled width (on the PF side) or a small
truth-side perturbation scale. No persistence across ticks, no RW.

This matches the physical model: the drifter's effective velocity
equals the climatology / field current plus a tick-uncorrelated
perturbation bounded by the climatology variance. Across 12 h the
residual stays bounded instead of walking off.

## What Changes

- MODIFY `maritime-fleet-dynamics` truth-drifter propagation
  requirement: drifter velocity perturbation SHALL be a per-tick
  Gaussian sample (not a random-walk increment). A new constant
  `DRIFTER_VEL_PERTURBATION_MS = 0.02` replaces the RW scale; the
  legacy `VEL_PROCESS_NOISE_MS_PER_SQRT_S` is either deleted or
  retained at zero to make the RW contribution exactly zero.
- MODIFY `maritime-pf-float` Requirement "Predict Uses Climatology-
  Derived Current": in predict, particle velocity SHALL be re-sampled
  each tick as `N(0, sigma(lat, lon))` where `sigma(lat, lon) =
  sqrt(climatology.var_vxvy(lat, lon)) + floor` (floor = 0.02 m/s,
  covering climatology cells that report zero variance). The
  `process_noise_vel_ms_per_sqrt_s` PFFloatConfig field is repurposed
  as the floor (its default drops from 0.05 to 0.02 m/s); at dt=60s
  the per-tick perturbation σ is still tight (√60 × 0.02 ≈ 0.15 m/s
  max), but the absence of RW accumulation means a multi-hour run
  stays physically bounded.
- ADD substance scenarios:
  - Truth: under constant 0.2 m/s eastward flow and zero perturbation
    seed, a pure-drifter truth position advances exactly `0.2 * dt`
    per tick for 10 consecutive ticks (velocity residual stays ≈ 0
    because its RW is gone).
  - PF: under non-trivial climatology and zero observations, the PF
    particle-mean position RMSE stays bounded by a climatology-std
    envelope over a 10-minute predict-only run (envelope width
    computed from the climatology var, not a guessed number).
- **Code changes:**
  - `dynamics.py` drifter branches: replace
    `new_state[3] += vel_noise[0]` etc. with
    `new_state[3] = rng.normal(0.0, DRIFTER_VEL_PERTURBATION_MS)` etc.
    (Re-sample each tick, don't accumulate.)
  - `pf_float.py::_advect_horizontal_and_velocity`: replace
    `particles[:, idx.vx] += vel_noise[:, 0]` with
    `particles[:, idx.vx] = rng.normal(0.0, sigma_vx, size=n)`
    where `sigma_vx` is a per-particle broadcast of
    `sqrt(var_vx_at_particle) + floor`.
  - `PFFloatConfig.process_noise_vel_ms_per_sqrt_s` default: 0.05
    → 0.02 (the new "floor" semantic).

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `maritime-fleet-dynamics`: truth drifter velocity evolution.
- `maritime-pf-float`: PF predict velocity sampling.

## Impact

- `rtl/vectors/maritime/dynamics.py` — drifter branches of
  `propagate_truth` (lines 71-100). The
  `VEL_PROCESS_NOISE_MS_PER_SQRT_S` constant is retired (or zeroed).
- `rtl/vectors/maritime/pf_float.py` —
  `_advect_horizontal_and_velocity` helper (lines 342-359) and the
  `vel_noise` draw at lines 296-300 (the draw may persist for RNG
  stream-order determinism, even if unused for RW).
- `PFFloatConfig.process_noise_vel_ms_per_sqrt_s` default value
  changes from 0.05 to 0.02. CLI override `--predict-noise-vel`
  continues to honor the knob.
- Tests: two new substance tests (truth + PF); existing tests in
  `test_dynamics.py` and `test_pf_float_predict.py` that assumed
  RW behavior need small rewrites. Golden trace regeneration is a
  separate follow-up commit (not part of this change).
- Dashboard / sweep harness: no schema change. Visual validation
  recommended post-apply.
