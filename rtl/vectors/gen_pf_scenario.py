"""Generate a 6D particle filter scenario for RTL E2E testing.

6D state: [x, y, z, vx, vy, vz] — drone-like constant-velocity model.
3 Gaussian sensors: GPS-x (dim 0), GPS-y (dim 1), Baro-z (dim 2).

Produces:
  1. scenario_init.hex    — initial particle states (6 dims × N) + constants
  2. scenario_sensors.hex — sensor measurements per timestep
  3. scenario_truth.hex   — ground truth per timestep (6 dims)
  4. scenario_ref.jsonl   — Python LNS8 reference trace (for dashboard)

Run from rtl/vectors/:
    python gen_pf_scenario.py [--steps N] [--particles N] [--sensors N]
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'experiments'))
from importlib import import_module

lns8 = import_module('08_lns_cycle_accurate')
import numpy as np

# Re-export what we need
_real_to_lns8 = lns8._real_to_lns8
_lns8_to_real = lns8._lns8_to_real
lns8_multiply = lns8.lns8_multiply
lns8_add = lns8.lns8_add
lns8_subtract = lns8.lns8_subtract
lns8_divide = lns8.lns8_divide
ZERO_LOG_MAG = lns8.ZERO_LOG_MAG

DIM_NAMES = ['x', 'y', 'z', 'vx', 'vy', 'vz']

# Sensor definitions: (name, observed_dim, sigma)
SENSOR_DEFS = [
    ('GPS-x', 0, 1.0),
    ('GPS-y', 1, 1.5),
    ('Baro-z', 2, 0.8),
]


def sign_to_hw(s):
    """Python sign (+1, -1, 0) → HW (0=pos, 1=neg)."""
    return 1 if s == -1 else 0


def mag_to_hw(m):
    """Python int mag → unsigned 8-bit."""
    return m & 0xFF


def real_to_hw(x):
    """Real value → (hw_sign, hw_mag) tuple."""
    s, m = _real_to_lns8(x)
    return (sign_to_hw(s), mag_to_hw(m))


def hw_to_real(hw_sign, hw_mag):
    """HW encoding → real value."""
    if hw_mag == 0x80:
        return 0.0
    py_sign = -1 if hw_sign else 1
    py_mag = hw_mag if hw_mag < 128 else hw_mag - 256
    return _lns8_to_real(py_sign, py_mag)


def generate_trajectory_6d(n_steps, dt, seed=42):
    """6D constant-velocity trajectory with process noise.

    State: [x, y, z, vx, vy, vz]
    Dynamics:
      pos[d] = pos[d] + vel[d] * dt + N(0, noise_pos)
      vel[d] = vel[d] + N(0, noise_vel)

    Realistic drone scenario: ~5 m/s cruise, gentle turns,
    altitude hold. Covers ~250m over 100 steps at dt=0.5s.
    """
    rng = np.random.default_rng(seed)

    # Initial state: drone cruising at ~5 m/s with slight lateral drift
    state = np.zeros((n_steps + 1, 6))
    state[0] = [0.0, 0.0, 20.0, 4.0, -1.5, 0.2]  # [x,y,z,vx,vy,vz]

    noise_pos_std = 0.05   # process noise on position (wind gusts)
    noise_vel_std = 0.08   # velocity random walk (maneuvering)

    for t in range(n_steps):
        # Velocity update
        for d in range(3):
            state[t + 1, d + 3] = state[t, d + 3] + rng.normal(0, noise_vel_std)
        # Position update
        for d in range(3):
            state[t + 1, d] = (state[t, d] + state[t + 1, d + 3] * dt
                               + rng.normal(0, noise_pos_std))

    return state


def generate_measurements_6d(true_state, sensors, seed=42):
    """Generate Gaussian sensor measurements for 6D scenario."""
    rng = np.random.default_rng(seed + 1000)
    n_steps = len(true_state) - 1
    n_sensors = len(sensors)
    measurements = np.zeros((n_steps, n_sensors))

    for t in range(n_steps):
        for si, (name, dim, sigma) in enumerate(sensors):
            measurements[t, si] = true_state[t + 1, dim] + rng.normal(0, sigma)

    return measurements


def run_python_reference_6d(true_state, measurements, sensors, dt,
                            n_particles, noise_pos_scale, noise_vel_scale,
                            seed=42):
    """Run 6D bootstrap PF in Python LNS8, emit JSON-lines records."""
    rng = np.random.default_rng(seed + 2000)
    n_steps = len(true_state) - 1
    n_sensors = len(sensors)

    # Precompute LNS8 constants
    dt_s, dt_m = _real_to_lns8(dt)
    nsp_s, nsp_m = _real_to_lns8(noise_pos_scale)
    nsv_s, nsv_m = _real_to_lns8(noise_vel_scale)
    tss = []
    for name, dim, sigma in sensors:
        tss.append(_real_to_lns8(2 * sigma**2))

    # Initialize particles: 6 dims per particle
    # p_s[i][d], p_m[i][d]
    p_s = np.zeros((n_particles, 6), dtype=int)
    p_m = np.zeros((n_particles, 6), dtype=int)
    for i in range(n_particles):
        for d in range(6):
            x = true_state[0, d] + rng.normal(0, 0.5 if d < 3 else 0.1)
            # Ensure not too close to zero for LNS8
            if abs(x) < 0.01:
                x = 0.01 if x >= 0 else -0.01
            p_s[i, d], p_m[i, d] = _real_to_lns8(x)

    records = []
    sum_sq_err = np.zeros(6)

    for t in range(n_steps):
        # --- Predict ---
        predict_cycles = 0
        for i in range(n_particles):
            for d in range(3):  # position dims
                # vel * dt
                vdt_s, vdt_m, c = lns8_multiply(
                    p_s[i, d + 3], p_m[i, d + 3], dt_s, dt_m)
                predict_cycles += c
                # pos + vel*dt
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], vdt_s, vdt_m)
                predict_cycles += c
                # position noise
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s, sn_m, c = lns8_multiply(nsp_s, nsp_m, ns, nm)
                predict_cycles += c
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], sn_s, sn_m)
                predict_cycles += c

            for d in range(3, 6):  # velocity dims
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s, sn_m, c = lns8_multiply(nsv_s, nsv_m, ns, nm)
                predict_cycles += c
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], sn_s, sn_m)
                predict_cycles += c

        # --- Weight ---
        weight_cycles = 0
        lw_s = np.zeros(n_particles, dtype=int)
        lw_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)

        for si, (name, dim, sigma) in enumerate(sensors):
            z_s, z_m = _real_to_lns8(measurements[t, si])
            tss_s, tss_m = tss[si]
            for i in range(n_particles):
                ds, dm, c1 = lns8_subtract(z_s, z_m, p_s[i, dim], p_m[i, dim])
                sq_s, sq_m, c2 = lns8_multiply(ds, dm, ds, dm)
                div_s, div_m, c3 = lns8_divide(sq_s, sq_m, tss_s, tss_m)
                neg_s = -div_s if div_s != 0 else 0
                lw_s[i], lw_m[i], c4 = lns8_add(lw_s[i], lw_m[i], neg_s, div_m)
                weight_cycles += c1 + c2 + c3 + c4

        # --- Resample ---
        resample_cycles = 6 * n_particles  # approximate
        linear_w = np.zeros(n_particles)
        max_lw = -np.inf
        for i in range(n_particles):
            lw = _lns8_to_real(lw_s[i], lw_m[i])
            if lw > max_lw:
                max_lw = lw
        for i in range(n_particles):
            lw = _lns8_to_real(lw_s[i], lw_m[i])
            linear_w[i] = np.exp(lw - max_lw)
        w_sum = linear_w.sum()
        if w_sum > 0:
            linear_w /= w_sum
        else:
            linear_w[:] = 1.0 / n_particles

        # Weighted estimate per dimension
        estimate = np.zeros(6)
        for i in range(n_particles):
            for d in range(6):
                estimate[d] += linear_w[i] * _lns8_to_real(p_s[i, d], p_m[i, d])

        # Systematic resampling
        cumsum = np.cumsum(linear_w)
        cumsum[-1] = 1.0
        u = (rng.uniform() + np.arange(n_particles)) / n_particles
        idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)
        p_s = p_s[idx].copy()
        p_m = p_m[idx].copy()

        # Build record
        particles_dict = {}
        for d in range(6):
            particles_dict[DIM_NAMES[d]] = [
                round(float(_lns8_to_real(p_s[i, d], p_m[i, d])), 4)
                for i in range(n_particles)]

        truth_dict = {}
        est_dict = {}
        rmse_dict = {}
        for d in range(6):
            truth_dict[DIM_NAMES[d]] = round(float(true_state[t + 1, d]), 4)
            est_dict[DIM_NAMES[d]] = round(float(estimate[d]), 4)
            sum_sq_err[d] += (estimate[d] - true_state[t + 1, d])**2
            rmse_dict[DIM_NAMES[d]] = round(float(np.sqrt(sum_sq_err[d] / (t + 1))), 4)

        sensor_dict = {}
        for si, (name, dim, sigma) in enumerate(sensors):
            sensor_dict[name] = round(float(measurements[t, si]), 4)

        cycles = {
            'predict': predict_cycles,
            'weight': weight_cycles,
            'resample': resample_cycles,
            'total': predict_cycles + weight_cycles + resample_cycles,
        }

        records.append({
            "t": t,
            "truth": truth_dict,
            "estimate": est_dict,
            "particles": particles_dict,
            "weights": [round(float(_lns8_to_real(lw_s[i], lw_m[i])), 4)
                        for i in range(n_particles)],
            "sensors": sensor_dict,
            "cycles": cycles,
            "rmse": rmse_dict,
            "method": "python_lns8",
        })

    return records


def real_to_fp16(x):
    """Real value → signed 16-bit fixed-point (8.8 format)."""
    v = int(round(x * 256))
    v = max(-32768, min(32767, v))
    return v & 0xFFFF


def write_init_hex_6d(filename, n_particles, true_state_0, dt,
                      noise_pos_scale, noise_vel_scale, sensors, seed=42):
    """Write initial particle states + constants for 6D RTL.

    Format:
      Section 1: n_particles × 6 lines (per-dim state)
        - Position dims (0,1,2): hi_byte lo_byte (16-bit signed FP, 8.8)
        - Velocity dims (3,4,5): sign mag (LNS8)
      Section 2: constants (DT, NOISE_SCALE_POS, NOISE_SCALE_VEL)
      Section 3: per-sensor (TWO_SIGMA_SQ sign mag, then DIM 00 dim_value)
    """
    rng = np.random.default_rng(seed + 2000)
    with open(filename, 'w') as f:
        # Particle initial states (6 dims per particle)
        for i in range(n_particles):
            for d in range(6):
                x = true_state_0[d] + rng.normal(0, 0.5 if d < 3 else 0.1)
                if abs(x) < 0.01:
                    x = 0.01 if x >= 0 else -0.01
                if d < 3:
                    # Position: 16-bit signed FP (8.8), as hi lo bytes
                    fp = real_to_fp16(x)
                    f.write(f'{(fp >> 8) & 0xFF:02x} {fp & 0xFF:02x}\n')
                else:
                    # Velocity: LNS8 sign + mag
                    hs, hm = real_to_hw(x)
                    f.write(f'{hs:02x} {hm:02x}\n')

        # Constants
        hs, hm = real_to_hw(dt)
        f.write(f'{hs:02x} {hm:02x}\n')  # DT

        hs, hm = real_to_hw(noise_pos_scale)
        f.write(f'{hs:02x} {hm:02x}\n')  # NOISE_SCALE_POS

        hs, hm = real_to_hw(noise_vel_scale)
        f.write(f'{hs:02x} {hm:02x}\n')  # NOISE_SCALE_VEL

        for name, dim, sigma in sensors:
            hs, hm = real_to_hw(2 * sigma**2)
            f.write(f'{hs:02x} {hm:02x}\n')  # TWO_SIGMA_SQ
            f.write(f'00 {dim:02x}\n')        # SENSOR_DIM

    n_lines = n_particles * 6 + 3 + len(sensors) * 2
    print(f"  {filename}: {n_particles} particles × 6 dims + constants ({n_lines} lines)")


def write_sensors_hex(filename, measurements):
    """Write sensor measurements per timestep.

    Format: one line per timestep, each sensor = hw_sign hw_mag.
    """
    n_steps, n_sensors = measurements.shape
    with open(filename, 'w') as f:
        for t in range(n_steps):
            parts = []
            for s in range(n_sensors):
                hs, hm = real_to_hw(measurements[t, s])
                parts.append(f'{hs:02x} {hm:02x}')
            f.write(' '.join(parts) + '\n')

    print(f"  {filename}: {n_steps} timesteps × {n_sensors} sensors")


def write_sensors_fp_hex(filename, measurements):
    """Write sensor measurements as 32-bit signed FP (8.8 extended).

    For delta-encoding: the TB reads these high-precision values, subtracts
    ref_pos, then encodes the offset as LNS8.  This avoids the precision loss
    from encoding large absolute positions in LNS8.

    Format: one line per timestep, each sensor = 8-hex-digit signed FP value.
    """
    n_steps, n_sensors = measurements.shape
    with open(filename, 'w') as f:
        for t in range(n_steps):
            parts = []
            for s in range(n_sensors):
                fp32 = int(round(measurements[t, s] * 256))
                parts.append(f'{fp32 & 0xFFFFFFFF:08x}')
            f.write(' '.join(parts) + '\n')

    print(f"  {filename}: {n_steps} timesteps × {n_sensors} sensors (32-bit FP)")


def write_truth_hex_6d(filename, true_state):
    """Write ground truth per timestep (6 dims).

    Format: one line per timestep, 6 dims × (sign mag).
    """
    n_steps = len(true_state) - 1
    with open(filename, 'w') as f:
        for t in range(n_steps):
            parts = []
            for d in range(6):
                hs, hm = real_to_hw(true_state[t + 1, d])
                parts.append(f'{hs:02x} {hm:02x}')
            f.write(' '.join(parts) + '\n')

    print(f"  {filename}: {n_steps} timesteps × 6 dims")


def run_python_reference_delta_6d(true_state, measurements, sensors, dt,
                                  n_particles, noise_pos_scale, noise_vel_scale,
                                  seed=42):
    """Run 6D bootstrap PF with delta-encoding in Python LNS8."""
    rng = np.random.default_rng(seed + 2000)
    n_steps = len(true_state) - 1
    n_sensors = len(sensors)

    dt_s, dt_m = _real_to_lns8(dt)
    nsp_s, nsp_m = _real_to_lns8(noise_pos_scale)
    nsv_s, nsv_m = _real_to_lns8(noise_vel_scale)
    tss = [_real_to_lns8(2 * sigma**2) for _, _, sigma in sensors]

    # Initialize particles (same RNG as non-delta version)
    p_s = np.zeros((n_particles, 6), dtype=int)
    p_m = np.zeros((n_particles, 6), dtype=int)
    refs = np.zeros(3)  # reference positions (float64)

    for i in range(n_particles):
        for d in range(6):
            x = true_state[0, d] + rng.normal(0, 0.5 if d < 3 else 0.1)
            if abs(x) < 0.01:
                x = 0.01 if x >= 0 else -0.01
            p_s[i, d], p_m[i, d] = _real_to_lns8(x)

    records = []
    sum_sq_err = np.zeros(6)

    for t in range(n_steps):
        # --- Predict ---
        predict_cycles = 0
        for i in range(n_particles):
            for d in range(3):  # position dims
                vdt_s, vdt_m, c = lns8_multiply(
                    p_s[i, d + 3], p_m[i, d + 3], dt_s, dt_m)
                predict_cycles += c
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], vdt_s, vdt_m)
                predict_cycles += c
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s, sn_m, c = lns8_multiply(nsp_s, nsp_m, ns, nm)
                predict_cycles += c
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], sn_s, sn_m)
                predict_cycles += c

            for d in range(3, 6):  # velocity dims
                nr = rng.normal(0, 1.0)
                ns, nm = _real_to_lns8(nr)
                sn_s, sn_m, c = lns8_multiply(nsv_s, nsv_m, ns, nm)
                predict_cycles += c
                p_s[i, d], p_m[i, d], c = lns8_add(
                    p_s[i, d], p_m[i, d], sn_s, sn_m)
                predict_cycles += c

        # --- Weight (measurements adjusted to offset space) ---
        weight_cycles = 0
        lw_s = np.zeros(n_particles, dtype=int)
        lw_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)

        for si, (name, dim, sigma) in enumerate(sensors):
            z_real = measurements[t, si]
            # Convert measurement to offset space for position dims
            if dim < 3:
                z_offset = z_real - refs[dim]
            else:
                z_offset = z_real
            if abs(z_offset) < 0.01:
                z_offset = 0.01 if z_offset >= 0 else -0.01
            z_s, z_m = _real_to_lns8(z_offset)
            tss_s, tss_m = tss[si]

            for i in range(n_particles):
                ds, dm, c1 = lns8_subtract(z_s, z_m, p_s[i, dim], p_m[i, dim])
                sq_s, sq_m, c2 = lns8_multiply(ds, dm, ds, dm)
                div_s, div_m, c3 = lns8_divide(sq_s, sq_m, tss_s, tss_m)
                neg_s = -div_s if div_s != 0 else 0
                lw_s[i], lw_m[i], c4 = lns8_add(lw_s[i], lw_m[i], neg_s, div_m)
                weight_cycles += c1 + c2 + c3 + c4

        # --- Resample ---
        resample_cycles = 6 * n_particles
        linear_w = np.zeros(n_particles)
        max_lw = -np.inf
        for i in range(n_particles):
            lw = _lns8_to_real(lw_s[i], lw_m[i])
            if lw > max_lw:
                max_lw = lw
        for i in range(n_particles):
            lw = _lns8_to_real(lw_s[i], lw_m[i])
            linear_w[i] = np.exp(lw - max_lw)
        w_sum = linear_w.sum()
        if w_sum > 0:
            linear_w /= w_sum
        else:
            linear_w[:] = 1.0 / n_particles

        # Weighted estimate (offsets for pos, absolute for vel)
        offset_est = np.zeros(6)
        for i in range(n_particles):
            for d in range(6):
                offset_est[d] += linear_w[i] * _lns8_to_real(p_s[i, d], p_m[i, d])

        # Absolute estimate
        estimate = offset_est.copy()
        for d in range(3):
            estimate[d] += refs[d]

        # Systematic resampling
        cumsum = np.cumsum(linear_w)
        cumsum[-1] = 1.0
        u = (rng.uniform() + np.arange(n_particles)) / n_particles
        idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)
        p_s = p_s[idx].copy()
        p_m = p_m[idx].copy()

        # --- Re-center: update refs, re-encode offsets ---
        recenter_cycles = 0
        for d in range(3):
            mean_off = offset_est[d]
            refs[d] += mean_off
            for i in range(n_particles):
                real_off = _lns8_to_real(p_s[i, d], p_m[i, d])
                real_off -= mean_off
                if abs(real_off) < 0.001:
                    real_off = 0.001 if real_off >= 0 else -0.001
                p_s[i, d], p_m[i, d] = _real_to_lns8(real_off)
            recenter_cycles += 3 * n_particles

        # Build record
        particles_dict = {}
        for d in range(6):
            particles_dict[DIM_NAMES[d]] = [
                round(float(_lns8_to_real(p_s[i, d], p_m[i, d])), 4)
                for i in range(n_particles)]

        truth_dict = {}
        est_dict = {}
        rmse_dict = {}
        refs_dict = {}
        for d in range(6):
            truth_dict[DIM_NAMES[d]] = round(float(true_state[t + 1, d]), 4)
            est_dict[DIM_NAMES[d]] = round(float(estimate[d]), 4)
            sum_sq_err[d] += (estimate[d] - true_state[t + 1, d])**2
            rmse_dict[DIM_NAMES[d]] = round(float(np.sqrt(sum_sq_err[d] / (t + 1))), 4)
        for d in range(3):
            refs_dict[DIM_NAMES[d]] = round(float(refs[d]), 4)

        sensor_dict = {}
        for si, (name, dim, sigma) in enumerate(sensors):
            sensor_dict[name] = round(float(measurements[t, si]), 4)

        cycles = {
            'predict': predict_cycles,
            'weight': weight_cycles,
            'resample': resample_cycles,
            'recenter': recenter_cycles,
            'total': predict_cycles + weight_cycles + resample_cycles + recenter_cycles,
        }

        records.append({
            "t": t,
            "truth": truth_dict,
            "estimate": est_dict,
            "particles": particles_dict,
            "sensors": sensor_dict,
            "cycles": cycles,
            "rmse": rmse_dict,
            "refs": refs_dict,
            "method": "python_lns8_delta",
        })

    return records


def main():
    parser = argparse.ArgumentParser(
        description='Generate 6D PF scenario for RTL testing')
    parser.add_argument('--steps', type=int, default=100,
                        help='Number of timesteps')
    parser.add_argument('--particles', type=int, default=128,
                        help='Number of particles')
    parser.add_argument('--sensors', type=int, default=3,
                        help='Number of sensors (uses first N from SENSOR_DEFS)')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    dt = 0.5
    noise_pos_scale = 0.5   # LNS8 process noise for position (wind gusts)
    noise_vel_scale = 0.3   # LNS8 process noise for velocity (maneuvering)
    sensors = SENSOR_DEFS[:args.sensors]

    print(f"Generating 6D PF scenario: {args.steps} steps, {args.particles} particles, "
          f"{len(sensors)} sensors")
    for name, dim, sigma in sensors:
        print(f"  Sensor: {name} → dim {dim} ({DIM_NAMES[dim]}), σ={sigma}")

    true_state = generate_trajectory_6d(args.steps, dt, seed=args.seed)
    measurements = generate_measurements_6d(true_state, sensors, seed=args.seed)

    # Write hex files for RTL
    write_init_hex_6d('scenario_init.hex', args.particles, true_state[0],
                      dt, noise_pos_scale, noise_vel_scale, sensors,
                      seed=args.seed)
    write_sensors_hex('scenario_sensors.hex', measurements)
    write_sensors_fp_hex('scenario_sensors_fp.hex', measurements)
    write_truth_hex_6d('scenario_truth.hex', true_state)

    # Write LFSR seed so RTL uses scenario-dependent noise
    with open('scenario_lfsr_seed.hex', 'w') as f:
        f.write(f'{args.seed & 0xFFFFFFFF:08x}\n')
    print(f"  scenario_lfsr_seed.hex: LFSR seed 0x{args.seed & 0xFFFFFFFF:08x}")

    # Run plain Python reference
    print("Running Python LNS8 6D reference (plain)...")
    records_plain = run_python_reference_6d(
        true_state, measurements, sensors, dt,
        args.particles, noise_pos_scale, noise_vel_scale,
        seed=args.seed)

    with open('scenario_ref.jsonl', 'w') as f:
        for rec in records_plain:
            f.write(json.dumps(rec) + '\n')
    print(f"  scenario_ref.jsonl: {len(records_plain)} records")

    # Run delta-encoded Python reference
    print("Running Python LNS8 6D reference (delta-encoded)...")
    records_delta = run_python_reference_delta_6d(
        true_state, measurements, sensors, dt,
        args.particles, noise_pos_scale, noise_vel_scale,
        seed=args.seed)

    with open('scenario_ref_delta.jsonl', 'w') as f:
        for rec in records_delta:
            f.write(json.dumps(rec) + '\n')
    print(f"  scenario_ref_delta.jsonl: {len(records_delta)} records")

    # Summary
    fp = records_plain[-1]
    fd = records_delta[-1]
    print(f"\nPython reference RMSE (plain / delta):")
    for d in DIM_NAMES:
        print(f"  {d:>3s}: {fp['rmse'][d]:.4f} / {fd['rmse'][d]:.4f}")
    print(f"Done.")


if __name__ == '__main__':
    main()
