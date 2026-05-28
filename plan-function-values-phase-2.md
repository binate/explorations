# Plan: Function Values — Phase 2 (Closures + Method Values)

> **Status: DRAFT (2026-05-27)** — design pass, not yet ratified.
> Predecessor: [plan-function-values.md](plan-function-values.md)
> §"Phase 2 — Closures + method values (DEFERRABLE)" + §"Capture
> design — open". This document closes the open design questions
> and slices the implementation into B.1..B.N. Phase 1 (A.1–A.7)
> landed 2026-05-01 and is the substrate.

## Why this plan exists separately

Phase 2 was deferred from the main function-values plan because the
capture-design was openly unresolved at the time. With the design
notes already committing to "always capture by value, no capture
lists, no by-reference" (see
`claude-discussion-detailed-notes.md` §"Closures"), most of the
design space is closed; this doc consolidates that decision,
answers the residual open questions, and lays out the
implementation slices in the same form Phase 1 used (A.1–A.7).

## Scope

Phase 2 = the *user-facing* closure feature + method values:

1. **Capturing function literals** — `func(...) { use(local) }`
   where `local` is from the enclosing function/block scope. Phase
   1 rejects these at the type-checker; Phase 2 lifts the
   restriction.
2. **Method values** — `x.M` (receiver `x` bound) producing a
   function value. Method expressions `T.M` (Phase 1 / A.6) are
   the no-receiver-bound form and stay non-capturing.

Out of scope (still):

- Generics. Function-value types of generic functions need
  monomorphized instantiation; orthogonal piece, separate plan.
- Cross-mode dispatch fill-in (Phase 3 of the umbrella plan).
- Recursive anonymous closures via Go-style self-capture (see
  §"Recursive lambdas" below — confirmed NOT supported).

## Decided design

### Capture semantics: always by value

**Captured locals are snapshot at the moment of the literal's
evaluation.** Writes to the captured-name *inside* the closure
body are local to the closure's copy and do not propagate back to
the enclosing scope. Writes to the original *outside* the closure,
*after* the closure is constructed, are not visible to the closure.

```binate
x := 5
f := func() int { return x }
x = 10
f()  // returns 5 — captured by value
```

Source: `claude-discussion-detailed-notes.md` §"Closures":

> Always capture by value. No capture-by-reference, no capture
> lists. If you want shared mutable state, capture a pointer
> (managed or raw).

**No capture lists** in source syntax — the compiler infers
captures from free-variable analysis of the literal's body. (C++
-style explicit `[x, &y]` capture lists are not adopted; cognitive
load doesn't pay for itself when by-value is the only mode.)

Shared mutable state is expressed by capturing a managed pointer:

```binate
count := box(0)
inc := func() int { *count = *count + 1; return *count }
```

The closure's captured `count` is the `@int` itself — the
pointer-value is snapshotted, but the *pointee* is the same heap
slot, so writes from inside the closure are visible to subsequent
calls and to the enclosing scope (until it drops its own
reference).

### Capture analysis (type-checker)

Free-variable analysis at type-check time:

1. Body is now checked in a scope whose parent is the *enclosing*
   scope (not the package scope, as Phase 1 does).
2. Each Ident lookup that resolves to a symbol bound in an
   enclosing **function or block** scope (not the package scope,
   not a builtin) is recorded as a capture on the literal's
   synthetic Decl.
3. Each captured local's `(name, type, kind)` is added to a
   `Decl.Captures @[]@CaptureInfo` list, preserving first-use
   order (stable iteration → stable closure-struct layout).

A capture's *kind* is one of `CAP_VALUE`, `CAP_RAW_PTR`,
`CAP_MANAGED_PTR` — derived from the captured type, not from how
the closure uses it. This decides refcount behavior at
construction and dtor.

### Closure struct + per-shape vtable instance

Per (literal, capture-shape), the compiler synthesizes:

- A **closure struct type** `__closure_<lit_id>` with fields for
  each captured value (laid out in capture order, with standard
  alignment rules — same as user structs).
- A **call shim** `__shim_<lit_id>(data *uint8, args...) → ret`
  that:
  1. Bit-casts `data` to `*__closure_<lit_id>`.
  2. Loads each captured field into a parameter of the lifted
     body (alongside the user-declared params).
  3. Tail-calls the lifted body.
