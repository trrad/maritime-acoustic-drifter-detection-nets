# Bias-inference architecture

**Status:** v1.1 implemented (commits `b0d1868` + `382ef9f`, 2026-04-25).
Step 1 architecture; Step 1.5 / Step 2 / Step 3 queued.

This document tells future-you why `bias_field.py` carries a 64×64 dense
covariance per particle per depth instead of just per-cell variances,
why `PositionRBPF` has parallel `shadow_lats`/`shadow_lons`, and why the
bias-Kalman observation is `tri_pos − x_start − prior_disp` rather than
`tri_pos − pf.lats`. It synthesises five rounds of domain-practitioner
review (oceanographer/DA, AUV/glider robotics, Bayesian/KF/PF stats,
drifter controls, applied Bayesian) plus the diagnostic results that
forced the rebuild. The verbatim reviewer outputs are archived in
`architecture_review_findings_2026-04-25.md`.

## 1. The problem the bias-Kalman is trying to solve

A passive ballast-controlled drifter station-keeps in central SoG over
72 h. The simulator's truth currents are SalishSeaCast NEMO + a
**physically-structured 5-component layered noise** (`coh + plume·tanh(z)
+ submeso_wind·exp(-z/L_z) + inertial(rotating, z) + white`, see
`noise_model_design.md` §3). The PF and controller see only clean
SalishSeaCast as their prior. The gap is what the bias-Kalman tries to
recover.

The slow components (coh, plume, submeso, inertial-amplitude) are the
**learnable** part — within a 72 h mission, τ_slow ≈ 36 h means the
slow-component instantiation is approximately stationary; with ~12 leg
observations a sufficiently-structured posterior can recover it. The
white component is unlearnable; it lives in σ_obs.

The drifter uses its bias estimate two ways:
1. **In its own predict step** (`prior + b̂[i, cell]` per particle) so
   the PF tracks reality with the current bias estimate baked in.
2. **In its controller's depth choice** (`LiveBiasKnowledge` returns
   `prior + ensemble_mean(b̂)` to the StationKeeper).

The bias state must therefore be:
- **Per-particle** (Rao-Blackwellised — each particle's hypothesised
  trajectory implies a different posterior over the bias field).
- **Spatially structured** (the truth has σ_s ≈ 5 km correlation length;
  a diagonal prior wastes most of the prior information).
- **Updated correctly from observations** (LoRa leg-end fix, eventually
  also CTD salinity-residual via Step 2's plume-offset channel).

## 2. The cannibalisation bug (and what the analytical observation fixes)

The original v1 grid bias-Kalman observation was:

```python
innov_east_m  = (tri_lon − pf.lons[i]) × EARTH_R × cos_lat
innov_north_m = (tri_lat − pf.lats[i]) × EARTH_R
bias.kalman_update_leg(innov_east_m, innov_north_m, σ_obs)
```

In the **no-CTD case** (legacy `grid` config), each particle dead-reckons
through the leg with `prior + b̂[i, cell] + process_noise`. `pf.lats[i]`
at leg-end reflects this dead-reckoning. The innovation `tri − pf.lats`
is large for particles whose dead-reckoning landed far from truth (most
of them) — the Kalman attributes the gap to the bias field and updates
b̂ accordingly. **Correct.**

In the **CTD-on case** (`grid+ctd`), every 10-min submerged tick the CTD
likelihood reweights particles by (T,S) consistency with truth. Particles
at positions far from truth get exponentially down-weighted; on resample,
they're killed and replaced with copies of the survivors. **After ~36
CTD ticks across a 6 h leg, the surviving PF cluster has been
concentrated near truth — not by being moved, but by being selected.**
The particles whose dead-reckoning happened to land near truth survived;
the rest are gone.

When the bias-Kalman fires at leg-end and computes `tri_pos − pf.lats[i]`,
**`pf.lats[i]` is near `tri_pos` by construction** because CTD selected it
that way. The innovation is small not because the forecast error is
small, but because the survivor selection cherry-picked particles that
don't see a residual. The Kalman treats small innovation as "small bias
to learn," updates b̂ minimally, and the controller's knowledge stays
close to the (uncorrected) prior. Better information about position →
worse information about bias.

This is the **cannibalisation bug**. It surfaced in single-station smoke
(2026-04-25):

| config | mean dist | %<500m | PFerr | bias \|b̂\|_max |
|---|---|---|---|---|
| no_learn | 1780 m | 9% | 258 m | — |
| grid | 2196 m | 9% | 255 m | 8.9 cm/s |
| grid+ctd | 2363 m | 11% | **124 m** (-51%) | **5.0 cm/s** (-44%) |

`grid+ctd` PFerr drops 51% (CTD tightens position posterior) but `|b̂|_max`
drops 44% (cannibalisation starves the Kalman) and station-keeping
regresses.

**The Step 1 fix** is the **analytical observation**:

```
y_obs[i] = tri_pos − x_start[i] − prior_disp[i]
```

where:
- `x_start[i]` is particle i's position at leg-start (snapshotted from
  the SHADOW trajectory, which equals the real PF after the previous
  LoRa fix).
