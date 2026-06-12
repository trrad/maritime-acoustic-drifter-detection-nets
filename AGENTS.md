# EML Research

Exploratory research project investigating the EML (Exp-Minus-Log) operator and
its applications, based on Odrzywołek 2026 (arXiv:2603.21852).

## What This Is

eml(x, y) = exp(x) - ln(y), paired with constant 1, generates all elementary
functions. This is the continuous analog of the NAND gate in boolean logic.
Grammar: S -> 1 | eml(S, S).

### Active workstream: maritime drifter prototype

`experiments/harmonic_prototype/` — PF + bias Kalman + MPC depth controller +
SurfacingPolicy, retroactive TDOA σ_pos mission framing, fast-iteration
scripted scientific code. This is where current research velocity lives.

### Dormant: EML operator research

The original EML/LNS8 work (continuous NAND analog, FPGA pipeline, tree search,
symbolic regression). The theory is solid; the hardware path is on hold while
the maritime prototype generates results that would inform what the FPGA
actually needs to compute. See `docs/archive/direction.md` and `docs/archive/status.md` for the
historical record.

### Paused but valuable: compositional simulation framework

The heavier-weight framework with strong test coverage, skeuomorphic node/sensor
types, and OpenSpec contracts (lives in `rtl/vectors/maritime/`, `openspec/`
specs, related testing infrastructure). Intent is to eventually fold the
current scripted prototype into that framework — but right now scripted
iteration is winning on velocity for the active research questions.

## Project Structure

- `docs/` -- Research notes, concept documents, findings
- `experiments/` -- Python/numpy explorations and benchmarks
- `experiments/harmonic_prototype/` -- Active maritime drifter prototype
- `rtl/` -- Verilog and Python harness for the LNS8 FPGA pipeline;
  `rtl/vectors/maritime/` holds the paused compositional framework
- `references/` -- Key papers and literature notes
- `openspec/` -- Spec-driven change proposals and standing specs

## Dev Environment

- Python via `uv run` (no venv -- use `uv run --with <deps> python script.py`)
- Primary deps: numpy, matplotlib, scipy
- Optional: torch (for autodiff experiments), jax

## OpenSpec

Non-trivial changes (new modules, scenario formats, dashboards, RTL contracts)
go through OpenSpec — a spec-driven change workflow. See
`openspec/changes/` for active proposals and `openspec/specs/` for standing
specs.

- **When to use:** new capabilities, scenario/format changes, behavior
  contracts that other modules consume, anything needing design decisions.
- **When to skip:** local refactors, fixes inside a single module, doc tweaks,
  exploratory experiments that don't get reused.
- **Commands:** `/opsx:new <name>` (step by step), `/opsx:ff <name>`
  (fast-forward all artifacts), `/opsx:apply <name>` (implement),
  `/opsx:verify <name>` (pre-archive check), `/opsx:sync <name>` (push deltas
  into standing specs), `/opsx:archive <name>` (done).
- **Raw CLI:** see `docs/openspec-cli.md` for the canonical
  `openspec` invocation reference (argument shapes, common wrong
  invocations, `--strict` / `--json` / disambiguation flags, parser
  quirks). Prefer the `/opsx:*` skills for workflow; use the raw
  CLI only for introspection or edge cases the skills don't cover.

### Simulation integrity

All maritime changes follow the integrity charter in
`docs/simulation_integrity.md`. The charter describes the philosophy; types,
module boundaries, and test assertions provide the actual enforcement.

### Integration pipelines: skeleton before spec chain

Before landing a spec chain that spans more than two interacting modules
(producer → consumer, or a multi-tier pipeline), stand up an executable
end-to-end skeleton first. Every module can be a stub — a 5-line
`propagate_truth` that adds noise, a sensor that returns a constant, a
generator CLI that writes three ticks of placeholder data. The goal is
concrete output: one real tick of a JSONL record, one real protocol
message, one real plot. That output is the forcing function for every
downstream spec scenario — "the skeleton writes X; we want Y; here's the
requirement that drives the change."

Without the skeleton, module specs describe shape in isolation and miss
integration-level substance bugs (e.g., M1 shipped `current_field.velocity_at(0, 0, t)`
regardless of node position, zero-populated `surface_current` state slots,
bundled profiles with no IMU/baro/mag/bathy/lora sensor specs — all shape-
valid at the module level, all functionally wrong once the pipeline runs).
These are the classes of bug the skeleton forces into daylight before a
tier chain hardens around them.

