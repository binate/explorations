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

## Current state (audit)

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

The LLVM backend (`pkg/codegen`) consumes these CALLs as ordinary
function calls — no special handling needed. **This works correctly.**

The interpreter (`pkg/interp`) does **not** consume the IR copy/dtor
calls. Instead, `pkg/interp/exec.bn` and `helpers_refcount.bn` use
hand-written `structRefInc`/`structRefDec` helpers driven by the same
type info. Duplicate implementation, opportunity for divergence (see
the just-fixed double-RefInc bug for `@T` parameters).

The bytecode VM (`pkg/vm`) also does not consume the copy/dtor calls
correctly. The bug (`pkg/vm/vm_extern_test.bn:TestRepro_…ManagedSliceFieldAppend`
under `boot-comp-int2`) shows that struct-field `@[]char` backings get
freed prematurely. Two questions to answer in step 1 below: are the IR
CALLs actually present in the bytecode the VM lowers, and if so, why
don't they preserve refcounts?

## Plan

### Step 1 — Diagnose the VM's actual gap (quick, ≤30 min)

Before changing anything, confirm which of these is true for the
failing repro:

- (A) `pkg/ir.emitStructCopy` is being called and emits a `CALL
  __copy_Pair` IR instruction, the VM lowers it as a regular `BC_CALL`,
  but `__copy_Pair` itself is not generated/registered in the VM (so
  the call resolves to a no-op or extern-not-found path).
- (B) The IR CALL is emitted and the function exists, but
  `pkg/vm/lower.bn` skips or mis-lowers calls to compiler-generated
  helpers (e.g., `LookupFunc` doesn't see them).
- (C) `emitStructCopy` is **not** called at the slice-element store
  site (`ns[i] = s[i]`) — i.e., a code path in `gen_control.bn`
  reaches `EmitStore` without the copy call.

Check by inspecting the IR module produced for the repro source (dump
function names and the body of `appendPair`). This decides what the
fix looks like.

### Step 2 — Make IR uniformly emit copy/dtor calls

Whichever case from step 1, the goal is: **for every struct/array
copy site and scope exit, IR contains a CALL to `__copy_X`/`__dtor_X`
that all backends will see as a normal call.**

Likely sub-tasks:
- Audit `gen_control.bn` paths that end in `EmitStore` for struct
  values; ensure each is preceded by an `emitStructCopy` when the
  destination is freshly written (and for slice-element writes, also
  RefDec the previously-stored value if any).
- Ensure the generated `__copy_X` / `__dtor_X` functions are emitted
  into the IR module that every backend consumes (not gated by
  backend).
- Cross-package: when a struct lives in package A but is copied in
  package B's code, the copy function must be available — either
  declared extern in B and defined in A, or generated link-once in B.
  `pkg/ir/gen_copy.bn` already has `qualifiedCopyName` support; verify
  the per-module emission story is right.

### Step 3 — Bytecode VM consumes the calls

With step 2 done, the VM should "just work" because the calls are
ordinary `BC_CALL`s. Likely small fixes:
- Make sure `LookupFunc` finds `__copy_X` / `__dtor_X` (qualified
  name conventions should match).
- Remove the special-case in `pkg/vm/lower_instr.bn:lowerStore` that
  emits a raw `BC_MEMCPY` for struct stores — the IR-level copy call
  before the store is what RefIncs the new value, so the store itself
  can stay a memcpy of the bytes (this matches what LLVM does), but
  any "old value RefDec" responsibility must already have been emitted
  by IR.
- Verify scope-exit dtor calls reach the VM (alloca cleanup).

### Step 4 — Interpreter consumes the same calls

Replace `pkg/interp/exec.bn`'s hand-written `structRefInc`/
`structRefDec` calls with execution of the IR CALL instructions to
`__copy_X`/`__dtor_X`. The interpreter already runs IR; this is just
deleting the hand-written paths and trusting the IR emission. Keep
the helpers around if needed as the IMPL of the IR copy/dtor functions
when the interpreter executes them, but the *invocation* should come
from IR, not from a separate scope-walking pass.

### Step 5 — Tests + cleanup

- `pkg/vm/vm_extern_test.bn:TestRepro_StructWithManagedSliceFieldAppend`
  must pass under `boot-comp-int2`.
- `pkg/ir.TestGenConstIota` must pass under `boot-comp-int2` (this is
  the original symptom).
- All existing conformance tests still green across all 4 modes.
- Lift the `pkg/ir`, `pkg/codegen`, `cmd/bnlint` xfails for
  `boot-comp-int2` (we expect this fix to unblock them).
- Possibly lift `pkg/vm` xfail (depends on whether other VM bugs
  remain — investigate after the copy/dtor fix lands).
- `pkg/interp` xfail likely remains (separate "dead end" interpreter
  per `Interp vs VM` memory).

## Out of scope

- Refactoring the dtor naming scheme.
- Performance optimization of the generated copy/dtor functions
  (e.g., specialized "copy and inline-RefInc" opcodes for the VM).
- Removing the interpreter altogether — it's likely dead-end work
  but stays around until the VM is fully proven.

## Risk

- Step 4 (interpreter) risks breaking interp tests that currently
  pass on the old hand-written path. If the cost is high relative to
  the value, scope down to steps 1–3 and 5; the interpreter migration
  can wait, since the interpreter is on the deprecation path.
- Step 3's removal of the raw-BC_MEMCPY shortcut needs care: a
  struct copy where the source is an IR temp (no live alias) may not
  need the full copy-then-dtor dance.
