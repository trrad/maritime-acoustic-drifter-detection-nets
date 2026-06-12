## Context

Sensors are the boundary between Level 0 (physics truth — what the world actually is) and Level 1 (observations — what a node can measure). The scenario generator is the tick-loop driver; sensors are the function objects it invokes to produce observations from truth state. PFs consume the produced measurements downstream and never see truth directly.

Six sensor types for M1: gps, imu, baro, mag, lora_toa, bathy_probe. Five are "single-node, single-measurement" (one call → one Measurement or None). The sixth (lora_toa) is "node-pair, multi-measurement" (one call → zero or more Measurements, one per eligible neighbor). Treating the sixth as a special shape rather than forcing it into the single-sensor protocol keeps the protocol honest.

Two kinds of scheduling:
1. **Periodic**: fires at `max_rate_hz` (gps, imu, baro, mag, bathy_probe). A simple "should this fire at time t given last fire time t_last" check.
2. **TDMA-slotted**: fires only during the node's assigned slot (lora_toa). The scheduling logic lives inside the LoRa sensor because it is coupled to `CommsProfile.slot_length_sec` and `tdma_period_sec`.

## Goals / Non-Goals

**Goals:**
- A uniform `Sensor` protocol for the five single-measurement sensors so the scenario generator can iterate over them without per-sensor glue code.
- `LoraTOASensor` as an explicit separate class with its own interface for per-neighbor pair sampling — no pretending it fits the single-sensor protocol.
- Capability-envelope enforcement by construction: a sensor constructed with a `SensorSpec` for `"baro"` cannot produce a GPS measurement; a sensor called on a node whose profile lacks the matching SensorSpec raises `CapabilityViolation`.
- All noise applied via injected RNG; no global seeding; noise σ matches `SensorSpec.noise_sigma`.
- All timestamps come from the node's clock (`NodeClock.wall_time`), not from the global tick timestamp. M1 clocks are zero-offset stubs, so this is transparent; M2 will activate real clock skew without touching the sensor code.
- `BathyProbeSensor.sample` returns `None` when the node is on land. The sensor does not fabricate a reading when the truth map says the position has no bathymetry.

**Non-Goals:**
- Scheduling beyond the per-sensor `should_sample` check. The scenario generator owns the main tick loop and per-node schedule state (last-fire timestamps). Sensors are stateless.
- Variable-rate duty-cycling (e.g., "sample at 10 Hz when something is happening, 1 Hz otherwise"). All M1 sensors have fixed `max_rate_hz`.
- Sensor failure modes, calibration drift, biofouling. Declared non-goals in the integrity charter.
- Acoustic hydrophone observations (M2).
- Iridium uplink / message encoding (deferred — Iridium is a comms fabric, not a sensor in the observation-producing sense).

## Decisions

### D1: `Sensor` as a Protocol, not an ABC

**Choice:** `typing.Protocol` with `name`, `spec`, `should_sample(t_sec, last_fire_sec, env) -> bool`, and `sample(node, env, t_sec, rng) -> Measurement | None`. The single-measurement sensors implement this protocol structurally.

**Why:** Consistent with `CurrentField` in `maritime-current-fields` — structural typing, no inheritance tax, test doubles trivially satisfy the protocol. The five single-measurement sensors carry their `SensorSpec` as a construction-time attribute, so the scenario generator can iterate `for sensor in node.sensors` uniformly.

### D2: `LoraTOASensor` is not part of the single-sensor protocol

**Choice:** `LoraTOASensor` has its own interface: `sample_pair(self_node, neighbor_node, env, t_sec, rng) -> Measurement | None` for a single pair, and a higher-level `sample_all_pairs(self_node, fleet, env, t_sec, rng) -> tuple[Measurement, ...]` that iterates neighbors. Scheduling (TDMA slot check) lives inside the class.

**Why:** LoRa TOA is fundamentally multi-output — one call produces 0..N measurements depending on how many neighbors are in range. Forcing it into `sample(node, env, t_sec, rng) -> Measurement | None` would require callers to also know about neighbors, or require the sensor to return a list-wrapped-in-Measurement, or require the scenario generator to call it N times. All three workarounds leak complexity. Giving it its own interface is cleaner and makes the inter-node nature explicit.

**Trade-off:** The scenario generator has a small branch: "if sensor.name == 'lora_toa', use sample_all_pairs; else use sample." Acceptable — the special case is honest and documented.

### D3: Noise via injected RNG, no global state

