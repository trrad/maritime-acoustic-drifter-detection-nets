"""Trace weight kernel operations step-by-step in Python LNS8.

Reads the same scenario data as the RTL, uses the same initial particles,
and traces the weight kernel for the first 2 particles at step 0 to
produce output matching the RTL's DBG_WKERNEL format.

Usage:
    cd rtl && uv run --with numpy python vectors/pf_weight_trace.py
"""

import sys
import os

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


def sign_to_hw(s):
    return 1 if s == -1 else 0

def mag_to_hw(m):
    return m & 0xFF


def main():
    # Read initial particle states from scenario_init.hex
    # (same file the RTL reads)
    particles = []  # list of dicts with pos_fp[3] and vel_lns8[3]
    constants = {}

    with open('vectors/scenario_init.hex') as f:
        lines = [l.strip() for l in f if l.strip()]

    idx = 0
    n_particles = 128
    n_dims = 6

    # Read particles
    for i in range(n_particles):
        p = {'pos_fp': [0]*3, 'vel_s': [0]*3, 'vel_m': [0]*3}
        for d in range(n_dims):
            hi = int(lines[idx].split()[0], 16)
            lo = int(lines[idx].split()[1], 16)
            if d < 3:
                # Position: hi_byte lo_byte → 16-bit signed FP
                fp = (hi << 8) | lo
                if fp >= 32768:
                    fp -= 65536
                p['pos_fp'][d] = fp
            else:
                # Velocity: sign mag → LNS8
                p['vel_s'][d-3] = -1 if hi else 1
                p['vel_m'][d-3] = lo if lo < 128 else lo - 256
            idx += 1
        particles.append(p)

    # Read constants
    # DT
    hi, lo = int(lines[idx].split()[0], 16), int(lines[idx].split()[1], 16)
    dt_s = -1 if hi else 1
    dt_m = lo if lo < 128 else lo - 256
    idx += 1

    # NOISE_SCALE_POS
    hi, lo = int(lines[idx].split()[0], 16), int(lines[idx].split()[1], 16)
    nsp_s = -1 if hi else 1
    nsp_m = lo if lo < 128 else lo - 256
    idx += 1

    # NOISE_SCALE_VEL
    hi, lo = int(lines[idx].split()[0], 16), int(lines[idx].split()[1], 16)
    nsv_s = -1 if hi else 1
    nsv_m = lo if lo < 128 else lo - 256
    idx += 1

    # Sensors: TWO_SIGMA_SQ + dim
    sensors = []
    for s in range(3):
        hi, lo = int(lines[idx].split()[0], 16), int(lines[idx].split()[1], 16)
        tss_s = -1 if hi else 1
        tss_m = lo if lo < 128 else lo - 256
        idx += 1
        _, dim_byte = int(lines[idx].split()[0], 16), int(lines[idx].split()[1], 16)
        idx += 1
        sensors.append({'tss_s': tss_s, 'tss_m': tss_m, 'dim': dim_byte})

    # Read sensor measurements for step 0
    with open('vectors/scenario_sensors.hex') as f:
        sensor_line = f.readline().strip().split()
    sensor_meas = []
    for s in range(3):
        ss = int(sensor_line[s*2], 16)
        sm = int(sensor_line[s*2+1], 16)
        sensor_meas.append({
            's': -1 if ss else 1,
            'm': sm if sm < 128 else sm - 256
        })

    print("=== Constants ===")
    print(f"DT: sign={sign_to_hw(dt_s)} mag={mag_to_hw(dt_m)} (real={_lns8_to_real(dt_s, dt_m):.4f})")
    print(f"NOISE_SCALE_POS: sign={sign_to_hw(nsp_s)} mag={mag_to_hw(nsp_m)}")
    for s in range(3):
        print(f"Sensor {s}: dim={sensors[s]['dim']} "
              f"TWO_SIGMA_SQ=({sign_to_hw(sensors[s]['tss_s'])},{mag_to_hw(sensors[s]['tss_m'])}) "
              f"z=({sign_to_hw(sensor_meas[s]['s'])},{mag_to_hw(sensor_meas[s]['m'])})")

    print("\n=== Step 0 Weight Kernel (pre-predict particles, sensor 0 only for detail) ===\n")

    # At step 0, particles haven't been predicted yet.
    # The RTL runs predict first, THEN weight. But the pre-predict particle
    # states were loaded from the hex file. After predict, positions change.
    #
    # For the trace comparison, we need POST-PREDICT particle states.
    # But we don't know the noise values (LFSR vs numpy differ).
    #
    # Instead, let's trace what the weight kernel WOULD compute for the
    # pre-predict particles, and compare to what the RTL computes.
    # The RTL's DBG_WKERNEL runs during weight phase (after predict),
    # so the particle values are post-predict.
    #
    # For an exact comparison, we need the RTL's actual post-predict
    # particle values. Let's read them from the trace file.

    print("Reading RTL post-predict particle states from trace...")
    rtl_post_predict = {}
    with open('build/rtl_trace.txt') as f:
        in_post_predict_t0 = False
        for line in f:
            line = line.rstrip()
            if line.startswith('DBG_POST_PREDICT t=0'):
                in_post_predict_t0 = True
                continue
            if in_post_predict_t0 and line.startswith('  p='):
                import re
                m = re.match(r'  p=(\d+)(.*)', line)
                pidx = int(m.group(1))
                rest = m.group(2).strip()
                dims = {}
                for tok in rest.split():
                    if tok.startswith('F'):
                        dm = re.match(r'F(\d+)=(-?\d+)', tok)
                        dims[int(dm.group(1))] = int(dm.group(2))
                    elif tok.startswith('L'):
                        dm = re.match(r'L(\d+)=(\d+)_(\d+)', tok)
                        d = int(dm.group(1))
                        dims[d] = (int(dm.group(2)), int(dm.group(3)))
                rtl_post_predict[pidx] = dims
            elif in_post_predict_t0 and line.startswith('DBG_'):
                break

    if not rtl_post_predict:
        print("ERROR: No DBG_POST_PREDICT t=0 data found. Run RTL with -DDEBUG_TRACE.")
        return

    # Now trace weight kernel for first 2 particles using RTL's actual
    # post-predict values + sensor 0 at step 0

    # At step 0, t=0: sensor measurement is absolute (no offset conversion)
    # because the TB only does offset conversion for t > 0
    z_s = sensor_meas[0]['s']
    z_m = sensor_meas[0]['m']
    tss_s = sensors[0]['tss_s']
    tss_m = sensors[0]['tss_m']
    sensor_dim = sensors[0]['dim']

    print(f"\nSensor 0: dim={sensor_dim} z=({sign_to_hw(z_s)},{mag_to_hw(z_m)}) "
          f"tss=({sign_to_hw(tss_s)},{mag_to_hw(tss_m)})")
    print(f"  z_real = {_lns8_to_real(z_s, z_m):.4f}")
    print(f"  tss_real = {_lns8_to_real(tss_s, tss_m):.4f}")

    # Weight microcode (from pf_ucode_rom.v):
    # [6] SUB SENSOR_Z, PARTICLE_DIM → TEMP0   (diff = z - x)
    # [7] MUL TEMP0, TEMP0 → TEMP1              (diff²)
    # [8] DIV TEMP1, TWO_SIGMA_SQ → TEMP2       (diff²/(2σ²))
    # [9] SUB WEIGHT, TEMP2 → WEIGHT            (log_w -= penalty)

    for pidx in range(2):
        if pidx not in rtl_post_predict:
            continue
        p = rtl_post_predict[pidx]

        # Get particle's observed dimension value (as LNS8)
        # Position dims: stored as FP in SPRAM, converted to LNS8 by lin_to_lns8
        if sensor_dim < 3:
            fp_val = p[sensor_dim]  # signed 16-bit FP
            if fp_val == 0:
                x_s, x_m = 0, ZERO_LOG_MAG
            else:
                x_s = -1 if fp_val < 0 else 1
                _, x_m = _real_to_lns8(abs(fp_val) / 256.0)
        else:
            x_s_hw, x_m_hw = p[sensor_dim]
            x_s = -1 if x_s_hw else 1
            x_m = x_m_hw if x_m_hw < 128 else x_m_hw - 256

        # Initial weight (ZERO_LOG_MAG, set by resampler or init)
        w_s, w_m = 0, ZERO_LOG_MAG

        print(f"\n--- Particle {pidx}, dim {sensor_dim} ---")
        print(f"  x_fp = {p.get(sensor_dim, '?')}")
        print(f"  x_lns8 = ({sign_to_hw(x_s)}, {mag_to_hw(x_m)})  x_real = {_lns8_to_real(x_s, x_m):.6f}")
        print(f"  z_lns8 = ({sign_to_hw(z_s)}, {mag_to_hw(z_m)})  z_real = {_lns8_to_real(z_s, z_m):.6f}")

        # ucode[6]: SUB z, x → TEMP0 (diff)
        # Note: in the microcode, a_src=SENSOR_Z, b_src=PARTICLE_DIM
        # SUB means a - b. But the microcode has negate_a=0.
        # Actually: op=SUB, a=SENSOR_Z, b=PARTICLE_DIM → result = z - x
        diff_s, diff_m, _ = lns8_subtract(z_s, z_m, x_s, x_m)
        print(f"\n  [ucode 6] SUB z, x → diff")
        print(f"    op_a = ({sign_to_hw(z_s)}, {mag_to_hw(z_m)})")
        print(f"    op_b = ({sign_to_hw(x_s)}, {mag_to_hw(x_m)})")
        print(f"    res  = ({sign_to_hw(diff_s)}, {mag_to_hw(diff_m)})  = {_lns8_to_real(diff_s, diff_m):.6f}")
        print(f"  DBG_WKERNEL p={pidx} ucode=6 op_a={sign_to_hw(z_s)}_{mag_to_hw(z_m)} "
              f"op_b={sign_to_hw(x_s)}_{mag_to_hw(x_m)} "
              f"-> res={sign_to_hw(diff_s)}_{mag_to_hw(diff_m)} dest=6")

        # ucode[7]: MUL diff, diff → TEMP1 (diff²)
        sq_s, sq_m, _ = lns8_multiply(diff_s, diff_m, diff_s, diff_m)
        print(f"\n  [ucode 7] MUL diff, diff → diff²")
        print(f"    op_a = ({sign_to_hw(diff_s)}, {mag_to_hw(diff_m)})")
        print(f"    op_b = ({sign_to_hw(diff_s)}, {mag_to_hw(diff_m)})")
        print(f"    res  = ({sign_to_hw(sq_s)}, {mag_to_hw(sq_m)})  = {_lns8_to_real(sq_s, sq_m):.6f}")
        print(f"  DBG_WKERNEL p={pidx} ucode=7 op_a={sign_to_hw(diff_s)}_{mag_to_hw(diff_m)} "
              f"op_b={sign_to_hw(diff_s)}_{mag_to_hw(diff_m)} "
              f"-> res={sign_to_hw(sq_s)}_{mag_to_hw(sq_m)} dest=7")

        # ucode[8]: DIV diff², tss → TEMP2 (penalty)
        div_s, div_m, _ = lns8_divide(sq_s, sq_m, tss_s, tss_m)
        print(f"\n  [ucode 8] DIV diff², tss → penalty")
        print(f"    op_a = ({sign_to_hw(sq_s)}, {mag_to_hw(sq_m)})")
        print(f"    op_b = ({sign_to_hw(tss_s)}, {mag_to_hw(tss_m)})")
        print(f"    res  = ({sign_to_hw(div_s)}, {mag_to_hw(div_m)})  = {_lns8_to_real(div_s, div_m):.6f}")
        print(f"  DBG_WKERNEL p={pidx} ucode=8 op_a={sign_to_hw(sq_s)}_{mag_to_hw(sq_m)} "
              f"op_b={sign_to_hw(tss_s)}_{mag_to_hw(tss_m)} "
              f"-> res={sign_to_hw(div_s)}_{mag_to_hw(div_m)} dest=8")

        # ucode[9]: SUB weight, penalty → weight (log_w -= penalty)
        # Note: negate_a=0 in the microcode? Let me check...
        # Actually the microcode for weight is: SUB WEIGHT, TEMP2
        # This is weight - penalty. Since penalty is positive, this gives
        # weight - penalty = negative (making log weight more negative)
        new_w_s, new_w_m, _ = lns8_subtract(w_s, w_m, div_s, div_m)
        print(f"\n  [ucode 9] SUB weight, penalty → new_weight")
        print(f"    op_a = ({sign_to_hw(w_s)}, {mag_to_hw(w_m)})  [ZERO_LOG_MAG]")
        print(f"    op_b = ({sign_to_hw(div_s)}, {mag_to_hw(div_m)})")
        print(f"    res  = ({sign_to_hw(new_w_s)}, {mag_to_hw(new_w_m)})  = {_lns8_to_real(new_w_s, new_w_m):.6f}")
        print(f"  DBG_WKERNEL p={pidx} ucode=9 op_a={sign_to_hw(w_s)}_{mag_to_hw(w_m)} "
              f"op_b={sign_to_hw(div_s)}_{mag_to_hw(div_m)} "
              f"-> res={sign_to_hw(new_w_s)}_{mag_to_hw(new_w_m)} dest=1")


if __name__ == '__main__':
    main()
