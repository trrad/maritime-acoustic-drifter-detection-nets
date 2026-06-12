"""
⚠️  BROKEN FOUNDATION — DO NOT BUILD ON THIS SCRIPT
====================================================
σ_pf in this script is injected synthetic position noise independent of
any surfacing / LoRa physics. In reality σ_pf is an OUTPUT of the PF
given (surface cadence, anchor geometry, dead-reckon quality). Treating
it as a free knob decouples it from the physical mechanism that produces
it, which produced a misleading "σ_pf doesn't matter at σ_fc=20cm/s"
conclusion (because the sensitivity question being asked is malformed).

Keep for history. The right script is `20_*` (field-resolved bias
learning + dynamic surfacing, built on top of 17's real LoRa-at-surface
PF).

--- original docstring below ---

2D sensitivity sweep: PF accuracy × operational forecast error.

Published regional-NEMO forecast skill in the Salish Sea is ~20 cm/s
RMSE at 24–66 h lead (SalishSeaCast-forecast / CIOPS-SalishSea). That's
the *real* prior quality an operationally-deployed drifter would have —
NOT oracular truth and NOT last year's hindcast.

Given that, the question is: how tight does the onboard PF's position
estimate have to be for station-keeping to still work? Can a coarse
position fix compensate for a good forecast? Does a tight PF rescue a
lousy forecast?

Two axes:
  - σ_pf ∈ {0, 50, 100, 200, 500, 1000} m — injected position noise into
    the controller's perceived position. No actual PF: the σ_pf value
    IS the "PF accuracy" parameter.
  - σ_forecast ∈ {0, 5, 10, 20} cm/s — prior is truth + Gaussian-
    correlated noise (~1 km / ~3 h decorrelation) with this RMS.
    Sweeps operational-forecast-quality space.

Dynamics always use clean truth. Controller plans against the noisy prior.
Perceived position for depth choice = truth + Gaussian(σ_pf).

Output: figures/21_pf_accuracy_sensitivity.png — 2D heatmap of station-
keeping success vs (σ_forecast, σ_pf).
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from dataclasses import dataclass

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_submesoscale_field  # type: ignore[import-not-found]
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M, build_truth_field, distance_m,
)


@dataclass
class NoisyForecastPrior:
    """Prior = truth + correlated-noise field. Models operational-forecast
    residual error vs truth (e.g., 20 cm/s RMS for real ocean forecasts).
    Controller reads this as if it were the actual forecast."""

    truth: "object"
    noise: "object"

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        ut, vt = self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        return ut + un, vt + vn


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

STRIDE_Y = 10
STRIDE_X = 10
INTERIOR_MARGIN_Y = 8
INTERIOR_MARGIN_X = 8
MIN_BATHY_M = 60.0
CAP_DEPTH_MARGIN = 0.8
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

RUN_HOURS = 72
DT_SEC = 3600.0
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0

# Axis 1: injected PF position noise.
SIGMA_PF_SWEEP_M = [0.0, 50.0, 100.0, 200.0, 500.0, 1000.0]
# Axis 2: operational forecast error (prior RMS error in m/s).
# 0 = perfect; 0.05 = very good forecast; 0.10 = published typical; 0.20 = realistic.
SIGMA_FORECAST_SWEEP_MS = [0.0, 0.05, 0.10, 0.20]

ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def run_station(
    truth, prior_knowledge,
    station_lat: float, station_lon: float,
    depth_set: list[float], sigma_pf_m: float, seed: int,
) -> dict:
    """Controller uses prior_knowledge; perceived position = truth + N(0, σ_pf).
    Dynamics always use clean truth."""
    rng = np.random.default_rng(seed)
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=prior_knowledge,
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=INITIAL_DEPTH_M, depth_setpoint_m=INITIAL_DEPTH_M,
    )
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            # Inject position noise into what controller perceives.
            cos_lat = np.cos(np.deg2rad(state.lat))
            noise_lat = rng.normal(0, sigma_pf_m / EARTH_R_M)
            noise_lon = rng.normal(0, sigma_pf_m / (EARTH_R_M * cos_lat))
            perceived_lat = state.lat + noise_lat
            perceived_lon = state.lon + noise_lon
            chosen, _ = keeper.choose_depth(
                state.lat, state.lon, t_sec,
                perceived_lat=perceived_lat,
                perceived_lon=perceived_lon,
            )
            state = set_setpoint(state, chosen)
            last_decision = t_sec
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1], lons[i + 1] = state.lat, state.lon

    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        if len(last) > 0:
            dists = np.where(valid, dists, dists[last[-1]])
        else:
            dists = np.full_like(dists, np.inf)
    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    return {
        "station_lat": station_lat, "station_lon": station_lon,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
    }


def main() -> None:
    print("=== 2D sensitivity: PF accuracy × forecast error ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    print("building truth ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s")

    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"stations: {len(candidates)}")

    # 2D grid of results.
    results: dict[tuple[float, float], list[dict]] = {}

    for sigma_fc in SIGMA_FORECAST_SWEEP_MS:
        if sigma_fc == 0.0:
            prior_source = PerfectKnowledge(truth=truth)
            print(f"\n=== σ_forecast = 0 cm/s (perfect prior) ===")
        else:
            print(f"\n=== σ_forecast = {sigma_fc*100:.0f} cm/s — building noisy prior ===")
            t0 = time.time()
            noise_field = build_submesoscale_field(
                ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET,
                target_sigma_ms=sigma_fc,
                spatial_sigma_cells=2.0,
                temporal_sigma_hours=3.0,
            )
            print(f"  noise field built in {time.time() - t0:.1f}s")
            prior_source = NoisyForecastPrior(truth=truth, noise=noise_field)

        for sigma_pf in SIGMA_PF_SWEEP_M:
            t0 = time.time()
            rs: list[dict] = []
            for i, (gy, gx) in enumerate(candidates):
                s_lat = float(truth.lat_axis[gy])
                s_lon = float(truth.lon_axis[gx])
                s_bathy = float(bathy_grid[gy, gx])
                d_set = depth_set_for_bathy(s_bathy)
                if len(d_set) < 2:
                    continue
                u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
                if not (np.isfinite(u0) and np.isfinite(v0)):
                    continue
                r = run_station(truth, prior_source, s_lat, s_lon, d_set,
                                sigma_pf, seed=1000 + i)
                r["bathy_m"] = s_bathy
                rs.append(r)
            results[(sigma_fc, sigma_pf)] = rs
            n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
            mean_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
            mean_1k = float(np.mean([r["envelope_fracs"][1000.0] for r in rs]))
            print(f"  σ_pf={sigma_pf:>5.0f}m  "
                  f"rough={n_rough:>2}/{len(rs)}  "
                  f"%<500m={mean_500*100:>3.0f}%  %<1km={mean_1k*100:>3.0f}%  "
                  f"({time.time() - t0:.1f}s)")

    # --- Aggregate table ---
    n_total = len(results[(SIGMA_FORECAST_SWEEP_MS[0], SIGMA_PF_SWEEP_M[0])])
    print()
    print(f"=== aggregate: rough-station-keeping count of {n_total} ===")
    print(f"{'':>12}  " + "  ".join(f"σ_pf={int(s):>4}m" for s in SIGMA_PF_SWEEP_M))
    for sfc in SIGMA_FORECAST_SWEEP_MS:
        row = [
            sum(1 for r in results[(sfc, spf)] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
            for spf in SIGMA_PF_SWEEP_M
        ]
        label = f"σ_fc={sfc*100:.0f}cm/s"
        print(f"  {label:>12}  " + "  ".join(f"{v:>9}" for v in row))

    print()
    print(f"=== aggregate: mean %-within-500m ===")
    print(f"{'':>12}  " + "  ".join(f"σ_pf={int(s):>4}m" for s in SIGMA_PF_SWEEP_M))
    for sfc in SIGMA_FORECAST_SWEEP_MS:
        row = [
            np.mean([r["envelope_fracs"][500.0] for r in results[(sfc, spf)]]) * 100
            for spf in SIGMA_PF_SWEEP_M
        ]
        label = f"σ_fc={sfc*100:.0f}cm/s"
        print(f"  {label:>12}  " + "  ".join(f"{v:>8.0f}%" for v in row))

    # --- Plot: heatmap + curves ---
    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], hspace=0.35, wspace=0.3)

    # Heatmap: mean %-within-500m vs (σ_forecast, σ_pf).
    ax0 = fig.add_subplot(gs[0, 0])
    heatmap_500 = np.array([
        [np.mean([r["envelope_fracs"][500.0] for r in results[(sfc, spf)]]) * 100
         for spf in SIGMA_PF_SWEEP_M]
        for sfc in SIGMA_FORECAST_SWEEP_MS
    ])
    im = ax0.imshow(heatmap_500, cmap="viridis", aspect="auto", origin="lower",
                     vmin=0, vmax=60)
    for i in range(len(SIGMA_FORECAST_SWEEP_MS)):
        for j in range(len(SIGMA_PF_SWEEP_M)):
            ax0.text(j, i, f"{heatmap_500[i, j]:.0f}", ha="center",
                      va="center", color="white", fontsize=9)
    ax0.set_xticks(range(len(SIGMA_PF_SWEEP_M)))
    ax0.set_xticklabels([f"{s:.0f}" for s in SIGMA_PF_SWEEP_M])
    ax0.set_yticks(range(len(SIGMA_FORECAST_SWEEP_MS)))
    ax0.set_yticklabels([f"{s*100:.0f}" for s in SIGMA_FORECAST_SWEEP_MS])
    ax0.set_xlabel("σ_pf (m)  — PF position noise")
    ax0.set_ylabel("σ_forecast (cm/s)  — prior RMS error")
    ax0.set_title("% of 72h within 500m")
    plt.colorbar(im, ax=ax0, shrink=0.85)

    # Heatmap: rough-station-keeping count.
    ax1 = fig.add_subplot(gs[0, 1])
    heatmap_rough = np.array([
        [sum(1 for r in results[(sfc, spf)] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
         for spf in SIGMA_PF_SWEEP_M]
        for sfc in SIGMA_FORECAST_SWEEP_MS
    ])
    im = ax1.imshow(heatmap_rough, cmap="plasma", aspect="auto", origin="lower",
                     vmin=0, vmax=n_total)
    for i in range(len(SIGMA_FORECAST_SWEEP_MS)):
        for j in range(len(SIGMA_PF_SWEEP_M)):
            ax1.text(j, i, f"{heatmap_rough[i, j]}", ha="center",
                      va="center", color="white", fontsize=9)
    ax1.set_xticks(range(len(SIGMA_PF_SWEEP_M)))
    ax1.set_xticklabels([f"{s:.0f}" for s in SIGMA_PF_SWEEP_M])
    ax1.set_yticks(range(len(SIGMA_FORECAST_SWEEP_MS)))
    ax1.set_yticklabels([f"{s*100:.0f}" for s in SIGMA_FORECAST_SWEEP_MS])
    ax1.set_xlabel("σ_pf (m)")
    ax1.set_ylabel("σ_forecast (cm/s)")
    ax1.set_title(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m (of {n_total})")
    plt.colorbar(im, ax=ax1, shrink=0.85)

    # Line plot: %<500m vs σ_pf, one line per σ_forecast.
    ax2 = fig.add_subplot(gs[1, 0])
    for i, sfc in enumerate(SIGMA_FORECAST_SWEEP_MS):
        pct = [
            np.mean([r["envelope_fracs"][500.0] for r in results[(sfc, spf)]]) * 100
            for spf in SIGMA_PF_SWEEP_M
        ]
        ax2.plot(SIGMA_PF_SWEEP_M, pct, "-o", lw=1.6,
                  label=f"σ_fc={sfc*100:.0f} cm/s")
    ax2.set_xlabel("σ_pf (m)")
    ax2.set_ylabel("mean %-of-run within 500m")
    ax2.set_xscale("symlog", linthresh=50.0)
    ax2.set_title(f"%<500m vs PF accuracy, by forecast quality  ({RUN_HOURS}h)")
    ax2.legend(fontsize=9)
    ax2.grid(alpha=0.3)

    # Line plot: %<500m vs σ_forecast, one line per σ_pf.
    ax3 = fig.add_subplot(gs[1, 1])
    for spf in SIGMA_PF_SWEEP_M:
        pct = [
            np.mean([r["envelope_fracs"][500.0] for r in results[(sfc, spf)]]) * 100
            for sfc in SIGMA_FORECAST_SWEEP_MS
        ]
        ax3.plot([s * 100 for s in SIGMA_FORECAST_SWEEP_MS], pct, "-o", lw=1.6,
                  label=f"σ_pf={spf:.0f} m")
    ax3.set_xlabel("σ_forecast (cm/s)")
    ax3.set_ylabel("mean %-of-run within 500m")
    ax3.set_title(f"%<500m vs forecast quality, by PF accuracy")
    ax3.legend(fontsize=9)
    ax3.grid(alpha=0.3)

    fig.suptitle(
        f"2D sensitivity: PF position accuracy × operational forecast error  "
        f"({n_total} stations, {RUN_HOURS}h, clean-truth dynamics)",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "21_pf_accuracy_sensitivity.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
