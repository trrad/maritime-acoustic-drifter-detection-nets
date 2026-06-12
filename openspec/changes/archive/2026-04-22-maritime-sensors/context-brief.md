# Context Brief: maritime-sensors

## Purpose
Deliver the Level 1 (sensor model) layer referenced in the integrity charter's forward-contracts table. Six M1 sensor types as typed callables that enforce capability envelopes, apply datasheet noise from `SensorSpec`, timestamp via the node's clock, and refuse to fabricate readings (bathy probe returns `None` on land; LoRa returns `None` out of range or on drop).

## Key Decisions
- `Sensor` as `typing.Protocol` with `@runtime_checkable`; five single-measurement sensors implement it structurally. `LoraTOASensor` is explicitly *not* part of the protocol — its multi-output, per-pair nature gets its own `sample_pair` / `sample_all_pairs` interface.
- All noise applied via injected `numpy.random.Generator`; no global seeding. Deterministic tests require identical RNG state in.
- Timestamps come from `node.components["clock"].wall_time(t_sec)` — a `KeyError` if `"clock"` is absent from the node (which indicates a blueprint-factory bug, not a runtime condition to recover from). In M1 bundled-profile clocks have `drift_ppm=0.0`, so `wall_time(t) == t` emerges; M2 activates real skew by parameter change only.
- `Measurement.value` is always a tuple (length-1 for scalar sensors, length-2 for GPS, length-6 for IMU). Uniform shape at the schema level.
- Capability enforcement at call time (not construction). `GPSSensor(spec).sample(drifter_node, ...)` raises `CapabilityViolation`. Lets a single sensor instance be shared across nodes of the appropriate class.
- LoRa TOA range + drop + TDMA are internal to `LoraTOASensor`. Callers never check `max_range_m` or `packet_loss_rate` themselves.
- Bathy probe returns `None` on land (distinguishable from a NaN measurement). Scenario generator omits the field in that tick's JSONL.
- `SensorEnv` bundles optional dependencies (`regional_map`, `fleet`) so all sensors share a uniform `sample` signature even though they consume different inputs. Clocks are read from `node.components["clock"]`, not from env.

## Tasks
1. Measurement Record — Tests
2. Measurement Record — Implementation
3. Sensor Protocol and SensorEnv — Tests
4. Sensor Protocol and SensorEnv — Implementation
5. Periodic Scheduling — Tests
6. Capability Enforcement — Tests
7. Timestamp via Node Clock — Tests
8. Noise and Determinism — Tests
9. GPSSensor — Tests
10. IMUSensor — Tests
11. BaroSensor — Tests
12. MagSensor — Tests
13. BathyProbeSensor — Tests
14. LoraTOASensor — Tests
15. GPSSensor — Implementation
16. IMUSensor — Implementation
17. BaroSensor — Implementation
18. MagSensor — Implementation
19. BathyProbeSensor — Implementation
20. LoraTOASensor — Implementation
21. Verification

## Files Affected
- `rtl/vectors/maritime/sensors.py` (new)
- `tests/maritime/test_sensors.py` (new)

## Spec Pointers
maritime-sensors → Requirement: Measurement Record, Requirement: Sensor Protocol, Requirement: Capability Envelope Enforcement, Requirement: Periodic Sample Scheduling, Requirement: Timestamp via Node Clock, Requirement: Noise Applied via Injected RNG, Requirement: GPSSensor, Requirement: IMUSensor, Requirement: BaroSensor, Requirement: MagSensor, Requirement: BathyProbeSensor Returns None on Land, Requirement: LoraTOASensor Range and Drop
openspec/changes/maritime-sensors/specs/maritime-sensors/spec.md
