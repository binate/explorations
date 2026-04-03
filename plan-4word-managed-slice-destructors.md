# Plan: 4-Word Managed-Slice and Destructors

## Context

With Free re-enabled in RefDec, the gen1 compiler crashes because managed
allocations containing managed references (e.g., `@[]@Instr`) are freed
without cleaning up their contents. Two things are needed:

1. **Destructors** — per-type generated functions that RefDec managed fields
2. **Backing element count** — so destructors know how many elements to clean up

## Design: 4-Word Managed-Slice

Extend `@[]T` from 3 words (24 bytes) to 4 words (32 bytes):

```
@[]T = { data, len, backing_refptr, backing_len }

Word 0: data           — raw pointer to start of view
Word 1: len            — view length (element count visible through this slice)
Word 2: backing_refptr — managed pointer to backing allocation (refcounted)
Word 3: backing_len    — total element count in backing allocation
```

- `[]T` (raw slice) is still 2 words: `{ data, len }`
- `@[]T → []T` conversion: read first 2 words (prefix-compatible, unchanged)
- Subslicing `s[lo:hi]`: `{ s.data + lo*elemSize, hi-lo, s.backing_refptr, s.backing_len }`
- `make_slice(T, n)`: `{ &backing[0], n, backing_ptr, n }` (view = full backing initially)

**Terminology**: `backing_len` is NOT "capacity" (Go sense). Go's cap counts from
the start of the current slice view. `backing_len` is the total element count of
the backing allocation, regardless of where the view starts. Go-style capacity
is computable from `backing_len - (data - backing_start) / elem_size`, meaning
a correct `append()` is technically feasible (but not currently planned).

## Design: Destructors

### RefDec with destructor parameter

```
func RefDec(ptr *uint8, dtor *uint8)    // dtor is function pointer, or nil
```

When refcount hits 0:
1. Call `dtor(ptr)` if non-nil (deinitialization)
2. Call `Free(ptr)` (deallocation)

`free_fn` in the management header stays for custom allocator support
(separate concern from deinitialization).

### Struct destructors

Generated for any struct with managed fields (`@T`, `@[]T`). Example:

```
type Node struct {
    Name     @[]char
    Children @[]@Node
}

// Generated destructor:
func __dtor_Node(ptr *uint8) {
    // RefDec Name's backing_refptr (no element destructor — chars aren't managed)
    var name_refptr = ... // extract field 0's word 2
    RefDec(name_refptr, nil)
    // RefDec Children's backing — elements are @Node, need element cleanup
    var children_refptr = ... // extract field 1's word 2
    var children_backing_len = ... // extract field 1's word 3
    // Iterate backing elements and RefDec each @Node
    for i := 0; i < children_backing_len; i++ {
        var elem = backing[i]
        RefDec(elem, __dtor_Node)
    }
    RefDec(children_refptr, nil)  // free the backing itself
}
```

### Managed-slice element cleanup

At a RefDec call site for a managed-slice's backing_refptr, the codegen has
the full 4-word value and knows the element type. Before calling
`RefDec(backing_refptr, nil)`, it emits:

```
if Refcount(backing_refptr) == 1 {
    // Last reference — clean up elements
    for i := 0; i < backing_len; i++ {
        RefDec(backing[i], element_dtor)
    }
}
RefDec(backing_refptr, nil)   // frees the backing
```

This is emitted inline at the call site because the element type (and thus
the element destructor) is statically known there. The `Refcount == 1` check
ensures cleanup only happens on the last decrement.

Alternative: pass backing_len to a generated destructor. But the destructor
would need the count as a parameter (it can't derive it from just the pointer),
making the interface less uniform. Inline emission is simpler.

## Changes Required

### 1. Type system: SizeOf for @[]T → 32 bytes

**File:** `pkg/types/types.bn`

Change `TYP_MANAGED_SLICE` size from 24 to 32:
```
if t.Kind == TYP_MANAGED_SLICE { return 32 }
```

### 2. Codegen: %BnManagedSlice → 4 words

**File:** `pkg/codegen/emit.bn`

```llvm
%BnManagedSlice = type { i8*, i64, i8*, i64 }
```

### 3. Codegen: make_slice emits 4-word value

**File:** `pkg/codegen/emit_helpers.bn`

`emitMakeSliceInstr` currently produces 3-word `%BnManagedSlice`. Must set
word 3 (backing_len) = the length argument.

### 4. Codegen: emitManagedToRaw extracts words 0,1

Already correct — extractvalue fields 0 and 1. No change needed as long
as field indices stay the same.

### 5. Codegen: slice_expr preserves backing_refptr and backing_len

**File:** `pkg/codegen/emit_slice.bn`

When subslicing a managed-slice, the result must carry the original
backing_refptr (word 2) and backing_len (word 3), not the view's.

### 6. Codegen: RefDec for managed-slices emits element cleanup

At every site where a managed-slice's backing_refptr is RefDec'd, emit
the element cleanup loop (if element type has managed fields).

### 7. RefDec takes destructor parameter

**File:** `pkg/rt/rt.bn`

Change `RefDec(ptr *any)` to `RefDec(ptr *any, dtor_fn_ptr)`.
Update all call sites (codegen emits the destructor or nil).

### 8. Generate struct destructors

**File:** `pkg/codegen/emit.bn` or new file

For each struct type with managed fields, emit a destructor function
in the LLVM IR output.

### 9. Update rt.ManagedSlice

**File:** `pkg/rt/rt.bn`, `pkg/rt.bni`

ManagedSlice struct grows to 4 fields:
```
type ManagedSlice struct {
    Data       *any
    Len        int
    BackingPtr *any
    BackingLen int
}
```

### 10. Update interpreter

**File:** `pkg/interp/`

The interpreter's managed-slice representation must track backing_len.

### 11. Update bootstrap interpreter

**File:** `bootstrap/interpreter/`

The bootstrap's ManagedSliceVal (or equivalent) must handle 4-word layout.

## Implementation Order

1. Update type system (SizeOf)
2. Update LLVM type and make_slice emission
3. Update slice operations (subslice, managed-to-raw)
4. Update rt.ManagedSlice
5. Update RefDec signature (add dtor parameter)
6. Generate struct destructors
7. Emit element cleanup at managed-slice RefDec sites
8. Re-enable Free
9. Update interpreter and bootstrap
10. Tests at each step

## Verification

After each step:
```
./conformance/run.sh basic
```

After completion:
```
./conformance/run.sh all
```

All modes should pass with Free enabled.
