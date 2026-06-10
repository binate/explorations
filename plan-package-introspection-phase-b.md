# Plan: Package introspection — Phase B (function-value table + auto-injection)

**Status: NOT STARTED** — design ratified; the owner resolved the key B0 calls on
2026-06-10 (see "Owner decisions" below), so the B0 `FunctionInfo` ABI is locked.
One design question stays open — whether/how an interpreted package gets a real
`_Package()` (see B2). This plan supersedes the Phase-B sections of
[`notes-package-introspection.md`](notes-package-introspection.md) (the design notes)
and refines the `claude-todo.md` entry "Package descriptors (Phase B) — general
Functions-table still future".

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
  every *exported* function (the `.bni` surface, D3b) of each module, so the
  table's entries have a defined handle to point at (B0).
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
  `argSlots(param.Typ)`); it future-proofs the 7-int arg-packing fast path.
  **Ratified included** (owner, 2026-06-10) — all five payload fields land together
  per D1-ABI, so there is no second ABI bump to add it later.
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
later is a lockstep three-site edit + ABI bump. **Ratified (owner, 2026-06-10): land
all five payload fields at once** (`Pkg`, `Name`, `Value`, `ResultSize`, `ParamSlots`,
`Sig`) — no reserve-and-defer, no second bump.

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
pre-pass** with "all `.bni`-exported (see D3b), non-`IsExtern`, in-`mod.Funcs` funcs",
so each `@__handle.<exported fn>` is a defined symbol the local table can symref.

The filter must be `.bni-exported AND non-IsExtern AND in mod.Funcs`. The native
emitters deliberately *skip* cross-module references (`x64_funcvalue.bn:collectFuncValueRefs`,
`emit_funcvals.bn:lookupFuncValueType` exclude `IsExtern` / not-in-`mod.Funcs`)
precisely to avoid duplicate `__vt`/`__handle` symbols breaking Mach-O strict-symbol
linking — the same class as the native-`_Package` link bug fixed in `f7d116f3`.
Getting the force-emit filter wrong re-introduces that bug class. Each local table
references **local** `@__handle` symbols only; cross-package access is a *runtime*
walk via each package's own `_Package()` accessor.

### D3b. The exported surface is the package's `.bni` — NOT capitalization

**Exports in Binate are controlled by the `.bni` interface file, not by identifier
capitalization** (correcting an earlier Go-style assumption that was baked into this
plan). `bootstrap.bni` declares lowercase `formatInt` / `formatInt64` / `formatUint` /
`formatBool` / `formatFloat` — they are *exported* precisely because they appear in the
`.bni`. There is no capitalization-based export check anywhere in the compiler.

The exported set is already available at compile time: the loader parses each package's
own `.bni` into `bniFile` and merges it (`loader.bn:175-300`). But the merge only
*prepends* `.bni` decls for externs/generics — a normally-exported function (declared in
the `.bni` AND implemented in the `.bn`, the common case) survives as its `.bn`
`DECL_FUNC` carrying **no exported marker** (`loader.bn:273-288`, the `hasImpl` skip). So
there is no per-function "exported" signal threaded to codegen today.

Phase B adds one: during the loader merge, record the set of `DECL_FUNC` names from
`bniFile.Decls` and set an `Exported` flag on the matching merged `DECL_FUNC` (carried
onto the lowered `ir.Func`), or attach the exported-name set to the module. The
descriptor/table emission and the D3 force-emit pre-pass both filter on that flag. The
same `.bni`-derived set is what an interpreted package uses for its own table (B2) — the
loader loads `bniFile` for every package, builtin or bytecode.

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

### D5. Builtins and bytecode packages reach the table by different *emission* paths — but an interpreted package should still get a real `_Package` (owner steer)

- **Builtins** (rt, bootstrap, reflect): real native symbol, **no `VMFunc`** (skipped
  from VM lowering, `cmd/bni/main.bn:92,96,196,200`). Reached via `execExtern → vm.Externs`.
  Their table is the *compiled* `__pkg_info` symbol, bound as an extern. **This is B1.**
