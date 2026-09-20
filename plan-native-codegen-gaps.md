# Plan: native↔LLVM codegen gap — fasta / richards (instruction selection + IR load-forwarding)

A disassembly-grounded analysis of the two largest **non-floating-point**
native↔LLVM gaps in the benchmark suite — `fasta` and `richards` — turned into
orthogonal work tracks. Companion to `plan-native-fannkuch-gap.md` (fannkuch flip
loop) and `plan-native-regalloc.md` (the allocator arc).

The native backend is THE backend; LLVM/clang is a stopgap reference. "Close the
gap" means make native emit code as good as clang emits from the same IR.

## Relationship to prior work — READ FIRST

The `### Native codegen quality — closing the native↔LLVM gap` item in
`claude-todo.md` already records a large, measured arc on this gap (mostly against
the **compiler self-compile** workload). Its conclusions bound what is worth doing
here — do NOT redo settled work:

- **Aggregate scalar-replacement (SROA) is DONE** (biggest lever on the compiler,
  ~53% of that gap). SROA scalar-replaces aggregate *values*; it is not
  redundant-load elimination for managed-pointer *field loads* (see Track 4).
- **Register-allocation / spill is DONE and further-refuted.** Spill-cost eviction,
  loop-depth weighting, and arg-bank caller-saved homes all landed (last narrowed
  the compiler self-compile 3.10→2.89×). **Interval splitting was implemented and
  REGRESSED ~3.5% — DO NOT LAND it.** Three independent measurements concluded
  **register spill is not the remaining gap term** on the compiler workload. So a
  track that just "homes more values" is a dead end; the remaining gap is
  **instruction selection** and the **memory-op / copy path**.
- **Blanket inliner threshold-raising is measured NET-NEGATIVE on native** (native's
  per-function codegen deficit scales with body size, so bigger inlined bodies cost
  more; default stays 15). The fasta/richards agents suggested raising it — that is
  **contraindicated**; inlining is downstream of codegen quality, not a lever now.
  It becomes a win only once the tracks below make merged bodies cheap on native.
  (A *targeted* post-inline constant-fold that recovers the `1.0 *` in fasta's
  `genRandom` is a different, narrow idea — but not "raise the threshold.")

What this fasta/richards analysis ADDS: it CONFIRMS "the gap lives in instruction
selection" with a concrete, self-contained example (constant div/mod), and it
surfaces an **untried IR-level lever** — load-forwarding / promotion of
managed-pointer field loads — that the compiler-workload conclusions did not
exercise and that is distinct from the (done, refuted-further) allocator work.

## Measurement basis

Benchmarks repo (`github.com/binate/benchmarks`), `bnc-0.0.16`, user CPU time,
best of 5 interleaved rounds (`scripts/run.sh` times wall-clock, so a custom
`/usr/bin/time -p` driver was used; interleave native/llvm per round to average
out load). Non-FP native/llvm ratios: `fasta` ~2.1×, `richards` ~2.0×, `fannkuch`
~1.8×, `binary-trees` ~1.2×. FP (out of scope here, larger): `mandelbrot` ~11×,
`spectral-norm` ~6×, `n-body` ~5×. Evidence: native-vs-LLVM disassembly diff of
each hot path (aarch64, macOS), 2026-09-19. Sources:
`bench/fasta/binate/cmd/fasta/main.bn`, `bench/richards/binate/cmd/richards/main.bn`.

## Findings, with evidence

### fasta (~2.1×)
Hot path: `randomFasta → selectRandom → genRandom`, run n×8 times.
- **Constant `% 139968` → real `sdiv`+`msub`** (long-latency); LLVM uses a
  `smulh`+shift magic-number multiply. Native also fails to fuse `mul`+`add`→`madd`
  and re-`mov`s literal constants. `native/aarch64/aarch64_ops.bn` synthesizes
  `OP_REM` as `Sdiv`+`Msub` unconditionally — no constant-divisor strength
  reduction exists in `native/`. **Biggest single fasta gap; pure instruction
  selection.**
- **`rt.DivCheck` CALL fires in the hot loop on BOTH backends** for a constant
  divisor (139968, never 0/−1). Dead check, ~7% of native samples. Backend-neutral
  win to elide.
- **Bounds checks + loop-invariant length/base not hoisted**: native's inner scan
  is ~20 instr with a `BoundsFail` branch and per-iteration length/base reloads;
  LLVM's is 7 register-only instr with the check folded into a countdown and a
  post-increment pointer. `seed[0]` is bounds-checked 3× in one function.
- FP intermediates round-trip fp-reg → GPR → stack → GPR → fp-reg per op (~6 extra
  `fmov`/`str`/`ldr`); float scalars are non-allocatable today (a known lever).

