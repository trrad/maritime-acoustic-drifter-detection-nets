# Controller MPC baseline (Step 3a, 2026-04-26)

**Status:** site-authority + horizon sweep complete under perfect knowledge.
MPC h=24 closes 42% of the mean-distance gap from greedy across 8 SoG
stations, monotone improvement with horizon, diminishing past h=12 (one M2
half-cycle). Code in `ballast_controller.py` (`MPCStationKeeper`,
`TrajectoryStationKeeper`), `ballast_dynamics.py` (`step()` substepping),
`truth_field.py` + `submesoscale.py` (`sample_batched`).

This document records the chain that produced the result + the design
decisions made along the way. It is the durable replacement for
`docs/reference/architecture_review_findings_2026-04-25.md`'s "Controller
chatter / certainty-equivalence — DEFERRED" status row.

## 1. The chain

The 2026-04-25 multi-seed Step 1 validation showed `grid` ≈ `no_learn`
within noise (Δ=88 m, |Δ|/SD=0.15) on station-keeping mean distance —
even though grid+ctd dropped PFerr by 42%. The plan's gate triggered:
"if multi-seed regression survives, controller is the bottleneck."

The chatter diagnostic (`_diag_rung_chatter.py`) made this concrete: 94%
of greedy controller decisions flipped depth rungs at score margins
< 100 m **even on the clean SalishSeaCast prior with no bias state**.
Universal decision indifference — the failure mode wasn't `b̂_mean`
jitter, it was the discrete winner-takes-all rule on a near-tied score
landscape.

First fix attempt: a multi-step trajectory-integrating predictor
(`TrajectoryStationKeeper`) with vertical transit modelling. Did NOT
reduce chatter. Made mean-dist *worse* in 2/3 configs. Investigation
revealed the predictor's substep model diverged from the dynamics'
single-shot per-tick model — the controller was solving an optimization
against a physics it would never execute.

Two fixes applied together:
1. **`ballast_dynamics.step()` substeps internally** (`n_substeps=10`,
   60 s sub-resolution for the 600 s mission tick). Models vertical
   shear during transit, time evolution within the tick, horizontal
   field gradient as the drifter moves.
2. **Predictor IS dynamics.** `TrajectoryStationKeeper._score_rollout`
   and `MPCStationKeeper._score_batch` literally call `step()` — no
   separate physics model. Under perfect info the prediction matches
   the executed trajectory exactly.

Result: greedy `traj` becomes competitive with `single` (mean across
sites 1196 vs 1200 m). But strict greedy dominance under perfect info
still doesn't hold — different greedy first-decisions land in different
states; greedy compounding doesn't guarantee global optimality.

The actual fix: **real receding-horizon MPC**, planning a sequence of
depth setpoints over an extended horizon, scoring via forward-rollout
mean distance, committing only the first setpoint, replanning each
decision (Mayne, Rawlings, Diehl 2017 §2.4).

## 2. Architecture

### `MPCStationKeeper` (vectorized beam search)

Plans `horizon_n` decision intervals ahead. At each decision time,
expands the beam (B → B·K) by K candidate depths, rolls each forward
one decision interval via `step()`, scores by accumulated mean distance,
prunes back to top `beam_width`. Returns the first setpoint of the best
surviving sequence.

`beam_width >= K^horizon_n` ⇒ exact brute-force search (no pruning).
At beam_width=200 with K=5 depths, captures essentially all of brute
force at h=6 (verified: brute h=6 = 841 m mean across sites; beam
extension to h=8/12/24 monotonically improves to 657 m).

### Vectorized batched RGI sampling

Required because beam search at `B·K = 1000` candidates per substep
makes O(N) scalar sampling infeasible. Added `sample_batched(lats,
lons, depths, t_sec) -> (u_arr, v_arr)` to `TruthField`,
`LayeredNoiseField`, and per-component fields. Each batched call does
~12 RGI lookups (one per noise component + truth slabs), each with N
points — amortising the per-call overhead (~50 µs) across all sequences.

Empirically ~50× speedup at N=625 (h=4); the speedup scales sublinearly
with N because per-point RGI cost (~200 ns) dominates at large N.

### `step()` substepping default

`n_substeps=10` (60 s sub-resolution for the 600 s tick). Replaces the
single-shot per-tick model that sampled current at end-depth-after-
transit only. Frozen scripts 10-21 pick up the new behaviour
transparently — their outputs will shift toward correctness if rerun.
This is a substrate change, not a regression.

## 3. Site-authority sweep methodology

`_diag_site_authority.py` runs at each of 8 hand-picked SoG stations
(from `22_rbpf_v2_bias_learning.py:HAND_PICKED_STATIONS`):

