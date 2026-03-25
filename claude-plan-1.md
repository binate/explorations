# Binate — Plan to Nail Down the Language Design

This plan identifies the areas that need to be fully specified before we can write a formal grammar and begin implementing the bootstrap interpreter. It's organized into phases: things that must be resolved first (because other decisions depend on them), things that can be resolved in parallel, and things that can be deferred until after the bootstrap interpreter is underway.

Reference documents:
- `claude-notes.md` — running design decisions
- `claude-discussion-detailed-notes.md` — detailed rationales and alternatives considered
- `README.md` — project goals

---

## Phase 1: Core Language Specification

These must be resolved before writing a grammar. They are the foundational decisions that everything else depends on.

### 1.1 Primitive Types — DONE

```
int, uint                           // platform word size
int8, int16, int32, int64           // fixed-width signed
uint8, uint16, uint32, uint64      // fixed-width unsigned
float32, float64                    // floating point
bool                                // true, false
byte = uint8                        // alias
char = uint8                        // alias
```

- `int`/`uint` are platform word size. No `uintptr` (pointer size = word size on all targets).
- `int64`, `uint64`, `float32`, `float64` optional subject to hardware support.
- No unqualified `float`. `byte` and `char` are aliases for `uint8`.
- `nil` for null pointers/slices. `true`/`false` for bools.
- `any` is a built-in implicit interface, usable in type expressions: `*any`, `@any`.

### 1.2 Concrete Syntax — DONE

All major syntax decisions have been made. Summary:

#### Variable declarations
```
var x int              // zero-initialized
var x int = 5          // explicit init
x := 5                 // short declaration, type inferred
const x int = 5        // constant
```

#### Function declarations
```
func foo(a int, b int) int { ... }
func foo(a int, b int) (int, int) { ... }       // multiple returns
func (r *MyStruct) method(a int) int { ... }     // receiver
func (r *const MyStruct) read() int { ... }      // const receiver
```
No named return values. No same-type parameter shorthand.

#### Type & struct declarations
```
type Point struct { x int; y int }    // named struct (only way)
type Celsius float64                   // distinct type
type byte = uint8                      // alias
```

#### Enum-like constants (no first-class enums)
```
type Color uint8

const (
    Red   Color = iota    // 0
    Green                  // 1
    Blue                   // 2
)
```

#### Interface declarations
```
type Writer interface {
    write(buf []char) int
    close()
}
```

#### Impl declarations
```
impl *FileHandle : Writer, Reader     // raw pointer receiver
impl @FileHandle : Retainable         // managed pointer receiver
impl FileHandle : Stringer             // value receiver
```

#### Pointer syntax
```
*T              // raw pointer
@T              // managed pointer
&x              // take raw address
*p              // dereference
p.field         // auto-dereference (Go-style, no ->)
make(T)         // allocate managed, zero-init, returns @T
box(T{...})     // allocate managed with init, returns @T
```
Implicit `@T` → `*T` conversion. Never implicit `*T` → `@T`.

#### Slice syntax
```
[]T             // raw slice (ptr, length)
@[]T            // managed slice (managed ptr, raw ptr, length)
@([]T)          // managed pointer to raw slice (parens break sugar)
arr[low:high]   // slice expression, exclusive end
```

#### Generic syntax
```
type List[T any] struct { ... }
func sort[T Comparable](items []T) { ... }
sort[int](myArray)              // no type inference
```

#### Control flow
```
if cond { ... } else if cond { ... } else { ... }
for i := 0; i < n; i++ { ... }     // C-style
for cond { ... }                     // while-style
for { ... }                          // infinite
for item in collection { ... }       // range/iteration
switch x { case 1: ... default: ... }
switch { case x > 0: ... }          // condition-less
```
No fallthrough by default.

#### Const syntax (left-to-right reading)
```
const *int           // const pointer to int
*const int           // pointer to const int
const *const int     // const pointer to const int
[]const *int         // slice of const pointers to int
```

#### Type casts (builtins, not function-call syntax)
```
cast(int, y)              // value conversion
bit_cast(*int, rawAddr)   // reinterpret bits
```

#### Closures
```
f := func(x int) int { return x * 2 }
```
Always capture by value. Use managed pointers for shared mutable state.

#### Variadic functions
```
func println(args ...Stringer) { ... }     // raw interface — zero-overhead
func collect(args ...@Stringer) { ... }    // managed — retains args
```

#### Annotations
```
#[packed, align(4)]
type Foo #[packed] struct { ... }
#[tools.export] type Bar struct { ... }
```
Comma-separated, no stacking. Namespaced: unqualified = standard, `compiler.*`, `tool.*`.

