# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

**BUG BASH 2026-06-27.** Open *bugs* still carry an inline `🏷[BUG-BASH 2026-06-27 → LANE N]`
tag routing them to a parallel-worker lane (1 = front-end `pkg/binate/{checker,types,parser}`;
2 = IR-gen & native codegen `pkg/binate/{ir,codegen,native/*}`; 3 = VM & cross-mode runtime).

---

## CRITICAL

### box() of a struct with managed fields did not retain them — use-after-free — ✅ FIXED & LANDED (`16471d71`, 2026-07-02)

**Severity: CRITICAL (silent use-after-free / memory corruption).**

**RESOLUTION (`16471d71`).** Fix + `conformance/965` landed on main. Verified:
conf 965 fails (crashes) with the fix disabled and passes under builder-comp /
-int / -comp; full builder-comp conformance 2604/0; full builder-comp unit suite
51/0; hygiene 15/15. Unblocks bnfmt step 5's token-equality tests (`0093ff8b`).

**Root cause.** `box(v)` in IR-gen (`pkg/binate/ir/gen_expr.bn`, the `token.BOX`
arm of `genBuiltin`) bit-copied `v` into the new managed cell via `EmitBox` but
never RefInc'd `v`'s managed fields. When `v` is a struct/array with managed
fields (managed slice / ptr / func / iface), the box shared the source's single
reference; the source local/temp's scope-exit RefDec then freed the shared
backing while the box still pointed at it → **use-after-free**. A copy read
*immediately* (no intervening allocation) is byte-correct — the copy itself is
fine; the missing operation is the per-field **retain (RefInc)** on copy. It hid
because the codebase almost always builds structs in place via `make(T)` + field
assignment (each field write RefIncs) and accesses them through managed pointers
(`@Decl`), rarely `box`-ing a value-struct-with-managed-fields.

`box` was the **only** copy site missing this — every other site (var-init,
assignment, slice-element store, call arg, method receiver, return, for-in bind,
composite element) already routes through `emitStructCopy` / the
`emitStoreManagedSlot` acquire discipline. (An earlier note here also fingered
`cells[i] = t` — that was **collateral corruption** from a box in the same test
process; with the box fix in place `cells[i] = t` + churn reads back correctly,
so that path was never broken.)

