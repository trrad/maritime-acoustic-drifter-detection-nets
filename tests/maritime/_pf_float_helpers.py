"""Shared helpers for the split ``test_pf_float_*.py`` suite.

Reuses the uniform-climatology / uniform-bathymetry / test-map /
zero-noise-config / anchor-positions / land-polygon-map / pf-at-origin
fixtures across the per-stage test files (construct / predict / weight /
resample_estimate / truth_separation).

Direct underscore-access to ``pf._particles`` / ``pf._weights`` is the
documented test-fixture boundary — the resample/estimate tests need to
install specific posterior shapes that are awkward to reach via the
public API.
"""

from __future__ import annotations

import numpy as np

from rtl.vectors.maritime.map_payload import (
    BathymetryGrid,
    ClimatologyGrid,
    RegionalMap,
)
from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.state_layout import StateLayout


TEST_ENU_ORIGIN_LAT = 20.0
TEST_ENU_ORIGIN_LON = -160.0
TEST_BBOX = (20.0, -160.0, 20.5, -159.5)  # (south, west, north, east)


def make_uniform_climatology(
    bbox: tuple[float, float, float, float],
    mean_vx: float,
    mean_vy: float,
    n: int = 3,
    var_vx: float = 0.0,
    var_vy: float = 0.0,
) -> ClimatologyGrid:
    """Uniform ``n x n`` climatology grid — every cell carries
    ``(mean_vx, mean_vy)`` with optional uniform variance
    ``(var_vx, var_vy)``. ``ClimatologyGrid.at`` is nearest-neighbor,
    so uniformity means any particle's (lat, lon) returns the same
    mean/variance — tests can reason about the PF predict step
    independently of which grid cell each particle falls in.
    """
    south, west, north, east = bbox
    lats = np.linspace(south, north, n)
    lons = np.linspace(west, east, n)
    return ClimatologyGrid(
        lats=lats,
        lons=lons,
        mean_vx_ms=np.full((n, n), mean_vx),
        mean_vy_ms=np.full((n, n), mean_vy),
        var_vx_ms2=np.full((n, n), var_vx),
        var_vy_ms2=np.full((n, n), var_vy),
    )


def make_uniform_bathymetry(
    bbox: tuple[float, float, float, float],
    depth_m: float = 1000.0,
    n: int = 3,
) -> BathymetryGrid:
    """Uniform ``n x n`` bathymetry grid. Only the bathy_probe tests
    exercise this; other tests need it just to satisfy
    ``RegionalMap``'s bathymetry field."""
    south, west, north, east = bbox
    lats = np.linspace(south, north, n)
    lons = np.linspace(west, east, n)
    return BathymetryGrid(
        lats=lats,
        lons=lons,
        depths_m=np.full((n, n), depth_m),
    )


def make_test_map(
    *,
    mean_vx: float = 0.0,
    mean_vy: float = 0.0,
    var_vx: float = 0.0,
    var_vy: float = 0.0,
    bbox: tuple[float, float, float, float] = TEST_BBOX,
) -> RegionalMap:
    """``RegionalMap`` whose climatology is uniform at
    ``(mean_vx, mean_vy)`` with optional uniform variance
    ``(var_vx, var_vy)``, and with no land / lanes."""
    return RegionalMap(
        bathymetry=make_uniform_bathymetry(bbox),
        land_polygons=[],
        shipping_lanes=[],
        climatology=make_uniform_climatology(
            bbox, mean_vx, mean_vy, var_vx=var_vx, var_vy=var_vy
        ),
    )


def make_map_with_land_polygon(
    bbox: tuple[float, float, float, float] = TEST_BBOX,
    *,
    polygon_lon_lat: np.ndarray | None = None,
    depth_m: float = 1000.0,
) -> RegionalMap:
    """``RegionalMap`` with a single user-supplied land polygon
    (``[lon, lat]`` columns — the GeoJSON convention used by
    ``coastline.point_on_land``). Climatology is uniform-zero; the only
    thing distinguishing "on land" from "off land" is polygon membership.
    """
    polygons: list[np.ndarray] = []
    if polygon_lon_lat is not None:
        polygons.append(polygon_lon_lat)
    return RegionalMap(
        bathymetry=make_uniform_bathymetry(bbox, depth_m=depth_m),
        land_polygons=polygons,
        shipping_lanes=[],
        climatology=make_uniform_climatology(bbox, mean_vx=0.0, mean_vy=0.0),
    )


def zero_noise_config(n_particles: int = 100) -> PFFloatConfig:
    """``PFFloatConfig`` with every process-noise scale set to zero —
    for tests that want deterministic advection."""
    return PFFloatConfig(
        n_particles=n_particles,
        process_noise_pos_m_per_sqrt_s=0.0,
        process_noise_vel_ms_per_sqrt_s=0.0,
        process_noise_heading_deg_per_sqrt_s=0.0,
        process_noise_current_ms_per_sqrt_s=0.0,
    )


def anchor_positions_default() -> dict[str, tuple[float, float]]:
    """Single anchor near the test bbox center."""
    return {"a00": (20.05, -159.95)}


def make_pf_at_origin(
    *,
    layout: StateLayout,
    rng: np.random.Generator,
    cov_diag: np.ndarray | None = None,
    initial_state_mean: np.ndarray | None = None,
    n_particles: int = 100,
    onboard_map: RegionalMap | None = None,
    anchor_positions: dict[str, tuple[float, float]] | None = None,
    config: PFFloatConfig | None = None,
) -> PFFloat:
    """Construct a PF at ENU origin with sensible defaults. ``rng`` is a
    required kwarg (the PF constructor crashes unhelpfully if it's
    ``None``). All particles start at the origin (mean = zeros) unless
    ``initial_state_mean`` is supplied; process noise zero by default.
    """
    state_dim = layout.state_dim
    mean = initial_state_mean if initial_state_mean is not None else np.zeros(state_dim)
    cov = cov_diag if cov_diag is not None else np.zeros(state_dim)
    onmap = onboard_map if onboard_map is not None else make_test_map()
    anchors = anchor_positions if anchor_positions is not None else anchor_positions_default()
    cfg = config if config is not None else zero_noise_config(n_particles=n_particles)
    return PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=mean,
        initial_state_cov_diag=cov,
        onboard_map=onmap,
        anchor_positions=anchors,
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=cfg,
        rng=rng,
    )