### richards (~2.0×)
Hot path: `schedule` driving `s.current.task.run(...)` through the `Task` interface.
- **Hot managed pointers never promoted → reload-per-use.** `iropt/mem2reg.bn`
  promotes only non-managed scalars; `iropt/load_forward.bn` declines struct /
  interface / by-address aggregates. So `s`, `s.current`, `packet` stay
  memory-resident in the IR — a fresh load per use. Native `schedule` reloads `s`
  from its stack home 21×; LLVM (managed pointers lowered to plain `i8*`, then
  clang's mem2reg/GVN) keeps `s` in x19 and `s.current` in x23 for the whole loop.
  This is a **memory-op** gap at the IR level, NOT allocator spill (there is no
  long-lived value for the allocator to home — the IR emits independent loads).
- **RefDec materializes the destructor handle (`adrp`+`add`) before the zero-test**,
  so it runs on every dec even when nothing frees; LLVM hoists the handles once
  before the loop and touches them only on the taken free-branch (native emits 9
  `adrp` in `schedule`, LLVM 4).
- Interface dispatch is **NOT** a culprit: the vtable/handle resolution is identical
  depth on both; the cost is stack round-tripping the receiver/args (a facet of the
  non-promotion above), not an extra shim hop.
- Missing store-to-load forwarding: native has literal `str x6,[slot]; ldr x6,[slot]`
  on adjacent instructions.

## Work tracks (orthogonal insofar as possible)

Designed so different workers touch disjoint files. Each is independently landable
and measurable. Coordination points are called out. **Every track: measure the
native/llvm ratio on the named benchmark(s) before/after — a change that doesn't
move the ratio doesn't close the gap.**

- **Track 1 — Native aarch64 constant int div/mod → magic-number multiply
  (+ `madd` fusion, `mul ×1` elimination, constant hoisting).** The flagship:
  directly on "the gap lives in instruction selection," self-contained, helps any
  integer div/mod. Files: `native/aarch64/aarch64_ops.bn` (`OP_REM`/`OP_DIV`/`OP_MUL`
  selection) + tests. Fully isolated. Benchmarks: `fasta` (and the compiler). Also
  fold in the known defect "`mul rd,i,#1` not strength-reduced."
- **Track 2 — IR check-elision for provably-safe operands** (`OP_DIV_CHECK` on a
  constant nonzero non-(−1) divisor; `OP_SHIFT_CHECK` on a constant in-range count).
  Backend-neutral; removes a runtime CALL from fasta's hottest loop for both
  backends. Cleanest at the insertion site (`pkg/binate/ir/gen_binary.bn`,
  `emitDivCheckGuard`) or a small `iropt` pass. Benchmarks: `fasta`. Shares the
  "prove operand safe" theme with Track 5 — coordinate if both add an `iropt` pass.
- **Track 3 — Native aarch64 RefDec: sink the dtor-handle operand past the zero-test
  + loop-invariant hoist.** Instruction-selection/lowering peephole. Files: the
  RefDec lowering in `native/aarch64/aarch64_emit.bn`. Benchmarks: `richards`
  (refcount-dense). Coordinate with any deep aarch64-emitter work; keep it small and
  localized. (Confirm the pattern still reproduces on current main first.)
- **Track 4 — IR load-forwarding / promotion of managed-pointer field loads
  (STRUCTURAL, UNTRIED).** The richards lever: forward/eliminate redundant loads of
  the same field through a managed pointer while preserving refcount semantics, so a
  hot pointer/field is loaded once and reused instead of reloaded per use. This is
  the "memory ops" component (attribution had memory-ops N/L 3.23×), **distinct from
  SROA (values, done) and from the allocator/spill work (done, refuted-further).**
  The hard part is alias + refcount safety — `iropt/sroa_managed.bn` is the nearest
  prior art. Files: `iropt/load_forward.bn`, `iropt/mem2reg.bn`, possibly
  `iropt/sroa_managed.bn`. Benchmarks: `richards` (validate here specifically — the
  compiler-workload "spill isn't the gap" conclusion did NOT cover this IR lever).
  Largest / most speculative track; must show a ratio move on richards to be worth
  landing.
- **Track 5 — Array-loop bounds-check elimination + induction/pointer strength
  reduction.** Hoist loop-invariant slice length/base, drop redundant /
  provably-in-range checks (`seed[0]` checked 3×), strength-reduce the induction
  variable to a post-increment pointer. Files: `iropt/bce_loop.bn`, `iropt/loops.bn`
  + the native bounds-check emitter. Benchmarks: `fasta` (and array-loop code
  generally). Builds on the single-unsigned-compare bounds check already landed and
  `plan-native-fannkuch-gap.md`.

Coordination summary:
- `iropt/opt.bn` (`RunOptPasses`) is shared by Tracks 2, 4, 5 if they add/reorder
  passes — expect small mechanical merges.
- `native/aarch64/aarch64_emit.bn` is shared by Track 3 and any emitter-touching
  work — keep Track 3 small.
- Tracks 1, 2, 3 are self-contained Tier-A style (start here / run in parallel);
  Track 4 is the big structural bet; Track 5 is medium.

**Explicitly NOT tracks (per prior measurement — see `claude-todo.md`
`### Native codegen quality`):** raising the inline threshold (net-negative on
native); further allocator "home more values" work / interval splitting (done and
refuted, ~3.5% regression). FP-register homes (float scalars non-allocatable) is a
real but separate known lever tracked with the FP work, not here.

## How to re-measure

```
cd <benchmarks repo>
scripts/run.sh fasta binate-native binate-llvm c        # correctness + wall-clock
scripts/run.sh richards binate-native binate-llvm c
# user-CPU native-vs-llvm, interleaved: build both backends, time with
# /usr/bin/time -p best-of-N alternating per round; compare the native/llvm ratio.
```

Test an unreleased toolchain by building a bundle from a binate `main` checkout and
pointing the harness at it with `BINATE_BUNDLE=<dir>` (see the benchmarks repo
`scripts/fetch-builder.sh` header).
