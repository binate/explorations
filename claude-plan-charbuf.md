# Plan: CharBuf — Growable Character Buffer

## Design

CharBuf is a value-type struct with a managed backing slice, logical length, and
capacity. It replaces `[]char` + `append` for all string-building in the compiler
and interpreter.

```
type CharBuf struct {
    Data @[]char   // managed backing store (refcounted)
    Len  int       // logical length (chars written)
    Cap  int       // allocated capacity (≥ Len)
}
```

**Why `@[]char` not `[]char`?** Raw slices are unmanaged — there's no way to free
or grow them safely. A managed-slice gives us refcounted ownership of the backing
array. When CharBuf is copied (it's a value type), the refcount on Data increments;
when a copy goes out of scope, it decrements. The last owner frees the memory.

**Why a separate Cap field?** `len(Data)` gives the managed-slice's length, which
equals the capacity. Having Cap explicit avoids repeated `len()` calls and makes
the growth logic clearer. It also allows for a future optimization where we don't
need to update the managed-slice's length on every append — we only replace Data
when we grow.

**Return-by-value pattern:** All mutating functions take a CharBuf by value and
return the modified CharBuf. This matches the current `append` pattern and avoids
needing managed pointers to CharBuf itself:
```
b = buf.WriteByte(b, 'x')
b = buf.WriteStr(b, "hello")
```

## API

```
package "pkg/buf"

func New() CharBuf                              // empty buffer, initial cap 64
func WriteByte(b CharBuf, c char) CharBuf       // append one char
func WriteStr(b CharBuf, s []char) CharBuf      // append a string ([]char)
func WriteInt(b CharBuf, n int) CharBuf         // append decimal integer
func WriteHexByte(b CharBuf, val int) CharBuf   // append 2-digit uppercase hex
func WriteBuf(b CharBuf, src CharBuf) CharBuf   // append another CharBuf's contents
func Bytes(b CharBuf) []char                    // extract contents as raw []char
func Clear(b CharBuf) CharBuf                   // reset to empty, keep capacity
func BufLen(b CharBuf) int                      // current logical length
```

## Growth Strategy

- Initial capacity: 64 chars
- Growth: double capacity until it's ≥ needed
- `grow(b, needed)` allocates new `@[]char` via `make_slice(char, newCap)`, copies
  existing data, replaces `b.Data`
- Old backing store is freed automatically via refcount decrement when replaced

## Dependency: `make_slice`

CharBuf requires `make_slice(char, n)` to return `@[]char` (a proper managed-slice).
This builtin doesn't exist yet — the current `make([]T, n)` is broken (returns raw
`BnSlice` instead of managed-slice) and is being removed.

**This means CharBuf cannot be implemented until `make_slice` exists.** See
`claude-plan-fix-make.md` for the migration plan (specifically step 4).

## Files

| File | Contents |
|------|----------|
| `pkg/buf.bni` | CharBuf struct definition |
| `pkg/buf/buf.bn` | Implementation |
| `pkg/buf/buf_test.bn` | Unit tests |

## Conversion Order (after CharBuf exists)

1. `emit.bn` — 82 append calls, biggest win
2. `gen.bn` — name/string construction
3. `loader.bn`, `parser.bn`, `compile.bn` — string building
4. `interp/value.bn` — intToStr, concatStr, valueToString
5. `interp/interp.bn` — escape sequence parsing

## Validation

- `bootstrap -test pkg/buf` — unit tests
- Full conformance suite after each conversion step
- Self-compilation chain
