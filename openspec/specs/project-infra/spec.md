## Purpose

Project infrastructure: dependency management, test runner configuration, shared fixtures, type checking scope, and frozen baseline protection.

## Requirements

### Requirement: Dependency Manifest
The project SHALL declare all runtime dependencies in a `pyproject.toml` at the repository root. The declared dependencies SHALL include at minimum numpy, scipy, and matplotlib. Running `uv run python <script>` SHALL resolve and use these dependencies without requiring `--with` flags.

#### Scenario: Dependencies resolve without --with flags
- **WHEN** a Python script imports numpy, scipy, or matplotlib
- **AND** the script is invoked via `uv run python <script>`
- **THEN** the imports succeed without error

### Requirement: Test Runner Configuration
The project SHALL configure pytest via `pyproject.toml` with `testpaths = ["tests"]`. Running `uv run pytest` SHALL discover and execute all tests in the `tests/` directory.

#### Scenario: pytest discovers tests
- **WHEN** `uv run pytest` is executed from the repository root
- **THEN** pytest collects tests from the `tests/` directory
- **AND** reports pass/fail/skip counts for collected items

#### Scenario: pytest ignores experiment and RTL directories
- **WHEN** `uv run pytest` is executed
- **THEN** no test collection occurs in `experiments/` or `rtl/`

### Requirement: Deterministic RNG Fixtures
The `tests/conftest.py` SHALL provide a `make_rng` factory fixture that returns a seeded `numpy.random.Generator`. The default seed SHALL be 42. Every test that uses randomness SHALL obtain its RNG through this fixture rather than calling `numpy.random` global functions.

#### Scenario: Repeated test runs produce identical results
- **WHEN** a test uses `make_rng` to generate random values
- **AND** the test is run twice with no code changes
- **THEN** both runs produce identical values and identical pass/fail outcomes

#### Scenario: RNG seed is overridable
- **WHEN** a test is run with a custom seed value
- **THEN** the `make_rng` fixture uses the provided seed instead of the default

### Requirement: Numerical Tolerance Assertions
The `tests/conftest.py` SHALL provide an `assert_close` helper function that wraps `numpy.testing.assert_allclose`. The helper SHALL accept `atol` (absolute tolerance) and `rtol` (relative tolerance) parameters with physical-unit documentation. The convention is that absolute tolerances are expressed in physical units (meters, m/s, seconds) and are the primary tolerance mechanism.

#### Scenario: assert_close with absolute tolerance in meters
- **WHEN** `assert_close(actual, desired, atol=0.5, msg="position RMSE")` is called
- **AND** all elements of `actual` are within 0.5 of `desired`
- **THEN** the assertion passes without error

#### Scenario: assert_close detects out-of-tolerance values
- **WHEN** `assert_close(actual, desired, atol=0.5, msg="position RMSE")` is called
- **AND** any element of `actual` differs from `desired` by more than 0.5
- **THEN** the assertion fails with an error message including the physical-unit context

### Requirement: Type Checking Scope
The `pyrightconfig.json` SHALL include `experiments`, `rtl/vectors/maritime`, and `tests` in its type checking scope. Running pyright against the repository SHALL analyze all active code directories.

#### Scenario: pyright analyzes maritime package
- **WHEN** pyright is run against the repository
- **THEN** it reports type diagnostics for files in `rtl/vectors/maritime/`

#### Scenario: pyright analyzes test files
- **WHEN** pyright is run against the repository
- **THEN** it reports type diagnostics for files in `tests/`

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

### Requirement: Frozen Baseline Protection
The experiment files `experiments/01_eml_basics.py` through `experiments/11_pf_dashboard.py` and the existing RTL Python harness files in `rtl/vectors/` SHALL NOT be modified by this or any subsequent change. New code SHALL be added in new files alongside the frozen baseline.

#### Scenario: frozen files are not modified
- **WHEN** a change's implementation is complete
- **THEN** `git diff` shows zero modifications to files `experiments/01*.py` through `experiments/11*.py`
- **AND** `git diff` shows zero modifications to existing files in `rtl/vectors/` (excluding new files in `rtl/vectors/maritime/`)
