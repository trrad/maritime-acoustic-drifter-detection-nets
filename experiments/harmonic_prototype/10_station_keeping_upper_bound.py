"""Phase A (Q1): perfect-knowledge station-keeping upper bound.

Question: given a ballast node with truth-perfect current knowledge at
every available depth, how effectively can it station-keep in
SalishSeaCast 2023 Apr–Jun currents at the centre of our 20×20 km bbox?

Baseline: a passive fixed-depth drifter at the same start point.

This is the best-case ceiling. If this fails, no amount of PF sophistication
or sensor work can rescue the mission.

Also dumps a "current diversity" diagnostic at the station first: does
the truth current at the 5 available depths × 24h span enough directions
for depth-choice + tidal-phase to hold position, or is everything flowing
roughly the same way all day (in which case no controller, perfect-
knowledge or otherwise, can station-keep at this point)?

Run: uv run --with xarray,netCDF4,numpy,matplotlib,scipy \\
     python experiments/harmonic_prototype/10_station_keeping_upper_bound.py
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


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
MONTHS = ["2023-04", "2023-05", "2023-06"]

# Defaults (plan §Defaults).
STATION_ENVELOPE_M = 500.0
RUN_HOURS = 24
DT_SEC = 3600.0                 # 1h sim tick — matches SalishSeaCast cadence
CONTROL_CADENCE_SEC = 1800.0    # decide every 30 min
LOOKAHEAD_SEC = 1800.0          # project 30 min ahead when scoring
W_Z_MAX_MS = 0.1                # vertical speed cap
AVAILABLE_DEPTHS_M = [0.5, 5.0, 10.0, 20.0, 50.0]
PASSIVE_DEPTH_M = 10.0          # baseline passive drifter depth (middle of range)
INITIAL_DEPTH_M = 10.0

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)


def current_diversity_diagnostic(truth, station_lat, station_lon,
                                 depths, run_hours) -> dict:
    """Measure whether the currents at the station across depths × time
    actually span enough directions for station-keeping to be possible.

    Reports:
      - per-depth mean current vector + instantaneous speed range
      - per-depth set of instantaneous directions across the run
      - best opposing pair: at any time t, is there a pair (d_i, d_j)
        whose currents point more than 90° apart? If so, the controller
        has directional authority at that instant.
      - fraction of time ≥ 1 opposing-pair exists

    This is a necessary-but-not-sufficient check. Enough reversibility
    here means the physics admit station-keeping; the actual metric is
    still the Phase A simulation result.
    """
    print("--- Current-diversity diagnostic at station ---")
    n_ticks = run_hours
    # Sample at integer-hour ticks inside the 24h window.
    sample_times = np.arange(n_ticks) * 3600.0
    per_depth: dict[float, dict] = {}
    u_all = np.zeros((len(depths), n_ticks))
    v_all = np.zeros((len(depths), n_ticks))
    for i, d in enumerate(depths):
        for j, t in enumerate(sample_times):
            u, v = truth.sample(station_lat, station_lon, d, t)
            u_all[i, j] = u
            v_all[i, j] = v
        speed = np.sqrt(u_all[i]**2 + v_all[i]**2)
        dirs = np.rad2deg(np.arctan2(v_all[i], u_all[i]))
        per_depth[d] = {
            "u_mean": float(u_all[i].mean()),
            "v_mean": float(v_all[i].mean()),
            "speed_mean": float(speed.mean()),
            "speed_max": float(speed.max()),
            "dir_range_deg": float(dirs.max() - dirs.min()),
        }
        print(f"  {d:>5.1f}m  mean=({u_all[i].mean():+.3f},{v_all[i].mean():+.3f}) m/s  "
              f"|spd|∈[{speed.min():.3f},{speed.max():.3f}]  "
              f"dir range {dirs.max() - dirs.min():.0f}°")

    # At each tick, find max pairwise angle between depth currents.
    max_pair_angle = np.zeros(n_ticks)
    for j in range(n_ticks):
        angles = []
        for i in range(len(depths)):
            for k in range(i + 1, len(depths)):
                dot = u_all[i, j] * u_all[k, j] + v_all[i, j] * v_all[k, j]
                m1 = np.sqrt(u_all[i, j]**2 + v_all[i, j]**2)
                m2 = np.sqrt(u_all[k, j]**2 + v_all[k, j]**2)
                if m1 > 1e-6 and m2 > 1e-6:
                    cos_a = np.clip(dot / (m1 * m2), -1.0, 1.0)
                    angles.append(np.rad2deg(np.arccos(cos_a)))
        max_pair_angle[j] = max(angles) if angles else 0.0
    frac_reversible = float((max_pair_angle > 90).mean())
    max_angle_over_run = float(max_pair_angle.max())
    print(f"  max inter-depth angle over run: {max_angle_over_run:.0f}°  "
          f"fraction of ticks with ≥1 pair > 90° apart: {frac_reversible*100:.0f}%")
    return {
        "per_depth": per_depth,
        "max_angle_over_run_deg": max_angle_over_run,
        "frac_reversible": frac_reversible,
    }


def run_controlled(
    truth, station_lat, station_lon,
    start_lat, start_lon, start_depth,
    run_hours, dt_sec, control_cadence_sec,
) -> dict:
    """Run the greedy station-keeper for `run_hours`."""
    keeper = StationKeeper(
        station_lat=station_lat,
        station_lon=station_lon,
        available_depths_m=AVAILABLE_DEPTHS_M,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
    )

    def current_at_depth_for_dynamics(
        t_sec: float, lat: float, lon: float, depth_m: float,
    ) -> tuple[float, float]:
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=start_lat, lon=start_lon,
        depth_m=start_depth, depth_setpoint_m=start_depth,
    )
    n_steps = int(run_hours * 3600 / dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    setpoints = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    depths[0] = state.depth_m
    setpoints[0] = state.depth_setpoint_m

    t_sec = 0.0
    last_decision_t = -control_cadence_sec  # force decision at t=0
    depth_decisions: list[tuple[float, float]] = []  # (t_sec, chosen_depth)
    for i in range(n_steps):
        if t_sec - last_decision_t >= control_cadence_sec - 1e-6:
            chosen, _ = keeper.choose_depth(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            depth_decisions.append((t_sec, chosen))
            last_decision_t = t_sec
        state = step(state, t_sec, dt_sec,
                     current_at=current_at_depth_for_dynamics,
                     w_z_max_ms=W_Z_MAX_MS)
        t_sec += dt_sec
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
        depths[i + 1] = state.depth_m
        setpoints[i + 1] = state.depth_setpoint_m

    return {
        "lats": lats, "lons": lons, "depths": depths, "setpoints": setpoints,
        "decisions": depth_decisions, "dt_sec": dt_sec, "n_steps": n_steps,
    }


def run_passive(
    truth, start_lat, start_lon, fixed_depth,
    run_hours, dt_sec,
) -> dict:
    """Baseline: no depth control, passive drift at fixed depth."""

    def current_at_depth_for_dynamics(
        t_sec: float, lat: float, lon: float, depth_m: float,
    ) -> tuple[float, float]:
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=start_lat, lon=start_lon,
        depth_m=fixed_depth, depth_setpoint_m=fixed_depth,
    )
    n_steps = int(run_hours * 3600 / dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon

    t_sec = 0.0
    for i in range(n_steps):
        state = step(state, t_sec, dt_sec,
                     current_at=current_at_depth_for_dynamics,
                     w_z_max_ms=W_Z_MAX_MS)
        t_sec += dt_sec
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
    return {"lats": lats, "lons": lons, "dt_sec": dt_sec, "n_steps": n_steps}


def compute_station_metrics(lats, lons, station_lat, station_lon, envelope_m):
    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    within = float((dists <= envelope_m).mean())
    return {
        "dists_m": dists,
        "frac_within": within,
        "max_excursion_m": float(np.nanmax(dists)),
        "final_m": float(dists[-1]),
        "mean_m": float(np.nanmean(dists)),
    }


def main() -> None:
    print("=== Phase A: perfect-knowledge station-keeping upper bound ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}")
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    print(f"truth dims: {dict(ds.sizes)}")

    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]
    cy, cx = n_y // 2, n_x // 2
    station_lat = float(lats_grid[cy, cx])
    station_lon = float(lons_grid[cy, cx])
    station_bathy = float(bathy_grid[cy, cx])
    print(f"station: ({station_lat:.4f}, {station_lon:.4f}) "
          f"bathy={station_bathy:.0f}m")

    print(f"building truth interpolators at {AVAILABLE_DEPTHS_M} m ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, AVAILABLE_DEPTHS_M)
    print(f"  built in {time.time() - t0:.1f}s")
    for d, interp in sorted(truth.interps.items()):
        print(f"  target {d:>5.1f}m  → nearest grid level {interp.actual_depth_m:.2f}m")

    diag = current_diversity_diagnostic(
        truth, station_lat, station_lon, AVAILABLE_DEPTHS_M, RUN_HOURS,
    )

    print()
    print(f"running controlled drifter for {RUN_HOURS}h "
          f"(decision every {CONTROL_CADENCE_SEC/60:.0f} min, lookahead {LOOKAHEAD_SEC/60:.0f} min)")
    ctrl = run_controlled(
        truth, station_lat, station_lon,
        station_lat, station_lon, INITIAL_DEPTH_M,
        RUN_HOURS, DT_SEC, CONTROL_CADENCE_SEC,
    )

    print(f"running passive baseline (fixed {PASSIVE_DEPTH_M}m) for {RUN_HOURS}h")
    passive = run_passive(
        truth, station_lat, station_lon, PASSIVE_DEPTH_M,
        RUN_HOURS, DT_SEC,
    )

    ctrl_metrics = compute_station_metrics(
        ctrl["lats"], ctrl["lons"], station_lat, station_lon, STATION_ENVELOPE_M,
    )
    passive_metrics = compute_station_metrics(
        passive["lats"], passive["lons"], station_lat, station_lon, STATION_ENVELOPE_M,
    )

    print()
    print("=== results ===")
    print(f"controlled: {ctrl_metrics['frac_within']*100:.1f}% of {RUN_HOURS}h "
          f"within {STATION_ENVELOPE_M:.0f}m,  "
          f"max excursion {ctrl_metrics['max_excursion_m']:.0f}m,  "
          f"mean dist {ctrl_metrics['mean_m']:.0f}m")
    print(f"passive  : {passive_metrics['frac_within']*100:.1f}% of {RUN_HOURS}h "
          f"within {STATION_ENVELOPE_M:.0f}m,  "
          f"max excursion {passive_metrics['max_excursion_m']:.0f}m,  "
          f"mean dist {passive_metrics['mean_m']:.0f}m")
    print()
    print(f"perfect-knowledge station-keeping: {ctrl_metrics['frac_within']*100:.1f}% "
          f"of {RUN_HOURS}h within {STATION_ENVELOPE_M:.0f}m, "
          f"max excursion {ctrl_metrics['max_excursion_m']:.0f}m")

    # --- Plot ---
    fig = plt.figure(figsize=(15, 6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.1, 1.0], wspace=0.25)

    ax = fig.add_subplot(gs[0, 0])
    masked = np.where(bathy_grid > 0, bathy_grid, np.nan)
    ax.imshow(masked, origin="lower", cmap="Blues", alpha=0.4,
              extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
    ax.plot(passive["lons"], passive["lats"], "-", color="C3", lw=1.3,
            label=f"passive @ {PASSIVE_DEPTH_M:.0f}m")
    ax.plot(ctrl["lons"], ctrl["lats"], "-", color="C0", lw=1.8,
            label="perfect-knowledge controlled")
    ax.plot(station_lon, station_lat, "*", color="black", markersize=15,
            label="station")
    # Envelope ring.
    theta = np.linspace(0, 2 * np.pi, 200)
    env_dlat = (STATION_ENVELOPE_M / 111_320.0) * np.sin(theta)
    env_dlon = (STATION_ENVELOPE_M / (111_320.0 * np.cos(np.deg2rad(station_lat)))) * np.cos(theta)
    ax.plot(station_lon + env_dlon, station_lat + env_dlat, "--",
            color="black", alpha=0.5, label=f"{STATION_ENVELOPE_M:.0f}m envelope")
    ax.plot(ctrl["lons"][0], ctrl["lats"][0], "o", color="C0",
            markeredgecolor="black", markersize=8)
    ax.plot(passive["lons"][-1], passive["lats"][-1], "s", color="C3",
            markeredgecolor="black", markersize=8)
    ax.plot(ctrl["lons"][-1], ctrl["lats"][-1], "s", color="C0",
            markeredgecolor="black", markersize=8)
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title(f"Phase A: station-keeping in SalishSeaCast truth\n"
                 f"{RUN_HOURS}h from {MONTHS[0]}-01  station at ({station_lat:.3f}, {station_lon:.3f})")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.3)

    ax = fig.add_subplot(gs[0, 1])
    hrs = np.arange(ctrl["n_steps"] + 1) * (DT_SEC / 3600.0)
    ax.plot(hrs, ctrl_metrics["dists_m"], "-", color="C0", lw=1.8,
            label=f"controlled ({ctrl_metrics['frac_within']*100:.0f}% within {STATION_ENVELOPE_M:.0f}m)")
    ax.plot(hrs, passive_metrics["dists_m"], "-", color="C3", lw=1.3,
            label=f"passive ({passive_metrics['frac_within']*100:.0f}% within {STATION_ENVELOPE_M:.0f}m)")
    ax.axhline(STATION_ENVELOPE_M, ls="--", color="black", alpha=0.5,
               label=f"{STATION_ENVELOPE_M:.0f}m envelope")
    # Overlay chosen depth as a twin axis.
    ax2 = ax.twinx()
    decision_hrs = [t / 3600.0 for t, _ in ctrl["decisions"]]
    decision_depths = [d for _, d in ctrl["decisions"]]
    ax2.step(decision_hrs, decision_depths, where="post", color="C2",
             lw=1.0, alpha=0.6, label="chosen depth")
    ax2.set_ylabel("chosen depth (m)", color="C2")
    ax2.tick_params(axis="y", colors="C2")
    ax2.invert_yaxis()
    ax.set_xlabel("hours since start")
    ax.set_ylabel("distance from station (m)")
    ax.set_title(
        f"distance-from-station vs time\n"
        f"diversity: {diag['frac_reversible']*100:.0f}% of ticks have depth pair > 90° apart"
    )
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    out = FIG_DIR / "12_station_keeping_upper_bound.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")

    # Persist metrics for Phase B reuse.
    npz_out = FIG_DIR / "12_station_keeping_upper_bound.npz"
    np.savez(
        npz_out,
        station_lat=station_lat, station_lon=station_lon,
        ctrl_lats=ctrl["lats"], ctrl_lons=ctrl["lons"],
        ctrl_depths=ctrl["depths"], ctrl_setpoints=ctrl["setpoints"],
        passive_lats=passive["lats"], passive_lons=passive["lons"],
        ctrl_dist_m=ctrl_metrics["dists_m"],
        passive_dist_m=passive_metrics["dists_m"],
        envelope_m=STATION_ENVELOPE_M,
        run_hours=RUN_HOURS, dt_sec=DT_SEC,
    )
    print(f"[data] wrote {npz_out}")


if __name__ == "__main__":
    main()
