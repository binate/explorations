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

### ~~boot-comp-int: cross-pkg multi-return struct destructure clobbers struct on 2nd+ call~~ — FIXED (`c5b29cb`)
- The hypothesis ("destructure path overlaps src/dst on 2nd call") was
  wrong. The actual bug was in BC_RETURN's multi-return *packing*:
  the branch chose MEMCPY vs scalar-store based on `sz > 8`, but
  `srcVal` is a *pointer* for any multi-word type (lowerLoad returns
  the alloca pointer for struct/slice/array). For a struct exactly 8
  bytes (like `Counter { Val int }`), the scalar branch wrote the
  pointer-to-callee's-local-alloca into the tuple slot; after the
  callee frame popped, the destructure landed a pointer-into-dead-
  stack-memory in the destination variable. The 1st call's
  destructure of `c` was already corrupt — just unobserved until the
  2nd call's `prev2` (= old `c.Val`) and the final `Read(c)`
  surfaced it.
- Fix: branch on type, not size. `VMFunc.ResultMultiWord []bool`
  populated at lower time via `isMultiWordField(t)`; BC_RETURN
  consults it and chooses MEMCPY for any multi-word type regardless
  of size.
- conformance/157_cross_pkg_struct_multiret xfail.boot-comp-int
  removed; passes boot-comp-int and boot-comp-comp-int.
- Conformance basic green (204/281/275 — boot-comp-int +1 pass);
  pkg/vm unit tests green.

### ~~boot-comp-int-int: SIGSEGV after ~218s (post-BC_RETURN-fix)~~ — FIXED (`900a44e` + `a723acb`)
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
- **Diagnosis (2026-04-29)**: ASan caught a HOST stack-overflow
  inside `malloc`, triggered from
  `execLoop → execExtern → libc.Malloc`. Diagnostic instrumentation
  showed `execFuncCalls=1` and `execFuncDepth=1` throughout the
  entire 260M+ iteration run — so the leak was NOT host-recursion of
  `execFunc`. ulimit confirmed it was a true leak (8 MiB → 246s,
  64 MiB → 1264s, roughly 5x more time for 8x more stack).
- **Root cause**: 1 alloca outside execLoop's entry block —
  `var callArgs @[]int = make_slice(int, instr.Imm)` declared
  inside the BC_CALL extern branch. bnc emits the @[]int header
  alloca in that branch's BB, not the function entry, so each
  extern call leaks 32 bytes that's only released on execLoop
  return. 8 MiB / 32 = 262144 extern calls before overflow —
  matches the observed ~218s.
- **Fix (two commits)**: First (`900a44e`) hoisted callArgs's @[]int
  header alloca by declaring it at function entry — but bnc still
  emitted a temp alloca for `make_slice`'s sret return INSIDE the
  branch when the buffer needed to be (re)allocated, so the leak
  was only partly closed. Second (`a723acb`) closed it fully:
  pre-allocate a generously-sized callArgs (capacity 64) ONCE at
  entry; reuse across all extern calls; panic on overflow.
  Bundled with a defensive iterative-dtor reform of BC_REFDEC
  (no host recursion through dtor cascades), though that wasn't
  load-bearing for this specific bug.
- **Regression test**: `conformance/339_extern_call_loop.bn` —
  1M iterations of `bootstrap.Close(-1)` (cheap scalar-arg extern
  that doesn't push onto vm.Stack per call). Pre-fix, SIGSEGV at
  ~150K calls. Post-fix, runs in <1s.
- **Followup work landed in this same arc**:
  - `f3478cb` (codegen-side hoist for OP_MAKE_SLICE / sret OP_CALL):
    closes the bug class in the LLVM backend.
  - `daacfe3` (BC_LOAD_STR no-push): closes the parallel vm.Stack
    leak so loops with string-literal extern args don't overflow
    vm.Stack at ~262K iterations.
- **Aftermath**: After the full chain (`900a44e` + `a723acb` +
  `f3478cb` + `daacfe3`), boot-comp-int-int 001_hello no longer
  hangs OR crashes silently. It now exits cleanly with a
  diagnosable error from a SEPARATE bug:
  `vm: indirect call: function index out of range`. That comes
  from BC_CALL_INDIRECT's dtor-dispatch path (the new f08ddcb
  `rt._call_dtor` mechanism) — its own followup, tracked below.

