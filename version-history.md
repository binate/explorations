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
| `bnc-0.0.3` | `bnc-0.0.2` | _(pending)_ | Will carry the generic-call type-argument parser fix, making generics usable over managed/raw composite element types. |

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
