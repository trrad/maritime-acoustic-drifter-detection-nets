# Aerial Drone — Ultralight Near-Passive Atmospheric Sensor

## Concept

Extremely lightweight (grams) near-passive atmospheric sensor/observer that
exploits thermals for persistent flight with minimal control authority. 
Bio-inspired (samara/seed, flat glider) with the PCB-as-airframe approach
proven by CICADA (NRL) and UW microflier.

The key insight: **thermal soaring is a solved sensing problem** (ArduSoar,
ALOFT), and the sensors required (BMP390 baro + IMU) weigh under 0.5g. The
open question is whether a samara-like autorotating body can modulate its
descent rate enough to gain altitude in a 1-5 m/s thermal updraft.

## Design Space

Two distinct architectures, depending on whether we optimize for persistence
(thermal riding) or deployment (scatter many):

### Architecture A: Thermal-Riding Samara (persistent)

- **Form factor**: Single-wing autorotator, ~5-10 cm chord, 5-20g total mass.
  Carbon fiber or rigid PCB wing with mass at hub.
- **Propulsion**: None. Thermals provide altitude. Autorotation provides stable
  descent between thermals.
- **Control**: Wing pitch modulation (single servo or SMA actuator) to vary
  descent rate. In a thermal: reduce descent rate (flatten pitch) to gain
  altitude. Between thermals: increase descent rate (steepen pitch) to glide
  toward next thermal.
- **Key physics**: At Re 900-3,500, stable leading edge vortex (LEV) on the
  autorotating wing provides lift enhancement. Same mechanism as insect flight.
  Natural samaras descend at ~0.5-1.0 m/s. If we can reduce effective descent
  to <1 m/s, a 2-3 m/s thermal gives net climb.
- **Endurance**: Hours (thermals available during daytime). Limited by battery
  for sensors/comms, not flight energy.
- **Prior art**: Lockheed Samarai (30 cm, <0.5 lb, 2 moving parts, VTOL).
  UMD robotic samaras (7.5 cm - 0.5m). SUTD F-SAM (69g, passively stable).
  But none of these ride thermals — all use powered rotation.

### Architecture B: Dispersible Sensor (scatter many)

- **Form factor**: PCB-as-airframe, 0.1-5g total mass. Inspired by UW microflier
  (30 mg) and NRL CICADA ($250, flying PCB, 18 in a 6-inch cube).
- **Propulsion**: None. One-way descent from deployment altitude.
- **Control**: None or minimal (drag modulation via deployable flap).
- **Sensors**: Temperature, humidity, pressure. Backscatter radio (UW approach)
  or LoRa for data.
- **Power**: Solar + supercap (no battery). Active only in sunlight.
- **Endurance**: Single descent (~minutes from deployment altitude), then
  ground-based sensing indefinitely (solar powered).
- **Prior art**: UW microflier (30 mg, backscatter, 95% upright landing).
  Northwestern 3D microfliers (grain-of-sand scale). CICADA (GPS-guided, $250).
- **Value**: Cheap enough to scatter hundreds. Atmospheric profiling during
  descent, then ground sensor network.

**Architecture A is more interesting for the PF research** — the thermal detection
and exploitation loop maps directly to our particle filter's state estimation
capability. Architecture B is simpler but doesn't exercise the PF much.

**Recommended focus: Architecture A (thermal-riding samara).**

## Thermal-Riding Samara — Detailed Design

### The Core Question

A natural maple samara descends at ~0.5-1.0 m/s in autorotation. Thermals
provide 1-5 m/s updraft (typically 2-3 m/s on a reasonable day). So a samara
dropped into a thermal should gain altitude — the updraft exceeds the descent
rate.

The challenges:
1. **Finding thermals**: Need to detect updraft and navigate toward thermal center.
   ArduSoar's EKF approach solves this with just baro + IMU + position.
2. **Staying in thermals**: Need to circle within the thermal column (100-500m
   diameter). Requires lateral control authority.
3. **Modulating descent rate**: Need to reduce descent rate in thermals (maximize
   lift) and increase it between thermals (maximize glide distance).
4. **Transitioning between thermals**: Thermals are spaced 1-3 km apart. Need
   enough altitude (from thermal climb) to glide to the next one.

### Thermal Detection (solved problem)

