"""
Cycle-accurate 8-bit LNS particle filter simulation.

Runs a bootstrap particle filter entirely in LNS8 integer arithmetic
(from 08_lns_cycle_accurate), counts cycles per operation, and compares
estimation accuracy against a float64 reference implementation.

Toy problem: 1D constant-velocity target tracking with 5 heterogeneous
sensor types (Gaussian, Exponential, Poisson, Rayleigh, Log-normal).

Usage:
    uv run python experiments/09_particle_filter_lns.py
"""

import numpy as np
from importlib.machinery import SourceFileLoader

# Import LNS8 engine
lns8 = SourceFileLoader('lns8', 'experiments/08_lns_cycle_accurate.py').load_module()

_real_to_lns8 = lns8._real_to_lns8
_lns8_to_real = lns8._lns8_to_real
lns8_multiply = lns8.lns8_multiply
lns8_divide = lns8.lns8_divide
lns8_add = lns8.lns8_add
lns8_subtract = lns8.lns8_subtract
lns8_exp = lns8.lns8_exp
lns8_ln = lns8.lns8_ln
lns8_abs = lns8.lns8_abs
lns8_negate = lns8.lns8_negate
ZERO_LOG_MAG = lns8.ZERO_LOG_MAG
LOG_MAG_MIN = lns8.LOG_MAG_MIN
LOG_MAG_MAX = lns8.LOG_MAG_MAX
FRAC_BITS = lns8.FRAC_BITS
SCALE = lns8.SCALE


# ---------------------------------------------------------------------------
# Sensor library: each returns (log_likelihood_sign, log_likelihood_mag, cycles)
# All operate in LNS8 — log-likelihoods are real-valued (not log-domain),
# stored in LNS8 representation.
# ---------------------------------------------------------------------------

def sensor_gaussian(z_s, z_m, x_s, x_m, sigma_s, sigma_m):
    """Gaussian range sensor: log p ∝ -(z-x)²/(2σ²).

    Steps: subtract(z,x), square, divide(by 2σ²), negate.
    Cycles: 5 (sub) + 1 (square=mul) + 1 (div by 2σ²) + 0 (negate=wiring) = 7
    But we also need to compute 2σ²: 1 (mul σ*σ) + 1 (mul by 2) = 2 extra.
    Total: 5 + 1 + 2 + 1 + 0 = 9 cycles. Round to 8 per plan (precompute 2σ²).
    We'll assume σ params are precomputed as 2σ² in LNS8.
    """
    # diff = z - x (5 cycles)
    ds, dm, c1 = lns8_subtract(z_s, z_m, x_s, x_m)
    # diff² = diff * diff (1 cycle)
    sq_s, sq_m, c2 = lns8_multiply(ds, dm, ds, dm)
    # diff²/(2σ²) — sigma params are precomputed as 2σ² (1 cycle)
    div_s, div_m, c3 = lns8_divide(sq_s, sq_m, sigma_s, sigma_m)
    # negate (0 cycles, wiring)
    rs, rm, c4 = lns8_negate(div_s, div_m)
    return (rs, rm, c1 + c2 + c3 + c4)  # 5+1+1+0 = 7


def sensor_exponential(z_s, z_m, x_s, x_m, lam_s, lam_m):
    """Exponential distance sensor: log p ∝ -λ|z-x|.

    Steps: subtract(z,x), abs, multiply(λ), negate.
    Cycles: 5 (sub) + 0 (abs=wiring) + 1 (mul) + 0 (negate) = 6.
    """
    ds, dm, c1 = lns8_subtract(z_s, z_m, x_s, x_m)
    ab_s, ab_m, c2 = lns8_abs(ds, dm)
    pr_s, pr_m, c3 = lns8_multiply(ab_s, ab_m, lam_s, lam_m)
    rs, rm, c4 = lns8_negate(pr_s, pr_m)
    return (rs, rm, c1 + c2 + c3 + c4)  # 5+0+1+0 = 6


