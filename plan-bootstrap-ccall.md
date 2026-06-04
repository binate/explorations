# Plan: inject `pkg/bootstrap` into the VM + convert I/O to `__c_call`

Status: **Phase 1 DONE** (landed on main `a7fabc7a`, 2026-06-03);
**Phase 2 not started**. The float-arg shim prerequisite
([`plan-float-arg-shim.md`](plan-float-arg-shim.md)) landed first
(`7abc3809`), then Phase 1 made `pkg/bootstrap` native-only in the VM:
cmd/bni skips lowering it, the format helpers are registered as externs
in both `registerBootstrapExterns` copies, bootstrap's bytecode unit
tests are xfailed in the `-int` modes, and `extern_register_std_test`
guards the format-helper registration. Verified: `287_float_println`
green in `-int` (the float-extern payoff), full `builder-comp-int` /
`-comp-int` (only pre-existing `520`) and `-int-int` (`520` + pre-existing
rt-loader gap `136`/`383`), hygiene clean. Phase 2 (convert the
C-implemented I/O to `.bn` + `__c_call`) remains — see below.

## Goal

Eliminate the hand-written `bn_pkg__bootstrap__*` I/O implementations from
`runtime/binate_runtime.c` by converting them to ordinary `.bn` code that
reaches libc via `__c_call`, and making `pkg/bootstrap` native-only in
the bytecode VM (so its `__c_call` bodies run as native code). Direct
analog of the just-landed rt work
([`plan-rt-ccall-drop-libc.md`](plan-rt-ccall-drop-libc.md)).

**Not C-freedom.** `__c_call("open"/"read"/…)` still hard-links libc's
syscall wrappers; this removes the hand-written *glue layer*, not the C
dependency. (True C-freedom needs direct syscalls — out of scope.)

## Background — bootstrap is MIXED (unlike rt/libc)

`pkg/bootstrap` today is two things in one package:
- **Pure-Binate format helpers** — `Itoa`, `formatInt`, `formatInt64`,
  `formatUint`, `formatBool`, `formatFloat` — `.bn` bodies in
  `pkg/bootstrap/bootstrap.bn`; run as **bytecode** in the VM.
- **C-implemented I/O** — `Open`, `Read`, `Write`, `Close`, `ReadDir`,
  `Stat`, `Exit`, `Args`, `Exec` — declared in `pkg/bootstrap.bni`,
  implemented as `bn_pkg__bootstrap__*` in `binate_runtime.c`; body-less,
  so already resolved as **native externs** in the VM via
  `registerBootstrapExterns`.

So the I/O half is *already* native-extern — half the "injection" is
done. The per-target structure also already exists: arm32 baremetal
ships its own bootstrap impl (semihosting) under
`runtime/baremetal_arm32/pkg/` + `impls/core/baremetal/pkg/bootstrap/`,
selected by the path mechanism — exactly like rt's libc-vs-baremetal
split.

## Phase 1 — inject bootstrap native-only in the VM — DONE (`a7fabc7a`)

Once the I/O functions gain `.bn` bodies containing `__c_call`, the VM
must NOT lower bootstrap (the `__c_call` ops hard-abort at lower, same as
rt). So: make `cmd/bni` skip lowering `pkg/bootstrap` (mirror the rt
skip in both lowering loops).

**The one non-obvious prerequisite**: making the *whole* package
native-only also pushes the **format helpers** from bytecode to
native-extern, so they must be registered. `registerBootstrapExterns`
currently registers only the I/O surface — Phase 1 must additionally
register `formatInt` / `formatInt64` / `formatUint` / `formatBool` /
`formatFloat` / `Itoa` (they're compiled into the host binary, so
native dispatch is fine and faster; `print`/`println`, which lower to
`bootstrap.formatInt64` + `Write`, keep working through the externs).

Phase 1 is small and behavior-preserving (I/O is already native-extern;
format helpers just move bytecode→native) and can land before any C→.bn
conversion. It can be done as the very first step even with the I/O
still in C.

## Phase 2 — convert I/O C impls → `.bn` + `__c_call` — DEFERRED (2026-06-03)

