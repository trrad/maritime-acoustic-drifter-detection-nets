"""JAX/XLA implementation of MPCStationKeeper's per-interval rollout.

Beam expansion + scoring + pruning stay numpy in the caller; each
decision interval (n_dt_per_interval × n_dyn_substeps of depth ramp,
field sample, advect, σ_pos evolution, planned-surface fusion) runs
inside one `lax.scan`-driven jit kernel. Beam shape changes per
interval (1 → K → ... → beam_width) so jit recompiles until the
beam plateaus; subsequent plans reuse the cache.

Public:
    `MpcRolloutBundle`: device-resident NEMO grid + bias-basis +
        process-noise scalars. Built once per keeper.
    `build_bundle(tf, basis, process_noise_cfg)`: bundle factory.
    `get_compiled_interval()`: returns the jit'd interval function;
        signature matches the `interval_fn` defined inside.
"""
from __future__ import annotations

import os
from typing import Any, NamedTuple

import numpy as np   # type: ignore[import-not-found]


EARTH_R_M = 111_320.0


# ---------- JAX lazy-load (see field_interp_jax.py rationale) ----------

_JAX = None


def _jax():
    global _JAX
    if _JAX is None:
        import jax   # type: ignore[import-not-found]
        import jax.numpy as jnp   # type: ignore[import-not-found]
        _JAX = (jax, jnp)
    return _JAX


# ---------- Device-resident bundles ----------

class MpcRolloutBundle(NamedTuple):
    """Static, device-resident parameters that don't change per plan
    (or even per drifter). Passed to `rollout_interval` as a pytree;
    NamedTuple is auto-registered with JAX so all fields are traced.

    Scalars stored as `jnp.float32` (or python floats — they'll be
    promoted on first use). `basis_n_cells` is the only static-shape
    value; it's a python int and used only in arithmetic, so JAX
    traces it as a 0-d int32 — works as long as we don't use it for
    shape construction inside the jitted code.
    """
    u_grid: Any   # jnp (n_slabs, T, Lat, Lon) f32
    v_grid: Any   # jnp (n_slabs, T, Lat, Lon) f32
    t_axis: Any   # jnp (T,) f32
    lat_axis: Any   # jnp (Lat,) f32
    lon_axis: Any   # jnp (Lon,) f32
    depth_keys: Any   # jnp (n_slabs,) f32 — sorted ascending
    basis_station_lat: Any   # f32 scalar
    basis_station_lon: Any
    basis_n_cells: Any   # int32 scalar
    basis_cell_size_m: Any
    basis_depth_centers: Any   # jnp (D,) f32
    pn_sigma_coh_sq: Any
    pn_sigma_plume_sq: Any
    pn_sigma_submeso_sq: Any
    pn_sigma_inertial_sq: Any
    pn_sigma_white_sq: Any
    pn_tau_coh: Any
    pn_tau_plume: Any
    pn_tau_submeso: Any
    pn_tau_inertial: Any
    pn_tau_white: Any
    pn_plume_base_m: Any
    pn_plume_width_m: Any
    pn_L_z_surf_m: Any
    pn_L_z_inertial_m: Any


_BUNDLE_CACHE: dict[tuple, "MpcRolloutBundle"] = {}
# Bound the per-worker bundle cache so sweeps where every drifter has
# a unique drop point (e.g., the mobility-map grid scan) don't
# accumulate one bundle per drifter and OOM the GPU. Each bundle holds
# the device-resident NEMO grid (~70-100 MiB) plus the basis/PN
# scalars; FIFO eviction keeps memory bounded while still letting
# campaign-mode cycles re-use the bundle for the same drifter station.
_BUNDLE_CACHE_MAX = int(os.environ.get("FLEET_BUNDLE_CACHE_MAX", "2"))


