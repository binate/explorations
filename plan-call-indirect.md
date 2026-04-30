# Plan: `OP_CALL_INDIRECT` IR Op

> **Status: LANDED** — `rt.CallDtor` retired. PR 1 (`ee93644`):
> IR op + LLVM lowering. PR 2 parts 1-3 (`6f064a5`, `4e20ffb`,
> `f08ddcb`): VM lowering, native arm64 lowering, RefDec migration.
> Foundation for `plan-function-values.md` Phase 1 is in place.
>
> Resolution of the open questions (see below):
> - **Signature representation**: derived implicitly from
>   `instr.Typ` (return) and `instr.Args[i].Typ` (call args). No
>   explicit signature operand on the op.
> - **Binate-source spelling**: option C from the plan — no
>   user-facing builtin. RefDec calls `_call_dtor`, an internal
>   `.bni` declaration whose role is just to give the type-checker
>   a signature to validate against. IR-gen recognizes the symbol
>   and emits `OP_CALL_INDIRECT`.
> - **Type-checker rules**: existing function-decl signature-check
>   path; no new logic.
> - **VM null fn_ptr handling**: caller-checks (RefDec already has
>   `if dtor != nil` before calling).
> - **Cross-mode dtor dispatch**: per-call-site convention as
>   originally proposed — fn_ptr is a real C pointer in compiled
>   mode, a 1-based VM function index in VM mode. Each backend
>   interprets the value the same way as direct calls.
>
> The plan body below is preserved for historical reference.

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
This plan covers introducing it.

## What `OP_CALL_INDIRECT` does

Conceptually:

```
ret = OP_CALL_INDIRECT[signature](fn_ptr, args...)
```

- `fn_ptr` is a `*uint8` (or whatever raw-function-pointer type
  Binate ends up with — see "Open questions" below). Untyped at
  the language level.
- `signature` is the call signature — encoded in the IR op
  (probably as a `@types.Type` reference to a `func(args) → ret`
  shape, or an explicit args-and-return-type list).
- `args...` are the argument operands, in the standard call ABI.
- `ret` is the return value, typed per the signature.

Backends lower the op as follows:

- **LLVM (compiled)**: emits an indirect call through the cast
  `fn_ptr` — `call <ret> %fn(<args>)` with appropriate type
  annotations. Same as a direct call, but the callee is a value
  rather than a symbol.
