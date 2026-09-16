# Plan: native aggregate-copy load→store fusion (eliminate redundant intermediate buffers)

Status: DESIGN (revised after an adversarial safety review; for a second review /
sign-off before implementation). Owner: temp-5 (2026-09-15). The review found a
CRITICAL error in the first draft — the safety condition was framed as lifetime-only,
but an aggregate load is a *snapshot*, so a source *written* between load and use
(the `a, b = b, a` swap idiom) corrupts silently too. §2.1/§3/§4/§5/§6 now carry the
two-part (no-free AND no-write) condition.
Tracked by the `claude-todo.md` entry "Native aggregate-copy: eliminate redundant
intermediate buffers (load→store fusion)". Follows the landed aggregate-copy-*width*
work (aarch64 `a7d49192d`, x64 `800467f7c`, arm32 `f8a532d96`), which widened each
copy; this eliminates the *number* of copies.

## 1. Problem

The native backends give every aggregate `OP_LOAD` its **own** stack data region
(reserved in `PlanFrame`, `pkg/binate/native/common/common.bn`) and copy the loaded
bytes into it (`emitAggLoad`). A subsequent `OP_STORE` (struct-copy) then copies
*that region* into the destination. So a by-value aggregate move runs **src → temp →
dst** — one redundant full-width copy.

Measured on the landed width-fixed backend (aarch64 disassembly):

- `*dst = *src` (a 4-word struct through raw pointers): the aggregate load copies
  src→temp (4 LDP/STP after the width fix), then the store copies temp→dst (another
  4) — **2 copies where LLVM emits 1** (`ldp q0,q1,[src]; stp q0,q1,[dst]`, direct).
- A managed-slice `b.s = a`: **five** 4-word copies threaded through temp buffers
  (≈40 memory ops) versus LLVM's single direct move — the aggregate value is
  materialized repeatedly across the refcount-adjustment spine.

This redundant-copy traffic is native-only: the same IR feeds the LLVM backend, but
LLVM's codegen loads the aggregate into an SSA value and its own passes elide the
temp. The width fix already made each copy fast; this plan removes the extra copy.

## 2. Mechanism — the elision path already exists

`emitAggLoad` (each backend) already has an alias path:

```
var myAlloc int = rm.LookupAlloc(ins.ID)   // the load's own data region
if myAlloc < 0 {
    Mov(rd, srcPtr)   // NO region reserved → just alias the source pointer
    return
}
// ... otherwise copy src bytes into the region at myAlloc ...
```

When `PlanFrame` does **not** reserve a data region for an aggregate `OP_LOAD`,
`emitAggLoad` sets the load's result register (and its spill slot) to the **source
pointer** — no copy. The downstream `OP_STORE` struct-copy then reads that pointer
and copies **src → dst directly**. That is exactly load→store fusion, with zero new
copy machinery.

So the entire change reduces to a single decision in the shared `PlanFrame`: **for
which aggregate `OP_LOAD`s is it safe to skip the data-region reservation** (keeping
only the spill slot that will hold the source pointer)? One change in
`pkg/binate/native/common`; all three native backends benefit; no IR-semantics or
interpreter blast radius (the LLVM backend and the VM are untouched — this is a
native frame-planning decision).

### 2.1 Consumer contract, and what materialization actually buys

Every consumer of an aggregate value reads it through the spill-slot **pointer** to
its bytes — and only READS it (verified: the load's result is never a store
*destination* `Args[0]` nor a `GET_FIELD_PTR`/`GET_ELEM_PTR` base; the front end
computes lvalue addresses separately, never from an aggregate load value):

- `OP_STORE` struct-copy: copies `SizeOf` bytes from the pointer to the dest (the
  load is the *source* `Args[1]`, read-only; the dest is a separate pointer).
- `OP_EXTRACT` field: reads `[ptr + FieldOffset]`.
- `OP_BOX`: heap-copies `SizeOf` bytes out of the pointer.
- aggregate arg-spread / return marshalling: reads words from the pointer.

So no *consumer* writes through the pointer. But that does **not** make aliasing
value-transparent. An aggregate `OP_LOAD` in SSA is a **snapshot** of the source
bytes at the load point; the materialized private region is both a durable copy AND
an immutable one. A materialization therefore buys **two** things:

1. **Lifetime** — the copy stays valid after the source is freed.
2. **Snapshot immutability** — the copy is unaffected if some *later* instruction
   (not necessarily a consumer of this value) writes the source's memory before the
   value is read.

Aliasing forfeits both. So the safety question is **two-part**: alias only if,
between the load and every use of its result, the source is **neither freed nor
written**. (§3.1's `return container[i]` is the free case; the swap idiom below is
the write case — both must be excluded.)

**The write case — the struct-swap idiom `a, b = b, a` (found in review).** Parallel
assignment lowers (`gen_assign_parallel.bn`) to all-loads-then-all-stores; for a
non-managed struct there is no intervening RefInc/RefDec:

```
t0 = LOAD b
t1 = LOAD a          ; snapshot of a
STORE a = t0         ; WRITES a  — not a call/refdec, so a "no-freeing-op" rule misses it
STORE b = t1         ; reads t1; if t1 aliases a, reads the OVERWRITTEN a (=B) → b := B (WRONG)
```

Aliasing `t1` to `a` silently loses `A`. `*p, *q = *q, *p` on struct pointees lowers
identically. Any correct rule MUST treat the intervening `STORE a` (a write to the
source) as a barrier — "no *write* to the source between load and use," not merely
"no *freeing* op."

## 3. Safety condition (the entire risk) — two parts

Aliasing a source is safe **iff, between the load and every use of its result, the
source is (A) not freed and (B) not written.** A false positive on (A) is a
**use-after-free**; a false positive on (B) is **wrong-value** corruption (the swap
idiom, §2.1). Both are silent, so the rule must be provably conservative on both.

**(A) Lifetime.** In Binate's refcounting model, source bytes become invalid only
when the managed object that owns them is `RefDec`'d to zero (its dtor frees the
backing), or when its stack storage leaves scope. Stack storage (`alloca`s, spilled
params) lives to function end. So the source is freed mid-function only by a
**RefDec-to-zero of the source's owning managed object**.

**(B) Snapshot.** The source bytes must not be overwritten between the load and the
use. The writers to worry about are: any `OP_STORE` whose destination may alias the
source, and any call (`OP_CALL`/`OP_C_CALL`/indirect) that may write through a
pointer to the source. Proving "does not alias the source" is alias analysis;
conservatively, **any** store or call between load and use is a barrier.

### 3.1 The canonical UNSAFE case

`return container[i]` where `container : @[]T` (T aggregate):

1. Load `container[i]` — the element lives inside `container`'s **backing**.
2. Function-end scope cleanup RefDecs `container`; if that was the last ref, the
   backing (and the element bytes) are freed.
3. The sret copy delivers the return value — **after** step 2 in the current
   lowering (documented in `PlanFrame`'s existing comment and
   `gen_return.bn`'s cleanup ordering).

If the load aliased `container`'s backing, step 3 reads freed memory → UAF. The
private copy (made at step 1, before cleanup) is what makes this correct today.
**This case must keep materializing.**

### 3.2 The SAFE cases

- `*dst = *src` (raw/`*T` pointers): no managed owner, nothing freed between load and
  store. Safe.
- `b = a` / `b.s = a` where `a` is a param or local: `a`'s owning object is RefDec'd
  only at **function-end** scope cleanup, which is *after* the mid-function store
  that consumes the load. The RefInc/RefDec ops the assignment itself emits
  (RefInc `a`'s backing, RefDec the *old* `b`) do **not** free `a`. So the source is
  live at the store → safe. (This is the hot 41.6% managed-slice-copy case.)

The distinguishing structural tell (confirmed in the LLVM IR): **unsafe** loads flow
to the **return** (consumed at/after function-end cleanup); **safe** loads are
consumed by a store/extract that completes **before** the source's owner is freed.

## 4. The analysis

For an aggregate `OP_LOAD %v = load %src`, "safe to alias" requires **all uses of
`%v`** (scanned whole-function **including fault pads** — the cleanup spine reads
whole aggregates in pads, exactly as `sroa.bn`'s `valueUsesAllExtract` scans both
`f.Blocks` and `f.FaultPads`) to satisfy both:

1. **(A) source not freed before the use** — classify `%src`'s producer (`Args[0]`):
   - *Stable* (valid to function end): derives directly from a param, an `alloca`
     (`OP_ALLOC`), or a global.
   - *Ephemeral / unknown* (may be freed mid-function): a managed **temporary** whose
     end-of-statement RefDec can free it (`foo().field`), OR a **raw `*T`** whose
     pointee owner cannot be tracked — a raw pointer severs the owner linkage, so it
     may point into a managed backing that a mid-function reassignment (Axiom-5 `b =
     other` frees old `b`) or nested-scope exit releases (review Hole 2). Treat raw
     `*T` as *unknown* → materialize, unless proven otherwise.
2. **(B) source not written before the use** — no `OP_STORE` (of any kind) and no
   call between the load and the use. (The swap idiom's intervening `STORE a` is the
   tripwire; §2.1.)

**Safe to alias iff** every use is (A)-safe and (B)-safe.

### 4.1 Proposed increments (revised after the safety review)

- **Increment 1 (provably safe, minimal).** Skip materialization iff: `%v` has a
  single use (whole-function, incl. fault pads); that use is an `OP_STORE` (struct-
  copy) with `%v` as the value operand in the **same block**; and **no** `OP_STORE`,
  `OP_CALL`, `OP_C_CALL`, `OP_REFDEC`, or other write-or-free op lies between the load
  and the store. Provably (A)-safe (nothing between frees the source) and (B)-safe
  (nothing between writes the source). Correctly **rejects the swap idiom** (the
  intervening `STORE a` is a barrier). Captures `*dst = *src` and truly-adjacent
  non-managed copies. **Misses** the hot managed case (its store sits after
  RefInc/RefDec).
- **Increment 2 (the hot managed case).** Relax (A) to "no RefDec of **`%src`'s
  owning object**" and (B) to "no store/call that may alias `%src`" between load and
  use, plus "`%v` does not flow to the return value (or any other function-end / out-
  of-scope use)." Requires source-owner classification (Hole 2: raw `*T` stays
  *unknown*) and alias reasoning to prove the RefInc `a.backing` / RefDec-old-`b` and
  the store's own dest do not touch `a`'s header. Captures `b = a` / `b.s = a`.
  Materially larger analysis and blast radius; both UAF and wrong-value are in play.

