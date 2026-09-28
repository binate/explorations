# Plan: one exact, type-aware constant evaluator

**Status:** 🟡 design (2026-09-27, work-4/session).  Decided by the user 2026-09-27: do it now ("2. yes");
typed constants wrap at their type ("Wrap at the type"); a typed-constant `MIN / -1` is a compile-time
error ("I guess a compile-time error is fine"); `conv.cast.const-not-laundered` stays ("I guess we can keep
the exception, since it probably catches real bugs"); a negative constant count in a constant
`unsafe_shl` / `unsafe_shr` is a compile-time error ("3. compile-time check seems fine.").  Tracked in
`claude-todo.md` ("Typed-constant expressions are folded without their type").  Follows
`plan-untyped-bitwise-constants.md` (part 1 landed: docs `b1c5a66`, binate `71b184eaf`).

## Problem

Integer constant expressions are folded by five evaluators that disagree:

| Evaluator | Arithmetic | Used by |
|---|---|---|
| checkExpr's untyped fold (`foldIntArith` / `foldIntBitwise`, HasLitVal) | exact bignum, untyped only | typing / fit of untyped constants |
| `check/foldConstNum` | exact bignum, **typeless** | cast fit check, `constIntFor` (const decls), `foldConstMagSign`, `.bni` consts |
| `check/evalConstIntValue` | host `int`, typeless | array dims (`evalConstInt`), shift-count sign check, const-decl fallback, composite keys, `.bni` / const-resolve fallbacks |
| `check/foldConstBoolValue` | via `foldConstNum` | bool consts |
| `irgen/evalConstExpr` (+ `evalConstBool`) | host `int`, typeless | const decls without a checker stamp (bare group members, imports, REPL), array dims without `LenVal`, switch-case values, composite keys |

None applies the typed rule, so typed constants are right only at run time.  The host-`int` ones also
wrap, truncate on a 32-bit host, crash on a negative shift count, and lack `unsafe_shl` / `unsafe_shr`,
and `genConstGroup` / `genConst` silently store the iota value (or 0) when they fail.  Repros are in
the todo entry: `const Z uint8 = cast(uint8, 1) / cast(uint8, 0)` reads 0,
`[cast(uint8, 200) + cast(uint8, 100)]int` has length 300, `const M int8 = -cast(int8, -128)` is an ICE,
`const ( A = -1 >> (0xFFFFFFFFFFFFFFFF - iota); B )` crashes bnc, `const ( A = unsafe_shl(1, iota + 4); B; C )`
reads `16 1 2`, `[(1 << 64) + 3]int` has length 3.

IR-gen cannot just read checker stamps: it folds per generic instantiation (`sizeof(T)` in an array
length or const inside a generic body) and on paths the checker never stamped (imports, the REPL).

## Semantics (spec text to write)

`const.expr.typed` (§6.4) — an operator whose integer operands are constants and at least one is typed,
of type `T` (a type whose underlying type is an integer type: named, alias, `char`), is evaluated exactly
as for non-constant operands of type `T`, and its result is a constant of type `T`:
- An untyped constant operand takes `T` and must fit it (`const.expr.fit`); it is folded exactly first
  (untyped subexpressions stay exact, `const.expr.precision`).
- `+ - *` and unary `-` wrap (§13.3 `expr.arith.defined`); `/ %` truncate; `~` is the complement at `T`'s
  width (`expr.bitwise`); `& | ^` on `T`'s bits.
- A shift takes `T` from its value operand only; the count is its own position, any integer type, and
  its value is exact.  `expr.shift` / `expr.shift.overshift` apply (`cast(uint8, 1) << 8` is 0).
- Division or remainder by zero, and signed `MIN / -1` / `MIN % -1`, are compile-time errors.
- A negative constant shift count is a compile-time error (`expr.shift.negative`), for the unsafe forms
  too.
- `sizeof` / `alignof` are typed `uint` constants and `len` of an array a typed `int` constant (§15),
  so arithmetic on them wraps at the target's width.
- A comparison of constants is a constant `bool` (by value; typed values are already at their type).
- `cast(T, x)` of a constant is a constant of type `T`; `x`'s value (at its own type if typed) must fit
  `T` (`conv.cast.const-not-laundered`, unchanged).  `bit_cast` reinterprets (`types.ReinterpretBitCast`).

Examples for the spec: `cast(uint8, 200) + cast(uint8, 100)` is 44; `~cast(uint8, 1)` is 254;
`-cast(int8, -128)` is -128; `'a' - 'b'` is 255 (`char` is `uint8`); `cast(int8, cast(uint8, 200) + cast(uint8,
100))` is 44 (valid); `cast(int8, cast(uint8, 200))` is an error.

**Open (ask the user before writing the spec):** a constant `unsafe_shl` / `unsafe_shr` with a TYPED
value and a count ≥ its width, and a constant `unsafe_div` / `unsafe_rem` by zero or `MIN / -1` — all
undefined at run time.  Proposed: compile-time errors, for the same reason as the negative count.

## Design

New package `pkg/binate/constval` (in the BUILDER-compiled surface; imports `bignum`, `types`, `ast`,
`token`), a pure evaluator used by both the checker and IR-gen:

- `Value` — an integer (`bignum.Num`) or bool, plus its type (`nil` = untyped).
- `Env` interface — `Const(e @ast.Expr) (Value, int)` for an identifier / selector (with a
  "not known yet" status for pass-1 forward references); `Type(te @ast.TypeExpr) @types.Type` for
  `sizeof` / `alignof` / cast targets; `Iota() (int, bool)`.
- `Eval(env, e) (Value, Status)` — `Status` is OK, not-a-constant, not-known-yet, or an error kind with
  the offending sub-expression (overflow, divide by zero, MIN / -1, negative shift count, cast does not
  fit, operand does not fit its peer's type).
- Operator primitives (`Binary(op, a, b)`, `Unary(op, a)`, `Wrap(n, t)`) that checkExpr's untyped fold
  also calls, so the untyped results agree by construction.

Consumers: the checker's `Env` resolves through its scopes and records values on symbols
(`ConstVal` becomes exact); every checker consumer calls `Eval` and reports its error.  IR-gen's `Env`
resolves through `Module.Consts` and the current instantiation; since the checker has already rejected
every error, an IR-gen `Eval` failure is an internal error (loud), never a silent fallback value.
`evalConstIntValue`, `foldConstNum`, `foldConstMagSign`, `foldConstIntValue`, `evalConstExpr`,
`evalConstBool`, and the host-`int` fallbacks are deleted.  A constant's stored value (`ModuleConst.Val`,
`Symbol.ConstVal`) stays int64-exact as today (every value at a type fits its 64-bit pattern).

## Commits

1. `constval` package with exhaustive unit tests (typed wrap at every width and signedness, the error
   kinds, untyped exactness shared with part 1).  No consumers yet.
2. Checker and IR-gen switched to it together (so they cannot disagree in between), the dead evaluators
   deleted, spec `const.expr.typed` + the unsafe negative-count rule, and a spec conformance test for
   every repro above, plus the constant forms of `unsafe_shl` / `unsafe_shr` (a part 1 coverage gap).
   Audit as in part 1: compile the toolchain and the conformance corpus before and after, host and
   arm32, and compare.

If (2) is too large to review as one, split it by consumer family (declarations; array dims and counts;
casts and bools; IR-gen), each keeping checker and IR-gen in step for that family.
