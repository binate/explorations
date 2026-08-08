# Plan: `CHECK_TOOLS_VERSION` — decouple the hygiene check tools from the BUILDER

## Motivation

The stdx containers migrated to methods-on-generic-types (landed 2026-07-06), a
feature newer than the frozen BUILDER (`bnc-0.0.10`).  The hygiene check tools that
come from the BUILDER bundle — `bnlint` (and, once bundled, `bnfmt`) — therefore
can't parse the containers, so any linted package that *imports* one aborts the
`lint` check.  The container-adoption sweep is thus blocked at the lint gate; the
first adopter, `pkg/binate/format` (uses `vec.Vec`), dragged `format` + its
importer `cmd/bnfmt` into `LINT_SKIP` (`binate` `dc8441a3`), which does not scale.

Crucially, the tree itself BUILDS fine with `bnc-0.0.10`: the build goes
`BUILDER → gen1 → final`, and gen1 has current-source codegen, so the BUILDER never
compiles methods-on-generics.  So the **BUILDER does not need bumping** — only the
CHECK TOOLS do.  Bumping `BUILDER_VERSION` just to get newer bnlint/bnfmt would add
a permanent rung to the build ladder ("permanent additions … forever", per
`release-process.md`) for no build reason.

## Design

Add a repo-root **`CHECK_TOOLS_VERSION`**, parallel to `BUILDER_VERSION`:

- **`BUILDER_VERSION`** — the STABLE release used to build the tree (a build-ladder
  rung).  Advances only through stable `bnc-0.0.X` releases.
- **`CHECK_TOOLS_VERSION`** — the release whose bundled tools (bnlint, bnfmt, …) the
  HYGIENE checks use.  May point at a PRE-RELEASE `bnc-0.0.XpreN`.

**Pre-releases (`bnc-0.0.XpreN`) are general toolchain bundles** (every tool: bnc,
bni, bnas, bnlint, bnfmt + `lib/`) that we HAPPEN to use for checks — a way to
dogfood new features *outside the BUILDER tree* without a ladder rung.  Only STABLE
`bnc-0.0.X` advances `BUILDER_VERSION`.

Decisions (user, 2026-07-10): (1) preN = tool bundles, not ladder rungs — yes.
(2) `CHECK_TOOLS_VERSION` covers bnfmt too — the only reason bnfmt builds from
source today is that it's absent from `bnc-0.0.10`'s bundle.  (3) The pre-release
bundle ships everything.

## Status (2026-07-10)

- **Phase A — LANDED** (`binate` `d20a2b5e`): `CHECK_TOOLS_VERSION` = `bnc-0.0.10`;
  `fetch-builder --check-tools`, `lint.sh`, `bnfmt-format.sh` wired.
  Behavior-preserving (bnlint from 0.0.10, bnfmt from source, `LINT_SKIP`
  unchanged).
- **Phase B — pre1 TAGGED** (`bnc-0.0.11pre1` → `42b3bc83`), release build pending
  (queued behind the runner backlog).  `release.yml` marks preN as a GitHub
  pre-release (`749dde9a`/`b2c1b55d`).  `VERSION` bumped to `bnc-0.0.11pre2` on
  `main` (`d8d078dd`), and the tag points at the last `pre1`-VERSION commit per the
  convention.  Local validation green: all 5 tools build with `bnc-0.0.10` at the
  target; bnlint parses all three containers.
- **Phase C — PENDING** the published pre1 bundle (fetch-builder must be able to
  download it): advance `CHECK_TOOLS_VERSION` → `bnc-0.0.11pre1`; drop
  `pkg/stdx/containers/{vec,hashmap,set}` + `pkg/binate/format` + `cmd/bnfmt` from
  `LINT_SKIP`; bnfmt-format switches to the bundled bnfmt; verify hygiene.

## Phases

