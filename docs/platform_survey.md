# Existing Autonomous Platform Survey

Survey of existing platforms across maritime, river, and aerial domains to inform
our LNS8 particle filter platform designs. Focus: sensor suites, power budgets,
navigation strategies, form factors, and hard-won lessons.

Sources gathered via web research, April 2026.

## Maritime / Ocean Platforms

### Argo Floats

- **Fleet**: 4,133 active (March 2025), 249 BGC-Argo. Entire global array runs on <100W total.
- **Form factor**: ~1.3m cylinder, 25 kg. Free-drifting profiling float.
- **Propulsion**: None. Hydraulic bladder buoyancy pump (oil between internal reservoir
  and external bladder). Profiles to 2000m (Deep Argo: 6000m).
- **Cycle**: 10-day repeat — sink to parking depth, drift, profile, surface, transmit.
- **Sensors**: CTD. BGC-Argo adds: chlorophyll fluorescence, O₂, nitrate, pH, backscatter.
- **Navigation**: GPS at surface only. Purely Lagrangian submerged.
- **Comms**: Iridium SBD.
- **Power**: Lithium battery, ~5200-8600 kJ total. ~20-26 kJ per profile cycle.
  Buoyancy pump is 32% of per-profile energy. ISUS nitrate sensor ~18%.
- **Cost**: $25K-185K per unit depending on config.
- **Failure modes**: 5% pre-deployment failures (bad seals, vacuum loss). ~19% lost
  within 6 months (Canadian NOVA). Polar ice antenna damage ~20-30% mortality.
  Typical end-of-life: pump can't generate enough force to surface.
- Refs: https://argo.ucsd.edu/, https://argo.ucsd.edu/how-do-floats-work/,
  https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2019.00502/full

### Wave Glider (Liquid Robotics / Boeing)

- **Form factor**: Surface float tethered to submerged glider with articulated fins. SV3 model.
- **Propulsion**: Wave energy — articulated fins with spring mechanisms produce forward
  thrust on both upstroke and downstroke. 1.0-1.5 kt typical, **0.25-0.5 kt in calm seas**.
  Auxiliary vectored thruster for calms.
- **Sensors**: Modular payload bay. NVIDIA Tegra compute. Up to 400W burst payload power.
- **Power**: 3× 64W solar panels (225W peak, 170W nominal). 980 Wh battery.
  Continuous payload power ~10W (the real constraint, not propulsion).
- **Cost**: $250-300K per unit. $2K/day operational (vs $40K/day research vessel).
- **Track record**: >3M nautical miles logged.
- **Lessons**: Biofouling limits endurance to months. Very poor for low-frequency
  acoustics (flow noise from sub). Calm seas are fundamental and unfixable weakness.
  Tether is critical failure point.
- Core patent: US7371136B2 (wave-to-thrust mechanism).
- Refs: https://www.liquid-robotics.com/wave-glider/overview/,
  https://www.comm-tec.com/Prods/mfgs/LRI/Liquid_Robotics_Wave_Glider_Specs.pdf

### Saildrone

- **Models**: Explorer (7m, 2-3 kt), Voyager (10m, 5 kt), Surveyor (20m, 15 tons).
- **Propulsion**: Rigid wing sail. Survived Hurricane Sam (110 kt wind). Antarctic
  circumnavigation survived 50-ft waves, icebergs.
- **Sensors**: Gill 3D ultrasonic anemometer, Vaisala baro, Seabird CTD, LI-COR,
  NOAA CO₂ sensor, smart camera array, AIS. NVIDIA Jetson for edge processing
  (minimizes satellite bandwidth).
- **Power**: Wind propulsion, solar for electronics.
- **Cost**: ~$2,500/day operational. ~$4.25/nm/day. Total funding >$345M.
- **Key lesson**: Rigid wing is consensus correct choice for unmanned platforms.
  Higher lift coefficient (~1.15 vs ~0.8 for soft sails), simpler control, no
  sheets/winches. But can be "mediocre to dangerous" outside narrow optimal conditions.
- Refs: https://www.saildrone.com/platform/explorer,
  https://www.saildrone.com/news/how-saildrone-wing-was-born

### Sailbuoy (Offshore Sensing AS, Norway)

