# Plan: REPL forward references — dependencies park, groups resolve or fail together

Status: IN PROGRESS (work-6, 2026-09-29).  User: "Do the full rework."

## Why

The REPL's Tier 3 forward references are *optimistic*: a declaration checked
tentatively (a function body, a variable or constant initializer) that uses a
name bound to a PARKED declaration resolves against the parked declaration's
signature and is emitted, betting the parked declaration will be emitted later.
The bet fails whenever the parked declaration never resolves, and — since a
retry that resolves its missing names but fails to check is now reported and
undone (commit 1699f203c on work-6, not landed) — whenever it fails.  Findings
from that commit's review (all reproduced):

1. A redefinition of a parked name leaves the old parked entry; when that
   entry's retry fails, its rollback overwrites the valid later redefinition
   (`var x int = y` / `var x int = 5` / `var y bool = true` → `undefined: x`;
   the method form removes a valid method).
2. A type whose retry fails has its IsPending cleared before the rollback, so
   declarations parked on it resolve in the same sweep, then the type is
   undone — IR-gen panics "unresolved selector", killing the REPL.
3. A declaration using a parked declaration is not parked on it (checkIdent
   skips IsPendingDecl in TentativeMode), so it can be emitted before — or
   instead of — its dependency: `func g() int { return f() }` /
   `func f() int { return y }` / `var y bool = true` → f fails, g is resolved,
   `g()` panics "extern not found: main.f".  (Pre-existing variant: f never
   resolves at all.)
4. Parking a redefinition of an existing type mutates that type in place
   (collectTypeDecl returns early on the existing TYP_NAMED, and
   checkDeclTypeTentative clears its Underlying), so the old type cannot be
   restored.

## The model

- **R1 — a use parks on what it uses.**  A declaration checked tentatively
  that uses a name bound to a parked declaration — a variable, constant or
  function by name, a method of a parked method declaration, or a parked type
  in a use that needs its layout (today's sized-use capture) — records it as a
  missing name, so the using declaration parks on it.  The exception is a
  name being resolved in the same group (R3), whose members' uses of one
  another do not park.  (Open for review: whether a pointer-only use of a
  parked type, which today deliberately does not park, should.)
- **R2 — a redefinition replaces a parked declaration.**  A declaration that
  checks at its prompt (resolves, or parks) for a name drops every older
  parked declaration of that name — a method, every older parked method of the
  same receiver base type and name — with its kept snapshot.  A declaration
  that fails at its prompt drops nothing: its rollback restores the earlier
  binding, and an older parked declaration stays parked.
- **R3 — resolve by groups, dependencies first.**  A retry sweep builds the
  "parks on" graph over the parked declarations (an edge from each to every
  parked declaration named in its missing names) and processes its strongly
  connected groups in dependency order.  A group whose missing names are all
  bound — to resolved declarations or to its own members — is re-checked with
  its members' names in the resolving set.  If every member checks with no
  errors and no new missing names, the group resolves: all members are
  emitted.  If any member reports errors, the group fails: all their errors
  are reported and every member is undone from its snapshot.  If a member
  finds a new missing name, the group stays parked (missing names updated).
  Because groups are processed dependencies first, one sweep reaches the
  fixpoint.
- **R4 — every declaring prompt retries,** including one that only parks: a
  park can complete a mutually recursive group (`f` uses `g`, then `g` uses
  `f`).
- **R5 — a failed type stays pending until undone:** its placeholder keeps
  IsPending and no Underlying, so nothing resolves against it.
- **R6 — parking a type redefinition does not touch the existing type:** the
  parked declaration gets a fresh placeholder; the existing type keeps its
  layout until the new declaration resolves (then the name is rebound).

## Visible behaviour changes (e2e/repl.sh expectations to update)

- Resolution order is dependencies first: with `a` parked on `b` and `b` on
  `c`, `c` arriving prints `function b resolved` before `function a resolved`
  (today: a then b, a having resolved against b's parked signature).
- A declaration using a parked one now parks on it ("parked (pending: f)")
  instead of resolving immediately.

## Steps

1. check: R1 (checkIdent / method / selector uses of parked declarations
   capture the name unless in the resolving set); R5; the resolving set.
2. check: R3 — RetryPendingDecls computes the groups, re-checks each, and
   returns resolved groups and failed groups (errors migrated).
3. check / repl: R2 — drop superseded parked entries (and snapshots) after a
   successful or parking declaration.
4. repl: R4; failed groups undone from their snapshots; resolved groups
   emitted in dependency order.
5. check: R6.
6. Tests: check unit tests per rule; repl unit tests for each review finding
   (1–4) and for mutual recursion; e2e/repl.sh expectations updated.
