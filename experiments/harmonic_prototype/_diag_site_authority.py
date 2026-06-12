"""Per-site authority + physics-floor diagnostic (2026-04-26).

What this measures:

  - `b_uncontrolled[d]` for d ∈ {0.5, 5, 10, 20, 50} m: passive drifter at
    each fixed depth, no controller. Best of these per site is what a
    drifter-class node achieves at zero control authority.
  - `b_truth_perf_traj`: TrajectoryStationKeeper with PerfectKnowledge
    (controller sees the ACTUAL truth currents for its lookahead).
    Multi-step Euler scoring + vertical-transit modelling. This is the
    physics-imposed floor at this site under our current best controller
    architecture.
  - `b_truth_perf_single`: original StationKeeper with PerfectKnowledge
    (single-point Euler scoring, no transit modelling). Tells us whether
    predictor architecture matters at the perfect-info ceiling.

Two derived metrics per site:

  - **Site authority** = best `b_uncontrolled` − `b_truth_perf_traj`.
    Sites with large authority delta have shear that a controller can
    exploit; sites with small delta are drifter-only zones in production
    drop planning.
  - **Predictor lift** = `b_truth_perf_single` − `b_truth_perf_traj`.
    How much of the controller-headroom comes from the multi-step
    integrator alone, even with perfect information. If small, the
    integrator alone isn't where to put effort; if large, it's a
    real lift before continuous-depth + posterior-aware MPC ship.

Mission profile, dt, lookahead, depth ladder all match the multi-seed
smoke harness so absolute numbers are comparable. NO PF, NO bias state,
NO sensors — these baselines isolate dynamics + control authority from
observer quality.

Per-site ranking output is the input to production drop-site planning:
sites with high site-authority + low absolute floor get tagged
shear-keeper; sites with low authority get tagged drifter-only.
"""

from __future__ import annotations

import os
import time
from multiprocessing import Pool, current_process

import numpy as np  # type: ignore[import-not-found]

from _diag_common import (  # type: ignore[import-not-found]
    DEFAULT_DEPTH_SET, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN,
    RealCurrents as _RealCurrents,
    init_diag_worker, summarise_dists as _summarise,
)

# All 8 hand-picked stations from 22_rbpf_v2_bias_learning.py.
STATIONS = [
    (49.3533, -123.7411, 289),
    (49.3533, -123.6892, 188),
    (49.3924, -123.7411, 182),
    (49.3924, -123.6374,  92),
    (49.3091, -123.6773, 115),
    (49.2699, -123.7033, 373),
    (49.3287, -123.7810, 410),
    (49.3533, -123.5855,  90),
]
# No seed axis here. There is no PF, no observation noise, no
# resampling — the truth + noise are deterministic given the worker's
# noise seed (=42), so a "seed sweep" at fixed noise field would give
# identical rows. The honest axis is "across NOISE realisations,"
# which requires per-job noise builds. Blocked on noise-cache work
# (task #8); separate sweep when ready.
# Worker count: 16. ~6-7GB per worker (vectorized state + Python +
# scipy + per-worker noise) × 16 ≈ 110GB, within the 140GB budget.
# 24 OOM'd this box.
N_PROCS = int(os.environ.get("AUTH_N_PROCS", "16"))

# Mission knobs (matched to multi-seed smoke unless overridden per-config).
RUN_HOURS = 72
DT_SEC = 600.0
CONTROL_CADENCE_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0

# Greedy perfect-controller configs (label, predictor, lookahead_sec).
# `step()` substeps internally for physical faithfulness; lookahead-window
# resolution is determined by the dynamics' `dt_sec` (600s default).
PERFECT_CONFIGS: list[tuple[str, str, float]] = [
    ("perf_single_30m",   "single",  1800.0),
    ("perf_traj_30m",     "traj",    1800.0),
    ("perf_traj_1h",      "traj",    3600.0),
    ("perf_traj_3h",      "traj",   10800.0),
    ("perf_traj_12h",     "traj",   43200.0),
]

