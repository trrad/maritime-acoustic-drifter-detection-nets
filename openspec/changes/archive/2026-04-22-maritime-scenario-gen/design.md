## Context

The 6D POC collapsed generation + schema + reading into `gen_pf_scenario.py` with ad-hoc `.hex` and `.jsonl` output. Maritime has ten nodes, six sensor types, per-node clocks, onboard-vs-truth map distinction, and a JSONL schema that will evolve (acoustic events in M2, vessel tracks later). Collapsing doesn't scale — the producer logic and the consumer contract will drift.

This change splits them: `maritime-scenario-schema` is the consumer-facing contract (read-side types and version gating); `maritime-scenario-gen` is the producer (CLI + tick loop). Both deltas ship together because M1 has exactly one producer, but the schema is the load-bearing standing spec that everyone downstream cites.

## Goals / Non-Goals

**Goals:**
- A JSONL schema versioned from day one (`"schema_version": "1.0"` on the header record; readers raise on unknown versions).
- A `ScenarioReader` that yields observation-only records — no PF code can accidentally read truth, because `ScenarioReader.__iter__` never produces a dict containing a `"truth"` key.
- A `ScenarioTruthReader` for validation tooling with full access. Different type, different call site, clear intent.
- Deterministic scenario output: given `(seed, bbox, duration_hours, dt_sec, nodes)`, two runs produce byte-identical files. This is the contract `dev-infra` would have owned; it lives here now because the CLI is here.
- A small committed golden trace (under 50 KB) that regresses the tick loop end-to-end. Re-blessed only when a change means to alter the output.
- Composition is explicit and visible: the generator's `main()` reads as a linear sequence of "build fleet (clocks ride inside the factory), build maps, build field, build sensors, tick loop." No hidden globals, no auto-discovery.

**Non-Goals:**
- Multi-producer support. Exactly one CLI in M1; follow-on changes can add producers (e.g., a HYCOM-field variant) when needed.
- Schema migration between versions. Readers raise on unknown versions; migration tools are a future concern.
- Parallel tick execution. Single-process, single-threaded. Performance is not a M1 concern (10 nodes × 900 ticks × 6 sensors × minimal math is sub-second).
- Acoustic events, dark-vessel tracks, TDOA hyperbolae. M2.
- Dashboard wiring. The generator writes JSONL; the dashboard reads it. No shared types, no coupling.
- Runtime map-update simulation (over-the-air LoRa map delivery). M3.

## Decisions

### D1: Two delta specs in one change

**Choice:** `specs/maritime-scenario-schema/spec.md` and `specs/maritime-scenario-gen/spec.md`. Schema is the standing spec everyone downstream will cite; generator is the one producer.

**Why:** Same pattern as `maritime-fleet-dynamics` → `state-layout` + `fleet-dynamics`. The schema earns its own standing spec because PF changes, dashboard, and validation harness will list "conforms to `maritime-scenario-schema`" in their deltas. Generator is narrower. Splitting into two deltas lets the schema evolve (e.g., v1.1 with acoustic events) with a separate change proposal that leaves the generator mostly unchanged.

### D2: JSONL, not HDF5 / Parquet / Arrow

**Choice:** Newline-delimited JSON. Text format. Each line is one record.

**Why:** Debuggable without special tools (`head`, `jq`, editor), human-readable, git-friendly for the golden trace fixture, trivial to produce in Python without dependencies. The 6D POC already uses JSONL for the reference trace; consistency pays. At 10 nodes × 900 ticks × ~1 KB/record = ~9 MB per 15 min run — well within the "load into memory, iterate" regime.

**Trade-off:** Larger on disk than binary formats. Acceptable for M1. If 100-node simulations in M2 create ballooning files, we can gzip the JSONL (readers already handle it cleanly) or migrate to a binary format behind the same reader interface.

### D3: Header record with schema version

**Choice:** The first line of every scenario JSONL is a header:
```
{"record_type": "header", "schema_version": "1.0", "config": {...}, "fleet": [...], "bbox": [lat_s, lon_w, lat_n, lon_e], "created_at_utc": "2026-04-20T..."}
```

Subsequent lines are tick records:
```
{"record_type": "tick", "t": 0, "t_sec": 0.0, "nodes": {...}, "observations": [...], "lora_links": [...]}
```

