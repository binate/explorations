# Plan: constraints and identities that depend on a type parameter's length

Status: design v3 (design C), 2026-11-05 (temp-5).  For the `claude-todo.md` entry "In a generic's own check, a
constraint whose length depends on the generic's type parameter is judged with the placeholder length — valid
programs rejected" (claimed 2026-11-04, temp-5).  Decided 2026-11-04 (user: "C seems ok. Go with your recs, but get
focused reviews of the decisions."): design C, after two reviews of a design that marked each deferred judgment for
the instances (v1, explorations `49f1c1cce`; v2, `2384cd876`; section 9).  The focused review of C (2026-11-04) is
answered here; two of its points wait for the user (section 8).

## 1. The rule

Spec §12.3 `gen.mono.check`: array type identity and assignability where a length depends on a type parameter are
dependent constructs, deferred by the declaration's check to each instantiation.  Whether a type argument satisfies
its constraint, whether an impl's methods cover its interface, whether an interface value can be built from a type,
and whether two parent interfaces are the same compare interface instances by their type arguments, so they depend
on such a length when a type argument holds one: `*N : Eq[[sizeof(U)]uint8]` holds for `U = int64` (with `impl *N :
Eq[[8]uint8]`) and not for `U = int32`.

Design C: the declaration's check does not decide what such a length decides; every concrete instantiation is
judged where it is formed, with the lengths known.  Accepted differences from the spec and from the 2026-11-03
decision (b) (section 8 for the open ones):
- A type-parameter argument whose judgment was deferred is judged per instance through impls, not by comparing its
  bound with the constraint.  They differ only where two instantiations of an interface have the same method set
  (`Tag[T] { tag() int }`, or a method whose types come out the same: `e(o [sizeof(T) / 64 + 1]uint8)`), with a type
  implementing both.
- Inside an instance a deferred judgment sees the impls an instance's check sees (`ImplView.All` today; section 8).

## 2. Today (probed on binate main `b52e54139` unless said)

Declarations: `interface Eq[T any] { eq(o T) int }`, `impl *N : Eq[[8]uint8]`, `func inner[T Eq[[sizeof(U)]uint8],
U any](t T) int`, `type G[T Eq[[sizeof(U)]uint8], U any] struct { t T }`, `type Box[K any] struct { k K }`, `type
G1[A any] struct { a A }`.

**(A) Valid programs rejected at the declaration:** a call (`func fwd[U any](n *N) int { return inner[*N, U](n) }`), a
field (`type W[U any] struct { g *G[*N, U] }`), a signature (`func fA[U any](k *K2[[sizeof(U)]uint8, *N]) int`), a
generic interface's method (`interface Hh[U any] { take(k *G[*N, U]) int }`), an alias's target (`type GA[U any] =
G[*N, U]`), a call in a field's length (`type W9[U any] struct { a [len(mk2[*N, U]())]uint8 }`), a type-parameter
argument (`func fwd2[V Eq[[8]uint8]](v V) int { return inner[V, int64](v) }`, `func f6[V Eq[[8]uint8], U any](v V)
int { return inner[V, U](v) }`), a struct instance in a signature (`type W3[V Eq[[sizeof(U)]uint8], U any, X any]
struct { g *G[V, X] }` with `func s3[X any](w *W3[*N, int64, X]) int`), identity and assignability through an
instance's arguments (`var p *G1[[sizeof(U)]uint8] = q` with q a `*G1[[8]uint8]`, `p == q`), `same` (even on
`*([sizeof(U)]uint8)` and `*([8]uint8)`), interface values (`var e *Eq[[sizeof(U)]uint8] = n`; an upcast from `*J[U]`
with `interface J[U any] : Eq[[sizeof(U)]uint8]`; the decay from `@Eq[[sizeof(U)]uint8]`), an impl's coverage
(`impl *Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [8]uint8) int`), duplicate parents (`interface
KP2[U any, X any] : Tag[[sizeof(U)]uint8], Tag[[sizeof(X)]uint8] {}`: two dependent lengths are one placeholder).