# MPC configs (label, horizon_n, beam_width). `decision_interval_sec`
# is fixed at CONTROL_CADENCE_SEC=1800s, so horizon_n=N intervals →
# N*30min plan.
#
# Beam search prunes to top `beam_width` partial sequences after each
# horizon step. `beam_width >= 5^horizon_n` ⇒ exact brute force.
# At beam_width=200 with K=5 depth options, the per-decision RGI cost
# is dominated by ≤5*200=1000 active candidates regardless of horizon.
#
# h=6 (3 h, one M2 quarter — minimum tidally meaningful horizon, since
# M2 period is 12.4 h): brute-force at K^h=15625, fast enough.
# h=8 (4 h): beam search 200 — brute would be 7+ hours/mission.
# h=12 (6 h, half M2): beam search 200 — brute is infeasible.
# h=24 (12 h, full M2): beam 200, longest plan we test.
MPC_CONFIGS: list[tuple[str, int, int]] = [
    ("mpc_h6_3h_full",    6, 15625),   # brute force at h=6 (K^h)
    ("mpc_h8_4h_b200",    8,   200),
    ("mpc_h12_6h_b200",  12,   200),
    ("mpc_h24_12h_b200", 24,   200),
]

_W: dict = {}


def _init_worker() -> None:
    """Pool initializer wrapper — init_diag_worker takes the per-
    worker state dict so the diagnostic can keep using its module-
    local `_W` without exposing it to the shared module."""
    init_diag_worker(_W)


def _resolve_station(s_idx: int) -> tuple[float, float, float, list[float]]:
    """Snap target lat/lon to the SalishSeaCast cell grid; return
    (s_lat, s_lon, s_bathy, depth_set_for_this_station)."""
    nemo = _W["nemo"]
    bathy_grid = _W["bathy_grid"]
    s_lat_target, s_lon_target, _ = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    return s_lat, s_lon, s_bathy, d_set