- **Form factor**: 2m, surfboard-shaped. Rigid trapezoidal sail near bow.
- **Speed**: ~1.5 kt avg, 3 kt max.
- **Battery**: 400 Wh — lasts 6 months for navigation alone (~110 mW avg autopilot draw).
- **Endurance**: 6+ months. Tested to 30 m/s winds (MK4).
- **Atlantic crossing** (SB Met, 2018): Newfoundland → Ireland, 80 days, 5,100 km
  sailed for 3,000 km straight-line (1.7× ratio). Only successful Microtransat-class crossing.
- **Fleet**: 14 units built. 25,000+ km missions in 2024.
- **Previous attempt**: SB Wave got ~1,500 km before going in circles (control failure).
- Refs: https://sailbuoy.no/, https://sailbuoy.no/news/transatlantic-crossing

### Sofar Ocean Spotter Buoy

- **Form factor**: 42 cm wide × 31 cm tall (basketball-sized). 
- **Power**: 5× 2W solar panels (10W total) + 11,200 mAh (41 Wh) battery.
  Sufficient for year-round at 55°N.
- **Sensors**: Wave spectra (GPS-based displacement at 2.5 Hz — not IMU), SST,
  baro, wind, subsurface temp, currents.
- **Network**: >1.5M real-time observations/day. All five Great Lakes. 10,000 km
  of Western Australia coastline. CRADA with NOAA NDBC.
- **Cost**: Estimated $5-15K (10× lower than traditional NDBC buoys at $50-300K).
- Refs: https://www.sofarocean.com/products/spotter,
  https://www.sofarocean.com/posts/spotter-technical-specifications

### SubSeaSail

- **Form factor**: Gen6 — 5 ft long, 5 ft high, 60 lbs. Two-person deployable.
- **Propulsion**: Rigid wing sail with **passive automatic control** — no electronics,
  lines, or pulleys for sail positioning. <1W total electrical load (rudder servo only).
- **Dive capability**: Submerge to 10m (claims 100m) via ballast — hide from weather
  or threats.
- **Patents**: 11 issued, 8 pending. Key: US 10,029,773 (submerged sailing vessel),
  US 10,625,841 (passive wing control), US 10,526,096 (solar wind sail).
- **Relevance**: Closest existing platform to our maritime concept. Different nav
  approach (we use LNS8 PF for dead reckoning, they use GPS-only).
- Refs: https://subseasail.com/, https://subseasail.com/unmanned-autonomous-surface-vessels/our-tech

### Microtransat / SailBot Competitions

- **History**: Started 2010. >30 attempts, **one success** (Sailbuoy SB Met, 2018).
- **Failure modes**: Battery death, seal failures, fishing net entanglement, picked up
  by curious fishermen, Sargasso Sea seaweed, "lost at sea with vague last-known location."
- **Hull designs**: Mono-hull with double-skinned foam core, carbon Kevlar composite.
  Self-righting via weighted keel essential.
- **Key lesson**: After 14+ years, one success. Reliability over months is the
  dominant challenge, not performance.
- **Active competitions**: SailBot/IRSR 18th annual at Cornell, June 2026.
- Refs: https://www.microtransat.org/, https://www.sailbot.org/

### Iridium SBD (Comms Reference)

- **Message size**: 340 bytes max (mobile-originated), 270 bytes (mobile-terminated).
- **Power**: 73 µA sleep (takes 24 hrs to settle — use hard power-gate instead).
  1.5 A·s per successful send. **Failed sends cost 2.5× more** (3.8 A·s).
- **Cost**: ~$0.04-0.15 per message.
- **Lifetime**: 2,400 mAh battery sending 100-byte messages every 100 min → ~0.95 years.
- **Coverage**: Only true pole-to-pole global (66 LEO sats at 780 km).
- Refs: https://docs.groundcontrol.com/iot/rockblock/user-manual/9603-power-consumption

### Maritime Key Takeaways