**Fix (applied).** In the `BOX` arm, after `EmitBox`, call
`emitStructCopy(ctx.Func, b, result, innerTyp)` when `needsStructCopy(innerTyp)`
— RefInc the boxed copy's managed fields so the box owns its own references,
balanced by the managed-ptr dtor (which runs the pointee's struct dtor and
RefDec's them when the box is freed). Managed-*scalar* box inner types are left
unchanged: the box's managed-ptr dtor does not RefDec a non-struct pointee, so
retaining a `box(@[]T)` inner would convert the UAF to a *leak* — a separate,
pre-existing gap (the box dtor doesn't clean up a managed-scalar pointee) not
exercised by any current code; note it if `box` of a bare managed scalar ever
lands.

**How discovered.** Building bnfmt step 5. The token-equality harness
(`pkg/binate/format` `normTokens`) does
`slices.Append[@token.Token](raw, box(t))` where `token.Token` has a managed
`Lit @[]char`. On multi-statement inputs (more churn between the `box` and the
compare) `assertTokenEq(src, src)` — the same string against itself — failed or
crashed. The step-4 *type* tests passed only by UAF luck (few tokens, tight read
→ freed backing not yet reused).

**Regression test.** `conformance/965_box_managed_field_retain` — boxes a struct
with a managed `@[]char` field (single + a slice of many), churns ~200
allocations, reads the fields back. Passes with the fix; without it prints the
first line then crashes.

### float32 mixed-type expression miscompile — IR-gen mis-types float ops — ✅ FIXED & LANDED (`fef6cd35`, 2026-07-02)

**Severity: CRITICAL (silent miscompile in native + malformed IR in LLVM).**

**RESOLUTION (`fef6cd35`).** Root cause was NOT the checker (it types these correctly)
— it was purely IR-gen: `pkg/binate/ir/gen_binary.bn`'s `widenType` (which derives a
binop's result type from its operand types) had no float case, so a float pair fell
through to the integer-width logic and returned `TypInt()` (e.g. `widenType(untyped-
float, float32)`), and codegen keys `fadd`-vs-`add` off the node's result type → an
INTEGER add with the float operand coerced via `fptosi`. Fix: a float branch in
`widenType` — a concrete typed float wins (plain float32/float64 OR a **named** float
type, via `IsFloat()` which peels named), an untyped literal peer coerces to it, two
untyped floats default to untyped-float. Also fixed the adjacent (pre-existing)
named-float32 double-promotion caught by adversarial review (`type Temp float32`
op untyped-float computed in double). `conformance/962` flipped from `.xfail.all` to a
passing regression test (chain, struct-fields, mixed ops, named-float32 precision).
Verified native==LLVM; ir (587) + codegen (246) unit tests + float conformance green;
integer arithmetic untouched. Adversarially reviewed (`wf_025b40b1-bde`): no regression,
`widenType` confirmed the sole float-lowering site. Detailed original diagnosis below.

**Symptom.** A float32 expression that mixes a multiply-by-untyped-float-constant
with a trailing bare-float32 addend produces WRONG results (native: garbage; LLVM:
clang rejects the emitted IR outright). E.g. with `a,b,c,d` all `float32`,
`a*1000.0 + b*100.0 + c*10.0 + d` returns garbage (~4.65e18 / `1`) instead of 1234.
`(a*1000.0 + b*100.0) + (c*10.0 + d)` returns 1230 — the bare `+ d` is silently
DROPPED. Breaking the expression into intermediate `var`s, or using all-add (no mul)
float32 chains, or float64 throughout, all work — so it's specific to float32
expressions that mix a `float32 * <untyped float const>` product with a bare float32
operand.

**Root cause (IR-gen type resolution, `pkg/binate/ir` / the checker).** Two coupled
defects visible in the emitted LLVM IR for `chain(a,b,c,d float32) float32 { return
a*1000.0 + b*100.0 + c*10.0 + d }`:
  1. The untyped float constant `1000.0` is typed **float64**, not coerced to the
     float32 context: `%c = fadd double 0.0, 1000.0` + `%ae = fpext float %a to
     double` + `fmul double` — so `float32 * const` is computed in double.
  2. The resulting `double + float32` binary op is resolved to an **INTEGER** add:
     `%di = fptosi float %d to i64` then `%r = add i64 %sum_double_bits, %di`, and
     the `float`-returning function does `ret i64 %r`. Native lowers this literally
     (`fcvtzs x, d` + integer `add`); LLVM's verifier/clang rejects the malformed IR.

The float32 addend is float→int truncated and added to the running sum's RAW BIT
PATTERN. So the binary-op typing picks the wrong result type (int) for a mixed
float32/float64 operand pair, and untyped-float-constant coercion ignores a float32
sibling operand.

**Discovery.** Surfaced while verifying float32 HFA passing for the native HFA ABI
work (`plan-native-hfa-abi.md`) — HFA *passing* is fine (field reads correct); the
value check failed only because the float32 *arithmetic* used to verify it is itself
miscompiled. Reproduces with plain float32 locals, zero HFA involvement.

**Repro / test.** `conformance/962_float32_expr_typing` (`.xfail.all` — fails every
backend). `.expected` holds the correct `1234`, so it flips to passing once fixed.

**Proposed fix.** In IR-gen/checker float binary-op typing: (a) coerce an untyped
float constant to a float32 sibling operand (don't default it to float64); (b) for a
genuinely mixed float32/float64 op, resolve the result to the wider FLOAT type (fpext
the narrower) and never fall into the integer-add path; (c) audit the binary-op
result-type selection so a float operand pair can never yield an int-typed add /
`fptosi` coercion. Needs a front-end/IR investigation to find where the result type is
chosen.

### HFA-in-SIMD is a CROSS-BACKEND contract — native-only enablement miscompiles — 🟢 REPLANNED + IN PROGRESS (Stages 0-2 landed/implemented dormant, 2026-07-03)

**STATUS (2026-07-03).** The replan (`explorations/plan-hfa-crossbackend.md`) is
executing; all work lands DORMANT behind the single gate `types.HfaInSimd()`
(returns false), flipped ON only at Stage 3.
- **Stage 0 landed** (`06f9a8ff` classifier lift to `pkg/types`, `d69eded8`
  variadic NSRN walkers — item 2 above fixed).
- **Stage 1 landed** (`7692508e` TargetInfo.Arch + gate, `9ebf4119` LLVM codegen
  emits `[N x float]`/`[N x double]` — item 1's LLVM half fixed; adversarially
  reviewed SOUND).
- **Stage 2a landed** (`4bc6fa7c` native aa64 HFA returns in D0..D3 +
  `ReturnsHfaInRegs`).
- **Stage 2b implemented** (worktree `cd0d27c6`, pending land): native dispatch
  shims — func-value / closure / interface (item 3 fixed). Adversarially reviewed;
  a func-value FP-register-budget defect (multi-f32 HFA overflowing v0..v7) was
  caught and fixed pre-land.
- Verified flip-on across all dispatch kinds + CROSS-MODULE (native main → LLVM
  dep) — the coverage the original effort lacked.

**REMAINING before the Stage-3 flip:**
1. **Func-value stack-spill shim HFA marshalling** (`aarch64_funcvalue_spill.bn`).
   Currently a WIDE-arg / FP-overflowing HFA func-value fails LOUD (SetError) rather
   than miscompiling — safe but incomplete. Must be implemented before flipping.
2. **Stage 3 flip + comprehensive tests** — incl. automated tests for the
   multi-HFA-arg FP-overflow (fails loud) and FP-fitting (compiles) routing cases,
   which are only exercisable once `HfaInSimd()` is arch-gated.

Original problem writeup (what the replan addresses) follows.

### (original) HFA-in-SIMD native-only enablement miscompiles — 🟠 MITIGATED (gated off `1a790663`), replan OPEN (2026-07-02)

**Severity: CRITICAL wrong-code / SIGSEGV when enabled** (mitigated by gating off).
`332b4298` enabled Homogeneous Floating-point Aggregate passing in SIMD registers on
the **native aa64 arg path only**. That path is AAPCS64-correct (a clang caller into a
native `Hfa2(D2)` callee returns 37), BUT an adversarial review (all reproduced
native-vs-LLVM on this host) showed enabling it native-only produces reachable
wrong-code because HFA passing is an **ABI contract shared by every backend + the
dispatch shims**, and only native args implemented it:

  1. **Cross-module (critical).** bnc's LLVM backend GP-coerces a float struct to
     `[N x i64]` (x0/x1), not SIMD — `define double @fnS([2 x i64])`
     (`pkg/binate/codegen/emit_agg_coerce.bn`). Under `-backend native` ONLY the main
     module is native; every dependency package goes through LLVM. So a native-main HFA
     call into an LLVM-dep passes SIMD where the callee reads GP → wrong data (≤16B) /
     SIGSEGV (>16B indirect). Repro: native-main `dep.Sum(D2{5,6})` → 0, LLVM → 56.
  2. **Arg-after-HFA (critical).** The variadic-family NSRN walkers
     (`common_callconv_variadic.bn` lines ~38/64/86) inline `if IsFloatScalarTyp{nsrn++}`
     and never count HFA members, so a fixed FP arg AFTER an HFA is dropped. Repro:
     `f5(5 scalars, D3 HFA, 42.0)` → native 7, LLVM 42. Fix: use `cc.advanceNsrn(...)`.
  3. **Dispatch shims (critical).** The aa64 func-value / closure / interface-method
     shims GP-marshal args (`aarch64_funcvalue_shim.bn` / `aarch64_closure_shim_*`), so
     an HFA reaching a shim is mismarshalled.

**Mitigation (landed pending):** `1a790663` sets `cc.HfaAggregates = false` — restores
native==LLVM GP behavior (cross-module Sum 56 on both backends, f5 42, 963/964 still
pass). The classifier + emitters are kept in-tree, dormant.

**To actually ship HFA (replan — the old native-first staging is wrong):** classify
HFAs identically in (a) `pkg/binate/codegen` so the LLVM backend emits real HFA/SIMD
param types (`[N x float]`/`{double,double}` or a form LLVM lowers to v-regs) instead
of `[N x i64]`; (b) the aa64 dispatch shims; (c) the variadic NSRN walkers; (d) native
args (done) + returns. Lift `HfaClassify` to a shared location both backends consume.
Flip `HfaAggregates` on only when all four agree. **Required coverage that would have
caught this**: a CROSS-MODULE HFA conformance test (native-main importing an HFA-taking
dep — mirror `337_cross_pkg_struct_arg`'s layout) and an HFA-through-func-value test,
run in native aa64 mode vs LLVM. Single-program HFA tests (963/964) are self-consistent
by construction and CANNOT catch this class. **Process lesson**: "native matches clang
(AAPCS64)" is NOT the correctness bar inside the toolchain — "native matches the Binate
LLVM backend + shims" is, because deps + dispatch always route through them.

### native-aa64 self-hosted conformance: intermittent timeout flakiness — 🟡 OPEN (2026-07-02)

**Severity: minor (CI flake, not a miscompile).** The
`builder-comp_native_aa64-comp_native_aa64` conformance mode intermittently reports
1–2 spurious failures per full 2606-test run: a *correct* compiled test binary that
occasionally hits the runner's `timeout 3` (`conformance/runners/…native_aa64….sh`)
and yields empty output. **Non-deterministic** — different tests fail run-to-run and
none reproduce in isolation. Observed independently on two full runs:
`iota-repeat` + `shr/16/signed` on one tree, `311_err_index_assign_oob` on another
(baseline) — so it is **pre-existing**, not tied to any one change (discovered while
regression-checking the HFA stage-1 landing). The compiled code is byte-identical
across compiles (only Mach-O metadata differs), so this is a timeout-under-load / rare
runtime-slowness issue, not a codegen defect. Possible fixes to investigate: raise the
per-test `timeout` (3s is tight when the full sweep saturates the host), or make the
runner retry a timed-out test once before reporting failure. Until then a red
native-aa64 run with a lone `[3s]` timeout failure is very likely this, not a real
regression — re-run the single test in isolation to confirm.

### Slicing a string literal (`"abc"[:]`) emitted invalid LLVM — ✅ FIXED & LANDED (main `77ae3c54`, 2026-07-03)

**Severity: MAJOR (a valid language construct did not compile — hard LLVM
verifier error, in every mode).** `genSliceExpr` (`pkg/binate/ir/gen_access.bn`,
the array-to-slice arm) only materialized a `{data, len}` slice when the sliced
collection's type was `TYP_ARRAY`. A **string literal** `"abc"` has type
`[N]readonly char` (an array), but its `genExpr` yields a bare
`OP_CONST_STRING` `*readonly char` pointer (not `TYP_ARRAY`) — so the conversion
was skipped and the subsequent `EmitSliceLen` emitted `extractvalue i8* <ptr>, 1`,
which fails the LLVM verifier. **Triggered by** any `"lit"[:]` — e.g. the plain
non-variadic `cc("abc"[:])` (no variadics/spread involved).

- **Fix:** `genSliceExpr` now materializes an `OP_CONST_STRING` collection into a
  `*[]readonly char` rodata slice (`EmitStringToChars`) before the slice logic;
  the result type matches the checker (`checkSliceExpr` → `*[]readonly char`).
- **Test:** `conformance/regressions/slice-string-literal` (now passing — full
  slice / bounds / empty-string / index / var-bind); the plan's `stringLit[:]...`
  spread positive was added to `conformance/spec/10-functions/178`. Green
  comp/int/comp-comp; adversarially reviewed (no defects).
- **Discovered:** 2026-07-03, writing the variadics Phase 4 spread tests (the
  plan's `stringLit[:]...` positive spread case — no existing test ever sliced a
  string literal, so the path was untested).

---

## Native arm32 backend (AAPCS32 / ILP32) build-out

### Small-aggregate coercion was `[N x i64]` on ILP32 — native↔LLVM ABI mismatch — ✅ FIXED & LANDED (`5b65e369`, 2026-07-03)

**Landed** as `5b65e369`: native-arm32-baremetal conformance 1754 → **1771** (+17;
`conformance/967` + 16 pre-existing odd-register-aggregate tests the old `[N x
i64]` was corrupting). LP64 byte-identical (verified: empty `--emit-llvm` diff,
codegen/types/native-x64/aa64 unit tests green); adversarially reviewed (a
would-be-critical `[2]int64`-array alloca-under-alignment concern was checked
against clang and refuted — LLVM uses the pointer's provable align-4, lowering to
word-granular `ldm`, never `ldrd`). **Follow-up (docs-only):** a repo-wide
`[N x i64]` → `[N x iW]` comment sweep (~61 stale references across codegen) — the
landed commit fixed only the justifications adjacent to the code.

**Severity: MAJOR (silent argument corruption at the native↔LLVM boundary on
arm32).** `pkg/binate/codegen/emit_agg_coerce.bn` coerced a `<=16-byte`
by-value aggregate at the LLVM boundary to `[N x i64]` (`aggCoerceLLTy` /
`aggCoerceWords` = ceil(SizeOf/8)), hardcoded and target-independent. On arm32
clang coerces such structs to `[N x i32]` (N = ceil(SizeOf/4), 4-aligned) — an
`i64` element is 8-aligned, so `[N x i64]` triggers LLVM's AAPCS §6.5 C.3
even-register bump (skips an odd GP register) whereas `[N x i32]` does not. The
native arm32 backend already packs 4-aligned words (matching clang's
`[N x i32]`), so the LLVM-side `[N x i64]` was the defect: a native caller
passing a naturally-4-aligned struct after a leading scalar places it at r1:r2,
but the LLVM callee with `[N x i64]` reads r2:r3.

- **Fix.** `aggCoerceLLTy` / `aggCoerceWords` are now target-aware via
  `aggCoerceElemBytes()` (gated on `types.GetTarget().PointerSize == 4`, same
  predicate as `types.NeedsSret`): `[N x i32]` (ceil(SizeOf/4)) on ILP32,
  `[N x i64]` (ceil(SizeOf/8)) on LP64. The LP64 path is byte-identical to
  before (verified: full `--emit-llvm` diff on the host is empty). The
  `AggregateReturnSize` 8-byte rounding is a safe over-allocation on ILP32
  (always ≥ SizeOf); the retbuf/by-address slots are `aggCoerceLLTy`-typed on
  both halves so they stay target-consistent. Native callconv untouched (it was
  already correct). Comments in `common_callconv_ctors.bn` (the old "KNOWN GAP
  P3") and the `emit_agg_coerce.bn` header updated.
- **Test.** `conformance/967_aggregate_abi_odd_reg` (cross-package: LLVM dep
  `Odd(scalar int32, s P2)` / `OddB5(scalar, B5)` with a naturally-4-aligned
  struct starting on r1; native `main` calls it). Fail-before/pass-after
  demonstrated on `builder-comp_native_arm32_baremetal` (without fix:
  `309000`/`644042` corruption; with fix: correct). Passes on host / LLVM-arm32
  (self-consistent).

### Cross-package call to an LLVM-compiled `int64`-returning function wedges native-arm32 — 🟠 OPEN (needs investigation)

**Severity: MAJOR (hang / no output).** On
`builder-comp_native_arm32_baremetal`, a native `main` calling an
LLVM-compiled dependency function whose result is `int64` (or which does int64
multiply) hangs QEMU with no output — even with **only scalar args** (no
aggregates). Discovered 2026-07-03 while writing the small-aggregate ABI
fixture above: the first int64-returning design of `967` hung, and a minimal
repro (`Mul(scalar, a, b int32) int64` returning `scalar*1e9 + a*1e6 + b`,
called cross-package) reproduced it with no structs involved; the same shape
returning `int32` works, and a native-`main`-only int64 println/multiply works.
So it is **independent of the aggregate coercion** — likely an int64 return-
register / `__aeabi_*` libgcc-helper linkage issue at the native↔LLVM boundary.
`967` sidesteps it by returning `int32`. Root cause: **unknown — needs
investigation.** No dedicated xfail added (the whole native-arm32 mode is
experimental/non-blocking with ~832 fail-loud shapes; this is one class of
them). Related: `conformance/877_aggregate_abi_xpkg` also hangs on this mode,
and its methods return int64 — plausibly the SAME int64-return defect rather
than an aggregate-ABI one.

### `Self` nested in a composite iface-method param (`*[]Self` / `...Self`) fails impl-satisfaction — 🟠 OPEN (2026-07-03)

**Severity: minor-major (a spec'd construct is rejected; obscure).** An interface
method whose parameter nests `Self` inside a composite type — `grab(rest *[]Self)`,
and hence a variadic `merge(others ...Self)` (body `*[]Self`) — is **not** satisfied
by a correct impl. `methodSigSatisfies` (`pkg/binate/types/check_impl.bn`) compares
`substituteSelf(ifaceFt.Params[i].Type, named).Identical(implFt.Params[i+1].Type)`;
`substituteSelf` (`check_self.bn`) DOES recurse into a slice element, so `*[]Self`
with `Self=*Bag` should map to `*[]*Bag` and match the impl's `*[]*Bag` — yet the
impl is rejected ("type Bag does not implement Holder (method `grab` has wrong
signature)"). **Not variadic-specific:** a plain non-variadic `*[]Self` param
reproduces it, so the gap is in how the interface method's `Self`-in-composite
param is *resolved* (likely the inner `Self` is not resolved to `TYP_SELF` when
nested in a composite, so `substituteSelf` has nothing to substitute), NOT in the
variadic work.

- **Impact:** blocks a variadic `...Self` interface / generic-constraint method.
  The variadics Phase 6c `substituteSelf`-recursion in `tryTypeParamMethodCall`
  (correct code) therefore has no end-to-end conformance test until this is fixed.
- **Repro / xfail:** `conformance/regressions/iface-self-in-composite` (`.xfail.all`).
- **Discovered:** 2026-07-03, adding Phase 6 (variadic method/interface/generic
  call) coverage.
- **Proposed fix:** ensure the interface-method param-type resolver marks a nested
  `Self` (inside `*[]Self` / `@[]Self` / `[N]Self` / `*Self` composites) as
  `TYP_SELF` so `substituteSelf` substitutes it, then drop the xfail.

---

## Language features — specified, not yet implemented

### Type assertions, type switches & RTTI — spec'd 2026-07-02, NOT implemented — 🔴 OPEN

Go-style downcasting from an interface value to a concrete type or narrower
interface, plus the `TypeInfo` RTTI substrate. **Specified** in the spec (§11.12
`iface.assert`/`iface.assert.kind`/`iface.assert.absent`/`iface.typeswitch`/`iface.rtti`;
§7.13.14 `type.layout.typeinfo` + §7.13.8 any-block `*TypeInfo`; §13.8
`expr.type-assert`; §14.10 `stmt.type-switch`; §17.5 failed-assertion panic) but
**not implemented**. High-level plan (adversarially reviewed — 3 criticals + 4
majors fixed before landing): **[plan-type-assertions.md](plan-type-assertions.md)**
(a follow-up worker expands it into ordered steps). Model: source `*I`/`@I`
(incl `*any`); target = nameable type with mandatory `*`/`@`/value recovery kind
(`@I`→`@T`/`*T`/value, `*I`→`*T`/value, `@T`-from-`*I` rejected); concrete match =
exact identity, interface match = satisfaction **incl transitive ancestors**; both
`x.(K T)` (aborts) and `v, ok := x.(K T)`; type switch (no `case nil`, unset→default,
typed-nil→its type); RTTI via a `*TypeInfo` in the vtable any-block (identity +
dtor + size + align + name + transitive satisfaction-table), one per type
program-wide, cross-mode agreement on the *result*. **Highest implementation risk:
the any-block grows to 2 words, re-basing every vtable method slot** — all backends
+ VM must apply it consistently. Open (no sum types). Seeds the future reflection
surface (§20.3).

---

## Method values & function values (codegen)

### Function values — residual follow-ups (the MAJOR PROJECT landed) — 🟡 OPEN (low priority)
Function values are done across all three phases (archived in [claude-todo-done.md](claude-todo-done.md):
Phase 1 non-capturing + type/vtable machinery, Phase 2 closures/capture — `plan-function-values-phase-2.md`
is "COMPLETE (shipped)", conformance 338–344 + 501/508–510/513…, Phase 3 cross-mode trampolines).
Residual:
- Broader cross-mode trampoline signature shapes beyond `TrampolineScalar` (floats, aggregates, >7 args) —
  add when a path actually reaches them.
- Recursive lambdas (`var f = func(x){ … f(…) … }`) — non-goal during Phase 1; revisit now that Phase 2
  capture is settled (Y-combinator is the current workaround).
- Downstream interop hand-off (package descriptor; retiring ~30 hand-written `vm_extern` arms) is tracked
  under "Compiler/interpreter interop — MAJOR PROJECT".

### 🏷[BUG-BASH 2026-06-27 → LANE 3] cross-mode coerced-agg func-value ABI — residual native-shim follow-ups
The cross-mode coerced-aggregate-ARG residuals — the iface/func-value by-address
fix, the >7-arg extern guard, and the sub-word/bool RETURN — LANDED via the by-address
ABI rework (`233cc82d`) + the >7-arg guard (`17cfc16b`); see claude-todo-done.md. An
observable native-struct-return-into-by-value-extern fixture (`dd3d8b59`) landed too.
Smaller follow-ups remain:

1. **shim-extends RETURN (cleanup, optional).** The sub-word RETURN was fixed VM-side
   (the 25117a2e VM-narrow mechanism extended to iface/func-value), since the sub-word/bool
   RETURN concern is VM-only. The review's cleaner shim-extends design (every backend's shim
   sext/zext's sub-word returns; drop the VM narrow) is deferred — a multi-backend,
   target-word-dependent change with a tail-branch→call-shape wrinkle.  Plan +
   per-backend shim sites + verification: [plan-funcvalue-shim-extend.md](plan-funcvalue-shim-extend.md).

(The x64 closure-shim soft-length split and the conditional func-value spill staging are
✅ DONE & LANDED — see claude-todo-done.md.)

See explorations/plan-funcvalue-byaddr-abi.md.

## Cross-mode interface dispatch & compiler/interpreter interop

### 🏷[BUG-BASH 2026-06-27 → LANE 3] MINOR — cross-mode interface dispatch: residual LP64/HFA/upcast gaps (2026-06-14) — 🟡 OPEN

The shim-route that dispatches a native-only package's interface methods from
bytecode (landed `93f75f27` + the math/big extension `7c3b17a2`) is exercised by
726 (`strings.Builder` via `io.Writer`: a raw-slice arg, a scalar arg, a no-arg
method; scalar + multi-return) and 577 (`errors.Error`: no-arg, multi-return).
An adversarial review found four more shapes UNTESTED — each needed a SYNTHETIC
native-only test package, since no stdlib impl hits them. ✅ NOW COVERED by
`e2e/xmiface.sh` (main `7f15b1e9`, 2026-07-01): a custom host injects a fixture
package's `__Package()` into the VM inject-set (`Interp.isCompiled` → its impls
dispatch natively) while the dispatching main runs as bytecode —

- A VALUE-receiver iface method (the iv-dispatch thunk deref; `a0` = the iv-data
  ptr the thunk derefs; 410 covered native-to-native only) — `Double()` → 42.
- A method with MULTIPLE aggregate args (the `a1/a2` by-address slots) —
  `Combine(Pair,Pair)` → 110.
- A FLOAT arg (the shim's int-slot → FP bitcast path) — `Scale(2.5)` → 20.
- The `n>6` user-arg overflow guard (a negative test) — the loud vmPanic, which,
  being specific to the cross-mode path, also proves the fixture is genuinely
  native-injected (a bytecode-lowered fixture would print 28, not panic).

Residuals (still open):

Latent, LP64-host-only (NOT active — default VM modes run a 64-bit host):
- `dispatchCompiledIfaceMethod`'s `resultSize > 8` aggregate-vs-scalar threshold
  (and `dispatchExternBinding`'s identical one) must track `isAggregateReturn`'s
  `> target.PointerSize`; on an ILP32 VM host a 5–8-byte aggregate return would
  pick the wrong shim shape. (Now commented in `vm_exec_iface.bn`.)
- 64-bit-scalar args pack as 2 slots on a 32-bit host (`argSlots`); the dispatch
  reads them as positional shim args.

Separately (PRE-EXISTING, independent of the VM): the native backend has no HFA
classification — a struct of ≤4 same-kind floats (an AAPCS64/SysV Homogeneous
Floating-point Aggregate) is passed as a GP aggregate, because the arg classifier
(`common_call.bn:156`) only special-cases SCALAR floats (`IsFloatScalarTyp`), with
no struct-of-floats → SIMD branch; the LLVM side relies on LLVM to classify HFAs.
**NOT a reachable native-dispatch miscompile** (verified 2026-07-02: 2-double,
3-double/24B, 4×float32, and float-struct-return iface dispatch all pass on native
aa64 + x64) — native is SELF-CONSISTENT (caller + callee both use GP), so pure-native
is correct. It is a latent **ABI-NONCONFORMANCE**: native uses GP where the standard
ABI uses SIMD (v0–v7 / XMM), so a mismatch is reachable only at a cross-ABI boundary
— a C-extern with an HFA-by-value arg (rare), mixed LLVM/native modules (not a normal
build), or a VM→native cross-mode dispatch of an HFA-struct arg (the `e2e/xmiface`
coverage tested only a scalar float, not an HFA struct). **In progress** (2026-07-02,
user-requested): classify HFAs → SIMD in the native arg/return classifier on aa64 +
x64 to match AAPCS64/SysV. See `plan-native-hfa-abi.md`.
  - **Stage 1 (aa64 HFA ARGS) was landed (`332b4298`) then GATED BACK OFF
    (`1a790663`, 2026-07-02) — see the CRITICAL "HFA-in-SIMD cross-backend mismatch"
    entry at the top of this file.** The native aa64 arg path is AAPCS64-correct
    (verified against a clang caller), but enabling it native-only produced reachable
    wrong-code / SIGSEGVs: an adversarial review found the LLVM backend GP-coerces
    float structs to `[N x i64]` (so native-main↔LLVM-dep HFA calls disagree), the aa64
    dispatch shims GP-marshal, and the variadic NSRN walkers drop a fixed FP arg after
    an HFA. The classifier + emitters remain in-tree, dormant. `conformance/963` and
    `964` still pass (both backends GP again). **HFA can only flip on once the LLVM
    backend + dispatch shims + variadic walkers classify HFAs identically — it is a
    coordinated CROSS-BACKEND project, not a native-only stage.**
  - **Replan needed**: the old "stage 1 = native args, stage 2 = native return, …"
    decomposition is wrong (each piece must land in native + LLVM + shims together, and
    the flag flips on only at the end). See `plan-native-hfa-abi.md`.
  - Note: full float32 HFA *value* verification is also blocked by the separate CRITICAL
    float32 expression-typing miscompile (top of this file).

**Native-source iface UPCAST offset>0 — ✅ FIXED & LANDED (`7f832f64`,
2026-07-02).** The VM's `BC_IFACE_UPCAST` native-source branch
(`vm_exec_iface.bn`) advances the native vtable word by `offset*8`, mirroring
`emit_iface_upcast.bn`. A REAL-parent upcast (offset>0) advances the word to the
parent sub-block — INTERIOR to the base `@__ivt` — and a method call on the
result used to do `lookupShimVtable(base + offset*8)`, an exact-match MISS →
loud "no shim vtable" abort. The old "unreachable, no stdlib interface extends
another" claim was WRONG: the embeddable interp (`Interp.New` with a custom
inject-set) lets an embedder inject a native package whose `interface B : A` is
dispatched from bytecode — a valid program that aborted (surfaced by the user,
2026-07-02). Fix: carry each vtable's slot count in `reflect.VtableInfo.SlotCount`
(threaded through `ir.PkgVtableEntry` + `buildVtableInfoNode` + all four gathers —
codegen, native x64/arm32/aarch64, and the VM bytecode gather) and make
`lookupShimVtable` a bounded RANGE lookup: match the vtable whose extent
`[base, base + SlotCount*8)` contains the word, return `shim + (rawAddr − base)`;
out-of-extent → 0 (loud abort preserved). Offset 0 (`@X→@any`, `@X→*X` decay)
resolves to the shim base exactly as before. Coverage: `e2e/xmiface.sh`
(`cross-mode-iface-parent-upcast`: native-injected `Ext : Base` + a 3-level
`C1 : B1 : A1` transitive upcast, offset>1; and a VALUE-receiver parent method AT
offset>0 — case (g), `80cf34b6` — proving the iv-dispatch thunk resolves through
the range-lookup-selected shim slot) + `pkg/binate/vm` `vtable_inject`
(interior/boundary/out-of-extent) + descriptor unit tests. Adversarially reviewed
(no bugs). No known coverage gaps remain.

### Package descriptors (Phase B) — `__Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `__Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `__Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `__Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `__Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg.__Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg.__Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg.__Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `__Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg.__Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt.__Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `__Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `__Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `__Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### Embeddable-interp — open follow-ups (Inc 2 extern cleanup core landed) — 🟡 OPEN (2026-06-20)

The embeddable-interp core (Inc 1, Inc 2 Layers 1/2 + the review (b)-fix, and the
loader de-rooting) is **✅ DONE & LANDED** — full detail in
[claude-todo-done.md](claude-todo-done.md). Plan:
[`plan-embeddable-interp.md`](plan-embeddable-interp.md). Remaining open
follow-ups (deferred with user sign-off):

- **Interpreted `__c_call` guard — ✅ DONE & LANDED (`da3bd46a`, 2026-07-02),
  at the FRONTEND (not lower-time).** Interpreted code that uses `__c_call` now
  errors at type-check (`Checker.Interpreted` → `checkCCall`), and injected /
  compiled-instance packages load INTERFACE-ONLY (`Loader.InterfaceOnly`), so
  their native-only `__c_call` impls are never parsed/checked/lowered on the
  interp path (which also fixes the old `os.Seek`/`cLseek` silently-broken-
  bytecode problem — the impl isn't lowered at all). Covers the run path
  (`TypecheckAll`) and the REPL (define + import, both initial-load and
  mid-session-at-the-prompt). The earlier idea of a *lower-time* impl check was
  rejected by the user ("too late — do it at the frontend"). Coverage:
  conformance 961 + `TestCheckCCallInterpretedRejected` + e2e/repl.sh
  `tier5-mid-session-import-ccall-rejected`.
- **`--test`-path frontend guard — ✅ DONE & LANDED (`1de21404`, 2026-07-02).**
  `TypecheckPackages` now sets `Checker.Interpreted`, and `cmd/bni` runTests wires
  `Loader.InterfaceOnly = interp.NativeOnlyInterfacePaths(cli.Filenames)` (the
  native-only set — rt + bootstrap + every pkg/std package — minus any that are
  themselves `--test` targets). So the `--test` path now rejects interpreted
  `__c_call` at the frontend exactly like the run path and REPL: a `__c_call`
  package run as its own `--test` target gets a clean "cannot be interpreted"
  type error instead of `lower_instr`'s default-arm abort, and injected
  dependencies load interface-only. This ALSO closed the older "runTests /
  `IsNativeOnlyInVM` unification" follow-up — the runner's interface-only set now
  derives from the same source (`stdPkgs`) as the skip predicate. Coverage: interp
  unit tests (`NativeOnlyInterfacePaths` × 4 target-set cases +
  `TypecheckPackages`-sets-`Interpreted`); adversarially reviewed (no bugs).
- **Globals/vtables-sensitive inject-set test.** `TestNewCustomPkgsRespected`
  proxies on `len(Externs)` (function registration only); add a test that a
  custom set's globals + impl vtables are honored (the `errors.Is`
  sentinel-identity path).
- **Layer 2b — `@reflect.Package` wrapping helper.** Build a modified descriptor
  from an existing one with selected `FunctionInfo` values replaced, so an
  embedder overrides e.g. `os.Args()` without hand-constructing a descriptor.
  This is the ergonomic per-function override path; it also rehomes the
  `progArgsAfterDash` Args shim (becomes a cmd/bni-built wrapped-`os` concern
  rather than baked into interp's bootstrap registration). Land with an
  end-to-end test proving a wrapped package changes observed runtime behavior.
- Optional: auto-enumerate bootstrap's exported format helpers via
  `RegisterPackageFunctions` (they qualify — exported, non-extern), leaving only
  the 9 extern C-I/O entries hand-bound.

## VM runtime faults & the rt.Exit/abort/panic paradigm

### rt.Abort/rt.Panic Plan 2 — make user-code VM faults recoverable (host survives) — 🟡 SCOPE REQUIRED (2026-06-20)

**Related robustness gap (filed 2026-06-30):** a bad-pointer deref inside a NATIVE EXTERN
called from the VM (e.g. handing a wild pointer to `rt.Refcount`) SIGSEGVs the VM host with
NO guard — it is not one of the 6 guarded VM user-fault sites (bounds/divide/shift/nil-deref/
stack-overflow/call-through-nil), and there is no signal handler in `pkg/binate/vm` / `cmd/bni`
/ `rt`. Surfaced while resolving the "VM refcount halt" probe-artifact (see done file). If
this VM-fault-recovery work is picked up, the native-extern boundary should be considered too.

Plan doc: [`plan-rt-abort-panic.md`](plan-rt-abort-panic.md). **Plan 1 (the
`rt.Abort`/`rt.Panic` primitives, the `panic()` single-string + lowering change,
and the VM internal-abort migration through `panic()`) is DONE & LANDED** — see
claude-todo-done.md.

User-code runtime faults (bounds / divide / shift / nil-deref / stack-overflow /
call-through-nil) should be RECOVERABLE in the VM (the host REPL / test-runner /
embedder survives a bad interpreted program) while staying fatal in compiled
code. The 6 VM user-fault sites are deliberately still on `rt.Exit(1)` pending
this. Approach (per user): rt is already injected into the VM, so a faulting user
op already calls the *injected* `rt.Panic`/`rt.Abort`; inject a VM-specific
variant that unwinds the VM's DATA-stack frames (`vm.Stack`) back to `CallFunc`
instead of killing the host (no longjmp — the user call stack is data, not the
host stack). Open: the exec-loop unwind mechanism + refcount-correct frame
teardown.

Related smaller follow-up: route panic / `runtime error:` / VM diagnostics to
**stderr** (fd 2) — deferred out of Plan 1 (infra exists: `bootstrap.Write(fd)`,
`bootstrap.STDERR = 2`); a real behavior change for anything scraping them off
stdout.

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

## 32-bit-host toolchain: IR constant width & VM machine word

### 🏷[LANE 3] MAJOR: cross-mode shim mis-marshals 64-bit scalar ARGS on ILP32 (println of unsigned/int64/float segfaults) — ✅ FIXED & LANDED (`a5511a8d` codegen + `83819d60` vm, 2026-07-03)
- **Severity: MAJOR.** On the 32-bit VM host (`builder-comp_arm32_linux_int`),
  `println` of any `uint8`/`uint16`/`uint32`/`uint64`/`int64`/`float64` segfaults
  (`int`/`bool` are fine). Root cause of `conformance/133`'s crash (the slice
  indexing was a red herring — `s[0][0]` is a `char` → `formatUint(uint64)`).
- **Root cause**: a 64-bit scalar shim arg takes 2 VM slots (lo,hi), but the
  per-function `__shim` declares an `i64` param; `rt._call_shim_scalar` passes
  all args as `int`(=i32), so the reconstructed indirect-call type has no `i64`
  to even-align while AAPCS32 even-aligns the shim's `i64` → the following `buf`
  arg reads garbage → the formatter derefs a bad pointer. LP64-invisible.
- **Fix (designed + adversarially reviewed)**: `plan-vm-32bit-crossmode-64bit-args.md`.
  Slot-based shim arg ABI on ILP32 via a shared `slotTypesFor` helper across all
  six signature sites + the native caller's arg preamble.
- **Related MAJOR (separate, confirmed): 64-bit scalar RETURNS via retbuf on
  ILP32 are also broken.** A bare `int64`/`uint64`/`float64` result routes through
  `_call_shim_aggregate` (IsAggregateReturn true at SizeOf 8 > word 4), but the
  dispatch stores the retbuf ADDRESS into `regs[Dst]` (one slot,
  `vm_exec_funcref.bn:356` / `vm_extern.bn:63`) while `regWidths` flags the result
  register WIDE (2 slots, `lower_slots.bn:170`) — `regs[Dst+1]` stays stale and
  the 8 bytes are never loaded from the retbuf into the pair. Not exercised by the
  format helpers (they return `int`), so it does NOT block `133`, but any cross-
  mode func value / extern returning a bare 64-bit scalar on ILP32 is wrong. Fix
  shape: aggregate-return dispatch must load the retbuf pair for a 64-bit *scalar*
  result rather than storing the pointer. Discovered by the design-doc review.

### 🏷[BUG-BASH 2026-06-27 → LANE 3] IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
- **Symptom**: under `builder-comp_arm32_linux` unit tests, `pkg/ir`
  and everything downstream of it (`pkg/native{,/amd64,/arm64,/common}`,
  `pkg/codegen`, `pkg/vm`, `cmd/{bnc,bni,bnas}`) fail to compile for
  arm32 with int-width type errors.  `pkg/ir` is the cascade root.
- **Discovery**: triaging the 14 arm32_linux unit-test failures after
  type-check errors gained source locations (binate `c011827`,
  conformance/494).  With locations on, `pkg/ir`'s only *source* error
  is `gen_util_literals.bn:234` (`intFitsInType` compares against
  `4294967295` > INT32_MAX), and tracing the value upstream shows the
  whole literal path is `int`.
- **Root cause**: the IR stores program integer constants in
  `Instr.IntVal`, typed `int` (`pkg/ir.bni:356`) — host-width.  The
  feeding path (`exprIntLitValue`, `bignumToInt`, `parseIntLit`,
  `EmitConstInt`) is all `int` too.  On a 64-bit host this happens to
  work (it's really storing a 64-bit *bit pattern* — a `uint64`-max
  literal lands as the int64 pattern `-1` and codegen emits it fine).
  On a 32-bit host `int` is 32 bits, so the path neither compiles nor
  can represent a `uint32`/`int64` constant.  Symbol/codegen output
  must not depend on host int width.
- **Severity**: major.  Loud (compile failure) on 32-bit, not a silent
  64-bit-host miscompile — but it blocks the C-free / 32-bit-hosted
  self-hosting goal.  `int64` vs `uint64` for the field is immaterial
  (it's a stored bit pattern reinterpreted by the constant's type);
  `int64` is the minimal-churn choice since the existing range-check /
  negation code is written in signed terms whose bounds fit `int64`.

- **Layer 1 — IR + codegen + native (DONE)**: made the program
  -constant path host-independent.  Landed: binate `879ba38`
  (asm 64-bit immediates: x64 Imm→int64 + Imm64, finished aarch64
  Imm consumers in pkg/asm/parse), `035022c` (IR int64 contract),
  `294b5f0` (wide-constant tests), `075e1f5` (made the int-width
  -assuming bootstrap/vm tests 32-bit compatible).
  - `Instr.IntVal` `int` → `int64`.
  - `exprIntLitValue` / `bignumToInt` return `int64`; `intFitsInType`
    takes `int64`.  (`parseIntLit` stayed host-`int` — a
    non-type-checked fallback; the real path takes the bignum branch.)
  - `EmitConstInt(int)` kept (widens internally) + new
    `EmitConstInt64(int64)` for the literal path.
  - `buf.WriteInt64` added; codegen's OP_CONST_INT emit uses it.
  - `pkg/native/{amd64,arm64}` `emitConstInt64` → `int64`; arm64
    extracts MOVZ/MOVK chunks via int64 shifts.  Fixed a latent bug:
    arm64 `emitConstFloat` did `cast(int, bits)` on a 64-bit IEEE
    pattern (dropped the high word on a 32-bit host) → `cast(int64,…)`.
  - VM boundary: `lower_instr.bn` `bc.Imm = cast(int, instr.IntVal)`
    — lossless on a 64-bit host; the truncation-on-32-bit is what
    Layer 2 addresses.
  - **Result**: all 14 packages in the arm32_linux unit-test set
    compile for arm32 (verified locally; runtime validated by the
    `builder-comp_arm32_linux` CI job).

- **Layer 2 — VM machine word (INT64 PATH DONE)**: `pkg/vm` uses host
  `int` as its universal machine word — registers, immediates,
  pointer arithmetic (`bit_cast(int, frameBase) + instr.Imm`),
  offsets.  So a 32-bit-hosted VM is a 32-bit machine and can't carry
  64-bit immediates.  Open design question (raised by user): can the
  VM keep host-sized words for most values and use 64-bit only when
  necessary?
  - On a 32-bit host the VM interprets 32-bit-*target* bytecode, where
    pointers / `int` / sizes / offsets are all 32-bit by definition —
    so host-word is already correct for the vast majority of values.
    The 64-bit cases are exactly the explicitly-64-bit ones: `int64` /
    `uint64` values and large literals.
  - Two implementations of "64-bit only when necessary":
    (a) uniform 64-bit value slots + width-aware ops — simplest and
    correct; on a 32-bit host it costs 64-bit slot storage and 64-bit
    arithmetic only where the op is 64-bit (the compiler already
    supports `int64` on 32-bit; bytecode is largely typed already).
    (b) host-word slots + 64-bit via register pairs / a parallel wide
    slot, switched by typed opcodes — saves the 32-bit storage but
    complicates the register model and bytecode (must track which
    slots are wide).
  - Recommendation: do (a) first (correctness, minimal model change);
    treat (b)'s host-word-mostly layout as a later 32-bit perf
    refinement, not a correctness prerequisite.
  - **Investigation findings (2026-05-26)**: the change is larger and
    more entangled than the (a)/(b) framing implies — `int` is a
    *single conflated word* across three distinct roles, so it can't
    be swapped to int64 blindly:
    1. **Register slots.** `regs *int`, accessed `regs[i]`.  But
       `pushFrame` already budgets `f.NumRegs * 8` bytes/reg
       (`vm.bn:181`) — 8-byte slots.  On a 64-bit host int==8 so it's
       consistent; **on a 32-bit host this is a latent stride bug**
       (8-byte budget, 4-byte `*int` access → registers alias).  So
       `regs *int → *int64` actually *fixes* this and matches the
       existing layout.
    2. **Host pointers.** Registers also hold host addresses via
       `bit_cast(int, vm.Stack)` / `bit_cast(*uint8, regs[i])`.  With
       int64 regs on a 32-bit host these become a width mismatch
       (host ptr 32-bit, reg 64-bit) — `bit_cast` is illegal
       (size differs); they need explicit widen-on-store /
       truncate-on-read helpers (`ptrToReg` / `regToPtr`).
    3. **Target-memory-structure access.** `bit_cast(*int, hdrPtr)`
       reads managed-slice/refcount headers as `*int`.  These are
       target-word-sized fields; tying their stride to the register
       word is wrong if the two ever differ.  Needs separating
       "VM register word" from "target word".
  - Surface: ~106 `bit_cast(int,…)/(*uint8,…)/(*int,…)` sites across
    vm_exec*.bn + vm.bn, plus `BCInstr.Imm int→int64`, register
    arithmetic, and the memory ops.  This is a multi-step refactor;
    settle the register-word-vs-target-word model before editing.
  - **What landed (int64 path)** — model:
    register == host word; 64-bit values use register pairs; pair ops
    only engage when `REG_SLOT < 8` (no-op on a 64-bit host).
    Pointer-vs-target-word ambiguity stays narrow because `bit_cast`
    sites are at register-vs-pointer boundary — register word stays
    host `int`, so the ~106 `bit_cast` sites are untouched.
    - Step 1 (binate `f7cae70`): `REG_SLOT = sizeof(int)`; register
      area / frame header sized by it.
    - Step 2a (`ca7def6`, `394a16a`, `ca41a75`): `buildSlotMap` /
      `regWidths` / `remapRegisters` — id→slot mapping with the
      audited `BC_RETURN.Dst` exception.
    - Step 3 (`fd3ca06`, `f764a66`, `be877fd`, `60657fd`, `947205f`,
      `ebaa077`): full `BC_*64` handler set — `LOAD_IMM64`, `MOV64`,
      arith / bitwise / shifts / signed+unsigned compares / unary
      (NEG, BITNOT) / casts (WIDEN_S, WIDEN_U, NARROW, MOV64-bitcast)
      / pair memory `LOAD64_PAIR` / `STORE64_PAIR`.  Pure compute
      factored into evalArith64 / evalCmp64 / evalShift64 /
      evalUnary64 / widen64* — host-tested across the tricky cases.
    - Step 4 (`925e9bc`, `949ea29`, `ebaa077`): lowering emits the
      `BC_*64` ops host-word-aware — `OP_CONST_INT`, all binary
      arith / cmp / shift, load/store, casts, NEG/BITNOT.
    - Step 2b (`24a5d67` RETURN64, `7353523` direct CALL,
      `2eaa8f9` indirect/func-value/iface call ABI,
      `11da9d7` multi-return pair-aware): int64 return + call ABI
      complete.  `NumParamSlots` + slot-count `Imm` semantics.
    - Step 6 (`1fd3b9f`): conformance/499 int64 arithmetic E2E.
  - **Float64-on-32-bit (DONE)**: mirrors the int64 pair pattern.
    - `ba1a798`: route the existing `BC_FNEG` / `BC_F*` /
      `BC_SITOF` / `BC_FTOSI` / `BC_F64_TO_F32` / `BC_F32_TO_F64` /
      `OP_CONST_FLOAT` `bit_cast(int, float64)` hops through
      int64 — compile-clean on a 32-bit host without yet changing
      lowering semantics.
    - `3126655`: `BC_F*64` opcode decls (`BC_FNEG64`,
      `BC_FADD64..BC_FDIV64`, `BC_FEQ64..BC_FGE64`) + pure
      `evalFloatArith64` / `evalFloatCmp64` / `evalFloatNeg64`
      helpers in `vm_exec64.bn` + host-testable unit tests for
      each helper.
    - `ae08c1ed`: `execOp64` dispatch glue — joins source pair(s),
      bit_casts through `int64` to `float64` for the compute,
      bit_casts back, splits to dst pair (or single-slot bool for
      compares).  Direct `execOp64(&stackArr[0], instr)` tests
      cover all three shapes (binary arith, unary FNEG, compare-
      writes-single-slot).
    - `00b10e38`: lowering — `lowerBinOp` / `lowerCmpOp` add an
      `isFloatPair` branch alongside the existing `isIntPair`;
      `OP_NEG` dispatches `BC_FNEG64`; `OP_CONST_FLOAT` emits
      `BC_LOAD_IMM64` with `splitInt64` halves when
      `is64BitScalar(instr.Typ) && REG_SLOT < 8`.
    - `769d2e54`: gate test for OP_CONST_FLOAT — confirms 64-bit
      host falls back to `BC_LOAD_IMM` (no spurious pair branch).
  - **REMAINING GAP — int64 side of int↔float CONVERSION casts is NOT
    pair-aware (latent; surfaced 2026-06-12 by the int↔float32 VM-fix
    review).** The "DONE" above covers float *arith/compare* pairs and
    the *float* side of conversions; it does NOT cover an int64/uint64
    operand of a `cast` to/from a float:
    - int→float SOURCE side (`BC_SITOF`/`BC_UITOF`/`BC_SITOF32`/
      `BC_UITOF32`): the handlers read the int source as a single slot
      (`regs[instr.Src1]`) and `lowerCast`'s int→float arm has no
      `is64BitScalar(srcTyp) && REG_SLOT < 8` check, so `cast(float*,
      <int64>)` on a 32-bit host drops the source's high half. (These
      handlers ARE dest-pair-aware for the float64 result — the
      asymmetry is source-only.)
    - float→int DEST side (`BC_FTOSI`/`BC_FTOUI`/`BC_F32TOSI`/
      `BC_F32TOUI`): the handlers write a single dest slot via
      `cast(int, f)` (host int) and `lowerCast`'s float→int arm has no
      `is64BitScalar(dstTyp)` check, so `cast(<int64/uint64>, <float>)`
      on a 32-bit host leaves the dest's high slot stale (and truncates
      through a 32-bit host int). (These handlers ARE source-pair-aware
      for a float64 source — the asymmetry is dest-only.)
    Latent, not a live miscompile: no conformance mode runs the bytecode
    VM on a 32-bit host (the `-int` legs run `bni` natively on the
    64-bit build host; arm32 modes are comp/native, not VM), and the
    arm32 `pkg/vm` unit tests don't exercise int64↔float conversion
    casts. NOT introduced by the int↔float32 fixes (`289420b6`/
    `3fd7e712`) — the new float32 ops faithfully mirror the existing
    single-slot float64 ones. Fix (to land before/with any arm32
    VM-host enablement): add `is64BitScalar` gates in both conversion
    arms of `lowerCast` and pair-aware source/dest handling
    (`joinInt64`/`splitInt64`) in the eight handlers, plus direct
    `execNumericCast` unit tests in `vm_exec64_test.bn` driving a
    pair-wide int64 source and dest.
  - **End-to-end arm32 coverage status (2026-05-28)**:
    - `pkg/vm` source compiles cleanly on arm32 (since `ba1a798`).
    - Conformance `builder-comp_arm32_linux`: green.
    - **pkg/vm unit tests on `builder-comp_arm32_linux`: green**
      (was 16 failures pre-session → 9 → 1 → 0).  The bytecode-VM
      BC_*64 / BC_F*64 dispatch and slot allocation are now fully
      end-to-end-validated on a real 32-bit target — including
      the `TestRepro_StructWithManagedSliceFieldAppend` managed-
      memory path, which surfaced the hardcoded-LP64 managed-
      allocation-header offset that `81d31b7c`'s MANAGED_HDR
      const fixed.
    - The cascade-revealed packages — pkg/{types, codegen,
      native/{common,aarch64,x64}} — are also green on arm32 now
      after the LP64-baked-test cleanup (`11ff9864`, `2d13838d`).
    - Remaining arm32_linux failures (5) are all the int64-min-
      boundary cluster in pkg/{bootstrap,buf,ir} — see the
      "arm32 unit-test cleanup" entry for the bucket.  Unrelated
      to this work.

### `data_pkg_descriptor.bn` header/slice-width conflation — 🟢 LOW (non-urgent cleanup)
The `GetTarget().IntSize` "footgun" was a MISDIAGNOSIS and the native-accessor header reads
were switched to `ManagedHeaderSize()` (main `581216d9`) — see [claude-todo-done.md](claude-todo-done.md).
Residual: `data_pkg_descriptor.bn` (IR-gen phase) still uses one int-sized `w` for BOTH the
managed-header words (pointer-sized) AND slice lengths (int-sized) — a documented "assumes
PointerSize==IntSize" conflation, harmless on every shipping ABI. Untangle header (→
`ManagedHeaderSize`/ptrSize) from slice-length (→ IntSize) only if a wide-int ILP32 ABI is targeted.

## Slimming pkg/bootstrap & pkg/libc; C interop (`__c_call`)

### Slim `pkg/bootstrap` and `pkg/libc` by migrating callers OUT
- **What**: rather than converting bootstrap's I/O surface
  in place, migrate callers AWAY from `pkg/bootstrap.X` and
  `pkg/libc.X` toward whatever the long-term replacement is
  (a new I/O package, a slimmer `pkg/std/os`, etc., TBD).
  Goal: shrink the surface of both bootstrap and libc until
  they can either be retired entirely or held as truly minimal
  bootstrap primitives.
- **Approach** (sketch — needs design): identify call sites,
  classify them by what they want (formatted print, file I/O,
  process control, raw libc memops), and route each class to
  the canonical replacement.  bootstrap and libc only get
  what's TRULY platform-essential and inappropriate for any
  higher-level package.
- **Progress**:
  - **libc Memcpy / Memset — DONE 2026-06-02 (binate `87965b70`)**:
    the libc-host rt's MemCopy / MemZero now do pure-Binate byte loops
    (matching the baremetal rt, which already did) and Box copies via
    MemCopy, so both primitives were removed from the whole surface —
    `pkg/libc.bni`, `runtime/libc_stubs.c`, the cmd/bni + vm extern
    registries, and the vestigial baremetal `bn_pkg__libc__*` aliases
    in semihost.s.  No BUILDER bump (gen1 links BUILDER's runtime;
    gen1's outputs emit no `bn_pkg__libc__*` and link checkout's
    runtime).  Verified across compiled / VM / self-hosted / baremetal
    lanes.  Perf footnote: the byte loops are slower than libc
    memcpy/memset at -O0 (no idiom recognition) — accepted for now,
    revisit with a word-at-a-time loop if it shows in profiles.  This
    does NOT touch the C-ABI memcpy/memset LLVM emits for aggregate
    copies (llvm.memcpy intrinsics), which are independent of pkg/libc.
- **Remaining libc surface**: Malloc / Calloc / Free (now the only
  callers; need a real Binate allocator to retire) and Exit (needs a
  process-exit syscall, gated on the C-free syscall story).
  `pkg/bootstrap` — the larger I/O surface — is the next target.
- **`bootstrap.Itoa` — FULLY RETIRED (2026-06-08, `f7966135`).**  Every
  caller migrated, then the function, declaration, tests, baremetal
  duplicate, and VM extern registration all removed.  Now that
  `pkg/std/strconv` has `Itoa(v int)`
  (base 10), `FormatInt(v int64, base)`, and `FormatUint(v uint64, base)`,
  they are the canonical replacement for `bootstrap.Itoa`.  Goal: every
  Tier-1/Tier-2/Tier-3 caller uses strconv instead of bootstrap (a
  sub-step of retiring the bootstrap int-format surface).
  - **The old "BUILDER tree CANNOT import strconv" constraint was wrong /
    is now moot.**  `strconv` (whole package, incl. its `pkg/std/math/big`
    dependency via `ftoa.bn`) is ALREADY in cmd/bnc's BUILDER-compiled
    tree: `pkg/binate/ir/gen_const_fold.bn` and
    `pkg/binate/native/common/common_float.bn` import it, and BUILDER
    compiles them when building gen1.  So BUILDER-surface packages
    (`token`, `native/*`, codegen, ir, …) CAN migrate — verified by
    migrating `token` (gen1 rebuilds clean across builder-comp / -int /
    -comp).  No integer-only strconv subpackage is needed.
  - **`pkg/builtins/lang` (Tier-0 core) — DONE (2026-06-07):** lang can't
    import `strconv` (below Tier 1; layering inversion, and a cycle since
    strconv's closure reaches the builtins), so it got package-internal
    full-width formatters (`formatUint64` / `formatInt64`, mirroring
    `bootstrap.Itoa`'s uint64-magnitude approach incl. the two's-complement
    trick for int64-min).  This also fixed a correctness bug: the impls had
    funnelled through `bootstrap.Itoa(cast(int, x))`, which on 32-bit
    targets TRUNCATED the wide types — `(int64/uint32/uint64).String()`
    were WRONG on ILP32 for values outside int32 range — and mis-signed
    unsigned values ≥ 2^63 on every target.  Each impl now widens
    losslessly (signed → `cast(int64, x)`, unsigned → `cast(uint64, x)`);
    lang keeps `bootstrap` only for `formatFloat`.  Covered by lang_test.bn
    boundary cases (the unsigned ≥ 2^63 ones fail under the old code on a
    64-bit host) and `conformance/653_int_string_width` (width-independent
    output, one .expected for LP64+ILP32; guards the 32-bit truncation
    under the arm32 modes — green on all 64-bit modes locally, arm32 needs
    qemu so it runs in CI).
  - **Conversion discipline for the migration:** route each site by the
    *argument's* type, never by a lossy down-cast — bare `int` →
    `strconv.Itoa`; wider signed → `strconv.FormatInt(cast(int64, x), 10)`;
    unsigned → `strconv.FormatUint(cast(uint64, x), 10)`.
  - **Leave (not formatting calls / separate decisions):** the extern
    registrations that expose `bootstrap.Itoa` to interpreted code
    (`pkg/binate/vm/extern_register_std.bn`, `cmd/bni/externs.bn`) — those
    go when `bootstrap.Itoa` is deleted, not now; the test-runner codegen
    in `cmd/bnc/gen_test_runner.bn` (emits source that calls
    `bootstrap.Itoa`); and `conformance/064_bootstrap_funcs.bn` (tests
    `bootstrap.Itoa` itself).
  - **Progress — all migratable package callers DONE** (2026-06-07; each
    green across builder-comp / -int / -comp, landed on main, one package
    per commit): `token`, `repl`, `native/{x64,aarch64}`, `vm`, `ir`
    (test-only), `lexer` (test-only), `types` (test-only), `lint`
    (test-only), `cmd/bnlint`, `cmd/bni`.  Every arg was a bare `int`, so
    all sites used `strconv.Itoa` directly (no `FormatInt`/`FormatUint`
    needed yet).
  - **Retirement — DONE** (landed in order, each its own commit):
    `gen_test_runner.bn` formats counts via `passed.String()` (`c2aaaabf`,
    relying on [A]); `321` migrated to `total.String()` (`9ba85eec`);
    `conformance/064` retired (`0d7c0501`); the VM extern registration
    dropped from both drivers (`6d2384de`); and finally the definition,
    `.bni` declaration, unit tests, and baremetal duplicate removed
    (`f7966135`).  The bootstrap int-formatting surface used by
    print/println (`formatInt`/`Int64`/`Uint`/`Bool`/`Float`) deliberately
    STAYS — only the standalone allocating `Itoa` is gone.
  - **Done since:** the ad-hoc `intToChars` helpers — the package-scoped
    one in `pkg/binate/ir/gen_func_lit.bn` (3 call sites: `__closure_local_`,
    `__funclit_`, `__mv_local_`) and a duplicate in
    `pkg/binate/vm/func_index_test.bn` — now use `strconv.Itoa` and are
    deleted (2026-06-07).
- **[A] Primitive `.String()` without importing `pkg/builtins/lang` —
  DONE across all execution modes (compiled `37b2ffcc`, VM `487c2d08`).**
  `myInt.String()` resolves AND links/executes with no import in both the
  compiled backends and the bytecode VM; naming the `lang.Stringer`
  interface *type* still requires the import (gated by the type checker).
  Mechanism (reverses the "No auto-import" decision in
  `plan-primitives-impl-interfaces.md`, for methods only): `ensureLangLoaded`
  force-loads lang so its carve-out impls attach `String()`/`Compare()` to
  the global primitive singletons (resolution); `appendLangImport` (a clone
  of `appendBootstrapImport`, added at every `RegisterImports` site with the
  same self-import guard, in BOTH `cmd/bnc/compile_imports.bn` and
  `cmd/bni/irgen.bn`) registers lang's signatures so the cross-package call
  resolves/links.  DCE/baremetal worry is moot (unused impls stripped by
  `--gc-sections`/`-dead_strip`).  Full conformance green in both
  builder-comp (1085) and builder-comp-int (1072).  Covered by conformance
  `654`–`656` (per-type positives) + `658` (negative).
  - **Remaining follow-up — the repl.** The repl has its own import setup
    (`pkg/binate/repl/{ir_imports,session,util}.bn`) not covered by the
    `cmd/bni` change; add `ensureLangLoaded` + `appendLangImport` there so
    `.String()` works at the repl too.  Small, same pattern.
- **[B] Test runners can depend on the stdlib — DONE (2026-06-08,
  `36e979df`).**  The `cmd/bnc --test` runner (`gen_test_runner.bn`,
  compiled by `test.bn`) is parsed *after* typecheck, so a stdlib package
  it imports that no test package pulls in was never loaded → not compiled
  → wouldn't link.  Fix: `genTestRunner` declares its stdlib deps in
  `testRunnerStdlibImports()`, and `test.bn` force-loads that list before
  typecheck (the compile loop already builds every loaded package, so they
  then link).  Adding the future `pkg/std/os` (for `Args`/`Open` when
  bootstrap I/O migrates) is a one-line addition to that list plus its use
  in the runner.  Exercised end-to-end now by a placeholder: the runner
  imports `pkg/std/errors` and makes one harmless `errors.New` call
  (TODO-marked for removal once a real dep lands) — proven by
  `pkg/binate/buf` (closure `{buf, testing}` excludes errors) whose test
  binary links the errors-importing runner only via the force-load.  The
  whole unit-test suite now exercises [B].  (The VM `-int` path is
  unaffected — `cmd/bni` executes tests directly, no generated runner; a
  future VM stdlib dep would be force-loaded there the same way as
  bootstrap/lang.)  Distinct from [A], which force-loaded lang to make
  `bootstrap.Itoa` removable.
- **Why migrate OUT rather than convert in place (do NOT re-attempt the
  in-place shape)**: in-place renames of packages whose surface is
  declared-only and resolved by C symbols (`pkg/libc`, and the I/O side
  of `pkg/bootstrap`) hit a wall that pure-Binate-package renames
  (pkg/rt → pkg/builtins/rt) do not.  The wall: at Stage 1, gen1 is
  linked against BUILDER's bundled `libc_stubs.c` (auto-found next to
  `--runtime`), which only defines symbols under the OLD mangled name
  (e.g. `bn_pkg__libc__Memset`).  Checkout source — now compiling under
  the NEW package name — emits calls to `bn_pkg__builtins__libc__Memset`,
  which is UNRESOLVED at Stage 1's link.  Pure-Binate packages don't hit
  this because the bnc-compiled package provides the NEW-name symbols as
  definitions in its own `.o`; declare-only-via-C packages have no such
  Binate-side definition.  Compat aliases in checkout's `libc_stubs.c`
  don't help — BUILDER's runtime is what Stage 1 links against, not
  checkout's.  Resolving would require either (a) pointing Stage 1's
  `--runtime` at checkout's (build-script surgery), (b) a supplemental
  compat .o via `--link-after-objs` (build-script surgery + new
  artifact), or (c) two release cycles with a transitional bridge —
  none worth the bootstrap migration's payoff.  Migrating callers OUT
  side-steps the whole tangle.
- **Status**: in progress.

### Inject `pkg/bootstrap` into the VM + convert I/O to `__c_call` — Phase 1 DONE; Phase 2 DEFERRED (BUILDER-runtime coupling)
- **Phase 1 LANDED** on main (`a7fabc7a`, 2026-06-03): bootstrap is now
  native-only in the VM — cmd/bni skips lowering it, the format helpers
  (formatInt/Int64/Uint/Bool/Float, Itoa) are registered as externs in
  both `registerBootstrapExterns` copies, bootstrap's bytecode unit tests
  are xfailed in the 3 `-int` modes, and `extern_register_std_test` guards
  format-helper registration.  `formatFloat` (the first native float
  extern) dispatches via the all-int shim ABI (`7abc3809`).  Verified:
  `287_float_println` green in `-int`; full `builder-comp-int` /
  `-comp-int` / `-int-int` clean but for pre-existing failures.
- **Plan**: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md). The
  rt-drop-libc pattern applied to bootstrap: eliminate the hand-written
  `bn_pkg__bootstrap__*` I/O glue in `binate_runtime.c` by converting it
  to `.bn` + `__c_call`, and make bootstrap native-only in the VM.
- **Phase 2 DEFERRED (2026-06-03), possibly indefinitely**: converting
  the I/O to `.bn` *adds* `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}`
  defs that collide with BUILDER's pinned runtime (gen1 links it,
  `build-compilers.sh:55-62`) → duplicate-symbol link failure building
  gen1. It's a runtime-ABI change, so it can only be done *during a
  BUILDER bump/release* (the new BUILDER's runtime omits the I/O), not in
  the pinned-BUILDER tree. The trivial+moderate `.bn` code was written +
  reviewed (correct modulo the link blocker) and is preserved in
  plan-bootstrap-ccall.md's appendix. `Stat` is a further defer (struct
  stat platform divergence → needs a per-libc-platform impl split). It may
  be better to *eliminate* these bootstrap I/O functions (subsumed by a
  real stdlib `io`) than convert them — so this may never be worth doing.
- **Harder than rt**: `__c_call` is scalar/pointer-only, but bootstrap's
  I/O takes slices + returns managed-slice aggregates → marshalling
  (null-term cstr, data-ptr extraction, aggregate construction). `Args`
  can't be pure `__c_call` (no libc fn returns argv) — a minimal argv
  hook stays in C. Not C-freedom (still links libc syscall wrappers).
- **Needs a BUILDER bump** (the deferral reason above; the original
  "no BUILDER bump" claim was wrong — BUILDER *compiles* `__c_call` fine,
  but its *runtime* still defines the I/O symbols gen1 links). Baremetal
  keeps its semihost impl (per-target, like rt). Filed 2026-06-03.

### Annotations and C function interop
- **Option E (`__c_call` intrinsic) has a detailed implementation plan:
  [plan-c-call.md](plan-c-call.md).**
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
  - **Option E**: a `__c_call` compiler intrinsic at the call site, no
    declaration needed.  Two forms were considered:
    - **E1 (rejected)**: pass a C prototype string —
      `__c_call("ssize_t write(int, const void*, size_t)", fd, buf, len)`.
      Reads nicely, but forces the compiler to parse C and resolve C
      types, which drags in typedefs, macros, and platform builtins
      (`__size_t` &c.).  Not practical.
    - **E2 (preferred)**: pass the C symbol name, an explicit return
      type, then the argument values already in (or cast to) the
      Binate types that match the C ABI —
      `result = __c_call("write", int, cast(int, fd), cast(*uint8, buf), cast(uint, len))`
      (casts are unnecessary when the variables already have the right
      type).  Supported argument/return types: scalars, struct types,
      and pointers to these (to any depth: `*T`, `**T`, …).  This
      reuses the backends' existing platform-C-ABI lowering (struct
      sret thresholds, register assignment) — no C parsing, no type
      resolution, no new ABI logic.  The symbol name is emitted
      verbatim (no `bn_` mangling); the backend emits the matching
      `extern`/`declare`.
  - **C-types alias package (decided)**: a package (e.g. `pkg/c`)
    pins the Binate↔C scalar correspondence in one place so call sites
    don't open-code it.  `C_int`/`C_uint` = `i32`/`u32` (C `int` is
    32-bit on both ILP32 and LP64, *not* target-word-width like Binate
    `int`); `C_long`/`C_ulong` = target-word (LP64 Unix; matches Binate
    `int`/`uint`); `C_size_t` = `uint` (pointer-width); `C_char` = `i8`
    (signedness is platform-dependent in C — note the caveat, but it's
    promoted on pass so rarely matters).  Plus a sentinel `C_void` for
    the return-type slot of functions that return nothing.  So the
    example's `fd` is really `C_int` (= `i32`), not `int`.
  - **Scope decisions (v1)**:
    - **Compiled-mode-only to start.** The compiler emits a direct
      call; the VM would need FFI-style dispatch (resolve the symbol
      via the extern registry + marshal by the supplied types) — punt
      that.  `__c_call` outside compiled mode is an error for now.
    - **Include variadics from the start.** The whole point of
      `__c_call` is to retire `pkg/bootstrap`'s hand-written C
      wrappers and the special shim machinery — and several of those
      OS interfaces are variadic in C (`open(const char*, int, ...)`
      where `mode` is a vararg; `fcntl`, eventually the `printf`
      family).  Punting variadics would leave bootstrap unable to go
      away, defeating the purpose.  So v1 supports them.
      - **Boundary marker (required).** The call site must declare
        where fixed args end and variadic args begin — it can't be
        inferred from the values (`open(path, flags, mode)` is
        indistinguishable from a 3-fixed-arg call).  Proposed: a
        `C_varargs` sentinel (or a recognized `...` token) in the
        argument list:
        `__c_call("open", C_int, path, flags, C_varargs, mode)`.
        Everything after the marker is an anonymous/variadic arg.
      - **Backend work is lopsided.** LLVM path: nearly free — emit
        `declare i32 @open(i8*, i32, ...)` + a varargs call with the
        right fixed-arg count, and LLVM does the platform-correct
        lowering (x86-64 `AL` = vararg float count, darwin-arm64
        stack-passing, 64-bit-vararg alignment) for us.  Native
        backends (`pkg/native/{arm64,amd64}`): real work — they emit
        machine code directly and must implement the vararg
        convention per target (darwin-arm64 stacks all varargs;
        x86-64 SysV sets `AL`; AArch64-Linux/arm32 mostly match the
        fixed convention but 64-bit varargs need 8-byte alignment).
        This extends the existing `CallConv`/register-assignment
        logic; needs per-target tests.
  - **Open considerations for E2 (still to resolve)**:
    - Confirm the full `pkg/c` scalar table against each target
      (`C_long` on a 32-bit target, `C_char` signedness, the float
      types if/when floats land).
    - Final spelling of the variadic boundary marker (`C_varargs`
      sentinel vs a `...` token vs an explicit fixed-arg count).
    - VM/dual-mode FFI dispatch (deferred above) when interpreted-mode
      `__c_call` is eventually wanted.
  - **Companion idea — link-requirement annotation (sketch)**: Option E
    makes a C symbol *callable*; a complementary annotation would make
    it *resolve at link time* by declaring, at the source level, that
    using a package requires linking some C library — so the driver
    adds the flag automatically instead of every consumer passing
    `--cflag -lm` / `--link-after-objs` by hand.  Prior art:
    Rust `#[link(name = "m", kind = "static")]`, Go cgo
    `// #cgo LDFLAGS: -lm`, MSVC `#pragma comment(lib, "foo")`.
    Natural shape: `#[link("m")]` (optionally a `static`/`dynamic`/
    `framework` kind), most naturally on the `.bni` since the link
    requirement is part of the package's contract.  This is also the
    first real payoff of the general annotations feature this item is
    about — both Option E and this want it.
    - **Open wrinkles**:
      - **Transitivity** — the requirement must propagate through the
        import graph (aggregate + dedup all declared libs for any
        binary that transitively imports the package).  Hooks into the
        loader's `ldr.Order` walk + the driver's `clangArgs` assembly.
      - **Link ordering** — static archives only supply symbols
        referenced by *earlier* inputs, so aggregated `-l` entries
        need correct placement vs. the `.o` files and runtime (the
        driver already does this for `linkAfterObjs`).
      - **Search paths** — keep the annotation name-only (`-l`); leave
        `-L<dir>` to driver flags.
      - **Platform-conditionality** — a `libm` dep is meaningless on
        bare-metal arm32 and `framework` kind is macOS-only, so the
        annotation likely needs to be target-qualifiable.  Ties into
        the C-free principle: this exists only to interface with
        existing C systems and should evaporate on freestanding
        targets.
      - **Static-spec portability** — even with `kind = static`,
        expressing it portably is messy (GNU ld `-l:libfoo.a` /
        `-Wl,-Bstatic`; macOS `ld` has neither), so it may need
        per-platform lowering in the driver or a full-path escape
        hatch.

