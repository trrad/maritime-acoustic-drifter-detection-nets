## ADDED Requirements

### Requirement: Versioned PF Estimate JSONL Schema
The system SHALL define a newline-delimited JSON format for the PF's
main estimate stream. The first line SHALL be a header record with
`"record_type": "header"` and a `schema_version` string. Subsequent
lines SHALL be estimate records with `"record_type": "estimate"`. The
constant `PF_ESTIMATE_SCHEMA_VERSION` SHALL be `"1.0"`. Readers SHALL
raise `ValueError` on unknown schema versions.

#### Scenario: Reader accepts v1.0 estimate file
- **WHEN** `PFEstimateReader` is constructed with a valid v1.0 file
- **THEN** `reader.header().schema_version == "1.0"`

#### Scenario: Reader rejects unknown version
- **WHEN** a file declares `"schema_version": "2.0"`
- **THEN** reader construction raises `ValueError`

### Requirement: PF Estimate Header Structure
The main estimate stream header SHALL contain `record_type`,
`schema_version`, `scenario_path`, `scenario_seed`, `pf_impl`
(implementation name string, e.g., `"float64_bootstrap"`),
`n_particles`, `node_ids` (tuple of strings covering every node the
PF ran), and `created_at_utc`. `scenario_seed` SHALL match the seed of
the scenario that produced the estimates, enabling cross-check. The
header SHALL NOT contain `focus_node_ids` or any privileged-subset
field — the main stream emits records for every node in `node_ids`.

#### Scenario: Valid header decodes
- **WHEN** a valid header is parsed
- **THEN** a `PFEstimateHeader` is returned with all fields populated

#### Scenario: Header with non-positive n_particles is rejected
- **WHEN** a header declares `n_particles=0`
- **THEN** `ValueError` is raised

#### Scenario: Header has no focus_node_ids field
- **WHEN** a header is constructed
- **THEN** it has no attribute `focus_node_ids`
- **AND** `node_ids` enumerates every node the main stream covers

