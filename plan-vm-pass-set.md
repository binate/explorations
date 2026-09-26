# Plan: per-pass optimization switches, and a fixed pass set for the VM

**Status:** 🟡 IN PROGRESS (claimed 2026-09-25). Tracked in `claude-todo.md` under "VM runs a
user-selectable -On of the compiler's IR passes".

## Decisions so far (user, 2026-09-25)

1. **The VM gets a fixed, interpreter-appropriate pass set, always on, with no user-facing -On**
   (option 2 of: VM at -O0 / fixed VM set / keep -On and test it). Code under bni should still run
   fast, but passes run on every load (there is no ahead-of-time step), so the set is chosen for
   that tradeoff rather than inherited from bnc. Some passes make no sense for an interpreter, and
   the increasingly marginal compiler passes (a worse compile-time : speedup ratio, fine when paid
   once at compile time) won't pay for themselves there.
2. **Individual optimizations can be switched on/off.** Useful for a compiler in its own right (each
   pass should be correct by itself, and toggling isolates a miscompile to one pass), and it is
   the tool for measuring which passes are worth it for the interpreter. A pass known not to work
   in some context (e.g. on the VM) can be excluded ("blacklisted").
3. Flag spelling `-f<pass>` / `-fno-<pass>` is fine. The REPL runs the VM pass set too. No CI
   sweep running conformance with each pass disabled in turn: its cost is prohibitive and grows
   with every pass added.
4. (2026-09-25, after the adversarial review) The REPL's config leaves out inlining (re-lowering
   inlining callers on redefinition is too complex/costly). Release builds and the default CI
   lanes build bnc/bni at `-O2` (IR passes; `-O2` also implies clang `-O2` on the LLVM backend).
   The all-passes-off bni gets one cheap CI lane. Self-drive: land each commit once reviewed
   clean; bugs a review finds in existing code are handled separately.

## Current state

- `iropt.RunOptPasses(m, level)` (`iropt/opt.bn`) is a plain ordered call list with one
  `level < 1` early return, so -O1, -O2 and -O3 run the identical IR pipeline (-O2/-O3 differ only
  in the clang -O they imply on the LLVM backend; on native not at all). Order: inlineCalls,
  runSroa, promoteScalars (mem2reg), eliminateDeadPhis, forwardLoads, forwardFieldLoads,
  simplifyIdentities, elideSafeDivChecks, bceConstIndex, bceLoop, bceRedundant,
  hoistLoopInvariants, groundGlobalRefPhiOperands, fuseMulAdds. Callers: `cmd/bnc/compile.bn`
  (two), `cmd/bnc/main.bn`, `cmd/bnc/test.bn`, `vm/lower.bn`.
- Other optimizer knob: `--inline-threshold` sets the package global `iropt.InlineSizeThreshold`.
- `vm.LowerModule` calls `RunOptPasses(m, vm.OptLevel)`; `bni -O <n>` sets it (default 0, accepts
  `-O 2` but not `-O2`); `--test` (its own VM, `cmd/bni/main.bn` `runTests`) and the REPL never set
  it. The REPL lowers prompt-entered code per function (`LowerOneFunc` / `LowerOneFuncShadow`),
  which never runs passes; only session start and mid-session imports go through `LowerModule`.
- **IR-gen shape is level-gated:** irgen (`gen_local_cleanup.bn` `emitManagedStructPtrDtor`, via
  `GenCtx.OptLevel`) and irbuild (`emit_refdec.bn`, via `Module.OptLevel`) emit an SROA-friendly
  managed-struct cleanup shape at OptLevel >= 1. The interpreter never sets `GenCtx.OptLevel`, so
  `bni -O 2` runs the passes on -O0-shaped IR. That combination is correct (VM unit tests use it,
  and a full conformance sweep at `bni -O 2` matched -O0), but it means SROA declines most managed
  structs under bni. Nothing else (backends, VM lowering, linker) reads the level.
