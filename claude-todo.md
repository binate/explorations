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
- **boot-comp-int: 146/147 conformance tests pass** (was 129 before this work began)
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
- **Remaining xfails (1)**: 206 (duplicate function detection — type checker gap, not memory model)
- **Unit tests**: 151 in pkg/interp (boot-comp). pkg/interp xfail'd in boot-comp-int due to inner interpreter return value wrapping (pre-existing limitation, not a regression).
- **Next**: managed-slice backing refcounting (RefInc/RefDec on backing_refptr during env operations), then managed-slice element cleanup on scope exit

### Binate type checker: duplicate function detection
- The Binate type checker (pkg/types) does not detect duplicate function declarations within the same package
- The bootstrap Go type checker does ("foo redeclared in this block")
- Conformance test 206 is xfail'd for boot-int and boot-comp

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

### Raw slice subslice expression copies data (bug)
- `bn_slice_expr_i8/i64/struct` in `binate_runtime.c` allocate a new buffer and copy. This is wrong — raw slice `s[lo:hi]` should produce a zero-copy view `{s.data + lo * elemSize, hi - lo}`, since raw slices are borrowed views.
- The copy breaks borrowing semantics: mutations to the subslice don't affect the original.
- `@[]T` subslice is already correct (codegen adjusts data/len, preserves backing refptr).
- **Fix**: change C runtime to return `{s.data + lo * elemSize, hi - lo}` without allocating.
- **Conformance tests needed**: test that mutating a subslice affects the original (e.g., `s := arr[:]; sub := s[1:3]; sub[0] = 99; assert s[1] == 99`). Test for both `[]T` and `@[]T`.
- See `explorations/slice-operations-analysis.md`.

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

### Compiler bug: multi-return with struct containing managed fields generates bad LLVM IR
- `func f() (StructWithManagedField, int)` generates `ret i64 %v7` type mismatch in LLVM IR
- Reproducer: `strTabAdd(st StrTab, s []char) (StrTab, int)` where `StrTab` contains a `buf.CharBuf` (which has `@[]char`)
- **Workaround applied**: changed to `strTabAdd(st *StrTab, s []char) int` (pointer param instead of struct return) in `pkg/asm/macho/macho.bn`
- **TODO**: fix the compiler codegen to handle this correctly, then revert the workaround
- May also affect `pkg/asm/parse` functions that return `(Lexer, Token)` and `(Lexer, Token, ExprResult)` — these structs don't contain managed fields, so they may be fine, but worth checking
- Likely root cause: the LLVM IR codegen for multi-return struct lowering doesn't correctly handle structs that contain managed pointer or managed-slice fields

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

---

## Done (session 2026-04-08)

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
