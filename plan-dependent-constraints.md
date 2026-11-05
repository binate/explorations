# Plan: constraints and identities that depend on a type parameter's length

Status: design, 2026-11-04 (temp-5), not reviewed yet.  For the `claude-todo.md` entry "In a generic's own check, a
constraint whose length depends on the generic's type parameter is judged with the placeholder length — valid
programs rejected" (claimed 2026-11-04, temp-5).  The design is the one decided there: the hybrid as the base (user
2026-11-02, "Go with your recs"), with a type-parameter argument decided per instance for the length comparison
only (user 2026-11-03, "(b) sounds fine"); (S) is not part of it.

## 1. The rule

Spec §12.3 `gen.mono.check`: in a generic declaration, array type identity and assignability where a length depends
on a type parameter are dependent constructs, which the declaration's check defers and each instantiation the
program names checks with the type parameters bound.  Whether a type argument satisfies its constraint, whether an
impl's methods cover its interface, and whether an interface value can be built from a type all compare interface
instances by their type arguments, so they too depend on such a length when an interface's type argument holds
one: `*N : Eq[[sizeof(U)]uint8]` holds for `U = int64` (with `impl *N : Eq[[8]uint8]`) and not for `U = int32`.

So: the declaration's check does not reject what only a dependent length makes fail; each instance's check makes
those judgments with the lengths known.  A type-parameter argument is the one case gen.satisfy decides at the
declaration; decided 2026-11-03, it is decided per instance for the length comparison only: the declaration still
rejects a bound that cannot give the constraint for any type arguments (no bound, another interface, a different
element type, two unequal fixed lengths), and where the only difference is a dependent length, each instance
compares its bound with its constraint.

## 2. Today (probed on binate main `b52e54139`)

The declarations: `interface Eq[T any] { eq(o T) int }`, `impl *N : Eq[[8]uint8]`, `func inner[T
Eq[[sizeof(U)]uint8], U any](t T) int`, `type G[T Eq[[sizeof(U)]uint8], U any] struct { t T }`, `type Box[K any]
struct { k K }`.

**(A) Valid programs rejected at the declaration**, each with "type argument … does not satisfy constraint
Eq[[0]uint8]" or "cannot assign … to …[[0]uint8]…":
- a call: `func fwd[U any](n *N) int { return inner[*N, U](n) }` (fwd[int64] is valid);
- a field: `type W[U any] struct { g *G[*N, U] }`;
- a signature: `func fA[U any](k *K2[[sizeof(U)]uint8, *N]) int` with `type K2[A any, B Eq[A]]`;
- a generic interface's method: `interface Hh[U any] { take(k *G[*N, U]) int }`;
- a generic alias's target: `type GA[U any] = G[*N, U]`;
- a type-parameter argument: `func fwd2[V Eq[[8]uint8]](v V) int { return inner[V, int64](v) }` and `func f6[V
  Eq[[8]uint8], U any](v V) int { return inner[V, U](v) }` (f6[*N, int64] valid, f6[*N, int32] not);
