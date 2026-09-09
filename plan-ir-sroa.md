# Plan: IR-level SROA (scalar replacement of aggregates) — the biggest native↔LLVM gap-closer

**Status:** DRAFT v2 (2026-09-08, work-1), REWRITTEN after an adversarial plan
review found the v1 model was **wrong about the IR** (v1 keyed on
`OP_GET_FIELD_PTR` field-ptr uses; Binate's IR is register-aggregate SSA, so v1's
Phase 1 would have captured ~nothing). v2 is grounded on the actual IR model,
empirically verified via `--emit-llvm`. Not yet started; **v2 itself wants a
re-review before Phase 0.** Sibling of `plan-mem2reg-phase2a.md` (mem2reg landed,
`ea7687188`).

## Problem & goal (unchanged; payoff re-confirmed by the review)

The native↔LLVM `-O2` gap is **~53% aggregate-copy traffic** (per-hot-function
attribution, 2026-09-08): the native backend materializes `@[]T`/`*[]T`/struct
locals in stack slots and copies them field-by-field slot→slot — **308,903
mem→mem copy-pairs = 25.6% of native instructions**, 41.6% of them 4-word
managed-slice-header copies, 94% internal locals. The review CONFIRMED these
copies survive the full `-O2` pipeline (inline + mem2reg + load-forward + BCE).
mem2reg promotes only non-managed SCALAR allocas, so aggregate locals stay in
memory. **SROA's job:** scalar-replace SROA-eligible aggregate allocas so mem2reg
then promotes the non-managed fields and the copies collapse.

## THE IR MODEL (v1 got this wrong — this is the crux)

Binate IR is **register-aggregate / first-class-value SSA**. For an aggregate
local `x` (struct or slice) held in an `OP_ALLOC` slot:

- **A read of `x` is a WHOLE-aggregate `OP_LOAD(alloca)`** yielding an aggregate
  SSA VALUE (`gen_expr.bn:106` — every ident read is `EmitLoad(alloca, typ)`).
- **A write `x = v` is a WHOLE-aggregate `OP_STORE(alloca, aggValue)`**
  (`gen_store_slot.bn:56-92` `emitStoreManagedSlot` — one `OP_STORE` of the whole
  value, for non-managed AND managed-scalar-kind slots incl. `@[]T`).
- **A field read is `OP_EXTRACT(aggValue, i)` on the VALUE**, not a field-ptr on
  the slot (`ir_ops.bn:11-13`: `EmitSliceLen = EmitExtract(slice, 1)`).
- Field-by-field GET_FIELD_PTR+STORE exists too, but for SPECIFIC idioms
  (comma-ok merge slots, the by-address managed **copy-helper** function body —
  `gen_copy_emit.bn`, which is itself a whole-aggregate-address escape), NOT for
  an ordinary local `b = a`. **v1's "already lowered field-by-field" premise was
  false** (it conflated the copy-helper with local assignment).
- The LLVM backend already handles "an aggregate SSA value stored to a pointer"
  as the generic `OP_STORE` case (`emit_copy_ssa.bn:12-16,42`, the `.ssN`
  scalarized store); the native backends lower the same to slot copies. This IS
  the copy traffic.

So SROA operates over **aggregate SSA values + their alloca slots**, scalarizing
whole load/store and forwarding extracts — NOT over field pointers.

## Established facts (reviewer-verified)

- Aggregate locals are `OP_ALLOC(aggregateTyp)`; the only `@Instr`-typed value
  operands are `Args @[]@Instr` and `Phis[].Val` (`ir.bni:491-590`) — so
  "find all uses of V" = scan `Args` + `Phis[].Val` across `f.Blocks` AND
  `f.FaultPads`. (v1's completeness claim here was CORRECT.)
- mem2reg's `isPromotableAlloca` accepts a raw `TYP_POINTER` (a slice's data
  field) and a word-width `TYP_INT` (len) — so a split slice's data+len ARE
  promotable. A managed-slice's refptr is `TYP_MANAGED_PTR` — NOT promotable
  (Phase 2 keeps it in memory).
- mem2reg's escape predicate `blockUsesAllocaNonLoadStore` already ALLOWS whole
  `OP_LOAD`/`OP_STORE` at `Args[0]` and treats `GET_FIELD_PTR` as an escape — so
  it is NOT directly reusable; SROA needs its own predicate (below), though the
  use-scan traversal is shared.
- Pass ordering: inserting SROA between `inlineCalls` and `promoteScalars` in
  `RunOptPasses` (`opt.bn:33-42`) is correct and lands before `bceBlock`'s
  "mutating passes run before me" constraint — no ordering hazard (reviewer
  verified).

## What gets SROA'd — corrected promotability (v2)

An `OP_ALLOC(aggTyp)` is a **splittable slot** iff EVERY use of the alloca value
is one of:

1. the ADDRESS (`Args[0]`) of a **whole** `OP_LOAD(alloca)` (field reads are
   `OP_EXTRACT` on the loaded value — allowed), or