#### Struct & array literals
```
Point{x: 1, y: 2}          // named fields
[3]int{1, 2, 3}            // array
[...]int{1, 2, 3}          // inferred size
[3]int{1}                   // partial → {1, 0, 0}
```

### 1.3 Package & Module System — DONE

- **Package declaration**: `package "pkg/foo"` (string-based, matches import path)
- **Import syntax**: `import "pkg/foo"`, `import alias "pkg/foo"`
- **File extensions**: `.bn` (implementation), `.bni` (interface)
- **Directory layout**: `pkg/foo.bni` (interface sibling), `pkg/foo/*.bn` (implementation)
- **One interface file per package**. Multiple `.bn` files per package.
- **Search path**: project root highest priority. `pkg/`-prefixed = public. Non-`pkg/` = local.
- **No `internal/`** — interface file existence controls visibility.
- **Main package**: `package "main"`, requires `main()`, no `.bni` needed.
- **Visibility**: no per-symbol keywords. In the `.bni` = public. Not in `.bni` = private.

### 1.4 Operator Set — DONE

**Arithmetic**: `+`, `-`, `*`, `/`, `%`
- Integer division truncates toward zero. `%` result has same sign as dividend.
- Division by zero: runtime trap. Overflow: wrapping (two's complement).

**Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>`
- `>>` arithmetic for signed, logical for unsigned. No `>>>`.
- Shift by >= bit width: defined (zero or sign-extended).

**Comparison**: `==`, `!=`, `<`, `>`, `<=`, `>=` — no chaining.

**Logical**: `&&`, `||`, `!` — short-circuit, `bool` operands only.

**Assignment**: `=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=` — statements, not expressions.

**Increment/decrement**: `x++`, `x--` — postfix only, statements only.

**Unary**: `-`, `~`, `!`, `*` (deref), `&` (address-of).

**Member access**: `.` only, auto-deref. No `->`. No operator overloading.

**Precedence** (highest to lowest): unary → `*`/`/`/`%` → `+`/`-` → `<<`/`>>` → `&` → `^` → `|` → comparisons → `&&` → `||`

---

## Phase 2: Detailed Semantics

These can be worked on in parallel, and are needed before the bootstrap interpreter can be fully functional, but don't block writing the grammar.

### 2.1 Scoping Rules — DONE

- Block scoping: every `{}` introduces a new scope
- Variable shadowing: allowed, compiler warns by default (suppressible)
- Top-level: `type`, `func`, `const`, `var`, `interface`, `impl`, `import` only. No bare expressions.
- Package-level `var` allowed (mutable globals). Init order: dependency-based, then source order.
- No `init()` functions — explicit initialization in `main`.

### 2.2 Memory Management Details — DONE

**Managed allocation layout** (2 words overhead):
```
[ refcount (uint) | free fn ptr | user data ... ]
                                  ^ managed pointer
```
- `@T` → `*T` is trivial (same address). Header at negative offsets.
- Free fn ptr: `free()` for heap, no-op for static/ROM data, custom for pool allocators.
- No destructor in header — compiler knows the type statically; interfaces carry drop in vtable.
- Static managed data: sentinel refcount (`UINT_MAX`), never decremented/freed.

**`make` semantics:**
```
make(Point)              // @Point, zero-init (takes a type)
make([100]int)           // @[100]int, fixed-size
make([]int, n)           // @[]int, runtime-sized

box(42)                  // @int (takes an expression)
box(x)                   // @T where x: T
box(Point{x: 1, y: 2})  // @Point, allocate with init
```
No capacity argument. Growing is a library concern.

### 2.3 Method Resolution & Dispatch — DONE

- One method per name per base type (no overloading on receiver kind)
- One level of auto-deref: `@T`/`*T` → look for methods on pointer type and on `T`
- Receiver conversion: `@T` → `*T` → `*const T` (implicit, safe direction). Never `*T` → `@T`.
- Value receivers implemented as `*const T` (never null). Avoids copying large structs.
- Interface declarations: `type Name interface { ... }`. Anonymous interfaces supported.
- Interface embedding: list names in body, means "is-a". `impl *T : Child` implies parent impls.
- Vtable layout: `[any][embed1 full vtable][embed2 full vtable][own methods]` — no dedup, uniform.
- Converting child → parent interface: adjust vtable pointer by fixed offset.
- Destructor lives in the `any` vtable entry (all interfaces implicitly extend `any`).

### 2.4 Generic Instantiation — DONE

- Type params on functions, structs, and interfaces: `[T Comparable]`
- Multiple constraints: define a named combined interface (no `+` syntax)
- No type inference — always explicit: `sort[int](myArray)`
- Monomorphized: each instantiation generates specialized code
- Body checked once against constraint; instantiation verifies type satisfies constraint
- No generic methods on types (Go's rule). Use free functions.
- No conditional impls for v1. Only specific instantiations can `impl`.
- Cross-package: generic bodies in `.bni` files (needed for instantiation).

### 2.5 String & Array Semantics — DONE

- String literals: `[]const char` by default. Null-terminated in storage, slice excludes null.
- Bounds checking: always on by default. Out-of-bounds = runtime trap.
- `unsafe_index(buf, i)` builtin for unchecked access in hot paths.
- Nil slices: slices can't be compared to `nil` (slices are value types, not pointers). Check `len(s) == 0` for empty. Use `*[]T`/`@[]T` for optional.
- Zero-based indexing. `s[low:high]` exclusive end. `s[:]`, `s[low:]`, `s[:high]` shorthand.
- `len()`: slice length field, or compile-time constant for `[N]T`.

---

## Phase 3: Formal Grammar

Once Phases 1 and 2 are sufficiently resolved:

### 3.1 Write an EBNF (or PEG) Grammar — DONE

- Covers all syntax from Phase 1
- Token types defined (keywords, identifiers, literals, operators, punctuation)
- Automatic semicolon insertion rules defined (Go-style)
- Operator precedence and associativity defined
- 11 disambiguation rules documented (D1–D11)
- Builtins (`make`, `box`, `cast`, `bit_cast`, `len`, `unsafe_index`) are keywords (not predeclared names)
- See `grammar.ebnf`

### 3.2 Identify the Bootstrap Subset — DONE

The bootstrap interpreter only needs to support enough of the language to run the Binate compiler/interpreter source. Identify what can be deferred:

**In the bootstrap subset:**
- Functions (non-generic)
- Structs, distinct types, aliases
- Basic types: `int`, `uint`, `bool`, `byte`/`char` (no floats)
- Variables: `var`, `:=`, assignment, compound assignment
- Constants: `const` (grouped, with `iota`)
- Control flow: `if`/`else`, `for` (all forms), `switch`/`case`
- Raw pointers (`*T`, `&`, `*p`, `.` auto-deref)
- Managed pointers (`@T`, `make`, `box`)
- Raw and managed slices, slice expressions
- String literals
- `cast`, `bit_cast`, `len`
- Multiple return values
- Basic I/O (file read/write, stdout)
- Package/import (simplified — no `.bni` enforcement)

**Deferred from bootstrap:**
- Generics (type parameters, constraints, instantiation)
- Interfaces, `impl`, methods with receivers
- Annotations (`#[...]`)
- Variadic functions (`...T`)
- Closures / function literals
- Float types (`float32`, `float64`)
- `unsafe_index`
- `const` in types (const pointers/slices)
- Function types as values (beyond simple function calls)
- REPL / retained-vs-immediate mode
- `.bni` interface file enforcement

See `grammar.ebnf` for the formal grammar with `[BOOTSTRAP]`/`[DEFERRED]` annotations.

---

## Phase 4: Bootstrap Interpreter Implementation (Go)

### 4.1 Lexer
- Tokenize the bootstrap subset
- Handle automatic semicolon insertion

### 4.2 Parser
- Parse the bootstrap subset into an AST
- Error recovery (at least basic)

### 4.3 Type Checker
- Type check the AST
- Handle the subset of the type system needed for bootstrap

### 4.4 Tree-Walking Interpreter
- Evaluate the AST directly
- Implement managed memory with refcounting (in Go, backed by Go's GC for the interpreter's own allocations, but tracking refcounts for Binate objects)
- Implement basic standard library (I/O, string operations, memory allocation)

### 4.5 Self-Test
- Write small Binate programs to test the interpreter
- Build up a test suite that will also serve the future compiler

---

## Phase 5: Writing the Real Compiler/Interpreter in Binate

(Beyond the scope of this plan, but the end goal of Phases 1-4.)

---

## Current Status & Next Steps

**Phase 1 is complete.** All core language specification items decided.
**Phase 2 is complete.** All detailed semantics decided.
**Phase 3 is complete.** Formal EBNF grammar written (`grammar.ebnf`) with bootstrap subset annotations.

**Next:**
1. **Begin Go implementation** (4.x) — lexer, parser, type checker, tree-walking interpreter
