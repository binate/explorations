# Plan: any-SHAPE cross-mode func-value dispatch via a `call_packed` vtable slot

Owner-approved design (2026-09-09) for the callee-side any-SHAPE fix — the real
correctness restoration for the 17 cross-mode func-value tests b1 (98219ab3e)
regressed in the VM modes (xfail'd by aacd1f232; enumerated in claude-todo.md).
Supersedes the any-arity-only framing in `plan-crossmode-anyarity-shim.md` (kept
as background: the two-cap analysis and the fault-unwind reasoning there still
hold).  This design also subsumes "item 1" (the case-A native-closure→VM packed
shim) — it falls out as the native producer's `call_packed`.

## The problem (recap)

A function value is `{vtable@0, data@1}`; its vtable is `%BnVtable = {dtor@0,
call@1}`.  `vtable.call` is the ONE dispatch entry, used blindly by every caller.

- A COMPILED caller calls `vtable.call` with args in the callee's REAL ABI
  (floats in FP regs, aggregates by-ref/value, narrow natural-size, spill past
  the reg budget) — it knows the func value's static type, so it is per-signature
  and fast.
- The VM caller (`dispatchCompiledFuncValue`, vm_exec_funcref.bn) is GENERIC — it
  holds args as a uniform int-slot window and today spreads a0..a6 into GP regs
  via `rt._call_shim_scalar/64/aggregate`.  That boundary is scalar-only and
  caps at 7 user words, so it cannot carry FP floats, natural-size narrow args,
  by-retbuf aggregates / big multiret, or >7 spilled words.

b1 routed EVERY func-value call (including bytecode→bytecode, wholly in the VM)
through `vtable.call`, so all those shapes now break in the VM modes.  Pre-b1 a
bytecode→bytecode call dispatched inline (execFunc, uniform int-slots) and handled
them all.

Because `vtable.call` is dispatched blindly, the VM caller cannot pass one thing
to native callees and another to VM callees without a data-peek or thunk-identity
(b2) — both off the table (owner: b2 is an OPTIMIZATION, never correctness).

## The design: a second vtable slot `call_packed`

Grow the func-value vtable to THREE slots:

    %BnVtable = { i8* dtor, i8* call, i8* call_packed }   // dtor@0, call@1, call_packed@2

- `call` (slot 1) is UNCHANGED — the real-ABI, per-signature entry.  COMPILED↔
  COMPILED func-value calls keep using it: a clean, direct register call, no VM
  tax.  (This is the whole point of choosing this over a uniform-packed ABI —
  compiled code pays nothing for the VM's needs.)
- `call_packed` (slot 2) is NEW — a uniform PACKED-int-slot entry.  The **VM
  caller uses `call_packed` UNCONDITIONALLY** for every func-value dispatch
  (native or VM callee alike), so no data-peek / thunk-identity is needed.

### `call_packed` ABI (one uniform signature for all func values)

    call_packed(data *uint8, args *int, nSlots int, retbuf *uint8) int64

- `data`  — the func value's data word verbatim (native env / VM record / null).
- `args`  — pointer to the packed user args as int-slots.  The VM already holds a
  call's args as a CONTIGUOUS int-slot window at `&regs[Src2]` (aggregates ride
  ONE by-address slot; a 64-bit scalar rides 2 slots on an ILP32 host; a float
  rides its bit pattern in a slot), so this is ZERO-COPY — pass `&regs[Src2]`.
- `nSlots` — the packed slot count (`funcValueImmSlots(instr.Imm)` today).
- `retbuf` — result buffer for an AGGREGATE / multi-return result (caller-
  allocated, natural-image-sized, as the aggregate shim/trampoline does today);
  `null` for a scalar/void return.
- return — the SCALAR result as `int64` bits (float returns ride their bits;
  ILP32 64-bit scalar is the r0:r1 pair the VM splits via storeScalar64Result);
  ignored when `retbuf != null`.

This ONE shape carries every return kind (scalar via the i64 return, aggregate/
multiret via retbuf), so — unlike today's Scalar/Scalar64/Aggregate trio selected
by return shape — there is a SINGLE `call_packed` entry per func value.

### Producers (each func value's `call_packed`)

1. **VM function value** — a GENERIC packed trampoline (replaces the role of
   TrampolineScalar/64/Aggregate on this slot).  Reads the closure record from
   `data` (fnIdx, captures), builds the execFunc argv = captures (via
   CaptureOffsets/ByPtr/Wide, as closureArgv does) followed by `args[0..nSlots)`,
   runs `execFunc`, then writes the result to `retbuf` (aggregate) or returns it
   (scalar).  Any shape, because everything is int-slots and execFunc already
   handles arbitrary arity.  This greens all 17 case-B tests.
2. **Native non-capturing func value** — a per-signature `__shimP.<mangled>`
   generated alongside the existing `__shim.<mangled>`: reads `args[0..nSlots)`
   as int-slots and RE-MARSHALS each into the real callee ABI (int slot → GP reg;
   float slot bits → FP reg via fmov; a 64-bit-scalar 2-slot → reg/pair; an
   aggregate by-address slot → load N words to GP regs/stack/by-ref per ABI),
   spilling past the reg budget; then calls the real function; adapts its return
   to the `call_packed` shape (fmov FP→GP for a float scalar return; write retbuf
   for an aggregate).  Per-signature because the shim knows every param/return
   type at compile time — the SAME knowledge the existing `__shim` uses, just
   sourcing args from the packed array instead of incoming registers.
3. **Native capturing closure** — per-closure `call_packed`: reads captures from
   `data` (the env struct) + `args[0..nSlots)`, re-marshals as (2), calls the
   lifted body.
4. **LLVM** — the codegen equivalents of (2)/(3): an LLVM `call_packed` shim that
   loads args from the packed array and calls the real function; float/aggregate
   handled by LLVM's own lowering of the re-marshalling.

### Consumer (the VM caller)

`dispatchCompiledFuncValue` (vm_exec_funcref.bn):
- read `call_packed` from `vtable[2]`;
- do the existing cross-mode iface-arg substitution IN PLACE on the reg window
  (`regs[Src2+i]` for each layout slot) — same substArgSlotIface swap as today,
  just applied to the window instead of a0..a6;
- allocate `retbuf` on vm.Stack iff the return is aggregate/multiret (as today);
- call `call_packed(data, &regs[Src2], nSlots, retbuf)`;
- store the scalar i64 result (narrow / split as today) or the retbuf address.
- DROP the a0..a6 gather, the `_call_shim_*` calls, AND the `nArgs > 7` vmPanic.

The old `vtable.call` (slot 1) is now UNUSED by the VM caller (it stays for
compiled callers).  The generic trampolines (TrampolineScalar/64/Aggregate) that
occupied slot 1 for VM func values are replaced on the VM-dispatch path by the
packed trampoline on slot 2 — but slot 1 must still be populated for a COMPILED
caller reaching a VM func value (case: native code calls a VM function value).
See "Open questions" — slot 1 for a VM func value keeps its current trampoline
(the pre-existing scalar-only limitation on THAT path is unchanged by this work).

## Sites (blast radius)

- **Vtable type + slot index**: `%BnVtable` in codegen emit.bn (`{i8*,i8*}` →
  `{i8*,i8*,i8*}`); a new `types.FuncValueVtableCallPackedIndex() = 2` (alongside
  FuncValueVtableIndex/DataIndex in types/layout_offsets.bn).  **MUST verify
  %BnVtable is func-value-specific and NOT shared with iface `@__ivt` vtables**
  (which have variable method slots) — if shared, growing it shifts iface method
  slots (a serious hazard).  Grep every `%BnVtable` / vtable-slot writer.
- **Native vtable emitters** (the `@__vt.<mangled>` builders) — emit the 3rd slot
  pointing at `__shimP`.  aarch64 / x64 / arm32 shim generators + codegen
  emit_funcvals*.bn.
- **VM `ensureHandle`** (vm_funcvalue_handle.bn) — populate the VM func value's
  vtable slot 2 with the packed trampoline's native address (from Externs), and
  keep slot 1 as the existing trampoline (for compiled callers).
- **VM caller** (vm_exec_funcref.bn dispatchCompiledFuncValue) — switch to
  call_packed; drop `_call_shim_*` + the >7 guard.
- **rt.bni** — the packed trampoline is a real `pkg/binate/vm` function (like the
  existing trampolines), registered as an extern; NO new rt intrinsic and NO
  gen_call recognizer / new IR op needed (this DROPS the OP_CALL_INDIRECT_PACKED
  idea from the anyarity plan — call_packed is a real per-signature shim / real
  trampoline, not IR-magic).
- **Remove** the 17 `*.xfail.builder-comp*-int` markers once green.

## Sequencing (each commit green)

1. Grow `%BnVtable` to 3 slots + `FuncValueVtableCallPackedIndex()`; every vtable
   emitter writes slot 2 = the func value's existing `call` shim as a TEMPORARY
   placeholder (so nothing is null; behavior unchanged — VM caller still uses the
   old path).  Verify %BnVtable is not shared with iface vtables FIRST.
2. Native non-capturing + capturing `__shimP` (per-signature packed shims), and
   the LLVM equivalents; point slot 2 at them.  Unit-test the shims.  Still
   unused by the VM caller → green.
3. VM packed trampoline; `ensureHandle` points a VM func value's slot 2 at it.
   Unit-test it (extend vm_trampoline_test.bn).  Still unused → green.
4. Switch `dispatchCompiledFuncValue` to `call_packed`; drop `_call_shim_*` + the
   >7 guard; remove the 17 xfails.  Now any-shape works end-to-end.
5. Full VM-mode conformance (the 17 un-xfail'd) + e2e/xmclosure.sh + xmfuncvalue.sh
   + all three native backends' + codegen unit tests; every changed package.

Stage 4 is the only behavior change; 1–3 build machinery that lands unused (audit
bnlint for unused-func on the new shims — they ARE referenced by the vtable
emitters, so not unused).  Consider folding 1–4 into fewer commits if the unused
interim is objectionable.

## Adversarial review (2026-09-09): SOUND-WITH-FIXES — answers + corrections

A full adversarial review verified the design against the code.  Verdict:
SOUND-WITH-FIXES — the core mechanism is correct and will green the 17 tests; the
top hazard is a false alarm; three refinements (C1–C3) fold in below.

**Cleared (verified):**
- **%BnVtable growth is SAFE.** `%BnVtable = {i8*,i8*}` (emit.bn:116) is
  func-value-only.  Iface vtables (`@__ivt`) are NOT %BnVtable — they are raw
  `i8*` arrays `[dtor, *TypeInfo, method0…]` (emit_impls.bn:206-212,295) indexed
  as method-HANDLE pointers (emit_iface_call.bn:85-96); each handle is a
  %BnFuncValue whose own %BnVtable is read only for slot 1.  Growing %BnVtable
  enlarges the pointed-to `@__vt` objects but shifts NO iface method slot.  No
  `[N x %BnVtable]` / sizeof-%BnVtable idiom anywhere — every use is a slot GEP
  (`i32 0, i32 n`), stable under append.  VM side: `VMFuncVtable {Dtor,Call}`
  (vm.bni:522) is a managed struct built by `make(VMFuncVtable)`
  (vm_funcvalue_handle.bn:52) — add `CallPacked int` at offset 2, no fixed-size
  block, the func VALUE stays 2 words.
- **Uniform `call_packed(...) int64` return shape is sufficient** for void /
  scalar / float-scalar (bits) / ILP32 64-bit scalar (r0:r1) / aggregate+multiret
  (retbuf).  Only the pre-existing 64-byte cross-mode window limit is inherited.
- **`__shimP` re-marshalling is equivalent to the existing `__shim`**: the
  existing shim ALREADY consumes a uniform all-int dispatch ABI (float bits in GP,
  by-address aggregates, spill) and re-marshals to the real ABI; `__shimP` is that
  same shim's SPILL/FRAME path with args sourced from the array instead of
  incoming regs.  VM packing matches (float=1 slot bits LP64; narrow=1 slot;
  int64=2 slots lo,hi on ILP32; aggregate=1 by-address slot — lower_slots.bn:93,
  lower_call.bn:173).  NB the 17 tests are case B (wholly-VM) → they hit the
  packed TRAMPOLINE (pure int-slots→execFunc, no FP marshalling), NOT `__shimP`.
- **Fault unwind is fine**: cross-mode faults propagate via the `vm.FaultRaised`
  flag + normal return through the VM's own recursion (vm_exec_helpers.bn:14-30,
  vm_exec.bn:180-260 over VMFunc.FaultTable); native frames (`__shimP`, the
  BLR vehicle, the trampoline) are TRANSPARENT.  The existing `__shim` already
  uses a real frame for aggregate/float/spill returns, so `__shimP`-with-a-frame
  is not a new hazard.

**C1 — invocation mechanism (must specify): REUSE `_call_shim_scalar64` as the
vehicle; the shim intrinsics are NOT removed.** `call_packed` is a raw address in
`vtable[2]`; invoking it needs an indirect-call intrinsic.  Reuse the EXISTING
`_call_shim_scalar64(fn, data, a0..a6) int64` (rt.bni:96): call
`_call_shim_scalar64(callPackedPtr, data, argsPtr, nSlots, retbuf, 0, 0, 0)`.
`call_packed` has fixed arity 4, so `data→X0, argsPtr→X1(a0), nSlots→X2(a1),
retbuf→X3(a2)` lands correctly on AAPCS64 / AAPCS32 (R0-R3) / SysV-x64; the i64
return carries scalar bits or is ignored (retbuf case).  So: DO NOT remove the
`_call_shim_*` intrinsics — `_call_shim_scalar64` becomes the invocation vehicle;
`_call_shim_scalar`/`_aggregate` stay for the other consumers (C2).  The earlier
"drop the `_call_shim_*` calls" wording is corrected to: drop only the
scalar/aggregate DIRECT calls in `dispatchCompiledFuncValue`.  Upside: ONE
invocation shape now covers scalar+aggregate+void.

**C2 — two more cross-mode dispatchers share the scalar-only/≤7 limitation
(SCOPE OUT as a tracked follow-up):** `dispatchExternBinding`/`execExternCall`
(vm_extern.bn:38,64-71; >7 guard) and `dispatchCompiledIfaceMethod`
(call_iface_host.bn:78; >6 guard, receiver takes a0).  These are PRE-EXISTING
limitations (NOT b1 regressions, NOT among the 17), so this work leaves them as-is
— but their vtables grow the `call_packed` slot too (all via
`irdata.BuildFuncValue`), so the machinery is present and they can be migrated
later.  Filed as a follow-up so the asymmetry (func-value cross-mode is any-shape;
iface-method/extern cross-mode still vmPanics on shape) is tracked, not silent.

**C3 — blast radius re-anchored.** The vtable-DATA change is ONE shared builder:
`irdata.BuildFuncValue` (irdata/data_funcval.bn:22-46) appends the terms; all four
backends lower it (emit_funcvals.bn:246, x64_funcvalue_vtables.bn:48,
arm32_funcvalue.bn:243, aarch64_funcvalue.bn:282) + the dtor triple
(emit_funcvals_dtor.bn:192).  So the data change = one `appendTerm` in
BuildFuncValue + grow %BnVtable (emit.bn:116) + grow VMFuncVtable (vm.bni:522) +
each backend's gather resolves/passes the `__shimP` symbol (4 small sites).  The
GENUINELY substantial work is the per-signature `__shimP` MACHINE-CODE GENERATORS
(aa64/x64/arm32 + the LLVM emit_funcvals_shim.bn path) — the existing shim's
spill/frame path reading from the packed array.

**Missed sites (add to the list):**
- `pkg/binate/vm/vm_extern.bn` + `pkg/binate/vm/call_iface_host.bn` — C2 (scoped
  out now; tracked follow-up).
- **Extern registration**: register `pkg/binate/vm.TrampolinePacked` in
  `RegisterStandardExterns` (pkg/binate/interp/externs.bn) so `ensureHandle` can
  resolve it.
- `pkg/binate/vm.bni:522` — `VMFuncVtable` gains `CallPacked int`.
- The invocation vehicle (C1) — reuse `_call_shim_scalar64`, with a comment.

**Minor:** slot 2 needs NO per-return-shape selection — ONE universal
`TrampolinePacked(data, args, nSlots, retbuf) int64` serves every VM func value
(retbuf handles aggregate/multiret, i64 handles scalar); slot 1 keeps its
Scalar/64/Aggregate selection for compiled callers.  In-place `regs[Src2+i]`
iface substitution is fine provided the arg-window slots are dedicated packed-arg
temporaries (contiguous callArgBase region — confirm during impl).  Native→VM
func value with a float arg via slot 1 stays a pre-existing dormant limitation
(not in the 17).

## Corrected sequencing (each commit green)

1. Grow `%BnVtable` (emit.bn) + `VMFuncVtable` (vm.bni) to 3 slots +
   `types.FuncValueVtableCallPackedIndex()=2`; `BuildFuncValue` appends slot 2 =
   the func value's existing `call` shim as a TEMPORARY placeholder; the 4 backend
   gathers pass it through; `ensureHandle` sets slot 2 = slot 1's trampoline for
   now.  Behavior unchanged (VM caller still uses the old path) → green.
2. VM packed trampoline `TrampolinePacked` + register it in
   `RegisterStandardExterns`; `ensureHandle` points slot 2 at it.  Unit-test
   (extend vm_trampoline_test.bn).  Still unused by the VM caller → green.
3. Native (aa64/x64/arm32) + LLVM per-signature `__shimP` generators; point slot 2
   at them for native/LLVM func values.  Unit-test the shims.  Still unused →
   green.  (This is the case-A capability = old "item 1", now folded in.)
4. Switch `dispatchCompiledFuncValue` to invoke slot 2 via
   `_call_shim_scalar64(callPacked, data, &regs[Src2], nSlots, retbuf, 0,0,0)`
   (in-place iface substitution on the reg window first); drop the a0..a6
   scalar/aggregate direct-shim calls + the `>7` vmPanic; remove the 17 xfails.
   Any-shape works end-to-end.
5. Full VM-mode conformance (the 17 un-xfail'd) + e2e/xmclosure.sh +
   xmfuncvalue.sh + all three native backends' + codegen + vm unit tests; every
   changed package (C3 shared files feed all backends — smoke each).

Stage 4 is the only behavior change.  Steps 1–3 land machinery that is unused by
the VM caller but REFERENCED by the vtable emitters (so not bnlint-unused).

## Status

- 2026-09-09: owner picked (B); adversarial review = SOUND-WITH-FIXES (C1–C3
  folded in above).  Ready to implement per the corrected sequencing.  17 case-B
  tests xfail'd on main (aacd1f232) meanwhile.  The extern + iface-method
  cross-mode any-shape migration (C2) is a tracked follow-up (not these 17).
