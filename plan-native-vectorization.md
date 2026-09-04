# Plan: native vectorization (closing the memory-loop half of the native↔clang gap)

Status: PLAN / not started (2026-09-03).  Corrected after an adversarial review
(findings folded in below).

## The goal (non-negotiable)

**The native backend is THE backend; LLVM/clang is a stopgap slated for deletion.**
The objective of this work is to **NARROW the native↔LLVM codegen performance gap** —
to make native-generated code as fast as LLVM-generated code for the same program.
Where LLVM emits faster code than native, that is a native-backend defect to fix.
This is not negotiable and not up for reframing as "maybe just make native faster in
absolute terms" — general-throughput wins that speed up BOTH backends do NOT close the
gap and are a separate, lower-priority concern.  (See CLAUDE.md "The Native Backend Is
the Goal.")

## Why vectorization

The register allocator (v1 + the MemZero/MemCopy word-widening) took the native↔LLVM
`-O2` self-compile gap from **~9–12×** to **~2.5×**.  Two Stage-5 register refinements
since (caller-saved homes, copy coalescing) were **neutral** — v1 already captured what
register allocation can reach.  The remaining gap is where **LLVM vectorizes / calls an
optimized memory primitive and native emits a scalar loop**.  That is the lever.

**The clang baseline, verified by disassembly (do NOT restate the pre-widening
guesses):**
- **`rt.MemZero`:** LLVM's LoopIdiomRecognize lowers the fill loop to a **libc `bzero`
  call** (aa64: `bl _bzero`, which uses `DC ZVA` — zeroes a whole cache line per
  instruction; x64: `call ___bzero`).  It is NOT inline NEON.  To close this gap,
  native must emit an **equally-fast zero-fill in our own asm** — `DC ZVA` on aa64, a
  `rep stosb` / wide-SSE fill on x64.  That is C-free-legal (asm ≠ C) and IS the work;
  it is not a reason to call the gap unclosable.  ("We can't call libc `bzero`" is a
  constraint on the implementation, not permission to leave native slow.)
- **`rt.MemCopy`:** LLVM inline-vectorizes it (aa64: `ldp/stp q0–q3`, 64 B/iter).
  Native must match with a SIMD/wide copy.
- **String loops** (`charsEqual`/`streq`/`symHash`): LLVM may vectorize; native is
  scalar.  Close per the current profile.

**Premise correction (from the review — must be honored):** the "~50% memory
management / MemZero ~42%" figures in `claude-todo.md` are **pre-widening** (they
justified the widening, which then did ~8× fewer stores).  They are STALE.  The
addressable slice is also narrower than "50%" — that bucket includes libc `malloc`/
`free` (identical on both backends → NOT a gap contributor, not vectorizable) and
refcount dtors (pointer-chasing, not fill/copy loops).  **Therefore step 0 of this
whole plan is a RE-PROFILE of the current (widened) native bnc, framed as "where is
native slower than LLVM," to size each gap source before building anything.**

## The landscape (what exists, what's missing)

- **The asm layer has NO SIMD/vector instructions.**  `asm/aarch64` has scalar FP
  only (`Fadd`/`Fmul`/`Fcmp`/`Fcvt` on single D/S registers — `aarch64_fp.bn`); no
  NEON (no `LD1`/`ST1`/`MOVI`/vector arithmetic on V registers).  `asm/x64` has SSE2
  **scalar** float only (XMM used as a scalar-float scratch, MOVQ through it —
  `x64_fp.bn`); no packed ops (no `MOVDQU`/`PXOR`/`PADD`).  So **any codegen that
  emits SIMD needs new instruction encoders first.**
- **Float scalars are not even register-allocated** — they are non-allocatable and
  spill (`regalloc_liveness.bn:82`); FP values shuttle through a scratch D/XMM.  So
  there is minimal vector-register plumbing to build on; SIMD register management
  would be largely new.
- **The runtime is pure Binate** (`rt.MemZero`/`MemCopy` in `rt_managed.bn`, now
  word-at-a-time).  There are **no persistent `.s` runtime files**, but the
  self-hosted assembler CAN assemble `.s` (bnld synthesises + assembles `_start.s`
  via `asm/assemble`), so hand-written arch asm is an available mechanism.
