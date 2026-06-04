# Plan: REPL Tier 3 — Pending types / vars / consts

> **Status: COMPLETE (shipped); kept for design rationale.**
> An addendum to `plan-repl.md`'s "Tier 3 follow-ups" entry,
> expanding the design for pending non-func decls.  Tier 3
> first cut (2026-05-05) shipped pending-validation for `func`
> decls only; this doc described how to extend it to `type` /
> `var` / `const`, which shipped over 9 commits between
> 2026-05-28 and 2026-05-29.
>
> Tier 3 forward refs are now functionally complete: every
> top-level decl kind parks on forward-referenced dependencies,
> use-site propagation works through sized contexts, the per-
> caller sized-vs-reference distinction preserves recursive
> types via pointers, and unbreakable cycles get a clean
> diagnostic at park-close time.

## Background — what Tier 3 shipped, what's missing

The first cut of Tier 3 added a pending-validation queue
(`pkg/types/check_pending.bn`) keyed on a `PendingDecl` record
holding the AST decl and the names it was missing.
`CheckDeclInScope` runs the body check in `TentativeMode`:
`errUndefined` routes captured names into `c.TentativeMissing`
instead of surfacing.  If TentativeMissing is non-empty after
the body check, the decl is parked; otherwise any captured
errors migrate out as real errors.  `RetryPendingDecls`
re-attempts each parked decl when a subsequent prompt entry
might have bound the missing names; resolved decls return for
IR-gen + lowering.  Use-site references to a still-parked
function surface a clean checker error (`function f is
unresolved`) rather than an opaque runtime "extern not found".

The first cut wired only `DECL_FUNC` through tentative mode.
So at the prompt:

```
> type T struct { F Bag }
<repl>:1:21: undefined: Bag        ← fires immediately, T isn't registered
> type Bag struct { N int }
> // T is gone — the earlier decl was rejected
```

vs the analogous func case:

```
> func f() int { return g() + 1 }
function f parked (pending: g)
> func g() int { return 41 }
function f resolved
> f()                              → 42
```

The asymmetry was the user-visible footgun this work closed.

## Stages (as shipped)

Three independent stages plus cycle detection, shippable in
order.  Stage 1 is the smallest and gives the biggest user-
visible win; Stage 2 is the structural piece plan-repl.md flags
("substantial structural work"); Stage 3 fell out of Stage 2.

### Stage 1: Pending vars + consts — initializer parking

