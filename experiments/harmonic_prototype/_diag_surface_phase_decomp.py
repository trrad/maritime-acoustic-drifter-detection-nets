"""Decompose perfect_info mean_dist into surface-phase components.

Question: of the ~1436m perfect_info mean_dist (and the 552m no_learn →
perfect_info closure gap), how much lives in:

  - surface dwell:  forced surface time at depth ≤ 1m (LoRa fix window),
                     where MPC has no depth choice and currents are
                     strongest (no depth attenuation)
  - submerged steady: drifter at depth, controller actively keeping
                       station against post-resurface position offset
  - re-convergence:   the early portion of each submerged leg, when the
                       controller is pulling back from wherever the
                       surface dwell deposited it

Approach: run perfect_info × 4 stations × 5 seeds = 20 missions, capture
per-tick (dists_m, at_surface_mask, depths). Decompose:

  - mean_dist (whole mission)
  - mean_dist | at surface
  - mean_dist | submerged
  - mean_dist as function of time-since-surface-exit (re-convergence trace)
  - "instantaneous-resurface counterfactual" = mean over submerged ticks
    only — i.e., mean_dist if surfacing took zero time

Saves chart to figures/surface_phase_decomp.png. Prints aggregation table.
"""

from __future__ import annotations

import os
import sys
import time
from multiprocessing import Pool, current_process

import numpy as np  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]
STATIONS = [
    (49.3533, -123.7411, 289),
    (49.3533, -123.6892, 188),
    (49.3924, -123.7411, 182),
    (49.3924, -123.6374,  92),
]
SEED_BASE = 1000
N_SEEDS = int(os.environ.get("DIAG_N_SEEDS", "5"))
N_PROCS = int(os.environ.get("DIAG_N_PROCS", "16"))

_W: dict = {}


def _init_worker():
    from salishseacast_cache import (  # type: ignore[import-not-found]
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import (  # type: ignore[import-not-found]
        build_layered_noise_field,
        build_layered_tracer_noise_field,
    )
    from truth_field import (  # type: ignore[import-not-found]
        build_tracer_field, build_truth_field,
    )
    label = current_process().name
    t0 = time.time()
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=True)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    tracer = build_tracer_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    noise = build_layered_noise_field(ds, lats_grid, lons_grid, seed=42)
    tracer_noise = build_layered_tracer_noise_field(
        ds, lats_grid, lons_grid, seed=42,
    )
    _W["nemo"] = nemo
    _W["tracer"] = tracer
    _W["noise"] = noise
    _W["tracer_noise"] = tracer_noise
    _W["bathy_grid"] = bathy_grid
    print(f"[{label}] init done ({time.time() - t0:.1f}s)", flush=True)


class _RealCurrents:
    def __init__(self, nemo, noise):
        self.nemo, self.noise = nemo, noise

    def sample(self, lat, lon, depth_m, t_sec):
        ut, vt = self.nemo.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)
        return ut + un, vt + vn

    def sample_batched(self, lats, lons, depths, t_sec):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t_sec)
        un, vn = self.noise.sample_batched(lats, lons, depths, t_sec)
        u = np.where(np.isfinite(ut), ut + un, np.nan)
        v = np.where(np.isfinite(vt), vt + vn, np.nan)
        return u, v

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)

    def get_current_at_batched(self, lats, lons, depths, t_sec):
        return self.sample_batched(lats, lons, depths, t_sec)


class _RealTracer:
    def __init__(self, tracer, tracer_noise):
        self.tracer, self.tracer_noise = tracer, tracer_noise

    def sample(self, lat, lon, depth_m, t_sec):
        Tt, St = self.tracer.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(Tt) and np.isfinite(St)):
            return float("nan"), float("nan")
        Tn, Sn = self.tracer_noise.sample(lat, lon, depth_m, t_sec)
        return Tt + Tn, St + Sn


class _NemoPrior:
    def __init__(self, nemo): self.nemo = nemo

    def sample(self, l, lo, d, t):
        return self.nemo.sample(l, lo, d, t)

    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)

    def get_current_at(self, *a):
        return self.sample(*a)

    def get_current_at_batched(self, *a):
        return self.sample_batched(*a)


def _make_bias():
    from rbpf_prototype import BiasConfig  # type: ignore[import-not-found]
    return BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=float(np.sqrt(
            0.04**2 + 0.02**2 + 0.05**2)),
    )


