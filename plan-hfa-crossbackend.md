# Plan: HFA passing as a cross-backend ABI contract

**Status:** planned (2026-07-02), not started. Supersedes the *staging* of
`plan-native-hfa-abi.md` (which is marked NEEDS REPLAN). The native aa64 arg path
from that effort is in-tree, **dormant** (`cc.HfaAggregates=false`), and correct —
it is reused here.

## Why (the lesson that reshaped this)

A Homogeneous Floating-point Aggregate (HFA) — a struct/array folding to 1–4
members all the same float type — is passed in SIMD registers under AAPCS64
(v0..v7) and by eightbyte-SSE classification under x64 SysV. HFA stage 1 enabled
this on the **native aa64 arg path only** (`332b4298`), was AAPCS64-correct against
a clang caller, and was then **gated back off** (`8b884642`) because an adversarial
review proved it miscompiles/crashes: HFA passing is an **ABI contract shared by
every backend, every dispatch shim, and the VM boundary**, and enabling one half
made it disagree with the others.

**The correctness bar is NOT "native matches clang (AAPCS64)". It is "every part of
the toolchain that touches an HFA agrees with every other part."** Under
`-backend native`, only a program's main module is native; **every dependency
package is compiled by the LLVM backend** (`cmd/bnc/compile.bn`), and dispatch
(func-value / closure / interface / VM cross-mode) goes through per-function shims.
So the parts that must agree are: LLVM codegen (params + returns + call sites),
native aa64 (args — done — + returns + shims + variadic walkers), x64, and the
shared classifier they all consult. The VM inherits correctness from the shims.

## The disagreement, verified

`bnc --emit-llvm` on `func fnS(v D2) float64` (D2 = `{f64,f64}`):

    define double  @…fnS([2 x i64] %v0.ag)     ; HFA arg  -> GP x0/x1, NOT v0/v1
    define [2 x i64] @…mkD2(double, double)     ; HFA return -> GP x0:x1, NOT v0:v1

clang for the same C struct emits `%struct.D2` / `[2 x double]`, which LLVM lowers
to v0:v1. So native (SIMD, when enabled) ≠ LLVM backend (GP `[N x i64]`). The native
side is the AAPCS64-correct one; the LLVM backend is the non-conformant one, but
consistency *within the toolchain* is what governs.

## Architecture (two foundational pieces)

### A. Lift the HFA classifier into `pkg/binate/types` (shared)

`hfaFold` / `HfaClassify` / `hfaMemberCount` live in
`pkg/binate/native/common/common_callconv.bn` today and depend only on the
`types.Type` API (`.Kind`, `.Fields`, `.Elem`, `.Width`, `.SizeOf`) plus
`peelTransparent`. The import graph is verified acyclic: `codegen → types`,
`native → types`, `types → neither`. So the classifier moves to
`pkg/binate/types` (into `abi_return.bn` beside `AggInRegCoercedKind` / `NeedsSret`
/ `AggRetCoerced`, or a new `abi_hfa.bn`), swapping `peelTransparent` for the
package-local `StripWrappers`. Both `codegen` and `native` then consult the ONE
source of truth. Per `ir-backend-guidelines.md`, ABI/layout classification is a
language-level contract that belongs in a shared layer, not a backend — this is
exactly that.

**BUILDER note:** `types` and `codegen` are in cmd/bnc's frozen-BUILDER tree.
`hfaFold` uses multiple return values, `.Fields` iteration, named types, `SizeOf()`
— all already used in BUILDER-compiled code, so the move is expected to be
BUILDER-safe; verify by building gen1 after the move.

### B. Give codegen a target/arch discriminator

`TargetInfo` (`types.bni`) is `{PointerSize, IntSize, MaxAlign, BigEndian}` — **no
arch field**, and aa64 + x64 are both LP64, so `pkg/binate/codegen` currently
CANNOT tell them apart. HFA classification is per-target (aa64: 1–4 same-width
members up to 32B; x64: eightbyte-SSE, ≤16B only, mixed-width allowed). So add an
`Arch` (enum: AA64 / X64 / ARM32 …) field to `TargetInfo`, set by
`cmd/bnc/target.bn:applyTarget` alongside the clang triple, and read by codegen's
HFA classification. Without this, codegen cannot emit the aa64 vs x64 HFA form.

