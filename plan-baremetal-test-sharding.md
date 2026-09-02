# Plan: test-level sharding + skip for the arm32-baremetal test runner

## Goal
The compiled `builder-comp_arm32_baremetal` unit runner boots each package's
test binary under `qemu-system-arm -kernel` (no argv) and runs ALL of a
package's tests in one process. So:
- **memory**: `pkg/binate/vm`'s 57-file suite exhausts the 4 MiB no-free
  bare-metal arena (`rt.RawAlloc: arena exhausted`);
- **skip**: FS-dependent tests in `pkg/binate/link` / `cmd/bnld` (readFile /
  os.Create — no filesystem under semihosting) can't be excluded so the pure
  tests can run.

Today these three are the only non-xfail'd baremetal Unit failures; the
alternative (whole-package xfail) throws away real ILP32 coverage. `cmd/bni`
already shards/skips at runtime (`--shard-index/--shard-count/--skip`); this
brings the same to the COMPILED runner so bare-metal gets it too.

## Approach B (chosen): runtime filtering via the semihosting command line
Give the bare-metal test binary an argv (from the qemu command line), then
reuse the runner's existing runtime `--run` filter machinery for
`--shard`/`--skip`. One build → N runs (no per-shard rebuild), consistent with
host/bni.

**Linchpin — VERIFIED:** `qemu-system-arm -M virt … -kernel BIN -append "ARGS"`
delivers, via semihosting `SYS_GET_CMDLINE` (0x15), the string
`BIN ARGS` — exactly argv shape (element 0 = program name). Confirmed by a probe
that printed `/tmp/cltest_bin shard-index 2 shard-count 4 --skip Foo`.

## Steps
**Steps 1-3 landed in `d4f2dfd52`.**
1. **DONE** — `semihost.SemihostGetCmdline(buf *uint8, cap int) int`
   in `runtime/baremetal_arm32/semihost.{bni,s}` (SYS_GET_CMDLINE; returns the
   length or -1). Baremetal-gated, not BUILDER-compiled.
2. **DONE** — `impls/core/common/pkg/builtins/startup/args_baremetal.bn` — replaces the
   empty-placeholder argv with: fetch the cmdline via SemihostGetCmdline, split
   on spaces into argv, install via SetArgs; fall back to the 1-element
   placeholder when there is no cmdline (preserves the len>=1 invariant).
   Baremetal-gated (`is(entrypoint,"start")`), imports pkg/semihost — not
   BUILDER-compiled.
3. **DONE** — `cmd/bnc/gen_test_runner.bn` — extends the generated runner's runtime filter
   (currently `--run <substr>`) to also honor `--shard-index N`,
   `--shard-count M`, and `--skip <substr>`, mirroring cmd/bni's semantics: run
   test at position p (0-based, over the run/skip-selected set) iff
   `p % M == N-1`. BUILDER-compiled surface, but this is plain os.Args parsing —
   BUILDER-safe (verify against the pinned BUILDER before landing).
4. `scripts/unittest/runners/builder-comp_arm32_baremetal.sh` — pass
   `-append "--shard-index I --shard-count N --skip PAT"` to qemu; iterate shards
   when a `.split` marker is present.
5. `scripts/unittest/run.sh` — honor `<key>.split.builder-comp_arm32_baremetal`
   (shard count) and `<key>.skip.builder-comp_arm32_baremetal` (skip pattern) for
   the baremetal mode, the way `.split.vm` / `.skip.<mode>` already work for the
   bni runners; thread SHARD_IDX/COUNT + SKIP_FILTER through.
6. Markers: `pkg-binate-vm.split.builder-comp_arm32_baremetal` (shard count that
   fits the arena), `pkg-binate-link.skip.…` / `cmd-bnld.skip.…` (FS-test
   patterns; if a package is entirely FS-dependent it stays a whole xfail).

## Per-package application (decided)
- **vm**: do NOT xfail; the 4 MiB-arena LEAK is the real issue (sharding only
  partially helps — some shards still exhaust) — investigate the leak properly as
  separate work (see claude-todo.md).  Once the leak is fixed/bounded, a `.split`
  marker can shard vm to fit.
- **link**: `.skip` the filesystem-dependent tests (readFile-based Emit*/Link*),
  leaving the pure tests to run on bare-metal.
- **cmd/bnld**: entirely FS (os.Create) — whole-package xfail.

## Verify
Each step under qemu locally (Darwin has qemu-system-arm + ld.lld) and finally
the full `scripts/unittest/run.sh builder-comp_arm32_baremetal` green with the
markers; keep the Unit gate red until it lands (per the owner).
