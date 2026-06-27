# Plan: deprecate the `pkg/bootstrap` file-IO surface — conformance-test classification

Status: **COMPLETE — the bootstrap file-IO surface is retired** (landed
`826d665e`, 2026-06-26).  `bootstrap.{Open,Read,Close,Stat,ReadDir}` and their
`O_*`/`STD*` constants are gone from the `.bni`, the baremetal impl, the
runtime-C shims, and the VM extern registrations; conformance `081`/`277` are
deleted.  Kept on purpose: `Write` (the print/println lowering's I/O sink),
`Exit`/`Args`/`Exec` (process control, no `os` equivalent), and the format
helpers.  Retiring `Write` would require migrating the print/println lowering's
sink off `bootstrap` — a separate codegen task (the long-standing `println`
temporary-hack item), out of this plan's scope.

The landing arc (all 2026-06-26):
- `2b995f14` + `f84c4884` + `a0a8ea96` — toolchain (cmd/bnc, loader,
  asm/{elf,macho,parse}, debug, asm/native test harnesses) migrated to
  `pkg/std/os`, unblocked by the `bnc-0.0.10` BUILDER (which compiles
  `pkg/std/os`).
- `f5137f0a` — bucket-2 tests (142, 343) moved to a VM unit test
  (`pkg/binate/vm/vm_extern_mechanism_test.bn`).
- `826d665e` — the surface itself retired (this plan's removal checklist).

This doc's original classification (which conformance tests consumed the
surface, and how to handle each) is below as the record.

Related: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md) (convert the C
I/O impls to `.bn` + `__c_call`, a separate axis).

Related: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md) (convert the C
I/O impls to `.bn` + `__c_call`, a separate axis).

## Remaining bootstrap-file-IO consumers (post-migration)

1. **Conformance tests** — classified below.
2. **VM extern registrars** — `pkg/binate/interp/externs.bn` and
   `pkg/binate/vm/extern_test_helpers_test.bn` *register* the bootstrap
   functions as VM externs (so bytecode can call them). Providers, not
   consumers; they go away with the surface.
3. **The C shims themselves** — `runtime/binate_runtime.c`
   `bn_F2_3_pkg9_bootstrap1_*` (Open/Read/Write/Close/Stat/ReadDir) + the
   `ifaces/core/pkg/bootstrap.bni` declarations.

`bootstrap.{Exec,Args,Exit}` are **process control, not file I/O**, and have
no `os` equivalent yet — they are out of scope for this deprecation.

## Conformance-test classification

Five files match a `bootstrap.<file-IO>` grep; `317_native_println_int.bn` is
a **comment-only** false positive (it is just `println(7)` and does not import
bootstrap — the `formatInt`/`Write` mention is in a comment describing the
println *lowering*). The four real ones:

### Bucket 1 — tests *of* bootstrap file-IO → DELETED with the surface (`826d665e`)

- **`081_file_write_read.bn`** — regression guard for `bootstrap.Open`'s
  *combined-flag bitmask* handling (`O_WRONLY|O_CREATE|O_TRUNC`). That logic is
  in the bootstrap C shim; `os.OpenFile`'s equivalent (`nativeOpenFlags`) is
  covered by the `os` package tests. Nothing to preserve.
- **`277_bootstrap_stat.bn`** — explicitly tests `bootstrap.Stat`'s 0/1/2
  return + the VM's `Stat`-extern registration. `os.Stat`'s `IsDir()` mapping
  is covered by the `os` tests + the landed migration. Delete with the surface.

### Bucket 2 — incidental file-IO; the real subject is the VM↔native extern mechanism — DONE (`f5137f0a`)

Neither of these is a file-IO test. Each uses a bootstrap file-IO function as a
convenient **registered native extern** to exercise the VM's `BC_CALL` →
`execExtern` branch — the path taken when `calleeFuncIdx < 0` (callee is a
registered native function, no compiled bytecode body):

- **`343_extern_call_loop.bn`** — regression for a host-stack leak in
  `execLoop`'s extern branch (the per-call `callArgs` alloca, now hoisted in
  `pkg/binate/vm/vm_exec.bn`). Uses `bootstrap.Close(-1)` purely as "a cheap,
  scalar-arg, non-allocating extern." File-IO is irrelevant.
- **`142_read_slice_mutation.bn`** — verifies a native extern receiving a
  Binate slice writes into its backing and the VM caller observes it. Uses
  `bootstrap.Read` as the slice-mutating extern. The file round-trip is the
  vehicle; the extern-slice-ABI is the point.

