# Profiling notes: bni running fib(36) (2026-04-30)

> **Status (2026-05-01):** OP_REFDEC inline lowering has since
> landed (commits `46e8e52` / `a8104d2` / `445e40d` / `a4847b2` /
> `19502d4`, plus `6aa78d1` ZeroRefDestroy slow-path).  See
> `notes-profiling-bni-followup-2026-05-01.md` for the v3
> re-profile and the new top-of-profile shape (RefDec / RefInc
> are now zero-sample; execLoop is 61%).  This file is preserved
> as the original baseline + CallCache result.

> Companion to `notes-profiling-bnc-2026-04-29.md` /
> `notes-profiling-bnc-followup-2026-04-29.md`. `sample` profiler on
> macOS, compiled `bni` (cmd/bni built via boot-comp at `-O2 -g`)
> running a recursive `fib(36)` test program (~14.9M calls).
> Goal: identify where bni spends its time before deciding what to
> optimize.  Outcome: a single targeted fix (per-VMFunc CallCache
> for `BC_CALL` / `BC_FUNC_ADDR`) eliminated the dominant
> non-runtime cost.

## Workload

- **Build**: `bnc --cflag -O2 -g -o bni cmd/bni` (via the
  `scripts/build-bni.sh` convenience wrapper, or its underlying
  bootstrap → bnc invocation).
- **Profiled run**: `bni fib_big.bn` where `fib_big.bn` declares
  `fib(36)` and calls it from `main`.  Wall: ~3s, ~100% CPU.
- **Profiler**: `/usr/bin/sample` at 1ms intervals.

## Baseline (pre-fix) — n = 3299 samples

**Top self-time:**

| Samples | % wall | Function |
|---:|---:|:---|
| 1141 | 35% | `bn_vm__execLoop` (dispatch loop) |
|  821 | 25% | `bn_rt__BoundsCheck` |
|  521 | 16% | `bn_rt__RefDec` |
|  379 | 11% | `bn_vm__LookupFunc` |
|  270 |  8% | `bn_rt__RefInc` |
|   78 | 2.4% | `bn_vm__pushFrame` |

97% accounted for, no long tail.

## The hotspot: `LookupFunc` is a linear scan per call

```binate
// pkg/vm/vm.bn (pre-fix)
func LookupFunc(vm @VM, name *[]const char) int {
    for i := 0; i < len(vm.Funcs); i++ {
        if streq(vm.Funcs[i].Name, name) { return i }
    }
    return -1
}
```

Called from `BC_CALL` (`pkg/vm/vm_exec.bn`) on every dispatch even
though `f.Names[instr.Aux]` is constant per call site.  For
fib(36) (~14.9M calls), that's 14.9M scans, each doing string
compares.  ~400 of the 821 `BoundsCheck` samples were inside the
scan loop, so the inclusive cost was ~17–20% of wall — and would
grow with the function-table size on larger programs.

## Fix: per-VMFunc `CallCache` (commit `6c8e0c0`)

Memoize the `LookupFunc` result per call site, parallel to
`Names`:

```binate
// VMFunc adds:
CallCache @[]int   // -2 = unresolved, -1 = extern, >=0 = vm.Funcs index

// vm_exec.bn BC_CALL:
var calleeFuncIdx int = f.CallCache[instr.Aux]
if calleeFuncIdx == -2 {
    calleeFuncIdx = LookupFunc(vm, f.Names[instr.Aux])
    f.CallCache[instr.Aux] = calleeFuncIdx
}
// ...
```

Same cache used by `BC_FUNC_ADDR`.  No language-semantic change;
safe because `vm.Funcs` is finalized before execution begins
(parse → check → lower → run).  Designed to be invalidated under
future REPL mutation (full flush on rebind / append-with-shadow,
-1-only flush on pure append).

## After the fix

| Metric | Before | After | Δ |
|--|--:|--:|--:|
| fib(36) wall (n=3) | ~2.67 s | ~1.86 s | **−0.81 s (−30%)** |
| fib(36) user CPU | ~2.59 s | ~1.74 s | **−0.85 s (−33%)** |
| boot-comp-int conformance | 282/282 | 282/282 + faster (~9s → 6s wall) | clean |

Re-profile (also at -O2):

| Samples (post) | % wall | Function |
|---:|---:|:---|
| 581 | 42% | `bn_vm__execLoop` |
| 287 | 21% | `bn_rt__RefDec` |
| 281 | 20% | `bn_rt__BoundsCheck` |
| 128 |  9% | `bn_rt__RefInc` |
|  53 |  4% | `bn_vm__pushFrame` |

`LookupFunc` is gone from the profile entirely.  `BoundsCheck` is
down 66% (most of it was inside `LookupFunc`'s scan loop).  Total
samples dropped 58% (3299 → 1394) because the workload finishes
faster and `sample` collects fewer ticks.

## Read

The remaining cost shape is **runtime helpers** (RefDec, RefInc,
BoundsCheck) and the dispatch loop itself.  Per the project's
explicit position on refcount transparency
(`feedback_refcount_transparency` memory: refcounting is
intentionally transparent and deterministic; no cross-function
elision because that breaks interop), the right way to reduce
refcount work is **programmer ownership choices**, not optimizer
elision.  So further wins would need to come from:

- Dispatch-loop tightening (computed-goto / threaded interpreter
  shape).  Substantial change to `execLoop`.
- BoundsCheck elision in provably-safe positions.  Requires
  range-analysis infrastructure in IR-gen.  Substantial.

These are real projects, not profiler-driven micro-fixes.

For the workload that motivated the profile (test speeds): the
~30% wall reduction directly translates into a ~30% wall
reduction in `boot-comp-int` conformance + unit-test runs.

## Reproducing

```sh
# Build bni at -O2 +debug (needed for sample to see symbols).
./scripts/build-bni.sh -o /tmp/bni

# Tiny workload — fib(36) takes ~1.5–3s, plenty for a 10s sample.
cat > /tmp/fib_big.bn <<'EOF'
package "main"
func fib(n int) int {
    if n < 2 { return n }
    return fib(n-1) + fib(n-2)
}
func main() { println(fib(36)) }
EOF

# Sample.
/tmp/bni /tmp/fib_big.bn > /dev/null &
PID=$!
sample $PID 10 -wait -file /tmp/bni_fib.txt -mayDie
wait $PID
```
