# Plan: float-argument support for the function-value / extern shim path

Status: **MECHANISM LANDED on main** (`7abc3809`), 2026-06-03 —
Design A. Verified green across all default LLVM modes (full
`builder-comp` 478/0; full `builder-comp-int` clean except the
pre-existing `520` `@Iface`-dtor VM bug; func-value+float subset green in
`builder-comp-comp` / `-comp-int` / `-int-int`) plus codegen + vm unit
tests in `builder-comp` and `builder-comp-int`; hygiene clean. Native
self-host lanes (`builder-comp_native_aa64-...`) could NOT be exercised —
that lane is pre-existing-RED at compiler-build time (`duplicate symbol
predeclaredNil`, see claude-todo), unrelated to this change (which is
LLVM-backend-only). Unblocks the bootstrap native-only work in
[`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md).

## Testing note (discovered during implementation)

The `-int` bug is **only** reachable when a BYTECODE caller invokes a
NATIVE, non-trampoline float callee through `@__shim` — i.e. a registered
float **extern**. A *user* float func-value in `-int` is bytecode (or an
all-int trampoline re-entry), so its float travels as a type-erased
64-bit VM slot and round-trips fine *without* the fix. So the conformance
tests below (562-566) are **compiled-mode guards** for the ABI reshape;
they pass on baseline and do NOT catch the bug. The canonical
bug-reproduction is the **VM unit tests** `pkg/binate/vm`
`TestExternFloat{Arg,Return,32Arg}ViaRegistry`, which register a native
float function as an extern and call it from hand-built bytecode (the
exact shape the bni host hits for `bootstrap.formatFloat`). Confirmed:
those two/three fail on the clean `aae8ea43` baseline and pass after the
change.

## Problem

The per-function shim `@__shim.<mangled>` (emitted by `pkg/binate/codegen/
emit_funcvals*.bn`) is declared with **natural** parameter/return types
(`double` for `float64`, `float` for `float32`) and tail-calls the real
function with those natural types.

- **Compiled** function-value calls (`emit_call.bn:emitCallFuncValue`)
  bitcast the loaded `vtable.call` slot to that natural signature and pass
  natural args → float params/returns **work** in compiled mode.
- **VM dispatch** (`dispatchExternBinding`, `dispatchCompiledFuncValue`,
  `dispatchNativeIndirect`) type-erases every arg to `int` and routes
  through `rt._call_shim_scalar(fn, data, a0..a6 int) int` /
  `rt._call_shim_aggregate(...) void`. These are IR-magic
  (`pkg/binate/ir/gen_call.bn:233`) lowering to an **all-`int`**
  `OP_CALL_INDIRECT`. The native backends place an arg in an FP register
  only when the IR operand type is float (`aarch64_call_indirect.bn:44`,
  `x64_call_indirect.bn:61`); with all-`int` operands the FP path never
  fires. So a float arg's bits land in a GP register while the shim reads
  `d0`/`xmm0` → **broken**. Float **returns** break symmetrically (x64
  reads the return from a GP reg for an `int`-typed magic; aarch64
  indirect has *no* float-return path — it always reads `X0`).

This is a real latent bug, not only a bootstrap blocker: **float
function-values are silently miscompiled in every `-int` (VM) mode
today**, and there is *zero* test coverage for float func-values (so
nothing catches it). The concrete consumer that forces the issue:
`println(float)` → `lang.float64.String` → `bootstrap.formatFloat(float64,
*[]uint8) int`; once `pkg/bootstrap` goes native-only in the VM (the
bootstrap plan), `formatFloat` dispatches as a native extern through this
broken path and `conformance/287_float_println` regresses in `-int`
modes. (Verified: 287 passes `builder-comp-int` today with bootstrap
lowered to bytecode.)

## Design A — uniform all-`int` shim ABI (chosen)

Make every shim signature carry float **scalars** as integer slots; the
shim `bitcast`s `i64↔double` / `i32↔float` at its own boundary (LLVM
bitcast between same-width int and float is exact and free); the compiled
call site bitcasts float args→int and the int return→float to match. Then
the VM's existing all-`int` dispatch works **unchanged** — int bits in a
GP register in, int bits in a GP register out, which is exactly what the
all-`int` IR-magic already emits.

The shim becomes the single place where int↔float register reconciliation
happens, on the compiled side. The VM never has to know an arg/return is a
float. This preserves the "one uniform all-int dispatch shape serves every
function value" invariant the always-shim convention is built on
(`plan-uniform-native-fnptrs.md`).

### Conventions (confirmed)

1. **Scalar-slot scope.** Int-ify only **scalar** float args and the
   **scalar** float return. Aggregate-return retbufs stay **natural-typed**
   — `dispatchExternBinding` copies `ResultSize` bytes verbatim, so a
   `double` field in a retbuf is byte-identical to an `i64` field; no
   change needed for aggregate/multi-return shapes. This keeps the change
   small.
2. **Exact-width slots.** `float64 → i64`, `float32 → i32`. Never widen a
   `float32` into an `i64` slot (avoids any high-bit/zero-fill ambiguity);
   slot width always equals type width.
3. **Single shared predicate.** Both the shim emitters and the call site
   derive int-ification from one helper so they can never disagree — the
   asymmetry case is the only way Design A can silently miscompile (a
   critical-class bug), so it is structurally prevented, not just tested.

### Affected sites

Shared (new) in `emit_funcvals.bn`:
- `shimIntSlotType(t) @[]char` — `i64` for float64, `i32` for float32,
  else the existing natural/`i8*` result. The chokepoint.
- `isFloatScalarParam(t) bool` (or reuse an existing float predicate).

Routed through the chokepoint (no-op for non-floats):
- `shimParamType` (`emit_funcvals.bn:91`) — shim **signature** params.
- scalar branch of `writeFuncResultsLLVM` (`emit_funcvals.bn:107-109`) —
  shim **signature** return. (Multi-return/aggregate retbuf left natural
  per convention 1.)

Shim **bodies** bitcast at the boundary (the underlying call keeps natural
types — need a natural-typed arg/return emitter distinct from the now-int
shim signature):
- `emitFuncValueShim` (`emit_funcvals_shim.bn:40`) — scalar.
- `emitFuncValueShimAggregate` (`emit_funcvals_shim.bn:137`) — float
  *args* only (return retbuf stays natural).
- `emitClosureShim` (`emit_funcvals_closure.bn:37`) — user-arg forward
  (captures are loaded from the closure struct naturally, unaffected).
- The dtor shim (`emit_funcvals_dtor.bn`) is pointer-only — untouched.

Compiled call site:
- `emitCallFuncValue` (`emit_call.bn:223`) + `emitFuncValueArgList`
  (`emit_call.bn:417`): bitcast float args `double→i64` / `float→i32` in
  the call preamble; receive a scalar float return as int and bitcast back
  to natural.

Subtlety: when the shim return is int-ified-from-float, the shim can no
longer `tail call` the natural-`double`-returning real function (a bitcast
must follow the call) — emit a regular `call` + bitcast + `ret` for that
case. Float-arg / int-return functions (e.g. `formatFloat`) keep the tail
call.

### What does NOT change

VM dispatchers, `rt._call_shim_*` signatures, `ExternBinding`,
`RegisterExtern`, and the native backends — all unchanged. Design A is a
pure `pkg/binate/codegen` change.

## Design B — natural shim + float-aware VM dispatch (rejected)

Keep the natural shim (compiled untouched); teach the VM to place floats
in FP registers. Rejected because it needs (a) per-arg/return float
metadata threaded through three dispatchers *and* the bytecode
function-value representation (which carries no type info today), and
(b) **new native codegen** — a runtime-descriptor-driven FP-aware indirect
call plus the missing aarch64 indirect float-return path. The existing
native FP support can't be reused (it's keyed on compile-time operand
types). Strictly more work across strictly more layers, against the
"uniform dispatch" direction.

## Steps

Steps 1–3 are one atomic ABI flip (intermediate states are
type-inconsistent), but each is individually reviewable; land them as one
commit with the tests.

- **Step 0** — add `shimIntSlotType` + `isFloatScalarParam` in
  `emit_funcvals.bn`; unit-test them directly. No behavior change.
- **Step 1** — route `shimParamType` + scalar `writeFuncResultsLLVM`
  through `shimIntSlotType` (shim **signatures** int-ify float scalars).
- **Step 2** — shim **bodies** bitcast `i64↔double` / `i32↔float` at the
  boundary in all three shim emitters; underlying call stays natural;
  float-return shims use `call` (not `tail call`).
- **Step 3** — `emitCallFuncValue` / `emitFuncValueArgList` int-ify float
  args + bitcast the int return back to float. Closes the loop; compiled
  mode green again under the new ABI.
- **Step 4 (proof)** — the canonical repro is the **VM unit tests**
  (`pkg/binate/vm` `TestExternFloat*ViaRegistry`): a bytecode caller
  invokes a native float extern via the registry — fails pre-change,
  passes after (see Testing note above). Plus codegen golden tests
  (int-ified shim + call site) and conformance 562-566 as compiled-mode
  ABI-reshape guards. Validates the mechanism independent of bootstrap.

After this lands, the bootstrap native-only work (`plan-bootstrap-ccall.md`)
is unblocked: registering `bootstrap.formatFloat` as a native extern then
dispatches correctly in `-int`.

## Test plan

New VM unit tests (`pkg/binate/vm`) — the canonical bug repro (bytecode
caller -> native float extern via the registry):
`TestExternFloatArgViaRegistry` (float64 arg), `TestExternFloatReturn-
ViaRegistry` (float64 arg + return), `TestExternFloat32ArgViaRegistry`
(float32 / i32 slot). These FAIL on baseline and pass after the fix.

New conformance tests (run in **all** default modes; compiled-mode
ABI-reshape guards — they pass on baseline, see Testing note):
1. Float-arg func value, scalar return: `var f *func(float64) int = …`.
2. Float-return func value: `var f *func() float64 = …`.
3. **Bit-exact round-trip** through a func value:
   `bit_cast(int64, f(x)) == bit_cast(int64, x)` for `f := identity`, with
   `x` incl. nonzero-low-mantissa (`0.1+0.2`) — catches the asymmetry
   miscompile.
4. Mixed int+float args: `*func(int, float64, int) int` — arbitrary float
   **position**.
5. `float32` variants of (1)/(3) — guards the `i32`-slot width hazard.

New unit tests (`pkg/binate/codegen/*_test.bn`):
- `shimIntSlotType` / `isFloatScalarParam` direct (Step 0).
- Golden-LLVM for `emitFuncValueShim` / `emitClosureShim` /
  `emitFuncValueShimAggregate` with a float param + float return: assert
  `i64`/`i32` slots and the boundary `bitcast`s.
- Golden-LLVM for `emitCallFuncValue` with float arg + float return.

## Verification matrix

Unit: `pkg/binate/codegen`, `pkg/binate/vm`. Conformance: all default
modes (`builder-comp`, `builder-comp-int`, `builder-comp-int-int`,
`builder-comp-comp`, `builder-comp-comp-int`, `builder-comp-comp-comp`) —
especially the func-value suite and the float suite, to confirm A is a
no-op for non-float signatures. Alternate-backend modes
(`builder-comp_native_aa64-comp_native_aa64`, `builder-comp_arm32_baremetal`,
`builder-comp_arm32_linux`) — float ABI differs across targets and
`formatFloat` itself uses `int64` for the 32-bit-target path.

## Relation to other work

Prerequisite for `plan-bootstrap-ccall.md` (bootstrap native-only). The
machinery is the same all-shim convention proven by the rt work
(`plan-rt-ccall-drop-libc.md`). Bug Discovery Protocol: the new
func-value-float tests + their pre-fix failing state in `-int` modes are
the tracked reproduction of the latent miscompile this fixes.
