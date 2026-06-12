"""
3D drone particle filter — LNS8 stress test.

6D state [x, y, z, vx, vy, vz] with heterogeneous sensors.
Two scenarios expose LNS8 precision limits:
  Near-origin (5m):  position LSB ~0.22m — velocity updates barely visible
  Far-origin (50m):  position LSB ~2.2m — velocity updates completely invisible

The predict step effectively freezes: position updates from velocity are
below quantization threshold. The filter works only because sensors keep
correcting. This is the fundamental limitation of uniform-relative-precision
arithmetic for state estimation with additive dynamics.

Usage:
    uv run python experiments/10_drone_pf_lns.py
    uv run python experiments/10_drone_pf_lns.py --stream --method delta
    uv run python experiments/10_drone_pf_lns.py --stream | uv run python experiments/11_pf_dashboard.py
"""

import numpy as np
from importlib.machinery import SourceFileLoader

lns8 = SourceFileLoader('lns8', 'experiments/08_lns_cycle_accurate.py').load_module()

_real_to_lns8 = lns8._real_to_lns8
_lns8_to_real = lns8._lns8_to_real
lns8_multiply = lns8.lns8_multiply
lns8_divide = lns8.lns8_divide
lns8_add = lns8.lns8_add
lns8_subtract = lns8.lns8_subtract
lns8_ln = lns8.lns8_ln
lns8_abs = lns8.lns8_abs
lns8_negate = lns8.lns8_negate
ZERO_LOG_MAG = lns8.ZERO_LOG_MAG
LOG_MAG_MAX = lns8.LOG_MAG_MAX
LOG_MAG_MIN = lns8.LOG_MAG_MIN
FRAC_BITS = lns8.FRAC_BITS
SCALE = lns8.SCALE

# State dimensions
DIM_X, DIM_Y, DIM_Z, DIM_VX, DIM_VY, DIM_VZ = range(6)
DIM_NAMES = ['x', 'y', 'z', 'vx', 'vy', 'vz']
N_DIMS = 6

# Position and velocity dimension indices
POS_DIMS = [DIM_X, DIM_Y, DIM_Z]
VEL_DIMS = [DIM_VX, DIM_VY, DIM_VZ]


# ---------------------------------------------------------------------------
# LNS8 sensor functions
# ---------------------------------------------------------------------------

def sensor_gaussian_lns8(z_s, z_m, x_s, x_m, tss_s, tss_m):
    """Gaussian: log p ∝ -(z-x)²/(2σ²). 7 cycles.
    tss = precomputed 2σ².
    """
    ds, dm, c1 = lns8_subtract(z_s, z_m, x_s, x_m)           # 5
    sq_s, sq_m, c2 = lns8_multiply(ds, dm, ds, dm)            # 1
    div_s, div_m, c3 = lns8_divide(sq_s, sq_m, tss_s, tss_m)  # 1
    rs, rm, c4 = lns8_negate(div_s, div_m)                    # 0
    return (rs, rm, c1 + c2 + c3 + c4)


def sensor_lognormal_lns8(z_s, z_m, x_s, x_m, isig_s, isig_m):
    """Log-normal: log p ∝ -((ln z - ln x)/σ)². 11 cycles.
    isig = precomputed 1/σ. Inputs must be positive.
    """
    lnz_s, lnz_m, c1 = lns8_ln(z_s, z_m)                       # 2
    lnx_s, lnx_m, c2 = lns8_ln(x_s, x_m)                       # 2
    d_s, d_m, c3 = lns8_subtract(lnz_s, lnz_m, lnx_s, lnx_m)  # 5
    sc_s, sc_m, c4 = lns8_multiply(d_s, d_m, isig_s, isig_m)    # 1
    sq_s, sq_m, c5 = lns8_multiply(sc_s, sc_m, sc_s, sc_m)      # 1
    rs, rm, c6 = lns8_negate(sq_s, sq_m)                        # 0
    return (rs, rm, c1 + c2 + c3 + c4 + c5 + c6)


def sensor_exponential_lns8(z_s, z_m, x_s, x_m, lam_s, lam_m):
    """Exponential: log p ∝ -λ|z-x|. 6 cycles."""
    ds, dm, c1 = lns8_subtract(z_s, z_m, x_s, x_m)            # 5
    ab_s, ab_m, c2 = lns8_abs(ds, dm)                          # 0
    pr_s, pr_m, c3 = lns8_multiply(ab_s, ab_m, lam_s, lam_m)  # 1
    rs, rm, c4 = lns8_negate(pr_s, pr_m)                       # 0
    return (rs, rm, c1 + c2 + c3 + c4)


# ---------------------------------------------------------------------------
# Sensor configuration
# ---------------------------------------------------------------------------

