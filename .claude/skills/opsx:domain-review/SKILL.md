---
name: opsx-domain-review
description: Domain-practitioner review of an OpenSpec change proposal. Spawns 2-4 practitioner-persona agents that read the proposal and surface objections a domain expert would raise. Catches "spec is internally coherent but solves the wrong problem" failures — the class of bug where structural verification passes but a real practitioner would object on first read.
user_invocable: true
---

Run a domain-practitioner adversarial review of an OpenSpec change.

**When to invoke:**
- AFTER `proposal.md` exists (the earliest useful moment).
- BEFORE `design.md` / delta specs are drafted (design locks in assumptions; catching errors post-design is more expensive than pre-design).
- OPTIONAL again after full artifact draft — catches late-stage drift.
- NOT a replacement for `/codex-review`; codex reviews coherence/completeness against stated goals. This reviews whether the stated goals themselves are correct against domain reality.

**What it catches that other reviews don't:**
- "Your prior is a monthly mean but your deployment is tidally-dominated — M2 alone drives ±0.5 m/s."
- "Your latency budget ignores context-cache invalidation cost on cold reads."
- "Your acoustic model assumes isothermal water but the operational region has a pronounced thermocline."

The failure pattern this targets is "proposal is internally consistent, enforces its invariants, validates clean, and solves a problem nobody would actually face in production." Neither type-checkers nor substance-scenario tests catch this — it requires domain knowledge that's in training data but doesn't activate under artifact-review framing.

**Input:** `<change-name>` (required).

**Steps**

1. **Validate change exists**

   ```bash
   openspec show <change-name> --json 2>&1 | head -3
   ```

   If it fails, ask the user to pick from `openspec list`.

2. **Read the proposal**

   Read `openspec/changes/<change-name>/proposal.md`. Also read `design.md` and any delta specs if present, but the proposal is the primary target — this review is most valuable BEFORE design drafts.

3. **Identify 2-4 domain personas**

   Infer which practitioners would have the most relevant domain knowledge for this proposal. Draw from the project's known domains (oceanography, particle filters, FPGA/LNS, data pipelines, etc.) plus any non-obvious domains the proposal touches.

   **Persona construction rules:**
   - Each persona gets a specific role, years of experience, sub-specialty, and a recent frustrating experience that primes them for catching the relevant error class.
   - BAD: "You are an oceanographer." (Too generic — produces textbook-safe responses.)
   - GOOD: "You are a DFO drifter-ops lead with 15 years deploying SVP-B floats in the Salish Sea. Last month you spent 3 days searching for a drifter that advected opposite to your onboard climatology's predicted direction during a spring tide. You are deeply skeptical of any proposal that uses time-averaged currents as a prior in these waters."

   Aim for 2-4 personas covering distinct sub-domains. More than 4 dilutes signal; fewer than 2 misses independent perspectives.

4. **Dispatch one Agent per persona (in parallel)**

   Use the Agent tool with `subagent_type: "general-purpose"`. Send all persona calls in a single message (parallel dispatch).

   Each agent's prompt MUST include:
   - The persona paragraph (role + experience + recent frustration).
   - The full proposal text (inline, not a file path).
   - Explicit instructions:
     > "Your job is NOT to verify coherence, structure, completeness, or spec quality. Your job is to raise substantive objections a real practitioner would raise in a design review. Do not praise the proposal. Do not validate reasonable-looking things. Flag only what a domain expert in your role would find wrong, questionable, or obviously-missing-an-alternative.
     >
     > Output format: a bullet list of up to 5 objections, ranked by severity. Each bullet is 1-3 sentences: what's wrong, why it's wrong in your domain, and (if obvious) what the standard-in-the-field alternative is. End with one sentence: 'the first objection a junior on my team would raise is ___.'
     >
     > Be ruthless. If the proposal looks fine, say so in one sentence and stop — false objections waste the author's time."

   Each agent returns its list of objections independently.

5. **Synthesize**

   Collect all objection lists. Deduplicate overlaps (multiple personas raising the same concern cluster into one). Rank by severity: "this design cannot work in the target environment" > "this default is wrong but fixable" > "this is suboptimal but ships".

6. **Write the review artifact**

   Save to `openspec/changes/<change-name>/domain-review-<timestamp>.md` with structure:

   ```markdown
   # Domain-Practitioner Review: <change-name>

   **Personas consulted:**
   - <Persona 1 one-line summary>
   - <Persona 2 one-line summary>
   - ...

   ## Severity 1: Design cannot work as proposed

   - <objection with persona attribution>

   ## Severity 2: Wrong default / parameter / assumption

   - <objection with persona attribution>

   ## Severity 3: Suboptimal but acceptable

   - <objection with persona attribution>

   ## If no substantive objections

   - <one-line statement from the persona(s) that signed off>

   ## First objections raised

   - <Persona 1>: <their "first objection a junior would raise" line>
   - <Persona 2>: ...
   ```

7. **Report to user**

   Print a short summary (5-10 lines):
   - N personas consulted
   - Count of objections at each severity level
   - The single most severe objection, verbatim
   - File path to the full review

   Prompt the user: "Want me to revise the proposal against these objections, or accept and continue?"

**Guardrails**
- Do NOT modify any artifact files — this skill only produces the review document.
- Do NOT fabricate domain expertise — if the proposal is in a domain the personas genuinely don't cover, say so explicitly rather than producing plausible-sounding objections.
- Keep personas distinct — two versions of "senior ocean modeler" is one persona, not two.
- If the proposal is clearly a small fix that doesn't touch domain assumptions (e.g., a type migration, a test refactor), note "domain review N/A" and exit without spawning personas.

**Known failure mode this does NOT catch**
- Business/market reality ("would anyone use this?"). The skill is for operational-correctness review, not product-market fit.
- Implementation bugs. That's what `/codex-review <name> implementation` and the test suite handle.
- Problems that require the specific system the proposal is building to already exist. Personas can't reason about performance of a model that hasn't been written yet.
