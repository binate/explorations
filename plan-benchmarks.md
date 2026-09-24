# Plan: cross-language benchmark suite

Repo: **github.com/binate/benchmarks** (workspace submodule `benchmarks/`).

Cross-language performance benchmarks for Binate. Each benchmark is one problem
implemented in Binate and in peer languages (C, C++, Rust, Go, Java, Python).
`scripts/run.sh <bench> [langs]` builds, runs, times, and cross-checks every
implementation. The two Binate rows — the same source compiled `--backend
native` vs `--backend llvm` — are the point; the peers are the external
yardstick. Feeds the native↔LLVM gap work (see the Performance section of
`claude-todo.md`).

**Status:** all 7 planned benchmarks landed (spectral-norm, n-body, mandelbrot,
binary-trees, fannkuch-redux, fasta, richards), plus record-churn (the
aggregate-copy probe). Further benchmarks can be added a couple at a time.

For the native/LLVM ratio itself use `scripts/native-vs-llvm.sh` (interleaved,
order-alternating rounds on user CPU time, with an A/A `--self` noise-floor
mode) rather than `run.sh`'s sequential wall-clock rows.

## Benchmarks

- [x] **spectral-norm** — FP power-method on AᵀA; pinned N=5500. First gap data
      point captured (native markedly slower than llvm on this division-heavy
      loop; run the suite for current figures — do not record numbers here, they
      go stale).
- [x] **n-body** — scalar FP, loop-carried dependencies; pinned N=5000000
- [x] **mandelbrot** — complex FP, vectorizable inner loop; pinned N=1000, byte-exact P4 output
- [x] **binary-trees** — allocation / refcount path; pinned N=16, exact compare
- [x] **fannkuch-redux** — integer, array indexing; pinned N=11, exact compare
- [x] **fasta** — deterministic RNG + buffered output; pinned N=1000 (== CLBG reference), exact compare
- [x] **record-churn** — aggregate-copy probe (8-field uint32 records through
      managed slices, serial passes); pinned N=8000, exact compare
- [x] **richards** — interface/vtable dispatch axis (Richards OS scheduler);
      pinned N=10000 iterations, exact compare (canonical counts queue 2322 hold 928)

## Notes

- Binate entries build against the pinned BUILDER (`BUILDER_VERSION` +
  `scripts/fetch-builder.sh`, mirroring the examples repo); backend chosen with
  `--backend`. Peer toolchains are used if present and skipped if not.
- Same algorithm and operation order across languages; outputs cross-checked
  with a 1e-8 floating-point tolerance (last-ULP FMA differences allowed, real
  divergence fails the run).
- Implementations are written from the Computer Language Benchmarks Game problem
  specs, not copied from its licensed source. MIT licensed.
