# pkg/ Directory Layout — Migration Plan

Sequenced moves to get the binate repo from the original (pre-2026-05)
`pkg/` layout to the structure defined in
[`pkg-layout-spec.md`](pkg-layout-spec.md).  Covers the binate repo
only; other repos (when they exist) will follow the same spec
independently.

## Status

All steps **LANDED**, captured by the `bnc-0.0.5` release.  Build
state on `main` matches the spec for tiers 0 / 0b / 2; tier 1 / 1x
trees (`pkg/std`, `pkg/stdx`, `ifaces/stdlib`, `impls/stdlib/*`)
are scaffolded empty pending content.

| Step | Subject | Where |
|---|---|---|
| 1 | `ifaces/` + `impls/` skeleton | `16dede56` |
| 2 | `pkg/builtin/` → `pkg/builtins/` | `56d61fda` |
| 3 | `pkg/std` → `pkg/builtins/lang` | `c83d5381` (later split via 6b) |
| 5 (14 leaves) | tier-2 → `pkg/binate/<X>` | `b3955a7a`…`b3d1c6ae` |
| 6a | build scripts: add `ifaces/core` + `impls/core/common` to `-I`/`-L` | `bf2354a4` |
| 6b | `pkg/builtins/lang` → split tree | `91613c27` |
| 6c | `pkg/builtins/testing` → split tree | `6f332c70` |
| **Pre-4** | gen1-routing of `scripts/build-*.sh` + `e2e/*.sh` | `e903aaa9` |
| **4** | `pkg/rt` → split tree (`ifaces/core` + `impls/core/{libc,baremetal}`) | `e8f52e21` |
| **5b** | `pkg/vm` → `pkg/binate/vm` | `a6389017` |
| 7 (release bits) | `release.yml` bundles `ifaces/`+`impls/`; consumer `$BUILDER_LIB` paths | `8707d408` |
| 7 (release cut) | `bnc-0.0.5` release | tag `bnc-0.0.5` at `f4557915` |
| Post-release | `BUILDER_VERSION → bnc-0.0.5`; `VERSION → bnc-0.0.6-pre` | `5dc79ae9` |

The "Pre-4" gen1-routing wasn't in the original plan; it was the
unblocker for Steps 4 + 5b which otherwise hit the BUILDER-skew
trap.  See its section below.

The sections that follow are kept as a historical record of what
each step entailed — useful when a follow-on migration in this
shape needs to be done (e.g. a tier-1 `pkg/std` move someday).

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

## Pre-Step-4 unblocker — gen1-routing `scripts/build-*.sh` + e2e

The BUILDER-skew trap that originally held Step 4: the BUILDER
binary's compiled-in codegen emits hardcoded `bn_pkg__rt__*`
call-site string literals (e.g. `out.WriteStr("call ...
@bn_pkg__rt__Alloc(...)")` in `pkg/binate/codegen/emit_*.bn`).
When BUILDER directly compiles a program that imports
`"pkg/builtins/rt"` (the new path), declarations get mangled from
the new path (`bn_pkg__builtins__rt__Alloc`) while the call sites
still emit OLD names — clang errors with "use of undefined value
'@bn_pkg__rt__Alloc'".

Conformance / unit-test runs sidestep this because they go through
gen1 (`scripts/lib/build-compilers.sh::build_gen1` builds gen1, then
gen1 compiles each test).  Gen1's *compiled-in* codegen is CURRENT
source's codegen, so gen1's outputs use the NEW literals.  But
`scripts/build-{bnc,bni,bnas,bnlint}.sh` and `e2e/{repl,
print-args}.sh` invoked BUILDER directly, bypassing gen1, so they
ran into the mismatch.

Solution: route those scripts through gen1 too.  Two-stage build:

