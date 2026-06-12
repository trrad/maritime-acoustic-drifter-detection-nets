# Forecast-error validation notes — Salish Sea / coastal BC

Purpose: calibrate the synthetic forecast-error noise model used by the
maritime prototype against published validation studies for operational
or hindcast ocean-current models in the Salish Sea and regionally
comparable coastal/shallow-strait regimes.

All PDFs listed below live in this directory. Papers noted as
"paywalled, not downloaded" we could not retrieve open-access and
rely on abstract/search-surface text for their numbers (clearly
marked so we don't cite them as if we'd read them).

---

## 2026-04-24 update — recommendations revised for central-SoG regime

The §"Calibration recommendation" section below (σ=20 cm/s, depth-
independent) has been **superseded** by a more careful treatment for
our mid-Strait-of-Georgia basin target regime. See:

- `noise_model_design.md` — current operational recommendation,
  **σ=8 cm/s nominal** (5–15 cm/s regime), **surface-intensified
  vertical structure** (layered σ_coh + σ_surf·exp(-z/L_z) model).
- `ctd_sensor_model.md` — T/S observation model, plume-offset
  physical mode identifier.
- `controller_architecture.md` — controller Tier 0–3 roadmap, the
  convex-hull authority bound.

Key revisions from this doc's original §"Calibration recommendation":

1. **σ=20 cm/s was from Norwegian shelf** (Idžanović 2023), dominated
   by tidal passes. **Central SoG 1–3 day horizon: σ ≈ 5–8 cm/s** per
   Halverson/Pawlowicz lineage, rising to 10–15 cm/s near plume /
   wind-event regimes. The 20 cm/s number was 2–3× too pessimistic
   for our target deployment zone and pushed the controller below its
   noise floor artificially in the Phase 1 experiments.
