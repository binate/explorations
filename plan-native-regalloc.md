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
- **Stage 5 (further, additive):** interval splitting (add locations to ranges),
  spill-cost heuristics (use-density × loop depth), float register file (D8–D15 callee-saved),
  reclaim x64
  RCX/RDX, arm32 int64-in-registers.

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
