"""SoG site-survey grid scan (2026-05-04).

Grid-scan extension of `_diag_site_authority.py`. Replaces the 8 hand-
picked stations with a regular grid over (LAT_MIN..LAT_MAX) ×
(LON_MIN..LON_MAX) at configurable spacing. For each grid point and each
surfacing-cadence in the policy axis, runs:

  - Per-depth `b_uncontrolled[d]` for d ∈ {0.5, 5, 10, 20, 50} m: passive
    drifter at fixed depth, NO controller. Best of these per-site is
    what a drifter-class node achieves at zero control authority.
  - `b_truth_perf_traj`: TrajectoryStationKeeper with PerfectKnowledge
    (sees ACTUAL truth currents for its lookahead). The physics-imposed
    floor at this site under our current best controller architecture.

Both runs are interrupted by periodic forced-surfacing dwells per the
sweep's surfacing axis: every `period_h` the drifter is forced to
0.5 m for `surface_dwell_h` (default 0.5 h, matching SimConfig). The
controller resumes after each dwell. Compares fixed_24h vs fixed_8h
(default) so we can see whether tighter cadences open station-keeping
at sites the longer cadence rules out.

NO PF, NO observation noise, NO bias state — pure dynamics + control
authority diagnostic. Deterministic given the noise seed.

PRIMARY purpose: characterize the SoG bbox's site-authority distribution
— is the basin worst/middle/best case for our 24h-planning drifter?
A long tail of high-authority sites we're not using = bbox is fine,
just need better picks. Flat distribution near zero = bbox is dynamics-
limited, alternative regions are urgent.

SECONDARY purpose: emit a station-keeping-optimized SoG density config
(`D7_skopt_<N>`) for A/B vs the empirically-tuned `D6_empirical`.

Outputs in `figures/sog_site_survey/`:
  - sites_<spacing>m.csv — per-(site, policy) rows with bathy, best
    passive, best controlled, site authority, predictor lift
  - distribution_<spacing>m.png — histogram of site_authority over
    bbox, faceted by surfacing policy (the headline figure)
  - D7_skopt_<N>_<policy>.txt — paste-ready DensityConfig snippet for
    `_fleet_sweep_v0.py`'s DENSITY_CONFIGS
  - per-site_top<N>_<policy>.png — quick visual of the top-N selected
    sites overlaid on the bathy / passive-drift map

Env knobs (defaults are a coarse, fast first-pass):
  SURVEY_SPACING_M     — grid spacing in meters (default 3000.0)
  SURVEY_CADENCES_H    — comma-separated surface cadences in hours
                          (default "8,24")
  SURVEY_TOP_N         — top-N picker count for D7_skopt emit (default 16)
  SURVEY_N_PROCS       — worker pool size (default 16)
  SURVEY_LOOKAHEAD_SEC — perfect-traj lookahead (default 1800)
  SURVEY_RUN_HOURS     — mission length in hours (default 72)
"""

from __future__ import annotations

import csv
import os
import time
from multiprocessing import Pool, current_process
from pathlib import Path

import numpy as np  # type: ignore[import-not-found]

from _diag_common import (  # type: ignore[import-not-found]
    DEFAULT_DEPTH_SET, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN,
    RealCurrents as _RealCurrents,
    init_diag_worker, summarise_dists as _summarise,
)
from truth_field import EARTH_R_M  # type: ignore[import-not-found]

SURVEY_SPACING_M = float(os.environ.get("SURVEY_SPACING_M", "3000.0"))
RUN_HOURS = int(os.environ.get("SURVEY_RUN_HOURS", "72"))
DT_SEC = 600.0
CONTROL_CADENCE_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0
SURFACE_DEPTH_M = 0.5
SURFACE_DWELL_H = 0.5

# Surfacing-cadence sweep axis. "0" disables forced surfacing (legacy
# pure-controlled run). Any positive value forces a `SURFACE_DWELL_H`
# dwell every `period_h` hours.
def _csv_floats(env_name: str, default: str) -> tuple[float, ...]:
    raw = os.environ.get(env_name, default).strip()
    return tuple(float(x.strip()) for x in raw.split(",") if x.strip())


