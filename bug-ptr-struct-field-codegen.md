# Bug: Chained field access through *Struct generates wrong code

## Summary

When a function takes `*Struct` (raw pointer to a `.bni`-defined
struct) and does chained field access through a `@T` field
(`o.Ref.Val`), the compiled code returns wrong values (0 instead
of the actual field value). Simple field access (`o.Ref`) works;
the bug is in the chained deref.

## Minimal repro

Conformance test **263_ptr_struct_field_bni** (multi-package).

```binate
// pkg/mypkg.bni
type Inner struct { Val int }
type Outer struct {
    Kind int
    Typ  @Inner
    Addr *uint8
    // ... more fields ...
}
func GetTypVal(o *Outer) int

// pkg/mypkg/mypkg.bn
func GetTypVal(o *Outer) int {
    if o.Typ == nil { return -1 }
    return o.Typ.Val  // BUG: returns 0 instead of 42
}
```

- **boot**: PASS (interpreter handles it correctly)
- **boot-comp**: FAIL (`GetTypVal` returns 0, `CheckTyp` returns false)

Simple accesses work:
- `HasTyp(o *Outer) bool { return o.Typ != nil }` → correct
- `GetTyp(o *Outer) @Inner { return o.Typ }` → correct
- `GetTypVal(o *Outer) int { return o.Typ.Val }` → **WRONG**

## Analysis

The first deref (`o.Typ`) works — it loads the `@Inner` pointer
from the struct. But the second deref (`.Val` on the loaded
`@Inner`) fails. The loaded pointer value is likely being treated
as the wrong type, so the GEP for `.Val` reads from the wrong
offset or the wrong base.

## Relevant code

- `pkg/ir/gen_selector.bn` — `genSelector`, `isRawPtrToStruct`
- `pkg/codegen/emit_helpers.bn` — `emitGetFieldPtr`
- `pkg/codegen/emit_instr.bn` — `emitLoad`

The chained access `o.Typ.Val` generates two selector operations:
1. Load `o.Typ` (field 1 of Outer through *Outer) — works
2. Load `.Val` (field 0 of Inner through @Inner) — broken

The second operation receives the result of the first as a
managed pointer (`@Inner`). The codegen for accessing a field
through `@Inner` (when the `@Inner` came from a `*Struct` field
read) may lose type information.

## Impact

Blocks the interpreter Value ownership refactor. Reader functions
(`IntOf`, `IsString`, `StrOf`, etc.) need to take `*Value`
instead of `@Value` to enforce unique ownership. Without this
fix, chained access like `v.Typ.Elem` through `*Value` would
silently return wrong values.
