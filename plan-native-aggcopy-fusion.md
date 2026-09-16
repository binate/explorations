# Plan: native aggregate-copy load→store fusion (eliminate redundant intermediate buffers)

Status: DESIGN (for review before implementation). Owner: temp-5 (2026-09-15).
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

### 2.1 Consumer contract (why aliasing is otherwise transparent)

Every consumer of an aggregate value reads it through the spill-slot **pointer** to
its bytes:

- `OP_STORE` struct-copy: copies `SizeOf` bytes from the pointer to the dest.
- `OP_EXTRACT` field: reads `[ptr + FieldOffset]`.
- aggregate arg-spread / return marshalling: reads words from the pointer.

If the pointer is the **source** address (aliased) instead of a private copy, each
consumer reads the *source* bytes — which is the correct value, byte-for-byte. So
aliasing changes nothing about the *values* a consumer sees. **The only thing
materialization buys is lifetime**: a private copy stays valid after the source is
freed. Therefore the safety question is purely a lifetime/liveness question.

## 3. Safety condition (the entire risk)

Aliasing a source is safe **iff the source bytes are still valid at every use of the
load's result.** A false positive here is a **use-after-free** — silent memory
corruption — so the rule must be provably conservative.

In Binate's refcounting model, source bytes become invalid only when the managed
object that owns them is `RefDec`'d to zero (its dtor frees the backing), or when
its stack storage leaves scope. Stack storage (`alloca`s, spilled params) lives to
function end. So the source is invalidated mid-function only by a **RefDec-to-zero of
the source's owning managed object**.

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

For an aggregate `OP_LOAD %v = load %src`, decide "safe to alias" from two facts:

1. **Source lifetime** — until when are `%src`'s bytes valid? Classify `%src`'s
   producer (`Args[0]`):
   - *Stable* (valid to function end): derives from a param, an `alloca`
     (`OP_ALLOC`), a global, or a raw `*T` pointer not backed by a managed temp.
   - *Ephemeral* (freed mid-function): derives from a managed **temporary** whose
     end-of-statement RefDec can free it (e.g. `foo().field` where `foo()` returns a
     fresh managed value).
2. **Use span** — every use of `%v`: is it a mid-function consumer (store/extract
   that runs before the source's owner is freed), or does `%v` flow to the **return
   value** (sret), i.e. a use at/after function-end cleanup?

**Safe to alias iff** every use of `%v` precedes the end of `%src`'s lifetime.
Conservatively: source is *stable* AND no use of `%v` is (or reaches) the function's
return value AND — for the strict first increment — no memory-freeing op sits
between the load and its use.

### 4.1 Proposed increments

- **Increment 1 (provably safe, minimal).** Skip materialization iff: `%v` has a
  single use; that use is an `OP_STORE` (struct-copy) in the **same block**; and
  **no** `OP_CALL`/`OP_C_CALL`/`OP_REFDEC` (or other memory-freeing op) lies between
  the load and the store. Provably cannot UAF (nothing between load and store frees
  the source). Captures `*dst = *src` and adjacent non-managed copies. **Likely
  misses** the hot managed case, whose store sits after RefInc/RefDec of *unrelated*
  objects.
- **Increment 2 (the hot managed case).** Replace "no freeing op between" with the
  precise "no RefDec of **`%src`'s owning object** between load and use, and `%v`
  does not flow to the return value." Requires source-owner classification and a
  use-scan (reuse the SROA use-scan patterns in `pkg/binate/ir/sroa.bn`). Captures
  `b = a` / `b.s = a`. Larger analysis, larger blast radius.

**Recommended path:** implement Increment 1, **measure** the redundant-copy
reduction on the cmd/bnc self-compile (static count of aggregate-load materializations
elided, and N-vs-L memory-op ratio), then decide whether Increment 2's added benefit
justifies its added UAF-analysis risk. (Matches the todo's "measure the traffic
reduction" note.)

## 5. Validation strategy

A false positive is memory corruption, so validation is heavier than the width fix:

1. **Full native conformance, all three arches** (`builder-comp_native_aa64`,
   `builder-comp_native_x64_darwin`, `builder-comp_native_arm32_baremetal`) — 0 new
   failures.
2. **LLVM-vs-native `-O0`/`-O2` differential** over the whole conformance corpus — 0
   mismatch (the elision must not change any observable result).
3. **The `return container[i]` regression** (and kin: returning a struct field of a
   managed local, returning an element of a managed slice) — must **still
   materialize**; add explicit conformance tests asserting the returned value is
   correct (not freed garbage). These are the UAF tripwires.
4. **Refcount-balance / leak tests** — the elision must not change any RefInc/RefDec
   (it only removes a copy); assert refcount returns to baseline for the managed
   copy cases (like `iife-capturing-no-leak`).
5. **Unit tests** on the `PlanFrame` predicate: for hand-built IR, assert a region is
   reserved for the unsafe shapes and elided for the safe shapes.
6. **Adversarial review** focused solely on: "construct an input where an aliased
   load's source is freed before a use."

## 6. Risks and open questions

- **Source-owner classification precision.** Mis-classifying an ephemeral source as
  stable → UAF. When in doubt, classify as ephemeral (materialize). Raw-pointer
  sources: a `*T` can point into a managed temp's backing; treat a raw pointer as
  stable only when it does not derive from a within-statement managed temp.
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
  reads) is also aliasable under the same lifetime rule; include or defer explicitly
  (don't silently only handle `OP_STORE`).

## 7. Alternative considered — IR-level peephole

Rewrite `OP_LOAD → OP_STORE` into a direct copy in the IR (removing the load).
**Rejected as the primary approach:** it changes the IR both backends and the
**interpreter** consume (dual-mode), widening the blast radius and requiring VM
changes, for a redundancy the LLVM backend already elides. The `PlanFrame`
frame-planning decision is native-local and reuses the existing alias path, so it is
the lower-risk lever. (Revisit only if a native-local rule proves insufficient.)
