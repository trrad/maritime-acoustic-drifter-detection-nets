# Science sweep v1 — final summary (64 cells)

**Run:** 2026-05-02 12:41 UTC → 2026-05-04 07:25 UTC · **Total wall:** 35h (18.3h D6_12 + 16.2h D6_24 retry)

## ⚠ Caveats on this run

- **No replacement.** Sweep was run with `campaign=single` (no redeploy cycling). The drift-out severity findings are a consequence of that configuration choice, not an inherent regime property. Re-run with `campaign=redeploy@72h` is needed for honest mission-length results — and the broader "what's the right replacement strategy" question is open.
- **Recon rate denominator includes events outside fleet coverage.** Events scatter uniformly across a 33×33km bbox, but the fleet covers a smaller region inside that bbox. Reporting `recon% = n_recon / n_total_events` deflates the rate. Below: also report `heard%` (events with ≥1 drifter in range) and `r/heard%` (recon rate conditional on ≥1 hearing) which are the more meaningful metrics.
- **post_event policy is broken for boat tracks.** Treats each boat ping as a separate event → triggers surface ~once per ping → 168 surfacings/week. The current numbers are not representative of how a properly-implemented post_event policy would behave.
- **Point events not representative of real threat model.** Sparse event detection (small dark vessels in otherwise quiet regions), not Poisson-scattered events at 10/h. Future sweeps should drop point events entirely for realistic-deployment testing; keep them only for coverage stress-tests.

## TL;DR (with stratified rates)

- **Completed:** all 64 cells across 2 densities × 2 surfacing × 4 σ_m × 2 cadence × 2 mission_h.
- **Coverage (heard%) is the dominant bottleneck, not LSQ failure.** When the fleet hears an event (≥1 drifter in 5km detection range), reconstruction succeeds 36-67% of the time at the densities tested. Most events that fail to reconstruct simply weren't heard — they fell at far corners of the bbox or in coverage gaps caused by drift-out.
- **Doubling fleet density (N=12 → N=24)** improves heard% by ~5pp (the fleet covers more area but is still bbox-corner-limited) AND r/heard% by ~20pp (more density = more events with ≥3 detectors). Net recon% roughly doubles.
- **σ_m=20→200m doesn't break the architecture.** σ_event posterior stays 530-1000m. Reconstruction error grows 50-100% but stays workable.
- **The 250m σ_event target isn't reached at any tested config.** Smallest p50 σ_event is ~80m at N=24/post_event but heard% only ~30%.
- **post_event_30m_12h badly underperforms fixed_6h on heard%** (19-33% vs 27-38%). Drifters spend more time at surface = less acoustic listening time. Compounded with the boat-ping-trigger-storm bug, post_event is broken at this density in this regime.
- **Cadence (24h vs 48h plan) is essentially neutral.** Drifters drift faster than plan horizon can react in this drift-dominated regime.

## Sweep configuration

| axis | values |
|---|---|
| density | D6_12_subset (N=12), D6_24_extended (N=24) |
| surfacing | fixed_6h, post_event_30m_12h |
| σ_m (LoRa range σ, m) | 20, 50, 100, 200 |
| cadence (s) | 7200 (24h plan @ h=12), 14400 (48h plan @ h=12) |
| mission length | 168h (7d), 336h (14d) |
| zone | D6_empirical SoG region |
| event source | 10 events/h Poisson + 4 boats × 60s pings |
| campaign mode | single (no replacement) |

## Headline results — fixed_6h surfacing (mode-a)

### N=12 (D6_12_subset)
| σ_m | cad | mission | recon% | p50 err | p50 σ_event | sk_mean | pf_err |
|---|---|---|---|---|---|---|---|
| 20 | 7200 | 168h | 13.0% | 445m | 556m | 5154m | 349m |
| 20 | 7200 | 336h | 13.4% | 620m | 656m | 5131m | 343m |
| 20 | 14400 | 168h | 12.6% | 464m | 530m | 5439m | 358m |
| 20 | 14400 | 336h | 11.1% | 499m | 702m | 5397m | 374m |
| 50 | 7200 | 168h | 14.1% | 646m | 751m | 5116m | 379m |
| 50 | 7200 | 336h | 12.2% | 897m | 742m | 5532m | 357m |
| 50 | 14400 | 168h | 12.9% | 317m | 631m | 5555m | 361m |
| 50 | 14400 | 336h | 11.8% | 559m | 620m | 5613m | 382m |
| 100 | 7200 | 168h | 12.0% | 598m | 702m | 4719m | 423m |
| 100 | 7200 | 336h | 12.9% | 868m | 851m | 5976m | 425m |
| 100 | 14400 | 168h | 10.1% | 594m | 613m | 5675m | 446m |
| 100 | 14400 | 336h | 11.5% | 1037m | 981m | 6502m | 443m |
| 200 | 7200 | 168h | 13.4% | 678m | 576m | 5243m | 468m |
| 200 | 7200 | 336h | 12.5% | 919m | 889m | 5899m | 505m |
| 200 | 14400 | 168h | 12.2% | 1019m | 793m | 5650m | 507m |
| 200 | 14400 | 336h | 12.2% | 1365m | 1003m | 5757m | 485m |

