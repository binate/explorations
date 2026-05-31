# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## CRITICAL

### ~~codegen omits `byval` on >16-byte struct params — cross-pkg ABI miscompile~~ — FIXED 2026-05-30 (binate `f5340fac` + `8ba29d11`)
- **Final fix**: emit a plain `ptr` (NO `byval` attribute) for
  >16-byte aggregate params in pkg/codegen — both arches' LLVM
  lowering then treats it as "pointer in next free GP arg reg",
  uniformly matching the indirect-pointer-pass semantics native
  backends now implement.  The plan doc proposed `ptr byval(<T>)`,
  but verifying empirically showed LLVM's `byval` lowering on
  AArch64 lays the struct on the caller stack (matching the
  SysV-byval shape), not the pointer-in-reg-indirect shape clang
  picks for AAPCS at the frontend.  Plain `ptr` gets the desired
  indirect-pointer-pass on BOTH targets.  Caller-side alloca +
  memcpy lives in the call's preamble (`writeByvalArgPreamble`).
  Native common gained `IndirectLargeAggregates` flag (true for
  AAPCS64 / AAPCS64_Darwin / SysV-AMD64); pkg/native/x64 also
  needed a separate sret-shift fix in `emitCallIfaceMethod` (8ba29d11)
  to place iv.data in RSI when the iface-dispatched callee returns
  via sret.  Tests in common_callconv_test.bn / aarch64_call_test.bn
  / x64_call_test.bn / x64_emit_func_test.bn updated to the new
  shape; conformance 411 + 331 + 337 are the end-to-end pins.
- **Symptom**: cross-package call where the callee is LLVM-compiled
  and the caller is native (or vice-versa) and the signature includes
  a >16-byte struct param by value: callee reads the struct from the
  wrong place, returns wrong answer (or segfaults).  Surfaced as
  conformance failures 331 / 337 / 411 on
  `builder-comp_native_x64_darwin`.  On aa64 the same root cause was
  latent — the native backend *matched* LLVM's non-textbook
  emission (SplitAggregates=true) so aa64 conformance was green, but
  that match was to a non-textbook ABI, not the spec.
- **Discovery**: 2026-05-29, while investigating remaining x64-darwin
  conformance failures after the float-lowering work landed.  Verified
  empirically by compiling a minimal C file with the same struct shape
  via clang `-target x86_64-apple-darwin` and comparing the emitted IR
  + asm to binate's.  GCC would produce identical x86_64 asm (textbook
  SysV — entire >16-byte struct on caller's outgoing-stack).
- **Root cause**: clang emits `ptr byval(%struct.T) align 8` for
  >16-byte struct params; that attribute tells LLVM to lower per the
  target's textbook calling convention (MEMORY-on-stack for SysV,
  indirect-pointer-pass for AAPCS).  Binate's codegen never emits
  `byval` (zero matches across `pkg/codegen/`).  Without `byval`,
  LLVM falls back to IR-level struct-value rules — on x86_64 it
  decomposes the struct into separate i64 args (the `RDX/RCX/R8/R9 +
  stack…` spread we observe); on AAPCS64 it splits across X regs +
  stack.  GCC, clang, and every other C compiler emit `byval`.
  Binate is the outlier; native backends are forced to match the
  outlier convention.
- **Proper fix**: see [`plan-codegen-byval.md`](plan-codegen-byval.md).
  Emit `ptr byval(<T>) align 8` for >16-byte aggregate params in
  declarations, definitions, and all call-site emitters
  (`pkg/codegen/emit_call.bn`, `emit_call_handle.bn`,
  `emit_call_indirect.bn`, plus iface vtable shims).  Update
  `pkg/ir/gen_func.bn`'s function-entry to skip the alloca + store
  for byval params (use the byval pointer directly as the field-
  access base).  Switch aa64 native aggregate-arg path from split-
  passing to indirect-pointer-pass to match the new emission;
  rewrite `common_callconv.bn` AAPCS64 config + the ~5 tests pinning
  the current shape.  X64 native is already textbook-compatible
  (`SplitAggregates=false` matches byval-SysV).  Estimated 600-1000
  LOC across ~15 files, must land atomically (any non-atomic landing
  breaks one arch's conformance mid-flight).
- **Workaround NOT in place**.  The 3 failing conformance tests
  (331, 337, 411) remain failing on x64-darwin.  A band-aid would be
  to flip `SysV.SplitAggregates=true` in `common_callconv.bn` so x64
  matches LLVM's non-byval emission the way aa64 does (~20 LOC).
  That trades textbook correctness for unblocking the conformance
  failures; the root fix is preferable when bandwidth allows.

---

## MAJOR

### ~~bnc: int64 literals under unary-minus silently truncate to i32 on ILP32 targets~~ — FIXED 2026-05-29 (binate `224e7bef`)
- **Final fix**: added `tryFoldOversizedConst` in `pkg/ir/gen_util_literals.bn`, dispatched from `genExprInner`'s EXPR_UNARY / EXPR_BINARY branches.  When the type checker's bignum-fold on the resolved type carries a magnitude that exceeds the target's host-int signed range, emit a single OP_CONST_INT at int64 directly — bypassing the recursive `genExpr` that would emit the leaf literal at TypUntypedInt → intLL() = i32 on the 32-bit target.  Option-2-as-originally-described (gating at EXPR_INT_LIT on the *literal's* own resolved type) didn't fix it — the typed-int context lives on the parent expression's HasLitVal after `check_expr.bn`'s untyped-preserving sign-flip, not on the literal itself.  Pulling the gate up to the parent reaches the same fold values.  No-op on LP64 — `targetIntBits >= 64` short-circuits.  Tests: `TestGenCastNegLitOverflowingHostIntPromotesToInt64` (unit, pins fold-emitted Width=64 under setTarget32 with snapshot/restore), `conformance/507_int64_min_via_unary_minus` (end-to-end round-trip).  Companion fix-up `8981d5bf` locks LP64 around `TestGenUnaryMinusOnInt64Preserves` so its OP_NEG-shape assertion still holds (the fold absorbs OP_NEG on 32-bit targets, which is correct, but breaks that test's invariant).
- **Symptom**: `cast(int64, -9223372036854775807)` evaluates to `1` (not `-9223372036854775807`) under `--target arm32-linux`.  Any int64 literal with magnitude > 2^31 wrapped in unary-minus (or any non-cast typed context that doesn't route through `genIntLitWithHint`) gets truncated to i32 before negation, silently producing wrong values.  No LP64 host effect — `intLL()` returns i64 there, which can hold the full magnitude.
- **Repro** (arm32 LLVM IR for `cast(int64, -9223372036854775807)`):
  ```
  %v3 = add i32 9223372036854775807, 0   ; literal truncated to i32 → -1
  %v4 = sub i32 0, %v3                   ; → 1
  %v5 = sext i32 %v4 to i64              ; → 1
  ```
  For `cast(int64, 9223372036854775807)` (no unary-minus), `genIntLitWithHint` fires and emits `add i64 …, 0` correctly.
- **Discovery**: 2026-05-28, while triaging arm32_linux unit-test failures `TestBignumToIntInt64Min` (pkg/ir), `TestFormatInt64Boundaries` (pkg/bootstrap), `TestWriteInt` (pkg/buf), which all construct int64-min via `cast(int64, -9223372036854775807) - cast(int64, 1)`.  The expression evaluates to `0` on arm32, not int64-min.
- **Root cause**: `genExprInner`'s `EXPR_INT_LIT` branch (pkg/ir/gen_expr.bn:34) unconditionally emits the literal at `types.TypUntypedInt()`.  `TypUntypedInt` has `Width=0`, so `llvmType` falls through to `intLL()` — i64 on LP64, **i32 on `--target arm32-linux`**.  The literal text is widened to int64 by `exprIntLitValue` (via the type checker's bignum-fold), but the LLVM emit type drops back to host int, so the IR-text writer's i32 literal silently wraps.  `genIntLitWithHint` papers over this for the most common case (bare `EXPR_INT_LIT` argument to `cast(T, …)` or `var x T = …`), but it doesn't peek through `EXPR_UNARY`, so `cast(T, -lit)` falls through to the buggy path.
- **Tests covering it**: the three failing unit tests above are the regressions.  A targeted IR-gen test would also help (e.g. `TestGenIntLit2Pow62InInt64Context` asserting the OP_CONST_INT carries Width=64).  No conformance test yet for the unary-minus shape — should add one.
- **Proper fix (chosen, option 2)**: in `genExprInner` `EXPR_INT_LIT`, when the type checker has resolved the literal to a concrete typed-int (`TYP_INT` with `Width > 0`) wider than the host word, emit `EmitConstInt64(v, resolvedTyp)` instead of `EmitConstInt64(v, TypUntypedInt())`.  Closes the structural hole more broadly than the narrower "peek through unary-minus in `genIntLitWithHint`" alternative — also covers binop operands, return values, and any other typed context where a too-wide-for-host-int literal appears without explicit cast/var hint.

