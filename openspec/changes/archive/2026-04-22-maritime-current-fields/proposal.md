## Why

Maritime node dynamics depend on the ocean current field — nodes advect with the water, and the particle filter needs to predict this advection. A synthetic analytical current field (mean flow + eddies + tidal component) enables fast iteration during development without requiring external data. The `CurrentField` protocol ensures that the HYCOM real-data source (future M3 work) can be swapped in without changing the dynamics or PF code.

## What Changes

- Add `rtl/vectors/maritime/current_fields.py` — `CurrentField` protocol definition + `SyntheticEddyField` implementation
- Synthetic field: spatially-varying mean flow + 2-4 Gaussian eddies + M2 tidal oscillation, all analytically defined
- Tests verify that the field produces physically reasonable velocities and that Lagrangian advection integrates correctly

## Capabilities

### New Capabilities
- `maritime-current-fields`: Ocean current field abstraction (`CurrentField` protocol) and synthetic analytical implementation (`SyntheticEddyField`) providing velocity queries at arbitrary lat/lon/time

### Modified Capabilities
(none)

## Impact

- **New files**: `rtl/vectors/maritime/current_fields.py`, `tests/maritime/test_current_fields.py`
- **No existing file modifications**
- **Dependencies**: numpy (already declared). The `CurrentField` protocol depends on the coordinate types from `maritime-geo` (lat/lon scalars), but no import required — the protocol accepts plain floats.
