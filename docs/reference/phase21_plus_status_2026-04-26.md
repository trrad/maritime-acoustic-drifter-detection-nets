# Phase 2.1+ status refresh — 2026-04-26

Refresh of the original Phase 2.1+ plan
(`~/.claude/plans/jiggly-cuddling-wilkes.md`) after the 2026-04-25 →
04-26 work session. Captures what got done, what shifted in flight,
what's still outstanding, and the new findings that have rearranged
the priority order.

Read alongside:
- `controller_mpc_baseline_2026-04-26.md` — the controller findings
  this session produced (the substantive deliverable)
- `bias_inference_architecture.md` — Step 1 architecture (unchanged)
- `architecture_review_findings_2026-04-25.md` — prior reviewer
  outputs; Status of Objections table needs the refresh below applied

## Status against the original plan

| Original plan item | Status | Notes |
|---|---|---|
| Step 1 validation #1 — `_diag_bias_vs_truth.py` | ✅ Done | Matérn prior fix works: 72% learned cells (was 6%), magnitude no longer overshoots, per-depth correlations preserved (z=10 r_u=0.85 with submeso). |
| Step 1 validation #2 — multi-seed smoke | ✅ Done | 4 stations × 5 seeds × 3 configs = 60 runs, parallelised across 12 workers. `grid` ≈ `no_learn` within noise (Δ=88 m, \|Δ\|/SD=0.15). PFerr -42% with CTD (cheating per σ_S caveat). |
| Step 1 validation #3 — M1 canonical sweep | ❌ Skipped | Would have measured a known-flat aggregate against a known-broken controller. Will re-emerge as the M1 sweep against MPC + honest observer. |
| Step 1.5 — surface-drift channel + σ_obs decomp | ⏸ Demoted | Pointless until observer can be honestly measured (Step 2.1 prereq) and controller can use it (now resolved). Still useful when both prereqs land. |
| Step 2.1 — tracer noise injection | ❌ Not done — **PROMOTED to urgent** | Without realistic CTD noise every PFerr is 15-35× too optimistic. Now blocking the next observer comparison. |
| Step 2.2 — (T, S) bias state + plume-offset channel | ⏸ Queued | Depends on Step 2.1. |
| Step 3 — controller rework | ✅ Substantially done | TrajectoryStationKeeper + MPCStationKeeper landed; vectorized beam-search MPC; site-authority diagnostic shows MPC h=24 closes 42% of greedy→physics-floor gap under perfect info. Still to do: wire into `experiment.py`, posterior-aware (CVaR / chance-constrained) version. |
| Fleet-scale aggregation | ⏸ Untouched | Separate workstream. |
| M1/M2 canonical sweeps | ⏸ Pending | Blocked on Step 2.1 + MPC-in-experiment. |

## Mid-flight changes from original plan

These shifted while doing the work; record so future-you doesn't try to
relitigate.

1. **Mission objective reframed.** Not literal station-keeping; the
   prototype's job is fleet coverage + retrospective σ_pos for
   acoustic-event TDOA triangulation (per `docs/maritime_buoy_design.md`).
   The "1500 m envelope" target was a prototype default
   (`FINDINGS.md` lines 277, 370). Real metrics are per-site authority
   delta + retrospective σ_pos at acoustic-event timestamps. The
   site-authority diagnostic is dual-purpose: controller-ceiling
   reference AND production drop-site planning tool.

2. **`step()` substepping default.** `ballast_dynamics.step()` now
   substeps internally (`n_substeps=10`, 60 s sub-resolution for the
   600 s mission tick). Frozen scripts 10-21 pick up the new behaviour
   transparently — their outputs will shift toward physical correctness
   if rerun. Substrate change, not a regression.

3. **Predictor IS dynamics.** TrajectoryStationKeeper + MPCStationKeeper
   forward-rollouts call `step()` directly. No separate physics model
   in the controller; under perfect knowledge the prediction matches
   the executed trajectory exactly. Earlier divergence between
   predictor and dynamics was a real bug.

4. **Vectorized batched RGI sampling.** Added `sample_batched(lats,
   lons, depths, t_sec)` to TruthField, LayeredNoiseField, and per-
   component fields. ~50× speedup at N=625; required to make MPC
   beam-search at h≥6 tractable.