- `prior_disp[i]` is the time-integral of `prior_velocity(shadow_pos, t)
  × dt` along particle i's SHADOW trajectory through the leg.

This decomposes as:
```
y_obs[i] = (tri_pos − x_start[i]) − prior_disp[i]
         ≈ Σ_cells dwell_shadow[i, cell] × true_bias[cell] + noise
```

`x_start` and `prior_disp` come from the shadow trajectory which advects
with prior + process noise only (no `b̂`, no CTD/LoRa reweight). They are
therefore **independent of CTD's selection of particles** — the observation
sees the bias-induced displacement directly, regardless of what CTD did
to the position posterior.

CTD still helps the bias estimate, just indirectly:
1. CTD-tightened position posterior gives the controller a more accurate
   "perceived position" (the PF mean is what `LiveBiasKnowledge` uses to
   index into the bias field).
2. Shadow particle survival via resamples is correlated with CTD
   selection (gathered together) — but the shadow's *positions* aren't
   directly pulled by CTD, only its identity-survival is.
3. Better dwell-weighting in the H matrix when shadow trajectories that
   match truth are preferentially gathered.

## 3. Why GMRF spatial prior, why Matérn ν=0.5, why L_c = 5 km

The diagonal-prior failure mode (Dee 2005, "Bias and data assimilation,"
QJRMS 131): a single leg's residual is a linear functional of the bias
state weighted by `dwell`. With diagonal prior covariance, the Kalman
gain dumps the entire residual into the few cells with non-zero dwell
(∼2–4 cells per leg per depth). Other cells stay at prior. After 12
legs, only ~6% of 320 cells receive any update, and those cells overshoot
truth by 1.5–3× because they absorb the full leg residual rather than
sharing it across the spatially-correlated neighbourhood. Diagnostic
confirmed (`_diag_bias_vs_truth.py`, 2026-04-25):

```
depth   submeso correlation r_u   learned RMS_u (cm/s)   truth RMS_u
 5.0      +0.40                   0.34                   7.88   ← undershoot at z=5 (truth dominant)
10.0      +0.96                   7.15                   5.64   ← matches truth at z=10
20.0      +0.41                   1.23                   2.43   ← undershoot
50.0      −0.18                   3.19                   1.11   ← 3x overshoot
```

The structure tracks (z=10 r=0.96 with submeso, z=50 shifts to coh) but
magnitudes are noisy. Smoothing the posterior was tested; at any kernel
size that meaningfully regularises the overshoot, marginal smoothing
collapses the signal toward zero (it averages neighbouring cells whose
updates have different signs). Sollich & Williams 2004: the
GP-as-kernel-smoothing equivalence is asymptotic in N; doesn't apply at
N=12.

