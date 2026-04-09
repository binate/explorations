# Plan: Interpreter Memory Model Parity

## Goal

Make the self-hosted interpreter (pkg/interp) store ALL values in flat
ABI-compatible memory, matching the compiled code's layout. Flat storage
is not a goal in itself — it's required for **dual-mode interop**:
compiled code and interpreted code share the same heap, call each other
via function pointers, and pass values by address. This requires
identical memory layout.

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

Function values must ultimately use the **same representation** in both
compiled and interpreted code, because function values can be passed
between the two modes — compiled code must be able to call interpreted
functions (via a trampoline) and vice versa.

**Target representation (required for interop)**: `{funcPtr, closureCtx}`
pair, matching the compiled representation. The closure context for an
interpreted function would point to a trampoline that dispatches into
the interpreter. This is a non-trivial design — the trampoline needs
access to the AST declaration, closure env, types, and aliases.

**Current pragmatic approach**: keep function values Cell-based. This
works because the bootstrap subset doesn't have closures or first-class
function values (only direct calls by name). The Cell representation
is a temporary compromise until the full interop design is implemented.

**When this blocks**: if/when Binate gains closures, function values
stored in slices/maps, or callbacks passed between compiled and
interpreted code. At that point, the `{funcPtr, closureCtx}` design
becomes mandatory.

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
