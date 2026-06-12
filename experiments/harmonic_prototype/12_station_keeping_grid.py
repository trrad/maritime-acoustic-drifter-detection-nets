"""Phase A+: where in the bbox is station-keeping feasible?

Generalises `10_station_keeping_upper_bound.py` from a single mid-bbox
station to a grid of candidate stations. For each station, runs
perfect-knowledge greedy control vs passive drift for 24 h and reports:
  - % of run within several envelopes (500 m, 750 m, 1000 m, 1500 m)
  - controlled max excursion
  - steering factor (passive mean dist / controlled mean dist)

The 1-station Phase A result (36% within 500 m, ctrl mean 749 m) is a
single sample. The real question is how much that varies with location
— is the bbox mostly feasible, mostly hopeless, or patchy?

Grid: stride across (gridY, gridX) of the cached bbox, skipping cells
with bathymetry < `MIN_BATHY_M` (need water column for 50 m depth choice).

Available depth set at each station is clipped to `min(50, bathy * 0.8)`.

Output: figures/14_station_keeping_grid.png
  - bathy background of bbox
  - one panel per metric (ctrl mean dist, ctrl max excursion, steering factor)
  - a bar chart "N stations within envelope X" for X in [500, 750, 1000, 1500, 2000]
  - overlay: marker at each station coloured by metric

Run: uv run --with xarray,netCDF4,numpy,matplotlib,scipy \\
     python experiments/harmonic_prototype/12_station_keeping_grid.py
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    bbox_latlon_arrays,
    fetch_bbox_months,
)
from truth_field import build_truth_field, distance_m  # type: ignore[import-not-found]


# Two run modes:
#   "preview" — existing cached 15×18 km bbox, central stations only, 72 h.
#               Works immediately; some trajectories will exit the domain.
#   "expanded" — 60×60 km bbox (fetch in progress), full grid, 72 h.
#
# Flip RUN_MODE once the expanded fetch is complete.
RUN_MODE = "expanded"

if RUN_MODE == "preview":
    LAT_MIN, LAT_MAX = 49.25, 49.35
    LON_MIN, LON_MAX = -123.78, -123.62
    MONTHS = ["2023-04"]
    # Keep stride 5 but pad interior margin heavily so stations concentrate
    # near the bbox centre (at 72 h they need 20-km+ head-room ideally).
    STRIDE_Y = 5
    STRIDE_X = 5
    INTERIOR_MARGIN_Y = 8   # cells of padding on each edge (out of 30)
    INTERIOR_MARGIN_X = 10  # cells of padding on each edge (out of 36)
else:  # "expanded"
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

# Simulation settings.
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]
RUN_HOURS = 72  # 3 days — exercises multiple spring-tide cycles
DT_SEC = 3600.0
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0
PASSIVE_DEPTH_M = 10.0

# "Rough station-keeping" threshold used for the summary count.
# Loosened from the 24 h defaults; a 72 h run exercises multiple tidal
# cycles and a drifter with any residual mean flow will accumulate.
ROUGH_ENVELOPE_M = 3000.0
ROUGH_STEERING_FACTOR = 2.0

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    """Pick the subset of DEFAULT_DEPTH_SET that fits in this bathymetry."""
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def run_one_station(
    truth,
    station_lat: float, station_lon: float,
    depth_set: list[float],
    initial_depth: float,
) -> dict:
    """Run controlled + passive at one station. Returns a metrics dict."""
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

    # --- Controlled ---
    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=initial_depth, depth_setpoint_m=initial_depth,
    )
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    c_lats = np.zeros(n_steps + 1)
    c_lons = np.zeros(n_steps + 1)
    c_lats[0], c_lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            chosen, _ = keeper.choose_depth(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            last_decision = t_sec
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        c_lats[i + 1], c_lons[i + 1] = state.lat, state.lon

    # --- Passive ---
    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=PASSIVE_DEPTH_M, depth_setpoint_m=PASSIVE_DEPTH_M,
    )
    p_lats = np.zeros(n_steps + 1)
    p_lons = np.zeros(n_steps + 1)
    p_lats[0], p_lons[0] = state.lat, state.lon
    t_sec = 0.0
    for i in range(n_steps):
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        p_lats[i + 1], p_lons[i + 1] = state.lat, state.lon

    c_dists = np.array([
        distance_m(la, lo, station_lat, station_lon) for la, lo in zip(c_lats, c_lons)
    ])
    p_dists = np.array([
        distance_m(la, lo, station_lat, station_lon) for la, lo in zip(p_lats, p_lons)
    ])
    # Passive can NaN out when it exits the interp domain. Treat as "all-bad":
    # clip to the last-valid, carry forward, which favours the passive baseline
    # (if anything). For the metric we want the honest excursion — replace NaN
    # with the max observed so far.
    valid = np.isfinite(c_dists)
    if not valid.all():
        # The ctrl exited the domain — this station is infeasible.
        last = np.where(valid)[0]
        c_dists_full = c_dists.copy()
        if len(last) > 0:
            c_dists_full[~valid] = c_dists_full[last[-1]]
        else:
            c_dists_full[:] = np.inf
    else:
        c_dists_full = c_dists
    valid_p = np.isfinite(p_dists)
    if not valid_p.all():
        last = np.where(valid_p)[0]
        p_dists_full = p_dists.copy()
        if len(last) > 0:
            p_dists_full[~valid_p] = p_dists_full[last[-1]]
        else:
            p_dists_full[:] = np.inf
    else:
        p_dists_full = p_dists

    envelope_fracs = {
        e: float((c_dists_full <= e).mean()) for e in ENVELOPES_M
    }
    return {
        "station_lat": station_lat, "station_lon": station_lon,
        "depth_set": depth_set,
        "ctrl_lats": c_lats, "ctrl_lons": c_lons,
        "passive_lats": p_lats, "passive_lons": p_lons,
        "ctrl_dists_m": c_dists_full,
        "passive_dists_m": p_dists_full,
        "ctrl_mean_m": float(np.nanmean(c_dists_full)),
        "ctrl_max_m": float(np.nanmax(c_dists_full)),
        "passive_mean_m": float(np.nanmean(p_dists_full)),
        "passive_max_m": float(np.nanmax(p_dists_full)),
        "envelope_fracs": envelope_fracs,
        "steering_factor": (float(np.nanmean(p_dists_full))
                            / max(float(np.nanmean(c_dists_full)), 1e-6)),
        "ctrl_valid_all": bool(valid.all()),
        "passive_valid_all": bool(valid_p.all()),
    }


def main() -> None:
    print("=== Phase A+: station-keeping feasibility grid ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}")
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]
    print(f"bbox grid: {n_y} × {n_x} cells")

    print(f"building truth interpolators at {DEFAULT_DEPTH_SET} m ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  built in {time.time() - t0:.1f}s")

    # --- Enumerate candidate stations ---
    # Keep candidates inside the bbox interior by INTERIOR_MARGIN_{Y,X}
    # cells on each side. In preview mode this concentrates stations
    # near the bbox centre so 72 h runs have head-room before drifting
    # into the NaN fill-value region at the boundary.
    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"candidate stations: {len(candidates)}  "
          f"(stride {STRIDE_Y}×{STRIDE_X}, bathy ≥ {MIN_BATHY_M}m, "
          f"interior margin {INTERIOR_MARGIN_Y}×{INTERIOR_MARGIN_X}, "
          f"mode={RUN_MODE})")

    results: list[dict] = []
    n_skipped_nan = 0
    t_loop = time.time()
    for i, (gy, gx) in enumerate(candidates):
        # Use the interpolator's own axis values so the start point is
        # exactly representable — avoids fill-value NaN at the first sample.
        s_lat = float(truth.lat_axis[gy])
        s_lon = float(truth.lon_axis[gx])
        s_bathy = float(bathy_grid[gy, gx])
        d_set = depth_set_for_bathy(s_bathy)
        if len(d_set) < 2:
            continue  # not enough depth choice to steer

        # Reject stations whose first sample is NaN (shouldn't happen with
        # the axis-exact query, but cheap to verify).
        u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
        if not (np.isfinite(u0) and np.isfinite(v0)):
            n_skipped_nan += 1
            continue

        r = run_one_station(truth, s_lat, s_lon, d_set, INITIAL_DEPTH_M)
        r["gy"], r["gx"], r["bathy_m"] = gy, gx, s_bathy
        results.append(r)
    dt = time.time() - t_loop
    print(f"ran {len(results)} stations in {dt:.1f}s "
          f"({dt/max(len(results),1):.2f}s/station; "
          f"skipped {n_skipped_nan} with NaN initial sample)")

    # --- Print table ---
    print()
    env_cols = ENVELOPES_M[:3]  # first three for the text table
    env_hdr = " ".join(f"{'%<'+str(int(e)):>7}" for e in env_cols)
    print(f"{'#':>3}  {'lat':>7}  {'lon':>9}  {'bathy':>6}  "
          f"{'ctrl_mean':>9} {'ctrl_max':>8} {'pass_mean':>9} {'steer':>6}  "
          f"{env_hdr}")
    for i, r in enumerate(results):
        env_cells = " ".join(
            f"{r['envelope_fracs'][e]*100:>6.0f}%" for e in env_cols
        )
        print(f"{i:>3}  {r['station_lat']:>7.4f}  {r['station_lon']:>9.4f}  "
              f"{r['bathy_m']:>5.0f}m  "
              f"{r['ctrl_mean_m']:>7.0f}m  {r['ctrl_max_m']:>7.0f}m  "
              f"{r['passive_mean_m']:>7.0f}m  "
              f"{r['steering_factor']:>5.1f}x  "
              f"{env_cells}")

    # --- Aggregate stats ---
    env_counts = {
        f"ctrl_max≤{int(e)}m": sum(1 for r in results if r["ctrl_max_m"] <= e)
        for e in ENVELOPES_M
    }
    env_fracs_avg = {
        e: float(np.mean([r["envelope_fracs"][e] for r in results]))
        for e in ENVELOPES_M
    }
    rough_count = sum(
        1 for r in results
        if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M
        and r["steering_factor"] >= ROUGH_STEERING_FACTOR
    )
    print()
    print("=== aggregate ===")
    for label, n in env_counts.items():
        print(f"  {label}: {n}/{len(results)}  ({n/len(results)*100:.0f}%)")
    for e, f in env_fracs_avg.items():
        print(f"  mean %-time within {int(e)}m: {f*100:.0f}%")
    print(f"  rough station-keeping (ctrl_max ≤ {ROUGH_ENVELOPE_M:.0f}m "
          f"AND steer ≥ {ROUGH_STEERING_FACTOR:.1f}x): "
          f"{rough_count}/{len(results)}  ({rough_count/len(results)*100:.0f}%)")

    # --- Plot ---
    fig = plt.figure(figsize=(18, 11))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.2, 1.0], hspace=0.3, wspace=0.3)

    masked_bathy = np.where(bathy_grid > 0, bathy_grid, np.nan)

    def scatter_map(ax, values, title, cmap, vmin=None, vmax=None, log=False):
        ax.imshow(masked_bathy, origin="lower", cmap="Blues", alpha=0.35,
                  extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
        lons_s = [r["station_lon"] for r in results]
        lats_s = [r["station_lat"] for r in results]
        if log:
            values = np.log10(np.maximum(values, 1e-3))
        sc = ax.scatter(lons_s, lats_s, c=values, cmap=cmap,
                        vmin=vmin, vmax=vmax,
                        s=180, edgecolors="black", linewidths=0.6)
        ax.set_xlabel("Longitude (°)")
        ax.set_ylabel("Latitude (°)")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        plt.colorbar(sc, ax=ax, shrink=0.8)

    ax0 = fig.add_subplot(gs[0, 0])
    scatter_map(ax0,
                [r["ctrl_max_m"] for r in results],
                "controlled max excursion (m)\nlower = better",
                cmap="RdYlGn_r", vmin=0, vmax=4000)

    ax1 = fig.add_subplot(gs[0, 1])
    scatter_map(ax1,
                [r["ctrl_mean_m"] for r in results],
                "controlled mean dist-from-station (m)\nlower = better",
                cmap="RdYlGn_r", vmin=0, vmax=2500)

    ax2 = fig.add_subplot(gs[0, 2])
    scatter_map(ax2,
                [r["steering_factor"] for r in results],
                "steering factor (passive_mean / ctrl_mean)\nhigher = better",
                cmap="RdYlGn", vmin=1.0, vmax=8.0)

    # Envelope success bar chart.
    ax3 = fig.add_subplot(gs[1, 0])
    counts = [env_counts[f"ctrl_max≤{int(e)}m"] for e in ENVELOPES_M]
    ax3.bar(range(len(ENVELOPES_M)), counts,
            color="steelblue", alpha=0.75)
    for i, c in enumerate(counts):
        ax3.text(i, c + 0.3, f"{c}/{len(results)}", ha="center", fontsize=10)
    ax3.set_xticks(range(len(ENVELOPES_M)))
    ax3.set_xticklabels([f"≤{int(e)}m" for e in ENVELOPES_M])
    ax3.set_xlabel("envelope (controlled max excursion)")
    ax3.set_ylabel("# stations")
    ax3.set_ylim(0, len(results) + 2)
    ax3.set_title(f"Stations achieving ctrl_max ≤ envelope\n"
                  f"{len(results)} stations total, "
                  f"rough station-keeping at {rough_count}/{len(results)}")
    ax3.grid(alpha=0.3, axis="y")

    # Mean %-time within each envelope.
    ax4 = fig.add_subplot(gs[1, 1])
    vals = [env_fracs_avg[e] * 100 for e in ENVELOPES_M]
    ax4.bar(range(len(ENVELOPES_M)), vals, color="C0", alpha=0.75)
    for i, v in enumerate(vals):
        ax4.text(i, v + 1.5, f"{v:.0f}%", ha="center", fontsize=10)
    ax4.set_xticks(range(len(ENVELOPES_M)))
    ax4.set_xticklabels([f"≤{int(e)}m" for e in ENVELOPES_M])
    ax4.set_xlabel("envelope")
    ax4.set_ylabel(f"mean % of {RUN_HOURS}h within envelope")
    ax4.set_ylim(0, 100)
    ax4.set_title("Mean (across stations) %-time within envelope")
    ax4.grid(alpha=0.3, axis="y")

    # Histogram of controlled max excursion.
    ax5 = fig.add_subplot(gs[1, 2])
    vals_max = [r["ctrl_max_m"] for r in results]
    ax5.hist(vals_max, bins=list(np.linspace(0, 5000, 21)),
             color="C1", alpha=0.7, edgecolor="black")
    ax5.axvline(ROUGH_ENVELOPE_M, ls="--", color="black",
                label=f"rough-threshold {int(ROUGH_ENVELOPE_M)}m")
    ax5.set_xlabel("controlled max excursion (m)")
    ax5.set_ylabel("# stations")
    ax5.set_title("Distribution of controlled max excursion")
    ax5.legend(fontsize=9)
    ax5.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Phase A+: perfect-knowledge station-keeping across "
        f"{len(results)} stations in bbox\n"
        f"({RUN_HOURS}h from {MONTHS[0]}-01, ballast depths {DEFAULT_DEPTH_SET} m, "
        f"decisions every {CONTROL_CADENCE_SEC/60:.0f} min)",
        fontsize=12, y=1.0,
    )
    out = FIG_DIR / "14_station_keeping_grid.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")

    # --- Second figure: all controlled trajectories on one shared map,
    #     each segment coloured by distance-from-station at that point. ---
    from matplotlib.collections import LineCollection  # type: ignore[import-not-found]
    from matplotlib.colors import BoundaryNorm, ListedColormap  # type: ignore[import-not-found]
    from matplotlib.cm import ScalarMappable  # type: ignore[import-not-found]

    fig_tr, ax_tr = plt.subplots(figsize=(13, 11))
    ax_tr.imshow(masked_bathy, origin="lower", cmap="Blues", alpha=0.25,
                 extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX),
                 aspect="auto")

    rough_flag = lambda r: (
        r["ctrl_max_m"] <= ROUGH_ENVELOPE_M
        and r["steering_factor"] >= ROUGH_STEERING_FACTOR
    )

    # Discrete colour bands by distance-from-station:
    #   [0,     1km)  → green  (near — station-keeping tight)
    #   [1km,   5km)  → black  (loose — drifted but recoverable)
    #   [5km,  15km]  → red    (lost — no realistic recovery)
    cmap = ListedColormap(["#1a9641", "#222222", "#d7191c"])
    bounds = [0.0, 1000.0, 5000.0, 15000.0]
    norm = BoundaryNorm(bounds, cmap.N)

    for r in results:
        lons = r["ctrl_lons"]
        lats = r["ctrl_lats"]
        d = r["ctrl_dists_m"]
        # Build segment list for LineCollection.
        pts = np.column_stack([lons, lats]).reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        # Segment colour = distance at the segment's starting endpoint.
        seg_d = d[:-1]
        lc = LineCollection(
            list(segs), cmap=cmap, norm=norm,
            linewidths=1.6 if rough_flag(r) else 1.0,
            alpha=0.95 if rough_flag(r) else 0.7,
        )
        lc.set_array(seg_d)
        ax_tr.add_collection(lc)
        # Endpoint dot in the segment's colour.
        end_color = cmap(norm(d[-1]))
        ax_tr.plot(lons[-1], lats[-1], "o", color=end_color,
                   markersize=3.0, alpha=0.85,
                   markeredgecolor="black", markeredgewidth=0.3)

    # Station markers: medium-sized white-filled dot so they're visible
    # against the bathy + coloured trajectories without dominating.
    for r in results:
        ax_tr.plot(r["station_lon"], r["station_lat"], "o",
                   markerfacecolor="white", markeredgecolor="black",
                   markeredgewidth=1.0, markersize=7, alpha=0.95,
                   zorder=5)

    # Steering-factor label only on rough-met stations, slightly offset.
    for r in results:
        if not rough_flag(r):
            continue
        ax_tr.annotate(
            f"{r['steering_factor']:.1f}×",
            xy=(r["station_lon"], r["station_lat"]),
            xytext=(3, 3), textcoords="offset points",
            fontsize=7.5, color="black", fontweight="bold",
        )

    # Discrete-band colorbar.
    sm = ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig_tr.colorbar(sm, ax=ax_tr, shrink=0.7, pad=0.02,
                            ticks=[500, 3000, 10000], boundaries=bounds)
    cbar.ax.set_yticklabels(["< 1 km\n(tight)", "1–5 km\n(loose)",
                              "5–15 km\n(lost)"])
    cbar.ax.tick_params(labelsize=8)
    cbar.set_label("distance from station at segment")

    ax_tr.set_xlim(LON_MIN, LON_MAX)
    ax_tr.set_ylim(LAT_MIN, LAT_MAX)
    ax_tr.set_xlabel("Longitude (°)")
    ax_tr.set_ylabel("Latitude (°)")
    n_rough = sum(1 for r in results if rough_flag(r))
    ax_tr.set_title(
        f"Phase A+: controlled trajectories at {len(results)} stations, "
        f"{RUN_HOURS}h from {MONTHS[0]}-01 (mode={RUN_MODE})\n"
        f"segments: green < 1 km / black 1–5 km / red 5–15 km;  "
        f"bold = rough station-keeping ({n_rough}/{len(results)}, "
        f"max ≤ {int(ROUGH_ENVELOPE_M)}m & steer ≥ {ROUGH_STEERING_FACTOR:.1f}×)"
    )
    ax_tr.grid(alpha=0.25)

    fig_tr.tight_layout()
    out_tr = FIG_DIR / "15_station_keeping_grid_trajectories.png"
    fig_tr.savefig(out_tr, dpi=120, bbox_inches="tight")
    plt.close(fig_tr)
    print(f"[viz] wrote {out_tr}")

    # --- Save ---
    npz_out = FIG_DIR / "14_station_keeping_grid.npz"
    np.savez(
        npz_out,
        station_lats=np.array([r["station_lat"] for r in results]),
        station_lons=np.array([r["station_lon"] for r in results]),
        bathy_m=np.array([r["bathy_m"] for r in results]),
        ctrl_mean_m=np.array([r["ctrl_mean_m"] for r in results]),
        ctrl_max_m=np.array([r["ctrl_max_m"] for r in results]),
        passive_mean_m=np.array([r["passive_mean_m"] for r in results]),
        passive_max_m=np.array([r["passive_max_m"] for r in results]),
        steering_factor=np.array([r["steering_factor"] for r in results]),
        envelope_m=np.array(ENVELOPES_M),
        envelope_fracs=np.array([[r["envelope_fracs"][e] for e in ENVELOPES_M]
                                  for r in results]),
    )
    print(f"[data] wrote {npz_out}")


if __name__ == "__main__":
    main()
