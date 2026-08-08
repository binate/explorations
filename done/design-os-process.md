# Design: `pkg/std/os/process` — synchronous subprocess execution (replaces `bootstrap.Exec`)

Status: **DESIGN — ratified in discussion 2026-07-17, being implemented.** Replaces
`bootstrap.Exec`. Stdlib API (not core language), so no spec change; this doc is the
authority for the implementer, **subject to the implementation corrections below**.
See `explorations/plan-os-process.md` for the execution plan and commit breakdown.

**Implementation corrections (2026-07-18, from adversarial review of the plan):**
- **§5 "re-home the VM extern"** is obsolete. `os/process` is a normal *injected*
  stdlib package (listed in `stdPkgs()`), so its `__c_call`s run natively under `bni`
  with no extern; the `bootstrap.Exec` VM extern is simply **deleted**, not re-homed.
- **§7 caller list undercounts.** Beyond the 5 `cmd/bnc` files there are 7 internal
  asm/native test harnesses (44 sites), plus `README.md` and ~6 prose comments. Full
  retirement (the ratified scope) migrates all of them.
- **§4.5 "ENOEXEC→Unsupported" is not true today** — `errnoToBase` does not map
  `ENOEXEC`; the implementation adds that arm.
- **§4.3 / footgun #1 "`os.Env()` is an empty stub"** is false on hosted targets
  (the entry glue populates it via `captureEnv`). Inherit-via-`execv` is still
  correct, but because it avoids envp marshalling + snapshot-vs-live staleness.
- **BUILDER gate:** `cmd/bnc` cannot import `os/process` until it ships in a released
  bundle and `BUILDER_VERSION` is bumped (gen1 resolves `cmd/bnc`'s stdlib from the
  frozen bundle). So the `cmd/bnc` migration + `Exec` deletion are a **second phase**,
  gated on that bump; adding `os/process` itself is not gated.

## 1. Goal & scope

A **synchronous, run-to-completion** way to launch a child program and get its result —
the basic/common need (esp. on non-multitasking systems), as opposed to a
start-returns-a-handle-then-wait split. Replaces `bootstrap.Exec(program *[]readonly
char, args *[]@[]char) int`, whose shape is lossy and whose defaults (always PATH-search
via `execvp`, inherit env, `int`/`-1` return) we want to redo.

Hosted-only (fork/exec/wait need a process model); baremetal is present-but-fail-loud
(§4.6). Implemented over syscall shims (`fork`/`execve`/`execv`/`waitpid`) — the
sanctioned C-interop-for-syscalls use, consistent with how the rest of `os` does per-OS
syscalls.

## 2. Package & naming

Lives in **`pkg/std/os/process`** — a subpackage so it (a) isolates the hosted-only
process machinery from the rest of `os` (args/env/files, some of which are
baremetal-relevant), and (b) lets the types shed a prefix: `process.Run`,
`process.Options`, `process.ExitStatus`, `process.LookPath`. (`proc` rejected —
abbreviates against the house style that prefers `Command` over `Cmd`; `exec` rejected —
re-imports the `exec*()` connotation we kept off the function names.)

The primary verb is **`Run`** — accurate (run a child to completion), with none of the
baggage of `Exec` (evokes the image-replacing `exec*()` family), `Spawn`
(`posix_spawn`/async), or `StartProcess` (Go — implies start-then-wait-separately). It
**reserves `Start`** (→ an `@Process` handle) for a future async sibling, so the
synchronous name never has to be reclaimed.

## 3. API

