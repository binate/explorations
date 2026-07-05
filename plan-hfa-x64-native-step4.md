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
- **4c — call args:** `x64_call.bn` arg loop (nsrn already at ~line 98) —
  eightbyte-walk the source aggregate's stack image into XMM/GP instead of the GP
  `emitAggregateArg`.
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
