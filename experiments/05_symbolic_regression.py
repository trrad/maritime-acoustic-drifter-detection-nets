"""
Symbolic regression with EML trees.

Given noisy samples from an unknown function, recover the closed-form
expression. Compares EML tree recovery against polynomial baselines.

Uses the master formula EML tree architecture (softmax routing).

Usage:
    uv run --with torch,numpy python experiments/05_symbolic_regression.py
    uv run --with torch,numpy python experiments/05_symbolic_regression.py --target decay --depth 3
"""

import argparse
import time
import sys

import torch
import numpy as np

sys.path.insert(0, '.')
from importlib.machinery import SourceFileLoader
eml_mod = SourceFileLoader('eml_mod', 'experiments/04_torch_master_formula.py').load_module()
EMLTree = eml_mod.EMLTree
DTYPE = eml_mod.DTYPE
REAL_DTYPE = eml_mod.REAL_DTYPE


# --- Target functions (unknown to the optimizer) ---

TARGETS = {
    'simple_exp': {
        'label': 'exp(x)',
        'fn': lambda x: np.exp(x),
        'range': (0.5, 2.5),
    },
    'simple_ln': {
        'label': 'ln(x)',
        'fn': lambda x: np.log(x),
        'range': (0.5, 5.0),
    },
    'decay': {
        'label': '3·exp(-0.5x)',
        'fn': lambda x: 3.0 * np.exp(-0.5 * x),
        'range': (0.5, 6.0),
    },
    'gaussian': {
        'label': 'exp(-x²/2)',
        'fn': lambda x: np.exp(-0.5 * x ** 2),
        'range': (0.5, 3.0),
    },
    'power_half': {
        'label': 'sqrt(x)',
        'fn': lambda x: np.sqrt(x),
        'range': (0.5, 5.0),
    },
    'inv': {
        'label': '1/x',
        'fn': lambda x: 1.0 / x,
        'range': (0.5, 5.0),
    },
}


# --- Polynomial baseline ---

def fit_polynomial(x_train, y_train, x_test, y_test, max_degree=8):
    """Fit polynomials up to max_degree, return best by test MSE."""
    x_np = x_train.numpy()
    y_np = y_train.numpy()
    xt_np = x_test.numpy()
    yt_np = y_test.numpy()

    best_mse = float('inf')
    best_deg = 0

    for deg in range(1, max_degree + 1):
        coeffs = np.polyfit(x_np, y_np, deg)
        pred = np.polyval(coeffs, xt_np)
        mse = np.mean((pred - yt_np) ** 2)
        if mse < best_mse:
            best_mse = mse
            best_deg = deg

    return best_mse, best_deg


# --- EML symbolic regression ---

def fit_eml(x_data, y_data, depth, n_seeds=5, seed0=137,
            search_iters=6000, hardening_iters=2000,
            lr=0.01, tau_search=2.5, tau_hard=0.01,
            noise_level=0.0):
    """Fit univariate EML tree to (x, y_noisy) data.

    Uses the paper's training procedure adapted for univariate:
    - Leaves choose from {1, x} (n_vars=1)
    - Softmax routing at internal nodes: {1, x, f}
    - NaN clamping, 3-stage training
    - Sweeps init strategies × seeds
    """
    # For univariate, we pass x as both x and y to the tree,
    # but with n_vars=1 the tree only uses {1, x} at leaves
    # We need dummy y values for the tree forward call
    x_t = torch.tensor(x_data, dtype=REAL_DTYPE)
    t_target = torch.tensor(y_data, dtype=DTYPE)

    best_snap_mse = float('inf')
    best_decoded = []
    best_exact = False
    n_exact = 0
    total_runs = 0

    for seed in range(seed0, seed0 + n_seeds):
        for strategy in EMLTree.INIT_STRATEGIES:
            total_runs += 1
            torch.manual_seed(seed)
            tree = EMLTree(depth, n_vars=1, init_strategy=strategy)
            optimizer = torch.optim.Adam(tree.parameters(), lr=lr)

            best_soft_loss = float('inf')
            best_soft_state = None
            total_iters = search_iters + hardening_iters

            for it in range(1, total_iters + 1):
                if it <= search_iters:
                    tau = tau_search
                    lam_ent = 0.0
                    lam_bin = 0.0
                else:
                    t_frac = (it - search_iters) / hardening_iters
                    t_tau = t_frac ** 2
                    tau = tau_search * (tau_hard / tau_search) ** t_tau
                    lam_ent = t_frac * 0.02
                    lam_bin = t_frac * 0.02

                optimizer.zero_grad()
                # Univariate: pass x as x, dummy None as y
                pred, leaf_probs, route_probs = tree(x_t, y=None,
                                                     tau_leaf=tau, tau_gate=tau)

                data_loss = torch.mean((pred - t_target).abs() ** 2).real
                eps = 1e-12
                leaf_ent = -(leaf_probs * (leaf_probs + eps).log()).sum(dim=1).mean()
                route_ent = -(route_probs * (route_probs + eps).log()).sum(dim=-1).mean()
                total_loss = data_loss + lam_ent * leaf_ent + lam_bin * route_ent

                if not torch.isfinite(total_loss):
                    if best_soft_state is not None:
                        tree.load_state_dict(best_soft_state)
                        optimizer = torch.optim.Adam(tree.parameters(), lr=lr)
                    continue

                total_loss.backward()
                torch.nn.utils.clip_grad_norm_(tree.parameters(), 1.0)
                optimizer.step()

                sl = data_loss.item()
                if np.isfinite(sl) and sl < best_soft_loss:
                    best_soft_loss = sl
                    best_soft_state = {k: v.detach().clone()
                                       for k, v in tree.state_dict().items()}

            # Snap and evaluate
            if best_soft_state is not None:
                tree.load_state_dict(best_soft_state)

            from copy import deepcopy
            snapped = deepcopy(tree)
            snapped.snap()

            with torch.no_grad():
                sp, _, _ = snapped(x_t, y=None, tau_leaf=0.01, tau_gate=0.01)
                snap_mse = torch.mean((sp - t_target).abs() ** 2).real.item()

            exact = np.isfinite(snap_mse) and snap_mse < 1e-15
            if exact:
                n_exact += 1

            decoded = snapped.decode()
            status = "EXACT" if exact else ("fit" if snap_mse < 1e-4 else "miss")

            print(f"    seed={seed} {strategy:12s}: snap={snap_mse:.3e}  "
                  f"[{status:5s}]  {' '.join(decoded[:6])}")

            if snap_mse < best_snap_mse:
                best_snap_mse = snap_mse
                best_decoded = decoded
                best_exact = exact

    return {
        'snap_mse': best_snap_mse,
        'decoded': best_decoded,
        'exact': best_exact,
        'n_exact': n_exact,
        'total_runs': total_runs,
    }


