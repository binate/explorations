# Plan: one exact, type-aware constant evaluator

**Status:** 🟡 design (2026-09-27, work-4/session).  Decided by the user 2026-09-27: do it now ("2. yes");
typed constants wrap at their type ("Wrap at the type"); a typed-constant `MIN / -1` is a compile-time
error ("I guess a compile-time error is fine"); `conv.cast.const-not-laundered` stays ("I guess we can keep
the exception, since it probably catches real bugs"); a negative constant count in a constant
`unsafe_shl` / `unsafe_shr` is a compile-time error ("3. compile-time check seems fine.").  Tracked in
`claude-todo.md` ("Typed-constant expressions are folded without their type").  Follows
`plan-untyped-bitwise-constants.md` (part 1 landed: docs `b1c5a66`, binate `71b184eaf`).

## Problem

Integer constant expressions are folded by five evaluators that disagree:

| Evaluator | Arithmetic | Used by |
|---|---|---|
| checkExpr's untyped fold (`foldIntArith` / `foldIntBitwise`, HasLitVal) | exact bignum, untyped only | typing / fit of untyped constants |
| `check/foldConstNum` | exact bignum, **typeless** | cast fit check, `constIntFor` (const decls), `foldConstMagSign`, `.bni` consts |
| `check/evalConstIntValue` | host `int`, typeless | array dims (`evalConstInt`), shift-count sign check, const-decl fallback, composite keys, `.bni` / const-resolve fallbacks |
| `check/foldConstBoolValue` | via `foldConstNum` | bool consts |
| `irgen/evalConstExpr` (+ `evalConstBool`) | host `int`, typeless | const decls without a checker stamp (bare group members, imports, REPL), array dims without `LenVal`, switch-case values, composite keys |

None applies the typed rule, so typed constants are right only at run time.  The host-`int` ones also
wrap, truncate on a 32-bit host, crash on a negative shift count, and lack `unsafe_shl` / `unsafe_shr`,
and `genConstGroup` / `genConst` silently store the iota value (or 0) when they fail.  Repros are in
the todo entry: `const Z uint8 = cast(uint8, 1) / cast(uint8, 0)` reads 0,
`[cast(uint8, 200) + cast(uint8, 100)]int` has length 300, `const M int8 = -cast(int8, -128)` is an ICE,
`const ( A = -1 >> (0xFFFFFFFFFFFFFFFF - iota); B )` crashes bnc, `const ( A = unsafe_shl(1, iota + 4); B; C )`
reads `16 1 2`, `[(1 << 64) + 3]int` has length 3.

IR-gen cannot just read checker stamps: it folds per generic instantiation (`sizeof(T)` in an array
length or const inside a generic body) and on paths the checker never stamped (imports, the REPL).

## Semantics (spec text to write)

`const.expr.typed` (§6.4) — an operator whose integer operands are constants and at least one is typed,
of type `T` (a type whose underlying type is an integer type: named, alias, `char`), is evaluated exactly
as for non-constant operands of type `T`, and its result is a constant of type `T`:
- An untyped constant operand takes `T` and must fit it (`const.expr.fit`); it is folded exactly first
  (untyped subexpressions stay exact, `const.expr.precision`).
- `+ - *` and unary `-` wrap (§13.3 `expr.arith.defined`); `/ %` truncate; `~` is the complement at `T`'s
  width (`expr.bitwise`); `& | ^` on `T`'s bits.
- A shift takes `T` from its value operand only; the count is its own position, any integer type, and
  its value is exact.  `expr.shift` / `expr.shift.overshift` apply (`cast(uint8, 1) << 8` is 0).
- Division or remainder by zero, and signed `MIN / -1` / `MIN % -1`, are compile-time errors.
- A negative constant shift count is a compile-time error (`expr.shift.negative`), for the unsafe forms
  too.
- `sizeof` / `alignof` are typed `uint` constants and `len` of an array a typed `int` constant (§15),
  so arithmetic on them wraps at the target's width.
- A comparison of constants is a constant `bool` (by value; typed values are already at their type).
- `cast(T, x)` of a constant is a constant of type `T`; `x`'s value (at its own type if typed) must fit
  `T` (`conv.cast.const-not-laundered`, unchanged).  `bit_cast` reinterprets (`types.ReinterpretBitCast`).

Examples for the spec: `cast(uint8, 200) + cast(uint8, 100)` is 44; `~cast(uint8, 1)` is 254;
`-cast(int8, -128)` is -128; `'a' - 'b'` is 255 (`char` is `uint8`); `cast(int8, cast(uint8, 200) + cast(uint8,
100))` is 44 (valid); `cast(int8, cast(uint8, 200))` is an error.

**Decided (user, 2026-09-27):** a constant `unsafe_shl` / `unsafe_shr` with a TYPED value and a count
≥ its width is a compile-time error ("I guess it can be an error, given that it's undefined at
runtime"); so is a constant `unsafe_div` / `unsafe_rem` by zero or `MIN / -1`, as for `/` and `%`.

## Design

New package `pkg/binate/constval` (in the BUILDER-compiled surface; imports `bignum`, `types`, `ast`,
`token`), a pure evaluator used by both the checker and IR-gen:

- `Value` — an integer (`bignum.Num`) or bool, plus its type (`nil` = untyped).
- `Env` interface — `Const(e @ast.Expr) (Value, int)` for an identifier / selector (with a
  "not known yet" status for pass-1 forward references); `Type(te @ast.TypeExpr) @types.Type` for
  `sizeof` / `alignof` / cast targets; `Iota() (int, bool)`.
- `Eval(env, e) (Value, Status)` — `Status` is OK, not-a-constant, not-known-yet, or an error kind with
  the offending sub-expression (overflow, divide by zero, MIN / -1, negative shift count, cast does not
  fit, operand does not fit its peer's type).
- Operator primitives (`Binary(op, a, b)`, `Unary(op, a)`, `Wrap(n, t)`) that checkExpr's untyped fold
  also calls, so the untyped results agree by construction.

Consumers: the checker's `Env` resolves through its scopes and records values on symbols
(`ConstVal` becomes exact); every checker consumer calls `Eval` and reports its error.  IR-gen's `Env`
resolves through `Module.Consts` and the current instantiation; since the checker has already rejected
every error, an IR-gen `Eval` failure is an internal error (loud), never a silent fallback value.
`evalConstIntValue`, `foldConstNum`, `foldConstMagSign`, `foldConstIntValue`, `evalConstExpr`,
`evalConstBool`, and the host-`int` fallbacks are deleted.  A constant's stored value (`ModuleConst.Val`,
`Symbol.ConstVal`) stays int64-exact as today (every value at a type fits its 64-bit pattern).

## Integration requirements (from the review of step 1, 2026-09-27)

- **IR-gen must store untyped constants exactly.**  `ModuleConst.Val int64` with `Typ nil` cannot tell
  an untyped 2^64-1 from -1, so IR-gen's Env would fold `const BIG = 0xFFFFFFFFFFFFFFFF; const ( X
  uint64 = BIG >> (60 + iota); Y )` (or the same in an imported `.bni`) wrongly.  Store a `bignum.Num`
  (or a sign bit) for an untyped constant.
- **Per-instantiation errors are user errors.**  A dependent constant (`sizeof(T)`) is evaluated per
  instantiation, where `cast(uint8, sizeof(T) * 100)` or `[sizeof(T) - 8]int` can fail for one T and not
  another.  The checker's Env answers DEPENDENT for them; IR-gen must report an instantiation's `Eval`
  error as a diagnostic at the use site (naming the instantiation), not as an internal error.  Every
  other IR-gen `Eval` failure is an internal error.
- **Checker Env statuses:** NOT_KNOWN for a forward constant or a type whose layout is not complete yet
  (pass 1, replacing `dimFullyKnown`); POISONED for a constant whose own initializer reported an error
  (no cascade); DEPENDENT for anything involving a type parameter (never `SizeOf` a type parameter).
- **`len` of an array / string literal** is a constant (spec §15 `builtin.len`); no evaluator folded it
  before.  Both Envs implement `Len`.
- **Known limitation:** `types`' layout computes sizes in a host `int`, so on a 32-bit host a type of
  2^31 bytes or more has a wrapped size.  constval fails loudly (panic) on a negative size rather than
  folding it; the layout itself is out of this plan's scope.

## Step 2 design (2026-09-27, after reading the consumers)

**constval: one-node step.**  Split `Eval` into `Operands(e)` (the child expressions whose values an
operation needs: `X`/`Y`, a cast's or an unsafe builtin's arguments; none for literals, names, sizeof,
`len`) and `Step(env, e, vals, stats)` (the node's result from its operands' values and statuses; the
first non-OK operand status propagates).  `Eval` = recurse over `Operands`, then `Step`.

**Checker: record a value per checked node.**  `checkExpr` already types bottom-up and registers each
node (`registerExprType`); right after, `recordConst(c, e)` calls `constval.Step` with the children's
recorded values and stores `(Value, status)` in a side table parallel to `ExprTypes`.  O(n), and the
dispatch is constval's, so the checker and `Eval` cannot disagree.  Errors are reported where they
arise, once: `recordConst` reports every ERR_* status at its node and records POISONED, so ancestors
stay quiet.  The untyped folds (`foldIntArith` / `foldIntBitwise` / unary `~`) keep computing the
HasLitVal typing stamps (through constval's primitives) but stop reporting; `checkCastConstFits` and
its `foldConstNum` go (a cast node's ERR_CAST_FIT is reported by `recordConst`); a typed operand-fit
failure is a type error already reported by the typing, so `recordConst` records POISONED silently for
ERR_OPERAND_FIT; `checkShiftCountNonNegative` keeps reporting a negative constant count only when the
shift is not itself a constant (else `recordConst` reports it).
- Symbols carry `constval.Value` + status (replacing `ConstVal` / `HasConstVal` / `SymBoolVal`), set by
  const declarations from the initializer's recorded value (converted to the declared type; an untyped
  value must fit it).  The checker's Env resolves names through the scope from these.
- Array dimensions (not `checkExpr`'d; pass 1) and `.bni` constants (not `checkExpr`'d) use
  `constval.Eval` with the checker's Env; NOT_KNOWN defers exactly as `dimFullyKnown` does today.
- Const groups: a bare member re-evaluates the previous initializer at its own iota with `Eval` (no
  re-`checkExpr` of the shared node, so the stamp save/restore workaround goes away).
- Deleted: `evalConstIntValue`, `evalConstInt`'s host fold, `foldConstNum`, `foldConstMagSign`,
  `foldConstIntValue`, `foldConstBoolValue`, `constIntFor`, `litIntValue`.
- IR-gen stamps (`attachConstLitVal` / `LenVal`) are written from the recorded values.

**IR-gen.**  An Env over `Module.Consts` (a `ModuleConst` holds a `constval.Value`, exact for untyped
constants) and the current instantiation's type resolution.  `evalConstExpr` / `evalConstBool` are
replaced by `constval.Eval`; a failure is an internal error except an ERR_* in a per-instantiation
(DEPENDENT-in-the-checker) evaluation, which is a user diagnostic.  Call sites: `genConst` /
`genConstGroup`, import const registration, `gen_type_resolve` array lengths, `gen_flow` case values,
`gen_composite` keys, the REPL.

**Landing.**  Develop as separate commits (constval step, checker, IR-gen, spec + tests), land together
(squashed or as a series in one round), since either half alone leaves the checker and IR-gen folding
typed constants differently.

## Per-instantiation checking (decided 2026-09-28)

The step-2 review found that a value depending on a type parameter can only be checked per
instantiation, and the switch handled that in IR-gen (a panic with no position).  User decisions:
- Arrays whose length depends on a type parameter get the correct fix, not an interim rejection ("We should
  do the correct fix"): generic signatures (`func F[T any](a [sizeof(T)]uint8)` called as `F[int64](x)`)
  and array literals (`[sizeof(T)]uint8{1, 2, 3}`) are checked with the length each instantiation has.
- An error that only one instantiation has (`cast(uint8, sizeof(T) * 100)` with a large T) must be caught
  by the type checker, not IR-gen ("I think we should reconsider how that's checked. Doing it in IR-gen is
  too late and the error must be caught earlier.").
So the checker must evaluate every DEPENDENT constant, and check what depends on it, for each
instantiation, with the instantiation's type arguments, reporting at the instantiation (with the generic
body's position).

**Chosen design (user, 2026-09-28: "B"): re-check each generic body per concrete instantiation.**  Mapped by
a design workflow (2026-09-28): the checker checks a generic body once, abstractly, and records no function
instantiations; only IR-gen discovers the concrete set, while emitting.  Alternatives considered: recording
dependent sites and evaluating them per instantiation (cheaper, but only the listed site kinds are checked
and dependent-length identity needs a new rule).  Design B:
- **Clones.**  Everything resolved or checked with type parameters bound works on a per-instantiation clone
  of the generic's AST (`ast` clone functions; the clone resets `ResolvedTypeID`, `ConstID`, `LenKnown`,
  `KeyKnown`, `Captures`), so per-node annotations stay per instantiation; the original generic AST only
  carries binding-independent annotations.  This also removes the shared-AST length stamp bug (claude-todo:
  "A generic struct's `[sizeof(T)]` field has the same length in every instantiation").
- **Instances.**  `FuncInstance { Decl, Args (concrete), Clone, Sig, Recv, Site, Parent, Depth, Checked }`,
  deduplicated by (decl, identical args); recorded at the instantiation funnels (`instantiateGenericFunc`
  for calls / `&F[T]` / defer / method values; user-facing `instantiateGenericDeclWithArgs` for types,
  whose methods all become instances).  An instantiation with abstract arguments inside a generic body is an
  edge, substituted when that body's instance is checked; a concrete one is a root.
- **Worklist.**  Drained at the end of each package's check (every body reachable from the package's roots
  is in it or a dependency, all already checked), with a depth limit (polymorphic recursion becomes an
  error; claude-todo: "Polymorphic recursion … crashes the compiler").
- **Abstract check.**  A dependent array length is deferred to the instances (placeholder length, no
  identity claims across dependent lengths); dependent literals are checked per instance.
- **Errors.**  Primary position in the generic's source; the message names the instantiation chain; one
  report per failing instance (errors dedupe by position + message).
- **IR-gen** emits the checked clones and reads their annotations like an ordinary function's; its
  DEPENDENT evaluation paths are deleted, and a missing record or failed constant is an internal error.
- **Spec:** a new §12 rule: a generic body is checked for each instantiation the program names (every
  method of each instantiated generic type included); an error only one instantiation has is a compile
  error.
- **Risks:** a clone missing a field (a clone-and-compare test over the corpus); checker time grows with
  instances × body size (measure the gen1 self-compile); the abstract and per-instance checks disagreeing
  (surfaces as errors, to investigate one by one); REPL tentative mode.
- **Commits (draft):** (1) `ast` clone functions + tests; (2) the shared-stamp repro + populate / imported
  method signatures on clones; (3) `FuncInstance` records, signature re-resolution, dependent lengths
  deferred in abstract bodies (fixes dependent signatures); (4) worklist, depth limit, error context, type
  methods (per-instantiation errors); (5) IR-gen emits the clones, DEPENDENT paths deleted; (6) cast /
  `bit_cast` checks move from IR-gen to the instances; (7) spec text.  Detailed design:
  `plan-generic-instance-check.md`.

## Commits

1. `constval` package with unit tests (typed wrap at every width and signedness, the error kinds and
   statuses, untyped exactness shared with part 1).  No consumers yet.  LANDED (binate `b6314e316`,
   2026-09-27).
2. Checker and IR-gen switched to it together (so they cannot disagree in between), the dead evaluators
   deleted, spec `const.expr.typed` + the unsafe negative-count rule, and a spec conformance test for
   every repro above, plus the constant forms of `unsafe_shl` / `unsafe_shr` (a part 1 coverage gap).
   Audit as in part 1: compile the toolchain and the conformance corpus before and after, host and
   arm32, and compare.

If (2) is too large to review as one, split it by consumer family (declarations; array dims and counts;
casts and bools; IR-gen), each keeping checker and IR-gen in step for that family.