`var x T = expr` and `const N T = expr` where `expr` references
undefined names.  Type `T` is assumed to resolve strictly (Stage
1 doesn't introduce pending types).  Closely mirrors DECL_FUNC:
DECL_VAR / DECL_CONST branches route their initializer body
checks through TentativeMode the same way DECL_FUNC does.

Pass 1 (`collectDecls`) registers the symbol unconditionally
(so use sites referring to `x` after a parked-but-defined entry
don't error); pass 2 tentatively-checks the initializer.

IR-gen side: if the decl is pending after CheckDeclInScope,
`evalReplDecl` skips GenDecl / MaterializeOneGlobal /
runReplVarInit (same gate as DECL_FUNC's `IsDeclPending` check).
When `RetryPendingDecls` resolves the var, the REPL driver runs
those steps.

Per-member group parking: `PendingDecl` gains `IotaIdx`,
`Checker` gains `PendingMark`; `checkGroupDeclTentative`
iterates group members with per-member park decisions and
positional iota.  IR-gen `genConstGroup` skips parked members
(still incrementing iota); `GenConstMember` covers the retry
path.

```
> const ( A=iota; B=M+iota; C=iota )
constant B parked (pending: M)
> println(A); println(C)
0
2                            ← positional iota preserved across parking
> const M int = 10
constant B resolved
> println(B)
11                           ← B = 10 + 1 (iota at position 1)
```

**Stage 1 follow-ups not in scope.**
  - Untyped var with non-literal initializer (`var x = g() + 1`)
    parks but its symbol isn't entered in scope until resolved
    — use sites get "undefined: x" rather than "variable x is
    unresolved".  First-cut limitation; users spell the type
    explicitly.  Same applies to untyped const.
  - Pending var redefinition while parked.  Tier 4 territory;
    not currently exercised.

**Why park the whole decl rather than reserve-now-init-later.**
For `var x int = foo()` (foo forward), the slot could be
reserved now (since `int` is sized) with only the initializer's
*evaluation* parked.  We park the whole decl instead:
subsequent uses of `x` get the "variable x is unresolved" error,
matching DECL_FUNC's parked behavior exactly, with no surprising
mid-pending observations (e.g. `println(x)` seeing 0, then a
different value later).  Reserve-now-init-later is a possible
follow-up if users want it.

### Stage 2: Pending struct types — the structural piece

`type T struct { F Bag }` (or any struct-typed decl) that
references undefined names in its field types.

**Why this is bigger.**  A pending struct's symbol must be
"unsized" — its layout depends on the missing name's resolution.
Unlike a pending var (one slot, parked), a pending type
propagates: any use of `T` is itself implicitly pending.
Use sites need to either (a) park transitively, or (b) tolerate
unsized types in non-sizing contexts (`@T`, `*T`, `@[]T` all
have known size regardless of T).

**Substrate — explicit `IsPending bool` on `@Type`.**  Not a
repurpose of the `Underlying == nil` state on TYP_NAMED:
`preRegisterTypeNames` already uses `Underlying == nil` for the
half-defined intermediate state, and that interpretation is
load-bearing in several places (where Underlying is transiently
nil during normal collection).  Overloading it would be brittle,
so the pending state is an explicit field.

`CheckDeclInScope`'s DECL_TYPE branch dispatches through
`checkDeclTypeTentative`: `preRegisterTypeNames` installs the
placeholder, then `collectTypeDecl` runs in TentativeMode.  On
park, IsPending goes on the placeholder Type and Underlying is
cleared so retry can re-resolve.  On clean retry, IsPending
clears.

**Use-site propagation — three classes of use:**
  1. **Sized use** — `var x T`, `type T2 struct { Inner T }`,
     `func f() T`.  Needs T's layout.  → Use site parks too,
     capturing T as a pending dependency.
  2. **Reference use** — `@T`, `*T`, `@[]T`, `[]T`.  Pointer /
     slice header size is independent of T's layout.  → Use
     site is fine.  Pointers to pending types are pointers like
     any other.
  3. **Method receiver** — `func (t *T) M() { ... }`.  Pointer-
     receiver methods on a pending T park; the method can't be
     checked until T's fields are known (the body might
     dereference fields).

The mechanism: `capturePendingIfSized(c, t)` — if `t.IsPending`,
capture `t.Name` as a pending dep (no-op outside TentativeMode).
Sized-use call sites are wired through it (struct fields, func
params/results, typed-var / typed-const TypeRef sites, alias
target, named-non-struct underlying, composite literals, impl
sites).  Reference-use sites (pointer / managed-ptr / slice /
managed-slice elements inside `resolveTypeExpr`'s recursive
cases) do NOT call it — those wrappers are size-stable.

Func-sig parking: `captureFuncSigPendingDeps` re-resolves
d.Recv / d.Params / d.Results in TentativeMode so a func whose
sig references a pending type parks.  Called from both
`CheckDeclInScope`'s DECL_FUNC tentative pass AND
`RetryPendingDecls`'s DECL_FUNC retry path — otherwise a
transitively parked func could be erroneously reported as
resolved on retry.

**Symbol-vs-completion distinction.**  Pending types have a
two-stage life:
  1. Symbol exists, IsPending=true (registered, but unsized).
  2. Symbol resolved (Underlying populated, IsPending=false).

Lookups against a stage-1 symbol return the pending type, NOT
a missing-name error.  This is critical — without it,
mutually-recursive struct decls couldn't ever resolve.  The
TentativeMissing capture happens at the *sized-use site*, not
at the symbol lookup itself.

**IR-gen interaction.**  IR-gen runs only on resolved decls.
A pending type passing into IR-gen would crash (helper
generation consults Type's Fields / Elem etc.), so
`evalReplDecl` gates on IsDeclPending and skips IR-gen for
pending types just like for pending funcs.  `evalReplDecl`'s
DECL_TYPE path also calls retryPending before returning — a
freshly-resolved type may unblock decls parked on its name.
`backfillExternCachesForName` handles late-arrival of helper-
name CallCache slots, same as elsewhere — no new VM-side work.

```
> type A struct { Next @B }
type A parked (pending: B)
> type B struct { Next @A }
type A resolved             ← mutual recursion via pointers
                              resolves via reference-use distinction
```

**Stage 2 follow-ups not in scope.**
  - **Generic type decls** (`type List[T] struct { ... }`).
    Skipped by the existing generic decl dispatch; tentative
    routing for generic instantiation is a separate concern.

### Stage 3: Pending aliases and named-non-struct

Aliases (`type R = X`) and named-non-struct (`type Celsius
Heat`) parking work end-to-end as a fallout of the unified
DECL_TYPE tentative dispatch in `checkDeclTypeTentative`.  No
separate Stage 3 commit was needed: `type R = Bag` parks via
the alias branch's capturePendingIfSized on `target`;
`type Celsius Heat` parks via the named-non-struct branch's
capturePendingIfSized on `underlying`.  (The original plan
expected a small separate substrate here, plus edge cases like
alias-to-pending-struct and alias-to-alias; those all fell out
of the unified dispatch.)

### Stage 4: Cycle detection

After Stage 2, mutual recursion through TYPE decls is possible
(e.g. `type A struct { Next @B }` / `type B struct { Next @A }`).
This is handled by the placeholder + IsPending representation
rather than by detecting cycles — both decls park, then both
resolve when the cycle closes.

A *genuine* cycle (no resolution — like `type A struct { B B }` /
`type B struct { A A }` where both are sized fields) would stay
parked forever.  Cycle detection converts this into an immediate
diagnostic.  Not strictly required for correctness (the user can
type something to break the cycle) but a real UX improvement.

Implementation:

  - `pkg/types/check_pending.bn`:
      * `FindFreshCycles(fromIdx)` — walks
        `c.Pending[fromIdx:]` (decls parked during the most-
        recent CheckDeclInScope call) and returns cycles
        their missing-names chains close in the global
        pending graph.  Each cycle is a list of names
        starting AND ending with the same name (e.g.
        `["B", "A", "B"]` for the canonical pair).
      * `findCyclePathFromName` + `dfsCycleSearch` — recursive
        DFS worker with a per-branch visited set so adjacent
        non-cycle dependencies don't produce false positives.
      * `pendingByName` — kind-agnostic pending-by-name
        lookup; sibling of `check_expr.bn`'s `lookupPending`,
        duplicated to keep cycle detection self-contained.

  - `cmd/bni/repl_decl.bn`:
      * `announcePendingCycle(path)` prints
        `pending cycle: A -> B -> A`.
      * `evalReplDecl` calls FindFreshCycles after the
        parked-announce loop, before retryPending — fresh
        parks are accurately at `c.Pending[c.PendingMark:]`
        before retry replaces the slice.

```
> type A struct { B B }
type A parked (pending: B)
> type B struct { A A }
type B parked (pending: A)
pending cycle: B -> A -> B
```

The user knows to break the cycle (e.g. switch one field to
a pointer); decls stay parked, so a subsequent redefine
either resolves them (cycle broken) or re-fires the cycle
warning.

## Design decisions (resolved)

  - **Sized-vs-reference flag plumbing — per-caller check.**
    Threading a flag through `resolveTypeExpr`'s recursive
    sites is intrusive.  Instead, each caller checks
    `IsPending` on the returned type and decides what to do:
    callers that need a sized type capture the dependency;
    pointer / slice / managed-ptr / managed-slice callers
    don't.  Small number of callers, clearer per-site
    decisions.

  - **Pending-method registration — methods on a pending
    receiver are themselves pending.**  `func (t *T) M() { ... }`
    when T is pending: the method goes into T's MethodSet via
    `SetOrAppendMethod`, and the method itself parks until T
    resolves (its receiver dereference depends on T's layout).
    Retry runs when T resolves; the method's body check runs
    then.  Symmetric with how a pending free function works.

  - **Const-group atomicity — per-member parking.**  Const
    groups are *syntactic sugar with no semantic effect*, so
    `const ( N1 = foo(); N2 = 1 )` behaves identically to two
    individual `const N1 = foo()` / `const N2 = 1` decls — N1
    parks, N2 lands.  Pending-decl identity is per-member
    (one PendingDecl per member, not per group), with positional
    iota preserved across parked members.

  - **Forward refs to pending decls from a parked decl's
    body — captured as a pending dependency.**  `func f() T`
    where T is pending: f captures "T is pending" into its
    TentativeMissing, parks, and resolves when T resolves.
    Falls out of the existing machinery — the right
    distinction is in the error wording at the use site
    ("T is unresolved" while T is pending → routes to
    TentativeMissing in tentative mode, surfaces as a
    "function f is unresolved" message otherwise).

## What this plan is NOT

  - A relitigation of REPL semantics — see `claude-notes.md`
    ("Forward references & REPL model — DECIDED").
  - A redesign of the existing Tier 3 substrate — the
    pending-decl queue + tentative-mode error routing are
    the right mechanism; we're extending what decl kinds
    feed into them.
  - Tier 4 (redefinition) work — orthogonal.  A parked decl
    that gets redefined while parked just replaces the parked
    entry; no interaction with replace/shadow paths.
  - A perf concern.  REPL workloads are interactive; the
    parked queue stays small.  No optimization needed.

## Adjacent things this plan touches

  - **`claude-notes.md` "Forward references & REPL model":**
    no change.  The decision was "deferred validation handles
    forward references; errors surface at use, not at
    definition" — this plan implements that decision for the
    remaining decl kinds.

  - **`plan-function-values.md`:** no direct interaction.
    Function value identity is about stable handles to a
    binding; pending decls don't yet have a binding to point
    at.

  - **Compiler/interpreter interop:** no change.  Pending decls
    are an interpreter-side concept (compiled code is
    closed-world).
