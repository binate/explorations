# Plan: a box records its object's `readonly`; recovery may add it but not drop it

Status: drafted 2026-10-03, design refined 2026-10-04 (work-3; in progress).  Implements the user's decision (option (a)) on the claude-todo entry
"Spec decision: may a type assertion recover a MUTABLE pointer to a boxed `readonly` named value?".

## The rule

A box of a readonly object — `var x *any = &c` with `c readonly Celsius`, `@readonly Node -> @any`, the
implicit value-borrow of a readonly variable, `&rs` with `rs readonly S` — has a dynamic type that records
the object's readonly.  On such a box:

- `.(*readonly Celsius)`, `.(@readonly Node)` and the value copy `.(Celsius)` match;
- `.(*Celsius)` / `.(@Node)` miss (abort in the expression form, `ok = false` in comma-ok; a type-switch case
  does not match);
- an interface target `.(*J)` / `.(@J)` matches only if the static widening of a readonly object to `J` would
  be accepted (every method of `J` that T implements binds a read-only receiver); a `Set` method taking a
  mutable receiver makes it miss;
- a box of a mutable object behaves as today (and `.(*readonly T)` matches it too — readonly may be added).

Slices already keep element readonly in their structural identity (`*[]readonly char` vs `*[]char`), so this
covers the nominal (named) dynamic types and pointer boxes.

## Representation (decided: a second, readonly record — user, 2026-10-04)

The readonly variant of a NAMED type T is a second receiver identity in T's package: the synthetic name
`__readonly_T` (irutil.ReadonlyVariantName; `__` names are reserved, so no user type can be named that — a
bracket form `__readonly_T` was rejected because nameIsGenericInst and the symbol manglers read brackets as a
generic instance).  The impl emitters name a row's method functions through ir.ImplMethodFuncName, which maps
the variant back to T's methods.  Everything keyed on a receiver identity then works unchanged:
- its own TypeInfo record `__typeinfo.<pkg>.__readonly_T` — same size / align / kind / fields / element
  words / dtor as T's (built from the same RecvTyp), display name `readonly <pkg>.T`;
- its own ImplInfo rows `(__readonly_T, I)` — same MethodFuncs (thunks included) and slot-0 dtor as
  `(T, I)` — so the vtable emitters (LLVM, native x3, VM), the satisfaction registry (CollectSatEntries,
  rt.SatLookup, the VM's lookupSatEntry), the package descriptors and the VM's name-based upcast suffix swap
  all handle it as just another receiver;
- `(__readonly_T, any)` rows via ensureAnyImplInfo at a readonly box site, like any `any` row.

A row `(__readonly_T, I)` exists only where a readonly object may reach I: the impl binds a read-only
receiver (`impl *readonly T : I`, `impl @readonly T : I`, `impl readonly T : I` — what receiverAssignable
admits for a readonly object; a plain value receiver `impl T : I` is a mutable copy and does not qualify).
As implemented: collectImplsFromDecl appends the variant rows (ancestors included, so a readonly-variant
concatenated vtable has the same layout and upcasts keep the variant); a generic receiver's rows are minted
on demand at the box site (ensureReadonlyVariantRows after ensureGenericImplInfo); a box of an imported
type names the variant vtable through ensureImportedImplInfo, and ensureAnyImplInfo mints the
`(__readonly_T, any)` row like any `any` row.  These rows are
emitted by the impl's own package UNCONDITIONALLY (a box site elsewhere names the vtable by symbol, and an
interface-target assertion on a readonly box needs the satisfaction entry), so each readonly-compatible impl
costs one more vtable + satisfaction entry, and each such type one more record.

Only named dynamic types get a variant.  A name-less type (slice, array, function value) is recovered by
value (a copy — no write-through) and already keeps element readonly in its identity; a named type over a
readonly type (`type RI readonly int8`) is readonly at its own outer level (`type.readonly.named`), so a
recovered `*RI` already refuses writes.

Box sites: the object is readonly when the boxed pointer's pointee is outer-`readonly` (after aliases) in the
checker type the call site passes (srcExprTyp): `&c` with `c readonly T`, `@readonly T`, the value-borrow of
a readonly variable, `box` of a readonly value.  Every wrapAsIfaceValue caller must pass srcExprTyp (a nil
one would silently box a readonly object as mutable — make it fail loud for a named pointee).

Matching: a mutable concrete target `.(*T)` / `.(@T)` compares slot 1 against `&__typeinfo.T` only (a
readonly box misses); a readonly target `.(*readonly T)` / `.(@readonly T)` and the value copy `.(T)`
compare against both records.  One helper builds the match for the expression, comma-ok and type-switch
forms.  An interface target is the satisfaction lookup, unchanged: a readonly box finds only the
`(__readonly_T, J)` entries, whose sub-vtables keep the variant.

Alternative considered: a flag word in the vtable any-block.  It changes the vtable layout on every backend
and the interop contract (§7.13.8), and still needs a satisfaction-side filter.

## Sites

- Box construction (`irgen/gen_iface.bn` `wrapAsIfaceValue`, value-borrow `gen_borrow.bn`, `box`,
  interface upcasts): today `stripOuterReadonly` / StripConstForIR erase the object's readonly; keep it in a
  flag that selects the readonly-variant vtable.  IR types have readonly stripped (StripConstForIR), so the
  information must come from the checker type the call sites already pass (`srcExprTyp`).
- Records: `ir.CollectTypeInfoDescs`, `irdata.BuildTypeInfo`, the vtable emitters (LLVM `codegen/
  emit_impls.bn`; native aarch64 / x64 / arm32 `*_iface.bn`; VM `vm/lower.bn`), `CollectSatEntries`.
- Matching: `gen_assert.bn` (`typeInfoSymFor` and the compare), `gen_assert_commaok.bn`,
  `gen_type_switch.bn`, `gen_assert_iface.bn`.
- REPL and interop: injected / compiled-in packages see the same symbols; check `gen_repl_iface.bn`.
- Spec §11.12: drop "outer-`readonly` stripped" for the object's readonly and "independent of any `readonly`"
  for the concrete match; state the add-but-not-drop rule for the object; the interface-target condition.

## Tests

Conformance (every backend: LLVM, VM, native aa64 / x64 / arm32): readonly named scalar, struct and pointer
boxes into `*any` / `@any` and a user interface; each recovery kind, comma-ok and type-switch forms; an
interface target with all-readonly receivers (hit) and with a mutating method (miss); a mutable box still
recovered by `.(*readonly T)`; cross-package (the box made in one package, asserted in another).  The probes
on the todo entry (`x.(*Celsius)` then `*p = 5`; `x.(*Setter).Set(9)`) become misses.  Unit tests for the
record / vtable emission where IR shape is pinned.
