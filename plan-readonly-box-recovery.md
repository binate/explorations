# Plan: a box records its object's `readonly`; recovery may add it but not drop it

Status: drafted 2026-10-03 (work-3).  Implements the user's decision (option (a)) on the claude-todo entry
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

## Representation (proposed)

Keep the vtable layout.  Give each type that is ever boxed as a readonly object a second TypeInfo record,
`__typeinfo.<T>` plus a readonly variant (a distinct mangled symbol, same 8-word layout, same dtor / size /
fields, name `readonly <T>`), and a readonly-variant vtable per (T, I) row used by such boxes: a copy of the
(T, I) vtable whose any-block slot 1 points at the readonly TypeInfo.  Identity stays an address compare:

- concrete mutable target `T`: compare slot 1 against `&__typeinfo.<T>` only (a readonly box misses);
- concrete readonly target `readonly T` and the value copy: compare against either record (two compares);
- interface target: the satisfaction registry (`CollectSatEntries` / `rt.SatLookup`, and the VM's own
  `lookupSatEntry`) gets (readonly T, J) entries only for the impls whose methods all bind a read-only
  receiver, pointing at the readonly-variant sub-vtables; the lookup itself is unchanged.
- an upcast keeps the variant: a child→parent vtable offset inside a readonly-variant concatenated vtable
  lands in its readonly-variant parent part (each variant vtable is a full copy of the concatenated layout).

Alternative considered: a flag word in the vtable any-block.  It changes the vtable layout on every backend
and the interop contract (§7.13.8), and still needs a satisfaction-side filter, so the second record is the
smaller change.  Raise this choice with the user before implementing.

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