class Sensor:
    """One sensor observing one state dimension."""
    def __init__(self, name, kind, state_dim, params_real, use_abs=False):
        self.name = name
        self.kind = kind
        self.state_dim = state_dim
        self.params_real = params_real
        self.use_abs = use_abs  # take |state| before evaluation (for speed sensors)
        self.params_lns8 = {k: _real_to_lns8(v) for k, v in params_real.items()}


def make_drone_sensors():
    """Drone sensor suite: GPS, baro, speed, sonar."""
    return [
        Sensor('GPS-x',     'gaussian',    DIM_X,  {'tss': 2 * 2.0**2}),
        Sensor('GPS-y',     'gaussian',    DIM_Y,  {'tss': 2 * 2.0**2}),
        Sensor('Baro-z',    'gaussian',    DIM_Z,  {'tss': 2 * 0.5**2}),
        Sensor('Speed-|vx|','lognormal',   DIM_VX, {'isig': 1.0 / 0.3}, use_abs=True),
        Sensor('Speed-|vy|','lognormal',   DIM_VY, {'isig': 1.0 / 0.3}, use_abs=True),
        Sensor('Sonar-z',   'exponential', DIM_Z,  {'lam': 2.0}),
    ]


def simulate_measurement(sensor, true_state, rng):
    """Generate a noisy measurement from true state."""
    x = true_state[sensor.state_dim]
    if sensor.use_abs:
        x = abs(x)
    if sensor.kind == 'gaussian':
        sigma = np.sqrt(sensor.params_real['tss'] / 2)
        return x + rng.normal(0, sigma)
    elif sensor.kind == 'lognormal':
        sigma = 1.0 / sensor.params_real['isig']
        return max(abs(x), 0.01) * np.exp(rng.normal(0, sigma))
    elif sensor.kind == 'exponential':
        lam = sensor.params_real['lam']
        return x + rng.laplace(0, 1.0 / lam)
    return x


def eval_sensor_float64(sensor, z, x):
    """Evaluate log-likelihood in float64."""
    eps = 1e-15
    if sensor.use_abs:
        x = abs(x)
    if sensor.kind == 'gaussian':
        tss = sensor.params_real['tss']
        return -(z - x)**2 / tss
    elif sensor.kind == 'lognormal':
        isig = sensor.params_real['isig']
        z_safe = max(abs(z), eps)
        x_safe = max(abs(x), eps)
        return -((np.log(z_safe) - np.log(x_safe)) * isig)**2
    elif sensor.kind == 'exponential':
        lam = sensor.params_real['lam']
        return -lam * abs(z - x)
    return 0.0


def eval_sensor_lns8(sensor, z_s, z_m, x_s, x_m):
    """Evaluate log-likelihood in LNS8. Returns (sign, mag, cycles)."""
    # Apply abs if needed
    if sensor.use_abs:
        x_s, x_m, _ = lns8_abs(x_s, x_m)
    p = sensor.params_lns8
    if sensor.kind == 'gaussian':
        ps, pm = p['tss']
        return sensor_gaussian_lns8(z_s, z_m, x_s, x_m, ps, pm)
    elif sensor.kind == 'lognormal':
        iss, ism = p['isig']
        return sensor_lognormal_lns8(z_s, z_m, x_s, x_m, iss, ism)
    elif sensor.kind == 'exponential':
        ls, lm = p['lam']
        return sensor_exponential_lns8(z_s, z_m, x_s, x_m, ls, lm)
    return (0, ZERO_LOG_MAG, 0)


# ---------------------------------------------------------------------------
# 6D Particle filter — LNS8
# ---------------------------------------------------------------------------

