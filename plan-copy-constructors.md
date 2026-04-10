# Plan: Copy Constructors for Structs with Managed Fields

## Problem

When a struct containing `@T` or `@[]T` fields is copied by value, the
compiler does not RefInc the managed fields in the copy. Destructors
correctly RefDec them at scope exit. The result: over-decrement → UAF →
heap corruption. Detailed writeup: `bug-struct-copy-refcount.md`.

Four copy sites are affected:

1. **Variable assignment**: `var x Pos = y` / `x = y`
2. **Struct field assignment**: `tok.Pos = pos`
3. **Function return**: `return pos`
4. **Function argument passing**: `f(pos)`

## Design: Emit Copy Functions (Symmetric to Destructors)

Generate a **copy function** for each type that `NeedsDestruction`. The copy
function RefInc's all managed fields in a struct value (the inverse of the
destructor's RefDec walk). This mirrors the dtor infrastructure exactly:

```
__copy_Pos(ptr *u8)         // walks Pos fields, RefInc's File.backing
__copy_Token(ptr *u8)       // walks Token fields, RefInc's Lit.backing + Pos.File.backing
__copy_arr5_mp_Node(ptr *u8) // array copy: loop 5 elements, RefInc each @Node
```

### What gets copy functions (and what doesn't)

Copy functions are generated for the same categories as destructors, minus
managed-slices:

- **Structs** (including anonymous): `__copy_X` — yes
- **`[N]T` arrays** (with destructible elements): `__copy_arrN_X` — yes
- **`@[]T` managed-slices**: NO copy function — just inline `RefInc` on the
  backing refptr at copy sites (same as we do for `@T` managed pointers)
- **`@T` managed pointers**: NO copy function — just inline `RefInc`

Rationale: `@T` copy is a single RefInc. `@[]T` copy is also a single
RefInc (on the backing). Neither needs a function. Only structs and arrays
have multiple fields/elements requiring a walk.

### Why copy functions for structs (not inline RefInc at each copy site)?

- **Structs can be deeply nested**: `Token` contains `Pos` which contains
  `@[]char`. Inline RefInc would need to recursively walk fields at every
  copy site — duplicating the same logic the destructor already has.
- **Symmetry with destructors**: each struct type that has a `__dtor_X` also
  gets a `__copy_X`. The dtor walks fields to RefDec; the copy walks fields
  to RefInc. Same naming scheme, same generation infrastructure.
- **Cross-package types**: extern dtor declarations already work for
  cross-package types. Copy function declarations work the same way.

### What a struct copy function does

For each field where `NeedsDestruction(fieldType)`:

- **`@T` field**: load the managed pointer, `RefInc` it.
- **`@[]T` field**: load the managed-slice, extract field 2 (backing_refptr),
  `RefInc` it. (Element refcounts are NOT touched — they belong to the
  backing, not the view.)
- **struct / `[N]T` field (inline, with managed sub-fields)**: get field
  pointer, call the field type's copy function recursively.

This is exactly the dtor walk with RefInc instead of RefDec, and without
the managed-slice element iteration (elements are shared, not copied).

### Array copy function

For `[N]T` where element type needs destruction: iterate N elements and call
element copy. Same loop structure as the array dtor, but calling `__copy_X`
instead of `__dtor_X` (or RefInc instead of RefDec for `@T` elements).

## Naming

Mirror the destructor naming with `__copy_` prefix:

| Type | Dtor name | Copy name |
|------|-----------|-----------|
| `struct Pos` | `__dtor_Pos` | `__copy_Pos` |
| `@Node` | (inline RefDec) | (inline RefInc) |
| `@[]@Node` | `__dtor_ms_mp_Node` | (inline RefInc on backing) |
| `[5]@Node` | `__dtor_arr5_mp_Node` | `__copy_arr5_mp_Node` |

## Files to Change

### 1. Copy function naming: `pkg/ir/gen_copy.bn` (new)

Mirror `gen_dtor.bn`. Functions:
- `copyNameForType(t @types.Type) @[]char` — `"__copy_" + typeSuffix`
- `qualifiedCopyNameForType(t @types.Type) @[]char` — cross-package variant
- `copyName(structName []char) @[]char` — legacy-style helper

Can reuse `dtorTypeSuffix` from `gen_dtor.bn` for the suffix (it builds the
type encoding string that's shared between dtor and copy names).

### 2. Copy function generation: `pkg/ir/gen_copy_emit.bn` (new)

Mirror `gen_dtor_emit.bn`. Functions:

- **`generateCopies(m @Module)`** — called alongside `generateDtors`. Same
  2-pass structure as dtors: local structs, then qualified structs. No
  pass 3 needed (no managed-slice copy functions to generate).

- **`genStructCopy(name, structTyp) @Func`** — for each field:
  - `@T`: load managed ptr → `EmitRefcountInc`
  - `@[]T`: load managed-slice → extract field 2 → `EmitRefcountInc`
    (no element iteration — just the backing)
  - struct/`[N]T`: get field ptr → call field type's copy function

- **`genArrayCopy(name, arrTyp) @Func`** — loop N elements, call element
  copy (or RefInc for `@T` elements).

### 3. Emit copy calls at copy sites

#### 3a. Variable assignment: `pkg/ir/gen_control.bn`

After storing the struct value, if `types.NeedsDestruction(varTyp)` and the
type is a struct (or array/named struct), call the copy function on the
destination pointer. Also RefDec the old value's managed fields first (via
the dtor, or by calling copy on old + dtor pattern).

Actually, the cleaner approach: **before the store, call the dtor on the old
value (to RefDec old managed fields), then store, then call copy on the new
value (to RefInc new managed fields).**

