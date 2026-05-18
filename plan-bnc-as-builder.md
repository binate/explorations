# Plan: bnc as Builder (Replacing the Bootstrap Interpreter)

The bootstrap interpreter (`bootstrap/` Go program) has run its course as the canonical first-stage compiler.  It is slow (every test compile pays the interp tax), restricts the language to a "bootstrap subset" that we maintain by hand, and ties the binate repo's CI/dev story to a sibling repo's working tree.  Replace it with a tagged binary of a previous `bnc` release.  Day-to-day development uses the prebuilt binary; the ladder back to the bootstrap interpreter exists only as an escape hatch for when no prebuilt is available.

## Goals

- **Speed.** Test compiles invoke a real native binary instead of `go run bootstrap`.
- **Decouple.** Binate's CI / tests no longer depend on the bootstrap repo's working tree.
- **Drop the bootstrap subset.**  `bnc` features become usable across the codebase without first being added to the bootstrap interpreter.
- **Versioned identity.**  "What state of bnc compiles this commit" becomes a discrete pin (a tag + binary on a GitHub release) instead of "whatever HEAD of bootstrap interpreted at run time."

## Non-goals

- Removing the bootstrap interpreter entirely.  It stays in its own repo with its own tests; it just isn't on the day-to-day path of binate.
- Vendoring the builder binary into the binate repo.  Releases ship via GitHub; the fetcher script grabs them on demand and caches locally.
- Cross-target builder binaries.  The builder runs on the host; it emits whatever target the test mode asks for.

## Concepts

### `BUILDER_VERSION`

A file at the repo root containing a single line: the bnc version required to build the current source tree.

```
bnc-0.0.1
```

During the transition phase the file may name the bootstrap interpreter:

```
bootstrap-0.0.1
```

The fetcher script branches on the prefix.  Once a real `bnc-X.Y.Z` lands, `bootstrap-` should disappear from `BUILDER_VERSION` for good.

### `VERSION`

A file at the repo root containing the *current* version this branch is heading toward.  Working-tree convention: `bnc-X.Y.Z-pre` for any commit that isn't itself a release; the release commit drops the `-pre` suffix and gets the tag.

```
bnc-0.0.2-pre
```

`VERSION` is hand-maintained.  Drift from git tags is avoided by checking in CI that the release commit's `VERSION` matches the tag being pushed.

### `builder` runner mode

The replacement for `boot`.  Instead of `go run bootstrap -- bnc-source ...`, the runner invokes the prebuilt `BUILDER_VERSION` bnc binary directly:

```
$BUILDER_BNC --root "$BINATE_DIR" --build-dir "$BDIR" "$pkg"
```

Mode-name convention follows the existing chains: `builder` (compile via builder bnc), `builder-comp` (builder bnc compiles bnc → gen-N bnc compiles tests), `builder-comp_native_aa64`, etc.  The two-segment forms replace today's `boot-comp`, `boot-comp_native_aa64`, ....

## Release infrastructure

### Release tags + binaries

Tags follow `bnc-X.Y.Z`.  A GitHub release workflow triggers on tag push and:

1. Builds bnc from source on each supported (host, target) platform.  Initial matrix: linux-x64, macos-x64, macos-arm64 (host).  arm64-linux added when arm32-linux work matures.
2. Uploads the binaries as release assets, named predictably: `bnc-<version>-<host-os>-<host-arch>` (e.g. `bnc-0.0.1-macos-arm64`).
3. Generates a manifest with sha256 sums so the fetcher can verify integrity.

Releases are permanent; CI caches are a speed optimization on top.

### Fetcher script

`scripts/fetch-builder.sh` (or equivalent):

1. Reads `BUILDER_VERSION`.
2. If the prefix is `bnc-`, looks for a cached binary at `~/.cache/binate/builders/<version>/<os>-<arch>/bnc`.  If absent, downloads from the matching GitHub release and verifies sha256.
3. If the prefix is `bootstrap-`, no fetch — just `cd` into the sibling `bootstrap/` repo (or a vendored copy keyed by version) and `go run .` from there.
4. Prints the absolute path of the resolved binary on stdout for the caller's $(...) capture.

The script also supports `--rebuild-from-source`: ignore the cache and rebuild the builder from a checkout of the `BUILDER_VERSION` tag in this same repo.  Used by the release workflow itself and as the "always works" escape hatch when the cache + release both miss.

### CI cache key

GitHub Actions cache keyed on `BUILDER_VERSION` + os + arch.  Cache hits skip the download; cache misses fall through to fetch from the release.

## Phases

### Phase 0: Land the first release

**Goal.**  Have a `bnc-0.0.1` tag with binaries on GitHub before any `BUILDER_VERSION` work in the repo.

1. Cut release candidate from current main: confirm conformance + unit tests green across all supported modes.
2. Tag `bnc-0.0.1`.  Manual GitHub release create with the platform binaries attached (later automated by phase 4).
3. Write the SHA256 manifest.

No code changes to main during this phase — just establish the artifact exists.

### Phase 1: BUILDER_VERSION + fetcher

**Goal.**  Wire up the builder fetcher and the `BUILDER_VERSION` file; CI still runs `boot` as canonical.

1. Add `BUILDER_VERSION = bnc-0.0.1` at the repo root.
2. Add `VERSION = bnc-0.0.2-pre`.
3. Write `scripts/fetch-builder.sh`.
4. Write a no-op smoke test in CI that fetches the builder, compiles `conformance/001_hello.bn`, and checks the output.  Doesn't replace anything yet — just proves the fetcher works.

