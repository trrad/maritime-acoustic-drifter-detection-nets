## Why

Truth separation in the maritime simulation must be enforced at the import
boundary by an AST-based tool — "PF modules cannot import
`scenario_truth_schema` or `current_fields`" needs to be a checked contract,
not a source-text regex, a naming convention, or a prose rule. That
capability doesn't yet exist at the project level. Without it,
`maritime-scenario-gen` and `maritime-pf-float` would each need to roll
their own enforcement, or worse, ship with none and rely on developer
discipline — the exact anti-pattern that AGENTS.md ("Enforcement over
instruction") rules out. This change installs `import-linter` as project
infrastructure so downstream changes declare contracts, not tooling.

## What Changes

- Add `import-linter` as a dev dependency in `pyproject.toml` (it pulls
  `grimp` transitively for AST import-graph parsing).
- Add a `[tool.importlinter]` config section with
  `root_package = "rtl.vectors.maritime"` and an empty contracts list.
  Downstream changes append contracts as they need them.
- Establish `uv run lint-imports` as part of the project's verification
  pipeline alongside `uv run pytest`. Both must exit zero for a change to
  be considered complete.
- Modify the `project-infra` standing spec to add a new requirement —
  Import Boundary Enforcement — establishing that import-linter is
  configured and run as part of verification, and that contract violations
  are a blocking failure.

No runtime code changes. No new modules under `rtl/`. Frozen baseline
untouched.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `project-infra`: adds **Import Boundary Enforcement** requirement —
  `import-linter` configured via `pyproject.toml`, contracts checked via
  `uv run lint-imports`, failure blocks verification.

## Impact

- **Modified files**: `pyproject.toml` (dev dep + `[tool.importlinter]` config).
- **New files**: none at this change; downstream changes register contract
  entries.
- **Dependencies**: `import-linter` (MIT-licensed, pulls `grimp` for
  AST-based import-graph analysis).
- **Downstream consumers**: `maritime-scenario-gen` (registers the
  truth-schema boundary contract), `maritime-pf-float` (relies on the
  enforced boundary). Both blocked on this change landing first.
- **Frozen baseline**: untouched.
- **Philosophy**: delivers the "enforce with tools, not prompts"
  commitment from AGENTS.md for import boundaries specifically.
