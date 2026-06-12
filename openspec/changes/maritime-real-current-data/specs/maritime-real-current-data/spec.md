## ADDED Requirements

### Requirement: RealCurrentField NetCDF Loader
The system SHALL provide a `RealCurrentField` class in `rtl/vectors/maritime/current_fields_real.py` that loads an oceanographic NetCDF (u, v, time, lat, lon — depth optional) and satisfies the `CurrentField` Protocol defined by `maritime-current-fields`. The class SHALL expose `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]` returning eastward and northward velocity in m/s via trilinear interpolation on the (lat, lon, t) grid. The loader SHALL populate provenance fields (`source_path`, `product_family`, `dataset_id`) from NetCDF attributes so downstream independence checks have values to compare. The class SHALL NOT accept, import, or reference any `ClimatologySource`-typed object — the real-current-field loader has zero knowledge of the climatology path.

#### Scenario: RealCurrentField satisfies CurrentField Protocol
- **WHEN** a `RealCurrentField` is constructed from the bundled offshore-VI fixture NetCDF and passed to a function with parameter type `CurrentField`
- **THEN** the isinstance check against `CurrentField` (with `@runtime_checkable`) returns True
- **AND** the function accepts the instance without type-check failure

#### Scenario: velocity_at returns interpolated values at a known grid point
- **WHEN** `velocity_at(lat_deg, lon_deg, t_sec)` is called with `(lat, lon, t)` that match a NetCDF grid node within float tolerance
- **THEN** the returned `(vx, vy)` equals the NetCDF's stored `(u, v)` at that node within 1e-6 m/s

#### Scenario: velocity_at interpolates between grid nodes
- **WHEN** `velocity_at` is called at the geometric midpoint of four adjacent grid nodes with stored `u` values `[0.1, 0.2, 0.3, 0.4]` m/s at a fixed time slice
- **THEN** the returned `vx` equals the linear average of the four values (0.25 m/s) within 1e-6 m/s

#### Scenario: velocity_at outside the time window raises
- **WHEN** `velocity_at` is called with `t_sec` outside the loaded NetCDF's time range
- **THEN** the call raises `ValueError` naming the requested `t_sec` and the loaded window bounds
- **AND** no extrapolated value is returned

### Requirement: NetCDF Format Polymorphism
The `RealCurrentField` loader SHALL dispatch on product-family metadata stored in NetCDF attributes so that multiple oceanographic products (CIOPS-SalishSea, CIOPS-West, CMEMS analysis-forecast) work with the same public API. The loader SHALL normalize product-specific variable names (e.g., `uo`/`u`, `vo`/`v`, `time_counter`/`time`) to the canonical internal (u, v, t) representation. Unknown product families SHALL fail with an explicit error naming the inspected attribute values and the supported families — silent-fall-through to a default schema is forbidden.

#### Scenario: CMEMS product loads successfully
- **WHEN** `load_real_current_field(path)` is called on the bundled offshore-VI fixture `truth_cmems_forecast_3h.nc`
- **THEN** the returned instance has `product_family == "cmems_anfc"` (or the equivalent literal the sniffer assigns)
- **AND** `velocity_at` returns sensible values for the bundled bbox

#### Scenario: Unknown product family raises with explicit error
- **WHEN** `load_real_current_field(path)` is called on a NetCDF whose attributes do not match any registered product family
- **THEN** the call raises an error whose message includes the inspected attribute values and the list of supported product families

### Requirement: Provenance Metadata Exposed
The `RealCurrentField` SHALL expose `source_path: str`, `product_family: str`, and `dataset_id: str` fields sourced from the NetCDF file path and attributes. These fields SHALL be set at construction and SHALL NOT mutate after construction. They SHALL be readable by the scenario generator's independence-validation logic without accessing the underlying NetCDF.

#### Scenario: Provenance fields are populated at construction
- **WHEN** a `RealCurrentField` is constructed from the bundled offshore-VI fixture
- **THEN** `instance.source_path` equals the input path string
- **AND** `instance.product_family` is a non-empty string matching one of the registered families
- **AND** `instance.dataset_id` is a non-empty string derived from the NetCDF's dataset-identifying attribute

### Requirement: Independence From Climatology Source
The `current_fields_real` module SHALL NOT import any symbol from `climatology_source`. An import-linter contract in `pyproject.toml` SHALL enforce this at the module-level. A structural test in `tests/maritime/test_climatology_provenance.py` SHALL additionally parse the module AST and assert that no `Import` or `ImportFrom` node resolves to `rtl.vectors.maritime.climatology_source`.

#### Scenario: import-linter forbids climatology_source import from current_fields_real
- **WHEN** `uv run lint-imports` is invoked
- **THEN** the contract "Real current field does not access climatology" (or equivalent) lists `rtl.vectors.maritime.current_fields_real` as a `source_module`
- **AND** `rtl.vectors.maritime.climatology_source` is in its `forbidden_modules`
- **AND** the command exits zero on the current codebase

#### Scenario: Adding forbidden import triggers contract failure
- **WHEN** a developer adds `from rtl.vectors.maritime.climatology_source import ClimatologySource` to `current_fields_real.py` and runs `uv run lint-imports`
- **THEN** the command exits non-zero
- **AND** the error names the violated contract

### Requirement: Optional Data-Fetch Helpers Gated from Runtime
The system MAY provide data-fetch helpers in `rtl/vectors/maritime/data_fetch.py` to subset and cache Copernicus / ERDDAP / MSC Datamart products. Any helper that performs network I/O SHALL be invocable only via an explicit subcommand entry point — not as a side effect of scenario generation or testing. `RealCurrentField.__init__`, `load_real_current_field`, and the scenario generator's real-data path SHALL NOT perform any network I/O.

#### Scenario: Scenario generator runs without network access
- **WHEN** the scenario generator is invoked with `--current-source real` against a bundled local NetCDF on a machine with no network connectivity
- **THEN** the generator completes successfully

#### Scenario: Tests run without network access
- **WHEN** the maritime test suite is executed with the system's DNS or outbound HTTP blocked
- **THEN** no test in the suite fails due to network I/O