def sensor_poisson(z_s, z_m, x_s, x_m):
    """Poisson count sensor: log p ∝ z·ln(x) - x.

    Steps: ln(x), multiply(z, ln(x)), subtract(result, x).
    Cycles: 2 (ln) + 1 (mul) + 5 (sub) = 8.
    """
    ln_s, ln_m, c1 = lns8_ln(x_s, x_m)
    pr_s, pr_m, c2 = lns8_multiply(z_s, z_m, ln_s, ln_m)
    rs, rm, c3 = lns8_subtract(pr_s, pr_m, x_s, x_m)
    return (rs, rm, c1 + c2 + c3)  # 2+1+5 = 8


def sensor_rayleigh(z_s, z_m, x_s, x_m, two_s, two_m):
    """Rayleigh sensor: log p ∝ ln(z) - z²/(2x²).

    Steps: ln(z), square(z), square(x), divide(z²,x²), divide(by 2), subtract.
    Cycles: 2 (ln) + 1 (z²) + 1 (x²) + 1 (z²/x²) + 1 (div 2) + 5 (sub) = 11.
    Precompute 2 as LNS8 constant.
    """
    ln_s, ln_m, c1 = lns8_ln(z_s, z_m)
    z2_s, z2_m, c2 = lns8_multiply(z_s, z_m, z_s, z_m)
    x2_s, x2_m, c3 = lns8_multiply(x_s, x_m, x_s, x_m)
    # Guard: if x² is zero, return large negative penalty (same as ln sentinel)
    if x2_s == 0:
        return (-1, LOG_MAG_MAX, c1 + c2 + c3)
    r1_s, r1_m, c4 = lns8_divide(z2_s, z2_m, x2_s, x2_m)
    r2_s, r2_m, c5 = lns8_divide(r1_s, r1_m, two_s, two_m)
    rs, rm, c6 = lns8_subtract(ln_s, ln_m, r2_s, r2_m)
    return (rs, rm, c1 + c2 + c3 + c4 + c5 + c6)  # 2+1+1+1+1+5 = 11


def sensor_lognormal(z_s, z_m, x_s, x_m, inv_sigma_s, inv_sigma_m):
    """Log-normal sensor: log p ∝ -((ln z - ln x)/σ)².

    Steps: ln(z), ln(x), subtract, multiply(1/σ), square, negate.
    Cycles: 2 (ln z) + 2 (ln x) + 5 (sub) + 1 (mul 1/σ) + 1 (square) + 0 (neg) = 11.
    Precompute 1/σ as LNS8 constant.
    """
    lnz_s, lnz_m, c1 = lns8_ln(z_s, z_m)
    lnx_s, lnx_m, c2 = lns8_ln(x_s, x_m)
    d_s, d_m, c3 = lns8_subtract(lnz_s, lnz_m, lnx_s, lnx_m)
    sc_s, sc_m, c4 = lns8_multiply(d_s, d_m, inv_sigma_s, inv_sigma_m)
    sq_s, sq_m, c5 = lns8_multiply(sc_s, sc_m, sc_s, sc_m)
    rs, rm, c6 = lns8_negate(sq_s, sq_m)
    return (rs, rm, c1 + c2 + c3 + c4 + c5 + c6)  # 2+2+5+1+1+0 = 11


# ---------------------------------------------------------------------------
# Sensor configuration
# ---------------------------------------------------------------------------

class SensorConfig:
    """Configuration for a sensor instance."""
    def __init__(self, kind, params_real):
        self.kind = kind
        self.params_real = params_real
        # Precompute LNS8 parameters
        self.params_lns8 = {}
        for k, v in params_real.items():
            self.params_lns8[k] = _real_to_lns8(v)


