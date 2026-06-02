# Plan: hoist byval-spill allocas to function entry block

**Status**: LANDED 2026-06-02 (binate `440485b0`).  `writeByvalArgPreamble` now emits
only the `store`; the `alloca` is hoisted to the entry block by a
new `emitByvalAllocDecls`, called from `emitFuncDbg`'s alloca-hoist
pre-pass (alongside OP_ALLOC / OP_MAKE_SLICE / sret).  The
`ulimit -s` band-aid was removed in the same change.  Verified: the
full `builder-comp-int` unit suite is 34/0 at the **default 8 MiB
stack** (was 24/10 even WITH the 64 MiB band-aid); `pkg/binate/types`
runs all 527 tests where it used to crash after test #1; `execLoop`
went from 14 dynamic stack-adjustments to 0.  Conformance unchanged
(`builder-comp` 450/0/1; native x64 byval cluster 331/337/411 still
green).
**Severity**: MAJOR (resolved)
**Tracks**: `claude-todo.md` MAJOR entry
  "bnc codegen: byval-spill alloca emitted at call site leaks per
  loop iteration"

## Follow-up: func-value-call + iface-method call-site allocas — LANDED 2026-06-02 (binate `d9800429`)

The same call-site-alloca leak class existed on two more paths and
was closed (latent-footgun hardening — a sweep showed no package
currently triggers them, but they're the same shape):

- **OP_CALL_FUNC_VALUE**: `emitFuncValueArgPreamble`'s `.ap<i>`
  aggregate-arg spill slots + `emitCallFuncValue`'s `.rb` aggregate-
  return retbuf.
- **OP_CALL_IFACE_METHOD**: `emitCallIfaceMethod`'s `.rb` sret buffer
  for a >16-byte aggregate return (its own comment had flagged the
  inline alloca as wanting hoisting).

Fix mirrors the byval split exactly: the call-site emitters now emit
only the store/bitcast/call/load; new `emitFuncValueCallAllocDecls`
and `emitIfaceMethodSretAllocDecl` emit the allocas, wired into
`emitFuncDbg`'s hoist pre-pass via `OP_CALL_FUNC_VALUE` /
`OP_CALL_IFACE_METHOD` branches.  `OP_CALL_HANDLE` and
`OP_CALL_INDIRECT` were verified to emit NO call-site allocas, so
they need no hoisting.  Regression tests
`TestFuncValueCallAggAllocasHoistedToEntry` /
`TestIfaceMethodSretAllocaHoistedToEntry` pin both.  Verified:
codegen unit 180/0, full `builder-comp` unit 35/0, conformance
`builder-comp` 454/0/1.

---

**Original plan below (design / pre-implementation).**

## Problem

`pkg/binate/codegen/emit_util.bn::writeByvalArgPreamble` emits the
byval argument preamble at the call site:

```binate
// emit_util.bn:344
func writeByvalArgPreamble(out buf.CharBuf, instr @ir.Instr) buf.CharBuf {
    for i := 0; i < len(instr.Args); i++ {
        var arg @ir.Instr = instr.Args[i]
        if !isByvalParam(arg.Typ) { continue }
        out = out.WriteStr("%v")
        out = out.WriteInt(instr.ID)
        out = out.WriteStr(".bv")
        out = out.WriteInt(i)
        out = out.WriteStr(" = alloca ")
        out = out.WriteStr(llvmType(arg.Typ))
        out = out.WriteStr("\n  store ")
        ...
    }
    return out
}
```

This is called from `emit_call.bn:49`:

```binate
// emit_call.bn:46-49
// Pre-emit alloca + store for byval-eligible aggregate args so the
// call line below can reference `%v<callID>.bv<i>` with byval.
out = writeByvalArgPreamble(out, instr)
```

The emission happens inline at the call site's basic block.  In
straight-line code this is harmless: the alloca releases when the
enclosing function returns, which happens shortly after the call.

In a **long-running loop body**, every iteration allocates a fresh
copy of the byval spill.  Allocas do NOT release on basic-block
exit; they only release at function return.  After N iterations,
the host stack carries N copies of every byval-spill in the loop.

bni's `execLoop` is the worst case: it has 13 byval-spill sites
(`execStringOp`, `execFuncRefOp`, `execMemoryOp`, `execArithOp`,
`execCmpOp`, `execUnaryOp`, `execOp64`, `execNumericCast`,
`execExtern`, and a few more — each takes `instr BCInstr` by
value).  Each spill is 48 bytes (`BCInstr` = 6 i64).  Per
iteration, 1-3 spills typically execute (depending on which opcode
arm is taken).  After ~165K iterations the 8 MiB host stack is
exhausted.

The team already worked around one analogous leak by hand
(`var callArgs @[]int = make_slice(int, 64)` hoisted out of the
`BC_CALL` branch — see the comment at `vm_exec.bn:24-36`).  That
fix is a per-symptom patch; the structural fix lives in codegen.

## LLVM idiom: allocas in the entry block

LLVM's documentation and every standard frontend (clang, rustc,
GCC) follow the rule: **all allocas go in the function's entry
block**.  This guarantees:
- mem2reg and other optimizations can promote allocas to SSA
  registers (mem2reg requires entry-block placement).
