"""Multi-station, multi-seed CTD smoke run (Step 1 validation, 2026-04-25).

Per the drifter-controls reviewer's #5 critique on the single-seed smoke:
"`no_learn` 1780 m vs `grid` 2196 m on a single seed is well inside SoG
mesoscale variability." Before declaring observer regression or
controller bottleneck, average over multiple seeds and stations.

Configuration:
  - 4 stations (first 4 from HAND_PICKED_STATIONS in 22_rbpf_v2_bias_learning.py)
  - 5 seeds per station
  - 4 configs: no_learn, grid (Step 1 dense Matérn), grid+ctd,
    perfect_info (controller-knowledge ceiling: same observer as
    grid+ctd, controller's keeper.knowledge replaced with truth currents)
  - Single noise realisation amortised across all 60 runs (controls
    reviewer's "tidal phase / wind regime" axis is out of scope; this
    is the reduced validation step)

Parallel structure: `multiprocessing.Pool` with N_PROCS workers. Each
worker's initializer builds noise/nemo/tracer ONCE per process, then the
worker handles N jobs (≈ 60 / N_PROCS). The padded-cube noise build is
~3–4 min and dominates per-worker setup; running 8–12 workers in
parallel cuts total wall clock from ~50 min serial to ~10 min.

Decision rules per the Phase 2.1+ plan:
  - grid ≥ no_learn AND grid+ctd ≥ grid (mean-dist + %<500m): observer
    is sound, proceed to canonical sweep + Step 1.5.
  - grid < no_learn after multi-seed averaging: controller is the
    bottleneck (controls reviewer #1 was right); jump to Step 3.
  - grid+ctd worse than grid: Step 1's analytical observation hasn't
    fully decoupled CTD's contribution from the bias-Kalman; investigate.
"""

from __future__ import annotations

import os
import time
from multiprocessing import Pool, current_process

import numpy as np  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# First 4 of HAND_PICKED_STATIONS from 22_rbpf_v2_bias_learning.py.
STATIONS = [
    (49.3533, -123.7411, 289),
    (49.3533, -123.6892, 188),
    (49.3924, -123.7411, 182),
    (49.3924, -123.6374,  92),
]
SEED_BASE = 1000
N_SEEDS = int(os.environ.get("SMOKE_N_SEEDS", "5"))
N_PROCS = int(os.environ.get("SMOKE_N_PROCS", "12"))

# Per-worker globals, populated by `_init_worker`.
_W: dict = {}


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

    def sample_batched(self, lats, lons, depths, t_sec):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t_sec)
        un, vn = self.noise.sample_batched(lats, lons, depths, t_sec)
        # Out-of-domain in nemo → NaN; preserve so the controller's
        # rollout sees the same NaN-handling as scalar sample().
        u = np.where(np.isfinite(ut), ut + un, np.nan)
        v = np.where(np.isfinite(vt), vt + vn, np.nan)
        return u, v

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)

    def get_current_at_batched(self, lats, lons, depths, t_sec):
        return self.sample_batched(lats, lons, depths, t_sec)


class _RealTracer:
    """Compose clean SalishSeaCast (T, S) with the layered tracer-noise
    bias. Hands a Soontiens-magnitude biased truth to the CTD sensor so
    its σ_S=0.02 likelihood isn't matched against a clean prior."""

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


def _make_bias():
    """Dense-Matérn bias config. σ_obs is now computed analytically
    per-leg from the unlearnable layered-noise components and the
    particle's dwell-weighted depth profile (no longer a fixed floor)."""
    from rbpf_prototype import BiasConfig  # type: ignore[import-not-found]
    bias_init = float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2))
    return BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=bias_init,
    )


def _init_worker():
    """Per-worker setup: build noise/nemo/tracer ONCE per process.

    Each worker process re-runs this initializer. Result is cached in
    the module-level `_W` dict and reused for every job assigned to
    this worker. With 8–12 workers each handling ~5–8 jobs, the
    expensive padded-cube noise build is amortised.
    """
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
    print(f"[{label}] init done ({time.time() - t0:.1f}s, "
          f"vel surface_rms={noise.surface_rms_ms()*100:.2f} cm/s; "
          f"S mean={tracer_noise.mean_S_coh_psu:+.2f} σ={tracer_noise.surface_rms_S_psu():.2f} g/kg; "
          f"T mean={tracer_noise.mean_T_coh_c:+.2f} σ={tracer_noise.rms_T_c():.2f} °C)",
          flush=True)


