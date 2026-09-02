# Plan: a real register allocator for the native backend

## Problem & goal

The native backend (`pkg/binate/native/`) generates code by a **"spill everything"**
policy: `common.RegMap.PlanFrame` gives *every* scalar SSA value its own stack slot,
and the per-arch emitter uses a rotating scratch pool (`nextReg`) that is **reset after
every instruction** (`spillAndReset` → `ResetRegs`) and every block. Nothing is ever
live in a register across an instruction boundary — every operand is `ldr`'d from its
slot and every result is `str`'d back. The policy is self-documented as a placeholder
(`common/common.bn:113-115`: "Correct but slow; a real allocator can reclaim later").

This is the dominant cause of the native↔clang gap. Measured: a 6-value arithmetic loop
`hotloop` emits **80 memory ops** with the native backend vs **8** with clang (which keeps
everything in registers); on the bnc self-compile, native `-O2` is ~9–12× slower than
clang and barely beats native `-O0` (the IR opt passes run, but the backend discards
their result — mem2reg promotes loop variables to registers-in-principle, then the
backend spills them all).

**Goal:** replace the placeholder with a real register allocator so scalar values live in
registers across their lifetimes, spilling only under genuine pressure — getting native
codegen into the same ballpark as clang. This is a foundation to build once and extend,
not a throwaway.

## What the allocator operates on (established facts)

From the codegen recon (anchors are current `temp-1` tree):

- **Flow:** `native.EmitObject → common.EmitObject → {ir.EliminatePhis(f); e.EmitFunc}`;
  per-arch `emitFunc` calls `rm.PlanFrame(f)`, emits prologue, then a per-block loop of
  `emitInstr` + `spillAndReset` (`common_emit_object.bn:23-32`,
  `aarch64_emit_func.bn:110-367`).
- **Phi-free input.** `ir.EliminatePhis` runs before emission (`common_emit_object.bn:30`):
  phis become `OP_COPY` moves; critical edges are split. So the allocator sees a **phi-free
  CFG**. Subtlety: the copies for one phi all carry the **phi's SSA id**
  (`ir_phi_elim.bn:250-259`) — one id, several defining `OP_COPY`s across predecessors.
  Live-range construction must treat these as one value.
- **Allocatable universe = scalar SSA values.** Exactly the non-aggregate, non-alloc
  `SpillIDs`: int / bool / raw-pointer (and float, see below). This coincides with
  `EliminatePhis`' `assertScalarPhi`-accepted set. **Must stay in memory regardless:**
  anything with an `AllocID` — `OP_ALLOC`, `OP_MAKE_SLICE`, aggregate params/results,
  aggregate SSA values (represented as pointers into a data region), and any address-taken
  alloca (the IR has no `OP_ADDR_OF`; `&x` reuses the alloca id and materializes
  `add rd, sp, #off` on demand, `aarch64_regmap.bn:96-111`). Fault pads are VM-only and
  irrelevant to the native path.
- **The seam** the allocator threads through is `getOperand` / `nextReg` / `spillAndReset`
  in each arch's `*_regmap.bn` — the op handlers call these and are otherwise arch code we
  do NOT rewrite.
- **Register inventory (free/reserved), per arch:**
  - **aarch64** (`aarch64_regmap.bn`): pool today = X9–X15 (7, caller-saved) + X16/X17
    (IP0/IP1, unsafe fallback). Reserved: SP, X29/FP, X30/LR, XZR, X18. **X19–X28 (10
    callee-saved) entirely unused** — the prologue saves only FP/LR. Arg X0–X7, ret X0,
    sret X8, FP args/ret D0–D7.
  - **x64** (`x64_regmap.bn`): pool today = R10,R11,RCX,RDX,R8,R9,RDI (7). RAX reserved
    (ret + IMUL/IDIV). **Two-address**: binops emit `mov rd,lhs; op rd,rhs`. **DIV/REM**
    need RDX:RAX; **shifts** need count in CL. R12–R15 callee-saved unused. sret ptr in RDI.
  - **arm32** (`arm32_regmap.bn`): pool today = R4–R10 (7, **callee-saved, already saved in
    prologue**). R11=FP, R12/IP = dedicated scratch, R13/14/15 = SP/LR/PC. 64-bit ints are
    multi-register (`arm32_int64.bn`); args R0–R3, sret in R0.
