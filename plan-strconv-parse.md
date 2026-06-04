# Plan: `strconv` `Parse...` series (`Parse{Bool,Int,Uint,Float}` + `Atoi`)

Status: IN PROGRESS (2026-06-03). Driver: complete `pkg/std/strconv` with the
string→value direction, mirroring Go's `strconv`. The `Format.../Append...`/
`Itoa` (value→string) direction already landed; this is its inverse.

## Error handling — `pkg/std/errors` is landed, so no temporary type

This plan was first drafted assuming `pkg/std/errors` did not exist yet, which
called for a throwaway concrete `@NumError` to be swapped out later. **That is
obsolete**: `pkg/std/errors` landed (commit `626c39f9`) and is functional
(conformance `545_present_iface`, `577_std_errors`):

- `interface Error { Error() @[]char; Unwrap() @Error }`, plus `New`/`Wrap`.
- the `present()` builtin: the "no error" value is the **empty** `@errors.Error`
  (the zero value `var e @errors.Error`), tested with `!present(err)` — there is
  **no `nil`** for interface values.
- the concrete-impl pattern is established: `type leafError struct{…}` with
  methods on `*leafError` + `impl *leafError : Error`, boxed via
  `var e @Error = le`.

So the Parse functions **return `@errors.Error` directly** — final signatures,
no future swap. Internally a single unexported concrete `numError` carries the
structured failure (function, input, kind) and implements `errors.Error`, giving
Go-style messages now while keeping the `kind` captured for later.

### What is NOT yet possible

