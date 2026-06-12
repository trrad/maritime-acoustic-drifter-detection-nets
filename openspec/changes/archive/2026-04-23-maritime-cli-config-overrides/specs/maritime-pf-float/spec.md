## MODIFIED Requirements

### Requirement: CLI Invocation
The system SHALL provide a CLI at `rtl/vectors/maritime/run_pf_float.py`
accepting:

- `--scenario <path>` (required): input scenario JSONL.
- `--out <path>` (required): main estimate stream output path.
- `--particles-out <path>` (optional): sidecar output path. If
  omitted and `--no-particles` is not set, defaults to a path derived
  from `--out`.
- `--no-particles`: disables the sidecar entirely.
- `--thin-ticks N` (default 1): tick-thinning for the sidecar.
- `--thin-particles K` (default 50): particle-subsample count for
  each sidecar record.
- `--thin-nodes IDS` (optional): comma-separated subset of node_ids
  for the sidecar (default: all nodes).
- `--n-particles N` (default 500): underlying PF particle count.
- `--predict-noise-pos <float>` (optional): override the PF's
  predict-step position process noise (m / √s). Default:
  `PFFloatConfig.process_noise_pos_m_per_sqrt_s`.
- `--predict-noise-vel <float>` (optional): override the PF's
  predict-step velocity process noise (m/s / √s). Default:
  `PFFloatConfig.process_noise_vel_ms_per_sqrt_s`. Setting 0.0
  disables the velocity random-walk entirely (supports the
  "is velocity a free RW?" hypothesis test that motivated Stage 3's
  climatology-slaved velocity rework).
- `--predict-noise-heading <float>` (optional): override
  predict-step heading process noise (deg / √s). Default:
  `PFFloatConfig.process_noise_heading_deg_per_sqrt_s`.
- `--predict-noise-current <float>` (optional): override predict-step
  current-state process noise (m/s / √s). Default:
  `PFFloatConfig.process_noise_current_ms_per_sqrt_s`.
- `--summary-out <path>` (optional): summary JSON output path.
  Defaults to a path derived from `--out`.

The CLI SHALL open the scenario via `ScenarioReader` for the PF's
observation intake (the `ObservationRecord` stream that feeds
`PFFloat.weight`). The CLI MAY additionally open
`ScenarioTruthReader` on the same scenario file strictly for the
truth-dependent portions of the summary report (per-class RMSE
aggregates in `pf_summary.json`); truth data SHALL NOT flow into
any `PFFloat` method call. The CLI SHALL NOT expose a
`--focus-nodes` flag — thinning replaces it. When a
`--predict-noise-*` flag is not provided (value is `None` after
argparse), the CLI SHALL use the corresponding
`PFFloatConfig.process_noise_*` default; when provided, the CLI
SHALL plumb the supplied float into the `PFFloatConfig` used by
every `PFFloat` instance for every node in the fleet (uniform
override, not per-class).

#### Scenario: CLI produces valid main estimate file
- **WHEN** the CLI is run against a valid scenario with `--scenario`, `--out`, and default flags
- **THEN** the command exits 0
- **AND** the main output file is a valid `maritime-pf-estimate-schema` v1.0 stream

#### Scenario: --predict-noise-vel 0.0 disables the velocity random walk
- **WHEN** the CLI is run against a valid scenario with `--predict-noise-vel 0.0` and all other predict-noise flags omitted
- **THEN** every PF instance's `PFFloatConfig.process_noise_vel_ms_per_sqrt_s == 0.0`
- **AND** in a scenario where no observations update weights (e.g., observation allow-list empty), particle velocity values are constant across predict ticks within float tolerance (no stochastic evolution of velocity dimension)

#### Scenario: --predict-noise-pos, --predict-noise-heading, --predict-noise-current plumb through to PFFloatConfig
- **WHEN** the CLI is run with `--predict-noise-pos 0.1 --predict-noise-heading 0.5 --predict-noise-current 0.02`
- **THEN** every PF instance's `PFFloatConfig.process_noise_pos_m_per_sqrt_s == 0.1`
- **AND** `process_noise_heading_deg_per_sqrt_s == 0.5`
- **AND** `process_noise_current_ms_per_sqrt_s == 0.02`

#### Scenario: CLI rejects scenario with unsupported schema version
- **WHEN** the CLI is run against a scenario whose header declares an unsupported `schema_version`
- **THEN** the CLI exits nonzero
- **AND** stderr names the version mismatch

#### Scenario: Legacy --focus-nodes flag is not accepted
- **WHEN** the CLI is invoked with the legacy flag `--focus-nodes n01,n05` (the privileged-subset flag from earlier drafts)
- **THEN** the CLI exits nonzero with an "unrecognized argument" error naming `--focus-nodes`
- **AND** per-node particle filtering is available via `--thin-nodes` (see the "Particle Sidecar Emission with Thinning" requirement) — the flag name changed because the semantics changed from a privileged-subset to a thinning knob

#### Scenario: CLI with --no-particles skips sidecar
- **WHEN** the CLI is invoked with `--no-particles`
- **THEN** only the main estimate file and `pf_summary.json` are written
- **AND** no particle sidecar path is produced
