## ADDED Requirements

### Requirement: Truth Schema Module Location
The system SHALL define truth-access types and the `ScenarioTruthReader`
class in a dedicated module at
`rtl/vectors/maritime/scenario_truth_schema.py` — physically separate
from the observation-only `rtl/vectors/maritime/scenario_schema.py`.
The module SHALL NOT be re-exported from the package's `__init__.py`;
consumers SHALL import it via its full module path, marking their
intent to read truth data at the import site. The module split is the
substrate that makes the `import-linter` contract (owned by
`project-infra-import-linter`, with PF-specific rules registered by
`maritime-pf-float`) meaningful — forbidding `scenario_truth_schema`
in PF source modules is only possible because it's a separate module.

#### Scenario: Truth module exists at the specified path
- **WHEN** the filesystem is inspected after this change is applied
- **THEN** `rtl/vectors/maritime/scenario_truth_schema.py` exists
- **AND** `rtl/vectors/maritime/scenario_schema.py` exists separately

#### Scenario: Truth module is not re-exported from package __init__
- **WHEN** `from rtl.vectors.maritime import ScenarioTruthReader` is attempted
- **THEN** `ImportError` is raised
- **AND** `from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader` succeeds

#### Scenario: Truth types live only in the truth module
- **WHEN** `rtl/vectors/maritime/scenario_schema.py` is inspected for public names
- **THEN** no name corresponds to `TruthTickView`, `ScenarioTruthReader`, or any other truth-access type — those are defined only in `scenario_truth_schema`

### Requirement: TruthTickView Structure
The truth module SHALL define a `TruthTickView` frozen dataclass with
fields `t: int`, `t_sec: float`, `node_truth: Mapping[str, numpy.ndarray]`,
`observations: tuple[ObservationRecord, ...]`, and
`lora_links: tuple[LoraLinkRecord, ...]`. The `node_truth` mapping
SHALL hold per-node state ndarrays whose length equals the node's
`StateLayout.state_dim`. The `ObservationRecord` and `LoraLinkRecord`
types SHALL be imported from `scenario_schema` (truth views carry the
same observation types; truth tooling consumes both truth state and
observations for side-by-side comparison).

#### Scenario: Valid truth view constructs successfully
- **WHEN** a `TruthTickView` is constructed with valid `t`, `t_sec`, a `node_truth` mapping for all fleet nodes, and observation/lora_link tuples
- **THEN** the view is immutable
- **AND** `view.node_truth[node_id].shape == (layout.state_dim,)` for each node_id

#### Scenario: TruthTickView references obs types from scenario_schema
- **WHEN** `scenario_truth_schema.py` source is inspected
- **THEN** `ObservationRecord` and `LoraLinkRecord` are imported from `rtl.vectors.maritime.scenario_schema`, not redefined locally

### Requirement: ScenarioTruthReader Contract
The `ScenarioTruthReader` class SHALL parse the same JSONL files that
`ScenarioReader` parses (the two readers share the file format), but
its `__iter__` SHALL yield `TruthTickView` objects populated with
per-node truth state. The reader SHALL expose a `header()` method
returning the parsed `ScenarioHeader` (imported from `scenario_schema`).
The reader SHALL raise `ValueError` on files whose header declares an
unsupported `schema_version` (shared version set with
`scenario_schema`).

#### Scenario: Reader yields TruthTickView with populated node_truth
- **WHEN** `ScenarioTruthReader` iterates a valid file whose tick records include per-node `"nodes"` truth state
- **THEN** every yielded object is a `TruthTickView` (not a dict)
- **AND** `view.node_truth` contains an ndarray entry for every fleet node_id declared in the header

#### Scenario: Reader shares header type with scenario_schema
- **WHEN** `ScenarioTruthReader(path).header()` is called
- **THEN** the returned object is an instance of `scenario_schema.ScenarioHeader` (not a duplicate type)

#### Scenario: Reader rejects unknown schema version
- **WHEN** a file declares `"schema_version": "2.0"` in its header
- **THEN** reader construction (or first iteration) raises `ValueError`

### Requirement: Truth Reader Consumers Are Explicit
The `ScenarioTruthReader` SHALL be imported only by modules whose job
is to consume truth — specifically, the dashboard
(`experiments/12_maritime_dashboard.py` or equivalent) and future
validation harnesses (`maritime-validate`, M2). PF modules SHALL NOT
import from `scenario_truth_schema`; this restriction is enforced by
the `import-linter` contract registered in `pyproject.toml` by
`maritime-pf-float`.

#### Scenario: Import-linter contract covers scenario_truth_schema
- **WHEN** the `pyproject.toml` import-linter configuration is inspected after `maritime-pf-float` lands
- **THEN** a contract lists `rtl.vectors.maritime.scenario_truth_schema` in `forbidden_modules` for PF source modules

#### Scenario: Dashboard is allowed to import
- **WHEN** the dashboard module imports `ScenarioTruthReader` from `scenario_truth_schema`
- **THEN** the import succeeds
- **AND** the import-linter contract does not flag the dashboard (dashboard is not in the contract's `source_modules`)
