## Context

Platform capability numbers live today as prose tables in `docs/maritime_buoy_design.md` and `docs/platform_design_notes.md`. Three node classes (anchor / shear-keeper / drifter) with distinct state dims (25 / 21 / 15), power envelopes (~50 / ~5 / ~2 mW), sensor suites, comms patterns, and compute budgets. Downstream code will repeatedly need these numbers: scenario generation needs sensor rates and LoRa slot lengths, fleet dynamics needs state dim, sensor models need duty cycles, PFs need cycle budgets. Re-encoding the same numbers in each module is exactly how simulation integrity rots.

The integrity charter (`docs/simulation_integrity.md`) classifies capability mismatches (a drifter emitting GPS, a sensor firing faster than its duty cycle, a PF exceeding its cycle budget) as bugs at Levels 1 and 3. Their forward contract is "skeuomorphic node classes with intrinsic capabilities" — meaning capabilities are data on the class, not external config that can drift.

This change delivers the data. Later changes build the classes (`maritime-fleet-dynamics`) and do the enforcement (`maritime-sensors`, `maritime-scenario-gen`, `maritime-pf-*`).

## Goals / Non-Goals

**Goals:**
- A single module (`rtl/vectors/maritime/platform_profile.py`) that is the source of truth for platform capability envelopes.
- Frozen, hashable, type-checked dataclasses — `SensorSpec`, `CommsProfile`, `ComputeBudget`, `NodeProfile`.
- Three bundled M1 profiles (`ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, `PURE_DRIFTER_PROFILE`) whose numbers match `docs/maritime_buoy_design.md`. Active ballast pump presence (`has_pump`) is the discriminator between the two non-anchor classes.
- Profile-internal consistency checks: sensor duty cycle within [0, 1], max rate > 0, average power non-negative, sum of sensor powers ≤ total power budget, compute-budget cycles-per-step × update rate fits within the declared clock frequency.
- A single typed exception `CapabilityViolation` that downstream changes raise when a consumer requests behavior outside a profile.

**Non-Goals:**
- Enforcement of sensor duty cycles at the sensor-sampling boundary (owned by `maritime-sensors`).
- Runtime sensor instantiation or node-object construction (owned by `maritime-fleet-dynamics`).
- Profile serialization to TOML/YAML or CLI-driven profile overrides (not needed for M1 — the three bundled constants cover the M1 fleet).
- LoRa PHY modeling, acoustic signal chain, or Iridium SBD specifics (only the high-level envelope lives here).
- Profiles for river-drone or aerial platforms (maritime only; future platforms get their own `<platform>-platform-profile` changes).

## Decisions

### D1: Plain Python frozen dataclasses, no pydantic / attrs / msgspec

**Choice:** `@dataclass(frozen=True, slots=True)` on the standard library only.

**Why:** Profiles are static data consumed by test and production code with no serialization boundary in M1. Frozen dataclasses give immutability + `__eq__` + `__hash__` for free. Adding a validation library creates a new dependency surface and a new place for integrity to leak (a validator that silently coerces a 1.1 duty cycle to 1.0 is a bug waiting to happen). Internal consistency checks go in `__post_init__` and raise `ValueError` on construction.

**Alternatives considered:**
- `pydantic.BaseModel`: dependency we don't have, and pydantic's coercion behavior is a footgun for integrity-sensitive types.
- `attrs`: similar objections; stdlib dataclasses are sufficient.
- `TypedDict`: no runtime validation, no immutability.

### D2: Profile data sourced from the buoy design doc, not re-derived

**Choice:** The three bundled profile constants cite specific tables/lines in `docs/maritime_buoy_design.md`. Any change to a profile number requires updating both the design doc and the profile, caught by a cross-reference test.

**Why:** The buoy design doc is the authoritative source. Profiles that drift from the doc re-introduce the "capability numbers scattered across files" problem we're solving. The cross-reference test (a comment citing the doc section and a spot-check of 2–3 numbers against the doc text) is cheap and makes the source explicit.

**Trade-off:** This introduces a soft coupling between the design doc and the code. Acceptable: the doc is versioned in the repo, and the test is advisory rather than a strict parser.

### D3: CapabilityViolation is defined here, raised elsewhere

**Choice:** `CapabilityViolation(Exception)` lives in `platform_profile.py` with a clear docstring. No profile method raises it directly; `__post_init__` raises `ValueError` for construction-time integrity. `CapabilityViolation` is reserved for runtime behavior mismatches downstream (sensor fires outside its duty cycle, PF exceeds cycle budget).

**Why:** Construction-time errors (malformed profile data) and runtime-time errors (real behavior doesn't fit the profile) are semantically different. Keeping `ValueError` for the former matches Python convention and tests can catch it without importing from this module. `CapabilityViolation` is the shared vocabulary for the latter and needs a single home — this is the natural one.

**Alternatives considered:**
- One exception type for both: muddles the test intent (bad config vs. bad behavior).
- Exception defined in `maritime-sensors`: creates a dependency inversion where `platform_profile` would need to import from `maritime-sensors` or every consumer would define its own.

### D4: Profiles are pure data, no methods that touch the world

**Choice:** `NodeProfile` has a few `@property`-level conveniences (e.g., `total_sensor_power_mw`) but no methods that take arguments or produce observations. It does not know about `CurrentField`, `RegionalMap`, or any runtime state.

**Why:** Testability and reuse. The profile is referenced from tests, docs, scenario generation, PF instantiation, and validation reports. Any of those contexts should be able to import a profile without dragging in the world. This also keeps the charter's Generator/Engine split clean — profiles belong to the Generator (configuration).

### D5: Compute budget is per-update-step, not per-second

**Choice:** `ComputeBudget.cycles_per_step` is the cycle ceiling for one full PF update (predict + weight + resample + estimate). The profile's `pf_update_rate_hz` × `cycles_per_step` must fit within `clock_mhz × 1e6`. An explicit headroom factor (default 0.8) reserves capacity for sensor I/O, RTL housekeeping, and jitter.

**Why:** The 6D POC measures in "cycles per step" (see `status.md`: ~73K cycles/step on current RTL). Converting to per-second is lossy — it hides whether a budget overflow is driven by state dim, sensor count, or clock. Keeping cycles-per-step as the primitive lets the PF-side changes compute it and compare directly.

### D6: Sensors identified by name (str), not by enum or class reference

**Choice:** `SensorSpec.name` is a plain string from a small documented vocabulary (`"gps"`, `"imu"`, `"baro"`, `"mag"`, `"hydrophone"`, `"lora_toa"`, `"bathy_probe"`). No enum.

**Why:** The sensor module (future change) will have its own types for measurement models. Defining sensors as strings here lets `maritime-sensors` own the vocabulary mapping without circular imports. A shared enum would create an awkward dependency: sensor implementations upstream of the profile that names them. Documented string vocabulary in the spec is the lightest binding that still tests.

### D7: Pump presence as the pure-drifter / ballast-drifter discriminator

**Choice:** `NodeProfile` carries an explicit `has_pump: bool` field. When `has_pump=True`, a separate `ballast_capacity_ml: float` field declares the bladder volume; when `has_pump=False`, `ballast_capacity_ml` SHALL be 0 (enforced in `__post_init__`). Anchors carry `is_moored: bool` and `has_satellite_uplink: bool` as additional discrete-tier markers.

**Why:** The plan's original "shear-keeper vs. drifter" cut is really about the presence of an active ballast pump — a discrete hardware component with its own power cost, failure modes, and control-loop overhead. Modeling it as a boolean on the profile (instead of as a continuous `ballast_capacity_ml`) keeps the hardware boundary explicit and lets the scenario generator and fleet-dynamics changes branch cleanly on capability. Ballast capacity is a continuous parameter of pump-equipped nodes; it is not meaningful without a pump. Mooring and satellite uplink are likewise discrete anchor-only tiers — cheap to model as booleans now, painful to retrofit later.

**Alternatives considered:**
- A continuous `ballast_capacity_ml` alone, with `has_pump` derived as `ballast_capacity_ml > 0`: conflates "does the hardware have a pump" with "how big is the bladder." A zero-capacity pump is still a pump (it has a motor, power draw, failure modes); `has_pump=False` is qualitatively different.
- Separate `NodeProfile` subclasses per class: violates D1 (frozen data, no hierarchy); makes downstream polymorphism unnecessarily complex.

**What this enables:** simulation sweeps over fleet composition — e.g., running identical scenarios with N% `BALLAST_DRIFTER_PROFILE` vs. N% `PURE_DRIFTER_PROFILE` nodes — to quantify the station-keeping benefit of pump hardware before committing to its BOM. Separately, fleet-dynamics can enforce "pure drifter is surface-only" as a capability invariant without re-deriving the rule per module.

## Risks / Trade-offs

- **[Risk] Profile numbers drift from `maritime_buoy_design.md`** → Mitigation: cross-reference test cites doc lines; reviewers check both on design-doc edits.
- **[Risk] Over-tight profiles reject legitimate M1 sensor rates** → The bundled profile tests include a "typical scenario fits within the profile" check, not just boundary checks. If a downstream change needs a rate that a profile disallows, the right response is to revisit the profile against the design doc, not to relax the profile post-hoc.
- **[Trade-off] Profiles are Python-only, not shared with RTL** → Acceptable. RTL consumes the profile-derived constants via the Python harness (cycle budgets, state dim). A future change could emit a Verilog header from the profile if needed.
- **[Trade-off] Three bundled profiles is hardcoded for M1 Monterey Bay** → Acceptable for M1; a follow-on change can add profile-variation support (e.g., for EEZ-scale deployments) when needed.

## Key Type Contracts

```python
@dataclass(frozen=True, slots=True)
class SensorSpec:
    name: str                   # documented vocabulary: "gps", "imu", "baro", ...
    observed_dim: int           # which state dim the sensor observes (-1 if multi-dim)
    noise_sigma: float          # physical-unit noise std (meters / m·s⁻¹ / Pa / ...)
    noise_unit: str             # unit label, e.g. "m", "m/s", "Pa"
    max_rate_hz: float          # ceiling — sensor can fire no faster than this
    duty_cycle: float           # fraction active in [0, 1]
    avg_power_mw: float         # average power draw at declared duty

@dataclass(frozen=True, slots=True)
class CommsProfile:
    slot_length_sec: float      # TDMA slot window
    tdma_period_sec: float      # full TDMA frame length
    max_range_m: float          # realistic LoRa line-of-sight ceiling
    ranging_sigma_m: float      # SX1262 TOA ranging 1σ accuracy
    packet_bits: int            # per-slot payload capacity
    packet_loss_rate: float     # expected fraction of lost packets in [0, 1]
    avg_power_mw: float

@dataclass(frozen=True, slots=True)
class ComputeBudget:
    clock_mhz: float
    cycles_per_step: int        # PF-step cycle ceiling (predict+weight+resample+estimate)
    pf_update_rate_hz: float
    headroom: float = 0.8       # reserved fraction of clock capacity
    avg_power_mw: float = 0.0

@dataclass(frozen=True, slots=True)
class NodeProfile:
    class_name: str             # "anchor" | "ballast_drifter" | "pure_drifter"
    state_dim: int
    sensors: tuple[SensorSpec, ...]  # tuple, not list, to preserve frozen semantics
    comms: CommsProfile
    compute: ComputeBudget
    total_power_budget_mw: float
    has_pump: bool              # discrete: active ballast pump present
    ballast_capacity_ml: float  # bladder volume; must be 0 when has_pump is False
    is_moored: bool             # anchor-style structural mooring
    has_satellite_uplink: bool  # anchor-style Iridium SBD module

    @property
    def total_sensor_power_mw(self) -> float: ...

    @property
    def total_avg_power_mw(self) -> float: ...

    def sensor(self, name: str) -> SensorSpec:
        """Return the sensor with the given name, raise KeyError if absent."""

class CapabilityViolation(Exception):
    """Raised when runtime behavior exceeds a NodeProfile's declared capability."""


# Module constants (M1 Monterey Bay fleet)
ANCHOR_PROFILE: NodeProfile            # has_pump=False, is_moored=True, has_satellite_uplink=True
BALLAST_DRIFTER_PROFILE: NodeProfile   # has_pump=True, ballast_capacity_ml>0
PURE_DRIFTER_PROFILE: NodeProfile      # has_pump=False, ballast_capacity_ml=0, surface-only
ALL_M1_PROFILES: tuple[NodeProfile, ...]   # (anchor, ballast_drifter, pure_drifter)
```

Construction invariants enforced in `__post_init__`:
- `SensorSpec`: `max_rate_hz > 0`, `0 <= duty_cycle <= 1`, `noise_sigma >= 0`, `avg_power_mw >= 0`.
- `CommsProfile`: `0 < slot_length_sec <= tdma_period_sec`, `max_range_m > 0`, `ranging_sigma_m >= 0`, `packet_bits >= 0`, `0 <= packet_loss_rate <= 1`, `avg_power_mw >= 0`.
- `ComputeBudget`: `clock_mhz > 0`, `cycles_per_step > 0`, `pf_update_rate_hz > 0`, `0 < headroom <= 1`, and `cycles_per_step * pf_update_rate_hz <= clock_mhz * 1e6 * headroom`.
- `NodeProfile`: `state_dim > 0`, `total_power_budget_mw > 0`, sum of sensor/comms/compute average powers ≤ `total_power_budget_mw`, all sensor names unique, `ballast_capacity_ml >= 0`, and if `has_pump is False` then `ballast_capacity_ml == 0`.

## Integrity-Charter Mapping

- **Level 1 (Sensor Model)** — `SensorSpec` pins duty cycle + max rate; `maritime-sensors` will consume and enforce.
- **Level 2 (Comms)** — `CommsProfile` pins max range and ranging sigma; `maritime-acoustics` (per charter table) or a future `maritime-lora-comms` will enforce.
- **Level 3 (Compute + Precision Budget)** — `ComputeBudget` caps cycles/step; the PF changes will assert actual cycle counts against this.
- Truth separation, Level 0 physics, and Level 4 onboard maps are unchanged and unaffected by this module.
