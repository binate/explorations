# Runtime Abstraction Plan (Phase 3)

Work plan for decoupling the compiler from the C runtime, enabling libc-free targets (starting with ARM32 Linux via QEMU). This is Phase 3 of the IR/backend cleanup plan.

## Current State

The compiled Binate program depends on two C files at link time:

1. **`runtime/binate_runtime.c`** (~480 lines) — provides:
   - Slice operations (get/set/len/expr/free/make) — 13 functions
   - String conversion (`string_to_chars`) — 1 function
   - I/O (`print_string`, `print_int`, `print_bool`, `print_newline`, `print_chars`, `exit`) — 6 functions
   - Bootstrap package (file I/O, process control, `Itoa`, `Concat`) — 11 functions
   - Program entry point (`main` → `bn_main`) — 1 function
   - **libc usage**: malloc/calloc/realloc/free, memcpy/strlen, printf/fprintf/fwrite/fflush, open/read/write/close, stat, opendir/readdir/closedir, fork/execvp/waitpid, exit

2. **`runtime/rt_stubs.c`** (~46 lines) — thin C wrappers for libc functions used by `pkg/rt`:
   - `c_malloc`, `c_calloc`, `c_free`, `c_memset`, `c_memcpy`, `c_exit`, `c_call_dtor`, `c_bounds_fail`

Additionally, **`pkg/rt`** (written in Binate) implements higher-level memory management on top of the C stubs:
- `Alloc`, `Free`, `RefInc`, `RefDec`, `Refcount`, `Box`, `BoundsCheck`, `MakeManagedSlice`
- Managed pointer header: 16 bytes (refcount + destructor function pointer) at negative offset from payload

The IR runtime manifest (`pkg/ir/runtime.bn`) lists 19 C runtime functions. 10 of these are marked `Inlineable` (slice get/set/len/expr — see `slice-operations-analysis.md`). The legacy `bn_append_*` functions have been removed (dead code — no IR opcode, no callers).

## Goals

1. **Eliminate `binate_runtime.c`** — all functions either reimplemented in Binate or inlined by backends
2. **Reduce `rt_stubs.c` to syscall wrappers** — the only truly platform-dependent code
3. **Enable libc-free linking** — a native backend can produce a standalone binary with no C dependency
4. **Preserve the LLVM backend** — it continues working with the same runtime, just linked differently
5. **Maintain interpreter interop** — the interpreter and compiler must agree on memory layout (headers, slices, etc.)

## Non-Goals