5. **Tidal-phase + wind-state bias state axis.** Resurrected from the
   originally-rejected Panel 2.2 critique. Yang 2020 PNNL evidence:
   SalishSeaCast tides have 11-27% M2 amplitude error, so the OU
   τ=36 h prior cannot represent M2 sign-flips at 6.2 h period. Queued
   as the next bias-state extension after Step 2.1.

6. **Controller architectural decisions made**:
   - Discrete depth ladder kept for now; continuous-depth optimization
     deferred (orthogonal to MPC; revisit if discrete-quantization
     shows up as a measurable loss after observer integration).
   - Beam search at width 200 with brute-force at h=6 (5^6 = 15625
     candidates fits memory; brute h=6 = 841 m mean across sites,
     beam h=24 = 657 m, monotone improvement).
   - Posterior-aware scoring (CVaR / chance-constrained) deferred
     until MPC is wired into the observer (needs `(b̂_mean, P)`).
   - Hysteresis on rung selection NOT NEEDED — was the obvious fix
     from the chatter diagnostic, but real MPC with longer horizon
     closes chatter structurally because score margins widen with
     lookahead.

## New artifacts this session

Code:
- `experiments/harmonic_prototype/_diag_rung_chatter.py` — per-decision
  controller logging, surfaced 94% sub-100 m chatter on clean prior.
- `experiments/harmonic_prototype/_diag_site_authority.py` — per-site
  passive + greedy + MPC sweep; produces the per-site x/y trajectory
  plots in `figures/_diag_site_authority/`.
- `ballast_controller.py::TrajectoryStationKeeper` — forward-rollout
  greedy keeper.
- `ballast_controller.py::MPCStationKeeper` — vectorized beam-search
  MPC.
- `ballast_controller.py::PerfectKnowledge.get_current_at_batched` —
  vectorized hook for MPC.
- `ballast_dynamics.step()` substepping refactor; `_step_atomic` for
  the sub-resolution body.
- `truth_field.py::TruthField.sample_batched`.
- `submesoscale.py::LayeredNoiseField.sample_batched` +
  `_StationaryField.sample_batched` + `_InertialField.sample_batched`.

Docs:
- `docs/reference/controller_mpc_baseline_2026-04-26.md` — substantive
  controller findings (this session).
- `docs/reference/phase21_plus_status_2026-04-26.md` — this doc.

Touch-ups still pending (next):
- `architecture_review_findings_2026-04-25.md` Status of Objections
  table: "Controller chatter / certainty-equivalence" → ADDRESSED;
  "Tidal/wind regression" → REOPENED.
- `controller_architecture.md` §6.5: link MPC baseline doc.
- `bias_inference_architecture.md`: queue tidal-phase + wind-state axis.

## What's next (in execution order)

1. **Step 2.1 — realistic CTD noise injection.** Layered tracer-bias
   field in simulator truth, calibrated against Soontiens 2017 SoG
   salinity bias (0.3-0.7 g/kg). Without this, every PFerr is
   structurally optimistic. Independent of controller work.

2. **Wire `MPCStationKeeper` into `experiment.py`** (replacing
   `TrajectoryStationKeeper` as the canonical active-workstream
   controller). Both keepers' `choose_depth` signatures already match;
   one-line construction change in `run_one_station`.

3. **Re-run multi-seed smoke with MPC + each observer config**
   (no_learn, grid, grid+ctd) at each σ_fc level. This is the actual
   measurement of how much of the 42% greedy→MPC gap a real (post-2.1)
   observer closes.

4. **Step 1.5** — surface-drift channel + σ_obs decomposition. Both
   prereqs (Step 2.1 done; observer measurable against MPC) now
   satisfied at this point.

5. **Tidal-phase + wind-state bias-state axis.** Independent of (1)-(4)
   in code; can parallelise. Adds dimensions (lat, lon, depth) →
   (lat, lon, depth, M2-phase, wind-state) on the bias-Kalman state.

6. **Posterior-aware MPC.** Sample N draws of the bias field from
   `N(b̂_mean, P)` (the particle ensemble already provides M scenarios);
   score by `E[cost] + λ · CVaR_α[cost]`.

7. **M1/M2 canonical sweeps** at the end, against the full stack.

## Out of scope (still)

- Fleet-scale aggregation (separate workstream).
- Continuous-depth control (revisit only if discrete-ladder shows up
  as a measurable loss after observer integration).
- All `rtl/vectors/maritime/` production code.
- Frozen experiments (scripts 01-11) — though `step()` substepping
  changes their numbers if rerun (correctness improvement).
- Frozen-by-convention experiments (scripts 12-21).
