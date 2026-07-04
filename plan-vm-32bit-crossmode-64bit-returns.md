# Plan: 32-bit VM host — cross-mode 64-bit SCALAR returns (retbuf read-back)

Status: DESIGN — **PREMISE UNDER RE-VERIFICATION.** A concurrent commit
(`0479813a`, native-arm32 lane) landed on `main` after this doc was drafted: it
gates `IsAggregateReturn`/`NeedsSret` on aggregate KIND, so a bare
`int64`/`uint64`/`float64` is **no longer** classified as a retbuf/aggregate
return on ILP32 — it is now a register-PAIR scalar return. That invalidates this
doc's core "rides the retbuf shim" premise. The forward bug may now manifest via
the SCALAR shim path instead (`_call_shim_scalar` returns one host word →
possible high-word loss), or may be fixed — re-verifying empirically on arm32-VM
against current main before implementing. The reverse-direction MAJOR (below) and
the review corrections stand regardless. Adversarially reviewed; corrections
folded in.

Sibling of `plan-vm-32bit-crossmode-64bit-args.md` (the ARG side, landed as
`a5511a8d` + `83819d60`). This is the RETURN side of the same ILP32 cross-mode
boundary. Parent context: `plan-vm-64bit-on-32bit.md`.

## Symptom (confirmed repro)

On the 32-bit VM host (`cmd/bni` cross-compiled to arm32-linux, run under
qemu-arm), a bytecode program that calls a native-injected function returning a
bare 64-bit scalar (`int64` / `uint64` / `float64`) gets garbage:

```
package "main"
import "pkg/std/math"
func main() {
    var r float64 = math.Floor(3.7)      // want 3.000000
    println(r)
    var b uint64 = math.Float64bits(1.0) // want 4607182418800017408
    println(b)
}
```

arm32-VM output (baseline, bug):

```
1083552236*2^-1074          <- retbuf pointer, read as a float mantissa
1083552244                  <- retbuf pointer low word
```

`1083552236` / `1083552244` are a **stack address** (the retbuf), not the value.
The 8 result bytes the shim correctly wrote into the retbuf are never read back
into the destination register pair.

## Root cause

On ILP32 (`PointerSize == 4`), a bare 64-bit scalar result satisfies
`IsAggregateReturn` (`abi_return.bn:88`: `SizeOf 8 > PointerSize 4`), so
`AggregateReturnSize` returns `8` and the per-function shim is **retbuf-shaped**
(`void @__shim.X(retbuf, data, a0..a6)`; it calls the underlying fn — which
returns the i64 in r0:r1 per AAPCS32 — and stores those 8 bytes into `retbuf`).

This retbuf routing is *correct and required*: the scalar-return shim primitive
`rt._call_shim_scalar` returns exactly one host word (4 bytes on ILP32), which
cannot carry a 64-bit value back. So a 64-bit scalar return MUST ride the
retbuf shim. (This is the deliberate asymmetry vs. `IsAggregateArg`, which IS
kind-gated to keep 64-bit scalar ARGS out of the by-pointer path: args have a
2-slot `, i32, i32` split convention; returns have no 2-slot scalar-return
primitive, so they reuse the retbuf.)

The shim **write** side is already correct. The bug is purely the VM
**read-back**: every cross-mode dispatcher that takes the retbuf path stores the
retbuf *address* into the single destination register:

```
if instr.Dst >= 0 { regs[instr.Dst] = bit_cast(int, retbuf) }   // WRONG for a 64-bit scalar
```

For a genuine aggregate (raw slice / managed-slice / struct) that IS the VM
convention — a multi-word aggregate lives on the stack and its register holds a
pointer. But a 64-bit scalar result register was flagged **wide** by
`regWidths` (`lower_slots.bn`, via `is64BitScalar(ins.Typ)`): it occupies a
register **pair** (`regs[Dst]` = low 32 bits, `regs[Dst+1]` = high 32 bits,
little-endian, per `splitInt64`/`joinInt64`). The value must be **loaded** from
the retbuf into that pair, not represented by a pointer. As written,
`regs[Dst]` gets the pointer and `regs[Dst+1]` stays stale.

