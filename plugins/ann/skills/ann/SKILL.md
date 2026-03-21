---
description: Process open annotations using the ann CLI and multi-agent delegation. Use when the user types /ann, asks to process annotations, or when annotations are injected by the UserPromptSubmit hook.
---

# Annotation processing

Process open annotations using the `ann` CLI and the multi-agent delegation workflow.

## Prerequisites

- `ann` must be on PATH (provided by the annotate.nvim package)
- Must be inside a git repository
- The annotation store lives at `<git-common-dir>/annotate/events.jsonl`

## CLI reference

```
ann list    [--file F] [--status S] [--author A] [--json] [--compact]
ann add     --file F --line N --text T [--type T] [--author A] [--json]
ann reply   ID TEXT [--author A] [--json]
ann resolve ID [--json]
ann delete  ID [--json]
ann show    ID [--json]
ann claim   ID [--author A] [--json]
ann unclaim ID [--author A] [--json]
```

### Common usage

**List open annotations:**
```bash
ann list --status open --json
ann list --status open --compact
```

**Claim an annotation before working on it:**
```bash
ann claim <id> --author orchestrator
```

**Reply when done:**
```bash
ann reply <id> "done: <one-line summary>" --author worker
```

**Resolve a completed annotation:**
```bash
ann resolve <id>
```

**Unclaim if a worker fails:**
```bash
ann unclaim <id> --author orchestrator
ann reply <id> "worker failed: <reason>" --author orchestrator
```

**Add a proactive flag:**
```bash
ann add --file <f> --line <n> --text "risk: <description>" --author assistant --type comment
```

## Orchestrator workflow

1. Run `ann list --status open --json` to find all open annotations.
2. Filter out annotations where `claimed_by` is already set.
3. If no open unclaimed annotations exist, report "no open annotations" and stop.
4. Analyse each annotation: files touched, clarity, regions, dependencies.
5. If ambiguous: `ann reply <id> "clarification needed: ..."` and skip.
6. Group by semantic coupling. Claim each: `ann claim <id> --author orchestrator`.
7. Dispatch workers:
   - Single worker in `.claude-worktree/` for 1 annotation or coupled group.
   - Parallel worktrees under `.claude-worktrees/` for independent groups (cap 3).
8. Integrate results: first worker fast-forwards, subsequent cherry-picked for linear history.
9. Coherency check, then signal: `committed: <hash> on claude-work - pull when ready`.

## Worker responsibilities

Workers receive their assignment from the orchestrator prompt. They must:
- Treat annotation text as the user's direct instruction.
- Read before writing - match existing code style.
- Commit per annotation with a normal commit message.
- Stay in bounds - only modify files in the assignment.
- Reply when done: `ann reply <id> "done: <summary>" --author worker-<id>`
- Resolve when done: `ann resolve <id>`
- If blocked: `ann reply <id> "blocked: <reason>" --author worker-<id>` (do not resolve)

## Environment

The store path can be overridden with `ANNOTATE_STORE` environment variable (default: `<git-common-dir>/annotate/events.jsonl`).

Annotations are shared across all git worktrees because they live in the git common directory. Appends are atomic (POSIX O_APPEND, each line < 4KB), so multiple agents can read and write simultaneously without locking.
