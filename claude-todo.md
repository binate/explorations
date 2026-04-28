# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## TODO

### ~~Method receivers (no interfaces)~~ — DONE
- Methods supported across all four execution paths: boot (Go
  interpreter), boot-comp (LLVM), boot-comp-int (bytecode VM),
  boot-comp_native_aa64 (ARM64 native).
- Receiver kinds: `T`, `*T`, `@T` (and const variants where
  applicable). Static dispatch only — no interfaces.
- One level of receiver smoothing: `*T → T` (auto-deref), `T → *T`
  (auto-take-address), `@T → *T` (reinterpret). Honored in the type
  checker, bootstrap interpreter, bytecode VM, and LLVM IR-gen.
- IR-level naming: methods are fully qualified
  (`<pkgShort>.<TypeName>.<MethodName>`); the mangler converts every
  dot to `__`, yielding `bn_<pkgShort>__<TypeName>__<MethodName>` C
  symbols.
- Conformance: 322–331 cover positive cases (basic, managed, full
  smoothing table, mutation, cross-package), the @T → *T smoothing
  case, and the three negative cases (alias, builtin, duplicate).
- Bootstrap subset: methods are now in (`bootstrap-subset.md`,
  Functions section). `impl Type : Interface` and method values
  remain deferred — see "Function values" / "Cross-package method
  visibility in .bni" entries below for the open follow-ups.
- Decision summary in `claude-notes.md` § "Method resolution &
  dispatch — DECIDED" (receiver kinds, smoothing, naming, `_`
  receiver name).

### Migrate self-hosted code to method form (opportunistic)
- Pattern: add methods alongside free functions (same body), migrate
  callers per function (perl pass for simple shapes, manual fixup for
  nested args), drop the free function + `.bni` decl.
  `conformance/run.sh boot` after each migration; full `basic` at the
  end of a batch.
- ~~`pkg/buf.CharBuf`~~ — DONE (commits `174666c` Len, `1d5a4f9`
  Bytes, `b3cd116` Freeze, `e4a90fb` WriteHexByte, `b8799cb`
  WriteInt, `b7958f3` WriteByte, `80e3ac8` WriteStr, `8f96357` test
  cleanup). `New` and `CopyStr` stay free — no CharBuf receiver.
- **Open candidates** (do as ergonomic, in any order):
  - `pkg/asm/elf/elf_util.bn:BinBuf` — same shape as CharBuf
    (`bbU8`/`bbU16`/`bbU32`/`bbU64`/`bbBytes`/`bbZeros`/`bbAlign`/
    `bbAddr`/`bbGrow`). Mechanical; ~50–100 callers.
  - `pkg/asm.Assembler` — `asm.Emit*` / `asm.AddSection` /
    `asm.AddRelocation`. Larger surface, same pattern.
  - `pkg/types.Type` — `IsInteger`, `IsFloat`, `Identical`,
    `AssignableTo`, `ResolveAlias`, `SliceElem`, `PointerElem`,
    `FieldByName`, `NeedsDestruction`, `IsConst`, `StripConst`,
    `TypeName`, etc. Cleanly mechanical; reads naturally as
    `t.IsInteger()`. Many call sites.
  - `pkg/parser.Parser` — `next(p)`, `expect(p, tok)`,
    `got(p, tok)`, `peekTok(p)`. Many small sites.
  - `pkg/lexer.Lexer` — same shape as Parser.
  - `pkg/ir.Func` / `Block` / `Instr` — `EmitConstInt(f, b, …)`,
    `EmitCall(f, b, …)`, etc. **Needs a design pass first** — most
    signatures take both Func and Block, so it's not obvious whether
    the receiver should be Block or Func. Pick one before starting
    mechanics.

### ~~pkg/vm: Stage 2b implicit-copy + OP_STRING_TO_ARRAY~~ — DONE (`9e9042a`)
- Added `BC_STRING_COPY_MS` (Stage 2b: fresh `@[]char` via
  `MakeManagedSlice` + memcpy from rodata) and `BC_STRING_COPY_ARR`
  (Stage 2c Phase 1: stack buffer of size N, zero-padded, with
  literal bytes copied in). Lowering of `OP_STRING_TO_CHARS` now
  branches on `instr.BoolVal`, mirroring the LLVM codegen path.
