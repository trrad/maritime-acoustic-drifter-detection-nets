---
name: run-tests
description: Run tests for a project area. Detects area from CWD or accepts explicit area/tier arguments.
user_invocable: true
---

Run pytest with a scoped area and tier. Inline `uv run pytest` — no shell
script delegation.

**Usage:**

```
/run-tests                → detect area from CWD, standard tier
/run-tests fast           → detect area from CWD, fast tier
/run-tests full           → detect area from CWD, full tier
/run-tests maritime       → maritime area, standard tier
/run-tests maritime fast  → maritime area, fast tier
/run-tests all            → all tests, standard tier
```

**Steps:**

1. **Parse arguments**

   Parse the input into `area` (optional) and `tier` (optional, default: `standard`).
   - Valid areas: `maritime`, `infra`, `all`
   - Valid tiers: `fast`, `standard`, `full`
   - If only one word and it matches a tier name, treat it as tier (area from CWD)
   - If only one word and it matches an area name, treat it as area (standard tier)

2. **Detect area from CWD (if not specified)**

   - CWD is under `tests/maritime/` or `rtl/vectors/maritime/` → `maritime`
   - CWD is under `tests/` and not `tests/maritime/` → `infra`
   - At repo root or anywhere else → `all`

3. **Map area to pytest path**

   | Area | Path |
   |------|------|
   | `maritime` | `tests/maritime/` |
   | `infra` | `tests/test_infra.py` |
   | `all` | `tests/` |

4. **Map tier to pytest flags**

   | Tier | Flags |
   |------|-------|
   | `fast` | `-x --ff` (stop on first failure; run previously-failed first) |
   | `standard` | *(no extra flags)* — default |
   | `full` | `-v` (verbose; include any slow-marked tests when they exist — no `-m` filter) |

5. **Run pytest**

   Use the Bash tool. Dependencies resolve via `uv`; never call bare `pytest`:

   ```bash
   uv run pytest <tier-flags> <path> [-- <extra-args>]
   ```

   Extra arguments after `--` are passed through to pytest.

6. **Report structured results**

   Parse pytest output for the summary line (`===== N passed, M failed in T.TTs =====`)
   and any failing test names. Report:

   ```
   ## Test Results: <area> (<tier>)

   **Summary:** <passed>/<total> passed in <runtime>s
   **Path:** <path>
   **Command:** uv run pytest <flags> <path>

   <if any failures>
   **Failures:**
   - <test name 1> — <first line of failure>
   - <test name 2> — ...
   ```

   If `pytest` exits non-zero but produced no failing-test names (e.g., collection
   error, import error), include the relevant stderr excerpt rather than leaving
   the failure unexplained.

**Import-linter parity:**

For changes that register import-linter contracts (truth separation, etc.),
also run `uv run lint-imports` in the same Bash call (chained with `&&`) or
as a second call. Both `pytest` and `lint-imports` must exit zero for the
test phase to be considered complete — this matches the `project-infra`
Import Boundary Enforcement requirement.

**Guardrails:**

- Never modify test files — this skill only runs tests.
- Don't invent markers or `-m` filters that the project hasn't configured
  (`conftest.py` and `pyproject.toml` are the authoritative source for
  markers). `fast`/`standard`/`full` currently differ only by pytest flags,
  not by marker filtering. Add `-m "not slow"` style filters only when
  slow markers are declared in `pyproject.toml`.
- Report failures loudly. If pytest exits non-zero, say so explicitly with
  the failing test names — never silently report "ran successfully."
