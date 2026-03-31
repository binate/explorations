# Plan: Binate Runtime Package (pkg/rt)

## Goal

Replace C runtime functions with Binate implementations in `pkg/rt`. Only thin C
stubs remain for libc wrappers (malloc, free, memset, memcpy) and the `main`
entry point. All struct-returning logic moves to Binate, eliminating C ABI
issues (the 3-word `@[]T` struct return segfaults on arm64-apple when crossing
the C↔LLVM boundary).

## Architecture

```
┌────────────────────────────────────────────────┐
│  User program                                  │
│  imports pkg/rt implicitly                     │
├────────────────────────────────────────────────┤
│  pkg/rt  (Binate)                              │
│  Alloc, RefInc, RefDec, MakeManagedSlice, ...  │
│  calls c_malloc, c_free, c_memset via .bni     │
├────────────────────────────────────────────────┤
│  runtime/rt_stubs.c  (thin C)                  │
│  bn_rt__c_malloc, bn_rt__c_free, ...           │
│  main() { bn_main(); }                         │
├────────────────────────────────────────────────┤
│  libc  (malloc, free, memset, write, ...)      │
└────────────────────────────────────────────────┘
```

C stubs use mangled names matching the Binate calling convention: a function
`c_malloc` in package `pkg/rt` becomes symbol `bn_rt__c_malloc`. These stubs
only return scalars or pointers (no struct returns), avoiding ABI mismatches.

## Prerequisites

### 1. `OP_BIT_CAST` codegen

**Files:** `pkg/codegen/emit.bn`

`bit_cast(TargetType, val)` reinterprets bits without conversion. In LLVM:
- Pointer-to-pointer: `bitcast i8* %val to i64*`
- Pointer-to-int: `ptrtoint i8* %val to i64`
- Int-to-pointer: `inttoptr i64 %val to i8*`

Add after the `OP_CAST` case:
```
if instr.Op == ir.OP_BIT_CAST {
    return emitBitCast(out, instr)
}
```

Implementation: check source and target type kinds, emit the appropriate LLVM
instruction (bitcast for ptr→ptr, ptrtoint/inttoptr for ptr↔int).

### 2. Pointer indexing in compiled mode

**Files:** `pkg/ir/gen.bn` — `genIndex` and `genAssign`

Currently, pointer indexing (`ptr[i]`) falls through to `EmitSliceGet`/
`EmitSliceSet`, which call C runtime functions expecting `%BnSlice`. Raw
pointers aren't slices.

Fix: add a `TYP_POINTER` case in both `genIndex` and `genAssign` that uses
`EmitGetElemPtr` (LLVM GEP) + `EmitLoad`/`EmitStore`, matching the existing
array indexing path.

For `genIndex`:
```
if collection.Typ != nil && collection.Typ.Kind == types.TYP_POINTER {
    var elemPtr @Instr = EmitGetElemPtr(ctx.Func, b, collection, index, elemTyp)
    return EmitLoad(ctx.Func, b, elemPtr, elemTyp)
}
```

For `genAssign` (the `EXPR_INDEX` branch):
```
if collection.Typ != nil && collection.Typ.Kind == types.TYP_POINTER {
    var elemPtr @Instr = EmitGetElemPtr(ctx.Func, b, collection, index, elemTyp)
    EmitStore(b, elemPtr, rhs)
}
```

Note: `EmitGetElemPtr` emits `getelementptr` which supports negative indices,
so `ptr[-1]` works for accessing the management header.

### 3. Verify `OP_GET_ELEM_PTR` handles pointer bases (not just allocas)

**File:** `pkg/codegen/emit.bn` — `OP_GET_ELEM_PTR` handler

Currently emits: `getelementptr inbounds T, T* %base, i64 0, i64 %idx`

The double-index `(0, idx)` form is for aggregate types (arrays in allocas).
For raw pointers, GEP should be: `getelementptr T, T* %base, i64 %idx`

May need a flag or check on whether the base is an alloca vs a raw pointer to
emit the correct GEP form.

## pkg/rt Implementation

### File: `runtime/rt_stubs.c`

Thin C wrappers with mangled names:

```c
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

void *bn_rt__c_malloc(int64_t size)                          { return malloc((size_t)size); }
void *bn_rt__c_calloc(int64_t count, int64_t size)           { return calloc((size_t)count, (size_t)size); }
void  bn_rt__c_free(void *ptr)                               { free(ptr); }
void  bn_rt__c_memset(void *ptr, int64_t val, int64_t size)  { memset(ptr, (int)val, (size_t)size); }
void  bn_rt__c_memcpy(void *dst, void *src, int64_t size)    { memcpy(dst, src, (size_t)size); }
```

### File: `pkg/rt.bni`

```binate
package "pkg/rt"

// Thin C wrappers (implemented in rt_stubs.c)
func c_malloc(size int) *any
func c_calloc(count int, size int) *any
func c_free(ptr *any)
func c_memset(ptr *any, val int, size int)
func c_memcpy(dst *any, src *any, size int)

// Managed memory
func Alloc(payloadSize int) *any
func Free(ptr *any)
func RefInc(ptr *any)
func RefDec(ptr *any)

// Managed slices
func MakeManagedSlice(elemSize int, length int) @[]any
```

