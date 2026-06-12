# Fleet-shared bias channel — design (2026-04-28)

## Purpose

Single-drifter bias inference is bounded: at our 36h LoRa cadence, OU
re-inflation (γ²≈0.717 per leg at τ=36h) limits per-leg posterior
shrinkage. Calibration plateaus at ~0.7. Multiple drifters in the
same basin observe correlated bias structure; sharing across the
fleet breaks the single-drifter information bound.

This is the **architectural** path forward, distinct from local
σ-accounting cleanup (already done) and smoother fixes (deferred).

## Out of scope (this doc)

- Dynamic redeployment policy (uses the `coverage_signal` output;
  separate planner workstream).
- Physical TDMA/scheduling. The aggregator is in-process; LoRa
  modelling is a payload-budget sanity check, not a stack
  implementation.
- Re-architecture into `rtl/vectors/maritime/` framework. Prototype
  iteration first; framework once the deployment metric story
  settles.

## Architecture

```
┌──── per-drifter (unchanged) ──────────────────────────────────┐
│ BiasFieldState (per-particle, station-relative grid, dense    │
│ Matérn cov, leg accumulators) — `bias_field.py`               │
│ LiveBiasKnowledge wraps NEMO prior + ensemble-mean bias       │
│ ──────────────────                                            │
│ at surface dwell:                                             │
│   exfiltrate ensemble-aggregated summary → fleet aggregator   │
│   import latest fleet-aggregated summary → local controller   │
└────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──── fleet (new) ──────────────────────────────────────────────┐
│ FleetBiasField (basin-extent grid, single instance)           │
│   mean_u, mean_v, var_u, var_v   shape (D, Y, X)              │
│   timestamps, drifter_count_per_cell                          │
│ FleetBiasAggregator                                           │
│   precision-weighted update on overlapping cells              │
│   cross-drifter correlation factor ρ ∈ [0, 1]                 │
└────────────────────────────────────────────────────────────────┘
```

### `FleetBiasField` state

A single shared field over a basin-extent grid:

- **Grid:** lat/lon basin-extent rectangle (e.g., SoG bbox
  `(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)` from `_fleet_sim_v0.py`).
  Cell size 2 km (matching `GridBiasBasis.cell_size_m`); grid shape
  ~(16, 23) for SoG. **Depth axis:** 5 layers, matching
  `DEFAULT_DEPTH_SET`.
- **Stored fields (per cell):**
  - `mean_u`, `mean_v` — fleet-pooled bias mean (m/s)
  - `var_u`, `var_v` — fleet-pooled bias variance (m²/s²)
  - `last_update_t_sec` — most recent contributing dwell time
  - `n_contributors` — count of distinct drifters that have ever
    contributed to this cell (a coverage proxy)
- **Initial state:** `mean = 0`, `var = sigma_bias_init_ms²`
  (matches the local bias prior at start-of-mission).
- **Decay:** when no drifter has updated a cell within `tau_ou_sec`,
  the cell decays back toward the prior via the same OU rule the
  local bias uses. Concretely:
  `var_t+Δ = γ² · var_t + (1-γ²) · prior_var`,
  `γ = exp(-Δ / tau_ou_sec)`. Mean decays toward 0.

### LoRa exfil schema (drifter → fleet)

Each drifter at surface-dwell composes one payload:

```
struct DwellExfilV0 {
    drifter_id     : u8           // 1 B
    dwell_t_sec    : u32          // 4 B
    fix_lat        : f32          // 4 B (most recent LoRa fix, for correlator)
    fix_lon        : f32          // 4 B
    cells          : Cell[K]      // K = changed cells only
}
struct Cell {
    di, yi, xi     : u8 each      // 3 B (cell coords; basin grid)
    mean_u, mean_v : f16 each     // 4 B
    var_u, var_v   : f16 each     // 4 B  (precision = 1/var)
}                                 // 11 B / cell
```

**Sparsification policy.** Only export cells whose local posterior
variance has dropped below the shared field's current variance at
that cell — i.e., cells where the drifter has *new information*
relative to what the fleet already knows. This is a precision
threshold; tuneable with default `local_var < 0.8 · fleet_var`.

**Payload budget sanity check.** Full basin grid (16 × 23 × 5) =
1840 cells × 11 B = ~20 KB. With sparsification, expect 50–200
cells exported per dwell = 0.5–2 KB. Comfortable at SF8–SF9
(typical link budget ~50 KB per surface dwell window). No
dimensional reduction needed in v0.5 if sparsification works.

