# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## CRITICAL (REGRESSION from `2e783acd`, gap (b)) — a TRANSITIVE `.bni` const (`const X = otherpkg.Y`) silently miscompiles to a garbage value when the top-level importer does not also import `otherpkg` (2026-06-16) — ✅ conservative guard FIXED & LANDED `8dd35667`; proper IR-gen fix still 🔴 OPEN (see below)

**Symptom (REPRODUCED, builder-comp gen1).** `pkg/a.bni: const VAL int = 7`; `pkg/b.bni: import "pkg/a"; const SZ int = a.VAL`; a `main` that imports ONLY `pkg/b`. Then `var arr [b.SZ]int` compiles with NO error and emits `alloca [392 x i64]` (garbage — constant regardless of `a.VAL`), instead of `[7]`. `x << b.SZ` likewise folds wrong. **Regression**: BEFORE `2e783acd`, `[b.SZ]int` was a CLEAN compile error ("array length must be a constant integer"); `2e783acd` converted that clean rejection into a SILENT wrong-size layout.

**Root cause (checker/IR-gen DISAGREEMENT).** `2e783acd`'s `defineBniConst` + `evalConstIntValue` `EXPR_SELECTOR` arm makes the CHECKER fold `b.SZ` correctly (to 7) — `resolveQualifiedSym` reads the topologically-ordered package scopes. But IR-gen's `RegisterImport` (`gen_register_import.bn:156-174`) folds `b.SZ` via `evalConstExpr`'s `EXPR_SELECTOR` arm = `lookupConst(buildQualName("a","VAL"))` keyed by `b.bni`'s LOCAL import alias `a`; when the top-level importer doesn't also import `pkg/a`, `pkg/a`'s const isn't registered under that alias → lookup fails → IR-gen DROPS the const → garbage. So the checker folds, IR-gen drops: the array SIZE (IR-gen's value) disagrees with the dim the checker validated. Latent today (no production `.bni` has a cross-package const expression — full suite is green), but a landmine for the first such `.bni`.

**Fix status:**
- **(conservative, immediate) — ✅ DONE & LANDED `8dd35667`.** `defineBniConst` now skips folding a `.bni` const whose initializer references an `EXPR_SELECTOR` (new `exprRefsSelector` guard), leaving it value-less → the use site is cleanly rejected ("array length must be a constant integer") instead of silently miscompiled. Preserves gap (b)'s win (self-contained `.bni` consts like `cast(int8,0)-cast(int8,3)` still fold). Regression test: conformance `806_transitive_bni_const_rejected` (3-package, array-dim → clean reject). Verified builder-comp conformance 1473/0 + unit 45/45, gen2 1473/0. **Note:** this fixes the memory-unsafe array-dim facet; a transitive const used as a SHIFT count (`x << b.SZ`) is still silently wrong (IR-gen folds it to garbage) — pre-existing, addressed only by the proper fix.
- **(proper) — 🔴 OPEN.** Fix IR-gen's `RegisterImport` (`gen_register_import.bn`) to fold transitive `.bni` consts consistently with the checker (resolve `pkg/a`'s consts when loading `pkg/b`, regardless of whether the top-level importer references `pkg/a`). Then both `[b.SZ]int` = `[7]` and `x << b.SZ` are correct, and the `exprRefsSelector` guard can be dropped. Bigger (IR-gen transitive-const loading). MUST keep checker and IR-gen in lockstep.

Add a 3-package conformance regression test (`[b.SZ]int` + `x << b.SZ`) once fixed. **MUST keep the checker and IR-gen in lockstep** — the lesson of this regression is that making the checker fold MORE than IR-gen can creates silent disagreements.

## MAJOR (checker, REGRESSION) — a CAST-hidden negative constant shift count was silently treated as an overshift (was a runtime trap) (2026-06-15) — ✅ cast case FIXED & LANDED `c9cce5ef` (option B); residual SAME-CLASS gaps (a–g) below are OPEN follow-ups (land one-by-one)

**Symptom (was REPRODUCED on builder-comp).** `const N int8 = cast(int8, 0) - cast(int8, 3)` (== -3); `x << N` printed **0** instead of a `negative shift count` compile error. A negative shift count converted from a DETECTABLE trap into SILENT WRONG CODE.

**Root cause.** `9a6af307`'s `emitConstOvershiftOrNil` compares the count magnitude UNSIGNED, asserting "a genuinely negative count is rejected at compile time." FALSE: the checker's `checkShiftCountNonNegative` uses `evalConstIntValue` (`checker_util.bn`), which had NO `cast`/`bit_cast` case, so a cast-based const folded to nothing on the checker side and the gate did NOT reject. IR-gen's `evalConstExpr` (`gen_const_fold.bn`) DOES fold cast/bit_cast (value passthrough), so the count materialized as `OP_CONST_INT` -3 and the unsigned compare classified it as a huge overshift → 0. Deeper issue: the checker-vs-IR-gen const-fold ASYMMETRY.

**Fix (option B, chosen 2026-06-15).** `evalConstIntValue` gained an `EXPR_BUILTIN` case for `cast`/`bit_cast` that passes the operand value through unchanged — mirroring `evalConstExpr` EXACTLY (same passthrough, NO truncation), so the checker's const value stays consistent with codegen's. New `constIntFor` helper in `check_const.bn` makes `checkConstDecl` record a cast-based const's folded value (via `evalConstIntValue`) instead of defining it value-less, so the NAMED-const form (`x << N`) is caught, not just inline. Also fixed the two stale comments (TILDE/LitSign in `check_expr_binop_test.bn`; the `~`-of-uint64-max edge in `check_expr_unary.bn`). Tests: conformance `799_err_shift_negative_count_cast` (.error) + checker unit tests (cast named/inline, bit_cast, positive guard). Verified: full builder-comp conformance 1465/0; gen2 (builder-comp-comp) 1465/0; builder-comp + gen2 types unit tests green; compound `<<=` and const-to-const chain also rejected.

**Residual SAME-CLASS gaps (PRE-EXISTING — NOT introduced by this fix; all confirmed + reproduced by adversarial review 2026-06-15). "Close the asymmetry" is bigger than cast: `evalConstIntValue` still diverges from `evalConstExpr` on other negative-yielding const forms.**

