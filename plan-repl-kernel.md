# Plan: `repl.Kernel` — a driver-agnostic evaluation kernel (Jupyter-ready)

Status: **DRAFT (design ratified 2026-07-16; not yet started).** Reshapes the
embeddable REPL engine (`pkg/binate/repl`) from a line-push read-loop into a
request/reply **`Kernel`** whose driver owns the loop, the presentation, and
the transport. Extends and closes the two deferred output decisions in
[`plan-repl-embeddable.md`](plan-repl-embeddable.md) (§Decisions #3 and #4).

Companion docs: [`plan-repl-embeddable.md`](plan-repl-embeddable.md) (the
embeddable-engine refactor this builds on), [`plan-repl.md`](plan-repl.md) (the
shipped 5-tier REPL + the deferred pretty-printer), [`plan-wasm-browser.md`](plan-wasm-browser.md)
(a downstream non-CLI driver).

---

## Why

The embeddable engine already made the hard, correct move: **the host owns
I/O, owns the read, and the engine is a loop-free importable library.** But its
entry-point surface is shaped like *a read loop turned inside out* — the host
feeds ONE line at a time and the engine accumulates, decides completeness, and
prints its own framing:

```binate
interface ReplSession {          // today (pkg/binate/repl.bni)
    Init() StepResult
    Step(line *[]readonly uint8, eof bool) StepResult   // one line; engine accumulates
    SetPoll(poll @func() PollResult)
}
```

That shape doesn't fit a Jupyter kernel — or several other drivers — because of
two mismatches:

1. **Line-accumulation vs. complete units.** The engine owns multi-line
   accumulation + completeness (`Accumulated` buffer, `computeOpenDepth`,
   `STEP_NEED_MORE`, `Depth`). A notebook frontend sends a WHOLE complete cell in
   one `execute_request` — it never dribbles lines. And the completeness check is
   itself a *separate* Jupyter call (`is_complete_request`, used by console
   frontends to decide Enter-vs-newline), not something to bury inside evaluation.

2. **Presentation baked into the engine.** `Init()` prints the banner string;
   per-turn errors are pre-formatted and flushed as strings (`s.errln(FormatCheckError(...))`);
   the result value isn't echoed; and evaluated-code `println` output bypasses the
   sink entirely (it lowers to the `bootstrap.Write` extern, bound direct to native
   fd 1 in `interp/externs.bn`). A kernel must return all of that as **data** so the
   driver frames it — a notebook builds an `error` message with a traceback and an
   `execute_result` with a MIME bundle; the CLI just prints a line.

### Drivers this shape serves ("as well as other situations")

The request/reply `Kernel` is more general than the line-push `Step`:

- **Jupyter** — notebook / console / qtconsole, over the Jupyter messaging protocol.
- **Wasm worker** (the `plan-wasm-browser.md` B1 consumer) — I/O over message ports; can't block on inbound `postMessage`.
- **IDE / LSP-ish** — `Complete`/`Inspect` are exactly completion + hover/signature.
- **Test harness** — drive `Execute` with no loop, assert on `Result`.
- **Agent / tool** — evaluate a snippet, capture structured output + errors.
- **CLI** (`cmd/bni`) — one driver among many, via the `RunReadLoop` helper.

### Good bones already in place (keep, don't rebuild)

- **Host-injected I/O** — a struct of `@func` sinks, not hardwired to stdout.
- **Host-injected externs** — native bindings stay with the host (`registerExterns` callback); the engine stays tier-2-clean.
- **Setup errors as values** — `NewReplSession → @[]ReplError`; the engine never `Exit()`s.
- **A VM-free interrupt seam** — `SetPoll` / `PollResult`; this *is* Jupyter's `interrupt_request`, already plumbed (inert).
- **Session state persistence** — the `replSession` struct (VM, main module, persistent `Checker`, loader, counters) is exactly a kernel's per-session state.

`pkg/binate/repl` is tier-2 (built by `bnc`, not BUILDER) — so the full
language (interfaces, generics, capturing closures, function values) is
available here.

---

## Ratified decisions (2026-07-16)

| # | Decision | Choice |
|---|---|---|
| 1 | Naming | **Package stays `repl`; `Kernel` is the headline interface; drop the stuttering `Repl*` prefixes** (`ReplIO`→`IO`, `ReplError`→`Error`, `StepResult`→`Result`). `repl.Kernel` reads clean and non-stuttering; "repl" names the domain, "Kernel" the central engine. |
| 2 | Loop placement | **Reusable `repl.RunReadLoop` helper**, clearly secondary to `Kernel`. Simple hosts/tests (and `cmd/bni`) share it; Jupyter/wasm ignore it. The loop is a *user* of the kernel, not part of it. |
| 3 | Output decisions | **Interface carries them day 1; implement incrementally.** `Result` carries `Display`+`Err` and `IO` narrows to the streaming role from the start; the implementation lands in ordered increments so each stays green and a Jupyter skeleton can target the full surface early. |
| 4 | Push model | **Retained, refined.** `plan-repl-embeddable.md` Decision #1 ("push; host owns the read") stands — but the *unit* changes from a line to a complete code unit: `Execute(completeCode)` + a separate `IsComplete(code)` replace `Step(line, eof)`. Host-owns-read is unchanged; line-accumulation moves into the driver (via `RunReadLoop`). |
| 5 | Transport | **Out of scope here.** A Jupyter *transport* driver (ZMQ 5-socket wiring, HMAC message signing, the wire protocol) is a separate future driver. This plan makes the `Kernel` *shaped* for it; it does not build the transport. |

This closes `plan-repl-embeddable.md`'s deferred **Decision #3** (evaluated-code
output routing) and **Decision #4** (result echo / display) — both were gated on
interfaces + generics, which have since landed.

