# Plan: 64-bit values in the bytecode VM on a 32-bit host (IR-int64 Layer 2)

## Goal

The bytecode VM (`pkg/vm`) must correctly execute programs that use
`int64` / `uint64` values (and wide integer constants) even when the
VM is itself hosted on a 32-bit machine — **without** penalizing the
common 32-bit case.  This is Layer 2 of the IR-int64 work (Layer 1 —
host-independent IR integer constants — is landed; see
`claude-todo.md`).

## Model (decided 2026-05-26)

- **Register size == host word size.**  Do NOT decouple them: it adds
  interp/compiled-interop complexity and only works when they're
  equal anyway.  So a register slot is `int` (4 bytes on a 32-bit
  host, 8 on a 64-bit host).
- **Pay for 64 bits only when a value is 64-bit.**  The hot path
  (pointers, `int`, offsets, sizes, comparisons, branches) stays a
  lean host-word machine — unchanged, no doubled storage or
  arithmetic.
- **A 64-bit value occupies a register pair** (two adjacent host-word
  slots, lo/hi) on a 32-bit host.  Width-typed `BC_*64` opcodes read
  the pair into an `int64` Binate local, compute (the Binate compiler
  lowers int64 ops to 32-bit sequences for us), and write the pair
  back.
- **Lowering is host-word-aware.**  Lowering and execution are
  co-located (same VM, same host; bytecode is never serialized across
  hosts), so lowering may branch on `sizeof(int)`:
  - host word ≥ 8 (64-bit host): `int64` fits one slot — emit the
    existing single-slot ops.  **Nothing changes on 64-bit hosts.**
  - host word < 8 (32-bit host): allocate a pair for each
    `int64`/`uint64` IR value and emit `BC_*64` ops.

Consequence: the register word stays host-`int`, so all existing
`bit_cast(int, ptr)` / pointer↔register handling (~106 sites) is
**unchanged**.

## Steps (each a green checkpoint; no-ops on a 64-bit host except where noted)

1. **Register-slot-size correctness (prerequisite, fixes a latent
   bug).**  `pushFrame` budgets `f.NumRegs * 8` bytes/reg (`vm.bn`),
   but registers are accessed via `regs *int` (host-word stride).  On
   a 64-bit host these agree; on a 32-bit host the 8-byte budget vs
   4-byte access makes registers alias.  Replace the hardcoded `8`
   (and any other register-stride `8`s in the frame layout / spill /
   alloca offset math) with the host word size (`cast(int,
   sizeof(int))`).  No-op on a 64-bit host; corrects the 32-bit frame.
   Audit: every `* 8` and `+ 8` tied to register indices.

2. **Pair slot allocation in lowering.**  When host word < 8, reserve
   two consecutive slots for each `int64`/`uint64` IR value; adjust
   `NumRegs` and the IR-value-id → slot mapping accordingly.  (When
   host word ≥ 8, one slot as today.)

   **Design: a single post-pass over the code array, with one audited
   exception.**  The VM register number currently *is* the IR value id
   (`bc.Dst = instr.ID`, 1:1).  Pairs break that, so an id→slot mapping
   (`slotOf`) is needed.  An audit of all ~50 register-id emission
   sites found that `Dst`/`Src1`/`Src2` hold a register (or `-1`) at
   *every* site **except one**: `BC_RETURN`'s `Dst`, which
   `lower_instr_helpers.bn` overloads as the return-shape code
   (`-1`/`0`/`len(Args)`).  Nothing else overloads a register field
   (arg counts ride in `Imm`, names/targets in `Aux`).

   So after the code array is built, **one pass** remaps each
   instruction's register fields: `Src1`/`Src2` when ≥ 0, and `Dst`
   when ≥ 0 *unless the op is `BC_RETURN`*.  `vmf.NumRegs` becomes the
   slot count.

   Why this over applying `slotOf` at each of the ~50 emission sites:
   a missed wrap is invisible on a 64-bit host (the mapping is the
   identity there), so it would not surface until an int64-on-arm32
   test exists.  Concentrating the remap in one auditable pass with a
   single documented exception gives far fewer places to get wrong
   than 50 scattered wraps — materially safer given the silent-on-host
   failure mode.  (A *blanket* post-pass with no exception would be
   incorrect — it would remap `BC_RETURN`'s shape `Dst`; the exception
   is what makes it principled.)  Cost: the "registers except
   `BC_RETURN.Dst`" invariant must hold as opcodes are added — guarded
   by a comment + test.
   - (Rejected C: reserve 2 IR ids per 64-bit value in IR-gen — pushes
     a VM-32-bit concern into the target-independent IR that the
     LLVM/native backends share.  Rejected F: a separate int64
     register file — more moving parts, call-arg packing spans both.)

   `slotOf` construction is a pure function of (per-value widths,
   wordSize) so it is unit-testable on a 64-bit host by forcing
   wordSize=4 (the live path keys off `REG_SLOT` and is identity when
   wordSize ≥ 8).  Split: **2a** = SSA values + globalReg + phi copies;
   **2b** = 64-bit call-arg packing/receipt in the VM call ABI.

