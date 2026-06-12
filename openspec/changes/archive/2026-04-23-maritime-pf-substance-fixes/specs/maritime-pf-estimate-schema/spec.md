## MODIFIED Requirements

### Requirement: PF Estimate Record Structure
Each estimate record SHALL contain `record_type="estimate"`, `t`,
`t_sec`, `node_id`, `mean` (list of floats of length matching the
node's layout state_dim), `cov_diag` (list of floats, same length,
non-negative entries), and `n_effective` (float strictly greater
than zero and less than or equal to `n_particles`). The
`n_effective` value SHALL be the **pre-resample** effective sample
size — the ESS computed from the normalized importance weights
after `weight` and before `resample` — so that downstream consumers
(dashboard, sweep harness, cadence reports) can detect
observation-informativeness and particle degeneracy. Post-resample
ESS (trivially equal to `n_particles` for systematic resampling)
SHALL NOT be written to this field. Estimate records SHALL NOT carry
`particles` or `weights` fields — particle-level data lives in the
separate sidecar stream.

#### Scenario: Estimate record has no particles or weights
- **WHEN** a valid estimate record is parsed
- **THEN** the record has no `particles` attribute
- **AND** the record has no `weights` attribute

#### Scenario: cov_diag is non-negative
- **WHEN** an estimate record with any negative `cov_diag` entry is parsed
- **THEN** `ValueError` is raised

#### Scenario: n_effective is strictly positive and bounded
- **WHEN** an estimate record with `n_effective <= 0` or `n_effective > n_particles` is parsed
- **THEN** `ValueError` is raised

#### Scenario: n_effective reflects pre-resample degeneracy on tight-noise observations
- **WHEN** a valid estimate stream is generated from a PF run where at least one tick weighted particles against a tight-noise observation whose residual was several sigma (pre-resample weight concentration)
- **THEN** that tick's record has `n_effective < n_particles`
- **AND** (consumer-contract guard) a reader inspecting `n_effective` across all ticks sees a value that varies tick-to-tick with observation informativeness, not a constant equal to `n_particles`

#### Scenario: mean and cov_diag lengths match
- **WHEN** an estimate record is parsed with `mean` length 15 and `cov_diag` length 14
- **THEN** `ValueError` is raised (shape mismatch)
