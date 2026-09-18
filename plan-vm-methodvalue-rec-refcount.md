# Plan: free the method-value closure record (VM `*func` rec leak)

Status: PLAN (claimed work-2, 2026-09-18). Tracks the "VM leaks the
per-CAPTURING-func-value closure record" item in claude-todo.md.

## Root cause (confirmed empirically)

A capturing func value's data record is a **managed block** the VM allocates in
`BC_FUNC_VALUE` (`vm/vm_exec_funcref.bn`, capturing branch: `rt.Alloc` of a 4-word
`{kind, vm, fnIdx+1, captured}` rec).  A MANAGED `@func` value RefDec's that record
through its lifecycle (`emitManagedFuncValueRefDec` RefDec's the value's data slot
via the closure-struct dtor), so it is freed.

But `genMethodValue` (`ir/gen_method_value.bn:220`) types every method value as a
**raw `*func`** (`MakeFuncValueType`, not `MakeManagedFuncValueType`).  A raw `*func`
has no RefInc/RefDec lifecycle, so a method value NEVER RefDec's its record → the
record leaks, one heap block per method-value construction, on the normal path.

Empirical (control experiment, current landed tree):
- plain function (no method value): 0 blocks leaked over N calls.
- capturing closure literal (`@func`): 0 (RefDec frees the managed rec).
- `*func` method value (value receiver, no managed fields): exactly N (one rec per
  construction).

So the leak is specific to method values (raw `*func`), not `@func` closures, and
not overflow-specific.  Latent because conformance checks output, not
`rt.LiveBlocks()`.

## Why not just make method values `@func`

The checker types a bound method value as `*func` and every conformance test /
caller uses `var mv *func(...) = obj.M` (503, 505, 511, 946, 947, ...).  Changing
the method-value type to `@func` is a language/type-system change (needs the "NEVER
change language semantics without asking" gate) and would churn every call site.  So
keep the `*func` surface; fix the ownership of the rec at the construction site.

## Current cleanup (and the gap)

`genMethodValue` today, when `closureStruct.NeedsDestruction()`, calls
`registerMethodValueLocalForCleanup` → `defineVar(closurePtr, closureStruct)`, so
scope cleanup (`emitDecForManagedLocals`) runs the closure-struct dtor on
`closurePtr` — RefDec'ing the CAPTURED managed fields.  It does NOT touch the VM
record (a separate heap block the VM allocates at `BC_FUNC_VALUE`, pointing at
`closurePtr`).  For a NON-managed closure struct it registers nothing at all.  Either
way the record leaks.

Note the double-free hazard: `emitManagedFuncValueRefDec` on the func value RefDec's
the record via the closure-struct dtor, which ALSO RefDec's the captured fields.  So
"RefDec the record" and "run the struct dtor on closurePtr" both release the captured
fields — doing both double-frees them.

## Fix (recommended): give the method-value LOCAL the func-value RefDec lifecycle

Treat a method-value local as OWNING its record (like `@func`), with `*func` copies
borrowing — mirroring the managed-slice→raw-slice borrow model (raw borrows; the
managed owner is scope-cleaned).  Concretely, at the method-value site:

1. Register the method-value RESULT (the `fv` instr / its data record) for a
   scope-end `emitManagedFuncValueRefDec` — which frees the record AND, via the
   closure-struct dtor, RefDec's the captured managed fields.
2. REPLACE (do not add alongside) the current `registerMethodValueLocalForCleanup`
   closurePtr-struct-dtor cleanup with (1), so the captured fields are released
   exactly once (by the record's dtor), never twice.
3. Do this for BOTH managed-capture and non-managed-capture method values (the
   record leaks in both; the non-managed case just has no fields for the dtor to
   RefDec, but the record itself still needs freeing).

Open implementation questions to resolve while coding:
- The record RefDec must run at the LOCAL's scope end, and `*func` copies passed to
  callees must borrow (no RefInc) and not outlive the local.  Confirm the existing
  `*func` copy/param/return paths already treat it as a borrow (they should — `*func`
  is raw); a method value `return`ed as `*func` that outlives its record is user
  error (coding-guide "raw borrows").
- Cross-mode: the record's dtor (`EmitFuncValueDtor` → the closure-struct dtor) must
  resolve for the wrapper's closure struct in every mode (it already does for
  `@func` closure literals — reuse that path).
- Avoid touching the `@func` closure-literal path (already correct); scope the change
  to the method-value construction site only.

Alternative considered (rejected): free the record VM-side at `closurePtr`'s frame
pop. Rejected — it doesn't cover the non-managed closure struct (closurePtr
unregistered), splits ownership across IR-gen + VM, and duplicates the dtor logic
`emitManagedFuncValueRefDec` already has.

