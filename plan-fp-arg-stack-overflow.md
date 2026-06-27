# Plan — native FP-argument stack-overflow ABI (claude-todo #121's 707 root)

**Status:** 🟢 APPROVED, IMPLEMENTING (user approved fix-now 2026-06-21).
**Scope:** `pkg/binate/native/` only — no IR / type / codegen-LLVM / VM changes.

## The bug (CRITICAL, native-only, latent)

Any function with **more than 8 float-scalar arguments** silently miscompiles on
BOTH native backends. `sum9(a..i float64) float64` returns **36** (= sum of the
first 8) instead of 45 on native aa64 AND native x64; LLVM and the VM are
correct. The 9th float (which must overflow the 8 FP arg registers
D0–D7 / XMM0–XMM7 to the stack) is dropped by the caller and not read by the
callee. Discovered 2026-06-21 while scoping 707 (the closure manifestation).

It is **latent**: nothing in the current tree (or the self-hosted compiler)
uses `>8`-float-arg functions, so it breaks no build today. It is a correctness
landmine for future numeric code.

## Root cause (single point)

`CallConv.argRegWordsStackWords` (`common_callconv.bn:137`) returns `(-1, 0, 0)`
for *every* float scalar — "in an FP reg, no GP, no stack" — with **no NSRN
parameter**. The convention layer therefore cannot tell when the FP arg
registers are exhausted. Cascade:

- `CallStackBytes` reserves **no** outgoing-stack space for an overflow float.
- `CallArgStackOff` returns `-1` for it (no stack slot).
- The caller's float loop (`x64_call.bn:215`, `aarch64_call.bn:68`) places a
  float only `if nsrn < 8` — **no else**, so the 9th is dropped.
- The callee prologue (`x64_emit_func.bn:150`, `aarch64_emit_func.bn:85`) loads
  a float only `if nsrn < 8` — **no else**, so it reads nothing.

(Precedent: the darwin-variadic path already pushes *variadic* floats to the
stack via `argRegWordsStackWordsV`/`VariadicStackOnly` — the plumbing exists;
the fix generalizes it from "variadic float" to "any overflow float".)

## Fix design

### Convention layer (the core) — `common_callconv.bn`

Thread NSRN through the classifier and the walkers:

- `argRegWordsStackWords(t, ngrn, nsrn)` — add the `nsrn` param. For a float:
  `nsrn < NumFpArgRegs → (-1,0,0)` (FP reg); else `(-1,0,1)` (1 stack word).
- `argRegWordsStackWordsV(t, ngrn, nsrn, isVariadic)` — pass `nsrn` through; the
  existing variadic-float-on-stack branch is unchanged.
- The 6 walkers — `CallArgRegStart` / `CallArgStackOff` / `CallStackBytes` and
  their `…V` variants — track `nsrn` alongside `ngrn`, incrementing it by 1 per
  float scalar (`IsFloatScalarTyp(argTypes[k])`) and passing it into the
  classifier. Stack args (GP-overflow + FP-overflow) accumulate into `soff` in
  arg order, so overflow floats interleave correctly with overflow GP args.

This change is internal: `argRegWordsStackWords{,V}`'s signature is private to
`common_callconv.bn`; the public walker signatures are unchanged.

### Caller (place the overflow float on the outgoing stack)

`{x64,aarch64}_{call, call_indirect, iface}.bn` — in each float-arg loop, add
an else for `nsrn >= NumFpArgRegs`: store the float bits to
`[SP + CallArgStackOff(argTypes, i)]` (8-byte slot). Increment `nsrn` for EVERY
float (not just `nsrn < 8`) so the caller's NSRN matches the walker's.
aarch64's caller already has a stack branch (the variadic path) — generalize it.

### Callee prologue (read the overflow float from the incoming stack)

`{x64,aarch64}_emit_func.bn` — add an else for `nsrn >= NumFpArgRegs`: read the
float from the incoming-stack arg area at `CallArgStackOff(paramTypes, i)` into
the param's spill slot. The GP-overflow incoming-stack read machinery already
exists; reuse it.

## Staging

- **Commit 1 — ✅ LANDED (binate `dba4d287`, 2026-06-21):** convention NSRN
  threading + caller + callee, BOTH backends. conformance/885 (>8 float64), 886
  (mixed GP+FP overflow, interleaved), 887 (float32, all-native single overflow)
  green on native aa64/x64-darwin, LLVM, VM, gen2 + convention overflow unit
  tests. Also aligned the aarch64 caller's float predicate to
  `common.IsFloatScalarTyp` (the agreement is now load-bearing). Adversarial
  review (binate worktree, wf_d6a0cf7f): 0 in-scope critical/major; it surfaced
  the darwin narrow-stack gap below (separate, pre-existing).
