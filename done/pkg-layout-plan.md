# pkg/ Directory Layout — Migration Plan

Sequenced moves to get the binate repo from the original (pre-2026-05)
`pkg/` layout to the structure defined in
[`pkg-layout-spec.md`](pkg-layout-spec.md).  Covers the binate repo
only; other repos (when they exist) will follow the same spec
independently.

## Status

**COMPLETE (shipped at `bnc-0.0.5`); kept for design rationale.**
Build state on `main` matches the spec for tiers 0 / 0b / 2; tier 1 /
1x trees (`pkg/std`, `pkg/stdx`, `ifaces/stdlib`, `impls/stdlib/*`)
are scaffolded empty pending content.

The "Pre-Step-4" gen1-routing wasn't in the original plan; it was the
unblocker for the `pkg/rt` and `pkg/vm` moves, which otherwise hit the
BUILDER-skew trap.  Its section and the retrospective risk notes below
are the durable record of *why* the migration took the shape it did —
useful when a follow-on migration in this shape needs to be done
(e.g. a tier-1 `pkg/std` move someday).

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

## The BUILDER-skew trap and gen1-routing (Pre-Step-4 unblocker)

The BUILDER-skew trap that originally held the `pkg/rt` move: the
BUILDER binary's compiled-in codegen emits hardcoded `bn_pkg__rt__*`
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
source's literals already match), so it could land independently.

The same trap applies to `pkg/vm`:
`pkg/binate/codegen/emit_funcvals.bn`'s `isUniversalTrampoline` check
hardcodes `"bn_pkg__vm__TrampolineScalar"` and `"…Aggregate"`.
BUILDER's compiled-in copy tested the OLD names; moving pkg/vm before
BUILDER was re-cut would have made BUILDER-direct builds of cmd/bni
silently wrap universal trampolines in `__shim` — wrong codegen.
gen1-routing fixes it the same way: gen1 has CURRENT source's
`isUniversalTrampoline` baked in, so the final binary's vtables match.

## Target layout (what shipped)

### Namespace renames

- `pkg/builtin/testing/` → `pkg/builtins/testing/` (pluralize the
  namespace).
- `pkg/std` (the language-defined interfaces + universe-primitive
  impls package) → `pkg/builtins/lang`, which frees the `pkg/std`
  name for tier 1.

### `pkg/rt` → split tree

`pkg/rt` landed directly into the spec's split-tree shape (skipping
the interim `pkg/builtins/rt` collocation the parked work originally
had — possible once gen1-routing handled the BUILDER-skew):

- `pkg/rt.bni` → `ifaces/core/pkg/builtins/rt.bni`
- `pkg/rt/{rt,rt_test}.bn` →
  `impls/core/libc/pkg/builtins/rt/` (libc-host impl + test)
- `runtime/baremetal_arm32/pkg/rt/rt.bn` →
  `impls/core/baremetal/pkg/builtins/rt/rt.bn`

Search-path notes so the libc impl is reachable + the baremetal
target preempts:

- All host `-L` colon-lists that include `impls/core/common` also
  include `impls/core/libc`.
- `cmd/bnc/target.bn`'s arm32-baremetal `targetImplPathSuffixes`
  prepends `impls/core/baremetal` before `runtime/baremetal_arm32`,
  so the baremetal pkg/builtins/rt impl wins over the libc-host
  one while the target-specific pkg/bootstrap stub + pkg/semihost
  interface continue to resolve from `runtime/baremetal_arm32`.

### tier-2 packages under `pkg/binate/`

All 15 tier-2 packages now live under `pkg/binate/<X>`:

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

14 of these have **no production-code hardcoded mangled-name
references**, so the move only changes mangler-derived symbols
which carry through automatically.  The exception was `pkg/vm` (see
the `isUniversalTrampoline` note above), unblocked by gen1-routing.

**Per-package commits** were preferred over a big-bang move to
preserve bisectability, and **leaves first** (packages with no
internal dependencies on other tree-resident binate packages), then
upward through the dependency graph:

- `pkg/token`, `pkg/buf`, `pkg/debug`, `pkg/mangle` (leaves)
- `pkg/ast`, `pkg/lexer` (depend on token / buf)
- `pkg/loader`, `pkg/types`
- `pkg/parser`
- `pkg/ir`
- `pkg/codegen`, `pkg/native`, `pkg/native/*`
- `pkg/vm`
- `pkg/asm`, `pkg/asm/*`, `pkg/lint` (more peripheral)

### tier-0 ifaces/impls split

The canonical-impl carve-out and testing framework moved into the
split layout:

```
pkg/builtins/lang.bni                    → ifaces/core/pkg/builtins/lang.bni
pkg/builtins/lang/*.bn                   → impls/core/common/pkg/builtins/lang/*.bn
pkg/builtins/testing.bni                 → ifaces/core/pkg/builtins/testing.bni
pkg/builtins/testing/testing_test.bn     → impls/core/common/pkg/builtins/testing/testing_test.bn
```

(`testing.bn` was deduped into the `.bni` earlier; the package has no
impl source — only the framework's own self-test.)  After this,
`pkg/builtins/` is empty under `pkg/` and removed; all tier-0 / 0b
content lives under `ifaces/core/` + `impls/core/common/`.

`scripts/unittest/run.sh` was extended to walk `$BINATE_DIR/impls`
(in addition to `pkg/` and `cmd/`) and strip the
`impls/<tier>/<platform>/` prefix when computing each package's
canonical name — without it, the test runner reports e.g.
`pkg/builtins/lang` tests as
`impls/core/common/pkg/builtins/lang`.

### Build-script search-path pattern

The loader supports colon-separated multi-root values per `-I` / `-L`
flag, so the pattern is:

    -I "$BINATE_DIR:$BINATE_DIR/ifaces/core:$BINATE_DIR/ifaces/stdlib"
    -L "$BINATE_DIR:$BINATE_DIR/impls/core/common:$BINATE_DIR/impls/stdlib/common"

(`ifaces/stdlib` and `impls/stdlib/common` are included for symmetry
even though those trees stay empty until tier-1 lands; the BUILDER
variants append `$BUILDER_LIB`/`$blib` to each list.)

## BUILDER tarball shape (`bnc-0.0.5`)

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

**Decision: `scripts/fetch-builder.sh`'s `--lib` accessor was NOT
split into multiple subcommands.**  Instead the consumer-side
colon-lists include `$BUILDER_LIB/ifaces/core`,
`$BUILDER_LIB/impls/core/{common,libc}` directly.  Simpler, keeps
`fetch-builder.sh` to one moving part, and lets consumers land
independently.

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
  build scripts (the BUILDER-skew section above), which keeps
  BUILDER's baked-in mangling out of the final binary entirely.

- **fetch-builder.sh consumer divergence** — handled by NOT changing
  `--lib`'s shape; consumers append `$BUILDER_LIB/ifaces/core`,
  `$BUILDER_LIB/impls/core/{common,libc}` to their own colon-lists.
  Keeps `fetch-builder.sh` to one moving part; consumers can land
  independently.

A new risk-class surfaced during execution:

- **Premature release cuts.**  A first attempt at `bnc-0.0.5` was
  cut before the `pkg/rt` + `pkg/vm` moves were applied in source,
  which made it functionally identical to `bnc-0.0.4` while still
  adding a permanent rung to the build ladder.  Reverted; see
  `release-process.md`'s lead section ("Is this release worth
  cutting?") for the resulting guardrail.  The actual bnc-0.0.5
  cut was substantively different — it bakes the post-move
  mangled-symbol contract into the next BUILDER.
