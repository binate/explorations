# Plan: Function Values — Phase 3 (Cross-Mode Trampolines)

> **Status: DRAFT** — sub-plan of `plan-function-values.md`. Phase
> 1 is done; this fills in the cross-mode `vtable.call` slot so
> function values dispatch correctly across compiled/VM mode
> boundaries. Independently retires (or generalizes) the
> `pkg/vm/vm_exec.bn` cross-mode dispatch hack landed at
> `5f4333f`.

## Problem statement

After Phase 1, function values work within a single mode:

- **Compiled mode**: `vtable.call` is the real function pointer.
  Compiled callers extract it, bitcast to the function's bare
  signature `<ret>(<args>)*`, and call directly.
- **VM mode**: `BC_CALL_FUNC_VALUE` short-circuits the trampoline
  by reading `closure_rec[0] = vm_func_idx` directly from the
  function value's `data` slot, dispatching via `execFunc`.

Cross-mode dispatch breaks:

1. **Compiled → VM**: a VM-side function value's `vtable.call` is
   `null` (Phase 1 placeholder — `pkg/vm/vm_exec_helpers.bn`
   line 168). A compiled caller calling through `vtable.call`
   would null-deref.

2. **VM → Compiled**: `BC_CALL_FUNC_VALUE` always reads
   `closure_rec[0]` as a VM index. A function value pointing at
   compiled code has no closure record (or it's not a VM index),
   so the dispatch mis-resolves.

3. **Indirect-call cross-mode (orthogonal)**: bytecode
   `BC_CALL_INDIRECT` through a NATIVE function pointer (e.g.
   `h[1]` on a header allocated by native `rt.Alloc`, RefDec'd
   from bytecode). Hacked at `5f4333f` for the single-arg
   `func(*uint8)` shape; remaining shapes still error.

## Design — call convention

Stick with the parent plan's recommended **check-data-nil**
default rather than always-shim. The vtable's `call` slot's
*actual* signature depends on the function value's nature:

- **Compiled non-capturing**: `vtable.call` = bare function
  pointer, signature `<ret>(<args>)*`. `data` = nil.
- **VM-side**: `vtable.call` = per-signature trampoline,
  signature `<ret>(i8* data, <args>)*`. `data` = closure record
  pointer.
- **Compiled capturing (Phase 2)**: `vtable.call` = per-closure
  shim, signature `<ret>(i8* data, <args>)*`. `data` = closure
  struct pointer.

Caller branches on `data == nil`:

- nil → bitcast `vtable.call` to bare signature, call with args.
- non-nil → bitcast `vtable.call` to shim signature, call with
  `(data, args)`.

Soundness: each producer of a function value is responsible for
making `vtable.call`'s actual function valid for the bitcast its
caller will use given the `data` value. The branch is
deterministic per function value, so only one bitcast is ever
exercised on a given instance — no UB.

**Why check-data-nil over always-shim:**

- Matches the parent plan's documented default and reasoning
  (`plan-function-values.md` "Per-shape `call` shim").
- Compiled non-capturing dispatch (the common case in self-hosted
  code today) keeps the current direct-call IR — no perf or
  bitcast change for code that's already shipping.
- The shim cost only shows up where it's actually meaningful
  (capturing closures, VM-side trampolines).

## Slicing

Sequential. Each slice is independently shippable and conformance-
green at every step.

### Slice 3.1 — Caller branches on data == nil (compiled mode)

What lands:

- `pkg/codegen/emit_call.bn`, compiled-side `OP_CALL_FUNC_VALUE`:
  emit the data-nil branch. Two call paths: bare (current)
  and shim (new but not yet exercised because no producer sets
  `data` to non-nil yet).
- `pkg/native/arm64/arm64_ops.bn`, `emitCallFuncValue`: same
  branch structure for the AArch64 backend.
- VM backend (`pkg/vm/vm_exec.bn`, `BC_CALL_FUNC_VALUE`): same
  branch structure. The Phase 1 short-circuit (read
  `closure_rec[0]` directly out of `data` and call `execFunc`)
  stays as the fast path for the non-nil branch when the
  closure-rec shape is recognized.

Net behavior: identical externally — `data` is always nil with
the Phase 1 producers, so the bare branch is taken every time.
The shim branch is dead but emit-correct and ready for 3.2/3.3
to start exercising.

Conformance: 338–342 + 344 still green.

### Slice 3.2 — Generic VM-side trampoline (compiled→VM)

Instead of a per-signature `__vmtramp_<sig>` (which would feel
unbounded as the static signature set grows), use ONE generic
trampoline (or a small fixed set keyed on return shape — see
below) plus signature info carried in `data`.

What lands:

- Extended `data` shape for VM-side function values. The closure
  record gains a fixed-shape signature descriptor:

  ```
  VMClosureRec (heap):
    {
      vm_func_idx     int,    // 1-based index into vm.Funcs
      captured_ctx    *uint8, // nil for non-capturing (Phase 1)
      // signature info (NEW in Phase 3):
      num_args        int,
      // result-shape and arg-shape live elsewhere or are
      // implicit in the trampoline variant chosen — see below.
    }
  ```

  Compiled callers don't read the signature info (their static
  type already tells them the layout). Only the trampoline
  reads it.

- A small fixed set of generic trampolines, keyed on **return
  shape**:

  - `__bn_vmtramp_void(i8* data, i64* argv)`
  - `__bn_vmtramp_scalar(i8* data, i64* argv) → i64`
  - `__bn_vmtramp_aggregate(i8* data, i64* argv, i8* retbuf)`

  All variants read `data → vm_func_idx`, dispatch into the VM
  via `execFunc`, and (for scalar/aggregate) marshal the return.
  The argv buffer is the VM's standard int[]-as-register-bank
  layout — bit-cast for floats, alloca-pointer for aggregates,
  matching the existing VM call ABI.

- `vtable.call` for VM-side function values points at the
  appropriate variant for the function's return shape. The
  variant is determined statically when constructing the
  function value (the function's signature is known from the
  ir.Func).

- Compiled-side caller (slice 3.1's shim branch) is updated to
  pack args into a stack-allocated `i64[]` and call the
  trampoline with `(data, argv)`. The packing is small and
  trivially eliminated for the data-nil branch (where we skip
  the trampoline entirely).

- The trampolines rely on a single global VM handle:
  `rt.CurrentVM()` (name TBD). `cmd/bni` sets it once at
  program start. Multi-VM scenarios are an embedder concern;
  the global is fine for the only consumer that exists today
  (cmd/bni). No TLS.

This gives:

- Bounded (≤ 3) trampolines per binary, regardless of program
  signature count.
- Compiled callers see a uniform call sequence in the shim
  branch.
- Cross-mode dispatch in the bytecode→compiled direction
  (Slice 3.3) can use the same packing convention from the
  bytecode side.

Conformance: a new test exercising compiled code that calls a
VM-side function value end-to-end.

### Slice 3.3 — Bytecode → compiled function-value dispatch

What lands:

- `BC_CALL_FUNC_VALUE` in `vm_exec.bn`: when the `data` slot is
  nil (compiled-side non-capturing), invoke `vtable.call` as a
  bare native function pointer with args. The current
  short-circuit via `closure_rec[0]` stays as the data-non-nil
  fast path for in-VM dispatch.
- Bytecode-side generic native call: same trick as the compiled
  side — pack args into argv (already the VM's register-bank
  layout), then call a generic native dispatcher that takes
  `(native_fn_ptr, argv, retbuf_or_nil)`. The dispatcher unpacks
  argv into typed args according to the function value's static
  signature and invokes the native fn. **Key observation**:
  unlike the compiled→VM direction, the bytecode side already
  has args in argv-shaped form (it's the VM's call ABI), so
  packing is free.
- The dispatcher itself is a small set of native helpers (one
  per arg-shape pattern actually used by the program), or — if
  the static signature set turns out small enough — a switch on
  return-and-arity in the natively-compiled VM. Bridge to libffi-
  style for arbitrary signatures is explicitly out of scope.

Conformance: a function value constructed in compiled mode and
called from bytecode.

### Slice 3.4 — Retire / fold the cross-mode hack

The `5f4333f` hack handles single-arg `func(*uint8)` indirect
calls through native pointers, only for `_call_free_fn` /
`_call_dtor`. After 3.3, the same machinery (generic native
indirect-call dispatch) covers it. Replace the hack's special
arm with a call into the generic dispatch.

If 3.3's generic dispatch handles all signatures used by
`_call_free_fn` / `_call_dtor`, the hack is fully retired. If
not, the hack stays for the un-handled signatures with an
explicit TODO and a clear extension story.

Conformance: boot-comp-int-int 001_hello stays at the same
post-hack progress level (cross-mode dispatch still works).

## Open questions

- **VM handle access from a trampoline**: single global. No
  TLS — Binate has no plans for thread-local. Single global
  is sufficient for cmd/bni today and any embedder running
  one VM at a time. Multi-VM is an embedder concern and
  doesn't change the design: each program builds with its
  own trampolines and registers whichever VM is current via
  `rt.SetCurrentVM`.

- **Argv packing convention**: standardize on the VM's
  existing int[]-as-register-bank layout. Bit-cast for
  scalars, alloca-pointer for aggregates, same as the VM call
  ABI. The compiled→VM packing in 3.2 and the VM→compiled
  unpacking in 3.3 use the same convention, which means the
  generic trampolines / dispatchers don't have to know the
  signature beyond return-shape and arg-count.

- **Phase 2 interaction**: capturing closures need `data` as
  the closure struct, not the VMClosureRec. The shim has to
  be per-closure (different for each capture set). Check-
  data-nil still works; the VM-trampoline path doesn't
  change. Phase 2 just adds the closure-shim variants on top.

## Cross-references

- `plan-function-values.md` — parent plan (Phase 1 sections lay
  the groundwork; Phase 3 sections describe what's done here).
- `claude-todo.md` — boot-comp-int-int hand-off entry tracks
  the hack and the downstream `vm: stack overflow`.
