## ADDED Requirements

### Requirement: Real Current Field Protocol Conformance
The system SHALL provide a real-data implementation of the `CurrentField` protocol at `rtl/vectors/maritime/current_fields_real.py::RealCurrentField`. See `maritime-real-current-data` spec for the full NetCDF loading, format-polymorphism, and provenance contracts. The `CurrentField` Protocol defined in this capability is unchanged — any conforming implementation (synthetic, real, or test double) supplies `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]`.

#### Scenario: Real and synthetic implementations both satisfy the Protocol
- **WHEN** a function typed on the `CurrentField` Protocol is called with either a `SyntheticEddyField` instance or a `RealCurrentField` instance
- **THEN** both calls type-check under pyright strict
- **AND** both `isinstance(instance, CurrentField)` checks return True at runtime

#### Scenario: Scenario generator composes either implementation interchangeably
- **WHEN** the scenario generator's field-construction path is invoked with `--current-source synthetic` or `--current-source real`
- **THEN** in both cases the resulting field object exposes `velocity_at(lat, lon, t)` with the same signature
- **AND** downstream truth-propagation code (`propagate_truth`, sensor simulators) consumes it identically