```
package "pkg/std/os/process"

// --- value types (in an UNCONSTRAINED file, shared by the hosted + baremetal variants) ---

// ExitStatus is HOW a finished child terminated. Exactly one of Exited()/Signaled() holds.
// A non-zero exit code is NOT an error (see §4.5). Fields private; the accessors are the API.
type ExitStatus struct { /* exited bool; code int; signal int */ }
func (s *readonly ExitStatus) Exited() bool     // normal termination?
func (s *readonly ExitStatus) Code() int        // exit code 0..255; returns -1 when signaled
func (s *readonly ExitStatus) Signaled() bool   // killed by a signal?
func (s *readonly ExitStatus) Signal() int      // terminating signal number; 0 when exited
func (s *readonly ExitStatus) Success() bool    // Exited() && Code() == 0 (the common check)

// Options configures Run. The ZERO VALUE is the safe default: inherit the environment,
// exact path (no PATH search), argv[0] = program, no extra args. (§4.4 explains why the
// zero value is safe here — no DefaultOptions() ceremony needed.)
type Options struct {
    Args       @[]readonly @[]readonly char  // argv[1..] ONLY — does NOT include argv[0]
    Argv0      *[]readonly char              // argv[0]; empty => defaults to `program`
    Env        @[]readonly @[]readonly char  // child environment ("NAME=value"); see ReplaceEnv
    ReplaceEnv bool                          // force replace even when Env is empty (=> empty child env)
    SearchPath bool                          // resolve `program` along PATH (else exact path)
}
// Child environment = (ReplaceEnv || Env is non-empty) ? Env : the inherited real environ.

// --- hosted entry points (#[build(!is(os,"baremetal"))]) ---

// Run runs `program` to completion and reports how it ended. The trailing error is present
// ONLY when the child could not be STARTED (§4.5). On a started child, err is absent and
// ExitStatus carries everything — a non-zero exit AND a signal death are both (status, no-err).
func Run(program *[]readonly char, opts *readonly Options) (ExitStatus, @errors.Error)

// RunArgs — 90%-case convenience: exact path, inherited env, argv[0]=program, argv[1..]=args.
// The drop-in for today's bootstrap.Exec SHAPE (program + args-after-argv0), variadic.
func RunArgs(program *[]readonly char, args ...*[]readonly char) (ExitStatus, @errors.Error)

// RunArgsPath — like RunArgs but resolves `program` along PATH (the searched convenience).
func RunArgsPath(program *[]readonly char, args ...*[]readonly char) (ExitStatus, @errors.Error)

// LookPath resolves `program` along PATH to a concrete executable path, or errors.NotFound.
// Used internally by the search path (resolve in the PARENT, §6) and exposed because callers
// often want resolve-without-run.
func LookPath(program *[]readonly char) (@[]char, @errors.Error)
```

`RunArgs`/`RunArgsPath` are thin wrappers over the same internal runner as `Run` (they
just supply a zero `Options` with `Args`=the variadic and `SearchPath` false/true). The
variadic element type is `*[]readonly char` (a borrow — each arg is copied to a C string
immediately, so no ownership is taken); string literals and `@[]readonly char` both
convert to it (`conv.assignable` cases 8 and 5), so `RunArgs("clang", "-c", x)` works.

## 4. Design decisions

### 4.1 argv[0] is a separate `Argv0` option, NOT folded into `Args`
`Args` is **argv[1..]**; argv[0] defaults to `program` and is overridable via `Argv0`.
Folding argv[0] into the args slice (Go's `Cmd.Args` convention) is a **silent
shift-by-one footgun**: forget to prepend the program name and every argument slides
down one — the child reads its first real arg as argv[0], nothing errors, it just
misbehaves. Keeping `Args = argv[1..]` makes the common case safe and preserves
`bootstrap.Exec`'s exact semantics (it auto-sets argv[0]=program); `Argv0` recovers 100%
of the genuine capability (login shells' leading `-`, busybox multi-call dispatch,
argv[0] spoofing) with zero risk.

### 4.2 Path search: OFF by default, opt-in, portable without `execvpe`
Default is exact-path `execve`/`execv` — no search. Search is opt-in (`SearchPath` /
`RunArgsPath`). **This diverges from `bootstrap.Exec` (which uses `execvp` and always
searches)** — a behavior change the caller sweep must handle (§7). `execvpe` (search +
explicit env in one call) does **not** exist on macOS/BSD, so search is a **manual PATH
walk over `execve`**: if `program` contains `/`, skip search; else read PATH, split on
`:`, and try `dir + "/" + program` per entry, looping while errno is ENOENT/ENOTDIR
(remember an EACCES to report if the walk exhausts). This routes search through `execve`,
so an explicit env always composes — on both platforms. The walk **resolves in the
parent** via `LookPath` (§6), so a not-found is a clean typed error before any fork.

### 4.3 Environment: inherit by default — via `execv`, NOT `os.Env()`
Default is **inherit**, but the critical implementation point: **inherit is `execv`
(no envp — the child keeps the real inherited `environ`), NOT "pass `os.Env()` as
envp".** `os.Env()` is a **stub returning empty today**; naively building an envp from it
would `execve` an empty environment and **silently clear the child's PATH/HOME/etc.**
Using `execv` for inherit is correct *now* despite the stub, and forward-compatible
(when `os.Env()` gains a real data source, `Env = os.Env()` also works with no API
change). Only the explicit-env path builds a `char**` envp and calls `execve`.

### 4.4 Env representation: zero-value-safe (refines the discussed `Inherit bool`)
Binate slices can't be nil, so Go's `Cmd.Env == nil`-means-inherit is impossible. Rather
than an `Inherit bool` (whose *safe* state is the non-zero `true`, so a bare `Options{…}`
literal defaults to an empty env — a footgun that needs a `DefaultOptions()` to paper
over), the model is **zero-value-safe**:
- child env = **inherit** when `Env` is empty and `!ReplaceEnv` (the zero value → the
  common case, bulletproof);
- **replace with `Env`** when `Env` is non-empty (no flag needed — setting `Env` is
  self-evidently intent to use it);
- **empty child env** when `ReplaceEnv` and `Env` empty (the rare isolation case, an
  explicit opt-in).

So `Options{}` = inherit; `Options{Env: e}` = replace with `e`; `Options{ReplaceEnv:
true}` = empty. No `DefaultOptions()` needed. This kills the "forgot the flag → wrong
env" footgun for both common directions. (Alternative if you prefer maximal
explicitness: `Inherit bool` + `DefaultOptions()` — same behavior, one more footgun to
document. The zero-value-safe model is recommended.)

