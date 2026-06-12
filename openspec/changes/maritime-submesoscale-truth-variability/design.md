## Context

Post-Change-1, the truth current field is a `RealCurrentField` interpolating
a NetCDF nowcast grid. Grid resolution sets a floor on spatial detail:
CIOPS-SalishSea is 500 m (sub-grid motions < 500 m invisible), CMEMS is
~10 km (sub-grid motions < 10 km invisible). Coastal submesoscale motions
— the dominant energy band for drifter trajectories on minute-to-hour
timescales — live in exactly that invisible region.

A PF whose truth is grid-interpolated nowcast and whose prior is
harmonic-from-hindcast tracks the deterministic tidal structure the
prior already captures. The substance of the inference problem —
"drifter pushed somewhere unexpected by a ~km-scale eddy the prior can't
resolve; LoRa fix locates it" — never arises in this regime, because
both truth and prior resolve approximately the same deterministic
component.

The oceanographic-literature standard for injecting that missing structure
into a Lagrangian simulation is a **stochastic sub-grid layer**:
- van Sebille et al. (2018), "Lagrangian ocean analysis: Fundamentals
  and practices" — Markov-1 hierarchy (M0 = random displacements,
  M1 = OU on velocity, M2 = OU on acceleration). M1 is the de-facto
  standard for drifter simulations.
- Poje et al. (2017) — GLAD drifter observations confirm k^(-5/3)
  Kolmogorov spectrum at 100 m – 10 km scales (forward cascade; distinct
  from geostrophic k^(-3) at larger scales).
- Reijnders et al. (2022) — practical synthesis methods (spectral Fourier
  synthesis vs Cholesky on covariance matrices) and parameter defaults.

This change implements the Markov-1 (OU-on-stream-function) variant with
spectral synthesis and divergence-free velocity derivation — matching
observational reality (non-divergent 2D flow at these scales, empirical
Kolmogorov spectrum) while being computationally cheap enough for our
tick-rate simulation.

Why divergence-free matters specifically for drifter-PF simulations: a
compressible velocity field accumulates drifters in convergence regions
over time, producing clustering artifacts that poison the PF's
bootstrap-importance-weight dynamics. A divergence-free field has no
sinks or sources and preserves Lebesgue measure on the drifter
distribution — what you put in, you get out, structure-wise.

## Goals / Non-Goals

**Goals:**
- Add a seeded-deterministic, divergence-free, k^(-5/3)-spectral
  submesoscale velocity layer that composes on top of `RealCurrentField`.
- Keep the PF's consumer path unchanged: composition happens at scenario-gen
  assembly; PF reads the composed truth through a single `CurrentField`
  Protocol surface.
- Thread the operator's expected submesoscale energy into the onboard
  climatology's variance channel — the prior knows variability exists;
  it does not know the realization.
- Verifiably honor the physics: divergence-free, spectrum-slope-correct,
  amplitude-matches-spec, reproducible-under-seed.
- Make the submesoscale layer's contribution visually apparent on the
  dashboard (truth-chaos overlay) — same enforcement-over-instruction
  principle as Change 1's truth-vs-climatology overlay.

**Non-Goals:**
- Non-Gaussian statistics. The GRF model captures energy but not
  heavy-tailed eddy events (extremes). Operationally relevant for M2+
  work but out of scope for Change 2.
- Vertical structure / 3D flow. Stream-function construction is 2D; all
  drifters in M1 are surface / fixed-depth. 3D submesoscale is a
  different model.
- Fitting model parameters to observations. Defaults come from literature
  (Poje 2017 amplitudes + Reijnders 2022 correlation lengths); we don't
  claim to calibrate against real drifter data in this change.
- Injecting submesoscale energy into the onboard climatology's
  `mean_vxvy`. The climatology is a *prior*; its mean is the
  operator-known resolvable component. Submesoscale goes into `var`,
  not `mean`.
- Overhauling PF predict-step dynamics to use a richer-than-diagonal
  covariance from climatology. That's Stage-3-revisit work after
  Change 2 lands.

## Decisions

