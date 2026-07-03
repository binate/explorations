# Plan: 64-bit scalar arguments across the cross-mode shim on a 32-bit VM host

**Status: design, adversarially reviewed (2026-07-03).** Sub-project of
`plan-vm-64bit-on-32bit.md` (Phase 3 tail). This is the "hard, separate
workstream" that doc flagged: 64-bit scalar *arguments* mis-marshaled through the
func-value / cross-mode `__shim` on ILP32. Discovered as the root cause of
`conformance/133`'s segfault. A 4-dimension adversarial review confirmed the core
mechanism (register-for-register agreement, endianness, float64 handling, LP64
no-op, 7-slot guard, native-sret path) and caught: the completeness miss (SIX
signature sites incl. the two closure shims, not three; a shared `slotTypesFor`
helper is now the ratified mechanism), an implementation-location error (the
native split goes in the arg PREAMBLE, not the arg-list), a corrected root-cause
register trace, and a confirmed **separate** MAJOR return-side bug (below) — all
folded in.

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

The two disagree. `fn` is the indirect-call *target*, not an ABI arg, so the
caller's arg registers are `data`→r0, `a0`(v.lo)→r1, `a1`(v.hi)→r2, `a2`(buf)→r3
(no even-align — there is no `i64` at the call site). The shim, seeing `i64 %a0`,
even-aligns it to **r2:r3** and reads `buf` from the **stack**. So the shim reads
`v` = r2:r3 = `{caller's v.hi, caller's buf}` (both halves already wrong) and
`buf` from a garbage stack slot → `formatUint` dereferences garbage → **segfault**.
The two type strings are simply not forced to match, and on ILP32 they don't.

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
where a 64-bit scalar expands to two `i32` slots." **Adversarial review
(2026-07-03) established there are SIX signature-emitting sites, not three**, and
that the native caller's arg-VALUE list is a further independent site — so the
"stay in lockstep by construction" hope only holds if a single shared helper
feeds them all. **Decision (resolves former open-questions 1 & 2): introduce one
`slotTypesFor(param) -> @[]@[]char` helper** (the ordered list of slot LLVM types
for a param: `["i32","i32"]` for a 64-bit scalar on ILP32, `["i8*"]` for an
aggregate, `["i32"]` for float32, `[llvmType]` otherwise; on LP64 always one
entry) and route every site through it. Enumerate before editing (grep
`shimParamType` / `funcSignatureLLVM` repo-wide). The sites:

- `emitFuncValueShim` (scalar shim def) — `emit_funcvals_shim.bn:54-59`.
- `emitFuncValueShimAggregate` (retbuf shim def) — `emit_funcvals_shim.bn:230-235`.
- **`emitClosureShim` (capturing-closure scalar shim def) —
  `emit_funcvals_closure.bn:64`.**
- **`emitClosureShimAggregate` (capturing-closure retbuf shim def) —
  `emit_funcvals_closure.bn:246`.**
  (Both closure loops call `shimParamType(...)` + `%a<i>` inline and route args
  through the shared `emitShimArgLoads`/`writeShimArgRef`, so a capturing `@func`
  with a 64-bit user arg hits the identical ABI — omitting them re-breaks
  closures on ILP32.)
- `funcSignatureLLVM` (the bitcast type `emit_call_funcvalue` casts
  `vtable.call` to) — `emit_funcvals_sig.bn:162-182`.
- `emitFuncValueArgList` / `emitFuncValueArgPreamble` (the native caller's actual
  arg-VALUE list — a THIRD, independent path that does NOT call
  `shimParamType`/`funcSignatureLLVM`; `emit_call_funcvalue.bn:325-363` /
  `:194-210`). This is why "lockstep by construction" is false without the shared
  helper.

`shimParamType`'s single-string contract stays (it and `shimIntSlotType` still
return the natural single-slot type — the LP64/1-slot answer; a unit test pins
`shimIntSlotType(int64)=="i64"`). The 2-slot EXPANSION lives only in the new
`slotTypesFor` + the emission loops, so the existing contract is untouched.

