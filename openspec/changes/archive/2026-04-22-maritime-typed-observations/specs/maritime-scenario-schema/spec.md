## REMOVED Requirements

### Requirement: ObservationRecord Structure
**Reason:** The single-shape `ObservationRecord` collapses six structurally
different sensor outputs (different dimensionality, different units,
different cardinality for `lora_toa` which is pair-shaped) into one record
with a discriminant string and a variable-length tuple. Replaced by a
sealed union of sensor-specific typed records — see "Typed Observation
Records — Per-Sensor Shape" and "Observation Union and Dispatch Contract".

**Migration:** Every consumer that switched on `obs.sensor` and indexed
into `obs.value[k]` is replaced by a `match` over the `Observation` union
(or `isinstance` checks). Each branch operates on a typed record with
named fields. The legacy `noise_sigma` scalar field is replaced by
sensor-appropriate fields (`noise_sigma_m`, `noise_sigma_pa`,
`noise_sigma_deg`, and the IMU split into `accel_noise_sigma_ms2` /
`gyro_noise_sigma_rad_s`). The legacy `unit` string field is dropped —
units are implicit in the typed record's class and field names.

## ADDED Requirements

### Requirement: Typed Observation Records — Per-Sensor Shape
The system SHALL define one frozen dataclass per sensor type, each
carrying exactly the fields needed by that sensor's downstream
likelihood model. Each record SHALL carry `t_sec: float` and
`node_id: str`. Sensor-specific fields SHALL be:

- `GPSObservation`: `lat_deg`, `lon_deg`, `noise_sigma_m`
- `IMUObservation`: `accel_xyz: tuple[float, float, float]`,
  `gyro_xyz: tuple[float, float, float]`,
  `accel_noise_sigma_ms2: float`, `gyro_noise_sigma_rad_s: float`
- `BaroObservation`: `pressure_pa`, `noise_sigma_pa`
- `MagObservation`: `heading_deg`, `noise_sigma_deg`
- `BathyProbeObservation`: `depth_m`, `noise_sigma_m`
- `LoraTOAObservation`: `partner_id: str`, `range_m`, `noise_sigma_m`

Each record SHALL validate its fields in `__post_init__`:
- GPS: `-90 <= lat_deg <= 90`, `-180 <= lon_deg <= 180`,
  `noise_sigma_m > 0`.
- IMU: both sigmas positive.
- Baro: `pressure_pa > 0`, `noise_sigma_pa > 0`.
- Mag: `0 <= heading_deg < 360`, `noise_sigma_deg > 0`.
- BathyProbe: `depth_m >= 0`, `noise_sigma_m > 0`.
- LoraTOA: `range_m >= 0`, `partner_id != node_id`, `noise_sigma_m > 0`.

#### Scenario: GPSObservation construction with valid fields
- **WHEN** `GPSObservation(t_sec=5.0, node_id="anchor_01", lat_deg=48.6, lon_deg=-123.5, noise_sigma_m=1.5)` is constructed
- **THEN** the instance is returned with matching field values
- **AND** the instance is immutable (frozen dataclass)

#### Scenario: GPSObservation rejects out-of-range latitude
- **WHEN** `GPSObservation(..., lat_deg=95.0, ...)` is constructed
- **THEN** `ValueError` is raised naming the offending field

#### Scenario: IMUObservation carries separate accel and gyro sigmas
- **WHEN** an `IMUObservation` is constructed with
  `accel_noise_sigma_ms2=0.05` and `gyro_noise_sigma_rad_s=0.005`
- **THEN** both sigmas are accessible as separate fields
- **AND** there is no joint `noise_sigma` field

#### Scenario: LoraTOAObservation requires partner_id distinct from node_id
- **WHEN** `LoraTOAObservation(..., node_id="n00", partner_id="n00", ...)` is constructed
- **THEN** `ValueError` is raised — a node cannot range against itself

