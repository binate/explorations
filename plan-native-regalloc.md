# Plan: a real register allocator for the native backend

> **Revision history.** v1 proposed a linear-scan allocator threaded "invisibly" through the
> existing emitter seam. An adversarial review (2026-09-01) found that premise false in
> correctness-critical places (call-result/param/return handlers write stack **slots**, not
> registers; x64 div/shift and aarch64 REM depend on pool order / read-after-write; the
> scratch pool can't be naively split — arm32's div-check needs all 7 pool registers at
> once), and that the interval model must be range-lists built from the liveness fixpoint,
> not `[firstDef,lastUse]` (wrong for loop-carried phi copies). This v2 folds those in. The
> algorithm (linear-scan), the shared-core-in-`common` split, and the aarch64→x64→arm32
> staging are unchanged.

## Problem & goal

The native backend (`pkg/binate/native/`) generates code by a **"spill everything"** policy:
`common.RegMap.PlanFrame` gives *every* scalar SSA value its own stack slot, and the per-arch
emitter uses a rotating scratch pool (`nextReg`) that is **reset after every instruction**
(`spillAndReset` → `ResetRegs`) and every block. Nothing is ever live in a register across an
instruction boundary — every operand is `ldr`'d from its slot and every result `str`'d back.
Self-documented placeholder: `common/common.bn:113-115` ("Correct but slow; a real allocator
can reclaim later").

This is the dominant native↔clang gap. Measured: a 6-value arithmetic loop `hotloop` emits
**80 memory ops** natively vs **8** with clang; on the bnc self-compile native `-O2` is ~9–12×
slower than clang and barely beats native `-O0` (the IR opt passes run — mem2reg promotes loop
vars — but the backend spills the result away).

**Goal:** a real register allocator so scalar values live in registers across their lifetimes,
spilling only under genuine pressure — native codegen into clang's ballpark. Build the
foundation once and extend it (splitting, coalescing, float regs); do not build something that
needs tearing out.

## Established facts (from codegen recon; anchors are the `temp-1` tree)

- **Flow:** `native.EmitObject → common.EmitObject → {ir.EliminatePhis(f); e.EmitFunc}`; per-arch
  `emitFunc` calls `rm.PlanFrame(f)`, emits prologue, then a per-block loop of `emitInstr` +
  `spillAndReset` (`common_emit_object.bn:23-32`, `aarch64_emit_func.bn:110-367`).
- **Phi-free input.** `ir.EliminatePhis` runs first (`common_emit_object.bn:30`): phis → `OP_COPY`
  moves, critical edges split. The copies for one phi all carry the **phi's SSA id**
  (`ir_phi_elim.bn:250-259`) — one id, several `OP_COPY` defs across predecessors. **Scalar-only,
  fail-loud** (`assertScalarPhi` panics on aggregate/managed/wide-int).
- **Allocatable universe (v2, precise):** an SSA id is register-allocatable iff it is a **scalar
  SpillID with NO AllocID**, is **not a float** (`!IsFloatScalarTyp` — see C6 below), and is not
  int64-on-a-32-bit-target (arm32 int64 stays spilled in v1). Everything else stays in memory:
  anything with an AllocID (`OP_ALLOC`, `OP_MAKE_SLICE`, aggregate params/results, aggregate SSA
  values held as pointers), and any address-taken alloca (no `OP_ADDR_OF`; `&x` reuses the alloca
  id and materializes `add rd,sp,#off`, `aarch64_regmap.bn:96-111`). Address-taken *scalars* are
  therefore already excluded (they are OP_ALLOCs). **A register-allocated value STILL keeps its
  stack slot** — the scalar `box()` path spills to the slot and boxes from that address
  (`aarch64_emit.bn:99-105`), and spilled/reloaded values need a home — so `PlanFrame` keeps
  allocating slots for all values.
- **Register inventory / reserved, per arch** (see recon): aarch64 free pool X9–X15 (caller-saved)
  + X16/X17 (currently unsafe fallback) + **X19–X28 callee-saved unused**; reserved SP/FP/LR/XZR/
  X18, arg X0–X7, ret X0, sret X8. x64 pool R10,R11,RCX,RDX,R8,R9,RDI; RAX reserved; **two-address**
  binops; DIV needs RDX:RAX; shift needs CL; **R12–R15 callee-saved unused**. arm32 pool R4–R10
  (**callee-saved, already prologue-saved**); R11=FP, R12/IP dedicated scratch, R13/14/15=SP/LR/PC;
  int64 = register pairs.
- **No liveness exists.** Must be built. Natural home: `common` (IR is arch-neutral).
- **VM impact: none.** `vm/lower_*.bn` uses only stateless `native/common` helpers, never
  `RegMap`/`PlanFrame`. **No DWARF** in the native backend, so no debug-location work.
- **Testing:** per-arch `*_emit_test.bn` assert byte-count/shape; three native conformance modes
  (`native_aa64`, `native_x64_darwin`, `native_arm32_baremetal`) gate correctness — codegen-clean
  baselines today (arm32 1 xfail; aa64/x64 8 distinct, all nil-deref/panic). Fail-loud (panic on
  pool exhaustion, `a.SetError` on unimplemented) surfaces regressions immediately.

## Algorithm: linear-scan over **range-list** live intervals

Linear-scan (liveness → intervals → scan → assign) is the right AOT baseline. Two v2 corrections
to make it the *right foundation* (not a redo):

- **R1 — range-list intervals.** Represent each interval as a **sorted list of live ranges**
  (LLVM `LiveInterval` / Wimmer style), even though v1 assigns a single location per interval.
  A single `[start,end]` would have to be replaced to support holes/splitting later; the
  range-list is the durable representation.
- **R2 — intervals from the liveness fixpoint, NOT def-use.** Build ranges from per-block
  live-in/live-out sets (backward dataflow to fixpoint over the phi-free CFG). `[firstDef,lastUse]`
  is *wrong* for a loop-carried phi id: in RPO the header **use** precedes the latch **def**, so a
  first-def-to-last-use interval ends before the back-edge def and the register is freed mid-loop →
  the latch `OP_COPY` clobbers a reassigned value. Liveness makes the value live-in across the whole
  loop (used-before-redefined in the latch), which is the only sound basis. One-register-per-id is
  fully compatible with several defs — that is the intent of the shared phi id.

**v1 assignment = whole-interval** (one register for a value's whole interval, or spilled entirely).
Interval *splitting* (register in the un-pressured sub-range, spilled elsewhere) is a Stage-5
refinement — but because intervals are already range-lists with per-range locations, splitting adds
locations to existing ranges rather than replacing the representation.

## The clobber & scratch model (the correctness core — v2's main addition)

The native backend **unifies "value registers" and "op-internal scratch" in one pool**, and the
current design stays correct only because it resets that pool every instruction. A real allocator
that keeps values in registers across instructions must therefore know, per instruction, which
registers each op **destroys** — both ABI-clobbered (calls) and scratch the handler grabs. This is
the standard "instruction clobber set / regmask" model, and it subsumes several review findings.

Each arch supplies **`clobbers(ins) → set of physical registers`** the lowering of `ins` destroys.
The **arch-neutral** half — which ops emit a call that can RETURN to the next instruction (across
which caller-saved is not preserved) on EVERY backend — is `common.EmitsReturningBl(op)`, landed and
adversarially-audited in Stage 0. The decisive criterion is "does the emitted call **return** to the
following instruction," NOT "does it emit a BL" — that distinction is what excludes the fault checks
that only call a noreturn fail path.

- **Returning-call ops clobber all caller-saved (arch-neutral, in `EmitsReturningBl`).** Verified
  against the actual lowering in all three backends: OP_CALL*/OP_C_CALL/OP_SAT_LOOKUP/iface,
  OP_MAKE (`rt.Alloc`), OP_BOX (`rt.Box`), OP_MAKE_SLICE (`rt.MakeManagedSlice`),
  **OP_RODATA_MSLICE_COPY** (owned-literal `rt.MakeManagedSlice`), **OP_STACK_FRAMES**
  (`rt.CaptureNativeFrames`), the **UNCONDITIONAL** guards OP_DIV_CHECK/OP_SHIFT_CHECK
  (`rt.DivCheck`/`rt.ShiftCheck`, return on valid input), and **OP_REFDEC** (`rt.ZeroRefDestroy`,
  conditional dtor slow path but it RETURNS).
- **EXCLUDED — NOT clobbers:** OP_BOUNDS_CHECK (inline compares + a conditional branch to a cold,
  **noreturn** `rt.BoundsFail`; the in-bounds path executes no call — this corrects v2's earlier
  claim that bounds-check was a caller-saved clobber) and OP_NIL_CHECK (a native no-op). Verified
  identical on all three backends.
- **PER-ARCH additions (NOT arch-neutral — each backend's descriptor must add these on top of
  `EmitsReturningBl`, or it silently keeps values in caller-saved regs across them):**
  - **x64:** **OP_REFINC** — x64 calls `rt.RefInc`, whereas aarch64/arm32 inline the bump. (Stage 3.)
  - **arm32 (soft-float / ILP32):** int64 **OP_MUL/OP_DIV/OP_REM/OP_SHL/OP_SHR** (`__aeabi_l*`) and,
    under soft-float, float **OP_ADD/SUB/MUL/DIV**, the float comparisons, and float **OP_CAST**
    (`__aeabi_[fd]*`). aarch64/x64 use hardware int/FP for these. Note 32-bit OP_DIV/OP_REM on the
    cortex-a15 target use hardware SDIV/UDIV — no call. (Stage 4.)
  - **aarch64 needs none** — the arch-neutral core alone is its complete returning-call set, so
    Stage 1 is safe consuming `EmitsReturningBl` directly.
- **x64 fixed-register ops clobber their fixed regs:** div/rem → {RAX,RDX}; shift → {RCX}.
- **Scratch-hungry handlers clobber their working set:** arm32 `emitDivCheck64` clobbers R4–R10 (7);
  aarch64 `emitStructCopy` clobbers 3; etc. (enumerate per handler in each arch's stage, alongside
  its physical register-class descriptor).

The allocator's contract, per instruction `ins` with clobber set `C`:
1. No value **live across** `ins` (live-in ∧ live-out, i.e. not defined/killed here) may occupy a
   register in `C`. The scan enforces this by making `C` unavailable for any interval whose range
   spans `ins`; such a value takes a non-clobbered register (callee-saved) or spills.
2. The handler's transient scratch is drawn **from `C`** (guaranteed free by (1)), so scratch never
   collides with a persistent value.

**Reserved scratch for ordinary ops.** Most ops need only enough scratch to reload spilled operands
and land a spilled result. Reserve a small fixed scratch set per arch (aarch64: X16/X17 — but see
C3: stop hardcoding X16 elsewhere; x64: reserve a pair; arm32: R12/IP is already the reserved
scratch) sized to the **worst ORDINARY op** (reload ≤2 operands + a result temp for read-after-write
lowerings like REM). Special ops (div-check64, struct copy, div, shift) declare a larger `clobbers`
set instead of drawing from the tiny reserved set.

This model **is** the fix for C2/C3/C4: call-clobber, rt-op clobber, x64 fixed regs, and per-handler
scratch demand are all one mechanism.

## Handlers that CHANGE (v2 is explicit; "untouched" was false)

The op handlers that *read* operands via `getOperand` keep working (getOperand returns the home
register or reloads a spilled value). But these handlers **produce/land** values into stack slots
today and must be changed to land into the value's **home register** when it is register-allocated
(falling back to slot when spilled):

- **Call results:** `collectScalarReturn` (aarch64_call.bn:370-379) `Str X0→slot` → must `Mov home←X0`
  (or Str→slot if spilled). Same for float returns and `collectMultiReturnFields`
  (aarch64_call.bn:304-362).
- **Parameters:** the entry prologue (aarch64_emit_func.bn:162-350) `Str`s each incoming arg reg to
  its slot → a register-allocated param needs an **arg-reg → home-reg move at entry** (G1), and
  must move off X0–X7 before the first clobbering op.
- **`OP_RETURN` value production** where it stages results.
- **Read-after-write lowerings must respect `rd ∉ {operands read after the write}`** (C5): aarch64
  **OP_REM** (`Sdiv rd; Msub rd,rd,rhs,lhs` re-reads lhs/rhs) and x64 two-address (`mov rd,lhs;
  op rd,rhs` when `rd==rhs`) and x64 div. Enforce via an allocator constraint (don't give `rd` a
  register equal to an operand read after the def) or a scratch temp for the intermediate.
- **Stop hardcoding pool registers by identity/order** (C3): aarch64's hardcoded `X16`
  (aarch64_emit_func.bn:279, aarch64_call.bn:56/146/155) must move to the declared scratch set; x64
  shift/div must **name** RCX/RDX/RAX explicitly and declare them clobbered, not fish them out of the
  rotating pool (which the allocator no longer advances).

Everything else (`emitBinop` arithmetic, `getOperand` reads, address materialization) is untouched.

## Register classes (per-arch descriptor)

Each arch supplies a small descriptor the shared allocator consults (never physical numbers):
caller-saved-allocatable, callee-saved-allocatable, reserved-scratch, reserved (never allocated),
and `clobbers(ins)`.

- **aarch64:** caller-saved-alloc {X9–X15}; callee-saved-alloc {X19–X28} (prologue save/restore what
  is used); scratch {X16,X17}; X0–X7 ABI-only (not allocated — transient at calls, clobbered anyway);
  X8 sret; SP/FP/LR/XZR/X18 reserved.
- **x64:** caller-saved-alloc {R10,R11,R8,R9,RDI}; callee-saved-alloc {R12–R15}; **RAX/RDX/RCX
  reserved** (ret + div + shift), declared in the relevant ops' clobber sets; a reserved scratch
  pair. (Reclaiming RCX/RDX via clobber-modeling is a Stage-5 refinement.)
- **arm32:** allocatable {R4–R10} (already callee-saved & prologue-saved) for scalars; R0–R3 ABI-only;
  R12/IP reserved scratch; int64 **spilled in v1** (its handlers need the whole pool as scratch, so
  a live int64-in-registers is infeasible until those handlers shrink — do not regress the ~1-xfail
  baseline).

## Architecture: shared core, per-arch descriptor

- **Shared in `common/` (new):** linearization (RPO, handling **unreachable blocks** — G3: skip or
  give empty allocations so `getOperand` never returns −1 for them), liveness (fixpoint), range-list
  interval construction, and the linear-scan assignment loop — all arch-neutral (read IR + the
  register-class descriptor + `clobbers`). Store the **stable** per-id → location (register or
  spilled) in an `Alloc` table on/beside `RegMap`, distinct from today's transient `IDs/Regs`.
- **Per-arch:** the register-class descriptor + `clobbers(ins)`; the changed landing/scratch handlers
  above; prologue/epilogue callee-save; frame-layout update (G2: the callee-saved save area shifts
  SP-relative offsets — `stackArgsBase`/spill/alloc/outgoing must all account for it consistently in
  `PlanFrame`).

## Staging (incremental; each stage lands green & cherry-pickable)

- **Stage 0 — the reusable core in `common`, no emission change. DONE — landed on main
  (`3bf3ac146`), adversarially reviewed.** `regalloc_liveness.bn` (RPO linearization + unreachable
  handling, allocatable-scalar universe, backward liveness **fixpoint**), `regalloc_interval.bn`
  (**range-list** intervals from the fixpoint + a validator), and `regalloc_clobber.bn`
  (`EmitsReturningBl` — the arch-neutral returning-call set). The validator checks well-formedness,
  def/use coverage, AND (driven independently from the liveness sets) that each interval covers every
  position the value is live — the pass-through-hole check. Unit-tested compiled + under the VM: the
  **loop-carried phi-copy-shared-id** interval, a genuine within-block dead hole, a forced 5-way
  pressure overlap, the fixpoint's loop convergence + upward-use guard, and that the validator catches
  a dropped live range. Three-reviewer adversarial pass found the code sound and fixed two clobber-set
  omissions (OP_RODATA_MSLICE_COPY, OP_STACK_FRAMES — now in the set) and the per-arch clobber points
  recorded above; the bounds-check/nil-check exclusion was verified SOUND on all three arches. No
  codegen change → all modes green. Per-arch **physical** register-class descriptors are deferred to
  each arch's stage (they are emission-coupled, not arch-neutral).
- **Stage 1a — arch-neutral linear-scan assignment in `common`. DONE — landed `54d53251c`.**
  `regalloc_scan.bn`: `RegClassDesc`, `ClobberPositions`, whole-interval `LinearScan` with expiry.
  No emission change; unit-tested (no-overlap invariant, pressure spill, clobber-span, spansClobber).
- **Stage 1b — aarch64 register allocation wired into emission. DONE — landed `f4bb7f4b7`.**
  **REORDERED from the plan (adversarially reviewed, sound):** homes come from the **callee-saved**
  bank X19–X28, NOT caller-saved X9–X15 — disjoint from the X9–X17 scratch pool (existing scratch
  path untouched) and callee-saved so a homed value survives calls (no clobber-spill). This folds the
  plan's Stage 1 (caller-saved) and Stage 2 (callee-saved) into one step, adding prologue/epilogue
  save/restore. `AllocateRegisters` runs the pipeline; getOperand/nextReg use a persistent home-map;
  PlanFrame reserves the save area inside the frame; params/call-results land in home registers; the
  landing handlers (C1) and REM read-after-write (C5) are handled; floats/aggregates excluded (C6).
  **New bug class found + fixed:** ops that mutate an OPERAND register in place —
  `emitRefIncInline`'s pre-index writeback `LDR [ptr,#-16]!` corrupted a homed pointer (safe only
  under spill-everything); fixed to SUB-into-scratch. The pre-landing 3-reviewer sweep found no other
  instance. Validated: `native_aa64` conformance 2995/0; bnc self-compiles natively; -O2 `hotloop`
  keeps loop-carried values in registers. Regression test `conformance/1231_regalloc_managed_ptr_refinc`.
  Note: since all homes are callee-saved, the clobber machinery (`spansClobber`/`ClobberPositions`)
  is present but inert on aarch64 — it activates when a stage populates caller-saved (Stage 5).
- **Stage 3 — x64 register allocation wired into emission. DONE — landed `712241d57`.**
  Same callee-saved-first reorder as Stage 1b: homes = RBX/R12–R15 (disjoint from the R10..RDI
  scratch pool), `CallerSaved` empty so the clobber machinery is inert (the plan's x64 OP_REFINC
  clobber is deferred to Stage 5 with caller-saved homes). `AllocateRegisters` before `PlanFrame`;
  getOperand/nextReg home fast-path; prologue save / epilogue restore; scalar-param home-landing.
  **Two operand-mutation bugs found + fixed:** the SHL/SHR count relied on `scratchReg` landing on
  RCX after two getOperands (breaks when an operand is homed or register-cached → shift by garbage
  CL) → now moves the count to RCX explicitly and reserves the pool cursor past it; and
  `emitUint64ToDouble` did `and src,1` in place, mutating the integer operand → now uses a second
  scratch. Regression `conformance/1233_regalloc_shift_homed_operands` (bites the shift bug). The
  emitUint64ToDouble fix has no bespoke test (triggering needs the uint64 homed, which x64 spills
  for every constructible shape — disassembly-confirmed); covered by 1233 + full conformance +
  1193/1226. Pre-landing adversarial review: no miscompiles. Validated: `native_x64_darwin`
  conformance 2996/0.
- **Stage 4 — arm32 register allocation wired into emission. DONE — landed `d49bd66a2`.**
  Unlike aarch64/x64 (callee-saved homes), arm32 has **no free callee-saved register** — R4–R10 is
  the scratch pool getOperand hands out and R11 is the frame pointer — so homes are **caller-saved**
  (R0–R3), and the clobber machinery, inert on aarch64/x64 (empty `CallerSaved`), goes **active for
  the first time**. New arch-neutral piece: a **type-aware** clobber classifier (`RegClassDesc`
  gains `Int64OpsClobber` / `SoftFloatOpsClobber`; `isClobberInstr` flags arm32's AEABI libcalls —
  int64 MUL/DIV/REM/SHL/SHR and int64↔float CAST always, plus float arith/compare/cast under
  soft-float — by operand *type*, so int32 arithmetic isn't over-clobbered); aarch64/x64 leave both
  flags false (unchanged). No prologue save/restore (caller-saved). **The core correctness rule:**
  R0–R3 homes overlap the arg/target/return registers, so a homed value marshalled *into* those
  registers would be clobbered by the marshalling, and >1 forms a permutation the naive per-`mov`
  order corrupts — so emitFunc **un-homes** the operands of every op that marshals into R0–R3 (the
  `EmitsReturningBl` call family + `OP_RETURN`); they revert to spill (read from slots, disjoint from
  R0–R3). The int64/soft-float libcalls need no un-homing because their operands (int64/float) are
  non-allocatable. Params land via spill-then-reload; call results collected home-aware.
  **One diagnostic-only defect found in pre-landing review + fixed:** `OP_BOUNDS_CHECK`'s cold
  `rt.BoundsFail(idx,len)` marshalling wasn't permutation-safe (a `len` homed in R0 corrupted the
  panic message's length) — fixed by staging len through IP (chosen over un-homing, which would
  despill the hot indexing path for a cold diagnostic); regression
  `TestDispatchBoundsFailMarshalIsPermutationSafe`. Two independent adversarial reviews (clobber-set
  completeness; exclusion/result/param/rodata) otherwise clean; the review also confirmed the
  emitStringToArray R0→pool-scratch change fixes a *pre-existing* miscompile. Validated:
  `native_arm32_baremetal` conformance 2953/0 (1-xfail baseline held); aarch64/x64 unaffected.
- **Stage 5a — caller-saved homes — TRIED aa64, NEUTRAL, SHELVED (2026-09-02).**
  Hypothesis: the native-specific cost is spill-heavy hot LEAF functions (charsEqual, streq,
  symHash) that pay prologue/epilogue save/restore for their callee-saved homes even though they
  never call anything, so also homing non-call-spanning values in caller-saved registers (no
  save/restore) would close gap.  Implemented on aa64: home in the DISJOINT arg bank (X0–X7, not
  the X9–X17 scratch pool — no scratch-pool split needed), keeping X19–X28 callee-saved homes for
  call-spanning values.  **Result: NEUTRAL on the native self-compile (15.44s vs 15.41s), so
  shelved** (correct + conformance-subset-green, but not landable as a gap-closer).  Preserved on a
  local branch (not on main).  Why neutral: (1) the leaf save/restore savings are a handful of
  str/ldr per call — negligible even for hot tiny leaves; (2) the added-homing-budget rarely
  prevents spills, since 10 callee-saved homes already cover most functions' pressure.  So
  caller-saved homes is not a meaningful aa64 gap-closer — the remaining ~2.5× wants something else
  (see the general-throughput / codegen items).
  - **Two real bugs found while implementing (both are general lessons, not caller-saved-specific
    once understood):** (a) aa64's param landing did a DIRECT arg-reg→home-reg move, which permutes
    and corrupts params once a home overlaps the arg registers — a whole-program miscompile of bnc;
    the fix is spill-then-reload (or a direct move only for the disjoint callee-saved homes).
    (b) `emitStringToArray` used X0 as the inline-byte-store base; OP_RODATA_ARRAY is not a clobber,
    so a value homed in X0 live across it would be corrupted — fixed to a pool scratch.
  - **Key design correction (the 5× regression):** the naive "un-home call operands after
    allocation" spills them; on call-heavy code that despills the callee-saved homes they used to
    get → **5× slower**.  The correct shape is to bar call/return operands from a caller-saved home
    at ALLOCATION time (a per-value caller-saved-INELIGIBLE set in LinearScan) so they route to
    callee-saved (or spill), exactly like a call-spanning value — they keep their home.  This
    `excludeCallerIDs` allocator capability is the reusable part if caller-saved homes is ever
    revisited.
- **Stage 5b — copy coalescing — TRIED aa64+x64, NEUTRAL, SHELVED (2026-09-03).**
  Implemented: a `coalesce` flag on AllocateRegisters builds `copySrc[dstId]` (the first
  RPO-order OP_COPY source per dst); LinearScan, when assigning a copy dst whose source's register
  is still free (source died at the copy → no interference), REUSES it so the move is `mov r,r`,
  which the backend elides (x64 already did; aa64 added).  Correct (native unit tests + aa64
  loop-heavy conformance subset green; unit tests pin the hint).  **Result: NEUTRAL** on the
  self-compile (16.85 vs 17.20s, interleaved) → shelved (preserved on a local branch).
  **Why neutral — the instructive part: the LIFO free pool already coalesces the common case for
  free.** A source that dies at its copy is the most-recently-freed register, so the dst reuses it
  naturally with no hint (perf/005_slice_sum's compiled binary was BYTE-IDENTICAL with and without
  coalescing).  The explicit hint only helps rare NON-adjacent copies (a handful of movs across the
  whole compiler, absorbed by function-alignment padding), and the per-function copySrc build is
  slight overhead — hence neutral-to-slightly-negative.
- **META (after two neutral Stage-5 refinements — caller-saved homes AND copy coalescing):** v1's
  simple heuristics (whole-interval assignment, LIFO free pool, callee-saved homes) already
  captured the codegen-quality wins.  Further register-allocation refinements are NOT expected to
  close the remaining ~2.5× native↔clang gap; that gap is now dominated by things register
  allocation can't touch (e.g. clang's vectorization of the byte/word memory loops).  Interval
  splitting / spill-cost heuristics below are likely the same story; float register allocation is
  the one untried item with a distinct mechanism (float scalars are non-allocatable today).
