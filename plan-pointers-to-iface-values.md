# Plan: Pointers to Interface Values

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

## Current state (2026-05-15)

Probed via small ad-hoc tests; results:

- **`**Iface`** — works for assignment, parses, type-checks,
  method dispatch through explicit deref (`(*p).Foo()`) works
  after `438f3f2` fixed the receiver-routing bug. Method dispatch
  via auto-smoothing (`p.Foo()` where `p` is `**Iface`) — not
  tested but suspect it falls through to direct-dispatch the
  same way the `(*p).Foo()` case did pre-fix.
- **`*@Iface`** — works: parses, `(*p).Foo()` dispatches
  correctly (438f3f2 covers this path too, since the deref
  yields a `@Iface` iv-value).
- **`@(*Iface)`** — parses + type-checks, but `(*p).Foo()`
  returns 0 instead of the expected value. Either box() of a
  raw iv doesn't faithfully preserve the iv shape, or the
  pointer-deref-then-vtable-dispatch path miscomputes for
  the `@(...)` form. Needs investigation.
- **`@(@Iface)`** — parses + type-checks, same dispatch failure
  as `@(*Iface)`.
- **`p.Foo()` smoothing on `@(*Iface)`** — rejected at type
  check ("cannot access field on this type"). Receiver smoothing
  doesn't apply to iv's reached through a pointer; only the
  explicit-deref `(*p).Foo()` form is recognized.

Other categories not yet probed: slices / arrays of iv's, struct
fields of iv-pointer type, function args / results of iv-pointer
type, `&iv` in const-context, raw-to-managed conversion through
pointer indirection.

## Slicing

Each slice should keep conformance + unit tests green and land
as one commit.

### Slice P.1 — Coverage audit + conformance pins — LANDED 2026-05-20

- **Scope**: write conformance tests for every shape that the
  design says should work, mark each `.xfail.boot` (interface
  syntax not in bootstrap) and add per-mode xfails where the
  current self-hosted toolchain misbehaves. Goal: convert
  "haven't checked" into "concrete fail entry in conformance"
  so the gaps are visible.
- **Tests landed** (conformance 438–445):
  - 443 `*@I` (raw ptr → managed iv), `(*p).Foo()` — passes.
  - 444 `@(*I)` (managed ptr → raw iv), `(*p).Foo()` — xfail
    in every compiled mode (returns 0); root-cause is Slice P.2.
  - 445 `@(@I)` (managed ptr → managed iv), `(*p).Foo()` — xfail
    in every compiled mode (returns 0); root-cause is Slice P.2.
  - 438 `p.Foo()` smoothing on `**I` — `.error` test pinning the
    current rejection; flips when Slice P.3 lands.
  - 439 `var s *[]*I` (raw slice of raw iv) — xfail in every
    compiled mode (segfaults at dispatch); Slice P.4.
  - 440 `var s @[]@I` (managed slice of managed iv) — xfail;
    Slice P.4.
  - 441 `var arr [N]*I` (array of raw iv) — xfail; Slice P.4.
  - 442 `struct { pp **I }` (struct field of pointer-to-iv) —
    passes.  Establishes that the pointer-to-iv-in-struct path
    works, even though iv-in-slice / iv-in-array does not.
- `**I` (raw → raw) was already pinned by
  `408_iface_method_call_through_deref` from before this plan.
- **Notable absences for follow-up slices to confirm**:
  pointer-to-iv as function arg / return; pointer-to-iv in
  composite literals; `&iv` in const-context; raw-to-managed
  conversion through pointer indirection.  These can be
  added by P.4 once iv-in-container is solid.

### Slice P.2 — Fix dispatch through `@(*Iface)` / `@(@Iface)` — LANDED 2026-05-20

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
- **Tests**: conformance 444 / 445 now pass under all compiled
  modes; their `.xfail.*` markers (except `.xfail.boot`, which
  still applies — bootstrap doesn't have interfaces) are gone.
- **Diff size**: ~10 lines in `pkg/ir/gen_expr.bn` plus the
  xfail-marker churn.

### Slice P.3 — Method-call receiver smoothing to pointer-to-iv — LANDED 2026-05-21

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
- **Tests**: conformance 438 / 448 / 449 / 450 (one per shape)
  + five new unit tests in `pkg/types/check_method_test.bn`
  (four positive, one unknown-method rejection).
- Pre-Slice-P.3 conformance 438 was an `.error` test pinning
  the rejection; it flips to `.expected` 42 and the name drops
  the `_err_` / `_rejected` markers.

### Slice P.4 — Slice / array / field composition — LANDED 2026-05-21

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
  rhs is constructed correctly.
- **Diff size**: ~7 lines.  Same hinting mechanism that already
  worked for var-decl init and selector-LHS field assignment.
- **Tests**: conformance 439 / 440 / 441 — every compiled-mode
  xfail marker (boot-comp + chains) is gone.  Pointer-to-iv in
  struct-field was already covered by 442 (passed pre-P.4).

### Slice P.5 — Bootstrap interpreter parity — DROPPED 2026-05-21

Boot mode was removed from the toolchain (BUILDER_VERSION moved
to `bnc-0.0.1`; conformance + unit tests now run via
`boot-comp` and the compiled chains).  Bootstrap parity is
moot — the slice goes away.

## Slicing order rationale

- P.1 first because the gaps aren't fully mapped; concrete xfailed
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

1. **`@(*Iface)` dispatch returns 0 — root cause unclear.**
   `box(iv)` for a raw-iv value — does box() preserve the iv's
   2-word shape, or does it copy by-value into a managed-int
   slot? Need to check `pkg/codegen/emit_*` for OP_BOX.

2. **Smoothing cross-product.** The five-receiver-kinds doc
   covers `*T / *const T / @T / value` receivers. Iv-pointer
   receivers add a sixth (well, four — raw/managed × raw/managed-
   inner-iv). Decide whether to enumerate all four explicitly
   or fold into a generic "auto-deref to iv-shape" rule.

3. **`*const Iface` and `@const Iface`.** `claude-notes.md` line
   422 explicitly carves these out — no `const` qualifier on
   interface values. So `*const *Iface` (pointer to const iv) is
   the const-pointer-to-iv form, NOT `*const Iface`. Worth
   spelling out in the plan if any user code reaches for the
   carved-out form.

4. **Refcount on managed iv as pointer target.** A
   `@(@Iface)` is a managed-pointer to a managed iface value.
   The outer managed-pointer owns the iv-slot; the iv-slot owns
   the data via its managed inner pointer. Dropping the outer
   pointer should walk the inner iv's destructor — verify the
   dtor chain composes.

5. **`@(Iface)` (bare iface inside parens) stays rejected.** The
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