- Changing the managed pointer header layout or refcounting semantics
- Changing the IR opcodes (backends choose how to lower them)
- Writing the ARM32 backend (that's Phase 4)
- Optimizing the allocator (simple and correct first)

## Architecture After Phase 3

```
                        ┌──────────────────┐
                        │   Binate Source   │
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │   IR Generation   │
                        └────────┬─────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │             │
           ┌────────▼──────┐    │    ┌────────▼──────┐
           │  LLVM Backend │    │    │  ARM32 Backend │
           └────────┬──────┘    │    └────────┬──────┘
                    │           │             │
                    │ (calls)   │             │ (inlines)
                    ▼           │             ▼
           ┌───────────────┐   │    ┌───────────────┐
           │ pkg/rt (Binate)│   │    │ pkg/rt (Binate)│
           └───────┬───────┘   │    └───────┬───────┘
                   │           │            │
                   ▼           │            ▼
           ┌───────────────┐   │    ┌───────────────┐
           │ libc stubs    │   │    │ syscall stubs  │
           │ (rt_stubs.c)  │   │    │ (inline asm)   │
           └───────────────┘   │    └───────────────┘
```

Key insight: `pkg/rt` is already written in Binate. The only C dependency is the 8 thin stubs in `rt_stubs.c`. For a libc-free target, we replace those 8 stubs with syscall-based implementations (also in Binate, with inline assembly or backend-specific intrinsics).

## Step-by-Step Plan

### 3.1. Lower slice operations to primitive IR ops — PARTIALLY DONE

**What**: The high-level slice IR ops (`OP_SLICE_GET`, `OP_SLICE_SET`, `OP_SLICE_LEN`, `OP_SLICE_PTR`, `OP_SLICE_EXPR`, `OP_SLICE_ELEM_PTR`) currently produce IR opcodes that the LLVM codegen lowers to C runtime function calls (`bn_slice_get_i64`, etc.). Instead, lower these in the IR gen layer into sequences of primitive IR ops that any backend already handles.

**Why this approach**: Slice/managed-slice layout is a language-level contract (shared by all backends and the interpreter for dual-mode interop). The decomposition into primitives — how to extract the data pointer, compute element addresses, load/store — should be encoded once in the IR gen (shared layer), not independently per backend. This is the same pattern already used for arrays: `arr[i]` emits `OP_GET_ELEM_PTR` + `OP_LOAD`, not a high-level `OP_ARRAY_GET`.

**Original approach (superseded)**: reimplement these as Binate functions in `pkg/rt`. This was abandoned because: (a) some ops like `len()` would be circular (Binate `SliceLen` calls `len` which compiles to `SliceLen`), (b) it introduces naming/ABI coupling between the IR manifest and the Binate package, (c) it doesn't share layout knowledge between backends.

**Operations to lower** (using existing primitive ops: `OP_EXTRACT`, `OP_BIT_CAST`, `OP_GET_ELEM_PTR`, `OP_LOAD`, `OP_STORE`):

| High-level op | Lowered to |
|---------------|------------|
| `OP_SLICE_LEN(s)` | `OP_EXTRACT(s, 1)` — extract len field from slice struct — **DONE** (inlined as `extractvalue` in codegen) |
| `OP_SLICE_PTR(s)` | `OP_EXTRACT(s, 0)` — extract data ptr field |
| `OP_SLICE_GET(s, i)` | extract data ptr, bitcast to `*elemType`, GEP by index, load |
| `OP_SLICE_SET(s, i, v)` | extract data ptr, bitcast to `*elemType`, GEP by index, store |
| `OP_SLICE_ELEM_PTR(s, i)` | extract data ptr, bitcast to `*elemType`, GEP by index (no load — returns ptr) |
| `OP_SLICE_EXPR(s, lo, hi)` | extract data ptr, GEP by lo, construct new slice `{new_ptr, hi-lo}` |

**Where the change happens**: In `EmitSliceGet`, `EmitSliceSet`, `EmitSliceLen`, etc. in `pkg/ir/ir_ops.bn`. These functions currently create a single high-level IR instruction; they should instead emit a sequence of primitive instructions. The codegen's handling of the high-level ops becomes dead code and can be removed.

**For each op, also**:
- Remove the C runtime function from `binate_runtime.c`
- Remove from the runtime manifest (`pkg/ir/runtime.bn`)
- Remove codegen handling of the high-level op
- Update codegen tests

**Note on struct elements**: `OP_SLICE_SET` for struct elements currently uses `memcpy` via the C runtime (`bn_slice_set_struct`). The lowered version needs a memcpy-equivalent — either `OP_STORE` of the struct value (LLVM handles struct stores), or a call to `c_memcpy`. The former is cleaner if LLVM's struct store semantics match.

**Note on bounds checking**: The C runtime functions include redundant bounds checks (the IR gen already emits `OP_BOUNDS_CHECK` separately). The lowered primitives naturally omit these.

**Depends on**: Nothing (independent)

### 3.2. Reimplement non-inlineable slice operations in Binate

**What**: `bn_make_slice`, `bn_string_to_chars`, and `bn_slice_free`.

**Functions**:
- `bn_make_slice(elemSize, length) → slice` — `calloc(length, elemSize)` → use `rt.c_calloc`
- `bn_string_to_chars(str, len) → slice` — `malloc(len)` + `memcpy` → use `rt.c_malloc` + `rt.c_memcpy`
- `bn_slice_free(s)` — `free(s.data)` → use `rt.c_free`

**Why separate from 3.1**: These need the allocator (malloc/calloc/free), so they're slightly more complex.

**Depends on**: Nothing (can be done in parallel with 3.1)

### 3.3. Reimplement I/O functions in Binate

**What**: `bn_print_string`, `bn_print_int`, `bn_print_bool`, `bn_print_newline`, `bn_print_chars`, `bn_exit`.

**Implementation approach**:
- Add `c_write(fd int, buf *uint8, len int) int` to `rt_stubs.c` (wraps POSIX `write`)
- Add `c_fflush()` to `rt_stubs.c` (wraps `fflush(stdout)`) — or we can just use `write` directly and skip buffering
- Reimplement `print_string` and `print_chars` as `c_write(1, data, len)` (write to fd 1 = stdout)
- Reimplement `print_int` in Binate: integer-to-decimal conversion + `c_write`
- Reimplement `print_bool` in Binate: write "true" or "false"
- Reimplement `print_newline` as `c_write(1, "\n", 1)`
- `bn_exit` → `rt.c_exit` (already exists)

**Where**: New package `pkg/io` or extend `pkg/rt`

**Consideration**: `printf`/`fprintf`/`fwrite` all go through stdio buffering. If we switch to raw `write`, output may interleave differently with stderr. For correctness, unbuffered `write` to fd 1 is fine — Binate programs don't use stdio anyway.

**Consideration**: `print_int` needs int-to-decimal conversion, which is the same operation as `Itoa` (3.4). If `Itoa` is done first, `print_int` can reuse the same conversion logic with a stack buffer (no allocation needed — write digits into a `[20]char` array). Alternatively, do `print_int` first with a stack-buffer helper, then `Itoa` wraps the same logic with an allocation.

**Depends on**: Nothing (independent of 3.1 and 3.2), but shares int-to-string logic with `Itoa` in 3.4

### 3.4. Reimplement bootstrap package functions in Binate

**What**: The 11 `bn_bootstrap__*` functions currently in `binate_runtime.c`.

**Functions** (grouped by what they need):
- **File I/O**: `Open`, `Read`, `Write`, `Close` — need POSIX syscalls (open/read/write/close)
- **File system**: `ReadDir`, `Stat` — need POSIX syscalls (getdents/stat)
- **Process**: `Exit`, `Exec` — need POSIX syscalls (exit_group, fork/execve/waitpid)
- **Arguments**: `Args` — need access to argc/argv (from program entry point)
- **String**: `Itoa`, `Concat` — pure computation + allocator

**Implementation approach**:
- `Itoa` and `Concat`: rewrite in pure Binate (use `rt.Alloc` for the managed-slice backing). These are straightforward.
- File I/O (`Open`, `Read`, `Write`, `Close`): add `c_open`, `c_read`, `c_write`, `c_close` to `rt_stubs.c`. Implement the Binate wrappers (path null-termination, flag translation) in `pkg/bootstrap`.
- `ReadDir`, `Stat`: add `c_stat`, `c_opendir`, `c_readdir`, `c_closedir` stubs. Or use raw syscalls (getdents64 + fstat).
- `Exec`: add `c_fork`, `c_execvp`, `c_waitpid` stubs. This is complex — consider if it's needed for ARM32. (Answer: probably not initially — ARM32 target doesn't need to run the compiler driver.)
- `Args`: the `main()` function in `binate_runtime.c` stores argc/argv in globals. Binate's `bn_main` currently takes no args. We need a way to pass argc/argv. Options: (a) global variables set before `bn_main`, (b) change `bn_main` signature.