| Factor | Lesson |
|--------|--------|
| Power | Entire Argo array <100W. Sailbuoy: 110 mW autopilot. SubSeaSail: <1W total. |
| Comms | Iridium SBD only viable for global ocean. 340 bytes, hard power-gate. |
| Propulsion | Sail = zero-power with steering authority. Rigid wing is consensus for unmanned. |
| Endurance limiter | Biofouling at 2-4 months, not energy. |
| Failure modes | Seals, fishing nets, curious fishermen, calm weather. |
| Scale | Simple + cheap + many beats complex + expensive + few. |
| Prior art | SubSeaSail already builds sail+dive. Our differentiation is LNS8 PF nav. |


## River / Waterway Robots

### Platypus Autonomous Boats (CMU)

- **Form factor**: Lutra Airboat — 13 lbs, vacuum-formed polyurethane hull + airbags.
  12" covered fan, 5-6 mph, ~2 miles per charge. Also Felis series (30 km/h, 100 kg payload).
- **Compute**: Android phone + custom circuit board. Bluetooth + wireless/3G/EDGE.
- **Sensors**: Sonar (depth), pH, dissolved O₂, conductivity, temperature, mass spec.
  ~4,500 data points/acre/sensor, 21,000+ per deployment with six sensors.
- **Notable deployment**: Three weeks in Kenya's Mara River mapping hippo fecal deposits
  via sonar in waters too dangerous for human researchers.
- **Co-operative**: Multi-boat coordinated surveys.
- Refs: https://senseplatypus.com/, https://www.cmu.edu/news/stories/archives/2014/may/may22_hippowaterquality.html,
  https://www.ri.cmu.edu/pub_files/2012/7/valada_fsr_2012_new.pdf

### River Drifter Studies (Lagrangian)

- **UCSD River Drifter**: Derived from CODE ocean drifter. 0.6m long, cross-section
  0.39 m². GPS + Nortek ADCP (3D velocity profiles beneath drifter) + SST + salinity.
  Fleet deployments for dispersion studies.
- **Open-source drifter** (2022): <150 EUR, off-the-shelf components. SIM800L (GSM) +
  RFM95 (LoRa). BNO055 9-DOF IMU. 1 Hz data via MQTT.
- **RTK-GNSS drifters**: 10 Hz, centimeter-level accuracy — far better than standard GPS.
- **Key insight**: These are closest to our concept but have NO steering. We add
  fins/rudder + ballast — genuinely novel.
- Refs: https://gdp.ucsd.edu/ldl/river/, https://pmc.ncbi.nlm.nih.gov/articles/PMC9780804/

### ROUGHIE (Purdue) — Closest Engineering Analog

- **Concept**: Underwater glider for shallow lakes/ponds. No external propulsion or steering.
- **Buoyancy**: Pumps water in/out of internal ballast tank.
- **Pitch**: Slides battery pack fore/aft on rail.
- **Roll/turning**: Tilts internal components port/starboard → roll → turn.
- **Turning radius**: 3m (10 ft) — very agile vs 10m for typical gliders.
- **Sensors**: Fluorimeters, magnetometers. Totally silent.
- **Relevance**: Proves ballast + internal mass-shifting provides adequate steering
  authority in shallow water. Directly applicable to river current-rider.
- Refs: https://newatlas.com/robotics/roughie-auv-underwater-glider/

### USGS Stream Gauges

- **Network**: 8,705 streamflow sites + 3,460 water-level-only (Oct 2024).
  1,885+ partner agencies.
- **Velocity measurement**: ADCP on traversing boats. Bottom-tracking + GPS.
  Resolution 2.5 cm bins, velocity accuracy within 1%.
- **Fixed velocity**: Parameter code 00055 — ADVM upward-looking acoustic Doppler.
- **APIs**: Legacy: https://waterservices.usgs.gov/nwis/iv/ (JSON, XML, RDB).
  New OGC API: https://api.waterdata.usgs.gov/ogcapi/v0/ (rolling out 2025).
  R package: `dataRetrieval`.
- **Relevance**: Free real-time current conditions for pre-mission planning. Ground
  truth for scenario generator calibration.
- Refs: https://waterdata.usgs.gov/, https://waterservices.usgs.gov/

### River Current Profiles

- **Vertical**: Log-law or 1/6th power-law. Surface velocity ~1.18× depth-averaged
  (α coefficient ~0.85, range 0.7-0.9 natural rivers, ~0.9 concrete channels).
