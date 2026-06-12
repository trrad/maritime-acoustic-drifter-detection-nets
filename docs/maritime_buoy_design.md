# Maritime — Passive Acoustic Drifter Mesh

## Concept

Persistent, low-cost mesh network of near-passive drifters for **detecting and
triangulating small dark vessels** (sub-15m, AIS-off, silent running) across
bounded ocean areas. Each node is a matchbook-to-softball-sized platform with
a hydrophone primary payload, IMU-based wave/motion sensing, and LoRa mesh
comms. Navigation via LNS8 FPGA particle filter fusing rare GPS fixes,
continuous dead reckoning, and inter-node LoRa ranging.

## Mission: Small Dark Vessel Detection

Persistent gap in the satellite stack: a sub-15m vessel running dark (no AIS,
no radar, silent comms) under cloud cover in moderate sea state is **invisible
to every space-based modality** except commercial SAR tasked to that exact km²
in that exact minute. Passive acoustic detection fills this gap.

**Use cases:**
- **EEZ fisheries enforcement** — small-vessel IUU detection below the
  SAR floor (~15 m waterline), in regions that can't afford persistent
  patrol fleets
- **Marine-protected-area monitoring** — persistent compliance coverage
  where patrol presence is intermittent
- **Remote-coast / seasonal surveillance** — e.g. Arctic ice-free
  windows between patrol sorties
- **Strategic chokepoints and counter-smuggling** — dark-vessel traffic
  monitoring
- **Distributed naval sensing** — a smaller-scale, expendable SOSUS
  analog

The simulation testbed is the Salish Sea (dense reanalysis coverage,
fjord/strait/plume regime that transfers to comparable coastal waters).

**Scale of the problem:**
- 75% of industrial fishing vessels are not publicly tracked (Paolo et al. *Nature* 2024, Sentinel-1 analysis)
- IUU fishing: $23B/year (FAO), up to $50B broader estimates
- 11-26 million tonnes of fish taken illegally per year

## Why Now (and Why This Architecture)

Three things recently converged to make this feasible:

1. **Satellite gap data is now public and actionable** — Global Fishing Watch
   has quantified the dark-vessel problem. The gap is real and measurable.

2. **Hydrophones + small compute got cheap** — piezo elements + low-power
   op-amps + modern MCUs/FPGAs handle acoustic detection at <10 mW.

3. **LoRa mesh makes fleet coordination practical** — peer-to-peer, no
   satellite subscription, ~1000× cheaper per message than Iridium.

**Where our LNS8 PF differentiates**:
- Near-passive platforms run on ~1-10 mW total average power. On those, MCU
  PF compute is 20-40% of the budget; FPGA PF is ~2%.
- High-dimensional state (own position + N neighbor relative positions +
  current/shear estimation + IMU bias) scales sublinearly on FPGA.
- Fleet of 100-1000 autonomous nodes each running its own PF copy.

## Form Factor

- **Size**: Matchbook to softball, 100-250g
- **Shape**: Spar-style (tall thin buoy with short antenna mast above, weight
  below waterline) for self-righting and low wind drag. Like a miniature
  Sofar Spotter but with drogue/ballast capability.
- **Buoyancy**: Small variable ballast (Argo-principle scaled down, ~20-50 mL
  displacement) for shear-exploitation station-keeping.
- **Drogue**: Fabric drogue at 15m depth for drift-rate reduction. Optional
  — depends on deployment zone.
- **Antenna**: Short flexible mast above waterline for LoRa + optional GPS +
  optional Iridium (anchor nodes only).
- **Hydrophone**: Piezoceramic element below waterline, potted assembly.
- **Power**: Small Li-ion (~5-20 Wh) + 1-4 cm² solar panel on top.
- **Enclosure**: Potted PCB in polyurethane or epoxy — disposable philosophy,
  no recoverability required.

## Fleet Architecture (Three Node Classes)

| Class | Role | % of Fleet | Per-Node Cost | Capabilities |
|-------|------|------------|---------------|--------------|
| **Anchor** | Fixed or tidal-locked reference. GPS + Iridium exfil. | 5-10% | $500-1000 | GPS fix, satellite comms, higher compute, larger battery |
| **Shear-keeper** | Semi-stationary via depth cycling. Deep parking. | 30-50% | $50-100 | Ballast pump, full sensor suite, LoRa mesh |
| **Drifter** | Cycle through area, accept drift, replaced on rotation. | 40-60% | $30-50 | Minimal sensors, LoRa only, pure acoustic listener |

