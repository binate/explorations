# Plan: bitwise operators on untyped integer constants (`~1`, `(0 - 2) | 1`)

**Status:** 🟡 spec text drafted, adversarial review pending (2026-09-27).  Tracked in `claude-todo.md`
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

## Proposed spec text

New rule in §6.4, after `const.expr.signedness`:

`const.expr.bitwise` — The bitwise operators `~`, `&`, `|`, and `^` on untyped integer constants act on
each constant's **two's-complement representation extended infinitely to the left**: a non-negative
value has infinitely many leading `0` bits, a negative value infinitely many leading `1` bits. Each
result is therefore an exact integer that depends on no width: `~x` is `-x - 1`, and `a & b`, `a | b`,
`a ^ b` combine the two representations bit by bit. A constant shift of a negative value is exact in
the same way: `x << k` is `x·2^k`, and `x >> k` is `⌊x / 2^k⌋` (the sign fills in). As for every
constant operation, a result outside the union range is rejected (`const.expr.precision`); of the
bitwise operators only `~x` for `x ≥ 2^63` and `^` of a value `≥ 2^63` with a negative value can leave
it. The value then fits a type, or does not, like any other constant (`const.expr.fit`).

```
~1                            -> -2
~(0 - 1)                      -> 0
(0 - 2) | 1                   -> -1
(0 - 2) & 0xFF                -> 254
(0 - 2) ^ 3                   -> -3
0xFFFFFFFFFFFFFFFF & ~1       -> 2^64 - 2       (fits uint64)
(0 - 1) << 3                  -> -8
(0 - 16) >> 2                 -> -4
~0xFFFFFFFFFFFFFFFF           -> -2^64          (rejected: outside the union range)
0xFFFFFFFFFFFFFFFF ^ (0 - 1)  -> -2^64          (rejected)
var v uint8 = (0 - 2) | 1     -> error: -1 does not fit uint8
```

> _Note._ A complemented mask for a narrow type is typically written with a typed constant: with
> `x uint8`, `x & ~1` is an error (`~1` is -2, which does not fit `uint8`); `x & ~cast(uint8, 1)` takes
> the complement at `uint8` (`expr.bitwise`) and is `x & 254`, as is `x & 0xFE`.

Cross-reference in §13.5 `expr.bitwise`: "… (`~` of a `uint8` is an 8-bit result); on untyped
constants the bitwise operators are exact, §6.4 `const.expr.bitwise`."

## Behavior changes vs the current implementation

- `&`, `|`, `^`, `<<` with a negative untyped constant operand fold exactly (were deferred, so their
  results were never fit-checked): code that relied on the silent truncation — `var v uint8 = (0 - 2) |
  1` — becomes a compile error.  `(0 - 2) & 0xFF` and other in-range results are unchanged.
- `~0xFFFFFFFFFFFFFFFF` (and `~x` for any `x >= 2^63`) becomes a range error (the checker kept the
  un-complemented value there).

## Implementation sketch

- `pkg/binate/bignum`: `And` / `Or` / `Xor` / a new `Not` on the infinite two's-complement value
  (a 65-bit view: sign bit + 64 bits covers the union range), reporting `ok=false` outside it; `Shl` of a
  negative value exact (`x·2^k`, range-checked).
- Checker: `foldIntBitwise` and unary `~` fold every case exactly through bignum (no deferral).
- Audit: compile the tree and the conformance corpus before/after and compare compile failures — new
  failures are code relying on the silent truncation.
- Tests: bignum and checker unit tests; spec conformance (values, fit errors, range errors).
