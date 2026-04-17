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

**Spec**: The callee is responsible for ensuring the returned value carries
exactly one transferred reference for the caller. After the return, the
caller owns that reference.

**Implementation (slow approach)**: every `return expr` where the return
type is `@T` or `@[]T`:
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

**Move optimization (implemented for locals)**: when `expr` is a local
variable, skip the RefInc and skip that local's RefDec at scope exit. The
local's reference becomes the caller's reference directly. This is
observable via `rt.Refcount` (the intermediate rc=+1 from RefInc is never
seen), but the final result is the same. The current compiler implements
this for local-ident returns but not for arbitrary expiring values.

**Struct return values**: when the return type is a struct with managed
fields, the same principle applies — the callee arranges for the returned
struct's managed fields to have correct refcounts for one owner. For local
struct returns, the scope-exit dtor is skipped (move). For non-local struct
returns, a copy constructor (RefInc managed fields) runs before scope
cleanup.

### 3. Function Arguments

```
foo(expr)
```

**Spec**: Before the function body executes, each `@T` or `@[]T` parameter
must own its own reference. During the function body, the parameter is a
live reference. At function exit (scope cleanup), the parameter's reference
is released (RefDec), unless the parameter is being returned (ownership
transfer to caller).

**Implementation (callee-side RefInc)**:
- **Caller**: does NOT RefInc `@T`/`@[]T` arguments. The argument value is
  passed "as-is" — the caller's reference keeps it alive during the call.
- **Callee entry**: RefInc each `@T` parameter (creating the parameter's
  own reference). For `@[]T`, RefInc the backing refptr.
- **Callee exit**: RefDec each `@T`/`@[]T` parameter via scope cleanup
  (releasing the parameter's reference), unless the parameter is returned.

This design means the caller is uninvolved — the callee manages its own
parameter references. The caller's reference is unaffected by the call.

**Why callee-side**: this keeps the call site simple and avoids the
question of who "owns" the argument during the call boundary. It also
means the caller doesn't need to know whether a parameter is `@T` or `*T`
at the IR level (though in practice, the type is known).

**Struct parameters**: when a struct with managed fields is passed by value,
the copy constructor runs (RefInc all managed fields in the copy). At scope
exit, the struct destructor runs (RefDec all managed fields). No special
cases for OP_CALL results — always copy (axiom 3). The temp cleanup at end
of statement provides the balancing RefDec for the return's ownership ref.

**Move optimization (not yet implemented for arguments)**: if the argument
is an expiring temporary (e.g., `f(make(T))`), the temporary's reference
could transfer directly to the parameter, skipping the callee entry RefInc
and the temp cleanup RefDec. This is a pure optimization — the observable
result is the same.

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

### 3a. Storing Parameters in Struct Fields

A common pattern:
```
func MakeWrapper(n @Node, tag int) Wrapper {
    var w Wrapper
    w.Node = n    // field assignment: RefInc n
    w.Tag = tag
    return w
}
```

The field assignment `w.Node = n` RefInc's `n` (creating a new reference
owned by the field). The callee-entry RefInc created the parameter's
reference. At scope exit, the parameter's RefDec releases one reference.
The field's reference survives in the returned struct.

