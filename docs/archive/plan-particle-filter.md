> **DEPRECATED** — This plan tracks the dormant EML/LNS8 hardware workstream.
> See AGENTS.md ("Dormant: EML operator research") for current status. Retained as
> historical record.

# Plan: Cycle-Accurate 8-bit LNS Particle Filter Simulation

## Context

We've established that EML/LNS at 8-bit gives ~1e-3 accuracy on elementary functions (500x better than degree-16 Chebyshev) with a 256-byte shared table. The motivating application is real-time Bayesian estimation on low-power edge devices: 1000 particles × 10 heterogeneous sensors at 200+ Hz on ~1mW.

The proof-of-concept: a cycle-accurate simulation that runs a particle filter entirely in 8-bit integer LNS arithmetic, counts cycles per operation, and compares estimation accuracy against float64. This demonstrates the approach works end-to-end before committing to HDL.

## Files

- `experiments/08_lns_cycle_accurate.py` — integer LNS arithmetic engine with cycle counting (numpy only)
- `experiments/09_particle_filter_lns.py` — particle filter + sensor library + benchmark (numpy, matplotlib)

## Part 1: `08_lns_cycle_accurate.py` — Integer LNS Engine

### Representation: LNS8

8-bit fixed-point log-magnitude: 1 sign bit + 7-bit signed s2.4 format (2 integer + 4 fractional bits).
- Dynamic range: 2^(-8) to 2^(+7.94) ≈ 0.004 to 245 (62,500:1)
- Resolution: 1/16 in log2 domain ≈ 4.4% relative precision
- Special: all-zeros = exact zero

### Gaussian Logarithm Tables

Two 128-byte tables (256 bytes total):
- `phi_plus[i]` = round(log2(1 + 2^(i/16))) for i in 0..127 — used in same-sign addition
- `phi_minus[i]` = round(log2(|1 - 2^(-i/16)|)) for i in 0..127 — used in different-sign subtraction

Build tables at module load time from float64, quantize to 7-bit fixed-point.

### Operations (all return result + cycle count)

| Operation | Algorithm | Cycles |
|-----------|-----------|--------|
| `lns8_multiply(a, b)` | XOR signs, add log_mags | 1 |
| `lns8_divide(a, b)` | XOR signs, subtract log_mags | 1 |
| `lns8_add(a, b)` | Gaussian log table lookup + max + add | 4 |
| `lns8_subtract(a, b)` | Negate + add | 5 |
| `lns8_exp(a)` | Convert real→log domain (a * log2(e)) | 2 |
| `lns8_ln(a)` | Convert log→real domain (log_mag * ln(2)) | 2 |
| `lns8_eml(x, y)` | exp + ln + subtract | 9 |

For exp/ln: compute correct value via float64, then quantize result to LNS8. Models the cycle cost of hardware format conversion without implementing barrel shifter logic.

### Validation

Compare every operation against `03_lns_prototype.py` (float64 LNS) and `06_lns_precision.py` (quantized torch) on a sweep of inputs. Print error table and confirm agreement.

### Key reuse
- `_gaussian_log_add()` from `03_lns_prototype.py` — algorithm reference for building tables
- `_quantize_real()` from `06_lns_precision.py` — validate integer results match float64 quantization at 4 frac bits

## Part 2: `09_particle_filter_lns.py` — Particle Filter + Benchmark

### Toy Problem: 1D Target Tracking

State: position x(t), constant-velocity dynamics with process noise.
```
x(t+1) = x(t) + v*dt + noise
```

### Sensor Library (5 types, each a sequence of LNS8 ops)

Sensor likelihoods are hand-coded as LNS8 operation sequences (not EML trees — known functions don't need tree discovery):

1. **Gaussian range**: log p ∝ -(z-x)²/2σ² → subtract, square (multiply), scale, negate. ~8 cycles.
2. **Exponential distance**: log p ∝ -λ|z-x| → subtract, abs, multiply. ~6 cycles.
3. **Poisson count**: log p ∝ z·ln(x) - x → ln, multiply, subtract. ~8 cycles.
4. **Rayleigh**: log p ∝ ln(z) - z²/(2x²) → ln, square, divide, subtract. ~10 cycles.
5. **Log-normal**: log p ∝ -((ln z - ln x)/σ)² → ln, ln, subtract, square, scale. ~12 cycles.

### Particle Filter Stages

**Predict** (5 cycles/particle): add noise = multiply (scale) + add.
**Weight** (N_sensors × ~8-12 cycles/particle): evaluate each sensor likelihood, accumulate log-weights. Weight accumulation in LNS is integer addition (1 cycle).
**Resample** (~6 cycles/particle): log-to-linear conversion (exp, 2 cycles) + cumulative sum (add, 4 cycles). Subtract max log-weight first to avoid overflow.

### Cycle Budget (1000 particles × 10 sensors)

| Stage | Per-particle | Total (1000 particles) |
|-------|-------------|----------------------|
| Predict | 5 | 5,000 |
| Weight (10 sensors × ~9 avg) | 90 | 90,000 |
| Resample | 6 | 6,000 |
| **Total** | | **~101,000** |

At 50 MHz: 101,000 / 50,000,000 = 2.0ms → **~500 Hz update rate**.

### Benchmark Output

Run with configurations: (100, 3), (1000, 5), (1000, 10) particles × sensors.
For each:
- RMSE of LNS8 estimate vs true trajectory
- RMSE of float64 estimate vs true trajectory
- Total cycles and breakdown
- Projected Hz at 50 MHz
- Comparison to MCU estimate (N_particles × N_sensors × 50μs)

### Key reuse
- `make_exact_tree()` and `get_test_configs()` from `06_lns_precision.py` — sensor tree configs
- `eval_tree_quantized()` from `06_lns_precision.py` — if any sensors use EML tree evaluation
- Particle filter structure is standard bootstrap PF (predict/weight/resample)

## Implementation Order

1. Build `08_lns_cycle_accurate.py`: LNS8 class, tables, all operations, validation
2. Build sensor library in `09_particle_filter_lns.py`: 5 sensor types as LNS8 op sequences
3. Build particle filter engine: predict/weight/resample with cycle counting
4. Build float64 reference PF and benchmark harness
5. Run benchmark, generate comparison tables

## Verification

1. **08**: All LNS8 operations match float64 within expected 8-bit quantization error
2. **09**: LNS8 particle filter RMSE is within 2x of float64 RMSE (8-bit precision sufficient for statistics)
3. **09**: Cycle count confirms >200 Hz at 1000 particles × 10 sensors at 50 MHz
4. **09**: Printed comparison table shows 100x throughput advantage over MCU estimate

## Known Challenges

- **Weight dynamic range**: 1000 particles × 10 sensors can produce extreme weight ratios. Mitigation: subtract max log-weight before resample (standard practice, N integer subtracts = N cycles).
- **Resampling in log domain**: need log-to-linear conversion for cumulative sum. Alternative: log-domain sequential search avoids conversion entirely.
- **Process noise generation**: model as external hardware RNG; exclude from cycle count.
