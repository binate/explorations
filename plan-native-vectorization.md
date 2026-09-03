# Plan: native vectorization (closing the memory-loop half of the native↔clang gap)

Status: PLAN / not started (2026-09-03).  Owner decision needed on scope (see "The
scope decision" at the end).

## Why this, why now

The native register allocator (v1 + the MemZero/MemCopy word-widening) took the
native↔clang `-O2` self-compile gap from **~9–12×** to **~2.5×**.  Two Stage-5
register refinements since — caller-saved homes and copy coalescing — were both
**neutral** (see `plan-native-regalloc.md`): v1's heuristics already captured the
codegen-quality wins that register allocation can reach.  What's left is the part
register allocation **can't** touch.

A `sample` profile of the native-built bnc self-compile (see the
"Native-compile profile" entry in `claude-todo.md`) attributes the remaining cost:

| bucket | share | what it is |
|---|---|---|
| memory management | ~50% | `rt.MemZero` (~42% alone) + malloc/free + refcount dtors |
| register allocator's own bookkeeping | ~20% | `slices.Append`, linear `LookupHome`, dtors |
| linker (bnld) | ~15% | O(n²) symbol resolution |
| other compiler | ~13% | byte/word string loops (`charsEqual`, `streq`, `symHash`) |

clang beats native on the SAME algorithm by **vectorizing (and unrolling) the byte /
word memory loops** — `rt.MemZero`'s fill loop, `charsEqual`'s compare loop.  native
emits a scalar loop (now one machine word per iteration after the widening); clang
emits a NEON/SSE loop moving 16–64 bytes per iteration, plus loop unrolling.  Closing
that is "vectorization" — the right lever for the remaining gap, but a genuinely
large project.  **This plan front-loads the cheap experiments that de-risk the ROI
before the expensive infrastructure is built.**

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

### V0 — scalar UNROLL of the runtime memory loops (NO new infrastructure) — DO THIS FIRST

Before any SIMD, test how much of clang's edge is just **unrolling**.  `rt.MemZero`'s
bulk loop stores one `int` (8B LP64 / 4B ILP32) per iteration; store **4–8 words per
iteration** from a run of GP registers (a manual unroll with a word-remainder tail on
top of the existing byte-alignment tail).  Same for `MemCopy`.  Pure Binate, no asm
changes, no backend changes, works on every target including bare-metal.

- **Why it may capture most of the win:** clang's memset/memcpy advantage is partly
  vectorization and partly reduced loop overhead (fewer branches/increments per byte)
  and better store-buffer utilisation — both of which a scalar unroll also gets.  The
  word-widening alone already bought ~25%; an unroll on top may buy a large fraction
  of the remaining memset gap for a day of work.
- **Deliverable + gate:** a benchmark (native-built bnc self-compile + a memset/memcpy
  microbench) comparing word-loop vs unrolled.  **If the unroll closes most of the
  memory-management gap, the entire SIMD project below may be unnecessary** — which is
  exactly the kind of result the two neutral register refinements taught us to check
  for before investing.
- Risk: register pressure (an 8-word unroll needs ~8 GP registers live) — but MemZero
  is a tiny leaf, so the register allocator has room.  Alignment/tail correctness is
  the same shape as the word-widening (already reviewed + tested).

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

### V2 — hand-vectorized runtime memory primitives (the biggest single lever)

With V1's encoders, provide SIMD `MemZero`/`MemCopy` (and a NEW `MemCompare`, which
`charsEqual`/`streq`-style callers can route through) as **hand-written per-arch
asm**, `#[build]`-gated: NEON on aa64, SSE2 on x64, the V0 scalar-unroll fallback on
arm32/bare-metal.  Two ways to wire it:
- **V2a — hand `.s`, assembled + linked into rt.**  Most direct: bypasses bnc's
  codegen entirely for these 3 functions.  Needs a small rt build-integration change
  (assemble the arch `.s` alongside the Binate rt) — the assembler already exists.
- **V2b — SIMD builtins in the language**, rt written with them.  More general (any
  Binate code could hand-vectorize) but a bigger surface (new builtins, type system,
  backend lowering, vector register allocation).  Deferred unless V3 is pursued.
- Recommendation: **V2a** — it targets exactly the ~50% memory-management cost with
  the least new surface.  This is the plan's primary deliverable if V0 proves
  insufficient.

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

## Recommended path

1. **V0 (scalar unroll)** — days.  Measure.  Gate: does it close most of the
   memory-management gap on its own?  (Learn before investing.)
2. If V0 is insufficient: **V1 (SIMD encoders) → V2a (hand-asm rt primitives)** — the
   high-value core.  Re-profile.
3. **V3 (idiom recognition)** only if the post-V2 profile justifies it.
4. **V4 (general vectorizer)** deferred / probably out of scope.

Each stage is independently measurable and independently landable, and each gate asks
the question the neutral register refinements trained us to ask: *does this actually
move the native↔clang number, or is clang's edge elsewhere again?*

## The scope decision (owner)

The realistic outcomes to choose between up front:
- **V0 only** — cheap; may capture a meaningful chunk of the memory-management gap; no
  new infrastructure.  Low risk, bounded upside.
- **V0 + V1 + V2** — builds the SIMD asm foundation + hand-vectorized rt primitives.
  The high-value core; a real but bounded project (encoders + 3 hand-asm functions per
  arch).  Closes most of the memory-management half of the gap if the hardware SIMD
  memset/memcpy is as much faster as clang's.
- **… + V3/V4** — general vectorization; large, open-ended, lower ROI.

Recommendation: **do V0, measure, then decide V1+V2 based on the number.**  Do not
pre-commit to V3/V4.

## Risks

1. **The whole thing may be marginal** — the same trap as the register refinements.
   V0 is the cheap probe that surfaces this before the SIMD investment.  Take its
   result seriously.
2. **SIMD correctness** (alignment, tails, overlap) is trickier than the scalar
   word-widening; each primitive needs the same exhaustive alignment×size test matrix
   the widening got, per arch, plus the strict-alignment discipline.
3. **Vector register management** — V2a (hand asm) sidesteps it (the asm picks fixed V
   registers); V2b/V3/V4 need real vector register allocation, a large addition.
4. **Per-arch divergence** — aa64 NEON, x64 SSE2, arm32 scalar-only; three code paths,
   `#[build]`-gated, each independently validated (the `native_arm32_baremetal` /
   `native_aa64` / `native_x64_darwin` conformance modes).
5. **bare-metal** — no libc, NEON possibly absent on arm32; the scalar (V0) path must
   remain the universal fallback.
