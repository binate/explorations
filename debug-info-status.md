# DWARF Debug Info Status

## What's Implemented

The Binate compiler emits DWARF debug metadata when the `-g` flag is passed.
This enables source-level debugging with lldb/gdb and meaningful stack
traces in tools like valgrind and AddressSanitizer.

### Line-level source mapping (2026-04-09)

- Per-instruction `!dbg !DILocation(line: N, scope: !M)` with real line
  numbers from AST nodes.
- `genExpr` annotates expression results with `e.Pos.Line`.
- `genBlock` annotates all instructions emitted per statement.
- Line numbers are from the merged package view (all files in a package
  get the same `DIFile`). This means line numbers for multi-file packages
  may not match individual source files.

### Variable names (2026-04-13)

- `emissionKind: FullDebug` (was `LineTablesOnly`).
- `DIBasicType` for `i64` (used as a generic type for all variables).
- `DILocalVariable` with variable name for each named alloca.
- `llvm.dbg.declare` intrinsic binds allocas to their debug variables.
- Variable names come from `defineVar`/`defineVarParam` in the IR gen,
  propagated to `OP_ALLOC` instructions via the `StrVal` field.

### What works in lldb

```
(lldb) frame variable
(long) x = 42
(long) name = 54307850800    ← managed-slice shown as raw i64
```

Variable names are correct. Types are all shown as `long` (i64) since
we only emit one `DIBasicType`.

## What's Missing

### Per-file DIFile

Currently all functions in a package share one `DIFile` with the package
name (e.g., `pkg/ir.bn`). Multi-file packages should have per-file
`DIFile` entries so line numbers match the actual source file. Requires:
- Track source file per function in the IR (`Func.File` field)
- Emit multiple `DIFile` entries
- Reference the correct `DIFile` from each `DISubprogram`

### Accurate types

All variables are typed as `DIBasicType("int", 64)`. For better debugging:
- `DIBasicType` for bool (1-bit), uint8/char (8-bit), int32 (32-bit), etc.
- `DIDerivedType(DW_TAG_pointer_type)` for `*T` and `@T`
- `DICompositeType(DW_TAG_structure_type)` for structs with field layout
- `DICompositeType(DW_TAG_array_type)` for arrays
- Struct field names and offsets would let debuggers show field values

### Function parameters

Function parameters are not yet tagged with `DILocalVariable`. Only
`OP_ALLOC` instructions created by `defineVar`/`defineVarParam` get names.
The parameter allocas in `genFunc` (gen_stmt.bn line 81) should also be
tagged.

### Scope nesting

All variables are scoped to the function's `DISubprogram`. LLVM supports
`DILexicalBlock` for inner scopes (if/for/block bodies), which would
let debuggers show only in-scope variables.

### DISubprogram line numbers

`DISubprogram` has `line: 0` and `scopeLine: 0`. These should be the
function's declaration line from the AST.

### Temp/generated variables

Compiler-generated temporaries (struct copies, managed-slice conversions,
etc.) don't have names. They show up as unnamed allocas in the debugger.
Could be given synthetic names like `_tmp.0`, `_copy.1` for clarity.

## Implementation Details

### Metadata ID layout

```
!0 = DICompileUnit
!1 = Dwarf Version flag (i32 4)
!2 = Debug Info Version flag (i32 3)
!3 = DIFile (package-level)
!4 = DISubroutineType (generic, empty types)
!5 = DIBasicType ("int", 64-bit, signed)
!6, !7 = DISubprogram + DILocation for first non-extern function
!8, !9 = DISubprogram + DILocation for second non-extern function
...
```

DILocalVariable and DILocation are emitted inline (not as numbered
metadata) to avoid managing a growing ID counter.

### Code locations

- `pkg/codegen/emit_debug.bn` — all debug metadata emission
- `pkg/codegen/emit.bn` — metadata ID assignment
- `pkg/ir/gen_stmt.bn` — variable name propagation to allocas
- `pkg/ir/gen_expr.bn` — line number annotation on expressions
