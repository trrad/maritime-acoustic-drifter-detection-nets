## MODIFIED Requirements

### Requirement: Bootstrap Particle Filter Pipeline
The system SHALL provide a `PFFloat` class implementing a bootstrap
(sampling-importance-resampling) particle filter with four stages:
`predict` (propagate particles through a dynamics model plus process
noise), `weight` (compute importance weights from observation
likelihoods), `resample` (systematic resampling), and `estimate`
(compute weighted mean + covariance diagonal + effective sample size).
A `step(dt_sec, observations, t, t_sec)` convenience method SHALL
perform all four stages in order, with `estimate` evaluated BEFORE
`resample` so that the returned `PFEstimateRecord.n_effective` carries
the pre-resample effective sample size (the diagnostically useful
value — post-resample ESS is trivially equal to `n_particles` for
systematic resampling and therefore carries no observation-informative
signal).

#### Scenario: step performs predict → weight → estimate → resample
- **WHEN** `pf.step(dt_sec=1.0, observations=[...], t=5, t_sec=5.0)` is called
- **THEN** particles are advanced, weights computed, the estimate record assembled (so `n_effective` reflects the weighted distribution), and then particles resampled
- **AND** the returned record has `t=5` and `t_sec=5.0`

#### Scenario: Effective sample size is strictly positive and bounded
- **WHEN** `pf.step(...)` is called and returns an estimate record
- **THEN** `0 < record.n_effective <= pf.n_particles`

#### Scenario: Anchor-class predict holds position fixed
- **WHEN** an anchor-class `PFFloat` is constructed with initial particles all at the anchor's surveyed lat/lon and is stepped with `predict(dt_sec=60)` for 10 consecutive ticks, with no observations and process noise overridden to zero
- **THEN** every particle's position slice at tick 10 equals the initial position within float tolerance (the moored class has no advection; `predict` is a no-op on position)
- **AND** the ESS remains equal to `n_particles` (uniform-weight cloud, no observations)

#### Scenario: Pure-drifter predict holds depth at the surface
- **WHEN** a pure-drifter `PFFloat` is constructed with initial particle depths all equal to 0 m and is stepped with `predict(dt_sec=60)` for 10 consecutive ticks against a non-trivial climatology current, with process noise overridden to zero
- **THEN** every particle's depth slice at tick 10 equals 0 m within float tolerance (the pure-drifter class is surface-only; `predict` does not introduce vertical motion even in the presence of a horizontal climatology flow)

## ADDED Requirements

### Requirement: LoRa TOA Range Likelihood Matches 2D Truth Sensor
The LoRa TOA observation-likelihood helper SHALL evaluate the range
residual as the 2D horizontal distance between the particle's
`(east, north)` position and the anchor partner's `(east, north)`
position, matching the truth-side `LoRaTOASensor.sample` forward
model. The particle's `depth` slot (and the anchor's depth, if
non-zero) SHALL NOT enter the range computation. A particle placed at
exact horizontal truth with any arbitrary non-zero `depth` SHALL score
log-likelihood == 0 against a noiseless range observation equal to
the truth-side 2D distance.

#### Scenario: Particle at exact horizontal truth with non-trivial depth scores zero log-likelihood
- **WHEN** a pure-drifter or ballast-drifter `PFFloat` has a particle placed at the horizontal truth `(east, north)` of a node, the particle's `depth` is set to a non-trivial value (e.g. 37 m), and the PF is weighted against a LoRa TOA observation whose range equals the anchor-to-node 2D horizontal distance under zero-noise LoRa sigma
- **THEN** the log-likelihood contribution for that particle equals 0 within float tolerance (the depth does NOT perturb the 2D-range residual)

### Requirement: PFEstimateRecord.n_effective Is Pre-Resample
`PFFloat.step(...)` SHALL set the returned `PFEstimateRecord.n_effective`
field to the effective sample size computed **after** `weight` and
**before** `resample`. The pre-resample ESS
varies with observation informativeness and is the degeneracy
indicator consumers (dashboard, sweep harness, cadence reports)
depend on. The post-resample ESS SHALL NOT be written to
`n_effective`.

#### Scenario: After informative observation, n_effective is below n_particles
- **WHEN** `pf.step(...)` is called with a tight-noise LoRa observation whose range residual at some particles is several sigma (so weights are concentrated)
- **THEN** the returned record has `n_effective < n_particles`
- **AND** `n_effective < 0.5 * n_particles` whenever the tight-noise likelihood collapses weights onto a small fraction of particles (pre-resample degeneracy visible as a low ESS)

### Requirement: M1 Ballast Depth Invariant in Predict
`PFFloat.predict(dt_sec)` SHALL hold every particle's `depth` state
slot constant across ticks whenever the layout `class_name` equals
`ballast_drifter`. This mirrors the truth-side M1 invariant that the
ballast pump is `pass` and the `KIND_BALLAST_DRIFTING_POSE` branch of
`propagate_truth` does not advance `state[2]`. Vertical velocity
(`vz`) and the `pos_noise[:, 2]` draw SHALL NOT be applied to the
depth state. (The `pos_noise[:, 2]` draw is still consumed from the
RNG for stream-order determinism; it simply goes unused for this
axis. See `pf_float.py` predict-stage comment.)

#### Scenario: Ballast depth is invariant across predict ticks
- **WHEN** a ballast-drifter `PFFloat` is constructed with all particle depths set to 42.0 m, non-zero particle `vz`, and non-zero process-noise scales
- **AND** `predict(dt_sec=60)` is called for 10 consecutive ticks
- **THEN** every particle's `depth` value at tick 10 equals 42.0 m within float tolerance
- **AND** (seeded-determinism guard) the total count of RNG `normal(...)` draws per tick is unchanged from the pre-fix behavior (the `pos_noise[:, 2]` slice is still drawn, just not applied)
