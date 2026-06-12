## MODIFIED Requirements

### Requirement: RegionalMap Composition
The `RegionalMap` SHALL compose bathymetry, land polygons, shipping lanes, and a climatology source into a single data structure. The `climatology` field SHALL be typed as `ClimatologySource` (the Protocol defined in `maritime-climatology-source`). `ClimatologySource` implementations in use after this change SHALL report variance channels that include the operator's expected unresolved-variability energy (submesoscale / tidal / eddy) added to any gridded observational variance — i.e., `var_vx` and `var_vy` from `velocity_at` represent "expected variance of `(truth − climatology_mean)` including unresolved energies", NOT "observed truth variance". The `current_climatology_at(lat, lon, t_sec)` method SHALL delegate to `climatology.velocity_at(lat, lon, t_sec)` unchanged; the variance semantics change is in the climatology implementation, not the map accessor.

#### Scenario: Construction with a Protocol-conforming climatology
- **WHEN** a `RegionalMap` is constructed with a `BathymetryGrid`, land polygons, shipping lanes, and any object satisfying `ClimatologySource` (including `HarmonicClimatology` with non-zero `submesoscale_energy_ms` and test doubles)
- **THEN** all query methods (`depth_at`, `is_on_land`, `is_in_shipping_lane`, `current_climatology_at`) work correctly

#### Scenario: current_climatology_at delegates unchanged
- **WHEN** `RegionalMap.current_climatology_at(lat, lon, t_sec)` is called with a valid `t_sec`
- **THEN** the returned `(mean_vx, mean_vy, var_vx, var_vy)` equals `regional_map.climatology.velocity_at(lat, lon, t_sec)` — the map accessor is a pass-through

#### Scenario: No file I/O during construction
- **WHEN** `RegionalMap.__init__` is called with in-memory data
- **THEN** no file system access occurs (constructible from pure test data)

## ADDED Requirements

### Requirement: Climatology Carries Operator-Expected Submesoscale Energy
The `HarmonicClimatology` (and any future `ClimatologySource` implementation) SHALL expose a `submesoscale_energy_ms: float` attribute representing the operator's expected rms amplitude of unresolved submesoscale variability in the region. This value SHALL be sourced from regional survey data or an operator-configured scalar — NOT computed from the scenario's truth field. `velocity_at(lat, lon, t_sec)` SHALL return variance components that include `submesoscale_energy_ms²` added to any gridded observational variance: `var_vx_returned = var_vx_grid + submesoscale_energy_ms²` (and similarly for `var_vy`). The attribute SHALL default to 0.0 for backwards compatibility; the returned variance equals the gridded variance in that case.

#### Scenario: Non-zero submesoscale_energy_ms broadens returned variance
- **WHEN** a `HarmonicClimatology` is constructed with `submesoscale_energy_ms = 0.1` and the gridded `var_vx_ms2` at cell `(lat, lon)` equals 0.004 m²/s²
- **AND** `velocity_at(lat, lon, t_sec)` is called
- **THEN** the returned `var_vx` equals `0.004 + 0.01 = 0.014` m²/s² within 1e-9
- **AND** the returned `var_vy` equals `gridded_var_vy + 0.01` within 1e-9

#### Scenario: Zero submesoscale_energy_ms matches gridded variance
- **WHEN** `submesoscale_energy_ms = 0.0` (the default)
- **THEN** the returned `var_vx` and `var_vy` equal the gridded values exactly

#### Scenario: Negative submesoscale_energy_ms rejected at construction
- **WHEN** `HarmonicClimatology` is constructed with `submesoscale_energy_ms = -0.05`
- **THEN** `__post_init__` raises `ValueError` naming the field

### Requirement: Climatology Does Not Derive submesoscale_energy_ms From Truth
The `submesoscale_energy_ms` attribute SHALL be populated at construction time from either a bundled-fixture default, an explicit CLI flag value, or a ClimatologySource-level configuration — NEVER computed from the scenario's truth field, truth particles, or any `CurrentField`-typed object. A signature-introspection test SHALL assert that no constructor function producing a `ClimatologySource` with a non-zero `submesoscale_energy_ms` takes a `CurrentField`-typed parameter.

#### Scenario: Constructor signatures do not accept CurrentField
- **WHEN** every public constructor function in `climatology_source.py` that can produce a `ClimatologySource` with non-zero `submesoscale_energy_ms` is inspected via `inspect.signature` + `typing.get_type_hints`
- **THEN** no parameter's resolved type is or contains `CurrentField`, `RealCurrentField`, `SyntheticEddyField`, or `SpectralSubmesoscaleField`

#### Scenario: Scenario-gen sets submesoscale_energy_ms from a CLI scalar, not from truth
- **WHEN** the scenario generator constructs the onboard climatology with `--climatology-expected-submesoscale-ms 0.1`
- **THEN** the resulting `HarmonicClimatology.submesoscale_energy_ms` equals 0.1
- **AND** no code path in the generator references the truth `CurrentField` to derive this value (verified by AST inspection of the generator's climatology-construction path)