- The alloca is allocated exactly once per function activation,
  regardless of how many times it's reached in control flow.
- The stack frame size is statically determinable at function
  entry (no dynamic-alloca surprises for codegen / unwinding).

bnc's byval-spill emission violates this rule.

## Fix shape

Two coordinated changes:

1. **Track byval-spill needs in the IR-gen pass** (`pkg/binate/ir/...`
   or a new "byval-spill collector" inside codegen):
   - For each `OP_CALL` whose args contain a byval, allocate a
     spill slot at the function level (not the call level).
   - Each unique `(byval_type, spill_index_in_function)` gets one
     entry-block `alloca`.
   - Multiple call sites with byval args of the same type SHARE
     spill slots (the spill is dead after the call returns, so
     overlapping call sites can reuse the same slot).

2. **Update codegen's `writeByvalArgPreamble`**:
   - Move the `alloca` emission to the function-entry write phase
     (which already exists for IR-Generator-allocated values like
     local variables).
   - Keep only the `store <T> %arg, ptr %spill_N` at the call
     site (the runtime fill of the byval slot).

Alternatively (simpler, less efficient): emit ONE entry-block
alloca per byval call site without de-duplication.  All entry-
block allocas use minimal stack (LLVM coalesces aggressively in
backends).

## Sites to touch

### `pkg/binate/codegen/`

- `emit_util.bn`:
  - `writeByvalArgPreamble` — change to emit only the `store`,
    not the `alloca`.
  - Add a new `writeByvalSpillEntry(out, fn)` that emits all the
    function's byval-spill allocas at entry.
- `emit.bn` (the per-function entry-block emit):
  - Call `writeByvalSpillEntry` after the existing entry-block
    emit for IR locals.

### `pkg/binate/ir/`

- `Func` struct: add a `ByvalSpills @[]@ByvalSpillInfo` field
  (similar to existing locals tracking).
- IR-gen: when emitting `OP_CALL` with byval args, register the
  spill slot in the enclosing `Func.ByvalSpills` (assign unique
  ID, capture type).
- `Instr.Args[i]` byval references: encode the spill ID so
  codegen knows which entry-block alloca to reference.

### Tests

- `pkg/binate/codegen/emit_call_test.bn`: assert byval-spill
  `alloca` shows up exactly once per spill ID at function entry,
  not at the call site.
- `pkg/binate/codegen/emit_byval_test.bn` (new): pin "two calls
  in a loop with byval args produce one alloca total, not two
  per iteration."
- `pkg/binate/vm/vm_exec_byval_pin_test.bn` (new): pin that
  execLoop's IR has ≤1 byval-spill alloca per `(byval_type)` at
  function entry.  Regression test for this exact symptom.

### Integration verification

- Re-run `./scripts/unittest/run.sh builder-comp-int` after the
  fix lands.  Expect the 10 currently-failing packages on macOS
  (24 currently pass) to flip green.
- Remove the `ulimit -s 65520` band-aid in
  `scripts/unittest/runners/builder-comp-{int,int-int,comp-int}.sh`
  once the fix is verified green on Linux CI.
- Conformance unaffected (single-test binaries don't have the
  iteration count to trigger the leak); should stay 100% on
  every mode.

## Phasing

If pursued as one commit:
1. Add `ByvalSpills` tracking to `ir.Func`.
2. IR-gen: register spill in `OP_CALL` with byval args.
3. Codegen: hoist `alloca` emission to entry block; keep `store`
   at call site.
4. Add the three test pins.
5. Verify locally on macOS (`builder-comp-int` packages: 24 →
   34/34).
6. Cherry-pick to main.
7. Wait for one CI cycle; if green on Linux, **remove the
   `ulimit -s` band-aid in a follow-up commit**.

If split:
- Commit A: shared spill-slot machinery + tests (no semantics
  change yet because byval is still emitted at call site).
- Commit B: flip the emission to the entry block.
- Commit C: remove the band-aid.

## Estimated LOC

- Codegen + IR-gen changes: ~150-300.
- Tests: ~200-400.
- Cleanup of `ulimit -s` band-aid: ~30 (3 runner files).

## Risk + rollback

- Risk: the `alloca` move could break tests that pin the exact
  text of emitted IR.  Mitigation: those tests should be updated
  to reflect the canonical (entry-block) placement.
- Rollback: revert the commit.  No on-disk format / linker
  implications.

## Out of scope

- General hoisting of ALL allocas to entry block (e.g.,
  `var x BCInstr = code[pc]` inside the loop body).  Today only
  the byval-spill is in a loop body (verified by IR inspection
  of execLoop — see claude-todo.md MAJOR entry).  Other inline
  allocas already go to the entry block.  This plan addresses
  the byval case specifically; if a future codegen change moves
  other allocas off the entry block, that's a separate fix.
- bni-VM eval refactor (the previous draft plan's direction).
  Empirically, the symptoms attributed to "extern-callback
  recursion" are 100% explained by the byval-spill leak — no
  refactor needed.
- Mach-O writer LC_DYSYMTAB + symtab sort (filed separately as
  `plan-macho-dysymtab.md`).
