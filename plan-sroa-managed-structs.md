# Plan: SROA of managed structs (structs with a managed field)

Status: DONE — LANDED `0c9998917` (2026-09-17). See claude-todo-done.md. Claimed work-1/session 01LPZ7, 2026-09-16.
Continues the SROA line (see `plan-ir-sroa.md`, `claude-todo.md`). Managed-slice
Phase 2 already landed (`bcdc9ba47` + `ebeb6d087`); this is the next frontier.

## Problem

A struct *local* with a managed field (`@T` / `@[]T` / `@func` / `@Iface`) is
never SROA-able today — its alloca stays whole at -O2, so the field-by-field
stack copies the native backends emit for it are never collapsed. Managed
aggregates are a large share of the native↔LLVM copy-traffic gap the SROA line
exists to close.

## Root cause (verified on current main)

Managed struct-local cleanup is emitted **by-address**. `emitDecForManagedLocals`
(gen_local_cleanup.bn:43) cleans up a managed struct local via
`emitStructDtor(ctx.Func, b, slot.Ptr, st)`, which (gen_util_refcount.bn:89):

    rawPtr   = EmitBitCast(slot.Ptr, *uint8)
    EmitCall(__dtor_T, [rawPtr])           // by-address dtor call

`slot.Ptr` (the alloca) flows into a bitcast used as a call arg — an address
escape — so L1 pins the alloca, universally. This is the SAME cleanup path for
normal blocks AND fault pads: `emitPadCleanup` (gen_local_cleanup.bn:59) calls
`emitDecForManagedLocals`, so fixing that one function fixes both.

The `__dtor_T` body itself (genStructDtorWithName, gen_dtor_emit_bodies.bn:27) is
already field-by-field: for each managed field it does
`GET_FIELD_PTR(bitcast(param), i) → load → RefDec` (the exact per-field switch on
`@T`/`@[]T`/`@func`/`@Iface`). The inliner runs BEFORE SROA (opt.bn:27 then :33),
so a *small* struct's dtor is inlined and the pin becomes
`bitcast(&h) → GET_FIELD_PTR(i) → load → RefDec` (a bitcast-of-alloca pin); a
*large* struct's dtor stays an out-of-line `call __dtor_T(&h)` (an address-escape
pin). Both are the same root cause: cleanup reaches the fields THROUGH the
alloca's address (a bitcast), not through direct field-ptrs on the alloca.

Verified empirically (fresh -O2 dumps): `Holder{tag int; ref @Inner}` — dtor
inlined, `%Holder` alloca survives, pinned by `bitcast %v0`; `Big` with 5 managed
fields — `call __dtor_Big(&h)` survives, alloca pinned. Both print correct values
(cleanup is correct; it is just un-SROA-able).

## Approach (recommended): emit struct-local cleanup as direct per-field RefDecs

Change managed struct-local cleanup so it reaches each managed field through a
**direct `GET_FIELD_PTR(slot.Ptr, i)` on the alloca** — exactly the L1-forwardable
shape SROA already scalar-replaces — instead of `bitcast(&slot) → call __dtor_T`.

Concretely: factor the per-field managed-field RefDec switch out of
`genStructDtorWithName` into a shared helper, e.g.

    emitStructFieldRefDecs(f, b, structPtr @Instr, structTyp) @Block

(the existing switch: `@T` → load+managedPtrRefDec; `@[]T` → load+extract-refptr
+RefDec or ms-dtor; `@func`/`@Iface` → load+RefDec; nested struct/array field →
by-address elem-dtor call — see "Increment plan"). Then:

- `genStructDtorWithName` calls it on `bitcast(param)` (unchanged behavior — the
  shared dtor body is byte-identical).
- `emitDecForManagedLocals` calls it on `slot.Ptr` **directly** (no bitcast, no
  call) for a struct local, replacing `emitStructDtor(slot.Ptr)`.

