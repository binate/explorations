# Plan: 32-bit VM host — cross-mode 64-bit SCALAR returns (both directions)

Status: DESIGN v2 (rewritten around Fix Q after `0479813a`; second adversarial
review pending). Supersedes the v1 retbuf-read-back design, whose premise
(`int64` rides the retbuf shim) `0479813a` removed.

Sibling of `plan-vm-32bit-crossmode-64bit-args.md` (the ARG side, landed as
`a5511a8d` + `83819d60`). Parent: `plan-vm-64bit-on-32bit.md`.

## Symptom (confirmed repro, main @ `0479813a`)

On the 32-bit VM host (`cmd/bni` cross-compiled to arm32-linux, run under
qemu-arm), a bytecode program calling a native-injected function that returns a
bare 64-bit scalar loses the high 32 bits:

```
package "main"
import "pkg/std/math"
func main() {
    println(math.Floor(3.7))       // want 3.000000
    println(math.Float64bits(1.0)) // want 4607182418800017408
}
```

arm32-VM output (baseline, bug): `0.000000` then `0`.
`math.Floor(3.7)` = 3.0 = `0x4008000000000000` (low word 0 → reads as 0.0);
`Float64bits(1.0)` = `0x3FF0000000000000` (low word 0). Both test values have a
zero low word, so the truncation-to-low-word shows as `0`; a value with a
nonzero low word would print its low 32 bits. (Pre-`0479813a` the same repro
printed a retbuf POINTER — the OLD form of this bug, see history below.)

## Root cause

`0479813a` (native-arm32 lane) gated `IsAggregateReturn`/`NeedsSret` on aggregate
KIND, so a bare `int64`/`uint64`/`float64` is now correctly a register-PAIR
SCALAR return, not sret/retbuf (this fixes native test 877). But the cross-mode
VM path was left with a HALF-WIDTH transport on BOTH directions:

### Forward (bytecode → native): the shim returns `i64`, the primitive reads `i32`

`IsAggregateReturn([int64])` is now false, so codegen emits the per-function shim
scalar-shaped, and `writeShimResultLLVM` → `shimIntSlotType` DECLARES an `i64`
return (`emit_funcvals_sig.bn:160,131`). The shim correctly returns the 64-bit
value in r0:r1 (AAPCS32). But the VM dispatch primitive `rt._call_shim_scalar`
is `int`-typed (`rt.bni:69` → i32 on ILP32), so IR-gen lowers the indirect call
as `i32(...)` (`emitCallIndirect` reconstructs the type from the primitive's
result type) — only r0 is read; the high word (r1) is dropped.

### Reverse (native → bytecode): `TrampolineScalar` + `execFunc` both return `int`

A native caller invoking a VM-side function value returning a 64-bit scalar
dispatches through `TrampolineScalar` (NOT `TrampolineAggregate`): `ensureHandle`
(`vm_exec_funcref.bn:172-174`) picks the aggregate trampoline only when
`ResultMultiWord[0] == true`, but that is `isMultiWordField(t) ||
isVMAddressAggregate(t)` (`lower_func.bn:85`), matching only
struct/slice/array/iface/func-value (`lower_instr_helpers.bn:88-128`) — a bare
64-bit scalar matches neither. The compiled caller expects the shim signature
`i64(i8*, args)` (r0:r1), but `TrampolineScalar` returns `int` (`vm.bn:72-95`,
i32) → high word never set. Worse, even its source is truncated: `execFunc`
returns `int` (`vm_exec_helpers.bn:9`), and `execLoop`'s top-level BC_RETURN64
returns only `retVal` (low word), discarding `retHi` (`vm_exec.bn:119-136`).

Both directions reduce to: on ILP32 a 64-bit scalar needs a register PAIR
(2 words), but the VM's cross-mode transport carries only one host word.

## Fix (Fix Q — widen the transport to a pair; symmetric with the arg-side split)

The shim already returns `i64` correctly, so the fix is the VM read/return side.
This keeps 64-bit scalars as register-pair scalars (coherent with `0479813a`),
matching the landed arg-side, which split a 64-bit scalar ARG into two i32 slots.

