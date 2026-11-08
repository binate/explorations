# Plan: deep nesting -- iterative type walks, and nesting limits

Status: decided 2026-11-03 (work-1); stages types and types/abi done, stage check next.  claude-todo.md: "Deep nesting -- of types or of expressions -- is bounded only
by the compiler's stack".  User (2026-11-03): "Let's rewrite them as loops, since that seems better regardless. We
may *also* want to impose an implementation limit (in case some path leads to recursion)".

## The problem

A deep enough nesting exhausts the compiler's stack instead of getting a diagnostic, and spec §21.4
(`conf.implementation.limits`) names no nesting limit.  Two kinds of depth, with different sources:

- **Semantic type depth** (`types.Type` graphs): a chain of named types, structs holding the next struct by value,
  pointers to the next pointer type, arrays of arrays.  It needs no deep SOURCE nesting: 10,000 one-line
  declarations, each over the next, make a 10,000-deep type.  So only the walks themselves (or a limit computed on
  the types) can bound it.
- **Syntactic depth** (the AST: expressions, statements, blocks, type expressions): `((((x))))`, `[1][1]...[1]int`,
  nested function literals.  It is the source's own nesting, so a limit the parser enforces bounds every pass that
  walks the AST.

## Inventory (2026-11-03, a script: functions calling THEMSELVES on a type's / node's parts -- a lower bound;
mutual recursion such as sizeOf <-> structLayoutOf is not counted)

Semantic-type walks (self-recursive on Elem / Underlying / Target / fields / params / results / instance args):
81 by the script, minus the type-EXPRESSION walks it also matched (collectTypeExprNames, collectTypeExprConstDeps,
typeExprNames, copyTypeExprStamps, typeExprBareNames, walkTypeRefs, ...), which are syntactic.

- check (36): peelNamedKeepReadonly, funcSignaturesSized, aliasIfacesKept, expandWalkType, substituteSelf,
  ensureMembersIn, containsByValueTypeParamIn, typeHasNodeAt, typeMentionsIota, partsMayBreak, typeMentionsNamed,
  typeMentionsParam, containsDependentArrayDepth, identicalUpToDependentDepth, AggregateComparable, AssignableTo,
  prepareLayoutIn, typeReachesNamedByValue, holdsItselfIn, collectLiteralTypes, instanceNotBuiltOf, ...
- codegen (14): llvmType, discoverStructFromType, typeBits, isUnsigned, cabiIntExtAttr, emitStoreSSARec,
  isFloatScalarParam, floatSlotIsI32, is64ScalarUnderlying, dbgTypeKey, emitLoadSSARec, countAggregateLeaves,
  emitCopyRec, emitZeroRec
- irgen (8): irTypeFromChecker, withNamedBase, aggEqLeaves, completeIfaceAliasStubsIn, mergeQualifiedReadonly,
  isAbstractType, innermostStructType (+ typeExprBareNames, syntactic)
- types (5): MentionsSelf, sizeOf, alignOf, Identical, IdenticalStrict
- types/abi (5): hfaFold, sysvMarkNonFloat, sysvFloatBits, sysvLeafCount, sysvLeafWidthAtBase
- irutil (3): dtorTypeSuffixRec, ReceiverBaseTypeName, MangleTypeArg
- ir (2): StripConstForIR, CanonicalIfaceType;  vm (2): collectIfaceVtOffsets, holdsIfaceValue
- lint (2): walkTypeRefs (syntactic), rhtaType;  irbuild, iropt, interp, native/arm32 (1 each)

AST walks: 75 self-recursive by the script (check 31, lint 22, irgen 10, format 6, ast 4, ...), and the parser,
the checker's expression checks and IR-gen's expression lowering are recursive descent throughout (mutually
recursive, so far more than 75).

## Approach (proposed)