### boot-comp-int-int: BC_CALL_INDIRECT "function index out of range" on 001_hello
- After the chain of fixes for the host-stack overflow + vm.Stack
  leak (`900a44e` / `a723acb` / `f3478cb` / `daacfe3`),
  boot-comp-int-int 001_hello now reaches a clean error rather
  than hanging or SIGSEGVing: `vm: indirect call: function index
  out of range` from BC_CALL_INDIRECT.
- **Diagnosed (2026-04-30)**: caller is bytecode `rt.Free`; fnIdx
  is a NATIVE function pointer (e.g. 0x1043F5BAC ≈ 4.37e9) being
  treated as a 1-based VM index. The allocation was made by
  NATIVE rt.Alloc (e.g. via the BC_MAKE_SLICE handler in vm_exec.bn
  calling native rt.MakeManagedSlice → native rt.Alloc, which
  stores `_raw_func_addr(RawFree)` in h[1] as a native pointer);
  it's then RefDec'd by bytecode rt.RefDec (in vm.Funcs because
  cmd/bni source imports pkg/rt), which calls bytecode rt.Free,
  which dispatches via h[1]'s value as if it were a 1-based VM
  index — out of range.
- **Fix path**: this is the function-values cross-mode interop
  problem at full strength. The right answer is what upstream is
  building: replace the indexed values with REAL function
  pointers, going through trampolines for bytecode-target
  callees. Then `h[1]` (and every other "function pointer" slot)
  carries a stable C function pointer in both modes — natively
  callable, plus bytecode-side `_call_free_fn` becomes a real
  indirect call. Required for bidirectional native↔bytecode
  interop, not just this specific bug.
- Not in the `all` modeset, so doesn't block CI. Tied to the
  function-values work (Slice A.2 just landed).
- vm_exec.bn's BC_CALL_INDIRECT diagnostic now prints fnIdx,
  vm.Funcs length, and caller name on failure — useful for the
  next person debugging cross-mode dispatch issues.

### ~~bnc: hoist managed-slice allocas to function entry~~ — FIXED (`f3478cb`)
- pkg/codegen already hoisted OP_ALLOC decls to the entry block via
  emit_debug.bn's hoisting loop. But two other inline-alloca paths
  were leaking:
  - emitMakeSliceInstr's `.p = alloca %BnManagedSlice` slot for
    bn_rt__MakeManagedSlice's store/load shuffle.
  - emitCall's sret path's `.sret = alloca <type>` slot for callees
    using sret return convention.
- Fix: extended the hoisting loop to cover OP_MAKE_SLICE and sret
  OP_CALL via two new helpers (`emitMakeSliceAllocDecl`,
  `emitSretAllocDecl`). The original emit*Instr functions now emit
  only the non-alloca portion.
- Verified pkg/vm LLVM IR has zero non-entry allocas across all
  functions. With this change, the prior hand-hoisted fix
  in execLoop's BC_CALL extern branch (a723acb) is no longer
  load-bearing — the codegen would have hoisted that case too.
  The hand-hoist stays as belt-and-suspenders.
- bnc IR-gen still emits OP_ALLOC at the current insertion point;
  the codegen is what fixes it post-hoc. A future cleanup would
  move the hoisting upstream to IR-gen, but the current arrangement
  is correct.
- Independent followup (still open): bnc -O2 has missing-symbol
  link errors. Worth investigating separately if/when we want
  optimization enabled by default.

### ~~conformance/283_float_untyped: VM float32 storage~~ — FIXED (`882893c`)
- VM registers carry IEEE bits in their declared width — float64 in
  8 bytes, float32 in low 4 bytes (zero-extended). float64 → float32
  needs a real IEEE conversion (the exponent biases differ); the
  prior lowering emitted BC_MOV, which left float32 storage
  containing the low half of a float64 bit pattern (garbage).
- Fix added BC_F64_TO_F32, BC_F32_TO_F64, and BC_F32TOSI; lowerCast
  now picks the right one for f64↔f32 width changes and f32→int.
  lowerLoad/lowerStore for float32 stay as 4-byte sub-word ops; the
  cast does the conversion.
- 283 now passes boot-comp-int and boot-comp-comp-int (both in
  `all`); xfail markers removed. The boot-comp-int-int xfail was
  also dropped — the test still fails there but only because the
  mode itself is broken (see entry above).

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

