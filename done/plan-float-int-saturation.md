# Plan: float→int saturation (well-defined `cast(<int>, <float>)` for out-of-range / ±Inf / NaN)

## Contract (RATIFIED 2026-06-12, user)

`cast(T, f)` where `T` is an integer type and `f` is a float:

- In-range `f`: **truncate toward zero** (unchanged).
- `f > T_MAX` (incl. `+Inf`): **`T_MAX`** (saturate up).
- `f < T_MIN` (incl. `-Inf`): **`T_MIN`** (saturate down; `0` for unsigned).
- `f` is **NaN**: **`0`**.
- Saturation is to **`T`'s own width** (`cast(int8, 1000.0)` → `127`, NOT
  int64-saturate-then-modular-narrow). Applies to every target width
  (int8/16/32/64, signed + unsigned) and both float32 and float64 sources.

This **refines Go** (whose spec leaves it "implementation-specific, conversion
succeeds, no panic") — saturation pins a defined, cross-target value while
staying panic-free. Matches Rust `as` (since 1.45) and WASM `trunc_sat`.

## Approach: lower it ONCE in shared IR-gen (NOT per-backend)

Saturation semantics is **language-level** — it must be byte-identical across
the LLVM backend, the x64 / aarch64 native backends, AND the bytecode VM (for
dual-mode interop). Per `ir-backend-guidelines.md`, language-semantic logic
belongs in the **shared layer**, not duplicated in each backend.

There is a direct precedent in the same file: **`emitGuardedShift`**
(`pkg/binate/ir/gen_binary.bn:203`) normalizes hardware-divergent
shift-overshift branchlessly, "so every backend inherits the behavior without
per-backend shift logic" (its own comment), and was the fix for the
CRITICAL integer-shift-overshift entry. Float→int saturation is the same class
of problem (hardware ISAs disagree: arm64 saturates, x86-64 returns
`INT_MIN`, LLVM `fptosi` is poison) and takes the same shape.

**Rejected alternative (per-backend):** LLVM `llvm.fptosi.sat` intrinsic + a
hand-written x64 `UCOMISD`+clamp + aarch64 narrow-FCVTZS/clamp + a VM Binate
clamp = **four implementations that must agree**, and narrow targets (int8/16)
need explicit clamps on x64 AND aa64 anyway (there is no narrow-dest CVTTSD2SI;
aa64's `FCVTZS Xd` saturates to int64, not int8). The only thing the per-backend
route buys is marginally tighter LLVM codegen (the intrinsic vs an
fcmp+select sequence the optimizer will fold). Not worth four code paths.

### The lowering (`emitGuardedFloatToInt`, new, in `gen_binary.bn` or a sibling)

Built entirely from ops every backend + VM already lowers (`OP_CAST` raw
convert, float `OP_GE`/`OP_LT`/`OP_NE`, integer `OP_AND`/`OP_OR`/`OP_SUB`/`OP_XOR`,
`EmitConstInt`, `EmitConstFloat`). For target int type `T` (width `N`, signed
`S`), source float precision `P`:

```
raw    = EmitCast(x, T)                       // raw truncating convert (in-range correct)
maxBnd = EmitConstFloat( S ? 2^(N-1) : 2^N , P)   // smallest power-of-2 > T_MAX (exact in P)
minBnd = EmitConstFloat( S ? -2^(N-1) : 0  , P)   // == T_MIN (exact)
maxInt = EmitConstInt( S ? 2^(N-1)-1 : 2^N-1 , T)
minInt = EmitConstInt( S ? -2^(N-1)   : 0    , T)

tooHi  = EmitBinop(OP_GE, x, maxBnd, bool)    // ordered: false for NaN
tooLo  = EmitBinop(OP_LT, x, minBnd, bool)    // ordered: false for NaN
isNaN  = EmitBinop(OP_NE, x, x,      bool)    // true ONLY for NaN

r1 = select(tooHi, maxInt, raw)               // select(c,a,b) = (a & m) | (b & ~m),
r2 = select(tooLo, minInt, r1)                //   m = 0 - (cast(c,T) & 1)  [the emitGuardedShift mask]
r3 = select(isNaN, zero,   r2)
return r3
```

Correctness notes:
- `maxBnd = 2^(N-1)` (signed) / `2^N` (unsigned) is the classic fptosi.sat
  boundary — it is exactly representable in float32 and float64 (a power of 2),
  and `x >= maxBnd` catches everything that would overflow even though `T_MAX`
  itself is not exactly representable for wide `N`. `minBnd = -2^(N-1)` (signed)
  IS exactly `T_MIN`, so `x < minBnd` (strict) leaves `x == T_MIN` in-range.
- NaN makes both ordered compares false, so only the `isNaN` select fires →
  `0`. The raw convert of a NaN/out-of-range input (poison on LLVM, `INT_MIN`
  sentinel on x64) is never selected, so it is harmless.
- Selection is over INTEGERS, reusing `emitGuardedShift`'s exact mask idiom
  (`0 - (bit & 1)`), robust to the bool→int zext/sext choice. No float select.
- 64-bit targets on a 32-bit host are register pairs; `EmitBinop` already
  handles that (the shift clamp does too).

### Where it hooks in

The user `cast(int, float)` expression site (the `EmitCast` call in the cast
handler — `gen_expr.bn:284` and/or the cast-expr lowering). Audit every
`EmitCast(<float>, <int>)` generation site (gen_expr, gen_binary, gen_print)
and route the float→int ones through `emitGuardedFloatToInt`; keep `EmitCast`
itself the raw single-op convert (so the saturation wrapper can call it for the
in-range path, and non-cast internal uses are unaffected). **Open: confirm
gen_print's float→int casts (lines 118/124/129) should also saturate** — they
are the println formatting path; for consistency they should, since they
realize the same `cast` semantics.

## Sequencing (small, each commit green on all modes)

Because the lowering is shared, ALL backends + the VM become correct in the
SAME commit — there is no per-backend xfail staging.

1. **Commit 1 — the lowering + spec + conformance + unit tests. ✅ LANDED
   (binate `b3a52025`).** `emitGuardedFloatToInt` (`pkg/binate/ir/gen_cast_float.bn`)
   routed in at the `gen_expr.bn` cast site; `claude-notes.md` + `plan-language-spec.md
   §21` state the saturation contract. `conformance/732_float_int_saturation`
   (renumbered from 731 at land — collision with a concurrently-landed
   `731_build_arch_select`) covers int8/16/32/64 signed+unsigned, f32+f64
   sources, `±Inf`, `NaN`, just-over/under, in-range — green on `builder-comp`,
   `builder-comp-int` (VM), gen2, gen3, native aa64, native x64. Unit tests
   (`gen_cast_float_test.bn`) pin the routing predicate, the boundary constants,
   and that the guard ops are emitted (absent for an int→int cast). Full
   builder-comp 1398/0; hygiene 13/13.
2. **Commit 2 — re-enable the generated matrix coverage. ✅ LANDED (binate
   `068749c8`).** `gen-diff-scalar.py` no longer excludes out-of-range float→int:
   `saturate_to_int()` is the spec oracle and `float_to_int_cell` sweeps the
   `2^(N-1)`/`2^N` thresholds (exact powers of two → identical in f32/f64), their
   doubles + negations, and exact ±Inf/NaN bit patterns, for every width
   int8…int64 signed+unsigned × both float precisions. Green on builder-comp,
   builder-comp-int (VM), gen2. The 2 pre-existing native-aa64 signed int8/int16
   xfails stay (orthogonal `aa64-subword` sign-extend miscompile, not saturation).
   This closed the self-review's coverage observations (exact boundaries, all
   widths, both precisions, Inf/NaN per width).
3. **minbasic follow-up — HANDOFF to the `binate/examples` repo (per user
   2026-06-12: someone else should own the examples work).** Un-skip the 3
   minbasic programs (P168/P174/P180) skipped pending this contract; their
   `+Inf → index` now has a defined cross-platform value (`INT64_MAX` /
   target-int MAX). NOT done here — examples-repo task for another worker.

## Risks / open questions

- **EmitConstFloat in a chosen precision.** Confirm `EmitConstFloat` can emit a
  float32-typed constant (for an f32 source) so the compare is in-precision; if
  it only emits float64, an f32 source must be compared after a defined
  widen (f32→f64 is exact) — fine either way.
- **`OP_GE`/`OP_LT`/`OP_NE` on floats** must lower to ORDERED compares on every
  backend (NaN → false for `>=`/`<`, true for `!=`). Verify the VM + native
  float-compare lowering uses ordered predicates (the language's `<`/`==` on
  floats already pin this).
- **gen_print routing** (above) — decide whether print's float→int casts
  saturate (recommended: yes).
- **Performance.** A handful of extra compares/ands per float→int cast. Matches
  `emitGuardedShift`'s accepted cost; the LLVM optimizer folds the pattern.
