"""Quick sanity check: run utide on the smoke-test cache (one month, small
bbox) and confirm the ellipse-form output structure matches what script 04
expects. Catches interface surprises before the full dataset lands.
"""

from __future__ import annotations

import time
import warnings

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import utide  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    GridBBox,
    _CACHE_DIR,
)


def main() -> None:
    # Smoke test bbox was created via bbox_from_latlon(49.20, 49.25, -123.60, -123.55).
    # Its cached file is already on disk; load directly.
    smoke_bbox = GridBBox(gy_min=464, gy_max=476, gx_min=261, gx_max=274)
    ym = "2023-06"
    # Construct the same hash the cache module would.
    import hashlib
    raw = f"ubcSSg3DuGridFields1hV21-11|{smoke_bbox.key()}|{ym}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    path = _CACHE_DIR / f"{h}.nc"
    if not path.exists():
        print(f"smoke cache missing ({path}); skipping")
        return

    print(f"loading {path.name}")
    ds = xr.open_dataset(path)
    print(f"dims: {dict(ds.sizes)}")

    # Pick a centre cell, surface depth, and run utide.solve.
    iy, ix = ds.sizes["gridY"] // 2, ds.sizes["gridX"] // 2
    u = ds["u_ms"].isel(gridY=iy, gridX=ix, depth=0).values
    v = ds["v_ms"].isel(gridY=iy, gridX=ix, depth=0).values
    times = ds["time"].values

    print(f"u stats: min={u.min():.3f} max={u.max():.3f} mean={u.mean():.3f} finite={np.isfinite(u).mean():.3f}")
    print(f"v stats: min={v.min():.3f} max={v.max():.3f} mean={v.mean():.3f}")
    print(f"time range: {times[0]} → {times[-1]}  ({len(times)} steps)")

    # utide needs latitude for nodal corrections.
    # Load bathy-backed lat/lon for this cell — we didn't save it in the
    # cache file but the bathy loader has it.
    from salishseacast_cache import _load_bathy
    bathy_ds = _load_bathy()
    cell_lat = float(bathy_ds["latitude"].values[smoke_bbox.gy_min + iy,
                                                 smoke_bbox.gx_min + ix])
    cell_lon = float(bathy_ds["longitude"].values[smoke_bbox.gy_min + iy,
                                                  smoke_bbox.gx_min + ix])
    cell_bathy = float(bathy_ds["bathymetry"].values[smoke_bbox.gy_min + iy,
                                                     smoke_bbox.gx_min + ix])
    print(f"cell lat/lon: ({cell_lat:.4f}, {cell_lon:.4f})  bathy={cell_bathy:.1f}m")

    print()
    print("running utide.solve (M2 S2 K1 O1) ...")
    t0 = time.time()
    coef = utide.solve(
        times, u, v,
        lat=cell_lat,
        constit=["M2", "S2", "K1", "O1"],
        nodal=True,
        trend=False,
        method="ols",
        conf_int="MC",
        verbose=False,
    )
    print(f"done in {time.time()-t0:.2f}s")

    # Dump the structure.
    print()
    print("coef keys:", list(coef.keys()))
    print()
    for i, name in enumerate(coef["name"]):
        print(f"  {name}: "
              f"Lsmaj={coef['Lsmaj'][i]:+.4f}  "
              f"Lsmin={coef['Lsmin'][i]:+.4f}  "
              f"theta={coef['theta'][i]:+.2f}°  "
              f"g={coef['g'][i]:+.2f}°")

    # Sanity: does reconstruction round-trip?
    print()
    t_rec = utide.reconstruct(times, coef, verbose=False)
    u_rec = t_rec["u"]
    v_rec = t_rec["v"]
    print(f"reconstruction shape: u_rec {u_rec.shape}, v_rec {v_rec.shape}")
    resid_u = u - u_rec
    resid_v = v - v_rec
    rms_u = float(np.sqrt(np.mean(resid_u**2)))
    rms_v = float(np.sqrt(np.mean(resid_v**2)))
    var_raw = float(np.var(u))
    var_resid = float(np.var(resid_u))
    frac_explained = 1 - var_resid / var_raw
    print(f"u:  raw RMS={float(np.sqrt(np.mean(u**2))):.3f} m/s  "
          f"residual RMS={rms_u:.3f} m/s  "
          f"variance explained {frac_explained*100:.1f}%")
    print(f"v:  residual RMS={rms_v:.3f} m/s")


if __name__ == "__main__":
    main()
