# Plan: Interpreter Memory Model Parity

## Goal

Make the self-hosted interpreter (pkg/interp) store ALL values in flat
ABI-compatible memory, matching the compiled code's layout. This enables
`bit_cast`, pointer indexing, `&x` on locals, and dual-mode interop.

Eliminate the legacy Elems/Cell/HeapObj paths entirely. Having two
parallel storage paths means every refcounting fix must be implemented
twice — this is unsustainable.

## Current State (updated 2026-04-08)

**boot-comp-int: 150/157 conformance tests pass.**

### What's flat (done)

| Type | env storage | How |
|------|-------------|-----|
| `int`, `bool` | `allocFlat(8)` or `allocFlat(1)` | `readScalar`/`writeScalar` |
| `[]T` (raw slice) | `allocFlat(16)` | 2-word `{data, len}` header |
| `@[]T` (managed slice) | `allocFlat(32)` | 4-word `{data, len, backing, backingLen}` |
| `[N]int`, `[N]bool` | `allocFlat(N*elemSize)` | flat contiguous, `&arr[i]` works |
| `@T` (managed-ptr) struct DATA | `rt.Alloc(SizeOf(T))` | field access via `RawAddr + FieldOffset` |

### What's still legacy (Elems/Cell/HeapObj)

| Type | Why legacy | Impact |
|------|-----------|--------|
| `@T` (managed-ptr) VARIABLE | `useFlatType` returns false | `&x` where x is @Node doesn't give flat addr |
| struct value types | not in `useFlatType` | struct vars in Cell, no `&s` |
| `[N]@T` | only `[N]int`/`[N]bool` flat | `&arr[i]` broken, refcounting needs 2 paths |
| `[N]@[]T` | same | same |
| `[N]Struct` | same | same |

### Refcounting status

Compiler: no known memory issues (155/157, only xfails: 139, 206).
Interpreter: 7 refcounting xfails (108, 131, 132, 133, 135, 138, 139).
Most interpreter gaps are BECAUSE of the dual legacy/flat paths.

## Remaining Migration

### Step 1: Managed-ptr variables flat

Make `@T` variables use flat env storage (8 bytes = pointer value).

**Change**: `useFlatType` returns true for `TYP_MANAGED_PTR`.

**readFlatValue** for `TYP_MANAGED_PTR`: already exists (reads 8-byte
pointer, creates Value with RawAddr). No change needed.

**writeFlatValue** for `TYP_MANAGED_PTR`: already exists (writes 8-byte
pointer from val.RawAddr). No change needed.

**Refcounting**: env operations need RefInc/RefDec for flat managed-ptr
entries (similar to how flat managed-slices are handled). Currently only
Cell-based managed-ptrs get `interpRefDec` in cleanup.

**Impact**: `envGet`, `envSet`, `envDefine`, `cleanupEnvExcept` need
flat managed-ptr handling. This eliminates Cell-based managed-ptr paths
for variables (struct field access already flat).

### Step 2: All array types flat

Make `[N]T` for ALL element types use flat env storage.

**Change**: `useFlatType` for `TYP_ARRAY` returns true unconditionally
(remove the int/bool restriction).

**readFlatValue** for `TYP_ARRAY`: already reads elements via flat
addresses. Works for all types. No change needed.

**writeFlatValue** for `TYP_ARRAY`: already writes elements to flat
addresses. Works for all types. No change needed.

**Refcounting**: array element assignment (`arr[i] = val`) goes through
the flat path for all types. The legacy Elems path for array element
assignment can be removed.

**Prerequisite**: the compiler's `[N]@T` field-write-through-index bug
(test 139) should be fixed first so compiled and interpreted behavior
match. BUT this is a compiler bug, not an interpreter bug — the
interpreter can proceed independently.

### Step 3: Struct value-type variables flat

Make struct VALUE TYPES (not `@Struct` — those are managed-ptrs)
use flat env storage.

**Change**: `useFlatType` returns true for `TYP_STRUCT`.

**readFlatValue** for `TYP_STRUCT`: already exists (lazy struct with
RawAddr). No change needed.

**writeFlatValue** for `TYP_STRUCT`: already exists (writes fields or
memcpy). No change needed.

**Impact**: struct variables like `var e Entry; e.Name = "foo"` get
flat addresses. `&e` works. Field access uses existing flat paths.

**Refcounting**: struct cleanup on scope exit needs to handle flat
struct entries — iterate fields and RefDec managed ones. Similar to
`cleanupFlatMSliceElems` but for struct fields.

### Step 4: Remove legacy paths

Once all types are flat:
- Remove `Cell @HeapObject` from `EnvEntry`
- Remove `Elems @[]@Value` usage in `assignTo` index paths
- Remove `HeapObj` from managed-slice Values
- Remove `interpCleanupSlice` (replaced by flat backing RefDec)
- Remove `copyValue` managed-slice HeapObj.Refcount logic
- Simplify `envGet`/`envSet`/`envDefine` to always use Addr path
- Strip `RawAddr` cleanup hack for struct field assignment

### Step 5: Interpreter refcounting fixes (unblocked)

With everything flat, the remaining interpreter refcounting fixes
become straightforward — single code path:
- Return leak: skip RefInc for returned locals in `cleanupEnvExcept`
  (or equivalent)
- Element-copy: RefInc/RefDec at all element assignment sites
- Assignment cascade: RefInc new before RefDec old in `envSet`

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

## Test plan

After each step:
- `scripts/unittest/run.sh boot` (all pass)
- `conformance/run.sh basic` (no new failures)
- Remove xfails for newly-passing tests
