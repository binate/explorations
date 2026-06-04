# Binate — Phase 6: Language Feature Completion

Status: LARGELY SHIPPED. This was a high-level roadmap for the major
language features that were designed but not yet implemented. Most of the
Tier 1/Tier 2 features below — methods, interfaces, function values/closures,
refcounting, variadics, generics — have since shipped, each with its own
dedicated `plan-*.md` doc and `claude-notes.md` section, which are the
authoritative records. This doc is kept for the design rationale on the
still-unbuilt items (floats, annotations, REPL) and the dependency ordering.

Reference documents:
- `claude-plan-3.md` — Phase 5b (compiler)
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

## Shipped features (see dedicated docs)

The following are implemented; their authoritative design records are in
`claude-notes.md` and the per-feature `plan-*.md` docs. Retained here only as
the design pointers that motivated the ordering:

- **1. Methods and receivers** — receiver is the first parameter, declared in
  `(recv Type)` before the function name; one method per name per base type
  (no overloading on receiver kind); auto-deref so `@T`/`*T` look for methods
  on the pointer type and on `T`; value receivers are proper by-value passing
  (revised 2026-05-14; see claude-notes.md § "Method resolution & dispatch").
- **2. Interfaces and impl** — structural subtyping with explicit `impl T : Interface`
  declarations and vtable-based dispatch. Vtable layout
  `[any vtable][embed1 vtable][embed2 vtable][own methods]`; interface value is a
  fat pointer `{ data_ptr, vtable_ptr }`; `any` is the implicit base interface
  (all types implement it); interface embedding `type ReadWriter interface { Reader; Writer }`;
  child → parent conversion adjusts the vtable pointer by a fixed offset.
  Depends on methods (feature 1).
- **3. Function values and closures** — functions as first-class values, with
  optional capture. Always capture by value; use managed pointers for shared
  mutable state. Non-capturing function value is just a function pointer;
  capturing closure is `{ func_ptr, env_ptr }` where `env_ptr` points to a
  heap-allocated struct of captured variables, passed as a hidden first parameter.
- **4. Memory management (refcounting)** — see `claude-plan-3.md` Step 12.
  Allocation layout `[refcount | free_fn_ptr | payload]`, managed pointer points
  to payload; inc on new references, dec on scope exit / overwrite, free at zero.
  Key challenge was correct inc/dec insertion around all control-flow paths
  (early return, break, continue, panic).
- **5. Variadic functions** — `...T` in last parameter position; caller passes
  args as a slice, callee receives `*[]T`; spread `f(slice...)` forwards a slice.
  Mostly sugar — IR/LLVM see a normal slice parameter.
- **7. Generics** — monomorphized (each instantiation generates specialized code);
  no type inference, always explicit (`contains[int](nums, 5)`); constraints via
  interfaces; generic bodies in `.bni` files for cross-package instantiation.
  Depended on interfaces (constraints).

---

## Still-unbuilt features

### 6. Float Types

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

### 8. Annotations

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

### 9. REPL / Interactive Mode

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
