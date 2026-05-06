# Plan: Cross-Package Interfaces

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

### Slice 2.6 — canonicalize vtable mangling

**Standalone change, independent of the cross-package work.** Renames
the vtable symbol so two TUs with the same `(R, I)` pair agree on the
mangling regardless of where the `impl` declaration lives. With same-
package-only impls (today), the canonical packages collapse onto the
impl's package and behavior is unchanged; the rename is purely
forward-looking.

Files:
- `pkg/ir/gen_impl.bn` (`implVtableName`): build the symbol from the
  receiver type's defining package + name plus the interface's
  canonical (alias-resolved) defining package + name. The
  `ImplInfo` struct grows two fields (`RecvPkg`, `IfacePkg`) populated
  by `collectImplsFromDecl`.
- `pkg/ir/gen_impl.bn` (`findImplVtableName`): keys lookup on the
  canonical pair, not on bare names.
- `pkg/codegen/emit_impls.bn`: unchanged — `implVtableName` is the
  single source of truth for the symbol.
- `pkg/vm/lower.bn`: same — uses `implVtableName` from the IR layer.
- Unit tests: update `TestEmitImplVtableSingleMethod` etc. to expect
  the new symbol form (e.g.
  `@__ivt.bn_main__T__main__I` instead of `@__ivt.bn_main__T__I`).

Acceptance: conformance green in boot-comp, boot-comp-int; same
behavior, different symbol.

### Slice 2.7 — type-checker: cross-package iface refs + impl import

Two pieces:

1. **Qualified interface references.** Parser already handles
   `*pkg.T` and `@pkg.T` for managed/raw pointers; extend the same
   path for `*pkg.Iface` / `@pkg.Iface`. Type-checker resolves the
   qualified ref against the imported package's interface table.

2. **Cross-package impl visibility.** Each `Checker` exposes its
   `Impls` table to importing checkers. The importing TU's
   assignability arms (`canAssignToRawInterfaceValue` /
   `canAssignToManagedInterfaceValue`) walk both the local impl table
   and the imported tables to find a satisfying `(R, I)` pair. Storage
   shape: `c.Impls @[]@Impl` becomes searched along with
   `c.Imports[i].Impls`. The `Impl` records use canonical iface refs
   (Slice 2.4 already added the alias-resolution helper).

Files:
- `pkg/parser/parse_decl.bn`: accept `*pkg.Name` / `@pkg.Name` in the
  type-ref grammar arm that today only handles bare iface names.
- `pkg/types/check_impl.bn`: store canonical `IfacePkg` + `IfaceName`
  on each `Impl`. Resolve aliases at collection time.
- `pkg/types/types_query.bn`
  (`canAssignToRawInterfaceValue` / managed counterpart): widen the
  impl search to imports.
- `pkg/types.bni`: export the impl table on `Checker` for importers.

Acceptance: a conformance test where package A defines `Stringer`,
package B defines `*T` that implements `String() @[]char`, and an
`impl *T : A.Stringer` lives in B (or A) and gets exercised from
either side.

### Slice 2.8 — IR-gen: cross-package impl table + iv emission

IR-gen needs to know which vtables exist so `OP_IFACE_VALUE` can spell
the mangled name. Today `findImplVtableName(m, recvName, ifaceName)`
walks `m.Impls`. With cross-package, the same lookup must walk the
importing TU's union of (its own + each imported package's) impl
table.

The lookup is moot for the *current* TU's vtable emission — that
still happens via `emitImplVtables(m)` and only emits impls declared
in this TU's source. Cross-package use sites end up with `OP_IFACE_VALUE`
that references a vtable defined `weak_odr` in another TU; the LLVM
emitter simply names the global, and the linker resolves it.

Files:
- `pkg/ir.bni`: `Module.Impls` becomes a window onto local impls
  plus references to imported impl tables (or a flat union, populated
  at module-finalize time).