1. BUILDER compiles `cmd/bnc` into a gen1 binary, linked against
   the BUILDER bundle's C runtime (`--runtime $BUILDER_LIB/runtime/
   binate_runtime.c`).  gen1's self-references use OLD mangling and
   resolve against BUILDER's OLD runtime — fully consistent.
2. gen1 compiles the final target (cmd/bnc, cmd/bni, cmd/bnas,
   or cmd/bnlint) from current source, linked against the *checkout's*
   C runtime.  gen1's compiled-in codegen is current-source's, so
   the final binary's calls + declarations + runtime symbols all use
   the NEW mangling.

Same shape as the existing `build_gen2` in
`scripts/lib/build-compilers.sh`.  Adds ~one extra gen1 compile to
each script's wall-clock time — minor.

This change is a NO-OP against current source (where BUILDER's and
source's literals already match), so it can land independently.
Landed at `e903aaa9`.

## Step 4 — `pkg/rt` → split tree

Lands `pkg/rt` directly into the spec's split-tree shape (skipping
the interim `pkg/builtins/rt` collocation the parked work originally
had).  Possible because the previous commit's gen1-routing handles
the BUILDER-skew that would otherwise block it.

File moves:

- `pkg/rt.bni` → `ifaces/core/pkg/builtins/rt.bni`
- `pkg/rt/{rt,rt_test}.bn` →
  `impls/core/libc/pkg/builtins/rt/` (libc-host impl + test)
- `runtime/baremetal_arm32/pkg/rt/rt.bn` →
  `impls/core/baremetal/pkg/builtins/rt/rt.bn`

Symbol + path flips tree-wide:

- `pkg/rt` → `pkg/builtins/rt` (imports + comments)
- `pkg__rt__` → `pkg__builtins__rt__` (mangled-symbol literals
  including `bn_pkg__rt__` globals; covers codegen emit strings,
  pkg/binate/native backends, `runtime/binate_runtime.c`,
  `runtime/libc_stubs.c`, the baremetal linker-script comment, the
  hygiene whitelists)

Executable name-equality strings:

- `"pkg/rt._call_dtor"` / `_call_free_fn` / `_call_shim_scalar` /
  `_call_shim_aggregate` (special-case lookups in
  `pkg/binate/ir/gen_call.bn`) flip
- `"pkg/rt.Refcount"` in `pkg/binate/ir/gen_dtor_emit.bn` flips
- `cmd/bnc/util.bn::ensureRtLoaded`'s synthesized import-path
  string (`imp.Path = "\"pkg/rt\""`) flips
- `cmd/bni/externs_test.bn`'s pinned extern names flip

Search-path updates so the libc impl is reachable + baremetal
target preempts:

- All host `-L` colon-lists that include `impls/core/common` also
  include `impls/core/libc`
- `cmd/bnc/target.bn`'s arm32-baremetal `targetImplPathSuffixes`
  prepends `impls/core/baremetal` before `runtime/baremetal_arm32`,
  so the baremetal pkg/builtins/rt impl wins over the libc-host
  one while the target-specific pkg/bootstrap stub + pkg/semihost
  interface continue to resolve from `runtime/baremetal_arm32`

Landed at `e8f52e21`.

## Step 5 — Move tier-2 packages under `pkg/binate/`

The bulk of the diff.  All 15 tier-2 packages now live under
`pkg/binate/<X>`:

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
pkg/vm                   → pkg/binate/vm           (held + then landed via gen1-routing)
```

14 of these have **no production-code hardcoded mangled-name
references**, so the move only changes mangler-derived symbols
which carry through automatically — they shipped as 14 per-package
commits between `b3955a7a` and `b3d1c6ae`.

The exception was `pkg/vm`: `pkg/binate/codegen/emit_funcvals.bn`'s
`isUniversalTrampoline` check hardcodes
`"bn_pkg__vm__TrampolineScalar"` and `"…Aggregate"`.  BUILDER's
compiled-in copy tested the OLD names; moving pkg/vm before BUILDER
was re-cut would have made BUILDER-direct builds of cmd/bni silently
wrap universal trampolines in `__shim` — wrong codegen.

This was unblocked by the Pre-Step-4 gen1-routing: gen1 has CURRENT
source's `isUniversalTrampoline` baked in, so the final binary's
vtables match.  pkg/vm landed at `a6389017`, alongside Step 4.

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

## Step 6 — Build-script updates + tier-0 ifaces/impls split

This step landed as three sub-commits.

