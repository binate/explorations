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

### Slice P.1 — Coverage audit + conformance pins

- **Scope**: write conformance tests for every shape that the
  design says should work, mark each `.xfail.boot` (interface
  syntax not in bootstrap) and add per-mode xfails where the
  current self-hosted toolchain misbehaves. Goal: convert
  "haven't checked" into "concrete fail entry in conformance"
  so the gaps are visible.
- **Files**:
  - `conformance/40N_iv_ptr_*` (one test per shape — raw, managed,
    cross product of inner-iv and outer-pointer kinds).
  - Method dispatch via `(*p).Foo()` already pinned by
    `408_iface_method_call_through_deref`; extend to managed
    forms.
  - Slice / array of iv: `var s *[]*Iface = ...` and `var s @[]@Iface = ...`.
  - Struct field of pointer-to-iv.
- **Tests**: each test calls a method through the receiver,
  prints a known value, and pins it via `.expected`. Negatives
  (rejected `(Iface)`, rejected smoothing if we decide it
  stays rejected) get `.error` files.
- **Estimated size**: ~150 lines mostly conformance tests + xfail markers.

### Slice P.2 — Fix dispatch through `@(*Iface)` / `@(@Iface)`

- **Scope**: the paren-form managed-pointer-to-iv parses + type-
  checks but `(*p).Foo()` returns 0. Root-cause and fix. Either
  box() of an iv-value isn't faithfully preserving the iv, OR
  the deref-then-dispatch path drops the iv shape for managed-ptr
  receivers.
- **Probable location**: `pkg/codegen/emit_*` for box-of-iv, OR
  `pkg/ir/gen_iface.bn` for the iv shape recovery after deref.
- **Tests**: re-enable conformance from P.1 that pin these
  shapes.
- **Estimated size**: hard to predict without root cause —
  likely 50–200 lines including unit tests.

### Slice P.3 — Method-call receiver smoothing to pointer-to-iv

- **Scope**: today `p.Foo()` where `p` is `*@Iface` (raw ptr to
  managed iv) is rejected at type check. Add a smoothing rule:
  pointer-to-iv auto-derefs at method-call sites the same way
  `*T → T` does. Either: (a) at the type-checker, treat a
  pointer-to-iv receiver as if it were dereferenced before
  iface-value method lookup; or (b) at IR-gen, emit the
  load-then-dispatch sequence. Probably (a) is cleaner.
- **Question to resolve before implementation**: should smoothing
  cover `**Iface → *Iface` (raw → raw deref) AND `@(*Iface) →
  *Iface` (managed-ptr-deref to raw iv) AND `@(@Iface) → @Iface`?
  All four combinations? The five-receiver-kinds doc suggests
  yes; pin in the plan doc when we resolve it.
- **Files**: `pkg/types/check_method.bn` (receiver-resolution),
  `pkg/ir/gen_method.bn` or `gen_iface.bn` (lowering).
- **Tests**: extend P.1's conformance suite to cover the
  smoothing forms; unit tests in `pkg/types` for the new
  receiver-kind cases.
- **Estimated size**: ~100 lines + tests.

### Slice P.4 — Slice / array / field composition

- **Scope**: ensure `*[]*Iface`, `@[]@Iface`, `[N]*Iface`,
  `struct { iv *Iface }` and the pointer-to-iv versions of each
  compose correctly: assignment, RefInc/RefDec for managed forms,
  composite literal initialization, indexing + method dispatch.
- **Tests**: conformance + unit. Most likely this "just works"
  once P.2 / P.3 are in, but pin the cases that the design says
  should work so a regression surfaces.
- **Estimated size**: ~100 lines mostly tests.

### Slice P.5 — Bootstrap interpreter parity (if needed)

- **Scope**: the bootstrap interpreter today rejects all
  interface forms (per `interfaces not supported by bootstrap`
  xfail markers across the conformance suite). If/when bootstrap
  gains iface-value support, this slice extends it to the
  pointer-to-iv shapes.
- **Defer**: until bootstrap gets iface-value support generally.
  Probably never, since the self-hosted toolchain is the long-
  term reference.

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