---

## The `Kernel` surface

```binate
package "pkg/binate/repl"

// Kernel is the driver-facing handle to a running evaluation session. It is an
// INTERFACE so a driver can hold any kernel behind it — the production VM-backed
// engine or a test fake. State (execution count, defined symbols, imports)
// persists across Execute calls; the driver owns the loop, the presentation, and
// the transport.
interface Kernel {
    // Execute runs ONE complete code unit and returns its structured outcome.
    // stdout/stderr from evaluated code STREAM through the IO sink as produced
    // (Jupyter IOPub `stream`); the result value, errors, status, and execution
    // count come back in Result (Jupyter `execute_reply` + `execute_result` +
    // `error`). Maps to execute_request.
    Execute(code *[]readonly uint8) Result

    // IsComplete reports whether code is a complete submittable unit (the
    // bracket-depth logic previously buried in Step). Maps to is_complete_request;
    // console/CLI drivers use it to decide Enter-vs-newline.
    IsComplete(code *[]readonly uint8) Completeness

    // Complete returns tab-completion matches at a cursor. Maps to
    // complete_request. Drives off the persistent Checker's live scope + the
    // loader's symbol graph.
    Complete(code *[]readonly uint8, cursorPos int) Completion

    // Inspect returns introspection (type / signature / doc) at a cursor. Maps to
    // inspect_request (Shift-Tab / `?`).
    Inspect(code *[]readonly uint8, cursorPos int, detail int) Inspection

    // KernelInfo returns the banner + language metadata as DATA (the driver
    // renders it). Maps to kernel_info_request.
    KernelInfo() KernelInfo

    // SetPoll installs the driver's cooperative-interrupt delegate (VM-free).
    // Maps to interrupt_request. Reserved for the suspend/break stages; inert
    // until a driver arms it.
    SetPoll(poll @func() PollResult)
}

// NewKernel builds a session from already-parsed source files (load → typecheck
// → build VM → lower), ending BEFORE main runs, and returns any setup errors as
// VALUES. Inputs are neutral (no CLIArgs); registerExterns is host-injected so
// the NATIVE-ONLY libc/bootstrap bindings stay out of the tier-2 library.
func NewKernel(files @[]@ast.File, root @[]char, bniPaths @[]@[]char, implPaths @[]@[]char,
        io IO, registerExterns @func(@vm.VM)) (@Kernel, @[]Error)
```

### Value types

