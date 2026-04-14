# Plan: Interpreter Value Ownership Model

## Problem

The interpreter's `Value` struct holds managed data (via `RawAddr`, `Typ`)
but its lifecycle is not explicitly managed. When a `@Value` dies (rc=0),
nothing cleans up the managed data it contains. This causes:

1. **Leaks**: `popEnv` discards sub-scope locals without cleanup. @T locals
   in for/if bodies leak (test 247).
2. **UAF**: A `@Type` object referenced by a Value gets freed and reused
   while another Value still holds a raw pointer to it, causing
   TYP_SLICE/TYP_MANAGED_SLICE confusion (boot-comp-int crash).

## Design Principles

1. **Values by pointer only.** Values are never copied by value. They are
   passed as `@Value` (owned) or `*Value` (borrowed). No `copyValue`.

2. **Unique ownership.** At any point in time, exactly one `@Value`
   reference owns a Value. The refcount on the `@Value` allocation should
   be 1 (modulo transient bumps from function call mechanics). In effect:
   `unique_ptr` semantics.

3. **Explicit destruction.** Before a Value is destroyed (rc reaches 0),
   the interpreter explicitly calls the destructor for the Value's
   contents based on the known type. The protocol:
   a. Transfer ownership to a single local `@Value` (nil out any other
      managed-pointer copies).
   b. Assert rc == 1.
   c. Call the content destructor (walk managed fields per `Typ`, RefDec
      each).
   d. Mark the Value as clean (set `IsClean = true` or similar).
   e. Nil the local `@Value` → rc=0 → Value shell freed.

4. **Borrowing is via raw pointer.** When a function needs to READ a
   Value without taking ownership (e.g., print, type-check, field read),
   it receives `*Value` or accesses the Value through its owner's
   `@Value`. No RefInc on the `@Value` needed — the caller guarantees
   the Value is alive during the call.

## Changes Required

### 1. Value struct: add IsClean field

```binate
type Value struct {
    Kind      int
    Typ       @types.Type
    RawAddr   *uint8
    IsClean   bool
    // ... other fields ...
}
```

`IsClean` is set to true after the interpreter has explicitly called the
content destructor. Defaults to false. Only needs to be true before the
Value's `@Value` reference is released.

### 2. Content destructor function

```binate
func cleanValue(v @Value) {
    if v == nil || v.IsClean { return }
    var t @types.Type = v.Typ
    if t == nil { v.IsClean = true; return }
    t = resolveUnderlying(t)
    if t.Kind == types.TYP_MANAGED_PTR && v.RawAddr != nil {
        interpRefDec(v)  // RefDec the managed pointer
    }
    if t.Kind == types.TYP_MANAGED_SLICE && v.RawAddr != nil {
        msliceRefDecBacking(v.RawAddr)
    }
    if (t.Kind == types.TYP_STRUCT || t.Kind == types.TYP_ARRAY) &&
            types.NeedsDestruction(t) && v.RawAddr != nil {
        structRefDec(v.RawAddr, t)
    }
    v.RawAddr = nil
    v.IsClean = true
}
```

### 3. Scope cleanup: cleanValue before release

**cleanupEnvExcept**: For each env entry being cleaned up, call
`cleanValue` on the Value before releasing it. For flat entries, the
Value needs to be reconstructed from the flat data and type.

**popEnv**: Must also clean up. Currently does nothing — needs to call
`cleanupEnvExcept` with an empty except list.

### 4. Function call: no more copyValue for @T

Currently `callFunc` calls `copyValue(args[i])` which returns the Value
as-is for `@T` (sharing the `@Value` reference). Instead:

- For `@T` args: the caller's `@Value` is borrowed during the call.
  `envDefine` for the parameter creates a NEW `@Value` with a fresh
  `RawAddr` pointing to the same managed allocation (RefInc'd). At scope
  exit, `cleanValue` + release.
- For struct args: `envDefine` allocates flat memory, copies struct bytes,
  calls `structRefInc`. At scope exit, `structRefDec` + free flat memory.

The key change: `copyValue` for `@T` currently just returns the same
`@Value`. It should instead be eliminated — the caller passes the raw
pointer, and `envDefine` creates its own `@Value`.

### 5. Function return: explicit ownership transfer

`execReturn` evaluates the return expression. The result Value's contents
are "moved" to the caller:
- The return Value's managed data is NOT cleaned (it's being transferred).
- The caller receives ownership.
- The function's scope cleanup runs `cleanValue` on all OTHER locals
  (not the returned one).

This matches the current `IsFresh` mechanism but is more explicit.

### 6. evalSelector: borrow, don't own

When reading a struct field (`o.Field`), `evalSelector` returns a
borrowed reference. The returned Value borrows from the struct's flat
memory. The caller must NOT hold this borrow across operations that
could free the struct.

For safety, `evalSelector` for `@T` fields could RefInc the managed
pointer and mark the Value as needing cleanup. But this requires temp
tracking. An alternative: the caller (evalExpr/execAssign) immediately
consumes the borrow by either:
- Storing it in a variable (envDefine RefIncs)
- Passing it to a function (function call RefIncs the param)
- Using it in an expression (no persist needed)

The current borrow model works IF the struct stays alive during the
borrow. The problem case is when a function call within the expression
evaluation frees the struct. This needs auditing.

## Implementation Order

1. Add `IsClean` field to Value.
2. Implement `cleanValue`.
3. Fix `popEnv` to call cleanup (fixes test 247 leak).
4. Audit and fix `cleanupEnvExcept` to call `cleanValue`.
5. Remove `copyValue` for `@T` — refactor `callFunc` parameter binding.
6. Audit `evalSelector` borrow safety.
7. Run boot-comp-int tests, fix remaining issues.

## Relationship to Debug Hooks

The `IsClean` field and explicit destruction protocol described here are
the runtime side. The debug hooks (`plan-debug-hooks.md`) provide
compile-time assertions:
- `pre_copy` hook: asserts Values are never copied
- `pre_destroy` hook: asserts `IsClean` before destruction

The hooks are optional tooling; the ownership model described here is the
behavioral fix. See `plan-interp-value-hooks.md` for how hooks would be
used.

## Relationship to `move` builtin

The proposed `move` builtin (see `claude-notes.md`) would make the
ownership transfers in this plan explicit in source code:

```binate
// Parameter binding
envDefine(paramName, move(argValue))  // caller gives up ownership

// Return
interp.ReturnVals[0] = move(localValue)  // local gives up ownership

// Scope cleanup
var v @Value = move(entry.Val)  // take ownership from env entry
cleanValue(v)                    // clean contents
v = nil                          // release Value shell
```

Without `move`, the same transfers happen via nil-and-swap patterns.
`move` makes the intent explicit and enables compiler optimization
(skip copy+dtor, just memcpy+zero).
