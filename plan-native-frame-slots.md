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

## Progress

- 2026-11-10: measured (above); step 1 started.
