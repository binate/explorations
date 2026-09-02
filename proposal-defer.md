# Proposal: `defer` — deferred calls at scope exit (`proposal-defer`)

Status: **PROPOSAL — under review, not yet ratified.** A language addition (new
reserved keyword + statement). Spec design only; implementation planned
separately.

## 1. Why now — the recorded rationale doesn't hold

The spec's deliberate-absence bullet says scope-exit release "covers the
memory-cleanup role" and non-memory resources use "an explicit call on every
exit path" (§14.14); the original design note is one line — "Destructors (dtors
run at scope exit) handle the common cleanup case; RAII-style"
(`differences-with-go.md`). But Binate has **no user-defined destructors**:
compiler-generated destructors release managed references *only*. RAII covers
memory and nothing else — a file close, a lock release, or any teardown that is
*user code* must be written on **every** exit path, and errors-as-values
multiplies exit paths. (The hidden-control-flow / unwinding objections in the
design notes were aimed at exceptions and `panic`/`recover`; a Go-style `defer`
has neither.) Go — a *garbage-collected* language — still has `defer`, because
GC, like refcounting, only ever covered memory.

Live wound: the VM.Shutdown leak (todo, 2026-09-02) — a `@VM` dropped without
calling `Shutdown()` leaks managed-global content, "there is NO user-destructor
hook" to run it, a custom FreeFn was **rejected** as a destructor substitute
(done log), and the fix is a ~40-file manual sweep adding explicit `Shutdown()`
calls before every drop. `defer vm.Shutdown()` is the missing sentence. (The
"Debug lifecycle hooks" PROPOSED item overlaps this motivation; `defer` covers
the *cleanup* half with no annotation machinery, leaving that proposal to its
debug-assertion half.)

## 2. The design in one paragraph

`defer <call>` evaluates the call's callee and arguments **now** and runs the
call when the **enclosing block** exits, **LIFO**, **before** the block's
managed locals are released. It is **block-scoped** (not Go's function-scoped),
runs on every *normal* exit (fall-through, `return`, `break`, `continue`) and
**never** on a panic/trap or runtime exit (Binate aborts do not unwind; there
is no `recover`). Deferred code cannot alter a return value (Binate has no
named results, and return operands are retained first).

## 3. Proposed spec text

Grammar (`binate.ebnf`; `Statement` gains an alternative; **not** a simple
statement, so it cannot appear in a `for` clause):

```
Statement  = … | DeferStmt | … ;
DeferStmt  = "defer" Expression ;
```

`defer` becomes the **25th reserved keyword** (§5.4; the list and its count
update; no ASI after `defer`, like other non-terminal keywords — a newline
after `defer` continues the statement). No identifier in the tree spells
`defer`, so the reservation breaks nothing.

New rules (a new §14 section, "Defer statements", after §14.12; rule-IDs
`stmt.defer*`):

> `stmt.defer` — A **defer statement** `defer c` schedules the call `c` to run
> when the **innermost enclosing block** exits. The call's **callee** — the
> function reference, the function value, or a method's receiver — **and every
> argument are evaluated when the defer statement executes**; the **call
> executes at block exit**. The evaluated values are retained as if bound to
> unnamed locals of the enclosing block (§18.4): they stay live until the
> deferred call runs and are released after it, under the ordinary call
> contract (§18.5). The call's results, if any, are **discarded** (as in an
> expression statement, §14.3). Each **execution** of a defer statement
> schedules one call: a defer inside a loop body schedules — and, the body
> block exiting each iteration, runs — one call **per iteration**.
>
> `stmt.defer.call` _(Constraint)_ — The operand shall be a **call**: a
> function call, a method call, or a function-value call (including a call of
> the predeclared `panic`). A non-call expression, or a builtin-operation
> keyword form (`make(…)`, `cast(…)`, …, §15.1 — special call shapes, not
> calls), is rejected.
>
> `stmt.defer.exit` — Deferred calls run on every **normal** exit of their
> block: falling off the end, and any `return`, `break`, or `continue` that
> exits it. An exit that leaves **several** blocks at once (a `return`; a
> `break`/`continue` inside nested blocks) runs the deferred calls of **every
> block being exited, innermost block first**. Within one block, deferred
> calls run in **reverse order of their scheduling (LIFO)**. A block's deferred
> calls run **before** that block's managed locals are released (§18.4
> `mem.scope-exit`) — a deferred call may therefore still use the block's
> locals — and after the *inner* blocks being exited have completed their own
> deferred calls and releases.
>
> `stmt.defer.return` — On a `return`, the return operands are evaluated and
> each managed result **acquires its owning reference first** (§18.5
> `mem.return`); every pending deferred call of the exited blocks then runs;
> the function's locals are then released and the retained results transfer to
> the caller. Deferred code observes the post-evaluation state but **cannot
> change the returned values** (results are unnamed and already retained).
>
> `stmt.defer.no-abort` — Deferred calls run on **normal control-flow exits
> only**. A defined non-recoverable panic (§17.5), a trap, or a runtime **exit**
> primitive terminates the program **without running deferred calls** (a panic
> is the program's last action — there is no unwinding and no `recover`;
> §14.14). _Note:_ a host environment that isolates a fault (§17.5's
> interpreter latitude) also does not run the faulting scope's deferred calls —
> the two modes agree (§19).

Amendments the statement forces:

- **§18.4 release-order note + §21.5 unspecified-behavior row**: their premise
  ("scope-exit release order is not observable") survives only as qualified —
  deferred calls are **sequenced before** a block's releases (`stmt.defer.exit`);
  *among the releases themselves* order remains unobservable, since no user
  code runs between them (destructors are compiler-generated).
