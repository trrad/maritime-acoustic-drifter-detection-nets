"""Multi-seed RTS smoother validation.

Single-mission validation gives a single-realisation calib that's too
noisy to trust. This runs grid+ctd × 4 stations × N_SEEDS missions and
reports aggregate (forward, smoothed) σ, error, calib statistics across
the whole matrix — the actual question of "is the σ-accounting honest"
is statistical, not per-mission.

Default: 5 seeds × 4 stations = 20 missions, single batch under 16
workers (~15 min wall after init). Override via DIAG_N_SEEDS /
DIAG_N_PROCS env.
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
TAU_OU_H = float(os.environ.get("DIAG_TAU_OU_H", "36.0"))

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
    def __init__(self, n, no): self.nemo, self.noise = n, no

    def sample(self, lat, lon, d, t):
        ut, vt = self.nemo.sample(lat, lon, d, t)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, d, t)
        return ut + un, vt + vn

    def sample_batched(self, lats, lons, depths, t):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t)
        un, vn = self.noise.sample_batched(lats, lons, depths, t)
        u = np.where(np.isfinite(ut), ut + un, np.nan)
        v = np.where(np.isfinite(vt), vt + vn, np.nan)
        return u, v

    def get_current_at(self, *a):
        return self.sample(*a)

    def get_current_at_batched(self, *a):
        return self.sample_batched(*a)


class _RealTracer:
    def __init__(self, t, tn): self.tracer, self.tn = t, tn

    def sample(self, lat, lon, d, t):
        Tt, St = self.tracer.sample(lat, lon, d, t)
        if not (np.isfinite(Tt) and np.isfinite(St)):
            return float("nan"), float("nan")
        Tn, Sn = self.tn.sample(lat, lon, d, t)
        return Tt + Tn, St + Sn


class _NemoPrior:
    def __init__(self, n): self.nemo = n
    def sample(self, l, lo, d, t): return self.nemo.sample(l, lo, d, t)
    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)
    def get_current_at(self, *a): return self.sample(*a)
    def get_current_at_batched(self, *a): return self.sample_batched(*a)


def _make_bias():
    from rbpf_prototype import BiasConfig  # type: ignore[import-not-found]
    return BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=float(np.sqrt(
            0.04**2 + 0.02**2 + 0.05**2)),
        tau_ou_sec=TAU_OU_H * 3600.0,
    )


def _run_one(args: tuple) -> dict:
    s_idx, seed_idx = args
    from rbpf_prototype import (  # type: ignore[import-not-found]
        CTDSensor, Experiment, FixedIntervalPolicy,
        LoRaRangeSensor, PFConfig, ProcessNoiseConfig, SensorConfig,
        SimConfig, StationConfig, rts_smooth_trajectory, run_one_station,
    )
    from truth_field import (  # type: ignore[import-not-found]
        EARTH_R_M, distance_m,
    )

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
        # mpc_scoring default is posterior_cvar (per recent SimConfig change)
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
    seed = SEED_BASE + s_idx * 100 + seed_idx
    t0 = time.time()
    exp = Experiment(
        station=station, sim=sim, sensor=sensor_cfg, pf_cfg=pf_cfg,
        truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )
    r = run_one_station(exp, seed=seed)
    pn_cfg = ProcessNoiseConfig()
    smoothed = rts_smooth_trajectory(
        pf_mean_lats=r.pf_mean_lats,
        pf_mean_lons=r.pf_mean_lons,
        pf_cov_m=r.pf_cov_m,
        depths=r.depths,
        lora_fix_mask=r.lora_fix_mask,
        dt_sec=sim.dt_sec,
        process_noise_cfg=pn_cfg,
    )

    s_lat_arr, s_lon_arr = smoothed.to_latlon()
    s_err = np.array([
        distance_m(r.lats[i], r.lons[i], s_lat_arr[i], s_lon_arr[i])
        for i in range(r.lats.size)
    ])
    s_sigma = smoothed.sigma_pos_per_axis_m()
    f_sigma = r.pf_std_m
    f_err = r.pf_err_m

    finite = np.isfinite(f_err) & np.isfinite(s_err) \
             & np.isfinite(f_sigma) & np.isfinite(s_sigma)
    sub = ~r.at_surface_mask & finite

    dt = time.time() - t0

    def _stats(arr, mask):
        x = arr[mask]
        return {
            "mean": float(np.nanmean(x)),
            "p95": float(np.nanpercentile(x, 95)),
            "rms": float(np.sqrt(np.nanmean(x ** 2))),
        }

    f_sig_ov = _stats(f_sigma, finite)
    f_err_ov = _stats(f_err, finite)
    s_sig_ov = _stats(s_sigma, finite)
    s_err_ov = _stats(s_err, finite)
    f_sig_sub = _stats(f_sigma, sub)
    f_err_sub = _stats(f_err, sub)
    s_sig_sub = _stats(s_sigma, sub)
    s_err_sub = _stats(s_err, sub)

    f_calib = f_err_ov["rms"] / max(f_sig_ov["rms"], 1.0)
    s_calib = s_err_ov["rms"] / max(s_sig_ov["rms"], 1.0)
    f_calib_sub = f_err_sub["rms"] / max(f_sig_sub["rms"], 1.0)
    s_calib_sub = s_err_sub["rms"] / max(s_sig_sub["rms"], 1.0)

    print(f"  s{s_idx + 1} seed={seed}  "
          f"fwd_σ={f_sig_ov['mean']:.0f}/err={f_err_ov['mean']:.0f}/"
          f"calib={f_calib:.2f}  "
          f"sm_σ={s_sig_ov['mean']:.0f}/err={s_err_ov['mean']:.0f}/"
          f"calib={s_calib:.2f}  ({dt:.0f}s)", flush=True)
    return {
        "s_idx": s_idx,
        "seed": seed,
        "dt_sec": dt,
        "f_sig_ov": f_sig_ov, "f_err_ov": f_err_ov,
        "s_sig_ov": s_sig_ov, "s_err_ov": s_err_ov,
        "f_sig_sub": f_sig_sub, "f_err_sub": f_err_sub,
        "s_sig_sub": s_sig_sub, "s_err_sub": s_err_sub,
        "f_calib": f_calib, "s_calib": s_calib,
        "f_calib_sub": f_calib_sub, "s_calib_sub": s_calib_sub,
    }


def _summarise(rows: list[dict], key_sig: str, key_err: str) -> dict:
    sigs = np.array([r[key_sig]["rms"] for r in rows])
    errs = np.array([r[key_err]["rms"] for r in rows])
    return {
        "sig_mean": float(np.mean([r[key_sig]["mean"] for r in rows])),
        "sig_p95":  float(np.mean([r[key_sig]["p95"] for r in rows])),
        "err_mean": float(np.mean([r[key_err]["mean"] for r in rows])),
        "err_p95":  float(np.mean([r[key_err]["p95"] for r in rows])),
        # Fleet calib = sqrt(mean(err²) / mean(σ²)) across all missions
        "calib_pooled": float(np.sqrt(np.mean(errs ** 2)
                                        / max(np.mean(sigs ** 2), 1.0))),
        # Per-mission calib distribution (mean ± std)
        "calib_per_mission_mean": float(np.mean([
            r[key_err]["rms"] / max(r[key_sig]["rms"], 1.0) for r in rows
        ])),
        "calib_per_mission_std": float(np.std([
            r[key_err]["rms"] / max(r[key_sig]["rms"], 1.0) for r in rows
        ])),
    }


def main():
    print(f"=== multi-seed RTS smoother validation "
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
    print(f"\n--- per-station aggregation (mean over {N_SEEDS} seeds) ---",
          flush=True)
    print(f"{'station':<10} {'fwd σ':>7} {'fwd err':>8} {'fwd calib':>10} "
          f"{'sm σ':>7} {'sm err':>8} {'sm calib':>10}", flush=True)
    print("-" * 75, flush=True)
    by_station: dict[int, list[dict]] = {s: [] for s in range(len(STATIONS))}
    for row in results:
        by_station[row["s_idx"]].append(row)
    for s_idx in range(len(STATIONS)):
        rows_s = by_station[s_idx]
        if not rows_s:
            continue
        f_sum = _summarise(rows_s, "f_sig_ov", "f_err_ov")
        s_sum = _summarise(rows_s, "s_sig_ov", "s_err_ov")
        bathy = STATIONS[s_idx][2]
        print(f"S{s_idx + 1} (b={bathy:>4}m)  "
              f"{f_sum['sig_mean']:6.0f}m  {f_sum['err_mean']:7.0f}m  "
              f"{f_sum['calib_pooled']:8.2f}    "
              f"{s_sum['sig_mean']:6.0f}m  {s_sum['err_mean']:7.0f}m  "
              f"{s_sum['calib_pooled']:8.2f}", flush=True)

    # Cross-station aggregate (the headline number).
    print(f"\n--- cross-station aggregate ({len(results)} missions) ---",
          flush=True)
    f_all_ov = _summarise(results, "f_sig_ov", "f_err_ov")
    s_all_ov = _summarise(results, "s_sig_ov", "s_err_ov")
    f_all_sub = _summarise(results, "f_sig_sub", "f_err_sub")
    s_all_sub = _summarise(results, "s_sig_sub", "s_err_sub")
    print(f"  forward filter (overall):", flush=True)
    print(f"    σ: mean={f_all_ov['sig_mean']:.0f}m  p95={f_all_ov['sig_p95']:.0f}m",
          flush=True)
    print(f"    err: mean={f_all_ov['err_mean']:.0f}m  p95={f_all_ov['err_p95']:.0f}m",
          flush=True)
    print(f"    calib (pooled): {f_all_ov['calib_pooled']:.2f}  "
          f"(per-mission: {f_all_ov['calib_per_mission_mean']:.2f}"
          f" ± {f_all_ov['calib_per_mission_std']:.2f})", flush=True)
    print(f"  RTS smoothed (overall):", flush=True)
    print(f"    σ: mean={s_all_ov['sig_mean']:.0f}m  p95={s_all_ov['sig_p95']:.0f}m",
          flush=True)
    print(f"    err: mean={s_all_ov['err_mean']:.0f}m  p95={s_all_ov['err_p95']:.0f}m",
          flush=True)
    print(f"    calib (pooled): {s_all_ov['calib_pooled']:.2f}  "
          f"(per-mission: {s_all_ov['calib_per_mission_mean']:.2f}"
          f" ± {s_all_ov['calib_per_mission_std']:.2f})", flush=True)
    print(f"  smoothed σ reduction vs forward: "
          f"{100 * (1 - s_all_ov['sig_mean'] / f_all_ov['sig_mean']):.1f}%",
          flush=True)

    print(f"\n--- submerged-only ({len(results)} missions, "
          f"~92% of mission ticks) ---", flush=True)
    print(f"  forward calib: {f_all_sub['calib_pooled']:.2f}  "
          f"smoothed calib: {s_all_sub['calib_pooled']:.2f}", flush=True)
    print(f"  forward σ: mean={f_all_sub['sig_mean']:.0f}m  "
          f"smoothed σ: mean={s_all_sub['sig_mean']:.0f}m", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
