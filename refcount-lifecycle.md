# Refcount Lifecycle

This document describes how reference counts are managed throughout the
lifetime of managed values in Binate. It covers the rules for every context
where managed pointers (`@T`) and managed-slices (`@[]T`) are created, copied,
passed, returned, and destroyed.

## Core Invariant

**rc == 0 means dead.** A managed allocation with refcount 0 has no live
references and may be freed. Every live reference to a managed allocation
must be reflected in the refcount. Violating this invariant leads to either:
- Premature frees (use-after-free): rc reaches 0 while references still exist
- Leaks: rc never reaches 0 because references weren't decremented

## Refcount Operations

- **Alloc**: creates a managed allocation with rc = 1
- **RefInc(ptr)**: rc += 1 (a new reference to ptr is being created)
- **RefDec(ptr)**: rc -= 1 (a reference to ptr is being destroyed); if rc == 0, free

## Ownership Transfer Convention

When a managed value is passed from one context to another (function return,
function argument, assignment), ownership of one reference is transferred.
The **slow (safe) approach** always performs explicit RefInc/RefDec to maintain
the invariant. The **fast approach** (optimization) recognizes when a RefInc
and RefDec would cancel out and skips both.

**We implement the slow approach first.** The fast approach (move optimizations)
is deferred — it's a pure optimization that doesn't change semantics.

## Contexts

### 1. Variable Declaration and Assignment

```
var x @T = expr
x = expr2
```

**Declaration with initializer**:
- Evaluate `expr`, producing a managed value
- If `expr` is fresh (make/box/make_slice): rc is already 1, representing x's reference. No RefInc needed.
- If `expr` is not fresh (variable load, function call, field access, slice element): RefInc to create x's reference.
- Note: function calls are NOT fresh in the general sense — see "Function Returns" below.

