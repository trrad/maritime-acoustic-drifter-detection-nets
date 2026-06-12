"""Coastline loading and spatial operations.

Provides functions for loading GeoJSON coastline data, clipping to bounding boxes,
and testing if points are on land using ray-casting algorithm.
"""

import json
from typing import Any

import numpy as np


def load_coastline_geojson(path: str) -> list[np.ndarray]:
    """Load coastline polygons from a GeoJSON file.

    Reads a GeoJSON file containing land polygons and extracts them as
    numpy arrays. Handles both Polygon and MultiPolygon geometry types.

    Args:
        path: Path to the GeoJSON file.

    Returns:
        List of polygon arrays, each with shape (N, 2) where columns are
        [lon_deg, lat_deg].

    Raises:
        FileNotFoundError: If the specified path does not exist.
    """
    # Check if file exists
    try:
        with open(path, 'r') as f:
            geojson = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Coastline GeoJSON file not found: {path}")

    polygons: list[np.ndarray] = []

    # Extract features from FeatureCollection
    features = geojson.get('features', [])

    for feature in features:
        geometry = feature.get('geometry', {})
        geom_type = geometry.get('type')
        coordinates = geometry.get('coordinates', [])

        if geom_type == 'Polygon':
            # Extract outer ring (first element of coordinates)
            outer_ring = coordinates[0]
            poly_array = np.array(outer_ring, dtype=float)
            polygons.append(poly_array)
        elif geom_type == 'MultiPolygon':
            # Extract outer ring of each sub-polygon as separate array
            for sub_polygon in coordinates:
                outer_ring = sub_polygon[0]
                poly_array = np.array(outer_ring, dtype=float)
                polygons.append(poly_array)

    return polygons


def clip_coastline_bbox(
    polygons: list[np.ndarray],
    south: float,
    west: float,
    north: float,
    east: float
) -> list[np.ndarray]:
    """Filter coastline polygons to only those intersecting a bounding box.

    A polygon intersects the bbox if ANY of its vertices fall within the
    bbox boundaries (inclusive).

    Args:
        polygons: List of polygon arrays, each with shape (N, 2) as [lon, lat].
        south: Southern latitude boundary of bbox.
        west: Western longitude boundary of bbox.
        north: Northern latitude boundary of bbox.
        east: Eastern longitude boundary of bbox.

    Returns:
        List of polygon arrays that intersect the bbox.
    """
    result: list[np.ndarray] = []

    for poly in polygons:
        # Check if any vertex falls within the bbox
        # Polygons are [lon, lat], so column 0 is lon, column 1 is lat
        lons = poly[:, 0]
        lats = poly[:, 1]

        # Check if any point is within bbox boundaries (inclusive)
        in_bbox = (
            (lats >= south) & (lats <= north) &
            (lons >= west) & (lons <= east)
        )

        if np.any(in_bbox):
            result.append(poly)

    return result


def point_on_land(
    lat_deg: float,
    lon_deg: float,
    polygons: list[np.ndarray]
) -> bool:
    """Test if a geographic point is on land using ray-casting.

    Uses the ray-casting (even-odd) algorithm to determine if a point is
    inside any polygon. Casts a horizontal ray from the test point to the
    right (+x direction) and counts intersections with polygon edges.

    NOTE: Polygon coordinates are [lon, lat] but function parameters are
    (lat_deg, lon_deg). The test_lon maps to polygon[:,0] and test_lat
    maps to polygon[:,1].

    Args:
        lat_deg: Latitude of test point in degrees.
        lon_deg: Longitude of test point in degrees.
        polygons: List of polygon arrays, each with shape (N, 2) as [lon, lat].

    Returns:
        True if the point is on land (inside any polygon), False otherwise.
    """
    # Handle empty polygon list
    if not polygons:
        return False

    # Iterate through all polygons, return True on first hit
    for poly in polygons:
        if _point_in_polygon(lon_deg, lat_deg, poly):
            return True

    return False


def _point_in_polygon(x: float, y: float, polygon: np.ndarray) -> bool:
    """Test if a point is inside a polygon using ray-casting.

    Args:
        x: X-coordinate of test point (longitude for our use case).
        y: Y-coordinate of test point (latitude for our use case).
        polygon: Polygon array with shape (N, 2) as [x, y].

    Returns:
        True if the point is inside the polygon, False otherwise.
    """
    n = len(polygon)
    inside = False

    # Ray-casting algorithm
    for i in range(n):
        j = (i - 1) % n

        # Get vertex coordinates
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        # Check if edge straddles the horizontal line at y
        # Edge goes from (xi, yi) to (xj, yj)
        # We count intersections where the ray passes from one side to the other
        if ((yi > y) != (yj > y)):
            # Calculate x-coordinate of intersection
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi

            # If intersection is to the right of the point, count it
            if x_intersect > x:
                inside = not inside

    return inside