# --- Main ---

def run_sr_experiment(target_name, depth=3, noise=0.01,
                      n_train=200, n_test=100, n_seeds=3):
    target = TARGETS[target_name]
    lo, hi = target['range']

    print(f"\n{'='*78}")
    print(f"SYMBOLIC REGRESSION: {target['label']}")
    print(f"Depth={depth}  Noise={noise}  Train={n_train}pts  Seeds={n_seeds}×4strat")
    print(f"{'='*78}")

    # Training data with noise
    np.random.seed(42)
    x_train = np.linspace(lo, hi, n_train)
    y_clean = target['fn'](x_train)
    y_train = y_clean + noise * np.abs(y_clean).mean() * np.random.randn(n_train)

    # Test data (no noise, slightly different points)
    x_test = np.linspace(lo + 0.02, hi - 0.02, n_test)
    y_test = target['fn'](x_test)

    # Polynomial baseline
    print(f"\n  Polynomial baseline:")
    x_t_train = torch.tensor(x_train, dtype=REAL_DTYPE)
    y_t_train = torch.tensor(y_train, dtype=REAL_DTYPE)
    x_t_test = torch.tensor(x_test, dtype=REAL_DTYPE)
    y_t_test = torch.tensor(y_test, dtype=REAL_DTYPE)

    poly_mse, poly_deg = fit_polynomial(x_t_train, y_t_train, x_t_test, y_t_test)
    print(f"    Best degree={poly_deg}  Test MSE={poly_mse:.4e}")

    # EML tree
    print(f"\n  EML tree (depth {depth}):")
    eml_result = fit_eml(x_train, y_train, depth, n_seeds=n_seeds)

    # Summary
    print(f"\n  {'─'*60}")
    print(f"  {'Method':<20s}  {'Test MSE':>12s}  {'Notes'}")
    print(f"  {'─'*20}  {'─'*12}  {'─'*30}")
    print(f"  {'Polynomial':.<20s}  {poly_mse:>12.4e}  degree={poly_deg}")
    eml_note = f"exact={eml_result['n_exact']}/{eml_result['total_runs']}"
    if eml_result['exact']:
        eml_note += f"  {' '.join(eml_result['decoded'][:6])}"
    print(f"  {'EML tree':.<20s}  {eml_result['snap_mse']:>12.4e}  {eml_note}")

    return {'target': target_name, 'poly_mse': poly_mse,
            'eml_mse': eml_result['snap_mse'],
            'eml_exact': eml_result['n_exact']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=str, default=None,
                        choices=list(TARGETS.keys()))
    parser.add_argument('--depth', type=int, default=3)
    parser.add_argument('--noise', type=float, default=0.01)
    parser.add_argument('--seeds', type=int, default=3)
    args = parser.parse_args()

    torch.set_default_dtype(REAL_DTYPE)

    if args.target:
        run_sr_experiment(args.target, args.depth, args.noise, n_seeds=args.seeds)
    else:
        print("EML SYMBOLIC REGRESSION BENCHMARK")
        print("Can EML trees discover functions from noisy data?\n")

        results = []
        for target in ['simple_exp', 'simple_ln', 'inv']:
            r = run_sr_experiment(target, args.depth, args.noise, n_seeds=args.seeds)
            results.append(r)

        print(f"\n{'='*78}")
        print("OVERALL SUMMARY")
        print(f"{'='*78}")
        print(f"  {'Target':<15s}  {'Poly MSE':>12s}  {'EML MSE':>12s}  {'Exact':>8s}")
        for r in results:
            print(f"  {r['target']:.<15s}  {r['poly_mse']:>12.4e}  "
                  f"{r['eml_mse']:>12.4e}  {r['eml_exact']:>8d}")


if __name__ == '__main__':
    main()
