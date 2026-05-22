# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## TODO

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

### Substitute LP64-pinned conformance tests with target-aware variants
- **Why**: A handful of existing conformance tests pin LP64-specific
  expected output or use `int`-typed bit_casts that assume `int` is
  64 bits.  Under `--target arm32-baremetal` (and arm32-linux once
  the codegen catches up), `int` is 32 bits and these tests
  legitimately produce different output — not because of a bug
  but because the test was written before there was a 32-bit
  target.
- **Currently xfailed on arm32-baremetal**:
  - `290_sizeof_alignof` — `.expected` hardcoded for LP64 sizes
    (sizeof(int)=8, sizeof(*[]int)=16, sizeof(@[]int)=32 etc.).
    ILP32 emits 4 / 8 / 16.
  - `330_float_bit_exact` — `bit_cast(int, float64)` — invalid
    LLVM cast on 32-bit int; works on LP64 because int == int64.
  - More likely to surface as the arm32 modes pick up more tests.
- **Mechanism options to evaluate** (no decision yet):
  1. Per-target `.expected.<mode>` overrides (e.g.
     `290_sizeof_alignof.expected.boot-comp_arm32_baremetal`).
     Local, file-based; tracks well with the existing xfail
     mechanism's shape.
  2. Substitution syntax in `.expected` (e.g. `@INTSIZE@`) that
     the conformance runner resolves per target.  Less per-mode
     duplication; needs runner support.
  3. Rewrite the .bn to use explicit `int64` everywhere so the
     test is target-agnostic by construction.  Works for 330 but
     not for 290 (which tests the predeclared int's size — the
     value of the test is target-dependent).
- Pick a mechanism, drop the xfails, and write the variants.


### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Use interfaces more in non-bootstrap-runnable code (opportunistic)
- **Constraint**: the bootstrap subset doesn't support interfaces
  (see `bootstrap-subset.md`), so anything in `cmd/bnc`'s
  dependency tree stays interface-free.  But everything *outside*
  that tree (cmd/bni, cmd/bnas, cmd/bnlint, pkg/vm, pkg/rt,
  pkg/lint, pkg/asm/parse, plus tests) is built by bnc first and
  then exercised — full language is fair game there.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.


- **Self-hosted (LANDED, 2026-05-01)**: type-checker
  (`pkg/types/check_stmt.bn:checkReturnStmt`) and IR-gen
  (`pkg/ir/gen_stmt.bn` STMT_RETURN branch) accept
  `return f(...)` when `f` returns the matching tuple. Each
  per-result type must be `AssignableTo` the outer's declared
  result. IR-gen lowers to one OP_CALL + one OP_EXTRACT per
  result; the existing return-RefInc/copy + temp-cleanup
  machinery handles ownership transfer. The literal-shape
  coercions in the per-expr return path (OP_CONST_NIL retyping,
  OP_CONST_STRING → string_to_chars, untyped-int width) all
  fire only on literals, which can't be call results — so the
  multi-return path skips them. The one non-literal coercion,
  `@[]T → *[]T` when the outer expects raw, is preserved on
  extracted values, mirroring the per-expr path.
  - Tests: `pkg/types/check_stmt_test.bn` (positive, arity-
    mismatch, type-mismatch); `pkg/ir/gen_stmt_test.bn`
    (`TestGenReturnMultiCallEmitsExtracts` pins
    1×OP_CALL + 2×OP_EXTRACT); conformance
    `347_return_multi_call` (all-scalar + mixed scalar/managed
    end-to-end; was 345 originally, renumbered after collision
    with `345_interface_decl`). xfail.boot. boot-comp /
    boot-comp-int / boot-comp_native_aa64 all green.
- **Bootstrap (pending decision)**:
  `bootstrap/types/checker.go:checkReturnStmt` (~963-978) still
  rejects this shape. Bootstrap acceptance is a separate
  question — the bootstrap subset is intentionally restrictive,
  and the self-hosted toolchain doesn't need this to compile.
  Defer until there's a concrete reason to widen the subset.
