# Project overview: vision and design

*Public-facing summary. For current state see [status.md](status.md); for the
running findings log see
[experiments/harmonic_prototype/FINDINGS.md](../experiments/harmonic_prototype/FINDINGS.md).*

## The problem

Small dark vessels — sub-15 m, AIS off, often silent-running — are
effectively invisible to coastal surveillance. Patrol vessels are expensive
and sparse; satellite passes are intermittent and SAR has a practical
detection floor near this size class; fixed seabed arrays are costly to
install and can't follow activity; powered uncrewed vessels and gliders
cost too much per unit to field in numbers. The gap matters for IUU
fishing, marine-protected-area enforcement, and remote-coast monitoring.

## The bet

Instead of a few expensive mobile assets, use **many near-passive ones**: a
fleet of small drifting buoys, each carrying a hydrophone, a LoRa radio, a
ballast pump, and a microcontroller-class brain. The design trades
propulsion for physics and accepts attrition:

- **Steering without thrust.** Coastal currents differ in direction and
  phase by depth (in the Strait of Georgia the M2 tide lags ~33° between
  surface and 24 m). A ballast pump that changes the drifter's depth picks
  which current it rides. That buys *loose* station-keeping — kilometers,
  not meters — for days at a time.
- **Listening is cheap; localizing is a fleet property.** An always-on
  acoustic envelope detector runs at ~100 µW. When ≥3 drifters hear the same
  event, time-difference-of-arrival (TDOA) triangulation localizes it — if
  each drifter knows where it was at event time.
- **Navigation accuracy is needed retroactively, not live.** A drifter
  doesn't need to know where it is *now*; it needs to know where it *was*
  when it heard something. So the stack runs a particle filter forward
  (LoRa ranging to a few fixed anchor buoys, CTD salinity as a water-mass
  position cue, sparse GPS)
  and an RTS smoother backward from the next surfacing fix. This converts a
  power problem (surface often to stay located) into a scheduling problem
  (surface *shortly after* hearing something).
- **Coverage is a planning output, not a hardware property.** Drifters
  drift. The fleet layer — drop-point optimization against empirical
  mobility maps, surfacing policies, periodic redeployment — is what turns
  drifting sensors into a coverage guarantee. The headline result so far:
  event-driven surfacing plus a 72-hour redeploy cycle dominates fixed
  schedules on coverage, localization error, time-to-detect, *and* power.

## System design

Per node (simulated; the hardware vision is microcontroller-class, ~$100s
per unit):

```
hydrophone → 100 µW envelope detector → wake classifier (~5–10 mW burst)
ballast pump ← MPC depth controller ← bias-Kalman current estimate
                                     ← particle filter ← LoRa TOA ranging,
                                                          baro depth, CTD salinity,
                                                          sparse GPS
surfacing policy: event-driven (30 min post-detection) with a 12 h safety cap
LoRa mesh: 1-hour TDMA frames, TOA/RSSI ranging, ~220 µW average
```

Fleet layer:

- 3–4 **fixed anchor buoys** (GPS-equipped, ~20 km spacing) give the LoRa
  ranging a georeferenced backbone.
- A **drop-point optimizer** (greedy placement + local refinement) maximizes
  expected localization coverage over a 72 h horizon, using per-site
  mobility statistics from the simulated ocean.
- **Redeployment triggers** flag drifters that leave the zone or whose
  position confidence stays degraded, with periodic replacement drops
  re-optimized for the remaining fleet.

## Why simulation-first

All current work runs against **SalishSeaCast** (NEMO 3.6 reanalysis,
0.5 km grid, hourly, 40 depth levels) as ground truth — real ocean structure,
not synthetic currents. The simulation honesty rules are written down in
[simulation_integrity.md](simulation_integrity.md): truth state and belief
state are physically separated modules, estimator code cannot import the
truth field, and "deployment-honest" metrics (forward-filter only) are
reported separately from retrospective (smoothed) ones.

## The compute thread: the LNS8 FPGA engine

The estimator stack above — particle filter, Kalman update, MPC scoring — is
the drifter's main compute load, and it has to fit a solar/coin-cell power
budget. The project's second workstream — historically its first — attacks
that. It began with the **EML operator** `eml(x, y) = exp(x) − ln(y)`
(Odrzywołek 2026, [arXiv:2603.21852](https://arxiv.org/abs/2603.21852)), a
single primitive that generates all elementary functions; hunting for a
practical use for it led to logarithmic-number-system arithmetic, where exp
and ln are table lookups. The operator itself turned out to add nothing over
plain LNS ops — an honest negative result — but the LNS engine it motivated
is real: `rtl/` holds an 8-bit ALU and a 6-D, 128-particle filter on an
iCE40UP5K (~2100 LUTs, 304 bytes of tables, ~0.1–1.6 mW depending on clock),
verified against a cycle-accurate Python reference.

The two workstreams gate each other deliberately: the FPGA design is paused
until the fleet simulations pin down what the onboard estimator actually
needs (particle count, sensor mix, precision, rate), so the next hardware
iteration solves a measured problem rather than a guessed one.

## Design principles

The repo's collaboration rules live in [AGENTS.md](../AGENTS.md); the short
version that shapes the code you'll see:

- **Enforcement over instruction** — invariants live in types, import-linter
  contracts, and tests, not in prose conventions.
- **Skeleton before spec chain** — multi-module pipelines get a runnable
  end-to-end stub before specs harden around them.
- **No unprincipled thresholds** — numeric targets in specs must trace to a
  measurement or an operational requirement.
- **Frozen baselines** — published experiment scripts are immutable; new
  work goes in new files.