- **META CORRECTION (2026-09-08, adversarial per-hot-function attribution of the native `-O2`
  self-compile, main `9fa1a37ff`, host aarch64):** the META note above is WRONG that the remaining
  gap is "dominated by things register allocation can't touch (clang's vectorization)." Measured:
  clang emits ZERO compute-vector ops (no `add.4s`/`cmeq`/`uminv`) — its ~35K q-register
  instructions are wide aggregate copies / zero-init in COLD functions, none in the hot path, so
  vectorization is ≈ 1–2% of the gap. Excluding a ~33%-of-runtime shared floor (`rt.MemZero` is
  byte-identical N vs L, plus malloc/dyld/kernel/irreducible dataflow), the *active* gap splits
  **~53% aggregate-copy** and **~45% scalar spill/reload**. The aggregate-copy half — slice/struct
  locals materialized and copied field-by-field through stack slots (308,903 mem→mem copy-pairs =
  25.6% of N's instructions, 41.6% of them 4-word managed-slice-header copies, 94% internal locals
  and NOT ABI-mandated) — is by design UNREACHABLE by register allocation (aggregates are
  non-allocatable), which is exactly why the two neutral Stage-5 experiments couldn't move it; it
  needs an IR-level **SROA + copy-propagation** pass (see the todo "Native codegen quality" entry).
  The scalar-spill half IS register-allocator work and is still OPEN via the untried knobs
  (spill-cost heuristics / interval splitting / more homes) — the SHELVED caller-saved-homes and
  coalescing were the wrong knobs, not proof the allocator is tapped out (e.g. `livenessFixpoint`,
  the #1 hot function, reloads its receiver from `[sp]` on every field access where clang holds it
  in a register). So the biggest single lever is SROA, NOT register allocation; SIMD
  (`plan-native-vectorization.md`) is low-value.