**Total energy variometer**: d/dt(altitude + v²/2g). Compensates for speed
changes — if you trade altitude for speed, total energy is constant, variometer
reads zero. Positive reading = air itself is rising.

At gram scale:
- BMP390 barometer: 0.1m noise RMS, 2×2mm LGA, milligrams. Detects ~1 m/s
  updraft within ~1 second at a few Hz sample rate.
- ICM-42688-P IMU: 2.8 mdps/√Hz gyro noise, 2.5×3mm, milligrams. Provides
  acceleration for kinetic energy term.
- Together: <0.5g on custom PCB. Full total energy variometer.

**Thermal centering (ArduSoar algorithm)**:
1. Detect climb rate above threshold → begin circling
2. EKF estimates thermal center from (position, climb_rate) samples around circle
3. Shift circle center toward estimated thermal center
4. Converge on thermal core for maximum climb

For a samara, "circling" is natural — autorotation IS circular motion. The
challenge is controlling the circle radius and center position.

### Control Authority

A samara has limited control degrees of freedom. Options:

**Wing pitch modulation (primary)**:
- Steepening pitch → faster descent, faster rotation, more centrifugal force
- Flattening pitch → slower descent, slower rotation, less centrifugal force
- Single actuator: SMA (shape-memory alloy) wire or sub-gram servo
- SMA: ~0.1g, milliwatts, slow response (~1s). Adequate for thermal timescales.
- Sub-gram servo: ~0.5-1g, more responsive, higher power.

**Mass shifting (secondary)**:
- Sliding a small mass (battery) along the wing span
- Shifts center of rotation → changes descent trajectory
- Provides lateral control (can bias drift direction)
- Proven in UMD robotic samaras (they control turn radius via wing pitch
  and mass distribution)

**Asymmetric drag (alternative)**:
- Small deployable flap on one side of the wing
- Changes drag asymmetry → changes rotation center → lateral motion
- Very simple mechanically

### Glide Performance

Between thermals, the samara needs to glide laterally (not just descend).
A pure autorotator descends nearly vertically. For lateral glide:

- Tilt the rotation axis (via mass shift or asymmetric pitch) to create a
  horizontal velocity component
- Trade altitude for distance at some glide ratio
- Natural samaras have very poor glide ratio (~0.3-0.5). An engineered wing
  with optimized airfoil could achieve ~1-2.
- At descent rate 0.7 m/s and glide ratio 1.5: 1.05 m/s horizontal.
  From 500m altitude (thermal climb): ~700m horizontal range.
  Thermals spaced 1-3 km → **marginal**. May need better glide ratio or
  higher thermal climb.

This is the main technical risk. If glide ratio between thermals is too poor,
the platform can only ride a single thermal before landing.

### State Vector (15D)

```
Position:      x_offset, y_offset, altitude                          (3)
Velocity:      vx, vy, vz                                           (3)
Orientation:   roll, pitch, yaw                                      (3)
IMU bias:      gyro_bias_x, gyro_bias_y, gyro_bias_z                (3)
               accel_bias_x, accel_bias_y, accel_bias_z             (3)
                                                                     ----
                                                                      15
```

Note: no environment states (wind) in the base vector. Wind could be added
(+2-3D) but at this scale, the platform moves with the air mass, so wind
is less observable and less useful for prediction than for the maritime case.
Could add thermal_strength and thermal_radius as estimated environment states
if the ArduSoar EKF approach is integrated into the PF.

### Dynamics Model

**Position** (3D):
```
x[t+1] = x[t] + vx[t] × dt + noise
y[t+1] = y[t] + vy[t] × dt + noise
alt[t+1] = alt[t] + vz[t] × dt + noise
```

**Velocity** (3D):
```
vx[t+1] = vx[t] + (drag_x + wind_x) × dt / mass + noise
vy[t+1] = vy[t] + (drag_y + wind_y) × dt / mass + noise
vz[t+1] = vz[t] + (lift - gravity + updraft) × dt / mass + noise
```
Lift and drag depend on rotation rate, wing pitch, and airspeed.
Updraft is the thermal/wind vertical component (environment, not directly
measured).

**Orientation** (3D):
```
roll[t+1]  = roll[t]  + (p - gyro_bias_x) × dt + noise
pitch[t+1] = pitch[t] + (q - gyro_bias_y) × dt + noise
yaw[t+1]   = yaw[t]   + (r - gyro_bias_z) × dt + noise
```
For a samara, yaw rate ≈ rotation rate (~10-30 Hz for small samaras).
Roll and pitch are relatively constant during stable autorotation.

