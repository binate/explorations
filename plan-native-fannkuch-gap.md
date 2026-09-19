# Plan: closing the native↔LLVM gap on fannkuch-redux

Investigation into why the native backend's code for the `fannkuch-redux`
benchmark runs several times slower than the LLVM backend's, and the concrete
backend changes that would narrow it. Feeds the "Native codegen quality" effort
in `claude-todo.md`. The benchmark itself lives at
**github.com/binate/benchmarks** (`bench/fannkuch-redux/`).

The lens is **"does it close the native↔LLVM gap?"** — every step below is a
native-backend change that makes native match what clang `-O2` already does for
the LLVM path. A general throughput win that helps both backends is not a
gap-closer and is marked as such.

## Measurement (snapshot — re-run for current figures)

Built the benchmark's Binate entry with a current-`main` `bnc`
(bnc-0.0.16-pre1), both backends, and ran at N=11 (best of 2, one arm64 dev box):

- `--backend native -O2`: ~7.1 s
- `--backend native -O0`: ~7.7 s   ← only ~8% slower than -O2
- `--backend llvm -O2`:   ~2.2 s

So native is **~3.2× slower** than LLVM here (a noisier earlier reading, and the
pinned bnc-0.0.15, put it at ~4.7×; the ratio is what matters, not the absolute).

**Key finding from the -O0/-O2 comparison:** the shared IR optimization passes
(`ir.RunOptPasses`: inline, SROA, mem2reg, load-forwarding, const/loop BCE) barely
move native — because native's lowering re-spills the SSA those passes produce.
**This is a machine-codegen problem, not an IR problem.** Improving IR passes
will not close it; the fix is in the native backend's lowering.

## Root cause (evidence)