**(B) Invalid programs accepted:** a function signature (`func sigb[V Eq[[sizeof(U)]uint8], U any, X any](g *G[V,
X]) int` at sigb[*N, int64, int32]), a method signature (`func (b *B2[V, U, X]) m(g *G[V, X])` at B2[*N, int64,
int32]), an impl list (`impl *B3[V, U, X] : Hk[*G[V, X]]` at B3[*N, int64, int32]), an impl list's own expression
(`impl *Box[K] : Eq[[len([...]uint8{cast(uint8, sizeof(K) * 40)})]uint8]` at Box[int64]: 320 does not fit uint8),
`Self` (`interface I3[U any, X any] : Eq[[sizeof(U)]uint8] { m(x *G[Self, X]) int }` at I3[int64, int32]), and an
instance built first inside a signature's derivation (`sw4[*N, int64, int32](nil)` then `var w W3[*N, int64,
int32]` compiles; in the other order it is rejected).  An instance's signature, method signatures, constraints
and impl lists are resolved under `DerivingInstanceSig`, which makes every instantiation they form not user-facing
(`instantiateGenericDeclWithArgs`), so no constraint is judged there; an instance first built there is never judged.

**(C) Valid programs rejected in an instance**, an impl's interface list substituted with the placeholder: `impl
*Box[K] : Eq[[sizeof(K)]uint8]` with `func (b *Box[K]) eq(o [sizeof(K)]uint8) int` at Box[int64]; `var e
*Eq[[16]uint8] = &b` under `impl *Box[K] : Eq[[2 * sizeof(K)]uint8]`; `func g4[U any](b *Box[U]) int { return
inner[*Box[U], int64](b) }` at g4[int64].

**(D) Outside this plan:** an interface extension chain that grows (`interface I[T any] : J[[sizeof(T) + 1]uint8]`,
`interface J[T any] : I[T]`) is a spec question of its own; a generic with no declaration check (an interface-only
package) has no instance checks and nothing deferred, so nothing changes for it; the todo entries "A constraint miss
in a generic's field is never reported when the instance it names waits for a layout" and "Inside a generic's
instance, a type argument satisfies a constraint through an impl private to another package's `.bn`".

## 3. Design

### 3.1 The declaration's check defers what a dependent length decides

A judgment in which a length that depends on a type parameter takes part, on either side, and that holds when every
such length is taken to match any length, is deferred: the declaration's check reports nothing for it.  One that
fails even so is rejected, as today.  ("Takes part" can be decided only as "either side contains such a length":
all such lengths are one placeholder, so an identity walk cannot tell `[sizeof(U)]` from `[2 * sizeof(X)]`.  A
judgment of the declaration's own instantiation with its own parameters is trivially true either way.)

The judgments, with section 2's cases: constraint satisfaction through a non-parameterized impl
(`typeSatisfiesConstraint`: `implCoversInterface`, `isDescendantInterface`), through a parameterized impl
(`genericImplSatisfiesConstraint`) and through a type parameter's bound (`boundSatisfiesConstraint`); identity and
assignability (`assignableDeferringDependent`, with `identicalUpToDependentLengths` extended through the type
arguments of a named instance and of an interface instance), `==`, `same` (check_builtin.bn); interface values
(`valuePtrSatisfies` and its managed counterpart, `isDescendantHere`, `sameInterface`); a parameterized impl's
coverage (`methodSigSatisfies`); duplicate parents (`containsTypePtr`, check_iface_extends.bn) and duplicate
interfaces in an impl's list.

### 3.2 Every concrete instantiation is judged where it is formed

`DerivingInstanceSig` stops making an instantiation not user-facing for its constraint check (the flag stays: the
postponing and the member deferral read it, check_generic_postpone.bn, check_generic_build.bn).  So an instance's
signature (`resolveFuncInstanceSig`, `instanceFuncType`), a method copy (`copyGenericMethod`), a function's
constraints resolved for its arguments (`instanceConstraint`), `resolveInstanceResult` and the impl lists (3.4) judge
the concrete instantiations they form, as a build outside a derivation does today.  Every concrete instance is then
judged where it is first formed, whatever the context (sw4/W3 in either order).  The concrete instantiations still
formed with no judgment are `substituteSelf`'s (an implementing method's signature names the same instantiation and
is judged) and `substituteFuncSig` / check_generic.bn's constraint substitution when a generic has no recorded home
(interface-only packages, section 2 (D)).  Substitution with concrete arguments goes away elsewhere (3.4).

### 3.3 `Self`

A `Self` judgment whose constraint or bound holds a dependent length is deferred at the interface's declaration
(3.1) and made for each interface instance whose type arguments contain no `Self`, with `SelfBound` that instance
(today `deferSelfConstraintCheck` returns at once inside an instance).  An instance whose arguments contain `Self`
(`Outer[Self, int32, int32]` named in I4's method) is not judged: its `Self` is the naming interface's, not its own,
and an implementing method's signature names the concrete instantiation (`Outer[*P, int32, int32]`), which is
judged (3.2).  Section 8 for the decision this needs.

### 3.4 An instance's impl interface lists are resolved from source

A struct instance's build resolves each of its generic's parameterized impls' receiver and interface list for its
arguments, after its fields, in the same context (its lender class, its layout frame), from a clone of the impl's
type expressions in the impl's declaration scope with the binders bound, and keeps them on the instance's entry
(`GenericInstantiation`), its errors dropped (the build is not the impl's check).  `Impl` records the impl's
declaration and scope for it (the `.bni`'s scope for an impl declared there).  An impl collected after the instance
was built is resolved for it as a late method is copied in (`backfillInstantiationMethods`); a postponed instance
resolves them when it is built.  `genericImplSatisfies*` with concrete arguments read the entry's lists (falling back
to substitution only for an instance not built yet, which rejects rather than accepts: a placeholder never equals a
concrete length); `checkInstanceImpls` resolves them again with their errors reported (the instance's: "(in the impl
for Box[int64], …)") and checks coverage and duplicate interfaces against them.  With arguments that mention a type
parameter, substitution stays (the declaration's check, 3.1), which keeps the same-declaration guard
(types_assignable_iface.bn) against the false "K does not satisfy" of a speculative probe.

### 3.5 Errors name the instance, once

- A constraint miss found in an instance (any of 3.2-3.4) is not reported when a constraint miss was already reported
  at its position, whatever the text (the declaration's says `Eq[[0]uint8]` or `Eq[U]`, the instance's `Eq[[8]uint8]`
  or `Eq[int64]`): a set of the positions of reported constraint misses, checked before reporting one in an instance.
  An instance built before the declaration's check (a forward reference) can still add one.
- Errors found while an instance is built or derived name it ("(in sigb[*N, int64, int32], instantiated at …)"):
  `resolveFuncInstanceSig` and `buildNewInstance` contextualize the errors since their start
  (`contextualizeInstanceErrors`), each naming its own instance -- a build nested in a body check names the build's
  instance, not the body's; a check replayed after collection names the instance recorded with it
  (`PendingConstraintCheck` gets the `InstWork` it was recorded under).  `instanceErrorAt`'s merge of a named error
  into an identical plain one stays.

### 3.6 The view

As today in an instance's check: `ImplView.All`, with the todo entry on private impls (section 2 (D)) to fix the view
for every instance judgment, deferred ones included.  Section 8.

## 4. Steps

Each lands alone and keeps the tree green; a deferral at the declaration (3.1) lands only with or after the
instance judgment that makes up for it (3.2-3.4).

1. **Impl interface lists from source** (3.4): fixes (C) and the uint8 overflow.  DONE, binate `d669cf25b`
   (2026-11-05).
2. **Attribution and duplicates** (3.5), so that the next step's new judgments read well and are not repeated.
   DONE, binate `43e760718` (2026-11-05): errors named as reported, from a stack of what is being worked on;
   repeats matched by generic, parameter and position, with DeclLevel meaning no concrete arguments bound.
3. **Concrete instantiations judged where formed** (3.2): fixes sigb, the method signature, the impl list, sw4/W3.
   DONE, binate `d11cd29ef` (2026-11-05): judged in ImplView.All, latched apart from a use's check; a probe's
   resolution judges nothing, but an instance it builds judges as any build; a declaration's miss noted at each site
   its own check reaches.  A judgment made while an argument's layout is built is still lost (the todo entry on
   layout waits, which now has this case).
4. **`Self` in interface instances** (3.3).
5. **The declaration's deferrals** (3.1), a family per commit if the diff is large: fixes (A).
6. **Spec** (section 5), with step 5.

## 5. Spec

`gen.mono.check`: add to the dependent constructs "whether a type argument satisfies its constraint, whether an
impl's methods cover its interface, whether an interface value can be built from a type, and whether two interfaces
in a list are the same, where an interface's type argument holds such a length"; and to the "not re-established"
bullet "-- except a judgment that depends on such a length, which is made for each instantiation, with the length
known".  §11.9 `iface.self.constraint`: the same exception, if `Self` is judged per instance (section 8).

## 6. Tests

Conformance (spec/12-generics), with each step: every (A) case as a valid program, with an `_err_` companion for an
invalid instance naming it; (B)'s cases as `_err_` tests (sw4/W3 in both orders); (C)'s as valid programs; the
`Self` forms (I3 at a valid and an invalid instance; `Outer[Self, …]` named in an interface, valid); duplicates (one
report per position, d1/d3 shapes).  Unit tests in check for the duplicate rule, attribution of build and derivation
errors, and the impl-list memo (backfill, postponed, REPL rollback).

## 7. Cost

Instances are cached before they are built, constraint checks latched per (entry, view) before they run, and a
function instance registered before its signature is resolved, so judging every concrete instantiation adds work in
proportion to instances × views, with no recursion risk found.  The impl lists (3.4) are resolved once per instance,
not per satisfaction probe.

## 8. Waiting for the user (from the focused review of C, 2026-11-04)

1. `Self` (3.3): §11.9 `iface.self.constraint` decides `Self` once, at the interface's declaration.  Per instance
   (recommended; a third difference, with the spec exception of section 5), or at the declaration -- which without
   length expressions to compare would have to reject every `Self` judgment involving such a length, valid ones
   accepted today included.
2. The view (3.6): `ImplView.All` lets any package's private impl decide an instance judgment (today's instance
   checks do so already; C extends it to deferred judgments).  Land C as decided and fix the view under its own entry
   (recommended), or give C's deferred judgments the declaration's view now.

## 9. Earlier versions and their reviews

v1 (`49f1c1cce`) had each instance's check re-judge its generic's declared types, gated on the declaration's
errors, with the instance's builds judging nothing; its review found that builds were the only judgment of alias
targets and interface instances, that the gate could not be recorded, and that classifying an argument by its syntax
broke programs main accepts.  v2 (`2384cd876`) marked each deferred judgment on the AST for the instances to judge;
its review found the marks lost on the copies the checker resolves from (constraints, abstract populations, alias
targets, receivers), latch replays marking parts with no instance judgment, and the `Self` path missing.  C judges
every concrete instantiation where it is formed and needs no record of what was deferred.