**Choice:** Every sensor's `sample` method takes `rng: numpy.random.Generator`. No module-level RNG, no `numpy.random.seed()` calls.

**Why:** Matches the fleet-dynamics `propagate_truth` discipline and the project-infra conftest convention. Deterministic tests need a seeded generator passed in; global state makes test order dependency latent.

### D4: Timestamps from node clock, not global truth time

**Choice:** `sample` returns a `Measurement` whose `t_sec` field is computed as `node.components["clock"].wall_time(global_t_sec)`. The clock runtime component is placed at `node.components["clock"]` by the blueprint factory (see `maritime-clock-model` and `maritime-fleet-dynamics`). In M1, bundled-profile clocks carry `drift_ppm=0.0`, so `wall_time(t) == t` emerges; M2 activates realistic drift by parameter change only — sensor code is unchanged.

**Why:** Level 2 integrity. The JSONL captures what each node saw, on its own clock. The PF later fuses timestamps from different nodes without assuming they agree exactly. Single source of truth for the clock: the node's own components mapping. No redundant `clock_by_node_id` env mapping — scenario generator does not need to construct or thread one. If a node is missing a `"clock"` component, that's a construction-time bug surfaced by the blueprint factory, not a runtime sensor bug.

### D5: Capability enforcement at call time, not construction time

**Choice:** A sensor instance is bound to a `SensorSpec` at construction (`GPSSensor(spec)` takes the GPS spec). But whether the sensor can be sampled for a given node is checked at `sample` call time: if `node.profile.sensor("gps")` raises `KeyError`, `sample` raises `CapabilityViolation`.

**Why:** A single `GPSSensor` instance is reusable across anchor nodes that all share the same GPS spec. If we enforced at construction, we'd need one sensor instance per (node, spec) pair. Call-time checks let us have one sensor per class, shared across nodes of that class.

**Alternative considered:** Bind sensor to a specific node at construction. Rejected: creates 1:1 node↔sensor objects that are mostly redundant; better to let the sensor be a function object with a spec, and make the call site explicit about which node it's sampling.

### D6: LoRa TOA drop and range handled inside the sensor, not at call site

**Choice:** `LoraTOASensor.sample_pair` internally (1) computes true range from the two node positions, (2) checks range ≤ `comms.max_range_m`, (3) checks `rng.uniform() < comms.packet_loss_rate` for a drop, (4) samples Gaussian noise and returns the Measurement. Callers don't check these conditions.

**Why:** Encapsulation. The caller (scenario generator) shouldn't need to know the physics of LoRa line-of-sight or the drop probability model. Putting it in the sensor keeps Level 2 integrity concerns local.

### D7: BathyProbe returns None on land, not NaN

**Choice:** If `regional_map.depth_at(lat, lon)` returns NaN (node is on land per the `maritime-map-payload` contract), `BathyProbeSensor.sample` returns `None` (no measurement produced this tick).

**Why:** The PF distinguishes "no measurement arrived" from "a measurement of NaN." Returning `None` is honest: there is no bathy probe reading when the node is on land because the pressure sensor has no water column above it. Returning a NaN measurement would be a nonsense observation that the PF would have to filter out specially.

**Related:** The scenario generator observes `None` and omits the sensor field in that tick's JSONL record for that node.

### D8: Sensor name vocabulary lives with the spec vocabulary

**Choice:** The string constants `"gps"`, `"imu"`, `"baro"`, `"mag"`, `"lora_toa"`, `"bathy_probe"` (documented in `maritime-platform-profile` D6) are the canonical names. Each sensor class's `.name` returns one of those strings. Tests assert these match.

**Why:** Avoids circular dependency. `maritime-platform-profile` declares the vocabulary as a documented string set; this change implements the sensors that use those names. A future helper (`sensor_registry`) could map names to classes, but for six sensors it's overkill.

## Risks / Trade-offs

- **[Risk] IMU's multi-dim output doesn't fit `value: float | tuple[...]`** → Use `tuple[float, ...]` for IMU (6-tuple: ax, ay, az, gx, gy, gz). Scalar sensors use a single-element tuple or a plain float — decision: always `tuple[float, ...]` with `len == 1` for scalars. Uniform output shape, minor verbosity at the call site.
- **[Risk] LoRa drop is per-packet, not per-ranging-round** → A ranging round is one exchange (two packets). Treating it as a single Bernoulli draw with `comms.packet_loss_rate` is an approximation. Acceptable for M1; M2 can model round-trip drop if needed.
- **[Risk] Range-ceiling cutoff is a hard step function; real radios have a fade zone** → Acceptable for M1. The ceiling represents "beyond this, packet success probability collapses." A probabilistic fade model is a future refinement.
- **[Trade-off] Duplicated scheduling logic between periodic sensors** → Each of the five single-measurement sensors implements `should_sample` the same way. Could factor into a mixin. Decision: keep it inlined for now — five lines per class, refactor when it becomes painful.
- **[Trade-off] SensorEnv as a context struct vs. per-sensor method signature** → Chose the struct to keep `Sensor.sample` uniform. Sensors that don't need a map/fleet ignore those fields. Minor overhead.

