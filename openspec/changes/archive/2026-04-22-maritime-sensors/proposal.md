## Why

The scenario generator and particle filters both need sensor observations, but the plan doc describes them only in prose (GPS σ≈1-3 m, IMU sigma, "once per 5 min on anchors"). Without typed sensor objects that enforce capability envelopes and produce timestamped measurements, the scenario generator would re-invent sensor noise and scheduling from scratch — exactly the integrity-rot failure the charter warns about.

Concretely, the Level 1 integrity contract requires:
- A drifter cannot produce GPS observations (it has no GPS module).
- A sensor cannot fire faster than its declared `max_rate_hz`.
- Noise σ on every sensor matches the datasheet figure in `SensorSpec.noise_sigma`.
- Sensor measurements derive only from physically observable quantities — not from truth state that the sensor can't physically access.

This change delivers the six M1 sensor types, each produced as a typed `Measurement` object, with capability enforcement built into the call signature (a `GPSSensor` only accepts nodes whose profile includes a `"gps"` sensor; otherwise `CapabilityViolation`).

## What Changes

- Introduce `rtl/vectors/maritime/sensors.py`:
  - A `Measurement` frozen dataclass capturing `(t_sec, node_id, sensor_name, value, unit, noise_sigma)`
  - A `Sensor` protocol with `name`, `spec`, `should_sample`, and `sample` members
  - Five single-measurement sensor classes: `GPSSensor`, `IMUSensor`, `BaroSensor`, `MagSensor`, `BathyProbeSensor`
  - One multi-measurement sensor class: `LoraTOASensor` (fires once per TDMA window per eligible neighbor pair)
  - A `SensorEnv` context struct bundling optional dependencies (`regional_map`, `fleet`) so `sample` has a uniform signature across sensors that need different inputs. Clocks are not threaded through env — sensors read from `node.components["clock"]` directly.
- Noise models tuned to the SensorSpec values in bundled M1 profiles; noise applied via the injected RNG (no global state).
- Capability enforcement: every sensor's `sample` raises `CapabilityViolation` when called on a node whose profile does not include the matching `SensorSpec`.
- LoRa TOA enforcement: range ceiling, packet drop rate, and TDMA slot scheduling are internal to `LoraTOASensor`. Range > `CommsProfile.max_range_m` or a sampled drop returns no measurement for that pair — not an exception.
- Bathy probe "on land" handling: `BathyProbeSensor.sample` returns `None` when the node's position is on land (truth-map `depth_at` returns NaN). The sensor does not fabricate a reading.
- **No scheduling loop, no JSONL emission.** The scenario generator owns the tick loop and is the consumer that drives sensors.

## Capabilities

### New Capabilities

- `maritime-sensors`: Typed sensor observations for the M1 fleet. Defines the `Sensor` protocol, the `Measurement` record type, five single-measurement sensor classes (GPS, IMU, baro, mag, bathy probe), and one multi-measurement class (LoRa TOA ranging). Every sensor enforces its capability envelope via `CapabilityViolation` on profile mismatch, applies noise from `SensorSpec.noise_sigma`, and (where relevant) respects range ceilings, packet loss, and TDMA scheduling from `CommsProfile`.

### Modified Capabilities

(none — no existing standing specs affected)

## Impact

- **New files**: `rtl/vectors/maritime/sensors.py`, `tests/maritime/test_sensors.py`.
- **Dependencies on earlier changes**: `maritime-platform-profile` (SensorSpec, CommsProfile, NodeProfile, CapabilityViolation); `maritime-state-layout` (to index observed dimensions on truth state); `maritime-fleet-dynamics` (Node classes — sensors are called on nodes, not on bare state arrays); `maritime-clock-model` (timestamping — sensors record `t_sec` from the node's clock, not from global truth time); `maritime-map-payload` (BathyProbeSensor samples the truth map's bathymetry; returns `None` on land).
- **Downstream consumers**: `maritime-scenario-gen` iterates sensors per tick to produce JSONL observation records; `maritime-pf-float` and later PF changes consume `Measurement` types as input to the likelihood evaluation.
- **Frozen baseline**: untouched.
- **Simulation integrity charter**: delivers the Level 1 (Sensor Model) enforcement referenced in the forward-contracts table. Noise σ values cite datasheet sources in module docstring. LoRa TOA drop / range handling delivers the "M1 inter-node comms modeled at the sensor boundary" architecture we settled on.