#### Scenario: LoraTOAObservation accepts non-anchor partner without complaint
- **WHEN** a `LoraTOAObservation` is constructed with `partner_id` that is not an anchor
- **THEN** construction succeeds — the schema does not know about anchor identity; the M1 anchor-only filter is applied downstream by the PF (per `maritime-pf-float`'s "LoRa TOA Anchor-Only Filter" requirement)

#### Scenario: BathyProbeObservation rejects negative depth
- **WHEN** `BathyProbeObservation(..., depth_m=-5.0, ...)` is constructed
- **THEN** `ValueError` is raised

### Requirement: Observation Union and Dispatch Contract
The system SHALL define a type alias
`Observation = GPSObservation | IMUObservation | BaroObservation | MagObservation | BathyProbeObservation | LoraTOAObservation`.
`ObservationTickView.observations` and `TruthTickView.observations`
SHALL both be typed as `tuple[Observation, ...]`. Consumers SHALL
dispatch on the concrete type via `match` statement or `isinstance`,
not on a string discriminant. The schema SHALL NOT define a common
base class beyond the union — each member is structurally distinct.

#### Scenario: Reader yields union members, not raw dicts
- **WHEN** a `ScenarioReader` iterates a valid file with one obs of each sensor type
- **THEN** the yielded `ObservationTickView.observations` tuple contains one instance of each typed record class
- **AND** each instance is one of the six `Observation` union members

#### Scenario: Match statement over Observation is exhaustive
- **WHEN** consumer code matches over `Observation` with a case for each member
- **THEN** pyright (in strict mode) reports the match as exhaustive — no `case _` fallthrough required

### Requirement: JSONL Discriminant Encoding
Each observation record in the JSONL SHALL carry a `"type"` key whose
value identifies the typed record class:
- `"gps"` → `GPSObservation`
- `"imu"` → `IMUObservation`
- `"baro"` → `BaroObservation`
- `"mag"` → `MagObservation`
- `"bathy_probe"` → `BathyProbeObservation`
- `"lora_toa"` → `LoraTOAObservation`

Reader implementations (`ScenarioReader`, `ScenarioTruthReader`) SHALL
discriminate on `"type"` and return the appropriate typed record.
Records with unknown `"type"` SHALL raise `ValueError` naming the
offending discriminant. The legacy `"sensor"` and `"value"` keys
SHALL NOT be emitted by the generator and SHALL NOT be silently
accepted by readers.

#### Scenario: Reader parses each known type into the matching record class
- **WHEN** a tick record contains observations with `"type": "gps"`, `"type": "imu"`, ..., `"type": "lora_toa"`
- **THEN** the parsed `observations` tuple contains one instance of each corresponding typed class

#### Scenario: Reader rejects unknown type discriminant
- **WHEN** a tick record contains `{"type": "sonar", ...}`
- **THEN** parsing raises `ValueError` naming `"sonar"` and the supported set

#### Scenario: Reader rejects legacy schema records
- **WHEN** a tick record contains `{"sensor": "gps", "value": [48.6, -123.5], "noise_sigma": 1.5, ...}` (legacy v1.0 shape, no `"type"` key)
- **THEN** parsing raises `ValueError` — the legacy single-shape format is no longer accepted (no implicit migration)

### Requirement: ObservationRecord Field Names Carry Units
Each typed observation record's value field name SHALL include the
physical unit as a suffix (`lat_deg`, `lon_deg`, `pressure_pa`,
`heading_deg`, `depth_m`, `range_m`, `accel_xyz`, `gyro_xyz`) or have
a clearly typed numeric tuple. Each noise sigma field name SHALL
include the unit suffix it parameterizes (`noise_sigma_m`,
`noise_sigma_pa`, `noise_sigma_deg`, `accel_noise_sigma_ms2`,
`gyro_noise_sigma_rad_s`). The legacy joint `unit: str` field SHALL
NOT exist on any typed record — units live in the field names, not
in a discriminant string.

#### Scenario: No record carries a unit string field
- **WHEN** any of the six typed records is inspected for fields
- **THEN** none of them has a field named `unit` or `noise_unit`

#### Scenario: Sigma field names indicate the parameterized unit
- **WHEN** an `IMUObservation` is inspected
- **THEN** sigma fields are named `accel_noise_sigma_ms2` and `gyro_noise_sigma_rad_s` — the unit suffix disambiguates which channel the sigma applies to
