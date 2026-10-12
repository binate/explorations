# Plan: `defer { … }` — a block run at scope exit, reading live variables

Status: ACCEPTED 2026-10-12 (work-4); plan review folded in 2026-10-12; transition order pending the user.  Supersedes the eager, function-scoped
`defer <call>` of `proposal-defer.md` (spec §14.13 as of docs `0cc5657`).

## Decision (user, 2026-10-11 / 2026-10-12)

- "I'm convinced that we should stray from Go."  Deferred code is evaluated when it runs, not at the defer
  statement (Go evaluates the callee and arguments at the defer statement; Zig / Swift / D evaluate the whole
  deferred code at exit).
- "Ok, scope exit with no copy sounds fine; it's a lot simpler."  The deferred code runs when the enclosing
  BLOCK exits, and reads and writes the variables as they are then (no snapshot copy — a copy would make
  `var buf Buffer; defer { buf.Flush() }` flush a stale copy).
- "I think we should require a block though: it distinguishes it from Go, which is important, *and* it's
  consistent with other control flow (e.g., if, for, etc. require a block)."

Earlier wording, kept for the record: "defer <stuff>; should be equivalent to __x := @func() { <stuff> };
defer __x();" (2026-10-11) — superseded by "no copy" above.

## Semantics

1. `DeferStmt = "defer" Block ;`  `defer f()` (no block) is a syntax error with a hint: "defer requires a
   block: `defer { f() }`".
2. Executing a defer statement evaluates NOTHING; it schedules its block on the innermost enclosing block (the
   defer statement's scope: a function body, a `{ }` block, an `if` / `else` body, a `for` body, a `switch` /
   type-switch case body).
3. When that block exits — falling off its end, or a `break`, `continue` or `return` passing out of it — its
   scheduled deferred blocks run, last scheduled first, and THEN its managed locals are released
   (`mem.scope-exit`).  An exit passing out of several blocks does this for each, innermost first.
4. Only executed defer statements are scheduled.  With no `goto` and every block a fresh scope per execution,
   which defers are pending at each exit point is known statically — no runtime flag, no record list, no
   allocation.
5. A `for` body is a block per iteration, so a defer in a loop body runs at the end of each iteration
   (including via `continue` / `break`).  `stmt.defer.no-loop` is removed.
6. The deferred block is ordinary code at the defer statement's position: it sees the names in scope there
   (not names declared later in the same block), reads and writes them as they are when it runs, and its
   statements have ordinary statement temporaries.  Writes are visible to code that runs afterwards (an inner
   block's deferred `n = 5` is seen by the code after that block).
7. On `return`: the return operands are evaluated and each managed result acquires its reference first; then
   the pending deferred blocks of every block the return passes out of run (innermost first, each block's
   releases after its defers); the results then transfer.  Deferred code cannot change a returned value.
8. `stmt.defer.no-abort` is unchanged: a panic / trap / exit runs no deferred block, and one inside a deferred
   block terminates the program at once.
9. Constraints: the deferred block shall not contain a `return`, nor a `break` / `continue` that would leave the
   deferred block (one targeting a loop or switch inside the deferred block is fine); a function literal's body
   is its own.  A defer inside a deferred block is allowed and runs at the end of that deferred block (rule 3
   applied to the deferred block).  A defer statement still requires an enclosing function (the REPL's
   immediate mode rejects it).

## Draft spec text (§14.13, replacing `stmt.defer`, `stmt.defer.call`, `stmt.defer.no-loop`, `stmt.defer.exit`,
`stmt.defer.return`; `stmt.defer.no-abort` kept)

