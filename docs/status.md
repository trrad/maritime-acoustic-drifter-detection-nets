# Project status

*Last updated: 2026-06-12. This is the honest-accounting page: everything
below is simulation; no hardware has been built or deployed.*

## Active: maritime fleet planning stack

`experiments/harmonic_prototype/` — where current research velocity lives.

### Done (with findings)

- **Tidal harmonic prior** from SalishSeaCast + UTide: M2/S2/K1/O1 explain
  ~9–16 % of surface current variance (wind/freshet residual dominates);
  baroclinic structure is first-order — 33° M2 phase lag surface→24 m.
- **Station-keeping feasibility**: perfect-knowledge depth control holds a
  mid-basin station within 500 m ~36 % of the time (passive: 8 %);
  feasibility is strongly site-dependent (survey across 54 stations);
  retractable-drogue drag modulation roughly doubles the rough-station count.
- **Navigation stack**: particle filter + bias-Kalman current learning + MPC
  depth control + RTS smoothing, with LoRa ranging to shared fixed anchor
  buoys (realistic 20–100 m σ).
- **Acoustic feasibility**: detection-range model by target class and sea
  state; classifier-assisted detection extends a 15 m trawler from ~0.9 km to
  ~8.4 km; TDOA triangulation error budget vs. per-node position σ.
- **Science sweep v1** (64 cells: density × surfacing policy × ranging σ ×
  cadence): doubling fleet density 12→24 roughly doubles reconstruction
  count; σ_event is flat in ranging σ over 20–200 m.
- **Continuous-coverage campaign** ([findings](findings_campaign_2026-04-30.md)):
  event-driven surfacing × 72 h redeploy dominates every measured axis
  (coverage 0.349, 26 % reconstruction rate, 80 m median σ_event, ~600
  surfacings/week). Redeployment is *essential* under event-driven surfacing
  (+124 % coverage) and merely helpful under fixed schedules (+19 %).

### In flight

[Smart-redeploy v1](smart_redeploy_v1_status.md): SoG site-survey grid scan,
track-divergence surfacing semantics (replaces naive per-ping triggering),
and the shared fixed-anchor model have landed; per-cadence mobility maps for
the optimizer and the perfect-controller ceiling diagnostic are deferred.

### Known caveats

- Campaign policy comparisons share event RNG seeds within policy but not
  across policies — directionally robust, single-seed precision overstated.
- Not modeled: shallow-water multipath, biological/shipping false-alarm
  clutter, classifier false-positive rates, coherent beamforming, LoRa
  congestion at full fleet scale.
- All coverage numbers are for one patrol box in the Strait of Georgia with
  one event-rate regime.

## Dormant: EML / LNS8 FPGA workstream

Complete as a milestone, deliberately paused. Done: EML identities and
function-recovery experiments; cycle-accurate integer-only LNS8 reference;
full Verilog implementation (22 modules — ALU, microcoded PF sequencer,
resampler, estimator, SPRAM banks, SPI); per-module + end-to-end testbenches
against golden traces; iCE40UP5K synthesis (~2124 LUTs, 17 BRAMs; estimated
0.12 mW @ 1 MHz → 1.57 mW @ 30 MHz); delta-encoding fix for the large-
coordinate precision cliff validated in Python (1.2× float64 RMSE, 752 Hz
@ 50 MHz).

Not started: deploying the floating-point position-storage variant in RTL,
running on physical hardware, power measurement, any tapeout path.

Negative result, recorded honestly: the EML operator that started the
project never earned a place in the final design — the working primitive
set is plain LNS MUL/DIV/ADD/EXP/LN. The operator was the road in, not the
destination.

Why paused: the next design decisions (8 vs. 10-bit log magnitude, which
sensor models, what particle count) should come from measured demands of the
fleet simulations above, not guesses. No technical blockers.

## Paused: compositional simulation framework

`rtl/vectors/maritime/` + OpenSpec specs — the heavier-weight, well-tested
node/sensor framework. Intent is to fold the scripted prototype into it once
the active research questions stabilize; right now scripted iteration wins
on velocity.
