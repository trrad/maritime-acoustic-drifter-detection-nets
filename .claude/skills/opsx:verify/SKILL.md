---
name: openspec-verify-change
description: Verify implementation matches change artifacts. Use when the user wants to validate that implementation is complete, correct, and coherent before archiving.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "2.0"
  generatedBy: "1.1.1"
---

Verify that an implementation matches the change artifacts (specs, tasks, design).

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **If no change name provided, prompt for selection**

   Run `openspec list --json` to get available changes. Use the **AskUserQuestion tool** to let the user select.

   Show changes that have implementation tasks (tasks artifact exists).
   Include the schema used for each change if available.
   Mark changes with incomplete tasks as "(In Progress)".

   **IMPORTANT**: Do NOT guess or auto-select a change. Always let the user choose.

2. **Check status to understand the schema**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to understand:
   - `schemaName`: The workflow being used (e.g., "spec-driven")
   - Which artifacts exist for this change

3. **Get the change directory and load artifacts**

   ```bash
   openspec instructions apply --change "<name>" --json
   ```

   This returns the change directory and context files. Read all available artifacts from `contextFiles`.

4. **Initialize verification report structure**

   Create a report structure with four dimensions:
   - **Completeness**: Track tasks and spec coverage
   - **Correctness**: Track requirement implementation and scenario coverage
   - **Test Integrity**: Track contract test quality and immutability
   - **Coherence**: Track design adherence and pattern consistency

   Each dimension can have CRITICAL, WARNING, or SUGGESTION issues.

5. **Verify Completeness**

   **Task Completion**:
   - If tasks.md exists in contextFiles, read it
   - Parse checkboxes: `- [ ]` (incomplete) vs `- [x]` (complete)
   - Count complete vs total tasks
   - If incomplete tasks exist:
     - Add CRITICAL issue for each incomplete task
     - Recommendation: "Complete task: <description>" or "Mark as done if already implemented"

   **Spec Coverage**:
   - If delta specs exist in `openspec/changes/<name>/specs/`:
     - Extract all requirements (marked with "### Requirement:")
     - For each requirement:
       - Search codebase for keywords related to the requirement
       - Assess if implementation likely exists
     - If requirements appear unimplemented:
       - Add CRITICAL issue: "Requirement not found: <requirement name>"
       - Recommendation: "Implement requirement X: <description>"

6. **Verify Correctness**

   **Requirement Implementation Mapping**:
   - For each requirement from delta specs:
     - Search codebase for implementation evidence
     - If found, note file paths and line ranges
     - Assess if implementation matches requirement intent
     - If divergence detected:
       - Add WARNING: "Implementation may diverge from spec: <details>"
       - Recommendation: "Review <file>:<lines> against requirement X"

   **Scenario Coverage**:
   - For each scenario in delta specs (marked with "#### Scenario:"):
     - Check if conditions are handled in code
     - Check if tests exist covering the scenario
     - If scenario appears uncovered:
       - Add WARNING: "Scenario not covered: <scenario name>"
       - Recommendation: "Add test or implementation for scenario: <description>"

   **Wiring Verification (goal-backward)**:

   Existence of code does not mean the feature works. For each key requirement, verify three levels:

   1. **Substantive** — Implementation is real, not placeholder
      - Search for stub patterns: `TODO`, `FIXME`, `pass`, `return None`, `...`, `raise NotImplementedError`
      - If found in implementation files: Add WARNING: "Possible stub: <file>:<line>"

   2. **Wired** — Components are connected to the rest of the system
      - For new modules: verify they're imported/called from somewhere
      - For new API endpoints: verify they're registered in routes
      - For new test files: verify they're discoverable by the test runner
      - If unwired: Add WARNING: "Implementation exists but not wired: <details>"

   3. **Functional** — Tests actually pass
      - Run the relevant test suite via the `run-tests` skill (invoked with the Skill tool)
      - If tests fail: Add CRITICAL: "Tests failing: <summary of failures>"
      - If no test command available: Add WARNING: "Cannot verify functionality — no test runner for this area"

    This catches the gap where all tasks are checked off but the feature doesn't actually work end-to-end.

    **Type Contract Fidelity**:
    - If design.md includes a "Key Type Contracts" section:
      - Extract the type contracts (interface signatures, input/output shapes)
      - For each contract, find the corresponding implementation
      - Verify the implemented types match the design's intent:
        - Interface members present (no missing properties, no extra optional properties that should be required)
        - Return types match spec requirements (not `any`, not overly broad)
        - Input types enforce the constraints the spec describes
      - If type contract drift detected:
        - Add WARNING: "Type contract drift: <interface> — <what diverged from design>"
        - Recommendation: "Update implementation to match designed type contract, or update design if the change was intentional"
    - If no "Key Type Contracts" section in design: skip this check, note "No type contracts in design to verify against"
    - This check closes the spec→type→implementation loop: the spec defined intent, the design expressed it in types, the implementer wrote code against those types. Are they still aligned?

