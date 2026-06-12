# River Drone — Passive Current-Riding Autonomous Platform

## Concept

A semi-active smart drifter for river navigation and monitoring. Rides the
current like a whitewater kayak — no propulsion, but hull shape + minimal
actuation exploit hydrodynamic forces for steering, depth control, and obstacle
avoidance.

The key insight from whitewater kayaking: **the hull is the primary control
surface.** A playboat hull is shaped so that small weight shifts (lean, edge)
dramatically change force profiles — ferrying across current, surfing waves,
eddying out behind obstacles. The kayaker doesn't fight the current; they read
the water and redirect forces through hull geometry.

We're solving the same problem without the constraints of a human cockpit, the
need to breathe, or the need to stay upright. This opens the design space to
shapes that are fully submersible, freely rolling, any aspect ratio, and much
smaller.

## Design Philosophy

**Semi-active drifter**: Hybrid between a passive Lagrangian drifter (no control)
and a propelled ASV (fights the current). Uses the current for propulsion and
exploits the river's velocity field for maneuvering. The only energy expenditure
is for small actuations that redirect existing hydrodynamic forces.

**Hull shape IS the steering mechanism.** Find a shape where small deformations
or orientation changes produce large changes in the lateral force profile.
Evolutionary optimization over parameterized shapes to maximize "steerability
per watt" — cross-stream force authority per unit of actuation energy.

**No prior art.** Research confirms: every autonomous river platform uses active
propulsion. Lagrangian drifters exist but have zero steering. Underwater gliders
(ROUGHIE) use ballast/mass-shifting but in still-water lakes/ocean. A current-
riding drone with hull-based steering in a river is genuinely novel.

## The Kayak Analogy (and Why We Can Do Better)

A whitewater kayak demonstrates that hull shape + small inputs = large force
changes in flowing water:

| Kayak Technique | Hull Mechanism | Our Analog |
|-----------------|---------------|------------|
| Edging (lean to one side) | Asymmetric waterline → lateral force | Roll via ballast shift |
| Ferry angle | Hull at angle to current → cross-stream drift | Yaw via rudder/fin |
| Eddy turn | Enter slow water behind obstacle, hull catches shear zone | Depth change exploits velocity gradient |
| Boof (launch off feature) | Hull shape + speed → clear obstacle | Pre-emptive lateral displacement |
| Bracing (paddle brace) | Stabilize against capsize | Not needed — can roll freely |

**Constraints we remove:**
- No cockpit/seat → any cross-section shape
- No breathing → fully submersible, any depth
- No orientation requirement → can roll, tumble, adopt any attitude
- No minimum size → decimeters, not meters
- No paddle → all control through hull shape + internal actuation
- No spray skirt seal → simplified watertight design (solid body)

## Form Factor Options

### Option A: Deformable Body (most novel)

A hull with sections that can change shape:
- Inflatable bladder sections that alter cross-section profile
- Expand port side → asymmetric drag → lateral force
- Expand bottom → increased drag → slow relative to current → fin authority increases
- Shape parameterized for evolutionary optimization

Challenges: sealing, fatigue, complexity. But maximum control authority per watt
since the entire hull is a control surface.

### Option B: Rigid Body + Internal Mass Shifting (ROUGHIE-proven)

- Fixed hydrodynamic hull shape
- Internal sliding mass (battery/ballast) for pitch and roll control
- Ballast pump for buoyancy (depth control)
- Small external rudder/fin for yaw
- ROUGHIE demonstrated 3m turning radius with this approach

Challenges: limited cross-stream authority from mass shifting alone in strong
current. Rudder fin needs relative flow to work.

### Option C: Rigid Body + Deployable Control Surfaces (kayak-inspired)

- Hull optimized for low differential drag (moves with current)
- Retractable/rotatable fins or skegs at multiple points
- Deploy a fin to one side → asymmetric drag → lateral force
- Deploy fins at different depths to exploit velocity gradient
- Trim tabs or flaps for fine control
- Internal ballast for depth control

Challenges: mechanical complexity of moving parts in debris-laden water.
But proven in marine engineering (submarine control surfaces).

