# Plan: `strconv` `Parse...` series (`Parse{Bool,Int,Uint,Float}` + `Atoi`)

Status: PROPOSED (2026-06-03). Driver: complete `pkg/std/strconv` with the
string→value direction, mirroring Go's `strconv`. The `Format.../Append...`/
`Itoa` (value→string) direction already landed; this is its inverse.

## Decided constraints

- **Input type**: `s *[]readonly uint8` — the read-only byte view of a string.
  (The existing strconv uses `*[]readonly char`; `char`/`uint8` are the same
  byte type, but the Parse surface standardizes on `uint8` per the API owner.
  A `streq`-style helper already operates on `*[]readonly char`, so the impl
  may need to treat the two interchangeably or add a `uint8` overload of the
  internal comparison helpers — confirm `char`≡`uint8` assignability when
  wiring.)
- **Error handling** (the crux — Binate has no `error` type yet): a TEMPORARY
  concrete error type `@NumError` with an `Error() @[]char` method, shaped to
  be source-compatible with the planned `pkg/std/errors.Error` interface
  (`interface Error { Error() @[]char; … }`, see `plan-std-errors.md`). When
  `std/errors` lands, the Parse return types swap `@NumError → @errors.Error`
  and the `ErrSyntax`/`ErrRange` kinds become sentinel errors comparable via
  `errors.Is`; call sites (`err != nil`, `err.Error()`) are unaffected.
- **`ParseFloat` is the correct decimal→double** (exact, over `pkg/std/math/big`
  — NOT the 128-bit-window approximation in `common.ParseFloatLitToBits`). It
  is the canonical inverse-of-`FormatFloat` and the home the compiler's
  float-literal converter should eventually route through (fixing the round-bit
  dtoa bug; see `claude-todo.md`), once stdlib is bundled with the BUILDER.

## 1. The temporary error type

```binate
// strconv.bni
// ErrSyntax / ErrRange classify a failed parse (mirrors Go's
// ErrSyntax/ErrRange sentinels; here a Kind int until std/errors lands).
const ErrSyntax int = 1   // the input was not valid syntax for the target
const ErrRange  int = 2   // the value was syntactically valid but out of range

// NumError records a failed numeric conversion (mirrors Go's strconv.NumError:
// the function, the input, and the failure kind).  TEMPORARY: once
// pkg/std/errors exists, this implements errors.Error and the Parse functions
// return @errors.Error instead.
type NumError struct {
    Func @[]char   // the failing function name, e.g. "ParseInt"
    Num  @[]char   // a copy of the offending input text
    Kind int       // ErrSyntax | ErrRange
}

// Error renders "strconv.<Func>: parsing <quoted Num>: <reason>".  This is the
// method that makes NumError forward-compatible with errors.Error.
func (e @NumError) Error() @[]char

// IsRange / IsSyntax: classify a returned error (the interim stand-in for
// errors.Is(err, strconv.ErrRange)).  nil → false.
func IsRange(e @NumError) bool
func IsSyntax(e @NumError) bool
```

- **Success is a nil `@NumError`.** `@NumError` is a managed pointer; the
  no-error return is `nil`. Callers test `if err != nil { … }`.
- On `ErrRange`, the value return is the saturated extreme (Go's behavior:
  `ParseInt` returns the min/max for the bitSize, `ParseFloat` returns ±Inf/0),
  so a caller that ignores the error still gets a sane clamp.
- `NumError` lives in its own file `num_error.bn`; the migration to
  `std/errors` touches only this file + the `.bni` return types.

## 2. Signatures (in `strconv.bni`)

```binate
// base: 0 = infer from prefix (0x/0o/0b → 16/8/2, leading 0 NOT octal —
//        match Go's modern base-0; plain digits → 10); else 2..36.
// bitSize: 0 / 8 / 16 / 32 / 64 — the result must fit a signed/unsigned int of
//          that width (0 means the platform `int`, 32-bit on ILP32).  Out of
//          range → ErrRange + the clamped extreme.
func ParseInt(s *[]readonly uint8, base int, bitSize int) (int64, @NumError)
func ParseUint(s *[]readonly uint8, base int, bitSize int) (uint64, @NumError)

// ParseFloat: bitSize 32 rounds to the nearest float32 (returned widened to
// float64, exactly as Go); 64 = nearest float64.  Accepts an optional sign,
// decimal/fraction/exponent, and the special forms "inf"/"+Inf"/"-Inf"/"nan"
// (case-insensitive).  Hex-float syntax ("0x1.8p3") is a documented follow-up,
// not in the first cut.
func ParseFloat(s *[]readonly uint8, bitSize int) (float64, @NumError)

// ParseBool accepts 1,t,T,TRUE,true,True and 0,f,F,FALSE,false,False (Go's set).
func ParseBool(s *[]readonly uint8) (bool, @NumError)

// Atoi is ParseInt(s, 10, 0) specialized to the platform int (with a fast
// digit-loop path for the common short case).
func Atoi(s *[]readonly uint8) (int, @NumError)
```

## 3. Integer parsing (`atoi.bn`)

- Shared core `parseUint(s, base) (uint64, kind)`: validate non-empty, optional
  base inference, accumulate digits in `uint64` with **overflow detection**
  (`acc > (cutoff)` before the multiply, à la Go) → `ErrRange`; reject a
  non-digit / digit ≥ base → `ErrSyntax`.
