# Plan: Embeddable / Coroutine-ish REPL

Status: **IN PROGRESS — Stages 1–3 + 4a/4b LANDED; Stage 4c underway**
(as of 2026-06-02). Supersedes the open design question in
`claude-todo.md` ("REPL refactor: embeddable component for non-CLI
hosts"). The "which shape (a/b/c)" question is decided (see Ratified
Decisions).

Landed on `main`: Stage 1 (`@ReplSession`, lift globals) `7045cf95`;
Stage 2 (`NewReplSession` constructor, errors as values) `4b95b1d1`;
Stage 3 (`ReplIO` framing sink) `7dcd1079`; Stage 4a (push-driven
`Init`/`Step`) and Stage 4b (`registerExterns` callback).

Stage 4c — extract the engine to `pkg/binate/repl` — is split into two
commits so each stays green: **commit 1** (stand up `pkg/binate/repl`
with the engine DUPLICATED — `repl.bni` + impl + full `_test.bn` set;
cmd/bni untouched) is committed on the branch (`d331ace9`, pending
cherry-pick); **commit 2** rewires cmd/bni to import `pkg/binate/repl`
and deletes the cmd/bni copies. Work happens in worktree
`temp-binate-4` / branch `repl-embeddable`.

Companion docs: [`plan-repl.md`](plan-repl.md) (the shipped 5-tier
REPL), [`pkg-layout-spec.md`](pkg-layout-spec.md) (tier-2 placement),
[`plan-wasm-browser.md`](plan-wasm-browser.md) (the downstream B1
consumer), [`plan-bni-heap-frames.md`](plan-bni-heap-frames.md) (gates
the deepest interrupt stage).

---

## Why

Today the REPL is welded to the CLI: `runRepl` (`cmd/bni/repl.bn`)
owns a blocking `for{}` loop that pulls input from stdin
(`bootstrap.Read(0,…)`) and pushes output to stdout
(`print`/`println` → `bootstrap.Write(1,…)`). That model can't embed
into a non-CLI host — most concretely a wasm worker, where I/O routes
through message ports and the worker must hand control back to the
event loop while waiting for input; also useful for test harnesses and
IDE integrations.

The refactor delivers three things the host needs:

1. **Embeddable** — the engine lives in an importable library, not the
   CLI binary.
2. **Pluggable I/O** — the host supplies the I/O sink; nothing in the
   engine assumes stdin/stdout.
3. **Coroutine-ish** — the host feeds one command and the engine runs
   until it needs the next command, then returns. No internal blocking
   loop.

Plus a designed-in (but not-yet-implemented) seam for two future
interrupt kinds: **break** (Ctrl-C-equivalent, unwind to the prompt)
and **continuable suspend** (pause/resume), both delivered through a
**poll delegate** the VM calls at safe points.

---

## Key facts the design rests on (all verified in tree)

- **Exactly one suspension point per turn.** `readReplLine`
  (`repl_input.bn:18`, the sole `bootstrap.Read` in the REPL) is the
  only place a turn blocks. Everything from one read to the next —
  parse, type-check, IR-gen, lower, VM call — runs synchronously to
  completion. So the push inversion needs **no** mid-turn
  checkpointing: a turn is a pure function of the accumulated bytes.
- **The engine is already I/O-decoupled.** `evalReplLine`
  (`repl.bn:169`) takes `(vmInst, mainMod, c, src, n)` explicitly and
  is unit-tested with no input loop (`repl_test.bn`,
  `repl_decl_test.bn`). Re-entrancy is blocked only by two sets of
  process-globals (below), which is mechanical to lift.
- **Two output categories, both redirectable.**
  - *Category A — REPL framing*: banner, prompts (`"> "`, `"... "`),
    parse/check error messages. Emitted directly via
    `print`/`println` from the REPL code (which runs as `cmd/bni`, not
    inside the VM).
  - *Category B — user-program output*: a typed `println(...)` runs
    *inside* `vmInst` and routes through the registered `bootstrap.Write`
    extern. **Redirectable without recompiling user code**:
    `RegisterExtern` overwrites in place and copies both the vtable and
    data words of the supplied function value (`vm.bn:264-289`), so a
    capturing `@func` closure over the host sink registers cleanly as
    the `Write` extern.
- **`cmd/bni` and `pkg/vm` are NOT in the BUILDER surface** (built by
  `bnc`, not BUILDER) — so the full language is available in the engine:
  interfaces, generics, capturing closures, function values. Phase-2
  function values (capturing closures) are landed.
