# Plan: Self-Hosted Parser Support for `*[]T` Raw-Slice Syntax

Implements Stage 0 + Stage 1 of `plan-raw-slice-syntax.md` in the self-hosted
toolchain (`/Users/vtl/binate/binate`, `pkg/parser`).

**Stage 0**: After `*`, reject a following `[` that is not `]` — force parens for
pointer-to-slice (`*([]T)`) and pointer-to-array (`*([N]T)`).
**Stage 1**: Accept `*[]T` as new syntax for raw slice (alongside existing `[]T`).

The self-hosted parser is shared by `cmd/bni` (interpreter) and `cmd/bnc`
(compiler), so one change covers both frontends. `cmd/bni2` uses the same
`pkg/parser`, so it's covered too. `pkg/vm` (bytecode VM) consumes AST from the
same parser and does not care about surface syntax.

Prerequisite: the Go bootstrap parser must accept `*[]T` first
(`plan-bootstrap-raw-slice-syntax.md`), otherwise the self-hosted parser source
cannot introduce `*[]T` in its own code. The order of work is:

1. Bootstrap parser accepts `*[]T`.
2. Self-hosted parser adds `*[]T` support (this plan). Self-hosted source files
   may now use `*[]T` (and pointer-to-slice must be written `*([]T)`).
3. (Stage 2 later) migrate all `[]T` to `*[]T` in all code.

## Files to touch

### `pkg/parser/parser.bn`, `parseType` (lines 87–193)

Today:

```binate
if p.tok.Typ == token.STAR {
    // *T — pointer type
    var pos token.Pos = p.tok.Pos
    next(p)
    var base @ast.TypeExpr = parseType(p)
    var te @ast.TypeExpr = make(ast.TypeExpr)
    te.Kind = ast.TEXPR_POINTER
    te.Pos = pos
    te.Base = base
    return te
}
```

Becomes (mirrors the existing `@` handler at lines 100–138):

```binate
if p.tok.Typ == token.STAR {
    // *T or *[]T
    var pos token.Pos = p.tok.Pos
    next(p)
    if p.tok.Typ == token.LBRACKET {
        var lbrack token.Pos = p.tok.Pos
        next(p) // consume [
        if p.tok.Typ == token.RBRACKET {
            // *[]T — raw slice sugar
            next(p) // consume ]
            var elem @ast.TypeExpr = parseType(p)
            var te @ast.TypeExpr = make(ast.TypeExpr)
            te.Kind = ast.TEXPR_SLICE
            te.Pos = lbrack
            te.Base = elem
            return te
        }
        // Bare "*[<expr>" no longer valid — require parens.
        errMsg(p, "bare \"*[\" is raw-slice sugar (*[]T); " +
            "use \"*([N]T)\" or \"*([]T)\"")
        // Recover as pointer-to-array to keep parsing going.
        var length @ast.Expr = parseExpr(p)
        expect(p, token.RBRACKET)
        var elem @ast.TypeExpr = parseType(p)
        var arrTe @ast.TypeExpr = make(ast.TypeExpr)
        arrTe.Kind = ast.TEXPR_ARRAY
        arrTe.Pos = lbrack
        arrTe.Base = elem
        arrTe.Len = length
        var te @ast.TypeExpr = make(ast.TypeExpr)
        te.Kind = ast.TEXPR_POINTER
        te.Pos = pos
        te.Base = arrTe
        return te
    }
    var base @ast.TypeExpr = parseType(p)
    var te @ast.TypeExpr = make(ast.TypeExpr)
    te.Kind = ast.TEXPR_POINTER
    te.Pos = pos
    te.Base = base
    return te
}
```

Uses the existing `TEXPR_SLICE` constant (`pkg/ast.bni`), so no AST schema
change — only surface-syntax change.

### `pkg/ast.bni`, `pkg/ast/*.bn`

No changes needed. `TEXPR_SLICE` already represents raw slices.

### `pkg/types/` and `pkg/types/checker.bn`

No semantic changes. `MakeSliceType` (types.bn:120) and `TypeName` formatting
(types.bn:204–208, currently prints `"[]T"`) remain unchanged for now. Stage 2
will revisit `TypeName` to print `"*[]T"`.

### `pkg/interp/`, `pkg/ir/`, `pkg/codegen/`, `pkg/vm/`

No changes. These consume the resolved `@Type` objects, not surface syntax.

## Tests

### Unit tests in `pkg/parser/parser_test.bn`

1. `var x *[]int` parses as `TEXPR_SLICE` with elem=`int`.
2. `*int` still parses as `TEXPR_POINTER`.
3. `*([]int)` parses as `TEXPR_POINTER → TEXPR_PAREN → TEXPR_SLICE`.
4. `*([N]int)` parses as pointer to array.
5. `*@[]int` still parses (raw pointer to managed-slice — the `@[` recursion
   is unaffected).
6. **Error**: `*[5]int` (bare `*[<expr>`) → parser emits the "use parens" error.

### Conformance tests

Add a small test that uses `*[]T` in a function signature, as a local
variable, and as a `bit_cast` target, verifying behavior matches existing
`[]T` usage. Keep `[]T` test coverage intact (backward compat).

### Bootstrap-subset note

The new `*[]T` syntax is in the bootstrap subset (it compiles to `TEXPR_SLICE`
the same as `[]T`). Self-hosted sources may use either during the migration.
The bootstrap must be updated first (`plan-bootstrap-raw-slice-syntax.md`) so
self-hosted sources introducing `*[]T` remain runnable under bootstrap.

## Rollout

1. Land self-hosted parser change on a worktree branch.
2. Run full test matrix: `go test ./...` in bootstrap, unit tests for all
   self-hosted packages, full `conformance/run.sh all` modeset.
3. Existing `*[]T` (pointer-to-slice) and `*[N]T` (pointer-to-array) uses in
   self-hosted sources must be rewritten to `*([]T)` / `*([N]T)`. A repo-wide
   scan (already done for `pkg/vm/vm_extern.bn` on branch `assembler`,
   commit `3692e87`) gives the migration list.
4. Merge.

## Out of scope for this plan

- Stage 2 (systematically migrating `[]T` → `*[]T` across `binate/`,
  `bootstrap/`, conformance, docs). Many small commits; each is a mechanical
  search-and-replace validated by tests.
- Stage 3 (removing `[]T` from the grammar and parsers). This is the terminal
  state; only do it after Stage 2 is complete everywhere.
- `TypeName` / error-message updates (Stage 2-adjacent): once `[]T` is gone,
  the string form should switch to `"*[]T"`.
