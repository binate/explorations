# Code-Red P2 — Divide-by-zero / mod-by-zero defined panic (standalone plan)

> A standalone P2 work plan, pulled out of Plan 4 (scalar) at the user's
> direction (2026-06-05). Implements the **ratified** defined-panic behavior for
> integer `/` and `%` by zero and the signed `MIN/-1` overflow, plus the
> `unsafe_div` / `unsafe_rem` opt-out builtins. Source-confirmed against the
> existing array-bounds-check panic, which is a clean 1:1 template. See the
> ratified entry in `claude-todo.md`.

## Status (2026-06-05)

- **Sequencing step 1 (runtime)** — **LANDED** (binate `f3327891`). `rt.DivCheck`
  / `rt.DivFail` in both libc + baremetal impls, `rt.bni` decls, `rt_test.bn`
  `TestDivCheck`.
- **Sequencing step 2 (IR op + guard + four lowerings)** — **LANDED** (binate
  `efeb0f94`). `OP_DIV_CHECK` before every integer `OP_DIV`/`OP_REM`; LLVM
  (`emit_instr`), aarch64 + x64 (`*_dispatch`), VM (`BC_DIV_CHECK` →
  `rt.DivCheck`). **Design refinement vs. this plan:** instead of each backend
  sign/zero-extending operands, IR-gen extends them to 64-bit once
  (`ensureInt64ForCheck` in `gen_binary.bn`) — a target-aware, backend-agnostic
  normalization that respects the IR/backend boundary. Backends only marshal two
  64-bit operands + the type's signed MIN, recovered via the new shared
  `types.SignedMinForWidth` from the width on `IntVal` and the signedness on
  `BoolVal`. The VM carries width+isSigned (not a 64-bit MIN immediate), so no
  immediate has to fit a host-int field. Unit tests across types/ir/codegen/vm.
- **Sequencing step 3 (panic conformance cells)** — **LANDED** (binate
  `a1c853d2`). Cells `606`–`609` (div0, rem0, int32 MIN/-1, int64 MIN/-1) — the
  plan's 602-605 numbers were taken by concurrent work, so they moved up.
  Verified green on builder-comp (LLVM) and builder-comp-int (VM); full
  builder-comp suite 810/0 with the guard on every divide.
- **Sequencing step 4 (`unsafe_div` / `unsafe_rem`)** — not started (next).
- **Sequencing step 5 (docs: move ratified entry to `claude-todo-done.md`)** —
  pending step 4.

## Summary

Binate's integer `/` and `%` currently lower to raw division on every backend
(LLVM `sdiv`/`udiv`/`srem`/`urem`, aarch64 `SDIV`/`UDIV`+`MSUB`, x64
`IDIV`/`DIV`, VM host `/`/`%`), so divide-by-zero and the signed `MIN/-1`
overflow are *accidental* behavior (LLVM UB, native SIGFPE, VM host trap). The
ratified decision makes these a **defined runtime panic**, exactly mirroring the
existing array bounds-check, plus opt-out builtins `unsafe_div`/`unsafe_rem`.

The existing `OP_BOUNDS_CHECK` is a clean, fully-general template: a **void IR op
emitted before the access**, lowered by every backend to a **call to a runtime
function `rt.BoundsCheck`** that does the comparison and, on failure, calls
`rt.BoundsFail` → prints `runtime error: ...` → `Exit(1)`. The compiled backends
do **not** inline the comparison. So the whole feature is: one new void IR op
`OP_DIV_CHECK`, one new runtime `rt.DivCheck`, four backend lowerings that each
emit a call, and two opt-out builtin keywords — every piece has a 1:1 analog in
the tree.

Key subtlety: **const-fold already rejects compile-time div-by-zero**
(`check_expr_constfold.bn:59`, test `427_err_const_fold_div_by_zero`), and uses
arbitrary-precision bignums so a *constant* `MIN/-1` folds without overflow. The
runtime check therefore only ever fires for **non-constant** operands.

## Ratified spec (restated precisely)

- Integer `/` (`OP_DIV`) and `%` (`OP_REM`), including the 64-bit variants, must
  trap as a **defined panic** when: (1) the divisor is `0` (signed or unsigned),
  or (2) (signed only) the dividend equals the type's signed `MIN` **and** the
  divisor is `-1`.
- The panic is per-target *defined* behavior, identical in shape to the
  bounds-check panic (Go/Rust semantics; Rust also panics on `MIN/-1`).
- Floats are unaffected: `fdiv` stays IEEE; `%` on floats remains a type error
  (`284_err_float_rem`).
