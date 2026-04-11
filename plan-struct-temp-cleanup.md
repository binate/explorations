# Plan: Struct Temp Cleanup (Test 226)

## Problem

When a function returns a struct with managed fields and the result is used
inline (not assigned to a variable), the managed fields leak. Example:

```binate
var tag int = getTag(makeOuter(n, 42))
```

`makeOuter` returns an `Outer` struct containing `Node @Inner`. The struct
is passed to `getTag` as an argument. After `getTag` returns, the temporary
`Outer`'s managed field `Node` is never RefDec'd. Each call leaks one
reference.

**Conformance test**: 226 (xfail'd on all modes).

## Refcount Trace

Consider `@Inner` with initial rc=1 (held by `main`'s local variable `n`).

### Inside `makeOuter(n, 42)`:
- Param `n @Inner`: arg copy fires → RefInc → rc=2
- `o.Node = n`: field assignment → RefInc → rc=3
- `return o`: local return, skip dtor on `o`. Param `n` is RefDec'd by
  `emitDecForManagedLocals` → rc=2
- **Returned struct has Node with rc=2** (one for main's `n`, one for the
  returned struct's ownership)

### At the call site `getTag(makeOuter(n, 42))`:
- `makeOuter` call result: struct with Node rc=2. NOT registered as temp.
- Arg copy to `getTag`: emitStructCopy → RefInc → rc=3
- Inside `getTag`: uses `o.Tag` (reads int), returns int.
- `getTag` scope exit: param dtor → RefDec Node → rc=2
- End of statement: nothing happens to the `makeOuter` temp
- **Node rc=2 instead of 1 → leak**

### What should happen:
- After the statement completes, the `makeOuter` temp should be cleaned
  up (dtor → RefDec Node → rc=1), balancing the +1 from `makeOuter`'s
  ownership.

## Design

Register struct call results as temporaries. Clean them up at end of
statement via `emitTempCleanup`. Consume them when ownership transfers to
a variable (var decl, var assign, multi-return destructuring).

### The refcount arithmetic

For a struct-returning function, the returned struct's managed fields have
rc = original + 1 (the +1 from the callee's field assignment, not cancelled
by the callee's dtor due to local-return ownership transfer).

There are three consumption paths:

1. **Var decl**: `var x T = f()`
   - Store to x. No copy (OP_CALL skip). consumeTemp.
   - Scope dtor: -1. Balances the +1 from return.
   - Net: +1 (return) -1 (scope dtor) = 0. ✓

2. **Var assign**: `x = f()`
   - Save old x. Store new. Skip copy (OP_CALL). Dtor old. consumeTemp.
   - Scope dtor: -1. Balances the +1 from return.
   - Net: +1 (return) -1 (scope dtor) = 0. ✓

3. **Inline use**: `g(f())`
   - Arg copy: +1. Callee dtor: -1. These balance.
   - Temp cleanup at end of statement: -1. Balances the +1 from return.
   - Net: +1 (return) +1 (copy) -1 (callee dtor) -1 (temp cleanup) = 0. ✓

**Critical rule**: arg copy must NOT be skipped for OP_CALL struct args.
The copy provides the +1 that balances the callee's param dtor. The temp
cleanup provides the -1 that balances the original return's +1.

### Where to consume

- `gen_stmt.bn` var decl: already skips copy for OP_CALL → add consumeTemp
- `gen_control.bn` var assign: skip copy for OP_CALL → add consumeTemp
- `gen_control.bn` multi-return destructuring: add consumeTemp

### Where NOT to consume

- Function args: the temp must survive until end of statement. The arg
  copy handles the callee's dtor balance. The temp cleanup handles the
  original return balance.

## Risk: Value-Type API Patterns (CharBuf)

The compiler itself uses `buf.CharBuf` (a struct with `Data @[]char`)
extensively with a value-type API:

```binate
cb = buf.WriteStr(cb, "hello")
```

This is a var assign where rhs is an OP_CALL returning a struct with
managed fields. Trace:

- `buf.WriteStr(cb, "hello")`: takes `cb` by value (arg copy RefInc's
  Data backing). May grow the backing (new alloc) or keep it. Returns
  updated CharBuf by value. Param dtor RefDec's the param's Data.
- At call site: old `cb` is dtor'd (RefDec old Data). New `cb` stored.
  If we skip copy (OP_CALL) + consumeTemp, scope dtor will be the only
  RefDec of the new Data.

**Potential issue**: if WriteStr returns a CharBuf that shares the same
`Data` backing as the input, the old dtor RefDec's the backing, and the
scope dtor later RefDec's again. If the only RefInc was WriteStr's
internal field assignment, that's two RefDec's for one RefInc → UAF.

This depends on how WriteStr manages refcounting internally. If WriteStr
returns a struct where `Data` was RefInc'd for the return value, it should
be correct. If WriteStr's return just copies the field values without
RefInc (because local return skips dtor), then the rc is already -1
short.

**This needs careful testing with the compiler's own code** (boot-comp-comp
mode). The previous attempt to implement this broke boot-comp-comp because
of exactly this pattern.

## Implementation Steps

1. Register struct call results as temps in `genCall` (`gen_expr.bn`)
2. Add struct temp handling in `emitTempCleanup` and `emitTempCleanupSince`
3. consumeTemp in var decl for OP_CALL struct results (`gen_stmt.bn`)
4. Skip copy + consumeTemp in var assign for OP_CALL struct results (`gen_control.bn`)
5. consumeTemp in multi-return destructuring (`gen_control.bn`)
6. Verify conformance test 226 passes
7. **Critical**: verify boot-comp-comp still passes (all 179 tests)
8. If boot-comp-comp fails, investigate which CharBuf/struct patterns
   break and whether the refcounting inside WriteStr/WriteByte is correct
   for the new ownership model

## Fallback

If the value-type API pattern (CharBuf) is fundamentally incompatible with
struct temp cleanup, options are:

- **Opt-out by type**: only register struct temps for types where the
  struct is NOT used in value-type API patterns. This is fragile.
- **Copy elision**: don't temp-cleanup if the result is consumed in the
  same statement (by any path — assignment, arg, etc.). Only temp-cleanup
  if the result is used for field access or other non-consuming operations.
  This requires tracking whether a temp was "consumed" vs "borrowed".
- **Accept the leak for now**: the leak is bounded (one refcount per inline
  struct use). It's not a correctness issue (no UAF), just a memory leak.
  Low priority relative to other work.
