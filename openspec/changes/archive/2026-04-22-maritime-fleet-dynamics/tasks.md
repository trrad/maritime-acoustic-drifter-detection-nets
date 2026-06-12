## 1. ComponentSpec Protocol — Tests

- [x] 1.1 A frozen dataclass with `kind: ClassVar[str]` and `avg_power_mw: float` satisfies `isinstance(spec, ComponentSpec)` — protocol is runtime_checkable and conforms structurally
      (tests/maritime/test_platform_profile.py)
- [x] 1.2 A class lacking either `kind` or `avg_power_mw` fails the `isinstance` check — protocol enforces both attributes
      (tests/maritime/test_platform_profile.py)

## 2. ComponentSpec Protocol — Implementation

- [x] 2.1 Add `ComponentSpec` as a `runtime_checkable` `typing.Protocol` to `platform_profile.py` with `kind: str` and `avg_power_mw: float`
      (rtl/vectors/maritime/platform_profile.py)

## 3. M1 Physics Component Specs — Tests

- [x] 3.1 `MooredPoseSpec(anchor_lat_deg, anchor_lon_deg, anchor_depth_m)` constructs and exposes `kind == "moored_pose"`; all instances conform to `ComponentSpec`
      (tests/maritime/test_fleet.py)
- [x] 3.2 `DriftingSurfacePoseSpec` and `BallastDriftingPoseSpec` construct with no extra parameters; both conform to `ComponentSpec`
      (tests/maritime/test_fleet.py)
- [x] 3.3 `BallastSpec(capacity_ml, pump_rate_ml_per_s, avg_power_mw)` constructs; rejects `capacity_ml <= 0`, `pump_rate_ml_per_s <= 0`, `avg_power_mw < 0`
      (tests/maritime/test_fleet.py)
- [x] 3.4 `SatelliteUplinkSpec(duty_cycle, avg_power_mw)` constructs; rejects `duty_cycle` outside `[0, 1]` and `avg_power_mw < 0`
      (tests/maritime/test_fleet.py)
- [x] 3.5 All five M1 spec types are immutable — mutation raises `FrozenInstanceError`
      (tests/maritime/test_fleet.py)

## 4. M1 Physics Component Specs — Implementation

- [x] 4.1 Define `MooredPoseSpec`, `DriftingSurfacePoseSpec`, `BallastDriftingPoseSpec`, `BallastSpec`, `SatelliteUplinkSpec` as frozen dataclasses with `kind: ClassVar[str]`
      (rtl/vectors/maritime/fleet.py)
- [x] 4.2 `__post_init__` on each spec enforces construction invariants
      (rtl/vectors/maritime/fleet.py)

## 5. NodeProfile Modification — Tests

- [x] 5.1 `NodeProfile` no longer accepts `has_pump`, `is_moored`, `has_satellite_uplink`, or `ballast_capacity_ml` keyword arguments — passing any raises `TypeError`
      (tests/maritime/test_platform_profile.py)
- [x] 5.2 Constructed profile has no `has_pump` / `is_moored` / `has_satellite_uplink` / `ballast_capacity_ml` attribute — `AttributeError` on access
      (tests/maritime/test_platform_profile.py)
- [x] 5.3 `NodeProfile` accepts `components: tuple[ComponentSpec, ...]` and the profile exposes it as an immutable tuple
      (tests/maritime/test_platform_profile.py)
- [x] 5.4 Duplicate component kinds in the components tuple raise `ValueError` at construction
      (tests/maritime/test_platform_profile.py)
- [x] 5.5 Component `avg_power_mw` sum counts toward `total_avg_power_mw`; overshoot of `total_power_budget_mw` rejected
      (tests/maritime/test_platform_profile.py)
- [x] 5.6 `profile.component(kind)` returns the matching spec; `KeyError` for unknown kind
      (tests/maritime/test_platform_profile.py)

## 6. NodeProfile Modification — Implementation

- [x] 6.1 Remove `has_pump`, `is_moored`, `has_satellite_uplink`, `ballast_capacity_ml` fields from `NodeProfile`
      (rtl/vectors/maritime/platform_profile.py)
