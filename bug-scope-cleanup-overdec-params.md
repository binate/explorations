# Bug: Scope cleanup over-decrements @T parameters stored in struct fields

## Summary

The compiled code's scope cleanup RefDec's `@T` parameters at function exit.
When a parameter was stored in a struct field (which RefInc'd it), the scope
cleanup's RefDec cancels out that RefInc. The struct's destructor later
RefDec's the field again — one more RefDec than RefInc. This over-decrements
shared objects like global type singletons.

## Concrete trigger

`AssignableTo` in `pkg/types/types.bn:383-384` creates temporary `@Type`
wrappers on every call:

```binate
var charSlice @Type = MakeSliceType(predeclaredUint8)
var managedCharSlice @Type = MakeManagedSliceType(predeclaredUint8)
```

Each call to `MakeManagedSliceType(predeclaredUint8)` over-decrements the
global `predeclaredUint8` singleton. After enough calls to `AssignableTo`,
`predeclaredUint8` reaches refcount 0, is freed, and its memory is reused.
Any type containing `uint8`/`char` (like `CharBuf.Data @[]char`) now has a
dangling `@Type` pointer. Reading the freed memory produces wrong `Kind`
values, causing `[]T` vs `@[]T` confusion and heap buffer overflows.

## The over-decrement mechanism

Inside `MakeManagedSliceType`:

```binate
func MakeManagedSliceType(elem @Type) @Type {
    var t @Type = make(Type)
    t.Kind = TYP_MANAGED_SLICE
    t.Elem = elem       // (1) field assignment: RefInc elem
    return t             // (2) RefInc t (non-local return)
}
// (3) scope cleanup: RefDec elem (parameter)
```

Step-by-step refcount of `predeclaredUint8` (starting at rc=N):

1. Caller loads `predeclaredUint8` from global — no RefInc (it's a load)
2. `t.Elem = elem` — managed-ptr field assignment → **RefInc elem** (rc=N+1)
3. `return t` — RefInc `t`, not `elem`
4. Scope cleanup — **RefDec `elem`** (rc=N)

Net effect on `predeclaredUint8`: N → N+1 → N. Balanced so far.

But the caller now holds `t`, whose `Elem` points to `predeclaredUint8`.
When `t` goes out of scope (e.g., `managedCharSlice` at end of
`AssignableTo`), `t`'s destructor runs `__dtor_Type`, which RefDec's
`t.Elem`:

5. `__dtor_Type` → **RefDec `t.Elem`** (rc=N-1)

**Net: N → N-1.** Each call to `MakeManagedSliceType(predeclaredUint8)`
decrements the singleton's refcount by 1.

## Why this happens

The scope cleanup at step 4 is the problem. The compiled code generates
RefDec for all `@T` parameters at function exit. This is correct for
parameters that are NOT stored elsewhere — the function borrowed them and
should release. But when a parameter is stored in a struct field (step 2),
the field assignment already RefInc'd it. The scope cleanup RefDec then
**cancels the RefInc**, leaving the field with an unowned reference.

The field assignment's RefInc and the scope cleanup's RefDec are both
individually correct, but together they produce a net-zero change when the
correct net change should be +1 (the field now owns a reference).

## Diagnosis trail

1. **Valgrind** showed 16-byte allocation read as 32 bytes — `allocFlat`
   allocates based on `TYP_SLICE` (16 bytes) when the data needs
   `TYP_MANAGED_SLICE` (32 bytes).

2. **GDB watchpoint** on the `@Type` object's Kind field caught it being
   zeroed by `memset` inside `rt.Alloc` — the `@Type`'s memory was freed
   and reused for a new allocation.

3. **No-free instrumentation** (disabling `Free` in `rt.bn`): all 195
   `pkg/ir` tests pass. Confirms use-after-free, not a logic error.

4. **Crash-on-Kind-10 instrumentation**: null-deref crash when a `@Type`
   with Kind=10 (`TYP_MANAGED_SLICE`) reaches refcount 0. Initially pointed
   to `collectTypeDecl` — but after adding a skip for already-resolved types,
   the crash moved to `AssignableTo`. This revealed that `collectTypeDecl`
   was a red herring; the real issue is the cumulative over-decrement from
   repeated `MakeManagedSliceType` calls in `AssignableTo`.

## Scope of impact

This affects **any function that receives `@T` as a parameter and stores it
in a struct field**. The pattern is ubiquitous:

```binate
func MakePointerType(elem @Type) @Type { ... t.Elem = elem ... }
func MakeSliceType(elem @Type) @Type { ... t.Elem = elem ... }
func MakeFuncType(params @[]@Param, results @[]@Type) @Type { ... }
func MakeStructType(name []char, fields @[]@Field) @Type { ... }
```

All of these over-decrement their parameters. The bug manifests when the
same `@T` object is passed to many such calls (like `predeclaredUint8`
passed to every `MakeSliceType`/`MakeManagedSliceType` call).

## Possible fixes

1. **Don't RefDec `@T` parameters at scope exit.** Parameters are borrowed
   references — the caller manages their lifetime. The function should not
   RefDec them. Only RefDec locally-created `@T` values.

2. **RefInc `@T` parameters on function entry.** If the function takes
   ownership of the parameter (by incrementing its refcount on entry), then
   the scope cleanup's RefDec is correct. This matches "copy on receive"
   semantics.

3. **Skip scope cleanup RefDec for parameters that were stored in fields.**
   Track which parameters were consumed by field assignments and skip their
   cleanup. This is the most targeted fix but requires tracking.

Option 1 is the simplest and matches the convention that function calls
transfer a borrowed reference (the caller keeps its reference alive for the
duration of the call). The caller's own scope cleanup handles the caller's
reference.