### 4.5 Return model: `(ExitStatus, @errors.Error)` — three outcomes, not one int
Today's `int`/`-1` collapses three distinct things. Split them:
- **`@errors.Error` carries ONLY "could not START"** — fork failed (EAGAIN/ENOMEM),
  exec failed (ENOENT/EACCES/ENOEXEC), or the PATH walk exhausted — mapped through the
  existing `os_errno` machinery (`errnoToBase`/`failErrno`: ENOENT→NotFound,
  EACCES→PermissionDenied, ENOEXEC→Unsupported). When err is present, `ExitStatus` is the
  zero value and must be ignored. Caller idiom: `status, err := …; if present(err) { … }`.
- **A non-zero exit is NOT an error.** `RunArgs("/usr/bin/false")` returns `(ExitStatus{
  exited, code:1}, no-err)`, `Success() == false`. The error slot is reserved for
  "couldn't run it," never "it ran and returned non-zero."
- **Signal termination is first-class** (fixes a real `bootstrap.Exec` defect — it checks
  only `WIFEXITED` and returns -1 for all abnormal exits, losing the signal and aliasing
  a real code). `Signaled()`/`Signal()` expose it; `Code()` returns -1 (never a valid
  0..255) when signaled, so a naive `Code()` check can't misread a signal as exit 0.

