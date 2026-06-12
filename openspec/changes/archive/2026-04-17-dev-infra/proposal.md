## Why

The project is transitioning from exploratory experiments to structured development with the maritime scenario harness. Currently there is no formal dependency manifest, no test runner, no shared package structure, and no test conventions. Every subsequent OpenSpec change (maritime-geo, current-fields, fleet-dynamics, etc.) depends on having `uv run pytest` working and a `tests/` directory with shared fixtures. Setting this up first ensures all maritime work is born tested.

## What Changes

- Add `pyproject.toml` with project metadata, dependency declarations (numpy, scipy, matplotlib), and pytest configuration (testpaths, markers, asyncio off)
- Create `tests/` directory with `conftest.py` providing shared fixtures: deterministic RNG seeds, numpy array comparison helpers with physical-unit tolerance conventions
- Update `pyrightconfig.json` to include `rtl/vectors/maritime/` and `tests/` in type checking scope
- Establish standing spec for project infrastructure conventions (test naming, fixture patterns, frozen baseline protection)

## Capabilities

### New Capabilities
- `project-infra`: Test infrastructure, dependency management, and development conventions. Covers pytest configuration, conftest fixtures, pyrightconfig scope, and the frozen-baseline rule for existing experiment files.

### Modified Capabilities
(none — no existing specs to modify)

## Impact

- **New files**: `pyproject.toml`, `tests/conftest.py`, `tests/__init__.py`
- **Modified files**: `pyrightconfig.json` (add `rtl/vectors/maritime/` and `tests/` to include paths)
- **Dependencies**: numpy, scipy, matplotlib declared formally in `pyproject.toml`
- **No existing code changes**: All experiments 01–11 remain frozen. No RTL changes.
