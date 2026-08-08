# Plan — property-based differential value-correctness harness

## Why (the gap the matrices leave)

The conformance matrices catch value-correctness bugs only **at the values
someone thought to write**. The escapes prove it: the shift-by-≥-width CRITICAL
hid because the scalar matrix only used in-range shift counts; the
unsigned-int↔float and sub-word dirty-bit bugs were caught at a couple of
hand-picked high-bit values, not exhaustively. These are all the **Class-5
family** — pure scalar value-correctness — where the *space of inputs is small
and enumerable* and the *correct answer is computable from the spec*. That is
exactly the shape a property-based **differential** harness covers exhaustively
and a hand-authored matrix never can.

Key distinction from the matrices: the oracle is the **spec**, computed
independently (Python now, a Go/bignum reference later), **not** a backend. The
shift bug is wrong on LLVM *and* the VM *and* both natives — so an
LLVM-as-oracle differential (VM-vs-LLVM) would have missed it. Comparing every
backend against the *spec-defined* answer catches **both** failure modes:
backend-divergence (one backend wrong → it alone fails) and spec-divergence (all
backends wrong → all fail vs the reference).

## What it is

A generator (`conformance/gen-diff-*.py`, Python now; port to Binate later to
dogfood) that, for each `(op, type)`, emits cells whose inputs are a **fixed,
seeded** value set — so regeneration stays idempotent, matrix-style — combining:

- **all boundary values** for the type/op: `0, ±1, min, max, high-bit-set,
  width-1, width, width+1, 2·width+k` (counts), `2^(w-1)±1`, NaN/±Inf/±0/denormal
  (floats);
- a **deterministic pseudo-random sample** across the range (a fixed LCG seeded
  per (op,type), not per-run — no `Math.random`, so cells are reproducible).

Each cell computes the op at runtime (operands/counts in `var`s, so it exercises
the backend, not const-fold) and `println`s the result; the `.expected` is the
**spec-defined** answer computed by the generator at full precision. The cell
runs across the existing modeset, so one cell = a differential across all
backends vs the spec.

## Op coverage (highest-yield first — the families that keep escaping)

1. **shifts** — `<< / >>` × width × sign × count ∈ {in-range, 0, width,
   overshift}. (The current `{shl,shr}-overshift` matrix cells are the
   hand-picked seed of this.)
2. **conversions / casts** — int↔float (both directions, signed/unsigned, all
   widths), narrowing/widening int casts, signedness reinterpret, float32↔float64.
3. **integer arithmetic** — add/sub/mul/div/rem × width × sign, with
   overflow/boundary + random operands, result consumed directly (dirty-bits).
4. **comparisons** — signed vs unsigned at width boundaries; float ordered/
   unordered (NaN).
5. **bitwise** — and/or/xor/not at sub-word widths (narrowing).

## Two realizations (pick per op)

- **(A) Generated static cells** — like the matrices: many cells per op, one
  input-tuple each, `.expected` precomputed. Fits the conformance infra
  verbatim; readable failures; idempotent. Cost: file count (mitigate by
  batching K input-tuples per cell, each `println`'d, with a K-line `.expected`).
  **Recommended for v1** — reuses everything; the shift cells already prove it.
- **(B) Self-checking corpus program** — one `.bn` per op that loops over an
  embedded table of `(inputs, expected)` and prints PASS/`mismatch i: got…want…`.
  Denser (thousands of cases per file), but the expecteds must be embedded
  (generated) and a loop-driver written. A v2 once (A) shows which ops need
  volume.

## Relationship to the existing matrices

The matrices stay — they are the **curated, human-readable, spec-edge** layer
(one cell per documented edge, with an explaining comment). The differential
harness is the **exhaustive volume** layer underneath, for the value-correctness
ops only. Place it as a sibling subtree (`conformance/matrix/<op>-diff/` or a
`conformance/diff/` tier) discovered by the same runner.

## Phasing

1. **v1: shifts + conversions** — LANDED 2026-06-06 (binate `0c43e0f3`,
   `conformance/gen-diff-scalar.py`, 41 cells / 1707 tuples, flavor A,
   self-checking). Oracle adversarially validated (5 spec-readers, all agree).
   Green on LLVM + arm32 baremetal; found two backend defects, xfailed + filed:
   VM `int→float32` (`vm-int-to-float32`) and native-aa64 sub-word narrowing
   (`aa64-subword`). int↔int casts and all shifts pass everywhere (the shift
   family is no longer red — `32fde83d` landed). See `claude-todo.md`.
2. **v2: arithmetic + comparisons + bitwise** — LANDED 2026-06-06 (binate
   `42ad4fa0` fix + `e71de1e0` harness; now 123 cells / 5415 tuples). v2 itself
   *found and fixed* a CRITICAL LLVM `~` (bitwise-complement) codegen bug
   (`bitnot-result-type` — result type was hardcoded to `int`). `fcmp` pins the
   ordered/unordered `==`/`!=` NaN semantics (built via `bit_cast`). Remaining
   xfails are known-class: VM `bitwise/not` sub-word-unsigned; native-aa64
   sub-word-signed `arith`/`bitwise`/`cmp`/`not` (`aa64-subword`). `fcmp/32`
   un-xfailed at land time (the concurrent float32-compare fix `fc11d862`). See
   `claude-todo.md`.
2b. **neg family** — LANDED 2026-06-08 (binate `d64b76d0`; harness now 131
   cells / 5511 tuples). Added a `neg/{8,16,32,64}/{signed,unsigned}` family
   (unary minus, mirroring `bitwise/not`: direct value + `>>`-consume dirty-bit
   check) as the plan-cr2-1 Defect-9 follow-up. Green on every backend — the
   IR-gen unary-minus-result-type fix (binate `fce07ccd`) makes them correct.
3. **v3: port the generator to Binate** (dogfood) + consider flavor B for the
   highest-volume ops; wire a fixed sample-size knob. — NOT STARTED.

## The broader net (context — this is one of four complementary layers)

The differential harness closes the **un-enumerated-value** blind spot. The
other matrix blind spots need different tools, tracked here so the picture is
whole:

- **factor combinations** (mixed managed-field struct dtors — the `@func`-field
  crash that wouldn't minimize): property-based / **fuzz** generation of random
  composite types with a dtor-balance/no-crash oracle.
- **fix regressions** (the Plan-1 adversarial-review defects): **adversarial
  review of every landed fix** (a skeptic agent via `--emit-llvm`/gen1) — a
  process step, not a test artifact.
- **real-world interactions** (the OOM, the `@func`-dtor crash, this shift bug
  were all first surfaced by compiling minbasic): a first-class **dogfood CI
  tier** — compile + run a corpus (minbasic, the stdlib, the compiler itself)
  continuously.

The differential harness is the single highest-leverage *test-artifact*
addition; the dogfood tier is the highest-leverage *process* addition.
