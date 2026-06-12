"""GPU-memory probe for path-A.

Single-process. Measures GPU memory at well-defined phases of one
worker's lifecycle (import → world build → experiment build →
first MPC plan → mid mission → end mission → fresh cycle 1 →
fresh cycle 2). Output is a phase-by-phase table of:
    self_used_mib    -- THIS process's GPU footprint via
                        nvidia-smi --query-compute-apps
    total_used_mib   -- entire GPU's footprint (other procs included)

Output answers two questions:
  1. What's the per-worker steady-state GPU footprint?
  2. Does a fresh cycle add GPU memory (bundle cache hit or not)?

Once we know steady-state, sweep config follows:
    safe_workers = floor((vram_mib - 1500_baseline_mib - 500_safety_mib)
                          / per_worker_steady_state_mib)

Run:
    uv run --with xarray,netCDF4,numpy,matplotlib,scipy,filterpy \
           --with "jax[cuda12]" python _probe_gpu_mem.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Match the fleet sweep / bench setup.
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]
STATION = (49.3533, -123.7411, 289)
RUN_HOURS = int(os.environ.get("PROBE_RUN_HOURS", "24"))
SEED = 1000
LORA_SIGMA_M = 20.0


# ---------- nvidia-smi helpers ----------

def gpu_total_used_mib() -> int:
    """Total GPU memory used (all processes), MiB."""
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used",
         "--format=csv,noheader,nounits"],
        text=True,
    ).strip()
    return int(out.splitlines()[0])


def gpu_self_used_mib() -> int:
    """This process's GPU memory footprint via compute-apps query, MiB.

    Returns 0 if our PID is not in the compute-apps list (i.e., we
    haven't allocated any GPU memory yet).
    """
    out = subprocess.check_output(
        ["nvidia-smi",
         "--query-compute-apps=pid,used_memory",
         "--format=csv,noheader,nounits"],
        text=True,
    )
    self_pid = os.getpid()
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if int(parts[0]) == self_pid:
            return int(parts[1])
    return 0


# ---------- phase tracker ----------

class GpuPhases:
    def __init__(self):
        self.rows: list[tuple[str, int, int, float]] = []
        self.t0 = time.time()

    def mark(self, label: str) -> None:
        self_mib = gpu_self_used_mib()
        total_mib = gpu_total_used_mib()
        self.rows.append((label, self_mib, total_mib,
                            time.time() - self.t0))
        print(f"  [{time.time() - self.t0:6.1f}s]  {label:<46}  "
              f"self={self_mib:>5d} MiB  total={total_mib:>5d} MiB",
              flush=True)

    def report(self) -> None:
        print("\n=== GPU memory probe ===")
        print(f"{'phase':<46}  {'self MiB':>10}  {'Δself':>8}  "
              f"{'total MiB':>10}  {'wall s':>8}")
        prev_self = 0
        for label, self_mib, total_mib, t in self.rows:
            ds = self_mib - prev_self
            ds_str = f"{ds:+d}" if ds != 0 else "  0"
            print(f"{label:<46}  {self_mib:>10d}  {ds_str:>8}  "
                  f"{total_mib:>10d}  {t:>8.1f}")
            prev_self = self_mib


# ---------- world builder + experiment builder (mirror _bench/_fleet) ----------

_W: dict = {}


def init_world() -> None:
    from salishseacast_cache import (
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import (
        build_layered_noise_field, build_layered_tracer_noise_field,
    )
    from truth_field import build_tracer_field, build_truth_field
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


def make_experiment(run_hours: int):
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
        run_hours=run_hours, dt_sec=600.0,
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


# ---------- run-one-mission with phase markers ----------

def run_mission(p: GpuPhases, run_hours: int, seed: int,
                  cycle_label: str) -> None:
    """Build experiment + run_one_station, marking GPU memory at:
      - after experiment build
      - after first MPC plan fires (inside run_one_station)
      - after 10 plans
      - after 30 plans
      - end of mission
    """
    from rbpf_prototype import run_one_station

    exp = make_experiment(run_hours=run_hours)
    p.mark(f"{cycle_label}: experiment built (host only)")

    # Mid-mission probes via tick_recorder. Grab marks every 30 plans
    # (each plan-tick is every 3 sim ticks at default cadence).
    plan_count = [0]
    target_marks = [1, 10, 30]   # MPC plans at which we sample memory

    def recorder(t_sec, state, pf, bias_state):
        # We can't directly count plan calls from here; instead use
        # a heuristic: a plan fires every 1800s (control_cadence_sec).
        # `t_sec` after the very first plan ≈ 600s (one tick post-plan).
        nonlocal plan_count
        # Fire markers at hand-picked t_sec boundaries.
        # Plan 1 fires at t=0 → recorder sees t_sec=600 (one dt post).
        # Plan 10 fires at t≈16200 → recorder sees t_sec≈16800.
        # Plan 30 fires at t≈52200 → recorder sees t_sec≈52800.
        if 595 < t_sec < 610 and 1 in target_marks:
            target_marks.remove(1)
            p.mark(f"{cycle_label}: after plan #1 (compile + first run)")
        elif 16795 < t_sec < 16810 and 10 in target_marks:
            target_marks.remove(10)
            p.mark(f"{cycle_label}: after plan #~10")
        elif 52795 < t_sec < 52810 and 30 in target_marks:
            target_marks.remove(30)
            p.mark(f"{cycle_label}: after plan #~30")

    t0 = time.time()
    res = run_one_station(exp, seed=seed, tick_recorder=recorder)
    wall = time.time() - t0
    p.mark(f"{cycle_label}: end of mission ({run_hours}h, "
           f"{wall:.1f}s wall)")
    # Tiny science sanity: number of surfacings should be sensible.
    print(f"  [{cycle_label}] surfacings={res.surface_events}, "
          f"lora_fix_ticks={int(res.lora_fix_mask.sum())}, "
          f"ctrl_mean={res.ctrl_mean_m():.0f}m", flush=True)


# ---------- main ----------

def main() -> None:
    # Force JAX path for the MPC.
    os.environ["FLEET_USE_JAX_MPC"] = "1"
    # No mem fraction cap, no preallocate — let JAX use what it needs;
    # the probe measures what that "what it needs" actually is.
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")

    p = GpuPhases()
    p.mark("startup (before any imports)")

    # Touch jax to force CUDA init.
    import jax
    _ = jax.devices()
    p.mark("after `import jax; jax.devices()`")

    # Build the world (host-only — should NOT touch GPU).
    init_world()
    p.mark("after init_world (NEMO, noise, tracer on host)")

    # Cycle 0: simulate "first drifter mission in this worker."
    run_mission(p, run_hours=RUN_HOURS, seed=SEED, cycle_label="cycle 0")

    # Cycle 1: simulate "second mission in same worker" (campaign mode
    # cycle 1). With the bundle cache, GPU delta should be near-zero.
    run_mission(p, run_hours=RUN_HOURS, seed=SEED + 100,
                  cycle_label="cycle 1")

    # Cycle 2 (24h tail).
    run_mission(p, run_hours=RUN_HOURS, seed=SEED + 200,
                  cycle_label="cycle 2")

    p.report()

    # Bundle-cache effectiveness check.
    rows = p.rows
    end_c0 = next((s for (l, s, t, _) in rows if "cycle 0: end" in l), None)
    end_c1 = next((s for (l, s, t, _) in rows if "cycle 1: end" in l), None)
    end_c2 = next((s for (l, s, t, _) in rows if "cycle 2: end" in l), None)
    if end_c0 is not None and end_c1 is not None:
        d01 = end_c1 - end_c0
        d12 = (end_c2 - end_c1) if end_c2 is not None else None
        print(f"\nbundle-cache check:")
        print(f"  cycle 0 → cycle 1 GPU delta: {d01:+d} MiB "
              f"(should be ~0 if cache works)")
        if d12 is not None:
            print(f"  cycle 1 → cycle 2 GPU delta: {d12:+d} MiB "
                  f"(should be ~0 if cache works)")

    # Worker-fit estimate.
    if end_c2 is not None:
        per_worker_mib = end_c2
        # GPU total minus baseline (idle ~1500 MiB) minus safety (500 MiB)
        # ≈ what's available for workers' worth of stuff.
        total_mib = 16384
        baseline_mib = 1500
        safety_mib = 500
        budget_mib = total_mib - baseline_mib - safety_mib
        n_workers_safe = budget_mib // per_worker_mib if per_worker_mib > 0 else 0
        print(f"\nworker-fit estimate:")
        print(f"  steady-state per-worker GPU: {per_worker_mib} MiB")
        print(f"  GPU budget (16GB - 1500 base - 500 safety): "
              f"{budget_mib} MiB")
        print(f"  → safe N_workers ≤ {n_workers_safe}")


if __name__ == "__main__":
    main()
