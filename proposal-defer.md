# Proposal: `defer` — deferred calls at scope exit (`proposal-defer`)

Status: **PROPOSAL — adversarially reviewed (two lenses: memory-model/semantics
and spec-consistency; both SOUND-WITH-MUST-FIXES, all fixes applied below),
awaiting ratification.** A language addition (new reserved keyword + statement).
Spec design only; implementation planned separately.

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
call when the **enclosing block** exits, **LIFO**, **before** the exited
blocks' managed locals are released. It is **block-scoped** (not Go's
function-scoped), runs on every *normal* exit (fall-through, `return`,
`break`, `continue`) and **never** on a panic/trap or runtime exit — including
a panic raised *inside* a deferred call, which abandons the remaining pending
calls (Binate aborts do not unwind; there is no `recover`). Deferred code
cannot alter a returned managed value (Binate has no named results, and return
operands are retained first).

## 3. Proposed spec text

Grammar (`binate.ebnf` **and** §14.1's inline `Statement` production —
`stmt.kinds`): `Statement` gains a `DeferStmt` alternative; `stmt.simple`'s
non-simple enumeration gains "defer statements" (so `defer` cannot appear in a
`for` clause), and the new section opens with its grammar per the
per-construct rubric:

```
Statement  = … | DeferStmt | … ;
DeferStmt  = "defer" Expression ;
```

`defer` becomes the **25th reserved keyword** (§5.4: the list re-flows and the
count "24" becomes 25; the `binate.ebnf` reserved-keyword comment block gains
it too, and Annex A / `rule-ids.txt` regenerate). **No ASI change**: `defer` is
not an insertion-trigger keyword, so a newline after `defer` continues the
statement (deliberate; Go behaves the same). No identifier in the tree spells
`defer`, so the reservation breaks nothing.

New section — **§14.13 "Defer statements"**, inserted after §14.12
(break/continue); Terminating statements renumber to §14.14 and the deliberate
absences to §14.15, with a cross-reference sweep (~15 `§14.13`/`§14.14`
citations across ~9 spec files, enumerated by grep at landing time). Rules
(each declared at column 0 in the spec file so the generated rule registry
picks them up; blockquotes here are presentation only). For all of these rules,
a **block** is every scope that performs its own scope-exit release: a braced
block, a `switch`/type-switch **case body**, and a loop **body** (including a
`for … in` iteration scope).

