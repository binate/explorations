# Plan: `repl.Kernel` — a driver-agnostic evaluation kernel

Status: **DRAFT v2 (design ratified 2026-07-16; revised after a 4-lens adversarial
review).** Reshapes the embeddable REPL engine (`pkg/binate/repl`) from a
line-push read-loop into a request/reply **`Kernel`** whose driver owns the loop,
the presentation, and the transport.

**Revision note (v2, post-review).** The v1 draft had real defects the review
caught. This revision: (1) corrects factual errors about the current code — the
`fd=1` selection lives in the print *lowering* (`gen_print.bn`), not the extern
binding, and there is no stderr extern at all; (2) **drops the v1 "`IO` stream
sink"** — v1 returns the engine's own output as `Result` DATA, and capturing
*evaluated-code* output/stdin is deferred to package-impl injection (the mechanism
`plan-repl-embeddable.md` Decision #3 already ratified); (3) makes per-turn errors
plural (`Result.Err @[]Diagnostic`); (4) surfaces `Complete`/`Inspect`'s hidden
`pkg/binate/types` prerequisites; (5) corrects the stale single-session
justification; and (6) **de-scopes Jupyter wire-protocol detail** — Jupyter is one
*motivating example* that suggested the `execute`/`complete`/`inspect` shape, not a
field-by-field spec the interface must satisfy.

Companion docs: [`plan-repl-embeddable.md`](plan-repl-embeddable.md) (the
embeddable-engine refactor this builds on), [`plan-repl.md`](plan-repl.md) (the
shipped 5-tier REPL + the deferred pretty-printer), [`plan-wasm-browser.md`](plan-wasm-browser.md)
(a downstream non-CLI driver).

---

## Why

The embeddable engine already made the hard, correct move: **the host owns the
read, and the engine is a loop-free importable library.** But its entry-point
surface is shaped like *a read loop turned inside out* — the host feeds ONE line
at a time and the engine accumulates, decides completeness, and prints its own
framing:

```binate
interface ReplSession {          // today (pkg/binate/repl.bni)
    Init() StepResult
    Step(line *[]readonly uint8, eof bool) StepResult   // one line; engine accumulates
    SetPoll(poll @func() PollResult)
}
```

Two mismatches make that shape a poor fit for the drivers we care about:

1. **Line-accumulation vs. complete units.** The engine owns multi-line
   accumulation + completeness (`Accumulated`, `computeOpenDepth`,
   `STEP_NEED_MORE`, `Depth`). A notebook-style frontend submits a WHOLE complete
   cell in one shot — it never dribbles lines — and the completeness check is a
   *separate* operation a console frontend runs as-you-type, not something to bury
   inside evaluation.

2. **Presentation baked into the engine.** `Init()` prints the banner (`step.bn:26`);
   per-turn errors are pre-formatted and flushed as strings
   (`s.errln(FormatCheckError(...))`, `eval.bn:52`); the result value isn't echoed.
   A driver should get all of that back as **data** and frame it however it likes.

### The shape: a `Kernel`

The general abstraction is a **`Kernel`** — a stateful evaluation session a driver
pokes with requests: run a complete unit, ask whether a unit is complete, complete
an identifier, inspect a symbol, ask for the banner/metadata. The *loop* (read,
accumulate, render prompts) is a driver concern, not the kernel's.

This shape was suggested by Jupyter's kernel model (`execute`/`is_complete`/
`complete`/`inspect`/`kernel_info`), but **Jupyter is one example driver, not the
spec.** The same shape serves several situations:

- **Jupyter** — notebook / console, over its messaging protocol (a *future*
  transport driver; see "Out of scope").
- **Wasm worker** (`plan-wasm-browser.md`) — I/O over message ports; can't block on inbound messages.
- **IDE / LSP-ish** — `Complete`/`Inspect` are completion + hover/signature.
- **Test harness** — drive `Execute`, assert on `Result`, no loop.
- **Agent / tool** — evaluate a snippet, capture structured output + errors.
- **CLI** (`cmd/bni`) — one driver among many, via `RunReadLoop`.

### Good bones already in place (keep, don't rebuild)