SURVEY_CADENCES_H = _csv_floats("SURVEY_CADENCES_H", "8,24")
SURVEY_TOP_N = int(os.environ.get("SURVEY_TOP_N", "16"))
N_PROCS = int(os.environ.get("SURVEY_N_PROCS", "16"))
LOOKAHEAD_SEC = float(os.environ.get("SURVEY_LOOKAHEAD_SEC", "1800.0"))


# ---- Per-worker truth + bathy cache ----

_W: dict = {}


def _init_worker() -> None:
    init_diag_worker(_W)


def _build_grid() -> list[tuple[float, float]]:
    """Regular lat/lon grid at SURVEY_SPACING_M spacing. Land filtering
    happens later (per-worker, using bathy_grid)."""
    cos_lat = float(np.cos(np.deg2rad(0.5 * (LAT_MIN + LAT_MAX))))
    d_lat = SURVEY_SPACING_M / EARTH_R_M
    d_lon = SURVEY_SPACING_M / (EARTH_R_M * cos_lat)
    n_lat = int(np.floor((LAT_MAX - LAT_MIN) / d_lat))
    n_lon = int(np.floor((LON_MAX - LON_MIN) / d_lon))
    lats = LAT_MIN + (np.arange(n_lat + 1) + 0.5) * (LAT_MAX - LAT_MIN) / (n_lat + 1)
    lons = LON_MIN + (np.arange(n_lon + 1) + 0.5) * (LON_MAX - LON_MIN) / (n_lon + 1)
    return [(float(la), float(lo)) for la in lats for lo in lons]


