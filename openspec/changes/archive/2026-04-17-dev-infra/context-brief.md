# Context Brief: dev-infra

## Purpose
Establish project testing infrastructure (pytest, pyproject.toml, shared fixtures, type checking scope) as the foundation for all subsequent maritime scenario harness changes.

## Key Decisions
- Flat pyproject.toml (no src layout) — project runs scripts via `uv run python`, not pip-installable
- pytest configured in pyproject.toml `[tool.pytest.ini_options]` with testpaths = ["tests"]
- conftest.py provides `make_rng` factory fixture (seed 42 default, `--seed` override) and `assert_close` helper wrapping numpy.testing.assert_allclose
- pyrightconfig.json expanded to include `rtl/vectors/maritime/` and `tests/`
- Frozen baseline: experiments 01–11 and existing rtl/vectors/ files are not modified

## Tasks
1.1 Dependencies resolve via pyproject.toml ✅
1.2 pytest discovers tests from tests/ ✅
2.1 Tests for make_rng fixture determinism ✅
2.2 Tests for assert_close tolerance behavior ✅
3.1 Implement make_rng factory fixture ✅
3.2 Implement assert_close helper ✅
4.1 Update pyrightconfig.json scope ✅
5.1 Placeholder smoke test passes ✅
6.1 pytest passes with zero failures ✅
6.2 Frozen baseline intact (git diff clean) ✅
6.3 pyright analyzes new directories ✅

## Implementation Notes
- Added pytest to pyproject.toml dependencies (needed for pyright to resolve imports)
- make_rng is a factory fixture returning a callable; rng_seed provides CLI --seed value
- assert_close is a fixture wrapping numpy.testing.assert_allclose with msg parameter
- rtl/vectors/maritime/ directory doesn't exist yet — pyright reports clean for tests/

## Files Affected
- pyproject.toml (new)
- tests/conftest.py (new)
- tests/__init__.py (new)
- tests/test_infra.py (new)
- pyrightconfig.json (modified — add directories)

## Spec Pointers
project-infra → Requirement: Dependency Manifest, Requirement: Test Runner Configuration, Requirement: Deterministic RNG Fixtures, Requirement: Numerical Tolerance Assertions, Requirement: Type Checking Scope, Requirement: Frozen Baseline Protection
openspec/changes/dev-infra/specs/project-infra/spec.md
