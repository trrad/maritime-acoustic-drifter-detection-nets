# Noise model design — maritime prototype

**Status:** working document, v1 (2026-04-24).
**Supersedes:** the σ_fc=20 cm/s depth-independent-Gaussian noise model used
in Phase-1 experiments (scripts 21–22). This document records why that
model was physically wrong on two axes, what replaces it, and the
sources that justify each number. All claims here are tagged with
confidence levels; every numeric threshold traces to a cited paper or
an explicit "no literature found — defensible estimate" flag.

## 1. The design question

We simulate a passive ballast-controlled drifter in the Strait of
Georgia. The "truth" is the SalishSeaCast NEMO hindcast (UBC Allen
group, `ubcSSg3DuGridFields1hV21-11` on ERDDAP). An operator deploying
a real node would have a forecast/hindcast with some RMSE vs. reality;
we model that gap as additive noise on top of truth. Two design
choices completely set how the controller and bias-learning PF behave:

- **Horizontal amplitude** of the noise — σ_forecast.
- **Vertical structure** — how the noise at different depths relates.

Both were wrong in Phase 1: amplitude too large (20 cm/s sourced from
a Norwegian-shelf aggregate, not mid-basin SoG), and structure
independent-per-depth (each depth's noise drawn separately). The
second was a silent bug — vertical shear of the simulator's truth was
scrambled at σ-comparable magnitudes because the noise itself
contributed an O(σ) random shear at every (lat, lon, t). The
controller's "authority" was then a mix of real NEMO shear and
artifact-shear from independent draws, dominated by the latter at
σ=20.

## 2. Horizontal amplitude

**Recommendation: σ_fc = 8 cm/s RMS for central-SoG basin nominal;
sweep 12–15 cm/s for plume-adjacent / wind-event sensitivity.**

### Evidence

| Source | Value | Regime | Confidence |
|---|---|---|---|
| Halverson & Pawlowicz 2016, *Atmosphere-Ocean* (paper read locally) | Surface stdev **17 cm/s (u), 21 cm/s (v)** at VENUS Central; domain variance ellipse semi-major **19-45 cm/s, avg 26 cm/s**; mean flow 5 cm/s domain-wide (14 cm/s near river mouth); depth-averaged stdev **6.2 cm/s (u), 14.8 cm/s (v)**; CODAR random-error floor "up to 10 cm/s" | Central SoG, HF radar 2012-2013, plume-adjacent + basin interior | **High** — direct observational study, long time series, our primary calibration anchor |
| Yang et al. 2020 PNNL-30448 | RMSE 20 cm/s, 80% < 30 cm/s, long tail to 67 cm/s | Puget Sound / SJDF / SJI, 135 ADCP stations, **tidal hindcast** (no wind/freshet forcing) | High — Salish Sea direct, dense station coverage |
| Idžanović et al. 2023, *Front. Mar. Sci.* | RMSE 20.3 cm/s +24h → 20.6 cm/s +66h; bias 2.6 cm/s; spatial correlation ~0.5 | Norwegian shelf, Barents-2.5 EPS, **full operational forecast** w/ wind | High for the analog but not SoG-specific |
| SalishSeaCast VENUS comparison (UBC Allen lab docs) | M2 subsurface currents 11–27% weak (central) / 12% strong (east), ellipse "too circular" | SoG deep-basin (35–290 m / 20–160 m), tidal-only | Medium — qualitative + VENUS-node specific |
| Halverson, Gower, Pawlowicz 2018 DFO TR 319 | SCT/Microstar drifter vs 25 MHz CODAR radial velocities | SoG surface, 2013-2015 | High — direct SoG observation/model-analog gap |

**How to read the three direct numbers.** Yang 2020 and Idžanović 2023
both land at ~20 cm/s RMSE, but these are model-vs-observation *total*
error numbers:

- Yang 2020 is across 135 Puget Sound ADCPs, dominated by narrow
  passes (Deception, San Juan Channel) where tidal currents are
  fastest. The 0.02 m/s station-averaged bias / 0.92 R are encouraging
  but 80% within 30 cm/s *includes* tidal-rip tails that don't apply
  to the mid-basin deployment region.
- Idžanović 2023 is Norwegian shelf/coast — structurally comparable
  (regional 2.5 km model, wind + tides + coastal currents) but not SoG.

**Halverson 2016 gives the primary mid-basin observational anchor.** The
paper is not a forecast-error study but characterizes the total
variability a forecast must attempt to capture: surface stdev 17-21
cm/s at VENUS Central (mean ~5 cm/s, so stdev dominates), depth-
averaged stdev 6-15 cm/s. A well-tuned NEMO hindcast that captures
tides plus the subtidal wind-and-freshet response would leave residual
error in the **8-12 cm/s RMS range at surface, 3-6 cm/s depth-averaged**.
σ=8 cm/s surface is the lower end of this (well-tuned-hindcast
midpoint); σ=12-15 cm/s is realistic for a production forecast with
imperfect wind forcing.

**Canonical sweep range: σ ∈ {8, 12, 15} cm/s.** The Phase-2 sweep
should span this full range, not a single point. σ=20 is the
Norwegian-shelf / Puget-Sound-tidal-pass regime and is not applicable
to mid-basin SoG operation.

### Pending gaps

- **No published central-SoG-specific σ vs. depth curve**; the
  calibration is inferred from Halverson 2016 total-variability
  observations plus an assumption about what fraction a well-tuned
  hindcast captures. A dedicated calibration against a DFO/ODL
  drifter program with paired SalishSeaCast-forecast archive would
  pin this down directly (flagged for future measurement).
- **Winter vs summer** σ differs because the dominant error source
  shifts from Fraser plume (summer) to wind-slab response (winter) —
  see §3 below. Current implementation uses a single σ; seasonal
  variation is a future knob.
- **Transfer to non-Salish Canadian deployment regions** (Great Bear
  Sea, Atlantic shelf-break, Arctic ice-free summer) is tracked in
  `docs/reference/regional_transfer_notes.md`. Headline findings
  summarised in §7 below.

## 3. Vertical + component structure

**Recommendation: five independent additive components, each keyed to
a distinct physical mechanism, each with its own (σ, horizontal scale,
temporal scale, vertical profile).**

```
u_err(x, y, z, t) = ε_coh(x, y, t)                        — depth-coherent
                  + ε_plume(x, y, t) · tanh_off(z)         — buoyant slab
                  + ε_submeso(x, y, t) · exp(-z / L_z_s)   — submeso + Ekman
                  + ε_inertial(x, y, z, t)                 — rotating at f
                  + ε_white(x, y, z, t)                    — unlearnable small-scale

tanh_off(z) := 0.5 · (1 - tanh((z - base) / width))
f at 49°N    := 2 · Ω · sin(49°) ≈ 1.05e-4 rad/s, period T_f ≈ 16.5 h
```

### Why five components, not three

The previous 3-component design (`ε_coh + ε_surf · exp(-z/L_z) +
ε_white`) lumped plume + wind-slab + submesoscale + near-inertial
into one `ε_surf` with one L_z. The 2026-04-24 domain-practitioner
review (`noise_model_boundary_review_2026-04-24.md` §"Severity 1 —
physical misspecification") objected on three grounds:

1. **Plume is a slab with a sharp halocline base, not an exponential
   tail.** `exp(-z / 15 m)` puts ~50% of plume-scale error at z=10 m,
   where the real plume is gone and the drifter is in return flow. A
   drifter's 2 m and 8 m ballast positions see qualitatively different
   water in reality but identical error structure under an exponential
   lumping.
2. **Near-inertial oscillations are missing.** At 49°N the inertial
   period is 16.5 h; a wind burst injects a surface-trapped rotating
   signal of ~5–10 cm/s amplitude persisting 1–2 days. A stationary
   Gaussian cannot represent rotation. For a 72 h station-keeping sim
   this mode is at exactly the PF's learning-window timescale.
3. **Correlation scales differ across mechanisms.** Plume fronts are
   1–3 km cross-front (narrower than basin-scale barotropic residual);
   submesoscale eddies ~5–10 km; wind events coherent across ~20 km.
   Lumping them onto one σ_s averages plume-edge errors out across
   the 25 km bbox instead of keeping them localised.

All three objections trace to citable primary sources (Kastner 2018;
Mahadevan 2016; Ekman-layer theory). The 5-component decomposition
fixes each of them and remains tractable: each component is an
independent draw from a stationary Gaussian random field with its
own (σ, σ_s, σ_t); the `inertial` component needs two amplitude
fields plus a deterministic rotation at f; the sampler sums them
with the right vertical profile at query time.

### Component table (central-SoG April nominal, σ_fc = 8 cm/s reference)

| Component | σ (cm/s) | σ_spatial | σ_temporal | Vertical profile | Physical mechanism / reference |
|---|---|---|---|---|---|
| `coh` | **4.0** | 5 km | 36 h | 1 (depth-coherent) | Barotropic + baroclinic-tide residual; estuarine exchange mismatch. Halverson 2016 reports depth-averaged u-stdev 6 cm/s, v-stdev 15 cm/s — a well-tuned hindcast captures tides, residual ≈ 4 cm/s in CODAR comparisons. |
| `plume` | **2.0** | 2 km | 24 h | `0.5·(1 − tanh((z−5)/2))` | Fraser plume slab. Kastner 2018: thickness 0.5–6 m (SE winds) to 10 m (NW winds); surface velocity 1.5–2.5 m/s at mouth, drops to ~10% by the 21-psu isohaline. April value small (pre-freshet); July peak freshet triples this. |
| `submeso_wind` | **5.0** | 5 km | 12 h | `exp(-z/20)` | Submesoscale eddies + fronts (Capet 2008: horizontal O(10) km, vertical O(10) m, τ ~ 1 day; Mahadevan 2016: vertical trapping = mixed-layer depth) + Ekman wind-slab (e-fold = turbulent Ekman depth ≈ 0.4·u*/f ≈ 10–20 m). L_z = 20 m matches April winter-deep ML. |
| `inertial` | **4.0** | 20 km | 24 h (amplitude) | `exp(-z/20)` | Near-inertial wind response, rotating clockwise at f = 2Ω sin(49°), period 16.5 h. Implemented as two independent stationary amplitude fields (c₁, c₂) with `u = c₁·cos(ft) + c₂·sin(ft)`, `v = −c₁·sin(ft) + c₂·cos(ft)` — stationary variance, isotropic rotation. Spatial scale matches wind-event coherence (~20 km). |
| `white` | **1.5** | 1 km | 3 h | 1 | Unlearnable small-scale residual absorbing unresolved shear; the only truly unlearnable component in the model. |

**Totals:** surface per-component RMS = √(4² + 2² + 5² + 4² + 1.5²) =
**7.95 cm/s** — matches the 8 cm/s mid-basin anchor from §2. Deep
floor (z → ∞, surface-trapped components decay out) = √(4² + 1.5²) =
**4.27 cm/s**. The deep floor is what the controller cannot escape
by diving; the plan's "dive below L_z to escape surface error" only
buys you the difference ≈ 3.7 cm/s of learnable amplitude.

### Why this matters for the controller

The controller's authority depends on how much surface-trapped error
it can escape by choosing a deeper depth. Under the Phase 1
depth-independent model, escape was zero (noise at every depth,
scrambling the truth's vertical shear). Under the 3-component
depth-coherent-plus-surf model the escape was one number (the
difference between 0–L_z surf and the coh floor). Under the
5-component model the escape is layered:

- Below plume base (~z = 7–9 m past the tanh transition): lose the
  plume error entirely. Controller avoiding the plume slab buys back
  the full σ_plume.
- Below L_z_surf ≈ 20 m: submeso + inertial decay by e⁻¹ ≈ 37% of
  surface. Controller dropping to ~40 m has ~14% of surface
  submeso+inertial error remaining.
- Only `coh + white` ≈ 4.3 cm/s per component survives below ~50 m.

The surface-intensified escape is **mechanism-specific**, not a
single exponential — a stratified drifter has physically-distinct
routes to escape plume vs. submeso vs. inertial error, which the
3-component model collapsed into one.

### Seasonal variation (future knob)

April is pre-freshet; values above are calibrated for that regime
and for σ_fc_ref = 8 cm/s. Seasonal adjustments from the literature
(to be applied when sim data extends beyond April):

- **July peak freshet:** σ_plume → ~5 cm/s (Halverson & Pawlowicz
  2008 / Kastner 2018 plume area peaks 1000–1500 km²; plume-edge
  velocities 10–30 cm/s); σ_submeso slight bump; plume base may
  shallow to 3 m under SE winds.
- **Winter (Oct–Mar):** σ_plume → 0 (low flow, plume absent in
  central basin); σ_submeso+wind up from storm frequency; σ_inertial
  up from wind-event density; L_z_surf deepens to 25–30 m (winter ML
  deeper than summer since there's no freshet-capped shallow layer).
- **Spring-to-summer transition (May–June):** plume onset is abrupt;
  the bias learner seeing a flat-in-time prior will mis-calibrate.
  Flagged as a seasonally-conditioned bias prior in §6.

### Pending gaps

- **Vertical error-covariance of SalishSeaCast forecasts is not
  directly published.** L_z values are inferred from the underlying
  physics (Ekman theory, Capet/Mahadevan ML trapping). A dedicated
  study against a DFO drifter-plus-forecast paired archive would
  validate these.
- **Internal-tide vertical structure** — mode-1 sign-flip near the
  pycnocline would reverse the sign of the error between surface and
  deep. Not modelled; small in mid-basin (Wang, Pawlowicz 2019,
  remote-sensing amplitudes < 10 m in summer central basin). Flagged
  as a known-omitted mode.
- **Horizontal anisotropy of submeso fronts** (along-strait ≫
  cross-strait) is not captured by an isotropic Gaussian σ_s. The
  domain review called this out specifically (plume fronts narrow
  cross-front, broader along-front). Fixable by replacing the
  isotropic Gaussian filter with an anisotropic one on the plume and
  submeso components; deferred to v2 of the layered physics.
- **Inertial amplitude intermittency.** Real near-inertial response
  is driven by discrete wind events (storms) and is heavily
  non-Gaussian — long quiescent periods punctuated by bursts. The
  current implementation uses a stationary Gaussian amplitude, which
  under-weights extremes. Fixable by amplitude-from-wind-residual
  conditioning; deferred.

## 4. Temporal + horizontal scales

Covered in §3's component table. Summary of the rationale:

- **Slow components (coh, plume, submeso_wind, inertial amplitude):**
  τ = 12–36 h, σ_s = 2–20 km. These are the learnable modes — a PF
  observing surface displacements at 6 h cadence over a 72 h mission
  accumulates enough samples to recover their slow structure.
- **Fast component (white):** τ = 3 h, σ_s = 1 km. Shorter than the
  sampling cadence → unlearnable → contributes to the Kalman
  observation-noise budget.
- **Inertial frequency is not a "correlation time" — it's deterministic
  rotation at f.** The amplitude's τ = 24 h is the timescale over which
  the rotating-vector's magnitude and phase decorrelate.

Idžanović 2023 shows forecast-error growth approximately flat from
+24 h to +66 h on the Norwegian shelf — implies τ ≫ 42 h for the
large-scale forecast-residual modes. Our σ_t values are conservative
(faster decorrelation ⇒ smaller integrated drift); more aggressive
values would weaken the PF's learnable signal.

## 5. Implementation status

- **M1 (this doc's target) is SHIPPED** in `submesoscale.py`:
  - `build_layered_noise_field(ds, bbox_lats_grid, bbox_lons_grid, *,
    sigma_coh_ms, sigma_plume_ms, sigma_submeso_ms, sigma_inertial_ms,
    sigma_white_ms, plume_base_m, plume_width_m, L_z_surf_m,
    L_z_inertial_m, ..., seed)` — five-component layered field with
    physically-grounded vertical profiles.
  - Boundary handling: **option B** (pad with independent white
    noise by 3σ on each axis, filter, crop to original bbox extent,
    renormalise). Eliminates the `mode="nearest"` boundary
    amplification characterised in the 2026-04-24 review — interior
    statistics match a stationary filtered field to kernel-
    truncation tolerance (~1 %).
  - Unit test: `_diag_layered_noise_rms.py` confirms surface
    per-component RMS = 8.0 cm/s (design 7.95) and exp/tanh vertical
    profile shape, sampled across the 8-station × 72 h sim operating
    region.

- **Legacy `build_submesoscale_field` + `build_multiscale_noise_field`
  retained unchanged** for scripts 14 and 18–21. They use the
  original `mode="nearest"` global-RMS construction; the boundary
  artefact is a known property of that code path and does not affect
  their published outputs (those experiments were calibrated against
  it).

- **`22_rbpf_v2_bias_learning.py` is wired to the M1 layered field.**
  The canonical Phase-2 sweep spans **σ ∈ {0, 8, 12, 15} cm/s** × 5
  seeds × 8 stations × 4 policies × {no_learn, grid} (plus
  grid+ctd after M2). All 5 component σ's scale uniformly with σ_fc.

- **Next milestone (M2):** add the CTD sensor; `plume` component
  becomes directly observable via salinity residual (see
  `ctd_sensor_model.md`).

## 7. Regional extensions (Canadian deployment)

Full analysis in `docs/reference/regional_transfer_notes.md`.
Headline findings that affect how to configure the Salish model for
non-Salish Canadian operation:

- **CIOPS-W, Vancouver Island shelf + JdF approaches**: Beutel & Allen
  2024 gives direct validation **RMSE = 8 cm/s** top-200 m at a mid-
  shelf ONC mooring. The Salish σ transfers cleanly here.
- **BC outer shelf decorrelation scales differ**: Cummins 2025 gives
  plume-jet eddy scale **L = 8–12 km** (vs our L_slow = 5 km) and
  wind-response lag **3–4 days** (vs our τ_slow = 36 h). Consider
  L_slow ≈ 10 km and τ_slow ≈ 72 h for BC outer shelf configurations.
- **CIOPS-E shelf interior**: Brickman & Drozdowski 2012 CANOPA modal
  error ~2 cm/s, 90% in [−15, +27] cm/s across the Maritime shelf
  supports σ ~ 8 cm/s open shelf with fatter tails than Salish. Use
  σ ∈ {8, 12, 15} sweep; assign frontal-zone stations (Labrador
  Current, St. Lawrence plume, ice edge) to the high end.
- **Arctic ice-free summer**: no published surface-current RMSE found
  for Beaufort / Baffin / Hudson Bay summer ice-free. LAB60
  overestimates West Greenland Current by ~50% on the boundary.
  **Operational deployment in Arctic regions requires fresh
  validation** before trusting σ = 8 cm/s. Rough σ-multiplier guess
  1.5–2.5× pending measurement.

## 8. Open research questions

These should drive the next round of either sim or field work:

1. **Seasonal switching** — what's the right σ_fc / L_z schedule by
   month? Freshet onset (late May) and SE wind-storm season (Oct–Mar)
   likely swap dominant error mode between plume and wind-slab.
2. **Event-triggered structure** — under plume excursions or storm
   events, σ_fc can spike 2–3× briefly. A bias-learner with a flat
   prior over time can't adapt; a seasonally-conditioned or HRDPS-
   residual-conditioned prior would.
3. **T/S observation as error-mode identifier** — see
   `ctd_sensor_model.md`. A drifter's measured salinity residual vs.
   SalishSeaCast-predicted salinity is a direct indicator of the
   plume-offset error mode; this is the CTD integration next on the
   roadmap.

## References (downloaded copies in this directory)

- Capet, X., McWilliams, J.C., Molemaker, M.J., & Shchepetkin, A.F.
  (2008). Mesoscale to submesoscale transition in the California
  Current System. Part III: Energy balance and flux. *J. Phys.
  Oceanogr.*, 38, 2256–2269. DOI 10.1175/2008JPO3810.1.
  File: `2008_capet_submesoscale_california_current_part3.pdf`.
- Halverson, M.J., & Pawlowicz, R. (2008). Estuarine forcing of a
  river plume by river flow and tides. *J. Geophys. Res.-Oceans*,
  113, C09033. DOI 10.1029/2008JC004844.
  File: `2008_halverson_fraser_plume_estuarine_forcing.pdf`.
- Halverson, M.J., Gower, J., & Pawlowicz, R. (2018). Comparison of
  drifting buoy velocities to HF radar radial velocities from the
  ONC Strait of Georgia 25 MHz CODAR array. *Canadian Technical
  Report of Hydrography and Ocean Sciences* No. 319, DFO, Sidney BC.
  ISBN 9780660236186.
  File: `2018_halverson_codar_drifter_strait_of_georgia.pdf`.
- Idžanović, M., Rikardsen, E.S.U., & Röhrs, J. (2023). Forecast
  uncertainty and ensemble spread in surface currents from a regional
  ocean model. *Frontiers in Marine Science*, 10, 1177337.
  DOI 10.3389/fmars.2023.1177337.
  File: `2023_idzanovic_barents_eps_surface_currents.pdf`.
- Mahadevan, A. (2016). The impact of submesoscale physics on primary
  productivity of plankton. *Annual Review of Marine Science*, 8,
  161–184. DOI 10.1146/annurev-marine-010814-015912.
  File: `2016_mahadevan_submesoscale_primary_productivity.pdf`.
- Oldford, G., Jarníková, T., Christensen, V., & Dunphy, M. (2025).
  HOTSSea v1: a NEMO-based physical hindcast of the Salish Sea
  (1980–2018). *Geosci. Model Dev.*, 18, 211–237.
  DOI 10.5194/gmd-18-211-2025.
  File: `2025_oldford_hotssea_salish_hindcast.pdf`.
- Yang, Z., Wang, T., Branch, R., & Xiao, Z. (2020). Validation of
  the High-Resolution Salish Sea Tidal Hydrodynamic Model. PNNL-30448.
  https://www.osti.gov/biblio/1776702.
  File: `2020_yang_pnnl_salish_tidal_validation.pdf`.

**Now read locally** (moved from abstract-only status 2026-04-24):

- Halverson, M.J., & Pawlowicz, R. (2016). Tide, wind, and river
  forcing of the surface currents in the Fraser River plume.
  *Atmosphere-Ocean*, 54(2), 131–152. DOI
  10.1080/07055900.2016.1138927.
  File: `references/halverson2016.pdf`. **Primary calibration anchor.**
- Kastner, S., Horner-Devine, A.R., & Thomson, J. (2018). Influence
  of wind and waves on spreading and mixing in the Fraser River
  plume. *JGR-Oceans*, 123(9), 6818–6840. DOI 10.1029/2018JC013765.
  File: `references/kastner2018.pdf`. Plume vertical-structure numbers
  used in §3.
- Soontiens, N., & Allen, S.E. (2017). Modelling sensitivities to
  mixing and advection in a sill-basin estuarine system. *Ocean
  Modelling*, 112, 17–32. DOI 10.1016/j.ocemod.2017.02.008.
  File: `references/soontiens2017.pdf`. **Note**: this is a T/S /
  deep-water-renewal sensitivity paper, not a current-velocity
  validation paper. Its salinity and temperature bias numbers belong
  in `ctd_sensor_model.md`, not here. Listed for provenance.

**Still paywalled, cited-from-abstract-only:**

- Pawlowicz, R., Di Costanzo, R., Halverson, M.J., Devred, E., &
  Johannessen, S.C. (2017). Advection, surface area, and sediment
  load of the Fraser River plume under variable wind and river
  forcing. *Atmosphere-Ocean*, 55(4–5), 293–313. DOI
  10.1080/07055900.2017.1389689.
- Pawlowicz, R. (2020). Wind waves in the Strait of Georgia.
  *Atmosphere-Ocean*. DOI 10.1080/07055900.2020.1735989.
