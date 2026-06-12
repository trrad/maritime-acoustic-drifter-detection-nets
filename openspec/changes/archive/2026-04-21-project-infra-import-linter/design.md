## Context

Truth separation in the maritime simulation requires that PF modules cannot
import truth-bearing modules (`scenario_truth_schema`, `current_fields`).
Without a tool-based boundary check, this is enforced by convention or by
regex source-scanning — both fragile, and both contradicting the project's
"enforcement over instruction" principle (AGENTS.md).

No import-boundary tool exists in the project today. The 6D POC didn't need
one (single-module PF, no truth/observation split). The maritime expansion
introduces a contract the type system alone cannot express: "module A must
not import module B." That's exactly what `import-linter` is designed to
check.

## Goals / Non-Goals

**Goals:**
- Install `import-linter` as a dev dependency with a `pyproject.toml` config
  section.
- Establish `uv run lint-imports` as part of the verification protocol,
  equal in status to `uv run pytest`.
- Provide an initial `[tool.importlinter]` block with an empty contracts
  list so downstream changes append contracts without touching structure.
- Modify the `project-infra` standing spec to make the tool plus verification
  protocol a spec-level requirement.

**Non-Goals:**
- Defining specific contract rules. Those land with the changes that need
  them — `maritime-scenario-gen` registers the truth-schema boundary,
  `maritime-pf-float` registers the climatology-vs-truth boundary.
- Provisioning a CI system. There is no CI today. When one lands, the
  requirement is that `lint-imports` joins `pytest` as one of its gates.
- Pre-commit hook integration. Useful but out of scope.
- Type-checker plugin work. `pyright` covers type-level enforcement;
  `import-linter` covers the orthogonal concern of which modules may import
  which.

## Decisions

### D1: `import-linter` over `grimp`-direct or custom AST script

**Choice:** Use `import-linter` (which uses `grimp` internally), not a
custom AST walker.

**Why:** `import-linter` is the standard Python tool for this, produces
good error messages, and has a declarative TOML config that downstream
changes extend without writing code. A custom script would duplicate work
and miss `grimp`'s handling of relative imports, conditional imports, and
re-exports. Using `grimp` directly would require coding every contract —
we'd be rebuilding `import-linter`.

**Alternative considered:** `pydeps` — visualization only, no enforcement.

### D2: Config in `pyproject.toml`, not `.importlinter`

**Choice:** Use a `[tool.importlinter]` section in `pyproject.toml`.

**Why:** `pyproject.toml` is already the project's configuration hub
(dependency manifest, pytest config, pyright config). Adding a separate
`.importlinter` file fragments the surface. `import-linter` supports both;
`pyproject.toml` is the modern choice and keeps the config co-located with
its consumers.

### D3: Empty contracts list is a valid starting state

**Choice:** The initial config has `root_package = "rtl.vectors.maritime"`
and no contracts. Running `lint-imports` on the empty config exits zero.

**Why:** Decouples the tool install from specific contract rules.
Downstream changes append contracts as they need them, rather than this
change anticipating what they'll want. Also means this change can land and
be archived without introducing new failure modes.

### D4: Verification parity with `pytest`

**Choice:** The spec places `uv run lint-imports` at equal status to
`uv run pytest`. Both must pass for a change to be considered complete.

**Why:** Matches how other `project-infra` requirements are framed
(`pytest` is part of the verification pipeline). Avoids implying specific
CI mechanisms the project doesn't yet have. The requirement is behavioral —
whatever verification process runs, `lint-imports` is in it.

### D5: `root_package` scope

**Choice:** `root_package = "rtl.vectors.maritime"`.

**Why:** The truth-separation and climatology-vs-truth contracts operate
within the maritime package. The 6D POC under `rtl/vectors/` (outside
`rtl/vectors/maritime/`) has no import boundaries to enforce — it's a
single-module POC. Scoping to `rtl.vectors.maritime` keeps the config
surface narrow and the import-graph analysis fast. If the maritime module
eventually moves to top-level `maritime/`, this is a one-line update.

## Risks / Trade-offs

- **[Risk]** `import-linter` config churn if module paths change.
  **Mitigation:** path updates are one-line edits; maritime relocation (if
  pursued) updates `root_package` once; downstream contracts reference
  module paths and each needs a one-liner.
- **[Risk]** `lint-imports` adds latency to every verification run.
  **Mitigation:** `grimp`'s analysis is sub-second on a package of this
  size (< 100 modules expected at M3). If it becomes a perceptible tax,
  scope to pre-merge checks rather than every test run.
- **[Trade-off]** Empty contracts means the tool install does nothing
  visible until downstream changes add rules. **Accepted:** the value is
  the infrastructure; contracts land with their motivating changes.

## Migration Plan

1. Add `import-linter` to `pyproject.toml` dev deps. Run `uv sync`.
2. Add `[tool.importlinter]` section with
   `root_package = "rtl.vectors.maritime"` and `contracts = []`.
3. Run `uv run lint-imports` locally to confirm it resolves and exits
   zero on the empty config.
4. Land the change. Downstream changes add their contracts.

Rollback is removing the dev dep and the config section — no migration
required.

## Open Questions

None.

## Key Type Contracts

N/A. This change introduces a dev tool and its configuration. No runtime
types change.
