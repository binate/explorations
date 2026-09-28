# Plan: the type of an untyped constant shift VALUE (`1 << n`)

**Status:** 🟡 spec change drafted, awaiting adversarial review (2026-09-27).  Tracked in
`claude-todo.md` ("An untyped constant shift VALUE is typed two ways").

## Problem

With `n uint8` = 9, `cast(int64, 1 << n)` is 0 but `cast(int64, (0 + 1) << n)` is 512 (LLVM and native
alike).  The checker types both shifts as `uint8` — an untyped operand of a shift goes through
`foldIntBitwise` → `commonType`, which gives the untyped VALUE the COUNT's type — while IR-gen follows
that only for a plain literal value; a constant-expression or const-name value is computed at `int`.
The spec (§13.5 `expr.shift`) says the result type is the value's type and the count's type is
independent, but does not say what type an untyped constant value takes when the count is not a
constant.

## Decision (user, 2026-09-27)

An untyped constant shift value is untyped and takes its type from the surrounding context — so
`1 << n` and `(0 + 1) << n` (and `K << n` for an untyped `const K`) always agree.

## Proposed spec text

New rule in §13.5, after `expr.shift`:

> `expr.shift.untyped-value` — A shift whose **count is not a constant** is not a constant expression,
> even when its value operand is an **untyped integer constant** (a literal, a constant expression, or
> an untyped `const` name; §6.1 `const.untyped.coercion`). Such a value operand takes the type it would
> take **if the whole shift were replaced by the value operand alone**: the **integer** type its context
> supplies — the declared type of the variable, field, parameter, or result it initializes or is
> assigned to, the other operand of an enclosing arithmetic, bitwise, or comparison operator, the
> target of an enclosing `cast` — or, where the context supplies **no integer type** (a short variable
> declaration, an index or shift-count position, a comparison against another untyped operand, or a
> non-integer target such as `cast(float64, …)`), its **default type `int`** (§6.2). The shift's result
> has that type (`expr.shift`), and the value must be representable in it (§6.4 `const.expr.fit`); the
> count's type never contributes. The rule composes: an operand of an arithmetic or bitwise operator
> that is itself such a shift is typed from the operator's context, exactly as an untyped constant
> operand would be. If the count is also a constant, the whole shift is a constant expression (§6.4)
> and this rule does not apply.

> _Example._ With `var n uint8 = 9`:
>
> ```
> var a uint8 = 1 << n          // uint8: 0 (overshift, expr.shift.overshift)
> var b int64 = 1 << n          // int64: 512
> x := 1 << n                   // int: 512 (default type)
> cast(int64, 1 << n)           // int64: 512
> cast(int64, (0 + 1) << n)     // int64: 512 — a constant-expression value is typed the same way
> m & (1 << n)                  // m's type (the & peer)
> (1 << n) | (1 << k)           // typed from the context of the whole |, like `1 | 1`
> cast(float64, 1 << n)         // the shift is int (a float target types no integer), then converted
> var f float64 = 1 << n        // error: the shift is int, which is not assignable to float64 (§6.5)
> var u uint8 = 256 << n        // error: 256 does not fit uint8
> ```

A one-line cross-reference in §6.1 `const.untyped` ("… takes a type from its context (§6.2, §6.5,
§6.6; for the value operand of a non-constant shift, §13.5 `expr.shift.untyped-value`)").

### Deliberate choice to review: non-integer contexts

A literal reading of "as if replaced by the value operand alone" makes `cast(float64, 1 << n)` a shift
of a `float64` value — Go rejects that (`float64(1 << s)` is a well-known gotcha).  The draft instead
gives the value its default `int` wherever the context supplies no *integer* type: an error still
results wherever an `int` is not allowed (`var f float64 = 1 << n`, by §6.5's no-int-mix), but an
explicit `cast` works.

## Behavior changes vs the current implementation

- The shift no longer takes the count's type: `x := 1 << k` (k uint8) becomes `int`, `var y uint8 = 1
  << k` (k int) becomes valid (was a type error), and `cast(int64, 1 << n)` on a 32-bit target shifts at
  64 bits (was 32).
- A constant-expression / const-name value agrees with a literal value in every context.

## Implementation sketch

- **Checker:** a non-constant shift with an untyped-constant value gets an untyped integer type WITHOUT
  a constant value (`TYP_UNTYPED_INT`, `HasLitVal` false); it combines with other untyped operands
  like an untyped constant would (the result stays untyped, non-constant); at every point where an
  untyped operand receives its final type (assignment / argument / return / field / cast / operator
  peer / default), the final type is recorded down the expression to each such shift.  Constant fit
  of the value is checked against that final type.
- **IR-gen:** reads each such shift's recorded type and emits the value constant (and the shift) at
  that type — dropping the literal-only re-emit at the count's type, and with it the shift path's
  double emission.
- **Audit:** the checker reports every shift in the tree, conformance and examples whose type changes
  (old type = the count's type), so each is reviewed; code in the BUILDER-compiled tree must type-check
  the same under the pinned BUILDER (old rule) and the new compiler.
- **Tests:** checker and irgen unit tests; a conformance test covering every context above on all
  modes, including 32-bit targets.
