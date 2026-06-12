## ADDED Requirements

### Requirement: Composite Current Field Pattern
The system SHALL support composing multiple `CurrentField`-conforming implementations via a `CompositeCurrentField` wrapper whose `velocity_at(lat, lon, t)` returns the element-wise sum of its constituents' velocities. This enables layering a stochastic submesoscale variability field (see `maritime-submesoscale-truth-variability`) on top of a grid-resolving base field (e.g., `RealCurrentField` or `SyntheticEddyField`) without modifying the base implementation. The composite SHALL itself satisfy the `CurrentField` Protocol.

#### Scenario: Composite satisfies the CurrentField Protocol
- **WHEN** `CompositeCurrentField(base, addition)` is instantiated with any two `CurrentField`-conforming objects and tested with `isinstance(composite, CurrentField)`
- **THEN** the check returns True

#### Scenario: Composition is additive
- **WHEN** `composite.velocity_at(lat, lon, t)` is called on a composite whose constituents return `(u_base, v_base)` and `(u_add, v_add)` at that point
- **THEN** the returned tuple equals `(u_base + u_add, v_base + v_add)` within 1e-9 m/s

#### Scenario: Composition is associative and preserves type
- **WHEN** three `CurrentField` objects are composed as `Composite(Composite(A, B), C)` or `Composite(A, Composite(B, C))`
- **THEN** both orderings satisfy the `CurrentField` Protocol
- **AND** both return identical velocities at any `(lat, lon, t)` within 1e-9 m/s (modulo floating-point associativity, which is permitted here since components sum to numbers of similar magnitude)
