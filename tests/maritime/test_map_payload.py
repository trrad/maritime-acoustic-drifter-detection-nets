"""Contract tests for maritime map payload implementation.

These tests define the interface and expected behavior for the
rtl.vectors.maritime.map_payload module. The module does not exist yet —
these tests will fail with ImportError until implementation is complete.
"""

import math
import os

import numpy as np
import pytest

from rtl.vectors.maritime.map_payload import BathymetryGrid, ClimatologyGrid, RegionalMap, generate_synthetic_bathymetry, make_onboard_map, climatology_from_field
from rtl.vectors.maritime.coastline import point_on_land
from rtl.vectors.maritime.current_fields import EddySpec, FieldConfig, SyntheticEddyField


def test_bathymetry_grid_point_exact():
    lats = np.array([10.0, 11.0, 12.0])
    lons = np.array([20.0, 21.0, 22.0])
    depths_m = np.array([
        [100.0, 110.0, 120.0],
        [130.0, 140.0, 150.0],
        [160.0, 170.0, 180.0],
    ])
    grid = BathymetryGrid(lats=lats, lons=lons, depths_m=depths_m)

    assert grid.at(10.0, 20.0) == 100.0
    assert grid.at(10.0, 22.0) == 120.0
    assert grid.at(12.0, 20.0) == 160.0
    assert grid.at(12.0, 22.0) == 180.0


def test_bathymetry_bilinear_midpoint():
    lats = np.array([10.0, 11.0])
    lons = np.array([20.0, 21.0])
    depths_m = np.array([
        [100.0, 110.0],
        [120.0, 130.0],
    ])
    grid = BathymetryGrid(lats=lats, lons=lons, depths_m=depths_m)

    depth = grid.at(10.5, 20.5)
    assert depth == 115.0


def test_bathymetry_boundary_clamp():
    lats = np.array([10.0, 11.0, 12.0])
    lons = np.array([20.0, 21.0, 22.0])
    depths_m = np.array([
        [100.0, 110.0, 120.0],
        [130.0, 140.0, 150.0],
        [160.0, 170.0, 180.0],
    ])
    grid = BathymetryGrid(lats=lats, lons=lons, depths_m=depths_m)

    assert grid.at(12.1, 21.0) == 170.0
    assert grid.at(9.9, 21.0) == 110.0
    assert grid.at(11.0, 22.1) == 150.0
    assert grid.at(11.0, 19.9) == 130.0


def test_climatology_nearest_cell():
    lats = np.array([10.0, 11.0, 12.0])
    lons = np.array([20.0, 21.0, 22.0])
    mean_vx_ms = np.zeros((3, 3))
    mean_vy_ms = np.zeros((3, 3))
    var_vx_ms2 = np.ones((3, 3)) * 0.01
    var_vy_ms2 = np.ones((3, 3)) * 0.01

    mean_vx_ms[1, 1] = 0.1
    mean_vy_ms[1, 1] = -0.05

    grid = ClimatologyGrid(
        lats=lats,
        lons=lons,
        mean_vx_ms=mean_vx_ms,
        mean_vy_ms=mean_vy_ms,
        var_vx_ms2=var_vx_ms2,
        var_vy_ms2=var_vy_ms2,
    )

    mean_vx, mean_vy, var_vx, var_vy = grid.at(11.0, 21.0)
    assert mean_vx == 0.1
    assert mean_vy == -0.05


def test_climatology_negative_variance_raises():
    lats = np.array([10.0, 11.0])
    lons = np.array([20.0, 21.0])
    mean_vx_ms = np.zeros((2, 2))
    mean_vy_ms = np.zeros((2, 2))
    var_vx_ms2 = np.array([[0.01, -0.01], [0.01, 0.01]])
    var_vy_ms2 = np.ones((2, 2)) * 0.01

    with pytest.raises(ValueError):
        ClimatologyGrid(
            lats=lats,
            lons=lons,
            mean_vx_ms=mean_vx_ms,
            mean_vy_ms=mean_vy_ms,
            var_vx_ms2=var_vx_ms2,
            var_vy_ms2=var_vy_ms2,
        )


def test_is_on_land_delegates():
    polygon = np.array([
        [-123.4, 48.4],
        [-123.3, 48.4],
        [-123.3, 48.5],
        [-123.4, 48.5],
    ])

    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[polygon],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.is_on_land(48.45, -123.35) is True
    assert regional_map.is_on_land(48.6, -123.35) is False
    assert regional_map.is_on_land(48.45, -123.35) == point_on_land(48.45, -123.35, [polygon])