def pf_step_lns8(signs, mags, sensors, measurements_lns8, dt_s, dt_m,
                 noise_pos_s, noise_pos_m, noise_vel_s, noise_vel_m, rng):
    """One step of 6D bootstrap PF in LNS8.

    signs, mags: (N_DIMS, n_particles) arrays.
    Returns: new_signs, new_mags, estimate_real (6D), cycle_breakdown.
    """
    n_particles = signs.shape[1]
    new_signs = signs.copy()
    new_mags = mags.copy()
    predict_cycles = 0

    # --- Predict ---
    for i in range(n_particles):
        for d_pos, d_vel in zip(POS_DIMS, VEL_DIMS):
            # v * dt (1 cycle)
            vdt_s, vdt_m, c = lns8_multiply(
                new_signs[d_vel, i], new_mags[d_vel, i], dt_s, dt_m)
            predict_cycles += c
            # pos += v*dt (4 cycles)
            new_signs[d_pos, i], new_mags[d_pos, i], c = lns8_add(
                new_signs[d_pos, i], new_mags[d_pos, i], vdt_s, vdt_m)
            predict_cycles += c
            # pos += noise (4 cycles)
            noise_r = rng.normal(0, 1.0)
            ns, nm = _real_to_lns8(noise_r)
            sn_s, sn_m, c = lns8_multiply(noise_pos_s, noise_pos_m, ns, nm)
            predict_cycles += c
            new_signs[d_pos, i], new_mags[d_pos, i], c = lns8_add(
                new_signs[d_pos, i], new_mags[d_pos, i], sn_s, sn_m)
            predict_cycles += c

        for d_vel in VEL_DIMS:
            # vel += noise (4 cycles)
            noise_r = rng.normal(0, 1.0)
            ns, nm = _real_to_lns8(noise_r)
            sn_s, sn_m, c = lns8_multiply(noise_vel_s, noise_vel_m, ns, nm)
            predict_cycles += c
            new_signs[d_vel, i], new_mags[d_vel, i], c = lns8_add(
                new_signs[d_vel, i], new_mags[d_vel, i], sn_s, sn_m)
            predict_cycles += c

    # --- Weight ---
    weight_cycles = 0
    lw_s = np.zeros(n_particles, dtype=int)
    lw_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)

    for si, sensor in enumerate(sensors):
        z_s, z_m = measurements_lns8[si]
        d = sensor.state_dim
        for i in range(n_particles):
            ll_s, ll_m, cyc = eval_sensor_lns8(
                sensor, z_s, z_m, new_signs[d, i], new_mags[d, i])
            lw_s[i], lw_m[i], c_acc = lns8_add(lw_s[i], lw_m[i], ll_s, ll_m)
            weight_cycles += cyc + c_acc

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

    # Weighted estimate
    estimate = np.zeros(N_DIMS)
    for d in range(N_DIMS):
        for i in range(n_particles):
            estimate[d] += linear_w[i] * _lns8_to_real(new_signs[d, i], new_mags[d, i])

    # Systematic resampling
    cumsum = np.cumsum(linear_w)
    cumsum[-1] = 1.0
    u = (rng.uniform() + np.arange(n_particles)) / n_particles
    idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)

    cycles = {
        'predict': predict_cycles,
        'weight': weight_cycles,
        'resample': resample_cycles,
        'total': predict_cycles + weight_cycles + resample_cycles,
    }
    return new_signs[:, idx].copy(), new_mags[:, idx].copy(), estimate, cycles


# ---------------------------------------------------------------------------
# 6D Particle filter — LNS8 with delta-encoding
# ---------------------------------------------------------------------------
# Position dimensions store offsets from a reference point (float64 register).
# Offsets stay small → LNS8 resolves velocity updates at any absolute position.
# Hardware cost: 3 extra 16-bit registers (one per position axis).
# Re-centering cost: 3 cycles/particle/pos_dim per step (antilog→subtract→log).

