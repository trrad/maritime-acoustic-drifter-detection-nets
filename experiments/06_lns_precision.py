"""
LNS precision analysis: does fixed-point EML hold up through depth?

Uses the actual EMLTree from 04_torch_master_formula.py with snapped
(known-exact) configurations, but intercepts the arithmetic to simulate
fixed-point LNS quantization at each operation.

The key question: can we evaluate elementary functions via composed EML
operations at practical precisions, or does rounding error kill accuracy?

Usage:
    uv run --with torch python experiments/06_lns_precision.py
"""

import numpy as np
import torch
from importlib.machinery import SourceFileLoader
from copy import deepcopy

# Import the real EML tree implementation
eml_mod = SourceFileLoader('eml_mod', 'experiments/04_torch_master_formula.py').load_module()
EMLTree = eml_mod.EMLTree
DTYPE = eml_mod.DTYPE
REAL_DTYPE = eml_mod.REAL_DTYPE

torch.set_default_dtype(REAL_DTYPE)


# --- Fixed-point quantization layer ---

def quantize(x_real, frac_bits):
    """Quantize a real tensor to fixed-point precision (simulate LNS rounding).

    In LNS, values are stored as fixed-point log-magnitudes.
    This simulates the rounding error by:
    1. Converting to log2 domain
    2. Rounding to frac_bits fractional bits
    3. Converting back

    Operates on the real and imaginary parts of complex tensors separately.
    """
    if frac_bits is None:
        return x_real  # no quantization (float64 baseline)

    scale = float(1 << frac_bits)

    if torch.is_complex(x_real):
        return torch.complex(
            _quantize_real(x_real.real, scale),
            _quantize_real(x_real.imag, scale),
        )
    return _quantize_real(x_real, scale)


def _quantize_real(t, scale):
    """Quantize real tensor via LNS round-trip."""
    sign = torch.sign(t)
    mag = torch.abs(t)

    # Avoid log of zero
    nonzero = mag > 0
    result = torch.zeros_like(t)

    if nonzero.any():
        log2_mag = torch.log2(mag[nonzero])
        # Quantize the log-magnitude
        log2_q = torch.round(log2_mag * scale) / scale
        result[nonzero] = sign[nonzero] * torch.pow(2.0, log2_q)

    return result


def eml_quantized(x, y, frac_bits):
    """eml(x, y) = exp(x) - ln(y) with quantization after each sub-operation."""
    exp_x = quantize(torch.exp(x), frac_bits)
    ln_y = quantize(torch.log(y), frac_bits)
    result = quantize(exp_x - ln_y, frac_bits)
    return result


# --- Evaluate a snapped EMLTree with quantization ---

def eval_tree_quantized(tree, x, y=None, frac_bits=None):
    """Evaluate a snapped EMLTree with fixed-point quantization at each step.

    Uses the tree's actual leaf_logits and route_logits (should be snapped),
    applying quantization after every arithmetic operation to simulate
    fixed-point LNS.
    """
    x_real = x.to(REAL_DTYPE)
    x_c = x.to(DTYPE)
    batch_size = x.shape[0]

    if tree.n_vars >= 2 and y is not None:
        y_real = y.to(REAL_DTYPE)
        y_c = y.to(DTYPE)
    else:
        y_real = None
        y_c = None

    # Leaf evaluation: snapped = one-hot, so this is just selection
    leaf_probs = torch.softmax(tree.leaf_logits * 100, dim=1)  # hard after snap
    ones = torch.ones(batch_size, dtype=DTYPE)

    if tree.n_vars == 1:
        candidates = torch.stack([ones, x_c], dim=1)
    else:
        yv = y_c if y_c is not None else ones
        candidates = torch.stack([ones, x_c, yv], dim=1)

    weights = leaf_probs.to(DTYPE)
    current_level = torch.matmul(candidates, weights.T)
    current_level = quantize(current_level, frac_bits)

    # Route labels for indexing
    if tree.n_vars == 1:
        # route choices: [1, x, f] -> indices 0, 1, 2
        pass
    # else: [1, x, y, f]

    # Bottom-up with routing and quantization
    node_idx = 0
    route_probs = torch.softmax(tree.route_logits * 100, dim=-1)  # hard after snap

    while current_level.shape[1] > 1:
        n_pairs = current_level.shape[1] // 2
        left_children = current_level[:, 0::2]
        right_children = current_level[:, 1::2]

        rp = route_probs[node_idx:node_idx + n_pairs]  # [n_pairs, 2, n_choices]

        # Route each input: pick the selected candidate
        left_inputs = torch.zeros(batch_size, n_pairs, dtype=DTYPE)
        right_inputs = torch.zeros(batch_size, n_pairs, dtype=DTYPE)

        for p in range(n_pairs):
            left_choice = rp[p, 0].argmax().item()
            right_choice = rp[p, 1].argmax().item()

            # Build candidate lists: [1, x, [y,] f]
            left_candidates = [ones, x_c]
            right_candidates = [ones, x_c]
            if tree.n_vars >= 2 and y_c is not None:
                left_candidates.append(y_c)
                right_candidates.append(y_c)
            left_candidates.append(left_children[:, p])
            right_candidates.append(right_children[:, p])

            left_inputs[:, p] = left_candidates[left_choice]
            right_inputs[:, p] = right_candidates[right_choice]

        # Quantize inputs before EML
        left_inputs = quantize(left_inputs, frac_bits)
        right_inputs = quantize(right_inputs, frac_bits)

        # EML with per-op quantization
        current_level = eml_quantized(left_inputs, right_inputs, frac_bits)

        # Clamp (same as real implementation)
        CLAMP = 1e300
        current_level = torch.complex(
            torch.nan_to_num(current_level.real, nan=0.0,
                             posinf=CLAMP, neginf=-CLAMP).clamp(-CLAMP, CLAMP),
            torch.nan_to_num(current_level.imag, nan=0.0,
                             posinf=CLAMP, neginf=-CLAMP).clamp(-CLAMP, CLAMP),
        )

        node_idx += n_pairs

    return current_level.squeeze(1).real


