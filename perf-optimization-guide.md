# Working on performance optimizations

How to do optimization work on this project: what counts as a result, when to
keep going, and how to measure so that the number means something.

Companion to `binate-coding-guide.md` (how to write the code) and
`ir-backend-guidelines.md` (where the code goes). Most optimization work here
serves the native↔LLVM codegen gap, which CLAUDE.md establishes as a first-class
problem to be narrowed — but the practice below applies to any optimization.

## 1. Measure carefully; small improvements are the point

**A 1% improvement is a result, not a failure.** Performance is won 1% — or
0.1% — at a time, and an optimization that lands a small, real, reproducible
improvement is worth having. Do not discard a working optimization because the
number was smaller than you hoped, and do not describe such a result as
disappointing or as "not worth landing." Ten of them is a 10% improvement, and
there is no other way to get one.

The direct consequence is that **your measurement precision has to be better
than the effect you are looking for.** A method whose run-to-run noise is ±5%
cannot see a 1% improvement — it can only produce a random number that you will
then over-interpret in whichever direction you already believed. So before
measuring anything, establish what the noise floor of your setup actually is:
measure the *same* binary against *itself* several times and look at the spread.
If that spread is larger than the effect you are chasing, fix the method (§4)
before drawing any conclusion from an A/B.

This cuts both ways. A "3% improvement" from a method with 5% noise is not a 3%
improvement, and landing it on that evidence is landing a coin flip. Precision
is what makes a small win real, and it is also what stops a non-win from
masquerading as one.

## 2. An underperforming optimization is a question, not a verdict

If an optimization does not perform as well as expected, **do not be quick to
abandon it.** The shortfall is information, and the most valuable thing you can
do next is find out *why* — investigate the disassembly, count the instructions,
check what actually got emitted.

Almost always the answer is one of a small set, and they call for completely
different responses:

- **It never fired.** A gate rejected it, a pattern did not match, a
  precondition was not met on the hot path. Extremely common, and invisible from
  timings alone. Check first: instrument the pass, or diff the disassembly of
  the hot function with and without the change. If the two disassemblies are
  identical, nothing you measured had anything to do with your optimization.
- **It fired and was undone.** A later pass re-materialized what you eliminated,
  or the win is real but a neighbouring cost grew to match it.
- **It fired, but the cost model was wrong** — the thing you optimized was not
  where the time goes, or the saving is genuinely smaller than the overhead it
  introduces.
- **The benchmark does not exercise it.** The optimization is fine; you are
  measuring a workload that does not hit the path.

Only the third is a real negative result, and it is only conclusive once you can
say *why* the cost model was wrong. That distinction is the whole value of the
investigation: "it didn't help, moving on" leaves the question open forever and
guarantees someone retries the same lever later, whereas a root-caused negative
result closes the door permanently and redirects the effort.

The register-allocator interval-splitting work (`plan-native-regalloc.md`, Stage
6) is the model here: three rounds of measurement, a definitive ~3.5%
instruction-count regression, and — the part that mattered — a root cause (the
save/restore overhead, especially the RefDec slow path, exceeds the reloads a
home avoids, in a refcounting language where that path runs constantly). That
turned "this didn't work" into "this *form* of interval splitting cannot work
for Binate," which is a permanent finding.

**Disassemble early.** This mirrors the debugging rule in CLAUDE.md: a concrete
disassembly of the two builds answers in minutes what a chain of theory-driven
rebuild cycles will not answer at all. Build a known-good variant, disassemble
the same function from both, and read what differs.

## 3. Do not thrash — follow through on the work you started

This is the failure mode to watch for in yourself, and it is the one that costs
the most.

**You (Claude) always think the grass is greener on the other side** — that some
*other* optimization is a bigger lever for less effort, and that the right move
is to pivot to it. But when you pivot, exactly the same thing happens: the
quick, easy work does not yield the expected results either, and you propose
pivoting again. The net output of a session spent that way is several
half-finished optimizations, no landed improvement, and no conclusive negative
results — strictly worse than having finished the first one, whichever way it
turned out.

So: **follow through.** Decide up front what "done" means for the lever you are
on, and reach it. Done is one of exactly two things:

1. the optimization lands (however small the improvement), or
2. it produces a **conclusive negative result** — root-caused per §2 and
   written down, so nobody retries it.

"I measured it, it was disappointing, here is a more promising idea" is neither.

Tells that you are about to thrash:

- A pivot proposal arrives immediately after a disappointing measurement,
  without an intervening investigation of *why* the measurement disappointed.
- You are estimating the payoff of the new idea optimistically and the remaining
  cost of the current one pessimistically — while having equally weak evidence
  for both.
- The new idea is described as "a bigger lever for less effort." You have
  believed this about every lever, including the one you are on now, at the
  moment you started it.
