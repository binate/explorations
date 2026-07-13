# `os.Args()` under the interpreter — why `SetArgs`, not VM special-casing

## The problem

`os.Args()` (main `0d1555f7`) reads a package-level `args` global captured once
at package init from `bootstrap.Args()`. It is correct on the compiled path, but
under the bytecode interpreter (`cmd/bni`) it returns the **host interpreter's**
own argv (bni's `-I/-L/program.bn` tokens), not the interpreted program's, and
those strings — living in the host's native memory — fault when read.

Root cause: `pkg/std/os` is **injected into the VM as host-native code** (so an
interpreted program can do file I/O through bni). So the interpreted program's
`os.Args()` runs *native* and reaches bni's own native `bootstrap.Args()`; it
never touches any VM-side arg path. Confirmed independent of the cached global —
a live, no-global `Args()` fails identically. (This is xfail'd in
`conformance/stdlib/os/011_args`'s `-int` modes.)

## Rationale for the chosen fix

1. **No special-casing in the generic VM infrastructure.** As a principle, the
   VM (`pkg/vm`, the interop/extern machinery) must stay general — it must not
   grow a hack for `args`. This rules out an `os.Args`-specific VM shim (and is
   also why the existing `bootstrap.Args` VM shim, `progArgsAfterDash`, is the
   wrong shape — a special-case in the interop layer).

2. **This situation is particular to bni.** For a *general* VM embedded in a
   larger compiled Binate program (true dual-mode interop), the interpreted code
   almost certainly *should* see the entire parent program's args — i.e. the
   current "returns the host's argv" behavior is arguably the correct default
   there. The problem only exists because bni wants the interpreted program to
   behave as if it were the whole program.

3. **The fully-generic solution is a closed universe — too heavyweight, and
   bni-specific anyway.** bni could inject *copies* of `os` (and, in principle,
   all of stdlib and every other package), giving it full control over
   everything the interpreted code sees — a self-contained universe as if the
   interpreted code were the only code in existence. But that is (a) very
   heavyweight, and (b) particular to bni's goal (a closed universe), not to
   interop. We do not actually want to provide a separate universe.

## The decision: `os.SetArgs`

Provide a setter on `pkg/std/os`:

```
// SetArgs replaces the arguments Args() returns and returns the PREVIOUS value,
// so a caller can save and later restore them: old := SetArgs(new); …; SetArgs(old).
func SetArgs(args @[]readonly @[]readonly char) @[]readonly @[]readonly char
```

- It takes and returns the same fully-shaped `@[]readonly @[]readonly char`
  (element 0 the program name, 1.. the arguments), so the returned previous
  value can be fed straight back to restore.
- The caller supplies the whole argv including element 0, so under the
  interpreter bni can even set a *real* program name at index 0 (something the
  compiled path can't yet do — it leaves an empty placeholder).

**bni's protocol.** The interpreter calls `os.SetArgs(<the interpreted
program's argv>)` *before* it begins executing the interpreted code. Because os
is the shared (injected) instance, overwriting its `args` global is exactly what
makes the interpreted program's `os.Args()` return its own args. The existing
`progArgsAfterDash` VM shim then goes away (point 1).

**Accepted downside.** bni's *own* view of `os.Args()` changes when it calls
`SetArgs` — it has handed its args over to the interpreted program. That is
fine: if bni needs its own args afterward, it saves them first (either the value
`SetArgs` returns, or by reading `Args()` beforehand).

## Implemented (2026-07-12)

Landed as `os.SetArgs` (`a3b39454`), the cmd/bni wiring + shim removal
(`8984ea2a`), and e2e coverage (`11f473f1`).  cmd/bni reads its own argv from
`os.Args()` (via `bniArgs`, skipping index 0) and installs the program's argv
via `setProgramArgs`/`os.SetArgs` before running it; a nested bni reads the argv
its parent installed, so no `--`-stripping shim is needed to avoid recursion.

Both formerly-open details, as decided by the user:

- **How bni determines the program's argv:** keep the `--` convention (the
  program's args are everything after `--`), with the **program path at index 0**.
  So under the interpreter `os.Args()[0]` is the real program path — better than
  the compiled path's empty placeholder (they converge once the compiled argv[0]
  follow-up lands).
- **Direct `bootstrap.Args()` from interpreted code diverges** (returns bni's own
  argv) — accepted. Programs use `os.Args()`. `conformance/487_bootstrap_args`
  asserts `bootstrap.Args()`'s content, so it is xfail'd in the `-int` modes.
