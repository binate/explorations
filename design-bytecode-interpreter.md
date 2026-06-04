# Design: Bytecode Interpreter (`pkg/vm`)

> **Status (shipped):** This document is the historical design rationale
> from when `pkg/vm` was being added alongside the existing tree-walking
> interpreter (`pkg/interp`). `pkg/interp` and its frontend `cmd/bni`
> have since been removed; `pkg/vm` (via `cmd/bni2`) is now the sole
> interpreter. References to `pkg/interp` below describe the retired code.
> Kept for the design rationale and the IR-op → bytecode-op mapping.

## Motivation

The original self-hosted interpreter (`pkg/interp`, now removed) was a
tree-walker that reimplemented refcounting ad-hoc. This was a persistent
source of use-after-free bugs — the interpreter's refcounting logic
diverged from the compiler's IR-level refcounting, leading to subtle
memory corruption that was difficult to diagnose.

A bytecode interpreter that operates on IR-derived bytecode would inherit
the compiler's well-tested refcounting for free. The IR gen already handles:
- Struct copy constructors (`structRefInc` / `__copy_X`)
- Struct destructors (`structRefDec` / `__dtor_X`)
- Managed pointer/slice RefInc/RefDec at all transfer points
- Scope cleanup (`emitDecForManagedLocals`)
- Temp cleanup (`emitTempCleanup`)
- Return value ownership transfer
- Assignment ordering (Axiom 5: copy-then-destroy)

All of this is encoded in the IR. A bytecode interpreter just executes it.

## Architecture

```
Source → Parse → Type Check → IR Gen → Bytecode Lowering → VM Execution
                                ↑                              ↑
                          (existing, shared)              (new: pkg/vm)
```

The first four stages are shared with the compiler. The bytecode
interpreter replaces only the last stage (LLVM codegen) with:
1. A lowering pass that converts SSA IR to register-machine bytecode
2. A VM that executes the bytecode

## Bytecode Format

### Lowering from SSA

SSA IR uses phi nodes for value merging at control flow joins. The
bytecode format eliminates phi nodes by inserting register copies at
the end of predecessor blocks:

```
// SSA IR:
bb3: %v5 = phi [bb1: %v3, bb2: %v4]

// Bytecode (phi resolved):
bb1: ...; MOV r5, r3; JUMP bb3
bb2: ...; MOV r5, r4; JUMP bb3
bb3: // r5 is ready
```

Each SSA value ID becomes a register index in a per-function register
file.

### Instruction Encoding

Each bytecode instruction is a struct:

```binate
type Instr struct {
    Op   int       // opcode (BC_ADD, BC_LOAD, etc.)
    Dst  int       // destination register (-1 for void)
    Src1 int       // first source register or immediate index
    Src2 int       // second source register or immediate index
    Imm  int       // immediate value (constants, offsets, sizes)
    Aux  int       // auxiliary (function ID, block target, etc.)
}
```

This is a fixed-size struct (6 words = 48 bytes). Larger than a typical
bytecode encoding but simple and fast to decode. Optimization (compact
encoding) can come later.

### Opcode Map

| IR Op | Bytecode Op | Notes |
|-------|-------------|-------|
| `OP_CONST_INT` | `BC_LOAD_IMM` | `Dst = Imm` |
| `OP_CONST_BOOL` | `BC_LOAD_IMM` | `Dst = 0 or 1` |
| `OP_CONST_NIL` | `BC_LOAD_IMM` | `Dst = 0` |
| `OP_CONST_STRING` | `BC_LOAD_STR` | `Dst = ptr to string constant` |
| `OP_ADD` | `BC_ADD` | `Dst = Src1 + Src2` |
| `OP_SUB` | `BC_SUB` | `Dst = Src1 - Src2` |
| `OP_MUL` | `BC_MUL` | `Dst = Src1 * Src2` |
| `OP_DIV` | `BC_DIV` | `Dst = Src1 / Src2` |
| `OP_REM` | `BC_REM` | `Dst = Src1 % Src2` |
| `OP_AND/OR/XOR/SHL/SHR` | `BC_AND/...` | Bitwise ops |
| `OP_EQ/NE/LT/LE/GT/GE` | `BC_EQ/...` | Compare → bool |
| `OP_NEG` | `BC_NEG` | `Dst = -Src1` |
| `OP_NOT` | `BC_NOT` | `Dst = !Src1` |
| `OP_BITNOT` | `BC_BITNOT` | `Dst = ~Src1` |
| `OP_ALLOC` | `BC_STACK_ALLOC` | `Dst = SP + Imm; SP += size` |
| `OP_LOAD` | `BC_LOAD` | `Dst = *(int64*)(Src1)` |
| `OP_STORE` | `BC_STORE` | `*(int64*)(Src1) = Src2` |
| `OP_GET_FIELD_PTR` | `BC_FIELD_PTR` | `Dst = Src1 + Imm` (field offset) |
| `OP_GET_ELEM_PTR` | `BC_ELEM_PTR` | `Dst = Src1 + Src2 * Imm` (elem size) |
| `OP_EXTRACT` | `BC_EXTRACT` | `Dst = *(int64*)(Src1 + Imm*8)` |
| `OP_CALL` | `BC_CALL` | Call function by ID |
| `OP_CALL_BUILTIN` | `BC_CALL_BUILTIN` | Call builtin (println, etc.) |
| `OP_JUMP` | `BC_JUMP` | `PC = Aux` |
| `OP_BRANCH` | `BC_BRANCH` | `if Src1: PC = Src2; else: PC = Aux` |
| `OP_RETURN` | `BC_RETURN` | Return value(s) |
| `OP_REFCOUNT_INC` | `BC_REFINC` | `rt.RefInc(Src1)` |
| `OP_REFCOUNT_DEC` | `BC_REFDEC` | `rt.RefDec(Src1, Src2)` |
| `OP_BOX` | `BC_BOX` | `rt.Box(Src1, Imm)` |
| `OP_MAKE` | `BC_MAKE` | `rt.Alloc(Imm)` |
| `OP_MAKE_SLICE` | `BC_MAKE_SLICE` | `rt.MakeManagedSlice(Imm, Src1)` |
| `OP_DEREF` | `BC_DEREF` | `Dst = *(int64*)(Src1)` (same as LOAD) |
| `OP_NIL_CHECK` | `BC_NIL_CHECK` | Panic if Src1 == 0 |
| `OP_BIT_CAST` | `BC_MOV` | `Dst = Src1` (reinterpret) |
| `OP_CAST` | `BC_CAST` | Width conversion (trunc/extend) |
| `OP_BOUNDS_CHECK` | `BC_BOUNDS` | Panic if Src1 < 0 or Src1 >= Src2 |
| `OP_FUNC_ADDR` | `BC_FUNC_ADDR` | `Dst = function address` |
| `OP_STRUCT_LIT` | `BC_STRUCT_LIT` | Construct struct from fields |
| `OP_PHI` | (eliminated) | Resolved to MOV copies |
| `OP_MANAGED_TO_RAW` | `BC_MSLICE_TO_RAW` | Extract first 2 words |
| `OP_STRING_TO_CHARS` | `BC_LOAD_STR_SLICE` | Load from string constant |
| `OP_STRING_TO_ARRAY` | `BC_LOAD_STR_ARRAY` | Copy string to array |