**Aggregator-side ingestion.** Drifter posts the message to a
shared in-process queue (sim) or a real LoRa gateway (deployment).
Aggregator pulls and applies; see below.

### `FleetBiasAggregator.update`

Takes an exfil payload from drifter `d`. For each cell `(di, yi, xi)`:

```
prec_local = 1 / payload.var_u(di, yi, xi)
prec_fleet = 1 / fleet.var_u[di, yi, xi]

# Cross-drifter correlation discount: if multiple drifters are
# reporting from the same OU-evolved bias, their observations are
# not independent. ρ ∈ [0, 1] discounts the new precision.
prec_local_eff = (1 - ρ) · prec_local

prec_post = prec_fleet + prec_local_eff
mean_post = (prec_fleet · fleet.mean_u + prec_local_eff · payload.mean_u) / prec_post
fleet.mean_u[di, yi, xi] = mean_post
fleet.var_u [di, yi, xi] = 1 / prec_post
fleet.last_update_t_sec[di, yi, xi] = payload.dwell_t_sec
fleet.n_contributors[di, yi, xi] += (1 if drifter_d_first_visit_cell else 0)
```

Same for `(mean_v, var_v)`. **ρ defaults to 0.5** — measured-not-pinned;
revisit once we have empirical data on cross-drifter posterior
correlation in the v0.5 sim. The "all drifters report identical
bias estimate" sanity check should give cov drop ≈ factor 1/(1-ρ);
ρ=0.5 → factor 2.

Before applying any update, run the OU decay on cells whose
`last_update_t_sec` is older than the current dwell time. This
keeps stale cells from contaminating the posterior at faraway
cells the fleet hasn't seen recently.

### Controller-side composition

