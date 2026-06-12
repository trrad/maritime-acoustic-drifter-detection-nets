# Context Brief: maritime-map-payload

## Purpose
Provide a regional map data structure (bathymetry grid, land exclusion, shipping lanes, current climatology) that the maritime particle filter uses for map-aided navigation observations.

## Key Decisions
- Bathymetry on regular lat/lon grid with bilinear interpolation — carries forward to GEBCO in M3
- Synthetic bathymetry for M1 (shelf/slope/deep profile, not GEBCO extract) — avoids external data dependency
- Land exclusion delegates to coastline module's point_on_land (no reimplementation)
- Shipping lanes as polygon list with same ray-casting PIP as coastline
- Climatology as coarse grid of (mean_vx, mean_vy, var_vx, var_vy) — nearest-cell lookup
- All data injected at construction, not loaded internally — testable without file I/O

## Tasks
All 40/40 complete.

### Implementation notes
- BathymetryGrid boundary clamping: fixed bug where below-grid queries returned index 1 instead of 0
- make_onboard_map: keeps downsampled grid (not re-interpolated to original size), uses linspace subsampling for determinism
- hardware_footprint_bytes(): estimates 16-bit word count × 2, maps to SPRAM block sizing
- Onboard map test replaced RMSE >= 50m threshold with structural degradation assertions (fewer grid cells, smaller hardware footprint) per "no unprincipled numeric thresholds" principle
- climatology_from_field: triple loop sampling (lat × lon × time), 100 time samples over duration

## Files Affected
- rtl/vectors/maritime/map_payload.py (new — 317 lines)
- rtl/vectors/maritime/data/bathymetry_bc_coast.npz (new)
- rtl/vectors/maritime/data/shipping_lanes_bc_coast.geojson (new)
- tests/maritime/test_map_payload.py (new — 548 lines, 22 tests)

## Spec Pointers
maritime-map-payload → Requirement: Bathymetry Grid Interpolation, Requirement: Land Exclusion via Coastline, Requirement: Shipping Lane Membership, Requirement: Current Climatology Grid, Requirement: RegionalMap Composition, Requirement: Synthetic Bathymetry Generation, Requirement: Bathymetry Undefined on Land, Requirement: Onboard Map Fidelity Reduction, Requirement: Climatology Consistency with Truth Field
openspec/changes/maritime-map-payload/specs/maritime-map-payload/spec.md