```binate
// IO is the driver-supplied sink for the STREAMS evaluated code writes to.
// (Engine framing — banner, prompts, error text — is NO LONGER emitted here:
// banner→KernelInfo(), prompts→driver, errors→Result.Err. So IO's whole job is
// the stdout/stderr streams of user code, pushed live during Execute — exactly
// Jupyter's `stream` messages / a wasm worker's output port.)
type IO struct {
    WriteOut @func(*[]readonly char) int   // evaluated-code stdout stream
    WriteErr @func(*[]readonly char) int   // evaluated-code stderr stream
}

// Result is the outcome of Execute: control status, the execution count for this
// turn, the rendered result-value representation(s) (empty when the turn has no
// value), and a structured error (nil on success). It never carries the stream
// payload — that already flowed through IO during evaluation.
type Result struct {
    Status    ExecStatus
    ExecCount int            // execution_count for this turn
    Display   @[]Display     // execute_result MIME bundle; empty if no value
    Err       @Diagnostic    // structured error; nil on OK
}

type ExecStatus int
const (
    EXEC_OK        ExecStatus = iota  // turn evaluated cleanly
    EXEC_ERROR                        // parse / check / eval error (see Result.Err)
    EXEC_SUSPENDED                    // turn suspended by an interrupt poll (future)
    EXEC_BROKE                        // turn aborted + unwound by an interrupt (future)
)

// Display is one representation of a value in a MIME bundle (a struct, not a
// map, since Binate has no built-in maps; a value may offer several).
type Display struct {
    Mime @[]char    // "text/plain", "text/html", ...
    Data @[]uint8
}

// Diagnostic is a structured error: a short name (ename), a message (evalue), and
// optional rendered traceback lines. The driver formats it (CLI prints a line; a
// notebook builds a colored traceback). Replaces today's pre-formatted WriteErr string.
type Diagnostic struct {
    Name      @[]char       // "ParseError", "TypeError", "RuntimeError", ...
    Value     @[]char       // the message
    Traceback @[]@[]char    // rendered lines; may be empty
}

// Completeness answers IsComplete: status + a suggested continuation indent.
type Completeness struct {
    Status int   // COMPLETE | INCOMPLETE | INVALID
    Indent int   // is_complete_reply.indent (continuation depth hint)
}
const (
    COMPLETE   int = 0
    INCOMPLETE int = 1
    INVALID    int = 2
)

type Completion struct {
    Matches     @[]@[]char
    CursorStart int          // replacement span start (Jupyter cursor_start)
    CursorEnd   int          // replacement span end   (Jupyter cursor_end)
}

type Inspection struct {
    Found   bool
    Display @[]Display       // rich representation of the inspected symbol
}

type KernelInfo struct {
    Banner      @[]char       // free-text banner (was Init()'s printed string)
    LangName    @[]char       // "binate"
    LangVersion @[]char
    Mimetype    @[]char       // "text/x-binate"
    FileExt     @[]char       // ".bn"
    // codemirror / pygments hints can join later without breaking the contract.
}

// Error is one setup-time error from NewKernel (load or type-check). Unchanged in
// spirit from today's ReplError, just de-prefixed.
type Error struct {
    Msg @[]char
}

// PollResult / POLL_* are unchanged from today (VM-free, so SetPoll carries no
// pkg/binate/vm dependency).
type PollResult int
const (
    POLL_CONTINUE PollResult = iota
    POLL_BREAK
    POLL_SUSPEND
)
```

### The `RunReadLoop` helper (secondary)

A small convenience for drivers that DO want a blocking read-accumulate loop
(the CLI, simple test harnesses). Jupyter/wasm ignore it. It is a *user* of
`Kernel`, deliberately not part of the interface:

```binate
// RunReadLoop drives a kernel with a host-supplied line reader: it accumulates
// lines until IsComplete reports COMPLETE (rendering a continuation prompt via
// render while INCOMPLETE), calls Execute, hands the Result to render, and loops
// until read signals EOF. The blocking read is the driver's — the kernel never
// blocks.
func RunReadLoop(k @Kernel, io IO,
        read @func() (@[]uint8, bool),          // returns (line, eof)
        render @func(Completeness, Result))     // driver renders prompt + result
```

`cmd/bni`'s REPL collapses to: build `IO` + `registerExterns`, `NewKernel`,
print `KernelInfo().Banner`, then `RunReadLoop` with a stdin reader and a
prompt/result renderer. The accumulation/completeness logic that lives in the
engine today moves into `RunReadLoop`.

---

## Jupyter message mapping

