## 1. ClockSpec — Tests

- [x] 1.1 `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` constructs; `spec.kind == "clock"`; `isinstance(spec, ComponentSpec)` is `True`; spec is immutable (mutation raises `FrozenInstanceError`)
      (tests/maritime/test_clock.py)

- [x] 1.2 `ClockSpec(drift_ppm=20.0, avg_power_mw=0.5)` constructs and exposes provided values
      (tests/maritime/test_clock.py)

- [x] 1.3 `ClockSpec(drift_ppm=-0.1, avg_power_mw=0.0)` raises `ValueError` naming the field
      (tests/maritime/test_clock.py)

- [x] 1.4 `ClockSpec(drift_ppm=0.0, avg_power_mw=-1.0)` raises `ValueError` naming the field
      (tests/maritime/test_clock.py)

## 2. ClockSpec — Implementation

- [x] 2.1 Define `ClockSpec` frozen dataclass in `rtl/vectors/maritime/clock.py` with `kind: ClassVar[str] = "clock"`, `drift_ppm: float`, `avg_power_mw: float`; `__post_init__` enforces non-negative drift and power
      (rtl/vectors/maritime/clock.py)

## 3. Clock Runtime Advance — Tests

- [x] 3.1 Zero-drift `Clock.advance(60.0)` leaves `_accumulated_offset_sec == 0.0`
      (tests/maritime/test_clock.py)

- [x] 3.2 `Clock` with `drift_ppm=10.0`: `advance(100.0)` yields `_accumulated_offset_sec == 0.001`
      (tests/maritime/test_clock.py)

- [x] 3.3 `Clock` with `drift_ppm=10.0`: `advance(30.0)` three times yields `_accumulated_offset_sec == 0.0009`
      (tests/maritime/test_clock.py)

- [x] 3.4 `Clock.advance(-1.0)` raises `ValueError`
      (tests/maritime/test_clock.py)

## 4. Wall Clock Readout — Tests

- [x] 4.1 Zero-drift clock, no advances: `wall_time(100.0)` returns `100.0` exactly (bitwise equality)
      (tests/maritime/test_clock.py)

- [x] 4.2 Zero-drift clock advanced 100 times by `dt=60.0`: `wall_time(7200.0)` returns `7200.0` exactly
      (tests/maritime/test_clock.py)

- [x] 4.3 `Clock(spec=ClockSpec(drift_ppm=10.0, ...))` advanced once by `dt=1000.0`: `wall_time(1000.0)` returns `1000.01`
      (tests/maritime/test_clock.py)

- [x] 4.4 `wall_time` is pure — calling it between advances does not change `_accumulated_offset_sec`; repeated calls between the same pair of advances return identical values
      (tests/maritime/test_clock.py)

## 5. Clock Runtime — Implementation

- [x] 5.1 Define `Clock` dataclass in `rtl/vectors/maritime/clock.py` with `spec: ClockSpec`, `_accumulated_offset_sec: float = 0.0`, `advance(dt_sec)` and `wall_time(true_sec)` methods
      (rtl/vectors/maritime/clock.py)

## 6. Blueprint-Factory Clock Attachment — Tests

- [x] 6.1 `make_anchor(ANCHOR_PROFILE, initial_state, rng)` returns a node with `"clock" in node.components`; `node.components["clock"]` is a `Clock` instance whose `spec is profile.component("clock")`; `_accumulated_offset_sec == 0.0`
      (tests/maritime/test_fleet.py)

- [x] 6.2 `make_ballast_drifter(BALLAST_DRIFTER_PROFILE, ...)` and `make_pure_drifter(PURE_DRIFTER_PROFILE, ...)` both attach a `Clock` component sourced from the profile's `ClockSpec`
      (tests/maritime/test_fleet.py)

- [x] 6.3 Blueprint factory called with a profile whose components tuple lacks a `ClockSpec` raises `ValueError` naming the missing `"clock"` kind
      (tests/maritime/test_fleet.py)

- [x] 6.4 There is no module-level `make_clock` function in `rtl/vectors/maritime/clock.py` (`hasattr(clock_module, "make_clock") is False`)
      (tests/maritime/test_clock.py)

## 7. Blueprint-Factory Clock Attachment — Implementation

- [x] 7.1 Update `make_anchor`, `make_ballast_drifter`, `make_pure_drifter` in `rtl/vectors/maritime/fleet.py` to instantiate `Clock(spec=profile.component("clock"))` and include it in the runtime components mapping under key `"clock"`; raise `ValueError` if `profile.component("clock")` is missing
      (rtl/vectors/maritime/fleet.py)

## 8. Bundled Profile Clock Inclusion — Tests

- [x] 8.1 `ANCHOR_PROFILE.component("clock")` returns a `ClockSpec` with `drift_ppm == 0.0` and `avg_power_mw == 0.0`
      (tests/maritime/test_platform_profile.py)

- [x] 8.2 `BALLAST_DRIFTER_PROFILE.component("clock")` returns a zero-drift / zero-power `ClockSpec`
      (tests/maritime/test_platform_profile.py)

- [x] 8.3 `PURE_DRIFTER_PROFILE.component("clock")` returns a zero-drift / zero-power `ClockSpec`
      (tests/maritime/test_platform_profile.py)

- [x] 8.4 Each bundled profile still satisfies its power-budget invariant with the clock component's `avg_power_mw=0.0` contribution included
      (tests/maritime/test_platform_profile.py)

## 9. Bundled Profile Clock Inclusion — Implementation

- [x] 9.1 Add `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)` to `ANCHOR_PROFILE.components`, `BALLAST_DRIFTER_PROFILE.components`, `PURE_DRIFTER_PROFILE.components` in `rtl/vectors/maritime/platform_profile.py`
      (rtl/vectors/maritime/platform_profile.py)

## 10. Propagate-Truth Clock Phase — Tests

- [x] 10.1 On a node whose clock has non-zero `drift_ppm` (test uses a directly-constructed profile with `ClockSpec(drift_ppm=10.0, ...)`, not a bundled profile), `propagate_truth(node, dt_sec=1.0, env, rng)` increases `node.components["clock"]._accumulated_offset_sec` by exactly `1.0 * 10.0 * 1e-6 = 1e-5`
      (tests/maritime/test_dynamics.py)

- [x] 10.2 On a bundled-profile node (zero drift), `propagate_truth` leaves `_accumulated_offset_sec == 0.0` regardless of `dt_sec` or number of ticks
      (tests/maritime/test_dynamics.py)

- [x] 10.3 `propagate_truth` does not modify any state-vector dimension as a consequence of the clock phase — isolated by comparing a run that includes a clock component to a run on an otherwise identical node whose clock component is absent (clock-free node is a test fixture that bypasses the blueprint factory's clock-required check)
      (tests/maritime/test_dynamics.py)

## 11. Verification

- [x] 11.1 `uv run pytest tests/maritime/test_clock.py` passes with zero failures
- [x] 11.2 `uv run pytest tests/maritime/test_fleet.py tests/maritime/test_platform_profile.py tests/maritime/test_dynamics.py` passes (covers the clock-attachment, bundled-profile, and phase-4 integration tests added here)
- [x] 11.3 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and pre-existing `rtl/vectors/*.py` files outside the declared surface of this change and its dependencies
- [x] 11.4 `openspec validate maritime-clock-model --strict` passes
- [x] 11.5 Module import sanity: `uv run python -c "from rtl.vectors.maritime.clock import ClockSpec, Clock; from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE, BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE; assert all(p.component('clock').drift_ppm == 0.0 for p in (ANCHOR_PROFILE, BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE))"` exits 0