### Phase 2: `builder` runner alongside `boot`

**Goal.**  Both modes run side-by-side in CI for one or two commits, so we can confirm parity before flipping.

1. Add `scripts/unittest/runners/builder.sh` mirroring `boot.sh` but using the fetched binary.
2. Add `conformance/runners/builder.sh` analogously.
3. Add the `builder` mode to the all-mode-set.  CI runs both `boot` and `builder` for the transition window.
4. Diff-check: any test that passes in `boot` but fails in `builder` (or vice versa) is a parity bug to fix before phase 3.

### Phase 3: Move bootstrap-subset tests to the bootstrap repo

**Goal.**  Tests that were intended to exercise the bootstrap interpreter's subset move to where they're actually verifying that subject.

1. In the bootstrap repo, set up `conformance/` mirroring binate's directory layout.  Copy the boot-runnable subset of tests over.
2. Set up the bootstrap repo's CI to run its conformance against its own interpreter.
3. Remove those tests from the binate repo (or leave them in place and stop running them under `boot` in CI).

The cross-repo test duplication is the cost of cleanly separating "tests the bootstrap interpreter" from "tests bnc."  An alternative considered: keep tests in binate and have the bootstrap repo's CI pull them via submodule.  Either works; submodule adds dependency complexity but avoids the duplication.  Recommend starting with copy + revisit if it becomes burdensome.

### Phase 4: Make `builder` canonical, drop `boot` from binate

**Goal.**  `boot` is gone from binate; `builder` is the first stage everywhere.

1. Rename mode references: `boot-comp` → `builder-comp`, `boot-comp_native_aa64-comp_native_aa64` → `builder-comp_native_aa64-comp_native_aa64`, etc.  Mass rename of `.xfail.boot*` files to `.xfail.builder*`.
2. Drop the `boot.sh` runner files and the `boot` mode from the all-mode-set.
3. Drop the bootstrap dependency from binate's CI workflows (no more `cd bootstrap && go run .`).
4. Automate the release workflow (the manual step from phase 0).  Tag pushes trigger platform-matrix builds and GitHub release publishing.

### Phase 5: Steady state

`BUILDER_VERSION` advances when a new feature requires bnc capabilities the current builder lacks (see chicken-and-egg below).  Cadence: probably tied to features, not time — when something useful accumulates, cut a release.  Pre-release work can land freely behind the existing builder.

## Chicken-and-egg situation

A change that wants to *use* a new bnc feature in binate's own source requires the builder to *support* that feature.  The workflow:

1. Land the feature in bnc (still buildable by the current builder).
2. Verify it works in CI under the new-feature mode (no temporary xfails needed yet — the feature isn't used in source).
3. Release a new bnc version that contains the feature.
4. Bump `BUILDER_VERSION` in a separate commit.
5. *Now* land code that uses the feature; the new builder can compile it.

This is essentially the same loop as the current `boot` mode (where bootstrap-subset features need a bootstrap update before being used), but it lives in releases instead of an unversioned sibling repo — and the lag is bounded by release cadence rather than "when the bootstrap interpreter happens to catch up."

### Handling tests for the new feature

Tests that exercise a not-yet-released feature can use the same xfail mechanism the boot mode used: `<test>.xfail.builder` with a clear "remove on next release" comment.  Once the new bnc is released and `BUILDER_VERSION` bumped, the xfail goes away in the same commit.  Net experience: temporary xfails analogous to current `.xfail.boot`, but with a known finite lifetime.

## Edge cases

### "I just want to hack on this"

Contributors should be able to clone and build without explicit setup.  `scripts/fetch-builder.sh` autoruns on first build; if there's no network and no cache, it falls back to `--rebuild-from-source` (which itself recurses to find the appropriate prior builder, eventually reaching `bootstrap-X.Y.Z` as the universal base case).  Document the network requirement.

### Cross-platform binary availability

If a contributor is on an unsupported platform (e.g., FreeBSD), there's no prebuilt binary.  Same `--rebuild-from-source` path applies.  Long-term: expand the release matrix as the user base does.

### Drift between `VERSION` and tags

CI check on the release workflow: `VERSION` (sans `-pre`) must equal the pushed tag.  Mismatch fails the release.

### Multiple builders coexisting

Cache directory keyed on version, so multiple `BUILDER_VERSION`s coexist (e.g., on branches at different points).  `~/.cache/binate/builders/bnc-0.0.1/...`, `bnc-0.0.2/...`, etc.

## Open questions

- **Tag prefix:** `bnc-X.Y.Z` or just `vX.Y.Z` (Go convention)?  `bnc-` is more self-documenting when multiple binaries might ship from the same repo (bni, bnas, bnlint).  Recommend `bnc-`.
- **Versioning scheme:** semver, calver, or 0.0.X-forever-until-1.0?  Probably 0.0.X for now — none of the API is stable.  Bump to 0.1.0 when the language reaches a meaningful milestone (e.g. "self-host without bootstrap fallback").
- **Bootstrap repo lifecycle:** kept indefinitely (it's still the only way to build bnc-0.0.1 from scratch), but development on it slows after phase 4.  Eventually moves to "maintenance only" status.
- **Workflow for the bootstrap repo when it's needed:** how does someone produce an updated bootstrap interpreter if a new bnc release needs language features the current bootstrap doesn't support?  Answer: it doesn't; releases are built by the previous bnc release, not by bootstrap.  Bootstrap is only the universal base case for the absolute first release.
