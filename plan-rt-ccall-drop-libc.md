# Plan: drop `pkg/libc` by using `__c_call` in `rt`

Status: **IN PROGRESS** (started 2026-06-03)

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

## BUILDER safety — verified

`__c_call` need only be understood by whatever compiles `rt`. `rt` is
the runtime, compiled+linked into every binary `bnc` builds — including
gen1, which BUILDER builds. So BUILDER must accept `__c_call`.

**Verified (2026-06-03): BUILDER `bnc-0.0.6` compiles and runs a
`__c_call("malloc")`/`__c_call("free")` program cleanly.** Non-variadic
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

## Recommendation

**Approach B (`BC_C_CALL`).** It costs one VM opcode but avoids the
coverage loss, the `rt` special-casing, and the newly-load-bearing
aggregate-extern path of Approach A — and it makes `__c_call` a
first-class, uniform C-FFI mechanism (the stated direction) rather than
a compiled-only escape hatch worked around per-package. Approach A is
the lighter-to-land option if we want to minimize new VM code and are
willing to drop `rt`'s `-int` unit tests.

(This is a real fork; confirm the approach before implementation.)

## Concrete steps (Approach B)

1. **VM `__c_call` support**:
   - `lowerCallOp` (`pkg/binate/vm/lower_call.bn`): add an `OP_C_CALL`
     arm — pack args like `OP_CALL`, emit `BC_C_CALL` carrying the
     verbatim symbol via `addName(instr.StrVal)` (NOT `qualifyCallName`).
   - `vm.bni`: add `BC_C_CALL` opcode.
   - `vm_exec.bn`: add a `BC_C_CALL` handler — marshal `Imm` args into
     `callArgs`, `LookupExtern(symbol)`, dispatch via
     `dispatchExternBinding` / `_call_shim_scalar`, result → `Dst`.
2. **Bare-C-name externs**: in `cmd/bni` (and `pkg/binate/vm`'s shared
   registration), register `malloc`/`calloc`/`free`/`exit` under their
   bare C names, backed by compiled `__c_call` wrappers (small Binate
   funcs in the VM host). This replaces `registerLibcExterns`.
3. **`rt` leaves → `__c_call`** (libc-host impl only,
   `impls/core/libc/pkg/builtins/rt/rt.bn`): `Exit`→`__c_call("exit")`,
   `RawAlloc`→`__c_call("malloc")`, `RawAllocZero`→`__c_call("calloc")`,
   `RawFree`→`__c_call("free")`. Drop `import "pkg/libc"`.
4. **Delete `pkg/libc`**: `pkg/libc.bni`, `runtime/libc_stubs.c`,
   `registerLibcExterns` (both copies), the `pkg/libc` import in
   `extern_register_std.bn` + `cmd/bni/externs.bn`. Audit every other
   `pkg/libc` / `bn_pkg__libc__*` reference (mostly stale comments in
   `native/{x64,aarch64}` + `compile_imports*`).
5. **Baremetal untouched**: `impls/core/baremetal/.../rt.bn` is
   libc-free (bump allocator + semihost) — out of scope.

(Approach A steps, if chosen instead: skip `rt` lowering in `cmd/bni`'s
two loops; add `Exit`+`ZeroRefDestroy` to `registerRtExterns`;
skip/xfail `pkg-builtins-rt` in `-int` modes; steps 3–5 as above; no VM
opcode.)

## Verification matrix

Same as the memset/memcpy change: unit (`builder-comp` + the `-int`
legs), conformance (`builder-comp`, `builder-comp-int`,
`builder-comp-comp`, `builder-comp-comp-int`, `builder-comp_arm32_
baremetal`). Approach B should additionally let the `__c_call`
conformance tests (498/500/527/530) drop their `-int` xfails — verify
and un-xfail where green (non-variadic at least).

## Follow-up (separate): rethink `rt.Exit`

`rt.Exit` (→ `exit`) is the wrong paradigm in general — process exit
makes no sense in an embedded/freestanding environment, and the runtime
mostly uses it for *abort* conditions (OOM, bounds-fail, refcount
corruption). "abort" / "panic" is likely the right model. Tracked as a
separate todo; NOT part of this change (this change preserves
`Exit`→`exit` behavior, just via `__c_call`).