Two commits, one body of work:

### Commit A — forward direction (bytecode → native)

1. **New IR-magic primitive** `rt._call_shim_scalar64(fn, data, a0..a6) int64`
   (`rt.bni`): identical arg shape to `_call_shim_scalar`, but an `i64` return.
   IR-gen recognizes it alongside `_call_shim_scalar` (`gen_call.bn:313`) →
   `EmitCallIndirect(fnPtr, callArgs, int64)`. `emitCallIndirect` then types the
   native indirect call `i64(i8*, i32×N)`, matching the shim's `i64 @__shim.X` —
   the whole fix on the native path is that the call type now agrees with the
   shim's declared return, so r0:r1 is read.
2. **Flag** (carried in the bytecode instruction, derived at lower time):
   `is64BitScalarReturn(instr.Typ) = is64BitScalar(instr.Typ) && REG_SLOT < 8`
   (the SAME predicate `regWidths` uses to make the result register wide). Packed
   exactly as v1 planned — into bit 0 of the retbuf field (retbuf sizes are
   multiples of 8, so bit 0 is free; for a 64-bit scalar the retbuf size is now 0,
   so the field is just the flag bit): `Aux` for BC_CALL_FUNC_VALUE, the high
   sub-field of `Aux` for BC_CALL_IFACE_METHOD, and `Imm` bit 20
   (`BC_CALL_RET_SCALAR64 = 1 << 20`) for BC_CALL.
3. **Dispatch** — each of the four sites gains a scalar64 sub-case in its SCALAR
   path (the aggregate path is unchanged; a 64-bit scalar now has retbufSize 0,
   so it falls to the scalar path):
   ```
   if retbufSize > 0 {            // genuine aggregate — unchanged
       ... _call_shim_aggregate; store retbuf ptr ...
   } else if scalar64 {           // NEW: 64-bit scalar on ILP32
       var r int64 = rt._call_shim_scalar64(fnPtr, dataPtr, a0..a6)
       var lo, hi = splitInt64(r)
       regs[Dst] = lo; regs[Dst+1] = hi
   } else {                       // one-word scalar — unchanged
       regs[Dst] = rt._call_shim_scalar(fnPtr, dataPtr, a0..a6)
   }
   ```
   Sites: `dispatchCompiledFuncValue` (`vm_exec_funcref.bn`),
   `dispatchCompiledIfaceMethod` (`vm_exec_iface.bn`), the BC_CALL extern arm
   (`vm_exec.bn`), and `dispatchExternBinding` (`vm_extern.bn`).
   - The extern path: `dispatchExternBinding` currently returns `int` and picks
     scalar-vs-aggregate by `b.RetbufSize`. A 64-bit scalar has `RetbufSize == 0`
     → it would call `_call_shim_scalar` (truncate). Fix: `execExtern` decodes the
     scalar64 flag from `instr.Imm` and passes it down; `dispatchExternBinding`
     (widened to return `int64`) calls `_call_shim_scalar64` when scalar64, else
     the existing paths widened to `int64` (a one-word value / retbuf ptr rides
     the low word). The BC_CALL extern arm then `splitInt64`s when scalar64, else
     `cast(int, r)`. `execExtern`/`dispatchExternBinding` have no non-test callers
     besides this arm, so the `int64` widening is contained.
   - `nSlots` masking: BC_CALL's `Imm` flag bit must be masked at all THREE readers
     (`vm_exec.bn` `>7` guard, extern arg-copy, VM-func arg-copy) — an unmasked
     `>7` guard would panic on every 64-bit-scalar extern call.
