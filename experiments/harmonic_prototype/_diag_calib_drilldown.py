"""Drill-down on the calibration over-confidence (calib > 1) seen in
the 5-strategy comparison, especially for grid+ctd uncertainty (calib≈3).

Top suspects:
  (A) CTD over-correction: bias_S_offset estimate goes to -1.05 PSU vs
      truth -0.4 PSU. Spurious bias estimate pulls cluster to a tight-
      but-wrong location → pferr stays high while σ shrinks.
  (B) PF resampling collapse: when ESS drops below 0.5N, systematic
      resampling picks particles by weight; if weights concentrate, the
      surviving cluster is tighter than the actual posterior should be.
  (C) Per-particle independent OU vs spatially-correlated truth noise:
      cluster spread reflects independent draws, but truth's actual
      realisation comes from correlated field. Cluster-mean drift relative
      to truth is dominated by correlated noise that the PF model misses.

Approach: run ONE 72h mission on (S1, seed=1000, grid+ctd, fixed_6h),
record per-tick (pferr, σ, ESS, bias_T_offset, bias_S_offset, depth)
via the run_one_station tick_recorder hook. Compute calib in 6h windows
and correlate with each suspect's signal.

Print-only summary, ~5 min wall clock after init.
"""

from __future__ import annotations

import sys
import time

import numpy as np  # type: ignore[import-not-found]


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

    bbox = bbox_from_latlon(49.15, 49.45, -123.95, -123.50)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=True)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    nemo = build_truth_field(ds, lats_grid, lons_grid,
                              [0.5, 5.0, 10.0, 20.0, 50.0])
    tracer = build_tracer_field(ds, lats_grid, lons_grid,
                                  [0.5, 5.0, 10.0, 20.0, 50.0])
    noise = build_layered_noise_field(ds, lats_grid, lons_grid, seed=42)
    tracer_noise = build_layered_tracer_noise_field(
        ds, lats_grid, lons_grid, seed=42,
    )
    return nemo, tracer, noise, tracer_noise, bathy_grid


class _RealCurrents:
    def __init__(self, n, no): self.nemo, self.noise = n, no

    def sample(self, lat, lon, d, t):
        ut, vt = self.nemo.sample(lat, lon, d, t)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, d, t)
        return ut + un, vt + vn

    def get_current_at(self, *a):
        return self.sample(*a)


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

    def sample(self, l, lo, d, t):
        return self.nemo.sample(l, lo, d, t)

    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)

    def get_current_at(self, *a):
        return self.sample(*a)

    def get_current_at_batched(self, *a):
        return self.sample_batched(*a)


