"""Verify n_ticks=runtime refactor: changing decision cadence between
two consecutive single-drifter runs in the same process doesn't
trigger a JAX recompile or grow GPU memory.

Pre-refactor: cad=1800 (n_ticks=3) and cad=7200 (n_ticks=12) compile
two separate kernel specializations, each ~1 GB GPU. Post-refactor:
one specialization handles both, total GPU stable at ~1 GB.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["FLEET_USE_JAX_MPC"] = "1"
os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")

import _probe_gpu_mem as probe_mod  # reuse helpers


def make_exp_with_cadence(run_hours: int, cadence_sec: float):
    from rbpf_prototype import (
        BiasConfig, CTDSensor, Experiment, LoRaRangeSensor, PFConfig,
        SensorConfig, SimConfig, StationConfig,
    )
    from rbpf_prototype.surfacing import FixedIntervalPolicy

    base_exp = probe_mod.make_experiment(run_hours=run_hours)
    base_exp.sim = SimConfig(
        run_hours=run_hours, dt_sec=600.0,
        control_cadence_sec=float(cadence_sec),
        lookahead_sec=float(cadence_sec),
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model="ou_integrated",
    )
    return base_exp


def run_short(exp, label: str, p):
    from rbpf_prototype import run_one_station
    t0 = time.time()
    r = run_one_station(exp, seed=1000)
    wall = time.time() - t0
    p.mark(f"{label}: end (wall {wall:.1f}s)")
    print(f"  [{label}] surfacings={r.surface_events}, "
          f"ctrl_mean={r.ctrl_mean_m():.0f}m", flush=True)


def main():
    p = probe_mod.GpuPhases()
    p.mark("startup")
    import jax; _ = jax.devices()
    p.mark("after jax init")
    probe_mod.init_world()
    p.mark("after world load")

    # Phase 1: cadence=1800 (n_ticks=3), 4-hour mission
    exp_a = make_exp_with_cadence(run_hours=4, cadence_sec=1800.0)
    p.mark("cad=1800 exp built")
    run_short(exp_a, "cad=1800 first run (compile)", p)

    # Phase 2: cadence=7200 (n_ticks=12), 4-hour mission
    # Pre-refactor: this would compile a SECOND kernel specialization
    # and add ~1 GB GPU. Post-refactor: same kernel, no growth.
    exp_b = make_exp_with_cadence(run_hours=4, cadence_sec=7200.0)
    p.mark("cad=7200 exp built")
    run_short(exp_b, "cad=7200 first run (should NOT recompile)", p)

    # Phase 3: cadence=1800 again, confirm no growth
    exp_c = make_exp_with_cadence(run_hours=4, cadence_sec=1800.0)
    run_short(exp_c, "cad=1800 again", p)

    p.report()

    # Diagnostic: GPU delta from end-of-cad=1800 to end-of-cad=7200.
    rows = p.rows
    end_a = next((s for (l, s, t, _) in rows if "cad=1800 first" in l), None)
    end_b = next((s for (l, s, t, _) in rows if "cad=7200 first" in l), None)
    if end_a is not None and end_b is not None:
        delta = end_b - end_a
        print(f"\nGPU delta cad=1800 → cad=7200: {delta:+d} MiB "
              f"(should be ~0 if refactor worked)")
        if abs(delta) < 100:
            print("  ✓ Refactor PASS — no recompile penalty")
        else:
            print("  ✗ Refactor FAIL — kernel re-specialized for n_ticks=12")


if __name__ == "__main__":
    main()
