# Plan: Function Values

> **Status: DRAFT** — initial sketch for review. Some details TBD;
> phasing and scope may shift before implementation begins.

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
- **`rt.CallDtor` retirement** — once Binate has callable function
  values, RefDec can call the dtor directly through a `func(*uint8)`
  value; the C-side helper goes away.
- **Method values** (`x.M`, `T.M`) — already deferred to function
  values per the existing TODO. They're function values whose
  closure captures the receiver.
- **Closures** — the bootstrap subset explicitly excludes them; full
  function values bring them in.
- **Higher-order helpers** in libraries (`slices.Map`, `Filter`,
  custom formatters, dispatch tables, etc.).

Because function values reuse the interface-value machinery (vtable
dispatch), they're *not* blocked on the interface frontend syntax
revision (`plan-interface-syntax-revision.md`). What they need is
the **backend**: vtable layout, per-(impl, interface) static-vtable
generation, and the cross-mode trampoline path. That backend work
serves both interfaces and function values.

## Representation

Two-word `{vtable, data}`, exactly the same shape as interface
values:

```
@func(int) int   // managed function value: (@data, vtable*)
*func(int) int   // raw function value:     (*data, vtable*)
```

- **`vtable`**: pointer to a per-(signature, capture-shape) static
  table. Contains:
  - `call`: pointer to a per-shape shim that takes `(data_ptr,
    args...)` → ret. Either a thin wrapper around the underlying
    function (non-capturing case), the closure body itself
    (capturing case), or a per-signature trampoline (VM case).
  - `dtor`: destructor for the captured-ctx struct, used when the
    function value's last reference drops. nil for non-capturing.
- **`data`**: the captured-ctx pointer. nil for non-capturing.
  Managed (refcounted) for `@func(...)`. Raw for `*func(...)`.

This is a structural function type. There is no user-declared
"function interface" — the type system synthesizes whatever
machinery a given signature needs.

### Bare `func(...)` is not a usable type

Following the same shift the slice migration made (`*[]T` / `@[]T`)
and the proposed interface syntax revision (`*Stringer` /
`@Stringer`), function types appear only with a raw or managed
prefix:

- `*func(int) int` — raw function value.
- `@func(int) int` — managed function value.

Bare `func(int) int` only exists as the inner part of those forms.
Reasoning is identical to the interface case: forcing the explicit
raw-vs-managed choice prevents the "I thought it was managed" UAF
class.

### Why this matches interface values exactly

A function value is morally `interface { Call(args...) → ret }` for
some specific signature. Two-word `{vtable, data}`. Single-method
"vtable" with a `call` slot (plus a `dtor` slot for the type-erased
data field). Identical runtime story.

The difference is at the **frontend**:

- User interfaces: declared (`interface Foo { ... }`), nominal
  identity, explicit `impl T : Foo`.
- Function values: structural (signature determines identity, no
  declaration), no `impl` syntax — the compiler synthesizes the
  necessary "impl" at function-literal sites and method-value
  sites.

Two type-system rules, one runtime. **The frontend syntax revision
for interfaces is independent — function values can land before
that work, as long as the backend vtable machinery is in place.**

## Per-shape shim (call slot)

The `call` slot in the vtable always has the shape
`func(data *uint8, args...) → ret`. The "data" parameter is the
captured-ctx pointer (or nil for non-capturing).

Per (function or method, capture-shape) pair, the compiler
generates a shim that adapts the underlying function/method body
to this uniform shape:

### Non-capturing: top-level function `f(a, b int) int`

```
__shim_f(data *uint8, a, b int) int { return f(a, b) }
```

vtable.call → `__shim_f`. data is always nil. The branch / extra
call is one cycle of overhead and avoids special-casing "data ==
nil" at call sites.

### Capturing closure: `func(a int) int { return a + captured }`

```
type __ctx_N struct { captured int }   // anonymous closure-struct
__call_N(data *uint8, a int) int {
    var ctx *__ctx_N = bit_cast(*__ctx_N, data)
    return a + ctx.captured
}
__dtor_N(ptr *uint8) { /* RefDec any managed fields in ctx */ }
```

