# Stage 4 Step 4: native x64 SSE eightbyte pass/return (implementation plan)

Companion to `plan-hfa-crossbackend.md`. The LLVM-codegen half (Steps 2a–2c) and
the asm movers (Step 3, `Movlps_load/store` `0F 12/13`, `Movss_load/store`
`F3 0F 10/11`) are landed dormant. Step 4 teaches the NATIVE x64 backend to
pass/return `<=16`-byte SysV float aggregates in XMM registers, matching the LLVM
side. Everything gated on a new `cc.SseAggregates` flag (= `types.SysVSseInRegs()`)
+ `types.SysVInSse(t)`, dormant until the Step-6 flip. Derived from the
2026-07-04 native-x64 survey workflow.

## The ABI shape (why x64 ≠ aa64)

aa64 HFA = N homogeneous same-width FP members in N consecutive FP regs (one
`cc.HfaAggregates` FP-only walk). x64 SysV = up to 2 eightbytes, EACH
independently SSE (→ next XMM / NSRN) or INTEGER (→ next GP / NGRN), so ONE
aggregate can consume BOTH an XMM and a GP reg, and the two cursors advance
independently (`{double,i64}` → XMM0+GP; `{i64,double}` → GP+XMM0). So x64 needs
its OWN per-eightbyte walk — it reuses NO aa64 HFA emitter code (only the CallConv
flag/accounting *pattern*).

## Central design

