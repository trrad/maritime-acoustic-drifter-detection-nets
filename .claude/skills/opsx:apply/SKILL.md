---
name: openspec-apply-change
description: Implement tasks from an OpenSpec change. Use when the user wants to start implementing, continue implementation, or work through tasks.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "2.0"
  generatedBy: "1.1.1"
---

Implement tasks from an OpenSpec change.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **Select the change**

   If a name is provided, use it. Otherwise:
   - Infer from conversation context if the user mentioned a change
   - Auto-select if only one active change exists
   - If ambiguous, run `openspec list --json` to get available changes and use the **AskUserQuestion tool** to let the user select

   Always announce: "Using change: <name>" and how to override (e.g., `/apply <other>`).

2. **Check status to understand the schema**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to understand:
   - `schemaName`: The workflow being used (e.g., "spec-driven")
   - Which artifact contains the tasks (typically "tasks" for spec-driven, check status for others)

3. **Get apply instructions**

   ```bash
   openspec instructions apply --change "<name>" --json
   ```

   This returns:
   - Context file paths (varies by schema - could be proposal/specs/design/tasks or spec/tests/implementation/docs)
   - Progress (total, complete, remaining)
   - Task list with status
   - Dynamic instruction based on current state

   **Handle states:**
   - If `state: "blocked"` (missing artifacts): show message, suggest using openspec-continue-change
   - If `state: "all_done"`: congratulate, suggest archive
   - Otherwise: proceed to implementation

4. **Read context (thin orchestrator)**

   Read **only** the lightweight context needed for dispatch decisions:
   - `openspec/changes/<name>/context-brief.md` — compressed summary (~2K tokens) written by `/ff`
   - `tasks.md` — for progress tracking (checkboxes) and task descriptions

   **Do NOT read full proposal, specs, or design files into the orchestrator context.** The subagent reads those from disk in its own fresh context window. The orchestrator's job is dispatch and quality gates, not holding the full artifact chain.

   **If context-brief.md doesn't exist** (change was created before this workflow update):
   - Fall back to reading full contextFiles from CLI output
   - After the session, suggest re-running `/ff` or manually writing the brief

5. **Show current progress**

   Display:
   - Schema being used
   - Progress: "N/M tasks complete"
   - Remaining tasks overview
   - Dynamic instruction from CLI