Wait — this is the same RefInc-before-RefDec ordering issue. The safe
sequence is:
1. Call copy on the source value (RefInc new fields) — but source is a value,
   not a pointer. We need a pointer to call the copy function.
2. Store the new value into the destination.
3. Call dtor on the old value (RefDec old fields) — but old is already
   overwritten.

Better approach: **emit the copy call on the destination pointer AFTER the
store.** For the old value, we need to RefDec its managed fields before the
store overwrites them. Two options:

- **Option A**: Load old value into a temp alloca, store new value, call copy
  on dest (RefInc new), call dtor on temp (RefDec old).
- **Option B**: Call dtor on dest (RefDec old), store new value, call copy on
  dest (RefInc new). Simpler, but RefDec-before-RefInc risks cascade issues
  if old and new share fields.

**Option A is safer** (RefInc before RefDec). But it requires a temp alloca.

Actually, let's look at what we already do for direct `@T` and `@[]T` fields:
we RefInc new, then RefDec old. We should do the same here:
1. Store new value.
2. Call copy on dest ptr (RefInc new managed fields).
3. Call dtor on a saved-old-value (RefDec old managed fields).

But we need the old value saved before the store. So:
1. Save old: `old = load dest`; store old into temp alloca.
2. Store new value into dest.
3. Call `__copy_X` on dest ptr (RefInc new).
4. Call `__dtor_X` on temp alloca ptr (RefDec old).

This handles the RefInc-before-RefDec ordering correctly.

**Fresh allocation optimization**: if the RHS is a fresh `make(T)`, skip
the copy call (rc is already correct). Use `consumeTemp` as we do for `@T`.

#### 3b. Struct field assignment: `pkg/ir/gen_control.bn`

Same pattern as 3a, but the destination is a field pointer from
`EmitGetFieldPtr` rather than a variable's alloca.

#### 3c. Function return: `pkg/ir/gen_stmt.bn`

Before returning a struct value, if `NeedsDestruction(retTyp)`:
- If returning a local variable: the cleanup skip already avoids double-free.
  But we need the copy's managed fields to survive the scope cleanup. The
  current code skips RefInc for local `@T`/`@[]T` returns but doesn't handle
  structs containing them. **Fix**: if returning a local struct variable,
  call `__copy_X` on the alloca before loading the return value. The scope
  cleanup dtor will then balance it. Actually this is tricky.

  Simpler: for local struct returns, treat it like non-local — call
  `__copy_X` after the load but before the return. The scope dtor will
  RefDec. The copy RefInc + dtor RefDec balance for the local, and the
  caller gets a properly RefInc'd value.

  But wait — the value is a loaded struct, not a pointer. We'd need to
  store it to a temp alloca, call copy on that, then return the loaded
  value. This is wasteful.

  **Better approach for returns**: instead of calling the copy function,
  inline the managed-field RefInc walk. For each managed field in the
  return type struct:
  - `@T` field: `EmitExtract` field → `EmitRefcountInc`
  - `@[]T` field: `EmitExtract` field → extract backing → `EmitRefcountInc`
  - nested struct/array: need pointer, so alloca + store + call copy

  OR: always store the return value to a temp alloca, call `__copy_X` on
  it, then load it back and return. The optimizer will clean this up.

  **Recommendation**: Use the alloca + copy function call approach for
  simplicity. The LLVM optimizer will eliminate the redundant load/store.

- If returning a non-local (expression/temp): same — alloca + copy + return.

#### 3d. Function arguments: `pkg/ir/gen_expr.bn`

Before passing a struct value as a function argument, if
`NeedsDestruction(paramTyp)`: store to temp alloca, call `__copy_X`, load
back. The callee's scope cleanup (dtor) will RefDec when the parameter goes
out of scope.

**Note**: for arguments, we need to think about whether the callee actually
runs a dtor on its parameters. If parameters are stack-allocated with dtors,
then yes, the copy RefInc is needed. If not, the copy RefInc would leak.

Currently, function parameters get alloca'd in the callee and the callee's
scope cleanup runs dtors on them. So yes, copy RefInc is needed for struct
arguments.

### 4. Module initialization: `pkg/ir/gen.bn`

Call `generateCopies(m)` alongside `generateDtors(m)` after struct fields
are populated.

### 5. Extern declarations for cross-package copy functions

Same pattern as `declareExternDtor` — add `declareExternCopy` for
cross-package struct types.

## Implementation Order

1. **Copy naming** (`gen_copy.bn`): `copyNameForType`, reuse `dtorTypeSuffix`
2. **Copy generation** (`gen_copy_emit.bn`): struct, managed-slice, array
3. **Module integration** (`gen.bn`): call `generateCopies`
4. **Variable assignment** (`gen_control.bn`): struct copy + old dtor
5. **Struct field assignment** (`gen_control.bn`): same pattern
6. **Function return** (`gen_stmt.bn`): copy before return
7. **Function arguments** (`gen_expr.bn`): copy before call
8. **Conformance tests**: verify token.Pos / token.Token copy scenarios
9. **Interpreter**: mirror copy RefInc logic if needed

Run `conformance/run.sh basic` and `scripts/unittest/run.sh boot` after each
step. Run `conformance/run.sh all` and `scripts/unittest/run.sh all` at end.

## Not in Scope

- Optimizing away copy+dtor pairs when source is immediately destroyed (copy
  elision). This is a future optimization.
- Move semantics. Binate has no move concept — all struct copies are copies.
- Element-level copy for managed-slice subslicing. Subslicing already handles
  backing refcount correctly.

## Open Questions

1. **alloca + copy for returns/args**: is the overhead acceptable?
   **Recommendation**: yes, LLVM's mem2reg and SROA passes eliminate
   redundant alloca/load/store patterns. The generated IR is verbose
   but the machine code is clean.
