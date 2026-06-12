"""Single-mission bias-state cov shrinkage diagnostic.

Question: is the bias-state's per-particle Matérn cov actually shrinking
at visited cells, or is OU re-inflation / large σ_obs killing the
Kalman gain?

Approach:
  1. Run one grid+ctd mission (S1, seed=1000) with tick_recorder
     capturing per-tick bias.cov_u diagonal at visited cells AND at
     unvisited cells (control), plus prior σ² for reference.
  2. Plot cov diagonal (visited / unvisited / prior) over time.
  3. Print mid-mission and end-of-mission summary stats.

If cov at visited cells shrinks to <50% of prior over the mission:
Kalman is operating, residual calib gap is "wide-prior, central-realisation"
and fix is to reduce sigma_bias_init_ms (or accept-and-retune).

If cov barely shrinks (>80% of prior): Kalman gain is too gentle or
OU re-inflation undoes shrinkage; mechanism bug to chase.
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
        if not (np.isfinite(ut) and np.isfinite(vt)): return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, d, t)
        return ut + un, vt + vn
    def sample_batched(self, lats, lons, depths, t):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t)
        un, vn = self.noise.sample_batched(lats, lons, depths, t)
        u = np.where(np.isfinite(ut), ut + un, np.nan)
        v = np.where(np.isfinite(vt), vt + vn, np.nan)
        return u, v
    def get_current_at(self, *a): return self.sample(*a)
    def get_current_at_batched(self, *a): return self.sample_batched(*a)


class _RealTracer:
    def __init__(self, t, tn): self.tracer, self.tn = t, tn
    def sample(self, lat, lon, d, t):
        Tt, St = self.tracer.sample(lat, lon, d, t)
        if not (np.isfinite(Tt) and np.isfinite(St)): return float("nan"), float("nan")
        Tn, Sn = self.tn.sample(lat, lon, d, t)
        return Tt + Tn, St + Sn


class _NemoPrior:
    def __init__(self, n): self.nemo = n
    def sample(self, l, lo, d, t): return self.nemo.sample(l, lo, d, t)
    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)
    def get_current_at(self, *a): return self.sample(*a)
    def get_current_at_batched(self, *a): return self.sample_batched(*a)


def main() -> None:
    from rbpf_prototype import (  # type: ignore[import-not-found]
        BiasConfig, CTDSensor, Experiment, FixedIntervalPolicy,
        LoRaRangeSensor, PFConfig, SensorConfig, SimConfig,
        StationConfig, run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    print("=== bias-state cov shrinkage diagnostic "
          "(S1 grid+ctd seed=1000) ===", flush=True)
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

    rec: list[dict] = []

    def recorder(t_sec, state, pf, bias):
        if bias is None:
            return
        # Per-tick: weighted-mean across particles of cov diag at
        # visited cells (cells with non-zero dwell) vs unvisited cells.
        # Also report basin-coh inertial cell as control.
        D = bias.dwell.reshape(pf.n, *bias.dwell.shape[1:])  # (N, D, Y, X)
        D_flat = D.reshape(pf.n, D.shape[1], D.shape[2] * D.shape[3])
        # cov_u/cov_v: (N, D, Y·X, Y·X). Diagonal: (N, D, Y·X)
        diag_u = np.einsum('ndii->ndi', bias.cov_u)
        diag_v = np.einsum('ndii->ndi', bias.cov_v)
        # Visited: cells with D > 0
        visited_mask = D_flat > 0  # (N, D, Y·X)
        # Per-particle mean cov over visited cells (weighted by dwell)
        if visited_mask.any():
            weighted_diag_u = np.where(visited_mask, diag_u * D_flat, 0.0)
            weighted_diag_v = np.where(visited_mask, diag_v * D_flat, 0.0)
            tot_dwell = np.where(visited_mask, D_flat, 0.0).sum(axis=-1)  # (N, D)
            tot_dwell_safe = np.where(tot_dwell > 0, tot_dwell, 1.0)
            mean_cov_u_visited_per = (weighted_diag_u.sum(axis=-1)
                                       / tot_dwell_safe)  # (N, D)
            mean_cov_v_visited_per = (weighted_diag_v.sum(axis=-1)
                                       / tot_dwell_safe)
            # Average across particles + depths (only finite entries)
            valid = tot_dwell > 0
            mean_cov_u_visited = float(np.mean(
                mean_cov_u_visited_per[valid])) if valid.any() else float("nan")
            mean_cov_v_visited = float(np.mean(
                mean_cov_v_visited_per[valid])) if valid.any() else float("nan")
        else:
            mean_cov_u_visited = float("nan")
            mean_cov_v_visited = float("nan")

        # Unvisited cells: D == 0
        unvisited_mask = D_flat == 0
        if unvisited_mask.any():
            mean_cov_u_unvisited = float(np.mean(diag_u[unvisited_mask]))
            mean_cov_v_unvisited = float(np.mean(diag_v[unvisited_mask]))
        else:
            mean_cov_u_unvisited = float("nan")
            mean_cov_v_unvisited = float("nan")

        # Reference: prior diagonal is bias.cov_prior (Y·X, Y·X) diag.
        prior_diag = float(np.mean(np.diag(bias.cov_prior)))

        rec.append({
            "t_sec": t_sec,
            "depth_m": state.depth_m,
            "mean_cov_u_visited": mean_cov_u_visited,
            "mean_cov_v_visited": mean_cov_v_visited,
            "mean_cov_u_unvisited": mean_cov_u_unvisited,
            "mean_cov_v_unvisited": mean_cov_v_unvisited,
            "prior_diag": prior_diag,
        })

    t1 = time.time()
    r = run_one_station(exp, seed=1000, tick_recorder=recorder)
    print(f"run done {time.time() - t1:.1f}s ({len(rec)} ticks recorded)",
          flush=True)

    # Aggregate stats.
    visited_u = np.array([r["mean_cov_u_visited"] for r in rec])
    visited_v = np.array([r["mean_cov_v_visited"] for r in rec])
    unvisited_u = np.array([r["mean_cov_u_unvisited"] for r in rec])
    unvisited_v = np.array([r["mean_cov_v_unvisited"] for r in rec])
    prior = np.array([r["prior_diag"] for r in rec])
    t_h = np.array([r["t_sec"] for r in rec]) / 3600.0

    finite_v = np.isfinite(visited_u) & np.isfinite(visited_v)
    print(f"\n=== aggregate cov diagonal across mission ===", flush=True)
    print(f"  prior diagonal:          {np.nanmean(prior):.5f} (m/s)² "
          f"= ({np.sqrt(np.nanmean(prior))*100:.2f} cm/s)²", flush=True)
    print(f"  visited cells (u, time-avg): {np.nanmean(visited_u[finite_v]):.5f} (m/s)² "
          f"= ({np.sqrt(np.nanmean(visited_u[finite_v]))*100:.2f} cm/s)²",
          flush=True)
    print(f"  visited cells (v, time-avg): {np.nanmean(visited_v[finite_v]):.5f} (m/s)² "
          f"= ({np.sqrt(np.nanmean(visited_v[finite_v]))*100:.2f} cm/s)²",
          flush=True)
    print(f"  visited / prior ratio (u): "
          f"{np.nanmean(visited_u[finite_v]) / np.nanmean(prior):.3f}",
          flush=True)
    print(f"  visited / prior ratio (v): "
          f"{np.nanmean(visited_v[finite_v]) / np.nanmean(prior):.3f}",
          flush=True)
    print(f"  unvisited cells (u, time-avg): {np.nanmean(unvisited_u):.5f} (m/s)² "
          f"= ({np.sqrt(np.nanmean(unvisited_u))*100:.2f} cm/s)²",
          flush=True)
    print(f"  unvisited / prior ratio (u): "
          f"{np.nanmean(unvisited_u) / np.nanmean(prior):.3f}", flush=True)

    # Mid-mission and end-of-mission separately.
    mid = (t_h >= 30) & (t_h <= 42)  # hours 30-42
    end = t_h >= 60                   # hours 60-72
    print(f"\n=== mid-mission (h 30-42) vs end (h 60-72) ===", flush=True)
    if mid.any() and finite_v.any():
        print(f"  mid: visited/prior u={np.nanmean(visited_u[mid & finite_v]) / np.nanmean(prior[mid]):.3f}, "
              f"v={np.nanmean(visited_v[mid & finite_v]) / np.nanmean(prior[mid]):.3f}",
              flush=True)
    if end.any() and finite_v.any():
        print(f"  end: visited/prior u={np.nanmean(visited_u[end & finite_v]) / np.nanmean(prior[end]):.3f}, "
              f"v={np.nanmean(visited_v[end & finite_v]) / np.nanmean(prior[end]):.3f}",
              flush=True)

    # Build chart.
    print(f"\n=== building chart ===", flush=True)
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 1, figsize=(13, 6))
    ax.plot(t_h, prior, color="black", linestyle="--", lw=1.0,
             label="prior diag (constant)")
    ax.plot(t_h, visited_u, color="C0", lw=1.0, alpha=0.8,
             label="visited cells: cov_u diag (dwell-weighted)")
    ax.plot(t_h, visited_v, color="C2", lw=1.0, alpha=0.8,
             label="visited cells: cov_v diag (dwell-weighted)")
    ax.plot(t_h, unvisited_u, color="C0", linestyle=":", lw=0.7, alpha=0.6,
             label="unvisited cells: cov_u diag (avg)")
    ax.plot(t_h, unvisited_v, color="C2", linestyle=":", lw=0.7, alpha=0.6,
             label="unvisited cells: cov_v diag (avg)")
    ax.set_xlabel("mission time (h)")
    ax.set_ylabel("bias velocity variance (m/s)²")
    ax.set_title("Bias-state Matérn cov diagonal: shrinkage at visited "
                  "vs prior (S1 grid+ctd seed=1000)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    out = "figures/bias_cov_shrinkage.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"  saved {out}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