def _run_one(args: tuple) -> dict:
    """Worker function: run one (station, seed, config) combo.

    Returns a dict row with metrics + identifying keys.
    """
    from rbpf_prototype import (  # type: ignore[import-not-found]
        CTDSensor, Experiment, FixedIntervalPolicy, LoRaRangeSensor,
        PFConfig, SensorConfig, SimConfig, StationConfig,
        run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    s_idx, seed_idx, cfg_name = args
    nemo = _W["nemo"]
    tracer = _W["tracer"]
    tracer_noise = _W["tracer_noise"]
    noise = _W["noise"]
    bathy_grid = _W["bathy_grid"]

    s_lat_target, s_lon_target, _ = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(
        lat=s_lat, lon=s_lon, envelope_m=3000.0,
        available_depths_m=d_set,
    )
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    anchors = [
        (s_lat + dn * 1000.0 / EARTH_R_M,
         s_lon + de * 1000.0 / (EARTH_R_M * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]
    # Honor the SMOKE_PROCESS_NOISE env var so we can run side-by-side
    # OU-vs-iid ablation arms without hand-editing the harness.
    pn_model = os.environ.get("SMOKE_PROCESS_NOISE", "ou_integrated")
    if pn_model not in ("ou_integrated", "iid_legacy"):
        raise ValueError(
            f"SMOKE_PROCESS_NOISE must be 'ou_integrated' or "
            f"'iid_legacy', got {pn_model!r}"
        )
    # MPC scoring per arm — deliberate config matrix, NOT a fallback.
    # `no_learn` has no bias posterior so it CANNOT use posterior_cvar
    # (run_one_station would raise per integrity charter). The other
    # arms default to posterior_cvar (the most-advanced controller);
    # ablation override via SMOKE_MPC_SCORING_BIAS_ARMS.
    bias_arm_scoring = os.environ.get(
        "SMOKE_MPC_SCORING_BIAS_ARMS", "posterior_cvar",
    )
    if bias_arm_scoring not in ("ensemble_mean", "posterior_cvar"):
        raise ValueError(
            f"SMOKE_MPC_SCORING_BIAS_ARMS must be 'ensemble_mean' or "
            f"'posterior_cvar', got {bias_arm_scoring!r}"
        )
    if cfg_name == "no_learn":
        scoring = "ensemble_mean"   # architectural — no posterior to draw
    else:
        scoring = bias_arm_scoring
    sim_cfg = SimConfig(
        run_hours=72, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model=pn_model,  # type: ignore[arg-type]
        mpc_scoring=scoring,  # type: ignore[arg-type]
    )
    # NOTE on process noise: the active model is now per-component
    # OU-integrated (SimConfig default `process_noise_model="ou_integrated"`),
    # mirroring `LayeredNoiseField`. The legacy `process_noise_ms=0.08`
    # value below is ignored under OU mode and only consumed when an
    # ablation arm sets `process_noise_model="iid_legacy"`.
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)

    if cfg_name == "no_learn":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=None,
        )
        bias_cfg = None
    elif cfg_name == "grid":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=None,
        )
        bias_cfg = _make_bias()
    elif cfg_name == "grid+ctd":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=CTDSensor(),
        )
        bias_cfg = _make_bias()
    elif cfg_name == "perfect_info":
        # Same observer as grid+ctd; only the controller's keeper.knowledge
        # is replaced with truth currents (set below via
        # controller_knowledge_override). Quantifies the controller-on-
        # bad-belief portion of the grid+ctd mean-dist gap.
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
            flow=None, ctd=CTDSensor(),
        )
        bias_cfg = _make_bias()
    else:
        raise ValueError(f"unknown cfg_name {cfg_name}")

    real = _RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = _NemoPrior(nemo=nemo)
    real_tracer = _RealTracer(tracer=tracer, tracer_noise=tracer_noise)

    if cfg_name == "perfect_info":
        from ballast_controller import PerfectKnowledge  # type: ignore[import-not-found]
        controller_override: object | None = PerfectKnowledge(truth=real)
    else:
        controller_override = None

    seed = SEED_BASE + s_idx * 100 + seed_idx
    t0 = time.time()
    exp = Experiment(
        station=station, sim=sim_cfg, sensor=sensor_cfg,
        pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
        controller_knowledge_override=controller_override,
    )
    r = run_one_station(exp, seed=seed)
    dt = time.time() - t0
    row = {
        "s_idx": s_idx,
        "seed_idx": seed_idx,
        "seed": seed,
        "cfg": cfg_name,
        "station_lat": s_lat,
        "station_lon": s_lon,
        # Coverage proxy (station-keeping)
        "mean": r.ctrl_mean_m(),
        "max": r.ctrl_max_m(),
        "pct500": r.envelope_frac(500.0) * 100,
        # Localization quality (primary metric for TDOA mission)
        "pferr": r.pf_err_mean_m(),
        "pferr_max": r.pf_err_max_m(),
        "pferr_p95": r.pf_err_p95_m(),
        # Time-fraction over operational σ_pos thresholds. The
        # right threshold depends on assumed fleet density at event:
        # 200m budget → N=8-12 typical-density (250m triangulation
        # RMSE target); 100m budget → sparse N=3 fallback. See
        # 23_acoustic_detection.py figures/26_acoustic_detection.png
        # for the σ_pos × N → triangulation-RMSE table.
        "pferr_pct100": r.pf_err_frac_over(100.0) * 100,
        "pferr_pct200": r.pf_err_frac_over(200.0) * 100,
        "pferr_pct500": r.pf_err_frac_over(500.0) * 100,
        "pfstd": r.pf_std_mean_m(),
        "pfstd_p95": r.pf_std_p95_m(),
        "calib": r.pf_calibration_ratio(),
        # Calibration cross-validation: MPC's predicted σ_pos at the
        # rollout horizon (averaged over decisions). Should track the
        # observed pf_err if the OU process-noise model matches truth.
        "pred_sigpos_h": r.predicted_sigma_pos_horizon_mean,
        # Observer state
        "surf": r.surface_events,
        "bias": r.bias_updates,
        "bmax": r.bias_max_learned_mag_ms * 100,
        "ctd": r.ctd_updates,
        "btoff": r.bias_T_offset_final_c,
        "bsoff": r.bias_S_offset_final_psu,
        "dt": dt,
    }
    label = current_process().name
    print(f"[{label}] S{s_idx+1} seed={seed} {cfg_name:<10}  "
          f"mean={row['mean']:5.0f}m %<500={row['pct500']:4.1f}%  "
          f"PFerr={row['pferr']:4.0f}/p95={row['pferr_p95']:4.0f}/max={row['pferr_max']:4.0f}m  "
          f"%>100={row['pferr_pct100']:4.1f}% %>200={row['pferr_pct200']:4.1f}% %>500={row['pferr_pct500']:4.1f}%  "
          f"σ={row['pfstd']:3.0f}/p95={row['pfstd_p95']:3.0f}m  "
          f"calib={row['calib']:.2f} predσh={row['pred_sigpos_h']:4.0f}  "
          f"ctd={row['ctd']:4d} bTS=({row['btoff']:+.2f}°C,{row['bsoff']:+.2f})  "
          f"({dt:5.1f}s)",
          flush=True)
    return row


