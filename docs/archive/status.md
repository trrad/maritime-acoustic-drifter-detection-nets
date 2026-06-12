> **DEPRECATED** — This status doc tracks the dormant EML/LNS8 hardware workstream.
> See AGENTS.md ("Dormant: EML operator research") for current status. Retained as
> historical record.

# Project Status

Last updated: 2026-04-16

## Where We Are

### Completed

**Theory and foundations:**
- Paper review and analysis (Odrzywołek 2026, arXiv:2603.21852)
- Core EML identities verified in numpy (`01_eml_basics.py`)
- LNS arithmetic prototype (`03_lns_prototype.py`)
- Paper-matched tree architecture (`04_torch_master_formula.py`)
- Depth 2 bivariate recovery at 100%, depth 3 at ~35%
- LNS precision sweep (`06_lns_precision.py`) — 8-bit gives ~1e-3 accuracy
- EML vs Chebyshev comparison (`07_lns_vs_polynomial.py`) — EML dominates at 8-12 bit

**Cycle-accurate LNS8 engine (`08_lns_cycle_accurate.py`):**
- Integer-only 8-bit LNS arithmetic — no float64 in any operation
- 304 bytes total table memory (256 Gaussian log + 48 domain conversion)
- Multiply/divide: 1 cycle. Add: 4 cycles. Exp/ln: 2 cycles.
- Validated against float64 reference and `06_lns_precision.py` quantizer

**1D particle filter proof-of-concept (`09_particle_filter_lns.py`):**
- 5 sensor types (Gaussian, exponential, Poisson, Rayleigh, log-normal)
- 1000 particles × 10 sensors: RMSE within 1.35x of float64, 365 Hz @ 50 MHz
- ~170x throughput vs MCU estimate
- Codex review: two edge-case bugs found and fixed

**3D drone stress test (`10_drone_pf_lns.py`):**
- 6D state [x, y, z, vx, vy, vz] with GPS, baro, speed, sonar sensors
- Exposed fundamental limitation: position updates invisible when |v*dt| < 4.4% * |x|
- Plain LNS8 at 50m origin: 10x worse than float64 on position
- Delta-encoding fix: 1.2x float64 at any distance, 752 Hz, 6 bytes extra hardware
- Velocity estimation fine at all scales (small magnitudes → good LNS8 resolution)

**RTL particle filter (iCE40UP5K):**
- Full LNS8 ALU: MUL, DIV, ADD, SUB, EXP, LN — all validated against Python
- 6D particle filter: sequencer (microcode-driven predict/weight), resampler
  (fixed-point systematic resampling), estimator, memory, RNG, SPI
- Delta-encoding with recentering — position dims store offsets from FP reference
- Weighted estimate (Rao-Blackwellized) on pre-resample bank, uniform mean for
  recentering on post-resample bank (two-phase protocol with bank swap)
- Sensor offset conversion in TB for delta-encoded position dims
- E2E test: 128 particles, 3 sensors, 50 steps, ~73K cycles/step
- 10-seed sweep RMSE vs Python LNS8 delta reference: ~2× gap on position/velocity
  dims. Root cause identified: LNS8→FP→LNS8 round-trip in recentering destroys
  particle diversity. Fix: store position dims in FP (Option B, next step).
- Synthesis: ~2124 LUTs (40%), 17 BRAMs (57%) — comfortable headroom

**Power/throughput at target clocks:**
- 1 MHz (ultra-low power): 0.12 mW, 14 PF steps/sec
- 6 MHz (low power): 0.38 mW, 81 PF steps/sec
- 30 MHz (normal): 1.57 mW, 406 PF steps/sec

### Not Started
- FP storage for position dims (Option B — eliminates recentering round-trip)
- LNS10 ALU (eventual, 4× precision improvement, fits iCE40UP5K)
- MCMC tree discovery for empirical sensor models
- FPGA synthesis and power measurement
- Tiny Tapeout or Efabless submission

## Key Findings

**LNS8 is viable for sampling-based Bayesian estimation.** The multiplicative
weight computation is the natural sweet spot — likelihood products are free
(1 cycle). 304 bytes of tables handle all elementary functions.

**Addition is the weak link.** 4.4% relative precision means additive state
updates (x += v*dt) vanish when the update is small relative to the state.
This is fundamental to uniform-relative-precision arithmetic.

**Delta-encoding solves it.** Store position as offset from a reference point
(3 × 16-bit registers). Offsets stay small, LNS8 resolves them. 6.8% cycle
overhead. The filter becomes position-invariant.

**EML provides completeness, LNS provides performance.** The practical value
is LNS fast-multiply and compact representation. EML's contribution is the
proof that {multiply, divide, add, subtract, exp, ln} suffices for any
elementary sensor model.

**The architecture is a programmable Bayesian coprocessor.** One LNS8 ALU
handles any sensor suite via configurable instruction sequences. Not an
FPGA competitor (more power-efficient, less flexible), not an MCU replacement
(sits alongside as a coprocessor).

## Target Applications

**Aerial drone:** Fast maneuvers, 10–50 Hz PF updates for stabilization.
GPS σ≈1–3m, IMU σ≈0.01–0.1 m/s², baro σ≈0.5–2m. Needs responsive tracking
for obstacle avoidance and waypoint following. 6 MHz clock gives 81 steps/sec.

**River drone (long-distance, ultra-low power):** Current-assisted journeys
over 10–100km. Needs rapid PF updates (10+ Hz) to feed control loop for
fin/rudder actuation against currents. GPS σ≈1–3m. Solar-powered — 1–6 MHz
operation fits within ~4 mW solar budget (50mm² cell). Delta-encoding is
essential: at 3 m/s with 0.1s updates, offsets are 0.3m (LNS8 resolves fine).

**Passive sailboat:** Similar low-power profile to river drone. Wind-driven,
longer missions, relaxed maneuvering requirements but still needs GPS-rate
PF updates for navigation. Solar/battery power budget constrains clock speed.

All applications share: GPS as primary position sensor, need for delta-encoding
(absolute positions grow beyond LNS8 useful range), ultra-low power operation
viable on iCE40UP5K at 1–6 MHz.

## Next Steps (Priority Order)

1. **FP storage for position dims (Option B)** — Store position offsets as
   16-bit signed FP in SPRAM instead of LNS8. Eliminates recentering
   round-trip noise (the dominant ~2× error source). Convert to/from LNS8
   only at predict/weight boundaries.
2. **FPGA synthesis + power measurement** — Validate resource estimates
   on actual hardware, measure real power draw at target clocks
3. **MCMC tree discovery** — offline sensor model search completes the
   "two-phase system" (discover offline, evaluate online)
4. **LNS10 ALU** — 4× precision improvement, 512-entry phi ROM still
   fits 1 EBR, ~100 extra LUTs for wider multiplier
5. **FPGA-in-the-loop demo** — particle filter on real hardware with
   simulated or real sensor data
6. **Tiny Tapeout submission** — real silicon for the LNS8/10 ALU

## External Resources

- Paper: https://arxiv.org/abs/2603.21852
- Paper's code: https://github.com/VA00/SymbolicRegressionPackage
