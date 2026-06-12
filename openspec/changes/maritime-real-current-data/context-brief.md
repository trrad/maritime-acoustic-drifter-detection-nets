# Context Brief: maritime-real-current-data

## Purpose

Eliminates a truth-leakage bug where `map_payload.climatology_from_field(field: CurrentField, ...) -> ClimatologyGrid` routed the truth current field into the onboard climatology. Replaces it with a harmonic-decomposition pipeline: truth = `RealCurrentField` (NetCDF nowcast/forecast); climatology = `HarmonicClimatology` (per-cell tidal constituents M2/S2/K1/O1 + non-tidal monthly background, computed from a historical hindcast via `utide` at fixture-prep time). Accessed through a `ClimatologySource` Protocol threading `t_sec`. Single class (`HarmonicClimatology`) covers both harmonic and degenerate (zero-constituent) regimes.

## Key Decisions

- D1: Delete `climatology_from_field` outright.
- D2: `ClimatologySource` is a `@runtime_checkable` Protocol with TWO required methods — scalar `velocity_at(lat, lon, t_sec)` (reference) and vectorized `velocity_at_vectorized(lats[:], lons[:], t_sec)` (PF hot path). A `loop_vectorize_velocity_at` helper lets simple test doubles delegate. Prevents PF predict from needing downcast/isinstance narrowing.
- D3: Single `HarmonicClimatology` class composes background + harmonics internally. Zero-constituent form = degenerate monthly-background-only path (used by CI/synthetic).
- D4: Harmonic analysis at fixture-prep time via `utide.solve()` per grid cell on the hindcast. Optional dep. Run once, commit output.
- D5: Canonical harmonic-table NetCDF schema — `(constituent, lat, lon)` for harmonics + `(month, lat, lon)` for background, plus provenance attrs.
- D6: `CONSTITUENT_FREQUENCIES_RAD_S` lookup hardcoded; NetCDF stores audit attr; loader cross-checks on load.
- D7: CLI `--current-source {synthetic, real}` + `--current-data-path` + `--climatology-data-path` (both required when real).
- D8: Synthetic climatology is seeded-deterministic, zero-constituent, no truth reference.
- D9: `RealCurrentField` uses xarray + `scipy.interpolate.RegularGridInterpolator`.
- D10: Independence validation: inode + NetCDF `(product_family, dataset_id)` metadata check.
- D11: Dashboard sidecar `current_field_grid.npz` has tick-indexed truth + climatology grids (climatology tick-indexed because harmonic eval is time-varying).
- D12: Two new import-linter contracts (mutual independence between `climatology_source` and `current_fields*`).
- D13: AST + signature + divergence provenance tests.
- D14: Temporal honesty — `analysis_window_end` NetCDF attr SHALL predate `--created-at`.
- D15: Charter + status doc updates ship with this change.
- D16: `utide` is optional-dep (fixture-prep only); production runtime doesn't import it.

## Tasks (24 groups)

1. Setup (deps, fixture dir verify)
2. ClimatologySource Protocol + HarmonicClimatology — Tests
3. ClimatologySource Protocol + HarmonicClimatology — Implementation
4. Fixture-Prep Helper (utide) — Tests
5. Fixture-Prep Helper (utide) — Implementation
6. RealCurrentField — Tests
7. RealCurrentField — Implementation
8. Provenance Enforcement — Tests (AST, signature, divergence)
9. Provenance Enforcement — Implementation (import-linter contracts)
10. RegionalMap Climatology Type Migration — Tests
11. RegionalMap Climatology Type Migration — Implementation
12. Delete `climatology_from_field` — Tests
13. Delete `climatology_from_field` — Implementation
14. PF Predict t_sec Threading — Tests
15. PF Predict t_sec Threading — Implementation
16. Scenario Generator Real-Data Path — Tests
17. Scenario Generator Real-Data Path — Implementation
18. Fetch Primary Salish Fixture (CIOPS truth + SalishSeaCast harmonic)
19. Secondary Offshore-VI Fixture Re-derivation (optional)
20. Current-Field Sidecar + Dashboard Visualization — Tests
21. Current-Field Sidecar + Dashboard Visualization — Implementation
22. Charter + Status Updates
23. End-to-End Verification
24. Land-Polygon Side-Issue (scoped out)

## Files Affected

**New production**:
- `rtl/vectors/maritime/climatology_source.py` (Protocol, `HarmonicClimatology`, loaders, synthetic helper, fixture-prep helper)
- `rtl/vectors/maritime/current_fields_real.py` (`RealCurrentField`)
- `rtl/vectors/maritime/data_fetch.py` (optional, gated)

