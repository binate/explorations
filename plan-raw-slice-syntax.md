# Plan: Raw Slice Syntax Change (`[]T` → `*[]T`)

## Motivation

Currently, raw slices are written `[]T` and managed-slices are `@[]T`. This proposal
changes raw slice syntax to `*[]T`, making the raw/managed distinction parallel for
both pointers and slices:

| | Raw | Managed |
|---|---|---|
| Pointer | `*T` | `@T` |
| Slice (new) | `*[]T` | `@[]T` |
| Slice (old) | `[]T` | `@[]T` |

**Why:**
- The `*`/`@` prefix pattern is consistent: `*` = raw/unmanaged, `@` = managed/refcounted.
- Visually signals that raw slices are "raw" — they don't own their data, and the
  programmer is responsible for lifetime management. This is important because raw
  slices behave very differently from Go slices.
- The current `[]T` syntax looks like Go slices, which are garbage-collected and
  own their data. `*[]T` makes the difference immediately visible.

## Disambiguation Rule

**`*` or `@` immediately before `[` is only valid as slice sugar.** To express
"pointer to array" or "pointer to slice," parentheses are required:

| Meaning | Syntax |
|---|---|
| Raw slice of T | `*[]T` |
| Managed-slice of T | `@[]T` |
| Pointer to raw slice | `*(*[]T)` |
| Pointer to managed-slice | `*(@[]T)` |
| Managed pointer to raw slice | `@(*[]T)` |
| Pointer to array | `*([N]T)` |
| Managed pointer to array | `@([N]T)` |

The rule is narrow: only `*[` and `@[` trigger the requirement. `**T` (pointer to
pointer) does NOT require parens — there's no ambiguity there.

Note: `@[` already has this property — `@[]T` is managed-slice sugar, and
`@([N]T)` requires parens. This change extends the same rule to `*[`.

## Stages

### Stage 0: Require parens for `*[` (reclaim the syntax)

Currently `*[]T` means "raw pointer to raw slice" and `*[N]T` means "raw pointer
to array of N T." Before repurposing `*[]T`, we must:

1. Update grammar to reject bare `*[` — require `*([]T)` and `*([N]T)`.
2. Update bootstrap parser + type checker to enforce this.
3. Update self-hosted compiler parser + type checker.
4. Update self-hosted interpreter parser + type checker.
5. Migrate all existing code that uses `*[]T` or `*[N]T` to the parenthesized form.

This stage can be done in substages (e.g., bootstrap first, then self-hosted tools,
then code migration).

**Expected impact**: minimal. `*[]T` (pointer to raw slice) is rare in the codebase.
`*[N]T` (pointer to array) may appear in some low-level code.

### Stage 1: Add `*[]T` as raw slice syntax (alongside `[]T`)

1. Update grammar: `*[]T` is now an alternative syntax for raw slices.
2. Update bootstrap parser to accept `*[]T` as raw slice type.
3. Update self-hosted compiler parser.
4. Update self-hosted interpreter parser.
5. Update `.bni` interface files to accept both syntaxes.

Both `[]T` and `*[]T` are valid raw slice syntax during this stage.

### Stage 2: Migrate all code to `*[]T`

Systematically replace `[]T` with `*[]T` across:
- `binate/` (self-hosted compiler, interpreter, all packages)
- `bootstrap/` (Go-based interpreter — string representations, error messages, etc.)
- `explorations/` (grammar, docs, plans)
- Conformance tests

This can be done in many small commits. The test suite verifies correctness at each step.

### Stage 3: Remove `[]T` syntax

1. Update grammar: remove `[]T` as a type form.
2. Update bootstrap parser to reject `[]T`.
3. Update self-hosted compiler parser.
4. Update self-hosted interpreter parser.
5. Final audit for any remaining `[]T` references.

## Type Declaration Impact

`type Buffer []uint8` becomes `type Buffer *[]uint8`. The `*[]` is syntactic sugar
for "raw slice," so the type is still a raw slice — the `*` is part of the slice
syntax, not a pointer indirection.

## Generics Interaction

The `@[]` sugar rule applies: `@T` where `T=*[]int` means `@(*[]int)` (managed
pointer to raw slice), not `@[]*int` or anything else. The `*[]` sugar works the
same way: `*T` where `T=[3]int` means `*([3]int)` (pointer to array), not
`*[]...` (raw slice). Sugar only applies to literal syntax, not type parameter
substitution.

## Open Questions

- Should error messages and docs say "raw slice" or "pointer-slice"? Recommendation:
  keep "raw slice" — the term is established and the `*` prefix reinforces "raw."
- Should the linter flag `[]T` during Stage 2 (migration period)?
