# Testing Philosophy

Reference for agents writing contract tests and implementers writing
production code. Applies the project's collaboration principles
(`AGENTS.md`) and simulation-integrity enforcement
(`docs/simulation_integrity.md`) to the test layer.

## Tests Define "Done"

A contract test specifies what observable behavior the implementation
must produce. The test is written and approved BEFORE production code.
The implementer cannot modify the test; they make the code pass the
test as written. The test is the "done" definition.

If you find yourself wanting to weaken a test to make it pass, that
means the spec is wrong, not the test. Surface the question via the
`---QUESTION---` sentinel (see `/opsx:apply`). Never edit the contract.

## What to Assert

- **Observable behavior, not implementation choices.**
  `assert np.all(np.isfinite(pf.estimate().mean))` is observable.
  `assert pf._internal_weights_buffer is not None` is implementation.
- **Physical units, stated absolutely.** Numerical tolerances are in
  meters, m/s, seconds — not normalized ratios. Use `assert_close`
  (declared in `project-infra` standing spec, implemented in
  `tests/conftest.py`) with explicit `atol` in physical units. See
  `assert_close(actual, desired, atol=0.5, msg="position RMSE")` —
  "within half a meter." Avoid bare relative tolerances.
- **One behavior per test.** A test that asserts both "the PF
  converges" AND "particle count is preserved" is two tests. Split.
- **Spec scenarios map 1:1 to tests.** Every `#### Scenario:` block in
  a delta spec becomes a test case. If a scenario has no test, the
  scenario is not enforced.

## What Not to Assert

- **Mock call counts or internal interactions.** "The PF called
  `_compute_weights` exactly once per tick" tests implementation. A
  refactor that fuses two loops breaks the test without introducing a
  bug.
- **Exact intermediate stochastic values.** Particle positions after
  one predict step depend on the RNG stream ordering; the test should
  assert "the particles moved with the current" or "the mean lies
  within one sigma of truth," not "particle 0 is at
  `(1.2345, 6.7890)`."
- **Arbitrary thresholds.** "RMSE < 100 m after 60 s" is assertion
  posing as measurement. Either (a) measure and report (the PF summary
  JSON pattern — report the number, don't compare to an invented
  bound) or (b) assert a sanity bound that only fails when something
  is actually broken: `all positions finite`, `ESS > 0`, `weights sum
  to 1 within 1e-6`. See AGENTS.md "No unprincipled numeric
  thresholds in specs."

## Shape vs. Substance

Observable-behavior assertions come in two flavors; prefer substance
when the spec commits to content.

- **Shape assertions** verify that the output exists with the right
  structure. `assert np.all(np.isfinite(state))`, `assert len(measurement.value) == 6`,
  `assert "clock" in node.components`. Shape catches crashes, NaN
  leaks, and gross type errors.
- **Substance assertions** verify that the content makes sense for a
  realistic input. `assert_close(state[layout.slice("surface_current")], field.velocity_at(node_lat, node_lon, t), atol=0.01)`,
  `assert measurement.value[0] == pytest.approx(expected_range, abs=3 * comms.ranging_sigma_m)`,
  `assert gps_measurement.value[0] == pytest.approx(enu_origin_lat + dy, abs=3*sigma_lat)`.

Substance catches the failure mode where a declared slot is structurally
valid but functionally empty or wrong. Examples from this project's own
history:

- `surface_current` slot in truth state was declared with the right
  shape and the right unit, but nothing in `propagate_truth` wrote
  the sampled field value into it — so the slot held zeros on every
  tick. A shape test passed (slot exists, values are finite); a
  substance test would have asserted "after one tick in a non-zero
  current, the slot equals the field's `(vx, vy)` at the node's
  lat/lon."
- IMU measurement had `unit="m/s^2"` and a 6-tuple value — shape
  correct. The value was `velocity + bias + noise` — substance
  physically wrong. A substance test would have asserted "with a
  known velocity delta across one tick, the accel channel equals
  `(v - v_prev) / dt + bias` within noise."

When reviewing a spec or test, ask: "if the implementer populates this
field with a trivial placeholder (zeros, constants, the identity
function), does any test fail?" If the answer is no, the test is
shape-only and the spec is likely shape-only too. Add a substance
scenario.

## Mocking Discipline

- **Mock external boundaries only** — network, real system clock (if
  the test cares), cryptographic sources. For this project there are
  almost no such boundaries in unit tests.
- **Don't mock internal code.** If your test mocks a helper function
  inside the module you're testing, you're testing the mock. Use the
  real implementation with known inputs.
- **RNG is injected, not global.** Tests obtain RNGs via the
  `make_rng` fixture (`tests/conftest.py`, contract in `project-infra`
  standing spec). Direct `numpy.random.seed` or unseeded
  `numpy.random.default_rng()` is disallowed — breaks determinism.

## Errors Must Be Explicit

If construction rejects a value, a test must exist that constructs
with that value and asserts the rejection. Every `raise` in a
`__post_init__` or validator is a promise; tests verify the promise.

