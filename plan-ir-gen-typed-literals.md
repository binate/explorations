# IR-gen consumes type-checker output for typed/folded literals

## Status

Planned 2026-05-22.  Not started.  This plan documents the design we
agreed on after discovering that the IR-gen layer's magnitude-only
heuristic for typing integer literals can't satisfy both
`var c int64 = 2147483648 * 2` (wants int64 throughout) and
`var y uint32 = x & 0xFFFFFFFF` (wants uint32 throughout) at the same
time, and that the fix needs the type-checker's already-computed
information.

Tracking entry in `claude-todo.md`: "IR-gen integer literal width
promotion can break uint32-context code".

## State of the world

The type checker computes a `Type` for every `Expr` and stores it via
`Expr.ResolvedTypeID` → `Checker.ExprType(id)`.  For integer literals
the resolved Type carries `HasLitVal=true` + `LitMag uint64` +
`LitSign bool` (a `bignum.Num` value, range `[-(2^64-1), +(2^64-1)]`).
`pkg/types/check_expr_constfold.bn` folds `+ - * / % & | ^ << >>` on
untyped × untyped, producing another `TYP_UNTYPED_INT` Type with the
folded `LitMag/LitSign`.  `AssignableTo` uses the folded magnitude
for fit-checking against typed targets.

**`pkg/lint` consumes this info** via
`ctx.Checker.ExprType(e.ResolvedTypeID)` — pattern at `lint.bn:162`.

**`pkg/ir` does not.**  IR-gen ignores `ResolvedTypeID` entirely.
For `EXPR_INT_LIT` it calls `parseIntLit(e.Name)` (re-parsing the
source text into a host i64), and for `EXPR_BINARY` it generates IR
for both operands separately — never seeing the type-checker's fold.

The Checker is currently created locally in
`cmd/bnc/compile.bn:typecheckAll` (and the equivalent in
`cmd/bni/repl.bn`) and dropped on return, before IR-gen runs.

## Two phases

### Phase A — IR-gen consumes the Checker's resolved types / folds

High-leverage piece.  Subsumes the magnitude heuristic
`gen_expr.bn` currently has for promoting wide literals to int64.

- **A1** — Keep the Checker alive past type-check.  `typecheckAll`
  returns `@types.Checker`; `cmd/bnc/{main,compile,test}.bn` and
  `cmd/bni/{main,repl}.bn` (and `repl_test.bn`) hold onto it.  Pure
  plumbing, no semantic change.

- **A2** — Thread it into IR-gen.  Add `Checker @types.Checker` to
  `GenContext`.  `ir.GeneratePackage` / `ir.GenModule` accept a
  Checker arg.  All callers pass it through (including
  `pkg/ir/ir_test.bn`'s `genFromSource` after running a real
  type-check pass).  Still no semantic change — IR-gen has the
  Checker but doesn't read it yet.

- **A3** — `EXPR_INT_LIT` consults
  `ctx.Checker.ExprType(e.ResolvedTypeID)`:
  - If the resolved type is a concrete typed integer
    (`TYP_INT` with Width/Signed populated) → emit `OP_CONST_INT`
    at that type.  This is the win — uint32 literals stay uint32,
    int64 literals stay int64, neither survives via a magnitude
    threshold.
  - If `TYP_UNTYPED_INT` with `HasLitVal` → emit `OP_CONST_INT` at
    `TypUntypedInt()` using the bignum value (existing widenType
    rules then adopt the operand context).
  - If the Checker is unavailable (`ResolvedTypeID == 0`, e.g.
    legacy test paths) → fall back to current heuristic.

- **A4** *(deferred — see note below)* — `EXPR_BINARY` folded-
  shortcut.  If `Checker.ExprType(e.ResolvedTypeID)` on a binop
  carries `HasLitVal`, emit a single `OP_CONST_INT` at the
  resolved type with the folded value and skip generating the
  binop IR entirely.  Would be a strict optimization (the
  existing magnitude heuristic already handles `2147483648 * 2`
  via individually-typed operands + widenType + ensureWidth).
  Attempted 2026-05-22; a folded `1 << 52` in `pkg/bootstrap.
  formatFloat` (the only fold that crosses the int32 boundary at
  bootstrap-compile time) produced IR that broke
  `TestRegisterImportsIotaConsts` via a downstream-state path I
  couldn't quickly trace.  Revisit once Phase B's context
  propagation is in — the fold-shortcut may be unnecessary by
  then, since literals + ensureWidth handle the equivalent cases
  end-to-end without an explicit AST collapse.

