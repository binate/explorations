# Plan: Interpreter Refcounting Axiom Audit

## Problem

The self-hosted interpreter (`pkg/interp`) does not follow the refcounting
axioms defined in `design-refcount-axioms.md`. This causes use-after-free
crashes when interpreting code that passes structs with managed fields
by value — specifically when the struct is returned from one function and
passed inline to another.

**Reproducer**: `checkLit(lexer.Next(l), "foo")` where `lexer.Next`
returns a `token.Token` (struct with `Lit @[]char` and `Pos.File @[]char`).
Crashes in `msliceRefIncBacking` trying to read a freed `@[]char` backing.

**Scope**: 11 boot-comp-int unit test packages fail (pkg/ir, pkg/types,
pkg/codegen, pkg/lint, pkg/lexer, pkg/parser, pkg/asm/*). All failures
are from the same root cause: the interpreter's refcounting for struct
values with managed fields doesn't properly handle returns and scope
cleanup. 183/183 conformance tests pass (simpler patterns that don't
trigger the bug).

## Root Cause: Struct Return + Scope Cleanup

The crash pattern, traced via libgmalloc + lldb:

1. Interpreted `lexer.Next(l)` executes. Inside, the lexer builds a
   `Token` struct with `Lit @[]char` (from `buf.CopyStr`).
2. `return tok` — the interpreter's `execReturn` copies the Token value
   and marks it `IsFresh = true`.
3. `cleanupEnvExcept` runs on `Next`'s scope. For struct locals, it calls
   `structRefDec` which walks fields and RefDec's `Lit.backing`.
4. The returned Token shares the same `Lit` backing as the local (because
   `copyValue` memcpys the bytes without RefInc'ing managed fields).
5. The caller receives a Token with `Lit` pointing to freed memory.
6. When the Token is passed to `checkLit`, the interpreter tries to
   `msliceRefIncBacking` on the freed backing → crash.

## Analysis Against Axioms

The compiler follows axioms 1-5 correctly for struct values. The
interpreter violates several:

### Axiom 3 (Copy → copy constructor) — VIOLATED

**`copyValue` for structs**: memcpys bytes without calling `structRefInc`.
The copy is a bitwise copy that doesn't adjust refcounts on managed fields.

The compiler's equivalent: `emitStructCopy` calls `__copy_X` which walks
fields and RefInc's all managed pointers/slices. The interpreter's
`copyValue` skips this entirely.

**Where `copyValue` is called**:
- `callFunc` line 111: copying args for function params
- Potentially other paths that copy struct values

**Fix**: after `copyValue` for structs, call `structRefInc` on the copy's
`RawAddr`. Or better: integrate the RefInc into `copyValue` itself.

Note: `envDefine` already calls `structRefInc` when `!IsFresh`. But
`callFunc` clears `IsFresh` before calling `envDefine`, so the RefInc
should fire. The issue might be in the interaction with `cleanupEnvExcept`
— see below.

### Axiom 2 (rc=0 → destructor) — PARTIALLY VIOLATED

**`cleanupEnvExcept` for structs**: calls `structRefDec` on struct locals.
This is correct in principle (the struct's managed fields should be
RefDec'd at scope exit). But when the struct is returned (ownership
transfer), the RefDec cancels out the field assignment's RefInc, leaving
the return value with under-counted managed fields.

The compiler's equivalent: `emitDecForManagedLocals` skips struct dtor
for returned locals? No — the slow path ALWAYS dtors. But it also always
copies on return. The copy + dtor cancel, and the return's copy provides
the +1 for the caller. The interpreter's `execReturn` doesn't copy on
return for structs — it just marks `IsFresh = true`.

### Axiom 5 (Assignment = save-copy-destroy) — VIOLATED

**`envSet` for structs**: calls `structRefInc` on the new value and
`structRefDec` on the old value. But the ordering might be wrong — if
both old and new share managed fields, the RefDec could free something
the RefInc needs.

**`assignTo` for struct fields**: the field assignment path at
exec.bn:270-276 does `msliceRefIncBacking` on the new value then
`msliceRefDecBacking` on the old. This handles TOP-LEVEL `@[]T` fields
but NOT nested structs (same issue the compiler had before
`emitStructElemRefcount` was replaced with `emitStructCopy/emitStructDtor`).

## Specific Fixes Needed

### Fix 1: Struct return — copy before scope cleanup

In `execReturn`, when returning a struct with managed fields, call
`structRefInc` on the return value BEFORE `cleanupEnvExcept` runs. This
is the interpreter equivalent of the compiler's "always copy on return"
(axiom 3).

```
// In execReturn, after setting vals[i]:
if vals[i].Kind == VAL_STRUCT && vals[i].Typ != nil &&
        types.NeedsDestruction(vals[i].Typ) {
    structRefInc(vals[i].RawAddr, vals[i].Typ)
}
```

Then `cleanupEnvExcept`'s `structRefDec` on the local balances this.
The caller receives a struct with correct refcounts (+1 from the
return copy).

### Fix 2: Struct arg passing — already works?

`callFunc` calls `copyValue(args[i])` which memcpys, then clears
`IsFresh`, then `envDefine` calls `structRefInc`. The `structRefInc`
on `entry.Addr` should RefInc the managed fields in the flat copy.
At scope exit, `cleanupEnvExcept` calls `structRefDec`. These should
balance.

**BUT**: `copyValue` creates a new `RawAddr` via `c_malloc` + `memcpy`.
Then `envDefine` creates ANOTHER allocation via `allocFlat` and copies
into it via `writeFlatValue`. The `structRefInc` runs on `entry.Addr`
(the `allocFlat` copy), not on `copyValue`'s `RawAddr`. The `copyValue`
allocation is leaked (never freed). This is a memory leak but not a UAF.

**Better fix**: skip `copyValue` for structs and just pass the Value
directly. `envDefine` will `allocFlat` + `writeFlatValue` + `structRefInc`
which handles everything. The `copyValue` memcpy is redundant.

### Fix 3: Struct field assignment — use structRefInc/structRefDec

In `assignTo` (exec.bn:260-276), the struct field assignment path handles
`@T` and `@[]T` fields individually but doesn't handle nested structs.
Replace with `structRefInc` on the new value and `structRefDec` on the
old field, similar to what the compiler does with `emitStructCopy` /
`emitStructDtor`.

### Fix 4: Struct scope cleanup for returned values

Currently `cleanupEnvExcept` checks `except` to skip returned values.
For `@T`, it compares pointer values. For structs, it compares `RawAddr`.
With fix 1 (copy on return), the `except` skip is no longer needed for
structs — the copy + dtor balance. But the `except` check should still
work correctly if it stays (the returned value's `RawAddr` matches the
env entry's `Addr`).

Actually, with fix 1, the returned struct IS RefInc'd. The scope cleanup
should still run `structRefDec` on the local (not skip it). So the
`except` check for structs should be removed or made to not skip struct
cleanup. This matches the compiler's behavior (always dtor structs, even
returned ones).

### Fix 5: `IsFresh` for struct returns

After fix 1, struct return values have `IsFresh = true` (from
`execReturn`) AND their managed fields have been RefInc'd (from the
explicit `structRefInc`). At the call site, `envDefine` sees
`IsFresh = true` → skips `structRefInc`. But the return's RefInc is
the caller's ownership ref. Scope exit will `structRefDec`. These
balance: return RefInc +1, scope dtor -1 = net 0. Correct.

If `IsFresh` is false (cleared by `callFunc` for args), `envDefine`
calls `structRefInc` → +1. Scope dtor → -1. Balanced.

## Implementation Order

1. **Fix 1**: `execReturn` — `structRefInc` on returned struct values
2. **Fix 4**: `cleanupEnvExcept` — don't skip `structRefDec` for returned
   struct locals (match compiler's always-dtor behavior)
3. Test: verify `test_lexer3.bn` reproducer no longer crashes
4. **Fix 3**: `assignTo` — use `structRefInc`/`structRefDec` for nested
   struct field assignment
5. **Fix 2**: evaluate whether `copyValue` for structs is needed
6. Run boot-comp-int unit tests, fix remaining failures
7. Run conformance tests to verify no regressions

## Relationship to Compiler Fixes

The compiler went through the same journey:

| Issue | Compiler Fix | Interpreter Equivalent |
|-------|-------------|----------------------|
| Struct copy missing RefInc | `__copy_X` functions | `structRefInc` (partially done) |
| Struct scope-exit missing dtor | `emitDecForManagedLocals` + `__dtor_X` | `cleanupEnvExcept` + `structRefDec` (partially done) |
| Struct return move leaks | Always copy on return (slow path) | **Needs fix 1** |
| Struct return dtor skipped | Always dtor returned structs | **Needs fix 4** |
| Nested struct field assign | `emitStructCopy`/`emitStructDtor` | **Needs fix 3** |
| Slice element nested fields | Replace `emitStructElemRefcount` | Not yet addressed |

The interpreter already has `structRefInc`/`structRefDec` helpers. The
main missing pieces are fix 1 (copy on return) and fix 4 (always dtor).