- an assignment through an instance: `func f1[U any](q *G1[[8]uint8]) int { var p *G1[[sizeof(U)]uint8] = q; … }`
  (`assignableDeferringDependent` does not look into an instance's type arguments);
- an interface value: `func cv[U any](n *N) int { var e *Eq[[sizeof(U)]uint8] = n; … }`;
- an impl's coverage: `impl *Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [8]uint8) int` ("type Box
  does not implement Eq[[0]uint8] (method `eq` has wrong signature)"; valid for Box[int64]).

**(B) Invalid programs accepted**, because an instance's declared types are not judged:
- `func sigb[V Eq[[sizeof(U)]uint8], U any, X any](g *G[V, X]) int` with `sigb[*N, int64, int32](nil)` compiles
  and runs, though `G[*N, int32]` needs `*N : Eq[[4]uint8]`.  An instance's signature is resolved under
  `DerivingInstanceSig`, which makes every instantiation it forms not user-facing, so no constraint is judged
  (`instantiateGenericDeclWithArgs`).  The same holds for a generic struct's method signatures, a generic function's
  own constraints (`instanceConstraint`) and the result `resolveInstanceResult` resolves.
- `impl *Box[K] : Eq[[len([...]uint8{cast(uint8, sizeof(K) * 40)})]uint8]` with `var e *Eq[[1]uint8] = &b` (b a
  Box[int64]) compiles and runs, though 320 does not fit uint8: the impl's interface list is substituted per instance
  (`checkInstanceImpls`, `genericImplSatisfies*`), never resolved for it.
- Building an instance judges its fields' constraints or not by where it is first built: `type W2[U any] struct { g
  *G[*M, U] }` (`*M` has no impl) is reported at W2 (abstract), then again at the same position for `var w
  W2[int64]` ("… Eq[[8]uint8]", with no instance named), and not at all for W2[int32] built first inside `func h[U
  any](w *W2[U])`'s instance signature; `func k[U any](g *G[*M, U])` is reported once, at k, and k[int16] adds
  nothing.

**(C) Valid programs rejected in an instance**, because the impl's interface list is substituted with the
placeholder: `impl *Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [sizeof(K)]uint8) int` fails as soon
as Box[int64] is named ("type Box[int64] does not implement Eq[[0]uint8] … (in the impl for Box[int64], …)"), so do
`var e *Eq[[16]uint8] = &b` under `impl *Box[K] : Eq[[2 * sizeof(K)]uint8]` and `func g4[U any](b *Box[U]) int {
return inner[*Box[U], int64](b) }` at g4[int64].

**(D) Outside this plan:**
- `interface I[T any] : J[[sizeof(T) + 1]uint8] { m() int }` with `interface J[T any] : I[T] {}`, never
  instantiated, is "interface extension cycle … via `J[[0]uint8]`": whether a chain that grows without bound is valid
  is a spec question of its own (the entry's review of (S)), to raise separately.
- A generic with no declaration-time check (an interface-only package: bni's injected packages, bnlint's
  dependencies) has no instance checks either (`checkFuncInstance` needs `home.Checked`), so none of its dependent
  constructs is checked per instance today; that holds for what this plan defers too.  To raise as its own entry if
  it is not tracked.

## 3. Design

### 3.1 An instance's check judges the generic's declared types

Today an instance's check (check_instance_body.bn) checks the generic's body for the instance, so a call or a type
written in the body is judged with the lengths known.  The types the generic declares -- a function's signature and
its type parameters' constraints, a struct's fields and its methods' signatures, a parameterized impl's receiver and
interface list, an interface's method signatures and parents -- are resolved per instance from source (with the
type parameters bound), but no constraint of an instantiation they form is judged then.  This plan makes the
instance's check judge them, as it judges the body:

- **A function instance** (`checkInstanceBody` for a generic function): the signature it resolves from the clone
  (`instanceFuncType`) and each type parameter's constraint (as `instanceConstraint` resolves it) are resolved with
  the instance judgment on (below).
- **A method of a struct instance**: the same, per method (`checkInstanceBody` from `checkTypeInstance`).
- **A struct instance's fields** (`checkTypeInstance`): resolved again from a clone of the declaration's underlying
  type, with the judgment on, and the result discarded (the instance's type was built when it was named).
- **A parameterized impl** (`checkInstanceImpls`): its receiver and interface list resolved from a clone of the
  impl's type expressions with the binders bound to the instance's arguments (3.3), with the judgment on, and the
  coverage checked against that list.
- **An interface instance**: it has no check today (`queueStructInstance` queues none).  A concrete interface
  instance gets one, as a struct instance does (an `InstCheckState`, queued when named, a lender class per naming),
  which resolves its methods' signatures and parents again from a clone with the judgment on.
- **An alias** is resolved at each use, as if its target were written there (`instantiateGenericAlias`); a use
  inside a generic's declared types is judged with them.

The instance judgment is a checker flag (`Generics.JudgingInstance`, set by the instance's check around those
resolutions) under which `instantiateGenericDeclWithArgs` judges a concrete instantiation's constraints although
`DerivingInstanceSig` is set (it stays set: its other effects, postponing and the build depth, are unchanged).  Its
errors are the instance's (`contextualizeInstanceErrors`: "(in sigb[*N, int64, int32], instantiated at …)").

**Gated on the declaration's check**, as the body check is (`home.Checked && !home.BodyErrs`), so a miss the
declaration reported is not reported again for each instance: a function's signature errors (reported when its
signature is collected) are recorded on its `GenericHome` too (`SigErrs`, new), a generic type's from its abstract
population (`checkGenericDeclAbstract`, a new `GenericTypeErrs` record), a parameterized impl's as today
(`Impl.AbstractErrs`), an interface's from its abstract population.

**Building an instance judges nothing.**  An instantiation formed while an instance is built (bound, its arguments
not the generic's own parameters: `bindInstance`) is not user-facing for its constraints; the instance's check
judges it.  This removes the build-time judgment of (B)'s W2 case, which reported a miss twice or never.

**The view** of an instance judgment is the declaration's, by argument:
- an argument that is a bare type parameter of the enclosing generic (`inner[V, U]`'s `V`, a field's `G[V, X]`'s
  `V`, read from the AST: a TEXPR_NAMED / EXPR_IDENT naming one of the enclosing declaration's type parameters or a
  method's receiver binders) is judged by the bound rule (`boundSatisfiesConstraint`), its bound (that parameter's
  constraint) and the constraint both resolved for the instance -- decision (b);
- any other argument (written in the declaration, `*N`, or built from type parameters, `*Box[U]`) is judged through
  the impls visible where the generic is declared (its package; the `.bni` when it is declared there), not
  `ImplView.All`, which instance checks use today and which would let a private impl the declaration cannot see
  decide a judgment the declaration deferred.

### 3.2 The declaration's check does not reject what only a dependent length makes fail

Each comparison below is made up to dependent lengths -- a length that depends on a type parameter matches any
length, in an array, an anonymous struct, a function type, and an instance's type arguments (a named instance or an
interface instance: same generic, arguments pairwise so).  Where it holds only that way, the declaration's check
reports nothing and leaves the judgment to the instances (3.1):
- a type argument's constraint, through a non-parameterized impl (`typeSatisfiesConstraint`: `implCoversInterface`,
  `isDescendantInterface`) -- fwd, W, fA, Hh, GA;
- through a parameterized impl (`genericImplSatisfiesConstraint`) -- g4;
- a type-parameter argument's bound (`boundSatisfiesConstraint`) -- fwd2, f6 (decision (b): still rejected when
  there is no bound, another interface, another element type, or two unequal fixed lengths);
- assignability (`assignableDeferringDependent`, `identicalUpToDependentLengths` extended to instance type
  arguments) -- f1;
- an interface value's construction (`valuePtrSatisfies` and its managed counterpart, through
  `implSatisfiesInterface`) -- cv;
- a parameterized impl's coverage of its interface (`methodSigSatisfies` from `checkImplCoverage`) -- q3.

A comparison of two dependent lengths stays "identical" at the declaration (types.Identical compares them as equal
placeholders): every instance makes it again (3.1).  The review should check this list for comparisons it misses
(interface method conflicts between parents whose type arguments hold a dependent length, for one).

### 3.3 An instance's impl interface list is resolved from source

`checkInstanceImpls` and `genericImplSatisfies` / `genericImplSatisfiesConstraint` with concrete arguments
substitute the arguments into the impl's recorded receiver and interfaces, which keeps a dependent length's
placeholder.  Instead, the impl's interface list is resolved per (impl, arguments) from a clone of its type
expressions, in the impl's declaration scope with its binders bound (as `checkInstantiationConstraints` resolves a
constraint), and kept on the impl (`Impl.InstanceIfaces`, keyed by the arguments).  `genericImplSatisfies*` reads it
with the resolution's errors dropped; the instance's impl check (3.1) resolves it with them reported.  With
arguments that mention a type parameter, substitution stays (the declaration's check, 3.2).

### 3.4 The instance cache

Two abstract instances whose arguments differ only in dependent lengths are one cache entry (`[sizeof(U)]uint8` and
`[2 * sizeof(X)]uint8` are identical placeholders), so fB's `K2[[2 * sizeof(X)]uint8, *N]` reuses fA's entry and is
never judged at fB.  Under 3.1 and 3.2 this is harmless: neither is judged at the declaration, and fB[int64]'s
check resolves `K2[[16]uint8, *N]` from source and judges it.

## 4. Steps

Each step lands alone and keeps the tree green; the declaration relaxations (step 5) come last, since without the
instance judgments they would accept invalid programs.

1. **Impl interface lists from source** (3.3): fixes (C) and the uint8-overflow case of (B).
2. **Function instances judge their signatures and constraints** (3.1, with `SigErrs`): fixes sigb and k[int16].
3. **Struct instances judge their fields; building an instance judges nothing** (3.1, with the type record): fixes
   W2.  Parameterized impls are judged in step 1's resolution under the instance's check.
4. **Interface instances get a check** (3.1).
5. **The declaration's check defers what only a dependent length fails** (3.2), one comparison per commit if the
   diff is large: fixes (A).
6. **Spec** (section 5) with the last step, or before it as a docs change.

The view change for instance judgments (3.1) goes with step 2, where instance judgments of declared types begin; the
body check's calls switch to it there too.

## 5. Spec

`gen.mono.check`: add to the dependent constructs "whether a type argument satisfies its constraint, whether an
impl's methods cover its interface, and whether an interface value can be built from a type, where an interface's
type argument holds such a length", and to the "not re-established" bullet: "except that where a type parameter's
bound differs from the constraint only in a length that depends on a type parameter, each instantiation compares
them with that length known".  `gen.satisfy`'s type-parameter bullet points there.

## 6. Tests

Conformance (spec/12-generics): each (A) case as a valid program printing its result for the valid instance, with an
`_err_` companion for the invalid instance (f6[*N, int32], fwd[int32], W[int32], fA with fB[int64], Hh[int32],
GA[int32], f1[int32], cv[int32], Box[int32] for q3) whose error names the instance; (B)'s sigb and the uint8 overflow
as `_err_` tests; (C)'s three as valid programs.  Unit tests in check for the instance-judgment gating (no second
report when the declaration reported a miss) and for W2's single report.

## 7. For the review

- Is 3.1's list of declared types complete?  Anything else resolved per instance with `DerivingInstanceSig` (the
  backfill path, check_generic_backfill.bn:178; `resolveInstanceResult`)?
- Is the gating sound: can an instance check run before the declaration's check has run (a generic named before
  its declaration is checked, in a `.bni`, at the REPL)?
- The REPL: a new interface-instance check needs the rollback the struct instances have (`markInstCaches`,
  `discardInstWork`).
- Performance: struct instances now re-resolve their fields in their checks.
