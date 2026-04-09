# Plan: String Literals as Global @[]char Constants

## Motivation

String literals in Binate are currently represented as a special
`VAL_STRING` kind in the interpreter (with `StrVal @[]char` on the
Value struct) and as `i8*` pointers to null-terminated data in the
compiler. This creates several problems:

1. **No uniform type**: string literals don't have a proper Binate type.
   They're a special case in the type checker, IR gen, codegen, and
   interpreter.

2. **Semantics are unclear**: `var x []char = "abc"` — is `x` a mutable
   view of immutable data? If so, mutation is unsound. If the data is
   copied, where does it live and who owns it?

3. **Interpreter has a special Value kind**: `VAL_STRING` with `StrVal`
   is the last remaining non-flat scalar cache. It prevents interop
   with compiled code.

4. **Null termination is an artifact**: the compiler uses `i8*` with
   null termination because it inherited a C-string model. Binate
   slices carry their length — null termination is unnecessary.

## Design

### String literals are untyped constants

A string literal `"abc"` is an untyped constant that can be used in
the following type contexts:

- **`@[]char`** (managed char slice): allocates a refcounted backing
  with the character data. Safe to store, pass around, return.
  Mutation of the copy is allowed (each `@[]char` copy shares the
  backing via refcounting; copy-on-write is not needed since the
  compiler already handles RefInc/RefDec).

- **`[]char`** (raw char slice): borrows from an immutable global
  constant. The slice header `{data, len}` points into read-only
  data. Mutation through this slice is undefined behavior (and will
  be prevented by `[]const char` when const types are added).

- **`[N]char`** (char array): copies the literal data into a
  fixed-size array (stack or struct field). `N` must be >= the
  literal length.

### Implementation: global @[]char per string literal

For each unique string literal in a package, the compiler generates
a **global `@[]char` constant**:

```
; Global string constant for "hello"
@str.0 = private constant [5 x i8] c"hello"
@str.0.ms = private global %BnManagedSlice {
    i8* getelementptr ([5 x i8], [5 x i8]* @str.0, i64 0, i64 0),
    i64 5,
    i8* null,    ; no managed backing (static data, never freed)
    i64 5
}
```

The managed-slice header has `backing_refptr = null` — this indicates
static data that is never freed. RefInc/RefDec on null is a no-op
(already the case in `rt.RefInc`/`rt.RefDec`).

When a string literal is used:

- As `@[]char`: load the 4-word value from the global. If the caller
  needs a mutable copy, the type checker/codegen can emit a copy
  (allocate new backing, memcpy characters). For read-only use (most
  cases), the shared global is sufficient.

- As `[]char`: extract the first 2 words (data pointer, length) from
  the global. This produces a raw slice borrowing from the static data.

- As `[N]char`: memcpy from the global's data pointer into the array.

### Interpreter changes

1. **Remove `VAL_STRING`** and `StrVal` from Value. String expressions
   produce `VAL_SLICE` with `Typ = @[]char` or `[]char`.

2. **`MakeStringVal` → allocate flat @[]char**: allocate a managed-slice
   header (32 bytes) and a character backing. Write character data into
   the backing. Set `backing_refptr = null` for string constants (static,
   never freed) or to the backing allocation for dynamic strings.

3. **String comparison**: currently `streq(a.StrVal, b.StrVal)`. With
   flat slices, compare by reading data pointers and lengths from the
   slice headers, then memcmp (or byte-by-byte).

4. **String printing**: currently reads `v.StrVal`. With flat slices,
   read data pointer and length, then print bytes.

5. **Bootstrap forwarding**: `bootstrap.Open(path, ...)` etc. currently
   extract `args[0].StrVal`. With flat slices, read the slice header
   and pass the data pointer + length to the C function.

### Compiler changes

1. **String constant collection**: already exists in `pkg/ir/strings.bn`
   (`CollectStrings`, `FindStringID`). Currently emits `i8*` globals.
   Change to emit `%BnManagedSlice` globals with null backing_refptr.

2. **String-to-chars conversion**: currently `OP_STRING_TO_CHARS` emits
   a call to `bn_string_to_chars` in the C runtime. Change to load
   from the global `@[]char` constant. For `@[]char` assignment where
   mutation is possible, emit a copy (allocate + memcpy).

3. **String comparison in codegen**: currently compares `i8*` pointers.
   Change to compare slice lengths, then memcmp data.

4. **Remove `TYP_STRING`**: string literals resolve to `@[]char` or
   `[]char` at the type level. The `TYP_STRING` type kind may be
   kept as an internal "untyped string literal" kind (like
   `TYP_UNTYPED_INT`) that gets resolved during type checking.

### Interaction with const types (future)

When const types are added:

- `"abc"` naturally has type `@[]const char` or `[]const char`
- Assignment to `@[]char` (mutable) requires a copy
- Assignment to `[]const char` borrows from the global (no copy)
- This prevents the mutation-of-literal-data problem

Until const types exist, `@[]char` from a string literal shares
the global data. Mutation through the shared backing is technically
possible but unsound — the programmer should treat string-derived
`@[]char` as immutable. This matches current behavior.

## Migration order

### Phase 1: Interpreter — remove StrVal (incremental)

1. Change `MakeStringVal` to allocate flat `@[]char` with character
   backing (null backing_refptr for constants).
2. Update all `v.StrVal` reads to go through flat slice access.
3. Update string comparison to use flat data.
4. Update bootstrap forwarding to extract chars from flat slices.
5. Remove `StrVal` from Value struct, remove `VAL_STRING`.

### Phase 2: Compiler — global @[]char constants

1. Change string constant emission from `i8*` to `%BnManagedSlice`.
2. Change `OP_STRING_TO_CHARS` to load from global constant.
3. Remove `bn_string_to_chars` from C runtime.
4. Update string comparison codegen.

### Phase 3: Type system cleanup

1. Resolve `TYP_STRING` to `@[]char` / `[]char` during type checking.
2. Remove `TYP_STRING` or rename to `TYP_UNTYPED_STRING`.
3. Update assignability rules for string literals.

## Open questions

1. **Should string literals allocate a fresh backing every time, or
   share a global?** Sharing is more efficient but allows aliased
   mutation. With const types this is resolved; without them, sharing
   is pragmatic.

2. **What about `println("hello")`?** Currently this is a string
   literal passed to println. With the new model, it's a `@[]char`
   value. The print function would read the data pointer and length
   from the 4-word header.

3. **Null-terminated C interop**: some C functions need null-terminated
   strings. The `slice_to_cstr` helper in the C runtime already handles
   this by copying and null-terminating. No change needed.
