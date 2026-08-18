# Plan: untyped string literals (`TYP_UNTYPED_STRING`)

## Status (2026-08-17) — COMPLETE

All increments landed; the MAJOR `[N]readonly char`→slice miscompile is fixed at the
root, full `builder-comp` conformance green (2972/0).  See the done-log entry for the
full commit list.

- **Inc 1** `b97e79656`, **Inc 2** `c713faf7b`, **comparison** `b40b10c9a` (the latter
  not in the original increment list; it resolved the string-literal `==` question that
  surfaced during Inc 2 — string literals compare element-wise with char arrays).
- **Inc 3:** spec (docs `e27280d` — `const.string.compare` + zero-pad scoped to init
  sites; adversarial-reviewed, two MAJORs fixed), cleanup `8056c5ba4` (rename
  `isStringLitNaturalType`→`isReadonlyCharArray`, drop `StringLitNaturalType` /
  `defaultTypeForExpr`), spec-coverage sync `48b339769`.

Sibling MAJOR still open (separate, in `claude-todo.md`): the explicit-`cast` array→slice
miscompile.

## Motivation — the root cause of a MAJOR miscompile

`bnc` wrongly accepts a **runtime** `[N]readonly char` array where a slice type
(`@[]char` / `*[]readonly char`) is expected, then mis-lowers it (dumps the array
bytes into the slice buffer — a use-after-free). The non-readonly `[N]char` is
correctly rejected; the hole is specific to the readonly-element array path.

**Root cause.** A string literal is given a *concrete* natural type
`[N]readonly char` (a `TYP_ARRAY`). Assigning it to a slice then requires an
array→slice "conversion", implemented in `AssignableTo` as a type-only arm
(`isStringLitNaturalType(src) && isStringWritableSliceTarget(dst)`). But a runtime
`[N]readonly char` array has the **same type** as a literal's natural type
(`isStringLitNaturalType` cannot tell them apart), so the arm fires for runtime
arrays too. array→slice is not a legal implicit conversion for a runtime array;
only a string **literal** (whose bytes live in immortal rodata) may decay.

An expr-gated patch (gate the decay on the source expression being
`EXPR_STRING_LIT`) works but *entrenches* the concrete-typed-literal + conversion
model that is the actual defect. The correct fix removes the collision entirely:
**make string literals untyped**, like `TYP_UNTYPED_INT` / `_FLOAT` / `_BOOL`. An
untyped-string type adopts its target by context and is a `Kind` a runtime array can
never have — so there is no array→slice decay to police, and the bug class is
eliminated **by construction**.

This is a language-semantics change (string-literal typing), approved 2026-08-17.

## End-state design

Introduce `TYP_UNTYPED_STRING`, a distinct untyped `Kind` carrying the literal's
unescaped byte length `N` in `ArrayLen` (and `Elem = readonly char`, so contexts
that need the natural array shape can reconstruct `[N]readonly char`). Mirrors the
existing untyped-scalar kinds.

- **Typing.** `checkExpr(EXPR_STRING_LIT)` returns a fresh `TYP_UNTYPED_STRING`
  with `ArrayLen = unescapedStrLen(text)` instead of `StringLitNaturalType` (the
  `[N]readonly char` array).
- **Adoption (`AssignableTo`), type-only and SAFE** because the kind is unique to
  literals — a runtime array can never reach these arms:
  - `TYP_UNTYPED_STRING → *[]readonly char` — zero-copy raw-slice borrow of rodata
  - `TYP_UNTYPED_STRING → @[]readonly char` — zero-copy managed-slice borrow
  - `TYP_UNTYPED_STRING → @[]char`          — literal-init allocate + copy
  - `TYP_UNTYPED_STRING → *[]char`          — REJECTED (mutable raw view of rodata)
  - `TYP_UNTYPED_STRING → [M]readonly char` / `[M]char`, `M >= N` — zero-padded
    array init (the current `stringLitFitsArray` rule, now reading `N` from the
    untyped type)
  - named-distinct / alias / readonly-wrapped targets peel first (e.g.
    `testing.TestResult = @[]char`)
- **Default (inference context).** `defaultType(TYP_UNTYPED_STRING)` →
  `@[]readonly char`. `defaultTypeForExpr`'s `EXPR_STRING_LIT` special-case becomes
  redundant (the default now falls out of the `Kind`, like untyped int) and is
  removed.
- **A runtime `[N]readonly char` array** stays `TYP_ARRAY`: it hits only the
  array→array arms (`Identical` exact-match / the `arrayLengthsMatch` copy), and
  there is **no** array→slice arm — so returning/assigning it as a slice is
  rejected. Bug gone by construction.

### What the concrete natural type currently buys — and how the untyped model keeps it

- **Array length `N`** (zero-pad `[M]char` init; exact-length copy) — carried in
  `ArrayLen` on the untyped type; `N = unescapedStrLen(text)`.
- **Nothing in IR-gen.** IR-gen already materialises strings from the *expression*
  (`EmitConstString` on `EXPR_STRING_LIT`, `gen_expr.bn`) plus the *target* slot
  type (`gen_short_var.bn:135` special-cases `EXPR_STRING_LIT` → `@[]readonly char`;
  `coerceArg` / `EmitStringToChars` pick rodata-borrow vs mslice-copy from the
  target). It does **not** drive off the checker's `[N]readonly char`. IR-gen also
  already defensively converts untyped kinds (`gen_short_var.bn:127`
  `TYP_UNTYPED_INT → int`); `TYP_UNTYPED_STRING → @[]readonly char` slots in the
  same way.