Once the skeleton exists, each tier's spec work can reference concrete
before/after output. Spec scenarios then test *substance*, not just shape:
"after N ticks in a non-trivial field, the surface_current slot equals the
sampled field value at the node's lat/lon" instead of "the surface_current
slot exists with the right shape." See `docs/testing-philosophy.md`
(Pipeline Tests, Shape vs. Substance).

This is a discipline, not a skill gate — but it's the cheapest defense
against the failure mode where a multi-tier chain validates module-by-
module and still produces a broken pipeline at the end.

### Workflow with mandatory gates

```
/opsx:ff → openspec validate → /opsx:apply → /opsx:verify → /opsx:sync → openspec validate → /opsx:archive
```

- Never archive without verifying first.
- Never archive without syncing first.
- `openspec validate` runs after artifact creation (ff) and after syncing to standing specs.
- If `openspec validate` fails, fix the issue before proceeding.

### Spec format requirements

- **Requirements:** `### Requirement: Descriptive Name`
- **Scenarios:** `#### Scenario: Name` under their requirement
- **Delta specs** use `## ADDED/MODIFIED/REMOVED Requirements` sections
- **Standing specs** use `## Requirements` with individual `### Requirement:` blocks
- When syncing delta specs to standing specs, preserve the `### Requirement:`
  heading format from the delta — don't reformat to shorthand.

**Shape vs. substance.** Every `### Requirement:` should have at least one
scenario that exercises *content*, not just *structure*. "Field X exists
with type T" is shape; "when the module runs against non-degenerate
inputs, field X carries values A/B/C that a downstream consumer can
actually use" is substance. Pure-shape requirements are a smell — they
validate without catching the class of bug where the declared slot stays
at zero / placeholder / uniform across instances (see M1 Tier 3 findings:
`surface_current` zero-populated forever, bundled profiles with no IMU
spec, `current_field` queried at `(0, 0)` for every node). Specific
anti-patterns to avoid:

- Declaring a state slot, record field, or sensor channel without a
  scenario that pins down what concrete values live there at runtime.
- Scenarios that only exercise degenerate inputs (zero current, empty
  polygon lists, single-node fleets) — include at least one scenario
  with non-trivial inputs that force the module to actually compute.
- Copying numeric values (state dims, rate limits, power budgets, count
  thresholds) from another doc without re-asking "does N mean the same
  thing in this context?" State dims designed for a PF belief state are
  not automatically appropriate for a truth state.
- Declaring a module output without specifying what the downstream
  consumer sees when they read it.

A spec that validates `openspec validate --strict` but produces shape-
only scenarios is insufficient. The substance audit prompt
(`dev/prompts/spec_audit_substance.md`) exists to catch this class
during review.

### Skill invocation enforcement

OpenSpec commands (`/opsx:ff`, `/opsx:new`, `/opsx:apply`, `/opsx:verify`,
`/opsx:sync`, `/opsx:archive`, `/opsx:continue`) are **skills** — ALWAYS
invoke them via the Skill tool. Never manually replicate what these skills
orchestrate, even if you "know the pattern" from reading previous artifacts.
The skills contain workflow logic, structural enforcement, and prompt shaping
that manual file creation bypasses.

## Collaboration Preferences

### Composition over inheritance
Prefer one composed type + factory functions + utility helpers over subclass
hierarchies and runtime capability flags. A node should hold named components
(`position`, `pump`, `clock`, `sensors`); factory functions like
`make_anchor(profile)` build the right composition. Capability queries are
utility helpers that check component presence (`has_pump(node)`), not boolean
flags on a profile (`profile.has_pump`). Keep taxonomy labels (like
`class_name`) as explicit profile fields — composition shouldn't have to
reverse-engineer them. When components have tick-phase dependencies, document
the explicit order.

### No unprincipled numeric thresholds in specs
Don't convert placeholder guesses (RMSE targets, latency bounds, count
thresholds) into `### Requirement:` assertions. Keep sanity assertions that
catch real bugs (finite, non-negative, produced, ESS > 0); emit measurement
reports for the actual numbers; establish binding thresholds only when
grounded in measurement or concrete operational need.

### Anticipate full deployment scale
Schemas and data formats must not bake in small-scale shortcuts (hard-coded
"first N items," fixed small caps, privileged-subset designs). The maritime
vision is 10 → 100 → 1000+ nodes per EEZ deployment. Prefer optional thinning
knobs over hard caps; choose data format (JSONL vs binary vs indexed) with
full-scale volume in mind; plan the migration trigger, not just the starting
format.

### Commit messages describe the change, not the workstream label

