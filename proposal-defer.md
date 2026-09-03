# Proposal: `defer` — deferred calls at function exit (`proposal-defer`)

Status: **RATIFIED (owner, 2026-09-02) — spec landed as docs commit `03dd078`
(new §14.13, Draft — ratified, not yet implemented).** Scoping is option A
(function-scoped with the loop restriction), and the three remaining questions
were ratified per the recommendations ("recs are fine"): call-only operand
including `defer panic(…)`; no deferred calls on a VM-isolated fault; the
`defer` keyword reserved immediately. Earlier draft was block-scoped; two
adversarial reviews (memory-model/semantics; spec-consistency) ran against it
and their still-applicable findings are folded in; the option-A rework passed
its own delta review (SOUND-WITH-MUST-FIXES — the borrow-not-consume call
contract and the operand-release timing pinned; both applied).
Spec design only; implementation tracked in `claude-todo.md` (Language-feature
proposals → defer).

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
calls before every drop. `defer vm.Shutdown()` is the missing sentence.

## 2. The design in one paragraph

`defer <call>` evaluates the call's callee and arguments **now** and runs the
call when the **enclosing function** exits — Go's semantics, including the
conditional idiom `if cond { defer f.Close() }`. Deferred calls run LIFO,
after the return operands are retained and before the function's remaining
locals release; they run on every *normal* function exit and **never** on a
panic/trap/exit (aborts do not unwind; no `recover`; a panic inside a deferred
call abandons the rest). The **one divergence from Go is loud, not silent**: a
defer statement **may not appear inside a loop** (compile error). That
restriction is what makes every lexical defer execute **at most once** per
activation (Binate has no `goto`/labels), so the pending set is static and the
implementation is fixed frame slots — **zero hidden allocation** — where Go
needs a runtime defer-record list (unbounded in loops, colliding with Binate's
allocation-transparency principle). It also happens to delete Go's best-known
defer wart (silent loop accumulation).

## 3. Proposed spec text

Grammar (`binate.ebnf` **and** §14.1's inline `Statement` production —
`stmt.kinds`): `Statement` gains a `DeferStmt` alternative; `stmt.simple`'s
non-simple enumeration gains "defer statements" (so `defer` cannot appear in a
`for` clause — independently of the loop restriction), and the new section
opens with its grammar per the per-construct rubric:

```
Statement  = … | DeferStmt | … ;
DeferStmt  = "defer" Expression ;
```

`defer` becomes the **25th reserved keyword** (§5.4: the list re-flows and the
count "24" becomes 25; the `binate.ebnf` reserved-keyword comment block gains
it too; Annex A / `rule-ids.txt` regenerate). **No ASI change**: `defer` is not
an insertion-trigger keyword, so a newline after `defer` continues the
statement (deliberate; Go behaves the same). No identifier in the tree spells
`defer`, so the reservation breaks nothing.

New section — **§14.13 "Defer statements"**, inserted after §14.12
(break/continue); Terminating statements renumber to §14.14 and the deliberate
absences to §14.15, with a cross-reference sweep (18 `§14.13`/`§14.14`
citations across 9 spec files, per grep; re-enumerate at landing time). Rules
(declared at column 0 in the spec file — the blockquotes here are presentation
only):

