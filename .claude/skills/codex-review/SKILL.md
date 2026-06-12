---
name: codex-review
description: Run an independent code review using Codex CLI for model-diverse feedback. Works at proposal stage (artifact review) or implementation stage (code review against specs).
user_invocable: true
---

Run a non-interactive review using Codex CLI. Delegates to `./scripts/codex-review.sh`.

**Usage:**
```
/codex-review <change-name>                → auto-detect stage from diff
/codex-review <change-name> proposal       → review artifacts only
/codex-review <change-name> implementation → review code against specs
```

**Steps:**

1. **Parse arguments**

   Parse `$ARGUMENTS` into `change-name` (required) and optional `stage` (`proposal` | `implementation`).

   If no change name provided, run `openspec list` and use **AskUserQuestion** to let the user select.

2. **Validate change exists**

   Check that `openspec/changes/<change-name>/` exists. If not, check archived changes under `openspec/changes/archive/`.

   If neither exists, report the error and stop.

3. **Run the review**

   ```bash
   ./scripts/codex-review.sh <change-name> [stage]
   ```

   The script handles:
   - Stage auto-detection (from `git diff main...HEAD`)
   - Context assembly (CLAUDE.md, context-brief, specs, artifacts)
   - Prompt construction (proposal vs implementation focus)
   - Codex invocation (`codex exec`)
   - Output file creation

   Run this in foreground — the review takes 1-3 minutes depending on diff size.

4. **Report results**

   ```
   ## Codex Review: <change-name> (<stage>)
   Output: openspec/changes/<name>/review-<timestamp>.md
   ```

   Read the output file and provide a brief summary (3-5 bullet points of key findings).

**Context Assembly Reference**

The script assembles this context for Codex (which doesn't read CLAUDE.md automatically):

| Source | Purpose |
|--------|---------|
| `AGENTS.md` (== `CLAUDE.md` via symlink) | Project principles, collaboration preferences, TDD methodology, conventions |
| `docs/testing-philosophy.md` | Testing discipline — what contract tests assert, mocking rules, tolerance hierarchy |
| `docs/simulation_integrity.md` | Enforcement Matrix — integrity concerns paired with their enforcement mechanisms |
| `context-brief.md` | Change-specific summary — purpose, decisions, scope |
| `specs/*/spec.md` | Delta specs — behavioral contracts with requirements and scenarios |
| `proposal.md`, `design.md`, `tasks.md` | Change artifacts for artifact-quality review |

Codex gets the diff context automatically via `--base main`. The assembled prompt controls review focus based on stage.

**Guardrails:**
- Never modify any files — this skill only runs the review
- The script creates the output file in the change directory
- If codex is not installed, the script will fail with a clear error
- Model and config come from `~/.codex/config.toml` — no flags needed