4. **Nested-VM (double-VM) path** — `dispatchNativeIndirect` (`vm_exec_funcref.bn`)
   handles BC_CALL_INDIRECT when `pkg/binate/vm` itself runs as bytecode. It keys
   the shim shape off the arg-slot count (`Imm==8` scalar / `Imm==9` aggregate) —
   `_call_shim_scalar64` has the same 8-arg shape as `_call_shim_scalar`, so it is
   NOT distinguishable by `Imm` there. Carry the scalar64 flag in
   BC_CALL_INDIRECT's `Aux` (free for that op; set at lower time from
   `instr.Typ == int64`), and branch: `if Imm==8 && scalar64 { _call_shim_scalar64
   + split } else if Imm==8 { _call_shim_scalar } else if Imm==9 { aggregate }`.
   This path is exercised only by double-nested-VM-on-ILP32 (e.g.
   `...int-int` modes); the primary `builder-comp_arm32_linux_int` mode runs
   `pkg/binate/vm` natively and hits the codegen path (step 1) instead. Handled
   for correctness regardless.

### Commit B — reverse direction (native → bytecode)

Review v2 rejected the original "widen `execFunc`/`execLoop` to `int64`" plan: it
has a 16-call-site blast radius (Binate has no implicit `int64→int` narrowing, so
every `var result int = execFunc(...)` breaks) AND collides with `execFunc`'s
value-based copy-back heuristic (`vm_exec_helpers.bn:30-41` treats the result as a
possible stack address). Instead use a **side-field for the high word** — the
VM's "r1":

5. **Add `vm.ReturnHi int`** (`vm.bni`/`vm.bn` VM struct). At `execLoop`'s
   top-level BC_RETURN64 return (`vm_exec.bn:132-136`, the `hdr[0] == -1` branch),
   set `vm.ReturnHi = retHi` before `return retVal` (`retHi` is 0 for a non-pair
   return, so this is unconditional and a no-op for one-word results).
   `execFunc`/`execLoop` keep their `int` return; NO caller sweep, NO copy-back
   change. Single-threaded + read-immediately-after-return makes the side-field
   safe (the top-level return is the last write before the trampoline reads it;
   LIFO nesting preserves it).
6. **New `TrampolineScalar64(data, a0..a6) int64`** (`vm.bn`): mirrors
   `TrampolineScalar` but returns `joinInt64(execFunc(...), vm.ReturnHi)` — the low
   word from `execFunc`, the high word from the side-field. `ensureHandle` selects
   it when the VM func's single result is a 64-bit scalar and `REG_SLOT < 8`. That
   needs a per-func bit `VMFunc.ResultReg64Scalar` (VMFunc field + computed in
   `lowerFunc` as `len(f.Results)==1 && is64BitScalar(types.StripWrappers(
   f.Results[0])) && REG_SLOT < 8`) — `ResultMultiWord[0]` does not distinguish a
   64-bit scalar (it is false for one). **Wrapper peeling MUST use `StripWrappers`
   (alias+readonly+named)**, because `f.Results[0]` is raw (unlike the forward
   `instr.Typ`, which `stripConstForIR` already peeled) and must agree with
   `funcSignatureLLVM`→`writeShimResultLLVM` (which declares the compiled caller's
   `i64(i8*,args)` vtable.call type). A `readonly int64` result that the selector
   left as `is64BitScalar==false` would mis-pick `TrampolineScalar` (i32) against
   an `i64` caller — the review-flagged mismatch.
7. **Register `TrampolineScalar64`** in `extern_register.bn`
   (`RegisterVmTrampolines`) with an `int64`-return `*func(...)` type, AND extend
   `isUniversalTrampoline` (`codegen/emit_funcvals.bn:218`) to recognize it (so it
   is referenced as a raw function address, not wrapped in a data-stripping shim,
   like the other two). `ensureHandle` (`vm_exec_funcref.bn:169-201`) resolves it
   via `vm.LookupExtern`, so registration is mandatory.

`splitInt64`/`joinInt64` (`lower_slots.bn`) are the shared pair primitives.

## Review v2 must-fix checklist (implementation)

