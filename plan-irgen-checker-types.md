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
   - DONE (binate `5333e1fc2`, 2026-10-19): bnc / bni `--x-type-differential <file>` (irgen
     gen_type_differential.bn) — `differs` and `abstract` (a type parameter, Self, or a type-parameter-
     dependent array length) lines.  Full builder-comp (3728) and native arm32 (3679) with it on: only the
     interface-alias legacy bug (1383, 1508) and parameterized impls' type parameters.
4. **Switch, by site class.**  Declarations first: var / global types (existing Decl.ResolvedTypeID),
   signatures, struct fields from checker types (removes the several copies of field resolution).  Then
   expressions: assertions and composite literals from their Expr types.  The remaining TypInt() fallbacks
   become internal errors.  Check the opaque rule (OpaqueExportPkg against the current module) where a
   switched site reaches an opaque type (moved here from stage 2: the audit found no difference).
   Decided (2026-10-19, user: "the single switch sounds reasonable, but get a quick, focused review of this
   choice (vs the written plan) first"; the review recommended it with the guards below): switch at the
   chokepoint rather than by site class — every class goes through the same mapper and registration
   functions, and a per-class intermediate state splits the interface-alias fix (1506's mismatch, one site
   with the instance and another with the alias's name).  Increments:
   - 4a: resolveTypeExpr returns irTypeFromChecker (lookup mode) for every recorded, non-abstract type
     expression; IR-gen's own reading only for syntax with no record (its synthesized nodes) and abstract
     records (parameterized impls, stage 5).  Guards: (a) NamingInstances cleared while mapping there (a naming
     path reaches resolveTypeExpr: instanceCopyName → canonicalTypeArgs → completeIfaceAliasStub →
     instantiatedAliasTarget); (b) a nil mapping of a concrete record is an internal error, never a fallback;
     (c) in lookup mode the mapper's interface arm requires the interface to be registered (the legacy
     isInterfaceTypeExpr failed loudly on a miss); (d) under --x-type-differential each fallback is logged
     with its reason.  Validation: byte-identical --emit-llvm, legacy vs switched bnc, over the conformance
     corpus and the self-compile on a 64-bit and a 32-bit target (allowed: 1383, 1506, 1508) — catches
     `__anon_N` numbering, registration order, func-type parameter names, the generic-alias path; full
     builder-comp-int (the interp drivers, a second checker over one AST, injected `.bni`-only packages);
     the unit-test runner's builds with the switched gen1; REPL and interp e2e (prompt generics, parked
     declarations, forward types, an interface alias at the prompt); 1383 / 1506 (xfail dropped) / 1508 on
     LLVM, VM, native aa64 / x64 / arm32, plus variants (X typing a field, a global, a parameter before
     interface collection; cross-package; G with parents); a user-CPU A/B of the self-compile.
     DONE (binate `240932251`, 2026-10-19): as above, all four guards.  --emit-llvm legacy vs switched
     byte-identical over the corpus (3743, 64- and 32-bit) and bnc's main module except 1506 (now compiles);
     builder-comp-int 3721; every package's unit tests; e2e repl / interp; 1383 / 1506 / 1508 / 1709 / 1708 on
     five backends (1508 — the early-instantiation hazard — green; the review found the first 1707 / 1708 never
     reached the stub, retargeted as 1709 / 1708, which abort the legacy build); self-compile user time neutral.
     The opaque check: a switched site returns the same registry entry the syntax reading did (same lookup
     order), so OpaqueExportPkg is unchanged — nothing to add.  The interface-alias todo entry moved to done.
   - 4b: type arguments and method-expression bases from their Expr records (ExprDenotedType).
     DONE (binate `ccd303d75`, 2026-10-19): typeArgExprToType and genericInstanceName take the concrete Expr
     record (typeFromRecord / concreteExprRecord, now in gen_checker_type.bn); the differential notes only
     IR-gen's own readings (`unrecorded`, `abstract`).  --emit-llvm byte-identical with no exception over the
     corpus (3747, 64- and 32-bit) and bnc's main module; builder-comp-int 3725; every package's unit tests;
     e2e; generics / alias subset on native aa64 / x64 / arm32 (341 each); self-compile time neutral.
   - 4c: the duplicated field / signature resolution goes.
     Fields DONE (binate `ac80c0313`, 2026-10-19): nine copies → structFields / fillStructFields
     (gen_typedecl.bn); --emit-llvm byte-identical (3751, 64- and 32-bit; bnc's main module).
     Signatures DONE (binate `4a4506027`, 2026-10-20): eleven copies → declParamTypes / appendParamTypes /
     declResultTypes / funcSigOf (gen.bn); --emit-llvm byte-identical (3752, 64- and 32-bit; main module).
   - 4d: TypInt() fallbacks become internal errors; assertions and composite literals from Expr types.
     4d1 DONE (binate `1509653b1`, 2026-10-23): the 27 element / operand / literal-type fallbacks and the
     dummy-constant paths (function literal, interface call, method value, composite literal, a struct with no
     fields) are internal errors; an instrumented compiler saw none fire (self-compile, builder-comp,
     builder-comp-int, e2e); --emit-llvm byte-identical (3291, 64- and 32-bit; bnc's main module).
     4d2 DONE.  4d2a DONE (binate `ce70a1f8e`, 2026-10-24): nine int defaults (multi-value binding /
     assignment components, indexed / ranged / assigned elements, an unregistered call's result, `*p`, `-x`, a
     missing field) and the unlowered expression / builtin / statement kinds are internal errors; an instrumented
     compiler saw none of the nine fire (self-compile, builder-comp, builder-comp-int, the REPL e2e); four
     no-checker irgen tests reached them and now check their source; --emit-llvm byte-identical (3298, 64-bit;
     bnc's main module).  Its REPL prerequisite: a resolved group's functions are declared before any is
     generated (binate `3cd936143`).
     4d2b DONE (binate `91e44506b`, 2026-11-08): genSelector's catch-all (no arm matches) is a compile-time
     internal error, not a runtime panic (user 2026-11-08: "Compile-time error"); it fired nowhere (self-compile,
     builder-comp, builder-comp-int, the REPL e2e, the unit tests of every package that runs IR-gen in-process).
     4d2c DONE (binate `82a160e68` + `997a195c1`, 2026-11-08): the type of a local `var` declared without one, of
     each `:=` name and of a range loop's hidden operand copy comes from the checker — Decl.ResolvedTypeID, a new
     record on each `:=` name (defineShortVar), the operand's record — and IR-gen's inference and the int default
     are gone (user 2026-11-08: "From the checker"); a local constant IR-gen does not register is an internal error.
     irgen's test helpers now check every test program first (`82a160e68`).  --emit-llvm byte-identical (2730
     single-file programs, host and arm32-linux; 358 multi-package; cmd/bnc's 53 packages), native objects identical
     on aa64 / x64 / arm32.  It fixed `u := 2.5` boxed into an interface (a compile crash; conformance spec/09 181)
     and `var s = Box[int].Get`; `s := Box[int].Get` was fixed after it (binate `710e52d5d`: genShortVarOne lowers its
     initializer against the variable's type too).
     The checker also records each multi-value `:=` name's type, which genShortVar's multi-value path does not read
     yet (it uses lookupFuncResults / multiReturnFieldTypes).
     4d3 DONE (binate `90e674c4b`, 2026-11-09): a type assertion's recovered type and a composite literal's type come
     from the checker's record of the expression (assertedType, compositeDeclType); a global's composite-literal
     initializer takes resolveGlobalVarType's general checker path.  The differential found the only syntax readings
     at these sites to be six inferred-length literals (`[...]T{…}`), now the literal's `[N]T`; the checker no longer
     stamps a `[...]` length, and reading one from syntax, or meeting an unstamped array-literal key, is an internal
     error.  --emit-llvm byte-identical (2734 single-file programs on the host, 2732 on arm32-linux; 363
     multi-package; cmd/bnc's 53 packages).
     4d4 DONE (binate `ad052a8f3`, 2026-11-10): a package variable declared without a type takes the checker's
     Decl.ResolvedTypeID (resolveGlobalVarType; an unrecorded one is an internal error); the literal-kind inference is
     left only for builds without a checker (stage 7 deletes it).  --emit-llvm byte-identical (2736 single-file
     programs on the host, 2734 on arm32-linux; 364 multi-package; cmd/bnc's 53 packages).  Landed with it (binate
     `3bd6572f3`): a method call's receiver type name comes from the checker's record of the receiver
     (methodRecvBaseTypeName), so a value method called on a named array or slice literal names its type's method.
     That completes the stage 4 work list; still open from 4d2c: genShortVar's multi-value path reads
     lookupFuncResults / multiReturnFieldTypes, not the multi-value `:=` records.
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
   Increments — IN PROGRESS (work-6, 2026-11-10; proposed from a survey of the registration sites):
   - 5a DONE (binate `9b990f0a1`, 2026-11-10; user: "yes"): the checker records the Type each type declaration declares — `Decl.ResolvedTypeID` on a DECL_TYPE
     (struct, distinct, alias, interface, generic), as DECL_VAR / DECL_FUNC already have — so IR-gen holds a
     declaration's identity without its name.  Check unit tests; no IR change.  A blank `interface _` records
     none (the depth check never measured one); checkTypeDepths' declaredTypeOf reads the record.
   - 5b DROPPED (user 2026-11-10, answering whether to drop it: "Key by checker's name (Recommended)"): no
     memo keyed by checker Type pointer.  The checker's identity for a named type, named struct or interface is
     its qualified name plus any instance arguments (types identicalStep: NamedIdentityName, (Pkg, Name), the
     struct's Name), not the object -- it makes two interface Types for one package interface (the `.bni`
     load's and preRegisterInterfaces').  So IR-gen's registries stay keyed by qualified name, taken from the
     checker's Type (5c, 5d), not from `d.Name` or the written text; a REPL shadow is a distinct type through
     plan-repl-type-shadowing.md approach B's internal name.
   - 5c: declarations register from their record.  Seven per-driver passes register named structs, named
     types (preRegisterNamedTypeDecl / registerTypeDeclEntry) and interfaces (collectInterfaceFromDecl) by
     `d.Name` (gen_module, gen_module_single, gen_module_register, gen_register_import, gen_import,
     gen_self_types, gen_repl_types).  Each maps the declaration's Type instead and registers it under the
     checker's (Pkg, Name), with fields, underlying, method set and parents from the checker type; the mapper
     registers a type it meets unregistered the same way, so no order leaves one nil.  Declaration order is
     kept (Module.Structs order is emission order), so the IR stays byte-identical.
     Note (review of 5a, 2026-11-10): a `.bni`'s type and interface Decls are the same nodes in pkg.BNI and
     pkg.Merged, so LoadPackageInterface records them and CheckPackage then overwrites the record with the
     implementation scope's type.  For an interface that is a different Type object from the one importers
     see (preRegisterInterfaces makes a fresh one and replaces the copied symbol); both name the one registered
     interface by its (Pkg, Name).
     5c-1 DONE (binate `612fd477a`, 2026-11-11; user: "wait 19 minutes, then land"): every type / interface registration site takes the declaration's name
     from its record (irgen declaredTypeName: the checker Type's bare Name), not `d.Name`; byte-identical IR.
     Aliases, blank declarations and declarations with no record keep `d.Name`.  5c-2: one registration routine
     for the passes, fields / underlying / method sets from the checker type, lazy registration -- MOVED to after
     stage 7 (user 2026-11-11, asked whether to merge now or after stage 7: "After stage 7 (Recommended)").  Five
     passes are production (registerStructTypesFlat + registerAllInterfacesFlat, RegisterImports,
     RegisterSelfTypes, GeneratePackage, the REPL's GenTypeDecls); GenModule and RegisterImport are test-only.
     Most of their differences are the per-file import overlays (value names, stage 6; unrecorded syntax) and
     the checker-less paths (stage 7), so the merge is simpler once those are gone.  Until then a change to
     declaration registration touches every pass, as 5c-1 did.
     Notes from the 5c-1 review: (a) the no-record fallback exists only for IR-gen unit tests that register
     unchecked declarations with a checker attached (gen_repl_types_test, gen_repl_test, gen_repl_forward_test,
     ...; a strict-check run of VM conformance and e2e/repl.sh found no production path) -- once stage 7
     converts them, a missing record with a checker attached should be an internal error (REPL plan refinement
     3: loud misses); (b) declaredTypeName drops the qualifier, so a checker / IR-gen package disagreement would
     surface only as a later irNamedFromChecker / irStructFromChecker miss -- an assertion that the qualifier
     matches the registering path would catch it at registration; (c) aliases stay keyed by `d.Name`
     (RegisterModuleTypeAlias keeps the first entry for a name), and methods, impls, generic stashes and
     interface parents (collectInterfaceParents by ref.Name) by written names -- 5c-2 / 5d / 5e.
   - 5d: emitted names from the checker's identity: methodQualName (the receiver TypeExpr's record, not
     recvTypeName's text), the dtor / copy helpers, impl rows and typeinfo name what the record maps to.
     Byte-identical until a checker makes two types of one name (plan-repl-type-shadowing.md approach B).
     5d-1 DONE (binate `009892417`, 2026-11-11; user: "wait 16 minutes, then go ahead"): methodQualName names a method after its receiver's checker type
     (methodRecvTypeName: concreteRecord -> ReceiverBaseNamed -> checkerBareName); byte-identical IR.  Thunks,
     REPL replace / shadow and method-value wrappers derive from that name or the registered type, so they follow.
     5d-2: impl rows -- recvBaseNameAndPkg (gen_impl.bn, gen_impl_imported.bn) -> the receiver's record; every
     backend rebuilds method symbols from row.RecvTypeName, so the row is the one point to change; the listed
     interfaces' names from their records too.  Notes from the 5d-1 review: (a) a method expression `T.M`
     (methodExprName, gen_bare_name.bn; funcRefName, method-value routing, declareMethodExprTarget) still names
     from `e.X.Name` and gates on lookupStructIdx(written name) -- once a shadow has an internal name it would bind
     the OLD T's method; switch it to the record (concreteExprRecord on e.X) before / with stage 8's internal
     names (stage 6 lists methodExprName among the value-name sites); (b) stage 8's internal names must contain no
     '.' (checkerBareName cuts at the last one).
     5d-2 DONE (binate `536c5d18c`, 2026-11-11; user: "wait 3 minutes, then land"): implRecvKey / implIfaceKey (gen_impl_keys.bn) key both impl collectors'
     rows on the receiver's and interfaces' records (the receiver as IR-gen registers it -- a generic instance's
     mangled name, not the checker's `R[[8]uint8]`); byte-identical IR.  `impl T : any` now keys on the universe
     `any` and builds (it failed on LLVM / native) -- user 2026-11-11, asked whether such an impl is allowed or
     rejected: "Allowed, a no-op (Recommended)"; conformance 1744.  5d-3a DONE (binate `4433296d6`, 2026-11-12;
     user: "yes, go ahead; then continue"): interface parents from records (ifaceRefKey), and a listed universe `any`
     left out of parents and impl lists (isUniverseAnyKey), a true no-op; conformance 1746; the claude-todo item is
     done.  5d-3b DONE (binate `bfec871b2`, 2026-11-13; user: "wait 15 minutes, then land"): an interface
     alias's target (mi.AliasTarget) comes from its record (d.ResolvedTypeID), the written name only without one;
     an alias whose record is a generic instance, also through another alias (aliasOfInstance), stays a pending
     stub, instantiated late as before (conformance 1383 caught the alias-of-an-alias case); byte-identical IR.  An
     alias of a package's own interface named `any` now targets it, not the universe `any`.  Re-check
     (2026-11-13): the per-increment "byte-identical IR" comparisons above (--emit-llvm) saw only each program's
     main module and cmd/bnc's packages, never a test's own packages, the runtime or the stdlib.  Comparing every
     module's object (bnc -c; scripts/codegen-ab.sh) between current main and main with 5a..5d-3b reverted:
     single 2747 SAME / 713 BOTHFAIL, multi 367 / 125, cmd/bnc SAME; the only other results are 1744 and 1746,
     which the old compiler cannot build (a listed `any` keyed on the package's own name).  Note from the 5d-2 review: stashedGenericDecl matches the checker's
     InstDecl by identity, so a REPL re-declaration of a generic interface (stashGenericIfaceDecl keeps the first)
     would panic there -- unreachable while the checker rejects redefinitions; stage 8 must handle it.
   - 5e: impl rows from the checker's impls with checker substitution: mintGenericImplRows stops resolving a
     parameterized impl's interface arguments by name, and resolveTypeExpr's abstract-record fallback (4a)
     goes.
     5e-1 DONE (binate `5d9459712`, 2026-11-14; user: "go ahead and land, then continue (and go ahead and take on
     that change in 5e)"): mintGenericImplRows keys each interface on the checker's copy of the impl for the
     instance (ifaceRefKey) and leaves out a listed universe `any`, which failed to build; every module's objects
     unchanged (codegen-ab.sh) but the new 1747.  5e-2: the abstract-record fallback becomes an internal error.
     A type-differential sweep (2026-11-13) over every conformance program and cmd/bnc found abstract readings
     only in 305_type_depth_nested_syntax, as isAbstractType counts a type nested past 32 levels as abstract; it
     is to stop at named types (an instance's abstractness is in its type arguments) and drop the cap -- taken
     from the deep-nesting todo entry (user 2026-11-14: "go ahead and take on that change in 5e").
     5e-2 DONE (binate `d7f9c914e`, 2026-11-14; user: "wait 4 minutes, then go ahead"): resolveTypeExpr,
     typeArgExprToType and genericInstanceName map every record, and an abstract one is an internal error
     (requireConcreteRecord, gen_type_record.bn); a parameterized impl with no checked copy is one too
     (genericImplRefs).  isAbstractType walks to any depth (into an instance's type arguments, not a named
     type's definition) and visits each type once.  The 32-level cap had sent DAG-shaped types (`type A_i =
     struct { x A_{i-1}; y A_{i-1} }`, and the same with `*func(A_{i-1}, A_{i-1})`) to the linear by-name
     reader; without it they took exponential time (98 s at 26 levels), so the record mapper also maps each
     alias once per mapping (irAliasFromChecker).  Every module's objects unchanged (codegen-ab.sh); the
     self-compile runs 0.09% more instructions.  With it, stage 5 is done apart from 5c-2 (after stage 7).
     Syntax with no record is still read by name.
6. **Value-name resolution.**  The checker records what each identifier / selector Expr resolves to (local,
   parameter, global, constant, function, package, method expression); IR-gen uses it; then delete
   CurrentImportPkg-as-resolution, the import overlay for generic bodies, RegisterGenericBodyDeps and the
   bare-name precedence rules.  (Coordinate with plan-constant-evaluator.md, which targets IR-gen's own
   constant evaluator and its CurrentImportPkg const lookup.)
   Increments (2026-11-14, work-6), each with byte-identical code where it claims no change:
   - 6-1 DONE (binate `84784aa31`, 2026-11-14; user: "yes, go ahead. Let's do the proper fix."; with the
     expected-fail conformance tests 1748-1750 in `baf40f6f9`): the record.  checkIdent and checkSelectorExpr's `pkg.X` keep the Symbol they resolved to in a side
     table beside ExprTypes (a method expression's `T` is already stamped as a type); a package-level Symbol
     gets its declaring package (set where collectDecls / the .bni loaders define it; HomePkg already gives an
     exposed one's home); a query `ExprSymbol(e)` with accessors (kind, declaring package or "" for a local,
     name).  Checker unit tests; no consumer.  An instance body's records are its checked copy's
     (InstanceBodies), so a generic body's names come out in its declaring package with no overlay.  From its
     review: a declaration can have more than one Symbol over a check (a typed package variable is defined at
     collection and again in pass 2), so 6b's hidden names need the declaration's identity, not the Symbol
     handle; `_func_handle` / `__c_entry` operands are recorded too; a generic function's name in an
     instantiation (`G[T]`, `pkg.G[T]`) resolves to a generic declaration and has no record, nor `__Package`.
   - 6-2 DONE (binate `ed9921bf3`, 2026-11-14; user: "wait 16 minutes, then land"): `--x-value-differential
     <file>` on bnc and bni notes each place IR-gen's by-name answer for a value name differs from the record
     (or the checker recorded none): identifier reads, the identVar / identVarType lookups (now given the
     identifier), the value-hiding checks (identValueBound), the constant lookups, closure captures, the
     `_func_handle` / `__c_entry` operands, and `pkg.X`'s package (a package qualifier now records its
     import's Symbol).  Not compared yet: function references by bare name, method expressions, generic heads,
     and which local a `local` answer means -- 6-4 and 6-5 add their sites to the differential before
     switching them.  Sweep (every conformance program and cmd/bnc's 53 packages under bnc, the programs under
     bni): the only differences are 1748 (bnc and bni), 1749 (bnc; bni cannot run `__c_entry`) and 1750.
   - 6-3 DONE (binate `eca8e280f`, 2026-11-14; landed on "wait 16 minutes, then go ahead", after the MAJOR capture
     fix `8e388117e` its review found, user: "Fix it first (Recommended)"): identifier reads from the record --
     genExpr's EXPR_IDENT, identVar / identVarType, identValueBound and the constant lookups (isPackageConstIdent,
     irgenEnv.Const, isFloatExpr): a function's variable or constant from the innermost binding of its name, a
     package-level one by its registered name (bare in its own package, else qualified with its declaring
     package); a binding or registration that disagrees with the record is an internal error.  A closure's
     captures are looked up among the enclosing function's variables only (localVarIdx): the checker captures no
     package-level variable, an instance body's included (the capture fix, conformance 1751).  A floating-point
     package constant is the last registered under its name (genExpr took the first).  Syntax with no record is
     still read by its spelling.  Sweep: object code identical to the capture fix's for every conformance
     program and cmd/bnc; the differential's only notes are 1748 / 1749 (6-4) and 1750.
   - 6-4 DONE (binate `017f68dde`, 2026-11-16; user: "wait 8 minutes, then land"): function references -- an
     identifier or `pkg.F`, called or taken as a function value (funcRefName / funcRefTarget, which namesFunction,
     genExprOrFuncRef and the declaration forms use), the operands of `_func_handle` / `__c_entry`
     (builtinFuncOperand) and a package's members `pkg.X` (pkgMemberQualName; a constant's package, memberPkgPath)
     -- from the record: by the declaring package, bare in the module's own.  A record of anything but a function
     makes the name no function reference; a recorded function the module has no signature for is an internal error.
     Fixes the MAJOR "a generic body's `_func_handle(f)` / `__c_entry(f)` names the instantiating package's `f`"
     (decided 2026-11-14, user: "Let's do the proper fix."; no stopgap): 1748 extended to a generic method and a
     nested generic, 1749 expected-fail only in its four interpreter modes.  Syntax with no record is still read by
     its spelling and noted under the differential (a bare `__Package` excepted, as `pkg.__Package` is).  The
     differential's member note now fires only for a member recorded as no package-level symbol -- the 1750 shape,
     covered by 1750 and the sweep only.  Sweep: object code identical to main's for every conformance program and
     cmd/bnc except 1748's and 1749's main.o; the differential's only notes are 1750's.
   - 6-5: method expressions `T.M` (methodExprName, the 5d-1 review note (a)) from their records, and generic
     heads (instantiationHead), which need a record of their own: the generic declaration a head resolves to.
     Method expressions DONE (binate `d6a44b6c3`, 2026-11-17; user: "wait 4 minutes, then go ahead; then continue"):
     a method expression names the method of the type the checker records for its `T` (recordedMethodExpr; an alias
     resolved to its target), the record tells it from a method value (namesMethodExpr), and a selector whose base
     the checker records as a type is no package's member (pkgMemberQualName).  Fixes the MAJOR "a type alias named
     like a single-segment package names that package's function" (1755, two e2e/repl.sh sessions) and, folded in
     (user: "Fold it in"), `present(T.M)`'s internal error (genMethodExprValue, 1756).  A `T` with no record is read
     by its spelling and noted under the differential.  Sweep: object code identical to main's for every conformance
     program but 1755 and 1756, and for cmd/bnc; the differential's only notes are 1750's.
     Generic heads DONE (binate `61670604f`, 2026-11-17; user: "wait 4 minutes, then land"): the checker records on
     an instantiation's head the generic function it names, and on a qualified head's package name the import
     (stampGenericHead, in checkInstantiateOrIndex); instantiationHead looks the generic up from that record by its
     declaring package and name (recordedGenericHead), so a head recorded as anything else (an indexed variable) is
     no instantiation.  A head with no record is read by its spelling and noted under the differential.  Sweep:
     object code identical to main's for every conformance program and for cmd/bnc; the differential's only notes are
     1750's.  6-5 DONE.
   - 6-6: delete CurrentImportPkg-as-resolution, overlayFileImports, RegisterGenericBodyDeps and the bare-name
     precedence (moduleNamesInScope, definingPkgType, bareGlobalIdx's rules).  Syntax with no record still
     reads by name until stage 7.
     Survey (2026-11-17, work-6: a temporary note at each lookup by spelling, the differential sweep over every
     conformance program and cmd/bnc's packages under bnc, and the programs under bni): with a checker, a lookup by
     spelling is reached only for (a) IR-gen's own syntax -- the package init's assignments (buildInitBody), the call
     of a generic instance by its mangled name, a receiver's `_` binders (`_#0`, also in another package's generic
     method, where it reads CurrentImportPkg); (b) checked syntax that a query asks about and that is not what it
     asks about -- instantiationHead on an index whose base is no name (3,177 of 4,039 inputs), pkgMemberQualName on
     a method or field selector (`v.M`, v a variable), genericInstanceName on an index -- and `__Package`, which has
     no record by design.
     6-6a: the record decides for checked syntax (no record answers the query), and IR-gen's own syntax is resolved
     directly (an instance call names its instance; `_` binders get records), so that with a checker no lookup by
     spelling runs in another package's code (CurrentImportPkg set).  6-6b: delete CurrentImportPkg-as-resolution,
     the import overlays (instances, impls, per declaration) and the precedence rules; their white-box tests go
     (decision 5).
     RegisterGenericBodyDeps is not only resolution: it declares the functions (signatures and externs), variables
     and constants of the packages a generic body reaches that the consumer does not import, and a recorded reference
     needs them (a recorded function with no signature is an internal error).  The drivers' scan of those packages
     also feeds RegisterImportedImpls (their vtables).  Decided 2026-11-17 (user: "Keep, drop resolution
     (Recommended)"): keep it as that declaration pass, dropping only its resolution role (CurrentImportPkg, the
     per-declaration overlay); declaring on demand from the records is revisited with 5c-2's lazy registration.
     Its review found that a package's `__Package` accessor is declared only for direct imports with declarations
     (MAJOR todo entry); fixed first (user: "Fix it first (Recommended)"), 6-6a rebased onto the fix.
     6-6a DONE (binate `36cf4d94e`, 2026-11-18; user: "wait 15 minutes, then land"), landed after the accessor fix
     (binate `04d83b376`): the checker records `__Package` (packageAccessorSymbol: bare, the package whose code is
     checked; `pkg.__Package`, the import's); a checked expression with no record (checkedExpr) is no generic
     function's instantiation, package member or generic type instance; a generic instance's call names the instance
     (genNamedCall); an instance method's receiver keeps its record.  Object code identical to the fix's for every
     conformance program and cmd/bnc (1750 fails either way); the differential has no note left (1750's went with
     it).  6-6 DONE: 6-6b is folded into stage 7 (user 2026-11-18: "Fold into stage 7 (Recommended)"): with a
     checker, CurrentImportPkg, the import overlays and the bare-name precedence are no longer consulted (a probe
     sweep after 6-6a, every conformance program and cmd/bnc's packages under bnc: no lookup by spelling in another
     package's code); they serve only IR-gen without a checker, on which about 25 irgen unit tests (12 files) depend,
     mostly ones registering another package's declarations.  A draft of the CurrentImportPkg deletion (31 files) is
     archived on the binate repo's branch `archive/20261118-irgen-delete-current-import-pkg`.
6b. **Decision 7: a generic keeps the bindings of its declaration** (user 2026-11-08: "yes, follow the plan" — after
   stage 6, on its records).  An instance's check and its IR use the generic's declaration-time records, never the
   live session scope: a generic method's sibling keeps calling the old (shadowed) method, a generic body keeps the
   function or variable it named when a REPL redefinition shadows it, and a shadowed generic stays instantiable.
   Needs: shadowed functions and generics reachable by hidden names in IR-gen and the VM (as shadowed variables are;
   the VM now unbinds a shadowed function, reachable only through callers' cached pointers), and instance names that
   do not collide with the new definition's (stage 5's naming).  Signatures are compared at the generic level (`Get()
   T` → `Get() int` shadows, also for `Cur[int]`; user 2026-11-08).  Remove with it the use recording in the bodies
   of the instances a parked REPL group names (binate `38b4fcd11`: recordingUses' InstanceState.RecordUses /
   NewBodies / BodyIsNew, InstWork.Requeued): a body then binds what it bound at its declaration, so a parked
   declaration has nothing to wait for there.  Resolves the todo entries "an incompatible redefinition of a generic
   method that a sibling still calls" and "adding a method to a generic type checks its existing instances again"
   (their sessions become e2e cases).
   Design (2026-11-18, work-6; a Plan agent's design, its key claims checked in the code): today a generic's home
   scope (recordGenericHome's h.Scope, recordGenericMethod's entry.Scope) is the live session scope, which
   Scope.Define overwrites in place, and a sibling's `c.Get()` selects through the receiver type's current method set
   (lookupVisibleMethod); clones keep no records (ast_clone.bn), so reusing the declaration-time records needs a node
   correspondence there is none of, and a frozen scope snapshot per generic would miss same-signature replacements
   (which must reach old bodies, as they do today) and methods.  Recommended: versioned bindings.  Each REPL input is
   a generation (the main file 1); the session scope stamps a symbol's generation and, when a value name (variable,
   constant, function) is bound again by another generation, keeps the old symbol in a history with its hidden name
   `__repl_shadowed_<gen>_<name>` (`__` names are reserved, so unspellable); a same-signature function (or
   identical-type variable) merges instead of shadowing, so old views see the replacement.  A prompt generic's home
   becomes a view of the session scope at its generation, through which every instance check (body, signature,
   constraint, method copy) resolves names as at its declaration.  Methods get versions keyed by T.M: a superseded
   method stays a member of the type and its instances under its hidden spelling, and lookupVisibleMethod selects,
   through a view, the version live at the view's generation; the checker records the selected method on method
   selectors, which IR-gen names the callee from.  The checker answers what a shadowed declaration is registered as
   (RegisteredName / RegisteredDeclName, by (name, generation), not the Symbol handle); IR-gen renames what it
   registered for the old declaration (global, constant, FuncSig, a generic's emitted instances, method-value
   wrappers) instead of forgetting it, keeps a shadowed generic declaration stashed, and names an old generic's
   instances after its hidden name; the VM renames the old function (Vm.RenameFunc) instead of unbinding it.
   Rollback (an undone input) truncates the history.  Constraint calls stay live (the type argument's impl).
   Increments: 6b-1 Vm.RenameFunc (no behaviour change).  6b-2 checker generations, versions, merges, rollback and
   the queries, computed but not consulted (object code byte-identical).  6b-3 value names follow the declaration
   (views for function and method homes; IR-gen RegisteredName, recordedGenericHead by declaration identity, hidden
   instance names; renames for variables, constants, functions, generic functions); e2e s3, s4 and the variable /
   constant / function / generic-function cases.  6b-4 the method-selection record and IR-gen's use of it
   (byte-identical).  6b-5 method versions (hidden members, view-aware selection, renames of method instances,
   wrappers and functions; EmitShadowedMethod, ShadowReplGenericMethod and UnbindFunc go); e2e s1, s2.  6b-6 delete
   the use recording of binate `38b4fcd11` (InstanceState.RecordUses / NewBodies / BodyIsNew, InstWork.Requeued) and
   rewrite its tests; close the two todo entries.  Risks: manual scope walks that would see a view as an empty scope
   (check_capture, check_instance_lenders, bni_view, bni_scope_demand), method lookups that bypass
   lookupVisibleMethod, hidden members in listed method sets, stage 8 re-keying the method versions by type identity.
   Decided (user 2026-11-18, all four as recommended): generic type, interface and alias declarations naming
   constants (`[N]T`) resolve through the declaration view too ("Yes, view too"); a superseded generic-type method is
   carried, checked and emitted for every instance ("Carry for every instance"); a shadowed constant warns, as a
   variable does ("Yes, warn"); constraint calls stay live ("Live").
   6b-1 DONE (binate `f9d8731be`, 2026-11-18; user: "yes, go ahead"): Vm.RenameFunc (refuses a bound new name,
   fails loud on a broken index key; the index's invariant docs say which paths free a Name a key borrows).
   6b-2 DONE (binate `540f072ae`, 2026-11-19; user: "wait 4 minutes, then land"): generations (a prompt
   declaration, a const-group member, a retry, a mid-session import), the versioned session scope with its
   history, the function and variable merges (a variable records its merge, so both its symbols answer), the
   undo of a name's versions (restoreName), and the queries ReplHistoryLen / ReplShadowsSince / RegisteredName /
   RegisteredDeclName (a generic's by the generation its home records).  Its review: the REPL's hidden names
   (`__repl_shadowed_<InitCounter>_`) and the checker's (`__repl_shadowed_<gen>_`) share a prefix -- 6b-3 must
   switch the REPL to the checker's at once, not run both.
   6b-3a DONE (binate `110b8f9eb`, 2026-11-19; user: "yes, go ahead"): the REPL renames what a prompt declaration
   or a mid-session import shadows to the checker's hidden name (check.ShadowedBinding; Repl.InputGen keeps a
   replacing redefinition from reporting an older input's shadow): a variable's global, a constant (with the
   warning), a function and a generic function's instances (IR-gen's ShadowReplFunc, and Vm.RenameFunc at commit);
   ShadowReplGenericFunc goes, and an import now shadows a function it takes the name of.  Methods are still
   forgotten and unbound (6b-5).  A follow-up (binate `824dfad0f`, 2026-11-20; user: "let's fix it now") renames
   back exactly what a failed shadowing declaration renamed (a generic's instances stayed renamed, and the next
   use aborted the REPL).  6b-3b: the views and IR-gen's naming (RegisteredName, recordedGenericHead by
   declaration identity, hidden instance names), in three commits.  C1 DONE (binate `26767b035`, 2026-11-21;
   user: "wait 4 minutes, then go ahead"): IR-gen names a recorded symbol of its own package by its registered
   name (recordedSymName), so a CONST_EXPR constant's re-lowered initializer reads the shadowed constant (the todo
   MAJOR "a floating-point constant computed from another constant ..." is done).  Its review found that a prompt
   group declaring a name twice was taken member by member for a redefinition (wrong values; a panic with C1);
   fixed ahead of C1 in the same landing (binate `93c8f4048`; user: "yes, fix it now"): such a group is rejected,
   as in a file (CheckDeclInScope's rejectRedeclaredMembers; Checker.CheckGroupNames for the REPL's split var and
   type groups).  C2 DONE (binate `eaa04b866`, 2026-11-21; user: "wait 4 minutes, then land; then continue"): the
   universe scope moves from scope.bn to universe.bn, unchanged.  C3 DONE (binate `25608c022`, 2026-11-21; user:
   "wait 8 minutes, then go ahead"): views of the session scope as of a generic's generation (replView,
   lookupAtGen) for the homes of generic functions and of generic types' methods and for generic type, alias and
   interface declaration scopes; IR-gen finds a called generic by its declaration (stashedGenericFunc, following
   GenCtx.ReplacedGenericFuncs to the declaration that replaced one with the same signature), keeps a shadowed
   generic stashed and names its later instances by its hidden name.  binate `38b4fcd11`'s e2e case now prints 1001
   (group-instance-body-keeps-declared-names).  Left by C3's review: a prompt declaration still parks on a parked
   redefinition its generic's view never sees (the use recording, which 6b-6 deletes); and the todo MAJOR "REPL:
   an import that rebinds a package name changes what an earlier generic calls" (import names are not versioned).
   6b-4 DONE (binate `9afef5b56`, 2026-11-22; user: "wait 8 minutes, then go ahead"): the checker records the
   method a selector selects (Checker.ExprMethod: method calls, method values, `T.M` and `Box[int].M`
   expressions, an instance body's calls through a constraint), and IR-gen's method calls and method values take
   the method from it (selectorMethod; a missing record is an internal error); the callee is still named by its
   name.  Emitted IR byte-identical (cmd/bnc and the 318 conformance programs that declare a method).
   6b-5 design (2026-11-22, work-6), in two commits.  Probed with a bni of main `9afef5b56`: a generic function's
   `t.M()`, method value `t.M` or method expression `T.N(*t)` on a non-generic T fails in an instance checked after M
   is redefined with another signature ("wrong number of arguments (in call[bool] ...)"), and s1 fails the same way
   for the generic type's own sibling; s2 passes since C3 (Cell[int].get keeps the old g).  Checker: a method
   carries the generation declaring it (types.Method.Gen; a same-signature redefinition takes the old one's, as a
   function does).  One of another signature from another generation supersedes it: the superseded Method is renamed
   in place to its hidden name (`__repl_shadowed_<gen>_<name>`) and stays a member of the type, and the session
   keeps a method version (its owner -- the named type, or a generic type's declaration --, name, Gen, Until, hidden
   name).  lookupVisibleMethod, in a view (a scope chain rooted at a replView), selects the member live at the
   view's generation: the hidden one of the version with Gen <= gen < Until.  A call through a constraint stays live
   (it looks up by name).  A record (ExprMethod) is the member selected, so one taken before the supersession names
   the hidden member too.  Rollback renames back and drops the version.  IR-gen names a method call's callee, a
   method value's wrapper and a method expression from the recorded method's Name (the selector's spelling without
   a record): nothing changes for bnc.  The REPL renames the superseded method's function, value-receiver thunk and
   method-value wrappers to the hidden spellings, in IR-gen when the redefinition is generated and in the VM
   (Vm.RenameFunc) when it is kept, instead of shadow-lowering and unbinding them.  6b-5a: methods of non-generic
   types (e2e: the probe above).  6b-5b: methods of generic types -- the superseded GenericMethodEntry stays
   recorded under its hidden name; an existing instance's copy is renamed in place instead of dropped; IR-gen keeps
   the superseded declaration stashed and emits its instances under the hidden name, renaming the instances and
   wrappers already emitted; ShadowReplGenericMethod, EmitShadowedMethod and Vm.UnbindFunc go; e2e s1, s2.
   Decided (user 2026-11-22, "Only where selected", replacing 2026-11-18's "Carry for every instance"): a
   superseded version is copied onto an instance formed later -- and checked and emitted there -- only when code
   declared before the supersession selects it, and the infinite-family check reads only the type's current
   methods.  Carried on every instance, a growing version (`Wrap() Box[Opt[V]]`) once defined made the type an
   infinite family for the rest of the session, even after Wrap was redefined back (check's
   TestExpandVerdictFollowsRedefinition: "generic instantiation Box[int] names infinitely many instantiations").
   6b-5b DONE (binate `564d1aa1a`, 2026-11-22; user: "wait 12 minutes, then land"): as designed, "Only where
   selected" -- a selection of a superseded version on an instance formed since copies it there and checks the
   instance again (carrySupersededMethod; an instance's own bodies' selections by a further pass of
   checkTypeInstance), and IR-gen emits it only where checked (hasInstanceCopy).  The new method is still emitted
   at the redefinition for the existing instance types (EmitSupersedingMethod), so its load check runs there.
   Its review found a method expression on an instance (`Cur[X].Get`) named by its spelling -- the new method
   called with the old arguments -- fixed in it: named by the checker's record, as calls and method values are.
   6b-5a DONE (binate `a9ed7ebeb`, 2026-11-22; user: "wait 4 minutes, then land, then continue"): as designed
   for non-generic types, except that the REPL still lowers the redefinition as a shadowing group
   (shadowingFuncs / LowerFuncsShadow) after renaming the superseded method's function, thunk and wrappers (only
   their unbinding goes).  A superseded member is neither selectable nor bound by its hidden name, and selecting a
   version notes no use (it never waits on a parked redefinition).  Its review found that a call through a
   constraint, looking up by name, was rejected in an instance made after a supersession while the impl still
   bound the old method -- the todo MAJOR, fixed in the same landing (binate `d360164ab`; user: "yes, let's do
   that stacked on your current commit"): such a call calls the method bound by the impl that makes the type
   argument satisfy the constraint, the version of the impl's generation (Impl.Gen; the selector records its
   receiver's type parameter, ast.Expr ConstraintParamID, in place of ViaConstraint).  So "a call through a
   constraint stays live (it looks up by name)" above now reads: it calls what the impl binds, as "Live" decided.
   The import-name MAJOR C3's review left is fixed (binate `c2401ceb2`, 2026-11-22; user: "yes"): an import's name
   is versioned as a variable's is (SYM_PKG in the session's history; an import of the package the name already
   names leaves it bound), the main file's imports are bindings of generation 1, and the REPL warns when an
   import's name is taken.  IR-gen needed nothing: it names a qualified reference from the checker's record.
7. **Delete the legacy and no-checker paths; convert the tests.**  With them, what 6-6b would have deleted:
   CurrentImportPkg, the import overlays (instances, impls, per declaration) and their alias-map snapshots, and the
   bare-name precedence rules.
   Then 5c-2 (stage 5's merged, lazy type registration).  ~84 irgen test files build IR without a
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
  type's), but the IR type misstates the source.  Decided: intern by IdenticalStrict (stage 2 above) —
  LANDED binate `22e7c18b4` (2026-10-18), with field-load forwarding no longer taking an anonymous struct for a
  distinct object (a pointer to one converts to one adding readonly; review found the miscompile, 1704).
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
