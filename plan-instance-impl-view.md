# Plan: the impls an instance's check sees

Todo entry: "Inside a generic's instance, a type argument satisfies a constraint through an impl private to another
package's `.bn`" (claimed 2026-11-06, temp-5).  Q2 of plan-dependent-constraints.md section 8 recommended fixing
this before design C's step 5 (the declaration's deferrals), which moves more judgments into instances.

## 1. The bug

An instance's check sees every impl (`ImplView.All`, set by `implViewHere` whenever `InstanceState.Bound > 0`: an
instance's build, a function instance's signature, an instance body).  The reasoning in the comment is that the
instance's uses of impls through its type arguments were judged where it was named, and its other uses where the
generic was declared.  That fails for every judgment the declaration could not make: the placeholder-length cases
today (design C's (A)/(B)), and after C every deferred judgment.  Repro (still on main `868d8f2bf`): r3lib's `.bni`
has `interface Tag[T any] { tag() int }`, `func inner[T Tag[[sizeof(X)]uint8], X any](t T) int`, `func outer[V
Tag[[sizeof(U)]uint8], U any, X any](v V) int { return inner[V, X](v) }`; r3dep's `.bni` declares `type T` with
`impl *T : r3lib.Tag[[8]uint8]`, its `.bn` a private `impl *T : r3lib.Tag[[4]uint8]`.  main's
`r3lib.outer[*r3dep.T, int64, int32](&t)` compiles and prints 5; `r3lib.inner[*r3dep.T, int32](&t)` is rejected
("the matching impl is private to package pkg/r3dep").  Whether main is accepted also depends on whether r3dep's
`.bn` is checked from source in the same run -- checking must depend only on main's sources and its dependencies'
`.bni` files.

## 2. The rule

§12.3 `gen.mono.check`, in an instance's check: "the `impl`s, visibility and opacity of a type supplied by a root
instantiation's type arguments are those at that root ...; a type written in a generic's own declaration is judged
where it is written".  §16.4: an impl only a package's `.bn` declares makes its receiver satisfy its interfaces only
in that package's `.bn` files.

## 3. Facts the design rests on

1. A private impl exists only in a package P with a `.bni`, declared in P's `.bn` (`privateImplPkg`).
2. A generic declared in P's `.bn` (P has a `.bni`) is private to P's `.bn`: generic bodies live in the `.bni`
   (and a `.bni` cannot name a `.bn`-private declaration, `pkg.bni.self-contained`).  So every chain of
   instantiations that reaches it is rooted in P's `.bn`.  Hence in any chain, the private impls the spec lets a
   judgment see are those of the root's package R, when the root is written in R's `.bn`: for a type the root
   supplies, and for a type written in one of R's `.bn` generics.  No other package's private impl is ever visible
   in an instance.
3. A package's impls are collected before its other declarations resolve (impl_collect_first.bn), so at any naming
   in R's `.bn` every private impl of R is recorded.
4. During a build `c.curPkgPath` is the generic's package (enterIfaceInstance, the struct populate), so the
   generic's own view -- `(Pkg, Bni)` as `implViewAt` computes it from the position -- is available.  (To verify
   for an instance body of a `.bni` generic: `implViewHere`'s `Bni` must be true there, or the generic's package's
   private impls leak into its `.bni` body.)

## 4. Design

**The view.**  An instance's judgments see public impls, the generic's own view's private impls (its package's,
when it is declared in a `.bn` -- as today for its declaration check), and those of the instance's **root
package** `Root`.  `ImplView` gains `Root @[]char` and `Inst bool` (taken inside an instance: `Bound > 0`).
`implVisible(rec, v)` = `PrivatePkg` empty, or `!v.Bni && PrivatePkg == v.Pkg`, or `Root` non-empty and
`PrivatePkg == v.Root`.  `ImplView.All` stays only for the probes that ask "is there any impl"
(`constraintImplNote`); `nestedInInstance` uses `Inst`.  The private-impl notes are then given inside instances too
("the matching impl is private to package pkg/r3dep").

**An instance's Root.**  For an instance X named in naming context n: `Root(X) = normalize(carried(n), X.args)`.
- `carried(n)` at a naming outside any instance is n's site (`namingContext.Site`: the package the naming is
  written in, empty in a `.bni`).  Inside instance Y's check (Current) or build (Building): `Root(Y)` when
  non-empty, else Y's generic's own package when Y's generic is declared in a `.bn` (it is then the root's
  package, fact 2), else empty.  `namingContext` gains `Root`.
