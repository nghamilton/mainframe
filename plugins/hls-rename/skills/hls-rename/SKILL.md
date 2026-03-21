---
name: HLS Rename
description: Use when the user asks to rename a Haskell symbol, type, constructor, function, or variable across the codebase. Trigger phrases include "rename", "refactor name", "change name of", "rename symbol", or any request to change a Haskell identifier to a new name project-wide.
---

# HLS Rename

Perform semantically-aware renames of Haskell symbols using HLS `textDocument/rename`. This is superior to text-based find-and-replace because HLS understands scoping, qualified names, re-exports, and typeclass method references.

## Prerequisites

- `haskell-language-server-wrapper` in PATH
- Python 3.10+
- A buildable Cabal or Stack project
- Must run outside the sandbox (`dangerouslyDisableSandbox: true`) because HLS needs write access to `~/.cache/hie-bios/`

## Script location

The rename script is bundled with this skill:

```
<skill-directory>/scripts/lsp-rename.py
```

Resolve the absolute path relative to this SKILL.md file. The script path is:

```
${CLAUDE_PLUGIN_ROOT}/skills/hls-rename/scripts/lsp-rename.py
```

## Usage

```bash
python3 <script-path> <file> <line> <column> <new-name> [--root <project-root>] [--timeout <seconds>]
```

Arguments are 1-based (matching editor line/column numbers). The script:

1. Starts a fresh HLS instance for the project
2. Waits for indexing to complete (tracks progress tokens)
3. Validates the rename position (`textDocument/prepareRename`)
4. Executes the rename (`textDocument/rename`)
5. Applies the resulting `WorkspaceEdit` to disk
6. Outputs JSON to stdout with the list of changed files
7. Shuts down HLS cleanly

## Workflow

### Step 1: Locate the symbol

Use the LSP tool's `findReferences` or `hover` operation to confirm the symbol's definition location. Get the exact file, line, and column.

### Step 2: Run the rename

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/hls-rename/scripts/lsp-rename.py \
  <file> <line> <column> <new-name> \
  --root <project-root> --timeout 180
```

**Important:** Run with `dangerouslyDisableSandbox: true`. Progress is reported on stderr; the JSON result appears on stdout.

### Step 3: Verify

After the rename completes:

1. Check the JSON output for `"success": true` and review changed files
2. Build the project: `cabal build` (or `stack build`)
3. Optionally grep for any stale references the rename might have missed

## Newtype and data type considerations

HLS treats the type name, constructor(s), and record accessors as separate symbols.

### Simple newtypes (no record syntax)

For `newtype Foo = Foo Int`, run **two** renames:

1. Rename the **type name** (the name after `newtype`/`data`)
2. Rename the **constructor** (the name after `=`)

```bash
# 1. Type name
python3 <script> src/Types.hs 5 9 Bar --root .
# Line is now: newtype Bar = Foo Int

# 2. Constructor (re-check column after step 1)
python3 <script> src/Types.hs 5 <new-column> Bar --root .
# Line is now: newtype Bar = Bar Int
```

### Record newtypes/data types

For `newtype Foo = Foo { unFoo :: UUID }`, rename in **three** passes.
**Order matters** -- rename the accessor first, because renaming the constructor
may also rename the accessor to the constructor's new name (an HLS quirk).

1. Rename the **record accessor** (`unFoo` -> `unBar`)
2. Rename the **type name** (`Foo` -> `Bar`)
3. Rename the **constructor** (`Foo` -> `Bar`)

```bash
# 1. Accessor first (unique name, clean rename)
python3 <script> src/Types.hs 5 <accessor-column> unBar --root .

# 2. Type name
python3 <script> src/Types.hs 5 9 Bar --root .

# 3. Constructor (re-check column after step 2)
python3 <script> src/Types.hs 5 <new-column> Bar --root .
```

If the accessor is renamed after the constructor, HLS may set the accessor
name to the constructor name (e.g., `unFoo` becomes `Bar` instead of `unBar`),
leaving the code in an invalid state that requires manual fixup.

## Troubleshooting

**"rename supported: False"** -- HLS started in degraded mode. Common causes:
- Sandbox blocking `~/.cache/hie-bios/` (use `dangerouslyDisableSandbox: true`)
- Missing or broken `hie.yaml`
- GHC version mismatch between HLS and the project

**"cannot rename at this position"** -- The cursor is not on a renameable symbol. Verify the line and column are correct (1-based). Use `hover` to confirm the symbol at that position.

**Timeout** -- HLS may take a long time to index large projects. Increase `--timeout`. Default is 120 seconds.

**"Renaming of an exported name is unsupported"** -- HLS cannot rename exported symbols from their definition site. **Workaround:** initiate the rename from a **call site** in a consumer module instead. HLS resolves the symbol and renames it across all modules it can see. Then manually fix any cross-component references (test suites, string literals, comments).

**Partial rename** -- HLS only renames symbols it can see. Files not part of the current Cabal component (e.g., test modules when pointing at a library file) may be missed. After an HLS rename, grep for the old name and fix remaining references manually.
