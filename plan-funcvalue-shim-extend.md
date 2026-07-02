# Plan: shim-extends sub-word RETURN (drop the VM narrow) — LANE 3 item-1 cleanup

**Status:** planned, not started (2026-07-02). Optional / cosmetic — the sub-word-
return concern is ALREADY fixed VM-side (the `25117a2e` VM-narrow mechanism,
extended to iface/func-value). This just relocates where the extension happens so
the shim is ABI-correct at the boundary independent of the VM. Low priority.

## What / why

A func-value / interface-method **shim** (the trampoline that unpacks the closure
data pointer and calls the real target) whose target returns a **sub-word** value
(int8 / int16 / bool, i.e. narrower than the target word) must sign/zero-extend
that return so the caller sees the full-width register. Today the VM compensates
by NARROWING on its side (`25117a2e`). The review's cleaner design: every backend's
shim does the sext/zext itself (matching the standard C-ABI expectation that a
callee returns an extended sub-word), and the VM narrow is dropped. Net: one
mechanism (in the shims) instead of a VM-side special case.

## Sites

- **LLVM shim:** `pkg/binate/codegen/emit_funcvals_shim.bn` (+ `emit_funcvals.bn` /
  `emit_funcvals_sig.bn`). LLVM likely already extends via the `signext`/`zeroext`
  return attribute or the IR return type — CHECK whether the LLVM shim already
  yields an extended sub-word (if so, LLVM needs no change and the cleanup is
  native + VM only).
- **aa64 shim:** `pkg/binate/native/aarch64/aarch64_funcvalue.bn` — **the wrinkle
  lives here.** Line ~216: "the shim tail-branches to the (extern) target." A
  tail-branch (`B`/`BR` to the target, no return to the shim) CANNOT post-process
  the return. To extend, the shim must instead: set up a frame, `BL` (call) the
  target, `SXTB/SXTH`/`UXTB/UXTH` (or AND-mask) the result reg to the return width,
  then `RET`. That is the "tail-branch → call-shape" change: it gives the shim a
  real stack frame (save/restore LR) it doesn't have today.
- **x64 shim:** `pkg/binate/native/x64/x64_funcvalue_shim.bn` (+ `x64_iface.bn`) —
  same wrinkle: a `jmp` tail-call becomes `call` + `movsx`/`movzx` + `ret`.
- **arm32 shim:** `pkg/binate/native/arm32/*` — same, target-word = 4 bytes.
- **VM narrow (to remove):** locate in `pkg/binate/vm` — the func-value / iface
  RETURN lowering that applies `BC_SEXT`/`BC_ZEXT` to a sub-word shim result
  (introduced by `25117a2e`; grep the VM call/return lowering + that commit's diff).
  Remove it ONLY after all backends' shims extend, else the VM double-processes or
  under-processes.

## Target-word dependence

The extend WIDTH is `min(returnTypeWidth, targetWord)`: a sub-word return is
extended to the target word (8 bytes on LP64, 4 on ILP32). The sext-vs-zext choice
follows the return type's signedness (int8/int16 → sext; uint8/uint16/bool → zext).
Reuse the existing per-backend sub-word extend helpers (the same ones a normal
`cast(int, int8Val)` / narrowing store emit — grep `SXTB`/`movsx`/`BC_SEXT`).

## Verification

The concern is "VM-only" per the todo, so native/LLVM sub-word shim returns likely
already work; the risk is a REGRESSION when relocating. Approach:
1. Find the existing cross-mode sub-word-return tests (grep conformance / the
   `25117a2e` test additions; likely a func-value / iface method returning int8 /
   bool). They MUST stay green after the change on all modes (VM + native + LLVM).
2. Add a NATIVE-only test that a func-value/iface shim returning e.g. `int8(-1)`
   is seen as full-width `-1` by the caller (would have needed the VM narrow before;
   proves the shim now extends). A cross-ABI C-driver style test (see
   plan-native-hfa-abi.md) could pin it if a pure-native test is self-consistent.
3. Sequence: add all backends' shim extends FIRST (verify green), THEN drop the VM
   narrow (verify still green) — never drop the VM narrow before the shims extend.

## Recommendation

Cosmetic; do it only if the VM-narrow special case is a genuine maintenance burden.
The functional behavior is already correct. If done, stage per-backend (LLVM check →
x64 → aa64 → arm32 → drop VM narrow), each verified, mirroring the HFA staging.
