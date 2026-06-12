# Spec Audit: Shape-Only Requirements and Missing Substance

## Role

You are auditing this project's OpenSpec artifacts and adjacent
project docs for one specific failure mode: **requirements and
scenarios that validate the shape of an output without pinning down
its substance**. A spec that declares a state slot, schema field, or
sensor channel — and has scenarios that verify the field exists with
the right type — but never asserts what content lives there when the
pipeline actually runs. Shape-only specs validate `openspec validate
--strict`, produce code that passes unit tests, and still yield a
pipeline that produces nonsense at the integration layer.

This is a sibling audit to `dev/prompts/spec_audit_unfounded_content.md`
(skeuomorphic + ungrounded-content). The failure modes differ:
unfounded-content hunts invented numbers and fake grounding;
substance hunts missing grounding — fields declared with no account
of what goes in them, scenarios that only exercise degenerate inputs,
numbers copied from other documents without re-asking whether they
mean the same thing in the new context.

This is a **review-only session.** You surface findings for the user's
approval or rejection. You do NOT silently fix anything. For each
finding, use the `AskUserQuestion` tool (not plain text) to solicit
the user's decision — see "Soliciting decisions" below.

## Canonical Calibration Example

The M1 Tier 3 round-2 findings are the calibration set. Four distinct
substance gaps made it all the way to implementation, all caught only
during a holistic "does this actually work?" review, not during
`openspec validate --strict` (which passed every module):

1. **Current queried at `(0, 0)` regardless of node position.**
   `propagate_truth` called `env.current_field.velocity_at(0.0, 0.0, env.t_sec)`
   and used the result for every node's advection. The fleet-dynamics
   spec said nodes "advect through the current field" — shape-correct —
   but no scenario asserted that two nodes at different positions see
   different currents from a spatially-varying field. SyntheticEddyField's
   whole reason to accept spatial arguments was discarded.
2. **`surface_current` slot in truth state populated with zeros forever.**
   The state layout declared the slot and the `propagate_truth` spec
   described reading the field, but no requirement wrote the sampled
   value back into state. Downstream truth consumers (dashboard,
   validation harness) saw zeros for every node at every tick.
3. **Bundled profiles declared almost no sensors.** Anchor had only
   GPS; ballast and pure drifters had `sensors=()`. Every other sensor
   type (IMU, baro, mag, bathy_probe, lora_toa) raised `CapabilityViolation`
   on any bundled-profile node. The spec said "anchor has GPS; drifters
   don't" and no scenario asserted that a full bundled fleet exercises
   every declared sensor without raising.
4. **`neighbor_range` fields in truth state.** The state layout
   inherited `state_dim` values from the buoy design doc's PF-belief-
   state analysis and included 4–8 "range to neighbor" slots per class.
   But those slots exist for PF range estimates, not truth; the truth
   runner never populated them. Dims copied without re-asking whether
   they meant the same thing in the truth context.

Each of these passed every module-level test and every `openspec
validate --strict`. Each would have been caught by a single substance
scenario that ran the pipeline (or a skeleton of it) for a few ticks
and asserted something specific about the resulting output content.

## Categories

### 1. Pure-shape requirement

A `### Requirement:` whose scenarios only assert structural properties
(existence, type, shape, slice length, protocol conformance) and never
assert what values live in the declared output when the module runs.

Test to apply:
> If the implementer populated the declared field with trivial
> placeholders (all zeros, constants, the identity function), would
> every scenario under this requirement still pass?

If yes → pure-shape → finding.

### 2. Degenerate-input-only scenarios

Scenarios that only exercise trivial inputs — zero current, empty
polygon lists, single-element fleets, constant fields with no
spatial variation, `t_sec=0`. These pass trivially for any
implementation because the expected output is also trivial.

Test to apply:
> Could an implementation that ignores one or more arguments
> still satisfy every scenario under this requirement?

If yes → likely finding.

### 3. Output declared without content contract

A field, slot, or record member declared in a requirement but never
referenced in any scenario's assertions. The spec declares the
slot exists; nothing specifies what content flows into it, what
transforms produce it, or what consumers expect to find there.

Test to apply:
> For each declared output field, is there at least one scenario
> that asserts on its content (not just its presence or type)?

If no → finding.

### 4. Numeric values copied without context check

Rate limits, state dimensions, power budgets, count thresholds,
default parameters — any numeric value imported from another doc
(design doc, hardware datasheet, prior spec) into a new capability
without an explicit check that the value means the same thing in
the new context. The neighbor_range case is canonical: the number
21 meant "PF belief state dimension" in the design doc and got
copied as "truth state dimension" in the layout spec — two
different things wearing the same number.

Test to apply:
> For each numeric value, does the text explicitly state the
> justification *in the context of this capability*? Or does it
> reference another doc without checking relevance?

If the latter → finding.

### 5. Missing integration scenario

A spec that describes a module whose output flows into another
module, but has no scenario that exercises the producer-consumer
handshake. This is where shape-only individual modules combine
into a pipeline that's functionally broken — each module validates
in isolation; no scenario asserts that downstream consumers can
actually use the output.