# --- Find exact trees for test functions ---

def find_exact_tree(target_fn, x_data, depth, n_vars=1):
    """Use the optimizer to find an exact EML tree, return snapped tree."""
    t_data = torch.tensor([complex(target_fn(xi)) for xi in x_data.numpy()], dtype=DTYPE)

    for seed in range(137, 200):
        for strategy in EMLTree.INIT_STRATEGIES:
            result = eml_mod.train_one_run(
                depth, x_data, None, t_data,
                n_vars=n_vars, init_strategy=strategy, seed=seed,
                search_iters=6000, hardening_iters=2000,
                use_gumbel=True,
            )
            if result.exact:
                # Reconstruct the snapped tree
                torch.manual_seed(seed)
                tree = EMLTree(depth, n_vars=n_vars, init_strategy=strategy)
                tree.load_state_dict(
                    {k: v.detach().clone() for k, v in
                     eml_mod.train_one_run(
                         depth, x_data, None, t_data,
                         n_vars=n_vars, init_strategy=strategy, seed=seed,
                         search_iters=6000, hardening_iters=2000,
                         use_gumbel=True,
                     ).__dict__.items() if isinstance(v, torch.Tensor)}
                )
                # Actually, just re-run and snap
                return _train_and_snap(depth, x_data, t_data, n_vars, strategy, seed)
    return None


def _train_and_snap(depth, x_data, t_data, n_vars, strategy, seed):
    """Train and return snapped tree."""
    torch.manual_seed(seed)
    tree = EMLTree(depth, n_vars=n_vars, init_strategy=strategy)
    opt = torch.optim.Adam(tree.parameters(), lr=0.01)
    best_loss = float('inf')
    best_state = None

    for it in range(1, 8001):
        if it <= 6000:
            tau = 2.5
        else:
            frac = (it - 6000) / 2000
            tau = 2.5 * (0.01 / 2.5) ** (frac ** 2)
        opt.zero_grad()
        pred, lp, rp = tree(x_data, y=None, tau_leaf=tau, tau_gate=tau)
        dl = torch.mean((pred - t_data).abs() ** 2).real
        if not torch.isfinite(dl):
            if best_state is not None:
                tree.load_state_dict(best_state)
                opt = torch.optim.Adam(tree.parameters(), lr=0.01)
            continue
        dl.backward()
        torch.nn.utils.clip_grad_norm_(tree.parameters(), 1.0)
        opt.step()
        sl = dl.item()
        if np.isfinite(sl) and sl < best_loss:
            best_loss = sl
            best_state = {k: v.detach().clone() for k, v in tree.state_dict().items()}

    if best_state:
        tree.load_state_dict(best_state)
    snapped = deepcopy(tree)
    snapped.snap()
    return snapped