### File: `pkg/rt/rt.bn`

```binate
package "pkg/rt"

// Management header layout (2 words = 16 bytes):
//   word 0: refcount (int)
//   word 1: free function pointer (int, used as function ptr)
// Header sits at negative offset from payload pointer.

const HEADER_WORDS int = 2
const WORD_SIZE int = 8
const HEADER_SIZE int = 16  // HEADER_WORDS * WORD_SIZE

func Alloc(payloadSize int) *any {
    var totalSize int = HEADER_SIZE + payloadSize
    var base *any = c_malloc(totalSize)
    var header *int = bit_cast(*int, base)
    header[0] = 1   // refcount = 1
    header[1] = 0   // free_fn = null (default: just free)
    var payload *any = bit_cast(*any, &header[HEADER_WORDS])
    c_memset(payload, 0, payloadSize)
    return payload
}

func headerPtr(ptr *any) *int {
    // Header is at ptr - HEADER_SIZE
    // = bit_cast(*int, ptr) at index -HEADER_WORDS
    var h *int = bit_cast(*int, ptr)
    return &h[-HEADER_WORDS]  // or: bit_cast(*int, cast(int, bit_cast(int, ptr)) - HEADER_SIZE)
}

func RefInc(ptr *any) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    h[0] = h[0] + 1
}

func RefDec(ptr *any) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    h[0] = h[0] - 1
    if h[0] == 0 {
        Free(ptr)
    }
}

func Free(ptr *any) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    // Free the base allocation (header start)
    c_free(bit_cast(*any, h))
}

type ManagedSlice struct {
    Data   *any    // pointer to first element (field 0 — matches []T layout prefix)
    Len    int     // number of elements   (field 1 — matches []T layout prefix)
    Refptr *any    // managed pointer to backing array (field 2 — refcounted)
}
// Note: fields 0,1 are identical in layout to []T (BnSlice).
// This means @[]T can be read as []T with no arithmetic — just ignore field 2.

func MakeManagedSlice(elemSize int, length int) ManagedSlice {
    var ms ManagedSlice
    ms.Len = length
    if length > 0 {
        ms.Refptr = Alloc(length * elemSize)
        ms.Data = ms.Refptr
    }
    return ms
}
```

**Open question:** `MakeManagedSlice` returns a struct. The return type needs
to be `@[]T` (or at least layout-compatible with `%BnManagedSlice`). Two options:

(a) Return a named struct `ManagedSlice` that has the same layout as
    `%BnManagedSlice`. The codegen recognizes this struct as the managed-slice
    representation.

(b) Return `@[]any` directly. This requires `@[]T` to be usable as a return
    type for Binate functions, which it should be once the managed-slice codegen
    is complete.

Option (b) is cleaner but depends on more codegen being done first. Option (a)
works now but needs the codegen to equate the struct with `%BnManagedSlice`.

For the initial implementation, option (a) is more practical.

## Wiring Up the Compiler

### compile.bn changes

The compile driver needs to:
1. Always compile `pkg/rt` (even if not explicitly imported)
2. Link `rt_stubs.c` alongside or instead of parts of `binate_runtime.c`

For (1): after loading imports, add `pkg/rt` to the load order if not already
present. This is similar to how Go implicitly imports `runtime`.

For (2): the `--runtime` flag already exists. Either extend it to also include
`rt_stubs.c`, or gradually replace `binate_runtime.c` contents with `pkg/rt`
equivalents while keeping the C file for non-migrated functions.

### codegen changes

Replace `OP_CALL_BUILTIN` emissions for migrated functions with `OP_CALL` to
the `pkg/rt` equivalents:

| Old (C runtime)        | New (pkg/rt)                |
|------------------------|-----------------------------|
| `bn_alloc`             | `rt.Alloc`                  |
| `bn_refcount_inc`      | `rt.RefInc`                 |
| `bn_refcount_dec`      | `rt.RefDec`                 |
| `bn_make_managed_slice`| `rt.MakeManagedSlice`       |

This happens in the IR gen, not the codegen emitter. The codegen just sees
`OP_CALL` with cross-package names.

## Implementation Order

1. ~~**OP_BIT_CAST codegen** — add emitBitCast to emit.bn~~ — DONE
2. ~~**Pointer indexing** — fix genIndex and genAssign for TYP_POINTER~~ — DONE
3. ~~**GEP for raw pointers** — ensure OP_GET_ELEM_PTR handles non-alloca bases~~ — DONE
4. ~~**Conformance tests** — add tests for bit_cast and pointer indexing~~ — DONE (090, 091)
5. ~~**C stubs** — create runtime/rt_stubs.c~~ — DONE
6. ~~**pkg/rt** — create pkg/rt.bni and pkg/rt/rt.bn with Alloc, RefInc, RefDec~~ — DONE (test 092 passes)
7. **MakeManagedSlice in pkg/rt** — implement and wire up
8. **compile.bn** — implicit pkg/rt import, link rt_stubs.c
9. **Migrate codegen** — replace OP_CALL_BUILTIN with OP_CALL to pkg/rt
10. **Test** — conformance suite across all modes

## Verification

```
./conformance/run.sh bootstrap
./conformance/run.sh selfhost
./conformance/run.sh compiled
./conformance/run.sh gen2-compiler
```