- **Commit 2a — ✅ LANDED (binate `5b7a1335`, 2026-06-21):** the non-capturing
  func-value spill shim places the 9th+ float on the underlying's outgoing-args
  stack (`emitSpillMarshal*` overflow else; the slot is reserved because
  CallStackBytes now counts overflow floats). conformance/888.
- **Commit 2b (707 proper) — ✅ LANDED (binate `1ad9e00f`, 2026-06-22):** the
  SCALAR closure float shim handles FP overflow — it reserves an outgoing-args
  area below the user-arg spill (the overflow floats are the only stack args,
  since GP captures/params stay in regs per the guard → sequential 8-byte
  slots) and the shared marshaller routes the 9th+ float there. On aarch64 the
  FP/LR save moved to a top 16-byte frame so the outgoing-args area sits at
  [SP+0]. conformance/707 un-xfailed; new 891 (GP + 9-float-capture), 892
  (float32). The float-AGGREGATE-return overflow and combined GP+FP overflow
  stay loud SetErrors (rarer follow-ups; the aggregate guard carries a
  do-not-relax-without-reserving-a-frame warning). Adversarial review
  (wf_902997e9): 0 critical/major; minors (stale 707 comment, marshaller/guard
  coupling, overflow unit test) addressed.
- **Commit 3 (darwin narrow-stack ABI) — ✅ LANDED (binate `e9474185`,
  2026-06-22; user chose land-core-+-track):** a FIXED narrow scalar (int32 /
  float32) stack arg now packs at its natural 4-byte size + alignment on
  AAPCS64_Darwin (`NaturalSizeStackArgs` flag + `stackArgFootprint` in the offset
  walkers). The direct-call caller AND the iface call site store a narrow stack
  arg at natural width; the scalar closure float shim places its overflow floats
  at the matching natural offset/width (now uses AAPCS64_Darwin).
  conformance/897 (cross-pkg direct call) un-broken, 895 (iface narrow stack — a
  CRASH the review caught), convention natural-size unit tests; full native aa64
  2301/0; x64/LLVM/VM unaffected. Adversarial review (wf_eebd46ad): caught a
  CRITICAL iface-store crash regression (fixed) + the int8/int16 gap below; 2
  "callee over-read" findings were verified false positives (the 8-byte read is
  harmless).
  - **GAP A — func-value-narrow (`894`) — ✅ RESOLVED (binate `885633f9`,
    2026-06-22):** the func-value path now agrees on natural-size narrow stack
    args (the dispatch substitutes a narrow scalar to a uniform 8-byte *uint8
    INCOMING word — which also fixed an X17-clobber SIGSEGV: a 64-bit Str of a
    narrow int32 at a 4-misaligned offset materialized `add x17, sp, #imm` over
    the BLR target; the spill shim uses AAPCS64_Darwin + a 32-bit OUTGOING store).
    894 un-xfailed, new 901 pins the 9-int32 / 9-/10-float32 boundaries. A
    1171-line objdump-confirmed investigation (wf_cf874a3e) + implementation
    review (wf_d7ec7f92) drove it; the review's PlanFrame-under-reserve finding
    was verified a false positive (the frameDelta coupling is correct).
  - **GAP B — int8/int16 — ✅ RESOLVED (binate `dd354aac`, 2026-06-22):**
    `StackArgNarrow4` handled only 4-byte scalars; int8/int16 kept the 8-byte
    slot and mismatched LLVM's 1/2-byte packing — the SAME wrong-offset
    miscompile class as 897, for narrower widths.  Generalized the convention:
    `StackArgNaturalSize` (1/2/4) → `stackArgFootprint` packs at `(n, n)` (so
    every offset walker goes natural) + `StackArgStoreSize` gives the matching
    store width; new `aarch64.StrSized` dispatches STRB/STRH/STR.  Natural-size
    slots are naturally aligned, so the scaled-imm store always fits (no X17
    materialization).  Adopted at all 6 marshalling sites (direct caller, iface,
    func-value spill, GP + float closure shims) and the func-value dispatch
    substitution widened to `SizeOf()<8`.  896 un-xfailed; 902 (mixed
    int8/int16/int32 cross-pkg, offsets 0/2/4/8/10/12, self-validating vs LLVM)
    and 903 (native shim paths) added; unit test `TestStackArgNaturalSizeSubWord`.
    Full aa64 conformance green; x64 byte-identical (NaturalSizeStackArgs off).
    Verified correct+complete by adversarial review wf_759b0e85 (the recurring
    `emitCallIndirect:103` flag is a refuted false positive — uniform-8 dispatch
    words only).
  - **GAP C — GP-only capturing-closure stack-spill shim — ✅ RESOLVED (binate
    `cb58ece5`, 2026-06-22; reproduced 306 vs 310 native aa64, review
    wf_d7ec7f92):** `emitClosureShimStackSpillAA64`
    (`aarch64_closure_shim.bn`) marshalled a capturing closure's `>8` GP
    args/captures to the lifted body via plain `AAPCS64()` + 64-bit stores, but
    the body reads a narrow int32 overflow arg at natural-4 → silent wrong-code.
    Fix mirrors the others: `emitClosureShim` + the inert fast path →
    `AAPCS64_Darwin()`; `StackArgNarrow4`-aware 32-bit stores in BOTH the Step-1
    capture spill and the Step-2 user-arg moves (`emitUserArgWordMoveAA64`). New
    `899_closure_narrow_stack_arg` pins both paths (6 int caps + 4 int32 args →
    121 was 81; 8 int caps + 3 int32 caps → 736 was 536; the 3rd narrow capture
    is needed — 2 adjacent narrow captures come out right by accident via an
    8-byte struct over-read carrying the next field into the +4 slot). Verified
    correct+complete by adversarial review wf_0fa6908a (fix-correctness clean; a
    sweep-flagged `emitCallIndirect` "critical" was a sound false positive —
    `OP_CALL_INDIRECT` only carries uniform 8-byte dispatch words from
    `rt._call_shim_scalar/_aggregate`, never narrow scalars).
  - **GAP D — aggregate / float-aggregate closure stack-spill shims:**
    - **GP-aggregate — ✅ RESOLVED (binate `b78819a1`, 2026-06-26):**
      `emitClosureShimAggregateAA64` (`aarch64_closure_shim_aggregate.bn`) now
      has a real stack-spill path (`emitClosureShimAggregateStackSpillAA64`) on
      `AAPCS64_Darwin()` + natural-size stores, instead of `a.SetError`.  Frames
      an FP/LR prologue (+ pack retbuf slot), stashes retbuf→X8 (sret)/slot
      (pack) + data→X9, marshals captures + user args (shared
      `emitClosureCaptureSpill/RegLoadAA64` helpers; `emitUserArgWordMoveAA64`
      generalized with prefixRegs+frameDelta), BLs, routes the result through
      the retbuf.  Aggregate USER args handled too: a SPLIT branch +
      `EffectiveArgWords` (round-2 review wf_25bd5ddf caught both as critical
      pre-land bugs — the SPLIT arg emitted nothing; an indirect-large arg
      over-looped → crash).  Tests 906 (pack+sret, scalar args) / 907 (SPLIT
      Pair + indirect-large Triple).  xfail'd on native x64, whose aggregate
      shim still `SetError`s (the x64 analogue — tracked, claude-todo).
    - **Pre-existing scalar X16-LR defect (discovered here, NOT GAP D's) — ✅
      RESOLVED (binate `1b6335b1`, 2026-06-26):** the round-2 review surfaced a
      separate CRITICAL crash — the SCALAR closure stack-spill shim
      (`emitClosureShimStackSpillAA64`) was a leaf shim that preserved LR across
      the BL in X16 (caller-clobbered IP0), which an indirect-large user arg's
      pointer-deref trashed → SIGSEGV on return.  Fixed by converting the scalar
      shim to a framed `STP FP,LR / LDP` prologue (LR on the stack;
      `frameDelta` → `16 + stkBytes`), like the aggregate/float shims already
      were.  Pinned by `908_closure_scalar_indirect_large_arg`; reviews
      wf_474bbc47 (no other LR-in-scratch site) + wf_82e18923; full aa64
      conformance 2412/0.
    - **Float closure shims (scalar + aggregate) — ✅ RESOLVED (binate
      `ba9555b9` + `69c6984e`, 2026-06-27):** both `emitClosureShimFloatAA64`
      and `emitClosureShimFloatAggregateAA64` (`aarch64_closure_shim_float.bn`)
      now handle GP / FP / incoming-stack overflow instead of `SetError`.  The
      shared marshaller `loadClosureFloatCallArgsAA64` was rewritten
      classifier-driven (float→D/FP-overflow, GP→reg/stack-overflow at
      `CallArgStackOff`, natural-size, SPLIT-aware, `EffectiveArgWords` — which
      also closes the closure-shim `ArgWords` facet for the aarch64 float
      shims, see claude-todo); both shims reserve the outgoing-args area at
      `[SP+0..]`, spill incoming args from the regs + the caller stack, on
      `AAPCS64_Darwin()`.  Tests 912-916 (scalar) + 919/920 (aggregate).
      Reviews wf_bb2f5df3 (marshaller + scalar) + wf_8940fb46 (aggregate),
      both clean.  x64's float shims still `SetError` (the x64 analogue —
      claude-todo:702 / the x64 aggregate-shim follow-up).

