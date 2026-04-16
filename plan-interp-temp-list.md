# Plan: Interpreter Temp List for @T Value Ownership

## Context

The interpreter's `readFlatValue` for `@T` (managed pointer) needs to
RefInc so the returned Value owns its reference. But every Value that
owns a reference must eventually be destroyed (RefDec'd). The previous
attempts (steps 5-6) failed because:

- Putting temps on a global `interp.Temps` list confused ownership —
  the same `@Value` was both "on the temp list" and "passed around as
  an argument", violating unique ownership.
- The `@Value` type can't represent a borrow — it lives on the heap
  and can escape anywhere.

## Design

### Core invariant

A `@Value` is always **owned** by exactly one of:
1. An env entry (stored in flat memory via envDefine/envSet)
2. A temp list (local `@[]@Value` on the stack)
3. A return value list (retVals in execReturn)

Transfers between these are **moves** — the source gives up ownership.
There is no sharing of `@Value` ownership.

### Borrowing via *Value

Code that only needs to **read** a Value receives `*Value` — a raw
pointer. The borrow is valid as long as the owner (`@Value` on the
temp list or in the env) is alive. `*Value` cannot be stored on the
heap or persisted past the current operation.

### readFlatValue returns *Value

```binate
func readFlatValue(addr *uint8, t @types.Type,
        temps @[]@Value) *Value
```

`readFlatValue` creates a `@Value` (with RefInc for `@T`), registers
the `@Value` on the temp list (which owns it), and returns `*Value`.
The caller gets a borrow guaranteed alive as long as the temp list
lives.

For ALL types (not just `@T`), the `@Value` goes on the temp list.
This is slightly inefficient for POD types (int, bool) but uniform
and correct. The temp list is cleaned at statement end.

Internal callers (envSet old-value read, interpRefDec field walk,
cleanupEntry) pass their own local temp list and clean it at the end
of the operation:

```binate
func cleanupEntry(entry EnvEntry, cleanupTyp @types.Type) {
    var localTemps @[]@Value
    // ... readFlatValue(..., localTemps) ...
    cleanTempList(localTemps)
}
```

### evalExpr returns *Value

```binate
func evalExpr(interp @Interpreter, e @ast.Expr,
        temps @[]@Value) *Value
```

`evalExpr` and all its callees (evalIdent, evalBinary, evalUnary,
evalCall, evalIndex, evalSlice, evalSelector, etc.) take a `temps`
parameter and return `*Value`.

When `evalIdent` looks up a variable via `envGet`, it calls
`readFlatValue(entry.Addr, entry.Typ, temps)` — the `@Value` goes on
temps, the `*Value` borrow is returned.

### Moving off temps (ownership transfer)

When a Value needs to be stored (envDefine, envSet, execReturn), the
`@Value` is **moved** off the temp list:

```binate
// Find the @Value on the temp list that v borrows from,
// remove it, and return the @Value (caller now owns it).
func moveFromTemps(temps @[]@Value, v *Value) @Value
```

The moved `@Value` is then passed to `envDefine` (which writes it to
flat memory and takes ownership) or to `retVals` (which transfers
to the caller).

### Statement lifecycle

```
execBlock(interp, block):
    for each stmt in block.Stmts:
        var temps @[]@Value
        execStmt(interp, stmt, temps)
        cleanTempList(temps)    // destroy remaining temps
```

Each statement gets a fresh temp list. Everything created during
expression evaluation is either moved (stored/returned) or destroyed.

### Function call lifecycle

```
callFunc(interp, fn, args []*Value, callerTemps @[]@Value):
    // Move each arg's @Value off caller's temps into callee's env
    for each arg, param:
        var owned @Value = moveFromTemps(callerTemps, arg)
        envDefine(callee_env, param.Name, owned)

    // Execute body — each statement has its own temps
    execBlock(interp, fn.Body)

    // Cleanup callee scope
    cleanupEnvExcept(callee_env, retVals)

    // Return value: the @Value from retVals is added to
    // the caller's temps (ownership transfers to caller)
    addToTemps(callerTemps, retVals[0])
    return &retVals[0]  // borrow from caller's temps
```

### What changes

1. **readFlatValue**: takes `temps @[]@Value`, returns `*Value`
2. **evalExpr and all callees**: take `temps`, return `*Value`
3. **All evalExpr callers**: pass temps, receive `*Value`
4. **envGet**: takes temps (or wraps readFlatValue with temps)
5. **envDefine/envSet callers**: move @Value off temps before storing
6. **execReturn**: move @Value off temps into retVals
7. **callFunc**: move args off caller temps into callee env
8. **execBlock**: create temps per statement, clean at end
9. **Internal callers** (envSet, interpRefDec, cleanupEntry): use
   local temp lists, clean immediately

### What doesn't change

- `writeFlatValue`
- `cleanValue`, `interpRefDec`, `structRefInc/Dec`
- `cleanupEntry` logic (just passes local temps to readFlatValue)
- Conformance tests, unit tests (same observable behavior)

### Implementation order

**Phase A: Signature changes (mechanical)**

1. Change `readFlatValue` signature: add temps, return *Value
2. Change `evalExpr` and all eval* signatures: add temps, return *Value
3. Change all callers to pass temps and use *Value
4. At this point, tests should still pass (temps are created but
   never cleaned — same leak behavior as today)

**Phase B: Ownership transfer**

5. Add `moveFromTemps` helper
6. At envDefine/envSet sites: move @Value off temps
7. At execReturn: move @Value off temps
8. At callFunc param binding: move args off caller temps

**Phase C: Temp cleanup**

9. In execBlock: create temps per statement, clean after
10. In internal callers: use local temps, clean after
11. Remove old infrastructure (Interpreter.Temps, consumeTemp,
    registerTemp, cleanTemps, step 5 cleanValue calls)
12. Remove refcount xfails

**Phase D: Validation**

13. Run conformance + unit tests across all modes
14. Verify exact refcounts match expected values

### Risk

Large refactor (~20 function signatures change). Every evalExpr call
site needs updating. But the changes are mechanical — add a parameter,
change return type. The semantic changes (move, cleanup) are
concentrated in a few places (envDefine callers, execReturn, callFunc,
execBlock).

The bootstrap interpreter (Go) will need corresponding changes to
accept the new signatures, but since it uses Go GC, the temps
parameter can be ignored (pass nil, never clean).
