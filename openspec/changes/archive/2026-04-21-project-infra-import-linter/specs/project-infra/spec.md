## ADDED Requirements

### Requirement: Import Boundary Enforcement
The project SHALL enforce module import boundaries via `import-linter`, a
Python import-contract checker that operates on the AST-derived import
graph. The tool SHALL be declared as a dev dependency in `pyproject.toml`.
Contract configuration SHALL live in a `[tool.importlinter]` section of
`pyproject.toml`. Running `uv run lint-imports` SHALL exit nonzero if any
configured contract is violated. The `lint-imports` check SHALL be part of
the project's verification protocol at equal status to `uv run pytest` —
both MUST pass before a change is considered complete.

#### Scenario: import-linter is declared as a dev dependency
- **WHEN** `pyproject.toml` is inspected
- **THEN** `import-linter` appears in the project's dev dependency
  declaration (e.g., a `[dependency-groups.dev]` array or equivalent
  dev-scoped location)

#### Scenario: import-linter configuration section is present
- **WHEN** `pyproject.toml` is inspected
- **THEN** a `[tool.importlinter]` section exists with a `root_package`
  setting
- **AND** the `root_package` value is `"rtl.vectors.maritime"` (scope of
  the enforced boundaries)

#### Scenario: lint-imports runs via uv without extra flags
- **WHEN** `uv run lint-imports` is executed from the repository root
- **THEN** the command succeeds without `ModuleNotFoundError` or
  dependency resolution errors
- **AND** the exit code reflects contract compliance: zero if all contracts
  pass, nonzero if any violation

#### Scenario: empty contracts list is a valid configuration
- **WHEN** `[tool.importlinter]` exists and no
  `[[tool.importlinter.contracts]]` entries are declared
- **THEN** `uv run lint-imports` exits zero (no contracts to check)
- **AND** the configuration is still valid for downstream changes to
  append contracts

#### Scenario: verification protocol requires both pytest and lint-imports
- **WHEN** the project's verification pipeline is run (currently manual:
  `uv run pytest` + `uv run lint-imports`; CI when it lands)
- **THEN** both commands MUST exit zero for verification to pass
- **AND** a contract violation detected by `lint-imports` blocks the
  change at the same status as a failing test