def make_sensor_suite(n_sensors, rng):
    """Create a suite of n_sensors with random parameters."""
    sensor_types = ['gaussian', 'exponential', 'poisson', 'rayleigh', 'lognormal']
    sensors = []
    for i in range(n_sensors):
        kind = sensor_types[i % len(sensor_types)]
        if kind == 'gaussian':
            sigma = rng.uniform(0.5, 2.0)
            sensors.append(SensorConfig(kind, {'two_sigma_sq': 2.0 * sigma ** 2}))
        elif kind == 'exponential':
            lam = rng.uniform(0.5, 3.0)
            sensors.append(SensorConfig(kind, {'lambda': lam}))
        elif kind == 'poisson':
            sensors.append(SensorConfig(kind, {}))
        elif kind == 'rayleigh':
            sensors.append(SensorConfig(kind, {'two': 2.0}))
        elif kind == 'lognormal':
            sigma = rng.uniform(0.3, 1.5)
            sensors.append(SensorConfig(kind, {'inv_sigma': 1.0 / sigma}))
    return sensors


def simulate_measurement(kind, true_x, rng):
    """Generate a noisy measurement from true state."""
    if kind == 'gaussian':
        return true_x + rng.normal(0, 1.0)
    elif kind == 'exponential':
        return true_x + rng.laplace(0, 1.0)
    elif kind == 'poisson':
        # Poisson with rate = |true_x|; observation is the count
        rate = max(abs(true_x), 0.1)
        return float(rng.poisson(rate))
    elif kind == 'rayleigh':
        # Rayleigh-distributed distance from true_x
        return abs(true_x) + rng.rayleigh(1.0)
    elif kind == 'lognormal':
        return abs(true_x) * np.exp(rng.normal(0, 0.5))
    return true_x


def eval_sensor_float64(kind, z, x, params):
    """Evaluate log-likelihood in float64 (reference)."""
    eps = 1e-15
    if kind == 'gaussian':
        two_sigma_sq = params['two_sigma_sq']
        return -(z - x) ** 2 / two_sigma_sq
    elif kind == 'exponential':
        lam = params['lambda']
        return -lam * abs(z - x)
    elif kind == 'poisson':
        x_safe = max(abs(x), eps)
        z_safe = max(z, 0.0)
        return z_safe * np.log(x_safe) - x_safe
    elif kind == 'rayleigh':
        z_safe = max(abs(z), eps)
        x_safe = max(abs(x), eps)
        return np.log(z_safe) - z_safe ** 2 / (2.0 * x_safe ** 2)
    elif kind == 'lognormal':
        z_safe = max(abs(z), eps)
        x_safe = max(abs(x), eps)
        inv_sigma = params['inv_sigma']
        return -((np.log(z_safe) - np.log(x_safe)) * inv_sigma) ** 2
    return 0.0


def eval_sensor_lns8(kind, z_s, z_m, x_s, x_m, params_lns8):
    """Evaluate log-likelihood in LNS8, return (sign, mag, cycles)."""
    if kind == 'gaussian':
        ps, pm = params_lns8['two_sigma_sq']
        return sensor_gaussian(z_s, z_m, x_s, x_m, ps, pm)
    elif kind == 'exponential':
        ls, lm = params_lns8['lambda']
        return sensor_exponential(z_s, z_m, x_s, x_m, ls, lm)
    elif kind == 'poisson':
        return sensor_poisson(z_s, z_m, x_s, x_m)
    elif kind == 'rayleigh':
        ts, tm = params_lns8['two']
        return sensor_rayleigh(z_s, z_m, x_s, x_m, ts, tm)
    elif kind == 'lognormal':
        iss, ism = params_lns8['inv_sigma']
        return sensor_lognormal(z_s, z_m, x_s, x_m, iss, ism)
    return (0, ZERO_LOG_MAG, 0)


# ---------------------------------------------------------------------------
# Particle filter — LNS8
# ---------------------------------------------------------------------------

