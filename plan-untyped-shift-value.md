# Plan: the type of an untyped constant shift VALUE (`1 << n`)

**Status:** 🟡 spec text revision 3 after two adversarial reviews (2026-09-27); ready to write into the spec.  Tracked in
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

## Proposed spec text (revision 3, after the second adversarial review)

New rules in §13.5, after `expr.shift` (rule-IDs declared at column 0 in the spec itself):

`expr.shift.untyped-value` — A shift is a **constant expression** exactly when both its value and its
count are constants (§6.4). A shift that is not a constant expression and whose value operand is
**untyped** — an untyped integer constant (§6.1 `const.untyped.coercion`: a literal, an untyped
constant expression, or an untyped `const` name) or an *untyped non-constant integer expression* — is
itself an **untyped non-constant integer expression**. So are: a parenthesized untyped non-constant
integer expression; a unary `-` or `~` applied to one; and a binary arithmetic (`+ - * / %`) or bitwise
(`& | ^`) operator whose operands are each an untyped integer constant or an untyped non-constant
integer expression, at least one being the latter. (`(1 << n) << 2` is one: its count is constant but
its value is not.) An arithmetic or bitwise operator combining an untyped non-constant integer
expression with an untyped floating-point, boolean, or string constant is an error (§6.5).

`expr.shift.untyped-value.typing` — An untyped non-constant integer expression is typed **exactly as an
untyped integer constant in the same position would be** (Ch.6, §8.1). For example, it takes the type
its context requires at a conversion boundary (§8.8 `conv.boundaries`: a variable, field, element,
parameter — the element type `T` for a variadic trailing argument — or result), from the other operand
of an enclosing binary arithmetic, bitwise, or comparison operator when that operand is typed (the
typed operand wins: in `var w int64 = x + (1 << n)` with `x int32` the shift is `int32`), from the tag of
the enclosing `switch` for a case expression, or as the target of an enclosing `cast` / `unsafe_cast`;
where the required type is an interface type (`*I`, `@I`, `*any`, `@any`) it takes its default type
`int` and then converts as an `int` value would (§11.4); and where no type is required it takes its
**default type `int`** (§6.2) — for example in a short or untyped variable declaration (`x := e`,
`var x = e`), a blank `_ = e` assignment, a `switch` tag, an index, sub-slice bound, `make_slice` length,
`unsafe_index` index, shift count, `bit_cast` / `box` operand, `__c_call` argument, a selector receiver,
or a comparison against another untyped operand. (A type-parameter target takes whatever an untyped
constant takes there, Ch.12.)

The type flows down **only through the operands that make up the expression** — the operands of `-`,
`~`, and the arithmetic and bitwise operators, and the value operand of each shift; a shift count is its
own position (`int` by default when it is untyped: in `var u uint8 = 1 << (1 << n)` the inner shift is
`int`). Every shift reached this way produces a result of that type; every **maximal untyped constant
subexpression** reached this way — including each shift's value operand — is evaluated as a constant
expression (§6.4) and its **value must fit** that type (`const.expr.fit`): into a `uint8`,
`(1 << n) + (300 - 100)` is valid (200 fits) and `(1 << n) + (0 - 1)` is an error (-1 does not). A
shift count's type never contributes. The shifted result is not a constant: it is computed at that
type, with `expr.shift.overshift`.

`expr.shift.untyped-value.integer` — The type so determined must be an **integer type** (an integer
type, or a named-distinct, alias, or `readonly` type whose underlying type is one); otherwise it is an
error — the value of a shift must be an integer. `cast(float64, 1 << n)` and `var f float64 = 1 << n`
are errors; write the width explicitly, `cast(float64, cast(int64, 1) << n)`. An untyped floating-point,
boolean, or string constant is never a valid shift value (`1.0 << n` is an error).

`expr.shift.untyped-value.not-const` — Such an expression is not a constant, so it cannot appear where a
constant is required: `const C = 1 << n` and `const C int64 = 1 << n` are errors, as is an array length
`[1 << n]T`. `1 << iota` is a constant shift and is unaffected.

`expr.shift.untyped-value.unsafe` — `unsafe_shl(v, c)` / `unsafe_shr(v, c)` (§15.8 `builtin.internal`)
are typed and folded exactly like `v << c` / `v >> c`: a constant expression exactly when both operands
are constants, and an untyped non-constant integer expression under the same conditions as a shift.

> _Example._ With `var n uint8 = 9`, `var m uint8 = 0xF0`, `var x int32 = 1`:
>
> ```
> var a uint8 = 1 << n                  // uint8: 0 (overshift at 8 bits)
> var b int64 = 1 << n                  // int64: 512
> c := 1 << n                           // int: 512 (default type)
> var d int64 = cast(int64, (0 + 1) << n) // int64: 512 — a constant-expression value types like a literal
> var e uint8 = m & ~(1 << n)           // uint8: the shift and `~` are computed at m's type (0xF0)
> var f uint8 = (1 << n) << 2           // uint8
> var g int64 = x + (1 << n)            // error: the shift takes x's type int32; int32 is not assignable to int64
> var h uint8 = (1 << n) + 300          // error: 300 does not fit uint8
> var i int8 = -1 << n                  // int8: `-1 << n` is `(-1) << n`, and -1 fits int8
> var j int64 = 0x100000000 << n        // int64, on every target
> k := 0x100000000 << n                 // error on a 32-bit target: 0x100000000 does not fit int
> var l uint8 = 256 << n                // error: 256 does not fit uint8
> var p float64 = cast(float64, 1 << n) // error: the value would be float64
> fmt.Print(1 << n)                     // int (an interface parameter: the default type)
> ```
>
> Precedence (§13.2): `1 << n + 1` is `1 << (n + 1)`; `x + 1 << n` is `(x + 1) << n`, whose value is typed,
> so these rules do not apply.