Anchors provide the **reference frame** for fleet-relative navigation.
Shear-keepers provide the **body of persistent coverage**. Drifters provide
**density and redundancy**, cycling in/out of the monitored area.

## Mission Sensing

### Primary: Passive Acoustic (Hydrophone)

- **Element**: Piezoceramic (cylindrical or disk, ~cm-scale), passive — generates
  voltage from pressure. Cost: $2-8 depending on sensitivity/bandwidth.
- **Signal chain**: Low-noise op-amp (~0.1 mW) + ADC (~1-5 mW at kHz rates).
  Tiered processing — always-on low-power threshold detector wakes full chain
  on event.
- **Detection bandwidth**: 500 Hz - 5 kHz primary (outboard tonals,
  propeller cavitation); 200 Hz - 10 kHz secondary extended band.
- **Detection range** (measured in `experiments/harmonic_prototype/23_acoustic_detection.py`, see `figures/26_acoustic_detection.png`):

| Target class | Energy-only, SS 4 | Classifier-assisted (+20 dB), SS 4 |
|---|---|---|
| 155 dB small trawler (~15 m) | 0.92 km | **8.4 km** |
| 140 dB gasoline outboard (~5 m skiff) | 0.17 km | **1.6 km** |
| 120 dB electric / sail / silent | 0.02 km | 0.17 km |

- **Note on "array gain"**: at km-scale inter-node spacing, coherent
  beamforming does NOT apply (wavelength 15-75 cm at 2-10 kHz;
  elements need <λ/2 separation for coherent gain). Multi-node
  advantage is independent-detection coverage and TDOA triangulation
  once ≥3 nodes detect — NOT per-node SNR gain. Previous "10-20 km
  with 3-node array" framing conflated these; detection range is
  set by per-node physics + classifier, not array geometry.
- **Classifier is on the critical path.** Energy-only detection ranges
  are too short for useful mesh triangulation at 5 km spacing. The
  product requires a narrowband/matched-filter classifier that
  identifies propeller blade-rate or engine firing tonals against a
  template bank. ~20 dB gain is typical; training-data pipeline is a
  required work item (AIS-correlated labelled events, ONC PAM
  archives, BC coastal field recordings).

**Duty cycling strategy**:
- Always-on ultra-low-power analog envelope detector (~100 µW)
- Wake full ADC + processing on detection (~5-10 mW for 1-10 seconds)
- Report detection + spectral signature + TDOA timestamp via LoRa to neighbors
- Average power: ~1-2 mW for acoustic sensing

### Secondary (Free from Navigation Sensors)

- **Wave spectra** (Sofar-style from IMU) — already computed for PF navigation
- **Sea surface temperature** — single thermistor, <1 mW
- **Atmospheric pressure** (when surfaced) — free from nav barometer
- **Drift pattern data** — free byproduct of PF state logging

## Fleet Coordination (LoRa Mesh)

### Comms Pattern
- **TDMA frame**: 1-hour cycles with scheduled slots per node
- **Wake window**: 50 ms per slot (sync beacon + listen + TX if needed)
- **Duty cycle**: ~0.0014% → **~220 µW average** for LoRa comms
- **TOA/RSSI ranging**: Each beacon exchange provides inter-node range
  measurement with ~10-50m accuracy (LoRa SX1262 TOF ranging)

### What Gets Transmitted
- Periodic: position estimate, battery status, detection summaries
- Event: acoustic detection with TDOA timestamp + spectral signature
- On demand: raw short acoustic snippet to anchor for classification

### Exfiltration
- Anchor nodes aggregate mesh data
- Iridium SBD burst to shore: 1-2 messages/hour, hard power-gated
- Aggregated detection events + periodic health summary
- Anchor power budget: ~5-10 mW for Iridium (can afford it — anchors have
  bigger batteries and optional solar)

## Navigation: The PF's Role

### State Vector (variable dimension per node class)