Test to apply:
> Does this capability have at least one scenario that involves
> another named capability (module, protocol, or downstream
> consumer)?

If no → likely finding, especially for integration-tier specs
(scenario-gen, pf-float, dashboard).

## Scope

Audit:

- `openspec/specs/**/*.md` — standing specs (shipped contracts).
- `openspec/changes/*/` — active drafts (proposal.md, design.md,
  `specs/**/spec.md`, tasks.md, context-brief.md).
- `docs/simulation_integrity.md` — charter claims about enforcement
  that might be shape-only (matrix rows pointing at nonexistent
  substance tests).

Do NOT audit:

- `openspec/changes/archive/` — historical record; substance issues
  there should already be visible in the corresponding standing
  spec. Audit standing specs and flag upstream if the archived
  change was the origin.
- `experiments/`, `rtl/`, `tests/` — code and tests are the
  substance evidence; the audit hunts spec gaps, not test gaps.
  (Note: if a substance finding exists in a spec, the corresponding
  test is also likely absent — flag that as part of the fix.)
- `.claude/skills/` — different concerns.

## Starting sequence

1. Read `AGENTS.md` (specifically the `### Spec format requirements`
   shape-vs-substance guidance and `### Integration pipelines`
   skeleton-before-spec-chain discipline) and `docs/testing-philosophy.md`
   (Shape vs. Substance, Pipeline Tests sections).
2. Re-read the Calibration Example above. Each finding there
   illustrates exactly the shape-valid / substance-empty gap
   you're hunting.
3. Begin with `openspec/changes/maritime-scenario-gen/`. This is
   the pending integration point and the highest-leverage place
   for substance gaps. Produce every finding you'd surface for that
   change's artifacts (proposal.md, design.md, specs/*/spec.md,
   tasks.md). Pause and wait for the user's decisions on the first
   few — those calibrate the bar for specificity.
4. After calibration, proceed systematically through the rest of
   the scope (changes `maritime-pf-float`, `maritime-dashboard`;
   then standing specs). One finding at a time. No batching.

## Output format per finding

```
## Finding N: <one-line title>

**File:** <path>:<line-or-range>
**Category:** 1 pure-shape | 2 degenerate-input | 3 output-without-content | 4 number-without-context | 5 missing-integration
**Severity:** blocking | major | minor | cosmetic

**Current text (exact excerpt):**
> <quoted content, verbatim from the file>

**Why it's shape-only:** <specific — what field/slot/output is
declared without a substance contract; what trivial placeholder
would still satisfy every scenario; what integration handshake
is missing>

**Substance scenario to add:** <concrete — a new
`#### Scenario:` block or prose describing the content assertion
that would close the gap. Include realistic inputs; avoid
degenerate cases.>

**Secondary: test gap** *(optional)*: <if the substance scenario
implies a new test, name the test file and the assertion. A
substance-audit fix usually lands in both spec and test.>
```

After emitting the finding, use the `AskUserQuestion` tool to
solicit the decision — see next section.

## Soliciting decisions

Use the `AskUserQuestion` tool (not plain text checkboxes or a
numbered list of options in prose) to get the user's decision on
each finding. The tool presents options as a structured question;
the user can pick one quickly without parsing a markdown
checklist.

Standard option set for substance findings:

- **"Approve fix"** — the proposed substance scenario is correct;
  add it (and the corresponding test) as part of the audit
  implementation pass.
- **"Modify"** — the general finding is right but the proposed
  scenario needs adjustment; user provides the adjustment in a
  free-text response.
- **"Not a gap"** — reject the finding (the spec is substantively
  complete as-is; the scenario you'd add is redundant or
  unwarranted).
- **"Defer"** — finding is valid but shouldn't block the current
  tier; track for later.

Question phrasing convention:
> "Finding N on <file>:<line> flags <one-phrase>. Proposal: add
> <one-phrase>. Decision?"

After the user responds, move to the next finding. Do not edit
anything until the user approves a batch of fixes explicitly.

## Severity guidance

- **blocking** — a pure-shape requirement on a critical path
  (pipeline output, PF input, cross-module handshake) where the
  trivial-placeholder implementation would produce broken output
  that downstream consumers cannot use. Must be fixed before
  `/opsx:apply`.
- **major** — shape-only requirement on a less-critical slot or
  a scenario that's trivially satisfiable by a degenerate
  implementation; pipeline wouldn't be broken but would carry
  meaningless values that confuse downstream consumers.
- **minor** — minor shape-without-substance in non-critical
  documentation or prose passages; low-leverage to fix but worth
  noting.
- **cosmetic** — inherited number with adequate context that just
  wants a citation clause.

Err toward "major" over "minor" when the finding is on a
capability that participates in the integration pipeline —
substance gaps compound at integration layers.

## Tone

Be aggressive. A false-positive substance finding costs the user
seconds; a missed substance gap ships a pipeline that passes
every gate but produces broken output. M1 shipped four such gaps
through six archived changes before a holistic review caught
them. That's the bar.

When unsure whether a requirement is truly shape-only or merely
terse, surface it anyway and let the user judge. The cost of a
wrong surface is low; the cost of a missed substance gap on a
load-bearing pipeline module is large.