1. **Semantic-type walks become iterative** (explicit work stacks), package by package, each keeping its visited /
   memo / path semantics exactly.  Order: types (layout, identity) -> types/abi -> check -> irutil / ir / irbuild ->
   irgen -> codegen -> native -> vm / interp -> lint.  Each stage: convert, then a test of that stage's walks on
   types far deeper than a stack allows (a 100,000-deep named chain, struct-in-struct chain, pointer chain, array
   nest) -- unit tests building the types directly, and conformance tests through the whole compiler once every
   pass on the path is iterative.
2. **A source nesting limit, enforced by the parser** (expressions, statements and blocks, type expressions,
   composite literals, function literals): a §21.4 implementation limit with a minimum, and a diagnostic at the
   construct that passes it.  This bounds every AST walk without rewriting the front end.
3. **Possibly a semantic type-depth limit** as a backstop for any type walk still recursive: each type's depth
   computed once (memoized, iteratively) where its layout is prepared, a §21.4 limit with a diagnostic at the
   declaration.  Not needed once step 1 is complete; useful if some walk stays recursive.

## Progress

- Stage 1 (types), part 1 DONE: binate `994ef2214` (2026-11-03) -- layout (sizeOf / alignOf loops, layStructs from a
  kept stack, a direct path for a struct holding no struct still to lay out).  cmd/bnc's IR identical before and
  after; no compile-time change in an interleaved A/B.  Deep tests skip the bare-metal and double-VM unit-test lanes.
- Stage 1 (types), part 2 DONE: binate `85c8cb4ed` (2026-11-04) -- Identical / IdenticalStrict from a stack of
  pairs (types_identical_walk.bn), NeedsDestruction deciding held structs from a stack (types_destruction.bn), type
  names written into one builder from a stack of pieces (whole names returned directly), MentionsSelf /
  FuncMentionsSelf from a stack, ReceiverBaseNamed a loop.  cmd/bnc's LLVM IR (x86-64, arm32) and native objects
  (aarch64, x86-64, arm32) identical before and after; instructions retired +0.17%, inside the 0.2% run-to-run
  spread, and a profile puts at most ~0.05% of the compile in the changed walks.  Review: no defect.
  Left for step 3: Identical on a looping composite with no named type in the loop (`A = *A` against `B = *B`)
  now loops instead of overflowing the stack; the checker rejects such aliases first, so believed unreachable.
- Stage types/abi DONE: binate `3e75af43c` (2026-11-04) -- hfaFold from a stack of parts (a non-aggregate answered
  at once), the four SysV walks as loops over one leaf list (sysvListLeaves).  New `types.BeginLayoutQuery` /
  `EndLayoutQuery` hold one layout query open across a walk: each Size / Offset call was its own query, so a walk
  asking at every level of a deep aggregate re-laid the levels below at each level (quadratic; the 200,000-level
  tests did not finish).  Later stages walking deep types and asking sizes need the same.  types.bni is at 990 of
  its 1000-line cap (claude-todo: split it before the next addition; user "Add now, file a split todo").  cmd/bnc's
  IR (3 targets), native objects (3 targets) and 169 ABI-shaped conformance tests' IR (3 targets) identical before
  and after; instructions retired within the run-to-run spread (the first version cost +0.8% on native aarch64
  until hfaFold answered scalars directly).