Programmatic syntax-vs-range classification (Go's `errors.Is(err, ErrRange)`)
needs sentinel-identity comparison or RTTI/`errors.As` to read `kind` back out
of the interface — and `plan-std-errors.md` **explicitly defers `Is`/`As`** (no
RTTI yet). So in v1 a caller can only:

- `present(err)` — did it fail?
- `err.Error()` — the message text distinguishes "invalid syntax" from "value
  out of range" for a human.
- `err.Unwrap()` — walk the (here always-leaf) chain.

The `kind` field is captured but **dormant** until `errors.Is` lands, at which
point exported `ErrSyntax`/`ErrRange` sentinels + classification are added
**purely additively** — no change to the Parse signatures or the stored data.

On `ErrRange` the value return is still the **saturated extreme** (Go's
behavior: `ParseInt` → min/max for the bitSize, `ParseFloat` → ±Inf/0), so a
caller that ignores the error still gets a sane clamp.

## Decided constraints

- **Input type**: `s *[]readonly uint8` — the read-only byte view of a string.
  Resolved open question: `char = uint8` is a **true alias**
  (`claude-notes.md:377`), so `*[]readonly char` ≡ `*[]readonly uint8`; string
  literals (natural type `char`) pass to a `uint8` parameter for free, and the
  internal compare helpers can be written against either spelling. The Parse
  surface standardizes on `uint8`.
- **`ParseFloat` is the exact decimal→double** over `pkg/std/math/big` — NOT the
  128-bit-window approximation in `common.ParseFloatLitToBits` (which is 1 ULP
  low for ~38+ sig-digit just-above-tie literals; see `claude-todo.md`). It is
  the canonical inverse-of-`FormatFloat`, and the home the compiler's
  float-literal converter should eventually route through (retiring the
  round-bit dtoa bug), once stdlib is bundled with the BUILDER.

## 1. The concrete error type (`num_error.bn`, unexported)

`numError` is an internal impl detail — it does **not** appear in `strconv.bni`.
Only `@errors.Error` crosses the package boundary.

```binate
// num_error.bn  (package "pkg/std/strconv")
import "pkg/std/errors"

// kindSyntax / kindRange classify a failed parse (mirrors Go's
// ErrSyntax/ErrRange).  Internal until pkg/std/errors gains Is/As; then
// they become the exported sentinels a caller compares with errors.Is.
const kindSyntax int = 1   // input was not valid syntax for the target
const kindRange  int = 2   // syntactically valid but out of range

// numError records a failed numeric conversion (mirrors Go's
// strconv.NumError: the function, the input, the failure kind).  It
// implements errors.Error; callers see only @errors.Error.
type numError struct {
    fn   @[]char   // failing function, e.g. "ParseInt"
    num  @[]char   // a copy of the offending input text
    kind int       // kindSyntax | kindRange
}

// Error renders "strconv.<fn>: parsing <quoted num>: <reason>".
func (e *numError) Error() @[]char { … }

// Unwrap: numError is always a leaf — returns an empty @errors.Error.
func (e *numError) Unwrap() @errors.Error { var none @errors.Error; return none }

impl *numError : errors.Error

// syntaxErr / rangeErr build a boxed @errors.Error for the two kinds.
// They copy the input (the input slice borrows; the error outlives it).
func syntaxErr(fn @[]char, s *[]readonly uint8) @errors.Error { … }
func rangeErr(fn @[]char, s *[]readonly uint8)  @errors.Error { … }
```

- **Success is an empty `@errors.Error`** (`var ok @errors.Error; return val, ok`).
  Callers test `if present(err) { … }`.
- The `Error()` message format mirrors Go for familiarity and good diagnostics.
- The migration to exported `ErrSyntax`/`ErrRange` sentinels (when `errors.Is`
  exists) touches only this file + adds decls to the `.bni`; the Parse
  signatures and call sites are unaffected.

## 2. Signatures (in `strconv.bni`)

```binate
import "pkg/std/errors"

// base: 0 = infer from prefix (0x/0o/0b → 16/8/2, leading 0 NOT octal —
//        match Go's modern base-0; plain digits → 10); else 2..36.
// bitSize: 0 / 8 / 16 / 32 / 64 — the result must fit a signed/unsigned int of
//          that width (0 means the platform `int`, 32-bit on ILP32).  Out of
//          range → kindRange + the clamped extreme.
func ParseInt(s *[]readonly uint8, base int, bitSize int) (int64, @errors.Error)
func ParseUint(s *[]readonly uint8, base int, bitSize int) (uint64, @errors.Error)

// ParseFloat: bitSize 32 rounds to the nearest float32 (returned widened to
// float64, exactly as Go); 64 = nearest float64.  Accepts an optional sign,
// decimal/fraction/exponent, and the special forms "inf"/"+Inf"/"-Inf"/"nan"
// (case-insensitive).  Hex-float syntax ("0x1.8p3") is a documented follow-up,
// not in the first cut.
func ParseFloat(s *[]readonly uint8, bitSize int) (float64, @errors.Error)

// ParseBool accepts 1,t,T,TRUE,true,True and 0,f,F,FALSE,false,False (Go's set).
func ParseBool(s *[]readonly uint8) (bool, @errors.Error)

// Atoi is ParseInt(s, 10, 0) specialized to the platform int (with a fast
// digit-loop path for the common short case).
func Atoi(s *[]readonly uint8) (int, @errors.Error)
```

## 3. Integer parsing (`atoi.bn`)

- Shared core `parseUintCore(s, base) (uint64, int /*kind, 0=ok*/)`: validate
  non-empty, optional base inference, accumulate digits in `uint64` with
  **overflow detection** (`acc > cutoff` before the multiply, à la Go) →
  `kindRange`; reject a non-digit / digit ≥ base → `kindSyntax`.
- `ParseUint` = core + bitSize range-check (`> (1<<bitSize)-1` → range, clamp to
  max).
- `ParseInt` = optional leading `+`/`-`, then the unsigned core on the rest,
  then the **signed** bitSize range-check (`[-2^(n-1), 2^(n-1)-1]`); INT_MIN is
  the usual `uint64` boundary case (mirror `FormatInt`'s INT64_MIN handling).
- `Atoi` = `ParseInt(s, 10, 0)` → `int`, with a short-string fast path.
- All limb/accumulator math stays in `uint64` (ILP32: never route the
  accumulator through `int`).

## 4. Bool parsing (`parse_bool.bn`)

Trivial table match against the accepted literals; anything else → `kindSyntax`.

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
compiler's `common.ParseFloatLitToBits` (the 128-bit-window approximation) can
route through `ParseFloat`'s core (or share it), retiring the round-bit bug and
the duplicate converter.

## 6. File layout

```
ifaces/stdlib/pkg/std/strconv.bni        # + the 5 Parse decls (numError stays internal)
impls/stdlib/common/pkg/std/strconv/
    num_error.bn        # numError + Error()/Unwrap() + impl + syntaxErr/rangeErr
    atoi.bn             # parseUintCore + ParseInt/ParseUint/Atoi
    parse_bool.bn       # ParseBool
    parse_float.bn      # ParseFloat over big.Nat
    *_test.bn           # per-file unit tests
conformance/
    NNN_strconv_parse_cross_pkg.bn + .expected   # cross-package consumer (pick a free number)
```

`pkg/std/strconv` already imports `pkg/std/math/big` (for `FormatFloat`); adding
`import "pkg/std/errors"` is the only new dependency.

## 7. Test strategy

- **Integers/bool**: golden tables (valid, each base, base-0 inference,
  bitSize range edges incl. the signed min/max and INT64_MIN, leading
  +/-, empty/`kindSyntax` cases, `kindRange` cases with the clamped extreme).
- **Float**: a Go differential (the gold standard from `FormatFloat`) +
  golden specials (inf/nan/signed-zero) + the round-trip property
  `ParseFloat(FormatFloat(x)) == x` across a sweep.
- **Error**: `Error()` message-format goldens; `present` on success vs failure.
- **Cross-package conformance**: a `main` consumer that imports strconv and
  exercises one of each Parse + an error path (`present(err)` + `err.Error()`).

## 8. Implementation order (each keeps the tree green)

1. `num_error.bn` (numError + Error()/Unwrap() + impl + syntaxErr/rangeErr) +
   the `import "pkg/std/errors"` in `.bni`.
2. `parse_bool.bn` (warm-up).
3. `atoi.bn` (parseUintCore → ParseInt/ParseUint/Atoi) + range/overflow tests.
4. `parse_float.bn` (the meaty one) + the Go differential.
5. Cross-package conformance + final sweep.

## 9. Open questions / decisions to confirm at implementation time

- **Hex floats** (`0x1.8p3`) and **underscored digits** (`1_000`) — Go's
  `ParseFloat`/`ParseInt` accept these; proposed as explicit follow-ups, not
  the first cut (the lexer/converter don't handle them today either).
- **`numError.num` copy cost** — Go stores the input substring; here we copy
  into an owned `@[]char`. Fine for an error path (cold), but note it allocates.
