# VM optimization pass set — measurements and decisions (living document)

Which IR optimization passes the bytecode VM (`bni`) runs, and why. Under bni the passes run on
**every load** (there is no ahead-of-time step), so a pass belongs in the VM set only if the run
time it saves outweighs the load time it adds. Compiled code (`bnc -O1+`) runs every pass; the
tradeoff there is different (the cost is paid once).

**Keep this document current.** When a pass is added to iropt (or one changes materially): run
`perf/vm-pass-costs.py`, add a row to the per-pass table below, and decide — here, with the numbers
— whether the VM runs it. A new pass is off for the VM until measured.

Context: [plan-vm-pass-set.md](plan-vm-pass-set.md) (steps 3-5); todo entry "VM runs a
user-selectable -On …" in [claude-todo.md](claude-todo.md).

## Current VM pass set

**Accepted (user, 2026-09-25), tentative:** mem2reg, dead-phi, load-fwd, field-load-fwd, simplify,
div-check-elim, bce-const, bce-loop, bce-redundant.

**Excluded:** inline, sroa (together ~+2.2 s = +50% on a large load; their benefit is concentrated
in small-by-value-struct code — record-churn), licm, fuse-madd (no measurable VM benefit).
The REPL excludes inline regardless (redefinition semantics; plan-vm-pass-set.md).

Implemented: `iropt.VMOptConfig()` (binate `4ff351ea`); bni applies `-f` / `-fno` on top of it.

## Method

`perf/vm-pass-costs.py --bni <bni> --bench <benchmarks-repo>/bench [--rounds N]` (binate repo).

- **Load cost:** bni loading cmd/bnc (`-main-dir cmd/bnc -- --version`): parse + check + IR-gen +
  passes + lowering of the largest program we have; the run itself is trivial.
- **Run benefit:** the benchmarks repo's 8 programs at VM-sized inputs (~1-4 s at -O 0):
  binary-trees 11, fannkuch-redux 8, fasta 25000, mandelbrot 300, n-body 30000, record-churn 800,
  richards 50, spectral-norm 250. (These include each program's own, small, load.)
- **Configs:** O0; O2; `cum:<p>` = the passes up to <p> in pipeline order; `loo:<p>` = O2 minus <p>.
- User CPU (wait4 rusage), all configs interleaved, order reversed on alternate rounds, median of
  the rounds with the min-max range beside it; every run's output checked against O0 (BAD =
  mismatch or nonzero exit).
- **Load cost, deterministic:** `--instructions --only load:cmd/bnc --jobs N` counts instructions
  executed under callgrind instead of timing; the `cum:` rows' step is that pass's cost given the
  passes before it. Resolves the cheap passes that the timed load cannot (±0.3 s noise).
- `--log F --resume` continues an interrupted measurement (a container restart kills it otherwise).

## Results — 2026-09-27 (binate `c6c3b430`, perf script `2f336df5`; bni built at bnc -O2; x86-64 VM)

The first measurement's `loo:mem2reg` rows were a miscompile (load-forwarding without mem2reg, since
fixed, binate `171df689`); passes now also run one function at a time and phi operands / field
offsets changed (binate `4ff351ea`, `ab2e981a`, `7102e88c`), so everything was remeasured. All runs
clean (no BAD).

**Load cost:** instructions executed by bni loading cmd/bnc (billions; ratio to O0; `cum:` step).
At O0 this load takes ~4.2 s, so 1G instructions ≈ 0.2 s.

