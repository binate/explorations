# Plan: 4-Word Managed-Slice (@[]T)

## Context

Currently `@[]T` is 3 words: `{ data, len, refptr }` (24 bytes). This needs
to become 4 words: `{ data, len, backing_refptr, backing_len }` (32 bytes).

The 4th word (`backing_len`) stores the total element count of the backing
allocation. This is needed for **destructors**: when the backing's refcount
hits zero, the destructor must iterate all `backing_len` elements to RefDec
managed references. This cannot be derived from the view length because
subslicing changes the view but not the backing.

The design is already documented in `explorations/plan-4word-managed-slice-destructors.md`
and ratified in `explorations/claude-notes.md`. This plan covers only the
4-word migration — destructors are a follow-up.

## Layout

```
@[]T = { data *T, len int, backing_refptr @any, backing_len int }
         word 0    word 1   word 2              word 3
```

- `*[]T` (raw slice) is still 2 words: `{ data, len }`
- `@[]T → *[]T`: read first 2 words (prefix-compatible, unchanged)
- `make_slice(T, n)`: `{ &backing[0], n, backing_ptr, n }`
- Subslice `s[lo:hi]`: `{ s.data + lo*elem, hi-lo, s.backing_refptr, s.backing_len }`
- Field indices: 0=data, 1=len, 2=backing_refptr (unchanged), 3=backing_len (new)

## Changes

### 1. Type system: SizeOf → 32

**File:** `pkg/types/scope.bn:53`

Change `TYP_MANAGED_SLICE` from 24 to 32.

### 2. LLVM type definition

**File:** `pkg/codegen/emit.bn:136`

```llvm
%BnManagedSlice = type { i8*, i64, i8*, i64 }
```

### 3. Runtime struct

**Files:** `pkg/rt.bni:34-39`, `pkg/rt/rt.bn:76-81`

```binate
type ManagedSlice struct {
    Data       *any
    Len        int
    Refptr     *any
    BackingLen int
}
```

### 4. MakeManagedSlice

**File:** `pkg/rt/rt.bn:83-92`

Set `ms.BackingLen = length` (same as `ms.Len` for fresh allocations).

### 5. make_slice codegen

**File:** `pkg/codegen/emit_helpers.bn:274-301` (`emitMakeSliceInstr`)

After `call %BnManagedSlice @bn_rt__MakeManagedSlice(...)`, the result
already has the right shape if the runtime returns a 4-word struct. No
field insertion needed — the runtime sets all 4 fields.

### 6. Subslice codegen (slice_expr)

**File:** `pkg/codegen/emit_slice.bn:230-262`

When building the result `%BnManagedSlice` for a subslice, also extract
and insert field 3 (backing_len) from the source:

```llvm
%v.bl = extractvalue %BnManagedSlice %src, 3
; ... existing fields 0, 1, 2 ...
%v = insertvalue %BnManagedSlice %v.m2, i64 %v.bl, 3
```

### 7. String-to-managed-chars (OP_STRING_TO_CHARS)

**File:** `pkg/codegen/emit_instr.bn:208-237`

When building the `%BnManagedSlice` for a string conversion, set field 3
(backing_len) = the string length (same as field 1):

```llvm
%v.m2 = insertvalue %BnManagedSlice %v.m1, i8* null, 2
%v = insertvalue %BnManagedSlice %v.m2, i64 %v.l, 3
```

### 8. RefDec extraction (field index unchanged)

**File:** `pkg/ir/gen_util.bn:205-215`

`emitManagedSliceRefInc` and `emitManagedSliceRefDec` extract field 2 —
this stays the same (field 2 = backing_refptr).

### 9. Managed-to-raw (fields 0,1 only)

**Files:** `pkg/codegen/emit_util.bn:82-114`, `pkg/codegen/emit_helpers.bn:250-272`

These extract fields 0 and 1 only — no change needed.

### 10. Interpreter: track backing_len

**File:** `pkg/interp/value.bn`

`MakeManagedSliceVal` should store the backing length. Currently managed
slices use `HeapObj` for refcounting but don't track backing_len separately.
Since the interpreter uses Go-level arrays (`Elems @[]@Value`), the backing
length is `len(Elems)` when fresh, but subslicing creates a view.

Simplest approach: add a `BackingLen int` field to Value (or piggyback on
HeapObj which already has the full backing). Review how subslicing works
in the interpreter to ensure backing_len is preserved.

### 11. Bootstrap interpreter

The bootstrap uses Go GC, so backing_len is informational. The `SliceVal`
struct may need a `BackingLen` field for correctness if any code reads it.
Review whether any bootstrap code path actually needs it.

### 12. Conformance tests

- Update `093_rt_managed_slice` to test BackingLen field
- Add a test for subslice preserving backing_len

### 13. Unit tests

- Add test in `pkg/ir/` for 4-word managed-slice IR generation
- Add test in `pkg/codegen/` for LLVM output verification

## Implementation Order

1. Type system (SizeOf → 32)
2. Runtime struct + MakeManagedSlice (pkg/rt)
3. LLVM type definition + make_slice codegen
4. Subslice codegen
5. String-to-managed-chars codegen
6. Conformance tests
7. Interpreter updates
8. Bootstrap interpreter updates (if needed)
9. Unit tests

Run `conformance/run.sh basic` and `scripts/unittest/run.sh boot` after each step.
Run `conformance/run.sh all` and `scripts/unittest/run.sh all` at the end.

## Not in scope

- Destructors (follow-up)
- RefDec with destructor parameter (follow-up)
- Element cleanup loops (follow-up)
- Re-enabling Free (follow-up, blocked on destructors)