### Why size alone cannot distinguish the two cases

`RetbufSize == 8` for BOTH a bare `int64` and a raw slice `[]T` on ILP32 (a raw
slice is 2 words = 8 bytes). The dispatcher, seeing only the retbuf size, cannot
tell "load into a register pair" from "store the pointer". The discriminator is
exactly `is64BitScalar(resultType)` — the SAME predicate `regWidths` used to
make the register a pair. It is a function of the result TYPE, not the size, so
it must be stamped where the type is known (IR/VM lowering), then read at
dispatch.

This is ILP32-only: on LP64, `SizeOf 8 == PointerSize 8`, so a 64-bit scalar is
NOT an aggregate return (`RetbufSize == 0`, scalar shim), and it works today.

## The four affected sites

All four take a retbuf-shaped cross-mode return and write the result register:

1. `dispatchCompiledFuncValue` (`vm_exec_funcref.bn:356`) — BC_CALL_FUNC_VALUE,
   compiled-side function value reached from bytecode. Reads `instr.Aux`
   (= RetbufSize).
2. `dispatchCompiledIfaceMethod` (`vm_exec_iface.bn:98`) — BC_CALL_IFACE_METHOD,
   native `@__ivt` interface method. Reads `(instr.Aux >> 16)` (= RetbufSize).
3. `dispatchExternBinding` (`vm_extern.bn:63`) returns the retbuf pointer, and
   the **BC_CALL extern arm** (`vm_exec.bn:216`) writes it into `regs[Dst]`.
   Reads `b.RetbufSize`. **This is the path the repro hits** (a direct call to a
   native-injected `math.*` function: `LookupFunc` misses → extern path).
4. (Not affected) `dispatchNativeIndirect` Imm==9 (`vm_exec_funcref.bn:277`) is
   the raw shim primitive `_call_shim_aggregate`, whose own return is void
   (`instr.Dst == -1`); no user scalar lands here.

The existing `isPair` return machinery for VM-function calls
(`vm_exec.bn:120-184`: a BC_RETURN64 callee sets `retVal`=lo, `retHi`=hi, and
frame-pop writes `regs[callerDst]` + `regs[callerDst+1]`) is the model — but it
only covers VM→VM returns; cross-mode retbuf returns bypass it.

## Design

Derive a **scalar64-return flag** at VM-lowering time from the call's result
type (`is64BitScalar(instr.Typ) && REG_SLOT < 8` — identical to the predicate
`regWidths` uses, so the flag is set exactly when the destination register is a
pair). Carry it in the bytecode instruction. At dispatch, when the flag is set,
LOAD the retbuf's two words into the register pair instead of storing the
pointer.

Nothing about the shim, the reflect payload, `AggregateReturnSize`, the native
backends, or `ExternBinding` changes — the fix is entirely within VM lowering +
VM dispatch, mirroring the existing `is64BitScalar`-driven pair machinery.

### Flag carriage (per site)

Retbuf sizes are always a multiple of 8 (`AggregateReturnSize` rounds to N*8),
so **bit 0 of the retbuf field is free** and is used as the flag wherever the
retbuf size rides the BCInstr:

- **Site 1** (BC_CALL_FUNC_VALUE, `Aux = RetbufSize`): `Aux = RetbufSize | flag`.
- **Site 2** (BC_CALL_IFACE_METHOD, `Aux = slot | (RetbufSize << 16)`): the flag
  is bit 16 (bit 0 of the retbuf field): `Aux = slot | ((RetbufSize|flag) << 16)`.

BC_CALL (site 3) does NOT carry the retbuf size on the BCInstr — the size lives
on the `ExternBinding`, and `Aux`/`Src*`/`Dst` are all occupied or remapped
(`Aux` = name index; `Src1` = callArgBase; both feed `f.Names`/`f.CallCache`/
register remap). Its only spare capacity is the packed **`Imm` slot count**, so
the flag rides a high bit there:

- **Site 3** (BC_CALL, `Imm = slots`): `Imm = slots | BC_CALL_RET_SCALAR64`,
  with `BC_CALL_RET_SCALAR64 = 1 << 20`. Safety is NOT the `callArgs`-buffer
  bound (that guards only the extern arm; the VM-function-call arm's slot count
  is bounded by the callee's `NumParamSlots`, not 64). It is that bit 20 sits
  far above any realistic param-slot count and far below the sign bit even on an
  ILP32 build host (`int` = 32-bit → `1<<20` positive).

### Encoding helpers (centralized)

Because the same encode/decode contract is read at several sites — and the review
flagged scattered masks as the main fragility — the pack/unpack helpers, the
`BC_CALL_RET_SCALAR64` const, the pure lower-time predicate
`is64BitScalarReturn(resultTyp, wordSize)` (= `is64BitScalar(t) && wordSize < 8`),
and the shared dispatch read-back `storeCrossModeRetbufResult(regs, dst, retbuf,
scalar64)` all live in ONE focused new file (`vm_crossmode_ret64.bn`) so producer
and consumer cannot drift and no existing file crosses the 500-line soft cap
(`vm_exec.bn` is already at 494). Encoding is applied uniformly at every
retbuf-stamp site in `lowerCallOp` (including OP_CALL_HANDLE, whose result is
void → flag false).

Two decode subtleties the implementation MUST honor (both flagged by review):
- The retbuf field's flag is bit 0, but the **byte size** used for `vm.SP`
  reservation must have it cleared (`retbufBytes(enc) = enc & ~1`). This applies
  to EVERY size read, not just the branch predicate: `vm_exec_funcref.bn:353`
  and `vm_exec_iface.bn:95` (SP bumps) currently read the field raw.
- The per-site **extraction** differs: site 1's field is the whole `Aux`; site
  2's is `(Aux >> 16) & 65535`. The decode helper is applied to the extracted
  field, not blindly to raw `Aux`.

Flag derivation is alias/readonly/named-safe: `newInstr` runs `stripConstForIR`
(`ir.bn:135,164`), which peels TYP_ALIAS + TYP_READONLY before the type reaches
`instr.Typ`, so only a residual TYP_NAMED can survive — which `is64BitScalar`
(via `vmUnwrapNamed`) peels. So `type Nanos int64` and `readonly int64` results
are correctly flagged, matching what `regWidths` did to make the register wide.

### Dispatch (per site)

Sites 1 & 2 (`dispatchCompiledFuncValue`, `dispatchCompiledIfaceMethod`) already
hold `regs`, `instr`, and `retbuf`; add a branch before the pointer-store:

```
if retbufIsScalar64(<aux field>) {
    var w *int = bit_cast(*int, retbuf)
    if instr.Dst >= 0 {
        regs[instr.Dst]     = w[0]   // low 32 bits
        regs[instr.Dst + 1] = w[1]   // high 32 bits
    }
    return
}
// else: genuine aggregate — store the retbuf pointer (unchanged)
```

The retbuf byte size used for `vm.SP` reservation is decoded with
`retbufBytes(enc)` (clears bit 0) so the flag never perturbs the `vm.SP`
arithmetic.

Site 3: `dispatchExternBinding` is unchanged (it still returns the retbuf
pointer for the aggregate/retbuf path — which for a 64-bit scalar is the retbuf
holding the 8 bytes). The BC_CALL extern arm decodes the flag from `instr.Imm`
and, when set, treats the returned pointer as a retbuf and loads the pair:

```
var nSlots int      = callArgSlots(instr.Imm)      // masks the flag off
var scalar64 bool   = callRetIsScalar64(instr.Imm)
... (use nSlots for the >7 guard and the arg-copy loop) ...
var result int = execExtern(vm, callName, callArgs, instr)
if instr.Dst >= 0 {
    if scalar64 {
        var w *int = bit_cast(*int, result)
        regs[instr.Dst]     = w[0]
        regs[instr.Dst + 1] = w[1]
    } else {
        regs[instr.Dst] = result
    }
}
```

`instr.Imm` has THREE readers inside the BC_CALL handler, ALL of which must use
the masked `nSlots` (compute it once at the top of the handler, before the
extern/VM-func split): `vm_exec.bn:208` (the `> 7` extern guard — if left raw,
`1048576 + slots > 7` panics on EVERY 64-bit-scalar extern call, i.e. the repro
path), `vm_exec.bn:211` (extern arg-copy loop), and `vm_exec.bn:230` (VM-func
arg-copy loop). The VM-func arm ignores the flag itself (a VM→VM 64-bit return
is handled by the callee's BC_RETURN64 + frame-pop `isPair` path) but must not
treat the flag bit as slot count. The func-value dispatchers'
own `instr.Imm` readers (`vm_exec_funcref.bn:319,441`) are unaffected — sites 1
& 2 carry the flag in `Aux`, not `Imm`.

Little-endianness: `w[0]`/`w[1]` are the low/high words in memory order; the VM
pair convention (`joinInt64`) is low-slot-first little-endian; ARM ILP32 is
little-endian — so `regs[Dst]=w[0]`, `regs[Dst+1]=w[1]` is correct for int64,
uint64, AND float64 (the float64 register form is the raw IEEE bits across the
pair — no bitcast needed).

### vm.SP reclamation (minor)

The retbuf is allocated on `vm.Stack` (`vm.SP += 8`) and, once the pair is
loaded into registers, is dead. The existing aggregate path leaves `vm.SP`
bumped until frame pop (the aggregate genuinely lives there); for the scalar64
case the 8 bytes could be reclaimed (`vm.SP -= 8`) since the value is now in
registers. Default: **leave it bumped**, matching the aggregate path exactly
(simplest, lowest-risk; the waste is 8 bytes per cross-mode 64-bit-scalar call,
bounded by frame depth). Reclamation is a possible follow-up; flagged for review.

## Alternatives considered

- **New BCInstr field** (a 7th int, e.g. `RetFlags`): rejected. Grows every
  bytecode instruction by a word and breaks the deliberate "BCInstr is 6 ints,
  pack into Aux" convention the retbuf-size stamping already follows.
- **Flag on `ExternBinding`** (threaded through the reflect FunctionInfo
  payload): rejected. The reflect payload is a cross-backend rodata format
  (LLVM + all three native backends + `reflect.bni`); changing it for a VM-only
  read-back concern is disproportionate. And it would only help site 3 — sites 1
  & 2 still need the per-instruction flag — so it unifies nothing.
- **Widened scalar-return shim** (`rt._call_shim_scalar64` returning i64;
  `IsAggregateReturn` excludes 64-bit scalars on ILP32; codegen emits an
  i64-returning scalar shim): rejected. This is the symmetric analogue of the
  arg-side 2-slot split, but it touches a SHARED ABI contract
  (`IsAggregateReturn`) consumed by the native backends, adds a new runtime
  primitive with dual native/bytecode lowering, and changes codegen shim
  emission — all to replace a retbuf write path that already works correctly.
  The minimal correct fix is the read-back.

## Tests

Placement note (review): the conformance test goes under `conformance/stdlib/math/`
(where every existing `pkg/std/*`-boundary test lives, e.g.
`stdlib/math/001_classify_round.bn`), NOT `conformance/spec/15-builtins/`. The
`spec/` subtree is core-only: a `pkg/std/math` import there would require a
`conformance-imports.whitelist` entry AND a `.rules` sidecar (`spec-coverage`);
the `stdlib/` subtree needs neither. This also sidesteps the `153` numbering
race in `spec/15-builtins/`.

- **Conformance** (`conformance/stdlib/math/NNN_return_64bit_crossmode.bn`): call
  native-injected `math` functions returning 64-bit scalars and print exact
  values — `math.Floor(3.7)` → `3.000000` (float64 read-back),
  `math.Float64bits(1.0)` → `4607182418800017408` (uint64 read-back). (No
  injected stdlib function returns a bare `int64` — only `uint64`/`float64` — so
  the int64 read-back, which shares the identical `w[0]/w[1]` path, is covered by
  the dispatch unit test below with a custom extern.) In
  `builder-comp_arm32_linux_int` this is genuinely cross-mode (bytecode body,
  native math) and exercises the extern path (site 3); in all-native / all-VM
  modes it is an ordinary call (still valid coverage). Note this conformance mode
  is currently NON-BLOCKING (experimental / continue-on-error) — the standing CI
  protection for the read-back comes from the unit tests below, which run
  BLOCKING under `builder-comp_arm32_linux`. Verified to FAIL on arm32-VM before
  the fix and PASS after (in the `binate-arm32` container).
- **Unit — dispatch read-back** (`runEqDispatch`-style, the substantive one): build
  IR → register a native extern returning `int64`/`float64`/`uint64` → LowerModule
  → `execFunc`, and assert the FULL 64-bit value comes back (not a truncated word
  or a pointer). This runs BLOCKING under `builder-comp_arm32_linux` (`REG_SLOT ==
  4`), where it actually exercises the retbuf-pair read-back (`dispatchExternBinding`
  → BC_CALL extern arm). Use the direct-extern variant (the func-value variant is
  skipped in double-VM modes per `vm_extern_coerced_test.bn`). Include a
  `type Nanos int64` (NAMED) case to pin alias/named transparency.
- **Unit — pure predicate** (host-independent): `is64BitScalarReturn(resultTyp,
  wordSize)` tested with `wordSize` 4 (flag set for int64/uint64/float64, clear
  for int32/pointer/slice/struct) and 8 (always clear). Plus encode/decode
  round-trip tests for the retbuf-field bit-packing (`packRetbuf`/`retbufBytes`/
  `retbufScalar64`) and the `Imm` high-bit (`callArgSlots`/`callRetIsScalar64`).
- **Unit — lowering** (conditional on `REG_SLOT` like `TestLowerCastInt64ToUint64`):
  assert BC_CALL_FUNC_VALUE `Aux`, BC_CALL_IFACE_METHOD `Aux` high field, and
  BC_CALL `Imm` carry the scalar64 bit iff `REG_SLOT < 8` for a 64-bit-scalar
  return, and do not for an int32/slice return. On 64-bit CI this verifies the
  flag stays clear; the set path runs BLOCKING when the VM's own unit tests run
  on the arm32 host (`pkg/binate/vm` under `builder-comp_arm32_linux`, `REG_SLOT
  == 4`).

## Related bug — reverse direction (CONFIRMED, raised separately)

The REVERSE direction — a VM function value returning a 64-bit scalar to a
NATIVE caller — is **also broken on ILP32**, by a DIFFERENT mechanism. (This
correction supersedes an earlier draft that wrongly claimed the reverse path
rides `TrampolineAggregate` + retbuf and was therefore fine.)

`ensureHandle` (`vm_exec_funcref.bn:172-174`) picks the aggregate trampoline
only when `ResultMultiWord[0] == true`. That flag is
`isMultiWordField(t) || isVMAddressAggregate(t)` (`lower_func.bn:85`), and BOTH
predicates match only struct / slice / managed-slice / array / iface-value /
func-value (`lower_instr_helpers.bn:88-128`) — a bare `int64`/`uint64`/`float64`
matches NEITHER. So `ResultMultiWord[0] == false`, and `ensureHandle` picks
**`TrampolineScalar`**, whose body returns `execFunc(...)` as a single host
`int` (`vm.bn:72-95`; its own docstring: "no floats, no aggregates pass
through"). On ILP32 that is 4 bytes — the high 32 bits of a 64-bit scalar
result are dropped.

This is the exact mirror of the read-back bug fixed here, and per the
bug-discovery protocol it is raised as its own tracked MAJOR (`claude-todo.md`)
rather than folded into this change silently. Whether to fix it in this same
body of work or as an immediate follow-up is the user's call; the two fixes are
in different code paths (trampoline selection / `execFunc` return width vs. the
dispatcher read-back), though they share the same root (a 64-bit scalar does
not fit one host word on ILP32).

The `dispatchNativeIndirect` Imm==9 arm (`vm_exec_funcref.bn:258`) and
`refDecCrossModeDispatch` (`vm_exec_helpers.bn`) both dispatch VOID returns and
are correctly unaffected — noted here so a future reader does not re-flag them.