- **Unpacking mask (critical).** The existing `if retbufSize > 0` guards read the
  retbuf field RAW: `dispatchCompiledFuncValue` `retbufSize = instr.Aux`
  (`vm_exec_funcref.bn:346`); `dispatchCompiledIfaceMethod` `retbufSize =
  (instr.Aux >> 16) & 65535` (`vm_exec_iface.bn:53`). With the scalar64 flag in
  bit 0 and retbuf now 0 for a scalar, the raw read is `1` → `> 0` TRUE → the
  scalar wrongly takes the AGGREGATE path. Each site MUST strip the flag bit
  before the `> 0` test (`retbufFieldBytes`) and extract it separately
  (`retbufFieldScalar64`). Per-site extraction differs (whole `Aux` for
  func-value; `(Aux>>16)&65535` for iface).
- **`Imm` mask at all three BC_CALL readers** (`vm_exec.bn` `>7` guard, extern
  arg-copy, VM-func arg-copy) — the flag is set on VM-func BC_CALLs too, so the
  VM-func-arm mask is load-bearing, not defensive.
- **Double-VM `dispatchNativeIndirect`**: the scalar64 flag rides
  BC_CALL_INDIRECT's `Aux` (free), derived at lower time with the FULL
  `is64BitScalar(instr.Typ) && REG_SLOT < 8` predicate (NOT `instr.Typ == int64`,
  which drops uint64/float64/named).
- **File-length**: `vm_exec.bn` (494) crosses 500 with both commits — factor the
  scalar64 read-back into a shared `storeScalar64Result(regs, dst, r int64)` (and
  the extern-arm handling into a small helper in `vm_extern.bn`, which has room).
  `vm_exec_iface.bn` (487) is tight; keep the sub-case ≤ a few lines via the
  helper. Put the const/predicate/pack/unpack helpers in a new focused file
  `vm_crossmode_ret64.bn`.
- **`_call_shim_scalar64` needs a `scripts/hygiene/naming.whitelist` entry**
  (siblings of `_call_shim_scalar` at lines 38-39).
- **Tests are BLOCKING and observable**: the reverse `TrampolineScalar64` IS
  directly unit-testable (build a module with an int64-returning func,
  `ensureHandle`, call `TrampolineScalar64(bit_cast(*uint8, callee.ClosureRec),
  ...)`, assert the full int64) — commit to it (with the double-VM skip), do not
  hedge to conformance-only. The forward `runEqDispatch`-style test observes 64
  bits by having the bytecode `main` CHECK the value in-bytecode and return 1/0
  (the harness returns `int`), so Commit A is testable without Commit B. Add a
  pure-predicate test for BOTH `is64BitScalarReturn` and `resultIsReg64Scalar`
  (host-independent, `wordSize`/`REG_SLOT`-parameterized).
- **Verify (implementation-time)**: whether a bare transparent alias `type R =
  int64` reaches shim emission un-resolved. If it does, `is64ScalarUnderlying`
  (`emit_funcvals_sig.bn`) must peel TYP_ALIAS to agree with the forward flag
  (which sees an alias-peeled `instr.Typ`); if the checker resolves it first, add
  a test pinning that and no codegen change is needed.

## Flag derivation is alias/readonly/named-safe

`is64BitScalar(instr.Typ)` sees a representation type with no top-level
alias/readonly, because `newInstr` runs `stripConstForIR` (`ir.bn:135,164`) which
peels TYP_ALIAS + TYP_READONLY before the type lands on `instr.Typ`; only a
residual TYP_NAMED can survive, which `is64BitScalar`/`vmUnwrapNamed` peels. So
`type Nanos int64` and `readonly int64` results are correctly flagged, matching
what `regWidths` did to make the register a pair. (A `type Nanos int64` test case
pins this.)

## Alternatives considered

- **Fix P (re-route 64-bit scalars back through the retbuf, cross-mode only)**:
  give the cross-mode shim shape a predicate distinct from `IsAggregateReturn`
  that treats 64-bit scalars as retbuf-carried again, reusing `_call_shim_aggregate`
  + the v1 read-back. No new primitive, but re-adds a retbuf allocation for every
  64-bit-scalar cross-mode return and DIVERGES the shim-shape decision from
  `0479813a`'s single-source-of-truth. Rejected in favor of Q (register-pair,
  coherent with the arg-side and with `0479813a`). User-selected: Q.
