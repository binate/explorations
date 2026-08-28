# mem2reg: promote inner-loop induction vars (materialize-zero, not abort)

Follow-up to Phase 2a. **Problem (found by benchmarking loop-BCE):** an
induction variable of an INNER loop — the idiomatic `for r { … for j:=0;
j<len(a); j++ {…} }` — is not promoted, so loop-BCE can't eliminate its bounds
check. Proven: single loops promote+eliminate (both `var i`/`for i:=0` forms);
nested loops don't; declaring the inner var *before* the outer loop unlocks it.

**Root cause.** During SSA construction, `j`'s def in the outer body puts `j`'s
iterated dominance frontier on the OUTER loop header, so mem2reg places a phi for
`j` there. That phi needs an operand from the pre-outer-loop edge, where `j` has
no reaching def (undef). `analyzeAlloca`'s conservative **nil-operand abort**
(and the sibling nil-load abort) then bail out of promoting `j` entirely — even
though that outer-header phi is DEAD (`j` is re-initialized to 0 at the top of the
outer body, before any use).

## Fix: materialize a zero constant instead of aborting

Replace both `curVal == nil → return res` aborts in the rename with a
lazily-created **zero constant** of the alloca's element type (`OP_CONST_INT 0` /
`OP_CONST_BOOL false` / `OP_CONST_NIL` — the only promotable kinds), used as the
reaching value. One zero const per alloca (cached), inserted at the entry block
head (dominates everything) during the apply phase. No more aborts.

Concretely: track `zeroVal @Instr` in `m2rResult` (nil until first needed). When
`curVal` is nil at a load or a phi-operand fill, create `zeroVal` (once) and set
`curVal = zeroVal`. Carry `zeroVal` out so `applyPromotion` prepends it to
`f.Blocks[0]` (before that block's phis, since it has no operands).

## Soundness (why zero is always correct here)

The abort fired exactly when the reaching value of a promoted non-managed scalar
is undefined at a load or phi operand. Zero is sound in every case:
- **Dead phi operand** (the nested-loop case): the phi is overwritten before any
  use, so the zero is never observed. Behavior identical.
- **A live undef LOAD** (load of the slot before any store on a path): pre-mem2reg
  that load reads the slot's memory, and gen **zero-initializes no-init scalar
  locals** (`gen_stmt.bn` `IsScalar()` / raw-pointer paths), so the memory is zero
  ⇒ the load reads zero ⇒ materialize-zero MATCHES. An *initialized* local's init
  store dominates its whole scope, so it has no undef read. A compiler temp
  loaded-before-stored (a gen defect) reads garbage today; zero is a safe,
  deterministic improvement.
- The zero const's type is the alloca's `TypeArg` (int/bool/raw-pointer), matching
  the phi and load types — no grounding needed, and `assertScalarPhi` still
  accepts the phi.

So promotion becomes total for eligible scalars, and the induction phi of an inner
loop now exists at the INNER header (the real `[preheader: 0, latch: j+1]` phi the
inner guard `j < len(a)` compares) — which loop-BCE matches, plus a dead
zero-seeded phi at the outer header (harmless).

## Tests

- **Unit (mem2reg):** the nested-loop shape (a scalar whose IDF reaches an outer
  header with an undef preheader operand) now promotes — the inner phi is a clean
  2-entry `[0, step]`, and the pass does not abort. A genuine load-before-store
  now forwards zero (previously left unpromoted) — assert the load becomes the
  zero const, not a surviving load. (Adjust the existing
  `TestMem2regLoadBeforeStoreNotPromoted` — it will now PROMOTE to zero.)
- **loop-BCE end-to-end:** the nested `for r { for j:=0;j<len(a);j++ { a[j] } }`
  over a raw slice now eliminates the inner bounds check at `-O2` (currently 1→1;
  after the fix 1→0), output unchanged.
- **Conformance:** full native `-O2` — must stay 2990/0 (zeroing an undef is
  semantics-preserving; the must-not-fault/​must-fault cases are unaffected).

## Scope note

This is a mem2reg change (soundness-sensitive — the first phi producer), so it
gets its own adversarial soundness + code review before landing, like Phase 2a.
Pairs with the managed-slice loop-BCE follow-up (fault-pad-aware load-forwarding);
together they take loop-BCE from "single raw-slice loop" to the common cases.
