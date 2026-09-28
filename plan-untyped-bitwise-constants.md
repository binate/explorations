# Plan: bitwise operators on untyped integer constants (`~1`, `(0 - 2) | 1`)

**Status:** 🟡 spec text revision 2 + the typed-constant rule (user decision: wrap at the type) (2026-09-27); implementation done for the untyped rules; second adversarial review pending.  Tracked in `claude-todo.md`
("Spec gap: unary `~` on an untyped integer constant is undefined").

## Problem

§6.4 defines constant arithmetic at union-range precision but not the bitwise operators on a negative
untyped constant, whose value would seem to depend on a width it does not have.  The checker already
folds unary `~` as `-x-1` (`~1` is -2), but defers `&`, `|`, `^` (and `<<`) when an operand is negative —
so no fit check happens and invalid code compiles silently: `var v uint8 = (0 - 2) | 1` is -1
mathematically, yet compiles and prints 255; `(0 - 2) ^ 3` prints 253.

## Decision (user, 2026-09-27)

An untyped constant's `~` extends 1s infinitely to the left (a two's-complement value with infinite sign
extension — `~x` is `-x-1`), "and `&` and `^` the same way": every untyped constant keeps one exact,
width-independent value.  (`|` and constant shifts of a negative value follow from the same model and
are included; an `&^` operator is NOT part of this change.)

## Proposed spec text (revision 2, after the first adversarial review)

New rules in §6.4, after `const.expr.signedness`:

`const.expr.bitwise` — The bitwise operators `~`, `&`, `|`, and `^` on **untyped** integer constants
act on each constant's **two's-complement representation extended infinitely to the left**: a
non-negative value has infinitely many leading `0` bits, a negative value infinitely many leading `1`
bits. Each result is therefore an exact integer that depends on no width: `~x` is `-x - 1`, and
`a & b`, `a | b`, `a ^ b` combine the two representations bit by bit. As for every constant operation,
a result outside the union range is rejected (`const.expr.precision`); the value then fits a type, or
does not, like any other constant (`const.expr.fit`).

`const.expr.shift` — A shift that is a constant expression (both operands constants, §13.5
`expr.shift.untyped-value`) with an **untyped** value `x` and a count `k` (a negative constant count is
an error, `expr.shift.negative`) is exact for every `x` of either sign: `x << k` is `x·2^k`, and
`x >> k` is `⌊x / 2^k⌋` (rounded toward −∞, so the sign fills in). The value has no width, so
`expr.shift.overshift` does not apply: for a large `k`, `x >> k` is `0` when `x ≥ 0` and `-1` when
`x < 0`, and `0 << k` is `0`; any other result outside the union range is rejected — `1 << 64` is an
error, not `0`.

```
~1                            -> -2
~-1                           -> 0
-2 | 1                        -> -1
-2 & 0xFF                     -> 254
-2 ^ 3                        -> -3
0xFFFFFFFFFFFFFFFF & ~1       -> 2^64 - 2       (fits uint64)
-1 << 3                       -> -8
-1 << 63                      -> -2^63
-16 >> 2                      -> -4
-3 >> 1                       -> -2             (rounded toward −∞)
-1 >> 1000                    -> -1
~0xFFFFFFFFFFFFFFFF           -> -2^64          (rejected: outside the union range)
0xFFFFFFFFFFFFFFFF ^ -1       -> -2^64          (rejected)
1 << 64                       -> 2^64           (rejected)
var v uint8 = -2 | 1          -> error: -1 does not fit uint8
```

> _Note._ Of the bitwise operators, `~x` for `x ≥ 2^63` and `^` of a value `≥ 2^63` with a negative
> value — these, and only these — leave the union range (always, landing in `[-2^64, -2^63-1]`); `&` and
> `|` of in-range values stay in range.

> _Note._ Because an untyped constant keeps its exact value until it is typed, a complemented untyped
> mask does not fit an unsigned type: with `x` of any unsigned type, `x & ~1` is an error (`~1` is -2),
> and so are `var u uint8 = ~1`, `const C uint8 = ~1`, `Mask uint8 = ~(1 << iota)`, and
> `(1 << n) & ~1` in a `uint8` context (`~1` is its maximal constant subexpression, §13.5
> `expr.shift.untyped-value.typing`). Write the mask directly (`x & 0xFE`) or complement a typed
> constant (`x & ~cast(uint8, 1)`, which is `x & 254`, `const.expr.typed`). By contrast
> `m & ~(1 << n)` is valid at `m`'s type: `1 << n` is not a constant, so its `~` is taken at the type
> the expression acquires.

