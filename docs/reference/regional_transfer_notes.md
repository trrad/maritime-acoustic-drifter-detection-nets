# Regional transfer notes — Canadian deployment regions

**Status:** working document, v1 (2026-04-24). Synthesized from a
targeted literature scan for published validation data on Canadian
regional ocean models. Companion to
`docs/reference/noise_model_design.md` — the Salish calibration
(σ_forecast ≈ 8 cm/s surface, L_z ≈ 15 m summer, τ_slow ≈ 36 h) is
our anchor; this doc tracks how that number transfers to the target
Canadian deployment regions.

## 1. Headline — transfer confidence by region

Ranked best to worst:

| Region | Confidence | Best-evidence anchor |
|---|---|---|
| **CIOPS-W, Vancouver Island shelf + JdF approaches** | **High** | Beutel & Allen 2024 JGR-Oceans: RMSE = **8 cm/s** top-200m at ONC-mooring 48.53°N/126.2°W, WSS 0.71. Direct match to Salish σ. |
| CIOPS-W, Hecate Strait / Queen Charlotte Sound / Great Bear outer | Medium | Atmosphere-Ocean 2022 (Hecate HF radar): "reasonable agreement" qualitative only, no published RMSE. Inferred transfer from adjacent JdF regime. |
| CIOPS-E, Scotian Shelf / central GSL / Bay of Fundy interior | Medium | Brickman & Drozdowski 2012 CANOPA (DFO CTR 278): modal vector error ~2 cm/s, 90% of errors in [−15, +27] cm/s across Maritime shelf. Hindcast, not operational forecast lead time. |
| CIOPS-E, Labrador Current front / ice edge / St. Lawrence plume | Lower | CANOPA Belle Isle 4-σ underestimate + Scotian Slope white-noise direction error. LAB60 Labrador Sea model high by ~50% on West Greenland Current boundary (Pennelly & Myers 2020). Regional multiplier ~1.5–2× σ indicated. |
| Arctic ice-free summer (Beaufort / Baffin / Hudson Bay / Labrador Sea coastal) | **Low** | Essentially no published surface-current RMSE. Validation is dominantly freshwater content / transport / iceberg-trajectory, not σ in cm/s. RIOPS shows Beaufort salinity bias 0.3–0.4 PSU upper 500 m. Operational deployment requires fresh validation against the BaySys mooring archive or summer ITPs. |

**Operational implication for the product.** The Salish-based σ = 8
cm/s is a defensible anchor for BC-coast deployments (Great Bear Sea,
BC outer shelf, Hecate Strait) with minor region-dependent
adjustments. Transfer to Atlantic Canada is plausible but should use
σ ∈ {8, 12, 15} sweep with frontal-zone stations assumed at the
higher end. Transfer to Arctic ice-free summer is **not** validated by
published literature; any Arctic deployment needs a pre-deployment
calibration exercise against BaySys-AN01/NE03/JB02 mooring records
or equivalent.

## 2. CIOPS-W — Pacific Canadian shelf

**System.** DFO operational forecast, NEMO 3.6 + CICE 6.2.0, 1/36°
(~2 km), 4× daily 48-h forecast, HRDPS+GDPS atmospheric forcing at
10 km, pseudo-analysis nudged to RIOPS. Version 2.3.0 implemented at
CMC 11 June 2024.

**Validation numbers.**

- **Beutel & Allen 2024** (*JGR-Oceans*, doi:10.1029/2023JC020106,
  open access). ONC mooring west of Juan de Fuca Strait, 48.53°N /
  126.2°W, ~500 m water. Top-200 m model-vs-observed velocity:
  **RMSE = 0.08 m/s = 8 cm/s**, Willmott Skill Score 0.71. This is
  the direct transfer anchor for the Salish σ.
- **Sahu et al. 2022** — referenced in Beutel & Allen as the CIOPS-W
  moving-vessel-profiler comparison (2013 Pathways Cruise); T RMSE
  1.09 °C, S RMSE 0.38 PSU, WSS 0.94/0.93. Full DOI unverified;
  flagged as literature TODO.
- **Cummins 2025** (*Atmosphere-Ocean*, doi:10.1080/07055900.2025.2552478,
  paywalled; abstract open). Four-year HF-radar record, central
  Juan de Fuca. Mean outflow with 3× seasonal swing; winter reversals
  lag downwelling winds by **3–4 days**. Plume-jet eddy length scale
  **L = 8–12 km**.
- **Halverson, Pawlowicz & Gower 2018** (DFO CTR 319, open access at
  publications.gc.ca). HF-radar vs drifter accuracy at SoG CODAR.
  Our primary anchor for σ_surf in `noise_model_design.md`.
- **Atmosphere-Ocean 2022, Hecate Strait HF radar** (doi:10.1080/07055900.2022.2068995,
  paywalled). "Reasonable agreement" with CIOPS-W qualitatively; no
  published surface-current RMSE.