### D1 — Markov-1 / OU-on-stream-function (not Markov-0 random walk, not Markov-2 acceleration)

**Decision:** Sub-grid velocity is an OU process on the stream function
`ψ`, with velocities derived as `(u, v) = (∂ψ/∂y, -∂ψ/∂x)`.

**Rationale:**
- Markov-0 (iid per-tick random velocities) has no Lagrangian correlation;
  drifters execute random walks with no structure. Rejected by Poje 2017
  data.
- Markov-2 (OU on acceleration) adds a parameter (drag timescale) and
  complexity without observational support at the scales we care about.
  Overkill for Change 2.
- Markov-1 on velocity produces Lagrangian trajectories with the observed
  integral timescale. Stream-function form guarantees divergence-free
  without extra projection step.

**Alternatives considered:**
- OU directly on velocity (without stream function) — rejected: would
  require a post-hoc Helmholtz decomposition per tick to project out
  divergent component. Stream-function form is simpler and exact.
- Random-phase spectral synthesis without temporal evolution — rejected:
  produces a new iid field per tick, failing the Lagrangian-correlation
  requirement.

### D2 — Spectral synthesis with k^(-5/3) energy spectrum + Gaussian coherence window

**Decision:** Stream-function `ψ(x, y)` on a regular internal grid
(default 256×256) is synthesized as the inverse FFT of spectral
coefficients `ψ̂(k) ~ 𝒩(0, E(k))` with:
```
E(k) = A · k^(-5/3) · exp(-(k · L_c / (2π))²)
```
The exponential window provides the correlation length `L_c` (suppresses
power above wavenumber ~2π/L_c) and prevents spectrum-integral divergence
at low k.

**Rationale:** Explicit spectral control of amplitude + correlation
length. The FFT approach scales as `O(N² log N)` per tick — 256² is
~0.4 M points, well within budget.

**Parameters (regime-tunable):**

| Regime       | σ_v (m/s) | L_c (m)   | τ_c (min) |
|--------------|-----------|-----------|-----------|
| Open ocean   | 0.05–0.10 | 1000–2000 | 30–60     |
| Coastal/shelf (default) | 0.10–0.15 | 500–1000 | 15–30 |
| Frontal zone | 0.20–0.50 | 100–500   | 5–15      |

### D3 — OU temporal evolution on the spectral coefficients

**Decision:** Per-mode coefficient evolves as:
```
ψ̂(k, t+Δt) = α(Δt) · ψ̂(k, t) + β(Δt) · η(k)
```
where `α(Δt) = exp(-Δt/τ_c)`, `β(Δt) = sqrt(1 - α²)`, and `η(k) ~ 𝒩(0,
E(k))` is a fresh independent spectral sample. For τ_c ≫ Δt the field
decorrelates slowly (persistent structure); for τ_c ≪ Δt it
decorrelates almost fully each tick (approaches Markov-0).

**Rationale:** This is the exact OU update at each mode, preserving the
stationary spectrum by construction. Computationally it's two complex
multiplications per mode — negligible cost.

### D4 — Internal grid is 1.5× bbox, periodic, interpolated to query points

**Decision:** Allocate a 1.5× padded internal grid centered on the
scenario bbox. FFT operates under periodic boundaries (required for
efficient synthesis). Reflection / ringing from the periodic boundary
enters the padded region but decays toward the center; the scenario
bbox sits in the non-padded interior. Query points are interpolated via
`scipy.interpolate.RegularGridInterpolator` on the interior grid.

**Rationale:** Non-periodic spectral synthesis is expensive; periodic
synthesis + padding is the standard turbulence-sim trick.

**Alternative considered:**
- Open-boundary Cholesky on covariance matrix — rejected: `O(N^6)` for
  dense covariance at 256² grid; infeasible.

### D5 — Composition via `CompositeCurrentField` wrapper at scenario-gen assembly

**Decision:** New helper class `CompositeCurrentField` (in
`rtl/vectors/maritime/current_fields_composite.py` or inlined in
`gen_maritime_scenario.py`):