- **5 passive runs**: no controller, hold setpoint at each candidate
  depth {0.5, 5, 10, 20, 50 m}. Best of these = the uncontrolled-drifter
  ceiling at this site.
- **5 greedy perfect-info runs**: `StationKeeper` (single-point Euler)
  + `TrajectoryStationKeeper` at lookaheads {30 min, 1 h, 3 h, 12 h},
  all with `PerfectKnowledge(truth=real)`.
- **4 MPC perfect-info runs**: `MPCStationKeeper` at h=6 (brute force),
  h=8 / h=12 / h=24 (beam_width=200), all with `PerfectKnowledge`.

72 h missions, dt=600 s tick, 30 min control cadence. 16 worker
processes. ~37 min wall clock for all 112 runs (8 stations × 14 jobs
each).

**No PF, no observer noise, no sensor pipeline.** This baseline isolates
the controller's authority bound at each site from observer quality.
Results are the upper-bound ceiling for any deployable controller; real
deployment with the bias-Kalman observer + realistic CTD noise (Step
2.1 prerequisite) lands somewhere worse.

## 4. Results

Per-site mean dist over 72 h, single noise realisation (seed=42):

| Site | bathy | best passive | best greedy | mpc h=6 | mpc h=8 | mpc h=12 | **mpc h=24** | greedy → h=24 |
|---|---|---|---|---|---|---|---|---|
| S1 | 289 m | 5894 (d=10) | 1276 (single) | 1017 | 865 | 779 | **766** | -40% |
| S2 | 188 m | 2538 (d=20) | 1017 (traj 12 h) | 979 | 782 | 698 | **674** | -34% |
| S3 | 182 m | 2329 (d=20) | 557 (traj 30 m) | 535 | 464 | 438 | **422** | -24% |
| S4 | 92 m | 1106 (d=20) | 513 (traj 1 h) | 395 | 393 | 375 | **374** | -27% |
| S5 | 177 m | 3031 (d=50) | 878 (single) | 739 | 705 | 658 | **643** | -27% |
| S6 | 328 m | 2393 (d=10) | 714 (traj 3 h) | 611 | 584 | 570 | **561** | -21% |
| S7 | 402 m | 8388 (d=5) | 3898 (traj 30 m) | 2252 | 1915 | 1666 | **1632** | -58% |
| S8 | 90 m | 1251 (d=20) | 259 (traj 30 m) | 203 | 201 | 188 | **186** | -28% |
| **mean** | | | 1139 | 841 | 738 | 672 | **657** | **-42%** |

### Five findings worth durable record

1. **MPC strictly dominates greedy at every site, every horizon.** No
   sites where greedy beats MPC at any tested h. Confirms the greedy-vs-
   DP gap diagnosis; rules out remaining "single beats traj under
   perfect info" anomaly.

2. **Horizon helps MPC monotonically; diminishes past M2 half-cycle.**
   Mean-across-sites: h=6 → h=8: -103 m (-12%), h=8 → h=12: -66 m
   (-9%), h=12 → h=24: -15 m (-2%). h=12 (6 h plan, M2 half-cycle)
   captures essentially all the benefit. h=24 confirms convergence.

3. **The "longer lookahead is bad" earlier finding was a greedy-
   controller pathology.** Single-decision controllers actively get
   worse with longer lookahead (perf_traj_30m=1196 → perf_traj_12h=
   2097, +75% worse) because the score evaluates a hypothetical N-h
   trajectory the controller will never execute. Real MPC inverts this
   — same horizon (12 h) under MPC gives 657 m.

4. **Site authorities now cleanly stratify.** Per-site physics floor
   ranges 186 m (S8) → 1632 m (S7). S7 is geometrically hard: even
   perfect-info MPC can't get under 1.6 km because currents are
   fast/depth-coherent across the 5-rung ladder there. S8/S4/S3 are
   inherently good shear-keeper sites. The earlier "drifter-only"
   tagging was a controller artifact.

5. **The greedy-controller "longer is sometimes better" was real but
   not universal.** S2 at greedy traj_12h beats traj_30m by 200 m; S6
   at traj_3h beats traj_30m by 200 m. Most sites though degrade
   monotonically with longer-lookahead-greedy.

## 5. Decided against

- **Continuous depth optimisation** (gradient descent / Brent search
  over d ∈ [d_min, d_max]). Defensible architectural move per
  field-practitioner reviewer; orthogonal to MPC and may stack. Not
  needed to demonstrate the MPC value; deferred until MPC + observer
  integration shows whether the discrete ladder is a meaningful loss.