**6a — build-script `-I` / `-L` updates.**  Add `ifaces/core` and
`impls/core/common` to every `-I` / `-L` flag pair (plus
`ifaces/stdlib` and `impls/stdlib/common` for symmetry, even though
those trees stay empty until tier-1 lands).  Loader supports
colon-separated multi-root values per flag, so the pattern is

    -I "$BINATE_DIR:$BINATE_DIR/ifaces/core:$BINATE_DIR/ifaces/stdlib"
    -L "$BINATE_DIR:$BINATE_DIR/impls/core/common:$BINATE_DIR/impls/stdlib/common"

(and the BUILDER variants append `$BUILDER_LIB` or `$blib` to each
list).  Touches `scripts/build-{bnc,bni,bnas,bnlint}.sh`,
`scripts/lib/build-compilers.sh`,
`scripts/{,hygiene}/{fetch-builder,lint}.sh`,
`scripts/unittest/runners/*.sh`,
`conformance/runners/*.sh`, `e2e/{repl,print-args,split-paths}.sh`.

This commit is a functional no-op (new roots resolve to empty
directories); it just wires the search paths so the subsequent
moves can land.

**6b — `pkg/builtins/lang` into split layout.**  Move the canonical-
impl carve-out:

    pkg/builtins/lang.bni       → ifaces/core/pkg/builtins/lang.bni
    pkg/builtins/lang/*.bn      → impls/core/common/pkg/builtins/lang/*.bn

Also extends `scripts/unittest/run.sh` to walk
`$BINATE_DIR/impls` (in addition to `pkg/` and `cmd/`) and strip
the `impls/<tier>/<platform>/` prefix when computing each package's
canonical name — without it, the test runner would report
`pkg/builtins/lang` tests as `impls/core/common/pkg/builtins/lang`.

Two further runner patterns were missed by 6a's sed and needed an
extension pass: `-I "$compile_root" -L "$compile_root"` (gen1/gen2
conformance runners) and `-I "$root:$BINATE_DIR" -L "$root:$BINATE_DIR"`
(interp conformance runners).

**6c — `pkg/builtins/testing` into split layout.**  Move the testing
framework's tiny surface:

    pkg/builtins/testing.bni                 → ifaces/core/pkg/builtins/testing.bni
    pkg/builtins/testing/testing_test.bn     → impls/core/common/pkg/builtins/testing/testing_test.bn

(`testing.bn` was deduped into the `.bni` earlier; the package
has no impl source — only the framework's own self-test.)
`pkg/builtins/` is now empty under `pkg/` and removed.

After 6c, all tier-0 / 0b content lives under `ifaces/core/` +
`impls/core/common/`.  `pkg/builtins/rt` remains parked in `pkg/rt`
(Step 4) until a new BUILDER tarball is cut (Step 7); when Step 4
resumes it'll land directly into `ifaces/core/pkg/builtins/rt.bni`
+ `impls/core/{common,libc}/pkg/builtins/rt/` rather than going
through `pkg/builtins/rt`.

**Deferred to Step 7**: `scripts/fetch-builder.sh`'s `--lib` output.
The current BUILDER bundle (`bnc-0.0.4`) still has the OLD pre-rename
layout under `bundle/lib/pkg/`, and the build scripts include
`$BUILDER_LIB` in the search-root list verbatim.  Once a new BUILDER
ships with the split layout, `--lib` will either expand to a colon
list of root paths or grow `--ifaces` / `--impls-common` subcommands,
and every consumer flips in lockstep.  Defer until that release
cuts.

## Step 7 — BUILDER tarball shape

The `bnc-0.0.5` release packages the new tree:

```
<tarball>/
  bin/{bnc,bni,bnas,bnlint}
  lib/
    pkg/                bundled stdlib (tier-2 + bootstrap + libc + …)
    runtime/            bundled C runtime
    ifaces/core/        tier-0 / 0b interfaces
    impls/core/common/  tier-0 / 0b platform-indep impls
    impls/core/libc/    tier-0 / 0b libc-host impls (currently: pkg/builtins/rt)
    impls/core/baremetal/  tier-0 / 0b baremetal impls (currently: pkg/builtins/rt)
```

`ifaces/stdlib/...` and `impls/stdlib/...` directories ship empty;
content arrives when tier-1 is designed (separate effort).

Release.yml changes that landed at `8707d408`:

- The `Build bundle` step now copies `binate/ifaces` and
  `binate/impls` into the bundle's `lib/` alongside `binate/pkg` and
  `binate/runtime`.
- Every consumer's `-I` / `-L` colon-list extends `$BUILDER_LIB`
  with the per-tier sub-paths (`$BUILDER_LIB/ifaces/core`,
  `$BUILDER_LIB/impls/core/{common,libc}`), so the bundle's
  ifaces/impls are reachable through `fetch-builder.sh --lib`
  without a separate accessor.

bnc-0.0.5 was tagged at commit `f4557915` and released to
`https://github.com/binate/binate/releases/tag/bnc-0.0.5`.  Both
platforms (linux-x64, macos-arm64) built green.  Bundle was
smoke-tested: SHA256 matched manifest, hello + carveout compile +
run, bnc-0.0.5's emitted IR uses `bn_pkg__builtins__rt__…` (the
NEW mangling) throughout.

`BUILDER_VERSION` then bumped to `bnc-0.0.5` and `VERSION` to
`bnc-0.0.6-pre` at `5dc79ae9`.  CI on that commit is the real
verification that bnc-0.0.5 works for all consumers' machines.

`scripts/fetch-builder.sh`'s `--lib` accessor was NOT split into
multiple subcommands — instead the consumer-side colon-lists
include `$BUILDER_LIB/ifaces/core` etc. directly.  Simpler and
keeps `fetch-builder.sh` to one moving part.

## Step 8 — Verification

What was verified locally before each commit / cherry-pick:

- `scripts/hygiene/run.sh` all 12 checks pass.
- `scripts/unittest/run.sh builder-comp` all 34 packages pass.
- `conformance/run.sh builder-comp` (448 / 448).
- `e2e/repl.sh` (52 / 52) and `e2e/print-args.sh` (2 passed, 1
  skipped — pre-existing).

Known pre-existing failures that persist on `main` (not introduced
by this migration; see `claude-todo.md`):

- `-int*` mode regressions (broad — bytecode-VM area).
- `native_aa64` capture-related failures.
- arm32-baremetal `363_aggregate_funcval`.

These don't block any layout step.

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

## Risks — retrospective notes

What materialized vs what didn't, for the historical record:

- **Forgotten symbol references** — handled, but the original plan
  underestimated the BUILDER skew.  When a `bn_pkg__X__…` literal
  is *baked into the BUILDER binary's compiled-in codegen*, no amount
  of careful sed in current source catches it: BUILDER emits its
  baked-in calls + the new manifest's declarations on every compile,
  and they mismatch.  The eventual fix was the gen1-routing of
  build scripts (Pre-Step-4 section above), which keeps BUILDER's
  baked-in mangling out of the final binary entirely.

- **xfail / skip file churn** — minor in practice.  Each leaf-move's
  test plan caught the renames consistently.

- **Bisectability between Step 5 sub-steps** — maintained.  Each
  per-package commit landed green; the one CI failure that materialized
  was a pre-existing arm32 issue (`363_aggregate_funcval`), unrelated
  to layout.

- **fetch-builder.sh consumer divergence** — handled by NOT changing
  `--lib`'s shape; consumers append `$BUILDER_LIB/ifaces/core`,
  `$BUILDER_LIB/impls/core/{common,libc}` to their own colon-lists.
  Keeps `fetch-builder.sh` to one moving part; consumers can land
  independently.

A new risk-class surfaced during execution:

- **Premature release cuts.**  A first attempt at `bnc-0.0.5` was
  cut before Step 4 + 5b were applied in source, which made it
  functionally identical to `bnc-0.0.4` while still adding a
  permanent rung to the build ladder.  Reverted; see
  `release-process.md`'s lead section ("Is this release worth
  cutting?") for the resulting guardrail.  The actual bnc-0.0.5
  cut was substantively different — it bakes the post-Step-4
  mangled-symbol contract into the next BUILDER.
