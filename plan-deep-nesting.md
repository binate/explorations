# Plan: deep nesting -- iterative type walks, and nesting limits

Status: decided 2026-11-03 (work-1); stage 1 (types) in progress.  claude-todo.md: "Deep nesting -- of types or of expressions -- is bounded only
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
- Stage 1 next: the rest of types' recursive walks -- type naming (typeNameImpl / renderAnonStructName),
  Identical / instArgsIdentical, IdenticalStrict, NeedsDestruction / needsDestruction, ReceiverBaseNamed,
  MentionsSelf / FuncMentionsSelf.
- Found on the way: freeing a deep structure recurses through destructors; decided as the program's responsibility
  (spec §18.2 note, docs `2ccb03d`), so the stages' tests release their deep types a link at a time, and the
  compiler must not drop a deep type graph whole either.

## Decisions (2026-11-03)

- Step 1, all of it, staged: user "All of them, staged (Recommended)".
- AST walks: user "Parser-enforced nesting limit (Recommended)" -- step 2, not converting them.
- Open: the source nesting limit's minimum (asked when stage 2 is designed); step 3 (a type-depth backstop) not
  decided, revisit once step 1 is done.