`/opsx:verify` enforces this via the "Untested rejection branches"
check — every `raise` in validation code must have a test that
triggers it. If the rejection is worth writing, it's worth testing.

This is the test-layer corollary of AGENTS.md's "All errors must be
explicit" — code raises, and tests prove it raises on the right
inputs.

## Failing for the Right Reason

Before implementation, run the newly-written contract tests. They
MUST fail. The failure messages must indicate the behavior is missing
or wrong — not that the test itself is malformed (syntax error, wrong
import, typo in a class name). A test that passes with no
implementation is not testing what you think it is.

Rule of thumb: delete the implementation function body, replace with
`raise NotImplementedError`, run the test. It should fail with a
clear message pointing at the missing behavior.

## Determinism and Reproducibility

- **Seed every RNG.** Via `make_rng` fixture — default seed 42, per
  `project-infra`.
- **No wall-clock dependencies.** If pass/fail depends on
  `datetime.now()` or similar, inject a fake clock at the boundary.
- **Byte-identical where specified.** The scenario generator's
  reproducibility contract is `(seed, bbox, duration_hours, dt_sec,
  nodes) → byte-identical JSONL`. Tests for that contract use file
  hashes, not approximate comparison.

## Tolerance Hierarchy

When a test asserts closeness, pick the tightest tolerance that
matches the underlying guarantee:

1. **Exact equality** — deterministic discrete values (tick counts,
   state dimensions, node IDs, finite-ness). Use `==`.
2. **Tight tolerance (sub-meter / sub-ms)** — numerical routines with
   known-good reference (round-trip conversions, calibrated
   transforms, zero-noise advection integrations). `atol=0.1` or
   better.
3. **Statistical bounds** — stochastic behavior (random walks,
   particle spread). Assert "within 50%–200% of expected std over N
   samples" with explicit N and explicit expected value derivation.
4. **No tolerance — measurement only** — for numbers the spec does
   NOT commit to (convergence RMSE, ESS trajectories). Emit to a
   report, do not assert. See `pf_summary.json` in `maritime-pf-float`.

## Vectorization Requirement

For numpy-heavy code (particle filters, field evaluations), no
Python-level `for i in range(n_particles)` loops. The test scans the
implementation's source text and fails if such a pattern appears in
the predict / weight / resample / estimate functions. This enforces
the vectorized-numpy contract at spec level, not by cultural
convention. See `maritime-pf-float` spec, "Vectorized Over Particles"
requirement.

## Test Pyramid for This Project

Write each layer for the problem it actually catches. Don't skip layers
by hoping the next one up will catch the gap — each layer's failure
mode is distinct.

- **Unit tests** (per module, `tests/<area>/test_<module>.py`). Always.
  One behavior per test. Cover every `### Requirement:` scenario.
  Catches: internal logic errors, invariant violations, rejection-
  branch regressions. The bulk of the test count lives here.
- **Integration tests** (two modules, shared data contract). Assert
  that a producer's output satisfies a consumer's input invariant.
  Example: `test_propagate_truth_output_drives_imu_sensor` — construct
  a node, run `propagate_truth`, pass the resulting state into
  `IMUSensor.sample`, assert the measurement is finite and in the
  expected bias + noise band. Catches: contract mismatches between
  modules that unit tests on each side miss.
- **Pipeline tests** (full chain, skeleton-driven). Run the actual
  producer → consumer → output chain end-to-end for a short horizon.
  See next section. Catches: integration-level substance bugs — the
  class where every module's unit tests pass and the pipeline still
  produces nonsense. A small set (< 10) is sufficient; these are slow
  and don't scale linearly.
- **Golden-trace regressions** (byte-identical against committed
  fixtures). See "Golden Trace Regression" below. Catches: silent
  drift in the tick loop, numeric recipe, or serialization, none of
  which the higher-level tests detect.

The skeleton-before-spec-chain discipline (AGENTS.md "Integration
pipelines") is the forcing function for the pipeline-test layer. Don't
try to write pipeline tests before the skeleton exists; don't defer
them until after the pipeline is fully spec'd. Pipeline tests land
alongside the skeleton.

## Pipeline Tests

A pipeline test instantiates the real chain of modules — no mocks of
internal boundaries — runs it for a few ticks, and asserts properties
*across the chain's output*. The assertion targets substance (see
"Shape vs. Substance"): the content produced by the pipeline end-to-
end, not each module's isolated invariants.

Examples of pipeline tests the M1 substance bugs would have failed:

- "Run `gen_maritime_scenario --seed 42 --bbox <bbox> --duration-hours 0.1 --dt-sec 60 --nodes 10 --out trace.jsonl`; parse the header; assert `anchor_positions` values match the bbox corners passed to `make_m1_fleet`" — would have caught placeholder (0, 0) anchor coords.
- "Run a 10-tick scenario with `SyntheticEddyField(mean_vx=0.1, eddies=[...])`; parse each truth record; assert `surface_current` in the truth state varies across nodes at different positions" — would have caught current queried at (0, 0).
- "Run a 10-tick scenario; assert that for every sensor name declared in each node's `profile.sensors`, at least one observation record with that `sensor_name` appears in the trace" — would have caught incomplete bundled-profile sensor suites.

