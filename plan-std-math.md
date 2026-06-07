# pkg/std/math — implementation plan

Implement `pkg/std/math`, analogous to Go's `math` package, starting with the
common functions. float64 only. Ports Go function-for-function and matches its
outputs **bit-for-bit**, reusing Go's test vectors.

## Status (2026-06-06)

- **Phase 0 — float `!=` → IEEE-unordered** — LANDED (binate `8f78575f`).
- **Phase 1 — Tier 0** — LANDED. Constants + bits/sign/special-value
  (`ac96ebb3`), rounding + Modf (`13551db1`), decompose + min/max + modular
  (`b10dae20`): `Float64bits/frombits`, `Abs`, `Signbit`, `Copysign`, `NaN`,
  `Inf`, `IsNaN`, `IsInf`, `Floor`, `Ceil`, `Trunc`, `Round`, `RoundToEven`,
  `Modf`, `Frexp`, `Ldexp`, `Max`, `Min`, `Dim`, `Mod`, `Remainder`.
- **Phase 2 — Sqrt + dependents** — LANDED `Sqrt` (`4d38b763`); `Hypot` + `Cbrt`
  (`8fd3ca32`). Software Sqrt is correctly-rounded and bit-identical on LLVM /
  VM / native-aa64.
- **Found + fixed along the way**: a CRITICAL integer-shift-overshift wrong-code
  bug (shift by count >= width was hardware-masked, not the spec's 0/sign-fill)
  — fixed in IR-gen `gen_binary.bn` (binate `32fde83d`), all backends. Surfaced
  by porting `math.RoundToEven`.
- **Phase 3 — transcendentals** — COMPLETE (all LANDED). The exp/log core:
  `Exp` (`4311098c`), `Log` (`696b1b5a`), and `Log2`/`Log10`/`Log1p` (`f0b9558a`)
  — all Go fdlibm ports. **Cross-backend** bit-identity holds (LLVM gen1/gen2,
  VM, native-aa64 all agree — none of Binate's backends fuse): `Exp(1)==E`;
  `Log(e)`/`Log(2)`/`Log(10)`==`1`/`Ln2`/`Ln10`; powers of two/ten exact under
  `Log2`/`Log10`; `Log1p(tiny)==tiny`. **Binate-vs-Go bit-identity does NOT fully
  hold**, though: the Go compiler fuses `a*b+c` into an FMA on amd64/arm64 while
  Binate is non-fused IEEE (no FMA intrinsic — Phase 5), so a handful of inputs
  differ by ONE ULP (first caught by `Tan(1e15)`/`Tan(1e20)`; the earlier
  "FP-contraction retired" claim was luck — those test values happened to round
  the same). Ratified policy (2026-06-06): FMA-sensitive value tests assert
  `<=1 ULP` vs Go (`nearULP`), not a bit-exact pin — FMA is typically the more
  accurate/faster, and this leaves room to adopt FMA later. `Log2E`/`Log10E` are
  bit-identical to
  Go's full-precision `1/Ln2`/`1/Ln10`, so the reciprocals are precomputed
  consts, never runtime division of the rounded `Ln2`/`Ln10`. The proven pattern:
  package-level `<fn>`-prefixed magic-constant consts (reuse shared fdlibm
  coefficients across functions where the value is genuinely the same),
  fully-parenthesized shift-vs-add expressions (Binate shift binds looser than
  `+`), the `ldexp.bn` `cast(int, iu>>shift)-cast(int, bias)` idiom for exponent
  recovery (no uint64->int wraparound), Go-derived bit-pattern test vectors, and
  a bit-exact pin to guard cross-backend identity.
  `Pow10` is also LANDED (`6d687d97`): table-based (three package-level
  `float64` lookup tables), bit-exact to Go across all four backends.
- **Compiler-fix detour (LANDED)**: building `Pow10`'s global float tables
  surfaced two codegen bugs — whole-array/struct `=` assignment silently dropped
  (it stored the composite-literal's alloca pointer, not the contents; this also
  broke all global array/struct initializers, which route through `__init`'s
  `x = expr`) and a global float `var` emitting invalid LLVM (`global double 0`).
  Both fixed in binate `65f79253` (ident/deref assignment arms now load the
  aggregate, matching the selector arm; float globals emit `0.0`). A new
  `conformance/matrix/aggregate/` value-movement matrix (`bf185391`,
  `gen-aggregate-matrix.py`, 46 cells) is the systematic prevention — it fails 13
  cells on the pre-fix compiler. Porting `Pow` then surfaced a THIRD codegen
  bug: a relational op with an untyped int literal on the LEFT against a signed
  int lowered to an UNSIGNED compare (`5 < xe`, `xe==-1` → wrongly true), all
  backends — fixed in binate `b54c9fdf` (IR-gen stamps the resolved concrete
  type onto an untyped-int operand), pinned by
  `conformance/regressions/cmp-literal-left-signedness`. Also surfaced and filed
  in `claude-todo.md` (separate, pre-existing): nested arrays `[N][M]T`
  mis-compiled, and a cross-module global-struct-type decl gap.
- **Phase 4 — trig + inverse + hyperbolic** — COMPLETE (all LANDED). `Sin`/`Cos`
  (`9704f510`, with the Payne-Hanek `trigReduce` + `mul64`/`add64`/
  `leadingZeros64` + the `mPi4` table for `|x|>=2**29`), `Tan` (`e756781f`),
  `Atan`/`Atan2`/`Asin`/`Acos` (`0aec152e`), `Sinh`/`Cosh`/`Tanh` (`0aec152e`),
  `Asinh`/`Acosh`/`Atanh` (`382577fa`). Small args bit-exact to Go; the
  FMA-prone large-argument trig path asserts `<=1 ULP` (`nearULP`).
- **Phase 5 — tail** — COMPLETE (all LANDED): `Nextafter`/`Logb`/`Ilogb`
  (`32af74f6`), `FMA` (`0b2e0ad9`, a software single-rounding fused multiply-add
  built on `mul64`/`add64`/`leadingZeros64`), `Erf`/`Erfc` (`d7675ece`), `Gamma`
  (`c54d4ffd`), `Lgamma` (`6471c6bb`), `J0`/`Y0`/`J1`/`Y1` (`64230724`), `Jn`/`Yn`
  (`3f69e8b4`). The polynomial-heavy `Lgamma`/Bessel functions are the first to
  exercise the `<=1 ULP` FMA policy (`nearULP`); the rest are bit-exact to Go.
- **DONE**: the float64 Go `math` package is fully ported (115 tests in
  `pkg/std/math`, green on LLVM gen1/gen2, the VM, and native aa64). Phase 5 was
  built via a parallel research workflow (one agent per group returning Go source
  + machine-generated bit-exact reference vectors + an idiom-aware draft +
  Binate-gotcha notes; agents return structured data only — no file writes — and
  I implemented/tested/landed each group sequentially).
- **Adversarial correctness review (2026-06-07) — NO bugs found.** Two prongs:
  (1) a parallel code audit (workflow, 14 agents over 13 function-groups, each
  finding adversarially verified) found ZERO structural bugs — coefficients,
  polynomial association, shift-precedence sites, special-case ladders, and
  branch thresholds all matched Go (the one finding, a `Modf` signaling-NaN
  payload, was refuted: neither Go's spec/test nor Binate pins the NaN payload;
  clean over 2M random bit patterns). (2) a differential numerical sweep (251
  random + boundary + edge inputs × 34 functions) vs Go and an
  arbitrary-precision (mpmath) oracle: 29/34 functions agree with Go to `<=1 ULP`
  everywhere; the other 5 (`Tan`, `Gamma`, `J0`, `Y0`, `Y1`) exceed 1 ULP ONLY
  on ill-conditioned inputs (near a zero/pole or via a long Horner chain), max
  ~30-42 ULP (`Y0(0.9)`, beside its zero at 0.894). The mpmath check confirms
  these are NOT bugs but the FMA accuracy gap amplified by cancellation — the
  fdlibm algorithm isn't correctly-rounded there even in Go (Go is 12 ULP off
  the true `Y0(0.9)`; Binate 42). **Accuracy caveat**: with no FMA, Binate's gap
  vs fused-Go can exceed 1 ULP near zeros/poles, so the ratified `<=1 ULP` policy
  holds for the (well-conditioned) curated test inputs but is NOT universal;
  closing it would mean using `math.FMA` in the hot polynomials of those
  functions. (Debugging note: the conformance runner truncates the "actual"
  output on a mismatch display — it briefly looked like a `Gamma` crash; built
  and run directly, every program exits 0.)
- **Possible follow-ups** (not started): a hardware `Sqrt`/`FMA` intrinsic; using
  `math.FMA` to match Go's compiler fusion exactly where desired (the `<=1 ULP`
  cases, and the ill-conditioned `Tan`/`Gamma`/Bessel inputs above);
  `Erfinv`/`Erfcinv`; the float32 `Float32bits`/`Float32frombits` helpers.

