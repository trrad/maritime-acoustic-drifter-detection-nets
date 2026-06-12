# Architecture review findings — 2026-04-25 session

Verbatim record of the five domain-practitioner review panels that
shaped the Phase 2.1+ bias-inference architecture rebuild. These were
spawned as `Agent` calls during the 2026-04-25 prototype work. Useful
as historical record; the syntheses live in
`bias_inference_architecture.md`.

The five panels in chronological order:

1. **Boundary review** (oceanographer + numerical-analysis personas) on
   the 5-component layered noise's Gaussian-filter boundary handling.
   Output: `noise_model_boundary_review_2026-04-24.md` (one day prior).
2. **Bias-architecture review** (oceanographer/DA + AUV/glider robotics
   + Bayesian/KF/PF stats) on the v1 grid bias-Kalman, after the
   `_diag_bias_vs_truth.py` diagnostic surfaced 1.5–3× magnitude
   overshoot and 6%-cells-learned.
3. **Step 1 design review** (drifter controls + Bayesian/PF stats) on
   the proposed analytical-observation + Matérn GMRF + shadow-trajectory
   architecture before any of it was coded.

Each panel was a parallel `Agent` dispatch with explicit instruction to
read local references, web-fetch primary literature, and raise
substantive objections (not validate reasonable-looking things).

---

## Panel 2: Bias-architecture review (2026-04-25)

### 2.1 Oceanographer / DA practitioner

> **Severity 1 — You have ~12 scalar observations and a 1280-DOF state
> (640 cells × 2 components). This is not a parameterisation problem;
> it is an observability problem, and reparameterising won't manufacture
> information.** With 8×8×5×2 ≈ 1280 unknowns and ~22 scalar leg-end
> residuals (11 fixes × 2 components), you are ~60× underdetermined.
> Dee 2005 (*QJRMS* 131:3323; the canonical bias-aware DA reference)
> and the Keppenne/Rienecker line that culminates in the Tellus A 2009
> Fertig–Hunt–Szunyogh "Observation bias correction with an ensemble
> Kalman filter" both keep bias states an order of magnitude *smaller*
> than the observation count, or use **separate, much longer
> localisation radii for the bias covariance than for the random-error
> covariance**. Your overshoot by 1.5–3× is the textbook signature of
> an underdetermined least-squares inversion with a non-shrinking prior
> — Kalman gain divides by `Σ T²·var + σ²_obs`, and a single particle's
> dwell concentrates in 2–4 cells per leg, so each updated cell absorbs
> the *entire* leg residual instead of sharing it across the correlated
> neighbourhood.

> **Severity 2 — Smooth-on-update is the standard fix in this regime
> and you should run it before any basis refactor.** The "smoothing as
> proxy for a correlation prior" trick is a well-known equivalence (a
> Gaussian-smoothed Kalman update with σ_smooth ≈ correlation length is,
> to leading order, an EnKF with a Gaussian-shaped B with that
> correlation length). Your design doc's σ_s for the dominant slow
> components is 5 km (coh, submeso); your cell is 2 km; so σ_smooth ≈
> 2.5 cells *physically matches* the noise model you wrote down.
> Running this is one line and one re-sweep.

> **Severity 3 — Per-component (v2.5) beats generic-spatial (v2) for
> this observation budget — but only after smoothing is exhausted, and
> only if you fix the depth-channel sharing first.** Your finding that
> z=10 m correlates r=0.96 with submeso and z=50 m with coh is the
> strongest evidence that **the components are physically separable
> and the depths are not independent unknowns** — they share the four
> amplitude fields (η_coh, η_plume, η_submeso, η_inertial). v1's
> grid×depth treats (z=10, z=50) as 2D unknowns; v2.5 with the right
> vertical profiles bakes in that they're projections of the same 4
> amplitude fields. That's a 5× state-dim reduction that's *physically
> grounded*, not a generic basis truncation.

