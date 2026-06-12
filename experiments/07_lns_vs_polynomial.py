"""
EML/LNS vs polynomial approximation benchmark.

Compares fixed-point LNS/EML function evaluation against Chebyshev
polynomial approximation at matched bit widths. Uses the quantization
primitives from 06_lns_precision.py and the EMLTree from 04.

Key question: does EML/LNS offer a genuine advantage over polynomials
for hardware function evaluation, and if so, under what conditions?

Usage:
    uv run --with torch python experiments/07_lns_vs_polynomial.py
"""

import numpy as np
from numpy.polynomial import chebyshev
from importlib.machinery import SourceFileLoader

# Import our LNS quantization primitives
lns_mod = SourceFileLoader('lns_mod', 'experiments/06_lns_precision.py').load_module()
quantize_real = lns_mod._quantize_real  # LNS round-trip quantization

import torch
torch.set_default_dtype(torch.float64)


# --- Quantization helpers ---

def q_lns(x, frac_bits):
    """LNS quantize a numpy array."""
    if frac_bits is None:
        return x
    t = torch.from_numpy(x) if isinstance(x, np.ndarray) else torch.tensor(x, dtype=torch.float64)
    scale = float(1 << frac_bits)
    sign = torch.sign(t)
    mag = torch.abs(t)
    result = torch.zeros_like(t)
    nonzero = mag > 0
    if nonzero.any():
        log2_mag = torch.log2(mag[nonzero])
        log2_q = torch.round(log2_mag * scale) / scale
        result[nonzero] = sign[nonzero] * torch.pow(2.0, log2_q)
    return result.numpy()


def q_fixed(x, frac_bits):
    """Standard fixed-point quantize a numpy array."""
    if frac_bits is None:
        return x
    scale = float(1 << frac_bits)
    return np.round(x * scale) / scale


# --- EML/LNS function evaluation ---

def eval_eml(name, x, frac_bits):
    """Evaluate function via EML decomposition with LNS quantization."""
    q = lambda v: q_lns(v, frac_bits)

    if name == 'exp':
        return q(np.exp(q(x)))

    elif name == 'ln':
        ones = q(np.ones_like(x))
        s1 = q(np.exp(ones) - np.log(q(x)))
        s2 = q(np.exp(s1) - np.log(ones))
        s3 = q(np.exp(ones) - np.log(s2))
        return s3

    elif name == 'sigmoid':
        exp_x = q(np.exp(q(x)))
        return q(exp_x / q(1.0 + exp_x))

    elif name == 'softplus':
        exp_x = q(np.exp(q(x)))
        return q(np.log(q(1.0 + exp_x)))

    elif name == 'gelu_approx':
        sx = q(1.702 * q(x))
        exp_sx = q(np.exp(sx))
        sig = q(exp_sx / q(1.0 + exp_sx))
        return q(q(x) * sig)

    elif name == 'arrhenius':
        Ea_over_R = 50000.0 / 8.314
        exponent = q(-Ea_over_R / q(x))
        return q(1e6 * q(np.exp(exponent)))

    elif name == '1/x':
        # 1/x = exp(-ln(x)), depth 2 via EML
        ln_x = q(np.exp(q(np.ones_like(x))) - np.log(q(x)))  # eml(1,x) = e - ln(x)
        # Need actual ln(x), not e - ln(x)... use depth-3 decomposition
        # Or: in LNS, 1/x is trivial (negate the log-magnitude)
        # Let's use the LNS-native approach
        return q(1.0 / q(x))

    raise ValueError(f"Unknown: {name}")


# --- Chebyshev polynomial evaluation ---

def fit_chebyshev(fn, domain, degree):
    """Fit Chebyshev approximation to fn on domain."""
    lo, hi = domain
    # Chebyshev nodes
    n = degree + 1
    k = np.arange(n)
    nodes = np.cos((2 * k + 1) * np.pi / (2 * n))  # on [-1, 1]
    x_nodes = 0.5 * (hi - lo) * nodes + 0.5 * (hi + lo)  # mapped to domain
    y_nodes = fn(x_nodes)

    # Fit in Chebyshev basis
    coeffs = chebyshev.chebfit(nodes, y_nodes, degree)
    return coeffs


def eval_chebyshev(coeffs, x, domain, frac_bits=None):
    """Evaluate Chebyshev polynomial with optional fixed-point quantization.

    Uses Clenshaw recurrence for numerical stability.
    """
    lo, hi = domain
    q = lambda v: q_fixed(v, frac_bits)

    # Map to [-1, 1]
    x_norm = q((2.0 * x - (hi + lo)) / (hi - lo))

    # Clenshaw recurrence: evaluates sum of c_i * T_i(x)
    n = len(coeffs)
    if n == 1:
        return np.full_like(x, q(coeffs[0]))

    b_next = np.zeros_like(x)
    b_curr = np.full_like(x, q(coeffs[-1]))

    for i in range(n - 2, 0, -1):
        b_prev = q(q(coeffs[i]) + q(2.0 * q(x_norm * b_curr)) - b_next)
        b_next = b_curr
        b_curr = b_prev

    result = q(q(coeffs[0]) + q(x_norm * b_curr) - b_next)
    return result


# --- Benchmark targets ---

