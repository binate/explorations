# Managed-slice loop-BCE — fault-pad-aware load-forwarding

> **⚠️ 2026-08-28: the store-forwarding approach below is WRONG for managed
> slices — it hit a by-address ABI miscompile. See "By-address blocker" at the
> top. The fault-pad soundness analysis (reviewed DESIGN SOUND) still holds and is
> reusable, but the core mechanism must change from store-forwarding to
> redundant-load-elimination. The implementation attempt was reverted.**

## By-address blocker (the real problem, found by implementing + testing)

A `@[]T` managed slice is a 4-word aggregate that the backends pass/represent
**by address** (an LLVM `ptr` param; the IR type is still the slice value). This
pass forwards a slot's LOADS to the single **stored value** `V`. When `V` is a
by-address value (a managed-slice param, or any by-address aggregate), the
backend then does `extractvalue %BnManagedSlice %ptr` on the **pointer** — the
materialization the original `OP_LOAD` provided is gone → wrong-code (a slice-value
read of an address; `msmin`/`msloop` SEGV/miscompile on the VM at `-O1+`). This is
the "representation is ABI" trap: store-forwarding assumes the stored value is a
first-class by-VALUE materialization, which holds for scalars and raw slices
(small, by-value) but NOT for by-address aggregates.

The landed raw-slice/array load-forwarding is unaffected (raw slices are by-value;
by-address structs escape via fieldwise/field-access ops, so they were never
forwarded — confirmed: the landed code gives correct results for managed slices,
simply declining to forward them).

## Corrected approach: RLE — materialize one load after the store, forward to it

> Design v2 (2026-08-28) — to be adversarially soundness-reviewed before
> implementing, per the refcount-sensitive-pass process.

### The by-value / by-address split (the fix's foundation)

Store-forwarding (forward loads to the stored value `V`) is correct **iff `V`'s
backend representation is by-VALUE**. The predicate the codebase already shares
between IR-gen and codegen is **`types.Type.IsByvalParam()`**: true ⟺ `SizeOf > 16`
⟺ passed by-address (`ptr byval`). So:

- **`!a.TypeArg.IsByvalParam()`** (≤16B: scalars, raw slices `*[]T`=16B, small
  structs) → **keep store-forwarding** unchanged. Validated (raw-slice loop-BCE
  landed, 2990/0). A ≤16B value is first-class in every backend, so forwarding to
  it is safe.
- **`a.TypeArg.IsByvalParam()`** (>16B: managed slices `@[]T`=32B, large structs)
  → **RLE** (below). Forwarding to `V` here is the exact bug that was reverted
  (`V` is a `ptr`, so the backend `extractvalue`s a slice out of an address).

This split is conservative-safe: RLE is correct for *any* slot, so using it for
the >16B class (and store-forwarding only for the proven-safe ≤16B class) can
never miscompile. It also leaves the landed ≤16B path untouched.

### RLE mechanism (for a >16B by-address slot `A`)

Conditions (reuse the reviewed-sound fault-pad analysis verbatim):
1. **Relaxed escape.** `A` escapes iff used as anything other than `Args[0]` of a
   plain `OP_LOAD`/`OP_STORE` in `f.Blocks`, or `Args[0]` of a plain `OP_LOAD` in
   a `FaultPads` block.
2. **Exactly one store `S`** (value `V`) in `f.Blocks`.
3. **`S` dominates every normal load** (same-block ⇒ textually before).
4. **`S` dominates every pad load's fault point** (the faulting op in `f.Blocks`
   whose `PadBlock` is that pad, by pointer identity). Bail if any pad load has no
   locatable fault point or is undominated.

Mutation (differs from store-forwarding — this is the redesign):
- **Insert `L0 = OP_LOAD(A)` immediately after `S`** (at `storePos+1` in
  `storeBlk`). Since `S` dominates every load + every pad fault point (conds 3–4),
  `L0` (right after `S`) dominates them too. `L0 == V` because `A` is single-store
  and `S` just wrote `V`. `L0` is a **load result → by-VALUE in every backend**
  (LLVM reconstructs the aggregate; the VM materializes it into a register) — the
  property store-forwarding lacked.
- **Forward every existing load** of `A` (normal + pad) to `L0`; delete them.
  **Keep `A`, `S`, and `L0`.**