- Spec recorded in `claude-notes.md` ("Tail-call return for
  multi-return functions"). `bootstrap-subset.md` notes the
  bootstrap-only rejection.

### `println(uint64)` of high-bit-set values prints as signed
- **Symptom**: `var b uint64 = 9223372036854775808; println(b)` prints
  `-9223372036854775808` instead of `9223372036854775808`. The bit
  pattern is correct (compile + runtime carry it through fine since
  `bootstrap/63a8889` fixed uint64 ordering), but the println IR-gen
  lowering in `pkg/ir/gen_print.bn:emitPrintInt` always dispatches
  through `bootstrap.formatInt` which treats the int as signed.
- **Fix sketch**: in `emitPrintInt`, dispatch on `val.Typ.Signed` —
  if unsigned, route to a new `bootstrap.formatUint` (mirroring
  formatInt with the loop driven by uint64-magnitude and no sign
  byte). The `formatInt` and `formatInt64` int-min fix in `e08ceb7`
  / `8b94bf6` already does the uint64-magnitude digit extraction;
  formatUint is the smaller variant without the sign byte.
- **Not blocking** — bnc itself doesn't println uint64 values; this
  is user-visible for code that does. Files for tracking only.

### Mirror `return f(...)` acceptance in the Go bootstrap — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

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

### pkg/vm: VMFunc.Vtable / VMClosureRec lazy allocs leak on VMFunc death — IN PROGRESS
- **State**: BC_FUNC_VALUE Path B (and Path A's transient fv) lazy-
  allocate a 16-byte vtable + 32-byte closure record via
  `rt.RawAlloc`, storing them on `VMFunc.Vtable` / `VMFunc.VMClosureRec`
  as raw `int` fields.  These are not refcounted; VMFunc's auto-
  generated dtor refdec's its managed fields (`Name`, `Code`, etc.)
  but does nothing for these int slots.  When a VMFunc dies (REPL
  function replacement, per-test `VM_T` teardown, etc.) those raw
  allocations leak.
- **Why it survived undetected**: in long-running `cmd/bni`
  invocations the VM lasts the whole process, so the leak is
  bounded.  REPL and `--test` workflows hit it but with small
  per-iteration cost.
- **Fix in flight**: Phase 4 of `plan-uniform-native-fnptrs.md`
  switches Path B's lazy allocs from `rt.RawAlloc` to `rt.Alloc`
  (refcounted), changes the storage field types from raw `int` to
  managed (e.g. `@[]uint8` for the byte buffer).  VMFunc's auto-
  emitted dtor will then refdec them on death.  Same applies to the
  new `_func_handle` machinery's lazy-allocated handles for
  bytecode-only functions and to `ExternBinding.HandleAddr`.

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

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
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
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
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

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

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

### `(*p).x` (field access through explicit deref) returns 0 — bnc-compiled only
- Discovered 2026-05-21 while auditing Slice P.2 (pointer-to-iv
  dispatch).  Field access through an explicit deref hits the
  `return b.EmitConstInt(0, types.TypInt())` fallback in
  `pkg/ir/gen_selector.bn` because `genSelector` has no
  EXPR_UNARY-base case — only IDENT / SELECTOR / CALL / BUILTIN
  bases route to a real field-pointer.  Boot (Go interpreter)
  handles it correctly; bnc-compiled (all modes) returns 0.
- Pinned by `conformance/456_field_access_through_explicit_deref`
  with `.xfail.{boot-comp, boot-comp-int, boot-comp-int-int,
  boot-comp-comp, boot-comp-comp-int, boot-comp-comp-comp}`.
- Workaround: use `p.x` (auto-deref) instead.  Both raw and
  managed pointers auto-deref transparently for field access; the
  explicit form is the broken path.
- Fix sketch: add an EXPR_UNARY-STAR base case to `genSelector`
  that genExprs the operand, recovers a struct/iv pointer, and
  routes to the field-pointer / iface-method-call logic the
  IDENT-base cases already use.  The deref-then-dispatch shape
  is already wired correctly for iface-method calls
  (`isInterfaceMethodCall` checks EXPR_UNARY on sel.X) — the
  field-access shape is the residual gap.

### ~~Test harness `isTestResultReturn` should resolve type aliases~~ — FIXED
- The test harnesses (bootstrap Go `main.go` and self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

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
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package-name/path conventions item below.
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. Decisions are interleaved with the name/path
  conventions item below — sketch and conventions probably ratify
  together.

### Package name/path conventions — decide and possibly reorganize
- Current `pkg/` layout mixes toolchain internals (`pkg/parser`,
  `pkg/types`, `pkg/codegen`, …) with runtime (`pkg/rt`), bootstrap
  support (`pkg/bootstrap`), libc bridges (`pkg/libc`), and small
  utilities (`pkg/buf`, `pkg/mangle`, …). Future stdlib packages would
  pile in alongside them with no organizing principle.
- Questions to answer:
  - Should toolchain internals live under a distinct prefix
    (`compiler/parser`, `compiler/types`, …) so that "what's stdlib"
    vs. "what's compiler implementation" is visible at the import
    path? Same question for runtime / bootstrap support.
  - What does a Binate package path *look* like? Is `pkg/` a real
    prefix or just a directory convention? Are external (third-party)
    packages spelled differently?
  - How do package paths interact with the package manager's naming
    scheme (URL? registry name? short alias)?
  - Mangling: short package names (`mangle.PkgShortNameFromModule`)
    currently derive from the path's last segment. If conventions
    change, mangled symbol names change, which affects ABI. Plan a
    migration story.
  - Are there packages that should move? `pkg/bootstrap` is arguably a
    stdlib piece; `pkg/rt` is closer to runtime-internal; toolchain
    internals could become `compiler/...`. Each move is a real refactor.
- Heavily entangled with the package-manager sketch — they should
  probably ratify together, since the manager design depends on what
  paths look like.
- Output: a plan / decision doc in `explorations/`. Reorganization is
  a follow-up project.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

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
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
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
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
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

### REPL — Tier 1 + Tier 2 + Tier 4 (full) LANDED (2026-05-01)
- **Status (2026-05-01)**: Tier 1 PoC ships as `bni --repl
  <file.bn|dir>`; Tier 2 adds top-level `func`, `const`, and
  typed `var` declarations at the prompt; Tier 4 full
  redefinition shipped — compatible-sig replaces in place
  (old callers see new body), incompatible-sig shadows (old
  callers retain the old VMFunc via eager-filled CallCache,
  new callers route to the new entry).  Substrate is an O(1)
  name→idx hash on `vm.Funcs` plus eager `CallCache` fill at
  lowering time (commit `9af2d56`); shadow itself in
  `63cc49b`.  Multi-line input also landed (paren-aware
  accumulator — tracks `{`/`}` and `(`/`)` in
  `computeOpenDepth`).  See `plan-repl.md` for the per-step
  commit table, verified behaviors, deviations from the
  original plan, and the remaining follow-ups (Tier 2: type
  at prompt, methods, prompt-introduced new managed-type dtor
  regen, var-initializer evaluation; Tier 4: refcount-aware
  shadow warning, forced-shadow escape hatch, method
  redefinition).  Tier 3 (forward refs) and Tier 5
  (mid-session imports) remain DRAFT.
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
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts only immediate-mode
     entries.  Each entry → synthetic `__repl_N()` → IR-gen →
     lower-one-function → call.  Auto-`println` wrap of bare exprs
     was deferred (gated on interfaces / proper Format dispatch
     once `bootstrap.println` is retired) — type `println(...)`
     explicitly.  Multi-line input also landed.
  2. ~~**Add new top-level decls at the prompt.**~~ **`func`,
     `const`, and typed `var` LANDED (2026-04-30 / 2026-05-01).**
     Per-decl entry points in parser/types/ir; append to current
     scope, plus `vm.Funcs` for funcs, `moduleConsts` for consts,
     and `globalNames`/`globalAddrs` for vars (via the new
     `vm.MaterializeOneGlobal`).  `type` / methods +
     prompt-introduced new-managed-type dtor regen +
     var-initializer evaluation are remaining follow-ups (see
     plan-repl.md).  Still no forward refs.
  3. **Forward references.** Pending-validation queue in the type
     checker.
  4. ~~**Redefinition.**~~ **LANDED (2026-05-01).**
     Compatible-sig: `LowerOneFunc` rebinds the existing
     `vm.Funcs` entry in place at the same idx, so the
     CallCache stays valid; old callers see the new body.
     Incompatible-sig: `LowerOneFuncShadow` appends a fresh
     entry and re-points the funcIndex hash; old callers'
     eager-filled CallCache slots keep them on the OLD VMFunc,
     while freshly-lowered code routes through the new one.
     Shipped via two commits: substrate (O(1) name→idx hash +
     eager `CallCache` fill, `9af2d56`) and the shadow path
     proper (`63cc49b`).  Refcount-aware shadow warning,
     forced-shadow escape hatch, and method redefinition are
     remaining Tier 4 follow-ups.
  5. **Mid-session imports.** Loader entry point for "load this one
     package now."
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
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
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

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

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
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
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Annotations and C function interop
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
