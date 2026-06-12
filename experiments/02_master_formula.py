"""
EML master formula: depth-2 and depth-3 parameterized EML trees.

Implements the softmax-parameterized master formula from Section 4.3 of
Odrzywołek 2026. Tests gradient-based optimization (Adam) for recovering
known elementary functions from data.

This is the core experiment for assessing EML as a trainable architecture.
"""

import numpy as np
from scipy.special import softmax


# --- Core ---

def eml(x, y):
    """EML operator over complex128."""
    return np.exp(x + 0j) - np.log(y + 0j)


# --- Master formula construction ---

def make_node_input(params, x, f_prev):
    """Compute a node input as softmax-weighted combination of {1, x, f_prev}.

    params: array of 3 logits [alpha, beta, gamma]
    x: input variable (scalar or array)
    f_prev: output of previous EML node (scalar or array), or None for leaves

    Returns: alpha'*1 + beta'*x + gamma'*f_prev, where [alpha', beta', gamma']
             are softmax(params).
    """
    weights = softmax(params)
    result = weights[0] * (1.0 + 0j) + weights[1] * x
    if f_prev is not None:
        result = result + weights[2] * f_prev
    else:
        # Leaves: only {1, x}, so redistribute gamma weight
        leaf_weights = softmax(params[:2])
        result = leaf_weights[0] * 1.0 + leaf_weights[1] * x
    return result


def eval_depth2_tree(params, x):
    """Evaluate a depth-2 EML master formula.

    Tree structure:
        eml(
            a1 + b1*x + g1*eml(a3+b3*x, a4+b4*x),
            a2 + b2*x + g2*eml(a5+b5*x, a6+b6*x)
        )

    params: 14 parameters = 2 leaf pairs * 2 params + 2 inner nodes * 3 params
            Layout: [leaf1(2), leaf2(2), leaf3(2), leaf4(2), inner1(3), inner2(3)]
    """
    # Leaves (no f_prev, so only 2 params each -> {1, x})
    leaf1 = make_node_input(np.append(params[0:2], -100), x, None)  # force leaf
    leaf2 = make_node_input(np.append(params[2:4], -100), x, None)
    leaf3 = make_node_input(np.append(params[4:6], -100), x, None)
    leaf4 = make_node_input(np.append(params[6:8], -100), x, None)

    # Inner EML nodes
    f_left = eml(leaf1, leaf2)
    f_right = eml(leaf3, leaf4)

    # Root inputs (3 params each: can use 1, x, or subtree result)
    root_left = make_node_input(params[8:11], x, f_left)
    root_right = make_node_input(params[11:14], x, f_right)

    return eml(root_left, root_right)


def eval_depth1_tree(params, x):
    """Evaluate a depth-1 EML tree (just one EML node).

    params: 4 parameters = 2 leaf inputs * 2 params each
    """
    leaf1 = make_node_input(np.append(params[0:2], -100), x, None)
    leaf2 = make_node_input(np.append(params[2:4], -100), x, None)
    return eml(leaf1, leaf2)


# --- Loss function ---

def mse_loss(params, x_data, y_data, eval_fn):
    """Mean squared error between tree output and target, real part only."""
    y_pred = eval_fn(params, x_data)
    y_pred_real = np.real(y_pred)
    return np.mean((y_pred_real - y_data) ** 2)


def numerical_gradient(params, x_data, y_data, eval_fn, eps=1e-7):
    """Central-difference gradient estimate."""
    grad = np.zeros_like(params)
    for i in range(len(params)):
        p_plus = params.copy()
        p_minus = params.copy()
        p_plus[i] += eps
        p_minus[i] -= eps
        grad[i] = (mse_loss(p_plus, x_data, y_data, eval_fn) -
                    mse_loss(p_minus, x_data, y_data, eval_fn)) / (2 * eps)
    return grad


# --- Adam optimizer ---

def adam_optimize(params, x_data, y_data, eval_fn, lr=0.01, n_steps=2000,
                  beta1=0.9, beta2=0.999, eps=1e-8, verbose=True):
    """Simple Adam optimizer with numerical gradients."""
    m = np.zeros_like(params)
    v = np.zeros_like(params)
    best_loss = float('inf')
    best_params = params.copy()

    for step in range(n_steps):
        loss = mse_loss(params, x_data, y_data, eval_fn)
        grad = numerical_gradient(params, x_data, y_data, eval_fn)

        if np.any(np.isnan(grad)):
            if verbose and step % 100 == 0:
                print(f"  step {step:4d}: NaN gradient, skipping")
            continue

        m = beta1 * m + (1 - beta1) * grad
        v = beta2 * v + (1 - beta2) * grad ** 2
        m_hat = m / (1 - beta1 ** (step + 1))
        v_hat = v / (1 - beta2 ** (step + 1))
        params = params - lr * m_hat / (np.sqrt(v_hat) + eps)

        if loss < best_loss:
            best_loss = loss
            best_params = params.copy()

        if verbose and step % 200 == 0:
            print(f"  step {step:4d}: loss={loss:.6e}")

    return best_params, best_loss


# --- Snap to discrete ---

