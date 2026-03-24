---
description: Nix-aware Haskell development - building, dependencies, and shell management
---

# Nix-Haskell development patterns

You are working in a Haskell project that uses nix for dependency management and build environments. Follow these rules strictly.

## Running commands

**Never run Haskell tooling outside of `nix develop`.**  Cabal, GHC, HLS, and formatters are provided by the nix shell - they are not installed globally.

Because agents cannot use interactive shells, always use the non-interactive form:

```bash
nix develop --command cabal build
nix develop --command cabal test
nix develop --command cabal run <executable>
nix develop --command ghci <module>
nix develop --command haskell-language-server-wrapper --lsp
```

Never run `nix develop` on its own then type commands. Never run `cabal build` or `ghc` directly - they will either not be found or use the wrong version.

## CRITICAL: Never use `#` in nix commands

The `#` character in flake references (e.g., `nixpkgs#haskellPackages.aeson`) triggers a manual approval prompt in Claude Code. **Always use `--impure --expr` or `--expr` instead.**

```bash
# WRONG - triggers permission prompt:
nix eval nixpkgs#haskellPackages.aeson.version
nix shell nixpkgs#cabal-install nixpkgs#ghc --command cabal build

# CORRECT:
nix eval --expr '(import <nixpkgs> {}).haskellPackages.aeson.version'
nix shell --impure --expr 'let p = import <nixpkgs> {}; in [ p.cabal-install p.ghc ]' --command cabal build
```

This applies to ALL nix commands: `nix shell`, `nix build`, `nix eval`, `nix run`. No exceptions.

## Build commands

| Task | Command |
|---|---|
| Build all | `nix develop --command cabal build all` |
| Build one package | `nix develop --command cabal build <package>` |
| Run tests | `nix develop --command cabal test all` |
| Run one test suite | `nix develop --command cabal test <suite>` |
| Run executable | `nix develop --command cabal run <exe>` |
| Start REPL | `nix develop --command cabal repl <package>` |
| Full nix build | `nix build` |
| Check it builds cleanly | `nix build --no-link` |

Prefer `cabal build` inside `nix develop` for fast iteration. Use `nix build` to verify the full derivation builds cleanly (CI-equivalent).

## Adding or fixing Haskell dependencies

**When you see build errors like "Could not find module", "unknown package", or a missing dependency in `build-depends`, the fix is almost always to update `flake.nix`.** Do not try to install packages with `cabal install`, `nix shell`, or any other method. All Haskell libraries come from nixpkgs via the project's flake.

### Workflow

1. Add the package to the `.cabal` file's `build-depends`.
2. Check if the package exists in nixpkgs:
   ```bash
   nix eval --expr 'builtins.hasAttr "aeson" (import <nixpkgs> {}).haskellPackages'
   ```
3. If it exists and the project's `flake.nix` already pulls in `haskellPackages` broadly: `nix develop --command cabal build` should just work.
4. If the build fails because nix doesn't provide the package to cabal, you need to **modify `flake.nix`** to include it. Read the existing `flake.nix` first and match its patterns. Common approaches:

   **Adding a package from Hackage (not in nixpkgs or wrong version):**
   ```nix
   haskellPackages = pkgs.haskellPackages.override {
     overrides = self: super: {
       my-package = self.callHackage "my-package" "1.0.0" {};
     };
   };
   ```

   **Overriding a broken package:**
   ```nix
   my-package = pkgs.haskell.lib.dontCheck (
     pkgs.haskell.lib.unmarkBroken super.my-package
   );
   ```

   **Adding to the devShell's packages (if the flake uses `shellFor` or similar):**
   Look for `packages = p: [...]` or `buildInputs` in the devShell definition and add the package there.

5. After modifying `flake.nix`, run `nix develop --command cabal build` to verify.
6. If you changed flake inputs, run `nix flake update` first.

### Common error -> fix mapping

| Error | Fix |
|---|---|
| `Could not find module 'Data.Aeson'` | Add `aeson` to `.cabal` `build-depends`, ensure flake provides it |
| `unknown package: foo` | Package not in the nix shell - update `flake.nix` to include it |
| `Setup: Encountered missing or private dependencies: foo` | Same - update `flake.nix` |
| `Could not resolve dependencies` | Version conflict - add an override in `flake.nix` |
| `error: attribute 'foo' missing` in nix build | Package not in nixpkgs - use `callHackage` or `callCabal2nix` in flake overrides |

## Flake structure

A typical Haskell flake has:
- `inputs`: nixpkgs and any other flake inputs
- `outputs.devShells`: the `nix develop` environment with GHC, cabal, HLS, and project dependencies
- `outputs.packages`: the built Haskell package(s)

Read the project's `flake.nix` before modifying it. Match the existing patterns.

## Common mistakes to avoid

- **Do not** use `nix shell` with `cabal-install` and `ghc` to build Haskell projects. `nix shell` gives you bare tools without the project's nix-provided dependencies, so cabal will download and compile everything from Hackage. Always use `nix develop --command cabal build` instead - the project's devShell provides GHC with all dependencies pre-built by nix.
- **Do not** run `cabal install` - nix manages dependencies.
- **Do not** run `stack build` unless the project specifically uses stack.
- **Do not** modify `cabal.project` to add source-repository-package stanzas - use nix overlays instead.
- **Do not** run `ghcup` - GHC is provided by nix.
- **Do not** assume the GHC version - check with `nix develop --command ghc --version`.
- **Do not** use `nix-shell` - use `nix develop` (flakes).
- **Do not** run `nix develop` interactively - always use `--command`.

## Lockfile

After changing `flake.nix` inputs, update the lockfile:
```bash
nix flake update
```

Or update a single input:
```bash
nix flake update nixpkgs
```