- Latent fix: `lowerStore` for `TYP_ARRAY` was a scalar 8-byte
  store (test `051_array_copy` passed by coincidence — only read
  element 0). Added array to both `lowerLoad` and `lowerStore`
  multi-word paths.
- Removed `xfail.boot-comp-int` markers on tests 298, 299, 307;
  boot-comp-int now at 258 passing (was 254, 7 xfails remain).
- Refactor: extracted `lowerLoad` / `lowerStore` / `lowerGetFieldPtr`
  into `pkg/vm/lower_memory.bn` to keep `lower_instr.bn` under the
  600-line cap.

### ~~Implement adjacent string-literal concatenation (C-style)~~ — DONE
- Implemented at the parser level (not lexer) because the lexer can't
  tell apart "merge me" from "you're between two grouped-import paths"
  — both look like STRING SEMI("\n") STRING. Parser merges only in
  `parsePrimaryExpr` (expression context), so grouped imports are
  unaffected.
- Cross-line merge works via a one-token parser lookahead (`peekTok`):
  if the current is STRING and the next is `SEMI("\n")` followed by
  another STRING, consume the SEMI as spurious and merge.
- Conformance test 308 covers same-line, cross-line, three-or-more,
  comment-in-gap, escapes, and the comma-blocks-merge negative case.
- Migrated `pkg/parser/parser.bn:135` (the original `// LONG-LINE
  ALLOWED` site) to use the new feature.

### ~~`&` on EXPR_SELECTOR doesn't return a field pointer (IR-gen bug)~~ — FIXED (`8866baa`)
- Pre-fix: `genUnary`'s `&` arm only special-cased EXPR_IDENT and
  EXPR_INDEX; an EXPR_SELECTOR fell through to `genExpr(e.X)` which
  emitted the LOAD of the field. Result: `&s.f` came back as the
  field VALUE rather than a field pointer; downstream
  deref/write-through touched the wrong memory.
- Fix shape was as anticipated: one branch in `genUnary`'s `&` arm
  routing EXPR_SELECTOR through `genSelectorPtr` (which already
  handles value structs, `@Struct`, `*Struct`, and indexed-element
  struct fields).
- Tests: `conformance/334_amp_on_selector` covers all four shapes
  (xfailed on boot — bootstrap doesn't support `*int` index-assign,
  separate issue not under test here). pkg/ir unit test
  `TestGenAmpOnLocalSelector` pins the IR shape directly: `&p.x`
  must produce OP_GET_FIELD_PTR and must NOT produce OP_LOAD-of-
  GET_FIELD_PTR.
- Discovered while writing diagnostic tests for the
  pkg/types-VM-regression entry below — `&target.PointerSize` and
  `target.PointerSize` returned the same number (the field address)
  in the VM, which initially looked like a VM-LOAD bug; that turned
  out to be a separate `IsGlobalRef` issue (also fixed), and the
  selector-`&` bug was the second bug they were tangled up with.

### ~~pkg/types unit tests fail under bytecode-VM modes (target.PointerSize)~~ — FIXED (`1b0cef8`)
- Symptom: 10 pkg/types tests failed under boot-comp-int /
  boot-comp-comp-int / boot-comp-comp (TestSizeOfPointers,
  TestSizeOfSlice, TestAlignOfPrimitives, TestAlignOfArray,
  TestSizeOfUniformStruct, TestSizeOfMixedStruct, TestFieldOffsetMixed,
  TestFieldOffsetPackedSmall, TestSizeOfNestedStruct,
  TestSizeOfStructWithSlice) — all transitively exercised
  `target.PointerSize` and saw a heap address instead of `8`.
- Both the original "write doesn't persist / stale zero" and the
  intermediate "OP_LOAD lowered to BC_MOV instead of BC_LOAD64"
  hypotheses were wrong. The actual bug was in **all three backends'
  global-pseudo-Instr detection**: they used a name-based heuristic
  (`Op == OP_ALLOC && len(StrVal) > 0`, with `lookupGlobalAddr(StrVal)
  != 0` as a tiebreaker for VM/arm64; LLVM used `ID == -1 &&
  len(StrVal) > 0`). Local parameter allocas tagged with the parameter
  name for debug info matched the same shape. When a local's name
  collided with a global, the local's storage was routed to the
  global's heap memory.
