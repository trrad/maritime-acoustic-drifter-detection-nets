# Spec Audit: Skeuomorphic Violations and Ungrounded Content

## Role

You are auditing this project's OpenSpec artifacts and adjacent project
docs for two related failure modes, in order of severity:

1. **PRIMARY — Skeuomorphic violations.** This codebase's reason for
   existing is simulated-process skeuomorphism: model the physical
   *mechanism*, do not inject the physical *result*. A spec that writes
   `offset_sec: float = 0.0` on a clock is the failure mode; a spec
   that writes `drift_ppm: float` on a crystal plus discrete sync
   events that correct accumulated drift is the honest model. The
   former produces a simulation that is parameterized, not simulated —
   results "transfer" to real hardware only by coincidence, which
   defeats the whole point.

2. **SECONDARY — Other ungrounded content.** Speculative numeric
   ranges, directional claims about physical behavior without
   evidence, architectural choices justified by invented reasoning,
   internal contradictions, nonexistent references, invented
   engineering numbers.

This is a **review-only session.** You surface findings for my
approval or rejection. You do NOT silently fix anything. You wait for
my decision on each finding before proposing or making edits.

## Canonical Calibration Example

`openspec/changes/maritime-clock-model/` fails the PRIMARY test. Its
current state ships:

- A `NodeClock` protocol with static `offset_sec` and `drift_ppm`
  parameters.
- `wall_time(t) = t + offset_sec + t * drift_ppm * 1e-6` — a
  parameterized result.
- Three classes split by operational role (`AnchorClock`,
  `ShearSyncedClock`, `DrifterClock`).
- Numeric offset ranges in the spec (e.g., `ShearSyncedClock` 1-10 ms;
  `DrifterClock` 10-100 ms).
- A factory `make_clock(..., realistic=True)` that samples those
  ranges via a seeded RNG and injects the values as the "realistic"
  mode.

All of this parameterizes physical results instead of simulating the
mechanism. The honest abstraction is:

- Each node has a **crystal** characterized by `drift_ppm` from its
  datasheet (accuracy of the oscillator — a physical property).
- Each node has a **sync mechanism** — GPS PPS for anchors
  (continuous disciplining to UTC, bringing offset to ~µs); LoRa
  TDMA-frame sync for others (one corrective event per TDMA cycle).
- `advance(dt_sec)` **accumulates** drift as `drift_ppm × dt` since
  the last sync — this is the process.
- Sync events (GPS PPS tick; LoRa frame boundary) **reset or
  correct** the accumulated offset. These are discrete events
  modeled by the simulation.
- `wall_time(true_sec) = true_sec + accumulated_offset_at_this_moment`,
  derived from the ongoing drift + sync process.
- At M1: set `drift_ppm = 0` on all crystals → accumulated offset
  stays 0 → `wall_time(t) = t`. **Zero-offset emerges from the input
  parameters; it is not itself an input parameter.**

The parameterized model cannot extend cleanly to M2 (realistic
offsets) — the abstraction has to be ripped out. The process model
extends trivially: set non-zero `drift_ppm` from crystal datasheets
and introduce realistic sync cadence, and the same code produces
realistic clock behavior.

Secondary issues in the same file:

- **Class split has no HW basis.** Ballast drifters and pure drifters
  use identical MCU + crystal + LoRa radio. The actual hardware
  discriminator is GPS-PPS presence (anchors) vs. absence (everyone
  else). Operational differences between ballast and pure drifters are
  parameter values on one `LoRaSyncedClock` class, not type
  distinctions.
- **Self-contradiction in a revision attempt.** A prior revision added
  prose "ballast submerges → LARGER accumulated offset than pure
  drifters" while keeping numeric values where pure drifter (10-100 ms)
  was LARGER than ballast (1-10 ms). Both the directional claim and
  the numeric values were invented. The contradiction is a symptom;
  the root cause is that both sides were invented independently without
  grounding.

**Begin the audit with `maritime-clock-model`.** Produce findings for
it first. My approval/modification on those findings is the signal
that we've calibrated the detection bar, and you may proceed to the
rest of the scope.

## Categories

### PRIMARY: Skeuomorphic violations

Any spec that:

- Writes a physical *result* as an input parameter (offset, RMSE,
  accuracy figure, detection distance) when the physical reality is
  that the result is a consequence of a mechanism.
- Uses class splits that don't reflect actual hardware distinctions —
  expressing operational variation as types rather than as
  constructor parameters on one type.
- Is structured such that the M1-to-M2 transition requires ripping
  out the abstraction rather than swapping parameter values.
- Parameterizes a time-evolving or event-driven process (drift, bias
  random walk, sensor aging, battery depletion, biofouling) as a
  single static value.

For each candidate, apply this test:

> **"If I set all physical mechanism inputs to their idealized values
> (zero drift, infinite-speed sync, perfect calibration), does the
> desired result emerge from the model — or is the desired result
> itself the input?"** The former is skeuomorphic. The latter is not.

### SECONDARY: Supporting failure modes

