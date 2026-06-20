# Plan: `rt.Abort()` / `rt.Panic(msg)` + simplify `panic()`, unify internal aborts

Status: **PLAN (2026-06-20)** — Plan 1 below is ready to implement pending the
three decisions in [§ Decisions](#decisions-for-the-user). Plan 2 is a
**scope-required follow-up** (see the end).

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
- No args. The bare unrecoverable terminate.
- **DECISION:** C `abort()` (SIGABRT → core dump + debugger break — best for
  shouldn't-happen post-mortem) **vs** `exit(1)` (clean, no core dump). Default
  recommendation: `abort()`.
- Lives in `pkg/builtins/rt` (per-target impl, like `rt.Exit`: hosted →
  `__c_call("abort")` or `__c_call("exit", 1)`; baremetal → semihost exit).

### `rt.Panic(msg @[]readonly char)`
- Writes `"panic: " + msg + "\n"` to **STDERR** (fd 2), then `rt.Abort()`.
- **Single string. No variadic formatting → no `bootstrap.format*` dependency.**
- Implemented as a raw `Write(STDERR, …)` (via `bootstrap.Write(STDERR, …)` or an
  rt-level write) + `rt.Abort()`.

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

### stderr
- Panic/diagnostic output moves to **STDERR (fd 2)**. **DECISION:** in scope here
  or kept separate? It's a real behavior change — anything currently scraping
  these messages off *stdout* shifts to stderr. Recommendation: do it (it's
  correct), but call it out so test harnesses that capture VM diagnostics are
  updated.

### Decisions for the user
1. **`rt.Abort` = `abort()` (rec.) or `exit(1)`?**
2. **Prefix `"panic: "` (rec., Go-like, matches the existing builtin) or
   `"PANIC: "`?**
3. **stderr for panic/diagnostics: in scope (rec.) or separate?**

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
`RegisterPackageFunctions(vmInst, rt._Package())`), exactly like the stdlib. So
user-bytecode `rt.*` already dispatches to the *injected* rt. A user div-by-zero
runs `rt.DivCheck → rt.Panic/Abort`, which dispatches to **whatever the VM
injected**. So the VM can inject a **VM-specific** `rt.Abort`/`rt.Panic` — no
physical `rt` source split needed; it's an injection override. The faulting user
op behaves "as if the user code called `rt.Panic`/`rt.Abort` itself."

### The open question (the scope-required part)
**What should the VM-injected `rt.Abort`/`rt.Panic` do to terminate JUST the
interpreted program, not the host?** I.e. how does it unwind the VM exec back to
the VM's caller (REPL / test-runner / embedder)?

Candidate (looks feasible, needs confirmation): the **VM call stack is data**
(`vm.Stack`, frames at offsets), not the host stack — so the user program's call
frames are not on the host stack. At fault time the host stack is shallow (exec
loop → `rt.DivCheck` → injected abort). So the injected `rt.Abort` could **set a
VM fault flag + message and return**; the exec loop checks the flag after the
faulting op and **abandons the VM frames** (popping them — running dtors where
required for refcount correctness?) back to the `CallFunc`/exec entry, which
returns a fault result to the host. No `setjmp`/`longjmp` needed (good — C-free),
because the unwind is over *data* frames, not host frames.

Things to pin down when scoping Plan 2:
- Which rt entry points get the VM-injected variant: the user-fault handlers
  (`BoundsFail`/`DivFail`/`ShiftFail` via `*Check`) **plus** the VM's own
  nil-deref / stack-overflow / call-through-nil guards (move them off `rt.Exit`
  onto the same recoverable path).
- The unwind's interaction with **refcounting** — abandoned VM frames may hold
  managed values that must be RefDec'd as the frames are popped (else a fault
  leaks). This is the trickiest correctness point.
- Whether the recoverable fault surfaces to the host as a value (an
  `@errors.Error`-shaped result from `CallFunc`) or a VM state the caller polls.
- The compiled side is unchanged (its `rt.*Check` stays fatal — there is nothing
  to unwind to).
