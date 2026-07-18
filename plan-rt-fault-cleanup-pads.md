# Design: cleanup landing pads + VM unwind mode (Plan 2, Inc 2a/2b)

Status: **PROPOSED — pending user sizing sign-off.** Detailed design for the
"long pole" of [`plan-rt-abort-panic.md`](plan-rt-abort-panic.md) Plan 2
(recoverable VM user-code faults) and, by construction, the REPL's Stage-7 break
([`plan-repl-embeddable.md`](plan-repl-embeddable.md)). Revised 2026-07-17 after
an adversarial design review (all load-bearing claims verified against source; the
review's blockers/majors resolved as clarifications — see §9). One fork (§5) is an
open decision for the user. Cites are against the tree at `6dd89502`.

## 1. The problem restated

When the bytecode VM runs a bad interpreted program that faults, the host must
survive. The exec loop must **unwind the VM data-stack frames back to `CallFunc`**,
running the RefDec/scope-exit cleanup each abandoned frame would have run — a naive
frame-discard **leaks** (RefDec is inline `BC_REFDEC_INLINE_FAST` bytecode at
specific PCs; `BC_RETURN` runs only the single `freeOnPop` slot, not scope
cleanup), which violates the strict never-leak rule.

## 2. Foundation (confirmed by recon + review)

- **IR-gen knows the exact live managed set at every program point.** An early
  `return` mid-nested-scope already emits the correct RefDec sequence:
  `emitDecForManagedLocals` (all `ctx.Vars`) + `emitTempCleanupForReturn` (all
  `ctx.Temps`) — `gen_return.bn:189-190`. Per-scope cleanup is
  `emitDecForScopeVars(ctx, b, savedVarLen)` (RefDec `Vars[savedVarLen:]`,
  `gen_util_refcount.bn:433-465`); per-statement temp cleanup is
  `emitTempCleanupBody` (`gen_util_refcount.bn:322-356`). **Pads reuse these exact
  emitters — no new cleanup logic.** `consumeTemp` (`gen_util_refcount.bn:273-284`)
  removes a returned/assigned temp from `ctx.Temps`, so it is never in a pad (no
  double-free of an ownership-transferred value).
- **Each managed temp gets a distinct, lifetime-long VM register** (= its IR value
  id; the remap only *widens* for 64-bit-on-32-bit pairs, it never packs/reuses —
  `lower_func.bn:436-449`, `lower_slots.bn` `buildSlotMap`/`remapRegisters`).
  Registers/allocas are zeroed at frame push (`vm.bn:278`), and `RefDec(nil)` is a
  nil-check no-op (`vm_exec.bn:370`). ⇒ a slot is `0` before its value is produced;
  a pad naming a not-yet-produced slot is a safe no-op. (Belt-and-suspenders; the
  core safety is exact per-PC live-sets, below.)
- **Granularity: per-statement (temps) + per-scope (vars).** `ctx.Temps` is
  batch-cleaned at statement end and holds across a whole statement — including a
  multi-block statement (short-circuit `&&`, ternary), since it is cleared only at
  the statement boundary, not per block. Vars are per-nesting-scope (loop bodies
  re-enter fresh each iteration — `gen_flow.bn:168-233`).
- **Branch targets are static PCs** resolved at lowering (`blockOffsets`); a pad is
  ordinary bytecode at a labeled PC the unwind `BC_JUMP`s to.

## 3. Cleanup pads + the fault table

For each **live-set region** of a function (a maximal set of PCs sharing one live
managed set — boundaries: a managed temp produced, a managed var declared, a scope
entered/exited, a statement boundary), emit an out-of-line **pad**: bytecode that
RefDecs the region's live temps (current statement) then its open-scope vars
innermost-out, then `BC_UNWIND_RETURN` (§4). Byte-identical pads are deduped. Pads
are **appended after the function body** (body PCs unchanged) in a cold section.

The **fault table** is keyed **per-PC, not per-range** (this is what makes
multi-block statements a non-issue): lowering, which already walks the live-set as
it emits, stamps each *unwind-relevant PC* → its region's pad PC. Unwind-relevant
PCs are exhaustively:

- **The 8 guard-site PCs** (fault origins) — the 6 fault kinds:
  1. `BC_BOUNDS_CHECK` (`vm_exec_helpers.bn:234`)
  2. `BC_DIV_CHECK` (`vm_exec_helpers.bn:237`)
  3. `BC_SHIFT_CHECK` (`vm_exec_helpers.bn:246`)
  4. `BC_NIL_CHECK` (`vm_exec_helpers.bn:228`)
  5. stack overflow in `pushFrame` (`vm.bn:259`)
  6. call-through-nil, **3 sites**: `BC_CALL_INDIRECT` (`vm_exec.bn:242`),
     `BC_CALL_IFACE_METHOD` (`vm_exec.bn:282`/`288`), `BC_CALL_FUNC_VALUE`
     (`vm_exec_funcref.bn:424`).
  All 8 become recoverable in Inc 3 (they are `println + rt.Exit(1)` today).
- **The post-`BC_CALL` PC of every normal user call** (unwind re-entry): when a
  callee faults and pops to a caller, the caller "faults" at its call site
  (`callerPC = FRAME_HDR[0]`, already stored). Its live-set there — temps produced
  *before* the call (e.g. `g()` in `f(g(), faulty())`), the call's own result not
  yet produced — is exactly what the caller's pad at that PC RefDecs.

A pad is **exact for the PCs that map to it** (it RefDecs precisely the region's
live set): no leak (everything live is named), no double-free (a cleaned
prior-statement temp lives in a different region under a different pad that does not
name it; a consumed temp is out of `ctx.Temps`). `BC_REFDEC_INLINE_FAST` (with its
existing slow-path dtor dispatch) is the RefDec op the pad emits.

## 4. VM unwind mode

New op **`BC_UNWIND_RETURN`** terminates every pad. It pops the frame like
`BC_RETURN` — restores caller state from `FRAME_HDR`, runs `freeOnPop`, and
**`vm.SP = callerSP`** (`FRAME_HDR[3]`), which reclaims the *entire* callee frame
including any mid-statement `vm.SP` growth (managed-slice headers, string/aggregate
copy-backs). Because the pad's RefDecs run **before** the pop (mirroring
`emitTempCleanup`'s RefDec-then-`OP_SP_RESTORE` ordering), every managed value whose
header sits on `vm.SP` is decremented via its register before its bytes are
reclaimed — no SP leak, no missed RefDec. `BC_UNWIND_RETURN` differs from
`BC_RETURN` only in: (a) it copies back **no return value** (a faulted frame has
none — skip the relocation at `vm_exec.bn:163-183`; SP is still restored), and (b)
instead of resuming the caller normally, it **re-enters unwind**: look up `callerPC`
in the caller function's fault table → the caller's pad → `BC_JUMP`. At the
top-level frame (`savedPC == -1`) it returns the fault sentinel to `execFunc` →
`CallFunc`, leaving (Inc 1 carrier) `vm.Status == VM_STATUS_FAULTED` + `FaultMsg`
for the host.

**Unwind entry** (from a guard, Inc 3): the guard calls `setFault(msg)`, then looks
up its own PC → pad → `BC_JUMP`. A fault and an unwind-through-a-caller are the same
operation keyed on a PC.

## 5. THE FORK (user decision): how the VM finds a PC's pad

- **(A) Static fault→pad table per `VMFunc` — RECOMMENDED, and locked in at Inc 2a.**
  Lowering builds a sorted `@[]int` (a plain int-slice — no managed elements, so
  `VMFunc`'s dtor gains only a single trivial backing-RefDec at VM teardown) mapping
  each unwind-relevant PC → its pad PC. On fault/unwind, binary-search the
  current/`callerPC`. **Zero normal-path cost**; all cost at lowering + the rare
  fault path.
- **(B) Per-frame "current pad PC" slot** (grow `FRAME_HDR` 6→7, or reserve a
  register; store at each live-set-change point; unwind reads the slot). Simpler
  unwind, but **taxes the hot path** with a store per live-set-change. **Reserved**
  for the future only if profiling ever shows (A)'s lookup is prohibitive; not
  pursued now (it would retro-change the frame layout).

**Recommendation: (A)** — faults are rare, the instruction stream is hot, and (A)
needs no `FRAME_HDR` change (reuses `callerPC`).

## 6. Scope boundaries, the dtor invariant, and edge cases

- **The dtor invariant (closes the review's two blockers).** Dtors are
  compiler-generated field-RefDec routines with **no guard ops of their own** — with
  ONE reachable exception: the iterative dtor dispatch pushes a dtor frame via
  `pushFrame` (`vm_exec.bn:408`/`436`), so a pathologically deep dtor chain can hit
  the **stack-overflow** guard. Therefore: **recoverable faults are armed ONLY in
  normal user execution. Any fault raised while executing a dtor frame OR a cleanup
  pad is FATAL** (a hard `vmPanic`, not a recoverable `setFault`), tracked by a VM
  cleanup-context flag/counter set on dtor-frame / pad entry. This keeps dtor frames
  entirely OUT of the recoverable-unwind path: the fault table never needs a
  dtor-RefDec-PC entry, and a callee can never fault below a live dtor frame.
- **Cross-mode dtor native fault** (`refDecCrossModeDispatch`, `vm_exec.bn:451`, no
  VM frame): a native dtor only RefDecs; and any native-code fault is the separate
  **native-extern SIGSEGV** concern (out of Plan 2 — needs a host signal handler),
  already scoped out. Covered by the dtor invariant + that boundary.
- **Outermost-`execLoop` only** (ratified): a fault under a live native callback
  frame stays fatal until heap frames. The unwind never crosses a native boundary.
- **Poll / suspend / break interaction.** `vm.Status` holds one value; a fault sets
  `VM_STATUS_FAULTED` and unwinds the *whole* frame stack to the host immediately —
  it leaves **no resumable state** (unlike a suspend). FAULTED and SUSPENDED are
  mutually exclusive; a fault supersedes any pending suspend (the turn is over, the
  host sees `EXEC_ERROR`, never calls `Resume`); `ResumePC` is irrelevant after a
  fault. Guards are not poll points, so they do not race. `CallFunc`/`CallByVMFunc`
  reset `Status`+`FaultMsg` per host call (Inc 1), so each turn starts clean.
- **Named returns / composite-literal / func-value temps**: no special-casing —
  they are live locals/temps and RefDec in the pad exactly as the return path treats
  them (review confirmed all these edge cases aligned).

## 7. Increment split

- **Inc 2a (IR-gen + lowering — inert).** Split into three green sub-commits
  (layering refinement: pads live in a separate `ir.Func.FaultPads` list OFF
  `Func.Blocks`, so the compiled backends — which iterate only `Blocks` — need NO
  changes; only the VM lowers pads):
  - **2a-0 — plumbing ✅ LANDED (`54ed2260`).** `Func.FaultPads` + `Instr.PadBlock`
    fields, `OP_UNWIND_RETURN`/`BC_UNWIND_RETURN`, `VMFunc.FaultTable`, inert arms.
  - **2a-1 — VM lowering ✅ LANDED (`b4c7b106`).** `lower_func` lowers
    `combinedBlocks = Blocks ++ FaultPads` (pad PCs after the body) and builds
    `VMFunc.FaultTable` (sorted `(resumePC, padPC)` pairs) from each op's
    `Instr.PadBlock`; `AddFaultPad`/`EmitUnwindReturn` builders; `OP_UNWIND_RETURN`
    is a terminator. Unit-tested via a hand-built pad. Empty `FaultPads` ⇒
    byte-identical to before.
  - **2a-2 — IR-gen emission ✅ LANDED (`a743e92f`, 2026-07-17). Inc 2a COMPLETE.**
    Emits a pad for every OP_BOUNDS_CHECK (genBoundsCheck -> attachFaultPad ->
    emitPadCleanup); the branching-cleanup subtlety below is handled via
    Func.PadEmitMode (AddBlock routes pad-continuation blocks into FaultPads) and
    findBlockOffset resolving over combinedBlocks. gen1 builds with pads emitted
    throughout; conformance smoke (slices/arrays/index) green on builder-comp AND
    builder-comp-int; the compiled backends stay untouched. div/shift/nil/call pad
    emission pairs with Inc 3. (Original scoping note below.) Hook each faulting/call
    op emission site (bounds/div/shift/nil-check + calls) to snapshot the live
    managed set (`ctx.Vars` managed subset + `ctx.Temps`) into a pad (`AddFaultPad`
    + a NON-clearing `emitPadCleanup` reusing the RefDec emitters) terminated by
    `EmitUnwindReturn`, and set `instr.PadBlock`. Tested by lowering real faulting
    functions (`FaultTable` non-empty; pad RefDecs the live set). Still inert at
    runtime until 2b.

    **SUBTLETY surfaced (2026-07-17) — pad cleanup can BRANCH.** The RefDec
    emitters are not all straight-line: `emitManagedIfaceValueRefDec`
    (`gen_util_refcount.bn:215`) and `emitManagedFuncValueRefDec` (`:411`) — and a
    managed-*element* slice RefDec — create new blocks via `AddBlock` +
    `EmitBranch`/`EmitJump`. So a pad that cleans up an iface-value / func-value /
    managed-element-slice local is **multi-block**. Two consequences the emission
    must handle: (1) those continuation blocks must go into `Func.FaultPads`, NOT
    `Func.Blocks` (else the compiled backends emit them) — needs `AddBlock` to
    honor a `Func.PadEmitMode` flag set around `emitPadCleanup`; (2) the intra-pad
    branches target `FaultPads` blocks, but `lower_func`'s `findBlockOffset`
    currently resolves only over `f.Blocks` — it must resolve over
    `combinedBlocks` so pad-internal branches map to the right PC (this is also the
    2a-1 review's finding #3). `emitPadCleanup` must return the pad's EXIT block
    (where `EmitUnwindReturn` goes); `instr.PadBlock` is the ENTRY block. So 2a-2 is
    a genuine sub-project, not just N call-site edits — size it accordingly.
- **Inc 2b (VM unwind mode) ✅ LANDED (`4efcd212`, 2026-07-17).** `BC_UNWIND_RETURN`
  exec handler + `setFault` / `dispatchFaultPad` / `lookupFaultPad`
  (`vm_fault.bn`), with the **bounds** guard as the single proving consumer.
  Three refinements to the original 2b plan, forced during implementation (all keep
  the agreed scope — top-level bounds recovery):
  - **`FaultRaised` transient flag, distinct from `Status`.** The recorded plan
    branched to a pad on a bare `Status == VM_STATUS_FAULTED` check after
    `execManagedMemoryOp`; that RE-FIRES on every managed-memory op that merely runs
    DURING the unwind (a passing bounds check, a `RefInc` in a destructor sub-frame
    the pad's RefDec cascade spawns — Status is still FAULTED then). `setFault` now
    also sets `FaultRaised`; the exec loop CONSUMES it (clears) the instant it
    dispatches, so a mid-unwind managed op never re-dispatches.
  - **Entry-frame gate; nested faults keep the LEGACY fatal path, not `vmPanic`.**
    Only an ENTRY-frame fault (frame `savedPC == -1`) recovers. A NESTED fault has
    no call-site pad to unwind through in 2b, so `dispatchFaultPad` prints exactly
    the message `rt.BoundsFail` would and `rt.Exit(1)`s — **byte-identical** to
    pre-Plan-2 behavior. The plan's proposed `vmPanic` fallback would have changed
    the output and reddened the existing bounds-fault conformance goldens
    (`310`/`929`/`314`, run under `-int`); the legacy path preserves them.
  - **`BC_UNWIND_RETURN` handles only the entry frame in 2b** (asserts `savedPC ==
    -1`, returns to host). The cross-frame pop (fault→pad→pop→`callerPC`
    lookup→…→top) needs call-site pads and moves to Inc 3 with its own test, rather
    than shipping untested speculative pop logic. The §6 cleanup-context fatal-guard
    flag was likewise **not needed** for 2b and defers to Inc 3.
  - **Test is a VM unit test, not conformance.** A standalone program's `main` runs
    NESTED under `main.__entry`, so a conformance program can't reach the
    entry-frame path — entry-frame recovery is the REPL/embedder scenario.
    `vm_test.bn TestEntryFrameBoundsFaultRecovers` drives a prompt (a managed slice
    then an out-of-bounds index) through `CallByVMFunc` (entry frame) and asserts
    host survival + `Status == FAULTED` + message + a clean following turn.
    `vm_fault_test.bn` unit-tests the helpers. Existing bounds conformance proves
    the nested-fatal path unchanged.
- **Inc 3 — cross-frame recovery + remaining guards + host policy.** Architecture
  steer (user): recovery is **embedder policy**, not a VM property — the VM must
  ALWAYS unwind to the entry frame and return `Status = FAULTED` + `FaultMsg`, and
  never `print`/`exit` itself. Each host decides: REPL → diagnostic (Inc 1); `bni`
  run → print + exit 1; `bni --test` → failed test; another embedder → its own
  call. "`bni` is just another embedder." The 2b in-VM `println + rt.Exit`
  (dispatchFaultPad's nested branch) is a staging expedient, removed at the flip.
  Chosen split: **gate-kept staging** (land the VM mechanism behind the entry-frame
  gate first, then flip on call-site pads).
  - **Inc 3a-1 ✅ LANDED (2026-07-17).** `frameLocals` prep refactor (`6b8da0cb`);
    `BC_UNWIND_RETURN` now pops its frame and continues at the caller's call-site
    pad (`lookupFaultPad(caller, callerPC)`), entry frame → host (`ed3a8f36`); and
    `cmd/bni --test` checks `Status == FAULTED` → failed test + continue, fixing the
    silent-pass hole 2b opened (`b96ec779`, with `e2e/bni-test-fault.sh`). The
    entry-frame gate STAYS, so real nested faults are still legacy-fatal and the
    cross-frame pop is inert for real programs — exercised only by a hand-built
    two-frame lowering test (callee body = `OP_UNWIND_RETURN` bypasses the gate).
  - **Inc 3a-2 (the flip) ✅ LANDED (2026-07-17).** Call-site pads (`05976a9f`,
    inert) + the flip (`8c3c2bf8`). IR-gen `attachFaultPad` after every USER-level
    call op — direct (`gen_call.bn` `EmitCall`), func-value (`EmitCallFuncValue`),
    iface-method (`gen_iface_dispatch.bn`), and concrete method (`gen_method.bn`,
    added after an adversarial memory-safety review found it un-padded). The
    internal magics (`EmitCallHandle`/`EmitCallIndirect` = `_call_dtor`/`_call_shim_*`)
    and the synthetic entry/wrapper frames (`__entry`, init dispatcher, iv-thunks,
    method-value wrappers) stay unpadded — they carry no managed cleanup. The VM
    handles them via the **pop-loop**: `BC_UNWIND_RETURN` pops each frame, runs the
    caller's call-site pad if present, or — if the caller has NO pad and an EMPTY
    `FaultTable` (a synthetic frame) — pops it transparently; a non-empty table
    missing a pad is a lowering gap → `vmPanic` (Option B, chosen by the user over
    padding every synthetic site). `dispatchFaultPad` lost the entry-frame gate + the
    in-VM `println`/`rt.Exit`: the VM now ALWAYS unwinds to the host and returns
    `Status = FAULTED`; the embedder picks policy (`interp.RunMain` surfaces the
    message as a run-error → `cmd/bni runProgram` prints + exits 1). Conformance
    `310`/`929`/`314` stay green (nested `main` fault → same message + exit 1);
    1125 tests green under `builder-comp-int`, 648 under `builder-comp`/LLVM (pads
    invisible to compiled backends). Memory-safety review: one MAJOR finding (the
    `gen_method` gap), fixed + regression-tested; all other claims held. Cross-frame
    recovery is now LIVE for bounds faults in any call depth.
  - **Inc 3b ✅ LANDED (`da66f20a`, 2026-07-17).** Divide-fault (divide-by-zero +
    signed MIN/-1 overflow) and negative-shift are now recoverable, wired exactly
    like bounds: `attachFaultPad` after `OP_DIV_CHECK` / `OP_SHIFT_CHECK`
    (`emitDivCheckGuard` / `emitShiftCheckGuard` now take `ctx`); the VM guards
    `setFault` (inlining `rt.DivCheck` / `rt.ShiftCheck`'s compares, with the
    identical `runtime error: …` text). 606 conformance green under
    `builder-comp-int`. **Still fatal (follow-ups):** nil-deref (`OP_NIL_CHECK` is
    currently UN-emitted by IR-gen — making it recoverable needs IR-gen to emit nil
    checks first); call-through-nil (inline in `execLoop`); stack-overflow
    (`pushFrame`).
  - **Inc 3c ✅ LANDED (`4a0cc0ad`, 2026-07-17).** The §6 cleanup-context fatal-guard.
    A per-VM `CleanupDepth` counter is incremented on pad entry (the fault consume
    point + the pop-loop's caller-pad entry) and dtor-frame push (the two
    `BC_REFDEC_INLINE_FAST` dtor arms), decremented on pad exit (`BC_UNWIND_RETURN`)
    and dtor-frame pop (`BC_RETURN` when `freeOnPop != 0`, which marks exactly a dtor
    frame — verified the only non-zero `hdr[5]` writers are those two pushes).
    `setFault` turns fatal (`vmPanicName`) whenever `CleanupDepth != 0`
    (`faultDuringCleanup`, factored so the decision is unit-testable — the fatal branch
    itself terminates the process). Reset per host `Call*`. Inert for the
    currently-recoverable guards (bounds/divide/shift never appear in a dtor or pad, so
    no observable change; conformance untouched) — it is the safety substrate the
    stack-overflow-recovery follow-up needs. Adversarial review CLEAN (counter proven
    balanced across every path; the `freeOnPop`⟺dtor invariant holds; the fatal branch
    is depth-0-unreachable today). Also extracted `BC_CALL_IFACE_METHOD` →
    `execCallIfaceMethod` (`vm_exec_ifacecall.bn`, mirroring `execCallFuncValue`) to
    keep `vm_exec.bn` under the file-length limit after the counter additions.
    **Review note for the stack-overflow follow-up:** `CleanupDepth` is per-VM and NOT
    saved/restored across a nested `execFunc`/`execLoop` re-entry — inert now (dtors are
    guard-free), but a re-entrant cleanup path would need to account for it.
  - **Still fatal (remaining follow-ups):** call-through-nil (inline in `execLoop`:
    `BC_CALL_INDIRECT` + `BC_CALL_IFACE_METHOD` — now in `vm_exec_ifacecall.bn` — nil
    checks); stack-overflow (`pushFrame`; recovering mid-push is subtle, and depends on
    3c to keep a dtor-chain overflow fatal); nil-deref (`OP_NIL_CHECK` is UN-emitted by
    IR-gen — needs IR-gen to emit nil checks first).

## 8. Open questions for the user

1. **Fork §5: static table (A, recommended + locked at 2a) vs frame slot (B).**
   RESOLVED: A (static fault→pad table), landed across 2a/2b.
2. Inc 2b wires only the **bounds** guard as the proving cut (rest in Inc 3).
   RESOLVED: bounds-only proving cut, landed (`4efcd212`).
3. Anything in §6 to scope differently. — §6 fatal-guard flag deferred to Inc 3.

## 9. Adversarial design-review resolutions (2026-07-17)

The review verified every load-bearing claim against source (distinct
lifetime-long registers, zeroed frames, `consumeTemp`, `RefDec(nil)` no-op,
`FRAME_HDR[0]=callerPC`, the reusable IR-gen emitters). Its findings, all resolved
as clarifications (no redesign):

- **[blocker] dtor-frame re-entry PC / [blocker] cross-mode dtor fault** → §6 dtor
  invariant: faults during a dtor frame or pad are fatal; dtor frames never enter
  the recoverable-unwind path.
- **[major] guard sites unenumerated** → §3 lists all 8.
- **[major] poll/suspend interaction** → §6: FAULTED unwinds fully+immediately, no
  resume state, mutually exclusive with SUSPENDED.
- **[major] aggregate return-temp SP** → §4: `BC_UNWIND_RETURN` restores `callerSP`
  (reclaims the whole frame incl. mid-statement SP growth); RefDec-before-pop order
  preserves the normal-path invariant — no SP leak.
- **[major] multi-block (short-circuit) statements** → §3: the table is per-PC, not
  per-range; `ctx.Temps` spans the statement across blocks.
- **[minor] VMFunc table dtor cost** → §5: plain int-slice, trivial teardown.
  **[minor] Inc 2a inertness** → §7: pads appended (body PCs unchanged), unreferenced.
  **[minor] fork lock-in** → §5: A locked at 2a, B reserved.

## 10. Fork verdict — Option A (2026-07-17, adversarial A-vs-B review)

A brief adversarial A-vs-B review (two grounded advocates) confirms **Option A**.
The B advocate's one decisive-severity argument — "A needs a post-PC-assignment
re-walk with the live-set context destroyed" — is defeated, and defeating it fixes
A's implementation approach:

- **How A's table is built (no re-walk):** the fault-op → pad-block association is
  made in **IR-gen**, where the live-set (`ctx.Vars`/`ctx.Temps`) is available. Each
  potentially-faulting IR op (and each call op) carries a **pad-block reference**,
  exactly like `OP_BRANCH`/`OP_JUMP` carry target blocks; **lowering** then resolves
  both the op's bytecode PC and the pad-block's PC via its existing block-offset
  machinery (`lower_instr.bn:219-234`, `blockOffsets`) and records the `(opPC,
  padPC)` pair. So the table build is a compile-time annotation resolved by
  machinery that already exists — **zero runtime cost, no fourth pass**.
- **Why not B:** B's per-live-set-change store is real hot-path bytecode (~20-100
  stores/function, one per managed temp/var/scope/statement), and B's `FRAME_HDR`
  6→7 growth taxes *every* `BC_RETURN` (7 header loads vs 6) and every frame-touching
  site (`hdr[5]` freeOnPop index shift at `vm_exec.bn:153`/`408`/`436` — off-by-one
  corruption risk). Both advocates' pro-B fallbacks require faults to be *frequent*
  (used as control flow); they are rare by premise.

**Pad structure decision (implementation):** each region's pad is a **flat** RefDec
sequence for that region's full live-set (all open-scope vars + current-statement
temps), reusing the return-path emitters — NOT intra-frame per-scope chaining.
Cross-*frame* chaining (`BC_UNWIND_RETURN` → caller's pad via `callerPC` lookup)
remains. Flat-per-region is simpler to emit/verify; the DRY per-scope-chained
variant (less bytecode for deep nesting) is a possible later refinement.
