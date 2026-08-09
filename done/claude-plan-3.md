# Binate — Phase 5b: Compiler (IR + LLVM Backend)

> **Status:** COMPLETE (shipped); kept for design rationale. Steps 1–15 are
> done, including self-compilation. This was the original LLVM-text / C-runtime
> backend, which has since evolved (multiple backends, runtime in Binate). The
> per-step landing logs, IR struct/OP_* dumps, and edit inventories that this
> doc used to carry have been removed — they live in the code now. What remains
> is the problem statement and the ratified design decisions, several of which
> are also captured (and kept current) in `claude-notes.md`.

> **NOTE (2026-03-31):** `append` has been fully removed from the Binate language.
> References to `append`, `OP_APPEND`, and `bn_append_*` runtime functions in this
> historical plan are outdated. See `claude-notes.md` for the current rationale
> (growable collections are a library concern).

This plan covers the compiler pipeline: Binate source → SSA IR → LLVM IR → native executable.

Reference documents:
- `claude-plan-2.md` — Phase 5a (self-hosted interpreter, now complete)
- `claude-plan-1.md` — Phases 1–4 (language design through bootstrap interpreter)
- `claude-notes.md` — current language-design decisions (string representation,
  managed-slice layout, name mangling, layout rules) — authoritative where it
  overlaps with this doc.
- `ir-backend-guidelines.md` — the IR/backend split and multi-backend work that
  superseded the single-LLVM-backend structure described here.

---

## Overview

The compiler reuses the existing frontend (lexer, parser, type checker) and adds two new packages:

1. **`pkg/ir`** — SSA-based intermediate representation + IR generation from typed AST
2. **`pkg/codegen`** — LLVM IR text emission + driver to invoke `clang` for linking

The bootstrapping chain:

```
Go bootstrap interprets → main.bn (compiler mode)
  main.bn uses: lexer → parser → types → ir → codegen
  codegen emits → .ll file (LLVM IR text)
  clang compiles → native executable
```

We emit LLVM IR as **text** (`.ll` files), not bitcode. This keeps the emitter simple — it's just string concatenation — and we can inspect the output directly. `clang` handles optimization, instruction selection, register allocation, and linking.

---

## `pkg/ir` — SSA Intermediate Representation

### Design Principles

1. **SSA form.** Every value is defined exactly once. Phi nodes at control flow merge points.
2. **Typed.** Every value carries its Binate type. Enables type-specific lowering.
3. **High-level enough.** Managed pointer operations, slice operations, and bounds checks are explicit IR instructions — not lowered to primitives yet.
4. **Target-independent.** No registers, no calling conventions, no instruction encodings.

The IR is organized as: Module → Functions → Blocks → Instructions. (Concrete
struct/OP_* definitions live in `pkg/ir`.)

### IR Generation Strategy

**Variables — alloca-heavy approach.** Each `var` declaration becomes an
`OP_ALLOC` (stack slot) + `OP_STORE`; reads/writes go through `OP_LOAD`/`OP_STORE`
on the alloc'd address. LLVM's `mem2reg` pass promotes these to SSA registers and
inserts phi nodes for us. This is the approach used by Clang, Go's SSA builder,
and most compiler frontends. **We do NOT generate `OP_PHI` ourselves** — this
dramatically simplifies IR generation, since we don't need to track SSA dominance
frontiers or insert phi nodes.

**Note on strings.** String literals are untyped. Once coerced to `*[]char` or
`[N]char`, they are just slices/arrays — `len()`, indexing, and slicing use the
standard slice/array operations. There is no `+` operator for strings; use
`Concat` from pkg/bootstrap.

---

## `pkg/codegen` — LLVM IR Emission

The codegen package translates our IR Module into LLVM IR text (`.ll` file). This is pure string emission — no LLVM library dependency.

### LLVM IR Mapping (type representation)

| Binate Type | LLVM IR Type |
|-------------|-------------|
| int, int64 | `i64` |
| int32 | `i32` |
| int16 | `i16` |
| int8, char, uint8 | `i8` |
| bool | `i1` |
| *T | `ptr` (opaque pointer) |
| @T | `ptr` (same as raw pointer at LLVM level; refcount is runtime) |
| *[]T | `{ ptr, i64 }` (data pointer + length) |
| [N]T | `[N x <elem>]` |
| struct { ... } | `{ <field1>, <field2>, ... }` or named `%StructName` |
| string / *[]char | `{ ptr, i64 }` (same as slice; backing data null-terminated for literals) |
| func(...)... | not first-class yet; direct calls only |

### Runtime Library

Some operations can't be emitted inline and need a small runtime library, linked
in by `clang` alongside the generated `.ll`. **Hybrid approach (chosen):** emit
simple operations inline (bounds checks, nil checks, arithmetic, control flow);
use runtime calls for allocation, string concat, and bootstrap builtins.

