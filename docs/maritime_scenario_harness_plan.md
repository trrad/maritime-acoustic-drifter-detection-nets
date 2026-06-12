# Plan Seed: Maritime Acoustic Drifter Mesh — Scenario Harness + Dashboard

> Source plan provided to seed an OpenSpec change proposal. Use as input when
> running `/opsx:new` or `/opsx:ff` for the maritime scenario harness work.
> Companion to `docs/maritime_buoy_design.md` (the broader concept doc).

## Context

Building a passive acoustic drifter mesh concept for small dark vessel
detection (see `docs/maritime_buoy_design.md`). Fleet of 10-100 nodes across
three classes (anchors / shear-keepers / drifters) with LoRa mesh comms,
hybrid station-keeping, bathymetry-aided navigation. State per node: 15-25D
depending on class.

This phase builds the Python-only scenario harness and extended dashboard to
validate:
1. Fleet-level dynamics and sensor models
2. Map-aided navigation (bathymetry match + land exclusion + climatology priors)
3. Acoustic detection/triangulation across nodes
4. Multi-node visualization with geographic grounding

No RTL changes yet.

## Key Architectural Decisions

### 1. Scenario data is PF-implementation-agnostic

The scenario generator produces trajectories, sensor measurements, acoustic
events, and map payloads in **physical units** (meters, m/s, degrees, Pa).
PF implementations are separate modules that consume the same scenario.
Delta-encoding is a PF internal choice, not a scenario concern.

```
gen_maritime_scenario.py → scenario.jsonl (physical units)
                        ↓                ↓
                  pf_float.py      pf_lns8_delta.py (M2)
                        ↓                ↓
                estimates_float    estimates_lns8_delta
                        ↓                ↓
                   dashboard / validation harness
```

Same scenario file drives float64 PF (M1), delta-LNS8 PF (M2), and eventually
the RTL testbench (later phase).

### 2. PF encoding: delta only (no plain LNS8)

Maritime trajectories are km-scale. Plain LNS8 cannot represent geographic
positions at the needed precision. **Only delta-encoded LNS8 is implemented**
for the maritime path. Plain LNS8 is explicitly not carried forward from the
6D POC.

### 3. Multi-node JSONL schema

One record per timestep containing all nodes. Sidecar arrays for acoustic
events and LoRa link state. Particle clouds emitted only for "focus nodes"
(3 of them, one per class) to keep record size manageable. Absolute RMSE in
meters.

### 4. Dashboard: forked, map-primary, zero external deps

`experiments/12_maritime_dashboard.py` is a new dashboard (11 stays frozen
for 6D regression). Pure HTML5 Canvas + vanilla JS, embedded CSS. Pre-baked
Natural Earth GeoJSON coastline served from a new `/coast.json` route. No
Leaflet/D3. Pan/zoom implemented in ~60 lines of JS.

### 5. Current field: synthetic by default, HYCOM opt-in

Synthetic analytical field (mean flow + Gaussian eddies + M2 tide) for fast
iteration. HYCOM via `copernicusmarine` behind `--current-source hycom` flag.
Both implement a common `CurrentField` protocol.

### 6. Acoustic model: geometric with Wenz/Thorp

Line-of-sight with spherical spread + Thorp absorption + Wenz ambient noise.
TDOA solved via scipy `least_squares` for 4+ nodes. Bellhop/Kadlu ray tracing
deferred as pluggable alternate.

### 7. Map-aided navigation as a core observation

Each node carries a regional map payload (bathymetry grid + coastline +
shipping lane polygons + current climatology). PF uses this as additional
observations:
- **Bathymetry match**: measured depth must be consistent with `bathy(lat_est, lon_est)`
- **Land exclusion**: zero-weight particles inside coastline polygons
- **Climatology prior**: drift prediction uses climatological current mean +
  variance when no real-time estimate available
- **Shipping lane priors**: reduces false-positive acoustic classification

Map payload ~100 KB-1 MB per node. Over-the-air updates via anchor → LoRa
mesh (simulated in scenario as slowly-updated map blob).

## Milestones

### M1 — Scenario generator + float64 PF + map dashboard

Scope: N=10 nodes (2 anchors, 4 shear, 4 drifters), 15 min @ 1 Hz, synthetic
current, static bathymetry, IMU+baro+mag continuous, GPS on anchors once/5
min, LoRa ranging every 60s, no acoustic events yet. Float64 PF only.
Map-view dashboard with trails and time slider.