## Tests

- Plain `sum9(... 9×float64) → 45`, and a 9-float32 variant (the 32-bit path),
  both native arches + LLVM + VM (commit 1).
- Mixed GP+FP overflow (e.g. 7 ints + 9 floats) so GP-stack and FP-stack args
  interleave (commit 1).
- 707: `>8`-float-capture closure (commit 2).

## MAJOR — narrow (float32 / int32) stack-overflow args miscompile across the native↔LLVM boundary on darwin (CONFIRMED, pre-existing) — 🟢 fix planned AFTER 707

CONFIRMED by the commit-1 adversarial review (verified against Apple clang 21,
`-target arm64-apple-darwin`): the native convention uses a fixed **8-byte
stride** for EVERY stack-overflow arg (`argRegWordsStackWords` returns
`(-1,0,1)` for an overflow float and every walker does `soff += sw*8`; ArgWords
floors at 1 word = 8 bytes for narrow ints too). But Apple-AArch64 LLVM packs
**narrow** stack args at their **natural size**: a float32 / int32 stack arg
takes 4 bytes. clang on darwin: an 11-float32 callee reads `ldr s,[sp]; ldp
s,s,[sp,#4]` (offsets 0,4,8), an 11-int32 callee reads `ldr w,[sp]; ldp
w,w,[sp,#4]`. So the 2nd+ narrow stack-overflow arg sits at the wrong offset
across a native-main / LLVM-dep boundary → silent wrong values.