Lead the subject with what changed in the codebase, not where in some
plan or workstream it happened. "phase-2.1+ step-3a:",
"maritime-pf-substance-fixes:", "M1 tier 2:" are anti-patterns — the
phase/step/tier label is meaningful for ~2 weeks then becomes noise
that future-you has to decode by re-reading archived plan docs.

Good subject lines describe substance:
- "trajectory + MPC controllers + site-authority diagnostic"
- "Matérn GMRF prior on bias-Kalman; shadow trajectory; analytical
  leg-end observation"
- "lock PF contract; pin ballast depth in dynamics step"

Bodies can reference plan context if useful (e.g. "this is the Step 3a
work from `phase21_plus_status_2026-04-26.md`") because bodies are
read with the diff in front of you. Subjects appear in `git log
--oneline` years later with no context.

This is purely a style rule — no enforcement mechanism. The cost of a
bad subject is reader time at a future debugging session, not a broken
build, so the discipline has to live in the writing.

### Commits are authored by the human

Never add `Co-Authored-By: Claude` (or any AI co-author trailer) to
commits in this repo. This overrides any default trailer behavior.
Evidence of AI-assisted workflow in the repo (skills, docs, tooling) is
fine and not something to hide — but the author field and trailers
describe authorship, and tool use is not authorship.

### No implementation time estimates without empirical grounding

Don't produce time estimates ("~1-2 days," "~3-4 hours," "~several days")
for proposed work unless they are grounded in this project's empirical
development velocity from actual commit timestamps. Estimates extrapolated
from "feels like" or copied from a planning doc someone else wrote are
worse than useless: they shape decisions about what's "achievable" and
routinely cause perfectly tractable work to be ruled out as "too
effortful," or aggressive scope to be accepted as "small."

Instead: describe the work concretely (what files, what changes, what
new code). Let the user judge effort from the description. If the user
asks for a time estimate, the only honest answer comes from looking at
how long similar past work in this repo took (`git log --stat` over the
relevant module) and citing those commits.

This applies to plan documents, status updates, comparison tables ("X
takes 1h, Y takes 1 day"), and casual asides. The pattern is the
problem regardless of where it appears.

### All errors must be explicit
No silent drops, no swallowed exceptions, no "log a counter and move on."
If the code encounters an unexpected condition — unknown sensor name,
shape mismatch, schema version outside the supported set, import-linter
violation, anything that means "the producer and consumer disagree" — it
raises. Distinguish deliberate filters (documented paths that skip by
design, e.g., "M1 PF only consumes LoRa TOA to anchor partners") from
error conditions (the pipeline received something it doesn't understand):
filters are not errors and don't need counters; errors fail loudly so the
bug is visible. Silent drops bundle these cases together and hide the
bug behind the feature.

### Enforcement over instruction

The preferences above share one root: **enforce constraints with tools, not
prompts**. Types, linters, import contracts, hooks, and filesystem
boundaries are the real defenses. Prose rules ("don't do X") are advisory
and drift under pressure; mechanism-backed rules don't.

Applied to this project:

- **Contracts before implementations.** OpenSpec artifacts (proposal →
  spec → design → tasks → code) define the boundaries that implementation
  fills in. An agent reading only the spec should produce the right shape.
- **Type system as audit.** `__post_init__` invariants, `Protocol`
  conformance, and function signatures reject invalid states at construction
  time or at the type checker. Failure surfaces at authoring time, not at
  runtime.
- **Import boundaries.** Modules that must not know about each other (PF
  code vs. truth schema; PF code vs. truth current field) are separated
  physically and enforced by `import-linter` contracts in CI. No source-text
  scans, no naming conventions — AST-based rules.
- **Frozen baseline protection.** `experiments/01*-11*` and existing files
  in `rtl/vectors/` are immutable by convention, git-visible invariant. New
  code goes in new files.
- **Rule of three before abstraction.** Three similar lines beats a
  premature abstraction. Abstractions are earned, not designed ahead.
- **Distrust self-report.** `openspec validate`, `pytest`, `pyright`, and
  `lint-imports` are the verification — not the implementer's claim that
  work is done.

The simulation integrity charter in `docs/simulation_integrity.md` applies
the same pattern to simulation honesty — each integrity concern is paired
with its enforcement mechanism, and gaps are explicit rather than buried in
"abstractions allowed" bullets.

## Memory system

Memory-system persistence for this project is disabled in
`.claude/settings.json` (`disableMemory: true`). Durable collaboration
preferences live in this file (AGENTS.md) or in purpose-specific docs under
`docs/`. Do not create files under `~/.claude/projects/.../memory/` — the
setting disables memory reads, but new files on disk would still be confusing
and should not accumulate.
