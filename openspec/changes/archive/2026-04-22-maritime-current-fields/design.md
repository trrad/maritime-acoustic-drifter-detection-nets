## Context

Maritime nodes drift with the ocean current. The truth propagation (dynamics.py) needs to know the current velocity at each node's position and time to advect the state forward. The particle filter's predict step needs the same current field to propagate particles.

The synthetic field provides three physically-motivated components:
1. **Mean flow**: constant background velocity (e.g., California Current at ~0.1 m/s southward)
2. **Gaussian eddies**: circular velocity perturbations modeling mesoscale eddies (10-50 km radius, 0.1-0.5 m/s peak)
3. **M2 tidal oscillation**: periodic velocity modulation (period ~12.42 hours, amplitude ~0.05-0.2 m/s)

The `CurrentField` protocol defines the interface that both synthetic and future real-data sources implement.

## Goals / Non-Goals

**Goals:**
- `CurrentField` protocol with a single `velocity_at(lat, lon, t_sec)` method returning (vx_ms, vy_ms)
- `SyntheticEddyField` implementing the protocol with configurable mean flow, eddies, and tidal parameters
- Synthetic field velocities are physically reasonable (0-1 m/s for typical parameters)
- Lagrangian advection through the field matches analytical integration within 5 m over 60 s

**Non-Goals:**
- HYCOM/Copernicus data loading (future M3 work)
- Parcels Lagrangian integration (future M3 work)
- Current field visualization (dashboard responsibility)
- Bathymetry-influenced currents (depth-dependent flow modeling)
- Spatial gradient bounds or divergence constraints (the synthetic field is analytically smooth by construction)
- Wind-driven surface current component (synthetic field models mesoscale and tidal flow only)

## Decisions

### D1: Protocol class (not ABC) for CurrentField

**Choice:** Use a `typing.Protocol` with a single method `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]`.

**Why:** Structural subtyping — the future HYCOM field doesn't need to inherit from a base class, it just needs to implement the method. No coupling. Protocol is the right Python pattern for this.

### D2: Gaussian eddy model — rank-0 vortex with tangential velocity

**Choice:** Each eddy is defined by center (lat, lon), radius_m, peak_velocity_ms, and sign (cyclonic/anticyclonic). Tangential velocity follows a Gaussian profile: `v_t(r) = v_peak * exp(-r² / (2 * sigma²))` where sigma = radius_m.

**Why:** Gaussian profile is smooth (differentiable, good for integration), bounded (no singularity at center unlike point vortex), and physically reasonable for mesoscale eddies. The velocity at the center is zero (rank-0 vortex) which avoids unrealistic fast flow at eddy centers.

### D3: M2 tide as uniform oscillation

**Choice:** Single sinusoidal velocity modulation: `v_tidal(t) = amplitude * sin(2π * t / T_M2)` with T_M2 = 44712 seconds (12.42 hours). Applied uniformly across the field (no spatial variation). Direction configurable via `tidal_direction_deg` (math convention: 0° = east, 90° = north; default 0° = eastward).

**Why:** The M2 constituent dominates tidal currents in most open-ocean areas. Spatial variation of tidal currents requires regional tidal models (TPXO etc.) — deferred to M3. The uniform oscillation captures the dominant temporal signal. Direction is configurable because real tidal currents flow along a principal axis that varies by region.

### D4: Field configuration via dataclass, not CLI arguments

**Choice:** `SyntheticEddyField` takes a `FieldConfig` dataclass with mean flow, eddy list, and tidal parameters. The scenario generator's CLI constructs the config from arguments.

**Why:** Separates the field definition from the CLI parsing. Tests construct fields programmatically with known parameters. The CLI is a thin wrapper.

## Risks / Trade-offs

- **[Risk] Synthetic field is too simple for meaningful PF testing** → Acceptable for M1. The synthetic field exercises the dynamics and PF machinery. Realistic current structure matters more for M3 (HYCOM comparison).
- **[Risk] Uniform tide doesn't test spatial variation** → The eddies provide spatial variation. The tide provides temporal variation. Together they test both dimensions of the PF's current estimation.
- **[Trade-off] Gaussian eddy profile vs Rankine vortex** → Gaussian is smoother and more realistic for open-ocean mesoscale. Rankine has a sharp velocity peak at the radius — simpler but less physical.

## Key Type Contracts

```
class CurrentField(Protocol):
    def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]:
        """Returns (vx_ms, vy_ms) — east and north current velocity at the given point and time."""
        ...

@dataclass
class EddySpec:
    center_lat_deg: float
    center_lon_deg: float
    radius_m: float          # Gaussian sigma
    peak_velocity_ms: float  # Max tangential speed at r = sigma
    cyclonic: bool           # True = counterclockwise (NH), False = clockwise

@dataclass
class FieldConfig:
    mean_vx_ms: float = 0.0        # Background eastward velocity
    mean_vy_ms: float = 0.0        # Background northward velocity
    eddies: list[EddySpec] = field(default_factory=list)
    tidal_amplitude_ms: float = 0.0
    tidal_period_sec: float = 44712.0  # M2 default
    tidal_direction_deg: float = 0.0   # Math convention: 0°=east, 90°=north

class SyntheticEddyField:
    def __init__(self, config: FieldConfig) -> None: ...
    def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]: ...
```