- **Stage 5 (further, additive):** interval splitting (add locations to ranges),
  spill-cost heuristics (use-density × loop depth), float register file (D8–D15 callee-saved),
  reclaim x64
  RCX/RDX, arm32 int64-in-registers.

- **Stage 5c — spill-cost eviction (increment 1: static use-count). ✅ LANDED `fb215bf79`
  (2026-09-17, work-4/temp-4).** Effect (native aa64 self-compile of cmd/bnc, interleaved
  5-round, host idle): native-backend median 18.45s → 16.03s, narrowing the native/LLVM
  code-quality ratio from **3.86× to 3.34×** (~13% faster; best-case ~18%; LLVM build
  unchanged at 4.78s, so the gain is entirely the native regalloc change — a real dent in
  the ~45% scalar-spill half of the gap, unlike the neutral 5a/5b).  Validated: native
  aa64 3037/0, arm32-linux 3037/0, x64_darwin 3256/0 (4 shards); native/common unit tests
  (+3 eviction tests); adversarial review clean (inductive no-overlap proof).

- **Stage 5c — increment 2 (loop-depth weighting). ✅ LANDED `c82f31b6d` (2026-09-18,
  work-4/temp-4).** The flat static count under-values a value used FEW times statically but
  inside a hot loop.  `computeSpillCosts` now weights each def/use by ~10^(block loop depth,
  capped at 4) via the new `ir.ComputeLoopDepths` (natural loops of the CFG back-edges, on the
  existing dom.bn dominance data).  Effect (native aa64 -O2, a high-register-pressure loop:
  14 straight-line values live across a loop whose accumulator+counter are used once/iteration):
  **~3.7× (0.63s → 0.17s)**.  DISASSEMBLY-confirmed — under the flat count the loop spills the
  accumulator+counter and reloads them ~10×/iteration; under loop-weighting they stay in
  registers (x25/x24) with ZERO loop stack-traffic, spilling the straight-line values (read once,
  post-loop) instead.  **Neutral on the compiler's own self-compile** (it is not a
  high-pressure-loop workload) — so it helps loop-heavy code at no cost elsewhere.  Validated:
  native aa64 3037/0, arm32-linux 3037/0, x64_darwin 3256/0 (4 shards); ir + native/common unit
  tests (incl. 3 loop-depth CFG tests); adversarial review clean (termination/bounds/nesting +
  index-space alignment verified).  **METHODOLOGY LESSON (cost me a wrong "shelve" call, corrected
  after the owner pushed):** the initial benchmark timed kernels compiled WITHOUT `-O2`, so the
  regalloc never ran — two identical -O0 binaries measured "neutral."  The regalloc only engages
  at -O2; always benchmark at -O2 and DISASSEMBLE to confirm the allocation actually changed before
  concluding a lever is neutral.  **Perf follow-up (open):** `ComputeLoopDepths` calls
  `ComputeDom(f)`, rebuilding succs/preds/RPO that the liveness pass already builds for the same
  `f` — two CFG traversals per `AllocateRegisters`.  Harmless (self-compile neutral) but shareable;
  a future increment could thread one CFG/dominance build through both.

