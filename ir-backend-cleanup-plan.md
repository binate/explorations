# IR/Backend Cleanup Plan

Work plan for refactoring the IR/codegen boundary to support multiple backends. The first new backend will target 32-bit ARM (tested via QEMU user-mode emulation on Linux/ARM).

See `ir-backend-guidelines.md` for the principles behind these decisions.

## Prerequisites

### P1. Parameterize type layout by target

**What**: `types.SizeOf`, `types.AlignOf`, and `types.FieldOffset` currently assume 64-bit (8-byte pointers, 8-byte ints). Add a `TargetInfo` struct and thread it through these functions.

**Where**: `pkg/types/scope.bn`, `pkg/types.bni`

**Details**:
- Define `TargetInfo { PointerSize int; IntSize int; MaxAlign int }` in `pkg/types.bni`
- Add a module-level `var target TargetInfo` with a `SetTarget` function
- Update `SizeOf`, `AlignOf`, `FieldOffset` to use `target.PointerSize` instead of hardcoded 8
- Default to current 64-bit values so nothing breaks before a target is explicitly set
- The compiler driver (`cmd/bnc`) calls `types.SetTarget(...)` based on a `--target` flag

**Impact**: Foundation for everything else. Must be done first. Note: the interpreter must also use these layout functions (or equivalent logic) to maintain compiler/interpreter interop — both must agree on struct layouts, slice sizes, managed pointer headers, etc. for a given target.

### P2. Add target flag to compiler driver

**What**: Add `--target` flag to `cmd/bnc` (e.g., `--target arm32-linux`, `--target x86_64-linux`, default: host).

**Where**: `cmd/bnc/args.bn`, `cmd/bnc/main.bn`

**Details**:
- Parse target triple into `types.TargetInfo`
- Call `types.SetTarget(...)` before type checking (since type checking uses `SizeOf` for array sizes, etc.)
- Pass target info to backend selection (P7)

## Phase 1: Extract Shared Layout Logic from Codegen

### 1.1. Extract struct padding computation

**What**: Move the struct padding logic from `codegen/emit.bn` (lines ~142–181) into a shared utility.

**Where**: New functions in `pkg/types` (or a new shared package)

**Details**:
- Create `types.StructLayout(t @Type) @[]FieldLayout` that returns a list of `{ FieldIndex int; Offset int; Size int; PaddingBefore int }` entries plus trailing padding
- Or simpler: `types.PaddingBefore(t @Type, fieldIndex int) int` and `types.TrailingPadding(t @Type) int`
- `codegen/emit.bn` calls the shared function instead of computing padding inline
- Future ARM backend uses the same function

See `layout-extraction-plan.md` for the detailed step-by-step plan covering Steps 1.1 and 1.2.

**Depends on**: P1 (layout depends on target)

### 1.2. Extract field index mapping

**What**: `structLLVMIndex` in `codegen/emit_util.bn` maps logical field index → physical index (accounting for padding fields). This is needed by any backend that uses a flat struct representation.

**Where**: `pkg/types`

