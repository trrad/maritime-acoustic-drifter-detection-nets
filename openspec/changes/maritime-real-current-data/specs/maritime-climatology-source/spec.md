## ADDED Requirements

### Requirement: ClimatologySource Protocol
The system SHALL define a `ClimatologySource` Protocol in `rtl/vectors/maritime/climatology_source.py` with TWO required methods:

- **Scalar**: `velocity_at(lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float, float, float]` returning `(mean_vx_ms, mean_vy_ms, var_vx_ms2, var_vy_ms2)` at a single query point. Reference semantics.
- **Vectorized**: `velocity_at_vectorized(lats_deg: np.ndarray, lons_deg: np.ndarray, t_sec: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]` returning four 1-D arrays aligned with the input lat/lon arrays. Called per-tick by the PF predict hot path; `t_sec` is scalar (uniform across particles within a tick).

The Protocol SHALL be `@runtime_checkable`. Any time-parameterized climatology implementation (harmonic from hindcast today, alternative harmonic products, fleet-learned in M2+) SHALL satisfy this Protocol without inheritance by implementing both methods.

The module SHALL additionally export a helper `loop_vectorize_velocity_at(source: ClimatologySource, lats_deg, lons_deg, t_sec)` that implements the vectorized contract as a scalar loop over `velocity_at`. Test doubles and simple non-performance-sensitive implementations MAY satisfy `velocity_at_vectorized` by returning `loop_vectorize_velocity_at(self, lats, lons, t_sec)`. Performance-sensitive concrete implementations (e.g., `HarmonicClimatology`) SHALL provide a native vectorized implementation that avoids the Python-level loop.

#### Scenario: Protocol is satisfied by a test double providing both methods
- **WHEN** a class provides both `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float, float, float]` and `velocity_at_vectorized(lats_deg, lons_deg, t_sec) -> tuple[ndarray, ndarray, ndarray, ndarray]` and an instance is tested with `isinstance(instance, ClimatologySource)`
- **THEN** the check returns True without inheritance
- **AND** functions typed on `ClimatologySource` accept the instance

#### Scenario: Missing scalar method fails the isinstance check
- **WHEN** a class omits `velocity_at` (provides only `velocity_at_vectorized`)
- **THEN** `isinstance(instance, ClimatologySource)` returns False

#### Scenario: Missing vectorized method fails the isinstance check
- **WHEN** a class omits `velocity_at_vectorized` (provides only `velocity_at`)
- **THEN** `isinstance(instance, ClimatologySource)` returns False

#### Scenario: loop_vectorize_velocity_at produces values consistent with scalar velocity_at
- **WHEN** a test-double `ClimatologySource` whose `velocity_at` returns deterministic per-query values is wrapped via `loop_vectorize_velocity_at(source, lats, lons, t_sec)` for an aligned `(lats, lons)` pair of length n
- **THEN** the i-th element of each returned array equals `source.velocity_at(lats[i], lons[i], t_sec)[j]` within 1e-9 for every `i ∈ [0, n)` and the corresponding channel `j ∈ {0, 1, 2, 3}`

#### Scenario: HarmonicClimatology's velocity_at_vectorized is consistent with its velocity_at
- **WHEN** `HarmonicClimatology.velocity_at_vectorized(lats, lons, t_sec)` is called on a bundled-fixture instance and compared element-by-element to per-point calls `HarmonicClimatology.velocity_at(lats[i], lons[i], t_sec)`
- **THEN** the vectorized output matches the scalar-loop output within 1e-9 for every element (the vectorized form is an optimization, not a different computation)

### Requirement: HarmonicClimatology Concrete Implementation
The system SHALL provide a concrete `HarmonicClimatology` class that satisfies `ClimatologySource` by composing two internal storage channels: (a) a gridded non-tidal background over `(month, lat, lon)` with mean and variance velocity fields, derived from a hindcast after detiding; and (b) a per-cell per-constituent harmonic table over `(constituent, lat, lon)` carrying `(amp_vx, amp_vy, phase_vx, phase_vy)`. `velocity_at(lat, lon, t_sec)` SHALL return `(background_mean_vx + Σ_i amp_vx_i · cos(ω_i · t_sec − phase_vx_i), background_mean_vy + Σ_i amp_vy_i · cos(ω_i · t_sec − phase_vy_i), background_var_vx, background_var_vy)` where `ω_i` is the astronomical frequency of constituent `i` from a hardcoded lookup. The class SHALL expose `source_path: str`, `product_family: str`, `dataset_id: str`, and `analysis_window: tuple[str, str]` provenance fields.