#### Scenario: Header echoes CLI inputs and PF configuration
- **WHEN** the CLI is invoked with `--scenario /tmp/s.jsonl --out /tmp/e.jsonl --n-particles 500` against a scenario whose header has `seed == 42` and `node_ids == (n00, ..., n09)`
- **AND** `PFEstimateReader('/tmp/e.jsonl').header()` is parsed
- **THEN** `header.scenario_path` identifies `/tmp/s.jsonl` (either verbatim or in a canonical form the CLI documents, e.g., absolute path)
- **AND** `header.scenario_seed == 42` (propagated from the source scenario's header, not derived from the CLI)
- **AND** `header.n_particles == 500` (matches the `--n-particles` argument the PF actually ran with)
- **AND** `header.pf_impl == "float64_bootstrap"` (stable identifier for this PF implementation, used by downstream comparisons such as the M2 float-vs-LNS8 harness)
- **AND** `header.node_ids` has the same membership as the source scenario's `header.node_ids` (the PF ran one instance per fleet node)

### Requirement: PF Estimate Record Structure
Each estimate record SHALL contain `record_type="estimate"`, `t`,
`t_sec`, `node_id`, `mean` (list of floats of length matching the
node's layout state_dim), `cov_diag` (list of floats, same length,
non-negative entries), and `n_effective` (float strictly greater
than zero and less than or equal to `n_particles`). Estimate records
SHALL NOT carry `particles` or `weights` fields — particle-level data
lives in the separate sidecar stream.

#### Scenario: Estimate record has no particles or weights
- **WHEN** a valid estimate record is parsed
- **THEN** the record has no `particles` attribute
- **AND** the record has no `weights` attribute

#### Scenario: cov_diag is non-negative
- **WHEN** an estimate record with any negative `cov_diag` entry is parsed
- **THEN** `ValueError` is raised

#### Scenario: n_effective is strictly positive and bounded
- **WHEN** an estimate record with `n_effective <= 0` or `n_effective > n_particles` is parsed
- **THEN** `ValueError` is raised

#### Scenario: mean and cov_diag lengths match
- **WHEN** an estimate record is parsed with `mean` length 15 and `cov_diag` length 14
- **THEN** `ValueError` is raised (shape mismatch)

### Requirement: PFEstimateReader Contract
The `PFEstimateReader` class SHALL expose a `header()` method
returning the parsed `PFEstimateHeader` and an `__iter__` method
yielding `PFEstimateRecord` objects. The reader SHALL be independent
of any scenario state — it operates on the estimate file alone.

#### Scenario: Reader yields typed records
- **WHEN** `PFEstimateReader` iterates a valid file
- **THEN** every yielded object is a `PFEstimateRecord`, not a dict

#### Scenario: Header and records linkable by scenario metadata
- **WHEN** a `PFEstimateReader` is opened on a file produced from a scenario with seed 42
- **THEN** `reader.header().scenario_seed == 42`

### Requirement: Particle Sidecar Schema
The system SHALL define a separate newline-delimited JSON format for
the particle sidecar stream. The sidecar SHALL have its own header
record (`"record_type": "particle_header"`) and particle records
(`"record_type": "particle"`). The sidecar SHALL share the main
stream's `schema_version` (`"1.0"`). Readers SHALL raise `ValueError`
on unknown versions.

The sidecar header SHALL contain: `schema_version`,
`parent_estimate_path` (path to the main estimate stream this sidecar
accompanies), `scenario_seed`, `n_particles_full` (the PF's actual
particle count), `thin_ticks` (the tick-thinning value used),
`thin_particles` (the subsampled particle count per emitted record),
`thin_nodes` (either a tuple of node_ids or null meaning all nodes),
and `created_at_utc`.

Each particle record SHALL contain `record_type="particle"`, `t`,
`t_sec`, `node_id`, `particles` (a list of `thin_particles`-many
lists, each matching the node's layout state_dim), and `weights`
(length `thin_particles`, non-negative floats summing to 1.0 within a
small tolerance).

#### Scenario: Sidecar header declares thinning config
- **WHEN** a valid sidecar header is parsed
- **THEN** `header.thin_ticks >= 1`
- **AND** `header.thin_particles >= 1`
- **AND** `header.thin_particles <= header.n_particles_full`

#### Scenario: Sidecar with all nodes
- **WHEN** a sidecar header has `thin_nodes = null`
- **THEN** the parsed header's `thin_nodes` is `None`, meaning all fleet nodes
  produced records in the sidecar

#### Scenario: Sidecar with a node subset
- **WHEN** a sidecar header declares `thin_nodes = ["n01", "n05"]`
- **THEN** every particle record in the sidecar has `node_id` in that subset

#### Scenario: Particle record particles shape matches thin_particles × state_dim
- **WHEN** a sidecar's header has `thin_particles = 50` and a participating node has layout `state_dim = 25`
- **THEN** every particle record for that node has `particles` shape `(50, 25)`

#### Scenario: Particle record weights sum to one
- **WHEN** a particle record is parsed
- **THEN** the sum of `weights` is within `1e-6` of 1.0

#### Scenario: Reader raises on shape mismatch
- **WHEN** a particle record has `particles` of shape `(40, 25)` but the header declares `thin_particles = 50`
- **THEN** `ValueError` is raised

#### Scenario: Sidecar header echoes CLI configuration
- **WHEN** the CLI is invoked with `--out /tmp/e.jsonl --particles-out /tmp/p.jsonl --n-particles 500 --thin-ticks 10 --thin-particles 50 --thin-nodes n01,n05` against a scenario whose header has `seed == 42`
- **AND** `ParticleStreamReader('/tmp/p.jsonl').header()` is parsed
- **THEN** `header.parent_estimate_path` identifies `/tmp/e.jsonl` (verbatim or in a documented canonical form)
- **AND** `header.scenario_seed == 42` (propagated from the source scenario's header)
- **AND** `header.n_particles_full == 500` (matches the `--n-particles` argument the PF actually ran with)
- **AND** `header.thin_ticks == 10` and `header.thin_particles == 50`
- **AND** `header.thin_nodes == ("n01", "n05")` (or the canonical tuple form of the comma-separated CLI value)

### Requirement: ParticleStreamReader Contract
The `ParticleStreamReader` class SHALL expose a `header()` method
returning the parsed sidecar header, an `__iter__` method yielding
`ParticleRecord` objects, and a `node_ids_present()` method returning
a `frozenset[str]` of all node_ids that appear at least once in the
sidecar (for dashboard discovery without iterating the full file).
The reader SHALL NOT require access to the main estimate stream or
to the scenario file.

#### Scenario: Reader yields typed records
- **WHEN** `ParticleStreamReader` iterates a valid sidecar
- **THEN** every yielded object is a `ParticleRecord`, not a dict

#### Scenario: node_ids_present covers appearing nodes
- **WHEN** a sidecar contains records for `n01` and `n05` only
- **THEN** `reader.node_ids_present() == frozenset({"n01", "n05"})`

#### Scenario: Reader handles empty sidecar gracefully
- **WHEN** a sidecar has a valid header and zero particle records (e.g., `--no-particles` was implicitly configured but header still wrote)
- **THEN** iteration yields zero records and `node_ids_present()` returns `frozenset()`

### Requirement: ParticleStreamWriter Interface
The system SHALL provide a `ParticleStreamWriter` type with
`write_header(header)`, `write_record(record)`, and `close()` methods.
A concrete JSONL-backed implementation SHALL be provided via
`make_jsonl_particle_writer(path)`. Producers SHALL depend on the
`ParticleStreamWriter` type, not on the JSONL class directly, so the
backing format is swappable without touching callers.

#### Scenario: JSONL writer round-trips with reader
- **WHEN** a `ParticleStreamWriter` backed by JSONL writes a header and three records, then a `ParticleStreamReader` opens the same file
- **THEN** the reader yields the three records with identical field values

#### Scenario: Writer enforces header-before-records ordering
- **WHEN** `write_record` is called before `write_header`
- **THEN** the writer raises a `RuntimeError` or equivalent

#### Scenario: Close is idempotent
- **WHEN** `close()` is called twice on the same writer
- **THEN** no exception is raised
