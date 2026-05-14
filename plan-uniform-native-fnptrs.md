## Plan: Uniform native function pointers in the bytecode VM

### Status

Draft (2026-05-13). Triggered by the
`pkg/vm:TestExecRefIncRefDecInline` SEGV root-caused in
`claude-todo.md` (commit `788ec56`): the bytecode-mode Path B in
`BC_FUNC_VALUE` writes a 1-based VM index into `vtable.call`, and
the same numeric slot points at a different function in a deeper-
nested VM, so cross-VM dispatch lands on the wrong target.

This plan covers the architectural cleanup the user wants: stop
distinguishing "real function pointer" vs "1-based VM index" at
the call-slot / function-pointer level — make every such value a
native function pointer.  The current hacky check
`if calleeFuncIdx < len(vm.Funcs) { vm-dispatch } else { native }`
in `BC_CALL_INDIRECT` goes away; everything is native.

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

4. **Phase 4 — drop the VM-idx-vs-native-ptr dispatch arm in
   `BC_CALL_INDIRECT`** (`pkg/vm/vm_exec.bn:237-263`).  Make
   `dispatchNativeIndirect` the only path.  Validate that no
   regression by running the full conformance + unit suite.

5. **Phase 5 — strip dead code**: Path B's vtable construction,
   `dispatchNativeIndirect`'s "fall through to vm-idx" comments,
   anything that distinguished the two cases.

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
| `BC_REFDEC_INLINE_FAST` (`vm_exec.bn:444-462`) | Reads `dtorIdx` (1-based), `dtorIdx-1` for vm.Funcs lookup | intra-vm OK (revisit later) |
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
