# Binate — Phase 6: Language Feature Completion

This plan covers implementing the major language features that are designed but not yet implemented. Each feature must work in **both** the bootstrap interpreter (Go) and the self-hosted compiler (LLVM backend). Some features also need support in the self-hosted interpreter.

Reference documents:
- `claude-plan-3.md` — Phase 5b (compiler, current)
- `claude-plan-2.md` — Phase 5a (self-hosted interpreter)
- `claude-plan-1.md` — Phases 1–4 (language design, bootstrap)
- `claude-notes.md` — design decisions
- `grammar.ebnf` — formal grammar with `[BOOTSTRAP]`/`[DEFERRED]` annotations

---

## Feature Priority

Features are ordered by dependency and value. Later features often depend on earlier ones.

```
Tier 1 — Enables self-hosting and real programs:
  1. Methods and receivers
  2. Interfaces and impl
  3. Function values and closures

Tier 2 — Ergonomics and correctness:
  4. Memory management (refcounting)
  5. Variadic functions
  6. Float types

Tier 3 — Power features:
  7. Generics
  8. Annotations
  9. REPL / interactive mode
```

---

## 1. Methods and Receivers

**What:** Functions with receiver parameters, dispatched via `.` syntax.

```binate
type Point struct { x int; y int }

func (p *Point) translate(dx int, dy int) {
    p.x = p.x + dx
    p.y = p.y + dy
}

func (p *const Point) magnitude() int {
    return p.x * p.x + p.y * p.y
}
```

**Design (from claude-notes.md):**
- Receiver is the first parameter, declared in `(recv Type)` syntax before the function name
- One method per name per base type (no overloading on receiver kind)
- Auto-deref: `@T`/`*T` → look for methods on pointer type and on `T`
- Value receivers implemented as `*const T` (never null)

**Implementation — all three backends:**

### Bootstrap interpreter (Go)
- Parser: already handles receiver syntax (or needs minor addition)
- Type checker: register methods in a method table keyed by `(baseType, methodName)`
- Interpreter: on `expr.method(args)`, look up method, bind receiver, call

### Self-hosted interpreter
- Same as bootstrap — method table lookup at runtime

### Compiler (IR + LLVM)
- Methods are just functions with a mangled name: `Point.translate` → `bn_Point_translate`
- Receiver is the first parameter
- Method calls desugar to: evaluate receiver, pass as first arg to mangled function
- Auto-deref: if receiver is `@T`, emit deref to get `*T` before passing

**Conformance tests needed:**
- Method call on pointer receiver
- Method call on value (auto address-of)
- Method call on managed pointer (auto-deref)
- Method with return value
- Method modifying receiver fields

---

## 2. Interfaces and impl

**What:** Structural subtyping with explicit `impl` declarations and vtable-based dispatch.

```binate
type Stringer interface {
    string() *[]char
}

impl *Point : Stringer

func (p *Point) string() *[]char {
    return "Point"
}

func printIt(s Stringer) {
    println(s.string())
}
```

**Design (from claude-notes.md):**
- `impl *T : Interface` declares that `*T` satisfies `Interface`
- Vtable layout: `[any vtable][embed1 vtable][embed2 vtable][own methods]`
- Interface value is a fat pointer: `{ data_ptr, vtable_ptr }`
- `any` is the implicit base interface (all types implement it)
- Interface embedding: `type ReadWriter interface { Reader; Writer }`
- Child → parent conversion: adjust vtable pointer by fixed offset

**Dependencies:** Methods (feature 1) must be implemented first.

**Implementation — all three backends:**

### Bootstrap interpreter (Go)
- Parser: interface declarations, impl declarations
- Type checker: verify impl satisfies interface, build vtable layout
- Interpreter: interface values carry `{ value, concrete_type }` pair; method dispatch looks up in vtable

### Compiler (IR + LLVM)
- New IR ops: `OP_IFACE_MAKE` (wrap concrete → interface), `OP_IFACE_CALL` (virtual dispatch)
- Interface value in LLVM: `{ i8*, i8* }` (data pointer + vtable pointer)
- Vtable: global constant array of function pointers
- Virtual call: load function pointer from vtable, call indirectly

**This is the largest single feature.** Consider splitting into:
- 2a: Interface declarations + type checking
- 2b: `impl` validation
- 2c: Interface values and virtual dispatch
- 2d: Interface embedding

---

## 3. Function Values and Closures

**What:** Functions as first-class values, with optional capture.

```binate
func apply(f func(int) int, x int) int {
    return f(x)
}

func main() {
    var double func(int) int = func(x int) int { return x * 2 }
    println(apply(double, 5))  // 10

    var factor int = 3
    var triple func(int) int = func(x int) int { return x * factor }
    println(apply(triple, 5))  // 15
}
```

