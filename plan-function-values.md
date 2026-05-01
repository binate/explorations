# Plan: Function Values

> **Status: Phase 1 IN PROGRESS** — A.1–A.5 landed (type syntax,
> nil + zero-init, function-reference-as-value, calling through a
> function value, flow through args/returns/fields). A.6 (method
> expressions) and A.7 (non-capturing function literals) remain.
> See "Phase 1 progress" subsection for details.
>
> `rt.CallDtor` retirement landed independently via the
> `_call_dtor` / `_call_free_fn` magic-symbol path on top of
> `OP_CALL_INDIRECT` (so it didn't need to wait for function
> values). Phase 2 (closures + method values) remains DEFERRABLE
> with substantial open questions on capture design.

## Why this is more important than it looks

Function values were initially framed as a "language feature we'll
get to eventually." They aren't — they're the **core mechanism for
compiler/interpreter interop**. Data already interops fine via
shared layout (the `.bni` contract). What needs to cross the
boundary at runtime are *exported functions and methods passed as
values* — and the only way to call a function across the
compiled-vs-interpreted divide uniformly is through a function-
value-shaped indirection. The "package descriptor" the interop
work needs is, structurally, just a struct of function values per
exported symbol.

That promotes function values to an upstream prerequisite for:

- **Compiler/interpreter interop** (the broader MAJOR PROJECT).
- **Method values** (`x.M`, `T.M`) — they're function values whose
  closure captures the receiver.
- **Closures** — the bootstrap subset explicitly excludes them; full
  function values bring them in.
- **Higher-order helpers** in libraries (`slices.Map`, `Filter`,
  custom formatters, dispatch tables, etc.).

`rt.CallDtor` retirement is *not* part of this plan — it lands
ahead of Phase 1 via the `OP_CALL_INDIRECT` IR op (see
`plan-call-indirect.md`). That op also turns out to be the
primitive Phase 1's vtable-indirect call sequence is built on,
so it's upstream of this plan in both senses.

## Phasing — what each phase actually delivers

The split into phases matters because **most current goals are
satisfied by Phase 1 alone**. Phase 2 (closures + method values)
can be deferred for as long as we don't need user-written closures
or method values in self-hosted code.

### Phase 1 — Backend vtable machinery + non-capturing function values

This is the load-bearing phase. It is much more about *building the
shared interface/vtable backend* than it is about "non-capturing
function values" — that feature happens to be the smallest user-
visible thing the backend can deliver.

What lands:

- **Vtable type generation** per signature (one vtable type per
  distinct function-type signature in use across the program).
- **Vtable instance generation** per (function, capture-shape) —
  for non-capturing function values this is per (taken-as-value
  function), since capture-shape is degenerate.
- **The `call` slot mechanism** in vtable types, and the per-shape
  shim function the slot points at.
- **Vtable indirect-call sequence** in the compiler and the VM.
- **Type-syntax** for function values: `*func(...)` raw and
  `@func(...)` managed (mirroring `*[]T` / `@[]T`). Bare `func(...)`
  is not a usable type. (Consistent with `plan-interface-syntax-
  revision.md`.)