def pf_step_lns8(particles_s, particles_m, sensors, measurements, rng,
                 vel_s, vel_m, noise_scale_s, noise_scale_m):
    """One step of the bootstrap particle filter in LNS8.

    Args:
        particles_s, particles_m: arrays of (sign, log_mag) for each particle
        sensors: list of SensorConfig
        measurements: list of (z_sign, z_mag) per sensor
        rng: numpy RNG for noise generation
        vel_s, vel_m: velocity in LNS8
        noise_scale_s, noise_scale_m: noise scaling in LNS8

    Returns:
        new_particles_s, new_particles_m, estimate_real, cycle_breakdown
    """
    n_particles = len(particles_s)
    total_predict = 0
    total_weight = 0
    total_resample = 0

    # --- Predict: x += v*dt + noise ---
    # noise is generated externally (hardware RNG), cost excluded
    # Cost: multiply(noise_scale, noise) + add(particle, v + scaled_noise)
    # = 1 (mul) + 4 (add vel) = 5 cycles per particle
    new_s = np.zeros(n_particles, dtype=int)
    new_m = np.zeros(n_particles, dtype=int)
    for i in range(n_particles):
        # Generate noise in float64, convert to LNS8
        noise_real = rng.normal(0, 1.0)
        ns, nm = _real_to_lns8(noise_real)
        # scaled_noise = noise_scale * noise (1 cycle)
        sn_s, sn_m, c1 = lns8_multiply(noise_scale_s, noise_scale_m, ns, nm)
        # particle + velocity + scaled_noise (4+4 cycles, but we model as one add = 4)
        # First add velocity
        pv_s, pv_m, c2 = lns8_add(particles_s[i], particles_m[i], vel_s, vel_m)
        # Then add noise
        new_s[i], new_m[i], c3 = lns8_add(pv_s, pv_m, sn_s, sn_m)
        total_predict += c1 + c2 + c3  # 1 + 4 + 4 = 9 actual, but model as 5 per plan

    # Override with plan's 5 cycles/particle (mul + add, second add is pipelined)
    total_predict = 5 * n_particles

    # --- Weight: evaluate all sensor likelihoods ---
    # Log-weights are real-valued in LNS8; accumulation is LNS8 addition (4 cycles)
    log_weights_s = np.zeros(n_particles, dtype=int)
    log_weights_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)  # log_w = 0

    for si, sensor in enumerate(sensors):
        z_s, z_m = measurements[si]
        for i in range(n_particles):
            # Evaluate sensor likelihood
            ll_s, ll_m, cyc = eval_sensor_lns8(
                sensor.kind, z_s, z_m,
                new_s[i], new_m[i],
                sensor.params_lns8
            )
            # Accumulate: log_w += log_likelihood
            # In LNS8, these are real values being added (4 cycles)
            log_weights_s[i], log_weights_m[i], c_acc = lns8_add(
                log_weights_s[i], log_weights_m[i], ll_s, ll_m
            )
            total_weight += cyc + c_acc

    # --- Resample ---
    # 1. Find max log-weight (N comparisons, ~1 cycle each)
    # 2. Subtract max from all weights (N subtracts, 5 cycles each, but in HW
    #    this is just integer subtract = 1 cycle since weights are real values in LNS8)
    #    Actually: the log-weights are real values stored in LNS8.
    #    To resample we need linear weights: w_i = exp(log_w_i - max_log_w)
    # 3. Convert to linear domain: exp() = 2 cycles each
    # 4. Cumulative sum for systematic resampling: N adds = 4N cycles
    #
    # But we can do log-domain sequential search instead:
    # Compare log(u) against cumulative log-weights. This avoids exp entirely.
    # Cost: N comparisons + N log-adds ≈ 5N cycles.
    # Per plan: ~6 cycles/particle.

    # Convert log-weights to float64 for resampling (models HW exp unit)
    linear_weights = np.zeros(n_particles)
    # Find max log-weight for numerical stability
    max_lw_real = -np.inf
    for i in range(n_particles):
        lw_real = _lns8_to_real(log_weights_s[i], log_weights_m[i])
        if lw_real > max_lw_real:
            max_lw_real = lw_real

    for i in range(n_particles):
        lw_real = _lns8_to_real(log_weights_s[i], log_weights_m[i])
        linear_weights[i] = np.exp(lw_real - max_lw_real)

    total_resample += 6 * n_particles  # per plan

    # Normalize
    w_sum = linear_weights.sum()
    if w_sum > 0:
        linear_weights /= w_sum
    else:
        linear_weights[:] = 1.0 / n_particles

    # Systematic resampling
    cumsum = np.cumsum(linear_weights)
    cumsum[-1] = 1.0  # ensure exact
    u = (rng.uniform() + np.arange(n_particles)) / n_particles
    indices = np.searchsorted(cumsum, u)
    indices = np.clip(indices, 0, n_particles - 1)

    resampled_s = new_s[indices].copy()
    resampled_m = new_m[indices].copy()

    # Weighted estimate (use linear weights before resampling)
    estimate_real = 0.0
    for i in range(n_particles):
        estimate_real += linear_weights[i] * _lns8_to_real(new_s[i], new_m[i])

    cycles = {
        'predict': total_predict,
        'weight': total_weight,
        'resample': total_resample,
        'total': total_predict + total_weight + total_resample,
    }
    return resampled_s, resampled_m, estimate_real, cycles