def main() -> None:
    pn_model = os.environ.get("SMOKE_PROCESS_NOISE", "ou_integrated")
    bias_arm_scoring = os.environ.get(
        "SMOKE_MPC_SCORING_BIAS_ARMS", "posterior_cvar",
    )
    print(f"=== multi-seed multi-station CTD smoke run "
          f"(N_PROCS={N_PROCS}, process_noise={pn_model}, "
          f"mpc_scoring=ensemble_mean for no_learn / "
          f"{bias_arm_scoring} for grid|grid+ctd|perfect_info) ===",
          flush=True)
    print(f"  stations: {len(STATIONS)}, seeds: {N_SEEDS}, "
          f"configs: 4 → {len(STATIONS)*N_SEEDS*4} total runs",
          flush=True)
    jobs = [(s, sd, c)
            for s in range(len(STATIONS))
            for sd in range(N_SEEDS)
            for c in ("no_learn", "grid", "grid+ctd", "perfect_info")]
    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        results = pool.map(_run_one, jobs)
    print(f"\nall {len(results)} runs done; total wall-clock "
          f"{time.time() - t0:.0f}s", flush=True)

    # --- Bucketise by config ---
    rows_by_cfg: dict[str, list[dict]] = {
        "no_learn": [], "grid": [], "grid+ctd": [], "perfect_info": [],
    }
    for row in results:
        rows_by_cfg[row["cfg"]].append(row)

    # --- Per-config aggregate statistics across all (station, seed) pairs ---
    n_per_cfg = len(STATIONS) * N_SEEDS
    print(f"\n--- per-config aggregates over {len(STATIONS)} stations × "
          f"{N_SEEDS} seeds = {n_per_cfg} runs ---", flush=True)
    # Localization metrics (PRIMARY for the TDOA mission) — pf_err is
    # actual position error (we know truth in sim); pf_std is what the
    # drifter exfiltrates as its self-reported uncertainty for fleet
    # consumers. Calibration ratio = √mean(err²)/√mean(std²); ≈1 means
    # filter's reported σ matches reality.
    print(f"{'config':<10}  "
          f"{'pf_err_mean':>13}  {'p95':>6}  {'max':>6}  "
          f"{'%>100m':>7}  {'%>200m':>7}  {'%>500m':>7}  "
          f"{'pf_std':>6}  {'calib':>6}  "
          f"{'mean_dist':>10}  {'%<500':>6}",
          flush=True)
    print("-" * 115, flush=True)

    summaries: dict[str, dict] = {}
    for cfg_name in ["no_learn", "grid", "grid+ctd", "perfect_info"]:
        rows = rows_by_cfg[cfg_name]
        if not rows:
            continue
        means = np.array([r["mean"] for r in rows])
        pcts = np.array([r["pct500"] for r in rows])
        pfes = np.array([r["pferr"] for r in rows])
        pferr_p95s = np.array([r["pferr_p95"] for r in rows])
        pferr_maxs = np.array([r["pferr_max"] for r in rows])
        pferr_pct100s = np.array([r["pferr_pct100"] for r in rows])
        pferr_pct200s = np.array([r["pferr_pct200"] for r in rows])
        pferr_pct500s = np.array([r["pferr_pct500"] for r in rows])
        pfstds = np.array([r["pfstd"] for r in rows])
        calibs = np.array([r["calib"] for r in rows])
        bmaxes = np.array([r["bmax"] for r in rows])
        s = {
            "mean_dist_mean": float(means.mean()),
            "mean_dist_std":  float(means.std()),
            "pct500_mean":   float(pcts.mean()),
            "pct500_std":    float(pcts.std()),
            "pferr_mean":    float(pfes.mean()),
            "pferr_std":     float(pfes.std()),
            "pferr_p95_mean": float(pferr_p95s.mean()),
            "pferr_max_mean": float(pferr_maxs.mean()),
            "pferr_pct100_mean": float(pferr_pct100s.mean()),
            "pferr_pct200_mean": float(pferr_pct200s.mean()),
            "pferr_pct500_mean": float(pferr_pct500s.mean()),
            "pfstd_mean":    float(pfstds.mean()),
            "calib_mean":    float(np.nanmean(calibs)),
            "bmax_mean":     float(bmaxes.mean()),
            "bmax_std":      float(bmaxes.std()),
        }
        summaries[cfg_name] = s
        print(f"{cfg_name:<10}  "
              f"{s['pferr_mean']:6.0f}±{s['pferr_std']:3.0f}  "
              f"{s['pferr_p95_mean']:5.0f}  {s['pferr_max_mean']:5.0f}  "
              f"{s['pferr_pct100_mean']:6.1f}%  {s['pferr_pct200_mean']:6.1f}%  "
              f"{s['pferr_pct500_mean']:6.1f}%  "
              f"{s['pfstd_mean']:5.0f}  {s['calib_mean']:5.2f}  "
              f"{s['mean_dist_mean']:6.0f}±{s['mean_dist_std']:3.0f}  "
              f"{s['pct500_mean']:5.1f}",
              flush=True)

    # --- Decision-rule comparisons ---
    # The mission's primary metric is pf_err (drives TDOA accuracy at
    # acoustic-event time); mean_dist is the coverage proxy. Lead with
    # pf_err deltas; mean_dist as supplementary.
    print(f"\n--- decision-rule comparisons (PRIMARY: pf_err) ---", flush=True)

    def _delta(summaries, a, b, key, std_key):
        d = summaries[a][key] - summaries[b][key]
        pooled_sd = float(np.sqrt(
            (summaries[a][std_key]**2 + summaries[b][std_key]**2) / 2.0))
        return d, pooled_sd

    if "no_learn" in summaries and "grid" in summaries:
        d, sd = _delta(summaries, "grid", "no_learn", "pferr_mean", "pferr_std")
        verdict = "grid BETTER" if d < 0 else "grid WORSE"
        print(f"  pf_err grid vs no_learn:   Δ = {d:+.0f}m  "
              f"({verdict}; pooled SD={sd:.0f}, |Δ|/SD={abs(d)/max(sd,1):.2f})  "
              f"({summaries['no_learn']['pferr_mean']:.0f} → "
              f"{summaries['grid']['pferr_mean']:.0f})", flush=True)
        d_p95 = (summaries["grid"]["pferr_p95_mean"]
                 - summaries["no_learn"]["pferr_p95_mean"])
        print(f"  pf_err P95 grid vs no_learn: Δ = {d_p95:+.0f}m  "
              f"({summaries['no_learn']['pferr_p95_mean']:.0f} → "
              f"{summaries['grid']['pferr_p95_mean']:.0f})", flush=True)
        d_pct100 = (summaries["grid"]["pferr_pct100_mean"]
                    - summaries["no_learn"]["pferr_pct100_mean"])
        print(f"  %time pf_err>100m grid vs no_learn: Δ = {d_pct100:+.1f}pp  "
              f"({summaries['no_learn']['pferr_pct100_mean']:.1f}% → "
              f"{summaries['grid']['pferr_pct100_mean']:.1f}%)", flush=True)
    if "grid" in summaries and "grid+ctd" in summaries:
        d, sd = _delta(summaries, "grid+ctd", "grid", "pferr_mean", "pferr_std")
        verdict = "ctd BETTER" if d < 0 else "ctd WORSE"
        print(f"  pf_err grid+ctd vs grid:   Δ = {d:+.0f}m  "
              f"({verdict}; pooled SD={sd:.0f}, |Δ|/SD={abs(d)/max(sd,1):.2f})  "
              f"({summaries['grid']['pferr_mean']:.0f} → "
              f"{summaries['grid+ctd']['pferr_mean']:.0f})", flush=True)
        d_p95 = (summaries["grid+ctd"]["pferr_p95_mean"]
                 - summaries["grid"]["pferr_p95_mean"])
        print(f"  pf_err P95 grid+ctd vs grid: Δ = {d_p95:+.0f}m  "
              f"({summaries['grid']['pferr_p95_mean']:.0f} → "
              f"{summaries['grid+ctd']['pferr_p95_mean']:.0f})", flush=True)
        d_pct100 = (summaries["grid+ctd"]["pferr_pct100_mean"]
                    - summaries["grid"]["pferr_pct100_mean"])
        print(f"  %time pf_err>100m grid+ctd vs grid: Δ = {d_pct100:+.1f}pp  "
              f"({summaries['grid']['pferr_pct100_mean']:.1f}% → "
              f"{summaries['grid+ctd']['pferr_pct100_mean']:.1f}%)", flush=True)

    print(f"\n--- secondary: station-keeping coverage proxy ---", flush=True)
    if "no_learn" in summaries and "grid" in summaries:
        d, sd = _delta(summaries, "grid", "no_learn", "mean_dist_mean", "mean_dist_std")
        print(f"  mean_dist grid vs no_learn: Δ = {d:+.0f}m  "
              f"(pooled SD={sd:.0f}, |Δ|/SD={abs(d)/max(sd,1):.2f})", flush=True)
    if "grid" in summaries and "grid+ctd" in summaries:
        d, sd = _delta(summaries, "grid+ctd", "grid", "mean_dist_mean", "mean_dist_std")
        print(f"  mean_dist grid+ctd vs grid: Δ = {d:+.0f}m  "
              f"(pooled SD={sd:.0f}, |Δ|/SD={abs(d)/max(sd,1):.2f})", flush=True)

    # Calibration check — drifter's self-reported σ vs actual error.
    # Honest filter: ratio ≈ 1. Over-confident: ratio > 1 (worse for fleet
    # consumers who weight by reported σ).
    print(f"\n--- calibration check (pf_err RMS / pf_std RMS, ≈1 if calibrated) ---",
          flush=True)
    for cfg_name in ["no_learn", "grid", "grid+ctd", "perfect_info"]:
        if cfg_name in summaries:
            print(f"  {cfg_name:<10}  ratio = {summaries[cfg_name]['calib_mean']:.2f}",
                  flush=True)

    # Predicted-σ-at-horizon vs observed pf_err — Step 1 calibration
    # cross-validation. The MPC's predicted σ_pos at the rollout horizon
    # should track the actual cluster spread; large deviations indicate
    # the OU process-noise model isn't matching truth in expectation.
    print(f"\n--- predicted σ_pos at horizon vs observed pf_err ---",
          flush=True)
    print(f"{'config':<10}  {'pred σ@h':>9}  {'pf_err':>7}  {'pred/pferr':>11}",
          flush=True)
    for cfg_name in ["no_learn", "grid", "grid+ctd", "perfect_info"]:
        rows = rows_by_cfg.get(cfg_name, [])
        if not rows:
            continue
        pred = float(np.nanmean([r["pred_sigpos_h"] for r in rows]))
        pf = float(np.nanmean([r["pferr"] for r in rows]))
        ratio = pred / max(pf, 1e-6)
        print(f"  {cfg_name:<10}  {pred:7.0f}m  {pf:6.0f}m  {ratio:9.2f}",
              flush=True)

    # --- Closure ratio: how much of the controller-knowledge gap each
    # observer arm closes, on station-keeping mean_dist.
    #   closure(arm) = (no_learn - arm) / (no_learn - perfect_info)
    # 1.0 = arm is as good as perfect knowledge. 0.0 = no improvement
    # over no_learn. <0 = arm regresses below no_learn.
    if (summaries.get("no_learn") and summaries.get("perfect_info")):
        denom = (summaries["no_learn"]["mean_dist_mean"]
                 - summaries["perfect_info"]["mean_dist_mean"])
        print(f"\n--- mean_dist closure vs perfect-info controller "
              f"({summaries['no_learn']['mean_dist_mean']:.0f}m no_learn → "
              f"{summaries['perfect_info']['mean_dist_mean']:.0f}m perfect_info "
              f"= {denom:.0f}m gap) ---", flush=True)
        for cfg_name in ["grid", "grid+ctd"]:
            if cfg_name not in summaries:
                continue
            num = (summaries["no_learn"]["mean_dist_mean"]
                   - summaries[cfg_name]["mean_dist_mean"])
            ratio = num / max(abs(denom), 1e-6) * (1.0 if denom != 0 else 0.0)
            print(f"  {cfg_name:<10}  closes {ratio*100:+5.1f}% of gap "
                  f"({summaries[cfg_name]['mean_dist_mean']:.0f}m)",
                  flush=True)

    # --- Plan decision summary (TDOA-mission framing: pf_err is primary) ---
    print(f"\n--- plan decision-tree outcome ---", flush=True)
    if not (summaries.get("grid") and summaries.get("no_learn")
            and summaries.get("grid+ctd")):
        print("  (insufficient data)", flush=True)
        return
    grid_better_pferr = (summaries["grid"]["pferr_mean"]
                         <= summaries["no_learn"]["pferr_mean"])
    ctd_better_pferr = (summaries["grid+ctd"]["pferr_mean"]
                        <= summaries["grid"]["pferr_mean"])
    if grid_better_pferr and ctd_better_pferr:
        verdict = ("PASS — observer monotone in localization quality "
                   "(no_learn → grid → grid+ctd in pf_err). Proceed to "
                   "controller-framework changes (posterior-aware MPC + "
                   "surfacing-as-action).")
    elif not grid_better_pferr:
        verdict = ("REGRESSION — bias-state observer doesn't improve "
                   "pf_err over no_learn. Investigate σ_obs decomposition "
                   "or x_start anchor leak before further observer work.")
    else:
        verdict = ("CTD HURTS GRID on pf_err — bias-aware likelihood is "
                   "still misleading the PF, or the (T,S) bias state has "
                   "absorbed too much position error. Investigate the S "
                   "over-correction (-1.0 vs truth -0.4) we observed.")
    print(f"  {verdict}", flush=True)

    print(f"\n=== smoke-run complete ===", flush=True)


if __name__ == "__main__":
    main()
