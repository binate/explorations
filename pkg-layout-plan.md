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

## Step 4 — `pkg/rt` → `pkg/builtins/rt`  *(held until Step 7)*

The same shape of work as Step 3, but **cannot land before a new
BUILDER tarball is cut** (Step 7). The current BUILDER's compiled-in
codegen emits hardcoded `bn_pkg__rt__*` call-site string literals
(e.g. `out.WriteStr("call ... @bn_pkg__rt__Alloc(...)")` in
`pkg/codegen/emit_*.bn`). When BUILDER directly compiles a program
that imports `"pkg/builtins/rt"` (the new path), the declarations get
mangled from the new path (`bn_pkg__builtins__rt__Alloc`) while the
call sites still emit OLD names — clang errors with "use of
undefined value '@bn_pkg__rt__Alloc'".

This is masked for gen1-routed builds (conformance, unit tests)
because gen1 is built FROM CURRENT source — once compiled, its NEW
codegen emits NEW names consistently. But direct-BUILDER builds
(`scripts/build-bni.sh`, `e2e/repl.sh`) hit the mismatch.

When this step is taken up, the mechanical work is:

- `pkg/rt.bni` → `pkg/builtins/rt.bni`.
- `pkg/rt/` → `pkg/builtins/rt/`.
- `runtime/baremetal_arm32/pkg/rt/` →
  `runtime/baremetal_arm32/pkg/builtins/rt/` (path-shadow that the
  arm32-baremetal target relies on).
- `import "pkg/rt"` → `import "pkg/builtins/rt"` everywhere.
- Symbol literals tree-wide: `pkg__rt__…` → `pkg__builtins__rt__…`
  (covers `bn_pkg__rt__…` globals, internal mangler intermediates,
  test pins). Grep:

  ```sh
  grep -rn 'pkg__rt__\|"pkg/rt' .
  ```

- Executable name-equality strings: `"pkg/rt._call_dtor"` /
  `_call_free_fn` / `_call_shim_scalar` / `_call_shim_aggregate`
  (special-case lookups in `pkg/ir/gen_call.bn`) and
  `"pkg/rt.Refcount"` (in `pkg/ir/gen_dtor_emit.bn`).
- `cmd/bnc/util.bn`'s synthesized import-path string
  (`imp.Path = "\"pkg/rt\""`).
- Whitelist updates in `scripts/hygiene/conformance-imports.sh`
  (`ALLOWED_REAL`), `scripts/hygiene/naming.whitelist`, and
  `scripts/hygiene/test-coverage.whitelist`.

After Step 4 (when it eventually lands), all current tier-0 / 0b
content lives under `pkg/builtins/`.

A parked WIP commit on the `park-step4` branch carries this work
already done, against the source tree as it stood post-Step-3 — pick
it up after Step 7 ships a BUILDER built from a tree with the
rename applied.

## Step 5 — Move tier-2 packages under `pkg/binate/`

The bulk of the diff. Candidates (verify each is tier 2 — most are
the embeddable-interpreter dependency closure):

```
pkg/asm                  → pkg/binate/asm           (safe pre-BUILDER)
pkg/asm/*                → pkg/binate/asm/*         (safe)
pkg/ast                  → pkg/binate/ast           (safe)
pkg/buf                  → pkg/binate/buf           (safe)
pkg/codegen              → pkg/binate/codegen       (safe)
pkg/debug                → pkg/binate/debug         (safe)
pkg/ir                   → pkg/binate/ir            (safe)
pkg/lexer                → pkg/binate/lexer         (safe)
pkg/lint                 → pkg/binate/lint          (safe)
pkg/loader               → pkg/binate/loader        (safe)
pkg/mangle               → pkg/binate/mangle        (safe)
pkg/native               → pkg/binate/native        (safe)
pkg/native/*             → pkg/binate/native/*      (safe)
pkg/parser               → pkg/binate/parser        (safe)
pkg/token                → pkg/binate/token         (safe)
pkg/types                → pkg/binate/types         (safe)
pkg/vm                   → pkg/binate/vm   *(hold for Step 7)*
```

The (safe) packages have **no production-code hardcoded mangled-name
references**, so the move only changes mangler-derived symbols
which carry through automatically. The exception is `pkg/vm`:
`pkg/codegen/emit_funcvals.bn`'s `isUniversalTrampoline` check
hardcodes `"bn_pkg__vm__TrampolineScalar"` and `"…Aggregate"`.
BUILDER's compiled-in copy still tests the OLD names; if we move
pkg/vm before BUILDER is re-cut, BUILDER-direct builds of cmd/bni
would silently wrap universal trampolines in `__shim` (wrong
codegen). Pkg/vm joins Step 4 in the held-for-Step-7 queue.

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