- Stage check: IN PROGRESS (2026-11-04, work-1).  Inventory (self-recursive on a type's parts, a lower bound): 47,
  of which 12 walk SYNTAX and are left to step 2's nesting limit (collectTypeExprNames, collectTypeExprConstDeps,
  checkBniValueEmbedding, typeHasNodeAt, typeMentionsIota, typeExprNames, checkValueEmbedding, listBatchDecls,
  expandWalkType, fieldPath, copyTypeExprStamps, declaredType).  The 35 semantic ones, in parts (one commit each):
  - Part 0: `typeVisits` (the visited set most walks share) scans a list, so a walk entering n types is O(n^2) --
    a 200,000-deep type would hang instead of crash.  Keep the scan for a few entries, then index the addresses in
    a `pkg/std/set` (in the BUILDER bundle).  Check the other visit structures (layoutVisits, opaqueWalk) likewise.
  - Part 1, searches over parts: holdsErrTypeByValueIn, containsTypeParam, containsDependentArrayDepth,
    containsByValueTypeParamIn, typeMentionsNamed, typeMentionsParam, typeReachesNamedByValue,
    layoutDependsOnTypeParamIn, mentionsTypeParamIn, mentionsTypeWhere, holdsItselfIn, AggregateComparable.
  - Part 2, collectors with side effects (order kept exactly): collectLiteralTypes, ensureMembersIn,
    collectTypeInstances, collectExportOwners, prepareLayoutIn, instanceNotBuiltOf.
  - Part 3, pairwise: identicalUpToDependentDepth, unsafeRetypeIn, DropsConstStrict, addsReadonlyAt, AssignableTo.
  - Part 4, transformers rebuilt bottom-up from a stack: substituteTypeParams, substituteSelf,
    peelNamedKeepReadonly; and instTypeHash's fold.
  - Part 5, the opaque walks: embedsOpaqueByValueSeen, partsPointToOpaqueEmbedding, funcSignaturesSized,
    ifaceSignaturesKept, partsMayBreak, aliasIfacesKept.  Part 6, interface parents: mergeIfaceMethods,
    ifaceFullMethods.
  Mutually recursive walks (a walk calling a helper that calls it back) are not in the inventory; each part looks
  for them in the functions it converts.
- Stage check parts 0 and 1 DONE: binate `4f72a70ba` (typeVisits indexed past 16 types) and `f524158f1` (the twelve
  searches from a shared work list, typeWalk in check_type_stack.bn; each walk looks at its root itself and pushes
  only its parts; containsDependentArray's 64-level cap removed; AggregateComparable / typeMentionsNamed /
  containsDependentArray start a visited set after 32 steps), 2026-11-05.  1,950 error and type-rule conformance
  programs' output, diagnostics and rc identical before and after; cmd/bnc's IR and objects identical.  Cost: about
  +0.1% instructions compiling cmd/bnc, all from part 1 (part 0 neutral) -- the push / pop of the parts of a type
  that has parts; down from +0.22% after a root step, fast paths for leaf roots and late visited sets.  Removing
  the rest would need a stack of raw pointers.  User: "go ahead now, actually".
- Found while starting check part 2 (2026-11-05): deep DECLARATION chains (`type T0 struct { n T1 }`, `type T1
  struct { n T2 }`, ...) are quadratic in the checker even with no recursion -- each declaration's check walks what it
  holds by value (layoutDependsOnTypeParam, typeReachesNamedByValue, checkValueEmbedding, a layout query), the whole
  chain below it: 2,000 types check in 0.9 s, 20,000 in 111 s (20,000 unrelated types: 5 s).  With a `sizeof` of the
  chain's head the 20,000 chain still crashes, in prepareLayout's recursion (prepareLayoutIn <-> prepareBatchType /
  prepareBniType: a stateful mutual recursion collecting declarations on demand; its bookkeeping lists TypesWalking /
  TypesCollecting / TypesPrepared are searched linearly, TypesWalking appended by copying).
  Decision (user, 2026-11-05): "Both, limit first" -- step 3's type-depth limit now, so nothing crashes or hangs; then
  the remaining conversions (check part 2 on) and the memoization of per-type answers across declarations as later
  stages.