### ~~Native AArch64 backend — unit-test packages failing under `boot-comp_native_aa64`~~ — DONE (`1612221`)
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
- **Cluster A residual — DONE** (`1612221`): all 8 remaining failing
  packages collapsed to a single root cause — `aarch64.Str/Ldr/Strb/
  Strh/Ldrb/Ldrh` silently masked the imm12 offset to 12 bits when it
  didn't fit. Frames > 32KB (or for sub-word ops, > 4KB) caused
  STR/STRB to write at a truncated address, corrupting unrelated data
  in the same frame. The auto-generated test runner has a frame
  proportional to the test count, so packages with many tests
  (pkg/types, pkg/codegen, pkg/native/arm64, pkg/ir, etc.) all hit
  this. Fix: `emitLdrStr` and `ldrStrSubWordEmit` materialize
  base+off into X17 when the offset doesn't fit
  (`LdrStrImmFitsUnsigned`). Clean sweep: 29/29 unit-test packages,
  285/285 conformance.
- Full inventory + plan of action in `explorations/native-aa64-bugs.md`.
- CI hookup for `boot-comp_native_aa64`: cluster A is closed — safe
  to land now.

### ~~Native AArch64 backend — cross-package by-value struct ABI mismatch (`337_cross_pkg_struct_arg`)~~ — FIXED (`0e3f357`)
- Surfaced while reducing the original cluster A pkg/asm/arm32 LDRSH
  unit-test crash. Not the cause of that crash — unit tests build all
  packages with native, so caller and callee agree. But it was a real
  native-backend bug exposed by the conformance runner, which builds
  main with -backend native and dependencies via LLVM.
- Repro: 56-byte struct (3 ints + @[]char), passed by value to a
  function in another package after 2 leading int args. LLVM's callee
  prologue does a split fill (X2..X7 + 1 stack arg). Native main's
  emitCall used to put the whole 7-word struct on stack[0..48] — when
  `ngrn + w > 8`, `CallArgRegStart` returned -1 and emitCall took
  the all-stack branch.
- Fix in `0e3f357`: support split passing in three call sites:
  1. `pkg/native/common/common.bn` `CallArgRegStart` /
     `CallArgStackOff` / `CallStackBytes` — when an aggregate
     straddles, regStart returns the first reg AND stackOff returns
     the overflow start; both can be ≥ 0 simultaneously.
     CallStackBytes only counts post-X7 words.
  2. `pkg/native/arm64/arm64_ops.bn` emitCall aggregate branch — fill
     `8 - regStart` regs first, then write overflow to stack via X16.
  3. `pkg/native/arm64/arm64.bn` prologue aggregate branch — store
     reg portion to data slot, copy overflow words from caller's
     stack-args area.
- Bug required the @[]char (managed-slice) field to repro — pure-int
  structs of the same total size pass. LLVM's struct ABI for managed
  types differs from int-only structs, so the disagreement only
  triggered on managed-aware structs.
- Conformance test `337_cross_pkg_struct_arg` (multi-package). Now
  passes under `boot-comp_native_aa64`. Verified no regressions:
  pre-fix and post-fix unit-test sweeps both 18 passed, 11 failed,
  same 11 packages.

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

### ~~Un-export `rt.c_*`~~ — DONE (via pkg/libc, `43179b7` / `eae28a1` / `d3e2081`)
- `pkg/rt.bni` no longer exports any `c_*` bridges. The libc dependency surface (Malloc / Calloc / Free / Memset / Memcpy / Exit) lives in a new package `pkg/libc` (.bni-only; implementations in `runtime/libc_stubs.c`). pkg/rt imports pkg/libc and forwards its raw-memory wrappers (RawAlloc / RawAllocZero / RawFree / MemCopy / MemZero) through it.
- pkg/libc is the **only** "magic" package: it is always libc, and on a libc-free target (ARM32 bare-metal etc.) code does NOT substitute a different pkg/libc — instead, that target ships an entirely different pkg/rt that doesn't import pkg/libc and implements the runtime directly.
- Naming whitelist: the eight `pkg/rt.bni:c_*` exemptions were dropped (no longer needed since `c_*` is gone).
- One residual non-libc C extern remains: `rt.CallDtor` (function-pointer dispatch helper in `runtime/rt_stubs.c`). Tracked separately under "Retire `rt.CallDtor`" below.
- The cmd/bnc + cmd/bni IR-gen drivers auto-import pkg/libc into every package's IR module (mirroring the existing pkg/rt and pkg/bootstrap auto-imports), so `bn_libc__Memcpy` calls emitted by the backends always have a matching `declare` line. Regression tests in `cmd/bnc/compile_test.bn`.
- Discovery sequence: rename the wrappers to RawAlloc/RawAllocZero/RawFree/MemCopy/MemZero with proper preconditions (`fde6760`); introduce pkg/libc + migrate pkg/rt (`43179b7`); switch backend memcpy emission to `bn_libc__Memcpy` (`eae28a1`); auto-import pkg/libc (`d3e2081`).

