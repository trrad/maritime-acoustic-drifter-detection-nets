"""Side-by-side RTL/Python particle filter trace comparison.

Runs a Python model matching the RTL architecture (FP position storage,
FP predict, lin_to_lns8 for weight kernel, delta-encoded sensors, uniform
recenter) and compares against the RTL's debug trace output.

The RTL trace is produced by compiling tb_pf_e2e.v with -DDEBUG_TRACE.

Usage:
    cd rtl
    python vectors/pf_debug_compare.py [--steps N] [--particles N]

Reads:  build/rtl_trace.txt (with DBG_ lines from DEBUG_TRACE build)
Writes: build/debug_compare.txt (side-by-side comparison)
"""

import sys
import os
import math
import re
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'experiments'))
from importlib import import_module
lns8 = import_module('08_lns_cycle_accurate')

_real_to_lns8 = lns8._real_to_lns8
_lns8_to_real = lns8._lns8_to_real
lns8_multiply = lns8.lns8_multiply
lns8_add = lns8.lns8_add
lns8_subtract = lns8.lns8_subtract
lns8_divide = lns8.lns8_divide
ZERO_LOG_MAG = lns8.ZERO_LOG_MAG

from gen_pf_scenario import (generate_trajectory_6d, generate_measurements_6d,
                             SENSOR_DEFS, DIM_NAMES)


# ---------------------------------------------------------------------------
# RTL trace parser
# ---------------------------------------------------------------------------

def parse_rtl_debug_trace(path):
    """Parse DBG_ lines and particle dumps from RTL trace."""
    steps = []
    current = None

    with open(path) as f:
        for line in f:
            line = line.rstrip('\n')

            if line.startswith('DBG_PRE_PREDICT'):
                m = re.match(r'DBG_PRE_PREDICT t=(\d+)', line)
                t = int(m.group(1))
                current = {'t': t, 'stages': {}}
                current['stages']['pre_predict'] = {'particles': {}}
                steps.append(current)
                _cur_stage = 'pre_predict'

            elif line.startswith('DBG_POST_PREDICT'):
                _cur_stage = 'post_predict'
                current['stages'][_cur_stage] = {'particles': {}}
                # Extract cycle info
                m = re.match(r'DBG_POST_PREDICT t=\d+ pos_cyc=(\d+) vel_cyc=(\d+)', line)
                if m:
                    current['stages'][_cur_stage]['pos_cyc'] = int(m.group(1))
                    current['stages'][_cur_stage]['vel_cyc'] = int(m.group(2))

            elif line.startswith('DBG_POST_WEIGHT'):
                _cur_stage = 'post_weight'
                current['stages'][_cur_stage] = {'particles': {}}

            elif line.startswith('DBG_POST_RESAMPLE'):
                _cur_stage = 'post_resample'
                m = re.match(r'DBG_POST_RESAMPLE t=\d+ weight_sum=(\d+) lfsr_raw=0x([0-9a-fA-F]+)', line)
                current['stages'][_cur_stage] = {
                    'particles': {},
                    'weight_sum': int(m.group(1)),
                    'lfsr_raw': int(m.group(2), 16),
                }

            elif line.startswith('DBG_POST_RECENTER'):
                _cur_stage = 'post_recenter'
                m = re.match(r'DBG_POST_RECENTER t=\d+ refs=(-?\d+)_(-?\d+)_(-?\d+) recenter=(-?\d+)_(-?\d+)_(-?\d+)', line)
                current['stages'][_cur_stage] = {
                    'particles': {},
                    'refs': [int(m.group(1)), int(m.group(2)), int(m.group(3))],
                    'recenter': [int(m.group(4)), int(m.group(5)), int(m.group(6))],
                }

            elif line.startswith('  p=') and current is not None:
                # Parse particle line: p=0 F0=62 F1=-3 F2=5100 L3=0_33 L4=1_10 L5=0_225
                m = re.match(r'  p=(\d+)(.*)', line)
                pidx = int(m.group(1))
                rest = m.group(2).strip()
                dims = {}
                for tok in rest.split():
                    if tok.startswith('F'):
                        dm = re.match(r'F(\d+)=(-?\d+)', tok)
                        dims[int(dm.group(1))] = ('F', int(dm.group(2)))
                    elif tok.startswith('L'):
                        dm = re.match(r'L(\d+)=(\d+)_(\d+)', tok)
                        dims[int(dm.group(1))] = ('L', int(dm.group(2)), int(dm.group(3)))
                    elif tok.startswith('lw='):
                        dm = re.match(r'lw=(\d+)_(\d+)', tok)
                        dims['lw'] = (int(dm.group(1)), int(dm.group(2)))
                current['stages'][_cur_stage]['particles'][pidx] = dims

    return steps


