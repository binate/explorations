# Binate TODO — Done

Items moved from [claude-todo.md](claude-todo.md) once fully complete. Active work lives there.

---

## Done

### ~~amd64 native backend: aggregate argument passing unimplemented~~ — FIXED 2026-05-27 (binate `f7a182b`, `b719d7e`)
- **Was**: under `builder-comp_native_x64_darwin-comp_native_x64_darwin`
  (the new local Rosetta runner), `002_arithmetic` and most tests
  miscompiled — garbage output + `runtime error: index out of bounds`.
  `pkg/native/amd64/amd64_call.bn::emitCall` explicitly skipped
  aggregate args (`if common.IsAggregateTyp(arg.Typ) { continue }`),
  so e.g. `bootstrap.formatInt(int, *[]uint8)`'s raw-slice arg (a 2-
  eightbyte `%BnSlice`) left RSI/RDX undefined.  Discovery surfaced a
  separate MAJOR latent bug in the shared CallConv (see next entry).
- **Fix landed in two commits**:
  1. `f7a182b` — `emitAggregateArg` handles the SysV INTEGER-eightbyte
     in-register case (≤ 16 B aggregate fitting in remaining GP arg
     regs).  Loads each eightbyte from the aggregate's storage into
     `argReg(regStart + w)`.  Conformance on builder-comp_native_-
     x64_darwin: 0/428 → 103/428.
  2. `b719d7e` — together with the CallConv classifier fix, the
     MEMORY-class path (> 16 B aggregates passed entirely on the
     stack) is now also emitted: each word is loaded into RAX and
     stored to `[rsp + stackOff + 8*w]`.  RAX is the load-shuttle
     scratch (not in regPool, not a GP arg register, dead pre-CALL).
- **Tests** (in `pkg/native/amd64/amd64_call_test.bn`):
  - `TestEmitCallAggregateArgLoadsTwoEightbytes`: 16 B aggregate as
    arg 1 after a scalar → 2 MOV-from-mem loads + LEA + CALL.
  - `TestEmitCallAggregateArgFirstSlot`: 16 B aggregate as arg 0
    (regStart = 0) — no off-by-one in `argReg(regStart + w)`.
  - `TestEmitCallAggregateArgOver16OnStack`: 32 B managed-slice →
    ≥ 4 MOV-from-mem loads + ≥ 4 MOV-to-mem stores.
- **What's still amd64-specific work** (separate gaps, not aggregate
  arg passing): many remaining x64_darwin failures (103/429) sit on
  non-aggregate-arg issues — e.g., `101_println_managed_chars` prints
  empty (likely OP_LOAD-of-aggregate or runtime crash), `003_variables`
  prints `0` for `var x int = 10` (OP_LOAD-of-int chain).  Float-arg
  passing (XMM regs) is also still unimplemented.