def pf_step_lns8_delta(signs, mags, refs, sensors, measurements_real,
                        dt_s, dt_m, noise_pos_s, noise_pos_m,
                        noise_vel_s, noise_vel_m, rng):
    """One step of delta-encoded 6D PF in LNS8.

    signs, mags: (N_DIMS, n_particles) — offsets for pos dims, absolute for vel dims.
    refs: (3,) float64 — reference positions (mutated in place).
    measurements_real: dict {dim: float} — raw measurements in absolute coords.
    """
    n_particles = signs.shape[1]
    new_signs = signs.copy()
    new_mags = mags.copy()
    predict_cycles = 0

    # --- Predict (identical to non-delta, but offsets are small) ---
    for i in range(n_particles):
        for d_pos, d_vel in zip(POS_DIMS, VEL_DIMS):
            vdt_s, vdt_m, c = lns8_multiply(
                new_signs[d_vel, i], new_mags[d_vel, i], dt_s, dt_m)
            predict_cycles += c
            new_signs[d_pos, i], new_mags[d_pos, i], c = lns8_add(
                new_signs[d_pos, i], new_mags[d_pos, i], vdt_s, vdt_m)
            predict_cycles += c
            noise_r = rng.normal(0, 1.0)
            ns, nm = _real_to_lns8(noise_r)
            sn_s, sn_m, c = lns8_multiply(noise_pos_s, noise_pos_m, ns, nm)
            predict_cycles += c
            new_signs[d_pos, i], new_mags[d_pos, i], c = lns8_add(
                new_signs[d_pos, i], new_mags[d_pos, i], sn_s, sn_m)
            predict_cycles += c

        for d_vel in VEL_DIMS:
            noise_r = rng.normal(0, 1.0)
            ns, nm = _real_to_lns8(noise_r)
            sn_s, sn_m, c = lns8_multiply(noise_vel_s, noise_vel_m, ns, nm)
            predict_cycles += c
            new_signs[d_vel, i], new_mags[d_vel, i], c = lns8_add(
                new_signs[d_vel, i], new_mags[d_vel, i], sn_s, sn_m)
            predict_cycles += c

    # --- Weight (measurements adjusted to offset space) ---
    weight_cycles = 0
    lw_s = np.zeros(n_particles, dtype=int)
    lw_m = np.full(n_particles, ZERO_LOG_MAG, dtype=int)

    for sensor in sensors:
        d = sensor.state_dim
        z_real = measurements_real[d]
        # Convert measurement to offset space for position dims
        if d in POS_DIMS:
            z_offset = z_real - refs[POS_DIMS.index(d)]
        else:
            z_offset = z_real
        # Clamp for LNS8 safety
        if sensor.use_abs:
            z_offset = max(abs(z_offset), 0.01)
        elif abs(z_offset) < 0.01:
            z_offset = 0.01 if z_offset >= 0 else -0.01
        z_s, z_m = _real_to_lns8(z_offset)

        for i in range(n_particles):
            x_s, x_m = new_signs[d, i], new_mags[d, i]
            if sensor.use_abs:
                x_s, x_m, _ = lns8_abs(x_s, x_m)
            ll_s, ll_m, cyc = eval_sensor_lns8(sensor, z_s, z_m, x_s, x_m)
            lw_s[i], lw_m[i], c_acc = lns8_add(lw_s[i], lw_m[i], ll_s, ll_m)
            weight_cycles += cyc + c_acc

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
    offset_est = np.zeros(N_DIMS)
    for d in range(N_DIMS):
        for i in range(n_particles):
            offset_est[d] += linear_w[i] * _lns8_to_real(new_signs[d, i], new_mags[d, i])

    # Convert to absolute estimate
    estimate = offset_est.copy()
    for idx, d in enumerate(POS_DIMS):
        estimate[d] += refs[idx]

    # Systematic resampling
    cumsum = np.cumsum(linear_w)
    cumsum[-1] = 1.0
    u = (rng.uniform() + np.arange(n_particles)) / n_particles
    idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)
    new_signs = new_signs[:, idx].copy()
    new_mags = new_mags[:, idx].copy()

    # --- Re-center: update refs, re-encode offsets ---
    # Hardware: antilog (1 cyc) + fixed-point subtract (1 cyc) + log (1 cyc) = 3 per particle
    recenter_cycles = 0
    for pos_idx, d in enumerate(POS_DIMS):
        mean_off = offset_est[d]
        refs[pos_idx] += mean_off
        for i in range(n_particles):
            real_off = _lns8_to_real(new_signs[d, i], new_mags[d, i])
            real_off -= mean_off
            new_signs[d, i], new_mags[d, i] = _real_to_lns8(real_off)
        recenter_cycles += 3 * n_particles

    cycles = {
        'predict': predict_cycles,
        'weight': weight_cycles,
        'resample': resample_cycles,
        'recenter': recenter_cycles,
        'total': predict_cycles + weight_cycles + resample_cycles + recenter_cycles,
    }
    return new_signs, new_mags, estimate, cycles


# ---------------------------------------------------------------------------
# 6D Particle filter — Float64
# ---------------------------------------------------------------------------

def pf_step_float64(particles, sensors, measurements_real, dt, noise_pos, noise_vel, rng):
    """One step of 6D bootstrap PF in float64.

    particles: (N_DIMS, n_particles) array.
    """
    n_particles = particles.shape[1]
    new_p = particles.copy()

    # Predict
    for d_pos, d_vel in zip(POS_DIMS, VEL_DIMS):
        new_p[d_pos] += new_p[d_vel] * dt + rng.normal(0, noise_pos, n_particles)
    for d_vel in VEL_DIMS:
        new_p[d_vel] += rng.normal(0, noise_vel, n_particles)

    # Weight
    log_w = np.zeros(n_particles)
    for sensor in sensors:
        z = measurements_real[sensor.state_dim]
        for i in range(n_particles):
            log_w[i] += eval_sensor_float64(sensor, z, new_p[sensor.state_dim, i])

    # Resample
    max_lw = log_w.max()
    w = np.exp(log_w - max_lw)
    w_sum = w.sum()
    if w_sum > 0:
        w /= w_sum
    else:
        w[:] = 1.0 / n_particles

    estimate = np.zeros(N_DIMS)
    for d in range(N_DIMS):
        estimate[d] = np.average(new_p[d], weights=w)

    cumsum = np.cumsum(w)
    cumsum[-1] = 1.0
    u = (rng.uniform() + np.arange(n_particles)) / n_particles
    idx = np.clip(np.searchsorted(cumsum, u), 0, n_particles - 1)

    return new_p[:, idx].copy(), estimate


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def _generate_trajectory(initial_state, n_steps, dt, noise_pos, noise_vel, rng):
    """Generate ground-truth drone trajectory."""
    true_traj = np.zeros((N_DIMS, n_steps + 1))
    true_traj[:, 0] = initial_state
    for t in range(n_steps):
        s = true_traj[:, t].copy()
        for d_pos, d_vel in zip(POS_DIMS, VEL_DIMS):
            s[d_pos] += s[d_vel] * dt + rng.normal(0, noise_pos * 0.1)
        for d_vel in VEL_DIMS:
            s[d_vel] += rng.normal(0, noise_vel * 0.1)
        true_traj[:, t + 1] = s
    return true_traj


