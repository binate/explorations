# Plan: Package introspection — Phase B (function-value table + auto-injection)

**Status: NOT STARTED** — design ratified, awaiting the owner's-call items in
"Open questions" below before slice B0 starts. This plan supersedes the Phase-B
sections of [`notes-package-introspection.md`](notes-package-introspection.md)
(the design notes) and refines the `claude-todo.md` entry "Package descriptors
(Phase B) — general Functions-table still future".

## Why

The motivating goal is **automatic whole-package interop**: replace the
hand-maintained extern table with auto-injection driven by per-package metadata.

Today, every function the VM calls into compiled code must be registered by hand.
`pkg/binate/vm/extern_register_std.bn:RegisterStandardExterns` calls four helpers
— `registerRtExterns` (~18 `rt.*` entries), `registerBootstrapExterns` (~13
entries), `registerVmTrampolines`, and `registerPackageDescriptorExterns` — each
of which is a stanza per function:

```
var fp *func(SIG) RET = pkg.Func
vmInst.RegisterExtern("pkg/path.Func", bit_cast(*uint8, &fp), resultSize,
                      bit_cast(int, _raw_func_addr(pkg.Func)))
```

Adding a new exported builtin means hand-editing one of these lists. This is the
boilerplate Phase B exists to delete. The replacement: each compiled package
already emits a `reflect.Package` descriptor; Phase B grows it with a
**`Functions` table** — one `(name, function-value, result-size)` entry per
exported function — and the VM **auto-enumerates** each package's table and binds
names → function-values, calling `RegisterExtern` from the table instead of from
a hand list.

Per [`notes-package-introspection.md`](notes-package-introspection.md) line 34-40,
this VM→compiled-code binding — *not* general user-facing reflection — is the
critical reflection consumer in Binate. RTTI / structured type metadata (Phase A
/ Phase C) is independent and comes later; this plan does not depend on it.

## What is LANDED vs what Phase B ADDS

**LANDED (the name-only descriptor scaffolding, binate `feadde2c` + `f7d116f3`):**

- `ifaces/core/pkg/builtins/reflect.bni` — `type Package struct { Name *[]readonly char }`.
  **One field.** No `Functions`, no `FunctionInfo`.
- Every compiled module emits, keyed off its package path, three symbols:
  - name rodata: `mangle.GlobalName(pkg, "_pkgname")` → `bn_<pkg>___pkgname`
  - descriptor node: `mangle.GlobalName(pkg, "_pkg_info")` → `bn_<pkg>___pkg_info`
  - accessor: `mangle.FuncName(pkg, "_Package")` → `bn_<pkg>___Package`
- The node is an **immortal static-managed** `reflect.Package` riding the
  `rt.STATIC_REFCOUNT` sentinel (see
  [`plan-static-managed-sentinel.md`](plan-static-managed-sentinel.md)): a 2-word
  header `{Refcount=STATIC sentinel, FreeFn=0}` laid immediately in front of the
  payload `{Name.data → _pkgname, Name.len = N}`. RefInc/RefDec on the returned
  `@reflect.Package` are no-ops.
- LLVM emitter: `codegen/emit_pkg_descriptor.bn:emitPackageDescriptor` (called
  unconditionally from `emit.bn:EmitModule` at `:343`). Hardcodes the payload
  LLVM type `{ ptr, <int> }`.
- Native emitter: shared data half `native/common/common_pkg_descriptor.bn:EmitPackageDescriptorData`
  (hardcoded 64-bit `EmitUint64`); per-arch accessors
  `native/{aarch64,x64}/*_pkg_descriptor.bn` (called from `aarch64.bn:54` /
  `x64.bn:62`).
- Type-check / IR wiring: `types/check_expr_access.bn:packageAccessorType`
  synthesizes `func() @reflect.Package` at the `pkg._Package` selector;
  `ir/gen_import.bn:322-343` registers `_Package` as an imported extern;
  `gen_import.bn:181:qualifiedReflectPackageType` rebuilds the struct with the
  path-dotted name so the mangler folds to the defining package's symbol.
