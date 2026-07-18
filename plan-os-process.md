# Plan: implement `pkg/std/os/process`, retire `bootstrap.Exec`

Status: **Phase A LANDED (`0d0b3a62`, 2026-07-18); Phase B BUILDER-gated.** Executes
`explorations/design-os-process.md` (read it first). User decision: **full
retirement, "obviously BUILDER gated."** The gate is decisive and splits the work
into two phases (§4). Errno is handled via the `pkg/std/os/sys` layer
(`design-syscall.md`), NOT the `os.Errno`/`os.FailErrno` approach §3 originally
described (rejected as leaky); see the todo/done for the landed shape.

## 1. Recon-verified facts

1. **The VM never executes `__c_call`** (native-only; `check_c_interop.bn`,
   `vm/lower.bn` has no `OP_C_CALL` arm). `pkg/std/os` runs under `bni` by being
   **injected as a native instance** — listed in `stdPkgs()` in
   `pkg/binate/interp/externs.bn`; that one table drives lowering-skip
   (`IsNativeOnlyInVM`), injection (`InjectStdlibExterns`/`StandardPackages`), and
   interface-only load (`NativeOnlyInterfacePaths`). Hygiene
   `stdlib-injected.sh` enforces a `stdPkgs()` entry for every
   `ifaces/stdlib/pkg/std/*.bni`.
2. **`os/process` is a normal injected `__c_call` package**, exactly like
   `pkg/std/os`: raw syscalls (`fork`, `execv`/`execve`, `waitpid`, `getenv`,
   `access`) via `__c_call`; `Options`/argv-envp-build/PATH-walk/status-decode in
   Binate; **no new C shim** (C-free directive).
3. **BUILDER feature-set is safe.** `cmd/bnc` already imports `pkg/std/os`, so
   `__c_call`, `#[build(...)]`, `@errors.Error` interfaces, methods, multi-return
   are all proven BUILDER-`0.0.11`-compilable. The one addition — **variadic
   function definitions** (`RunArgs`/`RunArgsPath`) — was verified against the
   real BUILDER for **both** individual-arg calls **and** spread `f(slice...)`,
   plus the `Options{Args:@[]readonly @[]readonly char}` field assigned from
   `@[]@[]char`. (Assignability confirmed in the type checker: `@[]@[]char →
   @[]readonly @[]readonly char` via `Identical`+`dropsConst` const-widening;
   string-literal → `*[]readonly char` via `isStringWritableSliceTarget`.)
4. **Caller set (repo-wide, multiple patterns):** 14 files reference
   `bootstrap.Exec` in code — Production (5) `cmd/bnc/{compile,main,library,
   test,util}.bn`; VM registration (2) `pkg/binate/interp/externs.bn`,
   `pkg/binate/vm/extern_test_helpers_test.bn`; test harnesses (7)
   `pkg/binate/asm/{elf/elf_test,macho/macho_test,macho/macho_x64_test,
   parse/aarch64_instr_test}.bn`, `pkg/binate/native/{aarch64/aarch64_test,
   aarch64/aarch64_dispatch_test,x64/x64_link_test}.bn` (**44 call sites**). Plus
   two doc/prose touchpoints: `conformance/273_bootstrap_exec.*`, the baremetal
   stub `impls/core/baremetal/pkg/bootstrap/bootstrap.bn`, `README.md:171`, and
   ~6 prose comments (`externs.bn`, `extern_test_helpers_test.bn`,
   `bootstrap.bni`, the baremetal stub, `runtime/binate_runtime.c`).

## 2. THE decisive gate — gen1 resolves `cmd/bnc`'s stdlib from the FROZEN bundle

`scripts/lib/build-compilers.sh` `build_gen1` compiles `cmd/bnc` with the pinned
BUILDER using **`--base "$blib"` (the frozen `bnc-0.0.11` bundle)** for stdlib
`-I`/`-L`; only `pkg/binate` + `pkg/bootstrap` come from source (`--prepend
"$BINATE_DIR"`). The bundle ships `pkg/std/os.bni` as a **file** (no `os/`
subdir). Therefore:

- **`cmd/bnc` cannot import a brand-new `pkg/std/os/process` until it ships in a
  released bundle and `BUILDER_VERSION` is bumped** — else gen1 fails "package
  `pkg/std/os/process` not found" (reproduced with the real BUILDER). Same for any
  **new `pkg/std/os` export** `cmd/bnc` would consume directly.