**Design (from claude-notes.md):**
- Always capture by value
- Use managed pointers for shared mutable state
- Function type: `func(params) results`

**Implementation:**

### Bootstrap interpreter (Go)
- Parser: function literal expressions (may already be partially parsed)
- Interpreter: closure value captures environment snapshot at creation time

### Compiler (IR + LLVM)
- Non-capturing function values: just a function pointer
- Capturing closures: `{ func_ptr, env_ptr }` pair
  - `env_ptr` points to a heap-allocated struct containing captured variables
  - Closure body receives `env_ptr` as hidden first parameter
- Indirect call: `OP_CALL_INDIRECT` — call through function pointer
- LLVM: `call void %fptr(i8* %env, ...)` or similar

**This enables:** Higher-order functions, callbacks, iterators, strategy patterns.

---

## 4. Memory Management

**What:** Transition from "leak everything" to proper reference counting.

See `claude-plan-3.md` Step 12 for the detailed plan. Summary:

**Phase 1 (current):** Leak everything.
**Phase 2:** Refcounting.
- Allocation layout: `[refcount (i64) | free_fn_ptr | payload]`
- Managed pointer points to payload
- `OP_REFCOUNT_INC` on new references
- `OP_REFCOUNT_DEC` on scope exit / overwrite; free when zero
- Must handle: function params, return values, struct fields, slice elements

**Phase 3:** Refcount elision via escape analysis.

**Both compiler and interpreter need this.** The interpreter already has a conceptual model for refcounting (in the design docs) but currently relies on Go's GC. The compiler needs explicit inc/dec emission.

**Key challenge:** Correct insertion of inc/dec around all control flow paths (early return, break, continue, panic). Consider generating cleanup blocks in the IR.

---

## 5. Variadic Functions

**What:** Functions accepting a variable number of arguments.

```binate
func sum(nums ...int) int {
    var total int = 0
    for n in nums {
        total = total + n
    }
    return total
}

println(sum(1, 2, 3))  // 6
```

**Design (from grammar.ebnf):**
- `...T` in last parameter position
- Caller passes args as a slice
- Callee receives `*[]T`
- Spread: `f(slice...)` forwards a slice as variadic args

**Implementation:**
- Parser: `...` in parameter list
- Type checker: variadic parameter, argument count validation
- IR gen: collect variadic args into a slice, pass as single `*[]T` argument
- This is mostly sugar — the IR and LLVM see a normal slice parameter

---

## 6. Float Types

**What:** `float32` and `float64` support.

```binate
var x float64 = 3.14
var y float32 = cast(float32, x)
```

**Implementation touches everything:**
- Lexer: float literal tokenization (already partially in grammar)
- Parser: float literal AST nodes
- Type checker: float types, numeric conversions
- Interpreter: float value representation and operations
- IR: float operations (fadd, fsub, fmul, fdiv, fcmp)
- LLVM: `float` and `double` types, `fadd`/`fsub`/etc. instructions
- Runtime: `bn_print_float32`, `bn_print_float64`

**Defer until needed.** The compiler and interpreter don't need floats for self-hosting.

---

## 7. Generics

**What:** Type-parameterized functions and structs.

```binate
type List[T any] struct {
    items *[]T
    len   int
}

func [T any] newList() @List[T] {
    return make(List[T])
}

func [T Comparable] contains(items *[]T, target T) bool {
    for item in items {
        if item == target { return true }
    }
    return false
}
```

**Design (from claude-notes.md):**
- Monomorphized: each instantiation generates specialized code
- No type inference — always explicit: `contains[int](nums, 5)`
- Constraints via interfaces
- Generic bodies in `.bni` files (needed for cross-package instantiation)

**This is a very large feature.** Implementation phases:
1. Generic functions (monomorphization at IR gen time)
2. Generic structs
3. Constraint checking
4. Cross-package generics (requires .bni changes)

**Defer until after interfaces work.** Constraints depend on interfaces.

---

## 8. Annotations

**What:** Metadata attached to declarations.

```binate
#[packed, align(4)]
type NetworkPacket struct { ... }

#[inline]
func fastPath() { ... }

#[deprecated("use newFunc instead")]
func oldFunc() { ... }
```

**Design (from grammar.ebnf):**
- `#[name]` or `#[name(args)]` syntax
- Namespaced: `compiler.inline`, `tool.export`
- No stacking — comma-separated within one `#[...]`