### ~~Retire `rt.CallDtor` via `OP_CALL_INDIRECT`~~ — DONE
- **Plan doc**: `explorations/plan-call-indirect.md`.
- `rt.CallDtor` is gone. RefDec now calls a compiler-internal helper `_call_dtor` (declared in `pkg/rt.bni` as a type-checking shape only — no real symbol). IR-gen recognizes the `_call_dtor` / `rt._call_dtor` symbol and emits `OP_CALL_INDIRECT` in place of `OP_CALL`. `runtime/rt_stubs.c` deleted; `vm_extern.bn`'s two `rt.CallDtor` arms removed; the C trampoline retires.
- **Path taken (option C from the plan)**: compiler-internal-only — no new builtin or keyword. The `.bni` decl gives the type-checker the right signature to validate RefDec's call against; IR-gen swaps in `OP_CALL_INDIRECT` for that one magic name. Lighter weight than designing a `call_indirect` user-facing builtin; generalizes naturally when function values land (which will need their own spelling).
- **Hygiene**: `scripts/hygiene/naming.sh` was tightened to also flag `_`-prefix exports (previously the `[a-z]` regex let them slip through). `_call_dtor` is whitelisted.
- **Commits**: `ee93644` (PR 1: IR op + LLVM), `6f064a5` (PR 2 part 1: VM lowering), `4e20ffb` (PR 2 part 2: native arm64), `f08ddcb` (PR 2 part 3: RefDec migration + retire C trampoline).
- **Paired with**: "Free-function pointer in managed-allocation header — bug" (also DONE) — `Free` reads `header[1]` and dispatches indirect through it via the parallel `_call_free_fn` magic helper, sharing the same OP_CALL_INDIRECT lowering as `_call_dtor`.

### Inline RefInc / fast-path inline RefDec (perf)
- **Plan doc**: `explorations/plan-refcount-inlining.md` (DRAFT).
- `rt.RefInc` and `rt.RefDec` are the hottest paths in any
  non-trivial Binate program — the compiler emits one at almost
  every managed-pointer assignment, struct-field copy with managed
  references, scope exit, and function-arg pass-by-value of managed
  types. Each lowers to a `call void @bn_rt__RefInc(...)` (or
  `RefDec`), and the call setup costs more than the work itself.
- **Approach (hybrid, preferred)**: inline RefInc fully (load + add
  + store + nil-check, ~5 instructions) and inline RefDec's fast
  path (load + dec + store + branch on zero). The slow path
  (refcount reached zero → run dtor + Free) calls out to a thin
  runtime helper since that path is rare per allocation.
- **Backend strategy**: keep `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC`
  as single IR ops; each backend (LLVM / VM / native arm64)
  lowers them inline with its own native-optimal sequence. The
  duplication is small (~10-15 lines per backend) and avoids
  bloating the IR.
- **Slow-path helper name** is open (bikeshed candidates:
  `OnZeroRef`, `ZeroRefDestroy`, `RefDecSlow`, `Destroy`, or a
  compiler-internal magic name).
- **User-visible impact**: none. RefInc/RefDec call sites are
  compiler-emitted, not user-written.
- See `plan-refcount-inlining.md` for backend specifics, phasing,
  and open questions.

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

### Compiler bug: `-O2` / `-Og` build fails to link (undefined dtor symbol)
- **Repro** (2026-04-29): build gen1 with optimization, e.g.
  `bnc -g --cflag -O2 --cflag -fno-omit-frame-pointer -o gen1_bnc cmd/bnc`
  (or `--cflag -Og` instead of `-O2`). Plain `-g` (no `-O`)
  links fine. Failure is at the final `clang` link step:
  ```
  Undefined symbols for architecture arm64:
    "_bn_types____dtor_CheckError", referenced from:
        _bn_main__typecheckAll in main.o
        _bn_main__typecheckPackages in main.o
  ```
- **Surfaced by**: profiling work (see
  `notes-profiling-bnc-2026-04-29.md`). Profile had to fall
  back to `-O0`, which biases the picture toward refcount /
  bounds / header-access helpers that would inline at `-O2`.
  Fixing this unlocks a much truer profile baseline.
