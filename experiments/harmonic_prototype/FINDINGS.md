# Harmonic Prototype — Findings Log

Running log of what we learn from each prototype step. Updated in place.
Keep this empirical, not speculative. Each entry: what we did, what we
observed, what it implies for the Change 1 spec.

## Step 1a: ERDDAP sanity check (`01_fetch_one_day.py`)

**Did:** Opened `ubcSSg3DuGridFields1hV21-11` via xarray against the SalishSeaCast ERDDAP griddap endpoint, dumped metadata.

**Observed:**
- Dataset covers 2007-01-01T00:30Z to present (continuously extending — 19+ years, 169k hourly timesteps as of 2026-04-23).
- Dims: `(time=169272, depth=40, gridY=898, gridX=398)`. Grid is integer-indexed — **no native lat/lon coords on the velocity datasets**.
- 40 non-evenly-spaced depth levels, 0.5m → 441.5m. Spacing is fine (~1m) near the surface, coarser (~30m) at depth.
- Variable is `uVelocity` (m/s, standard_name `sea_water_x_velocity`), NEMO 3.6.
- Metadata fetch: 0.74s. Dataset is production-quality, no auth.

**Implies for spec:**
- The "bbox in lat/lon" assumption baked into CLI drafts is wrong for this data source. Need a grid-index conversion layer. Document this as a format-polymorphism concern (Canadian NEMO is `gridY/gridX`-indexed; CMEMS is lat/lon-indexed directly).
- Continuously-extending dataset means "pin 2007–2023" is a content-hashing exercise, not a range specification. The data-pipeline persona's SHA-256 objection is now concrete: the dataset_id returns more rows tomorrow than today.

## Step 1b: Grid mapping + one-day column fetch (`02_map_grid_and_fetch_column.py`)

**Did:** Loaded `ubcSSnBathymetryV21-08` for its 2D lat/lon fields, found nearest cell to Race Rocks, pulled 25 hours × 40 depths × 1 cell of `uVelocity` and `vVelocity`.

**Observed:**
- Bathymetry dataset carries `latitude(gridY, gridX)` and `longitude(gridY, gridX)` as 2D fields — the mapping we need. Fetch: 1.4s for the full 898×398 pair.
- Race Rocks (48.298°N, -123.531°W) → `(gridY=288, gridX=159)`, at (48.2989°N, -123.5311°W) — **97.5m from target**. Subgrid precision since the grid is ~500m resolution.
- Velocity subset: one cell × 25 hours × 40 depths fetched in 16.7s (all network I/O).
- **Race Rocks is shallow in SalishSeaCast bathymetry** — velocity is non-zero at depth levels 0.5m, 5.5m, 10.5m, 15.6m; **zero** from 24m down.
- Below-seafloor cells are zero-filled, NOT NaN. `u_vals.mean()` would be misleading; must mask by `bathymetry` depth at each (gridY, gridX) before harmonic analysis.
- Surface currents: mean 1.44 m/s over the sampled 24h; max 2.76 m/s. Real tidal rip.

