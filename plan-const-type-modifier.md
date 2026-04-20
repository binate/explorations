# Plan: `const` Type Modifier

## Context

The grammar (`grammar.ebnf:267`) reserves `const Type` as a
type-expression modifier:

```
Type = ... | "const" Type
```

It's nowhere else in the surface language today — the `const` keyword
only parses in `var` / `param` declarations. This plan adds it as a
first-class type modifier, matching the design in
`claude-discussion-detailed-notes.md §5.3` (around lines 781-812) and
the decisions scattered through `claude-notes.md` (string literals at
325, `*[]const char` at 327, etc.).

The rollout looks small at the syntax level but fans out through
assignability rules, string-literal defaults, and existing code that
implicitly mutates string-literal-derived slices. This plan breaks it
into four stages so each piece lands with a clean validation.

## Semantics (what `const T` means)

- `const T` is a read-only *view* of `T`. The underlying value may be
  mutable through some other path; const is a promise about **this
  access path only**.
- `const T` has the same bit representation, size, and alignment as
  `T`. It's a type-system concept, not a layout concept. Codegen and
  the runtime never need to know about const.
- `const` is a type modifier: it composes. `const *const int` is
  a const pointer to a const int. `*[]const T` is a raw slice of const
  T (the slice header is mutable but the elements aren't). `const
  *[]T` is a const slice header whose elements are mutable.
- `const` does **not** affect struct field storage or layout — a
  struct with `const` fields is still writable at zero-initialization
  / composite-literal init time, but not via field assignment after.

## AST / type representation

Add a new type kind `TYP_CONST` with `Elem` pointing at the inner
type. Rationale:

- Matches the existing wrapper pattern (`TYP_POINTER`, `TYP_MANAGED_PTR`,
  `TYP_SLICE`, `TYP_MANAGED_SLICE` all have `.Elem`).
- Composable: `const *const int` = `MakeConst(MakePtr(MakeConst(Int)))`.
- Leaves the existing `Type` struct alone — no extra `Const bool`
  field that could forget to be checked.

AST: add `TEXPR_CONST` kind with `.Base` = inner TypeExpr. (`const` is
already an existing keyword, so no lexer change.)

Helper funcs:
- `types.MakeConstType(inner) @Type`
- `types.IsConst(t) bool` — reports whether `t`, directly or through
  an alias, is `const`.
- `types.StripConst(t) @Type` — returns the inner non-const type;
  `StripConst(const *const int)` = `*const int` (peels outer layer).

## Assignability rules

Const introduces a partial order on types: `T ≤ const T` (adding
const is safe; dropping it isn't).

| src → dst | Allowed? |
|-----------|----------|
| `T` → `const T` | Yes (widening: adds restriction) |
| `const T` → `T` | No (requires `cast`) |
| `*T` → `*const T` | Yes |
| `*const T` → `*T` | No |
| `*[]T` → `*[]const T` | Yes |
| `*[]const T` → `*[]T` | No |
| `@[]T` → `@[]const T` | Yes |
| `@[]const T` → `@[]T` | No |
| `const T` → `const U` (different T/U) | Only if `T` → `U` is allowed |
| `const T` → `const T` | Yes (identical) |

Deep const is NOT implied: `*[]T` → `*[]const T` is allowed because
it adds a read-only view to the elements, but `*const []T` →
`*[]const T` is still distinct (first is const slice header, second
is mutable header pointing at const elems).

Call boundaries get the same rules as assignment:
`func f(p *const int)` accepts `*int` or `*const int` arguments.

### Identity

`const T` and `T` are **distinct** as named types for method dispatch
(once methods land — `func (p *const Point) ...` is a different method
set from `func (p *Point) ...`). But they're structurally equivalent
for `Identical` purposes on assignable widening (see table above).

## `cast` drops const

`cast(T, x)` on an expression of type `const U` (for numerically /
structurally compatible `T`/`U`) drops the const. No separate
`const_cast` keyword — `cast` is already the "I know what I'm doing"
operator and extending it to cover const-drop keeps the language
smaller.

`bit_cast` is unchanged (already allowed to reinterpret anything).

## String literals

The spec (`claude-notes.md:325`,
`claude-discussion-detailed-notes.md:910`) says:

- Natural type: `[N]const char` (the literal stored in rodata).
- Default type: `@[]const char` (managed-slice view into static data;
  the managed header is cheap because "static" is refcount-exempt).
- `*[]const char` is also permitted (raw slice view).

This is the step with the most fallout. Today:

- The type checker returns `TypString()` (a distinct `TYP_STRING`
  singleton) for string literals.
- Source code widely declares `var s *[]char = "..."` and
  `var s @[]char = "..."` — roughly 1500 grep matches in the self-host
  tree.

The full spec change (string literals are `[N]const char`) is big. To
land it safely, the string-literal-default flip is deferred to Stage 2
below and blocked behind an explicit design check.

## `const_cast` is not needed

`cast` handles drop-const explicitly. No new keyword.

## Staged rollout

### Stage 0 — Syntax and plumbing, no semantic changes

- Lexer: `const` is already a keyword. No change.
- AST: add `TEXPR_CONST` with `.Base`. Parser: in `parseType`, if the
  current token is `CONST`, consume it and wrap the following type
  in a `TEXPR_CONST` node. Already permitted positions: anywhere a
  type-expression is valid (function params, results, struct fields,
  var decls, slice/array element types). Grammar-level accepts
  everywhere without per-position policing.
- Types: add `TYP_CONST` kind with `.Elem`. `MakeConstType`,
  `IsConst`, `StripConst` helpers in pkg/types. Add to
  `Identical` / `ResolveAlias` / `TypeName` / `SizeOf` / `AlignOf` —
  all transparent (delegate to `.Elem`).
- Type checker: `resolveTypeExpr` handles `TEXPR_CONST` → wrap the
  inner resolved type.
- `AssignableTo` and `commonType`: for now, treat `const T` and `T` as
  interchangeable (permissive — same as not-yet-enforcing). This
  avoids breaking any existing `const`-using code at once.
- Tests: parser tests for `const int`, `const *int`, `*const int`,
  `*[]const int`. Type-system tests for Identical / TypeName.
- No codegen or VM changes — const is invisible to backends.

**Validation**: full boot-comp + boot-comp-int green. No user-facing
behavior change.

### Stage 1 — Enforcement: disallow drops

- `AssignableTo`: implement the widening-only rules above. Source can
  gain const, not lose it.
- `cast(T, e)`: allow cast to explicitly drop const. Implementation:
  if the src type resolves through const to something assignable to
  the dst type, accept.
- Struct field assignment: if the field type is `const T` (or the
  selector chain goes through a `const` pointer / slice), reject the
  assignment. E.g. `p.x = 5` where `p: *const Point` errors.
- Index assignment: `s[i] = v` where `s: *[]const T` or
  `s: @[]const T` errors.
- Deref assignment: `*p = v` where `p: *const T` errors.
- Tests: negative conformance tests that try each of the disallowed
  mutations and expect type errors.

**Validation**: anywhere existing self-host code depended on implicit
const-drop, fix with explicit `cast` or by threading const through
properly. Expect this to surface some minor fixes but not wholesale
migration since const is not yet propagated from string literals.

### Stage 2 — String literal default type change

This is the risky stage. It flips the default type of string literals
from the current `TYP_STRING` / `*[]char` / `@[]char` accommodations
to the spec'd `@[]const char`.

- Type checker: `checkIdent` on `EXPR_STRING_LIT` returns an untyped
  string-literal type (or `@[]const char` directly) instead of
  `TypString()`.
- `AssignableTo` for string literals:
  - `[N]const char` (natural) → `[N]const char` / `[N]char` (array
    copy; the natural form already carries the length).
  - Default → `@[]const char` or `*[]const char` at use-site
    resolution.
  - `@[]char` (from a literal) requires `buf.CopyStr(...)` — the
    implicit-copy path is NOT automatic.
- Migrate self-host source: find every `var x *[]char = "..."`,
  `var x @[]char = "..."`, and cross-check whether the variable is
  actually mutated. Non-mutated → change to `*[]const char` /
  `@[]const char`. Mutated → wrap the RHS in `buf.CopyStr(...)`.
- Bootstrap interpreter parsing: bootstrap already doesn't care about
  const (it's just a keyword it doesn't enforce), but ensure the Go
  checker matches. If needed, add an xfail marker for boot-mode
  conformance tests that use the new const defaults.
- New conformance tests covering string-literal → const-slice flows.

**Validation**: all modes still green after migration. This is the
stage that's most likely to expose real mutation-of-literal bugs.

### Stage 3 — Methods with const receivers (deferred)

Once the methods / interfaces feature lands (separate plan), extend
the receiver-type handling to include `*const T`. Not part of this
plan. Parsing already allows const receiver types at Stage 0.

## Scope

In scope:
- `TYP_CONST` kind + AST, parser, type-checker, assignability.
- Enforcement of const at assignment sites.
- `cast` drops const.
- String-literal default-type change (Stage 2).

Out of scope (noted for later):
- Const method receivers (depends on methods).
- Const on function return types as a distinct enforcement path
  (covered trivially by assignability; methods may want more).
- Deep const / const-as-immutability (Binate's const is shallow,
  per design).
- Runtime enforcement (const is a compile-time concept only).

## Risks & open questions

- **Stage 2 migration size**: 1500 grep hits for `@[]char` /
  `*[]char`. Many are parameter types that will pick up const
  transparently. The actual mutation sites are a smaller subset, but
  need auditing one by one. Budget: a session's worth.
- **Bootstrap parity**: the bootstrap checker doesn't track const. If
  enforcement diverges between self-host and bootstrap, we need
  `.xfail.boot` markers for the gap rather than updating bootstrap
  (per project policy: bootstrap changes are bug-fix only).
- **`const_cast` ergonomics**: using `cast(*int, p)` where `p` is
  `*const int` does both a pointer-target-change and a const-drop.
  If the compiler rejects this combined form, we'll need to spell it
  as `cast(*int, cast(*int, p))` — ugly. Stage 1 decision: `cast`
  always allows const-drop as part of the conversion, even when
  combined with a width/pointer-target change.
- **Composite literals**: `[4]const int{1, 2, 3, 4}` — the elements
  aren't modifiable post-init, but init itself is how the array
  receives its values. No enforcement issue, but worth a test.

## Status

Planning only. No code written.
