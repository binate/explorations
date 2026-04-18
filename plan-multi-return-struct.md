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

### Interpreter side — MOOT

The original tree-walker (`pkg/interp`) was retired in 2026-04-17;
the "Step 4/5 — interpreter side TODO" block that used to live here
described how to rewrite `pkg/interp`'s multi-return to flat anonymous
structs and has been removed. The VM (`pkg/vm`) already consumes the
compiler's IR representation directly, so it inherits the
anonymous-struct layout with no further work needed here.

## Layout

`func f() (int, bool)` → returns `struct { _0 int; _1 bool }`
- SizeOf = 16 (8 + 1 + 7 padding), fields at offset 0 and 8

`func g() (StrTab, int)` → returns `struct { _0 StrTab; _1 int }`
- Standard struct layout with FieldOffset for each field

The LLVM type is an inline struct: `{i64, i1}`, `{%StrTab, i64}`, etc.
