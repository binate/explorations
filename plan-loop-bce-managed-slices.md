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
materialization the original `OP_LOAD` provided is gone → wrong-code. This is
the "representation is ABI" trap: store-forwarding assumes the stored value is a
first-class by-VALUE materialization, which holds for scalars and raw slices
(small, by-value) but NOT for by-address aggregates.

### This is a LIVE latent bug in the LANDED code (MAJOR), not just a blocker

The adversarial review (2026-08-28) corrected my earlier belief that the landed
code "simply declines to forward managed slices." **It does not.** A managed-slice
(or any >16B struct) PARAM is *whole-loaded*, not field-accessed, in gen's entry
RefInc / `len(s)` / exit-RefDec (`gen_func.bn`, `gen_local_cleanup.bn`), so the
slot does NOT escape; with the single param-spill store it IS forwarded — to the
by-address `paramRef` → invalid LLVM. **Confirmed** by a full compile (not
`--emit-llvm`, which doesn't validate):

```
$ bnc -O2 -o bin  (func slen(s @[]int) int { return len(s) })
error: '%v0' defined with type 'ptr' but expected '%BnManagedSlice ...'
  %v3 = extractvalue %BnManagedSlice %v0, 2
```

The failing function in a real build is `pkg/builtins/startup.SetArgs(args @[]char)`
— a stdlib function; a >16B struct param (`func idb(p Big) Big`) trips it too.

**Why it's dormant:** `RunOptPasses` is gated `level < 1` (`opt.bn`), so bnc's own
IR opt runs ONLY at bnc `-O1+`; the gen/CI build and the standard conformance suite
optimize via **clang `--cflag -O2`**, never bnc `-O2` (test compiles run at bnc
`-O0`). Loop-BCE was validated with `BINATE_FLAGS=-O2` on the **native/VM** backends
— which resolve `paramRef` to a spilled aggregate and accept the invalid IR — but
the **LLVM backend at bnc `-O2` was never run**, so the wrong-code hole is real yet
unhit. The `IsByvalParam` filter below is therefore a **required correctness fix**
(covering managed slices AND large structs), not merely a conservative guard.

## Corrected approach: RLE — materialize one load after the store, forward to it

> Design v2 (2026-08-28) — to be adversarially soundness-reviewed before
> implementing, per the refcount-sensitive-pass process.

### Two ORTHOGONAL axes (the review's key correction — do not conflate them)

The reverted v1 and the first v2 draft both conflated two independent questions.
They must be decoupled or the optimization dies on 32-bit (the primary target):

| slot | by-address? (materialization) | in fault pads? (escape) |
|---|---|---|
| `@[]T` 64-bit (32B) | **yes → RLE** | yes → relaxed escape |
| `@[]T` 32-bit (16B) | **no → store-forward** | yes → relaxed escape |
| raw slice (16B) | no → store-forward | no → plain escape |
| large struct >16B | yes → RLE (or just decline) | no → plain escape |

- **Axis 1 — escape relaxation** (does a fault-pad `OP_LOAD(A)` count as escape?).
  Gate on **managed-slice type** (the only thing gen loads into a cleanup pad),
  regardless of size. A `@[]T` on *either* word size needs this, or its
  bounds-check cleanup pad makes it escape and it is never forwarded → loop-BCE
  can't match → **the whole feature does nothing on 32-bit** if this is gated on
  size. Conditions 1 & 4 (pad-load-allowed escape; store dominates pad fault
  point) apply whenever the relaxation is in effect.
- **Axis 2 — materialization** (forward to `V`, or to an inserted load `L0`?).
  Gate on **`a.TypeArg.IsByvalParam()`**: >16B (by-address) → **RLE** (insert
  `L0`); ≤16B (by-value, incl. the 16B 32-bit `@[]T`) → **store-forward to `V`**
  (a ≤16B value is first-class in every backend — validated by raw slices).

`IsByvalParam()` is the correct by-value/by-address boundary (it is the exact
predicate gen uses to decide `ptr byval` lowering — `gen_func.bn`). It is *not*
literally `SizeOf > 16`: it has a kind-gate and an **HFA exemption** (an HFA
struct passes by value in SIMD registers even when >16B, `abi_hfa.bn` — live on
aa64), so an HFA correctly routes to store-forwarding. Use the predicate, not a
size comparison.

### MAJOR-#1 fix, standalone: filter store-forwarding to `!IsByvalParam()`

Independently of managed slices, `analyzeForwardAlloca` (the store-forwarding
analyzer) must **decline any `IsByvalParam()` slot** — that alone closes the live
LLVM wrong-code hole for by-address params/large structs (it stops forwarding a
slot to a by-address `V`). This is a correctness fix that should stand on its own
(and covers large structs, which the managed-slice RLE does not re-enable — a
>16B non-managed struct simply isn't forwarded, which is correct, just not
optimized). RLE (below) then RE-ENABLES forwarding for the >16B *managed-slice*
case via the materializing load.

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
   unchanged except the analyzer (a) filters to `!IsByvalParam()` and (b) applies
   the relaxed (pad-load-allowed) escape when the slot is a managed slice, so a
   16B 32-bit `@[]T` in a loop actually forwards.
2. **RLE** (>16B managed slices) → a new `applyRLE`: re-scan `f.Blocks` for fresh
   positions (the store-fwd rebuild moved things), analyze, then in one rebuild
   insert each `L0` after its `S`, drop the forwarded loads **by their specific
   ids** from both `f.Blocks` and `f.FaultPads`, and reuse the shared
   `rewriteUsesInBlocks` + `assertNoSurvivingUses` (with `allocaDeleted` all-false
   — RLE deletes loads, not the alloca; the fail-loud still catches any
   un-rewritten forwarded-load use). **Delete-by-id, NOT by-alloca** (MINOR #4):
   `instrIsDeleted` removes loads because their alloca is deleted; since RLE keeps
   `A` and `L0` is itself an `OP_LOAD(A)`, a by-alloca rule would delete `L0` too.
   Unit-test that `L0` survives.

### Tests

- **Unit (hand-built IR, `@[]T`-typed slot with a FaultPad):** RLE fires — a new
  `OP_LOAD` appears right after the store; guard+access+pad loads are gone and all
  `OP_EXTRACT`s read the new load; `A`+`S` remain. KEPT cases: a ≤16B slot still
  takes the store-forwarding path (no inserted load); a pad load whose fault point
  the store does not dominate → not forwarded; a two-store `@[]T` → not forwarded.
- **LLVM backend at bnc `-O1+` (guards MAJOR #1 at the exact bug site):** a
  managed-slice param and a >16B struct param must **full-compile** (clang
  validates the IR — `--emit-llvm` does NOT) at bnc `-O2`. This is the coverage
  gap that let the landed bug hide.
- **loop-BCE end-to-end:** `for i:=0;i<len(s);i++{ s[i] }` over a `@[]int` slice
  eliminates the inner check at `-O2` (1→0) AND runs correctly on the LLVM,
  native, and **VM** backends (the reverted bug SEGV'd the VM at `-O1+`;
  `msmin`/`msloop` must give 30 / 47 and balance refcounts). Include a **32-bit
  (arm32)** end-to-end asserting the inner check is actually eliminated there
  (MAJOR #2 — the 16B `@[]T` store-forward path).
- **Conformance:** `BINATE_FLAGS=-O2` on the **LLVM** mode (`builder-comp` — the
  never-run path that hides MAJOR #1), the native modes, and a **VM** run (the
  fault-pad path is VM-only) over the managed-refcount/stress cells
  (`250_managed_stress`, `spec/18-memory/*`).

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
