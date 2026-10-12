# Plan: IR-gen takes its types from the checker — one name resolver

Status: DRAFT for review (2026-10-12, work-6).  User: "What's the *right* thing to do, regardless of
effort" → this; "yes (and get it reviewed)".  Prerequisite for REPL type / interface shadowing
(plan-repl-type-shadowing.md, approach B), which then becomes a checker-only change plus a display name.

## Problem

IR-gen resolves type NAMES from the AST a second time, in parallel with the checker, with its own scopes:
`resolveTypeExpr` (82 calls in 38 files), ~81 name-keyed registry lookups (lookupStructIdx,
lookupGenericTypeDecl[Pkg], definingPkgType, bareIfacePkg, ifaceTypeForName, …), a type-parameter
substitution context (CurrentTypeParamNames / Types / Owner / Args, ~9 files), the defining file's import
overlay for generic bodies and the CurrentImportPkg precedence rules (121 sites), and `TypInt()`
fallbacks that silently turn a type it cannot resolve into `int`.  When the two resolvers disagree the
result is usually a silent miscompile.  Recent instances of the class:
- a consumer's same-named declaration or global capturing an imported generic body's names
  (`df56f73cd`; the bareGlobalIdx fallback fixed in `1d3a19624`);
- the defer pre-pass not seeing locals the checker knew about (`91e8867e7`);
- a prompt generic struct IR-gen had not registered becoming `int` (wrong size; item 3, `d34cc0451`);
- generic bodies needing the defining file's imports overlaid, because IR-gen re-resolves names the checker
  already resolved in the right scope.

## Goal

The checker is the single authority on what a name means.  IR-gen maps the checker's resolved types
(irTypeFromChecker, which exists: instances, named types, structs, function types, tuples, arrays, type
parameters by owner / position) and keys its registries by the checker's declarations / types, never
re-resolving a name.  With a checker attached, a type IR-gen cannot map is an internal error, never `int`.

What legitimately stays name-based: symbol mangling (function, helper, method, vtable, typeinfo names),
derived from the identity names the checker's types carry.

## Invariants to verify first (stage 0)

- One checker per compilation, holding every package IR-gen reads types from: bnc typecheckAll /
  typecheckPackages (one Checker, LoadPackageInterface + CheckPackage per package, then main) — VERIFIED;
  interp TypecheckAll; the REPL session and mid-session imports; bnc library and test modes; the bni test
  runner; bnlint (does not run IR-gen).  Any driver with several checkers needs a common type table first.
- Every TypeExpr IR-gen reads was resolved by that checker: declarations and bodies of the compiled
  packages; imported `.bni` declarations (LoadPackageInterface); generic instances emitted from the
  checker's checked copies (instanceCopy / typeInstanceCopy) — the copies, not the abstract declaration;
  function literals' signatures.  TypeExprs IR-gen SYNTHESIZES (gen_generic.bn: exprToTypeExpr, building a
  type argument from an expression) must take the checker's resolution of the source expression
  (Expr.ResolvedTypeID) instead.

## Stages (each lands green)

1. **The checker stamps TypeExprs.**  Add `TypeExpr.ResolvedTypeID` (Expr and Decl have one) and set it at
   every resolution (resolveTypeExpr and its variants: resolveNamedTypeExpr, typeArgOrInterfaceFromExpr,
   the method-expression base, instantiation heads, receivers, impl receivers and interface refs).  A
   checker unit test walks checked ASTs (including instance copies) and asserts every TypeExpr is stamped.
   No behaviour change.
2. **Differential IR-gen.**  `resolveTypeExpr` computes BOTH the stamp's mapping and the legacy result,
   asserts they agree (an internal error naming both, under a checker), and returns the legacy one.  Run
   the whole conformance suite on every mode, the unit tests and the self-compile (gen1 → gen2): each
   disagreement is a legacy-resolver bug or a mapping gap; fix each at its root (the mapper, or the
   checker's stamp) before going on.  This stage is the safety net — the switch in stage 3 changes only
   what we have seen agree.
3. **Switch.**  `resolveTypeExpr` returns the stamp's mapping when a checker is attached (a missing stamp is
   an internal error; no `TypInt()` fallback); the legacy path remains only without a checker.
4. **Registries by declaration.**  Struct / alias / interface registration and the generic stashes key on
   the checker's declaration and type identity (the decl's ResolvedTypeID), not `d.Name`; method receivers,
   impl rows, method expressions and generic-method association use the checker's resolution; the
   name-keyed lookups that remain become lookups by identity name taken from a checker type.
5. **Delete the second resolver.**  The type-parameter substitution context, the import overlay for generic
   bodies and the bare-name precedence rules that only served name resolution go; IR-gen's unit tests run
   through the checker (55 files generate IR without one today); the no-checker path is deleted.
6. **REPL type / interface shadowing** (plan-repl-type-shadowing.md) on top: a shadowing definition gets a
   fresh internal identity in the checker; IR-gen follows by construction.

## Validation

Stages 2–3 change how every compile resolves types: full conformance on LLVM, native aa64, native x64,
native arm32 (baremetal and linux), the VM; the self-compile; the unit tests of every package that runs
source through IR-gen (irgen, codegen, vm, native/*, interp, repl).  Stages 1, 4, 5: the affected packages'
unit tests, targeted conformance, the self-compile.  Each stage gets an adversarial review before landing.

## Risks / questions for the review

- irTypeFromChecker completeness: named-distinct non-struct types, opaque types, readonly wrappers,
  variadic function types, named function values (nominal since `3cf3d8ab5`), generic interfaces,
  ArrayLenDependent arrays (nil today — only in abstract bodies?), untyped constants (default types), where
  IR-gen deliberately differs from the checker (e.g. a func-value OP built at the bare type while the
  variable keeps the named one).
- IR-gen code paths that run with NO checker in production (not only tests)?
- Performance: a mapping per TypeExpr resolution (cache by checker Type).
- BUILDER: irgen and check are BUILDER-compiled; no new language features needed.
- The differential stage's cost: two resolutions per TypeExpr, only during the transition.
