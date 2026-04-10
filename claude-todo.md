# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## TODO

### ~~Linux/x86-64: boot-comp-comp string corruption~~ — FIXED
- **Root cause**: use-after-free in `cmd/bnc/test.bn`. `runtimePath` was declared as `[]char` (raw slice) instead of `@[]char` (managed). When the `candidate @[]char` from `bootstrap.Concat(root, "/runtime/binate_runtime.c")` went out of scope, it was RefDec'd and freed — but `runtimePath` still borrowed its data, creating a dangling pointer. The garbage filenames were freed memory being read as strings.
- **Fix**: changed `var runtimePath []char` to `var runtimePath @[]char = buf.CopyStr(cli.RuntimePath)` in test.bn, matching the pattern already used in main.bn.
- **CI now runs all modes** including boot-comp-comp and boot-comp-comp-comp.

### Self-hosted interpreter: boot-comp-int-int still failing
- boot-comp-int-int (compiled bni interprets bni which interprets test) still has issues.
- **Known issue**: inner interpreter function return values get wrapped in an extra `@Value` indirection through flat memory. When the outer compiled interpreter interprets the inner interpreter's `callFunc`, the return value goes through flat serialization/deserialization which adds a managed pointer layer. `println(add(3, 4))` prints "nil" instead of "7".
- Removed from mode sets. Needs dual-mode interop (flat memory roundtripping of `@Value` tagged unions) to work properly.

### Self-hosted interpreter memory model parity with compiler
- Plan: `explorations/plan-interp-memory-parity.md`
- The self-hosted interpreter can no longer run under the bootstrap (boot-int dropped) since it now uses `bit_cast`, pointer indexing, and `pkg/rt`. It requires compiled mode (boot-comp-int and above).
- **boot-comp-int: 157/158 conformance tests pass** (was 129 before this work began)
- Phase 1 (done): infrastructure — `flat.bn` with readFlatValue/writeFlatValue using bit_cast and pkg/rt
- Phase 2 (done): scalar variables in flat memory — envDefine/envGet/envSet use flat addresses for ints
- Phase 3 (done): structs in flat memory — `evalMake` allocates via `rt.Alloc`, all field access through `RawAddr + FieldOffset`. Lazy struct reads (no eager field materialization). Self-referential type resolution (in-place field update).
- Phase 4 (done): raw slices in flat memory — `[]T` as `{data, len}` in 16 bytes. `arr[:]` creates flat slices.
- Managed pointer refcounting (done): `envDefine` RefInc, `envSet` RefDec/RefInc, `cleanupEnvExcept` for scope exit, `interpRefDec` for recursive struct field cleanup, `interpCleanupSlice` for managed-slice element cleanup. Return values excluded from scope cleanup.
- bit_cast (done): pointer↔int, pointer↔pointer — 090 passes
- Pointer indexing (done): `p[i]` read/write, `&arr[i]` — 091 passes
- pkg/rt forwarding (done): c_malloc, Alloc, Free, RefInc, RefDec, Refcount, MakeManagedSlice — 092, 093, 104, 123 pass
- Pointer comparison (done): `p == q` via RawAddr
- String→[]char for flat slices (done): 079, 088 pass
- C ABI sret fix (done): large struct returns from C externs on ARM64
- TYP_NAMED resolution (done): `resolveUnderlying` resolves named types (`type Kind int`) in flat read/write paths
- Lazy struct optimization (done): `readFlatValue` for TYP_STRUCT returns RawAddr-only Values, avoiding O(n) allocation per field access. Fixed parser.ParseFile hang in boot-comp-int.
- Managed-slice flat storage (done): `TYP_MANAGED_SLICE` in `useFlatType`. 32-byte flat headers with `rt.MakeManagedSlice` backing. Flat-to-flat copy, subslicing, `@[]T→[]T` coercion, element refcounting. Fixed tests 126, 129.
- Managed-slice backing refcounting (done): envDefine/envSet RefInc/RefDec for backing_refptr. cleanupEnvExcept RefDec on scope exit. Element-level RefInc/RefDec for managed-ptr elements in flat index assignment.
- Full flat migration (done): ALL data types use flat storage (int, bool, []T, @[]T, @T, *T, [N]T, struct, string, named types). Only function values remain Cell-based.
- readFlatValue no longer materializes Elems (O(n) → O(1)). All consumers (for-in, index, len, print, subslice) use flat paths.
- Legacy Elems code removed: MakeSliceVal, MakeArrayVal, MakeManagedSliceVal removed. writeFlatValue Elems→flat conversion removed. Elems refs: 53→3 (VAL_MULTI only). HeapObj refs: 30→3 (function values only).
- All refcounting fixed: return leak (IsFresh flag), element-copy, struct field, assignment cascade, pointer deref write, managed-slice element cleanup (rc==1 check).
- **161/161 in boot-comp, boot-comp-int, and boot-comp-comp. Zero xfails.**

