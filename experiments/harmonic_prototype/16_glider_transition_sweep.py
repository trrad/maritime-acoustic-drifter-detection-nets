"""Question (B): glider-transition thrust — how much horizontal authority
does a buoyancy glider get by coupling its dive/ascent motion to
wing-generated horizontal thrust?

Model: during any depth transition (depth != setpoint), the node emits a
horizontal velocity of magnitude V_glide in a chosen heading. When
arrived at setpoint, glide is off until the next depth change is
commanded. Glide is free during transitions (buoyancy-engine-limited)
but requires commanding a depth change — the glider sacrifices depth
degree-of-freedom for horizontal steering.

A sophisticated controller might intentionally yoyo even when the depth
choice itself is suboptimal, just to get glide authority. Our 30-min
control cadence on a 1-hour sim tick constrains this: transit between
any two depths in the available set (0.5-50m) completes within one step,
so glide only contributes for a fraction of each step.

Sweeps V_glide ∈ {0, 0.10, 0.20, 0.30} m/s (0 = pure ballast baseline;
0.10 = small-glider class; 0.25-0.30 = Slocum-class).

Output: figures/19_glider_transition_sweep.png
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import GliderKeeper, PerfectKnowledge  # type: ignore[import-not-found]
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

V_GLIDE_SWEEP_MS = [0.0, 0.10, 0.20, 0.30]
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
    v_glide_ms: float,
) -> dict:
    keeper = GliderKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
        glide_v_ms=v_glide_ms,
        w_z_max_ms=W_Z_MAX_MS,
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
    depths = np.zeros(n_steps + 1)
    glide_mags = np.zeros(n_steps + 1)
    in_transit = np.zeros(n_steps + 1, dtype=bool)
    lats[0], lons[0] = state.lat, state.lon
    depths[0] = state.depth_m
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    cur_glide = (0.0, 0.0)
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            chosen, glide, _ = keeper.choose_action(
                state.lat, state.lon, t_sec,
                current_depth_m=state.depth_m,
            )
            state = set_setpoint(state, chosen)
            cur_glide = glide
            last_decision = t_sec
        # Record transit state before stepping.
        in_transit[i + 1] = abs(state.depth_setpoint_m - state.depth_m) > 1e-6
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current,
                     w_z_max_ms=W_Z_MAX_MS,
                     glide_uv_ms=cur_glide)
        t_sec += DT_SEC
        lats[i + 1], lons[i + 1] = state.lat, state.lon
        depths[i + 1] = state.depth_m
        glide_mags[i + 1] = float(np.hypot(*cur_glide))

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
        "depths": depths, "dists_m": dists,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "glide_mag_mean": float(glide_mags[1:].mean()),
        "transit_duty": float(in_transit[1:].mean()),
    }


def main() -> None:
    print("=== Question (B): glider-transition V_glide sweep ===")
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
    for v_g in V_GLIDE_SWEEP_MS:
        label = "pure ballast" if v_g <= 0 else f"V_glide={v_g*100:.0f}cm/s"
        print(f"\n--- {label} ---")
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
            r = run_one_station(truth, s_lat, s_lon, d_set, v_g)
            r["bathy_m"] = s_bathy
            results.append(r)
        all_results[v_g] = results
        n_rough = sum(1 for r in results if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mean_500 = float(np.mean([r["envelope_fracs"][500.0] for r in results]))
        mean_1k = float(np.mean([r["envelope_fracs"][1000.0] for r in results]))
        mean_mean = float(np.mean([r["ctrl_mean_m"] for r in results]))
        mean_transit = float(np.mean([r["transit_duty"] for r in results]))
        print(f"  rough={n_rough}/{len(results)}  "
              f"%<500m={mean_500*100:.0f}%  %<1km={mean_1k*100:.0f}%  "
              f"ctrl_mean={mean_mean:.0f}m  transit-duty={mean_transit*100:.0f}%  "
              f"({time.time() - t0:.1f}s)")

    print()
    print("=== aggregate ===")
    print(f"{'V_glide':>9}  {'rough':>8}  "
          + "  ".join(f"{'%<'+str(int(e))+'m':>8}" for e in ENVELOPES_M)
          + f"  {'ctrl_mean':>10}  {'transit':>8}")
    n_total = len(all_results[V_GLIDE_SWEEP_MS[0]])
    for v_g in V_GLIDE_SWEEP_MS:
        rs = all_results[v_g]
        n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        env_cells = "  ".join(
            f"{np.mean([r['envelope_fracs'][e] for r in rs])*100:>7.0f}%"
            for e in ENVELOPES_M
        )
        mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        td = float(np.mean([r["transit_duty"] for r in rs]))
        print(f"  {v_g*100:>4.0f} cm/s   {n_rough:>3}/{n_total:<3}   "
              f"{env_cells}  {mm:>8.0f}m   {td*100:>5.0f}%")

    # --- Plot ---
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    vg_pct = [v * 100 for v in V_GLIDE_SWEEP_MS]

    ax = axes[0, 0]
    for e in ENVELOPES_M:
        pct = [
            np.mean([r["envelope_fracs"][e] for r in all_results[v]]) * 100
            for v in V_GLIDE_SWEEP_MS
        ]
        ax.plot(vg_pct, pct, "-o", label=f"≤ {int(e)}m", lw=1.5)
    ax.set_xlabel("V_glide (cm/s)")
    ax.set_ylabel("mean %-of-run within envelope")
    ax.set_title(f"Envelope success vs glider V_glide ({RUN_HOURS}h)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[0, 1]
    rough_counts = [
        sum(1 for r in all_results[v] if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        for v in V_GLIDE_SWEEP_MS
    ]
    ax.bar(range(len(V_GLIDE_SWEEP_MS)), rough_counts,
           color="darkorange", alpha=0.75)
    for i, c in enumerate(rough_counts):
        ax.text(i, c + 0.3, str(c), ha="center", fontsize=10)
    ax.set_xticks(range(len(V_GLIDE_SWEEP_MS)))
    ax.set_xticklabels([f"{v*100:.0f}" for v in V_GLIDE_SWEEP_MS])
    ax.set_xlabel("V_glide (cm/s)")
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m")
    ax.set_title(f"# rough station-keeping (of {n_total})")
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1, 0]
    data = [[r["ctrl_mean_m"] for r in all_results[v]] for v in V_GLIDE_SWEEP_MS]
    ax.boxplot(data, tick_labels=[f"V={v*100:.0f}" for v in V_GLIDE_SWEEP_MS])
    ax.set_ylabel("controlled mean distance (m)")
    ax.set_title("ctrl_mean distribution per V_glide")
    ax.grid(alpha=0.3, axis="y")

    # Transit duty cycle — how much of the run is the glider yoyoing?
    ax = axes[1, 1]
    transit = [
        np.mean([r["transit_duty"] for r in all_results[v]]) * 100
        for v in V_GLIDE_SWEEP_MS
    ]
    ax.bar(range(len(V_GLIDE_SWEEP_MS)), transit,
           color="C2", alpha=0.75, label="% of ticks with active transit")
    for i, t in enumerate(transit):
        ax.text(i, t + 1.5, f"{t:.0f}%", ha="center", fontsize=9)
    ax.set_xticks(range(len(V_GLIDE_SWEEP_MS)))
    ax.set_xticklabels([f"{v*100:.0f}" for v in V_GLIDE_SWEEP_MS])
    ax.set_xlabel("V_glide (cm/s)")
    ax.set_ylabel("% of run in transit")
    ax.set_title("Transit duty — how often is glider yo-yoing for steering?")
    ax.set_ylim(0, max(max(transit), 1) * 1.2)
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Question (B): glider-transition V_glide sweep  "
        f"({n_total} stations, {RUN_HOURS}h, perfect knowledge)",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "19_glider_transition_sweep.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
