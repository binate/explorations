# Profiling notes follow-up: bni running fib(36) (2026-05-01)

> Follow-up to `notes-profiling-bni-2026-04-30.md`. Re-profiled
> after the OP_REFDEC inline lowering work landed (commits
> `46e8e52` IR op, `a8104d2` codegen, `445e40d` vm bytecode,
> `a4847b2` native arm64, `19502d4` IR-gen switchover, plus
> `6aa78d1` ZeroRefDestroy slow-path helper).  Same workload
> (`fib(36)` via compiled bni at `-O2 -g`) and the same `sample`
> setup as the original note.

## Status of original recommendations

The original note's "remaining levers" section listed two items.
The first is now resolved by the OP_REFDEC inline work; the
second remains.

| # | Recommendation | Status |
|---|---|---|
| 1 | Reduce refcount work via programmer ownership choices (no cross-function elision per the project's refcount-transparency stance) | **Inlining landed.**  RefDec / RefInc no longer appear in the profile at all — the work is now part of the bytecode dispatch path itself, not a callee.  Same observable refcount semantics, just no per-RefDec call overhead. |
| 2 | Dispatch-loop tightening / BoundsCheck elision | Still open. |

## Effect on fib(36)

Same workload, warm runs, n=3:

| Variant | Wall | User CPU |
|--|--:|--:|
| v1 (pre-CallCache) | ~2.67 s | ~2.59 s |
| v2 (CallCache, commit `6c8e0c0`) | ~1.86 s | ~1.74 s |
| **v3 (+ OP_REFDEC inline, this note)** | **~1.51 s** | **~1.34 s** |

Cumulative from v1: **−1.16 s wall (−43%)**, **−1.25 s user CPU
(−48%)** on this workload.

Step from v2 → v3 is **−0.35 s wall (−19%)**, **−0.40 s user CPU
(−23%)** — entirely attributable to the OP_REFDEC inline path
(no other behavior changes between the two builds for this
workload).

## New top-of-profile (v3, n = 1728 samples)

```
        bn_vm__execLoop   1057  (61% — dispatch + opcode bodies, including inlined RefDec)
        bn_rt__BoundsCheck 452  (26%)
        bn_vm__pushFrame   134  (7.8%)
        _platform_memset+MemZero  ~85  (4.9% — frame zeroing under pushFrame)
```

99.7% accounted for.  No long tail.  And critically:

- **`bn_rt__RefDec`: 0 samples** (was 287 at v2, 521 at v1).  The
  inlined body is part of whichever opcode path runs it; on this
  workload that's split between execLoop's main dispatch table
  entries.
- **`bn_rt__RefInc`: 0 samples** (was 128 at v2, 270 at v1).  Same
  story.
- **`bn_vm__LookupFunc`: 0 samples** (was already 0 at v2 from
  the CallCache fix; mentioned again here for the one-stop
  cumulative comparison).

`bn_vm__execLoop` self-time *grew* from 581 to 1057 because the
inlined RefDec / RefInc bodies are now attributed to it (or the
opcodes holding them).  Net wall is what matters, and that
dropped 19%.

## Comparison table — full arc

Total samples differ across runs because the workload finishes
at different speeds (`sample` collects ~1 tick/ms of wall),
so the absolute counts aren't directly comparable.  Within a
run, percentages are comparable.

| Function | v1 (3299) | v2 (1394) | v3 (1728) |
|---|---:|---:|---:|
| `bn_vm__execLoop` (self) | 1141 (35%) | 581 (42%) | **1057 (61%)** |
| `bn_rt__BoundsCheck` | 821 (25%) | 281 (20%) | 452 (26%) |
| `bn_rt__RefDec` | 521 (16%) | 287 (21%) | **0** |
| `bn_vm__LookupFunc` | 379 (11%) | 0 | 0 |
| `bn_rt__RefInc` | 270 (8%) | 128 (9%) | **0** |
| `bn_vm__pushFrame` | 78 (2%) | 53 (4%) | 134 (7.8%) |

## Read

The bni profile is now genuinely dominated by VM dispatch +
bounds checks + frame setup.  No remaining "inefficient call"
hotspot to fix.

Remaining levers, all qualitatively bigger than what landed
here:

1. **Dispatch-loop tightening** (`execLoop` 61% wall).  Threaded
   interpreter / computed-goto / direct-threaded shape.
   Substantial rewrite of the dispatch loop.  Biggest single
   lever on this workload.
2. **BoundsCheck elision** in IR-gen (26% wall).  Range analysis
   over `*int` indices known to be safe in their context.
   Substantial compiler work; benefits both bni and compiled
   binaries.
3. **Frame allocation cost** (~13% wall: `pushFrame` + frame
   zeroing).  Less obvious wins — smaller frames mean less to
   zero, but require register-allocation improvements;
   incremental zeroing is plausible but tricky.

Conformance / unit tests stayed green throughout the OP_REFDEC
landings — verified by the `boot-comp-int` runs in the relevant
commits.

## Reproducing

Same recipe as the original note (build bni at `-O2 +g` via
`scripts/build-bni.sh`, write `fib_big.bn` with `fib(36)`,
`sample` for 10 s).  No changes to the recipe.
