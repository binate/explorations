# Layout Extraction Plan

Extract memory layout computation from the LLVM backend (`pkg/codegen`) into a shared layer usable by all compiler backends and the interpreter.

## Background

### Current State

**`pkg/types` already has layout functions:**
- `SizeOf(t @Type) int` — byte size of a type including trailing padding
- `AlignOf(t @Type) int` — alignment requirement
- `FieldOffset(t @Type, index int) int` — byte offset of struct field

These are correct and well-tested. However:

1. **Hardcoded to 64-bit.** All pointer-sized types (pointers, managed pointers, strings, function pointers) return 8. Slices return 16, managed-slices return 32. There's no way to target 32-bit.

2. **Struct padding is recomputed in codegen.** `codegen/emit.bn` (lines 142–181) walks struct fields, calls `FieldOffset` and `SizeOf`, and computes padding between fields to emit LLVM `<{ field, [N x i8], field, ... }>` definitions. This padding computation is not LLVM-specific — any backend that emits struct layouts needs it.

3. **`structLLVMIndex`** in `codegen/emit_util.bn` maps a logical field index (0, 1, 2, ...) to a physical index in the LLVM struct that accounts for inserted padding entries. This is used by `emitGetFieldPtr` for LLVM GEP instructions. Other backends may need byte offsets instead, but the padding-aware mapping is still shared logic.

4. **Slice/managed-slice layout is implicit.** The sizes (16 and 32 bytes) are baked into `SizeOf`. The field structure ({data, len} for slices, {data, len, backing, backingLen} for managed-slices) is known only informally — there's no struct-like description that backends or the interpreter can query.

### Interpreter Situation

Both interpreters (Go bootstrap and self-hosted) use high-level value representations:
- Struct fields: `*[]Value` / `@[]@Value` — array of boxed values, accessed by name lookup
- No byte-level layout — field access is by index into the value array

This works for a standalone interpreter but cannot support dual-mode interop. When compiled code passes a struct to the interpreter (or vice versa), the interpreter needs to read/write fields at the correct byte offsets in the compiled struct's memory layout. The shared layout layer is the bridge.

## Goals

1. Parameterize layout by target (32-bit vs 64-bit)
2. Move struct padding computation to a shared location
3. Make slice/managed-slice/managed-pointer-header layouts queryable (not just sizes)
4. Keep the existing `SizeOf`/`AlignOf`/`FieldOffset` API stable (no breakage)
5. Prepare for interpreter byte-level access (future interop work)

## Plan

### Step 1: Define TargetInfo and wire it through

**What:** Add a target description to `pkg/types` and use it in layout functions.

**Changes to `pkg/types.bni`:**
```
type TargetInfo struct {
    PointerSize int    // 4 or 8
    IntSize     int    // typically == PointerSize
    MaxAlign    int    // typically == PointerSize
}

func SetTarget(info TargetInfo)
func GetTarget() TargetInfo
```

**Changes to `pkg/types/scope.bn`:**
```
var target TargetInfo  // module-level

func SetTarget(info TargetInfo) {
    target = info
}

func GetTarget() TargetInfo {
    return target
}
```

Default: `{ PointerSize: 8, IntSize: 8, MaxAlign: 8 }` (current behavior, nothing breaks).

**Update `SizeOf`:**
- `TYP_INT` with no explicit width: `target.IntSize` instead of `8`
- `TYP_STRING`: `target.PointerSize` (a string is an interned pointer — though this depends on future string design; for now it matches pointer size)
- `TYP_POINTER`, `TYP_MANAGED_PTR`: `target.PointerSize`
- `TYP_SLICE`: `2 * target.PointerSize` (data ptr + len, where len is IntSize but typically == PointerSize)
- `TYP_MANAGED_SLICE`: `4 * target.PointerSize` (data ptr + len + backing ptr + backing len)
- `TYP_FUNC`: `target.PointerSize`
- Default (nil type): `target.PointerSize`

**Update `AlignOf`:**
- Cap at `target.MaxAlign` instead of hardcoded `8`
- Pointer/slice/managed types: `target.PointerSize` alignment

**`FieldOffset`:** No changes needed — it already delegates to `AlignOf` and `SizeOf`.

**Wire through compiler driver (`cmd/bnc`):**
- Parse `--target` flag (e.g., `arm32-linux`, `x86_64-linux`, default: host)
- Call `types.SetTarget(...)` before type checking

**Tests:**
- Add unit tests for 32-bit target: `SizeOf` with `PointerSize=4`, verify slices are 8 bytes, managed-slices are 16 bytes, structs with pointer fields have correct padding
- Existing 64-bit tests must still pass (default target)

### Step 2: Extract struct padding into a shared function

**What:** Move padding computation from `codegen/emit.bn` into `pkg/types`.