The landed `LinearScan` never evicted before increment 1: when the eligible pool
  is exhausted it spills the *current* interval (`reg = -1`), regardless of how hot it is. So a
  hot value that needs a callee-saved register but arrives after the callee-saved pool is full of
  colder long-lived spanning values gets spilled — the `livenessFixpoint`-receiver case (a
  clobber-spanning param, live+used across the whole function, evicted by colder spanning values).
  Add eviction with a static spill-cost:
  - **Cost proxy:** `useCounts[id]` = number of instructions that def-or-use `id` (one IR pass in
    `AllocateRegisters`). A direct proxy for spill cost (each use → a reload, each def → a store);
    higher = keep in a register. Loop-depth weighting (`use-density × loop-depth`) is increment 2
    (needs back-edge/loop detection) — increment 1 is the flat static count, which already fixes the
    livenessFixpoint case (its receiver has many static uses).
  - **Decision:** when no register is free for the current interval C, find the eligible active
    interval A with the MINIMUM cost; if `cost(A) < cost(C)`, evict A — set A's result to spilled
    (`Reg = -1`), give C the register A held, and replace A's active-set entry with C's extent.
    Else spill C (unchanged). **Equal costs ⇒ no eviction ⇒ byte-identical to today** (so the
    existing scan tests, which carry no per-id cost, stay green under a uniform count).
  - **Register-class eligibility (the correctness core):** a clobber-spanning C needs a
    callee-saved register, so it may only evict actives *holding a callee-saved register*
    (evicting a caller-saved holder would hand C a register the call it spans destroys). A
    non-spanning C may evict any active (it takes whatever class the freed register is; landing a
    non-spanning value in a callee-saved reg just costs one prologue save, already tracked). The
    evicted value goes to memory, so ITS class no longer matters — a spilled clobber-spanning value
    still survives the call in its stack slot.
  - **No-overlap invariant preserved:** C takes a register freed by spilling A; any other interval
    D overlapping C coexisted with A and thus already holds a different register, so C (= A's old
    reg) never collides with D. Assignment is computed whole-interval before emission, so eviction
    only rewrites A's and C's final decisions — no other interval is perturbed.
  - **Validation:** unit tests (eviction fires hot-over-cold; respects the callee-saved constraint;
    equal-cost ⇒ spill-newcomer unchanged) + three native conformance modes (aa64 / x64_darwin /
    native_arm32_baremetal — a wrong assignment is a silent miscompile) + a native self-compile
    benchmark (does it actually narrow the ~45% spill half?) + adversarial review.

