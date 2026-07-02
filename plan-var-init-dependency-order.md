# Plan: dependency-order package-level variable initialization

**Status:** LANDED on main (2026-07-01, commit `444c9c90`).
Full `builder-comp` conformance green (2570 passed, 0 failed); types 858 / ir
586 unit tests green; hygiene 15/15.
Adversarial review (independent workflow) found + fixed one real MAJOR
missed-read: a grouped local `var ( ... )` inside an IIFE body parses to
`DECL_GROUP` (nil `.Value`, members in `.Decls`), so `collectVarDepsStmt`'s
`STMT_DECL` arm was dropping the read — fixed via `collectVarDepsDecl`
(recurses group members, mirroring `registerConstPlaceholders`), pinned by
`TestVarInitIIFEGroupedLocalReadFormsCycle`. Also updated the stale
`errors.bn` / `errors_test.bn` init-order comments and filed the pre-existing
dropped-blank-initializer gap in `claude-todo.md`.
**Decision (2026-07-01, designer):** switch package-var init from source order to
Go-style **dependency order**. "Order dependencies are sus; splitting/reordering
files should be easy."

## Implementation notes (as landed on the branch)

- `pkg/binate/types/check_var_resolve.bn` (new): `collectVarDeps` /
  `collectVarDepsStmt` / `collectVarDepsCase` (the syntactic walk),
  `resolveTopLevelVarOrder` + `visitVarByName` (source-stable DFS + cycle
  detection), `recordVarInitOrder` + `VarInitOrder` (per-package storage),
  `errVarCycle`. Composite-literal element KEYS are NOT collected (a struct key
  is a field name; an array key is a const — Binate has no maps — so a key that
  matches a package-var name must not create a false edge).
- `pkg/binate/types.bni`: Checker gains `VarVisiting` / `VarOrdered` (transient
  DFS marks) + `VarInitOrderPaths` / `VarInitOrders` (per-package result) +
  the `VarInitOrder` method decl.
- `pkg/binate/types/check_decl.bn`: `collectDecls` calls
  `resolveTopLevelVarOrder` after `collectDeclsBody`, gated `!c.ReplDeclMode`.
- `pkg/binate/ir/gen_init.bn`: `buildInitBody(decls, order)` emits in the
  checker's order (falling back to source order for an empty order), and
  defensively appends any init-var the order omits so a set mismatch can't
  silently drop an initializer.
- Tests: 7 checker unit tests in `check_var_resolve_test.bn` (forward-ref,
  self/mutual/transitive cycle, closure-skip = no false cycle, IIFE-walk =
  cycle caught, composite-key = no false cycle) + 5 conformance tests under
  `conformance/regressions/var-init-*` (forward-ref → 42, cross-file → 42,
  hierarchy/diamond → 32/11/21/1, mutual + self cycle → error). Cycle detection
  is pure checker behavior (mode-independent) so it lives in unit tests; the
  conformance tests pin the runtime ORDERING that is the actual deliverable.
- The `< 2` fast path was corrected to `== 0`: a lone `var a = a + 1` is a
  self-cycle that must still be swept.

## Bugs discovered while testing (raised in claude-todo.md, NOT fixed here)

- **CRITICAL: closures capture package GLOBALS by value** (silent miscompile) —
  `isCapturableKind` treats a package-scope `SYM_VAR` as capturable. Orthogonal
  to var-init; my "skip stored-closure bodies for ordering" choice is *correct*
  for the intended live-global semantics (the fix should make closures read
  globals live, not snapshot them).
- **IIFE in a var initializer fails clang codegen** (loud, not silent). The
  ordering walk handles IIFE bodies; only codegen is missing.

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
- **Grouped package vars** `var ( ... )`: DEFINING side ✅ DONE (2026-07-01) —
  `initVarDecls` / `appendPkgVarDecls` flatten grouped members into the init-var
  set, `registerVarGlobals` gives each a global, and grouped members join the
  dependency order. The cross-package EXPORT path (grouped vars in a `.bni`) is
  still open — see `claude-todo.md` "Grouped vars EXPORTED via a `.bni`" (loud
  error, deferred: naive extern-registration double-registers the defining
  package's grouped globals, a loader-merge/dedup concern).
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
