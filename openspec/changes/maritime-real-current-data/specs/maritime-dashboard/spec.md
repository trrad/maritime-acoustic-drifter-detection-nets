## ADDED Requirements

### Requirement: Current-Field Quiver Overlays
The dashboard SHALL render two toggleable Canvas quiver overlays visualizing the truth current field and the onboard climatology. Both overlays SHALL be rendered from the `current_field_grid.npz` sidecar emitted by the scenario generator (see `maritime-scenario-gen`'s "Current-Field Visualization Sidecar" requirement). The overlays SHALL render as arrow glyphs at each grid cell's `(lat, lon)`, oriented by `(u, v)` and length-scaled by speed. The two overlays SHALL use visually distinguishable styling — e.g., truth arrows in a saturated color (blue) and climatology arrows in a desaturated / dashed color (gray) — so that agreement and divergence between the two fields are visually apparent. Both overlays SHALL respect the user's pan/zoom state and time-slider position. Both are tick-indexed in the sidecar and evolve as the slider advances: truth evolves with the truth field's full time-dependence; climatology evolves through its tidal-harmonic phase structure (the underlying `HarmonicClimatology.velocity_at` is time-varying by construction).

#### Scenario: Truth overlay renders at scenario tick
- **WHEN** the dashboard is loaded against a scenario whose `current_field_grid.npz` sidecar contains non-trivial truth values and the "Truth currents" toggle is enabled
- **THEN** the Canvas contains arrow glyphs at the `n_grid × n_grid` sidecar grid cells
- **AND** the arrows' orientations and lengths change as the time slider advances (reflecting `truth_grid_u[t, :, :]` and `truth_grid_v[t, :, :]`)

#### Scenario: Climatology overlay evolves through tidal phases
- **WHEN** the "Climatology" toggle is enabled against a scenario whose climatology is a harmonic decomposition of a tidally-active hindcast (e.g., the primary Salish fixture) and the user scrubs the time slider across a tidal cycle (at least ~6 hours for M2)
- **THEN** the climatology quiver arrows at at least some grid cells rotate / reverse / change magnitude across slider positions (tidal-phase evolution is visually apparent)
- **AND** the spatial pattern of the climatology remains smoother than the truth pattern at any fixed tick (climatology resolves deterministic tidal structure, truth adds higher-frequency components)

#### Scenario: Climatology overlay is effectively constant in the synthetic path
- **WHEN** the "Climatology" toggle is enabled against a synthetic-path scenario (zero-constituent `HarmonicClimatology`) and the user scrubs the slider within a single month
- **THEN** the climatology quiver arrows do NOT change across slider positions within that month

#### Scenario: Truth and climatology overlays are visually distinguishable
- **WHEN** both overlays are enabled simultaneously
- **THEN** the dashboard JS renders truth arrows with one color / style and climatology arrows with a distinct second color / style (verified by inspecting the JS's arrow-rendering branches for two distinct style strings)
- **AND** the inlined JSON includes both `truth_grid` and `clim_grid` data separately so they can be rendered independently

#### Scenario: Overlays toggle off cleanly
- **WHEN** the user disables either toggle
- **THEN** the corresponding overlay's arrows are removed from the Canvas on the next redraw
- **AND** the other overlay's rendering is unaffected

#### Scenario: Overlays present on bundled scenario
- **WHEN** the dashboard is loaded against a scenario generated with `--current-source real` against a bundled real-data fixture
- **AND** `GET /` is fetched
- **THEN** the inlined JSON blob contains keys for both `truth_grid` and `clim_grid` (under the schema keys chosen by the builder — e.g., inside a top-level `current_field` sub-object)
- **AND** the parsed truth grid at some `(t, i, j)` differs from the climatology grid at the same `(t, i, j)` by more than 0.05 m/s (confirms the dashboard renders the substance of real-data truth-vs-climatology divergence, not placeholder-equal stubs)

### Requirement: Overlay Toggles In UI
The dashboard UI SHALL include two visibility toggle controls (checkboxes or equivalent) labeled `Truth currents` and `Climatology`. The toggles SHALL default to OFF so that the existing trail-and-icon rendering remains the default visual (keeps non-current-field users' experience unchanged). Toggling a control SHALL trigger a single Canvas redraw (aligned with the existing "Time Slider Scrubs All Layers Together" mechanism).

#### Scenario: Toggles default to off
- **WHEN** the dashboard is loaded
- **THEN** neither quiver overlay is rendered until the user enables the corresponding toggle

#### Scenario: Toggle change triggers redraw
- **WHEN** the user flips either toggle
- **THEN** the JS event handler invokes the same redraw function used by the time slider (verified by inspecting the toggle handlers for a call to the shared redraw function)
