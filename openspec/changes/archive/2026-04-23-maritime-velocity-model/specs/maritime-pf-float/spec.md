## ADDED Requirements

### Requirement: Particle Velocity Is Per-Tick Sampled From Climatology Variance
`PFFloat.predict` SHALL re-sample each particle's horizontal velocity
components (`vx, vy`) every tick from a zero-mean Gaussian whose σ is
derived from the onboard-map climatology at the particle's own
`(lat, lon)`, rather than adding a random-walk increment to the
previous tick's velocity. The per-particle σ SHALL equal
`sqrt(climatology.var_vx_ms2(lat, lon)) + floor` for the x component
and `sqrt(climatology.var_vy_ms2(lat, lon)) + floor` for the y
component, where `floor` is the `PFFloatConfig.process_noise_vel_ms_per_sqrt_s`
field (default 0.02 m/s under the new semantics).

The velocity state slot (`vx, vy`) continues to represent the
residual above the climatology current mean; the position update
formula `east += (vx + cur_vx) * dt + pos_noise[:, 0]` (and its
`north` counterpart) is unchanged. What changes is the residual
evolution: each tick's residual is independent of last tick's,
bounded by the climatology-variance-plus-floor σ.

The RNG-stream-order contract is preserved: the `vel_noise` draw at
the top of `predict` is still consumed (for RNG-stream-order
determinism) but its values are not used for the vx/vy update in the
new code path.

#### Scenario: Particle vx sampling σ tracks climatology var plus floor
- **WHEN** a pure-drifter `PFFloat` is constructed against an onboard map whose climatology reports `(mean_vx=0.1, mean_vy=0.05, var_vx=0.04, var_vy=0.01)` at the node's position, `PFFloatConfig.process_noise_vel_ms_per_sqrt_s=0.02`, and `predict(dt_sec=60)` is called once
- **THEN** the sample stddev of `particles[:, idx.vx]` across the particle cloud is in the range `[sqrt(0.04) + 0.02 - margin, sqrt(0.04) + 0.02 + margin]` = `[0.22 - margin, 0.22 + margin]` (finite-sample margin acceptable for the test's particle count)
- **AND** the sample mean of `particles[:, idx.vx]` is within 3σ/sqrt(n_particles) of 0.0 (zero-mean residual)

#### Scenario: Predict with zero-variance climatology still yields non-degenerate velocity cloud
- **WHEN** a pure-drifter `PFFloat` is constructed against a climatology reporting `var_vx=0, var_vy=0` everywhere, `process_noise_vel_ms_per_sqrt_s=0.02`, and `predict(dt_sec=60)` is called once
- **THEN** `particles[:, idx.vx]` is not a single repeated value (the floor prevents total collapse)
- **AND** the sample stddev of `particles[:, idx.vx]` is within `[0.5 * 0.02, 1.5 * 0.02]` (floor-dominated)

#### Scenario: Over 100 predict ticks, particle velocity residual stays bounded
- **WHEN** a pure-drifter `PFFloat` with default config is stepped with `predict(dt_sec=60)` for 100 consecutive ticks against a climatology reporting `(mean_vx=0.1, var_vx=0.01)`
- **THEN** the per-particle `|vx - 0|` maximum across all 100 ticks is below `5 * (sqrt(0.01) + 0.02) ≈ 0.6 m/s`
- **AND** the particle-mean `vx` across the cloud at each tick is within `3 * (sqrt(0.01) + 0.02) / sqrt(n_particles)` of 0.0 — the residual does NOT accumulate over time

#### Scenario: PF particle-mean position tracks truth under non-trivial climatology with zero observations
- **WHEN** a pure-drifter truth node is advected by the truth current field at `(0.2, 0.0) m/s` for 10 minutes (10 ticks at dt=60), and a `PFFloat` with matching onboard-map climatology `(mean_vx=0.2, mean_vy=0, var_vx=0.01, var_vy=0.01)` is stepped with `predict(dt_sec=60)` for 10 ticks without any `weight(...)` calls
- **THEN** the particle-mean east position at tick 10 is within `tick_count * dt * (sqrt(var_vx) + floor) = 10 * 60 * 0.12 ≈ 72 m` of the truth east position — a climatology-std-bounded envelope, not a runaway RW
