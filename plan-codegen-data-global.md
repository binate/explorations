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

2. **⬜ Inc 2 — `_Package` info-node tables + backing arrays.** Migrate the
   FunctionInfo/GlobalInfo/VtableInfo nodes + the `_pkg_funcs/globals/vtables`
   arrays onto `DataGlobal`; then **delete** `emit_pkg_{functions,globals,
   vtables}.bn`, `common_pkg_{functions,globals,vtables}.bn`, and the descriptor
   remnants (`emit_static_managed.bn`'s node wrapper, the thin
   `EmitPackageDescriptorData`/`emit_pkg_descriptor.bn` wrappers) — fully
   retiring the interim native `_Package` emitter (binate `f7d116f3`). The
   info-node Pkg back-pointer GEP (currently nested-type via
   `staticManagedPayloadPtr`) folds into a `symref(_pkg_info, +16)`.

3. **⬜ Vtables** — impl vtables (`@__ivt.*`) + func-value vtables (`@__vt.*` /
   handles). Carry per-arch layout + `weak_odr`/`linkonce` linkage. (Func-value
   `__shim`s are CODE → stay in `mod.Funcs`; only the symref *table* is data.)

4. **⬜ Strings** — string constants. **Preserve `FinalizeStrings`
   interning/dedup** (must not regress to one-global-per-occurrence). Natural
   place to introduce the native rodata-section split (with reloc-free string
   blobs, so the object-emitter concern above doesn't apply).

5. **⬜ Globals** — `mod.Globals`, last (front-end-coupled: extern vars,
   qualified-name resolution, `IsExtern` external-decl emission map *onto*
   `DataGlobal`, not replaced by it).

Each step keeps all backends green.

## Resolved by Phase 1
- **Native strong-symbol hardening** (was: native `SetGlobal`s `_pkg_info` +
  `_pkgname` strong, vs LLVM weak_odr/private): the `DataGlobal` linkage field
  now drives both backends → native node `weak_odr`, name `local`. Closed.

## Known minor follow-up
- `emit_data_global` null-symref (`ptr null` / native zeros) is exercised
  end-to-end by the descriptor tests but has no *dedicated* unit case in
  `emit_data_global_test.bn` / `common_data_global_test.bn`; a focused test is
  a nice-to-have when Inc 2 touches these files.
