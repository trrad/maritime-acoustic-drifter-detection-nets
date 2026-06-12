"""Drifter mobility map — empirical "where can a drifter end up" envelope.

For each drop point in a coarse grid covering the patrol area + drift
buffer, run a full single-drifter mission (PF + bias + MPC + LoRa
surfacing under the chosen policy) for N seeds. Record the truth
trajectory and station-keeping diagnostics. Aggregate per-drop-point
statistics: mean trajectory, drift envelope (95% radius vs station
over time), station-keeping p50/p95, achievable σ_pos profile.

Output: `mobility_map_<policy>_<tag>.npz` containing per-(drop_point,
seed) trajectories + per-drop-point aggregates. The placement
optimizer reads this to ground its trajectory predictions in
empirical reality rather than ballistic-vs-static synthetic models.

Computation: 100 drop points × 4 seeds × 72h missions ≈ 400 single-
drifter sims; with 16 worker processes ≈ 2.5h wall. NEMO truth field
loaded once per worker (~5 min init).

Usage:
  uv run --with numpy --with scipy --with matplotlib --with filterpy --with pandas \\
    python _drifter_mobility_map.py \\
      --patrol-bbox 49.3527,-123.7445,49.3931,-123.6619 \\
      --buffer-m 5000 --grid-spacing-m 1500 \\
      --policy fixed_6h --n-seeds 4 --mission-hours 72 \\
      --tag 20260429_fixed6h_d4patrol

For runtime placement use (smart-redeploy orchestrator, fixed-anchor v1
cell), rebuild over the FULL SoG bbox at finer 500 m spacing:
  uv run ... python _drifter_mobility_map.py \\
      --patrol-bbox 49.15,-123.95,49.45,-123.50 \\
      --buffer-m 5000 --grid-spacing-m 500 \\
      --policy fixed_6h --n-seeds 4 --mission-hours 72 \\
      --tag sog_bbox_500m
500 m grid over 30×22 km bbox is ~2640 drops × 4 seeds = ~10 500 jobs;
at 16 workers ≈ 11 h wall. Pre-mission compute, run once before the
science sweep that consumes the smart-redeploy orchestrator.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from multiprocessing import Pool

import numpy as np   # type: ignore[import-not-found]


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fleet_sim_v0 as fs   # noqa: E402
from truth_field import EARTH_R_M  # type: ignore[import-not-found]  # noqa: E402


def _build_candidate_grid(
    patrol_bbox: tuple[float, float, float, float],
    buffer_m: float, grid_spacing_m: float,
) -> list[tuple[float, float]]:
    lat_min_p, lon_min_p, lat_max_p, lon_max_p = patrol_bbox
    cos_lat = float(np.cos(np.deg2rad(0.5 * (lat_min_p + lat_max_p))))
    d_lat = buffer_m / EARTH_R_M
    d_lon = buffer_m / (EARTH_R_M * cos_lat)
    lat_min = lat_min_p - d_lat
    lat_max = lat_max_p + d_lat
    lon_min = lon_min_p - d_lon
    lon_max = lon_max_p + d_lon
    s_lat = grid_spacing_m / EARTH_R_M
    s_lon = grid_spacing_m / (EARTH_R_M * cos_lat)
    lats = np.arange(lat_min, lat_max + s_lat * 0.5, s_lat)
    lons = np.arange(lon_min, lon_max + s_lon * 0.5, s_lon)
    return [(float(la), float(lo)) for la in lats for lo in lons]


def _job_for(
    job_idx: int, drop_lat: float, drop_lon: float, seed_idx: int,
    policy: str, run_hours: int,
) -> tuple:
    """Build a job tuple compatible with `_fleet_sim_v0._run_one_drifter`.

    `s_idx` is set to `job_idx` so each (drop, seed) pair gets a unique
    SEED_BASE + s_idx*100 RNG seed inside the worker. `station_target`
    is the drop point + a placeholder depth_hint=100 m (the worker
    resolves the actual bathymetry-bounded depth set per station).

    `audible_events` is empty: we don't simulate events here. For
    event-driven policies (`post_event_*`), the policy only fires its
    safety-cap surfaces — caller is responsible for using a
    fixed-cadence policy for the mobility map, OR for accepting the
    cap-only behavior as the no-event reference.
    """
    return (
        job_idx, policy, [],
        (drop_lat, drop_lon, 100.0),
        run_hours,
    )


def _run_jobs(jobs: list, n_workers: int) -> list[dict]:
    """Run mobility-map jobs through the fleet-sim worker pool. Worker
    init loads the NEMO truth field (~5 min once per worker)."""
    with Pool(processes=n_workers, initializer=fs._init_worker) as pool:
        out = pool.map(fs._run_one_drifter, jobs)
    return out


def _per_drop_aggregates(
    drifters_for_drop: list[dict],
    drop_lat: float, drop_lon: float,
) -> dict:
    """Aggregate stats across seeds for one drop point. Inputs are the
    drifter dicts (one per seed) returned by `_run_one_drifter`."""
    n_seeds = len(drifters_for_drop)
    truth_lats_all = np.stack([d["truth_lats"] for d in drifters_for_drop])
    truth_lons_all = np.stack([d["truth_lons"] for d in drifters_for_drop])
    n_ticks = truth_lats_all.shape[1]
    dt_sec = float(drifters_for_drop[0]["dt_sec"])
    t_sec = drifters_for_drop[0]["t_sec"]

    # Per-tick distance-from-drop across all seeds.
    cos_lat = float(np.cos(np.deg2rad(drop_lat)))
    dy = (truth_lats_all - drop_lat) * EARTH_R_M
    dx = (truth_lons_all - drop_lon) * EARTH_R_M * cos_lat
    dist_from_drop_m = np.sqrt(dx * dx + dy * dy)

    return {
        "drop_lat": drop_lat,
        "drop_lon": drop_lon,
        "n_seeds": n_seeds,
        "dt_sec": dt_sec,
        "t_sec": t_sec,
        # Per-seed full trajectories (n_seeds, T).
        "truth_lats": truth_lats_all,
        "truth_lons": truth_lons_all,
        # Per-tick station-keeping aggregated across seeds.
        "dist_p50_per_tick": np.median(dist_from_drop_m, axis=0),
        "dist_p95_per_tick": np.percentile(dist_from_drop_m, 95, axis=0),
        # Mission-level station-keeping summary (per seed; user can
        # aggregate as p50/p95 across seeds).
        "sk_p50_per_seed": np.median(dist_from_drop_m, axis=1),
        "sk_p95_per_seed": np.percentile(dist_from_drop_m, 95, axis=1),
        "sk_max_per_seed": np.max(dist_from_drop_m, axis=1),
        # Mean trajectory across seeds, useful as the "expected drifter
        # path" estimate fed to coverage evaluators.
        "mean_truth_lats": np.mean(truth_lats_all, axis=0),
        "mean_truth_lons": np.mean(truth_lons_all, axis=0),
        # Surfacing diagnostic (per-seed).
        "n_surfacings_per_seed": np.array(
            [d["n_surfacings"] for d in drifters_for_drop], dtype=int,
        ),
    }


def _build_npz_dict(
    grid: list[tuple[float, float]],
    aggregates: list[dict],
    policy: str, run_hours: int, n_seeds: int,
) -> dict:
    """Pack the per-grid aggregates into a flat npz dict."""
    out: dict = {}
    out["grid_lats"] = np.array([g[0] for g in grid], dtype=float)
    out["grid_lons"] = np.array([g[1] for g in grid], dtype=float)
    out["policy"] = np.array(policy, dtype="<U64")
    out["run_hours"] = np.int64(run_hours)
    out["n_seeds"] = np.int64(n_seeds)
    out["n_drops"] = np.int64(len(grid))
    if aggregates:
        out["dt_sec"] = np.float64(aggregates[0]["dt_sec"])
        out["t_sec"] = aggregates[0]["t_sec"]

    # Stack per-drop arrays.
    truth_lats = np.stack([a["truth_lats"] for a in aggregates])    # (D, S, T)
    truth_lons = np.stack([a["truth_lons"] for a in aggregates])
    out["truth_lats"] = truth_lats
    out["truth_lons"] = truth_lons
    out["dist_p50_per_tick"] = np.stack(
        [a["dist_p50_per_tick"] for a in aggregates],
    )                                                                # (D, T)
    out["dist_p95_per_tick"] = np.stack(
        [a["dist_p95_per_tick"] for a in aggregates],
    )
    out["sk_p50_per_seed"] = np.stack(
        [a["sk_p50_per_seed"] for a in aggregates],
    )                                                                # (D, S)
    out["sk_p95_per_seed"] = np.stack(
        [a["sk_p95_per_seed"] for a in aggregates],
    )
    out["sk_max_per_seed"] = np.stack(
        [a["sk_max_per_seed"] for a in aggregates],
    )
    out["mean_truth_lats"] = np.stack(
        [a["mean_truth_lats"] for a in aggregates],
    )                                                                # (D, T)
    out["mean_truth_lons"] = np.stack(
        [a["mean_truth_lons"] for a in aggregates],
    )
    out["n_surfacings_per_seed"] = np.stack(
        [a["n_surfacings_per_seed"] for a in aggregates],
    )                                                                # (D, S)
    return out


def _quick_diagnostic_chart(
    grid: list[tuple[float, float]],
    aggregates: list[dict],
    out_path: str,
) -> None:
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    # Left: drop-point grid colored by sk_p50 (median across seeds).
    ax = axes[0]
    sk_p50 = np.array(
        [np.median(a["sk_p50_per_seed"]) for a in aggregates],
    )
    drop_lats = np.array([a["drop_lat"] for a in aggregates])
    drop_lons = np.array([a["drop_lon"] for a in aggregates])
    sc = ax.scatter(drop_lons, drop_lats, c=sk_p50,
                     cmap="viridis_r", s=70, edgecolor="black",
                     linewidth=0.3)
    cb = plt.colorbar(sc, ax=ax, fraction=0.046)
    cb.set_label("median station-keeping p50 (m) across seeds")
    ax.set_xlabel("drop longitude")
    ax.set_ylabel("drop latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Drop-point grid colored by station-keeping floor")
    ax.grid(alpha=0.3)

    # Right: a few sample mean trajectories (every Nth drop).
    ax = axes[1]
    step = max(1, len(aggregates) // 20)
    for a in aggregates[::step]:
        mtlats = a["mean_truth_lats"]
        mtlons = a["mean_truth_lons"]
        ax.plot(mtlons, mtlats, "-", lw=0.6, alpha=0.6)
        ax.plot(a["drop_lon"], a["drop_lat"], "ko", ms=3)
        ax.plot(mtlons[-1], mtlats[-1], "rx", ms=5)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        "Sample mean trajectories\n"
        "(black dot = drop, red x = end-of-mission)"
    )
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--patrol-bbox", type=str, required=True,
                    help="lat_min,lon_min,lat_max,lon_max")
    p.add_argument("--buffer-m", type=float, default=5_000.0)
    p.add_argument("--grid-spacing-m", type=float, default=1_500.0)
    p.add_argument("--policy", type=str, default="fixed_6h")
    p.add_argument("--n-seeds", type=int, default=4)
    p.add_argument("--mission-hours", type=int, default=72)
    p.add_argument("--n-workers", type=int, default=16)
    p.add_argument("--out-dir", type=str,
                    default="experiments/harmonic_prototype/figures/"
                            "mobility_map")
    p.add_argument("--tag", type=str,
                    default=time.strftime("%Y%m%d-%H%M%S"))
    args = p.parse_args()

    patrol_bbox = tuple(float(x) for x in args.patrol_bbox.split(","))
    if len(patrol_bbox) != 4:
        print("ERROR: --patrol-bbox needs 4 comma-separated floats",
              file=sys.stderr)
        sys.exit(1)
    grid = _build_candidate_grid(
        patrol_bbox, args.buffer_m, args.grid_spacing_m,   # type: ignore[arg-type]
    )
    n_drops = len(grid)
    n_jobs = n_drops * args.n_seeds
    print(
        f"=== drifter mobility map ===\n"
        f"  patrol bbox: {patrol_bbox}\n"
        f"  buffer: {args.buffer_m} m, grid spacing: {args.grid_spacing_m} m\n"
        f"  grid: {n_drops} drops × {args.n_seeds} seeds = {n_jobs} sims\n"
        f"  policy: {args.policy}\n"
        f"  mission_hours: {args.mission_hours}\n"
        f"  workers: {args.n_workers}\n",
        flush=True,
    )

    # Build jobs: each (drop_idx × seed_idx) gets a unique s_idx so
    # _run_one_drifter's `seed = SEED_BASE + s_idx*100` produces a
    # unique RNG.
    jobs = []
    for drop_idx, (lat, lon) in enumerate(grid):
        for seed_idx in range(args.n_seeds):
            j_idx = drop_idx * args.n_seeds + seed_idx
            jobs.append(_job_for(
                j_idx, lat, lon, seed_idx,
                args.policy, args.mission_hours,
            ))

    print(f"--- running {n_jobs} sims ---", flush=True)
    t0 = time.time()
    drifter_dicts = _run_jobs(jobs, n_workers=args.n_workers)
    print(
        f"  done in {time.time() - t0:.0f}s = "
        f"{(time.time() - t0) / 60:.1f} min", flush=True,
    )

    # Group by drop point and aggregate.
    print("--- aggregating per drop point ---", flush=True)
    aggregates = []
    for drop_idx, (lat, lon) in enumerate(grid):
        seeds = drifter_dicts[
            drop_idx * args.n_seeds : (drop_idx + 1) * args.n_seeds
        ]
        aggregates.append(_per_drop_aggregates(seeds, lat, lon))

    # Save outputs.
    os.makedirs(args.out_dir, exist_ok=True)
    npz_path = os.path.join(
        args.out_dir,
        f"mobility_map_{args.policy}_{args.tag}.npz",
    )
    np.savez(
        npz_path,
        **_build_npz_dict(grid, aggregates, args.policy,
                           args.mission_hours, args.n_seeds),
    )
    print(f"  saved {npz_path}", flush=True)

    chart_path = os.path.join(
        args.out_dir,
        f"mobility_map_{args.policy}_{args.tag}_diag.png",
    )
    _quick_diagnostic_chart(grid, aggregates, chart_path)
    print(f"  saved {chart_path}", flush=True)

    # Quick console summary.
    sk_p50_med = np.array(
        [float(np.median(a["sk_p50_per_seed"])) for a in aggregates],
    )
    sk_p95_med = np.array(
        [float(np.median(a["sk_p95_per_seed"])) for a in aggregates],
    )
    print(
        f"\n=== summary ===\n"
        f"  drops sk_p50 (m, median across seeds): "
        f"min={sk_p50_med.min():.0f} "
        f"p25={np.percentile(sk_p50_med, 25):.0f} "
        f"p50={np.percentile(sk_p50_med, 50):.0f} "
        f"p75={np.percentile(sk_p50_med, 75):.0f} "
        f"max={sk_p50_med.max():.0f}\n"
        f"  drops sk_p95 (m, median across seeds): "
        f"min={sk_p95_med.min():.0f} "
        f"p25={np.percentile(sk_p95_med, 25):.0f} "
        f"p50={np.percentile(sk_p95_med, 50):.0f} "
        f"p75={np.percentile(sk_p95_med, 75):.0f} "
        f"max={sk_p95_med.max():.0f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
