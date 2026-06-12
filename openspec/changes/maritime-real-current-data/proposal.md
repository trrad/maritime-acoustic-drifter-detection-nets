## Why

`rtl/vectors/maritime/map_payload.py::climatology_from_field(field, ...)` takes
the truth `CurrentField` object at scenario-generation time and time-averages
it into the onboard climatology that the PF reads as its predict-step prior.
For near-static truth fields the climatology mean ≈ truth at every grid cell
— truth leaks into the PF through the `onboard_map` surface, bypassing the
symbol-level truth-separation contract that import-linter enforces on
`pf_float.py`. The archived Stage 3 milestone (LoRa-only 49.9 m RMSE, per
`openspec/changes/archive/2026-04-23-maritime-velocity-model/`) was NOT the
PF doing useful work: it was the PF being advected by a climatology that
happened to equal truth.

Two structural fixes ship together in this change:

1. **Remove the leak path.** Delete `climatology_from_field`. Make
   climatology an independent data product accessed through a Protocol
   whose construction functions cannot accept a `CurrentField`.

2. **Make the climatology actually useful.** In tidally-dominated coastal
   waters (Salish Sea primary, most operational deployment targets), a
   monthly-mean prior is indefensibly crude — tides drive ±0.3–0.5 m/s
   oscillations every 6 hours, and a PF reading "expected current is
   October's mean" accumulates systematic error aligned with tidal phase
   between LoRa fixes. The climatology is a **harmonic decomposition of
   the hindcast**: per-grid-cell tidal constituents (M2, S2, K1, O1) plus
   a residual non-tidal monthly mean, computed once at fixture-prep time
   via `utide` on the SalishSeaCast / CMEMS hindcast time series. At
   PF-read time, `velocity_at(lat, lon, t_sec)` returns the background
   mean plus the harmonic sum evaluated at `t_sec`.

The `utide` analysis runs on the same hindcast NetCDF we're already
downloading (SalishSeaCast 2007–2023 hourly; CMEMS hourly reanalysis).
No separate tidal product to fetch, no new institutional dependency —
the input hindcast already contains full tidal signal, and harmonic
decomposition is the right way to extract the operator-usable prior from
it. SalishSeaCast does not publish pre-computed harmonic constituents as
a downloadable NetCDF; running `utide.solve()` at fixture-prep time
produces what we need and is a ~minutes-scale one-time cost per fixture.

Real-world drifter operators use tidal predictions computed this way.
Pulling that workflow forward forces the pipeline's architecture to
match operational reality and eliminates the synthetic-era shortcut
that made the leak possible.

## What Changes

### Truth / climatology provenance split
- **BREAKING**: `map_payload.climatology_from_field(field, ...)` is DELETED.
  The `field` argument was the leak mechanism; removing the signature
  forecloses it. Call sites in `gen_maritime_scenario.py` and
  `tests/maritime/test_map_payload.py` are rewritten.
- **BREAKING**: `RegionalMap.climatology` becomes typed `ClimatologySource`
  (Protocol), not `ClimatologyGrid`. The existing `ClimatologyGrid`
  dataclass stays as an internal storage type consumed by
  `HarmonicClimatology`'s non-tidal background channel.
- **BREAKING**: `ClimatologySource.velocity_at(lat, lon, t_sec) -> (mean_vx,
  mean_vy, var_vx, var_vy)` replaces `ClimatologyGrid.at(lat, lon) ->
  (...)`. The `t_sec` parameter is threaded so the primary implementation
  can evaluate tidal harmonics; future implementations (fleet-learned in
  M2+, alternative harmonic products) plug in without consumer changes.

### `HarmonicClimatology` as the primary concrete implementation
- `rtl/vectors/maritime/climatology_source.py::HarmonicClimatology`
  carries a gridded non-tidal background (monthly residual means +
  variance) plus a per-cell harmonic constituent table (M2 / S2 / K1 / O1
  by default). `velocity_at(lat, lon, t_sec) = background_mean +
  Σ_i amp_i(lat, lon) · cos(ω_i · t_sec - phase_i(lat, lon))`.
- A degenerate `HarmonicClimatology` with empty constituent list reduces
  to a pure monthly-mean prior — useful for CI / synthetic paths and
  regions without usable hindcast records.
- Construction functions:
  - `build_climatology_from_harmonic_analysis(hindcast_path, bbox, constituents, analysis_window)`
    — runs `utide.solve()` per grid cell on the hindcast time series and
    writes a canonical harmonic-table NetCDF. Fixture-prep helper.
  - `build_climatology_from_harmonic_netcdf(path, bbox)` — loads a
    pre-analyzed harmonic NetCDF (canonical format described in design D3).
  - `build_synthetic_climatology(seed, bbox)` — seeded pseudo-climatology
    for CI paths, no network, no truth-field dependency.
  None of these accept any `CurrentField`-typed parameter.

