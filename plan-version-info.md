# Version Information for the Binate Tools — Plan

Add `--version` support to all the binate tools, backed by a checked-
in version package generated from the canonical `VERSION` file.  Lay
groundwork for a separate `buildinfo` package that captures
target-architecture / backend / BUILDER info for diagnostic queries
inside compiled programs.

## Phase 1 — `pkg/binate/version` + tool `--version` flags

### 1.1 `pkg/binate/version` package

Tier-2 package (binate-toolchain-specific; not a runtime essential),
collocated under `pkg/binate/`:

    pkg/binate/version.bni
    pkg/binate/version/
      version.bn

Surface:

    package "pkg/binate/version"

    // Version names the tools build this came from.  Format
    // `bnc-X.Y.Z` for a tagged release; `bnc-X.Y.Z-pre` for a
    // working-tree build between releases.
    const Version *[]const char = "bnc-X.Y.Z(-pre)"

The const value is **declared in the .bni** (not only in the .bn).
This makes every importer see the same value at compile time and
sidesteps the separate-builds drift question (see open-questions
below).  The .bn either repeats the same const or omits it; the
hygiene check enforces consistency.

### 1.2 `scripts/gen-version.sh`

Generator script that reads `VERSION` and writes
`pkg/binate/version.bni` (and possibly `pkg/binate/version/version.bn`)
with the const value set from the file.  Invocations:

    scripts/gen-version.sh                  # writes files; non-zero
                                            # exit if state changes
                                            # (so it doubles as a check)
    scripts/gen-version.sh --check          # exit non-zero if the
                                            # files don't match VERSION;
                                            # don't modify anything

`--check` is the hygiene-mode invocation.  Default (no flag)
regenerates the files; useful after editing `VERSION` (release-cut
flow drops `-pre`, post-release flow bumps the next `-pre`, both
need the generated package to follow).

The output is checked in (not generated at build time on the fly)
so the source tree is self-consistent without a pre-build step;
this matches the project's overall "everything bnc needs to build
is in-tree" philosophy.

### 1.3 Hygiene check

`scripts/hygiene/version.sh`: runs `scripts/gen-version.sh --check`
and fails if the generated files diverge from `VERSION`.  Add to
`scripts/hygiene/run.sh`'s master list so the standard
"check hygiene" sweep catches a forgotten regeneration.

`scripts/build-{bnc,bni,bnas,bnlint}.sh` also run the check at the
top, so a stale checkout doesn't ship a binary with a mismatched
`Version` string.  Quick (the check is one file diff); failure mode
is "run `scripts/gen-version.sh` and rebuild."

### 1.4 `--version` flag on each tool

Tools and what they print:

- `cmd/bnc --version` → `bnc-X.Y.Z` (or `bnc-X.Y.Z-pre`)
- `cmd/bni --version` → `bni-X.Y.Z` (same X.Y.Z; the tools share a
  release cycle)
- `cmd/bnas --version` → `bnas-X.Y.Z`
- `cmd/bnlint --version` → `bnlint-X.Y.Z`

Implementation per tool: at CLI parse time, if `--version` is
present, print `<tool>-` plus `version.Version`'s stripped `bnc-`
prefix, then exit 0.  Shared helper lives in
`pkg/binate/version` (e.g., `func Format(toolName *[]const char)
@[]char`) so each tool's main is one line.

### 1.5 Per-cycle workflow tweaks

Release-cut flow (`explorations/release-process.md`) needs one
extra step between editing `VERSION` and committing:

> 2b. Regenerate the version package
>
>     scripts/gen-version.sh

(or rely on `hygiene/run.sh` failing if it's forgotten).  Add to
the doc's checklist.

Post-release flow needs the same: after bumping `VERSION` to the
next `-pre`, regenerate the package.

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

## Suggested implementation order

1. **Phase 1.1 + 1.2**: create `pkg/binate/version` package +
   `scripts/gen-version.sh`.  Initial generated content matches
   the current `VERSION` (`bnc-0.0.6-pre`).
2. **Phase 1.3**: hygiene check + integrate into
   `scripts/hygiene/run.sh` and `scripts/build-*.sh`.
3. **Phase 1.4**: wire `--version` through each tool.  Start with
   `cmd/bnc`, then `cmd/bni`, then `cmd/bnas` + `cmd/bnlint`.
4. **Phase 1.5**: update `explorations/release-process.md` to
   include the regenerate-version-package step in the release-cut
   + post-release flows.
5. **Phase 2 + 3**: defer.