- **Likely cause** (unverified): the dtor for
  `pkg/types.CheckError` is emitted with linkage that allows
  the LLVM optimizer's GlobalDCE pass to drop it as
  internally-unused, even though it's referenced from another
  compilation unit. Other backends / runtime hooks that drop
  emitted symbols at higher opt levels are a likely culprit
  (e.g., `linkonce_odr` without an `available_externally`
  fallback, or `internal` linkage where it should be `external`
  / `weak`).
- **Investigation steps**:
  - Run `clang -O2 -c types.ll -o types.o` and check
    `nm types.o` for `_bn_types____dtor_CheckError` — is it
    defined, defined-and-stripped-by-O2, or never emitted?
  - Compare the LLVM IR `define` line for the dtor at `-O0`
    vs. inspecting it after `opt -O2` would be applied. Is the
    linkage attribute the issue?
  - Look at how other dtors (e.g. `bn_buf____dtor_CharBuf`)
    survive — they show up fine in the `-g`-only build. What's
    different about `CheckError` vs. `CharBuf`?
- **Workarounds**: build at `-O0` (no `-O` flag) for now. All
  existing CI / test paths use `-O0`, so nothing's broken in
  practice; the issue only surfaces if someone tries to build
  with optimization for performance work.

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan doc**: `explorations/plan-function-values.md` (DRAFT — see
  for representation, phasing, and open questions).
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** Per-signature
    trampolines that bridge compiled ↔ VM. Builds on Phase 1's
    vtable layout. Unlocks the broader interop work. Doesn't
    require Phase 2 — package descriptors expose non-capturing
    exports.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### Interface syntax revision — *Stringer / @Stringer + top-level decl
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (DRAFT — pending review).
- **Scope**: revise the IN-PROGRESS interface design in
  `claude-notes.md` § "Interfaces" before any of it ships. Three
  shifts:
  1. Raw / managed forms become `*Stringer` / `@Stringer`
     (mirroring the slice migration). Bare `Stringer` is no
     longer a usable type — only a referenceable interface name.
  2. Top-level `interface Foo { ... }` declaration form replaces
     `type Foo interface { ... }`. Anonymous interface type
     expressions are dropped entirely.
  3. Interface aliasing: `interface MyStringer = Stringer` (or
     possibly `type MyStringer = Stringer` — open in the plan).
- **Why**: same UAF-prevention argument as the slice migration —
  forcing the explicit raw-vs-managed choice prevents the "I
  thought it was managed" failure mode. Interfaces aren't types
  in this model; they're named contracts referenced via `*Iface`
  / `@Iface` / `impl T : Iface`.
- **No frontend dependency on function values**, and vice versa.
  Either can land first.
- **Backend**: vtable machinery (per-(impl, interface) static
  tables, vtable-indirect dispatch, cross-mode trampoline path)
  is shared with function values — building it once serves both.

### ~~Free-function pointer in managed-allocation header — bug~~ — DONE
- `pkg/rt/rt.bn` defines a 2-word managed-allocation header
  `{refcount, free_fn}`. The free_fn slot is now populated by
  `Alloc` (with `&rt.RawFree`) and read by `Free`, which dispatches
  indirect through it via the new `_call_free_fn` magic helper
  (parallel to `_call_dtor`, same OP_CALL_INDIRECT lowering). Each
  rt impl plugs in *its own* RawFree without Free needing to know.
- The runtime's C-side `managed_alloc` helper (used by
  `cstr_to_managed_slice` etc.) was updated to set
  `header[1] = &bn_rt__RawFree`, keeping C-created managed
  allocations consistent with rt.Alloc-created ones.
- **Cross-mode caveat (unchanged from prior state)**: works within
  a single mode (compiled-side allocation freed compiled-side; VM-
  side allocated freed VM-side). Cross-mode allocation+free still
  requires per-signature trampolines (function values Phase 3) to
  translate header[1] between the C-pointer and VM-function-index
  conventions. No regression vs. before — pre-fix Free silently
  used libc.Free regardless of origin.
- **Sub-task that landed alongside**: a new compiler-internal
  builtin `_raw_func_addr(funcRef)` returning the raw function
  address as `*uint8`. Underscore-prefixed because it isn't a
  permanent language feature — when function values land, the
  canonical spelling will accept a function value and extract the
  underlying call slot. Used by Alloc to populate header[1].
- **Prelim layering fix**: Alloc now routes through RawAlloc and
  MemZero rather than calling libc.Malloc / libc.Memset directly,
  so a non-libc pkg/rt impl can plug in its own raw-memory layer.
