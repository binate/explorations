# Plan: Migrate Raw Slice/Pointer Fields to Managed

## Problem

When `@T` is freed (refcount → 0), raw slice fields (`[]char`, `[]@U`) inside it become dangling pointers. This prevents re-enabling `Free` in `RefDec`. Every field in a managed struct whose backing data lives in a separate allocation must itself be managed.

## Categories of Change

1. **`[]char` → `@[]char`**: String/name fields in managed structs
2. **`[]@T` → `@[]@T`**: Collection fields holding managed pointers
3. **`[][]char` → `@[]@[]char`**: Nested string collections (once `[]char` → `@[]char`)
4. **`[]uint8` → `@[]uint8`**: Byte buffer fields
5. **`[]T` → `@[]T`**: Other raw slices of value types in managed structs

## Change List by Package

Ordered by dependency (leaf packages first). Each item is one commit.

### 1. pkg/ast (AST node types)

**`[]char` → `@[]char`:**
- `Expr.Name`
- `Decl.Name`
- `TypeExpr.Name`
- `TypeExpr.Pkg`
- `ParamDecl.Name`
- `FieldDecl.Name`
- `ImportSpec.Alias`
- `ImportSpec.Path`
- `File.PkgName`

**`[]@T` → `@[]@T`:**
- `Expr.Args` (`[]@Expr` → `@[]@Expr`)
- `Expr.Elems` (`[]@Element` → `@[]@Element`)
- `Stmt.Stmts` (`[]@Stmt` → `@[]@Stmt`)
- `Stmt.Exprs` (`[]@Expr` → `@[]@Expr`)
- `Stmt.Exprs2` (`[]@Expr` → `@[]@Expr`)
- `Stmt.Cases` (`[]@CaseClause` → `@[]@CaseClause`)
- `Decl.Params` (`[]@ParamDecl` → `@[]@ParamDecl`)
- `Decl.Results` (`[]@TypeExpr` → `@[]@TypeExpr`)
- `Decl.Decls` (`[]@Decl` → `@[]@Decl`)
- `TypeExpr.Fields` (`[]@FieldDecl` → `@[]@FieldDecl`)
- `CaseClause.Exprs` (`[]@Expr` → `@[]@Expr`)
- `CaseClause.Body` (`[]@Stmt` → `@[]@Stmt`)
- `File.Imports` (`[]@ImportSpec` → `@[]@ImportSpec`)
- `File.Decls` (`[]@Decl` → `@[]@Decl`)

### 2. pkg/lexer

- `Lexer.src` (`[]uint8` → `@[]uint8`)
- `Lexer.file` (`[]char` → `@[]char`)

### 3. pkg/parser

- `Parser.errs` (`[]ParseError` → `@[]ParseError`)
- `ParseError.Msg` (`[]char` → `@[]char`)

### 4. pkg/types

**`[]char` → `@[]char`:**
- `Type.Name`
- `Field.Name`
- `Param.Name`
- `Symbol.Name`
- `Symbol.PkgPath`
- `CheckError.Msg`
- `PkgEntry.Path`

**`[]@T` → `@[]@T`:**
- `Type.Fields` (`[]@Field` → `@[]@Field`)
- `Type.Params` (`[]@Param` → `@[]@Param`)
- `Type.Results` (`[]@Type` → `@[]@Type`)
- `Checker.FuncRet` (`[]@Type` → `@[]@Type`)

### 5. pkg/loader

**`[]char` → `@[]char`:**
- `Package.Path`
- `Loader.Root`

**`[][]char` → `@[]@[]char`:**
- `Package.Imports`
- `Loader.Roots`
- `Loader.Order`
- `Loader.Errors`

**`[]@T` → `@[]@T`:**
- `Loader.Packages` (`[]@Package` → `@[]@Package`)

### 6. pkg/ir

**`[]char` → `@[]char`:**
- `Module.Name`
- `Global.Name`
- `TypeDef.Name`
- `Func.Name`
- `Param.Name` (ir.Param, not types.Param)
- `Instr.StrVal`

### 7. pkg/codegen

- `StructDef.Name` (`[]char` → `@[]char`)
- `StructDef.Fields` (`[]@types.Type` → `@[]@types.Type`)
- `StringConst.Data` (`[]char` → `@[]char`)

### 8. pkg/interp

**`[]char` → `@[]char`:**
- `Value.StrVal`
- `Value.FuncName`
- `TypeEntry.Name`
- `AliasEntry.Name`
- `AliasEntry.Path`
- `EnvEntry.Name`
- `PkgEnv.Path`
- `Interpreter.Stdout`

**`[]@T` → `@[]@T`:**
- `Value.Elems` (`[]@Value` → `@[]@Value`)
- `Value.Fields` (`[]@Value` → `@[]@Value`)
- `Interpreter.ReturnVals` (`[]@Value` → `@[]@Value`)

**`[][]char` → `@[]@[]char`:**
- `Interpreter.ProgArgs`

### 9. cmd/bnc, cmd/bni

- cmd/bnc `CLIArgs`: OK as-is (stack-scoped value type, never `@`-allocated)
- cmd/bni `CLIArgs.RootOverride`: OK as-is (borrowed from bootstrap.Args, single execution phase)

## Fields Confirmed Safe (No Change)

- `token.Pos.File []char` — value type, never `@`-allocated, short-lived
- `token.Token.Lit []char` — value type, consumed immediately in parsing
- `ir/gen.ModuleConst.Name []char` — struct stored in `@[]ModuleConst` by value; but name borrows from AST... **REVISIT** — may need change
- `ir/gen_stmt.VarSlot.Name []char` — borrowed from AST; parent GenContext keeps AST alive... **REVISIT** — may need change if AST nodes can be freed during gen
- `codegen/emit.FuncRetType.Name/RetType []char` — single-pass analysis, never outlives emit
- `rt.ManagedSlice.Data/Refptr *any` — C FFI layout, correct as raw
- cmd CLIArgs — ephemeral

## Implementation Strategy

- Change one package at a time, bottom-up by dependency
- For each package: update `.bni`, update `.bn` files, update all consumers
- Each field change: update the type, fix all read/write sites, run tests, commit
- After all packages done: re-enable `Free` in `RefDec` and verify

## Impact on Bootstrap Interpreter

The bootstrap interpreter (Go) must also support `@[]T` fields in structs. Currently it may treat `[]T` and `@[]T` differently (SliceVal vs ManagedSliceVal). Need to verify that the bootstrap handles managed slice fields in struct definitions correctly.

## Verification

After each package:
```
go run . -root ../binate -test <pkg>
./conformance/run.sh boot
./conformance/run.sh boot-comp
```

After all packages:
```
./conformance/run.sh boot-comp-comp
./conformance/run.sh boot-comp-comp-comp
./conformance/run.sh boot-comp-int
```

Then re-enable Free and retest.
