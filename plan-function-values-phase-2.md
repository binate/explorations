# Plan: Function Values — Phase 2 (Closures + Method Values)

> **Status: LANDED (2026-05-31; cleanup miscompile fixed 2026-06-01)**
> — B.1..B.6 all implemented.
> Predecessor: [plan-function-values.md](plan-function-values.md)
> §"Phase 2 — Closures + method values (DEFERRABLE)" + §"Capture
> design — open". This document closed the open design questions
> and sliced the implementation into B.1..B.6.  Phase 1 (A.1–A.7)
> landed 2026-05-01; Phase 2 closed in a sequence of cherry-picks
> through 2026-05-31.  See §"Implementation slices" for
> per-slice landing pointers and known follow-ups.
>
> Post-B.6 follow-up (2026-06-01): the @func vtable's slot-0 dtor
> pointer was a raw fn pointer, which made
> `rt.ZeroRefDestroy`'s `_call_dtor` (lowered as OP_CALL_HANDLE)
> byte-pun-read the dtor function's machine code as a `{vtable,
> data}` struct.  arm32-baremetal hung; LP64/aa64/x64 exited
> cleanly via a random jump but the closure-struct dtor never ran
> (captured `@T` / `@[]T` references leaked on every target).
> Fixed by emitting a per-dtor `(shim, vt, handle)` triple and
> storing the HANDLE POINTER in vtable[0] (`67952cf1`).  Same
> shape applied to `@__ivt[0]` for the iface side (`dc46ac7f`) —
> a latent twin of the same bug, masked in conformance/370 by
> caller-side RefInc but exposed by the new
> `conformance/520_iface_dtor_callee_sole_ref`.

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

### B.1 — Capture analysis in the type-checker — **LANDED**

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
- conformance/501_capture_value (basic CAP_VAL + CAP_RAW_PTR).
- Unit tests in `pkg/types/check_capture_test.bn` and
  `pkg/types/check_func_lit_test.bn`.

### B.2 — Closure struct + dtor + call shim (non-managed only) — **LANDED**

- IR-gen synthesises `__closure_<lit_id>`, the closure-struct
  type whose fields back the captures; per-shape `__shim` in
  each backend reads them out of the data pointer before
  tail-calling the underlying.
- Captures are prepended to the lifted body's params so the
  body resolves their names through the normal local-name
  mechanism (no special capture-load IR ops needed).
- Latent bug discovered and fixed mid-B.3 work: the type-
  checker's view of `*Box` is `TYP_POINTER → TYP_NAMED("Box")`
  but IR-gen's view is `TYP_POINTER → TYP_STRUCT`; the closure
  body's `p.N` selector missed the `isRawPtrToStruct` branch
  and fell through to the `EmitConstInt(0, …)` fallback.  Fix:
  `resolveCaptureTypes` now looks up via the IR-gen scope so
  prepended-capture params land shape-identical to non-captured
  ones (binate `359c128c`).

Tests:
- conformance/501_capture_value, 508_capture_ptr_field (by-value
  struct field-read regression).

### B.3 — Heap allocation for `@func(...)` capturing literals — **LANDED**

Split into two commits:

- **B.3a** (binate `3e0d00c5`): managed captures for `*func`.
  Each `CAP_MANAGED` capture RefInc's at the literal site;
  closure struct is registered in `moduleStructs` so
  `generateDtors` synthesises a struct-dtor that RefDec's each
  managed field; closure-local registered in `ctx.Vars` so the
  frame-end `emitDecForManagedLocals` invokes the dtor.
- **B.3b commit 1** (binate `da6eb9df`): type-checker context
  propagation via `Checker.ExpectedFVType` hint, so a literal
  flowing into an `@func()` VarDecl slot resolves as
  `TYP_MANAGED_FUNC_VALUE` instead of the default `*func`.
- **B.3b commit 2** (binate `1c09b410`): IR-gen heap-allocates
  the closure struct (`EmitMake` instead of `EmitAlloc`) when
  the resolved type is `@func`; new `OP_FUNC_VALUE_DTOR` op +
  `emitManagedFuncValueRefDec` drive the scope-end RefDec on
  an `@func` variable's data slot; backend populates the
  vtable's dtor slot with the closure-struct dtor symbol.

Native-shim follow-ups in the same series:
- Stack-spill for SysV-AMD64 aggregate captures (binate
  `85b37efa`).
- Stack-spill for aa64 outgoing user-args (binate `fe33556e`).
- Indirect-large pass-by-pointer (binate `40615a69`) once both
  ABIs flipped to `IndirectLargeAggregates = true` in
  `f5340fac`.

CRITICAL pre-existing bug discovered + fixed mid-B.3b: `Type.
Identical` had no `TYP_FUNC_VALUE` / `TYP_MANAGED_FUNC_VALUE`
branch, so any two same-kind func-values compared identical —
silent wrong-code on signature mismatch.  Fixed by folding the
existing `TYP_FUNC` branch with the value variants into a
single structural compare (binate `12bbb548`).

Tests:
- conformance/509_capture_managed, 513_managed_func_value_
  noncapture, 515_managed_func_value_capture (the arm32-baremetal
  xfail on 515 was lifted 2026-06-01 once the slot-0 raw-fn-ptr
  miscompile was fixed; see post-B.6 follow-up at the top).
- conformance/510_capture_managed_slice, 517_capture_split_
  aggregate, 518_capture_small_aggregate (native shim coverage).
- Unit tests in `pkg/binate/types/types_query_test.bn`
  (`TestIdenticalFuncValues`).

### B.4 — Method values `x.M` — **LANDED**

- **First cut** (binate, included in the B.4 series): same-shape
  receiver only — wrapper Func synthesised per (method,
  receiver-shape) with `IsClosure=true` so the B.2 closure
  machinery handles the per-shape shim.  Tests 503 / 505 / 506.
- **@T receiver** (binate `131a00b0`): drops the
  `isManagedReceiverType` gate, RefInc's the receiver, registers
  the closure struct + closure-local for cleanup.  Also fixes a
  latent naming bug — `methodValueWrapperName` carries dots which
  `generateDtors` Pass 2 treats as a package qualifier;
  `buildMethodValueClosureStruct` now folds dots to underscores
  up front.
- **Cross-shape smoothing** (binate `973d3ccd`): closure-struct
  field, wrapper receiver param, and dedup key now reflect the
  CAPTURED form (method's declared receiver type), not x's type.
  `genCapturedRecv` bridges x → captured form: T→*T (address-of),
  *T/@T→T (deref), @T→*T (bitcast), and the identical / fall-
  through cases.

Tests:
- conformance/503_method_value, 505_method_value_val,
  506_method_value_void, 511_method_value_managed,
  519_method_value_smoothing (all 7 smoothing shapes covered).

### B.5 — Linter rules — **LANDED**

(binate `10d19369`, `8ae97c74`).  Two new rules in
`pkg/binate/lint/func_value_escape.bn`:

- `func-value-escape` — capturing `*func(...)` returned from
  its enclosing function fires (closure struct is stack-bound
  to the returning frame; captures will dangle).  Gated on
  `TYP_FUNC_VALUE` so the `@func` path stays silent.
  Currently dispatched from return statements only; non-return
  escape paths (global assign, struct field, etc.) are
  documented as "best effort" in the plan and can land as
  follow-up.
- `managed-func-raw-capture` — `@func(...)` capturing a
  `CAP_RAW_PTR` fires per capture.  Walks via
  `walkExprFuncLits` / `walkStmtFuncLits` so nested literals
  are reached.

Tests: 7 unit tests in `pkg/binate/lint/func_value_escape_
test.bn`, covering positives + negatives for both rules and
the nested-literal walker.

### B.6 — Documentation + cleanup — **LANDED**

This commit.  Plan doc + cross-references updated; per-slice
landing pointers recorded above.  Open follow-ups left in
`claude-todo.md` (>5-incoming-user-arg shim spill on both native
backends).  The arm32 @func cleanup hang was tracked here as a
follow-up at landing time and root-caused + fixed 2026-06-01
(binate `67952cf1` + `dc46ac7f`); see the status block at the
top of this doc.

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