### 4.6 Baremetal: present-but-fail-loud, rooted in `errors.Unsupported`
A `#[build(is(os,"baremetal"))]` variant provides the same signatures, all returning
`(zero ExitStatus, errors.Rooted(errors.Unsupported, "os/process: no process model on a
freestanding target"))`. `Unsupported` (not `Unimplemented`) is deliberate: on a
single-address-space freestanding target you *are* the OS/embedded app — subprocess
execution is **categorically absent**, not merely unbuilt (contrast the FS stubs, which
root in `Unimplemented` because a freestanding filesystem is conceivable). The value
types (`ExitStatus`, `Options`) live in an unconstrained file shared by both variants, so
the package LINKS on every target and no importer needs its own `#[build]` gate.
`fork`/`execve`/`waitpid` must NOT appear in the baremetal file (they'd fail to link) —
split: `run.bn` (`#[build(!is(os,"baremetal"))]`) real impl; `run_baremetal.bn`
(`#[build(is(os,"baremetal"))]`) fail-loud stubs; a third unconstrained file the types.

## 5. Implementation notes

- **Syscall shims.** `fork` + (`execv` for inherit / `execve` for explicit env) +
  `waitpid`, via native externs (as `bootstrap.Exec`'s C shim does today). Build argv/env
  `char**` arrays in the **parent before fork** (the current shim's order); the parent
  frees them after `waitpid`. Hold the managed-slice inputs live across the `__c_call`s
  (the existing `cPath` buffer idiom) and RefDec at statement end — must not leak
  (memory-never-leak rule) nor free buffers the child's exec still needs.
- **Status-word decode in Binate.** `WIFEXITED`/`WEXITSTATUS`/`WIFSIGNALED`/`WTERMSIG`
  are C *macros* (no linkable symbols), so `waitpid` returns the raw `int` and Binate
  decodes it. Linux and macOS share the low-byte wait-status layout, so ONE portable
  decoder works: `exited = (status & 0x7f) == 0; code = (status >> 8) & 0xff; signaled =
  !exited; signal = status & 0x7f`. **Verify with a conformance test on both platforms**
  (portability claim, not an assumption).
- **PATH source under the env stub.** `LookPath`/search needs PATH, which the empty
  `os.Env()` stub can't provide. Read it via `__c_call("getenv", …, "PATH")` from the
  real environ (fallback POSIX default `/usr/bin:/bin` if unset) when inheriting; from the
  explicit `Env` slice when replacing. `getenv` is a sanctioned host-syscall shim (interop
  with the host, not our own env store).
- **VM extern.** The interpreter registers the C primitive as an extern (today
  `pkg/bootstrap.Exec` in `pkg/binate/interp/externs.bn`). Re-home this to the new
  fork/exec/wait primitive so `bni` can run subprocess code.
- **BUILDER-subset constraint.** `cmd/bnc` imports `pkg/std/os`, so `os/process` lands in
  bnc's **BUILDER-compiled tree** — it must stay within the BUILDER subset (structs,
  methods, `__c_call`, and variadics are all fine — BUILDER accepts variadics — just no
  BUILDER-newer features). This also means the CLAUDE.md list of bnc's tree (which omits
  `pkg/std/os`) is stale.

## 6. Start-failure fidelity — v1 (ratified)

Exec failure in the **direct (exact-path) case** happens child-side, after fork — the
parent sees only an exit status, not the errno. **v1:**
- **Search case** — resolve in the PARENT via `LookPath` (stat candidates before fork),
  so not-found/permission surface as clean typed errors and we never fork on a bad path.
- **Direct case** — a pre-fork `access(program, X_OK)` / `stat` in the parent catches the
  common ENOENT/EACCES cleanly; a coarse child sentinel (`execve(…); _exit(127)`) covers
  the residual (a post-check race, or ENOEXEC) as a generic start error.

**v1.1 (deferred refinement):** a close-on-exec **self-pipe** (write end `O_CLOEXEC`; on
exec success the pipe auto-closes → parent reads EOF; on failure the child writes `errno`
then `_exit`s → parent reads the exact errno) gives precise ENOEXEC/race reporting for
the direct case. Not much code, but not needed for v1.

## 7. Migration — retire `bootstrap.Exec`

Full retirement (no shim: `bootstrap` is core and a core→stdlib dep is forbidden, so a
delegating `bootstrap.Exec` → `os/process` is impossible anyway):
- **Delete** the `.bni` decl (`ifaces/core/pkg/bootstrap.bni`) and the C shim
  (`bn_…Exec` in `runtime/binate_runtime.c` — the last non-`Write` process shim).
- **Re-home the VM extern** (`pkg/binate/interp/externs.bn`) to the new primitive.
- **Callers** (`cmd/bnc/{compile,main,test}.bn` → `clang`; `library.bn` → `ar`;
  `util.bn` → `rm`) all pass **bare names**, so they move to the **search** form
  (`RunArgsPath`, or `Run` with `Options{SearchPath: true, Args: …}` for the ones holding
  a dynamic arg slice). They must also adapt to the new **return shape**: `int exitCode`
  → `status, err := …; if present(err) { fail }; …status.Code()/Success()`.
- **Tests:** replace `conformance/273_bootstrap_exec.bn` with an `os/process` conformance
  test (run `/usr/bin/true` and `/usr/bin/false`, assert `Success()` / `Code()==1`, plus
  a not-found start-error case and — for the status decoder — a signal case if feasible);
  unit tests for `LookPath`, the PATH walk, and status decoding.

## 8. Footguns this design defends against (reference)

1. **`os.Env()` stub → silent env-clear** — inherit via `execv`, never by passing the
   empty stub as envp (§4.3).
2. **No nil slices** — zero-value-safe `Env`/`ReplaceEnv`, not a nil sentinel or an
   unverified `present()`-on-slice-field (§4.4).
3. **`execvpe` absent on mac** — manual PATH walk over `execve` (§4.2).
4. **Non-zero exit as an error** — reserved the error slot for could-not-start (§4.5).
5. **Signal info loss** — `Signaled()`/`Signal()`, `Code()` = -1 when signaled (§4.5).
6. **argv[0] shift-by-one** — `Args` = argv[1..] + explicit `Argv0` (§4.1).
7. **argv/env ownership across fork** — build in parent, hold inputs live, parent frees
   (§5).
8. **exit-127 masquerade** — a failed exec `_exit(127)` is indistinguishable from a child
   that genuinely exited 127; resolve/`access` in the parent so start-failure is a real
   typed error, not a magic code (§5, §6).
9. **PATH-search default flip** — a behavior change from `bootstrap.Exec`; handled in the
   caller sweep, not folded in silently (§4.2, §7).
10. **wait-status / signal-number portability** — decode the raw status in Binate (macros
    aren't linkable) and test on both platforms; note that signal NUMBERS differ across
    Linux/mac, so `Signal()` is diagnostic-only until a portable signal enum exists (§5).

## 9. Open items / future

- **Async sibling** — `Start(program, opts) (@Process, err)` + `(*Process).Wait()` when a
  handle-then-wait form is wanted; `Run` deliberately reserves the naming space.
- **Portable signal enum** — like the `O_*` flags, so `Signal()` values are
  cross-platform-comparable (currently the raw host number).
- **`os.Env()` real data source** — not a blocker (inherit works via `execv` today), but
  when it lands, `Env = os.Env()` and PATH-from-`os.Env()` start working, retiring the
  `getenv` shim.
- **Self-pipe (v1.1)** — precise direct-exec errno (§6).