def _bundle_cache_key(truth_field: Any, basis: Any,
                       process_noise_cfg: Any) -> tuple:
    """Content-based cache key for `build_bundle`.

    Worker reuse: a multiprocessing worker handling multiple cycles for
    the same (or different) drifters builds many `Experiment` instances,
    each constructing a fresh `MPCStationKeeper` with its own private
    `_jax_bundle = None`. Without a cache, every keeper rebuilds the
    bundle and recompiles the inner kernel — accumulating JAX/XLA state
    on the GPU until the device runs out of memory at cycle 2-3.

    Keying on `id(truth_field)` is safe inside a worker because
    `_init_worker` puts the field in a module-level `_W` dict that
    persists for the worker's lifetime; identity is stable. Basis
    coordinates and depth set are content-keyed because new basis
    instances are constructed per `Experiment`. Process-noise config is
    content-keyed for the same reason.
    """
    pn = process_noise_cfg
    return (
        id(truth_field),
        float(basis.station_lat), float(basis.station_lon),
        int(basis.n_cells), float(basis.cell_size_m),
        tuple(float(d) for d in basis.depth_centers_m),
        # Process-noise content fingerprint.
        float(pn.sigma_coh_ms), float(pn.sigma_plume_ms),
        float(pn.sigma_submeso_ms), float(pn.sigma_inertial_ms),
        float(pn.sigma_white_ms),
        float(pn.tau_coh_sec), float(pn.tau_plume_sec),
        float(pn.tau_submeso_sec), float(pn.tau_inertial_sec),
        float(pn.tau_white_sec),
        float(pn.plume_base_m), float(pn.plume_width_m),
        float(pn.L_z_surf_m), float(pn.L_z_inertial_m),
    )


def build_bundle(
    truth_field: Any, basis: Any, process_noise_cfg: Any,
) -> MpcRolloutBundle:
    """Build the device-resident bundle from existing in-memory state.

    `truth_field`: a `truth_field.TruthField` instance.
    `basis`: a `bias_field.GridBiasBasis`.
    `process_noise_cfg`: a `process_noise.ProcessNoiseConfig`.

    Cached on a content key so cycles 1, 2, ... within a worker reuse
    the cycle-0 build instead of compiling fresh — this is what keeps
    long campaign-mode sweeps under the GPU memory ceiling.
    """
    key = _bundle_cache_key(truth_field, basis, process_noise_cfg)
    cached = _BUNDLE_CACHE.get(key)
    if cached is not None:
        return cached
    _, jnp = _jax()
    depth_keys = sorted(truth_field.interps.keys())
    u_slabs = []
    v_slabs = []
    for d in depth_keys:
        interp = truth_field.interps[float(d)]
        u_slabs.append(np.asarray(interp.u.values, dtype=np.float32))
        v_slabs.append(np.asarray(interp.v.values, dtype=np.float32))
    first = truth_field.interps[float(depth_keys[0])]
    f32 = lambda x: jnp.asarray(x, dtype=jnp.float32)
    bundle = MpcRolloutBundle(
        u_grid=jnp.stack([jnp.asarray(u) for u in u_slabs], axis=0),
        v_grid=jnp.stack([jnp.asarray(v) for v in v_slabs], axis=0),
        t_axis=jnp.asarray(np.asarray(first.u.grid[0], dtype=np.float32)),
        lat_axis=jnp.asarray(np.asarray(first.u.grid[1], dtype=np.float32)),
        lon_axis=jnp.asarray(np.asarray(first.u.grid[2], dtype=np.float32)),
        depth_keys=jnp.asarray(np.asarray(depth_keys, dtype=np.float32)),
        basis_station_lat=f32(basis.station_lat),
        basis_station_lon=f32(basis.station_lon),
        basis_n_cells=jnp.asarray(int(basis.n_cells), dtype=jnp.int32),
        basis_cell_size_m=f32(basis.cell_size_m),
        basis_depth_centers=jnp.asarray(
            np.asarray(basis.depth_centers_m, dtype=np.float32)
        ),
        pn_sigma_coh_sq=f32(process_noise_cfg.sigma_coh_ms ** 2),
        pn_sigma_plume_sq=f32(process_noise_cfg.sigma_plume_ms ** 2),
        pn_sigma_submeso_sq=f32(process_noise_cfg.sigma_submeso_ms ** 2),
        pn_sigma_inertial_sq=f32(process_noise_cfg.sigma_inertial_ms ** 2),
        pn_sigma_white_sq=f32(process_noise_cfg.sigma_white_ms ** 2),
        pn_tau_coh=f32(process_noise_cfg.tau_coh_sec),
        pn_tau_plume=f32(process_noise_cfg.tau_plume_sec),
        pn_tau_submeso=f32(process_noise_cfg.tau_submeso_sec),
        pn_tau_inertial=f32(process_noise_cfg.tau_inertial_sec),
        pn_tau_white=f32(process_noise_cfg.tau_white_sec),
        pn_plume_base_m=f32(process_noise_cfg.plume_base_m),
        pn_plume_width_m=f32(process_noise_cfg.plume_width_m),
        pn_L_z_surf_m=f32(process_noise_cfg.L_z_surf_m),
        pn_L_z_inertial_m=f32(process_noise_cfg.L_z_inertial_m),
    )
    if len(_BUNDLE_CACHE) >= _BUNDLE_CACHE_MAX:
        # Python 3.7+ dicts preserve insertion order; pop oldest.
        oldest_key = next(iter(_BUNDLE_CACHE))
        del _BUNDLE_CACHE[oldest_key]
    _BUNDLE_CACHE[key] = bundle
    return bundle


