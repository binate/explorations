# Binate — Phase 5b: Compiler (IR + LLVM Backend)

This plan covers the compiler pipeline: Binate source → SSA IR → LLVM IR → native executable.

Reference documents:
- `claude-plan-2.md` — Phase 5a (self-hosted interpreter, now complete)
- `claude-plan-1.md` — Phases 1–4 (language design through bootstrap interpreter)

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

## Part 1: `pkg/ir` — SSA Intermediate Representation

### Design Principles

1. **SSA form.** Every value is defined exactly once. Phi nodes at control flow merge points.
2. **Typed.** Every value carries its Binate type. Enables type-specific lowering.
3. **High-level enough.** Managed pointer operations, slice operations, and bounds checks are explicit IR instructions — not lowered to primitives yet.
4. **Target-independent.** No registers, no calling conventions, no instruction encodings.

### IR Structure

The IR is organized as: Module → Functions → Blocks → Instructions.

```
pkg/ir.bni:

// ============================================================
// Module — top-level compilation unit
// ============================================================

type Module struct {
    Name     []char
    Funcs    []@Func
    Globals  []@Global
    Types    []@TypeDef     // named struct types
}

type Global struct {
    Name     []char
    Typ      @types.Type
    Init     @Value          // constant initializer (nil = zero)
}

type TypeDef struct {
    Name     []char
    Typ      @types.Type     // the underlying struct type
}

// ============================================================
// Function
// ============================================================

type Func struct {
    Name     []char
    Params   []@Param
    Results  []@types.Type
    Blocks   []@Block
    // Locals  — not needed; SSA values *are* the locals
    IsExtern bool           // declared but not defined (bootstrap builtins)
}

type Param struct {
    Name     []char
    Typ      @types.Type
    ID       int            // SSA value ID
}

// ============================================================
// Basic Block
// ============================================================

type Block struct {
    Label    []char          // e.g., "entry", "if.then", "for.cond"
    Instrs   []@Instr
    // Terminated by exactly one terminator instruction (last in Instrs)
}

// ============================================================
// Instruction (SSA value + operation)
// ============================================================

type Instr struct {
    ID       int            // SSA value ID (unique within function; -1 for void instrs)
    Op       int            // operation (OP_* constant)
    Typ      @types.Type    // result type

    // Operands — usage depends on Op:
    Args     []@Instr       // input values (SSA references)
    Block1   @Block         // branch target / then block
    Block2   @Block         // else block / loop continue
    Blocks   []@PhiEntry    // phi node entries

    // Immediates:
    IntVal   int            // integer constant
    StrVal   []char         // string constant, global name, field name, function name
    BoolVal  bool           // boolean constant
    Index    int            // field index, element index

    // Type operand (for alloc, cast, etc.):
    TypeArg  @types.Type
}

type PhiEntry struct {
    Block    @Block         // predecessor block
    Val      @Instr         // value from that predecessor
}
```

### IR Operations (OP_* constants)

#### Constants
```
OP_CONST_INT        // IntVal = value, Typ = integer type
OP_CONST_BOOL       // BoolVal = value
OP_CONST_STRING     // StrVal = value (as []char)
OP_CONST_NIL        // nil value of type TypeArg
```

#### Arithmetic (binary: Args[0], Args[1])
```
OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_REM
OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR
OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE
OP_LAND, OP_LOR      // logical and/or (short-circuit in lowering)
```

#### Unary (Args[0])
```
OP_NEG              // -x
OP_NOT              // !x (logical not)
OP_BITNOT           // ~x (bitwise complement)
```

#### Memory
```
OP_ALLOC            // allocate local (returns pointer to stack slot); TypeArg = element type
OP_LOAD             // load from pointer: Args[0] = ptr
OP_STORE            // store to pointer: Args[0] = ptr, Args[1] = value (void result)
OP_GET_FIELD_PTR    // struct field pointer: Args[0] = struct ptr, Index = field index
OP_GET_ELEM_PTR     // array/slice element pointer: Args[0] = base, Args[1] = index
```

