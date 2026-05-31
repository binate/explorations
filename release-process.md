# Cutting a `bnc-X.Y.Z` Release

Step-by-step for releasing a new `bnc-X.Y.Z` tarball.  Also documents
how the moving parts fit together so you don't have to reverse-engineer
`release.yml` every time.

## Inputs

- **`VERSION`** at repo root — names what `cmd/bnc --version` reports
  for builds **from the current tree**.  Two shapes:
  - `bnc-X.Y.Z` — release shape; what `VERSION` says exactly on the
    commit that gets tagged `bnc-X.Y.Z`.
  - `bnc-X.Y.Z-pre` — pre-release shape; `-pre` says "this tree is
    in-progress toward X.Y.Z and not yet a tagged release."  Default
    state between releases.
- **`BUILDER_VERSION`** at repo root — names which prior-release
  binary `scripts/fetch-builder.sh` downloads to use as the BUILDER
  during local + CI builds.  Always a concrete `bnc-X.Y.Z` (no `-pre`).
- **`.github/workflows/release.yml`** — the release CI.  Triggered
  by push of a tag matching `bnc-*`.  Builds per-platform bundles
  and attaches them to a GitHub release named after the tag.

## The seven-step release

The release itself is two commits + one tag push, with verification
in between.

### 1. Prepare the tree

Make sure the tree you want to ship is on `main` and CI is happy
(or as happy as it gets — see "pre-existing failures" below).  All
the package-layout / hardcoded-symbol / runtime / build-script
changes that this release ought to capture should already be
committed and pushed.

### 2. Drop `-pre` from `VERSION`

Edit `VERSION` from `bnc-X.Y.Z-pre` → `bnc-X.Y.Z`.  This is the
commit that will be tagged.

Commit shape:

    Release bnc-X.Y.Z

    Drop -pre suffix; this commit will be tagged bnc-X.Y.Z.

Push to `main`.

### 3. Tag and push

Tag the just-pushed commit and push the tag.  From the main
checkout (`~/binate/binate`):

    git tag bnc-X.Y.Z
    git push origin bnc-X.Y.Z

The tag push triggers `release.yml`.  Don't tag a `-pre` commit; the
release CI happily builds whatever the tag points at, and a `-pre`
build would ship as a "release" with a broken version string.

### 4. Verify the release

Watch the `Release` workflow in GitHub Actions.  When all matrix
jobs (linux-x64, macos-arm64) finish and the `Publish release`
job succeeds, the GitHub release exists at
`https://github.com/binate/binate/releases/tag/bnc-X.Y.Z`.

Smoke test the bundle:

1. Download one of the platform tarballs + `SHA256SUMS`.
2. Verify the SHA matches.
3. Extract; check `bin/bnc --version` reports `bnc-X.Y.Z`.
4. Check `lib/` contains everything the build scripts expect — at
   minimum `pkg/`, `runtime/`, `ifaces/core/`, `impls/core/common/`.
5. Run a small program through the extracted `bin/bnc` against the
   bundled `lib/` to confirm the tier-0 carve-out resolves and
   `bn_pkg__builtins__lang__*` symbols are present in the runtime.

If anything's wrong, delete the GitHub release + tag and start over
— don't ship a bad release just because the tag is already there.

### 5. Bump `BUILDER_VERSION`

Once the release is verified good, point the checkout at it:

    BUILDER_VERSION:  bnc-<old> → bnc-X.Y.Z

`scripts/fetch-builder.sh` reads this file to resolve which release
to download as the BUILDER for local + CI builds.  Bumping it makes
the just-shipped release the new BUILDER everyone uses.

### 6. Bump `VERSION` to the next pre-release

Edit `VERSION` from `bnc-X.Y.Z` → `bnc-X.Y.(Z+1)-pre`.  This marks
the tree as "post-X.Y.Z, in-progress toward X.Y.(Z+1)."  The `-pre`
suffix is what flags a build as "not a tagged release."

Combine with the BUILDER_VERSION bump into one commit:

    Post-release: bump BUILDER_VERSION → bnc-X.Y.Z, VERSION → bnc-X.Y.(Z+1)-pre

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

The `-pre` ↔ release-shape dance keeps the `cmd/bnc --version` string
honest:

- Anything built from a commit whose `VERSION` says `bnc-X.Y.Z-pre`
  reports `bnc-X.Y.Z-pre` — clearly **not** the tagged release.
- The tagged commit itself says `bnc-X.Y.Z` exactly — `cmd/bnc
  --version` on that build matches the tag.
- The very next commit on `main` says `bnc-X.Y.(Z+1)-pre` — so
  builds from `main` after the tag again clearly distinguish from
  the release.

If you forget step 6 (bump VERSION to next `-pre`), every build
from main between this release and the next would report itself
as `bnc-X.Y.Z` even though it's not the tagged commit.  Don't
forget.

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

- [ ] `VERSION` is `bnc-X.Y.Z` exactly (no `-pre`).
- [ ] `BUILDER_VERSION` is the PREVIOUS release (not the one you're
      cutting — that doesn't exist yet).
- [ ] CI on the prepare-release commit is green for `-comp*` modes.
- [ ] The release.yml bundle copies all the trees the build scripts
      consume (currently: `pkg/`, `runtime/`, `ifaces/`, `impls/`).
- [ ] The tag points at the commit whose `VERSION` reads `bnc-X.Y.Z`.
      (`git log --oneline -1 bnc-X.Y.Z` should be the release-prep
      commit, not a `-pre` commit.)

After:

- [ ] Bundle works against a small smoke test.
- [ ] `BUILDER_VERSION` bumped to the new release.
- [ ] `VERSION` bumped to the next `-pre`.
- [ ] CI on the post-release commit is green.