- **Everything else uses source stdlib** (`--base "$BINATE_DIR"`): gen2, native,
  bni, and the unittest `builder-comp` runner. So `os/process` itself, its
  registration in `externs.bn` (not in `cmd/bnc`'s cone), and the 7 test-harness
  migrations (test files compiled with source stdlib) are **NOT gated**.

This is the documented "bump `BUILDER_VERSION`" case, not a language-subset case.

## 3. Implementation notes (marshalling, errno) — with review fixes

### Marshalling (from `pkg/std/os` idioms)
- `cPath(name) @[]uint8` (NUL-terminated); `dataOfManaged(p) *uint8` =
  `bit_cast(*uint8, bit_cast(*int,&p)[0])`; NULL = `bit_cast(*uint8,0)`; void call
  = `__c_call("sym","void",…)`.
- **argv/envp built in the PARENT before `fork`.** A `@[]@[]uint8` holds the C
  strings alive; a `@[]*uint8` is the pointer array (each = `dataOfManaged` of a
  buffer) with a `bit_cast(*uint8,0)` terminator; its `.data` (offset 0,
  pointer-width elements) is a valid `char**`. **Both slices must be
  function-scope managed locals held live past `waitpid`** — the `@[]*uint8` is a
  pure borrow view and does NOT keep the buffers alive; do NOT `consumeTemp` the
  buffer slice into the pointer array.
- **MANDATORY: hoist the raw pointers before `fork`.** Compute `progData`,
  `argvData`, `envpData` as raw `*uint8` locals in the parent. The child branch
  must be EXACTLY `if pid==0 { __c_call("execve"/"execv","void", progData,
  argvData[, envpData]); __c_call("_exit","void",127); panic("unreachable") }` —
  no Binate helper calls, no slice indexing (would emit a bounds check), no
  managed-value ops. This keeps the child allocation-/refcount-free and
  async-signal-safe (equivalent to the old C shim's execve-only child). The
  trailing `panic` terminates the block so no post-fork cleanup IR follows `_exit`
  (which the compiler does not know is noreturn). `_exit` (not `exit`) bypasses
  atexit/stdio-flush.
- **`waitpid` EINTR loop** — retry while return is `-1` and `errno()==EINTR`
  (matching `os.bn`'s read/write/open idiom; fixes the current shim's latent
  dropped-status bug).
- Status decode in Binate (one Linux+mac decoder): `exited=(s&0x7f)==0;
  code=(s>>8)&0xff; signaled=!exited; signal=s&0x7f`. **Verify on both platforms**
  via the conformance test.

### errno mapping — via the `pkg/std/os/sys` layer (SUPERSEDES the earlier plan)

The earlier "export `os.Errno`/`os.FailErrno`" idea was rejected (leaks errno onto
the high-level `os`) and a self-contained duplicate was rejected (drift; the
Commit-1 review found an EAGAIN misclassification). Per the 2026-07-18 decision,
errno is owned by a new **os-family-internal low-level layer `pkg/std/os/sys`**
(see `explorations/design-syscall.md`), which both `os` and `os/process` build on;
`errno` is fully hidden behind error-returning wrappers. So `os/process`'s errno
step (its local `sysErrno`/`mapStartErrno`/`startErrno`) is **deleted** and its raw
`__c_call`s become `sys.Fork()/Waitpid()/Accessible()/Getenv()/ChildExecOrExit()`
— which also fixes the EAGAIN classification centrally. The wait-status decoder and
`char**` building stay in `os/process`. The `ENOEXEC` arm + the EAGAIN fix land in
`sys`'s single classifier (moved from `os`'s `errnoToBase`). Staged: Stage 1 builds
`sys` + rewires `os/process` + routes `os`'s errno through `sys`'s classifier;
Stage 2 (tracked follow-up) wraps `os`'s file I/O.

## 4. Two-phase sequencing

**Phase A — now (not gated).** Land `os/process` so it can ship in the next
release; nothing destructive.
- **Commit 1 — add `pkg/std/os/process`.** New files
  `ifaces/stdlib/pkg/std/os/process.bni`, `impls/stdlib/pkg/std/os/process/{process.bn
  (ExitStatus+Options value types, unconstrained), run.bn (#[build(!is(os,
  "baremetal"))]), run_baremetal.bn (#[build(is(os,"baremetal"))])}`; the
  `stdPkgs()` entry + import in `externs.bn`; the `os.Errno`/`os.FailErrno`
  exports and the `ENOEXEC` fix in `pkg/std/os`. Tests: unit
  (`LookPath`, PATH walk over a temp layout, status-word decode with crafted
  values, `Options` env selection, and a focused **`@[]*uint8` dtor-safety test**
  — build the pointer array, drop it, confirm the still-live buffer slice's data
  is intact / not freed); a **conformance test** (run `/usr/bin/true` →
  `Success()`, `/usr/bin/false` → `Code()==1`, a not-found → `present(err)` +
  `errors.Is(NotFound)`; a signal case if feasible). `bootstrap.Exec` untouched.

**Phase B — after a released bundle carries `os/process` and `BUILDER_VERSION` is
bumped** (tracked in `claude-todo.md`; a release event, the project's call). Do
NOT start until the bump.
- **Commit 2 — migrate `cmd/bnc` production callers** (`compile/main/library/
  test/util.bn`): bare tool names → `&process.Options{SearchPath:true, Args:…}`;
  `int` → `status,err := …; if present(err){fail}; …status.Code()/Success()`.
  **Note the `&` — `Run` takes `*readonly Options`; a value literal won't
  type-check.**
- **Commit 3 — migrate the 7 asm/native test harnesses** (44 sites). Uniform
  rewrite via a tiny per-package helper `execExit(prog, args @[]@[]char) int`
  (calls `process.Run(prog, &process.Options{SearchPath:true, Args:args})`,
  returns `status.Code()` or `-1` on start-error/signal — the old `Exec`
  contract) to keep churn ~1:1. `SearchPath:true` reproduces `execvp`'s
  always-search and is correct for absolute `exePath`s (contain `/` → used
  directly). (Not gated — could be pulled into Phase A for early validation if the
  user prefers; default is Phase B for an atomic migration.)
- **Commit 4 — retire `bootstrap.Exec`.** Delete: `.bni` decl, C shim
  (`runtime/binate_runtime.c`), baremetal stub, both extern registrations
  (`externs.bn`, `extern_test_helpers_test.bn`), `conformance/273_bootstrap_exec.*`
  (subsumed by Commit 1's test), **the `README.md:171` `Exec` table row**, and the
  ~6 prose comments still naming `Exec`. Runtime deletion is safe: gen1 links the
  frozen bundle runtime (keeps the symbol, harmless), gen2 links the current tree
  (symbol gone, no emitted caller).

## 5. Risks & mitigations

- **BUILDER gate (Phase B)** — see §2. Do not migrate `cmd/bnc` pre-bump.
- **fork/exec safety** — the mandatory hoist (§3) makes the child
  execve+_exit-only; COW isolation + `_exit`/execve both noreturn ⇒ the child
  touches no refcounted state; verified equivalent to today's C-shim fork.
- **`@[]*uint8` is new to the codebase** — layout/dtor verified sound (raw-ptr
  element has `NeedsDestruction()==false`, so cleanup RefDecs only the backing,
  never frees the borrowed C strings); still add the focused dtor test (Commit 1).
- **wait-status portability** — one decoder; conformance test must pass on
  Linux+mac.
- **`builder-comp_arm32_linux` (qemu-user, in `modesets/all`, NON-experimental)**
  — the new `fork`/`execve`/`getenv`/`access`/PATH-walk pattern differs from the
  old monolithic C shim; qemu-user fork emulation is fragile. **Before landing
  Commit 1, RUN the new conformance test under `builder-comp_arm32_linux`.** If it
  fails, root-cause + fix or add `.xfail.builder-comp_arm32_linux` with a note
  (Bug Discovery Protocol) — do not assume parity.
- **baremetal xfail** — the new conformance test needs **only**
  `.xfail.builder-comp_arm32_baremetal` (the LLVM sibling marker); the native mode
  `builder-comp_native_arm32_baremetal` **inherits it via `OVERRIDE_MODE`** — do
  NOT add a separate native marker.
- **PATH-search default flip** — callers passing bare names get `SearchPath:true`
  explicitly (Phase B sweep).
- **LookPath env tension (v1)** — public `LookPath(program)` uses ambient PATH
  (`getenv`); the internal search helper takes the PATH string (from `Options.Env`
  when replacing, else `getenv`). Document that search+replace-env uses `Env`'s
  PATH internally while public `LookPath` is ambient-only.

## 6. Design-doc corrections (fold into an "Implementation corrections" note)

- §5 "re-home the VM extern" → **obsolete**: injected-package model, delete the
  extern (per user's full-retirement decision).
- §7 caller list → undercounts by 7 test harnesses + README + prose comments (§1.4).
- §4.5 "ENOEXEC→Unsupported" → **not true today**; add the arm (§3).
- §4.3 / footgun #1 "`os.Env()` is an empty stub" → **false on hosted targets**
  (entry glue populates it via `captureEnv`). The inherit-via-`execv` conclusion
  still stands, but the reason is avoiding envp marshalling + snapshot-vs-live
  staleness, not emptiness.

## 7. Out of scope / follow-ups

- **Phase B** — gated on the release + `BUILDER_VERSION` bump (todo entry).
- Workspace `CLAUDE.md` "bnc's tree" list is stale (omits `pkg/std/os`, now also
  `pkg/std/os/process`) — flag to the user; workspace-repo edit.
- Design §9 futures: async `Start`/`@Process`, portable signal enum, real
  `os.Env()` data source (retires `getenv`), v1.1 self-pipe.
