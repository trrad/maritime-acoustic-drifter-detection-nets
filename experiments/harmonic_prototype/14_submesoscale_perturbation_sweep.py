"""Question (b2): how does unresolved submesoscale variability degrade
station-keeping, and how much does a fin compensate?

Controller always uses the mesoscale truth as its knowledge — the
"best compressed prior" ceiling. Dynamics use mesoscale + a synthetic
submesoscale perturbation field of controlled RMS amplitude σ.

Sweeps:
  σ ∈ {0, 2, 5, 10, 20} cm/s
  V_max ∈ {0, 5 cm/s}  (ballast-only vs glide-assist fin)

Same 54 stations as the V_max sweep so results are comparable.

The submesoscale noise is correlated at ~1 km / ~6 h — rough model of
unresolved coastal-strait energy. The controller has no chance to
anticipate it (no local current sensor in this setup).

Output: figures/17_submesoscale_sweep.png with:
  - mean %-within envelope vs σ (curves per V_max)
  - rough-station-keeping count vs σ (curves per V_max)
  - distribution of ctrl_mean per (σ, V_max)
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_submesoscale_field  # type: ignore[import-not-found]
from truth_field import build_truth_field, distance_m  # type: ignore[import-not-found]


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

SIGMA_SWEEP_MS = [0.0, 0.02, 0.05, 0.10, 0.20]
V_MAX_SWEEP_MS = [0.0, 0.05]  # ballast-only vs glide-assist
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def run_one_station(
    truth, submeso,
    station_lat: float, station_lon: float,
    depth_set: list[float],
    v_max_ms: float,
) -> dict:
    """Controller uses `truth` (mesoscale, perfect knowledge).
    Dynamics use truth + submeso perturbation — the reality gap."""
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
        thrust_v_max_ms=v_max_ms,
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        u_t, v_t = truth.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(u_t) and np.isfinite(v_t)):
            return float("nan"), float("nan")
        u_s, v_s = submeso.sample(lat, lon, depth_m, t_sec)
        return u_t + u_s, v_t + v_s

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
    cur_thrust = (0.0, 0.0)
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            chosen, thrust, _ = keeper.choose_action(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            cur_thrust = thrust
            last_decision = t_sec
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current,
                     w_z_max_ms=W_Z_MAX_MS,
                     thrust_uv_ms=cur_thrust)
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
    print("=== Question (b2): submesoscale perturbation sweep ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    print(f"building truth ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  built in {time.time() - t0:.1f}s")

    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"candidate stations: {len(candidates)}")

    # Sweep.
    results: dict[tuple[float, float], list[dict]] = {}
    for sigma in SIGMA_SWEEP_MS:
        print(f"\n--- σ = {sigma*100:.0f} cm/s ---")
        t0 = time.time()
        submeso = build_submesoscale_field(
            ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET,
            target_sigma_ms=sigma,
        )
        print(f"  submesoscale field built in {time.time() - t0:.1f}s")

        for v_max in V_MAX_SWEEP_MS:
            t_run = time.time()
            rs: list[dict] = []
            for gy, gx in candidates:
                s_lat = float(truth.lat_axis[gy])
                s_lon = float(truth.lon_axis[gx])
                s_bathy = float(bathy_grid[gy, gx])
                d_set = depth_set_for_bathy(s_bathy)
                if len(d_set) < 2:
                    continue
                u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
                if not (np.isfinite(u0) and np.isfinite(v0)):
                    continue
                r = run_one_station(truth, submeso, s_lat, s_lon, d_set, v_max)
                r["bathy_m"] = s_bathy
                rs.append(r)
            results[(sigma, v_max)] = rs
            n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
            mean_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
            mean_1k = float(np.mean([r["envelope_fracs"][1000.0] for r in rs]))
            mean_mean = float(np.mean([r["ctrl_mean_m"] for r in rs]))
            print(f"  V_max={v_max*100:.0f} cm/s  rough={n_rough}/{len(rs)}  "
                  f"%<500m={mean_500*100:.0f}%  %<1km={mean_1k*100:.0f}%  "
                  f"ctrl_mean={mean_mean:.0f}m  ({time.time() - t_run:.1f}s)")

    # --- Print aggregate table ---
    print()
    print("=== aggregate ===")
    print(f"{'σ (cm/s)':>8}  {'V_max (cm/s)':>13}  {'rough':>6}  "
          + "  ".join(f"{'%<'+str(int(e))+'m':>8}" for e in ENVELOPES_M)
          + f"  {'ctrl_mean':>10}")
    n_total = len(results[(SIGMA_SWEEP_MS[0], V_MAX_SWEEP_MS[0])])
    for sigma in SIGMA_SWEEP_MS:
        for v_max in V_MAX_SWEEP_MS:
            rs = results[(sigma, v_max)]
            n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
            env_cells = "  ".join(
                f"{np.mean([r['envelope_fracs'][e] for r in rs])*100:>7.0f}%"
                for e in ENVELOPES_M
            )
            mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
            print(f"  {sigma*100:>5.0f}     {v_max*100:>9.0f}    "
                  f"{n_rough:>3}/{n_total:<3}   {env_cells}  {mm:>8.0f}m")

    # --- Plot ---
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    sigma_pct = [s * 100 for s in SIGMA_SWEEP_MS]

    ax = axes[0, 0]
    for v_max in V_MAX_SWEEP_MS:
        pct_500 = [
            np.mean([r["envelope_fracs"][500.0] for r in results[(s, v_max)]]) * 100
            for s in SIGMA_SWEEP_MS
        ]
        pct_1k = [
            np.mean([r["envelope_fracs"][1000.0] for r in results[(s, v_max)]]) * 100
            for s in SIGMA_SWEEP_MS
        ]
        pct_2k = [
            np.mean([r["envelope_fracs"][2000.0] for r in results[(s, v_max)]]) * 100
            for s in SIGMA_SWEEP_MS
        ]
        ax.plot(sigma_pct, pct_500, "o-", label=f"≤500m (V={v_max*100:.0f})",
                alpha=0.9, lw=1.4)
        ax.plot(sigma_pct, pct_1k, "s-", label=f"≤1km (V={v_max*100:.0f})",
                alpha=0.7, lw=1.2)
        ax.plot(sigma_pct, pct_2k, "^-", label=f"≤2km (V={v_max*100:.0f})",
                alpha=0.5, lw=1.0)
    ax.set_xlabel("submesoscale σ (cm/s)")
    ax.set_ylabel("mean %-within envelope (across stations)")
    ax.set_title("envelope success vs reality-gap σ")
    ax.legend(fontsize=7, ncol=2, loc="best")
    ax.grid(alpha=0.3)

    ax = axes[0, 1]
    for v_max in V_MAX_SWEEP_MS:
        n_rough = [
            sum(1 for r in results[(s, v_max)] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
            for s in SIGMA_SWEEP_MS
        ]
        ax.plot(sigma_pct, n_rough, "-o", lw=1.8,
                label=f"V_max={v_max*100:.0f} cm/s")
    ax.axhline(n_total, ls=":", color="gray", alpha=0.5, label=f"max possible ({n_total})")
    ax.set_xlabel("submesoscale σ (cm/s)")
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m")
    ax.set_title(f"rough station-keeping count vs σ")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    ax = axes[1, 0]
    for v_max in V_MAX_SWEEP_MS:
        mm = [
            np.mean([r["ctrl_mean_m"] for r in results[(s, v_max)]])
            for s in SIGMA_SWEEP_MS
        ]
        ax.plot(sigma_pct, mm, "-o", lw=1.8,
                label=f"V_max={v_max*100:.0f} cm/s")
    ax.set_xlabel("submesoscale σ (cm/s)")
    ax.set_ylabel("mean ctrl_mean distance (m, across stations)")
    ax.set_title("mean excursion vs σ")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    ax = axes[1, 1]
    # Box plot of ctrl_mean at each (σ, V_max).
    labels = []
    data = []
    for sigma in SIGMA_SWEEP_MS:
        for v_max in V_MAX_SWEEP_MS:
            data.append([r["ctrl_mean_m"] for r in results[(sigma, v_max)]])
            labels.append(f"{sigma*100:.0f}cm/s\nV={v_max*100:.0f}")
    ax.boxplot(data, tick_labels=labels)
    ax.set_ylabel("ctrl_mean distance (m)")
    ax.set_title("distribution of ctrl_mean per (σ, V_max)")
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Question (b2): submesoscale reality gap sweep  "
        f"({n_total} stations, {RUN_HOURS}h, controller sees mesoscale truth only)",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "17_submesoscale_sweep.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
