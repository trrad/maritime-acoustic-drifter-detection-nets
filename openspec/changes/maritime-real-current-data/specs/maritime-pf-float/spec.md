## MODIFIED Requirements

### Requirement: Predict Uses Climatology-Derived Current
The `predict` stage SHALL advance each particle using the onboard
map's climatology current as the expected drift velocity plus the
particle's own state velocity plus process noise. The climatology
SHALL be sourced from the `RegionalMap` instance the PF was
constructed with. The PF predict hot path SHALL call
`onboard_map.climatology.velocity_at_vectorized(lats[:], lons[:],
t_sec)` — the vectorized method declared on the `ClimatologySource`
Protocol (see `maritime-climatology-source`) — which returns four
per-particle arrays `(mean_vx[:], mean_vy[:], var_vx[:], var_vy[:])`
for an aligned `(lats, lons)` pair and a scalar `t_sec`.
No `isinstance`/`cast` narrowing to a concrete type SHALL be required:
the vectorized method is part of the Protocol, so the call typechecks
against the `ClimatologySource`-typed reference. The `t_sec` parameter
SHALL be the scenario tick's absolute time — the same `t_sec` the
PF's `step(...)` method receives. The M1 `HarmonicClimatology` returns
`(mean_vx, mean_vy)` that are time-varying within a month (tidal
harmonic signal) and returns `(var_vx, var_vy)` that are monthly-
constant (non-tidal residual variance); the PF sees this as a single
`ClimatologySource` with no special-casing. Future
`ClimatologySource` implementations (alternative harmonic products,
fleet-learned in M2+) drop in without a PF-side diff.

The PF SHALL use `(mean_vx, mean_vy)` as the expected drift velocity;
the `(std_vx, std_vy)` components inform the per-tick velocity
sampling σ per the "Particle Velocity Is Per-Tick Sampled From
Climatology Variance" requirement. The PF SHALL NOT use the truth
current field (the import-linter contract on `pf_float.py` forbids
that import), and SHALL NOT fall back to a hardcoded zero, constant,
or placeholder value in place of the climatology. Process noise
covariance SHALL remain as documented in design D4 (position
`1 m/√s`, velocity `0.05 m/s/√s`, heading `1 deg/√s`, current estimate
`0.01 m/s/√s`).

#### Scenario: Predict-mean drift tracks climatology when process noise is muted
- **WHEN** a pure-drifter `PFFloat` is constructed with an onboard map whose climatology reports `(mean_vx=0.2, mean_vy=0.0, var_vx=0, var_vy=0)` at the drifter's starting lat/lon for every month
- **AND** `predict(dt_sec=60)` is called for 10 consecutive ticks on a particle cloud whose initial velocity is zero, with process-noise scale factors overridden to zero (via a test-only `PFFloatConfig` override or an equivalent no-op RNG hook)
- **THEN** the particle-mean east-component position has advanced by approximately `0.2 * 10 * 60 = 120 m` relative to tick 0, within numerical-integration tolerance
- **AND** the particle-mean north-component position has advanced by approximately 0 m within the same tolerance

#### Scenario: Predict call path never references the truth current field
- **WHEN** the source of `PFFloat.predict` (and any helper it calls within `pf_float.py`) is inspected via AST walk
- **THEN** no call site references `rtl.vectors.maritime.current_fields.CurrentField`, `rtl.vectors.maritime.current_fields_real.RealCurrentField`, the `.velocity_at(...)` method on a truth-field type, or any other symbol from the truth current-field modules
- **AND** `uv run lint-imports` already forbids the module-level import (redundant with this scenario; this scenario additionally guards against a local/deferred import that lint-imports wouldn't catch by itself)

#### Scenario: Predict passes the tick's t_sec into the climatology read
- **WHEN** `PFFloat.step(dt_sec=60, observations=[], t=5, t_sec=300.0)` is called on a PF whose onboard climatology is a test-double `ClimatologySource` that records every `velocity_at` call's arguments
- **THEN** the climatology's recorded `velocity_at` calls during the predict stage all receive `t_sec == 300.0` (or the equivalent tick-aligned value)
- **AND** none of the calls use a hardcoded zero or a stale cached time

#### Scenario: Predict tracks tidal phase through climatology
- **WHEN** a pure-drifter `PFFloat` is constructed with a `HarmonicClimatology` containing a single `M2` constituent of amplitude 0.3 m/s and zero background at the drifter's starting position, and is stepped with `predict(dt_sec=60)` for 60 consecutive ticks (1 hour) spanning a tidal phase change, with process noise overridden to zero
- **THEN** the particle-mean east-component velocity at tick 0 differs from the particle-mean east-component velocity at tick 59 by a non-trivial amount consistent with the M2 phase change over 1 hour (≈0.24 rad, i.e., ~24% of amplitude change at the rising flank)
- **AND** the trajectory of the particle-mean position over the 60 ticks matches the analytical position integral of the M2 signal within numerical-integration tolerance

#### Scenario: Predict works identically against a HarmonicClimatology and a test-double ClimatologySource that returns the same tuple
- **WHEN** two otherwise-identical `PFFloat` instances are stepped: one with a `HarmonicClimatology` with zero constituents whose single-month background reports `(mean_vx=0.2, mean_vy=0.0, var_vx=0.01, var_vy=0.01)` at the node's position, and one with a test-double `ClimatologySource` that returns that exact tuple regardless of `t_sec`
- **AND** both are stepped with `t_sec` values spanning a range within the same month
- **THEN** the particle clouds after each tick are element-wise equal within RNG-stream-aligned float tolerance
