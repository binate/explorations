# Profiling notes follow-up: bnc compiling itself (2026-04-29)

> **Status (2026-05-01):** Re-profiled again after OP_REFDEC inline
> lowering landed.  See `notes-profiling-bnc-followup-2026-05-01.md`
> for the v4 result (RefDec / RefInc / headerPtr now zero-sample
> at -O2; bnc-self share dropped ~35%; wall barely moves because
> clang dominates).  This file is preserved as the v3 baseline
> after the addStructDef and weak_odr fixes.

> Follow-up to `notes-profiling-bnc-2026-04-29.md`. Two fixes from
> the original recommendations have landed; this note records the
> new shape after both — including a re-profile at `-O2`, which the
> previous note couldn't do.

## Status of previous note's recommendations

| # | Recommendation | Status |
|---|---|---|
| 1 | Don't re-mangle on every probe in `addStructDef` | **Landed** — commit `c884838` (cached `MangledName` field on `StructDef`). |
| 2 | Hash table for `moduleStructDefs` lookup | Not done — superseded by the data below. |
| 3 | Profile at `-O2` | **Done.** Required fix #4 below first. |
| 4 | Build pipeline: collapse per-package clang invocations | **Off the table.** The per-package boundary is intentional (independent processing, future caching). The right fix for the `__wait4` share is incremental compilation, which is a separate project. |
| (out-of-scope: `-O2` link bug) | Was: `_bn_types____dtor_CheckError` undefined. Root-caused & fixed. | **Landed** — commit `65cb258`. Cause: dtors/copies emitted as `linkonce_odr`, which LLVM is allowed to discard if locally unreferenced. At `-O2` the optimizer inlined all in-TU callers and dropped the bodies, leaving extern references in other TUs unresolved. Switched to `weak_odr` (same merge semantics, no discard). One-line change in `pkg/codegen/emit_debug.bn`. |

## Effect of fix #1 (still at -O0+debug)

Same workload as previous note (`gen1_bnc` compiling `cmd/bnc`),
warm runs, n=3:

| Metric | Before | After fix #1 | Δ |
|--|--:|--:|--:|
| Wall | ~3.17 s | ~2.13 s | **−33%** |
| User CPU | ~2.61 s | ~1.62 s | **−38%** |

`discoverStructFromType` dropped from 35% of wall (inclusive) to
5.2%. `bn_mangle__StructName` self-time fell from 65 → 10 samples.
Conformance `boot-comp-comp`: 282/282.

## Re-profile at -O2

Profiled `gen2_bnc_O2` (built with `--cflag -O2 -g`) compiling
`cmd/bnc`. 2846 samples (~2.85 s wall, ~2.0 s user CPU).

**Wall distribution (n = 2846):**

| Bucket | Samples | % wall |
|---|---:|---:|
| Clang spawn + wait (`__wait4` + `__fork` + atfork malloc-lock) | ~2270 | **~80%** |
| Bnc-self work | ~570 | **~20%** |

**Top non-clang leaves at -O2:**

| Samples | % wall | Function |
|---:|---:|:---|
| 187 | 6.6% | `bn_rt__RefDec` |
| 100 | 3.5% | `bn_rt__BoundsCheck` |
| 59 | 2.1% | `bn_rt__RefInc` |
| 29 | 1.0% | `bn_buf__grow` |
| 20 | 0.7% | `bn_codegen__emitCall` |
| 16 | 0.6% | `bn_codegen__llvmType` |
| 15 | 0.5% | `bn_ir__collectFuncStrings` |
| 14 | 0.5% | `bn_buf____copy_CharBuf` |

**Comparison with the original -O0 profile (3520 samples):**