- **(a) char-literal arithmetic — MAJOR (narrow) — ✅ FIXED & LANDED `81d2655b`.** `const N int = cast(int, 'A') - cast(int, 'Z')` (== -25); `x << N` → was silent **0**. `evalConstIntValue` had no `EXPR_CHAR_LIT` case (evalConstExpr does, `gen_const_fold.bn:43`); the cast case recursed into the subtraction whose leaves are char lits → ok=false → gate didn't reject. Fixed: added a `parseCharLiteral` (mirrors IR-gen's `parseCharLit`) and an `EXPR_CHAR_LIT` arm to `evalConstIntValue` + the array-dim shape helpers (`isConstShaped`/`dimFullyKnown`); side benefit — `['A']int` array dims now fold (were over-rejected). Tests: conformance `803_err_shift_negative_count_char` + checker unit tests. Verified builder-comp 1470/0, gen2 1470/0.
- **(b) selector-qualified cross-package const — MAJOR (broader) — ✅ FIXED & LANDED `2e783acd`.** imported `const N int8 = …`; `x << pkg.N` → was silent **0**, and `[pkg.SZ]int` was wrongly rejected as "not a constant integer". Turned out BIGGER than the review's "cheap arm": the `EXPR_SELECTOR` arm alone did nothing because `buildScopeFromFile` registered every imported `.bni` const VALUE-LESS (`defineConst`). Fixed: `evalConstIntValue` gained an `EXPR_SELECTOR` arm (`resolveQualifiedSym` → `SYM_CONST.ConstVal`) AND a new `defineBniConst` folds each imported const's initializer into the package scope + temp build scope — so EVERY imported const now folds (broad blast radius). Verified full builder-comp conformance 1472/0 + unit 45/45, gen2 1472/0 + unit 45/45. Tests: conformance `804` (cast-hidden negative via selector) + `805` (positive single + grouped const, array dim & shift). Residual sub-gap: **iota-grouped** `.bni` consts still stay value-less (narrow, pre-existing) — needs iota substitution ported into `bni_scope`.
- **(c) bit_cast sign-reinterpret — MAJOR — ✅ FIXED & LANDED `83abb2cb` (option 2, precise fold).** `const N int8 = bit_cast(int8, cast(uint8, 253))` (== -3 as int8); `x << N` was silent **0** (passthrough folded the SOURCE 253 → gate accepts → IR-gen overshift → 0); array dim was `[253]int`; yet the runtime value is -3 — incoherent. Fixed by folding bit_cast through a shared `types.ReinterpretBitCast` (truncate + sign-extend to the target width) used by BOTH `evalConstIntValue` and `evalConstExpr`, so the const folds to -3 in both → shift count rejected, array dim rejected (negative length), runtime value -3 — all consistent. `cast` stays passthrough (out-of-range narrowing is rejected upstream). Replaced the false-confidence unit test (it used an already-negative operand) with a genuinely-reinterpreting one + a positive guard + a direct `ReinterpretBitCast` test. Tests: conformance `807_err_shift_negative_count_bitcast`. Verified builder-comp conformance 1474/0 + unit 45/45, gen2 1474/0 + unit 45/45. (Edge: a >=64-bit target is passed through — the host int can't truncate it; fine for all current 64-bit-host build configs.)
- **(d) doc comment overstates parity — MINOR — ✅ RESOLVED (rewritten across `2e783acd`/`83abb2cb`/`767ca300`).** `evalConstIntValue`'s comment now accurately states what it folds (int/char literals, in-package + qualified const idents, unary/binary, cast passthrough, bit_cast via `ReinterpretBitCast`, sizeof/alignof) and the one `evalConstExpr` form it does NOT (`iota`, const-group only / non-negative).
- **(e) no POSITIVE end-to-end conformance test — MINOR.** Only the negative `799` exists. Add positive cast-const shift, compound `<<=`/`>>=`, cast-const array dim (indexing), and positive bit_cast-const shift tests (all pass today, per review).
- **(f) negated sizeof/alignof const shift count — MAJOR (pre-existing) — ✅ FIXED & LANDED `767ca300`.** `const N int = 0 - cast(int, sizeof(int)); x << N` was silent **0**: `evalConstIntValue` folded neither `sizeof` nor `alignof` (IR-gen's `evalConstExpr` does), so the negated count folded to nothing on the checker side and the gate didn't reject. Fixed: `evalConstIntValue`'s `EXPR_BUILTIN` arm now folds `sizeof`/`alignof` (resolve TypeRef → `SizeOf`/`AlignOf`), mirroring `evalConstExpr` → the gate rejects the negative; side benefit `[sizeof(T)]int` dims fold (were over-rejected). Gap (d)'s comment also corrected (the FALSE "no negative-shift risk" line is gone). Tests: conformance `808_err_shift_negative_count_sizeof` + checker unit tests. Verified builder-comp conformance 1475/0 + unit 45/45, gen2 1475/0 + unit 45/45. Now the only `evalConstExpr` form `evalConstIntValue` doesn't fold is `iota` (const-group only, non-negative — no shift risk).
- **(g) malformed char-literal escape in array-dim position — MINOR (diagnostic) — ✅ FIXED in worktree (pending land).** `['\q']int` was silently accepted as `[113]int` (array dims resolve via `evalConstInt → parseCharLiteral`, never `checkExpr`, so `checkCharLitEscapes` never ran). Fixed: new `validateDimCharLitEscapes` walks the dim expr and runs `checkCharLitEscapes` on each char literal (errors dedupe). Tests: conformance `809_err_dim_char_escape` + checker unit tests. Still TODO (separate): factor `parseCharLiteral`/`parseCharLit` into ONE shared decoder (currently duplicated `types`/`ir`, no tie test — drift risk); raw multi-byte char literal (`'é'`) accepted as its first UTF-8 byte (front-end leniency).
- **(e) positive end-to-end tests — MINOR — ✅ ADDED in worktree (pending land).** conformance `810_const_shift_and_dim_positive` (cast/bit_cast/char shifts, compound `<<=`/`>>=`, cast-const array dim + indexing) and `811_err_shift_negative_count_plain_selector` (plain non-cast negative imported const → reject; the path 804's cast-hidden form didn't cover). Still missing (per (c)/(f) review): positive `[bit_cast(...)]T` / `[sizeof(T)]int` / `[alignof(T)]int` array-dim end-to-end; cross-package `bit_cast`-in-`.bni` negative shift; negated-`alignof` `.error` + alignof checker unit tests.
- **(h) out-of-range narrowing `cast` is NOT fit-checked for a CONST-IDENT operand or in array-dim position — MAJOR (pre-existing silent miscompile; the FALSE invariant comment IS from (c)/(f)).** `const M int = 257; var a [cast(uint8, M)]int8` folds the dim to **257** but the same `cast(uint8, M)` evaluates to **1** at runtime (257 mod 256) — incoherent, wrong allocation size, fully silent (REPRODUCED). Also `[cast(int8, 200)]int` → `[200]int` though `cast(int8, 200)` is -56. Root cause: `checkBuiltinCall`'s cast fit-check (`check_builtin.bn:73`) is gated on `argType.HasLitVal`; a const-ident operand carries its value on `sym.ConstVal`, NOT `HasLitVal`, so the fit-check never fires — and array dims bypass `checkExpr` entirely. Same incoherence class (c) closed for bit_cast, left open for `cast`. PRE-EXISTING (predates c/f), BUT (c)/(f) added a load-bearing comment in `checker_util.bn` AND `gen_const_fold.bn` asserting "an out-of-range narrowing cast is rejected by the checker, so a cast that reaches here is in-range and the value is unchanged" — which is FALSE. **Action:** (1) correct the false comments NOW (attributable); (2) proper fix needs user decision — either extend the fit-check to consult `sym.ConstVal` for const-ident operands AND validate cast in array-dim position, or reject out-of-range narrowing in the const fold. Add `.error` conformance tests (`[cast(int8,200)]int`, `[cast(uint8,256)]int`, `cast(uint8, M)` with const M).
- **(i) `sizeof`/`alignof` missing from `isConstShaped` / `dimFullyKnown` — MINOR (consistency gap in (f)).** (f) added sizeof/alignof to `evalConstIntValue` but NOT to the array-dim deferral helpers, so `[sizeof(T) + forwardConst]int` (a sizeof term plus a later-declared const) isn't recognized as const-shaped (it's `[sizeof(T)]` that breaks the shape check). Fix: add an `EXPR_BUILTIN` arm (SIZEOF/ALIGNOF → true) to both `isConstShaped` and `dimFullyKnown`, matching how (a) added EXPR_CHAR_LIT — sizeof/alignof depend only on a resolvable type, so they're always "known". (The residual wrongness for a forward-const GLOBAL array dim is the separate pre-existing MAJOR IR-gen garbage-length bug below, not this.) Add a positive forward-ordered conformance test.
- **(j) alignof has no dedicated test coverage — MINOR.** (f) folds both sizeof and alignof but only sizeof is tested (808 + unit). Add negated-alignof `.error` + positive-alignof unit tests (a copy-paste `SizeOf`-instead-of-`AlignOf` regression would land green; note a shift-count or `len()` test can't distinguish the two — needs a size≠align type-identity observable, e.g. assign `[alignof([4]int32)]int` to `[4]int`).

## MAJOR (IR-gen) — a FORWARD-REFERENCED const used as an array dimension miscompiles to a GARBAGE stack size (2026-06-15) — 🔴 OPEN (pre-existing; surfaced by the cast-shift review, NOT caused by it)

`const USE int = BASE + 1; const BASE int = 7` declared AFTER `func main()`, used as `[USE]int`, deterministically emits `alloca [4071 x i64]` instead of `[8 x i64]`; the cast variant emits `[30 x i64]` instead of `[5 x i64]`. Reproduces IDENTICALLY on BUILDER `bnc-0.0.9` (predates the cast-shift fix), so it is pre-existing. The CHECKER computes the dim correctly (M6 dependency-ordered const resolution + `collectConstDeps` into `EXPR_BUILTIN` args); IR-gen's use-site array-dim resolution reads `moduleConsts` BEFORE the forward-declared consts are registered there → stale/garbage value. Silent wrong-size stack alloca (memory-safety). No conformance test covers forward-ref-const-as-array-dim. Fix: IR-gen must resolve all module consts (dependency-ordered, like the checker) before lowering use-site array dims, or defer dim resolution until consts are registered.

## DEFERRED (import resolution) — same-final-segment GENERICS collide (conformance/792, xfail) (2026-06-15)

The non-generic form of this bug — a package directly importing two packages
with the same final path segment double-emitting one's `_Package` declaration
(`invalid redefinition`) — was FIXED & LANDED (`e201f448`, approach B: the
loader keys import resolution on the full path, not the short name; full
investigation in claude-todo-done.md). The GENERIC form still collides:
`bb.Pick[int]` resolves to `aa.Pick[int]` (`100 100` not `100 200`), because the
generic-decl stash (`genericDeclPkgs` etc.) and the per-(decl,args)
monomorphized symbol aren't qualified by the full path. Fix: carry the full path
through the generic stash key + the monomorphization naming, mirroring what B
did for the non-generic registration. Tracked by conformance/792 (xfail).

---

## MINOR — cross-mode interface dispatch: test-coverage gaps + LP64 assumption (2026-06-14) — 🟡 OPEN

The shim-route that dispatches a native-only package's interface methods from
bytecode (landed `93f75f27` + the math/big extension `7c3b17a2`) is exercised by
726 (`strings.Builder` via `io.Writer`: a raw-slice arg, a scalar arg, a no-arg
method; scalar + multi-return) and 577 (`errors.Error`: no-arg, multi-return).
An adversarial review found these shapes UNTESTED — each needs a SYNTHETIC
native-only test package, since no current stdlib impl hits them:

- A VALUE-receiver iface method (`@__ivtshim` slot holds the thunk's handle, and
  `a0` = the iv-data ptr the thunk derefs). 410 covers native-to-native only.
- A method with MULTIPLE aggregate args (the `a1/a2/...` slot accounting).
- A FLOAT arg / float-containing aggregate (the shim's int-slot bitcast path).
- The `n>6` user-arg overflow guard (a negative test).

Latent, LP64-host-only (NOT active — default VM modes run a 64-bit host):
- `dispatchCompiledIfaceMethod`'s `resultSize > 8` aggregate-vs-scalar threshold
  (and `dispatchExternBinding`'s identical one) must track `isAggregateReturn`'s
  `> target.PointerSize`; on an ILP32 VM host a 5–8-byte aggregate return would
  pick the wrong shim shape. (Now commented in `vm_exec_iface.bn`.)
- 64-bit-scalar args pack as 2 slots on a 32-bit host (`argSlots`); the dispatch
  reads them as positional shim args.

Separately (PRE-EXISTING, independent of the VM): the COMPILED native iface-call
path (`emitCallIfaceMethod`) has no HFA classification — a struct-of-floats arg
is mis-seen as a GP aggregate (no `IsFloatScalarTyp`-style struct handling in the
native backend; the LLVM side relies on LLVM to classify HFAs).

---

## MINOR — remove the `impls/stdlib/common` compat symlink at the next BUILDER bump (2026-06-14) — 🟡 OPEN

`impls/stdlib/` was flattened (`impls/stdlib/common/pkg` → `impls/stdlib/pkg`,
`5ae15031`), but `scripts/binate-paths.sh` still emits `$BASE/impls/stdlib/common`
as the stdlib impl search root, and a `common -> .` symlink makes that resolve
against the flattened tree. The symlink exists ONLY because the pinned BUILDER
bundle (`bnc-0.0.9`) still ships a real `impls/stdlib/common/` dir, and
binate-paths uses one formula for both the current tree and the bundle base —
so emitting `$BASE/impls/stdlib` now would break gen1's resolution of the
bundle's stdlib.

**Do this once `BUILDER_VERSION` is bumped to a bnc cut from a tree at/after the
flatten** (any BUILDER built from main ≥ `5ae15031` ships `impls/stdlib/pkg`
directly, so `$blib/impls/stdlib` resolves):
1. Change `scripts/binate-paths.sh` (the `build_list impl` branch, ~line 169)
   from `$BASE/impls/stdlib/common` to `$BASE/impls/stdlib`.
2. `git rm impls/stdlib/common` (the symlink).
3. Sweep remaining `impls/stdlib/common` references: `scripts/fetch-builder.sh`
   (comment examples), `BUNDLE-HOWTO.md`, and the `pkg-layout-spec.md` /
   `impls/stdlib/README.md` notes that describe the symlink as transitional.
4. Verify: full `builder-comp` (gen1 from the new BUILDER + compile) green.

Until then the symlink is load-bearing — don't remove it without the
binate-paths change, and don't make the binate-paths change without a flattened
BUILDER.

## MAJOR — closure-shim cousins still use raw `ArgWords` for user words (latent funcval miscompile) — 🟡 OPEN

FOLLOW-UP to the now-resolved non-closure funcval-shim marshalling fix (full
diagnosis + Stage A/B + B0 Functions-table archived in claude-todo-done.md).
The non-closure shims were switched to `cc.EffectiveArgWords`, but the CLOSURE
shims were NOT:
- **(1) raw `ArgWords` for USER words** — `x64_closure_shim.bn:330` /
  `aarch64_closure_shim.bn:306` do `var nUw int = common.ArgWords(ut)` (no
  `EffectiveArgWords` exists in ANY closure-shim file). For an indirect-large
  user arg (managed-slice = 4 words, iface = 2) this over-counts vs. the
  dispatch caller's single-pointer placement, mis-shifting `inRegBase` /
  outgoing regs — latent wrong-code for closures with managed-slice/iface
  params.
- **(2) no float-scalar user-arg GP→FP marshalling** — the non-closure shim
  does this; the closure shims don't, so float closure params are mismarshalled.

Reference to mirror: the landed non-closure spill in
`pkg/binate/native/{x64,aarch64}/*_funcvalue_spill.bn` (uses
`cc.EffectiveArgWords`). No closure-spill/wide-closure conformance test exists
yet. B0's force-emit only emits NON-closure triples, so this doesn't block B0 —
ready-to-pick follow-up. (User owns.)

### Array composite-literal defects (indexed silent-miscompile; over-count OOB write) — spec Ch.13 (2026-06-12) — 🔴 OPEN
Found + verified firsthand authoring spec Ch.13 (read the type-check +
IR-gen; not run, but the code path is conclusive). Two MAJOR array-literal
defects; the type checker `checkArrayLit` (`check_expr_composite.bn:84-91`)
iterates elements positionally, never reading `el.Key`, and never checks
element count against `ArrayLen`; IR-gen `gen_composite.bn:149-152` stores
element `i` at index `i`.
- **Indexed array literals silently MISCOMPILE** (`expr.composite.array.indexed`,
  MAJOR wrong-code). `[5]int{1: 10, 3: 30}` is DECIDED (claude-notes.md:801) to
  mean `{0,10,0,30,0}`, but the keys are ignored and values stored positionally
  → `{10,30,0,0,0}`. Silent wrong values, no diagnostic, no test. Fix: in
  checkArrayLit/genArrayLit, when an element has a Key, fold it to a const index
  and place the value there (validate `index < N`, detect duplicates), zero-fill
  gaps — OR reject indexed-array syntax outright (user's call).
- **Array over-count not rejected → OUT-OF-BOUNDS stack writes** — ✅ RESOLVED 2026-06-12 (binate `910e08cb`; over-count reject only — indexed-literal + `[...]T` sub-items below remain OPEN). `checkArrayLit` now rejects `len(elems) > ArrayLen` with "too many elements in array literal" before IR-gen. conformance/740_array_overcount_rejected; full unit 45/0 + conformance 1407/0 native + 1389/0 VM (no previously-valid code rejected).
  - **Sibling found in self-review + fixed (binate `e81bfbbe`)**: NAMED array/slice composite literals (`type Row [3]int; Row{...}`) bypassed element validation ENTIRELY — `checkCompositeLit` routed a `TYP_NAMED` underlying to its element checker only for STRUCT underlyings, so named-array over-count (→ OOB) AND wrong-element-type (→ miscompile) were both silently accepted (exposed when named composite literals were enabled, `2eeb71c1`, which fixed IR-gen but not the checker). Fix: peel alias/const/named (`peelNamedBounded`) to the composite shape once up front so all element-check branches handle named + unnamed uniformly. conformance/742_named_array_lit_checked; 723/728 still green; full unit 45/0 + conformance 1408/0 native + 1390/0 VM.
  (`expr.composite.array.overcount`, MAJOR, latent memory-unsafety). `[3]int{1,2,3,4,5}`
  is accepted; `gen_composite.bn:149-152` emits stores at indices 0..4 into a
  3-element alloca → 2 out-of-bounds stack writes. Should be "too many elements
  in array literal". No test. (Struct over-count — the benign analogue, extra
  positional values silently discarded — ✅ RESOLVED 2026-06-12 binate
  `e185c9c4`: `checkStructLit` rejects `len(Elems) > len(Fields)` for a
  positional literal, "too many values in struct literal"; negative test
  `743_struct_overcount_rejected`. Applies to named structs too via the
  `peelNamedBounded` routing.)
- **Inferred-length `[...]T{...}` NOT IMPLEMENTED** (`expr.composite.array.inferred-len`).
  DECIDED (claude-notes.md:798) but the checker rejects it ("array length must be
  a constant integer"). Either wire it (substitute `len(Elems)` for the `...`
  marker) or mark deferred.
- **(minor) Positional struct-literal elements are not assignability-checked**
  (`check_expr_composite.bn:73-79` checks keyed but not positional values).
All referenced from `13-expressions.md`.

### `_Package()`: bytecode VM works only for the 4 builtins (Gap 2; unqualified form ✅ FIXED; builtin auto-injection ✅ LANDED) — 🔴 OPEN (user-package bytecode `_Package` remains)

> **Update 2026-06-12** — two related pieces landed on main:
> - **VM injection Part A** (binate `a8ba52f2`): `RegisterStandardExterns` now
>   auto-enumerates `rt._Package().Functions` (+ empty reflect) via
>   `registerPackageFunctions`, replacing the hand-maintained rt block. bootstrap
>   stays hand-bound (deprecation path + extern-heavy; table skips `IsExtern`);
>   the 3 `_Package` accessors + 2 trampolines stay hand-bound. See
>   `plan-vm-package-injection.md` Part A.
> - **`_Package` self-listing** (binate `53ea3875`): every package self-lists its
>   own `_Package` accessor as the last `Functions` entry (closing the reflection
>   gap), and `--pkg` compilation force-loads reflect (`ensureReflectLoaded`) so
>   it holds even for packages that don't import reflect — i.e. `cmd/bnc` now
>   force-loads reflect on ALL paths (main/test already did; `compileSinglePkg`
>   now too). fv stashed on `ir.Module.PackageAccessorSig` → byte-identical
>   LLVM/native entry (Name `<pkg>._Package`, ResultSize 8, ParamSlots 0, Sig
>   `()(@pkg/builtins/reflect.Package)`). Validated: builder-comp 1395/0,
>   builder-comp-int 1360/0, reflect byte-identical across LLVM/native-aa64/native-x64.
>   Follow-ups (binate `2988cda4`, `6d052181`): arm32 (ILP32) per-mode `expected`
>   overrides for 725/727 — the self-entry's ResultSize is `ptrSize()` (4 on
>   ILP32, 8 on LP64), breaking target-independence (⚠️ NOT verified locally —
>   no qemu; needs arm32 CI confirmation); plus native unit tests
>   (`TestEmitPackageDescriptorSelfListsPackage{AA64,X64}`) for the self-listing.
> - **Still open (the core Gap 2 below)**: user/stdlib packages compiled to
>   BYTECODE still have no `_Package` body → Part B (§2a of the VM-injection plan).
>   The `cmd/bni`-doesn't-force-load-reflect asymmetry below is still accurate
>   (the fix above is `cmd/bnc`-side only).

The compiler synthesizes a `_Package() @reflect.Package` accessor per package
returning the package's immortal static-managed descriptor (Phase B,
notes-package-introspection.md).  `codegen/emit_pkg_descriptor.bn` (+
`native/{x64,aarch64}/_pkg_descriptor.bn`) emit it as a NATIVE function; the
checker synthesizes its signature in BOTH the qualified-access arm
(`check_expr_access.bn`) and the unqualified `checkIdent` arm
(`check_expr.bn`).  Two gaps, surfaced 2026-06-11 by writing
`conformance/708_reflect_package_all_kinds` (user-requested "every package has a
`_Package`" coverage):

- **Gap 1 — no unqualified form (checker) — ✅ FIXED (binate `1164ef04`).** An
  UNQUALIFIED `_Package()` (the current package's own accessor) was `undefined:
  _Package`; now it type-checks and lowers like a normal exported function,
  callable unqualified within AND qualified from importers.  `checkIdent`
  (`check_expr.bn`) synthesizes the `() @reflect.Package` type; IR-gen's
  `registerCurrentModulePackageAccessor` (`gen_import.bn`) registers the current
  module's `_Package` FuncSig so the bare-ident call path lowers it to the local
  symbol `emit_pkg_descriptor.bn` emits.  Compiled modes only — VM still hits
  Gap 2.  Pinned by `conformance/709_reflect_package_unqualified` (compiled PASS,
  3 VM modes xfailed for Gap 2).
- **Gap 2 — VM works only for builtins (MAJOR VM-backend project; DEFERRED).**
  `_Package()` is emitted only as a native function; the bytecode VM reaches
  `_Package` ONLY for the four builtin packages, via the HARDCODED externs in
  `vm/extern_register_std.bn`.  A user/stdlib package compiled to bytecode has no
  native `_Package` symbol → `vm: extern not found: <pkg>._Package`.  The extern
  approach CANNOT work for bytecode-compiled packages.  Fix: emit `_Package()` +
  its static-managed descriptor as BYTECODE per package (the VM equivalent of
  `emit_pkg_descriptor`) so the VM runs it directly, dropping the
  hardcoded-builtin extern table.  Major VM-backend work — the user explicitly
  deferred this.  (Subsumes a sibling asymmetry: `cmd/bni` does not force-load
  reflect the way `cmd/bnc` does — `ensureReflectLoaded` is cmd/bnc-only — so
  reflect-dependent type-checking under the VM needs an explicit reflect import;
  709 imports reflect for exactly this reason.  When the VM emits `_Package`, it
  will force-load reflect too.)
- **Test**: `708_reflect_package_all_kinds` pins `<pkg>._Package().Name` == import
  path for a user package + all four builtins + a stdlib package.  PASSES on the
  3 compiled modes; **xfailed on the 3 VM modes** (`-int`/`-int-int`/`-comp-int`)
  for Gap 2 (int-int also hits the pre-existing multi-package double-VM failure).

## CR-2 Plan-1 Round-2 + Plan-A — closing adversarial review (2026-06-09): SIBLING gaps in the just-landed fixes

A 28-agent adversarial review of the 9 landed CR-2 Round-2 + Plan-A fixes (the same review style that found the Round-1 siblings) — verdicts triaged below against the code + (where noted) runtime probes. **Headline: the recurring pattern recurred — several of THIS round's fixes peeled/guarded SOME sites sharing a root cause and left siblings broken.** All are PRE-EXISTING/latent (variants the landed fixes didn't cover; none is a regression from the fixes — they're the *un*covered cousins). Filed per the bug-discovery protocol; **fix decisions are the user's.**

> ⚠️ **The two reviews MASSIVELY over-confirmed via static reasoning — runtime-verify before acting on ANY finding here.** (1) The 28-agent closing review's 6 "confirmed" gaps reduced under runtime probing to: 1 real (S1, fixed `5c9b00e1`) + 2 niche real-rejections (S3/S4, filed) + 3 false positives (S2/S5/S6). (2) A follow-up 32-agent sweep (verifying S1 + hunting more un-peel siblings) flagged **21 further candidate sites** in `gen_selector` fallback arms / `gen_access` (readonly/named/alias slice+array+ptr indexing) / `gen_iface` ptr-to-readonly-iface — **ALL runtime-refuted**: one probe per distinct category (`mk().v`, `(*p).v`, slice-of-`@readonly Box` field, `readonly @[]int` index, `[2]readonly int` struct field, `*readonly @Getter` dispatch) returns the CORRECT value; named-array variants don't even parse. The static agents flag `.Elem` reads without tracing that the type arrives ALREADY-unwrapped (return-coercion strips readonly; predicate guards peel before the arm). The sweep DID verify the S1 fix + the A2 revert are correct/clean. **Net real bugs from BOTH reviews: S1 (fixed) + S3/S4 (filed niche). Do not chase the 21 phantoms.**

### [closing-review] Triaged verdicts — RUNTIME-verified (the review's static verify phase over-confirmed: of 6 "confirmed", 1 was a clean real fix, 3 are false positives, 2 are real rejections whose type-only fix is a compile→SIGSEGV regression)

**✅ RESOLVED**
- **CRITICAL — `getSelectorType` un-peeled pointee** (`gen_selector_type.bn:56,63`) — ✅ landed `5c9b00e1`. Read the un-peeled `.Elem.Name` of a managed/raw ptr-to-struct base; `@readonly Box`/alias base → `""` → nil; `rp.inr.x` folded to const-0. R2-D1 sibling. Fixed with `peelTransparent(peelTransparent(baseTyp).Elem).Name` (peel the base's own alias wrapper too — an alias base has nil `.Elem`). Cell `regressions/nested-selector-readonly-pointee`, 7 modes.

**⚠️ REAL reject, but the type-only fix is a compile→SIGSEGV safety regression (needs an IR-gen companion) — per the user (2026-06-09): FILE as a known limitation, do NOT pursue the IR-gen work now. Type fixes were prototyped + REVERTED.**
- **MAJOR — alias receiver unsupported for METHOD VALUES** (`pkg/binate/types/check_expr_access.bn:249` + IR-gen): `type AB = @Box; var mv = ab.getV` is rejected ("undefined: getV") because the method-value path calls `ReceiverBaseNamed()` on the un-alias-peeled `origXt`. Peeling it (`resolveAliasAndConst(origXt).ReceiverBaseNamed()`) makes it type-check, but the method-value CLOSURE layout (`gen_method_value.bn`) doesn't peel the alias → runtime **SIGSEGV**. A DIRECT method value (`p.getV`) works; only the alias receiver is broken. Niche (method values × alias receiver). To fix properly: type peel + peel the alias in the closure-capture IR-gen.
- **MAJOR — alias receiver unsupported for IMPL declarations** (`pkg/binate/types/check_impl.bn:90` + dispatch): `type AB = *Box; impl AB : Getter` is rejected ("impl receiver must be (a wrapper around) a named type") because `checkImplSatisfaction` calls `ReceiverBaseNamed()` on the possibly-`TYP_ALIAS` `recv`. Peeling it accepts the impl, but dispatch through the alias-impl iface value → runtime **SIGSEGV**. Niche (impl on alias receiver). To fix properly: type peel + alias handling in impl/vtable dispatch.

**❌ REFUTED / non-exploitable — RUNTIME-verified; do NOT act**
- **R2-D6 ALIAS cycles** (flagged CRITICAL) — **REFUTED**: `type A = B; type B = A` does NOT hang (3 variants tested; compiles + runs). `type A = B` with `B` forward sets `A.Target` to a `TYP_NAMED` forward (not a `TYP_ALIAS`), so `resolveAliasAndConst`'s loop terminates at the named type — the cycle the review imagined isn't formed. The static "unguarded loop" claim missed the forward-decl resolution.
- **R2-D2 named-array `peelReadonly`** (flagged MAJOR) — **REFUTED**: named-distinct array types (`type Arr [N]S`) don't PARSE (syntax error), and alias arrays (`type Arr = [N]S`) resolve via `indexExprType` and work (`a[i][j].x` → 9). The `peelReadonly`-vs-`peelTransparent` gap doesn't manifest for arrays.
- **R2-D6 unbounded `Underlying`-walkers** (`NeedsDestruction`/`SizeOf`/`AlignOf`/`discoverStructFromType`) (flagged MAJOR) — **non-exploitable**: only reachable via a cycle; named cycles are decl-time-rejected + broken (`Underlying=nil`), and alias cycles don't form (above). No reachable hang; `peelNamedBounded` on the 4 comparison predicates is sufficient. (Bounding them anyway is harmless defense-in-depth if ever wanted, but defends an unreachable state.)
- **gen_stmt.bn:259 genDecl iface boxing** (flagged CRITICAL R2-D4 sibling) — **REFUTED**: runtime-verified `var iv readonly @Getter = im; iv.get()` → 7. `genExprOrFuncRef` boxes before the unpeeled `typ.Kind` check, so the skipped re-box at :259 is harmless.
- **LowerOneFunc / LowerOneFuncShadow missing externNameConflict** (flagged CRITICAL A2 sibling) — **MOOT**: A2 was reverted as a misdiagnosis; the guard no longer exists.

### [closing-review] Coverage gaps (lower priority — add tests)
R2-D7: no readonly/alias-wrapped named-int or named-float-minus test. R2-D5: matrix covers only `type AB = @Box` (not alias-over-readonly / value-receiver alias). R2-D4: only managed `readonly @Iface` construct un-xfailed (no `readonly *Iface`, no return/arg-pass position). A1: no float-scalar / named-sub-word / box-in-loop box test.

---

## CR-2 follow-up batch adversarial review (2026-06-09) — post-landing

Adversarial review (find → perspective-diverse cross-examine → synthesize, 56 agents)
of the 8 landed CR-2 follow-up commits (R2-1 `79ebfa98`, R2-2 `d086ccac`, B2
`e15680d7`, B1 `05901f97`, B4 `b4648200`, B3 `5fc5a52f`, R2-3 `ca155319`, split
`2beab6e5`). **Heeding the over-confirmation caution at the top of this file, the
three critical/major entries below were RUNTIME-verified by hand (gen1/gen2 bnc
built from the worktree + an A/B against BUILDER bnc-0.0.7), not just statically.**
Two of the serious findings are regressions in THIS batch's own commits.

- **CRITICAL — X2** (R2-3 `ca155319`): the new negative-offset `panic` false-fires
  on valid code (iface-value upcast to an unrelated zero-method interface).
  **✅ RESOLVED 2026-06-10 (binate `4ac123da`)** — root-caused as a checker
  duck-typing hole; fixed via `isUniverseAny` + supported `@Iface -> *Iface`
  decay (fork B). Full entry under ## CRITICAL.
- **MAJOR — B1/X3** (`05901f97`/`5fc5a52f`): bare const-group member drops its
  inherited narrow type → checker accepts an overflow the explicit form rejects,
  IR truncates (silent wrong value). Full entry under ## MAJOR. Straight bug fix.
- **MAJOR — B2** (pre-existing, NOT from `e15680d7`): named func-value types
  (`type Fn @func(...)`) are unconstructible. Full entry under ## MAJOR.

**Lower-severity / follow-up (not yet runtime-triaged unless noted):**
- **X3-highbit (major, DIRECTION CONTESTED — semantics-owned).** `1<<iota` now
  folds in the checker (B1), so a flag member hitting the SIGN bit of a signed
  target (`1<<63` → `int` on 64-bit; `1<<31` on 32-bit) computes positive
  2^(W-1), which `FitsSigned(W)` rejects — while IR's `evalConstExpr` wraps to the
  valid two's-complement `INT_MIN`. A real checker-vs-IR divergence, but the
  RESOLUTION is a spec call: `claude-notes.md` §const decides const values are
  abstract and must fit the target range (→ the reject may be CORRECT; the
  canonical idiom uses an UNSIGNED target, unaffected). Do NOT change semantics
  unilaterally. (The literal `1<<63` form was already rejected pre-B1; B1 only
  widens that to the iota form without aligning IR.)
- **X2b (major, derivative/pre-existing).** The VM upcast path (`vm_exec_iface.bn`)
  reacts to the SAME checker-accepted upcast with a runtime abort (`iface_upcast:
  target vtable not found`) — a third distinct behavior. Not touched by R2-3.
  Whatever fixes X2 must reconcile all four consumers (LLVM/aa64/x64/VM).
- **B3 type-divergence (minor) — ✅ RESOLVED 2026-06-10 (binate `b9d6d807`).** A bare
  const member that PARKS (REPL) used to resolve via `GenConstMember` (reads only
  `d.TypeRef`=nil → untyped int), whereas the non-parked sibling got the inherited
  type via `genConstGroup`. Fixed by the B1/X3 fix: `checkGroupDeclTentative` now
  threads the inherited type onto the synthesized repeat, so the parked member
  carries `d.TypeRef`=the inherited type and resolves at that width.
- **✅ RESOLVED 2026-06-10 (binate `e16d53bc`) — the four cheap CR-2-review minors:**
  - arm32 xfail rationale (value-struct-large linux+baremetal): corrected to the
    real cause (shared IR-gen readonly field-read defect / Defect 1), matching the
    sibling value-struct markers verbatim so both clean up together (was an XPASS
    landmine).
  - `IsByvalParam` unbounded peel: routed through `peelNamedBounded` (1024 cap),
    behaviour-identical for valid types.
  - stale `gen_func.bn` comment: rewritten to the actual mechanism (`IsByvalParamRef`
    flag drives `OP_STORE`'s memcpy; `ParamIndex` is debug-info only).
  - B3 test: added the `IotaIdx == 1` assertion (mirrors the sibling iota test).

REFUTED by cross-examination (recorded so they aren't re-chased): no other
`emitRef`/`emitValRef` global-ref drop sites beyond OP_CAST + iface-arg (R2-2 clean);
B2's `=` change correct for multi-assign/non-func-LHS; the split (`2beab6e5`) moved
all functions/tests intact; B4 regression tests are non-vacuous.

---

## CRITICAL

---

## MAJOR

### Named func-value type (`type Fn @func(...)`) — func-LITERAL construction still rejected — literal-half follow-up 🟡 OPEN
- **Symptom**: `type Fn @func(int) int; var f Fn = func(x int) int {...}` is rejected (a bare `@func` literal isn't `Identical`/assignable to the nominal `Fn`). The func-REFERENCE half (`var f Fn = dbl`) and value-rejection (`var f Fn = g`) already work — see archived diagnosis.
- **Test**: `conformance/regressions/named-func-value-construct-literal` (xfailed all 11 modes).
- **Fix (3 sites, none peel TYP_NAMED yet)**: `checkFuncLit` (`check_func_lit.bn:83`) must RETURN the named type when hinted by one (gates on `ExpectedFVType.Kind == TYP_FUNC_VALUE` only); `checkExprWithFVHint` (`check_expr.bn:32`) must peel TYP_NAMED before installing the FV hint (currently ignores non-FUNC_VALUE/MANAGED_FUNC_VALUE hints, so a `Fn`=TYP_NAMED hint is dropped); `isManagedFuncValueLit` (`gen_func_lit.bn:188-194`) must peel TYP_NAMED (keys on `TYP_MANAGED_FUNC_VALUE`).
- **Memory-sensitive**: a func literal can CAPTURE, so the stack-vs-heap-alloc + refcount classification must be right — validate under guard-malloc.
- **Severity**: MAJOR (spurious compile-time rejection, fail-safe, no miscompile). Workaround: anonymous `@func(...)` spelling.
- (Full resolved REF-half diagnosis — design decision, root cause, IR-gen `gen_typedecl.bn` fix — archived in claude-todo-done.md.)

## CR-2 Plan-1 Adversarial Review — pre-existing sibling miscompiles (2026-06-08)

An adversarial multi-agent review (53 agents) + hand-verification of the CR-2
Plan-1 defect fixes (Defects 1–9). **Headline: the landed fixes are correct
for exactly what they claimed, but INCOMPLETE — each peeled/migrated at SOME of
the sites sharing its root cause and left the siblings broken.** These siblings
are PRE-EXISTING miscompiles (no Plan-1 fix touched them; C1's pre-existence
was confirmed by building a pre-fix compiler) — **none is a regression
introduced by the fixes**, and no green test went red. The recurring root
causes: (R1) wrapper-transparency peeled in predicates but not at the consuming
extraction / call-convention / construction sites; (R2) `isAggregateAllocToLoad`
migrated to only 2 of ≥6 aggregate-store/arg arms; (R3) the multi-return
slot-typing fallback landed in `:=` but not `=`; plus the Defect-9 `-` fix
gating on `TYP_INT` (not peeling `TYP_NAMED`). Each fix is a peel-at-the-
consuming-site / swap-the-guard one-liner + xfail-then-fix coverage; all ship
green because no test exercises the wrapped / nameless / composite-literal /
named-type variant. Per the user (2026-06-08): FILE all, FIX nothing yet.
The CRITICAL entries below are also surfaced in `## CRITICAL`-class triage.

### [CR-2 Plan-1 review] MINOR / doc-comment & xfail-hygiene corrections (2026-06-08)
- **N2 / N3 / N10 / N11 — ✅ DONE**: N2 (dead `peelTransparent` comment in `gen_iface.bn`) and N10/N11 (stale iface/funcval-multi-return xfail markers) were resolved in-tree by later work (verified absent); N3 (the false "deferred to the concrete instantiation" comparability comments + an xfail `eq[@[]int]` cell, `conformance/772`) landed binate `15946a55`. See claude-todo-done.md.
- **N1 (narrow, pre-existing) — ✅ RESOLVED 2026-06-12 (`11f99ed9`)**: an out-of-range CONSTANT shift count was wrapped into [0,width) by `ensureWidth` BEFORE the overshift guard (`v << 256` on uint8 → 1 not 0; signed `int8 >> 256` stays -64 not sign-filled; same in `<<=`/`>>=`). New `emitConstOvershiftOrNil` (`gen_binary.bn`) detects a constant count `>= width` from its ORIGINAL (pre-`ensureWidth`) `IntVal` and emits the spec result directly — 0 (logical `<<`/unsigned `>>`) or sign-fill `lhs >> (W-1)` (signed `>>`), the SAME result `emitGuardedShift` already produces for a runtime overshift (VM-consistent — the path the reverted "widen the value" attempt regressed). Wired into BOTH `genBinaryExpr` and `emitCompoundBinop`, before each truncates the count. Keying on `IntVal` also covers a wider-TYPED constant count (uint16 const 256 shifting a uint8). `conformance/729_const_shift_overshift` green on LLVM / both VM lanes / native aa64 / native x64-darwin; the 48 existing runtime-count shift/overshift cases + ir unit tests unaffected. (The **runtime** count-wider corner (c) is now also ✅ RESOLVED — binate `0db709a1` reads the UNTRUNCATED count so a runtime count wider than the value is detected. Related shift hardening landed alongside: a runtime **negative** shift count now panics — `6bf1efab`, `runtime error: negative shift count` — and a constant negative count is a compile error — `f6b9ebce`; plus the guard-free `unsafe_shl`/`unsafe_shr` intrinsics — `c9a6ed36`. Spec updated: §13.5 `expr.shift.overshift`/`expr.shift.negative`, §15.8, §17.5, §21.)
- **Coverage-only (verified-correct paths)**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value receiver rows have no conformance/unit coverage (the reject paths are soundness-critical and the TYP_POINTER/TYP_MANAGED_PTR arms are duplicated).

## CRITICAL

### abi-matrix multi-return-through-dispatch cells lack a managed-component type — 🟡 OPEN
- **Coverage gap (residual of the `=`-multibind fix, full diagnosis archived in claude-todo-done.md).** The `=`/`:=` × {direct, iface-dispatch, func-value} multi-return abi-matrix cells (`conformance/matrix/abi/*multi-return*`) all use value-only component types — `MR_TYPES = {"int","u16","f64"}` in `conformance/gen-abi-matrix.py`. None binds a managed component (`@[]T` / `@T`), which is exactly the surface that hid the original mistyping bug (a managed component skipped its Axiom-3 copy-RefInc). 
- The managed-through-dispatch path is currently covered only at the IR-unit level (`gen_assign_multi_test.bn` TestMultiAssignFuncValueCallCopyRefInc), not end-to-end in conformance.
- **TODO**: extend `gen-abi-matrix.py` with a managed-component type for the multi-return-through-dispatch cells (both `:=` and `=` forms), regenerate the matrix, and confirm the 200k-iter-style refcount balance holds end-to-end.

### bnc IR-gen — remaining super-linear factors (perf, for very large programs) — 🟡 OPEN
The minbasic OOM that motivated this is FIXED (fix (1) — O(1) dtor-dedup, binate
`7804c287`; minbasic now ~1 s / 27 MB, was >8.5 GB / OOM).  Full diagnosis
archived in [claude-todo-done.md](claude-todo-done.md).  These secondary
super-linear factors remain — none blocks correctness, but they bite
even-larger programs (the unifying disease: no memoization on the `@types.Type`
node + module-global accumulators scanned/re-mangled linearly):
- **(2) memoize `@types.Type` queries** — add cache slots to `@types.Type`
  (`types.bni`) and memoize `NeedsDestruction` + `SizeOf`/`AlignOf`/`FieldOffset`
  + the dtor/copy name (layout is fixed within a compile); today each is
  recomputed at every emit-site.
- **(3) capacity-doubling `slices.Append`** — it does `make_slice(n+1)` +
  copy-all per append → O(n²) for the hot IR-gen accumulators
  (`pendingStructDtors`, `ctx.Temps`, `ctx.Vars`, return `vals`); give it
  amortized-O(1) growth or use growable buffers for those.
- **(4) compact per-function managed-cleanup list** — `emitDecForManagedLocals`
  re-scans ALL `ctx.Vars` at each scope-exit; track cleanup slots in a compact
  per-function list instead.
- Minor: `resolveTypeExpr` allocates a fresh `@Type` per occurrence (no
  interning); `lookupFuncParams`/`collectFuncStrings` do O(n) linear scans.

### Differential scalar harness (`matrix/scalar-diff`) landed — two backend defects found: `vm-int-to-float32` and `aa64-subword` — CONFIRMED
- **What landed**: `conformance/gen-diff-scalar.py` + 41 cells / 1707 tuples
  under `conformance/matrix/scalar-diff/` — a property-based **differential**
  value-correctness harness for scalar shifts & conversions. Oracle is the
  **spec** (computed at full precision, independently validated by a 5-reader
  adversarial pass), not a backend, so spec-divergences (the shift-bug class)
  are caught too. Self-checking cells (`println(cast(int, computed == spec))`)
  for target-stability across 32/64-bit. Green on all LLVM modes + arm32
  baremetal; the two clusters below are xfailed (verified non-stale via
  `--check-xpass`). Idempotent generator; `int↔int` casts and all shifts pass
  on every real backend (broadened regression net for `32fde83d`).
- **`vm-int-to-float32` — VM `int → float32` is broken (every width/sign) — ✅ RESOLVED 2026-06-12 (binate `289420b6`)**:
  every `cast(float32, <int>)` diverged — even `cast(float32, 1) > 0.0` was
  false on the VM. Root cause: `int → float` lowered to `BC_SITOF`/`BC_UITOF`,
  which land at **float64**; the VM's float32 register form is the float32 IEEE
  bits in the low 4 bytes, so the float64 pattern's low word (usually zero) read
  back as ~0. Fix: fused `BC_SITOF32`/`BC_UITOF32` opcodes that write the
  float32 bit pattern directly, selected in `lower_cast` when the cast dest is
  float32 (signedness still picks signed/unsigned). Un-xfailed **16 of 17** VM
  cells across all 3 VM modes; 3 VM unit tests added (lowering decision ×2 +
  end-to-end round-trip). The 17th cell (`float-to-int/64/unsigned`) uncovered a
  **distinct sibling bug** (`vm-float32-to-unsigned`, now also resolved — see
  below).
- **`vm-float32-to-unsigned` — VM `float32 → unsigned int` used the SIGNED conversion — ✅ RESOLVED 2026-06-12 (binate `3fd7e712`)**:
  surfaced while fixing `vm-int-to-float32`. `lower_cast`'s `float → int` arm
  picked `BC_F32TOSI` (signed) for a float32 source regardless of dest sign
  (its comment admitted "float32 → unsigned is not yet exercised; it stays on
  the signed `BC_F32TOSI`"). So `cast(uint64, <float32 ≥ 2^63>)` saturated to
  `INT64_MAX` instead of the in-range unsigned value — a *defined* (in-range)
  conversion miscompiled, MINOR (only float32→uint64 of values ≥ 2^63; the
  8/16/32-bit unsigned high-bit values fit signed int64 so those cells already
  passed). Fix: the exact mirror of the float64→unsigned `BC_FTOUI` — added a
  `BC_F32TOUI` opcode (`cast(int, cast(uint64, <float32>))`), picked in
  `lower_cast` for a float32 source with an unsigned dest. Un-xfailed the last
  scalar-diff VM cell (`float-to-int/64/unsigned`, the 2^63 round-trip) across
  all 3 VM modes; 2 unit tests added (lowering decision + high-bit round-trip).
  **All scalar-diff conversion cells are now green on every VM mode** — the VM
  int↔float32 story is complete in both directions.
- **`aa64-subword` — native-aa64 doesn't narrow/sign-extend sub-word results**:
  a sub-word op leaves dirty high bits / wrong sign. `int8(-128) << 1` keeps
  bit 8 set (so `== 0` fails); `cast(int8, 128:uint8)` and the other
  `uint8 → int{8,16}` casts are wrong. 17 xfailed cells: `shl`/`shr` 8/16/32
  **signed**, all 8 `int-cast`, signed sub-word `float-to-int`/`int-to-float`.
  64-bit and most unsigned paths are fine. The native sibling of the VM/native
  sub-word-narrowing gap above, here confirmed across shifts/casts/conversions
  (not just arithmetic). Fix: post-op narrow + sign-extend sub-word results in
  the aa64 backend (or an IR-gen narrow — the shared P3 design call).
- **native-x64 / arm32-linux not evaluated**: the host lacks x86_64 C runtime
  headers (`stdio.h` → every native-x64 cell `COMPILE_ERROR`s uniformly, an env
  limitation, *not* a backend result — no x64 xfails placed), and `arm32-linux`
  needs `qemu-arm` (skipped). Re-check on an x64 host: the aa64 sub-word defect
  very likely has an x64 analog needing its own xfails.
- **Discovery**: 2026-06-06, differential-harness v1 (plan-differential-testing.md).
- **v2 (arith/cmp/bitwise) — LANDED 2026-06-06** (binate `42ad4fa0` fix +
  `e71de1e0` harness): 123 cells / 5415 tuples total. v2 found+fixed the LLVM
  `~` bug (`bitnot-result-type`, above). Remaining divergences, all xfailed
  (`--check-xpass`-clean) and in the known classes: VM
  `bitwise/not/{8,16,32}/unsigned` (sub-word `~` dirty bits); native-aa64
  sub-word *signed* `arith/{add,sub,mul}/8`, `bitwise/{and,or,xor}/{8,16}`,
  `cmp/{8,16,32}`, `bitwise/not/*/unsigned`. Float compares incl. NaN/Inf/-0 pin
  the ordered/unordered `==`/`!=` semantics (corrected 2026-06-06). `fcmp/32`
  was xfailed at first but the float32-compare fix (binate `fc11d862`) landed
  concurrently, so it un-xfailed at land time (`--check-xpass` flagged the
  XPASS). The remaining VM `float32` *conversion* xfails (`int-to-float` /
  `float-to-int` / `float-cast`) stand — that gap is separate from compare.

### Audit the home of generic low-level helpers shared by cmd/bni + the REPL engine (low priority / code-org)
- **Context**: extracting the REPL engine to `pkg/binate/repl` (Stage 4c
  of `plan-repl-embeddable.md`) needs generic helpers that ALSO stay in
  cmd/bni: `streq`, `appendCharSlice`, `appendFilePtr`, `appendImportSpec`,
  `readFile`, `quotePath` (+ the IR-gen import-registration subtree
  `registerPkgImports`/`registerMainImports`/`loadBuiltinBNIs`/
  `ensureBootstrapLoaded`/`addLoaderPaths`).  For 4c these are
  **DUPLICATED** (each package keeps its own copy) to avoid a weird
  dependency (runProgram/runTests pulling in `pkg/binate/repl` just for
  `streq`).  `pkg/binate/buf` is the WRONG home (it owns CharBuf/CopyStr;
  `readFile`/`quotePath` don't belong there).
- **What to audit**: where these generic string / slice / file / IR-gen
  helpers SHOULD live long-term.  Survey the codebase for the real
  commonalities (who needs `streq`, `readFile`, the import-registration
  helpers?) and decide: a genuinely-shared tier-2 package (a possibly-
  uselessly-named `pkg/binate/utils`? a split between string-utils /
  file-utils / ir-import-helpers?), vs leaving the small ones duplicated.
  Consolidate the 4c duplicates once decided.

---

## MINOR

### Stdlib conformance tests: relax conformance-imports + add a conformance/stdlib/* suite — 2026-06-10
`pkg/std/os` (and stdlib packages generally) have unit tests but no
conformance coverage, because the `conformance-imports` hygiene check
(`scripts/hygiene/`) restricts what a conformance test may import — it
keeps the conformance set focused on the *language core*. In Binate the
stdlib is deliberately SEPARATE from the core language, so stdlib
conformance belongs in its own suite rather than mixed into the language
conformance tree.
- **Relax the check** so a conformance test may import core / builtins
  (per `pkg-layout-spec.md` — importing the always-bundled core is part
  of the language contract, not a stdlib dependency). Scope the
  relaxation precisely to what the spec sanctions; don't open it to
  arbitrary stdlib imports in the language conformance set.
- **Add a separate stdlib conformance suite** (e.g. `conformance/stdlib/*`)
  with its own runner wiring, so stdlib packages (`os` first) get
  end-to-end coverage across modes without polluting the language
  conformance set.
- Follow-up to landing `pkg/std/os` (binate `3ca36c82`), which shipped
  with libc unit tests only — conformance was deferred here per the user.

### Generic struct/interface instantiation skips constraint satisfaction — spec Ch.12 (2026-06-12) — 🔴 OPEN
Found authoring spec Ch.12 (verified via toolchain probes through
builder-comp). MAJOR (the spec implies it's enforced; it isn't) but it
doesn't miscompile. (The sibling "generic methods accepted at
declaration" defect is ✅ FIXED — rejected at collection time, binate
`a7e0beb2`; see claude-todo-done.md.)
- **Constraint satisfaction unchecked for generic struct/interface instantiation**
  (`gen.satisfy.struct-iface-unchecked`). `typeSatisfiesConstraint`/
  `reportConstraintMiss` are called ONLY from `instantiateGenericFunc`
  (`check_generic.bn:259-264`); `buildInstantiatedStruct` (:196-218) and
  `buildInstantiatedInterface` (:115-138) install the type-param scope but make
  NO satisfaction call. So `type Box[T lang.Orderable] struct{val T}`
  instantiated as `Box[NoOrder]` (no `impl NoOrder : Orderable`) compiles clean.
  Generic-FUNCTION constraint checking works correctly.

### Value-receiver "always readonly" not enforced — spec Ch.10 (2026-06-12)
MINOR (design-intent vs impl; no correctness bug — by-value copy makes any
mutation harmless). `claude-notes.md:359` says a value receiver `(r T)` is
"always readonly". The checker does NOT enforce it: `receiverShape`
(`check_method.bn:251-285`) classifies a plain `(r T)` as kind 0 with
`isObjectConst=false`, and no pass rejects `r.field = ...` in the body — the
mutation just modifies the discarded copy. Decide: enforce read-only on value
receivers (a checker addition + a diagnostic), or downgrade the design note to
"the receiver is a copy; mutations are local" (the implemented semantics, which
the spec `func.method.value-recv` currently describes). Referenced from
`10-functions-methods-function-values.md`.

### Layout follow-ups surfaced authoring spec Ch.7.13 (Type Layout) — 2026-06-12
Both referenced from the spec (`07b-type-layout.md`).
- **`type.layout.funcval-order-hardening`** (hardening). The function-value
  field order `{vtable, data}` and the interface-value order `{data, vtable}`
  (the deliberate, verified ABI asymmetry) are encoded as fixed/magic indices
  in codegen + IR (`emit_instr.bn`, `emit_funcvals.bn`, `emit_iface_call.bn`,
  `ir_ops_flow.bn`) rather than as shared named-offset helpers in
  `pkg/binate/types` (unlike `SliceDataOffset`/`MSliceBackingOffset`/
  `ManagedRefcountOffset`, which ARE shared helpers). The VM and codegen agree
  by convention, not a single shared definition — a divergence risk for the
  keystone cross-mode contract. Harden the func/iface field orders into shared
  named-offset constants in `pkg/binate/types`.
- **`type.layout.byte-order`** (open decision). `TargetInfo` (`types.bni:374-378`)
  carries no endianness field, so byte order is target-defined but unconstrained
  by the layout layer (observable via `bit_cast` and the representation builtins).
  Decide whether to pin endianness as implementation-defined and add a
  `TargetInfo` endianness field so layout-dependent constant emission is
  well-defined. (Also noted in `plan-language-spec.md` §21/§9.)

### Type-system issues surfaced while authoring spec Ch.7 (Types) — 2026-06-12
Found writing the docs spec's Types chapter (grounding + adversarial
verification against pkg/binate/types). The spec (`07-types.md`)
documents these as open items.
- **Opaque `make`/`sizeof`/`alignof` not gated**
  (`type.opaque.make-sizeof-gap`, MAJOR doc-vs-impl). The ratified design
  (plan-type-decls.md:42-51, ast.bni:232-233) says `make(Opaque)` /
  `sizeof(Opaque)` / `alignof(Opaque)` must be rejected outside the
  defining package (layout unknown). The checker enforces ONLY field
  access (`check_expr_access.bn:306`); `check_builtin.bn:17-22,144-155`
  accept make/sizeof/alignof on a nil-Underlying named type with no opaque
  gate, so the failure (if any) is a downstream layout/codegen error, not a
  clean diagnostic. Decide: add the opaque gate (per the ratified design),
  or update the design docs.
- **Named func-value LITERAL construction unimplemented** (gap). A func
  *reference* constructs a named `@func` type fine, but a func *literal*
  into a named func-value type is rejected in ALL modes
  (`conformance/regressions/named-func-value-construct-literal` xfailed
  everywhere; checkFuncLit must return the named type when hinted and peel
  TYP_NAMED at isManagedFuncValueLit). Value-rejection and reference
  construction both work.

### Untyped `const` coercion: implementation diverges from a DECIDED note — surfaced authoring spec Ch.6 (2026-06-11)
Needs a decision (MINOR — no miscompile; a type-system permissiveness
question).
- **The note (`claude-notes.md` "Type conversions & literals — DECIDED",
  ~line 444)**: untyped-literal coercion "does NOT extend to named
  constants — only literals." (A deliberate divergence from Go.)
- **The implementation does the opposite.** An untyped `const X = <expr>`
  (no explicit type) carries `TYP_UNTYPED_INT` (with `HasLitVal`) and
  **coerces / narrows at each use, exactly like a literal**:
  `check_const.bn:91-102` (no-`TypeRef` branch defines the name with the
  untyped `valType`), `check_expr.bn:185` (`checkIdent` returns it),
  fit-checked at the use site like a literal. Tests confirm:
  `check_const_test.bn:160-167` (`const A = 1+2` → assignable to `int`),
  `:210-217` (`const A = 200+100` → rejected against `uint8` because 300
  doesn't fit — pure literal-coercion behavior), and
  `check_expr_constfold_test.bn:181-204` whose comment says "the bare
  members stay untyped and **narrow freely at the use site**." Only a
  `const X <type> = …` (explicit type) gets a definite, non-coercing type.
- **Decision**: either (a) enforce the note — give an untyped `const` name
  a definite default type that does not coerce (the Go-divergent design),
  or (b) accept the implemented Go-like behavior and update
  `claude-notes.md:444`. The spec (docs `06-constants.md`,
  `const.untyped.coercion`) currently describes the **implemented**
  behavior and flags this as an open item.

### Lower the file-length `.bni` cap toward 1000/1200 — 🟡 OPEN
- **Residual** of the (now-archived) "Extend hygiene checks to scan `ifaces/`+`impls/`" work. The `.bni` file-length cap is currently 1500/1800 (warn/error); consider lowering toward 1000/1200.
- **Blocker**: `pkg/binate/ir.bni` (~1183 lines) exceeds the proposed lower cap and would need refactoring (split into sub-interfaces) first. A live `TODO` in `scripts/hygiene/file-length.sh` tracks this.
- (Full resolved diagnosis of the ifaces/impls hygiene-scan extension archived in claude-todo-done.md.)

## MAJOR

### MAJOR PROJECT — unify module-level static data into one IR representation (`ir.DataGlobal`) + one per-backend emitter — FILED 2026-06-10 (needs design + planning + phased migration)
- **The smell**: module-level constant data is currently modeled and emitted **per kind**, each with its own IR rep + its own LLVM emitter + its own native emitter: `mod.Strings` (string consts), `mod.Globals` (`var` storage), `mod.Impls` (impl vtables), func-value vtables/handles (derived from `mod.Funcs`), and the package descriptor `_Package` (worst case: LLVM-text-only, no IR rep, no native emitter). That's ~5 kinds × 2 backends ≈ 10 emitters for ONE concept — *a named, module-level constant blob the backend lays into a data section.* The proliferation is what let `_Package` ship with only its LLVM half written (see the native-`_Package` link bug below) — the LLVM-only-divergence bug class is structural to this design.
- **The unification**: one IR concept `ir.DataGlobal { Name; Linkage (private|weak_odr|linkonce_odr|external); Align; Init }` where `Init` is a sequence of terms: `bytes` | `int(width)` | **`symref(symbol, +offset)`** (pointer to another symbol). The `symref` term is the one expressive thing today's `ir.Global.Init` (a single int-only `@Instr`) lacks, and it's what every interesting blob needs. Then ONE `emitDataGlobal` per backend (lay bytes + apply relocations + linkage/align) replaces all the per-kind emitters. Mappings: string → `bytes`; var → `int/zero`; `_Package` → `int(RC),int(0),symref(_pkgname),int(len)` (the static-managed node, no special primitive); impl/func-value vtable → `[symref(dtor),symref(m0),…]`. Both backends walk one path → LLVM-only divergence becomes impossible. Consonant with `ir-backend-guidelines.md` ("string constant collection belongs in a shared layer") — this is the shared *static-data manifest* backends lower.
- **What stays / what resists (design must handle)**: (1) func-value `__shim`s are CODE → stay in `mod.Funcs`; only the symref *table* is data. (2) impl vtables carry **per-arch layout** + `weak_odr`/`linkonce` linkage + alignment — the model must carry linkage/align and backends keep arch layout knowledge. (3) **string interning/dedup** (`FinalizeStrings`) is a real optimization to preserve, not regress to one-global-per-occurrence. (4) `mod.Globals` carries **front-end semantics** (extern vars, qualified-name resolution, `IsExtern` external-decl emission) — the front-end layer maps onto `DataGlobal`, isn't replaced by it.
- **Payoff**: kills the LLVM-only-divergence bug class structurally; ~10 emitters → ~2; new static-data needs get both backends for free. **Cost/risk**: real IR + dual-backend refactor of *currently-working* code; non-trivial regression surface; per-kind quirks above. This is a project, not a bug fix — needs a `plan-*.md` (design the `Init`/relocation model + linkage/align + interning; phased migration).
- **Suggested migration order**: introduce `ir.DataGlobal` + one `emitDataGlobal` per backend → migrate `_Package` onto it FIRST (the proving case; also retires the interim native emitter below) → then impl + func-value vtables → then strings → then globals (front-end-coupled, last). Each step keeps all backends green.
- **Interim DONE**: the short-term native `emitPackageDescriptor` is LANDED (binate `f7d116f3`) — `common.EmitPackageDescriptorData` (shared static-managed-node layout) + a per-arch accessor. Explicitly throwaway: the `_Package` migration step of this project deletes it (and `codegen/emit_pkg_descriptor.bn`) once the descriptor is an `ir.DataGlobal`.
- **Low-priority hardening surfaced by the interim's adversarial review (not reachable today)**: the native interim `SetGlobal`s `_pkg_info` + `_pkgname` as STRONG symbols, vs LLVM's `weak_odr` (`_pkg_info`) / `private` (`_pkgname`). NOT a current bug — in `--backend native` only `main` is native and all deps go via LLVM (disjoint package names), so the same package's strong native `_pkg_info` never lands in two objects; conformance/532 + the native vm/repl/bni unit links are clean. It WOULD bite a future native-library-packaging path (a precompiled native `.o` for a package linked beside a from-source native recompile of it → duplicate strong symbol where `weak_odr` dedupes). Cheap fix when that lands (or sooner): `a.SetWeak` on `_pkg_info` (matches `weak_odr`); `_pkgname` only needs same-object visibility (sole consumer is the same-object `Name.data` fixup) so it can be local/weak. The `ir.DataGlobal` unification should carry a linkage field so this is expressed once. (`_pkg_info` must stay a defined symbol the accessor's cross-section reloc can target — the native Adrp/Lea fixup resolves to it like `emitGlobalAddr` — so not an unnamed local.)

### Add a hygiene check enforcing package-tier dependency rules (`pkg-layout-spec.md`) — bundled tiers must not import non-bundled tiers — FILED 2026-06-10
- **What**: a `scripts/hygiene/` check that statically validates every package's import closure against the tier ordering in `pkg-layout-spec.md` ("Tiers"). A package must not import a *less-bundled* (higher-numbered) tier. Concretely — tier 0/0b/1/1x packages (always- or by-default-bundled: `pkg/builtins/*`, `pkg/std/*`, `pkg/stdx/*`) must NOT import a tier-2/3 package (project-pulled / not bundled: `pkg/binate/*` and any other `pkg/<org>/*`). Also enforce the tier-2 transitive-closure rule (`pkg-layout-spec.md` "Tiers": tier 2's dependency closure must itself be tier 2). Tier is derivable from the import-path prefix (`pkg/builtins/`→0/0b, `pkg/std/`→1, `pkg/stdx/`→1x, `pkg/binate/` & other `pkg/<org>/`→2); `pkg/bootstrap` is a bundled runtime primitive (treat as tier-0-equivalent). EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately imports `pkg/binate/buf`).
- **Why**: a bundled package whose dependency closure escapes the bundled tiers silently breaks the bundle — the dependency's source isn't shipped, so a consumer compiling against the bundle gets `package "<dep>" not found`. NOTHING currently catches this: it only manifests when a consumer compiles the offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene / conformance step does today.
- **Motivating bug (discovery 2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0, always bundled) imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`. The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out (`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with `package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a literal-init allocate+copy). This check would have caught it at the `import` line.
- **Scope note**: adding the check ≠ wiring it into `scripts/hygiene/run.sh` / CI — but a hygiene check belongs in the run.sh master, so do both when implementing. A first audit may surface other pre-existing violations to triage.
- **First manual sweep (Lane C, 2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No non-test bundled package imports outside the bundled set. Two non-obvious cases the eventual check must handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, which is NOT a violation — `pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and resolves under the arm32-baremetal build's own `-I`/`-L`; the check should treat shipped `runtime/<target>/pkg/*` as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in `*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So `lang → pkg/binate/buf` (binate `84818a77`) was the only true tier-0→tier-2 violation; the baseline is otherwise clean.

### `==` / `!=` (and relational) on aggregates: checker now rejects — no more invalid LLVM. DECIDED + LANDED at the checker (binate `60719e01`, coverage `78af9c23`); struct/array impl + generic path remain OPEN
- **What it was**: the comparison type-check rule only checked mutual assignability and returned bool, so `==`/`!=`/`<`/`>`/`<=`/`>=` were accepted on *any* same-typed operands. For aggregates (raw/managed slice, raw/managed func value, interface value, struct, array) codegen then emitted `icmp` on a multi-word value → invalid LLVM (`error: icmp requires integer operands`), hard package compile failure.
- **DECIDED (user, 2026-06-07)** and **LANDED** in `pkg/binate/types` (binate `60719e01`; coverage `78af9c23`):
  - **Equality (`==`/`!=`)**: scalars + pointers compare directly. **Slices, interface values, func values → permanently rejected** with a type-specific diagnostic (consistent with `slice == nil` / `iface == nil` already being disallowed footguns; the sanctioned tests are `len()` / `present()` / identity). **Structs and arrays → "not yet implemented"** (comparable in principle; the fieldwise/elementwise lowering is deferred — arrays in the same bucket as structs, per user). `nil` is judged by the other operand (`ptr == nil` OK; `iface == nil` / `func == nil` rejected).
  - **Relational (`<`/`>`/`<=`/`>=`)**: numeric operands only — ordering is undefined for pointers (claude-notes.md:898) and every aggregate (folds in the same invalid-IR bug for `<` etc.).
  - **Type parameters / Self**: deferred (no error at generic-definition time) in both paths — preserves prior generic behavior; NOT a unilateral generic-semantics change.
  - Validated: 21 targeted checker unit tests; full unit suite (40 pkgs) green; conformance (1094) green; adversarial-reviewed (no real defects introduced).
- **STILL OPEN — do not lose these**:
  1. **Struct/array equality implementation** — currently a clean "not yet implemented" checker error. When implemented: a recursive "comparable iff all fields/elements comparable" check (a struct with a slice/iface/func field → permanent reject; all-comparable struct → fieldwise compare); add a runtime equality conformance cell then.
  2. **Generic path NOT covered** — `==`/relational on a type parameter later INSTANTIATED with an aggregate is not caught: the body is checked once with `T` opaque (deferred), and instantiation does not re-check it (`check_generic.bn`), so it can reach IR-gen → the same invalid-IR class, via generics. PRE-EXISTING (before this change all aggregate `==` was permissive); this change does not worsen it. Needs instantiation-time re-checking OR a `comparable`-style constraint decision. Separate follow-up.
  3. **Sentinel detection (`err == io.EOF`)** — disallowing interface-value `==` means this is NOT the mechanism; needs `identical`/`same` + `errors.Is` (under discussion / see io.EOF TODO). Resolve before the first real `Reader` lands.

### Remove the build.bni-dedup workarounds after a BUILDER bump
- **What**: the build-constraint migration collapsed `pkg/builtins/build` to one
  `#[build(...)]`-gated `ifaces/core/pkg/builtins/build.bni` and re-sourced the
  build config from the active target (binate `5a8714d8` / `b64b21fd` /
  `b0bd1096`).  Because the pinned BUILDER (`bnc-0.0.8`) predates BOTH the
  `ARCH_ARM64 → ARCH_AARCH64` rename AND `#[build]` parsing, three TEMPORARY
  workarounds were needed:
  1. an `ARCH_ARM64` alias (`= ARCH_AARCH64`) in `build.bni`, referenced by
     `buildcfg.HostConfig`, so `cmd/bnc` (which now imports `build`) compiles
     under the bundle's pre-rename `build.bni`;
  2. a throwaway ungated-`build.bni` shim in `scripts/hygiene/lint.sh` (prepended
     to `-I`) so the bundled bnlint — which can't parse `#[build]` and now loads
     `build` transitively via `buildcfg` — typechecks against the shim, not the
     gated file (keeps the fast bundled-bnlint path);
  3. a `[ -d ]`-guarded `ifaces/targets/<key>` lookup in `scripts/binate-paths.sh`
     so a bundle's old per-target `build.bni` (the bundle still ships
     `ifaces/targets/`) is still found when compiling cmd/bnc, while being a
     no-op against the current tree (`build` lives in `ifaces/core`).
- **Removal condition**: bump `BUILDER_VERSION` to a snapshot built AFTER this
  migration (its `build.bni` has `ARCH_AARCH64` and lives in `ifaces/core`, and
  its bnc/bnlint parse `#[build]`).  Then: drop the alias + switch
  `buildcfg.HostConfig` to `ARCH_AARCH64`; remove the lint shim (restore the
  plain bundled-bnlint invocation); drop the guarded `ifaces/targets` lookup +
  `TARGET_DIR` from binate-paths.  Each is comment-flagged in-tree
  (`TEMPORARY`/`Remove once BUILDER`).  Full plan +
  workaround list in
  [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md).
- **Bonus**: the same bump would also let `pkg/bootstrap` be collapsed onto
  `#[build]` (it's in cmd/bnc's BUILDER-compiled tree, currently left
  path-selected — see that plan doc).

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

### `__c_call` should support void returns
- Today `__c_call` "requires a return type" and `checkCCall` rejects
  void ("void and struct returns not yet supported"). So calling a void
  C function (`free`, `exit`) means declaring a dummy scalar return
  (e.g. `int`) and discarding it as a bare statement — see the
  placeholders in `impls/core/libc/pkg/builtins/rt/rt.bn`
  (`__c_call("free", int, ptr)` / `__c_call("exit", int, code)`).
- **Fix**: accept a void return spelling for `__c_call` (and a bare-
  statement form), so void C calls don't carry a misleading return type.
- Surfaced 2026-06-03 by the drop-libc work.

### Inject `pkg/bootstrap` into the VM + convert I/O to `__c_call` — Phase 1 DONE; Phase 2 DEFERRED (BUILDER-runtime coupling)
- **Phase 1 LANDED** on main (`a7fabc7a`, 2026-06-03): bootstrap is now
  native-only in the VM — cmd/bni skips lowering it, the format helpers
  (formatInt/Int64/Uint/Bool/Float, Itoa) are registered as externs in
  both `registerBootstrapExterns` copies, bootstrap's bytecode unit tests
  are xfailed in the 3 `-int` modes, and `extern_register_std_test` guards
  format-helper registration.  `formatFloat` (the first native float
  extern) dispatches via the all-int shim ABI (`7abc3809`).  Verified:
  `287_float_println` green in `-int`; full `builder-comp-int` /
  `-comp-int` / `-int-int` clean but for pre-existing failures.
- **Plan**: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md). The
  rt-drop-libc pattern applied to bootstrap: eliminate the hand-written
  `bn_pkg__bootstrap__*` I/O glue in `binate_runtime.c` by converting it
  to `.bn` + `__c_call`, and make bootstrap native-only in the VM.