## Build constraints (`#[build(EXPR)]`)

### Collapse `pkg/bootstrap` onto `#[build]` — 🟡 OPEN (next, per user 2026-06-19)
With BUILDER at `bnc-0.0.9` (both `bnc` and `bnlint` parse `#[build]`), `pkg/bootstrap` — whose
per-target variants are currently PATH-selected and which lives in cmd/bnc's BUILDER-compiled
tree — can be collapsed onto `#[build(...)]`-gated declarations, the same way `pkg/builtins/build`
was. See [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md). (This was
the "bonus" of the build.bni-dedup workaround removal, now landed — binate `9c2ac789`, archived in
[claude-todo-done.md](claude-todo-done.md).)

### Build constraints (`#[build(EXPR)]`) — deferred follow-ups (arch/os MVP landed) — 🟡 OPEN
The `#[build(EXPR)]` arch/os MVP is landed at all four granularities (file / decl / import / `.bni`),
host-default config overridable per `--target`, through `c7249552` (conformance 731/733/735/736/737/746/747);
full design in [`plan-build-constraints.md`](plan-build-constraints.md), archived in
[claude-todo-done.md](claude-todo-done.md). Still deferred (none started):
- Vocabulary beyond arch/os: `triple` / `backend` / `libc` / `ptrsize` / `version` with `is` / `at_least` / `at_most`.
- `bnlint --target`; main-module gating; migrating the `impls/` duplicate trees onto constraints.
- The separate inline-asm (`#[asm]`) doc that composes with this substrate.

