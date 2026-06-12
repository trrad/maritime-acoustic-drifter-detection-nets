## Context

Each maritime node carries a regional map payload used by the particle filter as additional observations. The plan doc (`maritime_scenario_harness_plan.md`) specifies four map-aid functions:
1. **Bathymetry match**: measured depth consistent with `bathy(lat_est, lon_est)`
2. **Land exclusion**: zero-weight particles inside coastline
3. **Climatology prior**: drift prediction uses climatological current mean + variance
4. **Shipping lane priors**: reduces false-positive acoustic classification

The map payload is ~100 KB–1 MB per node in the full system. For M1, the bathymetry grid is synthetic (analytical seafloor profile) or from a small GEBCO extract. The shipping lanes are simplified polygons. The climatology is derived from the synthetic current field's parameters.

## Goals / Non-Goals

**Goals:**
- `RegionalMap` class holding bathymetry grid, land polygons, shipping lane polygons, and climatology data
- Bathymetry interpolation: bilinear from grid, accurate to one grid cell
- Land exclusion via coastline module's `point_on_land` (delegation, not reimplementation)
- Bathymetry returns NaN for land coordinates (Level 4 integrity: PF can't get valid depth on land)
- Shipping lane membership test (point-in-polygon, same algorithm as coastline)
- Climatology query returns mean and variance of current at a grid cell
- `make_onboard_map` produces a deliberately imperfect copy of the truth map with configurable fidelity reduction
- `climatology_from_field` derives PF priors consistent with the truth current field by construction
- All map data injected (loaded from files outside the class), not owned by the class

**Non-Goals:**
- GEBCO/ETOPO data loading from NetCDF (future M3 work)
- Over-the-air map update simulation (future M3)
- Real shipping lane data (AIS-derived) — synthetic polygon only for M1
- Variable-resolution bathymetry grids
- Per-node map variation (all nodes of the same class share one onboard map)
- Time-varying map updates (over-the-air map refresh deferred to M3)

## Decisions

### D1: Bathymetry as a regular lat/lon grid with bilinear interpolation

**Choice:** Store bathymetry as a 2D numpy array on a regular lat/lon grid. Bilinear interpolation for queries between grid points.

**Why:** Regular grid is the simplest structure. GEBCO data is already on a regular grid, so the interpolation logic carries forward. Bilinear is sufficient — the PF doesn't need sub-grid-cell accuracy since sensor noise exceeds grid resolution.

**Alternatives considered:**
- Nearest-neighbor: too jumpy for PF observations (discontinuities cause weight spikes)
- Triangulated irregular network: overkill for regular-grid source data

### D2: Synthetic bathymetry for M1 (not GEBCO extract)

**Choice:** Generate a synthetic bathymetry grid with a simple analytical profile (shelf slope, canyon, and flat deep area). Ship as a small numpy .npz file.

**Why:** Avoids depending on GEBCO data download during M1 development. The synthetic grid has known values that tests can verify exactly. The GEBCO loader in M3 will produce the same grid format, so the interpolation code doesn't change.

### D3: Shipping lanes as polygon list (same format as coastline)

**Choice:** Shipping lanes are a list of polygon arrays (same ndarray(N, 2) [lon, lat] format as coastline). Membership uses the same ray-casting PIP.

**Why:** Reuses the point-in-polygon code. Shipping lanes are static polygons — no special handling needed. If lane data becomes more complex in the future (probability fields, time-varying), the interface can be extended.

### D4: Climatology as a coarse grid of (mean_vx, mean_vy, var_vx, var_vy)

**Choice:** Store current climatology on a coarser grid than bathymetry (e.g., 10×10 cells). Each cell has mean velocity components and variance components.

**Why:** The synthetic current field's "climatology" is just the mean flow parameters, but the interface should work for real data where climatology comes from historical averages. A coarse grid is sufficient since climatology is a prior, not a precise measurement.

### D5: Map data loaded externally, injected into RegionalMap

**Choice:** `RegionalMap.__init__` takes pre-loaded numpy arrays and polygon lists. A separate `load_regional_map(data_dir)` function handles file I/O.

**Why:** Testability — tests construct maps with known data without touching the filesystem. The loading function is a thin wrapper around numpy.load and coastline.load_coastline_geojson.

### D6: Truth map vs onboard map — fidelity reduction

**Choice:** A `make_onboard_map(truth_map, fidelity, seed)` function produces a deliberately imperfect copy of the truth map. Fidelity reduction includes: bathymetry grid downsampling (2× or 4× coarser), missing canyon features, and simplified coastline. The seed ensures reproducibility.

**Why:** In the real system, nodes carry imperfect maps — lower resolution, potentially outdated, missing features. If the PF uses the same map as truth, the map-aided-nav test measures how well the PF reads its own handwriting. The fidelity reduction models the real gap between "what the seafloor actually is" and "what the node's stored data says."

**Alternatives considered:**
- Same map for truth and PF: dishonest — overstates map-aid benefit
- Random noise injection: unphysical — real map errors are spatially correlated
- Manual specification of both maps: too much configuration work, hard to keep consistent

### D7: Climatology derived from CurrentField by sampling

**Choice:** `climatology_from_field(field, bbox, grid_resolution_deg, duration_sec, seed)` samples the field at grid points over the specified duration to derive mean and variance.

**Why:** Ensures climatology is consistent with the truth field by construction. If the field has a mean flow of 0.1 m/s east, the derived climatology will show that. This prevents the PF from getting either a perfect prior (cheating) or an inconsistent prior (unfair handicap).

**Alternatives considered:**
- Manual climatology specification: error-prone, no consistency guarantee
- Analytical derivation from field parameters: only works for the synthetic field, not HYCOM

## Risks / Trade-offs

- **[Risk] Synthetic bathymetry is too simple** → The synthetic profile includes three features (shelf, canyon, deep) which exercises the interpolation and PF observation. Real GEBCO adds more detail but the code path is the same.
- **[Risk] Bilinear interpolation edge effects** → Grid boundary queries extrapolate (nearest-cell value). This is acceptable — the simulation bbox has a margin beyond the fleet operating area.
- **[Trade-off] Single bathymetry resolution for all node classes** → Anchors, shear-keepers, and drifters all get the same map. In the real system, deeper nodes might need higher-resolution bathymetry. Not needed for M1.

## Key Type Contracts

```
@dataclass
class BathymetryGrid:
    lats: ndarray          # 1D, shape (M,), degrees, ascending
    lons: ndarray          # 1D, shape (N,), degrees, ascending
    depths_m: ndarray      # 2D, shape (M, N), positive = water depth
    def at(self, lat_deg: float, lon_deg: float) -> float:
        """Bilinear interpolation of depth at (lat, lon). Returns depth in meters."""
        ...

@dataclass
class ClimatologyGrid:
    lats: ndarray          # 1D, shape (P,)
    lons: ndarray          # 1D, shape (Q,)
    mean_vx_ms: ndarray    # 2D, shape (P, Q)
    mean_vy_ms: ndarray    # 2D, shape (P, Q)
    var_vx_ms2: ndarray    # 2D, shape (P, Q)
    var_vy_ms2: ndarray    # 2D, shape (P, Q)
    def at(self, lat_deg: float, lon_deg: float) -> tuple[float, float, float, float]:
        """Returns (mean_vx, mean_vy, var_vx, var_vy) at nearest grid cell."""
        ...

@dataclass
class RegionalMap:
    bathymetry: BathymetryGrid
    land_polygons: list[ndarray]       # from coastline module
    shipping_lanes: list[ndarray]      # polygon arrays, same format
    climatology: ClimatologyGrid
    def is_on_land(self, lat_deg: float, lon_deg: float) -> bool:
        """Delegates to coastline.point_on_land."""
        ...
    def is_in_shipping_lane(self, lat_deg: float, lon_deg: float) -> bool:
        """PIP test against shipping lane polygons."""
        ...
    def depth_at(self, lat_deg: float, lon_deg: float) -> float:
        """Bathymetry interpolation. Returns NaN for land coordinates."""
        ...
    def current_climatology_at(self, lat_deg: float, lon_deg: float) -> tuple[float, float, float, float]:
        """Nearest-cell climatology lookup."""
        ...

def load_regional_map(data_dir: str) -> RegionalMap:
    """Load all map components from a directory of pre-built data files."""
    ...

def generate_synthetic_bathymetry(bbox: BBox, resolution_deg: float = 0.01) -> BathymetryGrid:
    """Create a synthetic bathymetry grid with shelf, canyon, and deep features."""
    ...

def make_onboard_map(truth_map: RegionalMap, fidelity: float = 0.5, seed: int = 42) -> RegionalMap:
    """Produce a deliberately imperfect copy of the truth map.
    fidelity: 1.0 = perfect copy, 0.0 = maximally degraded.
    Default 0.5 produces ~2× coarser bathymetry and simplified coastline."""
    ...

def climatology_from_field(
    field: CurrentField,
    bbox: BBox,
    grid_resolution_deg: float = 0.05,
    sample_duration_sec: float = 86400.0,
    seed: int = 42
) -> ClimatologyGrid:
    """Derive climatology by sampling the field over time.
    Ensures consistency between PF prior and truth field."""
    ...
```