- **Velocity dip**: Maximum velocity often occurs BELOW surface, not at it, especially
  in narrow channels or near banks. Secondary currents cause this.
- **Cross-section**: Maximum near thalweg (deepest channel), decreasing toward banks.
  Shifts to outside of bends.
- **Turbulence**: Large-scale eddies scale with depth (h) vertically, ~2h laterally,
  4-7h downstream. Boils occur in low-speed streaks.
- **Relevance**: The velocity gradient IS our engine. Depth changes via ballast = speed
  control. Surface velocity 1.18× depth-averaged = meaningful authority.
- Refs: https://www.mdpi.com/2073-4441/15/21/3711,
  https://www.sciencedirect.com/science/article/abs/pii/S0309170807000152

### Forward-Looking Sonar for Rivers

- **FLS**: Real-time imaging ahead of platform. Multi-beam electronically steered
  for rapid, high-res 2D/3D. Argos 500: 500m range. Vigilant: 600m, works in 1-2m depth.
- **Frequency tradeoff**: Low freq = long range, low res. High freq = short range, high res.
- **For a passive drone**: FLS is critical — limited braking ability means early
  detection is everything. Need high-frequency FLS for resolution at short ranges
  in shallow water (1-5m depth).
- Refs: https://www.unmannedsystemstechnology.com/expo/forward-looking-sonar/

### Evolutionary Hull Optimization

- **Methodology**: GA (typically NSGA-II) + CFD. Well-established, numerous papers.
- **Parameterization**: Nose length, parallel body, tail, max diameter, shape coefficients.
- **Results**: 7.9-12.1% drag reduction, 2.0-14.1% overturning moment reduction.
- **Kriging surrogates**: Sobol sampling → approximate CFD → accelerate search.
- **Our objective is unique**: Not "minimize drag" but "minimize velocity differential
  with current while maximizing fin authority."
- Refs: https://journals.sagepub.com/doi/10.1177/1475090217714649,
  https://www.sciencedirect.com/science/article/abs/pii/S0029801823021066,
  https://github.com/jlobatop/GA-CFD-MO

### River Key Takeaways

| Factor | Lesson |
|--------|--------|
| Prior art | **No one** is doing autonomous current-riding with fin/rudder steering. Genuinely novel. |
| Closest analog | ROUGHIE (Purdue) — ballast + mass-shifting in shallow water. 3m turning radius. |
| Velocity profile | Surface ~1.18× depth-averaged. Depth control = speed control. |
| Control paradox | Perfectly matching current → zero fin authority. Need deliberate velocity differential. |
| Navigation | Sonar + IMU, no GPS. FLS critical — can't brake, must detect early. |
| Current data | USGS free real-time API for pre-mission conditions. |
| Hull optimization | GA+CFD is mature; our objective function is novel. |


## Ultralight Aerial

### senseFly eBee

- **eBee X**: 1.3-1.6 kg, 116 cm wingspan, 55-90 min endurance, 500 ha coverage.
- **eBee VISION**: 1.6 kg, 90 min endurance, 20 km encrypted link. Blue UAS cleared.
- **Key lesson**: Fixed-wing at ~1 kg achieves 90 min on small LiPo. Extremely
  favorable power-to-weight ratio at this scale. Foam = damage-tolerant + cheap.
- Now under AgEagle / EAGLENXT branding.
- Refs: https://eaglenxt.com/drones/ebee-x/

### DelFly (TU Delft)

- **Explorer**: 20g, 28 cm wingspan, 9 min autonomous. Autopilot 0.98g, stereo
  vision 4.0g. Camera runs at 20 Hz on STM32F4 (168 MHz). Feature histograms for
  optical flow + stereo disparity. **Lightest fully autonomous MAV ever demonstrated.**
  **Total flight power ~4.4W** (180 mAh × 3.7V / 9 min). ~85% motor, ~15% sensors + MCU.
- **Micro**: 3g variant, ~1W total (often confused with Explorer).
- **Nimble**: 29g, 33 cm wingspan, ~17 Hz flapping. **First tailless flapping-wing MAV.**
  Control via differential wing root adjustments (insect-inspired, no tail surfaces).
  Published in Science (2018). Max 7 m/s, cruise efficiency peak ~3 m/s, range >1 km.
