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

### Self-compiled compiler — FULLY PASSING (88/88)
- All conformance tests pass with self-compiled compiler (85 pass + 3 xfail for codegen bugs)
- `findRuntime()` doesn't discover runtime unless `--runtime` flag is passed

### Re-enable bn_refcount_dec freeing (managed pointers only)
- Freeing is disabled in `bn_refcount_dec` in `runtime/binate_runtime.c`
- This only affects **managed pointers** (`@T`), NOT raw slices (`[]T`)
- The inc/dec pairing looks correct: alloc sets rc=1, copy incs, scope exit decs, return skips dec
- Phase 1: uncomment the free, run full suite + self-compilation, fix any use-after-free crashes
- **Slices are intentionally unmanaged** — see design notes below

### Codegen bugs (exposed by conformance 084-086)
- **084**: `arr[:]` array-to-slice — loads `[N x i64]` and passes as `%BnSlice` with no conversion
- **085**: struct composite literal in `append` — alloca pointer passed instead of loaded value
- **086**: slice-typed struct field zero-init — emits `add %BnSlice 0, 0` instead of `zeroinitializer`

### Slice ownership model — design clarification
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`[]T`)** — two words: (raw ptr, length)
- Value types, no refcounting, no GC
- Caller manages lifetime (like C)
- `append` copies (currently O(n) per call — known performance issue)
- Sub-slicing copies data (no aliasing, no double-free risk)
- Cannot be compared to `nil` — check `len(s) == 0` for empty
- `s = nil` is a bootstrap/codegen convenience, not the spec design

**Managed slices (`@[]T`)** — three words: (managed ptr, raw ptr, length)
- Refcounted via the managed pointer (keeps backing allocation alive)
- `@[]T` is syntactic sugar, distinct from `@([]T)` (managed pointer to raw slice)
- `make([]T, n)` returns `@[]T`
- Not yet implemented in the compiler

**Current code deviations from spec** (to fix):
- `gen.bn` emits `emitDecForScopeVars` comment about "slice ownership semantics" —
  this is wrong framing. Raw slices don't need ownership tracking. The comment should
  be removed or replaced with a note that raw slice backing arrays are caller-managed.
- `s = nil` for slices works in bootstrap (Go semantics leaking through) but shouldn't
  exist per spec. Slices are value types; use `len(s) == 0`.
- `append` performance (O(n²) for incremental building) is a known design question —
  see discussion notes. Options: capacity on managed slices, library Buffer[T] type,
  or removing append in favor of explicit buffer types.

### ~~Remove redundant && workarounds in GeneratePackage~~ ✓
- Collapsed nested `if` blocks back to `&&` in GeneratePackage
- Committed: `b26357f`

### ~~Backfill unit tests (second pass)~~ ✓
- First pass added 18 tests (15 ir, 3 types)
- Second pass added 5 tests (3 ir, 2 codegen) covering OP_SLICE_ELEM_PTR, nil-to-slice, struct slice codegen
- ir: 83 → 86 tests, codegen: 12 → 14 tests
- Pre-existing `TestRegisterImportStruct` failure — fixed (`6de59ba`)
- Committed: `cc17909`

## Done

### Nil-to-slice assignment stores i8* instead of zeroed BnSlice
- `moduleStructs = nil` emitted `store i8* null` (8 bytes) to BnSlice global (16 bytes)
- Only cleared data pointer, left len field with stale value (e.g. len=2, data=NULL)
- Self-compiled compiler crashed in lookupStructIdx on cross-pkg struct compilations
- Fix: detect nil-to-slice assignment and re-emit as typed nil slice (zeroinitializer)
- Self-compiled conformance: 80 → 81/81 (FULL PASS)
- Committed: `ce85c8f`

### OP_SLICE_ELEM_PTR for in-place struct slice element access
- `genIndexPtr` only handled arrays, not slices — `sliceOfStructs[i].field = value` silently dropped
- Struct types in moduleStructs never got fields populated → composite literals were zero-initialized
- Fix: added OP_SLICE_ELEM_PTR (bn_slice_get_struct + bitcast, no load) for typed pointer to slice element
- Self-compiled conformance: 68 → 78 (10 struct composite literal tests fixed)
- Committed: `cace611`

### bn_slice_expr_struct and chained managed-ptr assignment workarounds
- `bn_slice_expr_i64` used for struct slices (e.g. `[]VarSlot`) copied n*8 bytes instead of n*elem_size
- Corrupted variable scope tracking in genBlock → lookupVar failures in for-loop bodies
- Also: chained `moduleStructs[si].Typ.Fields = fields` silently dropped (genSelectorPtr couldn't resolve)
- Fix: added `bn_slice_expr_struct` runtime function + broke chained assignments into two steps
- Self-compiled conformance: 8 → 68
- Committed: `21e1c9e`

### Compiled-compiler test runner missing default --root
- Runner didn't pass `--root` for single-file tests, causing bootstrap package imports to fail
- Fix: default to `$BINATE_DIR` matching the compiled runner
- Committed: `499a4d1`

### Runtime Open flags bitmask extraction
- `bn_bootstrap__Open` used `flags == 1` equality checks for base mode
- Combined flags like O_WRONLY|O_CREATE|O_TRUNC (577) didn't match, opened read-only
- Writes silently failed → empty .ll files → link failure in self-compiled compiler
- Fix: extract base mode with `flags & 3` bitmask
- Conformance test 081. 81/81 pass
- Committed: `d7e81c5`

### Uninitialized managed pointer locals hit garbage in refcount_dec
- `var d @Foo` emitted alloca without storing nil
- First assignment did `refcount_dec(old)` on stack garbage (e.g., 0x42)
- Self-compiled compiler crashed in `parseStmt` parsing programs with var declarations
- Fix: initialize managed ptr locals to nil, same as slices
- Conformance test 080, unit test `TestManagedPtrDeclNilInit`. 83/83 pass
- Committed: `b9ef64c`

### String literal assignment to []char missing conversion
- `genAssign` for ident assignment (`s = "hello"`) didn't call `EmitStringToChars`
- Raw `i8*` stored into `%BnSlice` alloca, leaving length at 0
- Self-compiled compiler emitted empty return types, icmp predicates, and truncated function names
- Conformance test 079, unit test `TestAssignStringToChars`. 82/82 pass
- Committed: `f4e5461`

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