**New functions in `pkg/types.bni`:**
```
// StructField layout describes one field's position in the physical layout.
type FieldLayout struct {
    FieldIndex     int    // logical field index in the Binate struct
    Offset         int    // byte offset from struct start
    Size           int    // field size in bytes
    PaddingBefore  int    // padding bytes inserted before this field
}

// StructLayout returns the physical layout of all fields in a struct,
// including padding. Returns nil for non-struct types.
func StructLayout(t @Type) @[]FieldLayout

// TrailingPadding returns the number of padding bytes after the last field.
func TrailingPadding(t @Type) int
```

**Implementation in `pkg/types/scope.bn`:**
```
func StructLayout(t @Type) @[]FieldLayout {
    t = ResolveAlias(t)
    if t == nil || t.Kind != TYP_STRUCT || len(t.Fields) == 0 {
        return nil
    }
    var result @[]FieldLayout = make_slice(FieldLayout, len(t.Fields))
    var offset int = 0
    for i := 0; i < len(t.Fields); i++ {
        var fieldAlign int = AlignOf(t.Fields[i].Type)
        var aligned int = offset
        if fieldAlign > 1 {
            aligned = (offset + fieldAlign - 1) / fieldAlign * fieldAlign
        }
        var fl FieldLayout
        fl.FieldIndex = i
        fl.Offset = aligned
        fl.Size = SizeOf(t.Fields[i].Type)
        fl.PaddingBefore = aligned - offset
        result[i] = fl
        offset = aligned + fl.Size
    }
    return result
}

func TrailingPadding(t @Type) int {
    t = ResolveAlias(t)
    if t == nil || t.Kind != TYP_STRUCT || len(t.Fields) == 0 { return 0 }
    var lastIdx int = len(t.Fields) - 1
    var contentEnd int = FieldOffset(t, lastIdx) + SizeOf(t.Fields[lastIdx].Type)
    return SizeOf(t) - contentEnd
}
```

**Update `codegen/emit.bn`:** Replace the inline padding loop (lines 142–181) with a call to `types.StructLayout(sd.Typ)`. The LLVM-specific part (emitting `[N x i8]` padding fields) stays in codegen, but the offset/padding computation comes from the shared function.

**Update `codegen/emit_util.bn`:** `structLLVMIndex` can be simplified — it counts how many padding entries precede a field. With `StructLayout`, it becomes:
```
func structLLVMIndex(t @types.Type, idx int) int {
    var layout @[]types.FieldLayout = types.StructLayout(t)
    if layout == nil { return idx }
    var llvmIdx int = 0
    for i := 0; i <= idx; i++ {
        if layout[i].PaddingBefore > 0 { llvmIdx++ }
        if i == idx { return llvmIdx }
        llvmIdx++
    }
    return llvmIdx
}
```

This stays in codegen because the concept of "LLVM physical index" is LLVM-specific (other backends would use byte offsets directly). But the padding data comes from the shared layer.

**Tests:**
- Unit tests for `StructLayout`: verify padding matches existing `FieldOffset` results
- Unit tests for `TrailingPadding`: verify against existing `SizeOf` - content size
- Test with both 32-bit and 64-bit targets

### Step 3: Make composite type layouts queryable

**What:** Define the internal structure of slices, managed-slices, and managed pointer headers as queryable data, not just sizes.

Currently, the layouts are:
- Raw slice `*[]T`: `{ data *T, len int }` — but this is only documented informally
- Managed-slice `@[]T`: `{ data *T, len int, backing *uint8, backingLen int }` — same
- Managed pointer header: `[refcount, free_fn]` at negative offset — only in `pkg/rt`

**New constants/functions in `pkg/types`:**

```
// Slice field indices (for any target)
const SLICE_FIELD_DATA int = 0
const SLICE_FIELD_LEN int = 1

// Managed-slice field indices
const MSLICE_FIELD_DATA int = 0
const MSLICE_FIELD_LEN int = 1
const MSLICE_FIELD_BACKING int = 2
const MSLICE_FIELD_BACKING_LEN int = 3

// Byte offsets (target-dependent)
func SliceDataOffset() int    // 0 always
func SliceLenOffset() int     // target.PointerSize
func MSliceDataOffset() int   // 0 always
func MSliceLenOffset() int    // target.PointerSize
func MSliceBackingOffset() int   // 2 * target.PointerSize
func MSliceBackingLenOffset() int  // 3 * target.PointerSize

// Managed pointer header (at negative offset from payload)
func ManagedHeaderSize() int     // 2 * target.PointerSize (refcount + free_fn)
func ManagedRefcountOffset() int // -(2 * target.PointerSize) from payload
func ManagedFreeFnOffset() int   // -(target.PointerSize) from payload
```

