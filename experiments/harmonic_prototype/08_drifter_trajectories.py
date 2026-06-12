"""Test G1: ballast steering authority — do drifters at different fixed
depths diverge meaningfully when advected through SalishSeaCast truth?

No PF, no climatology, no sensors. Just Lagrangian integration through
the cached 2023 Apr-Jun 3D current field at several fixed depths.

The separation of the resulting trajectories IS the steering authority
envelope. A ballast drifter can (eventually, under control) reach any
trajectory spanned by its available depth choices — this plot is the
reachability set.

Four fixed-depth drifters, same starting point, same time window:
    depth 0.5m (surface)
    depth ~5m
    depth ~20m
    depth ~50m

Simple 4th-order Runge-Kutta Lagrangian integration with 1h tick.
Trilinear interpolation in (lat, lon, t) at each drifter's fixed depth
level (snaps to the nearest SalishSeaCast depth level since our cache
has discrete levels).
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]
from scipy.interpolate import RegularGridInterpolator  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    bbox_latlon_arrays,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
MONTHS = ["2023-04", "2023-05", "2023-06"]

# Simulation settings.
SIM_DURATION_DAYS = 3          # 3 days — short enough to stay inside the bbox usually
DT_SEC = 3600.0                # 1h tick (matches SalishSeaCast hourly cadence)
DRIFTER_DEPTHS_M = [0.5, 5.0, 20.0, 50.0]

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)


def build_depth_level_interpolators(
    ds: xr.Dataset, bbox_lats_grid: np.ndarray, bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
) -> tuple[
    dict[float, tuple[RegularGridInterpolator, RegularGridInterpolator, float]],
    np.ndarray,
]:
    """For each requested target depth, find the nearest SalishSeaCast depth
    level and build (u, v) interpolators over (time, gridY, gridX).

    Returns a dict: target_depth_m → (u_interp, v_interp, actual_level_m).

    Note: SalishSeaCast lat/lon are 2D fields (curvilinear on the NEMO grid),
    so we can't use lat/lon directly as a regular axis — we operate in
    (gridY, gridX) space and map lat/lon → grid at query time via a KDTree
    or nearest-index lookup.

    For the prototype's short-duration / small-bbox case, a quick
    approximation is to treat the local lat/lon patch as a regular grid
    by taking the row-mean / column-mean of lat/lon. This is accurate to
    O(bbox-size × sphericity) ≈ O(10 km × 10⁻⁴) = meters. Good enough.
    """
    depth_values = ds["depth"].values
    time_values = ds["time"].values
    # Convert time to seconds since start for interpolation.
    t0 = time_values[0]
    times_sec = (time_values - t0) / np.timedelta64(1, "s")
    times_sec = times_sec.astype(float)

    # Take row-mean lat and column-mean lon as pseudo-regular axes.
    # (Small scale, small skew — good enough for the prototype.)
    lat_axis = bbox_lats_grid.mean(axis=1)         # per gridY row
    lon_axis = bbox_lons_grid.mean(axis=0)         # per gridX col
    # Ensure ascending for RegularGridInterpolator.
    if lat_axis[0] > lat_axis[-1]:
        lat_axis = lat_axis[::-1]
        flip_lat = True
    else:
        flip_lat = False
    if lon_axis[0] > lon_axis[-1]:
        lon_axis = lon_axis[::-1]
        flip_lon = True
    else:
        flip_lon = False

    out: dict[float, tuple[RegularGridInterpolator, RegularGridInterpolator, float]] = {}
    for target in target_depths_m:
        k = int(np.argmin(np.abs(depth_values - target)))
        actual = float(depth_values[k])
        u_cube = ds["u_ms"].isel(depth=k).values  # (time, gridY, gridX)
        v_cube = ds["v_ms"].isel(depth=k).values
        if flip_lat:
            u_cube = u_cube[:, ::-1, :]
            v_cube = v_cube[:, ::-1, :]
        if flip_lon:
            u_cube = u_cube[:, :, ::-1]
            v_cube = v_cube[:, :, ::-1]
        u_interp = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), u_cube,
            bounds_error=False, fill_value=np.nan,
        )
        v_interp = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), v_cube,
            bounds_error=False, fill_value=np.nan,
        )
        out[target] = (u_interp, v_interp, actual)
    return out, times_sec


def lat_lon_to_enu_step(u_ms: float, v_ms: float, lat_deg: float, dt_sec: float
                         ) -> tuple[float, float]:
    """Convert velocity (m/s) step into a (Δlat_deg, Δlon_deg) step.

    u_ms is eastward, v_ms is northward. Assumes small steps.
    """
    dlat = (v_ms * dt_sec) / 111_320.0
    dlon = (u_ms * dt_sec) / (111_320.0 * np.cos(np.deg2rad(lat_deg)))
    return dlat, dlon


def advect_drifter(
    start_lat: float, start_lon: float,
    u_interp: RegularGridInterpolator, v_interp: RegularGridInterpolator,
    times_sec: np.ndarray, dt_sec: float, n_steps: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RK4 Lagrangian integration at a fixed depth level."""
    lats = np.full(n_steps + 1, np.nan)
    lons = np.full(n_steps + 1, np.nan)
    ts = np.full(n_steps + 1, np.nan)
    lats[0], lons[0], ts[0] = start_lat, start_lon, times_sec[0]

    for i in range(n_steps):
        t = ts[i]
        lat = lats[i]
        lon = lons[i]
        if np.isnan(lat) or np.isnan(lon):
            break

        def velocity_at(t_q: float, lat_q: float, lon_q: float) -> tuple[float, float]:
            u = float(u_interp((t_q, lat_q, lon_q)))
            v = float(v_interp((t_q, lat_q, lon_q)))
            return u, v

        # RK4 in position (time held at midpoint for each stage per
        # standard textbook RK4-of-time-varying-ODE).
        u1, v1 = velocity_at(t, lat, lon)
        if not np.isfinite(u1):
            break
        dlat1, dlon1 = lat_lon_to_enu_step(u1, v1, lat, dt_sec / 2)

        u2, v2 = velocity_at(t + dt_sec / 2, lat + dlat1, lon + dlon1)
        if not np.isfinite(u2):
            break
        dlat2, dlon2 = lat_lon_to_enu_step(u2, v2, lat, dt_sec / 2)

        u3, v3 = velocity_at(t + dt_sec / 2, lat + dlat2, lon + dlon2)
        if not np.isfinite(u3):
            break
        dlat3, dlon3 = lat_lon_to_enu_step(u3, v3, lat, dt_sec)

        u4, v4 = velocity_at(t + dt_sec, lat + dlat3, lon + dlon3)
        if not np.isfinite(u4):
            break

        # Average the four samples using RK4 weights (velocities, not positions).
        u_avg = (u1 + 2 * u2 + 2 * u3 + u4) / 6
        v_avg = (v1 + 2 * v2 + 2 * v3 + v4) / 6
        dlat, dlon = lat_lon_to_enu_step(u_avg, v_avg, lat, dt_sec)

        lats[i + 1] = lat + dlat
        lons[i + 1] = lon + dlon
        ts[i + 1] = t + dt_sec

    return lats, lons, ts