| config | load:cmd/bnc |
|---|---|
| O0 | 20.52G (1.000) |
| VM | 30.68G (1.495) |
| O2 | 38.97G (1.899) |
| cum:inline | 22.94G (1.118) +2.42 |
| cum:sroa | 26.82G (1.307) +3.88 |
| cum:mem2reg | 31.62G (1.541) +4.79 |
| cum:dead-phi | 32.07G (1.563) +0.45 |
| cum:load-fwd | 35.97G (1.753) +3.90 |
| cum:field-load-fwd | 36.82G (1.795) +0.86 |
| cum:simplify | 37.54G (1.829) +0.71 |
| cum:div-check-elim | 37.56G (1.830) +0.02 |
| cum:bce-const | 37.58G (1.832) +0.02 |
| cum:bce-loop | 37.90G (1.847) +0.32 |
| cum:bce-redundant | 38.22G (1.863) +0.32 |
| cum:licm | 38.81G (1.891) +0.59 |
| cum:fuse-madd | 38.97G (1.899) +0.16 |
| loo:inline | 35.80G (1.744) |
| loo:sroa | 34.30G (1.672) |
| loo:mem2reg | 34.63G (1.688) |
| loo:dead-phi | 38.56G (1.879) |
| loo:load-fwd | 34.39G (1.676) |
| loo:field-load-fwd | 38.11G (1.857) |
| loo:simplify | 38.26G (1.864) |
| loo:div-check-elim | 38.95G (1.898) |
| loo:bce-const | 38.95G (1.898) |
| loo:bce-loop | 38.66G (1.884) |
| loo:bce-redundant | 38.65G (1.883) |
| loo:licm | 38.38G (1.870) |
| loo:fuse-madd | 38.81G (1.891) |

**Run benefit:** median user seconds over 2 rounds [min-max] (ratio to O0). The cmd/bnc load was
not timed this time (the counts above replace it). The O0 row's range is the noise floor: ~±2-5%.