- `normalize(R, args)` = R when some private impl of R has a receiver base -- the named type, or the generic of a
  parameterized receiver `*W[K]` -- that `args` mention at any depth (the walk `exportOwners` makes, cached on the
  instance's first state with its Owners); empty otherwise.  Only a type the root supplies, which `args` carry,
  can match such an impl (a type written in a `.bni` generic is judged where written), so R's private impls cannot
  serve X's judgments otherwise.  This keeps an instance like `Vec[int]`, named in the `.bn` of many packages with
  private impls, checked once rather than once per package.

**Classes.**  An instance's check state (`InstCheckState`) is per (lender class, Root): `instStateFor` matches
both, `InstWork` carries `Root`, the body check and the signature resolution make it the naming context's carried
root.  An interface instance's classes (`ifaceClassNew`) likewise.  `bindInstanceAt` sets
`InstanceState.BuildRoot` with `BuildLenders`.

**Builds are cached once.**  A type instance's build, a function instance's signature and an interface instance's
population are made for the first naming only, in its Root.  A build made with a non-empty Root records each
constraint judgment it makes on its instance (`RootJudged`: declaration, arguments, position, view).  A later naming
of the instance in a different Root (normalized against its arguments) re-judges those records in that Root -- each
as a naming of the judged instance in the same context, so that instance's own records are re-judged too, and
attributed to the chain as at the first naming -- latched per (instance, Root) (a `CheckedViews` key).  A build
made with an empty Root records nothing: a view with more private impls only accepts more, and its misses went to
the naming it was made for.  Records are made only where Root is non-empty, which needs a private impl that can
serve the instance's arguments: rare.

**Latch keys.**  `implViewKey` keys an instance's view by what it sees: prefix (`*derived:` for Derived, `*` for
Inst), the own package when not a `.bni` view, `+`, Root.  Outside instances the keys are unchanged.

## 5. What remains different from the spec

A type written in a `.bni` generic of R, judged in an instance whose Root is R, sees R's private impls; the spec
judges it where written (the `.bni`), seeing none.  It matters only for a judgment made in the instance rather than
at the declaration -- a judgment the declaration made stands, its error reported -- that is, the dependent ones the
declaration takes placeholders for, and after design C's step 5 every deferred one.  Closing it needs each type's
provenance (root-supplied or written), the problem that sank C's v1.  Today's difference (any package's private
impl, decided by what the run checks from source) shrinks to R's own impls, decided by R's own sources.

## 6. Tests

Conformance spec/12-generics, multi-package (`pkg/` dirs as in 108):
- the entry's repro: main's `outer[*r3dep.T, int64, int32]` rejected, with the private-impl note, naming outer's
  instance; the direct `inner` call as today;
- the same chain rooted in r3dep's `.bn` (a function there calling `r3lib.outer[*T, int64, int32]`): accepted;
- a build: a struct field `*W[V, int32]` formed in an instance build, `W` needing `Tag[[4]uint8]`;
- a function instance's signature (`func g[V ..., U any](p *W[V, U])`), and an interface instance's method;
- orders: the instance built first from r3dep's `.bn` (accepted there) and then named from main (rejected), and
  the reverse;
- valid: an instance named from two packages with private impls that cannot serve it (one check -- a unit test on
  the classes).
Unit tests in check: normalize, the classes, the latch keys, the re-judgment and its latch.

## 7. Cost

No change where no package's private impl can serve an instance's arguments.  Otherwise: one extra class per such
root (its body checks again), and the re-judgment of the build's records at a naming in another root.

## 8. Review 1 (2026-11-06): not ready

Probes under the session scratchpad's review-view/, with main `868d8f2bf`'s compiler.
1. MAJOR: fact 2 is false.  A `.bn`-only method or parameterized impl of a `.bni` generic type is checked for every
   instance of the type, other packages' included (`checkTypeTypeInstance`, `checkInstanceImpls`): main naming
   `gm2.W[int64]` is rejected for gm2's private `priv` (filed as its own entry, `36269a0ad`).  With it, a private
   member's own view gives its package's private impls to types main supplies (leak), and a member written in a
   `.bn` loses its package's impls when the carried root is another package (false rejection).  Fixing that entry
   first (check private members only for chains rooted in their package's `.bn`) restores fact 2.
2. MAJOR: "records each constraint judgment the build makes" misses judgments: those deferred while declarations
   are collected (`recordPendingConstraintCheck`, replayed after the build), on a layout wait
   (`deferConstraintCheck`) or a function signature (`deferFuncConstraintCheck`); and those skipped because the
   judged instance was latched already in the same view (S's build judges `W[*T, int32]`, S2's skips it, so S2 has
   no record).  Every naming a build makes would have to be recorded, latched or deferred.
3. MAJOR: a build's Root is not always its first naming's: a layout-wait rebuild, a postponed build, a waited
   function signature redone and a held build's release all run under a later naming.  Root would be stored on the
   entry, and the re-judgment made at a release too, under judgeInstantiation's latch rules.
4. A generic alias's target is bound like an instance but is as written at the use: normalizing against the
   alias's arguments drops the use's package; the target must be judged in the use's view.
5. Derived judgments made with `Bound == 0` (`checkInstanceImpls`, method copies, a function instance's signature)
   take Inst / Root from `Current` or the entry, not from `Bound`.
6. Section 5 understates the difference: any generic's written type the arguments also mention sees the root's
   private impls (G's `.bni` writing `inner[*dep.T, X]`, named from R with a private `impl *dep.T`).
7. Verified: facts 1, 3, 4 (multi-file path; with no per-file scopes `scopeForFile` falls back to the package
   scope and `Bni` is false in a `.bni` generic's instance), and normalization's soundness for root-supplied types
   (the walk must collect named types and generic declarations, not packages).

Direction for v2 (to be decided with the user): judge the namings a build makes for an instance whose arguments a
private impl could serve in each of the instance's classes rather than in the build -- in a struct or function
instance's per-class check, and for an interface instance at each new class -- so no judgment depends on which
naming made the build.