**Region-specific error modes.** Fraser plume jets with 8–12 km
eddies; wind-response lag 3–4 days (sub-inertial); coastal buoyant-
current regime closely analogous to Salish; no ice. Plume-thickness
wind-regime dependence documented in Kastner et al. 2018 (see
`noise_model_design.md` §3).

**Implication for our noise model.** L_slow = 5 km in the current
model is smaller than Cummins's 8–12 km eddy scale — consider
increasing toward ~10 km on the BC outer shelf specifically. τ_slow
= 36 h is shorter than the 3–4 day wind-response lag — consider
~72 h for BC outer shelf. Depth structure (L_z = 15 m summer) directly
supported.

## 3. CIOPS-E — Atlantic Canadian shelf

**System.** Companion to CIOPS-W, NEMO-based, Paquin et al. 2024.

**Validation numbers.**

- **Paquin et al. 2024** (*Ocean Dynamics*, doi:10.1007/s10236-024-01634-7,
  paywalled at Springer). Published successor to the withdrawn
  EGUsphere preprint (egusphere-2023-42, pulled for "insufficient
  quantitative improvement metrics"). **Not accessible in this
  session** — literature TODO to obtain institutionally.
- **Brickman & Drozdowski 2012, CANOPA** (DFO CTR 278, open access at
  waves-vagues.dfo-mpo.gc.ca). NEMO-OPA regional shelf model for
  Maritime Canada. Vector-by-vector current-meter comparison across
  ~13,000 records over the Maritimes shelf:
  - Median speed error **0.3 cm/s**, mean **2.05 cm/s**.
  - **90% of errors within [−15, +27] cm/s**.
  - Modal vector-difference magnitude **~2 cm/s**, mean error angle
    ≈ −4°.
  - Skill (error KE / observed KE) of 1.05 averaged across 8
    sub-regions; best below 2000 m, fair 0–50 m and 500–2000 m.
  - Region-specific weak spots: Belle Isle 4-σ underestimate (ice
    open-boundary issue), Scotian Slope direction error.
- **RIOPS v2** (Smith et al. 2021, *GMD* 14:1445, open access). Pan-
  Canadian regional analysis at 3–8 km. **Does not provide
  surface-current RMSE** — SLA, SST, T/S innovations only.
- **BNAM** (Wang et al. 2018, DFO CTR 327, open access). 1/12° NEMO
  hindcast 1990–2017. Surface-current validation qualitative only:
  "good agreement" vs GLDBs, Gulf Stream overshooting noted, no
  RMSE in cm/s.

**Region-specific error modes.** Labrador Current frontal position
errors propagating into Newfoundland Shelf and Grand Banks; Gulf-
Stream-overshooting biases (BNAM); Scotian Slope direction error
(CANOPA); Belle Isle ice-boundary underestimate; St. Lawrence plume
in eastern GSL.

**Implication for our noise model.** CANOPA's modal error ~2 cm/s and
the 90% range [−15, +27] cm/s are consistent with σ_forecast ~ 8 cm/s
on the open shelf at climatology timescales, but with meaningfully
fatter tails than Salish (the +27 cm/s upper tail is ~3× σ). Near
frontal zones (Labrador Current boundary, ice edge, St. Lawrence
plume), expect 1.5–2× σ multiplier.

## 4. Arctic ice-free summer — lowest-confidence transfer

**Scope note.** The product mission is small-vessel surveillance;
under-ice is a different product. This section addresses only the
**ice-free summer window (June–October)** in Beaufort Sea, Baffin
Bay, Hudson Bay coastal zones, and Labrador Sea coastal.

**Validation numbers.**

- **ANHA12 NEMO** (Hu, Myers & Lu 2019, *JGR-Oceans*,
  doi:10.1029/2019JC015111, paywalled). 1/4° vs 1/12° for Pacific-
  water pathway and Beaufort Gyre freshwater storage. **Resolution
  matters but no surface-current RMSE in cm/s in accessible text.**
- **BaySys NEMO** (Ridenour et al. 2019 GRL doi:10.1029/2019GL082344;
  Ridenour et al. 2021 *JGR-Oceans* doi:10.1029/2020JC017089, both
  paywalled). Hudson Bay Complex 3.5–5.5 km resolution, mooring
  validation at AN01 (59.97°N/91.95°W), NE03 (57.83°N/90.88°W), JB02
  (54.68°N/80.18°W). **Surface-current RMSE not surfaced.**
- **LAB60** (Pennelly & Myers 2020, *GMD* 13:4959, open access).
  Eddy-resolving Labrador Sea. **West Greenland Current in LAB60 =
  0.6 m/s vs AVISO observed 0.4 m/s — model is ~50% high on the
  boundary current.** Labrador Current 0.4 m/s in both. This is a
  substantive bias for any coastal small-vessel-surveillance
  application along the Labrador coast.
- **RIOPS v2** (Smith et al. 2021, *GMD* 14:1445). Beaufort salinity
  bias 0.3–0.4 PSU over the upper 500 m during YOPP 2017–2019 — flag
  for plume-buoyancy and stratification errors that propagate into
  surface-current shear.

**Region-specific error modes.** Mackenzie plume thickness /
buoyant coastal current wind-regime-dependent; 50% boundary-current
speed bias in LAB60; cyclonic summer circulation with weak coastal
currents in Hudson Bay; ITP-only sparse spatial coverage means
published validation is mostly via salinity / freshwater / sea-ice,
not currents.

**Implication for our noise model.** The published Arctic validation
does **not** support transferring σ = 8 cm/s to summer-ice-free
Canadian Arctic deployments. Possible σ there is likely in the 10–20
cm/s range, particularly near Labrador Current fronts, ice-edge
processes, and Mackenzie plume. Operational deployment should treat
Arctic as a distinct calibration problem and plan a pre-deployment
validation exercise against BaySys moorings or summer ADCPs.

## 5. Cross-cutting findings

**Decorrelation scales transfer better than magnitudes.**
- L_slow = 5 km (Salish) vs Cummins 8–12 km (BC outer shelf) → consider
  L_slow ≈ 10 km for BC outer shelf configurations.
- τ_slow = 36 h (Salish) vs 3–4 day wind-response lag (Cummins) →
  consider τ_slow ≈ 72 h for wind-dominated coastal regimes.

**Depth structure (surface-intensified L_z = 15 m summer).**
- Directly supported by Kastner 2018 for Salish.
- CANOPA's "fair skill at 0–50 m and 500–2000 m, best below 2000 m"
  qualitatively supports surface-intensified for Atlantic shelf too.
- No Atlantic or Arctic paper decomposes surface-intensified vs
  barotropic σ explicitly. L_z transfer is inferential, not measured.

**Regional σ multipliers (rough, for sweep planning).**
- BC outer shelf / Great Bear: 1.0× (σ = 8 cm/s)
- Atlantic shelf interior (Scotian, Bay of Fundy, central GSL): 1.0–1.3×
- Near frontal zones (Labrador Current, St. Lawrence plume, ice edge):
  1.5–2.0×
- Arctic ice-free summer: 1.5–2.5× (highly uncertain)

## 6. Literature TODOs

Items flagged during the lit scan that should be resolved when
convenient:

1. **Confirm Sahu et al. 2022 CIOPS-W validation paper** — full DOI
   and validation-table extraction. Referenced in Beutel & Allen 2024
   but not directly found.
2. **Obtain Paquin et al. 2024 CIOPS-East paper** (Ocean Dynamics
   doi:10.1007/s10236-024-01634-7, paywalled). Primary operational-
   forecast-lead-time RMSE for Atlantic Canada is likely in this
   paper. Institutional access or author contact required.
3. **Disambiguate Cummins/Blanken/Hannah 2022 Atmosphere-Ocean**
   (doi:10.1080/07055900.2022.2068995) — likely IS the Hecate Strait
   HF-radar paper listed in `noise_model_design.md`; verify author
   list and pull if distinct.
4. **BaySys Elementa 2024 overview paper** — try University of
   Manitoba CEOS route for mooring-vs-NEMO surface-current numbers.
5. **DFO WAVES library direct search** — CIOPS-W version 1.5 / 2.3
   technical companion documents may exist and be open; not openly
   extractable in initial search.

## 7. References (downloaded or otherwise accessible)

Open-access, verified accessible:
- Beutel & Allen 2024, *JGR-Oceans*, doi:10.1029/2023JC020106.
- Brickman & Drozdowski 2012, CANOPA, DFO CTR 278.
  https://waves-vagues.dfo-mpo.gc.ca/Library/347377.pdf
- Halverson, Pawlowicz & Gower 2018, DFO CTR 319.
  https://publications.gc.ca/site/eng/9.845185/publication.html
- Smith et al. 2021, RIOPS v2, *GMD* 14:1445.
- Wang et al. 2018, BNAM, DFO CTR 327.
  https://waves-vagues.dfo-mpo.gc.ca/Library/40731327.pdf
- Pennelly & Myers 2020, LAB60, *GMD* 13:4959.
- Hoshyar et al. 2024, *Frontiers in Marine Science*,
  doi:10.3389/fmars.2024.1456205.
- HOTSSea v1, *GMD* 18:211, 2025 (already in our `references/`).

Paywalled, cited-from-surface-only:
- Cummins 2025, *Atmosphere-Ocean*, doi:10.1080/07055900.2025.2552478.
- Paquin et al. 2024, *Ocean Dynamics*, doi:10.1007/s10236-024-01634-7.
- Atmosphere-Ocean 2022 Hecate Strait, doi:10.1080/07055900.2022.2068995.
- Hu, Myers & Lu 2019, *JGR-Oceans*, doi:10.1029/2019JC015111.
- Ridenour et al. 2019, GRL, doi:10.1029/2019GL082344.
- Ridenour et al. 2021, *JGR-Oceans*, doi:10.1029/2020JC017089.
- Marson et al. 2024, *JGR-Oceans*, doi:10.1029/2023JC020697.
