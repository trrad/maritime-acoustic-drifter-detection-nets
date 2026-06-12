# Smart-redeploy v1 — implementation status (2026-05-04)

Implementation status of the `jiggly-cuddling-wilkes` plan.

## What's landed

### 1. SoG site-survey grid scan
`experiments/harmonic_prototype/_diag_sog_site_survey.py`

Grid-scan diagnostic: per-site `b_uncontrolled[d]` + `b_truth_perf_traj`
across the SoG bbox at configurable spacing (default 3000 m), swept
over a surfacing-cadence axis (default `{fixed_8h, fixed_24h}`).
Outputs ranked CSV, distribution histogram + spatial map (the headline
worst/middle/best characterization), and paste-ready `D7_skopt_<N>`
density-config snippets. Run:

```bash
SURVEY_SPACING_M=3000 SURVEY_CADENCES_H=8,24 SURVEY_TOP_N=16 \
  uv run --with numpy --with scipy --with matplotlib --with filterpy \
  python experiments/harmonic_prototype/_diag_sog_site_survey.py
```

Defers: per-cadence mobility maps for the optimizer; perfect-controller
ceiling diagnostic.

### 2. post_event track-divergence
`experiments/harmonic_prototype/rbpf_prototype/surfacing.py`

Replaces the naive "every ping schedules a surface" semantics with
per-source track-divergence:

- Per-source state snapshot: position + estimated velocity at the
  most recent surfacing.
- New ping → project from snapshot to ping time → residual.
  - Residual ≤ `track_divergence_threshold_m` (default 500 m): drop
    the ping (track is still consistent with previously-exfiltrated
    info).
  - Residual > threshold: schedule a fresh surface in
    `post_event_delay_min`.
- Novel sources (no snapshot yet) trigger immediately.

Replaces the original "every-ping schedules a surface, hold-down 30 min"
proposal — the hold-down doesn't reduce surface fraction during a
continuous boat track because surface dwell + descent cycle the drifter
back to listening just in time for the next ping. Track-divergence
fires once per "boat behavior change", which is the right semantic.

`AcousticEvent.src` is now propagated through `EventInfo.src`.

### 3. Fixed-anchor v1
`experiments/harmonic_prototype/_fleet_sim_v0.py`,
`experiments/harmonic_prototype/rbpf_prototype/sensors.py`,
`experiments/harmonic_prototype/rbpf_prototype/experiment.py`

Magic per-drifter anchors → **shared fixed-buoy set per density-config**:

- `DensityConfig.anchors` field; default = `fs.DEFAULT_FIXED_ANCHORS`
  (4 buoys at edge+center of the SoG bbox).
- `LoRaRangeSensor`: σ_m default 20m → **100m** (realistic over-sea
  multipath); `max_range_m=20_000` LoRa LOS gating; out-of-range
  anchors return NaN ranges.
- `trilaterate_lora` returns **(lat, lon, σ_at_fix_m)** —
  σ_at_fix = σ_per_anchor × HDOP, derived from the actual anchor
  geometry at the fix. <3 valid ranges → all-NaN return; the experiment
  loop skips the fix entirely (PF σ_pos grows naturally → drives the
  high-σ_pos replacement trigger).
- σ_at_fix threaded into:
  - PF reinit σ: `max(reinit_sigma, tri_sigma)`
  - Bias-Kalman observation noise budget
    (`_compute_sigma_obs_per_particle` accepts `sigma_lora_end_m_override`
    — replaces the magic-anchor era 20m hard-code).
- `LoRaRangeSensor.log_likelihood_per_particle` skips NaN observations.

Smoke-tested at multiple bbox positions: σ_at_fix calibration matches
empirical Monte-Carlo σ_pos within 6–19% (target was within 10%; the
19% miss is at center where σ_at_fix is only 71m so the 13m absolute
miss is below noise). Edge positions correctly produce NaN fixes
(<3 in-range anchors).

### 4. Trajectory predictor for runtime placement
`experiments/harmonic_prototype/_drifter_mobility_map.py`

Code already supported `--grid-spacing-m 500`; usage block updated with
the SoG-bbox 1000 m / 500 m commands. **Currently building**: 1000 m
spacing, 3 seeds, full SoG bbox, fixed_6h, anchor v1 stack
(`tag=sog_bbox_1000m_anchorv1`, ~3.5h wall).

### 5. Out-of-zone + sustained-σ_pos triggers
`experiments/harmonic_prototype/_fleet_sweep_v0.py`

Per-drifter trigger evaluation at each cycle boundary:
- **out_of_zone**: drifter (lat, lon) at cycle end exits
  `FLEET_SWEEP_ZONE_BBOX` (default = full SoG bbox).