**Drifter node (15D, minimal)**:
```
Position:      lat_off, lon_off, depth                              (3)
Velocity:      vx, vy, vz                                           (3)
Orientation:   heading                                              (1)
Environment:   current_vx, current_vy                               (2)
IMU bias:      gyro_bias ×3, accel_bias ×3                          (6)
                                                                    ----
                                                                     15
```

**Shear-keeper node (21D, full)**:
```
Drifter state (15D)                                                 (15)
Surface current:  surf_vx, surf_vy                                  (2)
Deep current:     deep_vx, deep_vy                                  (2)
Neighbor ranges:  r_1 ... r_N (top N neighbors)                     (2)
                                                                    ----
                                                                     21
```

Multi-depth current estimation enables shear-exploitation station-keeping.
Neighbor ranges feed fleet-relative multi-lateration.

**Anchor node (25D+)**:
```
Shear-keeper state (21D)                                            (21)
More neighbor ranges                                                (+4)
                                                                    ----
                                                                     25+
```

### Observation Model

| Sensor | Observes | Availability |
|--------|----------|--------------|
| GPS (anchor only, rare) | Absolute position | Once per hour at anchor nodes only |
| Pressure | Depth | Continuous |
| Magnetometer | Heading | Continuous |
| IMU | Orientation, acceleration, bias | Continuous |
| LoRa TOA to neighbors | Range | Per TDMA slot |
| Hydrophone event | TDOA to acoustic source | Event-driven |
| Water speed (optional) | Velocity rel water | Continuous if equipped |

### Why High-D PF on FPGA Matters Here

1. **Neighbor-relative nav** — without GPS on most nodes, multi-lateration from
   LoRa TOA measurements is the primary position reference. Jointly estimating
   own position + neighbor positions scales as O(D²) in a Kalman filter but
   stays O(D) in a particle filter with appropriate factorization. At 20-25D,
   this is expensive on MCU, cheap on FPGA.

2. **Shear estimation for station-keeping** — PF estimates surface and deep
   current velocities from observed drift at different depths. Used to plan
   ballast cycles. Continuous multi-dimensional environment estimation is
   exactly what the particle filter is built for.

3. **Acoustic TDOA fusion** — detection events with timestamps from 3+ nodes
   localize the acoustic source. Mesh-level PF fusion of these detections
   gives a tracked contact, not just a set of pings.

## Station-Keeping Strategies

Typical drift rates without station-keeping:

| Config | Drift |
|--------|-------|
| Surface, undrogued | 20-90 km/day (1-5% wind speed + current) |
| Surface, drogued | 10-50 km/day |
| Parking depth (200-1000m) | 2-10 km/day |
| Deep (1000m+) | 1-5 km/day |

### Strategy per Node Class

**"Anchor" is a role, not a form factor.** Defined by surfacing cadence
(more frequent GPS fixes than drifters) and exfil role (Iridium), NOT
by mooring. Three implementation patterns:
- Fixed moored buoy in coastal zone (classic anchor, zero drift)
- Shear-keeper node with higher GPS-surfacing duty cycle
- Opportunistic: a patrol vessel acts as an anchor during sortie
  windows, providing occasional high-accuracy fixes that anchor the
  mesh timebase. Free when the platform is operationally deployed.

**Shear-keepers** (station-ish keeping, not locked):
- Deep parking at 200-500m where flow is 5-10× slower
- Periodically ascend to surface for LoRa sync, acoustic listening, solar charge
- In shear zones (equatorial undercurrents, western boundaries, coastal): alternate
  depths to hold station
- **Realistic envelope: 1-1.5 km at central SoG basin sites** with
  perfect-knowledge greedy-myopic control (prototype measurement,
  `experiments/harmonic_prototype/FINDINGS.md` Step 10). 500 m is not
  physically achievable at these sites; 2 km envelope reaches ~60% of
  surveyed stations. Target deployments outside SoG (tropical gyres,
  weakly-sheared basins) will have correspondingly larger envelopes.
- Power: ballast pump at low duty cycle, ~1-5 mW average

**Drifters**:
- Accept drift, cycle through the area
- Pure passive, minimal ballast (just surface/subsurface cycling for sensor
  optimization)
- Replaced at boundaries as they exit

