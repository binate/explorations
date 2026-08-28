# Phase 3 — loop-BCE (tightened soundness)

**Prerequisite (decided 2026-08-27): Phase 2b load-forwarding
(`plan-load-forwarding-phase2b.md`) lands FIRST.** The soundness review confirmed
the design is sound but that condition 5's **slice** sub-case never fires without
load-forwarding (no CSE; `len(s)` and `s[i]` load `s` twice → distinct
`OP_SLICE_LEN` operands). Load-forwarding coalesces those loads so slice loop-BCE
works; the **array** case (const-length equality) fires without it.

**Reach after 2b: arrays + RAW slices (`*[]T`).** The 2b soundness review found
that **managed slices (`@[]T`)** escape via the bounds check's own fault pad (it
loads live managed locals for cleanup), so they are not forwarded and their
loop-BCE is deferred to a v2 (fault-pad-aware handling). Raw slices — the common
read-iteration borrow form — and arrays are covered.

Design doc for the marquee payoff: remove `OP_BOUNDS_CHECK(idx, len)` for an
induction-variable array/slice access inside a counted loop, now that mem2reg
(Phase 2a) turns the induction variable into an SSA loop-header **phi** and Phase
D gives us the dominator tree. Lives in `pkg/binate/ir` (BUILDER-compiled). Runs
in `RunOptPasses` at `-O1+`, after `promoteScalars` (which produces the phi).

## The pattern (canonical `for i := 0; i < N; i++ { … a[i] … }`)

Gen lowers a for-loop to `for.cond` (guard) → `for.body` → `for.post` (step) →
back-edge to `for.cond`, with `for.exit` after. mem2reg places `i`'s phi at the
loop header **`for.cond`** (the join of the preheader init and the `for.post`
back-edge). So post-mem2reg the IR is:

- **header H = `for.cond`:** `P = phi [preheader: init, latch: step]`; then
  `cond = OP_LT(P, L)`; then `OP_BRANCH(cond, T, F)` (T=`for.body`, F=`for.exit`).
- **body (T and its dominatees):** `… OP_BOUNDS_CHECK(P, len); a[P] …`.
- **latch = `for.post`:** `step = OP_ADD(P, 1)`; jump H.

An `OP_BOUNDS_CHECK(idx, len)` is **eliminable** iff ALL of the following hold —
each is exactly one of the three soundness obligations (0 ≤ idx, idx < len, no
signed overflow), and every case not matched is KEPT:

## Eliminability conditions (all required)

1. **idx is a loop-header phi P.** `idx.Op == OP_PHI` with exactly 2 entries.
   Using `DomInfo`: the entry whose block is dominated by P's block H is the
   **latch/step** entry; the other is the **init** entry. (Two entries, one
   dominated by H and one not — the reducible-loop shape. If neither/both are
   dominated by H, or ≠2 entries, bail: not a simple counted loop.)

2. **Lower bound `P ≥ 0`.** The **init** entry value is an integer `OP_CONST_INT`
   with `IntVal ≥ 0`, AND the **step** entry value is `OP_ADD(P, C)` (either
   operand order) with `C` a positive integer `OP_CONST_INT`. Monotonic
   non-decreasing from a `≥0` start ⇒ `P ≥ 0` always. (A step that is not a clean
   `P + positiveConst` — e.g. the body also writes `i` — fails this and is KEPT,
   which also rules out non-induction phis.)

3. **No signed overflow: unit stride.** `C == 1` (v1). With the `P < L` guard and
   `+1` steps, `P` runs `init, init+1, …, L-1`, then `P == L` fails the guard and
   the loop exits — `P` never exceeds `L ≤ len ≤ IntMax`, so the index never
   overflows. (Non-unit stride can step *past* `L` and must prove no wrap;
   deferred. `int` is signed, so this is a real obligation, not automatic.)

4. **Upper bound `P < len` on the access path.** H's terminator is
   `OP_BRANCH(cond, T, F)` with `cond == OP_LT(P, L)`, and the bounds-check's
   block B is **dominated by T** (`DomInfo.Dominates(T, B)`) — so B is reached
   only via the true edge, where `P < L` holds. (P is the header phi, unchanged
   through the body, so `P < L` persists to the access.)