7. **Verify Test Integrity**

   **Contract Test Immutability**:
   - If contract tests were written during `/apply`, verify they were not modified by implementer:
     - `git diff --name-only` to find changed files
     - Cross-reference against contract test file paths (noted in tasks.md or context-brief.md)
     - If contract tests appear in the diff: Add CRITICAL: "Contract test modified by implementer: <file>"
   - This is the structural guarantee that the "done" definition wasn't weakened

    **Test Quality**:
    - Read test files for the change
    - Check for anti-patterns:
      - Tests asserting on mock call counts or arguments (not observable behavior)
      - Tests that pass trivially (e.g., `expect(true).toBe(true)`)
      - Missing edge case or error coverage for critical paths
      - Over-mocking internal code (only external boundaries should be mocked)
    - If found: Add WARNING: "Test quality concern: <details>"
    - Reference: `docs/testing-philosophy.md`

    **Untested Rejection Branches**:
    - Read implementation code for each new type/function
    - Extract every `raise` statement in validation code (e.g., every `raise ValueError(...)` in `__post_init__`)
    - For each `raise`, search test files for a test that constructs the type with values that trigger it
    - If a `raise` exists with no test that exercises it:
      - Add WARNING: "Untested rejection: `<type>` at `<file>:<line>` — no test passes the invalid value that triggers this raise"
      - Recommendation: "Add a test constructing <type> with <invalid value> to verify the ValueError"
    - Every `raise` in a constructor must have a test that triggers it. If it's worth guarding against, it's worth testing.

8. **Verify Coherence**

   **Design Adherence**:
   - If design.md exists in contextFiles:
     - Extract key decisions (look for sections like "Decision:", "Approach:", "Architecture:")
     - Verify implementation follows those decisions
     - If contradiction detected:
       - Add WARNING: "Design decision not followed: <decision>"
       - Recommendation: "Update implementation or revise design.md to match reality"
   - If no design.md: Skip design adherence check, note "No design.md to verify against"

   **Code Pattern Consistency**:
   - Review new code for consistency with project patterns
   - Check file naming, directory structure, coding style
   - If significant deviations found:
     - Add SUGGESTION: "Code pattern deviation: <details>"
     - Recommendation: "Consider following project pattern: <example>"

9. **Generate Verification Report**

   **Summary Scorecard**:
   ```
   ## Verification Report: <change-name>

   ### Summary
   | Dimension      | Status           |
   |----------------|------------------|
   | Completeness   | X/Y tasks, N reqs|
   | Correctness    | M/N reqs covered |
   | Test Integrity | Contract tests intact/modified |
   | Wiring         | Substantive/Wired/Functional |
   | Coherence      | Followed/Issues  |
   ```

   **Issues by Priority**:

   1. **CRITICAL** (Must fix before archive):
      - Incomplete tasks
      - Missing requirement implementations
      - Test failures
      - Contract test modifications by implementer
      - Each with specific, actionable recommendation

   2. **WARNING** (Should fix):
      - Spec/design divergences
      - Missing scenario coverage
      - Stubs or placeholder implementations
      - Unwired components (exist but not connected)
      - Test quality concerns
      - Each with specific recommendation

   3. **SUGGESTION** (Nice to fix):
      - Pattern inconsistencies
      - Minor improvements
      - Each with specific recommendation

   **Final Assessment**:
   - If CRITICAL issues: "X critical issue(s) found. Fix before archiving."
   - If only warnings: "No critical issues. Y warning(s) to consider. Ready for archive (with noted improvements)."
   - If all clear: "All checks passed. Ready for archive."

**Verification Heuristics**

- **Completeness**: Focus on objective checklist items (checkboxes, requirements list)
- **Correctness**: Use keyword search, file path analysis, reasonable inference - don't require perfect certainty
- **Test Integrity**: Contract tests must be unmodified. Test quality must follow testing philosophy.
- **Wiring**: Check substantive (not stubs), wired (imported/called), functional (tests pass). Run tests via the `run-tests` skill — don't infer from code alone
- **Coherence**: Look for glaring inconsistencies, don't nitpick style
- **False Positives**: When uncertain, prefer SUGGESTION over WARNING, WARNING over CRITICAL
- **Actionability**: Every issue must have a specific recommendation with file/line references where applicable

**Graceful Degradation**

- If only tasks.md exists: verify task completion only, skip spec/design checks
- If tasks + specs exist: verify completeness and correctness, skip design
- If full artifacts: verify all four dimensions
- Always note which checks were skipped and why

**Output Format**

Use clear markdown with:
- Table for summary scorecard
- Grouped lists for issues (CRITICAL/WARNING/SUGGESTION)
- Code references in format: `file.ts:123`
- Specific, actionable recommendations
- No vague suggestions like "consider reviewing"

If not already run, suggest: "Run `/codex-review <name>` for independent model-diverse review."