3. **Width-typed opcodes (implementation-ready spec).**  Each operand
   and the destination of a 64-bit op is a register *pair*: the base
   slot holds the low 32 bits, base+1 the high 32 bits.  `BCInstr.Imm`
   stays host-`int` (no widening — avoids the ripple that the IR
   IntVal change had): a wide constant is split into two 32-bit halves,
   `Imm` = low, `Aux` = high.

   Pure, host-independent (hence unit-testable on a 64-bit host)
   helpers carry the bit-math — the part where a sign/zero-extension
   slip is a silent 32-bit bug:
   - `splitInt64(v int64) (int, int)` → `(lo, hi)` 32-bit halves
     (`lo = cast(int, v & 0xFFFFFFFF)`, `hi = cast(int, (v >> 32) &
     0xFFFFFFFF)`; both are bit patterns, possibly "negative" int32).
   - `joinInt64(lo int, hi int) int64` → `cast(int64, cast(uint32, lo))
     | (cast(int64, cast(uint32, hi)) << 32)` — **zero-extend each
     half** (cast through uint32) so a high-bit-set low half doesn't
     sign-pollute the upper 32 bits.

   Opcodes (emitted only when `REG_SLOT < 8`):
   - `BC_LOAD_IMM64` — `regs[Dst]=Imm`, `regs[Dst+1]=Aux` (pair write;
     no join needed).
   - Arithmetic/bitwise/shift: `BC_ADD64 BC_SUB64 BC_MUL64 BC_DIV64
     BC_UDIV64 BC_REM64 BC_UREM64 BC_AND64 BC_OR64 BC_XOR64 BC_SHL64
     BC_SHR64 BC_LSHR64` — join both src pairs, compute in `int64`
     (uint64 for the unsigned forms), split the result to the Dst pair.
   - Compare: `BC_EQ64 BC_NE64 BC_SLT64 …` — join both pairs, compare,
     write a 0/1 bool to `regs[Dst]` (a bool is one slot, not a pair).
   - `BC_MOV64` — copy both slots (phi / arg packing of a 64-bit value).
   - Casts: int32→int64 widen (sign/zero-extend the single slot into a
     pair), int64→int32 narrow (take the low slot), int64↔float64
     bitcast (pair reinterpret).
   - Memory: a 64-bit value spans 8 bytes, which `BC_LOAD64`/
     `BC_STORE64` already move — but they move into/out of *one* host
     slot.  On a 32-bit host these must target a pair: load 8 bytes →
     `(regs[Dst], regs[Dst+1])`; store the pair → 8 bytes.  Likely
     `BC_LOAD64_PAIR` / `BC_STORE64_PAIR` variants, or widen the
     existing handlers when `REG_SLOT < 8`.

   Handlers live in a standalone `execOp64(regs, instr) bool` (like
   `execArithOp`) so they are directly unit-testable.

4. **Lowering emits the 64-bit variants** for 64-bit-scalar-typed IR
   instructions, host-word-aware (only when `REG_SLOT < 8`; on a 64-bit
   host the existing single-slot ops handle int64 unchanged).  Wide
   constants → `BC_LOAD_IMM64` via `splitInt64`.

5. **Exec handlers for `BC_*64`** (in `execOp64`): join the src pairs,
   compute, split to the dst pair.

6. **Tests.**  Conformance: `int64`/`uint64` arithmetic exercised
   under `builder-comp_arm32_linux` (incl. values > 2^32 and the
   int64-min boundary).  Unit: `BC_*64` handler round-trips; the
   pair-allocation slot math.

## Notes

- Bytecode is host-local (lowered and run in the same process), so
  host-word-aware lowering does not break portability.
- This does not give the VM 64-bit *pointers* on a 32-bit host (it
  emulates a 32-bit target); it gives 64-bit *integer values*, which
  is what `int64`/`uint64` programs need.
