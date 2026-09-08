# Plan: tag native/LLVM capturing-closure env so the VM can dispatch it

## Problem

The bytecode VM's func-value dispatcher (`vm_exec_funcref.bn` `execCallFuncValue`,
~L397-411): when a function value's `data` word is non-null, it treats `data` as
a kind-tagged record and reads `closureRec[0]` as a kind (must be
`DATA_KIND_VM_CLOSURE_REC=1` or `DATA_KIND_COMPILED_CLOSURE=2`, else `vmPanic`).
But a NATIVE/LLVM capturing closure (built by `gen_func_lit.bn` /
`gen_method_value.bn`) sets `data = &__closure_<id>` — a raw struct of one field
per capture, **no kind word**. So `closureRec[0]` is capture-word-0 → panic if not
1/2, or **silent misdispatch (memory-unsafe)** if a captured value equals 1 or 2.
Latent today (no conformance mode passes a native capturing closure into VM code),
but rides existing cross-mode infra (`e2e/xmfuncvalue.sh`, `xmiface.sh`; the VM
already dispatches non-capturing native func values via the `data==0` path). The
Phase-3 design (`done/plan-function-values-phase-3.md`) established the contract:
"when `data != null`, its first word is a kind discriminator" — which the
native/LLVM producer violates.

## Decision (owner: option b via option 1)

Make a native capturing closure callable from the VM (not just fail-loud), by
tagging the env: **prepend a `kind int` field as FIELD 0 of the closure struct**,
initialized to a new constant `DATA_KIND_NATIVE_CLOSURE = 3`.  The VM, on kind 3,
routes to `vtable.call(data, args)` — the native closure's own shim already reads
captures from `data` and runs the body (the same shim path non-capturing native
func values already use, via `dispatchCompiledFuncValue`).

Rejected alternatives: (2) a wrapper record `{kind, &struct}` — adds per-call
indirection on the native hot path; (3) a VM-side vtable-ownership set — feasible
but needs a real address set + per-dispatch lookup at multiple readers and moves
the discriminator out of the ABI.  A fixed-offset in-ABI discriminator forces
`data[0]`, so the "capture i -> struct field i+1" shift is intrinsic to (1).

## Design details (from the adversarial review)

- **Kind field type: plain `int`** — already word-sized (`SizeOf(int) == REG_SLOT`
  on every target), so `closureRec[0]` (`*int`, one REG_SLOT read) lands on it.  On
  ILP32 an 8-aligned capture-0 wastes one pad word; not a correctness issue.
- **Two producers, both must change in lockstep**: `gen_func_lit.bn` (closures) AND
  `gen_method_value.bn` (method values — a `{recv}` closure struct,
  NumCaptureParams=1).  Missing the method-value producer misdispatches every
  method-value closure — the biggest completeness trap.
- **The generic struct dtor stays UNCHANGED** (`gen_dtor_emit_bodies.bn`): it
  iterates all fields and RefDecs the managed ones; a leading non-managed `int`
  kind is skipped automatically, managed captures at fields 1..n RefDec at their
  real offsets.  Do NOT shift it.
- **`data` for the VM dispatch is the struct BASE** (kind at offset 0), not past
  the kind word — the shims read captures at the `+1`-shifted `FieldOffset(i+1)`,
  so base-pointer + shifted-reads is consistent.  Reuse `dispatchCompiledFuncValue`
  with `dataPtr = closureRecAddr` instead of nil.
- **Type/offset asymmetry**: the lifted func's `Params[i]` stays capture i
  (UNSHIFTED); only `ClosureStruct.Fields[i]` shifts to `i+1`.  A shim mixing
  `Params[i].Typ` (type) with `FieldOffset(i)` (offset) would be inconsistent —
  standardize: type from `Params[i].Typ`, offset from the helper.
- **CaptureOffsets**: fix the BUILD site only (`lower_func.bn:82`,
  `FieldOffset(i)` -> `FieldOffset(i+1)`); the VM read
  (`vm_exec_funcref.bn` capture-copy) is then auto-correct.
- `vm_exec.bn:459` is a second `data[0]`-as-kind reader; today guarded by
  `dataAddr==0` for native closures (not a live bug) — (1) makes it robust.

## Blast radius (~58 sites, ~17 files) — route through a centralizing helper

Add `ir.Func` helpers `CaptureFieldOffset(i) = ClosureStruct.FieldOffset(i+1)` and
`CaptureFieldType(i) = ClosureStruct.Fields[i+1].Type`, plus `captureLLVMIndex(f,i)
= structLLVMIndex(f.ClosureStruct, i+1)` in codegen.  Route every capture-offset/
type read through them.  Sites:
- native shims: `aarch64/{closure_shim,_float,_aggregate,_aggregate_spill}`,
  `x64/{closure_shim,_float,_spill,_aggregate,_aggregate_spill}`,
  `arm32/{closure_shim,_spill,_aggregate,shim_float}`.
- codegen: `emit_funcvals_closure.bn:145` (`structLLVMIndex`).
- VM: `lower_func.bn:82-88` (CaptureOffsets/ByPtr/Wide build).
- producers/store: `gen_func_lit.bn:145` (`EmitGetFieldPtr(closurePtr, i)` ->
  `i+1`), `gen_method_value.bn:240` (recv store field 0 -> 1); both struct builders
  prepend `kind int` + store `kind=3`.
- constants: `ifaces/core/pkg/builtins/rt.bni` + `impls/.../rt_managed.bn`.

**Grep-gate** (make completeness checkable): after adoption,
`grep -rn 'ClosureStruct\.Fields\[\|ClosureStruct\.FieldOffset(\|structLLVMIndex(f\.ClosureStruct' pkg/`
must match ONLY the helper definitions + the deliberately-raw dtor.  Any other hit
is an un-migrated site.

## Sequencing

1. **Pin test FIRST** (before any shift): a cross-mode e2e — a native-injected
   package whose function hands VM bytecode a CAPTURING closure it calls back;
   ≥2 captures incl. a managed one; all three native backends.  Confirm it
   reproduces the panic/misdispatch on current `main`.  Model on
   `e2e/xmfuncvalue.sh` (reverse direction) + `e2e/xmiface.sh`.
2. Helper + `DATA_KIND_NATIVE_CLOSURE=3` + route all reads through the helper +
   both producers prepend/store the kind.
3. Grep-gate; update `lower_closure_test.bn` FieldOffset(0)/(1) asserts + the
   native shim tests for the shift.
4. Wire the kind-3 dispatch arm (`dataPtr = struct base`, reuse
   `dispatchCompiledFuncValue`).
5. Smoke every changed package (all three native backends' `*_closure_shim_test`,
   `vm/lower_closure_test`, the closure conformance set), then the cross-mode pin.

## Status

- 2026-09-07: claimed; design settled (option 1) after an adversarial review of
  (1) vs (2) vs (3).  Implementation not yet started.
