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
- Found on the way: freeing a deep structure recurses through destructors; decided as the program's responsibility
  (spec §18.2 note, docs `2ccb03d`), so the stages' tests release their deep types a link at a time, and the
  compiler must not drop a deep type graph whole either.

## Decisions (2026-11-03)

- Step 1, all of it, staged: user "All of them, staged (Recommended)".
- AST walks: user "Parser-enforced nesting limit (Recommended)" -- step 2, not converting them.
- Open: the source nesting limit's minimum (asked when stage 2 is designed); step 3 (a type-depth backstop) not
  decided, revisit once step 1 is done.