### Interpreter Value struct cleanup
- **Done**: removed Elems, Fields, HeapObj, BoolVal, IntVal, StrVal, VAL_MULTI, VAL_STRING. All scalar caches eliminated.
- **Remaining**: 3 HeapObject refs for function-value Cell storage, IntTyp (int type info for readScalar width).
- **String literals**: compiler done (static `%BnManagedSlice` globals, `bn_string_to_chars` removed). Interpreter done (StrVal removed, MakeStringVal produces flat @[]char).
- See `explorations/plan-string-literals.md` for full plan.
- See `explorations/plan-interp-memory-parity.md` for function values.

### Lift string literal lowering from LLVM backend to IR level
- Currently, `OP_STRING_TO_CHARS` is lowered to a `load %BnManagedSlice` from a static global in `emit_instr.bn` (LLVM backend). The string constant collection and global emission are also in the LLVM backend (`emit.bn`).
- For multi-backend support, this should be at the IR level: an IR instruction like `OP_STRING_LITERAL` that produces an `@[]char` value. String constant globals become IR-level module data. Each backend then lowers to its own representation (LLVM: load from constant global; ARM: load from data section address; etc.).
- See `explorations/ir-backend-guidelines.md` for the IR vs backend responsibility split.

### Function values: compiled-compatible representation (required for interop)
- Function values MUST use the same representation in compiled and interpreted code, because function values can be passed between the two modes (compiled code calling interpreted functions and vice versa).
- **Target**: `{funcPtr, closureCtx}` pair matching compiled representation. For interpreted functions, `funcPtr` would be a trampoline that dispatches into the interpreter using `closureCtx` to find the AST decl, closure env, types, and aliases.
- **Current**: Cell-based `FuncVal` with interpreter-level metadata. Works because the bootstrap subset doesn't have closures or first-class function values.
- **When this blocks**: closures, function values in slices/maps, callbacks between compiled and interpreted code.
- See `explorations/plan-interp-memory-parity.md` for details.

### Self-hosted interpreter refcounting — ALL FIXED
- **No known memory issues.** boot-comp-int: 158/158. Zero xfails.
- Return leak fixed via IsFresh flag on Value (make/make_slice/box set IsFresh; execReturn sets IsFresh for local-ident returns via envGetLocalAddr; envDefine/envSet skip RefInc when IsFresh).
- Element-copy refcounting fixed for managed-ptr, managed-slice, and struct elements in slice/array assignment.
- Struct field assignment RefInc/RefDec for managed-ptr and managed-slice fields.
- Managed-slice element cleanup: only iterates elements when backing refcount==1 (last reference). Handles managed-ptr, managed-slice, and struct elements.
- Assignment cascade: RefInc new before RefDec old (cascade-safe) for managed-ptrs.
- Pointer deref write (*p = val): RefInc/RefDec for managed types.


### Verify .bni vs .bn visibility semantics
- Both `.bni` and `.bn` files can contain type declarations, constants, aliases, and globals
- `.bni` declarations are public (visible to importers); `.bn` declarations are private
- **Verify**: that declarations in `.bn` files are NOT accessible to importing packages
- **Add negative conformance tests**: importing a private type/constant should fail to compile
- **Check**: if the same name is declared in both `.bni` and `.bn`, does it cause duplicate registration errors?
- **Related**: Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### ~~Raw slice subslice expression copies data (bug)~~ — FIXED
- Fixed by lowering `OP_SLICE_EXPR` to primitive IR ops (step 3.1). Raw slice `s[lo:hi]` now produces a zero-copy view `{data + lo * elemSize, hi - lo}` via GEP. The C runtime `bn_slice_expr_*` functions (which incorrectly copied) have been removed.

