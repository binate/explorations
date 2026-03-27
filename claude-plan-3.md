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
    emit_func.bn        // IR function → LLVM function text
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

### Steps 6–7: Arithmetic, Variables, Control Flow — DONE

All included in the initial gen.bn implementation above.
Tested: `2+3*4=14`, `fib(10)=55`, `factorial(7)=5040`, variable mutation, if/else branching, for loops with `sum(1..10)=55`.

### Step 8: Structs & Pointers — DONE

- `OP_STRUCT_LIT`, `OP_GET_FIELD_PTR`, `OP_LOAD`/`OP_STORE` for fields
- Raw pointers: `OP_ALLOC` + address-of
- Managed pointers: `OP_MAKE`, `OP_BOX`, `OP_DEREF`, `OP_NIL_CHECK`

**Tested:** Conformance tests 011–013 (structs, pointers, managed_ptr).

### Step 9: Slices & Arrays — DONE

- Slice runtime: `bn_append`, `bn_slice_expr`
- `OP_SLICE_GET`/`SET`, `OP_SLICE_LEN`, `OP_BOUNDS_CHECK`
- `OP_APPEND`, `OP_SLICE_EXPR`
- Array literals and indexing
- For-in loops over arrays and slices

**Tested:** Conformance tests 009, 014, 016 (slices, arrays, for-in).

### Step 10: Strings & Remaining Features — DONE

- String operations: len, indexing, slicing (via []char)
- Switch statements
- Bitwise operations
- Type declarations, const/iota
- Compound assignment, inc/dec
- Multi-return
- Global variables
- Integer literal bases (hex, octal, binary)

**Tested:** All 40 conformance tests passing.

---

## Part 4: Remaining Work

### Step 11: Compiler Ergonomics — DONE

compile.bn is now a full compiler driver:
- Added `bootstrap.Exec` builtin for subprocess execution
- compile.bn writes `.ll` to temp file, auto-invokes `clang`, cleans up
- Flags: `-o <name>`, `--emit-llvm`, `--runtime <path>`, `-v`
- Auto-discovers runtime relative to input file via `findRuntime(inputFile)`
- Single-file workflow: `bootstrap compile.bn -- input.bn` → native binary

**Still TODO (multi-package, library output):**
- Multi-file packages: compile each `.bn` to `.ll`, link together
- Library packages: produce `.o` per package (or `.a` archive)
- Pre-compile `binate_runtime.c` to `.o`/`.a` for distribution

### Step 12: Memory Management

**12a. Reference counting runtime — DONE**
- Two-word header: `[refcount (i64) | free_fn_ptr | payload]`
- `bn_alloc(size)`: allocates with header, refcount=1, zero-init payload
- `bn_box(val, size)`: `bn_alloc` + memcpy
- `bn_refcount_inc(ptr)` / `bn_refcount_dec(ptr)`: inc/dec with nil-check and immortal sentinel
- When refcount hits 0, calls free_fn (default: `free(base_ptr)`)
- Codegen emits `OP_REFCOUNT_INC`/`DEC` as calls to runtime functions
- Also implemented: `OP_MAKE` emission via `bn_alloc` (was missing)

**12b. Scope-based refcount insertion — DONE**
- Inc managed ptr params at function entry (callee owns a reference)
- Dec all managed ptr locals before function return (skip returned values)
- Dec managed ptr vars at block scope exit (if/else, for body, nested blocks)
- On `p = newval`: dec old, inc new (if copy, not fresh creation)
- On `var p = expr` / `p := expr`: inc if RHS is a copy
- `isFreshManagedPtr()` distinguishes make/box/call from copies
- `emitDecForScopeVars()` handles inner scope cleanup
- Future: recursive release for managed ptr fields in structs, managed ptrs in slices

**12c. Slice memory management — DONE**
- `bn_slice_free()` runtime function frees slice backing data (no-op for nil)
- `OP_SLICE_FREE` IR op emitted at scope exit and function return for local slices
- Function parameters are marked `IsParam` and skipped (caller owns the data)
- Returned slices are skipped (ownership transfers to caller)
- `append` uses `realloc` which handles the old buffer correctly
- Sub-slicing copies data (safe, no double-free risk)
- Future: refcounted backing for shared sub-slices (not needed while copying)

**12d. Refcount elision (optimization, later)**
- Escape analysis: if a managed pointer doesn't escape the function, skip refcounting
- Stack promotion: `box(v)` where result doesn't escape → alloca instead of malloc
- These require an optimization pass over the IR

### Step 13: Test Gaps — DONE

Added 6 new conformance tests (054–059), bringing suite to 59 tests.
Fixed two-pass struct registration for self-referential types (e.g., `Node { val int; next @Node }`).
Fixed chained managed pointer field access (`list.next.val`).