- **User bytecode packages**: lowered to `VMFunc` (`vm/lower.bn:187-192`), resolvable
  by mangled name (`func_index.bn`/`LookupFunc`), but today have **no native symbol and no
  `_Package` body at all** — `_Package` is backend-only emission (`gen_import.bn:322-343`
  registers it as an imported-extern *declaration*; it is never an `ir.Func`, never
  VM-lowered). So `pkg._Package()` resolves today **only** for the three host-compiled
  builtins.

**Owner steer (2026-06-10): an interpreted package really should get a `_Package` too.**
That makes uniform `pkg._Package()` resolution the *design goal*, not a thing to declare
impossible — but it is a genuine gap to close, because a bytecode package emits no
descriptor. The VM would **synthesize** the descriptor + Functions table at
`vm.LowerModule` time from the package's `.bni`-derived export set (D3b), manufacturing
each entry's function-value via the existing BC_FUNC_VALUE/BC_FUNC_HANDLE Path-B
machinery (`vm_exec_funcref.bn`, `TrampolineScalar`/`Aggregate`). The two paths still
converge at the binding API (`vm.RegisterExtern`, or a sibling trampoline-shaped
registry), but the *semantic* surface (`pkg._Package().Functions`) becomes uniform.
**The "how" is still being designed — see B2; it is the one open call in this plan.**

### D6. The trampolines and the cmd/bni host overrides stay hand-registered

- `registerVmTrampolines` (`TrampolineScalar`/`Aggregate`): `vtable.call` must be the
  **raw** fn addr, not `__shim`, because their `data` IS the closure record
  (`isUniversalTrampoline`, `emit_funcvals.bn:326`). They live in `pkg/binate/vm`,
  not a builtin package's normal exported surface. A generic table sweep must NOT
  absorb them.
- The `cmd/bni` host overrides (`registerPureCExterns`, `progArgsAfterDash` overriding
  `bootstrap.Args`) stay hand-applied, and must run **after** auto-inject so they win
  (re-registration overwrites by name).
- **`bootstrap.format*` are NOT special** — they are declared in `bootstrap.bni` (D3b),
  so they are exported and the table auto-injects them. The earlier "lowercase ⇒
  unexported ⇒ must hand-register" claim was wrong; there is no residual format-helper
  hand-list.

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

**Ratified (owner, 2026-06-10): per-backend-now, behind the shared seam.** The owner
explicitly noted this *increases tech debt* (three hand-maintained layout copies until
the `ir.DataGlobal` migration pays it down — see Risk 6). That debt is accepted to avoid
gating the interop payoff on the unstarted DataGlobal refactor.

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
3. **Thread the `.bni`-exported set (D3b), NOT a capitalization check.** Exports are the
   `.bni` surface. In the loader merge (`loader.bn:256-300`) capture the `DECL_FUNC` names
   from `bniFile.Decls` and set an `Exported` flag on the matching merged `DECL_FUNC`
   (carried onto the lowered `ir.Func`), or attach the exported-name set to the module.
   The emission filter and the D3 force-emit pre-pass read that flag. (There is **no**
   capitalization-based export check to reuse — `scan.bn:209` is the `isLetter` char
   classifier, unrelated.)
4. **Force-emit the func-value triple for every exported func (D3).** Extend the
   `seen`/`sigs` pre-pass in `emit_funcvals.bn:emitFuncValueVtables` (template:
   `addImplDtorsToSeen`, `:256`) and the native mirrors
   (`native/x64/x64_funcvalue.bn:collectFuncValueRefs` + aarch64 sibling) to add every
   `.bni-exported (D3b) AND non-IsExtern AND in-mod.Funcs` function. **Must land in all
   three backends in lockstep** or `@__handle` references dangle on the un-updated backend.
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
   Params/Results), reading the `FuncSig`/`ir.Func` the compiler already holds. In-scope
   for B0 (owner ratified including `Sig` now — D1-ABI); the VM never parses it, so the
   format only has to be deterministic and stable.

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
- **Keep hand-registered (D6):** `registerVmTrampolines` and the
  `cmd/bni/externs.bn:registerPureCExterns` host overrides (`progArgsAfterDash`
  overriding `bootstrap.Args`) — which must run **after** auto-inject (re-registration
  overwrites by name, so ordering auto-first/override-second is the only requirement).
  `bootstrap.format*` are auto-injected (they are `.bni`-exported, D3b) — no residual
  hand-list.

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