`pkg/bootstrap` is a special "builtin" package — it has no `.bn` implementation,
only a `.bni` interface. The bootstrap interpreter implements it in Go; for
compiled code it is implemented in C in `binate_runtime.c` (file I/O, string ops,
alloc, refcount, print helpers), using POSIX syscalls or libc.

### Build Pipeline

The compiler driver (`compile.bn`) orchestrates:

```
1. Parse source files          → AST
2. Type check                  → Typed AST
3. IR generation (pkg/ir)      → IR Module
4. LLVM emission (pkg/codegen) → .ll file on disk
5. Invoke: clang -O2 foo.ll runtime.c -o foo
6. Done: native executable
```

`compile.bn` writes the `.ll`, then auto-invokes `clang` via the `bootstrap.Exec`
builtin (`-o <name>`, `--emit-llvm`, `--runtime <path>`, `-v` flags; runtime
auto-discovered relative to the input file).

---

## Type Layout Computation

**Key principle: Binate defines its own layout rules. LLVM is just a backend — we
don't match its rules, we tell it what to do.** Struct types are emitted as
**packed** LLVM structs (`<{ ... }>`) with explicit `[N x i8]` padding fields
inserted by us, so layout is deterministic and backend-independent.

**Motivation:** the design (claude-notes.md) requires "same struct layouts — no
marshalling" between compiled and interpreted code. Layout must therefore be a
shared, language-level contract, not knowledge hardcoded in the backend.

`pkg/types` owns the layout functions:

```
func SizeOf(t @Type) int                  // size in bytes (includes trailing padding)
func AlignOf(t @Type) int                 // alignment requirement in bytes
func FieldOffset(t @Type, index int) int  // byte offset of field in struct
```

Binate layout rules (LP64, 64-bit targets):

| Type | Size | Align |
|------|------|-------|
| bool | 1 | 1 |
| int8, uint8, char | 1 | 1 |
| int16, uint16 | 2 | 2 |
| int32, uint32 | 4 | 4 |
| int, int64, uint, uint64 | 8 | 8 |
| pointer (raw, managed) | 8 | 8 |
| slice (raw) | 16 | 8 |
| managed slice | 24 | 8 |
| [N]T | N * SizeOf(T) | AlignOf(T) |
| struct { fields } | sum of field sizes + padding | max field align |

**Rule: alignment = min(size, word_size).** Fields are aligned to their natural
alignment. Struct size is rounded up to struct alignment (= max field alignment).

Example:
```
struct { a int8; b int64; c int8 }
  offset 0: a (1 byte) + 7 padding
  offset 8: b (8 bytes)
  offset 16: c (1 byte) + 7 padding
  total: 24 bytes, align 8
```

> The managed-slice size in the table above (24 = 3 words) predates the
> **4-word** managed-slice layout decided later; see `claude-notes.md`
> ("Managed-slice representation — DECIDED") for the current
> `(data, len, backing_refptr, backing_len)` layout.

**Deferred / future:**
- Interpreter flat byte buffers: once the interpreter is compiled natively,
  managed pointers become real native pointers with the same
  `[refcount | free_fn | payload]` header as compiled code, so struct values can
  use flat byte buffers with `SizeOf`/`FieldOffset` for field access — no
  marshalling for interop. While running under the bootstrap, the interpreter
  keeps its `Fields *[]@Value` representation (the bootstrap doesn't interop with
  compiled code).
- `#[packed]` annotation support.
- 32-bit target layout (word_size=4, changing pointer/int sizes and the
  alignment cap).

---

## Multi-Package Compilation & Self-Compilation

**Architecture decision: separate compilation per package.** Each package compiles
to its own `.ll` → `.o`, then all are linked together. This enables partial
recompilation (only rebuild changed packages) and scales better than merging
everything into one module.

The key enabler is a **consistent name mangling scheme** so cross-package
references resolve at link time:
- Functions: `pkg.Func` → `bn_pkg__Func` (e.g., `parser.New` → `bn_parser__New`)
- Struct types: `pkg.Type` → `%bn_pkg__Type` (e.g., `ast.File` → `%bn_ast__File`)
- The `main` package's `main()` → `@bn_main` (called by the C runtime's `main()`)
- Package-local (unexported) names still get mangled with the package prefix for
  uniqueness

Each package's `.ll` contains `define` for its own functions, `declare` for
functions it imports from other packages or the C runtime, and struct type
definitions for its own types plus any it references from other packages.
Constants from imported packages are inlined at IR-gen time (compile-time values).

The loader provides packages in dependency order; `.bni` files (available as
`Package.BNI`) are processed first to register types and function signatures from
all dependencies before processing implementation files.

### Gotchas encountered

- **Self-referential struct types** (`Node { val int; next @Node }`) require
  two-pass struct registration: register names first, populate fields second.
- **Chained managed-pointer field access** (`list.next.val`): `getSelectorType`
  must handle `TYP_MANAGED_PTR` for chained access, and the generator must
  dereference the managed ptr before field access at each link.
