# Plan: `rt.Abort()` / `rt.Panic(msg)` + simplify `panic()`, unify internal aborts

Status: **Plan 1 DONE & LANDED** (2026-06-20) — `rt.Abort`/`rt.Panic` (`6718d41f`),
`panic()` single-string + lowering + arity (`ccbb5e04`, `fa70f788`), VM
internal-abort migration through `panic()` (`e824f6dd`). See claude-todo-done.md.
**Plan 2** (recoverable VM user-faults) is the **scope-required follow-up** (see
the end). One deviation from the wording below: the VM migration uses the
`panic()` builtin via `vmPanic`/`vmPanicName` helpers (not direct `rt.Panic`),
per the user — same output sink, one less concept at the call sites.

**Decisions (settled 2026-06-20):** `rt.Abort()` = C `abort()`; prefix `panic:`
(lowercase); **stderr deferred** — Plan 1 keeps panic/diagnostics on **stdout**
(unchanged), routing them to stderr is its own follow-up (see § stderr).

## Motivation

Process termination is currently a grab-bag of three idioms for the same intent:

- **codegen / native backends** use the builtin `panic("…")`
  (`emit_iface_upcast.bn`, `x64_dispatch.bn`, `aarch64_dispatch.bn`,
  `x64_regmap.bn`, …) — including the *exact* conditions the VM handles
  differently.
- **the bytecode VM** uses `println("vm: …") + rt.Exit(1)` (≈45 internal sites
  in `pkg/binate/vm`).
- **the rt fault handlers** (`BoundsFail` / `DivFail` / `ShiftFail`) use
  `print("runtime error: …") + Exit(1)`.

There is no canonical "terminate, this should never happen" primitive, and the
builtin `panic()` drags in machinery that should die (below).

### Findings that shape the plan

- **`panic()`'s variadic formatting is unused.** 11 `panic(...)` sites in real
  code, *every one a single string literal*. The variadic / computed-arg /
  multi-arg path is never exercised → can be cut to a single string at zero
  migration cost.
- **`panic()`/`print()`/`println()` hang off `pkg/bootstrap` formatters**
  (`bootstrap.Write`, `formatInt` / `formatInt64` / `formatUint` / `formatBool`
  / `formatFloat`, `bootstrap.Exit` — via `gen_print.bn`). This is the
  "semi-internal stuff" slated for removal. Killing `panic()`'s variadic-ness
  removes the panic path's dependency on the *formatters* entirely (it becomes
  just a raw write + terminate). `print`/`println(nonString)` still need the
  formatters — that broader cleanup (the `bootstrap.println`-hack removal) is a
  separate, larger effort; this is a self-contained down payment on it.
- **stderr is reachable today.** `bootstrap.Write(fd, buf)` takes an fd and
  `bootstrap.STDERR = 2` exists; the print/panic path just hardcodes stdout.
- **`panic()` lowers (today, `genPanicCall`) to** `print "panic: " + args + "\n"`
  (stdout) → `bootstrap.Exit(1)` → `OP_UNREACHABLE`. It routes through
  `bootstrap.Exit`, not `rt.Exit` (two distinct `Exit`s exist).

## Plan 1 — primitives + simplify `panic()` + migrate VM internal aborts

This covers **internal, "this should never happen" aborts only** — VM/IR/compiler
invariant violations that are genuinely unrecoverable. User-code runtime faults
(bounds / divide / shift / nil-deref / stack-overflow / call-through-nil) are
**out of scope** — they belong to Plan 2.