**Details**:
- Create `types.PhysicalFieldIndex(t @Type, logicalIndex int) int` — returns the index accounting for inserted padding fields
- This is only relevant for backends that represent structs as flat sequences (LLVM's `<{ field, pad, field, pad, ... }>`). Backends that use byte offsets directly (like ARM) won't need this, but the logic should still be shared rather than duplicated.

**Depends on**: 1.1

### 1.3. Extract name mangling

**What**: `mangleFuncName`, `mangleGlobalName`, `mangleStructName` in `codegen/emit.bn` implement the `bn_pkg__Name` convention. This is project-wide, not LLVM-specific.

**Where**: New shared location (could be `pkg/ir` or a small `pkg/mangle` package)

**Details**:
- Move the three mangle functions to a shared package
- Both LLVM and ARM backends import and use them
- The mangling scheme itself doesn't change

**Depends on**: Nothing (independent)

### 1.4. Extract string constant collection

**What**: `collectStrings` in `codegen/emit.bn` walks all functions to find `OP_CONST_STRING` instructions and deduplicates them. Every backend needs this.

**Where**: `pkg/ir` (it operates on IR data structures)

**Details**:
- Move `collectStrings` to `pkg/ir` as `CollectStrings(m @Module) @[]StringConst`
- The `StringConst` type (ID + data) moves to `ir.bni`
- Backends call `ir.CollectStrings(mod)` and emit in their own format

**Depends on**: Nothing (independent)

### 1.5. Extract runtime function manifest

**What**: The runtime function declarations in `codegen/emit.bn` (lines 220–242) are hardcoded LLVM `declare` statements. The *set* of runtime functions is shared; the *declaration format* is backend-specific.

**Where**: `pkg/ir` or a shared constants file

**Details**:
- Define a list of runtime function signatures (name, param types, return types) as data
- Each backend reads the list and emits declarations in its format
- This also documents the runtime contract clearly in one place
- Consider: some of these (slice get/set/expr) may be inlined by native backends, so the manifest should indicate which are "required" vs "optional" (can be inlined)

**Depends on**: Nothing (independent)

## Phase 2: Slice Operation Inlining — DONE

Analysis complete. See `slice-operations-analysis.md` for the full writeup.

**Decision**: keep high-level IR ops (`OP_SLICE_GET`, etc.) and let each backend choose how to lower them. The LLVM backend continues using runtime calls; native backends should inline as pointer arithmetic + load/store.

**Key findings**:
- Slice get/set/len are trivially inlineable (1–3 instructions, no allocator needed)
- Slice expressions and make_slice need an allocator but are otherwise simple
- I/O functions must remain as runtime calls or syscalls
- Bounds checking is already a separate IR op, so inline lowering can skip redundant checks
- **Bug found**: raw slice `s[lo:hi]` in C runtime copies data instead of producing a zero-copy view. Tracked in TODO.
- The `Inlineable` field in `ir.RuntimeFunc` (added in Phase 1) already marks the right functions

## Phase 3: Runtime Abstraction

### 3.1. Define platform abstraction layer for allocator

**What**: The runtime needs `malloc`/`free` (or equivalent). For a libc-free target, this means a Binate allocator on top of a page source.

**Where**: New `pkg/alloc` or extension of `pkg/rt`

**Details**:
- Define an allocator interface: `Alloc(size int) *uint8` and `Free(ptr *uint8)`
- Default implementation: call C malloc/free (current behavior)
- Alternative implementation: free-list allocator on top of `mmap` syscall (for libc-free targets)
- The page source is the only truly platform-dependent part: `mmap` on Linux, direct memory region on bare metal

### 3.2. Implement Binate-native allocator

**What**: Simple free-list allocator written in Binate.

**Where**: `pkg/alloc` (new package)

**Details**:
- Segregated free lists by size class (e.g., 16, 32, 64, 128, 256, 512, 1024, 2048, 4096+ bytes)
- For large allocations, go directly to the page source
- Page source abstracted as an extern function: `PageAlloc(size int) *uint8`
- Each target provides `PageAlloc` — Linux uses `mmap` syscall, bare metal carves from a memory region
- This is independent of the backend work and can be developed/tested separately

### 3.3. Reimplement C runtime functions in Binate

**What**: Functions in `binate_runtime.c` that don't require syscalls should be rewritten in Binate.

**Where**: `pkg/rt` and/or new packages

**Details** (in priority order):
1. **Slice operations** (get/set/len/expr) — trivial, can be inlined by backend (Phase 2)
2. **String-to-chars** — simple copy, can be Binate
3. **Memory operations** (memset, memcpy) — can be Binate loops, or backend intrinsics for performance
4. **I/O** (print_string, print_int, etc.) — need syscalls (write), platform-dependent
5. **File I/O, process control** — need syscalls, platform-dependent
6. **Program entry** (argc/argv, main) — platform-dependent startup code

Items 4–6 are the "bootstrap" package's responsibility and are inherently platform-dependent. Items 1–3 can be pure Binate.

## Phase 4: ARM32 Backend

### 4.1. Backend skeleton

**What**: Create `pkg/arm32` (or `pkg/backend/arm32`) with a `EmitModule(m @ir.Module) []uint8` that produces an ELF binary.

**Where**: New package

**Details**:
- ELF32 header generation (little-endian ARM)
- Minimal sections: .text, .data, .rodata, .bss, .symtab, .strtab
- ARM instruction encoding (ARMv7, Thumb-2 or ARM mode — decide)
- Reuse shared layout, mangling, string collection from Phase 1

### 4.2. Instruction selection

**What**: Map IR operations to ARM32 instructions.

**Details**:
- Arithmetic: ARM data processing instructions
- Memory: LDR/STR with various addressing modes
- Control flow: B, BL, BX, conditional branches
- Comparisons: CMP + conditional execution
- Function calls: BL with ARM calling convention (AAPCS)

### 4.3. Register allocation

**What**: Simple register allocator for ARM's 16 general-purpose registers (r0–r15, with r13=SP, r14=LR, r15=PC).

**Details**:
- Start with linear scan or simple graph coloring
- Spill to stack when needed
- Follow AAPCS: r0–r3 for args/returns, r4–r11 callee-saved

### 4.4. Linux syscall wrappers

**What**: Implement `PageAlloc`, `write`, `exit` as inline ARM syscalls.

**Details**:
- ARM Linux syscall convention: syscall number in r7, args in r0–r5, `svc #0`
- `mmap2` (syscall 192) for page allocation
- `write` (syscall 4) for I/O
- `exit_group` (syscall 248) for process exit

### 4.5. QEMU test integration

**What**: Add `compiled-arm` mode to conformance test runner.

**Where**: `bootstrap/conformance/run.sh`

**Details**:
- Cross-compile: `bnc --target arm32-linux -o test.elf test.bn`
- Run: `qemu-arm ./test.elf`
- Compare output to expected
- Requires: QEMU user-mode (`brew install qemu` on Mac)

## Order of Operations

```
P1 (target info) ──→ P2 (--target flag) ──→ Phase 1 (extract shared) ──→ Phase 4 (ARM backend)
                                              ↓
                                           Phase 2 (slice audit)
                                              ↓
                                           Phase 3 (runtime abstraction) ──→ Phase 4
```

Phase 1 items (1.1–1.5) are mostly independent of each other and can be done in any order. Phase 2 and 3 can proceed in parallel. Phase 4 depends on Phases 1 and 3.

## Non-Goals (for now)

- **Optimization passes**: No IR-level optimization before the ARM backend works
- **Bare-metal target**: Linux/ARM via QEMU first; bare metal later
- **64-bit ARM**: 32-bit first (primary target per language goals)
- **Mach-O/Windows**: ELF only for ARM target
