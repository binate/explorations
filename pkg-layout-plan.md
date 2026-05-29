# pkg/ Directory Layout — Migration Plan

Sequenced moves to get the binate repo from the current `pkg/` layout
to the structure defined in [`pkg-layout-spec.md`](pkg-layout-spec.md).
Covers the binate repo only; other repos (when they exist) will
follow the same spec independently.

## Constraints and ground rules

- **No ABI guarantees today** — symbol-name churn is acceptable across
  every step.
- **No source-level guarantees** — import paths can move freely.
- **CI green between steps** — each commit (or short chain) is
  bisectable and revertible.
- **Tier 1 / 1x don't exist yet.** This plan creates the *structure*
  they'll grow into, but doesn't author stdlib content.
- **`pkg/bootstrap` is slated for removal** and is left untouched by
  this plan — reorganizing it would be wasted effort.
- **Each step is sized to be a self-contained PR** (or commit chain on
  a worktree, cherry-picked).

## Step 1 — `ifaces/` + `impls/` skeleton

Create the empty parallel-tree shape:

```
ifaces/
  core/
  stdlib/             (empty; populated when tier 1 starts to exist)

impls/
  core/
    common/
    libc/             (empty; populated when libc-impls land)
  stdlib/
    common/           (empty)
    libc/             (empty)
```

Skip `baremetal/` subdirs until there's content to put there. Don't
create empty hierarchy that doesn't compile.

No code moves. Loader / `fetch-builder.sh` / build scripts are
unchanged — the new dirs are scaffolding for subsequent steps.

## Step 2 — `pkg/builtin/` → `pkg/builtins/`

Pluralize the namespace.

- `pkg/builtin/testing/` → `pkg/builtins/testing/`.
- Update every `import "pkg/builtin/testing"` site (sed across the
  tree; verify the build).
- Update test-runner xfail/skip filenames:
  `pkg-builtin-testing.xfail.<mode>` → `pkg-builtins-testing.xfail.<mode>`.
- Update hygiene scripts that hard-code the package path
  (e.g. `scripts/hygiene/test-coverage.whitelist`).
- The `cmd/bnc/test.bn` and bootstrap-side `isTestResultReturn` checks
  reference the testing package's path — update those.

Mangled symbols (`bn_pkg__builtin__testing__…` →
`bn_pkg__builtins__testing__…`) shift automatically through the
mangler; per-symbol patching is only needed where the old name is
hard-coded as a string literal.

## Step 3 — `pkg/std` → `pkg/builtins/lang`

Move the canonical-impl carve-out (current `pkg/std` — the
language-defined interfaces + universe-primitive impls package).

- `pkg/std.bni` → `pkg/builtins/lang.bni`.
- `pkg/std/` → `pkg/builtins/lang/`.
- Every `import "pkg/std"` → `import "pkg/builtins/lang"`.
- Symbol references in `runtime/binate_runtime.c` and native backends:
  `bn_pkg__std__…` → `bn_pkg__builtins__lang__…`. Grep:

  ```sh
  grep -rn 'bn_pkg__std__\|bn_pkg__std\b' runtime/ pkg/native/ pkg/codegen/
  ```

- `AllowUniverseRecv` in the type checker — the package-identity check
  uses the import path and switches to `"pkg/builtins/lang"`.
- Update tests that pin specific mangled names
  (`pkg/mangle/mangle_test.bn`, codegen unit tests).

Frees the `pkg/std` name for tier 1.

## Step 4 — `pkg/rt` → `pkg/builtins/rt`

Same shape of work as Step 3.

- `pkg/rt.bni` → `pkg/builtins/rt.bni`.
- `pkg/rt/` → `pkg/builtins/rt/`.
- `import "pkg/rt"` → `import "pkg/builtins/rt"` everywhere.
- Symbol literals in C runtime / native backends:
  `bn_pkg__rt__…` → `bn_pkg__builtins__rt__…`. Higher-volume than
  Step 3 — `rt` is referenced from compiler-emitted code (`pkg/ir`'s
  runtime manifest, inline refcount ops, etc.). Grep:

  ```sh
  grep -rn 'bn_pkg__rt__\|bn_pkg__rt\b' .
  ```

  Several call sites are computed at codegen time via the mangler;
  those carry through automatically. The hand-written ones in
  `runtime/binate_runtime.c` and native-backend symbol emit need
  updates.

After Step 4, all current tier-0 / 0b content lives under
`pkg/builtins/`.

## Step 5 — Move tier-2 packages under `pkg/binate/`

The bulk of the diff. Candidates (verify each is tier 2 — most are
the embeddable-interpreter dependency closure):

```
pkg/asm                  → pkg/binate/asm
pkg/asm/*                → pkg/binate/asm/*
pkg/ast                  → pkg/binate/ast
pkg/buf                  → pkg/binate/buf
pkg/codegen              → pkg/binate/codegen
pkg/debug                → pkg/binate/debug
pkg/ir                   → pkg/binate/ir
pkg/lexer                → pkg/binate/lexer
pkg/lint                 → pkg/binate/lint
pkg/loader               → pkg/binate/loader
pkg/mangle               → pkg/binate/mangle
pkg/native               → pkg/binate/native
pkg/native/*             → pkg/binate/native/*
pkg/parser               → pkg/binate/parser
pkg/token                → pkg/binate/token
pkg/types                → pkg/binate/types
pkg/vm                   → pkg/binate/vm
```