After this, a managed struct local's cleanup is `GET_FIELD_PTR(h, i) → load →
RefDec` per managed field — which L1 already admits (it is the same field-ptr
shape a non-managed struct's field access uses). SROA then splits `h`: each
managed field becomes its own unpromoted managed slot (nil zero-init via the
EXISTING `makeFieldZeroInits`, which already emits `OP_CONST_NIL` for any managed
scalar kind), and the cleanup `load(GET_FIELD_PTR(h,i))` forwards to
`load(field_slot_i)` — the exact managed-slice refptr pattern already validated.
Non-managed fields promote via mem2reg. **This reuses the entire managed-slice
split machinery; the only genuinely new code is the cleanup-shape change plus a
candidate gate.**

### Why this is refcount-safe (the correctness spine)

SROA never adds or removes a RefInc/RefDec — it only redirects the field-ptr+load
those ops consume onto the split slot. The store `h.f = x` (RefInc(x);
RefDec(load(GET_FIELD_PTR(h,f))); store(GET_FIELD_PTR(h,f), x)) becomes
RefInc(x); RefDec(load(f_slot)); store(f_slot, x) — balance preserved. The
scope-exit / pad RefDec(load(GET_FIELD_PTR(h,f))) becomes RefDec(load(f_slot)).
The managed field slot is an unpromoted raw-copy cell with nil zero-init, so an
unwritten field RefDecs nil (a no-op). This is identical to how the landed
managed-slice refptr slot behaves. The backend must NOT implicitly RefDec a
managed alloca at teardown — VERIFIED already for managed slices (refcount is
explicit OP_REFDEC only); re-verify holds for a managed-field struct slot.

### Cost / tradeoff — DECIDED: gate to -O1+ (option b)

Inlining the per-field RefDecs at every struct-local cleanup site emits N
field-RefDecs inline instead of one shared `__dtor_T` call. To avoid that at -O0,
the inline shape is **gated to -O1+**; -O0 keeps the current by-address
`emitStructDtor` call. The user chose this (option b) over emitting the inline
shape unconditionally, since the opt-level needs threading into gen eventually
anyway. Plumbing: add `OptLevel int` to `GenCtx` (mirrors the existing gen-time
flag `EmitNilChecks`), set it at the cmd/bnc compiled-path gen sites from the
`optLevel` global, read it in `emitDecForManagedLocals` (`ctx.Gc.OptLevel >= 1`).
Default 0 is safe: a site that doesn't set it emits by-address cleanup (correct,
just un-SROA-able) — a wrong/missing OptLevel only costs optimization, never
correctness (SROA won't split a by-address-cleaned struct; an inline-cleaned
struct that isn't split is still correct). The shared `__dtor_T` is still emitted
for NON-local drops (a `box`'d struct on the heap, a nested managed-struct field,
an element dtor), so it does not disappear.

## Alternatives considered (and why not)

- **L1-recursion through the bitcast** (like the nested-aggregate field-ptr
  recursion): would need to admit `bitcast(alloca) → GET_FIELD_PTR → load` as a
  non-pinning use. Rejected: (a) only helps the *inlinable*-dtor case; the large
  struct's `call __dtor_T(&h)` is a true call-arg escape the recursion can't
  touch; (b) a bitcast can reinterpret the WHOLE value, so admitting it safely
  needs proving the bitcast is used ONLY as a field-ptr base — fragile. The
  cleanup-shape change avoids the bitcast entirely.
- **SROA rewrites the `call __dtor_T` itself** into per-field RefDecs on the split
  slots. Rejected: couples SROA to the refcount / dtor semantics (SROA would have
  to understand what `__dtor_T` does), which the pass is deliberately kept free of.

## Increment plan

**Increment 1 — leaf managed fields.** Admit a struct whose managed fields are all
"leaf" managed scalars: `@T`, `@[]T` (non-managed element), `@func`, `@Iface`.
These are exactly the cleanup-switch arms that are load+RefDec (forwardable).
DEFER (keep pinned) a struct with:
- a NESTED managed-struct / managed-array field (the switch's default arm is a
  by-address elem-dtor call on that field — pins its slot, like `@[]@T` stays
  pinned for slices);
- an `@[]@T` field (managed element — the ms-dtor stores the whole slice value's
  address to a scratch and passes it to the elem dtor — a whole-value-address
  escape, L2-pinned, same reason `@[]@T` locals are pinned).
Gate: a new predicate (managed struct whose every field is non-managed OR a
leaf managed scalar), distinct from `sroaAggregateContainsManaged` (which
currently excludes ANY managed content). L1: allow the candidate's field-ptr
cleanup appearances in FaultPads (the managed-slice path already does this via
`managedAllocaUsesOK` → `blockAllocaUsesAreL1` over pads).

**Increment 2 (later, separate) — nested managed-struct fields.** Recurse the
cleanup-shape change so a nested managed-struct field is also cleaned via direct
field-ptrs; then the fixpoint splits it like a nested non-managed aggregate.

## Files touched (increment 1)

- `pkg/binate/ir/gen_util_refcount.bn` / `gen_dtor_emit_bodies.bn`: factor
  `emitStructFieldRefDecs`; `genStructDtorWithName` calls it on `bitcast(param)`.
- `pkg/binate/ir/gen_local_cleanup.bn`: `emitDecForManagedLocals` calls the shared
  helper on `slot.Ptr` for a struct local instead of `emitStructDtor`.
- `pkg/binate/ir/sroa_transform.bn` / `sroa.bn`: candidate gate for a leaf-managed
  struct; L1 already handles the field-ptr+pad shape.
- Field slot types / zero-init: `makeFieldZeroInits` already nil-inits any managed
  scalar kind — no change expected.

## Validation

- ir unit tests: a leaf-managed struct scalar-replaces (managed field → nil-init
  managed slot, non-managed field promotes); a nested-managed-struct-field struct
  stays pinned; an `@[]@T`-field struct stays pinned.
- conformance: a managed-struct-local RefInc/RefDec exercise (alias, overwrite,
  scope-exit) AND a faulting-mid-scope variant (the pad RefDec's the split managed
  slot) — mirror 1267/1268. Assert refcount balance (no leak / double-free).
- Full builder-comp 0-fail; the same on VM (`builder-comp-int`) since the pad
  rewrite is VM-relevant; native aa64/x64/arm32 smoke; self-compile (gen1/gen2).
- Adversarial review focused on refcount balance + the managed field slot's
  teardown (the highest-risk area of the whole SROA effort).
- Confirm the -O0 cleanup-shape change is behavior-identical (differential O0 runs
  of the refcount conformance tests 052/053/075/100/094).

## Risks

1. **Refcount balance** (leak / double-free-abort) — highest risk. Mitigated by:
   SROA never touches a RefInc/RefDec (only forwards the load); the change reuses
   the validated managed-slice slot mechanism; per-increment refcount-balance
   tests + adversarial review.
2. **The cleanup-shape change is a semantic change to EVERY managed struct local**
   (not just SROA candidates) — it must be byte-behavior-identical at -O0. Guarded
   by the differential O0 runs.
3. **-O0 code size** from inline per-field RefDecs — RESOLVED: gated to -O1+ via
   `GenCtx.OptLevel` (option b). The opt-level plumbing is a small standalone piece
   landable first.

## IMPLEMENTATION FINDINGS (2026-09-16, work-1) — increment 1 is bigger than estimated

WIP checkpoint committed on work-1 (`7b1dd1dba`, not landable). What was built and
VERIFIED WORKING (hand-built ir unit tests + simple synthetic shapes split correctly,
O0==O2): the OptLevel plumbing, the cleanup reshape (inline per-field RefDecs at
-O1+), managedStructLeafEligible, collectManagedStructCandidates, and — a fix the
design did NOT anticipate — **padAware L1**: the design assumed the managed-slice
machinery would carry over, but slices use whole-load+extract in pads while a struct's
inline cleanup uses FIELD-PTRs in pads, and `fieldPtrChainScalarReplaceable` PINS on
any pad appearance of a field-ptr. Threading a `padAware` flag through the field-ptr
L1 chain (blockAllocaUsesAreL1 / fieldPtrChainScalarReplaceable / blockFieldPtrChainOK)
+ routing managed structs through validateSroaCandidates' pad-aware branch fixed that.

**Two real-gen blockers remain (the machinery does NOT yet split a real `var h S`
managed struct):**
1. **Whole-store zero-init.** `var h S` (managed, no initializer) zero-inits via a
   whole-store of h whose value is NOT extractable (not OP_LOAD/EXTRACT/CALL), so
   `aggregateWholeStoresExtractable` rejects h. The split slots already get their nil
   zero-init from makeFieldZeroInits, so the original whole-store is REDUNDANT — the
   fix is likely to recognize+drop a redundant zero-const whole-store (or make it
   extractable), but that is new rewrite machinery.
2. **Managed-field deref.** `h.ref.v` (dereferencing a `@T` field) adds a use of h
   that fails the L1 field-ptr check (a bit_cast surfaced in probing — origin not
   fully traced; needs confirming whether it is h or a legitimately-pinned temp).

**Assessment:** the design's "reuse the managed-slice machinery" was optimistic.
Managed structs' field-ptr access + whole-store zero-init + deref gen shapes each need
handling the slice path never exercised. Increment 1 is a larger piece than one commit.
DECISION PENDING (surfaced to owner): continue (investigate/fix the two blockers, depth
unknown), re-scope, or park the WIP.

## UPDATE (2026-09-17, work-1): both blockers pushed through — core WORKING

Per owner "push through the blockers", both real-gen blockers are fixed and
leaf-managed struct locals now scalar-replace across the common patterns:
- **Zero-init blocker**: `var h S` lowers to `store(h, OP_CONST_NIL)` (not
  extractable). FIX: drop the redundant nil whole-store in the rewrite
  (expandWholeStore) — the split slots already get per-field zeros from
  makeFieldZeroInits (managed→nil) + mem2reg (promotable).
- **Literal-init blocker**: `S{...}` registers a composite-literal cleanup temp
  whose partial-aggregate fault pad used a by-address dtor (bitcast → pins the
  alloca). FIX: apply the SAME cleanup reshape to the temp-cleanup path
  (emitTempRefDecs / emitTempCleanupSince), factored with the var path into a
  shared `emitManagedStructPtrDtor`.

Verified: all three patterns split (field-assign+zero-init, literal-init,
refcount alias+overwrite) — whole struct → just the managed field slots,
non-managed fields promote; O0==O2 correct incl. refcount (no double-free).
832 ir unit tests (4 new managed-struct tests in sroa_managed_test.bn).
Self-compile 3036/0 (LLVM gen1→gen2 -O2) + 3024/0 (VM). Native-aa64 self-compile
+ adversarial review IN FLIGHT. sroa_transform.bn split → sroa_managed.bn (length).

REMAINING before landing: native self-compile + review results; commit-structure
the WIP (5455a340e) into landable pieces; per-instance land approval. Follow-ups
(separate): the dead zero-temp (native DCE); nested-managed-struct + @[]@T fields
(deferred increment 2).

## INCREMENT 2 — starting notes (2026-09-18, all follow-ups landed)

LANDED so far: leaf-managed struct SROA (`0c9998917`); block-scope + defer cleanup
reshape (`72a0db78c`); dead-nil-const cleanup (`0ebb17126`).  Base = current main.
Machinery to build on (all landed): `emitManagedStructPtrDtor` (gen_local_cleanup.bn),
`emitStructFieldRefDecs` (gen_dtor_emit_bodies.bn), `managedStructLeafEligible` /
`isLeafManagedField` (sroa.bn), `collectManagedStructCandidates` (sroa_managed.bn),
padAware L1 chain (sroa.bn), the nil-const drop (sroa_rewrite.bn).

Increment 2 = the two field kinds `managedStructLeafEligible` currently EXCLUDES:

**2a — NESTED managed-struct field** (a field that is itself a managed-containing
struct).  Today `isLeafManagedField` returns false for it, so the outer struct is
ineligible.  Root pin: `emitStructFieldRefDecs`'s `default` arm (gen_dtor_emit_bodies.bn)
cleans a nested struct/array field via `emitDtorOrCopyCall(elemDtorName, bitcast(fieldPtr))`
— a by-address elem-dtor call that bitcasts the field-ptr → pins the field's slot after
the outer splits.  FIX shape: for a nested LEAF-managed struct field, recurse —
emit inline per-field RefDecs on the field-ptr (a recursive `emitStructFieldRefDecs`
call) instead of the by-address call; and make `managedStructLeafEligible` recurse
(a nested struct field is OK iff it is itself leaf-eligible).  Then the outer splits
(field-ptr-only cleanup), the nested struct field becomes its own slot, and the
FIXPOINT + the landed nested-aggregate L1-recursion split it further.  Watch: array
fields with managed elements stay by-address (arrays aren't SROA'd — keep pinned);
refcount balance on the recursive RefDec.

**2b — `@[]@T` field** (managed slice whose ELEMENT needs destruction).  Harder:
the ms-dtor-with-element path (`emitStructFieldRefDecs` TYP_MANAGED_SLICE arm,
`Elem.NeedsDestruction()` true) calls the managed-slice dtor by ADDRESS
(`bitcast(fieldPtr)` → ms-dtor), and that ms-dtor stores the whole slice value's
address to a scratch + passes it to the elem dtor — a whole-value-address escape.
This is the SAME blocker that keeps `@[]@T` slice LOCALS pinned (Phase-2 slices only
did non-managed-element).  Recommend: do 2a first; 2b likely needs the managed-slice-
of-managed-element SROA generalized first (a separate, bigger piece — assess before
committing).

Validation (both): ir unit tests in sroa_managed_test.bn (a nested-managed-struct-field
struct splits; refcount balance); an O0==O2 refcount exercise; self-compile
(builder-comp-comp + native-aa64, -O2); adversarial review (refcount-critical).  Note
conformance is -O0 by default, so SROA is validated via self-compile + O0==O2, not a
plain conformance test.  Land per-increment with per-instance approval.