- Two opt-out builtins skip the check (hot paths), mirroring `unsafe_index`:
  `unsafe_div(a, b)` → unchecked `OP_DIV`; `unsafe_rem(a, b)` → unchecked
  `OP_REM` (truncated remainder — **not** `unsafe_mod`).
- `ir.bni` already documents this contract on `OP_DIV`/`OP_REM` (lines 25-38),
  with a "not yet implemented" note to remove.

## Current state (with file:line)

### Bounds-check panic — the template to mirror, end-to-end
- **IR op decl**: `pkg/binate/ir.bni:143` `OP_BOUNDS_CHECK` (void; `Args[0]=index, Args[1]=len`).
- **IR emit helper**: `pkg/binate/ir/ir_ops.bn:96-101` `EmitBoundsCheck`; op-name string at `ir_ops.bn:265`.
- **IR-gen call site**: `pkg/binate/ir/gen_access.bn:33` `genIndex(ctx, b, e, checked bool)` emits `EmitBoundsCheck` before the load when `checked`; `unsafe_index` calls with `checked=false` (`gen_expr.bn:301-312`).
- **Runtime entrypoint**: `impls/core/libc/pkg/builtins/rt/rt.bn:210-215` `BoundsCheck` → `BoundsFail` (`rt.bn:100-110`) → prints + `Exit(1)`. Mirrored in `impls/core/baremetal/pkg/builtins/rt/rt.bn:154-162,261-266`.
- **LLVM**: `pkg/binate/codegen/emit_instr.bn:397-407` emits `call void @bn_pkg__builtins__rt__BoundsCheck(...)`.
- **aarch64**: `pkg/binate/native/aarch64/aarch64_dispatch.bn:403-418` (idx→X0, len→X1, `Bl`, `ResetRegs`).
- **x64**: `pkg/binate/native/x64/x64_dispatch.bn:318-344` (len→RSI, idx→RDI, `Call`, `ResetRegs`).
- **VM**: `pkg/binate/vm/lower_instr.bn:259-265` (`OP_BOUNDS_CHECK → BC_BOUNDS_CHECK`); opcode `pkg/binate/vm.bni:210`; exec `pkg/binate/vm/vm_exec_helpers.bn:304-306`.

### DIV/REM today — where the guard inserts
- **IR-gen** (the one insertion point): `pkg/binate/ir/gen_binary.bn:38,90` — `tokenToOp` maps `/`→`OP_DIV`, `%`→`OP_REM` (`gen_util.bn:18-19`; compound `/=`,`%=` at `gen_util.bn:41-42`); final emission `b.EmitBinop(op, lhs, rhs, resultTyp)` at line 90. Emit `OP_DIV_CHECK` immediately before this `EmitBinop` when `op` is `OP_DIV`/`OP_REM` and the result type is integer. Signedness on `resultTyp.Signed`; operands `lhs`/`rhs` in hand.
- **Backends (no change to the divide ops themselves)**: LLVM `emit_ops.bn:26-43`; aarch64 `aarch64_ops.bn:56-74`; x64 `x64_ops.bn:65-66,128-180` (`emitDivOrRem`); VM `lower_instr_helpers.bn:172-189` + `vm_exec_pure.bn:22-33` / `vm_exec64.bn:21-26`.

### Builtin keyword wiring — the `unsafe_index` path to clone
- **Token enum**: `pkg/binate/token.bni:46-79` (`UNSAFE_INDEX` at :53).
- **Display**: `pkg/binate/token/token.bn:53`. **Lexing**: `token.bn:150-178` `Lookup` (linear scan; adding an enum value + `TypeName` case suffices). **Parsing**: `parse_primary.bn:36-37` → `parse_builtin.bn:212-229`. **Type-check**: `check_builtin.bn:164-191` (binary `/`/`%` ref: `check_expr.bn:284-305`). **IR-gen**: `gen_expr.bn:301-312`.

### Conformance test mechanics
- **Runner**: `conformance/run.sh:240-313` `run_error_test` (`.error` lines are `grep -E` regexes vs combined stdout; works for runtime panics since the message prints before `Exit(1)`); per-mode `.error.<MODE>` supported (`run.sh:337-342`).
- **Relevant existing**: `309_err_index_oob_slice` (runtime OOB panic), `291_unsafe_index` (unsafe no-panic), `427_err_const_fold_div_by_zero` (compile-time div0), `284_err_float_rem`, `151_unsigned_shift_div`. Highest test number today: `601` → new cells start at `602`.

