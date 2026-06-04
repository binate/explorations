# Plan: `OP_CALL_INDIRECT` IR Op

> **Status: COMPLETE (shipped)** — `OP_CALL_INDIRECT` landed and
> `rt.CallDtor` is retired. Foundation for
> `plan-function-values.md` Phase 1 is in place. Kept for design
> rationale.
>
> Resolution of the open questions (originally listed below):
> - **Signature representation**: derived implicitly from
>   `instr.Typ` (return) and `instr.Args[i].Typ` (call args). No
>   explicit signature operand on the op.
> - **Binate-source spelling**: no user-facing builtin. RefDec
>   calls `_call_dtor`, an internal `.bni` declaration whose role
>   is just to give the type-checker a signature to validate
>   against. IR-gen recognizes the symbol and emits
>   `OP_CALL_INDIRECT`.
> - **Type-checker rules**: existing function-decl signature-check
>   path; no new logic.
> - **VM null fn_ptr handling**: caller-checks (RefDec already has
>   `if dtor != nil` before calling).
> - **Cross-mode dtor dispatch**: per-call-site convention as
>   originally proposed — fn_ptr is a real C pointer in compiled
>   mode, a 1-based VM function index in VM mode. Each backend
>   interprets the value the same way as direct calls.

## Why this is its own plan

`rt.CallDtor` retirement was originally tracked as a downstream
consumer of full function values. That framing is wrong: RefDec
doesn't need data-ctx slots, vtable indirection, or any of the
function-value machinery — it just needs to call a function
pointer of a known signature. The right primitive is an
indirect-call IR op.

Once that primitive exists, two things follow:

1. **CallDtor retires immediately** — RefDec uses the IR op
   directly, no function-value machinery needed.
2. **Function values are built on top of it** — the compiled-side
   `call` slot in any vtable is "load the slot, OP_CALL_INDIRECT
   through it." Phase 1 of `plan-function-values.md` doesn't have
   to re-invent indirect dispatch; it consumes this primitive.

Treating it as a stand-alone primitive is the cleaner factoring.

## What `OP_CALL_INDIRECT` does

Conceptually:

```
ret = OP_CALL_INDIRECT[signature](fn_ptr, args...)
```

- `fn_ptr` is a raw function pointer. Untyped at the language
  level.
- `signature` is the call signature. As shipped, it is derived
  implicitly from `instr.Typ` (return) and `instr.Args[i].Typ`
  (call args); there is no explicit signature operand.
- `args...` are the argument operands, in the standard call ABI.
- `ret` is the return value, typed per the signature.

Backends lower the op as follows:

- **LLVM (compiled)**: emits an indirect call through the cast
  `fn_ptr` — `call <ret> %fn(<args>)` with appropriate type
  annotations. Same as a direct call, but the callee is a value
  rather than a symbol.
- **VM (bytecode interpreter)**: `fn_ptr` is interpreted as a 1-
  based VM function index. The VM marshals args from the standard
  call ABI into VM-stack convention, dispatches via
  `execFunc(funcIdx-1, args)`, and marshals the return value back.
- **Native AArch64 / ARM32 backends**: indirect call — load
  `fn_ptr` into a register, `BLR Xn` (AArch64) / `BLX Rn` (ARM32).
  Same machinery these backends already use for direct calls; only
  the call target changes.

The op is **not** a Binate-language-level construct (you don't
write `OP_CALL_INDIRECT` in Binate source). It's introduced at
the IR layer. The way Binate code gets at it is through specific
points where the compiler emits it — initially just RefDec's
dtor dispatch, later the per-function-value vtable shim, later
still as part of the language's generic indirect-call story
(once function values land at the frontend).

## How RefDec consumes it

RefDec's dtor dispatch is the IR-level indirect call. The
Binate-source spelling is `_call_dtor`, an internal `.bni`
declaration whose only role is to give the type-checker a
signature to validate against; IR-gen recognizes the symbol and
emits `OP_CALL_INDIRECT`.

The VM's `vm_extern.bn` `rt.CallDtor` arm is gone — its VM-
function-index special-case behavior moved into the VM's lowering
of `OP_CALL_INDIRECT`. The compiled `bn_rt__CallDtor` C function
in `runtime/rt_stubs.c` is gone too (it was the last symbol in
that file).

## Free-function-pointer-in-header story

Same op handles the free_fn-in-header bug (claude-todo §
"Free-function pointer in managed-allocation header — bug"). When
that bug is fixed, `Free` reads `header[1]` and calls through it
via `OP_CALL_INDIRECT` against signature `func(*uint8)`. The
free_fn fix doesn't depend on this plan, but pairs naturally with
it.

## Cross-mode dtor dispatch

A managed allocation created on the compiled side has a real C
function pointer in its dtor; one created on the VM side has a 1-
based function index. If a value crosses the boundary, the dtor's
interpretation must match its origin. The `OP_CALL_INDIRECT` op is
dispatched per-call-site by the side doing the call, so as long as
both sides agree on the convention "fn_ptr is a real pointer in
compiled mode, a 1-based VM function index in VM mode," cross-mode
drops do the right thing.

## Cross-references

- `plan-function-values.md` — downstream. Phase 1's vtable-
  indirect call sequence is built on `OP_CALL_INDIRECT`.
- `claude-todo.md` § "Free-function pointer in managed-allocation
  header — bug" — second concrete consumer (when the free_fn fix
  lands).
