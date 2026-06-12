## ADDED Requirements

### Requirement: Passive Drifter Velocity Is Per-Tick Sampled, Not Random-Walked
`propagate_truth` SHALL re-sample the drifter velocity residual from
a zero-mean Gaussian each tick for both `DRIFTING_SURFACE_POSE` and
`BALLAST_DRIFTING_POSE` branches, rather than adding a random-walk
increment to the previous tick's velocity. The per-tick sampling σ
SHALL equal a module-level constant `DRIFTER_VEL_PERTURBATION_MS`
(default 0.02 m/s), applied as the stddev of an independent normal
sample per tick. The legacy random-walk scale constant
`VEL_PROCESS_NOISE_MS_PER_SQRT_S` is retired.

The velocity state slot (`state[3:5]`) still carries the drifter
velocity residual above the current-field mean; the position update
formula `state[0] += (state[3] + current_vx) * dt + pos_noise[0]`
(and its `y` counterpart) is unchanged. What changes is the residual
evolution: each tick's residual is independent of last tick's,
bounded by the perturbation σ.

This matches the physical model of a passive drifter at 60 s tick
resolution: turbulent / wind / internal-wave perturbations around
the mean current are uncorrelated at that scale. Under the retired
RW model, the residual's stddev integrated to ~1 m/s over 12 h —
unphysical. Under the per-tick sampling model, the residual stays
bounded by `3 * DRIFTER_VEL_PERTURBATION_MS ≈ 0.06 m/s` on every
tick, indefinitely.

#### Scenario: Truth drifter residual is independent tick-to-tick
- **WHEN** `propagate_truth` is called 1000 times at `dt_sec=60.0` on a `DRIFTING_SURFACE_POSE` node in a constant-current field, with a seeded RNG
- **THEN** the sequence of `state[3]` values across the 1000 ticks has sample stddev in the range `[0.5 * DRIFTER_VEL_PERTURBATION_MS, 1.5 * DRIFTER_VEL_PERTURBATION_MS]`
- **AND** the lag-1 autocorrelation of the `state[3]` sequence is below 0.2 in absolute value (tick-uncorrelated within finite-sample noise)

#### Scenario: Truth drifter position advects with current under zero perturbation
- **WHEN** `propagate_truth` is called 10 times at `dt_sec=60.0` on a pure-drifter node starting at `(0, 0, 0)` with initial velocity `(0, 0, 0)`, in a constant current field returning `(0.2, 0.0) m/s`, with `DRIFTER_VEL_PERTURBATION_MS` temporarily monkey-patched to 0.0 (deterministic fixture), zero `POS_PROCESS_NOISE_M_PER_SQRT_S`
- **THEN** the final east position equals `0.2 * 10 * 60 == 120.0 m` within 0.1 m (tight tolerance, no RW contribution)
- **AND** the final north position is within 0.1 m of its starting value
- **AND** every tick's `state[3]` equals exactly `0.0` (no residual accumulation)

#### Scenario: Over a 12-hour run, the residual stays bounded
- **WHEN** `propagate_truth` is called `12 * 3600 / 60 == 720` times at `dt_sec=60.0` on a `DRIFTING_SURFACE_POSE` node in a constant-current field
- **THEN** the sequence's `max(|state[3]|)` across all 720 ticks is less than `5 * DRIFTER_VEL_PERTURBATION_MS` (≈ 0.1 m/s) — NOT the ~1 m/s that the retired RW model would have produced
