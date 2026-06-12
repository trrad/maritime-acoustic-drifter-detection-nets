## MODIFIED Requirements

### Requirement: CLI Invocation
The system SHALL provide a CLI at
`rtl/vectors/maritime/gen_maritime_scenario.py` that accepts:

- `--seed` (int)
- `--bbox` (four comma-separated floats: south,west,north,east)
- `--duration-hours` (float, default 24.0) — scenario duration
- `--dt-sec` (float, default 60.0) — per-tick time step; coarse default
  reflects operational scale (multi-day drifter deployments, LoRa
  TDMA cycles measured in hours); override to finer values (e.g., 1.0
  or sub-second) for TDMA-slot or acoustic-TDOA tuning work
- `--nodes` (int)
- `--out` (path)
- `--created-at` (ISO 8601 string, optional): header timestamp
  override for byte-identical reproducibility pinning. Also used as
  the deployment date for the temporal-honesty check against the
  climatology source when `--current-source real`.
- `--mean-flow-east-ms` (float, default 0.0) — bulk eastward flow
  (m/s) injected into the truth current field when
  `--current-source synthetic`.
- `--mean-flow-north-ms` (float, default 0.0) — bulk northward flow
  (m/s) injected into the synthetic truth field.
- `--tidal-amplitude-ms` (float, default 0.0) — peak tidal current
  amplitude (m/s) for the synthetic truth field; 0 disables the tide
  component.
- `--tidal-period-sec` (float, default 44712.0 ≈ M2 lunar semidiurnal).
- `--tidal-direction-deg` (float, default 0.0) — compass direction of
  the synthetic tidal flood.
- `--eddy` (repeatable string
  `lat,lon,radius_m,peak_ms,cyclonic`) — adds a rotating Gaussian
  eddy to the synthetic field. Repeat the flag for multiple eddies.
- `--current-source` (choice: `synthetic` | `real`, default `synthetic`)
  — selects whether the truth field is the synthetic `SyntheticEddyField`
  or a real-data `RealCurrentField`. CI and reproducibility tests
  default to `synthetic` (no NetCDF dependency, no network).
- `--current-data-path` (path, REQUIRED when `--current-source real`) —
  NetCDF file providing the truth current field. Format-polymorphic via
  the `RealCurrentField` loader (CIOPS-SalishSea, CIOPS-West, CMEMS
  analysis-forecast, etc.).
- `--climatology-data-path` (path, REQUIRED when `--current-source real`)
  — NetCDF file providing the onboard climatology, matching the
  canonical harmonic-table schema (per-cell tidal constituents +
  non-tidal background; see `maritime-climatology-source` for the
  schema contract). Loaded via
  `build_climatology_from_harmonic_netcdf`.
- `--enable-sensors` (comma-separated names, optional) — sensor
  allow-list. When set, only named sensors emit observations in the
  scenario; all others are silently skipped at generation time.
  Default: all sensors emit. LoRa TDMA cycles only fire when
  `lora_toa` is in the set.
- `--lora-period-sec` (float, optional) — override the bundled LoRa
  TDMA cycle period in seconds. Default: bundled M1 profile value
  (3600s — power-budget regime). Set to ~60 for obs-rich accuracy
  testing.
- `--gps-period-sec` (float, optional) — override the bundled GPS
  sampling period for anchors in seconds. Default: bundled M1 profile
  value (3600s).

The CLI SHALL reject any `--nodes` value other than 10 in M1 with a
clear error message. The CLI SHALL reject non-positive
`--duration-hours` or `--dt-sec` values with explicit error messages
(not silent clamping). The CLI SHALL reject `--enable-sensors` values
containing unknown sensor names with an explicit error naming the
unknown names. When `--current-source real`, the CLI SHALL require both
`--current-data-path` and `--climatology-data-path` and SHALL reject
invocations missing either flag with an explicit error naming the
missing flag. The CLI SHALL write a valid JSONL scenario file
conforming to `maritime-scenario-schema` v1.0 to the `--out` path.
The header record SHALL include both `duration_sec`
(= duration-hours × 3600) and `dt_sec` so readers can interpret tick
spacing without depending on the CLI arguments.

#### Scenario: CLI writes a valid scenario file with default time parameters
- **WHEN** `gen_maritime_scenario.py --seed 42 --bbox 48.4,-123.8,49.2,-123.2 --nodes 10 --out /tmp/s.jsonl` is run (defaults: 24 hours at 60-second steps, `--current-source synthetic`)
- **THEN** the command exits with status 0
- **AND** `/tmp/s.jsonl` exists and is a valid JSONL file
- **AND** `ScenarioReader('/tmp/s.jsonl').header().schema_version == "1.0"`
- **AND** the parsed header reports `duration_sec == 86400.0` and `dt_sec == 60.0`

#### Scenario: CLI honors custom --dt-sec and --duration-hours
- **WHEN** the CLI is invoked with `--duration-hours 0.25 --dt-sec 1.0` (15-minute fine-resolution run)
- **THEN** the header reports `duration_sec == 900.0` and `dt_sec == 1.0`
- **AND** the tick count is 900

#### Scenario: CLI rejects unsupported node count
- **WHEN** the CLI is invoked with `--nodes 5`
- **THEN** the command exits with non-zero status
- **AND** stderr explicitly states that only `--nodes 10` is supported in M1

#### Scenario: CLI rejects non-positive time parameters
- **WHEN** the CLI is invoked with `--duration-hours 0` or `--dt-sec 0` or negative values
- **THEN** the command exits with non-zero status
- **AND** stderr names the offending flag and value (explicit error, not silent clamping)

#### Scenario: CLI rejects missing required flags
- **WHEN** the CLI is invoked without `--out`
- **THEN** the command exits with non-zero status
- **AND** stderr indicates the missing flag

#### Scenario: CLI with --current-source real requires both NetCDF path flags
- **WHEN** the CLI is invoked with `--current-source real` but without `--current-data-path` or without `--climatology-data-path`
- **THEN** the command exits with non-zero status
- **AND** stderr explicitly names the missing flag(s)

#### Scenario: CLI with --current-source real uses real-data loader and independent climatology
- **WHEN** the CLI is invoked with `--current-source real --current-data-path <bundled truth NetCDF> --climatology-data-path <bundled harmonic NetCDF>` against a bundled fixture
- **THEN** the command exits with status 0
- **AND** the scenario's truth current field is a `RealCurrentField` loaded from the truth NetCDF (verified by inspecting `field.product_family`)
- **AND** the onboard map's climatology is a `HarmonicClimatology` loaded from the harmonic NetCDF
- **AND** truth current values at drifter positions differ from climatology `(mean_vx, mean_vy)` at the same positions by more than 0.05 m/s for at least one tick-position pair

#### Scenario: CLI rejects same-inode real-data paths
- **WHEN** the CLI is invoked with `--current-source real --current-data-path A.nc --climatology-data-path A.nc` (the same file for both)
- **THEN** the command exits with non-zero status
- **AND** stderr names "climatology and truth must be independent data products" (or equivalent) and identifies the offending inode

#### Scenario: CLI rejects same product-family-and-dataset-id real-data paths
- **WHEN** the CLI is invoked with `--current-source real --current-data-path A.nc --climatology-data-path B.nc` where A and B are different files but both resolve to the same `(product_family, dataset_id)` tuple via NetCDF metadata
- **THEN** the command exits with non-zero status
- **AND** stderr names the shared `product_family` and `dataset_id` and reports the "same product, different file" alias

#### Scenario: CLI rejects climatology source whose analysis window extends past --created-at
- **WHEN** the CLI is invoked with `--current-source real --climatology-data-path <climatology whose analysis_window_end attr ends 2025-12-31> --created-at 2024-10-15T00:00:00Z`
- **THEN** the command exits with non-zero status (unless an explicit waiver flag is provided)
- **AND** stderr names the climatology analysis-window end date and the deployment date, citing "temporal-honesty violation"

#### Scenario: Header echoes CLI bbox and seed verbatim
- **WHEN** the CLI is invoked with `--seed 42 --bbox 48.4,-123.8,49.2,-123.2 --duration-hours 0.25 --dt-sec 1.0 --nodes 10 --out /tmp/s.jsonl`
- **THEN** `ScenarioReader('/tmp/s.jsonl').header().bbox == (48.4, -123.8, 49.2, -123.2)`
- **AND** `ScenarioReader('/tmp/s.jsonl').header().seed == 42`

