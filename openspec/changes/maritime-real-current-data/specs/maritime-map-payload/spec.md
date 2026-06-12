## MODIFIED Requirements

### Requirement: Current Climatology Grid
The system SHALL retain the `ClimatologyGrid` dataclass (unchanged storage shape: `lats`, `lons`, `mean_vx_ms`, `mean_vy_ms`, `var_vx_ms2`, `var_vy_ms2`) as a low-level storage primitive available to internal uses (e.g., tests, diagnostic tools). Consumers of `RegionalMap.climatology` SHALL NOT read through `ClimatologyGrid.at(lat, lon)` directly; they SHALL use the `ClimatologySource.velocity_at(lat, lon, t_sec)` interface, which returns `(mean_vx, mean_vy, var_vx, var_vy)`. Construction SHALL reject negative variance entries.

#### Scenario: ClimatologyGrid remains constructible as internal storage
- **WHEN** `ClimatologyGrid(lats, lons, mean_vx_ms, mean_vy_ms, var_vx_ms2, var_vy_ms2)` is constructed with valid arrays
- **THEN** the dataclass is instantiated successfully
- **AND** the `__post_init__` variance-non-negativity check is enforced

#### Scenario: Variance is non-negative
- **WHEN** a ClimatologyGrid is constructed with valid parameters
- **THEN** all variance values are non-negative

### Requirement: RegionalMap Composition
The `RegionalMap` SHALL compose bathymetry, land polygons, shipping lanes, and a climatology source into a single data structure. The `climatology` field SHALL be typed as `ClimatologySource` (the Protocol defined in `maritime-climatology-source`) rather than the concrete `ClimatologyGrid`. All map data SHALL be injected at construction time, not loaded internally. The `current_climatology_at` method SHALL delegate to `climatology.velocity_at(lat_deg, lon_deg, t_sec)` — requiring a `t_sec` parameter so that time-parameterized climatology implementations (monthly-mean today, tidal harmonics in future changes) are consumed uniformly.

#### Scenario: Construction with a Protocol-conforming climatology
- **WHEN** a `RegionalMap` is constructed with a `BathymetryGrid`, land polygons, shipping lanes, and any object satisfying `ClimatologySource` (including `HarmonicClimatology` and test doubles)
- **THEN** all query methods (`depth_at`, `is_on_land`, `is_in_shipping_lane`, `current_climatology_at`) work correctly

#### Scenario: current_climatology_at requires t_sec
- **WHEN** `RegionalMap.current_climatology_at(lat, lon, t_sec)` is called with a valid `t_sec`
- **THEN** the returned `(mean_vx, mean_vy, var_vx, var_vy)` equals `regional_map.climatology.velocity_at(lat, lon, t_sec)`

#### Scenario: No file I/O during construction
- **WHEN** `RegionalMap.__init__` is called with in-memory data
- **THEN** no file system access occurs (constructible from pure test data)

## ADDED Requirements

### Requirement: Climatology Independence From Truth Field
The onboard climatology SHALL be constructed from a data source independent of the scenario's truth current field. No production-code function signature SHALL take a `CurrentField` (or any `CurrentField`-conforming type) and return or mutate a `ClimatologyGrid`, `ClimatologySource`, or `RegionalMap`. This is the structural defense against truth leaking into the PF prior through the onboard-map surface. The invariant SHALL be enforced at three layers: (a) an import-linter contract separating `climatology_source` from all `current_fields*` modules; (b) an AST test asserting no forbidden imports in `climatology_source.py`; (c) a signature-introspection test asserting `build_climatology_from_monthly_mean` (and any other `ClimatologySource` constructor) has no `CurrentField`-typed parameter.

#### Scenario: import-linter enforces independence
- **WHEN** `uv run lint-imports` is invoked
- **THEN** a contract separating `rtl.vectors.maritime.climatology_source` from `rtl.vectors.maritime.current_fields` and `rtl.vectors.maritime.current_fields_real` is present and passes
- **AND** the reciprocal contract separating `rtl.vectors.maritime.current_fields_real` from `rtl.vectors.maritime.climatology_source` also passes

#### Scenario: No ClimatologySource constructor takes a CurrentField
- **WHEN** `inspect.signature` is applied to every public constructor function in `climatology_source.py` and each parameter's annotation is resolved via `typing.get_type_hints`
- **THEN** no parameter's resolved type is or contains `CurrentField`, `RealCurrentField`, `SyntheticEddyField`, or any Union admitting a truth-side field type

#### Scenario: Bundled fixture's truth and climatology diverge meaningfully
- **WHEN** the bundled offshore-VI fixture's `RealCurrentField` and the bundled `MonthlyMeanClimatology` are both loaded, and a sweep over `(lat, lon, t_sec)` inside the fixture's bbox is performed
- **THEN** there exists at least one `(lat, lon, t_sec)` where `|truth.velocity_at(lat, lon, t_sec) - climatology.velocity_at(lat, lon, t_sec)[:2]|` exceeds 0.05 m/s (a typical tidal amplitude floor — distinguishes "climatology is an independent product with resolvable differences from instantaneous truth" from "climatology is truth's time-average")

## REMOVED Requirements

### Requirement: Climatology Consistency with Truth Field
**Reason**: This requirement mandated the `climatology_from_field(field: CurrentField, ...) -> ClimatologyGrid` function that derived the onboard climatology from the truth field by time-averaging. Under near-static truth fields, that derivation produced a climatology mean ≈ truth at every grid cell, causing truth to leak into the PF's predict-step prior through the onboard_map surface — bypassing the symbol-level truth-separation contract enforced by import-linter. The archived Stage 3 LoRa-only 49.9 m RMSE milestone was an artifact of this leak, not a measurement of real inference. The function's existence and its signature were the bug: no implementation taking a `CurrentField` and producing a PF-readable artifact is safe.

**Migration**: Replace all call sites with `build_climatology_from_harmonic_netcdf(path, bbox)` from `maritime-climatology-source`, which loads an independent harmonic-table climatology NetCDF (per-cell tidal constituents + non-tidal background, derived from a historical hindcast via `utide`). The scenario generator's real-data path (`--current-source real --climatology-data-path <path>`) wires this construction in; the synthetic path uses `build_synthetic_climatology(seed, bbox)`, a degenerate zero-constituent `HarmonicClimatology` whose construction does NOT reference any truth field object. The two existing test call sites in `tests/maritime/test_map_payload.py` (lines 524, 537) are rewritten against `build_climatology_from_harmonic_netcdf` + the bundled Salish harmonic fixture (or `build_synthetic_climatology` where the test only needs a Protocol-conforming stub).