## Ratified decisions (user, 2026-06-06)

- **Fix float `!=` to IEEE-unordered semantics** (Phase 0, prerequisite). Today
  Binate deliberately makes `!=` *ordered* (`NaN != NaN` is `false`), which
  diverges from Go/C/IEEE and makes `==`/`!=` non-complementary for NaN. Fix it
  to match everyone's expectations before building `math` on top.
- **float64 only** — mirrors Go; float32 callers convert. A `float32` layer (or
  generics) is a later, separate item.
- **Software `Sqrt` first; add a hardware `sqrt` intrinsic later.** Keeps the
  language surface stable and guarantees cross-backend bit-identity now; the
  intrinsic becomes a compiled-backend fast path (with the software impl staying
  as the VM/fallback) once it's worth the cross-backend wiring.
- **Match Go bit-for-bit, reuse Go's `all_test.go` vectors.** This inherits Go's
  exhaustive special-value (±Inf/NaN/±0) coverage for free.

## What already exists (foundation)

- `bit_cast` between float64↔uint64 / float32↔uint32 works and is used
  throughout `strconv` — so `Float64bits` / `Float64frombits` are trivial, and
  everything built on bit layout (`Abs`, `Signbit`, `Copysign`, `IsInf`,
  `Frexp`, `Ldexp`, `Modf`, `Trunc`/`Floor`/`Ceil`, `IsNaN`) follows from
  integer/bit ops.