**IMU bias** (6D): Slowly-varying random walk (same as other platforms).

### Sensor Suite

| Sensor | Measures | σ (typical) | Rate | Weight | Notes |
|--------|----------|-------------|------|--------|-------|
| BMP390 | Altitude (baro) | 0.25m abs, 0.1m rel | 50 Hz | ~mg | Total energy variometer |
| IMU (accel ×3) | Acceleration | 0.1 mg bias | 100 Hz | ~mg | ICM-42688-P |
| IMU (gyro ×3) | Angular rate | 2.8 mdps/√Hz | 100 Hz | ~mg | Rotation rate |
| Optical flow (×2, opt.) | Ground-relative motion | varies | 20 Hz | ~1g each | PMW3901. Optional — adds weight. |
| Ultrasonic (×2-4, opt.) | Range to obstacles | 1-5 cm | 10 Hz | ~0.5g each | Only if operating near surfaces |

**Minimum sensor set**: BMP390 + ICM-42688-P = 8 measurements (3 accel, 3 gyro,
1 pressure, 1 temperature), <0.5g. This is enough for thermal detection, altitude
hold, and orientation estimation.

**With optical flow**: Add 2 sensors (~2g), gives ground-relative velocity for
position estimation. Worth the weight if total mass budget is >10g.

### PF Hardware Requirements

**Compute** (15D, 8-10 sensors, 128 particles, 50 Hz):
- Predict: 15 × 128 × 8 = 15.4K cycles
- Weight: 8-10 × 128 × 7 = 7.2-9.0K cycles
- Resample: 768 cycles
- Estimate: 15 × 128 × 3 = 5.8K cycles
- **Total**: ~29-31K cycles @ 30 MHz = ~1.0 ms per step
- At 50 Hz: 50 ms per second. **Fits easily.**

**Memory** (15D):
- Particle state: 15 × 128 × 2 = 3.75 KB. Comfortable in UP5K BRAM.

**Power** (50 Hz):
- PF compute: 8 mW × 5% duty = 400 µW
- iCE40 static: 75 µW
- BMP390: ~0.7 mW
- ICM-42688-P: ~3 mW (can duty-cycle)
- SMA actuator: ~10 mW (intermittent)
- Radio (LoRa burst): ~100 mW × 0.1% = 100 µW
- **Total: ~5-15 mW**
- With 0.5g battery (~2 mWh) + 1 cm² GaAs solar (~30 mW in sun):
  Sustained operation in daylight, ~10-30 min buffer for cloud gaps.

### Mass Budget

| Component | Weight |
|-----------|--------|
| Wing (rigid PCB or carbon fiber) | 2-5g |
| BMP390 + ICM-42688-P (on wing PCB) | <0.1g |
| iCE40 UP5K (QFN package) | ~0.2g |
| SMA actuator (wing pitch) | ~0.1g |
| Solar cell (1 cm² GaAs) | ~0.1g |
| Supercap (10 mF) | ~0.3g |
| LoRa radio (SX1276 or similar) | ~0.5g |
| Antenna (PCB trace) | 0g (part of wing) |
| Hub/ballast mass | 1-3g |
| **Total** | **5-10g** |

This is heavier than UW microflier (30 mg) but lighter than DelFly Explorer
(20g). In the same range as CoulombFly (4.2g) and SUTD F-SAM (69g is too heavy —
we should target the lighter end).

## Key Technical Risks

1. **Glide ratio between thermals**: If < ~1.5, can only ride a single thermal.
   Mitigation: optimize wing airfoil for autorotation + glide, or accept
   single-thermal operation as a useful mode (deploy into a thermal, ride it,
   collect data during descent).

2. **Lateral control during autorotation**: Can a samara steer laterally well
   enough to stay centered in a thermal? UMD work shows mass-shifting and
   pitch control give some authority, but quantitative data on circling radius
   control is limited.

3. **Wind sensitivity**: At 5-10g, wind gusts comparable to terminal velocity
   (~1 m/s) cause large disturbances. Autorotation provides some stability
   (gyroscopic) but may not be enough in turbulent conditions.

