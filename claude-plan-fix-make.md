# Plan: Fix `make` Semantics, Add `make_slice`, Migrate Existing Code

## Problem

`make([]T, n)` has two issues:

1. **Semantic ambiguity.** `make(T)` should return `@T` for any type T. If T is
   `[]int`, then `make([]int)` should return `@([]int)` — a managed pointer to a
   raw slice. But the current `make([]T, n)` is special-cased to return `@[]T`
   (a managed-slice — the 3-word type). This breaks `make`'s uniformity and creates
   a trap for generics (`make(T)` where `T=[]int`).

2. **Implementation mismatch.** Even with the special-case typing, the runtime
   (`bn_make_slice`) returns a raw `BnSlice` (ptr + len via `calloc`) with no
   refcount header. All existing code assigns the result to `[]T` variables.

## Design Decision

- **`make(T)`** takes any type T, returns `@T`. No size argument. Uniform.
  - `make(Point)` → `@Point`
  - `make([]int)` → `@([]int)` (managed ptr to zero-value raw slice)
  - `make([100]int)` → `@([100]int)` (managed ptr to zero-init fixed-size array)

- **`make_slice(T, n)`** takes an element type and runtime size, returns `@[]T`
  (managed-slice — the special 3-word type: refptr, data_ptr, length). This is
  the ONLY way to create runtime-sized managed-slices.

- **`make_raw_deprecated([]T, n)`** temporary builtin preserving current broken
  behavior (returns raw `[]T` via `calloc`). Exists only during migration.

- **Notation**: `@([k]T)` with parens for managed pointer to fixed-size array.
  `@[k]T` without parens is ambiguous and should not be used.

## Current `make([]T, n)` Call Sites (~10)

| File | Line | Code |
|------|------|------|
| compile.bn | 510 | `var buf []uint8 = make([]uint8, 4096)` |
| compile.bn | 531 | `var buf []uint8 = make([]uint8, len(data))` |
| main.bn | 121 | `var buf []uint8 = make([]uint8, 4096)` |
| mini_driver.bn | 35 | `var buf []uint8 = make([]uint8, 4096)` |
| loader/loader.bn | 63 | `var buf []uint8 = make([]uint8, 4096)` |
| interp/bootstrap_fwd.bn | 90 | `var buf []uint8 = make([]uint8, bufSize)` |
| interp/bootstrap_fwd.bn | 107 | `var buf []uint8 = make([]uint8, n)` |

Plus 2 conformance tests (009_slices, 070_char_slice_set) and 1 test string.

All assign to `[]T` (raw slice), not `@[]T`. These are I/O buffers — raw slices
are arguably correct for them (data passed to syscalls, not retained).

## Migration Plan

### Step 1: Add `make_raw_deprecated` builtin

A temporary builtin that does exactly what `make([]T, n)` does today: allocates
a raw `[]T` via `calloc`. Returns `[]T` (NOT `@[]T`).

Changes across all layers:

| Component | File | Change |
|-----------|------|--------|
| Token | `pkg/ast.bni`, bootstrap `token/` | Add `MAKE_RAW_DEPRECATED` token |
| Parser | `pkg/parser/parser.bn`, bootstrap parser | Parse `make_raw_deprecated(...)` |
| Type checker | `pkg/types/checker.bn`, bootstrap checker | Returns `[]T` (raw slice type) |
| IR gen | `pkg/ir/gen.bn` | Emit `OP_MAKE_SLICE` (same as current) |
| Codegen | `pkg/codegen/emit.bn` | No change needed |
| Bootstrap interp | `interpreter.go` | Handle `make_raw_deprecated` → SliceVal |
| Self-hosted interp | `interp/interp.bn` | Handle similarly |

**Commit 1a**: Add to bootstrap (token, parser, checker, interpreter).
**Commit 1b**: Add to self-hosted compiler (token, parser, checker, IR).
**Commit 1c**: Add to self-hosted interpreter.

Validate: conformance suite, self-compilation.

### Step 2: Convert existing `make([]T, n)` → `make_raw_deprecated([]T, n)`

Mechanical find-and-replace across all ~10 call sites + 2 conformance tests.

**Commit 2**: One commit, all sites.

Validate: conformance suite, self-compilation. Behavior identical.

### Step 3: Remove the `n` form from `make`

Now that no code uses `make([]T, n)`, remove the special-case:
- Type checker: `make(T)` always returns `@T`, no second argument accepted
  for slice types. `make([]T)` returns `@([]T)`.
- IR gen: `make([]T)` emits `OP_MAKE` (like any other type), not `OP_MAKE_SLICE`
- Bootstrap interpreter: `make([]T)` creates a managed pointer to a zero-value
  raw slice (null ptr, length 0), not a managed-slice

For `make([k]T)`:
- Type checker: returns `@([k]T)` — managed pointer to fixed-size array
- IR gen: `OP_MAKE` with array type (may already work via existing path)
- Codegen: existing `OP_MAKE` path allocates `sizeof([k]T)` bytes, works

**Commit 3a**: Remove `n` argument from `make([]T, ...)` in type checkers.
**Commit 3b**: Update IR gen — `make` with slice type emits `OP_MAKE`, not
`OP_MAKE_SLICE`.
**Commit 3c**: Update interpreters — `make([]T)` returns managed ptr to
zero-value raw slice.

Validate at each commit.

### Step 4: Add `make_slice` builtin

New builtin: `make_slice(T, n)` takes an element type T and a runtime size n,
returns `@[]T` (managed-slice — 3-word type).

#### 4a: Runtime — `bn_make_managed_slice`