**Verification:**
- Generator produces valid multi-node JSONL; truth positions advect with
  current field
- Dashboard loads localhost:8911, shows coast + 10 node icons + trails +
  time slider
- RMSE (absolute meters): anchors < 100m, shear < 200m, drifters < 400m
  after convergence
- `--no-bathymetry` flag increases drifter RMSE by > 20% (confirms map-aid
  earns its keep)
- Unit tests pass: current field, coordinate conversion, sensor noise
  distributions

### M2 — Delta-LNS8 PF + acoustic events + LoRa ranging

Scope: Port PF to delta-encoded LNS8 using existing
`08_lns_cycle_accurate.py`. Run against same scenario files from M1. Add
scripted hidden vessel tracks producing acoustic detections. Dashboard
overlays LNS8 estimates (lighter trails), acoustic pings with TDOA
hyperbolae, active LoRa links. Validation harness compares float64 vs
LNS8-delta outputs.

**Verification:**
- LNS8-delta RMSE within 1.3× float64 RMSE (follows 6D POC trend)
- Effective particles > 10 sustained at 21D (tests "does LNS8 weight kernel
  collapse at higher dims")
- TDOA-solved acoustic position within 100m of hidden vessel truth for 4+
  node detections
- Validation harness produces deviation report per dimension (float64 vs
  LNS8-delta)
- Dashboard: click focus node → particle cloud; pings render at event times

### M3 — Extended features (deferred)

- HYCOM current field via `copernicusmarine`
- Parcels Lagrangian drift for higher fidelity
- GEBCO bathymetry contour overlay
- Bellhop acoustic propagation plug-in
- SanctSound paired acoustic+AIS validation harness
- Multi-focus-node client-side drill-down
- 50-node runs with multiprocessing
- Simulated OTA map-update distribution via mesh

## Files

### Create (M1)

```
rtl/vectors/gen_maritime_scenario.py        — CLI + main loop, emits scenario JSONL
rtl/vectors/maritime/__init__.py
rtl/vectors/maritime/fleet.py               — NodeSpec, make_fleet, StateLayout (15/21/25D)
rtl/vectors/maritime/current_fields.py      — CurrentField protocol, SyntheticEddyField, HYCOMField (opt-in)
rtl/vectors/maritime/dynamics.py            — per-node truth propagation
rtl/vectors/maritime/sensors.py             — IMU/baro/mag/GPS/LoRa-TOA/bathymetry-match sensor models
rtl/vectors/maritime/map_payload.py         — regional map (bathymetry + coastline + lanes + climatology)
rtl/vectors/maritime/coastline.py           — Natural Earth GeoJSON loader/clipper
rtl/vectors/maritime/data/                  — bundled sample coastline + bathymetry

rtl/vectors/maritime/pf_float.py            — float64 bootstrap PF (vectorized over particles)

experiments/12_maritime_dashboard.py        — multi-node map dashboard
```

### Create (M2)

```
rtl/vectors/maritime/acoustics.py           — Wenz/Thorp, detection events, TDOA solver
rtl/vectors/maritime/pf_lns8_delta.py       — delta-encoded LNS8 PF (consumes same scenario)
rtl/vectors/maritime/validate.py            — float64 vs LNS8-delta deviation harness
```

### Reuse

- `experiments/08_lns_cycle_accurate.py` — LNS8 arithmetic engine, imported
  by pf_lns8_delta.py
- `rtl/vectors/gen_pf_scenario.py` — pattern reference for trajectory /
  measurement / JSONL structure. Not called directly; structure mirrored.

### Modify

None. Existing files stay frozen for 6D regression safety.

## Per-Step Scenario Record Schema (physical units only)

```
{
  "t": int, "t_sec": float,
  "nodes": {
    "n00": {
      "class": "anchor" | "shear" | "drifter",
      "truth": {lat, lon, depth_m, vx_ms, vy_ms, vz_ms, heading_deg, ...},
      "sensors": {
        "gps": {lat, lon} | null,              // null when no fix available this step
        "imu": {accel_xyz, gyro_xyz},
        "baro": pressure_pa,
        "mag": {heading_deg, confidence},
        "bathy_probe": depth_to_seafloor_m,    // from pressure + map lookup
      },
      "bathy_at_truth_m": float,               // ground truth bathymetry at node location
      "on_land": bool                          // ground truth land exclusion
    },
    ...
  },
  "current_field": {"mean_vx_ms", "mean_vy_ms", "grid_id"},
  "lora_links": [{"a", "b", "range_m", "sigma_m", "dropped"}],
  "acoustic_events": [                         // M2+
    {"id", "src_truth_latlon", "toa": {node_id: t_sec_utc}, "confidence"}
  ],
  "focus_nodes": ["n00", "n03", "n07"]         // which nodes get particle emission from PFs
}
```

PF output is a separate stream, appending estimates per node:

```
{
  "t": int,
  "nodes": {
    "n00": {
      "estimate": {lat, lon, depth_m, vx_ms, ...},
      "particles": {dim: [vals]},              // focus nodes only
      "weights": [...],                         // focus nodes only
      "rmse_m": {pos, vel, heading},
      "cycles": {predict, weight, resample, total}  // LNS8 path only
    }
  }
}
```

Dashboard joins scenario.jsonl + pf_estimates.jsonl by step index.

## Verification Commands

```bash
# M1: Generate scenario, run float64 PF, view in dashboard
uv run python rtl/vectors/gen_maritime_scenario.py \
    --nodes 10 --duration-min 15 --seed 42 \
    --bbox 36.5,-122.2,37.0,-121.8 \
    --out /tmp/scenario.jsonl

uv run python rtl/vectors/maritime/pf_float.py \
    --scenario /tmp/scenario.jsonl \
    --out /tmp/pf_float.jsonl

uv run python experiments/12_maritime_dashboard.py \
    --scenario /tmp/scenario.jsonl \
    --pf /tmp/pf_float.jsonl
# Open http://localhost:8911

# M2: Same scenario, run LNS8-delta PF, compare
uv run python rtl/vectors/maritime/pf_lns8_delta.py \
    --scenario /tmp/scenario.jsonl \
    --out /tmp/pf_lns8.jsonl

uv run python rtl/vectors/maritime/validate.py \
    --float /tmp/pf_float.jsonl \
    --lns8 /tmp/pf_lns8.jsonl
```

## Library Choices

- **numpy** — vectorized float64 PF
- **scipy** — `least_squares` (TDOA solver), `cKDTree` (LoRa neighbor queries)
- **xarray + copernicusmarine** — HYCOM loader (M3, import-guarded)
- **Parcels** — Lagrangian advection (M3, import-guarded)
- **Natural Earth 1:10m coastline** — public domain, pre-clipped per run (~100 KB)
- **No external JS** — no Leaflet, D3, or Plotly

## Forward: Closed-Loop Control Architecture (M2+)

M1 is open-loop: nodes drift, the PF estimates, the dashboard renders. No
node consumes its own estimate. The `BallastSpec` component slot exists on
ballast drifters but `dynamics.py:63-64` is `if KIND_BALLAST_PUMP in node.components: pass`
— pump is dormant. This section sketches the control flow we'll add in M2+
so that the M1 data shapes are deliberately compatible with it, not a
later refactor target.

### Per-node closed loop

```
[Sensors] ──obs──▶ [PFFloat per node] ──estimate──▶ [Controller per node] ──action──▶ [Actuator]
                          │                                                                │
                          │                                                                ▼
                          │                                                       [propagate_truth]
                          │                                                                │
                          └─────────────[next tick observations]◀──────[truth state]───────┘
```

For each tick, per node:
1. Sensors emit observations (current M1 path).
2. PF processes obs → `PFEstimateRecord` (current M1 path).
3. **(M2+)** Controller consumes the PF estimate plus a target setpoint
   (e.g., "stay within 50 m of last GPS fix" for ballast drifters that
   want to ride a different current layer back to station) → emits a
   per-actuator command.
4. **(M2+)** Actuator command is applied to the node's actuator state
   (e.g., new pump rate) and recorded for replay/visualization.
5. **(M2+)** `propagate_truth` integrates the action — for `BallastSpec`,
   the pump command modulates buoyancy → vertical velocity → depth
   change. The current `pass` becomes the actuator response model.

### What M1 already supports

- **Component vocabulary.** `BallastSpec` is already a `ComponentSpec`
  with `pump_rate_ml_per_s` and `capacity_ml`. Future thrusters /
  drogue-release components land as new `ComponentSpec` types under the
  same factory pattern.
- **PF estimate shape.** `PFEstimateRecord` (mean + cov_diag +
  n_effective) is what a controller would consume. Mean drives the
  setpoint comparison; cov_diag enables risk-aware control (don't
  actuate aggressively when the posterior is wide).
- **Per-class factory pattern.** Anchor / ballast_drifter / pure_drifter
  factories already exist in `fleet.py`. M2+ adds a parallel
  per-class controller factory (anchors are passive, no controller;
  pure drifters are passive, no controller; ballast drifters get
  `BallastController`).
- **Tick-phase order.** `propagate_truth` already uses a documented
  4-phase order (pump → pose → imu_biases → clock). The pump phase is
  the slot where actuator integration lands; no reordering needed.
- **Observation schema isolation.** Observations describe what the
  physical world looks like to the node. Control actions are a
  *separate* concept — sensors don't observe control state, and the
  observation schema (per `maritime-typed-observations`) deliberately
  has no field for actuator state. Adding control later doesn't touch
  any of the typed `Observation` records.

### What M2+ needs to add

- **`rtl/vectors/maritime/control.py`** — `Controller` Protocol per
  actuator class. Methods: `update(estimate: PFEstimateRecord, target: ControlTarget) -> ControlAction`.
- **Per-class controllers** — `BallastController` for ballast drifters;
  no-op controllers (or absence) for anchors and pure drifters.
- **`ControlAction` typed record** — like `Observation` is a sealed
  union per sensor, `ControlAction` is a sealed union per actuator
  (`PumpCommand` for ballast, future `ThrusterCommand`, etc.). Lives
  in a new schema module (`control_action_schema.py` or similar).
- **`propagate_truth` signature extension** — add an optional
  `control_action: ControlAction | None = None` parameter. The
  pump-phase code reads it (currently `pass`) and applies the
  effect on the truth state. M1 callers pass `None`; M2+ callers pass
  the controller's output. Backward compatible.
- **Coupling decision (architectural).** The current pipeline is
  decoupled: scenario generator emits the JSONL; PF runs as a
  separate process consuming it. Closed-loop coupling needs *the
  controller's output to feed back into the next tick's truth*. Two
  shapes are plausible:
  - **Co-simulation.** The generator embeds the PF + controller and
    runs them inline. Truth, observations, estimates, and actions
    are emitted as one consistent record stream. Cleaner for
    scenario reproducibility; tighter coupling between previously-
    separate modules.
  - **Streaming live coupling.** The generator exposes a tick-callable
    API; an external runner drives both generator and PF/controller
    tick by tick. Preserves module separation; harder to make
    deterministic.
  Decision deferred to the M2 design phase. M1 doesn't need to pick;
  M1 just needs to make sure neither path is foreclosed by current
  type signatures or schema choices (it isn't).

### What stays the same

- Sensor module: sensors observe physical state (position, depth,
  pressure, heading, range). Control state is not observable by the
  same channel and stays out of the observation schema.
- PF interface: PF consumes observations and emits estimates. PF is
  not aware of controllers or actions; it's a pure estimator. (This
  is also why the truth-separation contract makes sense: the PF
  doesn't see truth and doesn't see control; both are external.)
- Scenario JSONL format: per-tick observation + truth records keep
  their current shape. M2+ adds parallel `control_actions` records
  in each tick (or a separate sidecar stream — analogous to the
  particle sidecar pattern).

The structural property to preserve through M1 is: **observation,
estimation, and control are three separable concerns.** The current
schema enforces that separation. M2+ extends, doesn't restructure.

## Open Questions

- Exact LoRa TOA noise model — refine from SX1262 ranging literature during M1
- Bathymetry grid resolution: start with 90m GEBCO; try higher res if map-aid
  benefit weak
- Acoustic event rate: tune scripted vessel tracks to ~1-10 detections per
  simulated hour
- Default test bbox: Monterey Bay (36.5,-122.2,37.0,-121.8) has MARS
  hydrophone + SanctSound + dense AIS; good ground-truth region
- Closed-loop coupling shape (M2): co-simulation vs. streaming live coupling
  per the "Forward: Closed-Loop Control Architecture" sketch above. Pick
  during M2 design.
