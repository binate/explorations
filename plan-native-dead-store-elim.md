# Plan: native backend dead-store elimination (lazy spill)

Follow-up to within-block value retention (landed all three backends: aarch64
`60ed89b0e`, arm32 `62f474df1`, x64 `7558a35ec`). Retention removed the redundant
*reloads* (a use reads a cached register instead of `mov rd, [slot]`). This plan
removes the redundant *stores*: `spillAndReset` currently spills EVERY result to
its slot on definition, but for a value used entirely from the retained register
(never reloaded), that store is dead.

Static evidence the stores are there to remove (x64, cmd/bnc, via
`perf/native-vs-llvm.sh --arch x86_64-darwin`): N(native) emits ~209k frame
reloads and ~209k frame spills; retention already cut reloads ~16% but left the
spills essentially untouched (by design). The spills are the target here.

## Mechanism: lazy spill

Don't store a result on definition. Keep it in its register (cached), marked
DIRTY (in-register, not yet written to its slot). Write a dirty value to its slot
only when it is about to become unreachable from the register AND is still needed:

- **On eviction** (`allocReg` reuses a dirty cached value's register): store it
  first — it will be reloaded on its next use.
- **Before any cache-dropping reset** (the mid-block retention barrier before a
  non-retention-safe op; a branch-emitting / returning-call reset; the block-end
  transition): store each dirty cached value that is LIVE-AFTER that point; drop
  the rest storeless — their store was dead.
- **Reload path unchanged**: `getOperand` still reloads from the slot on a cache
  miss; a reloaded value is CLEAN (its slot already holds it — SSA values are
  immutable, so no re-store on a later eviction).

### Correctness invariant

Every slot READ is preceded by a slot WRITE. A slot read is a cache miss on a
use; a cache miss means the value was dropped (evicted or reset). Eviction stores
it; a reset stores it iff it is live-after (and a later use is what makes it
live-after). A value dropped while dead is never used again → never read from its
slot → its store is correctly elided. (Spills are the only writes to these slots;
homed values bypass slots entirely.)

## Liveness needed: per-point live-after

Block-granularity `LiveOut[b]` is not enough — the mid-block barrier resets drop
the cache at arbitrary points. Derive per-instruction live-after within a block by
a backward walk from `LiveOut[b]`:

    liveAfter[last]  = LiveOut[b]
    liveAfter[i]     = (liveAfter[i+1] \ def(i+1)) ∪ uses(i+1)

`ComputeLiveness(f, wordBytes)` (pkg/binate/native/common/regalloc_liveness.bn)
already returns `LiveOut` per block (id-indexed bool), plus `Allocatable[id]`,
`BlockFrom/To`, RPO. The homing pass (`AllocateRegisters`) computes it internally;
emitFunc can call `ComputeLiveness` again (cheap) or we retain it from
`AllocateRegisters`. Only ALLOCATABLE ids (scalar, non-aggregate, non-float,
non-alloca) are cached/spilled through this path, matching the liveness universe.

## RegMap change (shared, native/common — BUILDER-compiled)

Add a per-cache-entry DIRTY marker (parallel to `IDs`/`Regs`), set true on
`AssignReg` from a fresh def, false when a value is reloaded from its slot or
after it is stored. Add accessors to store-and-clean and to enumerate dirty
entries. Additive: aarch64/arm32 keep eager spill until they adopt lazy spill.
(native/common and native/x64 are both in cmd/bnc's BUILDER-compiled tree — keep
the additions BUILDER-safe: arrays, bools, loops only.)

## Emit-loop restructure (x64 first)

Replace `spillAndReset(result eager-store + conditional reset)` with:

    for each instr:
      ClearUsed()
      if !retentionSafe(op): spillDirtyLive(liveAfter[pos]); ResetRegs()   // barrier
      emitInstr(...)                                                        // result stays DIRTY, cached
      if branchEmitted || EmitsReturningBl(op): spillDirtyLive(liveAfter[pos]); ResetRegs()
    // block end:
    spillDirtyLive(LiveOut[b])   // store live-out dirties; next block-entry ResetRegs clears the rest

`allocReg` eviction path: if the victim is dirty, store it before reuse.

Where `spillDirtyLive(liveSet)` = for each dirty cached id in liveSet with a spill
slot, emit the store and mark clean.

## Rollout

1. **x64 first** — restructure emit loop + allocReg eviction; RegMap dirty
   support; per-point liveness. Unit tests + full native_x64 conformance (3020/0)
   + adversarial review + measure the spill reduction via `--arch x86_64-darwin`.
2. **arm32**, then **aarch64** — same shape (their pools/emit loops mirror x64's).
   Each its own commit, conformance-green, reviewed.

## Testing

- Unit (native/x64): a value used only from cache (no barrier, no eviction, not
  live-out) emits NO store; a live-out value IS stored at block end; a value used
  after a barrier IS stored before the barrier; an evicted dirty value IS stored.
- A liveness-derivation unit test (live-after backward walk matches expected sets
  on a small CFG).
- Full native_x64 conformance 3020/0 (correctness spine — a missed store is a
  UAF/wrong-value miscompile).
- Adversarial review focused on the invariant: any un-stored value that is later
  reloaded; a barrier/branch/block-end that drops a live dirty value; eviction of
  a dirty value without a store; the reloaded-value-is-clean assumption.

## Adversarial review findings — CORRECTED DESIGN (supersedes the above where noted)

An adversarial design review found two fatal holes and three major gaps in the
first draft. The corrected design:

- **F1 (was fatal): gate lazy-spill on `isAllocatableDef`.** `PlanFrame` gives a
  spill slot to EVERY value-producing instruction (aggregates, floats,
  int64-on-32bit included), and `getOperand` caches any of them — so the "dirty
  universe == liveness universe" claim was FALSE (liveness covers only
  allocatable scalars). Only `isAllocatableDef` results are marked DIRTY and
  lazy-spilled; every non-allocatable result keeps the eager store on definition
  exactly as today. Now the dirty set and the liveness set coincide.
- **F2 (was fatal): the pre-op barrier stores `liveBefore[pos]`, not
  `liveAfter[pos]`.** The barrier `ResetRegs` runs BEFORE `emitInstr`, so the op's
  own operands (which will cache-miss and reload) must be stored first; a
  last-use operand is absent from `liveAfter[pos]`. Use
  `liveBefore[pos] = uses(pos) ∪ (liveAfter[pos] \ def(pos))` for the pre-op
  barrier (block-entry uses `LiveIn[b]`, though the cache is empty there). The
  POST-op branch/returning-call reset correctly uses `liveAfter[pos]`. The two
  sites need DIFFERENT sets.
- **F3 (major): the direct `EvictReg` sites must store-before-evict too.**
  `pickScratchGP`/`pickTwoScratchGP` (x64_float.bn, OP_CAST's uint64↔double
  lowering — retention-safe, so dirty cache flows in) call `m.EvictReg` directly
  on candidates that include RDX/R11 (pool regs). Route them through the same
  store-if-dirty-with-slot helper (or push the store into a wrapper). Their
  "eviction is loss-free" doc-comments become false under lazy spill and must be
  updated.
- **F4 (major): the eviction store guards on `LookupSpill(id) >= 0`.** A
  materialized alloca pointer is cached via `nextReg → AssignReg` but has an
  AllocID, not a SpillID (`LookupSpill == -1`); storing to slot -1 corrupts the
  frame. F1's gate excludes allocas (non-allocatable), but keep the guard anyway.
- **F5 (major): set the DIRTY bit per call-site, not inside `AssignReg`.**
  `nextReg → AssignReg` is shared by result-def (dirty=true), slot-reload
  (dirty=false, clean — the slot already holds the value), and alloca-materialize
  (clean). `AssignReg`'s REPLACE path (the mutable-variable id-reuse case, the
  same one the AssignReg-replace fix addressed) is a fresh def → dirty=true. The
  reload path must clear the bit after `nextReg`. Getting the replace case wrong
  elides a redefined value's store → a later reload reads the OLD value.