### `rt.Abort()`
- No args. The bare unrecoverable terminate. **= C `abort()`** (SIGABRT → core
  dump + debugger break — best for shouldn't-happen post-mortem).
- Lives in `pkg/builtins/rt` (per-target impl, like `rt.Exit`: hosted →
  `__c_call("abort", "void")`; baremetal → semihost exit with a nonzero code /
  trap).

### `rt.Panic(msg *[]readonly char)`
- Writes `"panic: " + msg + "\n"` to **stdout** (stderr deferred — see § stderr),
  then `rt.Abort()`.
- **Single string. No variadic formatting → no `bootstrap.format*` dependency**
  (string writes go straight to `bootstrap.Write`; only int/float/bool args would
  pull in the formatters, and `rt.Panic` takes none).

### builtin `panic(msg)`
- Reduced to a **single string** argument (variadic dropped — unused).
- New lowering (`gen_print.bn` `genPanicCall`): `call rt.Panic(msg)` +
  `OP_UNREACHABLE` (was: inline `print` + `bootstrap.Exit(1)`).
- **Stays a builtin** purely for its noreturn (`OP_UNREACHABLE`) semantics, which
  a plain `rt.Panic` call doesn't give the checker. `panic(msg)` is now exactly
  sugar for "`rt.Panic(msg)` and the rest is unreachable".

### Migrate the VM's internal-invariant aborts
- `println("vm: …") + rt.Exit(1)` → `rt.Panic("vm: …")` for the
  internal-invariant sites: nil/ malformed vtables and function values,
  out-of-range slot / method indices, `extern not found`, `no shim vtable`,
  unsupported feature (`>6 user arg slots`), unknown opcode, trampoline/closure
  corruption, `RegisterExtern` errors, the `iface_upcast` negative-offset guard,
  etc. These are unrecoverable by construction (the VM was handed invalid
  IR/bytecode or is itself broken), so a hard abort is correct.
- Dynamic-message sites (`"vm: extern not found: " + name`): pre-build the
  message (`strconcat`-style) and pass the single string to `rt.Panic`, or
  simplify the message. (Allocation on the abort path is fine.)
- **Do NOT migrate** the user-fault sites — `BC_NIL_CHECK` ("nil pointer
  dereference"), stack overflow, and call-through-nil-{function value, interface
  value, function pointer}. These move to Plan 2 with bounds/div/shift.
- codegen / native `panic("…")` sites need no change — they already route
  through the builtin, which now flows through `rt.Panic`.

### stderr — DEFERRED (follow-up)
Routing panic / `runtime error:` / VM diagnostics to **STDERR (fd 2)** is its own
follow-up, kept out of Plan 1 to reduce scope. Plan 1 leaves all of it on stdout
(unchanged). When done it's a real behavior change — anything scraping these off
*stdout* (test harnesses, conformance `.expected` capture) shifts to stderr, so
those consumers need updating in lockstep. Infra already exists
(`bootstrap.Write(fd)`, `bootstrap.STDERR = 2`).

### Implementation order (each step green)
1. Add `rt.Abort()` + `rt.Panic(msg)` to `pkg/builtins/rt` (all target variants:
   `rt.bn`, `rt_baremetal.bn`) + `rt.bni`. Unit-test that both link and
   terminate. **BUILDER note:** these must exist at link time before any
   `panic()` lowering emits a call to them; verify the current BUILDER tolerates
   the `rt.bni` additions (new exported funcs) when it compiles gen1.
2. Re-point `genPanicCall` to `call rt.Panic(msg)` + `OP_UNREACHABLE`; drop the
   variadic arg handling (checker: `panic` becomes arity-1, string arg). Keep
   the `OP_UNREACHABLE`. Update `gen_print.bn` + the `panic` scope/checker
   special-casing. **This file is in cmd/bnc's BUILDER tree — keep it
   BUILDER-compilable.**
3. Migrate the VM internal-invariant `rt.Exit(1)` sites to `rt.Panic`.
4. Conformance: a `panic("msg")` test asserting `panic: msg` on stderr + nonzero
   exit; confirm the codegen/native panic sites still behave (gen1/gen2 + native
   modes green).

---

## Plan 2 — user-code runtime fault recoverability (SCOPE REQUIRED — follow-up)

**Not in Plan 1.** Needs its own scoping/design pass before implementation.

### The problem
The rt fault handlers and core runtime are **shared by both backends** — codegen
emits `call @rt__BoundsCheck/DivCheck/ShiftCheck/RefInc/Box/…` and the VM calls
the *same* `rt.*` from its exec loop. So when the VM runs user bytecode that
indexes out of bounds / divides by zero / shifts negative / derefs nil,
`BC_*_CHECK → rt.*Check → *Fail → Exit(1)` **terminates the whole VM host
process**. Fine for `cmd/bni` running one program; wrong for an embedded VM /
REPL / test-runner that should survive a bad user program, report it, and
continue.

These are **user-code faults** (the interpreted *program* is wrong), distinct
from Plan 1's internal-invariant aborts (the VM/compiler is broken). They want
opposite handling: compiled → terminate; interp → recoverable.

### Approach (per the user — simpler than a source split)
rt is **injected into the VM** (`RegisterStandardExterns` →
`RegisterPackageFunctions(vmInst, rt.__Package())`), exactly like the stdlib. So
user-bytecode `rt.*` already dispatches to the *injected* rt. A user div-by-zero
runs `rt.DivCheck → rt.Panic/Abort`, which dispatches to **whatever the VM
injected**. So the VM can inject a **VM-specific** `rt.Abort`/`rt.Panic` — no
physical `rt` source split needed; it's an injection override. The faulting user
op behaves "as if the user code called `rt.Panic`/`rt.Abort` itself."

### Ratified design (2026-07-16): unified cleanup-pad unwind

A recon pass settled the "open question", and the answer **unifies Plan 2 with
the REPL's Stage-7 "break"** (`plan-repl-embeddable.md`):

- **The unwind IS over data frames** (confirmed): `execLoop` (`vm_exec.bn:20`)
  is iterative — `BC_CALL` pushes frames on `vm.Stack`, no host recursion. So a
  fault deep in a user call is unwound by popping data frames back to `execFunc`
  → `CallFunc`; no `setjmp`/`longjmp` (C-free).
- **Naive frame-discard LEAKS** (the trickiest point, now pinned): RefDec is
  emitted as **inline `BC_REFDEC` bytecode at specific PCs**; the VM keeps no
  runtime enumeration of a frame's live managed values, and `BC_RETURN` runs
  only the single `freeOnPop` slot — NOT scope cleanup. Skipping-to-return
  abandons the RefDec opcodes ⇒ leak, which violates the strict never-leak rule.
  So a leak-accepting unwind is not an option.
- **The leak-free answer already has a design: cleanup pads.**
  `plan-repl-embeddable.md` **Stage 7 (break)** specifies exactly the machinery
  Plan 2 needs — per-open-scope **cleanup landing pads** (a PC that runs the
  RefDec/scope-exit code for currently-open scopes) plus a **VM unwind mode**
  (innermost frame outward: jump to the frame's pad, run it, pop via the
  existing `BC_RETURN`/`freeOnPop`/`BC_SP_RESTORE` path, repeat to the top
  frame). **A fault is an internally-triggered break**; `POLL_BREAK` (Ctrl-C) is
  the external trigger. Same cleanup-pad + unwind-mode, different trigger. Plan 2
  does NOT need Stage 6's suspend/resume (a fault aborts to the prompt; it does
  not pause).

