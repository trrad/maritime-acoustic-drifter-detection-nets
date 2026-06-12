## Context

CLI flags landed in commit `20f7eda`:

- `gen_maritime_scenario.py`: `--mean-flow-east-ms`,
  `--mean-flow-north-ms`, `--tidal-amplitude-ms`,
  `--tidal-period-sec`, `--tidal-direction-deg`, `--eddy` (repeatable),
  `--enable-sensors` (comma-separated allow-list),
  `--lora-period-sec`, `--gps-period-sec`.
- `run_pf_float.py`: `--predict-noise-pos`, `--predict-noise-vel`,
  `--predict-noise-heading`, `--predict-noise-current`.
- `fleet.make_m1_fleet`: keyword-only `lora_period_sec`,
  `gps_period_sec`.

The pipeline substance tests in `tests/maritime/test_pipeline_field.py`
already exercise the generator flags end-to-end (e.g. "0.15 m/s
eastward flow appears in truth `surface_current` at tick 1"). What is
missing is (a) spec-level documentation and (b) unit coverage for the
two helper functions whose behavior the pipeline tests only see
indirectly.

## Goals / Non-Goals

**Goals:**
- Standing specs document every public CLI flag currently in the tree.
- Each delta scenario pins *substance*, not shape: a flag's scenario
  says what the flag changes about running behavior, not just "the
  flag parses."
- Two unit tests cover the builder helpers `make_m1_fleet` (cadence
  kwargs) and `_build_pf_config` (predict-noise overrides).

**Non-Goals:**
- Adding or removing CLI flags. The flag set is frozen by the catchup
  commit.
- Changing the tidal / eddy / flow field model — that lives in
  `maritime-current-fields` and is unchanged.
- Changing the PF config schema.

## Decisions

### D1. Substance scenarios reference the existing pipeline tests
Instead of inventing new end-to-end scenarios, the delta scenarios
reference concrete observable outcomes that the existing pipeline
tests already guarantee. For example:

> `--mean-flow-east-ms 0.15` produces a scenario where a static node's
> truth `surface_current` slot at tick 1 equals `(0.15, 0.0)` under
> zero tidal amplitude and no eddies.

This is a substance contract, not a shape contract, and it is already
enforced by `test_pipeline_field.py::test_mean_flow_east_appears_in_surface_current`
(or similar — the exact test name is not part of the contract, only
the pinned observable).

### D2. Unit tests for builder helpers
Two narrow unit tests land:

- `tests/maritime/test_fleet.py::test_make_m1_fleet_cadence_override_applies_to_all_nodes` —
  given `lora_period_sec=60.0` and `gps_period_sec=60.0`, every node's
  profile has the override applied (LoRa comms cycle + GPS sensor
  rate). This guards against the bug where an override silently
  applies to only a subset of node classes.
- `tests/maritime/test_run_pf_float.py::test_build_pf_config_applies_noise_overrides` —
  given an argparse.Namespace with non-None override values for each
  of the four noise flags, `_build_pf_config` returns a
  `PFFloatConfig` whose four noise fields match the overrides.

These are narrow because the pipeline tests already cover the
end-to-end behavior; the unit tests just pin the helpers so a
refactor of the argparse-to-config plumbing stays safe.

### D3. Do not re-express the `--eddy` parse contract in the delta
The `_parse_eddy_spec` helper already raises on malformed specs; a
scenario pinning the error message is not in scope here. The
substance contract is "a well-formed eddy injects a rotating current
perturbation"; validation-error surface is implementation detail.

## Key Type Contracts

- `make_m1_fleet(seed: int, bbox: tuple[float, float, float, float], *, lora_period_sec: float | None = None, gps_period_sec: float | None = None) -> tuple[Node, ...]`
  — keyword-only overrides, `None` means "use bundled profile values."
- `_build_pf_config(args: argparse.Namespace) -> PFFloatConfig`
  — uses `getattr(args, "predict_noise_*", None)`-style access to
  build the PFFloatConfig; `None` means default.
- No new types; the contract is on the argparse→dataclass plumbing.

## Risks / Trade-offs

- [Flags might be expanded again in Stage 3] → Stage 3 adds no new
  CLI flags (the velocity-model change is internal to dynamics +
  PF predict). Future velocity-related flags would land through their
  own spec change.
- [Tidal + eddy field behavior is not pinned here] → That contract
  belongs to `maritime-current-fields`, not `-scenario-gen`. The gen
  spec pins only "this flag maps to that field parameter"; the field
  model's behavior is a separate spec.

## Migration Plan

- Land delta specs + two new unit tests.
- `/opsx:verify` + `/opsx:sync` + `/opsx:archive`.

## Open Questions

None. Stage 3 (velocity model) is where new flags might appear; this
change is strictly retrospective.
