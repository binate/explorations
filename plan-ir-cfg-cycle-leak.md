# Plan: IR functions with a loop or a phi are never freed (reference cycles)

**Status:** 📝 DRAFT, revised after adversarial review (2026-09-26); awaiting go-ahead. Tracked in `claude-todo.md` MAJOR "IR functions with a
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
everything they own — when its module is dropped. **bnc pays it too:** it compiles every dependency
package in one process and drops each module after compiling it (`cmd/bnc/main.bn` ~321-350; also
`test.bn`, `library.bn`), so its peak memory grows with the transitive package set (worst on the
self-compile). bni, the REPL (per prompt entry, per mid-session import) and any embedder retain it
for the process lifetime.

Non-phi `Args` are acyclic (SSA: an operand is defined before its use, except through a phi).
`Instr.PadBlock` is already raw.

## Ownership today

- `Func.Blocks` / `Func.FaultPads` own every block (every block is appended at creation by
  `AddBlock` / `AddFaultPad`; the only removal, `pruneUnreachableBlocks`, runs before any phi exists
  and only drops unreachable blocks — so raw edges never dangle).
- A block owns its instructions through `Instrs`; `InstrsVec` owns only what IR-gen emitted (only
  `addInstr` pushes to it). Many passes rebuild `Instrs` and leave `InstrsVec` stale, so some values
  survive today only because a *deleted* instruction still sits in a stale `InstrsVec` — e.g. a param
  ref whose entry-block store mem2reg deleted after forwarding the ref into a phi.
- After SSA destruction (`EliminatePhis`, run in place by the VM and native lowering) phis are no
  longer in any block: they are owned by their users' `Args`.
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
   - **(a)** `PhiEntry.Val` raw, plus a real owner for every value that has no block, filled by every
     creator: `Func.ParamRefs` (one shared `OP_PARAM` instr per param, also removing the per-use
     duplicates) and `Func.GlobalRefs` for global-address pseudos (built with a bare `make(ir.Instr)`
     in `irgen/gen_func.bn` ~356/378 and `irbuild/module_init_emit.bn`). Grounding
     (`groundGlobalRefPhiOperands`) runs last, after mem2reg and every pass that can rebuild a block,
     so it cannot be the owner. Verifier invariant, checked after every pass in tests: every operand
     of a phi still in a block's `Instrs` is in some block's `Instrs`, a param ref, or a global ref.
   - **(b)** Keep `PhiEntry.Val` owning and break the value cycles when a function is torn down
     (clear every phi's entries). Needs an explicit teardown call at every point a module or function
     is dropped — easy to miss, and a missed call is a silent leak again.
   - **(c)** considered: the function owns every instruction (a per-function vector filled at
     `NewInstr`) and every intra-function reference, `Args` included, becomes raw. One invariant,
     immune to future passes — but param refs, void instrs and global pseudos have no function at
     creation (the same extra owners are needed), `Args` as `[]*Instr` touches nearly every IR
     consumer, and dead instructions accumulate until the function drops. Rejected for (a).
   Recommendation: (a). If step 1 lands first, the phi value cycle is the only remaining one, and
   the leak test below says whether it matters in practice (it does: loop-carried phis are in every
   optimized loop).
3. **Tests (land with the fix):** in pkg/binate/vm (which already measures `rt.LiveBlocks()`):
   leak = growth of build + optimize + **lower** + drop minus build + drop (after a warm-up build),
   for `iropt.VMOptConfig()` and `LevelOptConfig(2)`, lowering through the VM (and, where a test
   harness allows, a native emitter and LLVM codegen — SSA destruction's critical-edge blocks, swap
   temps and out-of-block phis live there). The program must cover: a loop; a phi at an if-merge; a
   loop-carried phi; a dead self-referential header phi (dead-phi removes it); a param used as a phi
   operand; a global address stored in a loop, including inside a multi-block-inlined callee (the
   -O2 inliner's `cont` / cloned blocks). Plus build + drop of an unoptimized loop module against a
   loop-free baseline. Verifier unit tests for the new invariants; the verifier run after every
   pass in these tests.
4. **Verification:** unit tests of every package that names these fields; conformance on LLVM, the
   VM and native x64 (+ CI for aa64/arm32); bnc `-O2 --emit-llvm` of cmd/bnc byte-identical (the
   change is ownership-only). A dangling raw edge would be a silent use-after-free, not a leak-test
   failure: also run the conformance and unit suites with freed memory poisoned (a debug rt mode, if
   one exists, else add one). Measure bnc's peak RSS on the cmd/bnc self-compile before and after.
5. **Cleanups found on the way:** `iropt/inline_multiblock.bn` `remapBlock`'s "defensive: return b"
   would plant a raw edge into a callee block — make it panic. The type change is not only field
   declarations: `*Block` does not flow into the many `@Block` parameters (`ir.BlockIndexIn`,
   `remapBlock`, `phiPreds`, `redirectTerminator`, …), so those signatures change too; hand-built
   test blocks (`make(ir.Block)` in native unit tests) must be appended to a function to pass the
   verifier.

## Order

Step 1 (CFG edges) is independent and removes the loop cycle and the phi→block cycle: land it
first with its tests. Then step 2 with the full leak test (which cannot pass before both land).

## Review outcome (2026-09-26)

- The three cycles are the only ones in the IR data model (Module/Func/Block/Instr/PhiEntry/
  Global; `Func.Mod`, `Block.Func`, `Instr.PadBlock` are raw; checker/types/irsym/irdata hold no IR
  back-references; no analysis caches live on IR objects).
- No path removes a block while an edge names it (see Ownership).
- Phi operands owned by nothing but the phi: param refs and global pseudos (hence the owners in
  2(a)); mem2reg's zero constants are inserted at the entry-block head; SROA's zeros predate phis.
