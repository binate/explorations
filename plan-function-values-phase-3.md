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

**Decision: always-shim.** `vtable.call` always has the uniform
shape `<ret>(i8* data, <args>)*`, regardless of the function
value's nature:

- **Compiled non-capturing**: `vtable.call` = per-function shim
  that ignores `data` and tail-calls the real function. `data`
  = nil.
- **VM-side**: `vtable.call` = generic trampoline (per return
  shape — see Slice 3.2). `data` = closure record pointer.
- **Compiled capturing (Phase 2)**: `vtable.call` = per-closure
  shim that uses `data` as the closure struct.

Caller is uniform across all cases: extract `data`, extract
`vtable.call`, bitcast to `<ret>(i8* data, <args>)*`, call with
`(data, args)`.

**Departure from parent plan**: the parent plan defaulted to
"check-data-nil" — caller branches on `data == nil`, taking a
direct path for non-capturing and a shim path for capturing /
cross-mode. The cost wasn't appreciated when that default was
written: at the IR-gen level, check-data-nil requires multi-
block dispatch (cond + 2 branches + phi-merge), substantial
changes across all three backends (LLVM, VM, native arm64).
Always-shim collapses every call site to one straight-line
call sequence in IR, with the per-function shim taking the
indirect hop instead.

**TODO — runtime cost investigation**: the always-shim choice
adds one indirect-call hop on the non-capturing compiled path
(vs the current direct call through `vtable.call`). On modern
CPUs with good branch prediction this should be near-free;
LLVM's tail-call optimizer should fold many shims into direct
calls in `-O2` builds. Worth measuring once we have any code
that calls function values in a hot loop:
- micro-benchmark a tight loop calling a non-capturing
  function value and compare to a direct call (and to a Phase
  1 vtable-mediated call) at `-O0` and `-O2`.
- if the shim hop turns out to matter, revisit check-data-nil
  with eyes open about the IR-gen cost; the call sites can
  always be specialized later.

**Soundness for cross-mode**: each producer of a function value
is responsible for making `vtable.call`'s actual function valid
for the uniform `(data, args)` signature given whatever `data`
they set. Per-function shims (compiled), generic trampolines
(VM-side), and per-closure shims (Phase 2) all conform.

## Slicing

Sequential. Each slice is independently shippable and conformance-
green at every step.

### Slice 3.1 — Switch compiled call sites to always-shim convention — LANDED

What lands:

- `pkg/codegen/emit_funcvals.bn`: emit per-function shims for
  each `OP_FUNC_VALUE`-referenced function:
  ```
  define <ret> @__shim.<mangled>(i8* %data, <args>) {
    %r = tail call <ret> @<mangled>(<args>)
    ret <ret> %r
  }
  ```
  Mark them `linkonce_odr` so cross-TU duplicates dedupe (or
  `weak_odr` matching the existing vtable globals — pick the
  one consistent with what the existing `__vt.<mangled>`
  global uses).
  Update each function's `__vt.<mangled>` to point its `call`
  slot at `__shim.<mangled>` instead of the bare function.

- `pkg/codegen/emit_call.bn`, compiled-side `OP_CALL_FUNC_VALUE`:
  bitcast `vtable.call` to `<ret>(i8*, <args>)*`, extract `data`
  (field 1 of the function value), and pass `data` as the first
  argument. This is the only call-site code change.

- `pkg/native/arm64/arm64_ops.bn`, `emitCallFuncValue`: same
  signature change. The AAPCS dispatch now passes `data` in X0
  and shifts user args one register over.

