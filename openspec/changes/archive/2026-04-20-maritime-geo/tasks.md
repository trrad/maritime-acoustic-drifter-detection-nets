## 1. Coordinate Conversion — Tests

- [x] 1.1 Lat/lon ↔ ENU round-trip preserves position within 1 m over 50 km baselines — tests round-trip accuracy for scalar and vectorized inputs
      (tests/maritime/test_coords.py)

- [x] 1.2 Haversine distance matches known distances within 0.5% — tests zero-distance, known-distance pair (Strait of Georgia), and antipodal edge case
      (tests/maritime/test_coords.py)

- [x] 1.3 Bearing returns correct values for cardinal directions — tests due-north (~0°), due-east (~90°), due-south (~180°), due-west (~270°)
      (tests/maritime/test_coords.py)

## 2. Coordinate Conversion — Implementation

- [x] 2.1 `latlon_to_enu` converts lat/lon arrays to ENU meters using WGS84 radii — scalar and array inputs, returns (east_m, north_m)
      (rtl/vectors/maritime/coords.py)

- [x] 2.2 `enu_to_latlon` converts ENU meters back to lat/lon — inverse of latlon_to_enu
      (rtl/vectors/maritime/coords.py)

- [x] 2.3 `haversine_m` computes great-circle distance in meters — haversine formula with WGS84 mean radius
      (rtl/vectors/maritime/coords.py)

- [x] 2.4 `bearing_deg` computes initial bearing in degrees [0, 360) — standard forward azimuth formula
      (rtl/vectors/maritime/coords.py)

## 3. Coastline — Tests

- [x] 3.1 Loading a valid GeoJSON returns non-empty polygon list with correct shapes — tests that bundled sample loads and each polygon is (N, 2)

- [x] 3.2 Non-existent file raises FileNotFoundError — tests error path for missing coastline file

- [x] 3.3 BBox clipping excludes polygons outside the bbox and retains those inside — tests with BC coast bbox against sample data

- [x] 3.4 Point-on-land returns True for known coastal point (Victoria, BC), False for known offshore point (Strait of Georgia)

- [x] 3.5 Point-on-land returns False for empty polygon list — tests degenerate case

## 4. Coastline — Implementation

- [x] 4.1 `load_coastline_geojson` loads OSM land polygon GeoJSON and extracts polygon arrays — returns list of ndarray(N, 2) as [lon, lat]
      (rtl/vectors/maritime/coastline.py)

- [x] 4.2 `clip_coastline_bbox` filters polygons to those intersecting a bounding box — bbox-exclusion test per polygon
      (rtl/vectors/maritime/coastline.py)

- [x] 4.3 `point_on_land` performs ray-casting point-in-polygon test — iterates polygons, returns True on first hit
      (rtl/vectors/maritime/coastline.py)

## 5. Package Initialization and Data

- [x] 5.1 `rtl/vectors/maritime/__init__.py` imports public API from coords and coastline modules
      (rtl/vectors/maritime/__init__.py)

- [x] 5.2 Bundled sample coastline GeoJSON for BC coast (Strait of Georgia) bbox is placed in `rtl/vectors/maritime/data/` and loadable by coastline module
      (rtl/vectors/maritime/data/)

## 6. Verification

- [x] 6.1 `uv run pytest tests/maritime/` passes with zero failures
- [x] 6.2 `git diff` shows zero modifications to frozen baseline files (experiments/01–11, existing rtl/vectors/*)
- [x] 6.3 Coordinate round-trip error < 1 m for points within 50 km of reference
- [x] 6.4 Point-on-land correctly identifies at least one on-land and one offshore point in BC coast (Strait of Georgia)
