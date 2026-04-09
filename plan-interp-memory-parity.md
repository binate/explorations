# Plan: Interpreter Memory Model Parity

## Goal

Make the self-hosted interpreter (pkg/interp) store ALL values in flat
ABI-compatible memory, matching the compiled code's layout. This enables
`bit_cast`, pointer indexing, `&x` on locals, and dual-mode interop.

## Current State (updated 2026-04-09)

**boot-comp-int: 150/157 conformance tests pass.**

### What's flat (done)

| Type | env storage | How |
|------|-------------|-----|
| `int`, `bool` | `allocFlat(8)` / `allocFlat(1)` | `readScalar`/`writeScalar` |
| `[]T` (raw slice) | `allocFlat(16)` | 2-word `{data, len}` header |
| `@[]T` (managed slice) | `allocFlat(32)` | 4-word `{data, len, backing, backingLen}` |
| `[N]T` (all arrays) | `allocFlat(N*elemSize)` | flat contiguous, `&arr[i]` works |
| `@T` (managed-ptr) | `allocFlat(8)` | 8-byte pointer value |
| `*T` (raw pointer) | `allocFlat(8)` | 8-byte pointer value |
| `struct` (value type) | `allocFlat(SizeOf(struct))` | fields at `FieldOffset` |
| `string` | `allocFlat(8)` | `i8*` null-terminated pointer |
| named types | via `resolveUnderlying` | resolves to underlying flat type |

### What's still legacy (Cell-based)

| Type | Why | Impact |
|------|-----|--------|
| function values | `FuncVal` carries interpreter metadata (AST decl, closure env, type entries, import aliases) with no compiled-code counterpart | `&f` doesn't work, function values in slices/arrays use Elems path |

### Refcounting status

Compiler: no known memory issues (155/157, only xfails: 139 codegen, 206 type checker).
Interpreter: 7 refcounting xfails (108, 131, 132, 133, 135, 138, 139).
These are now fixable with a single code path (no more legacy/flat duplication).

## Completed Steps

### Step 1: Managed-ptr variables flat — DONE
- `useFlatType` returns true for `TYP_MANAGED_PTR`
- Fixed `box` builtin: `rt.Alloc` instead of HeapObject
- Fixed `cleanupEnvExcept`: compare pointer VALUE (not entry address) for return-value skip
- Fixed `envSet`: cascade-safe RefInc-before-RefDec for flat managed-ptrs

### Step 2: All array types flat — DONE
- Removed int/bool restriction in `useFlatType` for `TYP_ARRAY`
- `readFlatValue`/`writeFlatValue` already handled all element types

### Step 3: Struct value-type variables flat — DONE
- `useFlatType` returns true for `TYP_STRUCT`
- `readFlatValue`/`writeFlatValue` already handled structs (lazy struct reads)

### Step 4: Raw pointers, strings, named types — DONE
- `useFlatType` returns true for `TYP_POINTER`, `TYP_STRING`
- `resolveUnderlying` instead of `ResolveAlias` in `useFlatType`
- Fixed `readFlatValue` for `TYP_STRING`: null-terminated scan

## Remaining Work

### Function values in flat memory — design needed

Function values in the interpreter carry rich metadata:
- Function name and AST declaration
- Closure environment (`@Env`)
- Package type entries (`@[]@TypeEntry`)
- Import aliases (`@[]@AliasEntry`)

In compiled code, function values are just `i8*` (function pointer) or
`{i8*, i8*}` (function pointer + closure context). The interpreter's
representation is much richer because it needs to resolve types and
imports when entering a function scope.

**Options**:

**(a) Opaque managed allocation**: allocate `FuncVal` via `rt.Alloc`,
store as `@FuncVal` (8-byte managed pointer) in flat memory. Field
access through registered type layout. Matches `@T` semantics. The
`FuncVal` struct would need to be defined as a proper Binate type with
known field offsets.

**(b) Keep Cell-based (pragmatic)**: function values are the only
exception. Accept that function-value variables don't have real
addresses. Since functions are rarely stored in slices/arrays or
refcounted, the impact is minimal.

**(c) Compiled-compatible representation**: `{funcPtr, closureCtx}` pair.
Would require rethinking how the interpreter resolves calls — the
closure context would need to encode env/types/aliases in a
compiler-compatible format. Needed for true dual-mode interop where
compiled code calls interpreter functions and vice versa.

**Recommendation**: start with (b), move to (a) if needed, target (c)
for dual-mode interop.

### Legacy path cleanup

With all data types flat, the legacy Cell/Elems code paths are mostly
dead for non-function-value types:
- `Cell @HeapObject` in `EnvEntry`: only used for function values
- `Elems @[]@Value` in `assignTo`: only reachable for function-value elements (rare)
- `HeapObj` on managed-slice Values: can be removed (flat backing handles refcounting)
- `interpCleanupSlice`: can be replaced by flat backing RefDec
- `copyValue` HeapObj.Refcount logic: can be removed

This cleanup can be done incrementally — it's dead code elimination,
not behavioral change.

### Interpreter refcounting fixes (unblocked)

With everything flat, the remaining interpreter refcounting fixes are
straightforward — single code path:
- Return leak (108, 131, 132, 133): skip RefInc for returned locals
- Element-copy for struct fields (135): RefInc managed fields in struct elements
- Assignment cascade (138): already fixed for flat managed-ptrs in step 1
- These can be done incrementally after the flat migration.

## Memory Layout Reference

All layouts match compiled code:

| Type | Size | Layout |
|------|------|--------|
| `int` | 8 bytes | 8 bytes at addr |
| `bool` | 1 byte | 1 byte at addr |
| `*T` | 8 bytes | pointer value |
| `@T` | 8 bytes | managed allocation payload pointer |
| `[]T` | 16 bytes | `{data *uint8, len int}` |
| `@[]T` | 32 bytes | `{data, len, backing_refptr, backing_len}` |
| `[N]T` | `N * SizeOf(T)` bytes | contiguous elements |
| `struct` | `SizeOf(struct)` bytes | fields at `FieldOffset` |
| `string` | 8 bytes | `i8*` null-terminated pointer |
