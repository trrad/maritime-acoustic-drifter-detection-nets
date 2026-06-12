# Controller architecture — depth-only station-keeping

**Status:** design doc, 2026-04-24. Documents the existing greedy-myopic
controller (what scripts 10–22 use) and the upgrade path (Tier 1–3)
justified by the control-literature survey.

## 1. The decision problem

A passive ballast-controlled node chooses `d_t ∈ {0.5, 5, 10, 20, 50}` m
at each 30-min decision point. It then advects horizontally at that
depth's water current (plus its 0.1 m/s vertical transition speed
while depth is changing) until the next decision. Goal: minimise
distance from a fixed station (lat, lon).

Three observations / uncertainties the controller must handle:

- **Position uncertainty** — the PF provides a posterior over the
  node's current position. The controller uses the posterior mean
  (currently) or could use the full posterior (future).
- **Forecast uncertainty** — the currents-at-depth are not known
  exactly; the forecast (SalishSeaCast clean NEMO + learned bias
  field) has residual error. The controller's depth choice is
  optimal w.r.t. the forecast, not the truth.
- **Future-current uncertainty** — even the forecast degrades with
  lead time. τ_fast ≈ 3 h, τ_slow ≈ 36 h (see
  `noise_model_design.md`). Anything planned beyond τ_fast has
  substantial forecast uncertainty.

The controller architecture is how we trade off against these three
uncertainty sources.

## 2. Tier 0 — greedy-myopic (what we have)

```
at each 30-min decision:
    for each depth d:
        v_d = forecast(pos, d, t)
        projected_pos = pos + v_d * lookahead
        score[d] = distance(projected_pos, station)
    setpoint = argmin(score)
```

- Horizon H = 30 min (one tick).
- Uncertainty: ignored (treats forecast as truth).
- State awareness: uses current position but doesn't plan forward.

Implemented in `ballast_controller.StationKeeper.choose_depth`. Runs
in O(n_depths) per decision — very cheap. Interpretable: "which depth
most opposes drift right now?"

### Empirical limits

Phase 2 sweep (2026-04-24) with depth-coherent noise:

| σ_fc (cm/s) | baseline_real %<500m | best PF %<500m | gap |
|---|---|---|---|
| 0 | 56% | 50% | 6 pp (PF tax) |
| 2 | 43% | 24% | 19 pp |
| 4 | 22% | 10% | 12 pp |
| 6 | 12% | 6% | 6 pp |
| 8 | 7% | 3% | 4 pp |

Even `baseline_real` (oracle-controller with *perfect* knowledge of
the noisy truth) drops sharply with σ_fc. That's the "controller
authority" ceiling. See §6 for why.

The specific Tier-0 fragility: 30-min lookahead is shorter than τ_fast,
so the controller can't average out fast-noise-driven mis-choices.
Longer-horizon controllers (Tier 1+) handle this.

## 3. Tier 1 — receding-horizon deterministic MPC

```
at each decision:
    plan: depth sequence (d_1, ..., d_H) minimising projected
          cumulative distance-from-station over H ticks
    execute d_1 only
    replan at next decision with updated position
```

- Horizon H = 3–12 h (6–24 ticks at 30 min cadence).
- Uncertainty: ignored inside the plan (forecast treated as truth
  over H). But *replanning* at each tick limits damage from forecast
  drift — if the H-step plan goes wrong early, next tick's replan
  corrects.
- State: uses current PF posterior mean as initial condition, plans
  depth trajectory forward.

### Published precedent

**Branch et al. 2017** (*IJCAI-17 Oceans & Space Workshop*, file
`2017_branch_ijcai_station_keeping.pdf`) is the closest analog: an
autonomous underwater glider station-keeping exercise with ROMS
current forecasts as input, during a 19-day October 2016 Monterey Bay
deployment supporting NASA's SWOT calibration "virtual mooring." They
use a greedy planner over glide-slope/depth/heading actions over
multi-hour horizons. Baselines cited: Hodges & Fratantoni 2009 (2.0 km
station-keeping RMS) and Rudnick et al. 2013 (3.6 km and 1.8 km).

