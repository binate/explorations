# Plan: comprehensive VM SP-guard (fault safely on stack overflow; never leak/corrupt)

Status: RESERVATION SERIES (R1-R5) COMPLETE — landed 2026-09-15 (R1 770b6fbc1,
R2 70bfdd37a, R3 4401121a9, R4 70265c12f, R5 6f2576927), plus the standalone
OP_IFACE_UPCAST reclaim fix (7697db626).  Temp-growth stack overflow is now safe
by construction (reserved at frame entry; clean Plan-2 unwind; no per-op checks).
REMAINING: Inc 3 (indirect/method/func-value/iface-method call frame-push moved-arg
leak) and the R5 end-to-end coverage follow-up.  Started 2026-09-09. **APPROACH
CHANGED 2026-09-14 to the RESERVATION model (see below) after the per-op approach's
cost was surfaced.** Owner: this session (work-2).

**COMPLETE 2026-09-17.** Inc 1, Inc 2 (reservation R1-R5), and Inc 3 (crash guard
`d2e21a89c` + func-value fast-path/pre-check `43b0acc37` + iface-method pre-check
`c990def66`) are all landed.  See "Inc 3 + b2 REDESIGN" at the bottom for the S1/S2/S3
breakdown.  (Historical: the original Inc 3a "approach A" pre-delivery
`OP_STACK_CHECK_FV` was ABANDONED as a dead end — fragile against the dispatch's
transient SP growth, and a wild-`@VM`-deref from classifying by `data[0]`; the user
directed the full redesign doing Inc 3 AND b2 together, which is what landed.)

## Goal (owner-clarified 2026-09-14 — supersedes the original "make overflow
## recoverable" framing)

For the interpreter we do NOT need (or necessarily want) stack overflow to be
*recoverable by the program*. What we need:

1. On stack overflow, the offending **VM instance terminates cleanly** — no silent
   corruption (never write past `vm.Stack`), and **crucially NO effect on the
   hosting program** (so NEVER `vmPanic` — that aborts the whole process; must
   gracefully unwind out of `execLoop` with a terminal status).
2. **No leaks** when that VM is torn down.
3. **Performance affected as little as possible** (no per-op cost on hot paths).
4. **Exception — the REPL:** a stack overflow must not kill the session, so the VM
   runs in a "recoverable" mode there. This is a HOST-SIDE policy difference (keep
   the VM + reset execution state and re-prompt), NOT separate per-op VM machinery.

Key facts that make this cheap:
- The existing Plan-2 recoverable-fault unwind ALREADY cleans up leak-free: it
  unwinds frame-by-frame running each frame's cleanup pad (RefDec live managed
  values), so `rt.LiveBlocks()` returns to baseline by the time control reaches the
  host (Inc 1 tests confirm). So "clean termination, no leak" needs NO teardown
  sweep — the unwind is the cleanup. Default vs REPL differ only in the host's
  response to the terminal status (drop the VM vs keep+reset).
- The perf cost is the per-op OVERFLOW CHECK (runs always), NOT the pads (pads run
  only on overflow — rare). Per-op pads also perturb the inliner (a pad turns a hot
  op into a "faulting op") and bloat the fault table.

## THE RESERVATION APPROACH (current design)

Every temp-growth amount is COMPILE-TIME KNOWN: make_slice/iface-value/func-value
headers are fixed (2–4 words), string→array/rodata sizes are literals, a call's
return-copy-back size is the callee's result type. And temp growth is
statement-scoped (reclaimed by `OP_SP_RESTORE` at statement end). So:

- Compute, per function at lower time, `MaxStmtTempGrowth` = max over statements of
  that statement's summed SP-growth (incl. call return-copy-back sizes).
- `pushFrame` reserves `frameExtent + MaxStmtTempGrowth` in its ONE existing check
  (`wouldFrameOverflow`). If it fits, ALL of the frame's temp-growth is safe by
  construction.
- Result: NO per-op checks, NO per-op pads → no inliner perturbation, no fault-table
  bloat; the only added runtime cost is folded into the existing per-call pushFrame
  check. Overflow is detected ONLY at frame entry and handled by the Plan-2 unwind
  that already exists (clean, no leak, graceful to the host) — for both default and
  REPL.
- **One exception (runtime-sized):** the cross-mode `...*any` scratch bump
  (`vm_iface_native_vt.bn:148`) is a runtime slice length, not compile-time known,
  so it KEEPS a runtime check (it already has one — currently `vmPanic`; per goal 1
  that should become a graceful terminal-fault, not a process panic — verify/adjust).

Why this beats the per-op approach against the goals: same clean-abort/no-leak
semantics, but the check is per-CALL (already there) instead of per-OP, and nothing
lands on the hot ops (no inliner perturbation, no bloat).

## REVIEW OUTCOME (2026-09-14) — reservation approach: SOUND but inventory INCOMPLETE