`LiveBiasKnowledge.get_current_at` currently returns
`nemo_prior(lat, lon, d, t) + ensemble_mean_local_bias(cell)`,
gated by local posterior variance (clean prior fallback when local
hasn't observed).

**New composition:**

```
fleet_bias_at_pos      = bilinear_interp(FleetBiasField.mean, lat, lon, d, t)
fleet_var_at_pos       = bilinear_interp(FleetBiasField.var,  lat, lon, d, t)
local_bias_at_pos      = ensemble_mean_local_bias(cell containing lat, lon)
local_var_at_pos       = ensemble_var_local_bias(cell)
local_minus_fleet_at_X = local_bias_at_X − bilinear(fleet, X)  for X = drifter pos

return nemo_prior(lat, lon, d, t)
     + fleet_bias_at_pos                            # long-spatial info from peers
     + local_minus_fleet_at_X                       # high-frequency local correction
```

The third term lets the local drifter's high-frequency bias signal
ride on top of the fleet's smoother basin-scale estimate. When a
drifter is in a cell the fleet has never seen, `fleet_var ≈
prior_var` and the term `local_minus_fleet` collapses to
`local_bias_at_X − 0 = local_bias_at_X`, recovering the current
local-only behaviour.

**Variance gate.** Existing `posterior_var_gate_ratio = 0.5` semantics
extend naturally: the gate triggers on
`max(local_var_at_pos, fleet_var_at_pos)` (use whichever has more
information). If neither has dropped below the threshold, fall back
to clean prior.

### `coverage_signal` output

A high-cov-cell map for the dynamic redeployment planner (out of
scope here, in scope later):

```
coverage_signal[d, y, x] = 1 - (fleet.var[d, y, x] / prior_var)
```

Range `[0, 1]`; `0` = no fleet information at this cell, `1` =
zero posterior variance. Per-tick or per-dwell snapshot. The
redeployment planner can use this to identify under-observed
regions and route drifters there.

## Module boundaries

```
experiments/harmonic_prototype/
    rbpf_prototype/
        bias_field.py            # unchanged; per-drifter local bias
        fleet_bias_field.py      # NEW: FleetBiasField, FleetBiasAggregator,
                                 #      DwellExfilV0 payload, OU decay
        experiment.py            # unchanged except LiveBiasKnowledge gains
                                 #   optional fleet_bias_field arg + composition
    _fleet_sim_v0.py             # current per-drifter independent
    _fleet_sim_v0_5.py           # NEW: shared-bias variant
                                 #   - fleet aggregator instance
                                 #   - per-drifter exfil call at surface dwell
                                 #   - per-drifter import of latest fleet field
                                 #   - same TDOA reconstruction back-end
```

## Aggregation: integrity guards

Per project policy on errors-must-be-explicit:

- Aggregator rejects payloads with NaN/Inf, var ≤ 0, or cell
  indices out of grid → raise (don't silent-drop).
- Aggregator stamps `payload.dwell_t_sec` against simulator clock;
  payloads received out of order are accepted (LoRa retransmits)
  but ones with `dwell_t_sec < last_update_t_sec[cell] −
  tolerance` are rejected.
- The `(1-ρ)` discount factor is asserted ∈ (0, 1] at construction.
- OU decay is applied **before** ingestion to ensure all cells are
  on the same time base.

## Tests (next-session)

Unit tests for the aggregator (in
`experiments/harmonic_prototype/tests/test_fleet_bias_aggregator.py`,
or the existing tests/ directory if present):

1. **No-op with one drifter, identical posterior.** Ingesting the
   same payload twice from one drifter should give precision growth
   factor (1 − ρ) per repeat, not double-precision (the same drifter
   isn't bringing new information).

   *Wait — the precision-weighted update treats each new payload as
   independent. We need a "drifter id + cell" memo: re-ingesting
   the same drifter's same-tick payload at the same cell is a no-op.
   Different drifters → independent (modulo ρ).*

2. **Two identical drifters, ρ=0.** Cov drop = factor 2 exactly
   (independent observations of same truth).

3. **Two identical drifters, ρ=0.5.** Cov drop = factor 1.5
   (correlated; less than factor 2).

4. **OU decay.** A cell ingested at `t=0` then queried at `t=2τ`
   without further updates returns variance ≈ prior variance to
   within `1 - exp(-2)² ≈ 0.98 · prior_var`.

5. **Sparsification.** A drifter whose local posterior variance is
   above `0.8 · fleet_var` exports zero cells (its information is
   redundant).

6. **End-to-end fleet sim.** `_fleet_sim_v0_5.py` σ_event vs.
   `_fleet_sim_v0.py` σ_event at the same 4-station configuration.
   Hypothesis: σ_event mean drops 20–40%, p95 drops 30–50%, at
   the same calibration. Substance test, not shape test — fail
   if σ_event doesn't decrease meaningfully.

## Decision: when to implement

After P1 (single-drifter v0 fleet sim) baseline σ_event is
documented. The v0 → v0.5 comparison is the deliverable; we want
the v0 number first to know what we're improving against. Don't
pre-commit to P4 before P1's baseline is in hand.

## Open design questions to revisit during implementation

- **Basin grid vs. mosaic of station grids.** The plan calls for
  a single basin-extent grid. An alternative is a mosaic of
  `GridBiasBasis` instances anchored at deployed stations and a
  weighted combine in overlap regions. Mosaic is closer to the
  current local data model; basin grid is closer to a true field.
  Default to basin grid for cleanliness; mosaic is an
  optimisation if memory becomes an issue (basin grid for SoG @
  16x23x5 floats = ~4KB per field × 4 fields × 8B = ~120KB —
  trivial).

- **Bilinear vs. nearest-neighbour interp** for fleet → drifter
  query. Local bias uses nearest-neighbour. If the basin grid is
  2km cells and drifters cross cells in ~20 min at 1 m/s, NN
  jumps will be visible in the controller's bias estimate.
  Bilinear is the fix. Default to bilinear.

- **What to share at LoRa: posterior summary or sufficient
  statistics?** Plan calls for `(mean, var)`. Alternative is to
  share `(precision · mean, precision)` and accumulate at the
  aggregator — equivalent to a Kalman update on the fleet field
  with the drifter's posterior as a "measurement." Same math,
  different bookkeeping. Stick with `(mean, var)` for clarity.

## Plan-level conclusion

Architecture is straightforward: one new module
(`fleet_bias_field.py`) + one new sim variant (`_fleet_sim_v0_5.py`)
+ a small composition change in `LiveBiasKnowledge`. The win is
contingent on (a) ρ being meaningfully < 1 in our basin (i.e.,
drifters' observations not perfectly correlated), and (b)
basin-coherent bias dominating the local high-frequency component.
Both are testable in v0.5 directly.
