# Native frames: share stack slots, and stop reserving slots nothing uses

Status: IN PROGRESS (claimed 2026-11-10, work-3; the todo entry "The native-built compiler overflows its stack on
types nested within the 1024 limit", fixes (a) and (b)).

## Problem

A valid type nested ~900-1000 levels deep (structs in structs) crashes the native-built bnc: each level is four
parser frames (parseType, parseTypeInner, parseStructType, parseStructField), and the native backends' frames are
2.5-3.5x LLVM's.  Measured at binate `be5bcd88d` (aarch64, `-c --pkg pkg/binate/parser`):

| function          | native | LLVM -O2 |
|-------------------|-------:|---------:|
| parseType         |  1,792 |      608 |
| parseTypeInner    |  2,224 |      768 |
| parseStructType   |  2,560 |    1,600 |
| parseStructField  |  3,008 |      864 |
| one struct level  |  9,584 |    3,840 |
| parseTypePrefix   |  4,688 |    2,192 |
| parsePrimaryExpr  |  7,792 |    3,152 |

Where native's bytes go (PlanFrame, a temporary breakdown under `--x-copy-stats`):

| function         | spill, homed | spill, not homed | allocas      | load / call / other regions |
|------------------|-------------:|-----------------:|-------------:|----------------------------:|
| parseTypePrefix  | 224 x 8      | 113 x 8          | 1,320 (34)   | 192 / 272 / 96              |
| parseStructField | 138 x 8      | 56 x 8           | 784 (21)     | 512 / 136 / 0               |
| parsePrimaryExpr | 314 x 8      | 172 x 8          | 2,408 (44)   | 608 / 568 / 160             |

- PlanFrame gives every parameter and value-producing instruction an 8-byte spill slot, also a value the register
  allocator homes for its whole life (the homed column: ~35% of each frame).
- No two slots, regions or allocas ever share bytes, whatever their lifetimes.
- IR-gen's allocas: each `p.tok.Pos` passed by value is loaded (a region), stored to an alloca and copied
  (`__copy_Pos`), reloaded (a region) and passed; a discarded `p.expect(...)` result gets a region and then an alloca
  copy made only to destroy it (allocStored in emitTempRefDecs).

## Steps

1. **No spill slot for a homed value.**  The emitters ask for a homed value's slot in a few places, which change
   first: call-result collection asks for the slot before checking the home; OP_BOX keeps its scalar operand in its
   result's slot; an indirect call saves an argument-register-homed target in a slot (its own on aarch64, the call's
   on x64); an interface / function-value call's scalar result comes back through a buffer that is the result's
   slot.  Instruction-local uses get a scratch cell of the frame; parameters keep their slots (their landing pads).
2. **Spill slots shared by live range** (stack-slot colouring) for the values that still have them, from the
   liveness the allocator already computes.
3. **Aggregate data regions shared by lifetime**: a region lives while its value does and while any value that
   reads it in place does (aliasing loads, extracts).
4. **Allocas shared by lifetime**: IR-gen marks where a local's scope or a temporary ends, the backends share
   allocas with disjoint lifetimes (the LLVM backend can emit `llvm.lifetime.*` from the same marks).
5. **IR-gen copies**: destroy a discarded aggregate result where it is, pass a field read by value without the
   alloca round trip.

Each step lands on its own, validated on the three native conformance modes (aa64, x64 darwin, arm32 bare metal and
Linux), with the parser frames re-measured.  Done when a struct and a func-parameter nesting at 1023 levels compile
under the native-built bnc, and the frames are near LLVM's.

## Step 4 design (2026-11-17)

Spec §18.4 `mem.scope-exit`: the storage of every local declared in a block ends when that execution of the block
exits (a loop body's locals are new storage each iteration; a raw pointer to one after its block exits is dangling),
and `mem.temporary` ends a statement temporary's at the end of its statement.  So allocas whose lifetimes are disjoint
may share bytes; the IR has to say where the lifetimes end.

- **IR.**  `Instr.Scope` on an OP_ALLOC: the id (>= 1) of the scope whose exit ends its storage; 0 = none (lives the
  whole function and is never shared).  A void `OP_SCOPE_END` with no operands carries the id in `Scope`, emitted
  where control leaves the scope.  `Func.NextScope` numbers a function's scopes.  An id, not an operand naming the
  alloca: the passes' escape checks treat any operand appearance of an alloca as an escape (mem2reg's
  allocaEscapes), so an operand marker would turn promotion and SROA off for every marked local; an id is invisible
  to them, and a pass that deletes an alloca leaves nothing dangling.  The inliner, the one place instructions move
  between functions (cloneInstr), renumbers a callee's ids past the caller's.  The verifier checks the ids; the VM
  and the native emitters emit nothing for OP_SCOPE_END.
