# Plan: 64-bit scalar arguments across the cross-mode shim on a 32-bit VM host

**Status: design (2026-07-03).** Sub-project of `plan-vm-64bit-on-32bit.md`
(Phase 3 tail). This is the "hard, separate workstream" that doc flagged:
64-bit scalar *arguments* mis-marshaled through the func-value / cross-mode
`__shim` on ILP32. Discovered as the root cause of `conformance/133`'s segfault.

## Symptom

On the 32-bit VM host (`builder-comp_arm32_linux_int`), `println` of any value
whose formatter takes a 64-bit first argument **segfaults**:

| `println(x)` | routes to | arm32-VM |
| --- | --- | --- |
| `int` (incl. negative), `bool` | `formatInt(int)` / `formatBool(bool)` — 1-slot arg | ✅ |
| `uint8`/`uint16`/`uint32`/`uint`/`uint64` | `formatUint(uint64, buf)` | 💥 |
| `int64` | `formatInt64(int64, buf)` | 💥 |
| `float`/`float64` | `formatFloat(float64, buf)` | 💥 |

(`uint8` crashes because `emitPrintInt` widens **all** unsigned to `uint64`
before calling `formatUint` — `gen_print.bn:160-164`.) `133` hit it at
`println(s[0][0])` (`s[0][0]` is a `char` → `uint8` → `formatUint`); the slice
indexing was a red herring.

## Root cause (confirmed by disassembly + a 4-reader ABI trace)

The cross-mode call `formatUint(v uint64, buf *[]uint8)` from bytecode goes
VM-dispatcher → `rt._call_shim_scalar(fn, data, a0..a6)` → indirect call → the
per-function `@__shim.formatUint`. Two ends spell the 64-bit arg differently and
only agree on LP64:

- **Caller side** (`rt._call_shim_scalar`): its `a0..a6` params are declared
  `int` (`rt.bni:69`), which is `i32` on ILP32. IR-gen lowers the primitive to
  `OP_CALL_INDIRECT`, and `emitCallIndirect` **reconstructs the indirect-call
  function-pointer type from the operand types** (`emit_call.bn:172-216`) —
  every `aN` is `i32`. So the call type is `...(i8*, i32, i32, i32, ...)`: the
  VM splits the 64-bit `v` into two `i32` slots (`a0=v.lo, a1=v.hi`), and clang
  places them in consecutive registers with **no even-alignment** (there is no
  `i64` at the call site to align). AAPCS32: `fn`→r0, `data`→r1, `a0`(v.lo)→r2,
  `a1`(v.hi)→r3, `a2`(buf)→stack.
- **Callee side** (`@__shim.formatUint`): `shimParamType(uint64)` returns `i64`
  (`emit_funcvals_sig.bn:99-110,195-201`) — **not target-conditioned**. So the
  shim is `define ... @__shim.formatUint(i8* %data, i64 %a0, i8* %a1)`. AAPCS32
  C.3 even-aligns the `i64 %a0`: after `%data` in r0 (ngrn=1, odd), `%a0` pads to
  **r2:r3**, and `%a1` (buf) goes to the **stack**.

