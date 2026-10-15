# Plan: IR-gen takes its names' meanings from the checker — one resolver

Status: REVISED after adversarial review (2026-10-12, work-6).  User: "What's the *right* thing to do,
regardless of effort" → this; "yes (and get it reviewed)".  Prerequisite for REPL type / interface
shadowing (plan-repl-type-shadowing.md, approach B).  Open questions for the user at the end.

## Problem

IR-gen resolves names from the AST a second time, in parallel with the checker, with its own scopes:
- TYPE names: `resolveTypeExpr` (82 calls in 38 files), ~81 name-keyed registry lookups, a type-parameter
  substitution context (CurrentTypeParamNames / Types / Owner / Args), `TypInt()` fallbacks that turn a
  type it cannot resolve into `int`;
- VALUE names: `CurrentImportPkg` precedence (101 sites: funcRefName, namesFunction, const lookups,
  bareGlobalIdx, methodExprName, moduleNamesInScope, instantiationHead), the defining file's import overlay
  for generic bodies (overlayFileImports), RegisterGenericBodyDeps, local-scope tracking for shadowing.
When the two resolvers disagree the result is usually a silent miscompile.  Recent instances:
`df56f73cd` and `1d3a19624` (a consumer's names capturing an imported generic body's — value names),
`91e8867e7` (the defer pre-pass not seeing locals — value names), `d34cc0451` (an unregistered prompt
generic struct becoming `int` — type names).

## Goal

The checker is the single authority on what a name means — a type name or a value name.  IR-gen maps the
checker's types (irTypeFromChecker) and the checker's recorded resolution of each identifier / selector,
keys its registries by the checker's type identity, and derives emitted names from that identity (symbol
mangling stays name-based: it is the cross-module and runtime contract).  With a checker attached, a name
IR-gen cannot map is an internal error, never `int`.

## What the review verified

- One checker per compilation in every driver that runs IR-gen (bnc compile / library / test / --pkg,
  the bnc --test runner, bni test, interp, the REPL incl. mid-session imports); `mod.Checker` is set before
  RegisterImports.  No production IR-gen runs without a checker (GenModule is test-only; the 47
  `Checker == nil` branches in irgen are test paths).
- Instance copies are separate (CloneDecl shares no nodes): function / generic-method bodies, struct fields,
  interface method sets and parents, generic alias targets are each resolved on their own copy.
- Precedent: TypeExpr.LenVal is already a checker stamp IR-gen requires under a checker.
- irTypeFromChecker already runs on every instance's type arguments (matchInstanceCopy, naming mode).

## Stages (each lands green; byte-identical IR across the corpus where a stage claims no change)

0. **Audits, no behaviour change.**  Define the stamp: "the type the syntax denotes in its scope" (not a
   context result — a recovery type, an inferred length, a Self substitution).  Audit the 59 raw checker-type
   reads in irgen (ExprType / PackageType used unmapped as IR types, e.g. registerImportVarExtern,
   reflectPackageStructType).  Assert-and-run whether bindTypeParams / CurrentTypeParamOwner are still
   reached for function bodies (they predate checked-copy emission, `d938d91bb`).
1. **Checker records.**
   - `TypeExpr.ResolvedTypeID` stamped at every resolution, including the piecewise sites:
     `[...]T` composite heads (checkCompositeLit infers the length without resolveTypeExpr), assertion /
     type-switch targets (IR-gen should take the assertion Expr's recovered type instead), impl interface
     refs (resolveImplInterfaceRef), interface parents (resolveInterfaceExtension), interface-alias targets,
     and write-back from the copies WithNamedBlankBinders makes (receivers / impl receivers with `_`
     binders); cleared by the clone functions (as LenVal is); re-set on REPL re-checks.
   - A DENOTED-TYPE record on type-denoting Exprs (type args written as expressions, `T.M` / `Box[int].M`
     bases, `pkg.T` selectors) — separate from Expr.ResolvedTypeID, which is a value's type.
   - A type-vs-value classification for IR-gen's probe sites (typeArgExprToType on the X of `X.M` / `X[...]`,
     isInterfaceTypeExpr, methodExprName, lookupStructIdx used as a type-or-not test).
   - A walk test: every TypeExpr in checked ASTs stamped; no TYP_TYPE_PARAM in instance copies.
   - Storage: a dedicated TypeExpr table (or reuse an id), not ever-growing ExprTypes.