- VM precursor: `vm/extern_register_std.bn:registerPackageDescriptorExterns`
  hand-binds the builtin packages' `_Package` accessors as externs.
- `conformance/532_reflect_package_accessor` (`rt._Package().Name` →
  `"pkg/builtins/rt"`) green in all 6 default modes.

**Phase B ADDS:**

- A `Functions` field on `reflect.Package` + a `FunctionInfo` type (B0).
- Force-emission of the func-value triple (`@__shim`/`@__vt`/`@__handle`) for
  every *exported* function of each module, so the table's entries have a defined
  handle to point at (B0).
- Static emission of the per-package `Functions` table (B0).
- A VM auto-enumerator that walks the builtin packages' tables and replaces the
  `registerRtExterns` / `registerBootstrapExterns` /
  `registerPackageDescriptorExterns` hand lists (B1).
- (Later slices) a Functions table for VM-lowered user packages (B2), and a
  cross-package registry / `PackageByName` (B3).

## Ratified decisions

These are settled and slice work should assume them. The genuinely-open calls are
in "Open questions / owner's calls".

### D1. `FunctionInfo` carries a precomputed `ResultSize` — NOT structured type info

`FunctionInfo` carries the **already-computed VM-dispatch metadata** plus an
opaque mangled signature string, and references **no** `TypeInfo`:

```
package "pkg/builtins/reflect"

type Package struct {
    Name      *[]readonly char
    Functions *[]@FunctionInfo
}

type FunctionInfo struct {
    Pkg        @Package          // back-pointer (matches the notes sketch; the VM ignores it)
    Name       *[]readonly char  // FULLY-QUALIFIED, e.g. "pkg/builtins/rt.Alloc"
    Value      *uint8            // &@__handle.<mangled> — a 16-byte {vtable,data} block, NOT a raw fn ptr
    ResultSize int               // VM scalar(<=8) / aggregate(>8) dispatch discriminator
    ParamSlots int               // packed int-slot count of params (0..7 fast path); see D1 note
    Sig        *[]readonly char  // mangled signature string, opaque; equality/debug/Phase-C hook
}
```

Rationale (verified against the binding path):

- The motivating call is `vmInst.RegisterExtern(name, fvAddr, resultSize, rawFnAddr)`.
  `name` → `FunctionInfo.Name` (`RegisterExtern` deep-copies it, `vm.bn:313-316`;
  it must be the fully-qualified `"pkg/path.Func"` form `LookupExtern` keys on).
  `fvAddr` **and** `rawFnAddr` both derive from `FunctionInfo.Value`: the
  funcvalue-emission analysis confirms they point into *the same* 16-byte
  `{vtable,data}` block, `@__handle.<mangled>`. `resultSize` → `FunctionInfo.ResultSize`.
- **`ResultSize` is load-bearing and is the field the original notes sketch
  (`{Pkg, Name, Signature, Value}`) OMITS.** `dispatchExternBinding`
  (`vm_extern.bn:25`) selects scalar (`<=8`) vs aggregate-retbuf (`>8`) dispatch
  **purely** on `ResultSize`. The three aggregate-return externs — `rt.MakeManagedSlice`,
  `bootstrap.Args`, `bootstrap.ReadDir`, all hand-tagged `32` today — would be
  dispatched as scalars (garbage X0 instead of a retbuf, a silent miscompile of
  the return) if the table omits it. **Therefore `ResultSize` is in-scope for B0,
  not deferrable** (per CLAUDE.md "raise critical bugs — don't work around them":
  shipping the table without it is exactly such a latent miscompile).
- `ResultSize` is **emitted into the table**, not re-derived in the VM: it is free
  at codegen time (`lower_func.bn:67-86` already computes it via `types.SizeOf` +
  `isMultiWordField`; `emit_funcvals.bn:isAggregateReturn` encodes the same
  threshold). Re-deriving in the binder would push a `types.SizeOf` into the
  registration loop *and* require shipping structured type info purely to recompute
  a number we already had — the exact Phase-A coupling we want to avoid.
- `ParamSlots` is the same story (`lower_func.bn:49-53` already sums
  `argSlots(param.Typ)`); it future-proofs the 7-int arg-packing fast path. If the
  owner deems it speculative it can be dropped (`ResultSize` alone unblocks every
  current binding) — but see D1-ABI below.