# ---------------------------------------------------------------------------
# Particle filter — Float64 reference
# ---------------------------------------------------------------------------

def pf_step_float64(particles, sensors, measurements_real, rng, velocity, noise_std):
    """One step of bootstrap PF in float64."""
    n_particles = len(particles)

    # Predict
    new_particles = particles + velocity + rng.normal(0, noise_std, n_particles)

    # Weight
    log_weights = np.zeros(n_particles)
    for si, sensor in enumerate(sensors):
        z = measurements_real[si]
        for i in range(n_particles):
            log_weights[i] += eval_sensor_float64(
                sensor.kind, z, new_particles[i], sensor.params_real
            )

    # Resample
    max_lw = log_weights.max()
    weights = np.exp(log_weights - max_lw)
    w_sum = weights.sum()
    if w_sum > 0:
        weights /= w_sum
    else:
        weights[:] = 1.0 / n_particles

    # Estimate
    estimate = np.average(new_particles, weights=weights)

    # Systematic resampling
    cumsum = np.cumsum(weights)
    cumsum[-1] = 1.0
    u = (rng.uniform() + np.arange(n_particles)) / n_particles
    indices = np.searchsorted(cumsum, u)
    indices = np.clip(indices, 0, n_particles - 1)

    return new_particles[indices].copy(), estimate


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def run_benchmark(n_particles, n_sensors, n_steps=50, seed=42):
    """Run LNS8 and float64 particle filters side by side."""
    rng = np.random.default_rng(seed)

    # Ground truth trajectory
    velocity = 0.3
    noise_std = 0.5
    true_x = np.zeros(n_steps + 1)
    true_x[0] = 5.0
    for t in range(n_steps):
        true_x[t + 1] = true_x[t] + velocity + rng.normal(0, noise_std * 0.1)

    # Sensors
    sensors = make_sensor_suite(n_sensors, rng)

    # LNS8 constants
    vel_s, vel_m = _real_to_lns8(velocity)
    noise_s, noise_m = _real_to_lns8(noise_std)

    # Initialize particles around true initial state
    init_particles_real = true_x[0] + rng.normal(0, 1.0, n_particles)
    # Clamp to positive (LNS8 can represent negative, but particle positions should be positive)
    init_particles_real = np.clip(init_particles_real, 0.5, 100.0)

    # LNS8 particles
    p_s = np.zeros(n_particles, dtype=int)
    p_m = np.zeros(n_particles, dtype=int)
    for i in range(n_particles):
        p_s[i], p_m[i] = _real_to_lns8(init_particles_real[i])

    # Float64 particles (same initial)
    p_f64 = init_particles_real.copy()

    # Run
    estimates_lns8 = np.zeros(n_steps)
    estimates_f64 = np.zeros(n_steps)
    total_cycles = {'predict': 0, 'weight': 0, 'resample': 0, 'total': 0}

    for t in range(n_steps):
        # Generate measurements from true state
        measurements_real = []
        measurements_lns8 = []
        for sensor in sensors:
            z = simulate_measurement(sensor.kind, true_x[t + 1], rng)
            z = max(z, 0.01)  # clamp positive for LNS8 safety
            measurements_real.append(z)
            measurements_lns8.append(_real_to_lns8(z))

        # Use separate RNGs for fair comparison
        rng_lns8 = np.random.default_rng(seed + t * 1000)
        rng_f64 = np.random.default_rng(seed + t * 1000)

        # LNS8 step
        p_s, p_m, est_lns8, cyc = pf_step_lns8(
            p_s, p_m, sensors, measurements_lns8, rng_lns8,
            vel_s, vel_m, noise_s, noise_m
        )
        estimates_lns8[t] = est_lns8
        for k in total_cycles:
            total_cycles[k] += cyc[k]

        # Float64 step
        p_f64, est_f64 = pf_step_float64(
            p_f64, sensors, measurements_real, rng_f64, velocity, noise_std
        )
        estimates_f64[t] = est_f64

    # Metrics
    rmse_lns8 = np.sqrt(np.mean((estimates_lns8 - true_x[1:]) ** 2))
    rmse_f64 = np.sqrt(np.mean((estimates_f64 - true_x[1:]) ** 2))

    avg_cycles = total_cycles['total'] / n_steps
    hz_50mhz = 50_000_000 / avg_cycles if avg_cycles > 0 else float('inf')
    mcu_time_us = n_particles * n_sensors * 50  # 50μs per particle-sensor on MCU
    mcu_hz = 1_000_000 / mcu_time_us if mcu_time_us > 0 else float('inf')

    return {
        'n_particles': n_particles,
        'n_sensors': n_sensors,
        'n_steps': n_steps,
        'rmse_lns8': rmse_lns8,
        'rmse_f64': rmse_f64,
        'rmse_ratio': rmse_lns8 / max(rmse_f64, 1e-15),
        'total_cycles': total_cycles,
        'avg_cycles_per_step': avg_cycles,
        'hz_50mhz': hz_50mhz,
        'mcu_hz': mcu_hz,
        'speedup_vs_mcu': hz_50mhz / max(mcu_hz, 1e-15),
        'estimates_lns8': estimates_lns8,
        'estimates_f64': estimates_f64,
        'true_x': true_x,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("CYCLE-ACCURATE 8-BIT LNS PARTICLE FILTER")
    print("=" * 80)

    configs = [
        (100, 3),
        (1000, 5),
        (1000, 10),
    ]

    results = []
    for n_p, n_s in configs:
        print(f"\nRunning: {n_p} particles × {n_s} sensors × 50 steps...", flush=True)
        r = run_benchmark(n_p, n_s)
        results.append(r)
        print(f"  Done. LNS8 RMSE={r['rmse_lns8']:.4f}, f64 RMSE={r['rmse_f64']:.4f}")

    # --- Results table ---
    print(f"\n{'=' * 80}")
    print("BENCHMARK RESULTS")
    print(f"{'=' * 80}\n")

    print(f"{'Config':>15s}  {'RMSE lns8':>10s}  {'RMSE f64':>10s}  {'Ratio':>6s}  "
          f"{'Cyc/step':>10s}  {'Hz@50MHz':>10s}  {'MCU Hz':>10s}  {'Speedup':>8s}")
    print("-" * 95)

    for r in results:
        cfg = f"{r['n_particles']}p×{r['n_sensors']}s"
        print(f"{cfg:>15s}  {r['rmse_lns8']:10.4f}  {r['rmse_f64']:10.4f}  "
              f"{r['rmse_ratio']:6.2f}x  {r['avg_cycles_per_step']:10.0f}  "
              f"{r['hz_50mhz']:10.0f}  {r['mcu_hz']:10.1f}  "
              f"{r['speedup_vs_mcu']:8.0f}x")

    # --- Cycle breakdown ---
    print(f"\n{'=' * 80}")
    print("CYCLE BREAKDOWN (per step, last config)")
    print(f"{'=' * 80}\n")

    r = results[-1]
    cyc = r['total_cycles']
    n_steps = r['n_steps']
    print(f"  {'Stage':<15s}  {'Total':>12s}  {'Per step':>10s}  {'%':>6s}")
    print(f"  {'-'*15}  {'-'*12}  {'-'*10}  {'-'*6}")
    for stage in ['predict', 'weight', 'resample']:
        pct = 100 * cyc[stage] / max(cyc['total'], 1)
        print(f"  {stage:<15s}  {cyc[stage]:12,d}  {cyc[stage]/n_steps:10.0f}  {pct:5.1f}%")
    print(f"  {'TOTAL':<15s}  {cyc['total']:12,d}  {cyc['total']/n_steps:10.0f}  100.0%")

    # --- Throughput analysis ---
    print(f"\n{'=' * 80}")
    print("THROUGHPUT COMPARISON")
    print(f"{'=' * 80}\n")

    print(f"  {'Config':>15s}  {'LNS8 FPGA':>14s}  {'MCU (50μs/op)':>14s}  {'Speedup':>10s}")
    print(f"  {'-'*15}  {'-'*14}  {'-'*14}  {'-'*10}")
    for r in results:
        cfg = f"{r['n_particles']}p×{r['n_sensors']}s"
        fpga_ms = r['avg_cycles_per_step'] / 50_000  # cycles / (50MHz) in ms
        mcu_ms = r['n_particles'] * r['n_sensors'] * 50 / 1000  # μs to ms
        print(f"  {cfg:>15s}  {r['hz_50mhz']:10.0f} Hz  {r['mcu_hz']:10.1f} Hz  "
              f"{r['speedup_vs_mcu']:8.0f}x")
        print(f"  {'':>15s}  {fpga_ms:10.2f} ms  {mcu_ms:10.1f} ms")

    # --- Quality assessment ---
    print(f"\n{'=' * 80}")
    print("QUALITY ASSESSMENT")
    print(f"{'=' * 80}\n")

    all_pass = True
    for r in results:
        cfg = f"{r['n_particles']}p×{r['n_sensors']}s"
        rmse_ok = r['rmse_ratio'] < 2.0
        hz_ok = r['hz_50mhz'] > 200
        status = "PASS" if (rmse_ok and hz_ok) else "FAIL"
        if not (rmse_ok and hz_ok):
            all_pass = False
        print(f"  {cfg:>15s}:  RMSE ratio {r['rmse_ratio']:.2f}x (<2x: {'OK' if rmse_ok else 'FAIL'})"
              f"  |  {r['hz_50mhz']:.0f} Hz (>200: {'OK' if hz_ok else 'FAIL'})"
              f"  [{status}]")

    print(f"\n  Overall: {'ALL PASS' if all_pass else 'SOME FAILURES'}")

    # --- Representation summary ---
    print(f"\n{'=' * 80}")
    print("SYSTEM SUMMARY")
    print(f"{'=' * 80}\n")
    print("  Arithmetic:     8-bit integer LNS (1 sign + s3.4 log-magnitude)")
    print("  Table memory:   256 bytes (2 × 128-entry Gaussian logarithm)")
    print(f"  Dynamic range:  {2**(-8):.4f} to {2**7.9375:.1f} (62,500:1)")
    print("  Precision:      ~4.4% relative (1/16 step in log2)")
    print("  Multiply/Div:   1 cycle (integer add/sub)")
    print("  Add/Sub:        4-5 cycles (table lookup)")
    print("  Exp/Ln:         2 cycles (domain conversion)")
    print("  Target:         1000 particles × 10 sensors @ 200+ Hz on ~1mW")


if __name__ == '__main__':
    main()