```python
@dataclass
class CompositeCurrentField:
    base: CurrentField  # e.g., RealCurrentField or SyntheticEddyField
    addition: CurrentField  # e.g., SpectralSubmesoscaleField

    def velocity_at(self, lat_deg, lon_deg, t_sec) -> tuple[float, float]:
        u1, v1 = self.base.velocity_at(lat_deg, lon_deg, t_sec)
        u2, v2 = self.addition.velocity_at(lat_deg, lon_deg, t_sec)
        return (u1 + u2, v1 + v2)
```

Scenario-gen builds the composite when `--submesoscale-amplitude-ms > 0`.
When amplitude is 0, `base` alone is used (no composite wrapper).

**Rationale:** Keeps `RealCurrentField` single-responsibility (grid
loader). Keeps the PF's consumer path identical — it sees a
`CurrentField`, doesn't care if it's composed. Keeps provenance-
independence clean: `RealCurrentField` doesn't import
`SpectralSubmesoscaleField`; the composite knows both but is itself a
truth-side object only.

**Alternative considered:**
- Add submesoscale as an attribute on `RealCurrentField` — rejected:
  couples two distinct responsibilities (grid I/O + stochastic synthesis)
  and complicates the Change-1 test matrix.

### D6 — Climatology's `submesoscale_energy_ms` is a scalar, variance-only

**Decision:** `ClimatologySource` gains an optional attribute
`submesoscale_energy_ms: float = 0.0`. `HarmonicClimatology`'s
`velocity_at(lat, lon, t_sec) -> (mean_vx, mean_vy, var_vx, var_vy)`
returns `var_vx = gridded_var_vx + submesoscale_energy_ms²` (and
similarly for `var_vy`). This captures the operator's prior belief
"we expect ~σ m/s of unresolved motion" without that prior ever seeing
the truth realization.

**Rationale:** A spatially-constant scalar is simpler than a gridded
field and matches the "operator's prior knowledge from regional
surveys" semantics — you know there's submesoscale activity; you don't
know where. Future gridded-expected-submesoscale work (from fleet-learned
observations) fits behind the same Protocol interface in M2+.

**Explicit non-coupling:** `submesoscale_energy_ms` is supplied via a
separate CLI flag (`--climatology-expected-submesoscale-ms`) whose
default happens to match `--submesoscale-amplitude-ms` but can be
decoupled. Decoupling enables the "prior wrong about expected amplitude"
experiment (operator expected 0.1 m/s, reality is 0.3 m/s) that would
be silently prevented by a single-knob design.

### D7 — Divergence-free enforced by construction + verified numerically

**Decision:** `(u, v) = (∂ψ/∂y, -∂ψ/∂x)` is analytically divergence-free
(`∂u/∂x + ∂v/∂y = ∂²ψ/∂x∂y - ∂²ψ/∂y∂x = 0`). Spatial derivatives are
computed in Fourier space (multiply by `ikx` / `iky` before IFFT) so the
discrete implementation preserves the invariant exactly up to FFT
roundoff. A substance test computes numerical divergence on the internal
grid and asserts `< 1e-6` m/s per m at every cell.

### D8 — Sidecar chaos arrays for dashboard

**Decision:** Change 1's `current_field_grid.npz` sidecar gains two new
arrays:
- `truth_grid_chaos_u[t, i, j]` — submesoscale-only eastward component
  at tick `t`, grid cell `(i, j)`.
- `truth_grid_chaos_v[t, i, j]` — northward counterpart.

Total truth remains `truth_grid_u = grid_interp_u + chaos_u` and
similarly for `v` (so Change 1's consumers see the sum without
downstream changes). The chaos arrays power the optional dashboard
overlay "Truth current chaos".

### D9 — Performance budget: 5 ms per tick at default config

**Decision:** Target budget is 5 ms per `SpectralSubmesoscaleField.step()`
call at 256×256 grid, τ_c = 20 min, Δt = 60 s. Implementation uses
numpy FFT (`scipy.fft.fft2` + `scipy.fft.ifft2`). If the budget is not
met, fall back to coarsening the internal grid (256 → 128) before
considering other optimizations.