def _generate_measurements(sensors, true_state, rng):
    """Generate noisy measurements from true state."""
    meas_real = {}
    for sensor in sensors:
        z = simulate_measurement(sensor, true_state, rng)
        meas_real[sensor.state_dim] = z
    return meas_real


def run_scenario(name, initial_state, n_particles=500, n_steps=50, dt=0.5, seed=42):
    """Run LNS8 (plain), LNS8 (delta-encoded), and float64 particle filters."""
    rng = np.random.default_rng(seed)
    noise_pos = 0.3
    noise_vel = 0.1

    true_traj = _generate_trajectory(initial_state, n_steps, dt, noise_pos, noise_vel, rng)
    sensors = make_drone_sensors()

    # LNS8 constants
    dt_s, dt_m = _real_to_lns8(dt)
    np_s, np_m = _real_to_lns8(noise_pos)
    nv_s, nv_m = _real_to_lns8(noise_vel)

    # Initialize particles (shared initial state for fair comparison)
    init_real = np.zeros((N_DIMS, n_particles))
    for d in range(N_DIMS):
        spread = 1.0 if d in POS_DIMS else 0.3
        init_real[d] = initial_state[d] + rng.normal(0, spread, n_particles)

    # --- Plain LNS8 ---
    p_s = np.zeros((N_DIMS, n_particles), dtype=int)
    p_m = np.zeros((N_DIMS, n_particles), dtype=int)
    for d in range(N_DIMS):
        for i in range(n_particles):
            p_s[d, i], p_m[d, i] = _real_to_lns8(init_real[d, i])

    # --- Delta LNS8 ---
    refs = np.array([initial_state[d] for d in POS_DIMS], dtype=float)
    dp_s = np.zeros((N_DIMS, n_particles), dtype=int)
    dp_m = np.zeros((N_DIMS, n_particles), dtype=int)
    for d in range(N_DIMS):
        for i in range(n_particles):
            if d in POS_DIMS:
                offset = init_real[d, i] - refs[POS_DIMS.index(d)]
                dp_s[d, i], dp_m[d, i] = _real_to_lns8(offset)
            else:
                dp_s[d, i], dp_m[d, i] = _real_to_lns8(init_real[d, i])

    # --- Float64 ---
    p_f64 = init_real.copy()

    est_plain = np.zeros((N_DIMS, n_steps))
    est_delta = np.zeros((N_DIMS, n_steps))
    est_f64 = np.zeros((N_DIMS, n_steps))
    cyc_plain = {'predict': 0, 'weight': 0, 'resample': 0, 'total': 0}
    cyc_delta = {'predict': 0, 'weight': 0, 'resample': 0, 'recenter': 0, 'total': 0}

    for t in range(n_steps):
        true_now = true_traj[:, t + 1]
        meas_real = _generate_measurements(sensors, true_now, rng)

        # Build LNS8 measurement list for plain PF
        meas_lns8_list = []
        for sensor in sensors:
            z = meas_real[sensor.state_dim]
            z_c = max(abs(z), 0.01) * (1 if z >= 0 else -1)
            meas_lns8_list.append(_real_to_lns8(z_c))

        # Plain LNS8
        rng1 = np.random.default_rng(seed + t * 1000)
        p_s, p_m, e, cyc = pf_step_lns8(
            p_s, p_m, sensors, meas_lns8_list,
            dt_s, dt_m, np_s, np_m, nv_s, nv_m, rng1)
        est_plain[:, t] = e
        for k in cyc_plain:
            cyc_plain[k] += cyc.get(k, 0)

        # Delta LNS8
        rng2 = np.random.default_rng(seed + t * 1000)
        dp_s, dp_m, e, cyc = pf_step_lns8_delta(
            dp_s, dp_m, refs, sensors, meas_real,
            dt_s, dt_m, np_s, np_m, nv_s, nv_m, rng2)
        est_delta[:, t] = e
        for k in cyc_delta:
            cyc_delta[k] += cyc.get(k, 0)

        # Float64
        rng3 = np.random.default_rng(seed + t * 1000)
        p_f64, e = pf_step_float64(
            p_f64, sensors, meas_real, dt, noise_pos, noise_vel, rng3)
        est_f64[:, t] = e

    # Per-dimension RMSE
    rmse_plain = np.array([np.sqrt(np.mean((est_plain[d] - true_traj[d, 1:])**2)) for d in range(N_DIMS)])
    rmse_delta = np.array([np.sqrt(np.mean((est_delta[d] - true_traj[d, 1:])**2)) for d in range(N_DIMS)])
    rmse_f64 = np.array([np.sqrt(np.mean((est_f64[d] - true_traj[d, 1:])**2)) for d in range(N_DIMS)])

    avg_cyc_plain = cyc_plain['total'] / n_steps
    avg_cyc_delta = cyc_delta['total'] / n_steps

    return {
        'name': name,
        'n_particles': n_particles,
        'n_steps': n_steps,
        'dt': dt,
        'initial_state': initial_state,
        'rmse_plain': rmse_plain,
        'rmse_delta': rmse_delta,
        'rmse_f64': rmse_f64,
        'cyc_plain': cyc_plain,
        'cyc_delta': cyc_delta,
        'avg_cyc_plain': avg_cyc_plain,
        'avg_cyc_delta': avg_cyc_delta,
        'hz_plain': 50_000_000 / avg_cyc_plain if avg_cyc_plain > 0 else float('inf'),
        'hz_delta': 50_000_000 / avg_cyc_delta if avg_cyc_delta > 0 else float('inf'),
        'true_traj': true_traj,
    }