## Key Type Contracts

```python
# sensors.py

@dataclass(frozen=True, slots=True)
class Measurement:
    t_sec: float                 # per-node wall time (from node's clock)
    node_id: str
    sensor_name: str             # "gps" | "imu" | "baro" | "mag" | "lora_toa" | "bathy_probe"
    value: tuple[float, ...]     # always a tuple; scalar sensors use len-1 tuple
    unit: str                    # unit label matching SensorSpec.noise_unit
    noise_sigma: float           # declared noise std (for PF observation model)


@dataclass(frozen=True, slots=True)
class SensorEnv:
    """Context passed to Sensor.sample(). Sensors use only the fields they need.
    Clocks are read from node.components["clock"], not threaded through env."""
    regional_map: RegionalMap | None = None
    fleet: tuple[Node, ...] | None = None


class Sensor(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def spec(self) -> SensorSpec: ...

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        """True iff the elapsed time since last_fire_sec permits another sample at the sensor's max_rate_hz."""

    def sample(self, node: Node, env: SensorEnv, t_sec: float, rng: numpy.random.Generator) -> Measurement | None:
        """Produce a measurement, or None if no measurement is available (on-land bathy, etc.).
        Raises CapabilityViolation if node.profile does not include this sensor."""


class GPSSensor:
    # Anchor-only in M1. value = (lat_deg, lon_deg). unit = "deg". noise σ from spec.
    ...

class IMUSensor:
    # value = (accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z). unit = "m/s^2;rad/s" (composite).
    ...

class BaroSensor:
    # value = (pressure_pa,). unit = "Pa". Computed from truth depth + sea-level pressure + noise.
    ...

class MagSensor:
    # value = (heading_deg,). unit = "deg". Computed from truth heading + magnetic-declination model + noise.
    ...

class BathyProbeSensor:
    # value = (depth_below_seafloor_m,). unit = "m". Sampled from regional_map.depth_at + noise.
    # Returns None if is_on_land.
    ...

class LoraTOASensor:
    # Multi-measurement sensor with its own interface.
    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool: ...

    def sample_pair(
        self,
        self_node: Node,
        neighbor_node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: numpy.random.Generator,
    ) -> Measurement | None:
        """Sample one pair. Returns None if out of range or dropped."""

    def sample_all_pairs(
        self,
        self_node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: numpy.random.Generator,
    ) -> tuple[Measurement, ...]:
        """Sample against every fleet member (excluding self). Returns only the successful pairs."""
```

Construction and call invariants:
- `Sensor.sample` raises `CapabilityViolation` when `node.profile.sensor(self.name)` raises `KeyError`.
- `Measurement.t_sec` comes from `node.components["clock"].wall_time(t_sec)`; if `"clock"` is absent from `node.components`, raise `KeyError` (never silently default — a node without a clock is a construction-time bug).
- `Measurement.noise_sigma == self.spec.noise_sigma`.
- `Measurement.unit == self.spec.noise_unit` for sensors where the spec unit applies (scalar sensors); composite sensors (IMU) document their own multi-unit convention.
- `LoraTOASensor.sample_pair` returns `None` if range > `comms.max_range_m` or if `rng.uniform() < comms.packet_loss_rate`.

## Integrity-Charter Mapping

- **Level 1 (Sensor Model)** — This change is the primary Level 1 implementation. Duty cycle (via `max_rate_hz` and `should_sample`), noise from datasheet figures, capability enforcement via `CapabilityViolation`, and refusal to fabricate readings (bathy on land → `None`) are all on-contract.
- **Level 2 (Comms)** — `LoraTOASensor` delivers the M1 subset: range ceiling + packet drop + TDMA slot scheduling. A future `maritime-lora-comms` would factor the slot logic out into a separate comms module when M2 needs mesh dissemination.
- **Level 0 truth separation** — Sensors read node truth state + environment but never PF estimates or prior observations. Pure function of (node, env, rng, time).
