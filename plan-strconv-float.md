# Plan: `pkg/std/math/big.Nat` + `strconv.AppendFloat`/`FormatFloat` (Dragon4 dtoa)

Status: **COMPLETE** (shipped 2026-06-03); kept for design rationale. Delivered
`pkg/std/math/big.Nat` (full unsigned bignum, Knuth-D division, ILP32-correct)
and `strconv.AppendFloat`/`FormatFloat` in both shortest-round-trip (`prec < 0`)
and fixed-precision (`prec >= 0`) modes, for `'f'`/`'e'`/`'E'`/`'g'`/`'G'`.
Validated by a 208k-case differential against Go go1.26.3 (0 mismatches).
Cross-package conformance: `conformance/535_strconv_float_cross_pkg`.

Explicit follow-ups still open (intentionally out of this work): signed `Int`
wrapping `Nat` to replace `pkg/binate/bignum` (see "Noted for later"); `'b'`/`'x'`
float formats (Q2); `println` rewiring off `bootstrap.formatFloat` (Q3). A
separate VM defect surfaced during testing — large-exponent float64 *constants*
load imprecisely on the bytecode VM — tracked in `claude-todo.md`
(`conformance/536_float_lit_large_exp`).

Original driver: extend `pkg/std/strconv` with float formatting. Prerequisite:
a `pkg/std/math/big.Nat` arbitrary-precision unsigned integer (a standalone Tier-1
stdlib deliverable in its own right).

## Decided constraints

- New package path **`pkg/std/math/big`** (Tier 1). Interface at the flat file
  `ifaces/stdlib/pkg/std/math/big.bni`; impl under
  `impls/stdlib/common/pkg/std/math/big/*.bn`. This introduces `math/` as a Tier-1
  namespace alongside `std/` (the layout spec's Tier-1 examples are all
  `pkg/std/X` — update `pkg-layout-spec.md` to note Go-style top-level stdlib
  namespaces like `math/` are also Tier-1).
- Scope **now**: a `Nat` (arbitrary-precision unsigned). **TODO later**: a
  signed `Int` wrapping `Nat` (to eventually replace `pkg/binate/bignum`).
  **Out of scope**: `Rat`; `Float` (`math/big.Float`).
- A Tier-1 package may depend on Tier-1x (`pkg/stdx/...`) when the dependency is
  **purely internal** (no `stdx` type in the public `.bni`). `Nat` may use
  `pkg/stdx/slices` internally.
- dtoa algorithm: **Dragon4 / Steele-White / Burger-Dybvig** exact method over
  `Nat` (not Ryū, no precomputed tables). Handles shortest-round-trip
  (`prec == -1`) **and** fixed precision (`prec >= 0`).

## Key design decisions (adjudicated against the codebase)

