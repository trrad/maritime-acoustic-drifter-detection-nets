## 1. Spec Lock-In (Already-Landed CLI Surface)

- [x] 1.1 Confirm `gen_maritime_scenario.py --help` lists the 9 new flags
      (`--mean-flow-east-ms`, `--mean-flow-north-ms`,
      `--tidal-amplitude-ms`, `--tidal-period-sec`,
      `--tidal-direction-deg`, `--eddy`, `--enable-sensors`,
      `--lora-period-sec`, `--gps-period-sec`) and that
      `tests/maritime/test_pipeline_field.py` substance tests pass.
      (rtl/vectors/maritime/gen_maritime_scenario.py, tests/maritime/test_pipeline_field.py)
- [x] 1.2 Confirm `run_pf_float.py --help` lists the 4 predict-noise
      flags and that the `_build_pf_config` function is wired into
      `main()` so every `PFFloat` is constructed from the
      override-aware config.
      (rtl/vectors/maritime/run_pf_float.py)
- [x] 1.3 Confirm `make_m1_fleet` accepts the `lora_period_sec` and
      `gps_period_sec` keyword-only arguments and the
      `_apply_cadence_overrides` helper handles both profile types.
      (rtl/vectors/maritime/fleet.py)

## 2. Builder Helpers — Tests

- [x] 2.1 Write failing unit test: `make_m1_fleet(seed=42, bbox=<valid>, lora_period_sec=60.0, gps_period_sec=60.0)` produces a fleet where every node's `profile.comms.tdma_period_sec == 60.0`, every `lora_toa` sensor has `max_rate_hz == 1/60`, and every anchor's `gps` sensor has `max_rate_hz == 1/60`. Verify bundled singleton profiles remain unmutated.
      (tests/maritime/test_fleet.py::test_make_m1_fleet_cadence_override_applies_to_all_nodes)
- [x] 2.2 Write failing unit test: `_build_pf_config(args)` with an `argparse.Namespace` carrying non-None override values for the four predict-noise flags returns a `PFFloatConfig` whose four noise fields match the overrides; with all four `None` returns the bundled defaults.
      (tests/maritime/test_run_pf_float.py::test_build_pf_config_applies_noise_overrides)

## 3. Builder Helpers — Implementation

- [x] 3.1 No production changes required — `make_m1_fleet` cadence kwargs and `_build_pf_config` noise overrides already exist in the tree. Tasks 2.1 / 2.2 tests must pass against current code. (If any test fails, pause and surface the divergence — do not modify code under this change.)

## 4. Verification

- [x] 4.1 `uv run pytest tests/maritime/ --no-header -q` — full suite green (499 + 2 new unit tests = 501 passing).
- [x] 4.2 `uv run lint-imports` — clean.
- [x] 4.3 `openspec validate maritime-cli-config-overrides --strict` — change artifacts validate.
- [x] 4.4 End-to-end smoke: invoke `gen_maritime_scenario.py` with `--mean-flow-east-ms 0.15 --tidal-amplitude-ms 0.0 --lora-period-sec 60` and `run_pf_float.py` with `--predict-noise-vel 0.0`, confirm the flags flow through to the observable behavior (surface current in truth, no velocity evolution in PF when obs are suppressed).