### The long tail — contexts that want a concrete array

A few contexts currently rely on a string literal being an array. Each ADOPTS the
natural `[N]readonly char` (reconstructed from `ArrayLen`/`Elem`) at its own check,
preserving today's behavior:

- **Comparison** `"a" == "b"`: equal-length string literals compare today as
  `[N]readonly char` arrays (`aggregateComparable`). `checkEqOperands` adopts each
  untyped-string operand to its array type before the existing array-comparability
  walk. (Unequal lengths remain non-comparable, as today.)
- **Indexing** `"abc"[i]`: adopt to the array (or to `@[]readonly char`) before the
  index check.
- **`len("abc")`**: today an array `len` is a compile-time constant `N`; adopt to
  the array so `len` stays constant (relevant if `len(lit)` is ever used in a const
  position). Verify whether any test/spec relies on this; if not, defaulting to the
  slice (runtime `len`) is acceptable — decide during implementation, don't silently
  regress.
- **Adjacent string concatenation** `"a" "b"`: confirm where it is folded (lexer /
  parser / checker). If folded to one `EXPR_STRING_LIT` before typing, no change; if
  combined at check time, the fold must produce one `TYP_UNTYPED_STRING` of the
  combined length.

## Increments (each keeps the tree green and is independently cherry-pickable)

**Inc 0 — regression tests (failing, marks the bug).** Land the checker-level
negative tests (runtime `[N]readonly char → @[]char` / `*[]readonly char` is an
error; the `[N]char` control; string-literal positives still accepted) as
`xfail`/expected-fail against current `main`, per the Bug Discovery Protocol, so the
hole is tracked before the fix. (Currently drafted in a scratch test; formalize
placement.)

**Inc 1 — add `TYP_UNTYPED_STRING`, inert.** New `Kind` const; constructor
(`makeUntypedString(n)`); predicates; the `AssignableTo` adoption arm (slice + array
targets); the `defaultType` arm. Nothing produces the kind yet, so the tree stays
green (pure addition). Unit-test the arm directly (`makeUntypedString(3).AssignableTo`
to each target shape).

**Inc 2 — flip `checkExpr(EXPR_STRING_LIT)` to `TYP_UNTYPED_STRING`.** The
interdependent core: switch the producer; remove the two `isStringLitNaturalType`
arms from `AssignableTo` (the array arm folds into the new untyped arm; the slice arm
is deleted — the bug); update `checkEqOperands` / indexing / `len` to adopt the
natural array; audit IR-gen for a defensive `TYP_UNTYPED_STRING → @[]readonly char`
conversion mirroring the untyped-int one. Make Inc 0's tests pass; rewrite the ~8
type-only unit tests (`types_const_test.bn` / `string_lit_test.bn` /
`types_assignable_test.bn`) that assert the removed type-level decay — repoint them
at the untyped kind (`makeUntypedString`) or at expression-level `checkSrc` tests.
Full `pkg/binate/types` + conformance (string-heavy) must stay green.

**Inc 3 — cleanup + spec.** Delete now-dead helpers (`isStringLitNaturalType`,
`StringLitNaturalType`, `stringLitInitFitsArray` if fully subsumed, `defaultStringLitType`
if folded). Update spec §6.6 (string-literal typing: untyped, adopts by context,
defaults to `@[]readonly char`). Verify Annex A / grammar unaffected.

## BUILDER / layering

`pkg/binate/types` is BUILDER-compiled (in `cmd/bnc`'s tree). A new `Kind` int const
+ new arms are BUILDER-safe (no new language feature). No `#[build]` / new-syntax
concerns. IR-gen (`pkg/binate/ir`) is BUILDER-compiled too; the added
`TYP_UNTYPED_STRING` conversion is a plain switch arm.

## Out of scope — tracked separately

**Explicit `cast(@[]char, a)` of a runtime `[N]readonly char` array** is a SEPARATE,
pre-existing MAJOR hole of the same UAF class: the `cast` branch
(`check_builtin.bn`) does no source→target shape check, so IR-gen emits an `OP_CAST`
from an N-byte array value into a 4-word managed slice (layout garbage). Orthogonal
to literal typing — the untyped rework does NOT close it (a runtime array is still a
`TYP_ARRAY`; `cast` validation is the gap). Filed in `claude-todo.md`; needs its own
shape-compatibility check in the cast checker. `bit_cast` is correctly rejected; only
value-preserving `cast` slips.

## Verification

- `scripts/unittest/run.sh builder-comp pkg/binate/types pkg/binate/ir` (+ `-int`).
- Conformance (string-literal-heavy): full `builder-comp` at least; the string /
  array / slice spec families.
- Each increment adversarially reviewed (the reviews that shaped this plan:
  completeness — no runtime-array→slice bypass; root-cause — untyped model
  eliminates the class and A was a clean-seam interim).
- Hygiene (`scripts/hygiene/run.sh`) — file-length on `types_assignable.bn` /
  `check_expr_binop.bn` if arms grow; bnfmt.
