# Phase 2b — load-forwarding (redundant-load elimination) — ✅ LANDED (`ddbc8fea5`)

**Status:** landed on main as `ddbc8fea5`. `pkg/binate/ir/load_forward.bn`
(+ tests). Design review DESIGN SOUND; code review NO BUGS. Full native `-O2`
conformance 2990 pass / 0 fail (no regressions). Coalesces the two slice-header
loads (`len(s)` + `s[i]`) onto one SSA value — confirmed on the canonical loop —
which is what lets loop-BCE (Phase 3) match a slice's guard bound to its
bounds-check length. Design below is as-built (v1: single dominating store; fires
for raw slices + arrays; managed slices deferred — see "Reach" below).


Prerequisite for full loop-BCE (Phase 3) over **slices**. The Phase 3 soundness
review found that condition 5's slice sub-case (`L` ≡ `len` when both are
`OP_SLICE_LEN` of the same slice value) essentially never fires today: there is no
CSE/GVN, and `genExpr(IDENT)` emits a **fresh `OP_LOAD` per reference**, so
`len(s)` (guard) and `s[i]` (access) load `s`'s slot twice → two distinct
`OP_SLICE_LEN` with different `Args[0]` → no structural match. (Verified: even a
*param* slice is copied to a slot and loaded twice.) Load-forwarding coalesces
those loads so both `OP_SLICE_LEN` read the SAME SSA slice value.

Lives in `pkg/binate/ir` (BUILDER-compiled). Runs in `RunOptPasses` at `-O1+`.

## What it does (v1: single dominating store)

Forward every load of a **non-escaping alloca that has exactly one store, and
that store dominates all its loads**, to the stored value — then delete the
alloca, the store, and the loads (the slot is now dead). This is exactly
"promote a single-def non-escaping slot to its value" — a degenerate,
phi-free mem2reg that works for **any** type, including aggregates
(slices/structs) that mem2reg (Phase 2a) skips because they can't be phi
operands.

Concretely, for each `OP_ALLOC A`:
1. **Non-escaping** (reuse mem2reg's `allocaEscapes` — every use of A is the
   `Args[0]` pointer of a plain `OP_LOAD`/`OP_STORE`, nothing else, and no
   appearance in `FaultPads`). NOTE: unlike mem2reg this is NOT type-gated —
   aggregates are allowed. (mem2reg already claimed all promotable *scalar*
   slots by the time this runs; but to avoid double-processing, load-forwarding
   only touches allocas mem2reg left behind — those that survive because they're
   aggregate or multi-store.)
2. **Exactly one store** `OP_STORE(A, V)` across `f.Blocks`.
3. That store **dominates every load** of A (`DomInfo.Dominates(storeBlock,
   loadBlock)`, or same block with the store textually before the load).
4. If all hold: replace every use of each load with `V` (the store's value —
   NOT a re-load), then delete A + the store + the loads (the `bceBlock` rebuild
   pattern). A's non-escape guarantees nothing else wrote it, and one dominating
   store means every load provably read exactly `V`.

## Why single-store (and why it is sound)

The soundness spine is **"one write, and it dominates every read, and nothing
else can write the slot."** Escape analysis gives the last clause (only
`OP_STORE(A,…)` writes A; no aliasing pointer, no call, since A's address never
leaves). One store dominating all loads gives the first two: every path to any
load passes the single store and hits no other, so the load's value is exactly
`V`. This directly defeats the **reslice-shrink unsoundness** the Phase 3 review
flagged: `for i…{ s[i]; s = make_slice(5) }` has TWO stores to `s` → fails
condition 2 → NOT forwarded (so loop-BCE later sees two distinct lengths and
KEEPS the check). A store *between* a would-be-forwarded load and its def is
exactly a second store, so it can never be crossed.

**Deferred to v2:** multi-store slots (need the reaching-def / aggregate-phi
analysis — a load reaches different stores on different paths). v1's single-store
case covers the overwhelmingly-common loop-invariant collection: a param slice or
a locally-initialized slice, iterated without reassignment.

## Reach (accurate — soundness review corrected the over-sell)

