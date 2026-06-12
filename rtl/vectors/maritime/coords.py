"""Coordinate conversion functions for maritime navigation.

Converts between geographic coordinates (lat/lon in degrees) and local
East-North-Up metric offsets (east/north in meters) using WGS84 ellipsoid.

Also provides great-circle distance and bearing calculations.
"""

import numpy as np

# WGS84 ellipsoid parameters
WGS84_A = 6378137.0  # Semi-major axis (meters)
WGS84_F = 1 / 298.257223563  # Flattening
WGS84_B = WGS84_A * (1 - WGS84_F)  # Semi-minor axis (meters)
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2  # Eccentricity squared

# Mean Earth radius for haversine (meters)
MEAN_EARTH_RADIUS = 6371000.0


def latlon_to_enu(
    lat_deg: float | np.ndarray,
    lon_deg: float | np.ndarray,
    ref_lat_deg: float,
    ref_lon_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert geographic coordinates to local ENU offsets.

    Args:
        lat_deg: Latitude in degrees (scalar or array)
        lon_deg: Longitude in degrees (scalar or array)
        ref_lat_deg: Reference point latitude in degrees
        ref_lon_deg: Reference point longitude in degrees

    Returns:
        Tuple of (east_m, north_m) offsets from reference point in meters.
        Always returns numpy arrays, even for scalar inputs.
    """
    # Convert to radians
    lat = np.asarray(lat_deg) * np.pi / 180.0
    lon = np.asarray(lon_deg) * np.pi / 180.0
    ref_lat = ref_lat_deg * np.pi / 180.0
    ref_lon = ref_lon_deg * np.pi / 180.0

    # Compute prime vertical radius of curvature at reference latitude
    N = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(ref_lat) ** 2)

    # Convert reference point to ECEF
    ref_x = (N) * np.cos(ref_lat) * np.cos(ref_lon)
    ref_y = (N) * np.cos(ref_lat) * np.sin(ref_lon)
    ref_z = (N * (1 - WGS84_E2)) * np.sin(ref_lat)

    # Compute N at each point for ECEF conversion
    N_points = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)

    # Convert points to ECEF
    x = (N_points) * np.cos(lat) * np.cos(lon)
    y = (N_points) * np.cos(lat) * np.sin(lon)
    z = (N_points * (1 - WGS84_E2)) * np.sin(lat)

    # Compute differences
    dx = x - ref_x
    dy = y - ref_y
    dz = z - ref_z

    # Rotation matrix from ECEF to local ENU at reference point
    sin_lat = np.sin(ref_lat)
    cos_lat = np.cos(ref_lat)
    sin_lon = np.sin(ref_lon)
    cos_lon = np.cos(ref_lon)

    # ENU coordinates
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz

    return east, north


def enu_to_latlon(
    east_m: float | np.ndarray,
    north_m: float | np.ndarray,
    ref_lat_deg: float,
    ref_lon_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert local ENU offsets to geographic coordinates.

    Args:
        east_m: East offset in meters from reference point (scalar or array)
        north_m: North offset in meters from reference point (scalar or array)
        ref_lat_deg: Reference point latitude in degrees
        ref_lon_deg: Reference point longitude in degrees

    Returns:
        Tuple of (lat_deg, lon_deg) in degrees.
        Always returns numpy arrays, even for scalar inputs.
    """
    # Convert to numpy arrays
    east = np.asarray(east_m)
    north = np.asarray(north_m)

    # Reference point in radians
    ref_lat = ref_lat_deg * np.pi / 180.0
    ref_lon = ref_lon_deg * np.pi / 180.0

    # Compute prime vertical radius of curvature at reference latitude
    N = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(ref_lat) ** 2)

    # Convert reference point to ECEF
    ref_x = (N) * np.cos(ref_lat) * np.cos(ref_lon)
    ref_y = (N) * np.cos(ref_lat) * np.sin(ref_lon)
    ref_z = (N * (1 - WGS84_E2)) * np.sin(ref_lat)

    # Rotation matrix from ENU to ECEF at reference point
    sin_lat = np.sin(ref_lat)
    cos_lat = np.cos(ref_lat)
    sin_lon = np.sin(ref_lon)
    cos_lon = np.cos(ref_lon)

    # Convert ENU to ECEF differences
    dx = -sin_lon * east - sin_lat * cos_lon * north
    dy = cos_lon * east - sin_lat * sin_lon * north
    dz = cos_lat * north

    # ECEF coordinates of the point
    x = ref_x + dx
    y = ref_y + dy
    z = ref_z + dz

    # Convert ECEF to geodetic (iterative method for latitude)
    # Start with initial guess
    p = np.sqrt(x**2 + y**2)
    lat = np.arctan2(z, p * (1 - WGS84_E2))

    # Iterate to converge on latitude
    for _ in range(5):
        N_iter = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
        alt = p / np.cos(lat) - N_iter
        lat = np.arctan2(z, p * (1 - WGS84_E2 * N_iter / (N_iter + alt)))

    # Longitude is straightforward
    lon = np.arctan2(y, x)

    # Convert to degrees
    lat_deg = lat * 180.0 / np.pi
    lon_deg = lon * 180.0 / np.pi

    return lat_deg, lon_deg


def haversine_m(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> float:
    """Compute great-circle distance between two points using haversine formula.

    Args:
        lat1_deg: Latitude of first point in degrees
        lon1_deg: Longitude of first point in degrees
        lat2_deg: Latitude of second point in degrees
        lon2_deg: Longitude of second point in degrees

    Returns:
        Distance in meters.
    """
    # Convert to radians
    lat1 = lat1_deg * np.pi / 180.0
    lon1 = lon1_deg * np.pi / 180.0
    lat2 = lat2_deg * np.pi / 180.0
    lon2 = lon2_deg * np.pi / 180.0

    # Differences
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    # Haversine formula
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )
    c = 2 * np.arcsin(np.sqrt(a))

    return MEAN_EARTH_RADIUS * c


def bearing_deg(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> float:
    """Compute initial bearing from point 1 to point 2.

    Args:
        lat1_deg: Latitude of first point in degrees
        lon1_deg: Longitude of first point in degrees
        lat2_deg: Latitude of second point in degrees
        lon2_deg: Longitude of second point in degrees

    Returns:
        Bearing in degrees in the range [0, 360).
    """
    # Convert to radians
    lat1 = lat1_deg * np.pi / 180.0
    lon1 = lon1_deg * np.pi / 180.0
    lat2 = lat2_deg * np.pi / 180.0
    lon2 = lon2_deg * np.pi / 180.0

    # Forward azimuth formula
    dlon = lon2 - lon1
    y = np.sin(dlon) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    bearing_rad = np.arctan2(y, x)

    # Convert to degrees and normalize to [0, 360)
    bearing = (bearing_rad * 180.0 / np.pi) % 360.0

    return bearing
