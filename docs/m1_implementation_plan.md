# M1 Implementation Plan

> Durable record of the order in which the M1 change set should be applied,
> the dependency chain between changes, and the validation gates between
> each step. Complements `docs/maritime_scenario_harness_plan.md` (what M1
> delivers) and `docs/simulation_integrity.md` (how honest it has to be).
>
> **Status:** Written 2026-04-21. Revised 2026-04-21 after the
> unfounded-content audit session: five findings approved and applied
> (see "Audit Outcomes" below). All twelve changes + specs validate via
> `openspec validate --all --strict`. Updated 2026-04-22: Tiers 0–1a
> applied previously; Tiers 1b and 2 applied externally; Tier 3a
> (`maritime-clock-model`) applied and archived; Tier 3b
> (`maritime-sensors`) archived; Tier 4 (`maritime-scenario-gen`)
> applied and archived after a detailed code review surfaced four
> substantive items (LoRa obs cardinality bug, ENU-vs-geodesic spec
> drift, two silent-drop sites, sensor-module bypass) plus the
> per-pair `LoraLinkOutcome` refactor; all fixed in-change. Next:
> Tier 5 (`maritime-pf-float`) — see "Pre-Apply Items for Tier 5"
> below for two open issues to resolve before `/opsx:apply`.

## Change Set

Nine active OpenSpec changes, one standing-spec modification delivered inline:

| # | Change | Adds | Modifies |
|---|---|---|---|
| 1 | `project-infra-import-linter` | — | `project-infra` |
| 2 | `maritime-current-fields` | `maritime-current-fields` | — |
| 3 | `maritime-map-payload` | `maritime-map-payload` | — |
| 4 | `maritime-fleet-dynamics` | `maritime-state-layout`, `maritime-fleet-dynamics` | `maritime-platform-profile` |
| 5 | `maritime-clock-model` | `maritime-clock-model` | — |
| 6 | `maritime-sensors` | `maritime-sensors` | — |
| 7 | `maritime-scenario-gen` | `maritime-scenario-schema`, `maritime-scenario-truth-schema`, `maritime-scenario-gen` | — |
| 8 | `maritime-pf-float` | `maritime-pf-estimate-schema`, `maritime-pf-float` | — |
| 9 | `maritime-dashboard` | `maritime-dashboard` | — |

## Dependency Graph

```
project-infra-import-linter ──────────────────────────────────────┐
                                                                  │
maritime-geo (archived) ───┬── maritime-current-fields ───┐       │
                           └── maritime-map-payload ──────┼───┐   │
                                                          │   │   │
maritime-platform-profile  │                              │   │   │
  (archived; MODIFIED       ├── maritime-fleet-dynamics   │   │   │
   delta in fleet-dynamics) │           │                 │   │   │
                           │           ├── maritime-clock-model  │
                           │           │                 │   │   │
                           │           ├── maritime-sensors      │
                           │           │                 │   │   │
                           │           └── maritime-scenario-gen
                           │                                     │
                           │                                     ├── maritime-pf-float
                           │                                     │          │
                           └─────────────────────────────────────┴──────────┴── maritime-dashboard
```

## Apply Order

Strictly dependency-ordered. Tiers within a row are independent and may
be applied in parallel via worktree isolation.

