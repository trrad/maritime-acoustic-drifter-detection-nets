## MODIFIED Requirements

### Requirement: M1 Fleet Factory
The system SHALL provide a `make_m1_fleet(seed, bbox, *, lora_period_sec=None, gps_period_sec=None)`
function that returns a tuple of exactly 10 `Node` instances: 2 built
via `make_anchor`, 4 via `make_ballast_drifter`, and 4 via
`make_pure_drifter`. Given identical `seed`, `bbox`, and cadence
kwargs, two calls SHALL produce byte-identical output. All initial
positions SHALL be strictly within the provided `bbox`. Anchor ENU
positions SHALL be deterministic and independent of the seed; drifter
positions SHALL be pseudo-random and seed-dependent. All 10 `node_id`
values SHALL be distinct.

The `lora_period_sec` and `gps_period_sec` keyword arguments are
optional overrides that clone the bundled platform profiles with
replaced cadences before node construction:

- `lora_period_sec=X` SHALL produce a fleet in which every node's
  `profile.comms.tdma_period_sec == X` and every node's `lora_toa`
  sensor spec (where present) has `max_rate_hz == 1/X`.
- `gps_period_sec=X` SHALL produce a fleet in which every anchor's
  `gps` sensor has `max_rate_hz == 1/X`; drifter nodes (which have no
  GPS sensor in M1) are unaffected.
- `None` (the default) SHALL preserve the bundled M1 profile values
  exactly — no cloning, no mutation.

For the two anchors, the factory SHALL construct distinct per-anchor
profiles via `make_anchor_profile(anchor_lat_deg, anchor_lon_deg)`
(see `maritime-platform-profile` Requirement: Anchor Profile Factory),
placing the first anchor at the bbox's south-west corner
(`min_lat`, `min_lon`) and the second at the north-east corner
(`max_lat`, `max_lon`). The two anchors' `MooredPoseSpec` components
SHALL therefore carry different `anchor_lat_deg` / `anchor_lon_deg`
values, so that consumers of `ScenarioHeader.anchor_positions` (the
PF's LoRa-TOA anchor-based localization in particular) see real bbox-
derived mooring coordinates rather than the placeholder `(0.0, 0.0)`
that `ANCHOR_PROFILE` carries as a template.

Coastline-aware placement (rejecting positions on land) is explicitly
NOT this factory's responsibility — that is handled by
`maritime-scenario-gen`, which has the `RegionalMap` loaded.

#### Scenario: Fleet composition
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8))` is called
- **THEN** the returned tuple has length 10
- **AND** exactly 2 elements satisfy `is_moored(node)` (the anchors)
- **AND** exactly 4 elements satisfy `has_pump(node)` (the ballast drifters)
- **AND** the remaining 4 elements satisfy neither (pure drifters)

#### Scenario: Determinism across calls
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` is called twice with identical arguments
- **THEN** the two returned fleets have identical node IDs, profiles, layouts, and initial states

#### Scenario: Different seed produces different drifter positions
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` and `make_m1_fleet(seed=43, bbox=...)` are called with the same bbox
- **THEN** at least one drifter position differs between the two fleets
- **AND** both anchor positions are identical across the two fleets

#### Scenario: All initial positions are strictly inside bbox
- **WHEN** `make_m1_fleet(seed, bbox)` is called with any valid seed and bbox
- **THEN** every node's initial position is strictly within bbox (not on the boundary)

#### Scenario: Unique node IDs
- **WHEN** `make_m1_fleet(seed, bbox)` is called
- **THEN** all 10 nodes have distinct `node_id` values

#### Scenario: Anchors carry bbox-corner mooring coordinates
- **WHEN** `make_m1_fleet(seed, bbox=(min_lat, min_lon, max_lat, max_lon))` is called
- **THEN** the first anchor's `profile.component("moored_pose").anchor_lat_deg == min_lat` and `anchor_lon_deg == min_lon`
- **AND** the second anchor's `profile.component("moored_pose").anchor_lat_deg == max_lat` and `anchor_lon_deg == max_lon`
- **AND** the two anchors' `anchor_lat_deg` values are not equal to each other
- **AND** the two anchors' `anchor_lon_deg` values are not equal to each other

#### Scenario: lora_period_sec override applies uniformly to every node
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8), lora_period_sec=60.0)` is called
- **THEN** every returned node's `profile.comms.tdma_period_sec == 60.0`
- **AND** for every node whose profile has a `lora_toa` sensor, that sensor's `max_rate_hz == 1.0 / 60.0` within float tolerance
- **AND** the bundled profile constants `BALLAST_DRIFTER_PROFILE`, `PURE_DRIFTER_PROFILE`, and `ANCHOR_PROFILE` (the module-level singletons) remain unmutated (clone-with-override, not in-place edit)

#### Scenario: gps_period_sec override applies to anchors
- **WHEN** `make_m1_fleet(seed=42, bbox=(36.5, -122.2, 37.0, -121.8), gps_period_sec=60.0)` is called
- **THEN** every anchor node's `gps` sensor spec has `max_rate_hz == 1.0 / 60.0` within float tolerance
- **AND** the four ballast-drifter and four pure-drifter nodes are unaffected (they do not carry a GPS sensor in M1)

#### Scenario: Default kwargs preserve bundled profiles byte-identically
- **WHEN** `make_m1_fleet(seed=42, bbox=...)` is called with no cadence kwargs
- **THEN** every node's `profile.comms.tdma_period_sec` equals the bundled `BALLAST_DRIFTER_PROFILE.comms.tdma_period_sec` (3600.0 s in M1)
- **AND** the factory does NOT clone profiles when no override is requested (bundled singletons flow through unchanged)
