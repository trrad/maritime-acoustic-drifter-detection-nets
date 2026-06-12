"""Compare surfacing strategies on a shared station+seed (and optional
multi-seed average). Runs 4 SurfacingPolicy variants and reports:

  - pferr_mean / p95 (whole mission)
  - pf_err_at_event_time_p95 (sampled submerged ticks — the deployment
    proxy for retroactive triangulation σ_pos)
  - mean_dist (station-keeping)
  - surface_count (power proxy)
  - calib (filter honesty)
  - pred_σh / observed σ ratio (MPC σ rollout vs reality)

Strategies tested:
  A: FixedInterval(6h)            — legacy default
  B: FixedInterval(12h)           — half cadence
  C: UncertaintyGated(300m, 12h)  — surface when σ > 300m, cap 12h
  D: Hybrid([FixedInterval(12h),  — relaxed safety + Poisson events
            EventTriggered(λ=1/4h, max=12h)])

Multiprocessing: one worker per (strategy, seed, config) job; world
build is shared per worker process via the existing `_init_worker`
pattern from `_smoke_ctd_one_station.py`.
"""

from __future__ import annotations

import os
import time
from multiprocessing import Pool, current_process
from typing import Callable

import numpy as np  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]
STATIONS = [(49.3533, -123.7411)]  # S1 only
N_SEEDS = int(os.environ.get("DIAG_N_SEEDS", "3"))
SEED_BASE = 1000
N_PROCS = int(os.environ.get("DIAG_N_PROCS", "12"))

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
    _W["tracer_noise"] = tracer_noise
    _W["noise"] = noise
    _W["bathy_grid"] = bathy_grid
    print(f"[{label}] init done ({time.time() - t0:.1f}s)", flush=True)


class _RealCurrents:
    def __init__(self, nemo, noise):
        self.nemo, self.noise = nemo, noise

    def sample(self, lat, lon, d, t):
        ut, vt = self.nemo.sample(lat, lon, d, t)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, d, t)
        return ut + un, vt + vn

    def get_current_at(self, *a):
        return self.sample(*a)


class _RealTracer:
    def __init__(self, t, tn):
        self.tracer, self.tn = t, tn

    def sample(self, lat, lon, d, t):
        Tt, St = self.tracer.sample(lat, lon, d, t)
        if not (np.isfinite(Tt) and np.isfinite(St)):
            return float("nan"), float("nan")
        Tn, Sn = self.tn.sample(lat, lon, d, t)
        return Tt + Tn, St + Sn


class _NemoPrior:
    def __init__(self, nemo):
        self.nemo = nemo

    def sample(self, l, lo, d, t):
        return self.nemo.sample(l, lo, d, t)

    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)

    def get_current_at(self, *a):
        return self.sample(*a)

    def get_current_at_batched(self, *a):
        return self.sample_batched(*a)


def _make_surfacing(strategy: str, mission_h: float, seed: int):
    from rbpf_prototype import (  # type: ignore[import-not-found]
        FixedIntervalPolicy,
        UncertaintyGatedPolicy,
    )
    from rbpf_prototype.surfacing import (  # type: ignore[import-not-found]
        EventTriggeredPolicy,
        HybridPolicy,
        PostEventSurfacingPolicy,
    )
    from acoustic_events import PoissonEventDetector  # type: ignore[import-not-found]
    if strategy == "fixed_6h":
        return FixedIntervalPolicy(period_h=6.0)
    if strategy == "fixed_12h":
        return FixedIntervalPolicy(period_h=12.0)
    if strategy == "uncertainty":
        # Surface when σ_pos > 300m or 12h cap.
        return UncertaintyGatedPolicy(threshold_m=300.0, max_interval_h=12.0)
    if strategy == "hybrid":
        # Fixed safety + Poisson event trigger. λ = 1/4h means avg 18
        # events per 72h mission. Detector seeded per-run for
        # reproducibility.
        det = PoissonEventDetector(
            lambda_per_h=1.0 / 4.0, seed=seed, mission_h=mission_h,
        )
        return HybridPolicy(policies=(
            FixedIntervalPolicy(period_h=12.0),
            EventTriggeredPolicy(event_detector=det, max_interval_h=12.0),
        ))
    if strategy == "post_event":
        # Reactive: surface ~30 min AFTER a detected event so the RTS
        # smoother can backproject from the LoRa fix to the event tick.
        # Same Poisson process as hybrid for an apples-to-apples
        # comparison of WHEN-after-event matters (immediately for fix
        # vs. after delay for retroactive σ_pos).
        det = PoissonEventDetector(
            lambda_per_h=1.0 / 4.0, seed=seed, mission_h=mission_h,
        )
        return PostEventSurfacingPolicy(
            event_detector=det,
            post_event_delay_min=30.0,
            max_interval_h=12.0,
        )
    raise ValueError(f"unknown strategy: {strategy}")


