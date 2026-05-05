# Plan: Shim Manifest for Cross-Mode Pure-C Extern Function Values

> **Status: DRAFT** — sub-plan of the boot-comp-int-int unblock + the
> "VM extern dispatch: name → function-value registry" line of work.
> Removes the `BC_FUNC_VALUE`-on-pure-C-extern wall that currently
> breaks `registerPureCExterns` when cmd/bni's main runs as bytecode.

## Problem

Bytecode `BC_FUNC_VALUE` constructs a function value by looking the
name up in `vm.Funcs`.  Pure-C externs (pkg/libc.\*, the C-shaped
pkg/bootstrap surface) have no `.bn` body, so `LookupFunc` misses
and the handler crashes with "vm: function not found".  This makes
`var f *func(int) *uint8 = libc.Malloc` work in native cmd/bni but
fail under boot-comp-int-int (where cmd/bni's own main runs as
bytecode in an outer cmd/bni's VM).

The shim and `__vt` for `libc.Malloc` already exist in cmd/bni's
binary — `emit_funcvals.bn` saw the `OP_FUNC_VALUE` reference in
cmd/bni's main during compilation and emitted them.  The host
binary just doesn't have a way for the bytecode VM to find them.

## Non-goals / hard constraints

- **No LLVM-specific tricks.**  Appending linkage works for the
  LLVM backend but not for the hand-rolled native AArch64 backend
  (or any future bare-metal target).  The design must be
  backend-agnostic.
- **No `dlsym`-style runtime symbol enumeration.**  Won't work
  bare-metal.
- **No constructors-before-main.**  Adds a bootstrapping phase
  that's hard to reason about and harder to make portable.
- **Minimal new runtime functions.**  The user's preference is
  data-driven design.  A few helpers are unavoidable (the lookup
  walker), but the source of truth should be static data.

## Design

### Shape: per-module manifests + synthesized master, all data

Every module that emits shims (i.e. has `OP_FUNC_VALUE` references)
also emits a static **per-module manifest**: a flat array of
`{name, vt_addr}` entries.  The compiler synthesizes, in the main
module's emit pass, a **master manifest**: a static array of
pointers to every imported module's per-module manifest.  Both are
plain data — no runtime registration phase, no startup
orchestration.  Lookup is a 10-line walk.

```
┌─────────────────────────┐        ┌──────────────────────────┐
│ bn_main__master_manifest│  ───▶  │ bn_libc__shim_manifest   │
│ (pointers to per-pkg    │  ───▶  │ bn_pkgX__shim_manifest   │
│  manifests, fixed at    │  ───▶  │ ...                      │
│  compile time)          │        └──────────────────────────┘
└─────────────────────────┘                │
                                           ▼
                                ┌─────────────────────────────┐
                                │ {"libc.Malloc", &__vt.bn... }│
                                │ {"libc.Free",   &__vt.bn... }│
                                │ ...                          │
                                └─────────────────────────────┘
```

`pkg/rt.LookupShim(name)` walks the master, returns the `__vt`
address (or null on miss).  `BC_FUNC_VALUE`, on `LookupFunc` miss,
calls `LookupShim`.  If found, constructs the function value with
`vtable = __vt_addr`, `data = null`.  Same dispatch path as a
compile-time-emitted compiled-side function value — the function
value just happens to have been constructed at runtime instead of
being a static const reference.

### Layer responsibilities

**`pkg/rt`** (new):

```binate
type ShimEntry struct {
    Name   *[]const char  // qualified, e.g., "libc.Malloc"
    VtAddr *uint8         // address of __vt.<mangled>
}

type ShimManifest struct {
    Entries *ShimEntry  // fixed-size array of entries
    Count   int
}

// LookupShim walks _shimMaster, returns the __vt address for
// `name` or null if not found.  O(N) — total entry count is
// bounded by total OP_FUNC_VALUE references in the binary, ~30
// for cmd/bni today.  Per-package routing (parse "libc.Malloc"
// → "libc" + "Malloc", check the libc-prefixed manifest first)
// is a future optimization once entries grow.
func LookupShim(name *[]const char) *uint8

// _shimMaster is set by main-module init; readable by LookupShim.
// Encoded as a pair (pointer + count) rather than a slice to keep
// the master itself representable as a static const.
var _shimMasterPtr *ShimManifest
var _shimMasterCount int
```

**`emit_funcvals.bn`**:

- Per-module: emit `bn_<consumer>__shim_manifest` static const
  alongside the existing per-shim emission.  Each entry is a
  `{name_string, vt_addr}` pair.  Module without shims emits
  nothing (no manifest needed).
- The const must be representable as IR-level data so both
  backends lower it to static memory.  This means promoting
  `__vt.<mangled>` from raw-LLVM emission to an IR-level const
  too — its address is taken from manifest entries, and the
  address must be reachable from bytecode.

**Main-module emit (new step)**:

After IR-gen completes for the main module, the compiler walks
the import graph, lists modules that emitted manifests, and
synthesizes:

```
bn_main__master_manifest = ShimManifest{
    Entries: <flat concat of all per-module entries>,
    Count:   <total>,
}
```

OR (alternative shape — simpler per-module emission, slightly
more lookup work):

```
bn_main__master_pointers = [&bn_libc__shim_manifest, &bn_X__shim_manifest, ...]
```

The flat-concat shape is preferred — single array, single walk,
addresses resolve once at link time.  Per-module pointers shape
adds a level of indirection but keeps each module's manifest
self-contained (potentially useful for incremental compile in
the future).  Defer the choice until implementation; either is
data-only and portable.

**`pkg/vm/vm_exec_helpers.bn` `BC_FUNC_VALUE` handler**:

```
- LookupFunc(vm, name)
  ├─ found  → existing VMFunc dispatch (TrampolineScalar /
  │           TrampolineAggregate based on result shape)
  └─ miss   → rt.LookupShim(name)
                ├─ found → build function value:
                │           fv.vtable = vt_addr
                │           fv.data   = null
                │           write to regs[Dst]
                └─ miss  → "function not found", rt.Exit(1)
```

The miss-on-shim path is the same loud failure as today; the
addition is the lookup step in between.

### `__vt` promotion

`emit_funcvals.bn` currently writes `__vt.<mangled>` directly as
LLVM IR (`@__vt.<mangled> = weak_odr constant %BnVtable {...}`).
For the manifest entries to point at it from bytecode-takeable
locations, `__vt` needs to be an IR-level const so both backends
lower it to addressable static memory.

The refactor:
- Introduce an IR-level "module const" emission API (pkg/ir),
  taking a typed value and a name.  Both backends already lower
  module-level globals; this is wrapping that machinery in a
  shape `emit_funcvals.bn` can call.
- Convert the existing `__vt` LLVM-direct emission to use the new
  API.  Backends emit static data with the right linkage.
- The IR const's address is takeable via the existing
  global-address machinery in both backends.

This is the foundation step — without it, the manifest entries
can't reference `__vt` portably.

### Cross-module global addresses (bytecode)

The master-manifest approach (and the per-module manifest entries
referencing `__vt`) needs the bytecode VM to handle global
addresses across modules.  Two flavors:

1. **Within a module**: the IR const's address is local to that
   module's globals area, computed at module-load time
   (`materializeGlobals`).  Already supported.

2. **Cross-module reference**: the master, in the main module,
   references `__vt`s that were emitted by other modules.  At
   bytecode load time, the loader needs to resolve symbolic
   references to global addresses post-load.

For (2), the bytecode VM already does this for cross-module
function calls — `LookupFunc` resolves names to indices.  The
analogous mechanism for globals is a `LookupGlobal` that maps
qualified name to the global's address in the post-load table.
Verify this exists before designing around it; if missing, add it.

### What about the registry / `RegisterExtern` API?

The shim manifest is **distinct from** the existing
`vm.Externs` registry that `RegisterExtern` populates.  The two
serve different purposes:

- **Shim manifest** (this plan): compile-time-resolved lookup
  for `BC_FUNC_VALUE` to construct function values for any
  pure-C extern.  No per-VM state.
- **`vm.Externs` registry**: per-VM dispatch table that
  `execExtern` consults on `BC_CALL` extern-name miss.  Routes
  the dispatch via `dispatchExternBinding` / aggregate path /
  etc.

Once the shim manifest unblocks `BC_FUNC_VALUE`, the existing
registry mechanism (cmd/bni's `registerPureCExterns`) starts
working in bytecode mode without changes.  The "wall" is solely
in `BC_FUNC_VALUE`, not in the registry.

### Dependencies / order of work

Sequential because each piece depends on the previous:

1. **`__vt` IR-level promotion** (pkg/ir + pkg/codegen +
   pkg/native/arm64 + emit_funcvals.bn).  Foundation step;
   no behavior change visible from outside emit_funcvals.bn.

2. **Per-module manifest emission** (emit_funcvals.bn).  Adds
   a static array per module; nothing reads it yet.

3. **Synthesized master in main module** (compile pipeline).
   Wires up the per-module manifests.  Still nothing reads it.

4. **`pkg/rt.LookupShim`** (pkg/rt + pkg/rt.bni).  Reader for
   the master.

5. **`BC_FUNC_VALUE` fallback** (pkg/vm/vm_exec_helpers.bn).
   Calls `LookupShim` on miss; constructs function value.

6. **Verification**: boot-comp-int-int 001_hello passes;
   `e2e/print-args.sh` `bni-under-bni` un-skipped.

Each step lands as its own commit, ideally cherry-pickable
independently for review.

## Open questions

- **Flat-concat master vs per-module-pointer master**: defer
  until implementation reveals a clear preference.
- **Per-package routing in `LookupShim`**: O(N) walk is fine for
  ~30 entries; revisit if the binary grows >>100 shims.
- **Does the bytecode VM already have `LookupGlobal`-equivalent
  for cross-module global addresses?**  Verify in step 1; this
  may force a small VM addition.

## Out of scope

- Migrating the slice-arg externs (`bootstrap.Open/Read/Write/
  Stat/Exec/ReadDir`) — those compose the pointer-arg shim
  convention with the registry; orthogonal to the
  `BC_FUNC_VALUE` wall this plan addresses.
- Retiring `vm_extern.bn`'s residual arms — registry path now
  works for pure-C externs; arms still serve as the fallback
  for not-yet-migrated cases.
- Closure / Phase 2 function values — `data` slot stays null
  for everything this plan handles.