- **IR-gen.**  openScope takes a fresh id; defineVar stamps a variable's slot (an OP_ALLOC of the function, not a
  parameter's, not a frame closure record's) with the innermost scope's id; closeScope emits OP_SCOPE_END at a
  fall-through exit, after the releases; emitLeaveScopes emits it for each scope a `break`, `continue` or `return`
  leaves.  Statement temporaries get a statement scope later (4e).
- **Native.**  PlanFrame: an alloca with a scope is live from its OP_ALLOC and from every instruction naming it until
  an OP_SCOPE_END of its scope -- a forward dataflow over the blocks, so a loop's back edge carries a live alloca to
  the loop's top, and a use a pass moved above the OP_ALLOC still counts.  Two allocas interfere when one is live
  where the other is referenced; they pack first fit by size and alignment, like the spill slots.  Scope 0 keeps
  bytes of its own.
- **LLVM.**  `llvm.lifetime.start` where the OP_ALLOC is (its alloca is hoisted to the entry), `llvm.lifetime.end`
  at each OP_SCOPE_END for the scope's allocas still in the function; LLVM's stack colouring then shares them.

Sub-steps, each landing on its own: 4a the IR (field, op, verifier, VM / backends ignore it, inliner renumbering, IR
printing) with nothing emitting it; 4b IR-gen marks variables' scopes; 4c native PlanFrame shares by them (frames
re-measured); 4d the LLVM intrinsics; 4e statement temporaries.

## Progress

- 2026-11-10: measured (above); step 1 started.
- Step 1 DONE: binate `20b98a7ba` (2026-11-11).  A homed value has no slot unless an emitter uses it (a call's
  result, an OP_BOX's result, an indirect or handle call's target; parameters keep theirs) -- needsSpillSlot.
  aarch64 frames after it: parseType 1,056, parseTypeInner 1,424, parseStructType 2,016, parseStructField 1,920 (a
  struct level 6,416, from 9,584), parseTypePrefix 2,992, parsePrimaryExpr 5,360.  Validated: native aa64 full
  conformance (self-hosted) 3912/0; call-centred subsets on native x64 458/0, arm32 bare metal 450/0, arm32 Linux
  458/0; the native unit tests; cmd/bnc compiled natively for x64 and arm32.
  - Left from the review: a homed direct-call result still has a slot it never uses on aarch64 / arm32 (their
    result collection asks for the slot before the home); step 2's sharing makes it nearly free, or move the ask
    after the home check.  aarch64's OP_CALL_HANDLE does not save a handle homed in an argument register before
    placing the arguments (OP_CALL_INDIRECT and x64 do); not reproduced in three probes (the allocator put the
    handle in x4 / x27), and only rt's `_call_dtor` / `_call_free_fn` produce the op.
- Step 2 DONE: binate `788ff5285` (2026-11-11).  The allocator's passes record each value's live span (noteLiveSpans);
  PlanFrame packs the slots of the values with spans first, nearest SP (planSharedSpills / packSpillSlots: first fit
  by span start); a homed call / OP_BOX result's kept slot spans its own position only.  aarch64 frames: parseType
  768, parseTypeInner 1,248, parseStructType 1,952, parseStructField 1,744 (a struct level 5,712), parseTypePrefix
  2,448, parsePrimaryExpr 4,800.  Conformance spec/07-types/305 (struct / array / pointer / func-value nestings at the
  1024 limit, written as one type expression each) passes in all 11 modes; a native bnc from before step 1 dies on it.
  Validated: native aa64 / x64 / arm32 bare-metal full conformance (3915 / 3915 / 3870, 0 failed), arm32 Linux subset.
- Where the parser's frame bytes are now (all its functions, aarch64, after step 1): allocas 41%, aggregate value
  regions 28%, scalar spill slots 12% (step 2 shares these), aggregate pointer slots 9%.  So steps 3-4 (regions and
  allocas by lifetime) and 5 (IR-gen's copies) hold most of what is left of the gap to LLVM.
