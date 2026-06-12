"""
EML master formula with torch autograd.

Tree architecture implements the paper's Section 4.3 master formula:
- Leaves: softmax over {1, x, y} (or {1, x} for univariate)
- Internal nodes: softmax routing — per-input choice among
  {1, x, [y,] f(child)} with learned weights (α + βx + γf style)
- NaN/Inf clamping on every EML output
- Three-stage training: search (high tau) → harden (anneal tau + penalties) → snap

This generalizes the sigmoid blend gate architecture by allowing
the identity function x (and y) to be routed at internal nodes,
not just at leaves. This enables representation of functions like
exp(x) at depth > 1 for univariate inputs.

Usage:
    uv run --with torch python experiments/04_torch_master_formula.py
    uv run --with torch python experiments/04_torch_master_formula.py --depth 3 --target eml_depth3
    uv run --with torch python experiments/04_torch_master_formula.py --depth 2 --runs 20
"""

import argparse
import time
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


DTYPE = torch.complex128
REAL_DTYPE = torch.float64
EML_CLAMP = 1e300
BYPASS_THR = 1.0 - torch.finfo(torch.float64).eps


# --- Core ---

def eml(x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    """eml(x, y) = exp(x) - ln(y), complex128."""
    return torch.exp(x) - torch.log(y)


# --- EML Tree (master formula architecture) ---

class EMLTree(nn.Module):
    """Full binary EML tree with softmax routing at internal nodes.

    Architecture (paper Section 4.3 master formula):
    - 2^depth leaves, each a softmax over {1, x, [y]}
    - 2^depth - 1 internal nodes, each with two softmax routers
      (one per EML input), choosing among {1, x, [y,] f(child)}
    - Bottom-up evaluation: pair leaves → route → EML → pair → route → EML → ...

    Route choices per input:
    - Univariate: {1, x, f} → 3 choices
    - Bivariate:  {1, x, y, f} → 4 choices

    Init strategies:
    - "biased": leaves biased toward 1, routes biased toward constant 1
    - "uniform": random init, routes mildly biased toward constant 1
    - "xy_biased": leaves and routes biased toward variables x, y
    - "random_hot": random one-hot-ish leaves/routes, some forced to f
    """

    INIT_STRATEGIES = ["biased", "uniform", "xy_biased", "random_hot"]

    def __init__(self, depth: int, n_vars: int = 2,
                 init_scale: float = 1.0, init_strategy: str = "biased"):
        super().__init__()
        self.depth = depth
        self.n_vars = n_vars
        self.n_leaves = 2 ** depth
        self.n_internal = self.n_leaves - 1

        n_leaf_choices = n_vars + 1   # {1, x} or {1, x, y}
        n_route_choices = n_vars + 2  # {1, x, f} or {1, x, y, f}

        if init_strategy == "biased":
            leaf_init = torch.randn(self.n_leaves, n_leaf_choices, dtype=REAL_DTYPE) * init_scale
            leaf_init[:, 0] += 2.0  # bias toward constant 1
            route_init = torch.randn(self.n_internal, 2, n_route_choices, dtype=REAL_DTYPE) * init_scale
            route_init[:, :, 0] += 2.0  # bias toward constant 1
        elif init_strategy == "xy_biased":
            leaf_init = torch.randn(self.n_leaves, n_leaf_choices, dtype=REAL_DTYPE) * init_scale
            for i in range(1, n_leaf_choices):
                leaf_init[:, i] += 1.0  # bias toward variables
            route_init = torch.randn(self.n_internal, 2, n_route_choices, dtype=REAL_DTYPE) * init_scale
            for i in range(1, n_vars + 1):
                route_init[:, :, i] += 1.0  # bias toward x, y
        elif init_strategy == "random_hot":
            leaf_init = torch.randn(self.n_leaves, n_leaf_choices, dtype=REAL_DTYPE) * init_scale
            hot_idx = torch.randint(0, n_leaf_choices, (self.n_leaves,))
            leaf_init[torch.arange(self.n_leaves), hot_idx] += 3.0
            route_init = torch.randn(self.n_internal, 2, n_route_choices, dtype=REAL_DTYPE) * init_scale
            n_inputs = self.n_internal * 2
            route_flat = route_init.view(n_inputs, n_route_choices)
            hot_route = torch.randint(0, n_route_choices, (n_inputs,))
            route_flat[torch.arange(n_inputs), hot_route] += 3.0
            # Force ~25% of inputs to route to f (child pass-through)
            f_mask = torch.rand(n_inputs) < 0.25
            if f_mask.any():
                route_flat[f_mask] = torch.randn(f_mask.sum(), n_route_choices, dtype=REAL_DTYPE) * init_scale
                route_flat[f_mask, -1] += 3.0
        else:  # "uniform" and fallback
            leaf_init = torch.randn(self.n_leaves, n_leaf_choices, dtype=REAL_DTYPE) * init_scale
            route_init = torch.randn(self.n_internal, 2, n_route_choices, dtype=REAL_DTYPE) * init_scale
            route_init[:, :, 0] += 2.0  # mild bias toward constant 1

        self.leaf_logits = nn.Parameter(leaf_init)
        self.route_logits = nn.Parameter(route_init)

    def forward(self, x, y=None, tau_leaf=1.0, tau_gate=1.0, use_gumbel=False):
        x_real = x.to(REAL_DTYPE)
        x = x.to(DTYPE)
        batch_size = x.shape[0]

        if self.n_vars >= 2 and y is not None:
            y_real = y.to(REAL_DTYPE)
            y_c = y.to(DTYPE)
        else:
            y_real = None
            y_c = None

        # Leaf evaluation: softmax over {1, x, [y]}
        if use_gumbel:
            leaf_probs = F.gumbel_softmax(self.leaf_logits, tau=tau_leaf, hard=True, dim=1)
        else:
            leaf_probs = torch.softmax(self.leaf_logits / tau_leaf, dim=1)
        ones = torch.ones(batch_size, dtype=DTYPE)

        if self.n_vars == 1:
            candidates = torch.stack([ones, x], dim=1)
        else:
            yv = y_c if y_c is not None else torch.ones(batch_size, dtype=DTYPE)
            candidates = torch.stack([ones, x, yv], dim=1)

        weights = leaf_probs.to(DTYPE)
        current_level = torch.matmul(candidates, weights.T)  # [batch, n_leaves]

        # Bottom-up: pair children, route, EML
        node_idx = 0
        all_route_probs = []

        while current_level.shape[1] > 1:
            n_pairs = current_level.shape[1] // 2
            left_children = current_level[:, 0::2]
            right_children = current_level[:, 1::2]

            # Routing: per-input choice among {1, x, [y,] f}
            if use_gumbel:
                rp = F.gumbel_softmax(
                    self.route_logits[node_idx:node_idx + n_pairs],
                    tau=tau_gate, hard=True, dim=-1)
            else:
                rp = torch.softmax(
                    self.route_logits[node_idx:node_idx + n_pairs] / tau_gate,
                    dim=-1)  # [n_pairs, 2, n_route_choices]
            all_route_probs.append(rp)

            wl = rp[:, 0, :].unsqueeze(0)  # [1, n_pairs, n_route_choices]
            wr = rp[:, 1, :].unsqueeze(0)

            # Weight for child (f) — last index
            child_wl = wl[:, :, -1]  # [1, n_pairs]
            child_wr = wr[:, :, -1]

            # Bypass child when its weight is negligible (avoids 0*NaN)
            bypass_left = child_wl < (1.0 - BYPASS_THR)
            bypass_right = child_wr < (1.0 - BYPASS_THR)

            # Clean weighted sum over real-valued candidates {1, x, [y]}
            cl = wl[:, :, 0] + wl[:, :, 1] * x_real.unsqueeze(1)
            cr = wr[:, :, 0] + wr[:, :, 1] * x_real.unsqueeze(1)
            if self.n_vars >= 2 and y_real is not None:
                cl = cl + wl[:, :, 2] * y_real.unsqueeze(1)
                cr = cr + wr[:, :, 2] * y_real.unsqueeze(1)

            # NaN-safe blending: real/imag separately
            lr = torch.where(bypass_left, cl, cl + child_wl * left_children.real)
            li = torch.where(bypass_left, torch.zeros_like(lr),
                             child_wl * left_children.imag)
            rr = torch.where(bypass_right, cr, cr + child_wr * right_children.real)
            ri = torch.where(bypass_right, torch.zeros_like(rr),
                             child_wr * right_children.imag)
            left_input = torch.complex(lr, li)
            right_input = torch.complex(rr, ri)

            current_level = eml(left_input, right_input)

            # Clamp to prevent Inf cascades; scrub NaN
            current_level = torch.complex(
                torch.nan_to_num(current_level.real, nan=0.0,
                                 posinf=EML_CLAMP, neginf=-EML_CLAMP
                                 ).clamp(-EML_CLAMP, EML_CLAMP),
                torch.nan_to_num(current_level.imag, nan=0.0,
                                 posinf=EML_CLAMP, neginf=-EML_CLAMP
                                 ).clamp(-EML_CLAMP, EML_CLAMP),
            )

            node_idx += n_pairs

        all_route_probs = torch.cat(all_route_probs, dim=0)  # [n_internal, 2, n_route_choices]
        return current_level.squeeze(1), leaf_probs, all_route_probs

    def snap(self, k=24.0):
        """Hard-project all weights to nearest discrete choice."""
        with torch.no_grad():
            # Leaves: argmax
            lc = torch.argmax(self.leaf_logits, dim=1)
            new_leaf = torch.full_like(self.leaf_logits, -k)
            new_leaf[torch.arange(self.n_leaves), lc] = k
            self.leaf_logits.copy_(new_leaf)
            # Routes: argmax over choices dim
            rc = torch.argmax(self.route_logits, dim=-1)  # [n_internal, 2]
            new_route = torch.full_like(self.route_logits, -k)
            new_route[
                torch.arange(self.n_internal).unsqueeze(1).expand(-1, 2),
                torch.arange(2).unsqueeze(0).expand(self.n_internal, -1),
                rc
            ] = k
            self.route_logits.copy_(new_route)

    def count_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def decode(self) -> list[str]:
        """Human-readable description of current (snapped) configuration."""
        leaf_labels = ['1', 'x', 'y'] if self.n_vars == 2 else ['1', 'x']
        route_labels = ['1', 'x', 'y', 'f'] if self.n_vars == 2 else ['1', 'x', 'f']
        result = []
        with torch.no_grad():
            leaf_probs = torch.softmax(self.leaf_logits, dim=1)
            route_probs = torch.softmax(self.route_logits, dim=-1)
            for i in range(self.n_leaves):
                idx = leaf_probs[i].argmax().item()
                result.append(f"L{i}={leaf_labels[idx]}")
            for i in range(self.n_internal):
                gl = route_labels[route_probs[i, 0].argmax().item()]
                gr = route_labels[route_probs[i, 1].argmax().item()]
                result.append(f"G{i}={gl},{gr}")
        return result


# --- Target functions (matching paper) ---

TARGETS = {
    # Bivariate targets from the paper's code
    'eml_depth2': ('e - log(exp(y) - log(x))',
                   lambda x, y: np.exp(1) - np.log(np.exp(y) - np.log(x)),
                   (1.0, 3.0)),
    'eml_depth3': ('e^e/(e^y - log(x))',
                   lambda x, y: np.exp(np.exp(1)) / (np.exp(y) - np.log(x)),
                   (1.0, 3.0)),
    'eml_depth4': ('log(e^x - log(y))',
                   lambda x, y: np.log(np.exp(x) - np.log(y)),
                   (1.0, 3.0)),
    'eml_depth5': ('log(e-log(e^x - log(y)))',
                   lambda x, y: np.log(np.exp(1) - np.log(np.exp(x) - np.log(y))),
                   (1.0, 3.0)),
}


def make_grid_data(target_fn, lo=1.0, hi=3.0, step=0.1):
    """Generate training grid matching the paper's data generation."""
    xs = np.arange(lo, hi + step * 0.5, step)
    ys = np.arange(lo, hi + step * 0.5, step)
    xx, yy = np.meshgrid(xs, ys, indexing='ij')
    xx, yy = xx.ravel(), yy.ravel()

    # Filter to real domain
    xc = xx.astype(np.complex128)
    yc = yy.astype(np.complex128)
    with np.errstate(all='ignore'):
        tc = target_fn(xc, yc)
    real_mask = (np.abs(tc.imag) < 1e-12) & np.isfinite(tc.real)
    xx, yy, tc = xx[real_mask], yy[real_mask], tc[real_mask].real

    print(f"  Training data: {len(xx)} valid points on [{lo}, {hi}]^2 step={step}",
          flush=True)
    return (torch.tensor(xx, dtype=REAL_DTYPE),
            torch.tensor(yy, dtype=REAL_DTYPE),
            torch.tensor(tc, dtype=DTYPE))


# --- Training ---

@dataclass
class TrainResult:
    best_loss: float
    snap_mse: float
    exact: bool
    decoded: list[str]
    wall_time_s: float
    strategy: str


def train_one_run(depth: int, x_train, y_train, t_train,
                  n_vars: int = 2,
                  init_strategy: str = "biased",
                  search_iters: int = 6000,
                  hardening_iters: int = 2000,
                  lr: float = 0.01,
                  tau_search: float = 2.5,
                  tau_hard: float = 0.01,
                  lam_ent: float = 0.02,
                  lam_bin: float = 0.02,
                  seed: int = 42,
                  verbose: bool = False,
                  use_gumbel: bool = False) -> TrainResult:
    """Three-stage training matching the paper's approach.

    If use_gumbel=True: search phase uses soft softmax (warm-up), then
    hardening phase switches to ST-Gumbel-Softmax (hard discrete samples
    with soft gradients). Tau floor is raised to 0.5 to avoid Gumbel
    variance explosion. Entropy penalties are skipped during Gumbel
    phase since hard sampling already enforces discreteness.
    """
    t0 = time.perf_counter()
    torch.manual_seed(seed)

    tree = EMLTree(depth, n_vars=n_vars, init_strategy=init_strategy)
    optimizer = torch.optim.Adam(tree.parameters(), lr=lr)

    tau_hard_eff = max(tau_hard, 0.5) if use_gumbel else tau_hard

    best_soft_loss = float('inf')
    best_snap_loss = float('inf')
    best_soft_state = None
    total_iters = search_iters + hardening_iters

    for it in range(1, total_iters + 1):
        # Phase selection
        if it <= search_iters:
            tau = tau_search
            cur_lam_ent = 0.0
            cur_lam_bin = 0.0
            gumbel_active = False
        else:
            t_frac = (it - search_iters) / hardening_iters
            t_tau = t_frac ** 2  # quadratic schedule
            tau = tau_search * (tau_hard_eff / tau_search) ** t_tau
            if use_gumbel:
                gumbel_active = True
                cur_lam_ent = 0.0  # not needed with hard sampling
                cur_lam_bin = 0.0
            else:
                gumbel_active = False
                cur_lam_ent = t_frac * lam_ent
                cur_lam_bin = t_frac * lam_bin

        optimizer.zero_grad()
        pred, leaf_probs, route_probs = tree(x_train, y_train,
                                             tau_leaf=tau, tau_gate=tau,
                                             use_gumbel=gumbel_active)

        # Data loss
        data_loss = torch.mean((pred - t_train).abs() ** 2).real

        # Penalty terms (skipped during Gumbel phase)
        eps = 1e-12
        leaf_ent = -(leaf_probs * (leaf_probs + eps).log()).sum(dim=1).mean()
        route_ent = -(route_probs * (route_probs + eps).log()).sum(dim=-1).mean()
        total_loss = data_loss + cur_lam_ent * leaf_ent + cur_lam_bin * route_ent

        if not torch.isfinite(total_loss):
            if best_soft_state is not None:
                tree.load_state_dict(best_soft_state)
                optimizer = torch.optim.Adam(tree.parameters(), lr=lr)
            continue

        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(tree.parameters(), 1.0)
        optimizer.step()

        soft_loss = data_loss.item()
        if not gumbel_active:
            # Soft phase: save best by soft loss
            if np.isfinite(soft_loss) and soft_loss < best_soft_loss:
                best_soft_loss = soft_loss
                best_soft_state = {k: v.detach().clone() for k, v in tree.state_dict().items()}
        elif it % 50 == 0:
            # Gumbel phase: save best by snap quality (argmax without noise)
            with torch.no_grad():
                sp, _, _ = tree(x_train, y_train, tau_leaf=0.01, tau_gate=0.01)
                snap_check = torch.mean((sp - t_train).abs() ** 2).real.item()
            if np.isfinite(snap_check) and snap_check < best_snap_loss:
                best_snap_loss = snap_check
                best_soft_loss = snap_check  # for reporting
                best_soft_state = {k: v.detach().clone() for k, v in tree.state_dict().items()}

        if verbose and it % 500 == 0:
            phase = "search" if it <= search_iters else ("gumbel" if gumbel_active else "harden")
            # Quick hard eval
            with torch.no_grad():
                hp, _, _ = tree(x_train, y_train, tau_leaf=tau_hard, tau_gate=tau_hard)
                hard_mse = torch.mean((hp - t_train).abs() ** 2).real.item()
            print(f"    it {it:5d}: soft={soft_loss:.3e}  hard={hard_mse:.3e}  "
                  f"tau={tau:.3f}  [{phase}]", flush=True)

    # Restore best and snap
    if best_soft_state is not None:
        tree.load_state_dict(best_soft_state)

    from copy import deepcopy
    snapped = deepcopy(tree)
    snapped.snap()

    with torch.no_grad():
        sp, _, _ = snapped(x_train, y_train, tau_leaf=0.01, tau_gate=0.01)
        snap_mse = torch.mean((sp - t_train).abs() ** 2).real.item()

    # Multi-sample Gumbel snap: try many random discrete configs, keep best
    if use_gumbel:
        k = 24.0
        best_leaf = None
        for _ in range(500):
            with torch.no_grad():
                lp = F.gumbel_softmax(tree.leaf_logits, tau=0.5, hard=True, dim=1)
                rp = F.gumbel_softmax(tree.route_logits, tau=0.5, hard=True, dim=-1)
                snapped.leaf_logits.copy_(lp * 2 * k - k)
                snapped.route_logits.copy_(rp * 2 * k - k)
                gp, _, _ = snapped(x_train, y_train, tau_leaf=0.01, tau_gate=0.01)
                g_mse = torch.mean((gp - t_train).abs() ** 2).real.item()
            if np.isfinite(g_mse) and g_mse < snap_mse:
                snap_mse = g_mse
                best_leaf = lp.clone()
                best_route = rp.clone()
        if best_leaf is not None:
            with torch.no_grad():
                snapped.leaf_logits.copy_(best_leaf * 2 * k - k)
                snapped.route_logits.copy_(best_route * 2 * k - k)
        else:
            # No Gumbel sample beat argmax snap — restore original
            snapped = deepcopy(tree)
            snapped.snap()

    wall_time = time.perf_counter() - t0
    exact = np.isfinite(snap_mse) and snap_mse < 1e-20
    decoded = snapped.decode()

    return TrainResult(best_soft_loss, snap_mse, exact, decoded, wall_time, init_strategy)


# --- Experiment runner ---

def run_experiment(target_name: str, depth: int, n_seeds: int = 8,
                   seed0: int = 137, n_vars: int = 2,
                   verbose: bool = False, **train_kwargs):
    """Run multiple seeds × strategies, matching the paper's sweep."""
    target_label, target_fn, (lo, hi) = TARGETS[target_name]

    print(f"\n{'='*78}")
    print(f"TARGET: {target_label}  |  depth={depth}  seeds={n_seeds}  "
          f"strategies={len(EMLTree.INIT_STRATEGIES)}")
    print(f"{'='*78}", flush=True)

    x_train, y_train, t_train = make_grid_data(target_fn, lo=lo, hi=hi)

    successes = 0
    fit_successes = 0
    total_runs = 0
    total_time = 0.0

    for seed in range(seed0, seed0 + n_seeds):
        for strategy in EMLTree.INIT_STRATEGIES:
            total_runs += 1
            result = train_one_run(
                depth, x_train, y_train, t_train,
                n_vars=n_vars, init_strategy=strategy, seed=seed,
                verbose=verbose and total_runs == 1,
                **train_kwargs,
            )

            total_time += result.wall_time_s
            if result.exact:
                successes += 1
            if result.snap_mse < 1e-6:
                fit_successes += 1

            status = "EXACT" if result.exact else ("fit" if result.snap_mse < 1e-6 else "miss")
            decoded_short = ' '.join(result.decoded[:8])

            print(f"  seed={seed} {strategy:12s}: soft={result.best_loss:.3e}  "
                  f"snap={result.snap_mse:.3e}  t={result.wall_time_s:.0f}s  "
                  f"[{status:5s}]"
                  + (f"  {decoded_short}" if result.exact else ""),
                  flush=True)

    avg_time = total_time / total_runs
    print(f"\n  SUMMARY: exact={successes}/{total_runs} ({100*successes/total_runs:.0f}%)  "
          f"fit={fit_successes}/{total_runs}  "
          f"avg={avg_time:.0f}s/run  total={total_time:.0f}s",
          flush=True)

    return successes, total_runs


def main():
    parser = argparse.ArgumentParser(description='EML master formula (softmax routing)')
    parser.add_argument('--depth', type=int, default=2)
    parser.add_argument('--target', type=str, default='eml_depth2',
                        choices=list(TARGETS.keys()))
    parser.add_argument('--seeds', type=int, default=8,
                        help='Number of seeds (each × 4 strategies)')
    parser.add_argument('--seed0', type=int, default=137)
    parser.add_argument('--search-iters', type=int, default=6000)
    parser.add_argument('--harden-iters', type=int, default=2000)
    parser.add_argument('--lr', type=float, default=0.01)
    parser.add_argument('--tau-search', type=float, default=2.5)
    parser.add_argument('--tau-hard', type=float, default=0.01)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--gumbel', action='store_true',
                        help='Use ST-Gumbel-Softmax during hardening phase')
    args = parser.parse_args()

    mode = "ST-Gumbel-Softmax" if args.gumbel else "Softmax Routing"
    print(f"EML Master Formula — {mode}", flush=True)
    torch.set_default_dtype(REAL_DTYPE)

    run_experiment(
        args.target, args.depth, n_seeds=args.seeds, seed0=args.seed0,
        verbose=args.verbose,
        search_iters=args.search_iters, hardening_iters=args.harden_iters,
        lr=args.lr, tau_search=args.tau_search, tau_hard=args.tau_hard,
        use_gumbel=args.gumbel,
    )


if __name__ == '__main__':
    main()