def make_exact_tree(depth, n_vars, leaf_choices, route_choices):
    """Manually construct a snapped EMLTree with known-exact configuration.

    leaf_choices: list of int indices per leaf (0=1, 1=x, 2=y)
    route_choices: list of (left_idx, right_idx) per internal node
                   (0=1, 1=x, [2=y,] last=f)
    """
    tree = EMLTree(depth, n_vars=n_vars)
    k = 24.0
    n_leaf_choices = n_vars + 1
    n_route_choices = n_vars + 2

    with torch.no_grad():
        # Set leaves
        new_leaf = torch.full_like(tree.leaf_logits, -k)
        for i, c in enumerate(leaf_choices):
            new_leaf[i, c] = k
        tree.leaf_logits.copy_(new_leaf)

        # Set routes
        new_route = torch.full_like(tree.route_logits, -k)
        for i, (lc, rc) in enumerate(route_choices):
            new_route[i, 0, lc] = k
            new_route[i, 1, rc] = k
        tree.route_logits.copy_(new_route)

    return tree


# --- Test configurations ---

def get_test_configs():
    """Return known-exact EML tree configurations for test functions."""
    configs = {}

    # exp(x) = eml(x, 1) — depth 1, univariate
    # Leaves: [x, 1], Route at root: left=f(child0=x), right=f(child1=1)
    # Wait -- depth 1 means 2 leaves paired into 1 EML.
    # At depth 1: leaves L0, L1 -> eml(route(L0), route(L1))
    # For exp(x): we need eml(x, 1).
    # Root routes: left=x (idx 1), right=1 (idx 0)
    # Leaves don't matter (routed past), but set to something valid
    configs['exp(x)'] = {
        'depth': 1, 'n_vars': 1,
        'leaf_choices': [0, 0],  # both 1 (ignored by routing)
        'route_choices': [(1, 0)],  # left=x, right=1
        'fn': np.exp,
        'domain': (0.1, 4.0),
    }

    # e - ln(x) = eml(1, x) — depth 1
    configs['e - ln(x)'] = {
        'depth': 1, 'n_vars': 1,
        'leaf_choices': [0, 0],
        'route_choices': [(0, 1)],  # left=1, right=x
        'fn': lambda x: np.e - np.log(x),
        'domain': (0.5, 5.0),
    }

    # exp(x) at depth 2 — root routes left=x, right=1, ignoring children
    # 4 leaves, 3 internal nodes
    # Nodes 0,1 are level-1 (pair leaves), node 2 is root
    configs['exp(x) d2'] = {
        'depth': 2, 'n_vars': 1,
        'leaf_choices': [0, 0, 0, 0],  # all 1 (ignored)
        'route_choices': [
            (0, 0),  # node 0: both 1 (doesn't matter)
            (0, 0),  # node 1: both 1 (doesn't matter)
            (1, 0),  # node 2 (root): left=x, right=1
        ],
        'fn': np.exp,
        'domain': (0.1, 4.0),
    }

    # ln(x) = eml(1, eml(eml(1,x), 1)) — depth 3
    # 8 leaves, 7 internal nodes
    # Level 1 nodes: 0,1,2,3 (pair leaves)
    # Level 2 nodes: 4,5 (pair level-1)
    # Level 3 node: 6 (root)
    #
    # We need: eml(1, eml(eml(1,x), 1))
    # Node 0: leaves L0=1, L1=x -> eml(1, x) if routed as f,f
    #   But with routing: node 0 routes left=f, right=f -> eml(L0, L1) = eml(1, x)
    #   Wait, route 'f' means use child. Leaves ARE the children at level 1.
    #   So route left=f means use L0, route right=f means use L1.
    #   For eml(1, x): L0=1, L1=x, routes=(f, f) -> eml(1, x) = e - ln(x) ✓
    #
    # Node 4 (level 2): pairs node0 and node1.
    #   We need eml(eml(1,x), 1).
    #   Left child = node0 output = e - ln(x). Route left=f -> use child.
    #   Right = 1. Route right=0 (constant 1).
    #   routes=(f=2, 0) for univariate where f is index 2
    #
    # Node 6 (root, level 3): pairs node4 and node5.
    #   We need eml(1, result_of_node4).
    #   Left = 1. Route left=0.
    #   Right child = node5 output. But we need node4's output!
    #   Wait -- node 6 pairs node4 (left) and node5 (right).
    #   We need eml(1, node4_output).
    #   So: route left=0 (constant 1), route right=f (use right child = node5)?
    #   No -- we need node4's output as the RIGHT input to eml.
    #   node6 pairs (node4, node5). node4 is left child, node5 is right child.
    #   route left=0 (constant 1) -> left input is 1
    #   route right needs to be node4's output, but node4 is the LEFT child.
    #   Hmm, the routing only lets you pick the corresponding child (left picks
    #   left child, right picks right child). We can't cross-wire.
    #
    # This means the tree topology constrains which subtree feeds which input.
    # Let me reconsider.
    #
    # Actually for a full binary tree at depth 3:
    # Level 1: nodes 0,1,2,3 from leaf pairs (0,1), (2,3), (4,5), (6,7)
    # Level 2: nodes 4,5 from pairs (node0, node1), (node2, node3)
    # Level 3: node 6 from pair (node4, node5)
    #
    # For ln(x) we found an exact tree earlier. Let me use one of those decoded configs:
    # From our test: L0=1 L1=1 L2=1 L3=x L4=1 L5=x L6=1 L7=1
    #                G0=1,1 G1=x,x G2=f,f G3=1,f G4=1,f G5=f,1 G6=1,f
    configs['ln(x) d3'] = {
        'depth': 3, 'n_vars': 1,
        'leaf_choices': [0, 0, 0, 1, 0, 1, 0, 0],  # 1,1,1,x,1,x,1,1
        'route_choices': [
            (0, 0),  # G0: 1,1
            (1, 1),  # G1: x,x
            (2, 2),  # G2: f,f  (f=index 2 for univariate)
            (0, 2),  # G3: 1,f
            (0, 2),  # G4: 1,f
            (2, 0),  # G5: f,1
            (0, 2),  # G6: 1,f
        ],
        'fn': np.log,
        'domain': (0.5, 5.0),
    }

    return configs


