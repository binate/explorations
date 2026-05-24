# IR-gen consumes type-checker output for typed/folded literals

## Status

Planned 2026-05-22, **landed 2026-05-23**.  All steps in main:

- A1, A2, A3 (Checker plumbing + EXPR_INT_LIT via bignum)
- A4 (EXPR_BINARY folded-shortcut, ungated after the typed-const
  fold bug was fixed in pkg/types)
- B (EXPR_INT_LIT and any HasLitVal-folded expression narrows to
  its typed-int hint at every site genExprOrFuncRef serves —
  var decl, assignment, call args, return, composite lit, binop
  operands)
- A5 (magnitude heuristic dropped from gen_expr's EXPR_INT_LIT
  branch)
- Spillover: pkg/types iota-counted bare consts now carry
  HasLitVal/LitMag/LitSign so `keyword_start + 1`-style folds
  compute correct values.

End-to-end measure: arm32-baremetal conformance ticked from a
baseline of 393 passes pre-work to 402 passes post-A5 — eight
tests on the int32/int64/uint32/uint64 boundaries that
previously regressed are now green, and no test regressed.

This plan documented the design that fixed the IR-gen layer's
magnitude-only heuristic that couldn't satisfy both
`var c int64 = 2147483648 * 2` (wants int64 throughout) and
`var y uint32 = x & 0xFFFFFFFF` (wants uint32 throughout) at
the same time.

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

#### Open issue: Phase B's first attempt regressed 8 unrelated packages

Attempted 2026-05-23.  An initial cut that wired hints through
`genExprOrFuncRef` (already used by var decl / assignment / call
args / return) broke 8 packages in pkg/asm + pkg/native, plus
crashed pkg/bignum's `TestParseOverflowHex` at runtime
(infinite-loop shape).

Initial hypothesis was that `OP_CONST_INT` codegen renders
large unsigned constants (e.g. uint32 = 3221225472 = 0xC0000000)
as out-of-range signed literals to LLVM.  Verified directly:
`add i32 3221225472, 0` and `add i32 -1073741824, 0` produce
identical machine code on aarch64 (both `mov w0, #-0x40000000`).
So that's NOT the bug.

The actual mechanism is still unidentified.  What we know:
- The narrowing emits an OP_CONST_INT at the typed integer
  (e.g. uint8 / uint32) instead of TYP_UNTYPED_INT.
- Without the narrowing, the legacy path emits at
  TYP_UNTYPED_INT (→ i64 LLVM type on host) then ensureWidth
  inserts a `trunc i64 → iN` cast at the consumption site.
- With the narrowing, the consumption site sees an already-
  typed iN constant and ensureWidth becomes a no-op.

Same machine-level result, in principle.  Something in the IR
shape or in downstream code that branches on `val.Typ.Kind ==
TYP_UNTYPED_INT` is sensitive to the difference.  Next debug
pass needs to find that branch.

Sub-step ordering (revised):

- **B0** *(needs investigation)* — Identify the specific
  downstream code path that diverges when an int literal lands
  as typed-iN vs TYP_UNTYPED_INT.  Likely candidates:
  `widenType` (treats TYP_UNTYPED_INT specially); `ensureWidth`
  (no-ops on width match); `EmitSliceSet` / store paths
  (may auto-narrow based on operand kind); rt-call lowering
  (slice-elem types, char-vs-byte interpretation, etc.).
- B1+B2 — hint API + plumbing.  *(blocked on B0)*
- B3 — `EXPR_INT_LIT` uses hint for untyped literals.

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

## Sequencing & landed commits

All landed on main 2026-05-22..23:

1. **A1** (`1547f3d`) — Checker survives `typecheckAll`.
2. **A2** (`ddd7329`) — Checker reaches pkg/ir via `SetChecker`.
3. **A3** (`0a0a3b0`) — `EXPR_INT_LIT` pulls its value from the
   Checker's bignum via `exprIntLitValue` / `bignumToInt`.
4. **B (var decl + typed-context sites)** (`6edc610`) —
   genExprOrFuncRef narrows `EXPR_INT_LIT` to its typed-int hint;
   genDecl routes var-decl RHS through genExprOrFuncRef.  Key
   subtlety: `ctx.CurBlock = b` sync on every early-return —
   without it, for-loop epilogue code lands in the wrong block.
5. **B (binop operands)** (`55ac339`) — genBinary narrows literal
   operands to the typed side's type.  Flips the
   `TestGenUint32MaskLiteralForcedToInt64` pin to assert the
   correct uint32 shape.  Bare-metal 394 → 397 passes.
6. **A4** (`3f05c1a`) — EXPR_BINARY folded-shortcut, initially
   gated to direct-literal operands to dodge a type-checker bug.
   Bare-metal 397 → 398.
7. **Type-checker iota-const fix** (`936a904`) — bare iota'd
   const symbols carry `HasLitVal/LitMag/LitSign` so binop folds
   through `keyword_start + 1`-style expressions compute correct
   values.  A4 drops its direct-literal gate.  Bare-metal
   398 → 400.  Recorded in claude-todo-done.md.
8. **A5** (`83df17e`) — magnitude heuristic dropped.  B broadened
   to fire on any expression whose resolved Type carries
   HasLitVal (catches `EXPR_UNARY(MINUS, lit)`-style folds).
   needsHintNarrowing relaxed to fire for any typed int with a
   shape (width or signedness) differing from the untyped-int
   default.  Bare-metal 400 → 402.

Total bare-metal gain across the series: 393 → 402 passes (9
tests on int width / signedness boundaries unfailed).  Plus the
test-coverage scaffolding (`genFromSourceWithChecker` helper,
direct unit tests for `bignumToInt` / `isTypedInt` /
`needsHintNarrowing` / `intFitsInType` / Phase B end-to-end
shapes) is left in place for future literal-handling work.

### Detours encountered

Two false starts worth recording:

- **B0 codegen normalization (red herring)** — initial guess
  that LLVM rejected `add i32 3221225472, 0` (uint32 value >
  INT32_MAX in signed i32 representation).  Verified by direct
  llc compile: LLVM accepts both `add i32 3221225472, 0` and
  `add i32 -1073741824, 0` and produces identical machine code.
  The actual divergence was the missing `ctx.CurBlock = b` sync
  in genExprOrFuncRef's early-return path, which orphaned IR
  blocks past for-loop terminators.
- **A4 first cut (deferred, then re-enabled)** — folding via
  `Checker.ExprType(binop).HasLitVal` for ALL binops broke
  pkg/token's `keyword_start + 1` because the type checker
  inherited the typed-const's LitVal from the OTHER (literal)
  operand via commonType.  Workaround: gate on direct-literal
  operands.  Permanent fix: store the const's iota value on the
  symbol's Type — see step 7.

After A: typed literals (uint32, int64, etc.) come through with
the right type.  Folded untyped × untyped collapses to one
literal at the folded type.  The `0xFFFFFFFF & uint32` test pin
flips to assert the correct shape.

After B: untyped literals assigned to typed contexts (without
arithmetic) also narrow correctly, removing the last reliance
on a magnitude heuristic.
