# Plan: package-level injection into the bytecode VM (Gap-2 VM-backend project)

Status: **Part A (builtin auto-injection) LANDED 2026-06-12** (binate `a8ba52f2`);
**Part B (Gap-2 bytecode `_Package`, §2a below) PENDING**. This is the
motivating use-case for the `_Package()` / `reflect.Package.Functions` work —
see `plan-package-introspection-phase-b.md` and the B0 entries in
`claude-todo.md`.

## Part A — LANDED (binate `a8ba52f2`)

`RegisterStandardExterns` (`vm/extern_register_std.bn`) now drives **rt** (and
the empty **reflect**) through `registerPackageFunctions(vmInst, <pkg>._Package())`
— enumerate `_Package().Functions` and `RegisterExtern` each entry — replacing
the hand-maintained per-function rt block. The binding path below was verified
end-to-end (`fi.Value` is the static `@__handle` block; `fvAddr` and `rawFnAddr`
both point at it). Per owner decision:
- **bootstrap stays hand-bound** — it is on the deprecation path and its
  C-shaped surface is mostly **extern**, which the FunctionInfo table excludes
  (`emit_pkg_functions.bn` skips `IsExtern`). Don't special-case the table for a
  package being removed; accommodate the oddity temporarily.
- The 3 `_Package` accessors stay hand-bound (`registerPackageDescriptorExterns`)
  — they are the bootstrap that makes enumeration reachable, and (until the
  self-listing below) were not in any table.
- The 2 universal trampolines stay hand-bound — table-driving them would pull the
  whole `pkg/binate/vm` API in as externs.

**Related, also landed 2026-06-12 (binate `53ea3875`): `_Package` self-listing.**
Every package now self-lists its own compiler-synthesized `_Package` accessor as
the LAST entry of its `_Package().Functions` table (closing the reflection gap
where the accessor was absent from its own table), and `--pkg` compilation
force-loads reflect (`ensureReflectLoaded`) so this holds for packages that don't
import reflect. The fv (`func() @reflect.Package`) is stashed on
`ir.Module.PackageAccessorSig` so LLVM + native emit a byte-identical entry. With
this, Part A's enumeration of `rt._Package().Functions` also re-registers
`rt._Package` from the table (idempotent over the hand-bound bootstrap binding).

## Goal

Replace the bytecode VM's **hardcoded** extern table (each of the 4 builtins'
functions hand-registered in `pkg/binate/vm/extern_register_std.bn` via
`vmInst.RegisterExtern(...)`) with **package-level injection**: the VM
enumerates a package's `_Package().Functions` (the FunctionInfo table B0 now
emits) and registers every exported function automatically — for ANY package
(builtin, stdlib, user), not just the 4 hardcoded builtins.

## Current state (what B0 step 3 already landed, all on main as of 0458f71a)

- Every package emits `_Package() @reflect.Package`, whose `.Functions` is a
  `*[]@FunctionInfo` table — one entry per `.bni`-exported func with
  `{Pkg @Package, Name *[]readonly char (fully-qualified "pkg/path.Func"),
  Value *uint8 (= &@__handle.<mangled>, a 16-byte {vtable,data} block),
  ResultSize int, ParamSlots int, Sig *[]readonly char}`.
- Emitted on the **LLVM** backend (`codegen/emit_pkg_functions.bn` +
  `emit_pkg_descriptor.bn`) and **native** (`native/common/common_pkg_functions.bn`
  + `common_pkg_descriptor.bn` + per-arch `*_pkg_descriptor.bn`).
- **NOT emitted on the VM (bytecode) backend** — this is Gap 2.

## The binding path (verified — `D1` of the phase-B plan)

`vmInst.RegisterExtern(name, fvAddr, resultSize, rawFnAddr)`:
- `name` → `FunctionInfo.Name` (the fully-qualified form `LookupExtern` keys on;
  `RegisterExtern` deep-copies it).
- `fvAddr` AND `rawFnAddr` both derive from `FunctionInfo.Value` (they point into
  the SAME 16-byte `@__handle.<mangled>` {vtable, data} block).
