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
- **Commit 3 (darwin narrow-stack ABI) — NEXT (user chose fix-now-after-707
  2026-06-21):** see the MAJOR below.

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
- **Fix (commit 3, after 707):** model sub-8-byte stack-arg packing for darwin —
  the stack-word stride should be the arg's natural size, not a fixed 8 — in the
  convention `soff` accumulation AND the caller stores / callee reads, for narrow
  GP **and** FP stack args. Broader than the FP-overflow work; its own commit.
- **Test (with the fix):** a cross-package (dir-style, like 337) function with
  ≥10 float32 params returning their sum, asserted on
  `builder-comp_native_aa64-comp_native_aa64` (fails today: 55 vs 66); a float64
  twin documenting float64 is fine; an int32 sibling for the GP side.

## Open questions / risks

- **Return-value FP overflow** is a separate concern (`>8` float RETURN values);
  out of scope here (rare; the multi-return collector caps at NumFpRetRegs).