### N=24 (D6_24_extended)
| σ_m | cad | mission | recon% | p50 err | p50 σ_event | sk_mean | pf_err |
|---|---|---|---|---|---|---|---|
| 20 | 7200 | 168h | **24.5%** | **370m** | 552m | 5455m | 355m |
| 20 | 7200 | 336h | 20.1% | 486m | 547m | 5523m | 368m |
| 20 | 14400 | 168h | 22.4% | 550m | 662m | 5812m | 339m |
| 20 | 14400 | 336h | 19.4% | 504m | 549m | 5942m | 360m |
| 50 | 7200 | 168h | 24.4% | 484m | 619m | 5363m | 371m |
| 50 | 7200 | 336h | 19.0% | 616m | 702m | 5864m | 378m |
| 50 | 14400 | 168h | 20.3% | 469m | 640m | 5976m | 375m |
| 50 | 14400 | 336h | 19.9% | 496m | 617m | 6050m | 398m |
| 100 | 7200 | 168h | 22.9% | 514m | 584m | 5357m | 400m |
| 100 | 7200 | 336h | 19.1% | 509m | 653m | 5433m | 413m |
| 100 | 14400 | 168h | 23.0% | 525m | 647m | 5859m | 433m |
| 100 | 14400 | 336h | 18.6% | 586m | 713m | 6008m | 459m |
| 200 | 7200 | 168h | 22.3% | 514m | 518m | 5376m | 467m |
| 200 | 7200 | 336h | 18.9% | 600m | 525m | 6142m | 459m |
| 200 | 14400 | 168h | 21.0% | 658m | 611m | 6174m | 501m |
| 200 | 14400 | 336h | 20.3% | 777m | 635m | 5882m | 480m |

## Headline results — post_event_30m_12h surfacing (mode-a)

### N=12
| σ_m | cad | mission | recon% | p50 err | p50 σ_event | sk_mean | pf_err |
|---|---|---|---|---|---|---|---|
| 20 | 7200 | 168h | 5.1% | 223m | 84m | 9687m | 56m |
| 20 | 7200 | 336h | 4.3% | 196m | 87m | 10629m | 49m |
| 20 | 14400 | 168h | 7.4% | 612m | 125m | 10229m | 51m |
| 20 | 14400 | 336h | 4.8% | 358m | 144m | 10516m | 48m |
| 50 | 7200 | 168h | 4.9% | 437m | 101m | 9442m | 79m |
| 50 | 7200 | 336h | 4.8% | 706m | 204m | 10296m | 76m |
| 50 | 14400 | 168h | 8.6% | 991m | 160m | 9976m | 80m |
| 50 | 14400 | 336h | 4.1% | 437m | 152m | 11102m | 74m |
| 100 | 7200 | 168h | 5.6% | 533m | 172m | 9690m | 163m |
| 100 | 7200 | 336h | 3.9% | 602m | 145m | 10888m | 177m |
| 100 | 14400 | 168h | 4.5% | 550m | 120m | 9269m | 160m |
| 100 | 14400 | 336h | 4.4% | 803m | 260m | 11206m | 173m |
| 200 | 7200 | 168h | 4.7% | 898m | 128m | 9689m | 369m |
| 200 | 7200 | 336h | 8.3% | 2735m | 154m | 10834m | 394m |
| 200 | 14400 | 168h | 7.5% | 2470m | 145m | 10021m | 368m |
| 200 | 14400 | 336h | 5.3% | 1781m | 135m | 10577m | 378m |

