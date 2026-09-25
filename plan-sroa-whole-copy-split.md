# Plan: SROA — split whole-value aggregate copies so L2 stops pinning

**Status:** ✅ LANDED binate `9da1662f` (with dead-phi elimination `9c934585`), 2026-09-25 — see claude-todo-done.md. Tracked in
`claude-todo.md` under "record-churn residual is SROA-pinned aggregate copies". Builds on
`plan-ir-sroa.md` (Phase 0–2 landed) — read its L1/L2 contract first.

## Problem

SROA's L2 rule (`sroaLoadedValuesAllExtract`, `iropt/sroa.bn`) pins an aggregate alloca `A` if
any whole `OP_LOAD(A)` value has a non-`OP_EXTRACT` use. One such use is a whole `OP_STORE(P, v)`
of the loaded value `v` to some other pointer `P`. When `P` is itself a splittable alloca, the
existing fixpoint handles it (splitting `P` rewrites the store to per-field extracts, and `A`
qualifies on the next pass). When `P` is NOT a splittable alloca — a heap element pointer
(`out[i] = m`), a field of a non-splittable struct, a global, a pointer param — `A` is pinned
forever, and so is every alloca copied into `A` (the chain unpins from the tail).

record-churn's inner loop is exactly this: the inlined `mix`'s `m` is copied into the caller's
`var m`, which is stored whole into `out[i]`. Both stay in memory: two 8-store zero-inits, three
32-byte copies, and 8 field reloads per element, on both native backends (x64 217 instrs/element
vs LLVM's 25).

## Transform

A pre-step in each `sroaFunc` pass, before candidate collection:

For each aggregate alloca `A` that would be an SROA candidate **except** that some whole loads of
it are stored whole elsewhere, rewrite each such store

    v = OP_LOAD(A)            ; whole load
    OP_STORE(P, v)            ; whole store of the loaded value

into, at the store's position,

    for i in fields(T):
        fp_i = OP_GET_FIELD_PTR(P, i)   ; *T.field_i
        e_i  = OP_EXTRACT(v, i)         ; T.field_i
        OP_STORE(fp_i, e_i)

After the rewrite every use of `v` is an `OP_EXTRACT`, so `A` passes L2 and is split in the same
pass; mem2reg then promotes the field slots, and the per-field loads feeding the stores become SSA
values. If `P` is itself a splittable alloca, `GET_FIELD_PTR(P, i)` + load/store is an ordinary
L1 use of `P`, so `P` still splits (same result as today's whole-store expansion).

No aggregate value is rebuilt, so this stays inside the pin-don't-rebuild design: `OP_EXTRACT`,
`OP_GET_FIELD_PTR` and `OP_STORE` are all lowered by every backend and the VM.

## Gates (soundness + profitability)

Apply the rewrite for `A` only when ALL hold; otherwise leave the IR untouched:

1. `A` is an `OP_ALLOC` of a **non-managed struct** (`TYP_STRUCT`, `!sroaAggregateContainsManaged`)
   with `aggregateFieldsScalarReplaceable` — the same scope as the non-managed Phase 1 path. Raw
   slices and managed aggregates are out of scope for this step (managed stores carry refcount
   semantics; raw slices are not field-ptr-addressed).
2. `A` passes L1 (`sroaAllocaL1OK`) and `aggregateWholeStoresExtractable`.
3. Every use of every whole load of `A` is either an `OP_EXTRACT` (Args[0]) or the VALUE operand
   (Args[1], not Args[0]) of a whole `OP_STORE` in a normal block; no phi-entry use and no
   FaultPad appearance (a non-managed aggregate never legitimately appears in a pad).
4. For each such store, the destination's pointee type (peeled) is `Identical` to `A`'s type, so
   field index i means the same offset at `P` as in `A` (guards `bit_cast`/reinterpreted
   destinations). If any store fails this, `A` is left pinned (no partial rewrite).

Gate 3 is "A would be a candidate but for these copy-out stores" — it guarantees the rewrite is
only done when it actually unpins `A`, so a pinned-anyway alloca never has its 2-instruction
vector copy turned into 8 scalar stores for nothing.

**Aliasing.** `P` cannot alias `A`: L1 guarantees `A`'s address never escapes. The extracts read
the SSA value `v` (a snapshot at the load), exactly as the whole store did.

**Termination.** Each rewrite removes whole-store-of-load uses and never adds aggregate allocas, so
it cannot loop; it only fires when `A` then splits in the same pass, which the existing fixpoint
bound already counts.

## Testing

- Unit (`iropt`): hand-built IR — (a) copy-out to a heap element pointer unpins and splits `A`;
  (b) chain `A → B(alloca) → heap` splits both; (c) destination with a non-identical pointee type
  leaves `A` pinned and the store whole; (d) a load with another non-extract use (call arg,
  return) is not rewritten; (e) managed struct untouched; (f) nested-struct field (extract of a
  nested aggregate stored whole into the nested field-ptr) splits across the fixpoint.
- Conformance: a test exercising struct copy-out to slice elements / globals / pointer params /
  nested structs with values checked, across the default modes and the native modes
  (`builder-comp_native_*` on x64 here; aa64/arm32 native modes cannot run in this environment,
  so they need CI or real hardware).
- Measure record-churn native/llvm with `benchmarks/scripts/native-vs-llvm.sh` (+ `--self` noise
  floor) before/after, plus the whole suite and callgrind instruction counts for the loop; check
  the compiler self-compile (`perf/native-vs-llvm.sh`) for regressions.

## Out of scope here (separate todos)

Constant shift amounts not folded, x64 two-compare bounds checks, x64 `imul` index scaling,
per-iteration managed-slice header reloads — see the same `claude-todo.md` section.