> `stmt.defer` — A **defer statement** `defer c` schedules the call `c` to run
> when the **enclosing function** exits (`stmt.defer.exit`). The call's
> **callee** — the function reference, the function value, or a method's
> receiver — **and every argument are evaluated when the defer statement
> executes**; the **call executes at function exit**. The evaluated values are
> retained with the **function's lifetime**: they behave as anonymous
> function-scope locals, released with the function's exit releases (§18.4)
> **after all pending deferred calls have run** — *not* as statement
> temporaries (see the §18.4/§9.7 amendment below). The deferred call
> **borrows** them as the caller's references under the ordinary call contract
> (§18.5 `mem.param` — the caller-side reference is unaffected by the call).
> Where an operand
> undergoes a **managed→raw** conversion at the defer site (§8.4), the
> **pre-conversion managed value** is what is retained, and the borrow is
> delivered at call time — preserving the argument-borrow liveness guarantee.
> A raw operand value *not* backed by a retained managed value is an ordinary
> borrow whose referent's liveness at call time is the programmer's
> responsibility (§18.7 `mem.raw-uaf`). The call's results, if any, are
> **discarded**; a discarded **managed** result is released **immediately
> after the call returns**, before the next pending deferred call runs.
> Because a defer statement cannot appear in a loop (`stmt.defer.no-loop`) and
> the language has no `goto` (§14.15), each lexical defer statement executes
> **at most once** per function activation, and the defer statements that
> execute do so in **lexical order**. A defer statement inside a **function
> literal** defers to that literal's own activation. A defer statement
> requires an enclosing function; in the REPL's **immediate mode**, a `defer`
> entered with no enclosing function is rejected.
>
> `stmt.defer.call` _(Constraint)_ — The operand shall be a **call**: a
> function call, a method call, or a function-value call (including a call of
> the predeclared `panic`). A non-call expression, or a builtin-operation
> keyword form (`make(…)`, `cast(…)`, …, §15.1 — special call shapes, not
> calls), is rejected.
>
> `stmt.defer.no-loop` _(Constraint)_ — A defer statement shall not appear
> **lexically inside a `for` statement** with no intervening function literal
> between the defer statement and the `for` (a defer inside such a literal
> belongs to the literal and is unrestricted). Rejected with a message of the
> form "defer may
> not appear in a loop; wrap the loop body in a function or call the cleanup
> explicitly". _(Rationale: this keeps each lexical defer to at most one
> pending call — a fixed, statically-known set — so `defer` costs no hidden
> allocation; it also removes Go's silent loop-accumulation wart. The
> restriction is deliberately loud: the one Go idiom that does not transfer
> fails to compile rather than silently misbehaving.)_
>
> `stmt.defer.exit` — Scheduled deferred calls run when the function exits
> **normally**: at a `return`, or on falling off the end of the body. A
> `break`, `continue`, or inner-block exit does **not** run deferred calls
> (they are function-scoped), and does not affect the inner blocks' ordinary
> scope-exit releases (§18.4 `mem.scope-exit`), which happen when those blocks
> exit, as today. At the function exit the pending deferred calls run in
> **reverse order of their scheduling (LIFO)** — equivalently, reverse lexical
> order of the defer statements that executed (`stmt.defer`) — and **then**
> the function's remaining live managed locals are released. A deferred call
> therefore runs while the function's still-open scopes' locals are live; no
> user code runs **between** the releases themselves (preserving §18.4/§21.5's
> release-order unobservability).
>
> `stmt.defer.return` — On a `return`, the return operands are evaluated and
> each **managed** result **acquires its owning reference first** (§18.5
> `mem.return`); the pending deferred calls then run (`stmt.defer.exit`); the
> function's locals are then released and the retained results transfer to
> the caller. Deferred code observes the post-evaluation state but **cannot
> change a returned managed value** (results are unnamed and already
> retained). _Note:_ a returned **raw** value that borrows state a pending
> deferred call releases or mutates dangles exactly as if that cleanup call
> were written textually before the `return` (§18.7 `mem.raw-uaf`); returning
> managed values is the safe pattern.
>
> `stmt.defer.no-abort` — Deferred calls run on **normal function exits
> only**. A defined non-recoverable panic (§17.5), a trap, or a runtime
> **exit** primitive terminates the program **without running deferred calls**
> — and one occurring **inside a deferred call** terminates the program
> immediately: the remaining pending deferred calls, and the pending releases
> of the exit in progress, do **not** run. (Deliberate divergence from Go,
> which runs the remaining deferred functions while panicking and offers
> `recover`; a Binate panic is the program's last action — §17.5, §14.15.)

Amendments the statement forces (the ratification touch-list):

- **§18.4 `mem.temporary` + §9.7 `decl.scope.statement` carve-out
  (normative)**: a defer statement's evaluated callee/receiver/argument values
  are **not** statement temporaries — they behave as anonymous function-scope
  locals, released with the function's exit releases **after all pending
  deferred calls have run** (`stmt.defer.exit`); the defer statement's *other*
  temporaries still release at the end of the statement.
- **§18.4 release-order note + §21.5 unspecified-behavior row**: requalified —
  deferred calls are **sequenced before** the function-exit releases
  (`stmt.defer.exit`); *among the releases themselves* order remains
  unobservable, since no user code runs between them.
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
  feature (with the loop restriction called out as the one loud divergence),
  and "explicit call on every exit path" becomes "or `defer`".
- Regenerate Annex A (`gen-annex-a.py`) and `rule-ids.txt`
  (`extract-rule-ids.py`).

## 4. Rationale for the shape (decided + carried)

**Function-scoped (DECIDED — option A).** Block scoping was considered (it
rides the per-block cleanup machinery and enables per-iteration cleanup) and
**rejected** for two reasons the owner weighed decisive: (1) conditional
defers — `if cond { defer f.Close() }`, a core Go idiom — become inexpressible
directly, and worse, the Go spelling would **compile and silently mean
something else** (cleanup at end of the `if`), a false friend of the worst
kind; (2) divergence from Go carries real cost, and defer scoping is ergonomic
preference, not load-bearing semantics. Function scoping keeps Go's semantics
exactly — with the loop restriction as the **one, diagnosed** difference.

**The loop restriction (the enabling trade).** Pure Go defer needs a *runtime*
pending list precisely because loops make the pending set dynamic — in Binate
that means unbounded hidden allocation (heap or stack), colliding with
allocation transparency. Banning `defer` lexically inside `for` (with no
`goto` in the language) caps every lexical defer at one pending call, so the
whole feature compiles to **fixed frame slots** (an armed flag + operand slots
per lexical defer) with static LIFO emission at each exit — and the lost idiom
is the one Go programmers already avoid (loop-accumulated defers).
Per-iteration cleanup remains available the Go way: wrap the body in a
function literal and defer inside it, or call the cleanup explicitly.

**Eager operand evaluation.** The callee, receiver, and arguments are
snapshotted at the defer statement — the same snapshot philosophy as Binate's
closures (`func.closure.capture`: capture is **by value**, at evaluation
time). One mental model, no late-binding surprises, and identical to Go's
argument-evaluation rule. Late observation, where wanted, goes through a
pointer, exactly as with closures — which is also how the conditional-cleanup
hoist (`defer closeIfSet(&f)`) reads state set after the defer statement.

## 5. What this deliberately does not include

- **No `recover`, no panic interaction** — Binate panics abort; defers run on
  normal function exits only, and a panic *inside* a deferred call abandons
  the rest (`stmt.defer.no-abort`). This deletes the hardest part of Go's
  defer chapter (defer/panic/recover interplay, defers during unwinding).
- **No defer in loops** (`stmt.defer.no-loop`) — the deliberate, diagnosed
  restriction; see §4.
- **No `errdefer`** (Zig) — the language cannot see an "error path": errors
  are ordinary values, so there is no channel to condition on.
- **No block-operand form** (`defer { … }`) — late-binding reads cut against
  the snapshot model; a small named function or function value covers it.
- **No change to returned values from deferred code** — no named results
  exist; this is a feature (Go's mutate-the-named-result idiom mostly serves
  `recover`).

## 6. Implementation notes (informative — NOT spec content)

Each lexical defer statement gets **fixed frame slots**: an armed flag plus
its retained operand values, materialized as **anonymous function-scope
slots**. Two conditions the balance depends on: the slots are
**pre-registered at function-entry depth** (a pre-pass over the function's
static lexical-defer set, like parameters — NOT registered at the defer
statement, else a nested-block defer's slots get scope-released and truncated
out of later sweeps at block exit), and they are **nil/false-initialized at
entry** (the backends do not zero allocas; a return taken before the defer
executes must sweep nil no-op slots, not stack garbage). Under those two, every
existing release sweep — return's whole-function cleanup
(`emitDecForManagedLocals`, already emitted per return site) and the VM-only
fault pads (`emitPadCleanup`) — releases the retained operands unchanged; no
new leak path. Executing the defer statement stores the operands and sets the
flag. Each return site (and the body's fall-off end) emits, **before** the
existing whole-function release: for each lexical defer in reverse lexical
order, "if armed → call + that call's discarded-result/temp cleanup". This is
statically correct because executed defers execute in lexical order
(`stmt.defer`), so reverse-lexical = LIFO. `break`/`continue`/block-exit
emitters are untouched. The **VM fault pads emit no deferred calls**
(`stmt.defer.no-abort`); their RefDec-only cleanup covers the operand slots,
so memory balances on a VM-isolated fault (that isolation facility is internal
machinery the core spec does not describe; both modes agree a fault runs no
defers). One shared IR-gen serves both modes. The `stmt.defer.no-loop` check
is a simple syntactic walk (inside a ForStmt's body/clauses, not separated by
a function literal). Keyword addition is mechanical (token.bni enum + one
TypeName case; table-driven lexer; zero identifier collisions repo-wide; a
checker comment already anticipates the feature). BUILDER note: *adding* the
keyword compiles under the current BUILDER; *using* `defer` inside cmd/bnc's
BUILDER-compiled tree waits for a BUILDER cut that carries it.

## 7. Decisions & open questions

**DECIDED (owner, 2026-09-02):** function-scoped with the loop restriction
(option A), over pure-Go function scoping (hidden unbounded allocation in
loops) and block scoping (silent conditional-defer false friend).

**RATIFIED (owner, 2026-09-02, "recs are fine"):**
1. **Operand breadth:** call-only (`stmt.defer.call`), with `defer panic("…")`
   allowed (it is a call of the predeclared `panic`).
2. **Faults under the VM's internal isolation facility:** no deferred calls on
   an isolated fault (both modes agree; the facility is extra-spec — the VM's
   fault pads release the operand slots but run no calls).
3. **Keyword:** `defer` reserved immediately (25th keyword, §5.4; zero
   identifier collisions in the tree at ratification time).

## 8. Sources

Grounded in: §14/§14b statement grammar and absences, §5.4 keyword list + ASI
rules, §18.4/§18.5 scope-exit/return ownership text, §17.4–17.5
termination/panic rules and the §21.5 unspecified-behavior table; the original
rationale (`differences-with-go.md`, `claude-discussion-detailed-notes.md`
§12); the VM.Shutdown leak entry; the implementation recon (per-edge cleanup
emitters in `pkg/binate/ir`, keyword table in `pkg/binate/token`, zero `defer`
identifier collisions); two adversarial reviews of the block-scoped draft
(their carried findings: the two-phase defers-then-releases order, the
`mem.temporary` carve-out, panic-inside-a-defer, raw-operand retention,
discarded-result timing, renumbering/amendment completeness, REPL rejection);
and the owner's option-A decision with its rationale (conditional-defer false
friend; Go-divergence cost; allocation transparency).