### Canonical harmonic-table NetCDF schema (bundled fixture)
- Dimensions: `(constituent, lat, lon)`; variables `amp_vx`, `amp_vy`,
  `phase_vx`, `phase_vy` plus background `mean_vx_ms`, `mean_vy_ms`,
  `var_vx_ms2`, `var_vy_ms2` over `(month, lat, lon)` (residual monthly
  means computed after detiding).
- Attrs: `product_family`, `dataset_id`, `analysis_window_start`,
  `analysis_window_end`, `constituents`.
- Same schema regardless of source product: SalishSeaCast-via-utide,
  CMEMS-anfc-via-utide, or (future) directly loaded WebTide / TPXO
  tables ingested into the same schema.

### New modules
- `rtl/vectors/maritime/current_fields_real.py` — `RealCurrentField` class
  implementing the `CurrentField` protocol. Loads a NetCDF nowcast/forecast
  grid (u, v, time, lat, lon) into memory, serves
  `velocity_at(lat, lon, t) -> (vx, vy)` via trilinear interpolation.
  Format-polymorphic (CIOPS-SalishSea, CIOPS-West, CMEMS analysis-forecast).
- `rtl/vectors/maritime/climatology_source.py` — `ClimatologySource` Protocol,
  `HarmonicClimatology` concrete, construction helpers (harmonic-from-hindcast,
  harmonic-from-netcdf, synthetic).
- `rtl/vectors/maritime/data_fetch.py` — optional fetch helpers, gated
  behind explicit subcommand. No runtime network in tests.

### Module boundary enforcement
- New `[tool.importlinter]` contracts:
  - `climatology_source` forbidden from importing any `current_fields*`
    module.
  - Independence contract between `current_fields*` and `climatology_source`.
- `current_fields_real.py` MUST NOT import `climatology_source`. Vice versa.
- AST + signature-introspection tests in
  `tests/maritime/test_climatology_provenance.py` assert (a) no forbidden
  imports, (b) no `CurrentField`-typed parameter on any climatology
  constructor, (c) runtime divergence between bundled truth and climatology
  at some `(lat, lon, t)`.

### Optional dependency: `utide`
- Added to `pyproject.toml` optional-dependency group (e.g., `[project.optional-dependencies.fixture-prep]`)
  since it's only needed at fixture-prep time, not at scenario-gen or PF
  runtime. The bundled harmonic NetCDFs are checked in; CI and production
  code paths do not import `utide`.
- Tests verifying the `utide`-driven fixture-prep helper are gated behind
  an `importorskip` and run only when `utide` is installed.

### Scenario generator CLI
- `--current-source {synthetic,real}` (default `synthetic` for CI).
- `--current-data-path <path>` — NetCDF for truth (required when `real`).
- `--climatology-data-path <path>` — canonical harmonic-table NetCDF
  (required when `real`). The loader dispatches on format (in-house
  utide-analyzed vs future WebTide / TPXO / FES ingests).
- Independence validation: inode check + NetCDF metadata check (product
  family + dataset ID). Temporal-honesty check: harmonic
  `analysis_window_end` SHALL strictly predate scenario `--created-at`.

### Bundled test fixtures
- **Primary (Salish Sea)** — fetched + analyzed during implementation:
  `rtl/vectors/maritime/data/real_currents/salish_ciops_salishseacast_2024_10_15/`
  - `truth_ciops_salishsea_2024_10_15.nc` — CIOPS-SalishSea 500 m forecast
    issued on/before deployment day.
  - `climatology_harmonic_salishseacast_2007_to_2023.nc` — canonical
    harmonic NetCDF derived from SalishSeaCast hindcast via `utide`.
    Years 2007–2023 (excludes deployment year; temporal honesty).
    Constituents M2, S2, K1, O1.
  - `README.md` — source-product versions, `utide` invocation, license,
    year bounds + rationale.
- **Secondary (Offshore VI)** — already downloaded this session:
  `rtl/vectors/maritime/data/real_currents/offshore_vi_2024_10_15/`
  truth already present; climatology to be re-derived as
  `climatology_harmonic_cmems_anfc_<years>.nc` via `utide` against
  CMEMS hourly hindcast. Secondary fixture's temporal-honesty caveat
  (documented at fixture-prep time) if window bounds are looser.

### Dashboard visualization
- `experiments/12_maritime_dashboard.py` gains two toggleable canvas
  overlays:
  - **Truth currents** — quiver plot sampled from the truth field at
    the current tick time.
  - **Climatology** — quiver plot sampled from the `HarmonicClimatology`
    at the current tick time. Tidal phase IS visible through the time
    slider by construction: as the user scrubs, the climatology arrows
    rotate through the M2 / S2 cycle.