- VM backend (`pkg/vm/vm_exec.bn`, `BC_CALL_FUNC_VALUE`):
  similarly, the dispatch now needs to pass `data` (= the
  function value's `data` slot) as the first arg. The Phase 1
  short-circuit (read `closure_rec[0]` directly out of `data`
  and call `execFunc`) stays — it's a same-mode optimization
  that bypasses the shim entirely; it doesn't go through
  `vtable.call`.

Net behavior: externally identical. The shim is a tail-call
that the optimizer folds in `-O2`; at `-O0` it's one extra
indirect call.

Conformance: 338–342 + 344 still green.

### Slice 3.1.5 — Common kind-tag at the start of `data`

A small forward-looking refactor that lands BEFORE the trampoline
work in 3.2. When `data != null`, it points at a record whose
first word is a `kind` discriminator. This lets every dispatcher
(VM bytecode, generic trampoline, future Phase 2 closure handling)
distinguish what's actually behind the pointer without fragile
heuristics like null-vs-non-null + "we just know it must be a
VM closure record."

Constants (defined in `pkg/rt`):

```
const (
    DATA_KIND_VM_CLOSURE_REC int = 1   // VM-side: vm_func_idx + sig info
    DATA_KIND_COMPILED_CLOSURE int = 2 // Phase 2: per-closure captured struct
    // future kinds slot in here
)
```

VM closure record layout becomes:

```
VMClosureRec (heap):
    { kind, vm_func_idx, captured_ctx_or_nil }
    // Slice 3.2 will add: num_args, sig info for the trampoline.
```

What lands:

- `pkg/rt`: the kind constants.
- `pkg/vm/vm_exec_helpers.bn` (`BC_FUNC_VALUE`): write
  `kind = DATA_KIND_VM_CLOSURE_REC` at offset 0 of the closure
  record. Existing fields shift by one word.
- `pkg/vm/vm_exec.bn` (`BC_CALL_FUNC_VALUE`): the existing Phase 1
  short-circuit reads `closure_rec[0]` as `vm_func_idx`; update
  to read it from the new offset (post-kind). Add a kind check
  with a clear error for unrecognized kinds — sets up the
  multi-kind dispatch shape Slice 3.3 will fill in.

Net behavior: identical to Phase 1 for VM-internal dispatch
(same fast path, just with a small offset shift and a kind
check). No cross-mode work yet.

Conformance: 338–342 + 344 still green.

### Slice 3.2 — Generic ABI-aware VM-side trampoline (compiled→VM)

Compiled callers (already shim-convention from 3.1) bitcast
`vtable.call` to `<ret>(i8* data, <args>)*` and call with typed
args via the C ABI. For VM-side function values, vtable.call
points at a hand-written assembly trampoline that decodes the
incoming arg-passing registers as a uniform register bank, uses
sig info from `data` as the schema, packs the args into the
VM's `argv` int[] format, and calls `execFunc`. No JIT — the
trampoline is one (or a small per-return-shape set) of native
functions in cmd/bni's compiled body.

What lands:

- Extended `VMClosureRec`:
  ```
  { kind, vm_func_idx, num_args, sig_info_words... }
  ```
  `num_args` and (later) per-arg type/width info drive the
  trampoline's register-bank decoding.

- Per-return-shape generic trampolines (~3 total):
  `__bn_vmtramp_void`, `__bn_vmtramp_scalar`,
  `__bn_vmtramp_aggregate(retbuf)`. Each is hand-written
  assembly (or a careful Binate function with inline asm where
  needed):
  1. At entry, save X0..X7 + V0..V7 to a stack buffer (X0 is
     `data`, X1+ are user args per the always-shim convention).
  2. Read `num_args` and arg-type info from `data`.
  3. Walk the saved register bank using AAPCS rules + sig info,
     copy each user arg into the VM's argv int[] buffer.
  4. Call `execFunc(rt.CurrentVM(), &vm.Funcs[vm_func_idx-1], argv)`.
  5. For non-void: copy the return value from execFunc's result
     into the C-ABI return slot.

  Bootstrap-subset reality check: with no floats and only
  scalar args ≤ 7, the trampoline's decoding is simple
  (X1..X7 → argv[0..n-1]). Floats / aggregates / >7 args are
  bounded extensions to add when broader signatures actually
  reach the trampoline.

- `vtable.call` for VM-side function values points at the
  appropriate variant for the function's return shape. The
  variant is determined when constructing the function value
  (the function's signature is known from the ir.Func).

- Single global VM handle: `rt.CurrentVM()` set by cmd/bni at
  program start. No TLS.

Compiled callers don't change — they already pass `(data, args)`
via the shim convention. The trampoline accepts that convention
on entry; its body is what's new.

This unblocks compiled callers holding a VM-side function value.

Conformance: a new test exercising compiled code that calls a
VM-side function value end-to-end.

### Slice 3.3 — Bytecode → compiled function-value dispatch

`BC_CALL_FUNC_VALUE` becomes a kind-aware dispatcher:

- `data == null` → compiled non-capturing function value (its
  caller-side construction sets data to nil). Call `vtable.call`
  as a native indirect call, passing args.
- `data != null && data.kind == DATA_KIND_VM_CLOSURE_REC` →
  VM-side. Short-circuit via `vm_func_idx` + `execFunc`. **This
  is the unchanged Phase 1 fast path.**
- `data != null && data.kind == DATA_KIND_COMPILED_CLOSURE` →
  Phase 2 territory; clear error for now.
- Unknown kind → diagnostic error.

The data==null path is the cross-mode bytecode→native case.
Bytecode already has args in argv int[] format. Calling the
native vtable.call with those needs ABI-aware unpacking — same
problem the trampoline solves in the other direction. Two sub-
options:

  (a) **A bytecode-side counterpart trampoline** (also
      hand-written assembly): takes (native_fn_ptr, argv,
      num_args, retbuf_or_nil), uses argv as the schema to
      populate X0..X7 / V0..V7 / stack per AAPCS, calls the
      native fn, copies the return back. Works for any
      compiled-mode signature.

  (b) **Per-shape inline dispatch** in BC_CALL_FUNC_VALUE for
      the few shapes the bootstrap subset actually exercises.

(a) is more general and matches the compiled→VM trampoline's
philosophy. Start with (a) since the cross-mode hack we want
to retire (Slice 3.4) covers the same shape.

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
  ABI. Both the compiled→VM trampoline (3.2) and the
  VM→compiled trampoline (3.3) decode/encode args via this
  layout using sig info from the data record.

- **Phase 2 interaction**: capturing closures get
  `kind = DATA_KIND_COMPILED_CLOSURE` and a per-closure shim
  in vtable.call (different from the VM trampoline). The
  kind-tag scheme that lands in 3.1.5 makes this easy to add
  without re-touching every dispatcher.

## Cross-references

- `plan-function-values.md` — parent plan (Phase 1 sections lay
  the groundwork; Phase 3 sections describe what's done here).
- `claude-todo.md` — boot-comp-int-int hand-off entry tracks
  the hack and the downstream `vm: stack overflow`.
