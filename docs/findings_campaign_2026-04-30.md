# Findings: continuous-coverage deployment campaigns

**Run:** `20260430_campaign_combined` — merge of `20260430_redep72h`,
`20260430_norep`, and the existing `20260429_realistic_fixed6h_week`
baseline (D6_empirical / fixed_6h cell).

**Setup:** D6_empirical drop pattern (16 drifters at empirically-
optimized positions, originally targeted at a 72h objective in
`20260429_v5_empirical_drop_points.json`). 168h missions, event rate
1.0/h point + 4 boats, LoRa σ_range = 20 m. MPC controller
(horizon_n=12, beam_width=200, posterior_cvar scoring). Same event
RNG seed within each policy across the redep72h / no-redep variants
(`fixed_6h` seed 5000 / `fixed_12h` seed 5100 / `post_event_30m_12h`
seed 5200 — see "verification & seeding caveat" below).

## Headline matrix

Patrol-area coverage is the fraction of the D6_empirical patrol bbox
where ≥3 drifters are within 5 km AND the LSQ σ_event_floor < 500 m,
evaluated at 1h bin midpoints from the forward-filter (deployment-
honest) σ_pos. Reconstruction rates and σ_event errors come from the
TDOA mode-b windowed-RTS reconstructions of detected acoustic events.

| policy / cadence              | events | cov_mean | time_with_cov | mode-b recon | mode-b err_p50 | mode-b ttd_p50 | drifter-deploys/wk | surfacings/wk (fleet) |
|-------------------------------|-------:|---------:|--------------:|-------------:|---------------:|---------------:|-------------------:|----------------------:|
| `fixed_6h` × no-redep         |    605 |    0.256 |           99% |  (baseline)¹ |   (baseline)¹  |   (baseline)¹  |                 16 |                  ~448 |
| `fixed_6h` × redep72h         |    605 |    0.305 |           99% |   140 (23.1%) |          204 m |        214 min |                 48 |                 ~1344 |
| `fixed_12h` × no-redep        |    576 |    0.120 |           61% |    55 ( 9.5%) |          481 m |        147 min |                 16 |                  ~208 |
| `fixed_12h` × redep72h        |    576 |    0.146 |           65% |    63 (10.9%) |          645 m |        168 min |                 48 |                  ~624 |
| `post_event_30m_12h` × no-redep |  612 |    0.156 |           64% |   116 (19.0%) |           96 m |        36.8 min |                 16 |                  ~200 |
| `post_event_30m_12h` × redep72h | 612 | **0.349** |       **99%** |   **159 (26.0%)** |       **80 m** |    **30.8 min** |                 48 |                  ~600 |

¹ The `fixed_6h × no-redep` baseline reuses the cell from
`20260429_realistic_fixed6h_week`; reconstruction stats are reported
in that run's summary.

## Key findings

### 1. `post_event_30m_12h` × `redep72h` dominates every axis

Highest coverage (0.349 vs 0.256 for the next best), highest
reconstruction rate (26.0%), lowest σ_event error (80 m mode-b),
fastest time-to-detect (30.8 min mode-b), AND about half the
surfacing count of `fixed_6h × redep72h` (~600 vs ~1344 fleet
surfacings per week, important for power budget).

The "30 min after event" surfacing structure is the load-bearing
piece: it ensures every detected event gets a tight back-projection
window from the next LoRa fix (≤ 30 min ahead), so σ_pos at event
time is dominated by the fix uncertainty plus 30 min of OU drift,
not 6–12 h of drift. This is why mode-b err_p50 is 80 m vs
`fixed_6h`'s 204 m.

### 2. Redeployment benefit depends *heavily* on the surfacing policy

Coverage gain from adding redep72h (relative %):

- `fixed_6h`: +19% (0.256 → 0.305)
- `fixed_12h`: +22% (0.120 → 0.146)
- `post_event_30m_12h`: **+124%** (0.156 → 0.349)

`post_event` benefits enormously from redeployment because of a
positive feedback that no-redep can't escape: a drifter that drifts
out of audible range of any boats stops triggering surfacings →
its σ_pos grows unbounded → it can't contribute to event
localisation even if it eventually re-enters range, because mode-b
needs a recent LoRa fix to back-project from. Redep72h forces
drifters back into the patrol band every 72 h, restarting the
event-detection / surfacing / fix loop. `fixed_6h` and `fixed_12h`
drifters keep surfacing on schedule whether or not they hear events,
so they don't have this collapse mode — redep just tightens their
position posterior modestly.