- [x] 6.2 Add `components: tuple[ComponentSpec, ...]` field with `__post_init__` validation (unique kinds, power sum)
      (rtl/vectors/maritime/platform_profile.py)
- [x] 6.3 Add `component(kind)` accessor mirroring `sensor(name)`
      (rtl/vectors/maritime/platform_profile.py)

## 7. Bundled M1 Profile Updates — Tests

- [x] 7.1 State dims match design: anchor 25, ballast drifter 21, pure drifter 15
      (tests/maritime/test_platform_profile.py)
- [x] 7.2 `ANCHOR_PROFILE.components` contains exactly one `moored_pose` and one `satellite_uplink`; no `ballast_pump`, no `drifting_surface_pose`, no `ballast_drifting_pose`
      (tests/maritime/test_platform_profile.py)
- [x] 7.3 `BALLAST_DRIFTER_PROFILE.components` contains exactly one `ballast_pump` and one `ballast_drifting_pose`; no `moored_pose`, no `satellite_uplink`, no `drifting_surface_pose`
      (tests/maritime/test_platform_profile.py)
- [x] 7.4 `PURE_DRIFTER_PROFILE.components` contains exactly one `drifting_surface_pose` and no other M1 physics kinds
      (tests/maritime/test_platform_profile.py)
- [x] 7.5 Sensors: anchor has `"gps"`; ballast drifter and pure drifter do not
      (tests/maritime/test_platform_profile.py)
- [x] 7.6 Power budgets: pure ≤ 2 mW, ballast ≤ 5 mW, anchor ≤ 50 mW; each profile's total ≤ budget
      (tests/maritime/test_platform_profile.py)

## 8. Bundled M1 Profile Updates — Implementation

- [x] 8.1 Rewrite `ANCHOR_PROFILE` constant with explicit components tuple including `MooredPoseSpec(...)` and `SatelliteUplinkSpec(...)`
      (rtl/vectors/maritime/platform_profile.py)
- [x] 8.2 Rewrite `BALLAST_DRIFTER_PROFILE` constant with components tuple including `BallastDriftingPoseSpec()` and `BallastSpec(capacity_ml=<design value>, pump_rate_ml_per_s=<design value>, avg_power_mw=<within budget>)`
      (rtl/vectors/maritime/platform_profile.py)
- [x] 8.3 Rewrite `PURE_DRIFTER_PROFILE` constant with components tuple containing only `DriftingSurfacePoseSpec()`
      (rtl/vectors/maritime/platform_profile.py)

## 9. StateField — Tests

- [x] 9.1 Valid `StateField(name, unit, description)` constructs; is immutable; round-trips
      (tests/maritime/test_state_layout.py)
- [x] 9.2 Hashability — two `StateField` values with identical fields compare equal and share a hash
      (tests/maritime/test_state_layout.py)

## 10. StateField — Implementation

- [x] 10.1 `StateField` frozen dataclass with `name: str`, `unit: str`, `description: str`
      (rtl/vectors/maritime/state_layout.py)

## 11. StateLayout — Tests

- [x] 11.1 `state_dim` equals the length of the fields tuple
      (tests/maritime/test_state_layout.py)
- [x] 11.2 Duplicate field names rejected at construction
      (tests/maritime/test_state_layout.py)
- [x] 11.3 Group slice outside state range rejected
      (tests/maritime/test_state_layout.py)
- [x] 11.4 `index_of` returns correct position; raises `KeyError` for unknown names
      (tests/maritime/test_state_layout.py)
- [x] 11.5 `name_at` returns correct name; raises `IndexError` for out-of-range indices
      (tests/maritime/test_state_layout.py)
- [x] 11.6 `slice` returns correct `slice` object; raises `KeyError` for unknown group
      (tests/maritime/test_state_layout.py)

## 12. StateLayout — Implementation

