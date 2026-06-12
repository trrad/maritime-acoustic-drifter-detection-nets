## ADDED Requirements

### Requirement: Predict Shifts Previous-Velocity And Previous-Heading Slots
`PFFloat.predict(dt_sec)` SHALL copy each particle's current velocity
components `(vx, vy, vz)` into the corresponding previous-velocity
slots `(prev_vx, prev_vy, prev_vz)` and the current heading into
`prev_heading` BEFORE any update to the current-velocity or
current-heading slots for this tick. This mirrors the truth-side
`propagate_truth` pattern at `rtl/vectors/maritime/dynamics.py:47-48`
— truth shifts `velocity → prev_velocity` and
`heading → prev_heading` at tick entry. Without this shift, the PF's
finite-difference acceleration prediction
`(vx - prev_vx) / dt + accel_bias` in `_imu_log_likelihood`
compares the latest velocity sample against a stale initial sample
drawn at PF construction time, producing a structurally meaningless
prediction. The shift SHALL be vectorized over the particle array (no
Python-level per-particle loop) to satisfy the `Vectorized Over
Particles` requirement.

#### Scenario: Previous-velocity slots hold pre-predict velocity after predict
- **WHEN** a `PFFloat` is constructed with any non-trivial class and any initial particle cloud whose `(vx, vy, vz, heading)` slots are NOT equal to their `(prev_vx, prev_vy, prev_vz, prev_heading)` slots (seeded non-zero initial state)
- **AND** a snapshot of the particle array is taken before calling `predict(dt_sec=60.0)`
- **THEN** after the predict call, each particle's `prev_vx` equals the PRE-predict `vx`, `prev_vy` equals the PRE-predict `vy`, `prev_vz` equals the PRE-predict `vz`, and `prev_heading` equals the PRE-predict `heading`
- **AND** the assertion holds for every particle in the cloud (all-particles predicate, not aggregate)

#### Scenario: Shift mirrors the truth-side pattern across multiple ticks
- **WHEN** a pure-drifter `PFFloat` is stepped with `predict(dt_sec=60.0)` for 5 consecutive ticks, with a snapshot taken before each tick
- **THEN** at each tick N ≥ 1, every particle's `prev_vx[N]` equals that particle's `vx[N-1]` (i.e. the `vx` from the snapshot taken before tick N-1's predict ran, which is the same as the `vx` immediately after tick N-1 completed minus any later updates — in this PF's predict structure, `vx` is written last in the predict, so tick N's `prev_vx` equals tick N-1's post-predict `vx`)
- **AND** `prev_heading` tracks the same one-tick lag against `heading`