The two disagree: the caller put `{v.lo=r2, v.hi=r3, buf=stack0}`; the shim reads
`v` from r2:r3 (= `{v.lo, v.hi}` — coincidentally OK here) and `buf` from a
*different* stack slot (garbage). In the general register skew the halves and
`buf` land in the wrong places; `formatUint` then dereferences a garbage `buf` →
**segfault**. (The exact skew depends on arg count/order; the point is the two
type strings are not forced to match, and on ILP32 they don't.)

**LP64 is invisible to this**: host `int` == `i64`, so a 64-bit scalar is ONE
slot, the reconstructed call type is `...(i8*, i64, i64, ...)`, and `i64`/`i8*`
are each one 8-byte register with no alignment quirk — caller and shim agree.

### Why this can't be fixed on one side alone

The `@__shim` is `vtable.call`, shared by **two** callers:

1. **Bytecode dispatch** (`dispatchCompiledFuncValue`, `dispatchExternBinding`,
   and the handle/dtor site) → `rt._call_shim_scalar` → all-`i32`-slot indirect
   call. **Broken on ILP32.**
2. **Native compiled caller** (`emit_call_funcvalue.bn`): for a `*func`/`@func`
   value call in fully-compiled code, it bitcasts `vtable.call` to
   `funcSignatureLLVM(fvTyp)` and passes a 64-bit arg as `i64`
   (`emit_call_funcvalue.bn:357-360`). The shim spells the param `i64` too, so
   clang even-aligns **both** sides identically. **Consistent — NOT broken**
   (`builder-comp_arm32_linux` is green, 99/0 on the func-value surface).

So the shim's arg ABI must stay consistent with *both* callers. Changing the
shim to read 2 `i32` slots (to satisfy caller 1) would break caller 2 unless
caller 2 also switches to the 2-slot form.

## The fix: a slot-based shim arg ABI on ILP32

Unify on the representation the VM already uses: **on ILP32, a shim argument is
a sequence of `int`-sized (`i32`) slots; a 64-bit scalar (`int64`/`uint64`/
`float64`) occupies TWO slots (low, high, little-endian); the shim reassembles
each 64-bit arg from its two slots before calling the underlying.** Both callers
produce the slot form; the shim consumes it. LP64 is unchanged (a 64-bit scalar
is one `i64` slot, which is what everything already does).

This is exactly analogous to how the VM already models 64-bit values as register
pairs (`splitInt64`/`joinInt64`, `lower_slots.bn:66-80`) and how the native
arm32 backend already even-aligns i64 *direct* calls (`common_callconv.bn:117`)
— we are extending the pair model across the shim boundary for the *indirect*
cross-mode path.

### Concrete changes (all in `pkg/binate/codegen`, ILP32-gated on
`types.GetTarget().PointerSize == 4`)

The unit is "emit a shim param list / a call arg list from a param-type list,
where a 64-bit scalar expands to two `i32` slots." Today three places spell that
list and MUST stay byte-identical (they already share helpers):

1. **`shimParamType` → per-slot expansion (`emit_funcvals_sig.bn`).** Today it
   returns one LLVM type per source param. It can't return two. Restructure the
   shim signature emission so it walks params and, per param, appends the right
   number of slot params:
   - 64-bit scalar int (`int64`/`uint64`) on ILP32 → two `i32` params
     (`%aN_lo, %aN_hi`).
   - `float64` on ILP32 → two `i32` params (int-slotted; reassembled to `i64`
     then `bitcast` to `double`).
   - `float32` → one `i32` (unchanged; already a single int slot).
   - aggregate (slice/mslice/iface/fv/coerced-struct/byval) → one `i8*`
     (unchanged; a pointer is one slot on ILP32).
   - other scalar/pointer → one slot of its natural width (unchanged).
   On LP64, every case is one slot (unchanged output).

2. **`emitShimArgLoads` + `writeShimArgRef` reassembly
   (`emit_funcvals_shim.bn`).** For a 64-bit scalar param on ILP32, emit the
   join in the shim body:
   ```
   %aN.lo64 = zext i32 %aN_lo to i64
   %aN.hi64 = zext i32 %aN_hi to i64
   %aN.hish = shl i64 %aN.hi64, 32
   %aN.v    = or i64 %aN.lo64, %aN.hish        ; the reassembled i64
   ; float64: %aN.f = bitcast i64 %aN.v to double
   ```
   `writeShimArgRef` then references `%aN.v` (or `%aN.f`) instead of `%aN`. The
   underlying call arg type is unchanged (`i64`/`double`). This mirrors the
   existing float32/float64 `bitcast` and coerced-aggregate `load` re-marshaling
   already in `emitShimArgLoads`.