## The crux: LLVM codegen HFA lowering

Single param chokepoint: `writeParamTypeLLVM` (`emit_util.bn:286`):
`>16B → ptr byval`; `≤16B named-struct/array → [N x i64]` (`aggParamCoerced` →
`aggCoerceLLTy`); else `llvmType`. Returns mirror via `aggRetCoerced` /
`emitAggReturn` (`emit_agg_coerce.bn`) and the `funcRetTypes` map (`emit.bn`).
Call-site + iface + func-value coercion also route through `emit_agg_coerce.bn`.

The fix: when the shared classifier says a type is an HFA *for the current target*,
emit the SIMD-lowering LLVM form instead of `[N x i64]` — verified against clang:
- **aa64**: `[N x float]` / `[N x double]` array (empirically arrives in d0 directly,
  no `fmov` from GP). A literal `{double,double}` works identically. Applies to
  params AND returns AND call-site args (one coercion, consumed by both LLVM caller
  and callee, so LLVM↔LLVM stays self-consistent).
- **x64**: differs — `≤16B all-SSE → <2 x float>` / `double` / `{double,double}`;
  `>16B → ptr byval` (a `[N x double]` array does NOT auto-become MEMORY at >16B on
  x64). So the emitted form must be arch-gated (needs piece B).

`llvmType` already spells `[N x float]`/`[N x double]`; the new work is choosing it
for HFAs and reworking the param prologue / return pack so an `[N x float]` param
binds directly (no `[N x i64]` spill-and-reconstruct).

## Staging (each stage keeps the tree green; flag flips ON only at the end)

The invariant every stage preserves: **for any HFA program, native == LLVM ==
clang/expected, INCLUDING the cross-module (native-main + LLVM-dep) topology.**
That cross-module check (a `337_cross_pkg_struct_arg`-style test) is the gate the
original effort lacked.