# ---------- Field sample (bilinear NEMO + bias gather) ----------

def _bilinear_nemo(bundle, t_q, lats, lons, depths):
    """Bilinear interp on (t, lat, lon) with nearest-slab depth snap.
    All inputs jnp arrays of compatible shapes (flat queries). Returns
    (u, v) flat arrays; out-of-bounds → NaN."""
    _, jnp = _jax()
    # Slab snap (nearest in depth_keys).
    slab_idx = jnp.argmin(
        jnp.abs(bundle.depth_keys[None, :] - depths[:, None]), axis=1,
    )

    def _bracket(axis, q):
        n = axis.shape[0]
        i_lo = jnp.clip(
            jnp.searchsorted(axis, q, side="right") - 1, 0, n - 2,
        )
        x_lo = axis[i_lo]
        x_hi = axis[i_lo + 1]
        frac = jnp.clip((q - x_lo) / jnp.maximum(x_hi - x_lo, 1e-30),
                          0.0, 1.0)
        in_b = (q >= axis[0]) & (q <= axis[-1])
        return i_lo, frac, in_b

    lat32 = lats.astype(bundle.t_axis.dtype)
    lon32 = lons.astype(bundle.t_axis.dtype)
    t_q32 = jnp.asarray(t_q, dtype=bundle.t_axis.dtype)

    ti, ft, in_t = _bracket(bundle.t_axis, t_q32)
    yi, fy, in_lat = _bracket(bundle.lat_axis, lat32)
    xi, fx, in_lon = _bracket(bundle.lon_axis, lon32)
    in_bounds = in_t & in_lat & in_lon

    def gather(grid, da, db, dc):
        return grid[slab_idx, ti + da, yi + db, xi + dc]

    w000 = (1 - ft) * (1 - fy) * (1 - fx)
    w001 = (1 - ft) * (1 - fy) * fx
    w010 = (1 - ft) * fy * (1 - fx)
    w011 = (1 - ft) * fy * fx
    w100 = ft * (1 - fy) * (1 - fx)
    w101 = ft * (1 - fy) * fx
    w110 = ft * fy * (1 - fx)
    w111 = ft * fy * fx

    u_g = bundle.u_grid
    v_g = bundle.v_grid
    u = (
        w000 * gather(u_g, 0, 0, 0) + w001 * gather(u_g, 0, 0, 1)
        + w010 * gather(u_g, 0, 1, 0) + w011 * gather(u_g, 0, 1, 1)
        + w100 * gather(u_g, 1, 0, 0) + w101 * gather(u_g, 1, 0, 1)
        + w110 * gather(u_g, 1, 1, 0) + w111 * gather(u_g, 1, 1, 1)
    )
    v = (
        w000 * gather(v_g, 0, 0, 0) + w001 * gather(v_g, 0, 0, 1)
        + w010 * gather(v_g, 0, 1, 0) + w011 * gather(v_g, 0, 1, 1)
        + w100 * gather(v_g, 1, 0, 0) + w101 * gather(v_g, 1, 0, 1)
        + w110 * gather(v_g, 1, 1, 0) + w111 * gather(v_g, 1, 1, 1)
    )
    nan = jnp.asarray(float("nan"), dtype=u.dtype)
    return jnp.where(in_bounds, u, nan), jnp.where(in_bounds, v, nan)