| config | binary-trees | fannkuch-redux | fasta | mandelbrot | n-body | record-churn | richards | spectral-norm |
|---|---|---|---|---|---|---|---|---|
| O0 | 1.43s [1.40-1.46] (1.00) | 0.69s [0.67-0.71] (1.00) | 0.75s [0.74-0.75] (1.00) | 1.40s [1.39-1.42] (1.00) | 1.96s [1.93-2.00] (1.00) | 1.11s [1.10-1.12] (1.00) | 1.77s [1.69-1.86] (1.00) | 1.97s [1.89-2.05] (1.00) |
| VM | 1.12s [1.11-1.13] (0.78) | 0.24s [0.24-0.25] (0.35) | 0.41s [0.41-0.42] (0.56) | 1.02s [1.00-1.04] (0.73) | 0.72s [0.70-0.74] (0.37) | 1.06s [1.00-1.12] (0.95) | 1.23s [1.21-1.25] (0.69) | 0.90s [0.90-0.90] (0.46) |
| O2 | 1.09s [1.08-1.09] (0.76) | 0.24s [0.23-0.24] (0.34) | 0.38s [0.37-0.38] (0.51) | 0.94s [0.92-0.97] (0.67) | 0.73s [0.70-0.76] (0.37) | 0.57s [0.53-0.61] (0.52) | 1.25s [1.19-1.32] (0.71) | 0.95s [0.92-0.97] (0.48) |
| cum:inline | 1.52s [1.43-1.62] (1.07) | 0.68s [0.66-0.71] (0.99) | 0.74s [0.74-0.75] (0.99) | 1.36s [1.31-1.40] (0.97) | 1.83s [1.81-1.86] (0.93) | 1.15s [1.15-1.15] (1.04) | 1.75s [1.74-1.75] (0.98) | 1.98s [1.93-2.02] (1.00) |
| cum:sroa | 1.46s [1.37-1.55] (1.02) | 0.64s [0.64-0.65] (0.93) | 0.80s [0.75-0.84] (1.07) | 1.49s [1.32-1.65] (1.06) | 1.93s [1.85-2.02] (0.99) | 2.05s [2.04-2.06] (1.85) | 1.69s [1.68-1.69] (0.95) | 1.96s [1.95-1.97] (0.99) |
| cum:mem2reg | 1.31s [1.28-1.34] (0.92) | 0.51s [0.49-0.53] (0.74) | 0.63s [0.60-0.66] (0.85) | 1.15s [1.15-1.16] (0.82) | 1.70s [1.66-1.75] (0.87) | 0.67s [0.61-0.74] (0.61) | 1.64s [1.63-1.66] (0.93) | 1.24s [1.16-1.31] (0.63) |
| cum:dead-phi | 1.37s [1.37-1.37] (0.96) | 0.51s [0.50-0.51] (0.74) | 0.64s [0.63-0.66] (0.86) | 1.13s [1.10-1.17] (0.81) | 1.59s [1.58-1.59] (0.81) | 0.59s [0.58-0.61] (0.54) | 1.71s [1.67-1.76] (0.96) | 1.33s [1.23-1.43] (0.68) |
| cum:load-fwd | 1.11s [1.11-1.12] (0.78) | 0.30s [0.29-0.31] (0.44) | 0.45s [0.44-0.46] (0.60) | 0.96s [0.95-0.97] (0.69) | 0.98s [0.95-1.01] (0.50) | 0.56s [0.56-0.57] (0.51) | 1.38s [1.37-1.39] (0.78) | 1.10s [1.04-1.16] (0.56) |
| cum:field-load-fwd | 1.11s [1.11-1.11] (0.78) | 0.35s [0.34-0.37] (0.51) | 0.48s [0.47-0.48] (0.64) | 0.95s [0.94-0.95] (0.67) | 0.98s [0.94-1.01] (0.50) | 0.60s [0.58-0.63] (0.55) | 1.17s [1.14-1.20] (0.66) | 1.13s [1.13-1.13] (0.58) |
| cum:simplify | 1.21s [1.08-1.34] (0.84) | 0.32s [0.29-0.34] (0.46) | 0.43s [0.41-0.44] (0.57) | 0.95s [0.94-0.95] (0.67) | 0.98s [0.95-1.02] (0.50) | 0.57s [0.55-0.58] (0.51) | 1.20s [1.15-1.26] (0.68) | 1.10s [1.05-1.15] (0.56) |
| cum:div-check-elim | 1.10s [1.06-1.13] (0.77) | 0.29s [0.29-0.30] (0.43) | 0.44s [0.44-0.44] (0.59) | 0.97s [0.97-0.98] (0.70) | 1.03s [1.02-1.04] (0.53) | 0.54s [0.54-0.54] (0.49) | 1.22s [1.19-1.26] (0.69) | 0.98s [0.97-0.98] (0.50) |
| cum:bce-const | 1.10s [1.10-1.11] (0.77) | 0.29s [0.29-0.30] (0.43) | 0.44s [0.42-0.46] (0.59) | 1.00s [0.96-1.05] (0.72) | 0.96s [0.93-0.98] (0.49) | 0.57s [0.54-0.59] (0.51) | 1.25s [1.18-1.33] (0.71) | 0.96s [0.96-0.97] (0.49) |
| cum:bce-loop | 1.14s [1.13-1.14] (0.80) | 0.28s [0.28-0.28] (0.41) | 0.45s [0.43-0.47] (0.61) | 1.03s [1.02-1.03] (0.73) | 0.83s [0.80-0.86] (0.42) | 0.54s [0.54-0.54] (0.49) | 1.20s [1.20-1.21] (0.68) | 0.92s [0.89-0.95] (0.47) |
| cum:bce-redundant | 1.10s [1.10-1.11] (0.77) | 0.26s [0.24-0.28] (0.38) | 0.44s [0.43-0.46] (0.60) | 1.00s [0.96-1.04] (0.71) | 0.70s [0.70-0.70] (0.36) | 0.56s [0.55-0.57] (0.50) | 1.22s [1.18-1.26] (0.69) | 0.89s [0.88-0.89] (0.45) |
| cum:licm | 1.13s [1.07-1.18] (0.79) | 0.24s [0.23-0.24] (0.35) | 0.41s [0.39-0.43] (0.55) | 0.97s [0.92-1.03] (0.70) | 0.71s [0.71-0.71] (0.36) | 0.59s [0.58-0.61] (0.54) | 1.26s [1.23-1.29] (0.71) | 0.92s [0.90-0.94] (0.47) |
| cum:fuse-madd | 1.19s [1.07-1.32] (0.84) | 0.27s [0.23-0.31] (0.40) | 0.43s [0.40-0.46] (0.58) | 0.94s [0.93-0.96] (0.67) | 0.72s [0.70-0.73] (0.37) | 0.55s [0.53-0.57] (0.50) | 1.23s [1.22-1.25] (0.69) | 0.90s [0.89-0.90] (0.46) |
| loo:inline | 1.08s [1.07-1.09] (0.75) | 0.23s [0.23-0.24] (0.34) | 0.46s [0.45-0.48] (0.62) | 0.93s [0.93-0.93] (0.66) | 0.71s [0.68-0.75] (0.36) | 1.51s [1.49-1.53] (1.36) | 1.19s [1.15-1.23] (0.67) | 0.94s [0.93-0.95] (0.48) |
| loo:sroa | 1.08s [1.04-1.12] (0.76) | 0.24s [0.24-0.24] (0.35) | 0.42s [0.40-0.43] (0.56) | 1.02s [0.99-1.05] (0.73) | 0.72s [0.71-0.72] (0.37) | 1.01s [0.97-1.05] (0.92) | 1.20s [1.18-1.21] (0.67) | 0.94s [0.93-0.95] (0.48) |
| loo:mem2reg | 1.11s [1.08-1.14] (0.77) | 0.63s [0.61-0.66] (0.92) | 0.56s [0.51-0.60] (0.75) | 1.30s [1.29-1.31] (0.93) | 1.19s [1.13-1.24] (0.60) | 2.02s [1.95-2.10] (1.83) | 1.25s [1.23-1.28] (0.71) | 1.22s [1.19-1.24] (0.62) |
| loo:dead-phi | 1.17s [1.09-1.24] (0.82) | 0.24s [0.24-0.25] (0.35) | 0.45s [0.42-0.48] (0.61) | 1.00s [0.94-1.05] (0.71) | 0.69s [0.67-0.71] (0.35) | 0.62s [0.60-0.65] (0.56) | 1.24s [1.24-1.25] (0.70) | 0.91s [0.87-0.94] (0.46) |
| loo:load-fwd | 1.37s [1.32-1.42] (0.96) | 0.48s [0.46-0.49] (0.69) | 0.62s [0.61-0.63] (0.83) | 1.09s [1.08-1.10] (0.78) | 1.64s [1.61-1.66] (0.84) | 0.60s [0.59-0.60] (0.54) | 1.67s [1.66-1.68] (0.94) | 1.12s [1.12-1.12] (0.57) |
| loo:field-load-fwd | 1.16s [1.12-1.20] (0.81) | 0.25s [0.24-0.26] (0.36) | 0.45s [0.40-0.50] (0.60) | 0.95s [0.95-0.96] (0.68) | 0.74s [0.69-0.79] (0.38) | 0.55s [0.52-0.57] (0.49) | 1.41s [1.38-1.44] (0.79) | 0.87s [0.84-0.90] (0.44) |
| loo:simplify | 1.12s [1.11-1.13] (0.78) | 0.25s [0.24-0.26] (0.36) | 0.40s [0.40-0.40] (0.54) | 1.04s [0.92-1.16] (0.74) | 0.72s [0.70-0.74] (0.37) | 0.55s [0.52-0.57] (0.49) | 1.28s [1.21-1.35] (0.72) | 0.87s [0.85-0.89] (0.44) |
| loo:div-check-elim | 1.12s [1.09-1.16] (0.79) | 0.26s [0.24-0.29] (0.38) | 0.44s [0.42-0.45] (0.58) | 1.03s [0.93-1.13] (0.74) | 0.78s [0.74-0.82] (0.40) | 0.53s [0.53-0.53] (0.48) | 1.22s [1.20-1.24] (0.69) | 0.97s [0.96-0.98] (0.49) |
| loo:bce-const | 1.14s [1.13-1.14] (0.80) | 0.24s [0.23-0.25] (0.35) | 0.46s [0.42-0.50] (0.62) | 0.92s [0.91-0.93] (0.66) | 0.70s [0.69-0.71] (0.36) | 0.54s [0.54-0.54] (0.49) | 1.31s [1.21-1.41] (0.74) | 0.88s [0.88-0.89] (0.45) |
| loo:bce-loop | 1.07s [1.07-1.08] (0.75) | 0.27s [0.24-0.30] (0.39) | 0.46s [0.45-0.48] (0.62) | 0.96s [0.93-0.99] (0.68) | 0.74s [0.73-0.74] (0.38) | 0.57s [0.53-0.60] (0.51) | 1.23s [1.21-1.25] (0.69) | 1.03s [1.01-1.05] (0.53) |
| loo:bce-redundant | 1.11s [1.10-1.12] (0.78) | 0.32s [0.31-0.34] (0.47) | 0.42s [0.40-0.43] (0.56) | 0.97s [0.96-0.99] (0.70) | 0.80s [0.78-0.81] (0.41) | 0.56s [0.53-0.58] (0.50) | 1.21s [1.19-1.23] (0.68) | 0.91s [0.89-0.93] (0.46) |
| loo:licm | 1.12s [1.12-1.13] (0.79) | 0.25s [0.24-0.26] (0.37) | 0.44s [0.42-0.46] (0.59) | 0.94s [0.94-0.94] (0.67) | 0.73s [0.72-0.75] (0.37) | 0.58s [0.54-0.61] (0.52) | 1.26s [1.15-1.37] (0.71) | 0.94s [0.88-0.99] (0.48) |
| loo:fuse-madd | 1.11s [1.08-1.13] (0.77) | 0.26s [0.25-0.27] (0.38) | 0.40s [0.38-0.41] (0.54) | 0.98s [0.95-1.01] (0.70) | 0.74s [0.72-0.75] (0.38) | 0.58s [0.54-0.62] (0.53) | 1.21s [1.20-1.22] (0.68) | 0.90s [0.89-0.91] (0.46) |

