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

Not yet implemented in bni (plan step 4).

## Method

`perf/vm-pass-costs.py --bni <bni> --bench <benchmarks-repo>/bench [--rounds N]` (binate repo).

- **Load cost:** bni loading cmd/bnc (`-main-dir cmd/bnc -- --version`): parse + check + IR-gen +
  passes + lowering of the largest program we have; the run itself is trivial.
- **Run benefit:** the benchmarks repo's 8 programs at VM-sized inputs (~1-4 s at -O 0):
  binary-trees 11, fannkuch-redux 8, fasta 25000, mandelbrot 300, n-body 30000, record-churn 800,
  richards 50, spectral-norm 250. (These include each program's own, small, load.)
- **Configs:** O0; O2; `cum:<p>` = the passes up to <p> in pipeline order; `loo:<p>` = O2 minus <p>.
- User CPU (wait4 rusage), all configs interleaved, order reversed on alternate rounds, median of
  3 rounds; every run's output checked against O0 (BAD = mismatch).

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

## Per-pass reading

| pass | load cost (cmd/bnc, O0 = 4.15 s) | run benefit | VM set? |
|---|---|---|---|
| inline | +0.65 s | none alone (slightly slower); with sroa/mem2reg: record-churn 0.44 vs 1.22 (loo) | no |
| sroa | +1.6 s | record-churn only (loo: 0.86 vs 0.44) | no |
| mem2reg | +1.3 s | large: fannkuch 0.68, record-churn 0.53, spectral 0.63, mandelbrot 0.81 | yes |
| dead-phi | +0.2 s | small, broad (record-churn 0.49, spectral 0.60, richards 0.92) | yes |
| load-fwd | +1.1 s | large: fannkuch 0.41, n-body 0.54, fasta 0.60, richards 0.76, binary-trees 0.80 | yes |
| field-load-fwd | ~+0.2 s | small (richards 0.69 vs 0.76) | yes |
| simplify | ~+0.3 s | small | yes |
| div-check-elim | ~0 (noise) | small (spectral) | yes |
| bce-const | ~0 (noise) | small | yes |
| bce-loop | ~0 (noise) | n-body 0.45 (from 0.51), spectral 0.41 | yes |
| bce-redundant | ~0 (noise) | n-body 0.37 (from 0.45) | yes |
| licm | ~0 (noise) | none measurable | no |
| fuse-madd | ~0 (noise) | none measurable | no |

Decision rule used (stated after the fact — make it explicit next time): a pass is in if its
run-time benefit is broad (several benchmarks) or large, and its load cost on the big-program
load is small; a pass whose load cost is large and whose benefit is confined to one code shape
(inline + sroa → record-churn) stays out. Revisit if the VM's intended workloads shift toward
long-running compute, where inline + sroa could pay for their load cost.

## History

- 2026-09-25: first measurement (above); tentative set accepted.