**Implementation:**
- Parser: parse annotations, attach to declarations
- Store in AST nodes
- Compiler: honor `packed`, `align`, `inline` during codegen
- Tools: `deprecated`, `export`, custom annotations for tooling

**Lower priority.** Nice to have but not blocking for self-hosting.

---

## 9. REPL / Interactive Mode

**What:** An interactive interpreter for exploratory programming.

```
$ binate repl
>>> var x int = 42
>>> println(x * 2)
84
>>> func double(n int) int { return n * 2 }
>>> println(double(x))
84
```

**Design considerations:**
- "Deferred execution" — statements entered at the REPL execute immediately in a persistent environment
- Must handle: incremental declarations, re-definitions, expression evaluation
- State persists across lines (variables, functions, types)
- Multi-line input (detect incomplete expressions/blocks)

**Implementation:**
- Extend the interpreter with an incremental evaluation mode
- Parser needs a "statement-at-a-time" mode (not just full-file parsing)
- Environment persists between inputs
- Error recovery: bad input shouldn't destroy the session

**This is a user-facing tool feature, not a language feature.** Can be built any time after the interpreter is solid.

---

## Cross-Cutting Concerns

### Every feature needs support in three places:

| Layer | Bootstrap (Go) | Self-hosted interp | Compiler (LLVM) |
|-------|----------------|-------------------|-----------------|
| Lexer | Go code | Binate code | Shared with interp |
| Parser | Go code | Binate code | Shared with interp |
| Type checker | Go code | Binate code | Shared with interp |
| Execution | Go interpreter | Binate interpreter | IR gen + LLVM emit |

The self-hosted interpreter and compiler share the frontend (lexer, parser, types). Only the backend differs. So each feature is roughly:
1. Add to Go bootstrap frontend + interpreter
2. Port frontend changes to self-hosted Binate packages
3. Add IR generation + LLVM emission for the compiler

### Testing strategy:
- Each feature gets conformance tests
- Tests run on all three backends (bootstrap, selfhost, compiled)
- Add unit tests for new parser/checker/IR gen logic

---

## Bootstrapping Milestones

These are key validation points on the path to full self-hosting:

### Milestone A: Compiler on self-hosted interpreter
Run `compile.bn` via `main.bn` (self-hosted interpreter running on Go bootstrap) to compile a test program. Validates that the interpreter can run the compiler.

**Status:** Should work now for single-file programs. Try it.

### Milestone B: Compile the self-hosted interpreter
Use `compile.bn` to compile `main.bn` + all its packages to a native binary. This requires multi-package compilation support.

**Requires:** Step 11 (compiler ergonomics), multi-package IR gen, bootstrap package runtime functions in C.

### Milestone C: Compiler compiles itself
The native interpreter (from B) runs `compile.bn` to compile `compile.bn` itself to native code. This is full self-hosting for the compiler.

**Requires:** Everything above, plus the compiled binary must be able to handle the same input as the interpreted version.

### Milestone D: Drop the Go bootstrap
The native toolchain (interpreter + compiler) is the primary development environment. Go bootstrap is only needed for initial bootstrap from scratch.

---

## Implementation Order (Suggested)

```
Near-term (current phase):
  □ Compiler ergonomics (auto-clang, -o flag)          — plan-3 step 11
  □ Conformance test gaps                               — plan-3 step 13
  □ Milestone A: compiler on self-hosted interpreter    — validation
  □ Memory management (refcounting)                     — plan-3 step 12

Medium-term:
  □ Methods and receivers                               — feature 1
  □ Interfaces and impl                                 — feature 2
  □ Multi-package compilation                           — plan-3 step 14
  □ Milestone B: compile the interpreter                — validation

Longer-term:
  □ Function values and closures                        — feature 3
  □ Variadic functions                                  — feature 5
  □ Milestone C: compiler compiles itself               — validation
  □ REPL / interactive mode                             — feature 9

Future:
  □ Float types                                         — feature 6
  □ Generics                                            — feature 7
  □ Annotations                                         — feature 8
  □ Milestone D: drop Go bootstrap                      — validation
```

---

## Current Status

| Feature | Designed | Bootstrap | Self-hosted | Compiler | Tests |
|---------|----------|-----------|-------------|----------|-------|
| Methods | Yes | No | No | No | No |
| Interfaces | Yes | No | No | No | No |
| Closures | Yes | No | No | No | No |
| Refcounting | Yes | No (uses Go GC) | No | No (leaks) | No |
| Variadic | Yes | No | No | No | No |
| Floats | Yes | No | No | No | No |
| Generics | Yes | No | No | No | No |
| Annotations | Yes | No | No | No | No |
| REPL | Partial | No | No | N/A | No |
