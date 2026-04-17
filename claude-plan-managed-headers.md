# Plan: Proper Managed-Type Headers and @[]T Representation

> **STATUS (2026-03-31): COMPLETED.** Managed-slice (`@[]T`) representation is implemented with the 3-word layout. `append` has been fully removed from the language; slice operations no longer include append.

## Context

Currently `@T` and `@[]T` have incorrect/incomplete LLVM representations:
- `@T` = `i8*` (pointer to payload, header at negative offset — correct structure but not expressed as a Binate struct)
- `@[]T` = `%BnSlice = { i8*, i64 }` — **wrong**, identical to `*[]T`. Should be 3 words: `{ *T data, uint len, @any refptr }`

The management header `{ refcount, free_fn }` exists in C runtime but isn't modeled as a Binate struct. All of these should be expressed as explicit Binate structs so the compiler and interpreter handle them uniformly.

## Design

### Value-type structs (not headers — these are the types themselves)

**`*[]T`** — raw slice (2 words, 16 bytes):
```
struct { data *T, len uint }
```
LLVM: `%BnSlice = type { i8*, i64 }` (unchanged)

**`@[]T`** — managed-slice (3 words, 24 bytes):
```
struct { data *T, len uint, refptr @any }
```
LLVM: `%BnManagedSlice = type { i8*, i64, i8* }`

The first two fields (`data`, `len`) are identical in layout to `%BnSlice`. This means a `@[]T` can be accessed as a `*[]T` by simply reading the first 16 bytes — no field extraction or pointer arithmetic needed. The `refptr` (a managed pointer `@any` to the backing allocation, carrying the refcount) is appended as the third word.

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

`@T` remains `i8*` in LLVM. A single pointer to the data, with the `MgmtHeader` at negative offset. `rt.Alloc`, `rt.RefInc`, `rt.RefDec` (in pkg/rt) manage the lifecycle.

### Unsigned for lengths and refcounts

