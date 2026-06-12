## Context

Maritime fleet simulation operates in two coordinate systems:
1. **Geographic** (lat/lon in degrees) — for scenario output, dashboard display, and real-world data
2. **Local metric** (east/north in meters from a reference point) — for dynamics propagation, sensor calculations, and PF state

Every downstream module needs to convert between these. The coastline module provides the land/water boundary used by the scenario generator (land exclusion for particle placement), the map payload (bathymetry masking), and the dashboard (coast rendering).

The reference point for ENU conversion is the center of the simulation bbox. For the default BC coast test bbox (48.4,-123.8,49.2,-123.2) covering the southern Strait of Georgia, this is approximately (48.8, -123.5).

## Goals / Non-Goals

**Goals:**
- Accurate lat/lon ↔ ENU meter conversion within the ~50km bbox scale
- Haversine distance accurate to < 1m over 100km baselines
- Coastline GeoJSON loading and bbox clipping (reduce full Natural Earth to simulation area)
- Point-in-polygon test for land exclusion at arbitrary lat/lon
- All functions pure and testable without file I/O (coastline data injected)

**Non-Goals:**
- Geodetic datum transformations (WGS84 assumed throughout)
- Map projections beyond local ENU (no UTM, no Mercator tile math)
- Full Natural Earth dataset bundling (only the clipped sample for the default bbox)
- Routing or pathfinding along coastlines
- GEBCO/ETOPO bathymetry loading (separate change: maritime-map-payload)

## Decisions

### D1: Local ENU (East-North-Up) as the metric frame

**Choice:** East-North-Up tangent plane centered on the bbox center. Conversion via standard WGS84 radii.

**Why:** ENU is the standard local tangent plane for small-area navigation. At the ~50km bbox scale, the flat-Earth approximation introduces < 0.1% error, which is well below sensor noise. Up (altitude/depth) is kept in geographic coordinates (meters from sea level) rather than projected.

**Alternatives considered:**
- UTM zone-based: unnecessary complexity for a ~50km area, adds zone boundary logic
- Pure lat/lon with degree-based distances: wrong units for dynamics (m/s doesn't compose cleanly with degrees)

### D2: Coastline data as pre-clipped GeoJSON, loaded at runtime

**Choice:** Ship a pre-clipped GeoJSON file (~2.7MB) for the default BC coast (Strait of Georgia) bbox in `rtl/vectors/maritime/data/`, generated from OSM land polygons. For other bboxes, the user generates a new clip from the full OSM land polygons dataset using a separate script.

**Why:** Full OSM land polygons (split-4326) is ~876MB. Clipping to the bbox gives ~2.7MB with 866 polygons and up to 28K vertices per polygon — far better resolution than Natural Earth 1:10m (~15-30 vertices per polygon in this area). The BC coast's complex fjord and island geometry makes it an ideal test region for land exclusion and bbox clipping.

**Alternatives considered:**
- Load full OSM land polygons every time: too large (876MB), too slow
- Natural Earth 1:10m: too coarse (~15-30 vertices per polygon in this area, ~1-2km resolution)
- Use shapely for geometry: dependency for one operation; ray-casting PIP is ~50 lines

### D3: Ray-casting point-in-polygon (no shapely dependency)

**Choice:** Implement ray-casting PIP directly. GeoJSON polygons are arrays of [lon, lat] coordinate pairs.

**Why:** Avoids adding shapely as a dependency. The coastline polygons for a ~50km bbox have at most a few hundred vertices — ray-casting is fast enough. The implementation is straightforward and well-tested.

### D4: Functions take lat/lon as scalars or arrays

**Choice:** All coordinate conversion functions accept either scalar float or numpy array inputs. Return type matches input type.

**Why:** Scenario generator needs vectorized operations (convert all node positions at once). Individual point tests need scalar operation. numpy handles both naturally if written correctly.

## Risks / Trade-offs

- **[Risk] ENU accuracy degrades at bbox edges** → At 50km from the reference point, ENU error is ~0.3m. This is well below sensor noise (>5m for LoRa ranging, >1m for GPS). No mitigation needed.
- **[Risk] Pre-clipped coastline only covers default bbox** → The scenario generator will error with a clear message if run outside the bundled data extent. User can generate new clips from Natural Earth. Primary testing regions are BC coast and Canadian North.
- **[Trade-off] No shapely means no polygon union/difference operations** → Acceptable. Land exclusion only needs point-in-polygon. If future work needs polygon operations (e.g., computing water area), shapely can be added then.

## Key Type Contracts

```
# Coordinate conversion
latlon_to_enu(lat_deg: float | ndarray, lon_deg: float | ndarray, ref_lat_deg: float, ref_lon_deg: float) -> tuple[ndarray, ndarray]
    # Returns (east_m, north_m) offsets from reference point

enu_to_latlon(east_m: float | ndarray, north_m: float | ndarray, ref_lat_deg: float, ref_lon_deg: float) -> tuple[ndarray, ndarray]
    # Returns (lat_deg, lon_deg)

haversine_m(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float
    # Returns distance in meters

bearing_deg(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float
    # Returns bearing in degrees [0, 360)

# Coastline
load_coastline_geojson(path: str) -> list[ndarray]
    # Returns list of polygon arrays, each shape (N, 2) as [lon, lat]

clip_coastline_bbox(polygons: list[ndarray], south: float, west: float, north: float, east: float) -> list[ndarray]
    # Returns only polygons that intersect the bbox

point_on_land(lat_deg: float, lon_deg: float, polygons: list[ndarray]) -> bool
    # Ray-casting PIP test

BBox = namedtuple("BBox", ["south", "west", "north", "east"])
    # All fields in degrees
```