An adversarial review of the reservation approach came back **NOT simply "clean."**
Verdict: the core mechanism (reserve per-function max-statement temp growth at
`pushFrame`; overflow only at frame entry; Plan-2 unwind cleans up leak-free) is
SOUND and implementable, BUT the growth INVENTORY as the plan enumerated it is
INCOMPLETE, plus the review surfaced one standalone pre-existing bug and one
semantic tradeoff needing owner sign-off.  Because the owner's condition for
reverting the per-op work was "if the review comes back clean," the per-op work is
NOT being auto-reverted — see decision points below.

**Corrected growth inventory (what `MaxStmtTempGrowth` MUST count, per region
delimited by OP_SP_RESTORE):**

1. **OP_IFACE_UPCAST (2 words)** — was MISSING from the SP-growing-op set
   entirely. Its standalone reclaim bug (unreclaimed vm.SP → unbounded growth in
   a pure-upcast loop) is now FIXED + LANDED (`7697db626`, added OP_IFACE_UPCAST
   to noteSPGrowingResult so the statement emits OP_SP_RESTORE); see
   claude-todo-done.md. The reservation inventory must still COUNT its 2 words.
2. **Transient pushManagedSlice scratch** — the string-copy path bumps vm.SP by 8
   words (4-word header incl. a transient pushManagedSlice) and the array path by
   4 + arrLen; these transient bumps must be included, not just the durable result
   header.
3. **Callee-side return-image size** — an aggregate-returning call grows the
   caller's vm.SP by the callee result type's size at BC_RETURN copy-back; count it
   at the call statement.
4. **Cross-mode arg-substitution scratch** — a cross-mode call marshalling a
   bytecode-impl iface arg to a native callee grows the caller's vm.SP in TWO
   ways (R1 review corrected the earlier "variadic is the ONE runtime growth"
   premise): (a) `substArgSlotIface` (`vm_iface_crossmode.bn:180`) —
   `align8(ByteSize)` per iface arg slot, STATICALLY known (the call's
   `ArgIfaceLayout` is built at lower time, `vmf.ArgIfaceLayouts`), so it is a
   per-call RESERVATION term (currently unchecked — see the MAJOR todo entry); and
   (b) `substituteSliceIfaceArgs` (`vm_iface_native_vt.bn:156/171`, the `...*any`
   path) — RUNTIME-sized, so it KEEPS a runtime check that must become a graceful
   terminal fault (setFault+pad), NOT vmPanic (R5).
5. Compute `MaxStmtTempGrowth` as the max over OP_SP_RESTORE-delimited regions of
   the summed growth within each region (not naively per-syntactic-statement), from
   an authoritative bump inventory that matches the VM handlers 1:1.

**Decision points for the owner (do NOT proceed past these unilaterally):**

- **(D1) Recursion-depth / over-reservation tradeoff.** Reserving
  `frameExtent + MaxStmtTempGrowth` at every frame push means recursion overflows
  sooner (fewer frames fit) and a function with one big conditional statement
  over-reserves on every call even when that branch isn't taken. This is a real
  (small) semantic change to how deep recursion can go before clean-abort — needs
  owner sign-off. [SIGNED OFF.]  R3-review facet worth recording: the same
  over-reservation also applies to dtor frames pushed DURING a fault unwind
  (CleanupDepth>0), where an overflow is a FATAL vmPanic (the §6
  fatal-in-cleanup guard), not a recoverable fault.  So a dtor whose temp growth
  lands in the narrow "would have just fit by frameExtent" band near stack
  exhaustion now hard-aborts instead of luckily completing.  Acceptable — the
  adjacent band was previously silent stack corruption (unchecked dtor temp
  growth during unwind), and a clean fatal is strictly better; it only bites at
  near-exhaustion during unwind.
- **(D2) Revert-or-keep the per-op work — DECIDED: (c) then (a).** Owner
  signed off on the D1 recursion-depth/over-reservation tradeoff and chose "(c)
  then (a)": (c) fix the standalone OP_IFACE_UPCAST bug first — DONE, landed
  `7697db626`; (a) NEXT — revert the landed per-op `1dd3f319f` (needs a fresh
  cherry-pick approval) + reset work-2 off `e7ee47730` (preserved as branch
  `work-2-perop-checkpoint`), then implement reservation with the corrected
  inventory above.

## (a) RESERVATION IMPLEMENTATION — increment breakdown (owner-approved 2026-09-14)

R1 → R2 → R3 land first (reservation is a SUPERSET of the per-op protection, so
adding it on top of the existing checks is safe); then R4 removes the redundant
per-op check with no regression window; R5 independently. Each is its own small
commit with tests.

