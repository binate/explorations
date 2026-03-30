# Plan: CharBuf — Growable Character Buffer

## Context

The compiler and interpreter build strings via repeated `append` to `[]char`, which
is O(n) per append (copies the entire slice each time). Building a string of length
m via m single-char appends is O(m^2). CharBuf replaces this with amortized O(1)
appends using geometric growth, backed by `@[]char` (managed-slice).

`make_slice` now exists across all layers, so the dependency is satisfied.

## Design

```binate
type CharBuf struct {
    Data @[]char   // managed backing store (refcounted), length = capacity
    Len  int       // logical length (chars written so far)
}
```

- **Cap = `len(Data)`**. No separate Cap field — the managed-slice's length *is*
  the capacity. `Len` tracks how much of it is used.
- **`@[]char` backing**: refcounted, freed automatically. When CharBuf is copied
  (value type), refcount on Data increments. Last owner frees.
- **Return-by-value**: all mutating functions take and return CharBuf, matching the
  existing `append` pattern:
  ```
  b = buf.WriteByte(b, 'x')
  b = buf.WriteStr(b, "hello")
  ```

## Growth Strategy

- Initial capacity: 64
- When `Len + needed > len(Data)`: double capacity until sufficient, allocate new
  `@[]char` via `make_slice(char, newCap)`, copy existing data, replace `Data`.
  Old backing freed automatically by refcount.

## API (`pkg/buf`)

```
func New() CharBuf                            // empty, initial cap 64
func WriteByte(b CharBuf, c char) CharBuf     // append one char
func WriteStr(b CharBuf, s []char) CharBuf    // append a []char
func WriteInt(b CharBuf, n int) CharBuf       // append decimal integer
func Bytes(b CharBuf) []char                  // return Data[0:Len] as raw []char
func Len(b CharBuf) int                       // return b.Len
```

`Bytes` returns `b.Data[0:b.Len]`. This works because:
- `@[]T → []T` conversion is supported (assignable in type system)
- Slice expressions on managed-slices work

## Files to Create

| File | Contents |
|------|----------|
| `pkg/buf.bni` | CharBuf struct + function declarations |
| `pkg/buf/buf.bn` | Implementation |

## Dependency: Proper @[]T Codegen

CharBuf's `Data` field is `@[]T`. For this to work correctly in compiled mode,
`@[]T` needs a proper LLVM representation distinct from `[]T`:

- `[]T` (raw slice) = `{ i8*, i64 }` (data ptr, length) — current `%BnSlice`
- `@[]T` (managed-slice) = `{ i8*, i8*, i64 }` (refptr, data ptr, length)

The refptr points to a management header with refcount. This needs to be
implemented before CharBuf can work in compiled mode. See the managed-type
headers plan for details.

## Conversion Order (after CharBuf exists)

1. `emit.bn` — ~540 appendStr/appendChars/appendInt calls, biggest win
2. `gen.bn` — name/string construction
3. `loader.bn`, `parser.bn`, `compile.bn` — string building
4. `interp/value.bn` — intToStr, concatStr, valueToString
5. `interp/interp.bn` — escape sequence parsing

## Validation

- `go run . -root ../binate -test pkg/buf` — unit tests
- Full conformance suite after each conversion step
- Self-compilation chain (gen2)