| Jupyter message | Kernel method / field | Today |
|---|---|---|
| `kernel_info_request` → reply | `KernelInfo()` | banner baked into `Init()` print |
| `execute_request` → `execute_reply` | `Execute(code) → Result{Status, ExecCount, Err}` | `Step`, but line-shaped |
| IOPub `stream` (stdout/stderr) | `IO.WriteOut` / `IO.WriteErr` during `Execute` | user output bypasses sink → fd 1 |
| IOPub `execute_result` | `Result.Display` | no result echo (deferred #4) |
| IOPub `error` | `Result.Err` (`Diagnostic`) | pre-formatted string to `WriteErr` |
| `is_complete_request` → reply | `IsComplete(code) → Completeness` | buried in `Step` (`computeOpenDepth`) |
| `complete_request` → reply | `Complete(code, pos) → Completion` | absent |
| `inspect_request` → reply | `Inspect(code, pos, detail) → Inspection` | absent |
| `interrupt_request` | `SetPoll` / `PollResult` | present (inert) |
| `shutdown_request` | drop the kernel value | implicit |

---

## Naming migration (Inc 1)

| Today (`pkg/binate/repl`) | Reshaped |
|---|---|
| `interface ReplSession` | `interface Kernel` |
| `NewReplSession(...)` | `NewKernel(...)` |
| `Init()` + `Step(line, eof)` | `Execute(code)` + `IsComplete(code)` + `KernelInfo()` |
| `type StepResult` | `type Result` (+ `Display`, `Err`) |
| `type StepStatus` / `STEP_*` | `type ExecStatus` / `EXEC_*` |
| `type ReplIO` | `type IO` (narrowed to streams) |
| `type ReplError` | `type Error` |
| `StepResult.Depth` / `.Counter` | `Completeness.Indent` / `Result.ExecCount` (split by concern) |
| `replSession` (impl struct) | `kernel` (impl struct) |

---

## Increments (each self-contained + green)

**Inc 1 — reshape the surface (critical path).**
Rename per the table; split `Step`→`Execute`+`IsComplete`; add `KernelInfo()`;
turn per-turn errors into `Result.Err` data (stop `s.errln(...)`); define `IO`
(narrowed) + `Result{Display, Err}` even though `Display` is empty and streams
aren't rerouted yet; add `RunReadLoop`; rewire `cmd/bni` onto it. User-code
output still goes to fd 1 (unchanged), `Display` empty. **Green end-to-end:** the
CLI REPL behaves as today; the surface is the new one.
*Files:* `repl.bni`, `repl/{session,step,eval,decl,mid_session_import,input,util}.bn`
+ tests; `cmd/bni/{repl,repl_input}.bn`.

**Inc 2 — `Complete`.**
Tokenize `code` up to `cursorPos`, isolate the partial identifier, enumerate
matching names from `s.Chk`'s live scope + imported package symbols; return
`Completion{Matches, CursorStart, CursorEnd}`. Additive; independent of Inc 1's callers.

**Inc 3 — `Inspect`.**
Resolve the identifier at `cursorPos` to its declaration via the persistent
`Checker`; render its kind/type/signature (and doc comment when available) into
`Inspection.Display`. Additive.

**Inc 4 — close Decision #3 (stream user output through `IO`).**
The host registers the `Write`/stderr externs as closures over the kernel's `IO`
sink instead of native fd 1 (`RegisterExtern` copies both words, so a capturing
`@func` sink binds cleanly — verify the extern registry is settled post-#169).
Now evaluated-code output streams through `IO.WriteOut/WriteErr` during `Execute`.
The kernel provides the wiring helper; the native binding stays host-side.
*Confirm during impl:* extern-rebind vs. package-impl injection (the mechanism
Decision #3 left open).

**Inc 5 — close Decision #4 (result display).**
New `pkg/replprint` pretty-printer (now unblocked by interfaces+generics): detect
a bare-expression turn with a value, capture the value, render it to a
`text/plain` (and later `text/html`) `Display`; populate `Result.Display`. This
is the largest chunk. See `plan-repl.md` §"Pretty-printing — DEFERRED".

**Ordering / MVP.** Inc 1 is the critical path. A *working Jupyter MVP* is
**Inc 1 + Inc 4** (execute a cell, see its output) → then Inc 5 (`Out[n]`
display) → then Inc 2/3 (completion/inspection, nice-to-have). Inc 2–5 are
mutually independent and can land in any order.

**Future (out of scope, tracked separately):** a Jupyter transport driver (ZMQ
5-socket + HMAC signing + wire protocol), a wasm-worker driver. Both consume the
`Kernel` surface this plan delivers.

---

## Verification

- **Unit:** the reshaped `pkg/binate/repl` `_test.bn` set (rename + new methods); `RunReadLoop` covered with a scripted reader + a `Result`-asserting renderer; `Complete`/`Inspect` fixture tests; a `pkg/replprint` test suite.
- **Engine stays tier-2-clean:** `pkg/binate/repl`'s dep closure must not pull in the native extern bindings (they're injected). Confirm with the existing tier audit.
- **CLI parity:** `e2e/repl.sh` must pass unchanged after Inc 1 (the CLI REPL is behavior-identical; only its internals moved into `RunReadLoop`).
- **Conformance:** `534` (the param→field-store anchor for the interrupt seam) stays green.

---

## Open questions / risks

1. **`Result.Display` for structs/managed values before `pkg/replprint`.** Inc 5 needs the pretty-printer; `text/plain` for scalars is easy, arbitrary aggregates need interface/generic dispatch. Scope `replprint`'s first cut to scalars + strings + slices, grow from there.
2. **Cursor-position semantics for `Complete`/`Inspect`.** Byte offset vs. rune offset (Jupyter uses codepoint offsets). Decide + document; the lexer works in bytes today.
3. **Extern-rebind vs. package-impl injection for Inc 4.** Decision #3 left the mechanism open; confirm the registry is settled (post-#169) and a capturing `Write` sink binds without the RefInc surgery the old path needed.
4. **`Traceback` content.** The VM has a heap frame stack; how much of it becomes `Diagnostic.Traceback` (frames? source lines?) is a follow-up — empty is acceptable for the MVP.
5. **Is a distinct `WriteResult` sink still wanted?** `plan-repl-embeddable.md` Decision #4 reserved one; with result display now carried in `Result.Display` (data, not a stream), the driver frames the result itself — so a separate sink channel may be unnecessary. Confirm at Inc 5.