- Trigger in pkg/types: `MakeAliasType(name @[]char, target @Type)`
  has a parameter named `target` — same name as
  `var target TargetInfo`. The parameter prologue's STORE-into-slot
  wrote the parameter VALUE (a `@Type` pointer) into the global's
  memory; subsequent reads of the parameter loaded back from the
  global. Every call clobbered `target.PointerSize` with a heap
  pointer.
- Fix (`1b0cef8`): added `IsGlobalRef bool` to `ir.Instr`,
  `lookupVar` sets it on the global pseudo-Instr, all three backends
  (pkg/vm, pkg/codegen, pkg/native/arm64) key off the flag instead of
  the name. Regression test `conformance/333_param_shadows_global`
  covers the exact pattern.
- Discovery surfaced one separate IR-gen bug (still open): see "&` on
  EXPR_SELECTOR doesn't return a field pointer" entry above.
- Verified: boot-comp-int unit tests now 29/29 passing (was 28/29 with
  pkg/types failing 10 tests). conformance basic clean across modes.

### Clarify rules for integer literals and constant expressions
- The bootstrap interpreter rejects hex literals with the high bit set
  (`strconv.ParseInt(..., 16, 64)` overflows int64), e.g.
  `0xFFFFFFFFFFFFFFFF`. The self-hosted type checker silently wraps
  via int64 overflow in `pkg/types/checker_util.bn:parseHexInt`. Two
  different bugs, both surprising.
- Go-style bignum support for constant expressions is too onerous, but
  we should at least support `uint64` literals — i.e. accept any
  64-bit value as either signed or unsigned depending on context, and
  reject (not wrap) values outside the chosen 64-bit range.
- Open questions to nail down in the spec:
  - What's the type of an integer literal? Currently "untyped int"
    that fits in int64; should an unsigned literal too big for int64
    but fitting in uint64 be allowed?
  - What about constant-expression overflow at type-check time
    (`1 << 63`, `0xFF * 0xFF * ... `)? Today it silently wraps.
  - Hex / binary / octal literals all need consistent rules.
- Update both impls together; document the result in claude-notes.md
  and update binate-coding-guide.md.

### Clarify spec for `return f(...)` with multi-return functions
- Today both impls reject this: bootstrap (`types/checker.go:963–978`)
  and self-hosted (`pkg/types/check_stmt.bn:237`) require the number
  of return-statement expressions to equal the number of declared
  result types, with no unpacking from a single multi-return call.
- Probable resolution: support it (Go-style `return f()` where `f`
  returns the matching tuple). Then implement in both checkers.
- Spec change goes in claude-notes.md; remove the rule from the
  hygiene/bootstrap-subset docs once both impls handle it.

### boot-comp-int-int: SIGSEGV after ~218s (post-BC_RETURN-fix)
- (Mode renamed from `boot-comp-int2-int2` after the int2→int rename in `b1e4f98`.)
- History (2026-04-25/26):
  1. Original symptom: SIGSEGV with no output.
  2. `bootstrap.ReadDir` was missing from `pkg/vm/vm_extern.bn` — added the binding. Fixed in `c44419f`.
  3. Next symptom: clean `vm: stack overflow` after ~35s on `001_hello` at 8 MiB stack.
  4. Probe at 64 MiB → clean overflow replaced by host SIGSEGV after ~335s.
  5. Probe at 1 MiB + diagnostic dump in `pushFrame` overflow handler → caller depth only **4** (main → runProgram → LowerModule → lowerFunc); `lowerFunc` runtime frame ~998 KB; lower-time frame only ~7912 B → **126x bloat per call**.
  6. Root cause identified: `BC_RETURN` was bumping `callerSP = vm.SP` whenever retVal pointed into callee region — leaking the entire callee frame on every call. In `lowerFunc`'s loop calling `lowerInstr`, ~90 × 11000 B ≈ 990 KB leaked.
  7. **FIXED in `be3c22e`**: `BC_RETURN` now mirrors `execFunc`'s copy-then-pop pattern, but with a precise size known at lower time (encoded in `BC_RETURN.Aux` for single returns; existing `totalSize` for multi-returns). Conformance test 320_struct_return_loop covers it.
  8. New symptom (2026-04-26 post-fix): `001_hello` runs for ~218s (vs 35s pre-fix), peaks at ~152 MiB RSS, then exits with SIGSEGV (139). No "vm: stack overflow" — this is genuine memory corruption / bad pointer, not a VM-stack issue.