- [x] 12.1 `StateLayout` frozen dataclass with `class_name`, `fields`, `groups`; `__post_init__` enforces invariants; accessors for `state_dim`, `index_of`, `name_at`, `slice`
      (rtl/vectors/maritime/state_layout.py)

## 13. Bundled M1 Layouts — Tests

- [x] 13.1 Layout dims match profile dims: `PURE_DRIFTER_LAYOUT.state_dim == 15`, ballast 21, anchor 25
      (tests/maritime/test_state_layout.py)
- [x] 13.2 Position group always at `slice(0, 3)` across all three bundled layouts
      (tests/maritime/test_state_layout.py)
- [x] 13.3 Heading always at index 6 across all three bundled layouts
      (tests/maritime/test_state_layout.py)
- [x] 13.4 IMU bias group has 6 fields in every bundled layout
      (tests/maritime/test_state_layout.py)
- [x] 13.5 Ballast drifter has `deep_current` group (2 fields); pure drifter does not (raises `KeyError`)
      (tests/maritime/test_state_layout.py)
- [x] 13.6 Ballast drifter has 4 `neighbor_range` slots; anchor has 8
      (tests/maritime/test_state_layout.py)
- [x] 13.7 Unit conventions: position `"m"`, velocity `"m/s"`, heading `"deg"`, neighbor_range `"m"`, gyro bias `"deg/s"`, accel bias `"m/s^2"`
      (tests/maritime/test_state_layout.py)

## 14. Bundled M1 Layouts — Implementation

- [x] 14.1 `PURE_DRIFTER_LAYOUT` (15 fields) with groups position / velocity / heading / surface_current / imu_bias
      (rtl/vectors/maritime/state_layout.py)
- [x] 14.2 `BALLAST_DRIFTER_LAYOUT` (21 fields) extending pure-drifter with deep_current (2) and neighbor_range (4)
      (rtl/vectors/maritime/state_layout.py)
- [x] 14.3 `ANCHOR_LAYOUT` (25 fields) extending ballast-drifter with 4 more neighbor_range slots (8 total)
      (rtl/vectors/maritime/state_layout.py)
- [x] 14.4 `ALL_M1_LAYOUTS` tuple aggregates all three
      (rtl/vectors/maritime/state_layout.py)

## 15. Composed Node — Tests

- [x] 15.1 Valid `Node(node_id, profile, layout, state, components)` constructs; is immutable; round-trips; `state` and `components` exposed
      (tests/maritime/test_fleet.py)
- [x] 15.2 State-shape mismatch rejected — state shape `(N,)` with `layout.state_dim != N` raises `ValueError`
      (tests/maritime/test_fleet.py)
- [x] 15.3 Profile/layout `state_dim` mismatch rejected
      (tests/maritime/test_fleet.py)
- [x] 15.4 State containing NaN or infinite values rejected at construction
      (tests/maritime/test_fleet.py)
- [x] 15.5 Component mapping key not in `profile.components` kinds is rejected — can't attach runtime component for an undeclared spec
      (tests/maritime/test_fleet.py)

## 16. Composed Node — Implementation

- [x] 16.1 `Node` frozen dataclass with `node_id`, `profile`, `layout`, `state`, `components`; `__post_init__` enforces all invariants
      (rtl/vectors/maritime/fleet.py)

## 17. Blueprint Factories — Tests

- [x] 17.1 `make_anchor(ANCHOR_PROFILE, initial_state, rng)` returns a `Node` with `is_moored == True`, `has_satellite_uplink == True`, `has_pump == False`
      (tests/maritime/test_fleet.py)
- [x] 17.2 `make_anchor` rejects a profile missing `moored_pose` with `ValueError`
      (tests/maritime/test_fleet.py)
- [x] 17.3 `make_anchor` rejects a profile missing `satellite_uplink` with `ValueError`
      (tests/maritime/test_fleet.py)
- [x] 17.4 `make_ballast_drifter(BALLAST_DRIFTER_PROFILE, ...)` returns a `Node` with `has_pump == True`, `is_moored == False`
      (tests/maritime/test_fleet.py)
