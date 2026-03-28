# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## In Progress

(none)

## TODO

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation in `gen.bn` (~40 `genExpr`/`genStmt` call sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

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

### Lightweight debug info (-g flag)
- `-g`/`--debug` flag on compile.bn enables DWARF metadata emission
- `source_filename`, `DICompileUnit`, `DIFile`, `DISubroutineType` at module level
- `DISubprogram` + `DILocation` per function, `!dbg` on every instruction via post-processing
- `-g` passed through to clang for compile and link steps
- `BINATE_FLAGS` env var added to conformance runners
- lldb now shows Binate function names and source file in backtraces
- Committed: `56ea542`

### Short-circuit && and || in compiled mode
- Implemented alloca+branch+load pattern with CurBlock tracking in GenContext
- Conformance tests 071/072 pass in all modes
- Committed: `2038329`

### DECL_GROUP import bug
- `RegisterImports` missed DECL_GROUP when resolving cross-package struct fields
- Committed: `f67f494`
