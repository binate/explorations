# Plan: inject `pkg/bootstrap` into the VM + convert I/O to `__c_call`

Status: **PLANNED** (not started) — filed 2026-06-03

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

## Phase 1 — inject bootstrap native-only in the VM

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

## Phase 2 — convert I/O C impls → `.bn` + `__c_call`

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

- **BUILDER**: bootstrap is in `cmd/bnc`'s tree (force-loaded for
  `print`/`println`), so its `.bn` bodies are BUILDER-compiled. BUILDER
  `bnc-0.0.6` accepts `__c_call` (verified during the rt work), and the
  marshalling (pointer ops, `rt.RawAlloc`, byte copies) stays in the
  BUILDER subset — so **no BUILDER bump**, same as rt. Keep the
  marshalling free of interfaces/generics/closures/floats.
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
