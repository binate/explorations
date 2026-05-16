# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## TODO

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

### Integer literals and constant expressions — RATIFIED 2026-05-15; impl pending
- **Spec**: `claude-notes.md` § "Integer literal value range and
  constant-expression arithmetic — DECIDED 2026-05-15".
- **Summary**: literals parse if their value is in `[-2^63, 2^64-1]`
  (int64 ∪ uint64).  Default type when context is ambiguous is
  target-width `int` (32 on 32-bit, 64 on 64-bit).  Context provides
  a target type and the constant's mathematical value must fit it.
  Const-expr arithmetic operates on abstract values stored at
  `(uint64 magnitude, sign bool)` with overflow detection — any
  intermediate that exceeds `[-2^63, 2^64-1]` is rejected at type
  check.  No silent wrap, no bignum.
- **Current divergence to fix**: bootstrap Go rejects high-bit hex
  via `ParseInt(..., 16, 64)`; self-hosted bnc silently wraps via
  int64 overflow in `pkg/types/checker_util.bn:parseHexInt`. Neither
  matches the ratified spec — both need updating.
- **Implementation scope**:
  - `pkg/types/checker_util.bn`: rewrite `parseIntLiteral` to parse
    into `(uint64 magnitude, sign bool)`. Reject literals outside the
    union range.
  - `pkg/types` constant representation: extend untyped-int Type to
    carry magnitude + sign for downstream fit-check. Today
    `TYP_UNTYPED_INT` has no value payload.
  - Const-expr evaluator: small `ConstInt` struct (magnitude uint64,
    sign bool) + Add/Sub/Mul/Div/Shift methods returning
    `(ConstInt, overflowed bool)`. Apply at every const-folding site.
  - Assignment / cast / arg-passing fit-check: replace today's
    int-fits-in-target check with the mathematical-value-in-target-
    range form.
  - Bootstrap `main.go`: mirror the same rules. A small sign-magnitude
    pair works there too; `math/big.Int` would be overkill.
  - Conformance tests pinning every row of the behavior table in
    `claude-notes.md` (positive + negative for each shape; also the
    "intermediate overflow rejects" cases).

### Bootstrap Go interpreter: uint64 ordering / division go through int64 (signed)
- **Symptom**: in `boot` mode, uint64 comparisons (`<`, `>`, `<=`,
  `>=`) and division (`/`) give wrong results when one operand has
  the high bit set.  Concrete repro: `cast(uint64, 1) << 63 > 5`
  evaluates **false** under boot, true under boot-comp.  And
  `0xFFFFFFFFFFFFFFFF / 2 == 0` under boot (the dividend's int64
  reinterpretation is -1; -1 / 2 = 0).
- **Root cause** (suspected): the Go bootstrap interpreter
  represents all Binate integer values as Go `int64` regardless of
  the Binate type tag.  Bitwise ops (`&`, `|`, `^`, `<<`, `>>`)
  and equality (`==`, `!=`) work fine because they're identical
  for the same bit pattern under either signedness; only the
  signedness-dependent ops (ordering, division) diverge.
- **Impact today**: `pkg/bignum` is xfailed under boot
  (`scripts/unittest/pkg-bignum.xfail.boot`).  In bnc operation
  this is unobservable — Go's `strconv.ParseInt(..., 64)` rejects
  high-bit literals at the bootstrap parser, so user code never
  produces a uint64 value with the high bit set in boot mode.  But
  the test-runner coverage gap is real, and any future code that
  legitimately needs uint64-with-high-bit semantics in
  bootstrap-runnable code will hit this.
- **Likely fix location**: the Go interpreter's value
  representation in `bootstrap/interpreter/`.  Currently a single
  `int64` slot; a uint64 path would require type-aware dispatch
  for ordering + division at the operator handler level.  Same
  story for `uint32` / `uint16` / `uint8` though high-bit hits
  the int64 reinterpretation less often.
- **Not blocking anything urgent**; mostly impacts pkg/bignum test
  coverage under boot.
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

### pkg/vm:TestExecRefIncRefDecInline crashes under boot-comp-int-int
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

