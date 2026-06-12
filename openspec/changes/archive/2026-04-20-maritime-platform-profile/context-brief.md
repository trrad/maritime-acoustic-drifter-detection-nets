# Context Brief: maritime-platform-profile

## Purpose
Make platform capability envelopes first-class typed data so downstream maritime changes consume a single source of truth, and so capability breaches (GPS on drifters, sensor over-rate, PF cycle overruns) become typed errors instead of prose guidelines. Delivers the "skeuomorphic node" forward contract from the integrity charter (Levels 1 and 3).

## Key Decisions
- Stdlib frozen dataclasses (`@dataclass(frozen=True, slots=True)`), no pydantic/attrs — immutability + equality for free; avoids silent coercion.
- Profile numbers sourced from `docs/maritime_buoy_design.md`; doc cited in module docstring; cross-reference spot-checks in tests.
- `CapabilityViolation(Exception)` lives in this module but is never raised here — reserved for downstream runtime checks. Construction-time integrity uses `ValueError`.
- Profiles are pure data — no methods that touch `CurrentField` / `RegionalMap`. Keeps Generator/Engine separation clean.
- `ComputeBudget` uses cycles-per-step as the primitive (matches 6D POC reporting); invariant is `cycles_per_step × pf_update_rate_hz ≤ clock_mhz × 1e6 × headroom`.
- Sensor names are plain strings from a documented vocabulary (`"gps"`, `"imu"`, `"baro"`, `"mag"`, `"hydrophone"`, `"lora_toa"`, `"bathy_probe"`) — avoids circular imports with `maritime-sensors`.
- `CommsProfile` includes `packet_loss_rate: float` (0 ≤ rate ≤ 1) for honest M1 LoRa drop modeling — drop rate is a comms-envelope property, not sensor logic.
- Active ballast pump presence (`has_pump: bool`) discriminates ballast-capable vs. pure-drifter classes; `ballast_capacity_ml` is only meaningful when `has_pump=True` (invariant enforced in `__post_init__`). Anchor-only discrete tiers are modeled as `is_moored: bool` and `has_satellite_uplink: bool`. Bundled profiles renamed accordingly: `ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE` (replaces `SHEAR_KEEPER_PROFILE`), `PURE_DRIFTER_PROFILE` (replaces `DRIFTER_PROFILE`).

## Tasks
1. SensorSpec — Tests ✓
2. SensorSpec — Implementation ✓
3. CommsProfile — Tests ✓
4. CommsProfile — Implementation ✓
5. ComputeBudget — Tests ✓
6. ComputeBudget — Implementation ✓
7. NodeProfile — Tests ✓
8. NodeProfile — Implementation ✓
9. Bundled M1 Profiles — Tests ✓
10. Bundled M1 Profiles — Implementation ✓
11. CapabilityViolation — Tests ✓
12. CapabilityViolation — Implementation ✓
13. Verification ✓

## Implementation Notes
- All 38 tests pass, frozen baseline intact, clean import verified.
- `openspec validate maritime-platform-profile --strict` passes.
- Module deduped (parallel subagents created duplicate class defs — merged).
- Profile constants: ANCHOR_PROFILE (25D, GPS, moored, satellite), BALLAST_DRIFTER_PROFILE (21D, pump, 50mL), PURE_DRIFTER_PROFILE (15D, surface-only).
- Shared `_LORA_COMMS` profile used by all three nodes (50ms slot, 1h TDMA, 10km range, 20m sigma, 10% loss).
- Ready for verify + archive.

## Files Affected
- `rtl/vectors/maritime/platform_profile.py` (new)
- `tests/maritime/test_platform_profile.py` (new)

## Spec Pointers
maritime-platform-profile → Requirement: Sensor Capability Envelope, Requirement: Comms Capability Envelope, Requirement: Compute Budget Fits Clock and Update Rate, Requirement: Node Profile Composes Capabilities, Requirement: Bundled M1 Fleet Profiles, Requirement: Capability Violation Exception
openspec/changes/maritime-platform-profile/specs/maritime-platform-profile/spec.md
