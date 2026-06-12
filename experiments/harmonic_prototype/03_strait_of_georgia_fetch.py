"""Fetch a deployment-scale 5×5 grid of cells across central Strait of
Georgia, 1 year of hourly u,v at all 40 depths. Uses the monthly cache.

Deployment context:
    Central Strait of Georgia is the primary M1 Salish deployment region.
    10 nodes spread across a ~50 km basin with ~5–10 km LoRa spacing.
    Water column 200–400 m in the basin centre; baroclinic tide may be
    a first-order effect for ballast-cycling drifters.

This script is the "realistic deployment bbox" fetch. Downstream scripts
(utide analysis, visualization) operate on the cached result.

Grid layout (centre of Strait of Georgia, ballpark):
    Lat range  ~49.0–49.4 °N
    Lon range  ~-123.8 to -123.4 °W
    5×5 pattern → 25 cells, ~10 km spacing.

Usage:
    uv run --with xarray,netCDF4,requests,numpy python \\
        experiments/harmonic_prototype/03_strait_of_georgia_fetch.py

Notes on load:
    25 cells × 12 months = 300 cache entries. Each takes ~5–15 s over
    ERDDAP. Total fetch wall-clock on a cold cache: roughly 30–90 min,
    throttled by the 1 s per-chunk polite delay. Re-runs from the cache
    are seconds.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    GridCell,
    cache_size_bytes,
    fetch_cell_column,
    find_grid_cell,
)


# Deployment-bbox geometry: 5×5 pattern across the central Strait of Georgia.
# Centre is ~49.2°N, -123.6°W (roughly between Nanaimo and Sechelt).
# Spacing approximates LoRa inter-node range for the M1 fleet (~5–10 km).
LAT_MIN = 49.00
LAT_MAX = 49.40
LON_MIN = -123.80
LON_MAX = -123.40
N_GRID = 5  # 5×5 = 25 cells

YEAR_START = 2023
YEAR_END = 2023  # one year — enough for M2/S2 Rayleigh separability, keeps fetch bounded


def build_deployment_grid() -> list[tuple[float, float]]:
    """5×5 evenly-spaced target lat/lon pairs across the deployment bbox."""
    lats = np.linspace(LAT_MIN, LAT_MAX, N_GRID)
    lons = np.linspace(LON_MIN, LON_MAX, N_GRID)
    return [(float(lat), float(lon)) for lat in lats for lon in lons]


def main() -> None:
    print(f"=== Strait of Georgia deployment-scale harmonic fetch ===")
    print(f"bbox: lat=[{LAT_MIN}, {LAT_MAX}]  lon=[{LON_MIN}, {LON_MAX}]")
    print(f"grid: {N_GRID}×{N_GRID} = {N_GRID*N_GRID} cells")
    print(f"years: {YEAR_START}–{YEAR_END}")
    print()

    targets = build_deployment_grid()

    # Map each target to its actual SalishSeaCast cell.
    # Deduplicate (nearby targets may share a cell at 500 m grid resolution).
    cells_by_key: dict[tuple[int, int], GridCell] = {}
    target_to_cell: list[tuple[tuple[float, float], GridCell]] = []
    for (lat, lon) in targets:
        cell = find_grid_cell(lat, lon)
        target_to_cell.append(((lat, lon), cell))
        cells_by_key[(cell.gy, cell.gx)] = cell

    print(f"unique grid cells: {len(cells_by_key)} (of {len(targets)} targets)")
    print()
    print("cell map:")
    for (lat, lon), cell in target_to_cell:
        print(f"  target ({lat:.3f}, {lon:.3f}) → "
              f"cell ({cell.gy}, {cell.gx}) at ({cell.lat_deg:.4f}, "
              f"{cell.lon_deg:.4f}) depth={cell.bathymetry_m:.1f} m")
    print()

    # Persist the cell map as JSON for downstream scripts.
    out_dir = Path(__file__).parent / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / f"deployment_grid_{YEAR_START}_{YEAR_END}.json"
    map_payload = {
        "bbox": {"lat_min": LAT_MIN, "lat_max": LAT_MAX,
                 "lon_min": LON_MIN, "lon_max": LON_MAX},
        "n_grid": N_GRID,
        "year_start": YEAR_START,
        "year_end": YEAR_END,
        "cells": [
            {"target_lat": lat, "target_lon": lon, **asdict(cell)}
            for (lat, lon), cell in target_to_cell
        ],
        "unique_cells": [asdict(c) for c in cells_by_key.values()],
    }
    with map_path.open("w") as f:
        json.dump(map_payload, f, indent=2)
    print(f"[info] wrote cell map → {map_path}")
    print()

    # Fetch each unique cell.
    t0 = time.time()
    for i, cell in enumerate(cells_by_key.values()):
        print(f"[{i+1}/{len(cells_by_key)}] fetching cell ({cell.gy}, {cell.gx})"
              f" at ({cell.lat_deg:.4f}, {cell.lon_deg:.4f}) depth={cell.bathymetry_m:.1f}m")
        ds = fetch_cell_column(cell.gy, cell.gx, YEAR_START, YEAR_END, verbose=True)
        print(f"    got {ds.sizes.get('time', 0)} hours × {ds.sizes.get('depth', 0)} depths")
    dt = time.time() - t0

    print()
    print(f"=== summary ===")
    print(f"total wall-clock: {dt/60:.1f} min")
    print(f"cache size: {cache_size_bytes() / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
