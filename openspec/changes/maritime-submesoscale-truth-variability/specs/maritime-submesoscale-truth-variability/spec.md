## ADDED Requirements

### Requirement: SpectralSubmesoscaleField Class
The system SHALL provide a `SpectralSubmesoscaleField` class in `rtl/vectors/maritime/current_fields_submesoscale.py` that generates a seeded-deterministic 2D stochastic velocity field matching the `CurrentField` protocol. The field SHALL be constructed via Fourier-space synthesis of a stream function `ψ(x, y)` with an energy spectrum following `E(k) ∝ k^(spectrum_slope)` (default `spectrum_slope = -5/3`, Kolmogorov) multiplied by a Gaussian coherence window of characteristic length `correlation_length_m`. Velocities SHALL be derived as `(u, v) = (∂ψ/∂y, -∂ψ/∂x)` via spectral differentiation (multiply by `iky`, `-ikx` before IFFT), making the field analytically divergence-free. The class SHALL satisfy the `CurrentField` Protocol (`velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]`).

#### Scenario: SpectralSubmesoscaleField satisfies CurrentField Protocol
- **WHEN** a `SpectralSubmesoscaleField` is constructed with a default `SubmesoscaleConfig` and tested with `isinstance(instance, CurrentField)` (the `@runtime_checkable` Protocol from `maritime-current-fields`)
- **THEN** the check returns True
- **AND** a function typed on `CurrentField` accepts the instance without type-check failure

#### Scenario: Zero amplitude produces zero velocity
- **WHEN** `SpectralSubmesoscaleField` is configured with `amplitude_ms = 0.0`
- **AND** `velocity_at` is called at any `(lat, lon, t)`
- **THEN** the returned `(vx, vy)` is `(0.0, 0.0)` within 1e-9 m/s

### Requirement: Submesoscale Amplitude Matches Configuration
The rms velocity magnitude of the generated field SHALL match the configured `amplitude_ms` within ±20% when measured over a sufficient sample of realizations at random `(lat, lon, t)` points inside the scenario bbox. This ±20% tolerance reflects finite-sample variance in the spectral synthesis; over many seeds, the ensemble mean SHALL converge to `amplitude_ms`.

#### Scenario: Measured rms velocity is within 20% of configured amplitude
- **WHEN** `SpectralSubmesoscaleField` is configured with `amplitude_ms = 0.15` and evaluated at 1000 random `(lat, lon)` inside the bbox across 100 distinct `t_sec` values
- **THEN** `sqrt(mean(vx² + vy²))` computed over all samples is in the range `[0.12, 0.18]` m/s
- **AND** the sample mean of both `vx` and `vy` is within `3 * (0.15 / sqrt(n))` of zero (zero-mean field, finite-sample check)

### Requirement: Divergence-Free Field
The numerical divergence `∂u/∂x + ∂v/∂y` of the generated field SHALL be less than `1e-6 m/s per m` at every cell of the internal synthesis grid. This is the physical invariant that prevents drifters from accumulating in spurious convergence regions and corrupting the PF's bootstrap importance-weight dynamics.

#### Scenario: Numerical divergence is negligible on the internal grid
- **WHEN** a `SpectralSubmesoscaleField` is stepped once and its internal `(u, v)` grids are inspected
- **AND** the discrete divergence is computed via centered finite differences (or the exact Fourier-space expression)
- **THEN** `max(|∂u/∂x + ∂v/∂y|)` over all cells is less than `1e-6` m/s per m
- **AND** this holds across at least 10 consecutive `step(dt_sec)` calls with default configuration

### Requirement: Kolmogorov Energy Spectrum
The 2D power spectrum of the generated field SHALL have a log-log slope within `[-2.0, -1.3]` (generous tolerance around the Kolmogorov -5/3 target) over the inertial range of the spectrum, measured by radially averaging the 2D power spectrum and fitting a linear regression in log-log space.

#### Scenario: Spectrum slope matches Kolmogorov within tolerance
- **WHEN** 100 independent realizations of `SpectralSubmesoscaleField` are generated (different seeds, same config) with `spectrum_slope = -5/3`
- **AND** each realization's 2D power spectrum is computed via `np.abs(fft2(field))**2`, radially averaged over wavenumber bins, and log-log-regressed over the inertial range (e.g., `k * L_c ∈ [0.3, 3.0]`, avoiding grid-scale and largest-scale contamination)
- **THEN** the ensemble mean slope is within the range `[-2.0, -1.3]` (inclusive)

### Requirement: Ornstein-Uhlenbeck Temporal Evolution
The spectral coefficients SHALL evolve as an Ornstein-Uhlenbeck process with relaxation time `correlation_time_sec`. Per-tick update SHALL be `ψ̂(k, t+Δt) = α · ψ̂(k, t) + β · η(k)` where `α = exp(-Δt/τ_c)`, `β = sqrt(1 - α²)`, and `η(k)` is a fresh independent spectral sample from the stationary spectrum. This SHALL preserve the stationary spectrum across all tick times (OU is variance-preserving when `α² + β² = 1`).

