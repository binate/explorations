# Plan: the impls an instance's check sees

Status: design v3 (section 11) with review 3's amendments (section 12), 2026-11-13 (temp-5); ready to
implement once the user answers Q1 / Q2.

Todo entry: "Inside a generic's instance, a type argument satisfies a constraint through an impl private to another
package's `.bn`" (claimed 2026-11-06, temp-5).  Q2 of done/plan-dependent-constraints.md section 8 recommended fixing
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
   Fixed by binate `c985aca4c` (2026-11-08): private members are checked only in chains rooted in their
   package (its `.bn` or its `.bni`).
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

## 9. Design v2 (2026-11-13)

v1 made each build's judgments in the view of the naming that ran the build and recorded them for a later naming in
another root to re-judge; review 1 showed the records miss judgments (deferred, latched, a layout wait's) and that
several builds run under a later naming.  v2 keeps no records: a class of an instance whose view sees private impls
the build's view did not judges the instance's derived types again, from source, in its own view -- as each class
already checks the instance's method bodies and parameterized impls from source (checkTypeInstance,
checkFuncInstance, checkInstanceImpls).

**The view** (as v1, section 4).  Inside an instance, a judgment sees public impls; its generic's package's private
impls when the generic is declared in that package's `.bn` (every chain naming it is rooted there, fact 2, restored
by binate `c985aca4c`); and the private impls of its class's **view root** `VRoot`.  `ImplView` gains `VRoot
@[]char` and `Inst bool`; `implVisible(rec, v)` = `PrivatePkg` empty, or `!v.Bni && PrivatePkg == v.Pkg`, or
`PrivatePkg == v.VRoot`.  `ImplView.All` stays only for probes asking whether any impl exists
(`constraintImplNote`), so the private-impl note is given in instances too.

**An instance's VRoot** = normalize(chain root, args): the root of the chain naming the instance
(namingContext.ChainRoot, already carried through builds and checks for the private-members fix, chain_root.bn),
kept when some private impl of that package has a receiver base -- a named type, or a parameterized receiver's
generic -- that the instance's type arguments mention at any depth (the walk exportOwners makes, cached with
Owners), and empty otherwise.  Only a type the root supplies can match such an impl, so an instance like `Vec[int]`
named from many packages with private impls keeps one VRoot (empty) and one check.

**Classes.**  An instance's check state is per (lender class, member root, VRoot): instStateFor and ifaceClassNew
match all three; InstWork and InstCheckState carry VRoot; bindInstanceAt sets InstanceState.BuildVRoot with
BuildLenders and BuildChainRoot; implViewHere takes VRoot from the build (Building) or the instance being checked
(Current), and Inst from Bound > 0 or Current non-nil (review 1, finding 5: derived judgments with Bound == 0 --
checkInstanceImpls, method copies, a function instance's signature -- take it from Current).

**Judged roots.**  Each instantiation entry (GenericInstantiation) and FuncInstance keeps `JudgedVRoots`: the view
roots its derived types were judged in.  A build, a rebuild after a layout wait, a postponed build, a waited
signature redone and a held build's release each add the VRoot they run in (review 1, finding 3: the root a build
ran under is recorded where it runs, whichever naming that is).  A class whose VRoot is not in the list judges
again, then adds it:
- a struct instance, in its class's check (checkTypeInstance, before the method bodies): each field's type
  expression resolved again from a clone of the generic's declaration, in its home scope with the type arguments
  bound, the result discarded -- every instantiation, call and constraint the resolution forms is judged as at the
  build, a deferred one replayed in the class (its record's naming is the class's);
- a function instance, in its class's check (checkFuncInstance, before the body): its signature resolved again from
  source (resolveInstanceSignature), discarded;
- a method copied onto a struct instance: its signature resolved again with its body's check in the class
  (checkInstanceBody already checks a clone of the method; to verify that the clone's signature is resolved there,
  or add it);
- an interface instance, at a new class (ifaceClassNew): its method signatures and parents resolved again from its
  clone body (as populateInstantiatedInterface does), discarded, `Self` judged bounded by the instance;
- a parameterized impl's list and coverage: already resolved from source per class (checkInstanceImpls).

An empty VRoot is in every list from the start: a view with no root's private impls is the build's first view in the
common case, and a class with an empty VRoot judges nothing again.  The re-judgment is made only where some chain's
root has a private impl that can serve the instance's arguments: rare.

**Aliases** (review 1, finding 4).  A generic alias's target is as if written at its use: its resolution binds the
alias's type arguments (Bound + 1) but judges in the use's view -- implViewHere sees past the alias namings
(aliasNamings, as the `Self` judgment already does), not Inst.

**Latch keys.**  implViewKey keys an instance's view by what it sees: `*derived:` for Derived, `*` for Inst, the own
package when not a `.bni` view, `+`, VRoot.  Outside instances the keys are unchanged.  A class's re-judgment
latches under its own key, so a judgment already made in that view is not made twice.

**Remaining difference from the spec** (section 5, widened by review 1, finding 6).  A type a generic writes -- in
its `.bni` or a `.bn` -- that the type arguments also mention is judged in the class's view, seeing VRoot's private
impls, where the spec judges it where written (G's `.bni` writing `inner[*dep.T, X]`, named from R with a private
`impl *dep.T`, sees R's impl).  It matters only for judgments made in an instance (the dependent ones, after design
C every deferred one).  Closing it needs each type's provenance, root-supplied or written, which sank design C's v1;
whether to accept it is the user's decision (Q1 below).

**Tests** (as section 6, plus) the orders and rebuilds of review 1, finding 3: an instance built first from r3dep's
`.bn` (accepted there), then named from main (rejected, the private-impl note, naming the instance), and the
reverse; a build that waits for a layout and is rebuilt under main's naming; a held provisional build released under
each; an alias whose target needs the use's private impl; a derived judgment (a method copy, an impl list) in a
class with a VRoot.

**Questions for the user.**
1. Q1: accept the remaining difference above (a written type the arguments mention sees the root's private impls),
   or require provenance tracking first?  Recommended: accept, documented in gen.mono.check -- today's difference
   (any package's private impl, decided by what the run checks from source) shrinks to the root's own impls, decided
   by the root's own sources.
2. Q2: VRoot only when a private impl could serve the arguments (normalize), or always the chain root?  Recommended:
   normalize -- the always-root form multiplies classes (one per package naming the instance) and their re-checks
   for no difference in what they see.

## 10. Review 2 (2026-11-13): v2 needs a v3

Probes under the session scratchpad's s6/review-v2/, with a compiler built from binate `928f6784b`'s tree; the
section-1 repro still reproduces there.  The approach (each class re-judges from source in its own view) is sound;
its rules were not:
- F1 (critical): seeding the empty VRoot as judged is backwards.  A view with more impls only accepts more (every
  impl query is an existence test), and the usual order is the dependency's `.bn` naming the instance first (VRoot
  r3dep, accepted) and main last (VRoot empty): main would skip the re-judgment and be accepted -- the bug.
- F2 (critical): namingContext.ChainRoot is normalized to empty (chainRootFor) once a chain enters another package's
  generic, so a nested instance would lose its VRoot: pkg/binate/check's `.bn` names `table.Table[...,
  instanceDeclHasher, ...]` with private hasher impls, and Table's `Cursor` would be judged without them -- the gen1
  build fails; a three-deep chain rooted in r3dep is falsely rejected.  VRoot must be carried from the enclosing
  class, as v1's carried(n) did.
- F3 (major): at a top-level naming in a `.bni`, chainRootAt returns the current package, not empty, so a package's
  `.bni` generic body could lean on its own `.bn`-private impls (accepted alone, rejected once imported).
- F4 (major): an alias's target resolution runs in the alias's package and `.bni` position; seeing past the alias
  namings is not enough -- the use's whole view (Pkg, Bni, Inst, VRoot) must be kept.
- F5 (medium): Inst from "Current non-nil" reaches an abstract resolution first reached inside a build
  (resolveGenericTypeDeclAbstractNow sets Bound 0 but leaves Current).
- F6 (medium): JudgedVRoots' bookkeeping (waits, holds, tentative REPL checks, releases, undo) is unspecified.
- F7 (medium): re-populating an interface at ifaceClassNew happens at naming time, possibly inside a wait, a
  collection or a held build; the population mutates the interface (a discard mode is needed); `Self` needs the
  instance on the naming stack.
- F8 (minor): resolveFuncInstanceSig sets Building itself; conversionImplNote, deferSelfConstraintCheck and
  judgeDerived also use All; a struct's whole underlying type (not only fields) is re-resolved; checkFuncInstance's
  early return; re-resolution errors need naming and contextualizing.

Verified sound: checkInstanceBody already resolves a method's or function instance's signature (receiver included)
per class; re-resolution hits the caches, queues new classes rather than recursing; deferred judgments record their
view; normalize is sound and computable when an instance is first named.  The review proposed dropping JudgedVRoots
for latches keyed by view (section 11).

## 11. Design v3 (2026-11-13)

**The view** (v2's): public impls; the generic's package's private impls when it is declared in a `.bn`; and the
class's **VRoot**'s private impls.  `ImplView` gains `VRoot` and `Inst`; implVisible as in v2.  Every use of `All`
for a judgment becomes `Inst` with the class's VRoot -- judgeDerived, deferSelfConstraintCheck, conversionImplNote's
probe (which bumps Bound) -- and `All` stays only for constraintImplNote's "is there any impl" probe.

**VRoot, carried** (F2, F3).  At a naming outside any instance: normalize(Site, args) -- the naming's package, empty
in a `.bni` (not chainRootAt's current package).  Inside an instance's build or check: normalize(the enclosing
class's VRoot, args) -- `InstanceState.BuildVRoot` in a build, `Current.VRoot` in a check -- independent of the
member root (InstCheckState.ChainRoot, which stays as it is for private members).  normalize(R, args) = R when some
private impl of R has a receiver base the args mention (named types and the generics of parameterized receivers,
through type arguments; fields need not be entered: a generic cannot project a field's type), else empty.

**Classes** (v2's): per (lender class, member root, VRoot); InstWork, InstCheckState and an interface instance's
class records carry VRoot; resolveFuncInstanceSig and bindInstanceAt set BuildVRoot (F8).

**Re-judgment without records** (F1, F6, review 1 finding 3).  In each class of an instance whose arguments mention
the receiver base of some private impl (of any package -- the class's VRoot may be empty), the class's check
re-resolves the instance's derived types from source, in its view, as the build resolved them -- not as a
derivation, so the latches (CheckedViews, keyed by view as below) of the class the build ran in make it a no-op
there, and a judgment already made in that view is not made again:
- a struct instance: its whole underlying type expression (fields, or the target of a non-struct generic type, F8),
  from a clone of the declaration in its home scope with the arguments bound, the result discarded, in
  checkTypeInstance before the method bodies;
- a function instance or a method: its signature, already resolved per class by checkInstanceBody -- moved before
  checkFuncInstance's early return for a generic whose body check failed (F8);
- a parameterized impl: already per class (checkInstanceImpls);
- an interface instance: its method signatures and parents resolved from its clone body into a scratch interface
  (F7), with the instance on the naming stack so `Self` is judged bounded by it -- queued as a work item at the
  class's first naming that reaches the user (not in a wait, a collection or a held build: the class is recorded
  only then), made in the instance-check drain.

Errors the re-resolution reports name the instance (pushInstanceNaming, contextualizeInstanceErrors); an error that
is no judgment (an array length too large) is the build's already, and is deduplicated by position and text.  The
cost is one re-resolution per extra class of an instance a private impl could serve: rare.

**Latch keys** (v2's): `*derived:` / `*` / own package and Bni / `+` / VRoot; a view with VRoot r3dep and one with
VRoot empty latch apart.

**Aliases** (F4).  Entering a generic alias's target saves the use's whole view (Pkg, Bni, Inst, VRoot) in aliasUse,
and implViewHere returns it while the target is resolved: the target is as if written at the use.

**Inst for derived judgments** (F5).  A derived judgment made with Bound 0 (checkInstanceImpls, a method copy, a
signature) takes Inst and VRoot from the derivation (judgeDerived sets them, as it sets All today), not from
Current, so an abstract resolution reached inside a build keeps its own view.

**Remaining difference** (v2's, Q1): a written type the arguments also mention sees VRoot's private impls.

**Tests** (section 6 and v2's, plus review 2's): the dependency-first order (r3dep's `.bn` names the instance before
main; main rejected); a three-deep chain rooted in r3dep (accepted); a struct within a struct shaped like Table /
Cursor with a private hasher (accepted; and the gen1 build itself); R's `.bni` generic body leaning on R's private
impl (rejected in R too); alias uses from r3dep's `.bn` (accepted) and main (rejected).

**Questions for the user** (v2's): Q1, accept the remaining difference (recommended); Q2, VRoot normalized
(recommended), now also the test for which instances re-judge.

## 12. Review 3 (2026-11-13): v3 nearly ready -- amendments

Review 3 re-checked v3 against review 2 (F1-F8 hold, with the gaps below) and read whether the build's own class
makes the re-resolution a no-op: the constraint checks of struct and interface instances, a generic alias's own
constraints and deferred replays are latched by view; nested opaque checks are skipped; generic-function calls in
length expressions, `Self` checks, a REPL TentativeMode check and errors that are no judgment are made again, in the
same view (the same verdict, merged by position and message: extra work, not a wrong result).  Amendments, part of
the design:
- A1 (V3-1, major).  The carried VRoot inside an instance keeps chainRootAt's site rule: a naming written in a `.bn`
  of a package with a `.bni` carries that package (fact 2); otherwise the enclosing class's VRoot.  (Probe v3site:
  r3dep's private `g[Z]` calling `r3lib.outer[*T, int64, int32]`, valid, would be rejected without it.)
- A2 (V3-2).  The entry records the mode its build ran in (`BuildDerived`: a struct instance first built inside a
  derivation judged its fields as derived, latched under `*derived:`), and a class re-resolves in that mode, the
  interface re-population too -- so the build's own class is a no-op and a use-only check (an interface instance's
  sizing) is not added.
- A3 (V3-3).  Interface instances get per-class check states (firstInstState / instStateFor, replacing IfaceClasses
  / ifaceClassNew and noteInterfaceInstances' class match) and a third branch in checkInstanceWork; they join chains
  (depth limit, failure propagation, REPL tracking) -- a behaviour change, tested.  The work is queued at every
  naming, as queueStructInstance / requeueStructInstance do (a collection-time naming included: replayPendingChecks
  never requeues), held in a provisional build by holdWork.  The re-population keeps the instance's identity for
  `Self`: c.SelfBound stays the populated instance while resolving into the scratch.
- A4 (V3-4).  conversionImplNote's probe stays All (it asks whether every impl would make the conversion work), and
  its early return on All becomes one on the probe itself, so the note is given inside instances.
- A5 (V3-5).  The alias's saved use view applies only while the alias's entry is on top of the naming stack
  (aliasNamings); a build nested in the target keeps Inst and its own BuildVRoot.
- A6 (V3-6).  namingContext carries VRoot, for the replays that bind at a recorded naming (replayPendingChecks,
  makeDeferredConstraintCheck, runQueuedSelfChecks).
- A7 (V3-7).  A derivation outside any build or check (backfillInstantiationMethods, copyMethodOnDemand,
  copyLaterMethod) judges nothing: every class re-resolves the method's signature in checkInstanceBody.
  resolveGenericTypeDeclAbstractNow also clears DerivingInstanceSig (today's All hides that it does not).
- A8 (V3-8).  The gate (some private impl's receiver base among the arguments) is evaluated when the class is
  checked, not cached at its first naming: a `.bni` load names instances before the `.bn`'s impls are collected.

**Steps** (each lands alone, green): (1) the view, VRoot carried and normalized, classes and latch keys, with A1,
A4, A6 -- the repro fixed for function and struct instances, struct re-resolution (A2, A8); (2) interface instances'
per-class states and re-population (A3); (3) aliases and derivations outside builds (A5, A7); (4) the spec's note on
the remaining difference (if Q1 accepts it).  Tests per section 6, v2's and review 2's lists, and review 3's v3site.
