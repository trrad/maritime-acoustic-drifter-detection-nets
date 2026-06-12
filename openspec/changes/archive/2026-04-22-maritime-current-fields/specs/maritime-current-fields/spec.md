## ADDED Requirements

### Requirement: CurrentField Protocol
The system SHALL define a `CurrentField` protocol with a method `velocity_at(lat_deg, lon_deg, t_sec)` that returns a tuple of (vx_ms, vy_ms) representing the eastward and northward current velocity at the given geographic position and time. Any current field implementation (synthetic, HYCOM, or test double) SHALL satisfy this protocol by implementing this method signature.

#### Scenario: Protocol is satisfied by a test double
- **WHEN** a class implements `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]`
- **THEN** it satisfies the `CurrentField` protocol without inheritance

### Requirement: Synthetic Eddy Field Velocities
The `SyntheticEddyField` SHALL compute current velocity as the superposition of a uniform mean flow, smooth spatially-bounded eddy contributions, and an M2 tidal oscillation. Each eddy SHALL produce tangential (non-radial) flow with zero velocity at the eddy center and a smooth radial decay. The total velocity SHALL be physically reasonable: between -2.0 and +2.0 m/s for typical configuration parameters.

#### Scenario: Mean flow only (no eddies, no tide)
- **WHEN** a `SyntheticEddyField` is configured with `mean_vx_ms = 0.1, mean_vy_ms = -0.05` and no eddies or tidal component
- **THEN** `velocity_at` returns `(0.1, -0.05)` at any position and time

#### Scenario: Eddy adds spatially-varying tangential velocity
- **WHEN** a single cyclonic eddy with `radius_m = 10000, peak_velocity_ms = 0.3` is centered at (36.75, -122.0)
- **AND** velocity is queried at a point 10 km east of the eddy center
- **THEN** the velocity has a nonzero northward component (tangential to the eddy center)
- **AND** the eddy's velocity contribution at this distance equals the peak velocity times the radial decay factor, within 0.01 m/s

#### Scenario: Velocity at eddy center is purely from mean flow
- **WHEN** velocity is queried at the exact center of an eddy
- **THEN** the eddy contributes zero tangential velocity
- **AND** the total velocity equals the mean flow plus tidal component only

#### Scenario: Eddy velocity decays smoothly with distance
- **WHEN** velocity is queried at 0.5×, 1.0×, and 2.0× the eddy radius from center
- **THEN** the velocity magnitudes are monotonically non-increasing with distance (smooth decay, no discontinuities)

#### Scenario: M2 tide oscillates with correct period
- **WHEN** tidal amplitude is set to 0.1 m/s with default M2 period (44712 s)
- **AND** velocity is queried at `t_sec = 0` and `t_sec = 44712 / 4` (quarter period)
- **THEN** the tidal contribution at quarter period has magnitude within 0.01 m/s of the configured amplitude

#### Scenario: Tidal direction is configurable
- **WHEN** tidal direction is set to 90° (northward)
- **AND** velocity is queried at quarter period
- **THEN** the tidal velocity is predominantly northward (vy ≈ amplitude, vx ≈ 0)

### Requirement: Synthetic Eddy Field Returns Physically Reasonable Velocities
For any query within the simulation bbox with typical parameters (mean flow < 0.5 m/s, eddy peaks < 0.5 m/s, tidal amplitude < 0.3 m/s), the total velocity magnitude SHALL be less than 2.0 m/s.

#### Scenario: No extreme velocities with typical parameters
- **WHEN** the field is configured with 3 eddies of peak velocity 0.4 m/s, mean flow 0.2 m/s, and tidal amplitude 0.15 m/s
- **AND** velocity is queried at 100 random points within the bbox and 10 random time values
- **THEN** all velocity magnitudes are less than 2.0 m/s

### Requirement: Advection Accuracy Through Synthetic Field
Lagrangian advection (forward Euler integration) through the synthetic current field SHALL match the analytical trajectory within 5 m over a 60-second integration period at 1 Hz update rate. This validates that the velocity field is smooth and the integration is accurate at the time scales used in the scenario generator.

#### Scenario: Advection in mean flow matches analytical displacement
- **WHEN** a particle is advected for 60 seconds through a constant mean flow field of (0.1, 0.0) m/s with 1 Hz time steps
- **THEN** the final ENU position is within 5 m of (6.0, 0.0) meters — i.e., 0.1 m/s × 60 s = 6 m east

#### Scenario: Advection through eddy field remains bounded
- **WHEN** a particle is advected for 300 seconds through an eddy field (no mean flow, no tide) starting at the eddy edge
- **THEN** the particle position remains within 2× the eddy radius of the starting point (eddy orbits are closed trajectories)