- **A5** *(blocked on B, not A4)* — Delete the magnitude
  heuristic in `gen_expr.bn`'s `EXPR_INT_LIT` branch (the
  `v < -2147483648 || v > 2147483647` promotion).  Phase B's
  context propagation subsumes it: literals adopt their
  surrounding typed context directly, so the magnitude trigger
  isn't needed.

Each is independently testable and committable.

### Phase B — context-type propagation for the still-untyped cases

Even after Phase A, an untyped literal in a context like
`var c int64 = 2147483648` (no arithmetic, just an assignment)
arrives at IR-gen as `TYP_UNTYPED_INT`.  The current magnitude
heuristic handled that; Phase A drops the heuristic, so the hint
must come from the LHS instead.

- **B1** — Hint API.  Add an optional `hint @types.Type` parameter
  to `genExpr` (via an internal `genExprHinted` variant; the
  public `genExpr` defaults to `nil`).

- **B2** — Plumb hints from call sites that know the context:
  - `var x T = value` → hint `value` with `T`.
  - `lhs = rhs` → hint `rhs` with `lhs`'s type.
  - `x op= rhs` → hint `rhs` with `x`'s type.
  - `f(args...)` → hint each arg with the callee's param type.
  - `return v1, v2, ...` → hint each with the function's
    corresponding result type.
  - `EXPR_BINARY` → propagate hint to both operands.

- **B3** — Literal uses hint.  In `genExpr` for `EXPR_INT_LIT`,
  after Phase A's Checker lookup, if the resolved type is
  `TYP_UNTYPED_INT` and `hint` is a typed integer + the literal
  fits in `hint`'s range → emit at `hint`.  Else fall through to
  the untyped path.

#### Known prerequisite: OP_CONST_INT codegen for typed-unsigned ranges

Attempted 2026-05-23.  An initial cut that wired hints through
`genExprOrFuncRef` (already used by var decl / assignment / call
args / return) broke 8 packages in pkg/asm + pkg/native, plus
crashed pkg/bignum's `TestParseOverflowHex` at runtime.

Root cause: the codegen for `OP_CONST_INT` (`pkg/codegen/
emit_instr.bn`) emits `add <type> <IntVal>, 0` with `IntVal`
written verbatim via `out.WriteInt`.  When `IntVal` is a uint32
value greater than `INT32_MAX` (e.g. `3221225472 = 0xC0000000`)
and the target type is i32, LLVM treats `add i32 3221225472, 0`
as out-of-range for signed-32 representation — the constant is
silently truncated or reinterpreted in a way that doesn't match
the untyped-then-narrow path.  Today that legacy path emits
`add i64 3221225472, 0` followed by `trunc i64 ... to i32`,
which preserves the bit pattern unambiguously.

The fix lives in `OP_CONST_INT` codegen, not in Phase B itself:
when `instr.Typ` is unsigned-N-bit, emit the value as its
two's-complement signed-N-bit representation (so a uint32 value
`v >= 2^31` is written as `v - 2^32`).  Same bit pattern, LLVM-
acceptable rendering.  Should be a small change in
`emit_instr.bn`'s OP_CONST_INT branch; orthogonal to the
context-propagation work but blocks it.

Sub-step ordering:

- **B0** *(prerequisite)* — Normalize OP_CONST_INT's LLVM
  rendering for typed unsigned ints whose `IntVal` exceeds the
  signed-N-bit range.  Two's-complement reinterpretation:
  `IntVal - (1 << Width)` when `IntVal >= 1 << (Width - 1)` and
  the type is unsigned.  Mirrors the existing `formatInt64`
  magnitude trick.
- B1, B2, B3 as above, after B0 lands.

## Bignum → IntVal conversion

`bignum.Num` is `(uint64 magnitude, bool sign)`.  Host `int` is
i64.  Conversion rules for the value passed to `EmitConstInt`:

