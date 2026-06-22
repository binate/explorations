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

- **Commit 1 (this one):** convention NSRN threading + caller + callee, BOTH
  backends + a plain `>8`-float conformance test (both native arches). Fixes the
  broad miscompile.
- **Commit 2 (707 proper):** lift the FP-overflow `SetError` in the func-value
  spill shims + the closure float shims; place/read overflow floats there too.
  Un-xfail 707 + add closure/funcval overflow conformance tests.

## Tests

- Plain `sum9(... 9×float64) → 45`, and a 9-float32 variant (the 32-bit path),
  both native arches + LLVM + VM (commit 1).
- Mixed GP+FP overflow (e.g. 7 ints + 9 floats) so GP-stack and FP-stack args
  interleave (commit 1).
- 707: `>8`-float-capture closure (commit 2).

## Open questions / risks

- **float32 stack-slot width.** Plan: 8-byte slots (matches the existing
  GP-overflow + variadic-float-stack code, and SysV/AAPCS round stack args up to
  8). Verify with a float32 `>8`-arg test and, ideally, a cross-package
  native↔LLVM case (a `>8`-float function defined in a dep).
- **Return-value FP overflow** is a separate concern (`>8` float RETURN values);
  out of scope here (rare; the multi-return collector caps at NumFpRetRegs).
