# Plan: SROA of managed structs (structs with a managed field)

Status: DESIGN — awaiting sign-off. Claimed work-1/session 01LPZ7, 2026-09-16.
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

### Cost / tradeoff to flag

At -O0 (SROA off), inlining the per-field RefDecs at every struct-local cleanup
site emits N field-RefDecs inline instead of one shared `__dtor_T` call — more
-O0 code for a struct with many managed fields used across many functions. The
shared `__dtor_T` is still emitted and used for NON-local drops (a `box`'d struct
on the heap, a nested managed-struct field, an element dtor), so it does not
disappear. If the -O0 bloat matters, an alternative is to gate the inline-cleanup
shape to -O1+ — but gen currently has no opt-level threaded through; that would be
extra plumbing. **Recommend: emit the inline shape unconditionally** (simpler; -O0
code size is not perf-critical) unless you prefer the gating.

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
3. **-O0 code size** from inline per-field RefDecs — flagged above; gate to -O1+
   if the user prefers (needs opt-level plumbing into gen).
