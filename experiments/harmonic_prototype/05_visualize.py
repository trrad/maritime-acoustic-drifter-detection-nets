"""Produce human-reviewable matplotlib figures from the utide results.

Inputs:
    cache/utide_results_<bbox>_<months>.nc   (from script 04)
    Monthly u,v cache files                   (from script 03)

Outputs: PNG figures + an HTML index, all under figures/.

Figures:
    1. tide_timeseries_week.png     Raw u,v + reconstruction + residual, 1 week at surface, centre cell
    2. tide_spectrum.png            FFT of raw u, with M2/S2/K1/O1 freq lines
    3. m2_amp_map.png               Spatial map of M2 amplitude at surface
    4. m2_phase_map.png             Spatial map of M2 Greenwich phase at surface
    5. tidal_ellipses.png           Tidal ellipses at sample cells across bbox
    6. m2_vertical_profile.png      M2 amp and phase vs depth at centre cell
    7. bathymetry_map.png           Sanity-check map of the bbox

Usage:
    uv run --with xarray,netCDF4,numpy,matplotlib python \\
        experiments/harmonic_prototype/05_visualize.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    _CACHE_DIR,
    bbox_from_latlon,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
MONTHS = ["2023-04", "2023-05", "2023-06"]
CONSTITUENTS = ["M2", "S2", "K1", "O1"]

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)


# Standard constituent frequencies (cph for FFT overlay).
CONSTITUENT_PERIODS_HR = {"M2": 12.4206, "S2": 12.0000, "K1": 23.9345, "O1": 25.8193}


def _save(fig, name: str) -> Path:
    path = FIG_DIR / name
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[viz] wrote {path}")
    return path


def figure_bathymetry(results: xr.Dataset) -> Path:
    fig, ax = plt.subplots(figsize=(7, 5))
    bathy = results["bathymetry_m"].values
    masked = np.where(bathy > 0, bathy, np.nan)
    im = ax.imshow(
        masked, origin="lower",
        extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX),
        aspect="auto", cmap="Blues",
    )
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title("Bathymetry (m) — central Strait of Georgia bbox")
    plt.colorbar(im, ax=ax, label="depth (m)")
    return _save(fig, "01_bathymetry_map.png")


def figure_timeseries(
    results: xr.Dataset, u_da: xr.DataArray, v_da: xr.DataArray, centre_y: int, centre_x: int
) -> Path:
    """First week at surface: raw u,v + harmonic reconstruction + residual."""
    # Surface = depth index 0.
    times = u_da["time"].values
    u = u_da.isel(gridY=centre_y, gridX=centre_x, depth=0).values
    v = v_da.isel(gridY=centre_y, gridX=centre_x, depth=0).values

    # First week only for readability.
    t_hours = (times - times[0]) / np.timedelta64(1, "h")
    week_mask = t_hours < 168

    # Reconstruct from fitted constituents at this cell/depth.
    u_rec = np.zeros_like(u)
    v_rec = np.zeros_like(v)
    for ci, cname in enumerate(CONSTITUENTS):
        omega_rad_s = 2 * np.pi / (CONSTITUENT_PERIODS_HR[cname] * 3600)
        amp_u = float(results["amp_u_ms"].values[centre_y, centre_x, 0, ci])
        amp_v = float(results["amp_v_ms"].values[centre_y, centre_x, 0, ci])
        phase_u = float(results["phase_u_rad"].values[centre_y, centre_x, 0, ci])
        phase_v = float(results["phase_v_rad"].values[centre_y, centre_x, 0, ci])
        if np.isnan(amp_u):
            continue
        t_sec = t_hours * 3600
        u_rec += amp_u * np.cos(omega_rad_s * t_sec - phase_u)
        v_rec += amp_v * np.cos(omega_rad_s * t_sec - phase_v)

    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    ax = axes[0]
    ax.plot(t_hours[week_mask], u[week_mask], color="C0", label="raw u", lw=0.8)
    ax.plot(t_hours[week_mask], u_rec[week_mask], color="C1", label="reconstructed u (M2+S2+K1+O1)", lw=1.2, alpha=0.8)
    ax.set_ylabel("u (m/s)")
    ax.legend(loc="upper right", fontsize=9)
    ax.set_title(f"Surface current, 1 week — cell (gY={centre_y}, gX={centre_x})")

    ax = axes[1]
    ax.plot(t_hours[week_mask], v[week_mask], color="C0", label="raw v", lw=0.8)
    ax.plot(t_hours[week_mask], v_rec[week_mask], color="C1", label="reconstructed v (M2+S2+K1+O1)", lw=1.2, alpha=0.8)
    ax.set_ylabel("v (m/s)")
    ax.legend(loc="upper right", fontsize=9)

    ax = axes[2]
    u_resid = u - u_rec
    v_resid = v - v_rec
    ax.plot(t_hours[week_mask], u_resid[week_mask], color="C2", label="u residual", lw=0.8)
    ax.plot(t_hours[week_mask], v_resid[week_mask], color="C3", label="v residual", lw=0.8)
    ax.axhline(0, color="k", lw=0.5, alpha=0.5)
    ax.set_xlabel("hours since start")
    ax.set_ylabel("residual (m/s)")
    ax.legend(loc="upper right", fontsize=9)
    resid_rms_u = float(np.sqrt(np.mean(u_resid**2)))
    resid_rms_v = float(np.sqrt(np.mean(v_resid**2)))
    ax.text(0.02, 0.95, f"RMS residual  u={resid_rms_u:.3f} m/s  v={resid_rms_v:.3f} m/s",
            transform=ax.transAxes, fontsize=9, verticalalignment="top",
            bbox=dict(facecolor="white", alpha=0.8, edgecolor="none"))

    return _save(fig, "02_tide_timeseries_week.png")


def figure_spectrum(
    u_da: xr.DataArray, centre_y: int, centre_x: int
) -> Path:
    """FFT of raw u at centre surface, with constituent frequencies marked."""
    u = u_da.isel(gridY=centre_y, gridX=centre_x, depth=0).values
    u = u - np.mean(u)
    n = len(u)
    dt_hr = 1.0  # hourly
    freqs_cph = np.fft.rfftfreq(n, d=dt_hr)  # cycles per hour
    psd = np.abs(np.fft.rfft(u)) ** 2 / n

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.loglog(freqs_cph[1:], psd[1:], color="C0", lw=0.8)

    # Constituent freq overlay.
    for name, period_hr in CONSTITUENT_PERIODS_HR.items():
        f = 1.0 / period_hr
        ax.axvline(f, color="C1", alpha=0.6, lw=1)
        ax.text(f, ax.get_ylim()[1] * 0.5, f" {name}", color="C1", fontsize=9)
    # Overtides (M4, M6) — show where they would be.
    for name, mult in [("M4", 2), ("M6", 3)]:
        f = mult / CONSTITUENT_PERIODS_HR["M2"]
        ax.axvline(f, color="C3", alpha=0.5, lw=1, linestyle="--")
        ax.text(f, ax.get_ylim()[1] * 0.2, f" {name}", color="C3", fontsize=9)

    ax.set_xlabel("frequency (cph)")
    ax.set_ylabel("PSD (m²/s² · hr)")
    ax.set_title("Surface u PSD — centre cell, 3 months")
    ax.grid(True, which="both", alpha=0.3)
    return _save(fig, "03_tide_spectrum.png")


def figure_amp_map(results: xr.Dataset, constituent: str = "M2") -> Path:
    ci = list(results["constituent"].values).index(constituent)
    # Depth index 0 = surface.
    # Use sqrt(amp_u^2 + amp_v^2) as the magnitude of the tidal ellipse vector.
    amp_u = results["amp_u_ms"].values[:, :, 0, ci]
    amp_v = results["amp_v_ms"].values[:, :, 0, ci]
    amp_mag = np.sqrt(amp_u**2 + amp_v**2)
    amp_mag = np.where(np.isfinite(amp_mag), amp_mag, np.nan)

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(
        amp_mag, origin="lower",
        extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX),
        aspect="auto", cmap="viridis",
    )
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title(f"{constituent} amplitude at surface — central Strait of Georgia")
    plt.colorbar(im, ax=ax, label="amplitude (m/s)")
    return _save(fig, f"04_{constituent.lower()}_amp_map.png")


def figure_phase_map(results: xr.Dataset, constituent: str = "M2") -> Path:
    ci = list(results["constituent"].values).index(constituent)
    g_deg = results["g_deg"].values[:, :, 0, ci]
    g_deg = np.where(np.isfinite(g_deg), g_deg, np.nan)

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(
        g_deg, origin="lower",
        extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX),
        aspect="auto", cmap="twilight",
    )
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title(f"{constituent} Greenwich phase (g) at surface")
    plt.colorbar(im, ax=ax, label="phase (degrees)")
    return _save(fig, f"05_{constituent.lower()}_phase_map.png")


def figure_tidal_ellipses(results: xr.Dataset, constituent: str = "M2") -> Path:
    ci = list(results["constituent"].values).index(constituent)
    Lsmaj = results["Lsmaj"].values[:, :, 0, ci]
    Lsmin = results["Lsmin"].values[:, :, 0, ci]
    theta_deg = results["theta_deg"].values[:, :, 0, ci]
    lats = results["lat_deg"].values
    lons = results["lon_deg"].values

    n_y, n_x = Lsmaj.shape
    # Sparsify: plot an ellipse every Nth cell to keep figure readable.
    step = max(1, max(n_y, n_x) // 6)

    fig, ax = plt.subplots(figsize=(8, 6))
    theta_circle = np.linspace(0, 2 * np.pi, 64)
    # Ellipse scale: map ellipse physical units (m/s) to a small visual radius
    # in degrees. Pick so that the largest Lsmaj fits ~30% of a grid step.
    max_lsmaj = np.nanmax(np.abs(Lsmaj))
    if not np.isfinite(max_lsmaj) or max_lsmaj <= 0:
        print("[viz]   no finite amplitudes for ellipses; skipping")
        return _save(fig, f"06_{constituent.lower()}_ellipses.png")
    scale = 0.015 / max_lsmaj  # degrees per m/s; hand-tuned

    for iy in range(0, n_y, step):
        for ix in range(0, n_x, step):
            if not np.isfinite(Lsmaj[iy, ix]):
                continue
            a = Lsmaj[iy, ix] * scale
            b = Lsmin[iy, ix] * scale
            th = np.deg2rad(theta_deg[iy, ix])
            # Parametric ellipse rotated by theta.
            x = a * np.cos(theta_circle)
            y = b * np.sin(theta_circle)
            x_rot = x * np.cos(th) - y * np.sin(th)
            y_rot = x * np.sin(th) + y * np.cos(th)
            cx, cy = float(lons[iy, ix]), float(lats[iy, ix])
            ax.plot(cx + x_rot, cy + y_rot, color="C0", lw=0.8, alpha=0.75)
            ax.plot(cx, cy, marker=".", markersize=3, color="k")

    # Add reference ellipse in a corner.
    ref_a = 0.5 * scale  # 0.5 m/s
    ax.plot(LON_MIN + 0.01 + ref_a * np.cos(theta_circle),
            LAT_MIN + 0.01 + ref_a * np.sin(theta_circle),
            color="red", lw=1)
    ax.text(LON_MIN + 0.01, LAT_MIN + 0.005, "0.5 m/s", color="red", fontsize=8)

    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title(f"{constituent} tidal ellipses at surface")
    ax.set_aspect("equal", adjustable="box")
    return _save(fig, f"06_{constituent.lower()}_ellipses.png")


def figure_vertical_profile(results: xr.Dataset, centre_y: int, centre_x: int) -> Path:
    """M2 amp and phase vs depth at the centre cell."""
    depths = results["depth"].values
    # All constituents, all wet depths at centre. We visualize magnitude
    # via Lsmaj (semi-major ellipse axis) and phase via g (Greenwich lag);
    # per-component amp_u/amp_v are available in the NetCDF for anyone
    # who wants to separate them.
    Lsmaj_profile = results["Lsmaj"].values[centre_y, centre_x, :, :]
    g_profile = results["g_deg"].values[centre_y, centre_x, :, :]

    cell_bathy = float(results["bathymetry_m"].values[centre_y, centre_x])

    const_names = list(results["constituent"].values)
    fig, axes = plt.subplots(1, 2, figsize=(11, 6), sharey=True)
    ax_amp, ax_phase = axes

    for ci, name in enumerate(const_names):
        amp = Lsmaj_profile[:, ci]
        phase = g_profile[:, ci]
        mask = np.isfinite(amp)
        ax_amp.plot(np.abs(amp[mask]), depths[mask], marker="o", label=name, lw=1.2)
        ax_phase.plot(phase[mask], depths[mask], marker="o", label=name, lw=1.2)

    for ax in (ax_amp, ax_phase):
        ax.invert_yaxis()
        ax.axhline(cell_bathy, color="brown", lw=1, linestyle=":", alpha=0.6)
        ax.text(ax.get_xlim()[1], cell_bathy, f" seafloor {cell_bathy:.0f}m",
                color="brown", fontsize=8, verticalalignment="center")

    ax_amp.set_xlabel("Lsmaj amplitude (m/s)")
    ax_amp.set_ylabel("depth (m)")
    ax_amp.set_title(f"Tidal amplitude vs depth — cell ({centre_y},{centre_x})")
    ax_amp.legend(loc="lower right", fontsize=9)
    ax_amp.grid(True, alpha=0.3)

    ax_phase.set_xlabel("Greenwich phase (deg)")
    ax_phase.set_title("Phase vs depth")
    ax_phase.legend(loc="lower right", fontsize=9)
    ax_phase.grid(True, alpha=0.3)

    fig.suptitle("Baroclinic structure probe — does tidal phase shift with depth?")
    return _save(fig, "07_m2_vertical_profile.png")


def write_index(fig_paths: list[Path]) -> Path:
    html_path = FIG_DIR / "index.html"
    items = "\n".join(
        f'  <li><a href="{p.name}">{p.name}</a></li>\n  <img src="{p.name}" style="max-width:900px;display:block;margin:8px 0 24px 0;border:1px solid #ccc" />'
        for p in fig_paths
    )
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Harmonic prototype — central Strait of Georgia</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 980px; margin: 20px auto; color: #222; }}
h1 {{ font-size: 20px; }}
li {{ margin: 6px 0; }}
a {{ color: #0366d6; }}
</style>
</head>
<body>
<h1>Harmonic prototype — central Strait of Georgia</h1>
<p>utide.solve on 3 months (Apr–Jun 2023) of hourly SalishSeaCast 3D u,v at
a ~10×10 km bbox near 49.30°N, -123.70°W.</p>
<ol>
{items}
</ol>
</body>
</html>
"""
    html_path.write_text(html)
    print(f"[viz] wrote {html_path}")
    return html_path