- **Widen `rt._call_shim_scalar` to `int64` universally** (no new primitive):
  would require every scalar shim to also DECLARE an `i64` return and the VM to
  narrow one-word results — a wider blast radius (all scalar dispatch, all shim
  signatures) for no gain. Rejected; the targeted `_call_shim_scalar64` keeps the
  one-word path (`_call_shim_scalar`, i32) exactly as-is.

## Tests

Placement: conformance under `conformance/stdlib/math/` (the `pkg/std/*`-boundary
subtree — no `.rules`/`conformance-imports.whitelist` friction, unlike `spec/`).

- **Conformance** (`conformance/stdlib/math/NNN_return_64bit_crossmode.bn`):
  `math.Floor(3.7)` → `3.000000` (float64), `math.Float64bits(1.0)` →
  `4607182418800017408` (uint64). CRUCIALLY also print a value whose low 32 bits
  are NONZERO — e.g. `math.Float64bits(3.7)` (3.7 = `0x400D99999999999A`, low word
  `0x9999999A` ≠ 0) — so the test also fails a low-word-only truncation, not just
  the zero-low-word cases above (exact decimal computed when writing the test).
  FORWARD direction, extern path (site 3). In
  `builder-comp_arm32_linux_int` this is genuinely cross-mode; that mode is
  NON-BLOCKING (experimental) so the standing protection is the unit tests below.
  Verify FAIL-before / PASS-after in the `binate-arm32` container.
- **Unit — dispatch read-back, FORWARD** (`runEqDispatch`-style, blocking on arm32):
  register a native extern returning `int64`/`uint64`/`float64` (incl. a value with
  a nonzero low word and a `type Nanos int64` NAMED case), dispatch from bytecode,
  assert the FULL 64-bit value. Runs blocking under `builder-comp_arm32_linux`
  (`REG_SLOT == 4`); direct-extern variant (func-value variant is skipped in
  double-VM modes per `vm_extern_coerced_test.bn`).
- **Unit — dispatch, REVERSE** (blocking on arm32): a VM func returning a 64-bit
  scalar invoked via its handle/`TrampolineScalar64`; assert the full value comes
  back. (If a `runEq`-style harness for the native→VM direction is awkward, at
  minimum a conformance case that constructs a `@func()int64` in bytecode, hands it
  to a native-injected higher-order function that calls it, and checks the result.)
- **Unit — pure predicate**: `is64BitScalarReturn(resultTyp, wordSize)` with
  wordSize 4 (set for int64/uint64/float64, clear for int32/ptr/slice/struct) and
  8 (always clear); encode/decode round-trips for the retbuf-field bit-packing and
  the `Imm` high-bit.
- **Unit — lowering** (conditional on `REG_SLOT`, like `TestLowerCastInt64ToUint64`):
  BC_CALL_FUNC_VALUE `Aux`, BC_CALL_IFACE_METHOD `Aux` high field, and BC_CALL
  `Imm` carry the scalar64 bit iff `REG_SLOT < 8` for a 64-bit-scalar return.

## Coordination

`0479813a`'s todo/commit claim that this item is "likely MOOT" is incorrect — it
MOVED the bug (retbuf-pointer → low-word truncation), verified empirically. This
corrected diagnosis is in `claude-todo.md`. The reverse-direction MAJOR is the
same body of work here (user decision). No change to `abi_return.bn` — Fix Q lives
in the VM + the one new `rt.bni` primitive + its IR-gen recognition.

## History

v1 of this doc (commit `bfe8fcc7` and earlier) designed a retbuf read-back,
correct against the pre-`0479813a` tree where `IsAggregateReturn([int64])` was
true and the shim was retbuf-shaped. `0479813a` landed mid-review and removed
that premise; this v2 is the rewrite. The v1 flag-derivation, four-site
enumeration, packing, and test structure carry over unchanged; only the dispatch
ACTION (retbuf read → wide-primitive + split) and the reverse direction are new.
