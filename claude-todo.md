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
- ~~Remove `make_raw_deprecated` builtin (replaced by `make_slice`)~~ — DONE

## TODO

### Self-hosted interpreter memory model parity with compiler
- The self-hosted interpreter (cmd/bni / pkg/interp) currently uses a tagged-union value model (`VAL_INT`, `VAL_STRUCT`, `VAL_SLICE`, etc.) that doesn't correspond to flat memory. This means `bit_cast` and pointer indexing can't work because there are no real addresses to reinterpret or index into.
- Currently XFAIL: 090 (bit_cast), 091 (pointer indexing), 092/093 (pkg/rt — depends on both)
- **This is more tractable than it first appears.** The fix is to make the interpreter lay out data in memory the same way the compiler does:
  - Structs: fields at the same offsets with the same padding (matching `SizeOf`/`AlignOf`/`FieldOffset`)
  - `@T`: heap-allocated with the refcount header at negative offset (matching `rt.Alloc` layout)
  - `@[]T`: `{data_ptr, len, backing_refptr, backing_len}` — same 4-word `%BnManagedSlice` layout
  - `[]T`: `{data_ptr, len}` — same 2-word `%BnSlice` layout
  - Arrays: contiguous elements at `elem_size` stride
- With ABI-compatible layout, `bit_cast(int, ptr)` is just reading the pointer address as an integer, and `ptr[i]` is pointer arithmetic — no simulated heap needed, just real memory operations on the interpreter's own heap.
- **Scope**: this is a refactor of `pkg/interp`'s value representation, not a fundamental architecture change. The interpreter already tracks types, sizes, and refcounts — it just needs to store values in flat memory instead of tagged unions.
- **Note**: `pkg/rt` uses `bit_cast` and pointer indexing, so it can only be loaded as a compiled package today. Once the interpreter has ABI-compatible layout, pkg/rt could run interpreted too — but since it's a low-level runtime, keeping it compiled-only is also reasonable.

### Temporary lifetime (statement-level implicit scope)
- Investigate current behavior in both the bootstrap interpreter and self-hosted compiler
- **Interpreter**: does the bootstrap interpreter already release temporaries at statement boundaries, or are they released immediately after expression evaluation? Check how managed values created in function call arguments are tracked.
- **Self-hosted interpreter** (pkg/interp): same investigation — does `evalExpr` / `evalCallExpr` keep temporaries alive through the call?
- **Compiler** (pkg/codegen): when `@[]int{1,2,3}` is passed to a function taking `[]int`, does the emitted LLVM IR keep the managed allocation alive through the call? Check whether `genCallExpr` emits release after the call returns or earlier.
- Spec: temporaries are unnamed locals in a statement-level implicit scope, released at statement end
- See claude-notes.md "Temporary lifetime" and claude-discussion-detailed-notes.md section 19.6

### ~~Remove implicit null termination from string literals~~ — DONE
- All 3 environments updated: bootstrap (was already clean), compiler (LLVM constants no longer emit `\00`, `bn_print_string`/`bn_string_to_chars` take `(i8*, i64)`), self-hosted interpreter (`MakeStringVal` no longer appends `\0`, `strLen`/`strContent` removed)