**Why:** A version string in the file is the cheapest possible migration scaffold. Readers check it and raise on unknown versions. The `record_type` discriminant lets future schemas add new record types (e.g., `"record_type": "acoustic_event"` in M2) without breaking readers that filter on tick records.

### D4: ScenarioReader vs. ScenarioTruthReader — physical module split

**Choice:** Two distinct reader classes in **two distinct modules**.
`ScenarioReader` lives in `rtl/vectors/maritime/scenario_schema.py`
with the observation-only types (`ObservationTickView`,
`ObservationRecord`, `LoraLinkRecord`, `ScenarioHeader`).
`ScenarioTruthReader` lives in `rtl/vectors/maritime/scenario_truth_schema.py`
with the truth types (`TruthTickView`). Neither reader yields raw
dicts — both decode into typed views.

An internal helper (`_parse_tick_line`) is shared via an internal
module (`_scenario_parse.py`) that both readers import. Observation-
only decoding lives in `scenario_schema`; truth decoding lives in
`scenario_truth_schema`.

**Why:** The module boundary is what the import-linter contract
operates on. `project-infra-import-linter` (landed first) installs the
tool; `maritime-pf-float` (landing after this change) registers a
contract with `scenario_truth_schema` in `forbidden_modules` for PF
source modules. Physical separation means PF code that tries
`from rtl.vectors.maritime.scenario_truth_schema import ...` trips the
CI check; PF code that uses `ScenarioReader` is clean. Earlier designs
put both readers in one module and relied on "PF code imports should
mention `ScenarioReader` not `ScenarioTruthReader`" as a convention —
that's instruction, not enforcement, and AGENTS.md rules it out.

**Trade-off:** Two files instead of one; shared internal parsing
logic lives in a `_scenario_parse.py` helper. Minor cost. The
shared helper can be imported by both schemas safely — it's observation
parsing that both need.

### D5: Seed reproducibility contract (moved from dev-infra)

**Choice:** `gen_maritime_scenario.py --seed S --bbox BB --duration-hours H --dt-sec DT --nodes N --out F` produces byte-identical output across repeated runs. Default `dt-sec = 60.0` and `duration-hours = 24.0` reflect operational scale — multi-day drifter deployments, LoRa cycles measured in hours, drift in km/day. Fine resolution (`--dt-sec 1.0` or sub-second) is an opt-in for TDMA-slot and acoustic-TDOA tuning work. The seed drives: fleet factory drifter placement, dynamics process noise, sensor noise, onboard map fidelity reduction, climatology sampling, LoRa packet drop decisions.

**Why:** Integration-level regression requires byte-identical output. Golden trace comparison depends on it. The contract was originally queued in `dev-infra`; we dropped it at review time because `gen_maritime_scenario.py` didn't exist yet. It exists here, so the contract lands here.

**Implementation detail (goes in design, not spec):** Single `numpy.random.Generator` is seeded at generator startup, then sub-generators are derived for each subsystem via `default_rng(parent.integers(...))`. This gives reproducibility even when subsystems are reordered later, as long as the seeding order stays stable.

**`created_at_utc` semantics:** The header field is informational metadata, not part of the byte-identical contract by default. `gen_maritime_scenario.py` accepts an optional `--created-at` flag; when omitted, the generator stamps `datetime.now(timezone.utc).isoformat()`, which by definition makes consecutive runs differ in this one field. Tests and tooling that need byte-identity (the golden-trace check, seed-reproducibility test, regenerator script) MUST pass an explicit `--created-at` value (e.g., `2026-04-22T00:00:00+00:00`). The byte-identical reproducibility guarantee in D5 is therefore conditional: identical `(seed, bbox, duration_hours, dt_sec, nodes, created_at)` tuples produce byte-identical files.

### D6: Golden trace fixture and regeneration CLI

**Choice:** Commit `tests/maritime/golden_trace/m1_tiny.jsonl` — 60 ticks × 3 nodes (1 anchor, 1 ballast, 1 pure drifter), seed 42, 1-km bbox. Under 50 KB. A `regenerate_golden_trace.py` script rebuilds it from the current generator; re-blessing requires running the script intentionally and committing the new file.

**Why:** Catches inadvertent tick-loop changes. If a developer modifies `propagate_truth` without meaning to change observed behavior, the golden trace diff will surface it. If a change means to alter the trace, the bless step is a deliberate commit.