def _bias_lookup(bundle, draws_u, draws_v, lats, lons, depths,
                  draw_idx_per_query):
    """Per-query bias correction. Inputs:
      `draws_u/v` jnp shape (n_eval, D, Y, X)
      `lats/lons/depths` jnp flat shape (M,)
      `draw_idx_per_query` jnp (M,) int32 — which draw index to gather
    Returns (bias_u, bias_v, inside) all (M,)."""
    _, jnp = _jax()
    di = jnp.argmin(
        jnp.abs(bundle.basis_depth_centers[None, :] - depths[:, None]),
        axis=1,
    )
    cos_lat = jnp.cos(jnp.deg2rad(bundle.basis_station_lat))
    dy_m = (lats - bundle.basis_station_lat) * EARTH_R_M
    dx_m = (lons - bundle.basis_station_lon) * EARTH_R_M * cos_lat
    half = 0.5 * bundle.basis_n_cells * bundle.basis_cell_size_m
    inside = (jnp.abs(dx_m) < half) & (jnp.abs(dy_m) < half)
    yi = jnp.clip(
        ((dy_m + half) / bundle.basis_cell_size_m).astype(jnp.int32),
        0, bundle.basis_n_cells - 1,
    )
    xi = jnp.clip(
        ((dx_m + half) / bundle.basis_cell_size_m).astype(jnp.int32),
        0, bundle.basis_n_cells - 1,
    )
    bias_u = draws_u[draw_idx_per_query, di, yi, xi]
    bias_v = draws_v[draw_idx_per_query, di, yi, xi]
    return bias_u, bias_v, inside


# ---------- σ_pos² OU growth (jnp port of process_noise.sigma_pos_growth_rate_per_axis_vec) ----------

def _sigma_growth_rate(bundle, depth_arr, t_anchor_arr):
    """Per-axis σ_pos² growth rate (m²/s) at given depth + time-since-
    anchor. Mirrors process_noise.sigma_pos_growth_rate_per_axis_vec."""
    _, jnp = _jax()
    z = jnp.maximum(depth_arr, 0.0)
    p_z = 0.5 * (1.0 - jnp.tanh(
        (z - bundle.pn_plume_base_m) /
        jnp.maximum(bundle.pn_plume_width_m, 0.1)
    ))
    s_z = jnp.exp(-z / jnp.maximum(bundle.pn_L_z_surf_m, 1e-6))
    i_z = jnp.exp(-z / jnp.maximum(bundle.pn_L_z_inertial_m, 1e-6))

    def rate(sigma_sq, tau_sec):
        return sigma_sq * 2.0 * tau_sec * (
            1.0 - jnp.exp(-t_anchor_arr / tau_sec)
        )

    return (
        rate(bundle.pn_sigma_coh_sq, bundle.pn_tau_coh)
        + rate((p_z * jnp.sqrt(bundle.pn_sigma_plume_sq)) ** 2,
                bundle.pn_tau_plume)
        + rate((s_z * jnp.sqrt(bundle.pn_sigma_submeso_sq)) ** 2,
                bundle.pn_tau_submeso)
        + rate((i_z * jnp.sqrt(bundle.pn_sigma_inertial_sq)) ** 2,
                bundle.pn_tau_inertial)
        + rate(bundle.pn_sigma_white_sq, bundle.pn_tau_white)
    )


# ---------- Substep + interval rollout ----------

