# Plan: split the type checker out of `pkg/binate/types` (→ `types` + `check`)

**Status:** IN PROGRESS (claimed 2026-09-19, work-3/session). `types.bni` over-export trim + the `vm.bni` reduction, per user sequencing. Needs
explicit user go before starting (it is ~3× the `ir → iropt` re-arch).

## Goal

Bring `pkg/binate/types.bni` (1109 lines) below the 1000-line cap by making
`types` a pure Type **data-model** package and moving the type **checker** into a
new `check` package. Estimated result: `types.bni` **1109 → ~650**.

## Why this is the right split (not number-chasing)

`pkg/binate/types` is two concerns fused: the Type data model (consumed by every
backend, the VM, `ir`, `irbuild`, `iropt`) and the type checker (a distinct
compiler phase). Same "data model + big consumer" shape as the old `ir`. The
checker is a compiler phase, not part of the layout contract that backends read —
separating them is genuine architectural merit.

## Feasibility — the boundary is CLEAN

- **Data-model side: 17 files, 3,245 lines.** ZERO references to checker types
  (`Scope` / `Checker` / `addCheckError`) — verified. Stays in `types`.
- **Checker side: 53 files, 14,212 lines.** Depends on the data model
  (`types.*`), never the reverse. Moves to `check` (imports `types`).
- Dependency after split: `check → types` (acyclic; nothing `types` imports will
  import `check`).

## File lists (as of 2026-09-19; re-derive before implementing — files churn)

**DATA-MODEL side → stays in `types` (17):**
abi_arm32.bn abi_hfa.bn abi_return.bn abi_sysv.bn abi_sysv_argmem.bn layout.bn
layout_offsets.bn literal_value.bn sig_string.bn string_lit.bn type_name.bn
types.bn types_const.bn types_identical.bn types_method.bn types_query.bn
wrapper_intern.bn

**CHECKER side → moves to `check` (53):**
bni_scope.bn bni_scope_collision.bn bni_scope_const.bn bni_scope_expose.bn
check_addr.bn check_assert.bn check_assign.bn check_builtin.bn check_c_interop.bn
check_capture.bn check_cast_fits.bn check_cast_safe.bn check_const.bn
check_const_resolve.bn check_decl.bn check_decl_func.bn check_decl_func_generic.bn
check_deferred_constraints.bn check_expose_collision.bn check_expr.bn
check_expr_access.bn check_expr_binop.bn check_expr_composite.bn
check_expr_constfold.bn check_expr_unary.bn check_func_lit.bn check_generic.bn
check_generic_backfill.bn check_generic_populate.bn check_generic_type.bn
check_iface_extends.bn check_impl.bn check_interface.bn check_method.bn
check_opaque.bn check_pending.bn check_pending_cycles.bn check_self.bn
check_stmt.bn check_terminates.bn check_type_redecl.bn check_var_resolve.bn
checker.bn checker_errors.bn checker_persistent.bn checker_state.bn checker_util.bn
escape.bn eval_const_int.bn resolve_type.bn scope.bn types_assignable.bn
types_assignable_iface.bn

**Boundary caveats to verify during implementation:**
- `types_assignable.bn` / `types_assignable_iface.bn` / `escape.bn` are on the
  checker side only because they reference `Checker`/`Scope`/`Impl`. Assignability
  is arguably a type relation; confirm no NON-checker consumer (backend/ir/vm)
  calls `AssignableTo` etc. before moving it. If a data-model consumer needs
  assignability, that part stays in `types`.
- The classifier heuristic keys on `Scope|Checker|addCheckError|checkExpr|
  resolveType|defineInterface` refs + filename prefixes. Re-run it fresh.

## Public surface that moves (types.bni → check.bni)

~36 checker public symbols (~484 lines incl. docs): Check, CheckDeclInScope,
CheckExprInScope, CheckMainPersistent, CheckPackage, CheckPackageDecls,
CheckStmtListInScope, Checker, CheckerErrors, ExprType, FindFreshCycles,
FormatCheckError, Interpreted, IsDeclPending, IsPendingDecl, LoadPackageInterface,
LookupMethodForDecl, NewChecker, NewScope, PackageMemberHome, PackageType, Pending,
PendingMark, RegisterReplImport, RetryPendingDecls, Scope, SetInterpreted,
VarInitOrder, VarInitOrder, plus Scope/Symbol methods (Define, Lookup, Kind, …).
(Estimate is noisy on method-name collisions like Type/Kind/Lookup — recompute.)

## Consumers to re-point (check.* instead of types.*)

The checker's callers: `irgen`, `cmd/bnc`, `cmd/bni`, `interp`, `repl`, `loader`
(grep `types.Check`, `types.NewChecker`, `types.Checker`, `types.ExprType`,
`types.Scope`, `types.LoadPackageInterface`, etc. across pkg+cmd). All import
`check` after the move; none is imported BY the checker, so acyclic.

## Approach (same toolchain as ir → iropt / irbuild)

1. `mkdir pkg/binate/check`; `git mv` the 53 checker files (+ their `_test.bn`)
   into it.
2. Requalify bare data-model refs → `types.` in the moved files (reuse
   `qualify.py`: qualify set = types.bni publics − names defined in the moved
   files; mask comments/strings; add `import "pkg/binate/types"`).
3. Generate `check.bni` from the moved public defs (with docs); remove those sigs
   from `types.bni` (paren-depth-based removal — NOT the greedy regex that ate
   StringConst in the irbuild phase).
4. Re-point consumers `types.<checkerSym>` → `check.<checkerSym>` + add
   `import check`.
5. Watch for the same gotchas hit in the ir re-arch: shared private helpers used
   by both sides must be exported from `types` (or moved); test files that build
   fixtures via checker internals move with the checker; methods on public types
   are cross-package-callable even absent from the .bni, so free functions newly
   split out may need explicit `.bni` export that the method form didn't.
6. Build (unittest gen1), hygiene (whitelist any test-name mismatches),
   adversarial review, full builder-comp conformance, then land.

## BUILDER note

The checker is in `cmd/bnc`'s compile pipeline, so `check` will be in the
BUILDER-compiled surface (as `types` is now). No new language features involved
(free functions + package split), so no BUILDER bump needed — but update the
CLAUDE.md BUILDER-surface list when it lands.