- **R1 — authoritative SP-growth inventory (LANDED `770b6fbc1`).**
  `spGrowthBytes(instr)` (VM package) covers the growths that are a pure function
  of (op, result type): make_slice = 4-word header; iface-value / iface-upcast /
  func-value = 2 words; rodata-mslice-copy = 8 words (BC_STRING_COPY_MS: transient
  pushManagedSlice header + result); rodata-array = 4-word header + 8-byte-aligned
  arrLen; and the CALLER-side return-image copy-back for every call op
  (align8(SizeOf) for an aggregate result, 0 for scalars). Unit-tested against
  each handler's actual `vm.SP +=`. Two call-context growths are DEFERRED to R2
  (they need lowering context, not just the ir.Instr): the callee-side
  return-image build (AggregateReturnSize(f.Results)) and the cross-mode
  arg-substitution scratch (sum over the call's ArgIfaceLayout slots of
  align8(ByteSize)). R1's doc states this explicitly.
- **R2 — per-function `MaxStmtTempGrowth` (LANDED `70bfdd37a`).** At lower time walk the IR summing
  `spGrowthBytes` over OP_SP_RESTORE-delimited regions, take the max; store on
  VMFunc. Unit-test. Also add, in the appropriate regions, the two deferred
  call-context growths: (i) the callee-side return-image build,
  `types.AggregateReturnSize(f.Results)`, in the return statement's region (per
  function); and (ii) the cross-mode arg-substitution scratch, sum over each
  call's `ArgIfaceLayout` slots of `align8(ByteSize)`, in that call's region (per
  call site). Folding (ii) into the reservation also FIXES the currently-unchecked
  substArgSlotIface overflow gap (see the MAJOR todo entry).
- **R3 — fold into the reservation (LANDED `4401121a9`).** `pushFrame` / `wouldFrameOverflow`
  reserves `frameExtent + MaxStmtTempGrowth`; the direct-call `OP_STACK_CHECK`
  pre-check adds the callee's `MaxStmtTempGrowth`. Overflow caught only at frame
  entry (clean Plan-2 unwind). Test: a big-per-statement function faults cleanly
  at entry, not mid-statement.
- **R4 — remove the redundant per-op machinery (LANDED `70265c12f`)** = revert the landed
  `1dd3f319f` (OP_RODATA_ARRAY per-op check/pad) + the `attachSPGrowthPad` path.
  Lands AFTER R3 (needs its own cherry-pick approval).
- **R5 — cross-mode `...*any` scratch (LANDED `6f2576927`)** (the one runtime-sized growth): make its
  overflow a graceful terminal fault (setFault + pad), not vmPanic.

## LANDED on main (all reviewed, VM+LLVM conformance green, hygiene 20/20)

- `7d610fdb6` — Inc 1: direct-call frame pre-check (`OP_STACK_CHECK`) + eval/deliver
  arg-building split (`evalCallArgs`/`deliverCallArgs`). Fixes the moved-arg
  frame-push leak (direct calls) + the mid-arg-eval leak (all call paths). STAYS —
  the pre-check is exactly where the `+MaxStmtTempGrowth` reservation belongs.
- `e2edbe999` — multi-block cleanup-pad cloning in the inliner
  (`collectPadBlocks`/`padInlinable`/`cloneInlinablePad`). Lifted a pre-existing
  limitation + removed the Inc-1 box-and-forward-wrapper inline regression. STAYS.
- `1dd3f319f` — Inc 2 first slice: recoverable overflow at OP_RODATA_ARRAY
  (string→array, BC_STRING_COPY_ARR) via a PER-OP pad+check. **SUPERSEDED by the
  reservation approach — TO BE REVERTED if the review comes back clean** (its
  `[N]char` growth would be reserved at pushFrame instead). Revert = the diff of
  1dd3f319f (attachSPGrowthPad in gen_local_cleanup.bn, the OP_RODATA_ARRAY arm in
  gen_temp_cleanup.bn:noteSPGrowingResult, the BC_STRING_COPY_ARR check in
  vm_exec_helpers.bn, the post-execStringOp wiring in vm_exec.bn, and the test
  TestSPGrowthArrayOverflowRecovers).
- `2b8066844` — composite-literal mid-init-fault leak (register the aggregate for
  cleanup BEFORE the element loop in genCompositeLit/genArrayLit/genManagedSliceLit).
  Independent of the SP-guard approach; STAYS.
- `89be1e76c` — emitTempCleanupSince missing the struct-dtor arm (`&&`/`||` operand
  aggregate-literal leak). Independent; STAYS.

## work-2 WIP (uncommitted-approach checkpoint)

