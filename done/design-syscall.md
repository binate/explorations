# Design: `pkg/std/syscall` — low-level libc-syscall wrappers (the os family's shared foundation)

Status: **DESIGN — 2026-07-18, decisions ratified.** Prompted by the `os/process` work
(`design-os-process.md`): `os` and the new `os/process` both make raw libc calls
and both must turn `errno` into an `errors.Error`, but `os`'s errno machinery is
private and unreachable from the sibling `os/process`. Exposing it on `os`
(`os.Errno`/`os.FailErrno`) leaks low-level strata onto a high-level package;
duplicating it in `os/process` is copy-paste rot. **The right fix (user's call,
2026-07-18) is a low-level syscall layer that both build on, with `errno` fully
hidden behind error-returning wrappers.** This doc proposes it; two decisions
(§3, §5) are flagged for the user.

## 1. Goal & scope

A single low-level home for **thin, error-returning wrappers over the libc calls
the os family uses**. Each wrapper:
- makes the `__c_call`,
- retries `EINTR` internally,
- on failure reads `errno` and classifies it into an `errors.Error` base
  (`NotFound`/`PermissionDenied`/`Retryable`/…),

so **`errno` is a private implementation detail of this package** — callers (`os`,
`os/process`, future `os/*`) get `(result, @errors.Error)` and never see an errno
number or a classification table. The `errno`→`errors.Error` mapping and the
per-OS `errno()` accessor live **here and only here** (moved out of `os`) — one
source of truth, no drift.

