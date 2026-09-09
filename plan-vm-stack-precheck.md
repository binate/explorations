# Plan: VM stack pre-check (fault before committing a call, not at frame push)

Status: IN PROGRESS (work-2 / session), 2026-09-09. Owner: this session.

## Problem

The VM detects a recoverable stack overflow at `pushFrame` (vm.bn) — i.e. AFTER
the caller has already committed the call's args. A MOVE-model arg (an `@Iface`
or managed-field-struct, `consumeTemp`'d out of the caller's fault-pad live set in
`coerceArgDelivery`) is then orphaned: the callee never runs to release it, and
the caller's call-site pad no longer covers it. One leaked block per
frame-push-overflow with such an arg. (Tracked as the "Frame-push (stack-overflow)
fault leaks a moved-in owned arg" todo; the sibling return-value leak landed as
`99ef6cf4a`.)

## Approach (A): fault before the commit, at a point with a clean live set

Detect the overflow EARLIER — before the args are delivered — so the fault fires
where the fault pad still covers everything owned. A passing check then
guarantees the subsequent `pushFrame` fits.

### Why the placement works (the load-bearing facts)

- `OP_ALLOC` is a pre-sized FRAME SLOT (`lower_func.bn` folds every alloca into
  `vmf.FrameSize`, allocated once at `pushFrame`), NOT a runtime `vm.SP` push.
- Arg DELIVERY (`coerceArgDelivery`) is therefore SP-NEUTRAL: `@Iface` = a
  `consumeTemp` (no op); a borrowed `@Iface` = a `RefInc` (no SP); a struct copy
  = `EmitAlloc` (frame slot) + store + field RefInc + load (no SP). Only arg
  EVALUATION (`buildCallArgs`: make_slice / string-copy / func-value / iface-box)
  grows `vm.SP`.
- So from the END of `buildCallArgs` through `pushFrame`, `vm.SP` is STABLE.

Therefore a check emitted AFTER `buildCallArgs` and BEFORE `coerceArgDelivery`,
reserving `frameExtent(callee)`, sees exactly the `vm.SP` `pushFrame` will see, and
its fault pad (via `attachFaultPad` at that point) still covers the args (fresh
`@Iface` boxes in `ctx.Temps`; borrowed args + the caller's struct originals in
`ctx.Vars`) — no arg has been consumed or copied yet. Fault → pad releases the
originals, no copies exist → clean. Pass → delivery (SP-neutral) → `pushFrame`
(same SP, fits) → no overflow → no leak. Holds for ALL arg kinds on a DIRECT call.

## Design

New VM-only IR op `OP_STACK_CHECK`, modeled exactly on `OP_NIL_CHECK` (a VM/REPL
recovery aid the compiled backends no-op — compiled code has no recoverable stack
overflow, it relies on OS guard pages):

- `iropcode`: add `OP_STACK_CHECK` at the enum END (keep existing values stable —
  `opcodes_test.bn` pins `OP_UNWIND_RETURN`'s position) + its `OpName` case.
  (iropcode is BUILDER-compiled: adding at the end + a switch case is BUILDER-safe.)
- `ir` (`ir_ops`): an `EmitStackCheck(calleeName)` block method (carries the callee
  name, like `EmitCall`, so VM-lowering/exec can find `frameExtent`).
- `gen_call.bn` `genCall` (DIRECT-call path only, Inc 1): after `buildCallArgs`,
  before the delivery loop, `EmitStackCheck(name)` + `attachFaultPad(ctx, b)`.
- VM lowering: `OP_STACK_CHECK` → `BC_STACK_CHECK` (new BC op); carry the callee
  name index. VM exec (`execLoop`): resolve callee (LookupFunc / a cache like
  `CallCache`) → `frameExtent` → if `frameStart + frameExtent > limit` (same
  red-zone / CleanupDepth logic as `pushFrame`, factored into a shared
  `wouldOverflow` helper) → `setFault("stack overflow")` → the check op's pad.
- LLVM `emit_instr.bn`: no-op (return, like `OP_SP_RESTORE`/`OP_NIL_CHECK`).
- native x64/arm32/aarch64 `*_dispatch.bn`: `case OP_STACK_CHECK: return` (no-op,
  like `OP_NIL_CHECK`). (A pure no-op is not a regalloc clobber point — no
  `regalloc_clobber` change needed, but confirm.)
- `pushFrame`'s existing overflow check STAYS as a backstop (indirect calls, and
  a safety net) — it should never fire for a pre-checked direct call.

## Increments

- **Inc 1 — DIRECT calls (all arg kinds).** The op + backends + IR-gen (direct
  path) + VM + the shared `wouldOverflow` helper. Test: a direct call passing an
  `@Iface` (and a managed-struct) arg, driven to a `pushFrame` overflow, recovers
  with Status = FAULTED and NO leak (rt.LiveBlocks stable across repeats);
  conformance stays green (the check never mis-fires on normal calls).
- **Inc 2 — INDIRECT calls** (func-value / iface-method / cross-mode re-entry):
  the callee — and its `frameExtent` — is only known at the runtime dispatch
  point, after args are evaluated. Options: emit the check at the dispatch point
  (before consuming the moved args), or fall back to releasing the in-flight moved
  args on overflow (the "owned-arg bitmap" variant, reusing the existing per-call
  `ArgIfaceLayout` metadata). Decide after Inc 1 is proven.

## Non-goals / notes

- Compiled backends are unaffected (the op is a no-op there).
- This generalizes: a conservative pre-check makes EVERY recoverable
  stack-overflow fault fire at a clean call boundary, not just the moved-arg case.