- `pkg/ir/gen_impl.bn` (`findImplVtableName`): walk the union.
- `pkg/ir/gen_module.bn`: pull imported impl tables in alongside
  imported funcs / types.
- No backend changes — `weak_odr` linkage already in place handles
  duplicate vtable definitions across TUs.

Acceptance: same conformance test as 2.7 but exercising a
construction site in package C (a third package using A's iface
over B's type with the impl declared in either A or B).

### Slice 2.9 — orphan-free duplicate impls

The duplicate-OK convention. Once cross-package iface refs land
(Slice 2.7) and IR-gen knows about transitive impls (Slice 2.8),
this slice tightens the rules:

1. The type-checker accepts identical impl declarations in different
   packages. "Identical" means same canonical `(R, I)` pair; method
   bindings are necessarily the same since methods are tied to `R`'s
   defining package.
2. No conflict diagnostic on duplicate declarations — the linker's
   `weak_odr` collapse is the single source of truth.

Files:
- `pkg/types/check_impl.bn`: drop the same-package requirement, drop
  any duplicate-impl error path.
- Conformance test: two packages declaring the same `impl *T : I`
  for an iface from a third package, link both, verify dispatch
  works.

Acceptance: each TU emits its `weak_odr` vtable, linker keeps one,
behavior identical to single-impl case.

## Open question: compiled/interpreted interop

The VM today builds its own `IfaceVtables` table at module-load time
(`lowerImplVtables`), keyed off the same mangled symbol the LLVM side
uses. Cross-package interop in mixed mode raises questions worth
revisiting before the implementation work, not just at the end:

- **VM's mangled-symbol lookup.** The VM's `findIfaceVtable(vm, name)`
  is a linear scan over `vm.IfaceVtables` records loaded from the
  module's `m.Impls`. With cross-package impls, the VM's view of
  `m.Impls` needs to be the same union the IR-gen layer computes. If
  the lowering layer hands the VM only the local module's impls, a
  cross-package iv construction looks the symbol up and misses.
- **Interleaved native + VM frames.** Today a compiled function can
  call into VM code (and vice versa) via the function-value shim
  convention. Iface dispatch through a VM-loaded vtable from a
  native frame would need the VM's vtable record to match the LLVM
  vtable's slot order *exactly* — currently they do (slot 0 = dtor,
  slots 1..N+1 = methods, after Slice 2.5), but cross-package iface
  values that originate in one mode and get dispatched in the other
  put more pressure on that invariant.
- **Dtor index encoding.** Compiled vtable slot 0 holds an `i8*`
  function pointer. VM's `Methods[0]` holds a 1-based VM func index.
  When an `@Iface` value is constructed in compiled mode and consumed
  in VM mode, the VM-side scope-exit RefDec needs the dtor as a VM
  index — but the actual vtable in memory holds the native function
  pointer. This already works for same-package because the VM
  `lowerImplVtables` synthesizes its own parallel vtable record, but
  with cross-package the symmetry is more subtle (which view "owns"
  the canonical record across TUs?).

These don't block the slices above, but they shape Slice 2.8 and the
shape of `Module.Impls`. Worth a short side-discussion before
landing 2.8.

## Out of scope

- Structural / duck-typing satisfaction (Go-style implicit `impl`).
  We're explicit-only by design; allowing `impl` anywhere with
  duplicates is the closest we get.
- Cross-language ABI for iface values. Iface values follow the same
  `%BnIfaceValue = { i8*, i8* }` shape regardless of caller mode;
  no separate work needed.

## Implementation order

1. **Slice 2.6** (canonicalize vtable mangling) — independent, ship
   first.
2. **Slice 2.7** (type-checker cross-package).
3. Sidebar discussion: compiled/interpreted interop expectations for
   `Module.Impls`.
4. **Slice 2.8** (IR-gen cross-package).
5. **Slice 2.9** (allow duplicate impls anywhere).