Result: the guard's `len(s)` and the access's `len(s)`, formerly
`OP_EXTRACT(guardLoad,1)` / `OP_EXTRACT(accessLoad,1)`, both become
`OP_EXTRACT(L0,1)` → loop-BCE's `lengthProvablyLE` matches → inner check
eliminated. The entry `RefInc`, exit `RefDec`, and every pad `RefDec` become
`RefX(OP_EXTRACT(L0,2))` — the same slice object `V`, so the refcount trajectory
is byte-identical (Binate refcounting is explicit + operand-based; a plain
`OP_LOAD` carries no implicit inc/dec, so inserting `L0` is refcount-neutral).

### Why inserting `L0` (vs reusing an existing dominating load)

Robustness: `L0` is constructed to dominate everything (given conds 3–4), so
there is no "no single dominating load exists → bail" gap and no loop-carried
subtlety (an existing guard load lives *inside* the loop). Cost is one load
instruction that replaces N — net fewer loads, and for a param slice it hoists the
header load out of the loop (a bonus).

### Apply integration

`forwardLoadsFunc` runs two independent sub-passes over the same `DomInfo` (CFG is
unchanged by either — no dominator recompute):
1. **Store-forwarding** (≤16B slots) → the existing `applyPromotion` path,
   unchanged except the analyzer now filters to `!IsByvalParam()`.
2. **RLE** (>16B slots) → a new `applyRLE`: re-scan `f.Blocks` for fresh
   positions (the store-fwd rebuild moved things), analyze, then in one rebuild
   insert each `L0` after its `S`, drop the forwarded loads (by id) from both
   `f.Blocks` and `f.FaultPads`, and reuse the shared `rewriteUsesInBlocks` +
   `assertNoSurvivingUses` (with `allocaDeleted` all-false — RLE deletes loads,
   not the alloca; the fail-loud still catches any un-rewritten forwarded-load
   use).

### Tests

- **Unit (hand-built IR, `@[]T`-typed slot with a FaultPad):** RLE fires — a new
  `OP_LOAD` appears right after the store; guard+access+pad loads are gone and all
  `OP_EXTRACT`s read the new load; `A`+`S` remain. KEPT cases: a ≤16B slot still
  takes the store-forwarding path (no inserted load); a pad load whose fault point
  the store does not dominate → not forwarded; a two-store `@[]T` → not forwarded.
- **loop-BCE end-to-end:** `for i:=0;i<len(s);i++{ s[i] }` over a `@[]int` slice
  eliminates the inner check at `-O2` (1→0) AND runs correctly on **both** the
  compiled backend and the **VM** (the reverted bug SEGV'd the VM at `-O1+`;
  `msmin`/`msloop` must give 30 / 47 and balance refcounts).
- **Conformance:** full native `-O2` stays green, plus a **VM** run (the fault-pad
  path is VM-only) over the managed-refcount/stress cells (`250_managed_stress`,
  `spec/18-memory/*`).

### Risk

Refcount-sensitive (rewrites managed cleanup RefDec operands) — gets an
adversarial soundness review before landing. The by-value/by-address split rests
on `IsByvalParam` being the true representation boundary (it is the shared IR-gen
↔ codegen predicate); RLE's correctness rests on "a load result is by-value in
every backend," which is backend-agnostic and the reason this approach is robust
where store-forwarding was not.

---

# (Original design — store-forwarding; superseded by the by-address blocker above)

Follow-up that takes loop-BCE from "arrays + raw slices" to also cover **managed
slices (`@[]T`)**. Lives in `pkg/binate/ir` (load-forwarding, `load_forward.bn`).

## The blocker (recap)

loop-BCE needs the guard's `len(s)` and the access's `len(s)` to be `OP_EXTRACT`
of the SAME SSA slice value — which load-forwarding produces by forwarding the
two slice-header loads to the single stored value. But for a `@[]T` slice,
`allocaEscapes` treats **any FaultPads appearance** as escape, and a bounds check
(`attachFaultPad` → `emitDecForManagedLocals`, `gen_local_cleanup.bn:27`) emits an
`EmitLoad(slot, …)` of every live managed local into its cleanup pad. So the slice
slot escapes via the very check we want to eliminate → not forwarded → lengths
stay distinct → loop-BCE can't match.

## Fix: allow a pad's cleanup-LOAD as a non-escaping use, and forward it