### ~~Audit and fix `*any` misuse as `void*`~~ — DONE
- All `*any` in pkg/rt (.bn, .bni), tests, and conformance tests replaced with `*uint8`
- No other Binate source files used `*any`
- Design notes (claude-notes.md) updated: `*uint8` is the opaque byte pointer; `any` reserved for future empty interface type
- `any` still registered in type checker universe scope (as `TypVoid()`) for forward compatibility

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### ~~Unit test runners for all 3 modes~~ — DONE
- All 3 tools support `-test`/`--test`: bootstrap, bni, bnc
- Unit test runner (`scripts/unittest/run.sh`) with modes: boot, boot-int, boot-comp, boot-comp-int
- All pass (pkg/rt xfail'd in interpreter modes, passes in boot-comp)

### Backfill negative conformance tests
- The conformance test framework now supports `.error` files for negative tests (programs that should fail to compile/type-check)
- Currently only 112_slice_nil_rejected exists
- Need negative tests for at least: type mismatches, undeclared variables, wrong number of arguments, invalid type conversions, duplicate declarations, invalid nil usage on non-pointer types
- Both bni and bnc now run the Binate type checker, so negative tests work across all modes

### ~~Array element field access and selector-base array assignment~~ — FIXED
- `arr[i].Field` where arr is `[N]@T`: genSelector now handles managed-ptr elements from genIndexPtr
- `cont.Items[i] = v`: genControl and genIndexPtr now handle EXPR_SELECTOR base for arrays
- Conformance tests: 117 (array elem field), 118 (selector array assign)

### Compiler must process package's own .bni file
- When compiling a package, the compiler currently only processes `.bn` files via `GeneratePackage`. Struct types defined in the `.bni` are only seen through the import mechanism (as qualified names like `token.Token`).
- **Problem**: Almost all structs are defined only in `.bni` files (not redefined in `.bn`). This means the package's own struct definitions aren't compiled as local types — they're treated as cross-package imports of itself.
- **Fix**: The compiler should load and process the package's own `.bni` file when compiling. Struct definitions from the `.bni` should be registered as local (unqualified) types, just as if they were in a `.bn` file.
- **Impact**: Simplifies destructor generation (local structs get local dtors, no qualified-name gymnastics). Also needed for correctness — struct type definitions need to be in the compiled output.
- **Related**: Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature, not yet needed.

### ~~Anonymous struct destructors~~ — DONE
- Anonymous structs compile and work in boot-comp, including with managed fields
- IR gen assigns synthetic names (`__anon_N`), deduplicates identical structs
- Dtor naming uses field type sequence: `__dtor_anon_<type1>_<type2>_...` with hash fallback for long names
- Conformance tests: 113 (anon struct dtor), 119-121 (field, param, return)

### ~~Anonymous struct types in the compiler~~ — DONE
- IR gen assigns synthetic names (`__anon_N`), registers in moduleStructs, deduplicates identical field sequences
- Works for variable types, struct fields, function parameters, return types
- Conformance tests: 119 (field), 120 (param), 121 (return), 113 (dtor)

### Function-local type declarations — design question
- Go supports `type Foo struct { ... }` inside function bodies. Binate currently doesn't handle this in the compiler (works in bootstrap interpreter).
- **Consider**: do we want function-local types at all? They're somewhat limited in Go (can't define methods on them in the same scope, can't use them outside the function).
- If not, the parser should reject them. If yes, the IR gen needs to handle them (register the struct type when encountered in the function body).
- Low priority — package-level types cover most use cases.

### Verify anonymous struct equivalence
- Anonymous structs should use structural equivalence: same type iff field names AND types match in sequence (Go semantics)
- Verify this is implemented correctly in both type checkers (bootstrap Go and self-hosted Binate)
- Test cases: same fields same order (equal), reordered fields (not equal), same types different names (not equal)
- See claude-notes.md and claude-discussion-detailed-notes.md section 22

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions for our own packages
- Current layout mixes toolchain internals (token, ast, lexer, parser, types, ir, codegen, linker, interp) with runtime (rt) and bootstrap support (bootstrap)
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live? What distinguishes "shipped with the language" from "toolchain internal"?

### Standard library design
- Start thinking about and designing standard library packages
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs
- Consider: what's in the language vs. stdlib vs. third-party, naming conventions, minimal footprint for embedded targets

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation in `gen.bn` (~40 `genExpr`/`genStmt` call sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

### ~~Self-compiled compiler — FULLY PASSING~~ ✓
- All 98 conformance tests pass with self-compiled compiler (boot-comp-comp: 0 fail, 0 xfail)
- Gen2 compiler (boot-comp-comp-comp) also passes 98/98

### ~~Re-enable rt.RefDec freeing~~ — DONE
- Free is called in `rt.RefDec` when refcount hits 0
- Slice element refcounting (RefInc on store, RefDec on overwrite) is implemented
- Destructors call `dtor(ptr)` before `Free(ptr)` for struct types with managed fields

### ~~Codegen bugs (084-086)~~ — ALL FIXED
- ~~**084**: `arr[:]` array-to-slice~~ — fixed: genSliceExpr builds BnSlice from array alloca
- ~~**085**: struct composite literal in slice element write~~ — fixed: genIndexPtr handles @[]T, load struct before slice_set
- ~~**086**: nested struct slice field write~~ — fixed: genIndexPtr handles selector base (c.Items[i]), positional composite literals, nil zero-init for slice/pointer fields

### Clean up conformance tests to use array literal + `arr[:]` pattern
- Now that `arr[:]` works in compiled mode, conformance tests that use `make_slice` + indexed assignment for static data could use the cleaner `[N]T{...}` array literal + `arr[:]` pattern instead
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

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Layout is prefix-compatible with `[]T` — first two words are identical
- Refcounted via the backing_refptr (field 2), which is a managed pointer to the backing allocation
- backing_len (field 3) stores total element count for destructor cleanup
- `@[]T` is syntactic sugar, distinct from `@([]T)` (managed pointer to raw slice)
- `make_slice(T, n)` returns `@[]T` (new builtin, replaces old `make([]T, n)`)
- `@[]T → []T` conversion: trivial extractvalue of fields 0,1 (OP_MANAGED_TO_RAW)
- **Implemented in compiler**: type system (24 bytes), codegen (%BnManagedSlice), refcounting (extract refptr, call rt.RefInc/RefDec), make (calls rt.MakeManagedSlice), conversion (@[]T → []T)

**Current code deviations from spec** (to fix):
- ~~`s = nil` for slices~~ — DONE: both type checkers now reject nil on slices. All code migrated to `len(s) == 0`.
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

### Struct destructors and managed-slice element cleanup
- `rt.RefDec(ptr *uint8, dtor *uint8)` — dtor is called before Free when rc hits 0
- `c_call_dtor` C stub for indirect function pointer call (bootstrap subset can't call fn ptrs)
- `types.NeedsDestruction(t)` — recursive query for types requiring cleanup
- `OP_FUNC_ADDR` — new IR opcode for function address as `i8*`
- `generateStructDtors(m)` — generates `__dtor_<Name>` IR functions for structs with managed fields
  - Handles local structs, .bni-only structs, and cross-package extern declarations
- `emitManagedPtrRefDec(f, b, ptrVal)` — passes struct dtor to RefDec at all managed pointer cleanup sites
- `emitManagedSliceRefDec` — emits element cleanup loop when element type needs destruction
  - Checks `Refcount(backing) == 1` before iterating (only on last reference)
  - Loops over `backing_len` elements, loads each, RefDec's with appropriate dtor
  - Returns continuation block (callers updated across gen_util, gen_stmt, gen_flow, gen_expr, gen_control)
- `*any` → `*uint8` migration in pkg/rt (opaque byte pointer, not interface pointer)
- Conformance tests: 114 (struct dtor), 115 (slice element dtor)
- Anonymous struct dtors: xfail 113, TODO for future (naming by field types)

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
