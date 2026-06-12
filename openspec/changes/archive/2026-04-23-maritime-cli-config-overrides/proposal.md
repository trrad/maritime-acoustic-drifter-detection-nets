## Why

The catchup commit `20f7eda` landed new CLI flags across three modules
(scenario gen, PF runner, fleet factory) to enable substance-debugging
experiments — field-config overrides, cadence overrides, sensor
allow-list, PF predict-noise overrides. Code and pipeline tests landed;
the standing specs still describe the pre-override CLI surface. This
change formalizes the new flags in the relevant standing specs so a
spec-only reader (or a future M2 contributor) knows they exist and
what contract they honor. Also adds two narrow unit tests for the
non-pipeline builders (`make_m1_fleet` cadence kwargs and
`_build_pf_config` noise overrides) — the existing pipeline tests in
`test_pipeline_field.py` already cover gen-CLI-flag substance, but
the builder helpers deserve direct coverage.

## What Changes

- MODIFY `maritime-scenario-gen` Requirement "CLI Invocation" — ADD
  scenarios documenting 6 field-config flags (`--mean-flow-east-ms`,
  `--mean-flow-north-ms`, `--tidal-amplitude-ms`, `--tidal-period-sec`,
  `--tidal-direction-deg`, `--eddy`), 2 cadence flags
  (`--lora-period-sec`, `--gps-period-sec`), and 1 sensor allow-list
  flag (`--enable-sensors`). Each flag gets a scenario that pins its
  substance contract (what an OPERATING pipeline does differently when
  the flag is set).
- MODIFY `maritime-pf-float` Requirement "CLI Invocation" — ADD
  scenarios for 4 predict-noise flags (`--predict-noise-pos/vel/
  heading/current`). Substance: `--predict-noise-vel 0.0` produces a
  PF whose particle velocity dimension does not evolve under predict.
- MODIFY `maritime-fleet-dynamics` Requirement "M1 Fleet Factory" —
  ADD scenario: `make_m1_fleet` honors `lora_period_sec` /
  `gps_period_sec` kwargs by cloning bundled profiles with the
  overridden cadences.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `maritime-scenario-gen`: CLI surface expanded.
- `maritime-pf-float`: CLI surface expanded.
- `maritime-fleet-dynamics`: fleet-factory signature expanded.

## Impact

- No code changes to production files — the CLI flags and builder
  kwargs already exist in the tree.
- New unit tests in `tests/maritime/test_fleet.py` (cadence-override
  kwarg) and `tests/maritime/test_run_pf_float.py` (predict-noise
  override via `_build_pf_config`).
- Standing specs pick up the delta during `/opsx:sync`.