#### Managed Pointers
```
OP_BOX              // box(val): Args[0] = value → managed pointer
OP_MAKE             // make(T): TypeArg = type → managed pointer to zero value
OP_MAKE_SLICE       // make([]T, n): TypeArg = elem type, Args[0] = length → managed slice
OP_DEREF            // dereference: Args[0] = pointer → pointed-to value
OP_NIL_CHECK        // panic if nil: Args[0] = pointer (void result)
OP_REFCOUNT_INC     // inc refcount: Args[0] = managed ptr (void result)
OP_REFCOUNT_DEC     // dec refcount: Args[0] = managed ptr (void result)
```

#### Slices & Arrays
```
OP_SLICE_LEN        // len(slice): Args[0] = slice → int
OP_SLICE_PTR        // data pointer of slice: Args[0] = slice → raw ptr
OP_SLICE_GET        // slice[i]: Args[0] = slice, Args[1] = index → element
OP_SLICE_SET        // slice[i] = v: Args[0] = slice, Args[1] = index, Args[2] = value (void)
OP_SLICE_EXPR       // s[lo:hi]: Args[0] = slice, Args[1] = lo, Args[2] = hi → new slice
OP_BOUNDS_CHECK     // panic if out of bounds: Args[0] = index, Args[1] = length (void)
OP_APPEND           // append(slice, val): Args[0] = slice, Args[1] = value → new slice
```

#### Strings
```
OP_STRING_TO_CHARS  // string literal → []char: Args[0] → slice (excludes null)
OP_STRING_TO_ARRAY  // string literal → [N]char: Args[0] → array (includes null)
```

Note: String literals are untyped. Once coerced to `[]char` or `[N]char`, they
are just slices/arrays — `len()`, indexing, and slicing use the standard slice/array
operations. There is no `+` operator for strings; use `Concat` from pkg/bootstrap.

#### Control Flow (terminators — must be last in block)
```
OP_JUMP             // unconditional: Block1 = target
OP_BRANCH           // conditional: Args[0] = cond, Block1 = then, Block2 = else
OP_RETURN           // return: Args = return values (0, 1, or multiple)
OP_PANIC            // panic: Args[0] = message string (no return)
```

#### SSA
```
OP_PHI              // phi node: Blocks = [{Block, Val}, ...] — one per predecessor
```

#### Calls
```
OP_CALL             // direct call: StrVal = function name, Args = arguments
OP_CALL_BUILTIN     // bootstrap builtin call: StrVal = builtin name, Args = arguments
```

#### Conversions
```
OP_CAST             // numeric cast: Args[0] = value, TypeArg = target type
OP_BIT_CAST         // reinterpret bits: Args[0] = value, TypeArg = target type
```

#### Struct
```
OP_STRUCT_LIT       // construct struct: Args = field values in order, TypeArg = struct type
OP_EXTRACT          // extract multi-return value: Args[0] = multi, Index = which
```

### IR Generation Strategy

IR generation walks the typed AST and produces SSA instructions. Key patterns:

**Variables:**
- Each `var` declaration → `OP_ALLOC` (stack slot) + `OP_STORE` (initializer)
- Variable read → `OP_LOAD` from alloc'd address
- Variable write → `OP_STORE` to alloc'd address
- LLVM's `mem2reg` pass promotes these to SSA registers automatically

This is the **"alloca-heavy"** approach used by Clang, Go's SSA builder, and most compiler frontends. We don't need to build phi nodes ourselves — LLVM does it.

**Control flow:**
- `if/else` → `OP_BRANCH` to then/else blocks, both jump to merge block
- `for` → header block (condition), body block, post block, exit block; back-edge from post to header
- `switch` → chain of `OP_BRANCH` comparisons, or LLVM `switch` instruction
- `break/continue` → `OP_JUMP` to enclosing loop's exit/post block

