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
  aggregate still arrives as one i8* pointer word); only two moves inside the shim
  become eightbyte-class-aware: (i) the shim's expansion of the by-address
  pointer-image INTO the underlying's registers (SSE→XMM, INT→GP, mirroring LLVM
  2c's `writeShimUnderlyingArg` / native `emitSseAggregateArg`), and (ii) the
  shim's COLLECT of the underlying's SSE return into the retbuf. Broken into:
  - **[LANDED e321f57d] 4d-1 func-value shim RETURN collect:**
    both shim shapes (register-only `x64_funcvalue_shim.bn` pack + over-budget
    `x64_funcvalue_spill.bn`) branch on `cc.ReturnsSseInRegs` and call the new shared
    `emitSseReturnCollectTo` (x64_sse.bn) — XMM0/XMM1 + RAX/RDX → retbuf by class;
    `collectSseAggregateReturn` (direct call) delegates to it too. Fixes the exact
    Step-5 gap (a native `*func` returning an SSE aggregate, called by an LLVM caller,
    delivered garbage). Verified: unit tests + new conformance/972_xpkg_funcval_sse
    dormant-green (builder-comp / native_x64_darwin / native_aa64) and flip-all-match.
  - **4d-arg — func-value shim ARG marshal** (an SSE aggregate PARAM expands the
    by-address image into XMM/GP by class):
    - **[LANDED b7b09c6e] 4d-arg-1 register-only shim:** `emitSseShimArgFromPtr`
      (x64_sse.bn) wired into `emitShimArgMarshal_x64`. The shim's outgoing budget
      still counts an SSE aggregate as its full ArgWords eightbytes — deliberately
      CONSERVATIVE (guarantees the whole aggregate fits in registers when the
      register-only shim is chosen, so no MEMORY tail); tightening it to count only
      INTEGER eightbytes is a perf follow-up. Conformance/981_xpkg_funcval_sse_arg
      (D2/DI/ID-INTEGER-first/F2), dormant-green + flip-all-match; adversarially
      reviewed clean (budget-safety confirmed airtight).
    - **[LANDED bf5e4feb] 4d-arg-2 spill shim:** the over-budget shim's
      `emitSpillCoercedAgg_x64` now expands an in-register SSE aggregate into XMM/GP by
      class via emitSseShimArgFromPtr (SSE eb -> XMM(nsrn), INTEGER eb -> argReg(gpDestBase+rs)),
      with nsrn threaded in/out of the marshal loop; a MEMORY-class aggregate keeps the
      class-agnostic byte-copy tail. Conformance/984_xpkg_funcval_sse_spill (pure D2 +
      mixed DI, each with four trailing int64 forcing the spill path); gate-forced-on
      unit tests in x64_funcvalue_spill_test.bn. Adversarially reviewed clean (nsrn
      lockstep + no-XMM8+ confirmed). Landing rebased through a concurrent bnfmt reformat
      conflict (resolved: my nsrn semantics, bnfmt-wrapped) + a 982->984 renumber.

  **Func-value family is now SSE-complete** (4d-1 return collect + 4d-arg-1/4d-arg-2 arg
  marshal). Remaining: **closure (4d-2)** and **iface (4d-3)** families.
  - **4d-2 — closure shim** (`x64_closure_shim*.bn`): same two moves (arg expand +
    return collect).
    - **[LANDED 7f7d6d99] 4d-2-ret — return collect:** THREE sites — a closure returning
      an SSE aggregate has separate register-only-no-float, spill-no-float, and
      float-parts shims (the dispatcher routes to the float shim iff
      closureHasFloatParts_x64: any float SCALAR capture/param/scalar-return). All three
      now branch cc.ReturnsSseInRegs -> emitSseReturnCollectTo. Conformance/985 (float-,
      int-, and 7-int64-capture closures returning D2/DI, hitting all 3 sites),
      flip-all-match; reviewed clean (a first pass missed the float-parts site — 985
      caught it via disassembly).
    - **[LANDED 4afa4d80] 4d-2-arg — arg/capture marshal:** rather than thread NSRN
      through the GP-only fast shim's right-to-left marshal, ROUTE any SSE-agg
      param/capture closure to the float-aware shim (which already marshals per class
      with NSRN) — closureHasFloatParts_x64 returns true for a SysV-SSE aggregate
      capture/param, GATED on SysVSseInRegs() (dormant routing byte-identical). So the
      fast + no-float aggregate shims never see an SSE arg under the flip.
      marshalFloatShimArg_x64 got an in-register SSE branch (RAX address scratch, not
      R11 — the aggregate-return float shim's struct base is R11; an earlier R11 draft
      mis-marshalled a capture after an SSE-agg capture, caught in review + negative-
      tested). Conformance/986_closure_sse_arg (param, capture, mixed, wide, and the
      aggregate-return capture-clobber regression). KNOWN GAP: a MEMORY-class SSE arg
      (needs 9+ SSE eightbytes) is untested — unchanged class-agnostic byte-copy.

  **Closure family SSE-complete** (4d-2-ret + 4d-2-arg). Remaining: **iface (4d-3)**.
  - **[LANDED 76fbcd51] 4d-3 — iface** (`x64_iface.bn`): `emitCallIfaceMethod`'s
    arg loop routes an in-register SSE aggregate through `emitSseAggregateArg`
    (XMM/GP by class) and its aggregate-single-return collect through
    `collectSseAggregateReturn` — mirroring `emitCall`, sharing the same helpers.
    Both gated on `cc.PassesSseInRegs` / `cc.ReturnsSseInRegs` (dormant). The
    impl-shim-vtable path needs NO change: `emitOneImplShimVtable` emits pure
    handle-address data, iface-method handles marshal via func-value shims
    (already SSE-aware, 4d-1/4d-arg), and native iface calls dispatch directly to
    the method (callee param spill = 4c-2). The adversarial review surfaced a
    PRE-EXISTING bug in the same function's float-scalar arg handling (a float
    past XMM7 was silently dropped, not spilled; `nsrn` not advanced
    unconditionally) — FOLDED IN and fixed to mirror `emitCall` (overflow spill +
    unconditional `nsrn++` + `NumFpArgRegs`); byte-identical for ≤8-float calls.
    Conformance/987_iface_sse + 988_xpkg_iface_sse (swap 2xf64 SSE arg+ret, fold
    {f64,i64}<->{i64,f64} dual-file both ways, tag SSE-arg/scalar-int-return, and
    mix float-scalar->SSE-agg interleave pinning nsrn threading past the scalar —
    landed 774fdc8e), flip-all-match cross-module + negative-tested; gate-forced-on
    SSE unit tests (incl. the float-then-D2 XMM2 nsrn check) + float-overflow spill
    regression tests.  KNOWN GAP (as with closure 4d-2-arg): a MEMORY-class SSE arg
    (stackOff >= 0) rides the unchanged class-agnostic byte-copy path, untested.

  **Stage-4 dispatch COMPLETE** (direct 4c + func-value 4d-1/4d-arg + closure
  4d-2 + iface 4d-3).

  **[DONE] Step 5 — full `builder-comp_native_x64_darwin` conformance under a
  temporary flip (the comprehensive gate).** Ran the whole
  `builder-comp_native_x64_darwin-comp_native_x64_darwin` suite (native x64
  backend, executed under Rosetta) dormant then with `SysVSseInRegs()` flipped
  to `GetTarget().Arch == ARCH_X64`, and diffed.  Both runs: **2661 passed / 8
  failed / 7 skipped — IDENTICAL sets**; the flipped-vs-dormant regression set is
  EMPTY.  The 8 failures are pre-existing and unrelated to SSE
  (`731/733/736/737_build_*_select` + `stdlib/os/{006_readdir,008_stat_errors,
  009_stat,010_modtime_chain}`) — they fail both ways.  So the whole Stage-4 SSE
  plumbing (direct + func-value + closure + iface, arg + return, all eightbyte
  classes incl. dual-file) is correct across the ENTIRE suite with the SSE ABI
  active — not just the ~18 targeted 97x/98x tests.  Flip was reverted after
  (main stays dormant; the live flip is Step 6).

  **[DONE — LANDED ce759c41] Step 6 — flip `SysVSseInRegs()` ->
  `GetTarget().Arch == ARCH_X64`, making x64 SSE live** (mirrors the aa64
  HfaInSimd Stage-3 flip).  A ≤16-byte SSE-classified aggregate now rides XMM on
  x64 across the LLVM backend, the native x64 backend (all four dispatch shapes),
  and the VM cross-mode boundary; >16-byte float aggregates stay MEMORY.  No-op
  on non-x64 (arm64 byte-identical).  The stale dormant assertion
  `TestSysVInSseDormant` became `TestSysVSseGatedByArch` (SSE on for ARCH_X64,
  off otherwise, mirroring `TestHfaSimdGatedByArch`); the aa64 test's "(Stage 4
  pending)" reasons updated to "(x64 uses SysV-SSE, not AAPCS64 HFA)".  Verified:
  types unit tests green (arch-gated test incl.), BUILDER-compilable (gen1 built
  with the flip), arm64 conformance smoke green; correctness rests on Step 5's
  zero-regression full-suite gate.

  **The x64 SysV eightbyte-SSE project is COMPLETE.**  x64 now matches aa64: both
  pass small float aggregates in FP registers per their platform ABI, in lockstep
  across LLVM + native + VM.

  ## Follow-ups (deferred, tracked separately — "handle other stuff separately")

  - **Broader x64-mode flip verification — RESOLVED (CI-covered).** The flip
    affects ALL x64 targets; Step 5 exercised `native_x64_darwin` (runtime, green).
    The other x64 modes are NOT locally runnable on this arm64 macOS host but ARE
    exercised by CI on the ubuntu-latest **x86_64** runner with the flip live:
    - `builder-comp_native_x64-comp_native_x64` (x64 ELF/Linux native) — can't run
      locally (no Linux libc / qemu; the local attempt failed at the C-runtime
      LINK with `stdio.h not found`, i.e. AFTER `bnc` emitted the ELF objects
      cleanly — so the ELF x64 SSE *codegen* is locally confirmed, only the Linux
      runtime link is unavailable).  Runs natively on the x86_64 CI runner.
    - **LLVM-x64 SSE** needs no dedicated mode: on the x86_64 CI runner the DEFAULT
      modes (`builder-comp` etc., bnc's default backend = LLVM, target x86_64-linux)
      ARE the LLVM-x64 conformance run.  Also cross-validated locally by the
      /tmp/sse_* all-LLVM x86_64-darwin reference builds.
    Both CI modes are non-experimental (gating).  Definitive confirmation = a green
    conformance CI run on a commit >= the flip (ce759c41); the SSE register
    placement is object-format-independent, so an x64-linux-specific SSE regression
    is very unlikely.
  - **8 pre-existing `native_x64_darwin` failures + the `TestX64MachoExitsWithCode`
    panic — RESOLVED.** Triaged via a workflow (one agent per group).  Two causes:
    (1) `731/733/736/737_build_*_select` were NOT bugs — the x64 output is correct;
    `native_x64_darwin` is a CROSS mode so the host-arch `expected.x64` tier never
    fired.  Fixed with per-mode `expected.<MODE>` overrides (`2f0478c4`).  (2)
    `stdlib/os/{006_readdir,008_stat_errors,009_stat,010_modtime_chain}` + the
    `TestX64MachoExitsWithCode` panic were ONE real MAJOR bug — x86_64-darwin `os`
    linked the legacy 32-bit-inode stat/readdir libc symbols instead of the
    `$INODE64` variants.  Fixed os-side (`7049fe52`; see claude-todo-done.md).
    `native_x64_darwin` is now fully green (both are local Rosetta modes, NOT in CI).
  - **MEMORY-class SSE arg** (stackOff >= 0, needs 9+ SSE eightbytes) rides the
    unchanged class-agnostic byte-copy path across all dispatch families —
    untested (documented gap).  ← follow-up #3, in progress.

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