**New tests:**
- 054_make_zero_init — `make(T)` zero-initializes struct fields ✓
- 055_struct_with_slice — struct containing slice field ✓ (fixed: string-to-chars for field assign, struct zero-init)
- 056_mixed_width — mixed-width integer arithmetic ✓
- 057_return_struct — functions returning structs by value ✓
- 058_recursive_managed — recursive managed pointer with chained field access ✓
- 059_switch_return — return from switch inside loop ✓

**Bug fixes during Step 13:**
- Two-pass struct registration: register names first, populate fields second (enables self-referential types)
- `getSelectorType` handles `TYP_MANAGED_PTR` for chained access (`list.next.val`)
- `genSelector` dereferences managed ptr before field access in chains
- String-to-chars conversion for selector assignment (`b.name = "test"` where field is `[]char`)
- Zero-initialize struct allocas (fixes uninitialized slice fields in structs)

**Remaining test gaps (future):**
- `@[]T` managed slices
- Nested arrays on compiled backend
- Slice of slices, slice of structs
- Multi-return with managed pointers

### Step 15: Type Layout Computation — DONE

Binate now defines its own struct layout rules, independent of any backend. The compiler emits packed LLVM structs with explicit padding, ensuring deterministic layout.

**What was done:**
- Added `SizeOf()`, `AlignOf()`, `FieldOffset()` to `pkg/types` — Binate-defined layout rules (alignment = min(size, 8), fields padded to natural alignment, struct size rounded to max field alignment)
- Codegen emits packed LLVM structs (`<{ ... }>`) with explicit `[N x i8]` padding fields
- `structLLVMIndex()` maps Binate field indices to LLVM indices (accounting for padding entries)
- `typeSizeBytes()`/`elemSizeOf()` now delegate to `types.SizeOf()`
- 13 new layout unit tests in types_test.bn (72 total)
- Conformance test 060_mixed_struct — 60/60 all backends

**Remaining (future):**
- Interpreter flat byte buffers (15e) — deferred until after self-compilation
- `#[packed]` annotation support
- 32-bit target layout (word_size=4)

**Original motivation:** The design (claude-notes.md line 27) requires "same struct layouts — no marshalling" between compiled and interpreted code. Previously:
- Layout knowledge is hardcoded in emit.bn, not shared with other packages

**Key principle:** Binate defines its own layout rules. LLVM is just a backend — we don't match its rules, we tell it what to do. Struct types are emitted as packed LLVM structs with explicit padding fields inserted by us, so layout is deterministic and backend-independent.

**15a. Layout functions in `pkg/types`**

Add to `pkg/types.bni` and implement in `pkg/types/types.bn`:

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

**Rule: alignment = min(size, word_size).** Fields are aligned to their natural alignment. Struct size is rounded up to struct alignment (= max field alignment).

Example:
```
struct { a int8; b int64; c int8 }
  offset 0: a (1 byte) + 7 padding
  offset 8: b (8 bytes)
  offset 16: c (1 byte) + 7 padding
  total: 24 bytes, align 8
```

Future: 32-bit targets would have word_size=4, changing pointer/int sizes and the alignment cap.

**15b. Compiler emits packed structs with explicit padding**

Change codegen to emit LLVM packed structs (`<{ ... }>`) with explicit `[N x i8]` padding fields between real fields. This ensures LLVM uses exactly the layout we computed — no surprises.

Example: `struct { a int8; b int64 }` emits:
```llvm
%MyStruct = type <{ i8, [7 x i8], i64 }>
```

Replace `typeSizeBytes()` and `elemSizeOf()` in emit.bn with calls to `types.SizeOf()`. Update `getelementptr` field indices to account for padding fields.

**15c. Layout unit tests**

Add tests in `pkg/types/types_test.bn`:
- `SizeOf`/`AlignOf` for all primitive types
- `SizeOf` for structs with uniform fields (all same type)
- `SizeOf` for structs with mixed-width fields (padding verification)
- `FieldOffset` for structs with padding
- `SizeOf` for nested structs, arrays of structs
- `SizeOf` for structs containing slices and pointers

**15d. Conformance test for layout correctness**

Add a conformance test that allocates a mixed-width struct via `make(T)`, writes to each field, and reads them back. This catches size mismatches between `bn_alloc` and the actual struct layout.

**15e. Interpreter flat byte buffers (after self-compilation)**

Once the interpreter is compiled natively (Step 14), managed pointers become real native pointers with the same `[refcount | free_fn | payload]` header as compiled code. At that point, struct values in the interpreter can use flat byte buffers with `SizeOf`/`FieldOffset` for field access. No marshalling needed for interop — interpreted and compiled code share the same memory representation.