- **Phase 2 DEFERRED (2026-06-03), possibly indefinitely**: converting
  the I/O to `.bn` *adds* `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}`
  defs that collide with BUILDER's pinned runtime (gen1 links it,
  `build-compilers.sh:55-62`) → duplicate-symbol link failure building
  gen1. It's a runtime-ABI change, so it can only be done *during a
  BUILDER bump/release* (the new BUILDER's runtime omits the I/O), not in
  the pinned-BUILDER tree. The trivial+moderate `.bn` code was written +
  reviewed (correct modulo the link blocker) and is preserved in
  plan-bootstrap-ccall.md's appendix. `Stat` is a further defer (struct
  stat platform divergence → needs a per-libc-platform impl split). It may
  be better to *eliminate* these bootstrap I/O functions (subsumed by a
  real stdlib `io`) than convert them — so this may never be worth doing.
- **Harder than rt**: `__c_call` is scalar/pointer-only, but bootstrap's
  I/O takes slices + returns managed-slice aggregates → marshalling
  (null-term cstr, data-ptr extraction, aggregate construction). `Args`
  can't be pure `__c_call` (no libc fn returns argv) — a minimal argv
  hook stays in C. Not C-freedom (still links libc syscall wrappers).
- **Needs a BUILDER bump** (the deferral reason above; the original
  "no BUILDER bump" claim was wrong — BUILDER *compiles* `__c_call` fine,
  but its *runtime* still defines the I/O symbols gen1 links). Baremetal
  keeps its semihost impl (per-target, like rt). Filed 2026-06-03.