2.1. **Speculative numeric ranges presented as spec requirements.** σ
     values, thresholds, duty cycles, power budgets, ranges,
     tolerances, timeouts — anything that doesn't cite a datasheet, a
     measurement in this repo, or a concrete operational requirement.

2.2. **Directional claims about physical behavior.** "X happens more
     than Y because Z" where Z is not sourced or not reasoned from
     first principles of the physical system.

2.3. **Architectural choices justified by invented reasoning.** Class
     splits, module boundaries, abstraction seams chosen "because
     that's how one might think about it" rather than to prevent a
     specific failure mode or to reflect actual hardware.

2.4. **Schema/format choices with arbitrary specifics.** Defaults like
     `n_particles = 500`, `thin_particles = 50`, `duration_hours = 24`,
     `dt_sec = 60`. Some are defensible convenience; others are hollow
     invention. Check each.

2.5. **Internal inconsistencies.** Spec vs. design, proposal vs. spec,
     prose vs. numbers in the same file.

2.6. **Nonexistent references.** Files, classes, data, tools that the
     text assumes but that do not exist in this repo.

2.7. **Made-up physics/engineering numbers.** Drift rates, accuracy
     figures, propagation distances, noise floors — dressed in
     technical language but not sourced.

## Scope

Audit:

- `docs/simulation_integrity.md` — Enforcement Matrix rows must point
  at real enforcement mechanisms, not aspirational claims.
- `docs/maritime_buoy_design.md`,
  `docs/maritime_scenario_harness_plan.md` — separate
  claim-of-fact-about-physical-systems from forward-looking
  hypothesis. Flag anything stated as fact that is actually
  aspiration.
- `docs/testing-philosophy.md`
- `docs/m1_implementation_plan.md`
- `AGENTS.md`
- `openspec/specs/**/*.md` — standing specs; shipped contracts.
- `openspec/changes/*/` — active drafts (proposal.md, design.md,
  `specs/**/spec.md`, tasks.md, context-brief.md).

Do NOT audit:

- `.claude/skills/` — ported from another project in a prior session;
  different concerns.
- `openspec/changes/archive/` — historical record; don't rewrite.
- `experiments/`, `rtl/`, `tests/` — code, not specs; different
  review.

## Starting sequence

1. Read `AGENTS.md`, `docs/simulation_integrity.md`, and
   `docs/testing-philosophy.md` to calibrate to the project's stated
   standards.
2. Re-read the skeuomorphic canonical example above. Sit with it.
   This is THE pattern the audit is hunting.
3. Begin with `openspec/changes/maritime-clock-model/`. Produce every
   finding you'd surface for that change. Pause and wait for my
   approval on the first few — those approvals calibrate the bar for
   severity and specificity.
4. After calibration, proceed systematically through the rest of the
   scope. One finding at a time. No batching.

## Output format per finding

```
## Finding N: <one-line title>

**File:** <path>:<line-or-range>
**Category:** PRIMARY (skeuomorphic) | 2.1 speculative-numeric | 2.2 directional-claim | 2.3 architectural-invention | 2.4 schema-arbitrary | 2.5 inconsistency | 2.6 nonexistent-reference | 2.7 made-up-physics
**Severity:** blocking | major | minor | cosmetic

**Current text (exact excerpt):**
> <quoted content, verbatim from the file>

**Why it's ungrounded:** <specific — what claim, what's missing, what
contradicts what, or what physical mechanism is being short-circuited>

**What the honest model looks like** *(required for PRIMARY; optional
for SECONDARY)*: <brief description of the process that should be
simulated, or the grounded value that should replace the invented one>

**Proposed fix:** <concrete — exact new text, the abstraction to
adopt, or "drop pending grounded input" if fixing requires data we
don't have>

**User decision:** ☐ approve fix  ☐ modify as: ____  ☐ reject (keep current)  ☐ defer
```

## Severity guidance

- **blocking** — skeuomorphic violation in a load-bearing component;
  or a contradiction that makes the spec unimplementable; or a
  reference to something that doesn't exist at all. Must be resolved
  before any `/opsx:apply` on that change.
- **major** — ungrounded numeric range in a spec requirement;
  class-split without HW basis; internal contradiction that doesn't
  prevent implementation but embeds nonsense in the shipped contract.
- **minor** — speculative value in a non-requirement prose passage;
  plausible-sounding claim in design.md that isn't cited.
- **cosmetic** — inconsistent formatting, stale comments, naming
  drift.

Prefer SEVERITY=blocking on any PRIMARY finding unless the
abstraction is already a pure process model and the finding is merely
about missing citations on otherwise-defensible values.

## Tone

Be aggressive. A false-positive finding costs me 5 seconds to
dismiss; a missed finding becomes shipped spec. The clock-model case
ships self-contradicting numeric ranges in a spec one tool-invocation
away from `/opsx:apply`. That's the bar — hunt for anything in that
neighborhood or worse.

Skeuomorphic violations are disproportionately severe because they
violate the codebase's central design principle. If you find one and
aren't sure whether it's really a violation, surface it anyway and
let me judge — the cost of a wrong surface is low; the cost of a
missed violation on a load-bearing component is large.
