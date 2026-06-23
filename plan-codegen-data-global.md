# `ir.DataGlobal` unification (todo #119)

Unify module-level **static data** (string constants, package globals, impl /
func-value vtables, the per-package `_Package` reflect descriptor) onto one
backend-neutral IR representation — `ir.DataGlobal` — that the LLVM and native
backends each lower through **one `emitDataGlobal`**, so byte layout is
described ONCE and cannot drift between backends.

## Data model (landed)

`ir.DataGlobal { Name; Linkage (DG_WEAK|DG_LOCAL|DG_GLOBAL); Align; ReadOnly;
Init: []DataTerm }`, where `DataTerm = DT_BYTES | DT_INT(width) |
DT_SYMREF(sym,+addend) | DT_ZERO` (an empty-`Sym` symref is a null pointer).
The `symref` term — a pointer to another symbol with an addend — is the
expressivity `ir.Global.Init` (an int-only `@Instr`) lacks.

- LLVM `codegen/emit_data_global.bn`: anonymous-struct constant/global; +addend
  symref = `getelementptr inbounds` const, null symref = `ptr null`.
- Native `native/common/common_data_global.bn` (shared by both arches): per
  term a sized int / raw bytes / an `AddFixup` relocation / zeros, then align.
  **All blobs go in `"data"`** (native's current convention); `ReadOnly` is
  acted on only by LLVM (`constant`) — a native rodata-section split is a
  deferred refinement (needs object-emitter support for relocations in a
  read-only section; the strings phase can introduce + verify it).

## Migration order + status

1. **✅ DONE & LANDED — foundation + `_Package` descriptor NODE+name** (binate
   `1ae1b52b`, 2026-06-21). `ir.DataGlobal` + `emitDataGlobal` (both backends) +
   `ir.BuildPackageDescriptor` (the descriptor node + name-bytes layout, one
   source of truth); both backends' descriptor emitters call it instead of
   hand-rolling the node. The node's flattened struct stays layout-identical
   (the accessor's `i32 0, i32 2` GEP still lands on the payload at byte 16).
   The unification also made the native node/name **weak_odr/local** (matching
   LLVM), **closing the low-priority duplicate-strong-symbol hardening item**
   below. Verified: ir/codegen/native units, gen1, hygiene 15/15, reflect
   conformance (525/532/708/709/725/727), full builder-comp (2048/0),
   native-aa64 (2042/0), gen2 (2048/0). (native-x64 + arm32 are CI-verified:
   the native descriptor code is arch-agnostic, arm32 is the builder-comp LLVM
   path.)

2. **✅ DONE & LANDED — `_Package` info-node tables + backing arrays** (binate
   `b2667902`, 2026-06-22). One shared builder `ir.BuildPackageDescriptors`
   lays the FunctionInfo/GlobalInfo/VtableInfo nodes + their name/sig rodata +
   the `_pkg_funcs/globals/vtables` backing arrays (per-kind builders in
   `ir/data_pkg_{funcs,globals,vtables,descriptor}.bn`); both backends gather
   row metadata + lower via `emitDataGlobal`. Deleted `emit_pkg_{functions,
   globals,vtables}.bn`, `common_pkg_{functions,globals,vtables}.bn`,
   `EmitPackageDescriptorData`, and `emit_static_managed.bn`'s
   `emitStaticManagedGlobal` — the interim native `_Package` emitter
   (`f7d116f3`) is fully retired (net −529 lines). The info-node PAYLOAD
   pointer (FunctionInfo.Pkg + every backing-array entry) carries addend
   `2*IntSize` (the managed-header size), not a hardcoded 16, so it is correct
   on ILP32 (the old LLVM typed field-2 GEP was target-portable; the old native
   hardcoded 16). Native GlobalInfo/VtableInfo nodes + all arrays unified to
   weak/local linkage (matching LLVM). Reflect output byte-identical to before
   across both backends. Verified: gen1+gen2 build, ir/codegen/native units,
   hygiene 15/15, full builder-comp conformance (2219/0), full native-aa64
   (2212/0 after re-running 2 load-flakes), reflect 525/532/708/709/725/727/726
   green both backends; adversarial review clean (no critical/major).
   **Concurrency note:** while in flight, `043318b1` independently refreshed the
   stale 725/727 `ResultSize` goldens (to the `AggregateReturnSize` semantics)
   and `d47d1a2e` renamed the misleading `ResultSize` → `RetbufSize`; Inc 2 was
   rebased onto both (adopting `RetbufSize` throughout) and the planned
   golden-fix commit was dropped as redundant.

3. **Vtables** — split into 3a (func-value) + 3b (impl):
   - **✅ 3a DONE & LANDED — func-value vtables + handles** (binate `30aca2d7`,
     2026-06-22). `@__vt` (vtable `{ dtor, call }`) + `@__handle`
     (function-value `{ vtable, data=null }`) now route through one shared
     `ir.BuildFuncValue` (`ir/data_funcval.bn`), lowered by both backends via
     `emitDataGlobal`.  Each backend's gather still resolves its own symbol
     names + call target (shim, or the function itself for a universal
     trampoline on LLVM) + closure-dtor handle, and keeps the SetGlobal
     bookkeeping on the referenced shim / dtor-handle; only the byte layout
     moved.  LLVM globals became anonymous `{ ptr, ptr }` (was named
     %BnVtable / %BnFuncValue) — the typed-pointer references elsewhere
     auto-upgrade to `ptr` under opaque pointers (clang-verified in review).
     The LLVM closure-dtor triple (`emitClosureDtorTriple`) was also routed
     through `ir.BuildFuncValue` so no hand-rolled func-value emitter remains.
     Deleted the per-backend vtable/handle emitters (codegen
     emitFuncValueVtable/Dtor/Handle, native emitFuncValueVtableDtorSlot{,_x64}
     + dead emitQuadLabelFV).  Verified: full builder-comp 2300/0 + native-aa64
     2296/0, func-value/closure/handle conformance both backends, adversarial
     review clean.
   - **✅ 3b DONE & LANDED — impl vtables** (`@__ivt.*` + `@__ivtshim.*`)
     (binate `787ed644`, 2026-06-22).  The variable-length, recursively-computed
     layout (per iface level: dtor HANDLE slot, then each parent's FULL
     sub-vtable INLINE so `*Child→*Parent` upcast is a fixed offset, then own
     methods; raw `@__ivt` uses fn symbols, shim `@__ivtshim` uses
     `@__handle.<m>`) now routes through one shared `ir.BuildImplVtable`
     (`ir/data_impl_vtable.bn`).  Each backend's gather only collects the ordered
     slot symbols (`collectImplVtableSlots` / `…_x64` / `…Native`, recursive) and
     keeps the SetGlobal bookkeeping on referenced method/dtor-handle symbols;
     only the byte layout moved.  LLVM globals became anonymous
     `{ ptr, ptr, … }` (was `[N x i8*]`); dtor slot became `ptr @__handle…` (was
     `i8* bitcast (%BnFuncValue* … to i8*)`) — typed-pointer refs elsewhere
     auto-upgrade under opaque pointers.  Native impl/shim vtables unified from
     strong `SetGlobal` to **weak** (`DG_WEAK`) to match LLVM (the Inc 1/2
     hardening, user-approved).  Deleted the per-backend impl-vtable emitters
     (codegen emitImplVtable/emitImplShimVtable/emitImplVtableLayout/…Slot +
     dead writeFuncPtrType/writeFuncResultLLVM; native
     emitOneImpl{,Shim}Vtable bodies + dead emitQuad{Label,Zero}{,Iface}).
     Verified: full builder-comp 2359/0 + native-aa64 2356/0, gen1+gen2 green,
     units ir/codegen/native 7/0, hygiene 15/15, adversarial review clean.

4. **🔜 Strings (IN PROGRESS)** — string constants + the `OP_RODATA_*`
   composite-literal blobs.  Each distinct byte sequence is one `StringConst`
   (deduped by `strEq` in `CollectStrings`, UPSTREAM of emission), emitted as a
   byte blob + a `%BnManagedSlice` `.ms` header.  **Strings are NOT byte-symmetric
   across backends today** (unlike the vtables) — the recon (2026-06-22) found:
   LLVM emits `@.str.<id>` = bare `[N x i8]` (exactly N bytes, `private
   unnamed_addr constant`, align 1) **plus** a static `@.str.<id>.ms`
   `%BnManagedSlice` header; native emits `Lstr_<id>` + `EmitAsciz`
   (**NUL-terminated**, N+1 bytes) in the **`text`** section and builds the 4-word
   header **on the stack at each use-site** (`emitRodataSliceHeader`).
   **User-ratified decisions (2026-06-22):**
   - **Drop the native NUL** — not load-bearing (length carried explicitly; no
     consumer scans for it; LLVM has always been NUL-free).  Blob unifies on
     exactly N bytes via `DataBytes`.
   - **Honor `ReadOnly` → rodata on native.**  `common.EmitDataGlobal` currently
     hardcodes the writable `data` section and IGNORES `ReadOnly`, so EVERY blob
     already migrated (descriptor, info-nodes, func-value + impl vtables — all
     `ReadOnly=true`) sits in `data` today.  Teaching it to honor `ReadOnly` moves
     all of them PLUS strings to a real rodata section (ELF `.rodata` SHF_ALLOC;
     Mach-O `__TEXT,__const`).  This is the FIRST read-only-section relocation
     (vtables + the `.ms` header hold symrefs FROM rodata) — so it gets its own
     gated increment verifying relocations on ELF + Mach-O + arm32 before any
     string lands on it.  aarch64 drops its post-table `Align(4)` (only needed
     because code followed in `text`).
   - **Bare-array form + `unnamed_addr`.**  `emitDataGlobal` currently struct-wraps
     every blob (`{ [N x i8] }`); a single-`DT_BYTES` DataGlobal emits a bare
     top-level `[N x i8]`, and a new `UnnamedAddr` field preserves the blob's
     `unnamed_addr` — so LLVM string text stays byte-identical.
   - **Unify the `.ms` header as a static DataGlobal on BOTH backends.**  Native
     gains the static `.ms` global; `OP_RODATA_MSLICE`/`OP_RODATA_SLICE` use-sites
     LEA/ADRP it instead of writing 4 stack words.  One header layout in one
     `ir.BuildStringMSHeader` (ILP32-correct: `DataInt(IntSize, …)` + pointer-sized
     symrefs).
   **Increment breakdown (each independently landable + green):**
   - **4a — LLVM primitive:** bare-array form for a single `DT_BYTES` +
     `UnnamedAddr` field honored by `emitDataGlobal`.  No callers; proven by
     `emit_data_global` unit tests.
   - **4b — native primitive (gated):** `EmitDataGlobal` honors `ReadOnly` →
     rodata; descriptor + all vtables relocate `data`→rodata.  Verify
     read-only-section relocations via full native conformance (ELF + Mach-O +
     arm32).  Byte-layout-identical; only the section changes.
   - **4c — byte blob (both backends):** `ir.BuildStringBlob`; route both
     `emitStringGlobal` (blob half) and both `emitStringTable`s through it.  Drops
     the NUL, lands native strings in rodata, aarch64 drops `Align(4)`.
     Byte-identical LLVM blob; fixes the stale `aarch64_dispatch.bn` `__cstring`
     comment.
   - **4d — `.ms` header unify (both backends):** `ir.BuildStringMSHeader`; LLVM
     `.ms` routes through it (becomes anonymous `{ptr,iN,ptr,iN}`,
     opaque-pointer-equivalent — typed `load %BnManagedSlice` consumers still
     resolve); native emits the static `.ms` and rewrites both arches'
     `emitRodataSliceHeader` to reference it.
   **Dedup preserved for free** (emission only iterates `m.Strings`).  The **VM**
   re-derives strings into heap allocations and never touches `DataGlobal` —
   untouched.
   **Open follow-up (NOT in this phase, flagged for a separate decision):**
   `OP_RODATA_ARRAY` / `OP_RODATA_MSLICE_COPY` bake their bytes inline yet are
   still collected onto `m.Strings`, emitting an UNUSED blob (+ unused `.ms`) each
   — a pre-existing inefficiency to clean up separately (it perturbs IDs / golden
   output, so not bundled into the layout migration).

5. **⬜ Globals** — `mod.Globals`, last (front-end-coupled: extern vars,
   qualified-name resolution, `IsExtern` external-decl emission map *onto*
   `DataGlobal`, not replaced by it).

Each step keeps all backends green.

## Resolved by Phase 1
- **Native strong-symbol hardening** (was: native `SetGlobal`s `_pkg_info` +
  `_pkgname` strong, vs LLVM weak_odr/private): the `DataGlobal` linkage field
  now drives both backends → native node `weak_odr`, name `local`. Closed.

## Adversarial review (binate `0b365dd8`, follow-up to Phase 1)
Verdict SOUND (byte-correct host + ILP32, no active miscompile). Fixed:
- **Native `EmitDataGlobal` now aligns the blob's OWN start** (before
  `DefineLabel`), not only what follows — it previously relied on every prior
  emitter leaving the section aligned, diverging from LLVM's `align N`. Safe for
  the descriptor today, but would misalign a word-bearing blob after an
  odd-length string/vtable in a later phase. Descriptor stays byte-identical.
- Documented the `PointerSize == IntSize` assumption in `BuildPackageDescriptor`
  (slice data field pointer-sized, len field IntSize-sized).
- Added the missing coverage: native start-alignment, null-symref
  (`ptr null` / zero word, no reloc) on both backends, DG_GLOBAL binding.
