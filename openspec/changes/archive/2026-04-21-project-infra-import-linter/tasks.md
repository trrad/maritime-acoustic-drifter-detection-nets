## 1. Setup

- [x] 1.1 Add `import-linter` to `pyproject.toml` dev dependencies and run `uv sync` so the tool is installed (`pyproject.toml`)
- [x] 1.2 Add `[tool.importlinter]` section to `pyproject.toml` with `root_package = "rtl.vectors.maritime"` and an empty `contracts = []` list (`pyproject.toml`)

## 2. Verification

- [x] 2.1 `uv run lint-imports` resolves and exits zero on the empty contracts configuration — tool reachable via `uv run` without extra flags
- [x] 2.2 `uv run pytest` still passes — the dev-dep addition does not regress any existing test
- [x] 2.3 `openspec validate project-infra-import-linter --strict` passes — delta spec against `project-infra` standing spec is well-formed