### ~~pkg/native/aarch64: float compares use integer CMP instead of FCMP~~ — FIXED 2026-05-29 (binate `21366bfa`)
- **Final fix**: `emitCompare` now gates on `ins.Args[0].Typ.IsFloat()` and routes float operands to a new `emitFloatCompare` helper that emits `FCMP` + `CSET` (= `CSINC Rd, XZR, XZR, invCond`) with ARM ordered-FP condition codes — `EQ`/`MI`/`LS`/`GT`/`GE` mapped per the proposed table.  `OP_NE` uses a two-step `CSET NE` then `CSEL rd, rd, XZR, VC` to zero the result when the operands were unordered.  `TestEvalFloatCmp64`'s "NaN == NaN must be false" now passes; `+0.0 == -0.0` correctly returns true; unit-test `TestInvertFloatCondForOp` pins the structural invariants of the inverse table.
- **Symptom**: `pkg/vm.TestEvalFloatCmp64` fails on `builder-comp_native_aa64-comp_native_aa64` with "NaN == NaN must be false".  Two NaN values constructed the same way (both `0.0/0.0`) have identical bit patterns; integer CMP says they're equal; IEEE / Binate's ordered-fcmp semantics say they're not.  `+0.0 == -0.0` is also wrong by the same mechanism (different bit patterns; IEEE says equal).
- **Discovery**: 2026-05-29, while triaging the residual aa64 failures after the dtor-vt fix landed.
- **Root cause**: `pkg/native/aarch64/aarch64_ops.bn::emitCompare` unconditionally emits `Cmp` (integer compare) for `OP_EQ`/`NE`/`LT`/`LE`/`GT`/`GE` without checking operand type.  `Fcmp` is defined in `pkg/asm/aarch64/aarch64_fp.bn:103` but is not called from anywhere in the native backend.
- **Why MAJOR**: every float compare in any program built via the aa64 native backend has wrong NaN / signed-zero semantics.  In practice most code doesn't intentionally use NaN, but `+0.0 == -0.0` returning false silently miscompiles any code that treats those as equivalent (Go's spec equivalent, IEEE 754 §5.11 mandate).
- **Tests covering it**: `pkg/vm.TestEvalFloatCmp64` is the regression.  No focused unit test on `emitCompare` for the float path (none exist because the path doesn't exist).
- **Proposed fix**: in `emitCompare`, gate on `ins.Args[0].Typ.IsFloat()` (or `ins.Args[1]`) and route to a new `emitFloatCompare` helper that emits `FCMP` then `CSET` (= `CSINC Rd, XZR, XZR, invert(cond)`) with ARM ordered-FP condition codes:
  - `OP_EQ` → cond `EQ` (Z=1; NaN sets Z=0 → false ✓).
  - `OP_LT` → cond `MI` (N=1; NaN sets N=0 → false ✓).
  - `OP_LE` → cond `LS` (C=0 OR Z=1; NaN sets C=1,Z=0 → false ✓).
  - `OP_GT` → cond `GT` (Z=0 AND N==V; NaN sets N=0,V=1 → false ✓).
  - `OP_GE` → cond `GE` (N==V; NaN sets N=0,V=1 → false ✓).
  - `OP_NE` → two-step: `CSET rd, NE`; `CSEL rd, rd, XZR, VC` (gate on ordered).  Ordered NE = `Z=0 AND V=0`, which ARM doesn't expose as a single condition; the two-step lets the second op clear `rd` to 0 when V=1 (unordered).

### ~~pkg/native/common: ParseFloatLitToBits overflows for extreme denormals~~ — FIXED 2026-05-30 (binate `6db081fc`)
- **Final fix**: two-part change to `pkg/native/common/common_float.bn`:
  - `underflowsToZero` guard: conservative log2 over-approximation (`bitLen(mantInt) × 10 + netExp × 33 < -10750`) routes any literal below 2^-1075 directly to 0 — clears `1.0e-330` before `pow10` even runs.
  - `parseLargeNegExpToBits` (new): for `-netExp > 19`, maintains `mantInt` as a 128-bit mantissa (`hi:lo`) with a separately-tracked binary exponent, processing each decimal-place shift as `10 = 5 × 2` — the `×2` goes into `binExp`, the `/5` goes through `div128by5` (bit-by-bit long divide).  Per-step normalization via `shl128` shifts the top set bit back into `hi`'s MSB so 128 bits of precision are maintained throughout (accumulated error stays below 2^-53 after 330 steps).  `bitsFrom128` packs the final `(hi:lo, binExp)` tuple into IEEE 754 double bits — handles normal, denormal (`unbiasedExp` in `[-1074, -1023]`), underflow (`< -1075`), and overflow (`> 1023`) ranges.
  - Test: `TestParseFloatTinyUnderflows` pins the underflow gate; `TestParseFloatLargeNegExp` pins biased exponents for `1.0e-30` and `1.0e-300` within ±1.
- **Result**: `pkg/vm.TestEvalFloatArith64` now passes, completing the aa64 self-host lane: `builder-comp_native_aa64-comp_native_aa64` 34/0 — full sweep green for the first time since this lane was tracked.
- **Symptom**: `pkg/vm.TestEvalFloatArith64` fails on `builder-comp_native_aa64-comp_native_aa64` with "FMUL64 must keep float64 precision".  At runtime `1.0e-300 * 1.0e-30` correctly produces the denormal `1.0e-330`; at compile time the literal `1.0e-330` parses to a garbage bit pattern.  Integer CMP (per the float-compare bug above) then says they're not equal.
- **Discovery**: 2026-05-29, same triage pass.
- **Root cause**: `ParseFloatLitToBits` in `pkg/native/common/common_float.bn:118` handles fractional-exponent literals via `divToDoubleBits(mantInt, pow10(-netExp))`.  `pow10(n)` loops `r = r * 10` `n` times in a `uint64`; for `n` ≥ 20 the multiplication overflows uint64 (`10^20` > 2^64) and `r` wraps to a garbage value.  For `1.0e-330` we hit `pow10(331)` which is wildly overflowed.  `divToDoubleBits(10, garbage)` then computes a quotient whose bit pattern bears no relation to the IEEE 754 denormal that LLVM (or any spec-conforming parser) would produce.
- **Why MAJOR (but lower priority than the compare bug)**: only affects float literals with extreme magnitude.  Most programs don't have these.  Composes with the compare bug (the test would still fail with this fix alone since the runtime FMUL produces a different bit pattern from any non-denormal-aware parser).
- **Tests covering it**: `pkg/vm.TestEvalFloatArith64` — but the test failure surfaces ONLY when the compare path is also fixed; otherwise the compare bug masks this one.
- **Proposed fix**: replace `pow10`+`divToDoubleBits` with a denormal-aware decimal-to-binary algorithm.  Options: (a) `frexp`/`ldexp`-style decomposition of the decimal exponent into a binary exponent + mantissa multiply; (b) a bignum decimal-mantissa path; (c) restrict the loop to never overflow and detect underflow explicitly (when the decimal magnitude is below the float64 minimum denormal ≈ 5e-324, the bit pattern is exactly the smallest denormal or zero, depending on rounding).  Out of scope for the immediate compare-bug fix.

### arm32_baremetal: pkg/native/{aarch64,x64} test binaries overflow `.bss` region
- **Symptom**: under `builder-comp_arm32_baremetal`, `pkg/native/aarch64` and `pkg/native/x64` test binaries fail to link with `ld.lld: error: section '.bss' will not fit in region 'RAM': overflowed by 92420 bytes` (aarch64) and `overflowed by 675956 bytes` (x64).  Other packages link fine — only the native-backend test suites are oversized.
- **Discovery**: 2026-05-29, while triaging the residual arm32 CI failures after the int64-fold + sret + dtor-vt fixes cleared the other clusters.  Pre-existing — failures appear on every completed CI run going back through the session start.
- **Why MAJOR**: blocks the arm32_baremetal CI lane for these two packages.  All other arm32_baremetal packages pass, so the lane's utility (catching arm32-specific regressions in baremetal mode) is partial.  Doesn't affect arm32_linux or LP64 lanes.
- **Tests covering it**: the existing pkg/native/aarch64 / pkg/native/x64 test suites are the regression once a fix lands.  No focused size-budget test.
- **Proposed fix (options)**:
  - (a) Bump the bare-metal RAM region size in the linker script that the `builder-comp_arm32_baremetal` runner uses (cheapest if the underlying QEMU semihosting environment has headroom).
  - (b) Split the pkg/native/aarch64 and pkg/native/x64 test suites into smaller binaries (e.g. one per source file), each fitting in the existing RAM region.  More work but exercises the same coverage.
  - (c) Mark the two packages XFAIL under arm32_baremetal with a documented size-budget rationale.  Loses coverage but unblocks CI.
  Worth a quick check on (a) first — if the RAM region is artificially small, bumping it is the right move.  The x64 binary's 676 KB overflow suggests the issue isn't just one or two large symbols; the suite as a whole is comprehensive.

### ~~macOS aa64-comp_native_aa64: duplicate destructor-vtable symbol across package boundary~~ — FIXED 2026-05-29 (binate `94b75294`)
- **Final fix**: two-part change to ir + native/aa64.  See commit message for the full reasoning.  Short version:
  - **ir**: `gen_dtor_emit.bn` / `gen_copy_emit.bn` pass-3 now mirrors pass-2's cross-package gate.  Consumer-side dtor/copy generation for cross-package struct types is replaced with `declareExternDtor` / `declareExternCopy` (via `funcAlreadyDeclared` dedup), so the consumer-side wrong-named duplicate (`__dtor_pkg/X.T` mangled `bn___dtor_pkg__X__T`) stops being emitted.
  - **native/aa64**: `collectFuncValueRefs` gets a pre-pass that adds every locally-defined IsLinkOnce function to `seen[]` regardless of OP_FUNC_HANDLE references — so the defining TU always emits `__vt`/`__handle`/`__shim`.  `lookupFuncValueTypeAA64` gets an `IsExtern { continue }` gate so consumer TUs skip emitting triplets for cross-package handles (defining TU resolves them at link).
- **Result**: macOS aa64 self-host sweep (builder-comp_native_aa64-comp_native_aa64): 33/1 (was 14 failures).  Remaining failure is pkg/vm's TestEvalFloatArith64 / TestEvalFloatCmp64 — a pre-existing aa64 float-codegen issue unrelated to dtor-vt.
- **LLVM-side unchanged**: clang's `weak_odr` already dedups via `__DATA,__datacoal_nt` + `S_COALESCED` so pkg/codegen doesn't need either piece.

### ~~OLD DIAGNOSIS (kept for reference)~~
- **Symptom**: link failure for every package downstream of `pkg/asm` under the `builder-comp_native_aa64-comp_native_aa64` mode on macOS:
  ```
  duplicate symbol '_bn_pkg__asm____dtor_Assembler__vt' in:
      pkg__asm.o
      pkg__asm__x64.o
  ld: 1 duplicate symbols
  ```
  Cascades into `FAIL: pkg/asm/{x64,macho,parse,arm32,aarch64,elf}`, `pkg/native/{x64,aarch64}`, `pkg/vm`, `cmd/{bnas,bnc}` — every consumer of `pkg/asm.Assembler` re-emits the destructor-vtable symbol.
- **Discovery**: 2026-05-28, while triaging the macOS aa64 CI lane during the int64-fold work (binate `224e7bef`).  Pre-existing — failures appear on every completed CI run going back at least 28 commits (earliest checked: `d88d3520`).  This is an unrelated cluster, not introduced by the int64 work.
- **Root cause (confirmed via local repro)**: `pkg/native/aarch64/aarch64.bn:lookupFuncValueTypeAA64` (the gate that `emitFuncValueVtables` consults to decide whether to emit local `__vt`/`__handle`/`__shim` for an `OP_FUNC_HANDLE` reference) doesn't distinguish between locally-defined functions and `IsExtern` import stubs.  Cross-package dtor handles (`@pkg/asm.Assembler` used as a managed pointer in `pkg/asm/x64`) get registered as extern entries in the consumer's `m.Funcs` (gen_import.bn:NewExternFunc), the lookup finds them by name match, returns a non-nil synthesized sigTyp, and `emitFuncValueVtables` re-emits the vtable in the consumer's `.o` — duplicating what the defining package already emitted.  The matching `aarch64.bn:collectFuncValueRefs` outer loop already excludes `f.IsExtern` for the *contribute-references* direction; the *resolve-targets* lookup just doesn't mirror that gate.
- **Why MAJOR**: blocks the entire macOS aa64 self-host lane.  Doesn't affect LLVM-mode amd64 / arm32 (LLVM emits these as `weak_odr` and clang/Mach-O coalesces those correctly; the native-aa64 backend's `SetWeak` + `N_WEAK_DEF` path doesn't get coalesced by Mach-O the same way).
- **Tests covering it**: every package in the cascade list above counts as a regression test once the symbol-emission is fixed.  A focused unit test on `pkg/native/aarch64` would also help (assert that `lookupFuncValueTypeAA64` returns nil for `IsExtern` entries).
- **Proposed fix — naive version doesn't work**: adding `if f.IsExtern { continue }` at the top of `lookupFuncValueTypeAA64` clears the `pkg/asm/x64` duplicate-symbol case (locally verified: `pkg/asm/x64` test binary builds and runs 70 tests green; full builder-comp sweep stays at 34/0).  BUT the `builder-comp_native_aa64-comp_native_aa64` sweep then trips a different cluster: `pkg/lint`/`pkg/types`/`pkg/native/*`/`pkg/ir`/etc. fail to link with `Undefined symbols: _bn_pkg__types__Append__bn_inst__mptr_pkg__ast__Decl` — the generic-instance-symbol case.  Instances of generics-using-cross-package-types depend on the consumer-side `__vt`/`__handle` emission for their internal dtor refs; the naive `IsExtern { continue }` skips those too, breaking the link from the other direction.  The fix needs to distinguish three cases at the gate:
  1. local function defined in this TU → emit `__vt`/`__handle`/`__shim` locally (current behaviour, keep).
  2. cross-package import (the `pkg/asm.__dtor_Assembler` shape) → skip (the defining TU emits; cross-TU resolution at link time).
  3. cross-package import whose body the consumer instantiates (the generic-instance shape) → still emit locally because the dtor body lives in this TU.
  The discriminator is probably "does this TU have an `IsExtern=false` function with a matching name?", or equivalently "is this an `IsExtern` extern whose `f.Body` is empty (no instance body)?"  Needs investigation before re-attempting the fix.  See also: pkg/codegen's `lookupFuncValueType` (the LLVM-side equivalent) already works correctly because LLVM's `weak_odr` linkage dedups across TUs at link time; native-aa64 needs the source-side gate because its `N_WEAK_DEF` path doesn't.
- **Deeper diagnosis 2026-05-29**: even with case-(2) skip in `lookupFuncValueTypeAA64`, the link fails the OTHER direction — `Undefined symbols: ___handle.bn_pkg__ast____dtor_File, referenced from pkg__parser.o`.  The defining package `pkg/ast` doesn't reference its own `__dtor_File` as a handle (the dtor function body exists in `pkg/ast.o`, but the `__handle`/`__vt`/`__shim` triplet is only emitted by `emitFuncValueVtables` when an `OP_FUNC_HANDLE`/`OP_FUNC_VALUE` references the function — and inside `pkg/ast` nothing does).  So consumers each emit their own triplet (currently all weak); skip the consumers and nobody emits.  Combined with the section-flag issue below, the fix has to be either:
  - (A) Have the defining TU **always** emit the `__handle`/`__vt`/`__shim` triplet for every locally-defined function whose body is non-empty (or at least every function that *could* be used as a function value cross-module — slim that down to functions whose address is taken, dtors, etc.).  Then consumers can safely skip.
  - (B) Keep the consumer-side emission but fix Mach-O coalescing: `pkg/asm/macho/macho.bn::machoSectType` returns `S_REGULAR` for the "data" section, which means even `N_WEAK_DEF` symbols don't coalesce across `.o` files on Mach-O (the LP64-side equivalent via clang's `weak_odr` lands them in `__DATA,__data_coal_nt` with section flag `S_COALESCED` = 0x0B).  Routing the `__handle`/`__vt`/`__shim` globals into a coalesced section would make consumers' duplicates safely dedupe at link time — closer to how LLVM achieves it.
  (A) is cleaner architecturally; (B) is a smaller diff but ties native-aa64's symbol model more closely to LLVM's.  Either way, the LLVM-side (pkg/codegen) is already correct and doesn't need to change.