> `stmt.defer` — A **defer statement** `defer c` schedules the call `c` to run
> when the **innermost enclosing block** exits. The call's **callee** — the
> function reference, the function value, or a method's receiver — **and every
> argument are evaluated when the defer statement executes**; the **call
> executes at block exit**. The evaluated values are retained with the
> **enclosing block's lifetime** (released with the block, §18.4
> `mem.scope-exit` — *not* as statement temporaries; see the §18.4 amendment
> below), so they stay live until the deferred call runs; the call consumes
> them under the ordinary call contract (§18.5). Where an operand undergoes a
> **managed→raw** conversion at the defer site (§8.4), the **pre-conversion
> managed value** is what is retained, and the borrow is delivered at call
> time — preserving the argument-borrow liveness guarantee. A raw operand
> value *not* backed by a retained managed value is an ordinary borrow whose
> referent's liveness at call time is the programmer's responsibility (§18.7
> `mem.raw-uaf`). The call's results, if any, are **discarded**; a discarded
> **managed** result is released **immediately after the call returns**,
> before the next pending deferred call runs. Each **execution** of a defer
> statement schedules one call: a defer inside a loop body schedules — and,
> the body block exiting each iteration, runs — one call **per iteration**. A
> defer statement requires an enclosing block; in the REPL's **immediate
> mode**, a `defer` entered with no enclosing block is rejected.
>
> `stmt.defer.call` _(Constraint)_ — The operand shall be a **call**: a
> function call, a method call, or a function-value call (including a call of
> the predeclared `panic`). A non-call expression, or a builtin-operation
> keyword form (`make(…)`, `cast(…)`, …, §15.1 — special call shapes, not
> calls), is rejected.
>
> `stmt.defer.exit` — Deferred calls run on every **normal** exit of their
> block: falling off its end, and any `return`, `break`, or `continue` that
> exits it. An exit is processed in **two phases**: first **all** pending
> deferred calls of **every** block being exited run, in **reverse order of
> their scheduling** (equivalently: innermost block's calls first, LIFO within
> each block); **then** the exited blocks' managed locals are released (§18.4
> `mem.scope-exit`). A deferred call therefore runs while **all** locals of
> the blocks being exited are still live, and **no user code runs between the
> releases** (preserving §18.4/§21.5's release-order unobservability).
>
> `stmt.defer.return` — On a `return`, the return operands are evaluated and
> each **managed** result **acquires its owning reference first** (§18.5
> `mem.return`); the exit then proceeds per `stmt.defer.exit` (every pending
> deferred call of the function's open blocks, then the releases), and the
> retained results transfer to the caller. Deferred code observes the
> post-evaluation state but **cannot change a returned managed value**
> (results are unnamed and already retained). _Note:_ a returned **raw** value
> that borrows state a pending deferred call releases or mutates dangles
> exactly as if that cleanup call were written textually before the `return`
> (§18.7 `mem.raw-uaf`); returning managed values is the safe pattern.
>
> `stmt.defer.no-abort` — Deferred calls run on **normal control-flow exits
> only**. A defined non-recoverable panic (§17.5), a trap, or a runtime
> **exit** primitive terminates the program **without running deferred calls**
> — and one occurring **inside a deferred call** terminates the program
> immediately: the remaining pending deferred calls, and the pending releases
> of the exit in progress, do **not** run. (Deliberate divergence from Go,
> which runs the remaining deferred functions while panicking and offers
> `recover`; a Binate panic is the program's last action — §17.5, §14.15.)

Amendments the statement forces (the ratification touch-list):

- **§18.4 `mem.temporary` carve-out (normative)**: a defer statement's
  evaluated callee/receiver/argument values are **not** statement temporaries
  — they are retained with the enclosing **block's** lifetime and released
  after the deferred call runs (or with the block, per `stmt.defer.exit`); the
  defer statement's *other* temporaries still release at the end of the
  statement. (Without this, §18.4/§9.7 as written release the operands at the
  semicolon — an internal contradiction.)
- **§18.4 release-order note + §21.5 unspecified-behavior row**: requalified —
  deferred calls are **sequenced before** the releases (`stmt.defer.exit`);
  *among the releases themselves* order remains unobservable, since no user
  code runs between them.
- **§14.1 `stmt.kinds`** inline production + **`stmt.simple`** enumeration;
  the Ch.14 and §14.8+ intro section maps; **section renumbering** as above.
- **§14.15 `stmt.absences`**: the "No `defer`" bullet is deleted; the
  `panic`/`recover` bullet gains "deferred calls do not run on a panic"
  (`stmt.defer.no-abort`). **§1's** absences list drops `defer`.
- **§14.14 (terminating statements)**: unchanged in content (a defer statement
  is not a terminator).
- **Status plumbing**: the new rules land **Draft — ratified, not yet
  implemented** (house style); the 14b chapter header's maturity line, the
  §5.4 chapter header (a Draft keyword in a Stable chapter), the 00-index
  Ch.14.8 row, and Annex C note it.
- **Guide/overview** (six sites): guide.md's Go-diff table row, §Memory
  bullet, control-flow absences bullet, and §15 absences row; overview.md's
  Memory bullet and control-flow absences line — "no `defer`" becomes the
  feature, and "explicit call on every exit path" becomes "or `defer`".
- Regenerate Annex A (`gen-annex-a.py`) and `rule-ids.txt`
  (`extract-rule-ids.py`).

## 4. Rationale for the two deliberate divergences from Go

**Block-scoped, not function-scoped.** Binate's entire cleanup model is already
block-scoped: `mem.scope-exit` releases a block's managed locals when *the
block* exits, and every exit edge (`return`/`break`/`continue`) already
performs the equivalent release. `defer` rides the same discipline — one
uniform "block exit: defers, then releases" story — instead of importing a
second, function-scoped lifetime the language otherwise doesn't have. It also
fixes Go's best-known defer wart: a defer in a loop body runs **each
iteration** (per-iteration cleanup is *expressible*), not accumulated to
function exit. A defer written at the top of a function body behaves exactly
like Go's **on every non-panic exit** (the body is a block; on a panic Binate
runs no defers — §5), so the common idiom transfers; only nested-block defers
differ — in the direction users generally want. (Zig precedent.)

**Eager operand evaluation.** The callee, receiver, and arguments are
snapshotted at the defer statement — the same snapshot philosophy as Binate's
closures (`func.closure.capture`: capture is **by value**, at evaluation time).
One mental model, no late-binding surprises, and the refcount story is
mechanical: retained with the block, consumed by the call, released after it.
Go does the same for arguments (famously surprising *there* only because Go's
closures capture by reference — a mismatch Binate doesn't have). Late
observation, where wanted, goes through a pointer, exactly as with closures.

## 5. What this deliberately does not include

- **No `recover`, no panic interaction** — Binate panics abort; defers run on
  normal exits only, and a panic *inside* a deferred call abandons the rest
  (`stmt.defer.no-abort`). This deletes the entire hardest part of Go's defer
  chapter (defer/panic/recover interplay, defers during unwinding, re-panics).
- **No `errdefer`** (Zig) — the language cannot see an "error path": errors are
  ordinary values, so there is no channel to condition on. Write the
  conditional cleanup explicitly.
- **No block-operand form** (`defer { … }`, Zig-style) for now — it implies
  late-binding reads of free variables, cutting against the snapshot model;
  `defer f(x)` with a small named function or function value covers it. Can be
  revisited later without breaking the call form.
- **No change to returned values from deferred code** — no named results
  exist; this is a feature (Go's mutate-the-named-result-in-defer idiom is a
  notorious source of subtle bugs and exists mostly to serve `recover`).

## 6. Implementation notes (informative — NOT spec content)

The IR already funnels every scope exit through two emitters with a watermark
discipline (`emitDecForScopeVars` at the **8** block/break/continue/case/
type-switch sites, `emitDecForManagedLocals` at return + the VM-only fault
pads). A defer registry with the same `savedVarLen`/`BreakVarLen`-style
watermarks slots in one-for-one: at each exit edge, emit the pending deferred
calls **down to the edge's watermark, in reverse registration order, before**
the existing release emission (this is exactly `stmt.defer.exit`'s two-phase
order); each emitted call is followed by its own discarded-result/temp
cleanup. Return runs the whole registry (reverse), then the existing
whole-function release. **Operand retention design (pinned, per review):** the
registry holds only the *call plan*; the retained operand values are
materialized as **anonymous scope slots alongside the block's named locals**
(ctx.Vars-style), so every existing release sweep — block exit,
break/continue watermark, return, and the VM fault pads — releases them
unchanged, and no new leak path exists. The **VM fault pads emit no deferred
calls** (`stmt.defer.no-abort`; the pads' RefDec-only cleanup, now covering
the operand slots too, keeps memory balanced on a VM-isolated fault — the
VM's fault-isolation facility is internal machinery the core spec does not
describe, and both modes agree that a fault runs no defers). One shared
IR-gen serves both modes, so compiled/VM agreement is by construction.
Keyword addition is mechanical (token.bni enum + one TypeName case; the lexer
is table-driven; zero identifier collisions repo-wide; a checker comment
already anticipates the feature). BUILDER note: *adding* the keyword compiles
under the current BUILDER; *using* `defer` inside cmd/bnc's BUILDER-compiled
tree must wait for a BUILDER cut that carries it. The for-in per-iteration
value var releases on the loop's own post/break edges *after* the body block's
defers have run on the body's exit edges — the existing edge structure already
sequences this correctly.

## 7. Open questions for ratification

1. **Block-scoped (recommended) vs Go's function-scoped.** The headline
   divergence — §4's case. Function-scoped would match Go muscle memory
   exactly but fights the language's block-scoped cleanup model, needs new
   machinery (there is no function epilogue; every return site emits its own
   cleanup), and re-imports the loop wart.
2. **Operand breadth.** Call-only (recommended, `stmt.defer.call`) vs also
   allowing a block form later. Sub-point: `defer panic("…")` is allowed as
   recommended (an ordinary call of a predeclared function; useful for
   invariant enforcement) — exclude it if that reads too clever.
3. **Faults under the VM's internal isolation facility.** The core spec says
   panics terminate (§17.5) and grants no isolation latitude; the VM's fault
   pads are extra-spec machinery. Recommended: no deferred calls on an
   isolated fault (both modes agree; the alternative breaks cross-mode
   agreement, since compiled mode aborts without them). If fault isolation is
   ever specified, its defer story is decided then.
4. **Reserve the keyword immediately on ratification** (recommended — zero
   collisions today; reserving early keeps new code from claiming it) vs
   reserving only when the implementation lands.

## 8. Sources

Grounded in: §14/§14b statement grammar and absences, §5.4 keyword list + ASI
rules, §18.4/§18.5 scope-exit/return ownership text, §17.4–17.5
termination/panic rules and the §21.5 unspecified-behavior table; the original
rationale (`differences-with-go.md`, `claude-discussion-detailed-notes.md`
§12); the VM.Shutdown leak entry and the lifecycle-hooks PROPOSED item; the
implementation recon (per-edge cleanup emitters and watermarks in
`pkg/binate/ir`, keyword table in `pkg/binate/token`, zero `defer` identifier
collisions); and two adversarial reviews (memory-model/semantics;
spec-consistency/completeness) whose findings — the two-phase ordering fix,
the `mem.temporary` carve-out, the panic-inside-a-defer rule, the block-set
definition, the raw-operand and discarded-result precision, the renumbering
plan, and the status/REPL/amendment-list completions — are folded in
throughout.
