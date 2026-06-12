# Context Brief: maritime-clock-model

## Purpose
Deliver a `ComponentSpec`-conforming clock component so every M1 node
carries one at `node.components["clock"]`. Sensors read
`wall_time(t_sec)`; `propagate_truth` phase 4 calls `advance(dt_sec)`.
M1 ships `drift_ppm=0.0` on every bundled profile, which makes
`wall_time(t) == t` an emergent property — not a contract. M2 swaps
parameter values and adds sync-mechanism components; this module's
protocol is unchanged.

## Key Decisions
- Clock is a `ComponentSpec` (frozen `ClockSpec` with `kind="clock"`,
  `drift_ppm`, `avg_power_mw`), composed into `profile.components`
  like every other M1 physics component.
- Runtime `Clock` exposes exactly `advance(dt_sec)` + `wall_time(true_sec)`.
  No `offset_sec` / `drift_ppm` attributes on the public protocol —
  those live on the spec and on internal state respectively.
- Zero-offset M1 behavior is emergent from `drift_ppm=0.0`, not a named
  requirement. M2 non-zero drift produces non-identity wall-time with
  no protocol change.
- Blueprint factories (`make_anchor` etc.) instantiate the runtime
  clock from the profile's `ClockSpec`. No standalone `make_clock`
  factory, no `realistic=True/False` mode flag.
- Sync mechanisms (GPS-PPS, LoRa-TDMA frame) are separate
  `ComponentSpec` types that land in M2 — not included here.

## Tasks
1.1-1.4 ClockSpec construction + conformance + rejection tests
2.1 ClockSpec implementation
3.1-3.4 Clock.advance tests (zero drift, linear accumulation, negative-dt rejection)
4.1-4.4 Clock.wall_time tests (identity under zero drift, non-identity under drift, purity)
5.1 Clock runtime implementation
6.1-6.4 Blueprint-factory clock attachment + absence of standalone make_clock
7.1 Blueprint-factory implementation updates
8.1-8.4 Bundled-profile clock inclusion tests
9.1 Bundled-profile implementation updates
10.1-10.3 propagate_truth phase-4 integration tests
11.1-11.5 Verification (pytest, openspec validate, frozen baseline)

## Files Affected
- rtl/vectors/maritime/clock.py (new — ClockSpec + Clock)
- rtl/vectors/maritime/fleet.py (modify blueprint factories to attach Clock)
- rtl/vectors/maritime/platform_profile.py (add ClockSpec to bundled profiles)
- tests/maritime/test_clock.py (new)
- tests/maritime/test_fleet.py (clock-attachment tests added)
- tests/maritime/test_platform_profile.py (bundled-profile clock tests added)
- tests/maritime/test_dynamics.py (phase-4 clock advance tests added)

## Spec Pointers
- `specs/maritime-clock-model/spec.md` — ADDED requirements for
  ClockSpec, runtime Clock, blueprint-factory attachment,
  propagate_truth phase-4 advance.
- `specs/maritime-platform-profile/spec.md` — MODIFIED delta on the
  Bundled M1 Fleet Profiles requirement to include a zero-drift,
  zero-power `ClockSpec` in each bundled profile's components tuple.

## Cross-Change Coordination
- Depends on `maritime-fleet-dynamics` (provides `ComponentSpec`,
  `Node.components` mapping, phase-4 dispatch).
- Triggers a follow-up audit finding on `maritime-sensors` — its
  current `SensorEnv.clock_by_node_id` mapping must migrate to
  `node.components["clock"].wall_time(t_sec)` once this change lands.
  That migration is a separate change to `maritime-sensors` tracked by
  the spec-audit; this module supplies the infrastructure it will
  migrate to.