- **`@Nat` managed-pointer with mutating receivers**, not a value struct. The
  house idiom for a *mutable heap-owning* struct is the managed-pointer
  receiver over an owned `@[]T` field — `@Assembler`/`@Section`
  (`pkg/binate/asm.bni:46-67`, `pkg/binate/asm/asm.bn:185-210`) mutate
  `s.Data` in place. (`buf.CharBuf` is a *value* struct, but it's an
  immutable-builder, not a tight-loop scratch object.) Dragon4 mutates a few
  scratch Nats (`R`, `S`, `M+`, `M-`) thousands of times per format, so a
  z-destination mutating API (Go's `nat` idiom) is correct.
- **Ship the proper unsigned-bignum op set**, not a Dragon4-only minimal
  subset. `Nat` is a Tier-1 type an `Int` will wrap; `Mul`/`DivMod` are core to
  *that* contract regardless of dtoa. Keep small-operand fast paths
  (`MulUint32`, `DivModUint32`) that the dtoa hot loop genuinely uses, and ship
  full `Mul`/`DivMod`/`Shr` too.
- **`Len`/cap split with geometric growth** for the limb store, mirroring
  `@Section` (`Len int` significant limbs + over-allocated `@[]uint32` backing,
  `ensureCapacity` doubling). There is no stdlib geometric-growth helper yet, so
  this small growth logic is inline. `slices.Append[uint32]` is O(n) per call
  (allocates+copies) — never used to build multi-limb results in a loop.

The dtoa lives in **strconv**, not `big`; `big` is a pure unsigned-integer
package with no float dependency.

---

## 1. `pkg/std/math/big.Nat`

### Representation

```binate
package "pkg/std/math/big"

import "pkg/stdx/slices"

// Nat is an arbitrary-precision unsigned integer.  Its value is
// sum(limbs[i] << (32*i)) for i in 0..len — little-endian base-2^32.
//
// Limb type is uint32 so any single-limb product fits in uint64 (Binate
// has no 128-bit integer): (2^32-1)^2 + 2*(2^32-1) < 2^64, so a
// limb*limb product plus an accumulator limb plus a carry never
// overflows uint64.  All limb arithmetic runs in uint64 and is NEVER
// routed through `int` (32-bit on ILP32 targets — would truncate).
//
// `len` is the count of significant limbs; `limbs` may be over-allocated
// (len(limbs) >= len), mirroring asm.Section's Len/cap split, so the
// dtoa scratch loop reuses backing without reallocating each step.
// NORMALIZED invariant: len == 0 (canonical zero) OR limbs[len-1] != 0.
type Nat struct {
    limbs @[]uint32   // backing, little-endian; len(limbs) is capacity
    len   int         // significant limb count (<= len(limbs))
}
```

- **Limb = uint32 / base 2^32** — forced by the no-128-bit constraint, justified
  by the overflow bound above. The single most important ILP32 invariant.
- **Normalization**: zero ⇔ `len == 0`. Private `norm(z)` trims high zero limbs.
  Never compare `limbs == nil` (managed-slices are non-nillable); test
  `z.len == 0`.
- **Ownership/refcount**: always used as `@Nat`. The `@[]uint32` backing is
  owned and refcount-freed when the `@Nat` dies. Ops that *replace* the backing
  (`z.limbs = newBacking`) RefDec the old one via normal assignment. Internal
  `*[]uint32` views borrow and must not outlive the owner. **Aliasing rule**:
  ops that re-read an operand after possibly clobbering the destination (`Mul`,
  `DivMod`, and `Add`/`Sub` when `z` aliases `x`/`y`) allocate a fresh result
  buffer, then assign into `z.limbs` at the end (Go `nat` discipline) — no leak,
  no use-after-free.
- **Growth**: `grow(z, n)` ensures `len(z.limbs) >= n`, doubling, copying live
  limbs. Multi-limb results are sized up front with `make_slice(uint32, k)` and
  index-assigned — never built by looping `slices.Append`. `@[]uint32` is a
  builtin type, so no `stdx` type leaks into the public `.bni`.

### Operation set

Mutating ops use the **z-destination receiver** (`z` written and returned, for
buffer reuse); operands `x`/`y` are not mutated and `z` may alias them.

**Schoolbook `Mul` overflow bound (the load-bearing correctness fact):**
```
t = xi*yj + r[i+j] + carry
  <= (2^32-1)^2 + (2^32-1) + (2^32-1) = 2^64 - 1   < 2^64
```
The uint64 accumulator never overflows — *this is why limbs are uint32*. A
direct runtime `uint32*uint32 -> uint64` test pins it.

**Digit extraction in Dragon4** is `d = floor(R/S)`, `d ∈ 0..9`, recovered by
≤9 trial subtractions (`while R.Cmp(S) >= 0 { R.Sub(R,S); d++ }`) — no `DivMod`
call; the standard Dragon4 division-free hot loop. `Shr` and full `DivMod` are
core-bignum (not used by dtoa) — shipped because `Nat` is a real unsigned
bignum; tested independently.

---

## 2. Dragon4 / Burger-Dybvig dtoa (in strconv)

### Float decomposition (width-matched `bit_cast`)

**CRITICAL ILP32 rule**: use the 64-bit partner for float64
(`bit_cast(uint64, f)`), the 32-bit partner for float32 (`bit_cast(uint32, f)`).
**Never `bit_cast(int, float64)`** — `int` is 32-bit on arm32, a size mismatch
that compiles on LP64 and silently diverges on ILP32.

```binate
// value = (-1)^sign * mant * 2^exp, mant an integer.
struct floatParts { mant uint64; exp int; sign bool; isZero bool; isInf bool; isNaN bool }
```

For float64: exp bias 1023, 52-bit frac, hidden bit `1<<52`, normal
`exp = be - 1023 - 52`, subnormal `exp = -1074`, all-ones exp `0x7FF`
(frac==0 → Inf else NaN). For float32: `bit_cast(uint32, f)`, 8-bit exp bias
127, 23-bit frac, hidden bit `1<<23`, normal `exp = be - 127 - 23`, subnormal
`exp = -149`, all-ones `0xFF`.

### R / S / M⁺ / M⁻ setup (Steele-White / Burger-Dybvig FPP2)

Maintain `value = R/S` with `M⁺`/`M⁻` the half-ulp gaps (rounding interval).
`mantLow` = smallest normal mantissa (`1<<52` f64 / `1<<23` f32). The
`mant == mantLow` branch handles **unequal gaps** at binade boundaries (lower
gap is half the upper) — the #1 correctness subtlety.

```
if exp >= 0:
    be = 2^exp                                  // Shl
    if mant != mantLow:  R = mant*be*2;  S = 2;     Mp = be;    Mm = be
    else:                R = mant*be*4;  S = 4;     Mp = be*2;  Mm = be
else:
    if mant != mantLow:  R = mant*2;     S = 2^(-exp)*2;   Mp = 1; Mm = 1
    else:                R = mant*4;     S = 2^(-exp)*4;   Mp = 2; Mm = 1
```
All `*2^k` are `Shl`; all `*small` are `MulUint32`. R, S, Mp, Mm are `@Nat`.

**Scale to base 10**: estimate `k ≈ ceil(log10(value))` from `BitLen` via a
fixed-point `log10(2)` constant (no float log — exact). If `k >= 0`:
`S *= 10^k`; else `R, Mp, Mm *= 10^(-k)`. Then a one-step fixup compares `R + Mp`
vs `S` and adjusts `k` by ±1 (the Burger-Dybvig fixup — exact, via `Cmp`, never
trusting the float estimate). `10^k` is built once via `Mul` square-and-multiply.

### Digit loop

```
loop:
    R.MulUint32(R, 10);  Mp.MulUint32(Mp, 10);  Mm.MulUint32(Mm, 10)
    d = 0; while R.Cmp(S) >= 0 { R.Sub(R, S); d++ }     // d in 0..9, R = R mod S
    low  = R.Cmp(Mm) < 0                                 // round down ok
    high = tmp.Add(R, Mp).Cmp(S) > 0                     // round up ok
    if !low && !high { emit d; continue }
    // terminal digit (round-to-nearest-even tie-break):
    if low && !high      { emit d }
    else if high && !low { emit d+1 }
    else { c = tmp2.Shl(R, 1).Cmp(S)                     // 2R vs S
           if c > 0 { emit d+1 } else if c < 0 { emit d }
           else { emit (d even ? d : d+1) } }            // half-to-even
    break
```
Output: `(sign, digits[], decExp k)` — a string-independent intermediate (like
Go's `decimalSlice`). `low`/`high` give the **shortest-round-trip** guarantee.

### Shortest (prec=-1) vs fixed (prec>=0)

- **Shortest (prec=-1)**: the loop above, with `bitSize`-aware gaps (float32 uses
  the float32 ulp, not float64's — else strings round-trip through f64 but not
  f32).
- **Fixed (prec>=0)**: drop M⁺/M⁻; generate exactly the requested digit count via
  the same `MulUint32(·,10)` + trial-subtract loop, then round the last digit by
  comparing `2R` vs `S` (half-to-even) and **propagate carry** up the digit array
  — a carry-out (`9.99→10.0`) bumps `decExp` and may change the digit count (must
  be reflected in the `needed` pre-pass before any write). `'e'`/`'f'` want
  digits-after-point; `'g'` wants significant digits; the caller converts via `k`.

### Special values (before any Nat work)

- `isNaN` → `"NaN"` (no sign).
- `isInf` → `"+Inf"` / `"-Inf"`.
- `isZero` → sign + `"0"` (or `"0.00…"` padded to prec for `f`; `"0e+00"` for `e`).
  `-0.0` keeps its sign (matches Go).

---

## 3. strconv API

```binate
// AppendFloat formats f per fmt/prec/bitSize, writes it into dst at pos,
// returns the next position or -(needed) (writing nothing) on overflow —
// same contract as AppendInt (needed >= 1, so a negative is unambiguous).
//   fmt:     'f' (-ddd.dddd) / 'e','E' (-d.dddde±dd) / 'g','G' (shortest of e/f)
//   prec:    -1 = shortest round-trip; >=0 = digit count (after point for e/f;
//            total significant for g)
//   bitSize: 32 (round-trip target float32) or 64. f is always float64.
func AppendFloat(dst *[]char, pos int, f float64, fmt char, prec int, bitSize int) int

// FormatFloat is the allocating convenience: returns a fresh caller-owned @[]char.
func FormatFloat(f float64, fmt char, prec int, bitSize int) @[]char
```

| fmt | meaning |
|---|---|
| `'f'` | `-ddd.dddd`, no exponent. prec = digits after point; -1 = shortest. |
| `'e'`/`'E'` | `-d.dddde±dd`, one digit before point. prec = digits after point. `E` uppercases the marker. |
| `'g'`/`'G'` | `'e'` when exp `< -4` or `>= 21` (shortest) / `>= prec` (fixed), else `'f'`; trailing zeros stripped when prec=-1. `G` uppercases. |
| unknown fmt | treat as `'g'` (no-panic house convention, like `normBase`). |
| `bitSize ∉ {32,64}` | default 64. |

**Overflow / `needed` pre-pass (load-bearing)**: float `needed` is variable, so
`AppendFloat` runs dtoa **once** into a small fixed scratch → `(sign, digits,
decExp)`, *finalizes any fixed-precision carry first*, then computes `needed`
exactly from `(fmt, prec, sign, ndigits, decExp)` (sign + integer digits +
optional `'.'` + frac digits + optional exponent). Then
`if pos < 0 || pos+needed > len(dst) { return -needed }`; else render into
`dst[pos:pos+needed]` from the *same* `(digits, decExp)` and return `pos+needed`.
`needed` and the render share layout helpers so they cannot disagree.
`FormatFloat` sizes an exact-fit `@[]char` via the same computation — identical
to `FormatInt`.

**Relationship to `bootstrap.formatFloat`**: this does **NOT** replace
`bootstrap.formatFloat` (`pkg/bootstrap/bootstrap.bn:143`, the 6-digit hack used
by `println` codegen via `pkg/binate/ir/gen_print.bn:162`). That path is
BUILDER-tree code and *cannot* import `pkg/std/math/big` (not BUILDER-compilable).
`AppendFloat`/`FormatFloat` is a strconv-level deliverable. **Rewiring `println`
is out of scope and a separate user decision.**

---

## 4. File layout & internal dependency

Following the strconv asymmetry (flat `.bni`, subdir impl): `big.bni` is a FLAT
file (like `strconv.bni`) — NOT `pkg/std/math/big/big.bni`. The impl `nat*.bn`
files are split along natural boundaries from the start (Go's `nat.go` is ~1500
lines) — never one blob.

- **Dependency**: `pkg/std/math/big` (T1) imports `pkg/stdx/slices` (T1x)
  **internally only** — `@[]uint32` is a builtin, no `stdx` type in `big.bni`.
  `pkg/std/strconv` (T1) imports `pkg/std/math/big` (T1) — `Nat` is internal to
  `ftoa.bn`, not in `strconv.bni`. No tier leaks.

---

## 5. Build / wire-up

- **No `-I`/`-L` additions.** Runners already pass
  `-I …:ifaces/stdlib -L …:impls/stdlib/common`; the loader resolves
  `import "pkg/std/math/big"` against those roots.
- **Conformance whitelist (required)**: add the cross-pkg test entry
  (`…:pkg/std/strconv`) to `scripts/hygiene/conformance-imports.whitelist`.
  `pkg/std/math/big` is transitive, needs no entry.
- **ILP32 xfail (likely)**: pkg/std unit tests are already xfail on
  `builder-comp_arm32_baremetal` (>int32 literals trip the ILP32 fit-check).
  Build big magnitudes through `cast(uint64,…)` / `bit_cast(float64,<int64
  bits>)` to avoid it. **Verify under arm32 modes** — LP64 passes even with a
  latent `int`-truncation bug.
- **Scope guard**: do NOT touch `gen_print.bn` / `bootstrap.formatFloat` / CI
  workflows. Adding the package + tests is in scope; hooking `println` to it is
  not.

---

## 6. Test strategy

- **Nat unit tests**: green under all modes incl. arm32 BEFORE any dtoa — a
  Dragon4 bug usually traces to a Nat bug. Includes the direct runtime
  `uint32*uint32 -> uint64` test (`0xFFFFFFFF*0xFFFFFFFF == 0xFFFFFFFE00000001`),
  carry/borrow across limbs, `Shl`/`Shr` crossing 32-bit boundaries, `DivMod`
  (Knuth D normalization + add-back).
- **strconv float unit tests**: **construct exact inputs via
  `bit_cast(float64, <pinned int64 bits>)`** (the `330_float_bit_exact`
  technique) — never decimal literals (the lexer rounds them). Covers shortest
  mode (`0.1+0.2` bits `4599075939470750516` → `"0.30000000000000004"`, max
  `1.7976931348623157e308`, smallest subnormal `5e-324`); fixed-precision carry
  (`0.95 prec=1`, `9.999…→10.0`); all fmts; float32-vs-float64 round-trip;
  overflow contract at exactly `needed` and `needed-1`.
- **Cross-package conformance** (`535_strconv_float_cross_pkg`): float ops run
  identically on LLVM and the VM (per 330), so no per-mode `.expected`.
- **Round-trip**: until `strconv.ParseFloat` exists (out of scope), shortest
  output is asserted against hand-verified golden strings (the golden strings
  *are* the round-trip property). A future `ParseFloat` enables
  `bit_cast(int64, parse(format(v))) == bit_cast(int64, v)`.

---

## 7. Resolved decisions, risks, follow-ups

### Biggest risks (ranked) — for reference

1. **ILP32 `int`-truncation in limb math** — silently corrupts on arm32, passes
   on LP64. Mitigation: every limb intermediate explicitly `uint64`; the direct
   `uint32*uint32->uint64` test; verify under arm32 modes.
2. **`bit_cast` width mismatch** — all extraction through `decompose64/32` using
   the width-matched partner; test inputs via `bit_cast(float64, <int64 bits>)`.
3. **Unequal-gap (binade-boundary) handling** — the `mant == mantLow` branch;
   golden tests at `2^n`, min-normal, max-subnormal.
4. **`needed` pre-pass disagreeing with the render** — finalize carry before
   computing `needed`; derive both from shared layout helpers.
5. **Round-half-to-even at the terminal digit** — `0.1+0.2`, `0.1`, parity-tie
   golden vectors.
6. **`DivMod` Knuth D correctness** (q̂ estimate, add-back) — tested
   independently before any dtoa.
7. **`@Nat` aliasing / refcount in z-out ops** (`R.Mul(R, ten)`) —
   fresh-result-buffer-then-assign for `Mul`/`DivMod`.
8. **float32 shortest using float64 gaps** — `decompose32` with float32 ulp.

### Resolved decisions

- **Q1 — `DivMod`/`DivModUint32` on a zero divisor**: **PANIC**
  (`"big: division by zero"`), matching Go's `big.Int.Div` and the builtin `/`.
  Keeps the clean 2-return shapes. NOT a `(…, bool)`: div-by-zero is a programmer
  error, and a defensive signal would want a real `error` type — which Binate
  does not yet have. Unguarded is not an option: a zero divisor is
  platform-dependent (ARM64 `UDIV` → 0 silently; x86 `DIV` → `#DE` trap), so it
  must be guarded for consistent behavior across backends.
- **Q2 — `'b'`/`'x'` float formats**: deferred as explicit follow-up TODOs (this
  note + a `// TODO` at the fmt-dispatch site in `ftoa.bn`). Not in the first cut.
- **Q3 — `println` rewiring**: out of scope. `bootstrap.formatFloat` is
  BUILDER-tree and can't import `pkg/std/math/big`; the 6-digit hack stays until a
  separate migration. This whole effort is a step toward eventually deprecating
  `pkg/bootstrap` (replacing `formatFloat`, then the `println` path, are later
  milestones of that arc).

### Noted for later (not this work)

- **Int-replacement TODO**: a signed `Int` wrapping `Nat` to replace
  `pkg/binate/bignum`. The real cost is not arithmetic but that
  `pkg/binate/types` stores folded literals as three flat fields
  (`HasLitVal`/`LitMag uint64`/`LitSign`, `types.bni:145-147`) read by IR
  (`gen_util_literals.bn:108`); an `Int` exceeding uint64 forces a `Type`-field
  representation change rippling into IR. Scope the TODO around that storage
  change, not the limb math.
- **Out of scope entirely**: `Rat`; `Float` (`math/big.Float`).
