# Bug: Chained field access through []*Struct element

## Summary

Same class of bug as the one fixed for test 263 (*Struct chained
field access). When code accesses a field through a raw pointer
element in a slice (`items[0].Val` where `items` is `[]*Item`),
the compiled code returns wrong values (0 instead of actual).

## Minimal repro

Conformance test **265_ptr_slice_elem_bni** (multi-package).

```binate
// pkg/mypkg.bni
type Item struct { Val int }
func GetFirstVal(items []*Item) int

// pkg/mypkg/mypkg.bn
func GetFirstVal(items []*Item) int {
    if items[0] == nil { return -1 }
    return items[0].Val  // BUG: returns 0 instead of 42
}
```

- **boot**: PASS
- **boot-comp**: FAIL (`GetFirstVal` returns 0)

Simple operations work:
- `items[0] != nil` → correct (nil check)
- `items[0].Val` → **WRONG** (chained access)

## Impact

Blocks the interpreter Value ownership refactor. The interpreter
uses `[]*Value` slices for function arguments, and accessing
fields through these elements generates wrong code.

## Likely cause

Same root cause as test 263 — the codegen for chained field
access through a raw pointer loses type information in
multi-package contexts with `.bni`-defined structs. The fix for
263 addressed `*Struct → @T → field` but not
`[]*Struct element → field`.
