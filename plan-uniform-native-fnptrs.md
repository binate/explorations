## Plan: Uniform native function pointers in the bytecode VM

### Status

(2026-05-15.)  **Phases 1–3 landed on main:**
 * Phase 1 (`9561a3b`, originally `66d07c3` in the worktree):
   register `vm.TrampolineScalar` / `Aggregate` as standard
   externs.
 * Phase 2 (folded into `29d5298`): BC_FUNC_VALUE Path B reads
   the trampoline native addr from `vm.Externs`; also a
   self-reference fix when the callee IS one of the trampolines.
   This unblocked the original crash.
 * Phase 3 (`c557870`): ExternBinding gains `RawFnAddr`;
   `_raw_func_addr(F)` in bytecode resolves via the Externs
   registry instead of returning a 1-based vm idx.  `pkg/vm`
   tests + conformance green in `boot-comp`, `boot-comp-int`,
   `boot-comp-int-int`.

**Phase 4 in flight.**  The audit's BC_CALL_INDIRECT magnitude
check still stands today.  Real elimination requires unifying the
`_raw_func_addr` machinery with the function-value handle shape so
that bytecode-only function references (REPL-defined, test-helper-
in-bytecode-mode) get dispatched through the universal trampolines.
See "Phase 4 revised" below.

Original Phase 2 history (a prior attempt was reverted on
2026-05-14 before the landed version above) is kept under
"Phase 2 misadventure" for context.

Triggered by the `pkg/vm:TestExecRefIncRefDecInline` SEGV root-
caused in `claude-todo.md` (commit `788ec56`): the bytecode-mode
Path B in `BC_FUNC_VALUE` writes a 1-based VM index into
`vtable.call`, and the same numeric slot points at a different
function in a deeper-nested VM, so cross-VM dispatch lands on the
wrong target.

This plan covers the architectural cleanup the user wants: stop
distinguishing "real function pointer" vs "1-based VM index" at
the call-slot / function-pointer level — make every such value a
native function pointer.  The current hacky check
`if calleeFuncIdx < len(vm.Funcs) { vm-dispatch } else { native }`
in `BC_CALL_INDIRECT` goes away; everything is native.

### Phase 2 misadventure (the libc-pattern doesn't quite extend)

The proposed Phase 2 was: "read `vm.Externs["vm.TrampolineScalar"].VtableAddr`
and store its `[1]` (call slot) into the per-callee vtable."  Built
it; the test still failed, with a *different* error:
`TrampolineScalar: data is not a VM closure record`.  The closure
record at the address TrampolineScalar received had `rec[0] = 0`,
not the expected `rt.DATA_KIND_VM_CLOSURE_REC = 1`.

Root cause: the binding's `VtableAddr.call` slot is the **per-
function shim** address (`__shim.vm.TrampolineScalar`), **not** the
trampoline function's own address.  The shim convention emits

    define <ret> @__shim.X(i8* %data, <user-params>) {
        %r = tail call <ret> @X(<user-params>)
        ret <ret> %r
    }

