# Plan: native trampoline vtables for bytecode iface impls (inject ANY package)

Fixes the MAJOR `errors.Is` VM SIGSEGV (claude-todo.md) and, generally, makes an
**injected native** stdlib function operate correctly on an interface value
implemented by a **bytecode (user) type** — RefDec/destroy AND method dispatch.
Injection must work for any package; this closes the last interop gap.

## Root cause (confirmed)

An iface value is 2-word `{data, vtable_word}`. `vtable_word` is a tagged union
(range-discriminated by `ifaceVtIsNative`): a 1-based index into
`vm.IfaceVtables` for a bytecode impl, or a native `@__ivt` pointer for an
injected/compiled impl. Injected-**native** code (e.g. `errors.Is`) treats
`vtable_word` as a native pointer unconditionally, so a bytecode impl's index
(e.g. 53) gets dereferenced → SIGSEGV (RefDec's `vtable[0]` dtor load; method
dispatch would fault identically).

## Design: unify iface method dispatch onto the func-value HANDLE convention

`@__ivt` slot 0 (dtor) is ALREADY a `{vtable,data}` handle, dispatched via
`_call_dtor` → `handle.vtable.call(handle.data, ptr)`. `@__ivtshim` already holds
the same handle in every METHOD slot (`@__handle.<m>`), used by the VM's
cross-mode path. Method slots are the only thing still raw (`@__ivt`,
`useHandles=false`), dispatched raw-`blr` — which has no data word for a bytecode
method's `{vm, fnIdx}`. So: make method dispatch handle-based like the dtor. Then
the context rides in the vtable slot's handle (where native code loads it), not a
raw-blr receiver — the real box stays `data`, so `same`/identity keep working; no
proxies, no side tables. A bytecode impl's method/dtor handles carry
`VMClosureRec{vm, fnIdx}` and re-enter the VM via the existing generic
trampolines (`ensureHandle`, `vm_funcvalue_handle.bn`).

## Stages (each keeps the tree green)

**Status (2026-08-03): S1 + S2 LANDED on `main`** — S1 `56826ba8` (dispatch → handles,
all four backends + shared frame sizer + regression tests 1178/1179), S2 `5324048b`
(VM builds native handle-vtables for bytecode impls + substitutes at
`dispatchExternBinding`; repro 1180). Both landed with a minimal adversarial review.
The `errors.Is` MAJOR is FIXED. **S3 below remains** (return-direction / nested-iface /
sibling dispatchers — those cases still crash under the VM, a documented gap).

- **S1 — native dispatch → handle-based (behavior-preserving refactor).** Make
  `@__ivt` method slots handles (`useHandles=true`; unify with `@__ivtshim`), and
  change iface method-call codegen — LLVM `emit_iface_call.bn`, native
  `aarch64_iface.bn` / x64 / arm32 — to dispatch `handle.vtable.call(handle.data,
  receiver, args)` (mirrors `emitCallHandle`; preserve sret / multi-return /
  agg-coercion). Native-to-native behavior identical; full conformance stays
  green. Retire the now-redundant `@__ivtshim` if clean.
- **S2 — bytecode impls cross to native.** VM builds a native handle-based vtable
  per bytecode `IfaceVtables` entry (slot 0 = dtor handle w/ `VMClosureRec{vm,
  Methods[0]}` or null; slot 1 = TypeInfo; method slots = handles w/
  `VMClosureRec{vm, Methods[k]}`), and substitutes its pointer for the vtable word
  at the marshalling choke point (`dispatchExternBinding`) for interface-typed
  args. Needs per-arg iface-kind metadata (thread from lowering or parse the
  extern Sig). Repro fixed; conformance green.
- **S3 — full generality.** Return-direction (native fn returning an @iface),
  iface nested in struct args/returns, and the sibling dispatchers
  (`dispatchCompiledFuncValue`, `dispatchCompiledIfaceMethod`). Tests: conformance
  (builder-comp-int) for the repro + chain-walk + signal variants; a
  `pkg/std/errors` unit test classifying a user error type. Unblocks the
  binate/examples `errors` both-modes example.

## Risk

S1 is the wide blast radius (all iface dispatch, all backends) but is a
behavior-preserving refactor gated by the full conformance suite. S2/S3 are
localized to the VM. Land S1 fully green before S2.