**Mixed-density fleet design principle.** Uniform spacing is wrong for
the mission. Different target classes need different fleet density:
- 155 dB trawler: detects at 8 km, so 5-10 km spacing triangulates.
- 140 dB skiff (main IUU target): detects at 1.6 km, so 1-2 km spacing
  required for reliable triangulation.
- 120 dB silent (electric/sail/paddle): detects at 170 m, so either
  very dense (<0.3 km) or deprioritized.

This implies deployments have **dense cells (~1-2 km)** at high-value
zones (MPA borders, known IUU hotspots, narrow passes) and **sparse
cells (~5 km)** elsewhere. Total fleet economics shift 3-6× depending
on density mix.

### Coverage Math (EEZ-Scale Example, mixed-density)

Target: 50,000 km² area. Two density tiers:
- **Sparse tier (~5 km spacing)** across 80% of area — catches commercial-size (155 dB+) targets. ~1600 nodes.
- **Dense tier (~1.6 km spacing)** in 20% high-value zones (MPA borders, known IUU hotspots, narrow passes) — catches small-skiff-class (140 dB) targets. ~3900 nodes.
- Total: ~5500 nodes (vs 2000 in the previous uniform-spacing estimate).

Fleet composition (indicative, 5-10% anchor / 30-50% shear-keeper / 40-60% drifter):
- 400 anchors × $750 = $300K
- 2200 shear-keepers × $75 = $165K
- 2900 drifters × $40 = $116K
- **Upfront: ~$580K**
- Replacement: ~30% of drifters per month at $40 = $35K/month = $420K/year
- **Year-1 total: ~$1.0M**

Comparison:
| Approach | Year-1 Cost | Coverage |
|----------|-------------|----------|
| **Drifter mesh (mixed density)** | **~$1.0M** | Persistent 24/7, small-boat + commercial |
| Drifter mesh (uniform 5 km) | ~$400K | Persistent 24/7, commercial only |
| Commercial SAR daily tasking | $274M | 1 snapshot/day, weather-limited |
| Patrol vessel | $3.6M | ~10% of area at any time |
| Single Saildrone | $900K | Patrol strip only |

**Small-vessel-capable mesh is ~250× cheaper than SAR, ~4× cheaper than patrol vessels.**
The previous "$400K total" number covers commercial-vessel detection only;
adding small-vessel (IUU fishing skiff) capability requires the denser
tier and roughly 2-3× the budget.

## Power Budget

### Drifter Node (target <2 mW average)

| Subsystem | Avg Power | Notes |
|-----------|-----------|-------|
| FPGA PF (LNS8, 15D, 1 Hz) | ~90 µW | iCE40 UP5K |
| IMU (ICM-42688-P, LP mode) | ~40 µW | Accelerometer + gyro |
| Pressure (BMP390 @ 1 Hz) | ~6 µW | |
| Magnetometer (RM3100 @ 1 Hz) | ~25 µW | |
| Acoustic listener (analog threshold) | ~100 µW | Always-on |
| Acoustic detection event processing | ~200 µW avg | 5-10 mW peak, event-driven |
| LoRa mesh (TDMA, 0.0014% duty) | ~220 µW | Per-hour slot |
| MCU housekeeping (nRF52 in sleep) | ~50 µW | |
| **Drifter total** | **~730 µW** | Under 1 mW — fits sub-2 mW budget |

### Shear-keeper Node (target <5 mW average)

Adds: ballast pump (~1-5 mW at low duty), larger PF state (21D, ~150 µW),
more active acoustic processing. Total: ~3-5 mW.

### Anchor Node (target <50 mW average)

Adds: GPS MAX-M10S (5-10 mW duty cycled), Iridium 9603N (5-10 mW hard-gated),
larger PF state (25D+), possibly camera or other richer sensor. Total: ~30-50 mW.

### Battery & Solar Sizing

**Drifter**:
- 0.73 mW × 24 × 180 days = 3.15 Wh → single CR123A or small Li-ion
- Add 1 cm² Si solar (~15 mW peak) → indefinite operation above water
- Design for 6-month biofouling lifetime without solar (batteries only)

**Shear-keeper**:
- 4 mW × 24 × 180 days = 17 Wh → 18650 cell
- 2-3 cm² solar → extends to 1+ year

