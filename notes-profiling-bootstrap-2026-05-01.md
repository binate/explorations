# Profiling notes: Go bootstrap interpreting bnc on hello.bn (2026-05-01)

> Quick look at where the Go bootstrap spends its CPU when
> interpreting `cmd/bnc` to compile a small program.  Workload
> chosen as a stand-in for the boot-comp test path — every
> conformance test under boot-comp invokes `go run . -root <repo>
> cmd/bnc -- ...` against a single `.bn`, so the per-invocation
> shape matters even though each invocation is short.
>
> **Shelf-life caveat (read this first):** the Go bootstrap is
> throwaway code.  bnc already self-compiles end-to-end (gen1 →
> gen2 → gen2-by-gen2 chains run in CI), so in principle we
> could replace the bootstrap with bnc-compiling-itself today,
> modulo release / packaging / build-bootstrap chicken-and-egg
> work.  bni can probably also run bnc directly (untested as of
> this note).  Anything we invest in bootstrap optimization has a
> shelf life bounded by whenever that replacement actually ships.
> The findings below are recorded for completeness, not as a
> recommendation to spend time on.

## Workload

- **Source**: tiny `package "main" / func main() { println("Hello, world!") }`.
  Has to live under the binate root for bnc's loader to resolve
  `pkg/bootstrap` etc. — temp file under `conformance/`.
- **Invocation**: pre-built `bootstrap_bin` (`go build -o`,
  avoiding per-invocation `go run` compile overhead) interpreting
  `cmd/bnc` to compile the hello source.
- **Wall (warm, n=3)**: ~0.43 s.  Of that, an unknown share is
  spent in the clang subprocess that bnc spawns; bootstrap is
  blocked in `__wait4` during that time, so the CPU profile
  reflects only the bootstrap's own work (~500 ms CPU).
- **Profiler**: bootstrap's built-in `-cpuprofile` (Go pprof).

## Top of profile

`bootstrap_bin -cpuprofile boot_hello.prof ...` → 590 ms total
samples / 504 ms wall (Go's profiler counts concurrent GC threads
toward total samples, hence > 100%).

**By category:**

| Bucket | % CPU |
|---|---:|
| GC marking + scanning (`gcDrain` / `scanobject` / `gcBgMarkWorker`) | **~35%** |
| Allocation + zeroing (`mallocgc` / `memclrNoHeapPointers`) | **~14%** |
| Interpreter dispatch (`callFuncInEnv` / `evalCall` / `evalExpr` / `execStmt` / `execIf` / `execFor` / `execAssign` / etc.) | ~30% |
| Misc runtime (`usleep` / `pthread_*` / `madvise` / locking) | ~20% |

**Top flat:**

| Flat | % | Function |
|---:|---:|---|
| 80 ms | 13.6% | `runtime.memclrNoHeapPointers` |
| 50 ms | 8.5% | `runtime.usleep` |
| 40 ms | 6.8% | `runtime.pthread_cond_wait` |
| 30 ms | 5.1% | `runtime.gcDrain` |
| 30 ms | 5.1% | `runtime.scanobject` |
| 30 ms | 5.1% | `runtime.madvise` |
| 30 ms | 5.1% | `runtime.pthread_kill` |

The bootstrap's own functions don't show up large in flat because
the work is highly fan-out: lots of short calls into the
interpreter.  The cumulative view tells the story: ~40% under
`callFuncInEnv` covers all of `evalCall` / `execStmt` / `execIf`
/ `execFor` etc., and a substantial fraction of that is the
allocation pressure those paths create.

## Allocation concentration

Under `runtime.newobject` (cum):

| Cum | Source |
|---:|---|
| 40 ms | `interpreter.newEnv` (inline) |
| 30 ms | `interpreter.pushEnv` |
| 30 ms | `interpreter.callMethod` |
| ~100 ms | `evalCall` / `execAssign` / `execStmt` / etc. (spread) |

The biggest single point is `newEnv` — every function call
allocates a new environment.  The downstream cost is the GC
pressure: ~50% of CPU is spent allocating-and-collecting.

## Hypothetical low-hanging fruit (not recommended to pursue)

1. **Pool / reuse `Env` objects** rather than per-call allocate.
   `Env` lifetime is well-bounded by the call frame, so a
   sync.Pool or a per-interpreter free-list would work cleanly.
   Estimate: 5–10% bootstrap wall savings; ~30–60 min of work;
   low risk.
2. **Slice-backed env entries** (if currently map-backed).
   Bigger change, larger savings (10–15%), more invasive.
3. **Pool `Value` instances** for common shapes (int / bool /
   nil).  Maybe ~5% additional, depending on how Values are
   currently constructed.

Combined ceiling: ~20% bootstrap wall reduction.

For boot-comp CI (282 conformance tests × ~0.4 s wall each = ~2
min serial today), a 20% saving is ~25 s per CI matrix entry.
Meaningful for individual contributor turnaround on a laptop;
small in the matrix-parallel CI shape.

## Recommendation

**Don't invest** unless boot-comp wall becomes actively painful.
The argument:

- The Go bootstrap is replaceable today.  Whatever we save here
  evaporates when bnc-compiling-itself (or bni-running-bnc)
  takes over the role.
- The same engineering effort applied to **bni** improvements
  (the OP_REFDEC inline work, dispatch-loop tightening,
  BoundsCheck elision) compounds — bni stays in the toolchain
  long-term.
- The work is Go optimization, which is orthogonal to the
  language design / self-hosted toolchain story.

If we ever do want this anyway, the env-pooling change is the
clearest single lever.  Pin it as the candidate; defer.

## Reproducing

```sh
go build -o /tmp/bootstrap_bin /Users/vtl/binate/bootstrap

cat > /Users/vtl/binate/temp-binate-4/conformance/000_hello_profile.bn <<'EOF'
package "main"
func main() { println("Hello, world!") }
EOF

bdir="$(mktemp -d /tmp/binate_build_XXXXXX)"
/tmp/bootstrap_bin -cpuprofile /tmp/boot.prof \
  -root /Users/vtl/binate/temp-binate-4 \
  /Users/vtl/binate/temp-binate-4/cmd/bnc -- \
  --root /Users/vtl/binate/temp-binate-4 --build-dir "$bdir" \
  -o /tmp/hello_bin \
  /Users/vtl/binate/temp-binate-4/conformance/000_hello_profile.bn

rm /Users/vtl/binate/temp-binate-4/conformance/000_hello_profile.bn

go tool pprof -top /tmp/boot.prof
go tool pprof -focus newobject -cum -top /tmp/boot.prof
```

Note the source file has to live under the binate repo root, not
in `/tmp` — bnc's loader uses paths relative to `--root` to find
the runtime stubs, and a source outside the root can't pull them
in.
