# Plan: comprehensive VM SP-guard (fault safely on stack overflow; never leak/corrupt)

Status: IN PROGRESS (work-2 / session). Started 2026-09-09. **APPROACH CHANGED
2026-09-14 to the RESERVATION model (see below) after the per-op approach's cost was
surfaced.** Owner: this session (work-2).

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
  owner sign-off.
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
- **R2 — per-function `MaxStmtTempGrowth`.** At lower time walk the IR summing
  `spGrowthBytes` over OP_SP_RESTORE-delimited regions, take the max; store on
  VMFunc. Unit-test. Also add, in the appropriate regions, the two deferred
  call-context growths: (i) the callee-side return-image build,
  `types.AggregateReturnSize(f.Results)`, in the return statement's region (per
  function); and (ii) the cross-mode arg-substitution scratch, sum over each
  call's `ArgIfaceLayout` slots of `align8(ByteSize)`, in that call's region (per
  call site). Folding (ii) into the reservation also FIXES the currently-unchecked
  substArgSlotIface overflow gap (see the MAJOR todo entry).
- **R3 — fold into the reservation.** `pushFrame` / `wouldFrameOverflow`
  reserves `frameExtent + MaxStmtTempGrowth`; the direct-call `OP_STACK_CHECK`
  pre-check adds the callee's `MaxStmtTempGrowth`. Overflow caught only at frame
  entry (clean Plan-2 unwind). Test: a big-per-statement function faults cleanly
  at entry, not mid-statement.
- **R4 — remove the redundant per-op machinery** = revert the landed
  `1dd3f319f` (OP_RODATA_ARRAY per-op check/pad) + the `attachSPGrowthPad` path.
  Lands AFTER R3 (needs its own cherry-pick approval).
- **R5 — cross-mode `...*any` scratch** (the one runtime-sized growth): make its
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
