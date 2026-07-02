# Plan: native HFA (struct-of-floats → SIMD) ABI conformance

**Status:** investigation complete (2026-07-02, workflow `wf_8b37b44d-363`); implementation staged, in progress on branch `temp-4`.

## Why (accurate framing)

The native backends (aa64 AAPCS64, x64 SysV) pass a Homogeneous Floating-point
Aggregate (a struct/array of floats) as a **GP aggregate**, not in SIMD registers.
This is **self-consistent within native** (verified: 2×f64, 3×f64/24B, 4×f32,
float-struct-return iface dispatch all PASS on native aa64+x64) — so it is NOT a
native-dispatch miscompile. It is an **ABI-nonconformance**: it mismatches the
standard ABI (C/clang, LLVM) at a cross-ABI boundary (C-extern-by-value-HFA,
mixed-backend, VM→native cross-mode HFA-struct). User-requested conformance fix.

## Root cause / hooks (REPORT 1)

No HFA concept exists in the tree. Every FP-register path keys on the scalar
predicate `common.IsFloatScalarTyp` (`common.bn:368`, TYP_FLOAT only). A
struct-of-floats is `IsAggregateTyp` (`common.bn:350`) → GP/by-ref path.

**Shared classifier (the primary hook):**
- `common_callconv.bn:222 argRegWordsStackWords(t, ngrn, nsrn) → (regStart, regWords, stackWords)`.
  Scalar-float branch at 227-230. Add an HFA branch ABOVE `var agg`: while
  `nsrn + N ≤ NumFpArgRegs` return `-1,0,0` (rides FP, no GP/stack); on overflow
  return `-1,0,N*memberWords` (**all-or-nothing**, never split, regardless of
  `SplitAggregates`).
- `advanceNgrn` (`common_callconv.bn:290`): exclude an in-FP HFA from GP saturation
  (like the scalar-float exclusion at :292).
- The 3 NSRN walkers must advance `nsrn` by **N** (not 1) for an HFA:
  `CallArgRegStart` (:323, incr :330), `CallArgStackOff` (:411, incr :426),
  `CallStackBytes` (:437, incr :448). Variadic V-variant `argRegWordsStackWordsV`
  (:310) forces variadic floats to stack — HFA variadic likely same.
- CallConv fields: `NumFpArgRegs` (8/8), `NumFpRetRegs` (8/2), `NumX87RetRegs`
  (0/2) at `common.bni:25`, constructors `common_callconv.bn:13/40/53`.

**Return (REPORT 1 §1d):** single named-struct return classified only by
`SizeOf()>16` (`FuncReturnsBigAggregate` :14 / `CallReturnsBigAggregate` :25). An
HFA return (≤32B) must route to FP return regs. `MultiReturnTupleNeedsSret` (:56)
already does per-field GP/FP split (`fpCount`) — the template.

**aa64 emitter:** caller placement `aarch64_call.bn:56-93` (scalar-float `Fmov_gp_to_fp(D0+nsrn,src); nsrn++` — extend to N members into `D0+nsrn+m`, `nsrn+=N`); callee prologue `aarch64_emit_func.bn:83-113`; return `aarch64_return.bn:110-118` (scalar) + multi-return per-field pack :126-160. Regs: `argReg(i)`→X0..X7, FP = `aarch64.D0+nsrn`; moves `Fmov_gp_to_fp`/`Fmov_fp_to_gp`.

**x64 emitter:** analogous in `pkg/binate/native/x64/*` (report truncated — re-derive from x64_call.bn / x64_emit_func.bn / x64_return.bn; scalar-float → XMM path is the template).

## Rules / decision table (REPORT 2) — per-target, NOT shared

aa64 HFA = 1–4 members ALL same float type (fold nested struct/array flat);
>16B still HFA (up to 4×f64). x64 = eightbyte classification: ≤16B all-SSE
eightbytes → XMM; >16B → MEM; mixed-width `{f32,f64}` → SIMD2 on x64 (x64 does
NOT require same width); mixed int+float → split GP+XMM.