- **Host-injected externs** — native bindings stay with the host (`registerExterns`); the engine stays tier-2-clean.
- **Setup errors as values** — `NewReplSession → @[]ReplError`; the engine never `Exit()`s.
- **A VM-free interrupt seam** — `SetPoll` / `PollResult`. **Honest status:** today the seam only *records* a status (`vmPollPoint` sets `VM_STATUS_SUSPENDED/BROKE`, `vm_exec_helpers.bn`); it does NOT unwind, and poll points fire only at VM bytecode-loop back-edges (`vm_exec.bn`) — never inside a native/C call. Real interruption (unwind) is `plan-repl-embeddable.md` Stages 6/7, still FUTURE. So a driver cannot yet honor a Ctrl-C-style abort; the seam is scaffolding.
- **Session state persistence** — the `replSession` struct (VM, main module, persistent `Checker`, loader, counters) is exactly a kernel's per-session state.

`pkg/binate/repl` is tier-2 (built by `bnc`, not BUILDER) — so the full language
(interfaces, generics, capturing closures) is available here.

---

## Ratified decisions (2026-07-16)

| # | Decision | Choice |
|---|---|---|
| 1 | Naming | **Package stays `repl`; `Kernel` is the headline interface; drop the stuttering `Repl*` prefixes** (`ReplError`→`Error`, `StepResult`→`Result`, `StepStatus`→`ExecStatus`). `repl.Kernel` reads clean; "repl" names the domain, "Kernel" the engine. |
| 2 | Loop placement | **Reusable `repl.RunReadLoop` helper**, clearly secondary to `Kernel`. The loop is a *user* of the kernel. Separate `renderPrompt`/`renderResult` callbacks (not one conflated callback) and explicit EOF handling. |
| 3 | Engine output | **Returned as `Result` DATA, not through a stream sink.** v1 has NO I/O sink: `Execute → Result` carries the rendered value, the per-turn errors (`@[]Diagnostic`), and the engine's informational notices (`@[]Notice`); `KernelInfo()` carries the banner. The driver renders all of it. (This supersedes the old `ReplIO` framing sink, which is dissolved.) |
| 4 | Evaluated-code I/O capture | **Deferred to package-impl injection** (the mechanism `plan-repl-embeddable.md` Decision #3 ratified — NOT extern-rebind). Capturing a cell's `println` output, and routing its stdin reads, is done by injecting session-scoped `bootstrap`/`os` implementations that route through the driver — future work. **Full capture of arbitrary side effects is impossible in general** (a cell can do a raw syscall, MMIO, etc.); the kernel captures what injection can reach and no more. On platforms where it's feasible, a driver may instead reroute the real fds. |
| 5 | Push model | **Retained, refined.** `plan-repl-embeddable.md` Decision #1 ("push; host owns the read") stands — but the *unit* changes from a line to a complete code unit: `Execute(completeCode)` + `IsComplete(code)` replace `Step(line, eof)`. Line-accumulation moves into the driver (via `RunReadLoop`). |
| 6 | Scope | **Support the general kernel shape, NOT a specific transport's wire protocol.** A concrete transport (Jupyter, wasm) is a separate future driver with its own message/field mapping (execution flags, message splits, indent conventions, kernelspec, etc.); none of that is modeled in the `Kernel` surface. |

---

## The `Kernel` surface

```binate
package "pkg/binate/repl"

// Kernel is the driver-facing handle to a running evaluation session. State
// (turn counter, defined symbols, imports) persists across Execute calls; the
// driver owns the loop, the presentation, and any transport.
interface Kernel {
    // Execute runs ONE complete code unit and returns its structured outcome:
    // status, the turn index, the rendered result value (if any), the engine's
    // informational notices, and zero-or-more diagnostics. Evaluated-code stdout
    // is NOT captured in v1 (see Decision #4) — it goes wherever the host's
    // injected externs send it.
    Execute(code *[]readonly uint8) Result

    // IsComplete reports whether code is a complete submittable unit (the
    // bracket-depth logic previously buried in Step). Drivers use it to decide
    // submit-vs-continue.
    IsComplete(code *[]readonly uint8) Completeness

    // Complete returns identifier-completion matches at a cursor.
    // PREREQUISITE (Inc 2): a Scope-enumeration API in pkg/binate/types.
    Complete(code *[]readonly uint8, cursorPos int) Completion

    // Inspect returns introspection (kind / type / signature) at a cursor.
    // PREREQUISITE (Inc 3): Symbol must retain a signature / doc source.
    Inspect(code *[]readonly uint8, cursorPos int) Inspection

    // KernelInfo returns the banner + minimal language metadata as DATA.
    KernelInfo() KernelInfo

    // SetPoll installs the driver's cooperative-interrupt delegate (VM-free).
    // Scaffolding only — records a status, does not unwind (see "Good bones").
    SetPoll(poll @func() PollResult)
}

// NewKernel builds a session from already-parsed source files (load → typecheck
// → build VM → lower), ending BEFORE main runs, and returns any setup errors as
// VALUES. No io parameter (v1 has no stream sink). registerExterns is
// host-injected so the NATIVE-ONLY libc/bootstrap bindings stay out of the
// tier-2 library — and is also where a future driver injects output-capturing
// package impls (Decision #4).
func NewKernel(files @[]@ast.File, root @[]char, bniPaths @[]@[]char, implPaths @[]@[]char,
        registerExterns @func(@vm.VM)) (@Kernel, @[]Error)
```

### Value types

```binate
// Result is the outcome of Execute. It carries everything the engine has to say
// about the turn, as data — the driver renders it.
type Result struct {
    Status  ExecStatus
    Turn    int            // this turn's index (In[n]/Out[n]); advances every evaluated
                           // turn. Doubles as the __repl_<n> symbol-uniqueness seed today.
    Display @[]Display     // rendered representation(s) of the result value; empty if none
    Notices @[]Notice      // informational engine framing ("package X loaded", "f parked", ...)
    Err     @[]Diagnostic  // zero-or-more per-turn errors (a turn can emit several)
}

type ExecStatus int
const (
    EXEC_OK        ExecStatus = iota  // turn evaluated cleanly (may still carry Notices)
    EXEC_ERROR                        // turn produced one or more diagnostics (see Err)
    EXEC_SUSPENDED                    // reserved: turn suspended by an interrupt poll (future)
    EXEC_BROKE                        // reserved: turn aborted + unwound by an interrupt (future)
)
// NOTE: a mutating turn (a mid-session `import`) may PARTIALLY succeed — some
// packages register while another errors — so Execute can leave session state
// changed even on EXEC_ERROR. Callers must not assume EXEC_ERROR ⇒ no effect.

// Display is one representation of a value (a struct, not a map — Binate has no
// built-in maps). A value may offer several (e.g. plain-text and a richer form).
type Display struct {
    Mime @[]char    // "text/plain", ...
    Data @[]uint8
}

// Notice is one informational engine message. Kept as data (like Err) so the
// driver decides whether/how to show it (a CLI prints it; a headless harness
// ignores it).
type Notice struct {
    Msg @[]char
}

// Diagnostic is one structured error: a short name, a message, and optional
// rendered traceback lines. The driver formats it. (Sourcing a real traceback
// needs walking the VM heap frame stack AND a bytecode-PC→source-position map
// that does not exist yet — see Open Questions; empty is fine for the MVP.)
type Diagnostic struct {
    Name      @[]char       // "ParseError", "TypeError", "RuntimeError", ...
    Value     @[]char       // the message
    Traceback @[]@[]char    // rendered lines; may be empty
}

// Completeness answers IsComplete. Status is a named type (matching ExecStatus /
// PollResult / the current StepStatus rationale), so an arbitrary int can't be
// assigned as a completeness status. Indent is meaningful only when INCOMPLETE.
type Completeness struct {
    Status CompStatus
    Indent int          // continuation-depth hint (a transport converts to its own indent form)
}
type CompStatus int
const (
    COMP_COMPLETE   CompStatus = iota  // a full unit; submit it
    COMP_INCOMPLETE                    // open brackets remain; keep reading
    COMP_INVALID                       // cannot become complete (unbalanced close)
)

type Completion struct {
    Matches     @[]@[]char
    CursorStart int          // replacement span start
    CursorEnd   int          // replacement span end
}

type Inspection struct {
    Found   bool
    Display @[]Display       // rich representation of the inspected symbol
}

type KernelInfo struct {
    Banner      @[]char       // free-text banner (was Init()'s printed string)
    LangName    @[]char       // "binate"
    LangVersion @[]char
    FileExt     @[]char       // ".bn"
}

// Error is one setup-time error from NewKernel (load or type-check).
type Error struct {
    Msg @[]char
}

// PollResult / POLL_* unchanged from today (VM-free, so SetPoll carries no
// pkg/binate/vm dependency).
type PollResult int
const (
    POLL_CONTINUE PollResult = iota
    POLL_BREAK
    POLL_SUSPEND
)
```

### The `RunReadLoop` helper (secondary)

A small convenience for drivers that DO want a blocking read-accumulate loop (the
CLI, simple test harnesses). Wasm/transport drivers ignore it. Split callbacks —
one for the prompt (before a read), one for a completed turn's result — so the two
disjoint events aren't conflated, and EOF is surfaced explicitly:

```binate
// RunReadLoop accumulates lines from read until IsComplete reports COMP_COMPLETE
// (calling renderPrompt with the running Completeness while INCOMPLETE), then
// Execute → renderResult, and loops. EOF mid-accumulation discards the buffer and
// is reported to the driver via renderResult with an EOF-flavored Result (or a
// dedicated onEof — TBD at impl). The blocking read is the driver's; the kernel
// never blocks.
func RunReadLoop(k @Kernel,
        read @func() (@[]uint8, bool),        // (line, eof)
        renderPrompt @func(Completeness),     // draw "> " / "... " before each read
        renderResult @func(Result))           // render a completed turn's Result
```

`cmd/bni`'s REPL collapses to: build `registerExterns`, `NewKernel`, print
`KernelInfo().Banner`, then `RunReadLoop` with a stdin reader and prompt/result
renderers that `print` to stdout (behavior-identical to today — the accumulation
logic just moved from the engine into `RunReadLoop`).

---

## Jupyter (one example driver — illustrative, not a requirement)

A future Jupyter *transport* driver would map its messages onto this surface:
`execute_request`→`Execute`, `is_complete_request`→`IsComplete`,
`complete_request`/`inspect_request`→`Complete`/`Inspect`,
`kernel_info_request`→`KernelInfo`, `interrupt_request`→`SetPoll`. Its
wire-specific concerns — `silent`/`store_history`/`user_expressions`/`stop_on_error`
flags, the `display_data`-vs-`execute_result` split, `stream` messages, the
`aborted` reply status, the string `indent`, `interrupt_mode=message` in the
kernelspec, syntax-highlighting metadata — live **in that driver**, on top of this
surface. They are deliberately NOT modeled here (Decision #6); baking a specific
transport's fields into the `Kernel` would be over-fitting one example.

---

## Naming migration (Inc 1)

| Today (`pkg/binate/repl`) | Reshaped |
|---|---|
| `interface ReplSession` | `interface Kernel` |
| `NewReplSession(...)` | `NewKernel(...)` (no `io` param) |
| `Init()` + `Step(line, eof)` | `Execute(code)` + `IsComplete(code)` + `KernelInfo()` |
| `type StepResult` | `type Result` (+ `Display`, `Notices`, `Err @[]Diagnostic`) |
| `type StepStatus` / `STEP_*` | `type ExecStatus` / `EXEC_*` |
| `type ReplIO` (framing sink) | dissolved — engine output is `Result` data |
| `type ReplError` | `type Error` |
| `StepResult.Depth` / `.Counter` | `Completeness.Indent` / `Result.Turn` (split by concern) |
| `replSession` (impl struct) | `kernel` (impl struct) |

---

## Increments (each self-contained + green)

**Inc 1 — reshape the surface (critical path). ✅ LANDED on `main` (`6910166f`..`6fa25ae5`, 2026-07-16; verified by a 3-lens adversarial review that caught two land blockers — a dead `cmd/bni/repl_test.bn` `ReplIO` reference and 3 hygiene failures — fixed in Inc 1b-4).**
Rename per the table; split `Step`→`Execute`+`IsComplete`; add `KernelInfo()`;
turn per-turn errors into `Result.Err @[]Diagnostic` (stop `s.errln(...)`, collect
instead — every error path loops, so it's genuinely plural); turn the engine's
informational output (`decl.bn`/`mid_session_import.bn` "loaded"/"parked"/
"resolved"/cycle/shadow announcements) into `Result.Notices`; drop `ReplIO`; add
`RunReadLoop`; rewire `cmd/bni` onto it. Evaluated-code output still goes to fd 1
(unchanged); `Display` empty; `Complete`/`Inspect` land as stubs (Inc 2/3).
**Green end-to-end:** `repl` + `cmd/bni` unit tests, hygiene 17/17, `e2e/repl.sh`
55/0; the CLI renders Notices/Err from `Result`.

**One accepted behavior change (NOT byte-identical).** Notices are now returned
as `Result` DATA and rendered *after* `Execute` returns, while evaluated-code
`println` still streams to fd 1 *during* eval — so a single turn that both emits a
Notice AND runs printing user code renders them in the *reverse* order vs. the old
immediate-write sink. The only reachable case is a parked `var x = f()` whose
initializer prints, resolved via `retryPending` in the same turn (old: the
"resolved" notice then the output; new: the output then the notice). This is an
inherent, accepted consequence of the notices-as-data model (Decision #3) —
cosmetic and narrow; an `e2e` case pins the chosen order (follow-up).

**Inc 2 — `Complete`.**
*Prerequisite:* `pkg/binate/types` exposes no enumeration/prefix API (`Scope` has
only exact-name `Lookup`). Add a scope-enumeration API (walk the `Scope.Syms`
parent chain; enumerate an imported package's exported members) — this lands in a
**shared, BUILDER-relevant package**, so it is a real design task, not a REPL-local
pass. Then `Complete` tokenizes to `cursorPos`, isolates the partial identifier,
and enumerates matches.

**Inc 3 — `Inspect`.**
*Prerequisite:* `Symbol` carries no doc field and no source-span/decl back-ref, so
a signature/doc is unavailable at inspect time — retaining it is a checker
data-model change. Scope the MVP to kind + type (available on `Symbol` today);
defer signature/doc text behind that prerequisite, stated explicitly.

**Inc 4 — result display (`Result.Display`).**
New `pkg/replprint` pretty-printer (now unblocked by interfaces+generics): detect a
bare-expression turn with a value, capture the value (the kernel evaluates the
synthetic function and holds its return — *capturable*, unlike arbitrary side
effects), render it to `text/plain`. First cut: scalars + strings + slices; grow to
aggregates. See `plan-repl.md` §"Pretty-printing — DEFERRED".

**Deferred / future (tracked, not scheduled here):**
- **Evaluated-code output + stdin capture** via package-impl injection (Decision
  #4): inject session-scoped `bootstrap`/`os` impls that route a cell's output and
  stdin through the driver. Only then does a stream/sink concept return to the
  surface. Full side-effect capture remains impossible in general.
- **Real interrupt** (unwind): `plan-repl-embeddable.md` Stages 6/7 — gated on
  heap-frames + IR-gen landing pads; sized with the user.
- **A concrete transport driver** (Jupyter / wasm) consuming this surface.

**Ordering.** Inc 1 is the critical path. Inc 2/3/4 are independent and can land in
any order. A *usable interactive kernel* is Inc 1 (+ Inc 4 for value echo); output
capture is the deferred package-injection work above.

---

## Verification

- **Unit:** the reshaped `pkg/binate/repl` `_test.bn` set (rename + new methods); `RunReadLoop` with a scripted reader + `Result`-asserting renderers (covering multi-line accumulation, EOF-mid-accumulation discard, and a completed turn); `Complete`/`Inspect` fixture tests; a `pkg/replprint` suite.
- **Engine stays tier-2-clean:** `pkg/binate/repl`'s dep closure must not pull in the native extern bindings (injected). Confirm with the existing tier audit.
- **CLI parity:** `e2e/repl.sh` passes unchanged after Inc 1.
- **Conformance:** `534` (the capturing-`@func` param→field-store UAF regression that `SetPoll`'s wrapper depends on staying fixed — *not* an interrupt-seam test per se) stays green.

---

## Open questions / risks

1. **`Complete`/`Inspect` prerequisites are real `pkg/binate/types` work.** The scope-enumeration API (Inc 2) and `Symbol` doc/signature retention (Inc 3) don't exist. Size them before committing to Inc 2/3 as "small."
2. **Cursor-offset units for `Complete`/`Inspect`.** Byte vs. codepoint offset — the lexer is byte-based. Define + document the `Kernel` API's unit; a transport translates to its own convention.
3. **Package-injection mechanism for output/stdin capture (Decision #4).** Confirm the injection path can reach both the print lowering's `bootstrap.Write(1, …)` and `os`'s `__c_call("write", 2, …)` (user stderr does NOT flow through `bootstrap.Write`). This is deferred, but the two distinct sinks shape it.
4. **`Diagnostic.Traceback` content.** A non-empty traceback needs walking the VM heap frame stack AND a bytecode-PC→source-position map that doesn't exist — a real feature, not formatting. Empty for the MVP.
5. **Kernel restart in the same process.** `pkg/binate/types` still has init-once process-globals (the `target` + predeclared singletons; `SetTarget` refuses after init), so a second `NewKernel` in one process is same-target only. A restart-heavy transport may need a fresh process per restart — decide and document. (Note: the `ir` process-globals that `plan-repl-embeddable.md` Decision #6 cited as the single-session blocker are **gone** — threaded off in the reentrancy work; the residual constraint is the `types` globals above, not `ir`.)
6. **`RunReadLoop` EOF surfacing.** How EOF-mid-accumulation (buffer discard) reaches the driver — a flavored `Result` vs. a dedicated `onEof` callback — TBD at impl.