### Continue backfilling negative conformance tests
- 19 negative tests exist (112, 200-210, 214-220), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types
- `.error` files now use `grep -E` regex matching (patterns like `(foo|bar)` match across type checkers)
- Still needed: shadowing errors, import errors, package mismatch, type conversion errors, const expression errors
- Some errors not caught by either type checker: break outside loop, missing return, assign to const

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### Function-local type declarations — design question
- Go supports `type Foo struct { ... }` inside function bodies. Binate currently doesn't handle this in the compiler (works in bootstrap interpreter).
- **Consider**: do we want function-local types at all? They're somewhat limited in Go.
- If not, the parser should reject them. If yes, the IR gen needs to handle them.
- Low priority — package-level types cover most use cases.

### Test harness `isTestResultReturn` should resolve type aliases
- All three test harnesses (bootstrap Go `main.go`, self-hosted `cmd/bni/main.bn`, self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bni/main.bn:isTestResultReturn`, `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

### x86-64 assembler: end-to-end tests on Linux CI
- Assemble x86-64 → ELF64 → link → run natively (no QEMU needed)
- CI runs Linux x86-64 so this would be a native end-to-end test
- Test cases: exit via SYSCALL, loop, function call with PUSH/POP

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`[]T{...}`) as sugar

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation (~40 sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions
- Current layout mixes toolchain internals with runtime and bootstrap support
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live?

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### ~~Compiler bug: multi-return with struct containing managed fields~~ — FIXED
- Bug was already fixed by earlier refcounting changes. Workaround reverted. Test 141 passes.

### Multi-return as anonymous struct — compiler DONE, interpreter TODO
- Multi-return is an ABI contract: `func f() (T1, T2)` returns `struct { _0 T1; _1 T2 }`.
- **Compiler side done**: `Func.MultiReturnType` struct type, propagated through FuncSig/call sites/return instructions, LLVM emission uses `llvmType(MultiReturnType)`.
- **Interpreter side TODO**: replace VAL_MULTI/Elems with flat anonymous struct. This eliminates the last 3 Value.Elems references.
- **Plan**: `explorations/plan-multi-return-struct.md`

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → []T` conversion: extractvalue fields 0,1.

### Test runner improvements
- **Better filtering**: ability to specify which tests to run more precisely, especially for unit tests (e.g., individual test functions, not just packages).
- **Better mode specification**: support specifying multiple modes, e.g., `boot,boot-comp` instead of requiring a predefined mode set.
- **Better output**: auto-summarize on success, but more verbose/explicit output for errors and failures.
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Better docs/help**: improve documentation and help output for the test runners.
- **Mode sets in files**: define mode sets in files (e.g., a directory of mode set definitions) so adding a new mode set is just adding a file. CI runners could read these files to manually run everything in a mode set.
- **Parallelization**: consider running test packages in parallel within a mode.

### Compiler/interpreter interop
- **Goal**: enable calling between compiled and interpreted code in both directions.
- **Start with**: exposing compiled packages to the interpreter.
  - Pass compiled package objects to the interpreter at init time.
  - **Question**: how does one specify/describe a package to the interpreter?
  - **Proposal**: for each package, build an object/struct describing it (based on the .bni). Made available automatically, e.g., `import "pkg/foo"` gives you `foo.Package` (or similar). But naming conflicts are a concern (see import aliases below).
  - Alternatively, packages could be referred to by path (e.g., `"pkg/foo"`), but that's less powerful. Having package objects means they could also be constructed dynamically in code.
  - Another option: `foo` itself *is* the package object after import, but this may be confusing. Also unclear what the "self" package object for the current package would be.
- **Interpreter structure**: separate initialization from calling. Starting a program = init (with main package, search paths, etc.) + call main.
  - Init could also accept compiled package objects.
  - Loading could be a series of steps during init, some of which inject package objects.
  - Maybe loading a package in the interpreter produces a package object, which is then added to the interpreter.
  - Separating init from call also enables the reverse direction: compiled code calling into the interpreter.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path strategy
- Consider a more coherent strategy for package resolution paths:
  - **BNI path**: searched for `.bni` interface files (like PATH, maybe `:` separated).
  - **BN source path**: searched for `.bn` package implementations.
  - **BN object/library path**: searched for `.a` or `.o` compiled package artifacts.

### CLI flag coherence
- Review and unify command-line flags across `bnc`, `bni`, `bnas`, `bnlint` for consistency (e.g., `-root` vs `--root`, `-v` vs `--verbose`).

### Annotations and C function interop
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.

### Simplify bootstrap.Read/Write signatures
- The `len` parameter in `bootstrap.Read(fd, buf, len)` and `bootstrap.Write(fd, buf, len)` is redundant — the slice already carries its length. If you want a smaller length, subslice.

---

## Done (session 2026-04-08/09)

### NeedsDestruction TYP_NAMED resolution (compiler bug)
- `NeedsDestruction` did not resolve `TYP_NAMED` (only `TYP_ALIAS`), so named struct types with managed fields were not detected as needing cleanup. Similarly, `emitStructElemRefcount` bailed out when element type was `TYP_NAMED` instead of `TYP_STRUCT`.
- Fixed both. Conformance test 140 added.

### Managed-slice dtor: iterate from backing start, not data ptr
- `genManagedSliceDtor` iterated elements from the `data` pointer (field 0), but after subslicing, `data` points into the middle of the backing. The dtor must iterate from `refptr` (field 2 = backing allocation start) over `backingLen` elements.
- This was the root cause of the boot-comp-comp crash: `ctx.Vars[:savedLen]` created subslices, and the dtor walked stale memory past the subslice boundary.
- boot-comp-comp now works (hello world compiles and runs).

### Phase 3.1: Lower slice ops to primitive IR ops — DONE
- All slice ops (`OP_SLICE_GET/SET/LEN/EXPR/ELEM_PTR`) lowered to primitives (`OP_EXTRACT`, `OP_GET_ELEM_PTR`, `OP_LOAD/STORE`) in the IR gen layer. Deprecated opcodes removed from `ir.bni`.
- 13 C runtime functions removed (22→9 in manifest). `emit_slice.bn` deleted.
- Raw slice subslice copy bug fixed: `s[lo:hi]` now zero-copy (was incorrectly copying in C runtime).
- **EmitSliceSet element type bug**: was using `val.Typ` (int/64-bit) instead of slice element type, causing wrong GEP stride for `[]uint8`. Test 141 added.
- **EmitSliceExpr GEP type mismatch**: codegen's internal bitcast produced typed pointer but slice field 0 expects `i8*`. Fixed with byte-level GEP.
- **readFile UAF** (6 call sites in cmd/bnc, cmd/bni, pkg/loader): `var src []uint8 = readFile(...)` dropped backing reference immediately. Changed to `@[]uint8`. Previously masked by copying slice_expr. Tests 142 added.

### Remove dead bn_append_* functions
- No IR opcode, no codegen emission, no callers. Removed from C runtime and manifest.

### ModuleConst.Name UAF
- `ModuleConst.Name` was `[]char` (raw) but assigned from `buf.CopyStr()` (`@[]char`). The managed temporary was freed at end of statement. Changed to `@[]char`. This caused iota values all reading 0 and cascading boot-comp-comp failures.

### 161/161 — ZERO XFAILS IN ALL MODES
- **boot-comp: 161/161. boot-comp-int: 161/161. boot-comp-comp: 161/161.**
- Was 158/158 before Phase 3 work. New tests: 140 (named struct slice elem rc), 141 (slice param mutation + multi-return managed field), 142 (read slice mutation).

### [N]@T field-write-through-index — FIXED (test 139)
- `genSelectorPtr` for `arr[i].Field` only handled struct elements. For `[N]@Node`, element type is `@Node` (TYP_MANAGED_PTR). Added: load managed-ptr from array element, then GEP for field.

### Duplicate function detection — FIXED (test 206)
- Added `checkDuplicateDecls`: O(n²) scan of declaration list for duplicate names. Reports "redeclared in this block". Skips .bni→.bn matches (only checks within same file).
- Added `LookupLocal` to Scope (current scope only, not parents).

### Compiler refcount fixes
- **Managed-slice return leak** (test 131): skip RefInc for returned managed-slice locals via `lookupLocalVar`.
- **Managed-ptr return leak** (test 132): same pattern. Key bug: `lookupVar()` fell back to globals — returning a singleton freed it. Fixed with `lookupLocalVar()`.
- **Element-copy refcounting** (tests 133-135): RefInc/RefDec for managed-ptr, managed-slice, and struct elements during slice/array assignment.
- **RefInc-before-RefDec ordering** (test 138): cascade-safe assignment (e.g., popScope).
- **Parser raw-slice borrow** (test 136): `parseImportDecl` `[]@ast.ImportSpec` → `@[]@ast.ImportSpec`.
- **Debugging**: sentinel-based RefDec (rc=-999) and ASan with instrumented .ll files.

### Interpreter flat migration — COMPLETE
- ALL data types use flat storage: int, bool, []T, @[]T, @T, *T, [N]T, struct, string, named types. Only function values remain Cell-based (pending interop design).
- readFlatValue no longer materializes Elems — O(1) variable read.
- evalMakeSlice, evalArrayLit, evalStructLit, ZeroValue, stringToCharSlice all produce flat Values directly.
- Legacy code removed: MakeSliceVal, MakeArrayVal, MakeManagedSliceVal, writeFlatValue Elems paths, HeapObj deref fallbacks, legacy index/subslice/for-in/struct-field paths. Elems: 53→3. HeapObj: 30→3.

### Interpreter refcount fixes
- **Return leak**: IsFresh flag on Value. make/make_slice/box set IsFresh (rc starts at 1, skip envDefine RefInc). execReturn sets IsFresh for local-ident returns via envGetLocalAddr (not parents/globals). envDefine/envSet skip RefInc when IsFresh.
- **Element-copy**: RefInc/RefDec for managed-ptr, managed-slice, and struct elements in both flat slice and flat array assignment paths.
- **Struct field assignment**: RefInc/RefDec for managed-ptr and managed-slice fields in both auto-deref and value-struct paths.
- **Managed-slice element cleanup**: only iterates elements when backing refcount==1 (last reference). Handles managed-ptr, managed-slice, and struct elements.
- **Assignment cascade**: RefInc new before RefDec old for managed-ptrs (cascade-safe).
- **Pointer deref write**: RefInc/RefDec for managed types in `*p = val`.

### Managed-slice flat storage in interpreter
- boot-comp-int: 148/156 (was 142 before).
- `TYP_MANAGED_SLICE` in `useFlatType`, flat subslicing, `@[]T→[]T` coercion, element refcounting, backing refcounting.

### 4-word managed-slice migration — finalized
- Conformance test 129 (subslice preserving backing_len), bootstrap interpreter confirmed no changes needed.

### x86-64 assembler backend — IMPLEMENTED
- **pkg/asm/x64**: full x86-64 instruction encoding with REX prefix, ModR/M, SIB byte. MOV, PUSH/POP, LEA, ADD/SUB/AND/OR/XOR/CMP/TEST, INC/DEC/NEG/NOT, SHL/SHR/SAR, IMUL (2 and 3 operand)/IDIV/DIV, CQO/CDQ, JMP/Jcc/CALL/RET, NOP/SYSCALL/INT. 40 unit tests.
- **x86-64 text parser**: register parsing (4 sizes × 16 regs), memory operands with `[base + index*scale + disp]`, size prefixes, Jcc mnemonic parsing. Full parity with encoding backend. 28 parser tests.
- **ELF relocation mapping**: FIX_REL32 → R_X86_64_PC32, FIX_ABS64 → R_X86_64_64.
- 295 tests total across all assembler packages.

### AArch64 parser: MVN added, full parity
- Added MVN (bitwise NOT) to encoding backend and parser. MVN Rd, Op2 = ORN Rd, XZR, Op2. AArch64 parser now has full parity with encoding backend. 3 encoding tests + 1 parser test.

### ARM32 semihosting end-to-end tests — IMPLEMENTED
- 3 tests: exit code, loop (sum 1..9=45), function call (PUSH/POP with BL)
- Uses `qemu-system-arm -semihosting` with SYS_EXIT_EXTENDED (0x20) for exit code passthrough
- Linked with `arm-none-eabi-ld` as bare-metal at 0x40000000 (virt machine)
- Fixed ELF symbol table ordering (locals before globals, required by GNU ld)

### ARM32 assembler backend — IMPLEMENTED
- **pkg/asm/arm32**: full ARMv7-A instruction encoding (data processing, load/store, load/store multiple, branches, multiply, system). Rotated 8-bit immediate encoder. All instructions accept condition codes. 73 unit tests.
- **ELF32 support**: generalized `pkg/asm/elf` writer to emit ELF32 (for ARM32) or ELF64 (for AArch64/x86-64). Proper structure sizes, field ordering, r_info encoding for each class. Extracted `elf_util.bn` for code hygiene. 16 tests.
- **ARM32 text parser**: register parsing (r0-r15 + named), all operand types including register lists with range syntax (`{r0-r7, lr}`). Condition suffix + S flag stripping from mnemonics (`bne`→B+NE, `addseq`→ADD+S+EQ). Full instruction dispatch. Added `TOK_LBRACE`/`TOK_RBRACE` to lexer. 32 new parser tests (65 total).
- **Parser hookup**: `.arch arm32` directive, dispatch to ARM32 instruction parser.
- **CLI**: `cmd/bnas` already works for ARM32 via the parser — no changes needed.
- 220 tests total across all assembler packages.

### 4-word managed-slice migration — finalized
- **Conformance test 129**: subslice preserving backing_len. Creates `@[]int` of 5 elements, subslices to `s[1:3]` (len=2), verifies backing_len stays 5. Also tests double-subslice.
- **Bootstrap interpreter**: confirmed no changes needed.
- **Status**: all plan steps complete.

### Managed-slice flat storage in self-hosted interpreter
- **boot-comp-int: 146/147 conformance tests pass** (was 142 before)
- Added `TYP_MANAGED_SLICE` to `useFlatType` — managed-slice variables now use 32-byte flat headers with real `rt.MakeManagedSlice` backing
- `writeFlatValue`: added flat-to-flat copy path (memcpy 32-byte header)
- `@[]T → []T` coercion: flat managed-slice creates flat raw slice sharing same data pointer
- Flat managed-slice subslicing: creates new 4-word header sharing backing, preserves backing_len, RefIncs backing
- Element refcounting: flat index assignment RefInc/RefDec managed-ptr elements; `cleanupFlatMSliceElems` on reassignment
- Managed-slice backing refcounting deferred (leaks backing allocations, no correctness issues)
- Removed xfails: 126 (boot-comp-int, boot-comp-comp-int), 129 (boot-comp-int, boot-comp-comp-int)

## Done (session 2026-04-07)

### Interpreter flat memory: fix 4 struct regressions + 2 new bugs
- **Managed-slice RawAddr confusion**: `readFlatValue` for `TYP_MANAGED_SLICE` set `RawAddr` to backing refptr, but `evalLen` treated it as the slice header address. Fixed: `RawAddr` = header address, `evalLen` uses `MSliceLenOffset` for managed-slices. Tests: 109, unit `TestFlatStructManagedSliceField`.
- **Self-referential type resolution**: `execTypeDecl` replaced pre-registered struct types with new objects, breaking `Node { next @Node }` where the field still pointed to the old empty placeholder. Fixed: update placeholder's Fields in-place. Tests: 058, unit `TestSelfReferentialType`.
- **Return value managed-slice cleanup**: `cleanupEnvExcept` called `interpCleanupSlice` on return values, freeing elements. Fixed: skip cleanup for managed-slices in the return-values exception list. Tests: 107, unit `TestReturnManagedSlicePreservesElements`.
- **Lazy struct reads**: `readFlatValue` for `TYP_STRUCT` eagerly materialized ALL fields (including string/slice data), causing O(n) allocation per struct access. Fixed: return lazy Value with RawAddr only. `evalSelector` reads specific fields on demand. This fixed the `parser.ParseFile` infinite hang in boot-comp-int.
- **TYP_NAMED resolution**: `readFlatValue`/`writeFlatValue` didn't resolve named types (`type Kind int`), falling through to memset. Fixed: `resolveUnderlying` resolves both aliases and named types. Tests: 127, unit `TestFlatStructNamedTypeField`.
- **Lazy struct copy**: `copyValue` and `writeFlatValue` handle lazy structs via memcpy. Tests: 128, unit `TestFlatStructCopyOutAndBack`.

### Unit test backfill for flat memory model
- 15 new unit tests in `pkg/interp/call_test.bn` (total: 151)
- Covers: managed-slice fields, managed-ptr fields, string→@[]char, nil managed-slice, self-referential types, return value survival, len/index through flat struct fields, nested structs, named types, lazy struct copy

### Conformance tests added
- 127: named type struct fields (TYP_NAMED in flat memory)
- 128: struct field copy (lazy struct copy/write paths)

### boot-comp-int progress
- 142/144 conformance tests pass (was 138 at start of session)
- Fixed 4 xfails: 058, 102, 107, 109 (flat struct regressions)
- pkg/interp unit test xfail updated: no longer hangs (was "RegisterBootstrapPackage hang"), now xfail'd for inner interpreter return value wrapping

## Done (session 2026-04-03/04/05)

### Destructors — struct, managed-slice, array, anonymous struct
- `rt.RefDec(ptr *uint8, dtor *uint8)` — dtor called before Free when rc hits 0
- `types.NeedsDestruction(t)` — recursive query for types requiring cleanup
- `OP_FUNC_ADDR` — new IR opcode for function address as `i8*`
- Struct dtors, managed-slice dtors (with element cleanup loops), array dtors, anonymous struct dtors
- All use `linkonce_odr` for linker dedup. Cross-package references via `qualifiedDtorNameForType`.
- Conformance tests: 113-116.

### Anonymous struct support
- Both type checkers: `Identical()` with structural equivalence (field names + types in order)
- IR gen: `resolveTypeExpr` handles TEXPR_STRUCT, synthetic names, deduplication
- Conformance tests: 113, 119-121.

### `*any` → `*uint8` migration in pkg/rt

### Array codegen fixes
- `arr[i].Field` for managed-ptr elements, `cont.Items[i] = v` selector-base, element refcounting
- Conformance tests: 117, 118.

### Temporary lifetime fix
- Removed all leaking `consumeTemp` for `@[]T→[]T`. Temps RefDec'd at end of statement.
- Migrated bnc to `@[]@[]char`. `bootstrap.Exec` now takes `[]@[]char`.
- Conformance test: 122.

### .bni processing: RegisterSelfTypes expanded
- Now handles struct types, type aliases, and constants from the package's own .bni file.

### Negative conformance tests (19 total)
- 112 (slice nil), 200-210 (type mismatch, undeclared, wrong args, nil, return type, duplicate decl, operators, conditions, field access, indexing), 214-220 (comparisons, unary, call non-func, managed ptr arith, slice nil assign, multi-return, undefined type)
- `.error` files use `grep -E` regex matching for cross-checker compatibility

### Test infrastructure
- 6-mode unit test runner: boot, boot-int, boot-comp, boot-comp-int, boot-comp-comp, boot-comp-comp-comp
- Mode sets: basic (3), all (5), full (6). `bnc --test` just compiles (runner executes).
- Summary lines show mode. Bug discovery protocol in CLAUDE.md. Never-leak rule. Coding guide reference.

## Done (previous sessions)

### @[]T refcounting, OP_MAKE_SLICE migration, C runtime cleanup — `80b5150`
### Self-hosted interpreter HeapObj tracking — `c997b9f`
### Package search paths and implicit pkg/rt import — `ad394ee`
### @[]T layout, MakeManagedSlice, @[]T → []T conversion — `da07f70`
### bit_cast, pointer indexing, pkg/rt — `c80d962`
### Codegen bugs (074-087) — ALL FIXED
### Self-compiled compiler — FULLY PASSING ✓
### Remove append — DONE
### Remove null termination — DONE
### 4-word managed-slice layout — DONE
### Unit test backfill (two passes) — DONE