## bnlint rules, unused-entity checks & lint skips

### unused-entity checks — all five bnlint rules (a/b/c/d/e) + file split DONE & LANDED (`plan-unused-checks.md`)

Add unused-locals `(b)` / unused-private-func `(c)` / -global `(d)` / -type `(e)` checks on top of the existing `unused-import` rule. Full design in **`explorations/plan-unused-checks.md`**.

- **Phase 0 (file-scoped imports): ✅ LANDED** (`cf0d1cad` + follow-ons); loader retains `pkg.Files`. This is what unblocked `(a)`.
- **`(a)` unused-import cross-file gap: ✅ DONE & LANDED** (main `51f8e90c`). Per-file import attribution via `pkg.Files` (was the deduped merged list → sibling-file unused imports silently missed).
- **shared `refs.bn` index + `(d)` unused-global: ✅ DONE & LANDED** (main `b57c6b18`). Reference-presence; `.bni` export skip; group recursion.
- **`(e)` unused-type: ✅ DONE & LANDED** (main `7083b65c`). Reference-presence; export via `.bni` same-name peer; self-ref + receiver-ref excluded (receiver does NOT count); generic instantiation heads/args recorded.
- **Adversarial review (2026-07-02)** fixed two latent-to-narrow false positives in the shared walk (annotation-arg exprs, bare-ident generic-CALL type args), landed with `(e)`. Tree-wide: 0 unused-import/global/type across all 50 packages.
- **Decisions (2026-07-02, user):** all are **WARNINGS**. Detection: reachability for `(c)`, reference-presence for `(d)`/`(e)`. `(e)` receiver does NOT count as a use.
- **Latent bugs:** `markBniExportedVars` DECL_GROUP gap ✅ FIXED (`a43c24f7`); `DECL_TYPE` still carries no `Exported` (`(e)` works around via `.bni` peer — a cleaner loader fix remains optional).
- **`(b)` unused-local: ✅ DONE & LANDED as a BNLINT rule** (main `11c6bb8e`, 2026-07-02). `pkg/binate/lint/unused_local.bn` — flags a function-local var never referenced in its function; CONSERVATIVE (reads AND writes count → no false positives), FLAT per-function scoping (shadowing under-warns), params/`_`/local-consts excluded, closure bodies walked. 21 tests; tree-wide 0; 2-lens adversarial review. **History:** first (wrongly) done CHECKER-side (compiler warning) and reverted (`8d8f7314`) — Binate emits NO compiler diagnostics for unused-X; it is a LINT concern. Optional future refinements (not scheduled): block-scope precision, write-only detection (~231 dead-write locals, would be lint warnings).
  - **`(c)` unused-func: ✅ DONE & LANDED** (main `83149f3e`) — reachability from roots; dead-code islands flagged; methods excluded; 6 tests; 0 tree-wide.
  - **File split: ✅ DONE & LANDED** (main `7f2e2c82`, 2026-07-02). `scope.bn` (547) → `scope.bn` + `layout.bn` (Type Layout) + `layout_offsets.bn` (Composite Type Layout); `check_expr.bn` (555) → `check_expr.bn` + `check_addr.bn` (addressability); `scope_test.bn` (591) split to match; new `check_addr_test.bn` (11 branch tests). Pure move (3-lens adversarial review: no blockers/majors), all under the 500-line cap, hygiene green.