> **Severity 4 — Your observation model is leaving substantial
> information on the table, and this dominates any state-parameterisation
> choice.** The Halverson 2018 CODAR/drifter comparison (DFO TR 319,
> locally) shows **drifter heading drift between fixes is itself a
> current-direction observation**. **CTD T/S residual against
> SalishSeaCast-predicted (T, S) is a direct observation of the plume
> component** — your own `ctd_sensor_model.md` flags it.

> **Severity 5 — The 6%-of-cells finding does not mean v1 is wrong; in
> operational DA literature this is normal and is fixed by (a) prior
> shrinkage + (b) correlation-informed spreading, not by reducing state
> dim.** "Most cells stay at prior" is fine; "the cells that update
> overshoot 3×" is the bug.

> The first objection a junior on my team would raise is **"you have
> 22 scalar observations, a 1280-DOF state, and a diagonal prior with
> no length scale — of course it overshoots; turn on the smoother that's
> already implemented and rerun before you write a single line of basis
> code."**

Cited: Dee 2005 QJRMS 131:3323; Keppenne 2005 NPG 12:491; Fertig 2009
Tellus A 61:2; Carrassi 2018 WIREs; Solin & Särkkä 2019 Stat Comp 29:419;
Vandenbergh 2017 Ocean Modelling; Fossum 2023 Front Mar Sci 10.

### 2.2 AUV/glider field practitioner

> **1. You are learning the wrong object. Halverson & Pawlowicz 2016
> establish that lower Strait of Georgia surface flow is dominated by
> tides (M2/MK3 enhanced at the surface, with the shallow-water MK3
> nearly as strong as the mean), the Fraser plume jet (~14 cm/s with
> twin asymmetric gyres), and a wind-coherent subtidal component (~20–30
> cm/s).** None of those are static spatial residuals — they are
> time-varying forcings whose phase is observable from boundary data
> the drifter does not need to learn (tidal harmonics from t_tide, Sand
> Heads wind, Fraser discharge). A 320-cell static grid is structurally
> incapable of representing an M2 ellipse rotating through your domain
> over 6 h, so the r=0.96 correlation with submesoscale-at-z=10 m is
> almost certainly aliasing tidal/wind phase into a spatial pattern
> that is then applied at the wrong phase one leg later.

> **2. No deployed AUV/glider does spatial-bias-field learning at this
> density; they all use a real-time depth-averaged current estimate
> per dive and a forecast prior, period.** Claus & Bachmayer 2015
> (terrain-aided Slocum) explicitly does **not** learn a forecast bias
> — they use altimeter+DEM correlation against dead-reckoning, accept
> ~50 m error when currents are <5 cm/s, and stop there. Smith et al.
> 2010/2011 (USC/Stanford persistent monitoring with ROMS) treats
> forecast error as **uncertainty to plan around**, not a field to
> estimate.

> **3. The "better information → worse station-keeping" regression is
> the textbook certainty-equivalence failure, and the literature's
> answer is not "smooth the estimator" — it is "make the controller
> uncertainty-aware".** This is exactly Mayne's dual-control / robust-MPC
> critique: feeding a high-variance point estimate into a controller
> that treats it as truth amplifies estimator error into actuation
> error.

> **4. Drop the bias field for v1 and pick option 4 + option 5.** With
> 12 obs / 72 h / 320 cells you do not have a spatial-estimation
> problem; you have a temporal-forcing problem (tide phase, wind, plume).

> **5. "Add smoothing first, then decide" is the wrong smallest step
> because it preserves the architectural mistake.** The smallest honest
> experiment is a **one-line ablation**: replace the learned grid with
> a tidal-harmonic + wind-regression prior (no spatial cells) and
> compare against no_learn at the same σ_fc=8 cm/s.

> The first objection a junior on my team would raise is **"you're
> regressing a static spatial field against a tidally-dominated flow
> field — the r=0.96 is phase aliasing, not skill."**

Cited: Halverson & Pawlowicz 2016 Atmos-Ocean; Kastner 2018 JGR Oceans;
Claus & Bachmayer 2015 JFR; Paull 2014 IEEE JOE; Smith 2011 ICRA;
Lebedev YoMaHa'07; Lorenzen 2019 adaptive MPC.