### Option D: Hybrid — Rigid Hull + Minimal Deformation

- Hull shape with built-in asymmetric features (like a kayak's rocker profile)
- Single internal ballast shift (fore/aft and port/starboard)
- Single rudder (yaw)
- Ballast pump (depth)
- **Three actuators total: ballast pump, mass shifter, rudder servo**

This is probably the right starting point. Minimum actuator count, maximum
leverage from hull shape. Evolve the hull shape to maximize what those three
actuators can achieve.

**Recommended starting point: Option D.**

## Hull Shape Design

### Parameterization for Evolutionary Optimization

The search space is not just "find the best static hull shape" but **"find the
best hull shape AND the best transformation repertoire."** No constraint that the
hull must remain rigid — sections can rotate, extend, deform, hinge. The
evolutionary search co-optimizes morphology and actuation.

**Base hull** defined by cross-section profiles at N stations along the length:

```
Parameters per station (5):
  - width (half-beam)
  - height (half-depth)
  - top_curvature (convex/concave)
  - bottom_curvature
  - asymmetry (port/starboard bias)

Global parameters (6):
  - total_length
  - nose_shape (blunt ↔ sharp)
  - tail_shape
  - rocker (fore/aft curvature — how much the keel curves up at ends)
  - volume_distribution (center of buoyancy relative to center of mass)
  - max_diameter

With N=8 stations: 8×5 + 6 = 46 base parameters
```

**Transformation modes** (co-evolved with base shape):
```
Per station, optional transformation genes:
  - rotation_range: ±degrees this section can twist around long axis
  - extension_range: mm this section can telescope in/out
  - inflate_range: mm this section can expand radially (bladder)
  - hinge: boolean, can this station fold relative to neighbors?

Global transformation genes:
  - mass_shift_axes: which axes the sliding mass can move on (1-3)
  - mass_shift_range: mm of travel per axis
  - fin_stations: which stations have deployable fins
  - fin_size: area of each deployable fin

Transformation cost gene:
  - Each active transformation has an energy cost (joules per actuation)
  - Mechanical complexity cost (number of actuators, sealing points)
```

The fitness function evaluates the **reachable set of force profiles** — given
this base shape and these N transformation modes, how wide is the force envelope?
A hull with 3 simple transformations covering a wide force envelope beats one
with 10 overlapping transformations. This is co-optimization of morphology and
actuation, grounded in a concrete physical problem.

### Optimization Objectives (Multi-Objective, NSGA-II)

1. **Minimize differential velocity**: Hull should travel at nearly the same speed
   as surrounding water. Low form drag in the flow direction.
2. **Maximize cross-stream force per unit actuation**: When rudder deflects or
   mass shifts, how much lateral force results? This is "steerability."
3. **Maximize depth-change responsiveness**: How quickly does hull change depth
   when ballast shifts? Measured as time to transit 1m vertically.
4. **Stability in turbulence**: Resistance to uncontrolled rotation/tumble from
   eddies and boils (scale with depth h: eddies ~2h lateral, 4-7h downstream).
5. **Obstacle shedding**: Tendency to deflect off rocks/logs rather than getting
   stuck. Related to nose shape and cross-section.

**The unique objective (#2) is what makes this different from AUV hull
optimization.** Standard AUV work minimizes drag; we want to minimize drag
in the flow direction while maximizing controllable drag asymmetry.

### Flow Simulation

CFD at relevant Reynolds numbers for river current:
- Hull length ~0.3-0.5m, current 0.5-2.0 m/s → Re ~150,000-1,000,000
- Turbulent flow, need RANS or LES
- Evaluate at multiple current speeds and yaw angles
- Evaluate with rudder at 0°, ±15°, ±30°
- Evaluate with mass shifted port/starboard
- Evaluate at multiple depths in velocity profile

**Simplified 2D approach for initial sweep:**
- Cross-section drag coefficients from empirical data
- Panel method for 3D at low cost
- Full CFD (OpenFOAM) for top candidates only
- Kriging surrogate to accelerate GA search (proven approach, 7-12% improvements
  reported in AUV hull literature)

### Hull Shape Intuition

What might the evolved shape look like?

- **High rocker** (like a playboat): curved keel makes it easy to spin/pivot.
  Reduces directional stability but increases maneuverability.
- **Flat bottom with sharp chines**: like a kayak, edging produces strong lateral
  force because the chine bites into the water.
- **Asymmetric cross-section**: allows roll to produce predictable lateral force
  in one direction.
- **Blunt nose**: deflects off obstacles rather than catching. Also creates a bow
  wave that pushes debris aside.
- **Short and wide** (low aspect ratio): more maneuverable, less directional stability.
  Opposite of a torpedo (long/thin for straight-line efficiency).

A whitewater playboat is ~1.8m long, 0.65m wide — aspect ratio ~2.8:1.
Scaled down to 0.4m: about 0.4m × 0.15m — roughly the size of a large water bottle.

## Maneuvering Strategy

### Cross-Stream Movement (Ferry)

Like a kayak ferry: present the hull at an angle to the current. The current
pushes against the angled hull, producing a cross-stream force component.

```
Cross-stream force ≈ 0.5 × ρ × Cd × A_projected × v² × sin(θ)

Where:
  θ = hull angle to current (yaw)
  A_projected = hull cross-section projected at angle θ
  v = current velocity relative to hull
  Cd = drag coefficient at angle θ
```

For this to work, the hull needs relative velocity to the current. Three ways:

1. **Ride at a different depth** where velocity differs (surface vs mid-depth).
   The velocity gradient creates relative flow across the hull.
2. **Deploy control surfaces** that create asymmetric drag, slowing the hull
   relative to the current. The slow-down provides relative flow for the rudder.
3. **Exploit eddies/shear zones** where adjacent water moves at different speeds.

Option 1 is the most energy-efficient (just shift ballast).

### Depth Control (Dive/Surface)

Ballast pump — same principle as Argo floats, scaled down:
- Small volume change (~20-50 mL) shifts buoyancy by ~20-50 grams
- At hull mass ~1-3 kg, this is ~1-3% buoyancy change
- Sufficient for 0.1-0.5 m/s vertical velocity in still water
- In current: depth change also changes horizontal velocity (velocity profile)

**Depth as a control input**: Since velocity varies with depth (surface ~1.18×
depth-averaged, near-bed much slower), changing depth changes speed relative to
surface obstacles. Diving = slowing down. Rising = speeding up. This is free
maneuvering authority.

### Obstacle Avoidance

Strategy depends on detection range and current speed:

| Current Speed | Detection Range (FLS) | Reaction Time | Strategy |
|--------------|----------------------|---------------|----------|
| 0.5 m/s | 10m | 20s | Ferry around — ample time |
| 1.0 m/s | 10m | 10s | Ferry + depth change |
| 2.0 m/s | 10m | 5s | Pre-emptive lateral position, dive if needed |
| 3.0 m/s | 10m | 3.3s | Very limited — need earlier detection or bank-following |

**Critical insight**: A passive drifter in 2+ m/s current with 10m sonar range
has <5s to react. Cross-stream displacement in that time depends entirely on
hull design + available force. This sets the minimum "steerability" requirement
for the hull optimization.

Required cross-stream velocity to clear a 1m obstacle with 5s warning:
~0.2 m/s lateral. For a 2 kg hull, that requires ~0.5-1N sustained lateral force.
Achievable from a 15° ferry angle in 2 m/s current with a hull Cd of ~0.5 and
frontal area of ~0.01 m².

### Station-Keeping (Eddy Parking)

Like a kayak eddying out: move into slow water behind an obstacle.
In the eddy, current is reversed (upstream), so the drone can hold position
with minimal energy by sitting in the recirculation zone.

This enables:
- Extended sampling at a fixed location
- Waiting for conditions to change
- Coordinating with other drones

Requires: detecting eddies (velocity change on sonar or IMU) and navigating
into them (ferry + depth change).

## Sensor Suite

### Navigation Sensors

| Sensor | Measures | Dims Observed | σ (typical) | Rate | Notes |
|--------|----------|---------------|-------------|------|-------|
| FLS (forward sonar) | Distance/bearing to obstacles ahead | - | 0.1-0.5m | 5-10 Hz | Critical for obstacle avoidance |
| Side sonar (×2) | Distance to banks L/R | cross_stream | 0.1-0.3m | 5 Hz | Bank-relative positioning |
| Down sonar | Depth to bottom | depth | 0.05-0.1m | 5 Hz | Bathymetry reference |
| Pressure | Water depth (absolute) | depth | 0.01-0.02m | 10 Hz | Very precise |
| IMU (accel ×3) | Acceleration | velocity (indirect) | 0.1-1 mg bias | 100 Hz | Dead reckoning |
| IMU (gyro ×3) | Angular rate | roll, pitch, yaw rates | 1-10 °/hr drift | 100 Hz | Orientation |
| Water speed (pitot/drag) | Speed relative to water | vx (along-stream) | 0.05-0.1 m/s | 10 Hz | Ferry angle estimation |
| Optical flow (×2, opt.) | Bottom texture motion | vx, vy (ground-relative) | varies | 20 Hz | Absolute velocity reference |

### Science Sensors (Payload)

| Sensor | Measures | Notes |
|--------|----------|-------|
| Temperature | Water temp | Thermistor, minimal power |
| Conductivity | Salinity/TDS | Indicates pollution, mixing |
| pH | Acidity | Environmental monitoring |
| Dissolved O₂ | Oxygen saturation | Ecological health indicator |
| Turbidity | Suspended sediment | Backscatter or nephelometric |

### Why No GPS

- Canopy cover in forested rivers
- Canyon walls block satellite view
- Bridge/overpass interference
- Our differentiation: the LNS8 PF navigates entirely from relative sensors.
  This is the hard problem we're solving.

## State Vector (20D)

```
Position:         cross_stream, along_stream, depth                  (3)
Velocity:         vx_water, vy_water, vz                             (3)
  (relative to water — what the pitot/drag vane measures)
Orientation:      roll, pitch, yaw                                   (3)
Angular rates:    p, q, r                                            (3)
Environment:      current_vx, current_vy                             (2)
  (local current velocity — slowly varying, inferred from IMU + water speed)
IMU bias:         gyro_bias_x, gyro_bias_y, gyro_bias_z              (3)
                  accel_bias_x, accel_bias_y, accel_bias_z           (3)
                                                                     ----
                                                                      20
```

### Why 20D

- **Position (3)**: Cross-stream position (distance from reference bank) is the
  most important navigation dimension. Along-stream is less critical (we're going
  where the current takes us) but needed for mission planning. Depth for obstacle
  clearance and velocity profile exploitation.

- **Velocity relative to water (3)**: What the hull "feels." Determines fin/rudder
  forces. Measured by pitot/drag vane (along-stream) and inferred from IMU for
  cross-stream and vertical.

- **Orientation (3)**: Roll, pitch, yaw of the hull. Critical for interpreting
  IMU readings and computing hydrodynamic forces. Yaw is the ferry angle.

- **Angular rates (3)**: Gyro-measured, needed for orientation prediction and
  stability estimation. Also useful for detecting turbulence (rapid rate changes).

- **Current velocity (2)**: The river's local flow vector. Varies spatially
  (faster center, slower banks, eddies). Estimated from the difference between
  ground-relative motion (sonar/optical flow) and water-relative motion (pitot).
  Slowly varying as the drone drifts through the field.

- **IMU bias (6)**: MEMS gyro and accelerometer biases drift over time. Must be
  estimated online — without GPS corrections, bias estimation relies on sonar
  observations of bank position (slow, indirect constraint on accumulated drift).

### Dynamics Model

**Position** (cross-stream, along-stream, depth):
```
cross_stream[t+1] = cross_stream[t] + (vy_water[t] + current_vy[t]) × dt + noise
along_stream[t+1] = along_stream[t] + (vx_water[t] + current_vx[t]) × dt + noise
depth[t+1]        = depth[t] + vz[t] × dt + buoyancy_accel × dt² + noise
```

**Velocity relative to water** (driven by hydrodynamic forces):
```
vx_water[t+1] = vx_water[t] + drag_force_x(hull_shape, v_rel, yaw) × dt / mass + noise
vy_water[t+1] = vy_water[t] + (rudder_force + hull_lateral_force(roll, yaw)) × dt / mass + noise
vz[t+1]       = vz[t] + (buoyancy - gravity) × dt / mass + noise
```

**Orientation** (Euler angle update, simplified):
```
roll[t+1]  = roll[t]  + (p[t] - gyro_bias_x[t]) × dt + noise
pitch[t+1] = pitch[t] + (q[t] - gyro_bias_y[t]) × dt + noise
yaw[t+1]   = yaw[t]   + (r[t] - gyro_bias_z[t]) × dt + noise
```

**Angular rates** (driven by hydrodynamic torques):
```
p[t+1] = p[t] + roll_torque(hull, current, mass_position) × dt / I_xx + noise
q[t+1] = q[t] + pitch_torque(hull, current, mass_position) × dt / I_yy + noise
r[t+1] = r[t] + yaw_torque(rudder, hull, current) × dt / I_zz + noise
```

**Current velocity** (slowly-varying random walk):
```
current_vx[t+1] = current_vx[t] + noise  (σ ~ 0.005 m/s per step at 10 Hz)
current_vy[t+1] = current_vy[t] + noise  (σ ~ 0.002 m/s per step at 10 Hz)
```

**IMU bias** (very slowly-varying random walk):
```
gyro_bias[t+1]  = gyro_bias[t]  + noise  (σ from gyro bias instability spec)
accel_bias[t+1] = accel_bias[t] + noise  (σ from accel bias instability spec)
```

### Sensor Observation Model

| Sensor | Observes | Model |
|--------|----------|-------|
| Side sonar L | cross_stream | z = cross_stream + N(0, σ²_sonar). Range-dependent noise. |
| Side sonar R | river_width - cross_stream | z = W - cross_stream + N(0, σ²_sonar) |
| Down sonar | bottom_depth - depth | z = D(along_stream) - depth + N(0, σ²_sonar) |
| Pressure | depth | z = depth + N(0, σ²_pressure) |
| IMU accel ×3 | dv/dt + gravity | z = a_true + bias + N(0, σ²_accel). Gravity resolved by orientation. |
| IMU gyro ×3 | angular rate | z = ω_true + bias + N(0, σ²_gyro) |
| Pitot/drag | vx_water | z = vx_water + N(0, σ²_pitot) |
| Optical flow ×2 | ground-relative vx, vy | z = v_water + current + N(0, σ²_of). Needs bottom texture. |

**Sonar beam pattern** (not just Gaussian noise):
```
σ_sonar(range) = σ_base + α × range + β × range²
```
Plus clutter probability: P(clutter) increases with range.
Side-scan sonar has an angular beam width (~10-30°) so the measurement is
averaged over the beam footprint — smooth surfaces give clean returns,
rough/irregular banks give noisy returns with multipath.

## PF Hardware Requirements

### Compute Budget (20D, 10-12 sensors, 128 particles, 10 Hz)

- Predict: 20 dims × 128 particles × ~8 cycles/dim = ~20.5K cycles
- Weight: 10-12 sensors × 128 particles × ~7 cycles = ~9.0-10.8K cycles
- Resample: ~6 × 128 = 768 cycles
- Estimate: 20 dims × 128 × ~3 cycles = ~7.7K cycles
- **Total**: ~38-40K cycles @ 30 MHz = ~1.3 ms per step
- At 10 Hz: ~13 ms per 100ms window. **Fits comfortably.**

### Memory Budget
- Particle state: 20 dims × 128 particles × 2 bytes = 5.0 KB
- Double-buffered: 10 KB. iCE40 UP5K has 7.5 KB BRAM.
- **Problem**: Need 10 KB, have 7.5 KB. Options:
  - Reduce to 96 particles (7.5 KB fits single-buffered with headroom)
  - Use external SPI SRAM (~100 KB for pennies, but slower access)
  - Single-bank with careful sequencing (predict in-place)

### Power Budget (10 Hz)

| Component | Power | Duty Cycle | Average |
|-----------|-------|------------|---------|
| PF compute | ~8 mW active | 1.3% | ~100 µW |
| iCE40 static | 75 µW | 100% | 75 µW |
| IMU (ICM-42688-P) | ~3 mW | 100% | 3 mW |
| Pressure (BMP390) | ~0.7 mW | 100% | 0.7 mW |
| Sonar (×4) | ~50 mW each | 10% | 20 mW |
| Pitot/water speed | ~5 mW | 100% | 5 mW |
| Rudder servo | ~500 mW peak | 5% | 25 mW |
| Ballast pump | ~1 W peak | 1% | 10 mW |
| **Total** | | | **~64 mW** |

With a 20 Wh battery (small 18650 cell): ~312 hours = **13 days** endurance.
At river speeds of 1 m/s, that's ~1,100 km of river — more than the length
of most rivers.

## Critical Design Question: The Control Authority Paradox

**If the hull perfectly matches the current, fins have zero relative flow → zero
control force.** This is the fundamental tension.

### Resolution Strategies

1. **Depth differential**: Position the hull at a depth where velocity differs from
   the velocity at the fin/rudder depth. If the hull body is at one depth and fins
   extend to another, the differential velocity across the fins provides force.

2. **Deliberate asymmetric drag**: Hull shape designed so that in its default
   orientation, it rides slightly slower than the current. This creates relative
   flow (~0.1-0.3 m/s) across all control surfaces. Cost: slightly more drag,
   slightly more drift from straight downstream path.

3. **Drogue deployment**: A small drogue or drag plate that can be deployed to
   slow the hull relative to current. Provides relative flow for control surfaces.
   Retract when control isn't needed.

4. **Velocity gradient exploitation**: In the river's boundary layer near the bed
   or banks, velocity changes rapidly with position. By positioning in a high-shear
   zone, parts of the hull experience different velocities → differential forces.

**Strategy 2 is probably the right default**: design the hull to ride ~10% slower
than the current. 10% of 1 m/s = 0.1 m/s relative flow. For a rudder with 10 cm²
area, this gives ~0.05 N lateral force — modest but usable for gradual course
corrections. In faster current (2 m/s), the 10% differential gives 0.2 m/s and
~0.2 N — much more authority.

## Evolutionary Hull Design — Research Angle

This is the most interesting research contribution beyond the PF hardware.

### Simulation Framework

```
for each generation:
    for each hull candidate:
        1. Parameterize hull (46 parameters → 3D mesh)
        2. Simulate in CFD at multiple conditions:
           a. Straight flow, no actuation → measure form drag, drift velocity
           b. Straight flow, rudder at ±15° → measure lateral force, yaw moment
           c. Straight flow, mass shifted port/starboard → measure roll moment, lateral force
           d. Shear flow (velocity gradient) → measure differential forces
           e. Turbulent flow (vortex encounter) → measure stability margin
        3. Compute fitness vector:
           [drag_match, lateral_force_per_rudder, roll_authority,
            depth_response, turbulence_stability, obstacle_deflection]
    
    NSGA-II selection on Pareto front
    Crossover + mutation on hull parameters
```

### Open Questions

- **What does the Pareto-optimal hull look like?** Intuition says short, wide,
  high-rocker (like a playboat) but evolution might find something unexpected.
- **Is there a single design that works across a range of river conditions?**
  (slow/deep vs fast/shallow vs turbulent)
- **How much does the velocity profile assumption matter?** Log-law vs measured
  profiles from USGS data.
- **Can the hull itself detect current conditions** through its response? (The PF
  estimates current velocity — is the hull's motion informative enough?)

### Simplified 2D Simulation for Initial Sweep

Before committing to full 3D CFD (expensive), run a 2D cross-section optimization:
- Parameterize the cross-section shape (ellipse + perturbations)
- Compute drag coefficients at various angles using empirical data or panel method
- Evaluate cross-stream force vs roll angle
- Fast enough to run thousands of generations in minutes

This gives a starting cross-section shape for the full 3D optimization.

## Operational Concept

### Mission Profile

1. **Deploy upstream** (hand-launch from bridge or bank)
2. **Drift with current**, collecting sensor data (water quality, bathymetry)
3. **Navigate using PF**: maintain position relative to banks, avoid obstacles
4. **Exploit eddies** for extended sampling at points of interest
5. **Transmit data** via LoRa/cellular when in range (bridge overpasses, bank stations)
6. **Recover downstream** at collection point (net, eddy, or manual retrieval)

### Retrieval Strategy

Rivers are finite — drifters reach the end. Options:
- Designated collection point with net/boom
- Drone parks in a known eddy near a recovery site
- Multiple drones leapfrogged: recover spent drone, redeploy upstream
- Disposable at sufficiently low unit cost

### Multi-Drone Coordination

Multiple drones deployed simultaneously can:
- Map the full cross-section velocity profile (different drones at different depths)
- Provide spatial coverage of water quality
- Share current field estimates to improve individual PF performance
- Coordinate to maintain spacing (mesh network via acoustic modem or surfacing for RF)

## Comparison to Existing Platforms

| Feature | Ours | Lagrangian Drifter | Platypus (CMU) | ROUGHIE (Purdue) |
|---------|------|-------------------|----------------|------------------|
| Propulsion | Current (passive) | Current (passive) | Electric fan/jet | Ballast (buoyancy) |
| Steering | Hull + fin + ballast | None | Differential thrust | Mass-shifting |
| Environment | River current | River/ocean | River (any) | Still water (lakes) |
| Endurance | Days-weeks | Hours-days | ~2 hours | Hours |
| Navigation | PF (LNS8, 20D) | GPS only | GPS | Waypoint |
| GPS required | No | Yes | Yes | Yes |
| Obstacle avoidance | Sonar-based | None | Limited | None |
| Cost target | $200-500 | $150-5K | $1-3K | Research platform |
| Novel element | Hull-as-control-surface, no-GPS PF nav | Simplicity | Multi-robot coord | Mass-shifting |
| Update rate | 10 Hz | 1 Hz | 1 Hz | 1 Hz |

## Next Steps

1. **Scenario generator** (gen_river_scenario.py): 20D state, 10-12 sensors,
   spatially-varying current field, sonar with realistic beam patterns. Verify
   LNS8 accuracy and weight diversity survival at 20D.

2. **2D cross-section optimizer**: Evolutionary sweep of hull cross-sections for
   maximum roll-induced lateral force. Quick feasibility check.

3. **Hull shape parameterization**: Define the 46-parameter space, bounds, and
   constraints (volume, mass, moment of inertia).

4. **USGS data integration**: Pull real current profiles from nearby gauge stations
   to calibrate the scenario generator and hull optimizer.

5. **Control authority analysis**: Compute required lateral force vs current speed
   for obstacle avoidance scenarios. Sets minimum steerability threshold for hull
   optimization.

## References

- ROUGHIE underwater glider: https://newatlas.com/robotics/roughie-auv-underwater-glider/
- UCSD river drifter: https://gdp.ucsd.edu/ldl/river/
- Open-source river drifter: https://pmc.ncbi.nlm.nih.gov/articles/PMC9780804/
- River velocity profiles: https://www.mdpi.com/2073-4441/15/21/3711
- GA+CFD hull optimization: https://journals.sagepub.com/doi/10.1177/1475090217714649
- GitHub GA-CFD-MO: https://github.com/jlobatop/GA-CFD-MO
- Forward-looking sonar: https://www.unmannedsystemstechnology.com/expo/forward-looking-sonar/
- USGS water data API: https://waterservices.usgs.gov/
- River obstacle avoidance (ROSEBUD): https://pmc.ncbi.nlm.nih.gov/articles/PMC9269472/
- ADCP uncertainty: https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2019WR025296
