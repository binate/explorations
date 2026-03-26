# Binate Detailed Design Discussion Notes

This document captures the full discussion of Binate's design, including rationales for decisions, alternatives considered and why they were rejected, open questions, and the reasoning that led to the current design.

---

## 1. Memory Model

### Decision: Reference Counting

The central question was: manual (C-style), ownership/borrowing (Rust-style), or GC?

**Why refcounting was chosen:**
- GC was ruled out immediately — it's non-deterministic and too heavy for the target platforms (small 32-bit systems, kernel work). Deterministic cleanup is essential.
- Ownership/borrowing (Rust-style) was rejected for complexity reasons. Rust's borrow checker is powerful but adds significant complexity to both the language and the learning curve. This conflicts directly with the "simple and approachable" goal.
- Pure manual (C-style malloc/free) was considered — simplest to implement and understand, but too error-prone (use-after-free, double-free, leaks).
- Refcounting was chosen as the sweet spot: automatic enough for ergonomics (the majority of code can use managed memory without manual free), deterministic (cleanup happens immediately when refcount hits zero), and simple to understand.

**Trade-offs explicitly accepted:**
- **Cycles can leak.** No automatic cycle detection. This is the classic refcounting weakness, but the design provides escape hatches (raw pointers as unowned references — see below). Objective-C lived with this for years.
- **Refcount overhead.** Every copy/assignment bumps a counter. For hot paths, the escape hatch is raw structs/pointers with no refcounting.
- **No weak references as a first-class concept.** Instead, raw pointers to managed structs serve as the cycle-breaking mechanism. This is unsafe (dangling possible) but simple — one concept (raw pointers) serves double duty instead of introducing a separate weak-ref type.

### Two Worlds: Managed and Raw

**Managed structs** carry a refcount plus management info (how to free them, possibly custom allocator info). The management info is embedded in the struct itself, which has an important benefit for dual-mode interop: a compiled function can hand a managed struct to the interpreter (or vice versa), and the recipient knows how to release it without any special knowledge. This is reminiscent of COM's IUnknown or Objective-C's object model.

**Raw structs** have no overhead — just data. Manual lifetime management, like C.

**Raw pointers can point to managed structs.** This was initially considered as "raw pointers only to raw structs" but was revised. Allowing raw pointers to managed structs provides the cycle-breaking escape hatch — it's essentially a weak/unowned reference without safety nets. The programmer is responsible for ensuring the managed struct hasn't been freed.

### Drop Semantics

When a managed struct's refcount hits zero, it **recursively releases all managed fields** — decrementing their refcounts, which may trigger further drops. This gives deterministic, cascading cleanup. There was no real alternative considered here; it's the natural and expected behavior.

### Dual-Mode Benefit

The management info embedded in managed structs is critical for the dual-mode story. An object carries its own cleanup semantics, so neither compiled nor interpreted code needs special knowledge about objects created by the other mode. This was identified as one of the design choices that makes seamless interop possible without marshalling.

---

## 2. Value Types vs. Reference Types

### Decision

**Value types**: integers, floats, pointers (including managed pointers), raw structs, tuples, fixed-size arrays. Copied on assignment/pass. Live on the stack or inline within other structs.

**Reference types**: managed structs. Heap-allocated, refcounted, accessed via managed pointers.

**Key insight: a managed pointer is itself a value type.** It's a small, copyable thing. Copying it bumps the refcount of the thing it points to — "special semantics" but still a value type. This keeps the mental model clean.

### Struct Definition Style

We considered two approaches for how struct definitions relate to managed/raw:

1. **Auto-generate managed and raw versions** of every struct definition.
2. **C/C++ approach**: the unadorned struct type is always the value/raw type. To get a refcounted heap instance, put it behind a managed pointer.

**Chose option 2** — it's simpler, more familiar, and is one concept rather than two. You define a struct, it's a value type. Want it managed? Use a managed pointer.

### Struct Fields