def _run_one(args: tuple) -> dict:
    from rbpf_prototype import (  # type: ignore[import-not-found]
        BiasConfig, CTDSensor, Experiment, LoRaRangeSensor,
        PFConfig, SensorConfig, SimConfig, StationConfig, run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]
    s_idx, seed_idx, cfg_name, strategy = args
    nemo = _W["nemo"]; tracer = _W["tracer"]; tracer_noise = _W["tracer_noise"]
    noise = _W["noise"]; bathy_grid = _W["bathy_grid"]
    s_lat_target, s_lon_target = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy]); s_lon = float(nemo.lon_axis[gx])
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
    seed = SEED_BASE + s_idx * 100 + seed_idx

    if cfg_name == "no_learn":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=None,
        )
        bias_cfg = None
    elif cfg_name == "grid+ctd":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=CTDSensor(),
        )
        bias_cfg = BiasConfig(
            n_cells=8, cell_size_m=2000.0,
            sigma_bias_init_ms=float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2)),
        )
    else:
        raise ValueError(f"unknown cfg: {cfg_name}")

    real = _RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = _NemoPrior(nemo=nemo)
    real_tracer = _RealTracer(t=tracer, tn=tracer_noise)
    surfacing = _make_surfacing(strategy, sim.run_hours, seed)
    exp = Experiment(
        station=station, sim=sim, sensor=sensor_cfg, pf_cfg=pf_cfg,
        truth=real, prior=nemo_prior, surfacing=surfacing,
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )
    t0 = time.time()
    r = run_one_station(exp, seed=seed)
    dt = time.time() - t0
    row = {
        "strategy": strategy,
        "cfg": cfg_name,
        "seed_idx": seed_idx,
        "pferr": r.pf_err_mean_m(),
        "pferr_p95": r.pf_err_p95_m(),
        "pferr_event_p95": r.pf_err_at_event_time_p95(n_events=50, seed=seed),
        "pferr_event_mean": r.pf_err_at_event_time_mean(n_events=50, seed=seed),
        "mean_dist": r.ctrl_mean_m(),
        "surf": r.surface_events,
        "calib": r.pf_calibration_ratio(),
        "pfstd": r.pf_std_mean_m(),
        "pred_sigh": r.predicted_sigma_pos_horizon_mean,
        "dt": dt,
    }
    label = current_process().name
    print(f"[{label}] {strategy:<12} {cfg_name:<10} seed={seed} "
          f"pferr={row['pferr']:5.0f}/p95={row['pferr_p95']:5.0f}m  "
          f"event_p95={row['pferr_event_p95']:5.0f}m  "
          f"mean_d={row['mean_dist']:5.0f}m  "
          f"surf={row['surf']:2d}  calib={row['calib']:.2f}  "
          f"pred_σh/σ={row['pred_sigh']/max(row['pfstd'],1):.2f}  "
          f"({dt:.1f}s)", flush=True)
    return row


def main() -> None:
    strategies = ["fixed_6h", "fixed_12h", "uncertainty", "hybrid", "post_event"]
    configs = ["no_learn", "grid+ctd"]
    print(f"=== surfacing-strategy comparison ===", flush=True)
    print(f"  {len(strategies)} strategies × {len(STATIONS)} stations × "
          f"{N_SEEDS} seeds × {len(configs)} configs = "
          f"{len(strategies)*len(STATIONS)*N_SEEDS*len(configs)} runs",
          flush=True)
    jobs = [
        (s, sd, c, strat)
        for strat in strategies
        for s in range(len(STATIONS))
        for sd in range(N_SEEDS)
        for c in configs
    ]
    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        results = pool.map(_run_one, jobs)
    print(f"\nall {len(results)} runs done in "
          f"{time.time() - t0:.0f}s", flush=True)

    # Bucket by (strategy, cfg) and aggregate.
    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        buckets.setdefault((r["strategy"], r["cfg"]), []).append(r)

    print(f"\n--- aggregate (mean across seeds) ---", flush=True)
    print(f"{'strategy':<13} {'cfg':<10} {'pferr':>6} {'p95':>5} {'evp95':>6} "
          f"{'evmean':>7} {'mean_d':>7} {'surf':>5} {'calib':>5} "
          f"{'σh/σ':>5}", flush=True)
    print("-" * 88, flush=True)
    for strat in strategies:
        for cfg_name in configs:
            rows = buckets.get((strat, cfg_name), [])
            if not rows:
                continue
            agg: dict[str, float] = {}
            for k in ["pferr", "pferr_p95", "pferr_event_p95",
                       "pferr_event_mean", "mean_dist", "surf",
                       "calib", "pfstd", "pred_sigh"]:
                agg[k] = float(np.nanmean([r[k] for r in rows]))
            ratio = agg["pred_sigh"] / max(agg["pfstd"], 1.0)
            print(f"{strat:<13} {cfg_name:<10} "
                  f"{agg['pferr']:5.0f} {agg['pferr_p95']:5.0f} "
                  f"{agg['pferr_event_p95']:5.0f} {agg['pferr_event_mean']:6.0f} "
                  f"{agg['mean_dist']:6.0f} {agg['surf']:4.1f} "
                  f"{agg['calib']:4.2f} {ratio:4.2f}",
                  flush=True)


if __name__ == "__main__":
    main()
