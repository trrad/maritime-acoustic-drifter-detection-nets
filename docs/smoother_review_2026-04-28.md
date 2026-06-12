# RTS smoother fitness review (2026-04-28)

## Purpose

Step-back review of the RTS smoother
(`experiments/harmonic_prototype/rbpf_prototype/rts_smoother.py`) before
applying the obvious empirical fix (Q-asymmetry between filter and
smoother). The smoother regresses calibration consistently:
forward 0.78 → smoothed 0.69, while shrinking error 36% but σ only 28%.

The fix is a one-liner. The question is whether the smoother's *shape*
is the right thing to fix, given the deployment metric is σ_event at
acoustic-event timestamps (input to TDOA Gauss-Newton LSQ as
inverse-variance weights).

Six questions asked; verdicts below.

## Q1 — Posterior shape (Gaussian RTS over (mean, cov))

**Verdict.** Gaussian summary is plausibly OK in the fleet-sim regime
(4 stations, 72h, 3-anchor LoRa) but the assumption is not validated
empirically. Bimodality in the PF posterior, if present, would be
collapsed by the (mean, cov) summary — and downstream
`sigma_m = √(0.5·(c[0,0]+c[1,1]))` further isotropises into a scalar,
losing azimuthal anisotropy that the LSQ could use.

**Recommendation.** Don't change the Gaussian shape. Add an
ESS-based bimodal-tick diagnostic at LoRa fixes (flag
`ESS_post < 0.7 · N_particles`). If > 5-10% of fixes are bimodal,
revisit. Otherwise the assumption holds.

## Q2 — TDOA's actual needs

**Verdict.** Scalar σ is fundamentally sufficient. The current LSQ
(`_trilaterate_tdoa` in `_fleet_sim_v0.py:323-406`) doesn't even use
σ as an explicit per-detector weight — drifter positions are treated
deterministically; σ enters only via the detection-range threshold.
The hyperbolic-ambiguity argument doesn't bite because we have ≥3
detectors per event (overdetermined by ≤1 row).

**Recommendation.** Don't change. **Optional**: if outlier events
appear in the fleet sim baseline (events with > 5σ residual despite
good geometry), add per-detector inverse-variance weighting at
`_fleet_sim_v0.py:380` — one line: `J_weighted = J / σ[:, None]`
before lstsq. Wait for baseline to decide.

## Q3 — Online vs offline

**Verdict.** The full-mission RTS smoother is mildly optimistic vs.
real deployment. Real deployment reconstructs events offline at the
**next surface-dwell exfil** (~6h cadence, not 72h), so a fixed-lag
smoother of horizon ~6h is the deployment-honest formulation. The
gap matters most at end-of-leg events (~30-40% lower σ than the
fixed-lag answer); at events near a recent surface dwell the gap
collapses.

**Recommendation.** Add a `max_lag_sec` parameter to
`rts_smooth_trajectory`: when set, the backward pass terminates at
`t = T - max_lag_sec` rather than t=0. Re-run fleet sim with both
full-mission and `max_lag_sec=6h` to quantify the deployment-honest
gap. If the 6h-lag σ_event is unacceptable, the response is
operational (shorter LoRa cadence, faster surface ascent), not
algorithmic. Defer this change until after the v0 fleet baseline
gives us numbers to argue from.

## Q4 — Per-drifter vs fleet-coupled smoother

**Verdict.** Fleet-coupled smoothing is real but *coupled to P4*.
Cleavage:

- **Cheap path** (depends on P4): smoother consumes shared-bias
  state from P4. Each drifter's backward pass uses fleet-aggregated
  bias mean as the drift term; cov benefits from cross-drifter
  precision-weighted bias updates indirectly.
- **Expensive path** (independent of P4): joint backward pass over
  all drifters' beliefs simultaneously, exploiting cross-drifter
  position-error correlation. Requires reformulating the smoother
  math; doesn't compound with P4 cleanly.

**Recommendation.** Don't pursue joint smoothing standalone.
Defer to post-P4 — at that point adding "shared-bias context"
to the smoother is a small extension. If P4 doesn't ship, the
joint-smoother gain is probably not worth the architectural cost.

## Q5 — Linear interpolation on cov between ticks (`query_at_t`)