## Implementation

**Concern 1 — `OP_DIV_CHECK` + guard emission.**
`ir.bni`: add `OP_DIV_CHECK` near `OP_BOUNDS_CHECK` (:143), documented `panic if Args[1]==0 || (signed && Args[0]==MIN && Args[1]==-1)`; drop the "not yet implemented" note on `OP_DIV`/`OP_REM`. `ir_ops.bn`: add `EmitDivCheck(dividend, divisor, typ)` mirroring `EmitBoundsCheck` (set `instr.Typ=typ` so backends read `Typ.Signed`/`Typ.Width`) + op-name string. `gen_binary.bn:90`: before the final `EmitBinop`, when `op` is `OP_DIV`/`OP_REM` and `resultTyp` is integer, emit `EmitDivCheck(lhs, rhs, resultTyp)` — one site covers `/`, `%`, `/=`, `%=`.

**Concern 2 — runtime `rt.DivCheck`.** In both `impls/core/{libc,baremetal}/pkg/builtins/rt/rt.bn`, add `DivCheck` + `DivFail` mirroring `BoundsCheck`/`BoundsFail`. The MIN value is type-dependent (`int8` MIN ≠ `int64` MIN), so a width-agnostic `int` runtime can't hold a fixed MIN. **Design B — DECIDED (user, 2026-06-05)**: `DivCheck(dividend int64, divisor int64, signedMin int64, isSigned int)` — backend passes sign/zero-extended operands + the type's `MIN` constant (from `Typ.Width`) + a signed flag; runtime does `if divisor==0 { DivFail(0) }; if isSigned!=0 && dividend==signedMin && divisor==-1 { DivFail(1) }`. Keeps all compare logic in one place (the bounds-check shape). `DivFail(0)` prints `runtime error: integer divide by zero`; `DivFail(1)` prints `runtime error: integer overflow (MIN / -1)`; both `Exit(1)`.

**Concern 3 — backend lowering of `OP_DIV_CHECK` (one call each).** Each backend reads `ins.Typ`, computes the signed-`MIN` immediate, sign/zero-extends operands to 64-bit, emits a 4-arg call:
- **LLVM** `emit_instr.bn` (next to :397): `call void @bn_pkg__builtins__rt__DivCheck(i64, i64, i64, i32)` with `sext`/`zext` to `i64`; extern auto-declared from imports like `BoundsCheck` (`emit.bn:176-181`).
- **aarch64** `aarch64_dispatch.bn` (next to :403): dividend→X0, divisor→X1, min→X2 (`Movz`/`Movk`), isSigned→X3, `Bl`, `ResetRegs`.
- **x64** `x64_dispatch.bn` (next to :318): SysV RDI/RSI/RDX/ECX in clobber-safe order, `Call`, `ResetRegs`.
- **VM**: `vm.bni` add `BC_DIV_CHECK` (next to :210); `lower_instr.bn` (next to :259) lower `OP_DIV_CHECK`, carrying min + isSigned in `bc.Imm`/an `Aux` slot; `vm_exec_helpers.bn` (next to :304) `case BC_DIV_CHECK: rt.DivCheck(...)`.

**Concern 4 — `unsafe_div`/`unsafe_rem` (clone `unsafe_index`).** `token.bni` add `UNSAFE_DIV`/`UNSAFE_REM`; `token.bn` `TypeName` cases; `parse_primary.bn` dispatch; `parse_builtin.bn` two-arg parsers; `check_builtin.bn` handlers (both args integer, no floats, return widened type); `gen_expr.bn` lower to `EmitBinop(OP_DIV/OP_REM, ...)` **without** `EmitDivCheck` — the opt-out.

## Sequencing (each commit green)

1. **Runtime first** — `rt.DivCheck`/`rt.DivFail` in both `rt.bn` impls + `rt_test.bn` (runtime exists before any caller, like BoundsCheck).
2. **IR op + guard + all four lowerings in ONE commit** — an unhandled `OP_DIV_CHECK` would emit `; unhandled op` (LLVM `emit_instr.bn:409`) or silently no-op (native), so IR-gen + four backends must land together to keep every mode green. Add IR/codegen/native/VM unit tests (clone `gen_access_test.bn:79` `TestUnsafeIndexSkipsBoundsCheck`).
3. **Panic conformance cells** — `/0`, `%0`, `MIN/-1` (below), all modes.
4. **`unsafe_div`/`unsafe_rem`** — token/parser/checker/IR-gen + unit tests + no-panic cell (independent of 3).
5. **Docs** — move the ratified `claude-todo.md` entry to `claude-todo-done.md`.