Disassembling `main.main`'s pancake-flip inner loop (`while i<j: swap
perm[i],perm[j]; i++; j--`) in both builds:

- **LLVM flip-loop body: ~11 instructions.** `perm`'s base kept in a register
  (x19), scaled indexed addressing (`ldr x9,[x19,x8,lsl #3]`), i/j in registers,
  one combined bounds check, the swap is 2 loads + 2 stores.
- **Native flip-loop body: 91 instructions** for the identical work:

  | category | native | LLVM |
  |---|---|---|
  | loads + stores | 32 | 4 |
  | `mov` shuffles | 18 | ~2 |
  | slice-base reloads (`add x,sp,#0x228; ldr`) | 5 | 0 (in register) |
  | bounds-check sequences | 4 | 1 (combined) |
  | index `mul ×8` | 4 | 0 (scaled addressing) |
  | store-then-reload same-slot pairs | ~10 | 0 |

Native reloads the slice base+length from a stack slot on every element access,
spills every comparison result and temporary to a slot and immediately reloads it
(`cset x9,lt; str x9,[sp]; ldr x9,[sp]; cbnz x9`), bounds-checks each access
(twice for a read+write of the same element), and computes each index with a
`mul`.

### Why the native backend emits this

From the backend architecture (`pkg/binate/native/`):

- **Model:** straight-line lowering of the (post-IR-pass) SSA. `common.PlanFrame`
  gives every value a stack slot ("spill everything"). Over it sits a **linear-
  scan allocator** (`native/common/regalloc_scan.bn`, `LinearScan`) with **no
  interval splitting**, and on aarch64 the **caller-saved home pool is empty**
  (`native/aarch64/aarch64_emit_func.bn` ~:140) — only X19–X28 are available to
  home values. Plus a **within-block reload cache** (`native/aarch64/aarch64_regmap.bn`,
  `getOperand`/`LookupReg`) that is **dropped at every branch/block boundary**.
- **No machine-level CSE/GVN, no peephole, no strength reduction, no scaled
  addressing, no bounds-check hoisting.** The LLVM path gets all of these from
  clang `-O2`, which is **inert for native** (clang only links native output).
- The bounds checks **fragment the loop into basic blocks**; each block boundary
  drops the reload cache → the reload storm. So BCE is not just direct-overhead
  removal, it also restores register residency.
- IR `bceLoop` only recognizes canonical single-induction `for i:=0; i<L; i++`
  loops; the flip loop is dual-induction (`i++`, `j--`, cond `i<j`), so its checks
  are not eliminated.

## Steps, prioritized

All Tier 1–3 are native-backend gap-closers. Hook points are function names in
`pkg/binate/native/` (line numbers drift — grep the function).

### Tier 1 — local, low-risk, immediate (do first)

- **A. Store-then-reload elimination.** Kill the `str r,[sp,#X]; ldr r,[sp,#X]`
  (same register) pairs — the reload cache is dropped at branches even for a value
  consumed by that branch. Either a peephole (drop the `ldr` right after a `str`
  of the same slot/reg) or carry retention across simple intra-loop branches.
  Hook: `getOperand`/`handleResult` and the `ResetRegs` sites in
  `aarch64_regmap.bn` / `aarch64_emit_func.bn`. ~11% of the loop body.
- **B. Scaled addressing + power-of-two index strength reduction.** Replace
  `movz #elemSize; mul byteOff,idx,size; add; ldr [addr]` with
  `ldr rd,[base, idx, lsl #log2(size)]` (or an `lsl` when the size is a power of
  two). Removes the 4 muls + address adds + address reloads per iteration. Hook:
  `emitGetElemPtr` in `aarch64_emit.bn` plus the load/store selectors. Local; helps
  every array-indexing loop.

### Tier 2 — native-level, more work, the bulk of the gap

- **C. Machine-level redundant-load cache (base/len CSE) surviving branches.**
  Keep the slice base+length (and other loop-invariant loads) in registers across
  the loop instead of reloading them ~5× per iteration. Hook: a load cache keyed
  on (base, offset) in the `emitInstr` loop of `aarch64_emit_func.bn`, invalidated
  on stores/calls.
- **D. Register allocator: interval splitting + a non-empty caller-saved pool.**
  The deepest fix, attacking the reload storm at its root — the current allocator
  can't keep enough of a hot loop's values resident. Hook: `regalloc_scan.bn`
  (`LinearScan`, the "no splitting" note) and the empty home pool in
  `aarch64_emit_func.bn`.

### Tier 3 — bounds checks (the flip loop specifically)

- **E.** (i) Implement the already-noted single-unsigned-compare check
  (`cmp idx,len; b.hs` when `len≥0`) at the emit site in `aarch64_dispatch.bn` —
  halves each check and reduces block fragmentation. (ii) Extend IR `bceLoop`
  (`pkg/binate/ir/bce_loop.bn`) to recognize the dual-induction reversal pattern
  (`for i,j:=0,k; i<j; i++,j--`) so the flip-loop checks vanish entirely, which
  also un-fragments the loop and restores the reload cache.

### Not a gap-closer (helps both backends — lower priority for this effort)

- **F.** Elide the `DivCheck` for a constant-nonzero divisor and strength-reduce
  `x%2 → x&1`, `x/pow2 → shift`. Both backends currently emit a `DivCheck` +
  division for `permCount % 2` (~1.5% on each), so removing it does not change the
  ratio. Hook: `emitDivCheckGuard` in `pkg/binate/ir/gen_binary.bn`; `OP_REM`/
  `OP_DIV` selectors in `aarch64_ops.bn`.

## Recommended order and validation

Start with **A + B** (local, no analysis machinery, immediate measurable
movement), then **C**, then the **D** allocator work; **E** for the flip loop.
Validate each change by rebuilding the benchmark and watching the ratio:

```
scripts/run.sh fannkuch-redux binate-native binate-llvm c
```

(binary-trees and richards exercise the same weaknesses — redundant loads,
bounds checks, reloads on a mutable graph — so gains should generalize; re-check
them too.)

## Status

Investigation complete. Tier 1 taken on 2026-09-18 (work-6/session):

- **B (power-of-two index strength reduction) — ✅ LANDED `c77bdae4a`.**
  `emitGetElemPtr` now scales the index with a single `lsl` (or uses the index
  directly for elemSize 1) instead of `movz #elemSize; mul`. **~23% faster on
  fannkuch native** (best-of-3 N=11: 6.9s → 5.3s; gap 3.6× → 2.8×). Verified
  correct by self-compile, native-aarch64 conformance (3037 passed / 0 failed),
  and byte-exact output on fannkuch (elemSize 8), spectral-norm (float64),
  binary-trees (structs), and mandelbrot (elemSize-1 path); hygiene 20/20. NB:
  this is the strength-reduction half of B; fusing into scaled load/store
  addressing modes (`ldr [base,idx,lsl#n]`) is a further step, left for later.

- **A (store-then-reload elimination) — reverted; the naive approach is a
  MISCOMPILE.** Adding the block terminators (OP_BRANCH/OP_JUMP) to
  `aarch64RetentionSafe` removes the *pre-op* barrier that spills live-out values
  **before** the branch; the post-op branch-spill (step 6) runs *after* the
  `cbnz`/`b` are emitted, so those spill instructions are dead code (control has
  already transferred) and dirty register-resident loop-carried values never
  reach their slots → the successor block reads stale memory → SIGSEGV. The
  self-compile passed (bnc's own branch sites didn't hit the dirty-live-out
  pattern) — a silent miscompile. A **correct** A must spill the live-OUT set
  *before* emitting the terminator while keeping the condition cached (read from
  its register, not reloaded) — i.e. restructure the terminator's spill/reset
  ordering, not a one-line allowlist add. This is more involved than the plan
  assumed (Tier-2-scale), deferred pending a decision.

### Post-landing re-benchmark (current main, 2026-09-18)

Re-measured on current main (which also gained a concurrent native-regalloc
commit, `c82f31b6d` "loop-depth spill weighting increment 2"). Fannkuch N=11
native, best-of-5:

- current main **without** B: ~3.5s   (vs ~6.9s on old main → the regalloc commit
  alone gave ~49%)
- current main **with** B (landed): ~3.0s   (B still worth ~14% on top)
- llvm: ~1.5s

So the native↔llvm gap on fannkuch is now **~2.0×**, down from ~3.6× at the start
— the concurrent regalloc work did most of it, B added a bit. (Dev-box numbers are
noisy; run the suite for current figures.)

### A reassessment

**Recommendation: do NOT take on "correct A" as an independent change now.**
Correct-A is a restructuring of the terminator spill/reset ordering — the same
reload/spill machinery a concurrent worker is *actively* rewriting (the
loop-depth spill-weighting increments). Reasons to hold: (1) collision risk with
that effort, and they are better placed (already in that code); (2) it is
correctness-critical and subtle — the naive version silently miscompiled; (3) the
landing regalloc improvements may already shrink the store-then-reload waste, so
it should be re-measured after that effort settles rather than done as a risky
parallel change. Fold correct-A into / coordinate with the register-allocator
work (Tier-2 D territory) instead of a standalone terminator hack.

**Tier-2 C — ⛔ attempted (cache-survival-across-bounds-checks), reviewed SOUND,
implemented, measured a WASH, reverted.** Approach: make OP_BOUNDS_CHECK
retention-safe + exempt from the post-op branch cache-drop so the reload cache
survives across bounds checks (the checks fragment the loop into blocks and the
cache drops at each). An adversarial design review said SOUND-WITH-CONDITIONS
(aarch64-only; noreturn cold path; three unpinned emitter invariants). It was
correct (self-compile, fannkuch/binary-trees byte-exact, a new straight-line
multi-access regression test all passed), but a same-window flip-loop census
showed **no net instruction change (96 → 96)**:
- it dedup'd the header-**address** materialisation (`add x,sp,#off` 5 → 2), but
- keeping that address resident across the checks raised register pressure →
  +2 `mov`, and — the real miss — it did **not** eliminate the base/len **field
  loads** (`ldr [hdr]`, `ldr [hdr+8]`): those are *separate OP_LOAD SSA values*
  per access, so the SSA-id-keyed retention cache cannot merge them.

Reverted (a correct but zero-benefit change is not worth the added correctness
dependency on the three invariants).

**Finding — C is the wrong lever for this loop.** Eliminating the redundant
base/len *field loads* needs either (a) a memory-**address**-keyed load cache
(the plan's literal C — bigger, and still adds the same register pressure on a
7-pool-register loop) or (b) IR-level loop-invariant load hoisting (LICM). Both
fight the same register pressure. The higher-value levers are **Tier 3
(bounds-check elimination)** — which removes the loads AND the bounds branches
AND the fragmentation/pressure at once — and the **ongoing register-allocator
work** (Tier-2 D, active on main). Recommend pursuing Tier 3 (extend `bceLoop`
for the dual-induction reversal loop) or coordinating with the regalloc effort,
not a standalone load cache.

**Tier-3 single-unsigned-compare bounds check — ✅ LANDED `a6a5aa6f1`
(guard-emitter extraction) + `b18c5b7b0` (the single-compare).** Replaced the
two-signed-compare/two-branch element bounds check with one unsigned compare
(`cmp idx,len; b.lo ok`), sound because every `OP_BOUNDS_CHECK` length operand is
provably ≥ 0: element/array length is ≥ 0; a slice `s[lo:hi]` passes len+1 (≥ 1)
for the hi check and hi+1 (≥ 1) for the lo check, relying on gen_slice's
hi-before-lo ordering. AArch64 only — x64/arm32 keep the two-compare form. The
change tipped `aarch64_dispatch.bn` over the file-length cap, so the guard
emitters (`emitBoundsCheck`/`emitDivCheck`/`emitShiftCheck`) were extracted to a
new `aarch64_guards.bn` (mirroring the x64 split), with the guard unit tests moved
to `aarch64_guards_test.bn`; conformance 1272 covers element + slice-range checks.
Measured ~5% on the fannkuch flip loop (cmp 11→4, conditional branches 8→3 in the
hot window); native/LLVM ratio ~2.0x→~1.87x. The `bceLoop` *elimination* for the
dual-induction flip loop was found UNSOUND — the reversal bound `k = perm[0]` is
not provably `< len`, so eliminating the check would be a memory-safety hole; this
cheapens the check instead of removing it. Tier-2 D partly underway on main via
the concurrent regalloc effort.