def _run_passive(s_lat: float, s_lon: float, depth_m: float,
                   real: _RealCurrents) -> dict:
    """No controller. Hold setpoint. Drift."""
    from ballast_dynamics import BallastState, step  # type: ignore[import-not-found]
    from truth_field import distance_m  # type: ignore[import-not-found]

    def cur(t, lat, lon, d):
        return real.sample(lat, lon, d, t)

    state = BallastState(lat=s_lat, lon=s_lon,
                          depth_m=depth_m, depth_setpoint_m=depth_m)
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.full(n_steps + 1, depth_m)
    setpoints = np.full(n_steps + 1, depth_m)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    for i in range(n_steps):
        state = step(state, t_sec, DT_SEC,
                      current_at=cur, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
    dists = np.array([distance_m(la, lo, s_lat, s_lon)
                       for la, lo in zip(lats, lons)])
    summary = _summarise(dists)
    summary.update({"lats": lats, "lons": lons,
                    "depths": depths, "setpoints": setpoints,
                    "dists": dists})
    return summary


def _run_controlled(keeper, s_lat: float, s_lon: float,
                      d_set: list[float], real: _RealCurrents,
                      requires_current_depth: bool) -> dict:
    """Run a controller keeper through the 72h mission. Captures
    full lat/lon/depth/setpoint trajectory + dists for plotting."""
    from ballast_dynamics import (  # type: ignore[import-not-found]
        BallastState, set_setpoint, step,
    )
    from truth_field import distance_m  # type: ignore[import-not-found]

    def cur(t, lat, lon, d):
        return real.sample(lat, lon, d, t)

    initial_d = INITIAL_DEPTH_M if INITIAL_DEPTH_M in d_set else d_set[0]
    state = BallastState(lat=s_lat, lon=s_lon,
                          depth_m=initial_d, depth_setpoint_m=initial_d)
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    setpoints = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    depths[0] = state.depth_m
    setpoints[0] = state.depth_setpoint_m

    t_sec = 0.0
    last_decision_t = -CONTROL_CADENCE_SEC
    for i in range(n_steps):
        if t_sec - last_decision_t >= CONTROL_CADENCE_SEC - 1e-6:
            if requires_current_depth:
                chosen, _ = keeper.choose_depth(  # type: ignore[call-arg]
                    state.lat, state.lon, t_sec,
                    current_depth_m=state.depth_m,
                )
            else:
                chosen, _ = keeper.choose_depth(  # type: ignore[call-arg]
                    state.lat, state.lon, t_sec,
                )
            state = set_setpoint(state, chosen)
            last_decision_t = t_sec
        state = step(state, t_sec, DT_SEC,
                      current_at=cur, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
        depths[i + 1] = state.depth_m
        setpoints[i + 1] = state.depth_setpoint_m

    dists = np.array([distance_m(la, lo, s_lat, s_lon)
                       for la, lo in zip(lats, lons)])
    summary = _summarise(dists)
    summary.update({"lats": lats, "lons": lons,
                    "depths": depths, "setpoints": setpoints,
                    "dists": dists})
    return summary


def _run_perfect(s_lat: float, s_lon: float, d_set: list[float],
                   real: _RealCurrents,
                   predictor: str, lookahead_sec: float,
                   ) -> dict:
    """Greedy controller with PerfectKnowledge(real). predictor ∈ {single, traj}."""
    from ballast_controller import (  # type: ignore[import-not-found]
        PerfectKnowledge, StationKeeper, TrajectoryStationKeeper,
    )

    knowledge = PerfectKnowledge(truth=real)
    if predictor == "single":
        keeper = StationKeeper(
            station_lat=s_lat, station_lon=s_lon,
            available_depths_m=d_set, lookahead_sec=lookahead_sec,
            knowledge=knowledge,
        )
        requires_current_depth = False
    elif predictor == "traj":
        keeper = TrajectoryStationKeeper(
            station_lat=s_lat, station_lon=s_lon,
            available_depths_m=d_set, lookahead_sec=lookahead_sec,
            knowledge=knowledge,
            w_z_max_ms=W_Z_MAX_MS, dt_sec=DT_SEC,
        )
        requires_current_depth = True
    else:
        raise ValueError(f"unknown predictor {predictor}")
    return _run_controlled(keeper, s_lat, s_lon, d_set, real,
                            requires_current_depth)


def _run_mpc(s_lat: float, s_lon: float, d_set: list[float],
              real: _RealCurrents, horizon_n: int, beam_width: int) -> dict:
    """MPC controller with PerfectKnowledge(real). Beam-search depth
    sequence over `horizon_n` intervals, pruned to `beam_width`."""
    from ballast_controller import (  # type: ignore[import-not-found]
        MPCStationKeeper, PerfectKnowledge,
    )
    keeper = MPCStationKeeper(
        station_lat=s_lat, station_lon=s_lon,
        available_depths_m=d_set,
        horizon_n=horizon_n,
        decision_interval_sec=CONTROL_CADENCE_SEC,
        knowledge=PerfectKnowledge(truth=real),
        beam_width=beam_width,
        w_z_max_ms=W_Z_MAX_MS, dt_sec=DT_SEC,
    )
    return _run_controlled(keeper, s_lat, s_lon, d_set, real,
                            requires_current_depth=True)


def _run_one(args: tuple) -> dict:
    """Job dispatch.

    Job tuples:
      ("passive", s_idx, depth_m)
      ("perfect", s_idx, label, predictor, lookahead_sec)
      ("mpc",     s_idx, label, horizon_n)
    """
    kind = args[0]
    s_idx = args[1]
    s_lat, s_lon, s_bathy, d_set = _resolve_station(s_idx)
    real = _RealCurrents(nemo=_W["nemo"], noise=_W["noise"])

    t0 = time.time()
    if kind == "passive":
        depth_m = float(args[2])
        tag = f"passive_d{int(depth_m)}m"
        res = _run_passive(s_lat, s_lon, depth_m, real)
    elif kind == "perfect":
        label_short, predictor, lookahead_sec = args[2:]
        tag = label_short
        res = _run_perfect(
            s_lat, s_lon, d_set, real,
            predictor=predictor,
            lookahead_sec=float(lookahead_sec),
        )
    elif kind == "mpc":
        label_short, horizon_n, beam_width = args[2:]
        tag = label_short
        res = _run_mpc(s_lat, s_lon, d_set, real,
                        horizon_n=int(horizon_n),
                        beam_width=int(beam_width))
    else:
        raise ValueError(f"unknown kind {kind}")
    dt = time.time() - t0

    # Trajectory arrays (lats/lons/depths/setpoints/dists) come back from
    # _run_*; keep them in the row for plotting later.
    row = {
        "s_idx": s_idx, "kind": kind, "tag": tag,
        "station_lat": s_lat, "station_lon": s_lon, "bathy_m": s_bathy,
        **res, "dt": dt,
    }
    proc = current_process().name
    print(f"[{proc}] S{s_idx+1} {tag:<18} "
          f"mean={row['mean']:5.0f}m max={row['max']:5.0f}m "
          f"%500={row['pct500']:4.0f}% %1500={row['pct1500']:4.0f}% "
          f"%3000={row['pct3000']:4.0f}%  ({dt:5.1f}s)",
          flush=True)
    return row


def _plot_site(s_idx: int, rows: list[dict], out_path) -> None:
    """Per-site plot: trajectory map + dist-vs-time + depth-vs-time."""
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    s_lat = rows[0]["station_lat"]
    s_lon = rows[0]["station_lon"]
    s_bathy = rows[0]["bathy_m"]
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    n_steps = len(rows[0]["lats"])
    t_h = np.arange(n_steps) * DT_SEC / 3600.0

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    ax_xy, ax_d, ax_z = axes

    # --- Per-config style: passive=blues; greedy-perfect=plasma; MPC=viridis.
    passive_rows = [r for r in rows if r["kind"] == "passive"]
    perfect_rows = [r for r in rows if r["kind"] == "perfect"]
    mpc_rows = [r for r in rows if r["kind"] == "mpc"]

    cmap_pass = plt.get_cmap("Blues")
    cmap_perf = plt.get_cmap("plasma")
    cmap_mpc = plt.get_cmap("viridis")

    def to_local(lats, lons):
        ex = (lons - s_lon) * EARTH_R_M * cos_lat
        ny = (lats - s_lat) * EARTH_R_M
        return ex, ny

    # Plot passive trajectories — light background.
    for i, r in enumerate(sorted(passive_rows, key=lambda r: r["tag"])):
        c = cmap_pass(0.3 + 0.6 * i / max(len(passive_rows) - 1, 1))
        ex, ny = to_local(r["lats"], r["lons"])
        ax_xy.plot(ex, ny, color=c, lw=0.7, alpha=0.5, label=r["tag"])
        ax_d.plot(t_h, r["dists"], color=c, lw=0.7, alpha=0.5,
                   label=r["tag"])
        ax_z.plot(t_h, r["depths"], color=c, lw=0.7, alpha=0.5,
                   label=r["tag"])

    # Plot greedy perfect trajectories.
    perf_order = {c[0]: i for i, c in enumerate(PERFECT_CONFIGS)}
    perfect_rows = sorted(perfect_rows, key=lambda r: perf_order.get(r["tag"], 99))
    for i, r in enumerate(perfect_rows):
        c = cmap_perf(0.05 + 0.85 * i / max(len(perfect_rows) - 1, 1))
        ex, ny = to_local(r["lats"], r["lons"])
        ax_xy.plot(ex, ny, color=c, lw=1.4, alpha=0.85, label=r["tag"])
        ax_d.plot(t_h, r["dists"], color=c, lw=1.3, alpha=0.85,
                   label=r["tag"])
        ax_z.step(t_h, r["setpoints"], where="post", color=c, lw=1.3,
                   alpha=0.85, label=r["tag"])

    # Plot MPC trajectories — bold, on top.
    mpc_order: dict[str, int] = {c[0]: i for i, c in enumerate(MPC_CONFIGS)}
    mpc_rows = sorted(mpc_rows, key=lambda r: mpc_order.get(r["tag"], 99))
    for i, r in enumerate(mpc_rows):
        c = cmap_mpc(0.05 + 0.85 * i / max(len(mpc_rows) - 1, 1))
        ex, ny = to_local(r["lats"], r["lons"])
        ax_xy.plot(ex, ny, color=c, lw=1.8, alpha=0.95, label=r["tag"])
        ax_d.plot(t_h, r["dists"], color=c, lw=1.7, alpha=0.95,
                   label=r["tag"])
        ax_z.step(t_h, r["setpoints"], where="post", color=c, lw=1.7,
                   alpha=0.95, label=r["tag"])

    # Station marker + concentric envelopes for context.
    ax_xy.plot(0, 0, "k*", ms=14, label="station")
    for env_m in (500, 1500, 3000):
        theta = np.linspace(0, 2 * np.pi, 80)
        ax_xy.plot(env_m * np.cos(theta), env_m * np.sin(theta),
                    "k--", lw=0.4, alpha=0.4)
        ax_xy.annotate(f"{env_m}m", (env_m * 0.7, env_m * 0.7),
                        fontsize=7, alpha=0.5)
    ax_xy.set_xlabel("east (m from station)")
    ax_xy.set_ylabel("north (m from station)")
    ax_xy.set_title(f"S{s_idx+1} ({s_lat:.3f}, {s_lon:.3f})  "
                     f"bathy={s_bathy:.0f}m  ·  trajectories (72 h)")
    ax_xy.set_aspect("equal", "box")
    ax_xy.grid(alpha=0.3)
    ax_xy.legend(fontsize=6, loc="upper left", ncol=2)

    ax_d.axhline(500, color="k", ls=":", lw=0.4, alpha=0.4)
    ax_d.axhline(1500, color="k", ls=":", lw=0.4, alpha=0.4)
    ax_d.axhline(3000, color="k", ls=":", lw=0.4, alpha=0.4)
    ax_d.set_xlabel("mission time (h)")
    ax_d.set_ylabel("distance to station (m)")
    ax_d.set_title("distance to station vs time")
    ax_d.grid(alpha=0.3)

    ax_z.set_xlabel("mission time (h)")
    ax_z.set_ylabel("depth setpoint (m)")
    ax_z.set_title("depth choice over time (perfect controllers)")
    ax_z.invert_yaxis()
    ax_z.grid(alpha=0.3)
    ax_z.legend(fontsize=6, loc="lower right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    print(f"=== site-authority + physics-floor diagnostic "
          f"(N_PROCS={N_PROCS}) ===", flush=True)
    print(f"  stations: {len(STATIONS)}", flush=True)
    print(f"  perfect configs:", flush=True)
    for label, predictor, look in PERFECT_CONFIGS:
        print(f"    {label:<18} predictor={predictor:<6} "
              f"lookahead={look:7.0f}s", flush=True)
    print(f"  MPC configs:", flush=True)
    for label, h, bw in MPC_CONFIGS:
        full = len(DEFAULT_DEPTH_SET) ** h
        kind_tag = "BRUTE" if bw >= full else f"BEAM={bw}"
        print(f"    {label:<22} horizon_n={h:2d}  "
              f"({full:>9d} full / {kind_tag})",
              flush=True)

    jobs: list[tuple] = []
    for s_idx in range(len(STATIONS)):
        for d in DEFAULT_DEPTH_SET:
            jobs.append(("passive", s_idx, d))
        for cfg in PERFECT_CONFIGS:
            label, predictor, look = cfg
            jobs.append(("perfect", s_idx, label, predictor, look))
        for cfg in MPC_CONFIGS:
            label, h, bw = cfg
            jobs.append(("mpc", s_idx, label, h, bw))
    print(f"  total jobs: {len(jobs)}", flush=True)

    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        results = pool.map(_run_one, jobs)
    print(f"\nall {len(results)} runs done; total wall-clock "
          f"{time.time() - t0:.0f}s", flush=True)

    # --- Bucketise by station ---
    by_site: dict[int, list[dict]] = {}
    for row in results:
        by_site.setdefault(row["s_idx"], []).append(row)

    # --- Per-site numeric report (greedy + MPC controllers) ---
    perfect_labels = [c[0] for c in PERFECT_CONFIGS]
    mpc_labels = [c[0] for c in MPC_CONFIGS]
    all_ctrl_labels = perfect_labels + mpc_labels
    header_perf = " ".join(f"{l:>14}" for l in perfect_labels)
    header_mpc = " ".join(f"{l:>13}" for l in mpc_labels)
    print(f"\n--- per-site mean dist (m) — greedy + MPC controllers ---",
          flush=True)
    print(f"{'station':<28} {header_perf}    {header_mpc}    "
          f"{'best_passive':>22}",
          flush=True)
    print("-" * (28 + 15 * len(perfect_labels) + 14 * len(mpc_labels) + 30),
          flush=True)

    site_summaries: list[dict] = []
    for s_idx in range(len(STATIONS)):
        rows = by_site[s_idx]
        s_lat = rows[0]["station_lat"]
        s_lon = rows[0]["station_lon"]
        s_bathy = rows[0]["bathy_m"]
        slabel = f"S{s_idx+1} ({s_lat:.3f},{s_lon:.3f}) {s_bathy:.0f}m"
        ctrl_means: dict[str, float] = {}
        for tag in all_ctrl_labels:
            match = [r for r in rows if r["tag"] == tag]
            ctrl_means[tag] = float(match[0]["mean"]) if match else float("nan")
        passive_means = [(r, r["mean"]) for r in rows if r["kind"] == "passive"]
        if not passive_means:
            continue
        best_passive_row, best_passive_mean = min(passive_means,
                                                     key=lambda x: x[1])
        best_d = float(best_passive_row["tag"]
                        .replace("passive_d", "").replace("m", ""))
        cells_perf = " ".join(f"{ctrl_means[l]:14.0f}" for l in perfect_labels)
        cells_mpc = " ".join(f"{ctrl_means[l]:13.0f}" for l in mpc_labels)
        print(f"{slabel:<28} {cells_perf}    {cells_mpc}    "
              f"d={int(best_d):2d}m {best_passive_mean:7.0f}m",
              flush=True)
        best_ctrl_label = min(all_ctrl_labels, key=lambda l: ctrl_means[l])
        site_summaries.append({
            "s_idx": s_idx, "label": slabel,
            "perf_means": ctrl_means,
            "best_perf_label": best_ctrl_label,
            "best_perf_mean": ctrl_means[best_ctrl_label],
            "best_passive_d": best_d,
            "best_passive_mean": best_passive_mean,
            "site_authority": best_passive_mean - ctrl_means[best_ctrl_label],
        })

    # --- Per-config sweep summary across sites ---
    print(f"\n--- controller config effect on perfect-info ceiling "
          f"(mean of mean dist over all sites) ---", flush=True)
    print(f"{'config':<20} {'mean across sites (m)':>22}", flush=True)
    print("-" * 45, flush=True)
    for tag in all_ctrl_labels:
        vals = np.array([s["perf_means"][tag] for s in site_summaries
                          if np.isfinite(s["perf_means"][tag])])
        if vals.size:
            print(f"{tag:<20} {float(vals.mean()):20.0f}m",
                  flush=True)

    # --- Site-authority ranking by best controller (greedy or MPC) ---
    print(f"\n--- sites ranked by authority "
          f"(best passive − BEST-of-all-controller-configs) ---", flush=True)
    print(f"{'rank':>4}  {'station':<26} "
          f"{'authority':>12} {'best ctrl cfg':>22} "
          f"{'perf_floor':>12} {'best_passive':>14}",
          flush=True)
    ranked = sorted(site_summaries, key=lambda r: -r["site_authority"])
    for i, r in enumerate(ranked):
        print(f"{i+1:>4}  {r['label']:<26} "
              f"{r['site_authority']:+10.0f}m  "
              f"{r['best_perf_label']:>22}  "
              f"{r['best_perf_mean']:8.0f}m  "
              f"d={int(r['best_passive_d']):2d}m {r['best_passive_mean']:6.0f}m",
              flush=True)

    # --- Per-site plots ---
    from pathlib import Path
    out_dir = Path(__file__).parent / "figures" / "_diag_site_authority"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nrendering per-site plots to {out_dir} ...", flush=True)
    for s_idx in range(len(STATIONS)):
        out_path = out_dir / f"S{s_idx+1}_site_authority.png"
        _plot_site(s_idx, by_site[s_idx], out_path)
        print(f"  S{s_idx+1} → {out_path.name}", flush=True)

    print(f"\n=== done ===", flush=True)


if __name__ == "__main__":
    main()