# ---------------------------------------------------------------------------
# Python RTL-style model (matching RTL architecture)
# ---------------------------------------------------------------------------

def run_python_rtl_model(true_state, measurements, sensors, dt_val,
                         n_particles, noise_pos_scale, noise_vel_scale,
                         seed=42, max_steps=5, trace_particles=4):
    """Run Python model matching RTL architecture, return structured trace."""
    rng = np.random.default_rng(seed + 2000)

    dt_s, dt_m = _real_to_lns8(dt_val)
    nsp_s, nsp_m = _real_to_lns8(noise_pos_scale)
    nsv_s, nsv_m = _real_to_lns8(noise_vel_scale)
    tss = [_real_to_lns8(2 * sigma**2) for _, _, sigma in sensors]

    # Initialize particles: position as 16-bit FP, velocity as LNS8
    pos_fp = np.zeros((n_particles, 3), dtype=np.int32)
    vel_s = np.zeros((n_particles, 3), dtype=int)
    vel_m = np.zeros((n_particles, 3), dtype=int)
    refs = np.zeros(3, dtype=np.float64)

    for i in range(n_particles):
        for d in range(3):
            x = true_state[0, d] + rng.normal(0, 0.5)
            pos_fp[i, d] = max(-32768, min(32767, int(round(x * 256))))
        for d in range(3):
            x = true_state[0, d + 3] + rng.normal(0, 0.1)
            if abs(x) < 0.01:
                x = 0.01 if x >= 0 else -0.01
            vel_s[i, d], vel_m[i, d] = _real_to_lns8(x)

    # Weight is stored as LNS8 {sign, mag}
    lw_s = np.zeros(n_particles, dtype=int)
    lw_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)

    steps = []
    n_steps = min(max_steps, len(true_state) - 1)

    for t in range(n_steps):
        step = {'t': t, 'stages': {}}

        # --- Snapshot: pre-predict ---
        stage = {'particles': {}}
        for i in range(trace_particles):
            dims = {}
            for d in range(3):
                dims[d] = ('F', int(pos_fp[i, d]))
            for d in range(3):
                dims[d + 3] = ('L', 1 if vel_s[i, d] == -1 else 0, int(vel_m[i, d]) & 0xFF)
            stage['particles'][i] = dims
        step['stages']['pre_predict'] = stage

        # --- FP Position Predict ---
        for i in range(n_particles):
            for d in range(3):
                vdt_s_v, vdt_m_v, _ = lns8_multiply(
                    vel_s[i, d], vel_m[i, d], dt_s, dt_m)
                vdt_fp = int(round(_lns8_to_real(vdt_s_v, vdt_m_v) * 256))
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s_v, sn_m_v, _ = lns8_multiply(nsp_s, nsp_m, ns, nm)
                sn_fp = int(round(_lns8_to_real(sn_s_v, sn_m_v) * 256))
                pos_fp[i, d] = max(-32768, min(32767,
                                               pos_fp[i, d] + vdt_fp + sn_fp))

        # --- LNS8 Velocity Predict ---
        for i in range(n_particles):
            for d in range(3):
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s_v, sn_m_v, _ = lns8_multiply(nsv_s, nsv_m, ns, nm)
                vel_s[i, d], vel_m[i, d], _ = lns8_add(
                    vel_s[i, d], vel_m[i, d], sn_s_v, sn_m_v)

        # --- Snapshot: post-predict ---
        stage = {'particles': {}}
        for i in range(trace_particles):
            dims = {}
            for d in range(3):
                dims[d] = ('F', int(pos_fp[i, d]))
            for d in range(3):
                dims[d + 3] = ('L', 1 if vel_s[i, d] == -1 else 0, int(vel_m[i, d]) & 0xFF)
            stage['particles'][i] = dims
        step['stages']['post_predict'] = stage

        # --- Weight ---
        lw_s[:] = 0
        lw_m[:] = ZERO_LOG_MAG
        for si, (name, dim, sigma) in enumerate(sensors):
            z_offset = measurements[t, si] - refs[dim] if dim < 3 else measurements[t, si]
            if abs(z_offset) < 0.01:
                z_offset = 0.01 if z_offset >= 0 else -0.01
            z_s, z_m = _real_to_lns8(z_offset)
            tss_s, tss_m = tss[si]

            for i in range(n_particles):
                if dim < 3:
                    # Position: convert FP → LNS8 (using Python exact encode)
                    pval = int(pos_fp[i, dim])
                    if pval == 0:
                        p_sign, p_mag = 0, ZERO_LOG_MAG
                    else:
                        p_sign = -1 if pval < 0 else 1
                        _, p_mag = _real_to_lns8(abs(pval) / 256.0)
                else:
                    p_sign = vel_s[i, dim - 3]
                    p_mag = vel_m[i, dim - 3]

                ds, dm, _ = lns8_subtract(z_s, z_m, p_sign, p_mag)
                sq_s, sq_m, _ = lns8_multiply(ds, dm, ds, dm)
                div_s, div_m, _ = lns8_divide(sq_s, sq_m, tss_s, tss_m)
                neg_s = -div_s if div_s != 0 else 0
                lw_s[i], lw_m[i], _ = lns8_add(
                    lw_s[i], lw_m[i], neg_s, div_m)

        # --- Snapshot: post-weight (first N particle weights) ---
        stage = {'particles': {}}
        for i in range(trace_particles):
            hw_s = 1 if lw_s[i] == -1 else (0 if lw_s[i] in (0, 1) else 0)
            hw_m = int(lw_m[i]) & 0xFF
            stage['particles'][i] = {'lw': (hw_s, hw_m)}
        step['stages']['post_weight'] = stage

        # --- Resample ---
        linear_w = np.zeros(n_particles)
        max_lw = max(_lns8_to_real(lw_s[i], lw_m[i])
                     for i in range(n_particles))
        for i in range(n_particles):
            linear_w[i] = np.exp(
                _lns8_to_real(lw_s[i], lw_m[i]) - max_lw)
        w_sum = linear_w.sum()
        if w_sum > 0:
            linear_w /= w_sum
        else:
            linear_w[:] = 1.0 / n_particles

        cumsum = np.cumsum(linear_w)
        cumsum[-1] = 1.0
        u = (rng.uniform() + np.arange(n_particles)) / n_particles
        idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)
        pos_fp = pos_fp[idx].copy()
        vel_s = vel_s[idx].copy()
        vel_m = vel_m[idx].copy()

        # --- Snapshot: post-resample ---
        stage = {'particles': {}, 'weight_sum': int(round(w_sum * 256))}
        for i in range(trace_particles):
            dims = {}
            for d in range(3):
                dims[d] = ('F', int(pos_fp[i, d]))
            for d in range(3):
                dims[d + 3] = ('L', 1 if vel_s[i, d] == -1 else 0, int(vel_m[i, d]) & 0xFF)
            stage['particles'][i] = dims
        step['stages']['post_resample'] = stage

        # --- Recenter (uniform mean, FP) ---
        recenter_vals = []
        for d in range(3):
            mean_fp = int(round(np.mean(pos_fp[:, d])))
            refs[d] += mean_fp / 256.0
            pos_fp[:, d] -= mean_fp
            pos_fp[:, d] = np.clip(pos_fp[:, d], -32768, 32767)
            recenter_vals.append(mean_fp)

        # --- Snapshot: post-recenter ---
        stage = {
            'particles': {},
            'refs': [int(round(refs[d] * 256)) for d in range(3)],
            'recenter': recenter_vals,
        }
        for i in range(trace_particles):
            dims = {}
            for d in range(3):
                dims[d] = ('F', int(pos_fp[i, d]))
            for d in range(3):
                dims[d + 3] = ('L', 1 if vel_s[i, d] == -1 else 0, int(vel_m[i, d]) & 0xFF)
            stage['particles'][i] = dims
        step['stages']['post_recenter'] = stage

        steps.append(step)

    return steps


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def normalize_lns8(dim_data):
    """Normalize LNS8 to canonical (hw_sign, unsigned_mag) form.

    RTL uses: hw_sign (0=pos, 1=neg), unsigned mag (0-255)
    Python uses: py_sign (+1 or -1), signed mag (can be negative)
    """
    if dim_data[0] != 'L':
        return dim_data
    sign_raw, mag_raw = dim_data[1], dim_data[2]
    # Normalize sign: Python +1→0, -1→1; RTL already 0/1
    if sign_raw == 1 or sign_raw == 0:
        hw_sign = sign_raw
    elif sign_raw == -1:
        hw_sign = 1
    else:
        hw_sign = 0
    # Normalize mag: ensure unsigned 0-255
    hw_mag = mag_raw & 0xFF
    return ('L', hw_sign, hw_mag)