# --- Main ---

def main():
    print("=" * 80)
    print("LNS PRECISION ANALYSIS")
    print("Using actual EMLTree with routing — fixed-point quantization at each op")
    print("=" * 80)

    configs = get_test_configs()
    bit_widths = [None, 8, 12, 16, 24]  # None = float64 baseline

    # First verify all configs are exact at float64
    print("\nVerifying exact trees at float64...", flush=True)
    for name, cfg in configs.items():
        tree = make_exact_tree(cfg['depth'], cfg['n_vars'],
                               cfg['leaf_choices'], cfg['route_choices'])
        x = torch.linspace(*cfg['domain'], 100)
        result = eval_tree_quantized(tree, x, frac_bits=None)
        expected = torch.tensor([cfg['fn'](xi.item()) for xi in x])
        max_err = (result - expected).abs().max().item()
        status = "OK" if max_err < 1e-10 else f"FAIL (max_err={max_err:.2e})"
        print(f"  {name:<25s} depth={cfg['depth']}  max_err={max_err:.2e}  [{status}]",
              flush=True)

    # Precision sweep
    print(f"\n{'=' * 80}")
    print("PRECISION SWEEP: median and max relative error")
    print(f"{'=' * 80}\n")

    header = f"{'Function':<25s} {'Depth':>5s}"
    for bw in bit_widths:
        label = "f64" if bw is None else f"{bw}b"
        header += f"  {label+' median':>12s} {label+' max':>10s}"
    print(header)
    print("-" * len(header))

    for name, cfg in configs.items():
        tree = make_exact_tree(cfg['depth'], cfg['n_vars'],
                               cfg['leaf_choices'], cfg['route_choices'])
        x = torch.linspace(*cfg['domain'], 200)
        expected = torch.tensor([cfg['fn'](xi.item()) for xi in x])

        print(f"{name:<25s} {cfg['depth']:>5d}", end="")

        for bw in bit_widths:
            result = eval_tree_quantized(tree, x, frac_bits=bw)
            mask = expected.abs() > 1e-15
            if mask.sum() == 0:
                print(f"  {'N/A':>12s} {'':>10s}", end="")
                continue
            rel_err = ((result[mask] - expected[mask]).abs() / expected[mask].abs())
            median_err = rel_err.median().item()
            max_err = rel_err.max().item()
            print(f"  {median_err:>12.2e} {max_err:>10.2e}", end="")

        print(flush=True)

    # Table size estimates
    print(f"\n\n{'=' * 80}")
    print("GAUSSIAN LOGARITHM TABLE SIZE (with interpolation)")
    print(f"{'=' * 80}\n")
    print("Direct table (no interpolation):")
    for bw in [8, 12, 16]:
        # Function is significant for |r| < ~b * 2^frac (in fixed-point units)
        # With piecewise-linear interpolation, table can be much smaller
        # For the steep region near 0, need full resolution
        # For |r| > 4, function ≈ max(r, 0), need fewer entries
        direct_entries = 1 << bw
        direct_bytes = direct_entries * bw // 8
        # Piecewise-linear: ~2^(bw/2) segments with linear interp
        pw_entries = 1 << (bw // 2 + 2)
        pw_bytes = pw_entries * bw * 2 // 8  # slope + offset per segment
        print(f"  {bw:2d}-bit: {direct_entries:>8,d} entries = {direct_bytes:>8,d} bytes"
              f"  |  pw-linear: ~{pw_entries:,d} segments = {pw_bytes:,d} bytes")


if __name__ == '__main__':
    main()
