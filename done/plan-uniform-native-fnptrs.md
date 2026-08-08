## Plan: Uniform native function pointers in the bytecode VM

### Status

COMPLETE (shipped, 2026-05-24); kept for design rationale.  Every
function-pointer-shaped value is now a real function-value handle;
the BC_CALL_INDIRECT magnitude check is gone.  Two follow-on items
remained open at landing (see "Deferred / open items" at the end).

### Problem statement

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

### Key gotcha: the shim drops `data`, so trampolines need the raw function addr

An early attempt was: "read `vm.Externs["vm.TrampolineScalar"].VtableAddr`
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
address.  `_raw_func_addr(TrampolineScalar)` resolved to that
function address directly in native code — which was correct in
2-level scenarios.

The libc/bootstrap pattern works for them because the shim *is* the
right call slot — those functions don't introspect `data`.  The
trampolines are the only signature that explicitly consumes `data`
as a user-facing parameter.

### Constraint: no new C

User feedback (2026-05-14): no new C — Binate stays self-hosted.
A process-global approach (C-level globals set at native startup,
read by a libc-shape getter) was rejected on these grounds.  The
trampoline native address propagates purely through the existing
Externs-copy pattern instead (see "Mechanism" below).

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
TrampolineAggregate**, and the registration constructs the function
value natively from `var fv *func(…) = vm.TrampolineScalar`; bnc
emits `__vt.vm.TrampolineScalar` statically with a real native call
slot.  This adds two more entries to every vm's `Externs` table.

#### What doesn't change

- **`BC_CALL_FUNC_VALUE` same-mode short-circuit.**  The handler
  peeks the closure record's `vm_func_idx` slot and dispatches
  directly via the in-vm function index, bypassing the trampoline.
  That's fine — the closure record's idx is paired with a vm handle
  in the same record, so it's not vm-context-ambiguous.
- **`BC_CALL_IFACE_METHOD` / IfaceVtable.Methods.**  These are
  per-vm dispatch tables; the indices never leave the vm.
- **`BC_REFDEC_INLINE_FAST` dtor idx.**  (Initially listed here as
  "revisit later", but that turned out to be wrong — see
  "Interop-correct dtor dispatch" below.)

### Mechanism: how inner-vm `vm.Externs` gets the trampoline addr

When `cmd/bni`'s `main` runs at the outermost native level, it
calls `registerStandardExterns(vmInst)`, which now also registers
`vm.TrampolineScalar` / `Aggregate`.  This is **native compiled**
code: the line `var fv *func(…) = vm.TrampolineScalar` is compiled
by bnc to use the static `__vt.vm.TrampolineScalar` whose call slot
is the native shim address.  `RegisterExtern` copies that fv into
`vmInst.Externs`.

When `runTests` later calls `vmInst.CallFunc(…)` and the test runs
the same `registerStandardExterns(VM_T)` from bytecode-mode:

- The bytecode is interpreted by the outer native execLoop.
- The `var fv *func(…) = vm.TrampolineScalar` line compiles to
  the function-value construction opcode.
- The native handler reads the executing vm's
  `Externs["vm.TrampolineScalar"]` — and the executing vm here is
  `VM_INNER_CMD_BNI` (the vm whose bytecode the outer native
  execLoop is iterating), which had `vm.TrampolineScalar`
  registered at its own `registerStandardExterns` call.  So the
  handler finds the binding, copies its 16-byte vtable pointer /
  data pointer pair → the registered `fv` has the native
  trampoline shim address.
- That fv is then passed to `RegisterExtern(VM_T, …)`, which
  copies it into `VM_T.Externs`.

So the chain `outermost native → VM_INNER_CMD_BNI → VM_T`
propagates the trampoline native address purely through the
existing Externs-copy pattern.  No special vm-struct fields, no
process-global, no JIT.  The only invariant: **every binary that
ever creates a VM needs to call `registerStandardExterns` on
that VM at startup before it's used** — which is already the
convention.

### Universal function-value handles

`_raw_func_addr(F)` was renamed to `_func_handle(F)`.  The name
change is substantive: "raw" used to mean "the function's raw
symbol address," which is no longer what it is.  (The legacy
`_raw_func_addr` spelling is kept as a transitional alias; see
"Deferred / open items".)

The result of "give me a function pointer" is a single `*uint8`
that points to a stable 16-byte `{vtable, data}` function-value
handle.  Dispatch is uniform: `handle.vtable.call(handle.data,
args...)`.  No magnitude check; no per-call-site idx production.

#### Storage

**Natively-compiled functions:** `bnc` emits a static
`__handle.F = { vtable=&__vt.F, data=null }` global per function
whose address may be taken.  `_func_handle(F)` at native level is
just the symbol address `&__handle.F`.  Zero heap pressure.

**Bytecode-only functions:** `BC_FUNC_HANDLE` lazy-allocates the
handle on first use and caches it on the `VMFunc`:
 * `Vtable` field: 16-byte `{ dtor=0, call=&TrampolineScalar }`
   (or `&TrampolineAggregate` based on return shape).
 * `VMClosureRec` field: 32-byte `{ kind, vm, vm_func_idx, captured=0 }`.
 * `Handle` field: 16-byte `{ vtable, data }` whose vtable
   points to `Vtable` and whose data points to `VMClosureRec`.
   `_func_handle(F)` returns the address of this.