- `sign=false, mag ≤ 2^63-1` → `cast(int, mag)`.
- `sign=false, mag > 2^63-1` (uint64-only positives, e.g.
  UINT64_MAX) → `cast(int, mag)` — wraps to negative.  LLVM emits
  the same bit pattern either way (`add i64 -1, 0` ≡
  `add i64 18446744073709551615, 0`).
- `sign=true, mag ≤ 2^63` → `0 - cast(int, mag)` — handles
  int64-min via two's-complement wrap (same trick the
  `formatInt64` runtime uses).
- `sign=true, mag > 2^63` → out of int64 range; type-checker
  should have rejected, but assert.

Open question: do we keep `Instr.IntVal int` and treat it as a bit
pattern, or widen the IR to carry `(uint64, sign)` so uint64-only
values are first-class?  Lean toward keeping `int` — LLVM codegen's
`out.WriteInt(IntVal)` already produces the right bytes.

## Implications

### `cmd/bni` / `pkg/vm`

`cmd/bni` mirrors `cmd/bnc` — same `types.NewChecker()` →
`ir.GeneratePackage` flow.  A1's plumbing change repeats verbatim
in `cmd/bni/{main,repl}.bn` + `cmd/bni/repl_test.bn`.

`pkg/vm` is downstream of IR — it lowers `ir.Module` to bytecode.
Once IR has correct OP_CONST_INT types, the VM's
`pkg/vm/lower_instr.bn` consumes them as-is.  One re-test
concern: if the bytecode encoding picks an opcode per integer
width (distinct `BC_LOAD_CONST_*` for int8/16/32/64), Phase A may
shift which opcode gets emitted for a literal that was previously
TYP_UNTYPED_INT and is now typed.  Same value, same bit pattern,
different routing — verify via the bytecode conformance modes.

### Circular dependencies

Clean.  Dep graph today:

```
bignum (leaf)
  ↑
types ── imports bignum, ast, token
  ↑
ir ── imports types, ast, mangle, buf (not bignum, not vm)
  ↑                ↑
 vm           cmd/bn{c,i,...} (top-level)
```

Phase A adds:
- `pkg/ir → pkg/bignum` (to convert `LitMag`/`LitSign` → host int).
  Bignum is a leaf — no cycle.
- `pkg/ir tests → pkg/types.Checker construction`.  pkg/ir already
  imports pkg/types non-test; pkg/types doesn't import pkg/ir at all
  (verified).  No test-side cycle.

### Test infra migration

`pkg/ir/ir_test.bn`'s `genFromSource` currently skips type-check
(parser → IR-gen direct).  With Phase A landed, it needs to run
type-check too — otherwise untyped literals stay untyped through
A3's lookup and the new typed-literal IR shape isn't exercised.
Probably extract a `genFromSourceTypechecked` helper rather than
mutating `genFromSource` outright, in case some existing tests
intentionally exercise the no-typecheck IR-gen path.  Decide at
A2 implementation time.

## Sequencing & expected commits

Per-step commits, each individually green on host + arm32-baremetal:

1. A1 — Checker survives `typecheckAll`.  *(landed)*
2. A2 — Checker reaches `GenContext` (still unused).  *(landed)*
3. A3 — `EXPR_INT_LIT` consults Checker for the resolved type.
   *(landed)*
4. ~~A4~~ — `EXPR_BINARY` folded-shortcut.  *(deferred — see Phase
   A4 note above)*
5. B0 — Normalize OP_CONST_INT codegen for typed-unsigned values.
   *(prerequisite for B1+, blocks the rest of Phase B)*
6. B1+B2 — hint API + plumbing.
7. B3 — `EXPR_INT_LIT` uses hint for untyped literals.
8. A5 — drop the magnitude heuristic.  *(now last; B subsumes
   what A4 would have covered)*

After A: typed literals (uint32, int64, etc.) come through with
the right type.  Folded untyped × untyped collapses to one
literal at the folded type.  The `0xFFFFFFFF & uint32` test pin
flips to assert the correct shape.

After B: untyped literals assigned to typed contexts (without
arithmetic) also narrow correctly, removing the last reliance
on a magnitude heuristic.