**Alternative considered:** Hash comparison instead of full file diff. Hashes are more compact but lose the ability to diff the content when a test fails — hard to see *what* changed. Full file is more debuggable and the fixture is small enough.

### D7: Generator main() is a linear sequence, no framework

**Choice:** The CLI's `main()` reads as:
```
1. parse args
2. build fleet via make_m1_fleet(seed, bbox) — blueprint factories attach a zero-offset `Clock` at `node.components["clock"]` in M1
3. build truth map + onboard map
4. build current field
5. derive climatology from field
6. build sensors per node (reusing instances across nodes of the same class)
7. open output file, write header
8. for tick in 0..N:
     for node in fleet: propagate_truth
     for node, sensor in fleet × node.sensors: sample if should_sample
     for anchor_pair in lora_pairs: sample_pair
     write tick record
10. close file
```

**Why:** A research CLI with ten explicit steps is more legible than a framework-ified plugin architecture. Each step is named in the code; anyone reading `main()` can follow the pipeline. No hidden auto-discovery, no decorators, no config file.

### D8: Observation records carry noise_sigma and unit inline

**Choice:** Each observation record in the JSONL:
```
{"t_sec": 5.010, "node_id": "n00", "sensor": "gps", "value": [36.75, -122.0], "unit": "deg", "noise_sigma": 1.5}
```

Noise σ and unit come from the sensor (inherited from `SensorSpec`). They're duplicated in every observation record.

**Why:** The PF observation model needs σ to build likelihood functions. Duplicating σ on every record (instead of normalizing to the profile) keeps the record self-contained — a PF doesn't need to load profiles to interpret observations. Cost: a few bytes per record. Benefit: the JSONL is fully self-describing.

### D9: LoRa link records are separate from observations

**Choice:** Each tick record has an `"observations"` array (single-sensor measurements) and a `"lora_links"` array (attempted inter-node ranging pairs). A successful ranging round appears in `observations` (as a `lora_toa` measurement) *and* in `lora_links` (as a successful pair). A dropped or out-of-range attempt appears only in `lora_links` (as `"dropped": true` or `"out_of_range": true`).

**Why:** The PF consumes successful range measurements via the observations stream. The dashboard wants to visualize *attempted* links (including failures) to show mesh connectivity. Splitting the streams serves both consumers cleanly.

## Risks / Trade-offs

- **[Risk] The schema will need to evolve for M2 (acoustic events, vessel truth tracks)** → Mitigated by `schema_version`. v1.1 or v2 can add new record types; readers that pin to v1 will refuse to parse, which is the correct failure.
- **[Risk] Golden trace churn masks real regressions** → Mitigation: the regenerate script prints a diff summary. Re-blessing is a review-visible commit.
- **[Risk] CLI flag `--nodes` suggests tunability that M1 doesn't support** → Mitigation: reject anything other than `--nodes 10` with a clear error message. Follow-on change can relax it.
- **[Trade-off] Header record is a different shape than tick records** → Acceptable. Readers discriminate on `record_type`. The alternative (skipping a header and putting config into a sidecar file) loses self-containment.
- **[Trade-off] noise_sigma and unit are duplicated per observation** → Acceptable cost for self-describing records. JSONL is not the place to optimize bytes.

## Key Type Contracts