- **C-free constraint**: SIMD written in our own asm is fine (it's not C).  We must
  NOT reach for libc `memset`/`memcpy`; bare-metal has no libc.

## Approaches, cheapest-first

### Step 0 — RE-PROFILE the current native bnc — DONE (2026-09-03)

Re-profiled the current (widened) native bnc self-compiling cmd/bnc.  Current
"where is native slower than LLVM" breakdown (of 10880 leaf samples):
- **`rt.MemZero` — 12.2%** — the #1 gap.  LLVM lowers its fill loop to a libc
  `bzero` call (aa64 `DC ZVA`; verified by disassembly); native runs a scalar word
  loop.
- **byte string-compares (`charsEqual`/`streq`/`symHash`) — 6.8%** — LLVM vectorizes;
  native scalar.
- sha256 (code-signer rotate/loop) — 2.6%; MemCopy — ~0% on this workload.
So the memory-fill loop is confirmed the top gap even post-widening; the string
compares are second.

### V0 — 4-word UNROLL of the runtime memory loops — DONE & LANDED (`4fd5789e5`, 2026-09-03)

Unrolled the `MemZero`/`MemCopy` bulk word loops 4× (pure Binate, every target).
**Measured: MemZero 12.2% → 8.1% of self-time; native cmd/bnc self-compile 12.42s →
12.08s best-of-5, winning EVERY interleaved round (~2.8%) — a real, consistent
gap-closer** (contrast the two neutral register refinements).  Adversarial review
clean; LP64 conformance 3000/0, native arm32 2955/0; rt tests extended to exercise the
unroll to 95-byte fills.  This confirms a meaningful chunk of MemZero was loop
overhead, NOT store bandwidth.  The **residual 8.1% is the store side** — a scalar loop
moving 8 B/store cannot reach `DC ZVA` (64 B/instr); closing that needs the wide-store
work (V1/V2) below.  (Original note, retained:) a scalar unroll alone does not close the
MemZero gap; it moves the native side only (LLVM re-idiom-recognizes the unrolled loop
back to `bzero`), and it is the permanent fallback for arm32 / no-SIMD arches.

### V0 details / original framing — scalar unroll bound

A quick check of how much is just loop overhead: store **4–8 words per iteration** from
a run of GP registers (manual unroll + word-remainder tail on the existing byte tail),
`MemZero` and `MemCopy`.  Pure Binate, every target, no asm changes.

- **Bounded upside — do not oversell it.** LLVM's MemZero is a `bzero` call
  (`DC ZVA`); a scalar word-unroll leaves the store COUNT unchanged and only trims
  loop overhead (cmp/add/branch per word) — it CANNOT approach `DC ZVA`, so it does not
  close the MemZero gap.  (The ~25% the byte→word widening bought was ~8× fewer STORES,
  a different regime; word→N-word does not extrapolate from it.)  It helps MemCopy more
  (that gap is inline-NEON, which an unroll partly narrows).
- **It only moves the native side** (still Binate → LLVM re-idiom-recognizes the
  unrolled loop back into `bzero`, so the LLVM baseline is unchanged) — which is the
  correct way to close a gap: make native faster, not drag LLVM down.
- **Real value:** (a) a cheap data point on the loop-overhead fraction; (b) it is the
  permanent fallback for arm32 / any target without SIMD.  It does NOT make the SIMD
  work below unnecessary — closing the MemZero/MemCopy gap REQUIRES the wide asm.

### V1 — SIMD instruction encoders in the asm layer (the shared prerequisite for everything below)

Add the minimum vector instruction set to `asm/aarch64` (NEON) and `asm/x64`
(SSE2), plus the assembler's parser/encoder tables:
- aa64: `LDR/STR (128-bit Q)`, `LD1/ST1` (vector load/store), `MOVI` (broadcast
  immediate, for zero-fill), `DUP`, and the packed integer compares/ops needed by the
  chosen targets.
- x64: `MOVDQU/MOVDQA` (128-bit load/store), `PXOR` (zero), `PCMPEQB` (byte compare),
  `PMOVMSKB` (compare→mask) — the memcmp/memset/memcpy set.
- Scope: comparable to the existing FP encoder files, but a distinct instruction
  class (128-bit V/XMM operands, vector addressing modes).  Unit-tested against known
  encodings (the asm layer already has this test pattern).
- arm32: NEON is optional on the ISA and **absent on some bare-metal configs** — so
  arm32 keeps the scalar (unrolled) path; SIMD is aa64 + x64 only.  (Consistent with
  soft-float on arm32-baremetal.)

### V2 — hand-vectorized runtime memory primitives (the biggest single gap-closer)

With V1's encoders, provide `MemZero`/`MemCopy` (and a NEW `MemCompare`) as
**hand-written per-arch asm** that MATCHES what LLVM emits: aa64 `DC ZVA` + NEON
zero-fill for MemZero (the libc-`bzero` bar), `ldp/stp q` for MemCopy; x64 wide-SSE /
`rep stosb`/`rep movsb`; the V0 scalar-unroll fallback on arm32/no-SIMD.  This is where
the MemZero gap actually closes — a scalar loop cannot reach `DC ZVA`, so matching
LLVM here REQUIRES the wide asm.  Two ways to wire it:
- **V2a — hand `.s`, assembled + linked into rt.**  Most direct: bypasses bnc's
  codegen for these 3 functions.  Needs a NEW rt build seam — the runtime is currently
  pure Binate with no persistent `.s`, so this must assemble a per-arch `#[build]`-gated
  `.s` AND suppress the Binate definition for the SIMD arches without symbol collision.
  The assembler exists (bnld assembles `_start.s`), but "assembler exists" ≠ "rt build
  links conditional per-arch asm" — budget this seam, it is not free.
- **V2b — SIMD builtins in the language**, rt written with them.  More general (any
  Binate code could hand-vectorize) but a much bigger surface (new builtins, type
  system, backend lowering, real vector register allocation).  Deferred unless V4.
- Recommendation: **V2a** — least new surface for the memory gap.
- **`MemCompare` is gated on the re-profile:** it only helps LONG equal-prefix
  compares; compiler identifiers are short and `charsEqual` early-exits on the first
  mismatch, so the string-loop bucket may not pay for a SIMD memcmp.  Build it only if
  the current profile shows those loops are a real gap source.
- **Note on the LLVM baseline (a hand-asm subtlety, NOT a reason to hesitate):** since
  `rt` is shared, a hand-`.s` primitive is also what the LLVM-built bnc runs (LLVM
  can't idiom-recognize hand asm, so it stops calling `bzero` and runs our asm too).
  If our asm equals libc `bzero`, the gap closes with both builds fast; if it's slower,
  the gap "closes" partly by the LLVM build slowing — so the success metric is native's
  ABSOLUTE time reaching the libc-`bzero` bar, not just the ratio.  This is fine given
  LLVM is the stopgap; just measure native absolute, and make the asm genuinely fast.

### V3 — idiom recognition (optional, larger)

Teach the native backend (or an IR pass) to recognise memset/memcpy/memcmp **loop
idioms** in COMPILED code and lower them to the V2 primitives — the analogue of LLVM's
LoopIdiomRecognize.  This helps user code (and bnc's own hand-written fill/copy/compare
loops that aren't rt calls) without a general vectorizer.  Medium effort; do only if
profiling after V2 still shows scalar idiom loops dominating.

### V4 — general auto-vectorization (very large; likely out of scope)

A real loop vectorizer — dependence analysis, vectorizable-loop detection, SIMD
instruction selection, vector register allocation, remainder/peeling — for arbitrary
loops (`charsEqual`'s compare, numeric kernels).  This is a multi-month compiler
subproject with uncertain ROI against a ~2.5× gap that V0–V2 may already shrink
substantially.  **Recommend NOT committing to V4 now**; revisit only if a post-V2
profile shows a large, broad, non-idiom vectorizable surface.

## Sequencing (the question is HOW, not WHETHER — the gap gets closed)

1. **Step 0 — re-profile** the current native bnc vs LLVM (minutes).  Sizes each gap
   source; everything below is ordered from it.
2. **V0 (scalar unroll)** — days.  Cheap loop-overhead probe + the arm32/no-SIMD
   fallback.  Not the memory gap-closer on its own.
3. **V1 (SIMD/DC-ZVA asm encoders) → V2a (hand-asm rt MemZero/MemCopy[/MemCompare])** —
   the core: this is what actually makes native's memory primitives match LLVM's
   `bzero`/inline-NEON.  Re-profile after.
4. **V3 (idiom recognition)** — recognise memset/memcpy/memcmp loops in compiled code
   (not just rt calls) and lower to the V2 primitives.  Do it if the post-V2 profile
   still shows scalar idiom loops as a gap source.
5. **V4 (general auto-vectorizer)** — the remaining vectorizable surface (`charsEqual`
   compares, numeric kernels) that isn't a memory idiom.  Large; sequence LAST, but it
   is on the path to full parity, not "out of scope" — LLVM vectorizes these and native
   must eventually too.

Each stage is independently measurable and landable.  The gate at each stage is "how
much of the native↔LLVM gap did this close," and the answer drives ORDER and effort —
it does not reopen the question of whether to close the gap.

## Risks

1. **Sizing before building** — the pre-widening profile was stale; Step 0 re-profile
   is mandatory so V0–V4 are ordered by the CURRENT gap, not a retired number.  (This is
   about targeting the biggest gap source first, NOT about whether the gap is worth
   closing — it is.)
2. **SIMD correctness** (alignment, tails, overlap) is trickier than the scalar
   word-widening; each primitive needs the same exhaustive alignment×size test matrix
   per arch, plus the strict-alignment discipline.
3. **Matching the LLVM bar, not just "using SIMD"** — MemZero's bar is libc `bzero`
   (`DC ZVA`); a naive NEON store loop that's slower than `bzero` has NOT closed the
   gap.  Measure native absolute time against the LLVM lowering's speed, per primitive.
4. **Vector register management** — V2a (hand asm) sidesteps it (fixed V registers);
   V2b/V3/V4 need real vector register allocation, a large addition to the allocator.
5. **Per-arch divergence** — aa64 NEON/DC-ZVA, x64 SSE2/`rep`, arm32 scalar; three code
   paths, `#[build]`-gated, each validated on its `native_*` conformance mode.
6. **bare-metal / no-SIMD arch** — the scalar (V0) path is the universal fallback where
   the wide asm isn't available; it is a fallback, not the target.