def snap_params(params, eval_fn, n_leaf_params):
    """Snap softmax weights to nearest vertex (0 or 1).

    For leaf params (pairs): snap each pair to argmax.
    For inner params (triples): snap each triple to argmax.
    """
    snapped = params.copy()
    # Leaf params: groups of 2
    for i in range(0, n_leaf_params, 2):
        idx = np.argmax(snapped[i:i+2])
        snapped[i:i+2] = -100
        snapped[i + idx] = 100
    # Inner params: groups of 3
    for i in range(n_leaf_params, len(snapped), 3):
        idx = np.argmax(snapped[i:i+3])
        snapped[i:i+3] = -100
        snapped[i + idx] = 100
    return snapped


def decode_params(params, n_leaf_params):
    """Decode snapped params into human-readable form."""
    choices = {0: '1', 1: 'x', 2: 'f'}
    result = []
    for i in range(0, n_leaf_params, 2):
        idx = int(np.argmax(params[i:i+2]))
        result.append(f"leaf: {choices[idx]}")
    for i in range(n_leaf_params, len(params), 3):
        idx = int(np.argmax(params[i:i+3]))
        result.append(f"inner: {choices[idx]}")
    return result


# --- Experiments ---

def run_experiment(name, target_fn, eval_fn, n_params, n_leaf_params,
                   x_range=(0.1, 3.0), n_points=50, n_runs=10, n_steps=2000):
    """Run multiple optimization attempts for a target function."""
    print(f"\n{'='*70}")
    print(f"TARGET: {name}")
    print(f"Tree: {n_params} params, {n_runs} random runs, {n_steps} steps each")
    print(f"{'='*70}")

    x_data = np.linspace(x_range[0], x_range[1], n_points)
    y_data = target_fn(x_data)

    successes = 0
    for run in range(n_runs):
        params = np.random.randn(n_params) * 0.5
        best_params, best_loss = adam_optimize(
            params, x_data, y_data, eval_fn,
            lr=0.02, n_steps=n_steps, verbose=False
        )

        # Try snapping
        snapped = snap_params(best_params, eval_fn, n_leaf_params)
        snap_loss = mse_loss(snapped, x_data, y_data, eval_fn)

        exact = snap_loss < 1e-20
        good = best_loss < 1e-6
        status = "EXACT" if exact else ("good" if good else "miss")
        if exact:
            successes += 1

        print(f"  run {run:2d}: loss={best_loss:.3e}  snap_loss={snap_loss:.3e}  [{status}]"
              + (f"  {decode_params(snapped, n_leaf_params)}" if exact else ""))

    print(f"\nExact recovery: {successes}/{n_runs} ({100*successes/n_runs:.0f}%)")
    return successes


if __name__ == "__main__":
    np.random.seed(42)

    print("EML MASTER FORMULA OPTIMIZATION EXPERIMENTS")
    print("Using numerical gradients (no autodiff dependency)\n")

    # --- Depth 1: recover exp(x) ---
    # exp(x) = eml(x, 1), so params should snap to: leaf1=x, leaf2=1
    run_experiment(
        "exp(x)",
        target_fn=np.exp,
        eval_fn=eval_depth1_tree,
        n_params=4,
        n_leaf_params=4,
        x_range=(0.1, 2.0),
        n_runs=20,
        n_steps=1000,
    )

    # --- Depth 1: recover eml(1, x) = e - ln(x) ---
    run_experiment(
        "e - ln(x)",
        target_fn=lambda x: np.e - np.log(x),
        eval_fn=eval_depth1_tree,
        n_params=4,
        n_leaf_params=4,
        x_range=(0.1, 3.0),
        n_runs=20,
        n_steps=1000,
    )

    # --- Depth 2: recover exp(exp(x)) = eml(eml(x,1), 1) ---
    run_experiment(
        "exp(exp(x))",
        target_fn=lambda x: np.exp(np.exp(x)),
        eval_fn=eval_depth2_tree,
        n_params=14,
        n_leaf_params=8,
        x_range=(-1.0, 1.0),  # narrow range to avoid overflow
        n_runs=20,
        n_steps=3000,
    )

    # --- Depth 2: recover ln(x) ---
    # ln(x) = eml(1, eml(eml(1,x), 1)) -- this is depth 3 in pure EML,
    # but with bootstrapped exp/ln it might fit in a depth-2 master formula
    # if the formula can represent the composition.
    run_experiment(
        "ln(x)",
        target_fn=np.log,
        eval_fn=eval_depth2_tree,
        n_params=14,
        n_leaf_params=8,
        x_range=(0.1, 5.0),
        n_runs=20,
        n_steps=3000,
    )

    print("\n" + "=" * 70)
    print("NOTES:")
    print("- This uses numerical gradients (slow). Torch/JAX autodiff would be")
    print("  orders of magnitude faster and more stable.")
    print("- The depth-2 master formula searches over all elementary functions")
    print("  expressible as depth-2 EML trees -- a small but nontrivial space.")
    print("- 'EXACT' means snap_loss < 1e-20 (machine epsilon squared).")
    print("- The paper reports 100% recovery at depth 2 with proper setup.")
    print("  Our naive approach may underperform -- that's diagnostic, not bad.")
    print("=" * 70)