Guidelines:

- **Real modules, real output.** Pipeline tests use the production
  constructors and the real I/O path (to a temp dir or in-memory
  buffer). Mocking a module inside the pipeline defeats the test.
- **Short horizons.** A 10-tick, 3-node run exercises integration
  without bloating test time. Longer runs belong in offline
  measurement reports (`pf_summary.json`-style), not the unit-test
  suite.
- **Assert properties, not exact values.** The pipeline is stochastic
  (seeded); properties like "every declared sensor produced at least
  one record" or "nodes moved in the current's direction on average"
  are robust. Exact-value assertions on the full output are the
  golden-trace layer's job.
- **One pipeline test per integration hazard.** Not one per possible
  scenario. Budget 3–8 total, covering the substance risks that unit
  tests can't see.

## Golden Trace Regression

A golden trace is a small, committed, hand-verified output file (< 50
KB) that the pipeline produces deterministically for a fixed `(seed,
bbox, duration, dt_sec, nodes)` input. The regression test runs the
pipeline and asserts byte-identical output against the committed
trace. For `maritime-scenario-gen`, the planned fixture is
`tests/maritime/golden_trace/m1_tiny.jsonl` paired with a regenerator
CLI `tests/maritime/regenerate_golden_trace.py` (both land with the
scenario-gen implementation — see that change's tasks).

What it catches that pipeline tests don't: silent drift in the tick
loop, numeric-recipe changes that don't alter high-level properties
but do alter exact values (e.g., a refactor that reorders RNG draws,
changes a formula from `a * dt + b` to `b + a * dt`, or serializes
floats with different precision).

Re-bless discipline. When the golden trace diff appears:

1. Run the regenerator script.
2. Inspect the diff. Every change must be *explicitly explained* by the
   commit introducing it. "I changed the noise model; here's the one-line
   reason in the commit message."
3. If the diff is not obviously explained, stop — it's a regression
   hiding as a bless. Find the root cause before committing.
4. Re-blessing without understanding is the failure mode.

When to introduce a golden trace: after the pipeline is stable enough
that re-blessing would be rare (roughly, after all integration tests
pass consistently and the shape of the output won't change soon).
Premature golden traces churn — you re-bless every tier apply, which
defeats the purpose. The scenario-gen plan defers golden trace
creation to the end of its implementation deliberately for this
reason.

## Test File Organization

- One test file per module: `tests/<area>/test_<module>.py` mirrors
  `rtl/vectors/<area>/<module>.py`.
- Helper fixtures live in `tests/conftest.py` (project-wide) or
  `tests/<area>/conftest.py` (scoped).
- The test runner only discovers tests under `tests/`. `experiments/`
  and `rtl/` are excluded per `project-infra`.
- `uv run pytest` — no bare `pytest`. Dependencies resolve via uv.

## Anti-Patterns (Flag These in Review)

- `assert True` / `assert 1 == 1` — tautology, not a test.
- Tests that call the production function without asserting on its
  output or side effect.
- `# TODO: actually test this` next to a passing test.
- Mocks that make the test vacuously pass (mock returns the expected
  value; test verifies the mocked return → circular).
- Tests whose setup is > ~15 lines without a fixture — probable sign
  of untested complexity being pushed into setup.
- Tests that still pass when an obvious bug is introduced (e.g., the
  function under test replaced with `return None`) — the test does
  not test what its name claims.
- Floating-point `assert a == b` on computed values — use
  `assert_close` with a justified tolerance.

## Import Boundaries in Tests

Tests respect the import-linter contracts. For example, PF tests
import from `scenario_schema`, not `scenario_truth_schema` — the
same discipline as the production code they verify. The
`import-linter` CI gate (`uv run lint-imports`) catches boundary
violations in test code as well as production code.

Tests for truth-side behavior (validation tooling, dashboard tests)
are exempt and may import from `scenario_truth_schema` — the
contract's `source_modules` list does not include those test paths.

## References

- `AGENTS.md` — collaboration preferences (enforcement over
  instruction, errors explicit, no arbitrary thresholds, composition
  over inheritance, anticipate deployment scale, shape vs.
  substance in spec authoring, skeleton before multi-tier spec
  chain).
- `docs/simulation_integrity.md` — Enforcement Matrix; integrity
  concerns paired with their enforcement mechanism (test, invariant,
  lint rule).
- `openspec/specs/project-infra/spec.md` — `assert_close`, `make_rng`,
  pytest and import-linter configuration contracts.
- `dev/prompts/spec_audit_substance.md` — audit prompt for catching
  shape-only requirements that lack substance scenarios.
