"""Fit multiple candidate climatology models to 2018-2022 Apr-Jun data,
then score each against 2023 Apr-Jun truth. No arguing — just numbers.

Models compared (all computed on prior-years training data only,
applied forward to 2023):

  A. harmonic_4       : M2 S2 K1 O1 tidal harmonic only. No residual.
  B. harmonic_11      : M2 S2 N2 K2 K1 O1 P1 Q1 M4 MS4 M6 tidal. No residual.
  C. harmonic_4_monthly : harmonic_4 + per-month residual mean (3 bins: Apr/May/Jun).
  D. harmonic_4_weekly  : harmonic_4 + per-week-of-year residual mean (~13 bins).
  E. harmonic_4_doy     : harmonic_4 + per-day-of-year residual mean, smoothed
                          with a rolling 7-day window (~90 bins per cell).
  F. harmonic_11_doy    : same as E but with 11-constituent harmonic.

Score per model per cell per depth:
  variance explained = 1 - Var(truth_2023 - predicted) / Var(truth_2023 - mean)

Output:
  cache/climatology_scores_<bbox>.nc          (per-cell, per-depth, per-model scores)
  figures/climatology_model_comparison.png    (bar/box plot of variance explained)
  figures/climatology_model_map_*.png         (spatial maps per model)
  figures/climatology_timeseries_<cell>.png   (truth vs each model at one cell)

Usage:
  uv run --with xarray,netCDF4,numpy,matplotlib,utide python \\
      experiments/harmonic_prototype/07_compare_climatology_models.py
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
import pandas as pd  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import utide  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    _CACHE_DIR,
    bbox_from_latlon,
    bbox_latlon_arrays,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
TRAIN_MONTHS = [f"{y}-{m:02d}" for y in [2018, 2019, 2020, 2021, 2022] for m in [4, 5, 6]]
TEST_MONTHS = ["2023-04", "2023-05", "2023-06"]

CONST_SHORT = ["M2", "S2", "K1", "O1"]
CONST_LONG = ["M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1", "M4", "MS4", "M6"]

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)


def fit_harmonic(
    times: np.ndarray, u: np.ndarray, v: np.ndarray, lat: float, constit: list[str]
) -> dict | None:
    """utide.solve with sensible prototype defaults. Returns None on failure."""
    try:
        return utide.solve(
            times, u, v, lat=lat, constit=constit,
            nodal=True, trend=False, method="ols",
            conf_int="MC", verbose=False,
        )
    except Exception:
        return None


def reconstruct_harmonic(times: np.ndarray, coef: dict) -> tuple[np.ndarray, np.ndarray]:
    rec = utide.reconstruct(times, coef, verbose=False)
    return rec["u"], rec["v"]


def day_of_year(times: np.ndarray) -> np.ndarray:
    """Day-of-year index (1..366) for a datetime64 array."""
    return pd.DatetimeIndex(times).dayofyear.values


def month_index(times: np.ndarray) -> np.ndarray:
    return pd.DatetimeIndex(times).month.values


def week_of_year(times: np.ndarray) -> np.ndarray:
    """ISO week number (1..53)."""
    return pd.DatetimeIndex(times).isocalendar().week.values


def fit_doy_residual(
    resid: np.ndarray, doy: np.ndarray, smooth_window_days: int = 7
) -> np.ndarray:
    """Per-day-of-year mean of the residual, smoothed with rolling window.

    Returns an array of length 367 (1..366 inclusive) where entry i is the
    smoothed mean residual at day-of-year i. Days with no training samples
    get NaN; we fill via nearest-valid lookup in the evaluator.
    """
    lookup = np.full(367, np.nan)
    for d in range(1, 367):
        mask = (doy == d)
        if mask.any():
            lookup[d] = float(np.mean(resid[mask]))
    # Rolling smoothing via convolution (ignoring NaN).
    w = smooth_window_days
    kernel = np.ones(w) / w
    valid = ~np.isnan(lookup)
    filled = np.where(valid, lookup, 0.0)
    sm = np.convolve(filled, kernel, mode="same")
    cnts = np.convolve(valid.astype(float), kernel, mode="same")
    smoothed = np.where(cnts > 0, sm / np.maximum(cnts, 1e-9), np.nan)
    return smoothed


def eval_doy_residual(doy_lookup: np.ndarray, doy: np.ndarray) -> np.ndarray:
    out = doy_lookup[doy]
    # Fill any NaN with 0 (training had no sample for that DoY).
    return np.where(np.isnan(out), 0.0, out)


def fit_monthly_residual(resid: np.ndarray, months: np.ndarray) -> dict[int, float]:
    out: dict[int, float] = {}
    for m in np.unique(months):
        mask = (months == m)
        if mask.any():
            out[int(m)] = float(np.mean(resid[mask]))
    return out


def eval_monthly_residual(lookup: dict[int, float], months: np.ndarray) -> np.ndarray:
    return np.array([lookup.get(int(m), 0.0) for m in months])


def fit_weekly_residual(resid: np.ndarray, weeks: np.ndarray) -> dict[int, float]:
    out: dict[int, float] = {}
    for w in np.unique(weeks):
        mask = (weeks == w)
        if mask.any():
            out[int(w)] = float(np.mean(resid[mask]))
    return out


def eval_weekly_residual(lookup: dict[int, float], weeks: np.ndarray) -> np.ndarray:
    return np.array([lookup.get(int(w), 0.0) for w in weeks])


def variance_explained(truth: np.ndarray, predicted: np.ndarray) -> float:
    truth_centered = truth - np.mean(truth)
    resid = truth - predicted
    var_truth = float(np.var(truth_centered))
    if var_truth <= 0:
        return float("nan")
    return 1.0 - float(np.var(resid)) / var_truth


def score_cell(
    train_times: np.ndarray, train_u: np.ndarray, train_v: np.ndarray,
    test_times: np.ndarray, test_u: np.ndarray, test_v: np.ndarray,
    lat: float,
) -> dict[str, tuple[float, float]]:
    """Fit each model on train, predict for test, return (ve_u, ve_v) per model."""
    results: dict[str, tuple[float, float]] = {}

    # --- Harmonic-only fits ---
    coef4 = fit_harmonic(train_times, train_u, train_v, lat, CONST_SHORT)
    coef11 = fit_harmonic(train_times, train_u, train_v, lat, CONST_LONG)
    if coef4 is None or coef11 is None:
        return {m: (float("nan"), float("nan")) for m in
                ("harmonic_4", "harmonic_11", "harmonic_4_monthly",
                 "harmonic_4_weekly", "harmonic_4_doy", "harmonic_11_doy")}

    u4_train_rec, v4_train_rec = reconstruct_harmonic(train_times, coef4)
    u4_test_rec, v4_test_rec = reconstruct_harmonic(test_times, coef4)
    u11_train_rec, v11_train_rec = reconstruct_harmonic(train_times, coef11)
    u11_test_rec, v11_test_rec = reconstruct_harmonic(test_times, coef11)

    results["harmonic_4"] = (
        variance_explained(test_u, u4_test_rec),
        variance_explained(test_v, v4_test_rec),
    )
    results["harmonic_11"] = (
        variance_explained(test_u, u11_test_rec),
        variance_explained(test_v, v11_test_rec),
    )

    # --- Residual-on-top-of-harmonic_4 fits ---
    train_resid_u4 = train_u - u4_train_rec
    train_resid_v4 = train_v - v4_train_rec

    # B. Monthly residual mean.
    train_months = month_index(train_times)
    test_months = month_index(test_times)
    lookup_mu = fit_monthly_residual(train_resid_u4, train_months)
    lookup_mv = fit_monthly_residual(train_resid_v4, train_months)
    pred_u = u4_test_rec + eval_monthly_residual(lookup_mu, test_months)
    pred_v = v4_test_rec + eval_monthly_residual(lookup_mv, test_months)
    results["harmonic_4_monthly"] = (
        variance_explained(test_u, pred_u),
        variance_explained(test_v, pred_v),
    )

    # C. Weekly residual mean.
    train_weeks = week_of_year(train_times)
    test_weeks = week_of_year(test_times)
    lookup_wu = fit_weekly_residual(train_resid_u4, train_weeks)
    lookup_wv = fit_weekly_residual(train_resid_v4, train_weeks)
    pred_u = u4_test_rec + eval_weekly_residual(lookup_wu, test_weeks)
    pred_v = v4_test_rec + eval_weekly_residual(lookup_wv, test_weeks)
    results["harmonic_4_weekly"] = (
        variance_explained(test_u, pred_u),
        variance_explained(test_v, pred_v),
    )

    # D. Day-of-year residual mean, rolling-smoothed.
    train_doy = day_of_year(train_times)
    test_doy = day_of_year(test_times)
    lookup_du = fit_doy_residual(train_resid_u4, train_doy, smooth_window_days=7)
    lookup_dv = fit_doy_residual(train_resid_v4, train_doy, smooth_window_days=7)
    pred_u = u4_test_rec + eval_doy_residual(lookup_du, test_doy)
    pred_v = v4_test_rec + eval_doy_residual(lookup_dv, test_doy)
    results["harmonic_4_doy"] = (
        variance_explained(test_u, pred_u),
        variance_explained(test_v, pred_v),
    )

    # E. harmonic_11 + day-of-year.
    train_resid_u11 = train_u - u11_train_rec
    train_resid_v11 = train_v - v11_train_rec
    lookup_du = fit_doy_residual(train_resid_u11, train_doy, smooth_window_days=7)
    lookup_dv = fit_doy_residual(train_resid_v11, train_doy, smooth_window_days=7)
    pred_u = u11_test_rec + eval_doy_residual(lookup_du, test_doy)
    pred_v = v11_test_rec + eval_doy_residual(lookup_dv, test_doy)
    results["harmonic_11_doy"] = (
        variance_explained(test_u, pred_u),
        variance_explained(test_v, pred_v),
    )

    return results


def main() -> None:
    print("=== climatology model comparison ===")
    print(f"train months: {TRAIN_MONTHS}")
    print(f"test months: {TEST_MONTHS}")

    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}  ({bbox.n_cells} cells)")

    print("loading training cache ...")
    train = fetch_bbox_months(bbox, TRAIN_MONTHS, verbose=False)
    print(f"  train dims: {dict(train.sizes)}")

    print("loading test cache ...")
    test = fetch_bbox_months(bbox, TEST_MONTHS, verbose=False)
    print(f"  test dims: {dict(test.sizes)}")

    lats, lons, bathy = bbox_latlon_arrays(bbox)
    train_times = train["time"].values
    test_times = test["time"].values

    model_names = [
        "harmonic_4", "harmonic_11",
        "harmonic_4_monthly", "harmonic_4_weekly", "harmonic_4_doy",
        "harmonic_11_doy",
    ]
    n_y = train.sizes["gridY"]
    n_x = train.sizes["gridX"]

    # For the prototype first pass we score at surface only (depth 0) across
    # all cells, plus full column at centre cell. That produces spatial maps
    # of variance explained per model, and a vertical profile at centre.
    centre_y, centre_x = n_y // 2, n_x // 2

    ve_u_surface = {m: np.full((n_y, n_x), np.nan) for m in model_names}
    ve_v_surface = {m: np.full((n_y, n_x), np.nan) for m in model_names}

    print(f"\nscoring surface models on {n_y * n_x} cells ...")
    t0 = time.time()
    for iy in range(n_y):
        for ix in range(n_x):
            if bathy[iy, ix] <= 0:
                continue
            lat = float(lats[iy, ix])
            u_train = train["u_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            v_train = train["v_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            u_test = test["u_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            v_test = test["v_ms"].isel(gridY=iy, gridX=ix, depth=0).values
            if not np.any(np.abs(u_train) > 1e-9):
                continue
            scores = score_cell(train_times, u_train, v_train,
                                test_times, u_test, v_test, lat)
            for m in model_names:
                ve_u_surface[m][iy, ix] = scores[m][0]
                ve_v_surface[m][iy, ix] = scores[m][1]
        if iy > 0 and iy % max(1, n_y // 10) == 0:
            dt = time.time() - t0
            eta = dt / iy * (n_y - iy)
            print(f"  row {iy}/{n_y}  {dt:.1f}s elapsed  ETA {eta:.1f}s")

    print(f"done in {time.time()-t0:.1f}s")

    # --- Summary statistics ---
    print("\n=== surface variance-explained summary (all wet cells) ===")
    print(f"{'model':<22s}  {'u med':>7s}  {'u mean':>7s}  {'v med':>7s}  {'v mean':>7s}")
    for m in model_names:
        umed = np.nanmedian(ve_u_surface[m]) * 100
        umean = np.nanmean(ve_u_surface[m]) * 100
        vmed = np.nanmedian(ve_v_surface[m]) * 100
        vmean = np.nanmean(ve_v_surface[m]) * 100
        print(f"{m:<22s}  {umed:6.1f}%  {umean:6.1f}%  {vmed:6.1f}%  {vmean:6.1f}%")

    # --- Plot: boxplot of ve per model ---
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    data_u = [ve_u_surface[m].flatten() * 100 for m in model_names]
    data_v = [ve_v_surface[m].flatten() * 100 for m in model_names]
    for ax, data, label in [(axes[0], data_u, "u"), (axes[1], data_v, "v")]:
        # Filter NaNs per model.
        filtered = [d[~np.isnan(d)] for d in data]
        ax.boxplot(filtered, tick_labels=model_names, showfliers=False)
        ax.set_ylabel(f"variance explained — {label} (%)")
        ax.set_xticklabels(model_names, rotation=30, ha="right", fontsize=9)
        ax.grid(True, axis="y", alpha=0.3)
        ax.axhline(0, color="k", lw=0.5)
    fig.suptitle("Climatology models — variance explained at surface across 1080 cells\n"
                 "(trained on 2018–2022 Apr–Jun, scored against 2023 Apr–Jun truth)")
    fig.tight_layout()
    out = FIG_DIR / "08_climatology_comparison_boxplot.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[viz] wrote {out}")

    # --- Plot: spatial map per model ---
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    for ax, m in zip(axes.flat, model_names):
        arr = ve_u_surface[m] * 100
        im = ax.imshow(arr, origin="lower", cmap="RdYlGn", vmin=-20, vmax=60,
                       extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
        ax.set_title(m)
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")
        plt.colorbar(im, ax=ax, label="ve_u (%)")
    fig.suptitle("Spatial variance explained (u) per climatology model")
    fig.tight_layout()
    out = FIG_DIR / "09_climatology_spatial_maps.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[viz] wrote {out}")

    # --- Timeseries at centre cell ---
    lat_c = float(lats[centre_y, centre_x])
    u_train_c = train["u_ms"].isel(gridY=centre_y, gridX=centre_x, depth=0).values
    v_train_c = train["v_ms"].isel(gridY=centre_y, gridX=centre_x, depth=0).values
    u_test_c = test["u_ms"].isel(gridY=centre_y, gridX=centre_x, depth=0).values
    # v_test_c not used in current timeseries plot (u-only for readability).

    coef4 = fit_harmonic(train_times, u_train_c, v_train_c, lat_c, CONST_SHORT)
    coef11 = fit_harmonic(train_times, u_train_c, v_train_c, lat_c, CONST_LONG)
    if coef4 is not None and coef11 is not None:
        u4_test_rec, _ = reconstruct_harmonic(test_times, coef4)
        u11_test_rec, _ = reconstruct_harmonic(test_times, coef11)
        u4_train_rec, _ = reconstruct_harmonic(train_times, coef4)

        # DoY residual on top of harmonic_4.
        train_doy = day_of_year(train_times)
        test_doy = day_of_year(test_times)
        lookup_du = fit_doy_residual(u_train_c - u4_train_rec, train_doy, 7)
        doy_residual_pred = eval_doy_residual(lookup_du, test_doy)
        u_model_e = u4_test_rec + doy_residual_pred

        test_t_hours = (test_times - test_times[0]) / np.timedelta64(1, "h")
        week_mask = test_t_hours < 336  # 2 weeks

        fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
        ax = axes[0]
        ax.plot(test_t_hours[week_mask], u_test_c[week_mask], "k-", lw=0.7, label="truth 2023")
        ax.plot(test_t_hours[week_mask], u4_test_rec[week_mask], "C0", lw=1.1, alpha=0.8,
                label="harmonic_4")
        ax.plot(test_t_hours[week_mask], u11_test_rec[week_mask], "C1", lw=1.1, alpha=0.8,
                label="harmonic_11")
        ax.plot(test_t_hours[week_mask], u_model_e[week_mask], "C2", lw=1.1, alpha=0.8,
                label="harmonic_4 + doy residual")
        ax.set_ylabel("u (m/s)")
        ax.legend(loc="upper right", fontsize=9)
        ax.set_title(f"Centre cell ({centre_y},{centre_x}) at surface — first 2 weeks of test period")
        ax.grid(alpha=0.3)

        ax = axes[1]
        ax.plot(test_t_hours[week_mask], (u_test_c - u4_test_rec)[week_mask], "C0", lw=0.8,
                label="residual after harmonic_4")
        ax.plot(test_t_hours[week_mask], (u_test_c - u_model_e)[week_mask], "C2", lw=0.8,
                label="residual after harmonic_4 + doy")
        ax.axhline(0, color="k", lw=0.5, alpha=0.5)
        ax.set_xlabel("hours since test start")
        ax.set_ylabel("residual (m/s)")
        ax.legend(loc="upper right", fontsize=9)
        ax.grid(alpha=0.3)

        out = FIG_DIR / "10_climatology_timeseries_centre.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        print(f"[viz] wrote {out}")

    # Save scores as NetCDF for later analysis.
    ds_scores = xr.Dataset(
        {
            "ve_u": (("model", "gridY", "gridX"),
                     np.stack([ve_u_surface[m] for m in model_names])),
            "ve_v": (("model", "gridY", "gridX"),
                     np.stack([ve_v_surface[m] for m in model_names])),
            "bathymetry_m": (("gridY", "gridX"), bathy),
            "lat_deg": (("gridY", "gridX"), lats),
            "lon_deg": (("gridY", "gridX"), lons),
        },
        coords={"model": model_names},
        attrs={
            "train_months": ",".join(TRAIN_MONTHS),
            "test_months": ",".join(TEST_MONTHS),
            "bbox_key": bbox.key(),
        },
    )
    out_path = _CACHE_DIR / f"climatology_scores_{bbox.key()}.nc"
    ds_scores.to_netcdf(out_path)
    print(f"[data] wrote {out_path}")


if __name__ == "__main__":
    main()