### ~~MAJOR: shared SysV CallConv mis-models aggregate-arg dispatch~~ — FIXED 2026-05-27 (binate `b719d7e`)
- **Was**: `pkg/native/common/common_callconv.bn` modeled SysV-AMD64
  aggregate dispatch with AAPCS-style register/stack *splitting*, but
  real SysV/x86_64 (and LLVM's by-value lowering of structs on x86_64)
  classifies any aggregate larger than `AggregateInRegMax` (= 16) as
  MEMORY class — passed *entirely* on the stack, never split, NGRN
  unchanged.  And a ≤ 16 B aggregate that doesn't fit in the remaining
  regs *also* goes wholly to MEMORY under SysV (no split).
  `TestCallArgStackOffSysVSplit` deliberately pinned the wrong split
  shape.  Implementation against the wrong classifier would silently
  miscompile every managed-slice / > 16 B-struct argument crossing
  the native↔LLVM boundary.
- **Fix**: new `CallConv.SplitAggregates bool` field (AAPCS64 = true,
  SysV_AMD64 = false).  The three dispatch helpers (`CallArgRegStart`,
  `CallArgStackOff`, `CallStackBytes`) are rewritten to consume a
  shared per-arg classifier `argRegWordsStackWords` gated by the
  flag.  AAPCS path byte-identical (arm64 conformance 427/0
  unchanged); SysV now classifies > 16 B aggregates as MEMORY and
  ≤ 16 B aggregates that don't fit as MEMORY too, with NGRN unchanged
  so later args still take remaining GP regs.
- **Tests** (in `pkg/native/common/common_callconv_test.bn`):
  - Replaced `TestCallArgStackOffSysVSplit` with three positive SysV
    tests: 32 B managed-slice → MEMORY; 16 B raw-slice-doesn't-fit
    → MEMORY with NGRN preserved (trailing scalar takes the still-
    free reg); 16 B raw-slice-fits → all-in-regs at the right
    regStart.
  - Added `TestCallArgStackOffAapcs64SplitUnchanged` pinning that
    AAPCS64 still splits as before.
  - Constructor smoke tests now assert the `SplitAggregates` field.

### ~~Generic call type args reject `@T` / `@[]T` / `*[]T` (parser)~~ — FIXED 2026-05-26 (binate `18b8047`)
- **Was CRITICAL.** Expression-position generic instantiation with a
  managed-pointer (`f[@T](...)`), managed-slice (`f[@[]T](...)`), or
  raw-slice (`f[*[]T](...)`) type argument failed to **parse** —
  `parseIndexOrSlice` parsed the `f[...]` bracket contents as
  expressions (to share the `arr[i]` index path), and those three
  forms have no expression spelling.  Only bare names and `*T`
  survived (ident / unary-deref).  Blocked the appendXxxPtr →
  `slices.Append[@T]` migration (the bulk of the helpers append
  managed pointers).
- **Fix**: a new `EXPR_TYPE` Expr node wrapping a parsed `TypeExpr`,
  threaded through four layers.  Parser (`parse_expr.bn`):
  `startsBracketTypeArg` detects a bracket element with no expression
  spelling — `@…` or `*[` — and `parseBracketTypeArg` parses it via
  `parseType`, wrapping in `EXPR_TYPE`; `*T` / `*p` still flow through
  `parseExpr` (only `*[` routes to the type parser, so `arr[*p]` stays
  an index).  Type checker (`typeArgFromExpr`) resolves `EXPR_TYPE`'s
  `TypeRef`; `checkExpr` errors cleanly if one reaches value position.
  IR-gen (`exprToTypeExpr`) hands the `TypeRef` straight through.
- **Coverage**: `pkg/parser` units (`@T` / `@[]T` / `*[]T` / mixed
  `f[int,@T]` / `arr[*p]`-stays-an-index); conformance 492 (end-to-end
  over the three forms, all modes) + 493 (type-arg-in-value-position
  rejection); `pkg/slices` `Append[@Thing]` test restored.  Green
  across builder-comp / -int / -comp.
- **Build ladder**: BUILDER bnc-0.0.2 still has the bug (it predates
  the fix); a future bnc-0.0.3 cut from a post-fix tree is needed
  before `slices.Append[@T]` can be used *inside* cmd/bnc's own
  (BUILDER-compilable) tree.  See version-history.md.

### ~~arm32_linux unit tests SEGV at startup — C-extern struct-return sret threshold was LP64-only~~ — FIXED 2026-05-25 (binate `4874fe6`)
- **Symptom**: every `builder-comp_arm32_linux` unit-test binary
  SEGV'd at startup (0 passed / 33 failed), while
  `builder-comp_arm32_linux` *conformance* was fully green.  The
  distinguishing factor: the synthetic unit-test runner calls
  `bootstrap.Args()` at startup (to parse `--run`), and no
  conformance test calls a struct-returning C extern.
- **Root cause**: `pkg/codegen/emit_types.bn:needsSret` hardcoded
  the LP64 rule "C-extern struct return > 16 bytes → sret".  On
  arm-linux-gnueabihf the AAPCS32 rule is "> 4 bytes → sret"
  (verified against clang: an 8-byte struct gets `sret`, a 4-byte
  one returns in r0).  `bootstrap.Args()` returns a 16-byte
  `BnManagedSlice`; clang's C side (binate_runtime.c) used sret,
  but the Binate caller — seeing 16 ≯ 16 — emitted a register-
  return call.  The conventions diverged and the returned slice
  was read from the wrong place: `len(bootstrap.Args())` came back
  as garbage (0x41000004), so the runner crashed before running a
  single test.
- **Fix**: `needsSret` picks the threshold from the target's
  pointer size — 4 bytes for ILP32, 16 for LP64.  Only consulted
  for `IsCExtern` returns, so Binate-internal struct returns
  (consistent on both sides) and all LP64 codegen are untouched.
- **Isolated reproducer**: `conformance/487_bootstrap_args`
  (`len(bootstrap.Args())`), which failed on arm32_linux pre-fix
  and now passes across host (LP64) + arm32 modes — the
  cross-mode regression guard.
- **After-fix state**: arm32_linux conformance 417/0; unit tests
  0→19 passing.  The remaining 14 unit-test package failures are
  the same 32-bit-target categories tracked below (filesystem /
  native-host arch / int32-literal-fit), plus two genuine
  test-level failures still to investigate
  (`TestBinBufWriteU64LittleEndian`, `TestOrrImm`).

### ~~`(*p).x` (field access through explicit deref) returns 0 — bnc-compiled only~~ — FIXED 2026-05-21 (binate `5a5ffb1`)
- Root cause was as originally diagnosed: `genSelector`
  (`pkg/ir/gen_selector.bn`) had no EXPR_UNARY base case — IDENT /
  SELECTOR / INSTANTIATE_OR_INDEX / CALL / BUILTIN bases all routed
  to a real field-pointer; the explicit-deref `*p` form fell
  through to the `return b.EmitConstInt(0, types.TypInt())`
  fallback, so `(*p).x` read a constant 0.
- Fix: mirror the EXPR_CALL pattern.  `genExpr` the operand (`*p`);
  the resulting `val` carries either a struct value (when `*p`
  loads a `T`), a managed-pointer-to-struct (when it loads `@T`),
  or a raw-pointer-to-struct (when it loads `*T`).  Each routes
  through the existing field-pointer + load logic.  The struct-
  value branch alloca+stores the loaded value (you can't GEP
  through an SSA value), mirroring the EXPR_CALL value-struct arm.
- Relies on the deref-typing extension from Slice P.2
  (`pkg/ir/gen_expr.bn` sizes `*p` loads by the operand's `Elem`
  for both raw and managed pointers), so `val.Typ` is the pointee
  rather than `i64`.
- **Pins**: conformance `456_field_access_through_explicit_deref`
  (was rejection-pinned with `.xfail` markers in all six bnc-
  compiled modes; flipped to `.expected` 42 in the fix commit) plus
  IR-layer unit tests `TestGenExplicitDerefRawPtrFieldRead` /
  `TestGenExplicitDerefManagedPtrFieldRead` in
  `pkg/ir/gen_selector_test.bn` (each asserts a `GET_FIELD_PTR` is
  emitted rather than the const-0 fallback).

### ~~Phase 4: aa64 native backend missing OP_FUNC_HANDLE / OP_CALL_HANDLE handlers~~ — FIXED 2026-05-24 (binate `9d23198`)
- `builder-comp_native_aa64-comp_native_aa64`: 2/413/1 → 415/0/1.
- Three changes in `pkg/native/arm64`: new LLVM-shape name helpers
  (`handleSymFor`, `vtableSymForLLVM`, `shimSymForLLVM`) in
  `arm64_names.bn`; OP_FUNC_HANDLE + OP_CALL_HANDLE dispatch
  handlers in `arm64_dispatch.bn` (handle is ADRP+ADD against
  `___handle.<mangled>`, call delegates to `emitCallFuncValue`);
  `collectFuncValueRefs` extended to OP_FUNC_HANDLE filtered
  local-only via new `lookupFuncValueTypeAA64`, and
  `emitFuncValueVtables` emits a weak `___handle.<mangled>` per
  local entry whose vtable_ptr slot points at the existing
  aa64-style vtable.
- Cross-rebase note: `807a9bf` (concurrent) removed OP_FUNC_ADDR
  entirely (Phase 4 left it dead), so the eventual landed shape
  handles only OP_FUNC_HANDLE / OP_CALL_HANDLE.
- The previous attempt's duplicate-symbol pitfall is avoided by
  the local-only filter in `lookupFuncValueTypeAA64`: cross-
  module references resolve at link time to the LLVM-emitted
  dep's weak_odr definition; we never emit a competing one.

### ~~Phase 4 (uniform native fn ptrs) — finish: dtor refs MUST move from idx to handle~~ — DONE 2026-05-23 (binate `f3d9436`)
- emitManagedPtrRefDec now emits OP_FUNC_HANDLE end-to-end (handle
  pointer in both native and bytecode), and BC_REFDEC_INLINE_FAST's
  slow path inspects `handle.data`:
  * `DATA_KIND_VM_CLOSURE_REC` → recover FnIdx from
    `closureRec[2]`, push the dtor frame on vm.Stack with ptr
    stashed in `freeOnPop`, jump.  BC_RETURN pops the frame and
    frees ptr.  No host C-stack recursion through the dtor field
    graph — the iterative win the earlier stop-gap was protecting.
  * Otherwise (data is null, or future kinds like
    DATA_KIND_COMPILED_CLOSURE) → load handle.vtable.call (the
    per-function shim) and dispatch via
    `rt._call_shim_scalar(shim, data, ptr, ...)`.  Cross-mode
    call — takes a host frame but cannot recurse back into the
    bytecode VM, so depth is bounded by the cross-mode call
    chain.  After the shim returns, `rt.Free(ptr)`.
- Cross-mode interop now works as Phase 4 intended: a managed
  value created in native that crosses into a bytecode VM (or
  vice-versa) resolves its dtor through the shared handle layout
  instead of an intra-vm-only function index.
- Follow-up retired in binate `807a9bf` (2026-05-24): emitIndirectCall
  was renamed to emitDtorOrCopyCall and now takes a name string
  directly (no throw-away EmitFuncAddr Instr), so `OP_FUNC_ADDR` /
  `BC_FUNC_ADDR` had no producer left and were deleted from the IR
  + bytecode + LLVM + aa64 surface in one pass.
- Follow-up retired in binate `aab30cf` (2026-05-24):
  `ExternBinding.RawFnAddr` (raw int handle pointer — latent UAF
  for heap-allocated source handles) → managed `@VMFuncHandle
  HandleAddr` that RegisterExtern populates with a binding-owned
  copy.  Ownership test pinned in `24fb091`.
- Phase 4 plan doc (`plan-uniform-native-fnptrs.md`) updated to
  mark Phase 4 LANDED in binate `42f463f`.
- **Original context** (kept for posterity): Phase 4 landed at
  binate `666ecc0` with a stop-gap (emitManagedPtrRefDec emits
  OP_FUNC_ADDR; BC_REFDEC reads Src2 as 1-based intra-vm idx) to
  fix `builder-comp-int-int` stack overflow.  Reverted in
  `f3d9436` with the proper handle-pointer kind-discriminating
  design above.

### ~~Native aa64 backend: managed-pointer-to-iv deref segfaults at dispatch~~ — FIXED 2026-05-22
- Root cause: `pkg/native/arm64/arm64_emit.bn:emitBox` silently
  returned for non-OP_ALLOC operands, so `box(iv)` for a loaded
  iv (the way to construct `@(*I)` / `@(@I)`) never emitted the
  `bn_rt__Box` call — `p` (the @-pointer) stayed uninitialized
  and downstream dispatch chased a stack alias instead of the
  heap iv.
- Fix: aggregate-load branch in `emitBox` — `getOperand` already
  returns a register holding the pointer to the data (per
  `common.SpillHoldsAggregatePointer`); pass it directly to
  `bn_rt__Box`.  Mirrors LLVM's `emitBoxInstr` non-OP_ALLOC arm.
- Conformance 444 / 445 / 450 / 458 flipped from xfail to pass
  on `builder-comp_native_aa64-comp_native_aa64` (binate 01bb5b6).

### ~~IR-gen: large literals force i64 in narrow-context operations~~ — FIXED
- The context-driven literal type resolution that was the proper
  fix has landed via `plan-ir-gen-typed-literals.md` Phases A/B:
  the type checker now resolves a literal's type from context
  (var-decl LHS, binop operand type, etc.) before IR-gen sees it,
  so `0xFFFFFFFF` in a uint32 context lands at TYP_INT
  Width=32 unsigned directly — no int64 promotion, no widening
  ripple.
- Pinned by `pkg/ir/gen_expr_test.bn`:
  `TestGenVarDeclUint32LiteralStaysUint32`,
  `TestGenBinopLiteralLhsAdoptsRhsType`,
  `TestGenUint32MaskLiteralNarrowsToUint32`, plus
  `TestGenNarrowIntLitStaysUntyped` for the inverse (narrow
  literals retain TYP_UNTYPED_INT for further inference).
- End-to-end probe: `func ror32(x, n uint32) uint32 { return (x >> n
  | x << (32-n)) & 0xFFFFFFFF }` compiles and runs cleanly on both
  host (builder-comp) and arm32-baremetal (cross-compile) — no
  "ret i64 in i32-result function" mismatch.  Verified 2026-05-24.

### ~~Substitute LP64-pinned conformance tests with target-aware variants~~ — DONE 2026-05-22
- **Mechanism**: `conformance/run.sh` now honors per-mode
  `NNN_name.expected.<mode>` (and `.error.<mode>`) overrides,
  mirroring the `.xfail.<mode>` convention.  See binate 39bac8a.
- **Tests retired**: 290 (override) and 330 (rewritten to
  `bit_cast(int64, ...)`).  Both xfail.builder-comp_arm32_-
  baremetal markers gone.  See binate 0044cde.
- Approach for future arm32-broken tests: either drop in
  an `.expected.<mode>` override (option 1) or rewrite the .bn
  to be target-agnostic (option 3).  The substitution-syntax
  option (option 2) wasn't needed.

### ~~`println(int64)` hangs on arm32-baremetal~~ — FIXED 2026-05-22
- Diagnosis was on the right track (int64 codegen on ILP32) but
  wrong about the AEABI helper.  The actual fixes landed on main
  in three coupled commits: `c2f8501` routes `println(int64)`
  through `bootstrap.formatInt64` (emitPrintInt was previously
  truncating int64 args to the target's `int` via a cast to
  formatInt's declared param type); `d5195f0` types int64-magnitude
  integer literals as int64 (TYP_UNTYPED_INT was lowering via
  llvmType to i32 on arm32, silently truncating wide constants)
  and preserves int64 width through unary-minus; `38f9319` fixes
  formatInt's int-min handling and gives 424 an arm32 .expected
  override.
- 330 now passes in `builder-comp_arm32_baremetal` with no xfail.

### ~~bnc: function-call element inside `@[]@[]char{...}` composite literal stores wrong value~~ — FIXED 2026-05-23
- **Was**: `var a @[]@[]char = @[]@[]char{buf.CopyStr("libc")}`
  compiled fine but at runtime `a[0]` was empty (len=0).  Cause:
  `pkg/ir/gen_access.bn:genManagedSliceLit` stored each element
  without a refcount handoff, so a fresh managed value from a
  call (registered as a temp by `gen_call.bn`) got RefDec'd by
  the end-of-statement temp cleanup — leaving the slot with a
  dangling data ptr + freed header.
- **Fix**: mirror `gen_short_var.bn`'s `var x = …` handoff
  pattern in `genManagedSliceLit` — for managed-ptr / managed-
  slice element types, `consumeTemp` if `isFreshManagedPtr` /
  `isFreshManagedSlice` (slot inherits the temp's refcount),
  otherwise `EmitRefInc` / `emitManagedSliceRefInc` (slot takes
  its own reference).
- **Pinned by**: conformance/473_mslice_mslice_char_lit_call_elem
  (output check on `@[]@[]char{copyStr("a"), copyStr("b")}`).

### ~~bnc: `return ""` for `@[]char` leaves undeclared `bn_libc__Memcpy`~~ — FIXED
- **Surfaced by**: adding `--test --run <substr>` to `cmd/bnc`'s
  generated test runner (`21c03a4`).  The generator wanted
  `func _runnerFilter() @[]char { ...; return "" }`; the bnc codegen
  lowered the `""` exit-path literal to
  `call void @bn_libc__Memcpy(%dst, %src, i64 0)` (size-0 memcpy
  to copy zero bytes from a rodata placeholder into a freshly
  `rt.MakeManagedSlice`'d 0-length buffer).  The generated runner
  module imports `pkg/bootstrap` + the test packages — but not
  `pkg/libc` directly — so `test_main.ll` has no
  `declare … @bn_libc__Memcpy` and clang errors with
  `use of undefined value '@bn_libc__Memcpy'`.
- **Workaround in place**: the generator returns a zero-init local
  (`var empty @[]char; … return empty`) instead of `""`.  See
  `genTestRunner` in `cmd/bnc/test.bn` and the comment block above
  the `_runnerFilter` emission.
- **Two clean fixes**:
  1. In codegen, when lowering a `""` literal for `@[]char`, skip
     the `libc.Memcpy` emit when the size is statically zero (no
     bytes to copy — the `rt.MakeManagedSlice` already produced an
     empty backing).  Plausibly the right call regardless of this bug.
  2. Or: emit a `declare void @bn_libc__Memcpy(i8*, i8*, i64)` (and
     similar implicit-use declarations) into every module that calls
     into them through string-literal lowering, regardless of whether
     `pkg/libc` is in the import set.
- **Repro after removing the workaround**:
    1. Revert the `var empty` branch in `genTestRunner` back to
       `return ""`.
    2. `go run cmd/bnc -- --test --build-dir <tmp> cmd/bni` — clang
       fails on `test_main.ll` with the undefined-value error.
  Test would live in `pkg/codegen` (a minimal module with a single
  `@[]char`-returning function that does `return ""`).  Not yet
  added — recommend adding alongside fix (1).

### ~~pkg/vm: VMFunc.Vtable / VMClosureRec lazy allocs leak on VMFunc death~~ — FIXED
- VMFunc's lazy heap blocks moved from raw `int` slots (filled via
  `rt.RawAlloc`) to managed struct types: `VMFuncVtable`,
  `VMClosureRec`, and the new `VMFuncHandle` (a 16-byte
  `{VtableAddr, DataAddr}` block matching the `@__handle.F` static
  shape so dispatch is uniform between bytecode-only and natively-
  compiled functions).  `VMFunc.Vtable` / `ClosureRec` / `Handle`
  are now `@VMFuncVtable` / `@VMClosureRec` / `@VMFuncHandle`
  fields, allocated via `make(...)` in `vm_exec_funcref.bn:ensureHandle`.
  VMFunc's auto-emitted dtor refdec's all three on death; no leak.
- Same fix for `ExternBinding.HandleAddr` — now `@VMFuncHandle`.
- Phase 4 of `plan-uniform-native-fnptrs.md` is the umbrella that
  carried this in (along with the dtor-handle interop fix and the
  aa64 handler additions).

### ~~pkg/vm:TestExecRefIncRefDecInline crashes under boot-comp-int-int~~ — FIXED
- Phases 1–3 of `plan-uniform-native-fnptrs.md` landed
  (`9561a3b`, `c557870`).  Pre-existing diagnostic detail retained
  below for context.
- **Repro**: `./scripts/unittest/run.sh boot-comp-int-int pkg/vm`.
  Symptom is actually a **SIGSEGV** (exit 139), not a hang —
  earlier "hang past 8 min" reports were the runner timing out
  on the segfaulted child.  xfail marker:
  `scripts/unittest/pkg-vm.xfail.boot-comp-int-int`.
- **Shape**: three-level VM nesting.  OUTER cmd/bni native dispatches
  the inner cmd/bni's bytecode (the unit-test harness); the test
  creates a fresh VM_test via `vm.NewVM(...)` and runs a hand-built
  IR module — `EmitMake → EmitRefInc → EmitRefDec (rc=1, fast
  path) → BC_CALL "rt.Refcount" → EmitRefDec (rc=0, slow path) →
  BC_RETURN`.
- **Bisection** (variant-by-variant build of the IR module):
    - `EmitMake` (BC_ALLOC) alone — ✅ returns.
    - `EmitMake + EmitRefInc` — ✅ returns.
    - `EmitMake + EmitRefInc + EmitRefDec(fast)` — ✅ returns.
    - `+ BC_CALL "rt.Refcount"` — ❌ crashes.
  So the trigger is the BC_CALL extern dispatch on a name that's
  not in VM_test.Funcs but IS in VM_test.Externs (registered via
  RegisterStandardExterns).
- **Specific to 3-level nesting.**  pkg/vm passes 107/107 under
  boot-comp-int (2-level): TestExecRefIncRefDecInline runs cleanly
  there.  The crash only manifests in the deeper boot-comp-int-int
  chain.
- **Crash details (2026-05-12 via lldb on `/tmp/bni_dbg` built with
  `-g`)**:
    - `EXC_BAD_ACCESS (code=1, address=0x1)` in OUTER native
      `bn_vm__execMemoryOp` at line 251 — the BC_LOAD8 handler's
      `regs[instr.Dst] = cast(int, p[0])`.
    - The BC_LOAD8 being processed lives in
      `VM_INNER.Funcs[1068].Code[97]` (= `vm.execMemoryOp`'s OWN
      bytecode); pc=98 (one past). Instruction is
      `(Op=43, Dst=78, Src1=77, Imm=0)`.
    - vm.execMemoryOp's register 77 holds `0x01`. Bytecode at
      pc=95/96/97: `BC_LOAD_IMM R76, 0` → `BC_ELEM_PTR R77 = R75
      + R76*1` → `BC_LOAD8 R78 = *R77`. This corresponds to the
      source-level `cast(int, p[0])` where `p = bit_cast(*uint8,
      regs[instr.Src1])`. So source-level `p == 0x01` —
      vm.execMemoryOp was called with a `regs+instr` pair where
      `regs[instr.Src1] == 1`.
    - Caller of execMemoryOp (saved in execMemoryOp's frame
      header): funcIdx=1060 (= `vm.execLoop`) at saved pc=185.
      Caller of that inner execLoop (savedFuncIdx=1064) at pc=91.
      The inner execLoop's parameters at regsOff=12368 are
      reg[0]=0xAF079D310 (vm), reg[1]=1032 (funcIdx), reg[2]=1168
      (regsOff).
    - The inner execLoop's `vm` (0xAF079D310) is NOT the
      VM_INNER_CMD_BNI (0xAF0B58510) we entered through — so we're
      at the deeper-nested level (probably the test's
      `execFunc(VM_T, ...)` → execLoop call, with vm=VM_T).
      Unresolved discrepancy: funcIdx=1032 is way out of range for
      a VM_T that LowerModule populated with one function. So
      either the inner execLoop is iterating something other than
      VM_T (some intermediate VM?), or our register-offset
      assumption for params (reg[0..2]) is off.
- **Root cause (2026-05-13, confirmed via lldb on `/tmp/bni_dbg`
  with `--run TestExecRefIncRefDecInline`)**: vtable.call slot for
  every `rt.*` extern binding in `VM_T.Externs` is stored as
  `0x423` (= 1059), a tiny integer that isn't a native function
  pointer.  By contrast `libc.*` / `bootstrap.*` bindings have
  proper native call slots (e.g. `0x10010d4d4`).
- **Dispatch path that crashes**: `dispatchExternBinding` reads
  `vtable[1] = 1059` and feeds it into `rt._call_shim_scalar` →
  `BC_CALL_INDIRECT` with `fnIdx=1059`.  The handler in inner
  `pkg/vm.execLoop` does `calleeFuncIdx = fnIdx - 1 = 1058`,
  passes the `1058 < len(vm.Funcs)` check (`INNER vm.Funcs.len`
  = 1194), and pushes a frame for `vm.Funcs[1058]` — which is
  `vm.genModule` (a `vm_test.bn` helper).  genModule's first
  action is `toBytes(src)`, which dereferences `src.data`; src
  is actually the closure record passed as `dataPtr` (=
  `b.DataAddr`), whose word 0 is `rt.DATA_KIND_VM_CLOSURE_REC =
  1`.  Reading the byte at address `0x1` segfaults — exit 139.
  (Also explains the 44 GB memory blow-up the user observed when
  leaving the test running: genModule continues past toBytes
  into `parser.New / ParseFile` parsing the closure record as
  Binate source — unbounded allocation.)
- **Why vtable.call is the wrong number (cross-VM index leak)**:
  BC_FUNC_VALUE construction (Path B in
  `pkg/vm/vm_exec_funcref.bn:99-107`) sets
  `vtPtr[1] = bit_cast(int, _raw_func_addr(TrampolineScalar))`.
  `_raw_func_addr` lowers to BC_FUNC_ADDR.  When INNER
  pkg/vm.execLoop's bytecode dispatches BC_FUNC_VALUE, it
  source-level-calls `execFuncRefOp(vm=INNER vm, …)`.  But
  execFuncRefOp's BYTECODE (which contains the BC_FUNC_ADDR)
  is then iterated by OUTER NATIVE execLoop (one level up the
  call ladder).  OUTER native execLoop's BC_FUNC_ADDR handler
  uses OUTER's `vm` = VM_INNER_CMD_BNI for the LookupFunc, not
  the inner level's vm.  OUTER_vm.LookupFunc("vm.TrampolineScalar")
  = 1058, so `vtPtr[1] = 1059`.
- **Both directly verified via lldb**:
    - `INNER vm.LookupFunc("vm.TrampolineScalar")` = 1076,
      `INNER vm.Funcs[1076].Name = "vm.TrampolineScalar"`,
      `INNER vm.Funcs[1058].Name = "vm.genModule"`.
    - `execFuncRefOp.CallCache[22]` (the slot for the BC_FUNC_ADDR
      to TrampolineScalar) = 1076 in INNER vm.
    - But the actual stored `vtable[1]` for all rt.* externs
      registered in `VM_T.Externs` = 1059.
  So the construction came from a DIFFERENT execFuncRefOp execution
  context — namely the one iterated by OUTER NATIVE execLoop's
  handler chain.
- **Generalized bug shape**: any function-value vtable whose `call`
  slot is a 1-based VM index (Path B) is meaningful only in the
  vm at construction time.  In 3-level VM nesting, the vtable can
  be constructed by an upper-level execLoop and consumed by a
  lower-level execLoop, so the numeric index resolves to the
  wrong function.  Path A (extern registry fallback, libc.* /
  bootstrap.*) doesn't have this problem because vtables there
  hold native function pointers (immune to vm-context shifts).
- **Possible fixes (require user buy-in)**:
    1. Make Path B's `call` slot a NATIVE function pointer (the
       address of TrampolineScalar / TrampolineAggregate in the
       containing process).  In bytecode-mode VMs the index-based
       path goes away; BC_CALL_INDIRECT's `dispatchNativeIndirect`
       arm (Imm=8/9) takes over uniformly.  Cost: TrampolineScalar
       needs to be reachable as a native function from any vm
       depth — works if the outermost host is always native cmd/bni,
       which is the assumption.
    2. Store a vm-identity tag alongside the numeric index and
       translate at dispatch time.  More invasive.
    3. Re-resolve at first dispatch (lazy-translate the numeric
       call slot through dispatch-time vm.LookupFunc by Name).
       Requires keeping the symbol name in the vtable record.
- **Surfaced by** the boot-comp-int-int unit-test sweep after the
  vm_extern.bn cleanup (`a6a74c8`).  Pre-cleanup the test was
  hidden behind a separate codegen bug fixed in `666f2c9`.
- **Repro is now seconds**: `/tmp/bni_dbg -root <root> cmd/bni
  -- --test --run TestExecRefIncRefDecInline -root <root> pkg/vm`
  segfaults within ~2 s of launch (needs the `--run` filter from
  `6bea5ba`).
- **Investigation owner**: in progress.  Next concrete step:
  from lldb at the SEGV, call (or inline-script) the INNER vm's
  LookupFunc on `"vm.TrampolineScalar"` and compare against a
  linear scan of `INNER vm.Funcs[i].Name`.  If they disagree the
  bug is in funcIndex insertion / probing; if they agree at idx
  1058 the bug is in LowerModule's appendVMFunc / funcIndexSet
  pairing.

### ~~Pointers to interface values~~ — DONE 2026-05-21
- **Plan**: `plan-pointers-to-iface-values.md` (sliced P.1–P.5).
  Slices P.1 (audit) + P.2 (fix `@(*I)` / `@(@I)` deref-
  dispatch) LANDED 2026-05-20; P.3 (smoothing for pointer-to-iv
  receivers) + P.4 (iv-in-slice / iv-in-array element-write)
  LANDED 2026-05-21.  P.5 (bootstrap parity) DROPPED — boot
  mode is gone.
- Design pinned in `claude-notes.md` § "Interfaces" line 421:
  `**Stringer`, `*@Stringer`, `@(*Stringer)`, `@(@Stringer)` are
  all valid pointer-to-iv shapes; parens are required by the
  grammar to disambiguate the `@(@…)` form.
- **Conformance pins**: 408 + 443 + 444 + 445 cover
  `(*p).Foo()` dispatch through every shape; 438 + 452 + 453 +
  450 cover `p.Foo()` smoothing; 439 + 440 + 441 cover
  iv-in-slice / iv-in-array; 442 pins pointer-to-iv struct
  field; 456 pins the orthogonal `(*p).x` bnc-compiled bug
  still in `gen_selector.bn` (see entry above).
- Was needed for: generics (`*T` where `T=Stringer`), out
  parameters, arrays of interfaces, containers.

### ~~Test harness `isTestResultReturn` should resolve type aliases~~ — FIXED
- The test harnesses (bootstrap Go `main.go` and self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

### ~~Type-checker drops typed-const value through untyped binop fold~~ — FIXED 2026-05-23

- **Discovered + fixed**: 2026-05-23, while wiring up
  plan-ir-gen-typed-literals.md Phase A4 (consume the type
  checker's bignum fold from IR-gen).
- **Symptom**: when one operand of an untyped-arithmetic binop was
  an EXPR_IDENT referring to a bare iota-counted const (e.g.
  `keyword_start + 1` in pkg/token.bn:148, where `keyword_start`
  is declared inside `const ( ... Type = iota; ... ; keyword_start )`),
  the type checker treated the binop as foldable and wrote a
  result Type carrying `HasLitVal=true`, but `LitMag` on that
  result reflected only the untyped operand's value (the literal
  `1`), not the const's iota.  In effect the fold computed
  `0 + 1 = 1` instead of `iota_of_keyword_start + 1`.
- **Root cause**: `pkg/types/check_decl.bn:checkConstDecl` for a
  bare iota'd const called `defineConst(name, TypUntypedInt())` —
  the predeclared singleton, with no `HasLitVal` attached.  At
  reference time, `checkIdent` returned that LitVal-less
  singleton; `foldIntArith` bailed (lt.HasLitVal == false) and
  fell through to `commonType`, which returned the OTHER
  operand's Type — so the binop's resolved Type inherited the
  literal's LitVal.
- **Fix** (binate `7ced362` / main `936a904`): construct a fresh
  `TYP_UNTYPED_INT` Type with `HasLitVal=true` / `LitMag=c.Iota`
  / `LitSign=false` for each bare iota-counted const, via a new
  `makeUntypedIntWithLit` helper.  `foldIntArith` now folds
  correctly through typed-const operand references, and Phase A4
  drops its direct-literal gate.
- **Tests**: `TestConstFoldIotaConstPlusLiteralFits` +
  `TestConstFoldIotaConstPlusLiteralOverflows` in
  pkg/types/check_expr_constfold_test.bn pin the fix at the
  type-checker layer.  Bare-metal conformance ticks up from
  398 → 400 passes (two integration tests that depended on
  iota-arithmetic now compile + run cleanly).

### ~~Integer literals and constant expressions~~ — RATIFIED + IMPLEMENTED 2026-05-15..2026-05-18
- **Spec**: `claude-notes.md` § "Integer literal value range and
  constant-expression arithmetic — DECIDED 2026-05-15".
- **Slices** (all on self-hosted bnc; xfail.boot on the conformance
  tests since boot mode uses the Go bootstrap which doesn't run
  const-fold or fit-check — and that mirror is explicitly out of
  scope given the move toward bnc-as-builder):
  - **Slice 0** (`97115da`) — `pkg/bignum` (uint64 magnitude + sign;
    parse / arithmetic / fit-checks).
  - **Slice 1** (`d463bf0`) — EXPR_INT_LIT rejects literals whose
    magnitude exceeds `2^64-1` at parse time
    (`409_err_int_literal_overflow`).
  - **Slice 2** (`24ca04a`) — `TYP_UNTYPED_INT` carries `(LitMag,
    LitSign)` primitives on Type; EXPR_UNARY MINUS propagates with
    the sign flipped; AssignableTo enforces the fit-check, unwrapping
    `TYP_NAMED` / `TYP_ALIAS` / `TYP_CONST`
    (`419_err_int_fits_uint8`).
  - **Slice 3** (`df58bdd`) — `+ - *` on literal-bearing untyped-int
    operands fold at type-check (`421_const_fold_arith`,
    `418_err_const_fold_overflow`).
  - **Slice 4** (`bcfdc20`) — `& | ^ << >>` fold the same way
    (`424_const_fold_bitwise`); folders extracted to
    `pkg/types/check_expr_constfold.bn` along the file-length cap.
  - **Cleanup** (`72a0bac`, after `bootstrap/63a8889` fixed the
    uint64-as-int64 bug) — drop the bootstrap workarounds; const-
    fold uses full bignum.Add / Sub / Mul across the int64 ∪ uint64
    union range (`422_const_fold_wide`).
  - **Slice 5** (`25fad6f`) — `/` and `%` fold with div-by-zero +
    Go-semantics sign rules; new bignum.Num.Div + Mod (with seven
    unit tests) underpin the fold (`426_const_fold_div_mod`,
    `427_err_const_fold_div_by_zero`).

### ~~Bootstrap Go interpreter: uint64 ordering / division go through int64 (signed)~~ — FIXED 2026-05-18
- **Fix**: `bootstrap/63a8889` updated `evalIntBinaryOp` to
  dispatch on operand signedness for the ops where it matters
  (SLASH, PERCENT, SHR, LT, GT, LEQ, GEQ).  Unsigned uint64 values
  with the high bit set now compare and divide correctly under
  bnc-interpreted execution.
- **Symptom**: in `boot` mode, uint64 comparisons (`<`, `>`, `<=`,
  `>=`) and division (`/`) gave wrong results when one operand
  had the high bit set.  Concrete repro: `cast(uint64, 1) << 63 >
  5` was **false** under boot, true under boot-comp.
- **Cleanup landed in `binate/72a0bac`**: dropped the
  bootstrap-specific workarounds in `pkg/bignum.parseDigits`
  (precomputed thresholds → natural `uint64Max - du / base`
  overflow check), `pkg/types/types_assignable.bn:untypedIntLitFitsTarget`
  (inline bit-shift bounds → bignum.Num.Fits* methods), and
  `pkg/types/check_expr_constfold.bn:foldIntArith`
  (31-bit-magnitude window → full bignum.Add / Sub / Mul).
  `pkg/bignum` xfail.boot marker removed.
- **Pinned by** `conformance/422_const_fold_wide` (wide-fold cases
  that the 31-bit window couldn't handle).

### ~~Native AArch64 backend — regPool saturation (cluster A follow-up)~~ — WRAPPED UP
- **Silent-corruption hazard removed** (`e8dfb85`, 2026-05-01).
  `pkg/native/arm64/arm64_regmap.bn:regPool(i)` previously returned
  X15 for any `i >= 6`, silently aliasing distinct SSA values when
  more than 7 live scratch regs were needed (the original cluster-A
  miscompile shape). It now panics with a clear message that prints
  the offending `ir.OP_*` so the next saturation case identifies
  itself.
- **Two live sites fixed**: `emitCall` (8-arg call in
  `046_many_params`, `e8dfb85`) and `emitReturn`'s sret + pack-into-
  X0..X7 paths (9-value return in pkg/asm/parse, `f704e09`).  Both
  walked `ins.Args` without resetting the regmap; fix is per-arg
  `rm.ResetRegs()` between arg slots (plus reload of `dstPtr` inside
  the sret loop so the reset doesn't strand it).
- **Pool extended to X9..X17** (`ecdd8ad`, 2026-05-14).  X16/X17 are
  AAPCS IP0/IP1 — caller-saved intra-procedure scratches; safe under
  two disciplines (audited in tree):
    1. *BL discipline.* No emitter reads a pool reg after a BL/BLR;
       every BL site is followed by `rm.ResetRegs()`.
    2. *Direct-use discipline.* emitCall / emitCallIndirect use
       X16/X17 directly outside the pool, paired with per-arg
       `rm.ResetRegs()` so the pool never hands those regs back
       inside the same op.
- **If a future op ever needs 10+ live scratches**, regPool panics
  at slot 9 with `currentEmitOp` in the message.  Fix is either the
  per-arg ResetRegs pattern (emitCall / emitReturn) or a real
  spill-on-exhaustion allocator.  Not actionable until something
  trips it; the playbook lives in the regPool source comment.

### ~~Bytecode VM: unsigned compare / div / rem dispatched as signed~~ — FIXED
- **Symptom**: pkg/bignum had 7 failing tests in `boot-comp-int`
  (Add / Sub / Mul / FitsUnsignedMax).  Root cause: uint64
  comparisons returned wrong answers when an operand had the high
  bit set (e.g. `uint64Max > 100` was false), and `uint64Max / 7`
  was 0.  bignum's overflow checks rely on both.
- **Root cause**: `pkg/vm/lower_instr_helpers.bn` always routed
  integer cmp through BC_S* and integer DIV/REM through BC_DIV /
  BC_REM regardless of operand signedness.  The unsigned opcodes
  (BC_ULT / BC_ULE / BC_UGT / BC_UGE / BC_UDIV / BC_UREM) were
  declared in `pkg/vm.bni` but had neither dispatch nor executors.
- **Fix**: lowerCmpOp / lowerBinOp check `Args[0].Typ` (resp.
  `instr.Typ`) for `IsInteger() && !Signed` and dispatch to the
  BC_U* opcodes; added executors that cast operands to uint64
  before applying the operator.

### ~~Bytecode VM: BC_LOAD8 zero-extends signed sub-word loads~~ — FIXED
- **Symptom**: under any `*-int*` mode, signed narrow integer values
  with the high bit set came back wrong after a load through alloca'd
  storage (`var x int32 = -5; x < 0` was false; `int32 INT_MIN.String()`
  printed `"2147483648"`; `int32(-5).Compare(5)` returned 1).
- **Root cause**: `pkg/vm/vm_exec_helpers.bn` `BC_LOAD8` zero-filled
  upper bits regardless of the loaded type's signedness, and the
  lowering in `pkg/vm/lower_memory.bn:lowerLoad` had no signal to
  distinguish signed from unsigned sub-word loads.
- **Fix**: `lowerLoad` now sets `bc.Aux = 1` when the load is a
  sub-word `TYP_INT` with `Signed == true`.  `BC_LOAD8` honours the
  flag by checking the assembled value's sign bit and OR-ing in the
  upper-bit mask when set.  Store side untouched (`BC_STORE8`
  already wrote the correct byte payload).
- **Tests**: `conformance/416_narrow_int_sign_ext.bn` (now passes
  in all `*-int*` modes; xfail markers dropped).  `pkg/std`
  unit tests `TestInt32StringNegative` + `TestInt32CompareNegatives`
  now pass; package xfail markers for `boot-comp-int` /
  `boot-comp-comp-int` / `boot-comp-int-int` dropped.

### ~~pkg/types boot-comp regression: hang during unit-test run~~ — FIXED
- **Root cause**: `pkg/ir/gen_method.bn` was missing the
  needsStructCopy-on-arg handling that `gen_call.bn` does for free-
  function calls. When a method takes a value-struct arg with
  managed fields (e.g. `p.addError(pos, msg)` where `pos` is
  `token.Pos` with `@[]char File`), the method-call path passed
  the struct by value WITHOUT RefIncing the managed field. The
  callee's scope cleanup then RefDec'd the field at end of scope,
  freeing the backing under the caller. After many such calls the
  freed-but-still-referenced backings led to use-after-free, then
  malloc heap corruption — eventually trapped at the next Malloc
  (which happened to be deep inside checkSrc → ParseFile →
  appendDecl during TestCheckSizeofBasic).
- **Why it appeared at 7251ffc**: parser helpers like next /
  expect / addError were free functions before that commit, so
  argument copies went through `gen_call.bn`'s correct handling.
  Method form routed them through `gen_method.bn` instead, which
  was missing the args-side struct-copy emit. The receiver-side
  branch already had it; only user args were missed.
- **Fix**: add the args-side `needsStructCopy` block to
  `gen_method.bn` (mirrors `gen_call.bn`), and also the
  `ctx.StmtGrewSP = true` markers on managed-slice / struct-copy
  results (also missed). Boot-comp `pkg/types` 270/270 after fix.

### ~~Array of managed-slice elements: string→@[]char in array context~~ — FIXED
- **Was**: two distinct bnc miscompiles for arrays whose element type
  is a char-slice (`@[]char`):
  - `[N]@[]char{"a","b","c"}` array-literal — silent wrong output,
    each slot's data ptr written but len/refptr/backing_len left at
    zero, so println saw len=0 and printed nothing.
  - `var arr [N]@[]char; arr[i] = "x"` indexed assignment — bnc
    aborted with `extractvalue operand must be aggregate type` on
    the refcount-Inc step (extractvalue called on a bare i8* from
    OP_CONST_STRING instead of a %BnManagedSlice).
  Both: var-decl / non-array-assign paths were converting
  OP_CONST_STRING → managed-slice value via EmitStringToChars; the
  array-literal and array-index-assign paths weren't.
- **Repros** (now passing in all modes):
  conformance/365_array_managed_elem_lit.bn,
  conformance/366_array_managed_elem_assign.bn.
- **Unit tests** in pkg/ir/gen_access_test.bn:
  TestArrayLitManagedElemEmitsRodataMSliceCopy,
  TestArrayIndexAssignManagedElemEmitsRodataMSliceCopy.
- **Related verification sweep (2026-05-06)**: tested arrays of
  OTHER managed element shapes after the initial fix.  `[N]@T`
  and `[N]@[]int` (with @[]int{...} elements) work cleanly under
  bnc.  `[N]struct-with-managed-field` revealed two additional
  bugs in genCompositeLit and genArrayLit, now fixed and pinned
  by conformance/367 + 368 and
  TestGenCompositeLitStructManagedCharField:
  - genCompositeLit's per-field string→char-slice conversion was
    gated `&& ft.Kind == types.TYP_SLICE`, so it only fired for
    raw-slice fields; @[]char fields fell through and the
    managed-slice RefInc / store wrote 8 bytes into the 32-byte
    slot.  Fix: drop the kind gate (isCharSliceType already
    matches both raw and managed).
  - genArrayLit didn't load struct values from their alloca
    pointer before storing into the array slot (mirroring what
    gen_control.bn's array-index-assign branch already did), so
    `[N]S{S{...}, ...}` wrote each element's i8* alloca pointer
    into the struct-sized slot instead of the struct value.
    Fix: add the same load-from-alloca guard.
- **Third site, found 2026-05-07** while resuming the unit-test
  cleanup sweep into asm / bnc / bni / bnlint args fixtures
  (which want to use `@[]@[]char{"a","b",...}` in place of
  `make_slice(@[]char,N)` + indexed assigns): genManagedSliceLit
  had the same gap.  String-literal elements stored only their
  bare data pointer (8 bytes) into the 32-byte managed-slice
  element slot, so reads came back len=0 (silent empty output).
  Fixed and pinned by conformance/372 +
  TestManagedSliceLitCharElemEmitsRodataMSliceCopy.  All three
  sites — genArrayLit, gen_control's array-branch, gen_composite
  per-field, genManagedSliceLit — now apply the same isCharSliceType
  + OP_CONST_STRING → EmitStringToChars conversion.  If a fourth
  store site surfaces, look for a missing instance of that same
  pattern.

### ~~boot-comp-int-int: blocked on registerPureCExterns from interpreted cmd/bni~~ — DONE (2026-05-07)
- **Resolved by**: `b9e1fed` (BC_FUNC_VALUE registry-fallback in
  execFuncRefOp). `2662c5c` then unblocked the build chain by
  fixing four leftover `TypeName(t)` free-function call sites in
  `pkg/types/check_decl_func.bn`. Mode now in the `all` modeset.
  boot-comp-int-int: 314 passed / 0 failed / 1 skipped (the
  pre-existing `272_raw_slice_star_sugar.xfail`).
- **Repro**: `conformance/run.sh boot-comp-int-int 001_hello`.
  Smaller repro: e2e/print-args.sh's `bni-under-bni` case
  (currently SKIPed pointing here).
- **State (2026-05-04)**: TWO root causes were stacked.
  1. **vm.Stack overflow** — FIXED via OP_SP_RESTORE plumbing
     across IR + all backends + IR-gen end-of-statement emission.
     Five-step series: `322a90a`, `2e1a4c3`, `7079fa6`, `f47f474`,
     `3393e62`.
  2. **Infinite recursion** — FIXED. Inner cmd/bni called
     `bootstrap.Args()` and got the OUTER process's full argv
     (including `cmd/bni` itself), so its parseArgs reinterpreted
     cmd/bni at every level. Fix: cmd/bni now registers a Binate
     shim (`progArgsAfterDash`) under the `"bootstrap.Args"`
     extern name in the per-VM registry, so programs running in
     bni's VM see post-`--` args (matching the spec and the Go
     bootstrap interpreter). This is what made the original "leak"
     symptom (8 MB vmInst per recursion level) catastrophic.
  3. **CURRENT BLOCKER**: registerPureCExterns crashes when called
     from interpreted cmd/bni. `var libcMalloc *func(int) *uint8 =
     libc.Malloc` requires LookupFunc("libc.Malloc") to find a
     VMFunc; libc.Malloc has no `.bn` body, so lookup fails and
     execLoop calls rt.Exit(1) with "vm: function not found:
     libc.Malloc". Outer cmd/bni's main runs natively (so the
     direct function-pointer dereference works); inner cmd/bni
     runs as bytecode (so the same code path is hit through
     BC_FUNC_VALUE, which can only resolve VMFunc names).
  - Introduced by the registry refactor (`a841f30`, `9486de9`,
    `faa98dc`). Pre-refactor, hand-coded arms in vm_extern.bn
    served libc/bootstrap calls without any registration step;
    refactor moved bindings into a per-VM registry that requires
    a function value at registration time.
- **Chosen fix (2026-05-06)**: extend `BC_FUNC_VALUE`'s
  `LookupFunc` miss path in `pkg/vm/vm_exec_helpers.bn:execFuncRefOp`
  to fall back to the executing VM's `vm.Externs` registry. On
  hit, build the function value as
  `{vtable=ExternBinding.VtableAddr, data=ExternBinding.DataAddr}`
  — same shape `OP_FUNC_VALUE` produces today, just sourced from
  the registry instead of from `vm.Funcs`. ~15 lines, one file.
  - **Why this and not a manifest / .bn-body wrappers**: the wall
    is at the lookup. The registry is already populated by each
    layer's host (cmd/bni's `registerPureCExterns`) before the
    next layer's main runs, so each layer's `BC_FUNC_VALUE` is
    dispatched by a VM whose `vm.Externs` already has the
    bindings. Works at arbitrary recursion depth without any
    bytecode-side compile-time emission and without forcing
    pkg/libc.bn (or analogous wrapper bodies) to be loaded into
    every nested VM.
  - **Soft limitation**: a user program that does
    `var f = libc.Malloc` at top-level with no surrounding
    `RegisterExtern("libc.Malloc", ...)` in the calling VM gets
    "function not found". Not an issue for cmd/bni-on-cmd/bni;
    soft problem for ad-hoc scripts under unusual embeddings.
- **Considered and rejected**:
  1. Detect interpreted context in cmd/bni and skip
     registerPureCExterns. Fragile; "interpreted" detection isn't
     first-class.
  2. Revert pure-C externs out of the registry — mixes two
     dispatch shapes per extern name.
  3. Compile-time-emitted shim manifest in both native backends +
     `rt.LookupShim`. Drafted in (now-deleted)
     `plan-shim-manifest.md`. Comparable cost to option 2 below;
     redundant with the chosen fix; only wins for the
     "no-pre-registration" case which doesn't apply here.
  4. `.bn`-body wrappers (intrinsic-call form `_c_<name>` or
     `@cextern` annotation) for pure-C externs. Cleanest in
     theory but doesn't help nested VMs that don't load
     `pkg/libc.bn` — same wall recurs at depth.
- **CI status**: now in the `all` modeset; conformance, unit-tests,
  and perf-tests workflows run boot-comp-int-int as a matrix entry.
- **Earlier original diagnosis** (pre-leak-fix, kept for context):
  caller was bytecode `rt.Free`, fnIdx was a NATIVE function
  pointer (e.g. 0x1043F5BAC ≈ 4.37e9) being treated as a 1-
  based VM index. The allocation was made by NATIVE rt.Alloc
  via the BC_MAKE_SLICE handler in vm_exec.bn calling native
  rt.MakeManagedSlice → native rt.Alloc, which stored
  `_raw_func_addr(RawFree)` in h[1] as a native pointer; later
  RefDec'd by bytecode rt.RefDec → bytecode rt.Free →
  BC_CALL_INDIRECT mismatch. Phase 3 trampolines retire this.

### ~~Native AArch64 backend — emitCallFuncValue slice-arg ABI mismatch~~ — FIXED
- Root cause was actually in `emitFuncValueShims` (arm64.bn), not
  the call site: the shim shuffles X1..XN → X0..X(N-1) to drop
  the closure-data slot, but counted register words by
  `len(fvTyp.Params)` instead of summing each param's
  `common.ArgWords`.  A slice param occupies 2 consecutive arg
  registers, so the shim ran a single MOV X0, X1 and left
  slice.len in X2 dangling — the callee read X1 (= slice.data)
  as its len, so any `len(s)`-driven loop ran 0 iterations.
- Fix: sum `common.ArgWords(fvTyp.Params[i].Type)` across all
  params and shift that many register words.
- `conformance/364_funcval_slice_arg` now passes under
  boot-comp_native_aa64.

### Native AArch64 backend — interface dispatch — LANDED
- Implemented OP_IFACE_VALUE, OP_CALL_IFACE_METHOD, OP_IFACE_DTOR
  in pkg/native/arm64; added `__ivt.<...>` vtable emission to
  EmitObject; added TYP_INTERFACE_VALUE / TYP_INTERFACE_VALUE_MANAGED
  cases to IsAggregateTyp and PlanFrame's data-region allocator.
  See `arm64_iface.bn` + the new ops in `arm64_dispatch.bn`.
- Verified: boot-comp_native_aa64 conformance went from 0/327
  (everything failed at link with `_bn_entry undefined` — that
  side was fixed earlier in the same commit chain) → 321/1/6
  passing/failing/xfail.  The remaining failure (364) is the
  slice-arg ABI mismatch above.
- Layout note: matches LLVM's emit_impls.bn exactly — slot 0 is
  the receiver dtor (or null if no dtor in this TU), slots 1..N
  are method pointers in interface-declaration order, each slot
  is an 8-byte ARM64_RELOC_UNSIGNED fixup that the linker
  resolves to the symbol's absolute address.

### ~~Inline RefInc / fast-path inline RefDec (perf)~~ — DONE
- **Plan doc**: `explorations/plan-refcount-inlining.md` (Status: DONE).
- New IR ops `OP_REFINC` / `OP_REFDEC` added alongside the old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC`; IR-gen switched to emit the new ops; old emitters (`EmitRefcountInc` / `EmitRefcountDec` / `EmitRefcountDecDtor`) deleted in favor of `EmitRefInc` / `EmitRefDec` / `EmitRefDecDtor`.
- All three backends (LLVM, VM, native arm64) lower the new ops inline:
  - LLVM: nil-check diamond + header GEP at -16 + load/{add,sub}/store, with a slow-path call to `@bn_rt__ZeroRefDestroy` for RefDec when the count hits zero.
  - VM: fused single-dispatch bytecode ops `BC_REFINC_INLINE` / `BC_REFDEC_INLINE_FAST` — one switch arm per refcount site, vs ~5 if the IR had pre-expanded to primitives.
  - arm64: CBZ + LDR(pre-index for RefInc, separate SUB+LDR for RefDec to keep ptrReg alive across the BL) + add/sub + STR + CBNZ for RefDec; BL `bn_rt__ZeroRefDestroy` only on the slow path.
- **Slow-path helper**: `rt.ZeroRefDestroy(ptr, dtor)` lives in `pkg/rt`; called only when the inline RefDec decrement leaves the refcount at zero. Runs the optional dtor (via `_call_dtor`) and `Free`.
- **User-visible impact**: none. All call sites are compiler-emitted.
- **Commits** (chronological): `eb7332e` (OP_REFINC), `9cb934d` (LLVM RefInc), `e972953` (VM RefInc), `8b896de` (arm64 RefInc), `34511bd` (RefInc switchover); `6aa78d1` (ZeroRefDestroy), `46e8e52` (OP_REFDEC), `a8104d2` (LLVM RefDec), `445e40d` (VM RefDec), `a4847b2` (arm64 RefDec), `19502d4` (RefDec switchover + with-dtor tests).
- **Cleanup status (2026-05-02)**: IR/backend dead code is GONE — old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` constants, all three backends' old dispatch arms, the non-INLINE `BC_REFINC` / `BC_REFDEC` bytecode ops + their VM exec handlers, and `emitRefcountCall` are all removed. The `bn_rt__RefInc` / `bn_rt__RefDec` runtime symbols (declared `pkg/rt.bni:122-127`, defined `pkg/rt/rt.bn:157,166`) are NOT dead — but their remaining callers are dubious and they should probably be retired:
  - **Remaining callers**: (a) VM extern handlers in `pkg/vm/vm_extern.bn` — the `rt.RefInc` / `rt.RefDec` extern arms at lines 21-29 plus the managed-slice copy/dtor paths at 169/175/191/195 that hand-RefInc element backings during structural copies; (b) conformance tests `092_rt_alloc`, `093_rt_managed_slice`, `104_rt_refcount`, which exercise these as a public manual-refcount API.
  - **Why retire**: with every compiled refcount op inlined, the runtime symbols exist only for these dubious users. Keeping them in `pkg/rt`'s public surface entrenches a manual-refcount escape hatch that nothing in the language model encourages. The `vm_extern.bn` callers are part of a broader "all of `vm_extern.bn` is dubious" question — the managed-slice copy paths there should probably move out of host code entirely.
  - **Scope when picked up**: drop or rewrite the three conformance tests; audit/migrate the `vm_extern.bn` paths (likely part of a larger vm_extern.bn rework); then delete the symbols from `pkg/rt.bni` + `pkg/rt/rt.bn`. Not a "just deletion" change — has public-API implications. The "VM extern dispatch: name → function-value registry" entry below describes the natural vehicle: the `rt.RefInc` / `rt.RefDec` extern arms cease to exist (no caller left to register), and the surgical refcount paths in `bootstrap.Args` / `ReadDir` get audited as part of that rework.

### ~~VM extern dispatch: name → function-value registry~~ — DONE
- ExternBinding registry + RegisterExtern / LookupExtern API:
  landed.
- BC_FUNC_VALUE registry-fallback (`b9e1fed`): execFuncRefOp
  consults `vm.Externs` on `LookupFunc` miss and constructs the
  function value from `binding.VtableAddr` / `DataAddr`.  Removes
  the chicken-and-egg that blocked nested-VM
  `var x = pure_C_extern` constructions.
- All host externs (rt.*, libc.*, the full bootstrap.* C-shaped
  surface) migrated through the registry; vm_extern.bn's
  execExtern is now a pure registry dispatch.
- ReadDir's migration surfaced a latent codegen bug: emit_funcvals.bn's
  aggregate-shim was emitting a register-style call
  (`%r = call <ret> @<fn>(...)`) for IsCExtern callees regardless
  of whether they used the C-ABI sret convention.  For >16-byte
  returns (e.g., `@[]@[]char`), the sret-declared callee would
  write the result through what it interpreted as the sret
  pointer (the first user arg), corrupting memory.  Fixed in
  `666f2c9` — sret-aware shim emission, now consistent with
  emit.bn (declarations) and emit_call.bn (regular call sites).

### Migrate self-hosted code to method form (opportunistic) — DONE 2026-05-13
- All originally-listed candidates landed (free function +
  method shim → all callers converted → shims dropped):
  - `pkg/buf.CharBuf` — `Len` / `Bytes` / `Freeze` /
    `WriteHexByte` / `WriteInt` / `WriteByte` / `WriteStr` (commits
    `174666c..8f96357`).  `New` and `CopyStr` stay free.
  - `pkg/asm/elf.BinBuf` — `WriteU8` / `WriteU16` / `WriteU32` /
    `WriteU64` / `WriteBytes` / `WriteZeros` / `Align` /
    `WriteAddr` / `grow`.
  - `pkg/asm.Assembler` — `SetError` / `SetSection` / `DefineLabel` /
    `SetGlobal` / `SetWeak` / `AddFixup` / `Emit*` /
    `Align`/`AlignFill`/`Zero`/`Fill` / `Finalize` plus
    helpers (`findSection`/`addReloc`/etc.).
  - `pkg/types.Type` — `IsInteger` / `IsFloat` / `Identical` /
    `AssignableTo` / `ResolveAlias` / `SliceElem` / `PointerElem` /
    `FieldByName` / `NeedsDestruction` / `IsConst` / `StripConst` /
    `TypeName`.
  - `pkg/types.Scope` and `pkg/types.Checker` — full API
    methodified (Lookup / Check / CheckPackage / ExprType /
    LoadPackageInterface / etc.; commits `cb0f624`,
    `0b573b7`).
  - `pkg/parser.Parser` — top-level (`Parse*`) and primitives
    (`next` / `expect` / `got` / `peekTok`); commit `5fbba29`.
  - `pkg/lexer.Lexer` — `Next` / `advance` / `peek` / `col` /
    `curPos` / `newline` / `scan`.
  - `pkg/asm/parse.Parser` — `ParseLine` / `ParseFile`
    (commit `d18e5c8`).
  - `pkg/native/common.RegMap` — full API (commit `33e6475`).
  - `pkg/vm.VM` — `CallFunc` / `CallByVMFunc` (`9b4465d`),
    later `LookupFunc` / `LookupExtern` / `LowerModule` /
    `LowerOneFunc` / `LowerOneFuncShadow` / `RegisterExtern`
    (`b6b6155`).
  - `pkg/ir.Module` — `AddFunc` / `AddGlobal` / `AddTypeDef` /
    `CollectStrings` (`00fd13a`), `FinalizeStrings` /
    `HasPackageInit` (`d5dc8f4`), `EmitInitDispatcher` /
    `EmitMainEntry` (`8d05e92`).
  - `pkg/ir.Block` — `Emit*` family (~50 emitters) migrated in
    a four-stage pass with a temporary `Block.Func` back-pointer:
    `6708b49` (back-pointer), `49254ba` (method form alongside),
    `8cf9093` (call sites), `d67231a` (drop shims).
  - `pkg/ir.Instr` — `IsTerminator` (`b320c98`).
- Migration discipline: each batch added method-form +
  free-function shim, converted all call sites, then dropped the
  shim — one commit per stage, conformance/`basic` green
  throughout.  Documented in CLAUDE.md.

### Interface embedding/extension — DONE 2026-05-13
- **Plan**: `plan-interface-embedding.md`.  Design ratified in
  `claude-notes.md` § "Interfaces" (extension paragraph) and
  detailed in `claude-discussion-detailed-notes.md` § "Interface
  Extension".  Vtable layout from `claude-plan-1.md` § 2.3.
- **Slices** (all committed on main):
  - **E.1**: parser + AST + reject-extension placeholder (parser
    accepts `interface X : I1, I2, ... { ... }`; parent list
    stored in the existing `Decl.Interfaces` field).
  - **E.2**: type-checker parent resolution + method-set
    propagation (no cycles via forward-ref-only rule, no
    duplicate parents, no same-name signature conflicts; impl
    satisfaction walks `ifaceFullMethods`).
  - **E.3**: IR-gen transitive impl emission + concat vtable
    codegen (`(R, child)` triggers `(R, ancestor)` ImplInfo
    entries; LLVM vtable `[any-block][parent1 full vtable]...[own]`).
  - **E.4 part 1**: dispatch through inherited methods —
    `findInterfaceMethod` walks the parent chain and returns an
    absolute vtable slot; codegen + VM consume the slot directly
    (the old `+1` adjustment is gone).
  - **E.4 part 2a**: type-checker iface upcast assignability
    (`*Child → *Parent` etc.) + latent `Identical` bug fix for
    iface types.
  - **E.4 part 2b**: explicit upcast IR/codegen — new
    `OP_IFACE_UPCAST`, LLVM lowering via static slot-offset GEP,
    VM lowering via runtime name-rewrite of the vtable's mangled
    suffix.
  - **E.5**: cross-package extension verified by conformance
    388; docs flipped from "not yet implemented" to "implemented".
    Conformance positives + negatives added in a follow-up
    (`6a5203b`) to pin user-facing error wording at the bnc
    layer — 395 (multi-parent), 396 (3-level deep), 397
    (forward-ref cycle), 398 (duplicate parent), 399 (method
    signature conflict), 400 (parent isn't an interface).
- **Coverage**: 4 direct tests for the `Identical` fix /
  inherited slot / GEP dispatch slot / managed-to-raw upcast
  (commits `d485136..277f8b0`); end-to-end conformance 387
  (same-package upcast) + 388 (cross-package upcast) + 395–400
  (extension positives + negatives); 11 type-checker tests
  covering single/multi/deep extension, diamond inheritance,
  parent recording, full-method-set order, forward-ref/self/
  non-interface/duplicate-parent/signature-conflict rejections;
  IR test for transitive ImplInfo emission + redundant-parent
  dedup + recursive vtable size; codegen test for the concat
  layout shape plus a direct test for `emitIfaceUpcast` (LLVM
  extract/GEP/rebuild sequence and parent-slot offset).
- **Connection to RTTI** (still open): if/when concrete-type
  assertions land, a `*TypeInfo` slot in the `any`-block makes
  it reachable from any interface vtable via offset 0 —
  independent of which interface the value is currently typed
  as.  Tracked separately in `notes-package-introspection.md`.

### `Self` type in interface declarations — RATIFIED 2026-05-12
- **Outcome**: ratified as DECIDED per the proposal in
  `claude-notes.md` § "`Self` type in interface declarations
  — DECIDED 2026-05-12".  Reserved identifier valid only
  inside interface declarations; substituted with the
  receiver type at impl-collection time.
- **Open question resolution**: methods using `Self` in
  non-receiver positions are **rejected** when called
  through an interface value (Rust's "object-safe"
  restriction).  Such methods are callable only through
  generic constraints where T is statically known.
  Rationale: the alternative (type-erased dispatch through
  `*Iface`) would require every impl to provide a
  heterogeneous entry point — `int.Compare(*Comparable)`
  would have no useful behavior when called with a
  `string`, leaving only a panicking type assertion as the
  implementable shape.
- **Downstream**: unblocks `plan-primitives-impl-interfaces.md`
  Slice 2b (`Comparable` / `Orderable` / `Hashable` for
  primitives) and the constrained-generics path in
  `plan-generics.md` (Slice 3).

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
- CI hookup for `boot-comp_native_aa64`: DONE — added to the `all`
  modeset and the unit/conformance/perf workflows now split the
  matrix so native_aa64 runs on `macos-latest` (Apple Silicon) while
  the LLVM-chain modes stay on `ubuntu-latest`.

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

### ~~Compiler bug: `bnc -g` emits invalid LLVM IR after OP_REFDEC inline lowering~~ — FIXED
- **Repro** (2026-05-01): any source exercising `OP_REFINC` or
  `OP_REFDEC`, built with `bnc -g ...`, failed clang at compile time:
  ```
  error: expected instruction opcode
   ri.0.skip:, !dbg !DILocation(line: 179, scope: !12)
             ^
  ```
  Affected both inline RefInc and RefDec sites; in practice surfaced
  via OP_REFDEC since most -g use hits a managed-pointer destructor.
- **Root cause**: the inline lowerings (`emitRefIncInline` /
  `emitRefDecInline`) emit a multi-line sequence ending with a
  basic-block label (`ri.<seq>.skip:` / `rd.<seq>.skip:`).
  `addDbgToLastLine` in `pkg/codegen/emit_debug.bn` then appended
  `, !dbg !DILocation(...)` to the trailing line — including label
  lines, which is invalid LLVM IR.
- **Fix**: `addDbgToLastLine` now detects label declarations (last
  non-newline char is `:`) and skips the annotation. The label and
  any intermediate instructions in the multi-line emission stay un-
  annotated, but LLVM tolerates that — the surrounding `DISubprogram`
  is enough metadata for IR validity; only source-line attribution
  within those few lines is lost. Same convention as other multi-
  line emitters (e.g., `emitBoxInstr`).
- **Test**: `pkg/codegen/emit_debug_test.bn::TestEmitDebugDoesNotAnnotateLabels`
  compiles a managed-ptr copy under `SetDebugInfo(true)` and asserts
  no `<label>:, !dbg` substring appears in the output.
- **Verification**: full conformance under `BINATE_FLAGS="-g"` is
  green (boot-comp 287/287).

### ~~Lift function-name qualification into IR (shared across backends)~~ — DONE
- IR is now the single source of truth for canonical fully-qualified
  function names. `ir.Func.Name` (formerly `QualifiedName`, with the
  bare-name field retired) holds dot-qualified names everywhere
  ("asm.New", "main.main", "geom.Point.M"). All backends — LLVM
  codegen, VM, native AArch64 — read from `f.Name` directly; their
  prior `modulePkgName + bare-name` qualification dance is gone.
  `EmitCall` / `EmitFuncAddr` / `EmitFuncValue` / `OP_FUNC_VALUE`
  all carry already-qualified `instr.StrVal` strings.
- Migration was incremental (Steps 1–5b across `c1d4074` and
  surrounding commits): introduce `QualifiedName` field, populate it
  in `NewFunc` / `NewExternFunc`, flip writers, flip readers, then
  rename to `Name`. `mangle.QualifyName` / `mangle.FuncName` are
  unchanged — they already accepted pre-qualified dotted names.
- Regression guard: `TestGeneratePackageQualifiesByModuleName` in
  `pkg/ir/gen_module_test.bn` pins down the cmd/* divergence
  (`file.PkgName="main"` vs `m.Name="cmd/foo"`) where IR-gen had
  previously qualified by `file.PkgName` and broken every cmd/*
  binary's auto-helper symbols (`__copy_X`, `__dtor_X`).

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

### ~~Compiler bug: `-O2` / `-Og` build fails to link (undefined dtor symbol)~~ — FIXED (`65cb258`)
- Linkage was `linkonce_odr`, which lets the LLVM optimizer's
  GlobalDCE pass drop a dtor as internally-unused even though it's
  referenced from another compilation unit. Switched dtors and
  copies to `weak_odr`, which keeps the symbol live across object
  boundaries while still allowing the linker to dedupe.
- Verified `-O0` / `-O2` / `-Og` all link and self-compile cmd/bnc;
  boot-comp-comp green (282/282).

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

### ~~Verify .bni vs .bn visibility semantics~~ — VERIFIED
- Private functions (235) and types (236) in `.bn` but not `.bni` are correctly rejected by both type checkers.
- Public declarations work across packages (237). `.bni` and `.bn` definitions coexist without duplicate errors.
- Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### ~~Raw slice subslice expression copies data (bug)~~ — FIXED
- Fixed by lowering `OP_SLICE_EXPR` to primitive IR ops (step 3.1). Raw slice `s[lo:hi]` now produces a zero-copy view `{data + lo * elemSize, hi - lo}` via GEP. The C runtime `bn_slice_expr_*` functions (which incorrectly copied) have been removed.

### ~~Bounds checks on `s[i]` / `s[lo:hi]` are not wired up~~ — DONE
- `emitIndexBoundsCheck` helper added in `pkg/ir/gen_access.bn`; called from `genIndex`, from the multi-return / EXPR_INDEX assign paths in `gen_control.bn`, and from `genSliceExpr` (two checks: hi against len+1, lo against hi+1). `unsafe_index` stays check-free — `genIndex` takes a `checked bool` param and `EXPR_INDEX` passes true while `unsafe_index` passes false.
- Conformance tests 309–314 cover index OOB on slice/array, index-assign OOB, slice-hi OOB, slice lo>hi, and negative slice lo. Tests 312/313/314 xfailed on boot only because Go's bootstrap interpreter formats the trap message differently. (Original numbers 298–303; renumbered when conformance suite duplicates were resolved.)

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

### ~~Enforce parse-level rejection of function-local `type` declarations~~ — DONE
- Both parsers (`pkg/parser/parse_stmt.bn` and
  `bootstrap/parser/parser.go`) now emit
  `"type declarations must be at package level, not inside a function
  body"` when they encounter `TYPE` at statement position. Recovery
  is "parse the type-decl anyway and discard," so downstream parsing
  isn't derailed.
- Conformance test 319 (`319_err_function_local_type`) covers the
  rejection across all three basic modes.

### ~~.bni/.bn return type mismatch should be a compile error~~ — FIXED
- The type checker now verifies that `.bn` function definitions match their `.bni` declarations (parameter count/types, return count/types). Mismatches are reported as compile errors.
- Immediately caught two real bugs: `MakeStringVal` and `AddBlock` had `@[]char` in `.bni` but `*[]char` in `.bn`. Both `.bni` files fixed.
- Conformance test 221 now passes on all compiled modes.

### ~~Compiler bug: cast to sub-word pointer type emits invalid LLVM IR~~ — FIXED
- Cast codegen now uses `bitcast` (ptr→ptr), `ptrtoint` (ptr→int), `inttoptr` (int→ptr) instead of `add` for pointer types.
- Conformance test 161 passes on all compiled modes.

### ~~Compiler bug: multi-return with struct containing managed fields~~ — FIXED
- Bug was already fixed by earlier refcounting changes. Workaround reverted. Test 141 passes.

### ~~Multi-return as anonymous struct~~ — DONE
- Multi-return is an ABI contract: `func f() (T1, T2)` returns `struct { _0 T1; _1 T2 }`.
- Compiler side done long ago: `Func.MultiReturnType` propagated through FuncSig/call sites/return instructions; LLVM emission uses `llvmType(MultiReturnType)`.
- Interpreter side moot: the original tree-walker `pkg/interp` was retired in 2026-04-17. The bytecode VM (`pkg/vm`) consumes the compiler's IR directly, so it inherits the anonymous-struct layout — no separate work. Verified 2026-04-26: zero references to `VAL_MULTI`, `Value.Elems`, or `HeapObj` remain in pkg/ or cmd/.
- Plan file `plan-multi-return-struct.md` deleted (was MOOT).

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

### ~~CLI flag coherence~~ — DONE (2026-04-28, alongside Stage 1–6)
- Standardized on `--word` for long flags across bnc, bni, bnlint,
  bootstrap. Existing single-dash long flags (`-root`, `-add-root`,
  `-verbose`, `-test`, `-cpuprofile`) stay accepted as back-compat
  aliases. Single `-` is reserved for short flags (`-v`, `-I`, `-L`),
  including future combinable `-abc`-style.

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