The pass is **sound for any type**, including managed aggregates (refcounting is
explicit and operand-based, so forwarding leaves the RefInc/RefDec sequence
byte-identical). BUT it only *fires* for a slot the escape analysis clears, and
that excludes **managed slices in a bounds-checked loop**: a bounds check
(`OP_BOUNDS_CHECK`/`OP_NIL_CHECK`/…) attaches a **fault pad** that
`EmitLoad`s every live **managed** local for cleanup-RefDec, and `allocaEscapes`
treats any `FaultPads` appearance as escape. So a `@[]T` slice escapes via the
very bounds check we want to eliminate. Net v1 reach:

- **Fires:** **raw slices** (`*[]T`/`*[]readonly T` — never RefDec'd, no pad
  reference), fixed **arrays** (their length is a const, so loop-BCE needs no
  forwarding at all), and managed aggregates with no faulting op in their live
  range.
- **Does NOT fire (deferred):** **managed slices** (`@[]T`) in a bounds-checked
  loop — blocked by the fault-pad escape. Making these forward needs
  fault-pad-aware handling (treat a pad's cleanup-load as a non-escaping use and
  forward it too, with off-CFG dominance care) — a separate v2 step.

So full loop-BCE on top of this covers **arrays + raw slices**; managed-slice
loop-BCE is a follow-up.

## Implementation notes (from the soundness review)

- Reuse mem2reg's **cross-block** delete-plus-rewrite machinery
  (`applyPromotion` shape: build the id-indexed repl map, rebuild blocks dropping
  the alloca/store/loads, rewrite all `Args`/`Phis[].Val` uses), **including the
  `assertNoSurvivingUses` fail-loud guard** — NOT the per-block `bceBlock` pattern.
- Enforce **same-block textual order**: `Dominates(b, b)` is true regardless of
  order, so a load in the store's block must be checked to come textually AFTER
  the store (a pre-store load in the same block must NOT be forwarded).
- Aggregate store/load types match in practice (no scalar-style `OP_CAST`
  grounding needed) — **assert** it rather than assume; if a mismatch ever
  appears, bail on that slot.

## Interaction with mem2reg (Phase 2a)

mem2reg runs first and promotes non-escaping **scalar** slots (with phis). It
leaves behind: aggregate slots (slices/structs), and any slot it declined
(multi-store scalars that needed a phi it couldn't place, escaping slots). A
non-escaping single-store slot that is *scalar* is already gone (mem2reg took
it — single store, no phi, forwarded). So in practice load-forwarding claims the
**aggregate single-store slots** — precisely the slice/struct slots loop-BCE
needs coalesced. (If a scalar single-store slot somehow survives, forwarding it
here is still sound — same operation.)

## Where it runs / ordering

`RunOptPasses`, at `-O1+`: `promoteScalars` (mem2reg) → **`forwardLoads`** →
`bceConstIndex` → `bceLoop` (Phase 3). Load-forwarding before loop-BCE so the
slice-length loads are coalesced when loop-BCE checks condition 5. `ComputeDom`
per function (shared with the other passes if we thread it through; recompute is
fine for v1).

## Tests

- **Unit (hand-built IR):** a non-escaping slice/struct alloca with one
  entry-dominating store and two loads → both loads forwarded to the store value,
  alloca/store/loads gone; a **two-store** slot → NOT forwarded (loads survive);
  an **escaping** slot → NOT forwarded; a store that does NOT dominate a load
  (store in one arm of an if, load after the merge) → NOT forwarded.
- **Conformance (via the loop-BCE tests, Phase 3):** the slice loop
  `for i:=0;i<len(s);i++{ s[i] }` now has its two `OP_SLICE_LEN` on one value, so
  loop-BCE eliminates the check (size/quality check); and the reslice-shrink loop
  KEEPS it. Load-forwarding itself also gets a direct size check (a function that
  loads a param slice's length twice emits one `OP_SLICE_LEN`, not two — or the
  redundant load is gone).

## Soundness review focus (for the adversarial pass)

The one thing to break: can a load be forwarded to `V` when the slot actually
holds something else at the load? That requires either (a) an escape the analysis
missed (a write through an aliasing pointer / a call mutating the slot), or (b) a
second store the "exactly one store" count missed (a store hidden in a FaultPad,
a store via a different op, a partial/field store to an aggregate slot). Both are
the review's targets. Aggregate **field** stores are the sharp edge: does gen
ever write a slot's field via `OP_GET_FIELD_PTR(A)+OP_STORE`? If so, A's address
flowed into `OP_GET_FIELD_PTR` → escape (caught) — but confirm.