### 3. Coverage half-life is set by surfacing cadence, not week-long drift

Every cell shows `half_life_h ≈ 2.5h` (time from a LoRa fix until
σ_event grows past 500 m again). Within the 168h mission, coverage
is a sawtooth keyed to the surfacing schedule:

- `fixed_6h`: dips to ~0 every 6 h, recovers to ~0.55 at each fix.
- `fixed_12h`: dips to ~0 every 12 h, leaves ~6 h dead between fixes
  → `time_with_cov` only 61–65%.
- `post_event_30m_12h`: dips between events; with redep72h, events
  arrive often enough across the patrol band to maintain surfacings;
  without redep, the post-drift collapse leaves long dead windows
  (`time_with_cov` drops to 64%).

The original assumption that drifters would have a "half-life of
useful coverage" governed by station-keeping drift over a week was
wrong at this σ_threshold. The σ_pos growth between fixes dominates
much faster than positional drift across the patrol bbox does. A
week of station-keeping degradation only matters once the surfacing
cadence is fast enough that σ_pos is well-controlled — which our
runs show is essentially `fixed_6h` and `post_event` (with redep).

### 4. `fixed_12h` is operationally weak regardless of cadence

Both `fixed_12h` cells (no-redep and redep72h) bottom out around
cov_mean ~0.12–0.15 and ~9–11% reconstruction. The 12h inter-fix
window is simply too long for σ_pos to stay below 500 m most of the
time. Redeployment doesn't fix this — it's a surfacing-cadence
problem, not a drift problem.

### 5. Pareto positioning — drifter economics

Plotting coverage vs deployment count:

```
 0.35 |                                            ● post_event × redep72h (48 deploys)
 0.30 |           ● fixed_6h × redep72h (48 deploys)
 0.25 |    ○ fixed_6h × no-redep (16 deploys)
 0.20 |
 0.15 |    ○ post_event × no-redep (16 deploys)        ● fixed_12h × redep72h (48 deploys)
      |    ○ fixed_12h × no-redep (16 deploys)
 0.10 +---|---------------|---------------|
        16              32              48     drifter-deploys / week
```

Two takeaways:

- Adding 32 extra drifter-deploys/week (16 → 48) on `post_event`
  more than doubles coverage (0.156 → 0.349). On `fixed_6h` it's a
  modest bump (0.256 → 0.305).
- For the same 48-deploys/week budget, `post_event × redep72h`
  beats `fixed_6h × redep72h` on coverage AND uses ~half the
  surfacings — it's strictly better on every dimension we measured.
- `fixed_12h` × either cadence is dominated; remove it from the
  candidate set.

### 6. Architectural recommendations

For continuous 1-week patrol-band monitoring with the D6_empirical
drop pattern at this site:

1. **Use `post_event_30m_12h` surfacing.** The event-driven structure
   gives substantially better σ_event quality (80 m vs 200+ m) and
   lower fleet surfacing count, which is the dominant power
   consumer.
2. **Redeploy every 72 h.** It's not a small effect for `post_event`
   (+124% coverage); it's the difference between the policy
   working and collapsing. Even at +200% drifter cost (16 → 48
   deploys/week), the coverage payoff plus quality improvement
   makes this clearly worth it for any monitoring application
   where the drifter recovery + redeploy logistics are tractable.
3. **Don't bother with `fixed_12h`.** No combination of drop pattern
   + cadence in this iteration makes it competitive with the other
   policies.
4. **`fixed_6h` × no-redep is the cost-floor option** — coverage of
   0.256 with only 16 deploys/week. Acceptable for less stringent
   monitoring; falls behind `post_event × redep72h` on quality.

## Verification & seeding caveat

The redep72h vs no-redep cells use *paired event seeds* per policy
(seed 5000 for `fixed_6h`, 5100 for `fixed_12h`, 5200 for
`post_event`), so the comparisons within each policy row are on
identical event sets. This required adding
`FLEET_SWEEP_POLICY_INDEX_OFFSET` to the sweep driver so a partial-
policy run could align its seed indexing with a previous full-policy
run; the no-redep run was launched with offset=1 to match the
redep72h run's seeding.

A first attempt at the no-redep run used the default offset=0,
producing different events for the same policy across cadences —
that data was unusable for paired comparison and was discarded. The
final paired numbers above show modest (~+1.5pp) cross-cadence
deltas for `fixed_12h` and large (~+7pp) cross-cadence deltas for
`post_event` and `fixed_6h`.

