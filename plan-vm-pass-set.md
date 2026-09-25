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

## Current state

- `iropt.RunOptPasses(m, level)` (`iropt/opt.bn`) is a plain ordered call list; every pass is gated
  `level >= 1`, so -O1, -O2 and -O3 run the identical IR pipeline today (-O2/-O3 differ only in
  the clang -O they imply on the LLVM backend). Order: inlineCalls, runSroa, promoteScalars
  (mem2reg), eliminateDeadPhis, forwardLoads, forwardFieldLoads, simplifyIdentities,
  elideSafeDivChecks, bceConstIndex, bceLoop, bceRedundant, hoistLoopInvariants,
  groundGlobalRefPhiOperands, fuseMulAdds.
- `vm.LowerModule` calls `RunOptPasses(m, vm.OptLevel)`; `bni -O <n>` sets it (default 0, accepts
  `-O 2` but not `-O2`); `--test` and the REPL are fixed at 0.
- **IR-gen shape is also level-gated, and the interpreter doesn't pass the level to it:** irgen
  (`gen_local_cleanup.bn` `emitManagedStructPtrDtor`, via `GenCtx.OptLevel`) and irbuild
  (`emit_refdec.bn`, via `Module.OptLevel`) emit an SROA-friendly managed-struct cleanup shape at
  OptLevel >= 1. The interp builds its GenCtx with `irgen.NewGenCtx` and never sets `OptLevel`,
  so `bni -O 2` runs the passes on -O0-shaped IR, a combination bnc never produces. (Correct as far
  as known, but untested and not what "-O2" means anywhere else.)
- CI: the VM is only exercised at pass level 0 (the four VM runners ignore `BINATE_FLAGS`).

## Step 1 — per-pass switches in iropt

- A pass table in `iropt`: each entry has a stable name (e.g. `inline`, `sroa`, `mem2reg`,
  `dead-phi`, `load-fwd`, `field-load-fwd`, `simplify`, `div-check-elide`, `bce-const`,
  `bce-loop`, `bce-redundant`, `licm`, `fuse-madd`) and the minimum level it runs at. An
  `iropt.PassSet` (enabled bits per pass) is derived from a level, then adjusted by explicit
  enables/disables. `RunOptPasses(m, level)` stays as the level-derived convenience entry.
- **Required fixups are not switchable.** `groundGlobalRefPhiOperands` is a correctness step for
  whatever the forwarding passes produced, not an optimization: it runs whenever any pass that can
  produce a global-ref phi operand ran. Likewise the ordering constraints stay fixed (fuseMulAdds
  last; the "no addInstr pass after LICM/BCE/fuse" rule). Switches only remove passes from the
  fixed order, never reorder.
- **Dependencies:** a pass that is merely less effective without an earlier one (bceLoop without
  mem2reg finds no induction phis) is still allowed; that is exactly the "each pass correct by
  itself" property the switches test. If some pass is found to be *incorrect* without another,
  that is a bug to fix (or, while open, an explicit, commented dependency in the table), not
  something to hide.
- **bnc flags:** `-fno-<pass>` / `-f<pass>` (GCC style), applied after the -O level, repeatable;
  unknown names are an error. Also `--list-opt-passes` to print the table. `bni` gets the same
  spelling, for testing (see step 3 for its default).
- Unit tests in `iropt` for the table/PassSet derivation; `cmd/bnc/args_test.bn` for parsing.

## Step 2 — measure each pass under the VM

With the switches, measure per pass (and cumulative, in pipeline order) under `bni`:

- **Cost:** time spent in the pass at load, over the benchmark programs, the conformance corpus,
  and a large program (cmd/bnc itself interpreted, the realistic worst case for load time).
- **Benefit:** run time (user CPU, interleaved, order-alternating, per
  `perf-optimization-guide.md`), on the benchmarks suite under bni and a few conformance-heavy
  programs; bytecode instructions executed if the VM can count them cheaply.
- Commit the measurement script.

Expected shape (to verify, not assume): mem2reg + dead-phi, forwarding, simplify, and the BCE
passes remove executed bytecode ops cheaply; inlining and SROA are costlier and may or may not
pay; `fuse-madd` depends on whether the VM has a fused op (it lowers OP_MADD) or just expands it.

## Step 3 — the VM pass set

- `iropt` gets an interpreter entry point (e.g. `iropt.VMPassSet()`), defined in iropt next to the
  pass table so a new pass must decide there whether the VM runs it. Default for a new pass: off
  for the VM until measured.
- `vm.LowerModule` always runs that set (run path, `--test`, and the REPL alike). IR-gen for
  interpreted code uses the matching shape (set GenCtx/Module OptLevel consistently with the
  passes, or better, gate those shapes on the pass that needs them, `sroa`, rather than a level).
- `bni -O` is removed. `bni -f<pass>`/`-fno-<pass>` stay, for testing and bisecting.
- Passes known broken on the VM are excluded from the VM set with a comment naming the bug and a
  todo entry (never silently).

## Step 4 — CI

- The default VM conformance modes then test exactly the shipped VM set (they run bni as users do),
  so the VM's optimizer interaction is covered on every CI run, not only in the -O2 workflow.
- Fix `conformance-o2.yml`'s claim to cover the VM at -O2 (drop the VM shards, or keep them as a
  "VM with every pass forced on" run if that is still wanted — user's call).