### Remove verified-dead PRODUCTION unused-import / unused-func findings — ✅ DONE & LANDED (`cef6fdec` + `717b694d`, 2026-07-03)

**RESOLUTION.** 19 unused imports + 19 dead unexported funcs removed (the count
was 20 imports when first noted; one had already been fixed concurrently by
landing time). Landed in two commits: `cef6fdec` (types/codegen/ir front/mid-end)
+ `717b694d` (native/asm/vm backends). Also removed a cascade global (`lastRawSeq`,
used only by the removed `emitManagedToRaw`) and refreshed 3 stale comments the
removals exposed. Verified: per-package `bnlint --tests` = 0 production
unused-import/func; a 9-package adversarial workflow found 0 dangling refs / 0
test files touched; gen1 build succeeds (BUILDER tree still compiles); 17-package
unit suite green; hygiene 15/15.

**Note for the eventual hygiene-wiring:** the authoritative "truly dead" set was
taken from the `--tests` run, NOT a plain run. A plain (non-`--tests`) bnlint flags
**31** production unused-func — 12 more than the 19 removed — because those 12 are
production helpers used ONLY by tests (a plain run can't see the test caller). They
are NOT dead. So if `bnlint --tests` is ever wired into hygiene, unused-func MUST
run with `--tests`, or it will false-flag those 12. (They're a separate judgment
call — "production helper only a test uses": delete-with-test or keep — not
addressed here.)

Original context: surfaced while triaging `bnlint --tests` findings; real dead
code the current-source bnlint flags but the bundled hygiene bnlint
(`bnc-0.0.10`) predates. Removal was BUILDER-safe.
Do it per-package, verify with a per-package smoke + `scripts/build-bnc.sh` (many
are in bnc's BUILDER tree).

**20 unused-import (file → unused import):**
- `asm/parse/aarch64_instr_helpers.bn` → `pkg/binate/asm`; `asm/parse/parse.bn` → `pkg/binate/asm/aarch64`
- `codegen/emit_ccall.bn` → `pkg/binate/types`; `codegen/emit_impls.bn` → `pkg/binate/types`
- `ir/gen_flow.bn` → `pkg/binate/buf`; `ir/gen_generic.bn` → `pkg/std/strings`; `ir/gen_init.bn` → `pkg/binate/buf`; `ir/gen_selector.bn` → `pkg/binate/buf`
- `native/aarch64/aarch64_ops.bn` → `pkg/binate/buf` + `pkg/binate/mangle`
- `native/arm32/arm32_emit.bn` → `pkg/binate/iropcode`
- `native/common/common_callconv.bn` → `pkg/binate/ir` + `pkg/binate/iropcode`
- `native/x64/x64_funcvalue.bn` → `pkg/binate/asm`
- `types/check_expr_constfold.bn` → `pkg/binate/ast`; `types/checker.bn` → `pkg/binate/token`; `types/types.bn` → `pkg/binate/ast` + `pkg/binate/buf`
- `vm/lower_data.bn` → `pkg/binate/types`; `vm/lower_instr.bn` → `pkg/builtins/rt`

**19 unused-func (file → dead func):**
- `asm/parse/arm32.bn` → `expectArm32CommaLabel`; `asm/parse/lex.bn` → `lexPeek`; `asm/x64/x64_sys.bn` → `readI32`
- `codegen/emit_util.bn` → `emitSliceRef`, `emitManagedToRaw`, `isCharElem`, `isCharElemFromSlice`, `isStructElem`, `isStructElemFromSlice` (superseded by the DataGlobal migration)
- `ir/gen_copy_emit.bn` → `genStructCopy`; `ir/gen_func.bn` → `lookupLocalVar`; `ir/gen_generic.bn` → `lookupGenericTypeDecl`; `ir/ir.bn` → `makeArgs3`
- `native/aarch64/aarch64_closure_shim.bn` → `totalCaptureWordsAA64`; `native/aarch64/aarch64_names.bn` → `vtableSymForLLVM`, `shimSymForLLVM`; `native/x64/x64_closure_shim.bn` → `totalCaptureWords_x64`
- `types/check_generic_type.bn` → `lookupGenericIfaceDecl`, `lookupGenericTypeDecl`

(The 17 PRODUCTION `[managed-to-raw-assign]` findings in `pkg/binate/asm/*` are a
DIFFERENT, already-tracked follow-up — see the asm INCREMENT-2 entry below.)

### MINOR (hygiene / lint) — investigate the `[managed-to-raw-assign]` findings in `pkg/binate/asm/*` (2026-06-20) — 🟡 OPEN
The compiler-tree lint-coverage gap is ✅ FIXED & LANDED (`582c1327`): `scripts/hygiene/lint.sh`
discovery is now recursive over `pkg/`, so all ~23 `pkg/binate/*` compiler packages are bnlint
targets (the old one-level `pkg/*/` glob matched only `pkg/binate/`, which has no direct `.bn`, after
the `pkg/parser`→`pkg/binate/parser` reorg — so ZERO compiler packages were linted; only the
bnlint-RULES check had this gap, since file-length/naming/doc use a recursive `find`).  Two real
`[unused-import]`s it surfaced (`ir/gen.bn`→ast, `native/aarch64/aarch64_call.bn`→mangle, both
comment-only) were removed.  **Residual** — 5 asm subpackages are temporarily in `LINT_SKIP`
(`pkg/binate/asm/{arm32,elf,macho,parse,x64}`) for a `[managed-to-raw-assign]` finding
(`var data *[]uint8 = sec.Data` — a borrow of a held `@[]uint8`).

**Per-site audit DONE (2026-06-30, bnc-0.0.10 bnlint + adversarial workflow + source verification of
the one real bug).** 19 findings across the 5 packages:
- **1 REAL use-after-free** — `parse/parse.bn:160` (`name = expr` constant def borrowed `tok.Text`,
  then `LexNext` freed it before the read). ✅ **FIXED & LANDED (main `8a883450`)** — own the name
  first (`buf.CopyStr`) + regression test `TestParseConstNamePreserved` (verified failing pre-fix);
  write-up in the done file. The rule was RIGHT here — the skip hid a real UAF.
- **1 real `[unused-import]`** — `parse/aarch64.bn:3` imported `pkg/binate/asm`, never used. ✅ FIXED
  (main `8a883450`, same commit).
- **17 safe-borrow over-flags** — every site in `arm32`/`elf`/`macho`/`x64` (all 9) + 6 of the 8
  `parse` sites. All borrow a field of a managed owner (`@asm.Section`/`@asm.Assembler`/a `BinBuf`
  local / a by-value `Token` param / a function-scope buffer) that provably outlives the raw view's
  synchronous read or in-place patch. The rule conservatively flags `@[]T → *[]T` without lifetime
  analysis.
**Un-skip path:** the two real findings are ✅ FIXED (main `8a883450`). The 17 safe-borrow over-flags
are handled by **suppression: the `// bnlint:allow <rule>` directive mechanism is ✅ LANDED (main
`91286ab8`)** (decision A — keep the rule strict, annotate each safe borrow with a justification;
generic across all rules). **Remaining (OPEN, BUILDER-gated) — INCREMENT 2:** adopt the directives +
un-skip. Add a trailing `// bnlint:allow managed-to-raw-assign — <why the owner outlives the borrow>`
to each of the 17 sites (the per-site reasons are in the workflow audit / the 5 package sections), and
drop `pkg/binate/asm/{arm32,elf,macho,parse,x64}` from `LINT_SKIP`. **Gated on the next BUILDER bump**
because hygiene runs the BUNDLED bnlint (`bnc-0.0.10`), which predates `91286ab8` and would ignore the
directives → red hygiene until the bump. Do it in ONE commit at the next bump, alongside dropping
`pkg/binate/interp` (see the BUILDER-lag-lint-skips entry) — i.e. that bump clears ALL remaining
`LINT_SKIP` entries except any still-pending real findings.

### Remove the BUILDER-lag lint skips after a BUILDER bump — 🟡 OPEN (narrowed to `pkg/binate/interp`; gated on next BUILDER bump)
`scripts/hygiene/lint.sh`'s `LINT_SKIP` group (A) is the BUILDER-lag set — packages the bundled
bnlint can't typecheck because they use a feature/fix newer than the bundle.

**The bnc-0.0.9 lag is CLEARED** (BUILDER is now `bnc-0.0.10`, checked 2026-06-29). `pkg/builtins/rt`
(the `"void"` `__c_call` spelling) and `pkg/std/os` (the `.bni` free-function-vs-method fix
`796effc7`), plus their importer chain `pkg/binate/{vm,repl}` + `cmd/{bni,bnas,bnlint}`, all lint
**clean** under the bnc-0.0.10 bundled bnlint (verified each directly). Dropped from `LINT_SKIP` —
restoring style-lint coverage on those seven packages, hygiene 15/15 — in `binate` lint.sh change
`c5a14146`.

**Still skipped — `pkg/binate/interp`**, but for a *newer* lag (not the rt/os one). **Root-caused
(2026-06-30): a synthesized-accessor NAME skew, not a missing bnlint capability — so the next bump
fixes it and NO linter work is needed.** The compiler-synthesized reflect accessor was renamed
`_Package` → `__Package` in `e12a8a3b` ("fix CRITICAL … close silent collision", 2026-06-26), which
postdates the bnc-0.0.10 release (`cdea9b9f`, 2026-06-23). interp's extern-registration references the
new name as a func value (`rt.__Package`, `reflect.__Package`, `errors.__Package`, …), but the bundled
bnc-0.0.10 checker still synthesizes/resolves the OLD `_Package` (verified: `emit_pkg_descriptor.bn`
mangles `"_Package"` at cdea9b9f, `"__Package"` at HEAD), so `<pkg>.__Package` is undefined under the
bundle — cascading to all four errors (`undefined: __Package` → `cannot call non-function` → `cannot
assign void to @Package` → `_func_handle argument must be a named function`). A current-source
(post-rename) bnlint lints interp clean. Action: at the next BUILDER bump (source ≥ `e12a8a3b`), drop
`pkg/binate/interp` from `LINT_SKIP` and close this entry.

**Next-bump checklist — the `asm/*` group (B) joins here.** The 5 `pkg/binate/asm/*` skips (real
safe-borrow over-flags) are un-skipped via the `// bnlint:allow` suppression mechanism (landed main
`91286ab8`), which is ALSO newer than the bundle — so the same bump that drops `interp` should also
adopt the 17 asm directives + drop `pkg/binate/asm/{arm32,elf,macho,parse,x64}` (see the asm
`[managed-to-raw-assign]` audit entry above). One bump clears every remaining `LINT_SKIP` entry.

### Raw-slice escape: decide whether a BROADER best-effort escape lint is wanted — 🟡 NEEDS DECISION
The original framing ("demote the raw-slice escape TYPE ERROR to a linter rule")
is obsolete: there is NO type-check rejection for raw-slice escape (the checker
never rejected it), and a `raw-slice-return` LINT rule already exists (`lint.bn`,
landed `10d19369`) — but it only covers the `@[]T → *[]T` "drops the managed
wrapper" return case. **Open decision (user):** is a broader best-effort escape
lint wanted (return / store-to-outliving-field / assign-to-global of a raw slice
borrowing a local), or is the current narrow rule + "raw is an opt-in escape
hatch" sufficient (close this out)?

## Hygiene checks: tier dependencies & file length

### Hygiene check: enforce `pkg-layout-spec.md` tier dependency rules
**What**: a `scripts/hygiene/` check (new script alongside `conformance-imports.sh`) that
statically validates every package's import closure against the tier ordering in
[`pkg-layout-spec.md`](pkg-layout-spec.md) ("Tiers"). Two facets of the same rule:
- **Dependency direction**: a package may import only packages at its own tier or **lower**;
  importing a strictly-higher tier is a violation. (This is the runtime enforcement of the spec's
  "Transitive constraint" + tier table.) Tiers low→high: 0 / 0b (`pkg/builtins/*`) < 1
  (`pkg/std/*`) < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3 (app-specific).
  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal; `pkg/binate/parser` importing
  `pkg/std/os` is fine.
- **Bundled-set closure**: bundled tiers (0/0b/1/1x — always/by-default bundled) must NOT import a
  not-bundled tier (2/3), and a tier-2 package's dependency closure must itself be tier 2. A
  bundled package whose closure escapes the bundled tiers silently breaks the bundle — the
  dependency's source isn't shipped, so a consumer compiling against the bundle gets
  `package "<dep>" not found`.
- **`pkg/std` → `pkg/stdx` refinement**: tier 1 (`std`) may depend on tier 1x (`stdx`)
  **internally** (`.bn` impl files) but **not externally** (`.bni` interface files) — a `.bni`
  importing `stdx` leaks a no-inter-version-compat (1x) type into `std`'s strict-compat surface.
  So the check must scan `.bni` imports separately from `.bn`: the std→stdx edge is allowed only
  from `.bn`. (Generalize if other interface-vs-impl tier asymmetries surface.)

**Why NOTHING currently catches this**: it only manifests when a consumer compiles the
offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene /
conformance step does today.

**Motivating bug (2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0)
imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`.
The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out
(`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with
`package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out
smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed
in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a
literal-init allocate+copy). This check would have caught it at the `import` line.

**How**: tier is path-derivable (`ifaces/core` + `impls/core/*` → 0/0b; `ifaces/stdlib/pkg/std`
→ 1, `…/pkg/stdx` → 1x; `pkg/binate/*` & other `pkg/<org>/*` → 2); `pkg/bootstrap` is a bundled
runtime primitive (treat as tier-0-equivalent). Walk every package's imports (split `.bni` vs
`.bn`), map importer + imported to tiers, flag any higher-than-self edge, applying the std/stdx
refinement. A whitelist file (cf. `conformance-imports.whitelist` / `naming.whitelist`) covers
sanctioned exceptions. EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately
imports `pkg/binate/buf`).

**First manual sweep (2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the
bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No
non-test bundled package imports outside the bundled set. Two non-obvious cases the check must
handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, NOT a violation —
`pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and
resolves under the arm32-baremetal build's own `-I`/`-L`; treat shipped `runtime/<target>/pkg/*`
as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in
`*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So
`lang → pkg/binate/buf` (binate `84818a77`) was the only true violation; the baseline is otherwise
clean.

**Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script only; wiring it into
`scripts/hygiene/run.sh` / CI is a separate decision for the user. (An earlier filing noted that a
hygiene check ultimately belongs in the run.sh master, so both could be done together — but that
wiring is still the user's call.) A first audit may surface other pre-existing violations to
triage.

### Lower the file-length `.bni` cap toward 1000/1200 — 🟡 OPEN
- **Residual** of the (now-archived) "Extend hygiene checks to scan `ifaces/`+`impls/`" work. The `.bni` file-length cap is currently 1500/1800 (warn/error); consider lowering toward 1000/1200.
- **Blocker**: `pkg/binate/ir.bni` (~1183 lines) exceeds the proposed lower cap and would need refactoring (split into sub-interfaces) first. A live `TODO` in `scripts/hygiene/file-length.sh` tracks this.
- (Full resolved diagnosis of the ifaces/impls hygiene-scan extension archived in claude-todo-done.md.)

## Type-system & checker semantics

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Readonly method receivers — deferred (gated on methods/interfaces)
- A method's receiver kind (`*readonly T` / `@readonly T`, plus value
  receivers — which are always readonly) determines which pointer kinds
  satisfy an `impl` and bounds what the method may mutate.  See
  `claude-notes.md` (value receivers always readonly; readonly-restricted
  dispatch expressed at the impl level; `*readonly T` receiver smoothing
  auto-takes `&t` at the call site).
- This was "Stage 3" of the old `const` type modifier.  The rest of that
  work landed and the type-level modifier is now spelled `readonly`
  (`plan-const-readonly.md`, COMPLETE 2026-06-03 — `const` split into
  compile-time `const` / `var` storage / `readonly T` modifier; that
  plan's three listed deferrals — readonly-slice slicing, `.bni`
  extern-var, `&pkg.Const` — are all since resolved).
- Deferred, not abandoned — depends on the methods/interfaces feature.
  Fold into that project's tracking when it firms up.

### `==` / `!=` (and relational) on aggregates — residual (generic re-check corner cases) — 🟢 LOW (triaged 2026-06-30: NOT actionable now)
The `==`/`!=`/relational aggregate story is ✅ DONE & LANDED — checker rejection
(binate `60719e01`), struct/array implementation (920a, main `f99f4a4e`),
generic-function path (920b, `6b748a24`), the sentinel-comparison decision, and the
generic-aggregate-field re-check (main `076eb525`); full arc archived in
[claude-todo-done.md](claude-todo-done.md). Two small residuals in the generic
instantiation re-check remain — **triaged 2026-06-30, neither actionable now**
(neither is a live miscompile):
- **(a) Order-dependent — COSMETIC only.** A forward-ref instantiation checked BEFORE
  the generic's body is type-checked falls back to the loud IR-gen error instead of a
  clean checker rejection (never a silent miscompile, never a false reject — just a
  less-friendly diagnostic in that ordering). A fully order-independent version needs
  a checker sub-pass or an explicit `comparable` constraint — non-trivial work for a
  diagnostic-quality-only gain; deferred.
- **(b) Generic-TYPE methods — UNREACHABLE (blocked on a future feature).** Verified
  2026-06-30: bnc does NOT support a method on a generic type with a type-param
  receiver (`func (b Box[T]) eq(...)` → "method receiver must be a named type",
  "undefined: T"). So the re-check gap for generic-TYPE-method comparisons cannot be
  triggered — there is no way to define such a method today. This becomes a real
  follow-up only if/when generic-type methods land; not a live gap.

### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Purely-value const extension (future language direction) — DESIGN, not started
Future direction split out of the (now-resolved) non-int-const mis-emit bug:
allow `const` of certain non-scalar but purely-value types (no storage, no
managed fields). Currently `const` is scalar-only (non-scalar → `errNonScalarConst`,
"use `var readonly`"); no `isPurelyValueType` predicate exists yet. A genuine
language extension, not a bug fix.

## Language-feature proposals

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

## Spec authoring & language-decision residuals

### Package-level var initialization is declaration-order, not dependency-order — spec decision needed
`var A int = B + 1; var B int = 10` makes `A == 1` (B is still 0 when A initializes),
NOT 11 — package-level VAR initialization runs in DECLARATION order, not dependency order.
`decl.order.forward` guarantees the forward NAME reference resolves (it compiles), but the
VALUE at init time follows declaration order. Go initializes package vars in dependency
order; Binate does not, and §9.8 is silent on var-init order. → a spec-vs-impl decision
(declaration-order vs dependency-order) for `spec-todo.md`. The Ch.9 tests do not assert
any var-init-order value (forward-ref is tested via a function). Surfaced authoring
`conformance/spec/09-declarations-and-scope`.

### §8.5 spec "precision residual" note appears stale — verify and drop
The §8.5 "Open (precision residual)" note in the conversions spec chapter says a constant
≥ 2^63 reached through a bitwise/shift op "is not yet rejected": `cast(int64, 0x4000000000000000 << 1)`. That exact
example — and `cast(int64, 1 << 63)` — now **reject** ("constant does not fit the cast
target type"). The bitwise-const fold may have been fixed; verify (other patterns?) and, if
so, drop the §8.5 residual note (like the Ch.13 generic-unparsed/d4-paren stale notes). No
born-stale xfail added (rejection is the correct behavior). Surfaced authoring
`conformance/spec/08-conversions`.

### Relational-comparison chain (`a < b < c`) diagnostic reach — nicety
The `expr.compare.relational` rule: `a < b < c` is correctly rejected in every context, but the
dedicated "comparison operators do not chain" message fires only for the identifier-leading
for-clause Pratt path (`parse_for.bn:199`); `if`/`var`/literal-leading contexts reject via generic
parse errors. Conformant (rejection holds) — a diagnostic-consistency nicety only. Surfaced
authoring `conformance/spec/13-expressions`.

### Spec Ch.16 (Packages) — adversarial-review follow-ups (test-quality, non-blocking) — 2026-06-19
The Ch.16 review found 0 blockers, 7 should-fix (landed tests work; these
improve rigor). 015 mis-cite already FIXED (re-cited pkg.resolve→pkg.identity).
Remaining, for a focused follow-up (with the build-constraint rework below):
- **Harness limit (root cause of 2 findings):** the runner gives a test ONE
  search root, so `pkg.resolve.public` (013, public-vs-local under DIFFERENT
  roots) and `pkg.resolve`'s independent-.bni/impl-roots facet (012) can't be
  exercised — both tests only show "resolves under one root". Soften their
  comments to not overclaim; the multi-root facets need a harness extension (a
  second `--prepend` root) — note in Annex C as untested.
- **Vacuity to tighten:** 050 (`pkg.identity`) asserts values, not type-
  distinctness — the distinctness is actually pinned by 051's cross-pkg-assign
  reject; re-scope 050's comment. 091 (`pkg.extern` var) only reads once — make
  var-ness load-bearing (mutate via a setter, observe). 090 extern-func is the
  same shape as a normal exported func (inherent).
- **Missing coverage:** `pkg.bni.consistency` only tests return/var-type
  mismatch (033/034) — add param-type + param-count + result-count mismatch.
  `pkg.bni` (032) omits the opaque-type and interface/impl .bni decl kinds.
  `pkg.ccall` (092) has no C-ABI-passability reject test (§16.9). `pkg.clause`
  (010) and `pkg.import` (001) lack negative tests (package-must-be-a-string-
  literal; no block-scoped import).

### Spec Ch.16 (Packages) — build-constraint group needs rework + a possible gap — 2026-06-19
Ch.16 landed at **21/22 rules** (`spec/16-packages/`, binate `f7ed4eb4`):
imports / bni / identity / extern groups are green (compiler/VM/gen1/gen2/
native_aa64). The **build-constraint group** (the `#[build(EXPR)]` rules) was
authored by a fan-out agent on a wrong "gating-active by default + decl-level
gating + predicate-validation-errors" assumption; 8 of its tests failed and were
removed. The real mechanism (per `conformance/737_build_import_select`,
`747_err_build_bni_dropped`) gates whole FILES (via the package clause) and
IMPORTS by arch with `#[build(is(arch, …))]`, not individual decls. **Follow-up
(focused):** re-author the build-constraint tests on the real mechanism, which
restores the lone GAP **`pkg.build.errors`** (the Constraint: a false constraint
on a *required* element is an error). Surviving build tests: `070_annotation_
namespace`, `071_annotation_degenerate`, `072_err_annotation_no_stack`.
  - **Possible real gap to confirm during that rework:** the agent's
    `#[build(<unknown-predicate>)]` and `#[build]` with an unknown annotation
    name **compiled and ran** (printed `0`) instead of erroring — `pkg.build.errors`
    / `pkg.annotation.namespace` say these should be rejected. Either the tests
    were malformed (wrong gating context, so the annotation was never validated)
    or build-constraint validation doesn't fire — determine which.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

## Codegen & backend (non-func-value)

### Big-endian CODEGEN — deferred (no BE target exists yet) — 🟡 DEFERRED
The Ch.7.13 layout follow-ups (`type.layout.funcval-order-hardening` + the
`type.layout.byte-order` decision / `TargetInfo.BigEndian` field + little-endian-only
assert) are ✅ DONE & LANDED — see [claude-todo-done.md](claude-todo-done.md). What
remains: actual big-endian byte-EMISSION (object writers, `ir.DataGlobal` int terms,
`bit_cast` / the representation builtins) for a future big-endian / cross-endian
target. `SetTarget` currently `panic`s on a big-endian target, so there is no
silent-wrong-code risk meanwhile; do this when such a target is actually needed.

### DWARF debug info — finer-grained source positions (open-ended, low priority) — 🟡 OPEN

The DWARF foundation + full type coverage are done (archived in [claude-todo-done.md](claude-todo-done.md):
`-g`, DICompileUnit/DIFile/DISubprogram, per-function DISubroutineType, DILocalVariable for
locals + params, and DIBasicType/DICompositeType/DIDerivedType covering scalars, pointers,
structs, slices, managed-slices, interface-values, function-values, arrays, named typedefs).
The one remaining, open-ended piece:
- Thread source positions through more IR-gen sites (statements, assignments, calls) for
  finer-grained `DILocation` — today only `genExpr` threads `.Line`; most emission sites rely
  on coarse statement-line backfill. No columns.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).

### Static-managed sentinel — deferred follow-ups (optimizations, not correctness) — 🟢 LOW
Follow-ups split out of the (now-done) static-managed sentinel landing:
- **String-literal null-backing unification**: can the string-literal
  `backing_refptr = null` immortality trick (`emit.bn`) be unified under the
  negative-refcount sentinel? Representation can plausibly unify; the nil-check
  itself can't be dropped (it guards genuinely-nil `@` values). Repr cleanup.
- **ClosureRec-as-sentinel**: the VM's shared per-callee non-capturing-`@func`
  `ClosureRec` (`vm_exec_funcref.bn`) is a static, never-freed managed object.
  The premature-free CRITICAL was already fixed symmetrically (conformance 528);
  making the shared `ClosureRec` an immortal sentinel would remove per-instance
  refcount churn on a shared singleton. Optimization, not a correctness gap.

## Performance (double-VM `*-int-int` runtime)

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### pkg/asm/aarch64 slow under `builder-comp-int-int` (perf)
- **Symptom**: under `builder-comp-int-int`, the
  `pkg/asm/aarch64` test package alone is slow enough to time
  out its CI shard at the 30-min cap. Other packages in the
  same mode finish comfortably.
- **Mitigation in tree**: skipped via the whole-package skip
  mechanism `scripts/unittest/pkg-binate-asm-aarch64.skip-pkg.builder-comp-int-int`
  (2026-06-10 — migrated from the old `.xfail`; slowness is a skip,
  not an expected failure). Coverage is preserved by `builder-comp`,
  `builder-comp-int`, `builder-comp-comp*` and the native_aa64 / arm32
  modes — this is purely a double-interp pacing issue. See the
  "int-int slow-package skips" entry below in this group.
- **Hypothesis**: same shape as the codegen `TestEmitDebug*`
  entry above — many small CharBuf / refcount / bounds-check
  operations per emitted instruction, each paying 2× bytecode-
  dispatch overhead under VM-on-VM. The aarch64 assembler is
  string-heavy (encoding tables, mnemonic dispatch). Hasn't
  been profiled.
- **Next step**: profile one `pkg/asm/aarch64` test under
  `builder-comp-int-int` to confirm the hypothesis and identify
  the actual hot path before guessing at fixes. See the codegen
  entry above for the lesson on guessing-without-profiling.
- **Not blocking anything**; mitigation in tree.

### int-int slow-package skips — re-add after optimizing (or decide double-VM coverage isn't worth it) — FILED 2026-06-10
- **Context**: `builder-comp-int-int` (double-VM, VM-interpreting-VM) was "globally broken — every cell SIGSEGV'd" until `c997cf2e` (2026-06-09) made cells actually run. Now-healthy, the lane runs ~120+ min of work and was timing out its CI shards. Bumping unit sharding 4→8 (binate `e40fe3a0`) helped the light half but **4 of 8 shards still timed out at the 30-min cap, each completing ≤1 package** — i.e. a handful of packages each take **>~24 min (or hang) under double-VM**, which sharding can't fix (a single package can't be split across shards).
- **New mechanism (not xfail)**: added a whole-package skip — `scripts/unittest/<pkg-key>.skip-pkg.<mode>` (run.sh). Distinct from `.xfail` (asserts the package FAILS; XPASS-errors if it ever passes) and from `.skip` (drops individual tests but still runs the package). `.skip-pkg` omits the whole package from a mode because it's too slow there; it is NOT a failure (the tests pass — they're just not run in this lane). Counted as `pkg-skipped` in the summary.
- **Skipped under `builder-comp-int-int`**: round 1 (2026-06-10) — `pkg/binate/codegen` (its `TestEmitDebug` per-test `.skip` was insufficient), `pkg/binate/ir`, `pkg/binate/types`, `pkg/std/math/big`, `pkg/binate/asm/aarch64` (migrated from `.xfail`); these took 6 of 8 shards green. Round 2 (2026-06-10) — added `pkg/binate/vm` itself (CI showed it was the last timed-out shard's >24-min offender). The set was found empirically (heuristic + iterating on which shard still timed out), since the timed-out shards never log the offender's time.
- **Re-add work (the "separately" part)**: for each skipped package, either (a) profile + optimize its double-VM runtime so it fits a shard, or (b) make the explicit call that the double-VM lane adds no coverage over single-VM (`-int`) for that package (strong for the compiler-side ones — codegen/ir/types/asm test the COMPILER; `-int` already runs their tests through the VM; double-VM is the same logic + an extra dispatch layer). `pkg/binate/vm` is the one whose lost double-VM coverage is most arguable — its logic is still covered by `builder-comp-int` / `-comp-int` (single VM), and the lane's unique value is exercised by every OTHER package; re-adding it likely wants per-test `.skip` of its slowest tests rather than the whole package. When re-adding `codegen`, its `TestEmitDebug` per-test `.skip` still applies.
- **Separately unmasked**: `pkg/std/os` (landed `3ca36c82`) fails `vm/lower: unhandled IR opcode c_call` on ALL three VM-leg unit modes — libc-backed (native-only), same category as the `rt`/`bootstrap` xfails. NOT a slow-skip case (it genuinely FAILS in the VM), so it's `.xfail`'d (not `.skip-pkg`'d) for `builder-comp-int` / `-comp-int` / `-int-int`, matching that convention. My skips merely unmasked it (the shard used to time out before reaching it); it was already reding `builder-comp-int` independently.
- **Not a release blocker** (int-int non-blocking per `release-process.md`; was red at `bnc-0.0.7` too). Tracked here so the skips don't become permanent silent coverage loss.
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate modes, not this. NOTE: `native_x64` was NOT "WIP" — it was broken by an ELF PC32 reloc bug, fixed 2026-06-14 `dd74c91e`; that native_x64 ELF PC32 reloc bug is fixed and archived in claude-todo-done.md.)

## Testing: harness, runners & conformance coverage

### Conformance harness: `pkg0.testing` `--test`-only rules are not conformance-testable

1. **GAP (harness limitation, not a defect) — `pkg0.testing.testfunc` + `pkg0.testing.run` are not
   conformance-testable.** Both require the `--test` discovery/execution runner (`cmd/bnc --test` /
   `cmd/bni --test`); `conformance/run.sh` only runs ordinary programs (no `--test` plumbing). They
   are exercised by the unit-test suite, not conformance. Closing them would need a test-runner mode
   added to the harness. Left as documented coverage gaps (Ch.20 is 18/20). Candidate for an
   `untestable`/`framework` reclassification in `extract-rule-ids.py` (a denominator decision).

### Better test-mode/target annotation than `.xfail` (unit + conformance)
- We lean on `.xfail.<mode>` files to mark tests that can't run in a
  given configuration (e.g. `pkg-builtins-rt.xfail.builder-comp-int*`
  because rt is native-only in the VM; the `__c_call` conformance tests
  498/500/527/530 xfailed in every VM-leg mode). But "expected to FAIL"
  is the wrong semantics for "not APPLICABLE here" — these tests are
  *bnc-only* / *vm-only* / *target-specific* by nature, not regressions.
- **Want**: a first-class annotation (in the test source or a manifest)
  declaring a test's applicable modes/targets — `bnc-only`, `vm-only`,
  per-backend, per-target — so the runner *skips* inapplicable configs
  cleanly and reserves `xfail` for genuine known-failures. Would also
  let `__c_call` tests declare "compiled-only" honestly instead of a
  fan of per-mode xfail files.
- Surfaced 2026-06-03 by the drop-libc / native-only-rt work.

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### MINOR (e2e / BUILDER-lag cleanup) — drop the gen1 build in e2e/stat-values.sh after the next BUILDER bump (2026-06-20) — 🔴 OPEN

`e2e/stat-values.sh` builds gen1 from the tree (`scripts/build-bnc.sh`) and compiles its os.Stat probe through gen1, instead of the simpler `$BUILDER … cmd/bnc -- …` form the other e2e scripts use. Reason: os.Stat depends on the `.bni` free-func/method fix (`796effc7`) and the wholesale-os-injection work, which postdate `BUILDER_VERSION` (bnc-0.0.9) — the pinned BUILDER can't compile os yet. Once BUILDER is bumped past those, revert `e2e/stat-values.sh` to the plain `$BUILDER … cmd/bnc -- …` pattern (drops the ~1-min gen1 build per e2e run).

### Stdlib conformance suite — optional follow-ups — 🟢 LOW (2026-06-20)

The suite is built and every injected stdlib package has cross-mode coverage
(moved to claude-todo-done.md). Two optional cleanups remain:
- Fold the ~8 ad-hoc stdlib-importing tests in the MAIN conformance set
  (`577_std_errors`, `855_std_time`, `662_errors_is`, `526/528/535_strconv`,
  `663_io_iseof`, `726_cross_pkg_iface_impl`) into `conformance/stdlib/*` (and
  drop their `conformance-imports.whitelist` entries).
- Remove the now-redundant `os_test.bn` `TestErrorIfaceUpcast` (covered by
  `conformance/stdlib/errors/001`; only runs under `builder-comp` now), or keep
  it as a native-only smoke.

### Stale-xfail sweep — residuals (the cross-mode CONFORMANCE sweep is done) — 🟡 OPEN
The big stale-xfail sweep — all 10 modes via the `conformance-xpass.yml` CI workflow;
121 stale conformance markers + 8 VM-mode unittest markers removed; per-mode detail +
methodology — is ✅ DONE; see [claude-todo-done.md](claude-todo-done.md). Two residuals:
- **Cross-mode UNITTEST xfails (17)** — UNSWEPT. The unittest `--check-xpass` (binate
  `ddc624d2`) exists but isn't wired into CI, so the XPASS workflow is conformance-only;
  the 16 arm32-baremetal + 1 arm32-linux unittest xfails need qemu. Sweep by hand, or
  wire unittest `--check-xpass` into CI.
- **`value-struct-large` on `native_x64`** — *not* xfailed there yet crashes (empty
  output) when run; a real missing-xfail or native_x64 bug, surfaced (then masked by a
  substring collision) during the sweep. Worth a look now that `run.sh --exact` no
  longer pulls it into the `value-struct` filter.

### Plan-3 adversarial-review follow-ups (test-hygiene + coverage gaps from `cc2ddcc4` / `997c4c04` / `0c707e1f`) — 2026-06-08
Non-wrong-code items from the adversarial review of the plan-cr2-3 work; each is small. (The live wrong-code findings are the OP_CAST/iface-arg CRITICAL and the float-multi-return MAJOR (both fixed & archived in claude-todo-done.md).)
- **Weak / over-claimed Defect-6 pin**: the addr-aggregate `global` cells (`997c4c04`) + their generator docstring/README claim to pin "2-word sizing / mis-sized-to-one-word drops a word" — but store+load are width-consistent so the cell is INVARIANT to allocation size (it pins materialization + `__init`-store + read-back wiring, NOT sizing). Fix the docstring (`gen-addr-aggregate-matrix.py:96-104`) / README / commit framing to match. Also Defect 6 closed using only the two shapes that typecheck; readonly-wrapped + named-over-aggregate + raw `*func()` + uninitialized-nil global companions (the Class-A materialization risk in `plan-code-red-2.md`) were left out — record as an explicit deferral (invoking them is blocked upstream at the call typechecker).
- **Coverage gaps**: aa64 per-field iface-multi-return collect (`aarch64_iface.bn:204-228`, the exact loop that dropped sub-word fields) has NO unit test (only conformance on aa64); x64 `collectMultiReturnTuple`-for-iface has no unit test for the IFACE op; an aggregate-component iface multi-return tuple (`(Pair,int)`) is uncovered; the iface-method-arg-with-global position is covered by neither a unit test nor 551/573 (see the CRITICAL entry).
- **Latent fragility (nit)**: `pkg/binate/ir/gen_call.bn` computes `resultTyp` generically and hands it to `EmitCallHandle`/`EmitCallIndirect` (magic-name dispatch) with no structural guard that it isn't a multi-return struct — add a cheap assert so the "these ops never carry a multi-return" invariant is enforced in code, not convention.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (6 reviewers → adversarial verify → completeness critic; 21/23 findings confirmed).

The code-red conformance-matrix family (`conformance/matrix/`, see
`plan-code-red.md` §7) has four members realized: `refcount` (Class 1),
`scalar` (Class 5), `abi` (Class 4), `const` (named-constant invariant). These
are the remaining matrix-shaped classes not yet built as their own matrix —
candidates for after the loose-axis finish (const-expr folding + ABI
`handle`/`__c_call` shapes).

### (b2) Lifecycle matrix — Class 6 (`@Iface` / `@[]@I`) + Class 7 (captured-`@func` over-release) — PARTLY ADDRESSED 2026-06-05 (plan-cr-p2-2 step 5)
- **Status**: the existing `conformance/matrix/refcount` form × type grid already
  covers Class 6's construction/consumption shapes (the copy-sites are now uniform
  after the `emitStoreManagedSlot` consolidation), and `604`/`605` add lifecycle-
  DEPTH balance (a value chained through param/store/pass/return/bind/invoke) for
  captured-`@func` and cast-from-impl `@Iface`, green in builder-comp/-int/-comp/
  native-aa64. REMAINING: a true single-program **Class 7 native↔VM trampoline**
  balance test is not expressible in the single-mode conformance harness (each
  test runs in one mode) — needs a cross-mode harness; left as a follow-up.
- **Why a matrix**: Class 6 (`@Iface`/`@[]@I` first-class lifecycle) and Class 7
  (native call-a-captured-`@func` over-release via the VM trampoline) are
  lifecycle-completeness classes. Axes would be `managed-kind (@Iface / @[]@I /
  captured-@func) × construction (make / literal / cast-from-impl / capture) ×
  consumption (call-method / index / range / pass / return / discard) ×
  backend`, with a refcount-balance assertion (mortal source).
- **Status**: the refcount matrix already covers `@Iface`/`@func` as value-types
  across assignment-forms, so this would EXTEND rather than start fresh — the
  new axis is construction × consumption depth (esp. the native↔VM trampoline
  path for Class 7, which the refcount matrix does not exercise).
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family,
  `@[]@I` literal element leak); a matrix would close the long tail.

### (b3) Class 3 / Class 8 — point-bugs, NOT matrices
- Class 3 (cross-package / interface-name type-resolution ordering → `i8*`
  fallback) and Class 8 (multi-package loader resolution at int-int depth) are
  one-off ordering/loader bugs, not systematic products. Track them as
  individual regression tests under `conformance/regressions/` + filed bugs, not
  as a matrix.

### (b4) Differential harness v3 — port `gen-diff-scalar.py` to Binate (dogfood) + flavor B — NOT STARTED
- **Context**: the property-based differential value-correctness harness
  (`conformance/matrix/scalar-diff`, oracle = spec) is realized through v2 —
  shifts, conversions, arithmetic, comparisons, bitwise; 123 cells / 5415
  tuples; generator `conformance/gen-diff-scalar.py` (Python). See
  `plan-differential-testing.md` (phasing item 3) for the full design.
- **v3 scope** (the remaining phase):
  1. **Port the generator to Binate** — rewrite `gen-diff-scalar.py` as a `.bn`
     program so the harness dogfoods the language on a real codegen-shaped task
     (LCG, two's-complement oracle, bit-pattern formatting). Keep the emitted
     cells byte-identical so the existing `.expected`/`.xfail` set and
     `--check` idempotence carry over unchanged.
  2. **Flavor B (optional, for the highest-volume ops)** — one self-checking
     `.bn` per op that loops an embedded `(inputs, expected)` table and prints
     `mismatch i: got… want…`, denser than the current static-cell flavor A and
     debuggable on failure (flavor A shows *which* tuple, not the wrong value).
     Decide per op once flavor A shows which need the volume.
  3. **Sample-size knob** — a fixed, seeded count parameter so coverage can be
     dialed up without touching the generator logic.
- **Why**: dogfooding is the highest-leverage *process* check (the OOM, the
  `@func`-dtor crash, the shift bug all first surfaced by compiling real Binate
  programs); porting the generator turns the harness itself into one more such
  program. Not urgent — v1/v2 already give the value coverage; v3 is the
  dogfood + debuggability upgrade.

## Standard library & libraries

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Expand `pkg/slices` beyond `Append` — opportunistic
- `pkg/slices.Append[T]` is the only generic helper today.  Natural
  additions when call sites demand them (don't add speculatively):
  - `Concat[T](a, b) @[]T` — for the managed-slice + managed-slice
    shape.  `bootstrap.Concat` covers the char-slice case but is
    raw-slice-typed.
  - `Filter[T, P]` / `Map[T, U]` — block on closures or func-value
    params; only worth it once those constraints land properly.
  - `RemoveLast[T](s) @[]T` — `popLoading`-style pattern (rebuild
    minus last occurrence) repeats per element type.
  - Don't pre-add a kitchen-sink set — let the first 2-3 call
    sites pull each helper in.
- **Survey 2026-05-28** of the BUILDER-compilable tree: none of the
  above clears the "2-3+ same-shape sites" bar at the moment.
  Concrete numbers found:
    * `Concat[T]` over two managed slices: 0 sites; the only
      `Concat` callers all funnel through char-specialised
      `bootstrap.Concat`.
    * `Contains[T]`: 4 candidate sites (`containsTypePtr` /
      `containsName` / `containsPkgName` / `containsStr`) but each
      uses a different equality (Identical / charEq / streq), so
      collapsing them needs func-value comparators or method-based
      equality — gap.
    * `Reverse[T]`: 1 site (loader `popLoading`).
    * `RemoveLast` / `RemoveByValue[T]`: 1 site (also loader
      `popLoading`, but it's "rebuild minus *streq match*", which
      is `RemoveWhere` shape — not a pure index/value remove).
    * `Copy[T]` one-liner: 2 sites; most slice-copies in the tree
      are inlined in larger functions.
  So no new helper to add right now without going speculative.
- **The real next pkg/slices step** the survey surfaced: 168
  `slices.Append[T]` calls live inside `for` loops, i.e. O(n²)
  builds.  Folding those into a growable container with amortised
  O(1) append (a `Vector[T]` / `Builder[T]` shape with capacity
  tracking) is a substantive design, not a quick add — file it for
  later when the surface is being intentionally pulled into a
  proper stdlib effort.

### `os` errors carry only the op, not the failing path (P3)
`pkg/std/os` `failErrno(op)` renders e.g. `"open: not found"`, but
plan-std-error-hierarchy.md §7 specifies context `(path, op)` —
`"open /etc/foo: not found"`. The path is available in `OpenFile`'s `name`
param (Create/Open delegate to it); `read`/`write`/`seek` operate on an fd and
have no path, so op-only is correct there. Add the failing path to the open
family's error context (e.g. a path-aware wrapper, or `failErrno(op, path)`).
Deferred 2026-06-11 (user: op-only acceptable for now) — low impact (message
richness, not classification). Tests: extend the `TestOpen*Classified` cases
to assert the path appears in the rendered message.

## Package management & search paths

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package path conventions, decided in [`pkg-layout-spec.md`](pkg-layout-spec.md).
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. The path conventions are already ratified in
  [`pkg-layout-spec.md`](pkg-layout-spec.md); this sketch builds on them
  (esp. its "Package manager interaction" section).

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

## REPL

### REPL: remove process-global session state (multi-session blocker)
- **Now owned by [`plan-embeddable-vm.md`](plan-embeddable-vm.md)** (scoped
  2026-06-16): the `ir` half below is increments 4–5 of that plan, which
  covers the full compiler/VM global inventory, not just the REPL's two.
  This entry's `ir/gen.bn` line numbers are stale as of 2026-06-02; see the
  plan for verified ones.
- **What**: the REPL engine keeps per-session state in PROCESS-GLOBAL
  package vars instead of threading it through the session. v1 of the
  embeddable refactor (above) lifts the cmd/bni-local ones into
  `@ReplSession` but deliberately keeps **single live session per
  process**, leaving two `pkg/binate/ir` globals in place.
- **The globals**:
  - cmd/bni-local (lifted into `@ReplSession` by Stage 1 of the
    refactor): `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
    (`cmd/bni/repl_import.bn:24-41`) and `replInitCounter`
    (`cmd/bni/repl_decl.bn:411`).
  - `pkg/binate/ir` process-globals (NOT lifted in v1, the real
    multi-session blocker): `currentChecker` (`pkg/binate/ir/gen.bn:148`,
    set via `ir.SetChecker`) and the import alias map
    `importAliasNames`/`importAliasPaths` (`gen.bn:107/110`), with
    `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
    (`repl_import.bn:101/146`).
- **Why it matters**: single re-entrant session is unaffected (the ir
  globals are set once and save/restored inside import turns as today).
  But >1 concurrent embedded session in one process needs those globals
  session-scoped (or save/restored at every `Step` boundary) — a
  separate, larger change that must land BEFORE `pkg/binate/repl` can
  honestly claim multi-session support.
- **Guidance (applies now)**: **do not add any new REPL globals.** New
  per-session state goes through `@ReplSession`. Adding a global "to keep
  a signature stable" (the exact shortcut that created the current ones,
  per `repl_import.bn:18-20`) is what this entry exists to stop.
- **When**: only if multi-session embedding becomes a goal. Not needed
  for wasm B1 (one worker = one session).

### REPL — Tier-4 follow-ups + pretty-printer (all five tiers landed) — 🟡 OPEN (low priority)
All five REPL tiers are landed (archived in [claude-todo-done.md](claude-todo-done.md): Tier 1–2 eval +
redefinition, Tier 3 forward refs incl. pending types/vars/consts + cycle detection, Tier 4 replace +
shadow for funcs & methods, Tier 5 mid-session imports `78685ac3`). Residual:
- **Tier 4**: refcount-aware shadow warning (today fires unconditionally); forced-shadow escape hatch (syntax TBD per `claude-notes.md`).
- **Pretty-printer** (`pkg/replprint`) — deferred until interfaces land (`bootstrap.println` is a temporary hack; don't entrench it).

## ARM32 bare-metal target

### native arm32 backend — IN PROGRESS (live tracker: [plan-native-arm32.md](plan-native-arm32.md))

The `pkg/binate/native/arm32` backend (P0–P3.3 in progress) is tracked in detail
in `plan-native-arm32.md`; that doc is authoritative for phase status, landed
commits, and deferred shapes. Deferrals below are all **fail-loud** (a shape the
backend doesn't implement emits a clean COMPILE_ERROR, never silent wrong-code).

- **small (SizeOf ≤ InternalSretBytes = 4) in-register aggregate return —
  deferred (P4).** A struct ≤ 4 bytes (e.g. `struct{x int32}`) is returned BY
  VALUE in R0 on AAPCS32, not via sret (P3.3's single-aggregate-sret covers only
  the > 4-byte case). The in-register pack (callee) + collection (caller) are not
  implemented; both sides fail LOUDLY. The x64 backend packs this size class via
  `emitAggregateReturnPack` / the `!bigRet` RAX(+RDX) store — the arm32 analogue
  (LDR/STR the ≤ 1-word value into/out of R0) is the P4 port. Covered by
  `conformance/966_return_small_struct` (xfail'd for
  `builder-comp_native_arm32_baremetal`; passes on every backend that implements
  it) and unit tests `TestReturnSmallAggregateSetsError` /
  `TestCallSmallAggregateReturnSetsError`. Root cause of the fail-loud: the sret
  predicates use a strict `SizeOf > InternalSretBytes`, leaving the `≤ 4` class as
  a non-sret in-register shape that P3.3 doesn't lower.
- **multi-return (in-register tuple collection AND > register-budget sret) —
  deferred (P4).** Fail-loud today; not yet xfail'd per-test (they sit among the
  native-arm32 conformance failures, e.g. `401_return_many_scalars`).
- **soft-float (P5) / VFP hard-float + arm32-linux (P6) / CI wiring (P7)** — see
  the plan doc.

#### MAJOR — three silent runtime miscompiles on native-arm32-baremetal (found by the P4 reconnaissance, 2026-07-02)

These compile CLEAN through the native arm32 backend and then HANG at runtime
under QEMU (identifiable in a full run by their `[10s]` timeout vs `[0s]/[1s]` for
fail-loud). They violate the never-silently-miscompile invariant. Scope is
**native-arm32-only** (that mode is not in CI), so severity is MAJOR not CRITICAL,
but they are live red on `main` and were UNTRACKED (no xfail, no todo) until now.
Per the Bug Discovery Protocol each needs an xfail marker + fix:

- **`conformance/matrix/abi/struct-param/five-u8`** — a 5-byte (2-word) by-value
  struct param hangs; the `two-int`/`three-int`/`three-u32`/`int-u8`/`u16-int`
  siblings pass. **Root cause is the plan's already-documented MAJOR latent-P3
  gap** (plan-native-arm32.md "NEW, MAJOR (latent, P3)"): codegen coerces a
  ≤16-byte aggregate param to `[N x i64]` (`aggCoerceLLTy`, hardcoded i64), which
  clang lowers as 8-aligned i64 register PAIRS, but the native AAPCS32
  word-packing (`common_callconv.bn` `argRegWordsStackWords`) doesn't reproduce
  that pair-alignment for a 4-aligned struct starting on an odd register. The plan
  said to fix this "before P3/P4 passes such args" — the backend now exists and the
  hang is that validation failing. Fix: target-aware `[N x i32]` coercion OR native
  i64-pair even-register modeling — a SHARED codegen/callconv change, so re-verify
  LP64 byte-identity (x64/aa64) + pin against `clang -target arm-none-eabi
  -mfloat-abi=soft`. The SAME reconciliation gates any func-value shim / iface
  dispatch / multi-return in-register path that passes or returns an aggregate.
- **`conformance/599_addr_of_slice_elem`** — `make_slice` + `&s[i]` hangs; the
  test's comment references a prior shared-IR address-of miscompile fixed for other
  backends, which arm32 still mishandles (likely a localized `arm32_emit.bn`
  `emitGetElemPtr` / address-of bug).
- **`conformance/877_aggregate_abi_xpkg`** — cross-package 64-bit aggregate ABI;
  prints line 1 then hangs.

#### P3 GAP (fail-loud, not silent) — OP_MAKE / OP_BOX unimplemented — ✅ FIXED & LANDED (`b33eb9d6`, 2026-07-02)

`arm32_dispatch.bn` had no `OP_MAKE` / `OP_BOX` case (only `OP_MAKE_SLICE`), so
`make(T)` / `box(v)` hit the generic "unimplemented IR op make" fail-loud tail —
the **dominant** native-arm32 failure bucket (~41% of a 195-test sample: make×74,
box×6) and the blocker for measuring any P4 progress (most iface/func-value tests
allocate a managed value, so they failed on `make` before reaching P4 code).
**Resolution:** ported `emitMake` (`rt.Alloc(SizeOf)`) + `emitBox` (`rt.Box`, three
source shapes) from aa64 with the ILP32 word size. Native-arm32-baremetal
conformance jumped **1573 → 1754 passed** (+181), no regression, no XPASS.
Adversarial-reviewed (landable; two minor findings addressed — scalar box boxes
directly from the spill slot; unit tests for all three box branches).

#### MINOR (cross-backend diagnostics) — `iropcode.OpName` missing `OP_CONST_FLOAT`

`pkg/binate/iropcode/opcodes.bn`'s `OpName` switch lacks an `OP_CONST_FLOAT` case,
so float-const failures mislabel as "unimplemented IR op unknown" across all
backends/tools. 1-line fix (`case OP_CONST_FLOAT: return "const_float"`); pure
diagnostics, no pass/fail change.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

## Opportunistic code cleanups

### Use interfaces more (opportunistic)
- **Constraint**: now bounded by `BUILDER_VERSION`-pinned bnc
  rather than the historical bootstrap subset — cmd/bnc no longer
  has to be bootstrap-runnable now that boot mode is gone (binate
  `c1be3cc`, 2026-05-21).  bnc-0.0.1 (the current BUILDER) supports
  interfaces, so anything in cmd/bnc's dep tree is fair game too.
  Generics are NOT in bnc-0.0.1, but interfaces are.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
  pkg/ast's tagged-union nodes (DECL_*, EXPR_*, STMT_*, TEXPR_*
  Kind enums + switch-on-Kind in pkg/{parser,types,ir,codegen,
  loader}) is the biggest single target but also the longest
  refactor — touches every layer.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).
- **Recon finding (2026-05-26)**: there is NO clean *small*
  retrofit target.  The candidates above split into two
  unappealing buckets: (a) enum→value lookups (reloc maps,
  opName, the emitInstr op dispatch) where `switch` is genuinely
  the right tool and an interface would mean manufacturing one
  empty marker type per enum value — pure ceremony; and (b)
  monolithic tagged unions (`ast.Stmt`/`Decl`, `ir.Instr`) where
  a real interface means splitting a struct that touches every
  layer.  So "use interfaces more" here is a deliberate design
  choice, not opportunistic cleanup.
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`).  The genuinely-valuable use
  found: `cmd/bnc/compile.bn`'s `Backend` interface
  (`compileModule`) with `llvmBackend` / `nativeBackend` impls,
  dispatched via `compileModuleVia`.  This collapsed the
  duplicated driver flow — `compileMainNative` is gone, `main()`
  picks the backend and the LLVM/native paths are unified.
  pkg/native also got an internal arch `Backend`
  (arm64/amd64).  These are the first non-synthetic interface
  users beyond pkg/std's `Stringer`.  NOTE: interface values
  must be constructed from locals, not package globals — `&global`
  iface construction was a codegen bug (now fixed, see
  conformance/495).

### Use `@[]@[]char{...}` composite literals (opportunistic)
- **Constraint**: previously forbidden because bootstrap didn't
  support managed-slice-of-managed-slice composite literals; now
  unlocked everywhere (bnc-0.0.1 supports them).  Mirrors the
  unconstraint situation for `cmd/bnlint`'s tests, which already
  use this shape.
- **Pattern to replace**: a known-fixed-length run of
  `args = appendCharSlice(args, "foo"); args = appendCharSlice(args, "bar"); ...`
  → `var args @[]@[]char = @[]@[]char{"foo", "bar", ...}`.  Same
  shape for `appendRawCharSlice` (since string literals are
  already `*[]const char`).  When the run mixes constants with
  computed values, leave it alone — the literal form only helps
  for known-static sets.
- **Candidates**: argv construction in build scripts (e.g.
  `cmd/bnc/{main,test,compile}.bn` clang-args setup), test
  scaffolding (anywhere a test builds a known `@[]@[]char`
  fixture), and short fixed sets of import paths.
- **Why bother**: cuts line count, removes a runtime O(n²)
  rebuild pattern (each `appendCharSlice` allocates a new
  slice + copies), and matches the language's expressive
  default instead of the bootstrap workaround.

### Use function values to collapse explicit dispatch shims (opportunistic)
- **Constraint**: function values are unlocked now that
  cmd/bnc is no longer bootstrap-bound; bnc-0.0.1 has the
  function-value machinery (see plan-function-values-phase-3
  in `claude-todo-done.md`).
- **Pattern to look for**: places where we route through a
  `kind` int + a per-kind dispatch table, when the data flow
  would be clearer as "the caller hands us the function it
  wants invoked".  Candidates need a closer look before they're
  fully scoped — function-value adoption isn't always a win
  (each call adds an indirect-call overhead), so this is
  selectively-opportunistic, not blanket.
- **How to land**: TBD; needs concrete site survey.

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.
- **Landed (2026-05-25/26)**: the big per-op dispatchers are
  converted — `pkg/vm/vm_exec_pure.bn` + `vm_exec_helpers.bn`
  (binate `b4456ab`, `e4e7d29`), `pkg/codegen/emit_instr.bn`
  (`2d6d0f7`), `pkg/native/arm64/arm64_dispatch.bn` (`3756acc`).
  Where a chain mixes equality cases with op-RANGE checks
  (emit_instr's OP_ADD..OP_SHR / OP_EQ..OP_GE; arm64_dispatch's
  emitCompare/emitBinop/emitUnop delegates), the range arms stay
  as guards alongside the switch.  `ir_ops.bn`'s opName was
  already a switch — nothing to do there.  This work flushed out
  a CRITICAL case-scope miscompile (managed local in a `case`
  body), since fixed (`4306197`; archived in claude-todo-done.md).
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar
