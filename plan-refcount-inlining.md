# Plan: Inline RefInc / Fast-Path Inline RefDec

> **Status: DONE** (RefInc + RefDec). Eleven commits across IR, all
> three backends (LLVM / VM / native arm64), and the runtime.
> Final commit: `19502d4 pkg/ir: switch IR-gen to emit OP_REFDEC;
> with-dtor test coverage`. See claude-todo.md "Inline RefInc /
> fast-path inline RefDec (perf)" for the full commit list.
>
> The dead `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` ops, their backend
> dispatch arms, `BC_REFINC` / `BC_REFDEC` and their VM exec
> handlers, `emitRefcountCall`, and the `bn_rt__RefInc` /
> `bn_rt__RefDec` runtime symbols are all immediately removable in a
> follow-up cleanup commit.

## Motivation

`rt.RefInc` and `rt.RefDec` are the hottest code paths in any
non-trivial Binate program. The compiler emits a refcount op at
nearly every:

- Managed-pointer or managed-slice assignment (RefInc on the new
  value, RefDec on the old).
- Struct field copy where the field type contains managed
  references (RefInc each field).
- Scope exit / function return (RefDec each managed local).
- Function-argument pass-by-value of managed types (RefInc on
  entry, RefDec on cleanup).

Each call site currently lowers to a *function call*:

```llvm
call void @bn_rt__RefInc(i8* %p)
```

with the standard call overhead — branch+link, register save/spill,
parameter shuffle, return branch. The actual RefInc work is tiny:

```binate
func RefInc(ptr *uint8) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    h[0] = h[0] + 1
}
```

three loads/stores plus a compare. The call setup costs more than
the work itself. RefDec is similar in the fast path:

```binate
func RefDec(ptr *uint8, dtor *uint8) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    if h[0] <= 0 { BoundsFail(...) }
    h[0] = h[0] - 1
    if h[0] == 0 {
        // slow path: dtor + free
    }
}
```

with the slow path (refcount hit zero, run dtor + free) being rare
relative to the count of total RefDec invocations.

## Approach: hybrid (inline fast path, call out for slow path)

Inline the *fast paths* of RefInc and RefDec entirely; call out only
to a thin runtime helper when RefDec hits the slow path.

### RefInc — fully inline

Three IR ops at every call site (or a backend intrinsic that lowers
to the equivalent inline sequence):

```
if ptr == nil: skip
h = headerPtr(ptr)
h[0] = h[0] + 1
```

No call. The whole sequence is ~5 instructions in LLVM and ~5 in
arm64 (LDR + ADD + STR + nil-check branch).

### RefDec — fast path inline, slow path via call

```
if ptr == nil: skip
h = headerPtr(ptr)
new_count = h[0] - 1
h[0] = new_count
if new_count > 0: skip
// slow path: refcount reached zero
call rt.ZeroRefDestroy(ptr, dtor)
```

The slow-path helper:

```binate
func ZeroRefDestroy(ptr *uint8, dtor *uint8) {
    // refcount already at zero; ptr is non-nil.
    if dtor != nil { _call_dtor(dtor, ptr) }
    Free(ptr)
}
```

Slow path runs once per managed allocation (when its last reference
is released), not at every assignment. Inlining the dtor + free
sequence gains nothing because it's already rare.

### Slow-path helper name

`ZeroRefDestroy(ptr, dtor)` — describes what it does (refcount has
already hit zero; this destroys the object). Lives in `pkg/rt`
alongside `RefInc` / `RefDec`.

## Backend implementation

Two architectural choices:

### Option A — IR-level inlining

Replace `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` IR-gen with a sequence
of primitive ops (load / add / store / branch). Backends consume
the existing primitive ops directly; no backend changes needed.

Pros: portable across backends; one source of truth. Especially
attractive once we have multiple native backends (arm64, future
arm32 / x86-64), since none of them needs to know about refcount
ops at all.

Cons: IR-gen emits more ops per call site → bigger IR → slower
IR-gen and codegen passes. Each refcount site goes from 1 op to
~5 ops. **Critically, this is bad for the VM**: the VM dispatch
loop is the dominant cost, and ~5 primitive bytecode dispatches
per refcount site is much slower than a single fused dispatch.
(Still better than the current `BC_CALL` + body, but it leaves a
big VM win on the table.)

### Option B — Backend intrinsics (preferred)

Keep `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` as single ops. Each
backend lowers them inline:

- **LLVM**: emit the load+add+store+branch sequence directly in
  `emit_instr.bn`. ~5 LLVM instructions per call site instead of
  one `call`. Optimizer can hoist common nil-checks and the like.
- **VM**: introduce fused `BC_REFINC_INLINE` /
  `BC_REFDEC_INLINE_FAST` ops that the VM dispatch loop handles
  in one switch arm. **One dispatch per refcount site** instead
  of the ~5 that Option A would imply, which is the dominant
  perf win on the VM side.
- **Native arm64**: emit `LDR + ADD + STR + CBZ`-style sequence.
  Already saturating registers near refcount sites, so reusing
  X16/X17 (intra-call scratch) keeps register pressure manageable.

Pros: IR stays small; the VM gets a single-dispatch op (the
biggest perf delta in this whole project); native backends can
each use their native-optimal sequence.

Cons: each backend duplicates the inlining logic. Mitigated for
the native side by a **shared inline-lowering helper** (see
below).

**Recommendation: Option B**, with a shared helper for the native
backends.

### Shared inline-lowering helper for native backends

The "Option B con" — duplicated lowering across native backends —
is mostly avoidable. The inline sequence (nil check, header-offset
arithmetic, load, add/sub, optional zero-compare + branch, store)
is universal across architectures; only instruction selection
differs. A single helper in the shared backend layer can emit it
via each backend's primitive emit functions (load / store / add /
branch / call).

Requirements for the helper to be portable to future native
backends (arm32, x86-64, RISC-V, ...):

- The shared backend layer must expose target-agnostic primitive
  emit ops (load / store / add / sub / branch / call). Each
  backend's instruction selection turns these into machine
  instructions independently.
- The helper must be parameterized by **target word sizes**:
  header offset and refcount-field load/store size depend on
  pointer-size and int-size on the target. arm32 and 32-bit x86
  use 4-byte loads where arm64 / x86-64 use 8-byte. This is the
  same target parameterization the IR/backend guidelines already
  require for `types.SizeOf` / `FieldOffset`.
- Instruction-level idioms (e.g., x86's `addq $1, off(%rax)`
  memory-operand form) are an instruction-selection concern in
  each backend, not a helper concern. The helper emits the right
  semantic ops; LLVM folds them, a hand-rolled x86 backend would
  fold them in its own selector.

The VM does NOT use this helper — it lowers the IR ops directly to
its fused bytecode ops. That asymmetry is intentional: the VM's
win comes from fewer dispatches, not from a different inline
sequence.

#### Atomic refcounts (future, out of scope)

If we ever switch to atomic refcounts for threading, the inline
sequence diverges per arch — arm32 needs LDREX/STREX, arm64 has
LDXR/STXR or LSE atomics, x86 wants `lock xadd`. At that point
the shared helper would need per-arch hooks for the read-modify-
write primitive, or each backend would re-specialize. This is a
cross-cutting redesign regardless of the inlining strategy, and
the current plan is non-atomic, so it's noted only as a future
constraint.

## Migration

This change is invisible to user-Binate code. RefInc / RefDec call
sites are emitted by the compiler, not the user. The IR ops stay
the same; only the lowering changes.

The runtime side:

- `rt.RefInc` may stay as a `.bni` declaration (e.g., for
  reflection, debugging, or direct calls from C interop). Whether
  to delete its body is a follow-up decision — if no caller remains,
  it can go. (`pkg/vm/vm_extern.bn`'s `rt.RefInc` arm would also
  retire, since the VM bytecode never emits a call to it.)
- `rt.RefDec` likely stays for the same reasons, but most call
  sites disappear from the bytecode/native output.
- `ZeroRefDestroy` needs a `.bni` declaration and a body (in
  `pkg/rt`).

## Phasing

Suggested order (each phase independently testable):

1. **Add `ZeroRefDestroy`** in `pkg/rt`. Define the `.bni`
   signature. Body calls `_call_dtor` then `Free`. No call sites
   yet.
2. **Shared inline-lowering helper** for the native backends.
   Lives in the shared backend layer; takes target word-size
   parameters and a backend's primitive-emit interface. No
   callers yet.
3. **LLVM lowering** of `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` via
   the shared helper. Verify all conformance modes still pass.
4. **VM lowering** — add fused `BC_REFINC_INLINE` /
   `BC_REFDEC_INLINE_FAST` bytecode ops; lower the IR ops to
   them directly (NOT via the shared helper — the VM is dispatch-
   bound, so a single fused op is the win).
5. **Native arm64 lowering** via the shared helper.
   `emitRefcountCall` rewrites to inline. The compiled call to
   `bn_rt__RefInc` / `bn_rt__RefDec` goes away on the native
   side.
6. **Drop unused runtime symbols** if no caller remains. Tighten
   `vm_extern.bn`.

Each phase should run the full conformance suite and check perf
numbers (compile time + run time) on a benchmark.

## Open questions

- **Multi-word values (managed slices, struct types).** Current
  `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` operate on the underlying
  managed pointer. Multi-word values get a separate
  `emitManagedSliceRefInc` helper that extracts the refptr. The
  inlining plan above assumes the IR-gen has already extracted the
  refptr — confirm before implementation.
- **Dtor parameter on RefDec.** Current `OP_REFCOUNT_DEC` takes the
  dtor as a second argument. The fast-path-inline version still
  needs to pass the dtor through to the slow-path helper when the
  refcount hits zero. Make sure the dtor is in a usable form in a
  register (or addressable spill slot) at the call site.
- **Stack-frame impact on arm64.** Inlining adds a few instructions
  but no extra stack usage (refcount lives in the header; no spill
  needed). Verify by inspecting frame size for a representative
  test.
- **Effect on debug info.** Each refcount site goes from one `call`
  with a `!dbg` to several primitive ops. Make sure the `!dbg`
  annotation lands somewhere sensible.
- **Should the slow-path helper itself be inlinable?** Probably
  not — slow-path is rare, helper is small but has a function call
  to dtor anyway. Leave it as a normal extern.

## Cross-references

- `claude-todo.md` § "Inline RefInc / fast-path inline RefDec
  (perf)" — the entry that points to this plan.
- `pkg/rt/rt.bn` — current RefInc / RefDec implementations.
- `pkg/codegen/emit_instr.bn` — current `OP_REFCOUNT_INC` /
  `OP_REFCOUNT_DEC` lowerings (call-out form).
- `pkg/vm/lower_instr.bn` + `pkg/vm/vm_exec.bn` — VM side.
- `pkg/native/arm64/arm64_ops.bn::emitRefcountCall` — native side.