i.e. the shim receives `%data` as a hidden first arg and **drops
it** before calling the underlying X.  This is correct for ordinary
captured closures (where `data` is private bookkeeping the function
shouldn't see), but it breaks for the trampolines themselves —
their entire purpose is to inspect `data` as the closure record.
Routing through the shim makes TrampolineScalar receive the user's
first real arg in the slot where it expected the closure record.

So the actually-needed value in the registry is the trampoline's
**function** address (`&bn_vm__TrampolineScalar`), not the shim
address.  Pre-Phase-2's `_raw_func_addr(TrampolineScalar)` resolved
to that function address directly in native code — which was
correct in 2-level scenarios.  Phase 2 inadvertently swapped to the
shim, breaking 2-level and (still) 3-level.

The libc/bootstrap pattern works for them because the shim *is* the
right call slot — those functions don't introspect `data`.  The
trampolines are the only signature that explicitly consumes `data`
as a user-facing parameter.

### Revised mechanism options

The problem reduces to: **how do we propagate `&TrampolineScalar`
(the function symbol, not its shim) to bytecode-mode VMs at every
depth?**  Three concrete shapes:

**Option α — Hand-built registry binding with `call = &function` (not
&shim).**  At native level, `registerVmTrampolines` skips the
`var ts *func(...) = TrampolineScalar` construction (which uses the
shim) and instead builds the binding's vtable manually with `vt[1]
= bit_cast(int, _raw_func_addr(TrampolineScalar))`.  In native code
this resolves to the function symbol; in bytecode code it doesn't.
So this works for the outermost level but **doesn't help** when an
inner cmd/bni's bytecode calls registerVmTrampolines for a nested
vm — same `_raw_func_addr` bytecode-mode bug.

**Option β — Process-global captured at native startup.**  Add
C-level globals (`bn_rt__nativeTrampolineScalarAddr` etc.) in
pkg/rt's C runtime, plus C-only setters / getters declared in
pkg/rt.bni.  cmd/bni's `main` calls the setter with
`_raw_func_addr(vm.TrampolineScalar)` at native startup; bytecode-
mode code calls the getter (a libc-shape extern with a native
shim slot) which returns the global.  Path B reads via the
getter.  Works at any nesting depth because the C global lives in
the process address space, shared by every VM in that process.

**Option γ — Generalize `BC_FUNC_ADDR` to look up natives via the
extern registry.**  Extend `BC_FUNC_ADDR`'s handler to first try
`vm.LookupExtern(name)` and, on hit, return the binding's `call`
slot **but stored differently** — the binding would need to carry
the **function** address separately from the shim address.  This
means adding a field to `ExternBinding` (or a parallel "raw fn
addr" registry).  Cleaner for the long-run plan of dropping all
idx-based fn pointers, but bigger surface change.

### Recommendation (revised — option β rejected, no new C)

User feedback (2026-05-14): no new C — Binate stays self-hosted.
Option β is off the table.  We're going with **α + a slice of γ**
together:

1. **α — hand-built registry bindings** for the trampolines whose
   `vtable.call` is the trampoline's **function** address (not its
   per-function shim).  At native level, `_raw_func_addr(TrampolineScalar)`
   resolves directly to the function symbol.  At bytecode level
   (e.g., when an inner cmd/bni runs `registerVmTrampolines` for
   its own vmInst), `_raw_func_addr` lowers to `BC_FUNC_ADDR` —
   which today returns a 1-based vm idx, which is wrong.  So we
   also need:

2. **γ-narrow — `BC_FUNC_ADDR` checks `vm.Externs` first.**  If the
   name is in the executing vm's Externs (the host pre-populated
   it with the function address via α), return the binding's call
   slot.  Otherwise fall back to the existing
   `LookupFunc + idx+1` behavior (preserves the dtor / intra-vm
   idx use cases that aren't in Externs and aren't broken).

That combination resolves the bug without splitting `_raw_func_addr`
into a separate "native" variant and without rebroadening to the
whole BC_CALL_INDIRECT cleanup — those stay as future phases.

The `tVtable[1]`-lookup in Path B (the change I attempted as
"Phase 2") is no longer needed: Path B can keep using
`_raw_func_addr(TrampolineScalar)` exactly as before; the operator
itself now returns the right value at every level via the modified
BC_FUNC_ADDR.

### Goal

After this lands:
- Every "function pointer"-shaped value at runtime (in vtables,
  in register slots reachable by `BC_FUNC_ADDR` /
  `BC_CALL_INDIRECT`, in extern bindings) is a real native
  function pointer.
- The universal trampolines (`TrampolineScalar` /
  `TrampolineAggregate`) stay — they're how function values
  pointing at bytecode-only functions dispatch when reached from
  native code.  They're plain Binate source compiled by bnc into
  every binary that uses pkg/vm; their addresses propagate by
  the same registration machinery that already handles libc.*
  and bootstrap.*.
- 1-based VM indices remain only where they're intrinsically
  per-VM and never escape across VM boundaries: closure record
  `vm_func_idx` (paired with a vm handle in the same record), VM-
  internal `IfaceVtable.Methods`, the BC_REFDEC_INLINE_FAST dtor
  slot.  These can be revisited later — they're not broken in the
  3-level scenario because they never leak across vms.

### Design (the libc-pattern, extended)

The libc-pattern that already works for `libc.*` / `bootstrap.*`:

1. The function is implemented natively (libc by libsystem;
   trampolines by pkg/vm compiled by bnc).
2. The binary's `main` calls `registerLibcExterns(vmInst)` (or
   equivalent), which constructs a function value `var fv *func(…)
   = libc.X` at native level — the compiler emits a static vtable
   with `call = &__shim.libc.X` (a real native pointer).
3. `RegisterExtern` copies that 16-byte fv into `vm.Externs`.
4. At dispatch time (`dispatchExternBinding`), the binding's call
   slot is the per-function shim; `rt._call_shim_scalar` lowers
   to a real indirect call.

We extend this pattern to cover **TrampolineScalar /
TrampolineAggregate**, and we change BC_FUNC_VALUE Path B to use
the registry instead of constructing a fresh vtable.

#### What changes

1. **`registerStandardExterns` (and equivalent for cmd/bnc,
   cmd/bnlint, …) registers `vm.TrampolineScalar` and
   `vm.TrampolineAggregate`** alongside libc.* / rt.* / bootstrap.*.
   The function value for these is built natively from
   `var fv *func(…) = vm.TrampolineScalar`; bnc emits
   `__vt.vm.TrampolineScalar` statically with a real native call
   slot.  This adds two more entries to every vm's `Externs`
   table — cheap.

2. **`BC_FUNC_VALUE` Path B (`pkg/vm/vm_exec_funcref.bn:99-107`)
   disappears.  Path A becomes the only path.**  The lazy-built
   heap vtable goes away.  Instead, every BC_FUNC_VALUE construction
   reads the appropriate binding out of `vm.Externs`:
    - For "function with a native shim" (rt.*, libc.*, bootstrap.*,
      user-defined functions in the natively-compiled tree): the
      binding's call slot is `&__shim.<fn>`.  No closure record
      needed — the data slot is null.  Same as Path A today.
    - For "bytecode-only function" (built via IR construction in
      the same vm — like `test.main` in our crashing test): the
      binding's call slot is `&vm.TrampolineScalar` (or
      Aggregate), and the data slot is a fresh
      `VMClosureRec{ kind, vm, vm_func_idx, captured }`.  The
      lazy allocation of the closure record stays; only the
      vtable disappears.  The trampoline-address comes from a
      separate registry lookup (`vm.Externs["vm.TrampolineScalar"]`)
      whose call slot is the host's native trampoline address.

3. **`BC_FUNC_ADDR` returns a native pointer** (a per-function
   shim address, or — for bytecode-only functions — a small
   runtime-allocated thunk that dispatches via TrampolineScalar,
   see "open question" below).  The current `regs[Dst] = idx + 1`
   line becomes `regs[Dst] = <native ptr>`.

4. **`BC_CALL_INDIRECT` simplifies.**  The
   ```
   var calleeFuncIdx int = fnIdx - 1
   if calleeFuncIdx < 0 ||
       calleeFuncIdx >= len(vm.Funcs) {
       if dispatchNativeIndirect(...) { continue }
       … error
   }
   // VM function call
   ```
   arm goes away.  Every indirect call is a native indirect call;
   `dispatchNativeIndirect`'s Imm=1/8/9 cover the shapes the
   existing IR-magics emit (`rt._call_free_fn`,
   `rt._call_shim_scalar`, `rt._call_shim_aggregate`).

5. **The IR-magic `_raw_func_addr` lowers identically in both
   backends after this change** — it always means "give me a real
   native function pointer."  Native codegen emits the symbol
   reference directly (unchanged); the VM lowers it to
   `BC_FUNC_ADDR`, whose handler does the registry lookup.

#### What doesn't change

- **`BC_CALL_FUNC_VALUE` same-mode short-circuit.**  The handler
  in `vm_exec.bn:284-356` peeks the closure record's
  `vm_func_idx` slot and dispatches directly via the in-vm
  function index, bypassing the trampoline.  That's fine — the
  closure record's idx is paired with a vm handle in the same
  record, so it's not vm-context-ambiguous.
- **`BC_CALL_IFACE_METHOD` / IfaceVtable.Methods.**  These are
  per-vm dispatch tables; the indices never leave the vm.
- **`BC_REFDEC_INLINE_FAST` dtor idx (`Src2`).**  Same shape:
  the dtor idx is loaded from a Methods slot or function-local
  storage, both within a single vm's lifetime.  Worth revisiting
  in a follow-up cleanup, but not load-bearing for the current
  bug.

### Mechanism: how inner-vm `vm.Externs` gets the trampoline addr

When `cmd/bni`'s `main` runs at the outermost native level:

```binate
var vmInst @vm.VM = vm.NewVM(…)
registerStandardExterns(vmInst)   // <-- now also registers
                                  //     vm.TrampolineScalar /
                                  //     Aggregate
…
```

This is **native compiled** code.  Inside
`registerStandardExterns`, the line `var fv *func(…) =
vm.TrampolineScalar` is compiled by bnc to use the static
`__vt.vm.TrampolineScalar` whose call slot is the native shim
address (i.e., `&__shim.vm.TrampolineScalar`).  `RegisterExtern`
copies that fv into `vmInst.Externs`.

When `runTests` later calls `vmInst.CallFunc(…)` and the test runs
the same `registerStandardExterns(VM_T)` from bytecode-mode:

- The bytecode is interpreted by the outer native execLoop.
- The `var fv *func(…) = vm.TrampolineScalar` line compiles to
  `BC_FUNC_VALUE`.
- The native BC_FUNC_VALUE handler (Path A under the new design)
  reads the executing vm's `Externs["vm.TrampolineScalar"]` — and
  the executing vm here is `VM_INNER_CMD_BNI` (the vm whose
  bytecode the outer native execLoop is iterating), which had
  `vm.TrampolineScalar` registered at its own `registerStandardExterns`
  call.  So Path A finds the binding, copies its 16-byte vtable
  pointer / data pointer pair → the registered `fv` has the
  native trampoline shim address.
- That fv is then passed to `RegisterExtern(VM_T, …)`, which
  copies it into `VM_T.Externs`.

So the chain `outermost native → VM_INNER_CMD_BNI → VM_T`
propagates the trampoline native address purely through the
existing Externs-copy pattern.  No special vm-struct fields, no
process-global, no JIT.  The only invariant: **every binary that
ever creates a VM needs to call `registerStandardExterns` on
that VM at startup before it's used** — which is already the
convention.

### Migration phases

Each phase is independently committable and keeps the test
suite green.

1. **Phase 1 — add TrampolineScalar/Aggregate to standard
   registrations** (small, additive).  Update
   `pkg/vm/extern_register_std.bn` to register them alongside the
   rest.  Confirm via lldb that `vm.Externs["vm.TrampolineScalar"]`
   now exists in `VM_T.Externs` and its call slot is a native
   pointer.

2. **Phase 2 — rewrite Path B to use the registry** (the actual
   bug fix).  In `pkg/vm/vm_exec_funcref.bn:99-107`, replace the
   "lazy-build a heap vtable" code with an `Externs` lookup.
   Keep the closure record allocation as-is (its `vm_func_idx`
   stays).  Test:
   `--test --run TestExecRefIncRefDecInline pkg/vm` under
   boot-comp-int-int should pass.  Remove the xfail.

3. **Phase 3 — change `BC_FUNC_ADDR` to return native pointers**
   (the broader cleanup the user asked for).  Update the handler
   (`pkg/vm/vm_exec_funcref.bn:16-27`) to look up via Externs.
   For functions that aren't in Externs (e.g., a function the
   user takes the address of but isn't pre-registered), see the
   open question below.

4. **Phase 4 (revised) — universal function-value handles.**
   Detailed below.  Replaces the original "just drop the
   `BC_CALL_INDIRECT` magnitude arm" Phase 4, which couldn't
   stand alone: it would have crashed any bytecode-only function
   whose address gets taken (test-helper dtors in
   `pkg/rt/rt_test.bn`, user struct dtors loaded into a vm at
   runtime, REPL-defined functions).  Universal trampolines give
   those a real native dispatch target, so the magnitude arm
   genuinely becomes dead.

5. **Phase 5 — strip dead code**: Path B's vtable construction,
   `dispatchNativeIndirect`'s "fall through to vm-idx" comments,
   anything that distinguished the two cases.

### Phase 4 revised — universal function-value handles

After Phase 3, `_raw_func_addr(F)` returns the real native
address of F's per-function shim — but only when F is registered
as an extern.  Functions that exist *only* as bytecode in some
vm (test helpers under `boot-comp-int`, REPL-defined functions,
user struct dtors emitted by bnc into a sub-vm) still fall back
to `idx + 1`, which can't be passed to a native indirect call
and so requires `BC_CALL_INDIRECT`'s magnitude check to survive.

**Goal of Phase 4:** the result of "give me a function pointer"
is a single *uint8 that points to a stable 16-byte `{vtable, data}`
function-value handle.  Dispatch is uniform:
`handle.vtable.call(handle.data, args...)`.  No magnitude check;
no per-call-site idx production.

#### Rename
`_raw_func_addr(F)` → `_func_handle(F)`.  The name change is
substantive: "raw" used to mean "the function's raw symbol
address," which is no longer what it is.

#### Storage

**Natively-compiled functions:** `bnc` emits a static
`__handle.F = { vtable=&__vt.F, data=null }` global per function
whose address may be taken.  `_func_handle(F)` at native level is
just the symbol address `&__handle.F`.  Zero heap pressure.

**Bytecode-only functions:** `BC_FUNC_HANDLE` (the renamed
opcode) lazy-allocates the handle on first use and caches it on
the `VMFunc`:
 * `Vtable` field: 16-byte `{ dtor=0, call=&TrampolineScalar }`
   (or `&TrampolineAggregate` based on return shape).
 * `VMClosureRec` field: 32-byte `{ kind, vm, vm_func_idx, captured=0 }`.
 * `Handle` (new) field: 16-byte `{ vtable, data }` whose vtable
   points to `Vtable` and whose data points to `VMClosureRec`.
   `_func_handle(F)` returns the address of this.

All three lazy allocations are **refcounted** (`rt.Alloc` not
`rt.RawAlloc`).  The field types on `VMFunc` change from raw
`int` to managed (e.g. `@[]uint8`); VMFunc's auto-emitted dtor
refdecs them on death.  This also fixes a pre-existing leak —
the current Path B already allocates these via `rt.RawAlloc`
and never frees them when VMFunc dies (see
`claude-todo.md`).

#### Dispatch

`_call_free_fn` and `_call_dtor` change from "indirect call
fn(arg)" to "dispatch via handle:"
 * Load `handle[0]` → vtable address.
 * Load `vtable[1]` → call slot (the native function).
 * Call `call(handle[1], arg)`.

In bytecode this is the existing `BC_CALL_FUNC_VALUE` shape
(the handle pointer IS the function-value address that opcode
already expects).  In native it's two loads + an indirect call —
LLVM IR generates fine.

After this, `BC_CALL_INDIRECT`'s magnitude check goes away:
every `fnIdx` it sees is a real native pointer.
`dispatchNativeIndirect` becomes the only path.

#### Compiler-internal dtor idx (don't go through the handle)

`emitManagedPtrRefDec` currently emits `b.EmitFuncAddr(dtorName)`
to feed the dtor into `BC_REFDEC_INLINE_FAST`'s `Src2`.  The
fast path consumes a 1-based intra-vm idx (`vm.Funcs[idx-1]`)
and the audit table classifies this as "intra-vm OK."  After
Phase 4, `OP_FUNC_ADDR` / `_func_handle` no longer produces
that shape — it produces a handle pointer.  So the dtor-fast-
path dispatch needs a different source.

Split out: introduce `b.EmitDtorIdx(dtorName)` (compiler-
internal, never user-callable) that emits a new IR op
`OP_DTOR_IDX` → bytecode op `BC_DTOR_IDX`.  Both backends
lower it to "intra-vm function idx + 1" specifically for the
inline-RefDec fast path.  This is the minimal carve-out;
everything else moves to handles.

#### Non-goals

 * `BC_CALL_IFACE_METHOD` / `IfaceVtable.Methods` slots stay
   1-based intra-vm idx.  These never escape the vm and the
   handle shape would force extra allocation per method
   slot for no benefit.
 * `BC_CALL_FUNC_VALUE` is unchanged — handles are the input
   shape it already accepts.

#### Was-a-non-goal-now-MUST-FIX (2026-05-22): BC_REFDEC_INLINE_FAST idx form

The earlier draft of this plan listed
`BC_REFDEC_INLINE_FAST`'s intra-vm idx dispatch as a non-goal
("revisit later").  That was wrong.  Idx-form dtor refs are
intra-vm-only: a managed value created in native and crossing
into a bytecode VM (or vice-versa) cannot have its dtor resolved
via `vm.Funcs[idx-1]` when the dtor lives in the *other* mode.
That defeats the whole point of this plan — uniform interop via
handles.

The proper interop-compatible design (still iterative, no host
recursion):

 * `emitManagedPtrRefDec` emits OP_FUNC_HANDLE for the dtor ref
   (not OP_FUNC_ADDR).  Native: `&__handle.F`.  Bytecode:
   pointer to the lazy-allocated handle on `VMFunc.Handle`.
 * `BC_REFDEC_INLINE_FAST`'s slow path treats `Src2` as a handle
   pointer.  Read `handle.data`, check its `kind`
   discriminator (same trick `dispatchCompiledFuncValue` uses
   for `BC_CALL_FUNC_VALUE`):
   * `DATA_KIND_VM_CLOSURE_REC` → recover `FnIdx`, do the
     existing iterative push (`pushFrame`, `freeOnPop`, etc.).
     No host recursion — the dtor runs as a bytecode frame
     that `BC_RETURN` pops.
   * other (compiled-side) → cross-mode call via
     `rt._call_shim_scalar` / `dispatchCompiledFuncValue`.
     This *does* take a host frame but cannot recurse back into
     the bytecode VM, so the depth is bounded by the cross-mode
     call chain — not by the dtor's field graph.

Pre-existing `065e6f4` (dtor refs use OP_FUNC_ADDR; restore
iterative dispatch) and `a654afd` (BC_CALL_INDIRECT idx-arm drop
"capstone") landed the idx form as a stop-gap to fix
`builder-comp-int-int` stack overflow without doing the kind-
discriminating handle work.  That has to be undone in favor of
the design above before this plan is done.

#### Migration order

 1. Switch existing Path B lazy allocs to refcounted (managed
    types on VMFunc).  Pre-fix for the leak, isolated change.
 2. Add `HandleAddr` (managed) to `ExternBinding`; update
    `RegisterExtern` signature.  Callers updated to pass the
    handle alongside the existing fv-addr / RawFnAddr.
 3. bnc emits `__handle.F` per function.
 4. Rename `_raw_func_addr` → `_func_handle`; native codegen
    lowers to `&__handle.F`.
 5. Rename `BC_FUNC_ADDR` → `BC_FUNC_HANDLE`; handler reads
    `HandleAddr` from Externs (hit) or lazy-allocates from
    VMFunc fields (miss).
 6. Update `_call_free_fn` / `_call_dtor` lowering to handle-
    dispatch (BC_CALL_FUNC_VALUE shape in bytecode, two-load
    indirect call in native).
 7. Drop `BC_CALL_INDIRECT`'s magnitude arm.  Update
    `dispatchNativeIndirect`'s Imm=1 arm to load handle, dispatch.
 8. Split out `EmitDtorIdx` / `OP_DTOR_IDX` / `BC_DTOR_IDX`;
    update `emitManagedPtrRefDec`.
 9. Final pass: pkg/vm + conformance in all three modes.

### Audit: where 1-based VM indices currently live

Found by grepping for `idx + 1` / `fnIdx - 1` / explicit
`vm.Funcs[…]` in dispatch handlers.  Tagged "fixed by this plan"
if the migration touches it, "intra-vm OK" if it remains as-is.

| Site | What | Status under this plan |
|------|------|------------------------|
| `BC_FUNC_ADDR` handler (`vm_exec_funcref.bn:16-27`) | Stores `idx + 1` in regs | **fixed**: returns native ptr |
| `BC_FUNC_VALUE` Path B (`vm_exec_funcref.bn:99-107`) | vtable.call = `_raw_func_addr(TrampolineScalar)` → idx+1 in bytecode | **fixed**: Path B disappears |
| `BC_CALL_INDIRECT` (`vm_exec.bn:237-263`) | Tests `fnIdx-1 < len(vm.Funcs)` | **fixed**: arm removed |
| `BC_CALL_FUNC_VALUE` (`vm_exec.bn:284-356`) | Reads `closureRec[2]` (vm_func_idx) | intra-vm OK (paired with vm handle) |
| `BC_CALL_IFACE_METHOD` (`vm_exec.bn:358-407`) | `vt.Methods[slot]` = idx+1 | intra-vm OK |
| `lowerImplVtables` (`lower.bn:226-262`) | `slots[i] = idx+1` | intra-vm OK (constructs IfaceVtable) |
| `BC_REFDEC_INLINE_FAST` (`vm_exec.bn:444-462`) | Reads `dtorIdx` (1-based), `dtorIdx-1` for vm.Funcs lookup | **MUST FIX**: handle ptr in Src2 + kind-discriminated iterative dispatch (see "MUST-FIX" section above) |
| `BC_IFACE_DTOR` (`vm_exec.bn` — to confirm) | Reads vt.Methods[0] | intra-vm OK |

So the migration surface is small: three handlers, one Path B
block, one lowering site for closure records, and two
`extern_register_std.bn` additions.

### Open questions

1. **What does `BC_FUNC_ADDR` do for a function that has no
   native shim in the current process?**
    - Practical case today: every function in the loaded packages
      *does* have a native shim, because bnc emits one per
      function in the binary.  So
      `vm.Externs["vm.<name>"]` exists for any name we'd
      legitimately take the address of, as long as
      `registerStandardExterns` covers the function in
      question.
    - A REPL that *defines* a new function at the prompt and
      then takes its address: that function has no native shim
      (it was just IR-built).  Under this plan, we'd need to
      construct a TrampolineScalar-shape function value
      on-the-fly (vtable.call = native trampoline addr, data =
      fresh closure rec).  That's exactly the bytecode-only
      arm of Phase 2's BC_FUNC_VALUE handler; the same logic
      can serve `BC_FUNC_ADDR` for these cases — but the result
      is a `BnFuncValue` (16 bytes on stack), not a scalar
      pointer.  That's a TYPE change for `_raw_func_addr`'s
      result on bytecode-only functions, which has follow-on
      ABI implications.  Defer this to a follow-up plan; the
      bug we're fixing right now only needs the shim case.
    - Decision: for Phase 3, BC_FUNC_ADDR errors if the
      function is bytecode-only and not registered.  Note in a
      TODO for the REPL-defined-function case.

2. **Which binaries need to register
   `vm.TrampolineScalar`/`Aggregate`?**
    - Every binary that uses pkg/vm to create an inner VM.
      Today: cmd/bni, cmd/bnc (test runner), cmd/bnlint maybe.
    - Pure non-VM binaries don't need it.
    - Recommendation: put the registration inside
      `registerStandardExterns`; binaries that don't need pkg/vm
      don't call it.  Strictly required for any binary that calls
      `vm.NewVM`.

3. **Method dispatch tables (`IfaceVtable.Methods`).**  Currently
   1-based vm idx, intra-vm only.  If a future plan wants to
   eliminate idx-based dispatch *everywhere* (e.g., to allow an
   interface value to be passed across vm boundaries), this would
   also need to migrate to native pointers.  Out of scope for
   this plan; flagged for the follow-up.

### Test coverage

- **Reproducer**: `--test --run TestExecRefIncRefDecInline pkg/vm`
  under boot-comp-int-int crashes today (SEGV).  After Phase 2,
  this passes; remove `scripts/unittest/pkg-vm.xfail.boot-comp-int-int`.
- **Existing tests** in `vm_exec_funcref_test.bn` already cover
  Path A + Path B; both should continue to pass.
- **New unit test** for the registry-lookup-from-bytecode
  scenario: a bytecode-built function value pointing at a
  function with a registered native shim should dispatch
  correctly.  pkg/vm test, exercises Phase 2's path.
- **Cross-vm dispatch test**: a 3-level scenario where an inner
  vm's binding is consumed by yet-deeper-level dispatch.  This is
  essentially `TestExecRefIncRefDecInline` plus a deeper nest;
  the existing test already exercises 3-level — adding a 4-level
  variant would over-engineer.

### Risk / things to watch

- **Static vtable layout in pkg/codegen** must stay compatible
  with the runtime-allocated layout (16 bytes, dtor at offset 0,
  call at offset 8).  Path A already assumes this; no change.
- **Performance**: removing the same-mode VM-idx short-circuit in
  `BC_CALL_INDIRECT` means every indirect call goes through
  `dispatchNativeIndirect` even when the target is a bytecode-VM
  function in the same vm.  We expect this to be uncommon (most
  same-mode calls go through `BC_CALL` or `BC_CALL_FUNC_VALUE`,
  not BC_CALL_INDIRECT), but worth benchmarking on the
  conformance suite.
- **Order of registration matters**: `registerStandardExterns`
  must run *before* any code path that constructs a function
  value pointing at a registered function.  Today's flow
  satisfies this (registration is first in cmd/bni's main); the
  invariant just needs to hold in any new VM-using binary.