- **The VM frame stack is heap-resident** (`vm.Stack`; `BC_CALL`
  pushes frames without native recursion) — so pure-interpreted
  execution is *suspendable in principle*. BUT the active frame's
  control state (`pc`, `funcIdx`, `regs`, `frameBase`) is host-stack-local
  in `execLoop` (`vm_exec.bn:17`); `BC_RETURN` stores the *caller's*
  pc/funcIdx/regsOff in the frame header (`vm_exec.bn:119-121`), so the
  active frame needs a side-field to hold its resume pc. This shapes
  the suspend stage.
- **Cleanup is emitted inline as bytecode** (`BC_REFDEC_INLINE_FAST`,
  `BC_SP_RESTORE` reclaim) at each scope's normal exit — the VM has no
  enumeration of live managed values needing RefDec. This is why a
  naive frame-discarding break **leaks**, and why break is the most
  expensive stage (needs new IR-gen support).

---

## Ratified Decisions (2026-06-02)

| # | Decision | Choice |
|---|---|---|
| 1 | I/O model | **Push.** Host owns the read; engine exposes `Step(line, eof)`. Pull is structurally impossible on a wasm worker (can't block on inbound `postMessage`). |
| 2 | `ReplIO` shape | **Struct of `@func` fields**, not an interface. One impl per host, chosen at construction, never polymorphically dispatched. Mirrors the extern table's own function-value representation; stays host-side (Phase-3-safe). |
| 3 | Category-B redirection | **Out of scope (revised 2026-06-02).** The REPL refactor handles only the engine's OWN framing output (cat. A). Redirecting output from EVALUATED user code is deferred — NOT via extern rebind (that machinery is being reworked); the right answer is injecting appropriate package implementations later. |
| 4 | Result echo | **Distinct `WriteResult` channel** deferred until result-echo lands (no result echo today). When it lands: separate from user stdout and diagnostics so a wasm/IDE host can frame eval results distinctly. Stage 3 shipped `WriteOut`/`WriteErr` only. |
| 5 | Interrupts in v1 | **Seam only.** Design + reserve the poll/status plumbing as inert (nil-poll = zero-overhead Continue); implement no interrupt behavior. Run-to-prompt is free under push. |
| 6 | Sessions in v1 | **Single live session per process.** The `ir` process-globals stay as-is. Multi-session is an explicit, tracked blocker — see `claude-todo.md` "REPL: remove process-global session state". **Do not add new REPL globals — thread per-session state through `@ReplSession`.** |
| 7 | Engine home | **Extract to `pkg/binate/repl`** (tier-2, per `pkg-layout-spec.md`). The embeddable library *is* the deliverable. |
| 8 | Wasm scope | **I/O refactor only.** Running the type-checker + IR-gen + VM under wasm32 is a separate prerequisite for B1 — see Out of Scope. |

---

## Recommended API

Lives in new tier-2 package `pkg/binate/repl` (greenfield). Built by
`bnc`, full language. **Transitive-tier-2 constraint**: the engine must
not import the native extern bindings (`registerStandardExterns` etc.,
which are NATIVE-ONLY) — they are injected by the host via a callback,
keeping `pkg/binate/repl`'s dependency closure tier-2-clean.

```binate
// ── I/O sink: a struct of function-values, NOT an interface. One impl
//    per host, constructed + invoked entirely host-side (never crosses
//    the VM/compiled boundary, so it is Phase-3-safe). No ReadLine:
//    input is PUSH — the host owns the read and hands the line to Step.
struct ReplIO {
  WriteOut    @func(bytes *[]const uint8) int  // fd-1: user stdout + prompts/banner
  WriteErr    @func(bytes *[]const uint8) int  // fd-2: diagnostics / parse+check errors
  WriteResult @func(bytes *[]const uint8) int  // eval result-echo (host frames distinctly)
}

// ── Session: owns everything that is for{}-loop-local today PLUS the
//    currently-process-global import/init state.
struct ReplSession {
  // Heavy, constructed once (repl.bn:81 / :102 / :52):
  Vm      @vm.VM
  MainMod @ir.Module
  Chk     @types.Checker
  // Per-turn-persistent (reset at turn completion, repl.bn:125/126):
  Counter     int
  Accumulated @[]uint8
  // Lifted out of the repl_import.bn globals (24-41) + repl_decl.bn:411:
  Ldr           @loader.Loader
  Root          @[]char
  BniPaths      @[]@[]char
  ProcessedPkgs @[]@[]char
  InitCounter   int
  // Host I/O sink (stored once):
  Io ReplIO
  // INTERRUPT SEAM (reserved in v1; nil/no-op until suspend lands):
  Poll @func(@vm.VM) int   // POLL_CONTINUE | POLL_BREAK | POLL_SUSPEND
}

// ── Step statuses. Reserve the interrupt variants NOW even though v1
//    never returns Suspended/Broke, so the contract is forward-compatible.
enum StepStatus { NeedMore, Evaluated, EofClean, EofUnbalanced, Suspended, Broke }

// ── StepResult carries the per-turn OUTCOME plus the data the host
//    needs to render the NEXT prompt. The engine does NOT bake prompt
//    strings (on wasm the "prompt" is a UI state, not "> ") — it
//    exposes the data; the host renders. Eval/init OUTPUT is already
//    flushed through s.Io.*; StepResult holds only control + prompt
//    metadata, never the output payload.
struct StepResult {
  Status  StepStatus
  // Prompt-rendering hints (read by the host before the next read):
  Counter int   // In[n] index for the next input (== current turn's n while NeedMore)
  Depth   int   // open-bracket continuation depth; 0 ⇒ primary prompt, >0 ⇒ continuation
  // (future) result-type / pretty-printed summary for a rich prompt —
  // deferred to the pretty-printer (pkg/replprint), see plan-repl.md.
}
// Host convenience: Continuation := Depth > 0.

const POLL_CONTINUE int = 0
const POLL_BREAK    int = 1
const POLL_SUSPEND  int = 2

// ── Constructor: parse → load imports → CheckMainPersistent →
//    NewVM + externs → lower-all-deps → lower-main → initReplImportState
//    (= repl.bn:32-113 minus CLI-arg handling). Returns an error VALUE
//    instead of bootstrap.Exit. registerExterns is host-injected so the
//    NATIVE-ONLY libc/bootstrap bindings stay out of the library; the
//    constructor then rebinds Write/Read/Exit over the Io sink (cat. B).
func NewReplSession(
      root @[]char, sourceFiles @[]@ast.File, bniPaths @[]@[]char,
      implPaths @[]@[]char, io ReplIO,
      registerExterns @func(@vm.VM)
) (@ReplSession, @[]ReplError)              // errs empty/nil on success

// ── Init: run any PRE-PROMPT initialization (package init / top-level
//    initializers / banner hook) BEFORE the first prompt, flushing
//    output through s.Io.*. Kept separate from the constructor so
//    construction stays pure (no user-code execution; setup errors come
//    back from NewReplSession as VALUES) while Init is where user code
//    first runs — so it returns a StepResult (runtime init errors via
//    WriteErr; Suspended/Broke once the seam is live) and the prompt
//    data (Counter=0, Depth=0) for the first prompt. The host calls
//    Init exactly once, renders the result, then enters the Step loop.
func (s @ReplSession) Init() StepResult       // Status=Evaluated when ready for prompt 1

// ── Step: ONE host-pushed line (the push inversion of the for{} loop).
//    Append → computeOpenDepth; depth>0 → result {NeedMore, Depth=depth}
//    (host shows a continuation prompt); depth==0 → evalReplLine(...),
//    reset accumulated, bump Counter, result {Evaluated, Counter, Depth=0}.
//    EOF mid-unbalanced → EofUnbalanced (host chooses discard-vs-error);
//    EOF clean → EofClean. The returned StepResult's Counter/Depth tell
//    the host what to render before the next read.
func (s @ReplSession) Step(line *[]const uint8, eof bool) StepResult

// ── Reserved for the suspend stage (declared but trivial in v1):
func (s @ReplSession) Resume() StepResult  // v1: returns Evaluated immediately
```

**Lifecycle**: `NewReplSession` (pure construction; setup errors as
values) → `Init()` (run pre-prompt init, render its result) →
`for { render-prompt-from-last-result; read; Step }` →
(`Resume()` reserved for the future suspend stage). `Init` and `Step`
share the `StepResult` shape so the host's prompt-rendering is uniform.

CLI host (`cmd/bni`) shrinks to a thin shell:

```binate
io := ReplIO{
  WriteOut:    bytes => bootstrap.Write(1, bytes),
  WriteErr:    bytes => bootstrap.Write(2, bytes),
  WriteResult: bytes => bootstrap.Write(1, bytes),
}
s, errs := NewReplSession(root, files, bniPaths, implPaths, io,
  func(vm) { registerStandardExterns(vm); registerPureCExterns(vm) })
// ... report errs ...
var r StepResult = s.Init()        // pre-prompt init; output via the sink
for {
  if r.Status == EofClean { return }
  io.WriteOut(renderPrompt(r))     // host renders from r.Depth / r.Counter
  line, eof := readReplLine()      // host's own pull
  r = s.Step(line, eof)
}
// renderPrompt: r.Depth > 0 ⇒ continuation ("... " / indent by Depth);
// else primary ("> " or "In[r.Counter]: ").
```

wasm worker: **no loop**. `io.*` lower to `host_post_message`; the
`onmessage` handler calls `s.Step(msgBytes, false)` and posts the
returned `Depth`/`Counter` so the UI can update its prompt. `Init` runs
once at worker startup, before the first prompt is shown. A future
inbound interrupt message just sets the flag that `s.Poll` reads (once
the suspend stage lands).

---

## Staged plan

Each stage is independently landable and keeps everything green
(per the "stay close to main" cadence). Stages 1–5 are **v1**.
Stages 6–7 are **future**, gated as noted.

### Stage 1 — session struct + re-entrancy
Introduce `@ReplSession` owning the five `for{}`-loop locals
(`vmInst`/`mainMod`/`c`/`replCounter`/`accumulated`) **plus the lifted
globals**: `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
(`repl_import.bn:24-41`) and `replInitCounter` (`repl_decl.bn:411`).
Make `evalReplLine` + `evalReplStmtList` + `evalReplDecl` +
`evalReplImport` + `retryPending` + `runReplVarInit` +
`announce*`/`parkedDeclLabel`/`printRedefName` methods on (or takers of)
`@ReplSession`. Keep `cmd/bni`'s `for{}` loop calling the new shape;
keep `print`/`println` as-is for now.
- **Deliverable**: re-entrant engine; the cmd/bni-local globals gone.
  Unit tests (`setupReplState` mirrors the constructor) stay green.
- **Note**: the `ir` process-globals (`currentChecker`, alias map) are
  *not* touched here — single-session keeps them. Tracked separately.

### Stage 2 — `NewReplSession` constructor
Factor the constructor body (`repl.bn:32-113`) out of `runRepl`'s head,
returning `@[]ReplError` instead of `bootstrap.Exit` at the three
setup-error sites (`repl.bn:25/47/71`). Leave a thin CLI shell doing
`expandDirArgs` + `resolveRoot`/`primaryRoot` + `applyPathFlags` +
path-flag extraction from `CLIArgs` (`repl.bn:22-40`), then calling the
constructor. Keep `CheckMainPersistent` (`repl.bn:65`) — the one
REPL-specific setup divergence (leaves main's scope installed on
`c.Scope` for prompt entries).
- **Deliverable**: constructor returns errors, no process-exit on setup
  failure. CLI behavior unchanged. Green.

### Stage 3 — `ReplIO` sink for the REPL's OWN framing output (cat. A only) — LANDED
**Scope (decided 2026-06-02):** Stage 3 covers **only category-A** —
the REPL engine's own framing output. Category-B (output produced by
EVALUATED user code, e.g. a typed `println`) is explicitly **out of
scope** and is NOT done via extern rebinding. The extern-table machinery
is being reworked anyway; the correct long-term answer is **injecting
appropriate package implementations** so user code's `Write`/`Read` go
where the host wants. Do not rebind externs for this.

**What landed (binate `7dcd1079`):** `ReplIO{WriteOut, WriteErr}` (a
struct of `@func(*[]const char) int` channels) on `@ReplSession`;
`NewReplSession` takes it as a param. Sink helper methods
`out`/`outln`/`err`/`errln` replace every `print`/`println` in
`repl.bn` / `repl_decl.bn` / `repl_import.bn`; `announceParked` /
`announcePendingCycle` / `printRedefName` became `@ReplSession` methods
so they reach the sink. The CLI host's `cliReplIO` wires both channels
to the `print` builtin, so all framing output still lands on fd 1
(byte-for-byte unchanged) — a richer host can split `WriteOut` /
`WriteErr`. `WriteResult` deferred until result-echo actually lands (no
result echo today). Implementation notes: func *types* in struct fields
use unnamed params (`@func(*[]const char) int`); closure literals get
their `@func` flavour from a typed-var hint (a bare `field = func(){}`
assignment isn't a hint site).
- **Deliverable (met):** per-engine framing-output redirection.
  `TestReplFramingRoutesThroughSink` drives a diagnostic through a
  capturing-closure sink and asserts it lands on `WriteErr`; e2e/repl.sh
  53/53 confirms the CLI's framing output is byte-for-byte unchanged
  through the sink; hygiene 12/12.

### Stage 4 — invert the loop to `Step`/`Init` (push) + extract `pkg/binate/repl`
Replace the `for{}` loop with host-driven
`Step(line, eof) → StepResult` (append / `computeOpenDepth` / eval /
reset) plus `Init() → StepResult` for pre-prompt initialization. Give
`StepResult` its prompt-data fields (`Counter`, `Depth`) so the host
renders the prompt from the result rather than the engine baking prompt
strings. `cmd/bni` becomes: build `ReplIO` over fds, build the
`registerExterns` callback, `NewReplSession`, `Init`, then a trivial
`for { renderPrompt(r); readReplLine; r = Step; check EofClean }`.
**Physically move** the engine + pure continuation logic
(`computeOpenDepth`,
`appendByteRepl`) + construction helpers (`registerPkgImports` subtree,
`loadBuiltinBNIs`, `readFile`, `streq`, `containsPath`, `quotePath`) +
their tests into `pkg/binate/repl`; wire build-via-bnc. `readReplLine`
+ `CLIArgs` handling + the externs impl stay in `cmd/bni`.
- **Deliverable**: embeddable, coroutine-ish, push-driven REPL in
  `pkg/binate/repl`; `cmd/bni` is a thin host. Existing `repl_*_test.bn`
  move with the engine and stay green. **This completes the v1 I/O-shape
  unblock.**
- **Tier check**: confirm `pkg/binate/repl`'s dependency closure is
  tier-2-clean (the native externs must remain host-injected, not
  imported).

### Stage 5 — interrupt seam (inert plumbing) — *seam-only per decision #5*
Thread a `vm.Status` side-field + `StepStatus` through
`execLoop → execFunc → CallFunc/CallByVMFunc → Step`, all returning
`Continue` today. Add the `vm.Poll @func` field + a hook call site at
`BC_SP_RESTORE` and `BC_JUMP`/`BC_BRANCH` back-edges, with **nil-poll =
always-Continue (zero overhead)**. Add `vm.ResumePC`/`ResumeFuncIdx`/
`ResumeRegsOff` side-fields and a `Resume()` that is a no-op in v1.
No behavior change; everything stays green.
- **Deliverable**: the forward-compatible seam — future suspend/break
  need **no** second invasive return-contract refactor. Pure plumbing.
- **Perf guard**: verify the nil-poll fast path doesn't regress the
  conformance `int-int` runtime (the host-stack-leak comment at
  `vm_exec.bn:24-36` shows this loop is performance-sensitive).

### Stage 6 — continuable suspend (FUTURE — the easier interrupt)
Implement `POLL_SUSPEND` at the **outermost** `execLoop`: spill the
active-frame `pc`/`funcIdx`/`regsOff` into the `vm.Resume*` side-fields,
return `SUSPENDED` up the call chain, add a `ResumeLoop` that **reloads**
from the saved fields instead of a fresh `pushFrame`. Host contract:
`vm.Stack`/`vm.SP` are **FROZEN** while a suspension is outstanding
(`regs`/`frameBase` are raw pointers into the stack). `StepStatus.Suspended`
+ `Resume()` go live. Conformance tests for top-level suspend/resume.
- **Gated on** [`plan-bni-heap-frames.md`](plan-bni-heap-frames.md) for
  the mid-callback case: suspend is sound **only** at the outermost
  `execLoop` with no native callback on the host stack. The common REPL
  case (top-level pure-VM loop, e.g. a typed `for{}`) is **not** gated;
  poll points *under* an extern must only set a deferred flag, never
  actually suspend, until the `execExtern → callback → CallFunc →
  execFunc → execLoop` recursion is trampolined.
- **Invariant to design before claiming general suspend**: the active
  frame has no own-pc header slot, so the `vm.Resume*` side-fields
  support exactly **one** outstanding suspension. Nested/multiple
  suspensions need a per-frame pad slot (a `FRAME_HDR` layout change).

### Stage 7 — break (FUTURE — abort + run all cleanup, NO leak)
New **IR-gen support**: emit a per-open-scope **cleanup landing pad**
pc the VM can branch to that runs exactly the RefDec/scope-exit code for
currently-open scopes. New **VM unwind mode**: from the innermost frame
outward, set pc to that frame's cleanup pad, run it to completion, then
pop via normal `BC_RETURN` (reusing the `freeOnPop`/`BC_SP_RESTORE`
machinery at `vm_exec.bn:124-168`). `POLL_BREAK` triggers it;
`StepStatus.Broke` returned with `accumulated` reset and control at the
prompt.
- **A naive frame-discard break is FORBIDDEN** — it leaks managed
  allocations (cleanup is inline bytecode; strict no-leak rule).
- **Gated on** Stage 6 (suspend infra) AND on heap-frames for the
  mid-callback case (can't cooperatively unwind through a live native
  callback frame; any in-flight extern must run to completion first).
- The IR-gen landing-pad work is the **long pole** and must be sized
  with the user before starting — do not begin it as an implied
  follow-up.

---

## Out of scope (separate prerequisites — raised, not silently deferred)

- **VM-on-wasm for B1.** This refactor fixes the I/O *shape*. A
  REPL-in-browser also needs the type-checker + IR-gen + the bytecode
  VM compiled to wasm32 and runnable in-worker; the wasm plan currently
  compiles Binate→wasm and does not account for running the VM in-worker.
  This refactor is **necessary but not sufficient** for B1. Whether B1
  requires `pkg/vm`-on-wasm (vs some compiled-eval alternative) is its
  own open scope question for `plan-wasm-browser.md`.
- **Multi-session embedding.** Blocked by `ir` process-globals
  (`currentChecker` at `gen.bn:148`; alias map `importAliasNames`/
  `importAliasPaths` at `gen.bn:107/110`, with `Save`/`RestoreAliasMapState`
  bracketing in `evalReplImport` at `repl_import.bn:101/146`). Single
  re-entrant session is unaffected. Tracked in `claude-todo.md`
  ("REPL: remove process-global session state"). **Until then: do not
  add new REPL globals — thread per-session state through `@ReplSession`.**

---

## Open risks

- **Missed category-A site.** A stray `print`/`println`/`Write(1,…)`
  left in the REPL path after Stage 3 bypasses `ReplIO` and writes
  straight to the host's real stdout (on wasm, escaping the message port
  entirely). *Mitigation*: enumerate-and-grep the listed sites + a test
  asserting no output reaches fd 1 except via the sink.
- **Sink-closure lifetime.** The capturing closure registered as the
  `Write` extern must keep the captured sink alive for the whole
  session. `RegisterExtern` builds a managed `HandleAddr` copy
  (`vm.bn:274-280`) that owns the function-value handle — confirm it
  keeps the closed-over pointer alive (not just the transient registrant
  frame). *Verify*: a session that prints after many turns.
- **Alias-map save/restore must survive the move.** The
  `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
  (`repl_import.bn:101/146`) is load-bearing — a dropped save/restore
  corrupts the alias map across import turns. Carry it intact into
  `pkg/binate/repl`.
- **`js.import` extern reachability on wasm (UNVERIFIED).**
  `host_post_message` is a `#[js.import]` extern with no body. Confirm
  the VM extern table (`RegisterExtern` reading `fv[0]`/`fv[1]`) reaches
  `js.import` externs the same way it reaches C externs — if they are
  compiled-mode-only and bypass the VM table, the sink-backed `Write`
  swap only redirects when user code runs *compiled*, not in the VM.
  Verify before relying on the same swap on wasm.
- **Stage 5 plumbing touches hot paths.** Even side-field-first, it adds
  branches the all-VM fast path didn't have. The nil-poll zero-overhead
  path must be verified to not regress the `int-int` modes.
- **EOF/Ctrl-D policy.** Today EOF mid-unbalanced silently discards
  `accumulated` (`repl.bn:136`). The `Step` contract exposes
  `EofUnbalanced` so the **host** chooses discard-vs-error — verify every
  host (CLI + wasm + tests) handles all four non-interrupt `StepStatus`
  values, or a forgotten case silently drops input.
- **Cross-mode function-value dispatch is DRAFT**
  (`plan-function-values-phase-3`): a VM-constructed function value
  invoked by compiled host code (or vice versa) null-derefs on
  `vtable.call`. This is a **design invariant**, not a fixable risk here:
  keep `ReplIO` constructed+invoked entirely host-side, and user-code
  redirection entirely in the VM extern table; never let either cross.
