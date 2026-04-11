# Plan: Interpreter Struct Copy Refcounting

## Problem

The self-hosted interpreter (`pkg/interp`) does not RefInc managed fields
when copying structs by value, and does not RefDec them at scope exit. This
mirrors the compiler bug fixed in commit `2052570`, but in the interpreter.

The compiler fix generates `__copy_X` / `__dtor_X` functions. The
interpreter can't call those (they're LLVM IR constructs). Instead, the
interpreter needs equivalent logic in its own refcounting helpers.

## Current State

### What the interpreter handles today:

- `@T` and `@[]T` variables: RefInc on `envDefine`/`envSet`, RefDec on
  scope exit via `cleanupEnvExcept` ✓
- Struct field assignment (`s.Field = managedVal`): RefInc new, RefDec old ✓
- Slice element assignment (`arr[i] = structVal`): `structElemRefcount` ✓
- Return value ownership: `IsFresh` flag skips RefInc on `envDefine` ✓

### What's missing:

1. **Scope exit**: `cleanupEnvExcept` does NOT walk struct-typed variables
   to RefDec their managed fields. Struct locals with `@T`/`@[]T` fields
   leak on scope exit.

2. **Variable declaration** (`envDefine`): when defining a struct variable,
   managed fields within are not RefInc'd (except when `IsFresh` skips it
   anyway). Needed for copies like `var y Pos = x`.

3. **Variable assignment** (`envSet`): when overwriting a struct variable,
   old managed fields are not RefDec'd and new ones are not RefInc'd.

4. **`copyValue` for structs**: does a shallow memcpy of bytes, does NOT
   RefInc managed fields. Used by `envDefine` and assignment paths.

5. **Function arguments**: `callFunc` calls `envDefine` for each param.
   For struct params, the managed fields are not RefInc'd.

6. **Function return**: struct return values from non-local sources should
   have managed fields RefInc'd (local returns skip via `IsFresh`).

## Design

Add a helper `structRefInc(addr *uint8, typ @types.Type)` that walks struct
fields and RefInc's managed fields (recursively for nested structs). This is
the interpreter equivalent of the compiler's `__copy_X`. Similarly, add
`structRefDec(addr *uint8, typ @types.Type)` as the equivalent of `__dtor_X`.

These are recursive walks — the same logic as `structElemRefcount` but
split into separate Inc/Dec functions that work on a flat address.

### structRefInc(addr, typ)

For each field where `types.NeedsDestruction(fieldType)`:
- `@T`: read pointer at `addr + FieldOffset`, call `rt.RefInc`
- `@[]T`: read managed-slice at `addr + FieldOffset`, extract backing
  refptr (field 2), call `rt.RefInc`
- struct/`[N]T`: recurse with `addr + FieldOffset`

### structRefDec(addr, typ)

Same walk but RefDec:
- `@T`: read pointer, call `rt.RefDec(ptr, nil)`
- `@[]T`: read backing refptr, call `rt.RefDec(refptr, nil)`
- struct/`[N]T`: recurse

For arrays: loop `arrayLen` elements, call element RefInc/RefDec.

## Changes

### 1. Add helpers: `pkg/interp/helpers.bn`

```binate
func structRefInc(addr *uint8, typ @types.Type)
func structRefDec(addr *uint8, typ @types.Type)
```

Recursive field walk. Reuse `types.FieldOffset` for field addresses.
Handle `@T`, `@[]T`, nested struct, `[N]T` with destructible elements.

### 2. Scope cleanup: `cleanupEnvExcept` in `helpers.bn`

Add a case for struct-typed variables (where `types.NeedsDestruction`):
call `structRefDec(entry.Addr, entryType)`. Skip if the variable is in
the `except` list (being returned).

### 3. Variable declaration: `envDefine` in `helpers.bn`

After writing the value to flat memory, if the type is a struct with
`types.NeedsDestruction` and `!val.IsFresh`, call `structRefInc` on the
destination address. This handles `var y Pos = x` (copy from another
variable).

Skip for `IsFresh` values (function returns, `make`, `box`) — they
already carry correct refcounts.

### 4. Variable assignment: `envSet` in `helpers.bn`

For struct types with `NeedsDestruction`:
- Call `structRefInc` on the new value's address (RefInc before RefDec)
- Call `structRefDec` on the old destination address
- Then write the new value

Actually, since `envSet` writes flat bytes, the sequence is:
1. `structRefInc` on source (the value being assigned) — but the source
   is a Value, not necessarily at a stable address. We need to RefInc
   the destination AFTER writing.
2. Save old: `structRefDec` needs the old bytes, so RefDec before write.

Hmm — this is the same ordering issue as the compiler. Safe approach:
1. Read new value's managed fields and RefInc them (from the Value)
2. RefDec old destination's managed fields
3. Write new value

But the Value's `RawAddr` points to the source. We can RefInc from
`val.RawAddr`, then RefDec from `entry.Addr`, then memcpy.

Simpler: write first, then `structRefInc(dest)`, then `structRefDec(savedOld)`.
But we'd need to save old bytes. The simplest correct approach:

1. `structRefInc(val.RawAddr, typ)` — RefInc new
2. `structRefDec(entry.Addr, typ)` — RefDec old
3. Write new value to `entry.Addr`

This is RefInc-before-RefDec (safe). The val.RawAddr is valid because
it points to the source variable's memory.

### 5. Function arguments: no change needed

`callFunc` calls `envDefine` for params. With the `envDefine` fix (step 3),
struct param copies will get RefInc'd automatically. The scope cleanup
fix (step 2) will RefDec them when the function returns.

However: `callFunc` uses `copyValue` which does a shallow memcpy. The
`envDefine` call after `copyValue` will need to RefInc. Since `copyValue`
doesn't set `IsFresh`, the `envDefine` RefInc will fire. This should be
correct.

### 6. Function return: check `IsFresh` handling

For local struct returns, `execReturn` sets `IsFresh = true`. The caller's
`envDefine` skips RefInc. The callee's `cleanupEnvExcept` skips RefDec
(via `except` list). Balanced.

For non-local struct returns (expression results), `IsFresh = false`. The
caller's `envDefine` will RefInc. No scope cleanup RefDec needed because
the value isn't in the callee's scope. Balanced.

This should work correctly with the fixes in steps 2-3.

## Implementation Order

1. Add `structRefInc` and `structRefDec` helpers
2. Update `cleanupEnvExcept` for struct scope cleanup
3. Update `envDefine` for struct RefInc
4. Update `envSet` for struct RefInc/RefDec
5. Verify conformance tests 222-224 pass on boot-comp-int
6. Run full boot-comp-int conformance suite

## Testing

- Conformance tests 222, 223, 224 exercise struct copy with managed fields
- `TestScopeCleanupRefDec` in `call_test.bn` has a TODO for the expected
  broken behavior — update it once the fix is in
- Run `conformance/run.sh boot-comp-int` and `scripts/unittest/run.sh boot-comp`
