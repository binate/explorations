# Plan: comprehensive VM SP-guard (fault safely on stack overflow; never leak/corrupt)

Status: IN PROGRESS (work-2 / session), started 2026-09-09, comprehensive scope
2026-09-12. Owner: this session. WIP scaffolding: `OP_STACK_CHECK` enum +
`EmitStackCheck` (work-2 `09dc98da6`, unused/not landed).

## Motivation

The VM's recoverable stack-overflow model is INCOMPLETE, in two ways that each
break the "never leak / never corrupt" invariant:

1. **Overflow is only checked at `pushFrame`.** The other `vm.SP`-growing ops —
   interface boxes (`BC_IFACE_VALUE`), `BC_MAKE_SLICE` headers, string copies,
   func-value pushes, retbuf / aggregate copy-backs — grow `vm.SP` with NO
   overflow check (~13 of ~19 `vm.SP +=` sites). On a true overflow they SILENTLY
   WRITE PAST the stack buffer (heap corruption) instead of faulting.
2. **The `pushFrame` check fires AFTER the caller commits the call's args.** A
   moved (`consumeTemp`'d) `@Iface`/managed-struct arg is then orphaned on
   overflow (the call op's pad excludes it) → leak. And more broadly, arg building
   evaluates+delivers args PER-ARG interleaved (`buildCallArgs`), so a fault while
   evaluating a LATER arg already leaks EARLIER consumed args.

A leak's rarity never makes it acceptable, and silent corruption is worse. Fix the
model comprehensively: every `vm.SP` growth must be checked, and must fault at a
point where the fault pad still covers everything owned.

## Design

Enforce: no `vm.SP` growth ever crosses the limit undetected, and every
overflow fault fires with a clean (fully-covering) live set.

- **Shared check helper** (vm.bn): `wouldOverflow(vm, bytes) bool` — true iff
  `vm.SP + bytes` exceeds the limit (`StackSize - StackRedZone` when
  `CleanupDepth == 0`, else `StackSize`; the existing red-zone logic, factored out
  of `pushFrame`). `pushFrame` uses it (backstop for the frame push).
- **Temp-growth checks (corruption fix).** Every SP-growing VM op checks
  `wouldOverflow` before growing; on true, `setFault("stack overflow")` and
  dispatch the op's cleanup pad. Requires those ops to CARRY a pad — IR-gen
  attaches one (`attachFaultPad`) to each SP-growing producer (`OP_MAKE_SLICE`,
  `OP_BOX`/iface-value/func-value/string boxing, and the aggregate copy-back
  path). Audit which already sit under a pad.
- **Arg-building restructure (arg-eval-fault leak fix), shared by all calls.**
  `buildCallArgs`: split into EVAL-ALL (genExprOrFuncRef + `coerceArgEager`, args
  land in `ctx.Temps`) then DELIVER-ALL (`coerceArgDelivery` — the `consumeTemp` /
  RefInc / struct-copy phase). Left-to-right eval order preserved; eager/delivery
  act on disjoint type classes so the normal path is behavior-identical. A fault
  anywhere in eval now leaves every evaluated arg still covered.
- **Call-frame pre-check (moved-arg leak fix), approach A.** Between EVAL-ALL and
  DELIVER-ALL, emit `OP_STACK_CHECK(callee)` + `attachFaultPad` (pad covers the
  still-owned args). At that point `vm.SP` already reflects arg-eval growth and
  delivery is SP-neutral (`OP_ALLOC` = frame slot; `consumeTemp`/RefInc = no SP),
  so the check reserves exactly `frameExtent(callee)` and EXACTLY predicts the
  subsequent `pushFrame`. VM resolves the callee (name index) → `frameExtent` →
  `wouldOverflow` → fault. `OP_STACK_CHECK` is VM-only (compiled backends no-op
  it, like `OP_NIL_CHECK`).

## Increments

- **Inc 1 — arg-building eval/deliver split + `OP_STACK_CHECK` for DIRECT calls.**
  The op (iropcode enum [DONE, scaffolding] + `EmitStackCheck` [DONE]) + VM lower
  (`BC_STACK_CHECK`) + VM exec (resolve callee, `wouldOverflow`, fault) + LLVM &
  native ×3 no-ops + `wouldOverflow` helper; restructure `buildCallArgs`
  eval/deliver; genCall (direct) emits the check between. Tests: (i) direct call
  with an `@Iface` arg driven to a frame overflow recovers with Status = FAULTED,
  no leak; (ii) a mid-arg-eval fault after an earlier `@Iface` arg recovers with
  no leak (the split). Conformance stays green.
- **Inc 2 — temp-growth checks (corruption fix).** `wouldOverflow` at every
  SP-growing op + IR-gen pads for them. Test: a single frame overflowing via temp
  growth (a big statement / loop) recovers cleanly, no corruption.
- **Inc 3 — INDIRECT calls** (func-value / iface-method / cross-mode re-entry):
  callee `frameExtent` known only at the runtime dispatch point (after args). Emit
  the check at dispatch before the moved args are consumed, or (fallback) release
  the in-flight moved args on overflow via the existing per-call `ArgIfaceLayout`
  metadata. Decide after Inc 1.

## Notes

- Compiled backends are unaffected (the op is a no-op; they have no recoverable
  stack overflow — OS guard pages).
- `pushFrame`'s check STAYS as the backstop for any path a pre-check doesn't cover.
- Land increments through `main` one at a time, each green + adversarially
  reviewed.