- `pkg/std/math/big` has `Nat` (arbitrary-precision **unsigned int**); **no
  `big.Float` yet**. So argument reduction can't lean on arbitrary precision —
  we port Go's float64-only double-double tricks (Veltkamp split, etc.), which
  need only float64 arithmetic.
- `pkg/std/strconv` (`atof`/`ftoa`) is the precedent for float bit-twiddling and
  layout (one file per concern, under the length cap).
- `math` is **outside `cmd/bnc`'s tree**, so it may use the full language. It is
  bundled in the stdlib (the stdlib-bundle work), but nothing in `bnc`'s tree
  imports it, so there is no BUILDER-subset constraint on it.

## Phase 0 — Fix float `!=` (NaN-unordered) — PREREQUISITE, lands standalone

Today's behaviour is a *deliberate, documented* "ordered `!=`" choice (see the
comments in `x64_float.bn` / `aarch64_float.bn`: "every ordered compare against
NaN returns false, INCLUDING `!=`"). It is the one float relop that's wrong vs
IEEE/Go: `==` is `oeq` (NaN→false ✓), the four relationals are ordered
(NaN→false ✓), but `!=` is `one` (NaN→false ✗) when it should be `une`
(NaN→**true**). `oeq` and `une` are exact complements, restoring
`(a==b) == !(a!=b)`.

