"""Comprehensive single-mission breakdown: rich per-tick traces + chart.

Goals:
  (a) Understand bias_S over-correction (Issue A.3). Hypothesis: PF
      cluster mean systematically deviates from truth in salinity-
      gradient direction, and the scalar bias absorbs both the basin
      bias AND the spatial-mismatch term. We check this by sampling
      `prior_S` at BOTH the truth position AND the cluster mean position
      every CTD tick, and showing that:
          bias_S_estimate ≈ (truth_S − prior_at_truth)
                          + (prior_at_truth − prior_at_cluster_mean)
                          ≈ truth_bias                  (the part we want)
                          + cluster_displacement_term  (the spurious part)

  (b) Multi-panel chart visualizing the mission: pferr vs σ, station-
      keeping vs MPC physics floor, surface events as markers, bias_T/
      bias_S estimates over time, cluster-vs-truth distance.

Run on (S1, seed=1000, grid+ctd, fixed_6h). ~5-7 min wall clock.
Saves PNG to figures/mission_breakdown.png.
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
    from truth_field import (  # type: ignore[import-not-found]
        EARTH_R_M, distance_m,
    )

    print("=== mission breakdown (S1 grid+ctd seed=1000 fixed_6h) ===",
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

    # Per-tick recorder. Closes over `tracer`, `real_tracer`, `s_lat`,
    # `s_lon` so we can sample prior at both truth and cluster positions
    # for the A.3 attribution analysis.
    rec: list[tuple] = []

    def recorder(t_sec, state, pf, bias):
        ml, mo = pf.mean()  # cluster mean (lat, lon)
        ess = pf.ess()
        # Sample prior_S at TRUTH position and at CLUSTER MEAN position.
        T_prior_truth, S_prior_truth = tracer.sample(
            state.lat, state.lon, state.depth_m, t_sec,
        )
        T_prior_cluster, S_prior_cluster = tracer.sample(
            ml, mo, state.depth_m, t_sec,
        )
        # Sample TRUTH (with noise) at truth position — what CTD would read.
        T_truth, S_truth = real_tracer.sample(
            state.lat, state.lon, state.depth_m, t_sec,
        )
        if bias is not None:
            w = pf.weights
            bT = float(np.sum(bias.bias_T_offset * w))
            bS = float(np.sum(bias.bias_S_offset * w))
            PS = float(np.sqrt(np.sum(bias.P_S_offset * w)))
            PT = float(np.sqrt(np.sum(bias.P_T_offset * w)))
        else:
            bT = bS = PS = PT = float("nan")
        d_to_station = distance_m(state.lat, state.lon, s_lat, s_lon)
        rec.append((
            t_sec, state.depth_m, state.depth_setpoint_m,
            ml, mo, state.lat, state.lon,
            ess, bT, bS, PT, PS,
            T_prior_truth, S_prior_truth,
            T_prior_cluster, S_prior_cluster,
            T_truth, S_truth,
            d_to_station,
        ))

    t1 = time.time()
    r = run_one_station(exp, seed=1000, tick_recorder=recorder)
    print(f"run done {time.time() - t1:.1f}s", flush=True)

    # Unpack arrays.
    a = np.array(rec, dtype=float)
    t_h = a[:, 0] / 3600.0
    depth = a[:, 1]
    setp = a[:, 2]
    cl_lat = a[:, 3]
    cl_lon = a[:, 4]
    tr_lat = a[:, 5]
    tr_lon = a[:, 6]
    ess = a[:, 7]
    bT_est = a[:, 8]
    bS_est = a[:, 9]
    PT_std = a[:, 10]
    PS_std = a[:, 11]
    T_pri_tr = a[:, 12]
    S_pri_tr = a[:, 13]
    T_pri_cl = a[:, 14]
    S_pri_cl = a[:, 15]
    T_tr = a[:, 16]
    S_tr = a[:, 17]
    d_to_station = a[:, 18]
    pferr = r.pf_err_m[1:]   # recorder fires per tick after pf_err_m[i+1] set
    pfstd = r.pf_std_m[1:]
    surf_mask = r.at_surface_mask[1:]

    # A.3 attribution analysis.
    # Decompose: bias_S_estimate ≈ (truth_S − prior_at_truth)
    #                            + (prior_at_truth − prior_at_cluster)
    #                            ≈ true_basin_bias + spatial_mismatch_term
    truth_basin_bias_S = S_tr - S_pri_tr   # what bias_S SHOULD converge to
    spatial_mismatch_S = S_pri_tr - S_pri_cl
    truth_basin_bias_T = T_tr - T_pri_tr
    spatial_mismatch_T = T_pri_tr - T_pri_cl

    # Average over submerged ticks (CTD only fires submerged).
    submerged = ~surf_mask
    finite = (np.isfinite(truth_basin_bias_S) & np.isfinite(spatial_mismatch_S)
              & np.isfinite(bS_est) & submerged)
    print(f"\n=== A.3 attribution analysis (over {finite.sum()} submerged ticks) ===",
          flush=True)
    if finite.any():
        print(f"  truth basin bias_S       = {np.mean(truth_basin_bias_S[finite]):+.3f} PSU "
              f"(should be ≈ -0.40, the planted offset)", flush=True)
        print(f"  spatial mismatch term    = {np.mean(spatial_mismatch_S[finite]):+.3f} PSU "
              f"(prior_at_truth − prior_at_cluster)", flush=True)
        print(f"  predicted bias_S estimate = {np.mean(truth_basin_bias_S[finite] + spatial_mismatch_S[finite]):+.3f} PSU "
              f"(sum of above)", flush=True)
        print(f"  ACTUAL bias_S estimate    = {np.mean(bS_est[finite]):+.3f} PSU", flush=True)
        print(f"  └── consistency check: predicted vs actual differ by "
              f"{np.mean(bS_est[finite]) - np.mean(truth_basin_bias_S[finite] + spatial_mismatch_S[finite]):+.3f}",
              flush=True)
        print(f"\n  truth basin bias_T       = {np.mean(truth_basin_bias_T[finite]):+.3f} °C", flush=True)
        print(f"  spatial mismatch (T)     = {np.mean(spatial_mismatch_T[finite]):+.3f} °C", flush=True)
        print(f"  predicted bias_T estimate = {np.mean(truth_basin_bias_T[finite] + spatial_mismatch_T[finite]):+.3f} °C",
              flush=True)
        print(f"  ACTUAL bias_T estimate    = {np.mean(bT_est[finite]):+.3f} °C", flush=True)

    # Cluster-truth gap analysis.
    cluster_gap_m = np.array([
        np.sqrt(((cl_lat[i] - tr_lat[i]) * EARTH_R_M) ** 2
                + ((cl_lon[i] - tr_lon[i]) * EARTH_R_M
                   * np.cos(np.deg2rad(tr_lat[i]))) ** 2)
        for i in range(t_h.size)
    ])
    print(f"\n=== cluster-vs-truth gap ===", flush=True)
    print(f"  mean cluster_gap        = {np.mean(cluster_gap_m[finite]):.0f} m", flush=True)
    print(f"  p95  cluster_gap        = {np.nanpercentile(cluster_gap_m[finite], 95):.0f} m", flush=True)

    # Build chart.
    print(f"\n=== building chart ===", flush=True)
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(6, 1, figsize=(13, 18), sharex=True)

    # Physics-floor reference (perfect-info MPC h=12 b=200 mean dist)
    # for S1-S4 average, per controller_mpc_baseline_2026-04-26.md.
    PHYSICS_FLOOR_M = 559.0

    # Surface event ticks (entry transitions).
    surf_entries_h = (np.flatnonzero(np.diff(surf_mask.astype(int)) > 0)
                      * sim.dt_sec / 3600.0)

    def _shade_surface(ax):
        for h_e in surf_entries_h:
            ax.axvspan(h_e, h_e + 0.5, alpha=0.15, color="orange", zorder=0)

    # Panel 1: pferr + σ.
    ax = axes[0]
    ax.plot(t_h, pferr, color="C0", label="pferr", lw=1.0)
    ax.plot(t_h, pfstd, color="C1", label="pf_std (PF posterior σ)", lw=1.0)
    ax.set_ylabel("position error (m)")
    ax.set_title(f"Mission breakdown — S1, seed=1000, grid+ctd, fixed_6h "
                  f"surfacing  ({sim.run_hours}h)")
    ax.legend(loc="upper right", ncol=2, fontsize=9)
    ax.set_ylim(0, max(np.nanpercentile(pferr, 99), 1e3))
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 2: station-keeping vs physics floor.
    ax = axes[1]
    ax.plot(t_h, d_to_station, color="C2", label="truth dist to station", lw=1.0)
    ax.axhline(PHYSICS_FLOOR_M, color="black", linestyle="--", lw=1.0,
                label=f"perfect-info MPC mean ({PHYSICS_FLOOR_M:.0f}m)")
    ax.axhline(station.envelope_m, color="red", linestyle=":", lw=1.0,
                label=f"envelope ({station.envelope_m:.0f}m)")
    ax.set_ylabel("dist to station (m)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 3: depth + surface dwell.
    ax = axes[2]
    ax.plot(t_h, depth, color="C3", label="actual depth", lw=1.0)
    ax.plot(t_h, setp, color="C4", linestyle=":", label="setpoint", lw=1.0)
    ax.set_ylabel("depth (m)")
    ax.invert_yaxis()
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 4: bias_T, bias_S estimates vs truth basin bias.
    ax = axes[3]
    ax.plot(t_h, bT_est, color="C0", label="bias_T estimate (°C)", lw=1.0)
    ax.plot(t_h, truth_basin_bias_T, color="C0", linestyle=":", alpha=0.5,
             label="truth basin bias_T (°C)", lw=0.8)
    ax.set_ylabel("T bias (°C)", color="C0")
    ax.tick_params(axis="y", labelcolor="C0")
    ax2 = ax.twinx()
    ax2.plot(t_h, bS_est, color="C5", label="bias_S estimate (PSU)", lw=1.0)
    ax2.plot(t_h, truth_basin_bias_S, color="C5", linestyle=":", alpha=0.5,
              label="truth basin bias_S (PSU)", lw=0.8)
    ax2.set_ylabel("S bias (PSU)", color="C5")
    ax2.tick_params(axis="y", labelcolor="C5")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 5: A.3 decomposition — bias_S estimate vs (truth + spatial-mismatch).
    ax = axes[4]
    ax.plot(t_h, bS_est, color="C5", label="bias_S estimate (PSU)", lw=1.0)
    ax.plot(t_h, truth_basin_bias_S, color="black", linestyle="--",
             label="truth basin bias_S", lw=1.0)
    ax.plot(t_h, truth_basin_bias_S + spatial_mismatch_S, color="C8",
             label="predicted = truth + spatial mismatch", lw=1.0, alpha=0.7)
    ax.plot(t_h, spatial_mismatch_S, color="C9",
             label="spatial mismatch (prior@truth − prior@cluster)", lw=0.8,
             alpha=0.6)
    ax.set_ylabel("PSU")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    # Panel 6: cluster-truth gap.
    ax = axes[5]
    ax.plot(t_h, cluster_gap_m, color="C7", label="|cluster mean − truth|", lw=1.0)
    ax.set_ylabel("cluster gap (m)")
    ax.set_xlabel("mission time (h)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    _shade_surface(ax)

    plt.tight_layout()
    out = "figures/mission_breakdown.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"  saved {out}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
