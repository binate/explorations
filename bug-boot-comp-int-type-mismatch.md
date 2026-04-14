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

## Interpreter refcounting fixes (2026-04-13)

Two fixes committed to address Axiom 3 violations:

1. **`copyValue` structRefInc** (committed): `copyValue` for structs did a raw
   memcpy without RefInc'ing managed fields. Now calls `structRefInc` after
   copy and sets `IsFresh=true`. This fixed conformance tests 135, 140.

2. **`execReturn` structRefInc** (committed): when returning a struct with
   managed fields, RefInc managed fields before scope cleanup. This ensures
   the returned value survives `cleanupEnvExcept`'s `structRefDec`.

Both fixes improved conformance (boot-comp-int: 193/1). But the unit test
hang persists.

## Poison-on-free diagnosis (2026-04-13)

**Technique**: before `c_free`, overwrite the payload with an incrementing
counter and set the header refcount to `-1000000 - counter`. This turns the
hang into a detectable exit.

**Result**: under lldb, the process exits instead of hanging. Backtrace:

```
frame #0: exit
frame #1: bn_exit(code=1)
frame #2: evalLen at interp.bn
frame #3: evalBuiltinCall at interp.bn:215
frame #4: evalExpr at interp.bn:61
frame #5: execVarDecl at interp.bn:384
frame #6: execStmt → execBlock → callFunc → evalCall → evalExpr
```

The interpreter evaluates `len(something)` on a managed-slice whose flat
memory (`RawAddr`) points to freed-and-poisoned data. The poisoned length
value is garbage, triggering an error exit. Without poison, the freed memory
contains whatever was there before → valid-looking data → infinite loop.

**Key observations**:
- The affected `RawAddr` comes from `allocFlat` (c_malloc, never freed).
  The flat allocation itself is valid. But the DATA stored in it (a
  managed-slice header: `{data, len, backing, backingLen}`) contains a
  pointer to freed managed memory.
- The managed-slice backing was freed (via RefDec → rc=0) while the flat
  memory still held the backing pointer. This is a raw-pointer UAF: the
  flat memory stores the managed pointer as raw bytes, not as a managed
  reference that would keep the backing alive.
- The `allocFlat` + 8-byte prefix test confirmed that changing heap layout
  eliminates the bug — the freed memory isn't adjacent to the type object
  that gets corrupted.

## Further investigation (2026-04-13)

**Poisoning header refcount** (`h[0] = -1000000 - counter`) plus adding
`h[0] < -999999` check to `RefInc` — NEVER triggers. No managed-pointer
operation (RefInc/RefDec) touches freed memory. The corruption is purely
through RAW POINTER reads (`readScalar`, `readFlatValue`, `bit_cast`).

**`evalLen` type check** — added check for `arg.Typ.Kind < 0 || > 20` in
`evalLen`. Never triggers. The type pointer IS valid; the flat memory DATA
is corrupted.

**`structRefInc`/`structRefDec` audit** — these correctly handle managed
fields in structs: `@T` fields via RefInc/RefDec, `@[]T` fields via
`msliceRefIncBacking`/`msliceRefDecBacking`. No bugs found.

**Flat memory is never freed** — `allocFlat` uses `c_malloc`, and
`freeFlat`/`c_free` are never called from the interpreter. So `EnvEntry.Addr`
always points to valid memory.

**Lint clean** — all 6 managed-to-raw diagnostics in `pkg/interp` are
read-only patterns (StrOf → []char). None cause writes through dangling
pointers.

**Remaining hypothesis**: the corrupted data in flat memory comes from
`writeFlatValue` copying from a SOURCE whose memory was freed. When a
struct value is written to flat memory via `c_memcpy`, the source's
managed-slice headers (containing backing pointers) are copied as raw bytes.
If the source's backing was freed between the time the Value was created
and the time `writeFlatValue` copies from it, the flat memory gets stale
pointers. OR: the compiler's codegen for the interpreter binary has a
subtle refcounting bug that doesn't manifest in conformance tests but
affects the interpreter's complex call patterns (many nested struct returns
and copies).

## What's been ruled out

- Flat memory UAF (allocFlat memory is never freed)
- Managed pointer UAF via RefInc/RefDec (poisoned header check never fires)
- Type pointer corruption (Kind field is valid at evalLen call time)
- structRefInc/structRefDec logic errors (audit found no issues)
- Interpreter lint diagnostics (all read-only patterns)
- collectTypeDecl double-resolution (partial fix committed, doesn't resolve)

## What would help

- Full DWARF debug info (variable-level, not just line-level) for the
  compiled interpreter, enabling valgrind to show exact variable names
  and source lines in the backtrace
- Or: run on Linux with valgrind to catch the exact first invalid read
- Or: a deterministic reproducer smaller than the full pkg/lexer test suite