- **String-to-chars conversion for selector assignment** (`b.name = "test"` where
  the field is `*[]char`).
- **Struct allocas must be zero-initialized** (otherwise slice fields inside
  structs are left uninitialized).
- **Slice-of-slices** (`*[]*[]char`): `bootstrap.Args()` and `bootstrap.ReadDir()`
  return these; the runtime must unpack nested slices, and `Exec` takes
  `*[]*[]char` args.
- **Bootstrap string handling**: bootstrap functions use `*[]char` with
  null-terminator conventions; the C runtime must match these conventions.
- Self-compilation surfaced and fixed a `DECL_GROUP` import bug and a
  short-circuit `&&`/`||` evaluation bug.

---

## Open Questions & Decisions

### 1. println/print Implementation

`println`/`print` are **compiler intrinsics**, not regular function calls — they
expand differently based on argument types (which the type checker already knows):

- `println(intExpr)` → call `bn_print_int`
- `println(stringExpr)` → call `bn_write` + newline
- `println(boolExpr)` → call `bn_print_bool`

### 2. String Representation

String literals are **untyped constants** (like integer literals). They carry
null-terminated backing data and coerce based on context:

- **`*[]char` (or `*[]const char`):** Fat pointer `{ ptr, i64 }`. The slice view
  excludes the null terminator. `"hello"` → 5-element slice, but 6 bytes in
  backing data (`hello\0`). Same layout as any other slice.
- **`[N]char` (or `[N]const char`):** Fixed array that includes the null.
  `"hello"` → `[6]char` with contents `{'h','e','l','l','o','\0'}`. Conceptually:
  `var s [6]char = "hello"`.
- **Default (unforced context):** `*[]const char`.

A string-to-slice coercion is conceptually `cast([N+1]const char, "lit")[:N]` —
the backing data has the null, the slice view excludes it.

> Note: the current language design (claude-notes.md, "No `string` type") gives
> string literals the natural type `[N]readonly char` and default
> `@[]readonly char`, and disallows `*[]char` as a target. The `*[]char`
> conventions here reflect the bootstrap's stand-in (it lacks readonly types).

**LLVM emission:** String literals are emitted as `[N+1 x i8]` global constants
with trailing `\00`. A `*[]char` reference to one has `len = N`. An `[N+1]char`
reference includes the null.

**Concat** (runtime): Allocates `len(a) + len(b) + 1`, copies both, writes `\0`.
Returns slice with `len = len(a) + len(b)`.

**Slicing** doesn't maintain the null invariant — it produces general `*[]char`
values. Code that needs C interop on such strings must copy with a null
terminator.

There is **no `+` operator** for strings or slices. Use `Concat` from
pkg/bootstrap.

### 3. Slice Growth Policy

(Historical — `append` has since been removed; growth is now a library concern.)
The slice value is two words (`{ ptr, len }`), always-copy. A three-word
`{ ptr, len, cap }` form for amortized O(1) growth was contemplated for "when
performance matters."

### 4. Target Triple

For macOS ARM64: `target triple = "arm64-apple-macosx14.0.0"`
For Linux x86-64: `target triple = "x86_64-unknown-linux-gnu"`

Detect at compile time from the host, or accept as a flag.

### 5. Memory Management Strategy (Phased)

- **Phase 1:** Leak everything. `bn_alloc` = `malloc`, never free. (Used to get
  the conformance suite passing first; all conformance tests are short-lived.)
- **Phase 2 (shipped):** Reference counting. Two-word header
  `[refcount | free_fn_ptr | payload]`; `OP_REFCOUNT_INC/DEC` emitted at
  assignment/scope boundaries. Scope-based insertion: inc managed-ptr params at
  entry (callee owns a reference), dec managed-ptr locals before return (skip
  returned values) and at block-scope exit; on `p = newval` dec old + inc new
  (only when copying, not for fresh make/box/call results, distinguished by
  `isFreshManagedPtr`). Slice backing freed at scope exit / return for locals;
  params and returned slices skipped (caller / new owner owns the data).
- **Phase 3 (future):** Refcount elision via escape analysis — if a managed
  pointer doesn't escape the function, skip refcounting; `box(v)` whose result
  doesn't escape can become an alloca instead of a malloc. Requires an
  optimization pass over the IR.

  > Caution: per `feedback_refcount_transparency` and
  > `feedback_allocation_transparency`, refcounting is intentionally transparent
  > and deterministic and allocation is source-determined — any elision must not
  > change observable behavior or do cross-function refcount elision (it would
  > break dual-mode interop). Recursive release for managed-ptr fields in structs
  > and managed ptrs in slices is also future work.

---

## Future / Deferred Test Coverage

Test gaps noted at the time (some since addressed by the conformance suite):
- `@[]T` managed slices
- Nested arrays on the compiled backend
- Slice of slices, slice of structs
- Multi-return with managed pointers
