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

3. **Width-typed opcodes.**  Add `BC_LOAD_IMM64` and 64-bit arithmetic
   / bitwise / shift / compare / `MOV` / cast variants (`BC_ADD64`
   …).  `BC_LOAD64` / `BC_STORE64` already exist for memory.

4. **Lowering emits the 64-bit variants** for `int64`/`uint64`-typed
   IR instructions (host-word-aware); wide constants → `BC_LOAD_IMM64`
   (carried via a 64-bit immediate field or a constant pool — TBD in
   step 3).

5. **Exec handlers for `BC_*64`**: read the pair into an `int64`,
   compute, write the pair back.

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