2. the ADDRESS (`Args[0]`) of a **whole** `OP_STORE(alloca, aggValue)`, or
3. (optional, if present) a CONSTANT-index `OP_GET_FIELD_PTR(alloca, i)` whose
   only uses are load/store at its `Args[0]`.

ANY other appearance is an **escape** → un-splittable: the alloca address as a
call arg (by-address ABI), an sret/return of the whole aggregate, `box()`,
`bit_cast`, `&agg` / address-of, the address stored AS A VALUE
(`OP_STORE.Args[1]`), a `PhiEntry.Val`, a dynamic-index element, or (for a
MANAGED aggregate) ANY appearance in a `FaultPad`. Enumerate and test each.

## The transform (v2, per the reviewer's corrected shape)

For a splittable slot of aggregate type T with fields f0..fn:

- Allocate a fresh scalar `OP_ALLOC(fieldTyp_i)` per field.
- **Whole store** `OP_STORE(alloca, aggVal)` → per-field
  `OP_STORE(field_alloca_i, OP_EXTRACT(aggVal, i))`.
- **Whole load** `OP_LOAD(alloca)` feeding `OP_EXTRACT(load, i)` → forward each
  `OP_EXTRACT(load, i)` to `OP_LOAD(field_alloca_i)`.
- **Genuine whole-VALUE uses** of `OP_LOAD(alloca)` (a call arg by-value, a
  return, storing the value elsewhere) → rebuild the aggregate with
  `OP_INSERT` from the per-field loads. If a whole-value use can't be cleanly
  rebuilt, **pin the slot un-splittable** (conservative).
- Delete the original aggregate alloca. mem2reg then promotes the non-managed
  field allocas; pure copies collapse entirely (reviewer: mem2reg fully collapses
  the fields for pure copies).

## Phasing (corrected)

- **Phase 0 — infra + splittable-slot analysis + a validator, no rewrite.**
  Unit-tested on hand-built IR: a raw-slice alloca used only by whole load/store
  → splittable; one passed by-address to a call → not; a managed slice appearing
  in a FaultPad → not; a dynamic-index array → not.
- **Phase 1 — NON-MANAGED aggregates** (`*[]T`, POD structs — all fields
  non-managed). No refcount, no fault-pad interaction. This is the hazard-free
  first cut and still the right start — BUT it is a **materially bigger transform
  than v1 described**: scalarize whole-stores, forward extracts, rebuild via
  insert at whole-value uses. Captures the non-managed copy share (roughly the
  ~58% that isn't the 41.6% managed-slice headers, minus the ~6% ABI-mandated
  by-address, minus mixed/escaping cases).
- **Phase 2 — MANAGED aggregates (the 41.6% payoff; genuinely hard, no shortcut).**
  The reviewer refuted v1's "partial SROA keeps existing pad handling" escape
  hatch: the managed-slice refcount spine LOADS THE WHOLE 4-word value and
  `OP_EXTRACT`s field 2 — in normal blocks (`gen_util_refcount.bn:310-313`
  RefInc, `:359-382` RefDec) AND in every FaultPad (`gen_local_cleanup.bn:31-34`
  `emitPadCleanup`); the elem-dtor path even re-materializes the whole value and
  passes its ADDRESS to a dtor (a whole-aggregate-address escape). So ANY managed
  split must rewrite every whole-value-load+extract-2 including in pads — that IS
  the pad/refcount rework, not something a residual alloca sidesteps. Phase 2 is
  a separate design pass; do NOT promise it cheaply.

## Additional items the review surfaced

- **Companion fold (MINOR).** No `extract(insert(...))` fold exists, and native
  represents an aggregate SSA value as a memory data-region pointer
  (`aarch64_emit.bn:141-175`). Pure copies collapse via mem2reg, but MIXED cases
  that rebuild via `OP_INSERT` then re-`OP_EXTRACT` won't fully collapse on native
  without an `extract-of-insert` peephole. Add it (IR-level or per-backend) as a
  payoff-completeness follow-on; not a correctness bug.
- **Nesting / fixpoint (MINOR).** Splitting a struct whose field is itself a
  slice/struct exposes a new aggregate field alloca (scalar-only mem2reg won't
  promote it). Either recurse / run SROA to a fixpoint, or explicitly pin
  nested-aggregate fields in v1. Decide and document in Phase 0.
- **Payoff re-estimate (Q5).** Size the phases by counting **whole-aggregate
  OP_STORE sites** (by managed-ness), NOT field-ptr sites, in the self-compile IR.

## Validation (unchanged intent)

Per phase: unit tests (compiled + VM) on hand-built IR (splittable/not; whole
store→per-field; extract-forwarding; mem2reg promotes the fields; whole-value use
rebuilds via insert); the three native conformance modes + LLVM + VM; the
`scalar-diff` differential harness; a refcount-balance test for Phase 2. The
native-vs-llvm gap benchmark + the 308,903 mem→mem-copy-pair count before/after
is the direct success measure. Each phase independently green + cherry-pickable;
adversarial review per phase.
