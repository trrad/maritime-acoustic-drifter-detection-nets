"""Prototype step 1b: map (lat, lon) to SalishSeaCast (gridY, gridX) and pull
one day of hourly u, v at all 40 depth levels.

Uses the SalishSeaCast bathymetry dataset (ubcSSnBathymetryV21-08) to get
the 2D lat/lon fields aligned with the velocity grid. Finds the grid cell
nearest Race Rocks, pulls 24 hours of u/v at all depths, and prints a
summary (surface speed, deepest speed, depth profile at one time step).

Usage:
    uv run --with xarray,netCDF4,requests --with numpy python \\
        experiments/harmonic_prototype/02_map_grid_and_fetch_column.py
"""

from __future__ import annotations

import time

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]


RACE_ROCKS_LAT = 48.298
RACE_ROCKS_LON = -123.531

SAMPLE_DATE_START = "2023-06-15T00:30"
SAMPLE_DATE_END = "2023-06-16T00:30"

ERDDAP_BASE = "https://salishsea.eos.ubc.ca/erddap/griddap"
BATHY_DATASET = "ubcSSnBathymetryV21-08"
U_DATASET = "ubcSSg3DuGridFields1hV21-11"
V_DATASET = "ubcSSg3DvGridFields1hV21-11"


def find_grid_index(lat_target: float, lon_target: float) -> tuple[int, int]:
    """Fetch the bathymetry dataset's 2D lat/lon fields; find nearest cell."""
    url = f"{ERDDAP_BASE}/{BATHY_DATASET}"
    print(f"[info] opening {url} to get grid-to-latlon mapping")
    t0 = time.time()
    ds = xr.open_dataset(url)
    print(f"[info] bathy metadata fetched in {time.time()-t0:.2f}s")
    print(f"[info] bathy dims: {dict(ds.sizes)}")
    print(f"[info] bathy coords: {list(ds.coords)}")
    print(f"[info] bathy variables: {list(ds.data_vars)}")

    # The bathymetry dataset publishes `longitude(gridY, gridX)` and
    # `latitude(gridY, gridX)` as 2D fields on the same grid as the
    # velocity datasets. Fetch them (they're small — 898*398 = ~350k
    # floats each).
    t0 = time.time()
    lats = ds["latitude"].values
    lons = ds["longitude"].values
    print(f"[info] lat/lon fetched in {time.time()-t0:.2f}s")
    print(f"[info] lat shape: {lats.shape}")
    print(f"[info] lon shape: {lons.shape}")
    print(f"[info] lat range: {np.nanmin(lats):.3f} → {np.nanmax(lats):.3f}")
    print(f"[info] lon range: {np.nanmin(lons):.3f} → {np.nanmax(lons):.3f}")

    # Approximate nearest-cell — use local-cartesian distance.
    # Convert to meters via WGS84 cosine factor for lon, flat for lat
    # (fine at this scale).
    cos_lat = np.cos(np.deg2rad(lat_target))
    dlat_m = (lats - lat_target) * 111_320.0
    dlon_m = (lons - lon_target) * 111_320.0 * cos_lat
    dist_m = np.sqrt(dlat_m**2 + dlon_m**2)
    # Mask NaN cells.
    dist_m = np.where(np.isnan(dist_m), np.inf, dist_m)
    idx = int(np.argmin(dist_m))
    gy, gx = np.unravel_index(idx, dist_m.shape)
    gy, gx = int(gy), int(gx)
    found_lat = float(lats[gy, gx])
    found_lon = float(lons[gy, gx])
    found_dist_m = float(dist_m[gy, gx])
    print(f"[info] nearest cell: (gridY={gy}, gridX={gx})")
    print(f"[info]   at (lat={found_lat:.4f}, lon={found_lon:.4f})")
    print(f"[info]   {found_dist_m:.1f} m from target (Race Rocks)")

    return gy, gx