**DEFERRED, possibly indefinitely.** Blocked by the BUILDER-runtime
coupling (see "Constraints → BUILDER" below): the C→`.bn` migration must
be done *during* a BUILDER bump/release, not within the pinned-BUILDER
tree, because gen1 links BUILDER's runtime which still defines the I/O
symbols. The trivial+moderate `.bn` code (Close/Exit/Open/Read/Write) was
written and reviewed (correct modulo the link blocker) — preserved in the
appendix below for whenever a BUILDER bump happens. **`Stat` is a further
defer-within-defer**: `struct stat` is ABI-divergent across the libc
platforms (size 88–144B, `st_mode` offset 4–24B) with no portable
offset-free primitive, so it needs a per-libc-platform `pkg/bootstrap`
impl split — a separate project. Also note (per the 2026-06-03 decision):
it may be better to **eliminate** several of these bootstrap I/O functions
(as a real stdlib `io` package subsumes them) than to convert them — so
this conversion may never be worth doing. The original (now-superseded)
function-by-function plan follows.

`__c_call` is **scalar/pointer-only** (the checker rejects slice,
struct, and aggregate args/returns), but bootstrap's I/O signatures are
full of slices and managed-slice returns. So each function needs
marshalling glue in `.bn` (cstr copies via `rt.RawAlloc`, data-pointer
extraction, aggregate construction). Convert easy-first:

| fn | C today (`binate_runtime.c`) | conversion | difficulty |
|----|------|------------|------------|
| `Close(fd)→int` | scalar | `__c_call("close", int, fd)` | trivial |
| `Exit(code)` | scalar (void) | `__c_call("exit", int, code)` (dummy-int discard) | trivial |
| `Open(path,flags)→int` | `BnSlice path` | null-terminate path→cstr, `__c_call("open", int, cstr, flags)` | moderate |
| `Read(fd,buf)→int` | `BnSlice buf` | data-ptr+len, `__c_call("read", int, fd, ptr, len)` | moderate |
| `Write(fd,buf)→int` | `BnSlice buf` | data-ptr+len, `__c_call("write", …)` | moderate |
| `Stat(path)→int` | `BnSlice path` | cstr + scratch `struct stat` buffer ptr (platform-fixed size), `__c_call("stat", int, cstr, statbuf)` | moderate-fiddly |
| `Exec(prog,args)→int` | builds `char**` | construct null-terminated cstr array, `__c_call("execvp", int, prog_cstr, argv_ptr)` | harder |
| `ReadDir(path)→@[]@[]char` | → `BnManagedSlice` | opendir/readdir loop + `d_name` offset + build `@[]@[]char` | hardest |
| `Args()→@[]@[]char` | reads `static char **bn_argv` | **needs an argv hook** — see below | special |

As each function moves to `.bn`, delete its `bn_pkg__bootstrap__*`
definition from `binate_runtime.c`.

### The `Args` wrinkle — a minimal C remnant stays

`bn_pkg__bootstrap__Args` reads a `static char **bn_argv` saved by
`main()` at startup (`binate_runtime.c:217,294`). No libc function hands
back argv, so `Args` cannot be pure `__c_call`. Options:
1. Keep a tiny C accessor (`char **bn_argv(void)`) that `Args`
   `__c_call`s, then iterate argv in `.bn`.
2. Save argv into a Binate global at startup (still needs a C-side
   startup hook to populate it).

Either way a minimal argv remnant stays in C — so "eliminate all
`bn_pkg__bootstrap__*`" becomes "eliminate all *but* an argv accessor".

## Constraints / non-obvious points