Lengths (`*[]T.len`, `@[]T.len`) and refcounts (`MgmtHeader.refcount`) should be `uint` (unsigned 64-bit). This means `i64` → `i64` in LLVM (same bit width, semantics differ at the language level).

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
%BnManagedSlice = type { i8*, i64, i8* }
```

Change `llvmType` (line 1657):
```
if t.Kind == TYP_MANAGED_SLICE { return "%BnManagedSlice" }
```

### 3. Codegen — `OP_MAKE_SLICE` emits managed-slice creation

**File:** `binate/pkg/codegen/emit.bn`

`make_slice(T, n)` calls `rt.MakeManagedSlice(elemSize, length)` from pkg/rt, which returns
a `ManagedSlice` struct (layout-compatible with `%BnManagedSlice`). The codegen bitcasts
the packed struct return to `%BnManagedSlice`.

### 4. Codegen — slice operations on `@[]T`

All slice operations (`len`, `get`, `set`, `slice_expr`, `append`) currently take/return `%BnSlice`. For `@[]T`, they need to:
- Extract the `{ data, len }` prefix from `%BnManagedSlice` (fields 0,1) — identical layout to `%BnSlice`
- Pass as `%BnSlice` to existing runtime functions (or bitcast pointer since layout is a prefix match)
- Reconstruct `%BnManagedSlice` with updated data/len when operations return new slices (e.g., append), preserving the refptr (field 2)

### 5. Codegen — refcounting for `@[]T`

When a `@[]T` is copied (assigned, passed), extract the `refptr` (field 2) and call `rt.RefInc`.
When it goes out of scope, extract `refptr` and call `rt.RefDec`. This happens at: var declarations,
assignments, field assignments, function params, scope exit, and return cleanup.

### 6. Codegen — `@[]T → *[]T` conversion

Extract fields 0,1 (`data`, `len`) from `%BnManagedSlice` — these are already a `%BnSlice` by layout. Can be done with a simple bitcast of the pointer (since `%BnSlice` is a prefix of `%BnManagedSlice`) or extractvalue pair. No refcount change (the raw slice borrows).

### 7. Runtime — `rt.MakeManagedSlice` (pkg/rt)

Managed-slice creation is now in Binate (`pkg/rt/rt.bn`), not C:

```binate
func MakeManagedSlice(elemSize int, length int) ManagedSlice {
    var ms ManagedSlice
    if length > 0 {
        var ptr *any = Alloc(length * elemSize)
        ms.Data = ptr
        ms.Refptr = ptr
    }
    ms.Len = length
    return ms
}
```

The old C runtime functions (`bn_alloc`, `bn_refcount_inc`, `bn_refcount_dec`,
`bn_make_managed_slice`) have been removed from `binate_runtime.c`. Only `bn_alloc`
remains (used by `bn_box`, which hasn't been migrated yet).

### 8. Self-hosted interpreter — `@[]T` as 3-field value

**File:** `binate/pkg/interp/interp.bn`, `binate/pkg/interp/value.bn`

The interpreter currently uses `SliceVal { Elems, Typ }` for both `*[]T` and `@[]T`. For correct semantics, managed-slices need to track their refcount (via HeapObject). Options:
- Add a `HeapObj @HeapObject` field to the Value struct for managed-slices
- Or use a separate value kind (VAL_MANAGED_SLICE) with both Elems and HeapObj

The key requirement: when a `@[]T` is copied, its HeapObj refcount increments. When it leaves scope, it decrements.

### 9. Bootstrap interpreter

The bootstrap interpreter can mostly remain as-is since it uses Go-level GC. The `SliceVal` with `ManagedSliceType` already works. If needed, add a `HeapObj` field to `SliceVal` for managed-slice values so refcounting tests can verify behavior.

## Implementation Order

1. ~~**Runtime**: Add `BnManagedSlice` struct and `bn_make_managed_slice` to C runtime~~ — DONE (pivoted to pkg/rt in Binate)
2. ~~**Type system**: Fix `SizeOf` for `TYP_MANAGED_SLICE` → 24 bytes~~ — DONE
3. ~~**Codegen**: Add `%BnManagedSlice` type, update `llvmType`, update `OP_MAKE_SLICE` emission~~ — DONE
4. ~~**Codegen**: Handle slice operations on `@[]T` (extract inner `%BnSlice`, dispatch)~~ — DONE (emitManagedToRaw + emitSliceRef)
5. ~~**Codegen**: Update field indices for `{ data, len, refptr }` layout (fields 0,1,2)~~ — DONE (prefix-compatible with `*[]T`)
6. ~~**Codegen**: Add refcounting for `@[]T` (inc on copy, dec on scope exit) — refptr at field 2~~ — DONE (extractvalue field 2, call rt.RefInc/RefDec)
7. ~~**Codegen**: Implement `@[]T → *[]T` conversion~~ — DONE (OP_MANAGED_TO_RAW: extractvalue 0,1 into %BnSlice)
8. ~~**pkg/rt**: Add MakeManagedSlice, migrate OP_MAKE_SLICE to call it~~ — DONE (codegen calls bn_rt__MakeManagedSlice)
9. ~~**Self-hosted interpreter**: Add HeapObj tracking for managed-slices~~ — DONE (HeapObject gains Refcount, MakeManagedSliceVal, copyValue inc, coerce @[]T→*[]T, 095_managed_slice_sharing)
10. ~~**Tests**: Add conformance tests for @[]T~~ — DONE (093_rt_managed_slice, 094_managed_to_raw_slice, 095_managed_slice_sharing)
11. ~~**Remove old C runtime functions**: bn_refcount_inc, bn_refcount_dec, bn_make_managed_slice removed from binate_runtime.c~~ — DONE

Each step should be a separate commit. Run conformance tests after each.

## Verification

```
./conformance/run.sh bootstrap
./conformance/run.sh selfhost
./conformance/run.sh compiled
./conformance/run.sh gen2-compiler
```
