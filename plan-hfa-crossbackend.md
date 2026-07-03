# Plan: HFA passing as a cross-backend ABI contract

**Status:** in progress (2026-07-02). Stage 0 landed (`06f9a8ff` classifier lift,
`d69eded8` variadic NSRN fix). **Stage 1 landed** (dormant): prereqs `7692508e`
(TargetInfo.Arch + the `HfaInSimd()` master gate), codegen lowering `9ebf4119`
(LLVM backend passes HFAs in SIMD). Both adversarially reviewed SOUND. **Stage 2a
landed** (dormant, `4bc6fa7c`): native aa64 HFA returns in D0..D3 + the
`ReturnsHfaInRegs` classifier (with the AggInRegCoercedKind guard that closes the
Stage-1 classifier-agreement carry-forward). **Stage 2b implemented** (dormant,
worktree `3e416126`): native dispatch shims (func-value / closure / interface)
marshal HFAs — verified flip-on across all dispatch kinds + cross-module
(native main -> LLVM dep). `IsAggregateReturn`/`AggregateReturnSize` correctly
needed NO change (recon). ONE gap remains, failing LOUD (SetError, never silent):
the non-capturing WIDE-arg func-value stack-spill shim
(`aarch64_funcvalue_spill.bn`). **Next: finish the spill shim, then Stage 3 (flip
+ comprehensive tests).** Supersedes the *staging* of
`plan-native-hfa-abi.md` (which is marked NEEDS REPLAN). The native aa64 arg path
from that effort is in-tree, **dormant** (`cc.HfaAggregates = HfaInSimd()`,
currently false), and correct — it is reused here.

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

### Stage 1 groundwork (from the 2026-07-02 code survey) — the exact wiring

The precise edit sites and the two plumbing prerequisites, so the codegen work can
start cold:

**Codegen edit sites (all must move in lockstep — a param-only change would make
caller/callee disagree):**
- `emit_util.bn:writeParamTypeLLVM` (~:286) — the single param-type chokepoint;
  add an HFA branch above the `[N x i64]` (`aggParamCoerced`) case.
- `emit_agg_coerce.bn` — `aggParamCoerced`/`aggRetCoerced`/`aggCoerceLLTy` (the
  `[N x i64]` writer), the param prologue reconstruction, `emitAggReturn` (:197),
  and the call-site + iface arg/result coercion (`emitAggCallArgPreamble`/
  `writeAggCallArg` :254/:286; `emitAggIfaceArgPreamble`/`writeAggIfaceArg`).
- `emit_helpers.bn:emitReturn` (~:260) — the return-shape switch (sret vs
  `[N x i64]` vs first-class vs scalar); HFA return goes to the SIMD form.
- `emit.bn` — extern declare param/return types (~:201-233) and the `funcRetTypes`
  map (~:259-284) that call sites read for the ret spelling; both must spell the
  HFA form.