- **Why progress matters**: pre-fix, the leak hit overflow within ~35s of useful work. Post-fix, ~6× more work happens before any failure, so the next bug is much further along the execution. The new SIGSEGV is a separate (heap-side) bug, not a regression.
- Not in the `all` modeset, so CI/default runs don't exercise it.
- **Next** (skipped this session — needs separate investigation):
  - Identify what's at 152 MiB RSS — is the heap growing without bound (leak), or is it a one-shot bad alloc that crashes after some pattern of work?
  - Run under lldb / Address Sanitizer (compile bni with `--cflag -fsanitize=address` per the `--cflag` precedent from earlier debugging) to catch the bad access at the moment it happens.
  - The bug likely lives in pkg/vm or a runtime helper called from VM-interpreted code; native-compiled cmd/bni doesn't trigger it.

### ~~Native AArch64 backend — float args via D-registers (`287_float_println`)~~ — DONE (`8cd555e`)
- Two-part fix:
  - `common.IsFloatScalarTyp` and `CallArgRegStart` / `CallArgStackOff`
    / `CallStackBytes` skip floats from the GP NGRN budget. Mixed
    `(int, float, *[]u8)` signatures now place the slice at X1..X2
    instead of X2..X3 (`bootstrap.formatFloat(v float64, buf *[]uint8)`
    is the canonical case).
  - `emitFunc` prologue tracks NSRN separately and reads each float
    param from D(NSRN) via FMOV → scratch GP → spill slot, mirroring
    `emitCall`'s already-present caller-side NSRN handling.
- Tests: `pkg/native/common.TestIsFloatScalarTyp` and
  `TestCallArgRegStartSkipsFloats` lock in the dispatch behavior.
  Conformance 287_float_println passes on `boot-comp_native_aa64`;
  full native conformance 278/278.

### Native AArch64 backend — unit-test packages failing under `boot-comp_native_aa64`
- Conformance suite passes end-to-end under `boot-comp_native_aa64`,
  but a unit-test sweep on 2026-04-27 failed 10 of 29 packages. Three
  clusters: (C) a Mach-O reloc emission bug (pkg/ir), (A) seven
  test-binary crashes/runtime errors, (B) two packages with
  assembler-encoding assertion failures.
- **Cluster C — DONE** (`8bc6196` + `f18ff2c` + `e4c9edd` + `491ac60`):
  Mach-O r_extern always 1; `cmd/bnc --keep-objs`; cross-section string
  refs use ADRP+ADD instead of ADR (±1MB → ±4GB); ResolveFixups errors
  on out-of-range PC-rel fixups; macho writer rejects unsupported
  fixup-kind→reloc mappings; new tests in `pkg/asm/aarch64` and
  `pkg/asm/macho`.
- **Cluster A — partial** (`ca9f287` + `ac7be3f`): a tight conformance
  reduction (`332_struct_arg_forward_inserts`) caught the
  pkg/asm/macho TestLoopSum crash. Root cause: `regPool(i)` returns
  X15 for any index >= 6, so `getOperand` (for the source pointer)
  and `scratchReg` (for the load temp) both hand out X15 once
  m.Next exceeds the pool. The collision turns the per-word ldr/str
  into `ldr x15, [x15, #N]` chasing through loaded values — eventually
  faults on the first NULL it traces. Fixed in emitCall's stack-arg
  branch by hardcoding X16 (AAPCS intra-call scratch) for the load
  temp; safe across ldr/str (no `bl` between).
  - **pkg/asm/macho** unblocked. Other cluster A packages (pkg/types,
    pkg/asm/parse, pkg/asm/aarch64, pkg/native/arm64, pkg/codegen,
    pkg/vm, pkg/ir) need verification via a clean re-sweep — they
    may be the same bug or other distinct crashes.
  - pkg/types specifically had a different shape pre-fix: crash inside
    RefInc writing to a read-only memory region (`r--`), suggesting a
    bad managed pointer — possibly unrelated to the X16 collision.
  - Larger root cause: regPool's saturation at X15 is unsafe in
    general. A real fix spills when the pool is exhausted (or grows
    the pool); the X16 patch only covers this one call site. Worth
    a follow-up.