**Confirmed sound (no change):** `LiveOut` is indexed by f.Blocks position,
matching the emit loop's `for bi`; `AllocateRegisters` neither retains liveness
nor mutates `f`, so `emitFunc` recomputing `ComputeLiveness(f, 8)` is
deterministic and cheap; homed values bypass the cache (never in IDs/Regs) so
`spillDirtyLive` can't touch them; slots are 1:1 per id so a reloaded value is
genuinely clean.

**Build the invariant validator FIRST** (the review's recommendation): a
debug-mode check that every slot reload (`getOperand`'s `LookupSpill` path)
targets a slot that has been written on the emit path — executable proof of the
invariant that catches F1/F2/F3 immediately. A per-function monotonic
"everWritten" slot set is a cheap first cut (a slot is valid once any store
writes it; reload asserts membership); tighten to per-path if needed.

## Risks

1. **A missed store = silent miscompile** (reload reads a stale/garbage slot).
   Top risk. Mitigated by the invariant proof, per-point liveness, conformance,
   and adversarial review. Consider a debug validator: assert every slot reload
   targets a slot that was written on the current path.
2. **Per-point liveness cost** — one extra backward pass per block at emit; cheap
   relative to emission. Retain from `AllocateRegisters` if it shows up.
3. **Shared RegMap churn** — the dirty marker is additive and inert for backends
   that keep eager spill; no behavior change until a backend opts in.
