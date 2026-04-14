# Plan: Using Debug Hooks for Interpreter Value

## Overview

Once the debug hook infrastructure (plan-debug-hooks.md) is implemented,
the interpreter's Value struct can use it to enforce the ownership model
described in plan-interp-value-ownership.md. This is the "belt and
suspenders" approach — the ownership model is the behavioral fix, and the
hooks are runtime assertions that catch violations.

## Value struct with annotations

```binate
#[pre_copy(valuePreCopy), pre_destroy(valuePreDestroy)]
type Value struct {
    Kind      int
    Typ       @types.Type
    RawAddr   *uint8
    IsClean   bool
    // ... other fields ...
}
```

## Hook implementations

### pre_copy: reject all copies

```binate
func valuePreCopy(dst *uint8, src *uint8) {
    // Values must never be copied by value. They should only be
    // passed by pointer (raw for borrowing, managed for ownership).
    // If we reach here, something is passing Value by value.
    panic("Value copied by value — use pointer instead")
}
```

This catches:
- `callFunc` passing Value by value (should pass by pointer)
- Any function taking `Value` instead of `@Value` or `*Value`
- Struct embedding of Value (should use `@Value` field instead)

### pre_destroy: verify contents cleaned

```binate
func valuePreDestroy(ptr *uint8) {
    // Before a Value's memory is freed, its contents must have been
    // explicitly cleaned via cleanValue(). If IsClean is false,
    // the destructor is being called on a Value that still owns
    // managed data — which will leak.
    var v *Value = cast(*Value, ptr)
    if !v.IsClean {
        print("BUG: Value destroyed without cleanup. Kind=")
        println(v.Kind)
        panic("Value destroyed without calling cleanValue")
    }
}
```

This catches:
- `popEnv` discarding scope without cleanup (test 247)
- `@Value` going to rc=0 without explicit `cleanValue`
- Any code path that drops a Value reference without cleaning contents

## How to use

1. Build interpreter with `--debug-hooks`:
   ```sh
   bnc --debug-hooks -o /tmp/bni_debug cmd/bni
   ```

2. Run tests:
   ```sh
   /tmp/bni_debug --test -root . pkg/lexer
   ```

3. If a Value is copied, the `pre_copy` hook panics with a message
   identifying the violation.

4. If a Value dies without cleanup, the `pre_destroy` hook panics with
   the Value's Kind, making it easy to identify which code path is
   leaking.

## Expected findings

With the current interpreter code (before ownership model changes):
- `pre_copy` will fire in `callFunc` → `copyValue` for struct Values
- `pre_destroy` will fire in `popEnv` (sub-scope locals not cleaned)
- `pre_destroy` will fire when `@Value` references are abandoned

After the ownership model is implemented (plan-interp-value-ownership.md):
- Neither hook should fire in normal operation
- Any fire indicates a regression or missed code path

## Iterative debugging

The hooks can be used iteratively:
1. Enable hooks, run tests, get first panic
2. Fix the code path that caused the panic
3. Re-run, get next panic (or pass)
4. Repeat until all tests pass with hooks enabled

This is much more efficient than debugging UAFs via libgmalloc — the
hooks tell you exactly WHICH Value was mishandled and WHERE, rather than
crashing on a stale memory access much later.