A degenerate `HarmonicClimatology` with an empty `constituents` list SHALL satisfy the Protocol and return pure-background velocities — supports CI / synthetic paths and regions without usable hindcast records.

#### Scenario: velocity_at returns background mean plus harmonic sum
- **WHEN** a `HarmonicClimatology` is constructed with a single `M2` constituent at cell `(lat0, lon0)` having `amp_vx = 0.3, phase_vx = 0.0` and background `mean_vx = 0.05` for the month containing `t_sec`
- **AND** `velocity_at(lat0, lon0, t_sec=0.0)` is called
- **THEN** the returned `mean_vx` equals `0.05 + 0.3 * cos(0) = 0.35` within 1e-9 m/s

#### Scenario: velocity_at at a quarter M2 period returns background plus zero harmonic contribution
- **WHEN** the same configuration is queried at `t_sec = (12.4206 * 3600) / 4` (a quarter M2 period)
- **THEN** the returned `mean_vx` equals `0.05 + 0.3 * cos(π/2) = 0.05` within 1e-6 m/s (harmonic contribution passes through zero)

#### Scenario: Empty constituents list reduces to pure monthly background
- **WHEN** a `HarmonicClimatology` is constructed with `constituents=[]` and non-trivial background means
- **AND** `velocity_at(lat, lon, t_sec)` is called at multiple `t_sec` values within the same month
- **THEN** the returned `mean_vx` and `mean_vy` are constant across those calls (no tidal variation)
- **AND** the returned values equal the background month cell's stored means within 1e-9

#### Scenario: velocity_at dispatches background to nearest month
- **WHEN** `velocity_at` is called with `t_sec` values corresponding to consecutive months whose stored background slices differ at the same cell
- **THEN** the returned `mean_vx` (sans harmonic contribution, i.e., at a phase node) differs between the two calls
- **AND** the difference equals the difference between the two monthly background values

#### Scenario: Construction rejects invalid invariants
- **WHEN** `HarmonicClimatology` is constructed with a negative `var_vx_ms2` entry, or a negative amplitude, or a phase outside `[0, 2π)`, or a constituent name not in `CONSTITUENT_FREQUENCIES_RAD_S`
- **THEN** `__post_init__` raises `ValueError` naming the offending field / constituent

### Requirement: Constituent Frequency Lookup
The module SHALL expose `CONSTITUENT_FREQUENCIES_RAD_S: dict[str, float]` mapping constituent name (e.g., `"M2"`, `"S2"`, `"K1"`, `"O1"`) to angular frequency in rad/s, populated with at minimum `M2, S2, K1, O1` using standard astronomical values. Loaders SHALL cross-check the NetCDF's stored `constituent_frequencies_rad_s` audit attribute against this lookup and fail with an explicit error on mismatch; the audit attribute catches schema drift (e.g., fixture built against a different ω for M2).

#### Scenario: M2 frequency matches standard value
- **WHEN** `CONSTITUENT_FREQUENCIES_RAD_S["M2"]` is inspected
- **THEN** it equals `2 * pi / (12.4206 * 3600)` within 1e-12 rad/s

#### Scenario: Loader rejects mismatched audit attribute
- **WHEN** `build_climatology_from_harmonic_netcdf(path, bbox)` is called on a NetCDF whose `constituent_frequencies_rad_s` audit attribute lists an M2 frequency that does not match `CONSTITUENT_FREQUENCIES_RAD_S["M2"]` within 1e-9 rad/s
- **THEN** the loader raises an error whose message names M2 and both frequency values

### Requirement: build_climatology_from_harmonic_netcdf Does Not Accept CurrentField
The system SHALL provide a constructor `build_climatology_from_harmonic_netcdf(path: str, bbox: tuple[float, float, float, float]) -> HarmonicClimatology` that loads the canonical harmonic NetCDF schema (see `maritime-scenario-gen` for the schema contract) and subsets to `bbox`. The function signature SHALL NOT include any parameter typed as `CurrentField`, `RealCurrentField`, `SyntheticEddyField`, or any Union / generic admitting a `CurrentField`. Structural signature introspection SHALL assert this at test time.

#### Scenario: Signature introspection confirms no CurrentField parameter
- **WHEN** `inspect.signature(build_climatology_from_harmonic_netcdf)` is evaluated and each parameter's annotation is resolved via `typing.get_type_hints`
- **THEN** no parameter's annotation is or contains `CurrentField`, `RealCurrentField`, `SyntheticEddyField`, or any Union admitting a truth-side field type

#### Scenario: Loader produces a working ClimatologySource from bundled fixture
- **WHEN** `build_climatology_from_harmonic_netcdf(bundled_salish_harmonic_path, salish_bbox)` is called
- **THEN** the returned object satisfies the `ClimatologySource` Protocol (`isinstance` passes)
- **AND** `velocity_at(lat, lon, t_sec)` returns finite `(mean_vx, mean_vy, var_vx, var_vy)` for any in-bbox `(lat, lon, t_sec)`
- **AND** `velocity_at` at two `t_sec` values separated by ~3 h returns `(mean_vx, mean_vy)` pairs that differ by more than 0.02 m/s at at least one in-bbox `(lat, lon)` (tidal phase change visible in returned mean)

### Requirement: build_synthetic_climatology Has No Truth-Field Dependency
The system SHALL provide `build_synthetic_climatology(seed: int, bbox: tuple[float, float, float, float], resolution_deg: float = 0.05) -> HarmonicClimatology` that constructs a degenerate `HarmonicClimatology` with zero constituents and seeded-pseudo-random smooth background means and variance. The signature SHALL NOT accept any `CurrentField`-typed parameter. The returned background values SHALL depend solely on `(seed, bbox, resolution_deg)`.

#### Scenario: Signature has no CurrentField parameter
- **WHEN** `inspect.signature(build_synthetic_climatology)` is evaluated
- **THEN** no parameter's resolved type is or contains a `CurrentField`-conforming type

#### Scenario: Reproducible under seed
- **WHEN** `build_synthetic_climatology(seed=42, bbox=..., resolution_deg=0.05)` is called twice
- **THEN** the two returned objects have byte-identical `mean_vx_ms`, `mean_vy_ms`, `var_vx_ms2`, `var_vy_ms2` arrays

#### Scenario: Empty constituents list
- **WHEN** the returned object's `constituents` attribute is inspected
- **THEN** it is an empty list

### Requirement: build_climatology_from_harmonic_analysis Fixture-Prep Helper
The system SHALL provide `build_climatology_from_harmonic_analysis(hindcast_path: str, bbox: tuple[float, float, float, float], analysis_window_start: str, analysis_window_end: str, constituents: list[str], output_path: str) -> str` that runs `utide.solve()` per grid cell on the hindcast time series, converts `utide`'s ellipse-form output (`Lsmaj`, `Lsmin`, `theta`, `g`) to per-component `(amp_vx, phase_vx, amp_vy, phase_vy)` pairs, computes residual non-tidal monthly means by subtracting reconstructed tides from the raw series and averaging per month, and writes a canonical harmonic NetCDF to `output_path`. The helper SHALL import `utide` lazily inside the function body so the module stays importable without `utide` installed. The function signature SHALL NOT include any `CurrentField`-typed parameter. The helper SHALL NOT be invoked at scenario-generation or PF runtime; it is a one-time fixture-prep utility.

#### Scenario: Round-trip a synthetic signal with known M2 amplitude and phase
- **WHEN** a synthetic hindcast NetCDF is constructed at a single grid cell with `(u, v)` containing a pure M2 signal of known amplitude (e.g., 0.3 m/s) and phase (e.g., π/4 rad)
- **AND** `build_climatology_from_harmonic_analysis` is run on this NetCDF with constituents `["M2"]`
- **AND** the resulting canonical NetCDF is loaded via `build_climatology_from_harmonic_netcdf`
- **AND** `velocity_at(cell_lat, cell_lon, t_sec=0.0)` is queried
- **THEN** the returned harmonic contribution at `t_sec=0` is within 1e-3 m/s of `0.3 * cos(-π/4) ≈ 0.212` m/s (M2 phase recovered correctly)
- **AND** the same query at a quarter M2 period returns harmonic contribution within 1e-3 m/s of `0.3 * cos(π/2 - π/4) ≈ 0.212` m/s (phase evolution correct)

