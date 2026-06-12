"""Parity test: numpy MPC rollout vs JAX rollout.

Caches expensive init state to disk so iteration is fast:
  /tmp/_mpc_parity_fixture.pkl
    {
        nemo, basis, pn_cfg,            # for building bundle
        state_np, setpoints, draws_u,   # rollout inputs
        draws_v, t0, sub_dt, ...
        out_np                          # numpy reference output
    }
First run: builds and pickles (~4-5 min including NEMO load + numpy
rollout). Subsequent runs: load fixture (< 1s), then run JAX. Re-build
the fixture by deleting the pickle, or pass --rebuild.

Usage:
    python _test_mpc_rollout_jax_parity.py             # use cached fixture
    python _test_mpc_rollout_jax_parity.py --rebuild   # force rebuild

Tolerance: float32 ULP-level diffs over a 10-substep rollout —
positions ~1e-4 deg, σ_pos² ~1 m², d² ~5000 m² (relative ~0.02%).
`alive` and `applied_lora` masks must match exactly (control flow).
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time

import numpy as np   # type: ignore[import-not-found]


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FIXTURE_PATH = "/tmp/_mpc_parity_fixture.pkl"


# ---------- numpy reference rollout (faithful per-substep) ----------

def _numpy_substep(state, sub_idx, setpoints, t0, draws_u, draws_v,
                    bundle_params):
    p = bundle_params
    dz = setpoints[:, None] - state['depths']
    abs_dz = np.abs(dz)
    step_dz = np.where(
        abs_dz <= p['dz_per_substep'], dz,
        np.sign(dz) * p['dz_per_substep'],
    )
    new_depths = state['depths'] + step_dz

    BK = state['lats'].shape[0]
    n_eval = state['lats'].shape[1]
    flat_lats = state['lats'].reshape(-1)
    flat_lons = state['lons'].reshape(-1)
    flat_depths = new_depths.reshape(-1)
    draw_idx = np.tile(np.arange(n_eval), BK)
    t_mid = t0 + (sub_idx + 0.5) * p['sub_dt']

    u_flat, v_flat = p['truth_field'].sample_batched(
        flat_lats, flat_lons, flat_depths, t_mid,
    )

    di = np.argmin(
        np.abs(p['basis_depth_centers'][None, :] - flat_depths[:, None]),
        axis=1,
    )
    cos_lat = float(np.cos(np.deg2rad(p['basis_station_lat'])))
    dy_m = (flat_lats - p['basis_station_lat']) * 111_320.0
    dx_m = (flat_lons - p['basis_station_lon']) * 111_320.0 * cos_lat
    half = 0.5 * p['basis_n_cells'] * p['basis_cell_size_m']
    inside = (np.abs(dx_m) < half) & (np.abs(dy_m) < half)
    yi = np.clip(((dy_m + half) / p['basis_cell_size_m']).astype(int),
                 0, p['basis_n_cells'] - 1)
    xi = np.clip(((dx_m + half) / p['basis_cell_size_m']).astype(int),
                 0, p['basis_n_cells'] - 1)
    bu = draws_u[draw_idx, di, yi, xi]
    bv = draws_v[draw_idx, di, yi, xi]
    u_flat = np.where(inside, u_flat + bu, u_flat)
    v_flat = np.where(inside, v_flat + bv, v_flat)
    u = u_flat.reshape(BK, n_eval)
    v = v_flat.reshape(BK, n_eval)

    bad = ~(np.isfinite(u) & np.isfinite(v))
    new_alive = state['alive'] & ~bad
    u = np.where(bad, 0.0, u)
    v = np.where(bad, 0.0, v)

    cos_lat_e = np.cos(np.deg2rad(state['lats']))
    new_lats = state['lats'] + (v * p['sub_dt']) / 111_320.0
    new_lons = state['lons'] + (u * p['sub_dt']) / (111_320.0 * cos_lat_e)

    from process_noise import sigma_pos_growth_rate_per_axis_vec   # type: ignore

    t_mid_anchor = state['t_since_anchor'] + 0.5 * p['sub_dt']
    rate = sigma_pos_growth_rate_per_axis_vec(
        setpoints, t_mid_anchor, p['process_noise_cfg'],
    )
    new_sigma = state['sigma_pos_sq'] + rate[:, None] * p['sub_dt']
    new_sigma = (
        new_sigma - p['hazard_rate'] * p['sub_dt']
        * (new_sigma - p['sigma_lora_sq'])
    )
    new_t_since_anchor = state['t_since_anchor'] + p['sub_dt']

    substep_end_t = t0 + (sub_idx + 1) * p['sub_dt']
    fire = (substep_end_t >= p['next_surface_t']) & (~state['applied_lora'])
    fused_sigma = (new_sigma * p['sigma_lora_sq']) / np.maximum(
        new_sigma + p['sigma_lora_sq'], 1e-12,
    )
    new_sigma = np.where(fire[:, None], fused_sigma, new_sigma)
    new_t_since_anchor = np.where(fire, 0.0, new_t_since_anchor)
    new_applied_lora = state['applied_lora'] | fire

    return {
        'lats': new_lats, 'lons': new_lons, 'depths': new_depths,
        'sigma_pos_sq': new_sigma,
        'd_sq_sum': state['d_sq_sum'],
        'alive': new_alive,
        't_since_anchor': new_t_since_anchor,
        'applied_lora': new_applied_lora,
    }


def _numpy_interval(state, n_ticks, n_substeps, setpoints, t0,
                     draws_u, draws_v, bundle_params):
    p = bundle_params
    for tick_idx in range(n_ticks):
        tick_t0 = t0 + tick_idx * (n_substeps * p['sub_dt'])
        for sub_idx in range(n_substeps):
            state = _numpy_substep(
                state, sub_idx, setpoints, tick_t0,
                draws_u, draws_v, bundle_params,
            )
        d_lat_m = (state['lats'] - p['station_lat']) * 111_320.0
        d_lon_m = ((state['lons'] - p['station_lon'])
                   * 111_320.0 * p['cos_station'])
        d_sq = d_lat_m ** 2 + d_lon_m ** 2
        state = {**state, 'd_sq_sum': state['d_sq_sum'] + d_sq}
    return state


def _build_fixture():
    import _fleet_sim_v0 as fs
    from process_noise import ProcessNoiseConfig
    from rbpf_prototype.bias_field import GridBiasBasis   # type: ignore

    print("init worker (NEMO + tracer + noise)...", flush=True)
    t = time.time()
    fs._init_worker()
    print(f"  init done ({time.time() - t:.1f}s)", flush=True)

    tf = fs._W["nemo"]
    s_lat, s_lon = 49.375, -123.71
    basis = GridBiasBasis(
        station_lat=s_lat, station_lon=s_lon,
        depth_centers_m=tuple(sorted(tf.interps.keys())),
        n_cells=8, cell_size_m=2000.0,
    )
    pn_cfg = ProcessNoiseConfig()

    rng = np.random.default_rng(0)
    # BK = 800 = saturated beam size during a real plan
    # (beam_width=200 × K_depths=4 expanded candidates per interval).
    # Override via env to compare scaling.
    BK = int(os.environ.get("PARITY_BK", "800"))
    n_eval = int(os.environ.get("PARITY_N_EVAL", "5"))
    D = len(basis.depth_centers_m)
    Y = X = basis.n_cells

    state_np = {
        'lats': (s_lat + rng.normal(0, 0.001, size=(BK, n_eval))).astype(np.float32),
        'lons': (s_lon + rng.normal(0, 0.001, size=(BK, n_eval))).astype(np.float32),
        'depths': np.full((BK, n_eval), 10.0, dtype=np.float32),
        'sigma_pos_sq': np.full((BK, n_eval), 50.0 ** 2, dtype=np.float32),
        'd_sq_sum': np.zeros((BK, n_eval), dtype=np.float32),
        'alive': np.ones((BK, n_eval), dtype=bool),
        't_since_anchor': np.full((BK,), 600.0, dtype=np.float32),
        'applied_lora': np.zeros((BK,), dtype=bool),
    }
    setpoints = rng.choice(np.array([5.0, 10.0, 20.0, 50.0]),
                            size=BK).astype(np.float32)
    draws_u = rng.normal(0, 0.05, size=(n_eval, D, Y, X)).astype(np.float32)
    draws_v = rng.normal(0, 0.05, size=(n_eval, D, Y, X)).astype(np.float32)

    rollout_kwargs = dict(
        n_ticks=1, n_substeps=10, sub_dt=60.0,
        dz_per_substep=0.1 * 60.0,
        station_lat=s_lat, station_lon=s_lon,
        cos_station=float(np.cos(np.deg2rad(s_lat))),
        sigma_lora_sq=20.0 ** 2,
        next_surface_t=12 * 3600.0 + 30 * 60.0,
        hazard_rate=0.0,
    )
    t0 = 12 * 3600.0
    bundle_params_np = {
        'truth_field': tf,
        'basis_depth_centers': np.asarray(basis.depth_centers_m, dtype=np.float32),
        'basis_station_lat': basis.station_lat,
        'basis_station_lon': basis.station_lon,
        'basis_n_cells': basis.n_cells,
        'basis_cell_size_m': basis.cell_size_m,
        'process_noise_cfg': pn_cfg,
        **rollout_kwargs,
    }

    print("computing numpy reference...", flush=True)
    # Run twice: first to warm scipy RGI / numpy caches, second for timing.
    _numpy_interval(
        state_np, rollout_kwargs['n_ticks'], rollout_kwargs['n_substeps'],
        setpoints, t0, draws_u, draws_v, bundle_params_np,
    )
    t = time.time()
    out_np = _numpy_interval(
        state_np, rollout_kwargs['n_ticks'], rollout_kwargs['n_substeps'],
        setpoints, t0, draws_u, draws_v, bundle_params_np,
    )
    np_wall_ms = (time.time() - t) * 1000
    print(f"  numpy wall: {np_wall_ms:.1f}ms", flush=True)

    # Save EVERYTHING the JAX side will need + the reference output.
    # tf is not picklable (RGI is fine but xarray-derived state may be).
    # Save tf itself; if the pickle barfs on RGI we'll fix it.
    fixture = {
        "tf": tf,
        "basis": basis,
        "pn_cfg": pn_cfg,
        "state_np": state_np,
        "setpoints": setpoints,
        "draws_u": draws_u,
        "draws_v": draws_v,
        "t0": t0,
        "rollout_kwargs": rollout_kwargs,
        "out_np": out_np,
        "np_wall_ms": np_wall_ms,
    }
    with open(FIXTURE_PATH, "wb") as f:
        pickle.dump(fixture, f)
    print(f"  saved fixture: {FIXTURE_PATH}", flush=True)
    return fixture


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    if args.rebuild or not os.path.exists(FIXTURE_PATH):
        fixture = _build_fixture()
    else:
        print(f"loading fixture: {FIXTURE_PATH}", flush=True)
        t = time.time()
        with open(FIXTURE_PATH, "rb") as f:
            fixture = pickle.load(f)
        print(f"  loaded ({time.time() - t:.2f}s)", flush=True)

    import mpc_rollout_jax as mr
    import jax
    import jax.numpy as jnp

    print(f"\njax devices: {jax.devices()}", flush=True)
    print(f"default backend: {jax.default_backend()}", flush=True)

    print("\nbuilding device-resident bundle...", flush=True)
    t = time.time()
    bundle = mr.build_bundle(fixture["tf"], fixture["basis"], fixture["pn_cfg"])
    # Force materialization of jnp arrays on device.
    _ = bundle.u_grid.block_until_ready()
    print(f"  built ({time.time() - t:.2f}s)", flush=True)

    state_j = mr.state_to_jnp(fixture["state_np"])
    setpoints_j = jnp.asarray(fixture["setpoints"])
    draws_u_j = jnp.asarray(fixture["draws_u"])
    draws_v_j = jnp.asarray(fixture["draws_v"])
    rk = fixture["rollout_kwargs"]
    interval_fn = mr.get_compiled_interval()

    def run():
        return interval_fn(
            state_j, bundle=bundle, setpoints=setpoints_j,
            t0=float(fixture["t0"]),
            draws_u=draws_u_j, draws_v=draws_v_j,
            n_ticks=rk['n_ticks'], n_substeps=rk['n_substeps'],
            sub_dt=float(rk['sub_dt']),
            dz_per_substep=float(rk['dz_per_substep']),
            station_lat=float(rk['station_lat']),
            station_lon=float(rk['station_lon']),
            cos_station=float(rk['cos_station']),
            sigma_lora_sq=float(rk['sigma_lora_sq']),
            next_surface_t=float(rk['next_surface_t']),
            hazard_rate=float(rk['hazard_rate']),
        )

    print("\n--- jax rollout (first call: jit compile) ---", flush=True)
    t = time.time()
    out_j = run()
    jax.tree_util.tree_map(lambda x: x.block_until_ready(), out_j)
    print(f"  compile + first run: {(time.time() - t) * 1000:.1f}ms",
          flush=True)

    # Compiled-only timing.
    K_REPS = 50
    t = time.time()
    for _ in range(K_REPS):
        out_j_n = run()
    jax.tree_util.tree_map(lambda x: x.block_until_ready(), out_j_n)
    j_wall = (time.time() - t) / K_REPS
    print(f"  compiled wall (avg over {K_REPS}): {j_wall * 1000:.2f}ms",
          flush=True)

    out_j_np = mr.state_to_np(out_j)
    out_np = fixture["out_np"]

    print("\n--- parity ---", flush=True)
    fail = False
    tol = {
        'lats': 5e-4, 'lons': 5e-4, 'depths': 5e-3,
        'sigma_pos_sq': 1.0,
        'd_sq_sum': 5000.0,
        't_since_anchor': 1e-3,
    }
    for k in ['lats', 'lons', 'depths', 'sigma_pos_sq', 'd_sq_sum',
              't_since_anchor']:
        a = out_np[k]
        b = out_j_np[k]
        finite = np.isfinite(a) & np.isfinite(b)
        if not finite.any():
            continue
        diff = np.abs(a[finite] - b[finite])
        max_d = float(diff.max())
        passed = max_d <= tol[k]
        flag = "PASS" if passed else "FAIL"
        print(f"  {k}: max={max_d:.6g}, p95={np.percentile(diff, 95):.6g}  [{flag} tol={tol[k]:g}]")
        if not passed:
            fail = True
    for k in ['alive', 'applied_lora']:
        a = out_np[k]
        b = out_j_np[k]
        match = bool((a == b).all())
        print(f"  {k}: all-match={match}  [{'PASS' if match else 'FAIL'}]")
        if not match:
            fail = True

    if fail:
        sys.exit(1)
    print("\n  PARITY PASS", flush=True)
    np_wall_ms = float(fixture["np_wall_ms"])
    print(f"  numpy wall: {np_wall_ms:.1f}ms; jax compiled: "
          f"{j_wall * 1000:.2f}ms = {np_wall_ms / (j_wall * 1000):.1f}x speedup",
          flush=True)


if __name__ == "__main__":
    main()