- **VM (bytecode interpreter)**: `fn_ptr` is interpreted as a 1-
  based VM function index (matching the existing
  `vm_extern.bn` `rt.CallDtor` arm's special-case). The VM
  marshals args from the standard call ABI into VM-stack
  convention, dispatches via `execFunc(funcIdx-1, args)`, and
  marshals the return value back.
- **(Future) Native AArch64 / ARM32 backends**: indirect call —
  load `fn_ptr` into a register, `BLR Xn` (AArch64) / `BLX Rn`
  (ARM32). Same machinery these backends already use for direct
  calls; only the call target changes.

The op is **not** a Binate-language-level construct (you don't
write `OP_CALL_INDIRECT` in Binate source). It's introduced at
the IR layer. The way Binate code gets at it is through specific
points where the compiler emits it — initially just RefDec's
dtor dispatch, later the per-function-value vtable shim, later
still as part of the language's generic indirect-call story
(once function values land at the frontend).

## How RefDec consumes it

Today, `RefDec` body uses an extern call to `rt.CallDtor`:

```binate
func RefDec(ptr *uint8, dtor *uint8) {
    if ptr == nil { return }
    var h *int = headerPtr(ptr)
    if h[0] <= 0 { BoundsFail(h[0], 0) }
    h[0] = h[0] - 1
    if h[0] == 0 {
        if dtor != nil {
            CallDtor(dtor, ptr)   // extern → bn_rt__CallDtor in C
        }
        Free(ptr)
    }
}
```

After `OP_CALL_INDIRECT` lands, `CallDtor(dtor, ptr)` is replaced
with the IR-level indirect call. The Binate-source-level spelling
TBD — possibilities:

1. **A new builtin** — `call_indirect(dtor, ptr)` — that the type
   checker recognizes and lowers to the IR op. Pure compiler-
   internal mechanism; user code doesn't write this normally.
2. **A typed function-pointer call expression** — `var f
   func(*uint8); f = bit_cast(...); f(ptr)`. Requires at least a
   tiny slice of the function-values frontend, which this plan
   wants to avoid.
3. **Compiler-internal IR-emit only** — RefDec's Binate body
   keeps calling `CallDtor` (as a regular Binate function), but
   IR-gen for that specific symbol emits `OP_CALL_INDIRECT`
   directly. Hacky but minimal.

(1) is probably the right call — clean, generalizes, doesn't
require frontend function-value syntax. Confirm during
implementation.

The VM's `vm_extern.bn` `rt.CallDtor` arm goes away — its VM-
function-index special-case behavior moves into the VM's lowering
of `OP_CALL_INDIRECT`. The compiled `bn_rt__CallDtor` C function
in `runtime/rt_stubs.c` deletes (it was the last symbol in that
file; the file goes away too).

## Implementation scope

### IR

- Define the op (probably `ir.OP_CALL_INDIRECT`, fits the existing
  pkg/ir pattern).
- Operand layout: `Args[0]` = `fn_ptr`, `Args[1..]` = call args,
  `Typ` = signature (or two type refs, one for args / one for
  ret — TBD).
- IR-gen for the recognized builtin / spelling (per the choice
  above).

### Compiled (LLVM) backend

- `pkg/codegen/emit_ops.bn` (or wherever the call ops live) gets
  an arm for `OP_CALL_INDIRECT` that emits an LLVM `call <ret>
  %fn(<args>)`.
- Type-mapping: signature → LLVM call-target type. Reuse existing
  llvmType machinery.

### VM backend

- `pkg/vm/lower.bn` gets a bytecode op (probably
  `BC_CALL_INDIRECT_FN_INDEX`) that takes the same shape.
- `pkg/vm/vm_exec.bn` interpretation: load funcIdx from the
  fn_ptr operand, args from registers, `execFunc(funcIdx-1,
  args)`, return result.
- The VM-function-index convention (1-based; 0 means nil) carries
  over from how dtor pointers are constructed today.

### Native backends (AArch64, future ARM32)

- `pkg/native/arm64/arm64_ops.bn` gets an arm for the op:
  `MOV Xn, fn_ptr; BLR Xn`. Reuses the existing call machinery
  (frame setup, arg registers, return).
- `pkg/native/arm32/...` — same pattern when that backend is
  added.

### Tests

- IR test: `OP_CALL_INDIRECT` round-trips through the IR
  builder.
- LLVM test: emit module with a `OP_CALL_INDIRECT`, check the
  emitted IR has the expected `call %fn(...)` shape.
- VM test: build a module that calls a function indirectly,
  execute, verify result.
- Conformance test: a small program that uses the new builtin (or
  whatever spelling lands) to call a function indirectly. End-to-
  end across compiled and VM modes.
- Migration verification: `rt.CallDtor` retired, `runtime/
  rt_stubs.c` deleted, `vm_extern.bn` arm removed — full
  conformance + unit-test sweep stays green.

## Phasing

This plan is small enough to land in one or two PRs:

1. **PR 1 — IR op + compiled backend**. Define the op, emit
   logic, LLVM lowering, IR / codegen unit tests. Doesn't yet
   retire CallDtor (no Binate source uses the op yet).
2. **PR 2 — VM backend + Binate spelling + CallDtor migration**.
   VM lowering. Pick the Binate-source spelling (probably the
   `call_indirect` builtin). Migrate RefDec to use it. Drop
   `rt.CallDtor`, `runtime/rt_stubs.c`, the vm_extern arm. Update
   `pkg/native/arm64` if any direct emission of
   `bn_rt__CallDtor` was happening (unlikely).

Possibly fold these into one PR if they sequence cleanly.

## Free-function-pointer-in-header story

Same op handles the free_fn-in-header bug (claude-todo §
"Free-function pointer in managed-allocation header — bug"). When
that bug is fixed, `Free` reads `header[1]` and calls through it
via `OP_CALL_INDIRECT` against signature `func(*uint8)`. The
free_fn fix doesn't depend on this plan, but pairs naturally with
it.

## Open questions

- **Signature representation in the IR op.** Single `@types.Type`
  ref to a function-shape type? Two refs (args list + return
  type)? Inline encoded? Pick during implementation; the existing
  IR-op encodings for `OP_CALL` should guide.
- **Binate-source-level spelling.** Builtin (`call_indirect`),
  typed function-pointer call, or compiler-internal-only? Lean
  toward builtin (option 1 above).
- **Type-checker rules for the spelling.** If we go with a
  builtin: how does the type checker verify the signature matches
  the args and return-type expectations? Probably takes the
  signature as a type argument (like other type-builtins) plus the
  fn_ptr and the args, and checks consistency.
- **VM null fn_ptr handling.** Today `RefDec` has `if dtor != nil`
  before the call. Should `OP_CALL_INDIRECT` itself trap on null,
  or rely on the caller's nil-check? Probably caller-checks for
  consistency with the rest of the codebase (`if ptr == nil`
  guards everywhere).
- **Cross-mode dtor dispatch.** A managed allocation created on
  the compiled side has a real C function pointer in its dtor;
  one created on the VM side has a 1-based function index. If a
  value crosses the boundary, the dtor's interpretation must
  match its origin. The `OP_CALL_INDIRECT` op is dispatched per-
  call-site by the side doing the call, so as long as both sides
  agree on the convention "fn_ptr is a real pointer in compiled
  mode, a 1-based VM function index in VM mode," cross-mode drops
  do the right thing. Worth pinning explicitly.

## Cross-references

- `plan-function-values.md` — downstream. Phase 1's vtable-
  indirect call sequence is built on `OP_CALL_INDIRECT`.
- `claude-todo.md` § "Retire `rt.CallDtor`" — first concrete
  consumer.
- `claude-todo.md` § "Free-function pointer in managed-allocation
  header — bug" — second concrete consumer (when the free_fn fix
  lands).