### Better test-mode/target annotation than `.xfail` (unit + conformance)
- We lean on `.xfail.<mode>` files to mark tests that can't run in a
  given configuration (e.g. `pkg-builtins-rt.xfail.builder-comp-int*`
  because rt is native-only in the VM; the `__c_call` conformance tests
  498/500/527/530 xfailed in every VM-leg mode). But "expected to FAIL"
  is the wrong semantics for "not APPLICABLE here" — these tests are
  *bnc-only* / *vm-only* / *target-specific* by nature, not regressions.
- **Want**: a first-class annotation (in the test source or a manifest)
  declaring a test's applicable modes/targets — `bnc-only`, `vm-only`,
  per-backend, per-target — so the runner *skips* inapplicable configs
  cleanly and reserves `xfail` for genuine known-failures. Would also
  let `__c_call` tests declare "compiled-only" honestly instead of a
  fan of per-mode xfail files.
- Surfaced 2026-06-03 by the drop-libc / native-only-rt work.

### Slim `pkg/bootstrap` and `pkg/libc` by migrating callers OUT
- **What**: rather than converting bootstrap's I/O surface
  in place, migrate callers AWAY from `pkg/bootstrap.X` and
  `pkg/libc.X` toward whatever the long-term replacement is
  (a new I/O package, a slimmer `pkg/std/os`, etc., TBD).
  Goal: shrink the surface of both bootstrap and libc until
  they can either be retired entirely or held as truly minimal
  bootstrap primitives.
