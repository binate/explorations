# Plan: cross-mode func-value dispatch for ANY arity (remove the 7-arg cap)

Owner-assigned follow-up to the native-closure-into-VM rework (b1, commit
`98219ab3e`).  This is item (1) in claude-todo.md ("Cross-mode func-value dispatch
caps at 7 user args").  Do this BEFORE b2 (item 2).  Goal: cross-mode dispatch of
a function value through its vtable.call thunk must handle ANY number of user
args, so conformance `523_closure_many_user_args` (9 args) and
`524_closure_many_caps_reg_to_stack` pass in the VM modes and their
`.xfail.builder-comp*-int` markers can be REMOVED.

## The current 7-arg mechanism (what caps at 7)

`_call_shim_scalar(fn, data, a0..a6) int` (decl in `ifaces/core/pkg/builtins/rt.bni`,
+ `_call_shim_scalar64` i64-return, `_call_shim_aggregate` retbuf-shaped) is
IR-magic: `pkg/binate/ir/gen_call.bn` (~L276) recognizes the name and lowers it to
`OP_CALL_INDIRECT`, a native indirect call passing `data` + 7 args in registers
(AAPCS: X0=data, X1..X7=a0..a6; the callee reads the first NumParams).  The 7-arg
shape is baked into that fixed-signature indirect call.  The VM's cross-mode
dispatch (`dispatchCompiledFuncValue` in `pkg/binate/vm/vm_exec_funcref.bn`, and
the extern arm in `vm_extern.bn`) calls `_call_shim_*` with a0..a6 and FAILS LOUD
on `nArgs > 7` (the guards at vm_exec_funcref.bn:294 and vm_extern.bn:112).

The native/LLVM COMPILED closure shims (x64/arm32/aarch64 `*_funcvalue_shim.bn`,
`emit_funcvals_shim.bn`) already spill >7 args to the stack — that's why the
COMPILED conformance lanes pass 523/524; only the VM (cross-mode thunk) path caps.

## Design: an "always-pack" packed shim

Add packed-shim intrinsics that take an ARGS ARRAY + count instead of a0..a6:
```
func _call_shim_scalar_packed(fn *uint8, data *uint8, args *int, nArgs int) int
func _call_shim_scalar64_packed(fn *uint8, data *uint8, args *int, nArgs int) int64
func _call_shim_aggregate_packed(fn *uint8, retbuf *uint8, data *uint8, args *int, nArgs int)
```
IR-gen recognizes these names and lowers each to an OP_CALL_INDIRECT variant that
does a RUNTIME-arity spread of `args[0..nArgs)` into the callee ABI, then calls
`fn(data, args...)`:
  - the first K args (K = ABI GP-arg-reg count MINUS 1 for `data`; K=7 on AAPCS64,
    K=5 on SysV-x64, K=3 on AAPCS32 [R0=data,R1..R3]) go in arg registers via an
    UNROLLED load sequence (args[0]->reg1, ..., but guarded so a load past nArgs
    is skipped or reads a harmless slot — the callee only reads NumParams);
  - args[K..nArgs) go on the OUTGOING STACK via a RUNTIME LOOP (store args[i] ->
    [sp + (i-K)*wordbytes]); reserve (nArgs-K) stack words when nArgs>K;
  - then the indirect call to `fn`.
The callee reads data (reg0) + its NumParams from reg1.. then the stack — matching
a normal call.  Returns the scalar / i64 / (via retbuf) result.

### KEY DESIGN FINDING (investigated 2026-09-08, before compaction)

The existing `_call_shim_scalar(fn, data, a0..a6)` lowers (gen_call.bn ~L276) to
`EmitCallIndirect(fnPtr, [data, a0..a6], resultTyp)` = `OP_CALL_INDIRECT` with a
**compile-time-fixed** 8-element arg list.  A backend emits OP_CALL_INDIRECT by
spreading that FIXED list into regs+stack — so it cannot express a RUNTIME arity.
The 7-arg cap is thus baked into the shim SIGNATURE (a0..a6) + the fixed
OP_CALL_INDIRECT list; you can't just "pass more" through OP_CALL_INDIRECT because
IR-gen builds its arg list at compile time.

The hard part of the packed spread: outgoing STACK args (beyond the ABI reg
budget) for a runtime nArgs need a runtime-sized outgoing area.  The caller's
frame reserves a FIXED outgoing-args area, so a runtime count needs a DYNAMIC
alloca: reserve (nArgs-K)*word bytes at the top of stack (sp = base), store
args[K..nArgs) there, load args[0..K) into arg regs, then the indirect call — the
callee then reads its stack args at [sp+0..].  This alloca-then-spread-then-call
is awkward in IR/Binate and is most naturally a HAND-WRITTEN per-backend asm
helper (like rt's MemZero/MemCopy asm), one per arch (aa64/x64/arm32), plus the
LLVM path (codegen) which also can't do a runtime-arity `call` directly (same
alloca+spread, or inline asm / a musttail trick).

Likely concrete shape: `_call_shim_scalar_packed` etc. become REAL native asm
symbols (not IR-magic) in impls/core/.../rt (arch-gated `#[build]`), each doing
the dynamic spread + `blr fn` / `call fn`.  Reconsider whether IR-magic can still
work via a new OP_CALL_INDIRECT_PACKED(fn, data, argsPtr, nArgs) that the backend
lowers to the same asm pattern inline — cleaner if the backend can emit a dynamic
stack sub + a runtime store loop + the reg loads before the indirect call.  DECIDE
at implementation time by trying the aa64 backend-inline form first; fall back to
hand-asm rt symbols if the backend can't express the dynamic outgoing area.

Alternative considered + rejected by owner: a bigger FIXED cap (a0..a15).
Band-aid — fails at 17 args; the owner wants the general mechanism proven, not a
higher wall.

## Sites to change

1. `ifaces/core/pkg/builtins/rt.bni` — declare the 3 packed shims (+ add to
   `scripts/hygiene/naming.whitelist` like the existing `_call_shim_*`).
2. `pkg/binate/ir/gen_call.bn` (~L276 recognizer, ~L334 multi-return guard) —
   recognize the packed names, lower to the packed indirect call (new op or flag),
   carrying the args-array pointer + nArgs operands.
3. `pkg/binate/ir.bni` / `iropcode` — if a new opcode, add it.
4. Native backends — emit the runtime-arity spread for the packed indirect call:
   - `pkg/binate/native/aarch64/aarch64_call_indirect.bn` (+ `_funcvalue_shim.bn`) — DO THIS FIRST (host, prove it).
   - `pkg/binate/native/x64/x64_call_indirect.bn` / `x64_funcvalue_shim.bn`.
   - `pkg/binate/native/arm32/arm32_call_indirect.bn` / `arm32_funcvalue_marshal.bn`.
5. `pkg/binate/codegen/emit_funcvals_shim.bn` / `emit_funcvals_sig.bn` — the LLVM
   lowering of the packed indirect call (LLVM can emit a variable-arity call via a
   built args vector; or unroll+stack like native).
6. VM: `pkg/binate/vm/vm_exec_funcref.bn` `dispatchCompiledFuncValue` — build the
   args array (it already reads a0..a6 from regs[Src2..]; instead pass regs[Src2..
   Src2+nArgs) as the array) and call the packed shim; REMOVE the `nArgs > 7`
   guard.  Same for the extern arm (`vm_extern.bn:112`) if it shares the path.
   NB: a VM function value dispatched this way still lands in its TRAMPOLINE (its
   vtable.call); the trampoline's `closureArgv` (vm_trampoline.bn) currently reads
   a0..a6 (7 user args) — it must ALSO take the packed args (extend the trampoline
   OR give the trampoline a packed entry).  This is the subtle part: BOTH the shim
   (caller side) AND the trampoline (VM-func-value callee side) cap at 7; both must
   widen.  For a NATIVE closure the callee is its own compiled shim (already spills
   >7), so only the caller-side packed shim is needed there.
7. Remove `conformance/523_closure_many_user_args.xfail.builder-comp*-int` and
   `524_closure_many_caps_reg_to_stack.xfail.builder-comp*-int` (6 files) once VM
   modes pass.

## Sequencing

1. rt.bni decl + naming whitelist + iropcode/ir.bni (if new op).
2. gen_call lowering.
3. aarch64 backend spread (host — prove end-to-end first).
4. VM wiring (dispatchCompiledFuncValue builds the array + packed shim; widen the
   trampoline for >7; drop the >7 guard).
5. Test: `./conformance/run.sh builder-comp-int 523_closure_many_user_args
   524_closure_many_caps_reg_to_stack` must PASS (host = aa64).  Also
   `e2e/xmclosure.sh` still green, `pkg/binate/vm` unit tests green.
6. x64 + arm32 backends + LLVM codegen (the other lanes / cross-compile).
7. Remove the 6 xfail markers; full closure conformance across modes.
8. Minimal adversarial review (owner asked), then propose landing.

## Status

- 2026-09-08: assigned + planned.  Implementation not yet started.  (Context was
  compacted around here; resume from Sequencing step 1.)