Relax load-forwarding's escape/forward so a managed-slice slot forwards despite
its cleanup pad loads. A pad's `EmitLoad(A)` is a plain read for RefDec, not a
real escape; forwarding it to the stored value `V` is correct because Binate
refcounting is explicit and operand-based (established by the Phase-2b review):
the pad's `RefDec(load(A))` becomes `RefDec(V)` — the same slice object — so the
refcount trajectory is byte-identical.

### Conditions (all required)

1. **Relaxed escape.** `A` escapes iff used as anything other than (a) `Args[0]`
   of a plain `OP_LOAD`/`OP_STORE` in `f.Blocks`, or (b) `Args[0]` of a plain
   `OP_LOAD` in a `FaultPads` block. ANY other appearance (a store's value
   `Args[1]`, `GET_FIELD_PTR`/`GET_ELEM_PTR`, a non-load pad use, a phi entry) →
   escape, as today.
2. **Exactly one store** `S` (value `V`) in `f.Blocks`.
3. **`S` dominates every NORMAL load** (`f.Blocks`) — as today (same-block ⇒
   store textually before load).
4. **`S` dominates every PAD load's fault point.** A pad load reads `A` at a
   fault point; forwarding it to `V` is correct only if `S` reaches that point
   with no other store — i.e. `S` dominates the faulting op. For each pad `P`
   that contains an `OP_LOAD(A)`, find the faulting ops in `f.Blocks` whose
   `PadBlock` is `P` (pointer identity — `PadBlock` is a `*Block` back-ref into
   `FaultPads`), and require `S`'s block to dominate each such op's block (or
   same block, `S` before it). If any pad-load has no locatable fault point, or
   the store doesn't dominate it, bail (do not forward). (This is airtight even
   though it leans on gen's definite-assignment for cleanup: we *check* it, not
   assume it.)
5. If all hold: forward **all** loads of `A` (normal + pad) to `V`, and delete
   `A` + `S` + all its loads (normal + pad). Clean IR — no dead loads in the hot
   loop (unlike a "forward normal loads, keep the slot" variant, which would
   leave a dead per-iteration load on the native/VM backends).

### Why #4 is the soundness crux

With a single store `S`, `A` holds `V` at any point `S` dominates, and holds
initial memory before `S`. A pad load forwarded to `V` is wrong iff the pad can
run before `S` (reading initial memory). #4 forbids exactly that. For the target
case (a param slice stored at entry, or a local stored before the loop), `S`
dominates the loop and all its fault points, so #4 holds; a slice whose cleanup
pad is reachable before its store is (correctly) not forwarded.

## Ordering / interaction

Same hook (`forwardLoads` in `RunOptPasses`, before `bceLoop`). This only widens
which slots forward; the mutation phase (`applyPromotion`) already deletes an
alloca's loads across `f.Blocks` via `allocaDeleted` + `instrIsDeleted` — extend
it (or the rebuild) to also drop the pad `OP_LOAD(A)`s from `FaultPads` blocks
(they're `OP_LOAD` with `Args[0] == A`, so the same `instrIsDeleted` predicate
applies; just run the block rebuild over `FaultPads` too, and `rewriteUsesInBlocks`
already covers `FaultPads`).

## Tests

- **Unit (hand-built IR with a FaultPad):** a managed-slice slot loaded normally
  (guard + access) AND in a fault pad (cleanup) with the store dominating the
  fault point → forwarded (all loads, incl. pad, become `V`; the pad now
  `RefDec`s `V`). KEPT cases: a pad load whose fault point is NOT dominated by the
  store; a non-load use in a pad; a two-store managed slice.
- **loop-BCE end-to-end:** `for i:=0;i<len(s);i++{ s[i] }` over a `@[]int` slice
  now coalesces and eliminates the inner bounds check at `-O2` (currently KEPT),
  output + **refcount balance** unchanged (a leak/double-free check, since this
  touches managed cleanup).
- **Conformance:** full native `-O2` stays green, especially the managed-refcount
  / dtor / stress cells (`250_managed_stress`, `spec/18-memory/*`) — this is the
  managed-refcount-sensitive change, so it gets a dedicated adversarial soundness
  review (fault-pad + refcount are the two hazard areas mem2reg/2b deliberately
  avoided).

## Risk

Higher than the raw-slice work: it deletes a managed local's slot and rewrites
its cleanup RefDec's operand. The whole thing rests on the refcount-transparency
invariant (plain load/store carry no implicit inc/dec) and on #4's dominance. Both
get an adversarial review before landing.