1. **`cc.SseAggregates bool`** added to the shared `CallConv` struct
   (`pkg/binate/native/common/common.bni`), set `cc.SseAggregates =
   types.SysVSseInRegs()` in `SysV_AMD64()` (`common_callconv_ctors.bn:~73`,
   mirroring AAPCS64's `cc.HfaAggregates = types.HfaInSimd()`). x64-only; aa64
   leaves it false. This flag gates every x64 SSE site.

2. **Accounting (highest-leverage, SHARED):**
   `common_callconv.bn:argRegWordsStackWords` (~92–161) has an HFA branch (~108–114)
   that models an aggregate consuming FP regs. Add a PARALLEL SSE branch: when
   `cc.SseAggregates && types.SysVInSse(t)`, walk `SysVClassify(t)` — each `EB_SSE`
   eightbyte consumes an NSRN (XMM), each `EB_INTEGER` an NGRN (GP). Thread through
   `advanceNgrn`/`advanceNsrn`/`CallArgRegStart`/`CallArgStackOff` so
   `CallStackBytes`/offsets stay correct (incl. register-exhaustion → stack). This
   is the single change every native site depends on for correct offsets.

3. **New x64-specific emitter** `emitSseAggEightbytes_x64(a, ptrReg, t, &ngrn,
   &nsrn, ...)`: given a base pointer reg + type, for each eightbyte: `EB_SSE` →
   `Movlps_load`/`Movss_load` (by form: SEB_DOUBLE/2FLOAT → MOVLPS 8B; SEB_FLOAT →
   MOVSS 4B) into XMM[nsrn]; `EB_INTEGER` → MOV into GP argReg[ngrn]. Returns
   advanced cursors. The store direction (params/return-into-image) uses
   `Movlps_store`/`Movss_store`. Host it where nsrn is already threaded (e.g.
   `x64_funcvalue_shim.bn` / a shared x64 helper file).

4. **Return predicate:** an x64 `ReturnsSseInRegs(t)` analogue of
   `common_callconv_return.bn:ReturnsHfaInRegs` (gated `cc.SseAggregates &&
   NumFpRetRegs>0 && SysVInSse`), so `NeedsRetbuf`/sret classification exempts an
   SSE aggregate (it returns in XMM0/XMM1 + RAX/RDX per eightbyte, not via retbuf).

## Sub-steps (each dormant-landable; mirrors aa64 Stage 2's return/shim/spill split)

- **[LANDED 0831eba9 + 6847689e] Native SSE RETURN round-trip:** pack (x64_return.bn emitSseAggregateReturnPack) + collect (x64_call.bn collectSseAggregateReturn) — SSE ebs XMM0/XMM1, INTEGER ebs RAX/RDX. Dormant byte-identical; native Rosetta round-trip correct (mkD2->30, mkFD->7, mkDI->5/42, incl. mixed {double,i64}); both minimal-reviewed clean. NOTE: caller-side is a native<->native self-consistent check; the cross-module native<->LLVM gate is Step 5.
- **[was 0831eba9] Native SSE RETURN pack:** `cc.SseAggregates` flag + `ReturnsSseInRegs` + `emitSseAggregateReturnPack` (x64_return.bn) — SSE ebs -> XMM0/XMM1 (MOVLPS/MOVSS), INTEGER ebs -> RAX/RDX. Dormant byte-identical; flip-objdump verified {double,double}/{float,double}/mixed; minimal review clean. (Bundled the flag+predicate+return-emitter; the arg-accounting below is still to do.)
- **4a — CallConv accounting (foundation):** `common.bni` `SseAggregates` field +
  `SysV_AMD64()` set + `argRegWordsStackWords` SSE branch + `ReturnsSseInRegs`.
  No emit yet; pure classification/accounting. Dormant byte-identical. Unit-test
  the accounting (an SSE aggregate consumes the right XMM/GP counts; mixed cases).
- **4b — returns + params:** `x64_return.bn` `emitSseAggregateReturnPack` (SSE/HFA
  branch must PRECEDE the general-aggregate branch); `x64_emit_func.bn`
  `spillIncomingParams` eightbyte-store to the param's data-region image
  (`RSP+dataOff+8*eb`).
- **[LANDED 2be49c24 (971) + 79ead028 (4c-1) + ac6faa80 (4c-2)] 4c — call args + param spill:**
  - *4c-1 accounting (79ead028):* the dual-file cursor (the #1 hazard).
    `argRegWordsStackWords` SSE branch places INTEGER eightbytes in GP (from ngrn),
    SSE eightbytes in XMM (from nsrn); the all-or-nothing both-files fit lives in ONE
    predicate `sysvSseAggFitsInRegs` that both `argRegWordsStackWords` and
    `advanceNsrn` consult (advanceNsrn now takes ngrn; the walkers advance nsrn before
    ngrn so it reads the pre-advance ngrn).  Split `types.SysVInSse` into the gate +
    a gate-free `types.SysVAggHasSse` so the native predicates (`PassesSseInRegs`,
    `ReturnsSseInRegs`) are field-gated and the accounting is unit-testable with the
    field forced on (common_callconv_sse_test.bn: counts, fit/overflow, dual-file cursor).
  - *4c-2 emit (ac6faa80):* new `x64_sse.bn` — `emitSseAggregateArg` (caller: image ->
    XMM/GP arg regs) + `spillSseAggregateParam` (callee: arg regs -> data image),
    MOVLPS/MOVSS by form, each returning the XMM count so caller/callee NSRN stay in
    step; wired into `x64_call.bn` arg loop + `x64_emit_func.bn` param spill.  Unit
    tests x64_sse_test.bn.
  - *Verified:* unit tests green; conformance/971 dormant-green (builder-comp +
    native_x64_darwin); under a temporary flip, 971's native build (native main / LLVM
    dep, Rosetta) matches the all-LLVM reference on EVERY line — return collect + arg
    marshal + native param spill all cross-module-correct.
- **4d — dispatch shims:** `x64_funcvalue_shim.bn`, `x64_closure_shim*.bn`,
  `x64_iface.bn` — the by-address/retbuf dispatch convention is UNCHANGED (SSE
  aggregate still arrives as one i8* pointer word); only the shim's move of the
  pointer-image eightbytes INTO the underlying's registers eightbyte-walks
  (SSE→XMM, INT→GP), exactly mirroring LLVM 2c's `writeShimUnderlyingArg`.

## Open questions / risks (from the survey)

- **Mixed SSE+INTEGER register accounting** is the main hazard: the two cursors
  (NGRN/NSRN) must advance independently and match what LLVM emits. Verify
  `{double,i64}`, `{i64,double}`, `{f32,f32,i32}` register assignments vs clang.
- **Register exhaustion:** when XMM args run out (>8 SSE eightbytes across args)
  the eightbyte spills to stack — must match SysV. Rare but must be correct.
- **VM cross-mode dispatch is ABI-NEUTRAL** (by-address args + retbuf returns), so
  `rt._call_shim_*` / `IsAggregateReturn` / `AggregateReturnSize` need NO change
  for x64 SSE — same as aa64. Confirm no `AggregateReturnSize` divergence.
- **Verification:** temporary gate flip + `builder-comp_native_x64_darwin`
  (Rosetta) conformance + a native-main↔LLVM-dep cross-module program (the REAL
  gate — a single-program native-vs-native check is self-consistent even if wrong).

Landing discipline: each sub-step reviewed + dormant byte-identical + Rosetta
before landing. Flip is Step 6, blocked on 4+5.

## Step 5 findings (cross-module native↔LLVM, Rosetta) — return path

Verified with a temporary gate flip + a native-`main`/LLVM-`dep` two-package
program under Rosetta (x86_64-darwin), diffing `--backend native` (main native,
dep LLVM) against the all-LLVM reference build.  Harness in /tmp/sse_xmod
(forward) and /tmp/sse_rev (reverse); the gate was reverted after each run
(main stays dormant).

- **Forward (LLVM PACK → native COLLECT), GREEN.** A native `main` calls
  LLVM-compiled no-arg `Mk*` that return SSE aggregates; native collects via
  `collectSseAggregateReturn` (4b).  Covered 1×f64, 1×f32, 2×f32, [f64,f64],
  [2×f32,2×f32], [2×f32,f64], and the mixed dual-file cases [f64,i64] /
  [i64,f64] / [2×f32,i64] / [f64,{i32,i32}].  All fields read back via
  `bit_cast` (exact bits, sidestepping the separate native-float32-arith gap)
  matched the all-LLVM reference EXACTLY; the flip changed the native binary
  (cmp differs) and the SSE build heavily uses XMM — so this really exercised
  the SSE collect, not a GP fallback.  This is the REAL ABI gate for 4b (beyond
  the self-consistent native↔native round-trip).

- **Native RETURN PACK (4a) independently verified CORRECT** by disassembly:
  `myD2` (returns {f64,f64}) emits `movlps (%r10),%xmm0` / `movlps
  0x8(%r10),%xmm1` — x→XMM0, y→XMM1 per SysV.

- **Reverse (native PACK → LLVM COLLECT) is blocked on 4d, NOT a 4a/4b bug.**
  Passing a native func returning an SSE aggregate as a `*func` to an LLVM `dep`
  that calls it mismatches under the flip — because the native FUNC-VALUE SHIM
  (`_bn_…__shim`) still GP-collects the underlying's return
  (`callq myD2; movq %rax,(%r10); movq %rdx,0x8(%r10)`) instead of eightbyte-
  walking XMM0/XMM1.  That is precisely the unimplemented 4d shim work; on
  `main` (gate dormant) the shim's GP-collect matches the GP-pack, so there is
  no live defect.  **4d localization: the shim's post-underlying-call collect
  must eightbyte-walk (SSE→XMM, INT→GP) — mirror `collectSseAggregateReturn`.**

Still needs Step 4 before FULL Step-5 conformance: 4c call-arg placement +
param spill (arg direction, both ways) and 4d dispatch-shim SSE collect.  The
`builder-comp_native_x64_darwin` full-conformance mode is gated on those.