- [x] 17.5 `make_ballast_drifter` rejects profiles with `moored_pose` or `satellite_uplink` components
      (tests/maritime/test_fleet.py)
- [x] 17.6 `make_pure_drifter(PURE_DRIFTER_PROFILE, ...)` returns a `Node` with all three helper queries returning `False`
      (tests/maritime/test_fleet.py)
- [x] 17.7 `make_pure_drifter` rejects profiles containing `ballast_pump`, `moored_pose`, or `satellite_uplink`
      (tests/maritime/test_fleet.py)

## 18. Blueprint Factories — Implementation

- [x] 18.1 `make_anchor(profile, initial_state, rng)` — validates profile has required components; instantiates runtime components; returns `Node`
      (rtl/vectors/maritime/fleet.py)
- [x] 18.2 `make_ballast_drifter(profile, initial_state, rng)` — similar, enforces ballast-drifter component set
      (rtl/vectors/maritime/fleet.py)
- [x] 18.3 `make_pure_drifter(profile, initial_state, rng)` — similar, enforces pure-drifter component set
      (rtl/vectors/maritime/fleet.py)

## 19. Utility Helpers — Tests

- [x] 19.1 `has_pump(node)` returns `True` iff `"ballast_pump" in node.components`
      (tests/maritime/test_fleet.py)
- [x] 19.2 `is_moored(node)` returns `True` iff `"moored_pose" in node.components`
      (tests/maritime/test_fleet.py)
- [x] 19.3 `has_satellite_uplink(node)` returns `True` iff `"satellite_uplink" in node.components`
      (tests/maritime/test_fleet.py)
- [x] 19.4 For each blueprint factory, all three helpers return the expected values
      (tests/maritime/test_fleet.py)

## 20. Utility Helpers — Implementation

- [x] 20.1 Module-level `has_pump`, `is_moored`, `has_satellite_uplink` functions; use `KIND_*` module-constant strings to avoid typos
      (rtl/vectors/maritime/fleet.py)

## 21. Truth Propagation — Tests

- [x] 21.1 `propagate_truth(node, dt_sec, env, rng)` is deterministic — two calls with identical inputs + identically-seeded RNGs return element-wise equal states
      (tests/maritime/test_dynamics.py)
- [x] 21.2 Input node's state array is byte-identical after the call (no mutation)
      (tests/maritime/test_dynamics.py)
- [x] 21.3 Output shape matches input shape
      (tests/maritime/test_dynamics.py)
- [x] 21.4 Output contains no NaN or infinite values
      (tests/maritime/test_dynamics.py)
- [x] 21.5 Phase order is observable — a node with a pump component whose effect would change pose (M2 extension) shows pump result visible to pose phase, not vice versa (for M1 this is checked via a test-only phase tracer that records phase execution order per tick)
      (tests/maritime/test_dynamics.py)
- [x] 21.6 Moored anchor position unchanged in nonzero current
      (tests/maritime/test_dynamics.py)
- [x] 21.7 Moored anchor velocity remains zero across ticks
      (tests/maritime/test_dynamics.py)
- [x] 21.8 Anchor heading still evolves under process noise over 100 ticks
      (tests/maritime/test_dynamics.py)
- [x] 21.9 Pure drifter depth locked at zero — 60 s at 1 Hz with nonzero `vz` returns `state[2] == 0.0` every tick
      (tests/maritime/test_dynamics.py)
- [x] 21.10 Pure drifter east/north still advect in constant current
      (tests/maritime/test_dynamics.py)
- [x] 21.11 Ballast-drifting zero-noise advection — constant current `(0.1, 0)` for 60 s produces east displacement `6.0 m ± 0.1`
      (tests/maritime/test_dynamics.py)
- [x] 21.12 Ballast-drifting advection respects current direction — `(0, 0.2)` for 30 s produces north `6.0 m ± 0.1` and east unchanged
      (tests/maritime/test_dynamics.py)
- [x] 21.13 M1 ballast depth is unchanged between ticks (pump is a no-op)
      (tests/maritime/test_dynamics.py)