- **Relevance**: Control through rotation dynamics modulation (no flaps) is directly
  relevant to samara concept.
- Refs: https://mavlab.tudelft.nl/delfly-nimble/, https://mavlab.tudelft.nl/delfly-explorer/

### UW Microflier (Vikram Iyer, University of Washington)

- **Weight**: 30 milligrams. Flexible PCB IS the airframe.
- **Power**: No battery. Solar cells + capacitor (triggers on at dawn).
- **Comms**: Radio backscatter — reflects existing RF, near-zero power.
- **Landing**: Solar panels face upright with 95% accuracy (passive tumble-settle).
- **Dispersal**: Up to 100m from drone release in moderate breeze.
- Published in Nature 603, 427-433 (2022).
- **Also**: Northwestern 3D microfliers (John Rogers) — grain-of-sand scale,
  pop-up book fabrication, carry pH sensors, photodetectors.
- Refs: https://www.nature.com/articles/s41586-021-04363-9,
  https://www.nature.com/articles/s41586-021-03847-y

### CICADA (NRL) — Flying PCB Glider

- **Concept**: Airframe IS a printed circuit board. GPS-guided disposable glider.
- **Performance**: Glides to within 15 ft of target coordinates. No propulsion.
- **Size**: 18 vehicles fit in 6-inch cube. Cost ~$250 each.
- **Sensors**: Acoustic, magnetic, chemical/biological, SIGINT. Form ad-hoc networks.
- **Relevance**: Closest military precedent to our concept. But one-way glide-down only.
  Our innovation: thermal riding for persistence.
- Refs: https://spectrum.ieee.org/naval-research-lab-tests-swarm-of-stackable-cicada-microdrones

### Samara/Maple-Seed Drones

- **Lockheed Samarai**: 30 cm radius, <0.5 lb, only 2 moving parts. VTOL, hover, lateral.
  DARPA Nano Air Vehicle program. Natural fail-safe: autorotates on power loss.
- **UMD Robotic Samaras**: 7.5 cm to 0.5m, carbon fiber. Controlled by wing pitch.
  **SAW platform**: multiple samaras attach as collective rotor, separate into
  individual units mid-flight on command.
- **SUTD F-SAM**: 69g, single foldable wing. Single actuator. Passively stable —
  no closed-loop control needed for flight.
- **Aerodynamics**: Re 900-3,500. Stable leading edge vortex (LEV) provides lift
  enhancement during autorotation. Same mechanism as insect flight.
- Refs: https://newatlas.com/lockheed-martin-samarai-flyer-monocopter/19572/,
  http://www.avl.umd.edu/projects/proj11-robotic-samara.html,
  https://spectrum.ieee.org/watch-this-drone-explode-into-maple-seed-microdrones-in-midair

### Thermal Soaring

- **ALOFT (NRL, Dan Edwards)**: 5 kg sailplane, >100 test flights. **5.3 hours from
  300 ft winch launch.** 113.4 km range. **Beat human pilots** at Montague Cross Country.
  Strictly feedback control, no thermal drift estimation needed.
- **ArduSoar**: Open-source in ArduPilot. Total energy variometer (height + airspeed + roll)
  → EKF estimates thermal center/strength/radius → circle-and-glide.
- **Microsoft Frigatebird**: POMDP + RL for thermal/ridge/wave soaring strategy.
  Open source: https://github.com/microsoft/Frigatebird
- **How it works**: BMP390 baro (0.1m noise RMS) + IMU = total energy variometer.
  Detect updraft, circle, EKF estimates center, shift circle. Thermals: 100-500m
  diameter, 1-3 km apart, 1-5 m/s updraft.
- Refs: https://apps.dtic.mil/sti/pdfs/ADA614555.pdf, https://arxiv.org/pdf/1802.08215,
  https://ardupilot.org/plane/docs/soaring.html

### CoulombFly — Solar Rotorcraft

