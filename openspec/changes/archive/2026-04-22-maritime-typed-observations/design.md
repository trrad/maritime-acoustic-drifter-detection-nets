## Context

Tier 4 (`maritime-scenario-gen`) shipped `ObservationRecord` as a single
shape: `(t_sec, node_id, sensor: str, value: tuple[float, ...], unit, noise_sigma)`.
The shape was inherited from the 6 D POC's reference trace where there was
exactly one sensor type. Maritime has six sensor types with different
dimensionality, different units, and — for `lora_toa` — different cardinality
(two endpoints per measurement, not one). Stuffing all of them into one
record meant:

- `lora_toa` lost the partner identity. Tier 5 (`maritime-pf-float`) cannot
  process anchor-only ranging without it.
- IMU collapsed accel and gyro into one sigma despite different units. The
  bundled profile carries `noise_sigma=0.01` and `unit="m/s^2;rad/s"` —
  the semicolon in the unit string is the giveaway that the shape is wrong.
- Every consumer dispatches on the `sensor` string, defeating pyright.

The right shape is a sealed union. Each sensor's record carries exactly the
fields it needs. Pyright catches mistakes at authoring time. Adding new
sensors (acoustic events in M2) lands as new typed records.

## Goals / Non-Goals

**Goals:**
- Each sensor's record is a frozen dataclass with sensor-specific fields and
  no leftover discriminant slots.
- The generator's emit-side conversion is a small per-sensor function — no
  giant `if/elif` over field names.
- The reader's parse-side dispatch is a small per-`type`-discriminant
  function — pyright/mypy can verify the union is exhaustive.
- IMU noise sigma split into `accel_noise_sigma_ms2` and
  `gyro_noise_sigma_rad_s`, both first-class fields on the typed record
  and on `SensorSpec`.
- Backward compatibility is NOT a goal. The schema is internal to this
  research project; no external consumers.

**Non-Goals:**
- Restructuring `lora_links`. Successful ranging now lives in
  `LoraTOAObservation` (one per end, both with `partner_id`); link records
  remain the audit trail for `dropped` / `out_of_range` attempts (and for
  successful pairs, redundantly — the dashboard still wants this for mesh
  connectivity rendering). A future change can collapse `lora_links` into a
  pair-shaped event record if that becomes worth doing.
- Merging `Measurement` into the typed records. Sensor module stays simple;
  conversion at generator boundary stays explicit.