## Results — 2026-09-25 (binate `d7b9f7a6` + perf script; bni built at bnc -O2; x86-64 VM)

Median user seconds (ratio to O0):

| config | load:cmd/bnc | binary-trees | fannkuch-redux | fasta | mandelbrot | n-body | record-churn | richards | spectral-norm |
|---|---|---|---|---|---|---|---|---|---|
| O0 | 4.15s (1.00) | 1.87s (1.00) | 0.87s (1.00) | 0.98s (1.00) | 1.85s (1.00) | 2.47s (1.00) | 1.60s (1.00) | 2.30s (1.00) | 2.64s (1.00) |
| O2 | 9.43s (2.27) | 1.46s (0.78) | 0.32s (0.36) | 0.54s (0.55) | 1.27s (0.69) | 0.95s (0.39) | 0.71s (0.44) | 1.61s (0.70) | 1.17s (0.44) |
| cum:inline | 4.80s (1.16) | 1.95s (1.04) | 0.87s (1.00) | 0.98s (1.00) | 1.93s (1.04) | 2.36s (0.96) | 1.69s (1.06) | 2.43s (1.06) | 2.67s (1.01) |
| cum:sroa | 6.38s (1.54) | 1.97s (1.05) | 0.89s (1.03) | 0.94s (0.96) | 1.92s (1.04) | 2.37s (0.96) | 2.76s (1.73) | 2.29s (1.00) | 2.57s (0.98) |
| cum:mem2reg | 7.65s (1.84) | 1.76s (0.94) | 0.59s (0.68) | 0.86s (0.88) | 1.51s (0.81) | 2.11s (0.86) | 0.84s (0.53) | 2.26s (0.98) | 1.66s (0.63) |
| cum:dead-phi | 7.87s (1.90) | 1.77s (0.94) | 0.59s (0.68) | 0.86s (0.87) | 1.46s (0.79) | 2.09s (0.85) | 0.78s (0.49) | 2.11s (0.92) | 1.57s (0.60) |
| cum:load-fwd | 9.01s (2.17) | 1.50s (0.80) | 0.36s (0.41) | 0.58s (0.60) | 1.31s (0.71) | 1.32s (0.54) | 0.75s (0.47) | 1.75s (0.76) | 1.37s (0.52) |
| cum:field-load-fwd | 9.19s (2.22) | 1.56s (0.83) | 0.35s (0.40) | 0.68s (0.69) | 1.32s (0.71) | 1.26s (0.51) | 0.77s (0.48) | 1.59s (0.69) | 1.40s (0.53) |
| cum:simplify | 9.49s (2.29) | 1.48s (0.79) | 0.36s (0.41) | 0.58s (0.59) | 1.24s (0.67) | 1.25s (0.51) | 0.71s (0.44) | 1.69s (0.74) | 1.43s (0.54) |
| cum:div-check-elim | 9.38s (2.26) | 1.48s (0.79) | 0.36s (0.41) | 0.56s (0.57) | 1.29s (0.70) | 1.26s (0.51) | 0.72s (0.45) | 1.65s (0.72) | 1.23s (0.47) |
| cum:bce-const | 9.59s (2.31) | 1.54s (0.82) | 0.35s (0.40) | 0.57s (0.58) | 1.28s (0.69) | 1.27s (0.52) | 0.68s (0.43) | 1.63s (0.71) | 1.23s (0.47) |
| cum:bce-loop | 9.71s (2.34) | 1.49s (0.80) | 0.36s (0.42) | 0.53s (0.55) | 1.30s (0.70) | 1.12s (0.45) | 0.71s (0.44) | 1.63s (0.71) | 1.09s (0.41) |
| cum:bce-redundant | 9.47s (2.28) | 1.45s (0.77) | 0.32s (0.37) | 0.52s (0.53) | 1.26s (0.68) | 0.92s (0.37) | 0.69s (0.43) | 1.60s (0.70) | 1.11s (0.42) |
| cum:licm | 9.82s (2.37) | 1.46s (0.78) | 0.30s (0.35) | 0.54s (0.55) | 1.29s (0.69) | 0.92s (0.37) | 0.70s (0.44) | 1.62s (0.70) | 1.10s (0.42) |
| cum:fuse-madd | 9.96s (2.40) | 1.47s (0.78) | 0.31s (0.35) | 0.55s (0.56) | 1.25s (0.67) | 0.96s (0.39) | 0.72s (0.45) | 1.59s (0.69) | 1.10s (0.42) |
| loo:inline | 9.26s (2.23) | 1.50s (0.80) | 0.31s (0.35) | 0.52s (0.53) | 1.30s (0.70) | 0.91s (0.37) | 1.95s (1.22) | 1.65s (0.72) | 1.12s (0.43) |
| loo:sroa | 8.24s (1.99) | 1.51s (0.81) | 0.31s (0.36) | 0.54s (0.56) | 1.28s (0.69) | 0.91s (0.37) | 1.37s (0.86) | 1.69s (0.73) | 1.15s (0.44) |
| loo:mem2reg | 8.87s (2.14) | 1.51s (0.81) | 0.82s (0.95) | 0.01s (0.01) BAD | 1.70s (0.92) | 0.04s (0.02) BAD | 2.74s (1.72) | 1.68s (0.73) | 0.01s (0.00) BAD |
| loo:dead-phi | 9.59s (2.31) | 1.58s (0.85) | 0.33s (0.38) | 0.57s (0.59) | 1.32s (0.71) | 0.91s (0.37) | 0.81s (0.51) | 1.70s (0.74) | 1.12s (0.43) |
| loo:load-fwd | 8.77s (2.12) | 1.83s (0.98) | 0.59s (0.68) | 0.85s (0.86) | 1.47s (0.79) | 2.10s (0.85) | 0.69s (0.43) | 2.10s (0.91) | 1.41s (0.54) |
| loo:field-load-fwd | 9.61s (2.32) | 1.50s (0.80) | 0.31s (0.35) | 0.66s (0.67) | 1.26s (0.68) | 0.91s (0.37) | 0.72s (0.45) | 1.82s (0.79) | 1.15s (0.44) |
| loo:simplify | 9.63s (2.32) | 1.49s (0.80) | 0.32s (0.37) | 0.55s (0.56) | 1.30s (0.70) | 0.93s (0.38) | 0.68s (0.43) | 1.61s (0.70) | 1.15s (0.44) |
| loo:div-check-elim | 10.04s (2.42) | 1.49s (0.79) | 0.32s (0.37) | 0.56s (0.57) | 1.31s (0.71) | 0.92s (0.37) | 0.69s (0.43) | 1.65s (0.72) | 1.19s (0.45) |
| loo:bce-const | 9.63s (2.32) | 1.53s (0.82) | 0.31s (0.35) | 0.56s (0.57) | 1.29s (0.69) | 0.93s (0.38) | 0.73s (0.46) | 1.58s (0.69) | 1.09s (0.41) |
| loo:bce-loop | 9.75s (2.35) | 1.48s (0.79) | 0.31s (0.35) | 0.56s (0.58) | 1.26s (0.68) | 0.93s (0.38) | 0.69s (0.43) | 1.63s (0.71) | 1.20s (0.46) |
| loo:bce-redundant | 9.97s (2.41) | 1.49s (0.79) | 0.36s (0.41) | 0.55s (0.56) | 1.30s (0.70) | 1.05s (0.43) | 0.70s (0.44) | 1.61s (0.70) | 1.14s (0.43) |
| loo:licm | 10.10s (2.44) | 1.48s (0.79) | 0.31s (0.36) | 0.53s (0.54) | 1.25s (0.67) | 0.94s (0.38) | 0.77s (0.48) | 1.69s (0.74) | 1.11s (0.42) |
| loo:fuse-madd | 9.64s (2.33) | 1.48s (0.79) | 0.31s (0.36) | 0.53s (0.54) | 1.26s (0.68) | 0.90s (0.36) | 0.70s (0.44) | 1.65s (0.72) | 1.14s (0.43) |