These are trivial functions but they centralize the layout contract. The codegen currently hardcodes `%BnSlice = type { i8*, i64 }` — with these functions, it can verify or derive the representation. The interpreter can use the offsets to read/write fields in compiled structs. The runtime (`pkg/rt`) can use `ManagedHeaderSize()` instead of hardcoding 16.

**Note:** These could also be computed from synthetic struct types (i.e., create a `@Type` representing the slice layout and use `FieldOffset` on it). That's cleaner but more machinery. The direct offset functions are simpler for now and can be refactored later.

### Step 4: Update `pkg/rt` to use shared layout

**What:** Replace hardcoded sizes in the runtime with calls to layout functions.

**Current hardcoding in `pkg/rt/rt.bn`:**
- `HEADER_SIZE` is implicitly 16 (2 * 8) — used in `Alloc`, `Free`, `RefInc`, `RefDec`
- `ManagedSlice` struct has fields that assume 8-byte words

**Changes:**
- Use `types.ManagedHeaderSize()` or compute from `types.GetTarget().PointerSize`
- The `ManagedSlice` struct in `rt.bni` is a Binate struct — its layout is already governed by `types.SizeOf`. No change needed for the struct definition itself, but the runtime code that does pointer arithmetic on headers should use the shared constants.

**Complication:** `pkg/rt` is compiled as part of the target program, not the compiler. The `types` package is a compiler-time package. So `rt` can't import `types` at runtime.

**Resolution:** The header size is `2 * sizeof(int)` which equals `2 * sizeof(pointer)` on the targets we support (ILP32 and LP64). The runtime can compute this from its own knowledge of pointer size (which is implicit — it's running on the target). Alternatively, the compiler can emit a constant that the runtime uses. For now, the runtime can hardcode `2 * sizeof(int)` using Binate's own `int` size, which is target-correct by construction. The shared layout functions are for the compiler and interpreter to agree; the runtime is already running on the right target.

### Step 5: Update codegen to use shared layout

**What:** Refactor `pkg/codegen` to use the new shared functions instead of inline computation.

**Specific changes:**

1. **`emit.bn` struct type emission** (lines 142–181): Replace the inline padding loop with:
   ```
   var layout @[]types.FieldLayout = types.StructLayout(sd.Typ)
   var trailing int = types.TrailingPadding(sd.Typ)
   ```
   Then iterate `layout`, emitting `[fl.PaddingBefore x i8]` before each field and `[trailing x i8]` at the end. The LLVM type string generation stays in codegen.

2. **`emit.bn` slice type definitions** (line 135–136): Instead of hardcoding `%BnSlice = type { i8*, i64 }`, derive from target:
   ```
   // Still LLVM-specific string emission, but sizes from shared layer
   var ptrType *[]char = "i8*"
   var intType *[]char = llvmIntType()  // "i32" or "i64" based on target
   // emit: %BnSlice = type { ptrType, intType }
   ```

3. **`emit_util.bn` `structLLVMIndex`**: Use `types.StructLayout` as shown in Step 2.

4. **`emit_util.bn` `typeSizeBytes` and `elemSizeOf`**: These already delegate to `types.SizeOf`. No change needed.

5. **`emit_types.bn` `typeBits`**: Update to use `target.PointerSize * 8` for pointer types instead of hardcoded 64.

6. **`emit_types.bn` `llvmType`**: Update pointer-sized types to emit `i32` instead of `i64` on 32-bit targets.

### Step 6: Verify with existing tests

**What:** Ensure nothing breaks.

- Run all unit tests in `pkg/types` (existing + new from Steps 1–3)
- Run all unit tests in `pkg/codegen` if any exist
- Run conformance tests in all existing modes (bootstrap, selfhost, compiled, compiled-interp, compiled-compiler)
- Verify generated LLVM IR is identical to before (since default target is 64-bit)

## Order

```
Step 1 (TargetInfo) → Step 2 (StructLayout) → Step 3 (composite layouts)
                                                       ↓
                                              Step 4 (rt cleanup) — can be deferred
                                                       ↓
                                              Step 5 (codegen refactor)
                                                       ↓
                                              Step 6 (verify)
```

Steps 1–3 are the core extraction. Step 4 is a nice cleanup but not blocking. Step 5 is the codegen refactor that proves the shared layer works. Step 6 is verification.

## Future Work (not in this plan)

- **Interpreter byte-level access**: Teach the interpreter to read/write struct fields at computed byte offsets (for compiled/interpreted interop). This uses the layout functions from Steps 1–3 but is a separate, larger effort.
- **ARM32 backend**: Uses the shared layout to emit ARM struct access code. Depends on this plan being complete.
- **Interface value layout**: When interfaces are implemented, their layout (data pointer + vtable pointer, or managed pointer + vtable pointer) should be added to Step 3's queryable layouts.
