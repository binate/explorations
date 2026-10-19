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

0. **Audits, no behaviour change** — DONE 2026-10-15, results under "Stage 0 results".  Define the stamp: "the type the syntax denotes in its scope" (not a
   context result — a recovery type, an inferred length, a Self substitution).  Audit the 59 raw checker-type
   reads in irgen (ExprType / PackageType used unmapped as IR types, e.g. registerImportVarExtern,
   reflectPackageStructType).  Assert-and-run whether bindTypeParams / CurrentTypeParamOwner are still
   reached for function bodies (they predate checked-copy emission, `d938d91bb`).
1. **Checker records.**
   - DONE (binate `d93f895df`, 2026-10-18): `TypeExpr.DenotedTypeID` (named apart from Expr.ResolvedTypeID,
     which indexes ExprTypes) indexes a dedicated table, Checker.Stamps, read with Checker.TypeExprType.  Each
     record names its node (raw pointer), so a node resolved again keeps its id; an id another checker left
     or a whole-node copy inherited is not trusted.  Recorded by resolveTypeExpr /
     resolveTypeExprAllowInterface / resolveStructType; an assertion / type-switch target records the type it
     recovers (equal to what the target syntax denotes); a `_`-binder receiver is resolved through its
     WithNamedBlankBinders copy and the records copied back; a generic alias's target and a generic
     interface's parents take the records of the copy their abstract instantiation was resolved from (copying,
     not re-resolving, so nothing checked changes).  A type declaration's struct records the declared type's
     underlying (named): checkValueEmbedding's size check rebuilds it unrecorded; a generic struct's
     definition records its struct over the type parameters, unnamed (a package's .bni and .bn each declare
     it, instances are made from the first registered — the .bni's — and only the merged .bn's is checked
     abstractly), each instance copy the instance's named underlying.
     Walk test check_type_stamps_walk_test.bn; a temporary hook running the walk after every compilation
     found nothing unrecorded across the builder-comp corpus and bnc's self-compile.  Exempt from the walk:
     the `[...]` of an inferred-length literal (Stage 0: the literal's type has the length — no record, unlike
     the earlier text of this bullet), an instantiation's head (names a generic), an instance copy's
     constraints (never resolved: each instantiation checks against a copy of its own).
     Notes for the later stages, from the review: (a) a node resolved more than once records the LATEST
     resolution, so a composite type's record is identical to, but not the same object as, the one a symbol
     or signature holds — stage 2's memo by checker Type pointer is sound for named types only, composites
     need structural keys or the declaration's type; (b) the ids are per AST node, so a second checker over
     the same AST (interp LoadProgram then LoadCallable on one loader) takes them: the first checker's records
     then read nil — any stage that reads records must run before another checker re-checks that AST, or the
     records move to per-checker tables keyed by node; (c) throwaway clones (constraint checks, instance
     signatures, `_`-binder copies, failed instance bodies) add records, so the table grows with the
     instantiations, not only the source nodes (memory, not a leak).
   - DONE (binate `d50cc8676`, 2026-10-18): a denoted-type record on type-naming Exprs — a generic function's
     type arguments (every spelling, and each type within one) and a method expression's base (`T.M`,
     `Box[int].M`).  Expr.DenotedTypeID indexes a second table, Checker.Stamps.Exprs, read with
     Checker.ExprDenotedType; the record holds what the TypeExpr of the same spelling denotes.  Recorded by
     typeArgOrInterfaceFromExpr and checkSelectorExpr's `T.M` arm; checking a node as a value drops its record
     and recording it drops its value type (the latest check decides).  The walk requires a record exactly on
     the type positions (none on values or package names).  Found in its review and fixed since: a bare generic
     type name (`one[Box]`, `Box.Get`) is rejected (binate `986fcfa54`), and a REPL retry clears the parked
     declaration's annotations before checking it again (binate `99f45cae3`), so no stale value type is left on
     what is now a package name.  Open: the qualified method expression `pkg.T.M` is rejected (forwarder-audit
     entry) — when it is accepted, its base must be recorded too.
   - DONE (2026-10-18, no further records needed): the type-vs-value classification is the records — an Expr
     with a denoted-type record names a type, one with a value type (ResolvedTypeID) is a value, and a
     selector base with neither is a package (once the REPL retry clears annotations).  A temporary hook
     comparing IR-gen's own reading with the records at all 7 probe sites (methodExprName under
     !selectorBaseIsVar ×2, genericInstanceName ×2, the typeArgExprToType callers ×3) found no disagreement
     across bnc's self-compile and 797 conformance tests (generic, 12-generics, 11-interfaces, iface, method).
     isInterfaceTypeExpr reads the TypeExpr record (1a); lookupStructIdx's other type-or-not uses are
     declaration registration (stage 5).  Stage 4 switches the probes to the records.
   - DONE (1a, 1b): the walk test (check_type_stamps_walk_test.bn) and the storage (Checker.Stamps, ids per node).