- **Fixed-register constraints** the allocator must respect (§5 of recon): call arg passing
  (X0–X7 / D0–D7, overflow to outgoing stack), call results (X0 / D0 / X0..N / X8-sret),
  `OP_RETURN`, x64 DIV (RDX:RAX) and shift (CL), and the rt-call fault checks
  (`OP_DIV_CHECK`/`OP_SHIFT_CHECK`/`OP_BOUNDS_CHECK` pass args in X0–X3), `OP_MAKE`/`OP_BOX`
  (X0/X1, result X0). Every `BL` clobbers all caller-saved regs; today the
  `ResetRegs`-after-every-call discipline enforces that.
- **No liveness analysis exists** anywhere today — it must be built from scratch. Natural
  home: `common` (IR blocks/instrs are arch-neutral).
- **Testing surface:** per-arch `*_emit_test.bn` assert emitted **byte counts / shapes**
  (not exact bytes); three native conformance modes gate end-to-end correctness
  (`native_aa64`, `native_x64_darwin`, `native_arm32_baremetal`). Current xfail baselines
  are essentially codegen-clean: arm32 = 1 xfail, aa64/x64 = 8 distinct (all
  nil-deref/panic/interp cases, not codegen). Pool exhaustion and unimplemented ops
  **fail loud** (panic / `a.SetError`), so regressions surface immediately.

## Algorithm: linear-scan, whole-interval (v1), extensible

Choose **linear-scan over live intervals** — the standard "right" baseline for an AOT
backend not chasing peak. Graph-coloring is more work for marginal gain at this stage;
crucially, linear-scan's core (liveness → intervals → scan → assignment) is the *same*
infrastructure that the later refinements (interval splitting, coalescing) extend, so
this is a foundation, not a throwaway.