def _resolve_site(site_lat: float, site_lon: float
                   ) -> tuple[float, float, float, list[float]] | None:
    """Snap to NEMO grid and return (s_lat, s_lon, s_bathy, depth_set).
    Returns None if site is over land (s_bathy NaN or <=0)."""
    nemo = _W["nemo"]
    bathy_grid = _W["bathy_grid"]
    gy = int(np.argmin(np.abs(nemo.lat_axis - site_lat)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - site_lon)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    if not np.isfinite(s_bathy) or s_bathy <= 1.0:
        return None
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    if not d_set:
        return None
    return s_lat, s_lon, s_bathy, d_set


def _surface_mask(n_steps: int, period_h: float, dwell_h: float
                   ) -> np.ndarray:
    """Boolean mask: True at ticks where the drifter is forced to surface.

    First surface event starts at `period_h` (the drifter has just been
    deployed at t=0; it doesn't immediately surface). `period_h <= 0`
    disables surfacing (all-False mask).
    """
    mask = np.zeros(n_steps + 1, dtype=bool)
    if period_h <= 0:
        return mask
    dwell_steps = max(1, int(round(dwell_h * 3600.0 / DT_SEC)))
    period_steps = max(dwell_steps + 1, int(round(period_h * 3600.0 / DT_SEC)))
    t_idx = period_steps
    while t_idx <= n_steps:
        mask[t_idx:t_idx + dwell_steps] = True
        t_idx += period_steps
    return mask


def _run_passive(s_lat: float, s_lon: float, depth_m: float,
                   real: _RealCurrents, period_h: float) -> dict:
    """Passive (no controller). Drifter holds setpoint=depth_m unless
    forced to SURFACE_DEPTH_M during a periodic dwell."""
    from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
    from truth_field import distance_m  # type: ignore[import-not-found]

    def cur(t, lat, lon, d):
        return real.sample(lat, lon, d, t)

    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    surf_mask = _surface_mask(n_steps, period_h, SURFACE_DWELL_H)
    state = BallastState(lat=s_lat, lon=s_lon,
                          depth_m=depth_m, depth_setpoint_m=depth_m)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    for i in range(n_steps):
        target_depth = SURFACE_DEPTH_M if surf_mask[i] else depth_m
        if state.depth_setpoint_m != target_depth:
            state = set_setpoint(state, target_depth)
        state = step(state, t_sec, DT_SEC,
                      current_at=cur, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
    dists = np.array([distance_m(la, lo, s_lat, s_lon)
                       for la, lo in zip(lats, lons)])
    return _summarise(dists)


def _run_perfect_traj(s_lat: float, s_lon: float, d_set: list[float],
                        real: _RealCurrents, period_h: float) -> dict:
    """TrajectoryStationKeeper with PerfectKnowledge(real). Forced
    surface dwells override the controller's depth choice during dwell
    windows; controller resumes after."""
    from ballast_controller import (  # type: ignore[import-not-found]
        PerfectKnowledge, TrajectoryStationKeeper,
    )
    from ballast_dynamics import (  # type: ignore[import-not-found]
        BallastState, set_setpoint, step,
    )
    from truth_field import distance_m  # type: ignore[import-not-found]

    def cur(t, lat, lon, d):
        return real.sample(lat, lon, d, t)

    knowledge = PerfectKnowledge(truth=real)
    keeper = TrajectoryStationKeeper(
        station_lat=s_lat, station_lon=s_lon,
        available_depths_m=d_set, lookahead_sec=LOOKAHEAD_SEC,
        knowledge=knowledge,
        w_z_max_ms=W_Z_MAX_MS, dt_sec=DT_SEC,
    )
    initial_d = INITIAL_DEPTH_M if INITIAL_DEPTH_M in d_set else d_set[0]
    state = BallastState(lat=s_lat, lon=s_lon,
                          depth_m=initial_d, depth_setpoint_m=initial_d)
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    surf_mask = _surface_mask(n_steps, period_h, SURFACE_DWELL_H)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision_t = -CONTROL_CADENCE_SEC
    for i in range(n_steps):
        if surf_mask[i]:
            if state.depth_setpoint_m != SURFACE_DEPTH_M:
                state = set_setpoint(state, SURFACE_DEPTH_M)
        else:
            if t_sec - last_decision_t >= CONTROL_CADENCE_SEC - 1e-6:
                chosen, _ = keeper.choose_depth(  # type: ignore[call-arg]
                    state.lat, state.lon, t_sec,
                    current_depth_m=state.depth_m,
                )
                state = set_setpoint(state, chosen)
                last_decision_t = t_sec
        state = step(state, t_sec, DT_SEC,
                      current_at=cur, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1] = state.lat
        lons[i + 1] = state.lon
    dists = np.array([distance_m(la, lo, s_lat, s_lon)
                       for la, lo in zip(lats, lons)])
    return _summarise(dists)


def _run_one_job(args: tuple) -> dict | None:
    """Job dispatch. Args: (site_idx, site_lat, site_lon, period_h)."""
    site_idx, site_lat, site_lon, period_h = args
    resolved = _resolve_site(site_lat, site_lon)
    if resolved is None:
        return None
    s_lat, s_lon, s_bathy, d_set = resolved
    real = _RealCurrents(nemo=_W["nemo"], noise=_W["noise"])

    t0 = time.time()
    # All passive depths.
    passive_results: list[tuple[float, dict]] = []
    for d in d_set:
        try:
            res = _run_passive(s_lat, s_lon, d, real, period_h)
            passive_results.append((d, res))
        except Exception as e:
            print(f"  [s{site_idx}] passive d={d}m FAILED: {e}", flush=True)
    if not passive_results:
        return None

    # Perfect-traj controller.
    try:
        traj_res = _run_perfect_traj(s_lat, s_lon, d_set, real, period_h)
    except Exception as e:
        print(f"  [s{site_idx}] perfect_traj FAILED: {e}", flush=True)
        traj_res = None

    best_passive_d, best_passive_summary = min(
        passive_results, key=lambda kv: kv[1]["mean"],
    )
    best_passive_mean = best_passive_summary["mean"]
    traj_mean = traj_res["mean"] if traj_res is not None else float("nan")
    site_authority = best_passive_mean - traj_mean if np.isfinite(traj_mean) \
        else float("nan")

    dt = time.time() - t0
    proc = current_process().name
    print(
        f"[{proc}] s{site_idx:>4} ({s_lat:.4f},{s_lon:.4f}) bathy={s_bathy:5.0f}m "
        f"period={period_h:>5}h: best_pass(d={int(best_passive_d):2d}m)={best_passive_mean:6.0f}m "
        f"traj={traj_mean:6.0f}m authority={site_authority:+6.0f}m ({dt:5.1f}s)",
        flush=True,
    )
    return {
        "site_idx": site_idx,
        "lat": s_lat,
        "lon": s_lon,
        "bathy_m": s_bathy,
        "period_h": period_h,
        "best_passive_d_m": best_passive_d,
        "b_uncontrolled_mean_m": best_passive_mean,
        "b_truth_perf_traj_m": traj_mean,
        "site_authority_m": site_authority,
        "passive_per_depth_mean_m": {d: r["mean"] for d, r in passive_results},
        "n_depths_evaluated": len(d_set),
    }


# ---- Aggregation + outputs ----


def _ranked(rows: list[dict], policy_h: float) -> list[dict]:
    by_pol = [r for r in rows if r["period_h"] == policy_h
              and np.isfinite(r["site_authority_m"])]
    return sorted(by_pol, key=lambda r: -r["site_authority_m"])


def _emit_density_config(top_rows: list[dict], n: int, policy_h: float,
                          out_dir: Path) -> Path:
    """Paste-ready `_fleet_sweep_v0.py` DensityConfig snippet."""
    name = f"D7_skopt_{n}"
    label = (f"N={n} site-authority optimised "
             f"(SoG survey, fixed_{int(policy_h)}h)")
    selected = top_rows[:n]
    lines = [
        f"DensityConfig(",
        f'    name="{name}",',
        f'    label="{label}",',
        f"    stations=(",
    ]
    for r in selected:
        lines.append(
            f"        ({r['lat']:.6f}, {r['lon']:.6f}, "
            f"{r['bathy_m']:.1f}),"
        )
    lines.append(f"    ),")
    lines.append(f"),")
    out_path = out_dir / f"{name}_fixed{int(policy_h)}h_snippet.txt"
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def _save_csv(rows: list[dict], out_path: Path) -> None:
    if not rows:
        out_path.write_text("# no rows\n")
        return
    fieldnames = [
        "site_idx", "lat", "lon", "bathy_m", "period_h",
        "best_passive_d_m", "b_uncontrolled_mean_m",
        "b_truth_perf_traj_m", "site_authority_m",
        "n_depths_evaluated",
    ]
    with out_path.open("w") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def _plot_distribution(rows: list[dict], policies_h: tuple[float, ...],
                        out_path: Path) -> None:
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]

    fig, axes = plt.subplots(len(policies_h), 2,
                              figsize=(13, 4.0 * len(policies_h)),
                              squeeze=False)
    for i, pol in enumerate(policies_h):
        ax_h, ax_s = axes[i]
        rs = [r for r in rows if r["period_h"] == pol
              and np.isfinite(r["site_authority_m"])]
        if not rs:
            ax_h.text(0.5, 0.5, f"no sites for fixed_{int(pol)}h",
                       ha="center", va="center", transform=ax_h.transAxes)
            ax_s.set_axis_off()
            continue
        authority = np.array([r["site_authority_m"] for r in rs])
        ax_h.hist(authority, bins=30, edgecolor="k", alpha=0.7)
        ax_h.axvline(0, color="k", lw=0.8)
        ax_h.set_title(f"Site authority distribution — fixed_{int(pol)}h "
                        f"(N={len(rs)} sites)")
        ax_h.set_xlabel("site authority (best passive − perf_traj) [m]")
        ax_h.set_ylabel("# sites")
        ax_h.grid(alpha=0.3)
        # Spatial map: site_authority colormapped by lat/lon.
        lats = np.array([r["lat"] for r in rs])
        lons = np.array([r["lon"] for r in rs])
        sc = ax_s.scatter(lons, lats, c=authority, cmap="RdYlGn",
                            s=30, edgecolor="k", linewidth=0.4)
        plt.colorbar(sc, ax=ax_s, label="site authority (m)")
        ax_s.set_title(f"Spatial map — fixed_{int(pol)}h")
        ax_s.set_xlabel("lon")
        ax_s.set_ylabel("lat")
        ax_s.set_aspect("equal", adjustable="datalim")
        ax_s.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    print(f"=== SoG site-survey grid scan ===", flush=True)
    print(f"  spacing: {SURVEY_SPACING_M:.0f}m  cadences (h): "
          f"{SURVEY_CADENCES_H}  N_PROCS: {N_PROCS}", flush=True)
    grid = _build_grid()
    print(f"  raw grid points: {len(grid)} (incl. land — filtered in worker)",
          flush=True)
    jobs: list[tuple] = []
    for site_idx, (la, lo) in enumerate(grid):
        for pol in SURVEY_CADENCES_H:
            jobs.append((site_idx, la, lo, float(pol)))
    print(f"  jobs queued: {len(jobs)} "
          f"({len(grid)} sites × {len(SURVEY_CADENCES_H)} cadences)",
          flush=True)

    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        raw_rows = pool.map(_run_one_job, jobs)
    rows = [r for r in raw_rows if r is not None]
    print(f"\n{len(rows)} successful rows; "
          f"{len(jobs) - len(rows)} dropped (land or failed). "
          f"Wall: {time.time() - t0:.0f}s", flush=True)

    out_dir = Path(__file__).parent / "figures" / "sog_site_survey"
    out_dir.mkdir(parents=True, exist_ok=True)
    spacing_label = f"{int(SURVEY_SPACING_M)}m"

    csv_path = out_dir / f"sites_{spacing_label}.csv"
    _save_csv(rows, csv_path)
    print(f"  wrote {csv_path}", flush=True)

    dist_path = out_dir / f"distribution_{spacing_label}.png"
    _plot_distribution(rows, SURVEY_CADENCES_H, dist_path)
    print(f"  wrote {dist_path}", flush=True)

    # Per-policy ranking + density-config snippet.
    print(f"\n--- top-{SURVEY_TOP_N} per cadence (sites by authority) ---",
          flush=True)
    for pol in SURVEY_CADENCES_H:
        ranked = _ranked(rows, pol)
        if not ranked:
            print(f"  fixed_{int(pol)}h: no rows", flush=True)
            continue
        print(f"\n  fixed_{int(pol)}h (N={len(ranked)}):", flush=True)
        for i, r in enumerate(ranked[:SURVEY_TOP_N]):
            print(f"    {i+1:>3} ({r['lat']:.4f},{r['lon']:.4f}) "
                  f"bathy={r['bathy_m']:5.0f}m  "
                  f"authority={r['site_authority_m']:+6.0f}m  "
                  f"floor={r['b_truth_perf_traj_m']:6.0f}m  "
                  f"passive(d={int(r['best_passive_d_m']):2d}m)="
                  f"{r['b_uncontrolled_mean_m']:6.0f}m",
                  flush=True)
        snippet_path = _emit_density_config(ranked, SURVEY_TOP_N, pol, out_dir)
        print(f"    → {snippet_path}", flush=True)

        # Distribution headline summary.
        auth = np.array([r["site_authority_m"] for r in ranked])
        print(f"    distribution: mean={auth.mean():.0f}m  "
              f"p50={np.median(auth):.0f}m  "
              f"p90={np.percentile(auth, 90):.0f}m  "
              f"max={auth.max():.0f}m  "
              f"%>500m authority={(auth > 500).mean() * 100:.0f}%",
              flush=True)

    print(f"\n=== done ===", flush=True)


if __name__ == "__main__":
    main()
