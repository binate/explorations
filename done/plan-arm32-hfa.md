# Plan: AAPCS-VFP HFA passing as Binate's own arm32-linux convention

Status: COMPLETE (2026-10-15) — stages 0-5 DONE (binate `928bae828` mixed-backend e2e + tuple fix, `95a10dac9` conformance
1642-1644; 2026-10-13; stage 2 binate `cbbf1cf65`, the callconv HFA placement, inert; 2026-10-14);
stage 3, the native arm32 emitters, inert — direct call / param / return binate `0c96052ab`, the
func-value shims `7b6eab741`, the closure shims `c32d006d8`, the C-export trampoline `6114f67e9`;
2026-10-14; stage 4, the switch, binate `cb5bdfae1`, after the two variadic __c_call fixes it exposed,
`476259743` and `8cbcb6cd6`; stage 5, docs `0b10307`; 2026-10-15).  Drafted 2026-10-13, work-3.  User decision (2026-10-13): "1" — on arm32-linux (hard-float,
`types.Arm32HardFloat()`), a homogeneous float aggregate (HFA: a named struct / array of 1-4 same-width
float32 or float64 members, nested ones flattened) is passed and returned by the AAPCS-VFP rules in
Binate's own calling convention, on both backends — lifting ABI deviation (4) (docs/abi/02 §2.1, 04 §4.7)
and the checker's rejection of a `__c_call` HFA return.  Todo entry: claude-todo.md "arm32 hard-float: a
homogeneous-float-aggregate `__c_call` ARGUMENT is passed in GP registers".  Site map: a four-reader
read-only survey (2026-10-13), summarized below.

## The rule (AAPCS-VFP, as LLVM's ARM lowering implements it)

- An HFA argument is a co-processor register candidate allocated as ONE unit: float32 members take the
  lowest run of n consecutive free S slots (any start — back-fill into holes allowed); float64 members the
  lowest run of n consecutive free D registers (2n S slots, even base).  No fit: every remaining VFP
  register is marked used (the bank latches exhausted; later float scalars and HFAs stack too) and the HFA
  goes wholly on the stack — never split, no GP registers.  Stack slot: SizeOf bytes at 8-alignment for
  float64 members, 4 for float32.
- An HFA return: s0..s(n-1) for float32 members, d0..d(n-1) for float64 — never sret, at any size up to
  4 x float64 = 32 bytes.
- Variadic `__c_call`: the base standard for the whole call — every argument (fixed and tail) and the
  return.  An HFA argument is a GP composite (an 8-aligned one starts on an even register; it may split
  r3/stack while the stack is unused); an HFA return is sret above 4 bytes, r0 at 4 bytes.
- Checked against clang `--target=arm-linux-gnueabihf -march=armv7-a`: clang spells fixed HFAs as the
  first-class struct; LLVM lowers the `[N x float]` / `[N x double]` array form identically (back-fill,
  D-after-odd-S, the latch, the s0..s3 / d0..d3 return), so aa64's array spelling (codegen
  aggCoerceLLTy) is reused.  In a vararg call LLVM lowers float arrays like clang's int arrays for
  ARGUMENTS but not for the RETURN (`[2 x double]` comes back in r0..r3, not sret): codegen must apply the
  base rule to a variadic `__c_call` return itself.

## Multi-return tuples: HFA fields stay GP (ABI §2.7)

§2.7 (normative): each non-float field of a multi-return tuple fills consecutive GP return registers; only
float SCALAR fields use the float return registers.  An HFA field is not a float scalar, so it rides GP —
on arm32 as on aa64.  Native follows this; codegen does NOT on aa64: writeFlatMemberLLTy spells an HFA
field with aggCoerceLLTy's `[N x float]`, so LLVM returns it in FP registers.  Verified 2026-10-13 (aa64
host, separate compilation): `func Pair(a float32) (V2f, int32)` with the dependency compiled by LLVM and
main by the native backend prints `9.809089e-45 0 0` (want `1.5 2.5 7`); the reverse prints garbage too;
all-LLVM and all-native are correct.  Filed MAJOR (claude-todo).  The fix is codegen spelling an HFA tuple
field as GP words (`[N x iW]`) on every arch — done before the arm32 flip (stage 1), so the flip, which
would otherwise extend the same mismatch to arm32, leaves tuple placement alone.

## Stages (each lands green)

0. **Tests first** (pass today in every mode; they pin results, not placement):
   - conformance: every HFA shape as argument and result (1-member float32 / float64, 2-4 members of each
     width, `[N]float32` / `[N]float64`, nested), back-fill mixes (`f(f32, D2, f32)`, `f(f64, f32, F2,
     f32)`), overflow (`f(D4, D4, D4)` then a float scalar, then a GP aggregate that must not split),
     HFAs through a func value, a capturing closure (HFA capture + HFA param + float captures filling the
     bank), an interface method, and a multi-return with an HFA field.
   - a mixed-backend e2e (none exists: conformance compiles every package with one backend, so nothing
     checks native<->LLVM agreement — the "cross-package" 968 / 971 comments claiming otherwise are
     stale): separate compilation (`bnc --pkg` with one backend, `-c` main with the other, linked), both
     directions, over the HFA / multi-return / float shapes; runs on the aa64 host and, under qemu in
     binate-ci, on arm32-linux.  The aa64 tuple case is expected-fail until stage 1.
1. **aa64 tuple HFA fields (MAJOR bug)**: codegen spells an HFA tuple field as GP words on every arch
   (writeFlatMemberLLTy / multiReturnCoercedLLTy / the C-entry tuple adapters); the stage-0 e2e case passes.