- [x] 21.14 IMU bias random walk — 1000 ticks of 1 s from zero initial bias gives per-dim std in `[0.5×, 2×]` of expected `noise_per_sqrt_s × sqrt(1000)`
      (tests/maritime/test_dynamics.py)
- [x] 21.15 IMU bias is not clipped — biases grow freely without hidden clamp
      (tests/maritime/test_dynamics.py)
- [x] 21.16 Heading wraps to `[0, 360)` after multiple revolutions driven by gyro bias
      (tests/maritime/test_dynamics.py)

## 22. Truth Propagation — Implementation

- [x] 22.1 Module-level kind-constants: `KIND_BALLAST_PUMP`, `KIND_MOORED_POSE`, `KIND_DRIFTING_SURFACE_POSE`, `KIND_BALLAST_DRIFTING_POSE`, `KIND_CLOCK`
      (rtl/vectors/maritime/dynamics.py)
- [x] 22.2 Module-level process-noise constants (`POS_PROCESS_NOISE_M_PER_SQRT_S` etc.)
      (rtl/vectors/maritime/dynamics.py)
- [x] 22.3 `PhysicsEnv` frozen dataclass bundling `current_field` and `t_sec`
      (rtl/vectors/maritime/dynamics.py)
- [x] 22.4 `propagate_truth(node, dt_sec, env, rng)` — 4-phase walk (pump → pose → imu_biases → clock) with component dispatch; pure w.r.t. state
      (rtl/vectors/maritime/dynamics.py)

## 23. M1 Fleet Factory — Tests

- [x] 23.1 Composition — `make_m1_fleet(42, bbox)` returns 10 nodes with 2 moored, 4 pumped, 4 neither
      (tests/maritime/test_fleet.py)
- [x] 23.2 Determinism — two calls with identical args produce byte-identical node IDs, profiles, layouts, initial states
      (tests/maritime/test_fleet.py)
- [x] 23.3 Different seed produces different drifter positions but identical anchor positions
      (tests/maritime/test_fleet.py)
- [x] 23.4 All positions strictly inside bbox
      (tests/maritime/test_fleet.py)
- [x] 23.5 All 10 node IDs distinct
      (tests/maritime/test_fleet.py)

## 24. M1 Fleet Factory — Implementation

- [x] 24.1 `make_m1_fleet(seed, bbox)` dispatches to blueprint factories: 2 anchors at fixed bbox-relative corners, 4 ballast drifters and 4 pure drifters at seed-driven uniform-random positions strictly inside bbox; no coastline-aware rejection (that lives in `maritime-scenario-gen`)
      (rtl/vectors/maritime/fleet.py)

## 25. Verification

- [x] 25.1 `uv run pytest tests/maritime/test_platform_profile.py tests/maritime/test_state_layout.py tests/maritime/test_fleet.py tests/maritime/test_dynamics.py` passes with zero failures
- [x] 25.2 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and pre-existing `rtl/vectors/*.py` files
- [x] 25.3 Module imports cleanly — `uv run python -c "from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT, BALLAST_DRIFTER_LAYOUT, ANCHOR_LAYOUT; from rtl.vectors.maritime.fleet import Node, make_anchor, make_ballast_drifter, make_pure_drifter, make_m1_fleet, has_pump, is_moored, has_satellite_uplink, BallastSpec, MooredPoseSpec, SatelliteUplinkSpec, DriftingSurfacePoseSpec, BallastDriftingPoseSpec; from rtl.vectors.maritime.dynamics import propagate_truth, PhysicsEnv; from rtl.vectors.maritime.platform_profile import ComponentSpec, NodeProfile"` exits 0
- [x] 25.4 `openspec validate maritime-fleet-dynamics --strict` passes
- [x] 25.5 Cross-consistency — for each bundled layout/profile pair, `layout.state_dim == profile.state_dim`
- [x] 25.6 Flag removal sanity — `getattr(PURE_DRIFTER_PROFILE, "has_pump", None)` is `None` (no such attribute), same for all dropped flags across all three bundled profiles
