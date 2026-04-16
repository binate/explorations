# Plan: Interpreter Temp List for @T Value Ownership

## Context

The interpreter's `readFlatValue` for `@T` (managed pointer) needs to
RefInc so the returned `@Value` owns its reference. But every `@Value`
that owns a reference must eventually be destroyed (RefDec'd). The
previous attempts (steps 5-6) failed because:

- Putting temps on a global `interp.Temps` list confused ownership —
  the same `@Value` was both "on the temp list" and "passed around as
  an argument", violating unique ownership.
- `cleanValue` at envDefine sites was a workaround that broke when
  IsFresh semantics conflicted with the temp lifecycle.

## Design

### Core principle: move semantics with clear owner

A `@Value` containing a managed pointer is **always owned** by exactly
one of:
1. An env entry (stored in flat memory via envDefine/envSet)
2. A temp list (local to the current statement evaluation)
3. A return value list (retVals in execReturn)

Transfers between these are **moves** — the source gives up ownership.

### Temp list

The temp list is a **local variable** (`@[]@Value`), not a field on
the Interpreter. It's created at the start of statement evaluation and
passed down through `evalExpr` and its callees. This makes lifetimes
explicit — the temp list lives on the stack frame of the statement
executor.

```binate
// In execStmt / execBlock:
var temps @[]@Value
// ... pass &temps or temps to evalExpr etc. ...
// At statement end:
cleanTempList(temps)
```

### API changes

#### evalExpr: takes temps parameter

```binate
func evalExpr(interp @Interpreter, e @ast.Expr,
        temps @[]@Value) @Value
```

Every `evalExpr` call passes the temp list. When `evalExpr` creates a
`@Value` via `readFlatValue` (for `@T`), it adds the Value to temps.

#### Borrowing: *Value

Functions that only READ a Value (don't store it) should receive
`*Value` — a raw pointer borrow. The borrow is valid as long as the
temp list (or env entry) that owns the `@Value` is alive.

However, changing every function to take `*Value` is a massive refactor.
As an intermediate step, we can pass `@Value` but document that
the callee must NOT retain it — it's a conceptual borrow even though
the type system doesn't enforce it.

**Pragmatic compromise**: keep `@Value` as the parameter type for now.
The ownership discipline is enforced by convention:
- `evalExpr` returns `@Value` owned by the temp list
- Callers that store (envDefine, assignTo) **move** the Value off temps
- Callers that just read (print, comparison, field access) use the
  Value and let it stay on temps
- At statement end, temps are cleaned

#### Moving off temps

```binate
func moveTempValue(temps @[]@Value, v @Value) @Value
```

Removes `v` from the temp list and returns it. The caller now owns it.
Used by:
- `envDefine` / `envSet` — storing in env
- `execReturn` — transferring to retVals
- `callFunc` param binding — transferring to callee's env

#### readFlatValue

Stays as-is but RefIncs for `@T`. The caller (evalIdent, evalSelector,
evalIndex) adds the result to the temp list.

Actually — `readFlatValue` is also called by internal code (envSet old
value read, interpRefDec field walk, cleanupEntry). These internal
callers should NOT add to temps. So the RefInc should happen at the
evalIdent/evalSelector/evalIndex level, not in readFlatValue itself.

**Decision**: `readFlatValue` does NOT RefInc. It returns a borrow.
`evalIdent`, `evalSelector`, `evalIndex` call readFlatValue, then
RefInc the result and add to temps. Internal callers (envSet,
interpRefDec, cleanupEntry) use readFlatValue directly (borrow, no
temp registration).

### Statement lifecycle

```
execStmt(interp, stmt):
    var temps @[]@Value    // empty temp list
    
    // Evaluate expressions — fills temps
    evaluate(interp, stmt, temps)
    
    // Clean remaining temps
    for each v in temps:
        cleanValue(v)       // RefDec the managed pointer
    temps = nil
```

### Function call lifecycle

```
callFunc(interp, fn, args):
    // args are @Values from the caller's temp list
    // Move each arg off caller's temps into callee's env
    for each arg in args:
        envDefine(callee_env, param_name, arg)
        // The arg is now owned by the callee's env
        // The caller's temp list no longer has it
    
    // Execute function body — each statement has its own temps
    for each stmt in body:
        var temps @[]@Value
        evaluate(interp, stmt, temps)
        cleanTempList(temps)
    
    // Cleanup callee scope
    cleanupEnvExcept(callee_env, retVals)
    
    // Return value ownership transfers to caller
    return retVals[0]  // caller adds to its temps
```

### What changes

1. **evalExpr signature**: add `temps @[]@Value` parameter
2. **All evalExpr callers**: pass temps
3. **evalIdent, evalSelector, evalIndex**: RefInc + add to temps for @T
4. **envDefine/envSet callers**: move Value off temps before storing
5. **execReturn**: move return Value off temps
6. **callFunc**: move args off caller temps into callee env
7. **execBlock**: create temps per statement, clean at end
8. **readFlatValue**: NO RefInc (stays as borrow for internal use)

### What doesn't change

- `readFlatValue` signature (still returns @Value, but as a borrow)
- `writeFlatValue`
- `cleanValue`, `interpRefDec`, `structRefInc/Dec`
- `cleanupEntry`, `cleanupEnvExcept`
- Conformance tests, unit tests

### Implementation order

1. Add `temps @[]@Value` parameter to `evalExpr` and all its callees
   (evalIdent, evalBinary, evalUnary, evalCall, evalIndex, evalSlice,
   evalSelector, evalCompositeLit, evalStructLit, evalCast, evalMake,
   evalMakeSlice, etc.)
2. Update all `evalExpr` callers to pass temps
3. In evalIdent/evalSelector/evalIndex: RefInc for @T, add to temps
4. Add `moveTempValue` helper
5. At envDefine/envSet call sites: move Value off temps
6. At execReturn: move Value off temps
7. In execBlock: create temps, pass to execStmt, clean after
8. In callFunc: handle arg ownership transfer
9. Remove readFlatValue RefInc (if still present)
10. Remove step 5/6 infrastructure (consumeTemp, registerTemp,
    cleanTemps, Interpreter.Temps field)
11. Remove refcount xfails
12. Test: conformance + unit tests

### Risk

This is a large refactor touching most of pkg/interp. The `evalExpr`
signature change cascades to ~20 functions. Each one needs the temps
parameter threaded through.

The bootstrap interpreter (Go) doesn't need this — it uses Go GC.
But the self-hosted interpreter code must be compatible with both
bootstrap and compiled execution. The `temps` parameter is just a
`@[]@Value` — no bootstrap-incompatible features.
