"""Plot full-depth u/v profiles at the 8 hand-picked stations from the
2023-04 SalishSeaCast snapshot to check whether there's meaningful
control authority below 50 m (deep return-flow / exchange layer).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)

LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

STATIONS = [
    (49.3533, -123.7411),
    (49.3533, -123.6892),
    (49.3924, -123.7411),
    (49.3924, -123.6374),
    (49.3091, -123.6773),
    (49.2699, -123.7033),
    (49.3287, -123.7810),
    (49.3533, -123.5855),
]

bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)

u = ds["u_ms"].values   # (t, depth, gy, gx)
v = ds["v_ms"].values
depths = ds["depth"].values

fig, axes = plt.subplots(2, 4, figsize=(18, 9), sharey=True)
for i, (s_lat, s_lon) in enumerate(STATIONS):
    ax = axes[i // 4, i % 4]
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    dy = (lats_grid - s_lat) * 111320.0
    dx = (lons_grid - s_lon) * 111320.0 * cos_lat
    dist = np.sqrt(dy**2 + dx**2)
    gy, gx = np.unravel_index(np.argmin(dist), dist.shape)
    bathy = bathy_grid[gy, gx]

    u_st = u[:, :, gy, gx]  # (t, depth)
    v_st = v[:, :, gy, gx]
    # Mask sea-floor zeros (NEMO fills below-bottom with 0)
    mask = (u_st != 0) | (v_st != 0)
    u_st = np.where(mask, u_st, np.nan)
    v_st = np.where(mask, v_st, np.nan)

    u_med = np.nanmedian(u_st, axis=0) * 100  # cm/s
    v_med = np.nanmedian(v_st, axis=0) * 100
    u_p10, u_p90 = np.nanpercentile(u_st * 100, [10, 90], axis=0)
    v_p10, v_p90 = np.nanpercentile(v_st * 100, [10, 90], axis=0)

    ax.fill_betweenx(depths, u_p10, u_p90, alpha=0.2, color="tab:blue")
    ax.fill_betweenx(depths, v_p10, v_p90, alpha=0.2, color="tab:red")
    ax.plot(u_med, depths, "tab:blue", label="u (east)")
    ax.plot(v_med, depths, "tab:red", label="v (north)")
    ax.axvline(0, color="k", lw=0.5, alpha=0.5)
    for d in (0.5, 5.0, 10.0, 20.0, 50.0):
        ax.axhline(d, color="tab:green", lw=0.5, ls=":")
    ax.axhline(bathy, color="k", lw=0.8, alpha=0.4)
    ax.set_xlim(-30, 30)
    ax.set_ylim(min(bathy + 10, 200), 0)
    ax.set_title(f"S{i+1} ({s_lat:.3f}, {s_lon:.3f}) bathy={bathy:.0f}m",
                  fontsize=9)
    ax.set_xlabel("velocity (cm/s)")
    if i % 4 == 0:
        ax.set_ylabel("depth (m)")
    ax.grid(alpha=0.3)
    if i == 0:
        ax.legend(fontsize=8, loc="lower right")

fig.suptitle(
    "Vertical current profiles — SalishSeaCast 2023-04, 8 hand-picked stations\n"
    "Shaded = 10-90 percentile over month; dotted green = current depth ladder "
    "(0.5/5/10/20/50 m); black line = bathymetry",
    fontsize=11, y=1.0,
)
fig.tight_layout()
out = Path(__file__).parent / "figures" / "diag_vertical_profiles.png"
fig.savefig(out, dpi=120, bbox_inches="tight")
plt.close(fig)
print(f"wrote {out}")
