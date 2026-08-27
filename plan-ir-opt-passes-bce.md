# Plan: IR optimization passes — gating infrastructure + mem2reg-lite + bounds-check elimination (BCE)

## Motivation & strategic frame
Profiling bni running real programs (bnc compiling bnlint; bnlint linting packages)
shows the memory-access path dominates: `execMemoryOp` + `rt.BoundsCheck` ≈ 28% of
VM samples (vs ~12% in fib). Two distinct bounds-check costs, don't conflate them:
- **The VM's OWN native checks** (`code[pc]` per instruction, ~1287 samples/6% in
  bnlint; other VM-internal managed-slice reads). These are the biggest single
  chunk but are NOT IR-BCE-eliminable (the pc-in-range invariant isn't
  compiler-provable). Tactical `unsafe_index` is the only lever there — DEFERRED /
  separate (user deprioritized it in favor of the generic win).
- **Compiled programs' checks** — every `s[i]` in every Binate program. Today
  `OP_BOUNDS_CHECK` lowers to an **opaque `call @rt.BoundsCheck`** (codegen
  emit_instr.bn), which **LLVM cannot eliminate** (unlike C's inline array checks).
  So EVERY native Binate program pays a call per access; the native backends
  (pkg/binate/native/*) have no BCE at all; and the interpreter pays a
  BC_BOUNDS_CHECK per access. IR-level BCE removes `OP_BOUNDS_CHECK` once, before
  lowering, so **every consumer — LLVM backend, native backends, AND the bytecode
  VM — benefits**. This is the "make the native backends a viable LLVM alternative"
  enabler.

Baseline ceiling (this is what BCE recovers): VM `slice_sum` ~6.5% from removing
the interpreted checks; native likely higher (a call per access LLVM won't fold).

## Design decisions (settled with the user)
- **Gate = a bnc opt-level `-On`**, DISTINCT from `--cflag -On`. But `-On` should
  auto-imply `--cflag -On` when the backend is LLVM (one unified knob to ask for
  optimization). Default `-O0` = no IR passes = today's behavior (conservative for
  new passes); release/test builds opt in.
- **IR-level, shared** — the pass runner is invoked by BOTH cmd/bnc (before
  `compileModule`) AND cmd/bni's load pipeline (before IR→bytecode lowering), each
  with its own opt setting. So passes apply to the interpreter too.
- **Do mem2reg-lite** — the IR keeps locals in memory slots (VarSlot: name →
  SSA-pointer), not phis, so the marquee `for i < len(s) { s[i] }` BCE case can't be
  read off a phi. Promoting simple locals to SSA first makes BCE (and many future
  passes) tractable, and is itself a real enabler for the native backends.

## Phased plan (each phase independently landable + green)

### Phase 1 — pass infrastructure + `-On` gating (small, unblocks everything)
- Add a bnc `-O<n>` flag (args.bn / CLIArgs); `-On` sets an OptLevel AND (LLVM
  backend only) implies `--cflag -On`. Keep `--cflag` working standalone.
- Add `ir.RunOptPasses(mod @Module, level int)` — runs the (initially empty or
  no-op/verify) pass list gated by level. Slot it between `GeneratePackage` and
  `compileModule` in cmd/bnc/compile.bn, and into cmd/bni's IR→bytecode path.
- A trivial verify/no-op pass to prove wiring + gating end-to-end (a pass that,
  say, asserts the IR is well-formed, or counts ops), gated at `-O1+`.
- Tests: `-O0` unchanged output; `-On` runs the pass; the LLVM `--cflag` implication.
- BUILDER note: pkg/binate/ir IS BUILDER-compiled — keep the pass runner within the
  BUILDER language subset.

### Phase 2 — mem2reg-lite (the enabler)
- Promote function-local memory slots that are only load/stored (never
  address-taken / escaped) to SSA values with phis at merge points. Scope to the
  simple, safe cases first (single-block or reducible loops); leave escaped/aliased
  slots in memory.
- This benefits the native backends broadly (they otherwise emit load/store for
  every local) and is the precondition for a clean loop-BCE.
- Tests: semantics-preserving (unit + conformance); ideally a size/quality check.

### Phase 3 — BCE pass
- Remove `OP_BOUNDS_CHECK(idx, len)` proven safe:
  - constant idx within a constant/known len (arrays);
  - redundant/dominated: same (idx,len) already checked on a dominating path, no
    intervening len-invalidating store;
  - loop-guard-implies-safe: idx is an induction phi bounded by the loop guard
    `phi < len` with a stable `len` (this is the big one; tractable post-mem2reg).
- Both backends + the VM then skip the check automatically (one IR change).
- Tests: BCE unit tests (check removed where safe, KEPT where not — e.g. `s[i]`
  with unbounded i); conformance (bounds faults still fire for genuine OOB);
  perf micros (005_slice_sum should improve under the opt level, native + VM).

## Risks / open questions
- mem2reg-lite correctness (refcount ownership of promoted managed locals; phi of
  managed values) — get adversarial review; it's the riskiest piece.
- Whether to run passes in the interpreter by DEFAULT (adds load-time cost for the
  execution win) — tune bni's default opt level separately.
- Native backends' own opt story (they currently emit naive code) — mem2reg-lite +
  BCE are the first steps; more (regalloc quality etc.) is future work.
- Each phase gets a focused adversarial review before landing (esp. Phase 2/3).