- **Cluster B — DONE** (`43ab7a3`): one root cause for all 22 failures
  — native ARM64 mishandled multi-return tuples with sub-word fields.
  The caller-side spill walked by 8-byte word, losing the second
  X-register for `(uint32, uint32)`; emitExtract used 64-bit LDR for
  sub-word fields. Fixed by walking by FIELD (with sized stores) and
  size-dispatching through emitScalarLoad. pkg/asm/elf 22/22; the
  19 dpEnc-family tests in pkg/asm/arm32 all pass.
- Full inventory + plan of action in `explorations/native-aa64-bugs.md`.
- CI hookup for `boot-comp_native_aa64` is intentionally not landed
  yet — wait for cluster A residual + clean re-sweep.

### Native AArch64 backend — regPool saturation (cluster A follow-up)
- `pkg/native/arm64/arm64_regmap.bn`: `regPool(i)` returns X15 for
  any `i >= 6`. The pool is X9..X15 (7 slots). When more SSA values
  are simultaneously live than the pool has, both `nextReg` and
  `scratchReg` collapse to X15, silently aliasing distinct values.
- The cluster A X16 fix patches this for one specific call site. Any
  other call site that uses `scratchReg` while a same-pool reg holds
  a live value risks the same collision.
- Real fix: spill on pool exhaustion (or grow the pool, with the
  spill-on-exhaustion fallback). Today the codegen doesn't have a
  spill mechanism for in-instruction temporaries, so this is
  non-trivial. Tracked separately so the cluster-A entry can close
  cleanly when the rest of the cluster is sorted.

### ~~Remove OP_CALL_BUILTIN and the empty C-runtime manifest~~ — DONE (`0b7dd90`)
- After Step 2b (print rewired to `bootstrap.formatX` + `bootstrap.Write`)
  and Step 3.2 (`bn_exit` migrated to `rt.Exit`, runtime manifest
  emptied), no IR-gen path emitted `OP_CALL_BUILTIN`. Plumbing was
  dormant; this commit removed it (20 files, −332 lines net).
- Removed: `pkg/ir/runtime.bn` + `runtime_test.bn` (entire files);
  `OP_CALL_BUILTIN`, `EmitCallBuiltin`, op-name dispatch arm, and the
  `RuntimeFunc`/`RuntimeFuncs`/`RT_*` block from `pkg/ir.bni` +
  `pkg/ir/ir_ops.bn`; `RuntimeFuncs()` declare-emission loop +
  `emitRuntimeDecl` + `rtKindToLLVM` from `pkg/codegen/emit.bn`;
  `OP_CALL_BUILTIN` arms from `emit_util.bn` / `emit_ops.bn` /
  `emit_instr.bn`; `OP_CALL_BUILTIN` arms (~6 sites) from
  `pkg/native/common/common.bn`; arm from `pkg/native/arm64/arm64.bn`;
  `isBuiltin` parameter from `pkg/native/arm64/arm64_ops.bn:emitCall`
  (collapses `_underscorePrefix` vs `symFor` to `symFor` only);
  `BC_CALL_BUILTIN` from `pkg/vm.bni` + `pkg/vm/vm_exec.bn` +
  `pkg/vm/lower_instr.bn` + `pkg/vm/lower.bn`; `execBuiltin` from
  `pkg/vm/vm_extern.bn`; `TestEmitCallBuiltin` from
  `pkg/ir/ir_ops_test.bn`.
- Verified: boot 202/202, boot-comp 278/278, boot-comp-int 271/271,
  boot-comp-comp 278/278, boot-comp-comp-int 277/277. Hygiene 9/9.
- Cherry-pick onto main (post-merge with `pkg/buf` Stage-9 migrations)
  required one-file conflict resolution in `pkg/codegen/emit_ops.bn`:
  combined the OP_CALL_BUILTIN-arm collapse with main's `.Bytes()`
  method-syntax migration. boot-comp 278/278 post-merge confirms.

