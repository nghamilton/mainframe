---
name: Nix Tool Resolution
description: Use when a command-line tool is not found on PATH, when you see "command not found", or when you need to install or locate a tool on a NixOS system. Also use proactively before running any tool that might not be in the current shell environment.
---

# Nix Tool Resolution

This is a NixOS system. Tools are managed by nix, not installed globally via `apt`, `brew`, `pip install --global`, `npm install -g`, or similar. When a tool is missing, use nix to get it.

## When a tool is missing from PATH

Do **not** search `/nix/store` for binaries. Store paths are not stable and grepping them is slow and unreliable.

Instead, follow this sequence:

### 1. Check the project devShell first

If `flake.nix` or `shell.nix` exists in the project root, the tool may already be available inside the dev environment:

```bash
nix develop --command which <tool>
```

If found, run the tool via `nix develop --command`:

```bash
nix develop --command <tool> <args>
```

### 2. One-off usage: `nix shell`

For tools needed once or twice, use `nix shell` to get a temporary environment:

```bash
nix shell nixpkgs#<package> --command <tool> <args>
```

Common examples:

| Tool | Package | Command |
|------|---------|---------|
| `jq` | `jq` | `nix shell nixpkgs#jq --command jq ...` |
| `tree` | `tree` | `nix shell nixpkgs#tree --command tree ...` |
| `python3` | `python3` | `nix shell nixpkgs#python3 --command python3 ...` |
| `curl` | `curl` | `nix shell nixpkgs#curl --command curl ...` |
| `sqlite3` | `sqlite` | `nix shell nixpkgs#sqlite --command sqlite3 ...` |
| `rg` | `ripgrep` | `nix shell nixpkgs#ripgrep --command rg ...` |
| `fd` | `fd` | `nix shell nixpkgs#fd --command fd ...` |
| `htop` | `htop` | `nix shell nixpkgs#htop --command htop ...` |

### 3. Repeated usage: suggest adding to devShell

If a tool is used more than once in a session, suggest that the user adds it to the project's `flake.nix` devShell rather than using `nix shell` each time. Provide the specific change:

```nix
# In flake.nix, under devShells.default or devShell:
buildInputs = [
  pkgs.<package>  # <-- add this line
];
```

Then the user runs `nix develop` (or direnv reloads) and the tool is permanently available.

## Finding the right package name

If you don't know the nix package name for a tool:

```bash
nix search nixpkgs <tool-name> 2>/dev/null | head -20
```

Or check online: `search.nixos.org/packages`.

The package name is not always the same as the binary name (e.g., `ripgrep` provides `rg`, `sqlite` provides `sqlite3`).

## Things to never do

- **Never search `/nix/store`** for binaries. Paths are content-addressed and not stable.
- **Never use `apt`, `brew`, `pip install --global`, `npm install -g`**, `cargo install` or any non-nix global installer.
- **Never suggest `nix-env -i`**. It pollutes the user profile. Use `nix shell` for temporary access or add to `flake.nix` for permanent access.
- **Never hardcode `/nix/store/...` paths.** They change on every rebuild.
- **Never assume tools are globally available.** Always verify with `which` or `command -v` first.

## Inside `nix develop`

When already inside a `nix develop` shell (check for `IN_NIX_SHELL` or `NIX_BUILD_TOP` environment variables), tools from the devShell are on PATH directly. No need for `nix develop --command` prefix.

## Avoiding permission prompts

- Use `--expr` instead of `#` in nix commands to avoid the `#` triggering manual approval:
  ```bash
  # Instead of: nix eval nixpkgs#python3.version
  nix eval --expr '(import <nixpkgs> {}).python3.version'
  ```

- Use `--arg` for parameterised expressions rather than string interpolation with `#`.

## Multiple tools at once

To get several tools in one shell:

```bash
nix shell nixpkgs#jq nixpkgs#curl nixpkgs#tree --command bash -c '<commands>'
```
