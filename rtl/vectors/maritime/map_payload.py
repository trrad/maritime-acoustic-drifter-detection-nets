import os

import numpy as np
from dataclasses import dataclass

from rtl.vectors.maritime import coastline
from rtl.vectors.maritime.current_fields import CurrentField


@dataclass
class BathymetryGrid:
    lats: np.ndarray
    lons: np.ndarray
    depths_m: np.ndarray

    def at(self, lat_deg: float, lon_deg: float) -> float:
        lat_in_bounds = self.lats[0] <= lat_deg <= self.lats[-1]
        lon_in_bounds = self.lons[0] <= lon_deg <= self.lons[-1]

        if lat_in_bounds and lon_in_bounds:
            lat_idx = np.searchsorted(self.lats, lat_deg) - 1
            lon_idx = np.searchsorted(self.lons, lon_deg) - 1
            lat_idx = max(0, min(lat_idx, len(self.lats) - 2))
            lon_idx = max(0, min(lon_idx, len(self.lons) - 2))

            lat0 = self.lats[lat_idx]
            lat1 = self.lats[lat_idx + 1]
            lon0 = self.lons[lon_idx]
            lon1 = self.lons[lon_idx + 1]

            z00 = self.depths_m[lat_idx, lon_idx]
            z01 = self.depths_m[lat_idx, lon_idx + 1]
            z10 = self.depths_m[lat_idx + 1, lon_idx]
            z11 = self.depths_m[lat_idx + 1, lon_idx + 1]

            if lat_deg == lat0 and lon_deg == lon0:
                return float(z00)
            if lat_deg == lat0 and lon_deg == lon1:
                return float(z01)
            if lat_deg == lat1 and lon_deg == lon0:
                return float(z10)
            if lat_deg == lat1 and lon_deg == lon1:
                return float(z11)

            t = (lat_deg - lat0) / (lat1 - lat0)
            s = (lon_deg - lon0) / (lon1 - lon0)

            z0 = z00 * (1 - t) + z10 * t
            z1 = z01 * (1 - t) + z11 * t
            z = z0 * (1 - s) + z1 * s

            return float(z)

        if not lat_in_bounds and lon_in_bounds:
            lon_idx = np.argmin(np.abs(self.lons - lon_deg))
            lat_idx = len(self.lats) - 1 if lat_deg > self.lats[-1] else 0
            return float(self.depths_m[lat_idx, lon_idx])

        if lat_in_bounds and not lon_in_bounds:
            lat_idx = np.argmin(np.abs(self.lats - lat_deg))
            lon_idx = len(self.lons) - 1 if lon_deg > self.lons[-1] else 0
            return float(self.depths_m[lat_idx, lon_idx])

        lat_idx = np.argmin(np.abs(self.lats - lat_deg))
        lon_idx = np.argmin(np.abs(self.lons - lon_deg))
        return float(self.depths_m[lat_idx, lon_idx])


@dataclass
class ClimatologyGrid:
    lats: np.ndarray
    lons: np.ndarray
    mean_vx_ms: np.ndarray
    mean_vy_ms: np.ndarray
    var_vx_ms2: np.ndarray
    var_vy_ms2: np.ndarray

    def __post_init__(self) -> None:
        if np.any(self.var_vx_ms2 < 0):
            raise ValueError("var_vx_ms2 must be non-negative")
        if np.any(self.var_vy_ms2 < 0):
            raise ValueError("var_vy_ms2 must be non-negative")

    def at(self, lat_deg: float, lon_deg: float) -> tuple[float, float, float, float]:
        lat_diffs = (self.lats - lat_deg) ** 2
        lon_diffs = (self.lons - lon_deg) ** 2
        total_diffs = lat_diffs[:, np.newaxis] + lon_diffs[np.newaxis, :]
        lat_idx, lon_idx = np.unravel_index(np.argmin(total_diffs), total_diffs.shape)

        return (
            float(self.mean_vx_ms[lat_idx, lon_idx]),
            float(self.mean_vy_ms[lat_idx, lon_idx]),
            float(self.var_vx_ms2[lat_idx, lon_idx]),
            float(self.var_vy_ms2[lat_idx, lon_idx]),
        )