### B2 — An interpreted package gets a real `_Package` + Functions table (OPEN design)

A **different emission mechanism** (D5), not an extension of B1. **This is the one slice
whose design the owner wants to think through further (2026-06-10)** — the steer is that
an interpreted package should get a real `_Package()`, so the sketch below is a direction,
not a ratified design.

**Why B2 matters beyond this injection use-case (owner, 2026-06-10):** function-injection
is only the *first* consumer that surfaces the gap. A VM-resolvable, real per-package
`_Package` is the substrate the broader reflection roadmap rides on — Phase A identity
RTTI and Phase C type metadata (`notes-package-introspection.md`) both hang off the
per-package descriptor, and runtime **type assertions** need that descriptor to resolve in
VM mode. So B2 will eventually be needed for those even where whole-package injection is
not the driver; its priority is not governed by B1's use-case alone.

Sketch / things to settle:
- The export set comes from the package's `.bni` (D3b) — the loader already loads
  `bniFile` for bytecode packages, so the exported-func names are available; enumerate
  from that set, **not** from a capitalization or `Test*`-style name walk.
- For each exported func, manufacture a function-value via the existing
  BC_FUNC_VALUE/BC_FUNC_HANDLE Path-B machinery (`vm_exec_funcref.bn`):
  `@VMFuncHandle`/`@VMFuncVtable` with `Call=TrampolineScalar/Aggregate`,
  `data=VMClosureRec`.
- The VM **synthesizes** the descriptor + table at `vm.LowerModule` time
  (`vm/lower.bn:152`) — a bytecode package emits neither today. Settle whether
  `pkg._Package()` in bytecode mode dispatches to this VM-built descriptor (the
  uniformity goal) and how the static-managed sentinel is modeled when the node lives in
  VM heap rather than a data section.
- **Open:** do bytecode entries reuse `vm.Externs` (native-symbol/shim-shaped) or a
  sibling registry keyed by mangled qualified name (trampoline-shaped)? See "Still open".

Tests: a conformance/unit test that defines an exported func in a user package, then reads
`pkg._Package().Functions` and binds + calls an entry by name through the VM-built table.

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

### Owner decisions (2026-06-10)

Resolved; folded into the ratified decisions above.

1. **Include `ParamSlots` and `Sig` now** — land all five `FunctionInfo` payload fields
   at once (no reserve-and-defer). (D1, D1-ABI.)
2. **`bootstrap.format*` are exported** (they are in `bootstrap.bni`) — they auto-inject;
   there is no "unexported helper" special case. The whole export model is `.bni`-driven,
   not capitalization. (D3b, D6.)
3. **An interpreted package should get a real `_Package` too** — uniform `pkg._Package()`
   is the goal. The *how* still needs design → B2 is left OPEN.
4. **`ir.DataGlobal` ordering: per-backend-now** behind the shared seam; DataGlobal
   absorbs it later. Accepted as added tech debt. (D7.)

### Still open

1. **B2 design — how an interpreted package gets its `_Package`** (decision #3 above). The
   sub-questions: a VM-synthesized descriptor at `LowerModule` time; how the static-managed
   sentinel is modeled for a VM-heap node; and whether bytecode entries reuse `vm.Externs`
   or a sibling trampoline-shaped registry keyed by mangled qualified name. The owner wants
   to think this through before B2 starts; B0/B1 do not depend on it.
2. **Whole-program registry (B3) vs imported-packages-only.** Full `AllPackages` /
   `PackageByName` via the dispatcher fully replaces `RegisterStandardExterns`; the
   imported-only cut (no registry, caller walks its own imports) is cheaper but only
   reaches directly-imported packages. B1 already delivers the builtins; B3 is needed only
   if transitive/dynamic discovery is wanted. The owner sets the scope.
3. **Generics** (notes Q7). An exported generic is a family, not one symbol, and is stashed
   as AST (`gen_import.bn:262:genericDecls`), not a single `FuncSig`. The table covers only
   non-generic exported funcs unless specializations are enumerated. Out of scope for
   B0-B1; the export filter must explicitly skip generics rather than crash on them.

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