vtable.call → `__call_N`. vtable.dtor → `__dtor_N`. data points at
the heap-allocated ctx (managed) or the stack-allocated ctx (raw).

### Method value: `f := v.M` where M is `func (r *T) M(x int) int`

The captured form mirrors the *actual* receiver type at the
capture site — not M's declared receiver type. Smoothing happens at
call time, against the captured ctx. Three cases:

**Captured form is `T` (value receiver, M wants `*T`)**:
```
type __ctx_M_T struct { recv T }
__call_M_T(data *uint8, x int) int {
    var ctx *__ctx_M_T = bit_cast(*__ctx_M_T, data)
    return M(&ctx.recv, x)   // address of stable in-ctx storage
}
__dtor_M_T(ptr *uint8) { /* dtor for T's managed fields */ }
```

**Captured form is `*T` (raw pointer receiver)**:
```
type __ctx_M_RawT struct { recv *T }
__call_M_RawT(data *uint8, x int) int {
    var ctx *__ctx_M_RawT = bit_cast(*__ctx_M_RawT, data)
    return M(ctx.recv, x)    // pass through; UAF if recv dangles
}
// __dtor_M_RawT not needed (no managed fields in ctx)
```

**Captured form is `@T` (managed receiver, M wants `*T` or `T`)**:
```
type __ctx_M_MgT struct { recv @T }
__call_M_MgT(data *uint8, x int) int {
    var ctx *__ctx_M_MgT = bit_cast(*__ctx_M_MgT, data)
    return M(ctx.recv, x)    // smoothing applies: @T → *T or T as needed
}
__dtor_M_MgT(ptr *uint8) { /* RefDec ctx.recv */ }
```

The shim machinery is exactly the per-(method, capture-shape)
generation pattern that monomorphized generics already use. No new
runtime concept needed.

## VM-side: per-signature trampoline

When a function value's `call` is implemented in interpreted
bytecode rather than compiled native code, vtable.call points at a
per-signature trampoline (a tiny compiled-native function) that:

1. Takes the standard `(data *uint8, args...)` shape.
2. Uses `data` as a reference to the bytecode record (function
   index + module + closure env) the VM should execute.
3. Sets up the VM call frame, marshals args into VM stack
   convention, runs the bytecode, marshals the return value back.
4. Returns to the compiled caller.

Per-signature: each distinct function-value signature needs its own
trampoline, because the arg marshaling depends on the types. The
compiler generates trampolines for every signature that crosses
the compiled-vs-interpreted boundary at any point in the program.

This is the same machinery the interface backend needs for
cross-mode method dispatch — when a compiled caller dispatches
through an interface vtable to a method whose impl is in interpreted
code, vtable.method[i] points at a trampoline for that method's
signature. Function values are the single-method case.

## Boxing / allocation rules

Mirrors the existing managed/raw interface boxing rule:

- **Non-capturing, raw or managed**: degenerate. data = nil. No
  allocation. vtable points at the per-(function, no-capture)
  static vtable.
- **Capturing, raw**: the closure-struct is stack-allocated at the
  function-literal expression. The function value's `data` points
  inside the enclosing stack frame. Lifetime is tied to that frame
  — same "caller keeps data alive" contract as raw slices and raw
  interface values.
- **Capturing, managed**: the closure-struct is heap-allocated
  (via `Alloc` / similar). The function value's `data` is the
  managed pointer; refcount is bumped on copy, decremented on
  drop. Survives past the function-literal's enclosing scope.
- **Method value, `*func(...)`**: receiver-capture struct is
  stack-allocated. Same caller-keeps-data-alive contract.
- **Method value, `@func(...)`**: receiver-capture struct is heap-
  allocated; the captured receiver's RefInc fires at capture time
  if the receiver was `@T`.

There is **no implicit conversion from `*func(...)` to
`@func(...)`** (you might not have a managed allocation behind the
raw form), matching the existing raw → managed escape-hatch rule.
The reverse (`@func → *func`) is fine via smoothing.

## Phasing

Three phases. Phase 1 is small enough to be a meaningful early
deliverable. Phases 2 and 3 are the substantive work.

### Phase 1: Non-capturing function values

- Function type syntax: `*func(...)` / `@func(...)`. Type checker
  accepts them.
