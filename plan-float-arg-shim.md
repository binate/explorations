# Plan: float-argument support for the function-value / extern shim path

Status: **COMPLETE (mechanism shipped on main, `7abc3809`, Design A);
kept for design rationale.** Unblocks the bootstrap native-only work in
[`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md).

## Testing note (discovered during implementation)

The `-int` bug is **only** reachable when a BYTECODE caller invokes a
NATIVE, non-trampoline float callee through `@__shim` — i.e. a registered
float **extern**. A *user* float func-value in `-int` is bytecode (or an
all-int trampoline re-entry), so its float travels as a type-erased
64-bit VM slot and round-trips fine *without* the fix. So the conformance
tests (562-566) are **compiled-mode guards** for the ABI reshape;
they pass on baseline and do NOT catch the bug. The canonical
bug-reproduction is the **VM unit tests** `pkg/binate/vm`
`TestExternFloat{Arg,Return,32Arg}ViaRegistry`, which register a native
float function as an extern and call it from hand-built bytecode (the
exact shape the bni host hits for `bootstrap.formatFloat`).

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
  lowering to an **all-`int`**
  `OP_CALL_INDIRECT`. The native backends place an arg in an FP register
  only when the IR operand type is float; with all-`int` operands the FP path never
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
modes.

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

## Relation to other work

Prerequisite for `plan-bootstrap-ccall.md` (bootstrap native-only). The
machinery is the same all-shim convention proven by the rt work
(`plan-rt-ccall-drop-libc.md`). Bug Discovery Protocol: the new
func-value-float tests + their pre-fix failing state in `-int` modes are
the tracked reproduction of the latent miscompile this fixes.
