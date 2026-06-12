## ADDED Requirements

### Requirement: Truth Current Chaos Overlay
The dashboard SHALL render an optional toggleable Canvas overlay visualizing the submesoscale-only component of the truth current field. The overlay SHALL be sourced from `truth_grid_chaos_u` and `truth_grid_chaos_v` in the `current_field_grid.npz` sidecar (see `maritime-scenario-gen`'s Sidecar Includes Chaos Arrays requirement). Chaos arrows SHALL be visually distinct from the base-plus-composite truth overlay (defined by the Change 1 "Truth currents" overlay) — e.g., dashed or translucent arrows at the same grid cells — so the viewer can see "this is what the gridded prior cannot resolve" side-by-side with the full composed truth.

#### Scenario: Chaos overlay renders when sidecar has non-zero chaos arrays
- **WHEN** the dashboard is loaded against a scenario generated with `--submesoscale-amplitude-ms 0.15` and the "Truth current chaos" toggle is enabled
- **THEN** the Canvas contains arrow glyphs at the `n_grid × n_grid` grid cells representing the submesoscale-only velocity
- **AND** the chaos arrows move and change orientation as the time slider advances (reflecting `truth_grid_chaos_u[t, :, :]` evolution)

#### Scenario: Chaos overlay is styled distinctly from the main truth overlay
- **WHEN** both the "Truth currents" and "Truth current chaos" overlays are enabled
- **THEN** the dashboard JS renders the two overlays with distinct styles (e.g., solid vs dashed, or different color / opacity — verified by inspecting the JS's arrow-rendering branches for two distinct style strings)
- **AND** the inlined JSON includes both `truth_grid` and `truth_grid_chaos` data separately

#### Scenario: Chaos overlay is absent / zero when submesoscale is disabled
- **WHEN** the dashboard is loaded against a scenario generated with `--submesoscale-amplitude-ms 0.0`
- **AND** the "Truth current chaos" toggle is enabled
- **THEN** the overlay renders no arrows (the chaos arrays are zero; the renderer SHOULD render them as zero-length / invisible rather than raising)
- **AND** the dashboard does not crash when toggling the overlay on/off

#### Scenario: Toggle defaults to off
- **WHEN** the dashboard is loaded
- **THEN** the "Truth current chaos" toggle is unchecked by default (the main "Truth currents" + "Climatology" toggles from Change 1 remain the primary visualization knobs)

#### Scenario: Toggle change triggers redraw
- **WHEN** the user flips the "Truth current chaos" toggle
- **THEN** the JS event handler invokes the same redraw function used by the time slider and other toggles