def _run_one(args: tuple) -> dict:
    s_idx, seed_idx = args
    from ballast_controller import PerfectKnowledge  # type: ignore[import-not-found]
    from rbpf_prototype import (  # type: ignore[import-not-found]
        CTDSensor, Experiment, FixedIntervalPolicy,
        LoRaRangeSensor, PFConfig, SensorConfig, SimConfig,
        StationConfig, run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    nemo = _W["nemo"]; tracer = _W["tracer"]
    noise = _W["noise"]; tracer_noise = _W["tracer_noise"]
    bathy_grid = _W["bathy_grid"]

    s_lat_target, s_lon_target, _ = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(lat=s_lat, lon=s_lon, envelope_m=3000.0,
                              available_depths_m=d_set)
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    anchors = [
        (s_lat + dn * 1000.0 / EARTH_R_M,
         s_lon + de * 1000.0 / (EARTH_R_M * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]

    sim = SimConfig(
        run_hours=72, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model="ou_integrated",
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)
    sensor_cfg = SensorConfig(
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0,
                              max_depth_m=1.0),
        flow=None, ctd=CTDSensor(),
    )
    bias_cfg = _make_bias()
    real = _RealCurrents(nemo, noise)
    nemo_prior = _NemoPrior(nemo)
    real_tracer = _RealTracer(tracer, tracer_noise)
    perfect = PerfectKnowledge(truth=real)

    seed = SEED_BASE + s_idx * 100 + seed_idx
    t0 = time.time()
    exp = Experiment(
        station=station, sim=sim, sensor=sensor_cfg,
        pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
        controller_knowledge_override=perfect,
    )
    r = run_one_station(exp, seed=seed)
    dt = time.time() - t0
    print(f"  s{s_idx+1} seed={seed}  mean={r.ctrl_mean_m():.0f}m "
          f"({dt:.0f}s)", flush=True)
    return {
        "s_idx": s_idx,
        "seed": seed,
        "dt_sec": sim.dt_sec,
        "dists_m": r.dists_m,
        "at_surface_mask": r.at_surface_mask,
        "depths": r.depths,
    }


def _decompose_run(row: dict) -> dict:
    """Compute phase decomposition for one mission."""
    dists = row["dists_m"]
    surf = row["at_surface_mask"]
    submerged = ~surf
    finite = np.isfinite(dists)
    return {
        "frac_surface": float(surf[finite].mean()),
        "mean_dist_overall": float(np.nanmean(dists)),
        "mean_dist_surface": float(np.nanmean(dists[surf & finite])
                                    ) if (surf & finite).any() else float("nan"),
        "mean_dist_submerged": float(np.nanmean(dists[submerged & finite])
                                      ) if (submerged & finite).any() else float("nan"),
    }


def _reconvergence_trace(rows: list[dict], dt_sec: float,
                           max_h: float = 6.0) -> tuple[np.ndarray, np.ndarray]:
    """Mean dist as a function of time-since-surface-exit, averaged across
    legs across all (station, seed) runs. Returns (t_h_bins, mean_dist_per_bin)."""
    n_bins = int(max_h * 3600.0 / dt_sec)  # one bin per dt
    bin_t_h = np.arange(n_bins) * dt_sec / 3600.0
    bin_sum = np.zeros(n_bins)
    bin_cnt = np.zeros(n_bins)
    for row in rows:
        dists = row["dists_m"]
        surf = row["at_surface_mask"]
        n = surf.size
        # Find surface→submerged transitions (surface_exits): index i where
        # surf[i] = True, surf[i+1] = False.
        for i in range(n - 1):
            if surf[i] and not surf[i + 1]:
                # Submerged leg starts at i+1
                for j in range(n_bins):
                    idx = i + 1 + j
                    if idx >= n:
                        break
                    if surf[idx]:    # next surface event — stop the trace
                        break
                    if np.isfinite(dists[idx]):
                        bin_sum[j] += dists[idx]
                        bin_cnt[j] += 1
    mean = np.where(bin_cnt > 0, bin_sum / np.maximum(bin_cnt, 1), np.nan)
    return bin_t_h, mean


def main():
    print(f"=== perfect_info surface-phase decomp "
          f"({len(STATIONS)} stations × {N_SEEDS} seeds, "
          f"N_PROCS={N_PROCS}) ===", flush=True)
    jobs = [(s, sd) for s in range(len(STATIONS))
                    for sd in range(N_SEEDS)]
    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        results = pool.map(_run_one, jobs)
    print(f"\n{len(results)} runs done; total wall {time.time() - t0:.0f}s",
          flush=True)

    # Per-station aggregation.
    print(f"\n--- per-station decomposition (mean over {N_SEEDS} seeds) ---",
          flush=True)
    print(f"{'station':<10} {'overall':>10} {'surface':>10} {'submerged':>11} "
          f"{'frac_surf':>10} {'Δ(surf−sub)':>12}", flush=True)
    print("-" * 75, flush=True)
    by_station: dict[int, list[dict]] = {s: [] for s in range(len(STATIONS))}
    for row in results:
        by_station[row["s_idx"]].append(_decompose_run(row))
    for s_idx in range(len(STATIONS)):
        decs = by_station[s_idx]
        if not decs:
            continue
        ov = np.mean([d["mean_dist_overall"] for d in decs])
        sf = np.mean([d["mean_dist_surface"] for d in decs])
        sb = np.mean([d["mean_dist_submerged"] for d in decs])
        fs = np.mean([d["frac_surface"] for d in decs])
        bathy = STATIONS[s_idx][2]
        print(f"S{s_idx+1} (b={bathy:>4}m)  {ov:8.0f}m  {sf:8.0f}m  "
              f"{sb:9.0f}m  {fs*100:8.1f}%  {sf-sb:+10.0f}m",
              flush=True)

    # Cross-station aggregate.
    all_decs = [d for s in by_station.values() for d in s]
    print(f"\n--- cross-station aggregate (mean over "
          f"{len(all_decs)} runs) ---", flush=True)
    ov = np.mean([d["mean_dist_overall"] for d in all_decs])
    sf = np.mean([d["mean_dist_surface"] for d in all_decs])
    sb = np.mean([d["mean_dist_submerged"] for d in all_decs])
    fs = np.mean([d["frac_surface"] for d in all_decs])
    print(f"  overall mean_dist        = {ov:.0f}m", flush=True)
    print(f"  mean_dist | at surface   = {sf:.0f}m  ({fs*100:.1f}% of mission)",
          flush=True)
    print(f"  mean_dist | submerged    = {sb:.0f}m  ({(1-fs)*100:.1f}% of mission)",
          flush=True)
    print(f"  surface excess Δ         = {sf-sb:+.0f}m  "
          f"(if surface dwell were instantaneous, time-weighted impact "
          f"≈ {fs*(sf-sb):+.0f}m)", flush=True)

    # Re-convergence trace.
    dt_sec = float(results[0]["dt_sec"])
    t_h, recov = _reconvergence_trace(results, dt_sec, max_h=6.0)
    submerged_steady = sb  # mean of submerged ticks across legs
    print(f"\n--- re-convergence trace "
          f"(mean dist as fn of time-since-surface-exit) ---", flush=True)
    print(f"  t={'0':>5}  {'0.5':>5}  {'1.0':>5}  {'2.0':>5}  "
          f"{'3.0':>5}  {'4.0':>5}  {'5.0':>5}  hours since resurface", flush=True)
    sample_times_h = [0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0]
    sample_idx = [int(t * 3600.0 / dt_sec) for t in sample_times_h]
    valid_idx = [i for i in sample_idx if i < t_h.size]
    parts = "  ".join(f"{recov[i]:5.0f}" if np.isfinite(recov[i])
                       else f"{'  --':>5}"
                       for i in valid_idx)
    print(f"  d={parts}m", flush=True)
    print(f"  asymptotic submerged_steady ≈ {submerged_steady:.0f}m",
          flush=True)

    # Chart.
    print(f"\n--- saving chart ---", flush=True)
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Panel 1: per-station bar chart of (surface, submerged) means.
    ax = axes[0]
    s_labels = [f"S{i+1}" for i in range(len(STATIONS))]
    surf_means = [np.mean([d["mean_dist_surface"] for d in by_station[i]])
                  for i in range(len(STATIONS))]
    sub_means = [np.mean([d["mean_dist_submerged"] for d in by_station[i]])
                 for i in range(len(STATIONS))]
    overall_means = [np.mean([d["mean_dist_overall"] for d in by_station[i]])
                      for i in range(len(STATIONS))]
    x = np.arange(len(STATIONS))
    w = 0.28
    ax.bar(x - w, surf_means, w, label="at surface", color="C1")
    ax.bar(x, sub_means, w, label="submerged", color="C0")
    ax.bar(x + w, overall_means, w, label="overall", color="C2", alpha=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(s_labels)
    ax.set_ylabel("mean_dist (m)")
    ax.set_title("Per-station phase decomposition (perfect_info)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    # Panel 2: re-convergence trace.
    ax = axes[1]
    ax.plot(t_h, recov, color="C0", lw=1.5, label="mean dist | t_since_surface_exit")
    ax.axhline(sb, color="C2", linestyle="--", lw=1.0,
                label=f"submerged-mean asymptote ({sb:.0f}m)")
    ax.set_xlabel("hours since surface exit")
    ax.set_ylabel("mean dist to station (m)")
    ax.set_title("Re-convergence trace (across legs, all runs)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    out = "figures/surface_phase_decomp.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"  saved {out}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
