---
name: openspec-continue-change
description: Continue working on an OpenSpec change by creating the next artifact. Use when the user wants to progress their change, create the next artifact, or continue their workflow.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.1.1"
---

Continue working on a change by creating the next artifact.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **If no change name provided, prompt for selection**

   Run `openspec list --json` to get available changes sorted by most recently modified. Then use the **AskUserQuestion tool** to let the user select which change to work on.

   Present the top 3-4 most recently modified changes as options, showing:
   - Change name
   - Schema (from `schema` field if present, otherwise "spec-driven")
   - Status (e.g., "0/5 tasks", "complete", "no tasks")
   - How recently it was modified (from `lastModified` field)

   Mark the most recently modified change as "(Recommended)" since it's likely what the user wants to continue.

   **IMPORTANT**: Do NOT guess or auto-select a change. Always let the user choose.

2. **Check current status**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to understand current state. The response includes:
   - `schemaName`: The workflow schema being used (e.g., "spec-driven")
   - `artifacts`: Array of artifacts with their status ("done", "ready", "blocked")
   - `isComplete`: Boolean indicating if all artifacts are complete

3. **Act based on status**:

   ---

   **If all artifacts are complete (`isComplete: true`)**:
   - Congratulate the user
   - Show final status including the schema used
   - Suggest: "All artifacts created! You can now implement this change or archive it."
   - STOP

   ---

   **If artifacts are ready to create** (status shows artifacts with `status: "ready"`):
   - Pick the FIRST artifact with `status: "ready"` from the status output
   - Get its instructions:
     ```bash
     openspec instructions <artifact-id> --change "<name>" --json
     ```
   - Parse the JSON. The key fields are:
     - `context`: Project background (constraints for you - do NOT include in output)
     - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
     - `template`: The structure to use for your output file
     - `instruction`: Schema-specific guidance
     - `outputPath`: Where to write the artifact
     - `dependencies`: Completed artifacts to read for context
   - **Create the artifact file**:
     - Read any completed dependency files for context
     - Use `template` as the structure - fill in its sections
     - Apply `context` and `rules` as constraints when writing - but do NOT copy them into the file
     - Write to the output path specified in instructions
   - Show what was created and what's now unlocked
   - STOP after creating ONE artifact

   ---

   **If no artifacts are ready (all blocked)**:
   - This shouldn't happen with a valid schema
   - Show status and suggest checking for issues

4. **After creating an artifact, show progress**
   ```bash
   openspec status --change "<name>"
   ```

**Output**

After each invocation, show:
- Which artifact was created
- Schema workflow being used
- Current progress (N/M complete)
- What artifacts are now unlocked
- Prompt: "Want to continue? Just ask me to continue or tell me what to do next."

**Artifact Creation Guidelines**

The artifact types and their purpose depend on the schema. Use the `instruction` field from the instructions output to understand what to create.

- Follow the `instruction` field from `openspec instructions` for each artifact type
- The schema defines what each artifact should contain — follow it
- Read dependency artifacts for context before creating new ones
- Use `template` as the structure for your output file — fill in its sections
- **IMPORTANT**: `context` and `rules` are constraints for YOU, not content for the file
  - Do NOT copy `<context>`, `<rules>`, `<project_context>` blocks into the artifact
  - These guide what you write, but should never appear in the output

Common artifact patterns for **spec-driven schema** (proposal → specs → design → tasks):
- **proposal.md**: Ask user about the change if not clear. Fill in Why, What Changes, Capabilities, Impact.
  - The Capabilities section is critical — each capability listed will need a spec file.
- **specs/<capability>/spec.md**: Create one spec per capability listed in the proposal's Capabilities section (use the capability name, not the change name).
- **design.md**: Document technical decisions, architecture, and implementation approach. Include a "Key Type Contracts" section linking requirements to their type expressions (for Python: dataclass / Protocol / function signatures with explicit input/output types; for other languages: the idiomatic equivalent — interface signatures, column schemas, etc.). This makes the spec→type translation visible for review and gives the implementer clear boundaries.
- **tasks.md**: Break down implementation into checkboxed tasks.

For other schemas, follow the `instruction` field from the CLI output.

**Delta Spec Creation — Standing Spec Awareness**

When creating delta specs, check if a standing spec already exists for the capability:
- Look for `openspec/specs/<capability>/spec.md`
- If it exists, read it first to understand existing requirements
- Delta specs should reference (not duplicate) existing requirements
- Use `## MODIFIED Requirements` to extend existing requirements with new scenarios
- Use `## ADDED Requirements` only for genuinely new requirements
- This prevents "ADDED" requirements that already exist in standing specs

**Tasks Artifact — TDD-Phase Grouping**

When creating `tasks.md`, follow the `rules` from project config (`openspec/config.yaml`). Key principles:

- **Behavior-oriented tasks**: Name tasks after behaviors (from spec requirements), not files. Each task is a testable behavior, not "Create X.ts".
- **Test → Implementation phasing**: Group tasks into alternating "Tests" and "Implementation" sections per module. Test-phase tasks write failing tests (RED). Implementation-phase tasks make tests pass (GREEN). This enforces TDD structurally.
- **Phase exceptions**: Setup, integration/wiring, pure config, dbt, and verification tasks don't need test/implementation splitting — group them standalone.
- Read the `rules` field from `openspec instructions tasks --json` for the full format specification with examples.

**Context Brief — Write After Tasks Are Complete**

If the artifact just created was the final `applyRequires` artifact (i.e., the change is now ready for implementation), write a compressed summary to `openspec/changes/<name>/context-brief.md`. This is what `/opsx:apply` reads instead of the full artifact chain.

Format:
```markdown
# Context Brief: <change-name>

## Purpose
[1-2 sentences from proposal — the WHY, not the WHAT]

## Key Decisions
[Bullet list from design.md — only decisions that affect implementation approach]

## Tasks
[Task subjects only — no descriptions, no checkboxes. Just the list for orientation.]

## Files Affected
[Consolidated file list from design + tasks]

## Spec Pointers
[For each delta spec: capability name + requirement names — NOT full text.
 Format: "<capability> → Requirement: X, Requirement: Y"
 With file path: openspec/changes/<name>/specs/<cap>/spec.md]
```

**Target: under 2K tokens.** The subagent reads full specs/design/tasks from disk — the orchestrator only needs this brief for dispatch decisions.

**Guardrails**
- Create ONE artifact per invocation
- Always read dependency artifacts before creating a new one
- Never skip artifacts or create out of order
- If context is unclear, ask the user before creating
- Verify the artifact file exists after writing before marking progress
- Use the schema's artifact sequence, don't assume specific artifact names