def fmt_dim(dim_data):
    """Format a dimension value for display."""
    if dim_data[0] == 'F':
        return f"{dim_data[1]:6d}"
    elif dim_data[0] == 'L':
        return f"{dim_data[1]}_{dim_data[2]:<3d}"
    return str(dim_data)


def compare_traces(rtl_steps, py_steps, out):
    """Compare RTL and Python traces, write report to out."""
    n = min(len(rtl_steps), len(py_steps))
    total_checks = 0
    total_mismatches = 0
    first_mismatch = None

    for step_idx in range(n):
        rs = rtl_steps[step_idx]
        ps = py_steps[step_idx]
        t = rs['t']

        out.write(f"\n{'='*70}\n")
        out.write(f"STEP {t}\n")
        out.write(f"{'='*70}\n")

        for stage_name in ['pre_predict', 'post_predict', 'post_weight',
                           'post_resample', 'post_recenter']:
            if stage_name not in rs['stages'] or stage_name not in ps['stages']:
                continue

            r_stage = rs['stages'][stage_name]
            p_stage = ps['stages'][stage_name]

            out.write(f"\n  --- {stage_name} ---\n")

            # Compare metadata (refs, recenter, weight_sum)
            for key in ['refs', 'recenter', 'weight_sum']:
                if key in r_stage and key in p_stage:
                    rv = r_stage[key]
                    pv = p_stage[key]
                    match = rv == pv
                    tag = "  " if match else ">>"
                    out.write(f"  {tag} {key}: RTL={rv}  PY={pv}")
                    if not match:
                        out.write("  << MISMATCH")
                        total_mismatches += 1
                        if first_mismatch is None:
                            first_mismatch = (t, stage_name, key)
                    out.write("\n")
                    total_checks += 1

            # Compare particles
            r_parts = r_stage.get('particles', {})
            p_parts = p_stage.get('particles', {})
            common = sorted(set(r_parts.keys()) & set(p_parts.keys()))

            for pidx in common:
                rd = r_parts[pidx]
                pd = p_parts[pidx]

                if 'lw' in rd and 'lw' in pd:
                    # Weight comparison — normalize sign+mag conventions
                    r_lw = rd['lw']  # (hw_sign, hw_mag) already 0/1, unsigned
                    p_raw = pd['lw']
                    # Normalize Python: sign ±1→0/1, mag→unsigned
                    p_s = 1 if p_raw[0] == -1 else (p_raw[0] if p_raw[0] in (0,1) else 0)
                    p_m = p_raw[1] & 0xFF
                    p_lw = (p_s, p_m)
                    match = r_lw == p_lw
                    tag = "  " if match else ">>"
                    out.write(f"  {tag} p={pidx} lw: RTL={r_lw[0]}_{r_lw[1]:<3d}  PY={p_lw[0]}_{p_lw[1]:<3d}")
                    if not match:
                        out.write("  << MISMATCH")
                        total_mismatches += 1
                        if first_mismatch is None:
                            first_mismatch = (t, stage_name, f'p{pidx}_lw')
                    out.write("\n")
                    total_checks += 1
                else:
                    # Dimension comparison
                    mismatches_here = []
                    line_r = f"  p={pidx}"
                    line_p = f"  p={pidx}"
                    for d in sorted(k for k in rd if isinstance(k, int)):
                        if d not in pd:
                            continue
                        rv = normalize_lns8(rd[d])
                        pv = normalize_lns8(pd[d])
                        match = rv == pv
                        total_checks += 1
                        if not match:
                            total_mismatches += 1
                            mismatches_here.append(d)
                            if first_mismatch is None:
                                first_mismatch = (t, stage_name, f'p{pidx}_d{d}')
                        line_r += f" d{d}={fmt_dim(rv)}"
                        line_p += f" d{d}={fmt_dim(pv)}"

                    tag = ">>" if mismatches_here else "  "
                    out.write(f"  {tag} RTL: {line_r}\n")
                    out.write(f"  {tag}  PY: {line_p}")
                    if mismatches_here:
                        out.write(f"  << MISMATCH dims {mismatches_here}")
                    out.write("\n")

    out.write(f"\n{'='*70}\n")
    out.write(f"SUMMARY: {total_checks} checks, {total_mismatches} mismatches\n")
    if first_mismatch:
        out.write(f"FIRST MISMATCH: step {first_mismatch[0]}, "
                  f"stage {first_mismatch[1]}, field {first_mismatch[2]}\n")
    else:
        out.write("ALL MATCH\n")
    out.write(f"{'='*70}\n")

    return total_mismatches, first_mismatch


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description='PF debug trace comparison')
    parser.add_argument('--steps', type=int, default=5)
    parser.add_argument('--particles', type=int, default=128)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--trace-particles', type=int, default=4,
                        help='Number of particles to trace in detail')
    args = parser.parse_args()

    dt = 0.5
    noise_pos_scale = 0.5
    noise_vel_scale = 0.3
    sensors = SENSOR_DEFS[:3]

    print("Generating trajectory + measurements...")
    true_state = generate_trajectory_6d(args.steps, dt, seed=args.seed)
    measurements = generate_measurements_6d(true_state, sensors, seed=args.seed)

    print("Running Python RTL-style model...")
    py_steps = run_python_rtl_model(
        true_state, measurements, sensors, dt,
        args.particles, noise_pos_scale, noise_vel_scale,
        seed=args.seed, max_steps=args.steps,
        trace_particles=args.trace_particles)
    print(f"  {len(py_steps)} steps traced")

    rtl_trace = 'build/rtl_trace.txt'
    if not os.path.exists(rtl_trace):
        print(f"ERROR: {rtl_trace} not found. Run: make sim_e2e with -DDEBUG_TRACE")
        sys.exit(1)

    print("Parsing RTL debug trace...")
    rtl_steps = parse_rtl_debug_trace(rtl_trace)
    print(f"  {len(rtl_steps)} steps found in RTL trace")

    if not rtl_steps:
        print("ERROR: No DBG_ lines found. Was TB compiled with -DDEBUG_TRACE?")
        sys.exit(1)

    out_path = 'build/debug_compare.txt'
    print(f"Comparing traces → {out_path}")
    with open(out_path, 'w') as f:
        mismatches, first = compare_traces(rtl_steps, py_steps, f)

    print(f"\nResult: {mismatches} mismatches")
    if first:
        print(f"First mismatch: step {first[0]}, stage {first[1]}, field {first[2]}")

        # Print context around first mismatch
        print(f"\n--- Context from {out_path} ---")
        with open(out_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if '>>' in line or 'MISMATCH' in line:
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                for j in range(start, end):
                    print(lines[j].rstrip())
                print("...")
                break
    else:
        print("Traces match perfectly!")


if __name__ == '__main__':
    main()
