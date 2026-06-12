## ADDED Requirements

### Requirement: Versioned JSONL Schema
The system SHALL define a newline-delimited JSON scenario format where the first line is a header record with `"record_type": "header"` and a `"schema_version"` string field, and subsequent lines are tick records with `"record_type": "tick"`. The constant `SCHEMA_VERSION` SHALL be `"1.0"` in this change. The constant `SUPPORTED_SCHEMA_VERSIONS` SHALL be a frozen set containing at minimum `"1.0"`. Readers SHALL raise `ValueError` when opening a file whose header declares a version not in `SUPPORTED_SCHEMA_VERSIONS`.

#### Scenario: Reader accepts v1.0 file
- **WHEN** `ScenarioReader` is constructed with a path to a valid v1.0 JSONL file
- **THEN** `reader.header().schema_version == "1.0"`
- **AND** iteration yields tick records

#### Scenario: Reader rejects unknown version
- **WHEN** `ScenarioReader` is constructed with a file whose header declares `"schema_version": "2.0"`
- **THEN** construction (or first read) raises `ValueError` naming both the unknown version and the supported set

#### Scenario: Reader rejects missing header
- **WHEN** `ScenarioReader` is constructed with a file whose first line is a tick record, not a header
- **THEN** construction raises `ValueError` citing the missing or malformed header

### Requirement: Header Record Structure
The header record SHALL contain the fields `record_type`,
`schema_version`, `bbox`, `fleet_composition`, `node_ids`,
`node_classes`, `seed`, `duration_sec`, `dt_sec`, `created_at_utc`,
`onboard_map_path`, and `anchor_positions`. `bbox` SHALL be a 4-tuple
of `(lat_south, lon_west, lat_north, lon_east)` in degrees.
`fleet_composition` SHALL be a mapping from class name to node count.
`node_ids` SHALL be a list of the node identifiers present in the
scenario. `node_classes` SHALL be a mapping from every `node_id` in
`node_ids` to its class-name string (drawn from the same class-name
vocabulary used as keys in `fleet_composition` — currently
`"anchor"`, `"ballast_drifter"`, `"pure_drifter"`). Consumers that
need per-node class information (e.g., the dashboard picking per-class
icons) SHALL read `node_classes` directly rather than relying on any
implicit ordering of `node_ids`. Per-class counts derived from
`node_classes` SHALL equal `fleet_composition`. `duration_sec` and
`dt_sec` SHALL be positive floats; their ratio determines the tick
count. `onboard_map_path` SHALL be a string naming the onboard-map
sidecar file relative to the scenario file's directory (see
`maritime-scenario-gen` Requirement: Onboard Map Distributed As
Scenario Sidecar). `anchor_positions` SHALL be a mapping from anchor
`node_id` to `(lat_deg, lon_deg)` — a non-truth operational-survey
field modeling the real deployment pattern where anchor positions are
surveyed before drop and known to every consumer; readable by the PF
without violating truth separation. `anchor_positions` SHALL cover
exactly the node_ids classified as anchors (by `node_classes`).

#### Scenario: Header carries anchor_positions for every anchor
- **WHEN** a scenario header declares `fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}` and anchor `node_ids` `["n00", "n01"]`
- **THEN** `header.anchor_positions` is a mapping with exactly those two keys
- **AND** each value is a `(lat_deg, lon_deg)` tuple of floats

#### Scenario: anchor_positions missing anchor key is rejected
- **WHEN** a header declares two anchors but `anchor_positions` has only one entry
- **THEN** parsing raises `ValueError` naming the missing anchor node_id

#### Scenario: anchor_positions containing a non-anchor key is rejected
- **WHEN** a header declares `anchor_positions` with a key that is not classified as an anchor in `fleet_composition`
- **THEN** parsing raises `ValueError` naming the extraneous key

#### Scenario: Header carries node_classes for every node_id
- **WHEN** a scenario header declares `node_ids = ("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09")` and `fleet_composition = {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}`
- **THEN** `header.node_classes` is a mapping with exactly those ten keys
- **AND** each value is one of `"anchor"`, `"ballast_drifter"`, `"pure_drifter"`
- **AND** grouping `node_classes` by value yields counts equal to `fleet_composition` (2 anchors, 4 ballast drifters, 4 pure drifters)

#### Scenario: node_classes missing a node_id is rejected
- **WHEN** a header declares 10 `node_ids` but `node_classes` has only 9 entries
- **THEN** parsing raises `ValueError` naming the missing `node_id`

#### Scenario: node_classes with an extraneous node_id is rejected
- **WHEN** a header's `node_classes` contains a key not present in `node_ids`
- **THEN** parsing raises `ValueError` naming the extraneous key

#### Scenario: node_classes counts inconsistent with fleet_composition is rejected
- **WHEN** a header declares `fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}` but `node_classes` values contain 3 anchors and 3 ballast drifters and 4 pure drifters
- **THEN** parsing raises `ValueError` citing the mismatch between `node_classes` counts and `fleet_composition`

#### Scenario: Valid header constructs ScenarioHeader
- **WHEN** a header record with all required fields is parsed
- **THEN** a `ScenarioHeader` is returned with matching field values

#### Scenario: Header with bbox inversion is rejected
- **WHEN** a header declares bbox `(49.0, -123.2, 48.4, -123.8)` (lat_south > lat_north)
- **THEN** `ValueError` is raised citing the invalid bbox

