## 1. Setup

- [ ] 1.1 Verify `xarray`, `netCDF4` available (`uv run python -c "import xarray, netCDF4"`). `copernicusmarine` optional (secondary-fixture regeneration only).
- [ ] 1.2 Add `utide` to `pyproject.toml` optional-dependency group `fixture-prep`. Confirm `uv run --extra fixture-prep python -c "import utide"` succeeds.
- [ ] 1.3 Confirm bundled offshore-VI fixture directory exists; secondary fixture may keep its CMEMS-monthly-climatology NetCDF for now and gain a harmonic NetCDF during Task 16.

## 2. ClimatologySource Protocol + HarmonicClimatology — Tests

- [ ] 2.1 Protocol duck-typing + `@runtime_checkable` — test double providing both `velocity_at` AND `velocity_at_vectorized` passes `isinstance`; test double missing either fails (`tests/maritime/test_climatology_source.py`).
- [ ] 2.1b `loop_vectorize_velocity_at` helper produces values consistent with the wrapped source's scalar `velocity_at` within 1e-9 (`tests/maritime/test_climatology_source.py`).
- [ ] 2.1c `HarmonicClimatology.velocity_at_vectorized` output matches scalar `velocity_at` called per-point, within 1e-9 (consistency contract — vectorized is an optimization) (`tests/maritime/test_climatology_source.py`).
- [ ] 2.2 `CONSTITUENT_FREQUENCIES_RAD_S["M2"]` equals `2π / (12.4206 * 3600)` within 1e-12 (`tests/maritime/test_climatology_source.py`).
- [ ] 2.3 `HarmonicClimatology.velocity_at` — single M2 constituent returns `background + amp * cos(-phase)` at `t=0` and matches quarter-period prediction (`tests/maritime/test_climatology_source.py`).
- [ ] 2.4 Zero-constituent `HarmonicClimatology` returns pure monthly background (tidal signal absent) (`tests/maritime/test_climatology_source.py`).
- [ ] 2.5 Month-dispatch: two `t_sec` values in different months return different backgrounds at a phase node (`tests/maritime/test_climatology_source.py`).
- [ ] 2.6 Construction rejects invalid invariants: negative variance, negative amplitude, phase out of `[0, 2π)`, constituent name not in lookup (`tests/maritime/test_climatology_source.py`).
- [ ] 2.7 Loader cross-checks NetCDF's `constituent_frequencies_rad_s` audit attr against code's lookup; fails on mismatch (`tests/maritime/test_climatology_source.py`).
- [ ] 2.8 `build_climatology_from_harmonic_netcdf` loads bundled Salish harmonic NetCDF; returned object satisfies Protocol; tidal evolution visible at ~3 h intervals (`tests/maritime/test_climatology_source.py`).
- [ ] 2.9 `build_synthetic_climatology(seed, bbox)` reproducible under same seed; empty constituents list (`tests/maritime/test_climatology_source.py`).

## 3. ClimatologySource Protocol + HarmonicClimatology — Implementation

