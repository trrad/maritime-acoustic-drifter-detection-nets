# OpenSpec CLI Reference

Canonical flags and argument shapes for the OpenSpec CLI used in this
project. Written because incorrect invocations are common. When in
doubt, `openspec <command> --help`.

## Top-level shape

```
openspec [--no-color] <command> [options] [args]
```

One top-level command per invocation. There is no "run multiple
commands at once" — to validate five changes, run the command five
times (or use `--changes` / `--all` to batch internally).

## Core commands

### `openspec validate [<item-name>]` — validate one or many

```
openspec validate CHANGE_OR_SPEC              # validate one item
openspec validate CHANGE_OR_SPEC --strict     # strict mode
openspec validate --changes                   # all changes
openspec validate --specs                     # all specs
openspec validate --all                       # everything
openspec validate ITEM --type change          # disambiguate when a name
                                              #   exists as both
openspec validate ITEM --json                 # machine-readable output
openspec validate --concurrency 8             # override worker count
openspec validate ITEM --no-interactive       # fail instead of prompting
```

**Positional argument takes exactly one item.** Passing two item names
(`openspec validate a b`) fails with "too many arguments for
'validate'." To validate multiple specific items, chain with `&&` or a
shell loop — there is no multi-item positional syntax.

Strict validation's parser reads only the **first line** of each
requirement body when scanning for SHALL/MUST. A requirement whose body
begins with a sub-clause on line 1 and continues SHALL onto line 2 will
fail. Put SHALL on the first line.

### `openspec list [--specs|--changes]` — list items

```
openspec list                    # active changes (default)
openspec list --specs            # standing specs
openspec list --sort name        # alphabetical (default is recent)
openspec list --json             # machine-readable
```

### `openspec show [<item-name>]` — show one item

```
openspec show ITEM                       # pretty markdown render
openspec show ITEM --json                # full JSON structure
openspec show ITEM --json --deltas-only  # deltas only (change items)
openspec show ITEM --type change         # disambiguate
openspec show ITEM --json --requirement 3  # one requirement by 1-based index
```

`show --json --deltas-only` is the supported way to introspect a
change's parsed deltas. The older `openspec change show ...` form is
deprecated (still works, emits a deprecation warning).

**Parsed `requirement.text` is the first line of the body, not the
first paragraph.** If you need the full body content, read the
source markdown file directly — the JSON's `requirement.text` is a
normalized summary.

### `openspec status --change <id>` — artifact completion status

```
openspec status --change maritime-clock-model
openspec status --change maritime-clock-model --json
```

Reports which artifacts exist, which are missing, and which tasks are
checked off.

### `openspec archive <change-name>` — archive a completed change

```
openspec archive CHANGE
openspec archive CHANGE -y                  # skip confirmations
openspec archive CHANGE --skip-specs        # infra/tooling/doc-only
openspec archive CHANGE --no-validate       # last resort; requires confirm
```

Prefer the `/opsx:archive` skill — it orchestrates sync + validate +
archive with the project's workflow gates. Raw `openspec archive` is
for cases the skill can't handle.

### `openspec new change <name>` — scaffold a new change

```
openspec new change my-change
openspec new change my-change --description "Free-text README line"
openspec new change my-change --schema spec-driven   # default; explicit override
```

Creates `openspec/changes/<name>/` with the schema's starter layout.
Prefer `/opsx:new` or `/opsx:ff` for this project — they drive the
artifact sequence.

### `openspec init [path]` — initialize OpenSpec in a project

```
openspec init                           # interactive
openspec init --tools claude            # non-interactive, claude only
openspec init --tools claude,cursor     # multiple tools
openspec init --tools none              # skip tool integration
openspec init --force                   # auto-clean legacy files
```

### `openspec update [path]` — update instruction files

```
openspec update
openspec update --force                 # re-run even if already current
```

## Ambiguity resolution

When a change and a spec share a name (e.g., both named
`maritime-foo`), commands that take an item name error out until
disambiguated:

```
openspec validate maritime-foo --type change
openspec show     maritime-foo --type spec
```

## Common wrong invocations

| Wrong | Right |
|---|---|
| `openspec validate a b` | `openspec validate a && openspec validate b` |
| `openspec change show ...` | `openspec show ... --json` (the old form is deprecated) |
| `openspec list --changes --json` (fine, explicit) | `openspec list --json` also works (changes is default) |
| Omitting `--strict` when the workflow expects it | `openspec validate CHANGE --strict` — the `/opsx` skills and this project's Makefile use strict by default |
| Running `openspec archive` without `/opsx:sync` first | Use `/opsx:archive` skill, which runs sync + validate + archive in order |

## Env vars

- `OPENSPEC_CONCURRENCY` — default worker count for `validate --all`
  / `validate --changes` / `validate --specs`. Override per invocation
  with `--concurrency N`.

## Scripting tips

- Use `--json` on `list`, `show`, `validate`, `status`, `schemas`,
  `templates`, `spec show`, `spec list`, `spec validate`. The JSON
  shape is stable; stdout text is not.
- Use `--no-interactive` in scripts to fail fast instead of blocking
  on a prompt.
- Batch validation: `openspec validate --changes --json` is faster
  than looping per change (parallel internally via `--concurrency`).

## Related skills

- `/opsx:new`, `/opsx:ff`, `/opsx:continue`, `/opsx:apply`,
  `/opsx:verify`, `/opsx:sync`, `/opsx:archive`, `/opsx:bulk-archive`
  orchestrate the workflow that wraps these CLI commands. Prefer
  skills for any operation they cover; fall back to raw CLI only for
  introspection or edge cases.
- See `AGENTS.md` §"OpenSpec" for the workflow-level discipline the
  skills enforce.
