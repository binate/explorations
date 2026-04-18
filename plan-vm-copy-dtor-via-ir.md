# Plan: bytecode VM consumes IR-emitted copy/dtor calls

## Why this matters

Two reasons we must do this work, not just "patch the VM":

1. **Compiler/interpreter interop.** The dual-mode design has compiled and
   interpreted code calling each other via function pointers. Both modes
   must produce identical refcount behavior or shared data will be
   double-freed (or leaked) at the boundary. Today, each backend has its
   own copy/dtor implementation: the compiler emits `__copy_X`/`__dtor_X`
   IR functions; the interpreter has its own `structRefInc`/`structRefDec`
   helpers; the bytecode VM does raw `BC_MEMCPY` with no refcount
   handling. Three independent implementations is exactly the recipe for
   subtle interop divergence.

2. **Multi-backend plan.** The roadmap (see
   `ir-backend-cleanup-plan.md`) is to add additional compiler backends —
   starting with a direct 32-bit ARM backend. Every new backend would
   need its own correct copy/dtor support. Putting the logic in IR means
   one source of truth that all backends consume as ordinary CALLs.

## Current state (after step-1 audit)

The IR layer (`pkg/ir`) **already** generates `__copy_X` and `__dtor_X`
functions and emits CALL instructions to them at copy/scope-exit sites:

- `pkg/ir/gen_copy.bn` — naming scheme (`__copy_Pos`, `__copy_arr5_mp_Node`,
  `pkg.__copy_File` for cross-package).
- `pkg/ir/gen_copy_emit.bn` — emits the function bodies (RefInc per
  managed field, recursive for nested structs/arrays).
- `pkg/ir/gen_util_refcount.bn:emitStructCopy` — emits the CALL at
  copy sites: `var decl`, `var assign`, deref-LHS assign, field-LHS
  assign, slice-elem-LHS assign (in `gen_control.bn:368`), function
  arg/return passes (`gen_stmt.bn:341,481`).
- `emitStructDtor` — emits the CALL at scope-exit sites.

**Verified by instrumentation** (debug print in `emitStructCopy`):
when the failing pkg/ir TestGenConstIota runs, the IR for
`appendModuleConst` does emit two CALLs to `__copy_ModuleConst` (one
per copy site — `ns[i] = s[i]` and `ns[n] = v`). Other call sites
(genConst, genConstGroup, etc.) also emit copy calls correctly.

The LLVM backend (`pkg/codegen`) consumes these CALLs as ordinary
function calls — no special handling needed. **This works correctly.**

The bytecode VM (`pkg/vm`) **also already lowers the copy/dtor CALLs
to ordinary BC_CALL** — and `__copy_ModuleConst` itself is generated
into the IR module and lowered like any other function. So the
"missing copy/dtor" framing was wrong: the VM is consuming the IR
correctly, but the test still fails. The actual gap is more subtle
— something in the chain (struct-store BC_MEMCPY size? RefInc
opcode lowering? cleanup ordering between scope exit and global
reassignment?) is mis-accounting refcounts. Step 1' below replaces
the original step 1 with the targeted follow-up needed to localize
this.

The interpreter (`pkg/interp`) does **not** consume the IR copy/dtor
calls. Instead, `pkg/interp/exec.bn` and `helpers_refcount.bn` use
hand-written `structRefInc`/`structRefDec` helpers driven by the same
type info. Duplicate implementation, opportunity for divergence (see
the just-fixed double-RefInc bug for `@T` parameters). This is the
remaining opportunity for "move to IR" — the interpreter is the one
backend with its own parallel implementation.

## Plan

The plan now has two independent threads:

- **Thread A (architectural — the user's stated motivation)**: migrate
  the interpreter to consume the IR copy/dtor calls so that
  compiler/interpreter use the same single source of truth, and the
  multi-backend roadmap has one mechanism for new backends to inherit.
- **Thread B (tactical — the trigger that surfaced this)**: localize
  and fix the boot-comp-int2 VM struct-copy bug captured by
  `TestRepro_StructWithManagedSliceFieldAppend`. This may share root
  cause with Thread A, but per the step-1 audit, the IR/CALL plumbing
  is already in place — the bug is somewhere narrower.

### Thread B — step 1' (revised diagnosis, focused)

Confirm which of these is true for the failing repro under
boot-comp-int2 (the IR-emit instrumentation already showed the CALLs
*are* being emitted, so this is about runtime execution):

- (B-i) `__copy_X` is in `vm.Funcs` but the BC_CALL doesn't reach it
  (name mangling/qualification skew between caller and callee
  registration).
- (B-ii) `__copy_X` is reached but its emitted body does the wrong
  thing on the VM — e.g., `EmitExtract(slot=2)` reads the wrong word
  of a managed-slice in the VM's struct-field memory layout (vs. LLVM).
  Layout drift would be specifically a `pkg/types`-level bug for the
  VM target.
- (B-iii) `__copy_X` works correctly but a *different* path also
  RefDecs (e.g., scope-exit cleanup running in the wrong order vs.
  global-variable reassignment, double-counting once for the local
  parameter and once for the global).

Concrete probe: instrument `BC_REFINC` / `BC_REFDEC` execution to log
`(addr, op, refcount-after, calling fn name)`, then run the failing
`TestGenConstIota` and walk the trace for `moduleConsts[i].Name`'s
backing pointer. The trace will pinpoint which call goes too far.

### Thread A — interpreter migration

Replace `pkg/interp/exec.bn`'s hand-written `structRefInc`/
`structRefDec` calls with execution of the IR CALL instructions to
`__copy_X`/`__dtor_X`. The interpreter already runs IR; this is just
deleting the hand-written paths and trusting the IR emission. Keep the
helpers around as the IMPL of the IR copy/dtor functions when the
interpreter executes them, but the *invocation* should come from IR,
not from a separate scope-walking pass.

This is risky to do without the failing-mode tests for the interpreter
also stable (per the "Interp vs VM" memory, pkg/interp is likely
dead-end). Defer until thread B is closed and the interpreter is
either proven ground for migration or formally retired.

### Tests + cleanup (post-thread-B)

- `pkg/vm/vm_extern_test.bn:TestRepro_StructWithManagedSliceFieldAppend`
  must pass under `boot-comp-int2`.
- `pkg/ir.TestGenConstIota` must pass under `boot-comp-int2` (this is
  the original symptom).
- All existing conformance tests still green across all 4 modes.
- Lift the `pkg/ir`, `pkg/codegen`, `cmd/bnlint` xfails for
  `boot-comp-int2` (we expect this fix to unblock them).

## Out of scope

- Refactoring the dtor naming scheme.
- Performance optimization of the generated copy/dtor functions.
- Removing the interpreter — likely dead-end work but stays around
  until the VM is fully proven.