Hosted-only: every wrapper is a syscall, so the package is entirely
`#[build(!is(os,"baremetal"))]`. A freestanding target's `os`/`os/process` fail
loud without ever importing `syscall` (their baremetal files don't call it), so
`syscall` needs no baremetal variant.

## 2. Structure (mirrors os's existing per-OS/arch split, which moves here)

```
ifaces/stdlib/pkg/std/syscall.bni            # the wrapper surface (error-returning)
impls/stdlib/pkg/std/syscall/
    syscall.bn        # arch-neutral wrappers: fork/execv/execve/waitpid/access/
                      #   getenv/read/write/close/open/mkdir/rename/remove/... 
    errno.bn          # errno -> errors.Error classifier (PRIVATE); the current
                      #   os_errno.bn table, extended (ENOEXEC, ...) — one copy
    errno_linux.bn    # errno() accessor via __errno_location (PRIVATE, per-OS)
    errno_darwin.bn   # errno() accessor via __error            (PRIVATE, per-OS)
    stat_*.bn         # per-OS/arch stat/fstat/lstat + osStat layout (moved from
                      #   os's stat_io*.bn / stat_{linux,darwin}*.bn)
    readdir_*.bn      # per-OS readdir + osDirent layout (moved from os's readdir*.bn)
    lseek_arm32.bn    # per-arch lseek/lseek64, pread/pread64 (moved from os.bn)
    *_test.bn
```

The intricate per-OS/arch machinery (the `osStat`/`osDirent` struct layouts, the
`stat$INODE64`/`stat64`/`lseek64` symbol selection) is exactly what should live in
a low-level syscall package; `os` keeps only the portable `File`/`FileInfo`/
`FileMode`/`DirEntry` types and the policy on top.

## 3. Visibility — DECIDED: os-family-internal at `pkg/std/os/sys`

The package lives at **`pkg/std/os/sys`** (nested under `os`) and is documented
**"os-family only — not a supported public API."** Only `os` and `os/process`
import it; users go through `os`. Binate has **no `internal/` loader mechanism**
(verified), so the boundary is **convention + docs**, optionally backed by a
`bnlint` rule that flags any importer of `pkg/std/os/sys` outside `pkg/std/os*`.
This keeps both `errno` AND the raw fork/exec/wait/read surface out of users'
hands — the same reasoning that ruled out `os.Errno`/`os.FailErrno`; a public
`syscall`-style package would reintroduce that low-level exposure one package
over.

## 4. API shape (illustrative)

```
package "pkg/std/syscall"
import "pkg/std/errors"

// Process (what os/process needs):
func Fork() (int, @errors.Error)                 // pid; 0 in child; parent-side error
func Waitpid(pid int) (int, @errors.Error)       // raw status word; EINTR retried
func Accessible(path *[]readonly char) @errors.Error   // access(X_OK) — hides the mode bit
func Getenv(name *[]readonly char) (@[]char, bool)
// ChildExecOrExit runs ONLY in the forked child: raw execve (execv when envp is
// null) then _exit(127) on failure.  It is NORETURN and ALLOCATION-FREE — an
// error-returning Execve would allocate the error in the async-signal-safe child
// (forbidden between fork and exec), so the child-exec primitive cannot classify;
// the parent observes a failed exec as the child's _exit(127).  Takes the
// pre-hoisted raw char*/char** pointers (built parent-side by os/process).
func ChildExecOrExit(path *uint8, argv *uint8, envp *uint8)

// File I/O (what os needs; os.File methods become thin wrappers):
func Read(fd int, p *[]uint8) (int, @errors.Error)          // EINTR retried; EOF is io.EOF
func Write(fd int, p *[]readonly uint8) (int, @errors.Error)
func Open(path *[]readonly char, flag int, perm int) (int, @errors.Error)
func Close(fd int) @errors.Error
// … Lseek/Pread/Pwrite/Stat/Fstat/Lstat/Mkdir/Rename/Remove/ReadDir …
```

`errno`, the classifier, and the per-OS/arch symbol selection are all **private**.
The wait-status *word* is returned raw for `os/process` to decode (the WIF* macros
aren't linkable — the decoder is Binate, and belongs in `os/process`, not here,
since it's process-semantics not a syscall). `argv`/`envp` `char**` building stays
in `os/process` (it's process-specific marshalling), OR moves here as a helper —
minor, decide during implementation.

## 5. Scope / staging — DECIDED: staged (option B)

Porting **all** of `os`'s I/O (the ~28 libc calls across `os.bn` + 4 `readdir*` +
3 `stat_io*` files, all tested across every conformance mode, both native
backends, and arm32) onto `syscall` in one go is a large, delicate refactor of a
heavily-exercised core package. Options:

- **(A) Full port now** — build `syscall` with every wrapper, move all per-OS/arch
  machinery, rewire `os` and `os/process` onto it, delete `os`'s private errno +
  syscall code. Single source of truth immediately; one big reviewed change (risk
  concentrated; must keep os green across all modes/backends/arches).
- **(B) Staged (recommended)** — Stage 1: build `syscall` with the **errno
  foundation** (accessor + classifier, *moved* from `os` so there is ONE copy) +
  the **process wrappers** (fork/execv/execve/waitpid/access/getenv); `os/process`
  uses them (errno fully hidden); `os`'s I/O keeps its `__c_call`s but routes its
  errno step through `syscall` (so classification is single-source from day one,
  even before its calls are wrapped). Stage 2 (tracked follow-up): port `os`'s
  file I/O + stat/readdir onto `syscall` wrappers, deleting the last of `os`'s raw
  `__c_call`s. Unblocks `os/process` without a big-bang `os` rewrite; each stage is
  small and independently green.

Both honor "no duplication" (errno classifier exists once, in `syscall`, from the
start) and "no leak on `os`". They differ only in how much of `os`'s *I/O* is
wrapped now vs. incrementally.

## 6. BUILDER-gating (unchanged shape from `os/process`)

`os` imports `syscall`, and `cmd/bnc` imports `os`, so `syscall` enters `cmd/bnc`'s
BUILDER cone. This is **gen1-safe**: gen1 compiles `cmd/bnc` against the *frozen*
bundle's `os` (which has no `syscall` import), so source-`os`-importing-`syscall`
doesn't affect it; gen2+ use source `os`+`syscall`. `syscall` (and the refactored
`os` + `os/process`) ship in the *same* future release; the `cmd/bnc` migration +
`bootstrap.Exec` retirement (`os/process` Phase B) stay gated on bumping
`BUILDER_VERSION` to that bundle. `syscall` must stay within the BUILDER subset
(it uses only `__c_call` + structs + `#[build]` — all already in the cone via
`os`).

## 7. Effect on the in-flight `os/process` work

The `os/process` package already written (fork/exec/wait, marshalling, ExitStatus,
Options, LookPath — all verified correct by review) stays; only its **errno step
rewires** to `syscall`: delete `os/process`'s local `sysErrno`/`mapStartErrno`/
`startErrno`, and replace the raw `__c_call("fork"/"waitpid"/"access"/"getenv")`
with `syscall.Fork()/Waitpid()/Access()/Getenv()` (errno hidden, EINTR handled,
classification centralized — which also fixes the EAGAIN misclassification the
review found). The wait-status decoder and `char**` building stay in `os/process`.
