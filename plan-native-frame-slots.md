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
  where control leaves the scope.  `Func.NumScopes` counts a function's scopes.  An id, not an operand naming the
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

Notes for 4b-4d from the 4a review (2026-11-17):
- 4a gives copy-prop's folded source the destination's scope (the source stands in for the destination for as long
  as anything reads it), SROA's field slots their aggregate's, and every inlined call its own range of the caller's
  ids.  A new pass that merges or replaces allocas must do the same.
- 4b: a scope end interposed where a pass looks for a shape makes it optimize less, never wrongly: native branch
  fusion wants the compare right before its branch (native/fold/branch_fuse.bn), and jump threading's isJumpOnly /
  constPhiBranch, the inliner's isEmptyPad and the OpWritesNothing barrier (sink_extract, AggLoadElision) all see it.
  Emit a scope end only for a scope that stamped an alloca, and never between a compare and its branch.  The marker
  costs no code on any backend, no VM dispatch, nothing in the inliner's size count, and does not stop the native
  register cache (all three retention lists take it).
- 4c: the S-alloca in-place read (native/common/common_aggload_elision.bn, and codegen/emit_bulk_elide.bn for LLVM)
  is barred only by a store to the slot between the load and its last use; once slots share bytes, an end of the
  source's scope in that span must bar it too, and onlyPureOpsStrictlyBetween's "each OP_ALLOC is memory of its own"
  no longer holds.  OpWritesNothing keeps OP_SCOPE_END out on purpose (its comment says why).  RetbufLocal and an
  in-place parameter's slot own no frame bytes and are not allocas to LLVM: no sharing, no lifetime intrinsics.
- An inlined callee's scope-0 slots and the inliner's result / tuple slots live for the whole caller, so they never
  share; safe, but a callee inlined many times grows the frame by its slots each time.

Notes for 4c-4e from the 4b review (2026-11-17):
- 4c's PlanFrame should check the property it relies on and fail loud when it does not hold: no read of a slot of
  scope s after an end of s unless an OP_ALLOC of the slot or a whole store comes first.  It would have caught the
  copy-prop narrowing 4b's review found (4a gave the folded source the destination's narrower scope; a `break` ending
  that scope before the copy reached a later read of the source).  Copy-prop now keeps the source's scope only when
  the two are equal.
- `var a T` for a plain struct or array with no initializer has no store: its first write is the zero-fill at its
  OP_ALLOC, each time the declaration runs.  PlanFrame must keep that fill at the OP_ALLOC (never hoist it).
- An inlined callee's plain `return` (no deferred block) emits no scope ends, so its slots stay live through the rest
  of the caller; the inliner could end the clone's reserved range at the continuation.
- Hidden `.`-named slots (`.complit`, `.borrow_temp`, `.forin.*`, `.typeswitch.scrutinee`) keep scope 0 (a hidden
  slot's allocation can sit in a short-circuit operand that did not run while its scope's exit reads it); the
  `.`-prefix test in stampVarScope is the convention that decides it.
- sink_extract and the aggregate-load elision treat any scope end as a barrier; only the source's own scope needs to
  be one.

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
- Step 4a DONE: binate `c2737d70c` (2026-11-17), after ir.bni was split for room (`62ef28c65`: the Collect* gather
  moved to pkg/binate/irgather).  Instr.Scope, Func.NumScopes, OP_SCOPE_END, irbuild.NewScope / EmitScopeEnd, the
  verifier's VERR_BAD_SCOPE; the inliner reserves each call's range of ids; SROA's field slots and copy-prop's folded
  source keep a meaningful scope; no backend emits code for the marker (the VM drops it before lowering), the native
  register caches flow through it and the inliner's size count skips it.  Nothing emits it yet.  Next: 4b.
- Step 4b DONE: binate `867d46571` (2026-11-18).  IR-gen stamps a named variable's own slot with its scope (hidden
  `.` slots, parameters' slots and package references stay 0) and ends the scope after its release wherever control
  leaves it.  Jump threading and the constant-phi fold thread past scope ends, carrying them to the target.  Copy-prop
  keeps the source's scope only when it is the destination's.  cmd/bnc before / after: LLVM objects identical at -O0 /
  -O2, emitted LLVM IR identical for all 85 packages; native objects slightly smaller (aa64 -160 B, x64 -304, arm32
  -1,916) from the markers' instruction positions in the allocator.  Next: 4c.
- Step 4c DONE: binate `eb2442a91` (2026-11-18).  PlanFrame shares the bytes of allocas with a scope
  (common_alloca_share.bn): live from each access (OP_ALLOC, an instruction naming the slot, a call placed into it)
  to an end of its scope; interference where one is accessed while the other is live; first fit in first-access
  order.  The fail-loud check in the 4b notes became a demotion: a program may keep a slot's address past its scope
  (only reading through it is undefined), so only a LOAD of the slot not reached by a whole write since its scope
  ended counts, and such a slot keeps bytes of its own instead of stopping the compiler.  AggLoadElision's S-alloca
  rule and LastUseArgLoads stop at the slot's scope end.  Frames (aa64 / x64 / arm32): parseType 768 / 816 / 512,
  parseTypeInner 1,216 / 1,216 / 720, parseStructType 1,952 / 2,000 / 1,136 (unchanged), parseStructField 1,648 /
  1,648 / 1,008, parseTypePrefix 2,240 / 2,288 / 1,408, parsePrimaryExpr 4,112 / 4,112 / 2,480 (perf/frame-sizes.sh;
  a struct level 5,584 on aa64, from 5,760; LLVM 3,840).  Full conformance native aa64 / x64 / arm32 bare metal
  3971 / 3971 / 3926, 0 failed; arm32 Linux subset.  Next: 4d (LLVM lifetime intrinsics), 4e (statement
  temporaries); the hidden `.` slots stay unshared.
- Step 4d DONE: binate `7b0933b2f` (2026-11-18).  The LLVM backend brackets a slot with a scope with
  llvm.lifetime.start where its declaration runs (before the zero-fill, or before a call writing its result into
  it ahead of its OP_ALLOC) and llvm.lifetime.end at each end of its scope and before each `ret` (a `return` leaves
  its scopes with no OP_SCOPE_END, and LLVM brackets an inlined alloca to the call only when it has no markers, so
  an unended slot stayed live through the caller).  The retbuf local and in-place parameter slots (GEPs, not
  allocas) take none.  LLVM -O2 frames (aa64 / x64): parseTypeInner 640 / 648, parseStructField 720 / 728,
  parseTypePrefix 1,968 / 1,976, parsePrimaryExpr 2,432 / 1,992 (from 3,056 / 2,600); parseType and
  parseStructType unchanged.  Conformance 1764: a block's local starts as declared whatever its bytes held.  Full
  LLVM -O0 3973/0, -O2 3973/1 (1720, pre-existing, filed).  The (i64 size, ptr) form is untried with an LLVM 22
  clang (which drops the size argument; its auto-upgrade should take the old form).  Next: 4e (statement
  temporaries), then step 5.