- **Approach** (sketch — needs design): identify call sites,
  classify them by what they want (formatted print, file I/O,
  process control, raw libc memops), and route each class to
  the canonical replacement.  bootstrap and libc only get
  what's TRULY platform-essential and inappropriate for any
  higher-level package.
- **Progress**:
  - **libc Memcpy / Memset — DONE 2026-06-02 (binate `87965b70`)**:
    the libc-host rt's MemCopy / MemZero now do pure-Binate byte loops
    (matching the baremetal rt, which already did) and Box copies via
    MemCopy, so both primitives were removed from the whole surface —
    `pkg/libc.bni`, `runtime/libc_stubs.c`, the cmd/bni + vm extern
    registries, and the vestigial baremetal `bn_pkg__libc__*` aliases
    in semihost.s.  No BUILDER bump (gen1 links BUILDER's runtime;
    gen1's outputs emit no `bn_pkg__libc__*` and link checkout's
    runtime).  Verified across compiled / VM / self-hosted / baremetal
    lanes.  Perf footnote: the byte loops are slower than libc
    memcpy/memset at -O0 (no idiom recognition) — accepted for now,
    revisit with a word-at-a-time loop if it shows in profiles.  This
    does NOT touch the C-ABI memcpy/memset LLVM emits for aggregate
    copies (llvm.memcpy intrinsics), which are independent of pkg/libc.
- **Remaining libc surface**: Malloc / Calloc / Free (now the only
  callers; need a real Binate allocator to retire) and Exit (needs a
  process-exit syscall, gated on the C-free syscall story).
  `pkg/bootstrap` — the larger I/O surface — is the next target.
- **`bootstrap.Itoa` — FULLY RETIRED (2026-06-08, `f7966135`).**  Every
  caller migrated, then the function, declaration, tests, baremetal
  duplicate, and VM extern registration all removed.  Now that
  `pkg/std/strconv` has `Itoa(v int)`
  (base 10), `FormatInt(v int64, base)`, and `FormatUint(v uint64, base)`,
  they are the canonical replacement for `bootstrap.Itoa`.  Goal: every
  Tier-1/Tier-2/Tier-3 caller uses strconv instead of bootstrap (a
  sub-step of retiring the bootstrap int-format surface).
  - **The old "BUILDER tree CANNOT import strconv" constraint was wrong /
    is now moot.**  `strconv` (whole package, incl. its `pkg/std/math/big`
    dependency via `ftoa.bn`) is ALREADY in cmd/bnc's BUILDER-compiled
    tree: `pkg/binate/ir/gen_const_fold.bn` and
    `pkg/binate/native/common/common_float.bn` import it, and BUILDER
    compiles them when building gen1.  So BUILDER-surface packages
    (`token`, `native/*`, codegen, ir, …) CAN migrate — verified by
    migrating `token` (gen1 rebuilds clean across builder-comp / -int /
    -comp).  No integer-only strconv subpackage is needed.
  - **`pkg/builtins/lang` (Tier-0 core) — DONE (2026-06-07):** lang can't
    import `strconv` (below Tier 1; layering inversion, and a cycle since
    strconv's closure reaches the builtins), so it got package-internal
    full-width formatters (`formatUint64` / `formatInt64`, mirroring
    `bootstrap.Itoa`'s uint64-magnitude approach incl. the two's-complement
    trick for int64-min).  This also fixed a correctness bug: the impls had
    funnelled through `bootstrap.Itoa(cast(int, x))`, which on 32-bit
    targets TRUNCATED the wide types — `(int64/uint32/uint64).String()`
    were WRONG on ILP32 for values outside int32 range — and mis-signed
    unsigned values ≥ 2^63 on every target.  Each impl now widens
    losslessly (signed → `cast(int64, x)`, unsigned → `cast(uint64, x)`);
    lang keeps `bootstrap` only for `formatFloat`.  Covered by lang_test.bn
    boundary cases (the unsigned ≥ 2^63 ones fail under the old code on a
    64-bit host) and `conformance/653_int_string_width` (width-independent
    output, one .expected for LP64+ILP32; guards the 32-bit truncation
    under the arm32 modes — green on all 64-bit modes locally, arm32 needs
    qemu so it runs in CI).
  - **Conversion discipline for the migration:** route each site by the
    *argument's* type, never by a lossy down-cast — bare `int` →
    `strconv.Itoa`; wider signed → `strconv.FormatInt(cast(int64, x), 10)`;
    unsigned → `strconv.FormatUint(cast(uint64, x), 10)`.
  - **Leave (not formatting calls / separate decisions):** the extern
    registrations that expose `bootstrap.Itoa` to interpreted code
    (`pkg/binate/vm/extern_register_std.bn`, `cmd/bni/externs.bn`) — those
    go when `bootstrap.Itoa` is deleted, not now; the test-runner codegen
    in `cmd/bnc/gen_test_runner.bn` (emits source that calls
    `bootstrap.Itoa`); and `conformance/064_bootstrap_funcs.bn` (tests
    `bootstrap.Itoa` itself).
  - **Progress — all migratable package callers DONE** (2026-06-07; each
    green across builder-comp / -int / -comp, landed on main, one package
    per commit): `token`, `repl`, `native/{x64,aarch64}`, `vm`, `ir`
    (test-only), `lexer` (test-only), `types` (test-only), `lint`
    (test-only), `cmd/bnlint`, `cmd/bni`.  Every arg was a bare `int`, so
    all sites used `strconv.Itoa` directly (no `FormatInt`/`FormatUint`
    needed yet).
  - **Retirement — DONE** (landed in order, each its own commit):
    `gen_test_runner.bn` formats counts via `passed.String()` (`c2aaaabf`,
    relying on [A]); `321` migrated to `total.String()` (`9ba85eec`);
    `conformance/064` retired (`0d7c0501`); the VM extern registration
    dropped from both drivers (`6d2384de`); and finally the definition,
    `.bni` declaration, unit tests, and baremetal duplicate removed
    (`f7966135`).  The bootstrap int-formatting surface used by
    print/println (`formatInt`/`Int64`/`Uint`/`Bool`/`Float`) deliberately
    STAYS — only the standalone allocating `Itoa` is gone.
  - **Done since:** the ad-hoc `intToChars` helpers — the package-scoped
    one in `pkg/binate/ir/gen_func_lit.bn` (3 call sites: `__closure_local_`,
    `__funclit_`, `__mv_local_`) and a duplicate in
    `pkg/binate/vm/func_index_test.bn` — now use `strconv.Itoa` and are
    deleted (2026-06-07).
- **[A] Primitive `.String()` without importing `pkg/builtins/lang` —
  DONE across all execution modes (compiled `37b2ffcc`, VM `487c2d08`).**
  `myInt.String()` resolves AND links/executes with no import in both the
  compiled backends and the bytecode VM; naming the `lang.Stringer`
  interface *type* still requires the import (gated by the type checker).
  Mechanism (reverses the "No auto-import" decision in
  `plan-primitives-impl-interfaces.md`, for methods only): `ensureLangLoaded`
  force-loads lang so its carve-out impls attach `String()`/`Compare()` to
  the global primitive singletons (resolution); `appendLangImport` (a clone
  of `appendBootstrapImport`, added at every `RegisterImports` site with the
  same self-import guard, in BOTH `cmd/bnc/compile_imports.bn` and
  `cmd/bni/irgen.bn`) registers lang's signatures so the cross-package call
  resolves/links.  DCE/baremetal worry is moot (unused impls stripped by
  `--gc-sections`/`-dead_strip`).  Full conformance green in both
  builder-comp (1085) and builder-comp-int (1072).  Covered by conformance
  `654`–`656` (per-type positives) + `658` (negative).
  - **Remaining follow-up — the repl.** The repl has its own import setup
    (`pkg/binate/repl/{ir_imports,session,util}.bn`) not covered by the
    `cmd/bni` change; add `ensureLangLoaded` + `appendLangImport` there so
    `.String()` works at the repl too.  Small, same pattern.
- **[B] Test runners can depend on the stdlib — DONE (2026-06-08,
  `36e979df`).**  The `cmd/bnc --test` runner (`gen_test_runner.bn`,
  compiled by `test.bn`) is parsed *after* typecheck, so a stdlib package
  it imports that no test package pulls in was never loaded → not compiled
  → wouldn't link.  Fix: `genTestRunner` declares its stdlib deps in
  `testRunnerStdlibImports()`, and `test.bn` force-loads that list before
  typecheck (the compile loop already builds every loaded package, so they
  then link).  Adding the future `pkg/std/os` (for `Args`/`Open` when
  bootstrap I/O migrates) is a one-line addition to that list plus its use
  in the runner.  Exercised end-to-end now by a placeholder: the runner
  imports `pkg/std/errors` and makes one harmless `errors.New` call
  (TODO-marked for removal once a real dep lands) — proven by
  `pkg/binate/buf` (closure `{buf, testing}` excludes errors) whose test
  binary links the errors-importing runner only via the force-load.  The
  whole unit-test suite now exercises [B].  (The VM `-int` path is
  unaffected — `cmd/bni` executes tests directly, no generated runner; a
  future VM stdlib dep would be force-loaded there the same way as
  bootstrap/lang.)  Distinct from [A], which force-loaded lang to make
  `bootstrap.Itoa` removable.
- **Why migrate OUT rather than convert in place (do NOT re-attempt the
  in-place shape)**: in-place renames of packages whose surface is
  declared-only and resolved by C symbols (`pkg/libc`, and the I/O side
  of `pkg/bootstrap`) hit a wall that pure-Binate-package renames
  (pkg/rt → pkg/builtins/rt) do not.  The wall: at Stage 1, gen1 is
  linked against BUILDER's bundled `libc_stubs.c` (auto-found next to
  `--runtime`), which only defines symbols under the OLD mangled name
  (e.g. `bn_pkg__libc__Memset`).  Checkout source — now compiling under
  the NEW package name — emits calls to `bn_pkg__builtins__libc__Memset`,
  which is UNRESOLVED at Stage 1's link.  Pure-Binate packages don't hit
  this because the bnc-compiled package provides the NEW-name symbols as
  definitions in its own `.o`; declare-only-via-C packages have no such
  Binate-side definition.  Compat aliases in checkout's `libc_stubs.c`
  don't help — BUILDER's runtime is what Stage 1 links against, not
  checkout's.  Resolving would require either (a) pointing Stage 1's
  `--runtime` at checkout's (build-script surgery), (b) a supplemental
  compat .o via `--link-after-objs` (build-script surgery + new
  artifact), or (c) two release cycles with a transitional bridge —
  none worth the bootstrap migration's payoff.  Migrating callers OUT
  side-steps the whole tangle.
- **Status**: in progress.

### Package descriptors (Phase B) — `_Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `_Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `_Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `_Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `_Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg._Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg._Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg._Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `_Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg._Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt._Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `_Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `_Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `_Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Static-managed sentinel — deferred follow-ups (optimizations, not correctness) — 🟢 LOW
Follow-ups split out of the (now-done) static-managed sentinel landing:
- **String-literal null-backing unification**: can the string-literal
  `backing_refptr = null` immortality trick (`emit.bn`) be unified under the
  negative-refcount sentinel? Representation can plausibly unify; the nil-check
  itself can't be dropped (it guards genuinely-nil `@` values). Repr cleanup.
- **ClosureRec-as-sentinel**: the VM's shared per-callee non-capturing-`@func`
  `ClosureRec` (`vm_exec_funcref.bn`) is a static, never-freed managed object.
  The premature-free CRITICAL was already fixed symmetrically (conformance 528);
  making the shared `ClosureRec` an immortal sentinel would remove per-instance
  refcount churn on a shared singleton. Optimization, not a correctness gap.

### Purely-value const extension (future language direction) — DESIGN, not started
Future direction split out of the (now-resolved) non-int-const mis-emit bug:
allow `const` of certain non-scalar but purely-value types (no storage, no
managed fields). Currently `const` is scalar-only (non-scalar → `errNonScalarConst`,
"use `var readonly`"); no `isPurelyValueType` predicate exists yet. A genuine
language extension, not a bug fix.

### Raw-slice escape: decide whether a BROADER best-effort escape lint is wanted — 🟡 NEEDS DECISION
The original framing ("demote the raw-slice escape TYPE ERROR to a linter rule")
is obsolete: there is NO type-check rejection for raw-slice escape (the checker
never rejected it), and a `raw-slice-return` LINT rule already exists (`lint.bn`,
landed `10d19369`) — but it only covers the `@[]T → *[]T` "drops the managed
wrapper" return case. **Open decision (user):** is a broader best-effort escape
lint wanted (return / store-to-outliving-field / assign-to-global of a raw slice
borrowing a local), or is the current narrow rule + "raw is an opt-in escape
hatch" sufficient (close this out)?

### IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
- **Symptom**: under `builder-comp_arm32_linux` unit tests, `pkg/ir`
  and everything downstream of it (`pkg/native{,/amd64,/arm64,/common}`,
  `pkg/codegen`, `pkg/vm`, `cmd/{bnc,bni,bnas}`) fail to compile for
  arm32 with int-width type errors.  `pkg/ir` is the cascade root.
- **Discovery**: triaging the 14 arm32_linux unit-test failures after
  type-check errors gained source locations (binate `c011827`,
  conformance/494).  With locations on, `pkg/ir`'s only *source* error
  is `gen_util_literals.bn:234` (`intFitsInType` compares against
  `4294967295` > INT32_MAX), and tracing the value upstream shows the
  whole literal path is `int`.
- **Root cause**: the IR stores program integer constants in
  `Instr.IntVal`, typed `int` (`pkg/ir.bni:356`) — host-width.  The
  feeding path (`exprIntLitValue`, `bignumToInt`, `parseIntLit`,
  `EmitConstInt`) is all `int` too.  On a 64-bit host this happens to
  work (it's really storing a 64-bit *bit pattern* — a `uint64`-max
  literal lands as the int64 pattern `-1` and codegen emits it fine).
  On a 32-bit host `int` is 32 bits, so the path neither compiles nor
  can represent a `uint32`/`int64` constant.  Symbol/codegen output
  must not depend on host int width.
- **Severity**: major.  Loud (compile failure) on 32-bit, not a silent
  64-bit-host miscompile — but it blocks the C-free / 32-bit-hosted
  self-hosting goal.  `int64` vs `uint64` for the field is immaterial
  (it's a stored bit pattern reinterpreted by the constant's type);
  `int64` is the minimal-churn choice since the existing range-check /
  negation code is written in signed terms whose bounds fit `int64`.

- **Layer 1 — IR + codegen + native (DONE)**: made the program
  -constant path host-independent.  Landed: binate `879ba38`
  (asm 64-bit immediates: x64 Imm→int64 + Imm64, finished aarch64
  Imm consumers in pkg/asm/parse), `035022c` (IR int64 contract),
  `294b5f0` (wide-constant tests), `075e1f5` (made the int-width
  -assuming bootstrap/vm tests 32-bit compatible).
  - `Instr.IntVal` `int` → `int64`.
  - `exprIntLitValue` / `bignumToInt` return `int64`; `intFitsInType`
    takes `int64`.  (`parseIntLit` stayed host-`int` — a
    non-type-checked fallback; the real path takes the bignum branch.)
  - `EmitConstInt(int)` kept (widens internally) + new
    `EmitConstInt64(int64)` for the literal path.
  - `buf.WriteInt64` added; codegen's OP_CONST_INT emit uses it.
  - `pkg/native/{amd64,arm64}` `emitConstInt64` → `int64`; arm64
    extracts MOVZ/MOVK chunks via int64 shifts.  Fixed a latent bug:
    arm64 `emitConstFloat` did `cast(int, bits)` on a 64-bit IEEE
    pattern (dropped the high word on a 32-bit host) → `cast(int64,…)`.
  - VM boundary: `lower_instr.bn` `bc.Imm = cast(int, instr.IntVal)`
    — lossless on a 64-bit host; the truncation-on-32-bit is what
    Layer 2 addresses.
  - **Result**: all 14 packages in the arm32_linux unit-test set
    compile for arm32 (verified locally; runtime validated by the
    `builder-comp_arm32_linux` CI job).

