## Purpose

Deterministic CLI-driven scenario generator. Composes fleet, maps,
current field, and sensor observations into a JSONL stream conforming
to maritime-scenario-schema v1.0. Byte-identical output for identical
arguments (seed, bbox, duration, dt, nodes, created-at).

## Requirements

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

### Requirement: Seed Reproducibility
The scenario generator SHALL produce byte-identical output when
invoked twice with identical `--seed`, `--bbox`, `--duration-hours`,
`--dt-sec`, `--nodes`, and `--created-at` arguments against the same
code version. Two different seed values SHALL produce files that
differ in at least one byte. Changing any time parameter
(`--duration-hours` or `--dt-sec`) SHALL also produce a different
file — these parameters are part of the reproducibility tuple, not
optional ornaments. The `--created-at` flag is informational metadata;
when omitted it defaults to wall-clock now, which makes consecutive
runs differ in the `created_at_utc` field. Tooling that needs
byte-identity (golden-trace tests, regeneration scripts) SHALL pass
an explicit `--created-at` value.

#### Scenario: Same seed + time params + created_at produces byte-identical output
- **WHEN** the CLI is invoked twice with `--seed 42` and identical `--bbox`, `--duration-hours`, `--dt-sec`, `--nodes`, and `--created-at` arguments, producing files `A` and `B`
- **THEN** the byte contents of `A` and `B` are identical (e.g., `hash(A) == hash(B)`)

#### Scenario: Different seed produces different output
- **WHEN** the CLI is invoked once with `--seed 42` and once with `--seed 43`, identical other arguments
- **THEN** the byte contents of the two output files differ

#### Scenario: Different dt_sec produces different output
- **WHEN** the CLI is invoked once with `--dt-sec 60` and once with `--dt-sec 1` (same seed, duration, bbox, nodes)
- **THEN** the byte contents differ (different tick count, different sensor firing pattern)

#### Scenario: Golden trace matches under identical arguments
- **WHEN** the CLI is invoked with the golden-trace arguments (documented seed, bbox, duration-hours, dt-sec)
- **THEN** the output file matches the committed golden trace byte-for-byte

### Requirement: Fleet Composition
The scenario generator SHALL construct exactly the M1 fleet: 2 `AnchorNode`, 4 `BallastDrifterNode`, 4 `PureDrifterNode`. The header's `fleet_composition` field SHALL reflect this. All 10 node_ids SHALL appear in the header's `node_ids` list in a deterministic order that is stable across runs with the same `(seed, bbox, duration_hours, dt_sec, nodes)` tuple (required for byte-identical reproducibility); the ordering is NOT required to group nodes by class — consumers that need per-node class information SHALL read `header.node_classes` rather than rely on positional conventions. The generator SHALL populate `header.node_classes` so that every `node_id` maps to that node's `profile.class_name`. The generator SHALL populate `header.anchor_positions` with one entry per anchor node_id, sourced from the anchor's `MooredPoseSpec.anchor_lat_deg` and `MooredPoseSpec.anchor_lon_deg`. Each anchor node_id in `header.node_ids` SHALL appear as a key in `header.anchor_positions` and conversely; no anchor shall be missing, no non-anchor shall be present.

#### Scenario: Header declares full M1 composition
- **WHEN** a generated scenario's header is inspected
- **THEN** `header.fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}`
- **AND** `len(header.node_ids) == 10`

#### Scenario: node_classes reflects the actual node class of each fleet member
- **WHEN** a scenario is generated with the default bundled fleet
- **THEN** `header.node_classes[node_id] == node.profile.class_name` for every `node_id` in `header.node_ids`, where `node` is the fleet member with that identifier
- **AND** exactly 2 entries map to `"anchor"`, 4 to `"ballast_drifter"`, and 4 to `"pure_drifter"`
- **AND** the `node_ids` whose `node_classes` value is `"anchor"` equal the key set of `header.anchor_positions`

