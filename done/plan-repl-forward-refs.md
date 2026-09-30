# Plan: REPL forward references — a parked declaration binds nothing

Status: DONE — landed as binate `8ba473042` (2026-09-29).  User: "Do the full rework"; after the plan review,
"Let's do (b)" (type redefinition is an error until shadowing lands).  The first model below (R1–R6)
was reviewed and replaced: it tried to make every USE of a parked declaration wait on it, and the
review found about half the use forms uncovered (method calls, `impl`, array-length constants,
`make` / `sizeof`, generics, pointer-only uses), plus unsafe type-redefinition handling.

## Why

The REPL's Tier 3 forward references are optimistic: a declaration that uses a parked declaration
resolves against the parked declaration's signature and is emitted, betting the parked one is emitted
later.  Reproduced consequences (claude-todo.md entry "The REPL runs a parked declaration whose retry
fails to check"): stale parked entries clobber later redefinitions; a failed type retry unblocks its
dependents (IR-gen panic); dependents run before or without their dependency ("extern not found");
a parked type redefinition rewrites the old type in place (old values read through the new layout);
a parked function redefinition with a different signature replaces the old one in place (old callers
pass the old arguments); a parked method is callable; a pointer to a parked type crashes IR-gen.

## The model

- **B1 — a parked declaration binds nothing.**  When a declaration parks, everything its check bound
  (names, a method-set entry, a type placeholder) is undone at once; the declaration lives only on the
  parked list.  A use of it is therefore an unbound name — in a declaration being checked tentatively
  it is captured as a missing name (the using declaration parks on it), at the prompt it reports
  "function f is unresolved (pending: g)".  This covers every use form, pointer-only type uses
  included, with no per-form hooks.  A parked redefinition leaves the live definition in place until
  it resolves.
- **B2 — the capture points that must exist for B1:** an unbound name (exists); a signature / var
  type / type-declaration name (the declaration pass runs tentatively too — today it is strict, which
  would turn `func f(x T)` with T parked into an error); a constant name in a constant expression
  evaluated by constval (array lengths); a method missing from a session-package named type
  (`x.M()`, method value / expression) captured as `T.M`.
- **B3 — resolve by groups, dependencies first.**  A sweep builds the graph over parked entries (an
  edge from each to the entries providing its missing names — a name, or `T.M` for a method),
  computes strongly connected groups, and visits them dependencies first.  A group whose missing names
  are all bound or provided by its own members is checked together, like a file: the declaration pass
  over all members first (so mutually referring types, aliases and functions see each other), then
  each member's body / initializer (a constant group member at its iota), then the group-wide checks
  a file gets — by-value type cycles and value embedding, constant cycles, variable initialization
  cycles.  Outcomes: any missing name → the group stays parked (undone, missing names updated); else
  any error → the group fails (below); else it resolves and stays bound.
- **B4 — a failed retry stays parked** (claude-notes.md: "if `g` matches what `f` expects, `f`
  validates.  If not ... `f` remains pending").  It is undone, keeps its entry, and is re-checked on
  every sweep; its errors are shown when it first fails and whenever they change.  A declaration that
  fails at its own prompt is still rejected outright (not parked).
- **B5 — every declaring prompt sweeps,** including one that only parks: a park can complete a
  mutually recursive group.
- **B6 — a newer declaration replaces an older parked one.**  A declaration that resolves or parks at
  its prompt drops every older parked entry with the same name (method: same `T.M`).  One that fails
  at its prompt drops nothing.
- **B7 — a type redefinition is an error** (until type shadowing lands — claude-todo.md "The REPL
  silently ignores a type redefinition"): a `type T` whose name is bound to a type in the session
  scope is rejected at its prompt, and a parked `type T` whose name has since been bound fails.

## Emission of a resolved group (REPL)

Types first (register every member's struct / alias shell before building any member's fields, so
mutually referring types do not fall back to `int`; then their helpers), then constants (at their
iota), then functions and methods (helpers drained first; a member that redefines a bound function
chooses replace or shadow against the definition it replaces, recorded when the group resolved), then
variables (materialize all, then run initializers in dependency order).  Messages: one "X resolved"
line per member, dependencies first.

## Deletable

Type IsPending and every capturePendingIfSized call site, captureFuncSigPendingDeps,
checkDeclTypeTentative's placeholder juggling, the type-retry branch of RetryPendingDecls,
FindFreshCycles and the "pending cycle" message (a real cycle is now the group's check error), the
TentativeMode skip in checkIdent, the REPL's parkSnaps and RollbackDeclOf.

## Visible behaviour changes (e2e/repl.sh)

- Resolution order is dependencies first (case 22: b then a).
- A declaration using a parked one parks on it; a pointer to a parked type parks (cases 40, 41).
- A by-value type cycle is reported as the group's error, with no "pending cycle" line (case 44).
- A failed retry prints its errors and "X still parked (it does not check)".
- `type T` twice is an error.

## Not in this change (surface to the user)

- `impl` and `interface` declarations are checked strictly and never park; with a method parked,
  `impl T : I` reports the method missing.

## Steps

1. check: tentative declaration pass; park = undo; errUndefined / method-miss / constval capture;
   parked-name error at the prompt; B7.
2. check: group sweep (graph, groups, group check with the file-level checks, outcomes, B4, B6);
   delete the pending-type machinery and FindFreshCycles.
3. irgen / repl: group emission (type shells first; replace / shadow at resolve; variable
   initializer order); B5; failed-retry messages.
4. Tests: check unit tests per rule; repl unit tests for each reproduced bug and for mutual
   recursion; e2e/repl.sh expectations.
