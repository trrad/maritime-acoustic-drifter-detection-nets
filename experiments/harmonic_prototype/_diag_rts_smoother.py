"""Validate the RTS smoother on a single grid+ctd mission.

Question: how much does retroactive smoothing reduce mid-leg σ_pos
relative to the real-time PF posterior σ?

Approach:
  1. Run one mission (S1, seed=1000, grid+ctd, fixed_6h surfacing) with
     full per-tick (mean, cov) capture.
  2. Apply the RTS smoother (offline backward pass over the recorded
     trajectory).
  3. Compare per-tick:
        - PF forward σ_pos (real-time posterior std)
        - PF actual error (truth distance from cluster mean)
        - RTS smoothed σ_pos (retroactive)
        - RTS actual error (truth distance from smoothed mean)
  4. Plot 4-panel chart: σ traces, error traces, σ-vs-error scatter,
     improvement by time-since-LoRa-fix.

Saves figures/rts_smoother_validation.png. ~5 min wall after init.
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
        LoRaRangeSensor, PFConfig, ProcessNoiseConfig, SensorConfig,
        SimConfig, StationConfig, rts_smooth_trajectory, run_one_station,
    )
    from truth_field import (  # type: ignore[import-not-found]
        EARTH_R_M, distance_m,
    )

    print("=== RTS smoother validation (S1 grid+ctd seed=1000 fixed_6h) ===",
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
    t1 = time.time()
    r = run_one_station(exp, seed=1000)
    print(f"mission run done {time.time() - t1:.1f}s", flush=True)

    # Apply RTS smoother.
    t2 = time.time()
    pn_cfg = ProcessNoiseConfig()   # defaults match the truth-field build
    smoothed = rts_smooth_trajectory(
        pf_mean_lats=r.pf_mean_lats,
        pf_mean_lons=r.pf_mean_lons,
        pf_cov_m=r.pf_cov_m,
        depths=r.depths,
        lora_fix_mask=r.lora_fix_mask,
        dt_sec=sim.dt_sec,
        process_noise_cfg=pn_cfg,
    )
    print(f"smoother done {time.time() - t2:.2f}s "
          f"({r.pf_mean_lats.size} ticks)", flush=True)

    # Compare.
    s_sigma = smoothed.sigma_pos_per_axis_m()
    f_sigma = r.pf_std_m
    s_lat_arr, s_lon_arr = smoothed.to_latlon()
    s_err = np.array([
        distance_m(r.lats[i], r.lons[i], s_lat_arr[i], s_lon_arr[i])
        for i in range(r.lats.size)
    ])
    f_err = r.pf_err_m

    n_steps = r.pf_mean_lats.size
    t_h = np.arange(n_steps) * sim.dt_sec / 3600.0
    finite = np.isfinite(f_err) & np.isfinite(s_err)

    # Aggregate stats.
    print(f"\n=== aggregate: forward filter vs RTS smoother ===", flush=True)
    print(f"  forward σ:       mean={np.nanmean(f_sigma[finite]):6.0f}m  "
          f"p95={np.nanpercentile(f_sigma[finite], 95):.0f}m", flush=True)
    print(f"  smoothed σ:      mean={np.nanmean(s_sigma[finite]):6.0f}m  "
          f"p95={np.nanpercentile(s_sigma[finite], 95):.0f}m  "
          f"(reduction: {100 * (1 - np.nanmean(s_sigma[finite]) / np.nanmean(f_sigma[finite])):+.1f}%)",
          flush=True)
    print(f"  forward |error|: mean={np.nanmean(f_err[finite]):6.0f}m  "
          f"p95={np.nanpercentile(f_err[finite], 95):.0f}m", flush=True)
    print(f"  smoothed |error|: mean={np.nanmean(s_err[finite]):6.0f}m  "
          f"p95={np.nanpercentile(s_err[finite], 95):.0f}m  "
          f"(reduction: {100 * (1 - np.nanmean(s_err[finite]) / np.nanmean(f_err[finite])):+.1f}%)",
          flush=True)
    f_calib = np.sqrt(np.nanmean(f_err[finite] ** 2)
                       / max(np.nanmean(f_sigma[finite] ** 2), 1.0))
    s_calib = np.sqrt(np.nanmean(s_err[finite] ** 2)
                       / max(np.nanmean(s_sigma[finite] ** 2), 1.0))
    print(f"  calib (RMS err / RMS σ):  forward={f_calib:.2f}  "
          f"smoothed={s_calib:.2f}  (≈1 if calibrated)", flush=True)

    # Submerged-only stats — the deployment-relevant slice.
    sub = ~r.at_surface_mask & finite
    print(f"\n=== submerged-only ({sub.sum()} ticks; {100*sub.mean():.1f}% of mission) ===",
          flush=True)
    print(f"  forward σ:       mean={np.nanmean(f_sigma[sub]):6.0f}m  "
          f"p95={np.nanpercentile(f_sigma[sub], 95):.0f}m", flush=True)
    print(f"  smoothed σ:      mean={np.nanmean(s_sigma[sub]):6.0f}m  "
          f"p95={np.nanpercentile(s_sigma[sub], 95):.0f}m  "
          f"(reduction: {100 * (1 - np.nanmean(s_sigma[sub]) / np.nanmean(f_sigma[sub])):+.1f}%)",
          flush=True)
    print(f"  smoothed |error|: mean={np.nanmean(s_err[sub]):6.0f}m  "
          f"p95={np.nanpercentile(s_err[sub], 95):.0f}m", flush=True)

    # Build chart.
    print(f"\n=== building chart ===", flush=True)
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(4, 1, figsize=(13, 14), sharex=False)

    # Surface event entries.
    surf_entries_h = (np.flatnonzero(np.diff(r.at_surface_mask.astype(int)) > 0)
                      * sim.dt_sec / 3600.0)

    def _shade_surface(ax):
        for h_e in surf_entries_h:
            ax.axvspan(h_e, h_e + 0.5, alpha=0.12, color="orange", zorder=0)

    # Panel 1: σ_pos traces.
    ax = axes[0]
    ax.plot(t_h, f_sigma, color="C0", lw=1.0, label="forward σ_pos (real-time PF)")
    ax.plot(t_h, s_sigma, color="C2", lw=1.2,
             label="smoothed σ_pos (RTS, retroactive)")
    ax.set_ylabel("σ_pos (m)")
    ax.set_title("RTS smoother validation — S1 grid+ctd seed=1000")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 2: actual error traces.
    ax = axes[1]
    ax.plot(t_h, f_err, color="C0", lw=1.0, alpha=0.8,
             label="forward |truth − pf_mean|")
    ax.plot(t_h, s_err, color="C2", lw=1.2, alpha=0.8,
             label="smoothed |truth − rts_mean|")
    ax.set_ylabel("position error (m)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 3: σ vs error scatter (calibration view).
    ax = axes[2]
    ax.scatter(f_sigma[finite], f_err[finite], s=4, alpha=0.4,
                color="C0", label=f"forward (calib={f_calib:.2f})")
    ax.scatter(s_sigma[finite], s_err[finite], s=4, alpha=0.4,
                color="C2", label=f"smoothed (calib={s_calib:.2f})")
    lim = max(np.nanmax(f_sigma[finite]), np.nanmax(f_err[finite])) * 1.05
    ax.plot([0, lim], [0, lim], color="k", linestyle="--", lw=0.8,
             label="y = σ (perfect calibration)")
    ax.set_xlabel("σ_pos (m)")
    ax.set_ylabel("|error| (m)")
    ax.set_title("Calibration: |error| vs reported σ — points should track y=x")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)

    # Panel 4: σ improvement by time-since-LoRa-fix.
    # Bin ticks by time-since-last-LoRa, plot mean σ in each bin.
    n = r.pf_mean_lats.size
    tsa = np.zeros(n)
    last_t = 0.0
    for i in range(n):
        if r.lora_fix_mask[i]:
            last_t = i * sim.dt_sec
        tsa[i] = i * sim.dt_sec - last_t
    bins_h = np.linspace(0, 6, 19)   # 20-min bins from 0 to 6h
    bin_idx = np.digitize(tsa / 3600.0, bins_h) - 1
    bin_idx = np.clip(bin_idx, 0, len(bins_h) - 2)
    f_bin = np.full(len(bins_h) - 1, np.nan)
    s_bin = np.full(len(bins_h) - 1, np.nan)
    for b in range(len(bins_h) - 1):
        mask = (bin_idx == b) & finite
        if mask.any():
            f_bin[b] = np.nanmean(f_sigma[mask])
            s_bin[b] = np.nanmean(s_sigma[mask])
    centers = 0.5 * (bins_h[:-1] + bins_h[1:])
    ax = axes[3]
    ax.plot(centers, f_bin, marker="o", color="C0",
             label="forward σ_pos (mean per bin)")
    ax.plot(centers, s_bin, marker="o", color="C2",
             label="smoothed σ_pos (mean per bin)")
    ax.set_xlabel("hours since last LoRa fix")
    ax.set_ylabel("mean σ_pos in bin (m)")
    ax.set_title("σ_pos as function of time-since-last-LoRa "
                  "(retroactive smoother fills mid-leg)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    out = "figures/rts_smoother_validation.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"  saved {out}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