- **Even deeper diagnosis 2026-05-29 (mangling mismatch)**: there's also a *separate* dtor-name-mangling bug feeding into this.  For a cross-package type like `@pkg/ast.File`:
  - In defining package `pkg/ast`: `dtorNameForType` writes `"__dtor_File"` (the type is local, unqualified).  `NewFunc` qualifies via `QualifyName("pkg/ast", "__dtor_File")` → `"pkg/ast.__dtor_File"`.  Mangled: `bn_pkg__ast____dtor_File`. ✓
  - In consumer package `pkg/parser` (via `registerPendingStructDtor`): `dtorNameForType(pst with pst.Name="pkg/ast.File")` writes `"__dtor_" + "pkg/ast.File"` = `"__dtor_pkg/ast.File"`.  The package path gets baked *into* the dtor token instead of qualifying around it.  `NewFunc` passes through (has dot).  Mangled: `bn___dtor_pkg__ast__File`. ✗ (DIFFERENT shape from defining package).
  - `OP_FUNC_HANDLE` reference (from `qualifiedDtorName` in `gen_util_refcount.bn`): `"pkg/ast.__dtor_File"`.  Mangled: `bn_pkg__ast____dtor_File`. ✓ (matches defining package).

  So consumers emit a *second* dtor implementation under a different mangled name than the OP_FUNC_HANDLE refers to.  On LP64-LLVM the consumer's `bn___dtor_pkg__ast__File` symbol just sits unused (it's emitted but nothing references it), and the OP_FUNC_HANDLE's `bn_pkg__ast____dtor_File` finds the defining-package's dtor cross-TU.  On Mach-O native-aa64, BOTH the consumer's `__vt` for the (wrong-named) consumer dtor and the consumer's `__vt` for the (right-named) handle reference collide with the defining package's emission — the dtor-vt duplicate.

  **Actually-correct fix**: don't emit dtors at all for cross-package types in consumers — let the defining package generate them.  Concretely, in `gen_util_refcount.bn:registerPendingStructDtor` (and its callers in `gen_dtor_emit.bn`), skip when `pst.Name` is qualified (`hasDot(pst.Name)`).  The OP_FUNC_HANDLE reference resolves to the defining package's symbol cross-TU.  This also fixes the Mach-O dtor-vt cluster as a side effect (no consumer-side dtor → no consumer-side triplet emission attempt → naive `IsExtern { continue }` in `lookupFuncValueTypeAA64` becomes correct).  Needs a separate audit of `gen_copy_emit.bn` for the same shape.

  This is the right TODO direction: skip cross-package dtor generation rather than coalesce/dedup the duplicate emissions.  Out of scope for the current session (context budget).

### pkg/vm test binary crashes silently on arm32_linux after universal-sret commit
- **Symptom**: under `builder-comp_arm32_linux`, the `pkg/vm` unit-test binary builds successfully but the run produces zero test output and the runner reports `FAIL: pkg/vm [8s]`.  No `--- PASS:` or `--- FAIL:` lines — the binary appears to crash before the first test runs.  153 tests are defined in the package; LP64 (`builder-comp`) runs all of them green.
- **Discovery**: 2026-05-29, while polling arm32 CI for the int64-fold fix.  Bisected via CI history:
  - `22a55e49` (just before 5331235e): `PASS: pkg/vm (153 passed) [8s]`.
  - `5331235e` (codegen: force universal sret for >16-byte aggregate returns): `FAIL: pkg/vm [8s]`.  Every subsequent run through `60f1e008` reproduces.