#### Scenario: Changing --seed changes header.seed
- **WHEN** the CLI is invoked twice with identical arguments except `--seed 42` and `--seed 43`
- **THEN** the first file's parsed header has `seed == 42`
- **AND** the second file's parsed header has `seed == 43`

#### Scenario: --mean-flow-east-ms injects bulk eastward flow into truth surface_current
- **WHEN** the CLI is invoked with `--mean-flow-east-ms 0.15 --mean-flow-north-ms 0.0 --tidal-amplitude-ms 0.0` and no `--eddy` flags (zero tidal, no eddies)
- **THEN** every drifter node's truth `surface_current` slot at tick 1 equals `(0.15, 0.0)` within float tolerance
- **AND** the east-component of every drifter's truth position advances at ~0.15 m/s over subsequent ticks

#### Scenario: --mean-flow-north-ms injects bulk northward flow
- **WHEN** the CLI is invoked with `--mean-flow-east-ms 0.0 --mean-flow-north-ms 0.10 --tidal-amplitude-ms 0.0` and no eddies
- **THEN** every drifter node's truth `surface_current` slot at tick 1 equals `(0.0, 0.10)` within float tolerance

#### Scenario: --tidal-amplitude-ms + --tidal-period-sec + --tidal-direction-deg add a tidal component
- **WHEN** the CLI is invoked with `--tidal-amplitude-ms 0.2 --tidal-period-sec 43200 --tidal-direction-deg 90`
- **THEN** the truth `surface_current` at any drifter's position has a sinusoidal component of peak amplitude 0.2 m/s oriented along the 90° (north) axis
- **AND** the sinusoid's period matches the requested `tidal_period_sec`

#### Scenario: --eddy injects a rotating current perturbation
- **WHEN** the CLI is invoked with `--eddy 48.7,-123.5,5000,0.3,1` and zero mean flow and zero tidal amplitude
- **THEN** a drifter node placed near `(48.7, -123.5)` within the eddy radius sees a truth `surface_current` with non-zero tangential component (cyclonic rotation)
- **AND** a drifter node placed far outside the eddy radius sees near-zero truth `surface_current`

#### Scenario: --enable-sensors restricts observation emission to named sensors
- **WHEN** the CLI is invoked with `--enable-sensors lora_toa,imu`
- **THEN** the scenario's observation stream contains only `sensor=="lora_toa"` and `sensor=="imu"` records
- **AND** no `gps`, `baro`, `mag`, or `bathy_probe` observations appear in the stream
- **AND** LoRa TDMA cycles fire because `lora_toa` is in the allow-list

#### Scenario: --enable-sensors rejects unknown names with explicit error
- **WHEN** the CLI is invoked with `--enable-sensors lora_toa,bogus_sensor`
- **THEN** the CLI exits nonzero
- **AND** stderr names the unknown sensor(s) and lists the supported names

#### Scenario: --lora-period-sec overrides bundled LoRa TDMA cycle
- **WHEN** the CLI is invoked with `--lora-period-sec 60`
- **THEN** every node's `profile.comms.tdma_period_sec` in the scenario is 60.0
- **AND** LoRa TDMA cycles fire every ~60 seconds in the observation stream (instead of the bundled 3600 s default)

#### Scenario: --gps-period-sec overrides bundled anchor GPS cadence
- **WHEN** the CLI is invoked with `--gps-period-sec 60`
- **THEN** every anchor node's `gps` sensor has `max_rate_hz == 1/60` in the scenario header's node profiles

## ADDED Requirements

### Requirement: Synthetic Climatology Has No Truth-Field Dependency
When `--current-source synthetic`, the scenario generator SHALL construct its onboard climatology from a seeded-deterministic procedure that does NOT reference the synthetic truth `CurrentField` object. The construction function SHALL NOT accept a `CurrentField`-typed parameter. The synthetic climatology's grid values SHALL be derived solely from (seed, bbox, grid-resolution) inputs. This preserves the charter invariant "no truth-side object flows into the onboard-side artifact" even in the CI-default path.

