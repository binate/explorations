# Plan: Interpreter Memory Model Parity

## Goal

Make the self-hosted interpreter (pkg/interp) store ALL values in flat ABI-compatible memory, matching the compiled code's layout. This enables `bit_cast`, pointer indexing, `&x` on locals, and eventual dual-mode interop.

## Current State (updated 2026-04-07)

**boot-comp-int: 142/144 conformance tests pass.** The interpreter uses a hybrid model:

**Flat (done)**:
- Managed pointer structs: `make(T)` allocates via `rt.Alloc`, all field access through `RawAddr + FieldOffset`. Lazy struct reads (no eager field materialization).
- Scalars in env: int/bool stored in flat `*uint8` addresses via `useFlatType`.
- Raw slices: `[]T` as `{data, len}` in 16 bytes.
- Arrays of scalars: flat memory, `&arr[i]` produces real addresses.
- Managed pointer refcounting: `envDefine` RefInc, `envSet` RefDec/RefInc, `cleanupEnvExcept` scope cleanup, `interpRefDec` recursive struct cleanup, `interpCleanupSlice` element cleanup.
- Pointer operations: `bit_cast`, pointer indexing, pointer comparison via RawAddr.
- Named type resolution: `resolveUnderlying` handles `type Kind int` in flat paths.

**Legacy (still using Elems/HeapObj)**:
- `make_slice(T, n)` → `MakeManagedSliceVal` with `Elems @[]@Value` and `HeapObj` for refcounting. NOT backed by real `rt.MakeManagedSlice` flat memory.
- When a managed-slice is written to a flat struct field, `writeFlatValue` allocates a real backing and copies elements. Reading back via `readFlatValue` creates Elems from the flat backing. This means managed-slices stored in struct fields ARE flat, but standalone variables are legacy.

**Remaining xfails**: 126 (managed-slice flat storage), 206 (type checker gap).