- **§14.14 `stmt.absences`**: the "No `defer`" bullet is deleted; the
  `panic`/`recover` bullet gains "deferred calls do not run on a panic"
  (`stmt.defer.no-abort`). §1's absences list drops `defer`.
- **§14.13** terminating-statement analysis: unchanged (a defer statement is
  not a terminator).
- Guide/overview: the "no `defer`" rows and the memory-section claims update on
  ratification (the "explicit call on every exit path" idiom becomes "or
  `defer`").

## 4. Rationale for the two deliberate divergences from Go

**Block-scoped, not function-scoped.** Binate's entire cleanup model is already
block-scoped: `mem.scope-exit` releases a block's managed locals when *the
block* exits, and every exit edge (`return`/`break`/`continue`) already
performs the equivalent release. `defer` rides the same discipline — one
uniform "block exit = defers, then releases" story — instead of importing a
second, function-scoped lifetime that the language otherwise doesn't have.
It also fixes Go's best-known defer wart: a defer in a loop body runs **each
iteration** (per-iteration cleanup is *expressible*), not accumulated to
function exit. A defer written at the top of a function body behaves exactly
like Go's (the body is a block), so the common idiom transfers unchanged; only
nested-block defers differ — in the direction users generally want. (Zig
precedent.)

**Eager operand evaluation.** The callee, receiver, and arguments are
snapshotted at the defer statement — the same snapshot philosophy as Binate's
closures (`func.closure.capture`: capture is **by value**, at evaluation time).
One mental model, no late-binding surprises, and the refcount story is
mechanical: retained like unnamed locals, consumed by the call, released after
it. Go does the same for arguments (famously surprising *there* only because
Go's closures capture by reference — a mismatch Binate doesn't have). Late
observation, where wanted, goes through a pointer, exactly as with closures.

## 5. What this deliberately does not include

- **No `recover`, no panic interaction** — Binate panics abort; defers run on
  normal exits only (`stmt.defer.no-abort`). This deletes the entire hardest
  part of Go's defer chapter (defer/panic/recover interplay, defers during
  unwinding, re-panics).
- **No `errdefer`** (Zig) — the language cannot see an "error path": errors are
  ordinary values (§14.14), so there is no channel to condition on. Write the
  conditional cleanup explicitly.
- **No block-operand form** (`defer { … }`, Zig-style) for now — it implies
  late-binding reads of free variables, cutting against the snapshot model;
  `defer f(x)` with a small named function or function value covers it. Can be
  revisited later without breaking the call form.
- **No change to the return value from deferred code** — no named results
  exist; this is a feature (Go's mutate-the-named-result-in-defer idiom is a
  notorious source of subtle bugs and exists mostly to serve `recover`).

## 6. Implementation notes (informative — NOT spec content)

The IR already funnels every scope exit through two emitters with a watermark
discipline (`emitDecForScopeVars` at the 7 block/break/continue/case sites,
`emitDecForManagedLocals` at return + the VM-only fault pads). A defer registry
parallel to `ctx.Vars`, with the same `savedVarLen`/`BreakVarLen`-style
watermarks, slots in one-for-one: at each exit edge, emit the pending deferred
calls **down to the edge's watermark, in reverse registration order, before**
the existing release emission. Return runs the whole registry (reverse), then
the existing whole-function release. The VM fault pads emit **no** deferred
calls (`stmt.defer.no-abort`; the pads' RefDec-only cleanup is unchanged, so
memory still balances on a VM-isolated fault). One shared IR-gen serves both
modes, so compiled/VM agreement is by construction. Keyword addition is
mechanical (token.bni enum + one TypeName case; the lexer is table-driven; zero
identifier collisions repo-wide). BUILDER note: *adding* the keyword compiles
under the current BUILDER; *using* `defer` inside cmd/bnc's BUILDER-compiled
tree must wait for a BUILDER cut that carries it. The eagerly-evaluated
operands' retention needs care at the `mem.temporary` boundary: they are
scope-lifetime unnamed locals, **not** statement temporaries — the statement's
other temporaries still release at the semicolon.

## 7. Open questions for ratification

1. **Block-scoped (recommended) vs Go's function-scoped.** The headline
   divergence — §4's case. Function-scoped would match Go muscle memory
   exactly but fights the language's block-scoped cleanup model, needs new
   machinery (there is no function epilogue), and re-imports the loop wart.
2. **Operand breadth.** Call-only (recommended, `stmt.defer.call`) vs also
   allowing a block form later. Sub-point: `defer panic("…")` is allowed as
   recommended (an ordinary call of a predeclared function; useful for
   invariant enforcement) — exclude it if that reads too clever.
3. **Interaction with the VM's recoverable-fault isolation.** Recommended: no
   deferred calls on a fault, both modes (the note in `stmt.defer.no-abort`) —
   the alternative (VM runs defers during fault cleanup) breaks cross-mode
   agreement, since compiled mode aborts without them.
4. **Reserve the keyword immediately on ratification** (recommended — zero
   collisions today; reserving early keeps new code from claiming it) vs
   reserving only when the implementation lands.

## 8. Sources

Grounded in: §14/§14b statement grammar and absences, §5.4 keyword list + ASI
rules, §18.4/§18.5 scope-exit/return ownership text, §17.4–17.5
termination/panic rules and the §21.5 unspecified-behavior table (all quoted in
the recon record); the original rationale (`differences-with-go.md`,
`claude-discussion-detailed-notes.md` §12); the VM.Shutdown leak entry and the
lifecycle-hooks PROPOSED item; and the implementation recon (per-edge cleanup
emitters and watermarks in `pkg/binate/ir`, keyword table in
`pkg/binate/token`, zero `defer` identifier collisions).
