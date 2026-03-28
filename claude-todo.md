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

### Self-compiled compiler crashes (SIGSEGV)
- Struct literal init bug fixed (see Done), previously caused most struct conformance failures
- Still crashes — now in `bn_refcount_dec` from `bn_ir__GeneratePackage` (call #2128)
- Root cause: a managed pointer with `free_fn=0` — likely another struct field offset issue or spurious refcount_dec
- 72/74 conformance tests pass in compiled mode; 2 remaining: 067_slice_of_slices, 069_nested_managed_sel

### Remove redundant && workarounds in GeneratePackage
- `gen.bn` `GeneratePackage` still has manually-split `&&` chains from before short-circuit was fixed
- Now redundant — can be simplified back to normal `&&` expressions
- Low priority, harmless as-is

### Backfill unit tests (second pass)
- First pass added 18 tests (15 ir, 3 types)
- A second review pass was discussed but deferred
- Pre-existing `TestRegisterImportStruct` failure needs investigation (expects 2 fields, gets different count)

## Done

### Struct literal field initialization in GeneratePackage
- `GeneratePackage` populated `moduleStructs[si].Typ.Fields` but not `moduleStructs[si].Fields`
- `genCompositeLit` reads `moduleStructs[si].Fields`, so all struct literals were zero-initialized
- Fix: one line — also set `moduleStructs[si].Fields = fields` in the second pass
- Root cause of most struct conformance failures (11→2) and self-compiled compiler SIGSEGV
- Conformance test 074, unit test `TestGeneratePackageStructLitInit`

### Lightweight debug info (-g flag)
- `-g`/`--debug` flag on compile.bn enables DWARF metadata emission
- `source_filename`, `DICompileUnit`, `DIFile`, `DISubroutineType` at module level
- `DISubprogram` + `DILocation` per function, `!dbg` on every instruction via post-processing
- `-g` passed through to clang for compile and link steps
- `BINATE_FLAGS` env var added to conformance runners
- lldb now shows Binate function names and source file in backtraces
- Committed: `56ea542`

### For-loop back-edge with short-circuit conditions
- `genFor` was using `condBlk` (updated to short-circuit merge block by genExpr) for the post→cond jump
- Fix: save `condStart` before condition evaluation, use it for the back-edge
- Root cause of self-compiled compiler hanging in `scanIdentifier` (`for isLetter(ch) || isDigit(ch)`)
- Conformance test 073, unit test `TestGenForShortCircuitBackedge`
- Committed: `04534c7`

### Short-circuit && and || in compiled mode
- Implemented alloca+branch+load pattern with CurBlock tracking in GenContext
- Conformance tests 071/072 pass in all modes
- Committed: `2038329`

### DECL_GROUP import bug
- `RegisterImports` missed DECL_GROUP when resolving cross-package struct fields
- Committed: `f67f494`