2. **callconv (inert while arm32's gate is off)**: vfpArgPlace becomes the one CPRC allocator for float
   scalars AND HFAs (N-slot units, all-or-nothing, latch); the three vfp*V walkers and CallArgFpReg consume
   it identically (CallArgFpReg gains an HFA form: base slot + width); a spilled HFA's stack offset /
   footprint; variadicEffType reclassifies an HFA as a plain aggregate in a variadic call (base standard);
   CallReturnsBigAggregate is variadic-aware (a variadic OP_C_CALL's HFA return keeps the base rule); the
   multi-return field walkers stay scalar-only (stage 1); argRegWordsStackWords' monotonic HFA branch and
   advanceNgrn/Nsrn excluded under VfpBackfill.  Unit tests with an AAPCS32 hard-float CC that sets
   HfaAggregates (the cases listed under "Tests").  Verify (and fix if real) the func-value / iface
   PlanFrame sizing: shimDispatchArgTypes sizes a by-address aggregate seam argument by its real type,
   which under-reserves the outgoing area once an HFA counts as VFP.
3. **native arm32 emitters (inert while the gate is off)**: HFA branches ahead of the aggregate GP path in
   emitCallArg, emitSpillParam, emitReturn, emitCallReturn; the func-value shim (HFA-return framed shim,
   marshal / spill-marshal HFA branches, VFP-overflow routing in shimArgsFitRegisters), the closure shims
   (HFA captures, HFA results via the pack core), the c_export trampoline (an in-VFP HFA left in place, an
   overflowed one copied from the C stack slot).  Reuse vfpLoadBaseHard / vfpStoreBaseHard (the arm32
   analog of aa64's hfaMember* helpers).  Unit tests with the forced CC.
4. **The flip, both backends at once**: one shared gate (HfaInSimd renamed HfaInFpRegs = aa64 ||
   Arm32HardFloat; its two "is aa64" users — cexport_multiret.bn CMultiReturnTupleInRegCoerced and
   aarch64_hfa.bn — test the arch explicitly) feeding hfaSimdAggregate (NeedsSret, IsByvalParam),
   aggCoerceLLTy and AAPCS32's HfaAggregates; codegen's variadic `__c_call` return keeps the base rule
   (cCallRetSret and the coerced-return shape, together), variadic HFA arguments spelled as GP for clang
   parity; the checker's arm32 HFA-return rejection deleted; the pinned unit tests flipped.  Validation:
   arm32-linux LLVM and native conformance in Docker, the stage-0 mixed-backend e2e on arm32-linux, a new
   arm32 C-boundary e2e (gcc-arm-linux-gnueabihf C peer under qemu, both backends): `__c_call` with HFA
   arguments and returns (back-fill, overflow, variadic fixed and tail HFAs), `#[c_export]` / `__c_entry`
   taking and returning HFAs, called from C; soft-float bare metal byte-identical.
5. **Docs and comments**: docs/abi 01 / 02 / 04 (delete deviation (4) and §4.7; state the rule under
   §2.5 / §2.6 / §2.9 with the variadic note), docs/spec 16b (the `__c_call` exception); the stale comments
   the survey listed (callconv_ctors / callconv_vfp headers, types.bni HfaInSimd / Arm32HardFloat docs, the
   arm32 shim comments, the conformance runner / 968 / 971 headers, e2e headers).

## Hazards

- Flipping the gate without stage 3 miscompiles silently (an HFA argument emitted as nothing, a return of
  only r0, the c_export trampoline writing at SP-1): stage 4 is one commit with the gate, and stage 3's
  branches are unit-tested before it.
- The VM and interp need no change: they cross into compiled code only through the all-integer dispatch
  seam (an aggregate by address, results through a retbuf), and IsAggregateReturn / AggregateReturnSize /
  IsAggregateArg are unchanged for HFAs.  A types unit test pins that.
- IR shape changes on arm32 hard-float: 3x / 4x float64 HFA params stop being byval (IsByvalParamRef).
- e2e CI: new scripts named `arm32-*.sh` get the arm32 toolchain through the existing workflow.

## Tests (unit, from the survey)

callconv: an HFA alone (S0-S1, D0-D3); back-fill with HFAs (`(f32, f64, F1)` → s0, D1, s1; `(f32, f64,
F2)` → s0, D1, s4-s5; `(f32, D2, f32)` → s0, D1-D2, s1); overflow + latch (6 x f64 then D3 → stack 0,
8-aligned, 24 bytes; a later f32 stacks); C.5 after an HFA spill; HFAs take no GP registers; V-walkers ==
non-V walkers on non-variadic lists; variadic base standard (fixed and tail); ReturnsHfaInRegs /
Func/CallReturnsBigAggregate (not sret up to 32 bytes; `{i32,i32}` still sret; soft-float unchanged);
CEntryRegNormWalk; tuple walkers unchanged.  types: the gate truth table (arm32 hard / soft / default,
aa64, x64); NeedsSret / IsByvalParam / AggRetCoerced; IsAggregateReturn / Arg unchanged.  native arm32:
each emitter's HFA branch (VLDR / VSTR to the right S / D registers, no GP writes, large-frame offsets),
soft-float controls byte-identical.  codegen: a hard-float arm32 test file (fixed HFA declare / call /
define / ret, variadic return sret, shims, c_export alias, tuple HFA field GP).  check: arm32 hard-float
accepts an HFA `__c_call` argument and return.