- **Layer 2 — VM machine word (INT64 PATH DONE)**: `pkg/vm` uses host
  `int` as its universal machine word — registers, immediates,
  pointer arithmetic (`bit_cast(int, frameBase) + instr.Imm`),
  offsets.  So a 32-bit-hosted VM is a 32-bit machine and can't carry
  64-bit immediates.  Open design question (raised by user): can the
  VM keep host-sized words for most values and use 64-bit only when
  necessary?
  - On a 32-bit host the VM interprets 32-bit-*target* bytecode, where
    pointers / `int` / sizes / offsets are all 32-bit by definition —
    so host-word is already correct for the vast majority of values.
    The 64-bit cases are exactly the explicitly-64-bit ones: `int64` /
    `uint64` values and large literals.
  - Two implementations of "64-bit only when necessary":
    (a) uniform 64-bit value slots + width-aware ops — simplest and
    correct; on a 32-bit host it costs 64-bit slot storage and 64-bit
    arithmetic only where the op is 64-bit (the compiler already
    supports `int64` on 32-bit; bytecode is largely typed already).
    (b) host-word slots + 64-bit via register pairs / a parallel wide
    slot, switched by typed opcodes — saves the 32-bit storage but
    complicates the register model and bytecode (must track which
    slots are wide).
  - Recommendation: do (a) first (correctness, minimal model change);
    treat (b)'s host-word-mostly layout as a later 32-bit perf
    refinement, not a correctness prerequisite.
  - **Investigation findings (2026-05-26)**: the change is larger and
    more entangled than the (a)/(b) framing implies — `int` is a
    *single conflated word* across three distinct roles, so it can't
    be swapped to int64 blindly:
    1. **Register slots.** `regs *int`, accessed `regs[i]`.  But
       `pushFrame` already budgets `f.NumRegs * 8` bytes/reg
       (`vm.bn:181`) — 8-byte slots.  On a 64-bit host int==8 so it's
       consistent; **on a 32-bit host this is a latent stride bug**
       (8-byte budget, 4-byte `*int` access → registers alias).  So
       `regs *int → *int64` actually *fixes* this and matches the
       existing layout.
    2. **Host pointers.** Registers also hold host addresses via
       `bit_cast(int, vm.Stack)` / `bit_cast(*uint8, regs[i])`.  With
       int64 regs on a 32-bit host these become a width mismatch
       (host ptr 32-bit, reg 64-bit) — `bit_cast` is illegal
       (size differs); they need explicit widen-on-store /
       truncate-on-read helpers (`ptrToReg` / `regToPtr`).
    3. **Target-memory-structure access.** `bit_cast(*int, hdrPtr)`
       reads managed-slice/refcount headers as `*int`.  These are
       target-word-sized fields; tying their stride to the register
       word is wrong if the two ever differ.  Needs separating
       "VM register word" from "target word".
  - Surface: ~106 `bit_cast(int,…)/(*uint8,…)/(*int,…)` sites across
    vm_exec*.bn + vm.bn, plus `BCInstr.Imm int→int64`, register
    arithmetic, and the memory ops.  This is a multi-step refactor;
    settle the register-word-vs-target-word model before editing.
  - **What landed (int64 path)** — model:
    register == host word; 64-bit values use register pairs; pair ops
    only engage when `REG_SLOT < 8` (no-op on a 64-bit host).
    Pointer-vs-target-word ambiguity stays narrow because `bit_cast`
    sites are at register-vs-pointer boundary — register word stays
    host `int`, so the ~106 `bit_cast` sites are untouched.
    - Step 1 (binate `f7cae70`): `REG_SLOT = sizeof(int)`; register
      area / frame header sized by it.
    - Step 2a (`ca7def6`, `394a16a`, `ca41a75`): `buildSlotMap` /
      `regWidths` / `remapRegisters` — id→slot mapping with the
      audited `BC_RETURN.Dst` exception.
    - Step 3 (`fd3ca06`, `f764a66`, `be877fd`, `60657fd`, `947205f`,
      `ebaa077`): full `BC_*64` handler set — `LOAD_IMM64`, `MOV64`,
      arith / bitwise / shifts / signed+unsigned compares / unary
      (NEG, BITNOT) / casts (WIDEN_S, WIDEN_U, NARROW, MOV64-bitcast)
      / pair memory `LOAD64_PAIR` / `STORE64_PAIR`.  Pure compute
      factored into evalArith64 / evalCmp64 / evalShift64 /
      evalUnary64 / widen64* — host-tested across the tricky cases.
    - Step 4 (`925e9bc`, `949ea29`, `ebaa077`): lowering emits the
      `BC_*64` ops host-word-aware — `OP_CONST_INT`, all binary
      arith / cmp / shift, load/store, casts, NEG/BITNOT.
    - Step 2b (`24a5d67` RETURN64, `7353523` direct CALL,
      `2eaa8f9` indirect/func-value/iface call ABI,
      `11da9d7` multi-return pair-aware): int64 return + call ABI
      complete.  `NumParamSlots` + slot-count `Imm` semantics.
    - Step 6 (`1fd3b9f`): conformance/499 int64 arithmetic E2E.
  - **Float64-on-32-bit (DONE)**: mirrors the int64 pair pattern.
    - `ba1a798`: route the existing `BC_FNEG` / `BC_F*` /
      `BC_SITOF` / `BC_FTOSI` / `BC_F64_TO_F32` / `BC_F32_TO_F64` /
      `OP_CONST_FLOAT` `bit_cast(int, float64)` hops through
      int64 — compile-clean on a 32-bit host without yet changing
      lowering semantics.
    - `3126655`: `BC_F*64` opcode decls (`BC_FNEG64`,
      `BC_FADD64..BC_FDIV64`, `BC_FEQ64..BC_FGE64`) + pure
      `evalFloatArith64` / `evalFloatCmp64` / `evalFloatNeg64`
      helpers in `vm_exec64.bn` + host-testable unit tests for
      each helper.
    - `ae08c1ed`: `execOp64` dispatch glue — joins source pair(s),
      bit_casts through `int64` to `float64` for the compute,
      bit_casts back, splits to dst pair (or single-slot bool for
      compares).  Direct `execOp64(&stackArr[0], instr)` tests
      cover all three shapes (binary arith, unary FNEG, compare-
      writes-single-slot).
    - `00b10e38`: lowering — `lowerBinOp` / `lowerCmpOp` add an
      `isFloatPair` branch alongside the existing `isIntPair`;
      `OP_NEG` dispatches `BC_FNEG64`; `OP_CONST_FLOAT` emits
      `BC_LOAD_IMM64` with `splitInt64` halves when
      `is64BitScalar(instr.Typ) && REG_SLOT < 8`.
    - `769d2e54`: gate test for OP_CONST_FLOAT — confirms 64-bit
      host falls back to `BC_LOAD_IMM` (no spurious pair branch).
  - **REMAINING GAP — int64 side of int↔float CONVERSION casts is NOT
    pair-aware (latent; surfaced 2026-06-12 by the int↔float32 VM-fix
    review).** The "DONE" above covers float *arith/compare* pairs and
    the *float* side of conversions; it does NOT cover an int64/uint64
    operand of a `cast` to/from a float:
    - int→float SOURCE side (`BC_SITOF`/`BC_UITOF`/`BC_SITOF32`/
      `BC_UITOF32`): the handlers read the int source as a single slot
      (`regs[instr.Src1]`) and `lowerCast`'s int→float arm has no
      `is64BitScalar(srcTyp) && REG_SLOT < 8` check, so `cast(float*,
      <int64>)` on a 32-bit host drops the source's high half. (These
      handlers ARE dest-pair-aware for the float64 result — the
      asymmetry is source-only.)
    - float→int DEST side (`BC_FTOSI`/`BC_FTOUI`/`BC_F32TOSI`/
      `BC_F32TOUI`): the handlers write a single dest slot via
      `cast(int, f)` (host int) and `lowerCast`'s float→int arm has no
      `is64BitScalar(dstTyp)` check, so `cast(<int64/uint64>, <float>)`
      on a 32-bit host leaves the dest's high slot stale (and truncates
      through a 32-bit host int). (These handlers ARE source-pair-aware
      for a float64 source — the asymmetry is dest-only.)
    Latent, not a live miscompile: no conformance mode runs the bytecode
    VM on a 32-bit host (the `-int` legs run `bni` natively on the
    64-bit build host; arm32 modes are comp/native, not VM), and the
    arm32 `pkg/vm` unit tests don't exercise int64↔float conversion
    casts. NOT introduced by the int↔float32 fixes (`289420b6`/
    `3fd7e712`) — the new float32 ops faithfully mirror the existing
    single-slot float64 ones. Fix (to land before/with any arm32
    VM-host enablement): add `is64BitScalar` gates in both conversion
    arms of `lowerCast` and pair-aware source/dest handling
    (`joinInt64`/`splitInt64`) in the eight handlers, plus direct
    `execNumericCast` unit tests in `vm_exec64_test.bn` driving a
    pair-wide int64 source and dest.
  - **End-to-end arm32 coverage status (2026-05-28)**:
    - `pkg/vm` source compiles cleanly on arm32 (since `ba1a798`).
    - Conformance `builder-comp_arm32_linux`: green.
    - **pkg/vm unit tests on `builder-comp_arm32_linux`: green**
      (was 16 failures pre-session → 9 → 1 → 0).  The bytecode-VM
      BC_*64 / BC_F*64 dispatch and slot allocation are now fully
      end-to-end-validated on a real 32-bit target — including
      the `TestRepro_StructWithManagedSliceFieldAppend` managed-
      memory path, which surfaced the hardcoded-LP64 managed-
      allocation-header offset that `81d31b7c`'s MANAGED_HDR
      const fixed.
    - The cascade-revealed packages — pkg/{types, codegen,
      native/{common,aarch64,x64}} — are also green on arm32 now
      after the LP64-baked-test cleanup (`11ff9864`, `2d13838d`).
    - Remaining arm32_linux failures (5) are all the int64-min-
      boundary cluster in pkg/{bootstrap,buf,ir} — see the
      "arm32 unit-test cleanup" entry for the bucket.  Unrelated
      to this work.

### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Use interfaces more (opportunistic)
- **Constraint**: now bounded by `BUILDER_VERSION`-pinned bnc
  rather than the historical bootstrap subset — cmd/bnc no longer
  has to be bootstrap-runnable now that boot mode is gone (binate
  `c1be3cc`, 2026-05-21).  bnc-0.0.1 (the current BUILDER) supports
  interfaces, so anything in cmd/bnc's dep tree is fair game too.
  Generics are NOT in bnc-0.0.1, but interfaces are.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
  pkg/ast's tagged-union nodes (DECL_*, EXPR_*, STMT_*, TEXPR_*
  Kind enums + switch-on-Kind in pkg/{parser,types,ir,codegen,
  loader}) is the biggest single target but also the longest
  refactor — touches every layer.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).