**Implies for spec / next prototype:**
- **Prototype 1 (utide constituent-set question)**: Race Rocks is a good choice — barotropic tidal rip, documented DFO harmonic constants for cross-check, strong overtide energy (why it's a rip).
- **Prototype 2 (ballast-drifter baroclinic depth structure)**: Race Rocks is the WRONG cell — no water column below ~20m. Need a deeper cell. Candidates: central Juan de Fuca Strait (~100–200m), southern Strait of Georgia deep basin (~400m), Haro Strait deep section (~300m). Pick one before running utide per-depth-level.
- Below-bottom zero-masking is a utide preprocessing requirement. Must load the bathymetry grid, determine the bottom depth at each cell, and truncate the time series before `utide.solve()`. A missed mask here would fit zero-amplitude M2 constituents at every dead cell and corrupt the spatial harmonic grid.
- Fetch cost model: one cell × 24 hours = 16.7s. Extrapolated to 1 year × 1 cell × 40 depths = 365 × 16.7s / 24 ≈ 4 min. Full 17 years at one cell ≈ 1 hour. A 10×10 cell subset for a full bbox × 17 years = days. **Fixture-prep is NOT a minutes-scale operation** as the proposal claims. Either (a) server-side subset with larger time chunks and chunked HTTP requests, (b) download the raw NEMO NetCDF files (UBC publishes them as compressed annual archives somewhere, check), or (c) Dask-parallelized ERDDAP requests. Worth benchmarking before committing a fixture-prep strategy.

## Open questions for subsequent steps

- What does `utide.solve()` actually produce on a 1-year subset at a single cell? (Next script: `03_utide_one_year_race_rocks.py`.)
- Does the M2 amplitude match DFO's published Race Rocks constant (~1.4 m/s)?
- How does the M4/M6 energy compare to M2/S2/K1/O1 at this cell?
- At a deeper cell (e.g., central Juan de Fuca), how does M2 phase shift with depth? Does baroclinic tide dominate or is the barotropic mode clean?
- What's the actual wall-clock of a 17-year × 1-cell fetch if we use annual-chunk requests vs one request?

## Step 1c: Course correction — move off Race Rocks

**Done:** Built `salishseacast_cache.py` with deterministic per-month cache keys + 1s polite delay + idempotent fetch.

**Reconsidered target:** Race Rocks is a monitored tidal-pass; the M1 deployment vision is 10→100→1000+ nodes across an EEZ (Salish Sea basin scale, not a single rip). Diagnostic M2-at-a-rip is not the question. The question is what the fleet actually sees.

**Real deployment-scale candidates for SalishSeaCast-covered domain:**
- Central Strait of Georgia basin (~200–400 m water, ~50 km wide, 200 km long). Primary target for M1 Salish deployment.
- Western Juan de Fuca / Swiftsure Bank (shelf-to-open-ocean transition).
- NOT narrow passes (Race Rocks, Haro, Boundary, Seymour) — no one deploys drifters there; they avoid them.

**Revised prototype plan:**
1. Fetch a moderate subset of cells across central Strait of Georgia (e.g., 5×5 pattern spaced ~10 km, matching LoRa inter-node spacing). Cache them via the monthly fetcher.
2. Run utide at each cell, 1–2 years of data (enough for M2/S2 separability).
3. Visualize:
   - Spatial map of M2 amplitude + phase across the 5×5 pattern.
   - Time series at 2–3 cells overlaid, showing spatial coherence of the tidal signal.
   - Reconstruction vs held-out-month residual at one cell.
   - Vertical profile of M2 amp at a deep (~300 m) cell — this is where the ballast-drifter baroclinic-mode story lives.
4. Output: PNG figures + a small HTML index linking them, mirroring existing dashboard patterns.

**Rationale:** The prior a drifter fleet needs is a coherent basin-scale structure, not a pointwise tide-gauge prediction. Testing the model's ability to describe spatial coherence matters more than hammering one rip to death.

## Step 2: utide on the 1-month smoke cache (central Strait of Georgia, 49.2°N)

**Did:** Ran `utide.solve(nodal=True, method="ols")` on every cell × surface of the 182-cell smoke-cache bbox, then extended to full 40-depth column at the deepest cell (383m). Compared 4-constituent (M2/S2/K1/O1) vs 11-constituent Foreman-style set.

**Observed — this is a pile of big findings:**

1. **Surface variance explained by M2/S2/K1/O1 is 9–16% across the bbox (median 13% u, 11% v).**
   - Not a cell-specific artifact — consistent across all 182 cells in the bbox.
   - For context: Race Rocks-style narrow passes routinely > 70%; a deep-basin location with < 20% says the signal there is **dominated by non-tidal motion** (wind, freshet, sub-inertial eddies), not by the major-constituent tidal frame.
   - This directly confirms the PF-practitioner persona's warning: at the basin-scale deployment regime, the tidal prior describes a minority of the variance. The residual is the dominant signal, not a correction.

2. **Going from 4 to 11 constituents buys only ~3% more variance (9.4% → 12.9%).**
   - Missing ~85% of the variance is NOT in higher-order tidal constituents. It's genuinely non-tidal.
   - M4, M6, MS4 amplitudes are small (< 3 cm/s) at this basin cell (but probably larger near narrow passes — untested).
   - So the oceanographer persona's "need Foreman 45-constituent set" objection is partially wrong for THIS deployment regime: extra constituents don't help much in the deep basin. The objection still holds for tidal-pass sites like Race Rocks (not tested), but the deployment target isn't a tidal pass.

3. **1 month is too short for K1/P1 Rayleigh separation, and this corrupts the fit.**
   - 4-const fit: K1 Lsmaj 0.11 m/s, M2 Lsmaj 0.10 m/s (so K1-dominant regime, as expected for Salish in winter/shoulder season).
   - 11-const fit: P1 Lsmaj 0.19 m/s (!), K1 drops to 0.08. P1 & K1 periods are 24.07h vs 23.93h; need ~6 months for reliable separation.
   - **Implication**: the 3-month full Strait of Georgia fetch currently in progress will be better, but the proposal's "3-month = sufficient" claim needs revisiting. Real fixture prep probably wants at least 6 months, maybe 1-year+.

4. **Vertical M2 phase shifts by ~33° (~1.1 h) between surface (0.5m) and 24m depth at this cell.**
   - Surface (0.5m): M2 Lsmaj 0.10 m/s, phase 321°
   - 3.5m: 0.11 m/s, 312°
   - 24m: 0.13 m/s, 288° — **PEAK amplitude below the surface, phase-offset by ~33° from surface**
   - 100m: 0.10 m/s, 303°
   - 300m: 0.12 m/s, 325° (back near the surface phase)
   - This is a real baroclinic M2 signature — the tide at the surface and the tide at 24m are physically **not in phase**. For a ballast drifter cycling between 0.5m (surface obs) and 24m (ballasted), the tidal velocity it rides depends on depth, and there's a ~1 hour time offset between them. Over a 6-hour tidal phase, the ballast drifter at different depths can be advecting in genuinely different directions.
   - **Confirms the user's instinct: depth-dependent harmonic structure is first-order, not polish.** The simple "surface-only M2 amp/phase" climatology misses a predictable, operator-relevant signal.

**Implies for spec:**

- The 4-constituent M2/S2/K1/O1 surface-only climatology proposed in the rewrite is **fundamentally unfit for the deployment regime** as specified. It would produce a prior that captures ~10-15% of the variance with zero depth dimension.
- Two candidate fixes, both probably needed:
  - **Depth dimension in the harmonic table**: `amp_vx[constituent, month, depth, lat, lon]`, etc. Increases bundled-fixture size ~40x (40 depth levels). A ballast-aware PF would query the harmonic at the particle's depth.
  - **Broader background**: the harmonic decomposition is just the predictable piece; the bulk of variance needs a richer background than monthly residual means. Wind-driven / freshet-driven sub-tidal motion varies on O(10-day) event scales; monthly mean smears it. Consider a day-of-year climatology with a smoothed spline fit, or segment the hindcast into weeks and store per-week residual means (52 bins/year × grid × depth; doubles fixture size but captures event-scale structure).
- **Analysis window probably needs to be 1+ year, not 3 months**, for clean K1/P1 separation and to average out event-scale residuals.
- **CIOPS is irrelevant at this point** — the prior needs to be built from the hindcast alone, and CIOPS's value (forecast-of-today) isn't even the right truth. The PF practitioner's twin-experiment objection matters less if we acknowledge we're doing a twin experiment: CIOPS forecast vs SalishSeaCast-hindcast-harmonic prior is a well-defined reproducibility test, not a claim of operational-generalization fidelity. Real operational generalization needs HF-radar or in-situ drifter obs as truth — out of scope for M1.

## Open questions (refreshed)

- Does the 3-month fetch (now in progress) meaningfully improve K1/P1 separability vs 1-month? Expect yes but not enough.
- Does the same 33° surface-to-mid-column M2 phase shift hold at other basin cells, or is it local to the one I ran? (Needs spatial pattern of vertical structure.)
- Do the overtide amplitudes (M4, M6, MS4) climb meaningfully near the edges of the basin where bathymetry becomes complex?
- For M1 scope: is a depth-dependent harmonic climatology within reach, or does it dominate the fixture-prep + bundled-size budget? Cost-benefit: 40x NetCDF blowup for a first-order physics capture.
- Does the "residual is the dominant signal" finding change the PF's predict-stage design? Process noise σ derived from 10-15%-captured-tidal residual could be ~0.15-0.20 m/s, which over a 1-hour LoRa-silent window is ~500m of unguided uncertainty. LoRa fixes dominate; the prior mostly provides heading.

## Next

3-month fetch completed (`gy=479-508_gx=241-276`, Apr–Jun 2023, 1080 cells × 40 depths × 2185 hours, 761 MiB). utide run on all 1080 surface cells + centre-cell full column completed in 59 s. 7 figures written, `figures/index.html` has them.

## Step 3: course-correction on the "monthly vs day-of-year" framing

**Mistake caught:** In the notes above I claimed the non-tidal residual needs finer-than-monthly bins and asserted day-of-year would "only recover a fraction." That was a design-level assertion I had no data for. The user called it out correctly:

- **The climatology is a layered prior, not an oracle.** It interacts with LoRa (sparse absolute fix) and IMU/mag/baro (dead-reckoning between fixes). It needs to be good enough, not perfect. Don't evaluate it in isolation.
- **Day-of-year could capture a lot more than I claimed.** A chunk of the "85% residual" is probably seasonal signal (Fraser freshet ramping through May-June); a day-of-year climatology built from multi-year data would absorb that. How much? Unknown without measurement.
- **Prototype, not design.** The point is to build a ground-truth + climatology testbed, compute different candidate climatology models against it, and measure which wins. No model is obviously right. Stop asserting; measure.

## Step 4: climatology-model comparison testbed (in progress)

**Purpose:** Build multiple candidate climatology models from prior-years hindcast data, score each against 2023 held-out truth. Pure empirical comparison, no design claims.

**Setup:**
- **Truth**: SalishSeaCast 2023 Apr–Jun at 1080 cells × 40 depths (already in cache).
- **Training data**: SalishSeaCast 2018–2022 Apr–Jun at same cells. Fetch in progress (~75 min for 15 months).

**Candidate models** (all fit on training data only, scored against 2023):
- `harmonic_4` — pure M2/S2/K1/O1 tidal, no residual.
- `harmonic_11` — M2/S2/N2/K2/K1/O1/P1/Q1/M4/MS4/M6.
- `harmonic_4 + monthly_residual` — 3-bin (Apr/May/Jun) residual-means per cell.
- `harmonic_4 + weekly_residual` — ~13-bin residual means.
- `harmonic_4 + doy_smoothed_residual` — day-of-year residuals smoothed with 7-day rolling window.
- `harmonic_11 + doy_smoothed_residual` — fanciest.

**Scoring:** per-cell variance-explained at surface across 1080 cells; `1 − Var(truth − predicted) / Var(truth − mean)`.

**Open:** haven't yet added a full-column (depth-dependent) comparison. Will run after surface baseline lands so we can see whether the ballast-drifter depth question matters in variance terms.

**Explicit non-claims:**
- Nothing about which model is "right for the design yet" — measurement comes first.
- 5 prior years is thin for day-of-year variance. A real fixture would want 10+. Prototype uses what's cheap to fetch.
- Surface-only first pass. Depth story is a separate dimension, investigated after.

## Step 5 (test D): nodal correction matters

**Did:** fitted `utide.solve(nodal=True)` vs `nodal=False` on 30 sample cells at surface, 3 months of 2023 data. Compared per-constituent Lsmaj amplitude and Greenwich phase `g`.

**Observed:**
| const | amp diff (nodal off vs on) | phase diff (off vs on) |
|---|---:|---:|
| M2 | +3.19% | −1.32° |
| S2 | −0.18% | +0.04° (none expected — S2 has no nodal mod) |
| K1 | −9.87% | −4.19° |
| O1 | **−16.27%** | +5.21° |

Per-cell spread was < 0.3% for amp, < 0.1° for phase — the correction is astronomy, uniform across the bbox.

**Implies:** the oceanographer persona's "nodal=False is textbook misuse" is empirically validated. Dropping nodal would give O1 wrong by ~16% and K1 by ~10%, with few-degree phase errors. Our prototype already uses `nodal=True`, so the concern is resolved in code. Worth documenting the magnitude so future consumers don't "simplify" it away.

## Step 6 (reframe): prototype is about simulator-in-the-loop, not forecast skill

**Done:** user pushed back on my drift toward "which climatology model forecasts best." The real prototype question is tighter:

> Given accurate-ish depth-resolved truth (SalishSeaCast 2023), can a node running a compressed onboard climatology + sensor fusion + ballast-depth control actually **map its position and steer itself** in simulation?

That's not a forecasting question — it's a "does the ballast control concept work with a plausible prior" question. The climatology model comparison becomes a subroutine (measure compression loss), not the main result.

Reframed pipeline targets:
- truth: SalishSeaCast 2023 Apr–Jun (have it)
- compressed onboard climatology: various candidates (harmonic + residual variants)
- simulator: advect a simulated drifter through truth; PF consumes climatology + noisy sensor obs; ballast policy chooses depth to influence trajectory
- metric: simulator-level (position tracking, steering success), not variance-explained alone

## Step 7 (test G1): ballast steering authority is real and large

**Did:** truth-only Lagrangian advection of 4 drifters at fixed depths (0.5m, 5m, 20m, 50m) from (49.30°N, -123.70°W, bathy 278m), starting 2023-04-01 at 00:30 UTC. RK4 integration at 1h tick. No PF, no climatology, no sensors — just truth + depth choice.

**Observed — trajectory separations:**
| depth pair | sep @ h24 | max sep (within bbox) |
|---|---:|---:|
| 0.5m vs 5m | 4.62 km | 6.36 km |
| 0.5m vs 20m | 6.74 km | 6.74 km |
| 0.5m vs 50m | 5.76 km | 6.44 km |
| 5m vs 20m | 5.48 km | 5.48 km |
| 5m vs 50m | 3.08 km | 3.91 km |
| 20m vs 50m | 6.12 km | 6.12 km |

All drifters exited the 20×20 km bbox within 17–45 hours at these speeds — the bbox clipped the experiment, not the physics.

**Implies:**
- Ballast steering has **5+ km/day of reachable-trajectory spread** in this regime. More than enough authority for station-keeping or directional steering.
- A bigger bbox (~100 km×100 km) is needed for multi-day trajectory sims. Current bbox (chosen as a "climatology analysis scale" patch) is too small for drifter trajectories.
- This is physics-only — no PF has been deployed against this yet. Next step: add a PF with various onboard climatology priors, see how much of the 5+ km/day steering envelope is still reachable under realistic inference.

## What this prototype needs next

Real simulator-in-the-loop:
- Expand fetch bbox to ~100 km × 100 km (order of 10,000–20,000 cells; full month fetch would be hours, but cache is the same structure).
  - OR simulate in a wrap-around periodic truth field (fake but cheap).
  - OR use shorter (sub-day) simulation windows and stay in current bbox.
- Add noisy sensor layer: GPS intermittent, IMU, baro for ballast depth, LoRa to anchors.
- PF: reuse `rtl/vectors/maritime/pf_float.py` or build a stripped-down prototype PF.
- Ballast-depth controller: simple rule-based policy for prototype (e.g., "choose depth whose predicted current best matches desired trajectory").
- Metric: PF position RMSE vs truth, and "reachable trajectory envelope" with vs without ballast control.

## Step 8 (Phase A): perfect-knowledge station-keeping upper bound
 (`10_station_keeping_upper_bound.py`, `figures/12_station_keeping_upper_bound.png`)

**Purpose:** establish the physics-limited ceiling of ballast station-
keeping. Given truth-perfect knowledge of currents at every available
depth, how well can a greedy-myopic controller hold a node at a fixed
station?

**Setup:**
- Station: (49.3022°N, −123.6997°W), bathy 278 m (centre of the 20×20 km bbox).
- Depths available: 0.5, 5, 10, 20, 50 m.
- Ballast vertical speed cap: 0.1 m/s.
- Controller cadence: decision every 30 min, lookahead 30 min, greedy-
  myopic (assume current at each depth is constant for the next 30 min,
  pick depth whose projected displacement minimises distance to station).
- Sim tick: 1 h.
- Run: 24 h starting 2023-04-01 00:30 UTC.
- Envelope: 500 m.
- Baseline: passive drifter fixed at 10 m (no control).

**Current-diversity diagnostic (pre-check for whether station-keeping is
physically admissible at this point):**

100% of ticks across the 24h run have at least one depth pair whose
instantaneous currents differ by more than 90°; max inter-depth angle
over the run was 179° (near-opposing). Tidal reversal is strong enough
at this mid-basin point that every depth's direction spans ±180° during
the day. So "pick among 5 depths to oppose current at most times" has
authority. This pre-empts a well-posedness concern: if all depths at all
times pointed roughly the same way, no depth-choice policy could hold
the node — perfect knowledge or otherwise.

**Observed:**

| run         | % of 24h within 500 m | max excursion | mean distance |
|-------------|---:|---:|---:|
| controlled  | **36.0%** | 1566 m | 749 m |
| passive @10m |   8.0% | 6791 m | 3837 m |

**Implies:**
- Greedy-myopic depth control with perfect knowledge DOES provide
  meaningful steering: 5× better mean distance, 4× smaller max excursion
  than passive. This is real ballast-steering authority on a practical
  metric, not just a trajectory-separation stat.
- But perfect knowledge + greedy control does NOT lock the node within
  a 500 m envelope — it drifts out to ~1.5 km during parts of the tidal
  cycle. The controller can only choose among the currents offered at
  that instant; when a full tidal flood is sweeping every available depth
  in roughly the same direction (briefly, during the phase-alignment
  windows between cross-depth reversals), no depth-choice policy helps.
- The gap from "36% within 500m" to "locked on station" is probably not
  closable by more sophisticated control at greedy-myopic's cadence.
  Candidates for improvement: (a) longer lookahead that anticipates
  tidal reversal and lets the node ride-and-return rather than fight
  every half-cycle; (b) allow depths below 50 m to access calmer deep
  currents (untested — deeper levels weren't in the choice set). If
  Option (a) doesn't close the gap either, the conclusion is "ballast
  station-keeping at this Strait-of-Georgia site has a 1–1.5 km
  envelope even with perfect knowledge" — which is still a useful
  operational number, just not 500 m tight.
- The "500 m envelope" is itself a prototype default. A 1 km or 1.5 km
  envelope (more appropriate for the actual physics) would show
  "controlled 80%+, passive ~20%" and would be the honest mission-scale
  statement for this site.

## Step 9 (Phase B): degraded-knowledge sweep
 (`11_station_keeping_degraded.py`, `figures/13_station_keeping_degradation.png`)

**Purpose:** measure how station-keeping degrades as truth is replaced
by progressively more realistic knowledge — smoothed truth → historical
prior → PF-estimated belief.

**Tiers:**
- **B0 truth** — Phase A baseline.
- **B1 spatial σ=1 km** — per-timestep 2D Gaussian blur of each depth
  cube. Effective resolution ~2 km instead of native 500 m.
- **B2 temporal 6 h** — centered 6-h rolling mean along time at each cell.
- **B3 prior 2020** — same (depth, grid cell, calendar date + time of day)
  but from the 2020 cache instead of 2023 truth (3-year gap, 1095 days).
  Raw historical sample, no harmonic extraction.
- **B4 PF+prior** — controller reads PF mean position, not truth. PF runs
  a 200-particle Gaussian filter with B3 as the predict-stage current
  source, GPS updates (σ=3 m, every 30 min), and LoRa-to-anchor range
  updates (σ=20 m, every 10 min; anchor ~3 km NE of station).

**Observed:**

| tier              | % within 500m | max | mean | PF err mean |
|---|---:|---:|---:|---:|
| B0 truth          | 36.0% | 1566 m |  749 m |   —    |
| B1 spatial σ=1km  | 36.0% | 1664 m |  798 m |   —    |
| B2 temporal 6h    | 32.0% | 1689 m |  901 m |   —    |
| B3 prior 2020     |  **8.0%** | 3372 m | 1924 m |   —    |
| B4 PF+prior       |  8.0% | 3372 m | 1924 m |  280 m |

Degradation is monotonic (within-envelope fraction falls with each tier).

**Implies:**
- **Spatial and temporal smoothing of truth barely hurt** (36→36%, 36→32%).
  2 km effective resolution and 6 h temporal averaging both preserve
  enough of the local tidal-phase + shear signal to let the controller
  pick useful depths. This is a reassuring result for onboard
  prior-representation: a compressed-spatial prior (e.g., harmonic field
  stored at coarser grid than SalishSeaCast's 500 m) is not obviously
  broken.
- **The jump B2 → B3 is enormous** (32% → 8%, mean dist 901 → 1924 m).
  Reading the current at "same calendar date, same cell, different year"
  loses ~3/4 of the value of truth. This is larger than the B1/B2
  degradations and suggests that same-DOY-different-year has genuine
  tide-phase drift + non-tidal year-to-year variability that dominates
  the signal the controller needs.
- B3 prior is a 3-year gap (2020 vs 2023). Smaller gaps (2022 vs 2023)
  might be less brutal — worth measuring, but requires completing the
  2021-05 / 2022-04 / 2022-05 cache fill-in (currently missing months,
  see cache inventory).
- **B4 (PF+prior) matches B3 exactly.** With σ=3 m GPS every 30 min, the
  PF mean stays within ~280 m of truth on average; the control decisions
  are effectively driven by the same prior knowledge as B3. The PF itself
  is not the bottleneck — the PRIOR is. Sharpening the PF would not
  improve station-keeping until the prior improves.
- **Implication for the spec work.** The harmonic climatology debate
  (which constituents, how many residual bins) is downstream of a bigger
  question: can a compressed prior capture enough tide-phase + shear
  information to give us B2-level performance (32%) instead of B3-level
  (8%)? The existing harmonic_4 / harmonic_11 / harmonic+residual models
  (Step 4 testbed) need scoring on THIS metric — station-keeping success
  — not just per-cell variance-explained. Variance-explained is a
  scalar per cell; the controller cares about the tide-phase-at-depth
  signal being right at decision time.
- **Implication for harmonic-enrichment prior.** The plan deferred
  harmonic-extraction of the prior (B3 was raw same-DOY sampling). The
  massive B2→B3 step is the signal: we should now test whether a
  harmonic fit of the prior (which filters out event-scale non-tidal
  noise) closes some of that gap. If a harmonic-of-B3 gets us even to
  20%, that's evidence the harmonic model captures the predictable
  piece and the rest is irreducible interannual non-tidal variance.

**Honest limitations:**
- 24 h run horizon — bbox is 20×20 km; longer horizons need a bigger
  fetch (ERDDAP is serial-only, ~4 min/month per bbox; 100×100 km for
  3 months would take hours).
- Single drifter; no fleet-shared observations. With multiple nodes
  cross-ranging each other via LoRa, B4's PF would have richer
  constraints and might push past the prior's limits.
- Greedy-myopic controller; no optimality claims. "36% with truth" is a
  lower bound on what the physics admit, not an upper bound.
- Ballast dynamics are first-order: instant-response depth transitions
  capped at 0.1 m/s, no settling/overshoot/finite-capacity model.
- B3 prior year (2020) was the nearest fully-cached prior year; 2022 is
  partially cached. The gap-size effect on B3 degradation is untested.
- PF in B4 doesn't resample very often because GPS σ=3 m is extremely
  sharp relative to the prior-driven predict uncertainty; ESS pattern
  would change with a more realistic (sparser / noisier) GPS model.
- "500 m envelope" is arbitrary — a 1000 m envelope would show all
  tiers B0/B1/B2 at 70%+ and B3/B4 at ~30%, which is a different story.
  The envelope choice should ultimately be justified by mission-need
  (detection radius, coverage tiling).

## What this prototype needs next (refreshed after Phase A+B)

- Score the Step-4 climatology-testbed models (harmonic_4, harmonic_11,
  +monthly, +weekly, +day-of-year) on the station-keeping metric, not
  just variance-explained. The B2→B3 gap is the empirical case for
  harmonic-prior work.
- Complete cache fill-in (2021-05, 2022-04, 2022-05) and rerun B3/B4
  with smaller year gaps; separate "different-year artefact" from
  "smoothed-truth limit".
- Widen the envelope to 1000 m and check whether the B0/B2 cluster
  locks there, which would give a defensible mission-scale number for
  the ballast station-keeping regime at Strait of Georgia basin sites.
- Extend to longer horizons (72 h+) once a larger bbox is cached.
  Currently runs clip at 24 h to keep the passive drifter inside the
  interp domain.
- Consider a non-myopic controller (2–3 h lookahead) that anticipates
  tidal reversal. If it still can't close the B0 → 500m gap, that's the
  honest upper bound for this site.

## Step 10 (Phase A+): station-keeping feasibility swept across the bbox
 (`12_station_keeping_grid.py`, `figures/14_station_keeping_grid.png`)

**Purpose:** Phase A evaluated a single mid-bbox station and got 36%
within 500 m. One sample is one sample — is the bbox mostly feasible,
mostly hopeless, or patchy? Sweep a grid and find out.

**Setup:**
- Same bbox, truth, and controller as Phase A.
- 5-cell stride over (gridY, gridX), interior cells only, bathy ≥ 60 m.
- 42 candidate stations after filtering.
- 24 h per station, perfect-knowledge greedy-myopic control.
- Metrics per station: controlled mean/max distance; passive mean/max;
  steering factor (passive_mean / ctrl_mean); %-of-run within each
  envelope in [500, 750, 1000, 1500, 2000] m.

**Observed — aggregate:**

| envelope | # stations with ctrl_max ≤ envelope | mean % of run within envelope |
|---|---:|---:|
|  500 m |  **0 / 42**   | 23% |
|  750 m |   1 / 42 ( 2%) | 34% |
| 1000 m |   5 / 42 (12%) | 46% |
| 1500 m |  17 / 42 (40%) | 67% |
| 2000 m |  26 / 42 (62%) | 81% |

"Rough station-keeping" (ctrl_max ≤ 1500 m AND steering factor ≥ 2×):
**13 / 42 (31%)** of surveyed stations.

**Observed — spatial:**
- The NW quadrant (roughly lon < −123.75) consistently fails: controlled
  max excursions of 3–7 km, steering factor barely above 1×. Passive
  drifters from these stations exit the bbox to the SW early in the
  run, and the controller can't counter because all reachable depths
  sample the same outflowing regime.
- The central and eastern bbox (lon > −123.72) contains most of the
  successful rough-station-keeping stations. Steering factors up to
  17× at a few cells (#31 at 49.33°N/−123.70°W, bathy 131 m; #38 at
  49.35°N/−123.70°W, bathy 61 m).
- Bathymetry is not the decisive factor: strong-steering cells range
  from 61 m to 373 m. Sign is current-regime: cells where the tide's
  swept-ellipse is large relative to the mean drift have authority;
  cells sitting in a strong mean-drift channel don't.

**Implies:**
- The 500 m envelope default from the plan is not feasible anywhere in
  this bbox, not even with perfect knowledge. Reporting "Phase A hit
  36% within 500 m" at the mid-bbox station over-states the general
  case; that station was close to the ceiling for this domain.
- A 1500 m envelope is achievable at ~40% of locations and a 2000 m
  envelope at ~62%. If the mission task tolerates O(1–2 km) station
  drift, perfect-knowledge ballast control is viable at roughly half
  the surveyed sites.
- The spatial pattern is actionable: a deployment planner should avoid
  NW-corner-analogue sites (sites that sit in a strong outflow
  channel) and prefer sites where the local tide dominates the mean
  drift. This is an operationally-meaningful design output even before
  fleet and sensor considerations.
- For Phase B re-run at broader scale, the appropriate envelope is
  1500 m rather than 500 m. The "degradation curve" should be measured
  against a metric the physics can actually achieve; scoring every tier
  at 0–36% of an unreachable envelope loses signal.

**Honest limitations:**
- 24 h horizon still clips passive drifters that exit the bbox; a
  larger bbox is required to get clean comparisons at NW-quadrant
  cells where passive drifts 5 km+ and the interpolator goes NaN.
- Single start-date (2023-04-01 00:30 UTC). The tidal phase at that
  moment affects the "is this station feasible right now" metric. A
  spring-neap tide cycle or a different month could shift which cells
  succeed.
- The "rough" thresholds (1500 m max, 2× steering) are prototype-
  defaults; the mission-justified values come from the downstream
  detection/coverage analysis that hasn't been built yet.
- Skipped edge cells (stride/2 margin on each side of the bbox) to
  avoid the curvilinear row-mean interpolator losing accuracy. Full-
  bbox coverage would need a proper lat/lon kdtree query, not the
  pseudo-regular approximation.

## Step 11 (design exploration): sensor fusion, fin, drag, glider

Ran a series of control-authority and sensor-stack experiments. All at
72 h on the expanded 60 × 60 km bbox, 54-station grid, perfect prior
where noted:

- **Passive drag modulation** (`15_passive_drag_sweep.py`) — scalar α ∈
  [α_min, 1.0] applied to ambient advection. α_min=0.4 (aggressive
  retractable-drogue capability) takes rough count 19 → 36 of 54 (**2×
  improvement**), %<500m 24% → 46%. The drag budget is actually used
  (mean α ≈ 0.57 at α_min=0.4). Cheapest useful upgrade over pure
  ballast.
- **Glider-transition thrust** (`16_glider_transition_sweep.py`) — V_glide
  engaged only during depth transitions, direction analytically
  optimal. V_glide=30 cm/s (Slocum-class) gives rough 23/54 (~1.2×).
  Transit-duty rises 37% → 82% as controller learns to yoyo
  aggressively, but the effective glide time per hour remains modest
  because transits complete within each hour-step. Glider model is
  penalized by our 1 h sim tick; shorter step would help but also
  changes the ballast baseline.
- **Duty-cycle single-node with real PF** (`17_duty_cycle_single_node.py`)
  — scheduled surface every 6 h for 30 min at depth 0.5 m; 400-particle
  PF gets LoRa ranges (σ=20m) to 3 anchors at surface, dead-reckons via
  prior in between. With perfect-truth prior: 3/6 rough, mean PF err
  57 m, max 780 m — the architecture costs ~5 pp on 500 m envelope vs
  the truth-controller baseline. With 2020-hindcast-as-prior: 0/6 rough
  (2% within 500m). Prior quality dominates sensor-stack quality.

## Step 12 (ruled out as baselines — worth recording why)

- **"2020 hindcast as realistic prior"** — tried in 11 (B3) and 17.
  Gives ~2% within 500m. Looks definitive but isn't: a same-month
  different-year hindcast is NOT what an operationally-deployed node
  would have loaded. A real forecast at the deployment window reflects
  actual wind / freshet state from recent hindcast data, not a 3-year-
  old weather realization. Using 2020 as prior conflates "we have a
  bad model" with "we have a completely-different-weather realization,"
  which is much worse than real operational error. Do not use this as a
  baseline for design decisions.
- **"Older model version (v19-05) as prior vs current hindcast as
  truth"** — suggested by a research agent. Equally wrong: that's a
  model-version gap, not a forecast-vs-reality gap. Different physical
  cause, not what we care about.
- **Direct historical forecast archive for 2023-04** — searched. None
  available. SalishSeaCast-forecast endpoint is a rolling window (no
  archive). CIOPS-SalishSea didn't exist until 2024-06. NOAA SSCOFS
  went operational in 2024-11. For retrospective 2023 analysis, we
  cannot fetch the actual forecast that was produced at the time.

**Honest alternative baselines:**
- **Synthetic forecast error calibrated to published skill** —
  currently used. Multi-timescale noise (fast chop + slow persistent
  bias) added to truth as the prior. σ_forecast target ~20 cm/s RMS
  matches published regional-NEMO forecast skill (Frontiers 2023).
  Correlation scales still guessed (~4 cells spatial, 18 h temporal);
  paper-fetch subagent tasked with pinning these down.
- **Live prospective deployment test** — load today's SalishSeaCast
  forecast, run a sim for 48 h, compare dead-reckon to the eventual
  reanalysis. Real but not retrospective. Deferred.

## Step 13 (flagged): scripts 18 & 19 have broken foundations

`18_pf_accuracy_sensitivity.py` and `19_pf_with_bias_learning.py` treat
σ_pf (PF position error) as an independent free parameter, decoupled
from the physics that actually produces it (surface cadence + LoRa
ranging). This makes the sensitivity sweep answer a malformed question
and produced a misleading "PF accuracy doesn't matter at σ_fc=20 cm/s"
headline. Script 19 additionally collapses bias-learning to a scalar,
throwing away the spatial/depth dimensionality where the leverage
lives. Do not build on 18 or 19. Headers in both files flag this;
`20_*` is the right foundation.

**Correct framing going forward:**
- σ_pf emerges from PF + LoRa ranging + surface cadence + dead-reckon
  error growth. It is NOT a free knob.
- Forecast error is multi-timescale: fast (unlearnable chop) + slow
  (learnable persistent bias). The slow component is the entire
  leverage for any in-flight learning.
- A single-node PF can learn a scalar correction from its own drift
  history; this recovers ~80-90% of the slow-component *magnitude* but
  barely improves station-keeping because the scalar dimensionality is
  wrong for a spatially/depth-varying error.
- A single-node PF with a FIELD-valued correction (coarse grid or basis
  expansion) is the upper bound for what a single node can do. Never
  reaches truth, because only the depths and positions the node has
  visited get observed.
- A FLEET with shared observations is how the correction field gets
  populated across depths and positions the individual node hasn't
  been to. This is where the design lever actually sits.