**Recommended path:** implement Increment 1, **measure** the redundant-copy reduction
on the cmd/bnc self-compile (static count of aggregate-load materializations elided,
N-vs-L memory-op ratio). Increment 1 alone may be a small win (the hot case is
managed); the measurement decides whether Increment 2's alias analysis — now
understood to be genuinely harder and doubly-unsafe-if-wrong — is worth it. (Matches
the todo's "measure the traffic reduction" note.)

## 5. Validation strategy

A false positive is memory corruption, so validation is heavier than the width fix:

1. **Full native conformance, all three arches** (`builder-comp_native_aa64`,
   `builder-comp_native_x64_darwin`, `builder-comp_native_arm32_baremetal`) — 0 new
   failures.
2. **LLVM-vs-native `-O0`/`-O2` differential** over the whole conformance corpus — 0
   mismatch (the elision must not change any observable result).
3. **The `return container[i]` UAF regression** (and kin: returning a struct field of
   a managed local, returning an element of a managed slice) — must **still
   materialize**; explicit conformance tests asserting the returned value is correct
   (not freed garbage). UAF tripwire (case A).
4. **The swap wrong-value regression** — `a, b = b, a` and `*p, *q = *q, *p` for
   struct/array `a,b` (both non-managed and managed), asserting the values actually
   swap. This is the case-(B) tripwire the review surfaced; it MUST be in place before
   the elision lands, since it is the shape Increment 1 targets and could miscompile.
5. **Refcount-balance / leak tests** — the elision must not change any RefInc/RefDec
   (it only removes a copy); assert refcount returns to baseline for the managed
   copy cases (like `iife-capturing-no-leak`).
6. **Unit tests** on the `PlanFrame` predicate: for hand-built IR, assert a region is
   reserved for the unsafe shapes (freed source, written source, return-flowing) and
   elided only for the safe shapes.
7. **Adversarial review** focused on both failure modes: "construct an input where an
   aliased load's source is freed OR written before a use."

## 6. Risks and open questions

- **Value-snapshot, not just lifetime (review Hole 1 — the design's original error).**
  The safety condition is two-part (§3): a source that is *written* between load and
  use is as fatal as one that is freed. The write case is reachable from ordinary
  source (`a, b = b, a`), so it is not a corner case. Every increment's barrier set
  must include stores/calls, not only frees.
- **Source-owner classification precision (review Hole 2).** Mis-classifying an
  ephemeral source as stable → UAF. When in doubt, materialize. A **raw `*T`** severs
  the managed-owner linkage, so its pointee owner cannot be tracked and a mid-function
  RefDec (reassignment / scope exit) of that owner is invisible — classify raw `*T` as
  *unknown* (materialize), NOT "stable unless from a within-statement temp."
- **Use-scan must cover fault pads and all uses (review Hole 3).** The predicate's
  use-count must be whole-function **including `f.FaultPads`** (as `sroa.bn` does) —
  the cleanup spine reads whole aggregate values in pads. A same-block-only or
  blocks-only scan undercounts uses and could elide a materialization a pad still
  reads.
- **Multi-block liveness.** If `%v` is used in a different block than the load,
  the use-span analysis must be block-aware (dominance/liveness). Increment 1's
  same-block restriction sidesteps this; Increment 2 must handle it or stay
  conservative (materialize on any cross-block use).
- **Interaction with the width fix.** Fusion removes the intermediate copy; the
  remaining single copy still uses the wide LDP/STP·MOVUPS·LDM path. No conflict.
- **Interaction with the extract-consumed-field materialization** (SROA line,
  `ebeb6d087`). Confirm the elision predicate composes with existing
  materialize-only-consumed-field logic, so nothing double-decides.
- **`OP_EXTRACT`-only consumers.** A load consumed purely by `OP_EXTRACT` (field
  reads) is also aliasable under the same two-part rule; include or defer explicitly
  (don't silently only handle `OP_STORE`).

## 7. Alternative considered — IR-level peephole

Rewrite `OP_LOAD → OP_STORE` into a direct copy in the IR (removing the load).
**Rejected as the primary approach:** it changes the IR both backends and the
**interpreter** consume (dual-mode), widening the blast radius and requiring VM
changes, for a redundancy the LLVM backend already elides. The `PlanFrame`
frame-planning decision is native-local and reuses the existing alias path, so it is
the lower-risk lever. (Revisit only if a native-local rule proves insufficient.)
