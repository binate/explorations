# Plan: IR functions with a loop or a phi are never freed (reference cycles)

**Status:** 📝 DRAFT, for review (2026-09-26). Tracked in `claude-todo.md` MAJOR "IR functions with a
loop or a phi are never freed …".

## Problem

The IR is refcounted, and some of its cross-references are owning (`@`) and form cycles, which
refcounting never frees:

| reference | cycle it closes |
|---|---|
| `Instr.Block1` / `Block2` (terminator targets) | a loop back edge: header → … → latch → header |
| `PhiEntry.Block` (a phi's predecessor) | merge block → phi → predecessor → its branch → merge block (every phi) |
| `PhiEntry.Val` (a phi's operand) | loop-carried value: `i1 = phi [i0], [i2]`, `i2 = add i1, 1` |

`Block.Func` is already a raw `*Func` for exactly this reason; nothing breaks the rest. Measured
(`rt.LiveBlocks()` across build + drop of a one-function module, vm test package): loop-free, no
passes: 0; one loop, no passes: ~52 blocks; loop-free with mem2reg's phi: ~139. So every function
with a loop (from IR-gen) or a phi (mem2reg) leaks its whole IR — blocks, instructions, and
everything they own — when its module is dropped. No cost for a bnc run (it exits); bni, the REPL
(per prompt entry, per mid-session import) and any embedder retain it for the process lifetime.

Non-phi `Args` are acyclic (SSA: an operand is defined before its use, except through a phi).
`Instr.PadBlock` is already raw.

## Ownership today

- `Func.Blocks` / `Func.FaultPads` own every block; a block owns its instructions through
  `InstrsVec` (and `Instrs`, a view — passes that drop an instruction rebuild `Instrs` only, so
  `InstrsVec` still owns it).
- **Not every value an instruction refers to lives in a block.** Parameter references
  (`ir.NewParamRef`, `OP_PARAM`) and global-address pseudos (`IsGlobalRef`, id -1) are owned only by
  the `Args` / `PhiEntry.Val` that point at them. Constants a pass creates are usually inserted into
  a block, but not necessarily before they are referenced.

## Fix

1. **CFG edges non-owning: `Block1`, `Block2`, `PhiEntry.Block` become `*Block`.** Every block a
   branch or phi names is owned by its function's `Blocks` / `FaultPads`, so this is sound as long
   as a block is never removed from those while something still names it. Verify that invariant:
   audit every pass that removes or replaces blocks (dead-block removal, critical-edge splitting,
   the inliner's block splicing, SROA/mem2reg pad cloning, phi elimination), and add it to
   `irbuild`'s verifier (a branch target or phi predecessor must be in `f.Blocks` / `f.FaultPads`).
   Mechanical otherwise: ~46 `Block1` + ~25 `Block2` + ~7 `PhiEntry.Block` uses in ~20 files across
   ir, irbuild, irgen, iropt, vm, codegen and the three native backends; comparisons (`==`) and field
   reads don't change, only the declared types and any `@Block` locals/params they flow into.
2. **Phi operands: break the value cycle without dangling operands.** `PhiEntry.Val` cannot simply
   become `*Instr`: a param ref or global pseudo operand is owned only by the phi. Options:
   - **(a)** `PhiEntry.Val` raw, plus an owner for every value that has no block: `Func.ParamRefs`
     (one shared `OP_PARAM` instr per param, which also removes the per-use duplicates) and an
     owner vector for global pseudos (or ground them into the entry block, as
     `groundGlobalRefPhiOperands` already does for phis). Structural; the invariant "every phi
     operand is block-resident or func-owned" goes into the verifier.
   - **(b)** Keep `PhiEntry.Val` owning and break the value cycles when a function is torn down
     (clear every phi's entries). Needs an explicit teardown call at every point a module or function
     is dropped — easy to miss, and a missed call is a silent leak again.
   Recommendation: (a). If step 1 lands first, the phi value cycle is the only remaining one, and
   the leak test below says whether it matters in practice (it does: loop-carried phis are in every
   optimized loop).
3. **Tests (land with the fix):** in pkg/binate/vm (which already measures `rt.LiveBlocks()`):
   build + drop vs build + optimize + drop for `iropt.VMOptConfig()` and `LevelOptConfig(2)`
   (leak = difference, after a warm-up build), on a program with a loop, a phi at an if-merge, a
   loop-carried phi, a param used as a phi operand and a global address in a loop; plus
   build + drop of an unoptimized loop module against a loop-free baseline. Verifier unit tests for
   the new invariants.
4. **Verification:** unit tests of every package that names these fields; conformance on LLVM, the
   VM and native x64 (+ CI for aa64/arm32); bnc `-O2 --emit-llvm` of cmd/bnc byte-identical (the
   change is ownership-only).

## Order

Step 1 (CFG edges) is independent and removes the loop cycle and the phi→block cycle: land it
first with its tests. Then step 2 with the full leak test (which cannot pass before both land).

## Open questions (for review)

- Is there any path that removes a block from `Func.Blocks` while a branch or phi still names it
  (i.e. would a raw edge dangle)?
- Are there other owning cycles (e.g. through `Module` ↔ `Func`, closures, `InstrsVec`, cached
  analyses stored on blocks)?
- Can any phi operand be an instruction owned by neither a block, the function's params, nor a
  global pseudo (e.g. a pass-created constant referenced before insertion, inliner clones)?
