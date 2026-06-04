# Version Information for the Binate Tools — Plan

Status: Phase 1 SHIPPED (version package + `scripts/gen-version.sh` exist;
`--version` wiring tracked in `claude-todo.md`). Phases 2 and 3 deferred.
Kept for design rationale and the open Phase 2/3 questions.

Add `--version` support to all the binate tools, backed by a checked-
in version package generated from the canonical `VERSION` file.  Lay
groundwork for a separate `buildinfo` package that captures
target-architecture / backend / BUILDER info for diagnostic queries
inside compiled programs.

## Phase 1 — `pkg/binate/version` + tool `--version` flags (SHIPPED)

`pkg/binate/version` is a Tier-2 package (binate-toolchain-specific;
not a runtime essential), collocated under `pkg/binate/`. It exports:

    // Version names the tools build this came from.  Format
    // `bnc-X.Y.Z` for a tagged release; `bnc-X.Y.Z-pre` for a
    // working-tree build between releases.
    const Version *[]const char = "bnc-X.Y.Z(-pre)"

Key decisions and their rationale:

- **The const value is declared in the .bni** (not only in the .bn).
  This makes every importer see the same value at compile time and
  sidesteps the separate-builds drift question (see Phase 3).  The
  hygiene check enforces .bni/.bn consistency.
- **The generated files are checked in** (not generated at build time
  on the fly) so the source tree is self-consistent without a
  pre-build step; this matches the project's overall "everything bnc
  needs to build is in-tree" philosophy.
- `scripts/gen-version.sh` reads `VERSION` and writes the package.
  Default (no flag) regenerates the files; `--check` exits non-zero if
  the files don't match `VERSION` without modifying anything. `--check`
  is the hygiene-mode invocation, wired through `scripts/hygiene/run.sh`
  and the `scripts/build-*.sh` scripts so a stale checkout doesn't ship
  a binary with a mismatched `Version` string.
- Tools share a release cycle: `cmd/bnc --version` → `bnc-X.Y.Z`,
  `cmd/bni` → `bni-X.Y.Z`, `cmd/bnas` → `bnas-X.Y.Z`, `cmd/bnlint` →
  `bnlint-X.Y.Z` (same X.Y.Z, only the tool prefix differs).

Both the release-cut and post-release flows in
`explorations/release-process.md` must regenerate the version package
after editing `VERSION` (release-cut drops `-pre`, post-release bumps
the next `-pre`); `hygiene/run.sh` fails if it's forgotten.

## Phase 2 (TODO) — `pkg/builtins/buildinfo`

Separate **tier-0** package because any binate program (not just
the tools) might want to introspect what BUILDER it was compiled
with, what target it was emitted for, etc.  Living under
`pkg/builtins/` puts it on the always-bundled side.

Final name TBD; candidates: `pkg/builtins/buildinfo`,
`pkg/builtins/build`, `pkg/builtins/env`.

Captures (no timestamps — those break reproducible builds):

- `BuilderVersion *[]const char` — the `BUILDER_VERSION` that
  compiled the calling binary
- `Target *[]const char` — the target triple bnc was invoked with
  (e.g. `"native-aa64"`, `"arm32-baremetal"`, `"native-x64"`)
- `Backend *[]const char` — `"llvm"` or `"native"`
- `HostArch *[]const char` (maybe) — the arch the BUILDER itself
  ran on; useful for "I got this binary from a Linux-x64 release
  bundle" vs "I built this on macOS-arm64"

Open: who populates this?  Two options:

1. **bnc emits a synthetic `pkg/builtins/buildinfo` module** at
   compile time, like it already emits the per-binary
   `<main>.__entry` module.  Each binary gets a baked-in
   buildinfo whose const values match its own build environment.
   Pro: no checked-in file ever needs regeneration; pro: each
   build's buildinfo is correct for that build.  Con: the package
   exists logically but has no source file the user can read,
   which is a new pattern.

2. **Generated file**, like `pkg/binate/version` from Phase 1.
   Pro: consistent with version.  Con: meaningless without the
   build-time arguments threaded into the generation; would have
   to be re-generated on every `bnc` invocation, which makes the
   checked-in-vs-generated story muddier.

Option 1 is probably right but needs design — defer to Phase 2.

Phase 2 is **deferred**.  Phase 1 is enough for users to ask
"what version is this `bnc` I have."

## Phase 3 (TODO, longer-term) — const semantics

User flagged a related concern: the proposed CLI-flag-annotation
system (separately tracked) wants to allow `const` values to be
overridden from the command line at build time.  That interacts
with how the version/buildinfo packages are consumed:

- A const **declared with a value in the .bni** is part of the
  package interface — all importers see the same value at compile
  time, and it's effectively a fixed identifier.
- A const **declared in .bni without a value, initialized in .bn**
  is a per-package private fact; importers don't see it at compile
  time, and it could in principle vary between build invocations
  of the same package.

In "separate-builds" mode (where dependents and dependencies are
built at potentially different times), the second pattern lets the
two builds see different versions of a "constant" without a
visible interface change — bad for a version string that
downstream code might compare against.

Resolution likely: tighten the const-in-.bni-only constraint for
the version / buildinfo packages.  Maybe a general principle for
all "well-known" exported consts.  This goes alongside the CLI-flag-
annotation design — pulling on either thread should touch the other.

Deferred until the CLI-flag-annotation proposal is on the table.

## Out of scope

- Auto-discovering versions via `git describe` etc.  The `VERSION`
  file is the source of truth; `git` history is incidental and
  shouldn't influence the version string.
- A long version string with commit hash, build date, etc.
  Reproducibility matters more than narrative — the tag is the
  identifier; people who need the commit hash can look it up.
