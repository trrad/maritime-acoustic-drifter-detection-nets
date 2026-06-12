## Purpose

Float64 reference particle filter for the M1 maritime fleet. One
independent `PFFloat` per node — no shared state, no cross-node fusion.
Class-aware behavior (anchor / pure_drifter / ballast_drifter) dispatches
on `layout.class_name` inside a single class. Truth separation enforced at
three layers: module boundary (no truth-bearing import), import-linter
contract on `pyproject.toml`, and pyright-strict signatures that reject
truth types at the type-check layer. PF dynamics use climatology
reconstructed from the onboard map; LoRa TOA is consumed only when the
range partner is an anchor (drifter-to-drifter ranges defer to M2). The
CLI orchestrator (`run_pf_float.py`) is intentionally exempt from the
truth-separation contract — it is the final reporting layer and may read
truth solely to compute the per-class RMSE aggregates in `pf_summary.json`.

## Requirements


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

### Requirement: Predict Uses Climatology-Derived Current
The `predict` stage SHALL advance each particle using the onboard
map's climatology current as the expected drift velocity plus the
particle's own state velocity plus process noise. The climatology
SHALL be sourced from the `RegionalMap` instance the PF was
constructed with via the accessor
`onboard_map.current_climatology_at(lat_deg, lon_deg)`, which returns
a `(mean_vx, mean_vy, std_vx, std_vy)` tuple (the M1 climatology is
time-invariant — it is the long-term mean field at a position, not a
time-varying snapshot). The PF SHALL use `(mean_vx, mean_vy)` as the
expected drift velocity; the `(std_vx, std_vy)` components MAY inform
process-noise scaling but are not required to. The PF SHALL NOT use
the truth current field (the import-linter contract on `pf_float.py`
forbids that import), and SHALL NOT fall back to a hardcoded zero,
constant, or placeholder value in place of the climatology. Process
noise covariance SHALL be larger than the truth-side propagator's
process noise — the PF is deliberately less certain about its world
than the truth propagator is — per the values in design D4 (position
`1 m/√s`, velocity `0.05 m/s/√s`, heading `1 deg/√s`, current estimate
`0.01 m/s/√s`).

#### Scenario: Predict-mean drift tracks climatology when process noise is muted
- **WHEN** a pure-drifter `PFFloat` is constructed with an onboard map whose climatology reports `(vx=0.2, vy=0.0)` at the drifter's starting lat/lon
- **AND** `predict(dt_sec=60)` is called for 10 consecutive ticks on a particle cloud whose initial velocity is zero, with process-noise scale factors overridden to zero (via a test-only `PFFloatConfig` override or an equivalent no-op RNG hook)
- **THEN** the particle-mean east-component position has advanced by approximately `0.2 * 10 * 60 = 120 m` relative to tick 0, within numerical-integration tolerance
- **AND** the particle-mean north-component position has advanced by approximately 0 m within the same tolerance

