# Plan: constraints and identities that depend on a type parameter's length

Status: design v2, 2026-11-04 (temp-5).  v1 (explorations `49f1c1cce`) had a focused adversarial review the same day
(section 8); v2 answers it and is not reviewed yet.  For the `claude-todo.md` entry "In a generic's own check, a
constraint whose length depends on the generic's type parameter is judged with the placeholder length — valid
programs rejected" (claimed 2026-11-04, temp-5).  The design is the one decided there: the hybrid as the base (user
2026-11-02, "Go with your recs"), with a type-parameter argument decided per instance for the length comparison
only (user 2026-11-03, "(b) sounds fine"); (S) is not part of it.

## 1. The rule

Spec §12.3 `gen.mono.check`: in a generic declaration, array type identity and assignability where a length depends
on a type parameter are dependent constructs, which the declaration's check defers and each instantiation the
program names checks with the type parameters bound.  Whether a type argument satisfies its constraint, whether an
impl's methods cover its interface, whether an interface value can be built from a type, and whether two parent
interfaces are the same all compare interface instances by their type arguments, so they too depend on such a length
when a type argument holds one: `*N : Eq[[sizeof(U)]uint8]` holds for `U = int64` (with `impl *N : Eq[[8]uint8]`)
and not for `U = int32`.

So the declaration's check does not decide what a dependent length decides; each instance's check decides it with
the lengths known.  A type-parameter argument is the case gen.satisfy decides at the declaration; decided
2026-11-03, it is decided per instance for the length comparison only: the declaration still rejects a bound that
cannot give the constraint for any type arguments (no bound, another interface, a different element type, two
unequal fixed lengths), and where the bound and the constraint differ only in a dependent length, each instance
compares them with that length known.

## 2. Today (probed on binate main `b52e54139`)

The declarations: `interface Eq[T any] { eq(o T) int }`, `impl *N : Eq[[8]uint8]`, `func inner[T
Eq[[sizeof(U)]uint8], U any](t T) int`, `type G[T Eq[[sizeof(U)]uint8], U any] struct { t T }`, `type Box[K any]
struct { k K }`, `type G1[A any] struct { a A }`.

**(A) Valid programs rejected at the declaration** ("… does not satisfy constraint Eq[[0]uint8]", "cannot assign …"):
- a call, `func fwd[U any](n *N) int { return inner[*N, U](n) }`; a field, `type W[U any] struct { g *G[*N, U] }`; a
  signature, `func fA[U any](k *K2[[sizeof(U)]uint8, *N]) int` with `type K2[A any, B Eq[A]]`; a generic
  interface's method, `interface Hh[U any] { take(k *G[*N, U]) int }`; an alias's target, `type GA[U any] = G[*N,
  U]`; a call in a field's array length, `type W9[U any] struct { a [len(mk2[*N, U]())]uint8 }`;
- a type-parameter argument: `func fwd2[V Eq[[8]uint8]](v V) int { return inner[V, int64](v) }`, `func f6[V
  Eq[[8]uint8], U any](v V) int { return inner[V, U](v) }`;
- identity and assignability through an instance's arguments: `var p *G1[[sizeof(U)]uint8] = q` (q a
  `*G1[[8]uint8]`), and `p == q`;
- `same(p, q)`, even on `*([sizeof(U)]uint8)` and `*([8]uint8)` (check_builtin.bn compares with Identical);
- interface values: `var e *Eq[[sizeof(U)]uint8] = n` (n a `*N`); an upcast `var e *Eq[[8]uint8] = j` from `j
  *J[U]` with `interface J[U any] : Eq[[sizeof(U)]uint8]` (isDescendantHere); the decay `var r *Eq[[8]uint8] = m`
  from `m @Eq[[sizeof(U)]uint8]` (sameInterface);
- an impl's coverage: `impl *Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [8]uint8) int`;
- duplicate parents: `interface KP2[U any, X any] : Tag[[sizeof(U)]uint8], Tag[[sizeof(X)]uint8] {}` is "duplicate
  parent interface `Tag[[0]uint8]`" -- two dependent lengths are one placeholder, so the two parents are one cached
  instance (`containsTypePtr`, check_iface_extends.bn).

