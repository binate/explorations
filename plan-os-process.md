# Plan: implement `pkg/std/os/process`, retire `bootstrap.Exec`

Status: **PLAN — 2026-07-18.** Executes `explorations/design-os-process.md` (the
ratified design; read it first). This doc records the recon-verified facts, two
corrections to the design's implementation notes, the file-by-file changes, the
commit breakdown, and the risks. User decision on scope: **full retirement**
(migrate every caller incl. the internal test harnesses, delete `bootstrap.Exec`
entirely), keeping everything **BUILDER-gated** (the migrated `cmd/bnc` cone +
`os/process` must stay compilable by the pinned BUILDER, `bnc-0.0.11`).

## 1. Recon-verified facts (the load-bearing ones)

1. **The bytecode VM does not execute `__c_call` at all** — it's native-only,
   rejected at type-check in interpreted mode
   (`pkg/binate/types/check_c_interop.bn`; `pkg/binate/vm/lower.bn` has no
   `OP_C_CALL` arm). `bootstrap.Exec` runs under `bni` only because it's a
   monolithic **C-runtime shim registered as a VM extern**
   (`registerBootstrapExterns` in `pkg/binate/interp/externs.bn`).

2. **`pkg/std/os` uses `__c_call` freely and runs under `bni` by being injected
   as a native instance** — never lowered. A package is injected/native-only iff
   it is listed in the `stdPkgs()` table in `pkg/binate/interp/externs.bn`; that
   ONE table drives the lowering-skip (`IsNativeOnlyInVM`), the injection
   (`InjectStdlibExterns` / `StandardPackages`), and the interface-only load
   (`NativeOnlyInterfacePaths`). Hygiene `scripts/hygiene/stdlib-injected.sh`
   enforces that every `ifaces/stdlib/pkg/std/*.bni` has a `stdPkgs()` entry.

3. **`cmd/bnc` already imports `pkg/std/os`** (in `compile/target/main/library/
   test/util.bn`), so `pkg/std/os`'s full feature set — `__c_call`,
   `#[build(...)]` annotations, `@errors.Error` interfaces, struct methods,
   multi-return — is **already proven BUILDER-`0.0.11`-compilable** (gen1 builds
   today). `os/process` reuses exactly that set. Its one addition —
   **variadic function definitions** (`RunArgs`/`RunArgsPath`) — was verified
   directly against the real BUILDER: a probe with a variadic def + individual-arg
   call + a private-field struct with `*readonly`-receiver methods + `@errors.Error`
   return + `present()` compiled and ran cleanly under `bnc-0.0.11`.

4. **The caller set is repo-wide 12 files, not the 5 the design's §7 lists** (see
   §3 correction B).

## 2. Implementation model (settled)

`os/process` is a **normal injected stdlib package** implemented in Binate over
raw `__c_call` syscalls, exactly like `pkg/std/os`:

- Raw syscalls via `__c_call`: `fork`, `execv` (inherit env), `execve` (explicit
  env), `waitpid`, `getenv` (PATH source), `access` (pre-fork existence/perm
  check). All fixed-arity (no variadic `__c_call`, which isn't supported).
- Everything else in Binate: `Options` handling, argv/envp `char**` building,
  the manual PATH walk, wait-status decode, `ExitStatus` accessors.
- **No new C shim.** This matches the C-free directive ("C only for syscalls")
  and lets the old `Exec` C shim be deleted rather than replaced.

Under `bni`, a program importing `os/process` gets it **injected** (native), so
its `__c_call`s run against libc — the same path `pkg/std/os` already uses.

### Marshalling idioms (from `pkg/std/os`)
- `cPath(name) @[]uint8` — NUL-terminated copy of a `*[]readonly char`.
- `dataOfManaged(p @[]uint8) *uint8` = `bit_cast(*uint8, bit_cast(*int, &p)[0])`;
  a `*[]*uint8` variant yields the `char**` data pointer.
- NULL pointer = `bit_cast(*uint8, 0)`; void call = `__c_call("sym", "void", …)`.
- **argv/envp build in the parent before fork, held live in managed locals**
  (`@[]@[]uint8` keeps the C strings alive; `@[]*uint8` is the pointer array with
  a `bit_cast(*uint8,0)` terminator). Refcounting frees them after `waitpid` at
  scope end — no manual free, no leak, and the child (which only `execve`s +
  `_exit`s, no allocation) never touches refcounts. Satisfies design footgun #7.

### errno mapping
Reuse `pkg/std/os`'s errno machinery rather than duplicating `errnoToBase`:
export a small `os.FailErrno(op @[]readonly char) @errors.Error` (and
`os.Errno() int` if needed) from `pkg/std/os`; `os/process` imports `os` (its
parent — no cycle) and calls `os.FailErrno("execve")` etc. right after the failed
syscall in the **parent** (design §6 keeps start-failure detection parent-side via
`access`/`LookPath`, so errno is read where it's clean). `FailErrno` already
panics on `EBADF`/`EFAULT` (programmer error) — inherited for free.

## 3. Two corrections to the design's implementation notes

**A. §5 "VM extern — re-home `bootstrap.Exec` to the new fork/exec/wait
primitive."** Does not apply. With the injected-package model there is no extern
to re-home: `os/process` is a full injected package (added to `stdPkgs()`), and
the `bootstrap.Exec` extern registration is simply **deleted** (in
`externs.bn` and `pkg/binate/vm/extern_test_helpers_test.bn`). This is strictly
better (consistent with all other stdlib, hygiene-enforced) and meets §5's actual
goal ("`bni` can run subprocess code"). §5's own `getenv` line already assumes
`__c_call`, confirming the injected-`__c_call` model is the intent.

**B. §7 caller list is incomplete.** §7 lists only the 5 `cmd/bnc` files. A
repo-wide grep (`bootstrap\.Exec`) finds **12 files**:
- Production (5): `cmd/bnc/{compile,main,library,test,util}.bn`.
- VM extern registration (2): `pkg/binate/interp/externs.bn`,
  `pkg/binate/vm/extern_test_helpers_test.bn`.
- **Internal asm/native test harnesses (7, ~35 sites)** — the omission:
  `pkg/binate/asm/{elf/elf_test,macho/macho_test,macho/macho_x64_test,
  parse/aarch64_instr_test}.bn`,
  `pkg/binate/native/{aarch64/aarch64_test,aarch64/aarch64_dispatch_test,
  x64/x64_link_test}.bn`.
- Plus the conformance test `conformance/273_bootstrap_exec.bn` and the baremetal
  stub `impls/core/baremetal/pkg/bootstrap/bootstrap.bn`.

Full retirement (user's call) migrates all of them. The test harnesses are in
`cmd/bnc`'s BUILDER cone, so their migrated form must stay BUILDER-compilable
(simple `Run`/`Options`/`present`/`.Code()` — verified safe).

## 4. New package layout

```
ifaces/stdlib/pkg/std/os/process.bni        # single unconstrained interface
impls/stdlib/pkg/std/os/process/
    process.bn        # ExitStatus + Options value types + methods (unconstrained)
    run.bn            # #[build(!is(os,"baremetal"))] Run/RunArgs/RunArgsPath/LookPath + internals
    run_baremetal.bn  # #[build(is(os,"baremetal"))]  fail-loud stubs (errors.Unsupported)
    *_test.bn         # unit tests: LookPath, PATH walk, status decode, Options env selection
```

`process.bni` declares the types (incl. `ExitStatus`'s private fields, per the
`os.bni` convention), the accessor methods, `Options`, and the four functions —
**once, unconstrained** (the `.bni` is target-neutral; the build system selects
`run.bn` vs `run_baremetal.bn`). Nested-package paths resolve automatically
(`ifaces/stdlib/pkg/std/os/process.bni` + `impls/stdlib/pkg/std/os/process/`;
precedent: `pkg/std/math/big`).

## 5. Commit breakdown (each green, BUILDER-safe, small)

**Commit 1 — add `pkg/std/os/process` (no removals).**
- New package files (§4); `Run`/`RunArgs`/`RunArgsPath`/`LookPath`, hosted +
  baremetal.
- `pkg/binate/interp/externs.bn`: `import "pkg/std/os/process"` + a `stdPkgs()`
  entry (satisfies `stdlib-injected` hygiene).
- Export `os.FailErrno`/`os.Errno` from `pkg/std/os` (`.bni` + impl) if reused.
- Unit tests (LookPath, PATH walk on a temp layout, status-word decode with
  crafted values, Options env selection) + a new conformance test (`/usr/bin/true`
  → `Success()`, `/usr/bin/false` → `Code()==1`, a not-found start-error →
  `present(err)` + `errors.Is(NotFound)`; signal case if feasible) with a
  baremetal `.xfail`. `bootstrap.Exec` untouched — everything still green.

**Commit 2 — migrate `cmd/bnc` production callers** (`compile/main/library/
test/util.bn`) to `os/process`. Bare tool names (`clang`/`ar`/`rm`) → search form
(`Options{SearchPath:true, Args:…}`); return shape `int` → `status,err := …; if
present(err) { fail }; …status.Code()/Success()`. `bootstrap.Exec` still present
(used by the test harnesses). Green.

**Commit 3 — migrate the 7 asm/native test harnesses** (~35 sites). Uniform
rewrite: each `bootstrap.Exec(prog, args) → int` becomes
`process.Run(prog, process.Options{SearchPath:true, Args:args})` (uniform
`SearchPath:true` reproduces `execvp`'s always-search semantics and is also
correct for absolute `exePath`s, which contain `/` so LookPath uses them
directly). To keep churn ~1:1, add a tiny per-package test helper
`execExit(prog, args @[]@[]char) int` (returns `status.Code()`, or `-1` on
start-error/signal — the old `Exec` contract) so the ~35 call sites change
minimally. Keep helpers BUILDER-subset. `bootstrap.Exec` now unused. Green.

**Commit 4 — retire `bootstrap.Exec`.**
- Delete: the `.bni` decl (`ifaces/core/pkg/bootstrap.bni`), the C shim
  (`bn_…Exec` in `runtime/binate_runtime.c`), the baremetal stub
  (`impls/core/baremetal/pkg/bootstrap/bootstrap.bn`), and the two extern
  registrations (`externs.bn`, `extern_test_helpers_test.bn`).
- Delete `conformance/273_bootstrap_exec.*` (its coverage is subsumed by
  Commit 1's `os/process` conformance test). Green — nothing references `Exec`.

(2 and 3 could merge if the user prefers fewer landings; kept separate for
smaller, reviewable, easily-landable commits per "stay close to main".)

## 6. Risks & mitigations

- **`fork()` via `__c_call`.** The child runs a minimal Binate branch
  (`if pid==0 { __c_call("execve"/"execv", …); __c_call("_exit","void",127) }`)
  with argv/envp already built pre-fork — no allocation, only `execve`+`_exit`,
  async-signal-safe, equivalent to today's C-shim child path. Forks the whole
  process (incl. under injected-`bni`), same as today.
- **wait-status decode portability.** One decoder for Linux+mac low-byte layout
  (`exited=(s&0x7f)==0; code=(s>>8)&0xff; signaled=!exited; signal=s&0x7f`);
  **verify on both platforms** via the conformance test (the design mandates it).
- **PATH-search default flip.** New default is exact-path; production callers and
  test harnesses that pass bare names get `SearchPath:true` explicitly (design
  footgun #9). Covered by the caller sweep.
- **`char**` NULL terminator** — `bit_cast(*uint8,0)`, matching `os` readdir's
  nil idiom.
- **BUILDER gate** — after Commits 2/3 add `os/process` (and, transitively,
  variadics) into `cmd/bnc`'s cone, run a real BUILDER gen1 build (or the
  `builder-comp*` conformance modes) to confirm the pinned `bnc-0.0.11` still
  compiles the cone. (Probe already passed; this is the full-tree confirmation.)
- **baremetal `.xfail`** — the new conformance test can't run a process on
  freestanding arm32; add the matching `.xfail.builder-comp_native_arm32_baremetal`
  (and the LLVM arm32 baremetal mode if it runs conformance).

## 7. Out of scope / follow-ups (not done here without a decision)

- **Workspace `CLAUDE.md` "bnc's tree" list is stale** — it omits `pkg/std/os`
  (already imported by `cmd/bnc`) and now `pkg/std/os/process` + `pkg/std/errors`.
  Flag to the user; it's a workspace-repo edit (shared checkout), not part of the
  binate code change.
- Design §9 futures: async `Start`/`@Process`, portable signal enum, real
  `os.Env()` data source (retires the `getenv` shim), v1.1 self-pipe for precise
  direct-exec errno. None blocks v1.