- `resultSize` → `FunctionInfo.ResultSize`. `dispatchExternBinding`
  (`vm/vm_extern.bn`) selects scalar(<=8) vs aggregate-retbuf(>8) on ResultSize —
  load-bearing.

## The two sub-problems

### 2a. VM must EMIT `_Package()` + the descriptor as BYTECODE per package
Today `_Package()` is emitted only as native/LLVM; the VM reaches `_Package`
ONLY for the four builtins via the HARDCODED externs in
`extern_register_std.bn`. A user/stdlib package compiled to bytecode has no
native `_Package` symbol → `vm: extern not found: <pkg>._Package`. (Pinned by
`conformance/708`/`709`'s VM xfails — "Gap 2".)

**Fix:** emit, per package, a BYTECODE `_Package()` accessor + the static-managed
`reflect.Package` node + the `FunctionInfo` table — the VM equivalent of
`emit_pkg_descriptor.bn`/`common_pkg_descriptor.bn`. The VM then runs `_Package`
directly. Mirror the native byte layout (the descriptor is an in-memory managed
object; the VM allocates/initializes it as immortal static-managed, negative
`rt.STATIC_REFCOUNT`). Look at how the VM emits other static/managed data + how
`cmd/bni`/`pkg/vm` build module-level globals.

### 2b. Inject: drop the hardcoded extern table, enumerate `_Package().Functions`
Once `_Package().Functions` is available in the VM, replace
`RegisterStandardExterns` (hand-registers each builtin func) with a loop:
for each package to inject, call `<pkg>._Package()`, iterate `.Functions`, and
`vmInst.RegisterExtern(fi.Name, <fvAddr from fi.Value>, fi.ResultSize,
<rawFnAddr from fi.Value>)`. Then delete the hardcoded per-function table.

## Key files
- `pkg/binate/vm/extern_register_std.bn` — the hardcoded extern table (the thing
  to replace). `scalarResult` helper + the 4 builtins (rt, bootstrap, reflect, +
  trampolines).
- `pkg/binate/vm/vm_extern.bn` — `RegisterExtern` / `LookupExtern` /
  `dispatchExternBinding` (the scalar/aggregate selector on ResultSize).
- `pkg/binate/vm/vm_exec_funcref.bn` — `rt._call_shim_scalar`(fn, data, a0..a6) /
  `_call_shim_aggregate`(fn, retbuf, data, a0..a6). **VM dispatch caps at 7 user
  words** (a0..a6); a func with >7 effective words can't be VM-dispatched until
  these helpers are widened (separate sub-task — note it, don't silently cap).
- `cmd/bni` / `pkg/vm` — the VM driver + bytecode emission (where 2a's
  `_Package` emission must hook in).
- `ifaces/core/pkg/builtins/reflect.bni` — the `Package`/`FunctionInfo` ABI (do
  NOT change without owner sign-off — `@Package` is deliberate per immortal
  refcounts; see the dtor-handle history).

## First steps (post-compaction)
1. Read `extern_register_std.bn` + `vm_extern.bn` + `vm_exec_funcref.bn` to map
   the exact `RegisterExtern` signature and the current hardcoded registrations.
2. Read how the VM emits `_Package`/descriptors today (grep `_Package`,
   `RegisterStandardExterns`, `_pkg_info` in `pkg/binate/vm/`).
3. Decide 2a's bytecode descriptor representation (how the VM holds a static-
   managed `reflect.Package` + the FunctionInfo table in-memory).
4. Scope with the owner before building — this is a MAJOR VM-backend change the
   owner previously deferred and is now restarting; confirm approach + sequencing.

## Constraint reminders
- Conformance/708/709/725/727 currently **xfail the 3 VM modes** for Gap 2.
  When 2a+2b land, those xfails should flip to PASS (un-xfail them) — that is the
  acceptance signal.
- BUILDER-compilability: `pkg/binate/vm` is NOT in cmd/bnc's tree (it's built by
  bnc), so full language is allowed there — but `cmd/bni`'s own tree has its own
  constraints; check before adding deps.
