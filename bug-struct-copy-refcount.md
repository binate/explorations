# Bug: Missing RefInc on Struct Copies with Managed Fields

## Status: FIXED in compiler (commit 2052570), interpreter NOT YET FIXED

## Summary

When a struct containing `@[]T` (managed-slice) or `@T` (managed-pointer) fields is copied by value, the compiler did not emit RefInc for the managed fields in the copy. The compiler *does* generate destructors that RefDec these fields at end of scope. The result was that each struct copy over-decremented the backing refcount, leading to use-after-free and heap corruption.

### Compiler fix

Commit `2052570` ("Implement copy constructors for structs with managed fields") adds `emitStructCopy` / `emitStructDtor` calls to variable assignment, pointer deref assignment, and field assignment paths in `gen_control.bn`. This fixed the `pkg/types` and `pkg/parser` unit test crashes.

### Interpreter NOT YET FIXED

The self-hosted interpreter (`pkg/interp`) does **not** run copy constructors or destructors for struct copies with managed fields. This means:
- `TestScopeCleanupRefDec` in `call_test.bn` expects refcount to return to 1 after a function call that copies a managed struct, but the interpreter leaves it at 3 (param copy + local copy not decremented on scope exit).
- The test has been updated to expect the current (broken) behavior with a TODO comment.
- **Fix needed**: the interpreter's scope cleanup (`cleanupScope` or equivalent) should RefDec managed fields in struct-typed variables when they go out of scope, mirroring the compiler's destructor behavior.

## Symptoms

- `pkg/types` and `pkg/parser` unit tests crash in boot-comp mode with `malloc(): unaligned tcache chunk detected`
- The crash occurs after ~7-8 sequential test function calls (accumulated heap corruption)
- The same code works correctly in boot mode (bootstrap interpreter) because the interpreter doesn't use refcounting
- Standalone conformance tests that use the parser/type-checker don't crash because a single `main()` function doesn't create enough copies to trigger the corruption

## The Specific Trigger

In `pkg/lexer/lexer.bn:46-52`, the `curPos()` function creates a `token.Pos` struct:

```binate
func curPos(l @Lexer) token.Pos {
    var p token.Pos
    p.File = l.file    // @[]char — RefInc'd correctly by field assignment
    p.Line = l.line
    p.Col = col(l)
    return p           // returned by value — no RefInc on copy
}
```

In `pkg/lexer/scan.bn:119-121`, the returned Pos is embedded in a Token:

```binate
var pos token.Pos = curPos(l)   // struct copy — no RefInc on File.backing
// ...
tok.Pos = pos                    // another struct copy — no RefInc on File.backing
```

### The struct types involved

```binate
type Pos struct {
    File @[]char   // managed-slice — 32 bytes, has backing refptr
    Line int
    Col  int
}

type Token struct {
    Typ  token.Type
    Lit  @[]char   // managed-slice
    Pos  Pos       // contains File @[]char
}
```

Both `Token` and `Pos` have compiler-generated destructors (`bn_token____dtor_Token`, `bn_token____dtor_Pos`) that RefDec their managed fields when the struct is freed.

### What goes wrong, step by step

1. `p.File = l.file` — the codegen correctly RefInc's `l.file.backing` (managed-slice field assignment has RefInc/RefDec logic in `gen_control.bn:217-225`)
2. `return p` — the Pos struct is returned by value. The 48-byte struct is copied to the caller's stack. **No RefInc is emitted for `p.File.backing`.** The local `p` is then destroyed at function exit, and its destructor RefDec's `p.File.backing`.
3. At this point, `File.backing` has the same refcount it had before `curPos()` was called — the RefInc from step 1 was cancelled by the RefDec in step 2. But the caller now holds a copy of the File managed-slice with no corresponding RefInc.
4. `tok.Pos = pos` — another struct copy. Again no RefInc for the `File` field inside Pos.
5. When `pos` goes out of scope, its destructor RefDec's `File.backing` → refcount decremented below what it should be.
6. When `tok` is later destroyed, its destructor RefDec's `tok.Pos.File.backing` — the same backing pointer, now with a refcount that's already too low.
7. After enough token create/destroy cycles (~7-8 in the test), the refcount hits 0 while other references still exist. The backing is freed. Subsequent access through a dangling pointer corrupts the heap.

## The General Issue

The compiler handles refcounting for managed types in these cases:

- **Direct managed-slice field assignment** (`s.field = managedSlice`) — RefInc new, RefDec old ✓
- **Direct managed-pointer field assignment** (`s.field = managedPtr`) — RefInc new, RefDec old ✓
- **Variable assignment of managed-slice/managed-pointer** (`var x @[]T = y`) — RefInc ✓
- **Scope-exit destructors** for structs with managed fields — RefDec ✓

But it does **NOT** handle refcounting for:

- **Struct value copies** where the struct contains managed fields — `var x Pos = y` (no RefInc on managed fields inside)
- **Struct return by value** — `return pos` where Pos contains managed fields (no RefInc on copy)
- **Struct passed by value** as function argument — `f(pos)` (no RefInc for the copy)
- **Struct assigned to struct field** — `tok.Pos = pos` as a whole-struct copy (no RefInc on embedded managed fields)

In all these cases, the struct's bytes are copied but the managed fields' backing refcounts are not incremented, while the destructor of the source will still RefDec them.

## Scope of Impact

This affects any struct containing `@[]T` or `@T` fields that gets copied by value. Known affected types:

- `token.Pos` (contains `File @[]char`) — triggered by lexer
- `token.Token` (contains `Lit @[]char` and `Pos` with `File @[]char`) — triggered by lexer/parser
- `buf.CharBuf` (contains `Data @[]char`) — triggered by any buf usage with copy semantics
- Any user-defined struct with managed fields

The lexer and parser are the first to trigger visible corruption because they create and destroy many Token/Pos structs in quick succession.

## Where to Fix

The fix needs to be in the IR generation, specifically in:

1. **`pkg/ir/gen_control.bn` — variable assignment** (line ~98 area): when assigning a struct value to a variable, if `types.NeedsDestruction(varTyp)`, emit RefInc for each managed field in the source value, and RefDec for each managed field in the old variable value.

2. **`pkg/ir/gen_control.bn` — struct field assignment** (line ~223 area): when assigning a struct value to a struct field via selector, same logic.

3. **`pkg/ir/gen_expr.bn` — function return**: when returning a struct by value, RefInc its managed fields (the local's destructor will RefDec them).

4. **`pkg/ir/gen_expr.bn` — function arguments**: when passing a struct by value, RefInc its managed fields (the caller's copy should outlive the call).

The helper `types.NeedsDestruction(t)` already exists and returns true for structs with managed fields. The destructor generation code in `pkg/ir/gen_dtor_emit.bn` already knows how to walk struct fields and emit RefDec — a similar walk that emits RefInc is needed for copies.

## Workaround

Until this is fixed, code that copies structs with managed fields can avoid the bug by manually managing refcounts or by passing structs by pointer (`@T`) instead of by value. The lexer could be changed to use `@Pos` or to not store `File` as a managed slice in every Pos, but this is a band-aid.
