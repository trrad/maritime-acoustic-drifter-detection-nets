## Why

The maritime particle filter uses map-aided navigation as a core observation source: bathymetry matching (measured depth consistent with seafloor depth at estimated position), land exclusion (zero-weight particles on land), shipping lane priors (reduce false acoustic detections), and current climatology (drift prediction when no real-time estimate available). The map payload represents the static geographic data each node carries.

## What Changes

- Add `rtl/vectors/maritime/map_payload.py` — regional map data structure with bathymetry grid, land polygons, shipping lane polygons, and current climatology priors
- Bathymetry grid: bilinear interpolation of depth at arbitrary lat/lon
- Land exclusion: delegates to coastline module's point-on-land test
- Shipping lanes: polygon membership test for acoustic classification priors
- Current climatology: mean and variance of current velocity at each grid cell
- Add bundled sample bathymetry data for the default test bbox (BC coast / Strait of Georgia, consistent with the coastline data bundled by `maritime-geo`)
- Tests verify interpolation accuracy, land detection, and lane membership

## Capabilities

### New Capabilities
- `maritime-map-payload`: Regional map data structure providing bathymetry interpolation, land exclusion, shipping lane membership, and current climatology queries for the maritime particle filter's map-aided navigation

### Modified Capabilities
(none)

## Impact

- **New files**: `rtl/vectors/maritime/map_payload.py`, `rtl/vectors/maritime/data/` (sample bathymetry), `tests/maritime/test_map_payload.py`
- **No existing file modifications**
- **Dependencies**: numpy (already declared). Uses coastline module from `maritime-geo` for land exclusion — depends on that change being applied first.