**Modified production**:
- `rtl/vectors/maritime/map_payload.py` (DELETE `climatology_from_field`; retype `RegionalMap.climatology: ClimatologySource`; `current_climatology_at` takes `t_sec`)
- `rtl/vectors/maritime/pf_float.py` (predict reads via `velocity_at_vectorized(lats[:], lons[:], t_sec)`)
- `rtl/vectors/maritime/gen_maritime_scenario.py` (new CLI flags, independence + temporal-honesty checks, sidecar emission, synthetic/real routing)
- `rtl/vectors/maritime/scenario_schema.py` (header `current_field_grid_path`)
- `experiments/12_maritime_dashboard.py` (load npz, render tick-varying truth + climatology quiver overlays, two toggles)
- `pyproject.toml` (new `[tool.importlinter]` contracts; `utide` optional-dep group `fixture-prep`)

**New tests**:
- `tests/maritime/test_climatology_source.py`
- `tests/maritime/test_climatology_fixture_prep.py` (utide-gated)
- `tests/maritime/test_real_current_field.py`
- `tests/maritime/test_climatology_provenance.py`

**Modified tests**:
- `tests/maritime/test_map_payload.py` (rewrite two tests against `build_climatology_from_harmonic_netcdf` / `build_synthetic_climatology`)
- `tests/maritime/_pf_float_helpers.py` (`make_uniform_climatology` returns zero-constituent `HarmonicClimatology`)
- `tests/maritime/test_gen_maritime_scenario_cli.py` (new real-data scenarios, sidecar substance checks)
- `tests/maritime/test_dashboard.py` (tick-varying climatology overlay, substance divergence)

**Fixtures**:
- `rtl/vectors/maritime/data/real_currents/offshore_vi_2024_10_15/` (CMEMS truth present; harmonic re-derivation in Task 19)
- `rtl/vectors/maritime/data/real_currents/salish_ciops_salishseacast_2024_10_15/` (fetch CIOPS truth + derive harmonic via `utide` in Task 18)

**Docs**:
- `docs/simulation_integrity.md` (data-provenance + temporal-honesty invariants with enforcement mechanisms)
- `docs/status.md` (leaked-milestone finding; cross-ref archived velocity-model change)

## Spec Pointers

All delta specs under `openspec/changes/maritime-real-current-data/specs/`:

- `maritime-real-current-data` → ADDED: Requirement: RealCurrentField NetCDF Loader, Requirement: NetCDF Format Polymorphism, Requirement: Provenance Metadata Exposed, Requirement: Independence From Climatology Source, Requirement: Optional Data-Fetch Helpers Gated from Runtime
  (`…/specs/maritime-real-current-data/spec.md`)

- `maritime-climatology-source` → ADDED: Requirement: ClimatologySource Protocol, Requirement: HarmonicClimatology Concrete Implementation, Requirement: Constituent Frequency Lookup, Requirement: build_climatology_from_harmonic_netcdf Does Not Accept CurrentField, Requirement: build_synthetic_climatology Has No Truth-Field Dependency, Requirement: build_climatology_from_harmonic_analysis Fixture-Prep Helper, Requirement: Climatology Source Does Not Import Current Fields, Requirement: Temporal Honesty of Analysis Window, Requirement: Bundled Fixture Documentation
  (`…/specs/maritime-climatology-source/spec.md`)

- `maritime-current-fields` → ADDED: Requirement: Real Current Field Protocol Conformance
  (`…/specs/maritime-current-fields/spec.md`)

- `maritime-map-payload` → MODIFIED: Requirement: Current Climatology Grid, Requirement: RegionalMap Composition; ADDED: Requirement: Climatology Independence From Truth Field; REMOVED: Requirement: Climatology Consistency with Truth Field
  (`…/specs/maritime-map-payload/spec.md`)

- `maritime-scenario-gen` → MODIFIED: Requirement: CLI Invocation; ADDED: Requirement: Synthetic Climatology Has No Truth-Field Dependency, Requirement: Current-Field Visualization Sidecar
  (`…/specs/maritime-scenario-gen/spec.md`)

- `maritime-pf-float` → MODIFIED: Requirement: Predict Uses Climatology-Derived Current
  (`…/specs/maritime-pf-float/spec.md`)

- `maritime-dashboard` → ADDED: Requirement: Current-Field Quiver Overlays, Requirement: Overlay Toggles In UI
  (`…/specs/maritime-dashboard/spec.md`)