### Native AArch64 backend — regPool saturation (cluster A follow-up)
- **Silent-corruption hazard removed** (`e8dfb85`, 2026-05-01).
  `pkg/native/arm64/arm64_regmap.bn:regPool(i)` previously returned
  X15 for any `i >= 6`, silently aliasing distinct SSA values when
  more than 7 live scratch regs were needed (the original cluster-A
  miscompile shape). It now panics on `i == 7` with a clear message
  pointing at this TODO. Any future over-allocating op pattern
  surfaces as a loud compile-time abort with a stack trace, not a
  silent miscompile.
- **Live site fixed** in the same commit: `046_many_params`'s
  8-int-arg call was hitting saturation via `emitCall` (8
  sequential `nextReg` calls — one per arg load). Fix in
  `arm64_call.bn`: the per-arg scratch reg is dead after the
  immediately-following Mov-to-argReg / Str-to-stack, so reset
  between arg slots. Conformance 289/0/0 after.
- **Second live site fixed** (2026-05-13, `f704e09`): `OP_RETURN`'s
  two multi-return paths (sret-via-X8 for tuples > 64 bytes,
  pack-into-X0..X7 for tuples ≤ 64 bytes) walked `ins.Args` without
  resetting the regmap between iterations.  Same shape as the
  emitCall case — pkg/asm/parse's compile under
  boot-comp_native_aa64 panicked with "op=45 (OP_RETURN)" on a
  9-value return.  Fix: per-arg `rm.ResetRegs()` mirroring
  emitCall, plus `dstPtr` reload inside the loop in the sret case
  so the reset doesn't strand it.  Same commit adds a diagnostic:
  `regPool` now prints the current `ir.OP_*` int before panicking
  (`currentEmitOp` package var, set by `emitInstr`), so the next
  saturation case identifies itself in the panic instead of
  requiring an instrumented rebuild to chase.
- **Pool extended to X9..X17** (2026-05-14, `ecdd8ad`).  X16/X17 are
  AAPCS IP0/IP1 — caller-saved intra-procedure scratches, undefined
  after any BL.  Two disciplines keep the extension safe:
    1. *BL discipline.* No emitter reads a pool reg after a BL/BLR;
       every BL site in this package is followed by `rm.ResetRegs()`
       (audited in arm64_call.bn, arm64_call_indirect.bn,
       arm64_emit.bn's memcpy / MakeManagedSlice sites,
       arm64_ops.bn's RefDec slow path).
    2. *Direct-use discipline.* emitCall and emitCallIndirect
       reference X16/X17 directly outside the pool; their per-arg
       `rm.ResetRegs()` keeps `m.Next` below 7 inside those loops,
       so the pool never hands X16/X17 back inside an op that's
       also touching them directly.
- **Still open — structural fix for >9.** Pool now panics at slot 9.
  An op that needs 10+ simultaneously-live scratches would still
  fail — fix is either a per-arg ResetRegs in that op (the pattern
  emitCall and emitReturn use) or a real spill-on-exhaustion
  allocator.  Not blocking — no current op hits this; the panic
  prints `currentEmitOp` so the next saturation case identifies
  itself in the panic.

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

### Pointers to interface values
- **Plan**: `plan-pointers-to-iface-values.md` (sliced P.1–P.5).
- Design pinned in `claude-notes.md` § "Interfaces" line 421:
  `**Stringer`, `*@Stringer`, `@(*Stringer)`, `@(@Stringer)` are
  all valid pointer-to-iv shapes; parens are required by the
  grammar to disambiguate the `@(@…)` form.
- **Current state** (2026-05-15): `**Iface` and `*@Iface` work
  for assignment and explicit-deref method dispatch
  (`(*p).Foo()` after `438f3f2`). `@(*Iface)` / `@(@Iface)`
  parse and type-check but dispatch through them returns wrong
  values. Auto-smoothing of pointer-to-iv receivers
  (`p.Foo()` where `p` is any pointer-to-iv) is rejected at
  type check — needs a smoothing rule analogous to `*T → T`.
- Needed for: generics (`*T` where `T=Stringer`), out parameters,
  arrays of interfaces, containers.

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