def main() -> None:
    print("=== visualization ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    results_path = _CACHE_DIR / f"utide_results_{bbox.key()}_{'_'.join(MONTHS)}.nc"
    if not results_path.exists():
        raise FileNotFoundError(
            f"utide results not found at {results_path}. "
            "Run 04_run_utide.py first."
        )
    print(f"loading {results_path}")
    results = xr.open_dataset(results_path)

    print("loading raw u, v cache ...")
    raw = fetch_bbox_months(bbox, MONTHS, verbose=False)
    u_da = raw["u_ms"]
    v_da = raw["v_ms"]

    centre_y = int(results.attrs.get("centre_gridY_local", results.sizes["gridY"] // 2))
    centre_x = int(results.attrs.get("centre_gridX_local", results.sizes["gridX"] // 2))

    fig_paths = [
        figure_bathymetry(results),
        figure_timeseries(results, u_da, v_da, centre_y, centre_x),
        figure_spectrum(u_da, centre_y, centre_x),
        figure_amp_map(results, "M2"),
        figure_phase_map(results, "M2"),
        figure_tidal_ellipses(results, "M2"),
        figure_vertical_profile(results, centre_y, centre_x),
    ]
    write_index(fig_paths)
    print()
    print(f"open in browser: file://{FIG_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
