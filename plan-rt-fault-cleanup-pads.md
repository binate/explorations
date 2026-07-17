# Design: cleanup landing pads + VM unwind mode (Plan 2, Inc 2a/2b)

Status: **PROPOSED — pending user sizing sign-off.** This is the detailed design
for the "long pole" of [`plan-rt-abort-panic.md`](plan-rt-abort-panic.md) Plan 2
(recoverable VM user-code faults) and, by construction, the REPL's Stage-7 break
([`plan-repl-embeddable.md`](plan-repl-embeddable.md)). One fork (§5) is an open
decision for the user. Grounded in a recon pass (2026-07-17); file:line cites are
against the tree at `6dd89502`.

## 1. The problem restated

When the bytecode VM runs a bad interpreted program that faults (bounds / divide /
shift / nil-deref / stack-overflow / call-through-nil), the host must survive. The
exec loop must **unwind the VM data-stack frames back to `CallFunc`**, running the
RefDec/scope-exit cleanup that each abandoned frame would have run — because a
naive frame-discard **leaks** (RefDec is inline `BC_REFDEC_INLINE_FAST` bytecode
at specific PCs; `BC_RETURN` runs only the single `freeOnPop` slot, not scope
cleanup). Leaking violates the strict never-leak rule.

## 2. Foundation (confirmed by recon)

- **IR-gen knows the exact live managed set at every program point.** An early
  `return` mid-nested-scope already emits the correct RefDec sequence:
  `emitDecForManagedLocals` (all `ctx.Vars`) + `emitTempCleanupForReturn` (all
  `ctx.Temps`) — `gen_return.bn:189-190`. Per-scope cleanup is
  `emitDecForScopeVars(ctx, b, savedVarLen)` (RefDec `Vars[savedVarLen:]`) —
  `gen_util_refcount.bn:433-465`. Per-statement temp cleanup is
  `emitTempCleanupBody` — `gen_util_refcount.bn:322-356`. **A cleanup pad reuses
  these exact emitters; no new cleanup logic.**
- **Each managed temp gets a distinct, lifetime-long VM register** (= its IR value
  id; the lower_func remap only *widens* for 64-bit-on-32-bit pairs, it does not
  pack/reuse — `lower_func.bn:436-449`). Registers are zeroed at frame push
  (`vm.bn:273`). ⇒ a slot is `0` before its value is produced and holds its value
  after; **a pad that RefDecs a not-yet-produced slot is a nil-check no-op**
  (safe). Managed vars are distinct allocas (also zeroed, also distinct) — same
  property.
- **Granularity required: per-statement (temps) + per-scope (vars).** Temps are
  batch-cleaned at statement end (`emitTempCleanup`, `gen_util_refcount.bn:300`)
  and `ctx.Temps` cleared; a prior statement's temp slots hold stale (freed)
  pointers, so a pad must scope temp-RefDec to the *current statement*. Vars are
  per-nesting-scope (loop bodies re-enter fresh each iteration —
  `gen_flow.bn:168-233`). No finer granularity is needed (mid-statement temps are
  covered because each is a distinct slot — a fault before a temp is produced sees
  its slot `0`).
- **Branch targets are static PCs** resolved at lowering (`blockOffsets`,
  `lower_func.bn`); a pad is ordinary bytecode at a labeled PC that the unwind
  `BC_JUMP`s to. `BC_REFDEC_INLINE_FAST` (with its slow-path dtor dispatch) is the
  RefDec op the pad emits.

## 3. Cleanup pads

For each **live-set region** of a function (a maximal PC range across which the
live managed set is constant — boundaries are: a managed temp produced, a managed
var declared, a scope entered/exited, a statement boundary), emit an out-of-line
**pad**: the bytecode that RefDecs that region's live temps (current statement)
then its open-scope vars innermost-out, then a terminator (§4). Adjacent regions
whose pads are byte-identical are deduped. Pads live in a cold section after the
function body (forward `BC_JUMP` fixups, like any block).

A pad is **exact for the fault points that map to it** — it RefDecs precisely
what is live in its region. No over-approximation, no reliance on
zero-slot-no-op for correctness (that property is only a belt-and-suspenders
against off-by-one region boundaries). No double-free (a cleaned prior-statement
temp is in a *different* region with a *different* pad that does not name it); no
leak (everything live is named).

## 4. VM unwind mode