- Grid density ~10×10 arrows across canvas (configurable). Data sampled
  at scenario-gen time, bundled as a sidecar.

### Charter + status updates
- `docs/simulation_integrity.md` gains two invariants:
  - **Data-provenance invariant**: no function signature in production
    code SHALL take a truth-side object and return / modify an
    onboard-side artifact.
  - **Temporal-honesty invariant**: climatology source data SHALL
    strictly predate the scenario's deployment time.
- `docs/status.md` gains a short entry recording the leaked-milestone
  finding (cross-referencing archived
  `2026-04-23-maritime-velocity-model/` and this change).

## Capabilities

### New Capabilities
- `maritime-real-current-data`: real-data current-field loader
  (`RealCurrentField`) + format-polymorphic NetCDF dispatch + optional
  fetch helpers. Owns `CurrentField` protocol conformance for real data
  products.
- `maritime-climatology-source`: `ClimatologySource` Protocol,
  `HarmonicClimatology` concrete implementation, harmonic-analysis
  fixture-prep helper, canonical harmonic-table NetCDF schema,
  temporal-honesty + provenance-independence invariants.

### Modified Capabilities
- `maritime-current-fields`: ADD requirement documenting `RealCurrentField`
  protocol conformance + NetCDF interpolation contract.
- `maritime-map-payload`: REMOVE "Climatology Consistency with Truth
  Field" (was wrong). ADD "Climatology Independence from Truth Field".
  MODIFY `RegionalMap` to type climatology as `ClimatologySource`.
- `maritime-scenario-gen`: MODIFY "CLI Invocation" to add `--current-source`,
  `--current-data-path`, `--climatology-data-path` + independence /
  temporal-honesty validation. ADD requirement covering grid-sampled
  sidecars for dashboard.
- `maritime-pf-float`: MODIFY "Predict Uses Climatology-Derived Current"
  to thread `t_sec` through the climatology read call and to document
  that the climatology mean is now time-varying within a month.
- `maritime-dashboard`: ADD requirement for tick-varying current-field
  and climatology overlay rendering. Substance: bundled Salish scenario
  shows tidal oscillation in both truth and climatology (climatology
  from harmonic decomposition of the hindcast).

## Impact

- **Production code**: new `current_fields_real.py`, new
  `climatology_source.py` (with `HarmonicClimatology`), optional
  `data_fetch.py`. Modifications to `map_payload.py` (retype climatology,
  delete leak function), `pf_float.py` (thread t_sec), `gen_maritime_scenario.py`
  (new CLI flags, new construction paths, sidecar emission),
  `experiments/12_maritime_dashboard.py` (overlays).
- **Tests**: new `test_climatology_source.py`, `test_real_current_field.py`,
  `test_climatology_provenance.py`; modified `test_map_payload.py`,
  `_pf_float_helpers.py` fixture wrapper, `test_gen_maritime_scenario_cli.py`,
  `test_dashboard.py`.
- **Dependencies**:
  - Runtime: `xarray`, `netCDF4`, `scipy` (already in stack).
  - Fixture-prep only: `utide` (new optional dep, not required at
    runtime, not required for CI). Documented in `README` /
    `pyproject.toml` as optional-dependency group.
- **Import-linter**: two new contracts.
- **Data volume**: canonical harmonic NetCDFs are small (constituents ×
  grid cells × 4 fields + background months × grid × 4 fields — tens
  to low hundreds of KB per bundled region).
- **Fixture-prep wall-clock**: `utide.solve()` per grid cell × ~hundreds
  of cells × 17 years of hourly data = minutes-to-tens-of-minutes, one
  time per bundled fixture. Not in CI.
- **Side-issue (non-blocking)**: dashboard shows nodes advecting through
  land. Investigate during scenario-gen work; defer full fix if
  non-trivial.
- **Downstream**: Change 2 (submesoscale truth variability) composes on
  top of `RealCurrentField` — unaffected by the climatology redesign.

### Superseded assumptions

- Archived `2026-04-23-maritime-velocity-model/` milestone's 49.9 m
  LoRa-only RMSE was confounded by truth-leak and is not a baseline for
  future work. Recorded here and in `docs/status.md`.
- Earlier proposal drafts separated "monthly mean in Change 1" from
  "tidal harmonics in Change 4" — that was an artifact of thinking
  about pre-built global products (CMEMS climatology) as the primary
  climatology source. Running harmonic analysis on the hindcast we're
  already downloading is the simpler, more-correct design; the two-step
  split is collapsed into a single change. The corresponding "Change 4"
  never lands; alternative harmonic product sources (DFO WebTide /
  TPXO / FES) remain a possible future additive change if an operational
  need arises.
