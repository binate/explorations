# Phase 2a — non-managed scalar SSA promotion (mem2reg)

Design doc for the first `OP_PHI` *producer*. Consumed by the existing lowering:
LLVM keeps phis natively (Phase P LLVM lane); the VM + 3 native backends destruct
them via `ir.EliminatePhis` (Phase P). This pass is what finally makes the whole
Phase P + Phase D stack live, and is the direct enabler for Phase 3 loop-BCE (the
induction variable must be an SSA phi for the loop-BCE soundness conditions to
apply). Lives in `pkg/binate/ir` (BUILDER-compiled — stay in the subset).

**Adversarial design review (2026-08-27): design sound modulo three fixes, now
folded in** — (1) MAJOR: orphan-block → malformed LLVM phi, fixed by the
reachable-block prune below; (2) the real undef invariant is the entry-dominating
zero-init store, guarded by a rename-time nil-operand bail-out; (3) build phis via
`newInstr` (not `EmitPhi`) and insert only at rebuild. Escape completeness,
diamond/loop/back-edge phi filling, repl transitive chase, phi typing, pass ordering,
deletion/rebuild, and FaultPads were all verified sound.

## What gets promoted

An `OP_ALLOC` instruction is a **promotable slot** iff ALL hold:

1. **Type is a non-managed scalar** matching *exactly* what `EliminatePhis`'
   `assertScalarPhi` accepts, since we emit the phis it will lower:
   `peelToRepr(alloca.TypeArg).Kind` ∈ { `TYP_INT` with `Width ≤ TypInt().Width`,
   `TYP_BOOL`, `TYP_POINTER` (raw `*T`) }. **Float is excluded** (phi-elim has no
   float lowering), as are every managed kind, slices, structs, interface values.
   This scoping sidesteps the entire refcount-hazard family AND the fault-pad
   hazard (pads only ever touch *managed* locals; a non-managed scalar slot carries
   no inc/dec and never appears in a pad).
2. **Does not escape (over-broad).** Every use of the alloca result is the **address
   operand** of a plain `OP_LOAD` (`Args[0]`) or `OP_STORE` (`Args[0]`) — nothing
   else. ANY other appearance pins the slot unpromotable: as `OP_STORE.Args[1]` (the
   address is stored as a value), as any operand of any other op
   (`OP_GET_FIELD_PTR`/`OP_GET_ELEM_PTR`/`bit_cast`/a call arg/…), as a `PhiEntry.Val`,
   or **any** appearance inside `Func.FaultPads`. The escape scan covers `f.Blocks`
   *and* `f.FaultPads`. (A scalar slot cannot legitimately appear in a pad; treating
   any pad appearance as escape is a belt-and-suspenders that essentially never
   fires.)

The only `@Instr`-typed operand fields are `Args @[]@Instr` and `Phis[].Val @Instr`
(Block1/Block2/PadBlock are blocks; `Init` is a module-global field, not on `Instr`),
so "find all uses of value V" = scan every instr's `Args` and every `PhiEntry.Val`.

## Why undef never arises (the key soundness lever)

Binate's IR-gen emits a **zero-init store at every scalar/pointer local's
declaration** (`gen_stmt.bn`: raw `*T` → `EmitConstNil` store; `IsScalar()` int/bool →
`EmitConstInt(0)`/`EmitConstBool(false)` store). The declaration block dominates the
variable's entire scope, so **every load of a promotable local is dominated by a
store**. Compiler-internal scalar temps likewise always store-before-load by
construction. We do NOT rely on this blindly — we *gate* on it:

**Dominating-store pre-check (per alloca):** promote only if every load `L` of the
alloca has a store `S` to the same alloca with either (a) `S` in `L`'s block textually
before `L`, or (b) `S`'s block strictly dominates `L`'s block (`DomInfo.Dominates`).
If any load fails, the slot is left **unpromoted** (alloca/load/store untouched) —
fully sound, zero behavior change. Because a dominating store means every path from
entry to `L` passes a store, the rename can never reach a load with no reaching def.
This makes the whole "materialize a zero / insert undef / abort mid-rename" question
moot: undef is impossible for anything we promote, and anything that *could* be undef
is simply not promoted. (Given the zero-init guarantee, the pre-check passes for all
real locals; it is a safety gate, not a common rejection.)