def fetch_column_one_day(gy: int, gx: int) -> xr.Dataset:
    """Fetch one day of hourly u, v at all depths for cell (gy, gx)."""
    url_u = f"{ERDDAP_BASE}/{U_DATASET}"
    url_v = f"{ERDDAP_BASE}/{V_DATASET}"
    print(f"[info] opening u dataset")
    t0 = time.time()
    ds_u = xr.open_dataset(url_u)
    ds_v = xr.open_dataset(url_v)
    print(f"[info] both datasets opened in {time.time()-t0:.2f}s")

    # Subset: one grid cell, all depths, 24 hours.
    t0 = time.time()
    u_col = ds_u["uVelocity"].sel(
        time=slice(SAMPLE_DATE_START, SAMPLE_DATE_END),
    ).isel(gridY=gy, gridX=gx)
    v_col = ds_v["vVelocity"].sel(
        time=slice(SAMPLE_DATE_START, SAMPLE_DATE_END),
    ).isel(gridY=gy, gridX=gx)
    # Force materialization (xarray is lazy).
    u_vals = u_col.values
    v_vals = v_col.values
    dt_fetch = time.time() - t0
    print(f"[info] one-cell one-day 40-depth subset fetched in {dt_fetch:.2f}s")
    print(f"[info] u shape: {u_vals.shape}")
    print(f"[info] v shape: {v_vals.shape}")
    print(f"[info] u finite-fraction: {np.isfinite(u_vals).mean():.3f}")

    # Build an xarray Dataset to return.
    out = xr.Dataset(
        {"u_ms": u_col, "v_ms": v_col},
        attrs={
            "gridY": gy,
            "gridX": gx,
            "fetch_wall_clock_sec": dt_fetch,
        },
    )
    return out


def summarize_column(ds: xr.Dataset) -> None:
    u = ds["u_ms"].values  # (n_time, 40)
    v = ds["v_ms"].values
    depths = ds["depth"].values  # (40,)

    # Surface (depth level 0) vs deepest-valid speed over the 24h.
    speed = np.sqrt(u**2 + v**2)  # (n_time, 40)

    print()
    print("=== Summary: one day at Race Rocks ===")
    print(f"time steps: {speed.shape[0]}")
    print(f"depth levels: {speed.shape[1]}")

    # Surface (topmost level).
    surf_mean = np.nanmean(speed[:, 0])
    surf_max = np.nanmax(speed[:, 0])
    print(f"surface (depth={depths[0]:.2f}m): mean speed {surf_mean:.3f} m/s, max {surf_max:.3f} m/s")

    # 50m (find closest level — target for ballast drifter).
    idx_50m = int(np.argmin(np.abs(depths - 50.0)))
    finite_50 = np.isfinite(speed[:, idx_50m]).mean()
    if finite_50 > 0:
        m50_mean = np.nanmean(speed[:, idx_50m])
        m50_max = np.nanmax(speed[:, idx_50m])
        print(f"~50m (depth={depths[idx_50m]:.2f}m, finite={finite_50:.2%}): "
              f"mean speed {m50_mean:.3f} m/s, max {m50_max:.3f} m/s")

    # Mid-column at first time step — show baroclinic shear.
    print()
    print("First time step (hour 0): u, v vs depth")
    for k in range(0, len(depths), 5):
        finite = np.isfinite(u[0, k])
        if finite:
            print(f"  depth {depths[k]:7.2f}m: u={u[0, k]:+.3f} v={v[0, k]:+.3f} "
                  f"speed={speed[0, k]:.3f} m/s")
        else:
            print(f"  depth {depths[k]:7.2f}m: below bathymetry (NaN)")


def main() -> None:
    print(f"=== Race Rocks grid-mapping + column fetch ===")
    print(f"Target: ({RACE_ROCKS_LAT}°N, {RACE_ROCKS_LON}°W)")
    print(f"Window: {SAMPLE_DATE_START} → {SAMPLE_DATE_END}")
    print()

    gy, gx = find_grid_index(RACE_ROCKS_LAT, RACE_ROCKS_LON)
    print()
    ds_col = fetch_column_one_day(gy, gx)
    summarize_column(ds_col)


if __name__ == "__main__":
    main()