**Anchor**:
- 40 mW × 24 × 365 days = 350 Wh → 18650 pack (4-6 cells) or equivalent
- 10 cm² solar panel → sustained operation year-round

## Build Cost (10K Volume BOM)

### Drifter (minimal)

| Component | Cost |
|-----------|------|
| iCE40 UP5K | $5-8 |
| MCU (STM32G0) | $0.50-1 |
| LoRa SX1262 | $2-4 |
| IMU (ICM-42688-P) | $1-2 |
| Barometer (BMP390) | $1-2 |
| Magnetometer (LIS3MDL) | $1 |
| Hydrophone (piezo + op-amp) | $2-5 |
| PCB + assembly | $3-8 |
| Enclosure (potted) | $1-3 |
| Battery (single 18650) | $1-3 |
| Solar cell (1 cm² Si) | $0.50-1 |
| Antenna, misc | $1-2 |
| **Drifter BOM** | **$20-40** |

### Shear-keeper

Adds: ballast pump + valve ($8-15), larger battery, bigger solar. **$45-80**.

### Anchor

Adds: GPS ($5-10), Iridium 9603N ($100-150 volume), bigger everything,
possibly camera. **$300-600**.

## Key Technical Risks

Ranked by estimated likelihood × magnitude.

1. **Acoustic classifier is on the critical path, not polish.** Energy-
   only detection at 5 km spacing doesn't triangulate small vessels
   (see acoustic section). The product needs a narrowband / matched-
   filter classifier delivering ~20 dB gain. Training-data pipeline
   (AIS-correlated labelled events, ONC PAM archives, BC coastal
   field recordings) has to be a real plan with budget and timeline,
   not a future-work bullet. Currently unscoped.

2. **Matchbox-scale variable ballast is unresolved hardware.** Argo's
   pump (50-150 mL displacement, 25 kg platform, 1000 m depth) is
   mature; no COTS equivalent exists at sub-Argo scale for reliable
   6-month salt-water operation. Candidate vendors: Bartels mp6, Lee
   Company micro-solenoid, TTP Ventus. Partner decision required
   before physical prototype work.

3. **Acoustic array synchronization** — TDOA triangulation requires
   ~1 ms time sync across nodes. GPS-disciplined clocks on anchors +
   TDMA-slot sync to drifters can provide this, but needs validation
   at fleet scale. Store-and-forward mesh architecture is resilient
   to short-term jamming (nodes surface km away, hours later, with
   TDOA timestamp intact).

4. **Biofouling on hydrophone** — piezo elements accumulate biological
   growth that damps response, preferentially in the 2-10 kHz
   detection band. Antifouling coatings + periodic acoustic exercise
   to keep clear. 90-day in-water test in BC coastal water required
   to measure day-0 vs day-90 sensitivity; currently unscoped.

5. **False detections from biological / shipping / weather clutter** —
   snapping shrimp in reef environments, cetacean song in BC coastal,
   commercial shipping tonals, wave/rain/lightning. Classifier with
   multi-node coincidence filtering rejects these; false-alarm rate
   is the operational metric the training-data pipeline determines.

6. **End-of-life / marine debris**. Disposable drifters = e-waste +
   plastic going into protected waters. MARPOL Annex V, Canada's OPP
   marine-plastics rules, First Nations co-management consultation
   (Haida Gwaii, Coastal First Nations, Inuit Nunangat) all apply.
   Candidate strategies: sink-on-exhaustion (seafloor debris but out
   of surface plastic gyre), corrodible magnesium-alloy or bioplastic
   enclosures, surface beacon on battery death for recovery. Needs a
   written policy (`docs/eol_policy.md`, stub).

7. **Evidentiary chain for prosecution**. Detections need to be
   court-admissible under fisheries law. Requires GPS-disciplined
   timestamps, sensor calibration provenance, reproducible detection
   algorithm, and defensible false-alarm-rate statistics. AIS-dropout
   + acoustic-coincidence is a specific prosecution-grade signal
   pattern worth developing (`docs/evidentiary_chain.md`, stub).

