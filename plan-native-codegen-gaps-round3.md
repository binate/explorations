# Plan: native↔LLVM codegen gap — round 3 (record-churn, the aggregate-copy probe)

Round 3 targets `record-churn` — the synthetic aggregate-copy microbenchmark added to
the suite (github.com/binate/benchmarks), native/llvm **~8×** at N=8000 (native ~0.97s
vs llvm ~0.12s), the largest non-FP gap. Profiled + disassembled on current main
(2026-09-21). Continues `done/plan-native-codegen-gaps.md` (round 1) and
`plan-native-codegen-gaps-round2.md` (round 2).

The native backend is THE backend; LLVM is the reference. Known-REFUTED, do NOT
re-propose: raising the inline threshold (net-negative on native); interval
splitting / "home MORE values" (regressed). SROA (scalar-replace aggregate VALUES)
and the aggregate-copy-fusion line (`done/plan-native-aggcopy-fusion.md`) are DONE —
build on them, don't redo.

## Finding: the copy WIDTH is already fine; the gap is everything AROUND it

The workload loads an 8-field uint32 `Record` from `@[]Record`, combines two records
via `mix` (by value in and out), stores to `out[i]`, carries to the next element
(serial), N passes. Disassembly (native vs llvm) shows:

- **Both use paired `ldp`/`stp` for the 32-byte copy** — no 8× `ldr`/`str`, no
  `memcpy`/`rt.MemCopy`. The width fix landed; copy width is NOT the gap.
- **`mix` is a real out-of-line call on native (70% of samples), fully inlined +
  SROA'd + SLP-vectorized on LLVM.** Native inner loop = 78 instrs + a 140-instr
  `mix`; LLVM's = 27 total. LLVM keeps the carry in registers (`v0/v1/w9/w10`) across
  iterations and does the 8-field combine with `add.4s`/`eor.16b`.
- Alloc/refcount is only **~3.6%** of the native loop — NOT the gap (it's a bigger
  *share* on LLVM only because the loop got so fast).

So the levers are scalar-codegen quality around the copy, not the copy itself.

## Where native loses (evidence)

- **Per-field address `add`.** A field of a stack struct goes through
  `OP_GET_FIELD_PTR`, and the fuse pass rejects an alloca/global base
  (`gepBaseIsRegister` false) on the false premise that the register-base memory form
  "cannot express SP-relative" — but `[sp/x29,#imm]` (and x64 `[base+disp]`) can. So
  `mix` emits 33× `add xN, sp, #imm` each feeding a single `ldr/str` that could fold
  the offset:
  ```
  add x7, sp, #0x5c ; ldr w6, [x7]      ; should be: ldr w6, [sp, #0x5c]
  ```
- **32-bit arithmetic in 64-bit registers + re-narrow.** `emitBinop` hardcodes the
  64-bit form (`Add(a, true, …)`) then appends `ubfx …,#0,#32` (`emitSubWordNarrow`);
  the aa64 `w`-form self-clears bits [32,64) making the mask free (as LLVM does). 9×
  dead `ubfx` in `mix`.
- **`mix` by-value ABI marshalling.** AAPCS64 passes the two 32-byte structs
  indirectly + returns via sret, so the caller copies each input to a stack buffer;
  inside, each param is materialized twice and the result copied twice — several
  redundant 32-byte `ldp/stp` copies.
- **`mix` not inlined.** `InlineSizeThreshold=15`; `mix`'s IR (~40+ ops) is inflated by
  aggregate load/extract/insert plumbing that SROA later deletes.
- **(Ceiling) LLVM SLP-vectorizes the 8-field combine** (`add.4s`). Matching that is
  integer SIMD — a large, separate lever adjacent to the deferred FP-vectorization
  work; NOT a round-3 track (see below).

## Tracks (ranked)

### T1 — Fold constant field offsets on alloca/FP-relative bases into the load/store. Highest value; safe; GENERAL.
Extends the landed round-2 field-GEP fold (which handled REGISTER/managed-pointer
bases) to alloca/global bases: combine allocaOffset+fieldOffset (via `LookupAlloc`)
and emit the SP/FP-relative `[sp,#off]` form instead of a standalone `add`. Removes
~24 instrs in `mix` and helps **every struct-field access in native code**. Files:
`native/common/common_field_gep_fuse.bn` (relax the alloca/global exclusion for
wordBytes==8 targets; keep arm32's 255-imm bound), `common_elem_gep_fuse.bn`
(`gepBaseIsRegister`), `native/aarch64/aarch64_emit.bn` (emit SP-relative when base is
alloca); x64 analog.

### T2 — 32-bit integer arithmetic in `w`-registers; drop the `ubfx` re-narrow. Safe; GENERAL.
Select the 32-bit `w`-form for 32-bit-typed results and skip `emitSubWordNarrow` (the
`w`-form self-clears the top half); 8/16-bit still need `uxtb/uxth`. Removes 9 instrs
in `mix`; benefits all 32-bit integer code. File: `native/aarch64/aarch64_ops.bn`
(`emitBinop`, `emitSubWordNarrow`); x64/arm32 analogs.

### T3 — Extend aggregate-load elision to OP_EXTRACT-only consumers. Medium; explicitly deferred by the prior plan.
`done/plan-native-aggcopy-fusion.md` left "a load consumed purely by `OP_EXTRACT`" as
an open item — exactly `mix`'s param agg-loads whose only uses are field extracts.
Aliasing to the stable source (param/alloca) lets the extracts read it directly,
removing the 2 redundant per-input struct copies (~16 instrs in `mix`). File:
`native/common/common_aggload_elision.bn` (add the extract-only-consumer case to
`AggLoadElidable`).

### T4 — Let SROA-thin shapes like `mix` inline WITHOUT a blanket threshold raise. Largest single-benchmark impact, but POLICY-SENSITIVE — user decision.
The dominant residual is that `mix` stays an out-of-line call with by-value ABI
marshalling; LLVM erases it by inline+SROA. A cost-model change that **discounts
SROA-eliminable aggregate plumbing** when scoring a callee would let
small-arithmetic-through-structs functions inline without raising the threshold for
genuinely-large functions — DISTINCT from the refuted blanket raise, but ADJACENT, so
it must be measured for net effect across the tree (the refuted raise made native
MONOTONICALLY slower). Full LLVM parity additionally needs SROA to keep the record
fields register-resident inside `mix` (promoting by-value-indirect params/sret), which
borders the refuted "home more values" allocator work. **Surface for a decision; do
not land unilaterally.** Files: `iropt/inline_eligibility.bn` / `inline_calls.bn` (cost
model); SROA in `iropt`/`native/common`.

## Not a round-3 track: integer SIMD
LLVM's `add.4s`/`eor.16b` SLP-vectorization of the 8-field combine is the ceiling
after the scalar tracks above. Matching it is integer NEON vectorization — a large,
separate lever in the same family as the deferred FP vectorization
(`plan-native-vectorization.md`). Left out deliberately; the scalar tracks are the
in-scope, general wins.

## Order & measurement
**T1 + T2 first** (safe, general, cut `mix` ~30–35% and help the whole tree), then
**T3**, then bring **T4** as a decision. Measure `record-churn` native/llvm (build both
backends, `/usr/bin/time -p` user CPU, best-of-N interleaved) before/after each — a
change that doesn't move the ratio doesn't count. T1/T2/T3 also help the compiler
self-compile (field access + 32-bit arithmetic are everywhere), so re-check
`perf/native-vs-llvm.sh` too.
