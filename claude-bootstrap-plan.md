# Binate Bootstrap Interpreter — Implementation Plan (Go)

This plan covers the Go-based bootstrap interpreter that will run enough of Binate to eventually execute the self-hosted compiler/interpreter. The bootstrap subset is defined in `grammar.ebnf` (marked `[BOOTSTRAP]`).

Reference: `claude-plan-1.md` Phase 4, `grammar.ebnf` for the formal grammar.

---

## Architecture Overview

```
Source (.bn files)
    │
    ▼
┌─────────┐    ┌─────────┐    ┌────────────┐    ┌─────────────┐
│  Lexer   │───▶│ Parser  │───▶│ Type Check │───▶│ Interpreter │
│          │    │         │    │            │    │ (tree-walk) │
└─────────┘    └─────────┘    └────────────┘    └─────────────┘
  tokens         AST           typed AST         execution
```

Single-pass pipeline. No optimization. The interpreter evaluates the AST directly.

---

## 1. Project Structure

```
bootstrap/
  main.go              // entry point, file loading, CLI
  token/
    token.go           // token types, Token struct, keyword table
  lexer/
    lexer.go           // scanner, ASI insertion
    lexer_test.go
  ast/
    ast.go             // AST node types
  parser/
    parser.go          // recursive descent parser
    parser_test.go
  types/
    types.go           // type representations (int, bool, pointer, slice, struct, etc.)
    checker.go         // type checking pass
    checker_test.go
  interpreter/
    interpreter.go     // tree-walking evaluator
    value.go           // runtime value representation
    memory.go          // managed allocation, refcounting
    builtins.go        // make, box, cast, bit_cast, len
    io.go              // file I/O, stdout
    interpreter_test.go
  testdata/
    *.bn               // test programs
```

---

## 2. Lexer (token/ + lexer/)

### 2.1 Token Types

All tokens needed for the bootstrap subset:

- **Identifiers**: `identifier` (also matches predeclared names like `int`, `uint`, `bool`, etc.)
- **Keywords**: `break`, `case`, `const`, `continue`, `default`, `else`, `false`, `for`, `func`, `if`, `import`, `in`, `nil`, `package`, `return`, `struct`, `switch`, `true`, `type`, `var`
- **Builtin keywords**: `make`, `box`, `cast`, `bit_cast`, `len`
- **Literals**: `int_literal` (decimal, hex, octal, binary), `string_literal`, `char_literal`
- **Operators**: `+`, `-`, `*`, `/`, `%`, `&`, `|`, `^`, `~`, `<<`, `>>`, `==`, `!=`, `<`, `>`, `<=`, `>=`, `&&`, `||`, `!`, `=`, `:=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`, `++`, `--`
- **Punctuation**: `.`, `,`, `;`, `:`, `@`, `(`, `)`, `[`, `]`, `{`, `}`, `...`
- **Special**: `EOF`, `ILLEGAL`, `SEMICOLON` (auto-inserted)

Deferred from bootstrap: `#` (annotations), `unsafe_index`, `interface`, `impl`, float literals.

### 2.2 Automatic Semicolon Insertion (ASI)

After scanning each token, if it's the last token on its line and is one of:
- identifier, int/string/char literal, `true`, `false`, `nil`, `break`, `continue`, `return`, `++`, `--`, `)`, `]`, `}`

...then insert a synthetic `;` token before the next real token.

Implementation: track whether the previous token triggers ASI. When the next token is on a new line and ASI applies, emit `;` first.

### 2.3 Comments

- `//` line comments: skip to end of line
- `/* */` block comments: skip to closing `*/`, track newlines for ASI

### 2.4 Error Handling