A struct field that is itself a struct must be either:
- An inline raw struct (embedded in the parent's memory layout)
- A managed pointer to a managed struct (a pointer-sized value type field)

You do NOT embed a managed struct inline — you always go through a managed pointer.

### Interior Pointers

You can take a raw pointer to a field within any struct (managed or raw). For managed structs, this is dangerous — the managed struct could be freed while the interior pointer is live. This has the same risk profile as raw-pointer-to-managed-struct and is accepted under the "trade safety for power and simplicity" philosophy.

---

## 3. Arrays, Strings, and Slices

### The Evolution of This Design

This area went through several iterations during discussion.

**Initial approach**: two dimensions (raw vs. non-raw, fixed-size vs. unspecified-size), with non-raw arrays carrying their length inline. This led to the question of "wasting" a word for the known length when inlining a non-raw `char[123]` in a struct.

**The key realization**: the length doesn't need to live in the data. It can live in a **slice/view** — a (pointer, length) pair. This led to the current design.

### Slices as the Unspecified-Size Type

`char[]` is not a container — it's a **slice/view** into underlying data. This is semi-first-class: largely syntactic sugar over (pointer, length) pairs, but integrated enough to feel natural.

**Two flavors of slice:**

**Managed slice** — three words:
1. Managed pointer to the underlying allocation (keeps it alive via refcounting)
2. Raw pointer to the start of the view (for direct data access, no arithmetic needed)
3. Length

The three-word representation was chosen over (managed pointer, offset, length) because the raw pointer gives direct data access without arithmetic, and is what you'd pass to C code or use in tight loops.

**Raw slice** — two words:
1. Raw pointer to start
2. Length

**Constraint: managed slices can only refer to managed allocations.** For stack or static data, use a raw slice. If you need to pass stack/static data where a managed slice is expected, copy into a managed allocation first.

This constraint was debated. The alternative was allowing managed slices with a nil managed pointer for non-managed data, but this was rejected as deceptive — you'd lose the lifetime guarantees that are the whole point of managed slices.

**API semantics**: the choice of managed vs. raw slice at function boundaries communicates intent:
- Managed slice = "I will retain this data"
- Raw slice = "I just need to read it now"

This falls out naturally from the type system with no extra annotations needed.

### Alternatives Considered for Length Storage

1. **Length in the data** (non-raw arrays carry length inline): works but wastes a word for inlined fixed-size arrays.
2. **Fat pointers** (length in the pointer): this is essentially what slices are, but was initially deferred as adding complexity. Later embraced as the right design.
3. **Length-prefixed data** (like Pascal strings): considered briefly. Would work well for managed structs (length alongside refcount in the header) but not for raw/stack data. Rejected in favor of slices.

### Strings

**No explicit `string` type.** Strings are just char arrays/slices. Rationale: the language targets small systems where full UTF-8 support is too heavy, so distinguishing strings from char arrays adds a concept without sufficient benefit.

**String literals are always null-terminated.** One byte of overhead, but avoids the "is this null-terminated?" ambiguity. Any string literal is safe to pass to C without copying. This was chosen over:
- Never null-terminated (clean but every C FFI call needs conversion)
- Dual literal syntax (`"abc"` vs `c"abc"`) — cheap to support but always-null-terminated is simpler and less annoying long-term

**String literals are raw static data in the binary** (like C). The compiler generates wrapping code based on context:
- Assigned to a raw slice → slice pointing into static data, no allocation
- Assigned to a managed array → compiler generates allocation + copy code
- Used as a raw pointer → pointer to static data

String literals are **untyped** (like integer literals) and coerce to the appropriate type from context.

### Fixed-Size Arrays

Fixed-size arrays (e.g., `char[123]`) are value types. They don't store their length — the compiler knows it statically. When you create a slice from one, the length is captured in the slice.

### Move/Ownership Optimizations

Discussed briefly but deferred. The compiler could detect last-use of a managed pointer and skip refcount bumps/decrements. This is a pure optimization that doesn't change semantics.

---

## 4. Type System

### Decision: Static Typing, Same in Both Modes

The language is statically typed. Compiled and interpreted modes use the **exact same type system and rules**. The only difference is *when* checks run — the compiler checks everything upfront, while the interpreter (especially in REPL mode) may defer some checks.

This is important for the dual-mode interop story: if the type systems diverged, you'd need marshalling or type-conversion at the boundary, breaking the "seamless" promise.

### Type Conversions and Literals

**Explicit casts required** between named types (Go-style). No implicit conversions between e.g. `int` and `uint`.

**Untyped literals**: literals have no inherent type and coerce to any compatible type from context. This applies ONLY to literals, not to named constants (unlike Go, which has untyped constants). Examples:
- `123` can be `int`, `uint`, `i32`, `byte`, etc.
- `3.14` can be `f32`, `f64`, etc.
- `"abc"` can be various char array/slice types

This was chosen because Go's untyped constants, while useful, add complexity. Limiting it to literals is simpler and covers the most common ergonomic needs.

### Generics — Reconsidered

**Originally punted for v1**, but reconsidered after discussing `void*`/`any` and the boxing problem.

**The chain of reasoning that brought generics back:**

1. With explicit interface implementation (not duck typing), the language loses Go's `any`/`interface{}` — you can't have every type implicitly satisfy the empty interface. The `any` interface (implicitly implemented by all types) was introduced to solve this.

2. But even with `any`, a generic linked list of integers would require **boxing** every integer — heap-allocating each one with refcount and management info. For a systems language targeting small platforms, this overhead (memory and performance) is unacceptable.

3. This is exactly why generics exist: `List[int]` stores the int inline in the node, not boxed.

**Decision: include generics, but in the simplest form possible.**

- **Generic types AND functions.** Generic functions are needed alongside generic types — you can't write `sort` without them.
- **Interface constraints on type parameters.** Type parameters are constrained by interfaces: `func sort[T Comparable](items []T)`. The compiler checks the generic function body against the interface — it can only call methods the constraint guarantees.
- **No type inference for generics.** Always spell out type parameters: `sort[int](myArray)`. This simplifies the compiler and keeps code explicit. Can be relaxed in v2.
- **Monomorphized.** `List[int]` generates specialized code with int stored inline. Trade-off: more code generated (potential code bloat on small systems), but data is stored efficiently.
- **No trait bounds, no where clauses, no higher-kinded types.** Just "this type/function is parameterized by types that must satisfy certain interfaces."

**Alternatives considered:**
- **Type erasure with size info**: container knows element size, does memcpy-style operations. Less code bloat but less type-safe internally. Rejected for now.
- **C-style preprocessor macros**: solves code generation but ugly and error-prone. Not considered seriously.
- **No generics + boxing**: the overhead of boxing every value in a container is too high for a systems language.

### Boxing Value Types

`make(T)` (or similar) as the standard way to box a value type into a managed allocation. The result is a managed pointer to a heap-allocated copy carrying refcount + management info. This is explicit — you choose when to box.

Boxing is still needed even with generics (for `any`-typed containers, dynamic dispatch scenarios), but generics eliminate the need for boxing in the common case of type-safe containers.

### Sum Types

**Not included.** Full sum types (arbitrary `A | B | C`) add too much complexity: type narrowing, exhaustiveness checking, memory layout (different variant sizes), and inference complications. This was a deliberate choice aligned with the simplicity goal.

Tagged unions (defined in one place, fixed variants) were identified as a simpler alternative that provides most of the practical benefit. These are punted for v1 but desirable.

### Null/Optionality

**v1: all pointers nullable by default** (C-style). This is the simple, familiar approach.

**Future: non-nullable pointer types** via a `!` annotation (e.g., `!*MyStruct` or `*MyStruct!` — exact syntax TBD). This would require definite-initialization analysis in the compiler.

**Critical design constraint for v1**: don't make choices that block adding non-nullability later:
- Don't assume every type has a zero value in core semantics
- Don't design initialization rules that conflict with future definite-initialization analysis
- Ensure null checks (`if p != nil`) are clean and expressible

The reasoning is that non-nullability is valuable but adds compiler complexity (dataflow analysis for definite initialization). It's better to add it carefully in a later version than to rush it.

---

## 5. Enums

### Decision: No First-Class Enums — Use `type` + `const` + `iota`

First-class enums were initially designed with an explicit underlying integer type: `enum Opcode uint8 { ... }`. However, when aligning enum syntax with the `type` keyword (for consistency with `type Name struct { ... }` and `type Name interface { ... }`), a fundamental problem emerged: **enum values need a namespace**.

If `type Color enum uint8 { Red, Green, Blue }` parallels other `type` declarations, then `enum uint8 { Red, Green, Blue }` would be the anonymous form. But where do `Red`, `Green`, `Blue` live? In the enclosing scope (C-style, name pollution) or scoped to the type (`Color.Red`)? Anonymous enums would either leak values into the enclosing scope or be inaccessible.

**Options considered:**
1. Always scoped (`Color.Red`) — clean but anonymous enums are weird
2. Always flat (C-style) — name collision risk
3. Named scoped, anonymous flat — inconsistent
4. No anonymous enums — breaks the struct/interface parallel
5. Flat by default, qualified access also works — ambiguous: what type is `Red`?

**Decision: drop first-class enums entirely.** Use Go's approach:

```
type Opcode uint8

const (
    OpAdd Opcode = iota    // 0
    OpSub                   // 1
    OpMul                   // 2
)
```

`iota` is a predeclared constant equal to the zero-based index within a `const (...)` block. Within a grouped const block, omitting type and expression repeats the previous spec with `iota` incremented.

**Why this is sufficient:**
- Distinct type provides type safety (requires cast)
- `iota` eliminates numbering boilerplate
- `1 << iota` handles bit flags naturally
- No new language concept — uses existing `type` + `const`
- Exhaustiveness checking can be a linter concern

**What we lose:**
- No auto-increment syntax beyond `iota` patterns
- No exhaustiveness checking (even linter-level is harder without declared value sets)
- No scoped access (`Opcode.OpAdd`)

**Discriminated/tagged unions**: punted for v1 but desirable.

---

## 6. Interfaces

### Decision: Explicit Declaration and Implementation (Revised)

We considered four approaches:

1. **Go-style (structural, implicit)**: if a type has the right methods, it satisfies the interface automatically. Simple and flexible, but satisfaction can be accidental and refactoring can silently break it.

2. **Rust-style (nominal, explicit `impl`)**: clear intent, no accidents, but verbose. Orphan rules (can't impl a trait for a type you don't own) add complexity.

3. **C-style manual vtables**: maximum power, zero magic, but extremely verbose and error-prone.

4. **Minimal type constraints**: not full interfaces, just "this type must have field X." Less powerful but simple.

**Chose a hybrid**: explicit interfaces with explicit, separate `impl` declarations and Go-style receiver syntax for methods defined outside the impl blocks. This gives:
- Clear intent (no accidental satisfaction)
- Flexibility (methods can be defined across files, not tied to impl blocks)
- Clean integration with the package interface file system

### Evolution of the `impl` Model

The design went through several iterations:

**First iteration**: `impl` embedded in struct definition: `struct FileHandle impl Writer { ... }`. Simple, but limiting — can't add interface implementations for builtin types or types from other packages.

**Second iteration**: separate `impl` declarations with methods inside the impl block. But this ties methods to impl blocks, limiting flexibility.

**Final design**: `impl` declarations are purely relational (just declaring that a type implements an interface), and methods are defined separately with Go-style receiver syntax. This is the most flexible:

```
interface Writer {
    write(buf char[]) int
    close()
}

struct FileHandle {
    fd int
}

impl *FileHandle : Writer

func (f *FileHandle) write(buf []char) int { ... }
func (f *FileHandle) close() { ... }
```

This separation was driven by several realizations:
- **Value types need to implement interfaces.** If `int` should implement `Comparable`, you can't embed `impl` in a struct definition because there's no struct definition for builtins.
- **Go's lesson**: Go allows value types to implement interfaces, and having methods on both `T` and `*T` is valuable. The impl model needs to support this.
- **Receiver type matters for the impl.** The impl declaration specifies what receiver types can satisfy the interface — see "Five Receiver Kinds" below.

### The `any` Interface

**`any` is implicitly implemented by all types.** This is part of a small, closed, language-defined set of built-in implicit interfaces. You can't require every type (including builtins) to declare `impl any`. Others may be added by the language spec (e.g., `Sized` for generics), but user-defined interfaces are always explicit. This framing is more honest than calling `any` a unique exception.

`any` provides the type-erasure mechanism that Go gets from `interface{}`. A managed or raw pointer to `any` serves as the equivalent of `void*` — see section on Untyped Pointers.

The need for `any` was identified when discussing `void*` equivalents: without Go's structural typing (where every type implicitly satisfies the empty interface), there's no way to have a "pointer to anything" without an explicit special case.

### Five Receiver Kinds

Discussion about const-ness (see Const-ness section) led to identifying five receiver kinds for methods:

1. **const value** — read-only copy (most restrictive)
2. **const raw pointer** — read-only view, no refcount
3. **const managed pointer** — read-only view, with refcount
4. **raw pointer** — mutable, no refcount
5. **managed pointer** — mutable, with refcount

Value receivers are always const — mutating a copy is pointless, so we only allow const value receivers.

**Auto-conversion at call sites** follows the safe direction only (more-permissive → more-restrictive):
- managed → raw → const raw
- managed → const managed → const raw
- any pointer → value/const value (by copying the data out)
- Cannot auto-promote raw → managed (you might not have a managed allocation)

**Impl declarations specify receiver type.** This determines what kinds of pointers/values can satisfy the interface.

### Const-ness and Interfaces

A key design question was whether interfaces should specify const-ness on their methods. The answer: **no**. Const-ness is a consequence of the `impl`, not a constraint on the interface.

- `interface Stringer { toString() char[] }` — says nothing about const
- `impl Stringer for FileHandle` with a const receiver → `const *FileHandle` satisfies `Stringer`
- `impl Stringer for Widget` with a mutable receiver → only `*Widget` satisfies `Stringer`, not `const *Widget`

This means the same interface can be implemented with different receiver kinds by different types — some types genuinely need mutation to implement an operation, others don't. The caller finds out at the point of use whether their const pointer is sufficient.

This is elegant because it requires no extra syntax in interface declarations, and it falls out naturally from the type system.

### Interface Extension

Supported. An interface can extend one or more other interfaces. More important than embedding multiple interfaces is the ability to "extend" them — building richer interfaces from simpler ones.

### Vtable-Based Dynamic Dispatch

Interface values follow the managed/raw pattern:
- **Raw interface value** (e.g., `Stringer`): (raw ptr to data, vtable ptr) — no refcounting, temporary use
- **Managed interface value** (e.g., `@Stringer`): (managed ptr to data, vtable ptr) — keeps data alive

Both are value types. The compiler may devirtualize as an optimization.

### Connection to Package Interface Files

Interface files can contain forward declarations of interfaces, types, impl declarations, and method signatures without bodies. This enables binary-only library distribution and is critical for the interpreter's ability to call compiled code.

---

## 7. Syntax

### General Decisions

**C-family, leaning Go**: clean, minimal, familiar. Avoids Rust-level syntactic complexity.

- **Type-after-name declarations** (`x int` not `int x`): more natural, especially for complex types.
- **`:=` short declarations**: supported for ergonomics.
- **No semicolons**: automatic insertion (like Go).
- **Multiple return values**: Go-style, not first-class tuples.

**First-class tuples were considered then dropped.** They're elegant in theory ("all functions are unary") but raise many type system questions (is `(int)` the same as `int`? named fields? nesting?) for limited practical benefit. Go-style multiple returns cover 99% of use cases.

### Pointer Syntax

The most consequential syntax decision. The question: how to distinguish raw from managed pointers?

**Options considered:**
1. `*T` raw, `@T` managed — respects C conventions, `@` is visually distinct
2. `*T` managed (common case gets short syntax), new sigil for raw
3. Both get new sigils, no bare `*`
4. Keyword-based (`raw *T`, `managed *T`)

**Chose option 1: `*T` raw, `@T` managed.** Rationale:
- `*T` has strong "raw pointer" connotations from C — the target audience (systems programmers) expects this
- `@` is visually distinct and reads as "managed reference at"
- Both are one character — neither is favored by length
- Raw pointer receivers are the common case even for managed objects, so `*T` being short matters

**Full pointer syntax:**
```
*T              // raw pointer to T
@T              // managed pointer to T
&x              // take raw address of x
*p              // dereference (explicit)
p.field         // auto-dereference with . (Go-style, no ->)
```

**Implicit conversion:** `@T` → `*T` is implicit (safe — managed is "narrower," caller keeps the managed pointer alive). `*T` → `@T` is never implicit. This was debated — explicit conversion would be safer but too noisy. Since raw pointer receivers are the common case, requiring decoration at every method call site would be burdensome. The implicit direction is always safe as long as the managed pointer remains live in the caller's scope.

**`make` and `box` for managed allocation:**
```
make(Point)              // zero-init, returns @Point (takes a type)
make([]int, n)           // runtime-sized managed slice, returns @[]int
box(42)                  // box an integer, returns @int (takes an expression)
box(Point{x: 1, y: 2})  // allocate with init, returns @Point
```

`make` always takes a type (+ optional size for slices). `box` always takes a value expression. This clean split eliminates the parsing ambiguity of `make(foo)` where `foo` could be a type or a variable. Forward-compatible with non-nullable pointers (no intermediate nil state).

### Slice Syntax

Mirrors the pointer pattern — `@` prefix means "managed version":
```
[]T             // raw slice (two words: raw ptr, length)
@[]T            // managed slice (three words: managed ptr, raw ptr, length)
arr[low:high]   // slice expression (exclusive end, like Go)
```

**Ambiguity with pointers to slices:** `@[]T` is syntactic sugar for "managed slice of T." If you need a managed pointer to a raw slice (rare), use parentheses to break the sugar: `@([]T)`. The `@[]` sugar is syntactic only — in generics, `@T` where `T=[]int` means `@([]int)` (managed pointer), not managed slice.

### Interface Values — Managed/Raw Pattern

The managed/raw pattern extends to interface values:
```
Stringer        // raw interface value: (raw ptr to data, vtable ptr) — no refcounting
@Stringer       // managed interface value: (managed ptr to data, vtable ptr) — refcounted
```

This was discovered when discussing variadic functions. Raw interface values are zero-overhead — no boxing, no heap allocation. Managed interface values keep the data alive. The pattern is consistent: raw = "temporary use," managed = "I keep this alive."

### Variadic Functions

Go-style `...T` syntax. The key insight is that raw interface variadics are zero-overhead:
```
func println(args ...Stringer) { ... }  // raw interface — no boxing, no heap alloc
func collect(args ...@Stringer) { ... } // managed — retains args
```

For `println(x, p)`, each argument is packaged as a (raw ptr, vtable ptr) pair on the caller's stack. No heap allocation, no refcounting. This solves the embedded/logging use case — user-defined logging functions can be as efficient as compiler builtins.

**Alternatives considered:**
- Compiler intrinsics only for print-like functions — rejected because custom logging is important in embedded/server contexts
- Stack-allocated boxing — still useful as a compiler optimization for managed variadics, but raw interfaces eliminate the need for boxing entirely

### Function Syntax

```
func add(a int, b int) int { return a + b }
func divmod(a int, b int) (int, int) { return a / b, a % b }
x, y := divmod(10, 3)     // destructuring multiple returns

// Methods with various receiver types
func (p *Point) translate(dx int, dy int) { ... }     // raw pointer
func (p @Point) retain() { ... }                       // managed pointer
func (p *const Point) distance() float64 { ... }       // const raw pointer
func (p Point) toString() []char { ... }                // const value (always const)
```

**Decisions:**
- No named return values (confusing, not best practice in Go)
- No same-type parameter shorthand (`a, b int`) — also confusing
- No special closure capture semantics

### Closures

**Always capture by value.** No capture-by-reference, no capture lists. If you want shared mutable state, capture a pointer (managed or raw).

```
x := 5
f := func() int { return x }
x = 10
f()  // returns 5 — captured by value

// Shared mutable state via managed pointer
count := box(0)
f := func() int { *count++; return *count }
```

**Why this design:**
- **C++ approach** (explicit capture lists): maximally flexible but verbose and high cognitive load
- **Go approach** (implicit capture by reference + escape analysis): simple syntax but surprising semantics (loop variable gotcha), requires escape analysis for heap promotion
- **Capture by value + explicit pointers**: no surprises, no escape analysis, consistent with the rest of the language where managed/raw pointers are the mechanism for controlling lifetime and sharing

The `makeCounter` pattern works naturally — capture a managed pointer, the closure's copy keeps the allocation alive via refcounting.

### Control Flow

Go-style:
```
if x > 0 { ... } else if x < 0 { ... } else { ... }
for i := 0; i < n; i++ { ... }     // C-style
for cond { ... }                     // while-style
for { ... }                          // infinite loop
for item in collection { ... }       // range/iteration

switch x {
case 1: ...
case 2, 3: ...
default: ...
}

switch {                              // condition-less (like Go's switch true)
case x > 0: ...
case x < 0: ...
default: ...
}
```

No fallthrough by default (like Go). Condition-less switch supported — cleaner than long if/else-if chains.

### Const Syntax

**Left-to-right reading.** Each `const` applies to the thing immediately to its right:
```
const *int           // const pointer to int (pointer can't change)
*const int           // pointer to const int (data can't change)
const *const int     // const pointer to const int
[]const *int         // slice of const pointers to int
[]*const int         // slice of pointers to const int
```

This avoids C's left-right parsing confusion entirely because Binate types are always read left-to-right.

**Const on variable declarations:** the variable can't be reassigned:
```
const x int = 5
const p *int = &y    // p can't be reassigned, but *p can be modified
```

**Const on function parameters:** `const` on the parameter variable itself is allowed but not part of the type signature — it's a local implementation detail (like parameter names, useful for documentation and self-discipline, ignored for signature matching).

### Impl Declaration Syntax

Went through several iterations of bikeshedding:

- `impl Writer for FileHandle` — Rust-like, but reads as a command/directive rather than a declaration
- `FileHandle implements Writer` — perfect English but purely infix (no leading keyword for the parser)
- `implement Writer for FileHandle` — clearer unabbreviated, but still a directive
- `impl FileHandle : Writer` — concise, parseable, type-first

**Chose `impl Type : Interface, ...`:**
```
impl FileHandle : Stringer                  // value receiver
impl *FileHandle : Writer, Reader           // raw pointer receiver
impl @FileHandle : Retainable               // managed pointer receiver
impl *const FileHandle : Stringer           // const raw pointer receiver
```

Rationale:
- Leading keyword (`impl`) for easy parsing
- Type-first is natural for scanning ("what does FileHandle implement?")
- Colon reads as "satisfies" / "is a" — familiar from OOP
- Comma-separated for multiple interfaces

### Type Cast Syntax

**Keyword-based, consistent with `make`:**
```
cast(int, y)              // value conversion (e.g., float → int)
bit_cast(*int, rawAddr)   // reinterpret bits
box(Point{x: 1})          // allocate managed
```

Go-style `int(y)` was rejected because it looks identical to a function call — confusing when types and functions share a namespace. Generic-style `cast[int](y)` was rejected because without type inference, you'd need both type parameters: `cast[int, float64](y)`, which nobody wants.

All three (`make`, `cast`, `bit_cast`) are builtins that take types as arguments, distinct from regular function calls.

**Cast semantics:**
- Literals are checked at compile time for fit: `cast(uint, -1)` → compile error
- Typed values wrap/truncate at runtime: `cast(uint, x)` where x is int with value -1 → wraps to UINT_MAX
- `bit_cast` always just reinterprets bits, no checking

### Variable Declarations

```
var x int              // zero-initialized
var x int = 5          // explicit init
x := 5                 // short declaration, type inferred

// Function types
var f func(int) int
f := func(x int) int { return x * 2 }    // closure
```

### Visibility

**No per-symbol visibility keywords** (no `pub`, no capitalization convention like Go). Instead, visibility is structural: if a symbol appears in the package's interface file, it's public. If not, it's private.

This pairs naturally with the package interface file system and avoids the debates around visibility syntax.

---

## 7.5. Primitive Types

### Decision

```
int, uint                           // platform word size
int8, int16, int32, int64           // fixed-width signed
uint8, uint16, uint32, uint64      // fixed-width unsigned
float32, float64                    // floating point
bool                                // true, false
byte = uint8                        // alias
char = uint8                        // alias
```

**Design choices:**

- **Go-style spelled-out names** (`int32` not `i32`) — clearer, less cryptic
- **`int`/`uint` are platform word size** — natural register size, like Go
- **`int64`, `uint64`, `float32`, `float64` are optional** subject to hardware support. Many small 32-bit targets lack 64-bit integer support or FPU.
- **No unqualified `float`** — forces explicit choice of precision
- **No `uintptr`** — `uint` serves this purpose. On all target platforms, pointer size = word size. If a platform with divergent sizes is ever targeted, `uintptr` can be added then.
- **`byte` and `char` are aliases for `uint8`** — not distinct types. `byte` for raw data readability, `char` for string-related code. Keeps things simple; a distinct `char` type would add friction for low-level byte manipulation.
- **`bool` with `true`/`false`** — a proper type, not C-style integers-as-bools. Worth the tiny cost for type safety.

### Literal Defaults

When type context is ambiguous (e.g., `x := 123`):
- Integer literals → `int`
- Float literals → `float64`
- String literals → `[]const char` (raw slice into static read-only data)
- Bool literals → `bool`

**Literal overflow is a compile error:** `var x uint8 = 256` fails. Literals are checked at compile time for fit.

**String literal representation:** the raw storage includes a null terminator for C interop, but the slice excludes it. `"abc"` → storage is `{'a','b','c','\0'}`, default type is `[]const char` with length 3. The null exists in memory but isn't part of the slice's view.

---

## 8. Package System & Interface Files

### Design

Packages have **explicit, separate interface files** — declarations separate from definitions. The compiler verifies that implementations match their interfaces.

**Advantages over C header files:**
- Authoritative (compiler-enforced, not just convention)
- No preprocessor
- Clean separation of API contract from implementation

**Benefits:**
- Clear API contracts (the interface file IS the documentation)
- Faster compilation (consumers only need the interface)
- ABI stability (change implementation without changing interface)
- Binary-only library distribution (ship interface + compiled lib, no source needed)
- Dual-mode interop: interpreter can load interface files to call compiled code without source

This was identified as one of the design decisions with the widest-reaching positive effects — it ties into visibility, compilation speed, binary distribution, and the interpreter embedding model.

### File Extensions

- `.bn` — implementation files
- `.bni` — interface files
### Package Declaration

String-based, matching the import path:
```
package "pkg/foo"
```

Every file starts with a package declaration (after comments/whitespace). The string must match the package's position in the directory structure and is the same string used in `import` statements.

### Directory Layout

Interface file sits as a **sibling** of the implementation directory:
```
pkg/
  foo.bni          // interface
  foo/             // implementation directory
    impl1.bn
    impl2.bn
```

**Why sibling rather than inside:** enforces separateness and makes it clear that the `.bni` contents have "extern" semantics — declarations that must be properly defined in the `.bn` files.

**One interface file per package.** Multiple `.bn` files per package are supported (all in the same directory, all declaring the same package string).

### Import Syntax

```
import "pkg/foo"              // standard import
import myname "pkg/foo"       // aliased import
```

Go-style. When compiling a package, only `.bni` files are needed for imported packages — the implementation is only needed when compiling/interpreting the implementation itself, or at link/load time.

### Search Path & Visibility

- **Project root** is highest priority on the search path
- `pkg/`-prefixed packages are "public" — found via the full search path
- Non-`pkg/` packages are inherently local (not subject to external search path)
- Shadowing allowed: project-local packages take priority over external

**No language-enforced `internal/` convention.** With separate interface files, visibility is already controlled by whether a `.bni` file exists and is on the search path. Unlike Go, the interface/implementation separation already provides the access control.

### Main Package

`package "main"` is a special case:
- Requires a `main()` function
- No `.bni` required (it's the entry point, not a library)
- Multiple `.bn` files supported (all in same directory)
- The standard `main.bni` could potentially be overridden for special embedded configurations (e.g., different entry point signatures), though this would require linker configuration

### Alternatives Considered

- **Go-style directory = package**: adopted as the basic model, with the addition of separate `.bni` files
- **Rust-style `mod` declarations**: rejected for simplicity — file system is the source of truth
- **C-style `#include`**: rejected — preprocessor-based inclusion is fragile and order-dependent

---

## 9. Forward References and the REPL Model

### The Problem

In a statically typed language, forward references are a problem: if you define function `f` that calls undefined function `g`, the type checker can't validate `f`. This is especially acute in a REPL, where definitions happen incrementally.

### Approaches Considered

1. **Require declaration before use** (prototypes/forward declarations): unergonomic and unfashionable, though it simplifies the compiler. Also raises questions about changing declarations.

2. **Deferred validation**: don't validate a function until all its dependencies are defined. Functions sit in a "pending" state. Problems: errors become non-local (define g, get an error about f); unclear what happens when there's no possible definition of g that makes f valid.

3. **Purely runtime checks**: less friendly to compiled/interpreted interop.

4. **Hybrid with "draft blocks"**: multi-definition context in the REPL where nothing is validated until you close it.

### Decision: Retained Mode vs. Immediate Mode

The key insight was distinguishing between **retained mode** (definitions) and **immediate mode** (execution) in the REPL.

- **Retained mode**: function/type/struct definitions. Parsed and stored but validation is deferred until dependencies are available or validation is explicitly triggered. Source files are entirely retained mode.
- **Immediate mode**: expressions/statements to execute now. Fully checked at entry time.

In compiled or non-REPL interpreted mode, everything is retained — the whole program is available for validation before execution begins. No forward reference problem.

In the REPL, retained and immediate entries interleave. This model keeps compiled/interpreted semantics identical — only the *timing* of validation differs.

### Redefinition

Redefinition is supported in the REPL, even after use. The semantics fall out naturally from the refcounting memory model:

- The REPL name table maps names to current definitions (managed pointers)
- Capturing a function into a variable bumps the refcount on the definition object
- Redefinition updates the name table; old definition stays alive if anyone holds a reference
- Existing captured references keep the old definition

**Stale reference warning**: at redefinition time, if the old definition's refcount > 1, warn that outstanding references exist. This is a nice usability touch without being a hard error.

This approach was preferred over re-validating all dependent code on redefinition (which would be a reactive/incremental compilation model — powerful but complex) or purely runtime checks (which would break the static typing story).

---

## 10. Threading

### Decision: Single-Threaded Default, Threading-Compatible

The language is single-threaded by default but doesn't prevent OS-level threads. The compiler must not optimize based on single-threaded assumptions (no reordering memory operations visible across threads, no assuming globals can't change).

**Refcounts are non-atomic in v1.** Managed objects belong to one thread; cross-thread sharing requires explicit locks. Atomic refcounts are a possible v2 opt-in (per-type).

This was chosen because:
- Full concurrency support (goroutines, async/await) adds enormous complexity
- Small 32-bit targets may not have efficient atomic operations
- Non-atomic refcounts are fast, and the "objects belong to one thread" rule is simple

**Interrupt handlers**: the constraint is "don't manipulate managed objects in interrupt handlers." Best practice is to bump/queue work out of interrupt context, which is already standard kernel design. This constraint is much milder than Unix signal handler restrictions (where you can't even call `printf`), so it should be acceptable.

---

## 11. Dual-Mode Interop

### Decision: Function Pointers as the Unification Layer

This is the language's most distinctive feature. The design:

- **Compiled functions**: native function pointer, direct call
- **Interpreted functions**: pointer to a thunk that packages arguments, invokes the interpreter, returns the result
- The caller doesn't know or care which kind it's calling
- Overhead (one indirection for interpreted calls) is only paid when crossing the boundary

**Why this works**: all the other design decisions support it:
- Same heap, same refcounting, same struct layouts → no marshalling needed
- Same type system in both modes → thunk bridges calling conventions, not types
- Package interface files → interpreter discovers compiled function signatures and addresses
- Management info in managed structs → objects carry their own cleanup semantics across mode boundaries

**Interpreted → compiled**: interpreter loads interface files, resolves addresses from the binary's symbol table (or explicit registration), calls through native function pointers.

**Compiled → interpreted**: compiled code holds a function pointer that happens to be a thunk. Transparent.

**Mixed vtables**: some interface methods can be compiled, others interpreted. The caller is oblivious. This enables powerful workflows — prototype in the REPL, compile the hot paths.

### Alternatives Considered

1. **Everything through a dispatch table**: all function calls go through indirection. Clean but adds overhead to every call, even compiled-to-compiled.
2. **Explicit at call sites**: you know at compile time whether you're calling compiled or interpreted. Less overhead but breaks the "seamless" promise.

Both were rejected in favor of the function pointer/thunk model, which has the right performance characteristics (zero overhead for compiled-to-compiled) and the right abstraction (caller-transparent).

### Interpreter Embedding

The interpreter is a library linked into the compiled binary. It shares the same heap (no separate managed heap), accesses compiled symbols via interface files + symbol resolution, and has its own evaluation state but operates on the same data.

### Hot-Swapping

Redefining interpreted functions at runtime (while a compiled binary is running) was noted as a natural capability of the thunk model — just update what the thunk points to. Deferred for later discussion.

---

## 12. Error Handling

### Decision: Errors as Values, No Exceptions

No language-level error handling mechanism. No exceptions, no panic/recover (unlike Go which has panic/recover). Errors are just values — return them as part of a tuple, check them, handle them.

**Why no exceptions:**
- Exceptions create hidden control flow — a function can fail in ways not visible in its signature
- Stack unwinding across the compiled/interpreted boundary would be extremely complex to implement correctly
- Exceptions add significant runtime complexity (unwinding tables, catch handlers)

**Why no panic/recover:**
- Go's panic/recover is essentially exceptions with different syntax. The same hidden-control-flow objections apply.
- If something is truly unrecoverable, the program should crash. Simple.

**Why errors-as-values works well here:**
- Multiple returns make the ergonomics natural: `result, err := doSomething(x)`
- Errors cross the compiled/interpreted boundary trivially — they're just return values
- The pattern is explicit: the caller always sees what can fail by looking at the return signature

No built-in error type was deemed necessary — any type can serve as an error. Conventions will emerge (like Go's `error` interface), but the language doesn't prescribe one.

---

## 13. Untyped Pointers and Casting

### Decision: `any` Interface + `bit_cast`

The `void*` problem was solved by the `any` interface (see Interfaces section):

- **Managed `*any`**: pointer to some refcounted allocation of unknown type. Refcounting works because the management info is in the allocation, not dependent on the type. Must cast to use the data.
- **Raw `*any`**: just an address. Direct equivalent of C's `void*`.

**`bit_cast`** (or similar): reinterpret the bits of one type as another. No value conversion, no runtime checking. This is distinct from regular type casts (which perform value conversions like `int` → `float`). It's the explicit "I know what I'm doing" escape hatch for:
- Casting between pointer types
- Reinterpreting memory layouts
- Low-level systems work

---

## 14. Const-ness

### Decision: Const Variables, Const Pointers, No Deep Immutability

C's `const` is heavily overloaded and syntactically confusing (`const int *` vs `int const *` vs `int *const`). The goal was a cleaner design.

**What's included:**

- **Compile-time constants**: `const x = 5` — value baked into the binary.
- **Const pointers/slices**: read-only view, shallow. A function taking `const char[]` promises not to write through the slice. The data may be mutable through other references — this is a promise about *this* access path, not about the data itself.

**What's excluded:**

- **Deep immutability**: skipped for v1. Enforcing that an entire object graph is immutable is complex and potentially expensive to check.
- **C++ `mutable` keyword**: not needed because we don't have const methods that need to punch holes in their own guarantees.

**Syntax benefit**: type-after-name declarations avoid C's left-right confusion. `buf const char[]` (or similar) is unambiguous.

### Const and Methods: Five Receiver Kinds

The interaction of const with the three pointer types (value, raw, managed) led to identifying five receiver kinds. See the Interfaces section for full details. The key insight:

- Value receivers are always const (mutating a copy is pointless)
- Pointer receivers can be const or mutable
- This gives: const value, const raw pointer, const managed pointer, raw pointer, managed pointer

**Auto-conversion at call sites** follows the safe direction only: managed → raw → const raw. Cannot auto-promote raw → managed.

### Const-ness in Interface Implementations

Interfaces themselves don't specify const-ness. The `impl` declaration's receiver type determines what pointer types can satisfy the interface. If `impl Stringer for FileHandle` uses a const receiver, then `const *FileHandle` can be used as a `Stringer`. If the impl uses a mutable receiver, only mutable `*FileHandle` works.

This was preferred over having interfaces specify const-ness because:
- No extra syntax needed in interfaces
- Different types can implement the same interface with different const-ness
- Falls out naturally from the type system

---

## 15. Volatile

### Decision: Builtin Functions, Not a Type Qualifier

Unlike C's `volatile` keyword (which is a type qualifier that infects the type system), volatile access in Binate is done through builtin functions: `volatile_read`, `volatile_write`, etc.

**Why not a type qualifier:**
- C's `volatile` is viral — it infects pointer types and must be tracked through every cast and assignment
- You can accidentally do a non-volatile access through a pointer you forgot to mark
- It adds complexity to the type system for a feature used only in a small fraction of code (device drivers, MMIO)

**Why builtins are better:**
- Volatility is at the point of access, not on the type
- Every volatile access is explicitly visible at the use site
- Simpler compiler — no need for the type system to track volatile-ness
- The builtins just mean "emit this load/store with no optimization"
- Slightly more verbose for heavy MMIO code, but wrapper structs/methods can hide this

---

## 16. Type Declarations & Aliases

### Decision: `type` Keyword for All Named Types

All named types are introduced with the `type` keyword, following Go's model:

```
type Celsius float64           // distinct new type, same representation
type byte = uint8              // alias, fully interchangeable
type Point struct { x int; y int }  // named struct (only way to declare one)
type Handle @SomeStruct        // distinct type wrapping a managed pointer
type Buffer []uint8            // distinct type wrapping a slice
```

**Design choices:**

- **No `struct Point{...}` shorthand.** The only way to create a named struct type is via `type`. This is Go's approach and keeps one consistent mechanism for naming types.
- **Distinct types vs. aliases**: `type Celsius float64` creates a new type that requires explicit `cast()` to convert to/from `float64`. It can have methods and implement interfaces. `type byte = uint8` creates a pure alias — no new type, just an additional name. Aliases cannot have methods.
- **Methods and `impl` require named types.** Anonymous types cannot be receivers. This follows Go's rule and avoids the complexity of attaching methods to structural types.

### Anonymous Struct Types

`struct{x int}` defines an anonymous type. Two identical anonymous struct definitions refer to the same type (structural equivalence). `type Foo = struct{x int}` creates an alias for the anonymous type.

**Alternatives considered:**
- **Nominal equivalence for anonymous structs**: rejected — would mean every `struct{x int}` is a different type, making anonymous structs far less useful
- **Methods on anonymous types**: rejected (Go's rule). This keeps the type system simple — if you want methods, name the type.

---

## 17. Struct & Array Literals

### Struct Literals

```
Point{x: 1, y: 2}    // named fields
Point{1, 2}           // positional
Point{x: 1}           // partial — unspecified fields zero-initialized
Point{}               // all fields zero-initialized
```

**Design choices:**
- Named and positional forms both supported. Named is preferred for readability; positional is needed for anonymous fields.
- Partial initialization with zero-init for omitted fields (Go-style). Important for systems programming where you may want a partially-filled buffer struct.
- `make(Point{x: 1, y: 2})` allocates a managed copy — the compiler optimizes this to allocate + init-in-place (no temporary, no copy).

### Array Literals

```
[3]int{1, 2, 3}           // full initialization
[...]int{1, 2, 3}         // inferred size (Go-style)
[3]int{}                   // zero-initialized
[3]int{1}                  // partial → {1, 0, 0} — Go-style zero-fill
[5]int{1: 10, 3: 30}      // indexed → {0, 10, 0, 30, 0} — sparse/lookup tables
```

**Key decision**: partial initialization zero-fills remaining elements (Go behavior). This is important for systems programming where you often declare a buffer of a given size, only partially filled with data.

---

## 18. Annotation System

### Decision: `#[...]` Syntax with Namespaced Annotations

**Syntax:**
```
#[packed]                      // standard annotation
#[packed, align(4)]            // multiple, comma-separated
#[compiler.register_calling]   // compiler-specific
#[tool.export]                 // external tool annotation
```

**Namespacing:**
- **Unqualified** = language-standard. Compilers/interpreters enforce these are known/valid (catches typos).
- **`compiler.*`** (or specific compiler name) = compiler/interpreter-specific. Unknown namespaces are silently ignored by other implementations.
- **`tool.*`** = external tool annotations. Compiler ignores.

**Attachment model — "annotates the immediately following element":**
```
#[tools.export] type Foo struct { ... }     // annotates the declaration
type Foo #[packed] struct { ... }           // annotates the type definition
#[align(4)] x int                           // annotates the field
x #[foo] int                                // annotates the type
```

**Ambiguity resolution:** `#[foo] int` on an anonymous field is disallowed because it's ambiguous (does it annotate the type or the field?). Must use explicit `_` to disambiguate: `#[foo] _ int` (annotates the anonymous field) vs `_ #[foo] int` (annotates the type). Same rule applies in argument lists.

**Multiple annotations:** comma-separated within one `#[...]` block only. No stacking of separate `#[...]` blocks. This avoids the question of whether stacked annotations are independent or compose.

**Type identity:** only standard/compiler annotations that affect representation (e.g., `packed`) affect type identity. Tool/metadata annotations do not. So `struct #[packed] {x char; y char}` is a different type from `struct {x char; y char}`, but `struct #[tool.doc("...")] {x char; y char}` is the same type as `struct {x char; y char}`.

**Alternatives considered:**
- **Go-style struct tags** (string-based): less structured, no compiler validation for standard annotations
- **Rust-style `#[...]`**: adopted, with the addition of namespacing to distinguish standard vs. compiler vs. tool annotations
- **C-style `__attribute__`/`#pragma`**: rejected for readability and inconsistency

---

## 19. Scoping Rules

### Decision: Block Scoping, Shadowing with Warnings

**Block scoping:** every `{}` block introduces a new lexical scope. Variables declared in a block are not visible outside it. Standard and uncontroversial.

**Variable shadowing:** allowed, but the compiler warns by default.

**Alternatives considered:**
1. **Allow freely (Go)** — flexible but widely considered one of Go's mistakes. The `:=` shadowing bug (`val, err := bar()` inside an `if` silently shadows the outer `err`) is a classic.
2. **Disallow entirely** — safe but creates real friction (forces unique names, especially in deeply nested code or when reusing common names like `err`, `i`, `n`).
3. **Allow only across function boundaries** — inner functions can shadow outer variables, but not blocks within the same function. A middle ground but adds a rule that's hard to explain.
4. **Allow, but warn** — linter-level default warning, suppressible with an annotation. Trusts the programmer while being helpful.

Chose option 4. Consistent with the language's philosophy of trusting the programmer while providing safety nets.

**Top-level scope:** only declarations allowed — `type`, `func`, `const`, `var`, `enum`, `interface`, `impl`, `import`. No bare expressions or statements. Source files are purely declarative; bare expressions are REPL immediate-mode only. This keeps the compiled/interpreted distinction clean.

**Package-level mutable variables:** allowed (`var` at top level). Mutable globals are a fact of life in systems programming — hardware register mappings, global configuration, static buffers. Restricting to `const`-only would add friction without real benefit for the target audience.

**Initialization order:** dependency-based, then source order within a file, then file order within a package. This matches Go's approach and is predictable.

**No `init()` functions** (unlike Go). Go's `init()` functions are a source of surprising side effects — code runs at import time with no explicit call. For a language targeting embedded systems where startup behavior matters, explicit initialization is better. If you need setup logic, call it from `main`.

---

## 19.5. Memory Management Details

### Managed Allocation Layout

```
[ refcount (uint) | free function ptr | user data ... ]
                                        ^
                                        managed pointer points here
```

Two words of overhead per managed allocation.

**Why this layout:**
- Single allocation (header + data contiguous). Cache-friendly.
- Managed pointer points directly at user data — `*p` gives the data with no indirection.
- Converting `@T` → `*T` is trivial: same address. The raw pointer IS the managed pointer value. The management info lives at known negative offsets.
- Proven approach: Objective-C, COM, CPython all use variations.

**Refcount** (one word): decremented on managed pointer destruction/overwrite, incremented on copy. When it reaches zero, recursively release managed fields (decrement their refcounts), then call the free function.

**Free function pointer** (one word): called when refcount hits zero, after managed fields are released. Provides the flexibility to support different allocation strategies:
- Normal heap: `free(base_ptr)` (pointer adjusted back to start of header)
- Static/pre-initialized data: no-op (data lives in ROM or static memory)
- Custom/pool allocators: allocator's dealloc function

The free function in the header was chosen over always using the default allocator because pre-initialized managed data in static/ROM memory is a real use case on embedded targets. A no-op free function handles this cleanly.

**No destructor in the header.** For non-interface types, the compiler always knows the concrete type statically and generates the appropriate drop code (decrement managed fields, call free). For interface values, the drop function lives in the vtable/type-info alongside the other method pointers — the type info is already carried with the interface value, not in the per-object header. This saves a word per allocation.

**Static managed data:** uses a sentinel refcount value (e.g., `UINT_MAX`) that the decrement logic checks. If refcount is the sentinel, don't decrement, don't free. Static managed objects are effectively immortal. This enables pre-initialized managed data in ROM/static memory with a no-op free function and immortal refcount.

### `make` and `box` Semantics

```
make(Point)              // @Point, zero-init (takes a type)
make([100]int)           // @[100]int, fixed-size managed array
make([]int, n)           // @[]int, runtime-sized managed slice

box(42)                  // @int (takes an expression)
box(x)                   // @T where x: T
box(Point{x: 1, y: 2})  // @Point, allocate with init
```

`make` always takes a type; `box` always takes an expression. See "make vs box" section below for the disambiguation rationale.

**Runtime-sized arrays:** `make([]int, n)` is needed because `make([n]int)` requires `n` to be a compile-time constant. Dynamic sizes are common (reading files, building buffers). The result is `@[]int` — a managed slice pointing to a freshly allocated backing array of `n` zero-initialized elements.

**No capacity argument** (unlike Go's `make([]T, len, cap)`). Growing/resizable arrays are a standard library concern (a `Vec`/`Buffer` type). This keeps the language primitive simple.

### `make` vs `box` — Resolving the Ambiguity

The original design had `make` handling both type-based allocation (`make(Point)`) and value-based boxing (`make(42)`, `make(x)`). This created a parsing ambiguity: `make(foo)` — is `foo` a type (zero-init allocation) or a variable (box its value)? Since type names and variable names share a namespace, this is genuinely ambiguous.

**Solution: split into two builtins.**

- `make(T)` — always takes a type. Zero-initializes. Returns `@T`.
- `make([]T, n)` — takes a slice type + runtime size. Returns `@[]T`.
- `box(expr)` — always takes a value expression. Allocates, copies. Returns `@T`.

No overlap, no ambiguity. The parser knows: after `make(`, expect a type. After `box(`, expect an expression.

```
make(Point)              // @Point, zero-init
make([]int, n)           // @[]int, runtime-sized
box(42)                  // @int
box(x)                   // @T where x: T
box(Point{x: 1, y: 2})  // @Point, allocate with init
```

`box(Point{x: 1, y: 2})` replaces the old `make(Point{x: 1, y: 2})`. The composite literal is an expression, so `box` handles it naturally.

**Alternatives considered for dynamic arrays:**
- `make([100]int)[:]` for creating a managed slice from a managed fixed-size array — works but only for compile-time sizes
- Go-style `make([]int, len, cap)` — adds a concept (capacity vs length) at the language level that belongs in a library
- Separate `alloc(T, n)` builtin — adds another builtin when `make` can handle it

---

## 19.7. Method Resolution & Dispatch

### One Method Per Name

No overloading on receiver kind. A method name is defined once per base type, regardless of whether the receiver is value, `*T`, or `@T`. Go follows this rule. It eliminates ambiguity entirely — there's always at most one candidate.

### Auto-Dereferencing

One level only (like Go). For `obj` of type `@T` or `*T`, the compiler looks for methods on the pointer type and on `T`. No deep chains — `**T` requires manual deref. This keeps call resolution predictable.

### Value Receivers as `*const T`

Value receivers are implemented by passing `*const T` under the hood. This avoids copying large structs — the callee gets a read-only pointer to the original data. Since value receivers are always const (already decided), there's no observable difference. The compiler knows value receiver pointers are never null (you can't call a method on a non-existent value), so it can skip null checks — this is a pure implementation detail, not exposed to the programmer.

### Interface Declarations

Consistent with structs: `type Name interface { ... }`. Anonymous interfaces exist as type expressions: `interface { write(buf []char) int }`.

**Interface embedding**: list interface names in the body. Means "is-a" for all embedded interfaces:

```
type ReadWriter interface {
    Reader
    Writer
    flush()
}
```

`impl *T : ReadWriter` implies `impl *T : Reader` and `impl *T : Writer`. The compiler generates vtables for all implied parent interfaces.

### Vtable Layout

**No deduplication, uniform recursive structure.** Every interface's vtable is:

```
[any (destructor)] [embed1's full vtable] [embed2's full vtable] [own methods]
```

Each embedded interface's vtable is included in full, recursively. `any`'s entry (destructor) appears multiple times — once for the interface itself, and once within each embed. This redundancy is in static data and negligible.

**Why no dedup:** deduplication (the diamond problem) adds complexity to vtable construction and makes offset calculation harder. The cost of duplication is a few extra function pointers in static data per (type, interface) pair. On systems with kilobytes of RAM, vtable count is small anyway. Simplicity wins.

**Interface conversion:** converting a child interface value to a parent is just adjusting the vtable pointer by a known fixed offset (compile-time constant). Same data pointer. No indirection, no allocation.

**Destructor in vtable:** the `any` entry in every vtable carries the destructor. This is how `any` being implicitly implemented by all types works — every vtable starts with a destructor, so any interface value can be dropped correctly regardless of the concrete type.

**One vtable per (type, interface) pair.** Vtables are static data generated at compile time. The interpreter generates them dynamically when `impl` declarations are processed.

---

## 19.8. Generics

### Decision: Monomorphized Generics with Interface Constraints

**Type parameters** on functions, structs, and interfaces:
```
func sort[T Comparable](items []T) { ... }
type List[T any] struct { head @Node[T] }
type Container[T any] interface { get(index int) T }
```

**Constraint syntax:** `[T InterfaceName]`. For multiple constraints, define a named combined interface:
```
type ComparableStringer interface {
    Comparable
    Stringer
}
func foo[T ComparableStringer, U any](a T, b U) { ... }
```

**Why no `+` for combining constraints:** defining a named interface is a few extra lines but creates an explicit, reusable concept. Avoids adding syntax for a case that's relatively uncommon. If it proves too verbose in practice, `+` can be added later without breaking anything.

**No type inference.** `sort[int](myArray)`, never `sort(myArray)`. Keeps the compiler simple and code explicit. Can relax in v2.

**Monomorphization:** each unique instantiation generates specialized code. `List[int]` and `List[uint8]` are distinct types with distinct code. Happens at the use site — compiler sees `sort[int](myArray)` and generates a specialization.

**Type checking against constraints:** the generic body is checked once against the constraint interface. If `T` is `Comparable`, the body can only call `Comparable` methods on values of type `T`. Instantiation only verifies that the concrete type satisfies the constraint. This means error messages about invalid generic bodies point at the generic definition, not at a distant instantiation site — much better than C++ templates.

**No generic methods on types** (Go's rule). The problem: interface vtables can't accommodate methods with unknown type parameters (vtable slot count would vary). Use generic free functions instead:
```
// Not allowed:
func (c *Converter) convert[T Castable](val T) int { ... }

// Instead:
func convert[T Castable](c *Converter, val T) int { ... }
```

**No conditional impls for v1.** You can't write `impl [T Stringer] List[T] : Stringer`. Only specific instantiations: `impl List[int] : Stringer`. Conditional impls are powerful (Rust has them) but add significant complexity. Deferred.

**Cross-package generics:** generic function/type bodies must be included in `.bni` files. The consumer needs the body to instantiate — this is the same trade-off as C++ templates in headers. The `.bni` exposes the implementation of generics, but there's no way around this with monomorphization.

**Zero values in generics:** `make(T)` inside a generic needs to zero-init `T`. Currently safe since all types have zero values (nullable pointers). If non-nullable pointers are added in v2, this interaction needs care.

---

## 19.9. String & Array Semantics

### Bounds Checking: Always On, With Escape Hatch

**Default: bounds-checked.** Every `s[i]` and `s[low:high]` checks bounds. Out-of-range is a runtime trap — program terminates with a diagnostic. Not UB, not recoverable. Same philosophy as division by zero.

**`unsafe_index(buf, i)` builtin for unchecked access.** A builtin function that skips the bounds check. Explicit at the use site — no annotations, no compiler flags, no guessing.

**Why include `unsafe_index` rather than relying on optimizer:**
- Explicitness and predictability — performance-critical code doesn't depend on optimizer sophistication
- Self-hosting goal: a minimal compiler with no optimization passes should still produce fast code where the programmer asked for it
- The compiler on small systems may have limited or no optimizer — `unsafe_index` gives direct control regardless
- Educational value: the language should be understandable without knowing what the optimizer does

**Alternatives considered:**
- Always check, rely on optimizer (Go) — works well with a good optimizer, but couples performance to compiler sophistication
- Never check (C) — buffer overruns are the #1 security bug class
- Check in debug only (Rust's model for `[]`) — surprising behavior difference between debug and release builds
- Annotation-based (`#[compiler.no_bounds_check]`) — awkward because it applies to expressions, not declarations

The compiler may still optimize away provably-redundant checks, but this is a bonus, not something the programmer relies on. Optional optimizer modules are a natural fit for the compiler architecture — a minimal build skips them, a full build includes them.

### Nil Slices: No Such Thing

**Slices cannot be compared to `nil`.** `nil` is only for pointer types (`*T`, `@T`). Slices are value types (conceptually a struct of pointer + length). Comparing a value type to `nil` is a type error.

**Check `len(s) == 0` for empty.** Functions that return "no data" return a zero-length slice; callers check length.

**For optional/nullable semantics, use a pointer:** `*[]T` or `@[]T` can be nil. This forces the caller to handle the optionality explicitly.

**Why not Go's nil-vs-empty distinction:**
- In practice it's a source of bugs — code accidentally treats nil as empty or vice versa
- The "meaningful empty vs no data" case is better served by explicit types: `([]T, bool)` or `*[]T`
- Simpler mental model: a slice always has a length, period

**Indexing:** zero-based. `s[i]` reads/writes. `s[low:high]` sub-slice, exclusive end. `s[:]`, `s[low:]`, `s[:high]` shorthand.

**`len()`:** returns the slice's length field. For fixed-size arrays `[N]T`, returns `N` as a compile-time constant. For string literal slices, returns length excluding the null terminator.

---

## 20. Operators

### Decision: C/Go-Style Operators with Defined Behavior Everywhere

The operator set follows C/Go conventions with one critical difference: **no undefined behavior**. Every operation has defined semantics.

**Integer division and modulo:**
- Division truncates toward zero: `-7 / 2 = -3` (matches C99+, Go, hardware)
- `%` result has same sign as dividend: `-7 % 2 = -1`
- Identity `(a/b)*b + a%b == a` always holds
- **Division by zero is a runtime trap** — not UB. Hardware traps this on most architectures anyway; making it defined means the interpreter can do the same thing.

**Alternatives considered for division:**
- Floor division (Python-style): mathematically cleaner, but unfamiliar to systems programmers and doesn't match what x86/ARM division instructions do
- Undefined behavior (C89-style): rejected — this is a systems language but "systems" doesn't mean "undefined"

**Integer overflow: wrapping (two's complement).** Not undefined behavior. This is what hardware does, and systems code often depends on wrapping behavior (ring buffers, hash functions, etc.). A linter or annotation could warn about unintentional overflow, but the language guarantees wrapping.

**Right shift: `>>` is arithmetic for signed, logical for unsigned.** Matches C/Go/Rust and hardware behavior. No separate `>>>` operator (Java/JS-style) — if you want logical right shift on a signed value, cast to unsigned first. One less operator to learn.

**Shift overflow:** shifting by >= the bit width of the type is **defined** (unlike C). Result is 0 for `<<` and logical `>>`, sign-extended (0 or all-ones) for arithmetic `>>`. This avoids a class of UB bugs and is cheap for the compiler to handle.

**Booleans are strict:** `&&`, `||`, `!` require `bool` operands. No truthy/falsy (no `if ptr { ... }`). This was an easy decision given that we have a proper `bool` type — truthy/falsy is a C legacy that adds implicit conversions.

**Assignment is a statement, not an expression.** No `x = y = 5`, no `if (x = foo())`. Eliminates a common bug class (`=` vs `==` in conditions). Go made this choice; it's good.

**`++`/`--` are postfix statements only.** No `++x`, no `y = x++`. This eliminates the pre/post increment confusion entirely. Go's approach.

**No comparison chaining.** `a < b < c` is a compile error. In C, this silently compares the boolean result of `a < b` with `c` — a classic bug. Go disallows it; we follow suit.

**No operator overloading.** Keeps the language simple, predictable, and fast to compile. The meaning of `+` is always numeric addition. If you want custom operations, use methods.

**Precedence** follows the standard C/Go order, which is deeply familiar to the target audience. No surprises.

---

## 21. Formal Grammar & Disambiguation

### Decision: EBNF Grammar with Documented Disambiguation Rules

The formal grammar (`grammar.ebnf`) covers the full language and is annotated with `[BOOTSTRAP]`/`[DEFERRED]` markers for the Go interpreter subset.

**Builtins as keywords:** `make`, `box`, `cast`, `bit_cast`, `len`, `unsafe_index` are **keywords**, not predeclared names. They take types as arguments (e.g., `make(Point)`, `cast(int, x)`), which can't be parsed as regular function calls — a regular function can't take a type as an argument. Making them keywords eliminates the ambiguity at the grammar level.

**Eleven disambiguation rules (D1–D11):**

1. **D1 — ShortVarDecl vs Assignment/Expression**: `x := expr` is a short var decl (`:=` is always declaration). `x = expr` is assignment. `x op= expr` is compound assignment. Resolved by token after the identifier list.

2. **D2 — For-clause variants**: `for` followed by tokens is disambiguated: if `in` keyword appears, it's for-in; if `;` appears, it's C-style; otherwise while-style or infinite. The parser looks ahead for `;` or `in`.

3. **D3 — `@[]T` managed slice sugar**: `@[]T` is managed slice sugar (3-word representation). `@([]T)` is a managed pointer to a raw slice. Parens break the sugar. In generics, `@T` where `T=[]int` means `@([]int)`, not managed slice sugar.

4. **D4 — Composite literals in control flow**: `if x == Point{...}` is ambiguous — does `{` start the if-body or a composite literal? Resolved: in `if`, `for`, `switch` conditions, `{` cannot start a composite literal. Use parens: `if x == (Point{x: 1})`.

5. **D5 — Generic instantiation vs. indexing**: `foo[int]` could be generic instantiation or indexing. Resolved semantically: if `foo` is a generic function/type, it's instantiation. Otherwise indexing.

6. **D6 — Element keys**: In `{key: value}`, `key` could be a field name or an expression. Resolved semantically: for struct literals, bare identifiers are field names; for array literals, they're expressions.

7. **D7 — Const spec repetition**: In `const (...)` blocks, omitting type and expression repeats the previous spec (with `iota` incremented). Grammar allows the omission; semantics copy from the preceding spec.

8. **D8 — Unary `*` vs. binary `*`**: `*` as dereference (unary) vs. multiplication (binary). Standard resolution: unary if at expression start or after an operator; binary if after a primary expression.

9. **D9 — PrimaryExpr ordering**: BuiltinCall (keywords) is tried first, then CompositeLiteral (`TypeName` + `{`, including generic types like `identifier "[" TypeArgList "]" "{"`), then bare identifier. This ordering ensures composite literals and builtins are reachable — trying bare `identifier` first would consume the name before `{` is seen.

10. **D10 — StructField: named field vs anonymous embed**: When a struct field starts with an identifier, one-token lookahead determines whether it's a field name (followed by a type-starting token like `identifier`, `*`, `@`, `[`, etc.) or an anonymous embed (followed by `;`, `}`, or `.` for qualified names).

11. **D11 — TypeDef: TypeParams vs ArrayType**: Both `Type` (via `ArrayType "[" expr "]"`) and `TypeParams` (`"[" ident ident "]"`) start with `[`. Two-token lookahead after `[` resolves: `[identifier identifier` → TypeParams, `[identifier "]"` → ArrayType, `[identifier ","` → TypeParams, `[literal` → ArrayType, etc.

**Other grammar decisions:**
- **Compound assignment restricted to single expressions**: `x += 1` is valid, `x, y += 1, 2` is not. Compound assignment uses `=` for multi-value only.
- **Grouped declarations**: `import (...)`, `var (...)`, `const (...)`, `type (...)` all supported with identical grouping syntax.
- **For-in implicit declaration**: `for v in collection` implicitly declares `v` (value only). `for i, v in collection` declares `i` (index) and `v` (value). Iteration over slices and arrays only for v1.
- **QualifiedName**: `pkg.TypeName` for cross-package type references in type expressions.

---

## 22. Self-Hosting Bootstrap

### Decision: Interpreter-First Bootstrap in Go

**Why interpreter-first:**
- An interpreter is easier to write than a compiler (no codegen, no register allocation, no linker)
- The interpreter is a core language feature anyway — it's not a throwaway tool
- A tree-walking interpreter for a language subset can be quite small

**Why Go as the bootstrap language:**
- Good development speed (faster than C for this kind of work)
- The language's Go-leaning syntax means writing the parser feels natural
- Good string handling, easy parser construction
- GC means not fighting memory management while focusing on getting language semantics right
- Produces a single static binary
- Performance is adequate for a bootstrap tool that only needs to run a handful of times

**Alternatives considered:**
- **Python**: fastest to prototype, but slowest at runtime. Fine for a bootstrap tool, but Go isn't much harder and is much faster.
- **C**: most portable and closest to the metal, but slower to develop. No real benefit for a throwaway bootstrap tool.

**Bootstrap path:**
1. Write minimal interpreter in Go (supports a subset of Binate)
2. Write full interpreter and compiler in Binate
3. Use Go interpreter to run Binate compiler → produce native binaries
4. Compile the interpreter and compiler with themselves → fully self-hosted
5. Discard Go bootstrap interpreter

The compiler should support cross-compilation from the start, so the bootstrap doesn't need to happen on the target 32-bit systems.

---

## 23. Critical Review & Revisions

After the initial design pass, a critical review was conducted to stress-test all decisions. Key concerns raised, responses, and resulting changes:

### Complexity Budget

**Concern**: The aggregate of all decisions (managed/raw pointers, managed/raw slices, five receiver kinds, generics, explicit impl, const, package interface files, first-class tuples) makes the language substantially more complex than Go, despite claiming "simple and approachable."

**Response**: Simplicity has two dimensions — simplicity of use and simplicity of implementation. The managed/raw split is the minimum complexity to serve both:
- Managed memory is essential for REPL usability (manual management in an interactive environment is a nightmare)
- Raw memory is essential for kernel/systems work and the runtime itself
- A language with only one or the other is simpler along one axis but forces high costs on the other

The language targets "systems programming writ large" — from kernel to application code. C is fine for kernels but terrible for application code. Rust covers both but is complex everywhere. The goal is to be simple at every layer, with the programmer choosing how much control they need.

The five receiver kinds are the natural product of two orthogonal axes (const/mutable × value/raw/managed). A programmer doesn't need to memorize five things — they know const, and they know the pointer types.

### First-Class Tuples — REVISED

**Concern**: Elegant in theory but raises many type system questions (is `(int)` = `int`? named fields? nesting?) for limited practical benefit.

**Decision**: Dropped. Replaced with Go-style multiple return values — not first-class, just a calling convention feature. Covers 99% of practical use cases. Tuples can be added later as a proper type if needed.

### Refcounting for Kernel Code

**Concern**: Most kernel code has clear ownership; refcounting is rarely needed. The overhead of refcount headers is dead weight in kernel contexts.

**Response**: For kernel code, using only raw pointers is expected and natural. The managed system exists for higher layers (application code, REPL, tools). The language serves the full stack, and different layers use different features. This is analogous to how C++ code might use RAII in application code but raw pointers in kernel/driver code.

### The `any` Exception — REVISED

**Concern**: If the "explicit impl" rule needs an exception on day one, is the rule right?

**Decision**: Reframed. Instead of "`any` is a unique exception," the language defines a small, closed set of **built-in implicit interfaces** that all types satisfy. `any` is the primary one; others may be added (e.g., `Sized` for generics) but only by the language spec. User-defined interfaces are always explicit. This is more honest than pretending there's one exception.

### Package Interface Files

**Concern**: Reintroduces C's header-file maintenance problem.

**Response**: The separation is worth it for several reasons:
1. Enables pre-compiled, binary-only packages — critical for the embedded ecosystem
2. Enables libraries written in different languages to share interfaces
3. Enables multiple implementations of the same interface (e.g., small/slow vs. fast/big)
4. C interfaces remain the lingua franca precisely because other languages lack this capability
5. Explicit interfaces prevent accidental API changes
6. The compiler enforces consistency (unlike C headers)

Interface files could optionally be auto-generated from source code, but hand-written explicit interfaces should remain the core model. The auto-generation option is not precluded.

**Related future topic**: code annotations/metadata for external tooling, propagated to AST and object files.

### Const Justification

**Concern**: Is const worth the complexity (especially five receiver kinds)?

**Response**: Const is primarily justified by statically-initialized data (e.g., string literals). Without const, you must either:
1. Copy all static data to mutable memory at initialization (expensive on small systems)
2. Rely on hardware memory protection (runtime errors, requires MMU)
3. Allow modification of static data (major footgun)

Option 3 is unacceptable for a systems language. Const is the cheapest correct solution.

### String Handling — REVISED

**Concern**: len() including the null terminator is error-prone.

**Decision**: String literal → slice excludes the null from the slice. Storage for `"abc"` is `{'a','b','c','\0'}` (4 bytes), but the slice is `(ptr, 3)`. `len()` returns 3. The null exists in memory for C interop but isn't part of the slice's view. Clean separation.

### REPL Redefinition Semantics — REVISED

**Concern**: Deferred validation is complex. What happens when a function is redefined with an incompatible signature?

**Decision**: Two redefinition modes based on compatibility:
- **Compatible redefinition** (same signature): **replace**. All existing references continue to work.
- **Incompatible redefinition** (different signature): **shadow**. Old definition stays alive (refcounted) for anything that captured it. New code sees the new definition. Warn if old definition has outstanding references.
- A forced-shadowing escape hatch allows shadowing even for compatible changes.

For deferred/pending definitions: if `f` is pending waiting for `g`, and `g` is defined with a signature that doesn't match what `f` expects, then `g` is just a different `g` from `f`'s perspective — `f` remains pending. Error surfaces only when someone tries to call `f`.

This is robust because we can't know all the places that reference `g` (pointers may exist in compiled code). Shadowing is the only safe behavior for incompatible changes.

### Explicit `impl` Declarations

**Concern**: Go-style structural typing is simpler and eliminates the `any` special case.

**Response**: Explicit `impl` is preferred for two reasons:
1. **Explicitness**: Go's structural typing means identical interfaces are conflated. If `Reader` and `Fetcher` both have `Read([]byte) int`, a type satisfies both whether intended or not. This can't be fixed cleanly in Go.
2. **Implementation**: with structural typing, the compiler must check every type against every interface. With explicit `impl`, vtable generation is directed — the compiler knows exactly which (type, interface) pairs need vtables.

### 32-bit Target

**Concern**: Is 32-bit still relevant as a primary target?

**Response**: Low-cost and low-power embedded remains a real market. 32-bit targets don't need to be the *primary* focus, but supporting them well is part of the language's value proposition for embedded development.

### Embedded Development Use Case

The critical review surfaced an important framing for the dual-mode story: in embedded development today, "real" code is written in C/C++ while exploratory work uses MicroPython or similar. These are completely separate worlds — different languages, different semantics, can't share code. Binate bridges this gap: same language for both, explore interactively via the interpreter, compile for production. No rewrite, no semantic mismatch.

---

## 24. Phase 5 Planning: Self-Hosted Toolchain

With the Go bootstrap interpreter complete (Phase 4), the next step is writing the self-hosted toolchain in Binate itself. See `claude-plan-2.md` for the full plan. Key discussion points and rationale:

### Interpreter first, then compiler

The frontend (lexer, parser, type checker) is shared between interpreter and compiler. Writing the interpreter first means we build and validate the entire frontend before tackling codegen. The tree-walker backend is comparatively simple. This gets us to a self-hosting milestone sooner and provides a development platform for the compiler.

The bootstrapping chain: Go bootstrap → self-hosted interpreter → self-hosted compiler → self-compiled compiler (native binary).

### Single repo vs. multiple repos

The user's initial instinct was three repos (common code, interpreter, compiler). After discussion, starting with a single repo was preferred because:
- No cross-repo dependency tooling exists yet for Binate
- Shared frontend code will be heavily iterated; same-repo refactoring is trivial
- Binate's package system already enforces separation (`pkg/lexer`, `pkg/parser`, etc.)
- Can split mechanically later once boundaries are stable

### AST representation: the interface question

The bootstrap subset doesn't include interfaces, but the AST is inherently polymorphic (expressions, statements, declarations are all different types). Three options discussed:

1. **Tagged union with kind field + `*any` casts.** Works in the bootstrap subset but is ugly and error-prone. Would need refactoring once interfaces are available.
2. **Add interfaces to the Go bootstrap.** Significant work (vtable dispatch, impl declarations, method receivers) but produces clean self-hosted code from the start. Interfaces are already fully designed in the language spec.
3. **Hybrid approach.** E.g., use distinct types with a kind field but avoid `*any` by having per-kind accessor functions.

This is an open decision. Adding interfaces to the bootstrap is the cleanest path but the most work. The decision point is when we start writing `pkg/ast` for the self-hosted toolchain.

### Compiler architecture decisions

- **IR**: SSA-based, typed, target-independent. High-level operations (refcount inc/dec, bounds checks) lowered progressively.
- **Backends**: pluggable, one architecture at a time. Start with the dev machine's architecture.
- **Optimization**: optional and pluggable. Refcount elision and escape analysis prioritized (biggest wins for Binate's memory model).
- **Object files**: emit platform-native formats (ELF, Mach-O) directly behind a clean abstraction. Shell out to system linker initially; write own linker later for hermetic builds.
- **Inline assembly**: proposed `#[asm("arch")]` annotation syntax. Can be avoided for initial self-hosting by keeping OS primitives in builtin packages.

---

## 25. Maps / Hash Tables

### Decision: Library-only, no built-in map type

**Alternatives considered:**

1. **Built-in `map[K]V` (Go-style).** Convenient, but introduces language magic: special syntax for deletion, special iteration behavior, can't take address of elements, compiler must generate type-specific code behind the scenes. Conflicts with the "minimal core, no special-casing" philosophy.

2. **Library via generics (chosen).** `Map[K, V]` in a standard package, with hashability/comparability expressed as interface constraints on `K`. Just as ergonomic as a builtin once generics are available. Allows multiple implementations (hash map, tree map, etc.) without language changes.

**Why not built-in:**
- Go's built-in maps are one of its most "magical" features — they don't follow the same rules as user-defined types. Binate's design avoids this kind of special-casing.
- A library map is a normal generic type with normal semantics. No special deletion syntax, no hidden allocator, no compiler magic.
- Small-system targets may not need hash tables at all. Library = pay only if you import it.
- Implementation flexibility: users and the standard library can provide hash maps, tree maps, concurrent maps, etc. — all through the same generic interface.

**Bootstrap strategy (no generics available):**
- Concrete map types per key/value combination: `StringToInt`, `StringToType`, etc. More boilerplate, but the API shape matches what `Map[string, int]` would look like, so the transition to generics is mechanical.
- Alternative: sorted arrays + binary search. Simpler to implement, sufficient for bootstrap-scale data, but the API is more different from a generic map.
- Preference: concrete map types, because fewer code changes when generics arrive.

---

## 26. Spread Operator

### Decision: `...` spread operator for expanding slices into variadic arguments

The `...` spread operator allows a slice to be expanded into individual arguments when calling a variadic function. Syntax: `expr...` where `expr` is a slice type.

**Primary use cases:**
- `append(a, b...)` — slice concatenation (append all elements of `b` to `a`)
- Forwarding variadic arguments, e.g., a `printf` implementation that calls `sprintf` with accumulated args

**Why `append(a, b)` without spread was rejected:**
When `a` is `[]any`, `append(a, b)` is ambiguous — you cannot tell whether `b` is a single element to append or a slice whose elements should be spread. The explicit `b...` syntax resolves this ambiguity.

**Deferred from bootstrap.** The bootstrap subset does not implement the spread operator. For the primary bootstrap need (string concatenation), the `Concat` builtin is used instead.

---

## 27. Naming Conventions

### Decision: Capitalized exports (Go-style)

Exported symbols — those declared in `.bni` interface files — should use capitalized names: `TypeName`, `IsKeyword`, `Lookup`. Private symbols (not in `.bni`) use lowercase or snake_case: `helper_func`, `internal_state`.

**This is convention only.** The compiler does not enforce capitalization. Visibility is still determined solely by whether a symbol appears in the `.bni` file. The convention ensures readable code and makes it visually clear which symbols are part of the public API.

Types, functions, and constants that appear in `.bni` files should all follow this convention.

---

## 28. Topics Still Flagged for Future Discussion

- **Move/transfer ownership optimizations**: avoid refcount bumps when the compiler can prove last-use. Pure optimization, deferred.
- **Hot-swapping interpreted code at runtime**: natural fit for the thunk model, deferred.
- **Discriminated/tagged unions**: punted for v1.
- **Non-nullable pointer types**: `!` annotation, requires definite-initialization analysis, planned for post-v1.
- **Impl scoping rules**: who can declare an impl (package that defines the type? interface? either?)
- **Auto-generated interface files**: optionally generate interface files from source (not precluded, but not part of the core model).
- **Sentinel refcount details**: exact value for immortal/static managed data, interaction with overflow checking.
- **Optional optimizer modules**: compiler with varying optimization passes, relevant for self-hosting on small systems.
- **Annotations on control flow statements**: `#[likely] if cond { ... }`, `#[cold] for ...`, branch prediction hints. Natural extension of the annotation attachment model to statements.
- **Inline assembly syntax**: `#[asm("arch")]` proposed but details TBD (parameter-to-register mapping, clobber lists, whole-function vs. inline blocks).
- **Object file format strategy**: start with platform-native (ELF/Mach-O), possibly move to Binate-specific format with converters later.
- **Own linker**: needed eventually for hermetic builds and cross-compilation, deferred.

---

## 29. Design Philosophy Summary

The overarching philosophy that emerged through discussion:

- **Simple and approachable over safe and complex.** Willing to trade safety for power and simplicity. The language should be easy to learn without sacrificing low-level capability.
- **Trust the programmer, but provide ergonomic defaults.** Managed memory (refcounting) is the default; raw is the escape hatch. Not the other way around.
- **One concept over two.** Raw pointers serve as both raw-data-access and cycle-breaking (instead of adding weak refs). Interface files determine visibility (instead of per-symbol keywords).
- **Design for v1 simplicity but don't block v2 power.** Non-nullable pointers, generics, tagged unions, and atomic refcounts are all deferred but the v1 design is careful not to foreclose them.
- **The dual-mode story must be seamless.** Every design decision (memory model, type system, calling conventions) is evaluated partly on whether it supports transparent compiled/interpreted interop.
- **Simplicity of use vs. simplicity of implementation.** These are distinct and sometimes in tension. The managed/raw split is the minimum complexity to serve both: managed for ergonomics (especially REPL), raw for implementation simplicity and systems work.
- **Bridge the embedded gap.** Today, embedded developers use C for production and MicroPython for exploration — two separate worlds. Binate bridges this: one language for both, with the interpreter running alongside production code on the target device.