2. **Complete the mapper.**  Memoized by checker Type pointer; registers lazily and self-sufficiently from
   the checker type (fields, method sets, parents) so ordering cannot leave it nil; the opaque rule
   (OpaqueExportPkg against the current module — the checker's Type is shared between a `.bni` forward and
   the `.bn` definition, its Underlying filled in place); TYP_SELF is an internal
   error (decision 3); generic declarations by identity
   (irInstantiationFromChecker re-looks a generic up by d.Name).  Instruction-level typing choices stay out
   of scope (StripConstForIR, a named func value's OP_FUNC_VALUE at the bare type, untyped constants).
3. **Differential, behind a flag.**  Compute the mapped side in pure naming mode (GenCtx.NamingInstances —
   no registration, no `__anon_N` counter advance) and compare identity strings (kind + MangleTypeArg-style
   identity incl. readonly), not objects.  Run on the conformance corpus, the self-compile and the REPL e2e,
   on one 64-bit and one 32-bit target (LenVal / sizeof are target-dependent).  Triage each disagreement:
   mapper gap, stamp gap, or legacy bug — a legacy bug gets a test and a todo and is fixed by the switch, not
   patched in the resolver that is being deleted.  Expected (legit) differences: parameterized-impl
   type parameters (stage 5), registration-order gaps.
   One registration-order gap is a live bug the switch must fix (decided 2026-10-12, user: "yes" — no patch
   to the legacy resolver): an alias of a generic interface instantiation (`interface X = G[int]`) is an
   IR-gen stub until the interfaces are collected, so a type alias / global / field naming X before then
   keeps the alias name and the upcast to it fails — an internal error at build ("negative vtable slot
   offset"), "target vtable not found" in the VM; conformance `1506_iface_generic_alias_upcast`
   (`.xfail.all`, drop it when stage 4/5 lands).  The checker already resolves X to the instance (an
   interface alias is another symbol for the target's Type).  `1508_iface_generic_alias_type_order` is the
   guard: instantiating the generic early under the legacy resolver turned a later type alias into `int`.
4. **Switch, by site class.**  Declarations first: var / global types (existing Decl.ResolvedTypeID),
   signatures, struct fields from checker types (removes the several copies of field resolution).  Then
   expressions: assertions and composite literals from their Expr types.  The remaining TypInt() fallbacks
   become internal errors.
5. **Registries keyed by checker identity, names from it.**  Key by checker Type identity (a pointer memo) —
   not by Decl (opaque types have two Decls for one Type, instances none).  NAME things from the checker's
   identity, not `d.Name` (registerTypeDeclEntry, methodQualName's receiver text, dtor / copy helpers, impl
   rows, typeinfo) — else a shadowed T still collides on `main.T` symbols.  Impl rows from the checker's
   impls with checker substitution (parameterized impls are not instance copies: mintGenericImplRows resolves
   the abstract impl's interface args by name today); interface method sets from checker types.  Keep name
   indexes for the by-name consumers: the VM's runtime vtable / typeinfo lookup (BC_IFACE_VALUE,
   BC_DATA_SYM_ADDR, swapIfaceSuffix), LowerOneFunc's same-name replacement, interp FindInterfaceMethodSlot,
   injected-package RegisterPackageVtables / RegisterPackageSatEntries, FuncSig callee lookup, separate
   `bnc --pkg` builds.
6. **Value-name resolution.**  The checker records what each identifier / selector Expr resolves to (local,
   parameter, global, constant, function, package, method expression); IR-gen uses it; then delete
   CurrentImportPkg-as-resolution, the import overlay for generic bodies, RegisterGenericBodyDeps and the
   bare-name precedence rules.  (Coordinate with plan-constant-evaluator.md, which targets IR-gen's own
   constant evaluator and its CurrentImportPkg const lookup.)
7. **Delete the legacy and no-checker paths; convert the tests.**  ~84 irgen test files build IR without a
   checker (223 genFromSource calls in 44 files, 56 genFromSourcePkg in 17, 42 files building GenCtx by
   hand), plus GenModule(nil) helpers in codegen and the native iface tests; many are white-box tests of
   internals this deletes.
8. **REPL type / interface shadowing** (plan-repl-type-shadowing.md).

## Constraints / risks

- IR-gen drivers must fully check every dependency: imported `.bni` generic types / interfaces,
  generic-receiver methods and impls are stamped only because every driver runs CheckPackage on each
  dependency (whose merged file reuses the `.bni` Decl pointers) — a permanent constraint on any future
  interface-only separate compilation.
- REPL stamp staleness: parked / tentative re-checks re-stamp only the nodes they re-resolve — clear at
  re-check, or tag stamps with a generation.
- Performance: registerExprType never reuses an id; TypeExprs are re-resolved often (LoadPackageInterface
  plus CheckPackage, generic-alias clones per use).  On the IR side, memoize by checker Type pointer.
- Coordination: done/plan-defer-block.md (accepted 2026-10-12) rewrites the defer pre-pass; stages 2–6 touch 38+
  files other sessions edit.
- BUILDER: no new language features needed.

## Decisions (user, 2026-10-13: "Go with your recs (for 1/2/4/5/6), and investigate 3 more deeply")

1. Value-name resolution (stage 6) IS in this plan.
2. The differential (stage 3) is a flag used during the transition, removed in stage 7.
3. (User 2026-10-14: "2: Switching sounds fine.")  Interface method signatures come from the checker at the
   dispatch site: genInterfaceMethodCall maps the method's FuncType from the interface type of `sel.X`;
   interp.CallIfaceMethod looks it up through its checker and returns an error for a `Self`-using method.  The
   IR interface registry keeps names, parents and aliases only, so `TYP_SELF` never enters IR and the mapper
   treats it as an internal error.  Proposed order: lands with the checker fix for `Self` in struct types and
   type arguments (MAJOR todo entry; user 2026-10-14: "1: (a)" — support them), before stage 0.
4. Struct and interface INSTANCES map directly from the checker's instance type (typeInstanceCopy dropped
   for types; method bodies keep their checked copies).
5. White-box no-checker tests of deleted internals are deleted; behavioural ones are rewritten to run the
   checker.
6. "IR-gen drivers must fully check every dependency" is accepted as permanent.
7. User (2026-10-14), to (B) for generics with parked declarations unchanged: "That sounds good".  A generic's names — type
   names and value names — mean what they meant where it was declared: an instance reuses the checker's records
   from the generic's declaration-time check, never the live session scope, so a generic declared before a REPL
   shadow keeps the old T (as a non-generic function, already compiled against it, does).  A parked declaration
   was never accepted, so it resolves when it completes and sees the new T.

## Open questions for the user

3. What a Self-typed parameter is in the IR interface registry — DECIDED 2026-10-14 (Decisions 3); history:
   - Facts.  A method whose signature mentions `Self` is object-unsafe (spec iface.self.object-safety): the
     checker rejects calling it through an interface value (check_method.bn), also through a type parameter
     bound to an interface type (`eqv[*Eq]`: "does not satisfy constraint" and "uses `Self` and cannot be
     called through an interface value").  The IR registry's method signatures (MethodParamsFlat /
     MethodResultsFlat) are read only by IR-gen: registry construction (collectInterfaceFromDecl), generic
     interface instances (ensureInstantiatedInterface) and dispatch (genInterfaceMethodCall /
     findInterfaceMethodFromBase).  The VM, LLVM codegen and the native backends read interface method
     NAMES only (IfaceOwnMethodNames, for slot numbering).  So a Self method's signature is never consumed
     for code generation; today's `TypVoid()` (resolveTypeExpr's fall-through for TEXPR_SELF) is inert, but
     a wrong representation, and it would read as a mismatch in the differential.
   - Proposal.  Self stays Self: the registry holds `TYP_SELF` (already in pkg/binate/types, unused by
     IR-gen today); the mapper maps it structurally (`*Self`, `@[]Self`, `*func(Self) int`, results); generic
     interface instances keep it (Self is not a type parameter); interface dispatch asserts the method it
     lowers mentions no `TYP_SELF` (an internal error — unreachable under the checker's rule, so the
     invariant is loud in IR-gen too).  The legacy resolver switches TEXPR_SELF to `TYP_SELF` in stage 1 so
     the differential sees no Self mismatch.  Rejected alternative: storing no signature for an
     object-unsafe method (a per-method flag) — a second representation of what `TYP_SELF` already says.
   - User (2026-10-14): "I think the proposal is ok, but get a focused review of it".  Review (2026-10-14):
     two premises are false.  (i) The checker's rule does not make the dispatch assertion unreachable:
     `typeContainsSelf` / `substituteSelf` miss `Self` inside a struct type and inside type arguments, so
     `Mk() struct { x Self }` is callable through `*I` (MAJOR todo entry "`Self` inside an anonymous struct or a
     generic type argument escapes the checker's Self rules").  (ii) Today's void is not inert: declaring
     `Mk(b *Box[Self]) int` crashes IR-gen (registry construction instantiates `Box[void]`); under the proposal
     it would instantiate `Box[Self]` and emit its helpers.  Missed consumer: interp.CallIfaceMethod →
     FindInterfaceMethodSlot reads the signatures (a host API, so an assertion there must be an error).
     Alternative the reviewer raised: take a dispatched method's signature from the checker at the call site
     (the interface type of `sel.X`, mapped by irTypeFromChecker; interp through its checker) and store only
     names, parents and aliases in the registry — the only signature readers are in irgen
     (gen_iface_registry, gen_generic_type_inst, gen_iface_dispatch).  Then IR-gen maps only signatures the
     checker accepted as object-safe, `TYP_SELF` never enters IR (the mapper treats it as an internal error),
     and (d) is not needed.  My recommendation: this alternative, after the checker fix.  User (2026-10-14):
     "2: Switching sounds fine."
7. REPL late binding (from plan-repl-type-shadowing.md) — DECIDED 2026-10-14 (Decisions 7).
