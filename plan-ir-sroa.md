# Plan: IR-level SROA (scalar replacement of aggregates) — the biggest native↔LLVM gap-closer

**Status:** DRAFT for adversarial review (2026-09-08, work-1). Not yet started.
Sibling of `plan-mem2reg-phase2a.md` (mem2reg is landed, `ea7687188`). SROA is
the pass that FEEDS mem2reg: it breaks aggregate `OP_ALLOC`s into per-field
scalar `OP_ALLOC`s so mem2reg can then promote the non-managed scalar fields to
SSA — killing the aggregate-copy traffic that dominates the native gap.

## Problem & goal

An adversarial per-hot-function attribution of the native `-O2` self-compile
(2026-09-08; done-log `34fdc65e0` neighborhood + the "Native codegen quality"
todo entry) found the native↔LLVM gap is **~53% aggregate-copy traffic**: the
native backend materializes `@[]T` / `*[]T` / struct locals in stack slots and
copies them field-by-field slot→slot. Whole-binary, **308,903 mem→mem copy-pairs
= 25.6% of all native instructions**, 41.6% of them 4-word managed-slice-header
copies, and **94% internal locals** (NOT ABI-mandated). clang eliminates exactly
this via SROA + mem2reg + copy-propagation; native has no aggregate-level pass,
so a trivial leaf like `mangle.charsEqual` gets a 336-byte frame and copies its
slice headers through the stack 4+ times.

mem2reg (`plan-mem2reg-phase2a.md`) promotes only **non-managed scalar** allocas
(int/bool/raw-ptr); it explicitly EXCLUDES slices, structs, managed, and float,
to sidestep the refcount- and fault-pad-hazard families. So aggregate locals
never get promoted and stay in memory. **SROA fills that gap**: split the
aggregate alloca into its fields, so each non-managed scalar field becomes a
mem2reg-promotable scalar alloca, and the aggregate copies become per-field
scalar copies that mem2reg + copy-forwarding then eliminate.

