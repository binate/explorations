# Plan: IR-level SROA (scalar replacement of aggregates) — the biggest native↔LLVM gap-closer

**Status:** v3 (2026-09-09, work-1) — REVIEWED and ready for Phase 0. v1 was
**wrong about the IR** (keyed on `OP_GET_FIELD_PTR`; Binate IR is
register-aggregate SSA); v2 re-grounded it on whole `OP_LOAD`/`OP_STORE` values +
`OP_EXTRACT` (empirically verified via `--emit-llvm`); a re-review of v2 found two
transform holes now folded into v3: (1) the promotability check is **two-level**
(L1 alloca uses + L2 loaded-value uses), because `box`/`return`/by-value-arg
consume the loaded VALUE not the alloca; (2) there is **no aggregate-value
rebuild** primitive (`OP_INSERT` absent, `OP_STRUCT_LIT` unlowered), so slots
whose loaded value must survive whole are **pinned**, not split-then-rebuilt; plus
a load-site-placement rule for forwarded field-loads. With these, the re-review
cleared Phase 0. Sibling of `plan-mem2reg-phase2a.md` (mem2reg landed,
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

## What gets SROA'd — promotability (v3, TWO-LEVEL)

Splittability is a **two-level** check (the re-review's key correction: `box`,
`OP_RETURN`, and by-value call-args consume the LOADED VALUE, not the alloca — so
a single-level "scan the alloca's uses" would see only load/store and WRONGLY
declare such a slot splittable, then hit a whole-value use it cannot handle):

**L1 — alloca uses.** Every use of the `OP_ALLOC` value must be one of:
1. the ADDRESS (`Args[0]`) of a **whole** `OP_LOAD(alloca)`, or
2. the ADDRESS (`Args[0]`) of a **whole** `OP_STORE(alloca, aggValue)`, or
3. (if present) a CONSTANT-index `OP_GET_FIELD_PTR(alloca, i)` used only as
   load/store `Args[0]`.
Any other alloca appearance is an L1 escape → un-splittable: `&agg` / address-of
(`gen_expr.bn:189-193` returns the alloca directly), `bit_cast` of the alloca,
the alloca stored AS A VALUE (`OP_STORE.Args[1]`), a by-address call arg, a
`PhiEntry.Val`, or (MANAGED aggregate) ANY `FaultPad` appearance.

**L2 — loaded-value uses.** For EVERY whole `OP_LOAD(alloca)`, every use of the
LOADED VALUE must be an `OP_EXTRACT(load, i)` (constant i). If a loaded value has
ANY non-extract use — a by-value call arg, an `OP_RETURN`, `box()`, storing the
value elsewhere — the slot is **pinned un-splittable** (there is no cheap way to
reconstruct an aggregate value from scalars on the real backends — see the
transform). These value-level escapes are distinct from L1's alloca-level ones;
enumerate and test both sets.

(Plus the phasing split by managed-ness below.)

## The transform (v3 — pin, do NOT rebuild)

For a splittable slot (passes L1+L2) of aggregate type T, fields f0..fn:

- Allocate a fresh scalar `OP_ALLOC(fieldTyp_i)` per field.
- **Whole store** `OP_STORE(alloca, aggVal)` → per-field
  `OP_STORE(field_alloca_i, OP_EXTRACT(aggVal, i))`. Valid for ANY aggregate
  value source (load result, call result, `make_slice`, rodata) — `OP_EXTRACT`
  is a real backend-lowered op (reviewer-confirmed general).
- **Whole load** `OP_LOAD(alloca)` (all uses are `OP_EXTRACT`, by L2):
  materialize the per-field loads `OP_LOAD(field_alloca_i)` **at the original
  whole-load's position** — NOT at each extract site: a per-field store to the
  same slot intervening between the whole-load and an extract would make
  mem2reg's reaching-def resolve the wrong (post-store) value, a silent
  snapshot-aliasing miscompile — and rewrite each `OP_EXTRACT(load, i)` to
  REFERENCE the field-load SSA value.
- Delete the original aggregate alloca. mem2reg then promotes the non-managed
  field allocas; pure copies collapse entirely.

**No aggregate-value REBUILD.** There is no usable primitive to reconstruct an
aggregate value from scalars on the real target: `OP_INSERT` does not exist, and
`OP_STRUCT_LIT` is unlowered on LLVM + all three native backends and a `BC_NOP`
in the VM (reviewer-verified: opcode enum + codegen/native dispatch +
`vm/lower_instr.bn:424`; the `gen_assert_commaok.bn:23` comment states aggregate
values are built via the alloca-merge idiom, not a struct literal). So a slot
whose loaded value must survive as a WHOLE value is caught by L2 and simply
**pinned** — never split-then-rebuilt. (Materializing at a genuine ABI escape
would use the alloca-merge idiom into a fresh, non-splittable slot — an ABI
boundary, not a collapse — out of scope for v1.)

## Phasing (corrected)

- **Phase 0 — infra + splittable-slot analysis + a validator, no rewrite.**
  Unit-tested on hand-built IR: a raw-slice alloca used only by whole load/store
  → splittable; one passed by-address to a call → not; a managed slice appearing
  in a FaultPad → not; a dynamic-index array → not.
- **Phase 1 — NON-MANAGED aggregates** (`*[]T`, POD structs — all fields
  non-managed). No refcount, no fault-pad interaction. This is the hazard-free
  first cut and still the right start — BUT it is a **materially bigger transform
  than v1 described**: scalarize whole-stores, forward extracts to field loads,
  and PIN any slot whose loaded value has a non-extract use (L2). Captures the
  non-managed copy share (roughly the
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

- **No companion fold needed (v2's "extract-of-insert peephole" is moot).** With
  the pin-don't-rebuild transform there is no `OP_INSERT`/rebuild to fold away.
  Slots whose loaded value must survive whole are pinned (L2), so no mixed
  split+rebuild case reaches the backend. (Native does represent an aggregate SSA
  value as a memory data-region pointer, `aarch64_emit.bn:141-175`, but that only
  concerns the pinned/escaping aggregates SROA leaves alone.)
- **Nesting / fixpoint (MINOR).** Splitting a struct whose field is itself a
  slice/struct exposes a new aggregate field alloca (scalar-only mem2reg won't
  promote it). Either recurse / run SROA to a fixpoint, or explicitly pin
  nested-aggregate fields in v1. Decide and document in Phase 0.
- **Payoff re-estimate (Q5).** Size the phases by counting **whole-aggregate
  OP_STORE sites** (by managed-ness), NOT field-ptr sites, in the self-compile IR.

## Validation (unchanged intent)

Per phase: unit tests (compiled + VM) on hand-built IR (splittable/not per L1+L2;
whole store→per-field; extract-forwarding at the load site; mem2reg promotes the
fields; a slot with a non-extract loaded-value use is PINNED, not split); the
three native conformance modes + LLVM + VM; the
`scalar-diff` differential harness; a refcount-balance test for Phase 2. The
native-vs-llvm gap benchmark + the 308,903 mem→mem-copy-pair count before/after
is the direct success measure. Each phase independently green + cherry-pickable;
adversarial review per phase.