| struct shape | size | aa64 | x64 |
|---|---|---|---|
| {f32,f32}/f32[2] | 8B | SIMD2 | SIMD1 (`<2×f32>`) |
| f32[3] | 12B | SIMD3 | SIMD2 |
| f32[4] | 16B | SIMD4 | SIMD2 |
| {f64,f64}/f64[2] | 16B | SIMD2 | SIMD2 |
| f64[3] | 24B | SIMD3 | **MEM** ← divergence |
| f64[4] | 32B | SIMD4 | **MEM** ← divergence |
| >4 f32 / f64[5+] | >16B | non-HFA (GP/by-ref) | MEM |
| {f32,f64} mixed-width | 16B | non-HFA (GP) | **SIMD2** (x64 only) |
| {f64,i64} | 16B | non-HFA (GP2) | SIMD1+GP1 (split) |
| nested {{f32,f32},f32} | 12B | SIMD3 (folded) | SIMD2 |
| overflow (members > free vN) | — | whole→MEM, sticky-close SIMD | whole→MEM (not sticky) |

Returns: aa64 HFA→v0..v[n-1]; x64 ≤16B all-SSE→XMM0/1, mixed→split, >16B→sret.

So the HFA predicate + placement is **per-CallConv/target** — gate a new
`cc.hfaMemberCount(t) → (n, memberTyp)` on the target (aa64 rule vs x64 rule),
returning 0 when not-HFA. Staging keeps x64's returning 0 until x64 is done, so a
partial aa64-only state never breaks x64.

## Verification (REPORT 3) — PROVEN, decisive

Pure-Binate tests can't detect this (self-consistent). `__c_call` can't pass structs
by value (`isCCompatibleArgType` rejects aggregates). The working mechanism: a
**clang C driver calls a native-compiled Binate HFA callee via its mangled symbol**.
Proven end-to-end: current compiler returns **0** (wrong) for `Hfa2(D2{3,7})→v.x*10+v.y`
on aa64 AND x64; scalar control + clang↔clang reference return **37** (correct). The
test FAILS pre-fix (0), PASSES post-fix (37).

Home: a `TestHfaCalleeFromC` in each backend's `*_test.bn`
(`pkg/binate/native/aarch64/aarch64_test.bn` + `x64/x64_test.bn`), which already
`clang`-link-and-run via `bootstrap.Exec` + `EmitObject` + `canLinkAndRun()`/
`findRuntimePath()`. Build IR module `main` with `Hfa2(v {f64,f64})→f64` = `v.x*10+v.y`,
`EmitObject`, write a C driver declaring `extern double bn_F1_4_main1_4_Hfa2(struct D2)`,
clang-link, assert stdout `37`. (Also cover an HFA *return* + a 3×f64 aa64 case + a
4×f32 case; x64 divergence: 3×f64 → MEM, verify it stays by-ref.)

## Staging (each stage: implement → the C-driver test fails-then-passes → commit on temp-4)

1. **aa64 HFA args** — `hfaMemberCountAa64` helper + classifier branch (gated aa64) +
   3 walkers advance-by-N + `aarch64_call.bn` caller N-reg placement +
   `aarch64_emit_func.bn` callee N-reg read. Verify: `TestHfaCalleeFromC` (aa64) 37.
2. **aa64 HFA return** — `FuncReturnsBigAggregate`/collect route HFA→v0..v[n-1] +
   `aarch64_return.bn` pack + caller collect. Verify: C driver reads an HFA return.
3. **x64 HFA args** — eightbyte classifier (all-SSE ≤16B → XMM; mixed split) +
   `x64_call.bn` + `x64_emit_func.bn`. Verify: TestHfaCalleeFromC (x64) 37 + 3×f64→MEM.
4. **x64 HFA return** — XMM0/1 pack + collect.
5. Wire the `TestHfaCalleeFromC` tests into the native unit suites; update
   claude-todo.md HFA item → done.

**Anti-hazard:** getting the classifier and the emitter to DISAGREE (one FP, one GP)
is a miscompile. Every stage must keep classifier + all 3 walkers + caller + callee
in lockstep, and the C-driver test (which crosses the ABI boundary) is the gate.