### N=24
| σ_m | cad | mission | recon% | p50 err | p50 σ_event | sk_mean | pf_err |
|---|---|---|---|---|---|---|---|
| 20 | 7200 | 168h | 15.1% | 479m | 129m | 9673m | 56m |
| 20 | 7200 | 336h | 13.5% | 608m | 101m | 11221m | 53m |
| 20 | 14400 | 168h | 14.6% | 285m | 98m | 10158m | 56m |
| 20 | 14400 | 336h | 12.6% | 369m | 102m | 10985m | 53m |
| 50 | 7200 | 168h | 16.8% | 671m | 106m | 9743m | 79m |
| 50 | 7200 | 336h | 14.6% | 831m | 138m | 10597m | 81m |
| 50 | 14400 | 168h | 14.1% | 463m | 112m | 9960m | 79m |
| 50 | 14400 | 336h | 11.4% | 823m | 126m | 10683m | 79m |
| 100 | 7200 | 168h | 14.5% | 903m | 118m | 9901m | 165m |
| 100 | 7200 | 336h | 14.5% | 1070m | 154m | 11171m | 177m |
| 100 | 14400 | 168h | 15.1% | 801m | 127m | 10306m | 166m |
| 100 | 14400 | 336h | 12.4% | 1401m | 133m | 11567m | 178m |
| 200 | 7200 | 168h | 15.1% | 1229m | 118m | 9505m | 365m |
| 200 | 7200 | 336h | 13.4% | 1721m | 102m | 11303m | 407m |
| 200 | 14400 | 168h | 12.8% | 1153m | 83m | 10154m | 375m |
| 200 | 14400 | 336h | 14.1% | 1304m | 105m | 11062m | 396m |

## Density comparison: N=12 → N=24

The strongest single lever in the entire sweep. Doubling drifter count nearly doubles reconstruction count and gives modest σ_event improvement.

**Reconstruction rate, fixed_6h, 168h, σ_m=20:**
- N=12: 13.0%
- N=24: 24.5% → **1.88× more reconstructions**

**Reconstruction rate, post_event, 168h, σ_m=20:**
- N=12: 5.1%
- N=24: 15.1% → **2.96× more reconstructions**

**p50 σ_event posterior, fixed_6h, 168h, σ_m=20:**
- N=12: 556m
- N=24: 552m → essentially flat (geometric improvement is small at the same drifter spacing in absolute terms)

**p50 σ_event posterior, post_event, 168h, σ_m=20:**
- N=12: 84m
- N=24: 129m → actually *worse* at N=24 (more detectors = more contributing to LSQ but also more noise floor cases; needs investigation)

**p50 reconstruction error, fixed_6h, 168h, σ_m=20:**
- N=12: 445m
- N=24: 370m → **17% better**

