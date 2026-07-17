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

- **Inc 2a (IR-gen + lowering — inert):** emit region pads + build the per-`VMFunc`
  fault table (option A). Pads are appended after the body (body PCs unchanged) and
  **unreferenced** — nothing jumps to them yet, so 2a is a true no-op at runtime and
  lands green independently. Unit tests: lowering emits the expected pad bytecode +
  table entries for representative functions (managed local, nested scope, loop
  body, mid-statement temp, managed-field struct dtor, multi-block short-circuit).
- **Inc 2b (VM unwind mode):** add `BC_UNWIND_RETURN` + the exec-loop unwind
  (fault→pad→pop→`callerPC` lookup→…→top→host), the cleanup-context fatal-guard
  flag (§6), and wire the **bounds** guard as the single proving consumer.
  Conformance: a bounds-fault leaves the host alive, `EXEC_ERROR` + message, **and
  refcounts balance** (leak assertion) across managed-state shapes (bare `@T`,
  nested `@[]T` w/ element dtor, `@func` closure, nested calls, mid-statement temp).
- **Inc 3:** wire the remaining 7 guard sites + `cmd/bni` `runProgram` + the
  test-runner (fault = failed test, continue).

## 8. Open questions for the user

1. **Fork §5: static table (A, recommended + locked at 2a) vs frame slot (B).**
2. Inc 2b wires only the **bounds** guard as the proving cut (rest in Inc 3) —
   acceptable, or wire all 8 in 2b?
3. Anything in §6 to scope differently.

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
