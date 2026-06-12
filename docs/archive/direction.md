> **DEPRECATED** — This doc describes the dormant EML/LNS8 hardware workstream.
> See AGENTS.md ("Dormant: EML operator research") for current status. Retained as
> historical record.

# EML/LNS for Low-Power Real-Time Bayesian Estimation

## The Core Idea

EML (exp(x) - ln(y)) is a single operator that, composed with constant 1,
generates all elementary functions. In Logarithmic Number System (LNS)
arithmetic, EML evaluation reduces to one table lookup (the Gaussian
logarithm) plus addition. An 8-bit implementation needs just a 256-byte
table and a few hundred FPGA LUTs.

This enables a programmable transcendental evaluator: one tiny circuit that
computes any elementary function, configured by loading different EML tree
parameters. No redesign, no resynthesis — just swap the parameters.

## Why It Matters

Real-time Bayesian estimation (particle filters, sensor fusion) requires
evaluating likelihood functions in the inner loop — transcendentals like
exp, ln, powers — once per particle per sensor per timestep. With
heterogeneous sensors, each likelihood is a different function.

On a microcontroller (the current standard for cost/power-constrained
edge devices), each transcendental evaluation takes ~50μs in software.
This limits real-time estimation to small particle counts and slow update
rates.

An 8-bit EML/LNS FPGA evaluates any elementary function in ~500ns — 100x
faster at 1/20th the power. This unlocks:

- 1000+ particles (vs ~100 on MCU) — dramatically better estimation
- 200+ Hz update rate (vs ~2 Hz on MCU) — real-time for fast dynamics
- 10+ heterogeneous sensor types on one chip — all via tree parameters
- ~1mW power budget — suitable for battery-powered autonomous systems

## Key Results So Far

### Architecture (04_torch_master_formula.py)
- Softmax routing at internal nodes: each EML input chooses from {1, x, [y,] f(child)}
- ST-Gumbel-Softmax training with multi-sample snap: 35% exact recovery
  at depth 3 bivariate, exceeding the paper's ~25% rate
- Verified: univariate exp(x) at depth 2 (100%), ln(x) at depth 3 (100% on clean data)

### Precision (06_lns_precision.py)
- 8-bit LNS: ~1e-3 median relative error for depth 1-2 functions
- 16-bit LNS: ~5e-6 median error, viable through depth 3
- Cancellation issue at depth 3 (worst-case, not median) — known LNS limitation
- Gaussian log table: 256 bytes at 8-bit, 128KB at 16-bit

### vs Chebyshev Polynomials (07_lns_vs_polynomial.py)
- At 8-bit, EML gives ~1e-3 accuracy on sigmoid while degree-16 Chebyshev gives >10% error
- EML dominates at 8-12 bit — exactly the edge/ultra-low-power regime
- One shared table (256B) vs per-function polynomial coefficients
- At 16-bit, accuracy advantage persists but table size (128KB) is larger (still fine for FPGA BRAM)

## Motivating Application: Autonomous Low-Power Drone

A river-navigating autonomous vehicle with:
- 10+ sensors (GPS, IMU, compass, depth, sonar, pH, temperature, turbidity, current, camera)
- Each sensor has a different nonlinear calibration/likelihood function
- Real-time Bayesian state estimation (particle filter) for navigation
- Tight power budget (battery, small form factor)

The EML/LNS approach: one FPGA pipeline handles all sensor likelihoods
via programmable tree parameters. Adding a new sensor type = discovering
its EML tree offline (MCMC search) and loading the parameters.

## Architecture: Two-Phase System

### Offline: Function Discovery
Given calibration data for a new sensor, find the EML tree that
represents its likelihood function. Uses MCMC tree search:
- Fixed-depth tree, discrete choices at each node
- Propose: flip a leaf or route choice
- Evaluate: MSE on calibration data
- Accept/reject: Metropolis-Hastings

This reuses patterns from BCF/BART tree samplers (grow/prune/change
moves, GPU-parallel chains via JAX).

### Online: Real-Time Inference
FPGA runs particle filter with EML/LNS likelihood evaluation:
- Predict: propagate particles (standard arithmetic)
- Update: evaluate per-sensor likelihood via EML pipeline (one shared table)
- Resample: select particles by weight (comparisons)
- Weight accumulation is free in LNS (multiply = add log-magnitudes)

## What We Ruled Out

- **EML for variational family discovery**: no advantage over trying known
  parametric families. The search space encoding doesn't help.
- **EML for symbolic regression**: works on clean data, but no continuous
  parameters means it can't handle noise. Standard SR tools (PySR) with
  {+,-,*,/,exp,ln} trees are more practical.
- **16-bit EML replacing all FPGA function units**: table size crossover
  at thousands of functions makes this impractical. The sweet spot is
  8-12 bit where EML dominates.
- **Pure LNS8 for state propagation at large coordinates**: 4.4% relative
  precision makes additive updates invisible. Delta-encoding (reference +
  offset) solves this with minimal hardware cost.

## What's Proven

1. ✓ Cycle-accurate 8-bit LNS particle filter simulation (experiments 08-10)
2. ✓ Estimation accuracy within 1.2-1.4x of float64 (with delta-encoding)
3. ✓ Throughput: 365-752 Hz at 500-1000 particles × 6-10 sensors @ 50 MHz
4. ✓ Integer-only exp/ln (no float64 crutch) with 304 bytes total tables
5. ✓ Delta-encoding closes precision gap at arbitrary distances (6 bytes HW cost)

## What's Next

1. **RTL prototype (Verilog)** — LNS8 ALU targeting Lattice iCE40UP5K.
   Validate cycle counts, measure real area/Fmax/power. This is the
   credibility gate for the hardware story.
2. **MCMC tree discovery** — offline search for sensor EML trees from
   calibration data. Completes the two-phase system story.
3. **FPGA-in-the-loop** — particle filter on real FPGA with simulated
   sensor streams. Proves the full pipeline.
4. **Silicon** — Tiny Tapeout (~$300, limited area) or Efabless OpenMPW
   (free/~$10K, SkyWater 130nm) for actual chip.