**Assignment to existing variable**:
- RefDec the old value of x (destroying x's old reference)
- Evaluate new expr
- If not fresh: RefInc (creating x's new reference)
- Store new value in x

**Scope exit**:
- RefDec x (destroying x's reference)

### 2. Function Returns

```
func foo() @T {
    ...
    return expr
}
```

**Slow approach**: every `return expr` where the return type is `@T` or `@[]T`:
1. Evaluate `expr`
2. **RefInc the return value** — this creates the caller's reference
3. Normal scope cleanup runs (locals RefDec'd, temporaries die)
4. Return the value to caller

This is always correct regardless of what `expr` is:

- `return localVar`: RefInc(+1), then localVar's RefDec(-1) on exit. Net: one ref transferred to caller.
- `return globalVar`: RefInc(+1), global keeps its ref. Caller gets a new ref.
- `return make(T)`: make creates rc=1 (temporary). RefInc(+1) = rc=2. Temporary dies(-1) = rc=1. One ref transferred.
- `return otherFunc()`: inner call transfers rc=1. RefInc(+1) = rc=2. Temporary dies(-1) = rc=1. One ref transferred.
- `return slice[i]`: slice element has rc≥1. RefInc(+1). No scope exit dec for the temporary element read. One ref transferred.

**At the call site**: the caller receives a value with one transferred reference.
When stored in a local variable, that transferred reference IS the local's
reference — no additional RefInc needed. This is why `isFreshManagedPtr`
returns true for `OP_CALL`: the callee already accounted for the transfer.

**Fast approach (deferred)**: when `expr` is an expiring local (last use before
scope exit), skip the RefInc and skip that local's RefDec. The local's
reference becomes the caller's reference directly. Analogous to C++11 move
semantics — only applies when the source is expiring. Does NOT apply to
globals, captured variables, or anything with a lifetime beyond the return.

### 3. Function Arguments

```
foo(expr)
```

Where `foo` takes `@T`:

**Slow approach**:
- Callee entry: RefInc each `@T` parameter (the parameter is a new reference)
- Callee exit: RefDec each `@T` parameter (the parameter's reference dies)

This means the caller doesn't need to do anything special — the callee manages
its own parameter references. The caller's reference (if any) is unaffected.

**Fast approach (deferred)**: if the argument is an expiring temporary or
last-use local, skip the caller's RefDec and the callee's entry RefInc.

**Raw pointer parameters** (`*T`): when `@T` is passed to a function taking
`*T`, the managed-to-raw conversion is implicit. The managed value must remain
alive for the duration of the call. The caller's reference keeps it alive
(no special action needed as long as the caller holds a reference).

**Important case — `f(box(...))`**: `box(...)` creates a temporary with rc=1.
If `f` takes `@T`: callee RefInc's parameter (rc=2), uses it, RefDec's on
exit (rc=1). After the call, the temporary dies (RefDec, rc=0, freed). Correct.
If `f` takes `*T`: implicit `@T → *T` conversion. The temporary (rc=1) must
survive the call. The temporary's RefDec happens after `f` returns (end of
statement), so the temporary is alive during the call. Correct.

### 4. Temporaries

Temporaries are unnamed managed values produced by expressions. They are
real references and must be properly refcounted.

**Lifetime**: a temporary lives until the end of the statement that created it,
unless ownership is transferred (to a variable, return value, or function
parameter via the fast approach).

**Examples**:
- `println(make(T).Field)`: `make(T)` creates a temporary (rc=1). `.Field`
  accesses it. After the println call completes, the temporary dies (RefDec,
  rc=0, freed).
- `x = foo().Field`: `foo()` returns a temporary (rc=1, transferred). `.Field`
  is accessed. The temporary must survive at least until the field value is
  extracted. Then the temporary dies (RefDec, rc=0, freed). The field value
  (if `@T`) was RefInc'd when loaded.

### 5. Managed-Slice Operations

**`make_slice(T, n)`**: allocates backing memory, returns `@[]T` with the
backing's rc=1.

**`s[i]` (read)**: returns the element value. If element type is `@T`, the
returned pointer is a temporary — the slice still holds its reference, and
the temporary is a second reference. In the slow approach, this read should
RefInc the element (creating the temporary's reference), and the temporary
dies at end of statement (RefDec). When stored in a local, the local's
RefInc creates a third reference, and the temporary's RefDec removes one.

In practice, for `var x @T = s[i]`:
- Read s[i]: RefInc element (temp ref)
- Assign to x: no RefInc (treating temp as "fresh" per current convention)
- Temp dies: RefDec... but when?

This needs more thought. Currently the codegen does NOT RefInc on slice_get.
The value read from the slice is treated as a temporary but without explicit
lifetime management. This works as long as the slice remains alive (its
reference keeps the element alive), but is fragile.

**TODO**: define precise semantics for element-read temporaries.

**`s[i] = val` (write)**: RefDec old element, RefInc new value (unless fresh),
store. Already implemented.

**Subslicing `s[lo:hi]`**: produces a new `@[]T` that shares the backing.
RefInc on the backing's refptr (new managed-slice references the same backing).

**Scope exit**: RefDec on the managed-slice's backing_refptr (the managed-slice
value's reference to the backing dies).

### 6. Struct Field Access

**`obj.field` where field is `@T`**: loads a managed pointer from the struct.
This is similar to slice element read — the loaded pointer is a temporary
reference. The struct's reference keeps the pointed-to value alive.

In the slow approach: RefInc the loaded field value (creating a temporary
reference), RefDec when the temporary dies. When stored in a local, the
transfer convention applies.

**`obj.field = val`**: RefDec old field value, RefInc new value (unless fresh),
store. Already implemented.

### 7. Global Variables

Globals are never scope-exited, so they never get RefDec'd automatically.
They hold references for the lifetime of the program.

`return globalVar`: RefInc is needed (the global's reference stays, caller
gets a new one).

`globalVar = expr`: RefDec old global value, RefInc new value (unless fresh).

## Current Implementation Status (updated 2026-04-02)

**Working (slow approach)**:
- Variable declaration: RefInc on non-fresh values ✓
- Variable assignment: RefDec old, RefInc new ✓
- Struct field assignment: RefDec old, RefInc new ✓
- Slice element assignment: RefDec old, RefInc new ✓
- Function parameter entry/exit: RefInc/RefDec ✓
- Managed-slice backing RefInc/RefDec on copy/scope-exit ✓
- **Function return: RefInc on return value before scope cleanup ✓** (fixed 2026-04-02)
- **Free re-enabled in RefDec ✓** (boot-comp-comp and gen2 pass all 106 tests)

**Known issues — subslicing and temporaries (causes leaks, not crashes)**:

1. **Subslicing `@[]T` does not RefInc the backing refptr.** When `s[lo:hi]`
   creates a new managed-slice sharing the backing, it should RefInc the
   backing (new reference). Currently it doesn't. This means the subslice
   "borrows" without tracking. It works as long as the original slice
   remains alive, but if the original is RefDec'd to 0 while a subslice
   exists, the backing is freed with the subslice still pointing to it.
   Currently safe in practice because subslices are typically created from
   locals that outlive the subslice, but this is fragile.

2. **Managed-value temporaries do not get RefDec'd.** When an expression
   produces a temporary managed value (e.g., `foo().Field`, `s[i]` used
   inline, `box(x)` as a function argument), the temporary should be
   RefDec'd at end of statement. Currently no mechanism exists for tracking
   temporary lifetimes. This causes leaks (temporary's ref never released).
   For `@T` temporaries this is straightforward (one RefDec). For `@[]T`
   temporaries, the backing refptr needs RefDec.

3. **Managed-slice backing freed without RefDec-ing elements** (needs destructors —
   see plan-4word-managed-slice-destructors.md).

4. **Struct freed without RefDec-ing managed fields** (needs destructors).

Issues 1 and 2 happen to cancel out in common patterns: subslicing doesn't
RefInc, so the backing rc is 1 fewer than it should be. But temporaries
don't RefDec, so leaked refs prevent the rc from hitting 0. The result is
correct behavior by coincidence, not by design. Both should be fixed.

## Completed: RefInc on Return (2026-04-02)

**Fix**: emit `OP_REFCOUNT_INC` (for `@T`) or managed-slice refptr RefInc
(for `@[]T`) on return values in the IR gen (`gen_stmt.bn`), BEFORE
`emitDecForManagedLocals`. This ensures the return value's reference is
created before scope cleanup runs.

**Where**: `pkg/ir/gen_stmt.bn`, in the `STMT_RETURN` handler, before
`emitDecForManagedLocals`.

**Critical ordering**: the RefInc must be before scope-exit RefDecs.
Initially placed in codegen's `emitReturn` (after scope cleanup) — this
caused use-after-free because scope cleanup freed the backing before the
RefInc could run.

**What stays the same**:
- `isFreshManagedPtr` returns true for `OP_CALL` — correct, because the
  callee's return-RefInc already accounts for the caller's reference
- Callee entry RefInc / exit RefDec on parameters — unchanged
- Variable assignment RefInc/RefDec — unchanged

### Steps

1. Write a conformance test that fails with Free enabled (demonstrates the bug)
2. Fix codegen: emit RefInc on return values of managed type
3. Verify the conformance test passes
4. Re-enable Free and run boot-comp-comp
5. If more crashes: investigate (likely the slice-element-read or field-read issues)

### Future (deferred)

- Move optimization: skip RefInc-on-return + skip RefDec-on-local for expiring locals
- RefInc on slice element reads and struct field reads of @T
- Destructors for managed-slice backing and struct fields