**Priority**: `Itoa` and `Concat` are easy wins. File I/O is needed for the compiler to self-host. `Exec` and `ReadDir` are lower priority — only the compiler driver uses them, not compiled user programs.

**Depends on**: 3.2 (uses allocator patterns established there)

### 3.5. Reimplement program entry point

**What**: The `main()` function in `binate_runtime.c` that calls `bn_main()`.

**For LLVM backend**: Continue generating a `main` function in LLVM IR that calls `bn_main`. This is already partially the case — the C `main` just saves argc/argv and calls `bn_main`. We can emit this directly in LLVM IR.

**For native backends**: The entry point is backend-specific (ELF `_start` for Linux, etc.). The ARM32 backend will emit its own `_start` that sets up the stack and calls `bn_main`.

**Details**:
- Define a global `__binate_argc`/`__binate_argv` that `main`/`_start` populates
- `bootstrap.Args()` reads from these globals
- The LLVM backend emits a `main` function that stores argc/argv and calls `bn_main`
- Remove the C `main()` from `binate_runtime.c`

**Depends on**: 3.4 (Args needs the argc/argv mechanism)

### 3.6. Platform abstraction for syscalls

**What**: Define the minimal set of platform primitives needed by the runtime, and make them swappable per target.

**Minimal primitive set** (what `rt_stubs.c` should reduce to):
- `c_malloc(size) → *uint8` — or replaced by Binate allocator on mmap
- `c_calloc(count, size) → *uint8` — ditto
- `c_free(ptr)` — ditto
- `c_memset(ptr, val, size)` — can be a Binate loop
- `c_memcpy(dst, src, size)` — can be a Binate loop
- `c_exit(code)` — syscall
- `c_write(fd, buf, len) → int` — syscall
- `c_read(fd, buf, len) → int` — syscall
- `c_open(path, flags) → int` — syscall
- `c_close(fd) → int` — syscall
- `c_call_dtor(dtor, ptr)` — function pointer call (needs backend support)
- `c_bounds_fail(index, length)` — can be Binate (print + exit)

