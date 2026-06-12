> **Note:** The FPGA hardware path described here is dormant (see AGENTS.md
> "Dormant: EML operator research"). The maritime drifter concept is actively
> pursued via the scripted prototype in `experiments/harmonic_prototype/`.
> Platform survey data and sensor/power budgets remain current.

# Platform Design Notes — PF Hardware for Autonomous Low-Power Platforms

## Use Cases

### 1. Maritime: Passive Acoustic Drifter Mesh
- **Mission**: persistent detection and triangulation of small dark vessels (sub-15m, AIS-off)
- **Platform**: matchbook-to-softball-sized passive drifter, 100-250g, 3-class fleet
  (anchors with GPS/Iridium, shear-keepers with depth cycling, pure drifters)
- **Propulsion**: near-passive, ballast for shear-exploitation station-keeping
- **Power**: sub-mW to ~5 mW depending on node class; small Li-ion + 1-4 cm² solar
- **Sensors**: hydrophone (primary), IMU, baro, magnetometer, optional water speed
- **State**: 15-25D (position, velocity, orientation, current field, IMU bias, neighbor ranges)
- **Update rate**: 1 Hz
- **Comms**: LoRa mesh (TDMA, ~220 µW avg) + Iridium SBD on anchors only
- **Key challenge**: fleet-relative nav without continuous GPS. Station-keeping via shear exploitation.
- **Why FPGA PF matters**: high-D state (neighbor fusion + multi-depth current) at sub-mW

### 2. River Current-Rider
- **Mission**: hours-to-days autonomous river navigation/monitoring
- **Propulsion**: uses river current, minimal fin/paddle/ballast adjustments for steering
- **Power**: battery, potentially small solar. Needs multi-day endurance.
- **Sensors**: sonar (depth + side-looking for banks/obstacles), IMU, water speed sensor (drag vane/pitot), barometer, possibly optical flow camera
- **No GPS** (canopy/canyon). All navigation is relative.
- **State**: (cross-stream position, depth, heading, speed_relative_to_water)
- **Update rate**: 10-50 Hz (obstacle avoidance)
- **Accuracy need**: 1-3m relative to banks/obstacles
- **Key challenge**: all-relative navigation, no absolute reference

### 3. Ultralight Near-Passive Aerial
- **Mission**: minutes-to-hours atmospheric sensing / surveillance
- **Propulsion**: near-passive (thermal riding, minimal control surfaces)
- **Power**: extreme constraint — grams of battery
- **Sensors**: IMU, barometer, optical flow, ultrasonic rangefinders
- **No GPS** (weight/power, or indoor/urban)
- **State**: (x_offset, y_offset, altitude, vx, vy, vz, roll, pitch, yaw)
- **Update rate**: 50-100 Hz
- **Accuracy need**: 0.5-1m for collision avoidance
- **Key challenge**: extreme power, MCU-less architecture

## Architecture

### Current: LNS8 + FP Hybrid on iCE40 UP5K

**Resources:**
- ~2,324 / 5,280 LUTs (44%)
- ~1,206 / 1,280 FFs (94% — binding constraint)
- 17 / 30 BRAMs (57%)

**Pipeline:**
- Position predict: FP addition (vel×dt and noise via LNS8 MUL, converted to FP, added to FP position)
- Velocity predict: LNS8 throughout (microcode sequencer)
- Weight kernel: LNS8 throughout (SUB, MUL, DIV, SUB)
- Resampling: fixed-point cumulative sums
- Estimate: 40-bit FP accumulators, 32-bit ref_pos
- Recentering: FP subtraction

**Performance:**
- ~55K cycles/step @ 30 MHz = 544 Hz throughput
- ~10-20 µW at 1 Hz update rate
- 128 particles, 6 state dimensions, 3 sensors

**Accuracy (10-seed sweep, 100 steps, GPS σ=1.0m scenario):**
- x: 1.64m RMSE (1.64σ) — largest displacement dim
- y: 1.08m (0.72σ)
- z: 0.67m (0.84σ)
- Velocities: 0.34-0.40 m/s

### Quantization Error Analysis

The ~1.0m gap between RTL (1.64m) and Python float64 reference (0.64m) on x comes from:
1. Weight kernel: 4 LNS8 ops per sensor × 3 sensors, each rounding to 256 levels
2. vel×dt multiply: 8-bit LNS8 MUL output
3. NOT from: position storage (16-bit FP), FP addition, recentering (all exact)

The error scales with displacement: ~0.005m per step per 2m displacement.
For relative-measurement platforms (river, aerial), displacements between corrections
are small, so LNS8 error is well below sensor noise.

### Improvement Path

**Within UP5K (FF-constrained):**
- FP subtract for z-x in weight kernel (eliminate one LNS8 SUB) — ~0 FFs
- LNS10 phi_rom for weight kernel only (64 entries vs 16) — ~50 LUTs, ~20 FFs
- Wider Gaussian ROM for RNG (already 256 entries, could refine)

**For larger FPGA or custom silicon:**
- LNS12: 4096-entry phi_rom, eliminates quantization gap. ~few KB SRAM.
- 256-512 particles: linear compute/memory scaling
- Multiple PF instances for different subsystems
- Mixed-precision pipeline: FP for adds, LNS for multiplies (current hybrid, extended)