#### Scenario: Synthetic construction function signature rejects CurrentField
- **WHEN** `inspect.signature` is applied to the synthetic-path climatology constructor and each parameter's annotation is resolved
- **THEN** no parameter is or contains `CurrentField`, `SyntheticEddyField`, or `RealCurrentField`

#### Scenario: Synthetic climatology diverges from the synthetic truth field
- **WHEN** a synthetic-path scenario is generated with a non-trivial `SyntheticEddyField` (mean flow + eddies + tide) and its climatology is inspected
- **THEN** there exists at least one `(lat, lon, t_sec)` where `|climatology.velocity_at(lat, lon, t_sec)[:2] - field.velocity_at(lat, lon, t_sec)|` exceeds 0.05 m/s (confirms the climatology is NOT `field.velocity_at` time-averaged)

### Requirement: Current-Field Visualization Sidecar
The scenario generator SHALL emit a `current_field_grid.npz` sidecar file alongside the main JSONL output when scenario generation completes. The sidecar SHALL contain four arrays sampled on an `n_grid × n_grid` regular grid across the scenario bbox (default `n_grid = 12`, configurable via `--dashboard-current-grid-npts`):

- `truth_grid_u[t, i, j]` — eastward truth velocity at grid cell `(i, j)` at tick `t`, shape `(n_ticks, n_grid, n_grid)`.
- `truth_grid_v[t, i, j]` — northward truth velocity at the same cells/ticks.
- `clim_grid_u[t, i, j]` — eastward climatology `mean_vx` at grid cell `(i, j)` at tick `t`, shape `(n_ticks, n_grid, n_grid)`. Tick-indexed because `HarmonicClimatology.velocity_at` is time-varying (tidal phase).
- `clim_grid_v[t, i, j]` — northward climatology mean at the same cells/ticks.

Plus two coordinate arrays `grid_lats[n_grid]` and `grid_lons[n_grid]` for rendering. The sidecar path SHALL be recorded in the scenario header (e.g., `header.current_field_grid_path`) alongside `header.onboard_map_path`. The dashboard SHALL load the sidecar and render truth + climatology quiver overlays (see `maritime-dashboard`).

#### Scenario: Sidecar is emitted alongside scenario
- **WHEN** the scenario generator completes successfully
- **THEN** a file named `current_field_grid.npz` (or the path in `header.current_field_grid_path`) exists in the scenario output directory
- **AND** loading it via `numpy.load` returns the four value arrays plus `grid_lats`, `grid_lons`

#### Scenario: Sidecar truth grid has shape (n_ticks, n_grid, n_grid)
- **WHEN** a scenario of `N` ticks is generated with default `n_grid = 12`
- **THEN** `npz["truth_grid_u"].shape == (N, 12, 12)` and `npz["truth_grid_v"].shape == (N, 12, 12)`

#### Scenario: Sidecar truth grid reflects the configured truth field
- **WHEN** a synthetic-path scenario is generated with `--mean-flow-east-ms 0.2 --mean-flow-north-ms 0.0 --tidal-amplitude-ms 0.0` and no eddies
- **THEN** `npz["truth_grid_u"][:, :, :]` is within `1e-9` of `0.2` at every cell and every tick
- **AND** `npz["truth_grid_v"][:, :, :]` is within `1e-9` of `0.0` at every cell and every tick

#### Scenario: Sidecar climatology grid is tick-indexed and reflects tidal evolution
- **WHEN** a scenario is generated with a harmonic climatology covering tidally-active water (e.g., the primary Salish fixture) and its sidecar is inspected
- **THEN** `npz["clim_grid_u"]` and `npz["clim_grid_v"]` have shape `(n_ticks, n_grid, n_grid)`
- **AND** for at least one cell `(i, j)`, `clim_grid_u[:, i, j]` is not constant across ticks (tidal phase evolution visible)

#### Scenario: Sidecar climatology grid is effectively time-invariant in the synthetic path
- **WHEN** a synthetic-path scenario is generated (zero-constituent `HarmonicClimatology`) and its sidecar is inspected
- **THEN** `npz["clim_grid_u"]` and `npz["clim_grid_v"]` are constant across the tick axis for every cell within the same month (may vary across month boundaries if the scenario spans multiple months)