**Thompson et al. 2010** (*ICRA*, file
`2010_thompson_icra_adaptive_glider.pdf`) — "Spatiotemporal Path
Planning in Strong, Dynamic, Uncertain Currents" — uses 24–72 h
horizons on ROMS forecasts with explicit uncertainty treatment for
glider path planning in the Southern California Bight. Not station-
keeping specifically but the horizon-length lesson applies.

### Computational cost

Brute-force search over the full depth tree: 5^H. At H=6 ticks (3 h)
that's 15,625 — enumerable. At H=12 (6 h) = 244M — needs branch-and-
bound or coarser discretisation. At H=24 (12 h) = 6×10¹⁶ — requires
dynamic programming over the depth lattice.

Pragmatic approach: DP over (time, depth) states, backing up from
horizon H to present. O(H · n_depths²) per decision = O(24 · 25) =
600 ops per decision. Trivial. Gives the exact optimal plan at that
level of state abstraction.

### Expected performance

Agent B's estimate, conservative: Tier 1 → 10–20 pp improvement over
Tier 0 in the σ_fc = 4–8 regime, because the controller can
*tactically* accept near-term drift to position itself for later
favourable currents. Tier 0 can't see past 30 min.

### Implementation

Moderate — 4–6 hours of code work. Needs:
- State: (time, depth). Transitions: depth change by one level per
  tick (limited by vertical speed) + advection at that depth's
  forecast current.
- Reward: negative distance-from-station summed over horizon,
  possibly with quadratic depth-change penalty.
- DP: Bellman backup over the lattice.

## 4. Tier 2 — scenario-tree MPC

Same structure as Tier 1, but the plan is robust to forecast
uncertainty. Concretely:

```
at each decision:
    sample N scenarios from the forecast-uncertainty distribution
        (each scenario = one realisation of η_{t..t+H})
    build a scenario tree: at the root, branch on depth choice;
        at later stages, branch on scenario and on depth, enforcing
        non-anticipativity (decision at stage k is independent of
        scenario realisations after stage k)
    solve the stochastic programme to minimise E[cost] + λ·CVaR
    execute d_1 from root
```

Key concepts:

**Non-anticipativity**: at decision stage k, we must commit to one
depth without knowing which scenario is true. So d_1 is a single
value, not N values — but d_2 can be a function of the scenario
realisation observed at stage 1 (i.e., after executing d_1 and
observing position at stage 1). This is the defining constraint of
stochastic programming.

**Scenario generation**: sample N ≈ 10–30 forecast realisations from
the noise model. For our layered noise (see `noise_model_design.md`)
each realisation has a fresh draw of the coherent + surface-intensified
components over the horizon. Each scenario thus represents a
physically-plausible future.

**CVaR (Conditional Value at Risk)**: for risk level α (say 0.1), CVaR
is the expected cost *in the worst α-fraction of scenarios*. It
penalises tail risk without going all the way to worst-case. The
standard objective is E[cost] + λ·CVaR_α[cost] with λ ∈ [0.2, 1.0]
chosen to trade off average and tail behaviour.

### Published precedent

**Subramani & Lermusiaux 2016, 2019** (files
`2016_subramani_stochastic_do_levelset.pdf` and
`2019_subramani_risk_optimal_path.pdf`) — MSEAS work on stochastic
dynamically-orthogonal level-set PDEs over ensemble ocean forecasts.
They solve a *distribution* of time-optimal paths over an ensemble;
their 2019 extension explicitly minimises risk under decision-theoretic
cost metrics. The computational cost is severe (4D PDE per ensemble
member) — this is research-grade, not prototype-ready.

**Kularatne, Bhattacharya, Hsieh 2018** (files
`2018_kularatne_graph_general_flows_RSS.pdf` and
`2018_kularatne_adaptive_discretisation.pdf`) — graph-search path
planning in time-varying flows. Gives a tractable discretised
framework for scenario-MPC-like problems. Closer to prototype-ready.

### Expected performance and cost

Agent B's estimate: Tier 2 → 5–15 pp additional improvement over
Tier 1 at σ_fc = 4–8, because the controller can't cheat by trusting
one forecast; it must commit to depths that perform across a range of
realisations.

