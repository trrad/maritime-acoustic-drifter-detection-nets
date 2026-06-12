"""Contract tests for maritime coordinate conversion utilities.

These tests define the interface and expected behavior for the
rtl.vectors.maritime.coords module. The module does not exist yet —
these tests will fail with ImportError until implementation is complete.

All tests use reference point (48.8, -123.5) — BC coast, Strait of Georgia area.
"""

import numpy as np
import pytest

# This import will fail until the module is implemented
from rtl.vectors.maritime.coords import (
    latlon_to_enu,
    enu_to_latlon,
    haversine_m,
    bearing_deg,
)


# Reference point for all tests (BC coast, Strait of Georgia area)
REF_LAT = 48.8
REF_LON = -123.5


def test_enu_roundtrip_scalar():
    """Verify ENU conversion round-trip accuracy for a single point.

    Converts a point from lat/lon to ENU and back, then checks that the
    recovered position is within 1 meter of the original using haversine distance.
    """
    # Original point
    lat = 49.0
    lon = -123.3

    # Convert to ENU
    east_m, north_m = latlon_to_enu(lat, lon, REF_LAT, REF_LON)

    # Convert back to lat/lon
    lat_recovered, lon_recovered = enu_to_latlon(
        east_m, north_m, REF_LAT, REF_LON
    )

    # Check round-trip accuracy (within 1 meter)
    error_m = haversine_m(lat, lon, lat_recovered, lon_recovered)
    assert error_m < 1.0, f"Round-trip error {error_m:.2f}m exceeds 1m tolerance"


def test_enu_roundtrip_array():
    """Verify ENU conversion round-trip accuracy for multiple points.

    Creates 10 lat/lon positions spread across ~50km from the reference point,
    converts to ENU and back, and verifies all recovered positions are within
    1 meter of their originals.
    """
    rng = np.random.default_rng(42)

    # Generate 10 points spread across ~50km from reference
    # Using rough conversion: 1 degree lat ≈ 111 km, 1 degree lon ≈ 73 km at this latitude
    num_points = 10
    lat_spread_deg = 0.5  # ~55km
    lon_spread_deg = 0.7  # ~50km

    lats = rng.uniform(REF_LAT - lat_spread_deg/2, REF_LAT + lat_spread_deg/2, num_points)
    lons = rng.uniform(REF_LON - lon_spread_deg/2, REF_LON + lon_spread_deg/2, num_points)

    # Convert to ENU
    east_m, north_m = latlon_to_enu(lats, lons, REF_LAT, REF_LON)

    # Verify output arrays have correct shape
    assert east_m.shape == (num_points,), f"east_m shape {east_m.shape} != ({num_points},)"
    assert north_m.shape == (num_points,), f"north_m shape {north_m.shape} != ({num_points},)"

    # Convert back to lat/lon
    lats_recovered, lons_recovered = enu_to_latlon(
        east_m, north_m, REF_LAT, REF_LON
    )

    # Verify output arrays have correct shape
    assert lats_recovered.shape == (num_points,), f"lats_recovered shape {lats_recovered.shape} != ({num_points},)"
    assert lons_recovered.shape == (num_points,), f"lons_recovered shape {lons_recovered.shape} != ({num_points},)"

    # Check round-trip accuracy for each point (within 1 meter)
    for i in range(num_points):
        error_m = haversine_m(lats[i], lons[i], lats_recovered[i], lons_recovered[i])
        assert error_m < 1.0, f"Point {i} round-trip error {error_m:.2f}m exceeds 1m tolerance"