- **high_sigma**: median PF posterior σ_pos over the last
  `FLEET_SWEEP_SIGMA_SUSTAINED_H` hours of the cycle (default 6h)
  exceeds `FLEET_SWEEP_SIGMA_THRESHOLD_M` (default 500m).

Flagged drifters → next cycle deployed at original station target
(v1 fallback; the optimizer integration in #6 is the v1.5 upgrade).
Unflagged drifters → next cycle inherits the drifter's physical
end-of-cycle position (= "ship leaves it in place"). PF/bias state
itself does NOT carry over in v1 (PF serialization is a follow-up).

Trigger metadata persisted in the per-drifter results dict
(`redeploy_summaries`, `redeploy_triggers`, `redeploy_targets_used`)
so downstream analyzers can correlate flagged cycles vs recon
performance.

### 6. Optimizer fixed-existing-fleet mode
`experiments/harmonic_prototype/_drop_point_optimizer.py`

`optimize_replacements(existing_fleet, n_replacements, ...)` Python API:
greedy + refinement placement of `n_replacements` new drifters with
`existing_fleet` positions held fixed. Greedy seeds with existing fleet
then adds N more; refinement perturbs only the new drifters (existing
positions are operationally fixed — the ship can't move drifters that
aren't being replaced). Verified: parity with from-scratch greedy when
`existing_fleet=[]`; correctly worsens objective by 1.2% when 2/4
drifters are pinned vs. unconstrained 4-drifter optimization.

### 7. End-to-end re-sweep — partial

The orchestrator wiring is in place (smart-redeploy triggers + fixed
anchor + track-divergence + boat-track-only by default + new
optimizer API). Remaining limitation:

**Fleet-level optimizer integration is deferred.** Current architecture
runs each drifter's campaign independently in its own pool worker;
`optimize_replacements` requires the surviving-fleet positions at the
cycle boundary, which means a barrier across all per-drifter workers
+ a coordinator that runs the optimizer + dispatches the next cycle's
station targets. This is a substantial refactor of `_run_one_config`
and is the v1.5 upgrade. v1 ships with the "redeploy at original
station" fallback when flagged.

## Recommended end-to-end re-sweep

```bash
# Smart-redeploy + fixed-anchor v1 + track-divergence + boat-track-only
# at the canonical D6_empirical / fixed_6h / σ_m=100m / 168h cell.
cd experiments/harmonic_prototype && \
  FLEET_SWEEP_RUN_HOURS=168 \
  FLEET_SWEEP_CAMPAIGN_MODE=redeploy \
  FLEET_SWEEP_REDEPLOY_INTERVAL_H=72 \
  FLEET_SWEEP_LORA_SIGMAS_M=100 \
  FLEET_SWEEP_ONLY_DENSITIES=D6_empirical \
  FLEET_SWEEP_ONLY_POLICIES=fixed_6h \
  uv run --with numpy --with scipy --with matplotlib --with filterpy --with pandas \
  python _fleet_sweep_v0.py

# Baseline for comparison (single mode, same anchor v1 stack):
cd experiments/harmonic_prototype && \
  FLEET_SWEEP_RUN_HOURS=168 \
  FLEET_SWEEP_CAMPAIGN_MODE=single \
  FLEET_SWEEP_LORA_SIGMAS_M=100 \
  FLEET_SWEEP_ONLY_DENSITIES=D6_empirical \
  FLEET_SWEEP_ONLY_POLICIES=fixed_6h \
  uv run --with numpy --with scipy --with matplotlib --with filterpy --with pandas \
  python _fleet_sweep_v0.py
```

Expectation: smart-redeploy run shows higher `recon_of_heard%` over
168h vs single-mode (drifters that drift out of zone or develop
sustained high σ_pos get replaced rather than continuing to degrade).
Per-drifter trigger metadata (in the saved results) lets us verify
*how often* triggers fired and whether the fallback redeploys
materially affected fleet geometry.

## Future work (deferred from plan)

- **Fleet-level synchronized cycles** for proper optimizer integration
  (the v1.5 upgrade — replaces the "redeploy at original station"
  fallback with `optimize_replacements(unflagged_fleet, n_flagged)`).
- **PF/bias state serialization** so unflagged drifters truly continue
  with state preserved (currently PF resets each cycle, only the
  physical position carries over).
- **Per-cadence mobility maps** for cells using non-fixed_6h surfacing.
- **Perfect-controller ceiling mobility map** as a diagnostic of
  info-limited handicap on placement quality.
- **500m mobility map upgrade** once 1000m fidelity is shown to be
  insufficient.
- **Range-gating refinement**: dynamic `max_range_m` per environmental
  condition (sea state, antenna height, interference).
