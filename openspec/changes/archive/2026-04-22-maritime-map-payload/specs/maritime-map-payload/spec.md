## ADDED Requirements

### Requirement: Bathymetry Grid Interpolation
The system SHALL provide a `BathymetryGrid` that stores seafloor depth on a regular lat/lon grid and returns depth at arbitrary query points. Interpolation at the midpoint between four adjacent grid nodes SHALL return their linear average. The grid SHALL cover the simulation bbox with positive depth values (depth below sea level in meters).

#### Scenario: Grid point query returns exact stored value
- **WHEN** depth is queried at a grid node location (lat, lon matching a grid point exactly)
- **THEN** the returned depth equals the stored value at that grid point

#### Scenario: Interpolated query between grid points
- **WHEN** depth is queried at a point midway between four adjacent grid nodes with stored depths [100, 110, 120, 130] m
- **THEN** the returned depth equals the linear average of the four adjacent values (115 m)

#### Scenario: Query outside grid returns boundary value
- **WHEN** depth is queried at a lat/lon outside the grid extent
- **THEN** the returned depth equals the nearest boundary grid value (no extrapolation beyond boundary values)

### Requirement: Land Exclusion via Coastline
The `RegionalMap` SHALL provide an `is_on_land(lat, lon)` method that delegates to the coastline module's point-in-polygon test. This SHALL return True for positions on land and False for positions on water.

#### Scenario: Delegation to coastline module
- **WHEN** `is_on_land` is called with a known on-land coordinate (Victoria, BC at approximately 48.43, -123.37 — matches the test point used in `maritime-geo`)
- **THEN** it returns True, matching the result of `coastline.point_on_land` called with the same coordinate and polygons

#### Scenario: Empty land polygons — all water
- **WHEN** `is_on_land` is called with an empty `land_polygons` list
- **THEN** it returns False for all coordinates

### Requirement: Shipping Lane Membership
The `RegionalMap` SHALL provide an `is_in_shipping_lane(lat, lon)` method that tests point membership against a list of shipping lane polygons using ray-casting point-in-polygon.

#### Scenario: Point inside shipping lane polygon
- **WHEN** `is_in_shipping_lane` is called with a coordinate inside a defined shipping lane polygon
- **THEN** it returns True

#### Scenario: Point outside all shipping lanes
- **WHEN** `is_in_shipping_lane` is called with a coordinate not inside any shipping lane polygon
- **THEN** it returns False

#### Scenario: No shipping lanes defined
- **WHEN** `is_in_shipping_lane` is called with an empty `shipping_lanes` list
- **THEN** it returns False for all coordinates

### Requirement: Current Climatology Grid
The system SHALL provide a `ClimatologyGrid` storing mean and variance of current velocity on a regular lat/lon grid. The `at(lat, lon)` method SHALL return the nearest grid cell's (mean_vx, mean_vy, var_vx, var_vy) values.

#### Scenario: Query at grid center returns cell values
- **WHEN** climatology is queried at a lat/lon closest to a grid cell with mean_vx = 0.1, mean_vy = -0.05
- **THEN** the returned mean values match the cell values exactly

#### Scenario: Variance is non-negative
- **WHEN** a ClimatologyGrid is constructed with valid parameters
- **THEN** all variance values are non-negative

### Requirement: RegionalMap Composition
The `RegionalMap` SHALL compose bathymetry, land polygons, shipping lanes, and climatology into a single data structure. All map data SHALL be injected at construction time, not loaded internally.

#### Scenario: Construction with pre-built components
- **WHEN** a `RegionalMap` is constructed with a `BathymetryGrid`, land polygons, shipping lanes, and a `ClimatologyGrid`
- **THEN** all query methods (`depth_at`, `is_on_land`, `is_in_shipping_lane`, `current_climatology_at`) work correctly

#### Scenario: No file I/O during construction
- **WHEN** `RegionalMap.__init__` is called with in-memory data
- **THEN** no file system access occurs (constructible from pure test data)

