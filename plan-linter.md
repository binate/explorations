# Plan: Binate Linter (bnlint)

Status: SHIPPED. The linter exists as `pkg/binate/lint` + `cmd/bnlint`, with
`raw-slice-return`, `managed-to-raw-assign`, `func-value-escape`,
`managed-func-raw-capture`, `uninitialized-readonly-global`, and `unused-import`
rules implemented and tested. This doc is kept for the design rationale (the
footgun each rule targets) and the still-open v2 items below; the shipped
architecture/walker/CLI mechanics are redundant with the code and have been
trimmed.

## Goal

A lint tool that catches common mistakes in Binate code — especially memory
safety footguns that are tedious to debug at runtime (use-after-free, silent
leaks). The type system allows these patterns; the linter flags them.

## Motivation

Several classes of bugs have repeatedly come up during development:

- Returning `*[]T` from a function that allocates — the caller gets a dangling
  raw slice after the managed backing is freed
- Assigning `@[]T` to `*[]T` — drops the refcount, caller holds a raw slice
  whose backing may be freed
- Ignoring error returns — Go-style multiple returns mean the error is easy to
  silently discard
- Unreachable code after return — often indicates a logic error

These are all legal Binate code that the type checker accepts. A linter catches
them before they become runtime mysteries.

## Rule Rationale (the footgun each rule targets)

### Memory safety (high value)

1. **`raw-slice-return`**: Function returns `*[]T` but the value originates from
   a managed allocation (local `@[]T` variable, `make_slice`, etc.). The raw
   slice will dangle after the function returns and the managed backing is freed.

2. **`managed-to-raw-assign`**: Assigning a `@[]T` expression to a `*[]T`
   variable (or parameter). This silently drops the managed wrapper — if the
   `@[]T` was a temporary (e.g., function return), the raw slice is immediately
   dangling. Even if not immediate, it's a code smell indicating confused
   ownership.

3. **`raw-slice-escape`** (not yet shipped): Storing a raw slice derived from a
   local managed allocation into a struct field or global — the raw slice
   outlives the managed backing's scope.

### Correctness

4. **`unused-error`** (not yet shipped): Function returns multiple values (value,
   error pattern) and the caller discards the error. Heuristic: second return is
   `bool` or named `err`/`ok`.

5. **`unreachable-code`** (not yet shipped): Statements after unconditional
   `return`, `break`, or `continue`.

6. **`unused-variable`** (not yet shipped): Declared variable never read. (The
   compiler may already warn about this, but a lint pass is more thorough.)

7. **`unused-import`**: Imported package never referenced.

### Style (lower priority)

8. **`naming`** (not yet shipped): Enforce conventions (camelCase functions,
   PascalCase types, etc.) — defer until conventions are fully settled.

9. **`shadow`** (not yet shipped): Variable declaration shadows an outer scope
   variable with the same name.

## Open Questions / Deferred (v2)

1. **Data-flow for `raw-slice-return`**: The simple check (returned expression is
   literally `@[]T` typed) catches the obvious cases. Tracking through variable
   assignments (`var s *[]T = managed[:]`) requires data-flow analysis — deferred
   from v1.

2. **Suppression comments**: Should `// nolint:rule-name` suppress a diagnostic?
   Useful but adds complexity. Deferred to v2.

3. **Remaining unshipped rules**: `raw-slice-escape`, `unused-error`,
   `unreachable-code`, `unused-variable`, plus the style rules (`naming`,
   `shadow`).
