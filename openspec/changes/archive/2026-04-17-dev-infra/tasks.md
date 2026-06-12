## 1. Setup — Dependency Manifest and Pytest Configuration

- [x] 1.1 Project dependencies (numpy, scipy, matplotlib) resolve via `uv run python -c "import numpy, scipy, matplotlib"` without `--with` flags
      (pyproject.toml)

- [x] 1.2 `uv run pytest` discovers and collects tests from `tests/`, reports pass/fail counts
      (pyproject.toml — `[tool.pytest.ini_options]` with `testpaths = ["tests"]`)

## 2. Test Fixtures — Tests

- [x] 2.1 Tests verify that `make_rng` fixture returns a seeded `numpy.random.Generator` and produces identical values across repeated invocations with the same seed
      (tests/conftest.py)

- [x] 2.2 Tests verify that `assert_close` passes for in-tolerance arrays and fails with physical-unit context for out-of-tolerance arrays
      (tests/conftest.py)

## 3. Test Fixtures — Implementation

- [x] 3.1 `make_rng` factory fixture returns a `numpy.random.Generator` seeded with 42 by default, overridable via `--seed` CLI option
      (tests/conftest.py)

- [x] 3.2 `assert_close` helper wraps `numpy.testing.assert_allclose` with `atol`, `rtol`, and `msg` parameters for physical-unit documentation
      (tests/conftest.py)

## 4. Type Checking Scope

- [x] 4.1 `pyrightconfig.json` includes `rtl/vectors/maritime` and `tests` in type checking scope alongside existing `experiments`
      (pyrightconfig.json)

## 5. Smoke Test

- [x] 5.1 A placeholder test in `tests/test_infra.py` passes with `uv run pytest`, confirming the full test runner pipeline works end-to-end
      (tests/test_infra.py)

## 6. Verification

- [x] 6.1 `uv run pytest` passes with zero failures
- [x] 6.2 `git diff` shows zero modifications to files `experiments/01*.py` through `experiments/11*.py` and existing files in `rtl/vectors/` (frozen baseline intact)
- [x] 6.3 pyright analyzes `tests/` and `rtl/vectors/maritime/` directories without configuration errors