> `stmt.defer` — A **defer statement** schedules its **block** to run when the **innermost enclosing block** —
> the block the defer statement appears in — exits (`stmt.defer.exit`):
>
> ```
> DeferStmt = "defer" Block ;
> ```
>
> Executing a defer statement evaluates **nothing**: the deferred block is ordinary code at the defer
> statement's position, run later.  It sees the names in scope at the defer statement (§9.5) and reads and
> writes those variables as they are **when it runs**; its statements have ordinary statement temporaries
> (§18.4 `mem.temporary`).  Only a defer statement that has executed schedules its block.  A defer statement
> inside a **function literal** belongs to that literal's body.  A defer statement is **not** a simple
> statement (§14.1) and requires an enclosing function; in the REPL's immediate mode, a `defer` entered with
> no enclosing function is rejected.
>
> > _Rationale._ The block makes the late evaluation visible — the deferred code runs at exit, like the body
> > of an `if` runs when the condition holds — and distinguishes the construct from Go's `defer f(x)`, which
> > evaluates `f` and `x` at the defer statement.
>
> `stmt.defer.block` _(Constraint)_ — A deferred block shall not contain a `return` statement, nor a `break` or
> `continue` that would transfer control out of the deferred block; a `break` / `continue` targeting a loop or
> switch inside the deferred block, and anything in a function literal's body, are unaffected.
>
> `stmt.defer.exit` — A block **exits** when control falls off its end or a `break`, `continue` or `return`
> transfers control out of it.  At each exit, the deferred blocks the block has scheduled run in **reverse
> order of their scheduling** (last first), and **then** the block's managed locals are released (§18.4
> `mem.scope-exit`).  A transfer out of several nested blocks does this for each of them, **innermost first**.
> A `for` body is a block per iteration (§14.x), so a defer in a loop body runs at the end of every iteration.
> Every block the deferred code can name a variable of is still live when it runs.
>
> > _Rationale._ Which defer statements have executed at an exit is known from the program text (no `goto`,
> > §14.15), so `defer` costs no hidden allocation and no runtime record, in a loop as anywhere else.
>
> `stmt.defer.return` — On a `return`, the return operands are evaluated and each **managed** result
> **acquires its owning reference first** (§18.5 `mem.return`); then each block the `return` leaves exits
> (`stmt.defer.exit`), innermost first; the retained results then transfer to the caller.  Deferred code
> observes the post-evaluation state but **cannot change a returned value** (results are unnamed and already
> evaluated).
>
> > _Note._ A returned **raw** value that borrows state a deferred block releases or mutates dangles exactly as
> > if that code were written textually before the `return` (§18.7 `mem.raw-uaf`).
>
> `stmt.defer.no-abort` — (unchanged, "deferred calls" → "deferred blocks").

Spec gaps the draft must close (plan review, 2026-10-12): a `stmt.for.scope` rule in §14.9 — the C-style init
variable is scoped to the `for` statement (one variable per loop), the body is a block per iteration that exits
(deferred blocks, then releases) before the post statement, range variables enclose the body and are released
after the body's deferred blocks; the type-switch binder belongs to its case's scope (§11.12 / §14.10); a case
clause body counts as "the block" for a defer (it is not a `Block` in the grammar); "scheduled during this
execution of the block"; "every variable the deferred code can name is live" (parameters are not in a block);
the return statement's own temporaries are released after the results' acquire and before the deferred blocks;
a defer statement is never terminating, even `defer { panic(…) }` (§14.14); the rationale gives the user's
reason (consistency with `if` / `for`, which require a block); §14.12 cites `stmt.defer.block`.