**Caveats.** Load times vary by ~±0.3 s between runs, so small per-pass load costs (< ~0.3 s) are
not resolved; hygiene ran briefly during the run (extra noise). The `loo:mem2reg` BAD rows are a
miscompile (load-forwarding without mem2reg; MAJOR in claude-todo.md), not a measurement — rerun
after the fix. Next measurement should add deterministic per-pass load costs (callgrind
instruction counts of the cmd/bnc load per `cum:` step) to resolve the cheap passes.

## Per-pass reading (2026-09-27)

Load cost: the `cum:` step in instructions (≈ seconds at 0.2 s/G). Benefit: `loo:<p>` (O2 minus
the pass) against O2, i.e. what dropping the pass from the full set loses, with `cum:` for context.

| pass | load cost | run benefit (loo vs O2) | VM set? |
|---|---|---|---|
| inline | +2.42G (~0.5 s) | record-churn only (1.36 vs 0.52); nothing elsewhere | no |
| sroa | +3.88G (~0.8 s) | record-churn only (0.92 vs 0.52) | no |
| mem2reg | +4.79G (~1.0 s) | large, broad: record-churn 1.83, fannkuch 0.92 vs 0.34, n-body 0.60 vs 0.37, mandelbrot 0.93 vs 0.67, fasta 0.75 vs 0.51 | yes |
| dead-phi | +0.45G (~0.09 s) | small: binary-trees 0.82 vs 0.76, record-churn 0.56 vs 0.52 | yes |
| load-fwd | +3.90G (~0.8 s) | large, broad: n-body 0.84, binary-trees 0.96, fannkuch 0.69, fasta 0.83, richards 0.94, spectral 0.57 vs 0.48 | yes |
| field-load-fwd | +0.86G (~0.17 s) | richards 0.79 vs 0.71 | yes |
| simplify | +0.71G (~0.14 s) | none resolved (every loo within noise of O2) | yes — see below |
| div-check-elim | +0.02G | spectral (cum 0.56 → 0.50) | yes |
| bce-const | +0.02G | none resolved | yes (free) |
| bce-loop | +0.32G (~0.06 s) | n-body (cum 0.49 → 0.42), spectral 0.53 vs 0.48 | yes |
| bce-redundant | +0.32G (~0.06 s) | n-body 0.41 vs 0.37 (cum 0.42 → 0.36), fannkuch 0.47 vs 0.34 | yes |
| licm | +0.59G (~0.12 s) | fannkuch only (cum 0.38 → 0.35); loo within noise | no |
| fuse-madd | +0.16G (~0.03 s) | none | no |

The current set's load is 1.50× O0 (30.7G vs 20.5G instructions); all passes 1.90×. Its run time
matches O2 on every benchmark except record-churn (0.95 vs 0.52, the inline + sroa effect) and
mandelbrot (0.73 vs 0.67).

**Open question (not changed):** simplify costs ~3.5% of the O0 load and shows no run benefit these
benchmarks resolve. It may still pay on code they don't exercise (it folds what the other passes
expose); decide whether to keep it or drop it. licm stays out by the same reading (+0.59G, benefit
only on fannkuch).

Decision rule used (stated after the fact — make it explicit next time): a pass is in if its
run-time benefit is broad (several benchmarks) or large, and its load cost on the big-program
load is small; a pass whose load cost is large and whose benefit is confined to one code shape
(inline + sroa → record-churn) stays out. Revisit if the VM's intended workloads shift toward
long-running compute, where inline + sroa could pay for their load cost.

## History

- 2026-09-25: first measurement (above); tentative set accepted.
- 2026-09-27: remeasured after the load-fwd miscompile fix, with deterministic (callgrind) load
  costs; the set stands. Open: simplify (no resolved benefit).