- Step 3, the type-depth limit -- decisions (user, 2026-11-05): measure "Every part (Recommended)"; value "1024, at
  least 256 (Recommended)"; checked at "Each declaration (Recommended)".  Design:
  - The measure: a type's nesting depth is the number of types in the longest chain of types each a part of the one
    before -- an element (pointer, slice, array, readonly, interface value), a field, a parameter or result, a type
    argument, an interface's method signature or parent -- where a named type counts as its underlying type and an
    alias as what it denotes (so each `type Ti struct { n Ti+1 }` adds one level), and a chain ends where it reaches a
    type already on it (a cycle, through a pointer, slice or function type).
  - Where: a pass over a batch's declarations (checkTypeDepths) right after collection, before checkValueEmbedding --
    the four callers of collectDecls + checkValueEmbedding (package, file, REPL, pending decls).  A profile of 8,000-
    deep chains, declared top-first and bottom-first, puts ~99% of the time in checkValueEmbedding's checks
    (requireSizedType -> layoutDependsOnTypeParam, typeReachesNamedByValue); collection itself is cheap, so no check
    during collection is needed.  Generic declarations are skipped (their instances are bounded by the
    instantiation-chain limit); aliases are not types of their own.
  - How: depths computed with no recursion (an explicit stack of frames, post-order), memoized per type for the
    batch; a type on the current path contributes nothing (the cycle cut).  A named type whose depth passes 1024 is
    reported at its declaration ("type T nests too deeply: more than 1024 levels") and poisoned -- its underlying
    becomes the error type, as a type holding itself by value has its field poisoned -- so the types holding it stop
    there: one error per chain, at the type where it passes the limit, and the later walks see a bounded type.
  - Spec: a new rule in the type-declaration section (decl or type chapter) and a §21.4 row ("Maximum nesting depth
    of a type ... (an implementation limit, conf.implementation.limits; at least 256)"), focused review of the edit.
  - Tests: conformance -- a chain at the limit compiles; one past it is an error (by-value structs, a pointer chain,
    an interface method chain, a chain passing the limit inside an inline type); check unit tests of the depth
    computation (cycles, DAGs, memo).
  - Found while implementing (2026-11-05, 20,000-deep generated programs): cutting a chain only once, at the type
    where it first passes the limit, leaves the types above the cut walkable to their full depth -- quadratic again
    -- so a chain is cut every 1024 levels (19 errors for 20,000 struct declarations, each reported type does nest
    too deeply).  With that, checking is linear in the chain's length but slow: 13-60 s for 20,000 declarations
    (struct, pointer, interface chains; the per-declaration walks still go up to 1024 levels) -- the memoization
    stage's job.  A `sizeof` of a deep chain read during collection crashed in prepareLayout's on-demand recursion
    before the post-collection pass runs; prepareLayoutIn was made to count levels and stop past the limit (no
    report), but that did not land with `3114621b7` (its prepareLayoutIn had no cutoff, and a deep read crashed
    until `7fc716db6`).  Chains of named types over named types
    (`type N0 N1`, `type N1 N2`, ...) add no depth (names are transparent) and are not covered by the limit, but scale
    badly on main too: 2,000 in 3.4 s, 20,000 over 150 s -- a separate item, not yet looked into.
- Step 3 DONE: binate `3114621b7` (the type-depth limit, landed 2026-11-06), spec docs `7a69627`.  The design changed
  after two reviews: the measure is exact and order-independent (cycles of definitions by Tarjan's algorithm; inside
  a definition D, a chain that has gone through a part held indirectly -- a pointer / slice / interface value's
  element, a parameter or result, a type argument, an interface's signature or parent -- stops before a definition in
  a cycle with D; per declaration, not per type object, so a separately built `*func(Node)` counts the same as a
  shared one); aliases and blank `type _` are measured and only reported; a cut poisons only the parts past the limit
  (struct fields, interface signature parameters / results, parents); a by-value cycle is reported as a recursive
  type where the measure closes it; an opaque type outside its package is one level.  Spec: no Constraint tag ("may
  be rejected"), a constraint-candidate.  Next (user: "yes"): a layout read past the limit reported at that
  position, prepareLayout made an explicit frame machine -- DONE below.
- Stage memoization DONE: binate `7eb817dd8` (the pointer rule's answers kept across a batch), `10094a641`
  (concrete layouts kept), `db60c6121` (the by-value cycle check only where the depth walk met a by-value cycle),
  `f550149b5` (one layout query held across checkValueEmbedding, restarted on each type change), `07b29d367`
  (IR-gen: one layout query across destructor and copy generation), landed 2026-11-06; user: "let's do the planned
  memoization stage first".  Against check and IR-gen as before, instructions to check and compile: a 1024-type
  pointer chain 23.42G -> 0.69G, a 1024-type struct chain 4.78G -> 0.63G (= 1024 unrelated structs), conformance
  303 30.2G -> 3.3G, 304 34.2G -> 2.5G; under the double VM 303 + 304 take 81 s, not 822; cmd/bnc's self-compile
  unchanged within noise; IR and objects identical.  IR-gen's pending named types were fixed on main meanwhile
  (`b6cb84aee`), so this stage's own fix of them was dropped.  Open from the review: restartHeldLayout cannot tell
  that its restart starts a new layout generation (another query open) -- asserting it needs a types API, after
  the types.bni split (claude-todo).  What is still superlinear at many declarations is in their number, not their
  nesting (claude-todo, "Checking is quadratic in a package's number of declarations").
- Found on the way: freeing a deep structure recurses through destructors; decided as the program's responsibility
  (spec §18.2 note, docs `2ccb03d`), so the stages' tests release their deep types a link at a time, and the
  compiler must not drop a deep type graph whole either.
- Layout-read step DONE: binate `7fc716db6` (landed 2026-11-07; user: "yes"; with the blocked-build cycle fix, user:
  "folded in is fine").  prepareLayout is an explicit frame machine (check_layout_prepare.bn).  On main every deep
  read crashed: seven 20,000-deep probes (a `sizeof`, a read after another, a generic type's and a generic
  function's type argument, a variable's type, an inline `[1][1]T0`, a chain through a slice) now give errors.  The
  design after two reviews: no type is changed at the read -- a first version cut a declared chain there, which
  cascaded (304 reported Z2 once Z0's pointer cycle was cut) and in a waiting instance build kept its cut while its
  report was dropped.  The walk finds each type's by-value height (fields, array elements, a named type's
  underlying; a slice's element is walked and adds none), memoized per visited set and kept per prepared
  declaration (TypesPrepared: declaration -> height), so a later read stopping at one sees it; a read reaching, by
  value or through a slice, a type higher than 1024 is reported at the read ("type T nests too deeply", by
  TypeName) and its layout not read; a too-deep generic function's type argument becomes the error type.  A
  declaration whose walk meets one another read is walking keeps no height.  TypesCollecting / TypesCollected /
  TypesWalking are indexed (declSet).  cmd/bnc's IR identical; of 1985 corpus programs only 304 differs (its read
  reported, pinned at line 15); the 156 layout-reading conformance tests pass on the VM; the deep unit tests (skipped
  on int-int and bare metal by "TestDeep") take 89 s on the single VM; cmd/bnc's compile unchanged within noise.
  Remaining: collection itself still recurses -- a `.bni` chain an importer reads crashes in resolveBniTypeByName,
  a deep type used as a generic's type argument crashes in collectExportOwners (part 2), and `sizeof` / constant
  chains are resolved recursively; a read made inside another read's walk takes a declaration being walked as ready
  (claude-todo, "A type whose walk is still on the stack counts as prepared", MAJOR); reading a long chain is
  quadratic (claude-todo, "Preparing a layout during collection is quadratic").  Next -- IN PROGRESS (2026-11-07,
  work-1; user: "let's do the remaining work"): check parts 2-6 and collection's recursion, the parser nesting limit,
  then irutil / ir, irgen, codegen, native, vm / interp, lint.
- Stage check part 2 DONE: binate `6d028e9b1` (landed 2026-11-07; user: "wait 8 minutes, then go ahead").
  collectExportOwners, collectTypeInstances (its interfaces a typeVisits, not a list), instanceNotBuiltOf and
  ensureMembersIn take a type's parts from typeWalk in the recursion's order (collectLiteralTypes already did, and
  prepareLayoutIn is the frame machine).  A 200,000-deep chain (pointers, structs, named types, interfaces each the
  next's parent) crashed each; five TestDeep* tests now walk them.  cmd/bnc's IR and 1994 corpus programs' output
  identical; instructions +0.15% on average, within the spread.  Review: no defect.
- Stage check part 5, first walk, DONE with the slice-element fix: binate `e6581426d` (embedsOpaqueByValueSeen on the
  work list; a type parameter the frame binds walked as its argument in a nested walk with no frame) and `b6ad6b5b3`
  (the layout read stops at a slice; pending types, deferred opaque and size checks), landed 2026-11-07 (user: "wait
  8 minutes, then land"; design: "Don't collect it (Recommended)", "Defer at every site (Recommended)").  cmd/bnc's
  IR and 2012 corpus programs identical; 788 / 772 layout, opaque, C-interop and cast conformance tests pass on LLVM
  / the VM; instructions +0.14%, within the spread.  Left of part 5: partsPointToOpaqueEmbedding, funcSignaturesSized,
  ifaceSignaturesKept, partsMayBreak, aliasIfacesKept.  Found by the review: an array length's sizeof never applies
  the pointer rule (claude-todo, MAJOR, pre-existing).
- Stage check part 3 DONE: binate `be689d3e7` (landed 2026-11-08; user: "wait 8 minutes, then go ahead").  The
  pairwise walks take their pairs from work lists: the readonly walks (DropsConstStrict, dropsConst,
  signaturesMatchStrict, sigTypeIdenticalStrict, addsReadonlyAt) share one walk over (rule, src, dst) pairs
  (readonly_walk.bn); identicalUpToDependentLengths compares its pairs from typeWalk, and its 64-level cap (a false
  reject past 64 levels) is gone; the container retypes and AssignableTo's decay through named types are loops.  A
  pair of one type is not walked (types sharing parts were walked once per path, exponentially), and a function type
  inside two signatures already found identical is not asked Identical again.  The deep tests (200,000 levels)
  crash, time out or hit the cap on the old code.  cmd/bnc's IR and 2012 corpus programs identical; 383 cast /
  unsafe_cast / readonly conformance tests pass on LLVM and the VM; instructions +0.02%, within the spread.  Review:
  no defect.  Found: the container retypes, and AssignableTo through a chain of named types, are quadratic in depth
  (claude-todo, LOW, pre-existing).  Next: part 4 (substituteTypeParams, substituteSelf, peelNamedKeepReadonly,
  instTypeHash's fold).
- Stage check part 4 DONE: binate `b7b173d77` (landed 2026-11-08; user: "wait 4 minutes, then go ahead").
  substituteTypeParams and substituteSelf share one walk (check_subst_walk.bn) that rebuilds a type from its parts
  taken from a work list, re-entrant (an instance whose arguments change is instantiated, which can substitute in
  turn); the by-value bracket, Identical vs identity, and rebuild-always vs keep-unchanged are kept exactly.  A type
  whose parts are all leaves is substituted with no work list: through the work list alone compiling cmd/bnc took
  ~0.27% more instructions, with it -0.03% (within the spread).  peelNamedKeepReadonly and instTypeHash are loops.
  cmd/bnc's IR and 2012 corpus programs identical; 548 generic / Self conformance tests pass on LLVM and the VM.
  Review: no defect (one allocation note, folded in).  Next: part 5's rest (partsPointToOpaqueEmbedding,
  funcSignaturesSized, ifaceSignaturesKept, partsMayBreak, aliasIfacesKept), part 6 (mergeIfaceMethods,
  ifaceFullMethods), then collection's recursion.

## Decisions (2026-11-03)

- Step 1, all of it, staged: user "All of them, staged (Recommended)".
- AST walks: user "Parser-enforced nesting limit (Recommended)" -- step 2, not converting them.
- Open: the source nesting limit's minimum (asked when stage 2 is designed); step 3 (a type-depth backstop) not
  decided, revisit once step 1 is done.