@dataclass
class RegionalMap:
    bathymetry: BathymetryGrid
    land_polygons: list[np.ndarray]
    shipping_lanes: list[np.ndarray]
    climatology: ClimatologyGrid

    def is_on_land(self, lat_deg: float, lon_deg: float) -> bool:
        return coastline.point_on_land(lat_deg, lon_deg, self.land_polygons)

    def is_in_shipping_lane(self, lat_deg: float, lon_deg: float) -> bool:
        if not self.shipping_lanes:
            return False

        for lane in self.shipping_lanes:
            if coastline._point_in_polygon(lon_deg, lat_deg, lane):
                return True

        return False

    def depth_at(self, lat_deg: float, lon_deg: float) -> float:
        if self.is_on_land(lat_deg, lon_deg):
            return float('nan')
        return self.bathymetry.at(lat_deg, lon_deg)

    def current_climatology_at(self, lat_deg: float, lon_deg: float) -> tuple[float, float, float, float]:
        return self.climatology.at(lat_deg, lon_deg)

    def hardware_footprint_bytes(self) -> int:
        words = (
            self.bathymetry.depths_m.size
            + len(self.bathymetry.lats)
            + len(self.bathymetry.lons)
            + self.climatology.mean_vx_ms.size * 4
            + len(self.climatology.lats)
            + len(self.climatology.lons)
        )
        for poly in self.land_polygons:
            words += len(poly) * 2
        for lane in self.shipping_lanes:
            words += len(lane) * 2
        return words * 2


def generate_synthetic_bathymetry(bbox: tuple[float, float, float, float], resolution_deg: float = 0.01) -> BathymetryGrid:
    south, west, north, east = bbox
    lats = np.arange(south, north + resolution_deg, resolution_deg)
    lons = np.arange(west, east + resolution_deg, resolution_deg)
    depth_grid = np.zeros((len(lats), len(lons)))

    shelf_break_dist = 0.3
    slope_end_dist = 0.6

    canyon_lat = (south + north) / 2
    canyon_lon = west + (east - west) * 0.45
    canyon_width = 0.05
    canyon_depth_m = 300

    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            dist_from_coast = (lon - west) / (east - west)

            if dist_from_coast <= shelf_break_dist:
                depth = 100 + dist_from_coast / shelf_break_dist * 100
            elif dist_from_coast <= slope_end_dist:
                frac = (dist_from_coast - shelf_break_dist) / (slope_end_dist - shelf_break_dist)
                depth = 200 + frac * 800
            else:
                depth = 1000 + (dist_from_coast - slope_end_dist) / (1 - slope_end_dist) * 1000

            dist_to_canyon = np.sqrt(((lat - canyon_lat) / (north - south)) ** 2 + ((lon - canyon_lon) / (east - west)) ** 2)
            canyon_effect = canyon_depth_m * np.exp(-(dist_to_canyon / canyon_width) ** 2)
            depth = depth + canyon_effect

            depth_grid[i, j] = depth

    return BathymetryGrid(lats=lats, lons=lons, depths_m=depth_grid)


def load_regional_map(data_dir: str) -> RegionalMap:
    bathymetry_path = os.path.join(data_dir, 'bathymetry.npz')
    coastline_path = os.path.join(data_dir, 'coastline.geojson')
    shipping_lanes_path = os.path.join(data_dir, 'shipping_lanes.geojson')
    climatology_path = os.path.join(data_dir, 'climatology.npz')

    if not os.path.exists(bathymetry_path):
        raise FileNotFoundError(f"Bathymetry data file not found: {bathymetry_path}")

    if not os.path.exists(coastline_path):
        raise FileNotFoundError(f"Coastline data file not found: {coastline_path}")

    bathymetry_data = np.load(bathymetry_path)
    bathymetry = BathymetryGrid(
        lats=bathymetry_data['lats'],
        lons=bathymetry_data['lons'],
        depths_m=bathymetry_data['depths_m']
    )

    land_polygons = coastline.load_coastline_geojson(coastline_path)

    if os.path.exists(shipping_lanes_path):
        shipping_lanes = coastline.load_coastline_geojson(shipping_lanes_path)
    else:
        shipping_lanes = []

    if not os.path.exists(climatology_path):
        raise FileNotFoundError(f"Climatology data file not found: {climatology_path}")

    climatology_data = np.load(climatology_path)
    climatology = ClimatologyGrid(
        lats=climatology_data['lats'],
        lons=climatology_data['lons'],
        mean_vx_ms=climatology_data['mean_vx_ms'],
        mean_vy_ms=climatology_data['mean_vy_ms'],
        var_vx_ms2=climatology_data['var_vx_ms2'],
        var_vy_ms2=climatology_data['var_vy_ms2'],
    )

    return RegionalMap(
        bathymetry=bathymetry,
        land_polygons=land_polygons,
        shipping_lanes=shipping_lanes,
        climatology=climatology
    )


