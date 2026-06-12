"""Diagnostic: cross-validate the MPC's predicted σ_pos at the rollout
horizon against the observed pf_err over the same window.

If the OU-integrated process-noise model is correctly calibrated, the
PF's posterior σ_pos and the MPC's predicted σ_pos at horizon should
both track the actual pf_err observed over a mission. This diagnostic
runs a few short missions and reports:

  - per-config mean predicted σ_pos at horizon
  - per-config mean observed pf_err
  - their ratio (≈1 = calibrated)
  - distributional comparison via Q-Q-style summary (P50 / P95 of each)

Print-only; no figure output. ~60-90 s per config × 3 configs.
"""

from __future__ import annotations

import os
import time

import numpy as np  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# 4 stations from HAND_PICKED_STATIONS (matching the smoke harness).
STATIONS = [
    (49.3533, -123.7411),
    (49.3533, -123.6892),
    (49.3924, -123.7411),
    (49.3924, -123.6374),
]


def _build_world():
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
    return nemo, tracer, noise, tracer_noise, bathy_grid


class _RealCurrents:
    def __init__(self, nemo, noise):
        self.nemo = nemo
        self.noise = noise

    def sample(self, lat, lon, depth_m, t_sec):
        ut, vt = self.nemo.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)
        return ut + un, vt + vn

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


class _RealTracer:
    def __init__(self, tracer, tracer_noise):
        self.tracer = tracer
        self.tracer_noise = tracer_noise

    def sample(self, lat, lon, depth_m, t_sec):
        T_t, S_t = self.tracer.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(T_t) and np.isfinite(S_t)):
            return float("nan"), float("nan")
        T_n, S_n = self.tracer_noise.sample(lat, lon, depth_m, t_sec)
        return T_t + T_n, S_t + S_n


class _NemoPrior:
    def __init__(self, nemo):
        self.nemo = nemo

    def sample(self, lat, lon, depth_m, t_sec):
        return self.nemo.sample(lat, lon, depth_m, t_sec)

    def sample_batched(self, lats, lons, depths, t_sec):
        return self.nemo.sample_batched(lats, lons, depths, t_sec)

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)

    def get_current_at_batched(self, lats, lons, depths, t_sec):
        return self.sample_batched(lats, lons, depths, t_sec)


def _run_one(s_idx, seed_idx, pn_model, world):
    from rbpf_prototype import (  # type: ignore[import-not-found]
        Experiment, FixedIntervalPolicy, LoRaRangeSensor, PFConfig,
        SensorConfig, SimConfig, StationConfig, run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    nemo, tracer, noise, tracer_noise, bathy_grid = world
    s_lat_target, s_lon_target = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(
        lat=s_lat, lon=s_lon, envelope_m=3000.0, available_depths_m=d_set,
    )
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    anchors = [
        (s_lat + dn * 1000.0 / EARTH_R_M,
         s_lon + de * 1000.0 / (EARTH_R_M * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]
    sim_cfg = SimConfig(
        run_hours=72, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model=pn_model,
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)
    sensor_cfg = SensorConfig(
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
        flow=None, ctd=None,
    )
    real = _RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = _NemoPrior(nemo=nemo)
    real_tracer = _RealTracer(tracer=tracer, tracer_noise=tracer_noise)
    seed = 1000 + s_idx * 100 + seed_idx
    exp = Experiment(
        station=station, sim=sim_cfg, sensor=sensor_cfg,
        pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=None,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )
    r = run_one_station(exp, seed=seed)
    return {
        "s_idx": s_idx, "seed_idx": seed_idx, "pn_model": pn_model,
        "pferr_mean": r.pf_err_mean_m(),
        "pferr_p50": float(np.nanpercentile(r.pf_err_m, 50)),
        "pferr_p95": r.pf_err_p95_m(),
        "pfstd_mean": r.pf_std_mean_m(),
        "pfstd_p95": r.pf_std_p95_m(),
        "calib": r.pf_calibration_ratio(),
        "predsig_h": r.predicted_sigma_pos_horizon_mean,
    }


def main() -> None:
    print("=== σ_pos calibration diagnostic ===", flush=True)
    print("Comparing PF posterior σ, MPC-predicted σ at horizon, and "
          "observed pf_err across both process-noise models.", flush=True)
    pn_models_arg = os.environ.get("DIAG_PN_MODELS", "ou_integrated,iid_legacy")
    pn_models = [s.strip() for s in pn_models_arg.split(",") if s.strip()]
    n_seeds = int(os.environ.get("DIAG_N_SEEDS", "2"))

    t0 = time.time()
    world = _build_world()
    print(f"world built in {time.time()-t0:.1f}s", flush=True)

    rows = []
    for pn_model in pn_models:
        print(f"\n--- pn_model={pn_model} ---", flush=True)
        for s_idx in range(len(STATIONS)):
            for seed_idx in range(n_seeds):
                t1 = time.time()
                row = _run_one(s_idx, seed_idx, pn_model, world)
                rows.append(row)
                print(
                    f"  S{s_idx+1} seed={seed_idx}  "
                    f"pferr={row['pferr_mean']:5.0f}/p95={row['pferr_p95']:5.0f}m  "
                    f"pfstd={row['pfstd_mean']:4.0f}m  "
                    f"predσh={row['predsig_h']:4.0f}m  "
                    f"calib={row['calib']:.2f}  "
                    f"({time.time()-t1:.1f}s)",
                    flush=True,
                )

    print(f"\n--- aggregate by pn_model ---", flush=True)
    print(f"{'model':<14}  {'pf_err':>8}  {'pf_std':>8}  {'predσh':>8}  "
          f"{'calib':>6}  {'σh/pferr':>10}", flush=True)
    print("-" * 60, flush=True)
    for pn_model in pn_models:
        sub = [r for r in rows if r["pn_model"] == pn_model]
        pferr = float(np.mean([r["pferr_mean"] for r in sub]))
        pfstd = float(np.mean([r["pfstd_mean"] for r in sub]))
        predsig = float(np.mean([r["predsig_h"] for r in sub]))
        calib = float(np.nanmean([r["calib"] for r in sub]))
        ratio = predsig / max(pferr, 1e-6)
        print(f"{pn_model:<14}  {pferr:7.0f}m  {pfstd:7.0f}m  "
              f"{predsig:7.0f}m  {calib:5.2f}  {ratio:9.2f}",
              flush=True)
    print(
        "\nReadout: under OU calibration, calib ratio (PF posterior σ vs\n"
        "observed pf_err) should approach 1.0; under iid_legacy it was\n"
        "2.4-2.9 at Step 2.2. predσh / pf_err measures whether the MPC's\n"
        "horizon prediction tracks the realised cluster spread.",
        flush=True,
    )


if __name__ == "__main__":
    main()