- Adding control-loop / station-keeping concepts. M1 has none; ballast pump
  is dormant in `dynamics.py`. The forward control-flow architecture is
  sketched in `docs/maritime_scenario_harness_plan.md` "Forward: Closed-Loop
  Control Architecture (M2+)" — this typed-observations change is
  deliberately compatible with that sketch (observation, estimation, and
  control stay three separable concerns; the obs schema has no slot for
  actuator state and doesn't need one).

## Decisions

### D1: Sealed union, not a base class

**Choice:** `Observation = GPSObservation | IMUObservation | BaroObservation | MagObservation | BathyProbeObservation | LoraTOAObservation`

**Why:** Python doesn't have proper sealed classes. The `Union` type alias
plus `match` statements gives pyright an exhaustive-check it can verify.
A common base class would invite "use isinstance for dispatch" instead of
`match`, and would tempt new fields onto the base (regressing toward
`ObservationRecord`).

### D2: JSONL discriminant key is `"type"`, not `"sensor"`

**Choice:** Each obs record has `"type": "gps" | "imu" | "baro" | "mag" | "bathy_probe" | "lora_toa"`.

**Why:** The string is a *type tag*, not a sensor name in any meaningful
sense (no consumer parses `"type"` as a sensor identifier separate from
the record class). Using `"type"` for the JSON discriminant matches the
existing `"record_type": "header" | "tick"` pattern at the top level of
the file. Consistent vocabulary at both levels.

### D3: IMU sigmas split

**Choice:** `IMUObservation.accel_noise_sigma_ms2` and
`IMUObservation.gyro_noise_sigma_rad_s` are separate fields. The IMU
`SensorSpec` carries both. `IMUSensor.sample` applies the appropriate
sigma to each channel triple.

**Why:** Accelerometer noise is in m/s² (typical MEMS: 50–500 µg/√Hz
≈ 0.0005–0.005 m/s²); gyro noise is in rad/s (typical MEMS:
0.005–0.05 °/s/√Hz ≈ 1e-4–1e-3 rad/s). One scalar can't represent both.
The bundled profile's current `0.01` is in the right order of magnitude
for both as a starting point — split it but keep `0.01` for both
initially, defer real datasheet values for later.

### D4: Carry `noise_sigma_*` per record, not a global sensor pointer

**Choice:** Every typed observation carries its noise sigma(s) as
fields, just like the legacy `ObservationRecord.noise_sigma`. Field
names include the unit suffix (`noise_sigma_m`, `noise_sigma_pa`,
`noise_sigma_deg`, `noise_sigma_ms2`, `noise_sigma_rad_s`).

**Why:** The PF's likelihood model needs sigma; carrying it inline
keeps records self-describing (a PF or a M2 LNS8 PF doesn't need to
load profiles to interpret obs). Naming the field with its unit
suffix removes the ambiguity that `noise_sigma=0.01` carried under
the legacy joint-IMU shape.

### D5: `lora_links` retained for failure audit; `LoraTOAObservation` for measurements

**Choice:** Two separate streams in each tick record. `lora_toa`
observations are the canonical measurement record, one per end of each
successful pair (per Tier 4's cardinality decision). `lora_links` keeps
all attempts for visibility into drops and out-of-range, useful for the
dashboard's mesh-connectivity view but not consumed by the PF.

**Why:** Two consumers, two needs. The PF wants per-node measurements
with partner identity (now natively shaped). The dashboard wants
per-attempt status including failures. Keeping them separate is
cheaper than overloading one record with both concerns.

**Trade-off:** Successful ranging shows up in both arrays (obs +
link). Mild duplication. Acceptable for the size budget at M1 scale;
a future change could prune one or merge them.

### D6: Conversion lives in the generator, not in the sensor module

**Choice:** The sensor module continues to emit `Measurement` (the
existing flat tuple-shaped value type). The generator converts
`Measurement → Observation` at emit time, dispatching per sensor name.

**Why:** Sensors don't know about JSONL serialization or the typed-
record schema. Pulling the schema into the sensor module would couple
two concerns that have separate change cadences (sensor algorithm
tuning vs. scenario format evolution). Conversion is ~30 lines in the
generator; cheap.

### D7: `Observation` union members are frozen dataclasses with `__post_init__` validation

**Choice:** Each typed record validates its own fields:
- `LoraTOAObservation.__post_init__`: `range_m >= 0`,
  `partner_id != node_id`, `noise_sigma_m > 0`.
- `BaroObservation.__post_init__`: `pressure_pa > 0`,
  `noise_sigma_pa > 0`.
- `BathyProbeObservation.__post_init__`: `depth_m >= 0`,
  `noise_sigma_m > 0`.
- `MagObservation.__post_init__`: `0 <= heading_deg < 360`,
  `noise_sigma_deg > 0`.
- `GPSObservation.__post_init__`: `-90 <= lat_deg <= 90`,
  `-180 <= lon_deg <= 180`, `noise_sigma_m > 0`.
- `IMUObservation.__post_init__`: both sigmas positive.

**Why:** Construction-time validation is the cheapest defense against
schema-violating records sneaking in. Runtime cost is one comparison
per field per record — negligible.

## Risks / Trade-offs

- **[Risk] Tier 4 golden-trace fixture is invalidated.** Mitigation:
  regenerate the fixture as part of this change. The byte-identity
  contract is preserved for the *new* schema going forward.
- **[Risk] Outside-this-repo readers of the JSONL break.** None exist.
- **[Trade-off] Six classes instead of one.** Cognitive cost is real
  but localized to one file. The payoff is that every consumer's
  dispatch becomes type-safe, and adding sensor #7 is "add a typed
  record + a parse case + a generator case" with pyright telling you
  what you missed.
- **[Trade-off] `Measurement` and `Observation` both exist.** Two
  similar shapes side by side. Mitigated by clear ownership: sensors
  emit `Measurement`, generator emits `Observation`. If the duplication
  feels heavy in M2, a follow-up change collapses them.

## Key Type Contracts

```python
# scenario_schema.py

@dataclass(frozen=True, slots=True)
class GPSObservation:
    t_sec: float
    node_id: str
    lat_deg: float
    lon_deg: float
    noise_sigma_m: float

@dataclass(frozen=True, slots=True)
class IMUObservation:
    t_sec: float
    node_id: str
    accel_xyz: tuple[float, float, float]
    gyro_xyz: tuple[float, float, float]
    accel_noise_sigma_ms2: float
    gyro_noise_sigma_rad_s: float

@dataclass(frozen=True, slots=True)
class BaroObservation:
    t_sec: float
    node_id: str
    pressure_pa: float
    noise_sigma_pa: float

@dataclass(frozen=True, slots=True)
class MagObservation:
    t_sec: float
    node_id: str
    heading_deg: float
    noise_sigma_deg: float

@dataclass(frozen=True, slots=True)
class BathyProbeObservation:
    t_sec: float
    node_id: str
    depth_m: float
    noise_sigma_m: float

@dataclass(frozen=True, slots=True)
class LoraTOAObservation:
    t_sec: float
    node_id: str
    partner_id: str
    range_m: float
    noise_sigma_m: float

Observation: TypeAlias = (
    GPSObservation | IMUObservation | BaroObservation
    | MagObservation | BathyProbeObservation | LoraTOAObservation
)

# ObservationTickView and TruthTickView gain
#   observations: tuple[Observation, ...]
# (was tuple[ObservationRecord, ...])
```

JSONL form (illustrative tick record):

```json
{
  "record_type": "tick",
  "t": 0,
  "t_sec": 0.0,
  "nodes": {...},
  "observations": [
    {"type": "gps", "t_sec": 0.0, "node_id": "anchor_398a40aa",
     "lat_deg": 48.6, "lon_deg": -123.5, "noise_sigma_m": 1.5},
    {"type": "imu", "t_sec": 0.0, "node_id": "anchor_398a40aa",
     "accel_xyz": [0.0, 0.0, 9.81], "gyro_xyz": [0.0, 0.0, 0.0],
     "accel_noise_sigma_ms2": 0.01, "gyro_noise_sigma_rad_s": 0.01},
    {"type": "lora_toa", "t_sec": 0.0, "node_id": "anchor_398a40aa",
     "partner_id": "anchor_b7f55fc9", "range_m": 376.56,
     "noise_sigma_m": 20.0}
  ],
  "lora_links": [...]
}
```