- **Weight**: 4.21g. Electrostatic motor (1.52g). GaAs cells >30% efficiency (0.48g).
- **Hover power**: 137 mW. Lift-to-power: 30.7 g/W (vs 5-10 g/W conventional motors).
- **Key**: Sustained flight under sunlight. 9,000V operating voltage, microamp currents.
- Refs: https://spectrum.ieee.org/smallest-drone

### Gram-Scale Sensors

| Sensor | Type | Weight | Key Spec |
|--------|------|--------|----------|
| BMP390 | Barometer | ~mg (2×2mm LGA) | 0.1m altitude noise RMS |
| BMP585 | Barometer | ~mg, IP68 rated | 0.08 Pa noise |
| ICM-42688-P | 6-axis IMU | ~mg (2.5×3mm LGA) | 2.8 mdps/√Hz gyro noise |
| BMI270 | 6-axis IMU | ~mg (2.5×3mm LGA) | Ultra-low power mode |
| PMW3901 | Optical flow | ~1g on minimal PCB | SPI, on-sensor processing |
| Flow Deck v2 | OptFlow + ToF | 1.6g total (21×28mm) | PMW3901 + VL53L1x |

### Solar Power Scaling

Solar power per unit mass **increases** as drone scale decreases (area ∝ L²,
mass ∝ L³). At gram scale, 1 cm² GaAs cell (~0.1g) provides ~30 mW in sun.
Sensors + MCU + radio can run on 1-10 mW. Power margin exists for intermittent
actuator operation.

### Aerial Key Takeaways

| Factor | Lesson |
|--------|--------|
| Closest precedents | CICADA (flying PCB, one-way) + ALOFT (thermal soaring, reusable) |
| Thermal detection | Solved: BMP390 + IMU → total energy variometer. ArduSoar is open-source. |
| Control at gram scale | Samara autorotation naturally exploits LEV. Wing pitch modulates descent. |
| 40% battery rule | Zephyr and AtlantikSolar both ~40% battery. Thermal riding breaks this. |
| Power | CoulombFly: 137 mW hover at 4.2g. Solar favorable at small scale. |
| Key open question | Can descent rate modulation gain altitude in 1-5 m/s thermal? |


## Cross-Domain Synthesis

### Common Themes

1. **Power is always the constraint**. Argo array: <100W globally. Sailbuoy: 110 mW
   autopilot. Passive propulsion (wind, current, wave, thermal) is the only way to
   achieve long-duration operation.

2. **Simple + many > complex + few**. Argo's 4000 floats, Sofar's thousands of
   Spotters, CICADA's $250 disposables. Cost per unit determines fleet size
   determines spatial coverage.

3. **Relative navigation works**. DelFly: no GPS. River vehicles: sonar > GPS.
   ALOFT: baro + IMU for thermal soaring. IMU + environmental sensors substitute
   for absolute positioning.

4. **Sensor noise dominates at low power**. LNS8 quantization (~4.4%) is below
   typical sensor noise. BMP390 noise floor (0.1m) and MEMS gyro bias (1-10°/hr)
   set accuracy limits, not 8-bit arithmetic.

5. **Mechanical failure > electronic failure**. Tethers, servos, seals, hulls. The
   Microtransat record (30+ attempts, 1 success over 14 years) is about reliability.

6. **Biofouling is the universal ocean endurance limiter** at 2-4 months.

### Where Our PF Fits

| Platform | PF Role | Key Challenge | Update Rate |
|----------|---------|---------------|-------------|
| Maritime drifter mesh | Fleet-relative nav, acoustic event fusion, shear estimation | No continuous GPS, mesh coordination | 1 Hz |
| River drone | All navigation — banks, obstacles, current, relative position | No absolute reference, 20D state | 10 Hz |
| Aerial sensor | Thermal detection, altitude hold, drift estimation | Extreme power/weight, gram-scale | 50 Hz |

The LNS8 PF's ~90 µW total chip power (1 Hz) makes it viable for all three.
**FPGA vs MCU only matters when the platform is near-passive** (<10 mW total).
On active platforms (sail servos, motors, pumps at 100+ mW), the MCU/FPGA
compute delta is lost in the noise. This has sharpened our maritime focus
toward tiny passive drifters where sub-mW PF compute is a differentiator.

For the river drone (10 Hz, 20D), weight diversity survival at high dimensions
is the critical open question.