- **NOT a commit-1 regression.** The 8-byte stride pre-existed in the
  GP-overflow machinery (already mis-handles int32 on darwin); commit 1's float32
  path merely stays consistent with it. **Latent** — nothing in the tree hits
  it; float64 (8-byte both sides) and x86-64 SysV (clang pads float32 stack args
  to 8) are fine. Darwin-AArch64 (the primary native target) + narrow-arg only.
### Precise rules (confirmed via Apple clang, 2026-06-22)

- **Darwin AArch64 FIXED stack args** pack at NATURAL size + alignment: int32 /
  float32 → 4 bytes (4-aligned); int64 / float64 → 8 bytes (8-aligned, so a
  narrow arg before an 8-byte one leaves a gap — `gmix(...,int32,int64,int32)`
  reads `[sp+0], [sp+8], [sp+16]`). int16/int8 → 2/1 bytes.
- **Darwin VARIADIC stack args stay 8-byte** (`stp x9,x8,[sp]` for variadic ints
  — printf is UNAFFECTED, do NOT touch the VariadicStackOnly path).
- **x86-64 SysV** pads narrow stack args to 8 (clang) — UNCHANGED. So this is
  **AArch64-Darwin-only**, FIXED-args-only, narrow-scalar-only. The backend's CC
  is `AAPCS64_Darwin()` (aarch64_emit_func.bn:40); add a `NaturalSizeStackArgs`
  flag set there (NOT on plain AAPCS64() / SysV).

### Fix (commit 3) — sites (AArch64 only)

1. **Convention** (`common_callconv.bn`): the stack-offset walkers
   (CallArgStackOff{,V} / CallStackBytes{,V}) accumulate a narrow FIXED scalar's
   stack footprint at natural size+align (when `NaturalSizeStackArgs` && the arg
   is a non-aggregate scalar with SizeOf<8 && not variadic) instead of `sw*8`.
   8-byte args / aggregates / in-reg args / variadic / SysV are unchanged
   (surgical — the common case is untouched, so low regression risk).
2. **Caller** (`aarch64_call.bn`): store a narrow fixed stack arg at its natural
   width (Str false for int32/float32, etc.), not Str true (8 bytes). Covers GP
   narrow overflow AND the FP-overflow float store from commit 1/2.
3. **Callee** (`aarch64_emit_func.bn`): read a narrow fixed stack arg at natural
   width.
4. **Shims** (`aarch64_funcvalue_spill.bn`, `aarch64_closure_shim_float.bn`):
   the narrow-overflow-float store (emitFloatToOverflowStackAA64 etc.) at natural
   width. (Audit whether the funcval shim's local `AAPCS64()` should be
   `AAPCS64_Darwin()` — a separate inconsistency.)

- **Test:** cross-package (dir-style, like 337) functions with ≥10 narrow params
  (float32 + int32 siblings) returning their sum, on
  `builder-comp_native_aa64-comp_native_aa64` (fails today: 55 vs 66 for float32);
  a float64 twin documenting float64 stays fine.

## Open questions / risks

- **Return-value FP overflow** is a separate concern (`>8` float RETURN values);
  out of scope here (rare; the multi-return collector caps at NumFpRetRegs).