### 2.3 Spatial statistics / GP / Bayesian inference practitioner

> **Bottom line up front.** Smoothing first, then decide. v1-grid +
> Gaussian smooth is, to first order, the projection-onto-low-frequency-
> modes operator that v2 Hilbert spits out — and at N=12 you cannot
> tell the two apart. Hilbert is the wrong hammer here. Your problem
> is not basis regularity, it's that you have ~12 scalar observations
> and you're updating a 640-dim state under a posterior whose prior
> covariance is **diagonal** — so every cell that gets dwell weight
> gets a fully-independent, unregularised draw from the residual.

> **1. Diagonal prior is the bug, not the basis.** Your Kalman update
> has zero spatial prior coupling: `var_u` is diagonal at init and
> stays diagonal because `gaussian_smooth` smooths *marginals*, not
> the joint covariance. **The cheapest principled fix is not smoothing
> the posterior, it's putting an exponential/Matérn spatial prior on
> the bias and letting the Kalman update do the regularising itself.**

> **2. Smoothing the posterior ≠ GP prior, and the equivalence is
> asymptotic.** Sollich & Williams ("Using the Equivalent Kernel to
> Understand GP Regression," NIPS 2004) show GP regression looks like
> kernel smoothing **in the dense-data continuum limit**. That limit
> is exactly the regime you are not in.

> **3. Solin & Särkkä is the wrong reference for your regime.** It's
> a **computational** trick for *large N*: it replaces O(N³) GP
> inference with O(NM² + M³) by projecting the kernel onto Laplace
> eigenfunctions on a bounded domain. Nothing in that paper addresses
> N=12, M=50 — at that ratio you're not approximating a GP, you're
> fitting 50 free coefficients to 12 numbers and the Hilbert basis is
> just a fancy reparameterisation of an underdetermined system.

> **4. Component decomposition (v2.5) is non-identifiable by
> construction.** Each leg-end residual is a single linear functional
> of `coh + tanh(z)·plume + exp(-z/20)·submeso + R(t)·inertial + white`.
> With 12 such functionals and 4 components × ~30 coefficients = 120
> unknowns, the linear inverse problem has rank ≤ 12 — the components
> alias unless the dwell-weighting × depth-profile × time-rotation
> matrix has well-conditioned column blocks per component, which over
> a 6-h leg in a single station's vicinity it does not.

> **5. SPDE/Matérn is what you actually want — but offline-fit, not
> estimated online.** Lindgren–Rue–Lindström 2011 (*JRSS-B* 73) is
> the right hammer: a Matérn-1 GP with σ_s=5 km is *exactly* a sparse-
> precision GMRF on your 8×8 grid (a 5-point stencil with one
> parameter). Build the precision matrix Q at init from the **known**
> covariance, replace your diagonal `var_u/var_v` with this Q⁻¹, and
> your Kalman update propagates information across cells through the
> prior at update time — for free, no extra observations. **This is
> the fix; it's ~20 lines around `kalman_update_leg`.**

> The first objection a junior on my team would raise is **"why are
> you spatially smoothing the posterior instead of putting the spatial
> covariance in the prior?"**

Cited: Dee 2005 ECMWF preprint; Lindgren-Rue-Lindström 2011 JRSS-B;
Sollich & Williams 2004 NIPS; Solin & Särkkä 2014 arXiv:1401.5508;
Auligné/McNally/Dee 2007 QJRMS.

### Synthesis (Panel 2)

All three reviewers converged on:
- Diagonal prior is structurally wrong — bias_field.py needs a Matérn
  spatial prior, not posterior smoothing.
- Hilbert-space (v2) is wrong tool for N=12 sparse-obs regime.
- v2.5 per-component decomposition is non-identifiable from leg-end-only
  observations.

They diverged on:
- **Oceanographer/DA**: try smoothing first as a cheap regulariser, then
  add proper Matérn prior.
- **AUV robotics**: the entire bias-field architecture is wrong; replace
  with tide-phase + wind-regression. The controller is the deeper issue.
- **Stats**: skip smoothing entirely (it's a band-aid that doesn't fix
  the underlying problem); do Matérn GMRF prior directly.

The smoothing test (σ ∈ {1.0, 2.5} cells) was run inline:
both annihilated the bias signal. Stats reviewer was right; smoothing
isn't a partial fix, it's the wrong fix. We adopted the Matérn GMRF
prior approach directly.

---

## Panel 3: Step 1 design review (2026-04-25)

After drafting the analytical-observation + Matérn-GMRF + shadow-
trajectory architecture but before any code, two reviewers were
spawned in parallel to critique the design.

### 3.1 Drifter controls / field practitioner

> **Bottom line up front.** The proposal correctly identifies the
> cannibalisation bug and correctly diagnoses the diagonal-prior
> overshoot, but it patches the observer while leaving in place the
> reason the observer-improvement caused regression in the first place.
> PFerr↓ → station-keeping↑ is not a paradox; it is a classical
> *certainty-equivalence collapse* under multimodal control authority,
> and it will get worse with a sharper observer, not better.

> **Objection 1 — Severity: blocking. The regression is the controller,
> not the observer.** Better PF → worse station-keeping is the textbook
> signature of certainty-equivalent control over a *non-monotone* action
> set: as `b̂_mean` sharpens, the 30-min look-ahead more confidently
> picks the depth whose forecast advection looks closest to zero net
> displacement, and on a discrete ladder {0.5, 5, 10, 20, 50 m} that
> pick *flips* between adjacent rungs as `b̂_mean` jitters by O(cm/s).
> You are watching control chatter induced by observer sharpness,
> exactly the failure mode Smith et al. (Persistent Ocean Monitoring
> with Underwater Gliders, JFR 2011) and the Subramani/Lermusiaux line
> warn against. I have personally watched this on Spray gliders off
> Pt. Conception in 2014: tightening the EKF on near-surface current
> made the depth-ladder controller *worse* until we added a hysteresis
> band and a CVaR-style penalty against the worst-case rung.

> **Objection 2 — Severity: blocking. Matérn GMRF at L_c=5 km on a
> 16×16 km patch is rank-deficient by construction.** With L_c≈5 km
> and 2-km cells, the implied prior correlation between any two cells
> in the patch is ≥0.13 (Matérn-3/2) and typically >0.5 in the patch
> interior. The effective rank is roughly `(patch / L_c)² ≈ (16/5)² ≈
> 10` — and you only get ~12 leg observations per mission, of which
> only ~6 are at distinct depths.

> **Objection 3 — Severity: high. The `predicted_end = x_start +
> prior_disp` decouples observer from PF — but only because it ignores
> CTD-pulled trajectories, which is wrong on its own terms.** You are
> right that referencing `pf.lats` directly in the innovation is the
> cannibalisation bug. But `prior_disp[i] += prior_velocity(particle_pos,
> depth, t) × dt` accumulates along the *CTD-resampled particle
> trajectory*, so `predicted_end` is conditioned on CTD likelihood
> whether you wanted it to be or not. The clean fix is to maintain a
> parallel CTD-blind dead-reckoned trajectory per particle for
> `prior_disp` and `dwell`, and only use the CTD-aware trajectory for
> `pf.mean()` to the controller.

> **Objection 4 — Severity: high. You are missing two free observation
> channels that ARGO/Spray people would never leave on the table.**
> (a) **Surface heading drift during the 60-s LoRa surface event** is
> a direct vector-current observation at z≈0. (b) **Ballast setpoint
> vs achieved depth delta** is a vertical-velocity observation. (c)
> **Tidal phase** is the single strongest regressor on SoG surface
> currents per Halverson & Pawlowicz 2016.

> **Objection 5 — Severity: medium. Smoke-test n is too small to call
> regression at all.** `no_learn` 1780 m vs `grid` 2196 m on what looks
> like a single seed and a handful of missions is well inside SoG
> mesoscale variability. Before any architectural rework, run ≥30
> seeds × ≥3 tidal phases × wind regimes (NW, SE, calm).

> The first objection a junior on my team would raise is *"why are you
> tuning the observer when the controller still picks a depth from a
> 5-rung ladder using a point estimate of a field whose 1-σ is
> comparable to the rung spacing?"*

Cited: Smith 2011 JFR; Subramani/Haley/Lermusiaux JGR 2017; Branch et al
IJCAI 2017; Halverson & Pawlowicz 2016 (local); Soontiens & Allen 2017
(local).

### 3.2 Bayesian / KF / particle-filter statistics

> The architecture is a recognizable RBPF with a static linear sub-
> state — sound *as a research prototype* skeleton. But there are five
> real holes between it and a defensible posterior.

> **1. The per-particle observation `y_obs[i] = tri − x_start[i] −
> prior_disp[i]` is not a clean linear-Gaussian observation of `b̂[i]`,
> because `H[i] = dwell[i, ·]` is correlated with the unknown bias.**
> RBPF (Schön/Gustafsson/Nordlund 2005, *IEEE TSP* 53(7)) requires the
> conditionally-linear sub-state to satisfy `y_t = C_t(x_{1:t}^n) z_t
> + e_t` with `C_t` a deterministic function of the *nonlinear
> trajectory only*. In your design `dwell[i, cell]` is a function of
> the particle's predicted trajectory which in the predict step is `x
> ← x + dt(prior + b̂[i])` — so `H[i]` is a function of `b̂[i]` itself.
> That violates the conditioning argument. The Kalman gain becomes
> `K = P H^T (HPH^T + R)^{-1}` evaluated at a point estimate of `H`,
> and the linear update `b̂ ← b̂ + K(y − Hb̂)` is no longer the
> conditional posterior mean — it's an EKF-style local linearization
> with unaccounted Jacobian terms.

> **2. The factorization `p(x_{1:t}, b | y_{1:t}) = p(x_{1:t}|y_{1:t})
> p(b | x_{1:t}, y_{1:t})` is fine — but storing per-particle `b̂[i]`
> and *resampling along with x* converts a static parameter into a
> particle-indexed quantity that suffers Storvik degeneracy.** Storvik
> (2002, *IEEE TSP* 50(2)) and Andrieu/Doucet/Tadić (2005) show that
> naïvely carrying a static parameter through resampling collapses
> the parameter ensemble to the lineage of one ancestor; after a few
> resampling events all surviving particles share the same `b̂` history.
> Your N=200 with ESS<0.5N triggering ~once per leg means after 6–8
> legs you effectively have <10 distinct bias histories. Mitigation
> requires either Storvik's sufficient-statistic update (your `b̂, P`
> per particle qualifies — good) **plus a mechanism that doesn't reset
> `P` to its leg-start value when a particle inherits an ancestor with
> different dwell history**.

> **3. The CTD likelihood is a misspecified observation model and will
> pull the position posterior in a biased direction; the bias-Kalman
> then attributes the resulting mis-localization to `b̂`.** Soontiens
> & Allen (2017) bias of −0.3 to −0.7 g/kg vs. σ_S ≈ 0.02 PSU is a
> 15–35σ misspecification — exactly the regime Dee (2005, *QJRMS* 131)
> identifies as the failure mode of bias-blind assimilation: the system
> cannot separate forecast-state bias from observation-operator bias
> without independent constraints. You need `σ_S → σ_S²+σ_S_bias²`
> inflation at minimum, ideally a separate scalar T/S bias state per
> particle.

> **4. Treating `b̂` as static across a 72 h mission when τ_slow ≈ 36 h
> is wrong by ~14% per the user's own arithmetic, and matters for the
> late-mission posterior.** Lindgren-Rue-Lindström (2011, *JRSS-B* 73,
> §3.5) give the SPDE for a Matérn space-time field; the right move
> here is an OU temporal evolution `db = −(1/τ) b dt + Q^{1/2} dW`
> on each cell with the GMRF spatial coupling on Q. Practically: add
> `P ← e^{-2dt/τ} P + (1 − e^{-2dt/τ}) P_∞` between legs.

> **5. Identifiability: ~24 scalar leg observations vs. 320-dim bias
> state with effective rank ~10 from the GMRF prior is well-posed *as
> a regression* but only along the trajectory — the bias-field estimate
> outside the visited cells is the prior, not the posterior.** This
> is fine if and only if the controller only consumes `b̂` along
> future planned trajectories that overlap visited cells. If anything
> downstream queries `b̂` at unvisited cells (extrapolation), you are
> reporting prior mean (≈0) with prior variance (49 cm²/s²) and calling
> it a posterior — a silent honesty violation. **Add a posterior-
> variance gate before any use.**

> **The first objection a junior on my team would raise is that `H[i]`
> (the per-particle dwell vector) is a function of the very bias `b̂[i]`
> you're trying to estimate, so the leg-end Kalman update is not a
> conditional Gaussian update — it's an unflagged EKF linearization,
> and that breaks the RBPF correctness argument before any of the other
> concerns matter.**

Cited: Schön/Gustafsson/Nordlund 2005 IEEE TSP; Storvik 2002 IEEE TSP;
Andrieu/Doucet/Holenstein 2010 PMCMC JRSS-B; Dee 2005 QJRMS;
Lindsten/Bunch/Schön RB particle smoothers.

### Synthesis (Panel 3)

The reviews converged on:
- The H-depends-on-b̂ violation must be fixed before any other observer
  work matters. **The fix: dwell + prior_disp accumulate along the
  SHADOW trajectory (which advects with prior + process_noise only,
  no b̂)**, not the real PF trajectory. This was the design pivot: the
  shadow position is what makes H independent of b̂.
- Posterior-variance gate on controller queries is required (not
  optional) — silent honesty violation otherwise.
- OU temporal evolution between observations is required for late-
  mission posterior accuracy.
- CTD likelihood σ_S inflation is required for deployment realism (but
  not for current simulator's idealised T/S truth — Step 2 work).
- Storvik degeneracy mitigated correctly given sufficient-statistic
  Storvik (b̂, P per particle) AND no observation-double-counting at
  resample (audit-only).

The drifter controls reviewer's #1 (controller is the bottleneck) was
acknowledged but deferred — Step 1 fixes the observer first; multi-seed
smoke validates whether the regression survives. If it does, Step 3
(controller rework) becomes mandatory.

The L_c=5km on 16km patch rank-deficiency objection (controls #2) was
acknowledged but accepted: at single-mission scale the prior IS expected
to dominate (sample-sparse regime per oceanographer/DA panel #5). Fleet
aggregation (separate workstream) accumulates obs density that breaks
the rank deficit naturally. Variable L_c per region is a Step 4+ refinement.

The attribution leakage objection (controls #3) was the design pivot
that surfaced the shadow trajectory: prior_disp and dwell DO need to
accumulate along a CTD-blind trajectory. The Step 1 design adopts this.

---

## Status of each objection in Step 1 implementation

| Objection | Source | Step 1 status |
|---|---|---|
| Diagonal prior is wrong | Panel 2 (all three) | FIXED — dense Matérn |
| Smoothing is band-aid | Panel 2 stats | FIXED — removed |
| Hilbert (v2) wrong tool | Panel 2 stats | DECISION — rejected |
| v2.5 non-identifiable | Panel 2 stats | DECISION — deferred to Step 2 |
| Surface drift channel missing | Panel 2 oceano + Panel 3 controls | QUEUED — Step 1.5 |
| Cannibalisation via tri − pf.lats | (own diagnostic) | FIXED — analytical observation |
| H depends on b̂ | Panel 3 stats | FIXED — shadow trajectory |
| Storvik degeneracy | Panel 3 stats | AUDITED + PASSED |
| CTD σ_S misspec for deployment | Panel 3 stats | DEFERRED — Step 2 |
| b̂ static across mission | Panel 3 stats | FIXED — OU evolution |
| Identifiability outside visited cells | Panel 3 stats | FIXED — posterior-variance gate |
| Controller chatter / certainty-equivalence | Panel 3 controls | ADDRESSED 2026-04-26 — `MPCStationKeeper` (vectorized beam-search receding-horizon MPC) closes 42% of the greedy→physics-floor gap under perfect knowledge. See `controller_mpc_baseline_2026-04-26.md`. Posterior-aware version (CVaR / chance-constrained over `(b̂_mean, P)`) still queued. |
| GMRF rank-deficient | Panel 3 controls | ACCEPTED — sample-sparse single-mission |
| L_c heterogeneous in space | Panel 3 controls | DEFERRED — Step 4+ |
| Multi-seed sample size | Panel 3 controls | DONE 2026-04-26 — 4 stations × 5 seeds × 3 configs in `_smoke_ctd_one_station.py`; `grid` ≈ `no_learn` within noise (Δ=88 m, \|Δ\|/SD=0.15) |
| Tidal/wind regression as alternative | Panel 2 robotics | REOPENED 2026-04-26 — original rejection assumed SalishSeaCast tides are clean; Yang 2020 PNNL validation shows 11-27% M2 amplitude error, so OU τ=36 h prior cannot represent M2 sign-flips at 6.2 h period. Tidal-phase + wind-state axis on bias state is the queued response (does NOT require dropping spatial structure). See `phase21_plus_status_2026-04-26.md`. |
| Drop bias field entirely | Panel 2 robotics | NOT TAKEN — fleet-scale aggregation argument stands |

---

## Notes on what NOT to take from the reviews

The AUV/robotics reviewer was the most aggressive: they argued the
entire bias-field architecture is wrong for SoG (tidally-dominated,
phase-aliasing into spatial pattern) and recommended dropping it for
tidal-harmonic + wind-regression. This was NOT taken because:
1. SalishSeaCast already detrends tidal currents via the underlying
   NEMO model — the bias is the residual *after* tides, plume, and
   wind-driven response are accounted for. The reviewer's framing
   conflates total surface flow with forecast residual.
2. The fleet-scale deployment context: the design isn't optimised for
   one drifter to recover a complete bias map, but for hundreds of
   drifters' contributions to combine into a fleet-scale field. A
   tidal-regression-only approach loses the spatial structure that
   accumulates across drifters.
3. The diagnostic finding (z=10 r=0.96 with submeso, z=50 with coh)
   does indicate the spatial bias IS recoverable — it's just sample-
   sparse at single-mission scale, not phase-aliasing.

The reviewer's points (a) tide phase is a known signal we shouldn't
re-learn, (b) controller is the deeper issue, (c) field systems use
single-vector-per-leg approaches — are all valid framings that inform
Step 3 and beyond, but don't invalidate the bias-field-with-fleet-scale-
aggregation architecture.

The Smith 2011 / Subramani/Lermusiaux experience cited by the controls
reviewer is the canonical example of certainty-equivalence failure
under uncertain forecast — directly relevant to Step 3 controller
design.

---

## 2026-04-26 update

The "controller is the deeper issue" framing was right. Step 3 work
landed `MPCStationKeeper` (vectorized beam-search receding-horizon
MPC) which closes 42% of the greedy→physics-floor gap under perfect
knowledge across 8 SoG stations. See
`controller_mpc_baseline_2026-04-26.md` for the full sweep results,
the predictor=dynamics fix that unblocked it, and the design decisions
made along the way. The status table at top of this section is updated
accordingly.

Point (a) — "tide phase is a known signal we shouldn't re-learn" —
was rejected on the (now-disputed) ground that SalishSeaCast already
removes tides. Yang 2020 PNNL evidence reopens this: SalishSeaCast
M2 amplitude has 11-27% error at central VENUS; OU τ=36 h cannot
represent the resulting 6.2 h sign-flip residual structure. Tidal-
phase + wind-state axis on the bias-Kalman state is queued
(`phase21_plus_status_2026-04-26.md`). This does NOT require dropping
spatial structure — adds dimensions to the existing state.