**v1 = whole-interval assignment**: each allocatable value is either (a) assigned ONE
physical register for its entire live interval, or (b) **spilled** (lives in its stack
slot; loaded into a scratch register on each use, stored on each def — i.e. today's
behavior, but only for the values that couldn't get a register). This already keeps the
common case (loop-carried and straight-line temps) in registers. Interval *splitting*
(register when possible, spilled only in the pressured sub-range) is a later additive
refinement over the same intervals — explicitly deferred, not a redo.

### Pipeline (all per-function, after `EliminatePhis`, before emission)

1. **Linearize + number.** Order blocks in reverse-postorder; number instructions. Loops
   must nest contiguously enough that a value live across a back-edge gets a live interval
   spanning the loop (standard).
2. **Liveness.** Backward dataflow over the phi-free CFG: `live-out(b) = ∪ live-in(succ)`;
   `live-in(b) = use(b) ∪ (live-out(b) − def(b))`. Only allocatable scalar ids. The
   phi-copy-shared-id case: an id defined by multiple `OP_COPY`s is one value; its uses and
   all its defs contribute to one interval.
3. **Build intervals.** For each allocatable id, a live interval (contiguous [start,end]
   in v1; holes are a later refinement) from first def to last use, extended across any
   loop it is live around. Record, per interval, whether it **crosses a call** (spans any
   call instruction) — this drives the caller/callee-saved decision.
4. **Assign registers (linear scan).** Walk intervals in start order, maintaining an active
   set. For each interval, expire finished actives (free their regs), then:
   - Pick a free register from the appropriate class (below). If none, **spill** the
     interval whose end is furthest away (Poletto-Sarkar heuristic) — either the new
     interval or an active one — marking the loser spilled.
   - **Register class by call-crossing:** an interval that crosses a call must take a
     **callee-saved** register (survives the clobber) or be spilled; an interval that never
     crosses a call may take a **caller-saved** register (cheaper — no prologue save). This
     is the whole reason to unlock X19–X28 / R12–R15.
5. **Emit.** The per-arch `getOperand`/`nextReg`/`spillAndReset` consult the assignment:
   a register-allocated value uses its stable register (no reset, persists across
   instructions); a spilled value loads into / stores from a scratch. The
   per-instruction/per-block `ResetRegs` for allocated values goes away; a small **scratch
   set** (e.g. aarch64 X16/X17) is reserved for reloading spilled operands and for the
   existing address-materialization.
6. **Prologue/epilogue.** Save/restore exactly the callee-saved registers the allocation
   used (aa64/x64 add these; arm32 already saves its R4–R10 pool). Extend the frame layout
   for the saved-register area.

### How fixed-register constraints & call-clobbering are handled

The recon shows the op handlers **already marshal** values into/out of fixed registers:
call arg handlers move operands (from `getOperand`) into X0–X7; result handlers move X0
into the result's `nextReg`; the x64 div/shift handlers stage RDX:RAX / CL. With the
allocator, `getOperand` returns the value's *home register*, and the handler's existing
`mov`/marshal moves it to the ABI register — so **fixed constraints need no pre-coloring;
the existing marshaling code keeps working**, provided the allocator does not hand a
persistent value one of the fixed/clobbered registers at the wrong time. We guarantee that
structurally by keeping the constrained registers **out of the allocatable pool**:

- **aarch64:** allocatable = caller-saved {X9–X15} (for non-call-crossing intervals) +
  callee-saved {X19–X28} (for call-crossing). X0–X7 stay ABI-only (used transiently by
  handlers, and clobbered by calls anyway); X16/X17 = scratch; X8 = sret; SP/FP/LR/XZR/X18
  reserved.
- **x64:** allocatable = {R10,R11,R8,R9,RDI} caller-saved + {R12–R15} callee-saved. Keep
  **RAX/RDX/RCX out of the allocatable pool** (reserved for return, DIV, and shift-count)
  so the two-address/div/shift handlers stay correct without the allocator reasoning about
  their mid-instruction clobbers. (Reclaiming RCX/RDX for allocation is a later refinement
  that models div/shift as clobbers.)
- **arm32:** allocatable = {R4–R10} (already callee-saved & prologue-saved) for everything;
  R0–R3 ABI-only, R12/IP scratch. int64 values occupy register *pairs* — treat a 64-bit id
  as needing two adjacent-classed registers or spill (v1 may simply spill int64 to keep the
  first cut simple, matching today).

Calls are the clobber points: because call-crossing intervals are already forced to
callee-saved (or spilled), and the arg/scratch registers used at a call are caller-saved
and never hold a persistent value across the call, **the call site needs no per-call
`ResetRegs`** — the allocation already guarantees no live value sits in a clobbered
register across the `BL`. (We keep an assertion to that effect during bring-up.)

## Architecture: shared core, per-arch descriptor

- **Shared in `common/`** (new): linearization, liveness, interval construction, and the
  linear-scan assignment loop — all arch-neutral (they read IR + a register-class
  descriptor). Extend `RegMap` (or add a sibling `Alloc` table) to store the *stable*
  per-id → register (or spilled) result, distinct from today's transient `IDs/Regs`.
- **Per-arch register-class descriptor** (new small struct each arch fills): the caller-
  saved allocatable set, the callee-saved allocatable set, the scratch set, and the
  reserved/fixed registers — the allocator consults only this, never physical numbers.
- **Per-arch emission** stays where it is; only `getOperand`/`nextReg`/`spillAndReset` (and
  prologue/epilogue callee-save) change to consult the stable allocation. The op handlers
  are untouched. This is the low-risk seam the recon identified.

## Staging (incremental; each stage lands green)

Land aarch64 fully first (cleanest, unused callee-saved regs, near-clean conformance
baseline), then x64, then arm32. Each stage is a self-contained, cherry-pickable commit
that keeps its conformance mode green.

- **Stage 0 — liveness + intervals in `common`, no emission change.** Build the analysis and
  a validator (assert well-formedness: every allocatable id gets an interval; intervals
  respect def-before-use; call-crossing flags correct). Unit-test in isolation with
  hand-built IR. No codegen change → all modes still green. *This is the reusable core.*
- **Stage 1 — aarch64 linear-scan, caller-saved only (X9–X15); spill anything that crosses a
  call.** No prologue changes yet. Wins for straight-line code and call-free loops
  (hotloop → registers). Rewire `getOperand`/`nextReg`/`spillAndReset`; drop the
  per-instruction reset for allocated values. Validate: `native_aa64` conformance green;
  update byte-count unit tests; disassemble `hotloop` to confirm the memory-op drop.
- **Stage 2 — aarch64 callee-saved (X19–X28) + prologue/epilogue save-restore.** Call-crossing
  intervals now stay in registers. Validate `native_aa64` + a benchmark (bnc self-compile,
  USER CPU) to measure the win.
- **Stage 3 — x64** (two-address, RAX/RDX/RCX reserved, R12–R15 callee-saved). Validate
  `native_x64_darwin`.
- **Stage 4 — arm32** (pool already callee-saved; int64 pairs — spill int64 in v1). Validate
  `native_arm32_baremetal` (protect the ~1-xfail baseline).
- **Stage 5 (additive refinements, separately prioritized):** interval splitting (register in
  the un-pressured sub-range), copy coalescing (the `EliminatePhis` `OP_COPY`s — allocate
  source and dest to the same register to elide the move), better spill heuristics
  (spill-cost by use-density, loop-depth weighting), a real float register file (D8–D15
  callee-saved), reclaiming x64 RCX/RDX via clobber modeling, and arm32 int64-in-registers.

## Correctness is the top risk — validation

A wrong allocation is a **silent miscompile** (a value read from the wrong register). This
is the highest-severity failure mode, so validation is front-loaded:

- The three native **conformance modes** are the end-to-end gate; run the relevant one after
  every stage. They are essentially codegen-clean today, so a regression shows as new
  failures / xpass immediately.
- **Per-arch unit tests** (`*_emit_test.bn`, `*_regmap_test.bn`) are updated to the new emit
  shape and pin the allocator's behavior on small hand-built functions (a call-crossing
  value → callee-saved; a spill under forced pressure; a phi-copy-shared-id interval).