def test_is_on_land_empty_polygons():
    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.is_on_land(48.5, -123.5) is False
    assert regional_map.is_on_land(48.6, -123.35) is False


def test_is_in_shipping_lane_inside():
    lane = np.array([
        [-124.0, 48.0],
        [-123.0, 48.0],
        [-123.0, 49.0],
        [-124.0, 49.0],
    ])

    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[lane],
        climatology=climatology,
    )

    assert regional_map.is_in_shipping_lane(48.5, -123.5) is True
    assert regional_map.is_in_shipping_lane(47.9, -123.5) is False


def test_is_in_shipping_lane_empty():
    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.is_in_shipping_lane(48.5, -123.5) is False


def test_depth_at_delegates():
    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.depth_at(48.0, -124.0) == bathy.at(48.0, -124.0)
    assert regional_map.depth_at(48.5, -123.5) == bathy.at(48.5, -123.5)


def test_current_climatology_at_delegates():
    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.array([[0.1, 0.2], [0.3, 0.4]]),
        mean_vy_ms=np.array([[0.5, 0.6], [0.7, 0.8]]),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.02,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.current_climatology_at(48.0, -124.0) == climatology.at(48.0, -124.0)
    assert regional_map.current_climatology_at(48.5, -123.5) == climatology.at(48.5, -123.5)


def test_regional_map_no_io():
    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 200.0], [150.0, 300.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    assert regional_map.depth_at(48.5, -123.5) is not None
    assert isinstance(regional_map.is_on_land(48.5, -123.5), bool)
    assert isinstance(regional_map.is_in_shipping_lane(48.5, -123.5), bool)
    assert regional_map.current_climatology_at(48.5, -123.5) is not None


def test_synthetic_bathymetry_all_positive():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)
    assert np.all(grid.depths_m > 0)
    assert len(grid.lats) > 0
    assert len(grid.lons) > 0


def test_synthetic_bathymetry_shelf_and_deep():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)
    depth_near_coast = grid.at(48.5, -123.6)
    depth_offshore = grid.at(48.9, -123.4)
    assert depth_near_coast < 500
    assert depth_offshore > 500


def test_synthetic_bathymetry_grid_spacing():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)
    lat_diffs = np.diff(grid.lats)
    lon_diffs = np.diff(grid.lons)
    assert np.all(np.abs(lat_diffs - 0.01) < 0.001)
    assert np.all(np.abs(lon_diffs - 0.01) < 0.001)


def test_depth_at_land_returns_nan():
    polygon = np.array([
        [-123.4, 48.4],
        [-123.3, 48.4],
        [-123.3, 48.5],
        [-123.4, 48.5],
    ])

    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 100.0], [100.0, 100.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[polygon],
        shipping_lanes=[],
        climatology=climatology,
    )

    result = regional_map.depth_at(48.45, -123.35)
    assert math.isnan(result)


def test_depth_at_water_returns_finite():
    polygon = np.array([
        [-123.4, 48.4],
        [-123.3, 48.4],
        [-123.3, 48.5],
        [-123.4, 48.5],
    ])

    bathy = BathymetryGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        depths_m=np.array([[100.0, 100.0], [100.0, 100.0]]),
    )

    climatology = ClimatologyGrid(
        lats=np.array([48.0, 49.0]),
        lons=np.array([-124.0, -123.0]),
        mean_vx_ms=np.zeros((2, 2)),
        mean_vy_ms=np.zeros((2, 2)),
        var_vx_ms2=np.ones((2, 2)) * 0.01,
        var_vy_ms2=np.ones((2, 2)) * 0.01,
    )

    regional_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[polygon],
        shipping_lanes=[],
        climatology=climatology,
    )

    result = regional_map.depth_at(48.6, -123.35)
    assert math.isfinite(result)
    assert result > 0