The principled fix is **putting the spatial covariance in the prior**,
not smoothing the posterior afterward. Lindgren-Rue-Lindström 2011 SPDE:
a Matérn-ν GP is exactly equivalent to a GMRF with a specific sparse
precision matrix; for ν=0.5 (exponential kernel, `P[i,j] = σ² exp(-r/L_c)`),
this is the OU-process spatial extension.

We use **dense** Matérn covariance per particle per depth (the patch is
8×8 = 64 cells, computationally trivial; no need for sparse-precision
optimisation) with:
- **σ_init²**: prior variance, set to `(σ_coh² + σ_plume² + σ_submeso² +
  σ_inertial²) ≈ 0.078² m²/s²` matching the layered noise's learnable
  amplitude at σ_fc = 8 cm/s.
- **L_c = 5 km**: matches the slow-component spatial scale in
  `noise_model_design.md` (σ_s = 10 cells × 500 m = 5 km).
- **ν = 0.5 (exponential)**: roughness matches the underlying noise
  (Matérn-1/2 = OU spatial). Smoother kernels (ν=1.5) over-regularise.

The block-diagonal-in-depth Kalman update spreads each leg's residual
across ALL 64 cells via the prior covariance — not just the dwell-touched
ones. Residual attribution honours the prior's spatial structure.

**Cross-depth correlation is dropped** (covariance is block-diagonal
across depths). For the layered-physics noise this is wrong — `coh` is
truly depth-coherent, `submeso` and `inertial` share an `exp(-z/L_z)`
profile across depths — but capturing those couplings requires
per-component decomposition (v3 architecture). v1.1 keeps depth-block-
diagonal as the simplest principled fix to the spatial prior; v3 lands
when Step 2's CTD-as-bias-observation makes per-component identifiable.

## 4. Why shadow trajectory, not shadow PF

The earlier proposal was a "shadow PF" — a parallel particle-filter
ensemble that dead-reckons without CTD/LoRa reweight, used by the
bias-Kalman as the source of "where dead-reckoning would have ended up."

The shadow PF turned out to be unnecessary because **the bias-Kalman
observation can be computed analytically** from per-particle accumulators:

- `x_start[i]` (snapshot at leg-start, anchored to the previous LoRa fix)
- `prior_disp[i]` = `Σ prior_velocity(shadow_pos[i], depth, t) × dt` over
  the leg's submerged ticks
- `dwell[i, cell]` = time spent in each cell along the shadow trajectory

Together these give the deterministic prediction:
```
predicted_end[i] = x_start[i] + prior_disp[i] + Σ_cells dwell[i, cell] × b̂[i, cell]
y_obs[i]  = tri_pos − predicted_end[i] (modulo b̂; absorbed into innovation)
```

This is rigorously the conditional Gaussian observation a Kalman update
needs. Process noise (random component of the leg trajectory) is part
of σ_obs, not of `prior_disp`.

What the **shadow trajectory** (`pf.shadow_lats`, `pf.shadow_lons`) gives us:
1. **A b̂-independent position to compute prior_disp at**: `prior_velocity`
   is sampled at the shadow's current position each tick; this gives a
   prior_disp that doesn't depend on the bias estimate (RBPF correctness
   per Schön/Gustafsson/Nordlund 2005, "Marginalized particle filters,"
   IEEE TSP 53(7)).
2. **A b̂-independent dwell trajectory**: `dwell[i, cell]` counts cells
   the SHADOW visits, not cells the real PF visits. H = dwell is now
   independent of b̂.
3. **A re-anchoring point at LoRa fixes**: the shadow gets reinit'd at
   `tri_pos` alongside the real PF, so each leg's shadow starts from
   truth-anchored state.

The shadow is a per-particle accumulator state, not its own PF. It has
no weights, no observations, no resampling logic of its own. Its
process-noise draws are shared with the real PF (correlated; they
diverge only via the bias term `extra_*` and reweight-driven resample
selection on the real PF, with shadow gathered alongside).

## 5. Posterior-variance gate on controller queries

