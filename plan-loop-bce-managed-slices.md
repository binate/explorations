# Managed-slice loop-BCE — fault-pad-aware load-forwarding

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
