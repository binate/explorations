# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## TODO

### Discuss ways to split long string literals across lines
- No way to break a long string literal across source lines: Binate has no `+` operator for strings, no adjacent-string-literal concatenation (as in C), and `bootstrap.Concat` allocates at runtime (fine for one-shot, bad for hot paths / error messages that may never fire).
- Came up during the raw-slice migration: an `errMsg` call in `pkg/parser/parser.bn:106` has a 114-char string literal that can't be shortened without losing information. Tagged `// LONG-LINE ALLOWED` as a workaround — see `scripts/hygiene/line-length.sh` and `explorations/code-hygiene-check.md`.
- Options to discuss: C-style adjacent-string concatenation at the lexer level; a `\` line-continuation inside string literals; a compile-time const-fold of `bootstrap.Concat` on literal args; something else.

### boot-comp-int2-int2 mode segfaults (bni2 can't self-host)
- The `boot-comp-int2-int2` runner (added to unit/conformance/perf as a replacement for the too-slow `boot-comp-int-int`) crashes when the outer compiled bni2 is asked to interpret `cmd/bni2` source: exit 139 (SIGSEGV), with no output.
- Single-layer `boot-comp-int2` (compiled bni2 runs test.bn directly) works fine — the issue is specifically that bni2 cannot interpret its own source.
- Not in the `all` modeset, so CI/default runs don't exercise it. Left wired up so it can be run on demand once the self-hosting gap is closed.
- **Next**: pick a small probe (e.g. a single-feature .bn that exercises whatever bni2 source uses) and narrow which feature of bni2 the outer VM mishandles. Likely related to the same class of bugs as the int2 field-layout issue below.

### Lift function-name qualification into IR (shared across backends)
- The VM and the compiler both need to avoid cross-package function-name collisions. They currently solve it separately: `pkg/mangle.FuncName(pkgName, name)` produces C-style `bn_asm__New` for LLVM symbols, and `pkg/mangle.QualifyName(pkgShort, name)` produces dot-form `asm.New` for the VM's function table. Both backends extract the short package name from `ir.Module.Name` and apply their own qualification at lower/emit time.
- That duplication is fine but a cleaner alternative is to qualify in IR itself: have `pkg/ir` store all function names fully qualified ("asm.New", "bootstrap.Args") as canonical. `mangle.FuncName` already treats dotted names as pre-qualified, so the compiler would keep producing the same `bn_asm__New`. The VM would use qualified names directly. One source of truth.
- Not urgent — the current per-backend qualification works and the shared helpers in `pkg/mangle` de-duplicate the core logic. Worth revisiting if backend drift keeps biting (e.g., when adding the 32-bit ARM backend).
- Scope: touches `ir.GeneratePackage` (which currently emits unqualified names for intra-package functions), `moduleFuncs` lookup sites, `EmitCall`/`EmitFuncAddr` call sites, and all callers that pass a simple name to IR. Backends would shed their `modulePkgName` state.

### boot-comp-int2: 15 unit-test packages still fail (down from 17)
- 15 packages still xfail'd under boot-comp-int2 (cmd/bni2 bytecode VM): pkg-lint, pkg-types, pkg-asm-{x64,parse,arm32,aarch64,elf,macho}, pkg-ir, pkg-lexer, pkg-interp, pkg-parser, pkg-codegen, pkg-vm, cmd-bnlint. All xfail'd in `scripts/unittest/<pkg>.xfail.boot-comp-int2`.
- **Progress**: pkg-asm and cmd-bnc unblocked by the VM function-name qualification fix (see commits `32eb2f6` / `76294d8`). The root cause of those was `LookupFunc`'s suffix-match fallback: a user's call to `asm.New` (qualified extern ref) resolved to the *first* function named `New` in `vm.Funcs` — usually `buf.New`, `lexer.New`, `parser.New`, or `interp.New` depending on lowering order. Same collision hit intra-package calls with unqualified names. Fix: qualify by package at VM lowering time (dot-form `asm.New`) and make `LookupFunc` strict exact match. Helpers live in `pkg/mangle`.
- **Regression guard**: 270 conformance test covers the cross-package struct field resolution fix from `2be80b9`; the name-collision fix is exercised by pkg-asm's own tests (which now pass) — consider an explicit multi-pkg conformance test if the TODO above lifts qualification into IR.
- pkg/asm/macho xfail is a different bug: `vm: extern not found: bootstrap.Exec` — the bytecode VM doesn't implement that extern.
- **Next**: the 14 remaining silent-SIGSEGV xfails (non-macho) are likely a distinct class of bugs. Pick the smallest surface area (probably pkg/lexer — a Token-return + managed-ptr-arg pattern crashes early; see mid-session investigation notes) and trace.

### Interpreter: @T parameter stored in struct field over-increments refcount
- Conformance tests 228/229 show rc increasing by 2 per call instead of 1 on boot-comp-int. The compiler handles this correctly (tests pass on boot-comp).
- The spec (`refcount-lifecycle.md` section 3) says: callee RefInc's @T param on entry, RefDec's at scope exit. Field assignment RefInc's separately. The compiler does exactly this (callee-side RefInc in `gen_stmt.bn:104`). The interpreter's `envDefine` RefInc's (equivalent), but something causes a double-increment — possibly the interpreter's field assignment path RefInc's redundantly, or `cleanupEnvExcept` fails to RefDec the param.
- Tests 228/229 xfail'd on boot-comp-int.
- May be related to the boot-comp-int unit test crashes/hangs on larger packages.

### boot-comp-int crash: TYP_SLICE vs TYP_MANAGED_SLICE type mismatch
- Compiled interpreter segfaults in `--test` mode on large packages (`pkg/ir`, `pkg/codegen`, `pkg/lint`)
- Valgrind: 16-byte allocation (raw slice) read/written as 32 bytes (managed slice) — heap buffer overflow
- Root cause narrowed: a `@Type` object's `Kind` field is mutated from `TYP_MANAGED_SLICE` (10) to `TYP_SLICE` (9), likely use-after-free of the type object
- Only happens in `--test` mode with large packages; same code works in non-test mode
- **Detailed writeup**: `explorations/bug-boot-comp-int-type-mismatch.md`
- **Next**: add line-level DWARF debug info, or add targeted debug prints to find the exact struct.field where types diverge

### ~~Compiler bug: missing RefInc on struct copies with managed fields~~ — FIXED
- **Root cause**: two related issues:
  1. When a struct containing `@[]T` or `@T` fields is copied by value, the compiler did not RefInc the managed fields in the copy.
  2. Stack-allocated struct locals with managed fields were not cleaned up at scope exit (no dtor call).
- **Compiler fix**: Generate `__copy_X` functions (symmetric to `__dtor_X`) for structs and `[N]T` arrays. Call copy at struct copy sites (var decl, var assign, field assign, deref assign, function args, function return). Call dtor at scope exit for struct locals.
- **Interpreter fix**: `structRefInc`/`structRefDec` helpers walk struct fields recursively. Called from `cleanupEnvExcept` (scope exit), `envDefine` (var decl), `envSet` (var assign). Also fixed: `cleanupEnvExcept` false `isRet` match for `@T` (offset-0 field address collision); `IsFresh` leak on fresh `@T` function args.
- **`VAL_MANAGED_SLICE`**: added to distinguish `@[]T` from `*[]T` at Value.Kind level (was both `VAL_SLICE`), matching `VAL_MANAGED_PTR` vs `VAL_POINTER`.
- **Conformance tests**: 222 (struct copy managed), 223 (nested struct copy), 224 (struct field assign), 225 (managed ptr scope cleanup).
- **Detailed writeup**: `explorations/bug-struct-copy-refcount.md`
- **Plans**: `explorations/plan-copy-constructors.md`, `explorations/plan-interp-struct-copy-refcount.md`
- **Principled slow path** (2026-04-11): always copy on return, always dtor at scope exit, register struct call results as temps. Tests 226 and 227 now pass on compiled modes. See `design-refcount-axioms.md`.
- **[]char UAF migration** (2026-04-12): the slow path exposes latent UAFs where `*[]char` (or `*[]T`) borrows from `@[]char` (or `@[]T`) that gets freed by struct dtors. Systematic migration of function return types and callers. Key fixes: `EmitModule`, `llvmType`, `pathJoin`, `FuncRetType` fields, `parser.Errors`/`CheckerErrors` callers, `sliceToChars`/`StrOf` callers, `concatChars`, `quotePath`, test helpers. Also fixed: slice element assignment for nested struct fields (was only handling top-level `@T`/`@[]T`), multi-return assignment for struct variables (missing save-copy-destroy).
- **Status**: 187/187 conformance on boot-comp, boot-comp-comp, boot-comp-comp-comp. **26/26 boot-comp unit tests pass.** Zero failures.
- **`--cflag` option** added to bnc for passing flags to clang (e.g., `--cflag -fsanitize=address`). Used with libgmalloc to debug UAFs.

### ~~Linux/x86-64: boot-comp-comp string corruption~~ — FIXED
- **Root cause**: use-after-free in `cmd/bnc/test.bn`. `runtimePath` was declared as `*[]char` (raw slice) instead of `@[]char` (managed). When the `candidate @[]char` from `bootstrap.Concat(root, "/runtime/binate_runtime.c")` went out of scope, it was RefDec'd and freed — but `runtimePath` still borrowed its data, creating a dangling pointer. The garbage filenames were freed memory being read as strings.
- **Fix**: changed `var runtimePath *[]char` to `var runtimePath @[]char = buf.CopyStr(cli.RuntimePath)` in test.bn, matching the pattern already used in main.bn.
- **CI now runs all modes** including boot-comp-comp and boot-comp-comp-comp.

### Self-hosted interpreter: boot-comp-int-int still failing
- boot-comp-int-int (compiled bni interprets bni which interprets test) still has issues.
- **Known issue**: inner interpreter function return values get wrapped in an extra `@Value` indirection through flat memory. When the outer compiled interpreter interprets the inner interpreter's `callFunc`, the return value goes through flat serialization/deserialization which adds a managed pointer layer. `println(add(3, 4))` prints "nil" instead of "7".
- Removed from mode sets. Needs dual-mode interop (flat memory roundtripping of `@Value` tagged unions) to work properly.

### Self-hosted interpreter memory model parity with compiler
- Plan: `explorations/plan-interp-memory-parity.md`
- The self-hosted interpreter can no longer run under the bootstrap (boot-int dropped) since it now uses `bit_cast`, pointer indexing, and `pkg/rt`. It requires compiled mode (boot-comp-int and above).
- **boot-comp-int: 183/183 conformance tests pass** (was 129 before this work began)
- Phase 1 (done): infrastructure — `flat.bn` with readFlatValue/writeFlatValue using bit_cast and pkg/rt
- Phase 2 (done): scalar variables in flat memory — envDefine/envGet/envSet use flat addresses for ints
- Phase 3 (done): structs in flat memory — `evalMake` allocates via `rt.Alloc`, all field access through `RawAddr + FieldOffset`. Lazy struct reads (no eager field materialization). Self-referential type resolution (in-place field update).
- Phase 4 (done): raw slices in flat memory — `*[]T` as `{data, len}` in 16 bytes. `arr[:]` creates flat slices.
- Managed pointer refcounting (done): `envDefine` RefInc, `envSet` RefDec/RefInc, `cleanupEnvExcept` for scope exit, `interpRefDec` for recursive struct field cleanup, `interpCleanupSlice` for managed-slice element cleanup. Return values excluded from scope cleanup.
- bit_cast (done): pointer↔int, pointer↔pointer — 090 passes
- Pointer indexing (done): `p[i]` read/write, `&arr[i]` — 091 passes
- pkg/rt forwarding (done): c_malloc, Alloc, Free, RefInc, RefDec, Refcount, MakeManagedSlice — 092, 093, 104, 123 pass
- Pointer comparison (done): `p == q` via RawAddr
- String→*[]char for flat slices (done): 079, 088 pass
- C ABI sret fix (done): large struct returns from C externs on ARM64
- TYP_NAMED resolution (done): `resolveUnderlying` resolves named types (`type Kind int`) in flat read/write paths
- Lazy struct optimization (done): `readFlatValue` for TYP_STRUCT returns RawAddr-only Values, avoiding O(n) allocation per field access. Fixed parser.ParseFile hang in boot-comp-int.
- Managed-slice flat storage (done): `TYP_MANAGED_SLICE` in `useFlatType`. 32-byte flat headers with `rt.MakeManagedSlice` backing. Flat-to-flat copy, subslicing, `@[]T→*[]T` coercion, element refcounting. Fixed tests 126, 129.
- Managed-slice backing refcounting (done): envDefine/envSet RefInc/RefDec for backing_refptr. cleanupEnvExcept RefDec on scope exit. Element-level RefInc/RefDec for managed-ptr elements in flat index assignment.
- Full flat migration (done): ALL data types use flat storage (int, bool, *[]T, @[]T, @T, *T, [N]T, struct, string, named types). Only function values remain Cell-based.
- readFlatValue no longer materializes Elems (O(n) → O(1)). All consumers (for-in, index, len, print, subslice) use flat paths.
- Legacy Elems code removed: MakeSliceVal, MakeArrayVal, MakeManagedSliceVal removed. writeFlatValue Elems→flat conversion removed. Elems refs: 53→3 (VAL_MULTI only). HeapObj refs: 30→3 (function values only).
- All refcounting fixed: return leak (IsFresh flag), element-copy, struct field, assignment cascade, pointer deref write, managed-slice element cleanup (rc==1 check).
- **187/187 in boot-comp and boot-comp-comp. 183/183 in boot-comp-int (4 xfail'd). 26/26 boot-comp unit tests.**

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

### Self-hosted interpreter refcounting — MOSTLY FIXED, needs axiom audit
- boot-comp-int: 177/177 conformance. boot-comp-int unit tests: pre-existing failures in pkg/ir, pkg/codegen, pkg/lint, pkg/asm/* (9 packages). These are likely from the interpreter not following refcounting axioms consistently.
- **Known issue**: tests 228/229 (@T param stored in struct field over-increments) — see above.
- **Needs**: systematic audit of interpreter refcounting against `design-refcount-axioms.md`. The compiler now follows axioms 1-5; the interpreter needs the same treatment.
- Previous fixes: IsFresh flag, structRefInc/structRefDec, cleanupEnvExcept false-match, IsFresh on args, VAL_MANAGED_SLICE.


### ~~Verify .bni vs .bn visibility semantics~~ — VERIFIED
- Private functions (235) and types (236) in `.bn` but not `.bni` are correctly rejected by both type checkers.
- Public declarations work across packages (237). `.bni` and `.bn` definitions coexist without duplicate errors.
- Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### ~~Raw slice subslice expression copies data (bug)~~ — FIXED
- Fixed by lowering `OP_SLICE_EXPR` to primitive IR ops (step 3.1). Raw slice `s[lo:hi]` now produces a zero-copy view `{data + lo * elemSize, hi - lo}` via GEP. The C runtime `bn_slice_expr_*` functions (which incorrectly copied) have been removed.

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### Termination analysis — labeled break and `panic` design
- Initial missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.
- **`panic` as terminator**: Binate has a non-recoverable panic. Go treats `panic(...)` calls as terminating statements. For now, we accept the awkwardness of requiring a dummy `return` after an unconditional panic — the analysis doesn't know panic terminates. Consider marking the builtin as terminating in a later revision.

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

### ~~.bni/.bn return type mismatch should be a compile error~~ — FIXED
- The type checker now verifies that `.bn` function definitions match their `.bni` declarations (parameter count/types, return count/types). Mismatches are reported as compile errors.
- Immediately caught two real bugs: `MakeStringVal` and `AddBlock` had `@[]char` in `.bni` but `*[]char` in `.bn`. Both `.bni` files fixed.
- Conformance test 221 now passes on all compiled modes.

### ~~Compiler bug: cast to sub-word pointer type emits invalid LLVM IR~~ — FIXED
- Cast codegen now uses `bitcast` (ptr→ptr), `ptrtoint` (ptr→int), `inttoptr` (int→ptr) instead of `add` for pointer types.
- Conformance test 161 passes on all compiled modes.

### x86-64 assembler: end-to-end tests on Linux CI
- Assemble x86-64 → ELF64 → link → run natively (no QEMU needed)
- CI runs Linux x86-64 so this would be a native end-to-end test
- Test cases: exit via SYSCALL, loop, function call with PUSH/POP

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- Type coverage is basically just `i64`. Only one `DIBasicType` emitted (`emit_debug.bn:220`), reused for every variable. No `DIBasicType` for bool/uint8/uint16/uint32/char; no `DICompositeType` for struct/array/slice; no `DIDerivedType` for pointers/typedefs. All locals show as `i64` in the debugger.
- Parameters don't get `DILocalVariable` (stack slots exist but no dbg.declare for params).
- `DISubprogram` has `line: 0` and `scopeLine: 0` (function definition line never captured).
- `DISubroutineType` is a single shared generic; no per-function signature or parameter types.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. Emit `DIBasicType` for each scalar kind (bool, char, u8/16/32, i32, etc.) and reference from variable declares — unlocks correct type display in debuggers.
2. Capture function definition lines into `DISubprogram` (thread from AST `Func`/`FuncDecl` node).
3. Emit `DILocalVariable` for parameters.
4. Emit `DICompositeType` for structs (field names + types), `DIDerivedType` for pointers. Wire into `emit_types.bn`'s struct collection.
5. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
6. Per-function `DISubroutineType` with real parameter + return types.

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

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
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

### ~~Simplify bootstrap.Read/Write signatures~~ — DONE
- `Read(fd int, buf *[]uint8) int` and `Write(fd int, buf *[]uint8) int` — redundant `len` parameter removed. Callers subslice if they want a smaller length.

### ~~Raw slice syntax migration: `[]T` → `*[]T`~~ — DONE (2026-04-17)
- Raw slices now spelled `*[]T` (the `*`/`@` prefix consistently means raw/managed for both pointers and slices). Disambiguation rule: `*[` and `@[` before `]` are always slice sugar; pointer-to-array and pointer-to-slice require parens.
- Stages landed in order: Stage 0 (reclaim `*[`), Stage 1 (accept `*[]T` alongside `[]T`), Stage 2 (migrate all code + docs), Stage 3 (remove `[]T` entirely — `bare "[" "]"` is now a parse error in both the Go bootstrap and `pkg/parser`). Covered by conformance test 276.

---

## Done (session 2026-04-08/09)

### ~~NeedsDestruction TYP_NAMED resolution~~ — FIXED
- Fixed: `NeedsDestruction` resolves `TYP_NAMED`. Conformance test 140 passes.

### ~~Managed-slice dtor: iterate from backing start, not data ptr~~ — FIXED

### Phase 3.1: Lower slice ops to primitive IR ops — DONE
- All slice ops (`OP_SLICE_GET/SET/LEN/EXPR/ELEM_PTR`) lowered to primitives (`OP_EXTRACT`, `OP_GET_ELEM_PTR`, `OP_LOAD/STORE`) in the IR gen layer. Deprecated opcodes removed from `ir.bni`.
- 13 C runtime functions removed (22→9 in manifest). `emit_slice.bn` deleted.
- Raw slice subslice copy bug fixed: `s[lo:hi]` now zero-copy (was incorrectly copying in C runtime).
- **EmitSliceSet element type bug**: was using `val.Typ` (int/64-bit) instead of slice element type, causing wrong GEP stride for `*[]uint8`. Test 141 added.
- **EmitSliceExpr GEP type mismatch**: codegen's internal bitcast produced typed pointer but slice field 0 expects `i8*`. Fixed with byte-level GEP.
- **readFile UAF** (6 call sites in cmd/bnc, cmd/bni, pkg/loader): `var src *[]uint8 = readFile(...)` dropped backing reference immediately. Changed to `@[]uint8`. Previously masked by copying slice_expr. Tests 142 added.

### ~~Remove dead bn_append_* functions~~ — DONE

### ~~ModuleConst.Name UAF~~ — FIXED
- Fixed: `ModuleConst.Name` changed from `*[]char` to `@[]char`.

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
- **Parser raw-slice borrow** (test 136): `parseImportDecl` `*[]@ast.ImportSpec` → `@[]@ast.ImportSpec`.
- **Debugging**: sentinel-based RefDec (rc=-999) and ASan with instrumented .ll files.

### Interpreter flat migration — COMPLETE
- ALL data types use flat storage: int, bool, *[]T, @[]T, @T, *T, [N]T, struct, string, named types. Only function values remain Cell-based (pending interop design).
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
- `TYP_MANAGED_SLICE` in `useFlatType`, flat subslicing, `@[]T→*[]T` coercion, element refcounting, backing refcounting.

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
- `@[]T → *[]T` coercion: flat managed-slice creates flat raw slice sharing same data pointer
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
- Removed all leaking `consumeTemp` for `@[]T→*[]T`. Temps RefDec'd at end of statement.
- Migrated bnc to `@[]@[]char`. `bootstrap.Exec` now takes `*[]@[]char`.
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
### @[]T layout, MakeManagedSlice, @[]T → *[]T conversion — `da07f70`
### bit_cast, pointer indexing, pkg/rt — `c80d962`
### Codegen bugs (074-087) — ALL FIXED
### Self-compiled compiler — FULLY PASSING ✓
### Remove append — DONE
### Remove null termination — DONE
### 4-word managed-slice layout — DONE
### Unit test backfill (two passes) — DONE