Knock-on spec and doc edits: `binate.ebnf` (DeferStmt; canonical — Annex A is generated from it with
`docs/scripts/gen-annex-a.py`) and the grammar comment in §14 (`14-statements.md`); §14.1 `stmt.block` ("On
exit, a block releases…"); §18.4 `mem.temporary` and §9.7 `decl.scope.statement` lose their defer exceptions;
§18.4 `mem.scope-exit` gains "after the block's deferred blocks have run (§14.13)", and its note changes — user
code (an outer block's deferred blocks) now runs between blocks' releases, only within one block is the order
unobservable; §13 `expr.composite.lifetime` loses its defer case; §10.3 `func.call.value`; §10b
`func.closure.allocation`; §14.15 `stmt.absences`; the §21 implementation-defined table row; `00-index.md`;
`docs/guide.md` and `docs/overview.md`; the examples repo's `files/README.md`; `explorations/differences-with-go.md`
(still says "No defer") — the Go-difference notes live there, not in Annex D.  `rule-ids.txt` is generated
(`docs/scripts/extract-rule-ids.py`); binate's vendored copy `scripts/spec-coverage/rule-ids.txt` must be
re-vendored (it has drifted).  Retire `stmt.defer.call` / `stmt.defer.no-loop`, add `stmt.defer.block` and
`stmt.for.scope`.  `explorations/claude-notes.md` and `proposal-defer.md` (marked superseded) follow.

## Implementation

Prerequisite: the `for` init-variable scoping bug (todo "A C-style `for` init variable stays visible after the
loop in IR-gen") — the `for` statement needs its own IR-gen scope before a scope stack can be built on it.

- Grammar / parser / AST: `defer` + Block, the block stored in `Stmt.Body` (so the generic walkers —
  `walkStmtRefs`, `luStmt`, func_value_escape, readonly_handle_type_arg — descend into it for free, and the AST
  field-pin test is untouched); ast clone, bnfmt printing.  The call form's hint lands in transition step 4.
- Checker: check the block in place (normal scoping); save and clear InLoop / InSwitch on entering it (as
  `check_func_lit.bn` does) and add a flag rejecting `return` (`stmt.defer.block`); drop `stmt.defer.call` and
  `stmt.defer.no-loop`; `collectVarDepsStmt` walks the deferred block (the init-order todo).
- bnlint walkers that dispatch on statement kind must descend into the block: `lint.bn` lintStmt,
  `iface_borrow_escape.bn` escWalkStmt (escapes would be missed), `borrowable_char_param.bn` pcWalkStmt (could
  wrongly suggest `readonly`).
- IR-gen, deleting the eager machinery — the defer pre-pass and its entry-depth operand / borrow / retain /
  backing / literal slots, the armed flags, the operand roles and shapes, the deferred-call stack pre-check,
  SnapshotBorrows / DeferBorrowSlots, genCompositeElem's defer arm, genBlockEx's skipFalloffCleanup and
  genFunc's hasDefers epilogue (about 1,500 lines in `gen_defer*.bn` plus ~27 hooks) — and adding:
  - A scope stack: one entry per IR-gen scope (genBlockEx, genCaseBody, genTypeSwitchCaseBody, genForIn's
    per-iteration body, the `for` statement's own scope, and the hidden scopes — for-in operand / header
    temporaries, range variables, the type-switch scrutinee), each with its ctx.Vars start and its pending
    deferred blocks.  A defer statement appends (its block, the ctx.Vars length then) to the innermost entry and
    emits nothing.  `break` / `continue` record the target's stack depth instead of BreakVarLen /
    ContinueVarLen (which release flat today); every exit — fall-through end, `break`, `continue`, `return` —
    walks the stack innermost first, emitting each entry's deferred blocks (last first) and then that entry's
    releases, on a copy, popping as it goes.
  - Name lookup apart from the live set: while a deferred block is generated, ctx.Vars is
    `Vars[:L_defer]` plus `Vars[L_defer:entryEnd]` re-entered under unspellable hidden names (as
    `.typeswitch.scrutinee` is) — invisible to lookup, still live for the fault pads (`emitPadCleanup` releases
    all of ctx.Vars) — with the already-exited inner entries dropped; restored afterwards.  (Plain truncation
    would leak the live later locals on a VM fault inside deferred code.)
  - Return edges with pending deferred blocks: acquire (or move) each result; store it in a fresh frame slot
    registered as a hidden live entry (so a fault pad in the deferred code releases it); release the return
    statement's remaining temporaries with no OP_SP_RESTORE and clear the statement state (Temps, StmtGrewSP,
    AddressedLits, CurBlock); run the deferred blocks; reload the results, drop the hidden entries, EmitReturn.
    This reverses gen_return.bn's current order (deferred calls before the acquire), which was safe only
    because eager deferred calls could not see the return operands — and it keeps return values that live on the
    VM stack (aggregate call results, OP_IFACE_VALUE, literal copies) from being clobbered by the deferred
    code's own stack growth and SP restores.
  - A deferred block that ends in a terminator (`panic`) ends its exit edge: the remaining deferred blocks,
    releases and the jump / return are skipped.  BreakTo / ContinueTo are nil while deferred code is generated,
    so an escaping jump fails loud.
  - Closure sites: a deferred block is generated once per exit edge, so the lifted function and the frame
    closure record of each func-literal / method-value site are memoized per source site within a function —
    `func.closure.allocation` requires one record per site per frame, re-snapshotted per evaluation (a loop body
    whose deferred `saved = func() int { return i }` runs via `continue` in iteration 0 and by falling through
    in iteration 1 must give `saved()` = 1).  Generating the same AST more than once is otherwise safe (generic
    bodies already do; IR-gen mutates no shared AST node).
- VM / native / LLVM backends: nothing new (deferred code is ordinary code).
- BUILDER: no `defer` anywhere in non-test code; after this lands, cmd/bnc's cone must not use `defer` until the
  pinned BUILDER parses the block form (a constraint, not a reason to cut one).

## Transition (the pinned CHECK_TOOLS bnlint / bnfmt parse `pkg/`, `cmd/`, `impls/`, `ifaces/`)

`scripts/hygiene/lint.sh` (bnlint `--tests`; `pkg/`, `cmd/`, every `impls` / `ifaces` package) and
`bnfmt-format.sh` (`pkg cmd impls ifaces perf examples runtime`) use the CHECK_TOOLS_VERSION tools
(`bnc-0.0.17-pre1`), whose parser rejects `defer { … }` ("expected expression").  `conformance/` is neither
formatted nor linted.  The tree has 259 defers, all in `_test.bn` files at function top level, none reassigning
its operand later (pkg 225, cmd 4, impls 30; 222 are `defer os.RemoveAll(dir)`), so they mean the same under the
eager and the new semantics; there are none in non-test code, e2e, perf, runtime or examples.

Running the two forms side by side with different semantics is hazardous (plan review): at a `return` the old
calls run before the result acquire and the new blocks after it, and an old-form defer inside a new-form block
inside a loop passes the no-loop check.  So:

1. The call form becomes sugar for `defer { <call> }` with the new semantics; the eager machinery is deleted;
   the 81 conformance files move to the block form (expected outputs updated where they pinned eager evaluation
   or function-scoped timing — 9 have a defer in a nested block, 7 in an `if`, `1248_defer_in_loop` in a loop;
   `.error` files 1248 / 1249 pin the old messages); the spec lands with a note that the call form is accepted
   until step 4.
2. A CHECK_TOOLS pre-release whose bnlint / bnfmt parse `defer { … }`, and the CHECK_TOOLS_VERSION bump.
3. Migrate the 259 unit-test defers to the block form.
4. Reject the call form (syntax error + hint).

## Tests

New conformance tests (spec/14-statements): each exit kind (fall-through, `break`, `continue`, `return` through
nested blocks, a switch case, a for-in loop, a type switch), LIFO within a block and innermost-first across
blocks with the releases between, per-iteration defers in `for` (including one writing the loop counter), live
reads and writes (a write visible after the inner block), a shadow declared later in the same block and the
for-init shadow, a range variable and a type-switch binder used in deferred blocks, a return value the deferred
block cannot change, a VM `return` of a stack-resident aggregate with deferred code that grows the stack, a
recoverable VM fault inside a deferred block with leak accounting, a deferred block ending in `panic`, a nested
defer, the closure-site / exit-edge case above, unexecuted defers not run, `no-abort` (a panic runs none), an
IIFE package-variable initializer whose deferred code reads a later variable (init order), leak checks; error
tests for `stmt.defer.block` (return / escaping break / continue) and the call-form hint.  The retention tests
176–183 keep their programs; expected outputs change where they reassign a local after the defer (now read
live).  `scripts/hygiene/test-coverage.whitelist` loses its `gen_defer*` entries.

## Decided points

- The CHECK_TOOLS pre-release in step 2: "yes, a CHECK_TOOLS pre-release is fine" (user, 2026-10-12).
- A `defer` inside a deferred block is allowed: "yes, a defer inside a defer is fine (we can't ban it from
  functions called by the contents of the defer, so not allowing it is just silly)" (user, 2026-10-12).

## Open points for the user (2026-10-12)

- The transition order above: the old call spelling takes the new semantics in step 1 (the alternative —
  both forms side by side with different semantics — is hazardous, see Transition).
- The REPL: immediate mode already runs each entry as a synthetic function, so `{ defer { … } }` typed at the
  prompt is well-defined.  Proposed: keep rejecting a `defer` at the prompt's top level (it would run at once,
  at the end of that entry) and allow it in nested blocks.