**Verdict.** Real bug, modest magnitude. `query_at_t` linearly
blends `covs_m[i]` and `covs_m[i+1]` (rts_smoother.py:108). The
inline comment at lines 106-107 explicitly flags the issue: "for
big gaps a more honest answer would forward-evolve cov from t_i
with Q." With 10-min tick interval and ~60% smoothing-induced
shrinkage, mid-interval events see a few-% systematic σ underestimate
— small but in the wrong direction (anti-conservative).

**Recommendation.** Two options, both cheap:

1. **Forward-evolve from previous tick:**
   `c = covs_m[i] + a · Q[i]`, clamped PSD. Honest model evolution.
2. **Conservative envelope:** `c = max(covs_m[i], covs_m[i+1])`
   (elementwise PSD-respecting max, e.g., via eigenvalue clamp).
   Trivially safe, slightly over-conservative.

Option 1 is the correct fix; option 2 is defensible if option 1
breaks PSD in edge cases. Apply once Q-asymmetry (Q6) is decided —
both fixes touch the same per-tick Q.

## Q6 — Q asymmetry (the original concern)

**Verdict.** Real bug, primary suspect for the calibration regression.
The smoother's `_per_tick_Q` (rts_smoother.py:112-132) only adds the
OU growth rate; the docstring at line 119-121 claims this avoids
"double-counting" the bias contribution that's already in
`pf_cov_m[t]`. **The double-counting argument is wrong**: Q should
encode the *increment* from t to t+1, not the cumulative
contribution at t. The filter's actual `pf_cov_m[t+1] - pf_cov_m[t]`
between non-LoRa ticks includes bias-growth and resampling effects
that the smoother's OU-only Q misses.

**Empirical fix considered:** `Q_t = pf_cov_m[t+1] - pf_cov_m[t]`
at non-LoRa ticks. **Failure modes:**

- **PSD violation:** if `t+1` is a LoRa-fix tick, `pf_cov_m[t+1]` is
  the post-update (shrunken) cov, so `Q_t` would be negative.
  Backward gain becomes non-contractive.
- **Degenerate Q at surface dwells:** consecutive LoRa-fix ticks give
  near-zero or negative Q.

**Recommendation.** Hybrid:

1. Compute `Q_t = pf_cov_m[t+1] - pf_cov_m[t]` only at non-LoRa
   transitions (`not lora_fix_mask[t+1]`).
2. At LoRa-boundary transitions, fall back to the existing OU-only
   Q (or set Q=0 — treating the LoRa update as the only change).
3. Clamp PSD via eigenvalue floor on the result.

Then re-measure calibration. If smoother calib approaches forward
calib, Q-asymmetry was the dominant bug. If it doesn't, the
remaining gap is structural (DOF + OU re-inflation, addressed by
P2 τ_OU ablation).

## Decision matrix

| Q | Action | When |
|---|---|---|
| Q1 (Gaussian shape) | Add ESS bimodal-tick diagnostic | Cheap; do anytime |
| Q2 (TDOA weighting) | Wait for baseline; add per-σ weights only if outliers | Conditional on P1 output |
| Q3 (offline → fixed-lag) | Add `max_lag_sec` param to smoother | After P1 baseline |
| Q4 (joint smoother) | Defer to post-P4 | Coupled to shared-bias work |
| Q5 (cov interp) | Forward-evolve via per-tick Q (option 1) | Bundle with Q6 fix |
| Q6 (Q asymmetry) | Hybrid empirical Q at non-LoRa, OU at LoRa-boundary, PSD-clamp | After P1 baseline; primary suspect for calib regression |

## Plan-level conclusion

The smoother shape is fit-for-purpose for the deployment metric. The
calib regression is a Q-mismatch bug (Q6), compounded by a
cov-interpolation bug (Q5). Both are cheap fixes and should be
bundled. **But:** apply them only after the P1 fleet sim baseline
shows σ_event distribution at the current calib ≈ 0.7 — the σ_event
metric is what we're optimising, not single-drifter calib, and
fixing the smoother in a regime where the metric is already adequate
is yak-shaving.

The architectural improvements (Q3 fixed-lag, Q4 fleet-coupled) are
both downstream of bigger questions (P4 shared-bias) and should not
land independently.
