## Context

The project has 11 experiment scripts (experiments/01–11), an RTL Verilog implementation with Python harness (rtl/vectors/), and detailed design docs for a maritime scenario harness expansion. All testing has been manual verification within experiment scripts — no pytest, no shared test utilities, no formal dependency manifest.

The maritime expansion (11 planned OpenSpec changes) introduces a `rtl/vectors/maritime/` package with ~10 new modules. Each module needs unit tests. This change establishes the infrastructure that all subsequent changes inherit.

Current state:
- Dependencies managed ad-hoc via `uv run --with <deps>`
- No `pyproject.toml` — no project metadata, no dependency declarations, no pytest config
- No `tests/` directory — no test files exist anywhere
- `pyrightconfig.json` only includes `experiments/` — new maritime package and tests are out of scope
- OpenSpec config (`openspec/config.yaml`) already defines testing rules and conventions, but nothing enforces them mechanically

## Goals / Non-Goals

**Goals:**
- `uv run pytest` discovers and runs tests from `tests/`
- Dependencies declared in `pyproject.toml` so `uv run` resolves them automatically
- Shared fixtures in `conftest.py` for RNG seed control and numpy array comparison with physical-unit conventions
- `pyrightconfig.json` covers all active code directories
- A placeholder test that validates the test runner itself works

**Non-Goals:**
- CI pipeline configuration (deferred — local dev first)
- Test coverage enforcement or minimum coverage thresholds
- Refactoring existing experiments 01–11 into testable modules (frozen baseline)
- Creating a shared library package from existing experiment code
- RTL test infrastructure (Verilog testbenches are out of scope — this is Python-only)

## Decisions

### D1: Use `pyproject.toml` with `[project]` metadata (no src layout)

**Choice:** Flat `pyproject.toml` at repo root with `[project]` table. No `src/` layout, no installable package.

**Why:** The project runs scripts via `uv run python <path>` — it's not a library. A `src/` layout adds import complexity with no benefit. The pyproject.toml exists to declare dependencies and configure pytest, not to make the repo pip-installable.

**Alternatives considered:**
- `setup.py` / `setup.cfg`: deprecated pattern, pyproject.toml is current standard
- `src/` layout with `[tool.setuptools]`: overkill for a script-driven research project
- No manifest at all: already the status quo, and it means every `uv run` invocation needs `--with` flags

### D2: pytest configuration in `pyproject.toml` `[tool.pytest.ini_options]`

**Choice:** Testpaths = `["tests"]`. No asyncio, no custom markers initially.

**Why:** Keeps all config in one file. The `tests/` directory is separate from production code (experiments/, rtl/vectors/) which makes discovery clean.

### D3: `conftest.py` with deterministic RNG and tolerance fixtures

**Choice:** `tests/conftest.py` provides:
- `rng_seed` fixture returning a fixed integer (default 42, overridable via `--seed` CLI option)
- `make_rng` factory fixture returning a seeded `numpy.random.Generator`
- `assert_close` helper wrapping `numpy.testing.assert_allclose` with physical-unit documentation

**Why:** The openspec config requires "seed all RNGs explicitly in tests." Making this a fixture means every test gets deterministic behavior by default without boilerplate. The tolerance helper documents the convention (absolute physical units, not relative).

**Alternatives considered:**
- Per-test `np.random.seed()`: global state, not fixture-friendly
- No shared fixtures: every test file reinvents RNG seeding

### D4: `pyrightconfig.json` expansion to include maritime package and tests

**Choice:** Add `"rtl/vectors/maritime"` and `"tests"` to the `include` array.

**Why:** pyright only checks what's in `include`. Without this, new modules get no type checking. The maritime package will have the most complex type contracts in the project (state layouts, sensor models, current field protocols) — type checking is high-value there.

### D5: Tests directory mirrors maritime package structure

**Choice:** `tests/maritime/` for maritime-specific tests. Top-level `tests/` for cross-cutting infrastructure tests.

**Why:** As the maritime package grows (fleet.py, dynamics.py, sensors.py, etc.), the test directory should mirror it for discoverability. Each subsequent change adds its test files in the corresponding subdirectory.

## Risks / Trade-offs

- **[Risk] pyproject.toml becomes stale** → Mitigate: declare only the deps that actually appear in imports. Don't preemptively add torch/jax. Optional deps stay as `--with` flags.
- **[Risk] conftest fixtures become a dumping ground** → Mitigate: only fixtures that are used by 2+ test files belong in conftest. Module-specific helpers stay in the test file.
- **[Trade-off] No CI means no automated enforcement** → Acceptable for now. The pytest + pyright combination catches issues locally. CI can be added later without changing the test structure.

## Key Type Contracts

This change introduces no production types — it establishes testing infrastructure. The key contracts are:

- **`make_rng` fixture** → `Callable[[int], numpy.random.Generator]` — returns a seeded RNG
- **`assert_close` helper** → `(actual: ndarray, desired: ndarray, atol: float, rtol: float, msg: str) -> None` — wraps `numpy.testing.assert_allclose` with physical-unit documentation convention

These become the standard way all subsequent changes write numerical assertions.
