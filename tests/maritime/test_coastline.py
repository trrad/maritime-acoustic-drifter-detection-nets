"""Contract tests for coastline module.

Tests for load_coastline_geojson, clip_coastline_bbox, and point_on_land functions.
"""

import os
from typing import Any

import numpy as np
import pytest

# Test data constants
DATA_PATH = os.path.join(
    os.path.dirname(__file__),
    '..',
    '..',
    'rtl',
    'vectors',
    'maritime',
    'data',
    'bc_coast_sample.geojson'
)
BC_BBOX = (48.4, -123.8, 49.2, -123.2)  # (south, west, north, east)
VICTORIA_LAT, VICTORIA_LON = 48.43, -123.37  # On land (Vancouver Island)
OFFSHORE_LAT, OFFSHORE_LON = 49.0, -123.5   # In the Strait of Georgia, offshore


# Module-level fixture to load data once per test session
@pytest.fixture(scope='session')
def sample_polygons() -> list[np.ndarray]:
    """Load sample coastline polygons once per test session."""
    from rtl.vectors.maritime.coastline import load_coastline_geojson
    return load_coastline_geojson(DATA_PATH)


class TestLoadCoastlineGeojson:
    """Tests for load_coastline_geojson function."""

    def test_load_coastline_geojson_valid(self, sample_polygons: list[np.ndarray]) -> None:
        """Load valid GeoJSON file and verify structure.

        Assert result is a non-empty list.
        Assert each element is an ndarray with shape (N, 2) where N >= 3.
        Assert columns are [lon, lat].
        """
        # Assert result is a non-empty list
        assert isinstance(sample_polygons, list)
        assert len(sample_polygons) > 0

        # Assert each element is an ndarray with shape (N, 2) where N >= 3
        for poly in sample_polygons:
            assert isinstance(poly, np.ndarray)
            assert poly.ndim == 2
            assert poly.shape[0] >= 3, f"Polygon has {poly.shape[0]} vertices, need at least 3"
            assert poly.shape[1] == 2, f"Polygon has {poly.shape[1]} columns, need 2"

            # Assert columns are [lon, lat] - check reasonable ranges
            lons = poly[:, 0]
            lats = poly[:, 1]
            # Longitude should be around -124 to -122 for BC coast
            assert np.all(lons >= -125) and np.all(lons <= -121), \
                f"Longitude values out of BC range: {lons.min()} to {lons.max()}"
            # Latitude should be around 48 to 50 for BC coast
            assert np.all(lats >= 47) and np.all(lats <= 51), \
                f"Latitude values out of BC range: {lats.min()} to {lats.max()}"

    def test_load_coastline_geojson_missing(self) -> None:
        """Call load_coastline_geojson with a non-existent path.

        Assert raises FileNotFoundError with message containing the path.
        """
        from rtl.vectors.maritime.coastline import load_coastline_geojson
        non_existent_path = '/non/existent/path/file.geojson'

        with pytest.raises(FileNotFoundError) as exc_info:
            load_coastline_geojson(non_existent_path)

        assert non_existent_path in str(exc_info.value)


class TestClipCoastlineBbox:
    """Tests for clip_coastline_bbox function."""

    def test_clip_coastline_bbox_filters(self, sample_polygons: list[np.ndarray]) -> None:
        """Clip to bbox and verify filtering behavior.

        Assert result has fewer polygons than input.
        Assert result is non-empty (BC coast has land).
        Then clip to an ocean bbox like (0, -160, 1, -159).
        Assert result is empty.
        """
        from rtl.vectors.maritime.coastline import clip_coastline_bbox

        # Use a smaller bbox around Victoria to demonstrate filtering
        # The full BC_BBOX covers all 866 polygons, so we use a tighter bbox
        victoria_bbox = (48.35, -123.6, 48.55, -123.2)  # (south, west, north, east)
        south, west, north, east = victoria_bbox
        clipped_victoria = clip_coastline_bbox(sample_polygons, south, west, north, east)

        # Assert result has fewer polygons than input
        assert len(clipped_victoria) < len(sample_polygons)

        # Assert result is non-empty (Victoria area has land)
        assert len(clipped_victoria) > 0

        # Clip to an ocean bbox (far from BC)
        ocean_bbox = (0, -160, 1, -159)  # (south, west, north, east)
        clipped_ocean = clip_coastline_bbox(sample_polygons, ocean_bbox[0], ocean_bbox[1],
                                            ocean_bbox[2], ocean_bbox[3])

        # Assert result is empty
        assert len(clipped_ocean) == 0


class TestPointOnLand:
    """Tests for point_on_land function."""

    def test_point_on_land_victoria(self, sample_polygons: list[np.ndarray]) -> None:
        """Test a point known to be on land (Victoria, BC).

        Assert True for Victoria point on Vancouver Island.
        """
        from rtl.vectors.maritime.coastline import point_on_land

        result = point_on_land(VICTORIA_LAT, VICTORIA_LON, sample_polygons)
        assert result is True

    def test_point_on_land_offshore(self, sample_polygons: list[np.ndarray]) -> None:
        """Test a point known to be offshore (Strait of Georgia).

        Assert False for offshore point.
        """
        from rtl.vectors.maritime.coastline import point_on_land

        result = point_on_land(OFFSHORE_LAT, OFFSHORE_LON, sample_polygons)
        assert result is False

    def test_point_on_land_empty(self) -> None:
        """Test point_on_land with empty polygon list.

        Assert False for empty polygon list.
        """
        from rtl.vectors.maritime.coastline import point_on_land

        result = point_on_land(49.0, -123.5, [])
        assert result is False
