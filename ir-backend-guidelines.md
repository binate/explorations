# IR/Backend Guidelines

This document defines the boundary between the IR layer (`pkg/ir`) and code generation backends (e.g., `pkg/codegen` for LLVM, future `pkg/arm32` for ARM). The goal is to minimize duplicated work across backends and keep architectural decisions in one place.

## Guiding Principles

### Language-semantic vs target-specific

The IR should contain everything that is **language-semantic** — decisions that follow from the Binate language specification and are the same regardless of target. Backends should contain only **target-specific lowering** — translating abstract operations into the concrete instruction set, calling convention, and binary format of the target.

A useful test: if two backends would compute the same thing, it belongs in the IR or a shared layer, not in each backend independently.

### Compiler/interpreter interop constraint

Binate's defining feature is dual-mode execution: compiled and interpreted code run in the same process, share the same heap, and call each other transparently via function pointers. This imposes a hard requirement: **the compiler and interpreter must agree on the memory layout of every data type.**

This means memory layout is not a backend-internal decision — it is a **language-level contract** shared by:
- Every compiler backend (LLVM, ARM32, future targets)
- The interpreter (both bootstrap Go interpreter and self-hosted interpreter)
- The runtime (`pkg/rt`)

Concretely, the following layouts must be defined once in a shared location and used by all:
- **Structs**: field offsets with padding for alignment (parameterized by target)
- **Arrays** (`[N]T`): contiguous elements, element size derived from type
- **Raw slices** (`[]T`): `{ data *T, len int }` — 2 words
- **Managed-slices** (`@[]T`): `{ data *T, len int, backing *uint8, backingLen int }` — 4 words (first 2 words match raw slice layout)
- **Managed pointer header**: `[ refcount, free_fn ]` at negative offset from payload — 2 words
- **Interface values** (future): `{ data_ptr, vtable_ptr }` for raw interfaces; `{ managed_ptr, vtable_ptr }` for managed interfaces
- **Function pointers**: native pointer (same width as raw pointer); thunks for interpreted functions

If a backend computes layout differently from the interpreter, compiled/interpreted interop breaks silently — data corruption, crashes, or worse. This is why layout belongs in a shared layer, not in backends.

## What Belongs in the IR Layer

### 1. SSA Form and Control Flow

The IR already handles this correctly. Basic blocks, phi nodes, branches, jumps — all in IR. Backends consume this structure; they don't create it.

### 2. Memory Management Semantics

Reference counting operations (`OP_REFCOUNT_INC`, `OP_REFCOUNT_DEC`), nil checks, destructor calls — these are language semantics. The IR decides *when* to inc/dec; the backend decides *how* (e.g., call a runtime function, inline the operation).

### 3. Type Layout

**This is currently partially in the backend but must be shared.** `types.SizeOf`, `types.AlignOf`, and `types.FieldOffset` already exist in `pkg/types`. But:

- **Struct padding computation** (inserting padding fields between struct members) is done in `codegen/emit.bn` during LLVM struct type emission. This should be computed once in a shared layer, parameterized by a target description (pointer size, alignment rules).
- **Field index mapping** (`structLLVMIndex` in `codegen/emit_util.bn`) converts a logical field index to an index that accounts for padding fields. This is layout logic, not LLVM-specific.

The `types` package's `SizeOf`/`AlignOf`/`FieldOffset` functions currently assume a specific target (64-bit, 8-byte pointers). These need to be parameterized by target when we add 32-bit ARM support.

**The interop constraint makes this non-negotiable.** Layout functions aren't just "shared across backends for convenience" — they define the ABI that the interpreter also relies on. The interpreter must use the same `SizeOf`/`FieldOffset` functions (or equivalent logic) when accessing struct fields, indexing slices, etc. A single authoritative layout definition in `pkg/types` ensures compiler and interpreter never diverge.

### 4. Slice and Managed-Slice Representation

The abstract layout of slices should be defined in a shared place:

- Raw slice `[]T`: `{ data *ElemType, len int }` — 2 words
- Managed-slice `@[]T`: `{ data *ElemType, len int, backing *uint8, backingLen int }` — 4 words

Backends map these to their concrete representations (e.g., LLVM uses `%BnSlice = type { i8*, i64 }`), but the *structure* (which field is at which offset, how many words) is language-defined.

### 5. Managed Pointer Header Layout

The header layout `[refcount, free_fn]` at negative offset from the payload is a language/runtime contract, not a backend decision. The header size (currently 16 bytes on 64-bit, would be 8 bytes on 32-bit) should be derivable from the target's pointer/int size.

### 6. Runtime Function Manifest