- **Hysteresis band on rung selection.** Was the obvious next move from
  the chatter diagnostic; turned out unnecessary because real MPC
  closes the chatter problem structurally (longer horizon picks more
  decisively because score margins are larger).
- **CVaR / chance-constrained scoring over the posterior.** The right
  posterior-aware version per the MPC theorist reviewer's
  recommendation. Requires `(b̂_mean, P)` from the observer; requires
  observer to be honest (Step 2.1 first). Queued.

## 6. What's still TBD

1. **Step 2.1 — realistic CTD noise.** Without this, every PFerr is
   15-35× too optimistic per Soontiens 2017 SoG salinity bias. Prereq
   for any honest observer-quality measurement.

2. **Wire `MPCStationKeeper` into `experiment.py`** (replacing
   `TrajectoryStationKeeper`). Then the multi-seed smoke can be re-run
   with MPC + each observer config (no_learn, grid, grid+ctd) and
   we can measure how much of the 42% greedy→MPC gap a real observer
   closes.

3. **Posterior-aware MPC.** Once observer is honest: extend the rollout
   score to consume the per-particle bias posterior covariance — sample
   N draws of the bias field from `N(b̂_mean, P)`, run the rollout under
   each, score by `E[cost] + λ · CVaR_α[cost]` (Rockafellar-Uryasev
   2000; Chow & Pavone 2014). The particle ensemble already provides
   the M scenarios.

4. **Tidal-phase + wind-state bias state axis.** Resurrected from the
   originally-rejected Panel 2.2 critique with new physics evidence
   (Yang 2020 PNNL: SalishSeaCast tides have 11-27% amplitude error,
   so OU τ=36 h prior can't represent M2 sign-flips at 6.2 h period).
   Independent of MPC; orthogonal improvement to Step 1's spatial
   bias state.

5. **Continuous depth control.** Deferred — see "Decided against."
   Worth revisiting after observer integration if discrete-ladder
   quantization shows up as a measurable loss.

6. **Multi-seed / multi-noise-realisation site-authority sweep.**
   Current results are single noise seed=42. Cross-realisation variance
   axis requires per-job noise builds (blocked on noise-cache work,
   task #8). Separate sweep when ready.

## 7. Code map

```
ballast_dynamics.py
  step(state, t, dt, current_at, w_z_max, n_substeps=10)
    — substeps internally for physical faithfulness; replaces
      single-shot per-tick model
  _step_atomic(...)
    — sub-resolution integration step (the old `step()` body)

ballast_controller.py
  TrajectoryStationKeeper
    — greedy depth picker; rollout = forward step() over lookahead
  MPCStationKeeper
    — receding-horizon MPC; vectorized beam search over
      depth-setpoint sequences; predictor IS dynamics
  PerfectKnowledge
    — adds get_current_at_batched for vectorized rollouts

truth_field.py
  TruthField.sample_batched(lats, lons, depths, t_sec)
    — vectorized N-point sample, per-slab batching

submesoscale.py
  LayeredNoiseField.sample_batched(...)
  _StationaryField.sample_batched(...)
  _InertialField.sample_batched(...)
    — vectorized N-point samples per noise component

experiments/harmonic_prototype/
  _diag_rung_chatter.py
    — per-decision controller logging; surfaced 94% sub-100m chatter
  _diag_site_authority.py
    — per-site passive + greedy + MPC sweep; produces this doc's
      results table and per-site x/y trajectory plots in
      figures/_diag_site_authority/
```

## 8. References

- Mayne, Rawlings, Diehl. *Model Predictive Control: Theory,
  Computation, Design*, 2nd ed., Nob Hill, 2017. §2.4 (cadence vs
  horizon), §3 (terminal sets / stability).
- Bertsekas. *Dynamic Programming and Optimal Control*, Vol. I,
  4th ed., 2017. §6.3 (limited-lookahead pathologies — why greedy
  is provably suboptimal here).
- Mesbah. "Stochastic model predictive control: An overview and
  perspectives for future research." *IEEE Control Systems Magazine*
  36(6):30, 2016. (Posterior-aware Tier 3 reference.)
- Rockafellar & Uryasev. "Optimization of conditional value-at-risk."
  *J. Risk* 2(3):21, 2000. (CVaR formulation for posterior-aware
  scoring.)
- Yang, Wang, Branch, Xiao. PNNL-30448, 2020. SalishSeaCast tidal
  validation (11-27% M2 amplitude error). Cited by oceanographer
  reviewer; motivates tidal-phase axis on bias state.
- Halverson & Pawlowicz 2016. *Atmosphere-Ocean* 54(2). Surface-
  current observations, central SoG.