# ---------------------------------------------------------------------------
# Streaming mode — emit JSON-lines per timestep for dashboard
# ---------------------------------------------------------------------------

def run_streaming(method='delta', n_particles=128, n_steps=100, dt=0.5, seed=42):
    """Run particle filter and emit JSON-lines to stdout per timestep."""
    import json

    rng = np.random.default_rng(seed)
    initial_state = np.array([5.0, 5.0, 3.0, 1.0, 0.5, 0.1])
    noise_pos = 0.3
    noise_vel = 0.1

    true_traj = _generate_trajectory(initial_state, n_steps, dt, noise_pos, noise_vel, rng)
    sensors = make_drone_sensors()

    dt_s, dt_m = _real_to_lns8(dt)
    np_s, np_m = _real_to_lns8(noise_pos)
    nv_s, nv_m = _real_to_lns8(noise_vel)

    # Initialize particles
    init_real = np.zeros((N_DIMS, n_particles))
    for d in range(N_DIMS):
        spread = 1.0 if d in POS_DIMS else 0.3
        init_real[d] = initial_state[d] + rng.normal(0, spread, n_particles)

    # Running RMSE accumulators
    sum_sq_err = np.zeros(N_DIMS)

    if method in ('delta', 'both'):
        refs = np.array([initial_state[d] for d in POS_DIMS], dtype=float)
        dp_s = np.zeros((N_DIMS, n_particles), dtype=int)
        dp_m = np.zeros((N_DIMS, n_particles), dtype=int)
        for d in range(N_DIMS):
            for i in range(n_particles):
                if d in POS_DIMS:
                    offset = init_real[d, i] - refs[POS_DIMS.index(d)]
                    dp_s[d, i], dp_m[d, i] = _real_to_lns8(offset)
                else:
                    dp_s[d, i], dp_m[d, i] = _real_to_lns8(init_real[d, i])

    if method in ('plain', 'both'):
        p_s = np.zeros((N_DIMS, n_particles), dtype=int)
        p_m = np.zeros((N_DIMS, n_particles), dtype=int)
        for d in range(N_DIMS):
            for i in range(n_particles):
                p_s[d, i], p_m[d, i] = _real_to_lns8(init_real[d, i])

    for t in range(n_steps):
        true_now = true_traj[:, t + 1]
        meas_real = _generate_measurements(sensors, true_now, rng)

        step_rng = np.random.default_rng(seed + t * 1000)

        if method == 'delta':
            dp_s, dp_m, estimate, cycles = pf_step_lns8_delta(
                dp_s, dp_m, refs, sensors, meas_real,
                dt_s, dt_m, np_s, np_m, nv_s, nv_m, step_rng)

            # Get particle positions in absolute coords for display
            particles_abs = {}
            for d in range(N_DIMS):
                vals = []
                for i in range(n_particles):
                    v = _lns8_to_real(dp_s[d, i], dp_m[d, i])
                    if d in POS_DIMS:
                        v += refs[POS_DIMS.index(d)]
                    vals.append(round(float(v), 4))
                particles_abs[DIM_NAMES[d]] = vals

            # Log-weights (reconstruct from current state)
            weights = [0.0] * n_particles  # weights are consumed by resampling

            record = {
                "t": t,
                "truth": {DIM_NAMES[d]: round(float(true_now[d]), 4) for d in range(N_DIMS)},
                "estimate": {DIM_NAMES[d]: round(float(estimate[d]), 4) for d in range(N_DIMS)},
                "particles": particles_abs,
                "weights": weights,
                "sensors": {s.name: round(float(meas_real.get(s.state_dim, 0)), 4) for s in sensors},
                "refs": {DIM_NAMES[d]: round(float(refs[POS_DIMS.index(d)]), 4) for d in POS_DIMS},
                "cycles": cycles,
                "method": "delta_lns8",
            }

        elif method == 'plain':
            meas_lns8_list = []
            for sensor in sensors:
                z = meas_real[sensor.state_dim]
                z_c = max(abs(z), 0.01) * (1 if z >= 0 else -1)
                meas_lns8_list.append(_real_to_lns8(z_c))

            p_s, p_m, estimate, cycles = pf_step_lns8(
                p_s, p_m, sensors, meas_lns8_list,
                dt_s, dt_m, np_s, np_m, nv_s, nv_m, step_rng)

            particles_abs = {}
            for d in range(N_DIMS):
                vals = []
                for i in range(n_particles):
                    vals.append(round(float(_lns8_to_real(p_s[d, i], p_m[d, i])), 4))
                particles_abs[DIM_NAMES[d]] = vals

            record = {
                "t": t,
                "truth": {DIM_NAMES[d]: round(float(true_now[d]), 4) for d in range(N_DIMS)},
                "estimate": {DIM_NAMES[d]: round(float(estimate[d]), 4) for d in range(N_DIMS)},
                "particles": particles_abs,
                "weights": [0.0] * n_particles,
                "sensors": {s.name: round(float(meas_real.get(s.state_dim, 0)), 4) for s in sensors},
                "cycles": cycles,
                "method": "plain_lns8",
            }

        # Running RMSE
        for d in range(N_DIMS):
            sum_sq_err[d] += (estimate[d] - true_now[d])**2
        rmse = np.sqrt(sum_sq_err / (t + 1))
        record["rmse"] = {DIM_NAMES[d]: round(float(rmse[d]), 4) for d in range(N_DIMS)}

        print(json.dumps(record), flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description='3D drone particle filter — LNS8 stress test')
    parser.add_argument('--stream', action='store_true', help='Emit JSON-lines to stdout')
    parser.add_argument('--method', choices=['plain', 'delta', 'both'], default='delta',
                        help='Which LNS8 method to run (default: delta)')
    parser.add_argument('--particles', type=int, default=None,
                        help='Number of particles (default: 128 for stream, 500 for benchmark)')
    parser.add_argument('--steps', type=int, default=None,
                        help='Number of timesteps (default: 100 for stream, 50 for benchmark)')
    args = parser.parse_args()

    if args.stream:
        n_particles = args.particles or 128
        n_steps = args.steps or 100
        run_streaming(method=args.method, n_particles=n_particles, n_steps=n_steps)
        return

    print("=" * 80)
    print("3D DRONE PARTICLE FILTER — LNS8 STRESS TEST")
    print("=" * 80)

    # --- Resolution analysis ---
    print("\nLNS8 RESOLUTION AT TYPICAL STATE VALUES")
    print("-" * 60)
    lsb_frac = 2**(1/16) - 1  # ≈ 0.0443
    print(f"  Relative LSB: 2^(1/16) - 1 = {lsb_frac:.4f} = {lsb_frac*100:.1f}%\n")
    print(f"  {'Value':>10s}  {'LSB step':>10s}  {'v*dt @ 1m/s,0.5s':>18s}  {'Visible?':>10s}")
    for x in [2.0, 5.0, 10.0, 20.0, 50.0, 100.0]:
        lsb = x * lsb_frac
        vdt = 1.0 * 0.5
        vis = "YES" if vdt > lsb else "no"
        print(f"  {x:10.1f}  {lsb:10.3f}m  {vdt:18.3f}m  {vis:>10s}")

    print(f"\n  Threshold: position updates visible when |v*dt| > {lsb_frac:.1%} * |x|")
    print(f"  At 1 m/s, dt=0.5s: need |x| < {0.5/lsb_frac:.0f}m for prediction to work")

    # --- Run scenarios ---
    n_particles = args.particles or 500
    n_steps = args.steps or 50
    scenarios = [
        ('Near-origin (5m)',  np.array([5.0, 5.0, 3.0, 1.0, 0.5, 0.1])),
        ('Far-origin (50m)',  np.array([50.0, 50.0, 30.0, 1.0, 0.5, 0.1])),
    ]

    results = []
    for name, state in scenarios:
        print(f"\nRunning: {name} — {n_particles} particles × 6 sensors × {n_steps} steps...", flush=True)
        r = run_scenario(name, state, n_particles=n_particles, n_steps=n_steps, dt=0.5)
        results.append(r)
        print(f"  Done.")

    # --- Three-way RMSE comparison ---
    print(f"\n{'=' * 80}")
    print("PER-VARIABLE RMSE: PLAIN LNS8 vs DELTA-ENCODED LNS8 vs FLOAT64")
    print(f"{'=' * 80}\n")

    for r in results:
        print(f"  {r['name']}")
        print(f"  {'Var':>4s}  {'Plain':>8s}  {'Delta':>8s}  {'f64':>8s}  "
              f"{'P/f64':>6s}  {'D/f64':>6s}  {'Improvement':>12s}")
        print(f"  {'-'*4}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*12}")
        for d in range(N_DIMS):
            rp = r['rmse_plain'][d]
            rd = r['rmse_delta'][d]
            rf = r['rmse_f64'][d]
            pr = rp / max(rf, 1e-15)
            dr = rd / max(rf, 1e-15)
            improvement = rp / max(rd, 1e-15)
            print(f"  {DIM_NAMES[d]:>4s}  {rp:8.3f}  {rd:8.3f}  {rf:8.3f}  "
                  f"{pr:5.1f}x  {dr:5.1f}x  {improvement:10.1f}x better")
        print()

    # --- Throughput ---
    print(f"{'=' * 80}")
    print("THROUGHPUT")
    print(f"{'=' * 80}\n")

    for r in results:
        print(f"  {r['name']}:")
        print(f"    Plain LNS8: {r['avg_cyc_plain']:.0f} cyc/step → {r['hz_plain']:.0f} Hz @ 50 MHz")
        print(f"    Delta LNS8: {r['avg_cyc_delta']:.0f} cyc/step → {r['hz_delta']:.0f} Hz @ 50 MHz")
        cyc = r['cyc_delta']
        n = r['n_steps']
        for stage in ['predict', 'weight', 'resample', 'recenter']:
            if cyc.get(stage, 0) > 0:
                pct = 100 * cyc[stage] / max(cyc['total'], 1)
                print(f"      {stage:<10s}: {cyc[stage]/n:6.0f} cyc ({pct:4.1f}%)")
        print()

    # --- Analysis ---
    print(f"{'=' * 80}")
    print("ANALYSIS: DELTA-ENCODING EFFECTIVENESS")
    print(f"{'=' * 80}\n")

    r_near, r_far = results[0], results[1]

    def pos_mean(rmse):
        return np.mean(rmse[:3])
    def vel_mean(rmse):
        return np.mean(rmse[3:])

    print(f"  Position RMSE ratio vs float64:")
    print(f"                     {'Plain':>8s}  {'Delta':>8s}")
    print(f"    Near-origin:     {pos_mean(r_near['rmse_plain'])/pos_mean(r_near['rmse_f64']):7.1f}x  "
          f"{pos_mean(r_near['rmse_delta'])/pos_mean(r_near['rmse_f64']):7.1f}x")
    print(f"    Far-origin:      {pos_mean(r_far['rmse_plain'])/pos_mean(r_far['rmse_f64']):7.1f}x  "
          f"{pos_mean(r_far['rmse_delta'])/pos_mean(r_far['rmse_f64']):7.1f}x")

    print(f"\n  Velocity RMSE ratio vs float64:")
    print(f"                     {'Plain':>8s}  {'Delta':>8s}")
    print(f"    Near-origin:     {vel_mean(r_near['rmse_plain'])/vel_mean(r_near['rmse_f64']):7.1f}x  "
          f"{vel_mean(r_near['rmse_delta'])/vel_mean(r_near['rmse_f64']):7.1f}x")
    print(f"    Far-origin:      {vel_mean(r_far['rmse_plain'])/vel_mean(r_far['rmse_f64']):7.1f}x  "
          f"{vel_mean(r_far['rmse_delta'])/vel_mean(r_far['rmse_f64']):7.1f}x")

    delta_far_pos = pos_mean(r_far['rmse_delta']) / pos_mean(r_far['rmse_f64'])

    print(f"\n  Delta-encoding cost: +{r_far['cyc_delta'].get('recenter',0)/r_far['n_steps']:.0f} "
          f"cycles/step ({r_far['cyc_delta'].get('recenter',0)*100/max(r_far['cyc_delta']['total'],1):.1f}% overhead)")
    print(f"  Hardware cost: 3 × 16-bit reference registers (6 bytes)")

    if delta_far_pos < 2.0:
        print(f"\n  RESULT: Delta-encoding closes the gap at far-origin ({delta_far_pos:.1f}x f64).")
        print(f"  8-bit LNS + delta-encoding is viable for unbounded-range navigation.")
    else:
        print(f"\n  RESULT: Delta-encoding helps but gap remains ({delta_far_pos:.1f}x f64).")
        print(f"  May need 12-bit LNS or hybrid fixed-point predict for full precision.")


if __name__ == '__main__':
    main()