- `e7ee47730` (on work-2, NOT landed) — small-growth per-op pads: extends
  `noteSPGrowingResult` to `attachSPGrowthPad` for all 5 SP-growing ops + adds
  `isRegisteredTemp` and the result-exclusion in `attachSPGrowthPad`. NO VM checks
  for the small-growth ops were added. **Expected to be RESET AWAY for the
  reservation approach** (it's the abandoned per-op direction). Preserved as a
  checkpoint per never-discard-WIP.

## NEXT STEPS (in order)

1. **Adversarial review of the RESERVATION approach** (in flight). Attack: is temp
   growth truly bounded by max-per-statement reclaimed at statement end? are ALL
   growths compile-time known (headers, literal sizes, call return sizes) except the
   cross-mode variadic? does folding `+MaxStmtTempGrowth` into pushFrame compose
   correctly with nested calls (each frame push independently checked on top of the
   caller's peak)? does the existing Plan-2 unwind stay leak-free when the ONLY
   fault site is pushFrame? interaction with Inc 1's `OP_STACK_CHECK` (the direct-call
   pre-check reservation should also include the callee's `MaxStmtTempGrowth`).
2. **If clean: revert the per-op work** — reset work-2 off `e7ee47730` (drop the
   WIP), and revert the landed `1dd3f319f` (per-op OP_RODATA_ARRAY check) via a
   fresh revert commit (needs cherry-pick approval to land). [Owner said: revert the
   per-op check if the review is clean.] Alternatively leave `1dd3f319f` as a
   redundant backstop — owner leaned toward reverting.
3. **Implement reservation:** compute `MaxStmtTempGrowth` per VMFunc at lower time
   (sum SP-growth per statement incl. call return-copy-back sizes; max over
   statements), store on VMFunc, and add it to the pushFrame/`OP_STACK_CHECK`
   reservation. Keep the cross-mode-variadic runtime check (make it a graceful
   terminal fault, not vmPanic). Tests + review + land.

## Inc 3a IMPLEMENTATION CHECKLIST (func-value pre-check, approach A) — post-compaction handoff

State: leak CONFIRMED + repro committed on work-2 as `79fd1d549`
(`pkg/binate/vm/vm_indirect_precheck_test.bn`, TestFuncValueOverflowNoLeak, RED until
this lands). Approach A settled + reviewed (see DESIGN REVIEW OUTCOME below). work-2
is otherwise clean on main (all of R1-R5 + follow-up landed). Do Inc 3a as its own
landable commit (func-value + nil-func-value); iface-method is Inc 3b.

Mechanism: emit a NEW pre-delivery op `OP_STACK_CHECK_FV` carrying the func-value
operand, BEFORE deliverCallArgs, with an args-owning cleanup pad (attachFaultPad).
Its VM handler resolves the callee from the func value and faults into that pad if
the callee's frame would overflow OR the func value is nil — before any dispatch.

Edits:
1. `pkg/binate/iropcode/opcodes.bn`: add `OP_STACK_CHECK_FV` as the NEW LAST opcode
   (after OP_STACK_CHECK; NUM_OPS shifts) + its OpName case ("stack_check_fv").
   Update `opcodes_test.bn` TestOpcodeEnumTailPinned: OP_STACK_CHECK_FV == NUM_OPS-1,
   OP_STACK_CHECK == OP_STACK_CHECK_FV-1, OP_PARAM == OP_STACK_CHECK-1.
2. `pkg/binate/ir/ir_ops.bn`: `EmitStackCheckFV(fnVal @Instr)` — an instr with
   Args=[fnVal] (via makeArgs1), void result, Op=OP_STACK_CHECK_FV.
3. Compiled-backend NO-OP (mirror OP_STACK_CHECK exactly) at ALL FOUR sites:
   codegen/emit_instr.bn:32, native/aarch64/aarch64_dispatch.bn:458 (add to the
   `case OP_NIL_CHECK, OP_STACK_CHECK:` list), native/arm32/arm32_dispatch.bn:278,
   native/x64/x64_dispatch.bn:453.
4. VM BC op: find the BC_* enum const block (grep the `BC_STACK_CHECK =` / iota
   definition) and add `BC_STACK_CHECK_FV`.
5. `pkg/binate/vm/lower_call.bn` (near the OP_STACK_CHECK arm ~17): lower
   OP_STACK_CHECK_FV -> BC_STACK_CHECK_FV; bc.Dst=-1; bc.Src1 = instr.Args[0].ID
   (the func-value register).
6. `pkg/binate/vm/lower_slots.bn` remapRegisters (~224): ensure BC_STACK_CHECK_FV's
   Src1 is remapped (BC_STACK_CHECK has no reg operands, so the new op needs Src1
   added to the remap set — VERIFY and add).
7. `pkg/binate/vm/vm_exec.bn` (near BC_STACK_CHECK handler ~428): BC_STACK_CHECK_FV:
   `var fv *int = bit_cast(*int, regs[instr.Src1])`
   - if fv==nil OR fv[types.FuncValueVtableIndex()]==0 -> nil func value:
     setFault(vm, "runtime error: call of nil function value"); pc =
     consumeFaultToPad(vm, f, pc); continue.
   - `var data int = fv[types.FuncValueDataIndex()]`; if data==0 -> native
     non-capturing (no VM frame) -> continue (no check).
   - `var rec *int = bit_cast(*int, data)`; if !closureRecIsVm(rec[0]) -> compiled
     closure / native -> continue.
   - `var fnIdx int = rec[2]-1`; if fnIdx in range AND
     wouldFrameOverflow(vm, frameReserve(vm.Funcs.Get(fnIdx))) ->
     setFault(vm, "runtime error: stack overflow"); pc = consumeFaultToPad(vm,f,pc);
     continue.
   (Helpers: closureRecIsVm in vm_trampoline.bn:16; FuncValueVtableIndex/DataIndex
   in types/layout_offsets.bn; frameReserve/wouldFrameOverflow in vm.bn.)
8. `pkg/binate/ir/gen_call.bn` genFuncValueCallWithFn (~415-447): today it calls
   buildCallArgs (gen_variadic.bn:124, which fuses evalCallArgs+deliverCallArgs).
   Split it: evalCallArgs -> `b.EmitStackCheckFV(fnVal)` -> `attachFaultPad(ctx, b)`
   (args-owning pad) -> deliverCallArgs -> EmitCallFuncValue -> attachFaultPad
   (post-call pad, existing). Mirror genCall's OP_STACK_CHECK sequence exactly.
   NOTE: fnVal is the func-value operand already genExpr'd by the caller
   (genFuncValueCall / genFuncValueCallExpr); it is BORROWED by the call (not
   consumed), so a fresh-temp fnVal stays a Temp and the args-owning pad RefDecs it
   once on fault (correct — verified in the design review Q2).
9. Tests (pkg/binate/vm): TestFuncValueOverflowNoLeak (repro, already present) must
   PASS. Add TestNilFuncValueMovedArgNoLeak: `gFn` left nil, call `gFn(cast(@I,
   make(T)))`, assert Status FAULTED + "call of nil function value" + stable
   LiveBlocks (M1). Verify non-vacuity for both (revert the check -> they fail).
10. Inliner: func-value calls are NOT inlined (inliner only inlines direct OP_CALL
    by name), so OP_STACK_CHECK_FV never appears in an inlined body -> no inliner
    change needed (unlike OP_STACK_CHECK's dropOrphanedStackChecks, which a new op
    sidesteps). VERIFY no inliner pass keys off it.
11. Validate: unit tests (ir, vm, codegen, native/{aarch64,arm32,x64} for the skip),
    hygiene, VM conformance (builder-comp-int). Adversarial review. Land (needs
    per-instance approval).

Inc 3b (later): the SAME pre-check for iface-method calls (genInterfaceMethodCall
in gen_iface_dispatch.bn) — there the callee IS resolvable from the receiver
vtable+slot; the review notes its push is in-loop/synchronous so it could even reuse
pushFrame's own fault, but the uniform A pre-check (OP_STACK_CHECK_FV-analog on the
receiver, or a shared op) is cleanest. Plus nil-iface + iface-method-overflow tests.

## Inc 3 — indirect-call frame-push moved-arg leak (SCOPE, 2026-09-15)

CONFIRMED LEAK (repro `TestFuncValueOverflowNoLeak`, pkg/binate/vm): a func-value
call recursing with a moved `@I` box leaks that box when the callee frame-push
overflows.  Root cause: direct calls emit a PRE-DELIVERY `OP_STACK_CHECK`
(gen_call.bn) whose cleanup pad still OWNS the args, so a pushFrame overflow
faults there and RefDecs them.  Indirect calls (func-value / iface-method /
OP_CALL_INDIRECT) have only a POST-call pad — attached AFTER `deliverCallArgs`
consumeTemp'd the moved args, so that pad does NOT own them; on pushFrame overflow
the moved arg is orphaned.  (This is exactly the leak Inc 1's OP_STACK_CHECK fixed
for direct calls; indirect calls couldn't use it because the callee is a runtime
value, not a static name.)

Why not just make the indirect CALL op's pad own the args: the SAME op also faults
on a NESTED (in-callee) fault, where the callee already owns the args — RefDec'ing
them there would double-free.  Direct calls avoid this with TWO ops/pads:
OP_STACK_CHECK (pre-delivery, owns args, fires on pushFrame overflow) + OP_CALL
(post-delivery, does NOT own args, fires on nested faults).  Inc 3 must give
indirect calls the same two-pad split.

CANDIDATE APPROACHES:
- **(A) Separate pre-check op that resolves the callee at runtime.** Mirror direct
  calls: emit a pre-delivery check op (before deliver, args-owning pad) carrying
  the func-value / receiver operand; a VM handler resolves the callee VMFunc from
  that runtime value and checks frameReserve, faulting into the pad on overflow.
  Uniform with direct calls, but DUPLICATES the dispatch handler's callee
  resolution (func-value vs closure vs iface-method vs native — native func values
  push no VM frame, so skip).
- **(B) Dispatch-handler check + second (args-owning) pad — RECOMMENDED.** The
  BC_CALL_FUNC_VALUE / BC_CALL_IFACE_METHOD handler ALREADY resolves the callee to
  push its frame, so it can check frameReserve itself with no re-resolution.
  IR-gen attaches a pre-delivery args-owning pad (before deliver) in ADDITION to
  the post-call pad; the handler dispatches to the args-owning pad SPECIFICALLY on
  the pushFrame-overflow path (it knows it's an overflow — pushFrame returned -1),
  and to the post-call pad on nested faults (callFaultPending after the call).
  Needs a way to carry two pads per call op (a second PadBlock field or a distinct
  fault-table key) + lowering support.  No duplicated resolution.

DESIGN REVIEW OUTCOME (2026-09-16): **B is NOT sound — switch to A.**
- **C1 (CRITICAL):** for FUNC-VALUE calls the dispatch handler does NOT resolve the
  callee or push the frame — the push happens remotely in a re-entrant `execFunc`
  via the native trampoline, which CLEARS `FaultRaised` before returning.  So an
  entry-frame overflow is indistinguishable at the dispatch point from a genuine
  nested fault (`callFaultPending` sees the same state), and they need OPPOSITE pad
  handling — B cannot route correctly and risks a double-free.  (B's premise holds
  only for iface-method, whose push IS in-loop/synchronous.)  The stale
  `execCallFuncValue` doc comments describe the old push-in-handler model B was
  written against.
- **M2:** B's two-pads-on-one-op collides with the PC-keyed FaultTable + inliner
  (`fixupInlinedPads`) + the `BC_UNWIND_RETURN` relay.  A (two ops → two PCs) needs
  none of that — reuses the direct-call machinery wholesale.
- **A's downside is illusory:** the func-value handler resolves nothing (A
  duplicates nothing there); iface-method re-resolution is ~8 lines.

**ADOPTED: approach A** — a separate pre-delivery pre-check op carrying the runtime
callee operand (func value / iface receiver); its VM handler resolves the callee
VMFunc, SKIPS native/compiled callees (they push no VM frame), and on
`wouldFrameOverflow(frameReserve(callee))` faults into its own args-owning pad,
BEFORE any dispatch.  Mirrors direct calls' OP_STACK_CHECK.

Additional required work from the review:
- **M1:** also route the "callee never entered" faults from a NIL func value / NIL
  iface value (with a moved managed arg) to the args-owning pad — same leak class,
  currently untested.  Rule: "callee never entered (overflow OR nil-value) →
  args-owning pad."  Add LiveBlocks tests: nil-func-value+moved-arg,
  nil-iface+moved-arg, AND an iface-method overflow (repro only covers func-value).
- **M3:** split the fused `buildCallArgs` (eval+deliver) at both indirect call
  sites (`genFuncValueCallWithFn` gen_call.bn, `genInterfaceMethodCall`
  gen_iface_dispatch.bn) to create the pre-delivery seam, as `genCall` already does.

Open questions to settle at start: (1) how to carry the
second pad (new ir.Instr field `PrePadBlock` vs a parallel fault-table entry);
(2) which ops need it (func-value, iface-method, and OP_CALL_INDIRECT — but the
last is only the magic scalar/aggregate shims per gen_call.bn, likely no managed
moved args → verify); (3) native/compiled func-value callees push no VM frame, so
the handler skips the check for them (only vm-func / closure callees push).

## Increments (original framing — retained for context; Inc 2 now = reservation)

- **Inc 1 — LANDED `7d610fdb6`** (see above). STAYS.
- **Inc 2 — temp-growth safety.** WAS per-op checks/pads; NOW the reservation
  approach (per-function MaxStmtTempGrowth reserved at frame entry). See above.
- **Inc 3 — INDIRECT calls** (func-value / iface-method / cross-mode re-entry):
  callee `frameExtent` (+ its MaxStmtTempGrowth) known only at the runtime dispatch
  point. Emit the reservation check at dispatch before the moved args are consumed,
  or release the in-flight moved args on overflow. Still open; decide after Inc 2.

## Notes

- Compiled backends have no recoverable stack overflow (OS guard pages) — they no-op
  `OP_STACK_CHECK` and ignore pads; the reservation logic is VM-lowering-only.
- Two pre-existing composite-literal managed-field leaks found during this work were
  fixed + landed (`2b8066844`, `89be1e76c`) — see claude-todo-done.md.
- The `rt.MemZero`/`123_raw_mem` VM-extern gap seen as the lone conformance failure
  throughout is UNRELATED and now claimed by another worker.

---

## Inc 3 + b2 REDESIGN (2026-09-16): robust indirect-call overflow recovery via a VM-func-value fast-path

Owner: work-2 (user reassigned b2 here 2026-09-16, to do WITH Inc 3).  Supersedes the
Inc 3a approach-A pre-check (`d80e5b927`, abandoned).

### The three defects approach A left / exposed

1. **Aggregate-return CRASH (SEGV), pre-existing.** An aggregate-returning callee
   dispatched through `TrampolinePacked` (func-value via `dispatchCompiledFuncValue`
   OR iface-method via `dispatchCompiledIfaceMethod`) SEGVs on an entry-`pushFrame`
   overflow: `execFunc` clears `FaultRaised` and returns 0, then TrampolinePacked's
   retbuf path does `rt.MemCopy(retbuf, bit_cast(*uint8, 0), ...)` — read from 0.
2. **Moved-arg LEAK on overflow.** Indirect calls deliver (consume) their moved
   managed args, then dispatch; on an entry-push overflow the fault unwinds to the
   call op's POST-call pad, which does not own the delivered args → leak.
3. **Approach A can't fix (2) reliably** because the dispatch grows `vm.SP` AFTER a
   pre-delivery check but BEFORE the remote push — by the aggregate retbuf
   (`AggregateReturnSize`) and by cross-mode iface-arg substitution scratch
   (`substArgSlotIface`, `align8(ByteSize)`/VM-index-iface-arg, runtime-dependent) —
   so a pre-check predicting the push SP under-predicts.  And approach A's handler
   classified VM-vs-native by peeking `data[0]`, which wild-derefs a native
   closure's untagged env (no `DATA_KIND_NATIVE_CLOSURE`).

### Root cause (single)

Indirect calls to a VM callee route through the NATIVE marshalling thunk
(`call_packed` → `TrampolinePacked` → `execFunc` → *remote* `pushFrame`).  That
marshalling is the sole source of (a) the transient SP growth that defeats
prediction, (b) the `FaultRaised`-cleared-across-the-boundary ambiguity, and (c) the
crash.  A DIRECT VM call (`BC_CALL`) has none of these: it pushes in the call arm at
the real SP, so its `OP_STACK_CHECK` pre-check is exact and recovery is trivial.

### Mechanism: VM-func-value FAST-PATH (this is b2) + exact recovery

Discriminate a VM func value by **thunk identity** — its vtable `call_packed` slot
(`FuncValueVtableCallPackedIndex()`, slot 2) equals the executing vm's registered
`TrampolinePacked` entry — NOT by peeking `data[0]`.  For a VM func value called from
VM bytecode, PUSH THE FRAME DIRECTLY in the call arm (resolve callee from the closure
record, marshal captures + packed user args into the callee's leading regs, push,
switch context) exactly like `BC_CALL`.  Everything else (native / compiled func
values) keeps the marshalling thunk.

Why this fixes all three at once:
- **Overflow detection is EXACT** — the fast-path pushes in the call arm at the real
  SP with no marshalling growth, so a pre-delivery check (`frameReserve(callee)` at
  current SP) or the in-arm `pushFrame` sees the true SP.  Recovery reuses the
  direct-call args-owning-pad path (the moved args are still owned pre-delivery).
- **No crash** — VM callees no longer reach `TrampolinePacked`'s retbuf MemCopy.
- **No `data[0]` wild-deref** — thunk identity is unambiguous; only AFTER confirming
  a VM func value do we read the closure record (`rec[1]=vm`, `rec[2]-1=fnIdx`).
- Native func values push no recoverable VM frame (OS guard pages) → no leak/crash.

### Honoring "b2 is OPTIMIZATION ONLY — never on the correctness path"

Correctness must NOT depend on the fast-path existing.  So ALSO make the marshalling
path safe independently:
- **Crash guard (mandatory, independent):** in `TrampolinePacked` (and
  `TrampolineAggregate`), after `execFunc` return, if `vm.Status == VM_STATUS_FAULTED`
  do NOT MemCopy — return 0 / propagate.  This alone converts the crash → graceful
  fault on the marshalling path.  Landable on its own.
- With the crash guard, disabling the fast-path degrades VM-func-value overflow to a
  graceful fault (possibly leaking the moved args in the rare marshalling case), NOT
  a crash.  So dispatch-correctness (any-arity) and crash-freedom do not depend on
  b2; only the LEAK-freedom of the (reachable) same-VM func-value-overflow rides the
  fast-path — acceptable because native-callee overflow isn't recoverable anyway and
  cross-VM VM-func-values (the only marshalling-path VM callees left) are not a
  constructible path today (flag if one is found).

### Staged implementation (each stage self-contained, tested, landable)

- **S1 — crash guard. LANDED `d2e21a89c` (2026-09-16).** `TrampolinePacked`/
  `TrampolineAggregate` skip the retbuf MemCopy on `Status == FAULTED` after
  execFunc.  Test `TestAggFuncValueOverflowNoCrash` (aggregate-returning func value,
  deep-recursion overflow → `Status = FAULTED`, no crash).  Adversarial review clean
  (matches the pre-existing `CallFuncAggregate` guard; both func-value and
  iface-method aggregate returns route through TrampolinePacked, so the one guard
  covers both).  Leak not yet asserted — S2.
- **S2 — VM-func-value fast-path (b2 core) + exact pre-check. LANDED `43b0acc37`
  (2026-09-16).** Thunk-identity discrimination (`vmFuncValueFnIdx`, cached
  `VM.TrampolinePackedCall`) in `execCallFuncValue`; direct in-arm frame push for
  same-vm VM func values (`fastPushVmFuncValue`: captures + packed args via
  `closureArgvPacked`, no retbuf, no iface-arg substitution); `OP_STACK_CHECK_FV`
  pre-check (args-owning pad), exact because the fast path has no marshalling growth.
  Split the fast-path/pre-check helpers into `vm/vm_funcvalue_fastpath.bn`
  (file-length).  Tests (`vm_funcvalue_fastpath_test.bn`): scalar + aggregate
  func-value overflow + nil-func-value moved-arg → `Status = FAULTED` + stable
  LiveBlocks (all non-vacuous).  Verified: vm units 408/0; `builder-comp-int`
  3022/0; adversarial review clean (skip-substitution is *more* correct — matches
  direct calls).  `builder-comp-int-int` couldn't COMPLETE in the env (time limit)
  but ran a large fraction at 0 failures; review verified nested-VM classifier
  soundness.  Honors "b2 optimization only" at the crash level (S1 guard); same-vm
  leak-freedom rides the fast path (user-approved).
  KNOWN GAP (pre-existing, not widened): DEFERRED func-value calls
  (`gen_defer_exit.bn`) emit no `OP_STACK_CHECK_FV`, uniform with deferred direct
  calls (no `OP_STACK_CHECK`) — a moved arg can still leak on a deferred call's
  callee overflow.  Minor perf: `vmFuncValueFnIdx` runs twice per fast-path call
  (pre-check + dispatch) — correctness-neutral.
- **S3 — iface-method parity (Inc 3b). LANDED `c990def66` (2026-09-17).** No
  fast-path was needed: the VM iface-method dispatch (`execCallIfaceMethod`) already
  pushes a VM iface value's callee frame directly (only a native iface value uses the
  marshalling `dispatchCompiledIfaceMethod`).  So S3 is purely the pre-check:
  `OP_STACK_CHECK_IM` (receiver in Args[0], method slot in IntVal) with an args-owning
  pad; `stackCheckIfaceMethod` resolves the callee from the receiver vtable + slot
  (mirroring `execCallIfaceMethod` 1:1) and faults on nil-iface or callee overflow,
  exact (no marshalling growth).  Tests (`vm_exec_ifacecall_test.bn`):
  TestIfaceMethodOverflowNoLeak + TestNilIfaceMethodMovedArgNoLeak (both non-vacuous).
  Verified: vm units 410/0; `builder-comp-int` 3025/0; adversarial review clean.