## Compiler-gap disassembly diagnosis (2026-09-18, work-4/temp-4)

Disassembled `livenessFixpoint` (a top-hot compiler function) native `-O2` vs LLVM `-O2`
(both `--linker clang` so symbols survive), to characterize the gap that remains on the
COMPILER's own (control-flow-heavy) code — where loop-depth weighting is neutral.

    metric (livenessFixpoint)      native   LLVM
    instructions                     652     381
    scalar stores -> stack           153       5      <-- ~30x
    scalar loads  <- stack            82     ~29
    aggregate copies (ldp/stp)       118      40
    aggregate-source copies (ldp)     58       7      <-- LLVM SROA'd them away
    total stack accesses             237      47

Two large, distinct gaps: (1) **scalar spilling — the register allocator's domain** —
native stores 153 scalars to the stack vs LLVM's 5; native homes only 10 values
(callee-saved X19–X28; `CallerSaved` is EMPTY) and runs close to the store-every-def
baseline, while LLVM uses the full ~27-register file.  (2) **aggregate/slice-header copies
— SROA's domain (work-1)** — native copies 4-word headers through stack scratch 58x vs
LLVM's 7; register allocation cannot touch these (aggregates are non-allocatable).

**The scalar-spill gap (153 vs 5) directly contradicts Stage 5a's shelving justification
("10 callee-saved homes already cover most functions' pressure").**  Given the increment-2
`-O0` mismeasurement caught the same day, the Stage 5a "neutral" verdict is SUSPECT — likely
a mismeasurement and/or an artifact of its pool choice (it homed in the call-churned arg
bank X0–X7, only 8 registers, few of which survive across 18 calls).  Next lever (owner
picked option 1, 2026-09-18): re-open caller-saved homes — re-test the shelved Stage 5a impl
(`shelved-stage5a-caller-saved-homes`, `69d650f41`) at `-O2` WITH disassembly (does the
153-store count drop? does the self-compile improve?), then, if warranted, the proper form
(a non-arg caller-saved pool + barring call/return operands from a caller-saved home at
allocation) and eventually interval splitting (the real LLVM technique — caller-saved regs
for the non-call portions of long call-spanning intervals).  The aggregate-copy half stays
work-1's SROA.

