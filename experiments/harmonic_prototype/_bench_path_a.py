"""Path-A profile harness: single-process, one drifter, 24h.

Two passes:
  1. CPU baseline   (FLEET_USE_JAX_MPC unset)
  2. Path-A         (FLEET_USE_JAX_MPC=1)

For each pass: sets up world (NEMO + noise + tracer + bathy), instruments
key functions via monkey-patch wrappers that accumulate (n_calls, total_s)
into a `_PHASE_T` dict, runs `run_one_station` for 24h, prints the phase
breakdown.  Also runs a second iteration after instrumentation warmup so
the per-call mean excludes the JAX first-call compile.

Usage:
    python _bench_path_a.py                # both passes
    python _bench_path_a.py --jax-only     # path-A pass only
    python _bench_path_a.py --cpu-only     # CPU baseline only
    python _bench_path_a.py --cprofile     # also print cProfile top-40

Wall is reported in seconds; phase totals as % of mission wall.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import sys
import time
from collections import defaultdict
from contextlib import contextmanager

import numpy as np


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]
STATION = (49.3533, -123.7411, 289)
RUN_HOURS = int(os.environ.get("BENCH_RUN_HOURS", "24"))
SEED = 1000
LORA_SIGMA_M = 20.0


# ---------- world cache (shared across passes) ----------

_W: dict = {}


def _init_world() -> None:
    if _W:
        return
    from salishseacast_cache import (
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import (
        build_layered_noise_field, build_layered_tracer_noise_field,
    )
    from truth_field import build_tracer_field, build_truth_field

    print("[init] loading NEMO + noise...", flush=True)
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
    _W.update(dict(
        nemo=nemo, tracer=tracer, noise=noise, tracer_noise=tracer_noise,
        bathy_grid=bathy_grid,
    ))
    print(f"[init] world ready ({time.time() - t0:.1f}s)", flush=True)


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
    def get_current_at(self, *a): return self.sample(*a)
    def get_current_at_batched(self, *a): return self.sample_batched(*a)


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


def _make_experiment():
    from rbpf_prototype import (
        BiasConfig, CTDSensor, Experiment,
        LoRaRangeSensor, PFConfig, SensorConfig,
        SimConfig, StationConfig,
    )
    from rbpf_prototype.surfacing import FixedIntervalPolicy
    from truth_field import EARTH_R_M as ER

    nemo = _W["nemo"]; tracer = _W["tracer"]
    noise = _W["noise"]; tracer_noise = _W["tracer_noise"]
    bathy_grid = _W["bathy_grid"]

    s_lat_target, s_lon_target, _ = STATION
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
        (s_lat + dn * 1000.0 / ER,
         s_lon + de * 1000.0 / (ER * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]
    sim = SimConfig(
        run_hours=RUN_HOURS, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model="ou_integrated",
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)
    sensor_cfg = SensorConfig(
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=LORA_SIGMA_M,
                              max_depth_m=1.0),
        flow=None, ctd=CTDSensor(),
    )
    bias_cfg = BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2)),
    )
    real = _RealCurrents(nemo, noise)
    nemo_prior = _NemoPrior(nemo)
    real_tracer = _RealTracer(tracer, tracer_noise)
    surfacing = FixedIntervalPolicy(period_h=6.0)
    return Experiment(
        station=station, sim=sim, sensor=sensor_cfg, pf_cfg=pf_cfg,
        truth=real, prior=nemo_prior, surfacing=surfacing,
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )


# ---------- phase timing instrumentation ----------

class PhaseTimer:
    """Wraps callables with (n_calls, total_s) accounting."""
    def __init__(self):
        self.stats: dict[str, list[float]] = defaultdict(
            lambda: [0, 0.0]
        )
        self._patches: list[tuple] = []

    def wrap(self, obj, attr: str, label: str):
        orig = getattr(obj, attr)

        def wrapped(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return orig(*args, **kwargs)
            finally:
                dt = time.perf_counter() - t0
                self.stats[label][0] += 1
                self.stats[label][1] += dt
        setattr(obj, attr, wrapped)
        self._patches.append((obj, attr, orig))
        return wrapped

    def restore(self):
        for obj, attr, orig in self._patches:
            setattr(obj, attr, orig)
        self._patches.clear()

    @contextmanager
    def section(self, label: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self.stats[label][0] += 1
            self.stats[label][1] += dt

    def report(self, total_wall: float, header: str = "phase breakdown",
                umbrella: tuple[str, ...] = ()):
        """Sums inside `umbrella` phases double-count their wrapped callees;
        flag them as such and only count leaf wrappers toward 'accounted'.
        """
        rows = sorted(self.stats.items(), key=lambda kv: -kv[1][1])
        print(f"\n=== {header} (mission wall {total_wall:.1f}s) ===")
        print(f"{'phase':<42} {'calls':>8} {'total_s':>10} "
              f"{'pct':>6} {'mean_ms':>10}  note")
        accounted = 0.0
        for label, (n, total) in rows:
            if n == 0:
                continue
            mean_ms = 1000.0 * total / n
            pct = 100.0 * total / total_wall if total_wall > 0 else 0.0
            tag = "(umbrella)" if label in umbrella else ""
            print(f"{label:<42} {n:>8d} {total:>10.3f} "
                  f"{pct:>5.1f}% {mean_ms:>9.3f}  {tag}")
            if label not in umbrella:
                accounted += total
        unaccounted = total_wall - accounted
        upct = 100.0 * unaccounted / total_wall if total_wall > 0 else 0.0
        print(f"{'(unaccounted leaf time)':<42} "
              f"{'':>8} {unaccounted:>10.3f} {upct:>5.1f}%")


def _wrap_run(exp, timer: PhaseTimer):
    """Patch the hot-path callables on the Experiment + its components.

    Uses a tick_recorder to demarcate per-tick boundaries; phase totals
    are accumulated via wrapped methods on:
      - keeper.choose_depth         (MPC plan)
      - pf.predict                  (PF predict)
      - pf.reweight                 (PF reweight)
      - pf.maybe_resample           (PF resample)
      - pf.sample_currents_at_particles  (per-particle current eval)
      - pf.posterior_std_m / cov_m  (covariance bookkeeping)
      - bias.lookup_and_accumulate
      - bias.accumulate_prior_disp
      - bias.kalman_update_leg
      - bias.kalman_update_tracer_offset
      - bias.ou_evolve / ou_evolve_tracer_offset
      - bias.gather
      - LiveBiasKnowledge.precompute_for_decision
      - LiveBiasKnowledge.precompute_posterior_draws
      - exp.truth.sample            (truth-current eval at dynamics step)
      - ballast_dynamics.step       (truth advance)
      - lora trilaterate

    Patches are applied lazily, after the first call where a fresh
    `pf` / `bias` / `keeper` / `LiveBiasKnowledge` exists.
    """
    import ballast_dynamics
    import ballast_controller
    timer.wrap(ballast_dynamics, "step", "truth.step")

    # We can't directly wrap pf/bias/keeper before run_one_station builds
    # them, so we hook in via a tick_recorder that patches on first call.
    from rbpf_prototype.experiment import LiveBiasKnowledge  # noqa
    import rbpf_prototype.experiment as _exp_mod
    _orig_tri = _exp_mod.trilaterate_lora

    def _tri_wrapped(*a, **k):
        t0 = time.perf_counter()
        try:
            return _orig_tri(*a, **k)
        finally:
            dt = time.perf_counter() - t0
            timer.stats["lora.trilaterate"][0] += 1
            timer.stats["lora.trilaterate"][1] += dt
    _exp_mod.trilaterate_lora = _tri_wrapped

    # Patch LiveBiasKnowledge methods at class level (not instance);
    # all instances created after this see the wrappers.
    timer.wrap(LiveBiasKnowledge, "precompute_for_decision",
                "knowledge.precompute_for_decision")
    timer.wrap(LiveBiasKnowledge, "precompute_posterior_draws",
                "knowledge.precompute_posterior_draws")
    timer.wrap(LiveBiasKnowledge, "get_current_at_batched",
                "knowledge.get_current_at_batched")
    timer.wrap(LiveBiasKnowledge, "get_current_at_batched_draw",
                "knowledge.get_current_at_batched_draw")

    # MPC keeper
    timer.wrap(ballast_controller.MPCStationKeeper, "choose_depth",
                "mpc.choose_depth")

    # PositionRBPF + BiasFieldState
    from rbpf_prototype.rbpf import PositionRBPF
    from rbpf_prototype.bias_field import BiasFieldState
    timer.wrap(PositionRBPF, "predict", "pf.predict")
    timer.wrap(PositionRBPF, "reweight", "pf.reweight")
    timer.wrap(PositionRBPF, "maybe_resample", "pf.maybe_resample")
    timer.wrap(PositionRBPF, "sample_currents_at_particles",
                "pf.sample_currents")
    timer.wrap(PositionRBPF, "posterior_std_m", "pf.posterior_std_m")
    timer.wrap(PositionRBPF, "cov_m", "pf.cov_m")

    timer.wrap(BiasFieldState, "lookup_and_accumulate",
                "bias.lookup_and_accumulate")
    timer.wrap(BiasFieldState, "accumulate_prior_disp",
                "bias.accumulate_prior_disp")
    timer.wrap(BiasFieldState, "kalman_update_leg",
                "bias.kalman_update_leg")
    timer.wrap(BiasFieldState, "kalman_update_tracer_offset",
                "bias.kalman_update_tracer_offset")
    timer.wrap(BiasFieldState, "ou_evolve", "bias.ou_evolve")
    timer.wrap(BiasFieldState, "ou_evolve_tracer_offset",
                "bias.ou_evolve_tracer_offset")
    timer.wrap(BiasFieldState, "gather", "bias.gather")

    # Sensors
    from rbpf_prototype.sensors import CTDSensor, LoRaRangeSensor
    timer.wrap(LoRaRangeSensor, "log_likelihood_per_particle",
                "sensors.lora.logL")
    timer.wrap(LoRaRangeSensor, "sample", "sensors.lora.sample")
    timer.wrap(CTDSensor, "log_likelihood_per_particle",
                "sensors.ctd.logL")

    # Wrap exp.truth.sample for the dyn_current path. The closure
    # `dyn_current` inside run_one_station uses exp.truth.sample directly.
    orig_truth_sample = exp.truth.sample

    def _truth_sample_wrapped(lat, lon, d, t):
        t0 = time.perf_counter()
        try:
            return orig_truth_sample(lat, lon, d, t)
        finally:
            dt = time.perf_counter() - t0
            timer.stats["truth.sample"][0] += 1
            timer.stats["truth.sample"][1] += dt
    exp.truth.sample = _truth_sample_wrapped

    orig_truth_sb = exp.truth.sample_batched

    def _truth_sb_wrapped(lats, lons, ds, t):
        t0 = time.perf_counter()
        try:
            return orig_truth_sb(lats, lons, ds, t)
        finally:
            dt = time.perf_counter() - t0
            timer.stats["truth.sample_batched"][0] += 1
            timer.stats["truth.sample_batched"][1] += dt
    exp.truth.sample_batched = _truth_sb_wrapped

    # Wrap nemo prior.sample / sample_batched (used by pf.predict
    # via the prior_current closure).
    orig_prior_sb = exp.prior.sample_batched

    def _prior_sb_wrapped(lats, lons, ds, t):
        t0 = time.perf_counter()
        try:
            return orig_prior_sb(lats, lons, ds, t)
        finally:
            dt = time.perf_counter() - t0
            timer.stats["prior.sample_batched"][0] += 1
            timer.stats["prior.sample_batched"][1] += dt
    exp.prior.sample_batched = _prior_sb_wrapped

    orig_prior_s = exp.prior.sample

    def _prior_s_wrapped(lat, lon, d, t):
        t0 = time.perf_counter()
        try:
            return orig_prior_s(lat, lon, d, t)
        finally:
            dt = time.perf_counter() - t0
            timer.stats["prior.sample"][0] += 1
            timer.stats["prior.sample"][1] += dt
    exp.prior.sample = _prior_s_wrapped

    # tracer prior + truth — may be frozen dataclasses; wrap at class level
    # so all instances see it. Best-effort; skip if class-level patch fails.
    for obj_attr, label in (
        ("tracer_prior", "tracer_prior.sample"),
        ("tracer_truth", "tracer_truth.sample"),
    ):
        obj = getattr(exp, obj_attr, None)
        if obj is None:
            continue
        try:
            timer.wrap(type(obj), "sample", label)
        except (AttributeError, TypeError):
            pass


def run_pass(label: str, use_jax: bool, do_cprofile: bool = False) -> None:
    if use_jax:
        os.environ["FLEET_USE_JAX_MPC"] = "1"
    else:
        os.environ.pop("FLEET_USE_JAX_MPC", None)
    print(f"\n========== pass: {label} (FLEET_USE_JAX_MPC="
          f"{os.environ.get('FLEET_USE_JAX_MPC', 'unset')}) ==========",
          flush=True)

    from rbpf_prototype import run_one_station
    exp = _make_experiment()
    timer = PhaseTimer()
    _wrap_run(exp, timer)

    if do_cprofile:
        prof = cProfile.Profile()
        prof.enable()
    t0 = time.time()
    res = run_one_station(exp, seed=SEED)
    wall = time.time() - t0
    if do_cprofile:
        prof.disable()

    timer.restore()
    # Umbrellas: phases whose recorded total contains nested wrapped
    # callees and would double-count if added to the leaf-time sum.
    umbrella = (
        "mpc.choose_depth",
        "knowledge.get_current_at_batched",
        "knowledge.get_current_at_batched_draw",
        "knowledge.precompute_posterior_draws",
        "knowledge.precompute_for_decision",
        "pf.predict",
        "pf.cov_m",
        "pf.posterior_std_m",
        "pf.sample_currents",
        "bias.lookup_and_accumulate",
        "bias.kalman_update_leg",
    )
    timer.report(wall, header=f"{label} phase breakdown",
                  umbrella=umbrella)

    print(f"\n[{label}] mission wall: {wall:.2f}s "
          f"  ctrl_mean={res.ctrl_mean_m():.0f}m  "
          f"pf_err_mean={float(np.nanmean(res.pf_err_m)):.0f}m  "
          f"surfacings={res.surface_events}  "
          f"lora_fix_ticks={int(res.lora_fix_mask.sum())}")

    if do_cprofile:
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(40)
        print("\n--- cProfile top-40 cumulative ---")
        print(s.getvalue())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cpu-only", action="store_true")
    p.add_argument("--jax-only", action="store_true")
    p.add_argument("--cprofile", action="store_true")
    args = p.parse_args()

    _init_world()
    if not args.jax_only:
        run_pass("CPU baseline", use_jax=False, do_cprofile=args.cprofile)
    if not args.cpu_only:
        run_pass("Path-A (JAX MPC)", use_jax=True,
                  do_cprofile=args.cprofile)


if __name__ == "__main__":
    main()
