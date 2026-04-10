# Plan: String Literals as Statically-Initialized Global @[]const char

## Motivation

String literals in Binate are currently represented as `i8*` (null-
terminated pointer) in the compiler and `VAL_STRING` with `StrVal
@[]char` in the interpreter. This creates problems:

1. No uniform type — special-cased everywhere.
2. Mutable aliasing unsound — `var x []char = "abc"` allows mutation
   of immutable static data.
3. Interpreter StrVal cache prevents interop with compiled code.
4. Runtime overhead — `bn_string_to_chars` allocates+copies at runtime
   for every string-to-slice conversion.

## Design

### String literals are const-only untyped constants

`"abc"` is an untyped constant. Allowed target types:
- `@[]const char` — **default type**. Managed-slice pointing to static data.
- `[]const char` — raw slice borrowing from static data.
- `[N]const char` — **natural type**. Array copy.

NOT allowed: `[]char`, `@[]char`, `[N]char`. Mutation of literal data
is unsound; implicit copying violates no-hidden-behavior. For mutable
chars: `buf.CopyStr("hello")`.

(Bootstrap uses non-const as stand-in until const types are added.)

### Statically-initialized global @[]const char

For each unique string literal, the compiler emits a **statically-
initialized** `%BnManagedSlice` global. No runtime allocation.

```llvm
; Character data — constant, read-only
@str.0.data = private constant [5 x i8] c"hello"

; Managed-slice header — statically initialized, constant
; backing_refptr = null → immortal (RefInc/RefDec are no-ops on null)
@str.0 = private constant %BnManagedSlice {
    i8* getelementptr ([5 x i8], [5 x i8]* @str.0.data, i64 0, i64 0),
    i64 5,
    i8* null,
    i64 5
}
```

Using `"hello"` in code is just:
```llvm
%s = load %BnManagedSlice, %BnManagedSlice* @str.0
```

A 4-word value load. No allocation, no runtime initialization, no
`bn_string_to_chars` call.

**Why `backing_refptr = null`**: `rt.RefInc(nil)` and `rt.RefDec(nil,
...)` are already no-ops. This means string literal `@[]char` values
can be freely copied, assigned, passed, stored in structs — RefInc/
RefDec on the null backing pointer do nothing, so the static data is
never freed. No "immortal refcount" sentinel needed.

### Using a string literal

- **As `@[]const char`** (default): load the 4-word value from the
  global. Zero cost — no allocation, no copy.

- **As `[]const char`**: extract the first 2 words from the global
  (data pointer + length). Produces a raw slice borrowing static data.

- **As `[N]const char`**: memcpy from the data pointer into the array.

- **As function argument `[]char` (bootstrap compat)**: extract first
  2 words. In the bootstrap (which lacks const), this is the common
  pattern. Mutation through this raw slice is undefined behavior.

### Interpreter changes

1. **Remove `VAL_STRING`** and `StrVal`. String expressions produce
   `VAL_SLICE` with `Typ = @[]char` (or `@[]const char` when const
   types exist).

2. **`MakeStringVal` → flat @[]char with null backing**: allocate a
   32-byte managed-slice header. Allocate character backing via
   `c_malloc` (not `rt.Alloc` — no managed header needed since
   backing_refptr is null). Write characters. Set backing_refptr =
   null in the header.

3. **String comparison**: read data pointers and lengths from slice
   headers, compare byte-by-byte.

4. **String printing**: read data pointer and length, print bytes.

5. **Bootstrap forwarding**: extract data pointer + length from the
   slice header for C function calls.

### Compiler changes

1. **String constant collection** (already in `pkg/ir/strings.bn`):
   change emission from `@"str.N" = private constant [K x i8]` to
   the two-part pattern: `@str.N.data` (constant bytes) +
   `@str.N` (constant `%BnManagedSlice` with null backing_refptr).

2. **String-to-chars conversion**: `OP_STRING_TO_CHARS` currently
   calls `bn_string_to_chars` at runtime. Change to
   `load %BnManagedSlice, %BnManagedSlice* @str.N` — a compile-time
   load from the global constant. Zero runtime cost.

3. **Remove `bn_string_to_chars`** from C runtime. Remove from
   runtime function manifest.

4. **String comparison**: compare as managed-slices (length check +
   byte-by-byte data comparison).

5. **Remove `TYP_STRING`** or rename to `TYP_UNTYPED_STRING` (kept
   only as the untyped literal kind, resolved during type checking).

### Interaction with const types

String literals are const-only by design. This is the semantic
contract, not a future restriction. Non-const variants would require
unsound mutation of static data or implicit hidden copies.

Until const types are implemented, the bootstrap uses `[]char` and
`@[]char` as stand-ins. Documented as pragmatic compromise.

## Migration order

### Phase 1: Compiler — static string globals

1. Change string constant emission to `%BnManagedSlice` globals with
   constant character data and null backing_refptr.
2. Change `OP_STRING_TO_CHARS` to load from global (no runtime call).
3. Remove `bn_string_to_chars` from C runtime and manifest.
4. Update string comparison codegen.
5. Run conformance tests — all string-related tests should pass.

### Phase 2: Interpreter — remove StrVal

1. Change `MakeStringVal` to allocate flat `@[]char` with null
   backing_refptr.
2. Update all `v.StrVal` reads to go through flat slice access.
3. Update string comparison, printing, bootstrap forwarding.
4. Remove `StrVal` from Value struct, remove `VAL_STRING`.

### Phase 3: Type system cleanup

1. Resolve `TYP_STRING` to `@[]const char` / `[]const char` during
   type checking.
2. Remove or rename `TYP_STRING`.
3. Update assignability rules.

## Open questions

1. **`println("hello")`**: with the new model, the string literal is
   a `@[]const char` value (4-word managed-slice). `println` reads
   the data pointer and length from the header. This is the same as
   printing any `[]char` — no special string handling needed.

2. **Null-terminated C interop**: `slice_to_cstr` in the C runtime
   copies and null-terminates. No change needed. (String literal data
   does NOT include a null terminator — `"abc"` is 3 bytes.)

3. **Deduplication**: the IR `CollectStrings` / `FindStringID`
   already deduplicates string literals within a module. Cross-module
   deduplication is handled by the linker (private constants are
   per-module).