**RESOLUTION (2026-09-18): caller-saved homes is the WRONG lever for the compiler; Stage
5a's neutral was CORRECT.**  Recovered Stage 5a (`69d650f41`) onto inc1/inc2 (clean
cherry-pick) and examined the baseline `livenessFixpoint` spill sites: the ~30 spilled
scalars are loop-invariant values computed in the prologue and used ACROSS the loop body's
18 calls — CALL-SPANNING.  A caller-saved home is clobbered across a call, so
`spansClobber` (correctly) never gives one to these values; caller-saved homes only help
the FEW non-call-spanning temporaries, which is why Stage 5a measured neutral — and would
again.  (Aside: the recovered Stage 5a did not cleanly compose with the inc1/inc2 eviction
— its native self-compile OOM'd/crashed while a 72-test aa64 subset passed 0-fail; a naive
recovery is not viable regardless.)  **So the scalar-spill half of the compiler gap needs
INTERVAL SPLITTING** — split a call-spanning interval so it holds a caller-saved (or
callee-saved) register for its non-call use-clusters and spills/reloads only across the
calls, as LLVM does.  The landed range-list `LiveInterval` is the intended foundation
(its header: "the range-list is the durable representation interval splitting later
refines").  This is a substantial Stage-5 project.

**RE-VERIFIED against a current-main compiler (2026-09-18, after the full SROA line landed
DONE the same day) — corrects the "aggregate is the bigger lever" framing.**  Rebuilt
`livenessFixpoint` native `-O2` with a current-main `bnc` (full SROA + inc1/inc2) and
re-disassembled:

    livenessFixpoint       inc2-base   current(full SROA)   LLVM
    instructions              652            555             381
    aggregate-src copies       58             24               7
    scalar stores -> stack    153            123               5   <-- still ~25x

SROA IS done and DID help (652→555 instrs, 58→24 aggregate copies), but the native/LLVM
self-compile RATIO is UNCHANGED — 3.38× median now vs inc1's 3.34× (within noise).  So the
aggregate-copy half is largely addressed and is NOT the remaining lever; the **dominant
remaining compiler gap is SCALAR SPILL (123 stores-to-stack vs LLVM's 5, ~25×)** — native
homes only 10 values and stores the rest to their slots, and the spilled values are
call-spanning loop-invariants.  **Interval splitting is therefore THE lever for the
compiler's remaining native gap, not a secondary one.**  (The ratio being flat while a hot
function shrank shows the ratio is a blunt aggregate; the per-function store counts are the
sharper signal.)

## Stage 5d — caller-saved homes in the ARG BANK X0–X7 (parallel-move) — IN PROGRESS (2026-09-18, work-4/temp-4)

**Supersedes the "home in X9–X15 (static partition)" sketch below.**  Measuring killed that
approach: the scratch pool (X9–X16) is needed by every op, and homes drawn from X9–X15 collide
with it.  Two floors were measured over the whole self-compile: the max scratch a single op
claims is **6** (a 4-word aggregate `OP_STORE`, inline ldp/stp), and the max for
**non-retention-safe** ops (calls/refdec/div-check — the ops that marshal into or otherwise
can't cede the arg bank) is **4**.  A static X9–X15 home/scratch split therefore caps at ~2–3
homes (≈15% of the spill cost) — too little.

**The lever is the ARG BANK.**  X0–X7 (8 caller-saved registers) sit idle except at call
marshalling; used as **caller-saved homes for non-spanning values** they give ~18 homes (10
callee-saved X19–X28 + 8 arg-bank) — reaching peak-pressure ≤18, ≈40% of the loop-weighted
spill cost (measured band table: ≤12 → 11%, ≤15 → +8%, 16–20 → +28%).  This is **Stage 5a done
right**: 5a *did* home in X0–X7 but neutralized itself by **un-homing every call operand** to
dodge the arg-marshalling permutation (in call-heavy code most non-spanning values ARE call
operands, so they reverted to spilling → neutral).  Keep those homes; marshal correctly instead.

**Why the allocator side is already correct.**  `LinearScan` gives `CallerSaved` only to
non-spanning intervals (spanning ones draw from `CalleeSaved`, and the eviction path keeps a
spanning current interval to callee-saved holders), and `AllocateRegisters` records only
callee-saved homes in `SavedRegs`.  So a value homed in X0–X7 is provably non-spanning — dead
before / born after every call it reaches — and needs no prologue save.  A call clobbering its
home register is harmless because it is dead there.  Populating `desc.CallerSaved = {X0..X7}`
needs no scan change.

**The two marshalling hazards (the whole risk).**  A value homed in X0–X7 has its home register
IN the arg/return bank, so the naive per-slot moves permute:
1. **Param landing** — incoming params arrive in X0–X7; a param homed in a *different* X0–X7
   register than it arrived in permutes with its neighbors.  Fix: **spill-then-reload** — store
   every incoming param to its slot, then load each homed param from its slot into its home
   register.  No permutation (sources are memory); runs once at entry.  (This is what 5a already
   did for params.)
2. **Call marshalling** — an operand homed in X0–X7 must move to its arg register (also X0–X7);
   >1 such operand forms a permutation the naive per-arg `mov` corrupts.  Fix: a real
   **parallel-move** (sequentialize the register→register arg moves, breaking cycles through one
   scratch temp, e.g. X16).  This ~30-line routine is the one genuinely miscompile-prone piece;
   everything else is mechanical.  The X9–X16 scratch pool is **untouched** by this stage (lower
   risk than a scratch-pool rewrite).

**Increments (validate each: three native conformance modes — a wrong assignment is a silent
miscompile — plus native/common unit tests):**
1. **Param landing → spill-then-reload** for register-allocated params (independent of the arg
   bank; a no-op refactor while homes are still callee-saved-only, so it validates in isolation).
2. **Parallel-move call marshalling** — replace the per-arg reg→arg `mov`s with a
   sequentialized parallel move (still a no-op while no operand is homed in an arg register, so
   it too validates before the payload).
3. **Flip on the homes**: `desc.CallerSaved = {X0..X7}`.  Now (1) and (2) carry the weight.
   Disassemble `livenessFixpoint` to confirm its 137 non-spanning spills convert to X0–X7 homes
   (store count drops); interleaved self-compile benchmark (does the ratio move ~toward 40% of
   the spill half?).
4. Then x64 / arm32 (their caller-saved pools + marshalling differ; arm32 already has a
   caller-saved-home model — reconcile).

**Composes with a later X9–X13 arg-bank-SCRATCH pass** (frees ~3 more homes → ~21 total, ~55%)
and with **interval splitting** for the ≥27-pressure third (~33%) that no home count can cover.

### Stage 5d residual miscompile — ROOT-CAUSED AND FIXED (2026-09-19, work-4/temp-4)

The native self-compile hang (below) was a **register-allocator bug in `spansClobber`**, fixed
on branch `temp-4` at `9b9edd930` (a standalone allocator commit, no file overlap with the
arg-bank commit `6bf481432`, so it can land first / independently).

**Root cause (disassembly-confirmed).**  A value LIVE-IN to a block has its live-range `Start`
clamped to the block-entry position (`BlockFrom[b]`), which is the same linear position as the
block's FIRST instruction.  When that first instruction is a clobber (a call), `spansClobber`'s
guard `if p <= Start { continue }` treated the value as *born at the clobber* (Start == clobber
position) and cleared it to NOT span — even though a parameter (or any live-through value) is born
EARLIER and genuinely spans the call.  The allocator then handed it a **caller-saved register**,
which the call destroys.  Silent miscompile.

Confirmed in `irdata.DataZero(n)` (`var t = make(DataTerm); t.Kind = DT_ZERO; t.Width = n`): `n`
was homed in **X7** (arg bank), the opening `make()`→`rt.Alloc` (OP_MAKE, position == n's range
Start) clobbered X7, and the stale X7 (Alloc left the new object's header pointer `t-0x10` in it)
was stored as `t.Width`.  lldb dump of the corrupted DataTerm: `Kind=3` (DT_ZERO, correct),
`Bytes`/`IntVal`/`Sym`/`Addend` all null (correct), **`Width = t-0x10`** (a pointer) — a
single-field corruption, not whole-object UAF.  The garbage width drove the multi-GB `Assembler.Fill`.

**Why the earlier notes were wrong.**  This section previously claimed "Allocation is SOUND — a
precise per-instruction BADHOME check found ZERO caller-saved homes spanning a clobber" and "the
bug is NOT a spansClobber/interval misclassification."  Both were **false** — the BADHOME probe
shared `spansClobber`'s blind spot (a live-in value whose block opens with the clobber has
`Start == clobber pos`, so a birth-vs-live-in test keyed on `Start` misses it), a textbook case of
an assertion and the allocation sharing one buggy predicate (the exact failure the "drive the
bring-up assertion INDEPENDENTLY" note warns about).  The "runtime UAF / premature `emitRefDec`"
and "source UB that BUILDER lowers differently than gen1" hypotheses were also wrong: the value in
`Width` was `t-0x10` because a clobber-spanning caller-saved home was read back after the call, not
because anything was freed.  (gen2-native hung and gen3-native did not simply because they were
built from different source trees at different points, not because of a generation-specific
front-end difference.)

**The fix.**  Key the birth test on the value's true DEFINITION position (`DefPos`), not its range
`Start`.  `DefPos` is the defining instruction's position, or `-1` for a parameter (no in-function
def).  A clobber `P` is a birth only when `P == DefPos`; a parameter's `DefPos` is never a clobber
position, so it correctly spans.  Only the clobber sitting exactly at `Start` is reclassified —
every other clobber keeps the prior `Covers(P+1)` test, so holed / loop-carried intervals are
untouched.  `LiveInterval` gains a `DefPos` field (populated in `BuildIntervals`); a regression
test (`TestScanSpansClobberLiveInAtBlockStart`) covers the live-in-at-block-start case.

The misclassification was **inert on main** (empty `CallerSaved` ⇒ span-vs-not both reduce to
callee-saved), which is why it never bit before the arg-bank home pool made caller-saved homes
real — and why the fix is a safe no-op to land ahead of the arg-bank commit.

**Verified GOOD after the fix:**
- `DataZero`'s `n` now homed in **X28** (callee-saved, prologue-saved) and survives `rt.Alloc` —
  disassembly-confirmed.
- Native aa64 self-compile of cmd/bnc: gen2-native → gen3 completes in ~10 s (was: 3.6 GB balloon,
  killed at 48 s).  gen3 → gen4 completes in ~14 s; gen3 and gen4 are byte-identical except the
  Mach-O ad-hoc code-signature identifier (derived from the output filename) — a true
  self-compilation fixpoint on the actual code/data.
- `pkg/binate/native/common` unit tests pass (incl. the new regression test).
- Native aa64 conformance **3040 passed / 0 failed / 9 skipped** on the final reconstructed
  branch (fix + arg-bank, hygiene 20/20).
- Independent adversarial review of the spansClobber fix: **confirmed correct** — no unsafe or
  over-conservative case (the birth-skip `p == DefPos` is safe because a clobber-defined value is
  single-def with Start == DefPos, and the only multi-def ids are phi OP_COPYs, never clobbers).

Both Stage 5d commits LANDED on main: the spansClobber fix (`348cb15aa`) and the arg-bank homes
(`4eed9523a`).  The arg-bank commit's own pre-land hygiene was also fixed:
`aarch64_call.bn` (427→520 over the 500 cap from the new marshalling helpers) split into
`aarch64_call_return.bn` (return-value collection, whitelisted per the length-split precedent);
`argReg` / `emitReturn` doc comments (accidentally dropped when the arg-bank funcs were inserted
above them) restored; two bnfmt-dirty files reformatted.  Also caught + fixed: main's refactored
`emitDivCheck` / `emitBoundsCheck` used naive `Mov X0,a; Mov X1,b` marshalling that the arg-bank
homes miscompile on a crossing — ported to the parallel move in `aarch64_guards.bn`.  The arg-bank
marshalling got its own independent adversarial review (parallel moves at every call/return/
refdec/guard site, spill-then-reload param landing, X16/X17 cycle-temp freedom, PlanParallelMove)
— confirmed correct, no miscompiles.

**Gap impact (controlled before/after, `perf/native-vs-llvm.sh`, cmd/bnc self-compile, 5 rounds,
same machine, arg-bank commit is the ONLY difference):**
- WITHOUT arg-bank homes (parent `9699bb18e`): native median 9.515s, LLVM median 3.069s →
  ratio **3.10×** (best 3.08×).
- WITH arg-bank homes (main `4eed9523a`): native median 8.837s, LLVM median 3.053s →
  ratio **2.89×** (best 2.79×).
- LLVM side unchanged (3.069→3.053s), so the delta is entirely native codegen: the native
  self-compile is ~7% faster (9.52s→8.84s median) and the native↔LLVM ratio narrowed 3.10×→2.89×
  (~10% of the excess-over-1× closed).  A real but modest narrowing — arg-bank homes attack the
  SPILL cost (~75% of it non-call-spanning); the remaining ~1.89× excess is the genuinely
  call-spanning values (interval-splitting follow-up), instruction selection, and aggregate copies.

## Correctness & validation (miscompile is the top risk)

A wrong assignment is a **silent** wrong-register read. Front-load validation:

- **Bring-up assertion, driven INDEPENDENTLY of the interval-crossing predicate** (C4 corollary):
  enumerate clobber points from `clobbers(ins)` and assert no live allocatable value sits in a
  clobbered register there. If the assertion and the allocation shared one (buggy) predicate it would
  be blind — so derive them separately during bring-up.
- **Three native conformance modes** after every stage (codegen-clean baselines → regressions show
  immediately as failures/xpass).
- **Per-arch unit tests** updated to the new emit shape, pinning: a clobber-crossing value →
  callee-saved; a forced spill; the phi-copy-shared-id loop interval; the REM/two-address
  read-after-write constraint.
- **USER-CPU benchmark** (native self-compile of cmd/bnc, aarch64, no Rosetta) after Stages 1/2;
  target: from ~9–12× toward clang's ballpark.
- **Adversarial review of each stage's diff.**

## Sharp edges (call out in each stage's review)

1. **Clobber-set completeness (C4)** — REFDEC/MAKE/BOX/MAKE_SLICE/fault-checks are BLs; missing one =
   corruption. Assertion must be independent.
2. **Scratch starvation (C2)** — the reserved scratch set must cover the worst *ordinary* op; special
   handlers declare bigger clobber sets. arm32 div-check64 = 7 (whole pool) → values live across it
   spill.
3. **Landing into home registers (C1)** — call-result/param/return handlers must stop writing slots
   for register-allocated values.
4. **Read-after-write (C5)** — aarch64 REM, x64 two-address/div: `rd` ∉ operands-read-after-write.
5. **Loop-carried phi-copy-shared-id (R2)** — one id, defs in latch + pre-header; interval from the
   liveness fixpoint or the register frees mid-loop.
6. **Frame layout (G2)** — callee-saved save area shifts every SP-relative offset consistently.
7. **Float exclusion (C6)** — a float scalar matches "scalar non-alloc SpillID"; must be filtered out
   or its boundary handlers (which shuttle GP↔slot) leave a home register stale.
8. **Hardcoded pool registers (C3)** — aarch64 X16, x64 RCX/RDX-by-order — must become declared.
9. **Param placement (G1)** — arg-reg → home-reg at entry, off X0–X7 before the first clobber.
10. **Unreachable blocks (G3)** — RPO won't reach them; don't leave their operands unallocated.

## Non-goals (v1)

Float register allocation, interval splitting, copy coalescing, graph-coloring, arm32
int64-in-registers, reclaiming x64 RCX/RDX — all Stage 5, additive on this foundation.

## Stage 6 — interval splitting (call-spanning values): DESIGN (2026-09-19, work-4/temp-4)

Claimed as claude-todo item 2b.  Design first; discuss the approach + expected payoff before
implementing (this section is that design).

### Where the cost is, and what "splitting" would capture

Stage 5d homed the NON-call-spanning values in the arg bank (X0–X7) — measured ~75% of the
loop-weighted spill cost — and moved the self-compile ratio 3.10×→2.89× (median).  The remaining
~25% of spill cost is values LIVE ACROSS a call.  Those either (a) win one of the ≤10 callee-saved
homes (X19–X28) — best case, one prologue/epilogue save + free reads everywhere — or (b), when that
pool is exhausted, fully SPILL: they live in a stack slot and `getOperand` reloads them on use.

Crucial nuance about (b): a spilled value is NOT reloaded on *every* textual use.  The within-block
retention cache (aarch64_regmap.bn `allocReg`/`getOperand`/`LookupReg`) reloads it into a scratch
pool reg (X9–X15) on first use in a block and reuses that reg for subsequent uses in the SAME block,
dropping the cache at (i) any clobber op (call) and (ii) every block boundary.  So the reload
frequency a spilled call-spanning value actually pays is roughly "once per (block ∩ call-free)
region," not once per use.  **Interval splitting's incremental win over what already exists is
therefore only: (1) carry the value in a register ACROSS block boundaries within a call-free region
(the cache drops at every block end today), and (2) give it a DEDICATED register so it isn't evicted
by op-scratch pressure — reloading only across actual calls.**  That is a fraction of the 25%.

### The architectural constraint

The emitter resolves an operand to a location in exactly two modes (aarch64_regmap.bn):
- HOME: `LookupHome(id)` → ONE stable physical reg for the value's WHOLE interval (callee-saved or,
  since 5d, arg bank).  No reload, no per-point variation.
- SPILL: slot + reload-on-use + within-block cache.
`AllocateRegisters` maps each LinearScan assignment to a single (id→reg) home or leaves it spilled.
There is no per-program-point location.  Real interval splitting breaks the "one location per value"
assumption, which is baked into `LookupHome` and every `getOperand`/`nextReg` caller.

### Options

**A. Full per-point splitting (Wimmer/LLVM style).**  Split each interval's range-list at clobbers
into sub-intervals; allocate each independently; the emitter resolves a use to the sub-interval
covering the current position; insert resolution moves (spill/reload) at split points AND reconcile
locations at block boundaries (a value may be in a reg on one CFG edge and a slot on another).
Highest fidelity, captures the full 25%.  Cost: a large change to BOTH the allocator (range-list
splitting, per-range location, boundary reconciliation) and the emitter (position-aware location
lookup replacing `LookupHome`; a resolution-move pass).  Block-boundary reconciliation is the hard
part and is genuinely new machinery.

**B. Caller-saved home + per-call-site save/restore ("caller-saved homing").**  Let a spanning value
take a caller-saved / arg-bank reg for its WHOLE interval (fits the one-reg `LookupHome` model
unchanged), and have the CALLER save/restore it around each call it spans: `STR R,[slot]` before the
call's marshalling, `LDR R,[slot]` after.  vs full-spill this reloads only after each CALL (not each
call-free region boundary) and needs no scratch-cache eviction; vs a callee-saved home it pays a
save+restore at EACH spanned call instead of once in the prologue.  So B wins when a value spans FEW
calls but is read MANY times between/around them, and LOSES (vs full-spill or callee-saved) when it
spans many calls.  The allocator must choose B vs full-spill per value from the spill-cost model
(uses-between-calls vs number-of-spanned-calls).  Emitter change is moderate: at each clobber site,
save/restore the caller-saved-homed values live across it (a localized version of the callee-saved
prologue save).  Captures only the "few spanned calls, dense between-call use" slice of the 25%.

