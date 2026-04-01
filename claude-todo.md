# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## In Progress

### ~~Phase 2: Remove append from the language~~ — DONE
- ~~Implement CharBuf (growable char buffer using make_slice)~~ — DONE
- ~~Migrate all append calls in source code to CharBuf / make_slice / per-type helpers~~ — DONE
- ~~Remove append from conformance tests~~ — DONE
- ~~Remove append from _test.bn files~~ — DONE
- ~~Remove append builtin from parser, type checker, IR gen, codegen, and interpreter~~ — DONE
- Remove `make_raw_deprecated` builtin (replaced by `make_slice`)

## TODO

### Audit and fix `*any` misuse as `void*`
- `*any` is a pointer to an `any` interface value (2 words: data ptr + vtable ptr) — NOT equivalent to C's `void*`
- Code currently uses `*any` where it means "untyped address" — this is semantically wrong
- Replace with `*uint8` (or `*const uint8`) as the opaque byte pointer type, with `bit_cast` to recover the real type
- Audit: design notes (claude-notes.md, claude-discussion-detailed-notes.md), grammar.ebnf, bootstrap interpreter (Go), all self-hosted Binate code (pkg/rt, pkg/ir, pkg/codegen, pkg/interp, pkg/linker, pkg/types, pkg/ast, pkg/lexer, pkg/parser, pkg/bootstrap, compile.bn, main.bn), and .bni interface files
- Update design notes to document `*uint8` as the `void*` equivalent

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### Unit test runners for all 3 modes
- Ensure all 3 runners (bootstrap, selfhost interpreter, compiler) can run Binate unit tests (`-test` flag)
- Currently unit tests may only be exercised via the bootstrap interpreter
- Goal: unit tests run and pass in bootstrap, selfhost, and compiled modes, same as conformance tests

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions for our own packages
- Current layout mixes toolchain internals (token, ast, lexer, parser, types, ir, codegen, linker, interp) with runtime (rt) and bootstrap support (bootstrap)
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live? What distinguishes "shipped with the language" from "toolchain internal"?