### Un-export `rt.c_*` — wrap in Binate, hide from .bni
- Today `pkg/rt.bni` exports the C-stub bridges (`c_malloc`, `c_calloc`, `c_free`, `c_memset`, `c_memcpy`, `c_call_dtor`, `c_bounds_fail`, plus historically `c_exit` and `c_print_float`). They're conceptually implementation details, not part of pkg/rt's public API. Direct callers in pkg/* and cmd/* tie the rest of the codebase to the libc-target shape — on a libc-free target the same operations would dispatch through syscall stubs (or be inlined in Binate).
- Pattern is already established by `rt.Exit` (`a631ca9`): a thin Binate wrapper that currently calls `c_exit` but on a libc-free target would route through a syscall stub instead. Same shape needed for the rest.
- **Scope**:
  - Inventory every direct caller of `rt.c_*` outside `pkg/rt` itself (likely substantial — c_memcpy / c_memset are everywhere).
  - Add Binate wrappers for each c_* that has external callers. Naming convention: `rt.Memcpy`, `rt.Memset`, etc. (or pick whatever reads best — possibly bring them under existing higher-level helpers where applicable).
  - Migrate callers.
  - Un-export the c_* in `pkg/rt.bni` (move declarations to `pkg/rt/rt.bn` as package-private).
  - Update the bni naming whitelist (drop the c_* entries).
- Why now-ish: aligns with the multi-backend / libc-free target direction in `runtime-abstraction-plan.md`. Each c_* removed from the public surface is one less thing the ARM32 / bare-metal backend has to reproduce.
- Why not urgent: c_* are working today; this is a refactor for future portability, not a correctness fix.

### Lift function-name qualification into IR (shared across backends)
- The VM and the compiler both need to avoid cross-package function-name collisions. They currently solve it separately: `pkg/mangle.FuncName(pkgName, name)` produces C-style `bn_asm__New` for LLVM symbols, and `pkg/mangle.QualifyName(pkgShort, name)` produces dot-form `asm.New` for the VM's function table. Both backends extract the short package name from `ir.Module.Name` and apply their own qualification at lower/emit time.
- That duplication is fine but a cleaner alternative is to qualify in IR itself: have `pkg/ir` store all function names fully qualified ("asm.New", "bootstrap.Args") as canonical. `mangle.FuncName` already treats dotted names as pre-qualified, so the compiler would keep producing the same `bn_asm__New`. The VM would use qualified names directly. One source of truth.
- Not urgent — the current per-backend qualification works and the shared helpers in `pkg/mangle` de-duplicate the core logic. Worth revisiting if backend drift keeps biting (e.g., when adding the 32-bit ARM backend).
- Scope: touches `ir.GeneratePackage` (which currently emits unqualified names for intra-package functions), `moduleFuncs` lookup sites, `EmitCall`/`EmitFuncAddr` call sites, and all callers that pass a simple name to IR. Backends would shed their `modulePkgName` state.

### ~~boot-comp-int: all unit-test packages pass~~ — DONE
- All 27 unit-test packages pass under boot-comp-int (cmd/bni bytecode VM); zero xfails. Down from 17 failing at start of work.
- **Fixes**:
  - pkg-asm and cmd-bnc unblocked by VM function-name qualification fix (`32eb2f6` / `76294d8`).
  - pkg-asm-macho's `bootstrap.Exec` extern stub fixed (`e6b0d00`); pkg-asm-elf/macho unblocked via `bootstrap.Stat` extern stub fix (`4b70a9b`). Conformance tests 273 / 277.
  - Cross-package struct field resolution fix (`2be80b9`); conformance 270.
  - **pkg-ir, pkg-codegen, pkg-vm unblocked** by zero-init fix (`0933158`). Root cause: `var x T` (no initializer) for struct/array types allocated uninitialized memory; subsequent `x.field = ...` did "axiom 5 copy-then-destroy" — load old + RefDec — on garbage bytes that occasionally looked like a valid managed pointer, freeing a stranger's allocation. LLVM hides this via dead-load elimination on uninitialized allocas; the bytecode VM doesn't. Fix: IR now emits `OP_CONST_NIL + OP_STORE` after `OP_ALLOC` for struct/array types that contain managed fields. Both backends consume the same IR — refcount semantics are now IR-driven. Also extended pkg/codegen's `emitConstNil` to handle struct/array/named types.
  - **cmd-bnlint unblocked** by VM `bootstrap.Args` extern fix (`503a79b`). Stub was returning 0; cmd/bnlint's findRoot called bootstrap.Args() and crashed on null managed-slice. Fix: call host bootstrap.Args(), push the @[]@[]char header, and pre-RefInc both the outer and each inner @[]char's backing so the result's scope-cleanup dtor leaves all allocations alive for the VM caller.
- (Note: the prior `boot-comp-int2` mode was renamed to `boot-comp-int` in `b1e4f98` after `pkg/interp` and `cmd/bni` were retired; only one interpreter mode remains.)

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

### Function values: compiled-VM-compatible representation (required for interop)
- Function values MUST use the same representation in compiled and VM-interpreted code, because function values can be passed between the two modes.
- **Target**: `{funcPtr, closureCtx}` pair matching compiled representation. For VM-interpreted functions, `funcPtr` would be a trampoline that dispatches into the VM using `closureCtx` to find the bytecode, closure env, types, and aliases.
- **Current**: bootstrap subset doesn't have closures or first-class function values, so representation hasn't been forced yet.
- **When this blocks**: closures, function values in slices/maps, callbacks between compiled and VM-interpreted code.
- **Method values** (`x.M` as a first-class value) and method expressions
  (`T.M`) are deferred to the same feature work — they're a closure with
  the receiver bound. Once function values land, methods can adopt the
  same representation.

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

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

### ~~Bounds checks on `s[i]` / `s[lo:hi]` are not wired up~~ — DONE
- `emitIndexBoundsCheck` helper added in `pkg/ir/gen_access.bn`; called from `genIndex`, from the multi-return / EXPR_INDEX assign paths in `gen_control.bn`, and from `genSliceExpr` (two checks: hi against len+1, lo against hi+1). `unsafe_index` stays check-free — `genIndex` takes a `checked bool` param and `EXPR_INDEX` passes true while `unsafe_index` passes false.
- Conformance tests 309–314 cover index OOB on slice/array, index-assign OOB, slice-hi OOB, slice lo>hi, and negative slice lo. Tests 312/313/314 xfailed on boot only because Go's bootstrap interpreter formats the trap message differently. (Original numbers 298–303; renumbered when conformance suite duplicates were resolved.)

### ~~`const` type modifier~~ — Stages 0–2c LANDED; Stage 3 deferred
- Stage 0 (syntax + TYP_CONST wrapper kind), Stage 1 (enforcement
  + cast drops), Stage 2a (reject `string → *[]char`), Stage 2b
  (implicit alloc+copy for `@[]char = "..."`), and Stage 2c (string
  literal natural type `[N]const char`, default `@[]const char`,
  array-init copy `var s [N]char = "..."`, managed-slice + raw-slice
  composite literals `@[]T{...}` / `*[]const T{...}`) all landed.
- Stage 3 (const method receivers) deferred — depends on the
  methods/interfaces feature.
- Ratification: Phase 3 of the composite-literal generalization plan
  (next entry) supersedes the spec for *how* string literals lower at
  the IR level. The semantic surface is fixed.

### ~~Phase 3: unify strings as composite-literal sugar~~ — DONE
- Plan: `plan-composite-literal-generalization.md` § Phase 3 +
  `plan-phase3-string-unification.md` (sub-plan).
- End state: no string-specific IR ops, no `TYP_STRING` kind. String
  literals flow through the same `OP_RODATA_*` ops as user-written
  const-byte composite literals. Backend lowerings are uniform.
- Stages and commits:
  - **3.1** (`c164807`) — added `OP_RODATA_MSLICE` / `OP_RODATA_SLICE`;
    `genManagedSliceLit` / `genRawSliceLit` detect all-const-byte
    composites at IR-gen time and emit the new ops directly. Conformance
    test 320 covers `@[]const char{'a','b','c'}` etc.
  - **3.2** (`1264902`) — `EmitStringToChars` redirects read-only
    string→slice through the new ops.
  - **3.2b** (`29c4aaf`) — added `OP_RODATA_ARRAY`; redirected
    string→array through it.
  - **Stage 2b copy** (`d043acf`) — added `OP_RODATA_MSLICE_COPY` for
    `@[]char = "..."` (mutable) — alloc + memcpy from rodata.
  - **3.3** (`a868b4c`) — deleted `OP_STRING_TO_CHARS`,
    `OP_STRING_TO_ARRAY`, `EmitStringToArray`, all backend lowerings.
  - **3.4** (`b7243e7`) — eliminated `TYP_STRING`; IR-gen dispatch
    keys on `val.Op == OP_CONST_STRING` instead of the type-marker.
  - **Test backfill** (`4a2eb28`) — 7 IR-gen unit tests for the
    dispatch + fast-path detection.
- `EmitStringToChars` survives as the multi-way dispatch helper that
  picks the right rodata op based on target type. `OP_CONST_STRING`
  also survives — it's the IR's "raw bytes pointer" op (lowers to
  LLVM `getelementptr`), now typed as `*const uint8` instead of
  `TYP_STRING`. Both are non-string-specific in shape.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### ~~Enforce parse-level rejection of function-local `type` declarations~~ — DONE
- Both parsers (`pkg/parser/parse_stmt.bn` and
  `bootstrap/parser/parser.go`) now emit
  `"type declarations must be at package level, not inside a function
  body"` when they encounter `TYPE` at statement position. Recovery
  is "parse the type-decl anyway and discard," so downstream parsing
  isn't derailed.
- Conformance test 319 (`319_err_function_local_type`) covers the
  rejection across all three basic modes.

### Test harness `isTestResultReturn` should resolve type aliases
- The test harnesses (bootstrap Go `main.go` and self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

### ~~.bni/.bn return type mismatch should be a compile error~~ — FIXED
- The type checker now verifies that `.bn` function definitions match their `.bni` declarations (parameter count/types, return count/types). Mismatches are reported as compile errors.
- Immediately caught two real bugs: `MakeStringVal` and `AddBlock` had `@[]char` in `.bni` but `*[]char` in `.bn`. Both `.bni` files fixed.
- Conformance test 221 now passes on all compiled modes.

### ~~Compiler bug: cast to sub-word pointer type emits invalid LLVM IR~~ — FIXED
- Cast codegen now uses `bitcast` (ptr→ptr), `ptrtoint` (ptr→int), `inttoptr` (int→ptr) instead of `add` for pointer types.
- Conformance test 161 passes on all compiled modes.

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

### ~~Multi-return as anonymous struct~~ — DONE
- Multi-return is an ABI contract: `func f() (T1, T2)` returns `struct { _0 T1; _1 T2 }`.
- Compiler side done long ago: `Func.MultiReturnType` propagated through FuncSig/call sites/return instructions; LLVM emission uses `llvmType(MultiReturnType)`.
- Interpreter side moot: the original tree-walker `pkg/interp` was retired in 2026-04-17. The bytecode VM (`pkg/vm`) consumes the compiler's IR directly, so it inherits the anonymous-struct layout — no separate work. Verified 2026-04-26: zero references to `VAL_MULTI`, `Value.Elems`, or `HeapObj` remain in pkg/ or cmd/.
- Plan file `plan-multi-return-struct.md` deleted (was MOOT).

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

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal, not Linux —
  we want to write the OS in Binate, not run on top of one.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite**: function values. We need at least basic function-value
  representation for the descriptor's fields (pointers to functions) to be
  expressible. The compiled-VM-compatible representation in the "Function
  values" entry below is exactly the substrate this needs — a single
  `{funcPtr, closureCtx}` pair that both sides can construct and consume.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values: compiled-VM-compatible representation" (below) — direct
    prerequisite.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

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
- **x86-64 native end-to-end tests on Linux** (`29f4230`): assemble x86-64 → ELF64 → link with cc → run via host SYSCALL. Three tests in `pkg/asm/elf/elf_test.bn`: `TestX86_64ElfExit` (exit via SYSCALL), `TestX86_64ElfLoop` (sum 1..9 = 45), `TestX86_64ElfCall` (function call with PUSH/POP). `canLinkX86_64Elf()` probe makes them skip cleanly off Linux/x86-64. Verified passing on CI.
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