- A **bring-up assertion**: at every call site, assert no live allocatable value is in a
  caller-saved register (catches a call-clobber bug loudly instead of as data corruption).
  Keep it behind the existing verify path during development.
- **Adversarial review** of this plan *before* code, and of each stage's diff (focus: the
  call-clobber/callee-saved interaction, fixed-register marshaling, the phi-copy-shared-id
  live range, x64 two-address/div/shift, arm32 int64/tight-pressure, loop-carried interval
  extension across back-edges, spill-slot correctness for values that are both
  register-allocated somewhere and spilled elsewhere once splitting lands).
- **Benchmark by USER CPU** (native self-compile of cmd/bnc, aarch64, no Rosetta) after
  Stages 1/2 to quantify the win; target: from ~9–12× toward clang's ballpark.

## Sharp edges to get right (call these out in review)

1. **Call-crossing detection & the caller/callee-saved split** — the core correctness lever.
   An interval that spans a call in a caller-saved register = corruption.
2. **Phi-copy-shared-id** — multiple `OP_COPY` defs of one id; the interval is their union,
   and the spill slot is shared (`ir_phi_elim.bn` already relies on one-slot-per-id).
3. **Loop-carried intervals across back-edges** — must extend to the loop's full extent, or a
   value gets a register reused mid-loop.
4. **Fixed-register marshaling still works** because constrained regs are out of the pool;
   verify no handler assumes `getOperand` reloads from memory (some may rely on the reload
   side effect today — audit each handler's operand use).
5. **x64 two-address `mov rd,lhs`** when `rd`==`rhs` (result aliases the second operand) —
   the classic swap/clobber hazard.
6. **Frame layout** now includes a callee-saved save area; every SP-relative offset
   (spill/alloc/outgoing-args) must account for it consistently (`PlanFrame`).
7. **arm32 int64** register pairs and the tighter 7-register pool — v1 spills int64 to avoid
   pair allocation, but must not regress the near-clean baseline.

## Non-goals (v1)

Float register allocation (floats keep using GP spill slots / D-reg moves at boundaries as
today), interval splitting, coalescing, and graph-coloring — all deferred to Stage 5 as
additive work on the same foundation.
