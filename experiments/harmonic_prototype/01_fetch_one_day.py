"""Prototype step 1a: can we pull ONE DAY of SalishSeaCast data at one cell?

Before writing the full utide analysis, validate the ERDDAP endpoint works,
discover the actual data shapes, and confirm our target point (Race Rocks,
~48.30°N, -123.53°W) is covered.

Race Rocks is chosen because:
- Known strong tidal signal with documented overtides (M4, M6 meaningful)
- Published DFO harmonic constants available for cross-validation
- Located in Juan de Fuca Strait approach (operational deployment region)

Usage:
    uv run --with xarray,netCDF4,requests python experiments/harmonic_prototype/01_fetch_one_day.py
"""

from __future__ import annotations

import sys
import time

import xarray as xr  # type: ignore[import-not-found]


RACE_ROCKS_LAT = 48.298
RACE_ROCKS_LON = -123.531

# Use a recent but fully-archived date (not today — today's hindcast may
# still be finalizing). Pick a quiet date well inside the archive.
SAMPLE_DATE_START = "2023-06-15T00:00:00Z"
SAMPLE_DATE_END = "2023-06-16T00:00:00Z"

# SalishSeaCast ERDDAP — v21-11 Green hindcast, hourly 3D currents.
ERDDAP_BASE = "https://salishsea.eos.ubc.ca/erddap/griddap"
U_DATASET = "ubcSSg3DuGridFields1hV21-11"
V_DATASET = "ubcSSg3DvGridFields1hV21-11"


def fetch_u_one_day_one_column() -> xr.DataArray:
    """Fetch one day of hourly u-velocity at Race Rocks, all depth levels."""
    # ERDDAP griddap subset via xarray.open_dataset on the OPeNDAP URL.
    # xarray resolves the dataset metadata; we then .sel() to subset.
    url = f"{ERDDAP_BASE}/{U_DATASET}"
    print(f"[info] opening ERDDAP dataset at {url}")
    t0 = time.time()
    ds = xr.open_dataset(url)
    print(f"[info] dataset metadata fetched in {time.time()-t0:.2f}s")
    print(f"[info] dims: {dict(ds.sizes)}")
    print(f"[info] variables: {list(ds.data_vars)}")
    print(f"[info] coords: {list(ds.coords)}")

    # Discover the coordinate variable names — ERDDAP griddap convention
    # varies (lat/lon vs latitude/longitude vs gridY/gridX).
    coord_names = set(ds.coords)
    print(f"[info] coord names: {coord_names}")

    # Print time range of the dataset.
    if "time" in ds.coords:
        t_start = ds.time.values[0]
        t_end = ds.time.values[-1]
        print(f"[info] time range: {t_start} → {t_end}")
        print(f"[info] n time steps: {ds.sizes.get('time', '?')}")

    # Print depth axis.
    depth_coord = None
    for name in ("depth", "deptht", "depthu", "z"):
        if name in ds.coords:
            depth_coord = name
            break
    if depth_coord is not None:
        depths = ds[depth_coord].values
        print(f"[info] depth coord '{depth_coord}' shape: {depths.shape}")
        print(f"[info] depth levels: min={depths.min():.2f} max={depths.max():.2f} n={len(depths)}")
        print(f"[info] first 5 depths: {depths[:5]}")
        print(f"[info] last 5 depths: {depths[-5:]}")

    # Attempt a small subset: one day, all depths, one grid cell near Race Rocks.
    # ERDDAP griddap datasets often use 2D lat/lon(gridY, gridX) so
    # nearest-neighbor via .sel on lat/lon won't work directly. Discover
    # the coord structure first.
    print(f"[info] u variable data_vars: {list(ds.data_vars)}")
    u_var_name = None
    for candidate in ("uVelocity", "u", "uvel", "vozocrtx"):
        if candidate in ds.data_vars:
            u_var_name = candidate
            break
    if u_var_name is None:
        print(f"[error] could not find u-velocity variable in {list(ds.data_vars)}")
        sys.exit(1)
    print(f"[info] u variable: {u_var_name}")
    print(f"[info] u dims: {ds[u_var_name].dims}")
    print(f"[info] u shape: {ds[u_var_name].shape}")

    # Print attributes for provenance.
    print(f"[info] dataset global attrs (subset):")
    for key in ("title", "institution", "source", "history", "comment",
                "date_created", "time_coverage_start", "time_coverage_end"):
        val = ds.attrs.get(key)
        if val is not None:
            print(f"       {key}: {str(val)[:120]}")

    return ds[u_var_name]


def main() -> None:
    print(f"=== SalishSeaCast ERDDAP fetch prototype ===")
    print(f"Target: Race Rocks ({RACE_ROCKS_LAT}°N, {RACE_ROCKS_LON}°W)")
    print(f"Window: {SAMPLE_DATE_START} → {SAMPLE_DATE_END}")
    print()

    u = fetch_u_one_day_one_column()
    print()
    print(f"[success] u-variable handle obtained.")
    print(f"[info] proceeding requires coord mapping — see next script.")


if __name__ == "__main__":
    main()
