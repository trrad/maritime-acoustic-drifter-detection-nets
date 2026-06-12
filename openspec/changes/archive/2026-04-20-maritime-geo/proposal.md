## Why

All maritime modules operate on geographic coordinates (lat/lon) but need metric-space calculations for dynamics (advection in m/s), sensor models (ranges in meters), and map operations (bathymetry interpolation). The coastline loader is needed for both the scenario generator (land exclusion) and the dashboard (coast rendering). These geographic primitives are the shared foundation for every subsequent maritime change.

## What Changes

- Add `rtl/vectors/maritime/coords.py` — coordinate conversion utilities: lat/lon ↔ local ENU meters, haversine distance, bearing, bbox operations
- Add `rtl/vectors/maritime/coastline.py` — Natural Earth GeoJSON coastline loader, bbox clipping, point-in-polygon test for land exclusion
- Add bundled sample coastline data in `rtl/vectors/maritime/data/` — pre-clipped GeoJSON from OSM land polygons for the default BC coast (Strait of Georgia) test bbox
- Add tests for coordinate conversion accuracy (< 1m over 100km baselines) and coastline loading/intersection

## Capabilities

### New Capabilities
- `maritime-geo`: Geographic coordinate conversion (lat/lon ↔ metric ENU, haversine, bearing, bbox) and coastline data loading (Natural Earth GeoJSON, bbox clipping, point-in-polygon land exclusion)

### Modified Capabilities
(none — no existing specs)

## Impact

- **New files**: `rtl/vectors/maritime/coords.py`, `rtl/vectors/maritime/coastline.py`, `rtl/vectors/maritime/__init__.py`, `rtl/vectors/maritime/data/` (sample coastline), `tests/maritime/__init__.py`, `tests/maritime/test_coords.py`, `tests/maritime/test_coastline.py`
- **No existing file modifications**: All new code in the `maritime/` subpackage
- **Dependencies**: numpy (already declared), no new external dependencies