**Rationale:** At 5 ms × 86400 s / 60 s ≈ 7.2 s of wall-clock added per
24-hour scenario. Negligible relative to other scenario-gen costs
(sensor sim, JSONL writes). The budget is a guardrail against a naive
per-mode-Python-loop implementation.

### D10 — Verification of spectrum slope: empirical 2D power spectrum of generated field

**Decision:** A substance test generates ~100 independent spectral-layer
samples, computes the 2D power spectrum of each, radially averages, fits
a log-log slope over the inertial range (say, `k / (2π) ∈ [0.001, 0.01]
m^(-1)`), asserts slope ∈ `[-2.0, -1.3]` (generous tolerance around -5/3).

**Rationale:** Generous tolerance reflects finite-sample variance on
100 realizations; tightening requires more samples or a larger internal
grid. -5/3 = -1.667 is within the tolerance. The test catches "I
implemented k^(-1) by accident" bugs, not "my slope is -1.72 instead of
-1.667".

## Key Type Contracts

### Requirement: SpectralSubmesoscaleField (new, maritime-submesoscale-truth-variability)

```python
# rtl/vectors/maritime/current_fields_submesoscale.py
from rtl.vectors.maritime.current_fields import CurrentField

@dataclass
class SubmesoscaleConfig:
    amplitude_ms: float             # rms velocity σ
    correlation_length_m: float     # L_c
    correlation_time_sec: float     # τ_c
    spectrum_slope: float = -5/3    # Kolmogorov default
    grid_points: int = 256
    seed: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        # amplitude_ms >= 0; correlation_length_m > 0; correlation_time_sec > 0;
        # grid_points >= 32; spectrum_slope in [-3.0, -1.0] (physically
        # plausible range).
        ...

@dataclass
class SpectralSubmesoscaleField:
    config: SubmesoscaleConfig
    _internal_lats: np.ndarray      # (grid_points,) on 1.5x padded bbox
    _internal_lons: np.ndarray
    _times_sec: list[float]         # tick times where the field has been evaluated
    _psi_cache: dict[float, np.ndarray]  # t_sec → ψ(x, y) grid
    _interp_u_cache: dict[float, RegularGridInterpolator]
    _interp_v_cache: dict[float, RegularGridInterpolator]
    _rng: np.random.Generator
    _spectral_coeffs: np.ndarray    # current OU state, (grid_points, grid_points)

    def step(self, dt_sec: float) -> None:
        # Advance OU state; regenerate ψ; take spatial derivatives;
        # update interpolator cache for the new time.
        ...

    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float]:
        # Look up the cached interpolator at t_sec (or step to it if
        # newer); return (vx, vy) via RegularGridInterpolator.
        ...

# Satisfies CurrentField Protocol (duck-typed on velocity_at).
```

### Requirement: CompositeCurrentField (new, maritime-current-fields modification)

```python
# rtl/vectors/maritime/current_fields_composite.py (or inlined)
@dataclass
class CompositeCurrentField:
    base: CurrentField       # RealCurrentField or SyntheticEddyField
    addition: CurrentField   # SpectralSubmesoscaleField

    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float]:
        u1, v1 = self.base.velocity_at(lat_deg, lon_deg, t_sec)
        u2, v2 = self.addition.velocity_at(lat_deg, lon_deg, t_sec)
        return (u1 + u2, v1 + v2)
```

### Requirement: ClimatologySource gains submesoscale_energy_ms (modified)

```python
# rtl/vectors/maritime/climatology_source.py — MODIFIED

@dataclass
class HarmonicClimatology:
    # ... existing fields from Change 1 ...
    submesoscale_energy_ms: float = 0.0  # NEW — scalar expected-rms-σ

    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float, float, float]:
        mean_vx, mean_vy, gridded_var_vx, gridded_var_vy = self._base_lookup(
            lat_deg, lon_deg, t_sec
        )
        extra_var = self.submesoscale_energy_ms ** 2
        return (
            mean_vx, mean_vy,
            gridded_var_vx + extra_var,
            gridded_var_vy + extra_var,
        )
```