Cross-references: §6.1 `const.untyped` ("… takes a type from its context … (and the value operand of a
non-constant shift, §13.5 `expr.shift.untyped-value`)"); §8.1 `conv.assignable` case 2, a note that an
untyped non-constant integer expression is typed from `D` first and then checked under case 1; §15.8
`builtin.internal` for `unsafe_shl` / `unsafe_shr`.  Spec mechanics: declare the rule-IDs at column 0,
regenerate `rule-ids.txt`, add Annex C entries (Draft until implemented), tag the conformance test for
spec-coverage.

Separately (not part of this change): §6.4 does not define unary `~` on an untyped constant, whose value
depends on the width (`~1`); the checker defers that fold until the type is known.  Filed as its own
spec item.

## First adversarial review (2026-09-27) — outcomes

Verdict "sound decision, rule text needs rework"; all findings taken:
- The closed list of contexts contradicted "as if replaced by the value operand alone" (`m & ~(1 << n)`
  and `(1 << n) << m` came out `int`): the text now defines an *untyped non-constant integer expression*,
  recursive through unary `-`/`~`, arithmetic/bitwise operators and shift values, typed exactly like an
  untyped constant in the same position; the context list is illustrative (§8.8 `conv.boundaries`).
- "If the count is also a constant, the shift is a constant expression" was false for `(1 << n) << 2`.
- Non-integer contexts are an **error** (was: default `int`).  That is the literal reading of "take the
  type from the context" (the context type is float64, and a float cannot be shifted); it matches
  `conv.cast.const-not-laundered` (a constant cast converts the untyped value directly — the draft made
  `cast(float64, 0x80000000 >> n)` an error on arm32 only); it avoids a 32-bit footgun
  (`cast(float64, 1 << n)` silently 0.0 for n >= 32 on arm32); and it is forward-compatible (an error can
  be relaxed later without breaking code, and stays consistent if §6.5's no-int-mix is relaxed).
- Added: `unsafe_shl`/`unsafe_shr`, the fit of every constant operand against the final type, untyped
  float/bool/string values are errors, constant-required positions, the `conv.assignable` note, a
  definition of "integer type" (incl. named / alias / `readonly`), valid-statement examples with
  precedence cases.

## Second adversarial review (2026-09-27) — outcomes

"Needs another short pass" — taken: interface targets take the default `int` (not an error); the fit
rule is per maximal untyped constant subexpression (§6.4), not per leaf; the type flows down only through
operand positions, never into a shift count; `unsafe_shl`/`unsafe_shr` are in the definition with their
own rule-ID; a mixed untyped non-constant integer / untyped float operator is an error; the position lists
are illustrative; example `e`'s comment no longer relies on `~` of an untyped constant.

## Behavior changes vs the current implementation

- The shift no longer takes the count's type: `x := 1 << k` (k uint8) becomes `int`, `var y uint8 = 1
  << k` (k int) becomes valid (was a type error), and `cast(int64, 1 << n)` on a 32-bit target shifts at
  64 bits (was 32).
- A constant-expression / const-name value agrees with a literal value in every context.
- New compile errors: a non-integer context (`cast(float64, 1 << n)`, previously accepted and typed
  from the count), a value constant that does not fit the context type (`var u uint8 = 256 << n`), and a
  non-constant shift in a constant-required position.

## Implementation sketch

- **Checker:** an untyped non-constant integer expression gets an untyped integer type that is marked
  non-constant — distinct from the existing value-less untyped int used for constant folds the checker
  defers (e.g. a bitwise op on a negative constant), which remain constants.  Operators combining it
  with untyped operands keep it untyped non-constant; at every point where an untyped operand gets its
  final type (the §8.8 boundaries incl. variadic element and positional struct-literal fields — which
  are otherwise unchecked — operator peers, switch cases, `cast` / `unsafe_cast`, `unsafe_shl`/`shr`),
  the final type is recorded down the expression onto each node, and every untyped constant operand's
  value is fit-checked against it; a non-integer final type is an error.  Nodes never given a type are
  `int` (the default), checked for fit the same way.
- **IR-gen:** reads each such node's recorded type and emits the constants and the ops at that type —
  dropping the literal-only re-emit at the count's type, and with it the shift path's double emission.
- **Audit:** the checker reports every shift in the tree, conformance and examples whose type changes
  (old type = the count's type), flagging narrowing and signedness flips specifically (e.g. `x := 1 << k`
  with `k uint32` becomes a signed 32-bit `int` on arm32: n >= 32 now overshifts, `1 << 31` is negative),
  and `__c_call` arguments whose C type changes.  Code in the BUILDER-compiled tree must type-check the
  same under the pinned BUILDER (old rule) and the new compiler.
- **Tests:** checker and irgen unit tests; a conformance test covering every context above on all modes,
  including native arm32 and the 32-bit cases.
