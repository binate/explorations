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

### 3.1. Lower slice operations to primitive IR ops — DONE

All high-level slice IR ops have been lowered to primitive ops in the IR gen layer (`EmitSliceGet`, `EmitSliceSet`, `EmitSliceLen`, `EmitSliceExpr`, `EmitSliceElemPtr` in `pkg/ir/ir_ops.bn`). The `Emit*` functions still exist as the public API, but they now emit sequences of `OP_EXTRACT`, `OP_GET_ELEM_PTR`, `OP_LOAD`, `OP_STORE`, etc. instead of dedicated slice opcodes.

**What was done**:
- `EmitSliceLen(s)` → `OP_EXTRACT(s, 1)` (extract len field)
- `EmitSliceGet(s, i)` → extract data ptr + GEP + load
- `EmitSliceSet(s, i, v)` → extract data ptr + GEP + store
- `EmitSliceElemPtr(s, i)` → extract data ptr + GEP (returns pointer)
- `EmitSliceExpr(s, lo, hi)` → extract data ptr + GEP by lo + sub for new len + alloca/store/load to construct result. For `@[]T`, preserves refptr and backingLen from original.
- Deprecated opcode constants (`OP_SLICE_LEN/PTR/GET/SET/EXPR/ELEM_PTR`) removed from `ir.bni`
- 9 C runtime functions removed (`bn_slice_len`, `bn_slice_get_i64/i8/struct`, `bn_slice_set_i64/i8/struct`, `bn_slice_expr_i8/i64/struct`)
- Runtime manifest reduced from 22 to 9 functions
- Codegen `emit_slice.bn` deleted entirely

**Why this approach**: Slice layout is a language-level contract (shared by all backends and the interpreter). The decomposition into primitives is encoded once in the IR gen, not per backend. Same pattern as arrays.

**Also fixed**: Raw slice subslice copy bug — `s[lo:hi]` on `*[]T` now produces a zero-copy view `{data+lo*elemSize, hi-lo}` instead of copying data (the C runtime was wrong).

**Depends on**: Nothing (independent)

### 3.2. Lower remaining slice/string operations — DONE

All three sub-items landed:
- `OP_MAKE_SLICE`: codegen lowers to `bn_rt__MakeManagedSlice` (the Binate function in `pkg/rt`); see `emit_helpers.bn:emitMakeSliceInstr`. `bn_make_slice` was removed from the C runtime.
- `OP_SLICE_FREE`: opcode no longer exists (free is implicit through `RefDec` + dtor); `bn_slice_free` removed from the C runtime.
- `OP_STRING_TO_CHARS`/`OP_STRING_TO_ARRAY`: lifted to IR-level via the composite-literal Phase 3.x work — `OP_RODATA_MSLICE` / `OP_RODATA_SLICE` / `OP_RODATA_ARRAY` / `OP_RODATA_MSLICE_COPY` are now the canonical IR ops; backends lower them to backend-specific representations. `OP_STRING_TO_CHARS` / `OP_STRING_TO_ARRAY` and `EmitStringToArray` were deleted in `a868b4c` (Phase 3.3); `TYP_STRING` was eliminated in `b7243e7` (Phase 3.4). See `claude-todo.md` "Phase 3: unify strings as composite-literal sugar — DONE" for the full commit chain.

**Depends on**: 3.1 (done)

### 3.3. Reimplement I/O functions in Binate — DONE

All `bn_print_*` and `bn_exit` removed from C. The decoupling didn't
land in `pkg/io` — instead `bootstrap.Write` (already a thin wrapper
over POSIX `write`) became the single sink, and IR-gen now lowers
`print(x)` to `bootstrap.formatX(x) + bootstrap.Write(1, …)`. This
keeps the C surface smaller (one shared `write` stub instead of one
per print variant). See `plan-print-builtin-runtime-decoupling.md` for
the multi-step rollout (Steps 1, 2a, 2b, 3, 3.1, 3.2).

Specific landings:
- Step 2b (`42260b2` chain): `print(int)` → `bootstrap.formatInt` +
  `bootstrap.Write`; `bn_print_int` removed.
- Step 3 (`af19ca7`): `print(string)` / `print(bool)` / `println` /
  `print(@[]char)` / `print(*[]char)` all routed through
  `bootstrap.Write` (with `bootstrap.formatBool` for bools);
  `bn_print_string` / `bn_print_bool` / `bn_print_chars` /
  `bn_print_newline` removed.
- Step 3.1 (`6bd55dd`): `print(float)` → `bootstrap.formatFloat` +
  `bootstrap.Write`; `bn_print_float` and `c_print_float` removed.
  `formatFloat` is fixed-point (`integer.6digits`) with a
  ridiculous-but-honest `mantissa*2^exponent` fallback for extreme
  values — no `%g` dependency.
- Step 3.2 (`a31cd8a`): `bn_exit` removed; `OP_PANIC` lowers to
  `rt.Exit` (a Binate wrapper over `rt.c_exit`); runtime manifest now
  empty.
- Followup (`0b7dd90`): with the manifest empty and no IR-gen path
  emitting `OP_CALL_BUILTIN`, the entire opcode + plumbing was
  removed (see TODO entry).

**Followup**: `print(int)` does an allocation per call (`formatInt`
returns `@[]char`). A stack-buffer variant would avoid it, but no
hot path has been identified that cares — left as future cleanup.

**Depends on**: Nothing (was independent of 3.1 and 3.2).

### 3.4. Reimplement bootstrap package functions in Binate — PARTIAL

**What**: The 11 `bn_bootstrap__*` functions originally in
`binate_runtime.c`. Pure-computation pieces are done; POSIX-touching
pieces still in C.

**Status of each**:
- **String** (`Itoa`, `Concat`): DONE in `e8e4172` — pure Binate in
  `pkg/bootstrap/bootstrap.bn` using `rt.Alloc`. Not exposed as
  `IsCExtern`; the per-decl `IsCExtern` fix (driven off body
  presence) is what made this safe to mix into a partly-C package.
- **File I/O** (`Open`, `Read`, `Write`, `Close`): still in C. Need
  `c_open` / `c_read` / `c_write` / `c_close` stubs in `rt_stubs.c`
  + Binate wrappers handling path null-termination and flag
  translation. (Note: `c_write` would also displace `bootstrap.Write`'s
  current direct use of POSIX `write` from C, since `bootstrap.Write`
  is now the single output sink for all print/println.)
- **File system** (`ReadDir`, `Stat`): still in C.
- **Process** (`Exit`, `Exec`): still in C. (`bootstrap.Exit` is
  distinct from `rt.Exit` — the bootstrap one is the user-facing
  exit, `rt.Exit` is the panic-path exit.)
- **Args**: still in C — needs the entry-point work in 3.5 to give
  Binate access to argc/argv before it can move.

**Priority order for the remaining work**:
1. File I/O — needed for compiler self-host on libc-free targets.
2. `Stat` — small; pairs with file I/O.
3. `ReadDir`, `Exec` — only compiler driver uses these; not needed
   for compiled user programs on the target.
4. `Args` — blocked on 3.5 (entry-point rework).
5. `Exit` — trivial wrapper over `c_exit`; do alongside file I/O.

**Depends on**: 3.2 (uses allocator patterns established there).
`Args` additionally depends on 3.5.

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
