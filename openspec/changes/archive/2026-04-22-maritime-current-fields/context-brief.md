# Context Brief: maritime-current-fields

## Purpose
Provide an ocean current field abstraction (CurrentField protocol) and synthetic analytical implementation (SyntheticEddyField: mean flow + Gaussian eddies + M2 tide) for maritime node dynamics propagation and particle filter prediction.

## Key Decisions
- CurrentField as typing.Protocol — structural subtyping, no inheritance required for future HYCOM source
- Gaussian eddy model: smooth bounded tangential velocity profile, no singularity at center
- M2 tidal oscillation applied uniformly (no spatial variation) — spatial tide models deferred to M3
- FieldConfig dataclass separates field definition from CLI construction

## Tasks
All 18/18 complete. Implemented 2026-04-22.

## Implementation Notes
- Added tidal_direction_deg to FieldConfig (0°=east default, math convention) — original implementation hardcoded tide to eastward only
- 12 contract tests (11 original + tidal northward direction test)

## Files Affected
- rtl/vectors/maritime/current_fields.py (new)
- tests/maritime/test_current_fields.py (new)

## Spec Pointers
maritime-current-fields → Requirement: CurrentField Protocol, Requirement: Synthetic Eddy Field Velocities, Requirement: Synthetic Eddy Field Returns Physically Reasonable Velocities, Requirement: Advection Accuracy Through Synthetic Field
openspec/changes/maritime-current-fields/specs/maritime-current-fields/spec.md