3. **Slot numbering.** The shim's `%aN` naming currently uses the *source arg
   index*. With expansion, downstream slot positions shift. Track a running SLOT
   index while emitting the signature and the underlying-call arg list so a
   64-bit arg consumes two slot positions and the following arg's slots are
   numbered after them. (The `%aN_lo/%aN_hi` SSA names can stay keyed on the
   source arg index `N`; only the positional layout in the signature matters,
   and it's produced by the same loop.)

4. **`funcSignatureLLVM` (the shared bitcast type)** must produce the same
   slot-expanded signature, since `emit_call_funcvalue` bitcasts `vtable.call` to
   it. Because the shim def and this bitcast are generated by the *same* helper,
   they stay in lockstep by construction.

5. **`emit_call_funcvalue.bn` native caller: split 64-bit args on ILP32.** In
   `emitFuncValueArgList`, for a 64-bit scalar arg on ILP32, emit two `i32` args
   (`trunc` the low half, `lshr 32`+`trunc` the high half) instead of one `i64`.
   For `float64`, `bitcast` to `i64` first, then split. This keeps the native
   caller consistent with the now-slot-based shim (and with the bytecode
   dispatcher). `builder-comp_arm32_linux` stays green because caller and shim
   move together.

6. **Aggregate/retbuf shim variant (`emitFuncValueShimAggregate`)** — apply the
   identical per-slot expansion to its param list + reassembly (it shares
   `emitShimArgLoads`/`writeShimArgRef`; only the leading `%retbuf, %data` prefix
   differs). The bytecode aggregate dispatch (`_call_shim_aggregate`) already
   passes `a0..a6` as `int` slots, so it benefits automatically.

### Slot-bank capacity

The 7-slot bank (`a0..a6`) is a real limit; a 64-bit arg now costs 2 slots, so
≤3 64-bit args (or mixes) fit. The dispatchers already guard on SLOT count
(`Imm > 7` / `nArgs > 7`, counting slots) and `vmPanic` loudly past it — no
silent truncation. The format helpers use ≤3 slots. Widening the bank is a
separate follow-up if a real signature needs >7 slots.

## Return side (in scope to confirm, likely already handled)

A scalar shim returning a 64-bit value would truncate symmetrically
(`_call_shim_scalar` returns `int` = `i32` on ILP32). But it never does: a
64-bit *result* is an aggregate return on ILP32 —
`IsAggregateReturn([int64])` = `SizeOf(8) > PointerSize(4)` = true — so it routes
through the **retbuf** (`_call_shim_aggregate`) path, which writes the 8 bytes to
a target-sized buffer. The format helpers return `int` (1 slot, scalar), so this
plan's arg fix fully fixes them. **Design task:** add a test that a func value
returning `int64`/`float64` round-trips through the retbuf shim on ILP32 (verify
the retbuf read reconstructs the register pair), and note it if it's a separate
bug — do NOT fold a return-side fix into this arg-side change silently.

## Alternatives considered (rejected)

- **Change only the shim to slot-based, leave `emit_call_funcvalue` at `i64`.**
  Rejected: breaks the native compiled caller (`builder-comp_arm32_linux`), which
  shares the shim.
- **Reassemble the `i64` in the VM and pass it through `_call_shim_scalar` as a
  real `i64`.** Rejected: the primitive's params are fixed `int` (`i32`), and a
  fixed 7-int primitive can't carry a positionally-varying `i64`; and the
  indirect-call type is reconstructed from the `int` operands, so it can't spell
  `i64` at the varying position.
- **A separate cross-mode-only shim variant with `i32` slots.** Rejected:
  doubles the shim count and the `vtable.call` wiring; the unified slot ABI is
  simpler and the native caller change is small.

## Test plan

- **Conformance (arm32-VM-gated):** `println(uint8/uint16/uint32/uint64)`,
  `println(int64)` (incl. negative + int64-min), `println(float64)`; multiple
  64-bit args to one cross-mode call; a 64-bit arg followed by a slice arg
  (`formatUint` shape). `conformance/133` back to green. All already run by the
  default suite on every mode — the arm32-VM mode is the ILP32 gate.
- **Native regression:** `builder-comp_arm32_linux` func-value suite stays 99/0
  (caller+shim move together).
- **Unit (host-runnable):** a codegen test pinning the emitted `__shim`
  signature + reassembly for `formatUint`-shape on a target32 override (like the
  existing `SetTarget`-based type tests), and for `emit_call_funcvalue`'s split.
- **LP64 no-op:** every 64-bit modes' suite unchanged (the expansion is gated on
  `PointerSize == 4`).

## Open questions for review

1. Is per-slot signature emission cleanest as a rewrite of the shim-signature
   loop, or as a `slotTypesFor(param) -> []string` helper shared by the shim def,
   `funcSignatureLLVM`, and `emit_call_funcvalue`? (Prefer one shared helper so
   the three lists cannot drift — the same discipline that fixed bug A.)
2. Does any *other* consumer of `funcSignatureLLVM` / `shimParamType` exist that
   would be affected (closures `emit_funcvals_closure.bn`, dtor shims)? Enumerate
   before editing.
3. Endianness: the plan assumes little-endian (arm32-linux). Confirm no
   big-endian target is in scope (it isn't today).
4. The `%data`-shifts-registers subtlety (the shim's leading `i8* %data` vs the
   primitive's `fn, data` prefix): confirm the slot model makes the `%data`
   prefix irrelevant to the 64-bit split (it's one pointer slot on both sides).
