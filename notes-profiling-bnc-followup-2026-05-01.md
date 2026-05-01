# Profiling notes follow-up: bnc compiling itself (2026-05-01)

> Follow-up to `notes-profiling-bnc-followup-2026-04-29.md`. Re-
> profiled after the OP_REFDEC inline lowering work landed
> (commits `46e8e52` IR op, `a8104d2` codegen, `19502d4` IR-gen
> switchover, plus `6aa78d1` ZeroRefDestroy slow-path).  Same
> workload (gen2 self-compiling `cmd/bnc`) as the previous
> follow-up, run at `--cflag -O2`.  See "Open issue" below for
> why `-g` was dropped.

## TL;DR

- **Wall barely moves** (~2.85 s → ~3.0 s, within noise — and
  arguably slightly worse, plausibly within the noise floor of
  clang invocation timing on this machine).
- **`bn_rt__RefDec` and `bn_rt__RefInc` are gone from the
  profile** (inlined at IR-gen time; the inlined bodies are now
  parts of whichever function holds them at LLVM-IR level, then
  fully inlined / SROA'd by `-O2`).
- **Bnc-internal share dropped ~35%** (15.5% → 12.9% of wall in
  top-of-stack-≥5 leaves).  Same direction the bni follow-up
  saw, but on a smaller absolute share — because for the
  self-compile workload, clang already dominated wall.
- The original follow-up's prediction holds: "Modest absolute
  wall cut (~5–10%)" was if anything optimistic.  Wall is
  essentially flat; the bnc-self portion of CPU did improve.

## Numbers (warm runs, n = 3)

| Variant | Wall | User CPU |
|--|--:|--:|
| v3 (notes-profiling-bnc-followup-2026-04-29: -O2 + -g, OP_REFDEC NOT yet inlined) | ~2.85 s | ~2.0 s |
| **v4 (this run: -O2, no -g, OP_REFDEC inlined)** | **~3.0 s** | **~2.08 s** |

The ~5% wall regression is within the variance of repeated runs
and is most plausibly noise — the v4 user CPU is also flat.
What actually changed is the *internal* breakdown.

## Profile shape (v4, n = 2242 samples)

```
        __wait4                  1719  (76% — clang waiting)
        bn_rt__BoundsCheck        105  (4.7%)
        __fork                     66  (2.9%)
        bn_buf__CharBuf__WriteStr  34  (1.5%)
        bn_buf__grow               24  (1.1%)
        bn_buf____copy_CharBuf     16  (0.7%)
        bn_codegen__emitCall       13  (0.6%)
        bn_buf____dtor_CharBuf     12
        bn_main__writeFile         11
        DYLD-STUB$$bn_buf____copy_CharBuf  10
        bn_buf__CharBuf__WriteByte  9
        bn_ir____dtor_FuncSig       9
```

Notable absences:
- **`bn_rt__RefDec`: 0 samples** (was 187 = 6.6% wall at v3).
- **`bn_rt__RefInc`: 0 samples** (was 59 = 2.1% wall at v3).
- **`bn_rt__headerPtr`: 0 samples** (the inlined sequences read
  the header inline rather than calling the helper).

What DOES show up under `bn_codegen__emitRefDecInline` /
`bn_codegen__emitRefIncInline` is *bnc-the-compiler's IR-gen for
the inline sequences* — not the runtime work itself.  These are
new functions in the codegen layer and account for ~10 samples
total; they're the cost of generating the inlined refdec/refinc,
not running it.

## Side-by-side, v3 → v4

| Function | v3 (samples / % wall) | v4 (samples / % wall) |
|---|---:|---:|
| `__wait4`+`__fork` | ~80% | **~79%** |
| `bn_rt__RefDec` | 187 / 6.6% | **0** |
| `bn_rt__RefInc` | 59 / 2.1% | **0** |
| `bn_rt__BoundsCheck` | 100 / 3.5% | 105 / 4.7% |
| `bn_buf__grow` | 29 / 1.0% | 24 / 1.1% |
| `bn_buf__CharBuf__WriteStr` | 16 / 0.6% | 34 / 1.5% |
| Bnc-internal total share | ~20% | **~13%** |

The increase in WriteStr's share (1.5% vs 0.6%) probably reflects
that intervening commits (function-values work, some IR
restructuring) added more emission paths, not a regression in
WriteStr itself — its absolute sample count is comparable.

## Read

The original follow-up's read holds even more strongly now:
**bnc source-level optimization at this workload has hit
diminishing returns**, and the OP_REFDEC inline win is real but
invisible at the wall-clock level because it lands on the
~20% bnc-self slice while clang owns the other ~80%.

The remaining bnc-self time is now almost entirely **`buf.CharBuf`
churn** (WriteStr + grow + copy/dtor) plus **`BoundsCheck`** at
4.7%.  The CharBuf churn is what we'd expect from "the compiler
builds a lot of strings"; the BoundsCheck residue is the same
shape we see in bni — would be reduced by IR-level range
analysis.

For the workload that motivates this profile (test speeds): the
bnc share of test runtime is small relative to clang invocations,
so the bigger lever for test speed is the **bni** OP_REFDEC win
(see `notes-profiling-bni-followup-2026-05-01.md` — that one's
~19% wall) and longer-term incremental compilation / build cache
to address the clang share.

## Open issue: bnc emits invalid LLVM IR with `-g` after OP_REFDEC inline

Building with `bnc -g ...` (debug info) currently fails clang at
the link / compile step with:

```
error: expected instruction opcode
 ri.0.skip:, !dbg !DILocation(line: 179, scope: !12)
           ^
```

The OP_REFDEC inline lowering ends a sequence with a basic-block
label (`ri.0.skip:`).  Then `addDbgToLastLine` in
`pkg/codegen/emit_debug.bn` appends `!dbg !DILocation(...)` to
"the last line" — including label lines — producing invalid IR.
Repro: any source that exercises OP_REFDEC, built with `-g`.

This ran against the same workload as v3, so I dropped `-g` for
this profile.  `sample` resolves function symbols from the macho
symbol table (not debug info), so function-level breakdown is
unaffected; only source-line attribution is lost.

Worth filing as a separate todo / bug; surfaced by this profiling
exercise but unrelated to the inline lowering's correctness.

## Reproducing

Same recipe as the previous follow-up, with `-g` removed from the
gen1 + gen2 builds.  When the `-g` bug is fixed, future profiles
should restore `-g` for source-line attribution.
