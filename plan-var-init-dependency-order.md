# Plan: dependency-order package-level variable initialization

**Status:** designed + adversarially reviewed (2026-07-01); NOT yet implemented.
**Decision (2026-07-01, designer):** switch package-var init from source order to
Go-style **dependency order**. "Order dependencies are sus; splitting/reordering
files should be easy."

## Problem

Package-level `var` initializers run in **source order**: alphabetical by filename
across a package's files (`loader.bn` `sortStrings` the dir entries), top-to-bottom
within a file. So a forward or cross-file reference silently reads a **zero value**
— and the observable value depends on filenames (rename `z_file.bn`→`a_file.bn` and
a value changes). The loader already dependency-orders *packages* (import order);
only *within* a package is it filename-order at the var granularity.

The stdlib relies on same-file **backward** refs (which work today): `pkg/std/errors`
builds an error hierarchy `var Unimplemented @Error = Rooted(Unsupported, "...")`
(parent passed as an ARG). Dependency order preserves this and fixes the footgun.

## What Go does

Go dependency-orders package vars, **transitively through same-package function
calls**: `var b = f(); func f() int { return d }; var d = 3` → order `d, b`.
Cross-package refs aren't analyzed (those packages are already initialized).
Independent vars fall back to declaration/source order as a tiebreaker.

## Design (v1 — validated by adversarial review wf_40d3fb4e)

Compute a **stable dependency order** + detect cycles in the CHECKER; emit in that
order in IR-gen. Do NOT restructure the checker's two-pass var checking.

1. **`collectVarDeps(e)`** (checker, new — `check_var_resolve.bn`): a complete
   syntactic walk of an initializer expression collecting bare `EXPR_IDENT` names.
   Recurse `X/Y/Z`, `Args`, `Elems[].Key/.Value`. Do NOT recurse `TypeRef` (array
   dims are consts) or a func-literal body (`EXPR_FUNC_LIT` — a closure isn't run
   at init) — EXCEPT a direct-call **IIFE** callee (`EXPR_CALL` whose `X` is
   `EXPR_FUNC_LIT`): walk its `DeclRef.Body` statements (`collectVarDepsStmt`),
   because it IS run at init. The caller filters collected names to the package's
   own init-var set — so consts (`SYM_CONST`) and cross-package `pkg.X` (the `pkg`
   ident isn't an init-var) drop out naturally.
2. **Order + cycles** (checker): source-stable DFS exactly like
   `resolveConstByName`/`errConstCycle` (`check_const_resolve.bn`) — iterate vars in
   source order, DFS deps-first, `Visiting`/`Done` marks (terminates on cycles),
   emit a **variable initialization cycle** error at each back-edge. Result is the
   ordered var-name list. STABLE order is load-bearing: the six independent
   `New(...)` error roots have side effects and must keep source order.
3. **Store keyed by package path** (BLOCKER from review): ONE `Checker` instance
   checks ALL packages before ANY IR-gen runs, so a scalar order field is clobbered.
   Use per-package parallel arrays `VarInitOrderPaths @[]@[]char` +
   `VarInitOrders @[]@[]@[]char`, appended once per `CheckPackage` under
   `c.curPkgPath`; `buildInitBody` looks up by `gc.PkgPath`. Gate the whole thing on
   `!c.ReplDeclMode` (REPL runs one var at a time — no batch to sort).
4. **Emit** (IR-gen, `gen_init.bn buildInitBody`): emit `<name> = <value>`
   assignments in the checker's per-package order instead of source order. It gets
   the MERGED package decls with a per-STATEMENT import overlay keyed on `Pos.File`
   (`gen_module.bn:436`), so reordering statements is safe. Read the order via
   `m.Checker` + `gc.PkgPath`; fall back to source order if absent.

## Deferred (documented follow-ups)

- **Transitive-through-functions** (Go does this): `var A = compute()` where
  `compute()` reads package var `B` misses the `A→B` edge (v1 collects only
  syntactic edges + call args). SAFE for the current tree — every function-calling
  initializer's callee (`errors.New`/`Rooted`, `os.newFile`) reads no package var,
  and the error hierarchy passes parents as ARGS (captured). Full support needs
  same-package call-graph analysis.
- **Grouped package vars** `var ( ... )`: a PRE-EXISTING gap — grouped vars are
  neither registered as ModuleGlobals nor emitted in `__init` today
  (`buildInitBody`/`gen_module.bn` iterate top-level `DECL_VAR` only, never recurse
  `DECL_GROUP`). Keep the var graph restricted to that same top-level set so both
  sides agree; fixing groups is separate (touches registration + emit). **Tackle
  next.**
- **Inferred-var FORWARD refs**: `var A = B` where `B = 10` is a later INFERRED var
  fails to compile today (`B` isn't a `SYM_VAR` until pass 2's source-order
  `checkVarDecl`) — a pre-existing "not in scope yet" error, orthogonal to runtime
  order. TYPED forward refs (`var A int = B`) compile and get correct VALUES under
  v1. Full support needs pass-1 pre-registration + dependency-ORDER checking
  (mirror `resolveTopLevelConsts` for vars) — bundle with the grouped-var work.

## Review findings folded in (wf_40d3fb4e)

- Flag scoping (if the checkIdent-hook variant is ever used instead): `checkVarDecl`
  is shared with LOCAL var statements (`check_stmt.bn:110`); a flag must be
  save/restored around only the initializer VALUE check and cleared in
  func/closure bodies. (v1 uses the syntactic `collectVarDeps` walk, sidestepping
  this — but the IIFE walk must correctly skip nested non-IIFE closures, and a
  local var inside an IIFE that SHADOWS a package var is a rare spurious-edge
  limitation to document.)
- Cycle-safe emit: `os.Exit(1)` on any checker error (`compile.bn:66`) means a
  detected cycle stops the build before IR-gen; the topo-sort's own recursion must
  still terminate via Visiting/Done marks independent of error emission.
- Update the stale `errors.bn:106-109` comment asserting "intra-package var
  initializers run in SOURCE order" once this lands.

## Tests

Conformance: forward-ref (typed) gets the right value; cross-file ordering;
self/mutual/transitive cycle → error; closure-only ref does NOT create an edge and
inits correctly; IIFE `var A = (func()int{return B})()` orders A after B; a guard
that the `errors` hierarchy still initializes (shuffled-file-name variant).
