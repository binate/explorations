# Plan: Function Values

> **Status: DRAFT** — substantial open questions, especially around
> capture design, recursion, and whether `rt.CallDtor` retirement
> should ride this plan or take an independent IR-level path. See
> "Open questions" section. Phasing and scope likely to shift
> before implementation begins.

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

Per-signature trampolines that let compiled code call into VM
bytecode through a function value, and vice versa. Builds on
Phase 1's vtable layout.

Unlocks the broader compiler/interpreter interop work — package
descriptors fall out as "structs of function values whose vtables
either point at compiled code (compiled side) or trampolines
(interpreted side)."

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

Phase 3. When a function value's underlying body is interpreted
bytecode rather than compiled native code, the vtable instance's
`call` slot points at a per-signature **trampoline**:

1. Takes the standard `(data *uint8, args...)` shape.
2. Uses `data` as a reference to the bytecode record (function
   index + module + closure env) the VM should execute.
3. Sets up the VM call frame, marshals args into VM-stack
   convention, runs the bytecode, marshals the return value back.

Per signature: each distinct function-value signature needs its
own trampoline because arg marshaling depends on types. Generated
once per signature in use, not per function.

Trampolines live in the compiled binary (they're compiled native
code) regardless of the called body's location.

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

`rt.CallDtor` retirement is *not* part of this plan. It lands
ahead of Phase 1 via a separate, lighter-weight path: an
`OP_CALL_INDIRECT` IR op that takes a raw function pointer +
args and lowers to a native indirect call (compiled) or VM-
function-index dispatch (VM). RefDec uses the IR op directly;
`rt.CallDtor` and `runtime/rt_stubs.c` retire.

That work has its own plan: `plan-call-indirect.md`. It's
upstream of this plan in two ways:

1. The IR op retires `rt.CallDtor` without waiting for Phase 1.
2. Function values can be built *on top of* `OP_CALL_INDIRECT` —
   the compiled-side `call` slot in a vtable becomes "load the
   slot, OP_CALL_INDIRECT through it." Phase 1's vtable indirect-
   call sequence is just specialized OP_CALL_INDIRECT.

So `OP_CALL_INDIRECT` lands first as a stand-alone primitive,
retires CallDtor as its first concrete consumer, and serves as
the substrate Phase 1 builds on.

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