| | -O0+debug (original) | -O0+debug (after fix #1) | -O2 (after both fixes) |
|--|--:|--:|--:|
| `__wait4`+`__fork` share | 35% | 52% | **80%** |
| Bnc-self share | 65% | 48% | **20%** |
| `discoverStructFromType` (inclusive) | 35% | 5.2% | <0.3% |
| Largest single bnc function | 35% (the above) | 5.2% | <1% |

## Read

**Bnc source-level optimization at this workload has hit diminishing
returns.** No single bnc function consumes >1% of wall at `-O2`. The
work is genuinely dispersed across the codegen — there is no
algorithmic hotspot left to fix.

The remaining bnc-self time is dominated by **runtime helpers**
(`RefDec`/`RefInc`/`BoundsCheck` together ≈ 12% of wall, ≈ 60% of
bnc-self). These are inherent to the language's refcount semantics
and inline-out about as much as they're going to.

The big remaining lever — clang invocation overhead at ~80% of
wall — is *not* a bnc-internal problem. It is the cost of paying for
clang startup once per package; addressing it requires incremental
compilation / build caching, which is a separate, substantial
project.

## Outstanding observations (worth a separate look, not addressed here)

- **Possible regression in commit `f08ddcb`** ("RefDec dtor dispatch:
  rt.CallDtor → IR-gen-magic _call_dtor"). On the same workload:
  - v3 source (just before `f08ddcb`), -O0+debug: 1.64 s user.
  - HEAD source (after `f08ddcb`),  -O0+debug: ~3.0 s user.
  - v3 source, -O2 (no debug): 1.17 s user.
  - HEAD source, -O2 (+debug): ~1.95 s user.
  Both runs use the same workload and the same hardware. Could be a
  real CPU regression, or could be measurement noise from the `-g`
  difference in the -O2 case (but the -O0 difference is hard to
  explain that way). Filing as an observation; not chased here.

- **Lookup sites that the previous note suspected** (`scope.Lookup`,
  `LookupFunc`, `FindStringID`, `token.Lookup`) still do not appear
  in the profile at any optimization level. Confirmed non-issues for
  this workload.

## Future directions (qualitatively bigger than what landed here)

1. **Incremental compilation / build cache.** Addresses the ~80%
   clang share. Largest single remaining lever. Substantial design
   work — out of scope for a profiler-driven session.
2. **Refcount elision (lifetime analysis, paired RefInc/RefDec
   removal, borrow tracking on call args).** Addresses the ~12% wall
   from refcount helpers. Substantial compiler work.
3. **Different / non-bootstrap workloads.** Self-compile of
   `cmd/bnc` is one specific shape; profiles of large user code
   (when it exists) or of `bni` may surface different hotspots.

## Reproducing

Same recipe as previous note. To profile at `-O2`:

```sh
# build gen1 at -O0+debug (slow, via bootstrap)
GEN1_BUILD=$(mktemp -d /tmp/binate_build_XXXXXX)
(cd ~/binate/bootstrap && go run . -root ~/binate/binate ~/binate/binate/cmd/bnc -- \
  --root ~/binate/binate --build-dir "$GEN1_BUILD" -g \
  -o /tmp/binate_prof/gen1_bnc ~/binate/binate/cmd/bnc)

# build gen2 with -O2 + debug (so sample sees symbols)
GEN2_BUILD=$(mktemp -d /tmp/binate_build_XXXXXX)
/tmp/binate_prof/gen1_bnc \
  --root ~/binate/binate --build-dir "$GEN2_BUILD" --cflag -O2 -g \
  -o /tmp/binate_prof/gen2_bnc_O2 ~/binate/binate/cmd/bnc

# profile gen2 at -O2 compiling cmd/bnc
GEN3_BUILD=$(mktemp -d /tmp/binate_build_XXXXXX)
/tmp/binate_prof/gen2_bnc_O2 \
  --root ~/binate/binate --build-dir "$GEN3_BUILD" \
  -o /tmp/binate_prof/gen3_bnc ~/binate/binate/cmd/bnc &
PID=$!
sample $PID 10 -wait -file /tmp/binate_prof/sample_O2.txt -mayDie
wait $PID
```