**C. Cross-call-free-region retention.**  Extend the within-block cache to survive block boundaries
WITHIN a call-free region, with the allocator pinning a dedicated caller-saved reg per hot spilled
value.  This needs block-boundary location agreement (a value must be in the same reg on all CFG
edges into a block, or get a reconciliation move) — i.e. it reduces to the hard part of A.  Not
meaningfully simpler than A once done correctly.

### Recommendation + honest payoff

The measured reality argues for caution: the 75%-of-spill-cost lever (arg-bank homes) moved the
ratio only 3.10×→2.89×, so **spill is a minor contributor to the 2.89× total** — the bulk is
instruction selection, aggregate/slice-header copies, and the optimizations LLVM does that the
native backend does not.  Interval splitting targets the remaining 25% of *spill* cost, of which the
existing within-block cache already captures the within-block-reuse part — so its realistic ceiling
is a SMALL ratio move (order 0.05–0.1×, i.e. ~2.89×→~2.8×), for a large (option A) or cost-model-
delicate (option B) change.

Two honest paths to put to the user:
1. **Proceed with interval splitting**, starting with **option B** (tractable, fits the home model,
   directly targets the dense-use / few-spanned-calls slice), measured behind `native-vs-llvm.sh`;
   escalate to A only if B's measured gain justifies the boundary-reconciliation machinery.
