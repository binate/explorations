# Cutting a `bnc-X.Y.Z` Release

Step-by-step for releasing a new `bnc-X.Y.Z` tarball.  Also documents
how the moving parts fit together so you don't have to reverse-engineer
`release.yml` every time.

## Inputs

- **`VERSION`** at repo root — names what `cmd/bnc --version` reports
  for builds **from the current tree**.  Shapes:
  - `bnc-X.Y.Z` — stable-release shape; what `VERSION` says exactly on
    the commit that gets tagged `bnc-X.Y.Z`.
  - `bnc-X.Y.Z-preN` — numbered PRE-RELEASE shape (`-pre1`, `-pre2`, …).  A
    pre-release is a general toolchain bundle (all tools incl. bnfmt)
    that we can tag + publish and point `CHECK_TOOLS_VERSION` at, WITHOUT
    it being a build-ladder rung (only stable `bnc-X.Y.Z` advances
    `BUILDER_VERSION`).  It's how we dogfood a new feature outside the
    BUILDER tree — e.g. methods-on-generics for hygiene's bnlint/bnfmt —
    ahead of a stable cut.  See `plan-check-tools-version.md`.
  - `bnc-X.Y.Z-preN` — the "untagged, in-progress" shape used between
    stable releases; the working tree starts at `-pre1` right after a
    release and increments (`-pre2`, …) from there.
  - **VERSION labels the LAST commit carrying that value.**  Many commits
    in a row can read the same `VERSION`; the one a `bnc-…` tag points at
    is the last such commit before `VERSION` is bumped.  So to cut a
    pre-release without leaving `main` parked on the release-shape
    `VERSION`, bump `VERSION` to the NEXT value first (e.g. `pre1` →
    `pre2`), then tag the last `pre1`-VERSION commit — everything on
    `main` up to the bump is captured by that tag.
  - **Kept in sync with `pkg/binate/version/version.bn`.**  That file's
    `var Version = "..."` holds the same identifier **minus the `bnc-`
    builder prefix** — e.g. `VERSION` = `bnc-X.Y.Z`, `version.bn` =
    `X.Y.Z`.  A calling tool prepends its own name to `version.Version`
    for its `--version` banner (e.g. `bnc-X.Y.Z`; all four tools wire
    this).  Every edit to `VERSION`
    below (steps 2 and 6) must make the corresponding edit to
    `version.bn` (dropping `bnc-`), or the `version-sync` hygiene check
    fails.
- **`BUILDER_VERSION`** at repo root — names which prior STABLE release
  binary `scripts/fetch-builder.sh` downloads to use as the BUILDER
  during local + CI builds.  Always a concrete stable `bnc-X.Y.Z` (no
  `-preN` suffix); advances only through stable releases (a build-ladder
  rung).
- **`CHECK_TOOLS_VERSION`** at repo root — names which release's bundled
  HYGIENE tools (bnlint, bnfmt) the checks use, via
  `fetch-builder.sh --check-tools`.  May be a `bnc-X.Y.ZpreN` pre-release
  AHEAD of `BUILDER_VERSION`, so hygiene can run tools that understand
  newer non-BUILDER-tree language than the BUILDER the tree builds with.
  See `plan-check-tools-version.md`.
- **`.github/workflows/release.yml`** — the release CI.  Triggered
  by push of a tag matching `bnc-*`.  Builds per-platform bundles
  and attaches them to a GitHub release named after the tag.

## Before you start: is this release worth cutting?

Releases are **permanent additions to the build ladder** — every
future build that needs to reconstruct the chain has one more rung,
forever.  Cutting a release just to capture infrastructure tweaks
(e.g. "the bundle layout includes a few new directories now") that
have **no effect on mangled symbols or runtime ABI** is a waste:
the new bundle is functionally identical to the previous one,
nothing it enables couldn't have been done by waiting and folding
the infra change into the next release that DOES change something.

Concretely, ask: "what would consumers of this BUILDER see
differently?"

- Different mangled symbols (a package was renamed, the mangler
  changed, a hardcoded symbol literal in pkg/codegen flipped)
- Different runtime API (`pkg/builtins/rt`, `runtime/binate_runtime.c`)
- Different bundled-stdlib semantics
- A new tool binary

If the answer is "nothing — the bundle is the same set of symbols
arranged in a slightly different file layout," **don't cut the
release**.  Defer until something downstream actually needs the
change, then fold it into that release.

The infrastructure to support a future layout / runtime change CAN
still land in `main` ahead of time (release.yml updates, build-script
search-path extensions, etc.) — those just sit dormant against the
existing BUILDER and activate when the next *substantive* release
ships.

If yes the release does something substantive, on with it:

## The seven-step release

The release itself is two commits + one tag push, with verification
in between.

### 1. Prepare the tree

Make sure the tree you want to ship is on `main` and CI is happy
(or as happy as it gets — see "pre-existing failures" below).  All
the package-layout / hardcoded-symbol / runtime / build-script
changes that this release ought to capture should already be
committed and pushed.

### 2. Drop the `-preN` suffix from `VERSION`

Edit `VERSION` from `bnc-X.Y.Z-preN` → `bnc-X.Y.Z`.  This is the
commit that will be tagged.  **Make the corresponding edit to
`pkg/binate/version/version.bn`'s `var Version = "..."`, dropping the
`bnc-` prefix** (i.e. `var Version *[]readonly char = "X.Y.Z"`) — the
`version-sync` hygiene check compares them with `VERSION`'s `bnc-`
stripped.

Commit shape:

    Release bnc-X.Y.Z

    Drop -preN suffix; this commit will be tagged bnc-X.Y.Z.

Push to `main`.

### 3. Tag and push

Tag the just-pushed commit and push the tag.  From the main
checkout (`~/binate/binate`):

    git tag bnc-X.Y.Z
    git push origin bnc-X.Y.Z

The tag push triggers `release.yml`.  Don't tag a `-preN` commit; the
release CI happily builds whatever the tag points at, and a `-preN`
build would ship as a "release" with a broken version string.

### 4. Verify the release

Watch the `Release` workflow in GitHub Actions.  When all matrix
jobs (linux-x64, macos-arm64) finish and the `Publish release`
job succeeds, the GitHub release exists at
`https://github.com/binate/binate/releases/tag/bnc-X.Y.Z`.

Smoke test the bundle (each bundled tool accepts `--version`, printing
`<tool>-X.Y.Z`, so confirm-by-banner works; the behavior check below
still exercises the real pipeline):

1. Download one of the platform tarballs + `SHA256SUMS`.
2. Verify the SHA matches what `SHA256SUMS` claims.
3. Extract; confirm `lib/` contains everything build scripts consume:
   `ifaces/`, `impls/`, and `runtime/`.  There is **no** top-level
   `lib/pkg/` — packages live under `ifaces/{core,stdlib}/pkg/` (the
   `.bni` interfaces) and `impls/{core,stdlib}/.../pkg/` (the `.bn`
   implementations), per the spec's split tree.  In particular
   `pkg/builtins/rt`'s impl lives under `impls/core/libc/`, so a `-L`
   that omits it links fine for the compile but fails at the link stage
   with undefined `bn_pkg__builtins__rt__*` symbols.
4. Compile + run a small program through the extracted `bin/bnc`
   against the bundled `lib/`.  The bundle ships `bin/binate-paths` —
   the single source of truth for the `-I` / `-L` / `--runtime` formula
   — so use it rather than hand-coding the layout:

       BNC=./<bundle>/bin/bnc
       LIB=./<bundle>/lib
       I=$("./<bundle>/bin/binate-paths" --iface   --base "$LIB")
       L=$("./<bundle>/bin/binate-paths" --impl    --base "$LIB")
       RT=$("./<bundle>/bin/binate-paths" --runtime --base "$LIB")
       cat > hello.bn <<EOF
       package "main"
       import "pkg/bootstrap"
       func main() {
           println("hello bnc-X.Y.Z")
           bootstrap.Exit(0)
       }
       EOF
       "$BNC" -I "$I" -L "$L" --runtime "$RT" -o hello hello.bn
       ./hello

5. Also exercise the tier-0 carve-out so you confirm
   `ifaces/core/pkg/builtins/lang.bni` +
   `impls/core/common/pkg/builtins/lang/` are reachable from the
   bundle:

       cat > carveout.bn <<EOF
       package "main"
       import "pkg/bootstrap"
       import "pkg/builtins/lang"
       func main() {
           var x int = 42
           var s *lang.Stringer = &x
           println(s.String())  // expect: 42
           bootstrap.Exit(0)
       }
       EOF
       # ... same compile incantation ...
       ./carveout

If anything's wrong, delete the GitHub release + tag and start over
— don't ship a bad release just because the tag is already there.

### 5. Bump `BUILDER_VERSION`

Once the release is verified good, point the checkout at it:

    BUILDER_VERSION:  bnc-<old> → bnc-X.Y.Z

`scripts/fetch-builder.sh` reads this file to resolve which release
to download as the BUILDER for local + CI builds.  Bumping it makes
the just-shipped release the new BUILDER everyone uses.

### 6. Bump `VERSION` to the next pre-release

Edit `VERSION` from `bnc-X.Y.Z` → `bnc-X.Y.(Z+1)-pre1`.  This marks
the tree as "post-X.Y.Z, in-progress toward X.Y.(Z+1)."  The `-preN`
suffix is what flags a build as "not a tagged release."  **Make the
corresponding edit to `pkg/binate/version/version.bn`'s `var Version`,
dropping the `bnc-` prefix** (i.e. `"X.Y.(Z+1)-pre1"`) — version-sync
strips `VERSION`'s `bnc-` before comparing.

Combine with the BUILDER_VERSION bump into one commit:

    Post-release: bump BUILDER_VERSION → bnc-X.Y.Z, VERSION → bnc-X.Y.(Z+1)-pre1

Push to main.  CI re-runs against the new BUILDER; verify it stays
green.

### 7. Watch CI on the post-release commit

The first commit using the freshly-released BUILDER is the real
test of whether the release works for everyone else's machines.
Watch hygiene + unit + conformance + e2e CI on that commit.  If
anything breaks, it's either:

- A skew you didn't catch in step 4's smoke test (the BUILDER
  emits symbols current source doesn't define, or vice versa).
- A platform-specific issue (the bundle was built on macos-arm64
  but the linux-x64 binary is broken).

Treat these as critical fix-now bugs.

## Reference: what `release.yml` actually does

For each platform in the matrix (`linux-x64`, `macos-arm64`):

1. Checks out `binate` and (sibling) `bootstrap` repos.
2. Installs Go, and clang on Linux.
3. Runs `scripts/build-{bnc,bni,bnas,bnlint}.sh` to produce each
   tool binary.  Each script independently calls
   `scripts/fetch-builder.sh` to resolve `BUILDER_VERSION` and uses
   that BUILDER to compile its target.  For the first bnc release,
   `BUILDER_VERSION` is `bootstrap-*` and the fetcher Go-builds the
   bootstrap sibling; for later releases, the fetcher downloads
   the previously-released `bnc-X.Y.Z` tarball.
4. Assembles the bundle:

       <tag>-<platform>/
         bin/{bnc,bni,bnas,bnlint}
         lib/
           pkg/        bundled stdlib (matches the binary's mangling)
           runtime/    bundled C runtime
           ifaces/     bundled tier-0 / 0b interfaces
           impls/      bundled tier-0 / 0b impls
                      (`impls/core/{common,libc,baremetal}/...`)

5. Computes SHA256, packs `<tag>-<platform>.tar.gz`, uploads it as
   a workflow artifact.

After all matrix jobs finish, the `Publish release` job collects
the per-platform artifacts, assembles a single `SHA256SUMS`
manifest, and attaches everything to a GitHub release named
`bnc-X.Y.Z` (created via `gh release create`, or `gh release upload`
if the release already exists from a prior failed run).

## Why two `VERSION` bumps

The `-preN` ↔ release-shape dance keeps the in-tree `VERSION` manifest
honest with what each commit represents.  (`cmd/bnc` — and
bni/bnas/bnlint — now expose `--version`, reporting `<tool>-` +
`version.Version`, kept in sync with `VERSION` by the `version-sync`
hygiene check.  The `VERSION` file itself is still a written-down
convention the release-cut workflow and human readers rely on, not read
by any build script.)

- Anything built from a commit whose `VERSION` says `bnc-X.Y.Z-preN`
  is "in-progress toward X.Y.Z" — clearly **not** a tagged release.
- The tagged commit itself says `bnc-X.Y.Z` exactly — the file's
  state on disk at that commit matches what `git tag bnc-X.Y.Z`
  named.
- The very next commit on `main` says `bnc-X.Y.(Z+1)-pre1` — so the
  tree state after the tag is again clearly "between releases."

If you forget step 6 (bump VERSION to next `-pre1`), every commit on
main between this release and the next would still claim to be
`bnc-X.Y.Z` in-tree even though only one of them was actually tagged.
Don't forget.

## Pre-existing failures vs release-blockers

CI shows some persistent pre-existing failures (e.g. `-int` /
`-int-int` modes, native-aa64 capture tests).  These are tracked
in `claude-todo.md` and predate the release work.  They do **not**
block cutting a release — the release CI checks that the bundle
*builds*, not that every conformance mode passes against it.

What WOULD block: failures in `Code hygiene`, `E2E tests`, or
`Conformance tests` on modes where bnc-0.0.<prev> was green.  If a
release-prep commit introduces a NEW failure (especially on a `-comp*`
mode), fix that before tagging.

## BUILDER-skew traps

The release workflow uses `scripts/build-{bnc,bni,bnas,bnlint}.sh`,
which invoke the BUILDER directly (not through a gen1 stage).  This
matters when current source has hardcoded mangled-symbol literals
that the BUILDER's compiled-in codegen doesn't yet know about.

Symptoms: clang errors like `use of undefined value
'@bn_pkg__rt__Alloc'` when building cmd/bni — meaning the BUILDER's
codegen emits one mangling (say `bn_pkg__rt__*`, OLD) while the
loader resolves imports against current-source manifests (giving
`bn_pkg__builtins__rt__*`, NEW), and the .ll's call sites and
declarations don't match.

If you're about to cut a release that touches the mangled-symbol
contract (renames a referenced package, changes the mangler), the
release will fail unless you've either:

1. Already cut a prior release that bakes the new mangling into the
   BUILDER, so its compiled-in literals match current source.
2. Provide compat aliases in the runtime so BUILDER-emitted OLD
   names link against the NEW symbols (transitional approach;
   alias declarations can come out once `BUILDER_VERSION` is bumped
   to a build that uses the new mangling natively).
3. Switch `build-*.sh` to route through gen1 (BUILDER → gen1 →
   final binary) so the BUILDER never directly emits the final
   binary's IR — gen1 has current-source codegen baked in.

If none of these apply and you push the tag anyway, the release CI
will fail and you'll have to clean up the partial release.

## Pitfalls / checklist before tagging

- [ ] `VERSION` is `bnc-X.Y.Z` exactly (no `-preN` suffix).
- [ ] `BUILDER_VERSION` is the PREVIOUS release (not the one you're
      cutting — that doesn't exist yet).
- [ ] CI on the prepare-release commit is green for `-comp*` modes.
- [ ] The release.yml bundle copies all the trees the build scripts
      consume (currently: `pkg/`, `runtime/`, `ifaces/`, `impls/`).
- [ ] The tag points at the commit whose `VERSION` reads `bnc-X.Y.Z`.
      (`git log --oneline -1 bnc-X.Y.Z` should be the release-prep
      commit, not a `-preN` commit.)

After:

- [ ] Bundle works against a small smoke test.
- [ ] `BUILDER_VERSION` bumped to the new release.
- [ ] `VERSION` bumped to the next prerelease (`-pre1`).
- [ ] CI on the post-release commit is green.