## Tests needed (cells start at 602; all default + alt-backend modes)

- **`602_err_div_by_zero.{bn,error}`** — **non-constant** zero divisor (else const-fold `427` intercepts at compile time). `.error`: `runtime error: integer divide by zero`.
- **`603_err_rem_by_zero.{bn,error}`** — same with `%`.
- **`604_err_div_min_neg_one.{bn,error}`** — signed `MIN/-1`, non-constant operands. `.error`: `runtime error: integer overflow`. Consider a second cell for a narrower signed type (`int32` MIN) to exercise the width-dependent MIN.
- **`605_unsafe_div_rem.{bn,expected}`** — `unsafe_div`/`unsafe_rem` with **valid** operands match `/`/`%` (clone `291_unsafe_index`); do NOT assert `unsafe_div(x,0)` (UB by design, non-portable).
- **Unit tests** (Bug Discovery Protocol): IR `OP_DIV_CHECK` emission/omission (`gen_binary_test.bn` `countOp`); codegen string-shape; native dispatch; VM `BC_DIV_CHECK`; `rt_test.bn` `TestDivCheck` (non-failing path only — failing path `Exit(1)`s, like `TestBoundsCheck`).

## Risks / open questions

1. **MIN/-1 detection design — DECIDED (user, 2026-06-05): design B.**
   `rt.DivCheck` is parameterized by the type-`MIN` + signed flag (4-arg call;
   all compare logic in the runtime — the structural twin of `rt.BoundsCheck`).
   The rejected design A (compute predicates inline in each of 4 backends,
   thinner runtime) spread compare logic across backends. The worker implements
   the signature `rt.DivCheck(dividend int64, divisor int64, signedMin int64,
   isSigned int)`.
2. **Per-type MIN** is `-2^(Width-1)`; IR-gen has `resultTyp.Width`/`.Signed` at
   `gen_binary.bn:90`, so the constant is computed at lowering time. Unsigned
   types get only the zero-check (`isSigned` suppresses the overflow test).
3. **Const-fold already covers compile-time cases** — conformance cells **must**
   use non-constant operands or they hit the compile-error path (`427`), not the
   runtime panic. Call this out in test comments.
4. **Panic entrypoint exists on all targets** — `rt.BoundsCheck`/`BoundsFail` are
   in both `libc` and `baremetal` rt and reached on every backend, so
   `rt.DivCheck`/`DivFail` in the same two files are available everywhere. `rt`
   is outside `cmd/bnc`'s tree → may use the full language, but stay simple.
5. **VM `BCInstr` immediate plumbing** — `BC_BOUNDS_CHECK` uses only Src1/Src2;
   `BC_DIV_CHECK` (B) also needs the MIN constant + isSigned. `BCInstr` has an
   `Imm` field (used by `BC_ALLOC`/`BC_BOX`) and an `Aux` (used by
   `BC_FUNC_VALUE`); confirm there's room, else a small struct addition (flag).
6. **64-bit operands on 32-bit targets** — operands extended to `int64` become
   register pairs on arm32/baremetal; the VM already pairs 64-bit values and the
   arm32 ABI passes 64-bit args in pairs. Verify the call-emission helpers handle
   the `int64` arg type (adjacent to the float-arg-shim work `7abc3809`). The
   spec requires checking the `*64` variants, so the check must cover 64-bit
   divides on 32-bit targets.
7. **Cost** — every non-`unsafe` integer divide gains a preceding guard (a call,
   like bounds-check); `unsafe_div`/`unsafe_rem` are the opt-out. If an
   inline-branch-to-panic is preferred for div specifically, that's a larger
   divergence from the bounds-check pattern and a separate decision.

## Files the worker will touch

`pkg/binate/ir.bni`, `ir/ir_ops.bn`, `ir/gen_binary.bn`, `ir/gen_expr.bn`,
`token.bni`, `token/token.bn`, `parser/parse_primary.bn`,
`parser/parse_builtin.bn`, `types/check_builtin.bn`, `codegen/emit_instr.bn`
(+`emit.bn` manifest), `native/aarch64/aarch64_dispatch.bn`,
`native/x64/x64_dispatch.bn`, `vm.bni`, `vm/lower_instr.bn`,
`vm/vm_exec_helpers.bn`, `impls/core/libc/pkg/builtins/rt/rt.bn`,
`impls/core/baremetal/pkg/builtins/rt/rt.bn`, plus matching `*_test.bn` and
`conformance/602–605`.
