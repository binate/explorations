# Plan: native backend per-function analyses that scale with function size

Status: in progress (work-3, started 2026-10-20).  Tracks the fix for the claude-todo entry "CI red: native x64
unit — the runner is shut down partway through `pkg/binate/check`" (#5 of the 2026-10-20 CI triage), and is the
native-backend half of the older entry "Compile time is superlinear in function size" (2026-09-27); that
entry's iropt mem2reg (-O2) and compiler-wide `slices.Append` parts are NOT in this plan.

## Problem

The native backends' per-function analyses in `pkg/binate/native/common` are quadratic in a function's size,
in time AND memory.  The generated unit-test runner's `main` (one function, a block group and a managed local
per test) is the canonical trigger: compiling `pkg/binate/check`'s tests (1569 tests) natively takes 64.6 GB
peak footprint / 370 s, against 0.65 GB for LLVM, so CI's native unit lanes are OOM-killed (x64) or time out
(aa64).  Synthetic N trivial tests, native: 2.0 GB / 6 s (N=250), 6.7 GB / 22 s (500), 25.8 GB / 124 s (1000),
102.6 GB / 605 s (2000).  (Scratch harness: a package `pkg/scaletest` with N `TestS<i>` functions compiled with
`bnc --test --backend native`, timed with `/usr/bin/time -l`.)

Profile at N=1000 (aarch64, `sample`), share of EmitObject:
- RegMap.PlanFrame ~68%: LastUseArgLoads ~45% (per aggregate load: slotReachPtr + slotAliases fixpoints and
  slotDeadAfterLoad, each rescanning every block, with NextID-long arrays), AggLoadElision ~23% (per load:
  locating the load, valueUsedInOtherBlock, lastAliasUseIndex, allocaAddrConfinedToLoadStore — each a
  whole-function scan).
- ComputeLiveBeforeAllFused ~18%: a NextID-long `bool` array per INSTRUCTION, all retained through emission
  (instructions × values bytes — the dominant memory term).
- ComputeLivenessFused + ComputeLivenessFp ~13%: four dense NextID-long `bool` sets per block, per register
  class; collectBlockRanges allocates and scans a NextID-long array per block; emitRawRange grows each value's
  range list by copy-per-append.

## Approach

Analysis results must not change: every stage is checked for byte-identical native output against the
compiler built from its parent on the same base (see Validation).

### Stage 1 — live-before as a cursor, not per-instruction sets
Replace `ComputeLiveBeforeAllFused` (per-instruction id-indexed sets) with a `LiveCursor` in common: one
id-indexed `@[]bool` per function (the current point's live set) that the emit loop steps through each block.
`EnterBlock(bi)` sets it to the block's live-in and precomputes, by one backward scan from the live-out, which
uses die at each instruction and whether each def is live after it; `Step(ii)` turns live-before(ii) into
live-after(ii).  Clearing between blocks walks the ids set, not NextID.  The three backends' emit loops read the
cursor's set where they read `lb[ii]` / `lb[ii + 1]` today (RegMap.CurLive and spillDirtyLive only test
membership).

### Stage 2 — liveness over cross-block values, as bitsets
LiveIn/LiveOut only ever hold values live across a block boundary (upward-exposed in some block).  Number those
densely and keep LiveIn/LiveOut as packed bitsets over them; give the sets accessors (`IsLiveIn` / `IsLiveOut`,
and the live-out ids of a block) instead of raw id-indexed slices.  computeBlockDefUse uses a function-wide
stamped "defined" array instead of a NextID allocation per block.  collectBlockRanges reuses one function-wide
`liveEnd` array, seeded from the block's live-out ids and reset through the list of ids it touched; range lists
grow amortized (`vec`), not by copy-per-append.

### Stage 3 — the PlanFrame slot analyses over a use index
Build, once per function in PlanFrame, an index of each instruction's (block, position) and each value's use
sites (instruction, operand; phis included).  slotReachPtr / slotAliases become worklists over the uses of the
values they reach (re-visiting a value when it turns from reached to pointer), with sparse visited sets (a
stamped array plus a touched list) instead of NextID-long arrays per slot; slotDeadAfterLoad / touchesSlot,
CallResultSlots, LastUseArgLoads, RetbufLocal, InPlaceParamSlots and AggLoadElision's helpers
(valueUsedInOtherBlock, lastAliasUseIndex, allocaAddrConfinedToLoadStore, locating the load) query the index.

### Stage 4 — a guard against regressions
A test that compiles a large generated function (the N-test package, or one long `main`) natively and fails on
a time / memory budget well above the fixed cost and well below the quadratic one.  Where it runs (unit, e2e,
CI) is the user's call.

## Validation (each stage)
- native/common, native/aarch64, native/x64, native/arm32 unit tests.
- Byte-identical output: the native self-compile of bnc, and the objects of a conformance subset, from the new
  compiler and from one built from the stage's parent on the same base, on aa64, x64 (darwin) and arm32.
- Native conformance subsets on `builder-comp_native_aa64-comp_native_aa64`,
  `builder-comp_native_x64_darwin-comp_native_x64_darwin`, `builder-comp_native_arm32_baremetal` (and
  `builder-comp_native_arm32_linux` in docker).
- The scaling harness: N = 250 … 2000 time and peak footprint, before / after.
- The original trigger: `bnc --test --backend native pkg/binate/check` within a sane budget.

## Progress
- [ ] Stage 1
- [ ] Stage 2
- [ ] Stage 3
- [ ] Stage 4