**(B) Invalid programs accepted:**
- `func sigb[V Eq[[sizeof(U)]uint8], U any, X any](g *G[V, X]) int` with `sigb[*N, int64, int32](nil)` runs,
  though G[*N, int32] needs `*N : Eq[[4]uint8]`: at the declaration V's bound and G's constraint are equal
  placeholders, and an instance's signature is resolved under `DerivingInstanceSig`, which judges no constraint.
- `impl *Box[K] : Eq[[len([...]uint8{cast(uint8, sizeof(K) * 40)})]uint8]` with `var e *Eq[[1]uint8] = &b` (b a
  Box[int64]) runs, though 320 does not fit uint8: an impl's interface list is substituted per instance
  (`checkInstanceImpls`, `genericImplSatisfies*`), never resolved for it.

**(C) Valid programs rejected in an instance**, the impl's interface list substituted with the placeholder: `impl
*Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [sizeof(K)]uint8) int` once Box[int64] is named; `var e
*Eq[[16]uint8] = &b` under `impl *Box[K] : Eq[[2 * sizeof(K)]uint8]`; `func g4[U any](b *Box[U]) int { return
inner[*Box[U], int64](b) }` at g4[int64].

**(D) Outside this plan:**
- `interface I[T any] : J[[sizeof(T) + 1]uint8] { m() int }` with `interface J[T any] : I[T] {}`, never
  instantiated, is "interface extension cycle … via `J[[0]uint8]`": whether a chain that grows without bound is
  valid is a spec question of its own.
- A generic with no declaration check (an interface-only package: bni's injected packages, bnlint's dependencies)
  has no instance checks (`checkFuncInstance` needs `home.Checked`), so its dependent constructs are not checked per
  instance.  Nothing is deferred there either (only the declaration's check defers), so this plan changes nothing
  for it.
- Building an instance judges its fields' constraints again when it is built outside a signature's derivation:
  `type W2[U any] struct { g *G[*M, U] }` (`*M` has no impl) is reported at W2 and again, at the same position with
  no instance named, for `var w W2[int64]`.  A duplicate of a miss the declaration reports; separate.
- A constraint that names a later type parameter is judged without that parameter's bound in a type, an interface or
  an alias (todo entry filed 2026-11-04, explorations `bc7c017fd`).  The bound rule (3.3) needs those bounds, so
  that entry comes first.

## 3. Design

### 3.1 The declaration's check defers what a dependent length decides, and marks it

A judgment that holds when every length that depends on a type parameter is taken to match any length, and that
compared such a length (on either side), is **deferred**: the declaration's check reports nothing for it.  One that
fails even so is rejected, as today; one that compared no such length is decided, as today.  Two dependent lengths
count as compared, though types.Identical takes them for equal placeholders: `sigb`'s `[sizeof(U)]` against
`[sizeof(X)]` is deferred, not accepted.

The deferred judgments (with section 2's cases):
- constraint satisfaction, by each of its three paths: a non-parameterized impl (`typeSatisfiesConstraint`:
  `implCoversInterface`, `isDescendantInterface`), a parameterized impl (`genericImplSatisfiesConstraint`), and a
  type-parameter argument's bound (`boundSatisfiesConstraint`, decision (b));
- identity and assignability (`assignableDeferringDependent`, with `identicalUpToDependentLengths` extended through
  the type arguments of a named instance and of an interface instance: same generic, arguments pairwise), `==`, and
  `same`;
- interface values: construction (`valuePtrSatisfies` and its managed counterpart, `implSatisfiesInterface`), an
  upcast (`isDescendantHere`), the managed-to-raw decay (`sameInterface`);
- a parameterized impl's coverage of an interface (`methodSigSatisfies` from `checkImplCoverage`);
- duplicate parents in an interface's extension list, and, to check, duplicate interfaces in an impl's list.

A deferred judgment in a function's body needs nothing more: each instance's body check makes every judgment of the
body again (check_instance_body.bn).  A deferred judgment anywhere else -- a function's signature or its type
parameters' constraints, a struct's fields, a method's signature, an impl's receiver and interface list, an
interface's methods and parents, an alias's target -- is **marked** on the AST node it was made for, so that the
instances judge it (3.2): the generic instantiation (TEXPR_INSTANTIATE), the generic call (`mk2[*N, U]()` in a
length), or the declaration itself (an impl's coverage, an interface's parents).  A generic call or instantiation in
a body is marked too, for its type-parameter arguments (3.3).

A mark is checker output that a clone **keeps**, as `Expr.ViaConstraint` is kept (ast.bni; cleared by
`ClearCheckerAnnotations` like the rest): an instance's check runs on a clone made after the declaration's check
(the instance stack is drained after `checkDecls`; a `.bni` generic's instances are checked from its importers), so
the clone carries the marks.  A mark lists, for each deferred type argument, whether the declaration resolved it to
a type parameter of the declaration (and which), read from the argument's resolved type, not its syntax: `Id[V]`
with `type Id[T any] = T`, and `(V)`, are V.  The parts the declaration's check resolves from a copy (an alias's
target, an interface's parents and methods, a constraint resolved from a clone) have their marks copied back to the
declaration, as `stampFromAbstractCopy` copies stamps.

**The instantiation cache.**  Abstract instances whose arguments differ only in dependent lengths are one entry
(fA's `K2[[sizeof(U)]uint8, *N]` and fB's `K2[[2 * sizeof(X)]uint8, *N]`), whose constraints are judged once per
view (`constraintsCheckedIn`).  The latch keeps the judgment's deferred outcome, and each later use of the entry in
that view gets the same mark, so fB is judged at fB[int64].  Pending judgments made while declarations are collected
(`recordPendingConstraintCheck`, `deferFuncConstraintCheck`, replayed by `recheckDeferredConstraints`) record the node
to mark.

### 3.2 Each instance's check judges what its declaration marked

An instance's check resolves the generic's declared types again from its clone, with the type parameters bound, and
judges each marked node it reaches -- only the marked ones: an unmarked judgment was decided at the declaration
(accepted, or reported there), so judging it again would only repeat the declaration's report.  This needs no record
of whether the declaration had errors.  Where the instance's check resolves the declared types:
- a function instance (`checkInstanceBody`): its signature (`instanceFuncType`, already) and its type parameters'
  constraints (new), before its `d.Body == nil` return;
- a method of a struct instance: the same, per method;
- a struct instance (`checkTypeInstance`): its fields, from a clone of the declaration's underlying type (new; the
  instance's type was built when it was named, and the result is discarded), and its parameterized impls
  (`checkInstanceImpls`, from source: 3.4);
- an interface instance: it has no check today (`queueStructInstance` queues none).  A concrete interface instance
  gets one, as a struct instance has (an `InstCheckState`, queued when named, per lender class), which resolves its
  methods and parents from a clone and checks its parents for duplicates;
- an alias: its target is resolved at each use as if written there (`instantiateGenericAlias`), and judged there in
  full, as today.  A use inside a generic's declared types whose target had a deferred judgment is marked; the
  instance's check resolves the use again, and the target is judged as at any use.

A marked judgment's errors are the instance's (`contextualizeInstanceErrors`: "(in sigb[*N, int64, int32],
instantiated at …)").

**Building an instance** judges as today, except that it skips a marked node, whose judgment is its instance check's.
An instance built before its generic's declaration check (a forward reference) has a clone without marks and judges
in full; its instance check then judges the marked nodes again, so an invalid program can get a deferred judgment's
miss twice.  That needs a forward reference and an invalid program; to state, not to design around.

### 3.3 How an instance judges a deferred argument

- An argument the declaration resolved to a type parameter of the declaration (the mark says which) is judged by
  the bound rule (`boundSatisfiesConstraint`): that parameter's bound against the constraint, both resolved for the
  instance -- decision (b).  A method's or an impl's binder takes its bound from the type's declaration, by
  position.  A constraint may name a later parameter (gen.typeparams.scope), so the bounds of the whole list are
  resolved before any is judged (the 2026-11-04 entry, section 2 (D)).
- Any other argument (`*N`, written in the declaration; `*Box[U]`, built from type parameters) is judged through the
  impls visible where the generic is declared (its package; its `.bni` when it is declared there), not
  `ImplView.All`, which instance checks use today and which would let a private impl the declaration cannot see
  decide what the declaration deferred.  Unmarked judgments in a body keep `ImplView.All`.
- A bound-rule judgment does not touch the per-view latch (`constraintsCheckedIn`), which is for judgments through
  impls: the two rules on one (generic, arguments) must not share a latch.

### 3.4 An instance's impl interface lists are resolved from source

`checkInstanceImpls` and `genericImplSatisfies` / `genericImplSatisfiesConstraint` with concrete arguments substitute
the arguments into the impl's recorded receiver and interfaces, which keeps a dependent length's placeholder.
Instead, a struct instance's build resolves each of its generic's parameterized impls' interface lists for its
arguments, after its fields, in the same context (its lender class, its layout-wait frame), from a clone of the
impl's type expressions in the impl's declaration scope with the binders bound, and keeps them on the instance's
cache entry (`GenericInstantiation`), its errors dropped.  `Impl` records the impl's declaration and scope for it
(the `.bni`'s scope for an impl declared there), as `GenericHome` records a function's.  An impl collected after
the instance was built is resolved for it as a late method is copied in (`backfillInstantiationMethods`); a postponed
instance resolves them when it is built.  `genericImplSatisfies*` reads the entry's lists; `checkInstanceImpls`
resolves them again with their errors reported (the instance's: "(in the impl for Box[int64], …)") and checks the
coverage against them.  With arguments that mention a type parameter, substitution stays (the declaration's check,
3.1).  At the REPL the lists go with the entry when it is dropped.

### 3.5 The REPL

Marks are checker output, cleared on a retry (`ClearCheckerAnnotations`).  A latch's deferred outcome is set only
where its latch is (not under `TentativeMode`), and a tentative drain's latches (`drainTentativeInstances` clears
`TentativeMode`) need the same rollback as the instance caches (`markInstCaches` / `dropInstCaches`) -- to check, as
it may already be a gap for today's latches.  Interface instances' new check states are journaled as struct
instances' are.

## 4. Steps

A judgment may be deferred at the declaration only where its instances judge it, so each step adds the instance
judgment for a part of the declaration together with the deferral there; the declaration keeps rejecting elsewhere.
Each step lands alone and keeps the tree green.

1. **Impl interface lists from source** (3.4): fixes (C) and the uint8 overflow of (B).
2. **The later-parameter bound** (the 2026-11-04 entry), which the bound rule needs.
3. **The marks, the latch and the bound rule, with function bodies and signatures**: deferral (3.1) in a function's
   body, signature and constraints and in a method's; the instance judgment (3.2, 3.3) in function and method
   instances.  Fixes fwd, fwd2, f6, fA with fB, sigb.  Identity, assignability, `same`, `==` and the interface-value
   comparisons need only the body's re-check; with this step or one of their own.
4. **Struct fields**: deferral in a generic type's underlying type; the field judgment in `checkTypeInstance`.  Fixes
   W, W9.
5. **Interfaces**: the interface instance check; deferral in a generic interface's methods and parents, duplicate
   parents included.  Fixes Hh, KP2.
6. **Impls**: deferral of a parameterized impl's coverage and of judgments in its receiver and interface list; their
   judgment in `checkInstanceImpls` (step 1's resolution).  Fixes the fixed-length `eq(o [8]uint8)` impl.
7. **Aliases**: deferral in an alias's target; the use's mark.  Fixes GA.
8. **Spec** (section 5), as a docs change with step 3.

## 5. Spec

`gen.mono.check`: add to the dependent constructs "whether a type argument satisfies its constraint, whether an
impl's methods cover its interface, whether an interface value can be built from a type, and whether two interfaces
in a list are the same, where an interface's type argument holds such a length".  Its "not re-established" bullet
gets the exception for both kinds of argument: "except that a judgment that depends on a length that depends on a
type parameter is made for each instantiation: for a type parameter, its bound against the constraint, with the
length known; for another type built from the declaration's type parameters, through the impls visible where the
generic is declared".  `gen.satisfy`'s type-parameter bullet points there.

## 6. Tests

Conformance (spec/12-generics), with each step: each (A) case as a valid program printing its result for the valid
instance, with an `_err_` companion naming the instance for an invalid one (f6[*N, int32], fwd[int32], W[int32],
fB[int64] with fA[int64], Hh[int32], GA[int32], KP2[int64, int64], the fixed-length impl at Box[int32]); (B)'s two as
`_err_` tests; (C)'s three as valid programs; a bound-rule case where the instance's type has the constraint's impl
but its bound is not the constraint (rejected); `Id[V]` and `(V)` arguments across packages (accepted, as today).
Unit tests in check for: no second report of a miss the declaration reported; the latch replaying a mark (fA / fB);
marks surviving a clone and cleared by `ClearCheckerAnnotations`.

## 7. For the next review

- Is 3.1's list of deferred comparisons complete now?  Is "compared a dependent length" decidable in each (an
  identity walk that reports whether it wildcarded one)?
- Marks on nodes the declaration's check resolves from throwaway clones (a constraint resolved from
  `ast.CloneTypeExpr` in `checkInstantiationConstraints` / `instanceConstraint`): does every path copy them back?
- The forward-reference duplicate in 3.2: acceptable, or should a build skip judging judgments that may be deferred?

## 8. The review of v1 (2026-11-04) and what v2 does

1. MAJOR: v1's "building an instance judges nothing" covered alias targets, which are judged only at their uses, so
   an alias's invalid instance would be accepted.  v2 keeps builds judging (3.2) and judges an alias's target at each
   use, as today.
2. MAJOR: the same rule removed the only judgment of an interface instance's methods until its check existed.  v2
   keeps builds judging; interface instances get their check in step 5 with their deferrals.
3. MAJOR: v1 classified an argument as a type parameter by its syntax, so `Id[V]` and `(V)` would be judged through
   the declaration's impls and programs main accepts across packages rejected.  v2 classifies by the resolved type
   at the declaration (3.1, 3.3).
4. Missing comparisons: `same`, `==`, the upcast to a dependent parent, the managed-to-raw decay, a generic call in
   a field's length.  Added (3.1).
5. Two dependent lengths compared as equal can reject (KP2's duplicate parent) as well as accept (sigb).  v2 defers
   any judgment that compared one (3.1).
6. v1's gate on the declaration's errors could not be recorded (pending checks have no owner; abstract populations
   cached early; `AbstractErrs` covers coverage only), giving duplicates.  v2 judges only marked nodes and needs no
   gate (3.2).
7. Builds judging nothing would have dropped judgments for interface-only generics.  v2 keeps builds judging.
8. Constraints resolved for concrete arguments (`checkInstantiationConstraints`) judge the instantiations they form;
   v2 keeps that and marks their deferrals (3.1).
9. `instantiateGenericFunc` judges in builds; v2 has a build skip marked nodes, calls included (3.2).
10. Impl lists from source: v2 resolves them at the instance's build, in its context, keeps them on the instance's
    entry and backfills late impls (3.4).
11. The REPL: section 3.5; the bound rule kept off the latch (3.3).
12. Spec: the exception covers arguments built from type parameters (section 5).
13. Nits: the signature before `d.Body == nil` (3.2); binder bounds from the type (3.3); no `JudgingInstance` flag.
14. A constraint naming a later parameter (r25): filed as its own entry, a prerequisite (step 2).
