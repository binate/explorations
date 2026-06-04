# Plan: Cross-Package Interfaces

**Status:** COMPLETE (shipped). All four slices (2.6 / 2.7 / 2.8 / 2.9)
landed; Phase 2 cross-package interfaces is feature-complete per this
plan. Kept for design rationale. One open follow-up remains (mixed-mode
compiled ↔ VM dispatch — see below).

## Motivation

Interfaces (Phase 2) originally worked only within a single package: an
`impl R : I` declaration had to live in the same package as the
declarations of `R` and `I`, and IR-gen / type-check tables for impls
were per-module. To make interfaces useful for library boundaries
(e.g., a `Stringer` defined in one package, implemented by types in
many others), `*A.Iface` and `@A.Iface` need to be first-class types
across package boundaries, and `impl` resolution needs to span
imports.

## Design points (ratified 2026-05-06)

1. **`*pkg.Iface` / `@pkg.Iface` syntax** is accepted, parsed as a
   qualified interface reference. Same syntax as `*pkg.T` for
   pointers to qualified types. Aliases (`interface Foo = pkg.Bar`)
   resolve transparently — the canonical identity is the alias chain's
   tail.

2. **`impl R : I` may live in any package** that has both `R` and `I`
   in scope (transitively, through imports). Duplicate declarations
   across TUs are allowed: each TU that has the declaration emits a
   `weak_odr` vtable, and the linker collapses identical copies. No
   orphan rule — the duplicate-OK convention handles the case it
   would have prevented, since methods are tied to their type's
   defining package and so cannot diverge across `impl` sites for
   the same `(R, I)` pair.

3. **Vtable symbols are canonical on `(R, I)`, not on the impl's
   package.** Renaming the format from
   `__ivt.bn_<impl_pkg>__<recv>__<iface>` to
   `__ivt.bn_<recv_pkg>__<recv>__<iface_pkg>__<iface>` (with both
   `<iface_pkg>__<iface>` resolved through alias chains) ensures any
   two TUs that reference the same `(R, I)` pair agree on the symbol.
   Same-package impls land on `<recv_pkg> == <iface_pkg>` so the
   package appears twice in the symbol — by design.

4. **Visibility check at iv-construction sites.** Every TU that emits
   `OP_IFACE_VALUE` must have an `impl R : I` visible to the type-
   checker (in itself or transitively imported). If no such impl is
   visible, that's a compile error ("no impl for `*T` satisfying
   `pkg.I` is in scope"). If an impl is visible but no TU in the link
   actually emits the vtable, the linker reports an undefined symbol;
   that's coarser feedback but acceptable while we're still early.

## Implementation notes (divergences and gotchas)

These are the non-obvious points where the shipped implementation
diverged from the original slice plans, or where a subtle constraint
had to be respected.

- **Canonical (Pkg, Name) comparison, not Type identity.** The
  type-checker shares one `Checker` across packages (CheckPackage
  saves/restores scope but reuses `c.Impls`), so cross-package impl
  visibility was already implicit. The real gap was
  `implCoversInterface` matching by bare Name, which false-matched
  same-named interfaces in different packages. Switched to canonical
  (Pkg, Name) comparison; `types.Type` gained a `Pkg` field stamped
  at `TYP_INTERFACE` creation. Pointer-equality via Type identity
  would have been simpler but the bootstrap interp didn't support
  `@T == @T`.

- **.bni interface decls were being dropped.** The bni scope builder
  (`buildScopeFromFile`) didn't handle `DECL_INTERFACE` at all, so
  .bni-declared interfaces were silently dropped; added pass-1/pass-2
  handling mirroring `DECL_TYPE`. The loader's `MergeFiles` also
  dropped `DECL_INTERFACE` from .bni; added it to the preserved-decl
  list so importing TUs see the iface in the merged file.

- **Per-(Pkg, Name) interface registry.** IR-gen's `ModuleInterface`
  gained `Pkg` + `AliasTargetPkg`; lookup helpers all key on
  (Pkg, Name). `registerImportFieldsAndFuncs` registers imported
  interfaces under the import alias's pkg.

- **Imported impls are a separate table.** `Module` gained
  `ImportedImpls @[]@ImplInfo` (separate from local `Impls` so
  codegen doesn't re-emit imported vtables).
  `registerImportFieldsAndFuncs` walks `DECL_IMPL` in each imported
  package's merged file and appends to `ImportedImpls`;
  `findImplVtableName` walks both collections.

- **Receiver lookup splits on qualified name.** The receiver lookup
  had to split into (RecvPkg, RecvTypeName) because cross-package
  types arrive with qualified Name (`hello.Hello`) while `ImplInfo`
  stores bare names. Added `splitQualName` helper;
  `wrapAsIfaceValue` derives RecvPkg from the receiver Type's Name
  (defaulting to `currentModulePkgShort` for same-package).

- **External vtable declarations carry the right array type.**
  Codegen emits `external constant [N+1 x i8*]` declarations for
  imported vtables so importing TUs reference them with the right
  array type. Slot count comes from `ir.IfaceMethodCount`, which
  consults the per-module interface registry.

- **RecvPkg derived from the receiver TypeExpr (orphan-free dup).**
  `collectImplsFromDecl` derives RecvPkg from the receiver's TypeExpr
  (`recvTypePkg` helper) rather than defaulting to the impl's own
  package. `DtorFuncName` and `MethodFuncs` use RecvPkg, so an impl
  declared in a third package emits a vtable that references the
  receiver-package's symbols by their canonical names —
  byte-identical to what the receiver's own package would emit, which
  is what makes `weak_odr` dedup safe.

- **Codegen dedups local vs imported impls.** `emitImplVtables` skips
  imported-impl entries that duplicate a local entry
  (`importedImplCoveredLocally` helper) — without the dedup, a TU
  that both declares the impl AND imports a package that also
  declares it would emit a local `weak_odr` definition followed by an
  external declaration of the same symbol, which LLVM rejects as a
  redefinition.

## Open question: compiled/interpreted interop — **deferred**

The compiled-side flow landed cleanly because the VM compiles each
package separately and the cross-TU vtable references are already
`weak_odr` globals at the LLVM layer. The three concerns originally
flagged (VM's mangled-symbol lookup, interleaved native+VM frames,
dtor-index encoding asymmetry) are still real but specific to
mixed-mode dispatch across cross-package interfaces — they shape some
future slice when bytecode and native frames exchange iv values across
package boundaries. Parked as a follow-up; not on the critical path.

## Out of scope

- Structural / duck-typing satisfaction (Go-style implicit `impl`).
  We're explicit-only by design; allowing `impl` anywhere with
  duplicates is the closest we get.
- Cross-language ABI for iface values. Iface values follow the same
  `%BnIfaceValue = { i8*, i8* }` shape regardless of caller mode;
  no separate work needed.
