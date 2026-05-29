# Plan: REPL Tier 3 — Pending types / vars / consts

> **Status: ALL STAGES LANDED (2026-05-29).**
> An addendum to `plan-repl.md`'s "Tier 3 follow-ups" entry,
> expanding the design for pending non-func decls.  Tier 3
> first cut (`b470bb0`, 2026-05-05) shipped pending-validation
> for `func` decls only; this doc described how to extend it
> to `type` / `var` / `const` and shipped that work over 9
> commits between 2026-05-28 and 2026-05-29:
>
>   * Stage 1 (vars + consts incl. per-member group parking):
>     `312e2ffc` + `6769786e` + `573766e1`.
>   * Stage 2 (a) substrate (IsPending field + helper):
>     `fcabdb33`.
>   * Stage 2 (b)+(c) DECL_TYPE parking + use-site propagation:
>     `23367e32`.
>   * Stage 2 (d) sized-use audit closures + func-sig parking
>     + broader coverage: `bcf8790a` (this also implicitly
>     closes Stage 3 — aliases and named-non-struct fall out
>     of the unified DECL_TYPE tentative dispatch).
>   * Stage 2 (e) methods on pending receivers: `183a8db1`.
>   * Stage 4 cycle detection: `c0cc7c03`.
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

What's missing: only `DECL_FUNC` is wired through tentative
mode.  In `CheckDeclInScope`:

```binate
if d.Kind == ast.DECL_FUNC {
    c.TentativeMode = true
    checkDecls(c, single)
    c.TentativeMode = false
    ...
} else {
    checkDecls(c, single)   // strict — undefined names fire immediately
}
```

So at the prompt today:

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

The asymmetry is the user-visible footgun.

## Decomposition