### Original problems (status)
- ~~No byte addresses to reinterpret (`bit_cast` can't work)~~ — FIXED
- ~~No pointer arithmetic (`ptr[i]` can't work)~~ — FIXED
- ~~Scalars in structs/arrays aren't at real memory offsets~~ — FIXED (struct fields are flat)
- ~~`&x` on a local int doesn't produce a real address~~ — FIXED (flat env entries)
- Layout doesn't match compiled code (can't interop) — PARTIALLY FIXED (structs match, standalone managed-slices don't)

## Design: All Values in Flat Memory

### Core Principle

Every value lives at a **real memory address** with ABI-compatible layout. The interpreter's expression evaluator produces either:
- **lvalue**: an address (for assignment targets, `&x`, field/element access)
- **rvalue**: a loaded value (for computation — ints in registers, structs as addresses)

### Value Representation

```
// An interpreted value is an address + type.
// For scalars used in computation, we cache the int/bool value
// to avoid constant load/store for arithmetic.
type Value struct {
    Addr  *uint8        // address where this value's bytes live
    Typ   @types.Type   // type of the value at Addr
    // Cached scalar (optimization — avoids reading from memory for arithmetic)
    IntVal  int
    BoolVal bool
    Kind    int          // VAL_INT, VAL_BOOL for cached scalars; VAL_ADDR for address-based
}
```

Actually, simpler: split into two concepts:
- **Addr** (`*uint8`): a pointer to bytes in flat memory
- **ScalarVal**: an int/bool in a register (for arithmetic)

Expression evaluation returns one or the other depending on context. `evalExpr` returns an `Addr` for lvalues and a loaded scalar for rvalues.

### Memory Layout by Type

All layouts match compiled code (via `types.SizeOf`, `types.FieldOffset`, etc.):

| Type | Size | Layout |
|------|------|--------|
| `int` | 8 bytes | 8 bytes at addr |
| `bool` | 1 byte | 1 byte at addr |
| `int8`/`uint8` | 1 byte | 1 byte at addr |
| `int32`/`uint32` | 4 bytes | 4 bytes at addr |
| `*T` | 8 bytes | pointer value (address of target) |
| `@T` | 8 bytes | pointer value (managed allocation payload) |
| `[]T` | 16 bytes | `{data *uint8, len int}` |
| `@[]T` | 32 bytes | `{data *uint8, len int, backing *uint8, backingLen int}` |
| `[N]T` | `N * SizeOf(T)` bytes | contiguous elements |
| `struct{...}` | `SizeOf(struct)` bytes | fields at `FieldOffset` with padding |
| `string` | 8 bytes | pointer to char data (same as `i8*` in LLVM) |

### Local Variable Storage

`var x int` → allocate `SizeOf(int)` = 8 bytes. Environment maps `"x"` → `*uint8` (the address).

`var s struct{X int; Y bool}` → allocate `SizeOf(struct)` bytes. `s.X` = addr + `FieldOffset(struct, 0)`. `s.Y` = addr + `FieldOffset(struct, 1)`.

`&x` → just return x's address. No boxing needed.

### Expression Evaluation

**Ident** (`x`): look up address in env, load value from that address.

**Selector** (`s.X`): evaluate `s` to get struct address, compute `addr + FieldOffset(structType, fieldIndex)`.

**Index** (`a[i]`): evaluate `a` to get slice/array address. For slices: read `data_ptr` from `addr + SliceDataOffset()`, compute `data_ptr + i * SizeOf(elemType)`. For arrays: `addr + i * SizeOf(elemType)`.

**Deref** (`*p`): evaluate `p` to get pointer address, read the pointer value from that address. The result is the target address.

**Address-of** (`&x`): evaluate `x` as an lvalue (get its address, don't load).

**bit_cast** (`bit_cast(TargetType, val)`): evaluate `val`, get its address (or store scalar to temp), reinterpret the bytes at that address as TargetType.

**Pointer indexing** (`p[i]`): evaluate `p` to get pointer value, compute `p_value + i * SizeOf(elemType)`.

### Managed Pointer Allocation

`make(T)` allocates via `c_malloc`:
```
[refcount: 8 bytes] [free_fn: 8 bytes] [payload: SizeOf(T) bytes]
                                         ^-- returned address
```
This matches `rt.Alloc`. Header at `addr - ManagedHeaderSize()`.

RefInc: `*(int*)(addr - ManagedHeaderSize()) += 1`
RefDec: `*(int*)(addr - ManagedHeaderSize()) -= 1; if 0, call dtor + free`

### Slice Allocation

`make_slice(T, n)` allocates backing via `c_malloc(n * SizeOf(T))` (with managed header), then constructs the 4-word managed-slice value:
```
{data = backing_payload, len = n, backing_refptr = backing_payload, backing_len = n}
```

### Assignment

`x = val`: write `SizeOf(type)` bytes from val to x's address.

`s.X = val`: compute field address, write there.

`a[i] = val`: compute element address, write there.

### Function Calls

Arguments are passed by value: allocate parameter slots, `memcpy` from caller's memory to callee's parameter addresses. Return values: caller provides a return slot address.

### String Handling

String literals: the interpreter stores them as `*uint8` (pointer to char data). A `[]char` from a string literal: `{data = charPtr, len = strlen}`. This matches the compiled representation.

## Migration Strategy

### Phase 1: Memory infrastructure
- `interpAlloc(size int) *uint8` — malloc wrapper
- `interpFree(ptr *uint8)` — free wrapper
- `readInt(addr *uint8, size int) int` — read N bytes as int
- `writeInt(addr *uint8, size int, val int)` — write int as N bytes
- `readPtr(addr *uint8) *uint8` — read pointer from address
- `writePtr(addr *uint8, val *uint8)` — write pointer to address
- `memcpyInterp(dst *uint8, src *uint8, size int)` — byte copy
- Unit tests for round-tripping values through flat memory

### Phase 2: Environment and local variables
- Environment maps name → `*uint8` (address) instead of name → `@Value`
- `var x int` allocates 8 bytes, stores address in env
- Variable read: `readInt(addr, SizeOf(type))`
- Variable write: `writeInt(addr, SizeOf(type), val)`
- Zero-initialization: `memset(addr, 0, SizeOf(type))`

### Phase 3: Struct fields
- Struct allocation: `interpAlloc(SizeOf(structType))`
- Field read: `addr + FieldOffset(structType, i)`
- Field write: write at computed offset
- Remove `Fields @[]@Value` usage

### Phase 4: Managed pointers
- `make(T)`: allocate with managed header
- Dereference: just use the payload address
- RefInc/RefDec: read/write header at negative offset

### Phase 5: Slices, arrays, strings
- Slice values: 16/32-byte flat blocks
- Array values: contiguous element blocks
- Element access via pointer arithmetic
- String → `[]char` conversion produces flat slice value

### Phase 6: bit_cast and pointer indexing
- `bit_cast(TargetType, val)`: reinterpret bytes
- `ptr[i]`: pointer arithmetic
- Remove xfails 090-093, 104-106

## Open Questions

1. **Scalar optimization**: Should the evaluator carry scalars as ints (avoiding load/store for `a + b`), or always go through memory? Recommendation: keep scalar optimization for arithmetic — store to temp memory only when address is needed (`&x`, `bit_cast`, passing to function).

2. **Interpreter's own allocator**: Use `c_malloc`/`c_free` from rt_stubs, or the Go bootstrap's allocator? For the self-hosted interpreter running through the bootstrap, `c_malloc` is available. For the compiled interpreter, also `c_malloc`.

3. **Garbage collection of interpreter memory**: The interpreter itself is GC'd (refcounted). The flat memory blocks are raw allocations that must be freed explicitly when variables go out of scope or managed pointers are RefDec'd to 0.

4. **Bootstrap interpreter**: NOT changed. Only the self-hosted interpreter (pkg/interp) gets flat memory. The Go bootstrap keeps its interface-based Value model.