- `Sig` is **opaque** to Phase B (the VM never parses it). It is a single string
  symref (same shape as the existing `_pkgname` rodata) and exists for
  signature-equality, debug, and a Phase-C / `.bni`-replacement hook. There is **no**
  type-to-string serializer anywhere in `types`/`codegen`/`mangle` today (verified:
  mangling is name-only, Binate has no overloading), so `Sig` requires one small new
  serializer over `types.Type` — bounded, backend-independent, and decoupled from
  Phase A.

**D1-ABI:** field order and presence are an ABI commitment the moment B0 ships
(the compiled descriptor is consumed by a separately-built VM — notes Q5). The
layout is hand-encoded in three places (`reflect.bni`, the LLVM literal in
`emit_pkg_descriptor.bn`, the native `common_pkg_descriptor.bn`), so growing it
later is a lockstep three-site edit + ABI bump. **Land all payload fields at once**
rather than growing the struct twice. If `Sig`/`ParamSlots` are deferred, *reserve
their slots now*.

Alternatives rejected for Phase B: (a)-pure mangled-string-only (forces a string
parser + `SizeOf`-equivalent into the registration loop to recover `ResultSize` —
insufficient as-is); (b) structured `*[]@TypeInfo` (couples to Phase A/C
`TypeInfo`, which does not exist — would mean building a chunk of Phase A first).
Structured types are the right shape for **Phase C**, layered on top additively.

### D2. `FunctionInfo.Value` points at `@__handle.<mangled>` — never the raw fn or `@__vt`

`RegisterExtern` reads `fv[0]/fv[1]` from `Value` for `VtableAddr`/`DataAddr`
*and* copies its two words into `HandleAddr` for `BC_FUNC_HANDLE` (`vm.bn:283-299`).
A raw fn pointer would make `OP_CALL_HANDLE` byte-pun machine code as a
`{vtable,data}` struct and crash; `@__vt` is only half (missing the data slot).
`_func_handle(F)` / `OP_FUNC_HANDLE` already yields exactly this address
(`&@__handle.<mangled>`, the static 16-byte `{vtable, data=null}` from
`emit_funcvals.bn:emitFuncValueHandle`). The static-data embedding precedent is
the impl-vtable dtor slot (`emit_impls.bn:260`,
`i8* bitcast(%BnFuncValue* @__handle.<dtor> to i8*)`) — the table uses the same
relocation form.

### D3. Each exported function's triple is emitted exactly once, by its OWNER TU

`emit_funcvals.bn:emitFuncValueVtables` emits the `{shim,vt,handle}` triple only
for funcs referenced by `OP_FUNC_VALUE`/`OP_FUNC_HANDLE` in the module — plus a
pre-pass (`addImplDtorsToSeen`, `:256`) that force-emits triples for `IsLinkOnce`
funcs and impl-receiver-dtors even when unreferenced. Phase B **extends this
pre-pass** with "all exported, non-`IsExtern`, in-`mod.Funcs` funcs", so each
`@__handle.<exported fn>` is a defined symbol the local table can symref.

The filter must be `exported AND non-IsExtern AND in mod.Funcs`. The native
emitters deliberately *skip* cross-module references (`x64_funcvalue.bn:collectFuncValueRefs`,
`emit_funcvals.bn:lookupFuncValueType` exclude `IsExtern` / not-in-`mod.Funcs`)
precisely to avoid duplicate `__vt`/`__handle` symbols breaking Mach-O strict-symbol
linking — the same class as the native-`_Package` link bug fixed in `f7d116f3`.
Getting the force-emit filter wrong re-introduces that bug class. Each local table
references **local** `@__handle` symbols only; cross-package access is a *runtime*
walk via each package's own `_Package()` accessor.

### D4. Registry / cross-package enumeration is the import-graph dispatcher — NOT a linker section

