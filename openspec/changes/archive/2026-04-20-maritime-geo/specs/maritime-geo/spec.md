## ADDED Requirements

### Requirement: Lat/Lon to ENU Conversion
The system SHALL convert geographic coordinates (lat, lon in degrees) to local East-North-Up metric offsets (east_m, north_m in meters) relative to a reference point. The conversion SHALL use the WGS84 ellipsoid radii and SHALL be accurate to within 1 meter over 100 km baselines.

#### Scenario: Round-trip conversion preserves position within 1m
- **WHEN** a lat/lon pair at 50 km from the reference point is converted to ENU and back to lat/lon
- **THEN** the recovered lat/lon differs from the original by less than 1 meter (great-circle distance)

#### Scenario: Vectorized conversion for multiple nodes
- **WHEN** arrays of 10 lat/lon positions are converted to ENU
- **THEN** the output arrays have shape (10,) for both east_m and north_m
- **AND** each element matches the scalar conversion result for the same input

#### Scenario: Known-distance verification
- **WHEN** two points separated by exactly 10 km along the east axis are converted to ENU
- **THEN** the east_m difference is within 10 m of 10,000 m (0.1% accuracy)

### Requirement: Great-Circle Distance
The system SHALL compute the great-circle distance between two lat/lon points in meters. Accuracy SHALL be within 0.5% for distances up to 1,000 km and within 0.1% for distances under 100 km.

#### Scenario: Known distance pair
- **WHEN** distance is computed between (48.8, -123.5) and (49.0, -123.3)
- **THEN** the result is within 500 m of the surveyed distance (~27 km)

#### Scenario: Zero-distance returns zero
- **WHEN** distance is computed between a point and itself
- **THEN** the result is exactly 0.0

#### Scenario: Accuracy under 100 km
- **WHEN** distance is computed between two points separated by approximately 50 km
- **THEN** the result is within 0.1% of the reference value

### Requirement: Bearing Calculation
The system SHALL compute the initial bearing from one lat/lon point to another in degrees, in the range [0, 360).

#### Scenario: Due-east bearing
- **WHEN** bearing is computed from (48.8, -123.5) to (48.8, -123.3) (same latitude, eastward)
- **THEN** the result is within 2 degrees of 90

#### Scenario: Due-north bearing
- **WHEN** bearing is computed from (48.8, -123.5) to (49.0, -123.5) (same longitude, northward)
- **THEN** the result is within 2 degrees of 0 (or 360)

### Requirement: Coastline GeoJSON Loading
The system SHALL load OSM land polygon GeoJSON files and return polygon arrays suitable for geometric queries. Each polygon SHALL be an ndarray of shape (N, 2) with columns [lon_deg, lat_deg].

#### Scenario: Load bundled sample coastline
- **WHEN** the bundled BC coast (Strait of Georgia) sample coastline file is loaded
- **THEN** the result is a non-empty list of polygon arrays
- **AND** each polygon has shape (N, 2) with N >= 3

#### Scenario: Invalid file raises clear error
- **WHEN** a non-existent file path is provided
- **THEN** a FileNotFoundError is raised with a message indicating the missing file

### Requirement: Coastline BBox Clipping
The system SHALL filter a list of coastline polygons to only those intersecting a given bounding box. Polygons entirely outside the bbox SHALL be excluded.

#### Scenario: Clip to BC coast bbox
- **WHEN** coastline polygons are clipped to bbox (48.4, -123.8, 49.2, -123.2)
- **THEN** the result contains fewer polygons than the input
- **AND** at least one polygon remains (BC coast has complex coastline)

#### Scenario: Clip to empty bbox returns no polygons
- **WHEN** coastline polygons are clipped to a bbox in the open ocean with no land (e.g., center Pacific)
- **THEN** the result is an empty list

### Requirement: Point-on-Land Detection
The system SHALL determine whether a given lat/lon point falls on land by testing against coastline polygons.

#### Scenario: Known on-land point is detected
- **WHEN** point-on-land is tested for a known coastal location (e.g., Victoria, BC at approximately 48.43, -123.37)
- **THEN** the result is True

#### Scenario: Known offshore point is not land
- **WHEN** point-on-land is tested for a point 10 km offshore in the Strait of Georgia
- **THEN** the result is False

#### Scenario: Land detection uses correct polygons
- **WHEN** point-on-land is tested against an empty polygon list
- **THEN** the result is False (no land = all water)
