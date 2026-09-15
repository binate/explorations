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