- **Root cause (unconfirmed)**: `5331235e`'s "force universal sret for >16-byte aggregates" likely interacts badly with arm32 + pkg/vm's calling convention somewhere.  pkg/vm defines a `VM` struct that's far larger than 16 bytes; multi-return helpers (`splitInt64 → (int, int)`, etc.) produce 8-byte aggregates on arm32 (fits in two registers), but the universal-sret change may now route them through the sret path on arm32 and corrupt frame layout.  Needs `gh run view --job` on the failing arm32 binary under `qemu-arm -d cpu,exec` (or similar) to capture the crash address.
- **Why MAJOR**: blocks the arm32_linux unit-test lane.  Doesn't affect LP64 (amd64 CI green) and doesn't affect arm32_baremetal (`pkg/vm` is XFAIL'd there for an unrelated reason — int32-range literal fit-checks).
- **Tests covering it**: the silent crash itself is the regression — the existing 153 pkg/vm unit tests all run on amd64 and would expose the arm32 crash if reached.  A focused codegen test would help: assert the arm32 sret/non-sret threshold matches LP64's at the IR level for aggregates ≤ register-pair (which on arm32 = 8 bytes).
- **Proposed fix**: re-examine `5331235e`'s sret threshold logic to ensure arm32 honours its ABI's smaller register-pair budget (vs. LP64's 16-byte pair).  A "universal sret for >16 bytes" rule applied verbatim across targets won't match arm32's AAPCS (4-byte word, 2-register max for return-by-value scalars, ≥8 bytes goes via sret already).  Alternatively: revert `5331235e` and apply the change per-backend.

### aa64 closure shim: outgoing user-args don't yet stack-spill when captures fill X0..X7
- **Symptom**: a closure whose total outgoing-arg word count
  exceeds 8 (e.g., two `@[]T` captures (4+4 words) plus a
  single user `int` (1 word) = 9 words) falls back to plain
  `B underlying` on aarch64.  Captures land in X0..X7 fine; the
  user-arg never gets written to its outgoing stack slot, and
  the underlying body reads garbage from `[SP+0]`.  Pinned by
  the 3rd block of `conformance/510_capture_managed_slice`
  (xfailed on `builder-comp_native_aa64`).
- **Where**: `pkg/native/aarch64/aarch64_closure_shim.bn`
  `emitClosureShim` — the `if captureWords + nUserWords > 8`
  fast-path fallback.
- **Fix direction**: mirror what `emitClosureShimStackSpill_x64`
  does — use `common.AAPCS64()`'s `CallArgRegStart` /
  `CallArgStackOff` to plan the outgoing call, `SUB SP, sp,
  #stkBytes`, write stack-bound capture / user-arg words to
  `[SP + ofs]` via a scratch register (X16 is the AAPCS
  intra-call scratch, mirroring its use in
  `pkg/native/aarch64/aarch64_call.bn`), load reg-bound
  captures, `BL underlying`, `ADD SP, sp, #stkBytes`, `RET`.
- **Symmetric x64 follow-up**: x64's `nUserWords > 5` check
  is the same shape — when incoming user-args overflow the
  5-word GP budget (RSI..R9 after RDI holds data), the x64
  shim falls back to JMP without moving args.  Same fix
  outline plus reading from `[RSP + rspDelta + 8 + k*8]` for
  each incoming-stack user-arg word.


### Demote raw-slice escape check from type error to linter rule
- **Final diagnosis**: an unqualified EXPR_IDENT inside a
  `.bni`-declared const initializer (e.g. `WORDS` in
  `const SIZE int = WORDS * cast(int, sizeof(int))`) wasn't
  resolving during import processing — pkg/ir's evalConstExpr
  looked the name up only in unqualified form, but the sibling
  const had been registered under the import-qualified name
  (`pkg/x.WORDS`).  The EXPR_IDENT arm returned (0, false), the
  binary expression silently became 0, and the resulting const
  was registered with value 0.
- **Fix (binate `8fd4f378`)**: retry the lookup with
  `buildQualName(currentImportAlias, e.Name)` when the
  unqualified one misses.  Pinned by conformance
  `504_bni_const_sibling_ref`.
- **Boundary-enforcement aside**: my first writeup of this also
  speculated that bnc was accepting unexported cross-package
  references.  Re-tested with a focused repro: bnc DOES correctly
  reject `pkg.NAME` references when NAME isn't in the package's
  `.bni`.  Pinned positively by conformance
  `502_err_unexported_const_rejected`.  That part was always fine
  — the only bug was the sibling-ident lookup above.
- **Discovery**: managed-allocation-header refactor (binate
  `c7323fb2`).  Replacing pkg/vm's hardcoded `-16` managed-header
  offset with `ptr - rt.HEADER_SIZE` (declared as
  `HEADER_WORDS * cast(int, sizeof(int))`) built cleanly but
  produced `ptr - 0`, silently corrupting the payload's first
  word.  TestExecRefIncRefDecInline (pkg/vm) caught it on amd64.

### Demote raw-slice escape check from type error to linter rule
- **Today**: returning a raw slice (`*[]T`) into a local array
  (`return arr[:]`) is a hard type-check error.  The check catches
  the obvious pattern but **misses the real escape paths** the
  type system can't see (escape via out-param, via mutating
  callee, via interface, etc.), so it's a false-confidence trap:
  the user assumes "if it type-checks, my raw slice doesn't
  escape", which isn't what the check actually proves.
- **Why now**: while designing Phase 2 of function values
  (`plan-function-values-phase-2.md`), the same escape question
  came up for capturing `*func(...)`.  Decision: no type-check
  rejection; raw is the opt-in escape hatch, linter warns on
  obvious patterns.  That makes the raw-slice rule the
  inconsistent one — slices are the only raw type with a hard
  escape check in the type system.
- **Fix direction**: demote the raw-slice escape rejection to a
  linter rule in `cmd/bnlint` (best-effort detection of return,
  store-to-outliving-field, assign-to-global, etc.).  Type
  checker stops rejecting; existing tests that exercise the
  reject become linter-positive cases.
- **Scope cost**: small.  One rule to remove from the type
  checker, one to add to bnlint, conformance test updates for
  the affected patterns, doc updates.
- **Coordination**: ideally lands alongside or just after Phase
  2 of function values (where the analogous capturing-`*func`
  linter rule is added — B.5 of `plan-function-values-phase-2`).

### IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
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
  - **What landed (int64 path)** — model in `plan-vm-64bit-on-32bit.md`:
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

### `__c_call` Stage 4 (variadic in the native backends) — UNBLOCKED 2026-05-28 (Bug 1 fixed)
- **Plan**: [plan-c-call.md](plan-c-call.md) §6–§7 step 4 — the
  hard chunk: darwin-arm64 variadic stack-passing (Apple ABI stacks
  ALL varargs regardless of GP-reg budget) + amd64-SysV `AL` setup
  (number of vector regs used by varargs).
- **Unblocked by Bug 1 fix**: Bug 1 (the underlying convention
  mismatch that produced the misleading Stage 4 bisect) is now
  fixed via the universal-sret codegen series (binate 3f963073,
  1755212f, etc.).  `stage-4-wip-broken` should rebase onto
  current `work-2` and the Stage 4 work can resume — the field-
  write-to-wrong-offset behavior the bisect picked up was an
  artifact of the convention mismatch on the underlying
  CallConv struct, not a fundamental bug in the V-variant
  helpers.
- **Status**: WIP saved on the `stage-4-wip-broken` branch of the
  binate worktree (`temp-binate-2`).  Last sane commit `1938a86`.
  REBASE NEEDED: main has since renamed `pkg/native/amd64` →
  `pkg/native/x64`; the branch will need path rewriting before it
  rebases cleanly.
- **What the WIP contains**:
  - `CallConv.VariadicStackOnly bool` + `AAPCS64_Darwin()` constructor
    + three sibling V-variant helpers (`CallArgRegStartV` /
    `CallArgStackOffV` / `CallStackBytesV`) that take a `fixedCount
    int` and apply the saturation `if k == fixedCount &&
    cc.VariadicStackOnly { ngrn = cc.NumGpArgRegs }` to force
    variadic args to the stack.
  - arm64 `emit_func.bn` instantiates `AAPCS64_Darwin()`; arm64
    `emit_call.bn` routes through the V-variants with `fixedCount =
    ins.CFixedArgs` (OP_C_CALL) / `len(argTypes)` (OP_CALL).
  - amd64 `emit_call.bn` emits `MOV AL, 0` before `CALL` for variadic
    OP_C_CALL.
  - `common.bn`'s `PlanFrame` considers `OP_C_CALL` too and uses
    `CallStackBytesV`.
  - conformance/500 native xfails removed.
- **The bug**: switching arm64 to `AAPCS64_Darwin` regresses a
  NON-variadic OP_CALL (`bootstrap.formatInt64` from a `println(int)`)
  to silently no-op — disasm shows the 2-word raw-slice arg going to
  the STACK at `[sp+0..sp+8]` instead of X1+X2, then `bl
  _bn_pkg__bootstrap__formatInt64` returning, and the program exits
  0 with no output.  conformance/498 (non-variadic) fails under
  AAPCS64_Darwin; conformance/500 (variadic) passes.  Flipping back
  to AAPCS64() reverses both.  Can't get both green simultaneously.
- **Theory**: the V-variant saturation mathematically cannot fire
  for a non-variadic call (`k <= i < len(argTypes) = fixedCount`).
  Something else is diverging behavior — possibly a layout
  interaction with the new `VariadicStackOnly` struct field, or an
  ngrn-accounting side effect I'm not seeing.
- **Root cause IDENTIFIED 2026-05-27 — native-arm64 field-offset
  bug for the SECOND consecutive trailing-bool struct field.**
  Bisect on the rebased WIP narrowed the regression to setting
  `cc.VariadicStackOnly = true` alone, even after reverting V-routing
  AND PlanFrame.  Disassembled
  `_bn_pkg__native__common__AAPCS64_Darwin` in the failing
  `bnc_native` binary:
  ```
  mov  w8, #0x1
  and  x8, x8, #0x1
  str  x8, [sp, #0x48]    ; 8-byte store at offset 0 of cc
                            (NumGpArgRegs slot!), NOT 1-byte store
                            at offset 49 (VariadicStackOnly)
  ```
  The local `cc` starts at `sp+0x48` and is 56 bytes (verified by
  the field-by-field copy preceding this in the same disasm).  The
  assignment to the 2nd consecutive trailing-bool field is emitted
  with the WRONG offset (0 instead of 49) AND the WRONG width
  (8-byte vs 1-byte).  NumGpArgRegs gets clobbered from 8 to 1.
  Downstream: every `OP_CALL` past one arg spills to stack →
  formatInt64's 2-word raw-slice second arg lands at `[sp+0]..
  [sp+8]` instead of X1+X2 → `println(int)` silently produces no
  output → conformance/498 breaks.
- **This is a separate native-backend compiler bug, not a Stage-4
  bug.**  LLVM backend handles the same source correctly (gen1 is
  LLVM-built and runs `AAPCS64_Darwin` fine).
- **Standalone repro found (2026-05-27)** — and a SECOND, related
  bug surfaced.  Cross-package 6-int+2-bool struct, `Base()`
  constructor in `pkg/foo`, `main` calling `var s foo.S =
  foo.Base(); bootstrap.Exit(s.a)`: expect exit 8, native gives
  exit 16 (= s.f, the last int).  Disasm shows:
    * `Base` ASSUMES sret-return: `mov x9, x8` (saves caller's
      sret pointer) and stores all 7 words via `str x5, [x9]; str
      x4, [x9+8]; …`.
    * `main` ASSUMES register-return: no X8 setup; reads `str x0,
      [sp]; …; str x6, [sp+0x30]` after the call.
    * AAPCS-64 register-pack-vs-sret threshold is
      `InternalSretBytes=64`; the struct is 56 bytes → caller
      (`main`) is right, callee (`Base`) is wrong.  `Base`
      happens to leave the fields in X0..X5 as a side effect
      (in REVERSE: `ldr x5, [sp+8]` = a … `ldr x0, [sp+0x30]` =
      f), so caller's `X0 = f = 16` instead of `X0 = a = 8`.
  So we actually have **two distinct native-aarch64 bugs** for
  this struct shape, both surfaced by CallConv:
    1. **Convention mismatch**: callee uses sret, caller uses
       register-return, for a sub-threshold (56 ≤ 64) struct.
    2. **Field-write to the 2nd consecutive trailing-bool field**
       uses wrong offset+width (8 bytes at offset 0 instead of
       1 byte at offset 49 — the CallConv miscompile above).
  Both LLVM-correct, both native-broken.
- **Further narrowing on Bug 1**: same-package version of the
  cross-package repro (Base + main in one package, same struct
  shape) **works** — exit 8.  And in the CallConv intra-package
  case, the caller (`AAPCS64_Darwin`) DOES set up X8 before
  `bl AAPCS64` — caller AGREES with callee on sret.  So:
    * intra-package: `FuncReturnsBigAggregate` (callee) AND
      `CallReturnsBigAggregate` (caller) BOTH return TRUE for
      the 56-byte struct → both use sret → consistent.
    * cross-package: callee TRUE, caller FALSE → MISMATCH →
      broken.
  Both predicates use the same `t.SizeOf() > cc.InternalSretBytes`
  check; they differ only in input — `f.Results[0]` (callee) vs
  `ins.Typ` (caller).  Strong hypothesis: **`SizeOf` returns
  different values for the same struct depending on which side
  (callee-Func-Results vs caller-ins-Typ) is asking, and that
  asymmetry IS the underlying defect.**  Also note: the 56-byte
  struct shouldn't trigger sret AT ALL (threshold is 64) — so
  even the consistent intra-package "true" answer is wrong.
  Looks like SizeOf is returning 72 (each bool padded to 8) or
  similar in one of the contexts.
- **Fix area**: pkg/types `SizeOf` / `StructLayout` for the
  6-int + 2-bool shape, and/or cross-package type resolution
  not preserving layout.  Also: `FuncReturnsBigAggregate` /
  `CallReturnsBigAggregate` should give the same answer
  regardless of whether the type is reached via a Func.Results
  pointer or an Instr.Typ pointer.
- **Reliable repros**:
    * Bug 1: tiny standalone — `/tmp/x/pkg/foo.bni` (S struct
      with 6 ints + 2 bools, `Base() S`), `/tmp/x/main.bn`
      calling `foo.Base() + bootstrap.Exit(s.a)`; native exit
      16 instead of 8.
    * Bug 2: `git checkout stage-4-wip-broken` in
      `temp-binate-2`, then `conformance/run.sh
      builder-comp_native_aa64-comp_native_aa64 498`.
- **ROOT CAUSE for Bug 1 IDENTIFIED 2026-05-28 — AAPCS64
  `InternalSretBytes=64` is non-standard.**  Added a debug
  `println` to `FuncReturnsBigAggregate` and traced: SizeOf is
  56 in BOTH same-package and cross-package contexts — there is
  no SizeOf asymmetry after all.  Disagreement is from a different
  source.  gen1 compiles dependency packages via LLVM regardless
  of `-backend native` (only the main module honors the backend
  flag — see cmd/bnc/main.bn:183), so pkg/foo.Base ends up
  clang-emitted from `define %bn_pkg__foo__S
  @bn_pkg__foo__Base()`.  clang follows the AAPCS-64 spec
  strictly: aggregates **> 16 bytes** are returned via the
  indirect-result register X8 (sret).  Binate's `AAPCS64()` sets
  `InternalSretBytes = 64` (common_callconv.bn:68) — a wider
  non-standard threshold that "packs up to 8 GP regs" for
  Binate-internal calls.  So:
    * native main: `CallReturnsBigAggregate(ins)` → `56 > 64`
      = false → caller uses register-return.
    * LLVM-compiled foo.Base: standard AAPCS → 56 > 16 → uses
      sret.
    * MISMATCH at the native↔LLVM boundary for any aggregate
      between 17 and 64 bytes.
- **Why intra-package "works"**: both AAPCS64_Darwin (caller) and
  AAPCS64 (callee) are compiled by gen1-native using the same
  wider-threshold AAPCS64, so they coincidentally agree on sret
  for the 56-byte CallConv.  Bug 2 (the field-write to offset 0)
  still fires within Darwin's body but it's a separate defect.
- **Fix proposal for Bug 1**: change `AAPCS64()` InternalSretBytes
  from 64 to 16, matching standard AAPCS / what LLVM/clang does.
  Loses the Binate-internal pack-up-to-8-GP-regs optimization
  but eliminates the cross-backend silent miscompile.  Mechanical
  changes: common_callconv.bn:68 (`= 64` → `= 16`), update
  common_callconv_test.bn:30 (expected 64 → 16), audit
  conformance tests with aggregate returns in the 17–64 byte
  range for behavior change.  This is a significant ABI change
  for Binate-Binate calls — worth a user decision before
  proceeding.
- **Attempted Bug 1 fix (2026-05-28): InternalSretBytes 64→16 — REVERTED.**
  Reduced AAPCS64 InternalSretBytes from 64 to 16, expecting the
  cross-package native↔LLVM mismatch to dissolve.  All native/common
  + native/aarch64 unit tests passed after updating the test
  expectations (6 tests pinned the old 64-byte behavior).  Bug 1's
  standalone repro DID flip to "exit 8" as expected (fix works for
  that case in isolation).  But the bigger fallout: gen1-compiled
  bnc_native CRASHES IMMEDIATELY (SIGSEGV in bn_entry) on any input
  — ALL 430 conformance tests in `comp_native_aa64` mode failed
  with "COMPILE_ERROR".  The bnc_native SIGSEGV looks like an
  infinite recursion (frames 1 and 2 at the same address in lldb
  bt) hitting a small-negative address in a refcount-style
  increment loop (`ldr x10, [x9, #-0x10]!`).  Theory: the wider
  sret path through native-aarch64 has latent bugs that didn't
  surface pre-fix because internal Binate aggregates rarely
  crossed the 17–64 byte range, so the sret return path was
  mostly exercised by IsCExtern callees (>16-byte C-extern
  returns).  Once every internal aggregate above 16 bytes flips
  to sret, those latent bugs fire everywhere.  Worth pursuing
  separately: audit the aarch64 emit_return + sret prologue
  path for cases that pre-fix were not exercised
  (single-aggregate ≤64 bytes, multi-return tuples ≤64 bytes).
- **Recommended path forward**: don't ship the InternalSretBytes
  change in isolation.  Either (a) fix the native sret path's
  latent bugs first (then change the threshold), or (b) keep
  the wider threshold but mark cross-LLVM-boundary calls
  specially (route them via the CExternSretBytes=16 path).
  Option (b) is less invasive but requires propagating "this
  callee is LLVM-compiled" through to the call site.  Option
  (a) is correct in principle but blocks on the latent sret
  bugs.  Pinned by: standalone repro in /tmp/x/.
- **Round-3 root-cause (2026-05-28) — the round-2 "surgical" was
  effectively wholesale, AND wholesale exposes a true caller/callee
  mismatch for monomorphized generics.**  `pkg/ir.EmitCall` routes
  every call through `qualifyForCurrentModule` →
  `mangle.QualifyName(currentPkgPath, name)`.  `QualifyName` prepends
  `<pkg>.` to any bare name → **every** `ins.StrVal` has a dot
  (e.g. `splitColon` → `"main.splitColon"`).  So the round-2 helper
  `isCrossPackageCallName(name)` (dot-presence) returned true for
  everything — identical caller-side effect to wholesale.  Then the
  real mismatch: monomorphized generics get instantiated into the
  CURRENT module (`bn_main__Append__bn_inst__mslc_uint8`).  Their
  `ins.StrVal` becomes `"main.Append__bn_inst__mslc_uint8"` (dot
  from QualifyName).  Round-2/wholesale → caller emits sret.  But
  the callee is native-compiled in main, where
  `FuncReturnsBigAggregate(f)` = `32 > 64` = false → callee emits
  reg-return.  **Mismatch.**  Disasm-confirmed in parseArgs at
  `0x10030baf0`: caller sets `X8 = SP+0x1138`, calls
  `bn_main__Append__bn_inst__mslc_uint8`, then later loads
  `[X9 = 0x1]` (the corrupted "returned slice"'s data ptr) → SIGSEGV.
- **Correct surgical fix**: predicate should check
  `ins.StrVal` starts with `<currentPkgName>.`.  Intra-module
  (instantiated generics included) → `InternalSretBytes` (64).
  Cross-module (LLVM-compiled) → `CExternSretBytes` (16).
  Requires threading `pkgName` into `CallReturnsBigAggregate` /
  `CallReturnsBigMultiReturn` (currently `(cc CallConv) (ins)`);
  call sites at aarch64_call.bn:120, aarch64_iface.bn:143,
  x64_call.bn:92, x64_iface.bn:121, plus the .bni signature.
  `pkgName` is already available at all sites.
- **Note on wholesale**: untested whether wholesale's bnc_native
  crash is the SAME generic-instantiation mismatch in disguise
  (very plausible, since wholesale changes only `InternalSretBytes`
  → both caller and callee predicates use 64→16 → caller flips
  to sret for >16-byte aggregates, and callee
  `FuncReturnsBigAggregate` ALSO flips → match restored
  intra-module too).  If so, wholesale is actually a correct
  fix that simply needs the callee/caller predicate update and
  the test-expectation refresh (the 6 tests pinning 64).
  Worth re-running wholesale and checking carefully before
  committing to surgical-vs-wholesale.

- **Round-4 (2026-05-28) — wholesale fix landed as WIP
  commit `2c0cb952` on work-2, plus first round of latent
  native-aarch64 sret bug fixes.**
  - `common_callconv.bn`: `InternalSretBytes` flipped 64→16
    for AAPCS64.  Unit-test expectations updated (divergence
    tests reshaped as "agrees" tests since both AAPCS-64 and
    SysV-AMD64 now use 16).
  - `aarch64_emit.bn::emitMakeSlice`: had a HARDCODED
    reg-return convention assumption for the LLVM-compiled
    `bn_pkg__rt__MakeManagedSlice` (expected 32-byte
    ManagedSlice packed in X0..X3).  Wrong under AAPCS-C —
    LLVM uses X8 sret for >16-byte aggregates.  Pre-fix it
    happened to "work" because LLVM's local-variable register
    allocation left the values in X0..X3 as a side effect.
    Fixed to set X8 = &data region before BL, drop the
    post-call register-pack copy.  x64's emitMakeSlice was
    already correct (SysV always used 16).
  - `aarch64.bn::emitFuncValueShims`: had hardcoded
    `retSz > 64` for the sret-vs-pack shim shape.  Lowered
    to `> 16` to match the new underlying-function ABI.
  - **Verified**: standalone Bug 1 repro (/tmp/x) → exit 8
    (correct).  All native unit tests pass.
  - **REMAINING**: bnc_native_wholefix changes failure mode
    from SIGSEGV (pre-fix) to a controlled runtime "index
    out of bounds: 0 (len 0)" during cmd/bnc's startup arg
    parsing.  `_bn_main__main` and `_bn_main__parseArgs`
    disasms are BYTE-IDENTICAL between prefix and postfix
    binaries, so the regression must be in a downstream
    callee whose ABI flipped — likely a dtor, refcount
    helper, or generic-instantiation in the main package.
    Not yet root-caused.
- **Key insight that REFRAMES the analysis**: pre-fix's
  predicates were ACTUALLY CORRECT under LLVM's effective
  AArch64 ABI for the IsCExtern case but BROKEN for the
  non-IsCExtern case.  LLVM on AArch64 follows AAPCS-C target
  rules even WITHOUT the `sret(...)` annotation —
  `define %S @foo()` for a 56-byte `%S` uses X8 sret in the
  emitted machine code.  `pkg/codegen` only emits `sret(...)`
  annotations for `IsCExtern` functions
  (`emit.bn:254`), but ALL aggregate-returning functions get
  the same backend lowering on AArch64.  So for cross-package
  calls to non-IsCExtern Binate functions returning 17–64 byte
  aggregates, pre-fix's caller (reg-return via the wider
  64-byte threshold) DISAGREED with the LLVM-compiled callee
  (X8 sret).  Pre-fix's `CalleeUsesCSret` ONLY caught the
  IsCExtern case, which is why most things "worked" — most
  cross-package callees in the codepath either return scalars
  or are IsCExtern.  /tmp/x's 56-byte foo.S is the smoking
  gun proving pre-fix is broken.
- **Where this points**: the remaining bnc_native_wholefix
  failure isn't a side effect of the wholesale fix — it's
  ANOTHER latent native-aarch64 emit site assuming the old
  packed-return convention.  The set of "places that assume
  reg-return for ≤64-byte aggregates" is the audit set;
  that's the remaining work.  Audit candidates: any
  `argReg(0..N)` store immediately after a `Bl(callee)` where
  the callee returns a 17–64 byte aggregate.  Concrete
  next-session approach: diff every function that differs
  byte-for-byte between prefix and postfix binaries (since
  parseArgs/main don't differ, the broken site is in some
  other function, likely a per-package dtor, append, or
  generic-instantiation helper in the main module).

- **Earlier round-2 hypothesis (now superseded by round-3 above).**
  Hypothesis was: the wholesale `InternalSretBytes 64→16` change
  may be too broad; try just changing the caller-side predicate
  for CROSS-package calls (callee name contains '.') to use the
  16-byte threshold while keeping intra-package callees at 64.
  That should ONLY adjust the native↔LLVM boundary calls
  (cmd/bnc → dep packages compiled by LLVM) and leave
  intra-binary Binate-Binate calls alone.  Implemented:
  `isCrossPackageCallName(name)` helper that returns true iff
  the name contains '.'; threaded into `CallReturnsBigAggregate`
  and `CallReturnsBigMultiReturn`.  Bug 1 standalone repro:
  flips to exit 8 as expected (fix works in isolation).  All 34
  unit-test packages still pass.  But bnc_native STILL crashes
  on every conformance test the same way — same SIGSEGV at
  `ldr x10, [x9, #-0x10]!` with x9 = -16 (a managed-slice
  backing-ptr RefInc on a corrupted pointer).  So the surgical
  fix doesn't dodge the problem.
- **What this tells us**: tiny native programs returning the
  same shapes (managed-slice, struct-with-managed-slice,
  managed-slice-of-managed-slices) work fine post-fix.  The
  bug emerges only when cmd/bnc's MANY cross-package
  aggregate-returning calls all start using sret — probably ONE
  specific call shape triggers the latent native sret-call
  bug, but it took the wholesale switch (or its surgical
  equivalent) to surface it.  Next-session approach: build
  pre-fix and post-fix bnc_native with `--debug`, compare main
  disasm + frame layout side-by-side, AND identify which
  specific function in cmd/bnc's call chain first emits the
  bad ldr/str sequence.  The standalone repro proves the sret
  path works for SOME shapes — the question is which shape
  breaks.  Worth diffing all functions that emit `ldr [x9,
  #-0x10]!` (the RefInc pattern) and checking each for
  argument/result misalignment.
- **Round-5 audit (2026-05-28) — central finding: LLVM does NOT
  consistently use sret for >16-byte aggregate returns.  Both
  pre-fix and post-fix native predicates are wrong; the right fix
  is in pkg/codegen.**  Empirical evidence from
  `/tmp/bnc_native_wholefix`:
    * `bn_pkg__rt__MakeManagedSlice` (32-byte ManagedSlice return):
      LLVM emits `ldr x0..x3, [sp+0x10..0x28]; ret` — packs the
      4-word return in **X0..X3** (no sret).
    * `bn_pkg__foo__Base` (56-byte struct S): LLVM emits
      `mov x9, x8; str x5,[x9]; ...; str w8,[x9+0x37]; ret` —
      writes through **X8 sret**.
    Same IR-level form (`declare %T @foo(...)`, no `sret()`
    annotation), DIFFERENT machine code.  LLVM's AArch64 backend
    uses its own heuristic — likely **≤4 fields → pack into
    X0..X3; >4 fields → sret via X8** (the AArch64 "Composite
    Type" rule applied per-field-count not per-byte-count).
- **Implications**:
    1. Pre-fix native predicate (pack ≤64) is correct for the
       ManagedSlice case (matches LLVM's pack-X0..X3).  My
       wholesale fix (sret >16) BREAKS that case: caller sets up
       X8 buffer, callee packs into X0..X3, caller reads from
       X8 buffer (never written) → corruption.  Exactly the
       runtime bounds error we observe in `bnc_native_wholefix`.
    2. Pre-fix native predicate is WRONG for the foo.S case
       (56 bytes, 9 fields).  LLVM uses sret, native packs →
       original Bug 1 (the /tmp/x mismatch).
    3. Neither pure "always pack" nor pure "always sret" matches
       LLVM's effective heuristic.  Options:
        (a) **Mirror LLVM's heuristic exactly** in the native
            backend.  Fragile — depends on LLVM version.
        (b) **Force LLVM to use sret consistently** via
            `sret(...)` annotation in pkg/codegen — drop the
            `f.IsCExtern &&` gate at `emit.bn:254` (and the
            matching declare path at `emit.bn:175`).  Then
            both sides use sret universally.  Substantial
            pkg/codegen refactor: the define-line at
            `emit_debug.bn:43` also needs to switch to
            `define void @name(ptr sret(%T) %retbuf, ...)`
            for affected functions; OP_RETURN's lowering
            inside those functions needs to write through
            the sret pointer.
        (c) Force LLVM to use pack consistently — impossible
            for arbitrary struct sizes; LLVM won't pack
            >64 bytes into 8 registers.
- **emitMakeSlice "fix" in `2c0cb952` is WRONG.**  Pre-fix's
  emitMakeSlice (read packed X0..X3 from MakeManagedSlice) was
  CORRECT under current LLVM behavior.  The fix needs reverting
  unless we also pursue option (b) above.
- **The right path is (b)**: extend pkg/codegen to mark all
  >16-byte aggregate returns as sret (not just IsCExtern),
  threading it through the define/declare emission AND the
  OP_RETURN lowering AND the call-site sret-alloca pattern.
  Then `InternalSretBytes 64→16` in the native backend is
  consistent, and emitMakeSlice's sret fix becomes correct.
  This is a real refactor of pkg/codegen — significantly bigger
  than the native-side fix I attempted.  User-level scope
  decision before proceeding.

- **DONE 2026-05-28 — option (b) landed in 4 commits on work-2.**
  Series:
    * `2c0cb952` WIP — native side: AAPCS64 InternalSretBytes
      64→16, emitMakeSlice sret form, emitFuncValueShim
      thresholds 64→16.
    * `3f963073` codegen — universal sret for >16-byte aggregate
      returns: drop IsCExtern gate at emit.bn:175 (declare) and
      emit.bn:254 (registry); emit_debug.bn switches the define-
      line to `define void @name(ptr sret(%T) align 8
      %v.retbuf, <params>)` for IsSret funcs; emit_helpers.bn's
      emitReturn routes via store-to-%v.retbuf + ret void;
      emit_helpers.bn::emitMakeSliceInstr and emit_strings.bn::
      emitStringToCharsCopy converted to sret-form
      MakeManagedSlice calls; matching aarch64_emit.bn::
      emitStringToCharsCopy fix.
    * `d2d885e2` tests — invert/update the 5 unit tests pinning
      the old reg-pack convention (1 in pkg/codegen, 1 in
      pkg/native/common, 3 in pkg/native/aarch64).
    * `1755212f` codegen — extend universal sret to
      emit_funcvals.bn::funcValueUsesSret (drop IsCExtern gate)
      and emit_impls.bn::emitCallIfaceMethod (sret-form
      bitcast + sret-call + load pattern).  Fixes conformance
      363 (aggregate funcval) and 411 (pkg/std.Stringer via
      *Stringer iface dispatch).
  Verified: all 6 CI conformance modes pass — builder-comp
  (431/431), builder-comp-comp (431/431), builder-comp-int
  (429/429), builder-comp-comp-int (429/429), builder-comp-
  comp-comp (431/431), builder-comp_native_aa64-comp_native_
  aa64 (430/430).  All 34 unit-test packages green.  /tmp/x
  exits 8 (was 16 pre-fix).
- **Known follow-up (not in CI's modeset)**:
  builder-comp_native_x64_darwin-comp_native_x64_darwin fails
  on 363/411 — x64_iface.bn needs the same sret arg-shift fix
  aarch64 got automatically through the CallReturnsBigAggregate
  predicate.  Skipped for this round since darwin-x64 isn't in
  the CI mode-set; tracked separately if the user wants to
  un-fail it.

- **Two distinct fixes blocked on this**:
    1. Stage 4 native variadic (Apple ABI stacks all varargs).
    2. Any future native-compiled package adding a 2nd consecutive
       trailing-bool field — silent miscompile risk for everyone,
       not just `__c_call`.
- **Pinned by**: conformance/500_c_call_variadic (currently xfail in
  native modes; un-xfail when Stage 4 lands).

### ~~LLVM codegen: `&global` as an interface-value data pointer emits `%v-1`~~ — FIXED 2026-05-26 (binate `a2d84c0`)
- **Was**: constructing an interface value from the address of a
  package-level global — `var iv *Greeter = &g` where `g` is a global
  struct — emitted an invalid data-pointer operand (`%v-1`, no SSA id
  for the global's address) and clang rejected the module.  Loud, not
  silent.  Both the LLVM and native paths now materialize the global's
  address correctly; conformance/495_iface_construct_from_global passes
  in all modes (its xfails are gone).

### ~~CI: bump artifact actions off deprecated Node 20~~ — DONE 2026-05-26 (binate `665c198`)
- `actions/upload-artifact@v4` / `download-artifact@v4` ran on Node
  20 (deprecation flagged on every artifact step of the bnc-0.0.2
  release run; GitHub forces Node 24 on 2026-06-02, removes Node 20
  from runners 2026-09-16).
- Bumped the 4 uses — `release.yml` + `perf-tests.yml` — to
  `upload-artifact@v7` / `download-artifact@v8` (both node24).
  Params we use (`name`, `path`, `if-no-files-found`,
  `retention-days`, `pattern`) are stable across the bump; v8's
  "direct download" skip-unzip path only triggers for
  `archive:false` uploads, which we don't use.  `checkout@v6` /
  `setup-go@v6` were already node24.
- Not yet exercised by an actual run; the next Release or perf run
  will confirm the deprecation warnings are gone.

### arm32 unit-test cleanup: 5 remaining int64-boundary tests
- **Context (2026-05-28)**: `builder-comp_arm32_linux` unit tests
  are now down to **5 failures across 3 packages** — every other
  cascade of arm32 issues that surfaced through May 27–28 has
  been root-caused and fixed.  The remaining 5 share one shape:
  int64-min literal handling on a host whose `int` is 32-bit.
- **Resolved (commit trail)**:
  - `aee0260` — `cmd/bni` test runner lookup keyed on full
    pkgPath (fixed the entire `-int` unit-test lane that was
    silently broken since `7f989ad`'s mangler full-path flip).
  - `73651c28` — int↔int width-cast lowering: BC_TRUNC32 + emit
    BC_SEXT / BC_ZEXT for narrowings / widenings between
    int8/int16/int32/int64 (was unconditionally BC_MOV — wrong
    for any non-8-bit width change).
  - `a2588c54` — `pkg/types` `initTarget()` defaults host-detect
    via `sizeof` (was hardcoded LP64).  Fixes the root cause that
    made `is64BitScalar(TypInt())` true on arm32 and triggered
    pair-branch emission for plain-int ops.
  - `11ff9864` + `2d13838d` — LP64-baked test assertions across
    pkg/{vm,types,codegen,native/{common,aarch64,x64}} replaced
    with host-aware checks or explicit `setTarget64()` + a
    `TypInt → TypInt64` substitution where the test's intent was
    "an 8-byte int field on LP64 ABI".  Also fixed two real bugs
    the cascade exposed: BC_FTOSI / BC_SITOF / BC_F64_TO_F32 /
    BC_F32_TO_F64 pair-aware, and `is64BitScalar` accepting
    TYP_UNTYPED_FLOAT.
  - `81d31b7c` — managed-allocation header offset host-aware
    (`MANAGED_HDR` const = `2 * sizeof(int)`, was hardcoded 16),
    cleared the `TestRepro_StructWithManagedSliceFieldAppend`
    qemu segfault.
- **Status of previously-listed buckets**:
  - **Bucket 1 (LP64-baked tests)**: pkg/vm, pkg/codegen, pkg/native/*
    are GREEN.  pkg/asm/{x64,aarch64,macho} weren't in the
    cascade-revealed set and remain native-host-arch dependent
    (likely still need xfails, but separate workstream — host
    arch != target arch).
  - **Bucket 1b (pkg/vm TypInt width)**: ROOT-CAUSED.  Fixed by
    `a2588c54` (initTarget host-detect — the LP64-default was
    the deeper-than-suspected cause; not a test-scaffolding
    SetTarget ordering issue).
  - **Bucket 2 (genuine test-level)**: Still open as listed —
    `TestBinBufWriteU64LittleEndian` (pkg/asm/elf),
    `TestOrrImm` (pkg/asm/arm32).
- **Still open — Bucket 3 (int64-min boundary)**:
  - `pkg/bootstrap.TestFormatInt64Boundaries`
  - `pkg/buf.TestWriteInt` — "expected int64-min round-trip"
  - `pkg/ir.TestBignumToIntInt64Min`
  - `pkg/ir.TestGenUnaryMinusOnInt64Preserves`
  - `pkg/ir.TestNeedsHintNarrowing`
  All five share the int64-min literal pattern.  Likely one
  underlying fix: bignum / parseIntLit handling for values that
  overflow int32 on the host but fit int64 at the target.  Not
  blanket-xfail — investigate and fix.

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

### ~~bnc: managed local inside a `switch case` body miscompiles~~ — FIXED 2026-05-25 (binate `4306197`)
- **Was**: `genSwitch` generated case bodies with a bare `genStmt`
  loop and no variable-scope boundary (unlike `genBlock`, which
  saves/restores `ctx.Vars` and emits `emitDecForScopeVars` at scope
  exit).  A managed local declared in a `case` body lingered in
  `ctx.Vars` for the rest of the function and was RefDec'd on every
  later exit path — sibling cases and the switch's fall-through
  `return`s.  On those paths the local's alloca held a stale value
  from an earlier call that DID run the case (slot reuse), so the
  spurious RefDec freed a still-live backing → heap corruption.
  The VM tripped it hard: `execStringOp` runs for every bytecode
  instruction, and the `return false` path RefDec'd a stale
  `@[]char` slot (silent SEGV / empty output, ~340 builder-comp-int
  failures).
- **Fix**: extracted `genCaseBody`, mirroring `genBlock` — per-case
  var-scope save/restore + `emitDecForScopeVars` on normal fall-off.
- **Pinned by**: conformance/489_switch_case_managed_local_scope
  (SEGV pre-fix, correct post-fix).  Workaround removed:
  `pkg/vm/vm_exec_helpers.bn:execStringOp` now has all dispatchers
  in `switch` form.

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

### Generics in cmd/bnc's tree — UNBLOCKED 2026-05-26 (BUILDER → bnc-0.0.2)
- **Status**: BUILDER is now bnc-0.0.2 (binate `5414bab`), which
  was cut from a tree that has generics (slices 4–7).  Verified the
  builder compiles generic decls + explicit instantiation
  `f[T](...)`; cross-package monomorphization works too.  So
  cmd/bnc-tree code may now use generics.
- **No type inference** (claude-notes.md:537, 1000): always spell
  the type arg, e.g. `slices.Append[@ast.Decl](xs, d)`.  The
  builder's "generic function requires type arguments" diagnostic
  on a bare `f(...)` call is intended behavior, not a gap.
- **First consumer — `pkg/slices`** (IN PROGRESS): `Append[T]`
  collapses the dozens of per-type `appendXxx` / `appendXxxPtr`
  helpers scattered across cmd/bnc + pkg/*.  Migration is staged
  one package at a time (see below).
  - **Generic packaging pattern**: a generic's body must live in
    the `.bni` (body-included) so cross-package consumers can
    monomorphize at the call site.  For an all-generic package the
    `.bn` needs **no** copy of the body — just the `package` decl
    (the package's own compile + tests resolve the generic from the
    merged `.bni`).  Keeping a second body in the `.bn` is a
    needless sync hazard; don't.
- **Mechanical migration DONE 2026-05-28**: ~62 per-type append
  helpers across pkg/{ast,types,ir,parser,loader,codegen,vm,
  native/aarch64} + cmd/bnc collapsed into ~378 call sites of
  `slices.Append[T]`, one commit per package boundary
  (binate `2714e67` loader → `ed727f8` parser → `bbb7fab5` ir →
  `60f385ff` cmd/bnc → `12f20a06` types → `79c11465` ir literals →
  `efbac9db` codegen → `d43185bb` vm → `1a45bb9b` aarch64 →
  `d226b237` ir scattered → `13477619` types capture → `a66b287c`
  cmd/bnc test).  Four `pkg/{loader,parser,ir,cmd-bnc}/slices.bn`
  files deleted.  Net ~-750 lines.

### Review remaining non-standard `appendXxx` helpers — opportunistic
- 13 helpers were kept past the `slices.Append[T]` migration because
  their bodies aren't a pure slice-of-T append (per the commit
  messages around 2026-05-28).  Worth reviewing whether any could be
  refactored to use `slices.Append` plus a small adapter:
  - ~~**Char-concat into a `@[]char` buffer** (not slice-of-T):
    `pkg/native/x64/x64_iface.bn`'s `appendPkgIdent_x64`,
    `appendStrIface`; `pkg/native/aarch64/aarch64_iface.bn`'s
    `appendPkgIdentNative`, `appendStrLocal`.  These four could
    probably share a single `buf.WriteStr`-style helper.~~ — DONE
    2026-05-28 (binate `fd1e931c` + `1b762f16`): pulled the two
    distinct shapes into `pkg/native/common.AppendStr` /
    `AppendPkgIdent`, x64/aarch64 callers rewritten, 4 duplicate
    helpers deleted, direct unit coverage in common_test.bn.
  - **Dedup / diagnostic-emitting**:
    `pkg/types/check_iface_extends.bn`'s
    `appendIfaceMethodWithConflictCheck` (emits a `CheckError` on
    signature mismatch) and `appendUniqueMethods` (dedup by method
    name).  These stay non-standard.
  - **Parallel two-slice append**:
    `pkg/ir/gen_iface_extends.bn`'s `appendAncestors(pkgs, names,
    pkg, name)` — could split into two `slices.Append` calls but
    the paired-update pattern is the helper's value; debatable.
  - **Conditional multi-arg append**: `cmd/bnc/target.bn`'s
    `appendTargetFlags`, `appendTargetRuntime` — fine as-is.
  - **Loader-level Imports**: `cmd/bnc/compile_imports.bn`'s
    `appendRtImport`, `appendLibcImport`, `appendBootstrapImport` —
    not slice append; fine as-is.
  - **Raw-slice wrap-and-append**: `cmd/bnc/util.bn`'s
    `appendRawCharSlice(s, *[]const char) → @[]@[]char` (CopyStr +
    append).  Could inline the 47 call sites as
    `slices.Append[@[]char](s, buf.CopyStr(v))` but the named
    helper documents the wrap-and-append idiom; debatable.

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
  body), since fixed (`4306197`) — see the FIXED entry above.
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).


- **Self-hosted (LANDED, 2026-05-01)**: type-checker
  (`pkg/types/check_stmt.bn:checkReturnStmt`) and IR-gen
  (`pkg/ir/gen_stmt.bn` STMT_RETURN branch) accept
  `return f(...)` when `f` returns the matching tuple. Each
  per-result type must be `AssignableTo` the outer's declared
  result. IR-gen lowers to one OP_CALL + one OP_EXTRACT per
  result; the existing return-RefInc/copy + temp-cleanup
  machinery handles ownership transfer. The literal-shape
  coercions in the per-expr return path (OP_CONST_NIL retyping,
  OP_CONST_STRING → string_to_chars, untyped-int width) all
  fire only on literals, which can't be call results — so the
  multi-return path skips them. The one non-literal coercion,
  `@[]T → *[]T` when the outer expects raw, is preserved on
  extracted values, mirroring the per-expr path.
  - Tests: `pkg/types/check_stmt_test.bn` (positive, arity-
    mismatch, type-mismatch); `pkg/ir/gen_stmt_test.bn`
    (`TestGenReturnMultiCallEmitsExtracts` pins
    1×OP_CALL + 2×OP_EXTRACT); conformance
    `347_return_multi_call` (all-scalar + mixed scalar/managed
    end-to-end; was 345 originally, renumbered after collision
    with `345_interface_decl`). xfail.boot. boot-comp /
    boot-comp-int / boot-comp_native_aa64 all green.
- **Bootstrap (pending decision)**:
  `bootstrap/types/checker.go:checkReturnStmt` (~963-978) still
  rejects this shape. Bootstrap acceptance is a separate
  question — the bootstrap subset is intentionally restrictive,
  and the self-hosted toolchain doesn't need this to compile.
  Defer until there's a concrete reason to widen the subset.
- Spec recorded in `claude-notes.md` ("Tail-call return for
  multi-return functions"). `bootstrap-subset.md` notes the
  bootstrap-only rejection.

### Mirror `return f(...)` acceptance in the Go bootstrap — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

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
- **Mitigation in tree** (2026-05-22): xfailed via
  `scripts/unittest/pkg-asm-aarch64.xfail.builder-comp-int-int`.
  Coverage is preserved by `builder-comp`, `builder-comp-int`,
  `builder-comp-comp*` and the native_aa64 / arm32 modes —
  this is purely a double-interp pacing issue.
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

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### Interface syntax revision — *Stringer / @Stringer + top-level decl — MOSTLY DONE
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (RATIFIED 2026-05-01).
- **Implementation status (audited 2026-05-22 / 2026-05-23)**:
  Plan §1–§5 all landed.  §6 (`any` universal interface) landed
  end-to-end across type-checker (`e5f2f8a`) and IR-gen + codegen
  (`61eb6cd`): universe `any` is a real empty-method-set
  TYP_INTERFACE registered in both `pkg/types` (via
  `defineInterface`) and `pkg/ir` (via `registerUniverseAny` at
  `InitModule` time). `wrapAsIfaceValue` synthesizes a per-(T, any)
  ImplInfo on demand so codegen emits
  `__ivt.bn_<T_pkg>__<T>__any` as `[1 x i8*]` with T's dtor in
  slot 0 (or null if T has no dtor).  `@any` of a managed-field-
  bearing pointee now RefDec's the pointee's managed fields at
  scope exit via the synthesized vtable's dtor slot — the
  previously-silent leak is closed.
  Verified working: top-level `interface X { ... }` decl
  (`pkg/parser/parse_decl.bn:35`), `*Iface` / `@Iface` syntax
  (`pkg/types/resolve_type.bn:38-50`), bare-name rejection
  (`resolve_type.bn:30-35`, test 348), interface alias
  `interface X = Y` (test 369), construction-site explicit-only
  conversions (`types_assignable.bn:149-189`, tests 379/380/381),
  five receiver kinds + `impl T : Iface` (tests 357–410), per-
  (impl, interface) vtable codegen (`pkg/codegen/emit_impls.bn:24-40`),
  cross-package `.bni` interface visibility (tests 373–388, 464),
  universe `any` (tests 470–474, plus
  `pkg/ir/gen_iface_vtable_test.bn` for vtable-name mangling
  including the empty-pkg form).
- **Remaining (small) gaps**:
  1. **`type X = BareIface` explicit negative test** — the code
     flow should reject via `resolveTypeExpr`'s bare-interface
     error path, but it isn't separately covered. One-line
     negative test.
  2. **Interface-value nil comparison** — `iv == nil` (for any
     iv type, not just `*any`) is currently rejected:
     `IsNillable` in `pkg/types/types_query.bn:196` returns true
     only for pointer types and function-value types.  A nil iv
     IS a meaningful runtime state (both data and vtable slots
     zero, mirroring `*func(...)`'s convention), so the natural
     extension is to add `TYP_INTERFACE_VALUE` /
     `TYP_INTERFACE_VALUE_MANAGED` to `IsNillable`'s positive
     set and check both slots zero at the comparison site
     (codegen + VM lowering for `iv == nil`).  Not a regression;
     pre-existed plan §6 — surfaced while writing a nil-
     propagation test for the iv→any upcast.  This is a real
     language-semantics extension that should be confirmed
     before implementing.

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

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### ~~`const` type modifier~~ — Stages 0–2c LANDED; Stage 3 deferred
- Stage 0 (syntax + TYP_CONST wrapper kind), Stage 1 (enforcement
  + cast drops), Stage 2a (reject `string → *[]char`), Stage 2b
  (implicit alloc+copy for `@[]char = "..."`), and Stage 2c (string
  literal natural type `[N]const char`, default `@[]const char`,
  array-init copy `var s [N]char = "..."`, managed-slice + raw-slice
  composite literals `@[]T{...}` / `*[]const T{...}`) all landed.
- Stage 3 (const method receivers) deferred — depends on the
  methods/interfaces feature.
- Ratification: Phase 3 of the composite-literal generalization plan
  (next entry) supersedes the spec for *how* string literals lower at
  the IR level. The semantic surface is fixed.

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

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package-name/path conventions item below.
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
  not implementation. Decisions are interleaved with the name/path
  conventions item below — sketch and conventions probably ratify
  together.

### Package name/path conventions — decide and possibly reorganize
- Current `pkg/` layout mixes toolchain internals (`pkg/parser`,
  `pkg/types`, `pkg/codegen`, …) with runtime (`pkg/rt`), bootstrap
  support (`pkg/bootstrap`), libc bridges (`pkg/libc`), and small
  utilities (`pkg/buf`, `pkg/mangle`, …). Future stdlib packages would
  pile in alongside them with no organizing principle.
- Questions to answer:
  - Should toolchain internals live under a distinct prefix
    (`compiler/parser`, `compiler/types`, …) so that "what's stdlib"
    vs. "what's compiler implementation" is visible at the import
    path? Same question for runtime / bootstrap support.
  - What does a Binate package path *look* like? Is `pkg/` a real
    prefix or just a directory convention? Are external (third-party)
    packages spelled differently?
  - How do package paths interact with the package manager's naming
    scheme (URL? registry name? short alias)?
  - Mangling: short package names (`mangle.PkgShortNameFromModule`)
    currently derive from the path's last segment. If conventions
    change, mangled symbol names change, which affects ABI. Plan a
    migration story.
  - Are there packages that should move? `pkg/bootstrap` is arguably a
    stdlib piece; `pkg/rt` is closer to runtime-internal; toolchain
    internals could become `compiler/...`. Each move is a real refactor.
- Heavily entangled with the package-manager sketch — they should
  probably ratify together, since the manager design depends on what
  paths look like.
- Output: a plan / decision doc in `explorations/`. Reorganization is
  a follow-up project.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

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

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

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

### REPL — All five tiers LANDED (2026-05-29)
- **Status**: `bni --repl <file.bn|dir>` ships.  `plan-repl.md` is
  the live source of truth for per-step state — commit tables,
  verified behaviors, deviations from the original plan, and the
  per-tier remaining-follow-ups list.  Briefly:
  - **Tier 1 (load-then-poke)** LANDED.
  - **Tier 2 (top-level decls at the prompt)** LANDED in full,
    including the body-introduced dtor-regen follow-up landed
    2026-05-28 (`EnsureReplBodyHelpers`).  Every top-level decl
    kind supported by the language works at the prompt: `func`
    (incl. methods, redefinition replace + shadow), `const`
    (single, untyped, grouped), `var` (typed,
    untyped-with-literal-init, with init), `type` (aliases,
    named non-struct, structs incl. managed-field).  Bodies that
    introduce a fresh managed-aggregate shape with a destructible
    element (e.g. `@[]@Bag`) have their helper emitted before the
    body lowers.
  - **Tier 3 (forward refs)** LANDED for `func` decls.  Pending
    types / vars / consts (need a structural treatment of
    "unsized" type symbols) are deferred.
  - **Tier 4 (redefinition)** LANDED for both replace and shadow
    paths, free funcs and methods.
  - **Tier 5 (mid-session imports)** LANDED 2026-05-29 via
    `78685ac3`.  `import "pkg/foo"` at the prompt loads pkg/foo
    transitively, type-checks, IR-gens, lowers, and defines the
    package symbol in the session scope.
- **Remaining REPL work**, per plan-repl.md:
  - ~~**Tier 3**: pending types / vars / consts; cycle
    detection.~~  **ALL STAGES LANDED** 2026-05-28 → 2026-05-29
    via 9 commits on main; see
    [`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md)
    for the per-stage commit table.  Every top-level decl
    kind parks on forward-referenced dependencies; use-site
    propagation works through sized contexts (struct field,
    var decl, func sig, composite literal, impl recv, method
    receiver); per-caller sized-vs-reference distinction
    preserves recursive types via pointers; cycle detection
    catches genuine cycles through sized fields with a clean
    `pending cycle: A -> B -> A` diagnostic.
  - **Tier 4**: refcount-aware shadow warning (today fires
    unconditionally); forced-shadow escape hatch (syntax TBD per
    `claude-notes.md`).
  - ~~**Tier 5**: loader entry point for "load this one package
    now."~~  LANDED 2026-05-29 — `evalReplImport` in
    `cmd/bni/repl_import.bn` drives it via the session loader's
    existing LoadImports (plus a SaveAliasMapState /
    RestoreAliasMapState bracket around the per-package InitModule
    loop so the main alias map survives the wipes).
  - **Pretty-printer** (`pkg/replprint`) — **deferred** until
    interfaces land.  `bootstrap.println` is a temporary hack;
    building features on top of it would entrench it.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.  *(Now: Tier 3 added a
    pending queue (`check_pending.bn`) for `func` decls; types / vars
    / consts still fire immediately.)*
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.  *(Still true; deferred — see above.)*
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
    *(Now: Tier 4 substrate (`9af2d56`) added the funcIndex hash;
    `LookupFunc` is O(1).  Eager CallCache fill keeps shadow
    semantics correct.)*
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names, per-step commit tables,
  and the live follow-up state):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts immediate-mode entries.
     Multi-line input via paren-aware accumulator.  Auto-`println`
     wrap of bare exprs deferred (gated on interfaces).
  2. ~~**Add new top-level decls at the prompt.**~~ **FULLY LANDED
     (2026-04-30 → 2026-05-28).**  All decl kinds: `func` (incl.
     methods), `const`, `var` (typed + untyped-with-literal-init +
     var-initializer evaluation), `type` (aliases, named
     non-struct, structs incl. managed-field).  Body-introduced
     new-managed-aggregate dtor regen also landed (2026-05-28,
     `EnsureReplBodyHelpers`).
  3. ~~**Forward references.**~~ **LANDED for `func` decls
     (2026-05-05).**  Pending-validation queue in the type checker;
     parked decls retry on every newly-resolved name.  Pending
     types / vars / consts remain (see follow-ups above).
  4. ~~**Redefinition.**~~ **LANDED in full (2026-05-01 →
     2026-05-05).**  Compatible-sig: in-place rebind keeps
     CallCache valid.  Incompatible-sig: `LowerOneFuncShadow`
     appends + re-points funcIndex; old callers retain old VMFunc
     via eager-filled CallCache.  Methods follow the same rules,
     keyed on qualified `<pkg>.<TypeName>.<Method>`.  Substrate
     `9af2d56`; shadow `63cc49b`; method redef `026ad22`.
     Refcount-aware shadow warning + forced-shadow escape hatch
     are remaining follow-ups.
  5. ~~**Mid-session imports.**~~  **LANDED** 2026-05-29 via
     `78685ac3`.  evalReplImport in cmd/bni/repl_import.bn
     drives the existing loader's LoadImports for incremental
     transitive loads, brackets the per-package InitModule
     loop with SaveAliasMapState/RestoreAliasMapState so the
     session's main alias map survives, and routes through
     c.RegisterReplImport to make `foo.X` resolvable from
     subsequent prompt entries.
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (`layout-extraction-plan.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

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