### A — infra (behavior-preserving; `CHECK_TOOLS_VERSION` = `bnc-0.0.10`)
- Add `CHECK_TOOLS_VERSION` = `bnc-0.0.10`.
- `fetch-builder.sh`: resolve `--tool` / `--lib` from a caller-selected version
  file (default `BUILDER_VERSION`; check-tool callers select `CHECK_TOOLS_VERSION`
  via a flag/env).
- `lint.sh`: fetch bnlint from `CHECK_TOOLS_VERSION` (fallback: build from source).
- `bnfmt-format.sh`: fetch bnfmt from `CHECK_TOOLS_VERSION` (fallback: build from
  source — `bnc-0.0.10` has no bundled bnfmt, so it keeps building from source
  until `CHECK_TOOLS_VERSION` advances; this also resolves the existing "switch
  bnfmt-format to the bundled bnfmt after the next release" TODO).
- `release.yml`: add `bnfmt` to the bundle (so pre-releases ship it).
- **Net:** with `CHECK_TOOLS_VERSION` = `bnc-0.0.10`, hygiene behaves exactly as
  today (bnlint from 0.0.10; bnfmt from source; `LINT_SKIP` unchanged).  Safe to
  land ahead of any cut.

### B — cut `bnc-0.0.11pre1` (outward-facing; needs explicit approval)
- `VERSION` → `bnc-0.0.11pre1`; `version.bn` → `"0.0.11pre1"`.  Commit, push.
- Tag `bnc-0.0.11pre1`; `release.yml` builds + publishes (mark GitHub prerelease).
- Verify the bundled bnlint + bnfmt parse the migrated containers (dogfood).

### C — adopt the pre-release for checks
- `CHECK_TOOLS_VERSION` → `bnc-0.0.11pre1`.
- `VERSION` → `bnc-0.0.11pre2`; `version.bn` → `"0.0.11pre2"`.
- Remove `pkg/stdx/containers/{vec,hashmap,set}` + `pkg/binate/format` +
  `cmd/bnfmt` from `LINT_SKIP` (bundled bnlint now parses methods-on-generics).
- `bnfmt-format` now uses the bundled bnfmt (drops the build-from-source + cache).
- Verify hygiene green.  `BUILDER_VERSION` never moved.

## Notes / open details
- **Version-format note (2026-07-13):** the `bnc-0.0.11pre1` / `bnc-0.0.11pre2`
  tags and the current `CHECK_TOOLS_VERSION` are spelled WITHOUT a hyphen — they
  predate the hyphenated-prerelease convention (`X.Y.Z-preN`, landed 2026-07-13;
  see `release-process.md`).  Those already-published tags keep their
  non-hyphenated names, so the `preN` spellings throughout this doc are the
  historical ones.  Going forward, `VERSION` (now `bnc-0.0.11-pre3`) and every
  future pre-release use the hyphenated `-preN` form (`-pre1`, `-pre2`, …), which
  version-sync's format check now requires — so a re-cut today would write
  `version.bn → "0.0.11-preN"`, not `"0.0.11preN"`.
- **version-sync** strips the `bnc-` prefix, so a tag and its package literal name
  the same build (e.g. `bnc-0.0.11pre1` ↔ `"0.0.11pre1"`).  When pre1/pre2 were
  cut, version-sync had no format check; it does now (hyphenated `-preN` required),
  and the current in-tree shape is `bnc-0.0.11-pre3`.  Stable `bnc-0.0.11` (no
  `-preN` suffix) is cut later if/when the BUILDER is advanced.
- **release.yml** already triggers on `bnc-*`, so `preN` tags build with no trigger
  change.
- Removing the container/`format`/`cmd/bnfmt` `LINT_SKIP` entries and the
  bnfmt-format build-from-source cache are the Phase-C cleanups — tracked in
  claude-todo.md alongside the BUILDER-lag skip group.
- Future: when stable `bnc-0.0.11` is eventually cut and promoted to BUILDER,
  `CHECK_TOOLS_VERSION` can point at it (or keep riding `preN` bundles).
