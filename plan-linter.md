# Plan: Binate Linter (bnlint)

## Goal

A lint tool that catches common mistakes in Binate code — especially memory
safety footguns that are tedious to debug at runtime (use-after-free, silent
leaks). The type system allows these patterns; the linter flags them.

**Priority**: Moderate. The linter could save significant debugging time now,
especially for refcount/lifetime bugs that only manifest as crashes or leaks at
runtime. On the other hand, it's more code to maintain and the rule set will
evolve as the language matures.

## Motivation

Several classes of bugs have repeatedly come up during development:

- Returning `[]T` from a function that allocates — the caller gets a dangling
  raw slice after the managed backing is freed
- Assigning `@[]T` to `[]T` — drops the refcount, caller holds a raw slice
  whose backing may be freed
- Ignoring error returns — Go-style multiple returns mean the error is easy to
  silently discard
- Unreachable code after return — often indicates a logic error

These are all legal Binate code that the type checker accepts. A linter catches
them before they become runtime mysteries.

## Architecture

The linter piggybacks on the existing compiler frontend:

```
Source files
    │
    ▼
pkg/loader    ──→  discover packages, resolve imports
    │
    ▼
pkg/parser    ──→  parse to AST
    │
    ▼
pkg/types     ──→  type-check (provides type info for semantic rules)
    │
    ▼
pkg/lint      ──→  walk AST + type info, apply rules, collect diagnostics
    │
    ▼
cmd/bnlint    ──→  report diagnostics
```

No IR needed — linters work on AST + type information.

## Lint Rules (Initial Set)

### Memory safety (high value)

1. **`raw-slice-return`**: Function returns `[]T` but the value originates from
   a managed allocation (local `@[]T` variable, `make_slice`, etc.). The raw
   slice will dangle after the function returns and the managed backing is freed.

2. **`managed-to-raw-assign`**: Assigning a `@[]T` expression to a `[]T`
   variable (or parameter). This silently drops the managed wrapper — if the
   `@[]T` was a temporary (e.g., function return), the raw slice is immediately
   dangling. Even if not immediate, it's a code smell indicating confused
   ownership.

3. **`raw-slice-escape`**: Storing a raw slice derived from a local managed
   allocation into a struct field or global — the raw slice outlives the
   managed backing's scope.

### Correctness

4. **`unused-error`**: Function returns multiple values (value, error pattern)
   and the caller discards the error. Heuristic: second return is `bool` or
   named `err`/`ok`.

5. **`unreachable-code`**: Statements after unconditional `return`, `break`, or
   `continue`.

6. **`unused-variable`**: Declared variable never read. (The compiler may already
   warn about this, but a lint pass is more thorough.)

7. **`unused-import`**: Imported package never referenced.

### Style (lower priority)

8. **`naming`**: Enforce conventions (camelCase functions, PascalCase types,
   etc.) — defer until conventions are fully settled.

9. **`shadow`**: Variable declaration shadows an outer scope variable with the
   same name.

## Implementation

### AST Walker

The core is a recursive function that visits every AST node:

```
func walkFile(f @ast.File, ctx @LintContext)
func walkDecl(d @ast.Decl, ctx @LintContext)
func walkStmt(s @ast.Stmt, ctx @LintContext)
func walkExpr(e @ast.Expr, ctx @LintContext)
```

Each `walk*` function dispatches on the node's Kind, recurses into children, and
calls rule-check functions at appropriate points. Since the bootstrap subset
forbids function values and interfaces, rules are hardcoded in the walker (not
pluggable callbacks). This is fine — the rule set is small and known at compile
time.

### LintContext

Carries state through the walk:

```
type LintContext struct {
    Checker    @types.Checker   // for type queries
    File       @ast.File
    Diags      @[]Diagnostic
    NumDiags   int
    // per-function state
    ReturnType @types.Type      // current function's return type
    Scope      @types.Scope     // current scope (for unused tracking)
}

type Diagnostic struct {
    Pos     ast.Pos
    Rule    @[]char          // e.g. "raw-slice-return"
    Message @[]char
}
```

### Type Queries

The key rules need type information:

- `raw-slice-return`: check if function return type is `[]T`, and if the
  returned expression has managed origin
- `managed-to-raw-assign`: check if LHS type is `[]T` and RHS type is `@[]T`
- `unused-error`: check if called function's return type is a multi-return
  with error-like second value

The type checker already computes expression types during `Check()`. The linter
needs access to these — either by running after the checker and querying
resolved types, or by doing a second pass with the checker's output. The exact
mechanism depends on what `pkg/types` exposes (need to check if expression
types are stored on AST nodes or only in the checker's internal state).

**Open question**: Does the type checker annotate AST nodes with their resolved
types, or would the linter need to re-derive types? If the latter, the linter
may need to partially re-walk the type logic, which is more work.

## Package Structure

```
pkg/lint/           — lint rules and walker
    lint.bn         — walker, rule dispatch, context
    lint.bni        — public interface (LintFile, LintPackage, Diagnostic)
    rules.bn        — individual rule implementations
    lint_test.bn    — unit tests (snippets that should/shouldn't trigger)

cmd/bnlint/         — CLI
    main.bn         — argument parsing, loads packages, runs lint, prints diagnostics
```

## CLI

```
bnlint [flags] <package-or-files...>

bnlint pkg/codegen
bnlint file.bn
bnlint -rule raw-slice-return,managed-to-raw-assign pkg/ir
```

Flags:
- `-rule rule1,rule2` — only run specified rules
- `-disable rule1,rule2` — skip specified rules
- `-q` — quiet, only print summary count

Output format (one line per diagnostic):
```
pkg/foo/bar.bn:42: [raw-slice-return] function returns []char but value is from managed allocation
pkg/foo/bar.bn:87: [managed-to-raw-assign] assigning @[]uint8 to []uint8 drops managed wrapper
```

## Testing

- **Per-rule unit tests**: small code snippets that should trigger (or not
  trigger) each rule. The test calls the parser + type checker + lint walker on
  the snippet and checks diagnostics.
- **False-positive tests**: code that looks suspicious but is actually correct
  (e.g., returning a `[]T` parameter — no managed origin, no problem).
- **Integration tests**: run bnlint on the existing codebase, verify no crashes,
  review any findings.

## Incremental Steps

### Step 1: Infrastructure

Set up `pkg/lint` with the walker skeleton, `LintContext`, `Diagnostic` type,
and `cmd/bnlint` CLI that loads a package and runs the (empty) lint pass.

### Step 2: `managed-to-raw-assign`

Probably the simplest high-value rule — just check if LHS is `[]T` and RHS is
`@[]T` at assignment/short-var/parameter sites. Requires type info on
expressions.

### Step 3: `raw-slice-return`

Check function return statements where the return type is `[]T`. Flag if the
expression is a managed-slice (direct `@[]T` expression) or comes from a
managed origin (harder — may need data-flow tracking for full precision, but
simple heuristics catch the common cases).

### Step 4: `unreachable-code` and `unused-variable`

Control-flow and data-flow rules that don't need type info.

### Step 5: Remaining rules

`unused-error`, `unused-import`, `raw-slice-escape`, style rules.

## Open Questions

1. **Type annotation access**: How does the linter get expression types? Need
   to check if `pkg/types` stores resolved types on AST nodes or internally.
   This determines whether the linter runs after the checker or needs its own
   type derivation.

2. **Data-flow for `raw-slice-return`**: Simple check (returned expression is
   literally `@[]T` typed) catches the obvious cases. Tracking through variable
   assignments (`var s []T = managed[:]`) requires data-flow analysis — do we
   need that in v1?

3. **Suppression comments**: Should `// nolint:rule-name` suppress a diagnostic?
   Useful but adds complexity. Defer to v2.

4. **Running on self-hosted code**: The linter is itself written in Binate and
   subject to the bootstrap subset. It should be able to lint itself.

## Effort

- Step 1 (infrastructure): Small — mostly wiring up existing packages
- Step 2-3 (key rules): Medium — depends on type annotation accessibility
- Step 4-5 (remaining rules): Small per rule
- Total: probably 2-3 days for a useful initial linter with the top rules
