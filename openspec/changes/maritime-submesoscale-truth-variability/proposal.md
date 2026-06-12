## Why

Change 1 (`maritime-real-current-data`) establishes real-data truth (CIOPS /
CMEMS nowcast) + independent harmonic-decomposition climatology (per-cell
tidal constituents + non-tidal monthly background, derived from hindcast
via `utide`). That fixes the truth-leakage bug and resolves the dominant
deterministic tidal signal on the prior side, but leaves a gap: nowcast
grid resolution (500 m – 10 km) is too coarse to resolve the
submesoscale motions (100 m – 10 km) that dominate a drifter's sub-daily
trajectory in coastal waters beyond what harmonic analysis of the
hindcast can capture. A PF whose truth = grid-interpolated nowcast and
whose climatology = harmonic-from-hindcast still faces an unrealistically
benign inference environment: both fields resolve roughly the same
deterministic tidal structure, and the residual (non-tidal, non-gridded
eddies / fronts / filaments) is the class of motion the PF's sensors
should actually be constraining.

Real drifter deployments face submesoscale eddies, fronts, filaments, and
turbulence that neither the nowcast grid nor a monthly-mean prior can
resolve. That unresolved chaos — riding on top of the nowcast's
larger-scale circulation — is *the* inferential challenge: the PF's prior
describes what's resolvable; LoRa + GPS fixes constrain where the
unresolved part happened to push the drifter. Without it, the PF's
"successful tracking" is tracking of structure its own prior already
contains.

This change adds a **spectral Gaussian random field submesoscale layer**
on top of Change 1's real-data truth. It is:
- **Seeded-deterministic**, so scenario reproducibility is preserved.
- **Divergence-free** (stream-function construction), so drifter
  trajectories don't accumulate spurious clustering that would corrupt
  PF inference.
- **Kolmogorov-spectrum** (k^(-5/3) forward cascade), matching observed
  submesoscale energy distributions (Poje et al. 2017).
- **OU temporal evolution** with configurable correlation time, matching
  observed Lagrangian integral timescales (10–60 min).
- **Independent from the onboard climatology**: the climatology gains a
  scalar `submesoscale_energy_ms` field describing the operator's *expected*
  submesoscale amplitude in that region (from regional surveys), which
  flows into `var_vx` / `var_vy` — NOT the truth realization's actual
  per-tick values.

After this change, the PF faces a realistic inferential task: a time-
varying truth with unresolvable chaos, a smooth-in-space/coarse-in-time
prior that knows the chaos exists (via variance) but not where or when.
That is the problem the architecture has always claimed to solve, now
actually tested against it.

## What Changes

### New module
- `rtl/vectors/maritime/current_fields_submesoscale.py` — `SpectralSubmesoscaleField`
  class:
  - Internal FFT-based stream-function grid (default 256×256 on a 1.5×
    padded bbox to avoid periodic-boundary reflection into the scenario
    domain).
  - `step(dt_sec)` advances an OU state on the spectral coefficients;
    regenerates `ψ(x, y, t)`, takes spatial derivatives to produce
    `(u, v)`, stores a `RegularGridInterpolator` bundle per time slice
    (or per tick).
  - `eval(lat_deg, lon_deg, t_sec) -> (vx, vy)` lookups into the
    interpolator.
  - Seeded-deterministic: same `(seed, bbox, tick-grid)` → byte-identical
    field.

### RealCurrentField composition
- `RealCurrentField.velocity_at(lat, lon, t)` is extended by a composition
  pattern: scenario-gen constructs a `CompositeCurrentField(grid_interp,
  submesoscale)` that adds their contributions. The underlying
  `RealCurrentField` class itself does NOT gain a reference to the
  submesoscale field (keeps the provenance-independence contract intact —
  the submesoscale is a separate truth-side component, composed at scenario
  assembly time, not owned by the grid loader).

### Climatology: `submesoscale_energy_ms` operator-knowledge scalar
- `ClimatologySource` Protocol (from Change 1) adds an optional
  `submesoscale_energy_ms` attribute (scalar, defaults to 0 for
  backwards compatibility). `HarmonicClimatology` stores it at
  construction.
- The `velocity_at(lat, lon, t_sec)` return tuple's `var_vx`, `var_vy`
  components now include `submesoscale_energy_ms²` added to the gridded
  variance. Represents the operator's prior belief "we know there's
  ~0.1 m/s submesoscale variability in this region" — obtained from
  regional surveys, NOT from the truth field.
- CLI flag `--climatology-expected-submesoscale-ms` (default matches
  `--submesoscale-amplitude-ms`) injects the scalar at scenario-gen.

