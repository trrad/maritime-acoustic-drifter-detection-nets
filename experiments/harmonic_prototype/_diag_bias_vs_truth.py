"""Diagnostic: does the v1 grid bias-learner recover reality?

Runs one (station × seed) at σ_fc = 8 cm/s under the M1 layered noise.
At end of mission, compares the learned bias state against the time-
averaged true noise at each cell, broken down by component.

Outputs:
  - figures/_diag_bias_vs_truth.png:
    Per-depth scatter plots of learned u, v vs true time-averaged u, v.
    With per-component decomposition (coh, plume, submeso, inertial,
    white) shown alongside.
  - Console: correlation, RMSE per (depth, component).

Question being answered:
  Is the v1 grid bias-learner able to recover the realisation-specific
  slow-component noise within this 72 h mission window? Or is it
  fitting structurally-incompatible signals into a 640-dim grid that
  doesn't match the underlying physics?
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from rbpf_prototype import (  # type: ignore[import-not-found]
    BiasConfig, Experiment, FixedIntervalPolicy, LoRaRangeSensor,
    PFConfig, SensorConfig, SimConfig, StationConfig, run_one_station,
)
from rbpf_prototype.bias_field import GridBiasBasis  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_layered_noise_field  # type: ignore[import-not-found]
from truth_field import EARTH_R_M, build_truth_field  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]


class RealCurrents:
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


class NemoPrior:
    def __init__(self, nemo):
        self.nemo = nemo

    def sample(self, lat, lon, depth_m, t_sec):
        return self.nemo.sample(lat, lon, depth_m, t_sec)

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


def time_avg_noise_components(
    noise, lat: float, lon: float, depth_m: float,
    t_sec_array: np.ndarray,
) -> dict[str, tuple[float, float]]:
    """Time-average each component of the layered noise at (lat, lon, depth).

    Returns a dict with one entry per component plus 'total':
      coh, plume, submeso_z, inertial_z, white, total → (mean_u, mean_v)
    """
    z = max(depth_m, 0.0)
    plume_prof = 0.5 * (1.0 - np.tanh(
        (z - noise.plume_base_m) / max(noise.plume_width_m, 0.1)
    ))
    surf_prof = float(np.exp(-z / max(noise.L_z_surf_m, 1e-6)))
    inr_prof = float(np.exp(-z / max(noise.L_z_inertial_m, 1e-6)))

    sums = {k: np.zeros(2) for k in
             ["coh", "plume_z", "submeso_z", "inertial_z", "white", "total"]}
    n = 0
    for t in t_sec_array:
        t = float(t)
        uc, vc = noise.coh.sample(lat, lon, t)
        up, vp = noise.plume.sample(lat, lon, t)
        us, vs = noise.submeso_wind.sample(lat, lon, t)
        ui, vi = noise.inertial.sample(lat, lon, z, t)  # already depth-applied
        uw, vw = noise.white.sample(lat, lon, t)
        if not all(np.isfinite([uc, vc, up, vp, us, vs, ui, vi, uw, vw])):
            continue
        sums["coh"]        += [uc, vc]
        sums["plume_z"]    += [plume_prof * up, plume_prof * vp]
        sums["submeso_z"]  += [surf_prof * us, surf_prof * vs]
        sums["inertial_z"] += [ui, vi]   # already depth-applied
        sums["white"]      += [uw, vw]
        n += 1
    if n == 0:
        return {k: (float("nan"), float("nan")) for k in sums}
    out: dict[str, tuple[float, float]] = {}
    for k, s in sums.items():
        if k == "total":
            continue
        out[k] = (float(s[0] / n), float(s[1] / n))
    out["total"] = (
        sum(out[k][0] for k in ("coh", "plume_z", "submeso_z",
                                  "inertial_z", "white")),
        sum(out[k][1] for k in ("coh", "plume_z", "submeso_z",
                                  "inertial_z", "white")),
    )
    return out


def main() -> None:
    print("=== diagnostic: bias_learned vs true time-averaged noise ===",
          flush=True)
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)

    print("building (u, v) interpolator + layered noise ...", flush=True)
    t0 = time.time()
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    noise = build_layered_noise_field(
        ds, lats_grid, lons_grid, seed=42,
    )
    print(f"  {time.time() - t0:.1f}s", flush=True)

    real = RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = NemoPrior(nemo=nemo)

    s_lat_target, s_lon_target = 49.3533, -123.7411
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    print(f"\nstation: ({s_lat:.4f}, {s_lon:.4f}) bathy={s_bathy:.0f}m",
          flush=True)

    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(lat=s_lat, lon=s_lon, envelope_m=3000.0,
                              available_depths_m=d_set)
    sim_cfg = SimConfig(
        run_hours=72, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)

    cos_lat = np.cos(np.deg2rad(s_lat))
    anchors = [
        (s_lat + dn * 1000.0 / EARTH_R_M,
         s_lon + de * 1000.0 / (EARTH_R_M * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]
    sensor_cfg = SensorConfig(
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0, max_depth_m=1.0),
        flow=None, ctd=None,
    )
    bias_init = float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2))
    # Spatial regularisation comes from the dense Matérn covariance prior
    # (l_corr_m=5km, matern_nu=0.5) inside BiasFieldState. σ_obs is now
    # computed analytically per-leg from the unlearnable layered-noise
    # components × dwell-weighted depth attenuation.
    bias_cfg = BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=bias_init,
    )
    print(f"\nbias config: n_cells=8 cell=2000m init=7.8cm/s "
          f"σ_obs=analytical-per-leg  l_corr=5km matern_nu=0.5",
          flush=True)

    # Run mission with bias learning. We use a wrapper run that keeps the
    # final bias state accessible.
    print("\nrunning grid mission (no CTD) to capture bias state ...",
          flush=True)
    # Hook: build the experiment, then call run_one_station — but afterwards
    # we want to inspect the bias state. The current run_one_station returns
    # only ExperimentResult, not the bias state. To get the bias state, we
    # patch the experiment to retain a reference.
    exp = Experiment(
        station=station, sim=sim_cfg, sensor=sensor_cfg,
        pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
    )
    t0 = time.time()
    r = run_one_station(exp, seed=1000)
    print(f"  {time.time() - t0:.1f}s  PFerr={float(np.mean(r.pf_err_m)):.0f}m  "
          f"|b|_max={r.bias_max_learned_mag_ms*100:.1f}cm/s  "
          f"surf={r.surface_events}  bias_updates={r.bias_updates}",
          flush=True)

    # Re-run to capture bias state. The cleanest way: re-run with a small
    # patch that exposes the final state. Since run_one_station doesn't
    # return it, replicate the run_one_station logic enough to keep the
    # bias state. Cheaper alternative: patch run_one_station via a thin
    # wrapper; here we just import the internals.
    from rbpf_prototype.experiment import run_one_station as run  # noqa: F401
    from rbpf_prototype.bias_field import BiasFieldState  # noqa: F401
    from rbpf_prototype.rbpf import PositionRBPF  # noqa: F401

    # Hack: re-run with the same seed, capturing bias state via monkey patch.
    # Specifically wrap kalman_update_leg so we can capture the final state.
    from rbpf_prototype import bias_field as bf_mod
    captured = {"state": None, "basis": None}
    orig_kalman = bf_mod.BiasFieldState.kalman_update_leg

    def capturing_kalman(self, *args, **kwargs):
        orig_kalman(self, *args, **kwargs)
        captured["state"] = self  # mutated in place; keep reference

    bf_mod.BiasFieldState.kalman_update_leg = capturing_kalman  # type: ignore

    # Also capture the basis. Patch GridBiasBasis init.
    captured_basis: list = []
    orig_basis = bf_mod.GridBiasBasis.__init__

    def capturing_basis(self, *args, **kwargs):
        orig_basis(self, *args, **kwargs)
        captured_basis.append(self)

    bf_mod.GridBiasBasis.__init__ = capturing_basis  # type: ignore

    print("\nrunning grid mission again, capturing bias state ...",
          flush=True)
    exp2 = Experiment(
        station=station, sim=sim_cfg, sensor=sensor_cfg,
        pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
        surfacing=FixedIntervalPolicy(period_h=6.0),
        bias_cfg=bias_cfg,
    )
    t0 = time.time()
    r2 = run_one_station(exp2, seed=1000)
    print(f"  {time.time() - t0:.1f}s  bias_updates={r2.bias_updates}",
          flush=True)

    # Restore patches
    bf_mod.BiasFieldState.kalman_update_leg = orig_kalman  # type: ignore
    bf_mod.GridBiasBasis.__init__ = orig_basis  # type: ignore

    bias_state = captured["state"]
    basis = captured_basis[-1] if captured_basis else None
    if bias_state is None or basis is None:
        raise RuntimeError("Failed to capture bias state.")

    # --- Compare learned vs truth ---
    print("\nsampling true noise time-averages over mission window ...",
          flush=True)
    t_samples = np.linspace(0, 72 * 3600, 73)  # hourly samples
    half = basis.half_extent_m
    cell = basis.cell_size_m

    # Cell centres in (lat, lon, depth)
    cell_lats = np.zeros((basis.n_cells, basis.n_cells))
    cell_lons = np.zeros((basis.n_cells, basis.n_cells))
    cos_lat0 = float(np.cos(np.deg2rad(basis.station_lat)))
    for yi in range(basis.n_cells):
        for xi in range(basis.n_cells):
            dy_m = -half + (yi + 0.5) * cell
            dx_m = -half + (xi + 0.5) * cell
            cell_lats[yi, xi] = basis.station_lat + dy_m / EARTH_R_M
            cell_lons[yi, xi] = basis.station_lon + dx_m / (EARTH_R_M * cos_lat0)

    n_d = basis.n_depths
    depths = list(basis.depth_centers_m)
    truth_total_u = np.zeros((n_d, basis.n_cells, basis.n_cells))
    truth_total_v = np.zeros((n_d, basis.n_cells, basis.n_cells))
    truth_coh_u = np.zeros_like(truth_total_u)
    truth_coh_v = np.zeros_like(truth_total_u)
    truth_plume_u = np.zeros_like(truth_total_u)
    truth_plume_v = np.zeros_like(truth_total_u)
    truth_submeso_u = np.zeros_like(truth_total_u)
    truth_submeso_v = np.zeros_like(truth_total_u)
    truth_inertial_u = np.zeros_like(truth_total_u)
    truth_inertial_v = np.zeros_like(truth_total_u)
    truth_white_u = np.zeros_like(truth_total_u)
    truth_white_v = np.zeros_like(truth_total_u)

    for di, depth in enumerate(depths):
        for yi in range(basis.n_cells):
            for xi in range(basis.n_cells):
                la = float(cell_lats[yi, xi])
                lo = float(cell_lons[yi, xi])
                comps = time_avg_noise_components(noise, la, lo, depth, t_samples)
                truth_total_u[di, yi, xi]    = comps["total"][0]
                truth_total_v[di, yi, xi]    = comps["total"][1]
                truth_coh_u[di, yi, xi]      = comps["coh"][0]
                truth_coh_v[di, yi, xi]      = comps["coh"][1]
                truth_plume_u[di, yi, xi]    = comps["plume_z"][0]
                truth_plume_v[di, yi, xi]    = comps["plume_z"][1]
                truth_submeso_u[di, yi, xi]  = comps["submeso_z"][0]
                truth_submeso_v[di, yi, xi]  = comps["submeso_z"][1]
                truth_inertial_u[di, yi, xi] = comps["inertial_z"][0]
                truth_inertial_v[di, yi, xi] = comps["inertial_z"][1]
                truth_white_u[di, yi, xi]    = comps["white"][0]
                truth_white_v[di, yi, xi]    = comps["white"][1]

    # Learned bias: ensemble-mean over particles
    learned_u = bias_state.mean_u.mean(axis=0)  # (D, Y, X)
    learned_v = bias_state.mean_v.mean(axis=0)
    # Per-particle posterior variance (averaged). Step 1: extract the
    # diagonal of the dense (Y·X, Y·X) covariance per particle per depth,
    # then average over particles and reshape back to (D, Y, X).
    n_d_b = bias_state.mean_u.shape[1]
    n_y_b = bias_state.mean_u.shape[2]
    n_x_b = bias_state.mean_u.shape[3]
    diag_u = np.diagonal(bias_state.cov_u, axis1=2, axis2=3)  # (N, D, Y·X)
    diag_v = np.diagonal(bias_state.cov_v, axis1=2, axis2=3)
    var_u_mean = diag_u.mean(axis=0).reshape(n_d_b, n_y_b, n_x_b)
    var_v_mean = diag_v.mean(axis=0).reshape(n_d_b, n_y_b, n_x_b)
    var_init = bias_init ** 2
    # "Visited" cell: variance has dropped at all from init (any dwell
    # accumulated from any particle through any leg). With ~20 leg
    # updates and 640 cells, the 0.5x-var threshold is too strict —
    # most learned cells have only a few percent variance reduction.
    learn_mask = (var_u_mean < 0.99 * var_init) | (var_v_mean < 0.99 * var_init)
    print(f"\nvar_u distribution: min={var_u_mean.min():.4f} "
          f"median={np.median(var_u_mean):.4f} max={var_u_mean.max():.4f} "
          f"(init={var_init:.4f})", flush=True)
    print(f"var_v distribution: min={var_v_mean.min():.4f} "
          f"median={np.median(var_v_mean):.4f} max={var_v_mean.max():.4f}",
          flush=True)
    print(f"learned cells (var < 0.99·var_init): {int(learn_mask.sum())}/"
          f"{learn_mask.size}", flush=True)
    print(f"learned cells (var < 0.5·var_init):  "
          f"{int(((var_u_mean < 0.5 * var_init) | (var_v_mean < 0.5 * var_init)).sum())}/"
          f"{learn_mask.size}", flush=True)
    print(f"|learned mean| > 0.005 m/s:          "
          f"{int(((np.abs(learned_u) > 0.005) | (np.abs(learned_v) > 0.005)).sum())}/"
          f"{learn_mask.size}", flush=True)

    # --- Stats ---
    print("\n--- correlation analysis (learned vs truth) ---", flush=True)
    print(f"{'depth':>5}  {'comp':>10}  {'cells':>6}  {'r_u':>6} {'r_v':>6}  "
          f"{'rmse_u':>7} {'rmse_v':>7}  "
          f"{'true_RMS_u':>9} {'true_RMS_v':>9}  "
          f"{'lrn_RMS_u':>9} {'lrn_RMS_v':>9}",
          flush=True)
    print("-" * 110, flush=True)
    for di, depth in enumerate(depths):
        lm = learn_mask[di]
        n_learned_cells = int(lm.sum())
        if n_learned_cells < 4:
            print(f"{depth:5.1f}  {'(insufficient learned cells)':>10}", flush=True)
            continue
        for label, tu, tv in [
            ("total",    truth_total_u[di],    truth_total_v[di]),
            ("coh",      truth_coh_u[di],      truth_coh_v[di]),
            ("submeso",  truth_submeso_u[di],  truth_submeso_v[di]),
            ("inertial", truth_inertial_u[di], truth_inertial_v[di]),
            ("plume",    truth_plume_u[di],    truth_plume_v[di]),
        ]:
            lu_flat = learned_u[di][lm]
            lv_flat = learned_v[di][lm]
            tu_flat = tu[lm]
            tv_flat = tv[lm]
            if lu_flat.std() < 1e-6 or tu_flat.std() < 1e-6:
                r_u = float("nan")
            else:
                r_u = float(np.corrcoef(lu_flat, tu_flat)[0, 1])
            if lv_flat.std() < 1e-6 or tv_flat.std() < 1e-6:
                r_v = float("nan")
            else:
                r_v = float(np.corrcoef(lv_flat, tv_flat)[0, 1])
            rmse_u = float(np.sqrt(np.mean((lu_flat - tu_flat) ** 2)))
            rmse_v = float(np.sqrt(np.mean((lv_flat - tv_flat) ** 2)))
            true_rms_u = float(np.sqrt(np.mean(tu_flat ** 2)))
            true_rms_v = float(np.sqrt(np.mean(tv_flat ** 2)))
            learned_rms_u = float(np.sqrt(np.mean(lu_flat ** 2)))
            learned_rms_v = float(np.sqrt(np.mean(lv_flat ** 2)))
            print(f"{depth:5.1f}  {label:>10}  {n_learned_cells:6d}  "
                  f"{r_u:+6.2f} {r_v:+6.2f}  "
                  f"{rmse_u*100:6.2f}cm {rmse_v*100:6.2f}cm  "
                  f"{true_rms_u*100:8.2f}cm {true_rms_v*100:8.2f}cm  "
                  f"{learned_rms_u*100:8.2f}cm {learned_rms_v*100:8.2f}cm",
                  flush=True)

    # --- Plot ---
    print("\nrendering scatter plots ...", flush=True)
    fig, axes = plt.subplots(n_d, 2, figsize=(10, 2.5 * n_d), squeeze=False)
    for di, depth in enumerate(depths):
        lm = learn_mask[di]
        for col, (lu, lv, tu, tv, axlabel) in enumerate([
            (learned_u[di][lm], learned_v[di][lm],
             truth_total_u[di][lm], truth_total_v[di][lm], "u"),
            (learned_v[di][lm], learned_v[di][lm],
             truth_total_v[di][lm], truth_total_v[di][lm], "v"),
        ]):
            ax = axes[di, col]
            if col == 0:
                ax.scatter(tu * 100, lu * 100, s=10, alpha=0.6)
                ax.set_xlabel("truth time-avg u (cm/s)")
                ax.set_ylabel("learned u (cm/s)")
            else:
                ax.scatter(tv * 100, lv * 100, s=10, alpha=0.6, color="tab:orange")
                ax.set_xlabel("truth time-avg v (cm/s)")
                ax.set_ylabel("learned v (cm/s)")
            xlim = max(abs(np.array([ax.get_xlim(), ax.get_ylim()])).max(), 1.0)
            ax.plot([-xlim, xlim], [-xlim, xlim], "k--", alpha=0.3,
                     label="y=x")
            ax.axhline(0, color="k", alpha=0.2)
            ax.axvline(0, color="k", alpha=0.2)
            ax.set_xlim(-xlim, xlim); ax.set_ylim(-xlim, xlim)
            ax.set_title(f"depth={depth:.1f}m  ({int(lm.sum())} learned cells)")
            ax.grid(alpha=0.3)
    fig.tight_layout()
    out = Path(__file__).parent / "figures" / "_diag_bias_vs_truth.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out}", flush=True)

    print("\n=== diagnostic complete ===", flush=True)


if __name__ == "__main__":
    main()
