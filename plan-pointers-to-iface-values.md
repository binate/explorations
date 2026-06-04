# Plan: Pointers to Interface Values

Status: COMPLETE (shipped); kept for design rationale and root-cause notes.

## Context

Interface values are 2-word value types (`*Iface` raw, `@Iface`
managed). The design pinned in `claude-notes.md` § "Interfaces"
line 421 says:

> Both are 2-word value types (small, copyable). Pointers to
> interface values follow normal pointer rules: `**Stringer`,
> `*@Stringer`, `@(@Stringer)`.

So we need first-class support for all four pointer-to-iv shapes:

- `**Iface` — raw pointer to raw iface-value
- `*@Iface` — raw pointer to managed iface-value
- `@(*Iface)` — managed pointer to raw iface-value
- `@(@Iface)` — managed pointer to managed iface-value

Plus the obvious extensions: slices of iv's (`*[]*Iface`,
`@[]@Iface`, etc.), arrays of iv's, struct fields of pointer-to-iv
type, function-value args/returns of pointer-to-iv type.

`(Iface)` alone (the bare paren form) stays rejected — the
interface itself is not a value type; only `*Iface` and `@Iface`
are. The parens in `@(*Iface)` / `@(@Iface)` are required by the
grammar to disambiguate `@` applied to an already-`@`-sugared iv
from a malformed `@@`.

## Root-cause notes (the why)

### Dispatch through `@(*Iface)` / `@(@Iface)` (managed ptr → iv)

- **Root cause**: `pkg/ir/gen_expr.bn` deref of `*p` only
  recognized `TYP_POINTER` when determining the pointed-to type;
  for `TYP_MANAGED_PTR` it fell back to `TypInt`, so the IR
  emitted `load i64` instead of `load %BnIfaceValue`.  That
  produced a receiver with the wrong type at `genInterfaceMethod
  Call`, where the `iv.Typ.Elem == nil` guard fired and the
  call returned a constant 0.
- **Fix**: extend the deref's pointer-kind check to handle
  `TYP_MANAGED_PTR` alongside `TYP_POINTER` (both have an
  `Elem` field carrying the pointed-to type; the deref is a
  value load either way, refcount stays on the operand).
- box() of an iv-value was *not* the bug — the box correctly
  preserved the 16-byte iv shape; the problem was on the load
  side after the box.

### Method-call receiver smoothing to pointer-to-iv

- **Approach taken**: split between layers per the plan's
  option (a) + (b) hybrid.  Type-checker accepts the call;
  IR-gen emits the load before dispatch.
- **All four shapes smooth**: `**I`, `*@I`, `@(*I)`, `@(@I)`
  all auto-deref through one pointer layer to the iv at method-
  call sites.  This matches the design intent (pointers to iv
  follow normal pointer-receiver rules — `claude-notes.md`
  line 421).
- **Diff**:
  - `pkg/types/check_method.bn` — `tryMethodCall` peels one
    pointer layer when the operand is `TYP_POINTER` /
    `TYP_MANAGED_PTR` with an iv `Elem`, then routes to
    `tryInterfaceMethodCall`.
  - `pkg/ir/gen_iface.bn` — `isInterfaceMethodCall` returns
    true for pointer-to-iv receivers; `genInterfaceMethodCall`
    emits a single `EmitLoad` (sized by the inner iv kind)
    before the dispatch.

### Slice / array / field composition (`s[i] = &t` for iv elements)

- **Root cause**: `s[i] = &t` for a slice / array / managed-slice
  whose element type is iv bypassed the iv-construction path —
  the rhs was generated with `lhsTypHint = nil`, so
  `genExprOrFuncRef` saw a `*T` value and stored just the data
  pointer into the 16-byte iv slot.  Vtable half stayed
  undefined; subsequent dispatch loaded `{data, garbage}` and
  segfaulted calling through the bogus vtable.
- **Fix**: in `pkg/ir/gen_control.bn`'s single-assign path,
  when LHS is EXPR_INSTANTIATE_OR_INDEX, set the rhs's typ-hint
  to the collection's element type via `getIndexElemType`.  The
  existing iv-wrap path in `genExprOrFuncRef` then fires and the
  rhs is constructed correctly.  Same hinting mechanism that
  already worked for var-decl init and selector-LHS field
  assignment.

### Bootstrap interpreter parity — dropped

Boot mode was removed from the toolchain (BUILDER_VERSION moved
to `bnc-0.0.1`; conformance + unit tests now run via
`boot-comp` and the compiled chains).  Bootstrap parity is moot.

## Slicing order rationale

The work was sliced as: (P.1) coverage audit + conformance pins,
(P.2) fix dispatch through `@(*Iface)` / `@(@Iface)`, (P.3)
method-call receiver smoothing to pointer-to-iv, (P.4) slice /
array / field composition.

- P.1 first because the gaps weren't fully mapped; concrete xfailed
  conformance tests make the surface area visible.
- P.2 before P.3 because dispatch via explicit deref `(*p).M()`
  has to work before smoothing can be designed sensibly — the
  smoothed form is `p.M()` ≡ `(*p).M()`, so the latter being
  the canonical lowering means it has to work first.
- P.3 next because smoothing is the ergonomic-but-not-essential
  layer atop P.2's correctness.
- P.4 last because composition is "just works" once the building
  blocks are correct; the slice exists to pin that.

## Implementation notes / open questions

1. **Smoothing cross-product.** The five-receiver-kinds doc
   covers `*T / *const T / @T / value` receivers. Iv-pointer
   receivers add a sixth (well, four — raw/managed × raw/managed-
   inner-iv). Decide whether to enumerate all four explicitly
   or fold into a generic "auto-deref to iv-shape" rule.

2. **`*const Iface` and `@const Iface`.** `claude-notes.md` line
   422 explicitly carves these out — no `const` qualifier on
   interface values. So `*const *Iface` (pointer to const iv) is
   the const-pointer-to-iv form, NOT `*const Iface`. Worth
   spelling out in the plan if any user code reaches for the
   carved-out form.

3. **Refcount on managed iv as pointer target.** A
   `@(@Iface)` is a managed-pointer to a managed iface value.
   The outer managed-pointer owns the iv-slot; the iv-slot owns
   the data via its managed inner pointer. Dropping the outer
   pointer should walk the inner iv's destructor — verify the
   dtor chain composes.

4. **`@(Iface)` (bare iface inside parens) stays rejected.** The
   interface itself isn't a value type, only the iv is. The
   parser already errors on `(Iface)` so no extra work needed.

## Cross-references

- `claude-notes.md` § "Interfaces" — design summary (line 421
  enumerates the pointer-to-iv shapes)
- `plan-interface-syntax-revision.md` — base iface-value design
- `plan-interface-embedding.md` — interface extension (orthogonal
  but the same vtable-shape machinery)
- `claude-todo.md` § "Pointers to interface values" — TODO entry
  this plan supersedes
- `pkg/ir/gen_iface.bn:isInterfaceMethodCall` — receiver-routing
  helper extended in `438f3f2` to handle deref-receivers
