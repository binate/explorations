# Slice Operations Analysis

Analysis of C runtime slice operations for Phase 2 of the IR/backend cleanup. The goal is to document which operations can be inlined by native backends and what their semantics are, so that backends don't need to depend on the C runtime for these.

## Current Architecture

The IR emits high-level slice operations (`OP_SLICE_GET`, `OP_SLICE_SET`, etc.). The LLVM backend lowers these to calls to C runtime functions (`bn_slice_get_i64`, etc.). Bounds checking is a separate IR operation (`OP_BOUNDS_CHECK`) emitted before the slice access by the IR generator.

## Operations and Inline Analysis

### Trivially Inlineable (pointer arithmetic + load/store)

These are the simplest operations — just address computation and memory access. A native backend should always inline these rather than calling the C runtime.

**`bn_slice_get_i64(s, i)` → `((int64_t*)s.data)[i]`**
- Inline: `load(s.data + i * 8)`
- The C version includes bounds checking, but in the compiled pipeline bounds checking is already done by `OP_BOUNDS_CHECK` before `OP_SLICE_GET`. The runtime function's built-in bounds check is redundant (but harmless for safety).

**`bn_slice_get_i8(s, i)` → `((uint8_t*)s.data)[i]`**
- Inline: `load(s.data + i)`, zero-extend to int
- Same redundant bounds check note.

**`bn_slice_set_i64(s, i, v)` → `((int64_t*)s.data)[i] = v`**
- Inline: `store(v, s.data + i * 8)`

**`bn_slice_set_i8(s, i, v)` → `((uint8_t*)s.data)[i] = (uint8_t)v`**
- Inline: `store(trunc(v), s.data + i)`

**`bn_slice_get_struct(s, i, elemSize)` → `s.data + i * elemSize`**
- Inline: returns a pointer (no load — the caller loads from it)
- Used for struct/slice/managed-slice elements

**`bn_slice_set_struct(s, i, ptr, elemSize)` → `memcpy(s.data + i * elemSize, ptr, elemSize)`**
- Inline: `memcpy` or a loop for small sizes. A native backend can use a block copy instruction or unrolled stores for known small struct sizes.

**`bn_slice_len(s)` → `s.len`**
- Inline: `load(s + ptrSize)` — just read the second word of the slice

**`bn_slice_free(s)` → `free(s.data)`**
- Inline: call to the allocator's free function (not truly "inline" — still needs the allocator, but doesn't need the C runtime wrapper)

### Inlineable with Caveats (need allocator)

These allocate memory, so they can be inlined but require access to an allocator (malloc/free or the Binate allocator from Phase 3).

**`bn_slice_expr_i8(s, lo, hi)` → copy subslice**
- Current implementation: allocates new buffer, copies `s.data[lo..hi]`
- Inline: `alloc(hi - lo)`, `memcpy(dst, s.data + lo, hi - lo)`, return `{dst, hi - lo}`
- Bounds check is done inline (check `lo >= 0 && hi >= lo && hi <= s.len`)
- **Bug**: the C runtime copies the data, but this is wrong. A raw slice `*[]T` is a borrowed view — `s[lo:hi]` should just produce a new view `{s.data + lo * elemSize, hi - lo}` without copying. The copy wastes memory and breaks borrowing semantics (mutations to the subslice don't affect the original). For `@[]T` subslices, the LLVM codegen already handles this correctly (adjusts data/len, preserves backing refptr). The raw slice case should be a zero-copy pointer adjustment. See TODO.

**`bn_slice_expr_i64(s, lo, hi)` → copy subslice of int64s**
- Same as i8 but with `elemSize = 8`

**`bn_slice_expr_struct(s, lo, hi, elemSize)` → copy subslice of structs**
- Same pattern, parameterized by element size

**`bn_make_slice(elemSize, length)` → allocate zeroed slice**
- Inline: `calloc(length, elemSize)`, return `{ptr, length}`
- Requires allocator

**`bn_string_to_chars(str, len)` → copy string literal to slice**
- Inline: `alloc(len)`, `memcpy(dst, str, len)`, return `{dst, len}`
- Requires allocator

### Not Inlineable (I/O, process control)

These require OS interaction and must remain as runtime calls (either C functions or direct syscalls for libc-free targets).

**`bn_print_string(s, len)`** — writes to stdout via `fwrite`
**`bn_print_int(n)`** — prints integer via `printf`
**`bn_print_bool(b)`** — prints "true"/"false"
**`bn_print_newline()`** — prints newline, flushes stdout
**`bn_print_chars(s)`** — prints slice contents via `fwrite`
**`bn_exit(code)`** — exits process

### Append Operations — REMOVED

The `bn_append_i8/i64/struct` functions have been removed. They were dead code — no IR opcode, no codegen emission, no callers in the self-hosted compiler.

## Bounds Checking

The IR generator emits `OP_BOUNDS_CHECK(index, len)` as a separate instruction before every `OP_SLICE_GET` and `OP_SLICE_SET`. This means the actual get/set operation can assume the index is valid. The C runtime functions also check bounds internally, creating redundant checks.

For a native backend, the inline lowering is:
```
// OP_BOUNDS_CHECK(index, len):
if index < 0 || index >= len { call rt.BoundsCheck(index, len) }  // panics
// OP_SLICE_GET(slice, index):
result = load(slice.data + index * elemSize)   // no bounds check needed
```

The `rt.BoundsCheck` function (in `pkg/rt`) prints an error message and exits. A native backend could call this or emit its own panic path.

## Decision: Lower Slice Ops in IR Gen (updated 2026-04-09)

The original decision was to keep high-level slice ops in the IR and let each backend choose how to lower them. This was revised: since slice layout is a language-level contract (shared by all backends and the interpreter for interop), the lowering is now done once in the IR gen layer. The `Emit*` functions (`EmitSliceGet`, `EmitSliceSet`, `EmitSliceLen`, `EmitSliceExpr`, `EmitSliceElemPtr`) emit sequences of primitive ops (`OP_EXTRACT`, `OP_GET_ELEM_PTR`, `OP_LOAD`, `OP_STORE`, etc.). This is the same pattern used for arrays.

The high-level opcodes (`OP_SLICE_GET`, etc.) have been removed from `ir.bni`. Backends only need to handle universal primitives.

## Summary Table

| Function | Status | Notes |
|----------|--------|-------|
| slice_get_i64/i8/struct | **removed** | lowered to extract+GEP+load in IR gen |
| slice_set_i64/i8/struct | **removed** | lowered to extract+GEP+store in IR gen |
| slice_len | **removed** | lowered to extract(field 1) in IR gen |
| slice_expr_i8/i64/struct | **removed** | lowered to GEP+sub+alloca in IR gen (zero-copy) |
| ~~append_*~~ | **removed** | dead code |
| slice_free | remaining | needs free() — still a C runtime call |
| make_slice | remaining | needs calloc — still a C runtime call |
| string_to_chars | remaining | needs alloc+memcpy — still a C runtime call |
| print_* | remaining | I/O — runtime call or syscall |
| exit | remaining | process control — runtime call or syscall |

Runtime manifest: 22 → 9 functions remaining.