6. **Implement tasks (loop until done or blocked)**

   **Implementation mode:** Three-phase dispatch per task — test writing, implementation, review. The orchestrator collaborates with the human on contract test design, dispatches subagents for mechanical work, and reviews results.

   **Dispatch sizing — one task at a time by default.**
   - Batch test + implementation pairs for the SAME module only (e.g., test + impl = 2 tasks)
   - **Hard cap: 3 tasks per subagent.** Larger batches lose context, compound errors, and make failures harder to diagnose.
   - When in doubt, split rather than batch

   **Parallel dispatch decision tree:**

   Subagents can run with `isolation: "worktree"` — each gets an isolated repo copy
   on a temporary branch. This eliminates file-conflict risk for parallel dispatch.

   **Without worktree isolation (default):**
   - Different files, no shared state → parallel OK
   - Different subsystems (e.g., sensors vs dashboard) → parallel OK
   - Same module or same standing spec → sequential
   - One depends on the other's output → sequential
   - Shared test fixtures or RNG state → sequential

   **With worktree isolation:**
   - Independent requirements, even if touching shared files → parallel OK
   - One task's output is the other's input → still sequential
   - Shared database state or external services → still sequential

   **Per-task flow (three phases):**

   ### Phase A: Contract Test Design (orchestrator + human)

    a. **Design the contract tests** — From the context-brief, tasks.md, and spec:
       - Identify which spec scenarios map to test cases for this task
       - **Identify the type contracts** — what interfaces/types must be defined for the tests to compile? The test design implicitly defines interfaces; make this explicit. "Requirement X implies interface Y with these input/output types." When the types are locked down, the implementer can't smuggle in architectural decisions.
       - Determine what the tests should assert (observable behavior, not implementation)
       - Present the test design to the human for review:
         - Which scenarios become contract tests
         - Type contracts the tests will define (interface signatures, input/output shapes)
         - What each test asserts
         - Expected failure modes
       - Human reviews and refines the test design

   b. **Dispatch test-writer subagent** — Use Task tool (model choice
      left to orchestrator / project defaults; a cheap fast model is
      appropriate for this mechanical phase):
      ```
      Task (implementer):
        description: "Write contract tests for Task N: <task name>"
        prompt: |
          You are writing contract tests for a task. These tests define "done" —
          the implementer will make them pass.

          Read AGENTS.md (the project's canonical collaboration doc;
          CLAUDE.md is a symlink to it) and docs/testing-philosophy.md
          first for this project's preferences and testing discipline.

          ## Test Design (from orchestrator)
          [THE TEST DESIGN — scenarios, assertions, expected failures]

          ## Spec Context
          Read the full spec for context: [path to delta spec file]
          Focus on: [Requirement name(s) relevant to this task]

          ## Files
          - Create test file: [test file path]
          - Reference: [related modules for patterns]

          ## Rules
          - Assert on OBSERVABLE BEHAVIOR, not implementation details
          - Mock only external boundaries (network, time, randomness)
          - One behavior per test
          - Tests must fail for the right reason BEFORE implementation
          - Follow existing test patterns in the project
          - Use pytest (configured in pyproject.toml; run via `uv run pytest`)
          - Obtain RNGs via the `make_rng` fixture, not bare numpy.random

          ## Output
          Write the test file(s). Report:
          - Files created
          - Test names and what each verifies
          - Any ambiguities discovered while writing tests
      ```

   c. **Review contract tests with human** —
      - Read the test files the subagent wrote
      - Show the human: test names, assertions, any concerns
      - Run the tests via the `run-tests` skill to confirm they fail for the right reasons
      - Human approves or requests changes
      - If changes needed, make them directly or dispatch another test-writer round

   ### Phase B: Implementation (implementer subagent)

   d. **Dispatch implementer subagent** — Use Task tool (model choice
      left to orchestrator / project defaults):
      ```
      Task (implementer):
        description: "Implement Task N: <task name>"
        prompt: |
          You are implementing a task for this project.

          Read AGENTS.md first (the project's canonical collaboration
          doc; CLAUDE.md is a symlink to it). It contains the
          collaboration preferences, TDD methodology, and enforcement
          principles. Follow them exactly. Also read
          docs/testing-philosophy.md for testing discipline.

          Run tests by invoking the `run-tests` skill (via the Skill
          tool — it's a skill, not a native tool). It returns
          structured pass/fail results.

          ## Contract Tests (READ-ONLY)
          The following test files define "done" — make them pass.
          You CANNOT edit these files. You may write NEW test files for
          internal helpers or edge cases you discover.

          Contract test files: [list of test file paths]

          ## Task
          [FULL TEXT of task — pasted here]

          ## Spec Context
          Read the full spec for context: [path to delta spec file]
          Focus on: [Requirement name(s) relevant to this task]

          Also read the design doc for implementation approach: [path to design.md]

          ## Files
          - Modify: [file paths]
          - Reference: [related modules for patterns]

          ## Constraints
          - Do NOT edit the contract test files listed above
          - Do NOT change files outside your scope (listed above)
          - Do NOT add features, helpers, or abstractions beyond what the task specifies
          - Do NOT refactor neighboring code

          ## Deviation Rules
          - Auto-fix: bugs in code you just wrote, missing imports, type errors,
            broken tests from your changes
          - Auto-fix: missing error handling or input validation the spec implies
          - Ask: anything requiring a new file not listed in your scope
          - Ask: architectural changes (new abstractions, changed interfaces,
            schema modifications)
          - Ask: if a contract test appears wrong — do NOT edit it, surface via ---QUESTION---
          - Ask: if you've attempted 3 fixes on the same issue without resolution

          ## If You Hit Ambiguity
          If you encounter a question the spec/design doesn't answer that affects
          correctness, STOP implementation and return a block with exact sentinel
          lines so the orchestrator can detect it:

          ---QUESTION---
          question: "[the question]"
          context: "[why this matters]"
          options: ["Option A", "Option B"]
          completed_so_far: [list of completed subtasks]
          blocking: "[what you can't proceed with]"
          ---END_QUESTION---

          ## Your Job
          1. Read the contract tests — understand what "done" means
          2. Read the spec and design files for full context
          3. Write production code to make contract tests pass
          4. You may write additional tests for internal helpers or edge cases
          5. Run tests via the `run-tests` skill (invoked with the Skill tool) for structured feedback
          6. Self-review against the preferences in AGENTS.md
             (composition over inheritance, no unprincipled numeric
             thresholds, all errors explicit, etc.) and the testing
             discipline in docs/testing-philosophy.md
          7. Report back with:
             - Summary of changes
             - Files modified (with paths)
             - Test results from the `run-tests` skill (structured output)
             - Any concerns or open questions
      ```

   e. **Handle subagent results** —

      **Check for `---QUESTION---` sentinel in the subagent's return text.**
      If found:
      - Extract the question, options, context, and blocking fields
      - Present to the user via **AskUserQuestion** — map the subagent's `options` array to AskUserQuestion option labels, use `context` as option descriptions or question context
      - Once the user answers, **resume the same subagent** (pass `resume: <agentId>`) with: the user's answer, what was already completed (`completed_so_far`), and instruction to continue implementation
      - If resume fails, dispatch a fresh subagent with the answer + completed context

      **If no `---QUESTION---` sentinel:** Proceed to review.

   ### Phase C: Review

   f. **Dispatch reviewer subagent** — Use Task tool (model choice
      left to orchestrator / project defaults; a stronger reasoning
      model is appropriate here since this is the quality gate):
      ```
      Task (reviewer):
        description: "Review Task N: <task name>"
        prompt: |
          You are reviewing whether an implementation matches its specification.

          Read AGENTS.md and docs/testing-philosophy.md first for this
          project's collaboration preferences and testing discipline.
          Your job is independent verification — do NOT trust the
          implementer's self-report.

          ## What Was Requested
          [FULL TEXT of task requirements]

          ## Contract Test Files (must be unmodified)
          [list of contract test file paths]

          ## What Implementer Claims They Built
          [From implementer's report]

          ## CRITICAL: Do Not Trust the Report
          The implementer may have finished suspiciously quickly. Their report may be
          incomplete, inaccurate, or optimistic.

          You MUST:
          1. Read the actual code. Do NOT take their word for what they implemented.
          2. Check that contract test files are UNMODIFIED (compare against git diff).
          3. Run tests via the `run-tests` skill (invoked with the Skill tool) to verify all pass.
          4. Compare code to requirements line by line.

          Check for:
          - Missing requirements (spec requires things that aren't implemented)
          - Extra/unneeded work (features or abstractions not in the spec)
          - Misunderstandings (code does something different than spec intended)
          - Contract test edits (implementer modified tests — CRITICAL finding)
          - Test quality (behavioral assertions, not mock interactions)
          - Type safety (pyright strict should be clean on new code)
          - AGENTS.md preference violations (e.g., arbitrary numeric
            thresholds in spec tests, silent drops, capability flags
            reintroduced on profiles)

          ## Report Format
          Structured markdown with:
          - **Verdict**: PASS / PASS_WITH_CONCERNS / FAIL
          - **Summary**: 1-2 sentences
          - **CRITICAL findings** (empty list if none): each with file:line
            and specific action to fix
          - **WARNING findings** (empty list if none): each with file:line
            and suggested action
          - **Suggestions** (empty list if none): minor polish items
          - **Evidence checked**: what you actually read/ran (not what
            the implementer said)
      ```

   g. **If reviewer finds issues** — Assess severity:
      - CRITICAL issues (contract tests modified, spec violations): dispatch fix subagent or resume implementer
      - HIGH issues (incorrect behavior): fix and re-review
      - MEDIUM/LOW: note for the human, proceed unless human objects

   h. **Mark task complete** — Update tasks file: `- [ ]` → `- [x]`

   **Project-specific task guidance (eml-research):**
   - Numerical tolerances are in absolute physical units (meters,
     seconds, m/s) — not normalized ratios. Use the `assert_close`
     helper declared in `project-infra` standing spec.
   - RNGs are obtained via the `make_rng` fixture; no unseeded
     `numpy.random.*` calls.
   - Tests run via `uv run pytest`; discovery is scoped to `tests/`
     only (never `experiments/` or `rtl/` which are frozen baselines
     per `project-infra`).
   - Import-linter contracts run alongside pytest: `uv run lint-imports`.
     Both must exit zero before a task is considered complete.
   - PF-side tasks cannot import from `scenario_truth_schema` or
     `current_fields` — enforced by import-linter contract registered
     in `pyproject.toml`.
   - When a task touches spec-committed schema or reproducibility
     invariants (golden-trace fixtures, byte-identical output), run
     the full golden-trace regeneration to verify.

   **Constraints:**
   - Contract tests must be written and human-approved before implementation
   - Contract tests must be verified failing before implementation
   - Review is mandatory — never skip
   - If subagent fails, dispatch a fix subagent with specific instructions (don't fix manually to avoid context pollution)

   **Pause if:**
   - Task is unclear → ask for clarification
   - Implementation reveals a design issue → suggest updating artifacts
   - Error or blocker encountered → report and wait for guidance
   - User interrupts

   **After each batch (every 2-3 task completions), update context-brief.md:**
   - Mark completed tasks
   - Record any decisions made during implementation
   - Note any issues encountered or deferred
   - This ensures that if the session hits a plan-mode-clear boundary, the next session picks up from an accurate brief

   **After updating the context-brief, commit the batch:**
   - `git add` the specific files changed by the batch (implementation files, test files, tasks.md, context-brief.md)
   - Commit message format: `<change-name>: <what was implemented>`
   - Do NOT push — that happens at session end or when the user requests it
   - Subagents do not commit. The orchestrator owns commits.

7. **On completion or pause, show status**

   Display:
   - Tasks completed this session (with review outcomes)
   - Overall progress: "N/M tasks complete"
   - If all done: run full test suite via the `run-tests` skill (invoked with the Skill tool), then suggest verify → sync → validate → archive
   - If paused: explain why and wait for guidance

**Output During Implementation**

```
## Implementing: <change-name> (schema: <schema-name>)

### Task 3/7: <task description>
Designing contract tests...
[human reviews]
Writing test files...
Contract tests approved ✓
Dispatching implementer...
Implementing...
Review...
✓ Task complete

### Task 4/7: <task description>
...
```

**Output On Completion**

```
## Implementation Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Progress:** 7/7 tasks complete ✓

### Completed This Session
- [x] Task 1
- [x] Task 2
...

All tasks complete! Next steps: `/opsx:verify`, then `/opsx:sync`, then `openspec validate`, then `/opsx:archive`.
```

**Guardrails**
- Keep going through tasks until done or blocked
- Read context-brief.md + tasks.md for orchestrator context (NOT full artifacts — subagents read those)
- Contract tests are designed with human review, written by a test-writer subagent, verified failing, then handed to implementer
- Implementer CANNOT edit contract test files
- If task is ambiguous, pause and ask before implementing
- If implementation reveals issues, pause and suggest artifact updates
- Keep code changes minimal and scoped to each task
- Update task checkbox immediately after completing each task
- Update context-brief.md after each batch (every 2-3 tasks) for session continuity
- Pause on errors, blockers, or unclear requirements - don't guess
- If subagent result contains `---QUESTION---` sentinel, extract the question and present it to user via AskUserQuestion before continuing — never skip this
- Subagents read `AGENTS.md` (the project's canonical collaboration
  doc; `CLAUDE.md` is a symlink to it) and `docs/testing-philosophy.md`
  for collaboration preferences, TDD methodology, and coding standards
- Subagents read full spec + design files from disk (orchestrator provides paths, not content)
- Never skip review
- Evidence before claims: run full test suite before reporting "all done"

**Fluid Workflow Integration**

This skill supports the "actions on a change" model:

- **Can be invoked anytime**: Before all artifacts are done (if tasks exist), after partial implementation, interleaved with other actions
- **Allows artifact updates**: If implementation reveals design issues, suggest updating artifacts - not phase-locked, work fluidly
