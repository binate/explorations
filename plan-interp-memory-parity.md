# Plan: Interpreter Memory Model Parity

## Goal

Make the self-hosted interpreter (pkg/interp) store values in flat ABI-compatible memory, matching the compiled code's layout. This enables `bit_cast`, pointer indexing, and eventual dual-mode interop.

## Current State

The interpreter uses tagged-union `Value` objects:
- Structs: `Fields @[]@Value` — array of boxed values
- Slices: `Elems @[]@Value` — array of boxed values  
- Arrays: `Elems @[]@Value` — array of boxed values
- Pointers: `HeapObj @HeapObject` → `Val @Value`
- Scalars: `IntVal int`, `BoolVal bool`, `StrVal @[]char`

Problems:
- No byte addresses to reinterpret (`bit_cast` can't work)
- No pointer arithmetic (`ptr[i]` can't work)
- Layout doesn't match compiled code (can't interop)

## Design: Flat Memory Backing

### Core Change

Add a `Data *uint8` field to Value (or HeapObject) that points to a flat memory block with ABI-compatible layout. The interpreter allocates these blocks using `c_malloc` (via pkg/rt stubs or bootstrap.Alloc equivalent) and reads/writes values at computed byte offsets using `types.SizeOf`, `types.FieldOffset`, etc.

### Value Representation (After)

```
type Value struct {
    Kind    int
    Typ     @types.Type
    Data    *uint8       // flat memory for this value (nil for scalars/nil)
    IntVal  int          // still used for scalars (int, bool, untyped)
    // ... other fields for func values, multi-returns, etc.
}
```

**Scalars** (int, bool): stored in IntVal as before. `Data` is nil. `bit_cast(int, scalar)` just reinterprets IntVal.

**Structs**: `Data` points to a malloc'd block of `SizeOf(structType)` bytes. Fields are at `FieldOffset(structType, i)`. Reading field i: read `SizeOf(fieldType)` bytes at `Data + FieldOffset(structType, i)`, interpret according to field type.

**Arrays**: `Data` points to `N * SizeOf(elemType)` bytes. Element i is at `Data + i * SizeOf(elemType)`.

**Raw slices** (`[]T`): `Data` points to 2 words: `{data_ptr, len}`. The `data_ptr` points to the element array. Element access: read `data_ptr`, compute `data_ptr + i * SizeOf(elemType)`.

**Managed slices** (`@[]T`): `Data` points to 4 words: `{data_ptr, len, backing_refptr, backing_len}`. Same as raw slice for element access, plus refcount management through `backing_refptr`.

**Raw pointers** (`*T`): `Data` points to the target. Dereferencing: read/write at `Data` according to `T`'s layout.

**Managed pointers** (`@T`): `Data` points to a managed allocation (with refcount header at negative offset, matching `rt.Alloc` layout). The payload starts at `Data`. Dereferencing: same as raw pointer at `Data`.

**Strings**: `Data` points to raw char data (same as `[]char` data pointer). Length tracked separately or via the slice representation.

### Key Operations

**Read a value from flat memory** (`readValue(addr *uint8, typ @types.Type) @Value`):
- Int/bool: read N bytes, return IntVal
- Pointer/managed-ptr: read 8 bytes (pointer), return as Value with Data = that address
- Struct: return Value with Data = addr (no copy needed for read)
- Slice: read 16 bytes (data, len), return as Value
- Managed-slice: read 32 bytes (data, len, backing, backingLen)
- Array: return Value with Data = addr

**Write a value to flat memory** (`writeValue(addr *uint8, val @Value, typ @types.Type)`):
- Int/bool: write N bytes from IntVal
- Pointer/managed-ptr: write 8 bytes (the pointer address)
- Struct: memcpy SizeOf(structType) bytes from val.Data to addr
- Slice: write 16 bytes
- Managed-slice: write 32 bytes
- Array: memcpy N * SizeOf(elemType) bytes

**Field access** (`val.Data + FieldOffset(structType, i)`): replace name-based lookup with offset computation.

**Slice element** (`readPtr(val.Data + SliceDataOffset()) + i * SizeOf(elemType)`): replace `Elems[i]` with pointer arithmetic.

**bit_cast**: trivially reinterpret bytes at `Data`. `bit_cast(int, ptr)` = read 8 bytes as int. `bit_cast(*T, int)` = treat int as address.

**Pointer indexing** (`ptr[i]`): `Data + i * SizeOf(elemType)`.

### Managed Pointer Allocation

`make(T)` allocates:
```
[refcount (8 bytes)] [free_fn (8 bytes)] [payload (SizeOf(T) bytes)]
                                          ^-- Data points here
```
This matches `rt.Alloc` layout. `Data - ManagedHeaderSize()` = header start.

### Migration Strategy

**Phase 1: Add flat memory infrastructure**
- Add `readInt`, `writeInt`, `readPtr`, `writePtr` helpers (byte-level memory access)
- Add `readValue`, `writeValue` for type-aware read/write
- Add `allocFlat(size int) *uint8` and `freeFlat(ptr *uint8)` wrappers

**Phase 2: Migrate struct values**
- `MakeStructVal` allocates flat memory, writes zero values at field offsets
- Field read: `readValue(data + FieldOffset, fieldType)` instead of `Fields[i]`
- Field write: `writeValue(data + FieldOffset, val, fieldType)` instead of `Fields[i] = val`
- Remove `Fields @[]@Value` usage for structs

**Phase 3: Migrate managed pointers**
- `make(T)` allocates header + payload via `allocManaged(SizeOf(T))`
- Dereference: read from `Data` using the pointee type
- RefInc/RefDec: read/write refcount at `Data - ManagedHeaderSize()`

**Phase 4: Migrate slices and arrays**
- `make_slice(T, n)` allocates backing + 4-word managed-slice value
- Slice element access via pointer arithmetic on data_ptr
- Array values stored as flat byte blocks

**Phase 5: Enable bit_cast and pointer indexing**
- `bit_cast(int, ptr)` = read Data as int
- `bit_cast(*T, int)` = create Value with Data = int-as-pointer
- `ptr[i]` = Data + i * SizeOf(T)
- Remove xfails for 090-093

### Open Questions

1. **Should scalars also use flat memory?** Keeping IntVal for scalars avoids unnecessary indirection. But `bit_cast(float, int)` would need both to be in memory. Since Binate doesn't have floats yet, IntVal is fine for now.

2. **Environment storage**: Variables are currently Name → HeapObject → Value. With flat memory, should variables just point to their flat memory location? E.g., `var x int` → allocate 8 bytes, env maps "x" → address. This would make `&x` trivial (just return the address).

3. **String representation**: Strings are currently `@[]char`. In flat memory, a string literal would be a pointer to char data + length. This matches the `[]char` or `@[]char` representation.

4. **Bootstrap interpreter**: The Go bootstrap interpreter uses a completely different value model (Go interfaces). Parity with the bootstrap is NOT a goal — only parity with the compiled code matters.

5. **Memory allocation**: The interpreter can use `c_malloc`/`c_free` (from pkg/rt stubs) or Go's `make([]uint8, n)` (via bootstrap). For the self-hosted interpreter running through the bootstrap, it would use bootstrap.Alloc or similar. For the compiled interpreter, it would use c_malloc directly.

## Verification

After each phase:
- Run conformance tests in boot-int mode
- After Phase 5: xfails 090-093 should be removed
- Unit tests for readValue/writeValue round-tripping