### Standard library design
- Start thinking about and designing standard library packages
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is the immediate need (in progress); broader stdlib design should inform its API
- Consider: what's in the language vs. stdlib vs. third-party, naming conventions, minimal footprint for embedded targets

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation in `gen.bn` (~40 `genExpr`/`genStmt` call sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

### Self-compiled compiler — FULLY PASSING (92/92)
- All conformance tests pass with self-compiled compiler (89 pass + 3 xfail for codegen bugs)

### Re-enable rt.RefDec freeing (managed pointers only)
- Freeing is disabled in `rt.RefDec` in `pkg/rt/rt.bn` (dec but no free on zero)
- Also disabled in `bn_alloc`-based `bn_box` path (C runtime still has `bn_alloc`)
- This only affects **managed pointers** (`@T`) and **managed slices** (`@[]T`)
- The inc/dec pairing looks correct: alloc sets rc=1, copy incs, scope exit decs, return skips dec
- Phase 1: enable free in rt.RefDec, run full suite + self-compilation, fix any use-after-free crashes

### Codegen bugs (exposed by conformance 084-086)
- **084**: `arr[:]` array-to-slice — loads `[N x i64]` and passes as `%BnSlice` with no conversion
- **085**: struct composite literal as function arg — alloca pointer passed instead of loaded value
- **086**: slice-typed struct field zero-init — emits `add %BnSlice 0, 0` instead of `zeroinitializer`

### Fix array-to-slice (`arr[:]`) in compiled mode, then clean up conformance tests
- `arr[:]` works in bootstrap and selfhost interpreters, but compiled codegen passes `[N x i64]` as `%BnSlice` with no conversion (XFAIL 084)
- Fix: emit proper conversion in codegen (alloca BnSlice, GEP for data ptr, store len, load result)
- Once fixed: rewrite conformance tests that use `make_slice` + indexed assignment for static data to use the cleaner `[N]T{...}` array literal + `arr[:]` pattern instead
- Also consider adding slice literal syntax (`[]T{...}`) to the parser as sugar for the array+slice pattern

### Slice ownership model — design clarification
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC
- Caller manages lifetime (like C)
- ~~`append` copies (was O(n) per call)~~ — `append` has been removed from the language
- Sub-slicing copies data (no aliasing, no double-free risk)
- Cannot be compared to `nil` — check `len(s) == 0` for empty
- `s = nil` is a bootstrap/codegen convenience, not the spec design

**Managed slices (`@[]T`)** — three words: (data ptr, length, refptr)
- Layout is prefix-compatible with `[]T` — first two words are identical
- Refcounted via the refptr (field 2), which is a managed pointer to the backing allocation
- `@[]T` is syntactic sugar, distinct from `@([]T)` (managed pointer to raw slice)
- `make_slice(T, n)` returns `@[]T` (new builtin, replaces old `make([]T, n)`)
- `@[]T → []T` conversion: trivial extractvalue of fields 0,1 (OP_MANAGED_TO_RAW)
- **Implemented in compiler**: type system (24 bytes), codegen (%BnManagedSlice), refcounting (extract refptr, call rt.RefInc/RefDec), make (calls rt.MakeManagedSlice), conversion (@[]T → []T)

**Current code deviations from spec** (to fix):
- `s = nil` for slices works in bootstrap (Go semantics leaking through) but shouldn't
  exist per spec. Slices are value types; use `len(s) == 0`.
- ~~`append` has been removed from the language~~ — DONE (replaced by `buf.CharBuf`, `make_slice`, and per-type helpers)

### ~~Phase 2: Remove append + library buffer types~~ — DONE
- ~~Implement managed-slices (`@[]T`) — three words: (data_ptr, length, refptr)~~ — DONE
- ~~Implement @[]T refcounting (inc on copy, dec on scope exit)~~ — DONE
- ~~Implement @[]T → []T conversion (OP_MANAGED_TO_RAW)~~ — DONE
- ~~Create pkg/rt with Alloc, RefInc, RefDec, MakeManagedSlice~~ — DONE
- ~~Migrate codegen from C runtime to pkg/rt~~ — DONE
- ~~Package search paths for multi-root package resolution~~ — DONE
- ~~Remove `append` builtin from the language~~ — DONE
- ~~Write CharBuf and library buffer types for growable collections~~ — DONE
- ~~Switch compiler internals from `[]T` + append to managed-slices / buffer types~~ — DONE

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

### @[]T refcounting, OP_MAKE_SLICE migration, C runtime cleanup
- Added `@[]T` refcounting: extract refptr (field 2), call rt.RefInc/RefDec at var declarations, assignments, field assignments, function params, scope exit, return cleanup
- `isFreshManagedSlice` check skips refcount inc for `OP_MAKE_SLICE` results (already rc=1)
- Migrated `OP_MAKE_SLICE` codegen from inline alloc+insertvalue to `rt.MakeManagedSlice` call
- Removed `bn_refcount_inc`, `bn_refcount_dec`, `bn_make_managed_slice` from `binate_runtime.c`
- 91 compiled / 90 bootstrap / 90 selfhost — all passing
- Committed: `80b5150`

### Self-hosted interpreter HeapObj tracking for managed slices
- Added `Refcount int` to HeapObject struct
- `MakeManagedSliceVal` constructor creates HeapObj with Refcount=1
- `copyValue` increments Refcount when copying managed slices (sharing semantics)
- `coerce` handles `@[]T → []T` conversion (strips HeapObj, shares Elems)
- ~~`evalAppendCall` preserves managed-ness on append results~~ (append has been removed)
- `isCharSlice` recognizes `@[]char` (TYP_MANAGED_SLICE)
- Bootstrap interpreter updated in parallel (SliceVal gains HeapObj, same semantics)
- 92 compiled / 91 bootstrap / 91 selfhost — all passing
- Conformance: 095_managed_slice_sharing
- Committed: `c997b9f` (binate), `4e346c5` (bootstrap)

### Package search paths and implicit pkg/rt import
- Loader supports multiple roots (`Roots [][]char`), iterates them in `loadPackage`
- `discoverBinateRoot` derives project root from runtime path (two `dirOf` up from binate_runtime.c)
- Compiler adds binate project root as secondary search path via `loader.AddRoot`
- `ensureRtLoaded` creates synthetic import for pkg/rt; `appendRtImport` adds it to every module
- Deduplication: skips implicit rt import when explicit import exists
- Cross-package conformance tests (061-065) find pkg/rt even with custom `--root`
- 91 compiled / 90 bootstrap / 90 selfhost — all passing
- Committed: `ad394ee`

### @[]T layout, MakeManagedSlice, @[]T → []T conversion
- Updated `@[]T` layout from `{ refptr, data, len }` to `{ data, len, refptr }` (prefix-compatible with `[]T`)
- Added `MakeManagedSlice` to pkg/rt (Binate implementation, not C runtime)
- Added `OP_MANAGED_TO_RAW` for `@[]T → []T` conversion (extractvalue fields 0,1)
- Implicit coercion at var declarations, assignments, function call arguments
- Fixed `moduleFuncs = nil` bug that cleared imported function signatures
- Conformance tests: 093_rt_managed_slice, 094_managed_to_raw_slice
- Committed: `da07f70`

### bit_cast, pointer indexing, and pkg/rt Binate runtime
- `bit_cast(TargetType, val)` codegen: ptrtoint/inttoptr/bitcast as appropriate
- Pointer indexing `ptr[i]` and `ptr[i] = val` via GEP (supports negative indices)
- Created `pkg/rt` with Alloc, Free, RefInc, RefDec (Binate implementations)
- Created `runtime/rt_stubs.c` with thin C wrappers for libc (c_malloc, c_free, c_memset, c_memcpy)
- Conformance tests: 090_bit_cast, 091_pointer_indexing, 092_rt_alloc
- Committed: `c80d962`

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

### Nil-to-slice calling convention, slice free
- Nil passed to slice params emitted `i8*` (1 reg) instead of `%BnSlice` (2 regs), shifting args
- Root cause of crash: `bn_refcount_inc` received ASCII "_newline" data in shifted x6 register
- (Historical note: also fixed append refcount bug, but append has since been removed)
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