**Goal:** an IR pass that scalar-replaces SROA-eligible aggregate allocas, run
BEFORE mem2reg in `RunOptPasses`, so the aggregate-copy traffic collapses on the
native backend (and LLVM/VM benefit too — it's backend-neutral IR). Target: a
meaningful cut into the ~53% aggregate-copy share of the gap.

## Established facts (IR recon, `temp-binate-1` tree)

- **Aggregate locals are `OP_ALLOC(aggregateTyp)`** (`b.EmitAlloc(structTyp)` /
  `EmitAlloc(sliceTyp)` — gen_composite.bn, gen_assert*.bn). Same node mem2reg
  already targets, just an aggregate `TypeArg`.
- **Field/element access is `OP_GET_FIELD_PTR(alloca, fieldIdx, fieldTyp)` /
  `OP_GET_ELEM_PTR`**, then a plain `OP_LOAD`/`OP_STORE` on the resulting field
  pointer (gen_composite.bn:61/299-302, gen_assert.bn:222-224). A slice header's
  data/len/refptr/backing are fields 0..3.
- **Whole-aggregate copies** appear two ways: (a) field-by-field via
  EmitGetFieldPtr + EmitStore (gen_copy_emit.bn:216-221); (b) an aggregate-typed
  `OP_STORE` of an aggregate SSA value, which the LLVM backend lowers via
  emit_copy_ssa.bn (the `.ssN` scalarized store) and the native backends lower to
  field-by-field slot copies. A **managed**-aggregate copy is a save-copy-destroy
  (RefInc the new managed fields, RefDec the old) — the refcount spine.
- **mem2reg's promotability model** (reuse its escape scan): an alloca is
  promotable iff every use is the ADDRESS operand (`Args[0]`) of a plain
  `OP_LOAD`/`OP_STORE` — ANY other appearance (`OP_STORE.Args[1]` = address stored
  as a value, `OP_GET_FIELD_PTR`/`GET_ELEM_PTR`/`bit_cast`/call-arg/`PhiEntry.Val`,
  or ANY appearance in `Func.FaultPads`) pins it unpromotable. SROA's escape scan
  is the SAME shape but the "legal" uses are `OP_GET_FIELD_PTR`/`GET_ELEM_PTR`
  with a CONSTANT index (plus the load/store on those field ptrs).
- Only `@Instr`-typed operand fields are `Args @[]@Instr` and `Phis[].Val` — so
  "find all uses of alloca V" = scan every instr's `Args` and every `PhiEntry.Val`
  across `f.Blocks` AND `f.FaultPads` (mem2reg established this).

## What gets SROA'd (promotability — v1, deliberately conservative)

An `OP_ALLOC` of an aggregate type is a **splittable slot** iff ALL hold:

1. **Type is a struct or slice/managed-slice** with a statically-known field
   layout (`types.StructLayout` / the fixed slice field set). Arrays: v1 splits
   only CONSTANT-indexed, small arrays (or defer arrays entirely — see Q3).
2. **Every use is a CONSTANT-index `OP_GET_FIELD_PTR` / `OP_GET_ELEM_PTR` on the
   alloca**, whose only uses in turn are the address (`Args[0]`) of a plain
   `OP_LOAD` / `OP_STORE`. ANY other use of the alloca — the whole-aggregate
   address as a call arg (by-address ABI), `bit_cast`, `OP_STORE.Args[1]`,
   `box()`, a dynamic-index `GET_ELEM_PTR`, a `PhiEntry.Val`, or ANY appearance in
   a `FaultPad` for a MANAGED aggregate — pins it un-splittable. (This is the
   escape edge; the by-address-call case is the ABI-mandated 6% the attribution
   found — correctly left alone.)
3. **v1 scope split by managed-ness (phasing below).**

The transform: allocate a fresh scalar `OP_ALLOC(fieldTyp)` per accessed field;
rewrite each `OP_GET_FIELD_PTR(alloca, i)` to reference field-i's new alloca
(with a zero offset, or just replace the field-ptr value with the field alloca);
delete the original aggregate alloca. mem2reg (next pass) then promotes each
non-managed scalar-field alloca. A whole-aggregate `OP_STORE`/copy of a
split-eligible alloca is first lowered to per-field stores (it already is, on the
field-by-field path) so SROA sees only field accesses.

## Phasing (each phase lands green + cherry-pickable; adversarial review per phase)

- **Phase 0 — infra + eligibility scan (no rewrite).** The splittable-slot
  analysis + a validator, unit-tested against hand-built IR (a struct alloca with
  only const-field access → splittable; one with a call-arg-by-address use → not;
  a dynamic-index array → not). Mirrors mem2reg Stage 0. No codegen change.
- **Phase 1 — NON-MANAGED aggregates only.** Split raw slices (`*[]T`) and
  structs whose fields are ALL non-managed scalars/pointers. No refcount, no
  fault-pad interaction (a non-managed aggregate never appears in a pad). This is
  the clean, hazard-free cut — captures the raw-slice + POD-struct portion. Feeds
  mem2reg directly.
- **Phase 2 — MANAGED aggregates (the big payoff, the hard part).** Managed-slice
  headers are 41.6% of the copies. A managed-slice's field 2 (refptr) and a
  managed struct's managed fields are refcounted; the aggregate's save-copy-
  destroy and its FaultPad cleanup must be preserved after splitting. Options to
  evaluate in Phase 2's own design pass: (a) split only the NON-managed fields of
  a managed aggregate, leaving the managed field(s) in a residual small alloca
  (partial SROA — captures the data/len/backing scalar traffic, keeps the refptr
  in memory with its existing pad handling); (b) full split with per-field
  refcount + pad rewriting. (a) is likely the right v1 of Phase 2.

## Sharp edges / risks (call out per phase; the review must probe these)

1. **Managed-field refcounting (Phase 2).** Splitting must not drop or double a
   RefInc/RefDec. The save-copy-destroy of a managed aggregate and the FaultPad
   that RefDec's it on unwind must stay correct. This is the top risk — it's why
   mem2reg excludes managed entirely. Partial SROA (2a) sidesteps most of it.
2. **Fault pads.** A managed aggregate can appear in `Func.FaultPads` (cleanup).
   v1 treats ANY managed-aggregate pad appearance as un-splittable (belt-and-
   suspenders), like mem2reg. Phase 2a must define how a split's residual managed
   alloca inherits the pad entry.
3. **Escape completeness.** Missing an escape (a bit_cast, a call-arg-by-address,
   `OP_STORE.Args[1]`, a `&agg` that reuses the alloca id, a dynamic index) →
   splitting a slot whose whole-aggregate address is needed elsewhere → silent
   wrong-address. The escape scan must cover `f.Blocks` AND `f.FaultPads` and
   every `Args`/`PhiEntry.Val`, exactly like mem2reg's (reuse it).
4. **Whole-aggregate stores/loads.** An aggregate `OP_STORE`/`OP_LOAD` of the
   whole alloca (not via a field ptr) must either be pre-lowered to per-field
   accesses before SROA sees it, or pin the slot un-splittable. Decide which.
5. **Layout/ABI neutrality.** SROA is pure IR-level; it must not change any
   type's memory layout (that's `pkg/types`, a language contract) — it only
   changes how a LOCAL is realized. A split aggregate that must still be passed
   whole (ABI) is exactly case (2)-escape → not split.
6. **Ordering in RunOptPasses.** SROA runs BEFORE mem2reg (so mem2reg promotes
   the fields) and BEFORE load-forwarding/BCE. Confirm no pass ordering hazard.
7. **Backend neutrality.** The pass is `pkg/binate/ir`, BUILDER-compiled — stay
   in the subset. It emits ordinary OP_ALLOC/LOAD/STORE the LLVM + 3 native
   backends + VM already handle; no backend change should be needed (verify).

## Open questions for the adversarial review

- **Q1:** Is partial SROA (split non-managed fields, keep managed fields in a
  residual alloca) sound and worthwhile as Phase 2's v1, or does the residual
  alloca's pad/refcount handling make full-split simpler?
- **Q2:** Are whole-aggregate `OP_STORE`s already lowered to per-field stores
  before RunOptPasses runs, or does SROA need to lower them (or pin on them)?
- **Q3:** Arrays — split constant-indexed small arrays in v1, or defer all arrays?
- **Q4:** Does splitting interact with the aggregate-return / sret path (a struct
  returned by value) or with `box()` — both are escapes, but confirm the scan
  catches them.
- **Q5:** Estimated gap reduction: Phase 1 (non-managed) captures what fraction of
  the 308K copies vs Phase 2 (managed-slice headers, 41.6%)? A quick IR-level
  count of splittable non-managed vs managed aggregate allocas in the self-compile
  would size the phases.

## Validation

- Per-phase: unit tests (compiled + VM) on hand-built IR (splittable/not,
  correct field rewrite, mem2reg promotes the fields after); the three native
  conformance modes + LLVM + VM (correctness); the `scalar-diff` differential
  harness; a refcount-balance test for Phase 2 (managed).
- The native-vs-llvm gap benchmark + the mem→mem-copy-pair count (the 308,903
  metric) before/after — the direct measure of success.
- Hygiene; each phase independently green and cherry-pickable.