#### Scenario: Residual monthly background has reduced tidal variance relative to raw signal
- **WHEN** the synthetic hindcast's single cell contains background mean 0.05 m/s + M2 tidal amplitude 0.3 m/s
- **AND** the helper is run with constituents `["M2"]`
- **AND** the resulting NetCDF's background `mean_vx` for the month covering the signal is inspected
- **THEN** the background mean is within 0.02 m/s of 0.05 (tidal component successfully removed, not averaged in)

#### Scenario: Per-cell analysis failure does not abort the whole run
- **WHEN** the helper is run against a hindcast with one cell whose time series is all NaN or too short for Rayleigh separability
- **THEN** the helper writes the canonical NetCDF with zero amplitudes / zero phases at that cell
- **AND** the NetCDF's attributes include a `cell_ok` mask flagging the bad cell
- **AND** the helper does not raise an uncaught exception

#### Scenario: utide not installed triggers explicit error when helper invoked
- **WHEN** `build_climatology_from_harmonic_analysis` is invoked in an environment without `utide` installed
- **THEN** the function raises `ImportError` whose message names `utide` and the `pyproject.toml` optional-dependency group to install

### Requirement: Climatology Source Does Not Import Current Fields
The `climatology_source` module SHALL NOT import any symbol from `rtl.vectors.maritime.current_fields` or `rtl.vectors.maritime.current_fields_real`. An import-linter contract in `pyproject.toml` SHALL enforce this at the module level. A structural test SHALL additionally parse the module AST and assert no `Import` / `ImportFrom` node resolves to the forbidden modules.

#### Scenario: import-linter forbids current_fields import from climatology_source
- **WHEN** `uv run lint-imports` is invoked
- **THEN** a contract "Climatology source does not access current fields" (or equivalent) lists `rtl.vectors.maritime.climatology_source` as a `source_module`
- **AND** both `rtl.vectors.maritime.current_fields` and `rtl.vectors.maritime.current_fields_real` are in its `forbidden_modules`
- **AND** the command exits zero on the current codebase

#### Scenario: AST walk finds no forbidden import
- **WHEN** `tests/maritime/test_climatology_provenance.py`'s AST walk runs over `climatology_source.py`
- **THEN** no `Import` / `ImportFrom` node resolves to `rtl.vectors.maritime.current_fields` or `rtl.vectors.maritime.current_fields_real`

### Requirement: Temporal Honesty of Analysis Window
The canonical harmonic NetCDF's `analysis_window_end` attribute SHALL record the end date of the tide-gauge / hindcast analysis period the harmonics were derived from. The scenario generator SHALL, when `--current-source real` is active, assert that this attribute is strictly before the scenario's deployment date (`--created-at` when provided; otherwise the deployment date embedded in the fixture directory name). On violation, scenario generation SHALL fail with an explicit "temporal-honesty violation: climatology analysis window extends to `<end>` which is not strictly before deployment date `<D>`" error. A documented waiver flag MAY be accepted for regions where strict bounding is infeasible, paired with an explanatory note in the fixture README.

#### Scenario: Primary Salish fixture passes temporal-honesty
- **WHEN** the scenario generator is invoked with the primary Salish harmonic NetCDF (analysis window ending 2023-12-31) and `--created-at 2024-10-15T00:00:00Z`
- **THEN** the check passes

#### Scenario: Analysis window extending past deployment fails
- **WHEN** the scenario generator is invoked with a synthetic test NetCDF whose `analysis_window_end` is `2025-12-31` and `--created-at 2024-10-15T00:00:00Z`
- **THEN** scenario generation fails with an error whose message names the end date and deployment date

### Requirement: Bundled Fixture Documentation
Each bundled `data/real_currents/<fixture>/` directory SHALL include a `README.md` documenting source-product versions, harmonic-analysis parameters (constituents list, analysis window bounds), `utide` version, license citation, temporal-honesty rationale (year bounds), and the reproducible fixture-prep command.

#### Scenario: Primary Salish fixture README documents harmonic analysis
- **WHEN** the primary Salish fixture directory is inspected
- **THEN** `README.md` exists
- **AND** it names CIOPS-SalishSea as the truth source and SalishSeaCast 2007–2023 as the harmonic-analysis source
- **AND** it lists the analyzed constituents (at minimum M2, S2, K1, O1)
- **AND** it includes a reproducible `uv run python -c "from rtl.vectors.maritime.climatology_source import build_climatology_from_harmonic_analysis; ..."` invocation
- **AND** it explicitly states that analysis years exclude the deployment year and later (temporal honesty)