- **BUILDER — REQUIRES A BUMP (the Phase 2 blocker, discovered
  2026-06-03).** The original "no BUILDER bump" claim was WRONG. bootstrap
  is force-loaded into `cmd/bnc`, so converting the I/O to `.bn` compiles
  `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}` into gen1's
  `pkg__bootstrap.o`. But gen1 is built by BUILDER and **deliberately
  links BUILDER's pinned C runtime** (`scripts/lib/build-compilers.sh:55-62`),
  which still DEFINES those symbols → **duplicate-symbol link failure
  building gen1.** The rt precedent didn't hit this because rt only
  *removed* C symbols (stale BUILDER copies = harmless dead code);
  bootstrap I/O conversion *adds* `.bn` defs of symbols BUILDER's runtime
  owns. Moving I/O out of C is a **runtime-ABI change**, and "gen1 links
  BUILDER's pinned runtime" is exactly the invariant that gates the
  runtime ABI. So the migration must happen **during a BUILDER bump/
  release** (the new BUILDER's runtime omits the I/O), not within the
  pinned-BUILDER tree. (`__c_call` itself compiles fine under BUILDER
  `bnc-0.0.6` — the blocker is the runtime symbols, not the language.)
  The only same-tree workaround is pointing `build_gen1`'s `--runtime` at
  the checkout runtime, which erodes the gen1 ABI-safety margin — rejected.
- **`__c_call` void returns**: `exit` (and any void C call) uses the
  dummy-`int`-discard workaround until the
  [proper void-return support][void] lands.
- **Baremetal untouched**: the libc `bootstrap.bn` (with `__c_call`) is
  the libc-target impl; baremetal keeps its semihost impl. Scope the
  change to the libc impl tree.
- **`bootstrap.println` hack**: this work converts `Write`/the I/O, not
  the println path; don't let it lean harder on the println hack
  (slated for removal).
- **Test annotation**: like rt, bootstrap's own bytecode unit tests
  can't run once it's native-only with `__c_call` — they'll need the
  same `.xfail` treatment in the `-int` modes (a candidate for the
  better-annotation mechanism already on file).

## Verification

Same matrix as rt: unit (`builder-comp` + the `-int` legs), conformance
(`builder-comp`, `builder-comp-int`, `builder-comp-comp`,
`builder-comp-comp-int`, `builder-comp_arm32_baremetal`). Bootstrap I/O
is exercised by nearly every conformance test (file reads, `print`), so
coverage is broad. Phase 1 should be verified independently before
Phase 2 begins.

## Relation to other work

- Pattern + machinery proven by [`plan-rt-ccall-drop-libc.md`].
- Gated-improvement follow-ups that also apply: proper `__c_call`
  void-return support; a first-class bnc-only/vm-only test annotation.
- After both this and the rt work, `binate_runtime.c` sheds its
  `bn_pkg__bootstrap__*` I/O glue (keeping the core runtime + the argv
  hook), and `__c_call` is the uniform C boundary across rt + bootstrap.

[void]: claude-todo.md — "`__c_call` should support void returns"

## Appendix — ready Phase-2 `.bn` code (written 2026-06-03, unlandable per the BUILDER blocker)

Goes in `pkg/bootstrap/io.bn` (collocated; the loader merges it for libc
builds and baremetal ignores it — baremetal resolves its own complete
copy first). Delete the matching `bn_pkg__bootstrap__{Open,Read,Write,
Close,Exit}` from `runtime/binate_runtime.c` (keep `slice_to_cstr`,
`managed_alloc`, `cstr_to_managed_slice` — still used by ReadDir/Args/
Exec/Stat). Raw-pointer indexing `p[i]` and `&buf[0]` + `bit_cast(*uint8,
…)` are proven idioms (rt.MemCopy / baremetal rt.bn). This compiles under
BUILDER; the ONLY blocker is the gen1-links-BUILDER-runtime duplicate
symbols — so it can only land during a BUILDER bump.

```binate
package "pkg/bootstrap"

import "pkg/builtins/rt"

func pathCStr(s *[]readonly char) *uint8 {
	var n int = len(s)
	var p *uint8 = rt.RawAlloc(n + 1)
	for i := 0; i < n; i++ { p[i] = cast(uint8, s[i]) }
	p[n] = cast(uint8, 0)
	return p
}

func Open(path *[]readonly char, flags int) int {
	var cpath *uint8 = pathCStr(path)
	var oflags int = flags & 3
	if (flags & 64) != 0 { oflags = oflags | 64 }       // O_CREAT
	if (flags & 512) != 0 { oflags = oflags | 512 }     // O_TRUNC
	if (flags & 1024) != 0 { oflags = oflags | 1024 }   // O_APPEND
	var fd int = __c_call("open", int, cpath, oflags, 420)  // mode 0644
	rt.RawFree(cpath)
	return fd
}

func Read(fd int, buf *[]uint8) int {
	var n int = len(buf)
	if n <= 0 { return 0 }
	return __c_call("read", int, fd, bit_cast(*uint8, &buf[0]), n)
}

func Write(fd int, buf *[]readonly uint8) int {
	var n int = len(buf)
	if n <= 0 { return 0 }
	return __c_call("write", int, fd, bit_cast(*uint8, &buf[0]), n)
}

func Close(fd int) int { return __c_call("close", int, fd) }

func Exit(code int) { rt.Exit(code) }
```