#### Scenario: OU update preserves stationary spectrum
- **WHEN** a `SpectralSubmesoscaleField` is stepped for 1000 ticks at `dt_sec = 60` with `correlation_time_sec = 1200`
- **AND** the field's rms amplitude is measured at ticks 0, 100, 500, 1000
- **THEN** all four measurements are within ±15% of the configured `amplitude_ms` (no drift, no runaway growth, no collapse)

#### Scenario: Long-correlation regime produces persistent spatial structure
- **WHEN** a `SpectralSubmesoscaleField` is stepped with `correlation_time_sec = 3600` at `dt_sec = 60` for 10 ticks
- **AND** the field's spatial correlation between tick 0 and tick 10 is measured (e.g., pearson correlation of the `u` arrays)
- **THEN** the correlation exceeds 0.7 (field has not fully decorrelated over one sixth of a correlation time)

#### Scenario: Short-correlation regime approaches Markov-0 (iid-per-tick)
- **WHEN** a `SpectralSubmesoscaleField` is stepped with `correlation_time_sec = 30` at `dt_sec = 60` for 2 ticks
- **AND** the spatial correlation between tick 0 and tick 1 is measured
- **THEN** the correlation is below 0.3 (field has substantially decorrelated over more than one correlation time per step)

### Requirement: Seeded Determinism
The field SHALL be exactly reproducible under a given `(seed, bbox, config, tick-grid)` tuple. Two `SpectralSubmesoscaleField` instances constructed with identical parameters and stepped through the same tick times SHALL produce byte-identical `(u, v)` arrays at every grid cell.

#### Scenario: Byte-identical field across repeated scenario generation
- **WHEN** two `SpectralSubmesoscaleField` instances are constructed with the same config, the same seed, and stepped through the same 10-tick sequence
- **AND** their internal `(u, v)` grids are compared after each step
- **THEN** the arrays are byte-identical (element-wise exact equality) at every tick

### Requirement: Composition via CompositeCurrentField
The system SHALL provide a `CompositeCurrentField` dataclass that wraps a `(base, addition)` pair of `CurrentField`-conforming objects and returns `velocity_at(lat, lon, t) = base.velocity_at(...) + addition.velocity_at(...)` as the element-wise tuple sum. The composite SHALL satisfy the `CurrentField` Protocol so PF / sensor-sim consumers see a uniform interface regardless of whether composition is in effect.

#### Scenario: Composite returns additive velocity
- **WHEN** a `CompositeCurrentField(base=mock_base, addition=mock_addition)` is evaluated where `mock_base.velocity_at` returns `(0.1, 0.2)` and `mock_addition.velocity_at` returns `(0.03, -0.01)`
- **THEN** `composite.velocity_at(lat, lon, t)` returns `(0.13, 0.19)` within 1e-9 m/s

#### Scenario: Composite satisfies CurrentField Protocol
- **WHEN** `CompositeCurrentField` is tested with `isinstance(composite, CurrentField)`
- **THEN** the check returns True

### Requirement: CLI Wire-Up for Submesoscale
The scenario generator CLI (`rtl/vectors/maritime/gen_maritime_scenario.py`) SHALL accept the following flags for configuring the submesoscale layer:

- `--submesoscale-amplitude-ms` (float, default 0.1) — rms σ of the submesoscale velocity field in m/s. Setting 0 disables the submesoscale layer (scenario uses base field alone).
- `--submesoscale-correlation-length-m` (float, default 750.0) — spatial correlation length L_c in meters.
- `--submesoscale-correlation-time-sec` (float, default 1200.0 = 20 min) — OU correlation time τ_c in seconds.
- `--submesoscale-spectrum-slope` (float, default -1.667) — energy spectrum power-law slope.
- `--submesoscale-seed` (int, optional, default derived deterministically from `--seed`) — independent RNG stream seed for the submesoscale field.
- `--submesoscale-grid-points` (int, default 256) — internal FFT grid size.

All submesoscale parameters SHALL be recorded in the scenario header so downstream analysis can reconstruct the regime.

#### Scenario: --submesoscale-amplitude-ms 0 disables composition
- **WHEN** the CLI is invoked with `--submesoscale-amplitude-ms 0.0`
- **THEN** the scenario's truth field is the base `CurrentField` alone (no `CompositeCurrentField` wrapper)
- **AND** the emitted `current_field_grid.npz` sidecar's `truth_grid_chaos_u` and `truth_grid_chaos_v` arrays (when present for schema consistency) equal zero within 1e-9 at every cell

#### Scenario: Non-zero amplitude activates composition
- **WHEN** the CLI is invoked with `--submesoscale-amplitude-ms 0.15 --submesoscale-correlation-length-m 500 --submesoscale-correlation-time-sec 600`
- **THEN** the scenario's truth field is a `CompositeCurrentField` of the base field + a `SpectralSubmesoscaleField` configured with those parameters
- **AND** sampled truth velocities differ from the base field alone by a distribution whose rms magnitude is within ±20% of 0.15 m/s across random query points

#### Scenario: Header records submesoscale parameters
- **WHEN** a scenario is generated with non-zero submesoscale amplitude
- **THEN** the scenario header includes fields for every submesoscale CLI parameter (amplitude, correlation length, correlation time, slope, seed, grid points)
- **AND** setting `--submesoscale-amplitude-ms 0` SHALL still produce a header recording the (unused) amplitude-0 state, for reproducibility self-documentation