Before self-compilation (while running under the bootstrap), the interpreter keeps its current `Fields []@Value` representation. The bootstrap doesn't need interop with compiled code.

**Future:**
- `#[packed]` annotation support
- 32-bit target layout (word_size=4)

### Step 14: Multi-Package Compilation & Self-Compilation

The compiler currently handles single-file programs only. To compile `main.bn` (interpreter) or `compile.bn` (compiler), it must handle multi-package programs with cross-package calls, type references, and the bootstrap runtime.

**Architecture decision:** Separate compilation per package. Each package compiles to its own `.ll` → `.o` file, then all are linked together. This enables partial recompilation (only rebuild changed packages) and scales better than merging everything into one module.

The key enabler is a **consistent name mangling scheme** so cross-package references resolve at link time:
- Functions: `pkg.Func` → `bn_pkg__Func` (e.g., `parser.New` → `bn_parser__New`)
- Struct types: `pkg.Type` → `%bn_pkg__Type` (e.g., `ast.File` → `%bn_ast__File`)
- The `main` package's `main()` → `@bn_main` (called by C runtime)
- Package-local (unexported) names still get mangled with the package prefix for uniqueness

Each package's `.ll` file contains:
- `define` for its own functions
- `declare` for functions it imports from other packages or the C runtime
- Struct type definitions for its own types and any types it references from other packages

#### 14a. Loader integration in compile.bn

compile.bn currently reads one file, parses it, and calls `ir.GenModule(file)`. Change it to:

1. Parse input file(s) and merge them (like main.bn does)
2. Use `pkg/loader` to discover and load all imported packages recursively
3. Compile each package separately (IR gen → LLVM emission → `.ll` file)
4. Invoke clang to compile each `.ll` to `.o`, then link all `.o` files + runtime

The loader already provides packages in dependency order, so each package can reference types/functions from its dependencies.

#### 14b. Multi-package IR generation

`ir.GenModule` currently takes a single `@ast.File`. Extend it to take a list of packages in dependency order (as the loader already provides).

For each package:
1. Register its struct types (two-pass, as already implemented)
2. Register its function signatures
3. Generate IR for all function bodies

Cross-package name resolution:
- Function names are mangled: `parser.New` → `bn_parser__New` (or similar scheme). The dot in `parser.New` is already captured in IR as `StrVal`; the codegen needs a consistent mangling.
- Struct type names are mangled similarly: `parser.Parser` → `%bn_parser__Parser`
- Constants from imported packages must be inlined at IR gen time (they're compile-time values)
- Global variables from imported packages need qualified names

Key constraint: the `main` package's `main()` function continues to be emitted as `@bn_main` (called by the C runtime's `main()`).

#### 14c. Cross-package type resolution

Currently `resolveTypeExpr` in gen.bn only looks up types from `moduleStructs` (one package). For multi-package compilation:

- Each package's types must be registered with qualified names
- `resolveTypeExpr` must handle qualified type references (e.g., `ast.File`, `parser.Parser`)
- `.bni` files declare the public API — use these to know what types exist in each package before processing implementation files

The loader already parses `.bni` files and provides them as `Package.BNI`. IR gen can process these first to register types and function signatures from all dependencies.

#### 14d. Bootstrap runtime in C

`pkg/bootstrap` is a special "builtin" package — it has no `.bn` implementation, only a `.bni` interface. The bootstrap interpreter implements it in Go. For compiled code, we need C implementations in `binate_runtime.c`:

Functions needed (from bootstrap.bni / Go implementation):
```c
// Already implemented:
void    bn_exit(int64_t code);
void    bn_print_string(char *s);
void    bn_print_int(int64_t v);
void    bn_print_bool(int1_t v);
void    bn_print_newline();
// ... slice functions, alloc, refcount ...

// Need to add:
int64_t bn_bootstrap__Open(BnSlice path, int64_t flags);
int64_t bn_bootstrap__Read(int64_t fd, BnSlice buf, int64_t n);
int64_t bn_bootstrap__Write(int64_t fd, BnSlice data, int64_t n);
int64_t bn_bootstrap__Close(int64_t fd);
BnSlice bn_bootstrap__Itoa(int64_t v);
BnSlice bn_bootstrap__Concat(BnSlice a, BnSlice b);
int64_t bn_bootstrap__Stat(BnSlice path);
BnSlice bn_bootstrap__Args();          // returns [][]char
BnSlice bn_bootstrap__ReadDir(BnSlice path);  // returns [][]char
int64_t bn_bootstrap__Exec(BnSlice program, BnSlice args);  // [][]char args
```

