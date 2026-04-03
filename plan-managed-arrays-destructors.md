# Plan: Managed-Arrays, Destructors, and RefDec Cleanup

## Context

With Free re-enabled in RefDec, the gen1 compiler crashes because managed
allocations containing other managed references (e.g., `@[]@Instr`) are freed
without cleaning up their contents. The refcounts of contained `@T` values
are never decremented, and freed memory is reused while still referenced.

Two mechanisms are needed:
1. **Destructors** — generated per-type functions that RefDec all managed fields
2. **Managed-arrays** — a new type `@[N]T` that carries its element count,
   enabling destructors to iterate elements when the backing is freed

## Design

### Managed-Arrays (`@[N]T`)

A managed-array is NOT `@([N]T)` (a managed pointer to a boxed raw array).
It is a distinct type that carries the array size in a header, analogous to
how managed-slices carry a refptr separate from data.

**Syntax:**
```
@[N]T           // managed-array of N elements of type T
@([N]T)         // managed pointer to raw array (different thing!)
```

In generics, `@X` where `X = [N]T` yields `@([N]T)`, not `@[N]T`.
This parallels `@X` where `X = []T` yielding `@([]T)`, not `@[]T`.

**`box()` and literals:**
```
box([3]int{1, 2, 3})    // → @([3]int) — managed pointer to boxed array
@[3]int{1, 2, 3}        // → @[3]int  — managed-array literal
```

**Memory layout:**
```
[ MgmtHeader | ArrayHeader | element[0] | element[1] | ... | element[N-1] ]
                ^-- refptr points here

MgmtHeader:  { refcount int, free_fn *uint8 }     // 2 words, 16 bytes
ArrayHeader: { size int }                          // 1 word, 8 bytes
```

The refptr (in managed-slices derived from this array) points to the
ArrayHeader, not the element data. This lets the destructor find the
element count.

**Element data pointer:** `refptr + sizeof(ArrayHeader)` gives the start of
element data. Managed-slices created from a managed-array set their `data`
pointer to this offset.

**`make_slice(T, n)` under the hood:** Allocates a managed-array of `n`
elements, then returns a managed-slice viewing all of it:
```
@[]T { data: &elements[0], len: n, refptr: &ArrayHeader }
```

**Subslicing:** `@[]T[lo:hi]` produces a new `@[]T` with the same refptr
but different data/len. The backing managed-array's element count is
unchanged and available to the destructor.

### Destructors

A destructor is a generated function that RefDec's all managed references
within a value before its backing memory is freed.

**Signature:**
```
func __dtor_TypeName(ptr *uint8)
```

**When generated:** For any type `T` where freeing an instance of `T`
requires cleanup of managed fields. This includes:
- Struct types with `@T` or `@[]T` fields (directly or transitively)
- Managed-array element types that are `@T` or structs with managed fields

**Struct destructor example:**
```
type Node struct {
    Name @[]char
    Children @[]@Node
}

// Generated:
func __dtor_Node(ptr *uint8) {
    var n *Node = bit_cast(*Node, ptr)
    RefDec(n.Name.refptr, nil)           // @[]char backing — no element cleanup needed
    // n.Children is @[]@Node — its refptr's destructor handles elements
    RefDec(n.Children.refptr, __dtor_managed_array_of_Node)
}
```

**Managed-array destructor example:**
```
// For @[]@Node (backing is a managed-array of @Node):
func __dtor_managed_array_of_Node(ptr *uint8) {
    var header *int = bit_cast(*int, ptr)  // ArrayHeader
    var count int = header[0]
    var data *uint8 = ptr + 8              // skip ArrayHeader
    var elems *@Node = bit_cast(*@Node, data)
    for i := 0; i < count; i++ {
        RefDec(elems[i], __dtor_Node)      // or nil if @Node has no managed fields
    }
}
```

### RefDec with Destructor

**New signature:**
```
func RefDec(ptr *uint8, dtor *uint8)    // dtor is a function pointer
```

- `dtor` may be nil (no cleanup needed, e.g., `@int`, `@[]int`)
- When refcount hits 0: call `dtor(ptr)` if non-nil, then `Free(ptr)`
- `free_fn` in the management header remains for custom allocator support
  (separate concern from deinitialization)

**At every RefDec call site,** the codegen knows the type being dec'd and
looks up the appropriate destructor:
- `@T` where T is a struct with managed fields → `__dtor_T`
- `@[]T` backing where T is `@U` → `__dtor_managed_array_of_U`
- `@[]T` backing where T is a struct with managed fields → `__dtor_managed_array_of_T`
- `@T` where T has no managed fields → nil
- `@[]T` where T has no managed fields → nil

### Dedup for Managed-Array Destructors

The destructor for `@[]@Node` (the managed-array backing) is keyed on the
element type `@Node`. Multiple `@[]@Node` slices sharing the same element
type share the same destructor. The mangled name includes the element type:
`__dtor_managed_array_of_Node`.

These destructors are emitted once per element type per compilation unit.
Cross-package references use `declare` (forward declaration).

## Grammar Changes

The `Type` production needs a new alternative for managed-array sugar:

```ebnf
Type = ...
     | "@" "[" "]" Type          (* managed-slice sugar *)
     | "@" "[" Expression "]" Type  (* managed-array sugar — NEW *)
```

**Parser disambiguation after `@` `[`:**
- `@` `[` `]` → managed-slice (`@[]T`)
- `@` `[` expr `]` → managed-array (`@[N]T`)
- `@` `(` → grouping
- `@` otherwise → managed pointer

## Implementation Order

1. Add managed-array type to the type system (`TYP_MANAGED_ARRAY`)
2. Update parser for `@[N]T` syntax
3. Update `make_slice` to allocate with ArrayHeader
4. Generate struct destructors in codegen
5. Generate managed-array destructors in codegen
6. Update RefDec to accept destructor parameter
7. Update all RefDec call sites to pass the appropriate destructor
8. Re-enable Free in RefDec
9. Tests at each step

## Verification

After implementation:
```
./conformance/run.sh all
```

All modes should pass with Free enabled.
