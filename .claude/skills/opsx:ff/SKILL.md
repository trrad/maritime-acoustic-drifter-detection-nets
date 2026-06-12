---
name: openspec-ff-change
description: Fast-forward through OpenSpec artifact creation. Use when the user wants to quickly create all artifacts needed for implementation without stepping through each one individually.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.1.1"
---

Fast-forward through artifact creation - generate everything needed to start implementation in one go.

**Input**: The user's request should include a change name (kebab-case) OR a description of what they want to build.

**Steps**

1. **If no clear input provided, ask what they want to build**

   Use the **AskUserQuestion tool** (open-ended, no preset options) to ask:
   > "What change do you want to work on? Describe what you want to build or fix."

   From their description, derive a kebab-case name (e.g., "add user authentication" → `add-user-auth`).

   **IMPORTANT**: Do NOT proceed without understanding what the user wants to build.

2. **Create the change directory**
   ```bash
   openspec new change "<name>"
   ```
   This creates a scaffolded change at `openspec/changes/<name>/`.

3. **Get the artifact build order**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to get:
   - `applyRequires`: array of artifact IDs needed before implementation (e.g., `["tasks"]`)
   - `artifacts`: list of all artifacts with their status and dependencies

4. **Pre-creation clarification**

   Before starting the artifact loop, scan for likely ambiguities and resolve them upfront. 1-2 questions max — better to spend 30 seconds clarifying now than rewrite artifacts later.

   - If the user's description mentions multiple possible approaches → ask which one
   - If the scope is unclear (affects one area vs. many) → ask
   - If there's an existing change that might overlap → surface it via `openspec list`
   - If a relevant GitLab issue exists, read it for additional context: `glab issue view <number>`

   Use **AskUserQuestion** with concrete options. Skip this step if the user's description is already specific and unambiguous.

5. **Create artifacts in sequence until apply-ready**

   Use the **TodoWrite tool** to track progress through the artifacts.

   Loop through artifacts in dependency order (artifacts with no pending dependencies first):

   a. **For each artifact that is `ready` (dependencies satisfied)**:
      - Get instructions:
        ```bash
        openspec instructions <artifact-id> --change "<name>" --json
        ```
      - The instructions JSON includes:
        - `context`: Project background (constraints for you - do NOT include in output)
        - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
        - `template`: The structure to use for your output file
        - `instruction`: Schema-specific guidance for this artifact type
        - `outputPath`: Where to write the artifact
        - `dependencies`: Completed artifacts to read for context
      - Read any completed dependency files for context
      - Create the artifact file using `template` as the structure
      - Apply `context` and `rules` as constraints - but do NOT copy them into the file
      - Show brief progress: "✓ Created <artifact-id>"

   b. **Continue until all `applyRequires` artifacts are complete**
      - After creating each artifact, re-run `openspec status --change "<name>" --json`
      - Check if every artifact ID in `applyRequires` has `status: "done"` in the artifacts array
      - Stop when all `applyRequires` artifacts are done

   c. **If an artifact requires user input** (unclear context):
      - Use **AskUserQuestion tool** to clarify
      - Then continue with creation

5. **Show final status**
   ```bash
   openspec status --change "<name>"
   ```

6. **Write context-brief for downstream skills**

   Write a compressed summary to `openspec/changes/<name>/context-brief.md`. This is what `/opsx:apply` reads instead of the full artifact chain — keeping the orchestrator's context lean.

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

   **Target: under 2K tokens.** This is a compression of the full artifact chain. The subagent reads the full specs/design/tasks from disk — the orchestrator only needs this brief for dispatch decisions.

**Output**

After completing all artifacts, summarize:
- Change name and location
- List of artifacts created with brief descriptions
- What's ready: "All artifacts created! Ready for implementation."
- Context brief written to: `openspec/changes/<name>/context-brief.md`
- Prompt: "Run `/codex-review <name>` for independent review, or `/opsx:apply` to start implementation."

**Artifact Creation Guidelines**

- Follow the `instruction` field from `openspec instructions` for each artifact type
- The schema defines what each artifact should contain - follow it
- Read dependency artifacts for context before creating new ones
- Use `template` as the structure for your output file - fill in its sections
- **IMPORTANT**: `context` and `rules` are constraints for YOU, not content for the file
  - Do NOT copy `<context>`, `<rules>`, `<project_context>` blocks into the artifact
  - These guide what you write, but should never appear in the output

**Design Artifact — Key Type Contracts**

When creating `design.md`, include a "Key Type Contracts" section that links spec requirements to their type expressions. This makes the spec→type translation visible for review and gives the implementer clear boundaries.

The section should include:
- Interface/type signatures for each requirement that introduces or modifies a contract (for Python: dataclass / Protocol / function signatures; for TypeScript: interface signatures; for other languages: the idiomatic equivalent)
- The mapping: "Requirement X → produces type/interface Y with these input/output types"
- Any existing types being modified (reference standing specs)
- Construction invariants (`__post_init__` checks, validator contracts) — these are type-level promises the implementation must keep

This section is the bridge between spec intent and compiler enforcement. A good design artifact makes it clear what types the compiler will reject if the implementer deviates from the spec.

**Delta Spec Creation — Standing Spec Awareness**

When creating delta specs, check if a standing spec already exists for the capability:
- Look for `openspec/specs/<capability>/spec.md`
- If it exists, read it first to understand existing requirements
- Delta specs should reference (not duplicate) existing requirements
- Use `## MODIFIED Requirements` to extend existing requirements with new scenarios
- Use `## ADDED Requirements` only for genuinely new requirements
- This prevents "ADDED" requirements that already exist in standing specs

**Delta Spec Creation — Requirement Sizing**

Group requirements by change axis, not by type. When drafting requirements, ask: would these ever be modified independently? If two things always change together, they're one requirement. If one requirement covers things that would change independently, split it.

**Tasks Artifact — TDD-Phase Grouping**

When creating `tasks.md`, follow the `rules` from project config (`openspec/config.yaml`). Key principles:

- **Behavior-oriented tasks**: Name tasks after behaviors (from spec requirements), not files. Each task is a testable behavior, not "Create X.ts".
- **Test → Implementation phasing**: Group tasks into alternating "Tests" and "Implementation" sections per module. Test-phase tasks write failing tests (RED). Implementation-phase tasks make tests pass (GREEN). This enforces TDD structurally.
- **Phase exceptions**: Setup, integration/wiring, pure config, dbt, and verification tasks don't need test/implementation splitting — group them standalone.
- Read the `rules` field from `openspec instructions tasks --json` for the full format specification with examples.

**Guardrails**
- Create ALL artifacts needed for implementation (as defined by schema's `apply.requires`)
- Always read dependency artifacts before creating a new one
- If context is critically unclear, ask the user - but prefer making reasonable decisions to keep momentum
- If a change with that name already exists, suggest continuing that change instead
- Verify each artifact file exists after writing before proceeding to next