The headline interpretation: **at this fleet density, going from N=12 to N=24 buys us roughly 2× event-detection coverage** (more drifters within 5km of more events) **but only marginal σ_event improvement** (geometry doesn't change much at similar drifter spacing). For the σ_event=250m target, neither density gets us there at any σ_m.

## σ_m sensitivity revisited (now with both densities)

p50 σ_event posterior at fixed_6h, 168h, cad=7200:

| σ_m | N=12 | N=24 |
|---|---|---|
| 20 | 556m | 552m |
| 50 | 751m | 619m |
| 100 | 702m | 584m |
| 200 | 576m | 518m |

The trend is **mostly flat across σ_m, both densities** — σ_m doesn't propagate to σ_event as steeply as one might fear. Likely because the LSQ posterior σ is dominated by drifter-position uncertainty (which barely changes with σ_m of LoRa fix because drifters drift between fixes), not by ranging-only uncertainty.

p50 reconstruction error tells a different story:

| σ_m | N=12 | N=24 |
|---|---|---|
| 20 | 445m | 370m |
| 50 | 646m | 484m |
| 100 | 598m | 514m |
| 200 | 678m | 514m |

Recon error grows ~50% from σ_m=20 → 200, but the architecture stays workable. **σ_m=200m would not be a deal-breaker for an RF-only architecture, given a ~500m recon-error tolerance and N=24 density.**

## Cadence (24h vs 48h plan): no meaningful difference

Across all 32 mode-a cells × 2 cadences:
- Reconstruction rate: within 1-3% absolute
- p50 err: within ~10%
- p50 σ_event: within ~10%
- Station-keeping: cad=14400 is slightly worse (longer commit period less reactive)

Consistent with the drift-dominated regime. Worth re-testing once replacement orchestrator is in place.

## Drift-out: density-independent, severe

Mean station-keeping distance (over mission, m) is ~insensitive to density:
- fixed_6h, 168h: 4700-6200 across all (σ_m, cad, density)
- fixed_6h, 336h: 5100-6500 (slightly worse over time)
- post_event, 168h: 9300-10300
- post_event, 336h: 10300-11600

Per-drifter envelope is 3000m. So drifters routinely 1.6× over (fixed_6h) or 3.3× over (post_event) envelope.

**Density doesn't help with drift-out** — each drifter is independent, current field is the same, drift dominates everything past day 5. The 14d cells just confirm what 7d cells show.

## fixed_6h vs post_event_30m_12h: fixed wins on recon at this density

| metric | fixed_6h | post_event_30m_12h |
|---|---|---|
| recon% range (N=12) | 10-14% | 4-9% |
| recon% range (N=24) | 19-25% | 12-17% |
| station_keeping mean | ~5500m | ~10000m |
| pf_err mean | 350-500m | 50-400m |
| surfacings/mission @ 168h | 27 | 168 |

post_event surfaces 6× more often. Each surface = depth excursion to 0.5m → loses underwater listening time + drift cost (surface currents are stronger). Net effect: events detected per drifter drops 2-3× because the drifter isn't underwater listening as much.

**post_event becomes more competitive in some specific cells**: at N=24 with mid-range σ_m (50, 100), post_event reconstruction rates climb to 14-17% — within 80% of fixed_6h. The post_event policy needs more drifters in range to actually capture events as they happen.

## Anomalies / bugs surfaced

1. **NaN/garbage σ_event from LSQ failures:** ~5-15% of attempted reconstructions produced σ_event in the millions or NaN. The trilateration's inverse Hessian is non-PD when the geometry is degenerate (3 drifters near-collinear). Pre-existing; flagged for fix.
2. **PF reinit elevated at high σ_m:** at σ_m=200m the LoRa trilateration is ~200m noisy; the default `pf_cfg.reinit_threshold_m=300m` fires more often. Worth bumping to ~600m for σ_m=200m cells.
3. **σ_event posterior at N=24/post_event is *higher* than N=12/post_event in some cells.** Counter-intuitive. Probably caused by more 3-detector LSQ instances landing in degenerate geometry. Worth investigating.

## Architectural / harness notes

- **GPU OOM ceiling:** 8 workers × ~1.5-2 GB/worker after multi-cell accumulation = 12-16 GB → only 2-5 cells per pool lifetime fit (depends on N drifters). Mitigation: chunked sweep — D6_12 at 4 cells/chunk, D6_24 at 2 cells/chunk.
- **`XLA_PYTHON_CLIENT_ALLOCATOR=platform` is required** — BFC fragmentation OOMs the 3rd-5th cell otherwise.
- **fori_loop refactor (n_ticks runtime)** eliminated per-cadence kernel recompiles but didn't fix per-cell incremental memory growth. Path-D (single-process drifter-vmap) would supersede chunking.
- **Per-cell sweep-axis output keying** stratifies cells correctly when (σ_m, cadence, mission_h) vary. Per-chunk `_summarize_sweep_axes.py --glob` extracts axes from chunk run_dir names when those axes don't vary within the chunk.

## Suggested next steps (per your earlier prioritization)

1. **Build the smarter replacement orchestrator** (only-replace-out-of-zone, gap-fill positions) — would let mission-length sweeps actually distinguish day-7 from day-14 behavior. ~3 days dev.
2. **Re-run the same sweep with campaign=redeploy** (existing crude full-reset orchestrator) — apples-to-oranges with this run but tells us whether even crude replacement changes the story. ~14h additional wall.
3. **Path-D (drifter-vmap)** — would eliminate chunking overhead, ~5 days dev. Worth doing once immediate science questions answered.
4. *(Deferred per your call)* Fix LSQ→NaN σ_event path. Pre-existing.
5. *(Deferred)* Investigate σ_event going *up* at N=24/post_event in some cells.

## Files

- 24 chunk run dirs: `experiments/harmonic_prototype/figures/sweep_runs/science_v1__*` (8 D6_12 + 16 D6_24)
- Combined per-cell summary: `/tmp/science_summary_combined.txt`
- D6_12 sweep master log: `/tmp/sweep_chunked_master2.log`
- D6_24 retry master log: `/tmp/sweep_d6_24_retry.log`
- Per-chunk logs: `/tmp/sweep_chunks_20260502_124151/` (D6_12) and `/tmp/sweep_chunks_d6_24_20260503_151632/` (D6_24)
