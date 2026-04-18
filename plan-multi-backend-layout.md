# Plan: Multi-Backend Layout — LLVM + Native

Sketches a concrete package layout and IR-to-backend boundary for supporting
the existing LLVM backend alongside a new "native" backend with multiple arch
targets (initially arm32, arm64, x86-64). Complements `ir-backend-guidelines.md`
with a physical organization.

## Shape

```
                 pkg/ir          (SSA, lowered slice ops, layout-agnostic)
                   │
                   ▼
        ┌──────────┴──────────┐
        │                     │
   pkg/codegen           pkg/native
    (LLVM IR)        (shared native pipeline)
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
      native/x64     native/arm32  native/aarch64
      (instr sel,   (instr sel,   (instr sel,
       regalloc,     regalloc,     regalloc,
       ABI)          ABI)          ABI)
                           │
                           ▼
                     pkg/asm/<arch>     (already exists — final encoding)
                           │
                           ▼
                   pkg/asm/{elf,macho}  (already exists — object emission)
```

**Two peer backends: `pkg/codegen` (LLVM) and `pkg/native` (own).** The LLVM
backend stays as-is. The native backend is a new package that internally
delegates arch-specific steps to sub-packages but shares the lowering pipeline,
object emission framework, and runtime interface.

## Shared substrate (used by both backends)

These already exist and are (or should be) target-parameterized, not
backend-specific:

| Package         | Role                                                   |
|-----------------|--------------------------------------------------------|
| `pkg/ir`        | SSA, control flow, lowered slice/array/struct ops      |
| `pkg/types`     | SizeOf/AlignOf/FieldOffset parameterized by TargetInfo |
| `pkg/mangle`    | Name → symbol (shared between LLVM and native)         |
| `pkg/rt`        | Runtime interface declared abstractly                  |
| `pkg/asm/<arch>`| Arch-specific instruction encoders (already exists)    |
| `pkg/asm/{elf,macho}` | Object-file emitters (already exists)            |
| `pkg/debug`     | Source locations, DWARF builder (shared, arch-param'd) |

Anything that both the LLVM backend and the native backend would compute the
same way belongs here, not in either backend.

## Native backend internals (`pkg/native`)

The native backend's shared pipeline lives in `pkg/native/` and the arch-
specific pieces live in `pkg/native/<arch>/`. Proposed split:

### `pkg/native/` — shared across arches

1. **Driver** (`driver.bn`) — orchestrates the pipeline: load IR module →
   select arch → lower → emit object → link. Same top-level shape as
   `pkg/codegen/emit.bn` but target-dispatched.
2. **Lowering pipeline** (`lower.bn`) — IR operations that lower identically
   across arches:
   - Struct copy/dtor call sequences (already language-level)
   - Bounds check inlining decisions
   - Runtime-function call sites (RefInc/RefDec/Alloc)
   - Slice/managed-slice access decomposition (already done in IR)
3. **Abstract machine IR** (`mir.bn`) — optional intermediate tier between
   `pkg/ir` and arch-specific code. A "generic RISC" with virtual registers,
   load/store/branch/call/arith but no encoding. Post-regalloc it becomes
   arch-specific. Punt on this if it's not paying for itself — can go straight
   `ir.Instr` → `<arch>.Instr` with more boilerplate per arch but less
   infrastructure.
4. **Register allocator** (`regalloc.bn`) — parameterized by arch's register
   classes and calling-convention reservations. Linear-scan or graph-coloring;
   the algorithm is arch-independent, the input description isn't.
5. **Frame layout** (`frame.bn`) — stack frame assembly, spill slot assignment.
   Parameterized by alignment and callee-saved set.
6. **Object emission** (`object.bn`) — builds relocations + sections and hands
   to `pkg/asm/elf` or `pkg/asm/macho`. Format chosen by OS target.
7. **Debug info** (`dwarf.bn`) — shared DWARF builder; per-arch only affects
   register numbering table and CFI encoding.

### `pkg/native/<arch>/` — per-arch

Each `pkg/native/x64/`, `pkg/native/arm32/`, `pkg/native/aarch64/` provides a
small, well-defined interface to the shared pipeline:

```binate
// pkg/native/<arch>.bni sketch
type ArchSpec struct {
    Name            @[]char     // "x64", "arm32", "aarch64"
    PointerSize     int         // 4 or 8
    IntSize         int
    MaxAlign        int
    RegClasses      @[]RegClass // general, float, vector
    CalleeSaved     @[]Reg
    CallerSaved     @[]Reg
    ArgRegs         @[]Reg      // by class
    ReturnReg       Reg
    StackAlign      int
}

// Instruction selection: one IR instr → zero or more MIR instrs.
func SelectInstr(a @ArchSpec, i @ir.Instr, ctx @SelCtx) @[]mir.Instr

// Final encoding: MIR → arch asm instructions.
func EncodeMIR(a @ArchSpec, m @mir.Instr, out @asm.Buf)

// Calling convention: lower a call/return to MIR.
func LowerCall(a @ArchSpec, c @ir.Instr, ctx @LowerCtx) @[]mir.Instr
```

The arch packages delegate encoding to `pkg/asm/<arch>` (already per-arch).
They are responsible for:
- Instruction selection patterns (IR → MIR)
- Calling-convention lowering (how args and returns are placed)
- Any arch-specific legalization (e.g., imm-size limits, addressing modes)
- Providing the `ArchSpec` description consumed by the shared regalloc / frame

They do **not** own: regalloc algorithm, frame layout algorithm, object file
building, dwarf building, IR traversal. Those stay in `pkg/native/`.

## The LLVM backend as a peer

`pkg/codegen` stays as the LLVM backend. Changes to support multi-backend:

1. Consume the same `TargetInfo` as `pkg/native` (already needed per
   ir-backend-guidelines.md §Target Description).
2. Move any layout-computing code (struct padding, slice/managed-slice
   representations, runtime function manifest, string constant collection)
   out of `pkg/codegen` into `pkg/types` / shared layer per the guidelines'
   summary table.
3. After cleanup, `pkg/codegen` only owns:
   - LLVM IR text emission
   - LLVM type syntax (`i64`, `%BnSlice`)
   - Clang invocation

Both backends then rest on the same `pkg/ir` + `pkg/types` + `pkg/rt` +
`pkg/mangle` + `pkg/debug` substrate.

## IR-to-backend boundary (refined)

Below the summary table in `ir-backend-guidelines.md`, make these concrete:

| IR produces                    | LLVM consumes           | Native consumes               |
|--------------------------------|-------------------------|-------------------------------|
| SSA with explicit blocks       | llvm basic blocks       | MIR blocks (post-regalloc)    |
| `OP_LOAD`/`OP_STORE` + offsets | `load`/`store` on `i8*` | per-arch ld/st + displacement |
| `OP_CALL rt.RefInc`            | `call @bn_rt__RefInc`   | arch call to the same symbol  |
| `OP_BOUNDS_CHECK`              | cmp + br to panic label | cmp + branch (arch-inlined)   |
| Multi-return as struct         | `insertvalue`/`extract` | packed in return regs + stack |
| String constants (collected)   | `.ms` globals           | `.rodata` section entries     |
| Source positions               | `!dbg` metadata         | DWARF line program            |

Everything on the left is **already the IR's output today**. The goal is that
the two columns on the right are both valid, parallel lowerings — the IR
doesn't know or care which one runs.

## Runtime

`pkg/rt` (already written in Binate with a C shim) is called by both backends
via the runtime function manifest. The native backend's initial implementation
can keep the C shim: emit native code that calls into the same `bn_rt__*`
symbols the LLVM path uses, linked together at object level. Later, parts of
the shim can be rewritten in Binate or assembly per-arch if hot.

## Milestones

Rough ordering; each step keeps existing tests green.

1. **Layout/manifest extraction**: move struct padding, slice/managed-slice
   descriptors, runtime-function manifest, string constant collection from
   `pkg/codegen` to shared layers (`pkg/types`, possibly a new `pkg/abi`).
   The LLVM backend uses the new shared functions; no behavior change. Tested
   by existing conformance suite.
2. **`TargetInfo` parameterization**: thread pointer/int size through
   `SizeOf`/`AlignOf`/`FieldOffset`. Default target stays 64-bit; no visible
   change yet.
3. **`pkg/native` scaffold**: add the empty package with the `ArchSpec`
   interface and driver skeleton. One arch (probably x86-64 since that's the
   host) can echo LLVM output via the existing `pkg/asm/x64` assembler for a
   trivial program (`func main() { exit(0) }`). Gated behind a `-backend=native`
   flag on `bnc`; LLVM stays default.
4. **Regalloc + frame layout**: linear-scan in `pkg/native/regalloc.bn`,
   basic frame builder. Still just x86-64.
5. **Conformance**: expand the native backend's test surface by copying the
   LLVM conformance runner mode (`boot-comp-native-x64`). Fix bugs until the
   suite passes.
6. **Second arch**: add `pkg/native/arm32/` (or aarch64 — pick whichever
   assembler is most mature). The shared pipeline now gets exercised with a
   different `ArchSpec`; any accidental x86-64 assumptions in shared code
   surface here.
7. **Third arch**.

## Open questions

- **MIR tier**: worth building as a real data structure, or fold into arch-
  specific instruction types from the start? Argument for MIR: shared regalloc
  can operate on it uniformly. Argument against: three arches with ~10 common
  instruction kinds each may not justify the infrastructure. Decide after
  step 4.
- **Linker**: `ld` / clang (as driver) or our own? Short term, invoke the
  platform linker like `pkg/codegen` does. Long term, own it for the
  self-hosted toolchain (matches the "don't depend on clang" goal).
- **ABI compatibility with C**: for `bootstrap.Exec`, `rt.c_malloc`, etc.,
  the native backend must respect platform C ABI. Each `ArchSpec`'s
  calling convention encodes this.

## Out of scope

- Register allocator algorithm choice (linear scan vs graph coloring) —
  decided when implementing step 4.
- Windows/COFF support — plan assumes ELF (Linux) and Mach-O (macOS) to start.
- WebAssembly — would be a third peer backend, not a `pkg/native` arch.
