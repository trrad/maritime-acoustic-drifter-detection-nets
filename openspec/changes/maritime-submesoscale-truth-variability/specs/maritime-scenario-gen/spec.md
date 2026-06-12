## ADDED Requirements

### Requirement: Submesoscale CLI Flags
The scenario generator CLI SHALL accept the following additional flags governing the submesoscale truth-variability layer:

- `--submesoscale-amplitude-ms` (float, default 0.1) — rms amplitude of the sub-grid velocity field in m/s. Setting 0.0 disables the submesoscale layer entirely (scenario uses the base field alone).
- `--submesoscale-correlation-length-m` (float, default 750.0) — spatial correlation length of the submesoscale field in meters.
- `--submesoscale-correlation-time-sec` (float, default 1200.0 ≈ 20 min) — temporal OU correlation time.
- `--submesoscale-spectrum-slope` (float, default -1.667 ≈ Kolmogorov -5/3) — energy spectrum power-law slope.
- `--submesoscale-seed` (int, optional) — independent RNG stream seed for the submesoscale field. When omitted, derived deterministically from `--seed` so byte-identity is preserved across re-runs.
- `--submesoscale-grid-points` (int, default 256) — internal FFT grid size (power-of-two recommended).
- `--climatology-expected-submesoscale-ms` (float, default matches `--submesoscale-amplitude-ms`) — operator-expected rms submesoscale amplitude for the onboard climatology's variance channel (see `maritime-map-payload`'s climatology-carries-submesoscale-energy requirement). Decoupled from the truth amplitude so controlled experiments can test "prior wrong about expected amplitude" regimes.

#### Scenario: Default flags produce a realistic coastal regime
- **WHEN** the scenario generator is invoked with no submesoscale flags against a real-data bundled fixture (`--current-source real`)
- **THEN** the resulting scenario has submesoscale amplitude 0.1 m/s, correlation length 750 m, correlation time 1200 s
- **AND** the onboard climatology's `submesoscale_energy_ms` equals 0.1 m/s (matching default)

#### Scenario: Zero amplitude disables composition
- **WHEN** the CLI is invoked with `--submesoscale-amplitude-ms 0.0`
- **THEN** the scenario's truth field is the base field alone (no `CompositeCurrentField` wrapper in the construction path)

#### Scenario: Decoupled expected-amplitude flag
- **WHEN** the CLI is invoked with `--submesoscale-amplitude-ms 0.3 --climatology-expected-submesoscale-ms 0.1`
- **THEN** the truth field's submesoscale amplitude is 0.3 m/s
- **AND** the onboard climatology's `submesoscale_energy_ms` is 0.1 m/s
- **AND** this regime (prior under-estimates real variability) is a valid supported configuration (no error, generator completes normally)

#### Scenario: Submesoscale flags are recorded in the header
- **WHEN** a scenario is generated with any non-default submesoscale flags
- **THEN** the scenario header includes a `submesoscale` sub-object (or equivalent flat fields) carrying all six submesoscale parameters plus the climatology-expected-submesoscale value
- **AND** a downstream consumer reading the header can fully reconstruct the submesoscale configuration

### Requirement: Sidecar Includes Chaos Arrays
The `current_field_grid.npz` sidecar emitted by the scenario generator (see `maritime-scenario-gen`'s Current-Field Visualization Sidecar requirement from `maritime-real-current-data`) SHALL additionally carry two arrays:

- `truth_grid_chaos_u[t, i, j]` — submesoscale-only eastward velocity component at tick `t`, grid cell `(i, j)`; equals `truth_grid_u[t, i, j] - base_grid_u[t, i, j]`, shape `(n_ticks, n_grid, n_grid)`.
- `truth_grid_chaos_v[t, i, j]` — northward counterpart.

The existing `truth_grid_u`, `truth_grid_v` arrays SHALL continue to carry the *composed* truth velocity (base + submesoscale), so Change-1 consumers see the sum without downstream changes. When `--submesoscale-amplitude-ms 0.0`, the chaos arrays SHALL be present (shape-consistent) but equal zero within 1e-9 at every cell.

#### Scenario: Chaos arrays present with submesoscale active
- **WHEN** a scenario is generated with `--submesoscale-amplitude-ms 0.15` and its sidecar is loaded
- **THEN** `npz["truth_grid_chaos_u"]` and `npz["truth_grid_chaos_v"]` are present with shape `(n_ticks, n_grid, n_grid)`
- **AND** their rms magnitude is within ±20% of 0.15 m/s when measured over all cells and ticks

#### Scenario: Chaos arrays equal base-subtracted composite
- **WHEN** a scenario is generated with non-zero submesoscale amplitude and the sidecar's `truth_grid_u`, `base_grid_u`, `truth_grid_chaos_u` are compared element-wise
- **THEN** `truth_grid_u - base_grid_u` equals `truth_grid_chaos_u` within 1e-9 at every `(t, i, j)` (confirms the chaos array is the definitional difference, not an independent sample)

#### Scenario: Zero-amplitude chaos arrays are zero
- **WHEN** the generator is run with `--submesoscale-amplitude-ms 0.0`
- **THEN** `npz["truth_grid_chaos_u"]` and `npz["truth_grid_chaos_v"]` are present (shape-consistent) and equal zero within 1e-9 at every cell