2. **Redirect** to a higher-value gap component (instruction selection, the aggregate/slice-header
   copy path) that the 2.89× breakdown suggests dominates — interval splitting stays a lower-priority
   follow-up.  This is NOT a unilateral deferral: it is a scope question for the user, given the
   measured evidence that spill is no longer the dominant gap term.

### Stage 6 — option B RESULT: net REGRESSION, not landed (2026-09-19, work-4/temp-4)

Implemented option B (caller-saved home + per-call save/restore for spanning values) and measured
it against its own baseline, controlled (`perf/native-vs-llvm.sh`, cmd/bnc self-compile, 7 rounds
each, back-to-back same machine, LLVM side stable — base L median 3.464s vs option-B L median
3.493s, ~1% apart, so the comparison is trustworthy):

- WITHOUT option B (`d3018f084`): native median 9.674s → ratio **2.79×**.
- WITH option B (`f1efe2b35`): native median 10.410s → ratio **2.98×**.

**Option B REGRESSED the ratio 2.79×→2.98× (native ~7.6% slower); NOT landed.**  It IS correct
(native aa64 conformance 3040/0, self-compiles + gen3 fixpoint) — just slower.  Root cause: Binate
is refcount-heavy, so nearly every spanning value spans `OP_REFDEC` clobbers, and the emitter
save/restores a caller-saved home around EVERY spanned clobber — but a RefDec's call
(rt.ZeroRefDestroy) is CONDITIONAL (fires only when the refcount hits 0, the rare case), so on the
common fast path those save/restore pairs are pure overhead the reload savings don't recover
(+17 KB of save/restore code).  The cost model `2*SpanWeight < spillCost` counts RefDec spans at
full weight but the emission can't be made conditional without pushing the save/restore into the
RefDec slow path (a bigger change), and excluding conditional-call clobbers would disable option B
for almost all spanning values in refcount-heavy code anyway.

**This confirms the pre-implementation analysis: spill is no longer the dominant gap term, and
this form of interval splitting is a poor fit for a refcounted language.**  The option-B commit is
kept on the work branch as a record but is NOT for landing.  Higher-value next levers (per the
2.79× breakdown): instruction selection and the aggregate/slice-header copy path.

### Stage 6 — option B, after the (a)+(b) fix: STILL a ~4% regression (2026-09-19, work-4/temp-4)

Fixed the earlier regression's worst cause: RefDec's save/restore moved to its slow path (fast path
pays nothing), RefDec excluded from SpanWeight, and the whole caller-saved-spanning-home path gated
on a new `RegClassDesc.SplitSpanningHomes` flag that ONLY aarch64 sets.  (That flag also fixed a
real cross-arch bug the first cut introduced: the shared LinearScan change would have given arm32 —
caller-saved R0..R3, NO save/restore machinery — a caller-saved spanning home, a miscompile; now
x64/arm32 are provably unchanged.  Correct: native aa64 self-compiles + gen3 fixpoint; allocator
unit tests + gate test pass.)

But a CLEAN measurement — noise-immune user-CPU, ALTERNATING order (cancels ordering bias), PAIRED
per round (controls the loaded shared machine's thermal throttling), 12 rounds — shows option B
is STILL a net regression: mean(optB - base) = **+0.40 s on ~11 s user-CPU (~3.6%), 10 of 12 rounds
slower**.  (Wall-clock was too noise-dominated on this machine to read; user-CPU paired is the
trustworthy metric — the user directed using it.)

Root cause: the gate `2*SpanWeight < spillCost` OVERVALUES the benefit.  `spillCost` is the
loop-weighted def+use COUNT, but the within-block retention cache already eliminates within-block
reloads, so a spilled value's REAL reload cost is much smaller — only its cross-(call-free-region)
reloads.  The gate therefore homes values whose per-call save/restore cost exceeds their true
reload savings.  A correct gate would compare 2*SpanWeight against a reload-aware benefit (the
number of call-free regions in which the value is used), not raw spillCost.

Bottom line: this is the SECOND independent confirmation (after the arg-bank result) that spill is
no longer the dominant native↔LLVM gap term, and that the retention cache already captures the
easy reload wins — leaving interval splitting a small, cost-model-delicate lever.  Option B stays
on branch `optB-regression` / the `temp-4` commit; NOT landed.  Decision pending: refine the gate
to a reload-aware benefit estimate (bounded upside), or shelve and profile the dominant gap terms
(instruction selection, aggregate/slice-header copies).
