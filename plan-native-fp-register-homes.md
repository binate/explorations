# Plan: native FP-register homes (stop round-tripping float scalars through GP slots)

The single highest-leverage remaining native↔LLVM lever surfaced by the
fasta/richards analysis (`plan-native-codegen-gaps.md`, now largely landed). It is
**codegen mechanics for float VALUES, not FP arithmetic** — distinct from
vectorization/FMA (`plan-native-vectorization.md`), and separable from the "FP
work" that is otherwise deferred. It unblocks fasta AND all three FP benchmarks at
once.

## Problem

The native aarch64 backend deliberately has **no FP register allocation**. Per
`pkg/binate/native/aarch64/aarch64_float.bn` (its own header): *"every value
(including floats) lives in 8-byte GP-spill slots. Float values are FMOV'd in from
a GP scratch and back out per op"* — using D16/D17 as fixed inline FP scratch. So
every float operation is a round-trip:

```
scvtf d16, x7          ; int→float into scratch
fmov  x10, d16         ; back to a GPR
str   x10, [sp, #off]  ; spill to the value's GP home
ldr   x11, [sp, #off2] ; reload the other operand
fmov  d16, x11         ; into scratch again
fmul  d16, d16, d17    ; the actual op
fmov  x12, d16         ; result back to GPR
str   x12, [sp, #off3] ; spill result home
```

Measured consequence: `fasta` is ~2.0× native/llvm and the tracks that fixed its
integer path (constant div/mod magic-multiply, DivCheck elision, BCE) barely moved
it (−2%) — because the modulo was never the bottleneck; this FP round-tripping is.
Disassembly of `genRandom` on current main confirms the magic-multiply fired
(`smulh`/`msub`, no `sdiv`, no `DivCheck` call) yet the function is dominated by the
`scvtf`/`fmov`/`str`/`ldr` sequence above.

LLVM keeps the same computation in FP registers: `scvtf d0,x24 ; fdiv d0,d0,d1`.

## Scope

Give FP-typed SSA values real homes in the **FP register file** (V/D registers) so
consecutive FP ops keep values in registers across uses instead of bouncing through
a GP slot per op. This is a **second register class** in the native value model /
allocator, alongside the existing GP class.

Not in scope: SIMD/vectorization, FMA contraction, reciprocal-multiply for fdiv
(those are `plan-native-vectorization.md` / algorithmic FP). This plan is purely
"a float value that is live across several float ops stays in a D register."

## Impact (why it is the top lever)

The GP-slot round-trip is the *shared* weakness behind every float-using benchmark:

- `fasta` ~2.0× (largest non-FP-*algorithm* gap; its remaining cost is this).
- `mandelbrot` ~13×, `spectral-norm` ~6.8×, `n-body` ~4.6× — the three "deferred FP"
  benchmarks. Their tight FP compute loops pay this round-trip on every operation;
  FP-register homes are a prerequisite for those gaps to close at all (and for any
  later vectorization to pay off).

So this one lever touches fasta plus all three FP benchmarks — more of the suite's
remaining gap than any other single item. It does **not** require committing to the
harder FP-arithmetic work; it is the enabling mechanics under it.

## Where (files)

- `pkg/binate/native/aarch64/aarch64_float.bn` — the FMOV-per-op design lives here
  (`emitFloatBinop`/`emitFloatCompare`/`emitFloatNeg`/`emitFloatCast`,
  `FP_SCRATCH_A`=D16/`FP_SCRATCH_B`=D17). This is what changes from "always round-trip
  through GP scratch" to "operate on FP-homed values in place."
- `pkg/binate/native/common/regalloc_scan.bn`, `regalloc_interval.bn`,
  `regalloc_liveness.bn` — the linear scan needs to allocate a **float register
  class** (or a parallel FP scan) so a float value gets a V/D home. Today it only
  models the GP file.
- `pkg/binate/native/aarch64/aarch64_regmap.bn` — value→home mapping; needs to carry
  FP homes and FP reload caching.
- `pkg/binate/native/aarch64/aarch64_call_return.bn` — the ABI already uses V0–V7 for
  FP args and D0/D1 for FP returns (`collectScalarReturn`), so the calling-convention
  seam already touches FP registers; homing must interoperate with it (spill FP homes
  across calls per AAPCS: D8–D15 callee-saved, D0–D7/D16–D31 caller-saved).

## Approach sketch

1. Tag FP-typed values (`isFloatTyp` already exists) so the allocator routes them to
   the FP class.
2. Add an FP register pool (start conservative: a handful of callee-saved D8–D15 as
   FP homes + D16/D17 kept as scratch for spilled FP values) and let the existing
   linear-scan machinery allocate it as a second class. Reuse the interval/liveness
   infrastructure — it is register-class-agnostic in principle.
3. In `aarch64_float.bn`, when both operands and the result are FP-homed, emit the FP
   op directly on the D-registers with no `fmov`/`str`/`ldr` round-trip; fall back to
   the current GP-scratch path only for spilled FP values.
4. Honor AAPCS across calls (save/restore or prefer callee-saved D8–D15 for
   call-spanning FP values).

Prior art: the existing GP linear scan (spill-cost eviction + loop-depth weighting)
is the template. The known-refuted allocator experiments (interval splitting, "home
more GP values") do NOT bear on this — this is a *new class*, not more of the GP one.

x64 (XMM) and arm32 (VFP/`aeabi` soft-float, see `plan-aeabi-softfloat.md`) have the
same round-trip pattern; start with aarch64, then port.

## Measurement

`fasta` native/llvm ratio (build both backends, `/usr/bin/time -p` user CPU, best of
N interleaved) is the primary gate; the three FP benchmarks are the secondary
signal. A change that doesn't move fasta's ratio doesn't count.

## Relationship to the deferred FP work

FP arithmetic vectorization/FMA is deferred by choice. This plan is the *mechanics*
under that: even scalar FP code needs values to stay in FP registers. Landing this
first is what makes the later FP-arithmetic work measurable — and it is the piece
that is clearly in scope as "codegen quality," independent of the vectorization
decision.