### Generator CLI flags
- `--submesoscale-amplitude-ms` (default 0.1, disables when 0)
- `--submesoscale-correlation-length-m` (default 750, coastal/shelf)
- `--submesoscale-correlation-time-sec` (default 1200 = 20 min)
- `--submesoscale-spectrum-slope` (default -1.667 for Kolmogorov)
- `--submesoscale-seed` (default derived from main seed)
- `--submesoscale-grid-points` (default 256; perf knob)
- `--climatology-expected-submesoscale-ms` (default matches truth
  amplitude; separate flag so truth and prior knowledge can be decoupled
  in controlled experiments)

### Dashboard
- Extends Change 1's "Truth currents" overlay so it renders
  `grid_interp + submesoscale` when the submesoscale field is active.
- Adds optional "Truth current chaos" toggle rendering the differential
  (`truth − grid_interp`) — visually surfaces the submesoscale layer's
  contribution.
- Sidecar grows two arrays: `truth_grid_chaos_u`, `truth_grid_chaos_v`
  (the submesoscale-only component).

### Tests
- **Substance — submesoscale energy**: truth field velocity at a fixed
  `(lat, lon)` differs from `grid_interp(lat, lon, t)` by a distribution
  whose stddev matches `--submesoscale-amplitude-ms` within ±20%.
- **Substance — divergence-free**: numerical divergence of the spectral
  layer on its internal grid is < 1e-6 at every cell (stream-function
  construction guarantees this; we verify numerically).
- **Substance — energy spectrum**: 2D power spectrum log-log slope ∈
  `[-2.0, -1.3]` (generous tolerance around -5/3).
- **Substance — climatology does NOT carry submesoscale mean**:
  `climatology.mean_vxvy(lat, lon)` matches time-averaged truth's
  grid-interp component (no submesoscale) to within climatology-grid
  resolution noise.
- **Substance — climatology DOES carry submesoscale in var**:
  `sqrt(climatology.var_vxvy(lat, lon)) ≥ submesoscale_expected_amplitude`
  at every grid cell.
- **Reproducibility**: same seed → byte-identical truth field.
- **End-to-end**: PF dashboard shows realistic belief-cloud spreading
  over LoRa-silent windows; LoRa fixes snap the cloud back.

## Capabilities

### New Capabilities
- `maritime-submesoscale-truth-variability`: owns `SpectralSubmesoscaleField`,
  its spectral-synthesis / OU-evolution / divergence-free contracts, and
  the `CompositeCurrentField` composition pattern that layers
  submesoscale on top of a grid-resolving base field.

### Modified Capabilities
- `maritime-current-fields`: ADD requirement documenting the composition
  pattern — truth currents in real-data scenarios are the sum of a
  `CurrentField` base + a `SpectralSubmesoscaleField` addition, both
  satisfying the existing `CurrentField` Protocol when composed.
- `maritime-scenario-gen`: MODIFY "CLI Invocation" to add submesoscale
  flags + climatology-expected-submesoscale flag. MODIFY the sidecar
  requirement to include `truth_grid_chaos_u/v`.
- `maritime-map-payload`: MODIFY `ClimatologySource` — `velocity_at`'s
  returned `var_vx`/`var_vy` semantics expand to "expected variance
  including unresolved submesoscale / tidal / eddy energy" and the
  climatology gains a `submesoscale_energy_ms` scalar storage field.
- `maritime-dashboard`: ADD truth-chaos overlay requirement.

## Impact

- **Depends on Change 1**: `maritime-real-current-data`. The spectral
  submesoscale layer composes on top of `RealCurrentField`; the
  climatology modifications slot into `ClimatologySource`.
- **New production code**: `current_fields_submesoscale.py`,
  `composite_current_field.py` (or equivalent composition helper).
- **Modified production code**: `gen_maritime_scenario.py` (CLI +
  composition + sidecar chaos arrays), `climatology_source.py`
  (`submesoscale_energy_ms` field, variance composition),
  `map_payload.py` (documentation / pass-through — no runtime change
  since climatology is now Protocol-typed), `experiments/12_maritime_dashboard.py`
  (chaos overlay rendering).
- **Dependencies**: `scipy.fft`, `scipy.interpolate.RegularGridInterpolator`
  — already in the stack via Change 1's real-data work. No new deps.
- **Performance**: ~5 ms per tick per scenario for 256×256 grid.
  Scenario-gen wall-clock increases modestly (each tick now regenerates
  a spectral slice); PF hot path unchanged (consumes grid-interp result
  through existing accessor).
- **Expected RMSE impact**: PF drifter RMSE will degrade relative to
  Change 1's baseline. That is the intended outcome — we are now
  measuring inference against realistic input. Post-landing tasks scope
  how much degradation is acceptable and whether sensor-fusion work
  needs to be ahead of it.
