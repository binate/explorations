# Plan: native arm32 dispatch-seam → positional all-integer (match LLVM/VM)

Status: Phase A LANDED (`c3caaef29`, 2026-09-06); Phase B (hard-float) open.
Owner decision made: the dispatch seam contract is **positional all-integer**
(spec `abi/03` §3.3); the native arm32 backend is the divergent side and gets
fixed. Tracks `claude-todo.md` "ABI review #3".

## The contract (authoritative)

`docs/abi/03-dispatch-convention.md` §3.3 — the func-value / iface / cross-mode
**dispatch seam** is **all-integer and positional**: slot *g* is the *g*-th GP
argument register, then the stack. Per arg type:

- an **aggregate of any size** rides **one pointer slot** (its address); the
  shim loads + re-marshals to the underlying's direct-call convention.
- a **float scalar** rides its **bit image in an integer slot** (float32 → 32-bit,
  float64 → 64-bit); **no float register on this seam**. A float scalar *result*
  returns in the **GP** return register as its bit image.
- on ILP32 a **64-bit scalar arg splits into two consecutive 32-bit slots**
  (low word, then high word); a bare 64-bit scalar *result* uses the 64-bit
  scalar shape (§3.5, register pair).
- narrow scalars ride one slot, low bits only.

Crucially: **no AAPCS32 even-register-pair alignment on the seam**, and **no VFP
on the seam**. The seam is a flat sequence of 32-bit slots.

### Both reference producers already implement exactly this

- **VM** (`pkg/binate/vm/lower_slots.bn`): `argSlots(t)` = 2 for a 64-bit scalar
  (int64/uint64/float64/untyped-float) on ILP32, else 1; the slot cursor
  advances by `argSlots(t)` with **no parity rounding**. Aggregates = 1 slot.
  Floats ride GP bit images (the VM has no VFP at all).
- **LLVM** (`pkg/binate/codegen/emit_funcvals_sig.bn`,
  `emit_call_funcvalue.bn`): the shim signature is **pre-split** — a 64-bit
  scalar is declared as **two separate `i32` params** (`is64ScalarShimSplit`),
  a float scalar as its int slot (`shimIntSlotType` — `bitcast` to i32/i64), an
  aggregate as `i8*`. Because the shim's own params are pre-split i32s, clang's
  AAPCS32 lowering places them positionally — **no `i64` param exists to trigger
  even-pair alignment, no float param to trigger VFP.** The caller
  (`emitFuncValueArgPreamble`) mirrors this: `trunc`/`lshr` a 64-bit scalar into
  lo/hi i32s, `bitcast` a float to its int slot.

## Where native arm32 diverges

