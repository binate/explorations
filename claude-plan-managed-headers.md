# Plan: Proper Managed-Type Headers and @[]T Representation

## Context

Currently `@T` and `@[]T` have incorrect/incomplete LLVM representations:
- `@T` = `i8*` (pointer to payload, header at negative offset — correct structure but not expressed as a Binate struct)
- `@[]T` = `%BnSlice = { i8*, i64 }` — **wrong**, identical to `[]T`. Should be 3 words: `{ @any refptr, *T data, uint len }`

The management header `{ refcount, free_fn }` exists in C runtime but isn't modeled as a Binate struct. All of these should be expressed as explicit Binate structs so the compiler and interpreter handle them uniformly.

## Design

### Value-type structs (not headers — these are the types themselves)

**`[]T`** — raw slice (2 words, 16 bytes):
```
struct { data *T, len uint }
```
LLVM: `%BnSlice = type { i8*, i64 }` (unchanged)

**`@[]T`** — managed-slice (3 words, 24 bytes):
```
struct { refptr @any, data *T, len uint }
```
LLVM: `%BnManagedSlice = type { i8*, i8*, i64 }`

The `refptr` is a managed pointer (`@any`) to the backing allocation. It carries the refcount. `data` and `len` describe the usable window (like a raw slice).

### Management header (at negative offset from managed data)

**`MgmtHeader`** (2 words, 16 bytes):
```
struct { refcount uint, free_fn <fn_ptr> }
```

`@T` points to the data payload. The `MgmtHeader` sits immediately before it in memory:
```
Memory: [ MgmtHeader | payload... ]
                       ^-- @T points here
```

Access: `bit_cast(*MgmtHeader, ptr)[-1]` (or equivalently, `ptr - 16` as `*MgmtHeader`).

This is the same layout as the current C runtime — the change is expressing it as a Binate struct.

### @T — unchanged representation

`@T` remains `i8*` in LLVM. A single pointer to the data, with the `MgmtHeader` at negative offset. `bn_alloc`, `bn_refcount_inc`, `bn_refcount_dec` continue to work as-is.

### Unsigned for lengths and refcounts

Lengths (`[]T.len`, `@[]T.len`) and refcounts (`MgmtHeader.refcount`) should be `uint` (unsigned 64-bit). This means `i64` → `i64` in LLVM (same bit width, semantics differ at the language level).

## Changes Required

### 1. Type system — `SizeOf` for `@[]T`

**File:** `binate/pkg/types/types.bn` (line 438)

Change `TYP_MANAGED_SLICE` size from 16 to 24 bytes:
```
if t.Kind == TYP_SLICE { return 16 }
if t.Kind == TYP_MANAGED_SLICE { return 24 }
```

### 2. Codegen — new `%BnManagedSlice` LLVM type

**File:** `binate/pkg/codegen/emit.bn`

Add type definition (after line 144):
```llvm
%BnManagedSlice = type { i8*, i8*, i64 }
```

Change `llvmType` (line 1657):
```
if t.Kind == TYP_MANAGED_SLICE { return "%BnManagedSlice" }
```

### 3. Codegen — `OP_MAKE_SLICE` emits managed-slice creation

**File:** `binate/pkg/codegen/emit.bn` (lines 938-949)

`make_slice(T, n)` must:
1. Allocate backing array via `bn_alloc(n * elem_size)` — returns managed `@any` (payload ptr with refcount header)
2. Return `%BnManagedSlice { refptr, data_ptr, len }` where `refptr = data_ptr = the bn_alloc result`

New runtime function: `bn_make_managed_slice(i64 elem_size, i64 length) -> %BnManagedSlice`

### 4. Codegen — slice operations on `@[]T`

All slice operations (`len`, `get`, `set`, `slice_expr`, `append`) currently take/return `%BnSlice`. For `@[]T`, they need to:
- Extract the `{ data, len }` sub-struct from `%BnManagedSlice` (fields 1,2)
- Pass it as a `%BnSlice` to existing runtime functions
- Reconstruct `%BnManagedSlice` with updated data/len when operations return new slices (e.g., append)

### 5. Codegen — refcounting for `@[]T`

When a `@[]T` is copied (assigned, passed), increment the refcount on `refptr`.
When it goes out of scope, decrement. This uses the existing `bn_refcount_inc`/`bn_refcount_dec` on the `refptr` field (field 0 of `%BnManagedSlice`).

### 6. Codegen — `@[]T → []T` conversion

Extract fields 1,2 (`data`, `len`) from `%BnManagedSlice` and pack as `%BnSlice`. This is a pure data extraction — no refcount change (the raw slice borrows).

### 7. Runtime — `bn_make_managed_slice`

**File:** `binate/runtime/binate_runtime.c`

```c
typedef struct {
    void    *refptr;   // @any: managed pointer to backing array
    void    *data;     // *T: pointer to first element (= refptr for fresh allocs)
    int64_t  len;      // uint: number of elements
} BnManagedSlice;

BnManagedSlice bn_make_managed_slice(int64_t elem_size, int64_t length) {
    BnManagedSlice ms;
    ms.len = length;
    if (length > 0) {
        ms.refptr = bn_alloc(length * elem_size);  // refcounted
        ms.data = ms.refptr;  // initially same pointer
    } else {
        ms.refptr = NULL;
        ms.data = NULL;
    }
    return ms;
}
```

### 8. Self-hosted interpreter — `@[]T` as 3-field value

**File:** `binate/pkg/interp/interp.bn`, `binate/pkg/interp/value.bn`

The interpreter currently uses `SliceVal { Elems, Typ }` for both `[]T` and `@[]T`. For correct semantics, managed-slices need to track their refcount (via HeapObject). Options:
- Add a `HeapObj @HeapObject` field to the Value struct for managed-slices
- Or use a separate value kind (VAL_MANAGED_SLICE) with both Elems and HeapObj

The key requirement: when a `@[]T` is copied, its HeapObj refcount increments. When it leaves scope, it decrements.

### 9. Bootstrap interpreter

The bootstrap interpreter can mostly remain as-is since it uses Go-level GC. The `SliceVal` with `ManagedSliceType` already works. If needed, add a `HeapObj` field to `SliceVal` for managed-slice values so refcounting tests can verify behavior.

## Implementation Order

1. **Runtime**: Add `BnManagedSlice` struct and `bn_make_managed_slice` to C runtime
2. **Type system**: Fix `SizeOf` for `TYP_MANAGED_SLICE` → 24 bytes
3. **Codegen**: Add `%BnManagedSlice` type, update `llvmType`, update `OP_MAKE_SLICE` emission
4. **Codegen**: Handle slice operations on `@[]T` (extract inner `%BnSlice`, dispatch)
5. **Codegen**: Add refcounting for `@[]T` (inc on copy, dec on scope exit)
6. **Codegen**: Implement `@[]T → []T` conversion
7. **Self-hosted interpreter**: Add HeapObj tracking for managed-slices
8. **Tests**: Update conformance test 089, add new tests for @[]T refcounting and conversion

Each step should be a separate commit. Run conformance tests after each.

## Verification

```
./conformance/run.sh bootstrap
./conformance/run.sh selfhost
./conformance/run.sh compiled
./conformance/run.sh gen2-compiler
```
