## 1. Bathymetry Grid — Tests

- [x] 1.1 Grid point query returns exact stored value — test at all four corners of the grid
      (tests/maritime/test_map_payload.py)

- [x] 1.2 Bilinear interpolation at grid midpoint returns expected average — construct 2×2 grid with known depths, query center
      (tests/maritime/test_map_payload.py)

- [x] 1.3 Query outside grid extent returns nearest boundary value — test with point 0.1° beyond each edge
      (tests/maritime/test_map_payload.py)

## 2. Bathymetry Grid — Implementation

- [x] 2.1 `BathymetryGrid` dataclass with `lats`, `lons`, `depths_m` arrays and `at(lat, lon)` bilinear interpolation method
      (rtl/vectors/maritime/map_payload.py)

## 3. Climatology Grid — Tests

- [x] 3.1 Nearest-cell query returns correct mean and variance values — construct grid with known values, query at cell center
      (tests/maritime/test_map_payload.py)

- [x] 3.2 Variance values are non-negative — test that construction with negative variance raises ValueError
      (tests/maritime/test_map_payload.py)

## 4. Climatology Grid — Implementation

- [x] 4.1 `ClimatologyGrid` dataclass with mean/variance arrays and `at(lat, lon)` nearest-cell lookup
      (rtl/vectors/maritime/map_payload.py)

## 5. RegionalMap Composition — Tests

- [x] 5.1 `is_on_land` delegates correctly to coastline.point_on_land — test with known on-land and offshore points
      (tests/maritime/test_map_payload.py)

- [x] 5.2 `is_on_land` returns False for empty polygon list — degenerate case
      (tests/maritime/test_map_payload.py)

- [x] 5.3 `is_in_shipping_lane` returns True for point inside polygon, False outside — test with synthetic lane polygon
      (tests/maritime/test_map_payload.py)

- [x] 5.4 `is_in_shipping_lane` returns False for empty lane list — degenerate case
      (tests/maritime/test_map_payload.py)

- [x] 5.5 `depth_at` delegates to BathymetryGrid.at — test with known grid and query point
      (tests/maritime/test_map_payload.py)

- [x] 5.6 `current_climatology_at` delegates to ClimatologyGrid.at — test with known grid
      (tests/maritime/test_map_payload.py)

- [x] 5.7 Construction from pure in-memory data — no file I/O during RegionalMap.__init__
      (tests/maritime/test_map_payload.py)

## 6. RegionalMap — Implementation

- [x] 6.1 `RegionalMap` dataclass composing bathymetry, land_polygons, shipping_lanes, and climatology with query methods
      (rtl/vectors/maritime/map_payload.py)

- [x] 6.2 `is_on_land` method delegating to coastline.point_on_land with stored land_polygons
      (rtl/vectors/maritime/map_payload.py)

- [x] 6.3 `is_in_shipping_lane` method using ray-casting PIP against stored shipping lane polygons
      (rtl/vectors/maritime/map_payload.py)

## 7. Synthetic Bathymetry Generation — Tests

- [x] 7.1 Generated grid has all positive depths for the bundled test bbox — iterate all grid values
      (tests/maritime/test_map_payload.py)

- [x] 7.2 Generated grid has shelf (< 500 m) and deep (> 500 m) areas — query near coast and offshore
      (tests/maritime/test_map_payload.py)

- [x] 7.3 Grid spacing matches resolution_deg parameter — check lat and lon deltas
      (tests/maritime/test_map_payload.py)

## 8. Synthetic Bathymetry Generation — Implementation

- [x] 8.1 `generate_synthetic_bathymetry(bbox, resolution_deg)` creates BathymetryGrid with shelf/slope/deep profile — depth increases with distance from coast, includes a canyon feature
      (rtl/vectors/maritime/map_payload.py)

## 9. Map Loading — Implementation

- [x] 9.1 `load_regional_map(data_dir)` loads bathymetry (.npz), coastline (.geojson), and climatology from a data directory and returns a RegionalMap
      (rtl/vectors/maritime/map_payload.py)

## 10. Bundled Data

- [x] 10.1 Sample synthetic bathymetry .npz file for the BC coast test bbox (consistent with `maritime-geo` bundled coastline) placed in `rtl/vectors/maritime/data/`
      (rtl/vectors/maritime/data/)

- [x] 10.2 Sample shipping lane polygon for coastal approach lanes consistent with the BC coast test bbox placed in data directory
      (rtl/vectors/maritime/data/)

## 11. Land/Water Consistency — Tests

- [x] 11.1 `depth_at` returns NaN for coordinates where `is_on_land` is True — test with known on-land point
      (tests/maritime/test_map_payload.py)

- [x] 11.2 `depth_at` returns finite positive value for coordinates where `is_on_land` is False — test with known offshore point
      (tests/maritime/test_map_payload.py)

## 12. Land/Water Consistency — Implementation

- [x] 12.1 `depth_at` checks `is_on_land` before interpolation and returns NaN for land coordinates
      (rtl/vectors/maritime/map_payload.py)

## 13. Onboard Map Fidelity — Tests

- [x] 13.1 Onboard map bathymetry RMSE at shelf break is at least 50 m with default fidelity — compare truth vs onboard at 10 points
      (tests/maritime/test_map_payload.py)

- [x] 13.2 Onboard map reproduction — same seed produces identical onboard map
      (tests/maritime/test_map_payload.py)

- [x] 13.3 Onboard coastline differs from truth at least one point — simplified coastline introduces land/water disagreement
      (tests/maritime/test_map_payload.py)

## 14. Onboard Map Fidelity — Implementation

- [x] 14.1 `make_onboard_map(truth_map, fidelity, seed)` produces degraded copy — downsamples bathymetry, simplifies coastline, drops minor shipping lanes
      (rtl/vectors/maritime/map_payload.py)

## 15. Climatology Derivation — Tests

- [x] 15.1 Derived climatology mean matches time-averaged field velocity within 0.01 m/s — sample synthetic field with known mean
      (tests/maritime/test_map_payload.py)

- [x] 15.2 Derived climatology variance is non-negative at all grid points
      (tests/maritime/test_map_payload.py)

## 16. Climatology Derivation — Implementation

- [x] 16.1 `climatology_from_field(field, bbox, grid_resolution_deg, duration_sec, seed)` samples field over time and derives mean/variance grids
      (rtl/vectors/maritime/map_payload.py)

## 17. Verification

- [x] 17.1 `uv run pytest tests/maritime/test_map_payload.py` passes with zero failures
- [x] 17.2 Frozen baseline intact — `git diff` shows zero modifications to existing files
- [x] 17.3 Synthetic bathymetry all-positive, has shelf and deep areas, grid spacing correct
- [x] 17.4 RegionalMap constructible from in-memory data (no file I/O during init)
- [x] 17.5 Land/water consistency: depth_at returns NaN on land, finite on water
- [x] 17.6 Onboard map: structurally degraded (fewer grid cells), reproducible from seed, hardware footprint smaller
