# Context Brief: maritime-fleet-dynamics

## Purpose

Deliver composition-based node identity, canonical state-vector layout, and
Level 0 truth propagation. Replaces the three-subclass design with a single
composed `Node` type; replaces profile boolean flags with component
presence. Capabilities are intrinsic because components are intrinsic.

## Key Decisions

- **3-spec delta**: `maritime-state-layout` (ADDED), `maritime-fleet-dynamics`
  (ADDED), `maritime-platform-profile` (MODIFIED — drops `has_pump`,
  `is_moored`, `has_satellite_uplink`, `ballast_capacity_ml`; adds
  `ComponentSpec` protocol and `components: tuple[ComponentSpec, ...]`).
- **Composition over inheritance.** One `Node` type; no subclasses. Three
  blueprint factories (`make_anchor`, `make_ballast_drifter`,
  `make_pure_drifter`) assemble the right component set. Utility helpers
  (`has_pump`, `is_moored`, `has_satellite_uplink`) read component presence
  as ground truth.
- **`ComponentSpec` protocol** (`kind: str`, `avg_power_mw: float`) is the
  single uniform field on the profile for all physics/hardware components.
  Adding a new component type = new frozen dataclass; no profile structural
  change.
- **M1 physics component specs** live in `platform_profile.py` (moved from
  fleet.py to avoid circular import — fleet.py re-exports them):
  `MooredPoseSpec`, `DriftingSurfacePoseSpec`, `BallastDriftingPoseSpec`,
  `BallastSpec` (carries `capacity_ml`, `pump_rate_ml_per_s`),
  `SatelliteUplinkSpec`.
- **Fixed 4-phase tick order** in `propagate_truth`: pump → pose →
  imu_biases → clock. Pose merges position+heading deliberately (drogue
  shear couples them). Dispatch on component presence per phase. Pure
  function w.r.t. state.
- **State layout** unchanged from prior design: `StateField` + `StateLayout`
  with named groups; heading always index 6; position always `slice(0, 3)`;
  neighbor-range slots carry NaN sentinels when unused.
- **M1 fleet factory** places anchors at fixed bbox-relative corners and
  drifters via seed-driven uniform random inside the bbox. No coastline
  rejection (that lives in `maritime-scenario-gen`). Earlier draft's
  "2 km margin" heuristic was dropped — arbitrary threshold, didn't
  actually guarantee offshore.
- **Component power values** chosen: SatelliteUplinkSpec avg_power_mw=15.0
  (anchor budget 50 mW, usage ~23.7 mW), BallastSpec avg_power_mw=2.0
  (ballast budget 5 mW, usage ~2.4 mW), MooredPoseSpec/DriftingSurfacePoseSpec/
  BallastDriftingPoseSpec avg_power_mw=0.0 (passive).

## Tasks

1. ComponentSpec Protocol — Tests ✓
2. ComponentSpec Protocol — Implementation ✓
3. M1 Physics Component Specs — Tests ✓
4. M1 Physics Component Specs — Implementation ✓
5. NodeProfile Modification — Tests ✓
6. NodeProfile Modification — Implementation ✓
7. Bundled M1 Profile Updates — Tests ✓
8. Bundled M1 Profile Updates — Implementation ✓
9. StateField — Tests ✓
10. StateField — Implementation ✓
11. StateLayout — Tests ✓
12. StateLayout — Implementation ✓
13. Bundled M1 Layouts — Tests ✓
14. Bundled M1 Layouts — Implementation ✓
15. Composed Node — Tests ✓
16. Composed Node — Implementation ✓
17. Blueprint Factories — Tests ✓
18. Blueprint Factories — Implementation ✓
19. Utility Helpers — Tests ✓
20. Utility Helpers — Implementation ✓
21. Truth Propagation — Tests ✓
22. Truth Propagation — Implementation ✓
23. M1 Fleet Factory — Tests ✓
24. M1 Fleet Factory — Implementation ✓
25. Verification

## Files Affected

- `rtl/vectors/maritime/state_layout.py` (new)
- `rtl/vectors/maritime/fleet.py` (new)
- `rtl/vectors/maritime/dynamics.py` (new)
- `rtl/vectors/maritime/platform_profile.py` (MODIFIED — drop flags, add
  `ComponentSpec` + `components` field, rewrite bundled profile constants)
- `tests/maritime/test_state_layout.py` (new)
- `tests/maritime/test_fleet.py` (new)
- `tests/maritime/test_dynamics.py` (new)
- `tests/maritime/test_platform_profile.py` (new — or extends existing if
  present)

## Spec Pointers

- `maritime-state-layout` → Requirement: State Field Descriptor, Requirement:
  State Layout Structure, Requirement: State Layout Accessors, Requirement:
  Bundled M1 Layouts, Requirement: Layout Unit Labels
  openspec/changes/maritime-fleet-dynamics/specs/maritime-state-layout/spec.md
- `maritime-fleet-dynamics` → Requirement: M1 Physics Component Specs,
  Requirement: Composed Node Type, Requirement: Blueprint Factories,
  Requirement: Capability Utility Helpers, Requirement: Fixed 4-Phase Tick
  Ordering, Requirement: Moored Nodes Do Not Advect, Requirement: Pure
  Drifters Stay on the Surface, Requirement: Ballast-Drifting Nodes Advect
  Horizontally, Requirement: IMU Bias Random Walk, Requirement: Heading
  Wrapping, Requirement: M1 Fleet Factory
  openspec/changes/maritime-fleet-dynamics/specs/maritime-fleet-dynamics/spec.md
- `maritime-platform-profile` (MODIFIED) → ADDED Requirement: Component Spec
  Protocol; MODIFIED Requirement: Node Profile Composes Capabilities;
  MODIFIED Requirement: Bundled M1 Fleet Profiles
  openspec/changes/maritime-fleet-dynamics/specs/maritime-platform-profile/spec.md