**WHOLE PLAN COMPLETE (2026-09-17).** Inc 1 (direct-call eval/deliver split +
OP_STACK_CHECK), Inc 2 (reservation R1-R5), and Inc 3 (crash guard S1 + func-value
S2/b2 + iface-method S3) are all landed.  A VM stack overflow now faults cleanly and
leak-free on every call path (direct, func-value, iface-method), and the
aggregate-return dispatch no longer crashes.  KNOWN GAP (pre-existing, out of scope,
not widened): DEFERRED indirect calls emit no pre-check (uniform with deferred direct
calls) — a moved arg can still leak on a deferred call's callee overflow.

### Risks / open questions to settle during S2

- **Thunk identity across NESTED VMs.** The reference address is the EXECUTING vm's
  `Externs[LookupExtern("pkg/binate/vm.TrampolinePacked")]` call_packed entry (a
  nested VM re-registers TrampolinePacked), not a bare `_func_handle(TrampolinePacked)`
  — cache it per-VM at registration.  Verify the nested-VM (VM-in-VM) case.
- **Capture marshalling for the direct push** must match `closureArgvPacked`
  (COMPILED_CLOSURE captures at `rec[3]` via CaptureOffsets/CaptureByPtr/CaptureWide).
- **Does `OP_STACK_CHECK_FV` survive?** Yes, repaired: classify by thunk identity, and
  it is exact because the fast-path has no marshalling growth.  Its pad is the
  args-owning pad; its handler faults only for a fast-path (VM) callee whose
  `frameReserve` overflows or a nil value; native callees skip.
- **Aggregate result relocation** on the fast-path uses execFunc's existing
  retbuf-relocation (image at top of callee frame → caller stack top); confirm the
  in-arm push path returns the result the same way `BC_CALL` does.

### Test carry-over

`pkg/binate/vm/vm_indirect_precheck_test.bn` (from d80e5b927): TestFuncValueOverflow-
NoLeak + TestNilFuncValueMovedArgNoLeak are black-box (compile Binate source, run,
assert Status/LiveBlocks) — they carry over unchanged and gate S2.  Add aggregate +
iface-method + nil-iface variants per stage.  A crashing probe cannot live in the
suite (it takes down the binary); once S1 lands, the aggregate case is a graceful
fault and testable.