TARGETS = {
    'exp': {
        'fn': np.exp,
        'domain': (-4.0, 4.0),
        'eml_ops': 1,
        'desc': 'exp(x)',
    },
    'sigmoid': {
        'fn': lambda x: 1.0 / (1.0 + np.exp(-x)),
        'domain': (-8.0, 8.0),
        'eml_ops': 3,
        'desc': 'sigmoid(x)',
    },
    'softplus': {
        'fn': lambda x: np.log1p(np.exp(x)),
        'domain': (-8.0, 8.0),
        'eml_ops': 3,
        'desc': 'softplus(x)',
    },
    'gelu_approx': {
        'fn': lambda x: x / (1.0 + np.exp(-1.702 * x)),
        'domain': (-4.0, 4.0),
        'eml_ops': 5,
        'desc': 'GELU(x) approx',
    },
    'arrhenius': {
        'fn': lambda T: 1e6 * np.exp(-50000.0 / (8.314 * T)),
        'domain': (300.0, 2000.0),
        'eml_ops': 4,
        'desc': 'Arrhenius rate(T)',
    },
}


def rel_errors(expected, got):
    """Compute relative errors, filtering invalid points."""
    mask = (np.abs(expected) > 1e-15) & np.isfinite(expected) & np.isfinite(got)
    if mask.sum() == 0:
        return np.array([np.inf])
    return np.abs(got[mask] - expected[mask]) / np.abs(expected[mask])


# --- Main ---

def main():
    print("=" * 95)
    print("EML/LNS vs CHEBYSHEV POLYNOMIAL: Function Evaluation Benchmark")
    print("=" * 95)

    bit_widths = [8, 12, 16]
    cheb_degrees = [4, 8, 16]
    n_pts = 500

    for tname, tspec in TARGETS.items():
        fn = tspec['fn']
        lo, hi = tspec['domain']
        x = np.linspace(lo + 1e-6, hi - 1e-6, n_pts)
        expected = fn(x)

        print(f"\n{'━' * 95}")
        print(f"  {tspec['desc']}   domain=[{lo}, {hi}]   EML ops={tspec['eml_ops']}")
        print(f"{'━' * 95}")
        print(f"  {'Method':<35s} {'Median':>10s} {'Max':>10s} {'Storage':>10s} {'Ops':>5s}")
        print(f"  {'─' * 35} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 5}")

        for bw in bit_widths:
            # --- EML/LNS ---
            eml_vals = eval_eml(tname, x, frac_bits=bw)
            errs = rel_errors(expected, eml_vals)
            gauss_table = (1 << bw) * bw // 8
            print(f"  {'EML/LNS ' + str(bw) + 'b':<35s}"
                  f" {np.median(errs):>10.2e} {np.max(errs):>10.2e}"
                  f" {gauss_table:>8d} B {tspec['eml_ops']:>5d}")

            # --- Chebyshev at various degrees ---
            for deg in cheb_degrees:
                coeffs = fit_chebyshev(fn, (lo, hi), deg)
                cheb_vals = eval_chebyshev(coeffs, x, (lo, hi), frac_bits=bw)
                cerrs = rel_errors(expected, cheb_vals)
                coeff_bytes = (deg + 1) * max(bw, 8) // 8
                cheb_ops = 2 * deg  # Clenshaw: deg muls + deg adds
                label = f"Cheb-{deg} {bw}b fixed"
                print(f"  {label:<35s}"
                      f" {np.median(cerrs):>10.2e} {np.max(cerrs):>10.2e}"
                      f" {coeff_bytes:>8d} B {cheb_ops:>5d}")

        # Float64 baselines
        eml_f64 = eval_eml(tname, x, frac_bits=None)
        e_f64 = rel_errors(expected, eml_f64)
        print(f"  {'EML float64':<35s}"
              f" {np.median(e_f64):>10.2e} {np.max(e_f64):>10.2e}"
              f" {'─':>10s} {tspec['eml_ops']:>5d}")
        for deg in [8, 16]:
            coeffs = fit_chebyshev(fn, (lo, hi), deg)
            cheb_f64 = eval_chebyshev(coeffs, x, (lo, hi))
            cf64 = rel_errors(expected, cheb_f64)
            print(f"  {'Cheb-' + str(deg) + ' float64':<35s}"
                  f" {np.median(cf64):>10.2e} {np.max(cf64):>10.2e}"
                  f" {'─':>10s} {2*deg:>5d}")

    # --- Analysis ---
    print(f"\n\n{'=' * 95}")
    print("CROSSOVER ANALYSIS: When does shared EML table beat per-function Chebyshev?")
    print(f"{'=' * 95}\n")

    for bw in bit_widths:
        gauss_bytes = (1 << bw) * bw // 8
        for deg in cheb_degrees:
            per_fn = (deg + 1) * max(bw, 8) // 8
            crossover = gauss_bytes / per_fn if per_fn > 0 else float('inf')
            print(f"  {bw:2d}b: Gauss table={gauss_bytes:>8,d} B  "
                  f"Cheb-{deg} coeffs={per_fn:>4d} B  "
                  f"crossover at {crossover:>6.0f} functions")

    print("""
Key insight:
  The Gaussian log table is SHARED across all functions.
  Chebyshev coefficients are PER-FUNCTION.
  If your chip evaluates N different functions, EML wins when
  N × cheb_storage > gaussian_table_size.

  But: Chebyshev has uniform error bounds (no cancellation).
  EML has worst-case cancellation for functions near zero.
  For functions like sigmoid, softplus, exp — EML should be fine.
""")


if __name__ == '__main__':
    main()