4. **IMU in a spinning body**: Gyro and accelerometer readings from a body
   rotating at 10-30 Hz need careful handling. The PF must model the rotation
   and extract useful signals from periodic measurements. Centripetal
   acceleration dominates the accelerometer signal — gravity and linear
   acceleration are small perturbations on top of a large periodic signal.

5. **Thermal availability**: Thermals require solar heating of the ground →
   daytime, sunny, over land. Over water, at night, or in overcast: no thermals.
   Platform either lands or slowly descends. Operational window is limited.

## Comparison to Existing Platforms

| Feature | Ours | CICADA (NRL) | UW Microflier | ALOFT (NRL) | DelFly Explorer |
|---------|------|-------------|---------------|-------------|-----------------|
| Weight | 5-10g | ~25g | 30 mg | 5 kg | 20g |
| Propulsion | Thermal + autorotation | Glide only | Passive descent | Thermal soaring | Flapping wings |
| Control | Wing pitch, mass shift | GPS-guided glide | None | Full aero surfaces | Optical flow |
| Endurance | Hours (thermal) | Minutes (descent) | Indefinite (ground) | 5.3 hours | 9 minutes |
| Navigation | PF (LNS8, 15D) | GPS | None | GPS+baro | Optical flow |
| GPS required | No | Yes | No | Yes | No |
| Cost target | $10-50 | $250 | <$1 | ~$1K | Research |
| Novel element | Thermal-riding samara with FPGA PF | Flying PCB | Backscatter radio | Beat human pilots | Lightest autonomous MAV |

## Open Research Questions

1. **Samara + thermal = persistent flight?** Does the physics work? Need
   simulation: autorotation descent rate vs thermal updraft, modulated by
   wing pitch. This is answerable with a simple ODE model before building
   anything.

2. **Optimal wing shape for autorotation + glide**: Natural samaras are
   optimized for seed dispersal (slow descent, wide scatter). We want
   controllable descent + lateral glide. Different objective → different shape.
   Candidate for evolutionary optimization (like the river hull).

3. **PF in a spinning reference frame**: The IMU sees large periodic signals
   (centripetal acceleration, rotation rate). Can the PF extract useful
   state information from this? Or does it need to operate on rotation-averaged
   data (one estimate per revolution)?

4. **Multi-samara coordination**: If several samaras share a thermal, can they
   coordinate (acoustic? optical? RF?) to map the thermal structure and optimize
   individual trajectories? Distributed PF over a samara swarm.

## Next Steps

1. **Scenario generator** (not yet planned — lower priority than maritime/river):
   15D aerial scenario with periodic IMU signals from rotation, thermal model,
   barometric altitude.
2. **Autorotation + thermal ODE model**: Simple Python simulation of samara
   descent in updraft field. Vary wing pitch, compute steady-state climb/descent.
   Answers the core feasibility question.
3. **Wing shape survey**: Literature on engineered samara wings, optimized
   airfoils at Re 1000-5000.

## References

- ALOFT autonomous soaring: https://apps.dtic.mil/sti/pdfs/ADA614555.pdf
- ArduSoar: https://arxiv.org/pdf/1802.08215, https://ardupilot.org/plane/docs/soaring.html
- Microsoft Frigatebird: https://github.com/microsoft/Frigatebird
- CICADA: https://spectrum.ieee.org/naval-research-lab-tests-swarm-of-stackable-cicada-microdrones
- UW microflier: https://www.nature.com/articles/s41586-021-04363-9
- Lockheed Samarai: https://newatlas.com/lockheed-martin-samarai-flyer-monocopter/19572/
- UMD robotic samara: http://www.avl.umd.edu/projects/proj11-robotic-samara.html
- SUTD F-SAM: https://spectrum.ieee.org/foldable-monocopter-drone
- CoulombFly: https://spectrum.ieee.org/smallest-drone
- DelFly Nimble: https://mavlab.tudelft.nl/delfly-nimble/
- Samara LEV aerodynamics: https://www.mdpi.com/2226-4310/10/5/414
- AtlantikSolar design framework: https://github.com/ethz-asl/fw_conceptual_design
- BMP390: https://www.bosch-sensortec.com/en/products/environmental-sensors/pressure-sensors/bmp390
- ICM-42688-P: https://product.tdk.com/en/search/sensor/mortion-inertial/imu/info?part_no=ICM-42688-P
- PMW3901 optical flow: https://docs.px4.io/main/en/sensor/pmw3901
