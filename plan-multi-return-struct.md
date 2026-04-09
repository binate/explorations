# Plan: Multi-Return as Anonymous Struct

## Motivation

Multi-return values in Binate are an ABI contract: `func f() (T1, T2)`
returns `struct { _0 T1; _1 T2 }` with standard struct layout. Both
the compiler and interpreter must use the same representation for
dual-mode interop.

## Status (2026-04-09)

### Compiler side — DONE

- `Func.MultiReturnType`: anonymous struct type created in genFunc
  when `len(Results) > 1`. Fields named `_0`, `_1`, etc.
- `FuncSig.MultiReturnType`: propagated through function signature
  registry for cross-function lookup.
- Call sites: `lookupFuncMultiReturnType` used instead of placeholder
  empty struct.
- Return instructions: carry the struct type via `EmitReturnTyped`.
- LLVM emission: function signatures, return type cache, and return
  instructions all use `llvmType(MultiReturnType)`.
- `llvmType` for anonymous structs: generates inline `{T1, T2, ...}`
  from fields (was falling back to `i64`).
- The multi-return managed-fields bug (strTabAdd pattern) is already
  fixed by earlier refcounting changes. Test 141 passes. Workaround
  in macho.bn reverted.

### Interpreter side — TODO

- Step 4: change multi-return to use flat anonymous struct
- Step 5: remove VAL_MULTI, Elems, MakeMultiVal

## Remaining Work

### Step 4: Interpreter — flat anonymous struct for multi-return

In `pkg/interp`:

1. **execReturn**: instead of creating `VAL_MULTI` with Elems,
   construct a flat anonymous struct (allocFlat + writeFlatValue
   for each field at its FieldOffset). Use the function's result
   types to build the struct type (same `makeMultiReturnStructType`
   logic as the compiler).

2. **Call site destructuring**: `x, y := f()` reads fields from
   the struct via readFlatValue at field offsets, instead of
   indexing into Elems.

3. **Remove `VAL_MULTI`**: no longer needed. Remove `MakeMultiVal`,
   the `Elems` accesses in `execAssign` and `execShortVarDecl`.

This eliminates the last 3 Value.Elems references.

### Step 5: Cleanup

- Remove the `Elems @[]@Value` field from the Value struct
  (in `pkg/interp.bni`).
- Remove `MakeMultiVal` from value.bn.
- Update unit tests.

## Layout

`func f() (int, bool)` → returns `struct { _0 int; _1 bool }`
- SizeOf = 16 (8 + 1 + 7 padding), fields at offset 0 and 8

`func g() (StrTab, int)` → returns `struct { _0 StrTab; _1 int }`
- Standard struct layout with FieldOffset for each field

The LLVM type is an inline struct: `{i64, i1}`, `{%StrTab, i64}`, etc.