2. **Depth-independent noise was a physics bug**. Real upper-ocean
   forecast errors are *surface-intensified* — wind-slab, Fraser
   plume, submesoscale eddies all live in the top 10–30 m. Capet
   2008 (*JPO* 38) gives submesoscale vertical scale O(10 m),
   "thinner than the main pycnocline"; Halverson & Pawlowicz 2008
   gives Fraser plume thickness 2–8 m. A depth-coherent ("add the
   same noise to every depth") model is closer to truth than
   per-depth-independent but still too generous at depth — the
   correct model is layered.
3. **New dominant-mode identification**: Fraser plume front
   position is the #1 summer error mode in mid-SoG. Amplitude
   10–30 cm/s *local*, τ ~ 1–2 days, along-strait extent 10–30 km.
   Observable via salinity residual — motivates CTD integration.
4. **Seasonal structure matters**: summer = plume-dominated;
   winter = wind-slab-dominated. Single-σ annual-mean model misses
   this. Future work should condition the noise model on season or
   on HRDPS wind residual.

---

---

## 1. Yang, Wang, Branch, Xiao (2020). PNNL-30448

**Citation.** Yang, Z., Wang, T., Branch, R., & Xiao, Z. (2020).
*Validation of the High-Resolution Salish Sea Tidal Hydrodynamic
Model.* Pacific Northwest National Laboratory, Report PNNL-30448.
https://www.osti.gov/biblio/1776702

- Model: FVCOM, unstructured-grid, ~30–1000 m resolution; driven by
  tides at SJDF and north Georgia Strait + 19 rivers.
- Forms the physical foundation of NOAA's operational SSCOFS.
- **Local PDF:** `2020_yang_pnnl_salish_tidal_validation.pdf` (19 MB)
- **Open access:** Yes (U.S. Government technical report).

### Relevant error metrics (directly quoted)

- "The range of RMSE varies from 0.02 m/s (Station 1622) to 0.67 m/s
  (Station 1545). The SI varies from 0.16 (ST 1735) to 1.74 (Station
  1532). Bias varies from -0.31 (Station 1735) to 0.3 (Station 1701).
  R varies from 0.34 (Station 1532) to 0.99 (Station 1722)."
- "station-averaged values of the RMSE (0.2 m/s), SI (0.5), Bias
  (0.02 m/s), and R (0.92)."
- "80% of the RMSEs are within 0.30 m/s. Three stations — San Juan
  Channel (PUG1703); Deception Pass (PUG1701) and Libby Point
  (PUG1545) — have an RMSE greater than 0.5 m/s."
- Tidal elevation RMSE < 0.2 m; SI < 0.14; R > 0.98 at all 12 tide
  gauges.

### What we get for calibration

- **Headline: RMSE ≈ 20 cm/s depth-averaged principal velocity,
  across 135 ADCP stations** in Puget Sound / SJDF / San Juan
  Islands. Distribution has 80% within 30 cm/s, a long tail (up to
  67 cm/s) at narrow high-current passages (Deception Pass, etc.).
- This is **hindcast** error (model driven by observed tides + rivers,
  no forecast lead time). It's the lower bound — operational forecast
  error will be larger.
- No spatial / temporal correlation scales reported; only point-wise
  statistics at 135 stations.
- No decomposition into tidal / wind / freshet components.
- Bias near zero on average (0.02 m/s), so noise model should be
  zero-mean.

### Applicability

- **High** — Salish Sea direct, high station density, exactly our
  geographic region. Use as the primary anchor for the "resolved
  tidal + tidal-frequency noise" error floor.
- Caveat: FVCOM skill ≠ NEMO skill ≠ SSCOFS skill, and this is
  tides-only forcing (no wind/freshet in the boundary), so it
  **underestimates** real operational-forecast error.

---

## 2. Oldford, Jarníková, Christensen, Dunphy (2025). HOTSSea v1

**Citation.** Oldford, G., Jarníková, T., Christensen, V., & Dunphy, M.
(2025). HOTSSea v1: a NEMO-based physical Hindcast of the Salish Sea
(1980–2018) supporting ecosystem model development. *Geoscientific
Model Development*, 18, 211–237.
https://doi.org/10.5194/gmd-18-211-2025

- Model: NEMO 3.6 (shared lineage with SalishSeaCast), ~1.5 km
  horizontal.
- **Local PDF:** `2025_oldford_hotssea_salish_hindcast.pdf` (10 MB)
- **Open access:** Yes (CC-BY-4.0).

### Relevant metrics

- Paper evaluates **temperature and salinity only** at the
  Salish-Sea-wide scale; explicitly defers current-velocity
  evaluation as future work: *"Once circulation is evaluated, one
  potentially promising application would be to use velocity fields
  from HOTSSea v1..."*
- T bias 0–30 m: −0.39 °C; >150 m: +0.13 °C (Strait of Georgia
  North); Willmott skill 0.97 for SoG North.
- No current RMSE, no spatial/temporal correlation scales for
  currents.

### Applicability

- **Medium** — confirms that the NEMO-based Salish Sea simulation
  family has well-validated thermodynamics and depth-structured
  biases, but does not directly help calibrate the current-noise
  model. Useful context for depth structure of *any* error (surface
  largest, deep smallest).

---

## 3. Idžanović, Rikardsen, Röhrs (2023). Barents-2.5 EPS

**Citation.** Idžanović, M., Rikardsen, E. S. U., & Röhrs, J. (2023).
Forecast uncertainty and ensemble spread in surface currents from a
regional ocean model. *Frontiers in Marine Science*, 10:1177337.
https://doi.org/10.3389/fmars.2023.1177337

- Model: Barents-2.5 EPS (ROMS + CICE via METROMS), 24 ensemble
  members, 2.5 km, 66 h forecasts. Validated against CODAR HF radar
  on the Norwegian coast.
- **Local PDF:** `2023_idzanovic_barents_eps_surface_currents.pdf` (10 MB)
- **Open access:** Yes (CC-BY).

### Relevant metrics (directly quoted)

- "all members show a mean spatial correlation of 0.53, 0.52, and
  0.51 for forecast lead times +24h, +48h, and +66h, respectively."
- "The mean bias is approximately 2.6 cm/s for all lead times over
  all EPS members."
- "MAE and RMSE over all members, they are respectively about
  15.6 cm/s and 20.3 cm/s for +24h, and growing almost linearly
  with time to approximately 15.9 cm/s and 20.6 cm/s for +66h."
- HF radar observation noise itself: "Accuracy of the radar-derived
  velocities has been shown to be typically in the range of 3–12
  cm/s."
- Wind-forcing resolution matters: members forced by 2.5 km
  AROME-Arctic winds had **lower** RMSE/MAE than members forced
  by 10 km IFS winds — "high-resolution wind forcing... provides
  better forecast skill in currents." Quantitative Δ not tabulated.
- The authors observe that the **ensemble spread is roughly
  maintained across the 66 h forecast** (rank histograms do not
  significantly differ between +24h and +66h).
- Decomposition commentary (qualitative): in coastal domains,
  predictability comes from *coastlines, bathymetry, and
  wind-driven currents* plus tides; mesoscale currents have
  very limited predictability without assimilation. Citing Khade
  et al. (2017): "forecast uncertainties in coastal regions in
  the Gulf of Mexico are dominated by wind forcing rather than
  by initial conditions."

### What we get for calibration

- **Headline: RMSE = 20.3 cm/s at +24 h → 20.6 cm/s at +66 h**,
  against HF-radar surface currents; bias ~2.6 cm/s; spatial
  correlation ~0.5.
- Error growth with lead time is **nearly flat** over 24–66 h (~0.3
  cm/s added over 2 days). This is the single most important number
  we've found: it says the *forecast-error decorrelation timescale
  on 24–66 h horizons is long* — the error doesn't randomize within
  a 2–3 day window.
- Observation error stotal ≈ 3–12 cm/s (random, per cell) sets a
  floor. Actual model-vs-truth error is obtained by subtracting
  this in quadrature from the 20.3 cm/s number ⇒ model-true error
  ≈ sqrt(20.3² − 8²) ≈ 18.7 cm/s (using mid-range obs noise).

### Applicability

- **Medium-high** — regional analog, not Salish Sea. Norwegian
  coastal regime (Norwegian Coastal Current, tides, wind-driven,
  strait/shelf topography) is structurally comparable to the
  Salish Sea shelf + inlets. Same class of operational model
  (2.5 km regional ROMS vs 1.5 km NEMO for SalishSeaCast;
  CIOPS-West is 1/36° ≈ 1.5 km at these latitudes).
- This is the **closest published recipe for a forecast-error
  noise model** we found — exactly the "RMS + correlation +
  lead-time growth" decomposition we want, just in a different
  ocean.

---

## 4. Cummins, Blanken, Hannah (2022). Hecate Strait HF radar / CIOPS-W

**Citation.** Cummins, P. F., Blanken, H., & Hannah, C. G. (2022).
HF Radar Observations of Wintertime Surface Currents Over Hecate
Strait, British Columbia. *Atmosphere-Ocean*, 60(5).
https://doi.org/10.1080/07055900.2022.2068995

- DFO/Institute of Ocean Sciences CODAR HF radar on 5 km grid vs.
  **CIOPS-West v1.5** surface currents.
- **Local PDF:** *not downloaded* — Taylor & Francis paywalled;
  `WebFetch` returned 403 on the full-text and PDF URLs; no
  open-access preprint or ResearchGate full-text found in a
  time-boxed search.

### What we have (abstract/search-surface level only)

- "Comparisons with the CIOPS-W model currents show reasonable
  agreement with the HF radar currents, particularly with respect
  to the along-strait transport in Hecate Strait."
- Compares rotary spectra and tidal current ellipses; characterizes
  tidal vs subtidal wintertime surface circulation.
- Numeric RMSE / bias not available from the abstract.

### Applicability

- **Would be highest relevance** (direct BC-coast, direct CIOPS-W
  validation, includes tidal/subtidal decomposition we specifically
  want). Blocked by paywall. If access matters, try:
  (a) DFO institutional library (authors are DFO staff — there
      may be a DFO TR version),
  (b) contact the corresponding author,
  (c) institutional subscription to *Atmosphere-Ocean*.

---

## 5. Premathilake & Khangaonkar (2022). Salish Sea flushing + validation

**Citation.** Premathilake, L. T., & Khangaonkar, T. P. (2022).
Explicit quantification of residence and flushing times in the
Salish Sea using a sub-basin scale shoreline resolving model.
*Estuarine, Coastal and Shelf Science*.
https://doi.org/10.1016/j.ecss.2022.108022

- FVCOM Salish Sea at 75–100 m shoreline resolution; companion
  paper to PNNL-30448.
- **Local PDF:** *not downloaded* — Elsevier paywalled; tested OSTI
  (https://www.osti.gov/biblio/1888561) but no open PDF mirror in
  the time-boxed search.

### What we have (abstract/search-surface)

- Uses the same 135-ADCP-station dataset as Yang et al. 2020 for
  skill assessment. Likely overlaps with PNNL numbers; we're
  already covered by #1 for the Salish-Sea RMS anchor.

### Applicability

- **Medium** — content overlaps with #1. Not a priority to chase.

---

## 6. SalishSeaCast internal documentation (not a paper, but listed
   for traceability)

**URL.** https://salishsea-meopar-docs.readthedocs.io/en/latest/tidalcurrents/tidal_current_comparison.html

- Comparison of SalishSeaCast NEMO 3.6 modelled currents against
  ONC VENUS ADCP records at three nodes in Strait of Georgia.
- Key reported number: "Actual ADCP velocities (for Oct 15, 2013)
  are stronger by 20–40%" than SalishSeaCast model velocities.
- No formal RMSE; qualitative finding that **SalishSeaCast
  systematically underestimates current magnitudes at subsurface
  VENUS depths (~170 m and ~300 m)**, particularly on M2 and K1
  ellipses.
- Not downloaded as a PDF (it's a web doc), but recorded here
  because it's the only direct SalishSeaCast current-validation
  statement we located.

### Applicability

- **High for subsurface / deep-channel currents** — if our noise
  model needs to apply to anything below ~50 m, we should note
  that SalishSeaCast has a **systematic 20–40% magnitude bias**
  (underestimate) that's not captured by a zero-mean Gaussian
  noise model. Either (a) the bias should be applied as a scale
  factor before adding zero-mean noise, or (b) the noise RMS
  should be inflated to absorb the bias in a mean-squared sense
  (σ² = var + bias²).

---

## Calibration recommendation for the prototype noise model

> **⚠ Superseded 2026-04-24 — see top-of-file update and
> `noise_model_design.md` for the current recommendation (σ=8 cm/s
> central-SoG, layered surface-intensified vertical structure, τ_slow
> ≈ 36 h, τ_fast ≈ 3 h).** The table below reflects the original
> Norwegian-shelf-analog calibration that's too pessimistic for our
> target mid-basin regime and used a depth-independent structure
> that we've since recognised as physically wrong (see 2025-04-24
> update note at top).

Synthesizing the four sources, and explicitly labelling confidence:

| Parameter | Current prototype value | Recommended v1 value | Source / confidence |
|---|---|---|---|
| σ_slow (RMS of unresolved + forecast-error currents) | 20 cm/s | **20 cm/s** (keep) | Triangulated: PNNL 20 cm/s hindcast floor, Idžanović 20 cm/s operational forecast. **High.** |
| τ_temporal (error decorrelation timescale) | 18 h | **36–48 h** (increase ~2×) | Idžanović: error grows only 0.3 cm/s from +24 h to +66 h, implying decorrelation ≫ 42 h. **Medium.** |
| L_spatial (error decorrelation length) | ~2 km (4 cells @ 500 m) | **5–10 km** (increase 2–5×) | No clean published number. Anchored by HF-radar 5 km footprint (Idžanović, Cummins) and CIOPS-West 1/36° ≈ 2 km grid; unresolved eddies in a ~2 km model have scales of several grid cells. **Low–medium.** |
| Mean bias | 0 | 0 (keep zero-mean) | PNNL 0.02 m/s, Idžanović 2.6 cm/s — both ≤ 0.13 σ, Gaussian zero-mean is fine. **High.** |
| Depth scaling | not specified | **×1 at surface, ×0.8–1.2 below ~50 m, add +20–40% bias knob for SalishSeaCast-style runs** | SalishSeaCast docs: deep ADCP underestimate 20–40%. **Medium.** |
| Decomposition (tidal / wind / freshet / unresolved) | single lump | **Stay lumped for now**; note that tidal component is ~0 in a validated tidal model, so the 20 cm/s is predominantly wind + buoyancy + unresolved | Idžanović qualitative: "wind forcing dominates forecast uncertainty" (citing Khade 2017). **Medium.** |

### Concrete parameters to write into the prototype

```
sigma_slow       = 0.20  # m/s, isotropic RMS of unresolved-currents
tau_decorr_h     = 42    # hours, 1-e-fold temporal decorrelation
L_decorr_km      = 7.0   # km, 1-e-fold spatial decorrelation
bias             = 0.0   # m/s, zero-mean
# Optional: scale tau_decorr and L_decorr by a season knob later
# (not currently supported by any of the sources above —
# would need Cummins 2022 full text).
```

### Known gaps / things to cite as "pending"

1. **No Salish-Sea-specific correlation length or decorrelation
   timescale** found. Both numbers above are reasoned from the
   Barents analog + grid scale; they are the most speculative
   entries in the table.
2. **No seasonal split** — freshet (May–July) and winter (Nov–Feb,
   storm-driven) error structures should differ. None of the
   downloaded papers give a seasonal decomposition for currents.
   Cummins 2022 is the likely source if we get access.
3. **No decomposition number** — none of the open-access papers
   break the 20 cm/s into tidal / wind / buoyancy / mesoscale-
   unresolved sub-components. Worth noting that Idžanović
   attributes most of the 20 cm/s to wind-driven + mesoscale-
   unresolved, consistent with the hypothesis that well-tuned
   tidal models (PNNL, SSCOFS) contribute little to total
   forecast error.
4. **Operational forecast vs hindcast gap.** PNNL-30448 is a pure
   tidal hindcast — real forecasts see additional error from wind
   forecast skill, atmospheric-forcing resolution, and initial-
   condition uncertainty. Idžanović's 20 cm/s already includes
   these. The close agreement (both ~20 cm/s) is partly coincidence;
   in the Salish Sea the *forecast* RMSE should be somewhat larger
   than the *hindcast* 20 cm/s (probably 25–30 cm/s by analogy).