## FOLD IN (FINDING 2, from the `8ff97d2ab` wrapper review)

The wrapper's value-struct-receiver `emitStructCopy` (`gen_method_value_wrapper.bn`)
emits a `__copy` CALL just BEFORE the forward's `OP_STACK_CHECK`, with no pad; a
`__copy`-frame overflow (narrow VM-only window) hits an unpadded op → leak/vmPanic.
Fix together: move/extend the pre-check to cover the copy, or pad the copy call.

## Verification

- A method-value / capturing-closure construction called N times → stable
  `rt.LiveBlocks()` (the control experiment shape), as a `pkg/binate/vm` unit test.
  Non-vacuous: fails (delta = N) without the fix.
- Method-value conformance green (503/505/506/511/946/947/1270/1271) on LLVM, VM,
  native aa64 — no double-free/UAF regression (esp. the managed-capture cases 511 +
  the value-struct-receiver 1270/1271).
- Adversarial review (refcount ownership: no double-free of captured fields, no UAF
  of a borrowed `*func` copy, cross-mode dtor resolution).

## REVIEW UPDATE (2026-09-18): approach (a) is UNSOUND — rejected

An adversarial design review found approach (a) (and (b)) would introduce memory
corruption on EVERY backend, and would regress the native backend to fix a VM-only
leak.  Do NOT implement (a)/(b).  Key findings:

- The leak is **VM-only**.  On native/LLVM a method value's data slot IS the stack
  closure struct (the shim reads captures straight from it) — there is NO heap rec
  and NO leak, at zero cost.  The heap rec that leaks exists only in the VM's
  `BC_FUNC_VALUE` capturing branch.
- Method-value closure struct is a **stack** `EmitAlloc` (`gen_method_value.bn:233`);
  the `@func` closure-literal path uses a **heap** `EmitMake` (`gen_func_lit.bn:138`)
  and sets `IsManagedFuncValue` + `ClosureStructDtorName` (the wrapper sets neither).
  So `emitManagedFuncValueRefDec` — which assumes a heap-managed data record — would
  RefDec a **stack address**: VM's `compiledClosureDtorMark` path would `rt.Free()`
  `rec[3]` (a stack addr) and never RefDec the captured fields; native/LLVM would
  emit a null dtor and RefDec/free the stack struct pointer.  Corruption either way.
- (a) vs (b) is moot — both route through `emitManagedFuncValueRefDec`, unsafe on a
  stack-backed struct.

Two SOUND directions (a native-perf vs VM-only-leak tradeoff — user's call):

- **Variant V (VM-targeted; native untouched) — reviewer's preference, aligns with
  "native is THE backend, don't regress it for a VM artifact":** leave native/LLVM
  exactly as-is (no rec, no leak, no cost); fix only the VM rec.  Either
  frame-allocate the rec so it survives `OP_SP_RESTORE` (nothing to free — reclaimed
  with the frame, like the closure struct itself), or free JUST the rec (never
  `rec[3]`) tied to the existing stack-struct scope cleanup — NOT via the
  `compiledClosureDtorMark` path (which wrongly frees `rec[3]`).  More VM-specific.

- **Variant H (heap-align method values with `@func`):** make method-value
  construction identical to `genFuncLit`'s managed path — `EmitMake` the struct, set
  `IsManagedFuncValue` + `ClosureStructDtorName`, drive release via the `@func` slot,
  replace `registerMethodValueLocalForCleanup`.  Sound on all backends, reuses proven
  machinery — BUT turns the common no-managed-field method value from a zero-cost
  native STACK alloca into a per-construction heap alloc + refcount + free: a
  native/LLVM regression to fix a VM-only leak.

Either variant must ALSO cover `*func` capturing CLOSURE LITERALS (same VM rec leak),
and fold in FINDING 2 (wrapper `__copy`-before-pre-check pad).  Test must run under
VM AND native AND aarch64 (the corruption (a) would add is native-visible).