New op **`BC_UNWIND_RETURN`** terminates every pad. It behaves like `BC_RETURN`'s
frame-pop (restore caller state from `FRAME_HDR`, run `freeOnPop`, reclaim SP) but
(a) copies back **no return value** (a faulted frame has none — skip the
aggregate copy-back at `vm_exec.bn:163-183`), and (b) instead of resuming the
caller normally, **re-enters unwind** in the caller: look up `callerPC`
(`FRAME_HDR[0]` — the PC right after the caller's `BC_CALL`) in the caller
function's fault table → the caller's pad → `BC_JUMP` there. When the popped frame
is the top-level frame (`savedPC == -1`), return the fault sentinel to `execFunc`
→ `CallFunc`, which (Inc 1 carrier) leaves `vm.Status == VM_STATUS_FAULTED` +
`FaultMsg` for the host.

Unwind entry (from a guard, Inc 3): the guard calls `setFault(msg)` then looks up
its **own** PC in the fault table → pad → `BC_JUMP`. So a fault and an
unwind-through-a-caller are the *same* operation keyed on a PC (guard PC, or
`callerPC`).

Crucially, **`callerPC` is already in `FRAME_HDR[0]`** — no new frame-header slot
is needed for the table approach (§5). When a callee faults and pops to a caller,
the caller "faults" at its call site; its live-set there (temps produced *before*
the call, e.g. `g()` in `f(g(), faulty())`; the call's own result not yet
produced) is exactly what the caller's pad at `callerPC` RefDecs.

## 5. THE FORK (user decision): how the VM finds a PC's pad

Both are "cleanup pads"; they differ only in how a PC maps to its pad.

- **(A) Static fault→pad table per `VMFunc` — RECOMMENDED.** Lowering builds a
  sorted `@[]int` mapping each relevant PC (every guard op PC ∪ every post-`BC_CALL`
  PC) → its region's pad PC. On fault/unwind, binary-search the current/`callerPC`
  → pad. **Zero normal-path cost** (nothing added to the hot instruction stream);
  all cost is at lowering + the rare fault path. Cons: table build + a small
  binary search; the table must cover call-return PCs (unwind re-entry points).
- **(B) Per-frame "current pad PC" slot.** Grow `FRAME_HDR` 6→7 words (or reserve
  a register); the compiler stores the current pad PC at each live-set-change
  point; unwind reads the slot (no lookup). Simpler unwind, but **taxes the normal
  path** with a store per live-set-change point (per managed temp / var / scope /
  statement) — pure overhead for a rarely-triggered feature.

**Recommendation: (A).** Faults are rare and the VM instruction stream is hot;
keep the hot path untouched and pay only at lowering + fault time. (A) also needs
no `FRAME_HDR` change (reuses `callerPC`).

## 6. Scope boundaries & edge cases

- **Outermost-`execLoop` only** (as ratified): a fault under a live native
  callback frame (`execExtern → native callback → CallFunc`) cannot unwind
  through the host-stack frame — stays fatal until heap frames. The unwind stops
  at / never crosses a native boundary.
- **No return value on a faulted frame** — `BC_UNWIND_RETURN` skips copy-back.
- **Dtor-fault during a pad**: a managed value's dtor (a generated `__dtor` that
  only RefDecs fields, or a closure dtor) runs during pad cleanup. In current
  Binate a dtor cannot itself user-fault (dtors only RefDec, which nil-checks), so
  this is moot; documented as "a fault during unwind cleanup is fatal" if it ever
  becomes reachable.
- **Named returns / composite-literal / func-value temps**: no special-casing —
  they are live locals/temps and RefDec as part of the pad, exactly as the return
  path treats them (recon dimension A edge cases all aligned).

## 7. Increment split

- **Inc 2a (IR-gen + lowering, no behavior change):** emit region pads +
  build the per-`VMFunc` fault table (option A), gated behind lowering so the
  bytecode carries pads + table but nothing jumps to them yet. Unit tests:
  lowering emits the expected pad bytecode + table entries for representative
  functions (managed local, nested scope, loop body, mid-statement temp,
  managed-field struct dtor). Inert — no exec-loop change. Green.
- **Inc 2b (VM unwind mode):** add `BC_UNWIND_RETURN` + the exec-loop unwind
  (fault→pad→pop→caller lookup→…→top→host), and wire the **bounds** guard as the
  single proving consumer. Conformance: a bounds-fault in the VM leaves the host
  alive, `EXEC_ERROR` + message, **and refcounts balance** (leak assertion) across
  managed-state shapes (bare `@T`, nested `@[]T` w/ element dtor, `@func` closure,
  nested calls). Inc 3 wires the remaining 5 guards + `cmd/bni`/test-runner hosts.

## 8. Open questions for the user

1. **Fork §5: static table (A, recommended) vs frame slot (B)?**
2. Is wiring only the **bounds** guard in Inc 2b (rest in Inc 3) an acceptable
   proving cut, or wire all 6 in 2b?
3. Anything in §6 you want scoped differently.