- **Function literals as expressions** — restricted to non-
  capturing literals in this phase (i.e., the literal references
  no enclosing scope's locals).
- **`f(args)` desugaring** to vtable-indirect call.

The same vtable machinery is exactly what user-declared interfaces
need at the runtime layer. Phase 1 builds the backend once; the
interface frontend and function-value frontend both consume it.

What's enabled by Phase 1:

- Function values for top-level functions: `f := SomeFunc`.
- Method *expressions*: `T.M` (no receiver bound — receiver
  becomes the first arg). Equivalent to a non-capturing function
  reference.
- The cross-mode trampoline machinery (Phase 3) builds on these
  vtables — laying the groundwork now means cross-mode work in
  Phase 3 is "synthesize a per-signature trampoline and put it in
  the vtable" rather than "redesign the dispatch path."

What's NOT enabled by Phase 1:

- Closures (no capture analysis yet).
- Method values `x.M` (need receiver capture).
- Higher-order user code that wants to construct closures.

#### Phase 1 progress (status as of 2026-04-30)

Sliced internally as A.1–A.7. Conformance tests cover each landed
slice (338+).

- **A.1 — Type syntax** (`*func(...)`, `@func(...)`): LANDED.
  Parser, type-checker, and pkg/types kinds (TYP_FUNC_VALUE /
  TYP_MANAGED_FUNC_VALUE) accept the new syntax. Bare `func(...)`
  remains non-usable as a type.
- **A.2 — Nil + zero-init**: LANDED. `IsNillable` extended,
  zero-init alloca emits the all-zeros 16-byte function value,
  conformance test 338.
- **A.3 — Function reference as value** (`var f *func(...) = SomeFunc`):
  LANDED. IR-gen detects function-reference-to-function-value-type
  in var-decl, emits OP_FUNC_VALUE. Per-function `weak_odr` vtable
  globals in `%BnVtable = type { i8*, i8* }`. VM-mode vtables use
  Option 3 (heap-allocated VM closure record always; lazy on
  first BC_FUNC_VALUE). Conformance test 339.
- **A.4 — Calling through a function value** (`f(args)`):
  LANDED. New OP_CALL_FUNC_VALUE; LLVM extracts vtable_ptr →
  vtable.call → indirect call. VM short-circuits the trampoline:
  reads closure_rec.vm_func_idx and execFunc directly. Native
  arm64 backend extracts vtable.call into X17 and BLR's it.
  Conformance test 340. macho linker alignment quirk fixed in a
  separate companion commit (`pkg/asm/macho`: 8-byte align section
  addrs and file offsets).
- **A.5 — Function values flow through args / returns / fields**:
  LANDED (2026-04-30). Function-reference-to-function-value
  coercion at every IR-gen site where a bare function name may
  legitimately flow into a function-value-typed slot: call
  arguments, return values, single-assign Ident/Selector LHS,
  composite-literal struct fields. Reordered genCall dispatch so a
  Selector/Index callee of function-value type takes the
  function-value path before method-call dispatch. Conformance
  test 341. Followed by a unit-test backfill (pkg/ir,
  pkg/types) and a file split (`pkg/ir/gen_call.bn` extracted
  from `gen_expr.bn` to bring the latter back under the 600-line
  ERROR limit; tests follow the same boundary in
  `gen_call_test.bn`).
- **A.6 — Method expressions** (`T.M`): NOT STARTED. Receiver
  becomes the first arg; equivalent to a non-capturing function
  reference. Should be a small slice on top of A.5.
- **A.7 — Function literals as expressions** (non-capturing only):
  NOT STARTED. Larger: parser support for `func(...) ... { ... }`
  in expression position, lifting to module scope with a synthetic
  name, then reducing to a function reference at the call site.

### Phase 2 — Closures + method values (DEFERRABLE)

Adds capture analysis, closure-struct generation, and receiver-
capture for method values.

**This phase is deferrable.** The compiler and self-hosted runtime
do not currently need to write closures. CallDtor retirement does
not need it (alternative path discussed below). The interop
descriptor does not need it (descriptor fields are non-capturing
function values). Phase 2 is the user-facing "you can write
closures" feature; it can wait until there's a concrete need.

The capture design itself is **substantially open**. See "Capture
design — open" below.

### Phase 3 — Cross-mode trampolines

Phase 1 already lays an interop-compatible vtable layout (see
"VM-mode vtables — interop-compatible from Phase 1" below) with
the `vtable.call` slot left as a placeholder for VM-side values.
Phase 3's job is to fill it in: generate per-signature trampolines
(part of cmd/bni's compiled body or per-program codegen) that
read the VM closure record and dispatch into the VM via
`execFunc`. Once `vtable.call` is populated, compiled code can
read it and call directly — cross-mode dispatch just works.

The reverse direction (VM bytecode calling a compiled-side
function value through `vtable.call`) needs a way to invoke an
arbitrary C function pointer with prepared args from bytecode.
This is the longer Phase 3 work — same machinery the broader
compiler/interpreter interop will use.

Phase 3 does not require Phase 2: package descriptors expose
non-capturing exports. Closures-across-the-boundary is a separate
question (probably wants Phase 2 in some form, but isn't on the
critical path for the current interop goals).

## Representation

Two-word `{vtable, data}`, exactly the same shape as interface
values:

```
@func(int) int   // managed function value: (@data, vtable*)
*func(int) int   // raw function value:     (*data, vtable*)
```

- **`vtable`**: pointer to a static vtable instance.
- **`data`**: the captured-ctx pointer. nil for non-capturing.
  Managed (refcounted) for `@func(...)`. Raw for `*func(...)`.

### Vtable types vs vtable instances

These were conflated in the previous draft; calling out the
distinction explicitly:

- **Vtable type** is the static *layout* shared by all vtable
  instances for a given function signature. One vtable type per
  signature. Defined once and referred to by pointer.
- **Vtable instance** is a populated static global of that type.
  One vtable instance per *(function, capture-shape)* pair, since
  the `call` slot depends on which function's body to execute and
  the `dtor` slot depends on the capture struct's destructor.

For example, `func(int) int` is one vtable type. The instances are
one per (top-level function `Foo` taken as a value, no captures),
plus one per (closure literal at source location L, capture shape
S). Many function values can share the same vtable instance —
e.g., every site that does `f := Foo` shares one instance.

### Vtable layout — dtor first

The vtable type for a function-value signature `func(args...) →
ret` looks like:

```
struct __vt_<sig> {
    dtor   *(...)           // signature: func(*uint8) — the data dtor
    call   *(...)           // signature: func(*uint8, args...) → ret
}
```

**dtor is always the first slot**, matching the layout convention
for all other vtables (interface vtables have it in the same
position). This lets a generic "drop a vtable-using value"
sequence load the dtor without knowing the rest of the vtable's
shape — useful for type-erased holders and for common destructor
emission.

For non-capturing function-value vtable instances, `dtor` is nil
(no captured ctx to clean up).

### Per-shape `call` shim

The `call` slot has the uniform shape `func(data *uint8, args...)
→ ret`. The data pointer is the captured ctx, or nil for non-
capturing. Per (function or method, capture-shape) pair, the
compiler generates a shim that adapts the underlying body to this
uniform shape.

#### Non-capturing: top-level function `f(a, b int) int`

Two implementations on the table:

1. **Always-shim**: emit a per-function shim that ignores its data
   arg and tail-calls the real function:
   ```
   __shim_f(data *uint8, a, b int) int { return f(a, b) }
   ```
   Vtable.call → shim. Caller always does `vtable.call(value.data,
   args...)` — uniform code path, no branch.
2. **Check-data-nil** *(default)*: caller branches on
   `value.data == nil`; if nil, calls the real function directly
   with `args...`; otherwise calls through the shim. Skips the
   shim hop entirely for the common non-capturing case.

**Default: check-data-nil.** Two reasons: (a) consistency — the
codebase already uses explicit nil checks at refcounted-pointer
call sites (`if ptr == nil { return }` in RefInc/RefDec/Free, etc.)
rather than dispatching through shim functions, and adopting an
always-shim mode for function values would be inconsistent with
that; (b) modern branch predictors handle the predictable nil/
non-nil split well, so the perf argument that originally motivated
"always-shim, no branch" doesn't really hold. The shim still
exists for the capturing case (where its body actually does
something useful with `data`), but non-capturing call sites
short-circuit it.

#### Capturing closures and method values

See "Capture design — open" below.

### VM-mode vtables — interop-compatible from Phase 1

A late-Phase-1 design pass: vtable layout must be interoperable —
same 16-byte shape in both modes, with `vtable.call` holding a real
C function pointer in both — otherwise the compiled side cannot
call a VM-side function value through its vtable, and Phase 3
cross-mode dispatch has to redesign the layout instead of just
filling in a slot.

In compiled mode the design is straightforward: `vtable.call` is a
real function pointer to the actual function (or to a per-shape
shim for capturing closures). Compiled callers dispatch directly.

VM mode is harder. User-Binate functions in VM mode don't have
real C function pointers — they exist only as bytecode. Three
options were considered:

1. **Per-function JIT trampolines**: at VM startup, allocate
   executable memory and emit a tiny native stub per function-
   taken-as-value with the function index baked in. Heaviest
   (mmap MAP_EXEC, sysctl exec-on-write on macOS, ARM cache
   flush, etc.).
2. **Shared per-signature trampoline + data-slot-as-context**:
   one generic trampoline per call signature, part of cmd/bni's
   compiled body. The function value's `data` slot holds the VM
   function index instead of a captured-ctx pointer. Phase 1 fit
   is OK (non-capturing `data` is otherwise unused), but Phase 2
   conflicts: capturing values need `data` for the captured ctx,
   so the slot ends up doing two jobs.
3. **Heap-allocated VM closure record always** *(chosen)*:

       VM closure record (heap):
           {vm_func_idx, captured_ctx_or_nil}

   Even non-capturing VM-side values allocate one of these. The
   value's `data` slot points at the record. Phase 2 just extends
   the record's `captured_ctx` slot. Layout is uniform across
   capturing/non-capturing.

#### Phase 1 implementation (within-VM dispatch only)

For Phase 1, only same-mode dispatch is supported:

- Compiled-mode call site reads `vtable.call` directly and
  dispatches.
- VM-mode call site (a bytecode `OP_CALL_INDIRECT` through a
  function value) **short-circuits the trampoline**: the VM lowering
  reads the closure record's `vm_func_idx` directly out of `data`
  and calls `execFunc(vm_func_idx, args...)`. Cheap and direct.

`vtable.call` for VM-side function values stays a placeholder
(`null` or an abort stub). **Phase 1 must add code-level TODOs at
each VM-vtable emission site pointing at this plan**, so the
Phase 3 work is easy to find.

#### Phase 3 implementation (cross-mode dispatch)

Phase 3 fills in `vtable.call` for VM-side values with a per-
signature trampoline, generated as part of cmd/bni's compiled body
or via a per-program codegen step. The trampoline takes
`(data_ptr, args...)`, reads `data_ptr → vm_func_idx`, dispatches
into the VM via `execFunc`. With `vtable.call` filled in,
compiled code can read it and call directly — cross-mode dispatch
just works.

The reverse direction (VM-bytecode calling a compiled-side
function value through `vtable.call`) needs a way for bytecode to
invoke an arbitrary C function pointer with prepared args. This
is the longer Phase 3 work — same machinery the broader
compiler/interpreter interop will use.

### Bare `func(...)` is not a usable type

Following the same shift the slice migration made (`*[]T` /
`@[]T`) and the proposed interface syntax revision, function types
appear only with a raw or managed prefix:

- `*func(int) int` — raw function value.
- `@func(int) int` — managed function value.

Bare `func(int) int` only exists as the inner part of those forms.
Forces the explicit raw-vs-managed choice.

## Capture design — open

This section is a placeholder, **not a design**. Capture mechanics
are non-trivial and need their own design pass before Phase 2
implementation. Open questions:

- **By-value vs by-reference for captured locals**: Go captures
  by reference. C++ has both with explicit syntax. We may want
  by-value for raw types (to avoid surprises) and by-managed-ref
  for managed types. To be designed.
- **Mutability of captured locals from inside the closure**: do
  closures see *the* local (so writes from the closure are visible
  outside) or a snapshot (so writes don't escape)? Depends on the
  by-value-vs-reference decision.
- **Lifetime extension**: a managed function value extending the
  lifetime of captured `@T` is straightforward (RefInc the @T into
  the closure struct). For captured locals that aren't `@T` (raw
  scalars, value structs), what's the lifetime story for the
  managed function-value form? Boxed copies? Stack-allocated?
- **Closure-struct dtor generation**: per (literal, capture-shape)
  the compiler generates a dtor that walks the captured @T fields
  and RefDecs them. Implementation work but well-understood.
- **Receiver capture for method values**: parallel design — the
  captured form mirrors the actual receiver shape at the capture
  site, with smoothing applied at call time. Three cases (T,
  `*T`, `@T`) each generate a different shim. The semantics for
  the value-receiver case (capture creates a stable in-ctx copy,
  whose address is taken at call time when M wants `*T`) are
  reasonably clear; the others are less so.
- **`@func(...)` capturing `*T` receiver**: an unsafe combination
  — the function value can outlive the raw pointer it captured.
  Two stances:
  1. Reject at type check. Semantically clean: managed function
     values can't capture raw lifetimes. Surprising in practice
     because the user has to convert to managed first.
  2. Allow with a linter warning. Matches the existing escape-
     hatch policy for raw pointers. A managed function value with
     a raw-pointer capture is exactly as unsafe as the underlying
     raw pointer; the user opts in.
  
  Lean toward (2) — consistent with how we treat raw pointers
  elsewhere — but mark as open.

The plan above commits to the *representation* (2-word
`{vtable, data}` with per-shape vtable instances) but not the
*capture semantics*. Capture design is a follow-up to be done
before Phase 2 starts. Phase 1 only needs non-capturing function
values, where capture design is moot (`data = nil`,
`vtable.dtor = nil`, no captured ctx).

## Recursion — start by NOT supporting Go-style

Go allows closures to refer to themselves through the var being
assigned: `var f = func(x int) int { ... f(x-1) ... }`. The trick
relies on Go's "closure captures by reference" semantics — the
closure body sees the *current value* of `f`, which after the
assignment is the closure itself.

**Binate Phase 1 should NOT support this.** Reasons:

- Capture semantics aren't designed yet (Phase 2 territory). Go's
  trick depends on a specific capture rule we haven't decided.
- Recursive lambdas are rare; named recursive top-level functions
  are common and unaffected.
- The Y-combinator workaround (pass the function as an argument
  to itself) exists if someone really needs anonymous recursion.
- Easier to add later than to take away.

Documented stance: **anonymous recursive lambdas are not
supported.** Top-level named recursive functions work normally
(they reference themselves by name, not through capture). If
recursive anonymous closures become important, revisit when Phase 2
capture semantics are settled.

## VM-side: per-signature trampoline

Folded into the "VM-mode vtables — interop-compatible from Phase 1"
section above. Summary: Phase 3 work is to fill in `vtable.call`
for VM-side function values with a per-signature trampoline that
reads the VM closure record and dispatches into the VM. Phase 1
leaves the slot as a placeholder; same-mode dispatch in Phase 1
short-circuits the trampoline by reading the closure record
directly from the function value's `data` slot.

## Boxing / allocation rules

Mirrors the managed/raw rules for slices and (proposed) interface
values:

- **Non-capturing, raw or managed**: degenerate. data = nil. No
  allocation. vtable points at the static (function, no-capture)
  vtable instance.
- **Capturing, raw**: stack-allocated closure struct. Lifetime
  tied to the enclosing scope.
- **Capturing, managed**: heap-allocated closure struct, refcount
  bumped on copy, decremented on drop.
- **`*func → @func` is NOT auto**: matches the existing rule that
  raw cannot auto-promote to managed. The reverse (`@func →
  *func`) is fine via smoothing.

(Full rules are part of the Phase 2 capture design.)

## Relationship to `rt.CallDtor` retirement and `OP_CALL_INDIRECT`

`rt.CallDtor` retirement landed via a separate, lighter-weight
path: the `OP_CALL_INDIRECT` IR op (see `plan-call-indirect.md`,
status LANDED). RefDec calls a compiler-internal helper
`_call_dtor` whose `.bni` declaration is just a type-checking
shape; IR-gen recognizes the symbol and emits `OP_CALL_INDIRECT`.
`rt.CallDtor` and `runtime/rt_stubs.c` are gone.

For this plan, the relevant takeaway is:

1. The IR op already exists with LLVM, VM, and native-arm64
   lowerings, and is exercised end-to-end by RefDec's dtor
   dispatch.
2. Function values can be built *on top of* `OP_CALL_INDIRECT` —
   the compiled-side `call` slot in a vtable becomes "load the
   slot, OP_CALL_INDIRECT through it." Phase 1's vtable indirect-
   call sequence is just specialized OP_CALL_INDIRECT.

The substrate is in place; Phase 1 is unblocked on this front.

## Backend dependency on the interface plan

Function values share the vtable layout and dispatch path with
interfaces. **They depend on the runtime/codegen vtable
machinery, not on the frontend interface syntax**
(`plan-interface-syntax-revision.md`). The interface frontend
revision can land in any order relative to function values.

Specifically, what's shared:

- Vtable layout (dtor-first slot convention, static-struct
  representation).
- Per-(impl, interface) and per-(function, capture-shape) static
  vtable instance generation — same machinery, two consumers.
- Vtable-indirect call sequence in the compiler / VM.
- Cross-mode trampoline path.

What's not shared:

- Frontend syntax (`*Stringer` / `@Stringer` declared interfaces
  vs. `*func(...)` / `@func(...)` structural function types).
- Type-checker rules (interfaces are nominal-with-explicit-impl;
  function types are structural).

## Cross-references

- `plan-call-indirect.md` — upstream prerequisite. Defines the
  `OP_CALL_INDIRECT` IR op that this plan's vtable-indirect call
  sequence is built on. Lands first; retires `rt.CallDtor` as
  its first concrete consumer.
- `plan-interface-syntax-revision.md` — sibling plan for the
  interface frontend. Orthogonal at the frontend, paired at the
  backend.
- `claude-todo.md` § "Function values — MAJOR PROJECT" — current
  TODO entry pointing at this plan.
- `claude-todo.md` § "Retire `rt.CallDtor`" — direct dependent
  of `plan-call-indirect.md`, not this plan.
- `claude-todo.md` § "Free-function pointer in managed-allocation
  header — bug" — separate runtime bug; would also benefit from
  the `OP_CALL_INDIRECT` IR op (since `header[1]` is a callable
  pointer with a known signature).
- `claude-todo.md` § "Compiler/interpreter interop — MAJOR PROJECT"
  — depends on Phase 3 of this plan.
- `claude-notes.md` § "Function values" — high-level rationale
  (will cross-link here when this plan ratifies).

## Open questions (consolidated)

- **Capture design** (the whole "Capture design — open" section).
- **Recursive lambdas**: confirmed NOT supported in Phase 1; revisit
  for Phase 2.
- **`@func(...)` capturing `*T` receiver**: lean toward "allow +
  linter warning" but pin down before Phase 2.
- ~~**Always-shim vs check-data-nil for non-capturing call sites**~~
  — DECIDED: check-data-nil. Consistent with other nil-check sites
  in the codebase; branch predictors handle the split fine.
- ~~**CallDtor retirement path**~~ — DECIDED: separate plan
  (`plan-call-indirect.md`). The `OP_CALL_INDIRECT` IR op also
  serves as Phase 1's foundation.
- **Vtable type identity across packages**: two
  `*func(int) int` from different packages must use the same
  vtable type (or a structurally compatible one) — pin down the
  mangling / canonicalization rule.
- **Function value equality / nil**: probably mirror Go (compare-
  to-nil yes; structural comparison no).
