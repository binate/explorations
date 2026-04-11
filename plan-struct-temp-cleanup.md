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

## Attempts (2026-04-11)

### Attempt 1: always register + always copy + never consumeTemp

**Idea**: register all struct call results as temps. Always call copy
constructor at consumption sites (var decl, var assign, arg). Never
consumeTemp. Temp cleanup at end of statement dtors remaining temps.

**Result**: test 226 passes on boot-comp. But boot-comp-comp breaks
catastrophically (126/187 failures). The gen1 compiler's own code has
hundreds of struct-returning calls (`buf.WriteStr`, `buf.WriteByte`,
etc.) that all get temp-registered. The temp cleanup dtor fires on
every unconsumed struct temp at end of statement, prematurely freeing
CharBuf backings that are still live.

**Root cause**: the "always copy" rule means `var cb = buf.New()` calls
copy (RefInc) + scope dtor will RefDec + temp cleanup will also dtor.
That's +1 -1 -1 = -1 from the return's +1, which is correct in
isolation. But the gen1 compiler processes many statements, and the
cumulative effect of temp-registering every CharBuf return causes the
LLVM IR output to include dtor calls that corrupt the compiler's own
state mid-codegen.

### Attempt 2: register + consumeTemp for var decl + copy for others

**Idea**: consumeTemp at var decl (skip copy, as before). Keep copy for
var assign and args. Temp cleanup handles inline uses.

**Result**: same boot-comp-comp failure. Even with consumeTemp for var
decl, the temp registration + dtor infrastructure generates dtor calls
for struct temps in other patterns (e.g., struct returned from one call
and immediately passed to another). The gen1 compiler uses many such
patterns.

### Attempt 3: register + consumeTemp at var decl/assign/multi-return

**Idea**: consume at all assignment-like sites. Only temp-cleanup for
truly inline uses (field access on call result, etc.).

**Result**: boot-comp passes but boot-comp-comp still breaks. The
problem is fundamental: registering ALL struct call results as temps
means every `buf.WriteStr` call in the compiler generates temp cleanup
code. Even if consumed, the temp infrastructure changes the generated
IR enough to cause issues in the gen1 compiler.

## Analysis

The core difficulty is that the temp registration approach works at the
**statement** level, but struct-returning functions are used pervasively
in the compiler's own code at the **expression** level. Every `buf.*`
call returns a CharBuf struct with managed fields. Registering all of
them as temps and ensuring every one is properly consumed or cleaned up
requires tracking at every consumption site, which is fragile and
incomplete.

The approach works for `@T` and `@[]T` because those are scalar values
— the temp is the managed pointer/slice itself, and cleanup is a single
RefDec. For structs, cleanup requires calling a dtor function (which
walks fields), and the dtor generation + calling infrastructure adds
significant complexity to the generated code.

## Possible approaches (not yet tried)

1. **Expression-level tracking**: instead of registering at the statement
   level, track struct temps at the expression level. When a struct call
   result is consumed (by assignment, arg, or field access), emit the
   cleanup immediately after the consumption site, not at end of statement.
   This is more precise but requires rethinking the temp tracking model.

2. **Only register for specific patterns**: instead of registering ALL
   struct call results, only register when the result is used for field
   access (e.g., `makeOuter(n, 42).Tag`). For assignment and arg patterns,
   the existing copy/dtor infrastructure handles refcounting. This would
   fix the specific test 226 pattern without affecting the gen1 compiler.

3. **Callee-side struct cleanup**: make the callee responsible for cleaning
   up struct return values that the caller doesn't consume. This would
   require a protocol for the caller to signal whether it consumed the
   return. Complex but potentially more robust.

4. **Accept the leak**: the leak is bounded (one refcount per inline struct
   use). It's not a correctness issue (no UAF), just a memory leak for
   code that uses struct-returning functions inline. Most real code assigns
   struct returns to variables. Low priority relative to other work.

**Recommendation**: option 2 is the most pragmatic — it fixes the actual
bug (test 226) without touching the gen1 compiler's CharBuf patterns.
Option 1 is the most principled but requires significant refactoring of
the temp tracking model.