### Phi Resolution

During lowering, for each `OP_PHI` instruction:
1. For each `(predecessor_block, value)` pair, insert a `BC_MOV dst, value`
   at the end of the predecessor block (before its terminator).
2. Remove the phi instruction.

This is the standard "parallel copy" approach. For most Binate code, phis
have 2 predecessors (if/else merge, loop header), so this is simple.

## Execution Model

### Stack Management

- VM allocates a large stack at startup (e.g., 8MB via `c_malloc`)
- Each call frame claims space: `frame.FrameBase = vm.Stack + vm.SP`
- `BC_STACK_ALLOC` returns `frame.FrameBase + offset`
- On return, `vm.SP = frame.CallerSP` (releases frame)
- Stack overflow: check `vm.SP + frameSize > vm.StackSize` before push

### Struct/Managed-Slice Values

Structs larger than 8 bytes live on the stack (from `BC_STACK_ALLOC`).
Registers hold POINTERS to stack memory, not the struct data itself.
This matches the compiler's `OP_ALLOC` semantics.

Managed-slices are 4 words (32 bytes) on the stack. The register holds
the pointer to the 4-word header.

### Calling Convention

1. Caller evaluates args into registers
2. `BC_CALL` pushes a new frame
3. Callee's params are initialized from caller's arg registers
4. Callee executes, returns value(s) in register(s)
5. Caller receives return value in `Dst` register

Multi-return: the IR packs multiple returns into an anonymous struct.
The register holds a pointer to the struct on the stack.

### Builtins

`BC_CALL_BUILTIN` handles `println`, `print`, `len`, `make`, `make_slice`,
`box`, `cast`, `bit_cast`, `unsafe_index`, `panic`. These are dispatched
by name (or pre-assigned ID during lowering).

### C Externs and Bootstrap Functions

Functions from `pkg/bootstrap` (Open, Read, Write, etc.) and `pkg/rt`
(Alloc, Free, RefInc, etc.) are compiled into the binary. The VM calls
them directly via function pointers or a dispatch table.

This is the key advantage: `rt.RefInc(ptr)` is the SAME compiled function
whether called from compiled code or from the VM. No reimplementation.

## Risks and Open Questions

- **IR gen modifications**: if the IR gen needs changes for the VM, those
  changes affect the compiler too. Should be avoided — the IR should be
  a stable interface.
- **Destructors**: `OP_FUNC_ADDR` and `c_call_dtor` require function
  pointers. The VM needs a way to call destructors — either by interpreting
  them (they're IR functions) or by compiling them to native code. Since
  dtors are generated IR functions, the VM can interpret them like any
  other function.
- **C externs**: functions like `bootstrap.Open` are compiled C. The VM
  calls them via a dispatch table (function name → function pointer).
  This requires the VM binary to link against the C runtime.
- **String constants**: stored in the module's `Strings` field. The VM
  needs to materialize them as `@[]char` managed-slices at module load.
- **Performance**: for self-hosting (compiling the compiler), the VM needs
  to be fast enough to complete in reasonable time. The tree-walker handles
  this, so a bytecode VM should be significantly faster.