- 2-word representation locked in.
- Vtable layout: `{call, dtor}` with `dtor = nil` for non-
  capturing.
- Per-function shim generation for top-level functions taken as
  function values (`f := SomeFunc`).
- `f(args)` desugars to vtable-indirect call.
- No closures, no method values.

**Unlocks**:

- `rt.CallDtor` retirement — RefDec calls the dtor through a
  `func(*uint8)` value directly; `bn_rt__CallDtor` and
  `runtime/rt_stubs.c` go away.
- The 2-word ABI commitment — once this lands, that shape is
  baked in across compiled and interpreted code.

### Phase 2: Closures + method values

- Capture analysis on function literals — identify which locals
  are referenced in the body and lift them into an anonymous
  closure-struct.
- Closure-struct dtor generation per (literal, capture-shape).
- Method-value receiver capture per the actual-receiver-type
  model in this plan.
- Heap-vs-stack allocation per the boxing rules.

**Unlocks**:

- General-purpose closures and lambdas.
- Method values, both as receiver-bound (`x.M`) and as method
  expressions (`T.M` — receiver as first arg).
- Higher-order library helpers.

### Phase 3: Cross-mode trampolines

- Per-signature trampoline generation for every function-value
  signature that crosses the compiled / interpreted boundary.
- Bytecode-record reference layout for VM-side function values.
- Integration with the interface backend's cross-mode dispatch (if
  the interface backend isn't already handling this — they'd share
  the same trampoline machinery).

**Unlocks**:

- Compiler/interpreter interop for function-value-passing across
  the boundary.
- Package descriptors for the broader interop work — fall out as
  "struct of function values per export."

## Backend dependency on the interface plan

Function values share the vtable layout and dispatch path with
interfaces. **They depend on the runtime / codegen vtable
machinery, not on the frontend interface syntax**. Specifically:

- Vtable layout (a static struct of function pointers + dtor) —
  shared.
- Per-(impl, interface) static-vtable generation — same machinery
  generates per-(function, capture-shape) static vtables.
- Vtable-indirect call sequence in the compiler / VM — shared.
- Cross-mode trampoline path — shared.

The frontend interface syntax revision (`plan-interface-syntax-
revision.md`) decides **how users write interfaces**. That can land
in any order relative to function values. What both plans need is
the backend.

## Cross-references

- `plan-interface-syntax-revision.md` — sibling plan; orthogonal at
  the frontend, paired at the backend.
- `claude-todo.md` § "Function values" — current TODO entry; will
  be replaced by a pointer to this plan.
- `claude-todo.md` § "Retire `rt.CallDtor`" — direct dependent of
  Phase 1.
- `claude-todo.md` § "Compiler/interpreter interop — MAJOR PROJECT"
  — depends on Phase 3 (and provides the broader context for why
  function values matter).
- `claude-notes.md` § "Function values" — high-level rationale (will
  cross-link here when this plan ratifies).

## Open questions

- **Vtable layout details**: just `{call, dtor}` or are there fields
  the future interface backend will want here too (type info,
  method table size, etc.)? Probably bare-minimum for function
  values; expand only when interfaces force it.
- **Equality / comparison**: are function values comparable? Go
  says yes-for-nil, no-otherwise. Probably mirror that.
- **`nil` function value**: layout and zero-value semantics —
  vtable = nil, data = nil; calling a nil function value panics.
- **Closure capture: by-reference vs. by-value for raw types**.
  Default is by-value (copy at capture time). Capturing a local int
  by reference would require lifting the int into a heap-allocated
  cell, which feels surprising for raw types. Probably: managed
  types capture by-managed-ref (RefInc); raw types capture by-
  value. Worth pinning when Phase 2 starts.
- **Mutability of captured locals from inside the closure**: Go
  allows mutation of captured locals through closures (because they
  capture by reference). Binate may prefer the more restrictive
  "captures are immutable inside the closure" rule for raw types.
  TBD.
- **Recursive function values**: `var f @func(int) int = func(x int)
  int { return f(x-1) }` — does `f` see itself in the closure?
  Probably needs a fixpoint construction (Y-combinator pattern) or
  a top-level named recursive function. TBD if generally supported.
