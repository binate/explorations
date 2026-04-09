# Plan: Multi-Return as Anonymous Struct

## Motivation

Multi-return values in Binate should be implemented as if the function
returns an anonymous struct whose field types correspond to the return
type sequence. This is not just an implementation detail — it's an ABI
contract that both the compiler and interpreter must agree on.

Currently, multi-return is special-cased in both the compiler and
interpreter with dedicated codegen/handling that differs from single
returns. This causes:

1. **Compiler bug**: multi-return with structs containing managed fields
   generates bad LLVM IR (`ret i64 %v7` type mismatch). The special
   multi-return lowering doesn't correctly handle complex types.
   Workaround applied in `pkg/asm/macho/macho.bn`.

2. **Interpreter legacy code**: multi-return uses `VAL_MULTI` with
   `Elems @[]@Value` — the last 3 Value.Elems references. This is the
   only remaining non-flat value representation.

3. **Interop complexity**: compiled and interpreted code must agree on
   how multi-return values are passed. Special-casing in both makes
   this harder to reason about.

## Design

### The ABI contract

A function with return types `(T1, T2, ..., Tn)` returns a value of
anonymous struct type `struct { _0 T1; _1 T2; ...; _n-1 Tn }`. The
struct uses the standard Binate struct layout (fields at `FieldOffset`
with alignment padding). The field names `_0`, `_1`, etc. are internal
— user code destructures via multi-assignment, not field access.

This is purely an ABI/implementation concern — the language syntax
doesn't change. `return a, b` still works. `x, y := f()` still works.
The difference is entirely in how the values are represented at the
IR/codegen/interpreter level.

### Layout examples

`func f() (int, bool)` → returns `struct { _0 int; _1 bool }`
- SizeOf = 16 (8 + 1 + 7 padding), fields at offset 0 and 8

`func g() (int, int)` → returns `struct { _0 int; _1 int }`
- SizeOf = 16, fields at offset 0 and 8

`func h() (StrTab, int)` → returns `struct { _0 StrTab; _1 int }`
- SizeOf = SizeOf(StrTab) + padding + 8, normal struct layout

### Why this fixes the multi-return managed-fields bug

The current bug: multi-return codegen uses a custom lowering that
flattens return values into individual LLVM values and packs them
into an LLVM struct type. For structs with managed fields, the
flattening produces incorrect types.

With the anonymous struct approach: the return value is a single
struct, lowered through the existing (working) struct return path.
The struct happens to have fields matching the return types. All
the existing struct codegen (sret, field access, refcounting,
destructors) applies automatically.

## Implementation

### Step 0: Add xfailed conformance test for the existing bug

Add a conformance test for `func f() (StructWithManagedField, int)`
that exercises the known bug. Xfail for boot-comp (and any other
failing modes). This provides a clear signal when the fix works.

### Step 1: Compiler — construct anonymous return struct type

In `pkg/ir/gen_stmt.bn` (or `gen_expr.bn`), when a function has
multiple return types:

1. **Create the anonymous struct type**: in `genFunc`, when
   `len(d.Results) > 1`, build an anonymous struct type with fields
   `_0`, `_1`, etc. corresponding to the return types. Register it
   like any other struct type.

2. **Change the function's IR return type**: instead of returning
   multiple values, the function returns a single value of the
   anonymous struct type.

3. **At return sites**: `return a, b` constructs the anonymous struct
   (insertvalue for each field) and returns it.

4. **At call sites**: the call returns the struct. Multi-assignment
   `x, y := f()` extracts fields 0, 1 from the struct.

### Step 2: Compiler — LLVM emission

In `pkg/codegen/emit.bn`:

1. **Function signature**: a multi-return function emits a return type
   that is the anonymous struct's LLVM type (or uses sret for large
   structs, as existing struct return logic does).

2. **Return instruction**: emit `ret %AnonStruct %val` instead of
   the current multi-value packing.

3. **Call instruction**: the call returns `%AnonStruct`. Extracting
   individual return values is `extractvalue %AnonStruct %result, 0`
   for the first value, etc.

This should largely fall out of the existing struct return/call
codegen paths. The key change is mapping multi-return functions to
struct-returning functions.

### Step 3: Remove the xfail for the managed-fields bug

After step 2, the managed-fields multi-return bug should be fixed
(since it goes through the normal struct return path). Verify by
removing the xfail and running tests.

Also revert the workaround in `pkg/asm/macho/macho.bn` (change
back to `strTabAdd(st StrTab, s []char) (StrTab, int)`).

### Step 4: Interpreter — flat anonymous struct for multi-return

In `pkg/interp`:

1. **execReturn**: instead of creating `VAL_MULTI` with Elems,
   construct a flat anonymous struct (allocFlat + writeFlatValue
   for each field at its FieldOffset).

2. **Call site destructuring**: `x, y := f()` reads fields from
   the struct via readFlatValue at field offsets, instead of
   indexing into Elems.

3. **Remove `VAL_MULTI`**: no longer needed. Remove `MakeMultiVal`,
   the `Elems` accesses in `execAssign` and `execShortVarDecl`.

This eliminates the last 3 Value.Elems references, completing the
legacy Elems removal.

### Step 5: Cleanup

- Remove the `Elems @[]@Value` field from the Value struct
  (in `pkg/interp.bni`) if no other code uses it.
- Remove `MakeMultiVal` from value.bn.
- Update unit tests.

## Testing

- Conformance test for multi-return with managed-field struct (step 0)
- Existing conformance tests cover basic multi-return: 004, 015, 066,
  219 (error case)
- Unit tests for IR gen changes
- Run full suite after each step: `conformance/run.sh basic`,
  `scripts/unittest/run.sh boot`

## Not in scope

- Changing user-facing syntax (remains `return a, b` and `x, y = f()`)
- Named return values (future feature)
- Variadic returns