- `ParseUint` = core + bitSize range-check (`> (1<<bitSize)-1` → ErrRange,
  clamp to max).
- `ParseInt` = optional leading `+`/`-`, then the unsigned core on the rest,
  then the **signed** bitSize range-check (`[-2^(n-1), 2^(n-1)-1]`); INT_MIN is
  the usual `uint64` boundary case (mirror `FormatInt`'s INT64_MIN handling).
- `Atoi` = `ParseInt(s, 10, 0)` → `int`, with a short-string fast path.
- All limb/accumulator math stays in `uint64` (ILP32: never route the
  accumulator through `int`).

## 4. Bool parsing (`parse_bool.bn`)

Trivial table match against the accepted literals; anything else → `ErrSyntax`.

## 5. Float parsing (`parse_float.bn`) — over `pkg/std/math/big`

The exact, correctly-rounded decimal→double (Go's slow path, which we can
afford as the only path since `big` is available here):

1. Lex `[sign] digits [. digits] [ (e|E) [sign] digits ]` (+ inf/nan forms).
   Accumulate the significand as a `big.Nat` (no precision cap), track the
   decimal exponent `dexp`.
2. value = `mant × 10^dexp`. For `dexp ≥ 0`: `M = mant × 10^dexp` (a `Nat`);
   the double is `M` rounded to 53 bits — `BitLen(M)`, take the top 53, round
   to nearest-even from the exact dropped low bits. For `dexp < 0`:
   value = `mant / 10^(-dexp)`; scale the numerator by `2^k` so the quotient
   lands in `[2^52, 2^53)`, `DivMod`, round-to-even from the exact remainder vs
   `den/2`. Handle overflow→±Inf and underflow→±denormal/0.
3. For `bitSize == 32`: round the resulting double to nearest float32 (reuse
   the verified `common.F64BitsToF32Bits`, or compute the 24-bit round directly
   from the `Nat`), then widen back to float64 for the return — matching Go.
4. `Inf`/`NaN` literals short-circuit to the bit patterns.

**Correctness**: differential-test against Go `strconv.ParseFloat` exactly the
way `FormatFloat` was validated — millions of random + structured + tie-prone
inputs, both bitSizes, signs, denormal/overflow boundaries. This is the
table-maker's-dilemma-free converter.

**Tie-in (claude-todo)**: once stdlib is bundled with the BUILDER, the
compiler's `common.ParseFloatLitToBits` (the 128-bit-window approximation, 1
ULP low for ~38+ sig-digit just-above-tie literals) can route through
`ParseFloat`'s core (or share it), retiring the round-bit bug and the
duplicate converter.

## 6. File layout

```
ifaces/stdlib/pkg/std/strconv.bni        # + NumError, ErrSyntax/ErrRange, the 5 Parse decls
impls/stdlib/common/pkg/std/strconv/
    num_error.bn        # NumError + Error() + IsRange/IsSyntax  (the swap-point for std/errors)
    atoi.bn             # parseUint core + ParseInt/ParseUint/Atoi
    parse_bool.bn       # ParseBool
    parse_float.bn      # ParseFloat over big.Nat
    *_test.bn           # per-file unit tests
conformance/
    NNN_strconv_parse_cross_pkg.bn + .expected   # cross-package consumer (pick a free number)
```

`pkg/std/strconv` already imports `pkg/std/math/big` (for `FormatFloat`), so
`ParseFloat` adds no new dependency.

## 7. Test strategy

- **Integers/bool**: golden tables (valid, each base, base-0 inference,
  bitSize range edges incl. the signed min/max and INT64_MIN, leading
  +/-, empty/`ErrSyntax` cases, `ErrRange` cases with the clamped extreme).
- **Float**: a Go differential (the gold standard from `FormatFloat`) +
  golden specials (inf/nan/signed-zero) + the round-trip property
  `ParseFloat(FormatFloat(x)) == x` across a sweep.
- **Error type**: `Error()` message format goldens; `IsRange`/`IsSyntax`.
- **Cross-package conformance**: a `main` consumer that imports strconv and
  exercises one of each Parse + an error path.

## 8. Implementation order (each keeps the tree green)

1. `num_error.bn` (NumError + Error() + classifiers) + `.bni` decls.
2. `parse_bool.bn` (warm-up).
3. `atoi.bn` (ParseUint core → ParseInt/ParseUint/Atoi) + range/overflow tests.
4. `parse_float.bn` (the meaty one) + the Go differential.
5. Cross-package conformance + final sweep.

## 9. Open questions / decisions to confirm at implementation time

- **`char` vs `uint8`** for the input slice and the internal compare helpers —
  confirm assignability so string literals (`*[]readonly char`) pass cleanly to
  a `*[]readonly uint8` parameter (or settle on one type for the whole Parse
  surface).
- **Hex floats** (`0x1.8p3`) and **underscored digits** (`1_000`) — Go's
  `ParseFloat`/`ParseInt` accept these; proposed as explicit follow-ups, not
  the first cut (the lexer/converter don't handle them today either).
- **`NumError.Num` copy cost** — Go stores the input substring; here we copy
  into an owned `@[]char`. Fine for an error path (cold), but note it allocates.