def main() -> None:
    print("=== Ballast steering authority: fixed-depth trajectories ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}")
    print(f"loading 2023 truth cache ...")
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    print(f"  dims: {dict(ds.sizes)}")

    # Pick a starting point near the centre that's deep enough to sit all drifters.
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]
    cy, cx = n_y // 2, n_x // 2
    # Walk a bit toward the deeper side if needed.
    start_lat = float(lats_grid[cy, cx])
    start_lon = float(lons_grid[cy, cx])
    start_bathy = float(bathy_grid[cy, cx])
    print(f"start: ({start_lat:.4f}, {start_lon:.4f}) bathy={start_bathy:.0f}m")

    # Build interpolators at each requested depth.
    print(f"building interpolators at {DRIFTER_DEPTHS_M} m ...")
    t0 = time.time()
    interps, times_sec = build_depth_level_interpolators(
        ds, lats_grid, lons_grid, DRIFTER_DEPTHS_M,
    )
    print(f"  built in {time.time()-t0:.1f}s")
    for target, (_, _, actual) in interps.items():
        print(f"  target {target:.1f}m  →  nearest level {actual:.2f}m")

    # Advect each depth-drifter.
    n_steps = int(SIM_DURATION_DAYS * 24 * 3600 / DT_SEC)
    trajectories: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for target in DRIFTER_DEPTHS_M:
        u_interp, v_interp, actual = interps[target]
        lats, lons, ts = advect_drifter(
            start_lat, start_lon, u_interp, v_interp,
            times_sec, DT_SEC, n_steps,
        )
        last_valid = int(np.isfinite(lats).sum()) - 1
        if last_valid < 1:
            print(f"  depth {target}m: failed — drifter immediately NaN")
            continue
        trajectories[target] = (lats, lons, ts)
        print(f"  depth {target:>4.1f}m  (level {actual:.1f}m)  "
              f"{last_valid} steps advected  "
              f"final lat/lon = ({lats[last_valid]:.4f}, {lons[last_valid]:.4f})")

    # --- Separation statistics ---
    print()
    print("=== trajectory separation between depth pairs ===")
    keys = sorted(trajectories.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            d1, d2 = keys[i], keys[j]
            l1, o1, _ = trajectories[d1]
            l2, o2, _ = trajectories[d2]
            n = min(int(np.isfinite(l1).sum()), int(np.isfinite(l2).sum()))
            if n < 2:
                continue
            # Pairwise distance over time, in meters.
            cos_lat = np.cos(np.deg2rad(start_lat))
            dlat_m = (l1[:n] - l2[:n]) * 111_320.0
            dlon_m = (o1[:n] - o2[:n]) * 111_320.0 * cos_lat
            sep_m = np.sqrt(dlat_m**2 + dlon_m**2)
            hrs = np.arange(n)
            print(f"  {d1:>4.1f}m vs {d2:>4.1f}m: max sep {np.nanmax(sep_m)/1000:.2f} km  "
                  f"final sep {sep_m[-1]/1000:.2f} km  "
                  f"sep at hour 24: {sep_m[min(24, n-1)]/1000:.2f} km")

    # --- Plot trajectories ---
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))

    ax = axes[0]
    # Bathymetry background.
    masked = np.where(bathy_grid > 0, bathy_grid, np.nan)
    ax.imshow(masked, origin="lower", cmap="Blues", alpha=0.4,
              extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
    colors = ["C0", "C1", "C2", "C3"]
    for (d, color) in zip(DRIFTER_DEPTHS_M, colors):
        if d not in trajectories:
            continue
        lats, lons, _ = trajectories[d]
        mask = np.isfinite(lats)
        ax.plot(lons[mask], lats[mask], "-", color=color, lw=1.5,
                label=f"{d:.1f}m depth")
        ax.plot(lons[0], lats[0], "*", color="black", markersize=10)
        # Mark endpoint.
        last = np.where(mask)[0][-1]
        ax.plot(lons[last], lats[last], "o", color=color, markersize=8,
                markeredgecolor="black")
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title(f"Ballast steering authority: {SIM_DURATION_DAYS}d trajectories\n"
                 f"from ({start_lat:.3f}, {start_lon:.3f}) starting "
                 f"{MONTHS[0]}-01")
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.3)

    ax = axes[1]
    # Separation vs time for each depth pair (vs shallowest).
    base_d = keys[0]
    base_l, base_o, _ = trajectories[base_d]
    for (d, color) in zip(keys[1:], colors[1:]):
        lats, lons, _ = trajectories[d]
        n = min(int(np.isfinite(base_l).sum()), int(np.isfinite(lats).sum()))
        cos_lat = np.cos(np.deg2rad(start_lat))
        dlat_m = (base_l[:n] - lats[:n]) * 111_320.0
        dlon_m = (base_o[:n] - lons[:n]) * 111_320.0 * cos_lat
        sep_km = np.sqrt(dlat_m**2 + dlon_m**2) / 1000
        hrs = np.arange(n)
        ax.plot(hrs, sep_km, color=color, lw=1.5,
                label=f"{d:.1f}m vs {base_d:.1f}m")
    ax.set_xlabel("hours since start")
    ax.set_ylabel("separation from shallowest drifter (km)")
    ax.set_title("Depth-driven trajectory divergence")
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    out = FIG_DIR / "11_ballast_steering_authority.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