### Construction invariants

- `SubmesoscaleConfig.__post_init__`: non-negative amplitude, positive
  correlation length/time, grid_points ≥ 32, spectrum_slope ∈ [-3, -1].
- `SpectralSubmesoscaleField.__post_init__`: initial spectral state
  sampled per configured spectrum; internal grid allocated; first
  interpolator built at `t_sec = 0`.
- `HarmonicClimatology.__post_init__` (extended): rejects negative
  `submesoscale_energy_ms`.

## Risks / Trade-offs

**[Risk] Spectral-synthesis implementation bug (wrong slope, broken
divergence-free) silently produces plausible-looking but unphysical
truth.** → Mitigation: substance tests D7 (numerical divergence) and D10
(empirical spectrum slope) catch both failure modes with explicit
numerical criteria.

**[Risk] 256×256 grid is too small for large bboxes (coarse submesoscale
resolution in scenario domain).** → Mitigation: `--submesoscale-grid-points`
CLI flag lets users scale up for large-bbox scenarios (paying the
`O(N² log N)` cost). Default 256 is fine for the bundled Salish and
offshore-VI fixtures.

**[Risk] OU state reset across scenario regeneration breaks byte-identity
(RNG stream order).** → Mitigation: derive submesoscale seed
deterministically from the main seed; use an independent RNG instance
for the submesoscale (no shared stream with sensor / dynamics RNGs).

**[Risk] Climatology's `var_vx`/`var_vy` were previously used by the PF
for per-tick velocity σ sampling; adding `submesoscale_energy_ms²` to
the returned variance broadens the PF's velocity cloud, changing
existing test tolerances.** → Mitigation: default
`--climatology-expected-submesoscale-ms 0.0` in CI-default synthetic
scenarios (no submesoscale at all); golden-trace uses synthetic path
so byte-identity is preserved. Real-data scenarios use a non-zero
default matching truth amplitude; their PF tests use fixture-specific
tolerances computed from the expected σ.

**[Risk] Dashboard "chaos overlay" visually overwhelms the main truth
view.** → Mitigation: overlay is opt-in via a toggle (like the Change 1
overlays); default OFF; the visual design uses subtle differential
arrows (e.g., translucent gray) so normal dashboard use isn't disrupted.

**[Risk] Spectral-synthesis FFT cost adds material wall-clock to
scenario-gen.** → Mitigation: budget of 5 ms / tick × 1440 ticks / day
= 7.2 s total per 24h scenario. Acceptable for current workflow.
Long-horizon scenarios (week+) would need the coarser-grid fallback or
a lazy-evaluation path (only compute tiles around drifter positions).

## Migration Plan

1. Implement `SpectralSubmesoscaleField` + config + substance tests
   (divergence, spectrum, amplitude, reproducibility). No scenario-gen
   integration yet.
2. Add `CompositeCurrentField` + tests (additivity, both components'
   `velocity_at` contributions).
3. Extend `HarmonicClimatology` with `submesoscale_energy_ms`; update
   variance-return logic; add test.
4. Wire scenario-gen CLI flags; route composition at scenario assembly.
5. Extend sidecar emission with `truth_grid_chaos_u/v`.
6. Extend dashboard with chaos overlay + toggle.
7. End-to-end: run bundled Salish fixture with submesoscale on, verify
   dashboard visuals, verify PF still completes (ESS > 0, no NaN,
   reasonable RMSE envelope).

## Open Questions

- **Radial-averaging bin choice for spectrum-slope test**: depends on
  grid size and bbox aspect ratio; tune during implementation.
- **Should submesoscale be applied to the synthetic path too?** Default
  no — synthetic path is for CI reproducibility, not realism. But an
  opt-in flag `--submesoscale-amplitude-ms` already gates this; setting
  it to non-zero in the synthetic path should work without code changes.
- **OU correlation-time regime for frontal zones (τ_c ~ 5–15 min)**:
  default doesn't cover this; expose via CLI and let users pick. No
  calibration effort in Change 2.