Coverage **cannot** be preserved by a naive de-file-IO rewrite:
- `__c_call(...)` lowers to `OP_C_CALL`, a *different* VM opcode/path — it does
  not reach `execExtern`.
- `os.*` functions are compiled Binate (`calleeFuncIdx >= 0`) — calling them in
  the VM never reaches `execExtern` either.

So a `__c_call`/`os` rewrite would silently change *which* VM path is tested.

### Bucket 3 — essential, needs `pkg/std/os` + a hygiene carve-out → NONE

No conformance test essentially requires real file-IO that must become `os`
file-IO. **The `conformance-imports` carve-out is not needed** (whitelisting
`pkg/std/os` would also drag in `errors`/`io`/`build`/`time`, defeating the
"minimal tests" intent of `scripts/hygiene/conformance-imports.sh`).

## Recommended handling of the bucket-2 tests (142, 343)

**Move the coverage into VM unit tests (`pkg/binate/vm`), not conformance.**
A VM unit test can register a *self-contained, purpose-built* native test
extern and drive `execExtern`/`execLoop` directly — the established pattern in
`extern_test_helpers_test.bn`:

```
vmInst.RegisterExtern("vmtest.noop", <value-ptr>, scalar,
    bit_cast(int, _raw_func_addr(testNoop)))
```

Build an `ir.Module` whose function calls `"vmtest.noop"` (a name with no
compiled body in that module → `calleeFuncIdx < 0` → `execExtern`), lower, and
run a 1M-iteration loop (343) or pass a slice and assert the extern's writes
are observed (142). This needs **no** `pkg/builtins` surface change, **no**
bootstrap, **no** conformance-runner hack, and **no** hygiene carve-out.

Why not the alternatives (answering "where must the test extern live?"):
- **Conformance-runner-only extern** — not viable. A conformance test must
  *name* the extern in its source, and `conformance-imports` limits naming to
  `pkg/bootstrap` / `pkg/builtins/*` / a whitelist exemption / a local fixture.
  A symbol injected only at link/VM-setup time cannot be named in test source.
  (And a local fixture function would be *compiled* by the VM, so it would not
  reach `execExtern`.)
- **`pkg/builtins` reserved/"private" extern** — viable *if* you insist on
  keeping these as conformance tests (it is nameable, links in `comp` modes,
  and is manifest-registered in `int` modes), but it adds a permanent
  reserved function to the always-available surface for a test-only need. The
  VM-unit-test route avoids that, because the unit test controls registration
  itself.
- **Keep one cheap bootstrap extern alive** as the documented test vehicle —
  least work, but leaves a vestigial bootstrap function purely for testing.

Note: in `comp` (native) modes these two tests are near-vacuous anyway — 343
just runs `close(-1)` a million times with no leak path, and 142's native
slice-write is covered by the `os` tests. Their real value is the `int` (VM)
modes, which a VM unit test covers directly.

## Removal checklist — ALL DONE (`826d665e`, except #2 in `f5137f0a`)

1. ~~Delete conformance `081`, `277` (bucket 1).~~ DONE (`826d665e`).
2. ~~Move 343/142 coverage to `pkg/binate/vm` unit tests with a self-registered
   test extern (bucket 2); delete the conformance files.~~ DONE (`f5137f0a`):
   `pkg/binate/vm/vm_extern_mechanism_test.bn`.
3. ~~Remove the bootstrap file-IO extern registrations from
   `pkg/binate/interp/externs.bn` + `extern_test_helpers_test.bn`.~~ DONE
   (`826d665e`).
4. ~~Delete the `bn_F2_3_pkg9_bootstrap1_{Open,Read,Close,Stat,ReadDir}` C
   shims from `runtime/binate_runtime.c` and the `ifaces/core/pkg/bootstrap.bni`
   declarations.~~ DONE (`826d665e`) — also dropped the now-dead
   `<fcntl.h>`/`<sys/stat.h>`/`<dirent.h>` includes and the baremetal stubs.
   This retired the latent `bootstrap.ReadDir` EOVERFLOW bug (now in
   `claude-todo-done.md`).  Note: `Write` was NOT removed (print/println sink).
5. `conformance-imports.sh` needed no change (no `pkg/std/os` carve-out) — as
   predicted, no conformance test essentially required `os` file-IO.