The per-param body changes (below) MUST stay byte-identical across all four shim
defs:

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

4. **`funcSignatureLLVM` (the shared bitcast type)** produces the slot-expanded
   signature via `slotTypesFor`, so the bitcast type and the four shim defs agree
   *by construction* (they all consume the one helper). This does NOT
   automatically cover the caller's arg-VALUE list — see #5.

5. **`emit_call_funcvalue.bn` native caller: split 64-bit args on ILP32 — in the
   PREAMBLE.** The arg-VALUE list (`emitFuncValueArgList`) emits args inline into
   the `call` operand list; it CANNOT define standalone SSA `trunc`/`lshr` there
   (that would splice instructions into an operand list → malformed LLVM). Follow
   the existing float pattern: emit the split in `emitFuncValueArgPreamble`
   (which already defines `%v<id>.fb<i>` for float bitcasts,
   `emit_call_funcvalue.bn:194-210`) — define `%v<id>.lo<i> = trunc i64 %v to i32`
   and `%v<id>.hi<i> = trunc i64 (lshr i64 %v, 32) to i32` (for float64, `bitcast`
   to `i64` first) — and have `emitFuncValueArgList` only *reference* them as two
   `i32` args. Drive the "how many slots, which types" decision from the SAME
   `slotTypesFor(param)` so this third site cannot drift from the shim def /
   bitcast type. `builder-comp_arm32_linux` stays green because caller and shim
   move together; a drift would be a loud LLVM verifier error (arg count/type
   mismatch), not a silent miscompile.

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

## Return side — a CONFIRMED separate MAJOR bug (out of scope for this arg fix)

A scalar shim returning a 64-bit value would truncate (`_call_shim_scalar`
returns `int` = `i32`), but it never does: a 64-bit *result* is an aggregate
return on ILP32 (`IsAggregateReturn([int64])` = `SizeOf(8) > PointerSize(4)` =
true, `abi_return.bn:83-89`), so it routes through the **retbuf**
(`_call_shim_aggregate`) path. **That path is broken on ILP32** (verified, not
"likely handled"): `dispatchCompiledFuncValue` / `dispatchExternBinding` store
`regs[Dst] = bit_cast(int, retbuf)` — the retbuf ADDRESS in ONE slot
(`vm_exec_funcref.bn:356`, `vm_extern.bn:63`) — while `regWidths` flags a
64-bit-scalar result register **wide** (2 slots) with no retbuf exclusion
(`lower_slots.bn:170`). So `regs[Dst]` gets the retbuf pointer, `regs[Dst+1]` is
stale, and the 8 bytes are never loaded from the retbuf into the register pair; a
downstream wide read gets `{retbuf_addr, garbage}`.

This is **not exercised by conformance/133** (all three format helpers return
`int` — a 1-slot scalar — `bootstrap.bni`), so this arg-side fix genuinely fully
fixes `133` and `println` of unsigned/int64/float. But it IS a latent MAJOR bug
for any cross-mode func value / extern that RETURNS a bare `int64`/`uint64`/
`float64` on ILP32. **Tracked separately in `claude-todo.md`; do NOT fold a
return-side fix into this arg-side change.** The user decides fold-in vs
follow-up. (The fix shape: the aggregate-return dispatch must, when the result
type is a 64-bit *scalar* — as opposed to a genuine multi-word aggregate — load
the retbuf's 8 bytes into `regs[Dst]`/`regs[Dst+1]` instead of storing the
pointer; the wide-flag at `lower_slots.bn:170` and the dispatch must agree.)

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

**Gating caveat (corrected):** the arm32-VM mode `builder-comp_arm32_linux_int`
is run by the **conformance CI job** (wired directly into
`.github/workflows/conformance-tests.yml`'s matrix-gen, commit `19ad5047`) but is
**NON-BLOCKING** (`continue-on-error`) and is **NOT in `scripts/modesets/{all,
basic}`** (those feed unit/perf/xpass and must not carry a conformance-only mode).
So the mode SURFACES the fix's tests on CI but does not GATE until it is flipped
to blocking (which waits until the ILP32 tail is green). Locally it is
triple-emulated and too slow for a full sweep — use the Docker container for the
targeted repros. Do NOT claim "the default suite gates this."

- **Conformance (run under the arm32-VM mode explicitly):**
  `println(uint8/uint16/uint32/uint64)`, `println(int64)` (incl. negative +
  int64-min), `println(float64)`; multiple 64-bit args to one cross-mode call; a
  64-bit arg followed by a slice arg (`formatUint` shape); a capturing `@func`
  closure taking a 64-bit arg (covers the closure-shim sites). `conformance/133`
  back to green. Named existing tripwires to check: the int64-arg + retbuf
  interaction and the float64 print tests already in the suite (grep the suite
  for `int64`/`float64` print/format tests and confirm each greens under the
  arm32-VM mode).
- **Native regression:** `builder-comp_arm32_linux` func-value suite stays 99/0,
  AND spot-check `builder-comp_arm32_baremetal` (another ILP32 target sharing the
  same codegen shim path, `PointerSize==4`) — arm32-linux alone is insufficient
  since both ride the changed shim gen.
- **Unit (host-runnable):** a codegen test pinning the emitted shim signature +
  reassembly for a `formatUint`-shape (uint64 + slice) AND a float64 shape on a
  `SetTarget`-32 override (like the existing type tests), covering all four shim
  defs and `emit_call_funcvalue`'s preamble split; assert LP64 emits one slot.
- **LP64 no-op:** every 64-bit mode's suite unchanged (expansion gated on
  `PointerSize == 4`).

## Resolved by review (2026-07-03)

1. **Shared `slotTypesFor` helper — YES** (resolves former OQ1). One helper feeds
   all six signature sites + the caller's arg-value preamble; per-slot emission is
   NOT a per-site loop rewrite. Keeps `shimParamType`/`shimIntSlotType`'s
   single-string contract intact (pinned by a unit test).
2. **Other consumers enumerated — the two closure shims + the caller arg-value
   path** (resolves former OQ2). Folded into the change list above (six sites).
3. **Endianness** — little-endian only (arm32-linux/baremetal); no big-endian
   target in scope. `slotTypesFor` order is (lo, hi) matching `joinInt64`.
4. **`%data` prefix** — one pointer slot on both sides; irrelevant to the split.
   Verified register-for-register: post-fix `(i8* data, i32 lo, i32 hi, i8* buf)`
   → data=r0, lo=r1, hi=r2, buf=r3 on both caller and shim. No skew.

## Related scope items (surfaced by review — decide, don't silently defer)

- **Native arm32 assembler backend** (`pkg/binate/native/arm32/arm32_funcvalue.bn`
  ~:278-296) reportedly leaves a func-value 64-bit-arg case as a loud
  "unsupported". This LLVM-path fix does not touch the hand-written arm32
  backend; that backend is reached by `builder-comp_native_arm32_baremetal`, a
  separate mode. Confirm whether it needs a parallel fix or is genuinely
  unreachable for the cross-mode shim (the shim is LLVM-emitted, per the
  ABI map — the arm32 assembler has no `OP_CALL_INDIRECT` case). Likely out of
  scope, but name it.
- **Reverse direction** (`TrampolineScalar`/`TrampolineAggregate` in
  `pkg/binate/vm` — native→bytecode dispatch) and the **cross-mode iface method
  path** (`dispatchCompiledIfaceMethod`) ride the same 7-int slot bank; confirm a
  64-bit arg through those is covered by the same shim fix (it should be — same
  `__shim`), and add a test for each.