- Record position (file, line, column) in every token
- On illegal character or malformed literal: emit `ILLEGAL` token with error message, continue scanning (don't abort)

---

## 3. Parser (ast/ + parser/)

Recursive descent, following the grammar productions directly. No backtracking needed for the bootstrap subset (generics are deferred, so D5/D11 simplify to "always index/array").

### 3.1 AST Node Types

One Go type per grammar production (or group of related productions). Key nodes:

**Top-level:**
- `File` (package clause, imports, declarations)
- `ImportSpec` (alias, path)

**Declarations:**
- `TypeDecl` (name, type def — alias, struct, distinct)
- `VarDecl` (name, type, init expr)
- `ConstDecl` (name, type, expr, iota value)
- `FuncDecl` (name, params, results, body)
- `ShortVarDecl` (names, exprs)

**Statements:**
- `Block`, `IfStmt`, `ForStmt` (all variants), `SwitchStmt`, `CaseClause`
- `ReturnStmt`, `BreakStmt`, `ContinueStmt`
- `AssignStmt` (simple and compound), `IncDecStmt`
- `ExprStmt`

**Expressions:**
- `BinaryExpr` (op, left, right)
- `UnaryExpr` (op, operand — includes `*`, `&`, `-`, `!`, `~`)
- `CallExpr` (func, args)
- `IndexExpr` (expr, index)
- `SliceExpr` (expr, low, high)
- `SelectorExpr` (expr, field — dot access)
- `CompositeLit` (type, elements)
- `ArrayLit` (length expr or `...`, element type, elements)
- `Ident`, `IntLit`, `StringLit`, `CharLit`, `BoolLit`, `NilLit`
- `BuiltinCall` (which builtin, args — make/box/cast/bit_cast/len)

**Types (in AST):**
- `NamedType` (identifier, optional package qualifier)
- `PointerType` (raw `*T`)
- `ManagedPtrType` (`@T`)
- `ManagedSliceType` (`@[]T`)
- `SliceType` (`[]T`)
- `ArrayType` (length expr, element type)
- `StructType` (fields)

### 3.2 Disambiguation (Bootstrap Subset)

In the bootstrap subset, most disambiguation rules simplify:
- **D1 (ShortVarDecl vs Assignment)**: parse LHS as expression list, check for `:=` vs `=`/`op=`
- **D2 (For variants)**: lookahead for `;`, `in`, or `{`
- **D3 (@[]T sugar)**: lookahead `@` `[` `]`
- **D4 (Composite lit in conditions)**: require parens in if/for/switch conditions
- **D5 (Generic vs index)**: bootstrap always parses as index (generics deferred)
- **D9 (PrimaryExpr ordering)**: builtins are keywords; composite lit when identifier + `{`
- **D10 (StructField)**: lookahead after identifier
- **D11 (TypeParams vs ArrayType)**: bootstrap always parses as array (generics deferred)

### 3.3 Error Recovery

Minimal but functional:
- On parse error, skip to next synchronization point (`;`, `}`, or a top-level keyword)
- Report error with position, continue parsing
- Collect all errors, report at end

### 3.4 Operator Precedence

Implement via the precedence climbing encoded in the grammar: `OrExpr` → `AndExpr` → `CompareExpr` → ... → `UnaryExpr` → `PostfixExpr` → `PrimaryExpr`. Each level is a function.

---

## 4. Type Checker (types/)

### 4.1 Type Representations

Go types representing Binate types at compile time:

- `IntType` (width, signed) — covers int, uint, int8..int64, uint8..uint64, byte, char
- `BoolType`
- `PointerType` (element type, raw vs managed)
- `SliceType` (element type, raw vs managed)
- `ArrayType` (element type, length)
- `StructType` (name, fields)
- `NamedType` (name, underlying type) — distinct types
- `AliasType` (name, target type)
- `FuncType` (params, results)
- `NilType` (assignable to pointer types)

### 4.2 Type Checking Pass

Walk the AST, resolve types, check consistency:

1. **First pass — collect declarations**: scan top-level declarations to build the symbol table (types, functions, globals). This handles forward references — a function can call another function defined later in the file.

2. **Second pass — check bodies**: type-check function bodies, variable initializers, const expressions.

Key checks:
- Assignment compatibility (same type, or implicit `@T` → `*T`)
- Binary/unary operator type rules (arithmetic on integers, comparison, logical on bool)
- Function call argument/return type matching
- Struct field access validity
- Index/slice bounds types (must be integer)
- Composite literal field types
- `cast` / `bit_cast` validity
- Short var decl type inference
- Const expression evaluation (including `iota`)
- `nil` only assignable to pointer types
- No implicit integer conversions (require `cast`)

### 4.3 Symbol Table / Scoping

- Nested scopes: package → function → block → block...
- Each scope maps names to their type + declaration node
- Variable shadowing allowed (the type checker doesn't warn — that's a linter concern, not bootstrap)
- Package-level scope populated in first pass

### 4.4 Const Evaluation

Evaluate `const` expressions at type-check time:
- Integer arithmetic, bitwise ops, shifts
- `iota` substitution in grouped const blocks
- Result stored in the AST node for the interpreter to use directly

---

## 5. Tree-Walking Interpreter (interpreter/)

### 5.1 Runtime Values

Every Binate value at runtime is represented as a Go value. Options:

```go
type Value interface {
    Type() types.Type
}

type IntValue struct { Val int64; Typ types.Type }
type BoolValue struct { Val bool }
type PointerValue struct { Addr *HeapObject; Offset int; Managed bool }
type SliceValue struct { ... }
type StructValue struct { Fields []Value }
// etc.
```

Or a more compact tagged-union approach. Decide during implementation — correctness first, optimize later.

### 5.2 Managed Memory / Refcounting

Core of the runtime:

```go
type HeapObject struct {
    Refcount  int
    Data      []byte      // raw storage
    Type      types.Type  // for field traversal during release
    FreeFn    func()      // normally just removes from heap tracking
}
```

Operations:
- **Retain**: increment refcount
- **Release**: decrement refcount; if zero, recursively release managed fields, then free
- **make(T)**: allocate HeapObject, zero-init data, return managed pointer
- **box(expr)**: allocate HeapObject, copy value into data, return managed pointer

Since the interpreter runs on Go (which has its own GC), we don't literally `malloc`/`free`. But we track refcounts to verify correctness — the interpreter must exhibit the same refcounting behavior that compiled Binate code would. A HeapObject with refcount 0 that isn't freed is a bug in the interpreter.

### 5.3 Execution Model

- **Environment/frame stack**: each function call pushes a frame with local variables
- **Evaluate expressions** recursively, returning `Value`
- **Execute statements** recursively, using Go control flow for Binate control flow
- **Multiple returns**: return a `[]Value` from function calls
- **Break/continue**: use Go panic/recover or a sentinel return value to unwind to the enclosing for loop

### 5.4 Builtin Operations

- `make(T)` → allocate managed, zero-init
- `make([]T, n)` → allocate managed slice of n elements
- `box(expr)` → allocate managed, copy value
- `cast(T, expr)` → integer width conversions, truncation/extension
- `bit_cast(T, expr)` → reinterpret bits
- `len(expr)` → slice length or array length

### 5.5 Standard Library / I/O

Minimal, just enough to be useful:

- **stdout**: `print(args...)` and `println(args...)` — probably interpreter builtins, not Binate functions (since we don't have variadic/interfaces in bootstrap)
- **File I/O**: `open(path, mode)`, `read(fd, buf)`, `write(fd, buf)`, `close(fd)` — exposed as builtin functions
- **Process**: `exit(code)`, `args()` (command-line args)
- **Memory**: `make`, `box` (already builtins)
- **String operations**: basic operations exposed as builtins or via slice manipulation

The exact stdlib surface will be driven by what the self-hosted compiler needs. Start minimal, add as needed.

---

## 6. Testing Strategy

### 6.1 Unit Tests

- **Lexer tests**: token sequences for representative inputs, ASI insertion, error cases
- **Parser tests**: AST structure for each grammar production, error recovery
- **Type checker tests**: valid programs, type errors, scoping, const evaluation
- **Interpreter tests**: expression evaluation, control flow, memory management

### 6.2 Integration Tests (testdata/*.bn)

Small Binate programs that exercise specific features:

```
testdata/
  hello.bn              // print, string literals
  arithmetic.bn         // integer ops, precedence
  control_flow.bn       // if/else, for, switch
  functions.bn          // calls, multiple returns
  structs.bn            // struct literals, field access
  pointers.bn           // raw pointers, &, *
  managed.bn            // @T, make, box, refcounting
  slices.bn             // [], @[], indexing, slicing, len
  types.bn              // distinct types, aliases, cast
  const.bn              // const, iota, grouped const
  packages.bn           // multi-file, import
  stress_refcount.bn    // verify refcount correctness (cycles, drops)
```

Each test program prints expected output. Test harness runs the interpreter and compares stdout.

### 6.3 Regression Tests

As bugs are found, add minimal reproduction cases. These become the permanent test suite that the self-hosted compiler will also use.

---

## 7. Implementation Order

Build incrementally. Each step produces something testable.

### Step 1: Lexer
- Token types and keyword table
- Scanner with ASI
- Lexer tests
- **Milestone**: can tokenize any bootstrap-subset `.bn` file

### Step 2: Parser — Expressions
- AST node types for expressions
- Parse expression precedence chain
- Parse primary expressions (literals, identifiers, parens, builtins)
- Parse postfix ops (dot, index, slice, call)
- Parser tests for expressions
- **Milestone**: can parse and print expression ASTs

### Step 3: Parser — Statements and Declarations
- Statements: assignment, short var decl, if, for, switch, return, break, continue, block
- Declarations: var, const (with iota), type, func
- Top-level: package, import, source file
- Parser tests
- **Milestone**: can parse complete `.bn` files into ASTs

### Step 4: Type Checker — Basics
- Type representations
- Symbol table and scoping
- Type-check expressions (operators, calls, field access)
- Type-check statements (assignment compatibility, return types)
- Type-check declarations (var, const, type, func signatures)
- **Milestone**: can type-check valid programs and reject type errors

### Step 5: Interpreter — Expressions and Simple Statements
- Runtime value representation
- Expression evaluation
- Variable storage (environments/frames)
- Assignment, short var decl
- Print builtin for testing
- **Milestone**: can evaluate expressions and print results

### Step 6: Interpreter — Control Flow
- if/else, for (all variants), switch/case
- break, continue (loop targeting)
- Function calls and returns (including multiple returns)
- **Milestone**: can run programs with control flow and functions

### Step 7: Interpreter — Structs and Types
- Struct values, field access, composite literals
- Array values, array literals
- Distinct types, aliases, cast, bit_cast
- **Milestone**: can run programs with structured data

### Step 8: Interpreter — Pointers and Memory
- Raw pointers (&, *, dot auto-deref)
- Managed pointers (@T, make, box)
- Refcount tracking (retain, release, recursive release)
- Managed and raw slices, slice expressions, len
- **Milestone**: can run programs with heap allocation and verify refcount correctness

### Step 9: Interpreter — Packages and I/O
- Multi-file package support
- Import resolution (simplified — no .bni enforcement)
- File I/O builtins
- Command-line args
- **Milestone**: can run multi-file programs that read/write files

### Step 10: Test Suite and Hardening
- Build out testdata/ suite
- Edge cases: integer overflow (wrapping), division by zero (trap), bounds checking
- Error messages with source positions
- **Milestone**: robust enough to start writing the self-hosted compiler in Binate

---

## 8. What's Explicitly NOT in the Bootstrap

Per `grammar.ebnf` [DEFERRED] annotations:

- Generics (type parameters, constraints, instantiation)
- Interfaces, `impl`, methods with receivers
- Annotations (`#[...]`)
- Variadic functions (`...T`)
- Closures / function literals
- Float types (`float32`, `float64`)
- `unsafe_index`
- `const` in types (const pointers/slices)
- Function types as values
- `.bni` interface file enforcement

These will be implemented in the self-hosted compiler/interpreter written in Binate.

---

## 9. Success Criteria

The bootstrap interpreter is complete when it can:

1. Parse and type-check non-trivial multi-file Binate programs
2. Execute them with correct semantics (including refcounting)
3. Handle all bootstrap-subset features from the grammar
4. Provide useful error messages (with file/line/column)
5. Be stable enough to serve as the platform for writing the self-hosted compiler