```c
// Returns: {i8* refptr, i8* data, i64 len}
typedef struct {
    void    *refptr;   // points to refcount header
    void    *data;     // points past header to element data
    int64_t  len;
} BnManagedSlice;

BnManagedSlice bn_make_managed_slice(int64_t elem_size, int64_t length) {
    size_t header_size = 2 * sizeof(int64_t); // refcount + free_fn
    size_t data_size = length * elem_size;
    int64_t *header = calloc(1, header_size + data_size);
    header[0] = 1;                        // refcount = 1
    header[1] = (int64_t)free;            // free_fn = stdlib free
    BnManagedSlice ms;
    ms.refptr = (void*)header;
    ms.data = (void*)(header + 2);
    ms.len = length;
    return ms;
}
```

#### 4b: LLVM representation for `@[]T`

```llvm
%BnManagedSlice = type { i8*, i8*, i64 }  ; {refptr, data, len}
```

Operations needed:
- **Create**: `call %BnManagedSlice @bn_make_managed_slice(i64 elem_size, i64 n)`
- **Refcount inc**: extract refptr (field 0), call `bn_refcount_inc`
- **Refcount dec**: extract refptr (field 0), call `bn_refcount_dec`
- **To raw slice** (`@[]T → []T`): extract fields 1+2 into `%BnSlice`
- **Length**: extract field 2
- **Index**: extract data ptr (field 1), GEP + load/store
- **Load/store**: 3 × i64 loads/stores (or treat as `{i8*, i8*, i64}` aggregate)

#### 4c: Changes across layers

| Component | File | Change |
|-----------|------|--------|
| Token | `pkg/ast.bni`, bootstrap `token/` | Add `MAKE_SLICE` token |
| Parser | `pkg/parser/parser.bn`, bootstrap parser | Parse `make_slice(T, n)` |
| Type checker | `pkg/types/checker.bn`, bootstrap checker | Returns `@[]T` (managed-slice type) |
| IR gen | `pkg/ir/gen.bn` | New `OP_MAKE_MANAGED_SLICE` (or reuse `OP_MAKE_SLICE` with different semantics) |
| Codegen | `pkg/codegen/emit.bn` | Emit `bn_make_managed_slice` call, `%BnManagedSlice` type |
| Runtime | `binate_runtime.c` | Add `bn_make_managed_slice` |
| Bootstrap interp | `interpreter.go` | Handle `make_slice` |
| Self-hosted interp | `interp/interp.bn` | Handle `make_slice` |

Additional codegen work for `@[]T` as a first-class type:
- Load/store of `%BnManagedSlice` fields
- Refcount inc/dec on the refptr
- `@[]T → []T` conversion emission
- `len(@[]T)` emission
- Index operations on `@[]T`

**Commit 4a**: Add `bn_make_managed_slice` to runtime, `%BnManagedSlice` to preamble.
**Commit 4b**: Add `make_slice` token/parser/checker across bootstrap + self-hosted.
**Commit 4c**: IR gen + codegen for `OP_MAKE_MANAGED_SLICE`.
**Commit 4d**: `@[]T` load/store/refcount in codegen.
**Commit 4e**: `@[]T → []T` conversion, `len(@[]T)`, indexing.
**Commit 4f**: Conformance test for `make_slice`.

Validate at each commit.

### Step 5: Add `@([k]T) → @[]T` conversion

A managed pointer to a fixed-size array (`@([k]T)`) can be converted to a
managed-slice (`@[]T`) by constructing `(refptr, data_ptr, k)` where `data_ptr`
points into the same allocation.

This is useful for: `make([100]int)` → `@([100]int)`, then convert to `@[]int`
for passing to functions that take managed-slices.

**Commit 5**: Conversion support in type checker + codegen.

### Step 6: Implement CharBuf

With `make_slice(char, n)` properly returning `@[]char`, implement CharBuf per
`claude-plan-charbuf.md`.

**Commit 6**: CharBuf implementation + tests.

### Step 7: Convert append → CharBuf

Per `claude-plan-slice-compiler.md` and `claude-plan-slice-selfhost-interp.md`.

Multiple commits, one per file or logical group.

### Step 8: Remove `make_raw_deprecated`

Once all code is converted away from it:

**Commit 8**: Remove from all layers (token, parser, checker, IR, codegen,
interpreters, runtime).

## Implementation Order Summary

```
Step 1: Add make_raw_deprecated               [3 commits]
Step 2: Convert make([]T,n) → make_raw_dep.   [1 commit]
Step 3: Remove n-form from make                [3 commits]
Step 4: Add make_slice + @[]T codegen          [6 commits]
Step 5: Add @([k]T) → @[]T conversion         [1 commit]
Step 6: Implement CharBuf                      [1 commit]
Step 7: Convert append → CharBuf              [many commits]
Step 8: Remove make_raw_deprecated             [1 commit]
```

Steps 1-2 are mechanical and low risk.
Step 3 is straightforward — just removing a special case.
Step 4 is the bulk of the work — implementing `@[]T` as a first-class 3-word type.
Steps 5-8 build on the foundation.

## Risk Assessment

**Step 4 is high risk.** Implementing `@[]T` as a 3-word type in the codegen
touches: LLVM type representation, load/store, function call ABI, refcount
management, conversion to/from raw slices.

However, managed pointers (`@T` as `i8*`) already work, so there's precedent.
The managed-slice is conceptually `(managed_ptr, raw_slice)` bundled together.

**Mitigation:** Each sub-commit is validated independently. If step 4 proves
too large, we can pause after step 2 (everything works with `make_raw_deprecated`)
and take more time on the managed-slice implementation.

**Step 3 is moderate risk** — changing `make([]T)` semantics affects type checking
and IR generation. But since no code uses this form after step 2, there's nothing
to break.
