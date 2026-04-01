# Binate Bootstrap Interpreter — Language Subset Reference

This document describes the subset of the Binate language supported by the Go bootstrap
interpreter (`github.com/binate/bootstrap`), including what's supported, what's not,
known bugs/inconsistencies versus the intended language spec, and migration notes.

---

## Primitive Types

**Supported:**
- Signed integers: `int`, `int8`, `int16`, `int32`, `int64`
- Unsigned integers: `uint`, `uint8`, `uint16`, `uint32`, `uint64`
- Aliases: `byte` = `uint8`, `char` = `uint8`
- `bool` (`true`, `false`)

**Not supported:**
- `float32`, `float64` — no floating-point types at all
- `any` as a type (reserved but not usable as a value type)

**Note:** `int` and `uint` are 64-bit in the bootstrap (Go's native word size). The full
language defines them as platform word-size (32-bit on 32-bit targets).

---

## String Handling

There is **no `string` type** in Binate. A string literal of length N is always stored
as N+1 bytes (including a null terminator). The **natural type** is `[N+1]char` (or
`[N+1]const char` in the full language). The **default type** is `[]const char` (or
`[]char` in the bootstrap, which lacks const-qualified types).

When taken as a slice (`[]char` or `[]const char`), the literal behaves as if it were
the underlying array sliced to exclude the null — i.e., `cast([N+1]char, "...")[:N]`.
The slice view has `len` = N, but the underlying storage always retains the null
terminator immediately after the slice data, ensuring C interop safety.

The bootstrap has an internal `StringLitType` for type-checking string literals. The
type checker treats `string` and `[]char` as mutually assignable for convenience, but
self-hosted code must use `[]char` exclusively.

**String operations:**
- String literals: `"hello"` — stored as 6 bytes (`hello\0`), natural type `[6]char`;
  as a slice (the default), `len()` = 5 (null excluded from view, but still in storage)
- Character literals: `'a'` — produces a `uint8`/`char` value
- Escape sequences: `\n`, `\r`, `\t`, `\\`, `\"`, `\'`, `\xNN`, `\uNNNN`
- Indexing: `s[i]` — returns `char` (bounds-checked)
- Slicing: `s[lo:hi]`, `s[:hi]`, `s[lo:]`, `s[:]`
- **No `+` for string concatenation** — use `bootstrap.Concat(a, b)`
- String comparison: `==`, `!=`, `<`, `>`, `<=`, `>=` all work

---

## Composite Types

### Structs

Fully supported:
- Named structs: `type Point struct { x int; y int }`
- Anonymous structs: `struct { x int; y int }`
- Struct literals: `Point{x: 1, y: 2}` (named), `Point{1, 2}` (positional), `Point{}` (zero-init)
- Partial initialization: `Point{x: 1}` — unspecified fields are zero-initialized
- Field access: `s.field` with auto-deref through pointers (`p.field` where `p` is `*Point` or `@Point`)

### Arrays

Fully supported:
- Fixed-size: `[3]int`, `[10]char`
- Literals: `[3]int{1, 2, 3}`
- Inferred size: `[...]int{1, 2, 3}`
- Partial init: `[3]int{1}` produces `{1, 0, 0}`
- Zero-init: `[3]int{}`
- Indexing and slicing (bounds-checked)
- `len()` returns compile-time constant

**Not supported:**
- Indexed literals: `[5]int{1: 10, 3: 30}` — not implemented in bootstrap

### Slices

Fully supported:
- Raw slices: `[]T` — two words (data pointer, length)
- Managed slices: `@[]T` — three words (data pointer, length, refpointer)
- Slice expressions: `arr[lo:hi]`, `arr[:hi]`, `arr[lo:]`, `arr[:]`
- `len(s)` returns length
- Indexing: `s[i]` (bounds-checked)
- `@[]T` → `[]T` implicit conversion (managed to raw)

**Not supported:**
- Nil comparison: slices cannot be compared to `nil` (check `len(s) == 0` instead)

---

## Pointer Types

### Raw Pointers (`*T`)

- `&x` — take address of a variable, field, or array element
- `*p` — dereference (nil-checked at runtime)
- `p.field` — auto-deref for struct field access
- Nil literal: `nil`
- Comparison: `p == nil`, `p != nil`, `p == q`

### Managed Pointers (`@T`)

- `make(T)` — allocate zero-initialized `@T`
- `box(expr)` — allocate and copy value into `@T`
- Reference-counted: refcount bumped on copy, decremented on scope exit
- Implicit conversion: `@T` → `*T` (safe direction)
- Nil-checked on dereference

**Not supported:**
- Pointer arithmetic / pointer indexing: `ptr[i]` panics with "pointer indexing not supported"
- Interior pointers to managed struct fields (conceptually possible but risky)
- `unsafe_index()` — reserved keyword but not implemented

---

## Allocation Builtins

| Builtin | Signature | Returns | Description |
|---------|-----------|---------|-------------|
| `make(T)` | type argument | `@T` | Zero-initialized managed allocation |
| `make_slice(T, n)` | element type + size | `@[]T` | Runtime-sized managed slice |
| `box(expr)` | value expression | `@T` | Copy value into managed allocation |
| `cast(T, expr)` | target type + value | `T` | Type conversion (truncating for integers) |
| `bit_cast(T, expr)` | target type + value | `T` | Reinterpret bits (same as `cast` in bootstrap) |
| `len(x)` | slice/array/string | `int` | Length |

---

## Operators

### Arithmetic
`+`, `-`, `*`, `/`, `%`
- Integer only (no floats)
- Division by zero: runtime panic
- Modulo by zero: runtime panic
- Integer overflow: **silent wrapping** (Go int64 semantics), no detection or error

### Bitwise
`&`, `|`, `^`, `~`, `<<`, `>>`
- `>>` is arithmetic for signed, logical for unsigned
- `~` is bitwise complement (unary)

### Comparison
`==`, `!=`, `<`, `>`, `<=`, `>=`
- Works on integers, booleans, strings, pointers, nil
- No chaining: `a < b < c` is a parse error

### Logical
`&&`, `||`, `!`
- **Short-circuit evaluated**: `&&` skips RHS if LHS is false; `||` skips RHS if LHS is true
- Operands must be `bool`

### Assignment
`=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`
- Assignment is a statement, not an expression

### Increment/Decrement
`x++`, `x--`
- Postfix only, statement only (not an expression)
- No prefix `++x` or `--x`

### Unary
`-` (negation), `~` (complement), `!` (logical not), `*` (deref), `&` (address-of)

### Member Access
`.` only — auto-dereferences through `*T` and `@T`. No `->`.

### Precedence (highest to lowest)
1. Unary: `!`, `~`, `-`, `*`, `&`
2. Multiplicative: `*`, `/`, `%`
3. Additive: `+`, `-`
4. Shift: `<<`, `>>`
5. Bitwise AND: `&`
6. Bitwise XOR: `^`
7. Bitwise OR: `|`
8. Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
9. Logical AND: `&&`
10. Logical OR: `||`

---

## Declarations

### Variables
```
var x int
var x int = 5
var x = 5           // type inferred
x := 5              // short declaration
```

Grouped: `var ( x int; y int = 3 )`

### Constants
```
const x = 5
const x int = 5
```

Grouped with `iota`:
```
const (
    A = iota    // 0
    B           // 1
    C           // 2
)
```

- `iota` starts at 0, increments for each entry in the group
- Bare names (no explicit value) repeat the previous expression with incremented `iota`
- Expressions using `iota` work: `1 << iota`, `iota + 10`, etc.

### Type Declarations
```
type Celsius int          // distinct type (requires cast() to convert)
type byte = uint8         // alias (fully interchangeable)
type Point struct { ... } // named struct
```

Grouped: `type ( ... )`

### Functions
```
func add(a int, b int) int { return a + b }
func divmod(a int, b int) (int, int) { return a / b, a % b }
```

- Multiple return values via tuple syntax
- Destructuring: `q, r := divmod(10, 3)`
- No named return values
- No same-type parameter shorthand (no `a, b int`)

**Not supported:**
- Method receivers: `func (p *Point) translate(...)` — not in bootstrap
- Variadic parameters: `func f(args ...int)` — not in bootstrap
- Function literals / closures: `func(x int) int { ... }` — not in bootstrap
- Function types as values — functions cannot be stored in variables or passed as arguments

---

## Statements

### If/Else
```
if x > 0 {
    // ...
} else if x < 0 {
    // ...
} else {
    // ...
}
```
No init statement in `if` (no `if x := f(); x > 0`).

### For Loops

Four forms:
```
for { ... }                         // infinite
for cond { ... }                    // while-style
for init; cond; post { ... }        // C-style
for v in collection { ... }         // for-in (index optional)
for i, v in collection { ... }      // for-in with index
```

For-in works on slices and arrays only. Anything else panics at runtime.

`break` and `continue` are supported (unlabeled only).

### Switch
```
switch x {
case 1, 2:
    // ...
case 3:
    // ...
default:
    // ...
}
```

- No fallthrough — once a case matches, it executes and exits
- Multiple values per case: `case 1, 2, 3:`
- `default` case for no match
- Case values can be any comparable expression

### Return
```
return
return x
return x, y
```

### Block Scoping
Every `{ }` introduces a new lexical scope. Variable shadowing is allowed.

---

## Package System

### Declaration
```
package "pkg/foo"
```

### Imports
```
import "pkg/foo"              // use as foo.Symbol
import myname "pkg/foo"       // aliased import
```

### File Organization
- `.bn` — implementation files
- `.bni` — interface files (public API declarations)
- `*_test.bn` — test files (excluded from normal builds)
- Multiple `.bn` files in one directory form a single package
- Interface file is sibling to implementation directory:
  ```
  pkg/
    foo.bni          // interface
    foo/             // implementation
      impl1.bn
      impl2.bn
  ```

### Dependency Resolution
- Topological sort of import graph
- Cycle detection (error on cycles)
- Two-pass type checking: collect declarations, then resolve/validate

### Built-in Package: `pkg/bootstrap`

Provided by the Go runtime, not `.bn` files:

**File I/O:**
- `Open(path []char, flags int) int` — returns fd (-1 on error)
- `Read(fd int, buf []uint8, n int) int` — read up to n bytes
- `Write(fd int, buf []uint8, n int) int` — write n bytes
- `Close(fd int) int` — close file descriptor
- `ReadDir(path []char) [][]char` — list directory entries
- `Stat(path []char) int` — 0=not found, 1=file, 2=directory

**String Utilities:**
- `Itoa(v int) []char` — integer to decimal string
- `Concat(a []char, b []char) []char` — string concatenation

**Process:**
- `Exit(code int)` — exit process
- `Exec(prog []char, args [][]char) int` — run subprocess, return exit code
- `Args() [][]char` — command-line arguments (after `--`)

**Constants:**
- `O_RDONLY`, `O_WRONLY`, `O_RDWR`, `O_CREATE`, `O_TRUNC`, `O_APPEND`
- `STDIN`, `STDOUT`, `STDERR`

---

## Other Builtins

### print / println
```
print(arg1, arg2, ...)
println(arg1, arg2, ...)
```
Variadic, accepts any type. `println` appends a newline.

### panic
```
panic("error message")
```
Raises a runtime error with the given message. Not recoverable.

---

## Testing

- Test files: `*_test.bn`
- Test functions: `TestXxx() testing.TestResult`
- `testing.TestResult` is an alias for `[]char`
- Pass: return `""` (empty string)
- Fail: return a non-empty error message string
- Run: `binate -test [-root dir] pkg/foo [pkg/bar ...]`
- Output format: Go-style (`=== RUN`, `--- PASS`/`--- FAIL`, summary)

---

## Automatic Semicolon Insertion (ASI)

The lexer inserts a semicolon before a newline when the preceding token is one of:
- `IDENT`, `INT`, `STRING`, `CHAR`
- `TRUE`, `FALSE`, `NIL`
- `BREAK`, `CONTINUE`, `RETURN`
- `++`, `--`
- `)`, `]`, `}`

This matches Go's ASI rules. A semicolon is also inserted at EOF if the last token
triggers ASI.

---

## Error Handling

No language-level error handling. No exceptions, no panic/recover (aside from the
`panic()` builtin which terminates execution). Errors are values — return them as part
of a multiple-return tuple:
```
result, err := doSomething()
if err != 0 {
    // handle error
}
```

---

## Runtime Error Detection

The bootstrap interpreter detects and reports these runtime errors with source positions:
- Division by zero
- Modulo by zero
- Nil pointer dereference
- Index out of bounds (slices, arrays, strings)
- `panic()` calls
- Pointer indexing (not supported)

---

## Features NOT in the Bootstrap Subset

The following features are part of the full Binate language (per the grammar and design
notes) but are **not implemented** in the Go bootstrap interpreter:

### Interfaces
No `interface` type declarations, no interface values, no interface embedding, no
dynamic dispatch. The keywords `interface` and `impl` are reserved but deferred.

### Methods and Impl Declarations
No method receivers (`func (r T) name(...)`), no `impl Type : Interface` declarations.
All functions are free-standing. Struct field access uses `.` but there is no method
resolution.

### Generics
No type parameters, no constraints, no generic instantiation. The self-hosted code
works around this with concrete types per combination (e.g., separate map-like
structures for different key/value types).

### Function Literals and Closures
No anonymous functions, no closures. Functions cannot be stored in variables or passed
as arguments. The self-hosted code uses switch statements and explicit dispatching
instead of function tables.

### Variadic Parameters and Spread
User-defined variadic functions (`func f(args ...T)`) are not supported. The spread
operator (`slice...`) is not supported. Only the built-in `print`, `println`, and
`print` and `println` are variadic.

### Const-Qualified Types
No `const` modifier on types: no `*const T`, no `[]const char`, no const receivers.
String literals are `[]char` rather than `[]const char`.

### Float Types
`float32` and `float64` are not available. All arithmetic is integer-only.

### Annotations
The `#[...]` annotation system is not implemented. No compiler directives, no
`#[packed]`, no `#[asm(...)]`.

### unsafe_index
Reserved as a keyword but not functional. All indexing is bounds-checked; there is
no way to opt out in the bootstrap.

### Function Types as Values
Functions are not first-class values. You cannot store a function in a variable, pass
it as an argument, or return it from another function.

### Inline Assembly
Not implemented. Deferred pending full compiler backend.

---

## `append()` — REMOVED

**`append()` has been fully removed from the Binate language.** It has been removed from
the parser, type checker, IR gen, codegen, both interpreters, all source code, tests,
and conformance tests. Using `append()` is now a compile/interpret error.

### Why It Was Removed

`append` was a performance footgun. It copied the entire slice on every call (O(n) per
append, O(n^2) for incremental building). It did not fit the language's design philosophy
of making costs visible.

### Replacements

- **`buf.CharBuf`** for building strings incrementally (backed by `@[]char` with geometric growth)
- **`make_slice(T, n)`** + indexed assignment for known-size allocations
- Per-type append helpers that do O(n) copy for other element types
- **`Vec[T]`** (post-generics) for general growable lists

### Bootstrap Behavior (for reference only)

In the bootstrap, `append(slice, elem1, elem2, ...)` creates a new slice with all
elements. If the input is a managed slice (`@[]T`), the result is also managed with a
fresh refcount. The implementation uses Go's built-in append (geometric growth), so it
doesn't exhibit O(n^2) behavior *within the bootstrap itself* — but the Binate-level
semantics are still copy-per-call, and the compiled codegen path would be O(n) per call.

---

## Known Bugs and Inconsistencies

These are differences between the bootstrap interpreter's behavior and the intended
language specification.

### 1. `bit_cast()` is identical to `cast()`

In the full language, `cast(T, expr)` performs value conversion (e.g., float-to-int
truncation, integer narrowing) while `bit_cast(T, expr)` reinterprets the raw bits
without conversion. In the bootstrap, both use the same code path — `bit_cast` is
simply an alias for `cast`. This doesn't cause problems in practice because the
bootstrap lacks floats and the integer-to-integer cases are the same for both, but it
is semantically incorrect.

### 2. `string` exists as an internal type

The bootstrap has `StringLitType` as a distinct internal type and treats `string` and
`[]char` as interchangeable via special-casing in `AssignableTo()`. In the real
language, there is no `string` type at all — string literals have natural type
`[N+1]const char` and default to `[]const char` (or `[]char` in the bootstrap).
Self-hosted code must not rely on `string` as a type name.

### 3. `int`/`uint` are always 64-bit

The bootstrap runs on the host (64-bit) and uses Go's `int64` for all `int`/`uint`
values. The full language defines `int`/`uint` as platform word-size (32-bit on 32-bit
targets). Code that assumes 64-bit `int` may break on 32-bit targets.

### 4. No const-qualification means weaker type safety

Without `const` types, the bootstrap cannot enforce immutability. String literals are
`[]char` (mutable) rather than `[]const char` (immutable), and their natural type is
`[N+1]char` rather than `[N+1]const char`. Code can mutate data that should be
read-only. This is a known and accepted limitation of the bootstrap subset.

### 5. Integer overflow is silent

The spec says integer overflow is wrapping (two's complement), which the bootstrap
implements correctly via Go's native int64 behavior. However, the bootstrap performs
no overflow checking even for literal-to-type assignments at type-check time. The
full language requires compile-time overflow checking for literals assigned to explicit
types (e.g., `var x uint8 = 256` should be a compile error).

### 6. No indexed array literals

The full language supports indexed array literals (`[5]int{1: 10, 3: 30}`), but the
bootstrap parser does not handle this syntax.

### 7. Pointer indexing panics instead of working

The full language supports pointer indexing (`ptr[i]`) for raw pointers (equivalent
to C's `ptr[i]`). The bootstrap explicitly panics on this with "pointer indexing not
supported". This is a known limitation, not a bug — it was intentionally deferred.

### 8. No `if` init statements

The full language (per the grammar) supports `if x := f(); x > 0 { ... }`. The
bootstrap parser does not handle this form.

### 9. No labeled break/continue

The full language supports labeled loops with `break label` and `continue label`. The
bootstrap only supports unlabeled `break` and `continue`.

### 10. Value semantics for function arguments

The bootstrap copies structs and arrays when passing them as function arguments (value
semantics), which is correct per the spec. However, the full compiled language will need
the optimization of passing large value-type arguments by `*const T` pointer under the
hood (the "value receivers implemented as `*const T`" rule). The bootstrap doesn't
need this optimization since it's interpreted.

### 11. for-in iteration is limited

For-in only works on slices and arrays. The full language may extend for-in to work
with user-defined iterable types (via interfaces). Attempting to use for-in on
anything other than a slice or array panics at runtime rather than producing a
type-check error.

### 12. No exhaustive switch checking

Switch statements have no exhaustiveness checking, even for values of a distinct
integer type that acts as an enum (e.g., `type Opcode uint8` with `const` values).
This matches the spec (exhaustiveness is a linter concern), but is worth noting.