Trace for `var w = MakeWrapper(shared, 1)` where shared has rc=N:
1. Callee entry: RefInc shared → rc=N+1 (param owns a ref)
2. `w.Node = shared`: RefInc → rc=N+2 (field owns a ref)
3. `return w`: move (skip dtor for local w)
4. Scope cleanup: RefDec param → rc=N+1 (param's ref released)
5. Caller stores result: one reference from return (the field's)
6. **Result: rc=N+1** — shared has its original refs + one from w.Node. ✓

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

## Current Implementation Status (updated 2026-04-12)

**Working (compiler)**:
- Variable declaration: always copy (axiom 3), no OP_CALL skip ✓
- Variable assignment: save-copy-destroy (axiom 5) ✓
- Struct field assignment: save-copy-destroy ✓
- Slice element assignment: RefDec old, RefInc new ✓
- Function parameter entry/exit: callee-side RefInc/RefDec ✓
- Managed-slice backing RefInc/RefDec on copy/scope-exit ✓
- Function return: always copy for structs (no local-return skip) ✓
- Move optimization for @T/@[]T local returns: skip RefInc + skip scope RefDec ✓
- Scope exit: always dtor structs (no returned-local skip) ✓
- Subslice RefInc on backing refptr ✓
- Temporary RefDec for @T, @[]T, and structs at end of statement ✓
- Multi-return: RefInc extracted @T/@[]T fields from anon struct ✓
- Pointer dereference refcounting (*p = val) ✓
- @[]T → *[]T conversion: temp borrowed, not freed until statement end ✓
- Copy constructors (__copy_X) for structs/arrays with managed fields ✓
- Destructors (__dtor_X) for structs/arrays with managed fields ✓
- Struct field write-through copy/dtor ✓
- .bni/.bn signature mismatch detection ✓
- **Principled slow path**: axioms 1-5 from `design-refcount-axioms.md` ✓

**187/187 conformance on boot-comp, boot-comp-comp, boot-comp-comp-comp.**

**Working (interpreter)**:
- envDefine/envSet RefInc/RefDec for @T, @[]T ✓
- structRefInc/structRefDec for struct/array fields ✓
- cleanupEnvExcept: @T, @[]T, struct scope cleanup ✓
- IsFresh flag for ownership transfer on returns ✓
- VAL_MANAGED_SLICE distinguishes @[]T from *[]T ✓

**Known issues**:

1. **[]char UAF migration incomplete** — the slow path exposes latent UAFs
   where `*[]char` (or `*[]T`) borrows from `@[]char` (or `@[]T`) that gets
   freed by struct dtors. Many sites fixed; 6 boot-comp unit test packages
   still crash from freed-and-reallocated memory (passes with ASan). More
   `@[]T → *[]T` coercion sites to find. See `design-refcount-axioms.md`.

2. **Interpreter @T param over-increment** — tests 228/229 show rc
   increasing by 2 per `wrap(n, tag)` call instead of 1 on boot-comp-int.
   The compiler handles this correctly.

3. **Interpreter multi-return anonymous struct cleanup** — test 227
   xfail'd on boot-comp-int. The anonymous struct from multi-return is
   not cleaned up in the interpreter.

4. ~~Nil assignment to managed-slice struct field causes corruption.~~ **Fixed 2026-04-03.**
   Root cause: field assignment and pointer dereference assignment were missing nil
   coercion — raw `OP_CONST_NIL` (typed `TYP_NIL`) was stored instead of a proper
   `%BnManagedSlice zeroinitializer`. Added nil coercion for slice, managed-slice,
   and managed-ptr types in both paths.

## Changelog

- **2026-04-02**: RefInc on return values before scope cleanup.
- **2026-04-03**: Subslice RefInc. Temporary tracking and cleanup. Pointer
  dereference refcounting.
- **2026-04-10**: Copy constructors and destructors for structs/arrays with
  managed fields. Scope-exit dtors for struct locals. Interpreter
  structRefInc/structRefDec. VAL_MANAGED_SLICE. Interpreter @T cleanup
  fixes (false isRet match, IsFresh on args).
- **2026-04-11**: Sections 2-3 rewritten as normative spec. Added section
  3a (param stored in field). Updated status.
- **2026-04-12**: Principled slow path (axioms 1-5). Always copy on struct
  return/decl/assign, always dtor at scope exit. Struct call results
  registered as temps. Multi-return RefInc for extracted managed fields.
  Systematic `*[]char → @[]char` migration for functions returning
  buf.Bytes/llvmType/etc. Tests 226/227 pass. 187/187 conformance on
  all compiled modes. `--cflag` option for bnc.

### Future (deferred)

- Move optimization for function arguments (skip callee RefInc + temp RefDec)
- Struct temp cleanup (test 226) — see `plan-struct-temp-cleanup.md`
- RefInc on slice element reads and struct field reads of @T (currently
  relies on the container staying alive)