**Per-package commit** (recommended over big-bang to preserve
bisectability):

1. `git mv pkg/X.bni pkg/binate/X.bni`
2. `git mv pkg/X pkg/binate/X`
3. Update every `import "pkg/X"` site — sed across the tree, verify
   with a build.
4. Update xfail/skip filenames: `pkg-X.xfail.<mode>` →
   `pkg-binate-X.xfail.<mode>`.
5. Update literal mangled-name references in C runtime / native
   backends (`bn_pkg__X__…` → `bn_pkg__binate__X__…`).
6. Verify all CI modes pass (or have updated xfail markers).

**Order**: leaves first (packages with no internal dependencies on
other tree-resident binate packages), then upward through the
dependency graph. Roughly:

- `pkg/token`, `pkg/buf`, `pkg/debug`, `pkg/mangle` (leaves)
- `pkg/ast`, `pkg/lexer` (depend on token / buf)
- `pkg/loader`, `pkg/types`
- `pkg/parser`
- `pkg/ir`
- `pkg/codegen`, `pkg/native`, `pkg/native/*`
- `pkg/vm`
- `pkg/asm`, `pkg/asm/*`, `pkg/lint` (more peripheral)

If the bulk-sed is reliable enough to do as one commit, that's also
fine — but the per-package form makes CI failures localized.

## Step 6 — Loader / runner / build-script updates

The new tier-0 trees need their own `-I` / `-L` entries:

- Today: `-I $BINATE_DIR -L $BINATE_DIR` discovers everything from
  `pkg/`.
- After Steps 1-4: tier-0 ifaces+impls live under `ifaces/core/`
  and `impls/core/{common,libc}/`. These need additional `-I` / `-L`
  entries.
- After Step 5: in-repo tier-2 lives at `pkg/binate/<X>` — still
  discoverable with the existing `-I $BINATE_DIR -L $BINATE_DIR`.

Concrete touches:

- `scripts/build-bnc.sh`, `scripts/build-bni.sh`, `scripts/build-bnas.sh`,
  `scripts/build-bnlint.sh`: add tier-0 ifaces+impls roots to their
  `-I` / `-L` invocations.
- `scripts/fetch-builder.sh`: its `--lib` output expands from one
  root to several; either change `--lib` to emit a multi-root string,
  or grow new subcommands (`--ifaces`, `--impls-common`, …).
  Update the build helpers in `scripts/lib/build-compilers.sh` and
  the test runners in `scripts/unittest/` / `conformance/run.sh`
  in lockstep.

Do this step after Step 5 lands so the loader sees the new layout
in one switch.

## Step 7 — BUILDER tarball shape

The next `bnc-X.Y.Z` release packages the new tree:

```
<tarball>/
  bin/
  ifaces/core/...
  impls/core/{common,libc}/...
```

`ifaces/stdlib/...` and `impls/stdlib/...` join once tier 1 has
content (separate effort).

`.github/workflows/release.yml` needs to assemble this shape when
staging the bundle; the per-platform matrix entries need to know
which `impls/<platform>/` subtree to include. `scripts/fetch-builder.sh`
consumers need to handle the new layout on download.

Defer until the next release cuts.

## Step 8 — Verification

- All CI modes green: hygiene, unit tests (every mode), conformance,
  e2e, perf.
- `scripts/hygiene/*.sh` clean (file-format / naming / godoc / etc.
  may need package-name updates).
- `examples/selftest.bn` and friends still build and run.
- README and project-structure docs updated to reflect the new tree.

## Out-of-scope follow-ups

Separate TODOs / plan docs, not covered here:

- **Designing tier 1 (stdlib)** — `io`, `os`, containers, etc. The
  empty `ifaces/stdlib/` and `impls/stdlib/` trees just sit there
  until this is done.
- **Designing tier 1x (stdx)** — same.
- **Removing `pkg/bootstrap`** — separate effort with its own
  ordering and prerequisites.
- **Per-file selection within a package** (the "shared core + per-
  variant file" weakness) — future, possibly tied to a build-config
  or annotation system.
- **Package manager design** — separate spec + plan.

## Risks

- **Forgotten symbol references** — every `bn_pkg__X__…` literal in
  `runtime/binate_runtime.c`, native backends, runtime manifest
  tables, and test expectations. Mitigation: exhaustive
  `grep -rn "bn_pkg__"` before each move; rely on CI to surface
  what's missed.
- **xfail / skip file churn** — many of these files name packages
  via the dashes convention. Each rename touches several xfail
  filenames; easy to miss one.
- **Bisectability between Step 5 sub-steps** — keep each per-package
  commit green. If a particular move can't land green (e.g., depends
  on a sibling), batch with the next package rather than landing red.
- **fetch-builder.sh consumer divergence** — once the script's
  output shape changes (Step 6), every consumer must update in
  lockstep. Plan: identify all consumers up front, change them in
  one commit chain.
