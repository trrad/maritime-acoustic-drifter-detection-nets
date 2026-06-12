"""Run utide.solve per (depth, cell) on the cached Strait of Georgia
bbox. Masks below-bathymetry cells. Writes results to a single NetCDF
for visualization by script 05.

Inputs: monthly cache from script 03.
Outputs: cache/utide_results_<bbox_key>_<months>.nc

Scope:
    For each (gy, gx) in the bbox:
        bathy_m = bathymetry[gy, gx]
        wet_depth_levels = depths <= bathy_m
        for each wet depth level:
            utide.solve(time, u, v, lat=cell_lat,
                        constit=["M2","S2","K1","O1"],
                        nodal=True, trend=False, method="ols")
            record amp_vx, phase_vx, amp_vy, phase_vy per constituent
        dry depth levels get NaN

Performance note:
    utide.solve on 3 months × 1 cell × 1 depth is ~1-3 s.
    ~500 cells × ~20 wet depths each = 10000 fits × ~2s = ~5-6 hours.
    TOO SLOW for interactive prototype. For the first pass we analyze
    only a subset: all cells at surface (depth 0), plus the deepest
    wet depth and a mid-column depth for a handful of representative
    cells. That's tractable in minutes, still produces reviewable
    spatial maps + vertical profiles.

Usage:
    uv run --with xarray,netCDF4,numpy,utide python \\
        experiments/harmonic_prototype/04_run_utide.py
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]

# Suppress numpy 2.x deprecation warnings from utide (utide 0.3.1 is
# noisy against numpy 2.x but functionally correct).
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import utide  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    GridBBox,
    _CACHE_DIR,
    bbox_latlon_arrays,
    fetch_bbox_months,
)


# Match the bbox from script 03 so we use the same cached data.
LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
MONTHS = ["2023-04", "2023-05", "2023-06"]

# Constituents to fit. Starting with the 4 "classic" — the review's
# critique of this short list becomes measurable from the residual
# plots produced by script 05.
CONSTITUENTS = ["M2", "S2", "K1", "O1"]


def run_utide_at_cell(
    times: np.ndarray,      # shape (n_time,) datetime64
    u: np.ndarray,          # shape (n_time,)
    v: np.ndarray,          # shape (n_time,)
    cell_lat: float,
    constit: list[str],
) -> dict:
    """Single utide.solve call, returning per-component amp + phase per constituent.

    utide returns ellipse form (Lsmaj, Lsmin, theta, g). Converting to
    per-component cosines for independent u/v channels is an
    approximation valid for near-rectilinear flow. Returns both the
    ellipse form AND the per-component form for downstream use.
    """
    # utide needs time as pandas datetime. Convert from xarray's datetime64.
    # utide.solve returns a solution object with per-constituent outputs.
    coef = utide.solve(
        times,
        u,
        v,
        lat=cell_lat,
        constit=constit,
        nodal=True,
        trend=False,
        method="ols",
        conf_int="MC",
        verbose=False,
    )
    # `coef` is a Bunch with:
    #   name (list of constituent names), Lsmaj, Lsmin, theta, g
    # Build a dict keyed by constituent name, with both forms stored.
    out: dict[str, dict] = {}
    for i, name in enumerate(coef["name"]):
        if name not in constit:
            continue
        Lsmaj = float(coef["Lsmaj"][i])     # semi-major axis (m/s)
        Lsmin = float(coef["Lsmin"][i])     # semi-minor (signed; sign encodes rotation)
        theta_deg = float(coef["theta"][i])  # ellipse orientation CCW from east, degrees
        g_deg = float(coef["g"][i])         # Greenwich phase lag (degrees)

        # Decompose ellipse to per-component cosines.
        # A rotating current vector can be written as the sum of two
        # counter-rotating circles; for visualization we project to
        # u_amp(t) = Lsmaj*cos(theta)*cos(ωt − g) + Lsmin*sin(theta)*sin(ωt − g)
        # Equivalently u(t) = A_u * cos(ωt − φ_u) with
        #   A_u^2 = (Lsmaj*cos(theta))^2 + (Lsmin*sin(theta))^2
        #   tan(φ_u) = (Lsmin*sin(theta) / (Lsmaj*cos(theta)))  — modulo sign
        # This is valid but lossy (discards rotation sense). We store
        # both the per-component amp/phase AND the ellipse so the
        # visualizer can choose.
        th = np.deg2rad(theta_deg)
        g_rad = np.deg2rad(g_deg)
        # u-component: projection of ellipse onto east axis.
        a_u_cos = Lsmaj * np.cos(th)
        a_u_sin = Lsmin * np.sin(th)
        amp_u = float(np.sqrt(a_u_cos**2 + a_u_sin**2))
        phase_u_rad = float(np.arctan2(a_u_sin, a_u_cos)) + g_rad
        # v-component: projection onto north axis.
        a_v_cos = Lsmaj * np.sin(th)
        a_v_sin = -Lsmin * np.cos(th)
        amp_v = float(np.sqrt(a_v_cos**2 + a_v_sin**2))
        phase_v_rad = float(np.arctan2(a_v_sin, a_v_cos)) + g_rad

        out[name] = {
            "Lsmaj": Lsmaj,
            "Lsmin": Lsmin,
            "theta_deg": theta_deg,
            "g_deg": g_deg,
            "amp_u_ms": amp_u,
            "phase_u_rad": phase_u_rad % (2 * np.pi),
            "amp_v_ms": amp_v,
            "phase_v_rad": phase_v_rad % (2 * np.pi),
        }
    return out


def main() -> None:
    print("=== utide harmonic analysis over Strait of Georgia bbox ===")
    print(f"lat [{LAT_MIN}, {LAT_MAX}]  lon [{LON_MIN}, {LON_MAX}]")
    print(f"months: {MONTHS}")
    print(f"constituents: {CONSTITUENTS}")
    print()

    # Load cached data. fetch_bbox_months will be instant on cache hits.
    # We need the GridBBox to key the cache correctly, so derive it from lat/lon.
    from salishseacast_cache import bbox_from_latlon
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}  ({bbox.n_cells} cells)")

    print("loading cached data ...")
    t0 = time.time()
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    print(f"loaded in {time.time()-t0:.1f}s — dims: {dict(ds.sizes)}")
    print()

    lats, lons, bathy = bbox_latlon_arrays(bbox)
    print(f"bbox bathymetry: {bathy[bathy>0].min():.1f} – {bathy.max():.1f} m wet")
    print()

    # Shape: u_ms(time, depth, gridY, gridX)
    u_da = ds["u_ms"]
    v_da = ds["v_ms"]
    depths = ds["depth"].values
    times = ds["time"].values  # datetime64 array

    n_y = ds.sizes["gridY"]
    n_x = ds.sizes["gridX"]
    n_depth = ds.sizes["depth"]
    n_const = len(CONSTITUENTS)

    # Storage for per-(cell, depth, constituent) results.
    amp_u = np.full((n_y, n_x, n_depth, n_const), np.nan)
    amp_v = np.full_like(amp_u, np.nan)
    phase_u = np.full_like(amp_u, np.nan)
    phase_v = np.full_like(amp_u, np.nan)
    Lsmaj = np.full_like(amp_u, np.nan)
    Lsmin = np.full_like(amp_u, np.nan)
    theta_deg = np.full_like(amp_u, np.nan)
    g_deg = np.full_like(amp_u, np.nan)

    cell_ok = np.zeros((n_y, n_x), dtype=bool)

    # Choose depth levels to analyze.
    # Strategy: analyze ALL wet depths at cell (n_y//2, n_x//2) — the centre —
    # for a detailed vertical profile. Analyze only depth=0 (surface) at all
    # other cells for spatial maps.
    centre_y, centre_x = n_y // 2, n_x // 2
    print(f"Strategy: centre cell ({centre_y},{centre_x}) at all wet depths; "
          f"other cells at surface only.")
    print()

    total_cells = n_y * n_x
    t_analysis_start = time.time()

    for iy in range(n_y):
        for ix in range(n_x):
            cell_bathy = float(bathy[iy, ix])
            if cell_bathy <= 0:
                # Land / out-of-domain cell.
                continue
            cell_lat = float(lats[iy, ix])

            # Which depth levels to analyze at this cell?
            if (iy, ix) == (centre_y, centre_x):
                depth_levels = [k for k in range(n_depth) if depths[k] <= cell_bathy]
            else:
                depth_levels = [0]  # surface only for spatial maps

            for k in depth_levels:
                u_series = u_da.isel(gridY=iy, gridX=ix, depth=k).values
                v_series = v_da.isel(gridY=iy, gridX=ix, depth=k).values
                # Skip if velocities are all zero (below-bottom mask) or NaN.
                if not np.any(np.abs(u_series) > 1e-9):
                    continue
                if not np.all(np.isfinite(u_series)) or not np.all(np.isfinite(v_series)):
                    continue
                try:
                    res = run_utide_at_cell(
                        times, u_series, v_series, cell_lat, CONSTITUENTS
                    )
                except Exception as e:
                    # Non-fatal: log, leave NaN.
                    print(f"  [warn] utide failed at ({iy},{ix},depth={k}): {e}")
                    continue
                for ci, cname in enumerate(CONSTITUENTS):
                    if cname not in res:
                        continue
                    r = res[cname]
                    amp_u[iy, ix, k, ci] = r["amp_u_ms"]
                    amp_v[iy, ix, k, ci] = r["amp_v_ms"]
                    phase_u[iy, ix, k, ci] = r["phase_u_rad"]
                    phase_v[iy, ix, k, ci] = r["phase_v_rad"]
                    Lsmaj[iy, ix, k, ci] = r["Lsmaj"]
                    Lsmin[iy, ix, k, ci] = r["Lsmin"]
                    theta_deg[iy, ix, k, ci] = r["theta_deg"]
                    g_deg[iy, ix, k, ci] = r["g_deg"]
            cell_ok[iy, ix] = True

        # Progress every 10% of rows.
        if iy > 0 and iy % max(1, n_y // 10) == 0:
            dt = time.time() - t_analysis_start
            pct = iy / n_y * 100
            eta = dt / iy * (n_y - iy)
            print(f"  row {iy}/{n_y} ({pct:.0f}%, {dt:.1f}s elapsed, ETA {eta:.1f}s)")

    total_dt = time.time() - t_analysis_start
    n_ok = int(cell_ok.sum())
    print()
    print(f"analysis complete: {n_ok}/{total_cells} wet cells processed in {total_dt:.1f}s")
    print(f"  ({total_dt / max(n_ok, 1):.2f} s per wet cell including deep column at centre)")

    # Build an xarray Dataset of results and save.
    coord_const = np.array(CONSTITUENTS)
    out = xr.Dataset(
        {
            "amp_u_ms":    (("gridY", "gridX", "depth", "constituent"), amp_u),
            "amp_v_ms":    (("gridY", "gridX", "depth", "constituent"), amp_v),
            "phase_u_rad": (("gridY", "gridX", "depth", "constituent"), phase_u),
            "phase_v_rad": (("gridY", "gridX", "depth", "constituent"), phase_v),
            "Lsmaj":       (("gridY", "gridX", "depth", "constituent"), Lsmaj),
            "Lsmin":       (("gridY", "gridX", "depth", "constituent"), Lsmin),
            "theta_deg":   (("gridY", "gridX", "depth", "constituent"), theta_deg),
            "g_deg":       (("gridY", "gridX", "depth", "constituent"), g_deg),
            "cell_ok":     (("gridY", "gridX"), cell_ok),
            "bathymetry_m": (("gridY", "gridX"), bathy),
            "lat_deg":     (("gridY", "gridX"), lats),
            "lon_deg":     (("gridY", "gridX"), lons),
        },
        coords={
            "depth": depths,
            "constituent": coord_const,
        },
        attrs={
            "constituents": ",".join(CONSTITUENTS),
            "months": ",".join(MONTHS),
            "bbox_key": bbox.key(),
            "utide_nodal": "True",
            "utide_trend": "False",
            "utide_method": "ols",
            "centre_gridY_local": centre_y,
            "centre_gridX_local": centre_x,
        },
    )
    out_path = _CACHE_DIR / f"utide_results_{bbox.key()}_{'_'.join(MONTHS)}.nc"
    out.to_netcdf(out_path)
    print(f"wrote results → {out_path}")


if __name__ == "__main__":
    main()
