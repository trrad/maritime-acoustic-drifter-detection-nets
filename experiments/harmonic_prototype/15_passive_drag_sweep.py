"""Question (A): passive drag modulation — how much does a retractable
drogue / variable-area chute help, if it can only *slow* drift, never
steer against it?

Controller picks (depth, α) with α ∈ [α_min, 1.0] on a discrete grid.
α=1 means node matches local flow; α<1 means node slips (advection
magnitude scaled down). Cannot create cross-flow motion.

Hardware intuition: α_min=1.0 is just the pure-ballast baseline (no
drag modulation available). α_min=0.5 means the node can halve its
advection speed. That's an aggressive drag change — real drogue
geometries might deliver 0.7–0.9 realistic α_min; 0.5 is an optimistic
upper bound.

Sweeps α_min ∈ {1.0 (baseline), 0.8, 0.6, 0.4} on the expanded bbox at 72h.

Output: figures/18_passive_drag_sweep.png
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import DragKeeper, PerfectKnowledge  # type: ignore[import-not-found]
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

ALPHA_MIN_SWEEP = [1.0, 0.8, 0.6, 0.4]
ALPHA_N_LEVELS = 5
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
    alpha_min: float,
) -> dict:
    keeper = DragKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
        alpha_min=alpha_min,
        alpha_n_levels=ALPHA_N_LEVELS,
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
    alphas_used = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    cur_alpha = 1.0
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            chosen, alpha, _ = keeper.choose_action(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            cur_alpha = alpha
            last_decision = t_sec
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current,
                     w_z_max_ms=W_Z_MAX_MS,
                     advection_scale=cur_alpha)
        t_sec += DT_SEC
        lats[i + 1], lons[i + 1] = state.lat, state.lon
        alphas_used[i + 1] = cur_alpha

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
        "lats": lats, "lons": lons,
        "dists_m": dists,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "alpha_mean": float(alphas_used[1:].mean()),
        "alpha_min_used": float(alphas_used[1:].min()),
        "alpha_max_used_duty": float((alphas_used[1:] <= alpha_min * 1.05).mean()),
    }


def main() -> None:
    print("=== Question (A): passive-drag α sweep ===")
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

    all_results: dict[float, list[dict]] = {}
    for alpha_min in ALPHA_MIN_SWEEP:
        label = "ballast-only" if alpha_min >= 1.0 else f"α∈[{alpha_min:.1f},1.0]"
        print(f"\n--- α_min = {alpha_min:.1f} ({label}) ---")
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
            r = run_one_station(truth, s_lat, s_lon, d_set, alpha_min)
            r["bathy_m"] = s_bathy
            results.append(r)
        all_results[alpha_min] = results
        n_rough = sum(1 for r in results if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mean_500 = float(np.mean([r["envelope_fracs"][500.0] for r in results]))
        mean_1k = float(np.mean([r["envelope_fracs"][1000.0] for r in results]))
        mean_mean = float(np.mean([r["ctrl_mean_m"] for r in results]))
        mean_alpha = float(np.mean([r["alpha_mean"] for r in results]))
        print(f"  rough={n_rough}/{len(results)}  "
              f"%<500m={mean_500*100:.0f}%  %<1km={mean_1k*100:.0f}%  "
              f"ctrl_mean={mean_mean:.0f}m  mean α={mean_alpha:.2f}  "
              f"({time.time() - t0:.1f}s)")

    print()
    print("=== aggregate ===")
    print(f"{'α_min':>6}  {'rough':>8}  "
          + "  ".join(f"{'%<'+str(int(e))+'m':>8}" for e in ENVELOPES_M)
          + f"  {'ctrl_mean':>10}  {'mean α':>7}")
    n_total = len(all_results[ALPHA_MIN_SWEEP[0]])
    for a in ALPHA_MIN_SWEEP:
        rs = all_results[a]
        n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        env_cells = "  ".join(
            f"{np.mean([r['envelope_fracs'][e] for r in rs])*100:>7.0f}%"
            for e in ENVELOPES_M
        )
        mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        ma = float(np.mean([r["alpha_mean"] for r in rs]))
        print(f"  {a:>4.1f}    {n_rough:>3}/{n_total:<3}   {env_cells}  "
              f"{mm:>8.0f}m    {ma:>5.2f}")

    # --- Plot ---
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Envelope-success curves.
    ax = axes[0, 0]
    for e in ENVELOPES_M:
        pct = [
            np.mean([r["envelope_fracs"][e] for r in all_results[a]]) * 100
            for a in ALPHA_MIN_SWEEP
        ]
        ax.plot(ALPHA_MIN_SWEEP, pct, "-o", label=f"≤ {int(e)}m", lw=1.5)
    ax.set_xlabel("α_min (lower = more drag range)")
    ax.set_ylabel("mean %-of-run within envelope")
    ax.set_title(f"Envelope success vs α_min ({RUN_HOURS}h, perfect knowledge)")
    ax.legend(fontsize=8)
    ax.invert_xaxis()
    ax.grid(alpha=0.3)

    # Rough count.
    ax = axes[0, 1]
    rough_counts = [
        sum(1 for r in all_results[a] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        for a in ALPHA_MIN_SWEEP
    ]
    ax.bar(range(len(ALPHA_MIN_SWEEP)), rough_counts,
           color="teal", alpha=0.75)
    for i, c in enumerate(rough_counts):
        ax.text(i, c + 0.3, str(c), ha="center", fontsize=10)
    ax.set_xticks(range(len(ALPHA_MIN_SWEEP)))
    ax.set_xticklabels([f"{a:.1f}" for a in ALPHA_MIN_SWEEP])
    ax.set_xlabel("α_min")
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m")
    ax.set_title(f"# rough station-keeping (of {n_total})")
    ax.grid(alpha=0.3, axis="y")

    # ctrl_mean distributions.
    ax = axes[1, 0]
    data = [[r["ctrl_mean_m"] for r in all_results[a]] for a in ALPHA_MIN_SWEEP]
    ax.boxplot(data, tick_labels=[f"α_min={a:.1f}" for a in ALPHA_MIN_SWEEP])
    ax.set_ylabel("controlled mean distance (m)")
    ax.set_title("ctrl_mean distribution per α_min")
    ax.grid(alpha=0.3, axis="y")

    # Mean α actually used.
    ax = axes[1, 1]
    ax.bar(range(len(ALPHA_MIN_SWEEP)),
           [np.mean([r["alpha_mean"] for r in all_results[a]]) for a in ALPHA_MIN_SWEEP],
           color="C0", alpha=0.75, label="mean α used")
    ax.plot(range(len(ALPHA_MIN_SWEEP)), ALPHA_MIN_SWEEP, "r--o",
            label="α_min (floor)")
    ax.set_xticks(range(len(ALPHA_MIN_SWEEP)))
    ax.set_xticklabels([f"{a:.1f}" for a in ALPHA_MIN_SWEEP])
    ax.set_xlabel("α_min")
    ax.set_ylabel("α")
    ax.set_ylim(0, 1.05)
    ax.set_title("Is the drag budget used? (mean α vs α_min floor)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Question (A): passive-drag α sweep  "
        f"({n_total} stations, {RUN_HOURS}h, perfect knowledge)",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "18_passive_drag_sweep.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
