# Plan: Cross-Package Interfaces

**Status (2026-05-07):** All four slices (2.6 / 2.7 / 2.8 / 2.9)
landed. Phase 2 cross-package interfaces is feature-complete per
this plan.

## Motivation

Interfaces (Phase 2) currently work only within a single package: an
`impl R : I` declaration must live in the same package as the
declarations of `R` and `I`, and IR-gen / type-check tables for impls
are per-module. To make interfaces useful for library boundaries
(e.g., a `Stringer` defined in one package, implemented by types in
many others), `*A.Iface` and `@A.Iface` need to be first-class types
across package boundaries, and `impl` resolution needs to span
imports.

Phase 2 / cross-package follows the four-slice plan below. Each slice
is independently landable; later slices depend on earlier ones but
each leaves the tree green and the conformance suite passing.

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

4. **Visibility check at iv-construction sites.** Every TU that emits
   `OP_IFACE_VALUE` must have an `impl R : I` visible to the type-
   checker (in itself or transitively imported). If no such impl is
   visible, that's a compile error ("no impl for `*T` satisfying
   `pkg.I` is in scope"). If an impl is visible but no TU in the link
   actually emits the vtable, the linker reports an undefined symbol;
   that's coarser feedback but acceptable while we're still early.

## Slices

### Slice 2.6 — canonicalize vtable mangling — **DONE**

Symbol form changed from `__ivt.bn_<impl_pkg>__<recv>__<iface>` to
`__ivt.bn_<recv_pkg>__<recv>__<iface_pkg>__<iface>`. ImplInfo grew
RecvPkg / IfacePkg; alias chains resolve to canonical (Pkg, Name) at
collection time. Same-package impls land on `<recv_pkg> == <iface_pkg>`
so the package appears twice in the symbol — by design.

### Slice 2.7 — type-checker: cross-package iface refs + impl import — **DONE**

Implementation differed from the original plan in two ways:

- The type-checker shares one `Checker` across packages
  (CheckPackage saves/restores scope but reuses `c.Impls`), so
  cross-package impl visibility was already implicit. The actual
  gap was `implCoversInterface` matching by bare Name, which
  false-matched same-named interfaces in different packages.
  Switched to canonical (Pkg, Name) comparison; types.Type gained
  a Pkg field stamped at TYP_INTERFACE creation. Pointer-equality
  via Type identity would have been simpler but the bootstrap
  interp doesn't support `@T == @T`.
- The bni scope builder (`buildScopeFromFile`) didn't handle
  DECL_INTERFACE at all, so .bni-declared interfaces were silently
  dropped. Added pass-1/pass-2 handling mirroring DECL_TYPE.
- The loader's MergeFiles dropped DECL_INTERFACE from .bni; added
  it to the preserved-decl list so importing TUs see the iface in
  the merged file.
- IR-gen's ModuleInterface gained Pkg + AliasTargetPkg; lookup
  helpers all key on (Pkg, Name). registerImportFieldsAndFuncs
  registers imported interfaces under the import alias's pkg.

Conformance: 373_cross_pkg_iface (impl in importing pkg, iface in
imported pkg).

### Slice 2.8 — IR-gen: cross-package impl table + iv emission — **DONE**

Module gained `ImportedImpls @[]@ImplInfo` (separate from local
`Impls` so codegen doesn't re-emit imported vtables).
registerImportFieldsAndFuncs walks DECL_IMPL in each imported
package's merged file and appends to ImportedImpls. findImplVtableName
walks both collections.

Two follow-on adjustments needed past the original plan:

- The receiver lookup had to split into (RecvPkg, RecvTypeName)
  because cross-package types arrive with qualified Name
  (`hello.Hello`) while ImplInfo stores bare names. Added
  `splitQualName` helper; `wrapAsIfaceValue` derives RecvPkg from
  the receiver Type's Name (defaulting to currentModulePkgShort
  for same-package).
- Codegen emits `external constant [N+1 x i8*]` declarations for
  imported vtables so importing TUs reference them with the right
  array type. Slot count comes from a new `ir.IfaceMethodCount`
  helper that consults the per-module interface registry.

Conformance: 376_cross_pkg_iface_impl_split (iface in pkg/greeter,
type+impl in pkg/hello, use in main).

### Slice 2.9 — orphan-free duplicate impls — **DONE**

Implementation was largely a small fix to IR-gen plus codegen
dedup; the type-checker didn't actually have an explicit same-
package check to remove (it had implicitly worked because
RecvPkg defaulted to the impl declaration's package — fine for
same-package, wrong for cross-package).

- `collectImplsFromDecl` now derives RecvPkg from the receiver's
  TypeExpr (`recvTypePkg` helper) rather than defaulting to the
  impl's own package. DtorFuncName and MethodFuncs use RecvPkg,
  so an impl declared in a third package emits a vtable that
  references the receiver-package's symbols by their canonical
  names — byte-identical to what the receiver's own package
  would emit, which is what makes weak_odr dedup safe.
- Codegen's `emitImplVtables` skips imported-impl entries that
  duplicate a local entry (importedImplCoveredLocally helper) —
  without the dedup, a TU that both declares the impl AND
  imports a package that also declares it would emit a local
  weak_odr definition followed by an external declaration of the
  same symbol, which LLVM rejects as a redefinition.

Conformance:
- 377_iface_impl_in_third_pkg: impl declared in main, receiver in
  pkg/hello, iface in pkg/greeter.
- 378_iface_impl_dup: impl declared in BOTH pkg/hello and pkg/dup,
  both linked into main; weak_odr collapses, dispatch works.

## Open question: compiled/interpreted interop — **deferred**

Slice 2.8 didn't need to revisit this. The compiled-side flow
landed cleanly because the VM compiles each package separately and
the cross-TU vtable references are already weak_odr globals at the
LLVM layer. The three concerns originally flagged (VM's mangled-
symbol lookup, interleaved native+VM frames, dtor-index encoding
asymmetry) are still real but specific to mixed-mode dispatch
across cross-package interfaces — they shape some future slice
when bytecode and native frames exchange iv values across package
boundaries. Park as a follow-up; not blocking Slice 2.9.

## Out of scope

- Structural / duck-typing satisfaction (Go-style implicit `impl`).
  We're explicit-only by design; allowing `impl` anywhere with
  duplicates is the closest we get.
- Cross-language ABI for iface values. Iface values follow the same
  `%BnIfaceValue = { i8*, i8* }` shape regardless of caller mode;
  no separate work needed.

## Implementation order

1. ~~**Slice 2.6** (canonicalize vtable mangling)~~ — **DONE**.
2. ~~**Slice 2.7** (type-checker cross-package)~~ — **DONE**.
3. ~~**Slice 2.8** (IR-gen cross-package)~~ — **DONE**.
4. ~~**Slice 2.9** (allow duplicate impls anywhere)~~ — **DONE**.
5. Mixed-mode (compiled ↔ VM) cross-package iface dispatch —
   follow-up; not on the critical path.