Cost: N scenarios × DP cost per scenario. At N=10, H=12 ticks, DP
with depth + scenario coupling (partial scenario visibility at each
stage): a few 10⁴ ops per decision. Still fast on modern hardware.
Scenario sampling costs: fresh noise draws, ~ms each.

### Implementation

~1 day of code work. Main complexity: scenario tree data structure
and non-anticipativity bookkeeping. scipy/cvxpy or a custom
dynamic-programming solver.

## 5. Tier 3 — POMDP with surfacing as action

Now the action space includes `surface_now`:

```
state: (pos_belief, bias_belief, time_in_leg, surface_dwell_remaining)
action: (depth, surface_now ∈ {true, false})
observation: if surface: LoRa ranges (O(10 m) σ); if submerged: CTD
             (tight T, S but non-positional); no horizontal obs submerged
transition: advect under forecast+bias at chosen depth; surfacing
            constraint (node must be at 0.5 m for LoRa)
reward: -distance(pos, station) - λ_surf · surface_cost - λ_reconstr ·
        retrospective_position_error_at_acoustic_events
```

Full POMDP solvers (POMCP, DESPOT) exist but are research-grade and
slow. The value added over Tier 2 is an emergent adaptive-surfacing
policy: the controller surfaces *when its belief about position
diverges enough that a LoRa fix is worth the surface-drift cost*.

### Tractable approximation

Rather than a full POMDP solve, run threshold-based surfacing *inside*
a Tier-2 MPC:

```
surface_now = True iff:
    projected_envelope_breach_probability > θ_breach  OR
    retrospective_PFerr_budget_exceeded                OR
    bias_variance_at_current_cell_high + above_threshold_legs_since_last
```

80% of the POMDP benefit at a quarter the complexity. Captures the
"adaptive surfacing policy driven by controller state" discussion
(Phase-2 notes, 2026-04-24).

## 6. The authority bound (publishable)

Agent B flagged this: no published closed-form bound says "depth-only
station-keeping is limited by the convex hull of available depth
currents." It's folk knowledge implicit in virtual-mooring profiling-
float work (Fan et al. 2023, *Applied Ocean Research*, cited from
abstract only) but never formalised.

Statement (informal): at time t, the achievable instantaneous
velocity at the node's position is `{v(pos, d, t) : d ∈ depths}` —
five points in 2-D (east, north). The controller can realise any
convex combination over a depth-transition interval, but holds one
depth between decisions. Let `v_target` be the velocity required to
return to station over the lookahead. Station-keeping to within the
envelope is feasible iff `v_target ∈ hull({v_d})`; otherwise residual
drift per tick = distance from v_target to nearest hull point.

Over horizon H, the reachable *set* isn't H·hull (depths can change
mid-horizon). It's the Minkowski sum of per-tick hulls along any
admissible depth trajectory — a larger set. That's why longer-horizon
MPC does better than greedy: the reachable set *expands* with H
because of depth-switching.

Adding forecast error: under depth-coherent noise the hull shifts
rigidly by η. Under surface-intensified noise (the layered model in
`noise_model_design.md`) the hull *deforms* — deep depths are less
affected than shallow depths. This is why the layered noise model
matters: the controller gets authority back at depth.

Formalising this bound properly, with a worked diagram across σ
regimes and a theorem giving station-keeping feasibility as a function
of (NEMO shear spectrum, σ_fc, forecast temporal structure), is a
plausible prototype-paper contribution.

## 6.5 The certainty-equivalence regression flagged 2026-04-25

The Phase 2.1+ bias-architecture rebuild surfaced a controller-relevant
finding worth pre-flagging here: the single-station smoke (commit
`b0d1868` work) showed `grid > no_learn` mean-distance regression
(1780 → 2196 m) — better information about the bias field producing
WORSE station-keeping. The drifter-controls domain reviewer
(`architecture_review_findings_2026-04-25.md` Panel 3.1) diagnosed
this as the canonical certainty-equivalence-collapse failure mode:

> Better PF → worse station-keeping is the textbook signature of
> certainty-equivalent control over a *non-monotone* action set: as
> `b̂_mean` sharpens, the 30-min look-ahead more confidently picks the
> depth whose forecast advection looks closest to zero net displacement,
> and on a discrete ladder {0.5, 5, 10, 20, 50 m} that pick *flips*
> between adjacent rungs as `b̂_mean` jitters by O(cm/s).

Smith 2011 (Persistent Ocean Monitoring with Underwater Gliders, JFR)
and the Subramani/Lermusiaux line warn against this exactly: feeding
high-variance point estimates into a controller that treats them as
truth amplifies estimator error into actuation error.

**Implication for Tier 0:** this is in the noise floor of the
prototype's sample size (single station, single seed). Step 1
validation runs multi-seed smoke (5 seeds × 4 stations) to determine
whether the regression survives averaging. If yes, Step 3 (controller
rework) becomes mandatory before any further observer work is
worthwhile — no observer fix can compensate for a certainty-equivalent
controller fed a high-variance signal.

**Implication for Tier 1+:** the canonical fixes from the literature
are (a) hysteresis band on rung selection (Spray glider field-tested,
controls reviewer's Pt. Conception 2014 example), (b) CVaR-style
penalty against the worst-case rung within the posterior, (c) robust
MPC plan over the b̂ posterior covariance, (d) chance-constrained MPC.
All of these consume `(b̂_mean, P)` from the bias-Kalman, not just
`b̂_mean`. The Tier 1 receding-horizon MPC design in §3 should be
revisited with this in mind — the posterior-aware version IS the
right Tier 1, not a Tier 1.5 add-on.

**Implication for Tier 2 / scenario-tree MPC:** scenario-tree MPC was
already designed for forecast uncertainty; the same machinery handles
bias-posterior uncertainty. The transition from Tier 1 to Tier 2 may
be smaller than originally scoped if Tier 1 is built posterior-aware
from the start.

Decision deferred until Step 1 multi-seed validation completes. See
`bias_inference_architecture.md` §8 for the full open-question list.

### 2026-04-26 update — Tier 1 substantially built

Multi-seed validation came back as expected: `grid` ≈ `no_learn`
within noise (PFerr -42% with CTD, mean dist flat). The
chatter-diagnostic confirmed greedy-myopic decisions flip rungs at
< 100 m score margin 94% of the time **even on the clean prior with
no bias state at all** — so the failure mode is the discrete winner-
takes-all rule on a near-tied score landscape, not just `b̂_mean`
jitter.

Tier 1 receding-horizon MPC (`MPCStationKeeper` in
`ballast_controller.py`) now exists as a vectorized beam-search
brute-force over depth-setpoint sequences. Under perfect knowledge
across 8 SoG stations:

- Greedy mean across sites: 1139 m
- MPC h=24 (12 h plan, full M2 cycle) mean across sites: 657 m
- 42% reduction. MPC dominates greedy at every site, every horizon.
- Diminishing returns past h=12 (M2 half-cycle).

See `controller_mpc_baseline_2026-04-26.md` for the per-site table,
the predictor=dynamics fix that unblocked it, and the design
decisions made.

**What the §6.5 implication-list got right:** that real Tier 1 is
posterior-aware, not Tier 1.5. The current MPC consumes only
`b̂_mean` (via PerfectKnowledge in the baseline; via LiveBiasKnowledge
once wired into `experiment.py`); the CVaR / chance-constrained
posterior-aware version is the next step, requiring `(b̂_mean, P)` —
queued behind Step 2.1 (realistic CTD noise) so the observer can be
honestly measured.

**What the §6.5 implication-list got wrong:** hysteresis is NOT
needed. Real MPC with extended horizon closes the chatter problem
structurally because score margins widen with longer lookahead — the
indifferent-decision regime that motivated hysteresis disappears.

## 7. Ranking for the prototype roadmap

| Priority | Upgrade | Est. win | Est. code cost | Dependencies |
|---|---|---|---|---|
| 1 | Layered noise model (see `noise_model_design.md`) | n/a — this is an honesty fix | ~2 h | none |
| 2 | Tier 0 → Tier 1 (receding-horizon MPC) | +10–20 pp at σ=4–8 | ~4–6 h | (1) |
| 3 | CTD integration (see `ctd_sensor_model.md`) | PFerr floor drops; plume-mode identifier | ~1 day | ERDDAP tracer fetch |
| 4 | Tier 1 → Tier 2 (scenario-tree MPC + CVaR) | +5–15 pp at σ=4–8 | ~1 day | (2) |
| 5 | Physically-structured bias prior ((δ_plume, η_wind)) | Dimensionality reduction 640 → 3 parameters; faster convergence | ~2–3 days | (1), (3) |
| 6 | Threshold-based adaptive surfacing inside MPC | +? retrospective-PFerr win | ~0.5 day | (2) |
| 7 | Authority-bound formalisation + figure | Writing-level contribution | ~1–2 days | none |
| — | Full POMDP solve | Research-grade; defer | weeks | everything |
| — | Deep RL policy | Requires 10⁶ sim episodes + no interpretability; defer | weeks | everything |

## References (downloaded copies in this directory)

- Branch, A., Troesch, M., Flexas, M., Thompson, A., Ferrara, J.,
  Chao, Y., & Chien, S. (2017). Station keeping with an autonomous
  underwater glider using a predictive model of currents. *IJCAI-17
  Oceans & Space Workshop*.
  File: `2017_branch_ijcai_station_keeping.pdf`.
- Kularatne, D., Bhattacharya, S., & Hsieh, M.A. (2018a). Going with
  the flow: a graph based approach to optimal path planning in general
  flows. *Autonomous Robots*, 42(7), 1369–1387.
  DOI 10.1007/s10514-018-9741-6.
  File: `2018_kularatne_graph_general_flows_RSS.pdf` (RSS 2016 precursor).
- Kularatne, D., Bhattacharya, S., & Hsieh, M.A. (2018b). Optimal path
  planning in time-varying flows using adaptive discretisation.
  *IEEE Robotics and Automation Letters*.
  File: `2018_kularatne_adaptive_discretisation.pdf`.
- Lolla, T., Ueckermann, M.P., Yiğit, K., Haley, P.J., & Lermusiaux,
  P.F.J. (2012). Path planning in time dependent flow fields using
  level set methods. *ICRA 2012*.
  File: `2012_lolla_path_planning_levelset_ICRA.pdf`.
- Subramani, D.N., & Lermusiaux, P.F.J. (2016). Energy-optimal path
  planning by stochastic dynamically orthogonal level-set
  optimization. *Ocean Modelling*, 100, 57–77.
  File: `2016_subramani_stochastic_do_levelset.pdf`.
- Subramani, D.N., & Lermusiaux, P.F.J. (2019). Risk-optimal path
  planning in stochastic dynamic environments. *Comput. Methods Appl.
  Mech. Engrg.*, 353, 391–415. DOI 10.1016/j.cma.2019.04.033.
  File: `2019_subramani_risk_optimal_path.pdf`.
- Thompson, D.R., Chien, S., Chao, Y., Li, P., Cahill, B., Levin, J.,
  Schofield, O., Balasuriya, A., Petillo, S., Arrott, M., & Meisinger,
  M. (2010). Spatiotemporal path planning in strong, dynamic,
  uncertain currents. *ICRA 2010*.
  File: `2010_thompson_icra_adaptive_glider.pdf`.

**Cited from abstract/metadata only** (paywalled; flagged):

- Lolla, T., Ueckermann, M.P., Yiğit, K., Haley, P.J., & Lermusiaux,
  P.F.J. (2014). Time-optimal path planning in dynamic flows using
  level set equations: theory and schemes. *Ocean Dynamics*, 64(10),
  1373–1397.
- Fan et al. (2023). Research on ocean-current-prediction-based
  virtual mooring strategy for portable underwater profilers.
  *Applied Ocean Research*.
- Hodges, B.K., & Fratantoni, D.M. (2009); Rudnick, D.L. et al.
  (2013) — cited via Branch et al. 2017 for baseline station-keeping
  RMS numbers.