def _make_rollout_interval():
    """Build the JIT'd rollout_interval closure. Imported at first
    call; the inner functions reference jnp from _jax()."""
    jax, jnp = _jax()

    def substep(carry, sub_idx, *, bundle, setpoints, t0, draws_u, draws_v,
                  sub_dt, dz_per_substep, station_lat, station_lon,
                  cos_station, sigma_lora_sq, next_surface_t,
                  hazard_rate):
        state = carry
        # Depth ramp toward setpoint.
        dz = setpoints[:, None] - state['depths']
        abs_dz = jnp.abs(dz)
        step_dz = jnp.where(
            abs_dz <= dz_per_substep, dz,
            jnp.sign(dz) * dz_per_substep,
        )
        new_depths = state['depths'] + step_dz

        # Field sample with bias.
        BK = state['lats'].shape[0]
        n_eval = state['lats'].shape[1]
        flat_lats = state['lats'].reshape(-1)
        flat_lons = state['lons'].reshape(-1)
        flat_depths = new_depths.reshape(-1)
        # draw_idx tile: each row maps each col j to draw index j.
        draw_idx = jnp.tile(jnp.arange(n_eval, dtype=jnp.int32), BK)
        t_mid = t0 + (sub_idx.astype(jnp.float32) + 0.5) * sub_dt

        u_flat, v_flat = _bilinear_nemo(
            bundle, t_mid, flat_lats, flat_lons, flat_depths,
        )
        bu, bv, inside = _bias_lookup(
            bundle, draws_u, draws_v,
            flat_lats, flat_lons, flat_depths, draw_idx,
        )
        u_flat = jnp.where(inside, u_flat + bu, u_flat)
        v_flat = jnp.where(inside, v_flat + bv, v_flat)
        u = u_flat.reshape(BK, n_eval)
        v = v_flat.reshape(BK, n_eval)

        bad = ~(jnp.isfinite(u) & jnp.isfinite(v))
        new_alive = state['alive'] & ~bad
        u = jnp.where(bad, 0.0, u)
        v = jnp.where(bad, 0.0, v)

        # Advect.
        cos_lat_e = jnp.cos(jnp.deg2rad(state['lats']))
        new_lats = state['lats'] + (v * sub_dt) / EARTH_R_M
        new_lons = state['lons'] + (u * sub_dt) / (EARTH_R_M * cos_lat_e)

        # σ_pos² OU growth (per-beam).
        t_mid_anchor = state['t_since_anchor'] + 0.5 * sub_dt
        rate = _sigma_growth_rate(bundle, setpoints, t_mid_anchor)
        new_sigma = state['sigma_pos_sq'] + rate[:, None] * sub_dt
        # Hazard sink (no-op when hazard_rate=0).
        new_sigma = (
            new_sigma - hazard_rate * sub_dt
            * (new_sigma - sigma_lora_sq)
        )
        new_t_since_anchor = state['t_since_anchor'] + sub_dt

        # Planned-surface Kalman fusion at next_surface_t.
        substep_end_t = t0 + (sub_idx.astype(jnp.float32) + 1.0) * sub_dt
        fire = (substep_end_t >= next_surface_t) & (~state['applied_lora'])
        fused_sigma = (new_sigma * sigma_lora_sq) / jnp.maximum(
            new_sigma + sigma_lora_sq, 1e-12,
        )
        new_sigma = jnp.where(fire[:, None], fused_sigma, new_sigma)
        new_t_since_anchor = jnp.where(
            fire, jnp.float32(0.0), new_t_since_anchor,
        )
        new_applied_lora = state['applied_lora'] | fire

        new_state = {
            'lats': new_lats, 'lons': new_lons, 'depths': new_depths,
            'sigma_pos_sq': new_sigma,
            'd_sq_sum': state['d_sq_sum'],   # accumulated only at tick boundaries
            'alive': new_alive,
            't_since_anchor': new_t_since_anchor,
            'applied_lora': new_applied_lora,
        }
        return new_state, None

    def tick_body(carry, tick_idx, *, n_substeps, **kwargs):
        # Run n_substeps substeps. sub_idx counts within this tick;
        # absolute t for substep is t0 + (tick * n_substeps + sub_idx) * sub_dt.
        # Easier: we pass the global substep offset via t0.
        def step_sub(c, sub_idx):
            return substep(c, sub_idx, **kwargs)
        state, _ = jax.lax.scan(
            step_sub, carry, jnp.arange(n_substeps, dtype=jnp.int32),
        )
        # Accumulate d² at end of tick.
        d_lat_m = (state['lats'] - kwargs['station_lat']) * EARTH_R_M
        d_lon_m = ((state['lons'] - kwargs['station_lon'])
                   * EARTH_R_M * kwargs['cos_station'])
        d_sq = d_lat_m ** 2 + d_lon_m ** 2
        state = {**state, 'd_sq_sum': state['d_sq_sum'] + d_sq}
        return state, None

    def interval_fn(
        state, *, bundle, setpoints, t0, draws_u, draws_v,
        n_ticks, n_substeps, sub_dt, dz_per_substep,
        station_lat, station_lon, cos_station,
        sigma_lora_sq, next_surface_t, hazard_rate,
    ):
        """Roll forward one decision interval (n_ticks × n_substeps).

        `n_ticks` is a RUNTIME argument here (not a JIT static arg) so
        that varying decision-cadences across cells in a sweep don't
        force a per-cadence kernel recompile. Each compiled
        specialization can hold ~1 GB of GPU state; on a 12-worker
        pool, even two cached specializations can exceed the GPU's
        VRAM. Using `lax.fori_loop` (rather than `lax.scan` over
        `jnp.arange(n_ticks)`) keeps the loop dynamic and produces
        a single specialization that handles any n_ticks value.

        `n_substeps` stays static — it's always 10 in the calling
        path and the inner substep scan benefits from XLA unrolling.
        """
        kwargs = dict(
            bundle=bundle, setpoints=setpoints, draws_u=draws_u,
            draws_v=draws_v, sub_dt=sub_dt,
            dz_per_substep=dz_per_substep,
            station_lat=station_lat, station_lon=station_lon,
            cos_station=cos_station, sigma_lora_sq=sigma_lora_sq,
            next_surface_t=next_surface_t, hazard_rate=hazard_rate,
        )
        # fori_loop body: takes (i, carry) → carry. Per-tick t0 offset
        # computed from i; substep scan stays static-shape (n_substeps).
        def tick_step(tick_idx, carry):
            tick_t0 = t0 + tick_idx.astype(jnp.float32) * (n_substeps * sub_dt)
            tick_kwargs = {**kwargs, 't0': tick_t0,
                           'n_substeps': n_substeps}
            new_carry, _ = tick_body(carry, tick_idx, **tick_kwargs)
            return new_carry

        return jax.lax.fori_loop(0, n_ticks, tick_step, state)

    return jax.jit(
        interval_fn,
        # n_ticks is now runtime; only n_substeps stays static.
        static_argnames=("n_substeps",),
    )


_compiled_interval = None


def get_compiled_interval():
    global _compiled_interval
    if _compiled_interval is None:
        _compiled_interval = _make_rollout_interval()
    return _compiled_interval


# ---------- numpy → jnp / jnp → numpy state conversion ----------

def state_to_jnp(state_np: dict) -> dict:
    _, jnp = _jax()
    return {
        'lats': jnp.asarray(state_np['lats'], dtype=jnp.float32),
        'lons': jnp.asarray(state_np['lons'], dtype=jnp.float32),
        'depths': jnp.asarray(state_np['depths'], dtype=jnp.float32),
        'sigma_pos_sq': jnp.asarray(state_np['sigma_pos_sq'],
                                      dtype=jnp.float32),
        'd_sq_sum': jnp.asarray(state_np['d_sq_sum'], dtype=jnp.float32),
        'alive': jnp.asarray(state_np['alive'], dtype=jnp.bool_),
        't_since_anchor': jnp.asarray(state_np['t_since_anchor'],
                                        dtype=jnp.float32),
        'applied_lora': jnp.asarray(state_np['applied_lora'],
                                      dtype=jnp.bool_),
    }


def state_to_np(state_jnp: dict) -> dict:
    return {k: np.asarray(v) for k, v in state_jnp.items()}