- **Commits**: `eda5941` (Alloc → RawAlloc+MemZero), `217f8bb`
  (`_raw_func_addr` builtin), `7b325eb` (header[1] populate+use).

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
- **Prerequisite**: function values (see `plan-function-values.md`). The
  descriptor's fields are pointers to functions — that's exactly what
  function values are. The 2-word `{vtable, data}` representation in the
  function-values plan is the substrate this needs. Phase 3 of that plan
  (cross-mode trampolines) is specifically the "VM side produces a
  descriptor whose fields are trampoline-shaped function values that
  dispatch back into the interpreter" piece of this work.
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
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL — start now, interpreter-only
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names and concrete steps):
  1. **Load-then-poke.** Load a `.bn` module the normal way; prompt
     accepts only immediate-mode entries. Each entry → synthetic
     `__repl_N()` → IR-gen → lower-one-function → call. Single-expr
     input auto-wrapped in `println(...)`. No new defs, no
     redefinition.
  2. **Add new top-level decls at the prompt.** Per-decl entry points
     in parser/types/ir/lower; append to current scope and `vm.Funcs`.
     Still no forward refs / no redefinition.
  3. **Forward references.** Pending-validation queue in the type
     checker.
  4. **Redefinition.** Replace path = body swap at existing idx (cheap).
     Shadow path = append + last-match `LookupFunc` semantics (or a
     REPL-side name table layered atop).
  5. **Mid-session imports.** Loader entry point for "load this one
     package now."
- **What's free / "should-do-now-anyway"**:
  - The audit itself (this entry + the plan doc) — overlaps fully with
    the "verify rather than redesign" exercise the interop work already
    needs.
  - Per-decl entry points exposed opportunistically when the relevant
    code is touched for unrelated reasons.
  - Name → idx hash in `LookupFunc` (50-line change, removes the
    perf argument for ever baking idx into bytecode).
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (`layout-extraction-plan.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts**:
  - Top-level prompt grammar: bare expression vs. bare statement list
    vs. either? Suggested convention: input that parses as a single
    expression gets `println(...)`-wrapped; otherwise it's a statement
    list, run for side effects, no auto-print.
  - Error recovery: parse / type / runtime errors in immediate mode
    print and return to prompt; nothing in retained mode is affected.
  - Where pretty-printing lives.
  - Sentinel for "no result" (probably nothing).
  - Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### ~~Package path strategy (Phase 1)~~ — DONE (2026-04-28)
- Two-path resolution shipped: `BniPath` (`.bni` interfaces) and
  `ImplPath` (impl directories) are independently-searched, ordered
  lists. CLI surface: `-I` / `--interface-path` and `-L` / `--impl-path`
  on bnc, bni, bnlint, and the Go bootstrap. `--root <dir>` stays as
  sugar for "add to both paths."
- Stages 1–6 (loader split → per-tool CLI → drop deprecated `Roots`
  field) all landed across the binate + bootstrap repos. See
  [`plan-package-search-paths.md`](plan-package-search-paths.md) for
  the design and the per-stage commit table.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

### ~~CLI flag coherence~~ — DONE (2026-04-28, alongside Stage 1–6)
- Standardized on `--word` for long flags across bnc, bni, bnlint,
  bootstrap. Existing single-dash long flags (`-root`, `-add-root`,
  `-verbose`, `-test`, `-cpuprofile`) stay accepted as back-compat
  aliases. Single `-` is reserved for short flags (`-v`, `-I`, `-L`),
  including future combinable `-abc`-style.

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- A first stub lives at `e2e/split-paths.sh` (covers Stage 1–6 of
  the package-search-paths plan): sets up a fixture where
  `pkg/splitlib`'s `.bni` is in one root and impl is in another,
  then invokes each tool with `-I` and `-L` and verifies output.
- **Unique challenges this dir has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet.
  - **Where to live in CI.** `e2e/` exists but has no runner script
    or CI hookup. Decide: a single `e2e/run.sh` that picks up scripts
    by convention? Per-feature scripts invoked individually? Wired
    into existing matrix or a separate one?
- **Why split-paths is a useful motivating example:** the `-I`/`-L`
  feature is something `bootstrap`, `bnc`, `bni`, and `bnlint`
  should all support **identically** — a deliberate cross-tool
  contract. e2e is the only layer where that contract can be
  observed directly. Most other features (codegen, type checker,
  IR) are tool-internal and conformance covers them; CLI/loader
  contracts span tools and need their own layer.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec the e2e/split-paths.sh fixture validates.

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
