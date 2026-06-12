## 1. Setup

- [ ] 1.1 Confirm Change 1 (`maritime-real-current-data`) has landed: `ClimatologySource` Protocol + `HarmonicClimatology` + `RealCurrentField` exist; import-linter contracts in place; dashboard quiver overlays functional.
- [ ] 1.2 Verify `scipy.fft` and `scipy.interpolate.RegularGridInterpolator` are available (`uv run python -c "from scipy import fft, interpolate"`).

## 2. SpectralSubmesoscaleField — Tests

- [ ] 2.1 Protocol conformance — `isinstance(SpectralSubmesoscaleField(...), CurrentField)` returns True (`tests/maritime/test_submesoscale.py`).
- [ ] 2.2 Zero amplitude produces zero velocity (`tests/maritime/test_submesoscale.py`).
- [ ] 2.3 Measured rms amplitude within ±20% of configured (large-sample-mean test) (`tests/maritime/test_submesoscale.py`).
- [ ] 2.4 Numerical divergence < 1e-6 m/s per m on internal grid (`tests/maritime/test_submesoscale.py`).
- [ ] 2.5 2D power-spectrum slope ∈ [-2.0, -1.3] ensemble mean over 100 seeds (`tests/maritime/test_submesoscale.py`).
- [ ] 2.6 OU amplitude stationarity — rms constant within ±15% over 1000 ticks (`tests/maritime/test_submesoscale.py`).
- [ ] 2.7 Long-τ regime: spatial correlation across 10 ticks > 0.7 (`tests/maritime/test_submesoscale.py`).
- [ ] 2.8 Short-τ regime: spatial correlation across 1 tick < 0.3 (`tests/maritime/test_submesoscale.py`).
- [ ] 2.9 Byte-identical reproducibility under same seed (`tests/maritime/test_submesoscale.py`).

## 3. SpectralSubmesoscaleField — Implementation

- [ ] 3.1 `SubmesoscaleConfig` dataclass with `__post_init__` parameter validation (`rtl/vectors/maritime/current_fields_submesoscale.py`).
- [ ] 3.2 `SpectralSubmesoscaleField.__init__` — allocate internal grid, sample initial spectral coefficients from stationary spectrum, build first interpolators (`rtl/vectors/maritime/current_fields_submesoscale.py`).
- [ ] 3.3 `step(dt_sec)` — OU update on spectral coefficients, synthesize ψ via IFFT, take spectral derivatives for (u, v), cache interpolators (`rtl/vectors/maritime/current_fields_submesoscale.py`).
- [ ] 3.4 `velocity_at(lat, lon, t_sec)` — query cached interpolator at t_sec (stepping forward if needed) (`rtl/vectors/maritime/current_fields_submesoscale.py`).
- [ ] 3.5 Verify ≤5 ms / step at 256×256 grid on dev machine (`rtl/vectors/maritime/current_fields_submesoscale.py`, benchmark in test).

## 4. CompositeCurrentField — Tests

- [ ] 4.1 Additive velocity at known constituent values (`tests/maritime/test_composite_current_field.py`).
- [ ] 4.2 Protocol conformance via `isinstance` (`tests/maritime/test_composite_current_field.py`).
- [ ] 4.3 Associativity under repeated nesting (`tests/maritime/test_composite_current_field.py`).

## 5. CompositeCurrentField — Implementation

- [ ] 5.1 `CompositeCurrentField` dataclass (`rtl/vectors/maritime/current_fields_composite.py` or inlined in `current_fields.py`).

## 6. Climatology `submesoscale_energy_ms` — Tests

- [ ] 6.1 Non-zero `submesoscale_energy_ms` broadens `var_vx` / `var_vy` by amplitude² (`tests/maritime/test_climatology_source.py`).
- [ ] 6.2 Zero `submesoscale_energy_ms` matches gridded variance exactly (backwards compat) (`tests/maritime/test_climatology_source.py`).
- [ ] 6.3 Negative `submesoscale_energy_ms` rejected at construction (`tests/maritime/test_climatology_source.py`).
- [ ] 6.4 Provenance: climatology construction signatures still reject CurrentField-typed params (re-verify Change-1 contract under new field) (`tests/maritime/test_climatology_provenance.py`).