## Known gaps

- **Drop pattern was optimized against a 72h single-deployment
  σ_event objective**, which matches the redep72h regime exactly
  (each fresh cycle is 72h). For no-redep cells, the drops are
  72h-optimal but run for 168h — the optimizer didn't penalize
  day-7 drift. This is a slight bias against the no-redep numbers,
  but it's hard to imagine a 168h-optimized pattern being
  qualitatively different given that drifters can't be made to
  station-keep tightly past day ~3 anyway.
- **Only 72h-redep was tested.** 24h or 48h might further improve
  outcomes (especially for `fixed_12h` which still has cadence-
  driven coverage gaps), but at proportional drifter cost. A
  follow-up sweep with 48h vs 72h would tell us whether redep
  cadence has an interior optimum.
- **Mode-a / mode-b σ_event posteriors are all NaN in the per-cell
  summary.** The reconstruction error numbers are real, but the
  σ_p50 calibration column is NaN for every cell — likely a NaN
  contamination in the LSQ JᵀWJ inversion under the long-mission
  large-σ_pos regime, propagating through the posterior covariance.
  Worth investigating before relying on calibration metrics from
  these runs.
- **The dominant `post_event × redep72h` result depends on the
  `30m post_event delay + 12h cap` parameterization being correct
  for this site/event-rate.** Lower event rates or longer post-event
  delays would erode the coverage advantage. A sensitivity sweep on
  these two knobs is the natural follow-up.
- **Each redeployment cycle is a fresh PF/bias-Kalman state.**
  No belief-state carryover across cycles. Field-learning across
  cycles (which would matter most for the bias posterior) is a
  future-iteration item.
- **No `fixed_2h` arm.** De-scoped because prior runs showed bad
  station-keeping behavior, but a `post_event_30m_2h_cap` variant
  (event-driven with a tighter safety cap) was not explored — could
  combine the surfacing-quality of `post_event` with the
  σ-tightening cadence of `fixed_2h`.

## Phase 4 (sk-authority sensitivity) deferred

Per discussion before launching the sweep, the planned controller-
knob sensitivity sweep was dropped from this iteration:

- The proposed knobs (`available_depths_m`, `surface_dwell_h`,
  `control_cadence_sec`) are tuning parameters of the existing MPC
  depth controller, not architectural alternatives. The codebase
  already has first-class controller variants (`StationKeeper`,
  `TrajectoryStationKeeper`, `MPCStationKeeper`, `DragKeeper`,
  `GliderKeeper`) but only `MPCStationKeeper` and
  `TrajectoryStationKeeper` are wired into `run_one_station`.
- The meaningful first-class comparisons would be `MPCStationKeeper`
  with `mpc_horizon_n=12 → 24` (probably needs site-selection
  re-sweep to fully exploit), or wiring a true MPC variant of
  `GliderKeeper` (substantial new code combining beam-search
  rollouts with glide actuation). Both are larger workstreams than
  fits in this iteration.
- This iteration's findings already show that for this site +
  drop-pattern + event-rate regime, the surfacing-policy +
  redeployment-cadence pairing dominates the design-space question.
  Further controller-authority work should be motivated by a clear
  story about what coverage target the policy + cadence axis can't
  reach, and Phase 3's results don't yet identify such a target
  (the `post_event × redep72h` cell is already at 99%
  time_with_cov).

## Future-session items

1. **Adaptive redeployment** — trigger redeploy when median sk
   crosses a threshold rather than at fixed 72h cadence.
2. **Optimized re-drop placement** — re-run the drop optimizer with
   a multi-cycle objective (mean σ_event over 0–72h fresh + 72h
   post-drift, etc.) and compare to the single-cycle-optimal
   pattern used here.
3. **Drifter-economics optimizer** — search over (n_drifters,
   redeploy_cadence, drop_pattern) for minimum total drifter-
   deployments meeting a coverage target. Builds on this run.
4. **NaN σ_event diagnosis** — track down why the per-cell σ_p50
   summary is NaN for all cells; likely a degenerate JᵀWJ inversion
   under high-σ_pos conditions.
5. **Lower-event-rate sensitivity** — `post_event` × redep72h's
   dominance depends on events arriving often enough to keep
   drifters surfacing; explore the 0.1–1.0 event/h regime where
   real-deployment IUU patrol rates likely live.
