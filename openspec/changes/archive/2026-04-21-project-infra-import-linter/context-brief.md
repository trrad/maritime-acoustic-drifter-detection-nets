# Context Brief: project-infra-import-linter

## Purpose

Installs `import-linter` as project-level infrastructure so downstream
maritime changes can declare AST-based import contracts (truth-separation,
climatology-vs-truth) without each one rolling its own enforcement tooling.

## Key Decisions

- Use `import-linter` (standard Python tool, uses `grimp` for AST
  import-graph analysis) over a custom AST walker or `grimp` direct — the
  declarative TOML config is the value.
- Config lives in `[tool.importlinter]` in `pyproject.toml`, not a separate
  `.importlinter` file — single config surface.
- Ship with `root_package = "rtl.vectors.maritime"` and an empty contracts
  list. Contracts land with the downstream changes that need them.
- `uv run lint-imports` has equal status to `uv run pytest` in the
  verification protocol. Both must exit zero.
- No CI system assumption — the requirement is behavioral; CI, when added,
  gates both.

## Tasks

1. Setup ✓
   - Add `import-linter` to `pyproject.toml` dev deps, `uv sync` ✓
   - Add `[tool.importlinter]` section with `root_package` and empty contracts ✓
2. Verification ✓
   - `uv run lint-imports` exits zero on empty config ✓
   - `uv run pytest` still passes (68 tests) ✓
   - `openspec validate --strict` passes ✓

## Files Affected

- `pyproject.toml` (dev dep + tool config)
- No runtime code. No new modules.

## Spec Pointers

- `project-infra` → ADDED Requirement: Import Boundary Enforcement
  - openspec/changes/project-infra-import-linter/specs/project-infra/spec.md