## 7. Climatology `submesoscale_energy_ms` — Implementation

- [ ] 7.1 Add `submesoscale_energy_ms: float = 0.0` field to `HarmonicClimatology` + `__post_init__` non-negativity check (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 7.2 Modify `HarmonicClimatology.velocity_at` + `velocity_at_vectorized` to add `submesoscale_energy_ms**2` to returned variance components (`rtl/vectors/maritime/climatology_source.py`).
- [ ] 7.3 Extend `build_climatology_from_harmonic_netcdf` and `build_synthetic_climatology` with optional `submesoscale_energy_ms` parameter (defaults to 0.0) (`rtl/vectors/maritime/climatology_source.py`).

## 8. Scenario Generator Wire-Up — Tests

- [ ] 8.1 CLI flags parse and record in header (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 8.2 `--submesoscale-amplitude-ms 0.0` disables composition — truth field is base alone, not Composite (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 8.3 Non-zero amplitude produces Composite with matching parameters (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 8.4 Decoupled `--climatology-expected-submesoscale-ms` from truth amplitude — scenario completes normally (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 8.5 Sidecar chaos arrays shape-correct and equal truth - base (`tests/maritime/test_gen_maritime_scenario_cli.py`).
- [ ] 8.6 Zero-amplitude chaos arrays equal zero (`tests/maritime/test_gen_maritime_scenario_cli.py`).

## 9. Scenario Generator Wire-Up — Implementation

- [ ] 9.1 Add submesoscale argparse flags + `--climatology-expected-submesoscale-ms` (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 9.2 When amplitude > 0, wrap base field in `CompositeCurrentField(base, SpectralSubmesoscaleField(...))` (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 9.3 Pass `--climatology-expected-submesoscale-ms` into `build_climatology_from_harmonic_netcdf` / `build_synthetic_climatology` (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 9.4 Extend sidecar emission to also sample base field alone onto the grid and save the difference as chaos arrays (`rtl/vectors/maritime/gen_maritime_scenario.py`).
- [ ] 9.5 Extend `scenario_schema.py` header with submesoscale sub-object (`rtl/vectors/maritime/scenario_schema.py`).

## 10. Dashboard Chaos Overlay — Tests

- [ ] 10.1 Chaos-overlay toggle defaults off (`tests/maritime/test_dashboard.py`).
- [ ] 10.2 Toggle activates renders arrows with distinct styling from main truth overlay (`tests/maritime/test_dashboard.py`).
- [ ] 10.3 Chaos arrays flow into inlined JSON (`tests/maritime/test_dashboard.py`).
- [ ] 10.4 Zero-amplitude chaos overlay renders gracefully (no crash, no arrows) (`tests/maritime/test_dashboard.py`).

## 11. Dashboard Chaos Overlay — Implementation

- [ ] 11.1 Load `truth_grid_chaos_u/v` from the sidecar and inline in scenario JSON (`experiments/12_maritime_dashboard.py`).
- [ ] 11.2 Add "Truth current chaos" checkbox + JS render function with distinct styling (dashed or translucent arrows) (`experiments/12_maritime_dashboard.py`).
- [ ] 11.3 Wire toggle to shared redraw function.

## 12. End-to-End Verification

- [ ] 12.1 Full pytest green (`uv run pytest tests/maritime/ --no-header -q`).
- [ ] 12.2 Lint-imports green (`uv run lint-imports`).
- [ ] 12.3 Pyright green on changed files (`uv run pyright rtl/vectors/maritime/ experiments/12_maritime_dashboard.py tests/maritime/`).
- [ ] 12.4 Golden-trace regeneration — synthetic path with default `--submesoscale-amplitude-ms 0.0` keeps byte-identity; regenerate and confirm matches committed fixture.
- [ ] 12.5 End-to-end real-data pipeline with submesoscale on: gen → PF → dashboard. Confirm no crash; ESS > 0 throughout; RMSE finite.
- [ ] 12.6 Manual visual: dashboard chaos overlay shows moving submesoscale structure; truth overlay shows composed field; climatology is smooth and static. Visual inspection of fixture run.
- [ ] 12.7 Document new RMSE envelope (expected worse than Change 1's baseline; that is the point).
