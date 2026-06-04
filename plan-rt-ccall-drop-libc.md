# Plan: drop `pkg/libc` by using `__c_call` in `rt`

Status: **COMPLETE (shipped)** — Approach A landed: native-only `rt` +
`__c_call`, delete `pkg/libc`. Kept for design rationale.

## Goal

Delete `pkg/libc` entirely. Its surface is now just 4 declare-only
functions — `Malloc`, `Calloc`, `Free`, `Exit` — forwarded to libc by
`runtime/libc_stubs.c` under the `bn_pkg__libc__*` mangling. The only
functional consumer is the libc-host runtime
(`impls/core/libc/pkg/builtins/rt/rt.bn`), whose `RawAlloc` /
`RawAllocZero` / `RawFree` / `Exit` call `libc.Malloc/Calloc/Free/Exit`.

The `__c_call` intrinsic lets `rt` call the C functions *directly and
verbatim* (`__c_call("malloc", *uint8, n)` → a bare `malloc` call, no
`bn_…` mangling, no shim). So the libc dependency can move from "a
declared interface package backed by C stubs" to inline `__c_call`
annotations in `rt`, and `pkg/libc` + `libc_stubs.c` + the
`bn_pkg__libc__*` layer disappear.

This is the first concrete instance of the "slim `pkg/libc` / migrate
callers OUT" todo, and makes `__c_call` the canonical C boundary (per
`plan-c-call.md` §8).

**Note: this is NOT C-freedom.** `__c_call("malloc")` still hard-links
`malloc`/`free`/`exit`. It removes the `pkg/libc` indirection *layer*,
not the C dependency. True C-freedom still needs a Binate allocator +
syscall story.

## BUILDER safety

`__c_call` need only be understood by whatever compiles `rt`. `rt` is
the runtime, compiled+linked into every binary `bnc` builds — including
gen1, which BUILDER builds. So BUILDER must accept `__c_call`.

BUILDER `bnc-0.0.6` compiles and runs a
`__c_call("malloc")`/`__c_call("free")` program cleanly. Non-variadic
`__c_call` (which is all `rt` needs) is in BUILDER. **No BUILDER bump
required.** Unlike the canceled in-place-rename workstream, this is a
runtime *implementation* change (not a package rename), so there is no
`bn_pkg__libc__*` symbol-skew between BUILDER's prebuilt runtime and the
checkout: gen1 keeps linking BUILDER's old libc-based runtime; checkout
binaries use the new `__c_call`-based `rt`. Both are internally
consistent.

## The core challenge — `__c_call` and the bytecode VM

`__c_call` is **compiled-mode only**. The VM has no `OP_C_CALL`
handling: `lowerCallOp` has no arm for it, so it falls to the default
"unhandled IR opcode c_call" → `rt.Exit(1)` — a **hard abort at lower
(load) time**, not a runtime error.

Critically, `rt` is a transitive dependency of essentially every
package, so it is loaded and lowered to bytecode in **every** VM-leg
run (`builder-comp-int`, `-int-int`, `-comp-int`, …). So simply putting
`__c_call` in `rt` would abort *every* VM-leg unit-test and conformance
run at load. Therefore adopting `__c_call` in `rt` REQUIRES one of:

### Approach A — make `rt` native-only in the VM

Stop lowering `pkg/builtins/rt` to bytecode in `cmd/bni`; resolve `rt.*`
calls via the extern registry → the native `rt` linked into `bni`
(exactly how `pkg/libc` and the C-shaped `pkg/bootstrap` surface work
today). `rt`'s `__c_call`s then run as native machine code; the VM
never sees `OP_C_CALL`.

- **Pro**: no new VM execution code — reuses `execExtern`. The
  nested-VM native-address propagation (Externs-copy) already works for
  body-less native packages (`libc`/`bootstrap`-C are the precedent) and
  does NOT depend on `rt` having a bytecode body, so it works at
  arbitrary nesting depth.
- **Con**:
  - `rt`'s OWN bytecode unit tests cannot run in `-int` modes (its impl
    can't be lowered). Must skip/xfail `pkg-builtins-rt` in the `-int`
    modes. (Mitigation: this only removes tests for the *now-removed
    rt-as-bytecode path*; `rt`'s behavior is still covered by the
    compiled modes and by every other `-int` test's calls into the
    native `rt`.)
  - The aggregate-return extern path (`MakeManagedSlice`, 32-byte sret
    shim) becomes load-bearing for ordinary runs (today it's only
    exercised in the nested-VM case). Needs verification at depth.
  - Special-cases `rt` by name in `cmd/bni`'s lowering loops (mirrors
    the existing `irgen.bn` special-case, so not unprecedented).
  - `registerRtExterns` gaps must close: `Exit` and `ZeroRefDestroy`
    are not currently registered.

### Approach B — teach the VM `__c_call` (a `BC_C_CALL` opcode)

Add a `BC_C_CALL` opcode: a lowering arm in `lowerCallOp` (pack args,
emit the verbatim C symbol via `addName`, no mangling), an exec handler
that marshals args and dispatches the C symbol through a **bare-C-name
extern registry**, and registration of `malloc`/`calloc`/`free`/`exit`
(as compiled `__c_call` wrappers in `cmd/bni`) under their bare names.
`rt` stays bytecode.

- **Pro**: `rt` stays fully interpretable — its VM unit tests run
  unchanged; no special-casing; the aggregate path stays as-is. Also
  **unblocks user-level `__c_call` in the VM** (currently xfailed:
  conformance 498/500/527/530) — `__c_call` becomes uniform across
  modes. The checker already restricts `__c_call` to scalar/pointer
  args (≤7 ints), matching the existing `_call_shim_scalar` shape, so
  marshalling is largely already supported.
- **Con**: adds a new VM opcode + exec handler (more new VM execution
  code = more surface for subtle VM bugs). Variadic `__c_call` in the VM
  stays unsupported (the 7-int shim) — fine for `rt`'s non-variadic
  leaves, but the general feature is still partial.

## Decision

**Approach A — native-only `rt`.** (I had recommended B; the user chose
A, with this rationale, which supersedes my recommendation:)

> B forces you to register specific C functions, and then to *know which
> C functions you need* — a standing maintenance/knowledge burden. The
> long-term direction is to register arbitrary native implementations at
> the **package level** (the `_Package` infra being built now), and for
> perf we'd want the entire standard library registered as native. `rt`
> is fundamental enough that "rt **must** be native" is a clean,
> acceptable invariant.

So `rt` joins `pkg/libc`/`bootstrap`-C as native-in-the-VM, and its
`-int` bytecode unit tests are intentionally dropped (rt is native, so
interpreting its bytecode is not a supported configuration). This also
prefigures package-level native registration: today we special-case
`rt` by name; later that becomes a general "this package is native"
mechanism.

Note: baremetal `rt` (`impls/core/baremetal/.../rt.bn`) is libc-free
(bump allocator + semihost) and was out of scope — untouched.

## Follow-up (separate): rethink `rt.Exit`

`rt.Exit` (→ `exit`) is the wrong paradigm in general — process exit
makes no sense in an embedded/freestanding environment, and the runtime
mostly uses it for *abort* conditions (OOM, bounds-fail, refcount
corruption). "abort" / "panic" is likely the right model. Tracked as a
separate todo; NOT part of this change (this change preserves
`Exit`→`exit` behavior, just via `__c_call`).
