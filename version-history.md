# Binate Version History & Build Ladder

The self-hosted compiler (`bnc`) bootstraps from a *builder* — a
previously-built compiler that compiles the current tree.  Each
release is pinned to the builder it was produced with (the repo's
`BUILDER_VERSION` file at the time the release tag was cut).  This
file records that ladder and the salient facts of each release.

`VERSION` = what the current tree will be tagged as next.
`BUILDER_VERSION` = the builder the current tree compiles with.

## Ladder

| Release | Built with (BUILDER) | Tagged | Notes |
|---------|----------------------|--------|-------|
| `bootstrap-0.0.1` | — (Go bootstrap interpreter) | — | The Go-implemented bootstrap interpreter from `github.com/binate/bootstrap`. Not a `bnc` release; the root of the ladder. |
| `bnc-0.0.1` | `bootstrap-0.0.1` | 2026-05-21 | First self-hosted `bnc` release. Compiled by the Go bootstrap. Establishes the release-bundle shape (per-platform tarball: `bin/` + `lib/{pkg,runtime}` + `SHA256SUMS`). |
| `bnc-0.0.2` | `bnc-0.0.1` | 2026-05-25 | First release built by a prior `bnc` (Go bootstrap no longer in the build path). **Promised generics** — generic decls + monomorphization landed after 0.0.1 — **but generics are unusable for many real cases**: the parser rejects `@T` / `@[]T` / `*[]T` type arguments in expression-position generic calls (`f[@T](...)`), so any generic over a managed-pointer / managed-slice / raw-slice element (the common case — e.g. `slices.Append[@ast.Decl]`) can't be written. Bare names and `*T` work. See the CRITICAL entry in `claude-todo.md` ("Generic call type args reject `@T` / `@[]T` / `*[]T`"). Kept in the ladder anyway: 0.0.2 carries other fixes that later commits already build on, so reverting wasn't clean — the parser fix lands on top and `bnc-0.0.3` picks it up. |
| `bnc-0.0.3` | `bnc-0.0.2` | 2026-05-26 | Carries the generic-call type-argument parser fix (binate `18b8047`): `f[@T]` / `f[@[]T]` / `f[*[]T]` now parse, so generics are usable over managed-pointer / managed-slice / raw-slice element types. Validated with the released binary before promoting — `bnc-0.0.3` compiles + runs conformance/492 over all three forms. Promoting it to BUILDER (binate `5a27b65`) is what lets `slices.Append[@T]` be used *inside* cmd/bnc's own (BUILDER-compilable) tree, unblocking the `appendXxxPtr` → `slices.Append[@T]` migration. |
| `bnc-0.0.4` | `bnc-0.0.3` | 2026-05-27 | Carries four substantive threads landed since 0.0.3 (60 commits).  **(1) Phase B full-path symbol mangling** (binate `7f989ad` + follow-ups `f7f8f04` / `2122648` / `4cd596a`): every `/` in a package path folds into the linker symbol (`pkg/asm/x64` → `bn_pkg__asm__x64__…`), so two packages sharing a last-segment name no longer collide and cross-package generic instances emit matching def+call symbols regardless of how slashed the type-arg's full path is — promoting 0.0.4 to BUILDER unblocks the `appendXxxPtr` → `slices.Append[@T]` migration in cmd/bnc's (BUILDER-compilable) tree.  **(2) `__c_call` intrinsic** (`4cd873f` Stage 1a / `ced3b85` Stage 1b / `2a77341` Stage 2 native): direct C-symbol calls with no Binate name mangling and no Binate ABI obligations on the callee, the foundation for the C-free systems target's C-interop layer.  **(3) Host-independent 64-bit integers in the IR + VM** (`035022c` Layer 1 — IR ints stored as int64; Layer 2 register-pair lowering, e.g. `7f112e4` / `7ce4b91` / `c4687f5` / `0a86865` / `944517d`): `int64` arithmetic, calls, multi-returns, casts and memory ops all lower to register-pair bytecode in the VM, removing the host-int assumption that previously gated a 32-bit-hosted toolchain.  **(4) `x86_64-darwin` native target** (`a0bdf62` Mach-O run path, `bda81ca` Backend-interface refactor, `cd42bd6` conformance runner, `f7a182b` / `b719d7e` SysV aggregate-arg passing): third native target after arm64-darwin / arm32-linux, gated by its own conformance runner. |
| `bnc-0.0.5` | `bnc-0.0.4` | _(pending)_ | Next cycle (`bnc-0.0.5-pre`). No headline feature committed yet. |

## Bumping the builder

Advancing `BUILDER_VERSION` lengthens the build ladder (each link is
a generation that must be reproduced to build from scratch), so only
advance it when there's a substantial language gain to justify the
extra generation — e.g. a feature the tree wants to *use* that the
current builder can't compile.  The cut/promote dance:

1. `VERSION` → `bnc-X.Y.Z` (drop `-pre`), commit, push, tag
   `bnc-X.Y.Z` → release workflow builds + publishes the tarballs.
2. After the release succeeds, verify `scripts/fetch-builder.sh`
   resolves the new version and a smoke build passes.
3. `BUILDER_VERSION` → `bnc-X.Y.Z`; `VERSION` → `bnc-X.Y.(Z+1)-pre`;
   commit, push.

(0.0.2 is a cautionary example: it was cut to unblock generics, but
a parser bug left generics largely unusable — a builder bump that
delivered less than its headline feature.  Validate the headline
feature end-to-end *before* cutting, not just that it compiles.)