def make_onboard_map(truth_map: RegionalMap, fidelity: float = 0.5, seed: int = 42) -> RegionalMap:
    rng = np.random.default_rng(seed)

    keep_fraction = 0.05 + 0.5 * fidelity
    n_lats = len(truth_map.bathymetry.lats)
    n_lons = len(truth_map.bathymetry.lons)
    n_keep_lats = max(2, int(n_lats * keep_fraction))
    n_keep_lons = max(2, int(n_lons * keep_fraction))

    lat_indices = np.round(np.linspace(0, n_lats - 1, n_keep_lats)).astype(int)
    lon_indices = np.round(np.linspace(0, n_lons - 1, n_keep_lons)).astype(int)

    downsampled_lats = truth_map.bathymetry.lats[lat_indices].copy()
    downsampled_lons = truth_map.bathymetry.lons[lon_indices].copy()
    downsampled_depths = truth_map.bathymetry.depths_m[np.ix_(lat_indices, lon_indices)].copy()

    degraded_bathy = BathymetryGrid(
        lats=downsampled_lats,
        lons=downsampled_lons,
        depths_m=downsampled_depths,
    )

    simplified_polygons = []
    for polygon in truth_map.land_polygons:
        if len(polygon) <= 3:
            simplified_polygons.append(polygon.copy())
        else:
            n_keep_points = max(3, int(len(polygon) * keep_fraction))
            point_indices = np.round(np.linspace(0, len(polygon) - 1, n_keep_points)).astype(int)
            simplified_polygons.append(polygon[point_indices].copy())

    n_keep_lanes = max(1, int(len(truth_map.shipping_lanes) * keep_fraction))
    if len(truth_map.shipping_lanes) > 0:
        lane_indices = rng.choice(len(truth_map.shipping_lanes), size=n_keep_lanes, replace=False)
        simplified_lanes = [truth_map.shipping_lanes[i].copy() for i in lane_indices]
    else:
        simplified_lanes = []

    return RegionalMap(
        bathymetry=degraded_bathy,
        land_polygons=simplified_polygons,
        shipping_lanes=simplified_lanes,
        climatology=truth_map.climatology,
    )


def climatology_from_field(
    field: CurrentField,
    bbox: tuple[float, float, float, float],
    grid_resolution_deg: float = 0.05,
    sample_duration_sec: float = 86400.0,
    seed: int = 42
) -> ClimatologyGrid:
    south, west, north, east = bbox
    lats = np.arange(south, north + grid_resolution_deg, grid_resolution_deg)
    lons = np.arange(west, east + grid_resolution_deg, grid_resolution_deg)

    n_samples = 100
    t_samples = np.linspace(0, sample_duration_sec, n_samples)

    n_lats = len(lats)
    n_lons = len(lons)

    vx_samples = np.zeros((n_lats, n_lons, n_samples))
    vy_samples = np.zeros((n_lats, n_lons, n_samples))

    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            for k, t in enumerate(t_samples):
                vx, vy = field.velocity_at(lat, lon, t)
                vx_samples[i, j, k] = vx
                vy_samples[i, j, k] = vy

    mean_vx_ms = np.mean(vx_samples, axis=2)
    mean_vy_ms = np.mean(vy_samples, axis=2)
    var_vx_ms2 = np.var(vx_samples, axis=2)
    var_vy_ms2 = np.var(vy_samples, axis=2)

    return ClimatologyGrid(
        lats=lats,
        lons=lons,
        mean_vx_ms=mean_vx_ms,
        mean_vy_ms=mean_vy_ms,
        var_vx_ms2=var_vx_ms2,
        var_vy_ms2=var_vy_ms2
    )
