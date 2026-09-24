# Plan: native vectorization (SIMD) — closing the vector half of the native↔LLVM gap

Status: **RESURRECTED / planning (2026-09-21).** Supersedes the 2026-09-03 draft,
which was framed around the then-dominant memory-primitive gap; the scalar-codegen
rounds (SROA, regalloc, the fasta/richards/fannkuch/record-churn rounds 1–3) and the
just-landed **FP-register homes** (`a0afe37ec`/`945129d67`/`6daae4f1a`) have since
moved the landscape. This refresh commits the work rather than gating it on profiling.

## The goal (non-negotiable)

**The native backend is THE backend; LLVM/clang is a stopgap slated for deletion.**
The objective is that there be **no native↔LLVM codegen gap** — native emits code as
fast as LLVM for the same program. That endpoint is not realistic short-term, but it
is the target, and it is not up for reframing as "make native faster in absolute
terms." Where LLVM vectorizes and native emits a scalar loop, that is a native defect
to close. (CLAUDE.md "The Native Backend Is the Goal.")

## Why we plan this now, not after more profiling

The remaining large gaps are all vector-shaped and all things a serious backend must
have — so the question is HOW/ORDER, not WHETHER, and profiling to decide *whether* a
lever is worth it has low value against a full-parity endpoint:

- **record-churn ~4.75× native/llvm** (round 3 halved it; the residual is LLVM's
  `add.4s`/`eor.16b` SLP-vectorization of the 8-field integer combine — pure integer
  SIMD, no FP).
  CORRECTION (2026-09-24, claude-todo "record-churn residual is SROA-pinned aggregate
  copies"): the SLP part is only ~4 of LLVM's 25 loop instructions; the dominant residual
  is scalar — two SROA-pinned `Record` allocas copied/zeroed per iteration.
- **The FP benchmarks** (mandelbrot/spectral-norm/n-body) — now that scalar FP is
  register-homed, their residual is FP-arithmetic vectorization.
- **Memory primitives** (`rt.MemZero`/`rt.MemCopy`) — LLVM lowers these to
  `bzero`/`DC ZVA` and inline wide `ldp/stp q`; **every serious toolchain has fast
  SIMD/`DC ZVA` memory primitives**, so we will want them regardless of any profile.

So we **plan V1 immediately** (it's the shared prerequisite for all of the above) and
commit the memory-primitive track (A). Profiling still has a role — it ORDERS the work
and sizes each lever's payoff — but it does not gate whether these get built.

Note: much of this is **integer** SIMD (record-churn, memory primitives), so the
infrastructure is NOT gated on lifting the deferred FP-*arithmetic* work — it serves
the non-FP side too. FP kernels are one consumer of the same machinery.

## Landscape (what exists now)

- **The asm layer has NO SIMD/vector instructions** (confirmed 2026-09-21).
  `asm/aarch64` is scalar-FP-only (`aarch64_fp.bn`: `Fadd`/`Fmul`/`Fcvt` on single
  D/S regs); no NEON (`LD1`/`ST1`/`MOVI`/`DUP`/`ADD.4S`), no `DC ZVA`. `asm/x64` is
  scalar-SSE2-only; no packed ops (`MOVDQU`/`PADDD`/`PXOR`). `asm/arm32` scalar. **⇒ V1
  (encoders + a vector-register model) is the foundation; nothing vector can be emitted
  until it exists.**
- **FP-register homes just landed** (all three backends): a *float register class* now
  exists in the allocator (a parallel scan over the D/XMM file, disjoint from the GP
  class). **Vector register allocation is the natural extension of this** — the V/XMM
  registers are the same file, wider — so (B1) below is not greenfield.
- **The runtime is pure Binate** (`rt.MemZero`/`MemCopy` in `rt_managed.bn`, word-at-a-
  time). The self-hosted assembler can assemble `.s` (bnld synthesises + assembles
  `_start.s` via `asm/assemble`), so hand-written arch asm is an available mechanism.
- **C-free constraint:** SIMD in our own asm is fine (asm ≠ C); we must NOT reach for
  libc `memset`/`memcpy` (bare-metal has none).

## V1 — SIMD asm encoders + vector-register model (PLAN NOW; the foundation)

The immediate, committed prerequisite. Produces no perf win alone (nothing emits the
new instructions yet) but unblocks (A) and (B), and is fully testable in isolation
(assemble an instruction → assert the encoded bytes, per the existing `asm/*_test.bn`
pattern). Per-arch:

- **aarch64 NEON** (`asm/aarch64/aarch64_neon.bn` + tests): vector loads/stores
  (`LDR/STR q`, `LD1`/`ST1`), packed integer arith (`ADD`/`SUB`/`MUL`/`AND`/`ORR`/`EOR`
  on `.8b/.16b/.4h/.8h/.2s/.4s/.2d`), lane moves (`MOVI`/`DUP`/`INS`/`UMOV`/`FMOV`),
  packed FP (`FADD`/`FMUL`/`FDIV`/`FCMP` `.2s/.4s/.2d`), and **`DC ZVA`** (cache-line
  zero) for MemZero. The V-register arrangement model (V0–V31 × arrangement specifier).
- **x64 SSE2/AVX** (`asm/x64/x64_sse.bn` + tests): `MOVDQU`/`MOVDQA`, `PADDD`/`PSUBD`/
  `PAND`/`PXOR`/`POR`, packed FP (`ADDPS`/`MULPS`/`ADDPD`), broadcasts; plus `rep stosb`
  / wide-SSE fill for MemZero. XMM (and optionally YMM) model.
- **arm32**: NEON where the target has it, else the scalar path is the fallback (many
  arm32/baremetal configs have no NEON). Budget arm32 as "scalar fallback first, NEON
  optional," not a blocker for aa64/x64.

The object writers (elf/macho) don't change — vector instructions are just more opcode
bytes. Each arch's encoders land + are unit-tested independently.

## (A) SIMD memory primitives (COMMITTED — everyone has them)

Off V1, using **fixed** vector registers (no vector regalloc needed): rewrite the hot
runtime primitives to match the LLVM/libc bar.
- `rt.MemZero` → `DC ZVA` (aa64), `rep stosb` / wide-SSE (x64), scalar (arm32).
- `rt.MemCopy` → wide `ldp/stp q` (aa64), `MOVDQU` (x64), scalar (arm32).
- (`rt.MemCompare` only if a later profile shows the compare loops are a real gap —
  compiler identifiers are short and early-exit, so this one IS profile-gated.)

Mechanism: hand-`.s` per arch (`#[build]`-gated), assembled in-process by the existing
`asm/assemble` path. Success metric is native's ABSOLUTE time reaching the
`bzero`/inline-NEON bar (since `rt` is shared, a hand-`.s` primitive is also what the
LLVM build runs — so make the asm genuinely fast, and measure absolute, not just ratio).

## (B) Arithmetic SIMD — the parity work (large)

The record-churn `add.4s` frontier + FP kernels. Three layers, sequenced:

- **B1 — vector register allocation.** Extend the just-landed float register class to
  the full V/XMM width (vector values as a register class the linear scan allocates).
  The FP-homes work is the template + groundwork; this is its width-generalization.
- **B2 — SLP vectorization.** Pack adjacent independent scalar ops on a struct/tuple
  into one vector op (record-churn's 8 `uint32` field combines → 2× `add.4s`/`eor.16b`).
  An IR or backend pass; the highest-value integer-SIMD lever, and the one record-churn
  probes directly.
- **B3 — loop auto-vectorization.** Dependence analysis + vectorizable-loop detection +
  vector instruction selection + remainder/peeling, for numeric loops (FP kernels;
  integer reductions). The largest piece; sequence last.

## Idiom recognition (fits between A and B)

Recognise `memset`/`memcpy`/`memcmp` **loop idioms** in COMPILED code (not just `rt`
calls) and lower them to the (A) primitives — the analogue of LLVM's
LoopIdiomRecognize. Medium effort; helps bnc's own hand-written fill/copy loops.

## Sequencing

1. **V1** — SIMD asm encoders + vector-register model (aa64 NEON + `DC ZVA` first, then
   x64 SSE2, arm32 scalar-fallback). **Start here now.** Independently testable.
2. **(A)** memory primitives (`MemZero`/`MemCopy`) — fixed-register hand-`.s`, off V1.
3. **Idiom recognition** — memset/memcpy loops → (A) primitives.
4. **(B1) vector regalloc → (B2) SLP → (B3) loop auto-vec** — the arithmetic frontier
   (record-churn integer SIMD, then FP kernels).

Each stage is independently landable + measurable; the per-stage gate is "how much of
the native↔LLVM gap did it close," which orders effort — it does not reopen WHETHER.

## Risks

1. **SIMD correctness** — alignment, tails, overlap; each primitive/op needs an
   exhaustive alignment×size test matrix per arch, plus strict-alignment discipline.
2. **Matching the LLVM bar, not just "using SIMD"** — MemZero's bar is `DC ZVA`; a naive
   NEON store loop slower than `bzero` has NOT closed the gap. Measure native absolute.
3. **Vector register management** — (A) sidesteps it (fixed regs); (B1)+ need real
   vector regalloc — sized down by the landed FP-register-class groundwork, but still a
   real addition.
4. **Per-arch divergence** — aa64 NEON/`DC ZVA`, x64 SSE2/`rep`, arm32 scalar; three
   `#[build]`-gated paths, each validated on its `native_*` conformance mode.
5. **Bare-metal / no-SIMD arch** — the scalar path is the universal fallback (arm32
   baremetal); it is a fallback, not the target.
6. **B is multi-month** — B2 (SLP) is the tractable high-value integer piece; B3 (full
   loop auto-vec) is the open-ended one — land B1/B2, then size B3 against what's left.