- **CI never tests the optimizer on the VM, nor any optimized build of a toolchain binary:**
  - the four VM runners ignore `BINATE_FLAGS`, so the VM runs at level 0 everywhere;
  - `build_interp` / `build_gen1` / `build_gen2` (`scripts/lib/build-compilers.sh`) and the release
    scripts (`scripts/build-bni.sh`, `build-bnc.sh`) build with `--cflag -O2` — clang -O2 only, IR
    level 0 — and `BINATE_FLAGS` is not applied to those builds. So `conformance-o2.yml`'s VM shards
    are exact duplicates of the default -O0 VM run, and no CI job runs a bni or bnc whose own code
    went through the IR passes.
- **Load cost today (adversarial review measurement, bni built a few hours before `2ac1d516`):**
  interpreting cmd/bnc (`bni -main-dir cmd/bnc -- --version`) takes 4.2s user at `-O 0` vs 50.5s
  at `-O 2` (12×). Run time does improve: `perf/001_fib` 0.72s → 0.46s, `perf/005_slice_sum` 2.37s
  → 1.10s. Stack samples spread the load time over sroa (`instrListTable`, `splitSroaCopyOuts` /
  `boolTable`, candidate collection), forwardLoads / `coalesceSliceExtracts`, inlineCalls (incl.
  `sroaAdjustedSize` inside `inlinableCallee`), dead-phi, field-forward and simplify — mostly
  allocation, `slices.Append`, and per-function tables sized to `f.NextID`, built per candidate.
  That looks algorithmic, not inherent, and it is bnc's -O1+ compile time too.
- Nil checks: `--test` and the REPL always emit `OP_NIL_CHECK` (bnc never does), and fault pads
  are only ever executed by the VM, so pad rewriting in mem2reg/sroa/forwarding/inlining is only
  observable on the VM. A conformance sweep at `bni -O 2` with and without `--check-nil` matched
  -O0 (apart from the since-fixed 1283 SROA bug), but `--test` has never run with passes.
- No pass was found to be *incorrect* when an earlier one is skipped (bceLoop, forwardFieldLoads,
  elideSafeDivChecks just find less). The -O1+ managed cleanup shape is correct without SROA. One
  policy dependency: the inliner's cost model (`inline_sroa_cost.bn`) discounts callee size by what
  SROA + mem2reg would remove, so without SROA it over-inlines.

## Step 1 — per-pass switches in iropt

**✅ LANDED** binate `c11f3a99` + `2936905e` (2026-09-25; the toolchain-at-bnc-O2 build change
landed with it, `aa8c2bac`). As built: `iropt.OptConfig{Passes uint, InlineThreshold int}`,
`LevelOptConfig` / `WithOptPass` / `OptPassEnabled` / `OptPassName` / `OptPassByName`,
`RunOptConfig`; `GenCtx`/`Module.SroaCleanupShape` replaces `OptLevel`; `vm.Opt` replaces
`vm.OptLevel`; `pkg/binate/optflags` shared by bnc and bni. bni's `-f` flags (like `-O`) apply to
a plain run only until step 4.

- A pass table in `iropt`: each entry has a stable name (`inline`, `sroa`, `mem2reg`,
  `dead-phi`, `load-fwd`, `field-load-fwd`, `simplify`, `div-check-elim`, `bce-const`,
  `bce-loop`, `bce-redundant`, `licm`, `fuse-madd`). No per-pass minimum level: every level
  >= 1 runs every pass (`LevelOptConfig`). An
  `iropt.PassConfig` holds the enabled bit per pass plus the inline threshold (replacing the
  `InlineSizeThreshold` global); it is derived from a level, then adjusted by explicit
  enables/disables. `RunOptPasses(m, level)` stays as the level-derived convenience entry.
- The early return becomes "no pass enabled", so `-O0 -fsroa` runs just SROA.
- **Fixups are not switchable.** `groundGlobalRefPhiOperands` runs whenever any pass runs (only
  iropt creates phis, so it's a no-op otherwise). Ordering constraints stay fixed (fuseMulAdds last;
  no addInstr pass after LICM/BCE/fuse). Switches only remove passes from the fixed order.