**Chosen approach (user, 2026-07-16): build the shared cleanup-pad + VM
unwind-mode once**, drive it from the fault sites now, and reuse it for Stage-7
break later — over a leak-accepting first cut or a conservative frame-scan.

Refinement from recon (finalized in Inc 3): rather than shadow-injecting a
VM-specific `rt.Abort`/`rt.Panic` into the extern registry, the VM's own guard
sites call an internal `setFault(msg)` directly, and the bounds/div/shift checks
move inline into the VM's `BC_*_CHECK` handlers (they currently delegate to the
injected `rt.BoundsCheck`/`DivCheck`/`ShiftCheck`, which fault internally). Same
effect — the VM observes the fault and unwinds — with one fewer moving part (no
registry shadow, no reliance on a native handler "returning"). The compiled path
is untouched: compiled code calls `rt.*Check` directly and stays fatal.

### Scope boundaries (named, not silently deferred)

- **Outermost-`execLoop` only.** Cooperative unwind is sound only when the VM
  stack does not interleave with a live *native* frame on the host stack. A
  fault under a native callback (`execExtern → native callback → CallFunc → …`)
  cannot unwind through the host-stack frame — that case **stays fatal** until
  heap frames land (the mid-callback gate Stage 6/7 already carry — see
  `plan-repl-embeddable.md`). The top-level REPL / test-runner case (host →
  `CallFunc` → pure VM → fault) is the target and is not gated.
- **Native-extern SIGSEGV is a separate concern.** A wild-pointer deref *inside*
  a native extern called from the VM (e.g. handing a bad pointer to
  `rt.Refcount`) raises an OS signal that cooperative data-frame unwind cannot
  catch; it needs a host signal handler. Out of Plan 2; tracked separately (the
  2026-06-30 robustness note in `claude-todo.md`).
- All 6 fault kinds are in scope. Stack-overflow is safely recoverable — its
  guard (`pushFrame`, `vm.bn:259`) fires *before* `vm.SP` advances.

### Increment plan

Each increment stays green and lands small; the IR-gen pad design (2a) gets its
own detailed design + adversarial review before code.

- **Inc 1 — fault carrier + REPL surface (host-facing contract).** `@VM`:
  `VM_STATUS_FAULTED` + `FaultMsg @[]char`, reset in `CallFunc`/`CallByVMFunc`.
  `repl.Execute` maps a faulted VM status onto **`EXEC_ERROR`** (chosen over a
  new `EXEC_FAULTED`: a faulting turn is a failed turn like a compile error —
  fewer driver-facing concepts) plus a `Diagnostic` carrying `FaultMsg`. Inert
  (nothing sets `FAULTED` yet). The `cmd/bni` `runProgram` + test-runner
  fault-read wiring lands with Inc 3, where faults actually fire and it is
  end-to-end testable (rather than as unexercised code here).
- **Inc 2a — IR-gen cleanup landing pads (the long pole).** Emit per-open-scope
  cleanup pads + a per-frame "current unwind PC" the compiler maintains at
  scope/statement boundaries (reusing the scope-exit RefDec code IR-gen already
  emits), including in-flight mid-statement temps. Standalone design +
  adversarial review first.
- **Inc 2b — VM unwind mode.** On `FAULTED`, `execLoop` runs each frame's pad
  then pops to the top frame → returns a sentinel; `execFunc`/`CallFunc` surface
  the fault. Leak-free because 2a's pads run. Conformance per managed-state shape
  asserting host-survives AND refcount balance.
- **Inc 3 — wire the 8 guard sites + non-REPL hosts.** Convert bounds/div/shift +
  nil-deref + stack-overflow + 3× call-through-nil from `println + rt.Exit(1)`
  to `setFault(...)` + enter unwind, gated to the outermost `execLoop`. Wire
  `cmd/bni` `runProgram` + the test-runner (count a fault as a failed test, then
  continue). Conformance per fault kind.
- **Inc 4 (later — not Plan 2).** `POLL_BREAK` drives the same unwind (Stage-7
  break) for free.