Cross-reference in §13.5 `expr.bitwise`: "… (`~` of a `uint8` is an 8-bit result). On an untyped
constant the bitwise operators act on its width-independent value (§6.4 `const.expr.bitwise`) —
`var u uint8 = ~1` is an error, not 254; on an untyped non-constant integer expression they act at the
type it acquires (`expr.shift.untyped-value.typing`)."  Chapter 6's intro gains "bitwise and shift" in
its list of what §6.4 covers.

The typed-constant case (`~cast(uint8, 1)`, `flags & ~FlagRead`, `cast(uint8, 1) << 8`) is the rule
`const.expr.typed` below ("Typed constants").

## Behavior changes vs the current implementation

- `&`, `|`, `^`, `<<` with a negative untyped constant operand fold exactly (were deferred, so the
  results were never fit-checked): code relying on the silent truncation — `var v uint8 = -2 | 1`
  (compiled to 255) — becomes a compile error.  In-range results (`-2 & 0xFF`) are unchanged.
- New range errors: `^` of a value `>= 2^63` with a negative value (`0xFFFFFFFFFFFFFFFF ^ -1`, 0 at
  uint64 width before), a negative left shift out of range (`-1 << 64`), and a constant `x << k` with
  `k >= 64` and `x != 0` (`1 << 64`, previously unfolded and 0).
- **Wrong-code bug fixed:** `~x` for `x >= 2^63` kept the un-complemented value, so
  `var v uint64 = ~0xFFFFFFFFFFFFFFFF` compiled to 2^64-1; it is now a range error.
- `>>` of a negative constant already rounded toward −∞; unchanged.
- Audit (2026-09-27): all toolchain commands and the 1885 single-file conformance programs compile
  identically (host and arm32) — nothing relied on the silent truncation.

## Typed constants (decided by the user, 2026-09-27: wrap at the type)

No rule defined operators on typed constants (`cast(uint8, 1)` is a typed constant by
`conv.cast.const-not-laundered`).  Decision: a typed constant behaves exactly like a value of its type —
`x + y` must not mean something different when `x` is a typed constant rather than a typed variable.
(The first review proposed Go's "exact result must fit the type"; rejected: Binate is not Go.)  This is
what the compiler already does (`~cast(uint8, 1)` is 254, `cast(uint8, 1) << 8` is 0,
`cast(uint8, 200) + cast(uint8, 100)` is 44), so it is spec text and tests only.

New rule in §6.4, after `const.expr.shift`:

`const.expr.typed` — An operator whose operands are **typed** integer constants of a type `T` (an
untyped constant operand takes `T`, and must fit it, `const.expr.fit`) is evaluated exactly as it is for
operands of type `T` that are not constants — arithmetic wraps (§13.3), `~` is the complement at `T`'s
width (`expr.bitwise`), a shift follows `expr.shift` and `expr.shift.overshift` — and its result is a
constant of type `T`. A typed constant is never evaluated at union-range precision: `cast(uint8, 200) +
cast(uint8, 100)` is `44`, `cast(uint8, 1) << 8` is `0`, and `~cast(uint8, 1)` is `254`, exactly as for
`uint8` variables holding those values. (A constant division or remainder by zero is still a
compile-time error, §13.4.)

The untyped-mask Note then also offers `x & ~cast(uint8, 1)` (a typed constant, complemented at `uint8`:
`x & 254`).

## Implementation sketch

- `pkg/binate/bignum`: `And` / `Or` / `Xor` / a new `Not` on the infinite two's-complement value
  (a 65-bit view: sign bit + 64 bits covers the union range), reporting `ok=false` outside it; `Shl` of a
  negative value exact (`x·2^k`, range-checked).
- Checker: `foldIntBitwise` and unary `~` fold every case exactly through bignum (no deferral).
- Audit: compile the tree and the conformance corpus before/after and compare compile failures — new
  failures are code relying on the silent truncation.
- Tests: bignum and checker unit tests; spec conformance (values, fit errors, range errors).
