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

Investigation complete. **Tier 1 (A store-then-reload elimination, B scaled
addressing + power-of-two index strength reduction) — 🟡 IN PROGRESS (claimed
2026-09-18, work-6/session).** Tiers 2–3 not started.