**For libc targets** (LLVM/x86-64): these are thin wrappers around libc, as today.

**For libc-free targets** (ARM32 Linux): these become direct syscalls:
- `write` → syscall 4 (ARM) or 1 (x86-64)
- `read` → syscall 3 (ARM) or 0 (x86-64)
- `open` → syscall 5 (ARM) or 2 (x86-64)
- `close` → syscall 6 (ARM) or 3 (x86-64)
- `exit_group` → syscall 248 (ARM) or 231 (x86-64)
- `mmap2` → syscall 192 (ARM) for the allocator's page source

**`c_call_dtor`** is special — it's a function pointer call. On ARM32, this is just `BLX reg`. The native backend needs to support indirect calls.

**Depends on**: 3.1–3.4 (defines the interface that those steps implement against)

### 3.7. Optional: Binate-native allocator (for libc-free targets)

**What**: Replace `c_malloc`/`c_calloc`/`c_free` with a Binate allocator for targets without libc.

**Where**: `pkg/alloc` (new package)

**Design** (from the cleanup plan):
- Segregated free lists by size class (16, 32, 64, 128, 256, 512, 1024, 2048, 4096+ bytes)
- Large allocations go directly to the page source
- Page source: `PageAlloc(size int) *uint8` — extern function provided per target
- Linux: `PageAlloc` = `mmap2` syscall
- Bare metal: `PageAlloc` carves from a memory region

**This is optional for Phase 3** — the LLVM backend can continue using libc malloc. The Binate allocator is needed when we actually build the ARM32 backend (Phase 4) for libc-free targets. But the interface should be designed now so Phase 4 can slot it in.

**Depends on**: Nothing (can be developed and tested independently)

## Order of Operations

```
3.1 (inlineable slice ops)  ──┐
3.2 (non-inlineable slice)  ──┼──→  3.5 (entry point)
3.3 (I/O functions)         ──┤
3.4 (bootstrap functions)   ──┘
                                      ↓
                               3.6 (platform abstraction)
                                      ↓
                               3.7 (Binate allocator) [optional, can defer to Phase 4]
```

Steps 3.1–3.4 are mostly independent and can be done in any order. 3.1 is the simplest and validates the approach. 3.3 and 3.4 add new C stubs (c_write, c_read, etc.) that become the platform abstraction layer.

## Testing Strategy

- **Conformance tests**: Must continue passing in all modes after each step
- **Unit tests**: Add tests for new Binate implementations of runtime functions
- **Incremental removal**: Remove C functions from `binate_runtime.c` one at a time, verify tests pass
- **Link verification**: After each step, verify the compiled binary links with fewer C symbols

## Risk: Bootstrap Subset

All new Binate runtime code must work under the bootstrap interpreter (no interfaces, generics, closures, etc.). The existing `pkg/rt` already demonstrates this is feasible — it uses `bit_cast`, pointer indexing, and `c_*` stubs, all of which the bootstrap supports.

## Risk: Calling Convention

When replacing a C function with a Binate function, the symbol name and calling convention must match exactly. The LLVM backend emits `call @bn_slice_get_i64(...)` — it doesn't know or care whether that symbol is defined in C or Binate. As long as the Binate function is compiled to the same symbol name with the same parameter/return types, it works.

## What Stays in C After Phase 3

If all steps are completed, `binate_runtime.c` is eliminated entirely. `rt_stubs.c` contains only the minimal platform primitives (malloc/free/memset/memcpy/exit/write/read/open/close/call_dtor/bounds_fail). For libc-free targets, even these are replaced with syscall implementations.

## Relationship to Phase 4

Phase 4 (ARM32 backend) depends on Phase 3 for:
- Binate implementations of slice/I/O/bootstrap functions (so they can be compiled by the ARM backend)
- The platform abstraction interface (so the ARM backend knows what syscall stubs to provide)
- Optionally, the Binate allocator (for libc-free ARM32 binaries)

However, Phase 4 can begin before Phase 3 is fully complete — it just needs the LLVM backend to still work for compiling the runtime packages that the ARM backend will then link.