Per stats reviewer (Dee 2005 §3): outside the cells the drifter has
adequately observed, the bias posterior is just the prior. Reporting
`prior_mean ≈ 0` with `prior_variance ≈ σ_init²` as if it were a
posterior is a silent honesty violation — the controller treats it as
"I know this cell's bias is 0" when really it's "I know nothing."

`LiveBiasKnowledge.get_current_at` checks the per-particle ensemble-mean
posterior diagonal variance at the queried cell. If the variance hasn't
dropped below `posterior_var_gate_ratio × σ_init²` (default 0.5), the
gate falls back to clean prior and ignores the per-cell `b̂_mean`. This
stops the controller from acting on noisy unobserved-cell estimates
when the drifter happens to be planning into a region it hasn't seen.

## 6. OU temporal evolution between observations

The slow components have τ ≈ 12–36 h temporal correlation; the bias
state is approximately stationary within a leg but not across the
mission. Static-`b̂` would make the Kalman accumulate evidence as if
the true bias were time-invariant, producing over-confident posteriors
late in the mission.

`BiasFieldState.ou_evolve(dt, τ)` between Kalman updates:
```
γ = exp(-dt / τ)
mean ← γ × mean
cov ← γ² × cov + (1 - γ²) × cov_prior
```

Standard OU: shrinks mean toward 0 (the prior mean) and inflates cov
toward `cov_prior` (the stationary distribution). dt is time since
the last bias-Kalman update. τ default 36 h matches `τ_slow` in the
layered-noise design (Lindgren-Rue-Lindström 2011 §3.5 for Matérn
space-time formalism).

Within a 72 h mission this is small (γ ≈ 0.86 over 6 h leg), but
correctly inflates posterior variance late in the mission. Critical
when contributing to fleet posterior: a stale estimate from an aging
mission shouldn't dominate fresh observations from a new drifter.

## 7. Storvik audit (2026-04-25)

Per stats reviewer: per-particle static state (b̂, P, dwell, x_start,
prior_disp) carried through resamples is subject to Storvik 2002
degeneracy — naive carry-through collapses to one ancestral lineage.
Mitigation requires Storvik's sufficient-statistic update (we have:
`b̂` + `P` are sufficient stats given the linear-Gaussian observation
model) AND that the resample step does NOT re-apply observations
already absorbed into the posterior.

