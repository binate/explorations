# Plan: Inline RefInc / Fast-Path Inline RefDec

> **Status: DRAFT** — not started. Substantial perf project that
> touches IR, all three backends (LLVM / VM / native arm64), and the
> runtime.

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
call rt.<slow-path-helper>(ptr, dtor)
```

The slow-path helper:

```binate
func <slow-path-helper>(ptr *uint8, dtor *uint8) {
    // refcount already at zero; ptr is non-nil.
    if dtor != nil { _call_dtor(dtor, ptr) }
    Free(ptr)
}
```

Slow path runs once per managed allocation (when its last reference
is released), not at every assignment. Inlining the dtor + free
sequence gains nothing because it's already rare.

### Slow-path helper name — open

Bikeshed candidates:
- `OnZeroRef(ptr, dtor)` — describes when it fires.
- `ZeroRefDestroy(ptr, dtor)` — describes what it does.
- `RefDecSlow(ptr, dtor)` — describes the call-graph relationship.
- `Destroy(ptr, dtor)` — shortest, accurate.

Implementer's choice. The compiler-internal magic-name pattern (à la
`_call_dtor`) is also an option if we want to flag it as not-for-
direct-user-code.

## Backend implementation

Two architectural choices:

### Option A — IR-level inlining

Replace `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` IR-gen with a sequence
of primitive ops (load / add / store / branch). Backends consume
the existing primitive ops directly; no backend changes needed.

Pros: portable across backends; one source of truth.

Cons: IR-gen emits more ops per call site → bigger IR → slower
IR-gen and codegen passes. Each refcount site goes from 1 op to
~5 ops.

### Option B — Backend intrinsics (preferred)

Keep `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` as single ops. Each
backend lowers them inline:

- **LLVM**: emit the load+add+store+branch sequence directly in
  `emit_instr.bn`. ~5 LLVM instructions per call site instead of
  one `call`. Optimizer can hoist common nil-checks and the like.
- **VM**: emit a small bytecode sequence (BC_LOAD64 + BC_ADD +
  BC_STORE64 + BC_BRANCH) instead of BC_CALL. Or: introduce
  fused `BC_REFINC_INLINE` / `BC_REFDEC_INLINE_FAST` ops that the
  VM dispatch loop handles in one switch arm — fewer dispatch
  hops.
- **Native arm64**: emit `LDR + ADD + STR + CBZ`-style sequence.
  Already saturating registers near refcount sites, so reusing
  X16/X17 (intra-call scratch) keeps register pressure manageable.

Pros: IR stays small; each backend gets its native-best sequence;
optimizer sees the full inline form.

Cons: each backend duplicates the inlining logic. Three places to
maintain instead of one.

**Recommendation: Option B**. The duplication is minimal (each
backend's lowering is ~10-15 lines) and the perf wins are bigger
because each backend can use its native-optimal sequence.

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
- The new slow-path helper (whatever it's named) needs a `.bni`
  declaration and a body (in `pkg/rt`).

## Phasing

Suggested order (each phase independently testable):

1. **Add the slow-path helper** in `pkg/rt`. Define the `.bni`
   signature. Body calls `_call_dtor` then `Free`. No call sites
   yet.
2. **LLVM lowering** of `OP_REFCOUNT_INC` and `OP_REFCOUNT_DEC` to
   inline sequences. Verify all conformance modes still pass.
3. **VM lowering** — same change for `BC_REFINC` / `BC_REFDEC`
   (either inline the existing ops or add fused variants).
4. **Native arm64 lowering** — `emitRefcountCall` rewrites to
   inline. The compiled call to `bn_rt__RefInc` / `bn_rt__RefDec`
   goes away on the native side.
5. **Drop unused runtime symbols** if no caller remains. Tighten
   `vm_extern.bn`. Update naming whitelist if the slow-path helper
   uses a leading-underscore name.

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
