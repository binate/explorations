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

### Self-compiled compiler — remaining link/runtime issue
- Five codegen bugs fixed (see Done section); compiler no longer crashes or truncates
- Self-compiled binary now parses args and compiles, but link fails: `_main` undefined
- Likely missing runtime linkage — `findRuntime()` may not locate `binate_runtime.c`
- Freeing temporarily disabled in bn_refcount_dec; slice ownership semantics needed

### Remove redundant && workarounds in GeneratePackage
- `gen.bn` `GeneratePackage` still has manually-split `&&` chains from before short-circuit was fixed
- Now redundant — can be simplified back to normal `&&` expressions
- Low priority, harmless as-is

### Backfill unit tests (second pass)
- First pass added 18 tests (15 ir, 3 types)
- A second review pass was discussed but deferred
- Pre-existing `TestRegisterImportStruct` failure — fixed (`6de59ba`)

## Done

### Stale ctx.CurBlock after if drops subsequent statements
- `genIf` returns merge block but doesn't set `ctx.CurBlock`
- `genStmt` STMT_DECL returned stale `ctx.CurBlock` (pointed to then-block with terminator)
- `genBlock` loop saw terminated block and stopped processing remaining statements
- Root cause of self-compiled compiler producing empty binary (all code after arg check dropped)
- Fix: set `ctx.CurBlock = b` before `genDecl` call
- Conformance test 078, unit test `TestDeclAfterIfBlock`. 81/81 pass
- Committed: `0f4afa8`

### STMT_DECL wrong block after short-circuit in initializer
- `genStmt` returned original `b` instead of `ctx.CurBlock` after `genDecl`
- When `||`/`&&` in var initializer creates new blocks, subsequent stmts on wrong block
- Root cause of unreachable crash in genFunc (`var isVoid bool = ... || ...`)
- Conformance test 077, unit test `TestDeclShortCircuitBlock`. 80/80 pass
- Committed: `22ba787`

### Nil-to-slice calling convention, append refcount, slice free
- Nil passed to slice params emitted `i8*` (1 reg) instead of `%BnSlice` (2 regs), shifting args
- Root cause of crash: `bn_refcount_inc` received ASCII "_newline" data in shifted x6 register
- Also: append of managed ptr to slice didn't inc refcount; slice free caused dangling ptr
- Conformance test 076, unit test `TestNilSliceArgCoercion`. 79/79 pass
- Committed: `7e5a6b9`

### RegisterImport missing Fields
- Same bug pattern as GeneratePackage struct literal init — `moduleStructs[si].Fields` not set
- Committed: `6de59ba`

### Managed pointer field assignment refcounting
- `genAssign` EXPR_SELECTOR path didn't manage refcounts for managed pointer fields
- Assigning `o.Ptr = val` didn't inc new value or dec old value → use-after-free
- Root cause of self-compiled compiler SIGSEGV (PC=0x0 from freed free_fn header)
- Fix: emit refcount_dec(old), refcount_inc(new) before store in field assignment
- Also added NULL free_fn safety abort in `bn_refcount_dec`
- Conformance test 075, unit test `TestFieldAssignRefcount`. 75/75 pass
- Committed: `a340080`

### String-to-chars in slice set & nested managed selector ptr
- Slice set of string to `[][]char` element didn't convert via `bn_string_to_chars`
- Nested managed selector ptr (`o.Inner.Value`) didn't handle `TYP_MANAGED_PTR`
- Conformance tests 067/069, unit tests `TestSliceSetStringToChars`/`TestNestedManagedSelectorPtr`
- Committed: `506f437`

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