**Stage 0 — plumbing (no behavior change, safely landable now):**
- Lift `HfaClassify`/`hfaFold` to `pkg/binate/types`; re-point native consumers.
- Add `TargetInfo.Arch`, set in `applyTarget`, expose to codegen.
- Fix the 3 variadic V-walkers (`common_callconv_variadic.bn:38/64/86`):
  `if IsFloatScalarTyp{nsrn++}` → `cc.advanceNsrn(...)` (adversarial finding #3).
  Dormant while `HfaAggregates=false`; makes the walkers consistent with the
  non-V ones.
Verify: full build + conformance unchanged (pure refactor + dormant fix).

**Stage 1 — LLVM codegen HFA args + returns (aa64), gated by Arch==AA64:**
- `emit_agg_coerce.bn` / `emit_util.bn` / `emit_helpers.bn`: emit `[N x float]`/
  `[N x double]` (or `{…}`) for an aa64 HFA param/return/call-arg instead of
  `[N x i64]`; rework the prologue/return pack; update `funcRetTypes` + extern
  declares + iface + func-value-sig sites in lockstep.
- x64 path unchanged (Arch gate) → x64 stays GP-consistent.
Verify: all-LLVM (`builder-comp`) HFA programs compute correctly and the emitted IR
places HFAs in v-regs (clang-confirmed). This makes the LLVM backend the
AAPCS64-correct reference native must match.

**Stage 2 — native aa64 returns + all dispatch shims:**
- Shared HFA-return predicate (in `types`/`common_callconv_return.bn`), run BEFORE
  the size>16 sret decision (an HFA is ≤32B for 4×f64 yet still v0..v3).
- `aarch64_return.bn:emitReturn` HFA branch (FMOV member m → D0+m); caller collect
  in `aarch64_call.bn` (the non-big single-agg branch must skip the GP X0.. store).
- Shims — every marshaler that touches aggregate args/returns:
  `aarch64_funcvalue_shim.bn` (emitShimArgMarshalAA64 + return pack),
  `aarch64_closure_shim_float.bn` (`closureHasFloatParts` must detect HFAs;
  `marshalFloatShimArgAA64` HFA branch), `aarch64_closure_shim_aggregate.bn`
  (return pack), `aarch64_closure_shim.bn` (route HFA closures to the float-aware
  shim), `aarch64_iface.bn` (arg FMOV + return via collect), plus the stack-spill
  variants for FP-overflow HFAs.
- Native args are already dormant-ready (`aarch64_call.bn` / `aarch64_emit_func.bn`).
Verify: native == LLVM (from stage 1) across args + returns + each dispatch kind,
single-program AND cross-module.

**Stage 3 — flip the aa64 switch + comprehensive tests:**
- `AAPCS64_Darwin(): cc.HfaAggregates = true` (the Arch-gated classifier keeps x64
  GP-consistent, so the shared flag is safe).
- Tests (single-program tests provably can't catch the real bugs — all cross-module
  or cross-dispatch):
  - cross-module HFA **arg** (clone `337_cross_pkg_struct_arg`)
  - cross-module HFA **return** (clone `683`/`636`, bit_cast per field to dodge 962)
  - HFA through **func-value** (clone `340`), **closure**, **interface** (clone `358`)
  - negative controls: non-homogeneous `{f64,int}`, >4 members, >16B — must NOT
    SIMD-pass; and the aa64-vs-x64 divergent case (24B `[3]f64` = HFA on aa64, MEM
    on x64)
  - a `TestHfaCalleeFromC` cross-ABI unit test (clang caller ↔ native callee) in
    `aarch64_test.bn` (findRuntimePath/canLinkAndRun harness) — the strongest gate
  - run in `builder-comp_native_aa64-comp_native_aa64` (the native↔LLVM boundary)
    AND `builder-comp` (all-LLVM) AND a VM mode.

**Stage 4 — x64 SSE HFA (Option B, in scope):** a per-target eightbyte-SSE
classifier (≤16B all-SSE → XMM, incl. 2×f32-per-XMM and mixed-width `{f32,f64}`;
>16B → MEM — diverges from aa64 which keeps ≤4-member HFAs); `x64_call.bn` /
`x64_emit_func.bn` / `x64_return.bn` XMM arg+return placement (generalizing the
existing scalar-float XMM path); the x64 dispatch shims; new SSE pack/extract
opcodes in `asm/x64/x64_fp.bn` (2×f32 into one XMM eightbyte — `movlps`/`movhps`/
`unpcklps`/`insertps`); and arch-gated x64 LLVM coercion in `emit_agg_coerce.bn`
(`<2 x float>` / `double` / `{double,double}` / split `{double,i64}`). Then extend
the flag/tests to x64 modes (`builder-comp_native_x64*`).

## Decisions (settled)

1. **x64 scope: OPTION B — full x64 SSE HFA is IN SCOPE (user, 2026-07-02).** This
   effort implements textbook SysV eightbyte-SSE HFA on x64 as well as aa64: the
   per-target classifier rule + x64 call/emit_func/return + x64 dispatch shims +
   new SSE pack opcodes in `asm/x64/x64_fp.bn` + arch-gated x64 LLVM coercion. So
   Stage 4 below is a first-class part of the plan, not a deferred follow-up.
2. **Land Stage 0 independently: yes.** Pure refactor + dormant walker fix, safely
   landable on its own, shrinks the risky stages.
3. **HFA return width.** AAPCS64 allows 4×f64 = 32B HFAs in v0..v3, above the 16B
   sret threshold — the HFA-return check must precede the size-based sret decision
   on both func and call sides (confirm during Stage 2 / Stage 4).

## Verification methodology (non-negotiable)

Every stage is gated on the **cross-module native-vs-LLVM** check, not single-program
self-consistency. A single-program native-vs-LLVM comparison passes even when both
are wrong-but-matching; the bug only surfaces at native-main ↔ LLVM-dep and through
dispatch shims. Wire a `337`-style cross-module HFA test early and keep it green at
every stage.