```python
# scenario_schema.py

SCHEMA_VERSION: str = "1.0"
SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})


@dataclass(frozen=True, slots=True)
class ScenarioHeader:
    schema_version: str                     # must be in SUPPORTED_SCHEMA_VERSIONS
    bbox: tuple[float, float, float, float] # (lat_s, lon_w, lat_n, lon_e)
    fleet_composition: Mapping[str, int]    # {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}
    node_ids: tuple[str, ...]
    node_classes: Mapping[str, str]         # node_id -> class_name; covers every node_id
    # (values drawn from the same class_name vocabulary used in
    # fleet_composition keys; lets consumers such as the dashboard pick
    # per-class icons without an implicit ordering convention)
    seed: int
    duration_sec: float                     # CLI --duration-hours × 3600
    dt_sec: float                           # CLI --dt-sec (default 60.0)
    created_at_utc: str                     # ISO 8601
    onboard_map_path: str                   # relative path to onboard-map sidecar
    anchor_positions: Mapping[str, tuple[float, float]]
    # node_id -> (lat_deg, lon_deg); non-truth operational-survey field
    # (anchors are surveyed before drop; PF reads this without violating truth separation)


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    t_sec: float                            # per-node wall time
    node_id: str
    sensor: str                             # "gps" | "imu" | "baro" | "mag" | "lora_toa" | "bathy_probe"
    value: tuple[float, ...]
    unit: str
    noise_sigma: float


@dataclass(frozen=True, slots=True)
class LoraLinkRecord:
    t_sec: float
    node_a: str
    node_b: str
    status: str                             # "success" | "dropped" | "out_of_range"
    range_m: float | None                   # populated only when status == "success"


@dataclass(frozen=True, slots=True)
class ObservationTickView:
    """Emitted by ScenarioReader — no truth fields."""
    t: int
    t_sec: float
    observations: tuple[ObservationRecord, ...]
    lora_links: tuple[LoraLinkRecord, ...]


@dataclass(frozen=True, slots=True)
class TruthTickView:
    """Emitted by ScenarioTruthReader — includes truth + observations."""
    t: int
    t_sec: float
    node_truth: Mapping[str, numpy.ndarray]   # node_id -> state vector
    observations: tuple[ObservationRecord, ...]
    lora_links: tuple[LoraLinkRecord, ...]


class ScenarioReader:
    def __init__(self, path: str | Path): ...
    def header(self) -> ScenarioHeader: ...
    def onboard_map(self) -> RegionalMap:
        """Load the onboard map from the sidecar path declared in the header.
        Raises FileNotFoundError if the sidecar is missing. Memoized."""
    def __iter__(self) -> Iterator[ObservationTickView]:
        """Yields observation-only tick views. Never yields truth state."""

class ScenarioTruthReader:
    def __init__(self, path: str | Path): ...
    def header(self) -> ScenarioHeader: ...
    def onboard_map(self) -> RegionalMap:
        """Same sidecar as ScenarioReader. The truth map (if retained in the
        truth record) is separate and accessed via a different path."""
    def __iter__(self) -> Iterator[TruthTickView]: ...

def assert_golden_trace_matches(produced_path: Path, golden_path: Path) -> None:
    """Raises AssertionError with a unified diff on any byte-level mismatch."""
```

Construction and runtime invariants:
- `ScenarioHeader.__post_init__`: `schema_version in SUPPORTED_SCHEMA_VERSIONS`, bbox is valid, non-empty node_ids, `duration_sec > 0`, `dt_sec > 0`, `node_classes` covers exactly `node_ids` with values drawn from the class-name vocabulary used in `fleet_composition` keys (and its per-class counts equal `fleet_composition`).
- `ScenarioReader.header()` raises `ValueError` if the first line's `schema_version` is not in `SUPPORTED_SCHEMA_VERSIONS`.
- `ScenarioReader` never yields a dict or object containing truth state. Attempting to access truth via duck typing on the returned view fails — `ObservationTickView` has no `node_truth` attribute.
- `ScenarioTruthReader` is importable only from `rtl.vectors.maritime.scenario_schema` — it is not re-exported from any module a PF implementation would import.

## Integrity-Charter Mapping

- **Truth separation** — `ScenarioReader` vs. `ScenarioTruthReader` split enforces the charter's "PF code never sees truth" contract at the type level.
- **Schema versioning** — forward-contract insurance for M2 schema evolution.
- **Level 0 (Physics Truth)** — composed from `maritime-current-fields` + `maritime-fleet-dynamics` via the generator's tick loop.
- **Level 1 (Sensor Model)** — composed from `maritime-sensors` sampler outputs.
- **Level 2 (Comms)** — LoRa TOA range + drop + TDMA land in the JSONL as observation records + link records. Clock stubs (zero-offset) satisfy the schema's per-node timestamp contract; M2 activation is transparent.
- **Level 4 (Imperfect Map)** — generator builds truth map AND onboard map via `make_onboard_map`. Only truth map is used for observation generation; the onboard map is included in the scenario config for PF consumption. (Actually, on second look — the PF should load the onboard map on its own, not receive it via the scenario. Decided: the scenario header includes a reference to the map construction parameters — seed + fidelity — so the PF can reconstruct the same onboard map deterministically. This keeps the JSONL compact.)
