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

## Risks

1. **A missed store = silent miscompile** (reload reads a stale/garbage slot).
   Top risk. Mitigated by the invariant proof, per-point liveness, conformance,
   and adversarial review. Consider a debug validator: assert every slot reload
   targets a slot that was written on the current path.
2. **Per-point liveness cost** — one extra backward pass per block at emit; cheap
   relative to emission. Retain from `AllocateRegisters` if it shows up.
3. **Shared RegMap churn** — the dirty marker is additive and inert for backends
   that keep eager spill; no behavior change until a backend opts in.