Exact change, per backend (verified against the source):

- **LLVM** `pkg/binate/codegen/emit_ops.bn` (`emitCmp`): float `OP_NE` predicate
  `one` → `une`. One line. (The other five predicates are already correct.)
- **x64** `pkg/binate/native/x64/x64_float.bn` (`emitFloatCompare`): `OP_NE`
  currently goes through the "AND with `SETNP`" NaN-gate (giving ordered NE).
  Change it to `SETNE` **OR** `SETP` (true when not-equal OR unordered = `une`).
  Pull `OP_NE` out of the `floatPrimaryCC`+`SETNP` group into its own arm.
- **aarch64** `pkg/binate/native/aarch64/aarch64_float.bn` (`emitFloatCompare`):
  **delete** the `if ins.Op == ir.OP_NE { Csel ... COND_VC }` block — the base
  `CSINC` with the inverted condition already yields unordered NE (the block was
  added specifically to force it back to ordered).
- **VM** `pkg/binate/vm/vm_exec64.bn` (`evalFloatCmp64`): `BC_FNE64` executes the
  host `a != b`, which becomes `une` once the VM is recompiled by the fixed
  compiler — no code change, but add a VM test so the transitive fix is pinned.
  Also **flip the existing assertion** in `pkg/binate/vm/vm_exec64_test.bn` that
  currently pins *"NaN != NaN must be false (ordered)"* — it must now expect
  `true` (this test is the one concrete piece of in-tree reliance on the old
  semantics; the adversarial review confirmed nothing else depends on it).

Tests:
- Conformance cell: `NaN != NaN == true`, `NaN == NaN == false`,
  complementarity `(x==y) != (x!=y)` across normal + NaN operands, and the four
  relationals stay `false` against NaN. Must pass on all default modes **and**
  the native alt-modes (this is a native-backend change too).
- Emitter unit tests for the `une` predicate (LLVM string-shape) and the x64 /
  aarch64 NE sequences.

Docs:
- Rewrite the now-wrong "ordered `!=`" comments in `x64_float.bn` /
  `aarch64_float.bn` to describe the IEEE semantics.
- Add a float-comparison entry to `claude-notes.md` (== `oeq`, != `une`, the
  four relationals ordered) so the spec is written down (it currently lives only
  in code comments).

Filed as a MAJOR correctness bug in `claude-todo.md` (==/!= non-complementary →
silent wrong results in any user code that does `x != x` or NaN-aware compares).
Lands independently before any `math` code.

## Phase 1 — Tier 0: bit/integer-level (no approximation)

The high-value, zero-approximation-risk core. Ship as the first `math.bni`
+ impl so downstream code (and later phases) can use it immediately.

- Bits: `Float64bits`, `Float64frombits`.
- Sign/abs: `Abs`, `Copysign`, `Signbit`.
- Special values: `NaN()`, `Inf(sign)`, `IsNaN` (bit pattern: biased exp all-ones
  AND mantissa≠0 — **not** `x != x`, robust regardless of Phase 0), `IsInf`.
- Rounding: `Floor`, `Ceil`, `Trunc`, `Round`, `RoundToEven`.
- Decompose: `Modf`, `Frexp`, `Ldexp`.
- Algebraic-simple: `Max`, `Min` (Go's NaN/±0 rules), `Dim`, `Mod`, `Remainder`.
- Constants: `Pi`, `E`, `Phi`, `Sqrt2`, `SqrtE`, `SqrtPi`, `Ln2`, `Log2E`,
  `Ln10`, `Log10E`, and the limits `MaxFloat64` / `SmallestNonzeroFloat64`.
  **Define the limits via bit patterns** (`Float64frombits`) to sidestep any
  float-literal const-fold rounding risk; transcendental constants use Go's
  exact decimal literals (see Challenge: const-fold precision).

## Phase 2 — Sqrt (software) + immediate dependents

- Port Go `math/sqrt.go`'s pure-Go fallback (bit-manipulation + integer Newton,
  correctly rounded). Pin bit-exact against Go's `Sqrt` vector.
