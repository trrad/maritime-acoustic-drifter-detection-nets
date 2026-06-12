"""Question (a): does fin / glider-class active thrust meaningfully
improve station-keeping beyond ballast-only depth selection?

Sweeps V_max ∈ {0.00, 0.02, 0.05, 0.10} m/s on the expanded bbox,
72-hour runs, perfect knowledge. Re-uses StationKeeper + ballast_dynamics
with thrust enabled.

V_max = 0      : depth-only control (Phase A behaviour — the baseline)
V_max = 2 cm/s : fin-flick territory (low-power add-on)
V_max = 5 cm/s : Argo-profiler-scale glide assist
V_max = 10 cm/s: glider-class propulsion (Slocum, Seaglider — larger, pricier)

Produces:
  - figures/16_fin_thrust_sweep.png (aggregate + spatial)
  - prints per-V_max aggregate stats

The question is both "how much does thrust help" (aggregate) and "where
does thrust help most" (spatial). Cells where depth selection alone is
already close to optimal won't benefit much; cells in unidirectional
outflow channels where no depth opposes the current should benefit
disproportionately from even a modest fin.
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

V_MAX_SWEEP_MS = [0.00, 0.02, 0.05, 0.10]
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def run_one_station(
    truth,
    station_lat: float, station_lon: float,
    depth_set: list[float],
    v_max_ms: float,
) -> dict:
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
        thrust_v_max_ms=v_max_ms,
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
    thrusts_u = np.zeros(n_steps + 1)
    thrusts_v = np.zeros(n_steps + 1)
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
        thrusts_u[i + 1], thrusts_v[i + 1] = cur_thrust

    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    # NaN guard: controller can't sample if truth is NaN at current pos.
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        if len(last) > 0:
            dists = np.where(valid, dists, dists[last[-1]])
        else:
            dists = np.full_like(dists, np.inf)

    thrust_mag = np.hypot(thrusts_u, thrusts_v)
    # Saturation fraction — how often the controller wanted more than V_max.
    if v_max_ms > 0:
        sat_frac = float((thrust_mag >= v_max_ms * 0.99).mean())
        sat_level = float(thrust_mag.mean() / v_max_ms)
    else:
        sat_frac = 0.0
        sat_level = 0.0
    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    return {
        "station_lat": station_lat, "station_lon": station_lon,
        "lats": lats, "lons": lons,
        "dists_m": dists,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "thrust_mag_mean": float(thrust_mag.mean()),
        "thrust_mag_max": float(thrust_mag.max()),
        "thrust_duty_cycle": float((thrust_mag > 1e-6).mean()),
        "thrust_saturation_frac": sat_frac,
        "thrust_saturation_level": sat_level,
    }


def main() -> None:
    print("=== Question (a): fin/thrust V_max sweep ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}")
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]
    print(f"bbox grid: {n_y} × {n_x} cells")

    print(f"building truth interpolators ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  built in {time.time() - t0:.1f}s")

    # Candidate stations — same layout as 12_station_keeping_grid.py expanded mode.
    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"candidate stations: {len(candidates)}")

    # Sweep.
    all_results: dict[float, list[dict]] = {}
    for v_max in V_MAX_SWEEP_MS:
        print(f"\n--- V_max = {v_max*100:.0f} cm/s ---")
        t0 = time.time()
        results: list[dict] = []
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
            r = run_one_station(truth, s_lat, s_lon, d_set, v_max)
            r["bathy_m"] = s_bathy
            results.append(r)
        dt = time.time() - t0
        all_results[v_max] = results
        n_rough = sum(1 for r in results if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mean_within = {
            e: float(np.mean([r["envelope_fracs"][e] for r in results]))
            for e in ENVELOPES_M
        }
        mean_thrust_duty = float(np.mean([r["thrust_duty_cycle"] for r in results]))
        print(f"  {len(results)} stations in {dt:.1f}s  "
              f"rough-met (max ≤ {int(ROUGH_ENVELOPE_M)}m): "
              f"{n_rough}/{len(results)}")
        for e, f in mean_within.items():
            print(f"    mean %-within {int(e)}m: {f*100:.0f}%")
        print(f"    mean thrust duty cycle (|thrust|>0): {mean_thrust_duty*100:.0f}%")

    # Cross-sweep comparison table.
    print()
    print("=== aggregate by V_max ===")
    print(f"{'V_max':>8}  {'rough/total':>12}  " + "  ".join(
        f"{'%<'+str(int(e))+'m':>8}" for e in ENVELOPES_M
    ) + f"  {'ctrl_mean':>10}  {'ctrl_max':>9}")
    for v_max in V_MAX_SWEEP_MS:
        results = all_results[v_max]
        n_rough = sum(1 for r in results if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mean_mean = float(np.mean([r["ctrl_mean_m"] for r in results]))
        mean_max = float(np.mean([r["ctrl_max_m"] for r in results]))
        env_cells = "  ".join(
            f"{np.mean([r['envelope_fracs'][e] for r in results])*100:>7.0f}%"
            for e in ENVELOPES_M
        )
        print(f"  {v_max*100:>4.0f} cm/s  "
              f"{n_rough:>4}/{len(results):<5}   {env_cells}  "
              f"{mean_mean:>8.0f}m  {mean_max:>7.0f}m")

    # --- Plot ---
    fig = plt.figure(figsize=(17, 11))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 1.2], hspace=0.35, wspace=0.3)

    # Top-left: envelope-success vs V_max curves, one per envelope.
    ax0 = fig.add_subplot(gs[0, 0])
    for e in ENVELOPES_M:
        pct = [
            np.mean([r["envelope_fracs"][e] for r in all_results[v]]) * 100
            for v in V_MAX_SWEEP_MS
        ]
        ax0.plot([v * 100 for v in V_MAX_SWEEP_MS], pct, "-o",
                 label=f"≤ {int(e)}m", lw=1.6)
    ax0.set_xlabel("V_max (cm/s)")
    ax0.set_ylabel(f"mean %-of-run within envelope  (over stations)")
    ax0.set_title(f"Envelope success vs fin capability\n({RUN_HOURS}h, perfect knowledge)")
    ax0.legend(fontsize=8, loc="best")
    ax0.grid(alpha=0.3)

    # Top-mid: ctrl_mean distribution per V_max (violin/box).
    ax1 = fig.add_subplot(gs[0, 1])
    data_means = [
        [r["ctrl_mean_m"] for r in all_results[v]] for v in V_MAX_SWEEP_MS
    ]
    ax1.boxplot(data_means, tick_labels=[f"{v*100:.0f}" for v in V_MAX_SWEEP_MS])
    ax1.set_xlabel("V_max (cm/s)")
    ax1.set_ylabel("controlled mean distance (m)")
    ax1.set_title("ctrl_mean per V_max (distribution across stations)")
    ax1.grid(alpha=0.3, axis="y")

    # Top-right: rough-success count per V_max.
    ax2 = fig.add_subplot(gs[0, 2])
    rough_counts = [
        sum(1 for r in all_results[v] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        for v in V_MAX_SWEEP_MS
    ]
    ax2.bar(range(len(V_MAX_SWEEP_MS)), rough_counts,
            color="steelblue", alpha=0.75)
    for i, c in enumerate(rough_counts):
        ax2.text(i, c + 0.3, str(c), ha="center", fontsize=10)
    ax2.set_xticks(range(len(V_MAX_SWEEP_MS)))
    ax2.set_xticklabels([f"{v*100:.0f}" for v in V_MAX_SWEEP_MS])
    ax2.set_xlabel("V_max (cm/s)")
    ax2.set_ylabel("# rough station-keeping stations")
    n_total = len(all_results[V_MAX_SWEEP_MS[0]])
    ax2.set_ylim(0, n_total + 2)
    ax2.set_title(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m (of {n_total})")
    ax2.grid(alpha=0.3, axis="y")

    # Bottom: paired scatter — ctrl_max at V=0 vs V=max (biggest V).
    ax3 = fig.add_subplot(gs[1, 0])
    rs0 = all_results[0.0]
    rs_last = all_results[V_MAX_SWEEP_MS[-1]]
    # Match by station lat/lon.
    key0 = {(r["station_lat"], r["station_lon"]): r for r in rs0}
    xs, ys = [], []
    for rl in rs_last:
        r0 = key0.get((rl["station_lat"], rl["station_lon"]))
        if r0 is None:
            continue
        xs.append(r0["ctrl_max_m"])
        ys.append(rl["ctrl_max_m"])
    ax3.scatter(xs, ys, s=35, alpha=0.7, edgecolor="black", linewidth=0.5)
    lo, hi = 0, max(max(xs), max(ys)) * 1.05
    ax3.plot([lo, hi], [lo, hi], "--", color="gray", alpha=0.6, label="y=x (no fin benefit)")
    ax3.plot([lo, hi], [lo, lo], ls=":", color="gray", alpha=0.4)
    ax3.set_xlim(lo, hi)
    ax3.set_ylim(lo, hi)
    ax3.set_xlabel(f"ctrl_max at V_max=0 (m, ballast-only)")
    ax3.set_ylabel(f"ctrl_max at V_max={V_MAX_SWEEP_MS[-1]*100:.0f} cm/s (m)")
    ax3.set_title(f"per-station excursion: ballast-only vs V_max={V_MAX_SWEEP_MS[-1]*100:.0f}cm/s\n"
                  f"points below y=x benefit from fin")
    ax3.legend(fontsize=9)
    ax3.grid(alpha=0.3)

    # Bottom-mid: spatial map of improvement factor at V_max_max.
    ax4 = fig.add_subplot(gs[1, 1])
    masked_bathy = np.where(bathy_grid > 0, bathy_grid, np.nan)
    ax4.imshow(masked_bathy, origin="lower", cmap="Blues", alpha=0.25,
               extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
    # Improvement = ctrl_max(0) / ctrl_max(V_max). Higher is better.
    improvement = []
    lats_imp = []
    lons_imp = []
    for rl in rs_last:
        r0 = key0.get((rl["station_lat"], rl["station_lon"]))
        if r0 is None or rl["ctrl_max_m"] <= 0:
            continue
        improvement.append(r0["ctrl_max_m"] / max(rl["ctrl_max_m"], 1.0))
        lats_imp.append(rl["station_lat"])
        lons_imp.append(rl["station_lon"])
    sc = ax4.scatter(lons_imp, lats_imp, c=improvement,
                     cmap="RdYlGn", vmin=1.0, vmax=5.0,
                     s=80, edgecolor="black", linewidth=0.5)
    ax4.set_xlabel("Longitude (°)")
    ax4.set_ylabel("Latitude (°)")
    ax4.set_title(f"Improvement factor: ctrl_max(0) / ctrl_max({V_MAX_SWEEP_MS[-1]*100:.0f}cm/s)\n"
                  "higher = fin helps more")
    ax4.grid(alpha=0.25)
    plt.colorbar(sc, ax=ax4, shrink=0.8)

    # Bottom-right: is the controller thrust-limited?
    # Two bars per V_max:
    #   - mean |thrust| actually used (cm/s), across stations
    #   - the V_max budget itself, for reference — tall dashed line
    # Saturation fraction (fraction of ticks at |thrust| ≈ V_max) overlaid
    # as a second-axis line; 100% = always saturated = would use more if given.
    ax5 = fig.add_subplot(gs[1, 2])
    xs = np.arange(len(V_MAX_SWEEP_MS[1:]))
    mean_used = [
        np.mean([r["thrust_mag_mean"] for r in all_results[v]]) * 100
        for v in V_MAX_SWEEP_MS[1:]
    ]
    v_budget = [v * 100 for v in V_MAX_SWEEP_MS[1:]]
    ax5.bar(xs - 0.17, v_budget, width=0.32, color="lightgray",
            edgecolor="black", label="V_max budget")
    ax5.bar(xs + 0.17, mean_used, width=0.32, color="C0",
            alpha=0.85, label="mean |thrust| used")
    for i, (bud, used) in enumerate(zip(v_budget, mean_used)):
        ax5.text(i + 0.17, used + 0.15, f"{used:.1f}", ha="center", fontsize=8)
        ax5.text(i - 0.17, bud + 0.15, f"{bud:.0f}", ha="center", fontsize=8)
    ax5.set_xticks(xs)
    ax5.set_xticklabels([f"{int(v*100)}" for v in V_MAX_SWEEP_MS[1:]])
    ax5.set_xlabel("V_max (cm/s)")
    ax5.set_ylabel("cm/s")
    ax5.set_title("Is the controller thrust-limited?\n"
                  "bar gap small ⇒ wants more; large ⇒ has slack")
    ax5.legend(fontsize=9, loc="upper left")
    ax5.grid(alpha=0.3, axis="y")

    # Secondary axis: saturation fraction (% of ticks at V_max).
    ax5b = ax5.twinx()
    sat_frac = [
        np.mean([r["thrust_saturation_frac"] for r in all_results[v]]) * 100
        for v in V_MAX_SWEEP_MS[1:]
    ]
    ax5b.plot(xs, sat_frac, "o-", color="C3", lw=1.6, markersize=6,
              label="% time at V_max")
    ax5b.set_ylabel("% time saturated (red line)", color="C3")
    ax5b.tick_params(axis="y", colors="C3")
    ax5b.set_ylim(0, 105)

    fig.suptitle(
        f"Question (a): fin/thrust V_max sweep  "
        f"({len(all_results[0.0])} stations, {RUN_HOURS}h, perfect knowledge)",
        fontsize=13, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "16_fin_thrust_sweep.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
