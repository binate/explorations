# Plan: `defer { … }` — a block run at scope exit, reading live variables

Status: ACCEPTED 2026-10-12 (work-4); plan review pending.  Supersedes the eager, function-scoped
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

Knock-on spec edits: §18.4 `mem.temporary` and §9.7 `decl.scope.statement` lose their defer exceptions;
§18.4 `mem.scope-exit` gains "after the block's deferred blocks have run (§14.13)" and its note's "deferred
calls are sequenced before the function-exit releases" becomes per block; §13 `expr.composite.lifetime` loses
its defer case; §14.12's `break` / `continue` paragraph drops the `stmt.defer.no-loop` cross-reference; §10.3's
pack note is correct again as written; Annex A regenerated (`docs/scripts/gen-annex-a.py`); `rule-ids.txt`:
`stmt.defer.call` and `stmt.defer.no-loop` retired, `stmt.defer.block` added; Go-difference notes (Annex D)
updated.  `explorations/claude-notes.md` and `proposal-defer.md` (marked superseded) follow.

## Implementation

- Grammar / parser / AST: `defer` + Block; the AST's defer statement holds the block.  Hint for the call form.
  ast clone, bnfmt printing, bnlint's AST walkers.
- Checker: check the block in place (normal scoping); enforce `stmt.defer.block`; drop `stmt.defer.call` and
  `stmt.defer.no-loop`.
- IR-gen: delete the eager machinery — the defer pre-pass and its entry-depth operand / borrow / retain /
  backing / literal slots, the armed flags, the operand roles and shapes, the deferred-call stack pre-check,
  SnapshotBorrows / DeferBorrowSlots, genCompositeElem's defer arm (about 1,500 lines in `gen_defer*.bn` plus
  ~27 hooks elsewhere).  Add per-block pending-defer lists: a defer statement appends (block AST + the
  ctx.Vars length at that point) to the current block's list and emits nothing; each block-exit point — the
  fall-through end, `break`, `continue`, `return` — emits, for each block it leaves (innermost first), that
  block's pending deferred blocks in reverse order, then that block's releases.  A deferred block is generated
  with ctx.Vars truncated to its recorded length (so a later inner shadow is not seen) and restored afterwards.
- Code size: a deferred block is generated once per exit edge of its block.  Generating one AST more than
  once must be safe: audit IR-gen state keyed by AST identity (`same`) — func-literal lifting (FuncLitCounter
  gives each copy its own lifted function: correct, duplicated), AddressedLits / deferLitSlot (deleted),
  checker stamps IR-gen mutates.  A shared per-block cleanup chain can bound duplication later; not needed
  first.
- VM / native / LLVM backends: nothing new (deferred code is ordinary code).  Fault pads: deferred blocks never
  run on a fault (`no-abort`), pads release as now.
- BUILDER: cmd/bnc's non-test cone has no `defer` today; after this lands it must not use `defer` until the
  pinned BUILDER parses the block form (a constraint, not a reason to cut one).

## Transition (the pinned CHECK_TOOLS bnlint / bnfmt parse the tree's `pkg/` and `cmd/` files)

`scripts/hygiene/lint.sh` (bnlint `--tests`) and `bnfmt-format.sh` use the CHECK_TOOLS_VERSION tools
(`bnc-0.0.17-pre1`), whose parser rejects `defer { … }`.  `conformance/` is not bnfmt-checked or linted.  The
tree's 229 defers are all in unit tests under `pkg/` / `cmd/`, all at function top level (222 are `defer
os.RemoveAll(dir)`), so their meaning is the same under both forms; none is in non-test code or `examples/`.

1. Land the block form with its new semantics beside the old call form (the parser accepts both; the old form
   keeps its current implementation), with the new conformance tests and the spec text.  Nothing in `pkg/` /
   `cmd/` uses the block form yet, so hygiene stays green.
2. A CHECK_TOOLS pre-release whose bnlint / bnfmt parse `defer { … }` (the user's call — a release cut), and
   the CHECK_TOOLS_VERSION bump.
3. Migrate the 229 unit-test defers and the 81 conformance files to the block form (expected outputs updated
   where they pinned eager evaluation or function-scoped timing — 9 files have a defer in a nested block;
   `1248_defer_in_loop` becomes a per-iteration test).
4. Remove the call form (syntax error + hint) and delete the eager machinery.

## Tests

New conformance tests (spec/14-statements): each exit kind (fall-through, `break`, `continue`, `return` through
nested blocks, switch case), LIFO within a block and innermost-first across blocks with the releases between,
per-iteration defers in `for`, live reads and writes (a write visible after the inner block), a return value
the deferred block cannot change, a raw borrow that is live at exit, a deferred block with a nested defer and a
function literal emitted at several exits, unexecuted defers not run, `no-abort` (a panic runs none), leak
checks; error tests for `stmt.defer.block` (return / escaping break / continue) and the call-form hint.  The
retention tests 176–183 keep their programs; their expected outputs change where they reassign a local after
the defer (now read live).

## Decided points

- The CHECK_TOOLS pre-release in step 2: "yes, a CHECK_TOOLS pre-release is fine" (user, 2026-10-12).
- A `defer` inside a deferred block is allowed: "yes, a defer inside a defer is fine (we can't ban it from
  functions called by the contents of the defer, so not allowing it is just silly)" (user, 2026-10-12).