Note: functions taking `[]char` receive `%BnSlice` (data ptr + len). Functions returning `[][]char` need a slice-of-slices representation. `Exec` takes `[][]char` — this requires the runtime to unpack nested slices.

#### 14e. LLVM function declarations for imported packages

The codegen must emit `declare` directives for all cross-package functions that are called but defined in other packages (or in the C runtime). For functions defined in Binate packages that are part of the same compilation, they'll have `define` directives. For bootstrap builtins, they'll have `declare` directives matching the C runtime.

#### 14f. Validation milestones

1. **Compile a two-package program**: A trivial `main.bn` that imports a helper package. Validates the loader + multi-package IR + name mangling pipeline.
2. **Compile a program using pkg/bootstrap**: Validates the C runtime bridge (file I/O, string ops).
3. **Compile the compiler** (`compile.bn`): Imports `pkg/parser`, `pkg/ast`, `pkg/ir`, `pkg/codegen`, `pkg/bootstrap`, `pkg/debug`. Validates the full pipeline.
4. **Compile the interpreter** (`main.bn`): Imports `pkg/parser`, `pkg/ast`, `pkg/interp`, `pkg/loader`, `pkg/bootstrap`, `pkg/debug`. This is the largest target.
5. **Self-compilation**: The natively compiled compiler compiles itself. Validates correctness — the output should be functionally identical.

#### 14g. Expected challenges

- **Slice-of-slices** (`[][]char`): `bootstrap.Args()` and `bootstrap.ReadDir()` return these. The IR/codegen doesn't currently handle nested slices well.
- **Package-qualified field access**: `pkg.Struct.Field` resolution across packages.
- **Circular type references across packages**: Unlikely in current code, but possible.
- **Scale**: The interpreter + all its packages is ~5000 lines of Binate. This will stress the IR gen and LLVM emission at a scale not yet tested.
- **Bootstrap string handling**: Bootstrap functions use `[]char` with null-terminator conventions. The C runtime must match these conventions.

#### Order of work

Suggested order within Step 14:
1. Name mangling scheme (implement in codegen, applied to all function/type names)
2. 14a: Loader integration in compile.bn
3. 14b + 14c: Per-package IR gen with cross-package type resolution
4. 14e: Emit `declare` for imported functions
5. 14f.1: Validate with a trivial two-package program
6. 14d: Bootstrap C runtime functions
7. 14f.2: Validate with a bootstrap-using program
8. 14f.3: Compile the compiler
9. 14f.4: Compile the interpreter
10. 14f.5: Self-compilation

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

- **Phase 1 (current):** Leak everything. `bn_alloc` = `malloc`, never free. All conformance tests are short-lived.
- **Phase 2:** Reference counting. Add header before each allocation, emit inc/dec at assignment boundaries. Use the IR's `OP_REFCOUNT_INC/DEC` instructions.
- **Phase 3:** Refcount elision. Escape analysis determines which pointers don't need counting.

---

## Current Status

**Phase 5b: Steps 1–13, 15 complete. 60/60 conformance tests passing on all three backends (bootstrap, selfhost, compiled). Zero XFAILs.**

| # | Component | Status | Notes |
|---|-----------|--------|-------|
| 1 | `pkg/ir` data structures | Done | 55 ops, 34 unit tests |
| 2 | `pkg/ir/gen.bn` | Done | Full AST→IR for bootstrap subset |
| 3 | `pkg/codegen/emit.bn` | Done | LLVM IR text emission |
| 4 | `runtime/binate_runtime.c` | Done | Print, slices, box, bounds check |
| 5 | `compile.bn` driver | Done | Parse → IR → LLVM → auto-invokes clang |
| 6 | Conformance suite | Done | 60 tests, 3 backends (bootstrap/selfhost/compiled) |
| 7 | Global variables | Done | IR collection, @name emission, load/store |
| 8 | Integer literal bases | Done | Hex, octal, binary |
| 9 | For-in loops | Done | Arrays and slices |
| 10 | Multi-return | Done | Aggregate struct packing |
| 11 | Compiler ergonomics | Done | compile.bn auto-invokes clang, -o flag, --emit-llvm, runtime auto-discovery |
| 12 | Memory management | Done | 12a runtime, 12b scope-based refcount, 12c slice free (12d elision: future) |
| 13 | Test gap coverage | Done | 59 tests; added 054–059, fixed self-referential structs + chained managed ptr access |
| 14 | Self-compilation | TODO | Multi-package compilation, separate compilation per package, name mangling |
| 15 | Type layout computation | Done | Binate-defined layout rules, packed LLVM structs, 60 conformance tests |