2. **Complete the mapper.**  Memoized by checker Type pointer; registers lazily and self-sufficiently from
   the checker type (fields, method sets, parents) so ordering cannot leave it nil; the opaque rule
   (OpaqueExportPkg against the current module — the checker's Type is shared between a `.bni` forward and
   the `.bn` definition, its Underlying filled in place); TYP_SELF is an internal
   error (decision 3); generic declarations by identity
   (irInstantiationFromChecker re-looks a generic up by d.Name).  Instruction-level typing choices stay out
   of scope (StripConstForIR, a named func value's OP_FUNC_VALUE at the bare type, untyped constants).
   - DONE (binate `e95366b1b`, 2026-10-18): generic declarations by identity (stashedGenericDecl: the
     checker's InstDecl, an internal error when IR-gen stashed no such node) and TYP_SELF as an internal error.
   - The memo, lazy registration and the opaque rule: the mapper audit (below) found no lookup gap and no
     opaque difference at the resolveTypeExpr sites.  Decided (2026-10-18, user: "1. We can proceed as you
     suggest. 2. I think stopping is correct."): the memo and lazy registration move to stage 5, the opaque rule
     is a stage 4 check, and stage 3 is next; and anonymous structs that differ only in `readonly` stop being
     interned as one (internAnonStruct by IdenticalStrict).
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
   become internal errors.  Check the opaque rule (OpaqueExportPkg against the current module) where a
   switched site reaches an opaque type (moved here from stage 2: the audit found no difference).
5. **Registries keyed by checker identity, names from it.**  Key by checker Type identity (a pointer memo) —
   the mapper's memo and lazy, self-sufficient registration from the checker type (fields, method sets,
   parents) come here (moved from stage 2: the audit found no lookup gap at the switched sites) —
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

## Stage 0 results (2026-10-15, work-6)

### The stamp

`TypeExpr.ResolvedTypeID` records the type the type expression DENOTES in the scope it is written in — what the
checker's resolveTypeExpr returns for it there — and nothing a use site derives from it:
- `Self` in an interface method signature stamps `TYP_SELF` (substitution happens at impls and constraint calls,
  never on the syntax).
- `readonly` is kept (IR-gen strips it where IR types need that, StripConstForIR).
- A type parameter in a generic declaration stamps the `TYP_TYPE_PARAM`; the instance's copy stamps the argument
  (clones clear stamps, so an instance never inherits the abstract one).  A length that depends on a type
  parameter is dependent in the declaration and concrete in an instance's copy.
- Context results are NOT stamps — IR-gen reads them from the Expr that has them: the length `[...]T` gets from its
  literal (the composite literal's type), the type an assertion or a type-switch case recovers (the assertion
  Expr's type), an error-recovery type (never reaches IR-gen: errors stop compilation).
- A type written as an EXPRESSION (a type argument parsed as an Expr, the `T` of `T.M`, `pkg.T`) gets the
  separate denoted-type record of stage 1, not Expr.ResolvedTypeID, which stays a value's type.

### Raw checker-type reads in IR-gen

37 direct reads (`Checker.ExprType` / `PackageType`) plus 18 calls of five wrappers.  Classes:
- **Query only** (a kind, a flag, a constant stamp, a length, a method lookup; no type flows into IR): genBinary's
  negative-constant test, gen_constval `Len` / `stampedConstValue`, isManagedFuncValueLit, instantiationHead,
  isMethodCallSel, isMethodValueSelector (×2), genMethodValue's method lookup, initIsAggregateValue,
  opaqueDefinedAsStruct, exprIntLitValue, tryFoldOversizedConst, isUntypedIntConst, genExprOrFuncRef (×2),
  emitUntypedIntConst, isInterfaceMethodCall, selectorMethod / methodRecvCheckerType.  These stay: the checker
  is the authority they ask.
- **Mapped** (irTypeFromChecker): declareMethodExprTarget / ensureMethodSig, resolveGlobalVarType,
  methodValueRecvIRType, getIndexElemType, ifaceCallMethodType, and checkerExprIRType's 9 callers (gen_call ×3,
  gen_index_ptr ×2, gen_selector_type ×2, gen_slice ×2) — but checkerExprIRType falls back to the RAW checker
  type when the mapping fails.
- **Box identity** (the checker type kept beside the IR type for `readonly` and qualification, merged by
  mergeQualifiedReadonly after bindTypeParams): wrapAsIfaceValue's / convertToIfaceTarget's / coerceAssignValue's
  source type — genCastBuiltin (×2), genDecl, genExprOrFuncRef's convert, parallel and multi assignment
  (multiValueComponentType), multi-value return.  A complete mapper (readonly kept, names qualified) replaces the
  merge with the mapped type.
- **Raw as an IR type** (the stage 4 work list; each becomes mapped, its fallback an internal error):
  - genUnary: a named-int checker type as OP_NEG's type;
  - checkedIntType (genBinary shift, the shift builtin): a named-int checker type as the shift's type / hint;
  - resolveCaptureTypes: the checker's type as a closure capture's field type (a "defensive fallback");
  - reflectPackageStructType: the checker's `reflect.Package` struct as an IR struct;
  - registerImportVarExtern: the checker's type as an imported extern global's type;
  - checkedArrayType's callers (arrayBaseElemPtr, genIndexPtr, genSlice): the checker array type when the base
    has no IR array type;
  - checkerExprIRType's raw fallback (above).

### Is the type-parameter context still reached?

Neither is, with a checker attached.  Temporary panics in bindTypeParams and in irTypeFromChecker's
`TYP_TYPE_PARAM` branch (firing whenever a type parameter reached either with a checker) never fired across the
full VM-mode conformance suite (builder-comp-int, 3635 passed), the irgen / repl / interp unit tests, the
self-compile of bni (the conformance runner's compiled interpreter) and of bnc (build-bnc.sh stage 2), nor on a
probe boxing `*T` and `**[]readonly T` inside generic instances.  An instance's body is emitted from the checker's
checked copy, whose types are concrete; without a checker there is no checker type to bind.  So bindTypeParams
(and its stated reason, "IR-gen emits an instantiated generic body with the checker types recorded for the
generic definition") is dead, as are the mapper's type-parameter branch and GenCtx.CurrentTypeParamOwner / Args,
read only by those two.  The 23 tests the VM mode skips (compiled-only: C interop and the like) were not covered.
Deleted in binate `c9e1a5256` (landed 2026-10-16): the mapper's branch is an internal error, and selectorMethod
takes the method from the checker's receiver type only.

## Mapper audit (2026-10-18, work-6)

A temporary hook (not committed) wrapped IR-gen's resolveTypeExpr: for every type expression with a checker
record (stage 1a) it mapped the record with irTypeFromChecker and compared MangleTypeArg identity strings with
the legacy result — once in naming mode (GenCtx.NamingInstances), once in lookup mode — and compared the
generic a written instantiation finds by name with the checker's InstDecl.  Corpus: the full builder-comp
conformance run (3720 passed with the hook in), bnc's self-compile and e2e repl.
- Lookup mode: the mapper never returned nil where the legacy resolver resolved.  Every named type, struct,
  alias and instance the legacy resolver finds is registered when the mapper looks it up, so stage 4's switch
  of the resolveTypeExpr sites needs no lazy registration: stage 2's "registers lazily ... so ordering cannot
  leave it nil" has no case in the corpus, and the opaque rule showed no difference either.
- Differences, both modes: (1) an interface alias of a generic interface instantiation (`interface X =
  G[int]`, 1383 and 1508): the legacy resolver keeps the alias's name, the mapper gives the instance — the
  known legacy bug (1506), fixed by the switch; (2) parameterized impls (`impl *Box[T] : Getter[T]`, 194 sites):
  IR-gen resolves the abstract impl's type expressions through its own substitution, while the records hold
  the type parameters (stage 5).
- Naming mode only: an anonymous struct type that differs from an earlier one only in a field's `readonly`
  maps to that earlier struct in the legacy resolver — internAnonStruct interns by types.Identical, which
  ignores readonly (IdenticalStrict keeps it) — so `struct { a T; b int }` gets `b readonly int` (1441, 1476,
  1486).  The mapper keeps the checker's readonly when it builds the struct; in lookup mode it interns through
  the same function and agrees.  Not observable at run time as far as found (boxing identity is the checker
  type's), but the IR type misstates the source.  Decided: intern by IdenticalStrict (stage 2 above).
- Generic identity: the generic a written instantiation names, found by name, is the checker's InstDecl
  everywhere but generic aliases (`type L[T] = Box[T]`, 16 sites: InstDecl is Box's declaration, which the
  lookup of `L` does not find) — so IR-gen's stash holds the checker's own nodes.  The mapper now takes InstDecl
  by identity (stashedGenericDecl, an internal error on a miss) and makes `Self` an internal error; the audit
  run again on top found no miss and no Self.
- Type expressions IR-gen resolves with no checker record (1205 sites) are all its own synthesized nodes —
  type arguments written as expressions (exprToTypeExpr: `f[int]`, `Wrap[int]`, lists across lines),
  method-expression bases (`Box[int].Get`), its own copies of `_`-binder receivers — and the `[...]` heads of
  inferred-length literals (exempt by design).  Each has its record elsewhere: the Expr's (1b), the original
  receiver's (1a), the literal's type.

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
   treats it as an internal error.  LANDED: binate `6d96f6927` (2026-10-15), after the checker fix for `Self`
   in struct types and type arguments (`8846250ac`; user 2026-10-14: "1: (a)" — support them).
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