- You have more than one optimization in flight and none of them finished.

If you genuinely believe the current lever is a dead end, that belief has to be
*cashed out* as a §2 root cause before you move — which is itself a deliverable.
And if the reason to switch is something other than the evidence (the user's
priorities, a blocker), then it is a scope decision, so surface it and let the
user choose rather than pivoting unilaterally.

## 4. Measurement practice

### Interleave A/B runs

On a machine with varying load — which this one is, with concurrent worker
sessions — never time all of A and then all of B. The load during the A block
and the B block will differ, and that difference lands entirely in your result.
Interleave: A, B, A, B, … within a single run, so both arms see the same load
over the same interval. Report best and median across rounds.

### Measure user CPU time, not wall clock

Wall clock includes everything else happening on the box. User CPU time
(`/usr/bin/time -p`, the `user` line) is much more robust to a loaded machine.
Use it as the default metric; do not draw conclusions from wall clock.

Note that `perf/native-vs-llvm.sh` currently times **wall clock**
(`Time::HiRes`), so its numbers on a loaded box are noisier than a user-CPU
driver's.

### Thermal throttling: vary the A/B order

Interleaving fixes varying *load*, but not thermal throttling: a machine that
has been busy for several minutes is slower than the same machine at the start
of a run, so whichever arm runs later in each round is systematically penalized.
Fixed-order interleaving (always A then B) does not remove this — it converts it
into a consistent bias, which is worse than noise because it looks like a
result.

So **alternate the order across rounds**: A,B then B,A then A,B … Then a
monotonic thermal drift affects both arms equally and cancels in the average.
(`perf/native-vs-llvm.sh` interleaves but does not currently alternate the
order.)

### On Apple Silicon, prefer instructions retired

The noise-immune metric on this hardware is **instructions retired**, reported by
`/usr/bin/time -l`. It is independent of load, clock frequency, and preemption,
so it survives conditions that make even user CPU time unusable — on a heavily
loaded shared box, user-CPU comparisons of near-identical binaries have swung by
±0.5s of pure noise, while the instruction counts were stable and reproducible
to a few tenths of a percent (`plan-native-regalloc.md`, Stage 6).

It is a proxy, not the truth — it is blind to cache behaviour, branch
misprediction, and instruction latency, so an optimization that replaces a
long-latency `sdiv` with a cheaper sequence can *raise* the instruction count
while making the code faster. Use it as the primary signal when it agrees with
the intent of the change, and fall back to careful user-CPU timing when the
change is specifically about latency or memory behaviour.

### Build the thing you think you are measuring

The most embarrassing failure mode is a carefully-executed measurement of the
wrong binary. Check, every time:

- **Did both arms get rebuilt?** A stale binary is the classic source of a
  suspiciously clean "no change."
- **Are the flags identical apart from the variable under test?** Both arms at
  `-O2`, same bootstrap compiler, same runtime — `perf/native-vs-llvm.sh` builds
  L and N from the same tree with the same gen1 and the same rt precisely so the
  only difference left is codegen quality.
- **Are you measuring the target you changed?** `--backend native` on an arm64
  box builds *aarch64*, so an x64 or arm32 codegen change looks falsely neutral.
  Use `--arch KEY` for a cross target, which reports static code-quality metrics
  (`__text` size, reload / address-recompute counts) instead of wall clock —
  running a cross target under Rosetta or qemu is an unreliable proxy that hides
  the very gap you are measuring. The same "the `native` in the name is
  load-bearing" trap that CLAUDE.md documents for conformance modes applies
  here.
- **Is the work actually in-process?** A timed `bnc` run that shells out to
  clang is largely measuring clang. The default
  `--backend native --linker bnld` keeps the work in-process so the number
  reflects the binary's own code.

### Automate it, and commit the automation

If a measurement is worth running twice, write a script for it — and **commit
it**, so the next session (and the user) can reproduce the number rather than
re-deriving the method. Half of the traps above are exactly the kind of thing
that gets right once by hand and wrong the second time. The existing drivers are
`perf/run.sh` (per-mode compile/run timings), `perf/native-vs-llvm.sh` (the
codegen-gap ratio), and `perf/summarize.sh` (aggregation for CI); extend one of
those rather than starting over where they fit.

## 5. Recording results

Write results down where development history lives — `claude-todo.md` or the
relevant `plan-*.md` — never in code comments or durable docs, per CLAUDE.md.

A recorded measurement needs the date, the commit or release it was taken
against, the metric, and the method, because a bare number is unreproducible
and goes stale silently. Record **negative** results with the same care as
positive ones, including the root cause from §2: the negative results are what
stop the same lever being retried, and they are frequently the more valuable
half of the work.
