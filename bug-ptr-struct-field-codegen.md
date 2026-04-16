# Bug: Wrong LLVM IR type for @T field access through *Struct

## Summary

When a function takes `*Struct` (raw pointer to struct) and accesses
a field of type `@T` (managed pointer), the compiler sometimes
generates `i64` instead of `i8*` for the field load, causing an LLVM
IR type mismatch (`icmp ne i64 %vN, i8* %vM`).

## Reproduction

The bug manifests when compiling `pkg/interp` with functions that
take `*Value` instead of `@Value`:

```binate
func IsString(v *Value) bool {
    // v.Typ is @types.Type (managed pointer, should be i8*)
    // Compiler generates i64 load instead of i8* load
    if v.Typ != nil { ... }
}
```

**The bug does NOT reproduce in standalone programs.** A self-contained
test with the same struct layout compiles correctly. The bug only
appears when compiling multi-file packages where the struct is defined
in a `.bni` file (`pkg/interp.bni`).

Conformance test `262_ptr_struct_field_access` passes in boot and
boot-comp modes — it's a standalone program with a local struct
definition.

## LLVM IR error

```
/tmp/binate_bni_pval_interp.ll:32793:28: error:
  '%v28' defined with type 'ptr' but expected 'i64'
  %v29 = icmp ne i64 %v27, %v28
```

The field (index 1, `@types.Type`) is loaded as `i64` instead of
`i8*`. The null comparison then fails because it compares `i64`
with `i8*`.

## Context

This blocks the interpreter Value ownership refactor, which requires
changing reader functions (`IntOf`, `IsString`, `StrOf`, etc.) from
`@Value` parameters to `*Value` parameters. Without this fix, those
functions can't take `*Value`.

## Likely cause

The codegen for `OP_LOAD` or `OP_GET_FIELD_PTR` uses the field type
from the struct layout. When the struct is defined in a `.bni` file
and used across packages, the field type resolution may differ from
the standalone case. Specifically, `@T` fields accessed through `*T`
(raw pointer) might lose their managed-pointer-ness and be treated
as plain integers.

The relevant codegen is in:
- `pkg/ir/gen_selector.bn` — `genSelector` for raw pointer field access
- `pkg/codegen/emit_helpers.bn` — `emitGetFieldPtr` and `emitLoad`
- `pkg/codegen/emit_instr.bn` — `emitLoad`

The `lookupFieldType` in the IR gen may return a different type for
`.bni`-defined structs vs locally-defined structs.

## Workaround

Keep `@Value` as parameter type for reader functions. The ownership
discipline is enforced by convention — `@Value` returned from
`evalExpr` is conceptually a borrow from the temp list, even though
the type system doesn't enforce it.