Discovery of "which packages" uses the **import-graph driver**, mirroring the
already-working per-package `__init` dispatcher (`ir/gen_init.bn:EmitInitDispatcher`
/ `EmitMainEntry`): the driver walks `loader.Loader.Order` (the topo-sorted import
graph, `loader.bn:computeOrder/visit`) — which `cmd/bnc` and `cmd/bni` already do
to drive codegen + the init list — and synthesizes explicit register calls.

The linker-section approach (notes Q4's lean) is rejected as not cheaply feasible
in this tree, verified against source:

- **No section plumbing exists**: `codegen/` emits zero `section "..."`, `appending`,
  `@llvm.used`, or `@llvm.global_ctors` (the entry path deliberately routes through
  `bn_entry` via `EmitMainEntry`, not `global_ctors`).
- **The assembler has a hardwired 4-section vocabulary**: `asm.bn:SetSection`
  rejects unknown section names without explicit flags; `defaultFlags` /
  `macho.bn:machoSectName` know only `text`/`rodata`/`data`/`bss`. No arbitrary
  section, no `__start_`/`__stop_` synthesis, no section iteration.
- **Mach-O has no `__start_`/`__stop_`** (an ELF-linker feature); both native
  arches target Mach-O. A section registry would be **format-divergent at runtime**
  (ELF symbols vs Mach-O `getsectiondata`/dyld iteration — C/libdyld interop the
  C-free design avoids).
- It **re-opens the LLVM-only-divergence trap** on a high-stakes artifact, and it
  **still can't reach VM user packages** (bytecode packages produce no object).

The dispatcher emits **ordinary calls and ordinary globals** that both backends
already lower identically — zero new section/object/format plumbing — and extends
naturally to the VM. It also makes opt-in stripping (notes Q1) trivial later (filter
the driver's package list) where a passive section cannot.

### D5. Builtins and VM user packages are different mechanisms that converge only at the binding API

- **Builtins** (rt, bootstrap, reflect): real native symbol, **no `VMFunc`** (skipped
  from VM lowering, `cmd/bni/main.bn:92,96,196,200`). Reached via `execExtern → vm.Externs`.
  Their table is the *compiled* `__pkg_info` symbol, bound as an extern. **This is B1.**
- **User bytecode packages**: lowered to `VMFunc` (`vm/lower.bn:187-192`), resolvable
  by mangled name (`func_index.bn`/`LookupFunc`), but **no native symbol and no
  `_Package` body at all** (`_Package` is backend-only emission — `gen_import.bn:322-343`
  registers it as an imported-extern *declaration*; it is never an `ir.Func`, never
  VM-lowered). Their table must be **built at `vm.LowerModule` time** from the lowered
  module, with function-values manufactured by the existing BC_FUNC_VALUE/BC_FUNC_HANDLE
  Path-B machinery (`vm_exec_funcref.bn`, `TrampolineScalar`/`Aggregate`). **This is B2.**

A design that assumes `pkg._Package()` resolves uniformly in the VM is **wrong** —
it only works for the three hand-bound builtins today. The two classes converge only
at `vm.RegisterExtern(name, &value, resultSize, raw)` (or a sibling registry).

### D6. The trampolines and the unexported format helpers stay hand-registered

- `registerVmTrampolines` (`TrampolineScalar`/`Aggregate`): `vtable.call` must be the
  **raw** fn addr, not `__shim`, because their `data` IS the closure record
  (`isUniversalTrampoline`, `emit_funcvals.bn:326`). They live in `pkg/binate/vm`,
  not a builtin package's normal exported surface. A generic table sweep must NOT
  absorb them.
- `bootstrap.formatInt/Int64/Uint/Bool/Float` are **lowercase/unexported** yet
  registered today (load-bearing for every VM `print`). An exported-only table (notes
  Q6 / line 99: "Phase B initially only covers exported functions") would drop them.
  B1 keeps a residual hand-list for them (or the owner widens the predicate — see
  open questions); this is a concrete gap, not hypothetical.

### D7. `ir.DataGlobal` does NOT gate Phase B; Phase B becomes a DataGlobal client later

The `ir.DataGlobal` unification (`claude-todo.md` MAJOR, line 1511-1518) is the right
long-run home for the Functions table (it is exactly the `bytes | int | symref` static
blob that proposal unifies). But it is **filed UNSTARTED** — no `plan-*.md`, no
`ir.DataGlobal` in tree (verified) — and the todo itself calls it "a project, not a bug
fix" with "non-trivial regression surface" over currently-working code. Gating Phase B
on it would stall the actual interop payoff behind an unscoped refactor.

So: Phase B builds the table on the **current** per-backend emitters, behind a thin
**shared layout seam** (one function laying the table given the exported-func list,
called by each backend — exactly as `common.EmitPackageDescriptorData` already bounds
the Name-only descriptor). The `.bni` is the authoritative offset contract. When
DataGlobal lands, its `_Package` migration step (which `claude-todo.md:1517` already
marks as deleting `emit_pkg_descriptor.bn` + `common_pkg_descriptor.bn`) re-expresses
that **one shared layout function** as DataGlobal terms — throwaway-once, bounded by the
seam, not throwaway-twice. Building the table now also gives DataGlobal a *second,
harder* client (N symrefs to handles + N name strings) to validate its `Init`/relocation
model against before the refactor commits — strictly better design input.

## Slices (ordered, each independently landable, each keeps all backends + 6 modes green)

### B0 — Grow `reflect.Package` with `Functions`; emit the table for host-compiled packages, behind a shared seam

This is the load-bearing slice with the real engineering. **No VM behavior changes
yet** — the table is additive static data nothing consumes.

Work:

1. **`ifaces/core/pkg/builtins/reflect.bni`** — add `Functions *[]@FunctionInfo` to
   `Package`; define `FunctionInfo` per D1 (Pkg, Name, Value, ResultSize, ParamSlots,
   Sig). Update the doc-comment (currently "Phase B starts with just the package name…").
2. **Thread the exported-func list into the descriptor emitters.** Today
   `emitPackageDescriptor(out)` (`emit.bn:343`) and `emitPackageDescriptor(a, pkgName)`
   (`x64.bn:62`, `aarch64.bn:54`) take **no** function list (verified). Change all three
   signatures to take the module (or a pre-filtered exported-func list); the list is in
   scope at every call site (`m` / `mod`).
3. **Define `isExported(name)` once.** No central predicate exists (only the
   capitalized-first check at `lexer/scan.bn:209`). Add one helper (capitalized first
   letter, the Go-style convention the codebase already assumes) and use it at the
   emission filter.
4. **Force-emit the func-value triple for every exported func (D3).** Extend the
   `seen`/`sigs` pre-pass in `emit_funcvals.bn:emitFuncValueVtables` (template:
   `addImplDtorsToSeen`, `:256`) and the native mirrors
   (`native/x64/x64_funcvalue.bn:collectFuncValueRefs` + aarch64 sibling) to add every
   `exported AND non-IsExtern AND in-mod.Funcs` function. **Must land in all three
   backends in lockstep** or `@__handle` references dangle on the un-updated backend.
5. **Emit the table as static data behind ONE shared layout function.** Add a shared
   layout function (mirroring `common.EmitPackageDescriptorData`) that, given the
   exported-func list, lays the `FunctionInfo` array + the `Functions` slice header
   (ptr+len) as immortal static-managed data, with symref fixups to `@__handle.<fn>`
   (Value), to per-fn name rodata (Name), and to per-fn `Sig` rodata. Native side
   extends `common_pkg_descriptor.bn` (mind the hardcoded 64-bit `EmitUint64`); LLVM
   side extends `emit_pkg_descriptor.bn`'s hardcoded `{ ptr, <int> }` payload literal +
   init builder. Both share the offset/term-order contract via the `.bni`.
6. **Compute `ResultSize`/`ParamSlots` at emit time** via the same
   `isMultiWordField`/`types.SizeOf`/`argSlots` logic `lower_func.bn:49-86` uses.
7. **Write the `Sig` serializer** (new, small) over `types.Type` (Kind-tagged, walk
   Params/Results), reading the `FuncSig`/`ir.Func` the compiler already holds. (May be
   deferred to a follow-up *iff* the `Sig` slot is reserved per D1-ABI — owner's call.)

Tests:
- Extend `conformance/532_reflect_package_accessor` to read `rt._Package().Functions`
  (assert `len` and a known entry's `Name`, e.g. `"pkg/builtins/rt.Alloc"`, and its
  `ResultSize`).
- Update the pinned LLVM unit tests `codegen/emit_pkg_descriptor_test.bn` — **both the
  host-i64 and the ILP32-i32 variants** (easy to miss the ILP32 one).
- Update the native pinned tests `native/{aarch64,x64}/*_pkg_descriptor_test.bn` (these
  pin the 16-byte accessor shape — the accessor is **unchanged** since it still returns
  the node payload base; only the payload data grows).

Keeps green: the table is additive static data; no consumer reads it yet, so runtime
behavior is identical. The regression net is conformance/532 + the pinned emitter unit
tests across host + ILP32 + both native arches.

Gates: B1.

### B1 — VM auto-enumerates the host packages' tables; replaces the hand lists (THE payoff)

Work:
- In `vm/extern_register_std.bn`, replace `registerRtExterns` /
  `registerBootstrapExterns` / `registerPackageDescriptorExterns` with an enumerator
  that, for each host-compiled builtin package (rt, bootstrap, reflect), calls
  `pkg._Package().Functions` and, per `FunctionInfo`, calls
  `vmInst.RegisterExtern(fi.Name, &fi.Value, fi.ResultSize, bit_cast(int, fi.Value))`.
  `RegisterExtern` (`vm.bn:277`) is **unchanged** — it already derives the 5-field
  `ExternBinding` from exactly `(name, fvAddr, resultSize, rawFnAddr)`, which the entry
  now supplies (D1/D2).
- The enumerator walks the statically-known imported-builtins set (the import-graph
  approach, D4); no new dispatcher is needed yet because these packages are directly
  imported by the host.
- **Keep hand-registered (D6):** `registerVmTrampolines`, the unexported
  `bootstrap.format*` helpers (residual hand-list), and the
  `cmd/bni/externs.bn:registerPureCExterns` host overrides (`progArgsAfterDash`
  overriding `bootstrap.Args`) — which must run **after** auto-inject (re-registration
  overwrites by name, so ordering auto-first/override-second is the only requirement).

Tests: the existing `pkg/binate/vm` unit tests + `conformance/532` are the regression
net; the auto-bound entries produce byte-identical `ExternBinding`s (same name, same
handle, same `ResultSize`) to the hand-list, so VM dispatch is unchanged. Add a unit
test asserting an auto-bound `rt.MakeManagedSlice` (aggregate, ResultSize 32) and a
scalar (e.g. `rt.Alloc`) both dispatch correctly.

Keeps green: dispatch is unchanged by construction; the hand list is replaced by an
equivalent-output enumerator.

Gates: nothing downstream is forced; delivers the motivating payoff for the builtins.

### B1.5 (optional) — Externs scaling

`LookupExtern` is an O(N) linear scan (`vm.bn:335`) over `vm.Externs` (N~30 today),
hit by `execExtern`, the `BC_FUNC_VALUE` fallback, `BC_FUNC_HANDLE`, and `ensureHandle`.
Auto-inject grows N toward hundreds. Migrate `Externs` to the open-addressing hash
already used for `VM.Funcs` (`IndexBuckets`/`IndexMask`, `vm.bni:544`) **only if
profiling shows it matters** — do not pre-optimize. Independently landable, gates nothing.

### B2 — Functions table for VM-lowered user/bytecode packages

A **different mechanism** (D5), not an extension of B1.

Work:
- After `vm.LowerModule(mod)` (`vm/lower.bn:152`), enumerate the module's exported
  funcs from the AST (`pkg.Merged.Decls`, the same `DECL_FUNC`/capitalized-name walk
  `cmd/bni/main.bn:runTests` uses for `Test*` discovery, ~`:249`).
- Per entry, manufacture a function-value via the existing BC_FUNC_VALUE/BC_FUNC_HANDLE
  Path-B machinery (`vm_exec_funcref.bn`): `@VMFuncHandle`/`@VMFuncVtable` with
  `Call=TrampolineScalar/Aggregate`, `data=VMClosureRec`.
- Register name → handle. **Decide whether user entries reuse `vm.Externs` or a sibling
  registry** keyed by mangled qualified name — `Externs` is shaped for
  native-symbol-via-shim bindings whereas user entries dispatch through trampolines, so
  a sibling may be cleaner (see open questions).
- The VM must **synthesize** both the descriptor and the table for bytecode packages
  (they have neither). Whether `pkg._Package()` resolves to a VM-built descriptor in
  bytecode mode is a B2 scope call.

Tests: a conformance/unit test that defines an exported func in a user package and binds
+ calls it by name through the VM-built table.

Keeps green: additive — covers a case B1 structurally cannot.

Gates: B1 (for the name→value registry shape).

### B3 (optional, lowest priority) — Cross-package registry / `PackageByName`

Only needed to reach packages the host doesn't directly import (transitive deps,
dynamic discovery). Use the import-graph dispatcher (D4), **not** a linker section.

Work:
- Add a sibling to `ir/gen_init.bn:EmitInitDispatcher` — e.g.
  `EmitPkgRegistry(packagePkgInfoNames @[]@[]char)` — that synthesizes a
  `<main>.__register_all` calling a tiny `reflect.__register(&__pkg_info.<pkg>)` per
  package, registered as externs exactly as `EmitInitDispatcher` does for `<pkg>.__init`.
  Hook it into `EmitMainEntry` right after `__init_all` (`gen_init.bn:179`).
- `reflect.__register(@Package)` appends to a process-global `reflect.AllPackages`,
  backing `reflect.PackageByName(name)` (answers notes Q4's optional `PackageByName`).
- The driver builds the name list from `ldr.Order` (same loop that feeds
  `packageInitNames`).

Tests: a test that looks up an imported-but-not-directly-bound package via
`reflect.PackageByName` and binds from its table.

Keeps green: the dispatcher is a synthetic function full of ordinary `EmitCall`s +
`declare`s, lowered identically by both backends.

Gates: B2.

## Open questions / owner's calls

These are the genuine decisions the owner owns. None should be silently resolved.

1. **Include `ParamSlots`/`Sig` now, or reserve-and-defer?** (D1-ABI.) Recommendation:
   include all five payload fields at once to avoid a second ABI bump. If `Sig` is
   deferred, the slot **must** be reserved now (three-site lockstep edit + ABI bump to
   add it later). The owner picks include-now vs reserve-and-defer.

2. **Do the unexported, load-bearing `bootstrap.format*` helpers go in the table, or
   stay hand-registered?** (D6.) They are required for VM `print` but are *unexported*,
   so an exported-only table drops them. Cleanest near-term: keep them hand-registered in
   B1 and separately revisit whether they should be exported. The owner decides whether to
   widen the export predicate for `bootstrap` specifically vs keep the residual hand-list.

3. **Does the first slice cover only builtins (recommended), or attempt builtins +
   bytecode user packages together?** (D5.) Recommendation: B1 = builtins only (the actual
   ask, reachable today); B2 = user packages as a separate slice with its own design. The
   alternative (unified up front) blocks the builtins payoff behind B2's VM-side table
   construction and risks baking in the false "`_Package` resolves uniformly" assumption.

4. **`ir.DataGlobal` ordering** (D7; `claude-todo.md:1511-1518`). Recommendation:
   per-backend-now behind the shared seam, DataGlobal absorbs it later. The alternative
   (DataGlobal-first) is cleaner long-run but gates the interop payoff on an unstarted
   IR + dual-backend refactor of currently-working code. This is the owner's
   scope/sequencing call; the slicing in this plan holds under either choice.

5. **Whole-program registry (B3) vs imported-packages-only.** Full `AllPackages` /
   `PackageByName` via the dispatcher fully replaces `RegisterStandardExterns`; the
   imported-only cut (no registry, caller walks its own imports) is cheaper but only
   reaches directly-imported packages. B1 already delivers the builtins; B3 is needed
   only if transitive/dynamic discovery is wanted. The owner sets the scope.

6. **For B2: reuse `vm.Externs` or a sibling registry** keyed by mangled qualified name?
   (`Externs` is native-symbol-shaped; user entries dispatch via trampolines.)

7. **Generics** (notes Q7). An exported generic is a family, not one symbol, and is
   stashed as AST (`gen_import.bn:262:genericDecls`), not a single `FuncSig`. The table
   covers only non-generic exported funcs unless specializations are enumerated. Out of
   scope for B0-B1; flagged so the export filter explicitly skips generics rather than
   crashing on them.

## Risks

1. **Three-backend lockstep.** The proven failure: native `_Package` shipped late →
   MAJOR link bug (`f7d116f3`). B0's force-emit change touches `emit_funcvals.bn` +
   `x64_funcvalue.bn` + `aarch64_funcvalue.bn` and **must land together** or `@__handle`
   references dangle on the un-updated backend. Mitigation: the shared layout seam (D7) +
   the `.bni` as the offset contract bound the data-layout divergence; the force-emit
   filter (D3) is the one piece that must be mirrored per-backend by hand.

2. **CRITICAL — `ResultSize` omission is a silent miscompile.** Without it, aggregate-return
   externs (`rt.MakeManagedSlice`, `bootstrap.Args`, `bootstrap.ReadDir`, all 32) dispatch
   as scalars → garbage X0 instead of a retbuf. In-scope for B0 (D1). This is why the table
   carries the precomputed int rather than re-deriving in the binder.

3. **CRITICAL — `Value` must be a `@__handle`-shaped block (D2).** A raw fn ptr breaks
   `BC_FUNC_HANDLE` + the `BC_FUNC_VALUE` fallback and makes `OP_CALL_HANDLE` byte-pun
   machine code as a struct (crash).

4. **Triplet must be emitted exactly once, by the owner (D3).** The wrong force-emit filter
   re-introduces the duplicate-`__vt`/`__handle` Mach-O strict-symbol breakage the
   cross-module skip exists to prevent.

5. **Trampolines must survive the rewrite (D6).** A generic sweep that takes `@__handle` for
   `TrampolineScalar`/`Aggregate` routes their closure-record `data` through the
   data-stripping `__shim` — broken dispatch. Exclude them.

6. **Three hand-maintained copies of the layout** (LLVM text + native shared fn + `.bni`)
   are a drift magnet; the LLVM payload type/init is a hardcoded literal not derived from the
   reflect type, so the `.bni` and the literal can drift (a field added to the `.bni` but not
   the literal compiles but produces a wrong-layout node — silent miscompile). Bound by the
   shared native function + treating the `.bni` as authoritative; this is the strongest
   argument for landing `ir.DataGlobal` eventually (as a *follow-up* per D7, not a precondition).

7. **Native is hardcoded 64-bit** (`EmitUint64` in `common_pkg_descriptor.bn`) while the LLVM
   side is target-parameterized (`intLL`/`IntSize`). Any int-typed table field (`ResultSize`,
   `ParamSlots`, slice `len`) must respect this on the LLVM side; native is aa64/x64-only so
   the 64-bit assumption is latent today but should not be deepened.

8. **Binary size / dead-stripping.** Force-emitting a triple per exported func keeps the whole
   exported surface alive (defeats the current "unused descriptor strips free" story — notes
   Q1, the Phase-C size concern arriving early). Acceptable for the small host-compiled builtins
   surface; revisit opt-in granularity (trivial to gate via the driver's package list under D4)
   before B2/B3 widen it to user packages.

9. **Native linkage mismatch** (`claude-todo.md:1518`): native `_pkg_info`/`_pkgname` are STRONG
   vs LLVM `weak_odr`/`private`. The Functions table replicates this strong-symbol choice for its
   new data globals — harmless today (disjoint package names per object), a duplicate-strong-symbol
   hazard for a future native-library-packaging path. `ir.DataGlobal`'s linkage field is the
   structural fix; in the interim, match the existing descriptor's linkage choices.

10. **ABI commitment** (D1-ABI, notes Q5). `FunctionInfo` field order is fixed once B0 ships
    (crosses the VM/compiled build boundary). Consider a version/reserved word in the node if cheap.