#### Scenario: Header with non-positive duration is rejected
- **WHEN** a header declares `duration_sec=0`
- **THEN** `ValueError` is raised

#### Scenario: Header with non-positive dt_sec is rejected
- **WHEN** a header declares `dt_sec=0` or a negative value
- **THEN** `ValueError` is raised

#### Scenario: Header exposes dt_sec to readers
- **WHEN** a valid header is parsed
- **THEN** `header.dt_sec` returns the tick-interval float from the file
- **AND** downstream tooling can compute tick count as `ceil(header.duration_sec / header.dt_sec)` without consulting the CLI arguments

### Requirement: Tick Record Structure
Each tick record SHALL contain `record_type="tick"`, an integer `t` (tick index), a float `t_sec` (elapsed simulation seconds), and two arrays: `observations` (per-sensor measurements) and `lora_links` (attempted inter-node ranging rounds). Tick records MAY contain a `nodes` field with per-node truth state; the observation-only reader SHALL strip this field before yielding.

#### Scenario: Tick with observations is parsed
- **WHEN** a tick record with 5 observation entries is parsed
- **THEN** the resulting view has `len(observations) == 5`
- **AND** each observation is an `ObservationRecord`

#### Scenario: Tick with missing required field is rejected
- **WHEN** a tick record omits `t_sec`
- **THEN** parsing raises `ValueError` citing the missing field

### Requirement: ObservationRecord Structure
Each entry in a tick's `observations` array SHALL decode into an `ObservationRecord` with fields `t_sec`, `node_id`, `sensor`, `value` (list of floats), `unit`, and `noise_sigma`. The `sensor` field SHALL be one of the six M1 vocabulary strings. Sigma SHALL equal the producing sensor's `SensorSpec.noise_sigma`.

#### Scenario: Valid observation decodes successfully
- **WHEN** an observation entry `{"t_sec": 5.01, "node_id": "n00", "sensor": "gps", "value": [36.75, -122.0], "unit": "deg", "noise_sigma": 1.5}` is parsed
- **THEN** an `ObservationRecord` is returned with matching fields

#### Scenario: Observation with unknown sensor is rejected
- **WHEN** an observation declares `"sensor": "radar"` (not in the M1 vocabulary)
- **THEN** parsing raises `ValueError` naming the unknown sensor

### Requirement: LoraLinkRecord Structure
Each entry in a tick's `lora_links` array SHALL decode into a `LoraLinkRecord` with fields `t_sec`, `node_a`, `node_b`, `status` (one of `"success"`, `"dropped"`, `"out_of_range"`), and `range_m` (float or None). When `status == "success"`, `range_m` SHALL be non-None; otherwise it SHALL be None.

#### Scenario: Successful link has range
- **WHEN** a link record with `"status": "success", "range_m": 3500.0` is parsed
- **THEN** the resulting `LoraLinkRecord` has `range_m == 3500.0`

#### Scenario: Dropped link has None range
- **WHEN** a link record with `"status": "dropped"` is parsed
- **THEN** the resulting record has `range_m is None`

#### Scenario: Successful link without range is rejected
- **WHEN** a link record declares `"status": "success"` but omits `range_m`
- **THEN** parsing raises `ValueError`

### Requirement: ScenarioReader Strips Truth
The `ScenarioReader` iterator SHALL yield `ObservationTickView` objects
that contain observations and LoRa links but NOT node truth state. The
`ObservationTickView` type SHALL have no `node_truth` attribute, no
`truth` key, and no method that exposes truth fields — the exclusion
SHALL be enforced at the type level, not at runtime. The
`ScenarioReader` class SHALL live in
`rtl/vectors/maritime/scenario_schema.py` alongside the observation
types it yields; this module SHALL NOT define any truth-access type or
reader. Truth access is provided by a separate module
(`rtl/vectors/maritime/scenario_truth_schema.py`, per capability
`maritime-scenario-truth-schema`).

#### Scenario: Observation view has no truth fields
- **WHEN** `ScenarioReader` iterates a file whose tick records include `"nodes"` truth state
- **THEN** the yielded view objects have no attribute named `node_truth`, `truth`, or `nodes`
- **AND** accessing such an attribute raises `AttributeError`

#### Scenario: ScenarioReader yields typed views, not raw dicts
- **WHEN** a `ScenarioReader` iterates a valid file
- **THEN** each yielded object is an instance of `ObservationTickView`, not a `dict`

#### Scenario: scenario_schema module defines no truth types
- **WHEN** `rtl/vectors/maritime/scenario_schema.py` is imported and its public names are inspected
- **THEN** no name corresponds to `ScenarioTruthReader`, `TruthTickView`, or any truth-access type
- **AND** an attempt to `from rtl.vectors.maritime.scenario_schema import ScenarioTruthReader` raises `ImportError`

### Requirement: Golden Trace Comparison Helper
The system SHALL provide an `assert_golden_trace_matches(produced_path, golden_path)` function that raises `AssertionError` with a unified diff whenever the two files differ at the byte level. The function SHALL NOT attempt to "normalize" formatting differences (whitespace, key ordering) — byte-for-byte identity is the contract.

#### Scenario: Identical files match
- **WHEN** `assert_golden_trace_matches` is called with two identical files
- **THEN** the function returns None without raising

#### Scenario: Files differing by one byte raise with a diff
- **WHEN** two files differ in a single byte
- **THEN** `assert_golden_trace_matches` raises `AssertionError`
- **AND** the exception message contains a unified diff showing the differing line(s)