**Function calls:**
- Direct calls → `OP_CALL` with function name and args
- Bootstrap builtins → `OP_CALL_BUILTIN` (resolved to external symbols at LLVM emission)
- Multi-return → `OP_CALL` returns aggregate, `OP_EXTRACT` picks elements

**Managed pointers:**
- `box(v)` → `OP_BOX`
- `make(T)` → `OP_MAKE`
- Field access on `@T` → `OP_NIL_CHECK` + `OP_DEREF` + `OP_GET_FIELD_PTR` + `OP_LOAD`
- Assignment through `@T` → `OP_NIL_CHECK` + `OP_DEREF` + `OP_GET_FIELD_PTR` + `OP_STORE`

**Note on phi nodes:** Since we use the alloca approach, we do NOT generate OP_PHI ourselves. Every variable is an alloca'd pointer, and reads/writes go through LOAD/STORE. LLVM's mem2reg pass converts these to SSA with phi nodes. This dramatically simplifies IR generation — we don't need to track SSA dominance frontiers or insert phi nodes.

---

## Part 2: `pkg/codegen` — LLVM IR Emission

### Approach

The codegen package translates our IR Module into LLVM IR text (`.ll` file). This is pure string emission — no LLVM library dependency.

### LLVM IR Mapping

#### Types

| Binate Type | LLVM IR Type |
|-------------|-------------|
| int, int64 | `i64` |
| int32 | `i32` |
| int16 | `i16` |
| int8, char, uint8 | `i8` |
| bool | `i1` |
| *T | `ptr` (opaque pointer) |
| @T | `ptr` (same as raw pointer at LLVM level; refcount is runtime) |
| []T | `{ ptr, i64 }` (data pointer + length) |
| [N]T | `[N x <elem>]` |
| struct { ... } | `{ <field1>, <field2>, ... }` or named `%StructName` |
| string / []char | `{ ptr, i64 }` (same as slice; backing data null-terminated for literals) |
| func(...)... | not first-class yet; direct calls only |

#### Managed Pointer Layout

At the LLVM level, a managed pointer is just a `ptr` to a heap-allocated block:

```
[ refcount (i64) | payload ... ]
          ^
          managed pointer points here (past the header)
```

Wait — actually, for the initial compiler, we can use a simpler model. Since we're targeting LLVM and can use its GC support or a simple malloc/free scheme:

**Option A: Leak everything (simplest, correct, slow)**
- `box(v)` → `malloc(sizeof(v))`, store v, return ptr
- No refcount, no free
- Works for initial testing; conformance suite programs are short-lived

**Option B: Refcounted (correct, production-quality)**
- Allocate `[header | payload]`, header contains refcount
- `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` emit atomic inc/dec
- When refcount hits zero, free

**Recommendation: Start with Option A.** Get the conformance suite passing first. Add refcounting as an optimization pass later — the IR already has the `OP_REFCOUNT_INC/DEC` slots.

#### Runtime Library

Some operations can't be emitted inline and need a small runtime library (in C or Binate, compiled separately):

```c
// binate_runtime.c (or equivalent)
void* bn_alloc(int64_t size);           // malloc wrapper
void  bn_free(void* ptr);              // free wrapper
void  bn_panic(char* msg, int64_t len); // print + exit(2)
void  bn_bounds_check(int64_t idx, int64_t len);  // panic if out of bounds

// Slice operations
typedef struct { void* data; int64_t len; } bn_slice;
bn_slice bn_append(bn_slice s, void* elem, int64_t elem_size);
bn_slice bn_slice_expr(bn_slice s, int64_t lo, int64_t hi, int64_t elem_size);

// String operations
bn_slice bn_string_concat(bn_slice a, bn_slice b);

// I/O (maps to pkg/bootstrap)
int64_t bn_open(char* path, int64_t path_len, int64_t flags);
int64_t bn_read(int64_t fd, char* buf, int64_t n);
int64_t bn_write(int64_t fd, char* buf, int64_t n);
int64_t bn_close(int64_t fd);
void    bn_exit(int64_t code);
// ... etc for all bootstrap builtins
```