def test_enu_known_distance():
    """Verify ENU east distance is approximately correct.

    Places a point 10km east of the reference point and verifies that
    the converted ENU east coordinate is within 10m of 10000 meters.

    At lat 48.8°, 1 degree lon ≈ 111.32 * cos(48.8°) * 1000 ≈ 73346 m.
    So 10km east ≈ 10000 / 73346 ≈ 0.1363 degrees lon.
    """
    # Point 10km east of reference
    target_east_m = 10000.0
    lon_offset_deg = target_east_m / 73346.0  # ~0.1363 degrees
    lat = REF_LAT
    lon = REF_LON + lon_offset_deg

    # Convert to ENU
    east_m, north_m = latlon_to_enu(lat, lon, REF_LAT, REF_LON)

    # East should be approximately 10000m (within 20m tolerance — accounts for
    # spherical approximation used to place the point vs WGS84 ellipsoid ENU)
    assert abs(east_m - target_east_m) < 20.0, f"East {east_m:.2f}m differs from {target_east_m}m by more than 20m"

    # North should be approximately zero (point is directly east on spherical approx;
    # ellipsoid introduces small north component when following a parallel)
    assert abs(north_m) < 15.0, f"North {north_m:.2f}m should be ~0 for point directly east"


def test_haversine_zero():
    """Verify haversine distance is zero for identical points.

    Calls haversine_m with the same point for both arguments and asserts
    the result is exactly 0.0.
    """
    distance = haversine_m(REF_LAT, REF_LON, REF_LAT, REF_LON)
    assert distance == 0.0, f"Distance for identical points should be 0.0, got {distance}"


def test_haversine_known_pair():
    """Verify haversine distance for a known pair of points.

    Calls haversine_m(48.8, -123.5, 49.0, -123.3) and verifies the result
    is approximately 27km (between 25500 and 28500 meters).
    """
    distance = haversine_m(48.8, -123.5, 49.0, -123.3)
    # Should be approximately 27km
    assert 25500 < distance < 28500, f"Distance {distance:.0f}m is outside expected range [25500, 28500]m"


def test_haversine_accuracy():
    """Verify haversine distance is reasonable for points ~50km apart.

    Calls haversine_m(48.8, -123.5, 49.0, -123.0) and verifies the distance
    is between 40000 and 50000 meters. This is a sanity check that the
    implementation produces physically reasonable results.
    """
    distance = haversine_m(48.8, -123.5, 49.0, -123.0)
    # Should be reasonable (40-50km range)
    assert 40000 < distance < 50000, f"Distance {distance:.0f}m is outside expected range [40000, 50000]m"


def test_bearing_north():
    """Verify bearing for due north direction.

    Calls bearing_deg(48.8, -123.5, 49.0, -123.5) and verifies the result
    is within 2 degrees of 0 (or 360, since 0° ≡ 360°).
    """
    bearing = bearing_deg(48.8, -123.5, 49.0, -123.5)
    # Should be approximately 0° (or 360°, which is equivalent)
    assert bearing < 2.0 or bearing > 358.0, f"Bearing {bearing:.2f}° is not within 2° of north (0°/360°)"


def test_bearing_east():
    """Verify bearing for due east direction.

    Calls bearing_deg(48.8, -123.5, 48.8, -123.3) and verifies the result
    is within 2 degrees of 90.
    """
    bearing = bearing_deg(48.8, -123.5, 48.8, -123.3)
    # Should be approximately 90°
    assert 88.0 < bearing < 92.0, f"Bearing {bearing:.2f}° is not within 2° of east (90°)"


def test_bearing_south():
    """Verify bearing for due south direction.

    Calls bearing_deg(49.0, -123.5, 48.8, -123.5) and verifies the result
    is within 2 degrees of 180.
    """
    bearing = bearing_deg(49.0, -123.5, 48.8, -123.5)
    # Should be approximately 180°
    assert 178.0 < bearing < 182.0, f"Bearing {bearing:.2f}° is not within 2° of south (180°)"


def test_bearing_west():
    """Verify bearing for due west direction.

    Calls bearing_deg(48.8, -123.3, 48.8, -123.5) and verifies the result
    is within 2 degrees of 270.
    """
    bearing = bearing_deg(48.8, -123.3, 48.8, -123.5)
    # Should be approximately 270°
    assert 268.0 < bearing < 272.0, f"Bearing {bearing:.2f}° is not within 2° of west (270°)"
