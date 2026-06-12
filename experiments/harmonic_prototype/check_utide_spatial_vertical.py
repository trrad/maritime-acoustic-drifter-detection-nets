"""Extended smoke: utide on the full 182-cell × 40-depth cube we already
have cached. Shows:
  - Variance explained per cell at surface (is 14% typical?)
  - Vertical profile of M2, K1, S2, O1 amplitude at the centre cell
  - Whether Foreman's extended set (M2,S2,N2,K2,K1,O1,P1,Q1,M4,MS4,M6)
    recovers meaningfully more variance than the 4-constituent default.

All from the 1-month cached smoke test — no new fetches.
"""

from __future__ import annotations

import hashlib
import time
import warnings
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import utide  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    GridBBox,
    _CACHE_DIR,
    _load_bathy,
)


SMOKE_BBOX = GridBBox(gy_min=464, gy_max=476, gx_min=261, gx_max=274)
YM = "2023-06"
FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)

CONST_SHORT = ["M2", "S2", "K1", "O1"]
CONST_LONG = ["M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1", "M4", "MS4", "M6"]


def _smoke_cache_path() -> Path:
    raw = f"ubcSSg3DuGridFields1hV21-11|{SMOKE_BBOX.key()}|{YM}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{h}.nc"


def variance_explained(u_raw: np.ndarray, u_rec: np.ndarray) -> float:
    u_raw = u_raw - np.mean(u_raw)
    resid = u_raw - (u_rec - np.mean(u_rec))
    var_raw = float(np.var(u_raw))
    if var_raw <= 0:
        return float("nan")
    return 1.0 - float(np.var(resid)) / var_raw


def fit_and_reconstruct(
    times: np.ndarray, u: np.ndarray, v: np.ndarray, lat: float, constit: list[str]
) -> tuple[dict, np.ndarray, np.ndarray]:
    coef = utide.solve(
        times, u, v, lat=lat, constit=constit,
        nodal=True, trend=False, method="ols",
        conf_int="MC", verbose=False,
    )
    rec = utide.reconstruct(times, coef, verbose=False)
    return coef, rec["u"], rec["v"]


