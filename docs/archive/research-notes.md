> **DEPRECATED** — These notes track the dormant EML/LNS8 operator research.
> See AGENTS.md ("Dormant: EML operator research") for current status. Retained as
> historical record.

# EML Research Notes

Notes from initial analysis of Odrzywołek 2026 and follow-on research.

## The Core Result

eml(x, y) = exp(x) - ln(y), with constant 1, generates ALL elementary functions.

Reduction path: 36 primitives (full scientific calculator) -> 7 (Wolfram) -> 6 ->
4 -> 3 (EML + constant 1). No further reduction possible (need at least one
binary operator + one terminal symbol).

Key properties of the search that found it:
- All minimal configurations share: inverse function pairs + non-commutativity
- The asymmetry (subtraction, not addition) is load-bearing -- provides both
  growth and inversion in one operation
- Discovery was by systematic ablation + numeric bootstrapping using
  algebraically independent transcendentals (Schanuel conjecture)

Cousins: EDL(x,y) = exp(x)/ln(y) with constant e; -eml(y,x) with constant -inf;
ternary T(x,y,z) = e^x/ln(x) * ln(z)/e^y needing no distinguished constant.

## Complexity Ordering

EML Kolmogorov complexity (RPN program length, direct search):

| Function | K (direct) | Notes |
|----------|-----------|-------|
| exp(x)   | 3         | Trivial: eml(x, 1) |
| ln(x)    | 7         | eml(1, eml(eml(1,x), 1)) |
| x - y    | 11        | |
| -x       | 15        | |
| 1/x      | 15        | |
| x * y    | 17        | Easier than addition! |
| x^2      | 17        | |
| x / y    | 17        | |
| x + y    | 19        | Harder than multiplication |

Addition harder than multiplication reflects the exp-log geometry: multiplication
is natural (sum of logs), addition is unnatural (logsumexp).

## Paper's Tree Architecture (Key Implementation Details)

The paper's actual PyTorch code (v16_final) uses a DIFFERENT architecture than
the master formula described in Section 4.3. Critical differences we discovered:

### Parameterization
- **Leaves**: 3-way softmax over {1, x, y} (bivar) or {1, x} (univar). This
  matches the paper's description.
- **Internal nodes**: NOT a 3-way softmax over {1, x, f}. Instead, each internal
  node has a **2-element sigmoid blend gate**. Each sigmoid controls ONE child:
  s=1 → replace child with constant 1, s=0 → pass child through. This is a
  fundamentally different parameterization from our initial implementation.

### Training procedure
- **Search phase**: 6000 iterations, tau_search=2.5 (very soft softmax/sigmoid)
- **Hardening phase**: 2000 iterations, tau anneals from 2.5 → 0.01 with
  quadratic schedule. Entropy and binarity penalties ramp up linearly.
- **NaN handling**: Every EML output is clamped with nan_to_num and value
  clamping. Complex blending done component-wise (real/imag separately) to
  avoid 0*Inf=NaN in complex multiplication.
- **Multiple init strategies**: Each seed tested with 4 strategies (biased,
  uniform, xy_biased, random_hot). The "biased" strategy initializes leaf
  logits biased toward constant 1, and gate logits biased toward pass-through
  (s≈1, sigmoid input +4.0).
- **NaN restart**: On NaN streak, restore best-so-far state and reinit optimizer.

### Recovery rates (paper's claims, bivariate targets)
- Depth 2: 100%
- Depth 3-4: ~25%
- Depth 5: <1%
- Depth 6: 0/448

### Our reproduction results
- Depth 2 bivariate: confirmed 100% (both with paper's code and our
  paper-matched implementation)
- Univariate targets at all depths: ~100% (as expected — much simpler search
  space)
- Earlier attempts with wrong architecture (softmax routing instead of sigmoid
  blend gates, no NaN clamping, wrong tau, missing penalties) gave 0% on
  bivariate targets

## Key Research Directions

### 1. Symbolic Regression (in progress)
Can EML trees recover known functions from noisy data? Testing with univariate
targets (exp, ln, decay, etc.) against polynomial baselines. Experiment:
`experiments/05_symbolic_regression.py`.

### 2. Improving Recovery Rates
The paper's ~25% at depth 3-4 is the number to beat. Potential approaches from
our research:
- **Natural gradient** on per-node simplices/sigmoids (convergence rate
  independent of Fisher conditioning — Betancourt/Khan framework)
- **Gumbel-softmax exploration** (stochastic forward pass explores discrete
  space)
- **Branching SVGD** (particle ensemble for multimodal basin problem)
- **DARTS-style fixes** (entropy regularization, bilevel optimization)
- **Symmetry-breaking constraints** (eliminate redundant basins)

### 3. Hardware: LNS Representation
In Logarithmic Number System, EML reduces to standard LNS operations:
- exp(x) = exponent manipulation (nearly free)
- ln(y) = read stored log-magnitude (a wire)
- subtraction = LNS subtract (Gaussian logarithm — the one real operation)

Overflow problem largely disappears. Depth-7 tree evaluator: ~15k-30k LUTs on
mid-range FPGA. Arnold & Collange (2009) built a dual-purpose real/complex LNS
ALU.

Pre-reqs before FPGA work:
- LNS bit-width sweep (8/12/16/24/32 bit precision through depth 1-7)
- Gaussian logarithm accuracy analysis
- Pipeline latency model (spreadsheet before HDL)

### 4. Variational Inference (speculative)
EML trees as variational family: parameterize log q(z) as an EML tree. Potential
advantages: completeness (covers all elementary functions), interpretability
(snapped weights = named distribution), no diffeomorphism constraint (unlike
normalizing flows). Needs approximation efficiency results first.

## Experiment Files

| File | Status | Purpose |
|------|--------|---------|
| `01_eml_basics.py` | Done | Verify core identities in numpy |
| `02_master_formula.py` | Superseded | Numpy numerical gradients (too slow) |
| `03_lns_prototype.py` | Done | LNS arithmetic simulation |
| `04_torch_master_formula.py` | Working | Paper-matched tree with torch autograd |
| `05_symbolic_regression.py` | Testing | Recover functions from noisy data |

## Numerical Considerations

- EML requires complex arithmetic internally (trig via Euler's formula)
- Works in numpy and pytorch out of the box
- Needs NaN/Inf clamping on every EML output (critical for training stability)
- Branch cut issue: EML's ln(z) has a 2πi jump for negative reals
- Paper's code does component-wise real/imag blending to avoid 0*Inf=NaN
  in complex multiplication at sigmoid boundaries
