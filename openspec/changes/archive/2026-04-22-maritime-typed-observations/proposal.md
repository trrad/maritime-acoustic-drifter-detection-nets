## Why

`ObservationRecord` collapses six structurally different sensor outputs into
one shape with a discriminant string and a variable-length tuple:

| sensor | what it is | shoehorned into |
|---|---|---|
| gps | (lat, lon) | `value: (float, float)` + `unit: "deg"` |
| imu | (accel_xyz, gyro_xyz) | `value: (float,)*6` + `unit: "m/s^2;rad/s"` (two units in one string) |
| baro | pressure | `value: (float,)` |
| mag | heading | `value: (float,)` |
| bathy_probe | depth | `value: (float,)` |
| lora_toa | range to a partner node | `value: (float,)` — partner identity lost |

Three problems land in pf-float as direct consequences:

1. **`lora_toa` cannot be processed by the PF as written.** The pf-float
   spec assumes `obs.partner_id` exists ("when the observation's partner
   `node_id` appears as a key in `header.anchor_positions`"). It doesn't.
   Each successful pair emits two obs records each carrying only
   `value=(noisy_range,)` and the receiving node's id; the partner is
   dropped on the floor. The PF would have to cross-reference
   `tick.lora_links` to recover it.
2. **Stringly-typed dispatch instead of pyright-checked unions.**
   Every consumer matches on `obs.sensor` to figure out what `obs.value`
   means. Pyright cannot help. New sensors land as new strings + new
   tuple-position conventions.
3. **IMU joint sigma is wrong.** `SensorSpec.noise_sigma=0.01` is
   applied identically to accelerometer (m/s²) and gyro (rad/s)
   channels — different physical units, different real-world noise
   characteristics. The `unit="m/s^2;rad/s"` already telegraphs that
   one shape can't carry both.

## What Changes

- Replace `ObservationRecord` with a sealed union of typed records — one
  per sensor — in `rtl/vectors/maritime/scenario_schema.py`:
  - `GPSObservation` (`lat_deg`, `lon_deg`, `noise_sigma_m`)
  - `IMUObservation` (`accel_xyz`, `gyro_xyz`, `accel_noise_sigma_ms2`,
    `gyro_noise_sigma_rad_s`)
  - `BaroObservation` (`pressure_pa`, `noise_sigma_pa`)
  - `MagObservation` (`heading_deg`, `noise_sigma_deg`)
  - `BathyProbeObservation` (`depth_m`, `noise_sigma_m`)
  - `LoraTOAObservation` (`partner_id`, `range_m`, `noise_sigma_m`)
  - All carry `t_sec` and `node_id`.
  - Type alias: `Observation = GPSObservation | IMUObservation | BaroObservation | MagObservation | BathyProbeObservation | LoraTOAObservation`.
- JSONL records carry a `"type"` discriminant matching the lowercase
  sensor name (`"gps"`, `"imu"`, ..., `"lora_toa"`). The legacy
  `"sensor"` field and the variable-length `"value"` tuple are gone.
- Reader (`_parse_observations` in `_scenario_parse.py`) discriminates
  on `"type"` and returns the appropriate typed record. Unknown
  discriminants raise `ValueError` (existing contract preserved).
- Generator (`gen_maritime_scenario.py`) constructs the appropriate
  typed record per sensor at emit time. The generator already has the
  partner identity available in the lora pair loop, so populating
  `LoraTOAObservation.partner_id` is a free upgrade.
- `Measurement` (sensor-module internal type) is unchanged — sensors
  still emit a single tuple-shaped value. The conversion from
  `Measurement` to typed `Observation` happens in the generator's emit
  step, where the sensor name discriminates.
- `IMUSensor.sample` is updated to apply `accel_noise_sigma_ms2` to the
  accel channels and `gyro_noise_sigma_rad_s` to the gyro channels
  (sourced from new `SensorSpec` fields). Existing single-`noise_sigma`
  IMU specs migrated to dual sigmas in the bundled profile (current
  bundled value `0.01` interpreted as both, preserving Tier 4 byte
  identity for non-IMU paths).
- `ObservationTickView.observations` becomes
  `tuple[Observation, ...]` (the union type). Iteration is unchanged;
  consumers dispatch via `match` / `isinstance`.
- Golden trace fixture is regenerated under the new schema.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `maritime-scenario-schema` — Requirement: ObservationRecord Structure
  is removed and replaced with one Requirement per sensor type plus a
  Requirement for the discriminated-union dispatch contract.
- `maritime-sensors` — Requirement: IMU Sensor Output is updated to
  separate accel and gyro noise sigmas; `SensorSpec` for IMU carries
  both fields.

## Impact

- **Modified files:** `rtl/vectors/maritime/scenario_schema.py`,
  `rtl/vectors/maritime/_scenario_parse.py`,
  `rtl/vectors/maritime/scenario_truth_schema.py` (only the import
  surface; truth view's observations field is the same union),
  `rtl/vectors/maritime/gen_maritime_scenario.py`,
  `rtl/vectors/maritime/sensors.py` (IMU sigma split),
  `rtl/vectors/maritime/platform_profile.py` (IMU SensorSpec
  fields), bundled IMU profile noise values.
- **New files:** none — this is a schema rework, not a new module.
- **Modified tests:** `tests/maritime/test_scenario_schema.py`,
  `tests/maritime/test_scenario_truth_schema.py`,
  `tests/maritime/test_scenario_gen.py`,
  `tests/maritime/test_sensors.py` (IMU sigma split tests).
- **Golden trace:** regenerated. Same `--seed`, same `--bbox`, same
  `--created-at`, but different bytes because the obs record shape
  changed.
- **Frozen baseline:** untouched.
- **Downstream consumers:** `maritime-pf-float` (Tier 5, not yet
  applied) drafts directly against the typed records — its weight
  stage becomes a `match` statement that pyright exhaustively checks.
- **M2 outlook:** new sensor types (acoustic events, etc.) land as
  new typed records, not as new tuple-position conventions.

## Out of Scope

- Adding station-keeping / closed-loop control (verified absent in M1
  per `dynamics.py:63-64` — ballast pump is dormant). If wanted, that
  is a separate change.
- Restructuring `lora_links` (dropping the field, merging with obs,
  per-node timestamps for clock drift). The typed `LoraTOAObservation`
  carrying `partner_id` makes the links array redundant for the PF, but
  the dashboard still wants the dropped/out-of-range entries for mesh
  connectivity rendering. Keep `lora_links` as the audit trail for
  failed attempts; `LoraTOAObservation` is the primary record for
  successful ranging.
- Collapsing `Measurement` into the typed records. The sensor module's
  internal shape is fine as-is; conversion at the generator boundary is
  cheap and keeps the sensor-module surface area small.