- Then `Hypot` and `Cbrt` (build on Sqrt / Pow).

## Phase 3 — Exp / Log / Pow

`Exp`, `Exp2`, `Expm1`, `Log`, `Log2`, `Log10`, `Log1p`, `Pow`, `Pow10`. fdlibm
ports. Watch FP-contraction (Challenge below).

## Phase 4 — Trig + inverse + hyperbolic

`Sin`, `Cos`, `Tan`, `Sincos`, `Asin`, `Acos`, `Atan`, `Atan2`, `Sinh`, `Cosh`,
`Tanh`, `Asinh`, `Acosh`, `Atanh`. Argument reduction matches Go's reducer,
including its large-argument (Payne–Hanek) tail — we match Go's accuracy, not
exceed it.

## Phase 5 — Tail

`Gamma`, `Lgamma`, `Erf`, `Erfc`, `Erfinv`, `Erfcinv`, the Bessel family
(`J0/J1/Jn/Y0/Y1/Yn`), `Nextafter`, `Ilogb`, `Logb`, and `FMA` (Go's pure-Go
correctly-rounded fused multiply-add — non-trivial; sequence last).

## Cross-cutting challenges & resolutions

- **LLVM↔VM bit-identity (FP-contraction).** Conformance runs `-comp` (LLVM) and
  `-int` (VM); results must match bit-for-bit. bnc emits plain `fmul`/`fadd`
  with **no** fast-math flags, and the build is `-O2` (LLVM default
  `FPOpFusion::Standard` → unflagged ops are **not** fused into FMA). So separate
  multiply/add stays separate, matching the VM. Low risk by construction; still
  **pin it** with a contraction-sensitive test (e.g. a Veltkamp split whose
  result changes if `a*b+c` fuses).
- **Const-fold precision for float literals.** Recent `ParseFloatLitToBits`
  (via `big`) work should make long-decimal literals round correctly, but
  confirm the *compiler's* const-fold of a Go-style `Pi` literal yields the
  right bits before relying on it. Limits are defined from bit patterns
  regardless.
- **Special-value discipline.** Port Go's per-function ±Inf/NaN/±0 tables
  verbatim; Go's test vectors enforce them.
- **No `big.Float`.** Argument reduction and any extended-precision steps use
  float64-only double-double arithmetic (as Go does), not arbitrary precision.

## Testing strategy

- Port Go `src/math/all_test.go`'s `vec` / per-function expected tables into
  conformance cells (and/or `_test.bn` unit tests), asserting **bit-exact**
  results (compare via `Float64bits`).
- Run across `builder-comp`, `builder-comp-int`, the gen2 modes, and the native
  alt-modes (the Phase-0 fix and the math impls must agree everywhere).

## Layout (mirrors strconv)

- Iface: `ifaces/stdlib/pkg/std/math.bni` (alongside the existing
  `math/big.bni`).
- Impl: `impls/stdlib/common/pkg/std/math/{const,bits,floor,modf,sqrt,exp,log,
  pow,trig,asin,atan,sinh,...}.bn` + `*_test.bn`, one concern per file under the
  length cap (the `big/` subpackage stays as-is).

## Open questions / to confirm during Phase 1

1. Does the compiler const-fold a full-precision decimal `Pi` literal to the
   correct float64 bits? (If not, define transcendental constants from bit
   patterns too.)
2. Exact `Max`/`Min` semantics to match — Go's `math.Max` (NaN/±0 propagation),
   distinct from the builtin `max`/`min` if those exist for floats.
3. When the `sqrt` intrinsic lands later: confirm `@llvm.sqrt.f64` / `FSQRT` /
   `SQRTSD` / VM-host-sqrt all round identically to the software impl (they
   should — all IEEE correctly-rounded) so swapping it in stays bit-exact.