This runtime is linked in by `clang` alongside the generated `.ll` file.

**Alternative: emit everything inline.** For simple operations (bounds check = compare + branch to panic block), we can emit them directly as LLVM IR. The runtime is only needed for things that are complex (append with growth, malloc).

**Hybrid approach (recommended):** Emit simple operations inline (bounds checks, nil checks, arithmetic, control flow). Use runtime calls for allocation, append, string concat, and bootstrap builtins.

#### Example: Hello World

Binate source:
```
package "main"

func main() {
    println("hello world")
}
```

Generated LLVM IR (approximate):
```llvm
; String constant — 12 bytes in memory (11 chars + null), slice len = 11
@.str.0 = private unnamed_addr constant [12 x i8] c"hello world\00"

; println emits directly to stdout
define void @main() {
entry:
    ; Write "hello world\n" to stdout (len=11, not including null)
    call i64 @bn_write(i64 1, ptr @.str.0, i64 11)
    ; Write newline
    call i64 @bn_write(i64 1, ptr @.str.newline, i64 1)
    ret void
}

@.str.newline = private unnamed_addr constant [2 x i8] c"\0A\00"

; Runtime declarations
declare i64 @bn_write(i64, ptr, i64)
```

#### Example: Managed Pointer

```
func main() {
    var p @Point = make(Point)
    p.X = 10
    println(p.X)
}
```

```llvm
%Point = type { i64, i64 }  ; X, Y

define void @main() {
entry:
    ; make(Point) — allocate on heap
    %p = call ptr @bn_alloc(i64 16)    ; sizeof(Point) = 2 * 8
    ; zero initialize
    store %Point zeroinitializer, ptr %p

    ; p.X = 10
    %x_ptr = getelementptr %Point, ptr %p, i64 0, i32 0
    store i64 10, ptr %x_ptr

    ; println(p.X)
    %x_val = load i64, ptr %x_ptr
    call void @bn_print_int(i64 %x_val)
    call void @bn_print_newline()

    ret void
}
```

### Codegen Package Structure

```
pkg/
  codegen.bni           // Codegen interface: Emit(module) → []char (LLVM IR text)
  codegen/
    emit.bn             // Main emitter: module → LLVM IR text
    emit_type.bn        // Type emission: Binate type → LLVM type string
    emit_instr.bn       // Instruction emission: IR instr → LLVM instruction
    emit_func.bn        // Function emission: IR func → LLVM function
    runtime.bn          // Runtime function declarations
    codegen_test.bn     // Tests
```

### Build Pipeline

The compiler driver (`main.bn` in compiler mode) orchestrates:

```
1. Parse source files         → AST
2. Type check                 → Typed AST
3. IR generation (pkg/ir)     → IR Module
4. LLVM emission (pkg/codegen) → .ll file on disk
5. Invoke: clang -O2 foo.ll runtime.c -o foo
6. Done: native executable
```

For step 5, `main.bn` calls `bootstrap.Write` to write the `.ll` file, then the user runs `clang` manually (or we shell out if we add a `bootstrap.Exec` builtin).

---

## Part 3: Implementation Plan

### Step 1: IR Data Structures (`pkg/ir`) — DONE

Defined IR types: Module, Func, Block, Instr, and 55 OP_* constants.
Constructors, emitters, helpers. 34 unit tests.

**Deliverable:** `pkg/ir.bni` + `pkg/ir/ir.bn` + `pkg/ir/ir_test.bn`.

### Step 2: IR Generation — DONE (broad coverage)

Implemented `pkg/ir/gen.bn` covering:
- Functions with params and return types
- Variables (var decl, short var, assignment)
- Arithmetic, comparison, logical, unary, bitwise ops
- If/else, for loops, break/continue, inc/dec
- println/print expansion to type-specific runtime calls
- Function calls (direct + package-qualified)
- Cast, len, append, panic builtins
- String, int, bool, char, nil literals