def main() -> None:
    from rbpf_prototype import (  # type: ignore[import-not-found]
        BiasConfig, CTDSensor, Experiment, FixedIntervalPolicy,
        LoRaRangeSensor, PFConfig, SensorConfig, SimConfig,
        StationConfig, run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    print("=== calibration drill-down (S1 grid+ctd seed=1000 fixed_6h) ===",
          flush=True)
    t0 = time.time()
    nemo, tracer, noise, tracer_noise, bathy_grid = _build_world()
    print(f"world built in {time.time() - t0:.1f}s", flush=True)

    s_lat_target, s_lon_target = 49.3533, -123.7411
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in [0.5, 5.0, 10.0, 20.0, 50.0] if d <= max_d]
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
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
        flow=None, ctd=CTDSensor(),
    )
    bias_cfg = BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2)),
    )

    real = _RealCurrents(nemo, noise)
    prior = _NemoPrior(nemo)
    real_tracer = _RealTracer(tracer, tracer_noise)
    exp = Experiment(
        station=station, sim=sim, sensor=sensor_cfg, pf_cfg=pf_cfg,
        truth=real, prior=prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )

    # Per-tick recorder.
    recs: list[tuple] = []

    def recorder(t_sec, state, pf, bias):
        ess = pf.ess()
        if bias is not None:
            w = pf.weights
            bT = float(np.sum(bias.bias_T_offset * w))
            bS = float(np.sum(bias.bias_S_offset * w))
            PT = float(np.sum(bias.P_T_offset * w))
            PS = float(np.sum(bias.P_S_offset * w))
        else:
            bT = bS = PT = PS = float("nan")
        recs.append((t_sec, state.depth_m, ess, bT, bS, PT, PS))

    t1 = time.time()
    r = run_one_station(exp, seed=1000, tick_recorder=recorder)
    print(f"run done {time.time() - t1:.1f}s", flush=True)

    # Convert recs to arrays.
    t_arr = np.array([row[0] for row in recs])
    depth_arr = np.array([row[1] for row in recs])
    ess_arr = np.array([row[2] for row in recs])
    bT_arr = np.array([row[3] for row in recs])
    bS_arr = np.array([row[4] for row in recs])

    # The pf_err / pf_std arrays in r are length n_steps+1; recorder
    # fired on the LAST line of the for-i loop (after pf_err_m[i+1] is
    # set), so recs[i] corresponds to pf_err_m[i+1]. Align indices.
    assert len(recs) == r.pf_err_m.size - 1, (
        f"recorder count {len(recs)} != n_steps {r.pf_err_m.size - 1}"
    )
    pferr = r.pf_err_m[1:]
    pfstd = r.pf_std_m[1:]
    surf_mask = r.at_surface_mask[1:]

    print(f"\nmission summary:", flush=True)
    print(f"  pferr mean={np.nanmean(pferr):.0f}  p95={np.nanpercentile(pferr, 95):.0f}",
          flush=True)
    print(f"  pf_std mean={np.nanmean(pfstd):.0f}  calib_mission={r.pf_calibration_ratio():.2f}",
          flush=True)
    print(f"  surface_events={r.surface_events}  ctd_updates={r.ctd_updates}",
          flush=True)
    print(f"  bias_T_offset final={r.bias_T_offset_final_c:+.3f}°C  (truth -0.40 ... +0.35 typical)",
          flush=True)
    print(f"  bias_S_offset final={r.bias_S_offset_final_psu:+.3f} PSU  (truth -0.40)",
          flush=True)

    # 6h windows.
    n_steps = pferr.size
    dt_sec = 600.0
    chunk_size = int(6 * 3600 / dt_sec)  # 36 ticks per 6h
    n_chunks = (n_steps + chunk_size - 1) // chunk_size
    print(f"\n--- per-6h-window calib trace ---", flush=True)
    print(f"{'window (h)':<12}  {'pferr':>5}  {'σ':>5}  {'calib':>5}  "
          f"{'ess_mean':>8}  {'%surf':>6}  {'bT':>6}  {'bS':>6}",
          flush=True)
    print("-" * 70, flush=True)
    for ci in range(n_chunks):
        a = ci * chunk_size
        b = min(a + chunk_size, n_steps)
        h0 = a * dt_sec / 3600.0
        h1 = b * dt_sec / 3600.0
        # Submerged-only ticks for cleaner calib (surface ticks have
        # σ ≈ σ_lora ≈ 20m by definition, dilute the metric).
        sub_mask = ~surf_mask[a:b]
        if not sub_mask.any():
            continue
        pferr_sub = pferr[a:b][sub_mask]
        pfstd_sub = pfstd[a:b][sub_mask]
        ess_sub = ess_arr[a:b][sub_mask]
        bT_sub = bT_arr[a:b][sub_mask]
        bS_sub = bS_arr[a:b][sub_mask]
        err_rms = float(np.sqrt(np.nanmean(pferr_sub ** 2)))
        std_rms = float(np.sqrt(np.nanmean(pfstd_sub ** 2)))
        calib = err_rms / max(std_rms, 1e-6)
        pct_surf = 100.0 * surf_mask[a:b].mean()
        print(f"{h0:5.1f}-{h1:5.1f}h    {pferr_sub.mean():5.0f}  "
              f"{pfstd_sub.mean():4.0f}  {calib:4.2f}  "
              f"{ess_sub.mean():8.0f}  {pct_surf:5.1f}%  "
              f"{np.nanmean(bT_sub):+5.2f}  {np.nanmean(bS_sub):+5.2f}",
              flush=True)

    # Suspect (A) — bias S over-correction correlation.
    print(f"\n--- suspect (A): bias_S vs calib correlation ---", flush=True)
    # Window-mean bias_S vs window-calib.
    win_calibs = []
    win_bS = []
    win_bT = []
    win_ess = []
    for ci in range(n_chunks):
        a = ci * chunk_size
        b = min(a + chunk_size, n_steps)
        sub_mask = ~surf_mask[a:b]
        if not sub_mask.any():
            continue
        err_rms = float(np.sqrt(np.nanmean(pferr[a:b][sub_mask] ** 2)))
        std_rms = float(np.sqrt(np.nanmean(pfstd[a:b][sub_mask] ** 2)))
        win_calibs.append(err_rms / max(std_rms, 1e-6))
        win_bS.append(float(np.nanmean(bS_arr[a:b][sub_mask])))
        win_bT.append(float(np.nanmean(bT_arr[a:b][sub_mask])))
        win_ess.append(float(np.nanmean(ess_arr[a:b][sub_mask])))
    win_calibs = np.array(win_calibs)
    win_bS = np.array(win_bS)
    win_bT = np.array(win_bT)
    win_ess = np.array(win_ess)
    bS_err = np.abs(win_bS - (-0.40))   # |bias_S - truth|
    if win_calibs.size >= 3:
        # Robust pairwise pearson correlations.
        def _corr(x, y):
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() < 3:
                return float("nan")
            return float(np.corrcoef(x[mask], y[mask])[0, 1])
        print(f"  corr(calib, |bS - truth|)  = {_corr(win_calibs, bS_err):.2f}",
              flush=True)
        print(f"  corr(calib, bT estimate)   = {_corr(win_calibs, win_bT):.2f}",
              flush=True)
        print(f"  corr(calib, ESS)           = {_corr(win_calibs, win_ess):.2f}",
              flush=True)
        print("  (negative ESS-calib corr → resampling collapse worsens calib)",
              flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