- **Dependencies:** a pass that is merely less effective without an earlier one is allowed; that
  is the "each pass correct by itself" property the switches test. A pass found *incorrect*
  without another is a bug to fix (while open: an explicit, commented dependency in the table).
  The inliner's cost model reads the config: it only applies the SROA/mem2reg discount when those
  passes are enabled.
- **IR-gen shape gate follows the config, not the level:** `GenCtx` / `Module` carry the pass
  config (or just the sroa bit) instead of `OptLevel`, so `-O0 -fsroa` and `-O2 -fno-sroa` get the
  shape that matches. Every `GenCtx` construction site gets it (`interp.bn` ×4, `cmd/bni/main.bn`,
  `repl/session.bn` ×2, `mid_session_import.bn`, bnc's), and `irgen.GenModule` keeps shape 0.
- **Flags:** `-f<pass>` / `-fno-<pass>` on bnc and bni. `pkg/std/flags` is dash-insensitive with
  no prefix matching and no order preservation, so each pass registers two bool flags (as bnc does
  for `O0`..`O3`); giving both `-fX` and `-fno-X` is an error; unknown names already error. Plus
  `--list-opt-passes`. (Extending `pkg/std/flags` instead is possible but it is in the
  BUILDER-compiled surface.) `-On` keeps implying clang `-On` on the LLVM backend; `-f` flags
  don't affect clang.
- Unit tests: `iropt` for config derivation and the no-pass / single-pass cases;
  `cmd/bnc/args_test.bn` and bni's args tests for parsing.

## Step 2 — make the passes cheap enough to run on every load

The 12× load cost is a problem for bnc -O1+ as much as for the VM, and until it's fixed every
pass will look too expensive for the VM. Before choosing the VM set:

- Profile bnc -O2 compiling cmd/bnc (compiled bnc: callgrind), and bni loading cmd/bnc, per pass.
- Fix the per-candidate O(NextID) table construction and similar algorithmic costs pass by pass,
  measuring each (user CPU, noise floor first, per `perf-optimization-guide.md`).

**Findings (2026-09-25).** callgrind of `bnc -O2 --backend native` compiling cmd/bnc: the IR
passes were 92% of the whole compile (native codegen 6%); forwardLoads 37.5%, runSroa 37.4%,
promoteScalars 5.9%, simplify 5.5%, inline 2.5%. Root cause of the two big ones:
`slices.Append` is documented O(n) per call (fresh len+1 backing + copy), and iropt's
NextID-sized tables (`boolTable` / `instrTable` / `instrListTable`, and copies of the same loop in
`coalesceOneSliceExtracts`) were filled one Append per element — O(n^2) per function, rebuilt on
every SROA fixpoint pass and every coalesced slice load (29% + 26%). Replacing them with
`make_slice`: 2m13s -> 1m07s user for that compile, output byte-identical (vs ~12s for the
compile with no passes). iropt has ~190 other `slices.Append` call sites; the append-in-a-loop
ones (block rebuilds `kept = Append(kept, ins)`, worklists) are all quadratic too and are next
(`vec.Vec`, or presizing).

**✅ LANDED** binate `ff62917d`..`d7b9f7a6` (2026-09-25). Progress (same compile, user time,
output byte-identical at every step):
2m13s → 1m07s (NextID tables via make_slice) → 44s (append loops → vec.Vec / make_slice across
iropt) → 38s (the loops that sweep missed: simplify, dom, multi-line-signature funcs) → 25.5s
(SROA's L1/L2 legality checks and the inliner's cost model answered from a per-function use
index instead of a whole-function scan per candidate) → 23.1s (per-callee inlining facts cached)
→ ~20.5s (callee lookup via the name index instead of a linear scan per call site; flat use
index; load-forwarding's per-alloca analysis via the index). Whole compile with no passes: ~12s.
bni -O 2 loading cmd/bnc: 50.5s (before) → ~9.6s; -O 0 is ~4.3s. Open: `slices.Append` is O(n) per call
and has ~840 call sites across pkg/binate (parser, irgen, ...), not just iropt — whether to fix
per site or make Append amortized (managed-slices carry a backing length; aliasing semantics)
is a library decision put to the user.

## Step 3 — measure each pass under the VM

**Done (2026-09-25):** results and the accepted tentative set are in the living document
[vm-pass-set.md](vm-pass-set.md).

- **Cost:** per-pass load time (switches make leave-one-out and single-pass runs possible) on the
  benchmark programs, the conformance corpus, and cmd/bnc interpreted; peak RSS; REPL per-prompt
  latency.
- **Benefit:** run time (user CPU, interleaved, order-alternating) on the benchmarks suite under
  bni and some conformance-heavy programs.
- Both **cumulative in pipeline order and leave-one-out** from the candidate set (passes interact:
  bceLoop needs mem2reg's phis, the inliner's cost model assumes SROA).
- On the bni that ships (see step 5: built with the IR passes too) and a native-backend-built bni.
- **Decision rule stated before measuring:** the workload mix and the run length at which a pass's
  load cost is repaid; also CI wall time on the `-int-int` lanes (which interpret cmd/bni for every
  test, already sharded 6-12 ways) must stay within their caps.
- Commit the measurement script.

## Step 4 — the VM pass set

**LANDED (2026-09-26, binate `4ff351ea`):** `iropt.VMOptConfig`, `vm.NewVM` default, `bni -O` removed,
REPL = VM set minus inline via per-function `iropt.RunOptConfigFunc`, `ir.Func.Optimized`.  The first
`--test`-with-passes run found the VM `get_field_ptr` bug (fixed, `759ec68b`) and, via it, the IR-gen
field-base TypeArg tagging bug (fix in review) and a VM named-pointer field-offset bug (same fix).  Its
review also exposed the IR CFG/phi `@Block` cycle leak (MAJOR in claude-todo.md, open).

- `iropt.VMPassConfig()`, defined next to the pass table so each new pass decides there whether
  the VM runs it; a new pass starts off for the VM until measured.
- The VM holds a `PassConfig` (replacing `vm.OptLevel`), defaulting to the VM set. Unit tests
  that deliberately compare configs set it explicitly; `Interp.SetOptLevel` is replaced by a
  config setter. `LowerModule` runs it on every path: run, `--test`, REPL session start, and
  mid-session imports.
- **REPL:** prompt-entered functions need a per-function entry point (the passes minus inlining,
  which is inter-procedural) called from the per-function lowering. Inlining vs redefinition: a
  same-signature redefinition replaces the function in place so old callers see the new body; a
  caller that inlined the old body would keep running it. Decided: inlining is left out of the
  REPL's config. Also verify that no IR-gen
  appends to an already-optimized function afterwards (stale `InstrsVec`).
- **First `--test`-with-passes run is a bug hunt** (nil checks + fault pads + passes has never
  run on unit tests). Policy for what it finds: each failure gets root-caused; a pass that is
  wrong on the VM is fixed, or with the user's decision excluded from the VM set with a comment
  and a todo entry — never silently.
- `bni -O` is removed; bni keeps `-f<pass>` / `-fno-<pass>`.

## Step 5 — CI

- The default VM conformance and unit-test lanes then test exactly the shipped VM set, on every
  CI run, including for the first time on a 32-bit host (`builder-comp_arm32_linux_int`).
- **`conformance-o2.yml`'s VM shards test bni *built* with `bnc -O2`** (IR passes + clang), as an
  integration test of -O2 on a large program: `build_interp` (and gen1/gen2 builds where
  relevant) apply `BINATE_FLAGS`. This is independent of the VM pass set and doesn't have to wait
  for steps 1-4. Decided: the release builds (`build-bni.sh`,
  `build-bnc.sh`) and the default CI lanes build the toolchain at `-O2` (IR passes) rather than
  `--cflag -O2`; today no shipped binary goes through the IR optimizer.
- Decided: one cheap CI lane runs bni with all passes off (the reference executor for bisecting
  a pass), so it doesn't rot after step 4.