### Requirement: Synthetic Bathymetry Generation
The system SHALL provide a `generate_synthetic_bathymetry(bbox, resolution_deg)` function that creates a `BathymetryGrid` with a physically reasonable seafloor profile including a continental shelf (depth ~100-200 m), a shelf break/slope (depth increasing from ~200 m to ~1000 m), and a deep area (>1000 m). All depth values SHALL be positive.

#### Scenario: Generated grid has positive depths
- **WHEN** synthetic bathymetry is generated for the bundled test bbox (BC coast, per `maritime-geo`) at 0.01° resolution
- **THEN** all depth values are positive (> 0 m)

#### Scenario: Generated grid has shelf and deep areas
- **WHEN** synthetic bathymetry is generated for the bundled test bbox
- **THEN** depths near the coast are < 500 m (shelf)
- **AND** depths in the offshore portion are > 500 m (deep)

#### Scenario: Generated grid resolution matches parameter
- **WHEN** synthetic bathymetry is generated with `resolution_deg = 0.01`
- **THEN** the grid spacing in both lat and lon dimensions is within 0.001° of 0.01°

### Requirement: Bathymetry Undefined on Land
The `depth_at` method SHALL return NaN for coordinates where `is_on_land` returns True. This prevents the PF from generating valid bathymetry-match observations for terrestrial particles, ensuring that land exclusion and bathymetry observations are consistent.

#### Scenario: Depth query on known land returns NaN
- **WHEN** `depth_at` is called with coordinates where `is_on_land` returns True
- **THEN** the return value is NaN (not a number)

#### Scenario: Depth query on water returns finite value
- **WHEN** `depth_at` is called with coordinates where `is_on_land` returns False
- **THEN** the return value is a finite positive number

### Requirement: Onboard Map Fidelity Reduction
The system SHALL provide a `make_onboard_map(truth_map, fidelity, seed)` function (generator-side only) that returns a deliberately imperfect `RegionalMap` derived from the truth map. The onboard map's bathymetry SHALL differ from truth by at least a configurable RMSE threshold (default: 50 m) at the shelf break region. This function SHALL be called by the scenario generator when producing the onboard-map sidecar (see `maritime-scenario-gen` Requirement: Onboard Map Distributed As Scenario Sidecar). The PF SHALL NOT call `make_onboard_map` and SHALL NOT access the truth map directly; the PF consumes the onboard map through `ScenarioReader(path).onboard_map()`.

#### Scenario: Onboard bathymetry differs from truth at shelf break
- **WHEN** an onboard map is generated with default fidelity reduction from a truth map
- **AND** depth is compared between truth and onboard maps at 10 points along the shelf break
- **THEN** the RMSE of the differences is at least 50 m

#### Scenario: Onboard map is reproducible from seed
- **WHEN** an onboard map is generated twice with the same truth map and same seed
- **THEN** the resulting bathymetry grids are identical

#### Scenario: Onboard coastline may differ from truth
- **WHEN** an onboard map uses simplified coastline compared to truth
- **THEN** there exists at least one coastal point where truth and onboard maps disagree on land/water status

#### Scenario: Onboard map construction is deterministic
- **WHEN** an onboard map is constructed from a known truth map with a known seed
- **THEN** the depth error at any specific point is reproducible across runs

### Requirement: Climatology Consistency with Truth Field
The system SHALL provide a `climatology_from_field` function that derives a `ClimatologyGrid` from a `CurrentField` by sampling the field over a specified duration. This ensures the PF's climatology prior is consistent with the truth current field by construction. The derived climatology mean SHALL match the time-averaged field velocity within 0.01 m/s at each grid point.

#### Scenario: Derived climatology matches time-averaged field
- **WHEN** `climatology_from_field` is called with a synthetic field having mean_vx = 0.1 m/s
- **THEN** the derived climatology's mean_vx at all grid points is within 0.01 m/s of 0.1

#### Scenario: Derived climatology variance is non-negative
- **WHEN** `climatology_from_field` produces a ClimatologyGrid
- **THEN** all variance values are non-negative