The set of runtime functions that generated code may call (`rt.Alloc`, `rt.RefInc`, `rt.RefDec`, `rt.BoundsCheck`, `rt.Box`, `rt.MakeManagedSlice`, etc.) should be declared in a shared manifest — not hardcoded in each backend's emit code. Each backend reads the manifest and emits declarations in its own format.

### 7. Name Mangling

The mapping from Binate names to symbol names (`pkg.Func` → `bn_pkg__Func`) is a project-wide convention, not backend-specific. Different backends may need different *formats* (e.g., ELF vs Mach-O symbol rules), but the logical mangling scheme should be shared.

### 8. Multi-Return Representation

The decision to pack multiple return values into a struct is language-level. The IR already uses `OP_EXTRACT` to access individual returns. The *concrete* packing (LLVM `insertvalue`/`extractvalue` vs ARM register allocation) is backend-specific, but the *abstract* representation (it's a struct with N fields) should be in IR.

### 9. String Constant Collection

Collecting string literals from the IR and assigning them IDs is not backend-specific. The backend only needs to know "here are N string constants with these bytes; emit them in your format."

## What Belongs in the Backend

### 1. Instruction Selection

Mapping IR operations to target instructions. `OP_ADD` → LLVM `add` vs ARM `ADD` vs x86 `add`. This is inherently target-specific.

### 2. Register Allocation (for native backends)

LLVM handles this for us; a direct ARM backend would need its own allocator.

### 3. Calling Convention

How arguments are passed (registers vs stack), how returns are packed, stack frame layout. Target-specific.

### 4. Type Representation Strings/Encodings

LLVM needs `i64`, `i8*`, `%StructName`; ARM needs register classes; Wasm needs `i32`/`i64`. Pure backend concern.

### 5. Binary Format

ELF headers, Mach-O load commands, LLVM `.ll` text syntax. Backend-specific.

### 6. Debug Info Format

DWARF metadata emission, source maps, etc. Format is target/toolchain-specific, though the *source location data* could come from IR.

### 7. Target-Specific Optimizations

Instruction combining, peephole optimizations, target-specific lowering patterns. Backend-specific.

### 8. Linking

How object files are combined into executables. Backend-specific (though the *list* of objects to link is shared).

## Gray Areas and Guidelines

### Slice Operations: Inline vs Runtime Call

Currently, slice get/set/expr operations are implemented as runtime function calls in C (`bn_slice_get_i64`, etc.). These are simple enough to inline directly in the backend (pointer arithmetic + load/store). The guideline:

- The **IR** emits `OP_SLICE_GET`, `OP_SLICE_SET`, etc. as abstract operations.
- The **backend** decides whether to inline them (pointer arithmetic) or call a runtime function. For the LLVM backend, runtime calls may be acceptable. For a direct ARM backend, inlining is probably better to avoid the C runtime dependency.

### Bounds Checking

The IR emits `OP_BOUNDS_CHECK` as an explicit operation. The backend decides the implementation: call `rt.BoundsCheck`, or inline a compare-and-branch to a panic path.

### Constants and Zero Values

The IR represents constants abstractly (`OP_CONST_INT`, `OP_CONST_NIL`). The backend handles target-specific representations (LLVM's `zeroinitializer`, ARM's immediate encoding constraints).

## Target Description

To support multiple targets, layout-related functions need a target description. At minimum:

```
TargetInfo {
    PointerSize int    // 4 for 32-bit, 8 for 64-bit
    IntSize     int    // typically == PointerSize
    MaxAlign    int    // maximum natural alignment
}
```

This parameterizes `SizeOf`, `AlignOf`, `FieldOffset`, header sizes, slice layouts, etc. The target description is set once at compiler startup and flows through to all shared layout functions.

## Summary Table

| Concern | Layer | Notes |
|---------|-------|-------|
| SSA form, control flow | IR | Already correct |
| Refcount inc/dec/dtor | IR | Already correct |
| Struct layout/padding | Shared (types) | Currently in codegen, needs move |
| Slice/managed-slice layout | Shared | Currently hardcoded in codegen |
| Managed ptr header layout | Shared (rt) | Derives from target pointer size |
| Runtime function manifest | Shared | Currently hardcoded in codegen |
| Name mangling scheme | Shared | Currently in codegen |
| Multi-return struct shape | IR | IR has it; codegen re-derives |
| String constant collection | Shared | Currently in codegen |
| Instruction selection | Backend | Inherently target-specific |
| Register allocation | Backend | Inherently target-specific |
| Calling convention | Backend | Inherently target-specific |
| Type representation format | Backend | LLVM types, ARM regs, etc. |
| Binary/object format | Backend | ELF, Mach-O, .ll text |
| Debug info format | Backend | DWARF, etc. |
| Linking | Backend | Target toolchain |