def test_onboard_map_degraded_structure():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    bathy_grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)

    climatology = ClimatologyGrid(
        lats=bathy_grid.lats,
        lons=bathy_grid.lons,
        mean_vx_ms=np.zeros_like(bathy_grid.depths_m),
        mean_vy_ms=np.zeros_like(bathy_grid.depths_m),
        var_vx_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
        var_vy_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
    )

    truth_map = RegionalMap(
        bathymetry=bathy_grid,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    onboard_map = make_onboard_map(truth_map, fidelity=0.5, seed=42)

    assert onboard_map.bathymetry.depths_m.size < truth_map.bathymetry.depths_m.size

    differs = False
    for lat in np.linspace(48.5, 49.0, 10):
        for lon in np.linspace(-123.7, -123.3, 10):
            t = truth_map.depth_at(lat, lon)
            o = onboard_map.depth_at(lat, lon)
            if math.isfinite(t) and math.isfinite(o) and t != o:
                differs = True
                break
        if differs:
            break
    assert differs

    assert onboard_map.hardware_footprint_bytes() < truth_map.hardware_footprint_bytes()


def test_onboard_map_reproducible():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    bathy_grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)

    climatology = ClimatologyGrid(
        lats=bathy_grid.lats,
        lons=bathy_grid.lons,
        mean_vx_ms=np.zeros_like(bathy_grid.depths_m),
        mean_vy_ms=np.zeros_like(bathy_grid.depths_m),
        var_vx_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
        var_vy_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
    )

    truth_map = RegionalMap(
        bathymetry=bathy_grid,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    map1 = make_onboard_map(truth_map, fidelity=0.5, seed=42)
    map2 = make_onboard_map(truth_map, fidelity=0.5, seed=42)

    assert np.array_equal(map1.bathymetry.depths_m, map2.bathymetry.depths_m)


def test_onboard_coastline_differs():
    BC_BBOX = (48.4, -123.8, 49.2, -123.2)
    bathy_grid = generate_synthetic_bathymetry(BC_BBOX, resolution_deg=0.01)

    from rtl.vectors.maritime.coastline import load_coastline_geojson
    data_path = os.path.join(
        os.path.dirname(__file__), '..', '..', 'rtl', 'vectors', 'maritime', 'data', 'bc_coast_sample.geojson'
    )
    truth_land_polygons = load_coastline_geojson(data_path)

    climatology = ClimatologyGrid(
        lats=bathy_grid.lats,
        lons=bathy_grid.lons,
        mean_vx_ms=np.zeros_like(bathy_grid.depths_m),
        mean_vy_ms=np.zeros_like(bathy_grid.depths_m),
        var_vx_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
        var_vy_ms2=np.ones_like(bathy_grid.depths_m) * 0.01,
    )

    truth_map = RegionalMap(
        bathymetry=bathy_grid,
        land_polygons=truth_land_polygons,
        shipping_lanes=[],
        climatology=climatology,
    )

    onboard_map = make_onboard_map(truth_map, fidelity=0.5, seed=42)

    polygons_differ = (
        len(truth_map.land_polygons) != len(onboard_map.land_polygons) or
        any(len(tp) != len(op) for tp, op in zip(truth_map.land_polygons, onboard_map.land_polygons))
    )
    assert polygons_differ

    point_found = False
    for lat in np.linspace(48.5, 48.7, 20):
        for lon in np.linspace(-123.7, -123.3, 20):
            truth_on_land = truth_map.is_on_land(lat, lon)
            onboard_on_land = onboard_map.is_on_land(lat, lon)
            if truth_on_land != onboard_on_land:
                point_found = True
                break
        if point_found:
            break

    assert point_found


def test_climatology_mean_matches_field():
    config = FieldConfig(mean_vx_ms=0.1, mean_vy_ms=-0.05, tidal_amplitude_ms=0.0, eddies=[])
    field = SyntheticEddyField(config)
    climatology = climatology_from_field(field, bbox=(48.4, -123.8, 49.2, -123.2), grid_resolution_deg=0.1, sample_duration_sec=86400.0, seed=42)
    
    for i in range(len(climatology.lats)):
        for j in range(len(climatology.lons)):
            assert abs(climatology.mean_vx_ms[i, j] - 0.1) < 0.01
            assert abs(climatology.mean_vy_ms[i, j] - (-0.05)) < 0.01


def test_climatology_variance_non_negative():
    eddy = EddySpec(center_lat_deg=48.8, center_lon_deg=-123.5, radius_m=10000.0, peak_velocity_ms=0.2, cyclonic=True)
    
    config = FieldConfig(mean_vx_ms=0.0, mean_vy_ms=0.0, tidal_amplitude_ms=0.0, eddies=[eddy])
    field = SyntheticEddyField(config)
    climatology = climatology_from_field(field, bbox=(48.4, -123.8, 49.2, -123.2), grid_resolution_deg=0.1, sample_duration_sec=86400.0, seed=42)
    
    assert np.all(climatology.var_vx_ms2 >= 0)
    assert np.all(climatology.var_vy_ms2 >= 0)
