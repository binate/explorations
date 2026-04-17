# Plan: Bootstrap Parser Support for `*[]T` Raw-Slice Syntax

Implements Stage 0 + Stage 1 of `plan-raw-slice-syntax.md` in the Go bootstrap
interpreter (`/Users/vtl/binate/bootstrap`).

**Stage 0**: After `*`, reject a following `[` that is not `]` — force parens for
pointer-to-slice (`*([]T)`) and pointer-to-array (`*([N]T)`).
**Stage 1**: Accept `*[]T` as new syntax for raw slice (alongside existing `[]T`).

Both stages happen together in this plan — they are one parser change.

## Files to touch

### `parser/parser.go` (`parseType`, lines 106–176)

Today:

```go
case token.STAR: // *T
    pos := p.tok.Pos
    p.next()
    base := p.parseType()
    return &ast.PointerType{Star: pos, Base: base}
```

Becomes (mirrors the existing `@` handler at lines 114–135):

```go
case token.STAR: // *T or *[]T
    pos := p.tok.Pos
    p.next()
    if p.tok.Type == token.LBRACKET {
        lbrack := p.tok.Pos
        p.next() // consume [
        if p.tok.Type == token.RBRACKET {
            // *[]T — raw slice sugar
            p.next() // consume ]
            elem := p.parseType()
            return &ast.SliceType{Lbrack: lbrack, Elem: elem}
        }
        // Bare "*[<expr>" is no longer a pointer-to-array. Require parens.
        p.errorf("bare \"*[\" is raw-slice sugar (\"*[]T\"); " +
            "use \"*([N]T)\" for pointer to array, \"*([]T)\" for pointer to slice")
        // Recover: treat as if parens were present.
        length := p.parseExpr()
        p.expect(token.RBRACKET)
        elem := p.parseType()
        arr := &ast.ArrayType{Lbrack: lbrack, Len: length, Elem: elem}
        return &ast.PointerType{Star: pos, Base: arr}
    }
    base := p.parseType()
    return &ast.PointerType{Star: pos, Base: base}
```

Note: re-using `ast.SliceType` (lines 497–504 in `ast/ast.go`) — no new AST node.
The only difference between old `[]T` and new `*[]T` is surface syntax; both
produce the same AST. Stage 3 (dropping `[]T`) is out of scope for this plan.

### `ast/ast.go`

No changes needed — `SliceType` already represents raw slices regardless of how
they were spelled at the source level.

### `types/` and `types/checker.go`

No semantic changes. The type system already has `SliceType` (raw) distinct from
`ManagedSliceType` (managed). `SliceType.String()` (types.go:99) returns `"[]T"`
— keep that for now. Stage 2 will revisit to print `"*[]T"` instead.

## Tests

### Parser tests (`parser/parser_test.go` or similar)

1. `var x *[]int` — parses as `SliceType{Elem: int}` (Stage 1 acceptance).
2. `var x []int` — still parses (backward compat during migration).
3. `var x *int` — unchanged, parses as `PointerType{Base: int}`.
4. `var x *(*[]int)` — pointer to raw slice (paren form).
5. `var x *([]int)` — same as above.
6. `var x *(int)` — pointer to int via parens; still works.
7. `var x *@[]int` — raw pointer to managed-slice; still works (the `@[` case is
   inside `*T`'s recursive call).
8. `var x @(*[]int)` — managed pointer to raw slice.
9. **Error cases**: `var x *[N]int` (bare `*[<expr>`) → parser error telling
   the user to write `*([N]int)`.

### Integration

- Conformance tests: all existing tests that use `[]T` continue to pass
  (backward compat).
- Add a small conformance test that declares a variable of type `*[]T`, passes
  it to a function parameter of type `[]T`, and confirms the equivalence (can
  re-use existing slice behavior tests).

## Rollout

This is a **breaking change** for any code that uses `*[N]T` (pointer to array)
or `*[]T` with old meaning (pointer to slice). The binate repo has already been
scanned — only `pkg/vm/vm_extern.bn` used `*[]T` for pointer-to-slice (rewritten
to `*([]T)` preemptively). The bootstrap's own Go code is unaffected.

After merging: also check that error messages from the bootstrap parser still
make sense, and that `claude-notes.md` examples parse.

## Out of scope for this plan

- Stage 2 (migrating `[]T` → `*[]T` in all code).
- Stage 3 (removing `[]T` from the grammar).
- Self-hosted parser changes — see `plan-selfhost-raw-slice-syntax.md`.
- `.bni` interface files — those are parsed by the same `parseType`, so support
  comes automatically.