Three independent stages, shippable in order.  Stage 1 is the
smallest and gives the biggest user-visible win; Stage 2 is
the structural piece plan-repl.md flags ("substantial structural
work").  Stage 3 is a small follow-up.  Stages can be split
further into per-commit pieces.

### Stage 1: Pending vars + consts — initializer parking — LANDED 2026-05-28

Shipped in three commits:

  - `312e2ffc` (a) substrate — `IsPendingFunc` generalized to
    `IsPendingDecl`, `errPendingFunc` → `errPendingDecl` with
    per-kind wording, `CheckDeclInScope` routes DECL_VAR /
    DECL_CONST / DECL_GROUP through TentativeMode via
    `isParkableKind`.
  - `6769786e` (b) driver wire-up — `announceParked` uses
    per-kind wording ("variable x", "constant N", "function f"),
    `retryPending` extends to DECL_VAR (materialize + run init
    synthetic) and DECL_CONST (GenDecl is enough).
  - `573766e1` (c) per-member group parking — `PendingDecl`
    gains `IotaIdx`, `Checker` gains `PendingMark`, new
    `checkGroupDeclTentative` iterates group members with
    per-member park decisions and positional iota.  IR-gen
    side: `genConstGroup` skips parked members (still
    incrementing iota); new `GenConstMember` for the retry
    path.

End-to-end behaviors verified by 4 e2e cases + 8 unit tests:

```
> var x int = g() + 1
variable x parked (pending: g)
> func g() int { return 41 }
variable x resolved
> println(x)
42

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

The original Stage 1 design notes follow for historical
reference.

**Scope.**  `var x T = expr` and `const N T = expr` where `expr`
references undefined names.  Type `T` is assumed to resolve
strictly (i.e. existing types — Stage 1 doesn't introduce
pending types).  Drops the easy-but-common case: forward-ref
to a func or a const, in an initializer.

**Shape — closely mirrors DECL_FUNC.**  In `CheckDeclInScope`,
add DECL_VAR / DECL_CONST branches that route their body
checks through TentativeMode the same way DECL_FUNC does.  For
DECL_VAR / DECL_CONST the "body" is the initializer expression
(`d.Value`).

**Substrate change.**  Currently `collectDecls`'s DECL_VAR
branch resolves the type and defines the symbol *during pass 1*;
the initializer is checked in `checkDecls` (pass 2).  We want
pass 1 to register the symbol unconditionally (so use sites
referring to `x` after a parked-but-defined entry don't error),
and pass 2 to tentatively-check the initializer.

Slot the existing `if d.Kind == ast.DECL_VAR` block in
`CheckDeclInScope`'s pass 2 dispatch — same toggle, same park
condition, same migrate-tentative-errors fall-through.  Same
for DECL_CONST.

**What gets parked.**  A `PendingDecl` for a var carries the
AST decl (already does today) and the missing-name list.
`RetryPendingDecls` already runs `checkDecls(c, single)` on the
parked decl — for a var, that re-checks the initializer.  If
names resolve, the var is "resolved" and returned for IR-gen.

**IR-gen side.**  `evalReplDecl` for DECL_VAR currently does:
1. `ir.GenDecl(d, m)` — appends to moduleGlobals + m.Globals.
2. `vm.MaterializeOneGlobal(g)` — allocates the slot.
3. `runReplVarInit` — runs the synthetic initializer.

If the decl is pending after CheckDeclInScope, we skip 1-3 (same
gate as DECL_FUNC's `IsDeclPending` check).  The symbol is in
scope but the slot isn't allocated and the initializer hasn't
run.  Use-site reads of `x` while it's pending should surface
the same kind of "x is unresolved" error the func path gives.

When `RetryPendingDecls` resolves the var, the REPL driver runs
steps 1-3 — same shape as `retryPending`'s DECL_FUNC branch.

**Use-site error for unresolved var/const.**  Add an
`IsPendingVar` / `IsPendingConst` (or unify with `IsPendingFunc`
into `IsPendingDecl(name)`) and have `checkIdent` emit
"variable X is unresolved (pending: ...)" when not in
TentativeMode.

**Open question — typed-but-unresolved-initializer var.**  Should
`var x int = foo()` (where `foo` is forward) reserve the slot
NOW (since `int` is sized), and only park the initializer's
*evaluation*?  This is a slightly different mental model than
"park the whole decl."  Tradeoff:
  - **Reserve now + park init**: subsequent prompt entries see
    `x` with value 0.  Initializer runs when foo resolves.  More
    surprising semantics (the user might `println(x)` and see
    0, then see a different value later).
  - **Park the whole decl**: subsequent uses of `x` get the
    "variable x is unresolved" error.  Matches DECL_FUNC's
    parked behavior exactly.

Recommendation: park the whole decl.  Consistent with funcs;
no surprising mid-pending observations.  Reserve-now-init-later
is a possible follow-up if users want it.

**Coverage.**  Mirror the func cases in `e2e/repl.sh`:
  - `tier3-pending-var-resolves`: park, then resolve, then
    read.
  - `tier3-pending-const-resolves`: same with const.
  - `tier3-pending-var-use-site-error`: use site of a parked
    var surfaces a clean error.

Unit tests in `pkg/types/check_pending_test.bn`:
  - DECL_VAR parking captures TentativeMissing.
  - DECL_CONST same.
  - Retry resolves both.
  - Migration of tentative errors when the body is clean but
    has a real type error.

### Stage 2: Pending struct types — the structural piece — LANDED 2026-05-28

Shipped in four commits.

  - `fcabdb33` (a) substrate — `Type.IsPending bool` field +
    package-private `capturePendingDep` helper.  No behavior
    change yet (no call sites consult IsPending or call the
    helper).
  - `23367e32` (b)+(c) DECL_TYPE parking + use-site propagation:
    * `CheckDeclInScope`'s DECL_TYPE branch dispatches through
      a new `checkDeclTypeTentative` in `check_pending.bn`.
      preRegisterTypeNames installs the placeholder, then
      collectTypeDecl runs in TentativeMode.  On park, IsPending
      goes on the placeholder Type and Underlying is cleared so
      retry can re-resolve.  On clean retry, IsPending clears.
    * `RetryPendingDecls` extends for `DECL_TYPE` (re-runs
      collectTypeDecl, clears IsPending on success).
    * `IsPendingDecl` / `lookupPending` / `pendingDeclKindNoun`
      extend to include DECL_TYPE.
    * New `capturePendingIfSized(c, t)` helper — if t.IsPending,
      capture t.Name as a pending dep.  No-op outside
      TentativeMode (capturePendingDep returns early).
    * Sized-use call sites wired through capturePendingIfSized:
      `resolveStructType` (each struct field), `resolveFuncDeclType`
      (each param + result), `collectDecls` + `checkVarDecl` +
      `checkConstDecl` (all typed-var / typed-const TypeRef
      sites), `collectTypeDecl` (alias target + named-non-struct
      underlying).
    * Reference-use sites (pointer / managed-ptr / slice /
      managed-slice elements inside `resolveTypeExpr`'s
      recursive cases) do NOT call capturePendingIfSized — those
      wrappers are size-stable.
    * `repl_decl.bn`: parkedDeclLabel handles DECL_TYPE.
      retryPending lowers helpers added by GenDecl for resolved
      DECL_TYPE.  evalReplDecl's DECL_TYPE early-return now
      calls retryPending before returning — a freshly-resolved
      type may unblock decls parked on its name.
  - `bcf8790a` (d) sized-use audit closures + func-sig parking
    + coverage:
    * `captureFuncSigPendingDeps` re-resolves d.Recv / d.Params /
      d.Results in TentativeMode so a func whose sig references
      a pending type parks.  Called from `CheckDeclInScope`'s
      DECL_FUNC tentative pass AND from `RetryPendingDecls`'s
      DECL_FUNC retry path (otherwise a transitively parked func
      could be erroneously reported as resolved on retry).
    * `check_impl.bn` / `check_expr_composite.bn` routed through
      capturePendingIfSized — closes the audit at the last two
      sized-use sites that weren't covered by (b)+(c).

End-to-end behaviors verified by 5 e2e cases + 8 unit tests:

```
> type T struct { F Bag }
type T parked (pending: Bag)
> type Bag struct { N int }
type T resolved
> var x T
> x.F.N = 42; println(x.F.N)
42

> type T struct { F Bag }
type T parked (pending: Bag)
> var x T
variable x parked (pending: T)
> type Bag struct { N int }
type T resolved
variable x resolved
> x.F.N = 7; println(x.F.N)
7

> type A struct { Next @B }
type A parked (pending: B)
> type B struct { Next @A }
type A resolved             ← mutual recursion via pointers
                              resolves via reference-use distinction

> type T struct { F Bag }
type T parked (pending: Bag)
> func f(x T) int { return 0 }
function f parked (pending: T)
> type Bag struct { N int }
type T resolved
function f resolved
```

Stage 2 (d) implicitly closes Stage 3 as well — aliases and
named-non-struct fall out of the unified DECL_TYPE tentative
dispatch.  E.g. `type R = Bag` parks via the alias branch's
capturePendingIfSized on `target`; `type Celsius Heat` parks
via the named-non-struct branch's capturePendingIfSized on
`underlying`.

**Stage 2 follow-ups not in scope.**
  - **Methods on a pending receiver type** (was in the original
    design as Stage 2's "pending-method registration" point;
    bumped here to Stage 2 (e)).  `func (t *T) M() { ... }`
    when T is pending: today the method registers on T's
    method set in pass 1 strict, doesn't park.  To make it
    park, DECL_FUNC.Recv != nil needs its own tentative
    dispatch — `collectMethodDecl` is in pass 1.  Open work.
  - **Generic type decls** (`type List[T] struct { ... }`).
    Skipped by the existing generic decl dispatch; tentative
    routing for generic instantiation is a separate concern.

The original Stage 2 design notes follow for historical
reference.

### Stage 2: Pending struct types — the structural piece

**Scope.**  `type T struct { F Bag }` (or any struct-typed decl)
that references undefined names in its field types.  Aliases
and named-non-struct are Stage 3.

**Why this is bigger.**  A pending struct's symbol must be
"unsized" — its layout depends on the missing name's resolution.
Unlike a pending var (one slot, parked), a pending type
propagates: any use of `T` is itself implicitly pending.
Use sites need to either (a) park transitively, or (b) tolerate
unsized types in non-sizing contexts (`@T`, `*T`, `@[]T` all
have known size regardless of T).

**Substrate — type symbol pending flag.**  Add `IsPending bool`
to `@Type` (or repurpose the existing `Underlying == nil` state
on TYP_NAMED — `preRegisterTypeNames` already uses that
representation for the half-defined intermediate state).  The
distinction needs to be explicit so other paths (where
Underlying is transiently nil during normal collection) don't
get falsely flagged.

Recommendation: explicit `IsPending` field.  The intermediate-
state interpretation of `Underlying == nil` is load-bearing in
several places and overloading it would be brittle.

**Pending registration.**  `CheckDeclInScope` for DECL_TYPE runs
the field-resolution work in TentativeMode.  If
TentativeMissing is non-empty:
  - The placeholder named type already in scope from
    `preRegisterTypeNames` stays.
  - Mark its `IsPending = true`.
  - Park the decl (with its missing-name list).
  - On RetryPendingDecls: re-run `collectTypeDecl(c, pd.Decl)`
    in tentative mode; if it succeeds, clear `IsPending` and
    fill `Underlying`.

**Use-site propagation.**  Three classes of use:
  1. **Sized use** — `var x T`, `type T2 struct { Inner T }`,
     `func f() T`.  Needs T's layout.  → Use site must park
     too.  Capture T as a missing name in TentativeMissing
     (a "pending dependency").
  2. **Reference use** — `@T`, `*T`, `@[]T`, `[]T`.  Pointer /
     slice header size is independent of T's layout.  → Use
     site is fine.  Pointers to pending types are pointers
     just like any other.
  3. **Method receiver** — `func (t *T) M() { ... }`.  Pointer-
     receiver methods on a pending T should park; the method
     can't be checked until T's fields are known (the body
     might dereference fields).

`resolveNamedTypeExpr` returns the resolved Type; sized vs
reference is determined by the *caller* (which AST node embeds
this resolution).  The cleanest place to enforce sized-use
parking is in `resolveTypeExpr`'s callers — pass through an
"is this a sized context" flag, or have callers check
`.IsPending` on the resolved type and route appropriately.

**Retry trigger.**  Currently a retry runs after every
successful prompt-decl.  For pending types, the trigger needs
to also fire when a previously-parked decl resolves (since
that may complete a chain of pending types).  This actually
falls out for free: `RetryPendingDecls` retries every entry,
including newly-arrived ones; the loop runs until no decl
moves from pending to resolved.  Tier 3 first cut already
handles this via the simple "loop once per prompt entry"
pattern.

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
But the existing helper generation (struct dtors / copies,
field-type managed-slice dtors) consults Type's Fields /
Elem etc.  A pending type passing into IR-gen would crash —
`evalReplDecl` must gate on IsDeclPending and skip IR-gen for
pending types just like it does for pending funcs.

`backfillExternCachesForName` will handle the late-arrival of
helper-name CallCache slots, same as elsewhere — no new VM-side
work.

**Coverage.**  e2e cases:
  - `tier3-pending-type-resolves`: `type T struct { F Bag }`
    parks; defining `Bag` resolves both.
  - `tier3-pending-type-chain`: `type A struct { F B }`,
    `type B struct { F C }`, `type C struct { N int }` —
    all park, all resolve when C arrives.
  - `tier3-pending-type-mutual-recursion`: `type A struct {
    Next @B }`, `type B struct { Next @A }` — both park
    waiting on each other; the placeholder + IsPending
    representation lets them resolve simultaneously.
  - `tier3-pending-type-pointer-use-ok`: `type T struct { F
    Bag }` parks; `var p @T` is fine (reference use, doesn't
    need T's layout); `var x T` errors (sized use of pending
    type).
  - `tier3-pending-type-use-site-error`: `var x T` where T
    is pending errors with "T is unresolved".

Unit tests in `pkg/types/check_pending_test.bn`:
  - DECL_TYPE parking sets IsPending on the placeholder.
  - Resolution clears IsPending and populates Underlying.
  - Sized use of pending type fires the dependency capture.
  - Reference use of pending type doesn't fire.
  - Mutual recursion: two struct types parking on each other
    both resolve when the cycle completes.

### Stage 3: Pending aliases and named-non-struct — LANDED (implicitly) via Stage 2 (d)

Aliases (`type R = X`) and named-non-struct (`type Celsius
Heat`) parking work end-to-end as a fallout of the unified
DECL_TYPE tentative dispatch in `checkDeclTypeTentative`.  No
separate Stage 3 commit was needed.  e2e cases
`tier3-pending-alias-resolves` and `tier3-pending-named-
nonstruct-resolves` (both in `bcf8790a`) verify the
behaviors.

The original Stage 3 design notes follow for historical
reference.

### Stage 3: Pending aliases and named-non-struct

**Scope.**  `type R = Bag` (alias) and `type Celsius Foo`
(named non-struct), where `Bag` / `Foo` are undefined.

**Why separate from Stage 2.**  Aliases resolve to their target
directly — their handling is "target's IsPending propagates"
which is simpler than struct field analysis.  Named-non-struct
similarly resolves to a single underlying type, no field list
to traverse.

Mostly a smaller version of Stage 2's substrate, plus a few
edge cases (an alias to a pending struct, an alias to an
alias, etc.).  Expected to be ~1 commit.

### Stage 4: Cycle detection — LANDED 2026-05-29

Shipped via `c0cc7c03`.  Implementation:

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

Verified end-to-end:

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

The original Stage 4 design notes follow for historical
reference.

### Stage 4: Cycle detection

plan-repl.md's Tier 3 follow-ups list cycle detection for
"mutually-pending decls" today.  The first cut handles real
cycles trivially because `collectDecls` puts all sigs in
scope before checkDecls runs, so mutual-recursion through
function bodies just works.

After Stage 2, mutual recursion through TYPE decls becomes
possible (e.g. the `type A struct { Next @B }` / `type B
struct { Next @A }` case).  This is handled by the placeholder
+ IsPending representation rather than by detecting cycles —
both decls park, then both resolve when the cycle closes.

A *genuine* cycle (one with no resolution — like `type A
struct { B B }` / `type B struct { A A }` where both are
sized fields) would parked-forever today.  Cycle detection
would convert this into an immediate error ("type cycle: A → B
→ A").  Not strictly required for correctness — the user can
type something to break the cycle — but a real UX improvement.

Probably worth a separate small piece after Stage 2; can also
be deferred to whenever the user-visible footgun arises.

## Sequencing

  1. ~~**Stage 1** (vars + consts).~~  **LANDED** 2026-05-28
     across three commits: (a) `312e2ffc` substrate; (b)
     `6769786e` driver wire-up + e2e; (c) `573766e1` per-
     member group parking.  See the "Stage 1 ... LANDED"
     section above for what shipped and the e2e behaviors.
  2. ~~**Stage 2** (pending struct types).~~  **LANDED**
     2026-05-28 across four commits: (a) `fcabdb33` substrate
     (IsPending + capturePendingDep); (b)+(c) `23367e32`
     DECL_TYPE parking + use-site propagation; (d) `bcf8790a`
     coverage + func-sig audit + audit closures.  See the
     "Stage 2 ... LANDED" section above.  Stage 2 (e) methods-
     on-pending-receiver remains open.
  3. ~~**Stage 3** (aliases + named-non-struct).~~  **LANDED**
     implicitly via Stage 2 (d) — falls out of the unified
     DECL_TYPE tentative dispatch.
  4. ~~**Stage 4** (cycle detection).~~  **LANDED** 2026-05-29
     via `c0cc7c03`.  See the "Stage 4 ... LANDED" section
     above.

Final tally: 9 commits across all stages (Stage 1 = 3, Stage 2
= 5 incl. (e), Stage 3 = 0 standalone, Stage 4 = 1).  Tier 3
forward refs functionally complete.

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

  - **Const-group atomicity — first cut: park the whole
    group; follow-up: per-member.**  The right model is per-
    member parking: const groups are *syntactic sugar with no
    semantic effect*, so `const ( N1 = foo(); N2 = 1 )` should
    behave identically to two individual `const N1 = foo()` /
    `const N2 = 1` decls — N1 parks, N2 lands.  The current
    parser/checker layer registers groups atomically; making
    that per-member is more mechanism than the first-cut
    user-visible win warrants.  Land park-whole-group first
    to ship Stage 1; refine to per-member as a Stage 1
    follow-up commit.  The principle ("groups are syntactic
    sugar") should be checked elsewhere too — DECL_GROUP
    handling in collectDecls + checkDecls today already
    threads each member through individually for the most
    part, so the gap is mostly in pending-decl identity
    (today: one PendingDecl per group; want: one per member).

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