Native arm32 places seam args through the **full AAPCS32 machinery**
(`shimArgTypesArm32` builds argTypes with the *real* types; the caller uses
`cc.CallArgRegStart` / `emitCallArg`; the shim's incoming cursor mirrors it).
That machinery applies to the **seam**:

1. **Even-register-pair alignment** (AAPCS §6.5 C.3) for 64-bit scalars and
   8-aligned aggregate pointers — on BOTH the caller's placement and the shim's
   incoming cursor (`paddedKeepsTypeArm32`, the `isPair64Typ` incoming bump,
   `shimInWordsArm32`'s incoming pad). The spec seam is flat positional. Caller
   and shim agree *with each other*, so same-producer (compiled arm32 →
   compiled arm32) works; the placements coincide with LLVM/VM only at **even
   register parity**. At odd parity a compiled arm32 caller ↔ VM/LLVM shim (or
   vice-versa) misplaces every arg after the first 64-bit/8-aligned one.

2. **VFP float placement on the seam (hard-float only)**: under
   `Arm32HardFloat()` (target `arm32-linux`) a float scalar rides a VFP
   register on the seam — the caller places it in s/d regs (`emitCallArg` hard
   path), the shim skips it from the GP marshal (`hardFloatShimSkipArm32`,
   assuming it already sits in VFP). The spec seam carries floats as GP bit
   images. An entire subsystem (`arm32_shim_float.bn` + closure VFP up-shift +
   multi-return FP-aware store; ~41 `CallArgFpReg`/`…FloatB1…` sites) is built
   on the VFP-on-seam assumption.

## Fix design

Make the native arm32 seam a **flat positional 32-bit-slot layout** on both
ends; leave the shim's **outgoing** re-marshal to the underlying's *real*
AAPCS32 convention (even-pair, VFP for hard-float) unchanged — the shim's job is
exactly seam→AAPCS32.

Two phases, because the float ABIs differ sharply in blast radius.

### Phase A — soft-float seam (`arm32-baremetal`): drop seam even-pair padding — LANDED `c3caaef29`

On soft-float, floats already ride GP as their same-width integer twin, so the
*only* divergence is the even-pair padding. The outgoing (underlying-AAPCS32)
even-pair stays; the **incoming/seam** even-pair goes.

As landed, the caller went further than "pre-split argTypes": a dedicated flat
placer (`emitShimUserArgsFlatArm32` / `placeSeamWordArm32`) computes flat slots
directly from `ins.Args` (bypassing `CallArgRegStart`'s even-pair), and the
incoming pads are gated by a single predicate `seamIncomingEvenPairArm32` =
`Arm32HardFloat() && paddedKeepsTypeArm32` — false (flat) on soft-float, and
equal to the old condition on hard-float, so **hard-float is byte-identical**
(Phase B untouched). The classification predicates were split out to
`arm32_funcvalue_classify.bn` (+ test) for the length cap.

- **Caller** (`shimArgTypesArm32` + `emitShimUserArgsArm32`, shared by
  `emitCallFuncValue` and `emitCallIfaceMethod`): place seam args
  flat-positionally. Pre-split a 64-bit scalar into two consecutive 32-bit
  slots (lo, hi — no even alignment); aggregates already ride one `*uint8`;
  32-bit scalars one slot. (Mirror the LLVM pre-split rather than routing real
  int64 through `CallArgRegStart`'s even-pair.)
- **Shim incoming cursor** (`emitShimArgMarshalArm32`, `shimInWordsArm32`,
  `shimOutRegs`): drop the incoming even-pair bumps (the `srcPrefix + srcWord`
  parity bumps in the `isPair64Typ` and `paddedKeepsTypeArm32` branches, and the
  incoming pad in `shimInWordsArm32`). Keep the outgoing `gpDestBase + ngrn`
  even-pair (→ underlying AAPCS32). `paddedKeepsTypeArm32` collapses to an
  outgoing-only concern.
- **Spill shim** (`emitFuncvalSpillShimArm32` / `emitSpillMarshalArm32`) and
  **closure shim** (`arm32_closure_shim*.bn`): same incoming-side flattening.

### Phase B — hard-float seam (`arm32-linux`): floats ride GP bit images

- **Caller**: force seam float args into GP slots as bit images (not VFP), and
  read a scalar float *return* from R0 (bit image), bitcast to float. This
  *removes* the hard-float seam handling at the call site (the
  `callInstrUnhandledFloatB1Arm32` fail-loud, the VFP-spill deferral) — seam
  calls become GP-only like soft-float.
- **Shim**: gain a **GP-slot → VFP-register** marshal for each float arg
  (`VMOV`/`VLDR` the bits into the s/d register the underlying reads) and a
  **VFP → GP** step for a scalar float return (underlying returns in VFP → shim
  moves to R0 for the seam's GP bit-image return). `hardFloatShimSkipArm32` and
  the VFP-on-seam subsystem (`arm32_shim_float.bn`, closure VFP up-shift,
  multiret FP store) are reworked to this model.

## Site inventory (native arm32)

Caller: `arm32_call_indirect.bn` (`emitCallFuncValue`, `shimArgTypesArm32`,
`emitShimUserArgsArm32`), `arm32_iface_dispatch.bn` (`emitCallIfaceMethod`).
Shim: `arm32_funcvalue_marshal.bn`, `arm32_funcvalue_shim.bn`,
`arm32_funcvalue_spill.bn`, `arm32_funcvalue_multiret.bn`,
`arm32_closure_shim*.bn`. Hard-float subsystem (Phase B): `arm32_shim_float.bn`,
`arm32_call_hard.bn`, and the `Arm32HardFloat()` seam sites in
`arm32_call_return.bn` / `arm32_call.bn`.
Shared predicates encoding the divergence: `paddedKeepsTypeArm32`,
`hardFloatShimSkipArm32`, `shimInWordsForTypeArm32`, `isPair64Typ` (seam use).

## Test plan / how "cross-producer" is actually covered

A note on "cross-producer": the conformance harness compiles a whole test with
ONE backend per run, so a single run is same-producer. Genuine backend-mixing
(native caller ↔ LLVM/VM shim in one binary) is not a single-run conformance
thing here. What actually pins the cross-producer contract is: the **unit tests
pin native's caller AND shim to the exact flat layout** the LLVM/VM contract
defines (so neither can silently re-diverge to even-pair — a self-consistent
native regression would still redden the byte-refs), **plus** the odd-parity
conformance tests producing a producer-independent expected value under native,
LLVM, and VM modes.

Landed coverage (Phase A):
- **Unit** (soft-float, `setArm32Target()`): the 5 byte-ref tests that pinned
  the incoming even-pair now pin the flat layout — `(int64,)` and `(float64,)`
  (marshal), `(struct24,)` indirect-large (marshal), sret+`(int,int64)` with a
  reg/stack **straddle** (spill), 1-cap closure `(int64)` (closure). Plus a new
  **caller** byte-ref `TestEmitShimUserArgsFlatInt64ByteRefArm32` pinning
  `emitShimUserArgsFlatArm32`, and the classification-predicate tests (moved to
  `arm32_funcvalue_classify_test.bn`).
- **Conformance** (`builder-comp_native_arm32_baremetal`, soft): the odd-parity
  int64 func-value tests 1018 (incl. int64-first `sinkR0R1`), 1019 (spill), the
  cross-package 1020, and 1006 (native-dispatch shape) all pass; 548
  func-value/iface/closure tests pass, 0 failed. Hygiene 20/20.
- Hard-float unit tests pass; hard-float paths are byte-identical (gated), so no
  regression. Native hard-float conformance (`builder-comp_native_arm32_linux`)
  needs `qemu-arm` — CI covers it.

Phase B will add hard-float unit coverage (currently `seamIncomingEvenPair ==
true` is unit-uncovered — the reviewer's one note) and the GP-bit-image seam
tests.

## Risk / verification

High miscompile risk (silent wrong-arg-register). Verify by
disassembly-diffing a known-good (LLVM/VM) vs native build of the same
odd-parity crossing (per CLAUDE.md "Debug Miscompiles by Disassembling …
EARLY"), not by theorizing. Land Phase A and Phase B separately; each must keep
all native arm32 conformance + unit tests green.
