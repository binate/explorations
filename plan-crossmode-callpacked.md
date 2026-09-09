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

## Open questions for the adversarial review

- **%BnVtable sharing**: is the 2-slot `{i8*,i8*}` truly func-value-only, or do
  iface vtables / handle blocks reuse it?  Growing a shared struct is the top
  hazard.
- **Native→VM func value (case, not a red test)**: a COMPILED caller reaching a
  VM function value uses slot 1 (the existing scalar-only trampoline).  This
  design does NOT change that path (still scalar-only).  Acceptable? (It is a
  pre-existing limitation, not a regression, and not exercised by the 17 tests.)
- **retbuf sizing / the 64-byte cross-mode window** (TrampolineAggregate's
  ResultRetbufBytes>64 fail-loud): the packed trampoline inherits it; fine?
- **iface-arg substitution in place**: mutating `regs[Src2+i]` before the call —
  any aliasing / re-entrancy hazard vs. the current a0..a6-local substitution?
- **The packed trampoline is ONE generic function** but the native `__shimP` is
  PER-SIGNATURE.  Is the asymmetry (generic VM trampoline vs per-signature native
  shim) sound?  (It mirrors today: generic trampoline vs per-signature `__shim`.)
- **Float/narrow/aggregate re-marshalling from the packed array** in `__shimP`:
  is reading int-slots and re-spreading to the real ABI equivalent to the
  existing `__shim`'s incoming-register reading, or are there slot-vs-register
  representation mismatches (e.g. a float32 slot holds 4 or 8 bytes; an aggregate
  slot holds an address vs inline)?
- **Fault unwind**: `call_packed` shims are called inline from the VM dispatch
  (like the current `_call_shim_*` BLR) — same call-site pad, no extra frame?
  Confirm the packed trampoline / native shim don't introduce an unpadded frame
  the cross-mode unwind can't traverse.

## Status

- 2026-09-09: owner picked design (B) (call_packed second slot) over (A)
  (uniform-packed).  Awaiting adversarial review of THIS design before
  implementation.  17 case-B tests xfail'd on main (aacd1f232) meanwhile.