#### Scenario: Predict call path never references the truth current field
- **WHEN** the source of `PFFloat.predict` (and any helper it calls within `pf_float.py`) is inspected via AST walk
- **THEN** no call site references `rtl.vectors.maritime.current_fields.CurrentField`, the `.velocity_at(...)` method on a truth-field type, or any other symbol from the truth current-field module
- **AND** `uv run lint-imports` already forbids the module-level import (redundant with this scenario; this scenario additionally guards against a local/deferred import that lint-imports wouldn't catch by itself)

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

### Requirement: Per-Node Independence
Each `PFFloat` instance SHALL operate independently — no `PFFloat`
method SHALL read from or write to any other `PFFloat` instance's
state. The module SHALL NOT instantiate a fleet-level aggregator in
M1.

#### Scenario: Multiple PF instances are independent
- **WHEN** two `PFFloat` instances are constructed from the same initial state and stepped with identical observations and RNGs
- **THEN** their particle arrays remain element-wise equal across ticks
- **AND** modifying one instance's particles does not affect the other

### Requirement: Truth Separation via Module Boundaries and Import Linting
The `pf_float.py` library module SHALL NOT import any truth-bearing
module. The forbidden imports SHALL be enforced by an `import-linter`
contract (configured in `pyproject.toml` per
`project-infra-import-linter`) naming:

- `rtl.vectors.maritime.pf_float` as the sole `source_module`;
- `rtl.vectors.maritime.scenario_truth_schema` (the truth-schema
  module introduced by `maritime-scenario-gen`) and
  `rtl.vectors.maritime.current_fields` (truth current field) as
  `forbidden_modules`.

`run_pf_float.py` — the CLI orchestrator and final reporting layer —
SHALL NOT appear in the contract's `source_modules`; it is permitted
to import `ScenarioTruthReader` for the sole purpose of computing the
truth-dependent portions of `pf_summary.json` (see "PF Summary
Measurement Report" below). The operational invariant we enforce is
that the node-level algorithm simulated by `PFFloat` never sees truth;
a workstation-side orchestrator that runs the algorithm and reports
its measurement against truth afterwards is on the allowed side of
that line.

Running `uv run lint-imports` SHALL detect any attempt to import a
forbidden module from `pf_float.py` and exit nonzero. Function
signatures in `pf_float.py` SHALL accept only observation types
(`ObservationRecord`, `ObservationTickView`, `ScenarioReader`) —
pyright strict SHALL flag any attempt to pass truth types as a type
error at authoring time, so truth cannot flow from `run_pf_float.py`
into any `PFFloat` method even though `run_pf_float.py` is allowed to
read truth for its own reporting use.

#### Scenario: import-linter contract forbids scenario_truth_schema in pf_float.py
- **WHEN** the `pyproject.toml` import-linter configuration is inspected after this change lands
- **THEN** a contract named "PF library does not access truth" (or equivalent) lists `rtl.vectors.maritime.pf_float` as the sole entry in `source_modules`
- **AND** `rtl.vectors.maritime.scenario_truth_schema` is in that contract's `forbidden_modules`
- **AND** `rtl.vectors.maritime.current_fields` is in that contract's `forbidden_modules`
- **AND** `rtl.vectors.maritime.run_pf_float` is NOT in the contract's `source_modules` (the reporting CLI is intentionally exempt)

#### Scenario: Introducing a forbidden import into the library triggers contract failure
- **WHEN** a developer adds `from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader` to `pf_float.py` and runs `uv run lint-imports`
- **THEN** the command exits nonzero
- **AND** the error message names the violated contract

#### Scenario: PFFloat function signatures reject truth types at type-check time
- **WHEN** pyright strict is run against `pf_float.py`
- **THEN** no function signature on `PFFloat` accepts `ScenarioTruthReader`, `TruthTickView`, or `CurrentField`
- **AND** any file (including `run_pf_float.py`) attempting to call `pf.step(truth_view)` fails type-check — truth data cannot flow into `PFFloat` even from modules allowed to read it

#### Scenario: run_pf_float may read truth for summary without passing it to PFFloat
- **WHEN** `run_pf_float.py` imports `ScenarioTruthReader` (for example, to compute per-class RMSE aggregates in `pf_summary.json`)
- **THEN** `uv run lint-imports` exits zero — `run_pf_float.py` is not in the `source_modules` of the PF-library-truth-separation contract
- **AND** `pf.step(...)`, `pf.weight(...)`, `pf.predict(...)`, and `pf.estimate(...)` call sites in `run_pf_float.py` pass only observation-derived arguments (enforced by `PFFloat`'s type signatures; pyright strict flags any truth-typed argument as a type error)

### Requirement: Onboard Map From Scenario Reader
Each `PFFloat` SHALL receive a `RegionalMap` instance obtained from `ScenarioReader(path).onboard_map()` — the sidecar-backed accessor defined by `maritime-scenario-gen`. The PF SHALL NOT call `make_onboard_map` and SHALL NOT access the truth map in any form. Any observation likelihood that depends on the map (bathy probe, land exclusion) SHALL query the onboard map obtained from the reader. The import-linter contract delivered by `project-infra-import-linter` SHALL forbid `rtl.vectors.maritime.pf_*` and `rtl.vectors.maritime.run_pf_*` from importing `make_onboard_map` (or `rtl.vectors.maritime.map_payload` symbols that require truth map inputs).

#### Scenario: PF is constructed with the reader's onboard map
- **WHEN** a `PFFloat` is constructed with `onboard_map=ScenarioReader(path).onboard_map()`
- **THEN** the PF's bathymetry queries use the onboard map's values (not truth bathymetry)

#### Scenario: PF does not import make_onboard_map
- **WHEN** `uv run lint-imports` is executed against a `pf_float.py` that contains `from rtl.vectors.maritime.map_payload import make_onboard_map`
- **THEN** the command exits nonzero naming the violated contract

### Requirement: Observation Likelihood per Sensor
`PFFloat` SHALL apply a Gaussian likelihood centered on the
observation's measurement value with σ equal to the observation's
`noise_sigma` field for every observation record processed in the
weight step. The likelihood SHALL use the appropriate observation
function per sensor:

- `gps`: identity on (lat, lon)
- `imu`: identity on the 6-tuple (accel_xyz, gyro_xyz) per particle
  predicted reading
- `baro`: hydrostatic inversion to depth, Gaussian on pressure
- `mag`: Gaussian on heading with wrap-aware angular distance
- `bathy_probe`: Gaussian on `onboard_map.depth_at(particle.lat, particle.lon)`;
  particles whose position is on land receive zero weight
- `lora_toa`: Gaussian on range to the partner's position when the
  partner is an anchor (see the "LoRa TOA anchor-only filter"
  requirement)

The PF SHALL recognize exactly these six sensor names. See the
"Unknown Sensor Name Is an Explicit Error" requirement for the
failure mode when a different sensor name appears.

#### Scenario: GPS observation narrows position posterior
- **WHEN** a `PFFloat` is stepped on an anchor node with a GPS observation near truth, σ=1.5 m
- **THEN** after resampling, the particle mean position is finite and drawn toward the observation

#### Scenario: Bathy likelihood zeroes particles on land
- **WHEN** a subset of particles are at positions where `onboard_map.is_on_land == True`
- **AND** a bathy_probe observation is processed
- **THEN** those particles have weight 0 after the weight step

#### Scenario: Magnetometer wraps distance at 0/360
- **WHEN** a mag observation of 358 deg is processed and a particle has heading 2 deg
- **THEN** the likelihood treats the angular distance as 4 deg (not 356 deg)

#### Scenario: Baro observation narrows depth posterior via hydrostatic inversion
- **WHEN** a ballast-drifter `PFFloat` is stepped with a baro observation whose pressure corresponds to depth `d_obs` via the hydrostatic relation `pressure_pa = 101_325 + 10_000 * d_obs` (consistent with `maritime-sensors` `BaroSensor`)
- **AND** the particle cloud's initial depth mean is several sigma away from `d_obs`
- **THEN** after the weight + resample stages, the particle-mean depth is drawn toward `d_obs` within the observation's noise band
- **AND** particles whose predicted pressure differs from the observation by more than several sigma receive relatively lower weight than particles near `d_obs`

#### Scenario: IMU observation narrows bias posterior
- **WHEN** a `PFFloat` is stepped with an IMU observation whose accel channel reports `(v - v_prev) / dt + bias_truth + noise` and whose gyro_z channel reports `heading_rate_truth + bias_truth + noise` per `maritime-sensors` `IMUSensor`
- **AND** the PF's particle cloud has a wide initial accel-bias and gyro-z-bias prior (several sigma wide)
- **THEN** after the weight + resample stages, the particle-mean accel-bias slot is drawn toward `bias_truth` within the observation's noise band
- **AND** the particle-mean gyro-z-bias slot is drawn toward `bias_truth` within the observation's noise band
- **AND** effective sample size remains strictly positive (the gyro_x / gyro_y channels, which the M1 dynamics model does not drive, do not collapse the posterior)

### Requirement: LoRa TOA Anchor-Only Filter
The M1 `lora_toa` likelihood handler SHALL be a deliberate anchor-only
filter: when the observation's partner `node_id` appears as a key in
`header.anchor_positions` (the scenario header's non-truth
anchor-survey mapping), the handler SHALL apply a Gaussian range
likelihood using the (lat, lon) position from that mapping; when the
partner is not in `header.anchor_positions`, the handler SHALL return
no likelihood contribution (i.e., no weight update for that
observation). This is a documented M1 filter path, not an error — M2
fleet coordination will lift it. The filter SHALL NOT be framed as a
"drop" or require a drop counter; the handler simply has two branches
by design. `PFFloat.__init__` SHALL accept the anchor-positions
mapping as a constructor argument (sourced by the CLI from
`ScenarioReader(path).header().anchor_positions`); the PF SHALL NOT
access truth state or the truth reader for this purpose.

#### Scenario: LoRa to anchor applies range likelihood
- **WHEN** a drifter's `PFFloat` processes a `lora_toa` observation whose partner_id is an anchor (present in `anchor_positions` sourced from `header.anchor_positions`)
- **THEN** the particle weights are updated via Gaussian range likelihood using the anchor's (lat, lon) from the header

#### Scenario: LoRa to non-anchor partner does not update weights
- **WHEN** a drifter's `PFFloat` processes a `lora_toa` observation whose partner_id is not an anchor
- **THEN** the particle weights are unchanged from before that observation
- **AND** the handler does not raise, does not log a drop, and does not warn — the filter is the M1-specified path

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

### Requirement: Unknown Sensor Name Is an Explicit Error
`PFFloat.weight` SHALL raise `ValueError` on any observation whose
`sensor` field is not one of the six recognized M1 sensor types
(`"gps"`, `"imu"`, `"baro"`, `"mag"`, `"bathy_probe"`, `"lora_toa"`).
An unknown sensor name is a pipeline bug (the scenario schema and
the PF's handler set disagree) and MUST fail loudly. The PF SHALL
NOT silently drop unknown sensors, SHALL NOT log a count, and SHALL
NOT maintain a "drop counter."

#### Scenario: Unknown sensor raises ValueError
- **WHEN** `pf.weight([ObservationRecord(sensor="sonar", ...)])` is called (a sensor name not in the recognized set)
- **THEN** `ValueError` is raised
- **AND** the error message names the offending sensor name

#### Scenario: Recognized sensor names proceed normally
- **WHEN** observations with every one of the six recognized sensor names are processed
- **THEN** no `ValueError` is raised by dispatch

### Requirement: Systematic Resampling Every Tick
`PFFloat.resample()` SHALL perform systematic resampling: cumulative
weight array, generate a single uniform starting offset, and select
particles at evenly-spaced positions. `PFFloat.step()` SHALL call
`resample()` unconditionally every tick in M1.

#### Scenario: Resampling reduces weight variance
- **WHEN** a weight step produces unevenly-weighted particles and `resample` is called
- **THEN** the post-resample weights are all `1 / n_particles`

#### Scenario: Resampling preserves particle count
- **WHEN** resample is called
- **THEN** the number of particles after resample equals the number before

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

### Requirement: Vectorized Over Particles
All four pipeline stages SHALL operate on the `(n_particles, state_dim)`
particle array via numpy vectorization. No Python-level `for` loops
over particles SHALL be present in the predict, weight, resample, or
estimate implementations.

#### Scenario: No per-particle Python loop
- **WHEN** the pipeline stages are executed with `n_particles = 500` and `state_dim = 25`
- **THEN** no stage's implementation uses a Python-level `for i in range(n_particles)` loop (verified by targeted test scanning the source for that exact pattern in the four stage functions)

### Requirement: Main Estimate Stream for Every Node
The main estimate stream SHALL contain one `PFEstimateRecord`
(mean + cov_diag + n_effective) per `(tick, node_id)` pair for every
node in the scenario fleet. There SHALL be no privileged-subset
mechanism (no `focus_node_ids`). The stream SHALL conform to
`maritime-pf-estimate-schema` v1.0.

#### Scenario: Main stream covers every node every tick
- **WHEN** a PF run completes over a 900-tick scenario with a 10-node fleet
- **THEN** the main estimate stream contains 9000 `PFEstimateRecord` records
- **AND** every node_id appears exactly 900 times

#### Scenario: Main stream has no particles field
- **WHEN** any `PFEstimateRecord` in the main stream is inspected
- **THEN** it has no `particles` attribute
- **AND** it has no `weights` attribute

### Requirement: Particle Sidecar Emission with Thinning
The PF SHALL write a separate particle sidecar stream conforming to
`maritime-pf-estimate-schema`'s particle sidecar format when the CLI
receives a `--particles-out <path>` argument (and `--no-particles` is
NOT set), subject to three orthogonal thinning knobs:

- `--thin-ticks N` (default 1): only write records for ticks where
  `tick % N == 0`.
- `--thin-particles K` (default 50): for each emitted record, subsample
  `K` particles uniformly at random (without replacement) from the
  `n_particles` particle array, along with their normalized weights.
- `--thin-nodes IDS` (default all): restrict sidecar records to the
  comma-separated subset of node_ids.

The thinning knobs SHALL compose with AND — a record is written iff
the tick passes the tick filter AND the node passes the node filter.
If `--no-particles` is set, no sidecar SHALL be written regardless of
other thinning flags. If `--particles-out` is omitted and
`--no-particles` is not set, the CLI SHALL use a default sidecar path
derived from the main output path.

The sidecar header SHALL record the thinning config used
(`thin_ticks`, `thin_particles`, `thin_nodes`), so downstream readers
can interpret the records correctly.

#### Scenario: Default thinning writes every tick for every node with 50 particles each
- **WHEN** `run_pf_float.py --scenario s.jsonl --out e.jsonl --particles-out p.jsonl` is invoked on a 10-node 900-tick scenario with the default thinning
- **THEN** the sidecar contains 9000 particle records
- **AND** each record has `len(particles) == 50`

#### Scenario: --thin-ticks 10 reduces tick cadence
- **WHEN** the CLI is invoked with `--thin-ticks 10`
- **THEN** the sidecar contains records only for ticks where `tick % 10 == 0`
- **AND** the sidecar header records `thin_ticks = 10`

#### Scenario: --thin-nodes restricts node subset
- **WHEN** the CLI is invoked with `--thin-nodes n01,n05`
- **THEN** every particle record in the sidecar has `node_id in {"n01", "n05"}`
- **AND** the sidecar header records `thin_nodes = ("n01", "n05")`

#### Scenario: --no-particles disables sidecar
- **WHEN** the CLI is invoked with `--no-particles`
- **THEN** no sidecar file is written
- **AND** the main estimate stream is still produced normally

#### Scenario: Thinning filters AND together
- **WHEN** the CLI is invoked with `--thin-ticks 5 --thin-nodes n01`
- **THEN** records appear only for `(tick % 5 == 0) AND (node_id == "n01")`

### Requirement: PF Summary Measurement Report
Alongside the main estimate stream, `run_pf_float.py` SHALL emit a
`pf_summary.json` file containing per-class RMSE aggregates (median,
mean, p95) over the final 25% of the run window, per-node ESS
trajectory stats (mean, min, max over the full run), and a boolean
`completed` flag. RMSE is computed by `run_pf_float.py` using
`ScenarioTruthReader` to read per-tick truth state and the main
estimate stream for per-tick PF posterior means; `PFFloat` itself
never sees truth (see "Truth Separation via Module Boundaries and
Import Linting" — `run_pf_float.py` is the final reporting layer and
is intentionally exempt from the PF-library-truth-separation
contract). The summary is a measurement report for human inspection
and downstream analysis — it is NOT a spec assertion target. Binding
thresholds get established later, with grounding (measurement or
operational requirement).

#### Scenario: Summary file is written alongside main stream
- **WHEN** the CLI completes a run with output `/tmp/estimate.jsonl`
- **THEN** `/tmp/pf_summary.json` exists (or at `--summary-out` path if passed)
- **AND** the file is valid JSON
- **AND** it contains keys for per-class RMSE aggregates, ESS stats, and `completed: true`

#### Scenario: Summary contains per-class RMSE aggregates
- **WHEN** the summary is parsed
- **THEN** it contains RMSE aggregates keyed by class_name (`"anchor"`, `"ballast_drifter"`, `"pure_drifter"`)
- **AND** each class entry includes `median`, `mean`, and `p95` numerical values (finite)

#### Scenario: Reported RMSE actually consumes truth
- **WHEN** a PF run completes against a scenario whose `ScenarioTruthReader` returns real non-zero truth state
- **AND** the summary's anchor-class RMSE is computed and recorded
- **AND** the same summary-computation path is re-invoked with a substitute truth reader that returns all-zero truth (for every tick, every node's truth state is the zero vector — e.g., via a test hook or an injected reader)
- **THEN** the two resulting anchor-class RMSE values differ (the summary computation actually depends on the truth readings, ruling out a stub `rmse = 0.0` implementation)

#### Scenario: Summary does not assert thresholds
- **WHEN** the test suite runs against the summary
- **THEN** no test asserts a specific RMSE threshold (values are reported, not compared to a numeric bound)

### Requirement: Sanity Invariants on PF Output
The PF output SHALL satisfy the following sanity invariants (spec-level,
enforced by tests; these catch real bugs, unlike arbitrary RMSE
thresholds):

- Every `PFEstimateRecord.mean` entry SHALL be finite (no NaN, no inf).
- Every `PFEstimateRecord.cov_diag` entry SHALL be non-negative and finite.
- Every `PFEstimateRecord.n_effective` SHALL be strictly greater than zero.
- The PF SHALL complete all ticks (no early exit, no unhandled
  exception) on a valid scenario.

#### Scenario: No NaN in mean
- **WHEN** a 900-tick run completes
- **THEN** every estimate record's `mean` entries are finite

#### Scenario: cov_diag is non-negative
- **WHEN** a run completes
- **THEN** every estimate record's `cov_diag` entries are ≥ 0

#### Scenario: ESS never zero
- **WHEN** a run completes
- **THEN** every estimate record's `n_effective > 0`

#### Scenario: Run completes
- **WHEN** the CLI is invoked on a valid scenario with defaults
- **THEN** the exit code is 0
- **AND** the `completed` flag in `pf_summary.json` is `true`

### Requirement: CLI Invocation
The system SHALL provide a CLI at `rtl/vectors/maritime/run_pf_float.py`
accepting:

- `--scenario <path>` (required): input scenario JSONL.
- `--out <path>` (required): main estimate stream output path.
- `--particles-out <path>` (optional): sidecar output path. If
  omitted and `--no-particles` is not set, defaults to a path derived
  from `--out`.
- `--no-particles`: disables the sidecar entirely.
- `--thin-ticks N` (default 1): tick-thinning for the sidecar.
- `--thin-particles K` (default 50): particle-subsample count for
  each sidecar record.
- `--thin-nodes IDS` (optional): comma-separated subset of node_ids
  for the sidecar (default: all nodes).
- `--n-particles N` (default 500): underlying PF particle count.
- `--predict-noise-pos <float>` (optional): override the PF's
  predict-step position process noise (m / √s). Default:
  `PFFloatConfig.process_noise_pos_m_per_sqrt_s`.
- `--predict-noise-vel <float>` (optional): override the PF's
  predict-step velocity process noise (m/s / √s). Default:
  `PFFloatConfig.process_noise_vel_ms_per_sqrt_s`. Setting 0.0
  disables the velocity random-walk entirely (supports the
  "is velocity a free RW?" hypothesis test that motivated Stage 3's
  climatology-slaved velocity rework).
- `--predict-noise-heading <float>` (optional): override
  predict-step heading process noise (deg / √s). Default:
  `PFFloatConfig.process_noise_heading_deg_per_sqrt_s`.
- `--predict-noise-current <float>` (optional): override predict-step
  current-state process noise (m/s / √s). Default:
  `PFFloatConfig.process_noise_current_ms_per_sqrt_s`.
- `--summary-out <path>` (optional): summary JSON output path.
  Defaults to a path derived from `--out`.

The CLI SHALL open the scenario via `ScenarioReader` for the PF's
observation intake (the `ObservationRecord` stream that feeds
`PFFloat.weight`). The CLI MAY additionally open
`ScenarioTruthReader` on the same scenario file strictly for the
truth-dependent portions of the summary report (per-class RMSE
aggregates in `pf_summary.json`); truth data SHALL NOT flow into
any `PFFloat` method call. The CLI SHALL NOT expose a
`--focus-nodes` flag — thinning replaces it. When a
`--predict-noise-*` flag is not provided (value is `None` after
argparse), the CLI SHALL use the corresponding
`PFFloatConfig.process_noise_*` default; when provided, the CLI
SHALL plumb the supplied float into the `PFFloatConfig` used by
every `PFFloat` instance for every node in the fleet (uniform
override, not per-class).

#### Scenario: CLI produces valid main estimate file
- **WHEN** the CLI is run against a valid scenario with `--scenario`, `--out`, and default flags
- **THEN** the command exits 0
- **AND** the main output file is a valid `maritime-pf-estimate-schema` v1.0 stream

#### Scenario: CLI rejects scenario with unsupported schema version
- **WHEN** the CLI is run against a scenario whose header declares an unsupported `schema_version`
- **THEN** the CLI exits nonzero
- **AND** stderr names the version mismatch

#### Scenario: Legacy --focus-nodes flag is not accepted
- **WHEN** the CLI is invoked with the legacy flag `--focus-nodes n01,n05` (the privileged-subset flag from earlier drafts)
- **THEN** the CLI exits nonzero with an "unrecognized argument" error naming `--focus-nodes`
- **AND** per-node particle filtering is available via `--thin-nodes` (see the "Particle Sidecar Emission with Thinning" requirement) — the flag name changed because the semantics changed from a privileged-subset to a thinning knob

#### Scenario: CLI with --no-particles skips sidecar
- **WHEN** the CLI is invoked with `--no-particles`
- **THEN** only the main estimate file and `pf_summary.json` are written
- **AND** no particle sidecar path is produced

#### Scenario: --predict-noise-vel 0.0 disables the velocity random walk
- **WHEN** the CLI is run against a valid scenario with `--predict-noise-vel 0.0` and all other predict-noise flags omitted
- **THEN** every PF instance's `PFFloatConfig.process_noise_vel_ms_per_sqrt_s == 0.0`
- **AND** in a scenario where no observations update weights (e.g., observation allow-list empty), particle velocity values are constant across predict ticks within float tolerance (no stochastic evolution of velocity dimension)

#### Scenario: --predict-noise-pos, --predict-noise-heading, --predict-noise-current plumb through to PFFloatConfig
- **WHEN** the CLI is run with `--predict-noise-pos 0.1 --predict-noise-heading 0.5 --predict-noise-current 0.02`
- **THEN** every PF instance's `PFFloatConfig.process_noise_pos_m_per_sqrt_s == 0.1`
- **AND** `process_noise_heading_deg_per_sqrt_s == 0.5`
- **AND** `process_noise_current_ms_per_sqrt_s == 0.02`
