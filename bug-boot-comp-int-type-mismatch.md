# Bug: boot-comp-int crash — TYP_SLICE vs TYP_MANAGED_SLICE mismatch

## Summary

The compiled interpreter (`bni` built by boot-comp) segfaults when running
`--test` on packages with many declarations (e.g., `pkg/ir`, `pkg/codegen`,
`pkg/lint`). The crash is a heap buffer overflow: a 16-byte allocation (raw
slice `[]T`) is read/written as 32 bytes (managed slice `@[]T`).

## Reproduction

```sh
# Build compiled interpreter
cd bootstrap && go run . -root ../binate ../binate/cmd/bnc -- \
    --root ../binate -o /tmp/bni_test ../binate/cmd/bni

# Crashes:
/tmp/bni_test --test -root ../binate pkg/ir       # malloc corruption
/tmp/bni_test --test -root ../binate pkg/codegen   # segfault
/tmp/bni_test --test -root ../binate pkg/lint      # malloc corruption

# Works fine:
/tmp/bni_test --test -root ../binate pkg/token     # ok
/tmp/bni_test --test -root ../binate pkg/lexer     # ok
/tmp/bni_test --test -root ../binate pkg/parser    # ok
/tmp/bni_test -root ../binate /tmp/any_test.bn     # ok (non --test mode)
```

The crash does NOT happen in non-`--test` mode, even with identical code.
The difference: `--test` mode loads the full package (including all `_test.bn`
files) into the interpreter, while normal mode just imports compiled packages.

## Valgrind diagnosis

```
Invalid read of size 8
   at bn_interp__readScalar
   by bn_interp__msliceRefIncBacking
   by bn_interp__assignTo
Address 0x4cf9eb0 is 0 bytes after a block of size 16 alloc'd
   at malloc
   by bn_rt__c_malloc
   by bn_interp__allocFlat
   by bn_interp__envDefine
```

A 16-byte block (raw slice: `{data, len}`) is allocated by `allocFlat` in
`envDefine`. Later, `assignTo` treats this memory as a 32-byte managed slice
(`{data, len, backing, backingLen}`), reading 8 bytes past the end at offset 16.

## Root cause (narrowed but not pinpointed)

The interpreter's `envDefine` calls `allocFlat(valueType(val))`. The `val.Typ`
is `TYP_SLICE` (16 bytes), so 16 bytes are allocated. But later, a selector
assignment (`s.field = val`) hits a code path that checks the struct field's
type, which says `TYP_MANAGED_SLICE` (32 bytes). The field type and the
variable type disagree.

This means somewhere in the interpreter's type resolution, a `@[]T`
(managed-slice) type is being replaced by or confused with `[]T` (raw-slice).
Candidates:

1. **Type object corruption (use-after-free)**: Confirmed with GDB that a
   `@Type` object's `Kind` field changes from 10 (`TYP_MANAGED_SLICE`) to 9
   (`TYP_SLICE`) between creation and use. The type was created correctly by
   `resolveStructType` (confirmed with debug prints). The mutation is consistent
   with the `@Type` object being freed and its memory reused.

2. **Interpreter source bug**: The interpreter might have a code path that
   creates `VAL_SLICE` values with `Typ = TYP_SLICE` for data that is actually
   a managed slice. Checked `coerce`, `readFlatValue`, `evalSliceExpr` — they
   all appear correct.

3. **Compiler codegen bug**: The compiled interpreter binary might have
   incorrect code for some specific pattern. All simple patterns tested in
   conformance tests produce correct refcounts, but the interpreter binary
   exercises more complex patterns (deeply nested function calls, many type
   entries, etc.).

## What's been ruled out

- **`isFreshManagedPtr` OP_CALL bug**: Initially suspected, but the return-level
  RefInc protocol makes `OP_CALL` freshness correct. Conformance tests confirm
  balanced refcounts for all tested patterns.
- **Struct-copy constructor/destructor**: Working correctly in conformance tests
  (field assignment, append/copy, save/restore, function return by value).
- **Simple interpreter source bugs**: `envDefine`, `assignTo`, `evalSelector`,
  `readFlatValue`, `writeFlatValue`, `coerce` all appear correct on inspection.

## Confirmed: use-after-free (2026-04-11)

**DWARF line-level debug info added** — compiler now emits per-instruction
`!DILocation(line: N, scope: !M)` for source-level debugging.

**Disabling `Free` in `rt.bn` eliminates the bug.** With free disabled:
- pkg/lexer: all 10 tests PASS (was hanging on first test)
- pkg/lint: all 11 tests PASS (was crashing on first test)
- pkg/ir: gets much further (was hanging on first test)
- No rc≤0 sentinel triggers — the object IS correctly freed (rc reaches 0
  legitimately), but something still holds a dangling pointer to it.

**Not a double-free.** The object's refcount reaches 0 exactly once, and
the free is correct. The bug is that some other code holds a raw pointer
(or `@Type` reference without proper RefInc) that outlives the managed
reference keeping the object alive.

**Duplicate type registrations observed** (Type, TestResult from
pkg/builtin/testing) but confirmed NOT the cause — they happen in working
packages too, and skipping re-registration doesn't fix the bug.

**17 managed-to-raw-assign lint diagnostics in pkg/interp**, but inspection
shows they're all read-only patterns (StrOf → []char). The corruption
requires a *write* through a dangling pointer.

## Next steps

- The corruption writes to freed memory that gets reused for `@Type` objects.
  The write likely comes through a dangling flat-memory address
  (`EnvEntry.Addr`) or a dangling `Value.RawAddr` that outlives its scope.
- Instrument `writeFlatValue`/`writeScalar` to check if the target address
  overlaps a freed allocation (requires tracking freed ranges).
- Or: add a generation counter to `@Type` objects — set on creation, verify
  on use. If it doesn't match, the type was freed and reused.