#### Scenario: anchor_positions match MooredPoseSpec values
- **WHEN** a scenario is generated and its header's `anchor_positions` is inspected
- **THEN** each anchor node_id appears as a key
- **AND** each value equals `(spec.anchor_lat_deg, spec.anchor_lon_deg)` from the corresponding `MooredPoseSpec` component on that anchor's profile

### Requirement: Tick Count Matches Duration
The number of tick records SHALL equal `ceil(duration_sec / dt_sec)`
where both `duration_sec` and `dt_sec` come from the CLI arguments
(`--duration-hours` × 3600 and `--dt-sec` respectively, stored on
the scenario header). The first tick SHALL have `t == 0` and
`t_sec == 0.0`. Subsequent ticks SHALL have `t_sec == t * dt_sec` so
wall time advances uniformly.

#### Scenario: 24-hour default at 60-second steps produces 1440 tick records
- **WHEN** the CLI is invoked with default time parameters (`--duration-hours 24 --dt-sec 60`)
- **THEN** the output file contains 1440 tick records (plus 1 header record = 1441 lines total)

#### Scenario: Fine-resolution override
- **WHEN** the CLI is invoked with `--duration-hours 0.25 --dt-sec 1.0` (15 min at 1 Hz)
- **THEN** the output file contains 900 tick records

#### Scenario: Tick indices and times are consistent
- **WHEN** a scenario is parsed
- **THEN** the tick records have `t` values `0, 1, 2, ..., N-1` with no gaps
- **AND** for every tick, `t_sec == t * header.dt_sec` exactly (float equality)

### Requirement: Sensor Firing Respects SensorSpec
The tick loop SHALL query each sensor's `should_sample` before
calling `sample`. If `should_sample` returns False, the sensor SHALL
NOT produce a measurement that tick. No observation record in the
JSONL SHALL violate the declared `max_rate_hz` — for any
`(node_id, sensor)` pair, successive observation timestamps SHALL be
spaced at least `1.0 / max_rate_hz` apart. The effective minimum
firing interval for a sensor SHALL be `max(1.0 / max_rate_hz, dt_sec)` —
a sensor whose configured `max_rate_hz` exceeds `1 / dt_sec` still
cannot fire more than once per tick, because the tick is the
simulation's temporal granularity.

#### Scenario: GPS on anchor fires at most every 300 s
- **WHEN** a scenario is generated with anchor GPS `max_rate_hz = 1.0/300` (once per 5 minutes)
- **THEN** any two consecutive `"gps"` observations for the same anchor are at least 300 s apart in `t_sec`

#### Scenario: Continuous sensor fires at most once per tick
- **WHEN** a scenario is generated with a sensor whose `max_rate_hz` is 10 and `--dt-sec 60`
- **THEN** any two consecutive observations for that sensor on the same node are at least 60 s apart (tick-limited, not rate-limited)

#### Scenario: Continuous sensor fires at its datasheet rate when dt_sec is fine
- **WHEN** a scenario is generated with `--dt-sec 1.0` and a sensor whose `max_rate_hz` is 1.0
- **THEN** the sensor fires on every tick (interval is exactly `dt_sec` = `1 / max_rate_hz`)

### Requirement: Every Profile-Declared Sensor Produces Observations
The tick loop SHALL instantiate and fire every sensor declared in each node's `profile.sensors`. No declared sensor SHALL be silently skipped by the sensor-factory dispatch, and no observation SHALL be produced for a sensor name absent from the owning node's profile. Tests verifying this requirement SHALL choose a scenario duration at least as long as the slowest profile-declared sensor's natural cadence (currently the LoRa TDMA period `comms.tdma_period_sec`, 3600 s in the bundled profiles — so a 2-hour run at the default `--dt-sec 60` guarantees every sensor has fired at least once).

