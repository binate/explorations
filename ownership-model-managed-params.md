# Ownership Model for @T Parameters

## The Rule

When calling a function with an `@T` parameter, the parameter must own its
own reference for the duration of the function body. At scope exit, that
reference is released (RefDec). The two ways to establish this:

1. **Transfer from temporary**: If the argument is a fresh allocation (e.g.,
   `f(make(Node))`), the allocation starts at rc=1. That reference becomes
   the parameter's. No additional RefInc needed. At scope exit, RefDec
   brings it to rc=0 (freed, unless stored elsewhere).

2. **RefInc from shared value**: If the argument is a live variable (e.g.,
   `f(n)` where `n` continues to exist), a RefInc creates a new reference
   for the parameter. At scope exit, RefDec releases it, leaving the
   caller's reference intact.

The key invariant: **at function entry, the parameter variable owns exactly
one reference. At function exit (unless returned), that reference is
released.**

### Move optimization

For temporaries, a "move" is possible: the temp's reference transfers to
the parameter, and the temp is not RefDec'd (it's already "dead"). This
avoids a RefInc+RefDec pair. Whether this is a language-level guarantee or
an optimization is a design question — see "Open Question" below.

## What the Compiler Does

The compiler implements **callee-side RefInc**:

### Caller (gen_expr.bn `genCall`):
- Does NOT RefInc `@T` arguments before the call.
- Fresh temps (`make`, `box`) are registered in the temps list. The temp
  cleanup at end of statement will RefDec them — but this happens AFTER
  the call returns, so the callee can use the value safely during the call.

### Callee entry (gen_stmt.bn `genFuncBody`, lines 102-107):
- After storing params to allocas, emits `EmitRefcountInc` for each `@T`
  parameter. This is the callee "claiming" its reference.

### Callee exit (gen_util_refcount.bn `emitDecForManagedLocals`):
- RefDec's all `@T` variables (params and locals), skipping returned ones.
- This releases the reference claimed at entry.

### Caller post-call:
- `emitTempCleanup` at end of statement RefDec's any remaining temps.
  For a fresh `make(Node)` passed as arg: callee RefInc'd on entry (rc=2),
  callee RefDec'd on exit (rc=1), temp cleanup RefDec's (rc=0, freed unless
  stored in a field inside the callee).

### Net for `f(n)` where n is a local variable (rc=1):
1. Caller: no RefInc. rc=1.
2. Callee entry: RefInc → rc=2.
3. (Inside f, field assign `t.Elem = n` → RefInc → rc=3)
4. Callee exit: RefDec param → rc=2.
5. After call: n still rc=2 (caller's ref + field's ref). ✓

### Net for `f(make(Node))`:
1. make(Node) → rc=1. Registered as temp.
2. Callee entry: RefInc → rc=2.
3. (Inside f, field assign `t.Elem = n` → RefInc → rc=3)
4. Callee exit: RefDec param → rc=2.
5. Temp cleanup: RefDec → rc=1 (only field's ref remains). ✓

### Note on move optimization:
The compiler does NOT currently implement move for temps. The temp is
RefInc'd at callee entry AND RefDec'd at temp cleanup. A move would skip
both, producing the same result (rc stays at 1 after make, field assign
brings it to 2, callee exit would NOT RefDec since moved). This is a
potential optimization but is not currently done.

## What the Interpreter Does

The interpreter implements **envDefine-side RefInc**:

### Caller (`callFunc` in call.bn, lines 109-116):
- `copyValue(args[i])` — for `@T`, returns the same Value.
- `argCopy.IsFresh = false` — forces RefInc in envDefine.
- `envDefine(interp.Env, paramName, argCopy)` — allocates flat storage,
  writes the pointer, then RefInc's (because `!IsFresh`).

### Callee exit (`cleanupEnvExcept` in helpers.bn):
- For `@T` entries not in the except list: reads pointer, calls
  `interpRefDec` → RefDec.

### Discrepancy: boot-comp-int test results

Tests 228/229 show rc=3 after `wrap(n, 1)` where expected rc=2.

Expected trace:
1. n = make(Node) → rc=1
2. callFunc: envDefine param n, IsFresh=false → RefInc → rc=2
3. w.Node = n: field assign → RefInc → rc=3
4. cleanupEnvExcept: RefDec param n → rc=2
5. Return w with IsFresh=true → envDefine skips RefInc for struct

Actual: rc=3, meaning step 4 may not be firing, or there's an extra
RefInc somewhere. **This needs investigation.** The `cleanupEnvExcept`
`isRet` check was fixed (commit 965c459), but there may be another
path that prevents the RefDec from firing for `@T` params.

## Open Question: Move Semantics as Language Guarantee

The move optimization (transferring a temp's ref to a param without
RefInc+RefDec) is observable because refcounts are observable via
`rt.Refcount`. Two options:

1. **Move is an optimization, not a guarantee**: The language spec says
   params get their own reference. Whether this is achieved via RefInc or
   move is implementation-defined. Code should not rely on `rt.Refcount`
   producing specific values for temps-as-args.

2. **Move is a language guarantee**: Passing a temp to a function transfers
   ownership. The temp is "consumed" — no further RefDec. The param starts
   with rc=1 (not rc=2). This is simpler mentally but constrains the
   implementation and makes refcount values part of the spec.

**Recommendation**: Option 1 (optimization, not guarantee). Refcounts are
a debugging/introspection tool, not a semantic contract. The observable
behavior (when objects are freed) should be the same either way — the only
difference is the intermediate rc values during a function call.