**Deliverable:** `pkg/ir/gen.bn` — AST-to-IR generation.

### Step 3: LLVM Emission — DONE (matches gen.bn coverage)

Emits LLVM IR text for all operations gen.bn produces:
- String constants as global arrays with GEP
- Named SSA values (%v0, %v1, ...) to avoid sequential numbering requirement
- alloca/load/store for variables, function params
- Arithmetic, comparison, logical, unary ops
- Branch, jump, return control flow
- Direct calls and void builtin calls
- Cast (sext/zext/trunc)
- `main` renamed to `bn_main` (C runtime provides `main()`)

**Deliverable:** `pkg/codegen/emit.bn` + `pkg/codegen.bni`.

### Step 4: Runtime Library — DONE (minimal)

`runtime/binate_runtime.c`:
- `bn_print_string(char*)` — fputs
- `bn_print_int(i64)` — printf
- `bn_print_bool(i1)` — prints "true"/"false"
- `bn_print_newline()` — prints newline
- `bn_exit(i64)` — exit
- `main()` → calls `bn_main()`

### Step 5: End-to-End Test — DONE

Compiler driver: `compile.bn` (parse → IR gen → LLVM emit → stdout).
Pipeline: `bootstrap compile.bn -- input.bn > out.ll && clang -o out out.ll runtime/binate_runtime.c`

Tested with: hello world, arithmetic, variables, if/else, for loops, function calls, recursion (fibonacci), factorial.

TODO: Add `compiled` mode to `conformance/run.sh`.

### Steps 6–7: Arithmetic, Variables, Control Flow — DONE

All included in the initial gen.bn implementation above.
Tested: `2+3*4=14`, `fib(10)=55`, `factorial(7)=5040`, variable mutation, if/else branching, for loops with `sum(1..10)=55`.

### Step 8: Structs & Pointers

Add:
- `OP_STRUCT_LIT`, `OP_GET_FIELD_PTR`, `OP_LOAD`/`OP_STORE` for fields
- Raw pointers: `OP_ALLOC` + address-of
- Managed pointers: `OP_MAKE`, `OP_BOX`, `OP_DEREF`, `OP_NIL_CHECK`

**Target:** Conformance tests 011–013 (structs, pointers, managed_ptr).

### Step 9: Slices & Arrays

Add:
- Slice runtime: `bn_append`, `bn_slice_expr`
- `OP_SLICE_GET`/`SET`, `OP_SLICE_LEN`, `OP_BOUNDS_CHECK`
- `OP_APPEND`, `OP_SLICE_EXPR`
- Array literals and indexing

**Target:** Conformance tests 009, 014, 016 (slices, arrays, for-in).

### Step 10: Strings & Remaining Features

Add:
- String operations: concat, len, indexing, slicing
- Switch statements
- Bitwise operations
- Type declarations, const/iota
- Compound assignment, inc/dec
- Multi-return

**Target:** All 25 conformance tests passing.

### Step 11: Self-Compilation Readiness

Once conformance passes, try compiling the compiler itself:
- Compile each package (token, ast, lexer, parser, types, ir, codegen)
- This is the ultimate test and the gateway to Phase 5c

---

## Open Questions & Decisions

### 1. println/print Implementation

The interpreter's `println` is a builtin that does type-dispatched formatting. The compiler needs to do this at compile time:

- For `println(intExpr)` → emit call to `bn_print_int`
- For `println(stringExpr)` → emit call to `bn_write` + newline
- For `println(boolExpr)` → emit call to `bn_print_bool`

This means `println` is not a regular function call — it's a compiler intrinsic that expands differently based on argument types. The type checker already knows the types, so this is straightforward.

### 2. String Representation

String literals are **untyped constants** (like integer literals). They carry null-terminated backing data and coerce based on context:

- **`[]char` (or `[]const char`):** Fat pointer `{ ptr, i64 }`. The slice view excludes the null terminator. `"hello"` → 5-element slice, but 6 bytes in backing data (`hello\0`). Same layout as any other slice.
- **`[N]char` (or `[N]const char`):** Fixed array that includes the null. `"hello"` → `[6]char` with contents `{'h','e','l','l','o','\0'}`. Conceptually: `var s [6]char = "hello"`.
- **Default (unforced context):** `[]const char`.

A string-to-slice coercion is conceptually `cast([N+1]const char, "lit")[:N]` — the backing data has the null, the slice view excludes it.

**Current status:** The self-hosted interpreter enforces the null-terminator invariant for `VAL_STRING` and handles `[]char` coercion. The `[N]char` coercion path exists in `coerce()` but is currently unreachable — the type checker does not yet allow string-to-array assignment. The bootstrap interpreter does not enforce the invariant (not needed since it won't interop with compiled code).

**LLVM emission:** String literals are emitted as `[N+1 x i8]` global constants with trailing `\00`. A `[]char` reference to one has `len = N`. An `[N+1]char` reference includes the null.

**Concat** (runtime): Allocates `len(a) + len(b) + 1`, copies both, writes `\0`. Returns slice with `len = len(a) + len(b)`.

**append() and slicing** don't maintain the null invariant — they produce general `[]char` values. Code that needs C interop on such strings must copy with a null terminator.

There is **no `+` operator** for strings or slices. Use `Concat` from pkg/bootstrap.

### 3. Slice Growth Policy

`append` needs to grow the backing array when capacity is exceeded. Options:
- **Always copy:** Simple, correct, O(n) per append. Fine for now.
- **Capacity tracking:** `{ ptr, len, cap }` three-word slice. Amortized O(1) append. Add later.

Start with always-copy (two-word slice: `{ ptr, len }`). Switch to three-word when performance matters.

### 4. Bootstrap Builtins in Compiled Code

The interpreter calls bootstrap builtins through a forwarding layer. The compiler needs them as actual linked functions:

- Write `binate_runtime.c` (or `.ll`) implementing all `pkg/bootstrap` functions
- Use POSIX syscalls or libc for I/O
- Link with the compiled program

### 5. Target Triple

For macOS ARM64: `target triple = "arm64-apple-macosx14.0.0"`
For Linux x86-64: `target triple = "x86_64-unknown-linux-gnu"`

Detect at compile time from the host, or accept as a flag.

### 6. Memory Management Strategy (Phased)

- **Phase 1 (now):** Leak everything. `bn_alloc` = `malloc`, never free. All conformance tests are short-lived.
- **Phase 2:** Reference counting. Add header before each allocation, emit inc/dec at assignment boundaries. Use the IR's `OP_REFCOUNT_INC/DEC` instructions.
- **Phase 3:** Refcount elision. Escape analysis determines which pointers don't need counting.

---

## Package Layout

```
binate/
  pkg/
    ir.bni                    IR types and generation interface
    ir/
      ir.bn                   IR data structures, constructors, OP_* constants
      gen.bn                  AST → IR generation
      gen_expr.bn             Expression IR generation
      gen_stmt.bn             Statement IR generation
      gen_test.bn             Unit tests

    codegen.bni               Codegen interface
    codegen/
      emit.bn                 LLVM IR text emission (module level)
      emit_type.bn            Binate type → LLVM type string
      emit_instr.bn           IR instruction → LLVM instruction text
      emit_func.bn            IR function → LLVM function text
      codegen_test.bn         Unit tests

  runtime/
    binate_runtime.c          C runtime (alloc, I/O, panic, slice ops)
    binate_runtime.h          Header for runtime

  conformance/
    run.sh                    Updated with `compiled` mode
```

---

## Success Criteria

1. **Milestone 1:** Hello world compiles and runs (`001_hello`).
2. **Milestone 2:** Arithmetic + variables + functions work (`001`–`008`).
3. **Milestone 3:** All 25 conformance tests pass as compiled binaries.
4. **Milestone 4:** Compiler can compile non-trivial programs (selftest.bn).
5. **Milestone 5 (Phase 5c):** Compiler compiles itself.