- A **dtor** `__dtor_<lit_id>(data *uint8)` that walks the
  managed-typed fields and RefDecs them. For purely-non-managed
  capture sets (only value types and raw pointers), the dtor is
  the zero-cost no-op — but it's still generated to keep the
  vtable shape uniform (`{dtor, call}`).
- A **static vtable instance** `__vt_<sig>_<lit_id>` of the
  signature's vtable type, populated with `{__dtor_<lit_id>,
  __shim_<lit_id>}`.

This mirrors Phase 1's per-function vtable-instance scheme,
extended with a real dtor and a real shim.

### Allocation: raw vs managed forms

| Form          | Closure backing      | Lifetime                                                       |
|---------------|----------------------|----------------------------------------------------------------|
| `*func(...)`  | Stack-alloc'd struct | Tied to the enclosing function's frame                         |
| `@func(...)`  | Heap-alloc'd struct  | Refcounted; `vtable.dtor` runs at refcount=0                   |

- `*func(...)` from a capturing literal contains a raw pointer
  into the enclosing frame. Per the escape-hatch policy for raw
  types in general, **the type checker does not attempt to prove
  non-escape** — the user opting into `*func` opts into lifetime
  responsibility, same as any other `*T`. A linter rule (see B.5)
  warns on the obvious-escape patterns (return, store-to-
  outliving-field, etc.) but is informational, not a hard error.
  Rationale: real escape analysis is a whole-program flow problem
  the type system can't honestly deliver; catching only the
  obvious cases as type errors gives a false sense of safety
  while still missing the subtler escapes (via out-params, via
  interfaces, via mutating callees). See also the parallel
  cleanup item in `claude-todo.md` to demote the existing raw-
  slice escape type-check to a linter rule for consistency.
- `@func(...)` *is* allowed to escape; the heap allocation +
  refcount keep the capture struct alive.
- `*func → @func` does **not** auto-promote. (Symmetric with the
  `*[]T → @[]T` rule.) A user-written closure that needs to
  outlive its frame must be typed `@func(...)` directly so that
  IR-gen emits the heap allocation up front.
- `@func → *func` **does smooth** (the borrow direction,
  symmetric with `@[]T → *[]T`). The smoothed `*func`'s `data`
  slot points into the original @func's heap struct; the smoothed
  value borrows for the duration of the smoothed expression. The
  vtable is shared — its `dtor` slot still points at the closure
  -struct destructor, which is only invoked through the managed
  handle at refcount=0 (never through the borrowed `*func`). This
  makes `*func` the natural parameter type for higher-order
  helpers (`slices.Map(s, mapper *func(T) U)` accepts both
  managed function values via smoothing and locally-constructed
  capturing ones).

### Lifetime extension for non-managed captures inside `@func`

An `@func` capturing a value-typed local (`int`, struct, etc.)
copies the value into the closure struct. The closure struct
keeps that copy alive — no special handling needed beyond the
existing "managed allocation owns its non-managed-pointer
fields" rule.

An `@func` capturing a raw pointer (`*T` to a local, etc.) holds
that raw pointer in the struct. Lifetime is then **exactly as
unsafe as the underlying raw pointer**: if the pointee outlives
the closure, fine; if not, UB. This matches the policy elsewhere
in the language (raw pointers are the escape hatch; the user
opts in). A linter rule may warn (see §"Method value: `@func`
capturing `*T` receiver" below for the analogous discussion).

### Method values `x.M`

`x.M` is a function value whose closure captures the receiver.
The receiver kind comes from the method's *declared* receiver
(not the user's spelling of `x`), with smoothing applied:

| Method's declared receiver | `x`'s type    | Captured form       | Shim does                                             |
|----------------------------|---------------|---------------------|-------------------------------------------------------|
| value `T`                  | `T`           | `T` copy            | `M(captured, args...)`                                |
| value `T`                  | `*T` / `@T`   | `T` copy via deref  | `M(captured, args...)`                                |
| `*T`                       | `T`           | `*T = &captured`    | `M(&captured, args...)`                               |
| `*T`                       | `*T`          | `*T` snapshot       | `M(captured, args...)`                                |
| `*T`                       | `@T`          | `*T` from `@T` (smoothing) | `M(captured, args...)`                          |
| `@T`                       | `@T`          | `@T` (RefInc'd)     | `M(captured, args...)`                                |
| `@T`                       | `T` / `*T`    | **error**           | (no managed handle to bind)                           |

The shim's signature matches the function-value type's signature
exactly — i.e., the receiver is *not* a parameter of the function
value (unlike `T.M` method expressions, where the receiver *is*
Params[0]). This is the user-visible distinguishing feature
between method expressions and method values.

#### `@func(...)` capturing `*T` receiver

Allowed, **with a linter warning**. Rationale: the same escape
-hatch policy we apply to raw pointers in general. A user-written
`@func(...)` whose receiver capture is a `*T` is exactly as
unsafe as that `*T`; the user opts in. The linter can flag the
combination as a likely-bug pattern.

(Open during plan-function-values.md drafting; closing here as
"allow + linter warning" matches the existing escape-hatch
treatment elsewhere.)

### Vtable type identity across packages

Two function-value types are **structurally equivalent** iff
their signatures match (same param types, same result types, in
the same order). Vtable types are uniqued by mangled signature
string, not by source package. A `*func(int) int` declared in
`pkg/foo` and a `*func(int) int` declared in `pkg/bar` resolve
to the same vtable type and can be assigned across package
boundaries without conversion.

The mangling: `__vt_<sig-mangled>` where `<sig-mangled>`
follows the same name-mangling scheme as Phase 1's vtables.
Define in `pkg/mangle` (or `pkg/codegen` if uniqueness is a
codegen-internal concern); used uniformly by IR-gen, codegen,
VM, and native backends.

(Open during plan-function-values.md drafting; closing here as
"structural by signature" matches the user-visible structural
typing of function values.)

### Recursive lambdas

**Not supported**, same stance as Phase 1.

Reasons unchanged:

- Go-style recursive lambda relies on capture-by-reference. We
  capture by value, so the body would close over the *nil* value
  the var has at literal-evaluation time, not the closure itself.
- Named top-level recursive functions are unaffected.
- Y-combinator workaround exists.
- Cheaper to add later than to take away.

If a user really needs recursive anonymous code, the documented
pattern is:

```binate
type Step *func(*Step, int) int
var step Step = func(self *Step, x int) int {
    if x == 0 { return 0 }
    return x + (*self)(self, x - 1)
}
step(&step, 5)
```

(Awkward; that's the point. Use named top-level functions
instead.)

### Function-value equality / nil

Following plan-function-values.md §"Open questions": mirror Go.

- `f == nil` / `f != nil`: compares both data and vtable for
  nilness. A zero-initialized function value (data=nil,
  vtable=nil) compares equal to nil.
- Structural comparison between two non-nil function values
  (`f == g`): **not supported**. Two function values constructed
  from the same source function may have distinct closure-struct
  identities (different captures), so structural equality has no
  well-defined semantics outside the nil case.

This matches the interface-value comparison story
(plan-interface-syntax-revision.md): only nil comparison.

## Implementation slices (B.1..B.N)

Mirroring Phase 1's A.1–A.7 cadence: each slice lands with
conformance tests and unit tests, on a small, reviewable diff.

### B.1 — Capture analysis in the type-checker

- Remove the "parent at package scope" reparenting in
  `checkFuncLit` (`pkg/types/check_func_lit.bn`). Use the
  enclosing scope as parent so Idents resolve naturally.
- New AST field `Decl.Captures @[]@CaptureInfo` (each: name,
  type, kind, source-scope depth — to distinguish "from outer
  function" vs "from outer block in same function").
- New checker pass `recordCapture(c, sym)` — invoked from
  `lookupSymbol` when the resolution crosses a function-scope
  boundary. Idempotent (first reference wins layout order).
- Type-check the body; capture list is finalized when the body
  exits.
- No type-checker escape rejection for capturing `*func` — see
  §"Allocation: raw vs managed forms" for the rationale, and B.5
  for the linter rule that replaces it.

Tests:
- conformance/3XX: basic by-value capture (read).
- conformance/3XX: makeCounter pattern via `@int` capture.
- conformance/3XX: writes inside closure don't escape.
- Unit tests: capture-info attached to Decl with right kinds.

### B.2 — Closure struct + dtor + call shim (non-managed only)

- New IR-gen pass: synthesize `__closure_<lit_id>`,
  `__dtor_<lit_id>`, `__shim_<lit_id>` for each capturing
  literal. (Apply only to literals with non-empty Captures;
  empty-Captures literals stay on the Phase 1 path.)
- Vtable instance now carries a real dtor pointer (vs Phase 1's
  null).
- Per-package state in `gen_func_lit.bn` extended to track the
  set of generated closure types (for dedup across multiple
  evaluations of the same literal).

Tests:
- conformance/3XX: `*func(...)` capturing only value-typed
  locals; verify result; verify dtor is invoked at scope exit.

### B.3 — Heap allocation for `@func(...)` capturing literals

- IR-gen: when the literal's destination type is `@func(...)`,
  emit a heap allocation of the closure struct and RefInc each
  captured `@T` field into it. (Stack alloc for `*func(...)`,
  per B.2.)
- Hook the existing managed-allocation refcount infrastructure
  (`gen_dtor*`, `RefDec`, `gen_util_refcount`) — the closure
  struct is just a regular managed allocation.

Tests:
- conformance/3XX: `@func` returned from a function, called
  after the caller's frame is gone, captures still live.
- conformance/3XX: managed-pointer capture; verify pointee
  outlives the closure correctly.
- conformance/3XX: dtor runs at refcount=0 (observable via a
  printf in a `@CapturedThing`'s dtor).

### B.4 — Method values `x.M`

- Type-checker: extend `checkSelectorExpr` to recognize a
  Selector whose X resolves to a value (not a SYM_TYPE) and
  whose member is a method — emit a function-value type without
  the receiver in Params.
- IR-gen: extend `gen_func_lit.bn` (or a sibling
  `gen_method_value.bn`) to synthesize closure / shim / dtor
  per method-value site. The closure struct holds exactly the
  receiver in the captured form per the table above.
- Three shim shapes (one per receiver-kind T / `*T` / `@T`),
  one per method.

Tests:
- conformance/3XX: method value with each receiver kind.
- conformance/3XX: method value passed as `*func(...)` arg.
- conformance/3XX: method value stored as `@func(...)`, escapes
  the constructing scope.
- conformance/3XX: error case `M` requires `@T` receiver but
  user has only `T` / `*T`.

### B.5 — Linter rules

- `*func(...)` capturing literal flowing into a slot that may
  outlive the enclosing frame (returned, assigned to a global,
  stored in an outliving struct field, etc.) → warn "capturing
  raw function value may escape its enclosing frame; consider
  `@func(...)` if the closure must outlive the frame." Best-
  effort detection on the obvious patterns; does not claim
  non-escape outside the warned cases.
- `@func(...)` capturing `*T` (any raw-pointer capture, including
  via the receiver of a method value) → warn "managed function
  value captures raw pointer; lifetime is the caller's
  responsibility."

Tests:
- `cmd/bnlint` test fixtures exercising each warn case.

### B.6 — Documentation + cleanup

- Update `claude-notes.md` §"Closures" with a forward reference
  to this plan.
- Update `plan-function-values.md` Phase 2 status block.
- Move retired open questions from plan-function-values.md
  §"Open questions" into the §"Decided design" sections of this
  doc (already done in this draft).

## Cross-references

- [plan-function-values.md](plan-function-values.md) — Phase 1
  substrate + Phase 3 trampolines. This plan slots between.
- [plan-call-indirect.md](plan-call-indirect.md) — IR primitive
  that vtable dispatch is built on.
- [claude-discussion-detailed-notes.md](claude-discussion-detailed-notes.md)
  §"Closures" — source of the by-value capture decision.
- [claude-notes.md](claude-notes.md) §"Function values" — high
  -level rationale.

## Open questions (consolidated)

None expected post-ratification. Items closed in this draft:

- ~~By-value vs by-reference~~ — by value (per design notes).
- ~~Mutability of captures from inside the closure~~ — writes
  are local to the closure's copy (corollary of by-value).
- ~~Lifetime extension for non-managed captures in `@func`~~ —
  value-typed: copy into struct; raw-pointer: as unsafe as the
  pointer (linter warning).
- ~~Type-system escape check for capturing `*func`~~ — no, raw
  is an opt-in escape hatch; linter rule replaces it (B.5).
  Parallel cleanup for raw slices tracked separately in
  `claude-todo.md`.
- ~~Receiver capture for method values~~ — table above.
- ~~`@func` capturing `*T` receiver~~ — allow + linter warning.
- ~~Vtable type identity across packages~~ — structural by
  signature, mangled-signature dedup.
- ~~Recursive lambdas~~ — confirmed NOT supported (same as Phase
  1); workaround documented.
- ~~Function value equality~~ — nil-only, mirrors interface
  values.

Anything that surfaces during implementation reopens here.