Audit (test-only, no commit): 9 per-particle arrays gather correctly
on resample; gathered arrays are memory-independent from originals
(numpy fancy-indexing returns a copy); `pf.lats` and `pf.shadow_lats`
synced on resample (each slot's real and shadow are paired by lineage).
Leg-end Kalman update fires BEFORE the LoRa-driven resample, so the
post-resample state is the post-Kalman state and no double-counting
occurs. Pass.

## 8. Open architectural questions (queued)

### Controller chatter under sharper observer — RESOLVED 2026-04-26

The drifter controls reviewer flagged "better observer → worse control"
as the certainty-equivalent control failure mode over a discrete depth
ladder + 30-min greedy look-ahead.

Resolution: `MPCStationKeeper` (vectorized beam-search receding-horizon
MPC) closes 42% of the greedy→physics-floor gap under perfect knowledge
across 8 SoG stations. See `controller_mpc_baseline_2026-04-26.md`.
The chatter problem dissolved structurally with extended horizon — score
margins widen and the indifferent-decision regime that motivated
hysteresis disappears. Posterior-aware version (CVaR / chance-constrained
over `(b̂_mean, P)`) is the next step, queued behind Step 2.1.

### Tidal-phase + wind-state axis on bias state — QUEUED

Originally rejected with Panel 2.2 robotics critique (2026-04-25);
reopened 2026-04-26 with concrete physics evidence. Yang 2020 PNNL
Salish tidal validation: SalishSeaCast M2 amplitude has 11-27% error
at central VENUS, with the ellipse "too circular." This means a non-
trivial fraction of the forecast residual has 12.4 h periodicity. The
current OU prior with `τ_slow = 36 h` cannot represent residuals that
flip sign every 6.2 h: the Kalman either averages across phases (learns
~zero) or learns the residual at whichever phase happened during the
legs and applies it at the wrong phase later.

The honest bias model is `b̂(lat, lon, depth, M2_phase, K1_phase,
wind_state)`, not `b̂(lat, lon, depth)`. M2/K1 phase is deterministic
from `t_tide`; wind state is dimension-3 (NW/SE/calm) from HRDPS
persistence. Adds dimensions to the existing state — does NOT require
dropping spatial structure. Fleet-scale aggregation actually benefits
because different drifters sample different phases (improves
identifiability across the phase axis).

Implementation cost: per-particle state grows by `n_phase_bins ×
n_wind_states` = ~12-16× (e.g. 4 M2-phase bins × 3 K1-phase bins ×
3 wind states). Within current memory budget. Identifiability needs
re-validating against the multi-axis bias structure.

### CTD likelihood deployment-realism gap

Currently `CTDSensor.sigma_S_psu = 0.02` (instrument-only). The
simulator's `T_truth(lat, lon, depth, t) = TracerField.sample(...)` is
exactly the SalishSeaCast field with no bias injected, so this σ_S is
mathematically correct for the simulator. In real deployment, Soontiens
2017 SoG salinity bias is 0.3–0.7 g/kg = 15–35× σ_S_instrument; the
PF likelihood would degenerate (every particle gets weight ≈ 0).

Step 2 adds:
1. **Tracer noise injection** in simulator truth (parity with velocity
   layered noise) — gives physically-honest deployment-like residuals.
2. **(T, S) bias state** on bias-Kalman side — separate scalars per
   particle that absorb the systematic component of T/S residuals,
   leaving the discriminative spatial-gradient component for position
   inference.

### v3 plume-offset channel via salinity-gradient Jacobian

The `δ_plume` latent (plume-front mis-placement scalar, see
`ctd_sensor_model.md` §2) is observable via salinity residual × ∂S/∂x.
`TracerField.sample_salinity_gradient_ms` already computes the Jacobian
finite-differentially. Step 2 wires this into the bias-Kalman as a
per-tick observation channel separate from CTD's per-tick PF reweight.

This is what makes per-component bias state **identifiable** — without
it, leg-end displacement alone gives one 2D vector per leg, which can't
disambiguate `coh + plume·tanh(z) + submeso·exp(-z/20) + inertial(z)·rotating`
into separate components. CTD-via-δ_plume is the mechanism-specific
observation that breaks the ambiguity.

### Fleet-scale aggregation

Per-mission inference produces sufficient statistics `(b̂_mean, P)` for
Bayesian combination across drifters. Multiple drifters' contributions
combine via:
```
Λ_fleet = Σ_drifters P_drifter⁻¹
m_fleet = Λ_fleet⁻¹ × Σ_drifters P_drifter⁻¹ × b̂_drifter
```

Each drifter's controller uses its OWN posterior during the mission;
fleet aggregation produces a global prior for new missions but doesn't
replace per-mission inference. The architecture supports this without
per-mission ramifications. Aggregation is a separate workstream.

## 9. What did NOT work (decided against)

- **Hilbert-space reduced-rank GP (v2)**: stats reviewer ruled out for
  N=12 obs regime. Solin & Särkkä 2014 is a computational trick for
  large-N (target 100s–1000s of obs); doesn't address the sparse-obs
  identifiability problem. At M=50 basis functions × 12 obs we'd be
  fitting 50 free coefficients to 12 numbers.
- **v2.5 hybrid per-component Hilbert**: non-identifiable from leg-end-
  only obs. `tanh(z)` and `exp(-z/20)` are nearly collinear over 5–50 m
  so plume and submeso components alias. Becomes potentially viable
  AFTER Step 2 adds CTD-as-mechanism-specific channel.
- **Pure scalar v3 latent prior** (5 scalars): drops spatial structure
  that v1 grid IS recovering correctly (z=10 r=0.96 with submeso). v1.1
  dense-Matérn preserves spatial info while fixing the diagonal-prior
  bug.
- **Post-update marginal smoothing** of bias state: tested at σ ∈ {1.0,
  2.5} cells in `_diag_bias_vs_truth.py`; both annihilated the signal
  (learned magnitude → 0). Marginal smoothing is not a substitute for
  prior covariance. `gaussian_smooth` and `smooth_sigma_cells` removed
  in Step 1.
- **Shadow PF as full second filter**: rejected in favor of analytical
  observation. Shadow only needs dwell + prior_disp accumulators.

## 10. Code map

Step 1 architecture lives in:

- `experiments/harmonic_prototype/rbpf_prototype/bias_field.py`
  - `BiasFieldState` dataclass: `mean_u`/`mean_v` (N, D, Y, X);
    `cov_u`/`cov_v` (N, D, Y·X, Y·X) dense; `dwell` (N, D, Y, X);
    `cov_prior` (Y·X, Y·X) shared; `x_start_lat/lon`, `prior_disp_east/north` (N,)
  - `BiasFieldState.init`: builds Matérn covariance from L_c, ν
  - `BiasFieldState.kalman_update_leg`: block-diagonal-in-depth Kalman
    with rank-1 covariance reduction per particle per depth
  - `BiasFieldState.ou_evolve`: between-observation OU
  - `BiasFieldState.posterior_var_at`: query for variance gate
  - `BiasFieldState.gather`: copies all 9 per-particle arrays on resample
  - `_build_matern_cov`: Matérn ν=0.5 / 1.5 builder

- `experiments/harmonic_prototype/rbpf_prototype/rbpf.py`
  - `PositionRBPF`: adds `shadow_lats`, `shadow_lons` per particle
  - `predict()` advances real and shadow with shared process noise;
    returns shadow's prior samples for `prior_disp` accumulation
  - `maybe_resample()` gathers both real and shadow by same idx

- `experiments/harmonic_prototype/rbpf_prototype/experiment.py`
  - `BiasConfig`: `l_corr_m`, `matern_nu`, `tau_ou_sec`,
    `posterior_var_gate_ratio`, `sigma_obs_leg_m`
  - `LiveBiasKnowledge`: posterior-variance gate before returning
    `prior + b̂_mean`
  - `run_one_station`: leg-start snapshot (`reset_leg_accumulators`),
    shadow-driven dwell + prior_disp accumulation, analytical
    observation `y_obs = tri − x_start − prior_disp`, OU evolution
    between Kalman updates, shadow re-anchoring at LoRa reinit

## 11. References

- Dee 2005, "Bias and data assimilation," QJRMS 131. The canonical
  bias-aware DA paper. §3 on observation noise inflation.
- Lindgren, Rue, Lindström 2011, "An explicit link between Gaussian
  fields and Gaussian Markov random fields: the SPDE approach,"
  JRSS-B 73. Matérn↔GMRF equivalence; §3.5 for space-time.
- Schön, Gustafsson, Nordlund 2005, "Marginalized particle filters
  for mixed linear/nonlinear state-space models," IEEE TSP 53(7).
  RBPF correctness conditions (H = function of nonlinear state only).
- Storvik 2002, "Particle filters for state-space models with the
  presence of unknown static parameters," IEEE TSP 50(2). Sufficient-
  statistic approach to per-particle static state.
- Solin & Särkkä 2014/2019, "Hilbert space methods for reduced-rank
  Gaussian process regression." Why this is the wrong tool here.
- Sollich & Williams 2004, "Using the Equivalent Kernel to Understand
  GP Regression," NIPS. Why posterior smoothing is not equivalent to
  GP prior at finite N.
- Mayne et al, "Constrained model predictive control: Stability and
  optimality," Automatica 36(6) 2000. Dual-control / robust MPC,
  relevant to Step 3.
- Smith, Schwager, Rus 2011, "Persistent ocean monitoring with
  underwater gliders." Field experience with certainty-equivalent
  control under uncertain forecast.

Verbatim reviewer outputs from the 2026-04-25 architecture review are
in `architecture_review_findings_2026-04-25.md`.
