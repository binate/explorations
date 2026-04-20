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

Const introduces a partial order: `T ≤ const T` (adding const is
safe; dropping it isn't). **But**: for managed types, dropping
const at initialization is allowed as an implicit allocate+copy —
see "Implicit copy-on-init" below.

| src → dst | Allowed? |
|-----------|----------|
| `T` → `const T` | Yes (widening: adds restriction) |
| `const T` → `T` | No (requires `cast`) for raw types / values |
| `*T` → `*const T` | Yes |
| `*const T` → `*T` | No (raw ptr is a borrow — no owner for a copy) |
| `*[]T` → `*[]const T` | Yes |
| `*[]const T` → `*[]T` | No (same — raw slice is a borrow) |
| `@[]T` → `@[]const T` | Yes |
| `@[]const T` → `@[]T` at init only | **Yes, with implicit copy** |
| `@[]const T` → `@[]T` at bare assignment | No (requires explicit copy) |
| `@T` → `@const T` | Yes |
| `@const T` → `@T` at init only | **Yes, with implicit copy** |
| `@const T` → `@T` at bare assignment | No |
| `const T` → `const U` (different T/U) | Only if `T` → `U` is allowed |
| `const T` → `const T` | Yes (identical) |

Deep const is NOT implied: `*[]T` → `*[]const T` is allowed because
it adds a read-only view to the elements, but `*const []T` →
`*[]const T` is still distinct (first is const slice header, second
is mutable header pointing at const elems).

Call boundaries get the same rules as assignment. Passing a
`@[]const char` to a `@[]char` parameter triggers implicit copy at
the call site (same as initialization).

## Implicit copy-on-init for managed types

Forcing users to write `buf.CopyStr("...")` every time they want a
mutable managed-slice initialized from a string literal — or more
generally, from any `@[]const T` — would make ordinary code
annoying and push people away from const-correct APIs. So the
language allows an implicit allocate + memcpy at **initialization
sites only**, for managed targets:

- `var s @[]T = constExpr` where `constExpr` has type `@[]const T` or
  the natural type of a string literal (`[N]const char` defaulting to
  `@[]const char`): compiler emits `rt.MakeManagedSlice(...)` sized
  to match, memcpy's the source bytes, and stores the fresh managed
  slice.
- Same for `@T` target from `@const T` source (allocate a fresh
  managed payload, memcpy T-sized bytes).
- Function argument passing where the param type is `@[]T` / `@T`
  and the argument type is the const variant: same implicit copy at
  the call site.
- Short-var-decl `s := constExpr` infers the const type (no copy
  unless an explicit type annotation asks for the non-const form).

**Not allowed**:

- Bare assignment `s = constExpr` where `s` is already `@[]T`.
  Rationale: the assignment semantics are save-copy-destroy on the
  existing value. Mixing in a fresh allocation from a const source
  is confusing. Users can write `s = buf.CopyStr(constExpr)`
  explicitly.
- Raw slice / raw pointer targets (`*[]T`, `*T`). These are borrows
  — no owner for a heap copy. The existing rules apply: `cast` to
  drop const, or re-borrow from a separate owning storage.

**Zero-init**: the allocated backing for `@[]T` doesn't need to be
zeroed before the memcpy, since the memcpy immediately overwrites
all `backing_len` bytes. Skip the zero-fill for this path.

This rule generalizes: it's not string-specific. `@[]int` can be
initialized from `@[]const int` the same way. Users who want to
avoid silent allocations can still use `*[]const T` (a borrow) where
they don't need ownership.

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

With the implicit-copy rule above, the migration story is:

- `var s @[]char = "..."` keeps compiling — literal is
  `@[]const char`, the implicit allocate+copy rule fires, `s` gets a
  fresh owned copy. Behavior is *more* correct than today (which
  aliases static data under some paths).
- `var s *[]char = "..."` becomes a type error. Users must change to
  `*[]const char` (if the slice is only read) or migrate to `@[]char`
  / `@[]const char`.
- `var s @[]const char = "..."` is the preferred form and is free
  (no allocation).

**Transitional tolerance**: until the full migration is audited, we
can keep the existing buggy `@[]char` = literal behavior compiling
as-is under a compat path — i.e., emit the implicit copy even if it
changes previously-aliased code to previously-never-mutated code.
Worst case a former alias-read is now a copy-read — same bytes, just
slower. Worst case a former alias-write "succeeded" into static data
— now it writes into the copy, which is safer.

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

`cast` handles drop-const explicitly for raw types where no copy
is possible. For managed types, the implicit copy-on-init rule
covers the common ergonomic case. No new keyword.

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

Flip the default type of string literals from the current
`TYP_STRING` / `*[]char` / `@[]char` accommodations to the spec'd
`[N]const char` natural type with `@[]const char` default.

- Type checker: `checkIdent` on `EXPR_STRING_LIT` returns
  `[N]const char` (array type) as the natural type, resolving to
  `@[]const char` when no explicit target drives a different choice.
- `AssignableTo` for string literals:
  - `[N]const char` / `[N]char` — array copy (existing rule).
  - `@[]const char` / `*[]const char` — no-copy borrow of static
    data (the ideal form).
  - `@[]char` — allocate + copy via the general implicit-copy-on-init
    rule. Not special to strings; it's the same rule that lets
    `@[]int = constIntSlice` work.
  - `*[]char` — type error. Users migrate to `*[]const char` (if
    read-only) or switch to `@[]char`.
- Migration of existing self-host source:
  - `var x @[]char = "..."` — keeps compiling via implicit copy.
    No code change required, though we should audit for cases where
    the programmer *meant* `@[]const char` (most of them) and
    downgrade to avoid the allocation.
  - `var x *[]char = "..."` — now a type error. Audit each site:
    read-only → `*[]const char`; needs mutation → migrate to
    `@[]char` (which now auto-copies) and drop the raw-slice view.
    Roughly 1500 grep hits across the tree (many are parameter
    declarations that will pick up the const form transparently
    once their callers pass const slices).
  - Function parameters: walk the public API surface of cmd/bnc,
    pkg/loader, pkg/parser, etc. Params that read their input string
    become `*[]const char` or `@[]const char`.
- **Transitional tolerance**: until the per-site audit is done, the
  implicit-copy rule acts as a safety net — most existing code keeps
  working, just with a small perf hit on init. That buys time for
  the cleanup to land incrementally.
- Bootstrap interpreter: its checker doesn't track const (treats it
  as a keyword it accepts without enforcement). Conformance tests
  that rely on Stage 2 behavior (string literal → const variants)
  add `.xfail.boot` markers rather than trying to update bootstrap.
- New conformance tests:
  - `var s @[]const char = "..."` (zero-copy borrow).
  - `var s @[]char = "..."` (implicit copy — verify the copy is
    independent of the literal via a mutation test).
  - Generalized implicit copy for non-char: `@[]int = someConstInts`.
  - Negative: `*[]char = "..."` errors.

**Validation**: all modes still green after migration. This is the
stage most likely to expose real mutation-of-literal bugs, but the
implicit-copy rule softens the landing.

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
  `*[]char`. The implicit-copy-on-init rule means most `@[]char` =
  literal sites keep compiling without change (at a small perf
  cost). `*[]char` = literal sites are the ones that break and need
  to migrate to `*[]const char` (read-only) or `@[]char` (mutable).
  Parameter types should mostly pick up `const` transparently once
  callers pass const slices.
- **Silent allocations via implicit copy**: initializing `@[]T` from
  `@[]const T` now quietly allocates. This is visible only as a
  small perf/heap footprint, never a correctness issue. If it ever
  becomes a concern (hot loops initializing large managed slices
  from const sources), users can explicitly construct via
  `make_slice(T, len) + element-wise copy`, or the const source can
  be re-typed as `@[]const T` at the callee to avoid the conversion
  site altogether.
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
