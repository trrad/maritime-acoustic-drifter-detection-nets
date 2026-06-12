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
  override for byte-identical reproducibility pinning.
- `--mean-flow-east-ms` (float, default 0.0) — bulk eastward flow
  (m/s) injected into the truth current field.
- `--mean-flow-north-ms` (float, default 0.0) — bulk northward flow
  (m/s).
- `--tidal-amplitude-ms` (float, default 0.0) — peak tidal current
  amplitude (m/s); 0 disables the tide component.
- `--tidal-period-sec` (float, default 44712.0 ≈ M2 lunar semidiurnal).
- `--tidal-direction-deg` (float, default 0.0) — compass direction of
  the tidal flood.
- `--eddy` (repeatable string
  `lat,lon,radius_m,peak_ms,cyclonic`) — adds a rotating Gaussian
  eddy to the field. Repeat the flag for multiple eddies.
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
unknown names. The CLI SHALL write a valid JSONL scenario
file conforming to `maritime-scenario-schema` v1.0 to the `--out`
path. The header record SHALL include both `duration_sec`
(= duration-hours × 3600) and `dt_sec` so readers can interpret tick
spacing without depending on the CLI arguments.

#### Scenario: CLI writes a valid scenario file with default time parameters
- **WHEN** `gen_maritime_scenario.py --seed 42 --bbox 48.4,-123.8,49.2,-123.2 --nodes 10 --out /tmp/s.jsonl` is run (defaults: 24 hours at 60-second steps)
- **THEN** the command exits with status 0
- **AND** `/tmp/s.jsonl` exists and is a valid JSONL file
- **AND** `ScenarioReader('/tmp/s.jsonl').header().schema_version == "1.0"`
- **AND** the parsed header reports `duration_sec == 86400.0` and `dt_sec == 60.0`

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
