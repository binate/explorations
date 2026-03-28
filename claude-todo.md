# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## In Progress

### Lightweight debug info for compiled binaries
- Pass `-g` to clang in `compile.bn` (both `compileLL` and linker invocations)
- Emit `source_filename` in LLVM IR module header (`emit.bn`)
- Emit `DISubprogram` metadata for each function so debuggers/profilers show Binate function names
- Goal: function-level backtraces on crash instead of bare SIGSEGV

## TODO

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation in `gen.bn` (~40 `genExpr`/`genStmt` call sites)
- Emit `DIFile`, `DICompileUnit`, `DILocation` metadata in `emit.bn`
- Attach `!dbg` references to every emitted LLVM instruction
- Make debug info optional (e.g., `-g` flag on compile.bn, off by default)
- Prerequisite: lightweight debug info (above)

### Self-compiled compiler hangs on input
- The self-compiled compiler binary builds and starts (shows usage without args) but hangs/times out when actually compiling input
- Separate issue from short-circuit fix
- Needs investigation: possibly infinite loop in loader or parser when run as native binary

### Remove redundant && workarounds in GeneratePackage
- `gen.bn` `GeneratePackage` still has manually-split `&&` chains from before short-circuit was fixed
- Now redundant — can be simplified back to normal `&&` expressions
- Low priority, harmless as-is

### Backfill unit tests (second pass)
- First pass added 18 tests (15 ir, 3 types)
- A second review pass was discussed but deferred
- Pre-existing `TestRegisterImportStruct` failure needs investigation (expects 2 fields, gets different count)

## Done

### Short-circuit && and || in compiled mode
- Implemented alloca+branch+load pattern with CurBlock tracking in GenContext
- Conformance tests 071/072 pass in all modes
- Committed: `2038329`

### DECL_GROUP import bug
- `RegisterImports` missed DECL_GROUP when resolving cross-package struct fields
- Committed: `f67f494`