| Tier | Change(s) | Depends on | Parallel? | Pre-apply notes |
|------|-----------|------------|-----------|-----------------|
| 0 | `project-infra-import-linter` ✓ | — | — | Archived 2026-04-21. `import-linter` installed as dev dep, `[tool.importlinter]` configured, `uv run lint-imports` exits zero, 68 tests pass, `project-infra` standing spec updated with Import Boundary Enforcement requirement. |
| 1a | `maritime-current-fields` ✓ | `maritime-geo` (archived) | ∥ 1b | Archived 2026-04-22. `CurrentField` protocol + `SyntheticEddyField` (mean flow + Gaussian eddies + M2 tide with configurable direction). 12 contract tests, 80/80 suite green, pyright clean, `lint-imports` clean (0 contracts enforced yet — see Tier 5). Standing spec at `openspec/specs/maritime-current-fields/`. Post-apply fix: added `tidal_direction_deg` to `FieldConfig` (original hardcoded tide eastward only); removed silent `>0` guard on tidal amplitude. |
| 1b | `maritime-map-payload` ✓ | `maritime-geo` (archived) | ∥ 1a | Archived 2026-04-22. Delivers `RegionalMap` + bathymetry + climatology. No composition integration needed. |
| 2 | `maritime-fleet-dynamics` ✓ | `maritime-platform-profile` (archived; MODIFIED delta here), `maritime-current-fields`, `maritime-geo` | — | Archived 2026-04-22. Biggest change. Establishes composition (`Node` + components tuple), factories per blueprint, `ComponentSpec` protocol, fixed 4-phase tick order. Drops boolean capability flags from `NodeProfile` via the MODIFIED delta. Downstream components (clocks, sensors) build against what this delivers. |
| 3a | `maritime-clock-model` ✓ | `maritime-fleet-dynamics` (for `ComponentSpec` + `node.components["clock"]` slot) | — | Archived 2026-04-22. `ClockSpec` (frozen, `ComponentSpec`-conforming, `kind="clock"`, `drift_ppm`, `avg_power_mw`) + `Clock` runtime (`advance(dt_sec)`, `wall_time(true_sec)`, mutable `_accumulated_offset_sec`). Blueprint factories require and instantiate `Clock` from profile's `ClockSpec`. Bundled profiles carry `ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)`. `propagate_truth` phase 4 calls `clock.advance(dt_sec)`. 20 new tests (13 clock + 3 fleet + 4 profile + 3 dynamics — includes 1 relaxed pre-existing assertion), 207 total green, pyright clean, `openspec validate --all --strict` clean. Specs synced to `maritime-clock-model`, `maritime-fleet-dynamics`, `maritime-platform-profile`. |
| 3b | `maritime-sensors` ✓ | `maritime-fleet-dynamics`, `maritime-clock-model` (reads `node.components["clock"].wall_time` for timestamps; depends on blueprint factories actually attaching `Clock` — so 3a must land first), `maritime-map-payload` (bathymetry for bathy-probe sensor) | — | Archived 2026-04-22. `SensorEnv.clock_by_node_id` dropped (audit F3); sensors read `node.components["clock"].wall_time(t_sec)` directly, raising `KeyError` if absent. Six sensor types (gps/imu/baro/mag/bathy_probe/lora_toa) + `Measurement` record + `Sensor` Protocol. `LoraTOASensor.sample_link` and `LoraLinkOutcome` added during Tier 4 review (per-pair bidirectional ranging). |
| 4 | `maritime-scenario-gen` ✓ | All of tiers 1–3 | — | Archived 2026-04-22. 3-spec delta (`scenario-schema`, `scenario-truth-schema`, `scenario-gen`). Physical module split delivers AST-based truth separation. Header carries `onboard_map_path` (sidecar for the degraded onboard map) and `anchor_positions` (operational-survey mapping sourced from each anchor's `MooredPoseSpec`). Detailed code review at archive time surfaced and fixed: (a) LoRa obs cardinality bug — per-node `should_sample` collapsed 45 successful pairs to 10 obs; refactored to TDMA-cycle gating with 2 obs per success via `LoraTOASensor.sample_link`; (b) spec drift — "geodesic distance" → "ENU planar distance" since the entire state vector lives in ENU; (c) two silent-drop sites in the sensor-factory dispatch; (d) `parse_bbox` now an argparse `type=`; (e) added `--created-at` flag with default = wall-clock now (golden-trace tests pin `2026-04-22T00:00:00+00:00` for byte-identity); (f) golden-trace cap raised from 100 KB to 15 MB to fit `--nodes 10` × 900-tick spec D6 parameters. Final state: 346 maritime tests pass, lint-imports clean, frozen baseline intact. |
| 5 | `maritime-pf-float` | `project-infra-import-linter` (registers the PF truth-separation contract), `maritime-scenario-gen` (consumes `ScenarioReader`; obtains onboard map via `reader.onboard_map()`), `maritime-fleet-dynamics` (imports `StateLayout`), `maritime-map-payload` | — | 2-spec delta (`pf-estimate-schema`, `pf-float`). Main estimate stream + optional particle sidecar with thinning. Truth separation via module boundary + import-linter contract + type signatures. PF consumes onboard map via `ScenarioReader(path).onboard_map()`; anchor positions come from `header.anchor_positions`. **Open pre-apply items** (see "Pre-Apply Items for Tier 5"): (1) `lora_toa` partner_id schema gap; (2) `RegionalMap.current_climatology_at` API name vs. spec's `climatology_at`; (3) stale "reconstructs onboard map" wording in proposal/tasks. |
| 6 | `maritime-dashboard` | `maritime-scenario-gen`, `maritime-pf-float`, `maritime-geo`, `maritime-map-payload` | — | Consumer. Imports `ScenarioTruthReader` from the dedicated truth module. Per-node drill-down from the sidecar (no privileged focus-node subset). Missing optional files surface explicit warnings, not silent fallback. |

## Parallelism Opportunities

Genuine parallel-apply candidates (via `/opsx:apply` with worktree
isolation):

- **Tier 1:** `current-fields` ∥ `map-payload`. Independent; different modules; no shared state.

Previously-listed "Tier 3: clock-model ∥ sensors" has been demoted to sequential. Sensors' `sample` method reads `node.components["clock"].wall_time(...)` on factory-built nodes, and the Clock wrapper only lands at that slot once clock-model has shipped its MODIFIED delta on the blueprint factories. Applying `sensors` before `clock-model` would mean the sensor tests either skip the real factories (cheating) or hit `KeyError`. Land clock-model first, then sensors.

Sequential-only: Tier 2 (fleet-dynamics), Tier 3a (clock-model), Tier 3b (sensors), Tier 4 (scenario-gen), Tier 5 (pf-float), Tier 6 (dashboard). Each is the integration point for its downstream consumers.

## Validation Gates

Between every change:

1. `openspec validate <change-name> --strict` must pass before starting implementation.
2. `/opsx:verify <change-name>` at implementation end — specifically the wiring checks (substantive → wired → functional) and test integrity (contract tests unmodified by implementer; untested-rejection-branch check).
3. `uv run pytest` green.
4. `uv run lint-imports` exit zero once `project-infra-import-linter` has landed (Tier 0+).
5. `/opsx:sync <change-name>` before `/opsx:archive`.
6. `openspec validate --strict` after sync, against standing specs.

Do not batch-verify at the end of the whole chain — ten sequential changes compound a single ungrounded assumption into a hard-to-trace integration bug. Verify between each.

## Audit Outcomes

The unfounded-content audit (prompt in
`dev/prompts/spec_audit_unfounded_content.md`) completed 2026-04-21.
Five findings approved and applied; all twelve changes + specs
validate via `openspec validate --all --strict`. Cross-cutting
patterns are documented for future-you:

1. **F1/F2 — `maritime-clock-model` rewrite.** The draft shipped a
   parameterized closed-form clock (`wall_time(t) = t + offset + t *
   drift * 1e-6` with static `offset_sec`/`drift_ppm` attributes) and
   a non-`ComponentSpec` class hierarchy (`AnchorClock` /
   `ShearSyncedClock` / `DrifterClock`) split by operational role.
   Replaced with a single `ClockSpec` conforming to `ComponentSpec`
   (`kind="clock"`, `drift_ppm`, `avg_power_mw`) and a runtime
   `Clock` with `advance(dt_sec)` + `wall_time(true_sec)` plus
   internal accumulated-offset state. Blueprint factories attach it
   at `node.components["clock"]`. MODIFIED delta on
   `maritime-platform-profile` adds a zero-drift, zero-power
   `ClockSpec` to each bundled profile. Zero-offset M1 behavior is
   emergent from `drift_ppm=0.0`, not a contract.

2. **F3 — `maritime-sensors` clock-access unification.** The draft
   routed clock access through `SensorEnv.clock_by_node_id:
   Mapping[str, NodeClock]`, duplicating the `node.components["clock"]`
   path that fleet-dynamics establishes. Removed the mapping; sensors
   now read `node.components["clock"].wall_time(t_sec)` directly.

3. **F4 — onboard-map signature contradiction.** `map-payload`
   declared `make_onboard_map(truth_map, fidelity, seed)`;
   `scenario-gen` called it with `truth_map`; `pf-float` called it
   with `bbox` — the PF couldn't reconstruct without truth access it
   was explicitly forbidden from having. Resolved with a sidecar
   pattern: generator writes the degraded onboard map to a sidecar
   file; `ScenarioHeader` carries `onboard_map_path`; readers expose
   `reader.onboard_map()`; PF consumes via that accessor. PF never
   calls `make_onboard_map` and never receives the truth map.

4. **F5 — anchor positions via non-truth header field.** PF spec said
   "scenario header carries anchor truth placement" but the header
   spec didn't actually carry anchor positions. Added
   `anchor_positions: Mapping[str, tuple[float, float]]` to
   `ScenarioHeader`, populated by the generator from each anchor's
   `MooredPoseSpec.anchor_lat_deg` / `anchor_lon_deg`. Framed as
   "data loaded onto the node at deployment" (same category as the
   onboard map), not a new charter category.

5. **Cross-cutting pattern.** Cross-change contradictions (F3–F5)
   cluster at module boundaries where one change establishes a shape
   and a later change quietly redeclares it — `openspec validate
   --strict` passes each in isolation but misses the disagreement.
   The phrase "cross-change coordination item" in a design.md
   decision block is a reliable signal that the abstraction isn't
   clean and should either be pushed into its own change or resolved
   in place. None of the present changes carry such prose after the
   audit.

No pre-apply gates remain. Start `/opsx:apply` at Tier 0.

Tiers 0–4 are complete. Resume at Tier 5 (`maritime-pf-float`) after
addressing the open items below.

## Pre-Apply Items for Tier 5

Surfaced during the post-Tier-4 review of the standing specs and the
unmodified `maritime-pf-float` change. Item 1 is being resolved by a
new pre-Tier-5 change (`maritime-typed-observations`); items 2 and 3
remain mechanical edits to the pf-float change before it's applied.

1. **`lora_toa` partner_id gap (substantive) — being addressed by
   `maritime-typed-observations` (Tier 4.5).** Originally the gap was
   "ObservationRecord has no partner_id." Stepping back: that's
   actually a symptom of a deeper issue — `ObservationRecord` collapses
   six structurally different sensor outputs into one shape with a
   string discriminant and a variable-length tuple. Rather than
   patching with `partner_id: str | None`, the new change replaces
   `ObservationRecord` with a sealed union of typed records
   (`GPSObservation`, `IMUObservation`, ..., `LoraTOAObservation`)
   where `partner_id` is naturally a field on `LoraTOAObservation` and
   the IMU split (accel m/s² vs. gyro rad/s, currently shoehorned into
   one sigma + a `unit="m/s^2;rad/s"` joint string) becomes two typed
   sigma fields. See `openspec/changes/maritime-typed-observations/`.
   Lands before pf-float; pf-float drafts directly against the typed
   schema.

2. **`RegionalMap.climatology_at` vs. actual API.** `pf-float` spec
   "Predict Uses Climatology-Derived Current" cites
   `onboard_map.climatology_at(lat, lon, t_sec)`. The actual method
   on `RegionalMap` is `current_climatology_at(lat_deg, lon_deg)` —
   no time argument; returns a 4-tuple `(mean_vx, mean_vy, std_vx,
   std_vy)`. Recommended fix: update the spec scenario and the
   predict-stage task to use the real signature, and decide whether
   the PF wants the std components for adaptive process noise (or
   ignores them in M1).

3. **Stale "reconstructs onboard map" wording.** `pf-float`
   `proposal.md` Impact section ("consumes `ScenarioReader`,
   reconstructs onboard map via `make_onboard_map`") and `tasks.md`
   task 33.1 ("reconstructs onboard map from header") contradict the
   spec body and design D12, which correctly say "via
   `ScenarioReader(path).onboard_map()`." Recommended fix: edit both
   to match the sidecar accessor wording.

4. **Standing spec drift fixed during this review.** The synced
   `openspec/specs/maritime-scenario-gen/spec.md` Requirement: Golden
   Trace Committed and Regenerable still said "100 KB"; bumped to
   15 MB with rationale. Sync miss — the delta-spec edit happened
   in the change but the sync to standing missed the requirement
   body. (Handled inline; flagged here so future syncs re-check the
   specific requirement text, not just the change diff.)

## Tier 4.5: maritime-typed-observations

Inserted between Tier 4 (scenario-gen, archived) and Tier 5
(pf-float, not yet applied). Replaces the single-shape
`ObservationRecord` with a sealed union of per-sensor typed records
(`GPSObservation`, `IMUObservation`, `BaroObservation`,
`MagObservation`, `BathyProbeObservation`, `LoraTOAObservation`).
JSONL records discriminate on a `"type"` key. IMU sigma splits into
accel + gyro (separate fields and units). Generator regenerates
golden trace under the new schema. Touches:
`scenario_schema.py`, `_scenario_parse.py`, `scenario_truth_schema.py`
(import surface only), `gen_maritime_scenario.py`, `sensors.py` (IMU
dual sigma), `platform_profile.py` (`SensorSpec.noise_sigma_secondary`).
MODIFIED deltas on `maritime-scenario-schema` and `maritime-sensors`.
No new modules. Validates strict; ready to apply.

## Forward Control-Flow Architecture (M2+)

The post-M1 closed-loop control architecture is sketched in
`docs/maritime_scenario_harness_plan.md` under "Forward: Closed-Loop
Control Architecture (M2+)". Summary:

- M1 is open-loop. PF estimates feed the dashboard; nothing closes the
  loop back to truth dynamics. Ballast pump in `dynamics.py:63-64` is
  intentionally `pass`.
- M2+ adds per-node controllers consuming `PFEstimateRecord`, emitting
  `ControlAction` records. `propagate_truth` gains an optional
  `control_action` parameter; the pump phase reads it.
- M1 design is deliberately compatible: observation/estimation/control
  stay three separable concerns. The obs schema (locked in by
  `maritime-typed-observations`) has no slot for actuator state and
  doesn't need one. Component vocabulary already supports actuators
  (`BallastSpec`); the slot for new actuator commands is in
  `propagate_truth`'s pump phase, which is currently a no-op.
- Architectural choice deferred to M2: co-simulation (generator embeds
  PF + controller inline) vs. streaming live coupling (external runner
  drives generator + PF + controller tick by tick). M1 forecloses
  neither.

## Out-of-Scope for M1

Explicitly deferred:

- Realistic clock offsets and drift. M1 zero-offset; M2 activates.
- Acoustic event model, TDOA triangulation. M2.
- LNS8-delta PF (compute-budget enforcement). M3.
- HYCOM current field integration. M3.
- Fleet-coordinated PF (drifter-to-drifter LoRa range fusion). M2+.
- Validation against real-data ground truth (Argo float trajectories,
  HYCOM reanalysis for a chosen bbox). M2/M3 — flagged in the
  integrity charter.
- Region-specific calibration (candidate deployment EEZs). M3.

## Downstream (post-M1) flows

After all nine land, the pipeline ships:

1. `gen_maritime_scenario.py --seed S --bbox B --duration-hours 24 --dt-sec 60 --nodes 10 --out scenario.jsonl` — deterministic scenario JSONL with truth + observations.
2. `run_pf_float.py --scenario scenario.jsonl --out estimates.jsonl --particles-out particles.jsonl` — per-node PF estimates + thinned particle sidecar + `pf_summary.json` measurement report.
3. `experiments/12_maritime_dashboard.py --scenario s.jsonl --estimates e.jsonl --particles p.jsonl` — local HTTP dashboard for human-loop visual validation.

M2 adds acoustic events + LNS8 path + realistic clocks. M3 adds real-data validation + FPGA-in-the-loop.