#### Scenario: Every profile sensor fires at least once in a two-hour run
- **WHEN** a scenario is generated with the default bundled fleet, `--duration-hours 2 --dt-sec 60` (120 ticks — long enough to cover the slowest sensor's TDMA/rate cycle), and `ScenarioReader` iterates the output
- **THEN** for every `node_id` in `header.node_ids` and every `sensor_name` in that node's `profile.sensors`, at least one `ObservationRecord` exists in the stream with matching `(record.node_id, record.sensor)`
- **AND** no `ObservationRecord` has a `sensor` name that is absent from the owning node's profile (the generator does not fabricate undeclared sensors)

#### Scenario: Each node class contributes its declared sensor suite
- **WHEN** the observation stream from a two-hour scenario is grouped by (node class, sensor name), where the class is resolved via `header.node_classes`
- **THEN** anchor observations cover exactly `{"gps", "imu", "baro", "mag", "lora_toa"}`
- **AND** ballast_drifter observations cover exactly `{"imu", "baro", "mag", "bathy_probe", "lora_toa"}`
- **AND** pure_drifter observations cover exactly `{"imu", "baro", "mag", "bathy_probe", "lora_toa"}`

### Requirement: ObservationRecord Content Preserved From Producing Sensor
Each `ObservationRecord` emitted in the JSONL SHALL carry the in-memory `Measurement` produced by the sensor verbatim — `value`, `unit`, and `noise_sigma` SHALL round-trip from the sensor's output through the generator into the JSONL without corruption, truncation, normalization, or substitution. `noise_sigma` in particular SHALL equal the producing sensor's `SensorSpec.noise_sigma` (for single-measurement sensors) or the producing comms profile's `ranging_sigma_m` (for `lora_toa`), because the PF builds its likelihood model from this field and any discrepancy collapses the likelihood silently.

#### Scenario: ObservationRecord preserves producing sensor's noise_sigma and unit
- **WHEN** a scenario is generated with the default bundled fleet and `ScenarioReader` iterates the output
- **THEN** for every `ObservationRecord` `r` with `(r.node_id, r.sensor) = (node_id, sensor_name)` and `sensor_name != "lora_toa"`, `r.noise_sigma == node.profile.sensor(sensor_name).noise_sigma` where `node` is the fleet member with that identifier
- **AND** for every `r` with `r.sensor == "lora_toa"`, `r.noise_sigma == node.profile.comms.ranging_sigma_m`
- **AND** for every `r`, `r.unit` equals the sensor-type-declared unit string per `maritime-sensors` (`"deg"` for gps, `"m/s^2;rad/s"` for imu, `"Pa"` for baro, `"deg"` for mag, `"m"` for bathy_probe, `"m"` for lora_toa)

#### Scenario: GPS observation value is within noise of the anchor's surveyed position
- **WHEN** a scenario is generated with the default bundled fleet (anchors at known `MooredPoseSpec` coordinates) and at least one GPS observation is emitted for an anchor whose surveyed position is `(anchor_lat, anchor_lon)` in degrees
- **THEN** the observation record's `value` tuple `(lat_meas, lon_meas)` satisfies `|lat_meas - anchor_lat| <= 3 * sigma_deg_lat` and `|lon_meas - anchor_lon| <= 3 * sigma_deg_lon`, where `sigma_deg_lat` and `sigma_deg_lon` are the GPS `noise_sigma` converted from metres to degrees at `anchor_lat` (confirms the in-memory `Measurement.value` flows into the JSONL without corruption)

### Requirement: LoRa Links Recorded Per Attempt
The tick loop SHALL gate LoRa ranging by a TDMA cycle: at each tick where
`t_sec - last_lora_cycle_t >= comms.tdma_period_sec`, every unique
inter-node pair (i < j) SHALL attempt one ranging round, and `last_lora_cycle_t`
SHALL be updated to the current tick. On non-cycle ticks, no link records or
`lora_toa` observations SHALL be emitted. On a cycle tick, every attempt
SHALL be recorded in `lora_links` with `status` ∈ {`"success"`, `"dropped"`,
`"out_of_range"`} regardless of outcome. A successful attempt SHALL
additionally produce **two** observation records with `sensor == "lora_toa"`
— one attributed to each end of the pair — both carrying the same
`noisy_range` (per `LoraLinkOutcome`, both ends derive the range from the
same RTT). Dropped or out-of-range attempts SHALL NOT produce observation
records. Distance SHALL be computed in the local ENU frame the rest of the
state vector lives in (`sqrt(Δeast² + Δnorth²)`); for the small bboxes used
in M1, ENU planar distance is within float tolerance of geodesic distance.

#### Scenario: Cycle tick emits one link per pair plus 2 obs per success
- **WHEN** a scenario is generated with the default bundled 10-node fleet and the tick at `t_sec` is a TDMA cycle (`t_sec - last_lora_cycle_t >= comms.tdma_period_sec`)
- **THEN** that tick's `lora_links` array has exactly 45 entries (one per unique pair, `binom(10, 2) = 45`)
- **AND** the count of `lora_toa` observations that tick equals `2 *` the count of link records with `status == "success"`
- **AND** for every successful link between `node_a` and `node_b`, the obs stream contains exactly two `lora_toa` records, one with `node_id == node_a` and one with `node_id == node_b`

#### Scenario: Non-cycle tick emits no LoRa records
- **WHEN** the tick at `t_sec` is not a TDMA cycle (`t_sec - last_lora_cycle_t < comms.tdma_period_sec`)
- **THEN** that tick's `lora_links` array is empty
- **AND** no observation record that tick has `sensor == "lora_toa"`

#### Scenario: Out-of-range pair yields only link record
- **WHEN** a cycle tick attempts ranging between two nodes more than `comms.max_range_m` apart
- **THEN** their link record has `status == "out_of_range"` and `range_m is None`
- **AND** no observation record is produced for that pair that tick

#### Scenario: Successful link range_m matches ENU planar distance within sigma
- **WHEN** a scenario is generated with the default bundled fleet and at least one cycle tick contains a `LoraLinkRecord` with `status == "success"` between `node_a` and `node_b`
- **AND** the truth positions of `node_a` and `node_b` for that tick are read via `ScenarioTruthReader`
- **THEN** `link.range_m` is within `3 * comms.ranging_sigma_m` of the ENU planar distance (`sqrt((east_a - east_b)² + (north_a - north_b)²)`) between the two truth positions (confirms the sensor-layer `Measurement.value` flows through the generator into the link record without corruption or truncation)
- **AND** for the same tick and the same `(node_a, node_b)` pair, both `lora_toa` observation records (one per end) have `value[0] == link.range_m` exactly — the two ends share the same RTT and the link-record `range_m` is the same value, per design D9

### Requirement: Node Truth Recorded in Tick
Each tick record SHALL contain a `nodes` field mapping `node_id` to the node's truth state as a flat list of floats matching the node's `StateLayout`. This field is consumed by `ScenarioTruthReader` for validation tooling and stripped by `ScenarioReader` for PF consumption. The values the generator writes into this slot SHALL be the propagated truth state — not placeholder zeros, not the initial state — reflecting the tick loop's `propagate_truth` output at the position the node actually occupies.

#### Scenario: Truth is present for every node every tick
- **WHEN** a scenario is generated and `ScenarioTruthReader` iterates it
- **THEN** every yielded tick view has `node_truth` populated for all 10 node_ids
- **AND** each truth array's length equals the node's `layout.state_dim`

#### Scenario: Truth is stripped by ScenarioReader
- **WHEN** the same scenario is iterated via `ScenarioReader`
- **THEN** no yielded view has access to truth state (attribute access raises or the attribute does not exist)

#### Scenario: Truth surface_current reflects the current field at each node's position
- **WHEN** a scenario is generated with a non-trivial `SyntheticEddyField` (uniform mean flow plus at least one eddy whose centre lies inside the bbox) and the default M1 fleet, whose anchors are placed at distinct bbox corners (so the two anchors sit at demonstrably different lat/lon)
- **AND** `ScenarioTruthReader` iterates the resulting file
- **THEN** for at least one tick, the `surface_current` slot of `node_truth[anchor_a_id]` differs from `node_truth[anchor_b_id]` (confirming the field is sampled at each node's actual position, not at a fixed origin)
- **AND** for every node at every tick, the two-component `surface_current` slice equals `field.velocity_at(node_lat, node_lon, t_sec)` within float tolerance, where `(node_lat, node_lon)` is read from the same tick's `node_truth` position slot (via `enu_origin` in `SensorEnv`)

#### Scenario: Truth position advances under uniform current
- **WHEN** a scenario is generated with a uniform current field `(vx=0.1, vy=0)` and a pure drifter starting at a known initial position, with `--dt-sec 60 --duration-hours 0.1` (6 ticks)
- **AND** `ScenarioTruthReader` yields ticks `0` and `5`
- **THEN** the drifter's east-component position slot at tick 5 has advanced by approximately `0.1 * 5 * 60 = 30` metres relative to tick 0, within integration tolerance (three times the per-step process-noise sigma)
- **AND** the north-component position slot has advanced by approximately zero metres relative to tick 0 within the same tolerance

### Requirement: Onboard Map Distributed As Scenario Sidecar
The scenario generator SHALL serialize the degraded onboard map to a sidecar file alongside the main JSONL scenario output, and the scenario header SHALL carry a relative-path reference to that sidecar. `ScenarioReader` and `ScenarioTruthReader` SHALL provide an `onboard_map()` accessor that loads and returns the `RegionalMap` from the sidecar; the PF consumes the onboard map through that accessor. The PF SHALL NOT reconstruct the onboard map from seed/fidelity parameters and SHALL NOT access the truth map; the one authoritative onboard-map artifact is the sidecar. The generator-side function `make_onboard_map(truth_map, fidelity, seed)` from `maritime-map-payload` SHALL remain the only call site that takes `truth_map`, and SHALL NOT be imported by any PF module (enforced by the import-linter contract in `project-infra-import-linter`).

#### Scenario: Header carries onboard_map_path
- **WHEN** a scenario header is inspected
- **THEN** `header.onboard_map_path` is a string naming the sidecar file path relative to the scenario file's directory

#### Scenario: Reader's onboard_map accessor loads the sidecar
- **WHEN** `ScenarioReader(path).onboard_map()` is called on a scenario whose sidecar exists
- **THEN** a `RegionalMap` is returned whose contents match the onboard map produced by the generator

#### Scenario: Missing sidecar raises at access time
- **WHEN** `ScenarioReader(path).onboard_map()` is called on a scenario whose sidecar file is missing
- **THEN** the accessor raises `FileNotFoundError` naming the expected sidecar path

#### Scenario: Two readers return equivalent onboard maps
- **WHEN** `ScenarioReader(path).onboard_map()` and `ScenarioTruthReader(path).onboard_map()` are both called on the same scenario
- **THEN** the returned `RegionalMap` instances are structurally equal (same bathymetry grid, same coastline polygons, same climatology)

### Requirement: Golden Trace Committed and Regenerable
The repository SHALL include a committed golden-trace fixture at `tests/maritime/golden_trace/m1_tiny.jsonl` with known parameters (seed, bbox, duration, dt-sec, created-at). A `tests/maritime/regenerate_golden_trace.py` script SHALL rebuild the fixture by invoking the CLI with those parameters. The committed fixture size SHALL be under 15 MB. (Earlier drafts pinned 100 KB; that cap assumed a 3-node fleet, which `--nodes 10` rules out — at 10 nodes × 45 LoRa pairs per TDMA cycle, the spec D6 fixture parameters of `--duration-hours 0.25 --dt-sec 1.0` produce a multi-megabyte file. 15 MB keeps the fixture small enough for git but realistic enough to exercise the tick loop end-to-end.)

#### Scenario: Golden trace exists and is small
- **WHEN** the test suite runs
- **THEN** `tests/maritime/golden_trace/m1_tiny.jsonl` exists
- **AND** its size is less than 15 MB

#### Scenario: Regeneration script reproduces the committed fixture
- **WHEN** `regenerate_golden_trace.py` is invoked
- **THEN** it writes a file byte-identical to the committed fixture

#### Scenario: Generator output matches golden trace with matching args
- **WHEN** the CLI is invoked with the same parameters documented in `regenerate_golden_trace.py`
- **THEN** `assert_golden_trace_matches` passes on the output