## Reachable-block prune (prerequisite — fixes the orphan-phi hazard)

Gen legitimately leaves **benign orphan blocks** — e.g. an `if.merge` / `switch.exit`
whose arms all terminate (return/continue), which `verify.bn` documents and does not
flag. Such an orphan still carries a `jump` into its continuation, so if that
continuation becomes a mem2reg phi-block, the phi (filled only from reachable
predecessors via the dom-tree rename) would have **fewer entries than the block's CFG
predecessors**. The LLVM backend emits every block in `f.Blocks` with no reachability
filter, so it would see the orphan as a third predecessor and reject the phi
("PHINode should have one entry for each predecessor") — a build failure at `-O1+`.
(VM/native are immune — `EliminatePhis` derives predecessors from the phi's own
entries — but LLVM is not.)

So `promoteFuncScalars` **first prunes unreachable blocks**: DFS from `f.Blocks[0]`
over terminator successors, then rebuild `f.Blocks` keeping only reachable blocks.
This is sound because no *reachable* block's terminator targets an unreachable block
(else it'd be reachable), so removing the whole unreachable set dangles no kept
terminator — while it *does* drop the orphan→merge edges that caused the mismatch.
It also removes any store/load in dead code from consideration up front. `FaultPads`
are off `f.Blocks` and untouched. After pruning, `ComputeDom` runs on a CFG whose
every block (and every predecessor) is reachable, so phi/predecessor parity holds for
all backends. (This is a small DCE the pass performs as a precondition; it only runs
at `-O1+`.)

## Algorithm (one alloca at a time — correctness-first)

`pruneUnreachableBlocks(f)`, then `ComputeDom(f)` once, reused across allocas. For
each promotable alloca `a`:

1. **Collect** load-instrs and store-instrs of `a` (scan `f.Blocks`); `defBlocks` =
   the set of block indices containing a store to `a` (deduped).
2. **Pre-check** (above). If it fails, skip `a`.
3. **Phi placement:** `phiBlocks = d.IteratedDF(defBlocks)` (the DF⁺ query from
   Phase D). Create one `OP_PHI` per phi-block via **`newInstr(f, OP_PHI, a.TypeArg)`**
   (fresh SSA id) — NOT `EmitPhi`, which `addInstr`-appends *after* the block's
   terminator (transiently malformed) and would then need relocating. The phi
   Instrs are held in a block-index → phi map and are **not inserted into any block
   until the step-6 rebuild**, so an aborted alloca (below) leaves the IR untouched.
4. **Rename** by a dominator-tree DFS from the entry (iterative, explicit stack — no
   deep recursion), carrying `curVal @Instr` (the current reaching def of `a`):
   - entering a block: if it has a phi for `a`, `curVal = thatPhi`.
   - walking its instrs in order: at a store to `a`, `curVal = store.Args[1]`; at a
     load of `a`, record `repl[load] = curVal` (guaranteed non-nil by the pre-check).
   - for each CFG successor `s` (`d.Succs`), if `s` has a phi for `a`, append a
     `PhiEntry{ f.Blocks[b], curVal }` for the edge `b→s`. **If `curVal == nil` here
     (or at a load), ABORT this alloca** (leave it unpromoted, discard its
     not-yet-inserted phis) — see below. This is the true soundness guard: the
     load-only pre-check is necessary but not by itself sufficient, because a phi
     placed by IDF could in principle want an operand from a store-less path that no
     load observes. Given gen's entry-dominating zero-init store, `curVal` is in fact
     never nil for anything we promote, so this bail-out never fires today; it is the
     belt-and-suspenders that makes the pass sound regardless of future gen shapes.
   - recurse into dom-tree children (blocks whose `idom == b`) with the block-exit
     `curVal`; restore on the way back up (standard save/restore).
5. **Rewrite uses:** across `f.Blocks` *and* `f.FaultPads`, for every `Args[k]` and
   every `Phis[].Val`, if it is a replaced load, chase `repl` transitively (a load's
   reaching def may itself be a replaced load, e.g. `var x = y`; the chain terminates
   because reaching defs strictly dominate) to the final value and substitute.
6. **Delete** the loads, stores, and the alloca: rebuild each affected block's
   `Instrs` (the `bceBlock` pattern — reassign `blk.Instrs` to a fresh `@[]@Instr`;
   the old backing stays owned by `blk.InstrsVec`, so dropped instrs are unreachable,
   not freed early). Phi-blocks additionally get their new phi prepended in this
   rebuild.

Only **reachable** blocks are considered (the dom-tree DFS visits exactly those);
gen produces reachable, structured CFGs, and an unreachable load would fail the
pre-check anyway.

## Where it runs / ordering

Add `promoteScalars(m)` to `RunOptPasses` **before** `bceConstIndex`, still gated at
`-O1+` (it is a semantics-preserving optimization; `-O0` keeps the naive
alloca/load/store form). Pipeline:

- `RunOptPasses` (module-level, at the lowering boundary — bnc `compileModuleVia` +
  emit-llvm paths, VM `LowerModule`): mem2reg produces SSA + phis, then the BCE
  passes run on that SSA.
- Per-function lowering: LLVM emits phis natively; VM (`lowerFunc`) and native
  (`common.EmitObject`) run `EliminatePhis` to destruct them. `RunOptPasses` always
  precedes per-function lowering, so the ordering holds. At `-O0`, no phis are
  produced and `EliminatePhis` is a no-op.

## IR-verifier assertion

After `promoteScalars`, assert that **no promoted alloca has any surviving use** —
i.e. the loads/stores/alloca are truly gone and nothing references them. A surviving
use means the escape analysis under-approximated (a real miscompile), so fail loud.
Also assert:
- **phi/predecessor parity:** each emitted phi has exactly one entry per CFG
  predecessor of its block (`len(phi.Phis) == len(d.Preds[b])`, and each entry's
  `Block` is a distinct real predecessor). This is what would have caught the
  orphan-phi MAJOR *in the IR* rather than in the LLVM verifier; after the reachable
  prune it must hold.
- each emitted phi is `assertScalarPhi`-clean (it will be, by the type gate) — cheap
  insurance that we never hand `EliminatePhis` a phi it panics on, and that no phi
  operand is nil.

These run always (cheap, and this is the first phi producer — a wrong-code phi here
poisons every backend), not merely under the `SetVerifyIR` opt-in.

## Testing

- **IR unit tests** (`pkg/binate/ir`, hand-built funcs like the dom/phi-elim tests):
  a straight-line `store;load` promotes to the stored value (no phi); a diamond
  `if c { x=1 } else { x=2 }; return x` promotes to a 2-entry phi at the merge; a
  loop with an induction store on the back-edge promotes to a header phi; an
  **escaping** alloca (address passed to a call / stored as a value /
  `GET_ELEM_PTR`'d) is left untouched; a float/managed alloca is left untouched; a
  load-before-store slot is left untouched (pre-check gate).
- **Semantics-preserving end-to-end:** the full conformance suite at `-O1`+ across
  LLVM, native, and VM (the pass only runs when opt is on, so default runs are
  unaffected — but we should add `-O2` conformance coverage; scope that with the
  user). A size/quality check that an induction var actually becomes a phi.
- **Verifier on:** run the ir/opt tests with `SetVerifyIR(true)` so the
  no-surviving-use assertion is exercised.

## Scoping decisions

- **Conformance at `-O1+` — DECIDED (2026-08-27):** land the pass with IR-unit
  coverage first; add end-to-end opt-level conformance as a **separate follow-up**,
  structured as an **optimization-level dimension of the conformance matrix** (so
  each backend mode also runs at `-O1`/`-O2`). Tracked in `claude-todo.md`. This is
  the CI-wiring task, kept out of the pass-landing itself.
- **Managed promotion (Phase 2b)** stays out of scope — deferred, needs the refcount
  handling `assertScalarPhi` explicitly rejects.