## Sensor Integration Notes

All platforms need SPI/I2C sensor interfaces. The MCU-less concept requires
the FPGA to directly manage sensor communication. Options:
- Hardcoded SPI master for specific sensors (minimal logic)
- Small state machine for I2C (more complex, more sensors available)
- Soft RISC-V for flexibility (but consumes significant UP5K resources)

For the maritime platform, GPS parsing (NMEA over UART) is needed but
only when surfaced. Could duty-cycle the GPS module.

## Scaling: Multi-ALU Parallelism

The LNS8 ALU is ~500 LUTs (MUL, DIV, ADD, SUB, EXP, LN — complete).
Adding a second ALU for 2× particle throughput costs +500 LUTs (~20% area increase).
Equivalent fixed-point pipeline (16×16 MUL + DIV + accumulators) would be 700-1000 LUTs.

For high-dimensional state (12-18D) with 256 particles at 120 Hz:
- Single ALU @ 30 MHz: ~130-180 Hz (marginal)
- Dual ALU @ 30 MHz: ~260-360 Hz (comfortable headroom)
- RISC-V MCU no FPU @ 48 MHz: ~30-60 Hz (insufficient)

## ASIC Production Economics (130nm, ~10K gates, ~0.2mm² die)

| Volume | Per-unit cost | Dominant cost |
|--------|--------------|---------------|
| 1K | $150-500 | NRE (mask set, test development) |
| 10K | $16-50 | NRE (~80%) |
| 100K | $1.50-5.00 | Packaging + test |
| 1M | $0.30-0.80 | Packaging |

Silicon cost: ~$0.03/die (100K+ dies per 200mm wafer). Negligible.
Mask set: $150-250K (breakeven vs shuttle at ~5K-10K units).
With $500K-1M funding: 50K-500K packaged chips feasible.

Prototype path: TinyTapeout ($2-5K) → chipIgnite ($10-15K) → production masks.

## Power Budget Estimates

| Platform | Update Rate | Cycles/Step | Duty Cycle | Avg Power (30MHz) |
|----------|------------|-------------|------------|-------------------|
| Maritime | 1 Hz | 55K | 0.18% | ~15 µW |
| River | 10 Hz | 55K | 1.8% | ~150 µW |
| Aerial | 50 Hz | 55K | 9.2% | ~750 µW |

iCE40 UP5K static power: ~75 µW. So total chip power for maritime: ~90 µW.
Compatible with small solar cell (1 cm² gives ~1 mW outdoors).

### Revised estimates with realistic state dimensions

| Platform | State Dims | Sensors | Cycles/Step | At Update Rate | Chip Power |
|----------|-----------|---------|-------------|----------------|------------|
| Maritime (17D) | 17 | 7-9 | ~33K | 1 Hz | ~85 µW |
| River (20D) | 20 | 10-12 | ~40K | 10 Hz | ~175 µW |
| Aerial (15D) | 15 | 8-10 | ~31K | 50 Hz | ~475 µW |

Higher dimensions actually reduce cycles/step vs the 6D baseline (55K) because
the 6D estimate included overhead that doesn't scale linearly. The binding
constraint shifts to **BRAM** at 20D: 20 × 128 × 2 = 5.0 KB per bank, needing
10 KB double-buffered vs 7.5 KB available on UP5K.

## Detailed Platform Designs

See concept sketches:
- [Maritime drifter mesh](maritime_buoy_design.md) — passive acoustic mesh for dark vessel detection. 15-25D state, fleet-relative nav via LoRa, near-passive station-keeping.
- [River drone design](river_drone_design.md) — current-riding, 20D state, no GPS, evolutionary hull with co-evolved transformation repertoire
- [Aerial drone design](aerial_drone_design.md) — thermal-riding samara, 15D state, gram-scale

### Where FPGA PF Compute Actually Matters

On platforms with active actuation (motors, sail servos, ballast pumps), FPGA
vs MCU compute difference is ~1 mW — lost in the noise vs 100-5000 mW total
budgets. **The FPGA approach only delivers real value when the platform is
close to passive** (<10 mW total), where 1 mW = 10-40% of the budget.

This has narrowed our maritime focus: from "sailboat that can submerge" to
"matchbook-sized passive acoustic drifter in a mesh." The latter plays to
our strengths (sub-mW PF compute, high-D state for fleet coordination),
avoids SubSeaSail's patent space (no sail, no dive-as-recovery), and addresses
a genuine market gap (small dark vessel detection that satellites can't cover).

Platform research survey: [platform_survey.md](platform_survey.md)

## Scenario Analysis (Python-only, no RTL changes)

Scenario generators validate LNS8 accuracy at realistic dimensions before
committing to RTL modifications:
- `vectors/gen_maritime_scenario.py` — 15D, intermittent GPS, IMU bias drift
- `vectors/gen_river_scenario.py` — 20D, sonar with beam patterns, spatially-varying current

Key metrics: RMSE per dimension (absolute, physical units), effective particle
count trajectory (weight diversity), LNS8 vs float64 gap relative to sensor
noise floor.