5. **`L` provably ≤ `len`.** The guard bound `L` and the bounds-check length
   `len` must be **structurally equal** (sound because SSA values are immutable):
   - both `OP_CONST_INT` with equal `IntVal` (array length), OR
   - both `OP_SLICE_LEN` with SSA-identical `Args[0]` (the same slice value — its
     length is fixed), OR
   - SSA-identical instrs.
   This is the crux of soundness: `for i := 0; i < n; i++ { a[i] }` where `n` is
   an arbitrary variable (NOT `len(a)`) must be KEPT — `n` could exceed `len(a)`
   (conformance 310/311). Requiring `L` ≡ `len` structurally rejects it. (`≤`
   rather than `==` is the real obligation, but structural-equality is the sound,
   useful subset; a proven `L ≤ len` beyond equality is a future extension.)

When all hold, delete the `OP_BOUNDS_CHECK` (the `bceBlock` rebuild pattern:
reassign `blk.Instrs`; the check becomes unreachable, its operands stay owned by
`InstrsVec`). The access's `OP_GET_ELEM_PTR` / slice-get is untouched.

## Explicitly KEPT (soundness — must still fault when the program should)

- `i < n` where `n` is not the collection length (L ≢ len) — conformance 310/311.
- init `< 0` or non-constant init — the guard says nothing about sign
  (conformance 314).
- non-unit stride (v1) — overflow / step-past-L not proven.
- body modifies `i` (step ≠ `P + positiveConst`).
- `i <= len` guard (would access `len`; v1 matches only `OP_LT`), decreasing
  loops, `len` re-read from a slice reassigned in the body (different SSA value ⇒
  L ≢ len ⇒ kept).
- access block not dominated by the guard's true target.

## Where it runs / ordering

Add `bceLoop(m)` to `RunOptPasses` after `promoteScalars` (needs the phi) and
after `bceConstIndex` (independent; order-immaterial). `-O1+` gated. `ComputeDom`
per function (reuse across all bounds checks in the function).

## Testing

- **Unit (hand-built IR, like the mem2reg/dom tests):** the canonical loop
  (phi + `OP_LT(P,len)` guard + `OP_ADD(P,1)` step + `OP_BOUNDS_CHECK(P,len)` in
  a T-dominated block) → check removed; and one test per KEPT case: L≢len
  (different const / different slice), negative init, non-unit stride,
  body-modified step, guard `OP_LE`, access not dominated by T. Assert the check
  survives in each.
- **Conformance (real loops, size/quality + correctness):**
  - SHOULD-eliminate: `for i:=0;i<len(a);i++ { a[i] }` for an array and a slice —
    a size/quality check that the loop body has **zero** `OP_BOUNDS_CHECK`
    (mirroring the alloca-hoist / bce-const style checks), across LLVM/native/VM.
  - MUST-KEEP (correctness): a loop `for i:=0;i<n;i++ { a[i] }` with `n > len(a)`
    that must still fault at the boundary; a negative-index access; verifying the
    program's observable fault behavior is unchanged (the checks that 310/311/314
    already pin, run at `-O1+`).

## Soundness note (why each obligation, tersely)

The bounds check guards `0 ≤ idx < len` on a SIGNED index. The guard `P < L` gives
the upper half only when `L ≤ len` (cond 5) and only where it dominates (cond 4).
The lower half needs an independent `P ≥ 0` proof (cond 2) — the guard is
sign-blind. And "runs to exactly the boundary" needs unit stride (cond 3) so the
guard actually catches the last value before any wrap. Drop any one and the
elimination is unsound; that is the "tightened soundness" the whole project was
built toward.

## Open scoping question for the user

- **Non-unit stride / `<=` guard / descending loops** are deferred to a v2 of
  loop-BCE (each needs a bit more proof). v1 targets the overwhelmingly-common
  `for i := 0; i < len(a); i++` ascending unit-stride form. Flagging so the
  narrow v1 scope is an eyes-open choice, not a silent gap.