- [ ] 3.1 `ClimatologySource` Protocol — `@runtime_checkable` with BOTH `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float, float, float]` and `velocity_at_vectorized(lats_deg, lons_deg, t_sec) -> tuple[ndarray, ndarray, ndarray, ndarray]` (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.1b `loop_vectorize_velocity_at(source, lats_deg, lons_deg, t_sec)` module-level helper implementing the vectorized contract as a scalar loop; test doubles can delegate to it in a one-line `velocity_at_vectorized` (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.2 `CONSTITUENT_FREQUENCIES_RAD_S` dict with M2, S2, K1, O1; extensible (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.3 `HarmonicClimatology` dataclass — background (month, lat, lon) + harmonic (constituent, lat, lon) storage + provenance fields; `__post_init__` invariants (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.4 `HarmonicClimatology.velocity_at` — month dispatch for background; harmonic sum over constituents evaluated at `t_sec`; spatial nearest-neighbor (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.5 `HarmonicClimatology.velocity_at_vectorized(lats[:], lons[:], t_sec)` — per-particle vectorized lookup for PF predict-stage hot path (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.6 `build_climatology_from_harmonic_netcdf(path, bbox)` — opens canonical NetCDF via xarray, subsets to bbox, validates audit attr, returns `HarmonicClimatology`. Signature has NO `CurrentField`-typed parameter (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 3.7 `build_synthetic_climatology(seed, bbox, resolution_deg)` — seeded-pseudo-random background, zero constituents. No `CurrentField` reference (`rtl/vectors/maritime/climatology_source.py`).

## 4. Fixture-Prep Helper (utide) — Tests

- [ ] 4.1 Round-trip: synthetic single-cell hindcast with pure-M2 signal of known amplitude/phase → helper writes NetCDF → loader recovers amplitude/phase within 1e-3 m/s (`tests/maritime/test_climatology_fixture_prep.py`, `pytest.importorskip("utide")`).
- [ ] 4.2 Residual-background extraction: synthetic hindcast with mean 0.05 m/s + M2 amplitude 0.3 → residual monthly background recovers 0.05 within 0.02 (`tests/maritime/test_climatology_fixture_prep.py`).
- [ ] 4.3 Per-cell failure handling: hindcast with a bad cell (all NaN, or too-short series) → helper writes zero amp/phase at that cell + `cell_ok` mask in attrs; run completes (`tests/maritime/test_climatology_fixture_prep.py`).
- [ ] 4.4 `ImportError` with clear message if `utide` not installed (test forces the error path) (`tests/maritime/test_climatology_fixture_prep.py`).

## 5. Fixture-Prep Helper (utide) — Implementation

- [ ] 5.1 `build_climatology_from_harmonic_analysis(hindcast_path, bbox, analysis_window_start, analysis_window_end, constituents, output_path)` — lazy import of `utide`; per-cell `utide.solve()`; ellipse → component conversion; residual monthly means; writes canonical NetCDF (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 5.2 Ellipse → `(amp_vx, phase_vx, amp_vy, phase_vy)` conversion helper (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 5.3 Cell-failure tolerant wrapper around `utide.solve()` — catches per-cell exceptions, writes zeros + mask, logs progress.

## 6. RealCurrentField — Tests

- [ ] 6.1 `isinstance(RealCurrentField(...), CurrentField)` passes (`tests/maritime/test_real_current_field.py`).
- [ ] 6.2 `velocity_at` returns stored NetCDF value at a known grid node within 1e-6 m/s (`tests/maritime/test_real_current_field.py`).
- [ ] 6.3 `velocity_at` linear-average at midpoint of four grid nodes (`tests/maritime/test_real_current_field.py`).
- [ ] 6.4 Out-of-time-window raises `ValueError` (`tests/maritime/test_real_current_field.py`).
- [ ] 6.5 Format polymorphism — CMEMS-product NetCDF loads, sets `product_family == "cmems_anfc"` (`tests/maritime/test_real_current_field.py`).
- [ ] 6.6 Unknown product family raises with inspected attrs + supported list (`tests/maritime/test_real_current_field.py`).
- [ ] 6.7 Provenance fields populated at construction (`tests/maritime/test_real_current_field.py`).

## 7. RealCurrentField — Implementation

- [ ] 7.1 `RealCurrentField` dataclass with `__post_init__` invariants (`rtl/vectors/maritime/current_fields_real.py`).
- [ ] 7.2 `RealCurrentField.velocity_at` — trilinear via `scipy.interpolate.RegularGridInterpolator` (`rtl/vectors/maritime/current_fields_real.py`).
- [ ] 7.3 `load_real_current_field(path)` — xarray open, product-family sniffing, variable-name normalization (`rtl/vectors/maritime/current_fields_real.py`).
- [ ] 7.4 Product-family sniffer: CIOPS-SalishSea, CMEMS analysis-forecast at minimum; unknown-family error path.

## 8. Provenance Enforcement — Tests

- [ ] 8.1 AST walk of `climatology_source.py` finds no import of `current_fields` / `current_fields_real` (`tests/maritime/test_climatology_provenance.py`).
- [ ] 8.2 AST walk of `current_fields_real.py` finds no import of `climatology_source` (`tests/maritime/test_climatology_provenance.py`).
- [ ] 8.3 Signature introspection: `build_climatology_from_harmonic_netcdf`, `build_synthetic_climatology`, `build_climatology_from_harmonic_analysis` have NO `CurrentField`-typed parameter (`tests/maritime/test_climatology_provenance.py`).
- [ ] 8.4 Signature introspection: `load_real_current_field` has NO `ClimatologySource`-typed parameter (`tests/maritime/test_climatology_provenance.py`).
- [ ] 8.5 Bundled-fixture divergence: `RealCurrentField` and `HarmonicClimatology` differ by > 0.05 m/s at some `(lat, lon, t_sec)` (`tests/maritime/test_climatology_provenance.py`).

## 9. Provenance Enforcement — Implementation

- [ ] 9.1 `[tool.importlinter]` contract "Climatology source does not access current fields" (`pyproject.toml`).
- [ ] 9.2 `[tool.importlinter]` contract "Real current field does not access climatology" (`pyproject.toml`).
- [ ] 9.3 Run `uv run lint-imports`; confirm zero violations after Tasks 3 + 7.
- [ ] 9.4 Verify that deliberately introducing a forbidden import in each module fails the contract; revert.

## 10. RegionalMap Climatology Type Migration — Tests

- [ ] 10.1 `RegionalMap` construction accepts any `ClimatologySource`-conforming object — both `HarmonicClimatology` and a test-double pass (`tests/maritime/test_map_payload.py`).
- [ ] 10.2 `RegionalMap.current_climatology_at(lat, lon, t_sec)` delegates to `climatology.velocity_at` (`tests/maritime/test_map_payload.py`).
- [ ] 10.3 Existing `ClimatologyGrid` dataclass still constructs + enforces variance-non-negativity (kept as internal storage primitive) (`tests/maritime/test_map_payload.py`).

## 11. RegionalMap Climatology Type Migration — Implementation

- [ ] 11.1 `RegionalMap.climatology: ClimatologySource` annotation + `__post_init__` isinstance check (`rtl/vectors/maritime/map_payload.py`).
- [ ] 11.2 `RegionalMap.current_climatology_at` accepts `t_sec`, delegates to `climatology.velocity_at` (`rtl/vectors/maritime/map_payload.py`).
- [ ] 11.3 Leave `ClimatologyGrid` dataclass in place (unchanged).
- [ ] 11.4 Update `tests/maritime/_pf_float_helpers.py::make_uniform_climatology` to return a Protocol-conforming wrapper. Either (a) a degenerate `HarmonicClimatology` with zero constituents + uniform background (preferred — exercises the real implementation), or (b) a minimal test-double class that provides `velocity_at` plus `velocity_at_vectorized = lambda s, l, o, t: loop_vectorize_velocity_at(s, l, o, t)`. Both forms pass `isinstance(x, ClimatologySource)` (`tests/maritime/_pf_float_helpers.py`).

## 12. Delete `climatology_from_field` — Tests

- [ ] 12.1 Rewrite the two `test_map_payload.py` tests (currently at ~524, ~537) against `build_climatology_from_harmonic_netcdf` + bundled fixture (or `build_synthetic_climatology` for pure-stub needs) (`tests/maritime/test_map_payload.py`).
- [ ] 12.2 Grep-test: no production source file imports `climatology_from_field` (`tests/maritime/test_climatology_provenance.py`).

## 13. Delete `climatology_from_field` — Implementation

- [ ] 13.1 Remove `climatology_from_field` + its unused `CurrentField` import from `rtl/vectors/maritime/map_payload.py`.
- [ ] 13.2 Remove `from rtl.vectors.maritime.map_payload import climatology_from_field` from `rtl/vectors/maritime/gen_maritime_scenario.py`.
- [ ] 13.3 `uv run pytest tests/maritime/ -k "climatology"` green.

## 14. PF Predict t_sec Threading — Tests

- [ ] 14.1 PF predict passes tick's `t_sec` into `climatology.velocity_at` — test-double records args; all calls carry expected `t_sec` (`tests/maritime/test_pf_float_predict.py`).
- [ ] 14.2 Predict tracks tidal phase: M2-only climatology produces particle-mean velocity that matches M2 phase evolution over 60 ticks (`tests/maritime/test_pf_float_predict.py`).
- [ ] 14.3 Zero-constituent climatology + zero process noise → deterministic position drift of `mean_vx * dt * n_ticks` (`tests/maritime/test_pf_float_predict.py`).

## 15. PF Predict t_sec Threading — Implementation

- [ ] 15.1 `PFFloat.predict` reads tick's `t_sec`; passes to vectorized climatology helper (`rtl/vectors/maritime/pf_float.py`).
- [ ] 15.2 Replace existing nearest-cell argmin lookup with `climatology.velocity_at_vectorized(lats[:], lons[:], t_sec)` returning `(mean_vx[:], mean_vy[:], var_vx[:], var_vy[:])` (`rtl/vectors/maritime/pf_float.py`).
- [ ] 15.3 Regenerate golden trace; verify byte-identity on re-run.

## 16. Scenario Generator Real-Data Path — Tests

- [ ] 16.1 `--current-source real` without path flags fails explicitly (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 16.2 Same-inode real-data paths fail with independence-violation error (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 16.3 Generator runs end-to-end against bundled Salish harmonic fixture; truth is `RealCurrentField`; climatology is `HarmonicClimatology` (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 16.4 Synthetic climatology construction signature has no `CurrentField` param (`tests/maritime/test_climatology_provenance.py`).
- [ ] 16.5 Synthetic path's climatology diverges from truth field (zero constituents + seeded background ≠ synthetic eddy field) (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 16.6 Temporal-honesty: generator fails on `--created-at 2024-10-15` when climatology's `analysis_window_end` is 2025 (`tests/maritime/test_gen_maritime_scenario_cli.py`).

## 17. Scenario Generator Real-Data Path — Implementation

- [ ] 17.1 Add `--current-source`, `--current-data-path`, `--climatology-data-path` argparse entries + validation (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 17.2 Route field + climatology construction:
  - `synthetic` → `SyntheticEddyField` + `build_synthetic_climatology`
  - `real` → `load_real_current_field` + `build_climatology_from_harmonic_netcdf`
  (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 17.3 `validate_real_data_paths` — inode + NetCDF metadata independence checks (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 17.4 `validate_temporal_honesty` — reads `analysis_window_end` attr, compares to deployment date (`rtl/vectors/maritime/gen_maritime_scenario.py`).

## 18. Fetch Primary Salish Fixture

- [ ] 18.1 Download CIOPS-SalishSea forecast for 2024-10-15 from MSC Datamart; subset to Salish bbox; save to fixture dir as `truth_ciops_salishsea_2024_10_15.nc`.
- [ ] 18.2 Download SalishSeaCast hourly hindcast for Salish bbox, 2007-01-01 → 2023-12-31, via UBC ERDDAP. Save intermediate NetCDF (`salishseacast_hindcast_subset.nc`) under an `.gitignore`d `_intermediate/` directory if size exceeds practical limits (or under the fixture dir if small).
- [ ] 18.3 Run `build_climatology_from_harmonic_analysis(hindcast_path, bbox, "2007-01-01", "2023-12-31", ["M2","S2","K1","O1"], output_path)` → `climatology_harmonic_salishseacast_2007_to_2023.nc`. Commit to fixture dir.
- [ ] 18.4 Write fixture `README.md` documenting products, `utide` version, license (OGL-Canada), temporal-honesty year bounds, reproducible regeneration command.
- [ ] 18.5 If fetch or analysis blocks (ERDDAP timeouts, `utide` failure patterns), fall back: use CMEMS hourly hindcast for Salish bbox (coarser but covers region). Document in README. Track unresolved issues in `docs/status.md`.

## 19. Secondary Offshore-VI Fixture Re-derivation

- [ ] 19.1 Re-derive offshore-VI climatology as a harmonic NetCDF: CMEMS hourly anfc 2014–2023 (or similar window respecting temporal honesty for the 2024-10-15 deployment date) via `utide`. Save to existing fixture dir as `climatology_harmonic_cmems_anfc_<years>.nc`.
- [ ] 19.2 Update existing fixture README to document the harmonic file alongside the earlier monthly-climatology NetCDF. Either retire the old monthly NetCDF or keep it as a legacy artifact (note in README).
- [ ] 19.3 If CMEMS authentication / download friction is high for this task, defer and rely on the primary Salish fixture for the substance divergence test.

## 20. Current-Field Sidecar + Dashboard Visualization — Tests

- [ ] 20.1 Sidecar emission: `current_field_grid.npz` has `truth_grid_u/v`, `clim_grid_u/v` (both tick-indexed), `grid_lats`, `grid_lons` (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 20.2 Truth grid reflects configured field (synthetic uniform `(0.2, 0.0)` → every cell/tick 0.2) (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 20.3 Climatology grid tick-varying in harmonic fixture: `clim_grid_u[:, i, j]` not constant across ticks at some `(i, j)` (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 20.4 Climatology grid effectively constant in synthetic-path (zero constituents) within a single month (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 20.5 Dashboard HTTP GET / inlines both grids; truth and climatology differ by > 0.05 m/s at some `(t, i, j)` (`tests/maritime/test_dashboard.py`).
- [ ] 20.6 Dashboard JS has two branches with distinct styling (`tests/maritime/test_dashboard.py`).
- [ ] 20.7 Toggles default OFF (`tests/maritime/test_dashboard.py`).

## 21. Current-Field Sidecar + Dashboard Visualization — Implementation

- [ ] 21.1 Grid-sampling helper in `gen_maritime_scenario.py` — samples truth + climatology at every tick onto `n_grid × n_grid` grid; saves to npz (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 21.2 Add `header.current_field_grid_path` to scenario header (`rtl/vectors/maritime/scenario_schema.py`, `rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 21.3 Dashboard loads npz, inlines both tick-indexed grids into scenario JSON (`experiments/12_maritime_dashboard.py`).
- [ ] 21.4 JS renders two toggleable quiver overlays; both tick-respecting; distinct styling (saturated blue vs desaturated/dashed) (`experiments/12_maritime_dashboard.py`).
- [ ] 21.5 UI adds two checkboxes default-OFF, both triggering shared redraw (`experiments/12_maritime_dashboard.py`).

## 22. Charter + Status Updates

- [ ] 22.1 Add "Data-provenance invariant" to `docs/simulation_integrity.md` — no production function takes a truth-side object and returns/mutates an onboard-side artifact. Enforcement: import-linter + AST + signature-introspection tests.
- [ ] 22.2 Add "Temporal-honesty invariant" to `docs/simulation_integrity.md` — climatology analysis window SHALL strictly predate deployment. Enforcement: scenario-gen metadata check + fixture README docs.
- [ ] 22.3 Add entry to `docs/status.md` recording the leaked-milestone finding + harmonic-climatology design correction (cross-reference archived `2026-04-23-maritime-velocity-model/` + this proposal).

## 23. End-to-End Verification

- [ ] 23.1 `uv run pytest tests/maritime/ --no-header -q` green.
- [ ] 23.2 `uv run lint-imports` green.
- [ ] 23.3 `uv run pyright rtl/vectors/maritime/ experiments/12_maritime_dashboard.py tests/maritime/` no new errors.
- [ ] 23.4 Regenerate golden trace; confirm byte-identity on re-run.
- [ ] 23.5 Full pipeline against bundled Salish fixture:
  - `uv run python rtl/vectors/maritime/gen_maritime_scenario.py --current-source real --current-data-path rtl/vectors/maritime/data/real_currents/salish_ciops_salishseacast_2024_10_15/truth_ciops_salishsea_2024_10_15.nc --climatology-data-path rtl/vectors/maritime/data/real_currents/salish_ciops_salishseacast_2024_10_15/climatology_harmonic_salishseacast_2007_to_2023.nc --seed 42 --bbox <salish-bbox> --nodes 10 --duration-hours 6 --dt-sec 60 --out /tmp/salish.jsonl --created-at 2024-10-15T00:00:00Z`
  - `uv run python rtl/vectors/maritime/run_pf_float.py --scenario /tmp/salish.jsonl --out /tmp/salish_est.jsonl`
  - Both commands exit zero, no unhandled exceptions.
- [ ] 23.6 Manual dashboard visual: `uv run python experiments/12_maritime_dashboard.py --scenario /tmp/salish.jsonl --estimates /tmp/salish_est.jsonl --port 8913 --no-open`; in browser, enable both overlays, scrub slider across 6+ hours; confirm climatology arrows rotate through tidal phase; confirm truth arrows show similar tidal structure (shared M2 signal) with additional sub-grid variation; confirm they disagree visibly (no truth leak).
- [ ] 23.7 Document new RMSE envelope — expect baseline degradation vs archived leak-artifact milestone, that is the point.

## 24. Land-Polygon Side-Issue (Scoped Out)

- [ ] 24.1 Investigate: are nodes advecting through land due to gen-side placement, PF weight bug, or dashboard frame mismatch? Document findings in `docs/status.md`. Fix trivially if possible; otherwise defer to a separate change.