- **Recon finding (2026-05-26)**: there is NO clean *small*
  retrofit target.  The candidates above split into two
  unappealing buckets: (a) enum→value lookups (reloc maps,
  opName, the emitInstr op dispatch) where `switch` is genuinely
  the right tool and an interface would mean manufacturing one
  empty marker type per enum value — pure ceremony; and (b)
  monolithic tagged unions (`ast.Stmt`/`Decl`, `ir.Instr`) where
  a real interface means splitting a struct that touches every
  layer.  So "use interfaces more" here is a deliberate design
  choice, not opportunistic cleanup.
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`).  The genuinely-valuable use
  found: `cmd/bnc/compile.bn`'s `Backend` interface
  (`compileModule`) with `llvmBackend` / `nativeBackend` impls,
  dispatched via `compileModuleVia`.  This collapsed the
  duplicated driver flow — `compileMainNative` is gone, `main()`
  picks the backend and the LLVM/native paths are unified.
  pkg/native also got an internal arch `Backend`
  (arm64/amd64).  These are the first non-synthetic interface
  users beyond pkg/std's `Stringer`.  NOTE: interface values
  must be constructed from locals, not package globals — `&global`
  iface construction was a codegen bug (now fixed, see
  conformance/495).

### Use `@[]@[]char{...}` composite literals (opportunistic)
- **Constraint**: previously forbidden because bootstrap didn't
  support managed-slice-of-managed-slice composite literals; now
  unlocked everywhere (bnc-0.0.1 supports them).  Mirrors the
  unconstraint situation for `cmd/bnlint`'s tests, which already
  use this shape.
- **Pattern to replace**: a known-fixed-length run of
  `args = appendCharSlice(args, "foo"); args = appendCharSlice(args, "bar"); ...`
  → `var args @[]@[]char = @[]@[]char{"foo", "bar", ...}`.  Same
  shape for `appendRawCharSlice` (since string literals are
  already `*[]const char`).  When the run mixes constants with
  computed values, leave it alone — the literal form only helps
  for known-static sets.
- **Candidates**: argv construction in build scripts (e.g.
  `cmd/bnc/{main,test,compile}.bn` clang-args setup), test
  scaffolding (anywhere a test builds a known `@[]@[]char`
  fixture), and short fixed sets of import paths.
- **Why bother**: cuts line count, removes a runtime O(n²)
  rebuild pattern (each `appendCharSlice` allocates a new
  slice + copies), and matches the language's expressive
  default instead of the bootstrap workaround.

### Use function values to collapse explicit dispatch shims (opportunistic)
- **Constraint**: function values are unlocked now that
  cmd/bnc is no longer bootstrap-bound; bnc-0.0.1 has the
  function-value machinery (see plan-function-values-phase-3
  in `claude-todo-done.md`).
- **Pattern to look for**: places where we route through a
  `kind` int + a per-kind dispatch table, when the data flow
  would be clearer as "the caller hands us the function it
  wants invoked".  Candidates need a closer look before they're
  fully scoped — function-value adoption isn't always a win
  (each call adds an indirect-call overhead), so this is
  selectively-opportunistic, not blanket.
- **How to land**: TBD; needs concrete site survey.

### Expand `pkg/slices` beyond `Append` — opportunistic
- `pkg/slices.Append[T]` is the only generic helper today.  Natural
  additions when call sites demand them (don't add speculatively):
  - `Concat[T](a, b) @[]T` — for the managed-slice + managed-slice
    shape.  `bootstrap.Concat` covers the char-slice case but is
    raw-slice-typed.
  - `Filter[T, P]` / `Map[T, U]` — block on closures or func-value
    params; only worth it once those constraints land properly.
  - `RemoveLast[T](s) @[]T` — `popLoading`-style pattern (rebuild
    minus last occurrence) repeats per element type.
  - Don't pre-add a kitchen-sink set — let the first 2-3 call
    sites pull each helper in.
- **Survey 2026-05-28** of the BUILDER-compilable tree: none of the
  above clears the "2-3+ same-shape sites" bar at the moment.
  Concrete numbers found:
    * `Concat[T]` over two managed slices: 0 sites; the only
      `Concat` callers all funnel through char-specialised
      `bootstrap.Concat`.
    * `Contains[T]`: 4 candidate sites (`containsTypePtr` /
      `containsName` / `containsPkgName` / `containsStr`) but each
      uses a different equality (Identical / charEq / streq), so
      collapsing them needs func-value comparators or method-based
      equality — gap.
    * `Reverse[T]`: 1 site (loader `popLoading`).
    * `RemoveLast` / `RemoveByValue[T]`: 1 site (also loader
      `popLoading`, but it's "rebuild minus *streq match*", which
      is `RemoveWhere` shape — not a pure index/value remove).
    * `Copy[T]` one-liner: 2 sites; most slice-copies in the tree
      are inlined in larger functions.
  So no new helper to add right now without going speculative.
- **The real next pkg/slices step** the survey surfaced: 168
  `slices.Append[T]` calls live inside `for` loops, i.e. O(n²)
  builds.  Folding those into a growable container with amortised
  O(1) append (a `Vector[T]` / `Builder[T]` shape with capacity
  tracking) is a substantive design, not a quick add — file it for
  later when the surface is being intentionally pulled into a
  proper stdlib effort.

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.
- **Landed (2026-05-25/26)**: the big per-op dispatchers are
  converted — `pkg/vm/vm_exec_pure.bn` + `vm_exec_helpers.bn`
  (binate `b4456ab`, `e4e7d29`), `pkg/codegen/emit_instr.bn`
  (`2d6d0f7`), `pkg/native/arm64/arm64_dispatch.bn` (`3756acc`).
  Where a chain mixes equality cases with op-RANGE checks
  (emit_instr's OP_ADD..OP_SHR / OP_EQ..OP_GE; arm64_dispatch's
  emitCompare/emitBinop/emitUnop delegates), the range arms stay
  as guards alongside the switch.  `ir_ops.bn`'s opName was
  already a switch — nothing to do there.  This work flushed out
  a CRITICAL case-scope miscompile (managed local in a `case`
  body), since fixed (`4306197`) — see the FIXED entry above.
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### pkg/asm/aarch64 slow under `builder-comp-int-int` (perf)
- **Symptom**: under `builder-comp-int-int`, the
  `pkg/asm/aarch64` test package alone is slow enough to time
  out its CI shard at the 30-min cap. Other packages in the
  same mode finish comfortably.
- **Mitigation in tree**: skipped via the whole-package skip
  mechanism `scripts/unittest/pkg-binate-asm-aarch64.skip-pkg.builder-comp-int-int`
  (2026-06-10 — migrated from the old `.xfail`; slowness is a skip,
  not an expected failure). Coverage is preserved by `builder-comp`,
  `builder-comp-int`, `builder-comp-comp*` and the native_aa64 / arm32
  modes — this is purely a double-interp pacing issue. See the
  "int-int slow-package skips" entry below.
- **Hypothesis**: same shape as the codegen `TestEmitDebug*`
  entry above — many small CharBuf / refcount / bounds-check
  operations per emitted instruction, each paying 2× bytecode-
  dispatch overhead under VM-on-VM. The aarch64 assembler is
  string-heavy (encoding tables, mnemonic dispatch). Hasn't
  been profiled.
- **Next step**: profile one `pkg/asm/aarch64` test under
  `builder-comp-int-int` to confirm the hypothesis and identify
  the actual hot path before guessing at fixes. See the codegen
  entry above for the lesson on guessing-without-profiling.
- **Not blocking anything**; mitigation in tree.

### int-int slow-package skips — re-add after optimizing (or decide double-VM coverage isn't worth it) — FILED 2026-06-10
- **Context**: `builder-comp-int-int` (double-VM, VM-interpreting-VM) was "globally broken — every cell SIGSEGV'd" until `c997cf2e` (2026-06-09) made cells actually run. Now-healthy, the lane runs ~120+ min of work and was timing out its CI shards. Bumping unit sharding 4→8 (binate `e40fe3a0`) helped the light half but **4 of 8 shards still timed out at the 30-min cap, each completing ≤1 package** — i.e. a handful of packages each take **>~24 min (or hang) under double-VM**, which sharding can't fix (a single package can't be split across shards).
- **New mechanism (not xfail)**: added a whole-package skip — `scripts/unittest/<pkg-key>.skip-pkg.<mode>` (run.sh). Distinct from `.xfail` (asserts the package FAILS; XPASS-errors if it ever passes) and from `.skip` (drops individual tests but still runs the package). `.skip-pkg` omits the whole package from a mode because it's too slow there; it is NOT a failure (the tests pass — they're just not run in this lane). Counted as `pkg-skipped` in the summary.
- **Skipped under `builder-comp-int-int`**: round 1 (2026-06-10) — `pkg/binate/codegen` (its `TestEmitDebug` per-test `.skip` was insufficient), `pkg/binate/ir`, `pkg/binate/types`, `pkg/std/math/big`, `pkg/binate/asm/aarch64` (migrated from `.xfail`); these took 6 of 8 shards green. Round 2 (2026-06-10) — added `pkg/binate/vm` itself (CI showed it was the last timed-out shard's >24-min offender). The set was found empirically (heuristic + iterating on which shard still timed out), since the timed-out shards never log the offender's time.
- **Re-add work (the "separately" part)**: for each skipped package, either (a) profile + optimize its double-VM runtime so it fits a shard, or (b) make the explicit call that the double-VM lane adds no coverage over single-VM (`-int`) for that package (strong for the compiler-side ones — codegen/ir/types/asm test the COMPILER; `-int` already runs their tests through the VM; double-VM is the same logic + an extra dispatch layer). `pkg/binate/vm` is the one whose lost double-VM coverage is most arguable — its logic is still covered by `builder-comp-int` / `-comp-int` (single VM), and the lane's unique value is exercised by every OTHER package; re-adding it likely wants per-test `.skip` of its slowest tests rather than the whole package. When re-adding `codegen`, its `TestEmitDebug` per-test `.skip` still applies.
- **Separately unmasked**: `pkg/std/os` (landed `3ca36c82`) fails `vm/lower: unhandled IR opcode c_call` on ALL three VM-leg unit modes — libc-backed (native-only), same category as the `rt`/`bootstrap` xfails. NOT a slow-skip case (it genuinely FAILS in the VM), so it's `.xfail`'d (not `.skip-pkg`'d) for `builder-comp-int` / `-comp-int` / `-int-int`, matching that convention. My skips merely unmasked it (the shard used to time out before reaching it); it was already reding `builder-comp-int` independently.
- **Not a release blocker** (int-int non-blocking per `release-process.md`; was red at `bnc-0.0.7` too). Tracked here so the skips don't become permanent silent coverage loss.
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate modes, not this. NOTE: `native_x64` was NOT "WIP" — it was broken by an ELF PC32 reloc bug, fixed 2026-06-14 `dd74c91e`; see the top-of-file native_x64 entry.)

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### Readonly method receivers — deferred (gated on methods/interfaces)
- A method's receiver kind (`*readonly T` / `@readonly T`, plus value
  receivers — which are always readonly) determines which pointer kinds
  satisfy an `impl` and bounds what the method may mutate.  See
  `claude-notes.md` (value receivers always readonly; readonly-restricted
  dispatch expressed at the impl level; `*readonly T` receiver smoothing
  auto-takes `&t` at the call site).
- This was "Stage 3" of the old `const` type modifier.  The rest of that
  work landed and the type-level modifier is now spelled `readonly`
  (`plan-const-readonly.md`, COMPLETE 2026-06-03 — `const` split into
  compile-time `const` / `var` storage / `readonly T` modifier; that
  plan's three listed deferrals — readonly-slice slicing, `.bni`
  extern-var, `&pkg.Const` — are all since resolved).
- Deferred, not abandoned — depends on the methods/interfaces feature.
  Fold into that project's tracking when it firms up.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package-name/path conventions item below.
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. Decisions are interleaved with the name/path
  conventions item below — sketch and conventions probably ratify
  together.

### Package name/path conventions — decide and possibly reorganize
- Current `pkg/` layout mixes toolchain internals (`pkg/parser`,
  `pkg/types`, `pkg/codegen`, …) with runtime (`pkg/rt`), bootstrap
  support (`pkg/bootstrap`), libc bridges (`pkg/libc`), and small
  utilities (`pkg/buf`, `pkg/mangle`, …). Future stdlib packages would
  pile in alongside them with no organizing principle.
- Questions to answer:
  - Should toolchain internals live under a distinct prefix
    (`compiler/parser`, `compiler/types`, …) so that "what's stdlib"
    vs. "what's compiler implementation" is visible at the import
    path? Same question for runtime / bootstrap support.
  - What does a Binate package path *look* like? Is `pkg/` a real
    prefix or just a directory convention? Are external (third-party)
    packages spelled differently?
  - How do package paths interact with the package manager's naming
    scheme (URL? registry name? short alias)?
  - Mangling: short package names (`mangle.PkgShortNameFromModule`)
    currently derive from the path's last segment. If conventions
    change, mangled symbol names change, which affects ABI. Plan a
    migration story.
  - Are there packages that should move? `pkg/bootstrap` is arguably a
    stdlib piece; `pkg/rt` is closer to runtime-internal; toolchain
    internals could become `compiler/...`. Each move is a real refactor.
- Heavily entangled with the package-manager sketch — they should
  probably ratify together, since the manager design depends on what
  paths look like.
- Output: a plan / decision doc in `explorations/`. Reorganization is
  a follow-up project.

### Tier + dependency-direction hygiene checks (enforce `pkg-layout-spec.md`)
- **What**: a hygiene check (new script under `scripts/hygiene/`, alongside
  `conformance-imports.sh`) that enforces the tier dependency-direction rule
  from [`pkg-layout-spec.md`](pkg-layout-spec.md): a package may import only
  packages at its own tier or **lower**; importing a strictly-higher tier is
  a violation.  Tiers, low→high: 0 / 0b (`pkg/builtins/*`) < 1 (`pkg/std/*`)
  < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3
  (app-specific).  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal;
  `pkg/binate/parser` importing `pkg/std/os` is fine.  (This is the runtime
  enforcement of the spec's "Transitive constraint" + tier table.)
- **Special case — `pkg/std` → `pkg/stdx`**: tier 1 (`std`) may depend on
  tier 1x (`stdx`) **internally** (in `.bn` impl files) but **not externally**
  (in `.bni` interface files).  A `.bni` importing `stdx` would leak a
  no-inter-version-compat (1x) type into `std`'s strict-compat (tier 1)
  surface.  So the check must scan `.bni` imports separately from `.bn`
  imports: the std→stdx edge is allowed only from `.bn`.  (Generalize if
  other interface-vs-impl tier asymmetries surface.)
- **How**: derive each package's tier from its path — the realized layout
  makes tier path-derivable (`ifaces/core` + `impls/core/*` → tier 0/0b;
  `ifaces/stdlib/pkg/std` → tier 1, `…/pkg/stdx` → tier 1x; `pkg/binate/*`
  → tier 2).  Walk every package's imports (split by `.bni` vs `.bn`), map
  importer + imported to tiers, flag any higher-than-self edge, applying the
  std/stdx interface refinement.  A whitelist file (cf.
  `conformance-imports.whitelist` / `naming.whitelist`) covers sanctioned
  exceptions.
- **Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script
  only; wiring it into `scripts/hygiene/run.sh` and CI is a separate decision
  for the user.

### Per-file build constraints — conditional file inclusion/exclusion by target — DESIGN
- **STATUS — arch/os MVP IMPLEMENTED + LANDED.** The `#[build(EXPR)]`
  mechanism is live with the minimal `is(arch, …)` / `is(os, …)` vocabulary
  (membership form, bnas-aliased), gating at all four granularities: file
  (package clause), declaration, import, and `.bni` interface decls. The
  active config defaults to the host (read from `pkg/builtins/build` via
  `loader.ResolveBuildConfig`), overridable per `--target`. Landed across
  binate increments through `c7249552` (`.bni` gating + the `loader.bn` /
  `MergeFiles` split + conformance 746/747; the aliased-import fix `52d1c832`
  + coverage 738/745 was a detour surfaced en route). Conformance:
  731 (file), 733/735/736 (decl: const/var/type/func), 737 (import), 746
  (`.bni` decl), 747 (whole-`.bni` drop, negative). See
  [`plan-build-constraints.md`](plan-build-constraints.md) for the full
  status. **Still deferred** (each its own follow-up, none started):
  vocabulary beyond arch/os (`triple`/`backend`/`libc`/`ptrsize`/`version`
  with `is`/`at_least`/`at_most`), `bnlint --target`, main-module gating,
  migrating the `impls/` duplicate trees onto constraints, and the separate
  inline-asm (`#[asm]`) doc.
- **Concrete proposals**: see [`plan-build-constraints.md`](plan-build-constraints.md) — generalized per the user from *per-file* to **per-declaration** conditional compilation via a first-class `#[build(EXPR)]` annotation on any top-level decl (`const`/`type`/`var`/`func`/`package`/`import`); the `#[...]` grammar already reserves an `[ Annotation ]` slot on every top-level form (only `PackageClause` lacks it) and the attachment + `compiler.*`/`tool.*` namespacing are decided. Covers the predicate model + expression semantics (closed typo-checked vocab; ordered comparisons for `ptrsize`/`intsize`/`version`/`os.version`; hard-error on unknown/malformed/not-yet-wired), two gate seams (pre-parse file-level + post-merge/pre-resolve decl-level), disjoint variant definitions / conditional imports / conditional `.bni` decls (relaxing Invariant 1), the impls/-tree relationship + migration, tooling (bnlint `--target` now necessary; `tool.lint` lint-exempt), and a phased roadmap. Inline asm (`#[asm]`) is deferred to its own sibling doc that composes with this substrate.
- **What**: a way for a single file to opt *itself* in or out of
  compilation based on the build configuration — arch, target triple,
  OS, libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64),
  engine (`bnc` compiled vs `bni` interpreted), etc.
- **Why the current mechanisms are inadequate**:
  - **Separate trees + symlinks** (what we have now —
    `impls/{common,libc,baremetal}/…`, per
    [`pkg-layout-spec.md`](pkg-layout-spec.md) invariant 5 "Whole-package
    selection only"): too **coarse** (selection is whole-package /
    whole-variant-dir; "shared core + one per-variant file in the same
    package" is unrepresentable) and too **annoying** (symlinks to share
    the common files across variant dirs; a new axis means a new tree).
  - **Go-style filename suffixes** (`foo_posix.bn`, `foo_arm32.bn`): too
    **magical** (the constraint is invisible *inside* the file, smuggled
    in via the name) and too **coarse** (only a fixed suffix vocabulary;
    can't express conjunctions/disjunctions like "arm32 AND libc", or
    "any of {x64,aa64} but not baremetal").
- **Proposed shape**: an **annotation (writ large) near the top of the
  file** declaring the file's applicability condition as an *expression*
  over target predicates (`arch == "arm32"`, `libc`, `engine == "bni"`,
  with `&&` / `||` / `!`).  Two candidate syntactic forms to weigh:
  - a real **annotation on the `package` clause** (e.g.
    `#[build(arch == "arm32" && libc)] package foo`) — first-class,
    grammar-integrated, parseable; but the file must parse far enough to
    read it before we know whether to compile it, so the condition has to
    be evaluable from a cheap leading-prefix scan (read annotation →
    decide → continue or drop the file);
  - a **comment-form pragma** (a recognized leading comment, e.g.
    `//bn:build arch == "arm32" && libc` — Go-`//go:build`-shaped but
    expression-based, not suffix-based) — even cheaper to scan, but
    out-of-grammar / more "magical".
- **Design questions**:
  - **Predicate vocabulary + authority**: arch, triple, OS,
    libc-vs-freestanding, backend, engine, possibly user-defined build
    tags.  Where is the canonical list defined?  How extensible?
  - **Relationship to the `impls/` trees**: does this *replace* the
    `{common,libc,baremetal}` split (collapse back toward one tree, files
    self-select) or *complement* it (trees for the coarse axis,
    annotations for the fine)?  At minimum it should retire the symlink
    workaround; possibly the per-variant impl dirs too.  Decide
    explicitly — interacts with `pkg-layout-spec.md`.
  - **Loader/merge interaction**: excluded files simply don't join the
    merged package; ensure a package can still be legitimately empty (or
    require ≥1 surviving file) for a given target without spurious errors.
- **Tooling interaction (the bnlint question)**:
  - bnlint + the hygiene scripts must **understand** the annotation, so a
    file inapplicable to the current config isn't false-flagged (and so
    they can choose to lint each file under its applicable config(s)).
  - **Corollary worth designing in**: the same annotation surface could
    carry a directive telling bnlint / hygiene checks to **skip or ignore**
    a file (or regions of it) — a first-class "lint-exempt this file"
    mechanism, unifying build-constraints and lint-control under one
    annotation vocabulary.
- **Related entries to unify with**: the MAJOR "Better test-mode/target
  annotation than `.xfail`" entry above wants exactly this shape for
  *tests* (declare applicable modes/targets); and "Annotations and C
  function interop" below is the general annotation-syntax design.  This
  is the *source-file* instance of the same idea — design them together.
- **Prior art to consult**: Go build constraints (the `//go:build`
  expression form that replaced the `_GOOS` suffix era), Rust
  `#[cfg(...)]` / `cfg_if!`, Zig comptime target switches.  The
  expression form is the model.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL: remove process-global session state (multi-session blocker)
- **Now owned by [`plan-embeddable-vm.md`](plan-embeddable-vm.md)** (scoped
  2026-06-16): the `ir` half below is increments 4–5 of that plan, which
  covers the full compiler/VM global inventory, not just the REPL's two.
  This entry's `ir/gen.bn` line numbers are stale as of 2026-06-02; see the
  plan for verified ones.
- **What**: the REPL engine keeps per-session state in PROCESS-GLOBAL
  package vars instead of threading it through the session. v1 of the
  embeddable refactor (above) lifts the cmd/bni-local ones into
  `@ReplSession` but deliberately keeps **single live session per
  process**, leaving two `pkg/binate/ir` globals in place.
- **The globals**:
  - cmd/bni-local (lifted into `@ReplSession` by Stage 1 of the
    refactor): `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
    (`cmd/bni/repl_import.bn:24-41`) and `replInitCounter`
    (`cmd/bni/repl_decl.bn:411`).
  - `pkg/binate/ir` process-globals (NOT lifted in v1, the real
    multi-session blocker): `currentChecker` (`pkg/binate/ir/gen.bn:148`,
    set via `ir.SetChecker`) and the import alias map
    `importAliasNames`/`importAliasPaths` (`gen.bn:107/110`), with
    `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
    (`repl_import.bn:101/146`).
- **Why it matters**: single re-entrant session is unaffected (the ir
  globals are set once and save/restored inside import turns as today).
  But >1 concurrent embedded session in one process needs those globals
  session-scoped (or save/restored at every `Step` boundary) — a
  separate, larger change that must land BEFORE `pkg/binate/repl` can
  honestly claim multi-session support.
- **Guidance (applies now)**: **do not add any new REPL globals.** New
  per-session state goes through `@ReplSession`. Adding a global "to keep
  a signature stable" (the exact shortcut that created the current ones,
  per `repl_import.bn:18-20`) is what this entry exists to stop.
- **When**: only if multi-session embedding becomes a goal. Not needed
  for wasm B1 (one worker = one session).

### REPL — All five tiers LANDED (2026-05-29)
- **Status**: `bni --repl <file.bn|dir>` ships.  `plan-repl.md` is
  the live source of truth for per-step state — commit tables,
  verified behaviors, deviations from the original plan, and the
  per-tier remaining-follow-ups list.  Briefly:
  - **Tier 1 (load-then-poke)** LANDED.
  - **Tier 2 (top-level decls at the prompt)** LANDED in full,
    including the body-introduced dtor-regen follow-up landed
    2026-05-28 (`EnsureReplBodyHelpers`).  Every top-level decl
    kind supported by the language works at the prompt: `func`
    (incl. methods, redefinition replace + shadow), `const`
    (single, untyped, grouped), `var` (typed,
    untyped-with-literal-init, with init), `type` (aliases,
    named non-struct, structs incl. managed-field).  Bodies that
    introduce a fresh managed-aggregate shape with a destructible
    element (e.g. `@[]@Bag`) have their helper emitted before the
    body lowers.
  - **Tier 3 (forward refs)** LANDED for `func` decls.  Pending
    types / vars / consts (need a structural treatment of
    "unsized" type symbols) are deferred.
  - **Tier 4 (redefinition)** LANDED for both replace and shadow
    paths, free funcs and methods.
  - **Tier 5 (mid-session imports)** LANDED 2026-05-29 via
    `78685ac3`.  `import "pkg/foo"` at the prompt loads pkg/foo
    transitively, type-checks, IR-gens, lowers, and defines the
    package symbol in the session scope.
- **Remaining REPL work**, per plan-repl.md:
  - ~~**Tier 3**: pending types / vars / consts; cycle
    detection.~~  **ALL STAGES LANDED** 2026-05-28 → 2026-05-29
    via 9 commits on main; see
    [`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md)
    for the per-stage commit table.  Every top-level decl
    kind parks on forward-referenced dependencies; use-site
    propagation works through sized contexts (struct field,
    var decl, func sig, composite literal, impl recv, method
    receiver); per-caller sized-vs-reference distinction
    preserves recursive types via pointers; cycle detection
    catches genuine cycles through sized fields with a clean
    `pending cycle: A -> B -> A` diagnostic.
  - **Tier 4**: refcount-aware shadow warning (today fires
    unconditionally); forced-shadow escape hatch (syntax TBD per
    `claude-notes.md`).
  - ~~**Tier 5**: loader entry point for "load this one package
    now."~~  LANDED 2026-05-29 — `evalReplImport` in
    `cmd/bni/repl_import.bn` drives it via the session loader's
    existing LoadImports (plus a SaveAliasMapState /
    RestoreAliasMapState bracket around the per-package InitModule
    loop so the main alias map survives the wipes).
  - **Pretty-printer** (`pkg/replprint`) — **deferred** until
    interfaces land.  `bootstrap.println` is a temporary hack;
    building features on top of it would entrench it.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.  *(Now: Tier 3 added a
    pending queue (`check_pending.bn`) for `func` decls; types / vars
    / consts still fire immediately.)*
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.  *(Still true; deferred — see above.)*
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
    *(Now: Tier 4 substrate (`9af2d56`) added the funcIndex hash;
    `LookupFunc` is O(1).  Eager CallCache fill keeps shadow
    semantics correct.)*
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names, per-step commit tables,
  and the live follow-up state):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts immediate-mode entries.
     Multi-line input via paren-aware accumulator.  Auto-`println`
     wrap of bare exprs deferred (gated on interfaces).
  2. ~~**Add new top-level decls at the prompt.**~~ **FULLY LANDED
     (2026-04-30 → 2026-05-28).**  All decl kinds: `func` (incl.
     methods), `const`, `var` (typed + untyped-with-literal-init +
     var-initializer evaluation), `type` (aliases, named
     non-struct, structs incl. managed-field).  Body-introduced
     new-managed-aggregate dtor regen also landed (2026-05-28,
     `EnsureReplBodyHelpers`).
  3. ~~**Forward references.**~~ **LANDED for `func` decls
     (2026-05-05).**  Pending-validation queue in the type checker;
     parked decls retry on every newly-resolved name.  Pending
     types / vars / consts remain (see follow-ups above).
  4. ~~**Redefinition.**~~ **LANDED in full (2026-05-01 →
     2026-05-05).**  Compatible-sig: in-place rebind keeps
     CallCache valid.  Incompatible-sig: `LowerOneFuncShadow`
     appends + re-points funcIndex; old callers retain old VMFunc
     via eager-filled CallCache.  Methods follow the same rules,
     keyed on qualified `<pkg>.<TypeName>.<Method>`.  Substrate
     `9af2d56`; shadow `63cc49b`; method redef `026ad22`.
     Refcount-aware shadow warning + forced-shadow escape hatch
     are remaining follow-ups.
  5. ~~**Mid-session imports.**~~  **LANDED** 2026-05-29 via
     `78685ac3`.  evalReplImport in cmd/bni/repl_import.bn
     drives the existing loader's LoadImports for incremental
     transitive loads, brackets the per-package InitModule
     loop with SaveAliasMapState/RestoreAliasMapState so the
     session's main alias map survives, and routes through
     c.RegisterReplImport to make `foo.X` resolvable from
     subsequent prompt entries.
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (archived — see `historical-notes.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Annotations and C function interop
- **Option E (`__c_call` intrinsic) has a detailed implementation plan:
  [plan-c-call.md](plan-c-call.md).**
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
  - **Option E**: a `__c_call` compiler intrinsic at the call site, no
    declaration needed.  Two forms were considered:
    - **E1 (rejected)**: pass a C prototype string —
      `__c_call("ssize_t write(int, const void*, size_t)", fd, buf, len)`.
      Reads nicely, but forces the compiler to parse C and resolve C
      types, which drags in typedefs, macros, and platform builtins
      (`__size_t` &c.).  Not practical.
    - **E2 (preferred)**: pass the C symbol name, an explicit return
      type, then the argument values already in (or cast to) the
      Binate types that match the C ABI —
      `result = __c_call("write", int, cast(int, fd), cast(*uint8, buf), cast(uint, len))`
      (casts are unnecessary when the variables already have the right
      type).  Supported argument/return types: scalars, struct types,
      and pointers to these (to any depth: `*T`, `**T`, …).  This
      reuses the backends' existing platform-C-ABI lowering (struct
      sret thresholds, register assignment) — no C parsing, no type
      resolution, no new ABI logic.  The symbol name is emitted
      verbatim (no `bn_` mangling); the backend emits the matching
      `extern`/`declare`.
  - **C-types alias package (decided)**: a package (e.g. `pkg/c`)
    pins the Binate↔C scalar correspondence in one place so call sites
    don't open-code it.  `C_int`/`C_uint` = `i32`/`u32` (C `int` is
    32-bit on both ILP32 and LP64, *not* target-word-width like Binate
    `int`); `C_long`/`C_ulong` = target-word (LP64 Unix; matches Binate
    `int`/`uint`); `C_size_t` = `uint` (pointer-width); `C_char` = `i8`
    (signedness is platform-dependent in C — note the caveat, but it's
    promoted on pass so rarely matters).  Plus a sentinel `C_void` for
    the return-type slot of functions that return nothing.  So the
    example's `fd` is really `C_int` (= `i32`), not `int`.
  - **Scope decisions (v1)**:
    - **Compiled-mode-only to start.** The compiler emits a direct
      call; the VM would need FFI-style dispatch (resolve the symbol
      via the extern registry + marshal by the supplied types) — punt
      that.  `__c_call` outside compiled mode is an error for now.
    - **Include variadics from the start.** The whole point of
      `__c_call` is to retire `pkg/bootstrap`'s hand-written C
      wrappers and the special shim machinery — and several of those
      OS interfaces are variadic in C (`open(const char*, int, ...)`
      where `mode` is a vararg; `fcntl`, eventually the `printf`
      family).  Punting variadics would leave bootstrap unable to go
      away, defeating the purpose.  So v1 supports them.
      - **Boundary marker (required).** The call site must declare
        where fixed args end and variadic args begin — it can't be
        inferred from the values (`open(path, flags, mode)` is
        indistinguishable from a 3-fixed-arg call).  Proposed: a
        `C_varargs` sentinel (or a recognized `...` token) in the
        argument list:
        `__c_call("open", C_int, path, flags, C_varargs, mode)`.
        Everything after the marker is an anonymous/variadic arg.
      - **Backend work is lopsided.** LLVM path: nearly free — emit
        `declare i32 @open(i8*, i32, ...)` + a varargs call with the
        right fixed-arg count, and LLVM does the platform-correct
        lowering (x86-64 `AL` = vararg float count, darwin-arm64
        stack-passing, 64-bit-vararg alignment) for us.  Native
        backends (`pkg/native/{arm64,amd64}`): real work — they emit
        machine code directly and must implement the vararg
        convention per target (darwin-arm64 stacks all varargs;
        x86-64 SysV sets `AL`; AArch64-Linux/arm32 mostly match the
        fixed convention but 64-bit varargs need 8-byte alignment).
        This extends the existing `CallConv`/register-assignment
        logic; needs per-target tests.
  - **Open considerations for E2 (still to resolve)**:
    - Confirm the full `pkg/c` scalar table against each target
      (`C_long` on a 32-bit target, `C_char` signedness, the float
      types if/when floats land).
    - Final spelling of the variadic boundary marker (`C_varargs`
      sentinel vs a `...` token vs an explicit fixed-arg count).
    - VM/dual-mode FFI dispatch (deferred above) when interpreted-mode
      `__c_call` is eventually wanted.
  - **Companion idea — link-requirement annotation (sketch)**: Option E
    makes a C symbol *callable*; a complementary annotation would make
    it *resolve at link time* by declaring, at the source level, that
    using a package requires linking some C library — so the driver
    adds the flag automatically instead of every consumer passing
    `--cflag -lm` / `--link-after-objs` by hand.  Prior art:
    Rust `#[link(name = "m", kind = "static")]`, Go cgo
    `// #cgo LDFLAGS: -lm`, MSVC `#pragma comment(lib, "foo")`.
    Natural shape: `#[link("m")]` (optionally a `static`/`dynamic`/
    `framework` kind), most naturally on the `.bni` since the link
    requirement is part of the package's contract.  This is also the
    first real payoff of the general annotations feature this item is
    about — both Option E and this want it.
    - **Open wrinkles**:
      - **Transitivity** — the requirement must propagate through the
        import graph (aggregate + dedup all declared libs for any
        binary that transitively imports the package).  Hooks into the
        loader's `ldr.Order` walk + the driver's `clangArgs` assembly.
      - **Link ordering** — static archives only supply symbols
        referenced by *earlier* inputs, so aggregated `-l` entries
        need correct placement vs. the `.o` files and runtime (the
        driver already does this for `linkAfterObjs`).
      - **Search paths** — keep the annotation name-only (`-l`); leave
        `-L<dir>` to driver flags.
      - **Platform-conditionality** — a `libm` dep is meaningless on
        bare-metal arm32 and `framework` kind is macOS-only, so the
        annotation likely needs to be target-qualifiable.  Ties into
        the C-free principle: this exists only to interface with
        existing C systems and should evaporate on freestanding
        targets.
      - **Static-spec portability** — even with `kind = static`,
        expressing it portably is messy (GNU ld `-l:libfoo.a` /
        `-Wl,-Bstatic`; macOS `ld` has neither), so it may need
        per-platform lowering in the driver or a full-path escape
        hatch.

---

## TEST COVERAGE — conformance matrix follow-ups

### Sweep for STALE xfails — the runner skips xfailed tests, so now-passing ones sit marked-failing forever (2026-06-13) — 🟡 OPEN (all host-runnable modes SWEPT; only the qemu-gated cross modes remain)
Discovered while triaging done-but-residual todo entries: `const-group-bare-inherited-overflow` was fixed by `b9d6d807` but its 11 `.xfail.*` files were never removed, and `conformance/run.sh` does NOT re-run xfailed tests (it skips them — they show as `x`, never `XPASS`), so the stale xfail was invisible. There are ~247 conformance `.xfail.*` files (+29 unittest); an unknown number are similarly stale.
- **builder-comp + builder-comp-comp (gen2) swept (2026-06-13)**: only ONE stale xfail — `const-group-bare-inherited-overflow` — REMOVED (binate `680a4eca`, all 11 markers; `.error` type-check test, stale in every mode). Both default LLVM modes otherwise clean.
- **VM modes swept (2026-06-13)** — `builder-comp-int` / `-comp-int` / `-int-int`, via `run.sh --check-xpass <mode> <test-names>` (run only the xfailed tests, not the whole hang-prone suite). **25 stale removed in 2 commits:**
  - `8741c552` (14 top-level): `718_funcval_spill_over_vm_cap` ×3 VM modes (bytecode→bytecode func-value dispatch never hits the 7-arg `_call_shim_*` cap — that cap only bites compiled-target/nested-VM); + 11 `-int-int`-only that all blamed now-fixed double-VM infra (`272_raw_slice_star_sugar`; the `586/592/673/674/675/676/677/678/682` cross-pkg `*_balance` family on the int-int "package pkg/builtins/rt not found" loader bug; `665_transitive_iface_reexport` on the int-int multi-package `rt.MemCopy` NULL-deref). Confirmed fixed: the canaries `136`/`383`/`061`/`373`/`384` are unmarked + green under int-int.
  - `bcb3c362` (11 subdirectory readonly/matrix): `pass-arg/value-struct{,-large}` (int/-comp-int/-int-int) + the `-int-int` Round-2 cells (`nested-index/field/nested-value-struct`, `readonly/alias/method-receiver`, `readonly/construct/readonly-iface`, `readonly/wrapper-order/inner-{managed,raw}-ptr`). These were left xfailed only on VM after the plan-cr2-1 Defect-1/Round-2 fixes landed on LLVM (cf. line ~879 "stay xfailed on VM / native-globals").
  - **VM xfails KEPT (genuine)**: `regressions/c-call/*` + top-level `498/500/527/530` (VM has no FFI); `matrix/globals/readonly/struct` (Defect-1 `gen_selector` global-readonly path, still open); `regressions/named-func-value-construct-literal` (open B2 follow-up, xfailed in every mode incl. LLVM); `385/386_iface_nil_dispatch*`; `708/709/725/727_reflect_*`.
- **Unittest comp-comp-int swept (2026-06-13)** — `76fe86cc`: 4 stale (`cmd-bnlint`, `pkg-binate-{codegen,ir,vm}`) that blamed the now-fixed "boot-comp-int VM field-layout bug"; all 4 packages' full suites pass under comp-comp-int. NOTE: `scripts/unittest/run.sh` has NO XPASS detection (it just skips xfailed packages) — sweep by hand (move marker aside → run → restore). The 8 ccall unittest xfails (`pkg-bootstrap`/`pkg-builtins-rt`/`pkg-std-os` in VM modes) are genuine (VM can't interpret `__c_call`).
- **Native aa64 + x64_darwin swept (2026-06-13)**: 0 stale. `386` (compiled SEGVs with no VM panic msg; mode-correct, pinned by `385`), `705/706/707` (native closure-float shim gaps, claude-todo #121 open) all genuinely fail. gen3 (`builder-comp-comp-comp`) lone xfail is `386` — same mode-correct reason, structurally can't XPASS.
- **CROSS MODES SWEPT via the CI workflow (2026-06-14) — 99 stale conformance xfails removed.** The on-demand `.github/workflows/conformance-xpass.yml` (Actions → "Conformance XPASS (stale-xfail sweep)" → Run workflow; blank `mode` = all 10 modes, or pass one) re-runs each mode's xfailed tests under `--check-xpass`; a red job lists XPASS = stale markers. Full-matrix run results:
  - `native_aa64`: **29** `matrix/scalar-diff/*` signed sub-word cells (arith/bitwise/cmp/int-cast/shift/float-conv) — aa64-subword narrowing fixed; binate `5f94558b`. Host-runnable but MISSED by the earlier top-level-only host sweep (the same subdirectory-enumeration lesson — these live under `matrix/scalar-diff/`).
  - `arm32_linux`: **40**, `arm32_baremetal`: **30** — native arm32 backend + multi-return tuple-packing caught up (markers blamed "native arm32 not yet implemented" / Plan-3 tuple-packing; some carried already-stale "drops result type / SILENT wrong-code" text). binate `1ce5a6d9` / `56c275b6`. (Includes the line-~5077 `abi/iface-multi-return{,-assign}` cells — confirmed stale as predicted.)
  - `native_x64`: **22** stale, but only visible AFTER a **workflow bug** was fixed. run.sh filters were substring-match, so the `value-struct` xfail filter also pulled in the *unmarked* `value-struct-large` (which crashes on native_x64) → false-positive that masked everything else. Fixed by `run.sh --exact` (exact filter match) + the workflow passes it (binate `982727d1`). With `--exact`, two consecutive native_x64 CI runs agree on 22 stale: `538_float_lit_tie_roundbit` + `635_float32_arith` + the `matrix/const/*` float32/float64 tie/half/neg/tenth cells (native float round-bit / float32-narrowing, "blocked on a new BUILDER release" = bnc-0.0.9, now shipped); plus `matrix/readonly/*` + `matrix/nested-index/field/*` (plan-cr2-1 Defect-1/Round-2 shared-IR-gen, same cells dropped on the VM modes). Removed: binate `27ba1f7e`. Post-removal native_x64 sweep: green. **All 10 modes now green under the sweep** (121 stale conformance markers removed total: aa64 29 / arm32_linux 40 / arm32_baremetal 30 / native_x64 22).
  - **Unittest sweep now possible** — `scripts/unittest/run.sh` gained `--check-xpass` (binate `ddc624d2`; same XPASS-on-stale semantics, per-package): run `scripts/unittest/run.sh --check-xpass <mode>`. Swept the 3 VM modes: `pkg/builtins/rt`, `pkg/bootstrap`, `pkg/std/os` all XPASS (they're injected as native in the VM, so their tests run against native code and pass — e.g. rt runs 21 passing tests). **8 stale markers removed** (bootstrap+rt on `builder-comp-int`; bootstrap+rt+os on `-comp-int` and `-int-int`); binate `55229591`. The `native_aa64` unittest xfails (11, the weak-`buf.Builder`-dtor dup-symbol MAJOR bug) correctly stay XFAIL (`mangle` re-confirmed genuinely failing). The arm32 unit xfails (16 baremetal + 1 linux) need qemu + the unittest `--check-xpass` isn't wired into CI, so they're UNSWEPT.
  - **STILL OPEN — cross-mode unittest xfails (17)**: the unittest runner (`scripts/unittest/run.sh`) still lacks `--check-xpass` (it just skips xfailed packages), so the workflow is CONFORMANCE-only; sweep those by hand or teach the runner XPASS detection.
  - **FOLLOW-UP — `value-struct-large` on `native_x64`**: it's *not* xfailed there yet crashes (empty output) when run — a real missing-xfail or native_x64 bug, surfaced (then masked) by the substring collision. Worth a look now that `--exact` no longer pulls it in.
- **METHODOLOGY (learned the hard way)**: enumerate sweep sites with `find conformance -name '*.xfail.*'` (RECURSIVE) — a top-level `ls conformance/*.xfail.*` misses ~160 subdirectory (`matrix/`, `regressions/`, `abi/`) markers. Per-mode list: `find conformance -name '*.xfail.<mode>'`. Run only the xfailed tests as filters (amortizes one toolchain build); `--check-xpass` reports `XPASS` for the stale ones.
- **Why it matters**: stale xfails hide regressions (a real future failure on that test would still show `x`) and inflate the xfail count; each one may correspond to a "done-but-not-archived" todo entry.

### Plan-3 adversarial-review follow-ups (test-hygiene + coverage gaps from `cc2ddcc4` / `997c4c04` / `0c707e1f`) — 2026-06-08
Non-wrong-code items from the adversarial review of the plan-cr2-3 work; each is small. (The live wrong-code findings are the OP_CAST/iface-arg CRITICAL and the float-multi-return MAJOR above.)
- **Weak / over-claimed Defect-6 pin**: the addr-aggregate `global` cells (`997c4c04`) + their generator docstring/README claim to pin "2-word sizing / mis-sized-to-one-word drops a word" — but store+load are width-consistent so the cell is INVARIANT to allocation size (it pins materialization + `__init`-store + read-back wiring, NOT sizing). Fix the docstring (`gen-addr-aggregate-matrix.py:96-104`) / README / commit framing to match. Also Defect 6 closed using only the two shapes that typecheck; readonly-wrapped + named-over-aggregate + raw `*func()` + uninitialized-nil global companions (the Class-A materialization risk in `plan-code-red-2.md`) were left out — record as an explicit deferral (invoking them is blocked upstream at the call typechecker).
- **Coverage gaps**: aa64 per-field iface-multi-return collect (`aarch64_iface.bn:204-228`, the exact loop that dropped sub-word fields) has NO unit test (only conformance on aa64); x64 `collectMultiReturnTuple`-for-iface has no unit test for the IFACE op; an aggregate-component iface multi-return tuple (`(Pair,int)`) is uncovered; the iface-method-arg-with-global position is covered by neither a unit test nor 551/573 (see the CRITICAL entry).
- **Latent fragility (nit)**: `pkg/binate/ir/gen_call.bn` computes `resultTyp` generically and hands it to `EmitCallHandle`/`EmitCallIndirect` (magic-name dispatch) with no structural guard that it isn't a multi-return struct — add a cheap assert so the "these ops never carry a multi-return" invariant is enforced in code, not convention.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (6 reviewers → adversarial verify → completeness critic; 21/23 findings confirmed).

The code-red conformance-matrix family (`conformance/matrix/`, see
`plan-code-red.md` §7) has four members realized: `refcount` (Class 1),
`scalar` (Class 5), `abi` (Class 4), `const` (named-constant invariant). These
are the remaining matrix-shaped classes not yet built as their own matrix —
candidates for after the loose-axis finish (const-expr folding + ABI
`handle`/`__c_call` shapes).

### (b2) Lifecycle matrix — Class 6 (`@Iface` / `@[]@I`) + Class 7 (captured-`@func` over-release) — PARTLY ADDRESSED 2026-06-05 (plan-cr-p2-2 step 5)
- **Status**: the existing `conformance/matrix/refcount` form × type grid already
  covers Class 6's construction/consumption shapes (the copy-sites are now uniform
  after the `emitStoreManagedSlot` consolidation), and `604`/`605` add lifecycle-
  DEPTH balance (a value chained through param/store/pass/return/bind/invoke) for
  captured-`@func` and cast-from-impl `@Iface`, green in builder-comp/-int/-comp/
  native-aa64. REMAINING: a true single-program **Class 7 native↔VM trampoline**
  balance test is not expressible in the single-mode conformance harness (each
  test runs in one mode) — needs a cross-mode harness; left as a follow-up.
- **Why a matrix**: Class 6 (`@Iface`/`@[]@I` first-class lifecycle) and Class 7
  (native call-a-captured-`@func` over-release via the VM trampoline) are
  lifecycle-completeness classes. Axes would be `managed-kind (@Iface / @[]@I /
  captured-@func) × construction (make / literal / cast-from-impl / capture) ×
  consumption (call-method / index / range / pass / return / discard) ×
  backend`, with a refcount-balance assertion (mortal source).
- **Status**: the refcount matrix already covers `@Iface`/`@func` as value-types
  across assignment-forms, so this would EXTEND rather than start fresh — the
  new axis is construction × consumption depth (esp. the native↔VM trampoline
  path for Class 7, which the refcount matrix does not exercise).
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family,
  `@[]@I` literal element leak); a matrix would close the long tail.

### (b3) Class 3 / Class 8 — point-bugs, NOT matrices
- Class 3 (cross-package / interface-name type-resolution ordering → `i8*`
  fallback) and Class 8 (multi-package loader resolution at int-int depth) are
  one-off ordering/loader bugs, not systematic products. Track them as
  individual regression tests under `conformance/regressions/` + filed bugs, not
  as a matrix.

### (b4) Differential harness v3 — port `gen-diff-scalar.py` to Binate (dogfood) + flavor B — NOT STARTED
- **Context**: the property-based differential value-correctness harness
  (`conformance/matrix/scalar-diff`, oracle = spec) is realized through v2 —
  shifts, conversions, arithmetic, comparisons, bitwise; 123 cells / 5415
  tuples; generator `conformance/gen-diff-scalar.py` (Python). See
  `plan-differential-testing.md` (phasing item 3) for the full design.
- **v3 scope** (the remaining phase):
  1. **Port the generator to Binate** — rewrite `gen-diff-scalar.py` as a `.bn`
     program so the harness dogfoods the language on a real codegen-shaped task
     (LCG, two's-complement oracle, bit-pattern formatting). Keep the emitted
     cells byte-identical so the existing `.expected`/`.xfail` set and
     `--check` idempotence carry over unchanged.
  2. **Flavor B (optional, for the highest-volume ops)** — one self-checking
     `.bn` per op that loops an embedded `(inputs, expected)` table and prints
     `mismatch i: got… want…`, denser than the current static-cell flavor A and
     debuggable on failure (flavor A shows *which* tuple, not the wrong value).
     Decide per op once flavor A shows which need the volume.
  3. **Sample-size knob** — a fixed, seeded count parameter so coverage can be
     dialed up without touching the generator logic.
- **Why**: dogfooding is the highest-leverage *process* check (the OOM, the
  `@func`-dtor crash, the shift bug all first surfaced by compiling real Binate
  programs); porting the generator turns the harness itself into one more such
  program. Not urgent — v1/v2 already give the value coverage; v3 is the
  dogfood + debuggability upgrade.

## P3 — low-priority follow-ups

### `os` errors carry only the op, not the failing path (P3)
`pkg/std/os` `failErrno(op)` renders e.g. `"open: not found"`, but
plan-std-error-hierarchy.md §7 specifies context `(path, op)` —
`"open /etc/foo: not found"`. The path is available in `OpenFile`'s `name`
param (Create/Open delegate to it); `read`/`write`/`seek` operate on an fd and
have no path, so op-only is correct there. Add the failing path to the open
family's error context (e.g. a path-aware wrapper, or `failErrno(op, path)`).
Deferred 2026-06-11 (user: op-only acceptable for now) — low impact (message
richness, not classification). Tests: extend the `TestOpen*Classified` cases
to assert the path appears in the rendered message.