- `emit_iface_call.bn` (iface thunk param typing, :110) and
  `emit_funcvals_sig.bn` (func-value/closure shim sigs — aggregate args are `i8*`
  today; an HFA can't ride the all-int shim, ties to native finding #2).

**Prerequisite A — `TargetInfo.Arch` (codegen can't tell aa64 from x64 today).**
`TargetInfo` = `{PointerSize, IntSize, MaxAlign, BigEndian}` (types.bni:480); both
aa64 and x64 are LP64 and the LP64 targets in `cmd/bnc/target.bn:applyTarget` skip
`SetTarget` entirely, so codegen has no arch signal. Add `Arch int` (consts
`ARCH_AA64`/`ARCH_X64`/`ARCH_ARM32` in `types`). Set it per `--target`, and give
the HOST default the compiled-in host arch — `initTarget` (`layout.bn:12`) already
measures host layout from `sizeof`, but there is no arch primitive; the host arch
is `build.Arch` (used by `buildcfg.HostConfig`, `buildcfg.bn:42`). Cleanest: a
`types.SetArch(int)` that stamps just the field, called from `applyTarget` for
EVERY key (host included, from `build.Arch` — cmd/bnc can import build; check
`types`↔`build` has no cycle before putting arch consts where they cross). Also
fix `nativeArchForTarget`'s hardcoded `"aarch64"` no-triple fallback to read the
host arch. On this Apple-Silicon dev host the default must resolve to `ARCH_AA64`
so all-LLVM verification exercises the aa64 form.

**Prerequisite B — a single master gate consulted by BOTH backends.** Stage 1 makes
the LLVM backend pass HFAs in SIMD; if that ships while native still passes GP
(`HfaAggregates=false`), native-main↔LLVM-dep breaks the SAME way (reversed). So
codegen's HFA emission and native's `HfaAggregates` MUST flip together. Add one
predicate `types.HfaInSimd()` (initially `return false`; later `GetTarget().Arch ==
ARCH_AA64` once native+shims are ready, then `|| == ARCH_X64` after Stage 4).
Rewire native `AAPCS64_Darwin()` from the hardcoded `cc.HfaAggregates = false`
(common_callconv.bn) to `cc.HfaAggregates = HfaInSimd()`, and gate every codegen
HFA branch on `types.HfaInSimd()`. One flip enables both halves in lockstep, so
the tree stays GP-consistent (green) through Stages 1–2 with the codegen change
landed but DORMANT. Verify by a TEMPORARY flip build: all-LLVM HFA programs
compute correctly and `--emit-llvm` shows the HFA in v-regs (clang-confirmed),
then revert the flip and land dormant.

**Sequencing note.** Because the gate keeps it dormant, Stage 1's codegen change is
landable green, but it should get its OWN adversarial review (cross-module + every
coercion site + a temporary-flip all-LLVM run) before landing — it is the highest-
risk change in the effort.

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

**Stage 1 — LLVM codegen HFA args + returns (aa64) — LANDED (`7692508e`, `9ebf4119`):**
Implementation was *far* smaller than this bullet anticipated. The existing
in-register-aggregate coercion is a store-struct / load-coerced-type idiom over a
shared slot, and an HFA's struct, `[N x i64]`, and `[M x float]`/`[M x double]`
views all share ONE byte layout — so the whole thing is driven by THREE edits, and
every coercion site (param define-lines via `writeParamTypeLLVM`, returns via
`emitReturn`/`funcRetTypes`/declares, call args, iface thunks, func-value sigs)
routes through them automatically:
- `types.hfaSimdAggregate(t)` — the exact set codegen SIMD-coerces (an
  `AggInRegCoercedKind` aggregate folding to a 1-4 float HFA, when `HfaInSimd()`).
- `types.NeedsSret` + `types.IsByvalParam` exempt that set — so a >16-byte HFA
  (3x/4x f64 = 24/32 B) reaches the SIMD-coerced path instead of sret / byval,
  riding v0..v3 regardless of size (settled decision 3, handled here not deferred).
- `codegen.aggCoerceLLTy(t)` spells `[M x float]` / `[M x double]` for that set.
The x64 path is untouched (x64's `Arch` gate keeps `HfaInSimd()` off for it until
Stage 4), so x64 stays GP-consistent.
Verified by a temporary flip of `HfaInSimd()` → `Arch==AA64` (reverted before
commit): all-LLVM (`builder-comp`) HFA programs — conformance 963/964 args incl.
24B/32B, plus an HFA-return program (mkD2/mkD3/mkD4/mkF2) — compute correctly; the
emitted IR shows `define [3 x double] @mkD3(...)` etc. (no sret, no `[N x i64]`) and
`llc -O2` places the members in d0..d2 (textbook AAPCS64 HFA passing). Dormant: all
affected unit tests (types/codegen/native/ir/vm) + conformance 962/963/964 unchanged.
A `TestHfaDormantWhileGateOff` tripwire pins the Stage-1↔Stage-3 gating invariant.
This makes the LLVM backend the AAPCS64-correct reference native must match. NOTE:
the func-value / closure / iface / VM SHIM *marshalling* is not reworked here (only
the signature spelling follows `aggCoerceLLTy`) — that is Stage 2, and the Stage-1
verification deliberately covers direct calls only.

*Stage 1 adversarial review (two independent reviewers, both SOUND) carry-forwards:*
- **[Stage 2] `IsAggregateReturn` / `AggregateReturnSize` are still size-based**, so
  when the gate flips they'd say "retbuf" for a >16B HFA that codegen now returns
  by-value in v0..v2. Make them HFA-aware (the "shared HFA-return predicate run
  BEFORE the size>16 sret decision" already listed in Stage 2) so the VM cross-mode
  dispatch / pkg-descriptor / reflect agree with the register return.
- **[Stage 3, before flip] native-vs-codegen HFA classifier agreement is by-
  happenstance, not by-construction.** Native's arg path gates on bare
  `cc.HfaAggregates && HfaMemberCount(t)>0`; codegen gates on `hfaSimdAggregate` =
  `HfaInSimd() && AggInRegCoercedKind(t) && HfaMemberCount>0` (the extra
  named-struct/array guard excludes the anonymous multi-return tuple + named float
  scalar, which are unreachable as native ARGS but not excluded by construction).
  Before flipping, route native through the SAME shared predicate (or add a cross-
  backend test) so both halves classify identically. (Codegen's own `aggCoerceLLTy`
  spelling was already tightened to `hfaSimdAggregate`'s exact set, so within the
  LLVM backend the spelling and the byval/sret exemption agree by construction.)
- **[Stage 3] flip-on gets its first automated coverage then.** Stage 1 lands with
  the flip-on path verified only by a manual temporary flip (reverted) + the
  `TestHfaDormantWhileGateOff` off-state tripwire; once `HfaInSimd()` becomes
  `GetTarget().Arch == ARCH_AA64`, unit tests can `SetArch(ARCH_AA64)` and assert
  the `[M x double]`/`[M x float]` emission + exemption/coerce consistency directly.

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

### Stage 2 groundwork (from the 2026-07-03 native code survey) — the exact wiring

The survey (native return path + full dispatch-shim inventory) fixes the insertion
points. **Chunk it into 2a (direct-call returns) and 2b (shims + VM/descriptor),
each landable dormant** — 2a is self-contained and verifiable alone.

**The one genuinely-new primitive:** there is NO FP load/store on aa64 in this
codebase (floats round-trip through GP: LDR/STR-X + FMOV — see `asm/aarch64/
aarch64_fp.bn`). An HFA member LOAD (mem→D) already has a helper
(`emitFloatLoadToFpAA64`, `aarch64_closure_shim_float.bn:143`: LDR→X16→FMOV, f32
via `Fmov_w_to_s` / f64 via `Fmov_gp_to_fp`). An HFA member STORE (D→mem, needed
for return packing) has NO helper — write one: per member `Fmov_fp_to_gp`(Xtmp,
D0+m) then `Str`(Xtmp,[base+m*w]) (f32: `Fmov_s_to_w` + 32-bit Str). Put both in a
shared aa64 file so return/collect/shims/iface reuse them. Physical regs: `D0..D31`
are a disjoint namespace at `+32` from `X0..X30` (`asm/aarch64.bni`); HFA members
ride D0..D3.

**Chunk 2a — direct-call HFA returns (native emitReturn + caller collect):**
- `common_callconv_return.bn`: `FuncReturnsBigAggregate` (:14) and
  `CallReturnsBigAggregate` (:25) must EXEMPT an HFA (add `!(cc.HfaAggregates &&
  types.HfaMemberCount(t)>0)` to the `SizeOf>InternalSretBytes` test) so a 24/32B
  HFA is NOT sret'd. Add a shared `cc.ReturnsHfaInRegs(t)` predicate for the emit
  sites to branch on (mirrors the arg path's `cc.HfaAggregates && HfaMemberCount>0`).
- `aarch64_return.bn:emitReturn` (:22-48, the single-aggregate branch): a new HFA
  branch BEFORE the `IsAggregateTyp` GP-pack — load member m from `[ptr+m*w]` into
  `D0+m` (reuse the mem→D helper), no sret. (The ≤16B path currently packs GP
  X0/X1, so even a 16B HFA needs this.)
- `aarch64_call.bn` collect (:265-281): a new HFA branch in the single-aggregate
  collect — members from `D0..D(n-1)` into the data region (mem←D via
  `Fmov_fp_to_gp` + Str), gated on the same predicate; `bigRet` is already false
  for HFAs via the exemption above, so it takes the register-collect path.
Verify (flip on): a native-main program calling (i) a native fn and (ii) an
LLVM-dep fn each returning D2/D3/D4/F2 HFAs reads them back correctly; matches
Stage-1 LLVM. Run `builder-comp_native_aa64-comp_native_aa64` + `builder-comp`.

**Chunk 2b — dispatch shims (recon-revised 2026-07-03):**
- `types.IsAggregateReturn` / `AggregateReturnSize`: **NO CHANGE** (the survey's
  "make HFA-aware / return 0" was WRONG). The VM cross-mode dispatch
  (`vm_exec_funcref.bn:345`) uses `retbufSize` (= `AggregateReturnSize`) to pick
  the aggregate shim (retbuf) vs the scalar shim (one int in X0). An HFA has 2-4
  members, so it MUST keep the retbuf-dispatch path (`IsAggregateReturn` true);
  making it 0 would route the HFA to the SCALAR shim and drop members. The fix is
  entirely in the shim RETURN-PACK: the shim calls the underlying (HFA in D0..D3)
  and must `FMOV` the members into the retbuf instead of `Str`-ing from X0..
- Codegen shims (`emit_funcvals_shim.bn` etc.): **likely already correct** — they
  load/spell aggregate args + returns via `aggParamCoerced`/`aggRetCoerced`/
  `aggCoerceLLTy`, which Stage 1 already made spell `[N x double]` for HFAs, so the
  shim body reinterprets the by-pointer bytes as the SIMD type automatically.
  VERIFY the return-pack under the flip; only the NATIVE shims (which emit asm
  directly, not via `aggCoerceLLTy`) need explicit HFA branches.
- Native shim HFA branches reuse the chunk-2a helpers `hfaMemberLoadToFp` (arg,
  mem→D) / `hfaMemberStoreFromFp` (return, D→retbuf) in `aarch64_hfa.bn`.
- The shim ROUTER `aarch64_closure_shim.bn:emitClosureShim` needs NO structural
  change IF `closureHasFloatParts` (`aarch64_closure_shim_float.bn:23`) is taught to
  detect HFA captures/params/returns (add `HfaMemberCount>0` beside each
  `IsFloatScalarTyp` at :26/:32/:34) — HFA closures then auto-route to the
  float-aware shims.
- HFA-arg branches (member FMOVs, reuse `emitFloatLoadToFpAA64`): `emitShimArgMarshalAA64`
  (`aarch64_funcvalue_shim.bn`, before the `AggCoercedInReg` at :128),
  `marshalFloatShimArgAA64` (`aarch64_closure_shim_float.bn`, after :228),
  `emitCallIfaceMethod` (`aarch64_iface.bn`, after the float-scalar arg at :84),
  `emitSpillMarshalAA64` (`aarch64_funcvalue_spill.bn`, after :198), each honoring
  FP-overflow-to-stack.
- HFA-return branches (member FMOVs D→retbuf, reuse the new mem←D helper; and
  exempt the local `retSz>16` sret floors): funcvalue-shim pack (`:300-305`, sret
  floor `:247`), closure-float-aggregate pack (`aarch64_closure_shim_float.bn:350-360`,
  sret `:326`), iface collect (`aarch64_iface.bn:203-209`), funcvalue-spill pack
  (`aarch64_funcvalue_spill.bn:117-127`). The non-float closure-aggregate shim
  (`aarch64_closure_shim_aggregate.bn`) needs no HFA branch once the router diverts
  HFAs to the float-aware shim, but its two pack loops (:101, :320) are the safety net.
- **Budget note:** shim GP-word budgeting (`userBudget`, staging) counts HFA args as
  GP words today; once they FP-pass they must not count against the GP budget — audit
  the spill-routing thresholds when adding the arg branches.
- **Classifier agreement (Stage-1 carry-forward):** native gates HFA on bare
  `cc.HfaAggregates && HfaMemberCount>0`; codegen on `hfaSimdAggregate` (+
  `AggInRegCoercedKind`). Unify (route native through a shared predicate) before the
  Stage-3 flip so both halves classify the anonymous-tuple / named-scalar edge cases
  identically.
Verify (flip on): HFA through func-value (clone 340), closure, interface (clone 358),
and a VM mode — native == LLVM, single-program AND cross-module.

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