All three lazy allocations are **refcounted** (`rt.Alloc` not
`rt.RawAlloc`).  The field types on `VMFunc` are managed (e.g.
`@[]uint8`); VMFunc's auto-emitted dtor refdecs them on death.
This also fixes a pre-existing leak — the old Path B allocated
these via `rt.RawAlloc` and never freed them when VMFunc died.

#### Dispatch

`_call_free_fn` and `_call_dtor` dispatch via the handle:
 * Load `handle[0]` → vtable address.
 * Load `vtable[1]` → call slot (the native function).
 * Call `call(handle[1], arg)`.

In bytecode this is the existing `BC_CALL_FUNC_VALUE` shape
(the handle pointer IS the function-value address that opcode
already expects).  In native it's two loads + an indirect call.

After this, `BC_CALL_INDIRECT`'s magnitude check goes away:
every `fnIdx` it sees is a real native pointer.
`dispatchNativeIndirect` becomes the only path.  Imm=8 / Imm=9
remain for the `_call_shim_*` shapes; the Imm=1 / free_fn shape
disappeared with the magnitude arm (`_call_free_fn` now routes
through the handle).

#### Interop-correct dtor dispatch (was-a-non-goal-now-MUST-FIX)

The earlier draft of this plan listed `BC_REFDEC_INLINE_FAST`'s
intra-vm idx dispatch as a non-goal ("revisit later").  That was
wrong.  Idx-form dtor refs are intra-vm-only: a managed value
created in native and crossing into a bytecode VM (or vice-versa)
cannot have its dtor resolved via `vm.Funcs[idx-1]` when the dtor
lives in the *other* mode.  That defeats the whole point of this
plan — uniform interop via handles.

The proper interop-compatible design (still iterative, no host
recursion):

 * `emitManagedPtrRefDec` emits the handle ref for the dtor (not
   a raw func addr).  Native: `&__handle.F`.  Bytecode: pointer to
   the lazy-allocated handle on `VMFunc.Handle`.
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

An intermediate stop-gap landed the idx form (dtor refs use
OP_FUNC_ADDR; restore iterative dispatch) to fix a
`builder-comp-int-int` stack overflow without doing the kind-
discriminating handle work; it was then undone in favor of the
design above.  The kind-tag discrimination also superseded an
earlier proposed `OP_DTOR_IDX` carve-out — no new IR op was needed
once the kind tag does the work.  OP_FUNC_ADDR / BC_FUNC_ADDR were
retired as dead code.

### Audit: where 1-based VM indices currently live

Tagged "fixed by this plan" if the migration touches it,
"intra-vm OK" if it remains as-is.

| Site | What | Status under this plan |
|------|------|------------------------|
| `BC_FUNC_ADDR` handler | Stored `idx + 1` in regs | **fixed**: returns handle ptr (op retired) |
| `BC_FUNC_VALUE` Path B | vtable.call = `_raw_func_addr(TrampolineScalar)` → idx+1 in bytecode | **fixed**: Path B disappears |
| `BC_CALL_INDIRECT` | Tested `fnIdx-1 < len(vm.Funcs)` | **fixed**: arm removed |
| `BC_CALL_FUNC_VALUE` | Reads `closureRec[2]` (vm_func_idx) | intra-vm OK (paired with vm handle) |
| `BC_CALL_IFACE_METHOD` | `vt.Methods[slot]` = idx+1 | intra-vm OK |
| `lowerImplVtables` | `slots[i] = idx+1` | intra-vm OK (constructs IfaceVtable) |
| `BC_REFDEC_INLINE_FAST` | Reads handle ptr from Src2; if `handle.data` kind = `DATA_KIND_VM_CLOSURE_REC` → iterative push via `closureRec[2]`; else cross-mode shim call via `rt._call_shim_scalar`. | **fixed** |
| `BC_IFACE_DTOR` | Reads vt.Methods[0] | intra-vm OK |

### Deferred / open items

- **`_raw_func_addr` alias** in `pkg/token.Lookup` stays until the
  prebuilt BUILDER bumps to a version that natively recognizes
  `_func_handle`, at which point callers in pkg/rt / cmd/bni /
  cmd/bnc switch and the alias drops.

- **REPL-defined functions** (`BC_FUNC_ADDR`/`_func_handle` for a
  function with no native shim).  Every function in the loaded
  packages *does* have a native shim, because bnc emits one per
  function in the binary, so `vm.Externs["vm.<name>"]` exists for
  any name we'd legitimately take the address of (as long as
  `registerStandardExterns` covers it).  But a REPL that *defines*
  a new function at the prompt and then takes its address: that
  function has no native shim (it was just IR-built).  Handling it
  needs a TrampolineScalar-shape function value constructed
  on-the-fly — which is a TYPE change for the result on bytecode-
  only functions, with follow-on ABI implications.  Deferred to a
  follow-up plan; flagged for the REPL case.

- **Method dispatch tables (`IfaceVtable.Methods`).**  Currently
  1-based vm idx, intra-vm only.  If a future plan wants to
  eliminate idx-based dispatch *everywhere* (e.g., to allow an
  interface value to be passed across vm boundaries), this would
  also need to migrate to native pointers.  Out of scope for this
  plan.

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
