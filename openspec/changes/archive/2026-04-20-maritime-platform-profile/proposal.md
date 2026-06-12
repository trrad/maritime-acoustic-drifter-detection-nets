## Why

The maritime fleet has three node classes (anchor, ballast drifter, pure drifter) with intrinsically different capabilities — different sensors, update rates, power budgets, compute budgets, and comms patterns. Active ballast pump presence is the discriminator between the two non-anchor classes (ballast drifters can depth-cycle for station-keeping; pure drifters are surface-only). Today these numbers live as prose in `docs/maritime_buoy_design.md` and are re-derived ad-hoc by anyone writing scenario or PF code. That's exactly the "integrity by convention, not construction" failure mode the integrity charter warns against: a drifter simulation could cheerfully emit GPS fixes every second, or the PF could claim cycle counts the node can't actually afford, and nothing would fail.

This change makes platform capabilities first-class typed data. Downstream changes (`maritime-fleet-dynamics`, `maritime-sensors`, `maritime-scenario-gen`) consume a `NodeProfile` rather than re-encoding the same numbers. A capability violation (GPS on a drifter, sensor exceeding its duty cycle, PF exceeding its cycle budget) becomes a typed error, not a prose guideline.

## What Changes

- Introduce a `rtl/vectors/maritime/platform_profile.py` module defining:
  - `SensorSpec` — per-sensor capability envelope: name, observed state dim, noise sigma, max update rate, duty cycle, average power
  - `CommsProfile` — LoRa slot length, TDMA period, max range, per-packet bit budget, average power
  - `ComputeBudget` — FPGA clock MHz, cycles available per PF step, PF state dimensionality, average power
  - `NodeProfile` — frozen dataclass composing state dim, sensor list, comms profile, compute budget, total power budget, class name, plus discrete-tier capability flags (`has_pump`, `ballast_capacity_ml`, `is_moored`, `has_satellite_uplink`)
  - `CapabilityViolation` — exception raised when a consumer requests behavior outside the profile
- Three bundled profile constants for the M1 Monterey Bay fleet: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, `PURE_DRIFTER_PROFILE`. Numbers sourced from `docs/maritime_buoy_design.md` tables (state dim 25/21/15, sub-50/5/2 mW power envelopes, sensor duty cycles per class). Only the anchor is moored and satellite-uplinked; the ballast drifter is the only one with an active pump.
- Profile-level physical-plausibility tests: every sensor's average power fits in the node's total budget; sensor rates respect Nyquist vs. PF update rate; compute budget at the profile's clock meets the PF update rate.
- **No runtime behavior changes** to existing code. Profiles are consumed by later changes.

## Capabilities

### New Capabilities

- `maritime-platform-profile`: Platform capability envelope types (`SensorSpec`, `CommsProfile`, `ComputeBudget`, `NodeProfile`) + bundled anchor / ballast-drifter / pure-drifter profiles for Monterey Bay M1. `NodeProfile` carries discrete-tier capability flags (`has_pump`, `ballast_capacity_ml`, `is_moored`, `has_satellite_uplink`) so the pump vs. no-pump distinction is explicit in the type. Defines `CapabilityViolation` as the typed error for profile breaches. Does not own enforcement — downstream changes query profiles and raise on violation.

### Modified Capabilities

(none — no existing standing specs to modify)

## Impact

- **New files**: `rtl/vectors/maritime/platform_profile.py`, `tests/maritime/test_platform_profile.py`
- **Dependencies**: numpy only (already declared). No coupling to `maritime-geo`, `maritime-current-fields`, or `maritime-map-payload` — profiles are pure data.
- **Downstream consumers (later changes)**: `maritime-fleet-dynamics` builds node classes from profiles; `maritime-sensors` reads `SensorSpec` for duty-cycle enforcement; `maritime-scenario-gen` uses the profile list to configure the fleet; `maritime-pf-float` / `maritime-pf-lns8-delta` check their cycle counts against `ComputeBudget`.
- **Frozen baseline**: untouched. This change adds files only.
- **Simulation integrity charter**: delivers the "skeuomorphic node / capabilities are intrinsic" contract referenced in the charter's forward-contracts table (Level 1 sensor duty cycles, Level 3 compute budget, partial Level 2 via `CommsProfile`).
