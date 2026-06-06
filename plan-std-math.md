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
- **Phase 3 — transcendentals** — IN PROGRESS. `Exp` LANDED (binate `4311098c`)
  and `Log` LANDED (binate `696b1b5a`): both are Go fdlibm ports, and the
  FP-contraction bit-identity risk is **retired** — `Exp(1)` equals `E`, and
  `Log(e)`/`Log(2)`/`Log(10)` equal `1`/`Ln2`/`Ln10`, bit-for-bit on LLVM, VM,
  gen2, and native-aa64 (plain fmul/fadd at -O2 don't fuse, so transcendentals
  stay bit-identical across backends). The remaining ports — `Log2`/`Log10`/
  `Log1p`, `Exp2`/`Expm1`/`Pow10`, and `Pow` — follow the same proven pattern:
  package-level `<fn>`-prefixed magic-constant consts, fully-parenthesized
  shift-vs-add expressions (Binate shift binds looser than `+`), Go-derived
  bit-pattern test vectors, and a bit-exact pin to guard cross-backend identity.
- **Next on resume**: the derived log functions (`Log2`/`Log10`/`Log1p`), then
  `Exp2`/`Expm1`/`Pow10`, then `Pow`. Workflow note: a porting workflow works
  well (see the Tier-0 fan-out) BUT constrain agents to structured output only —
  one agent wrote scratch files into the main checkout, which had to be cleaned up.

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