8. **Shear-keeper current model** — station-keeping via shear
   exploitation requires accurate local current model. PF must
   estimate from observations; climatology provides prior. Phase-2
   work in progress; see `docs/reference/controller_architecture.md`
   and the Phase 2.1 plan.

9. **LoRa range at sea** — line-of-sight good but spar-antenna limits
   height. 5-10 km realistic with small antennas at sea level. May
   constrain deployment density at the sparse-tier end.

10. **Deployment logistics** — mixed-density fleet (5000+ nodes)
    needs a deployment strategy: research vessel bulk drop, air drop
    from fixed-wing, piggyback on commercial shipping, or multi-sortie
    patrol-vessel operations. Unit cost of deployment may exceed unit cost of
    hardware at dense-tier locations.

11. **Regulatory/legal** — UNCLOS (less of an issue in Canadian EEZ),
    MARPOL, ITAR for export cases, Canadian RF licensing (ISED
    Spectrum Management) at fleet scale, Marine Mammal Regulations
    under the Fisheries Act. Details in the stub `docs/risks.md`.

## Comparison to Existing Platforms

| Feature | Ours (Drifter Mesh) | Argo Float | Sofar Spotter | SubSeaSail | Sailbuoy |
|---------|--------------------|-----------|---------------|------------|----------|
| Unit cost (10K vol) | $30-100 | $25-185K | $5-15K | ? | $50-100K |
| Size | matchbook-softball | 1.3m, 25kg | basketball, 6kg | 5ft, 60lb | 2m, 60kg |
| Mission | Dark vessel detection (mesh) | Ocean profile | Wave/weather | Surveillance | Multi-mission |
| Navigation | LNS8 PF, fleet-relative | GPS only | GPS | GPS | GPS |
| Propulsion | Near-passive | Passive | Passive | Sail | Sail |
| Comms | LoRa mesh + Iridium (anchors) | Iridium | Iridium | ? | Iridium |
| Fleet-scale | 1000s per deployment | 4000 globally | ~5000 globally | ~few | 14 |
| Novel element | Mesh + acoustic + FPGA PF | Scale, simplicity | Cost, wave measurement | Passive wing | Sailbuoy endurance |

## Next Steps

1. **Acoustic detection feasibility**: Python simulation of single-element vs
   array detection for small vessel signatures. Verify 10-20 km detection
   range for 5m skiff with 3-node array.

2. **Scenario generator** (gen_maritime_scenario.py): 15-21D state, intermittent
   GPS (anchor only), LoRa ranging observations, shear-keeper dynamics.
   Verify LNS8 accuracy at 20+ dimensions.

3. **LoRa TOA ranging accuracy**: Field test or simulation of SX1262 ranging
   at typical drifter separations (5-10 km). What does "10-50m range accuracy"
   translate to for position estimation?

4. **Shear estimation validation**: Can the PF actually estimate multi-depth
   current from observed drift and ballast cycles? Simulation study.

5. **Station-keeping policy**: When to ascend, when to dive. PF-informed
   control law. Energy budget for ballast cycles vs drift avoidance.

6. **Use-case discovery**: Which mission drives the requirements — EEZ
   enforcement, MPA monitoring, or distributed naval sensing? The answer
   sets detection-range, persistence, and evidentiary targets.

## References

- Global Fishing Watch: https://globalfishingwatch.org/
- Paolo et al. *Nature* 2024 (75% fishing vessels untracked): https://www.nature.com/articles/s41586-023-06825-8
- Argo program: https://argo.ucsd.edu/
- Sofar Spotter: https://www.sofarocean.com/products/spotter
- Sailbuoy: https://sailbuoy.no/
- SubSeaSail: https://subseasail.com/
- ICEYE SAR: https://www.iceye.com/sar-data
- Capella Space: https://www.capellaspace.com/
- HawkEye 360 RF: https://www.he360.com/
- Small vessel acoustic signatures: https://www.sciencedirect.com/science/article/pii/S0029801824013787
- LoRa SX1262 ranging: https://www.semtech.com/products/wireless-rf/lora-connect/sx1262
- Iridium SBD power: https://docs.groundcontrol.com/iot/rockblock/user-manual/9603-power-consumption
- IUU fishing stats (NOAA): https://www.fisheries.noaa.gov/insight/understanding-illegal-unreported-and-unregulated-fishing