def main() -> None:
    path = _smoke_cache_path()
    print(f"loading {path}")
    ds = xr.open_dataset(path)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]
    n_depth = ds.sizes["depth"]
    print(f"dims: gridY={n_y}, gridX={n_x}, depth={n_depth}, time={ds.sizes['time']}")

    times = ds["time"].values
    bathy_ds = _load_bathy()

    # --- 1. Surface variance-explained map, 4-constituent fit ---
    print("\n=== surface variance-explained, 4-constituent fit ===")
    var_expl_u = np.full((n_y, n_x), np.nan)
    var_expl_v = np.full((n_y, n_x), np.nan)
    bathy_map = np.full((n_y, n_x), np.nan)
    for iy in range(n_y):
        for ix in range(n_x):
            bg_y = SMOKE_BBOX.gy_min + iy
            bg_x = SMOKE_BBOX.gx_min + ix
            cell_lat = float(bathy_ds["latitude"].values[bg_y, bg_x])
            cell_bathy = float(bathy_ds["bathymetry"].values[bg_y, bg_x])
            bathy_map[iy, ix] = cell_bathy
            if cell_bathy <= 0:
                continue
            u = ds["u_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            v = ds["v_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            if not np.any(np.abs(u) > 1e-9):
                continue
            try:
                coef, u_rec, v_rec = fit_and_reconstruct(times, u, v, cell_lat, CONST_SHORT)
            except Exception as e:
                print(f"  utide failed at ({iy},{ix}): {e}")
                continue
            var_expl_u[iy, ix] = variance_explained(u, u_rec)
            var_expl_v[iy, ix] = variance_explained(v, v_rec)
    print(f"surface variance-explained u:  "
          f"min={np.nanmin(var_expl_u)*100:.1f}%  "
          f"median={np.nanmedian(var_expl_u)*100:.1f}%  "
          f"max={np.nanmax(var_expl_u)*100:.1f}%")
    print(f"surface variance-explained v:  "
          f"min={np.nanmin(var_expl_v)*100:.1f}%  "
          f"median={np.nanmedian(var_expl_v)*100:.1f}%  "
          f"max={np.nanmax(var_expl_v)*100:.1f}%")
    print(f"(for context: Race Rocks-style tidal passes typically > 70%;")
    print(f" open-ocean subtidal-dominant cells may be < 30%.)")

    # --- 2. At deepest-bathymetry cell, compare 4-const vs 11-const ---
    deepest_flat = int(np.nanargmax(bathy_map))
    deep_y, deep_x = np.unravel_index(deepest_flat, bathy_map.shape)
    deep_y, deep_x = int(deep_y), int(deep_x)
    bg_y = SMOKE_BBOX.gy_min + deep_y
    bg_x = SMOKE_BBOX.gx_min + deep_x
    cell_lat = float(bathy_ds["latitude"].values[bg_y, bg_x])
    cell_lon = float(bathy_ds["longitude"].values[bg_y, bg_x])
    cell_bathy = float(bathy_ds["bathymetry"].values[bg_y, bg_x])
    print(f"\n=== 4 vs 11 constituents at deepest cell ===")
    print(f"cell ({deep_y},{deep_x}) lat/lon ({cell_lat:.4f}, {cell_lon:.4f}) depth={cell_bathy:.1f}m")

    u = ds["u_ms"].isel(gridY=deep_y, gridX=deep_x, depth=0).values
    v = ds["v_ms"].isel(gridY=deep_y, gridX=deep_x, depth=0).values
    for label, constit in [("4 const (M2/S2/K1/O1)", CONST_SHORT),
                           ("11 const (Foreman-ish)", CONST_LONG)]:
        t0 = time.time()
        try:
            coef, u_rec, v_rec = fit_and_reconstruct(times, u, v, cell_lat, constit)
            ve_u = variance_explained(u, u_rec) * 100
            ve_v = variance_explained(v, v_rec) * 100
            print(f"  {label:30s}  ve_u={ve_u:5.1f}%  ve_v={ve_v:5.1f}%  ({time.time()-t0:.2f}s)")
            # Show individual constituent amps.
            for i, name in enumerate(coef["name"]):
                print(f"       {name}: Lsmaj={coef['Lsmaj'][i]:+.4f}  g={coef['g'][i]:6.1f}°")
        except Exception as e:
            print(f"  {label}: FAILED — {e}")

    # --- 3. Vertical profile at deepest cell (4 const) ---
    print(f"\n=== vertical profile M2 amp/phase at deepest cell ===")
    depths = ds["depth"].values
    m2_lsmaj = np.full(n_depth, np.nan)
    m2_g = np.full(n_depth, np.nan)
    for k in range(n_depth):
        if depths[k] > cell_bathy:
            continue
        u_k = ds["u_ms"].isel(gridY=deep_y, gridX=deep_x, depth=k).values
        v_k = ds["v_ms"].isel(gridY=deep_y, gridX=deep_x, depth=k).values
        if not np.any(np.abs(u_k) > 1e-9):
            continue
        try:
            coef = utide.solve(
                times, u_k, v_k, lat=cell_lat, constit=CONST_SHORT,
                nodal=True, trend=False, method="ols",
                conf_int="MC", verbose=False,
            )
            for i, name in enumerate(coef["name"]):
                if name == "M2":
                    m2_lsmaj[k] = float(abs(coef["Lsmaj"][i]))
                    m2_g[k] = float(coef["g"][i])
        except Exception:
            pass
    print("depth | M2 Lsmaj (m/s) | M2 g (deg)")
    for k in range(n_depth):
        if np.isfinite(m2_lsmaj[k]):
            print(f"  {depths[k]:7.2f} | {m2_lsmaj[k]:.4f}        | {m2_g[k]:+7.2f}")

    # --- 4. Plot vertical profile + variance-explained map ---
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    ax = axes[0]
    mask = np.isfinite(m2_lsmaj)
    ax.plot(m2_lsmaj[mask], depths[mask], marker="o", color="C0")
    ax.invert_yaxis()
    ax.axhline(cell_bathy, color="brown", linestyle=":", alpha=0.6)
    ax.text(ax.get_xlim()[1] * 0.6, cell_bathy, f" seafloor {cell_bathy:.0f}m",
            color="brown", fontsize=9, verticalalignment="center")
    ax.set_xlabel("M2 Lsmaj (m/s)")
    ax.set_ylabel("depth (m)")
    ax.set_title(f"M2 amplitude vs depth\ncell ({cell_lat:.3f},{cell_lon:.3f}) bathy={cell_bathy:.0f}m")
    ax.grid(alpha=0.3)

    ax = axes[1]
    mask2 = np.isfinite(m2_g)
    ax.plot(m2_g[mask2], depths[mask2], marker="o", color="C1")
    ax.invert_yaxis()
    ax.axhline(cell_bathy, color="brown", linestyle=":", alpha=0.6)
    ax.set_xlabel("M2 Greenwich phase (deg)")
    ax.set_ylabel("depth (m)")
    ax.set_title("M2 phase vs depth\n(any shift → baroclinic tide)")
    ax.grid(alpha=0.3)

    ax = axes[2]
    im = ax.imshow(var_expl_u * 100, origin="lower", cmap="RdYlGn",
                   vmin=0, vmax=100)
    ax.set_title(f"Surface u variance explained by\n4 constituents (%) — {YM}")
    ax.set_xlabel("gridX (local)")
    ax.set_ylabel("gridY (local)")
    plt.colorbar(im, ax=ax, label="% variance explained")

    out = FIG_DIR / "smoke_vertical_and_variance.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
