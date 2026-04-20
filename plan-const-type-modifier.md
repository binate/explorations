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
safe; dropping it isn't).

| src → dst | Allowed? |
|-----------|----------|
| `T` → `const T` | Yes (widening: adds restriction) |
| `const T` → `T` | No (requires `cast`) |
| `*T` → `*const T` | Yes |
| `*const T` → `*T` | No |
| `*[]T` → `*[]const T` | Yes |
| `*[]const T` → `*[]T` | No |
| `@[]T` → `@[]const T` | Yes |
| `@[]const T` → `@[]T` | No (requires explicit copy) |
| `@T` → `@const T` | Yes |
| `@const T` → `@T` | No |
| `const T` → `const U` (different T/U) | Only if `T` → `U` is allowed |
| `const T` → `const T` | Yes (identical) |

Deep const is NOT implied: `*[]T` → `*[]const T` is allowed because
it adds a read-only view to the elements, but `*const []T` →
`*[]const T` is still distinct (first is const slice header, second
is mutable header pointing at const elems).

Call boundaries get the same rules as assignment.

Note on `@[]const T` → `@[]T`: a managed slice of const elements
cannot be widened to a managed slice of mutable elements, because
that would let the holder mutate data that's still const from
another view. To get a mutable managed slice from const data, the
caller constructs one — either from a literal (see below) or by
writing an explicit `make_slice` + element-copy.

## Literal-init copy rule (narrow)

A string literal or composite literal on the RHS of a non-const
managed target does NOT require a prior explicit copy — the
compiler treats the initialization as construction of a fresh
managed value, not as widening of an existing const view. The two
in-scope cases:

1. **String literals**
   - `var s @[]char = "hello"` — compiler emits a fresh
     `rt.MakeManagedSlice(char, 5)` and memcpy's the static bytes in.
     `s` is mutable and independent of the literal's rodata.
   - `var s @[]const char = "hello"` — zero-copy borrow of the rodata.
     Default form; preferred.
   - `var s *[]const char = "hello"` — also zero-copy borrow.
   - `var s *[]char = "hello"` — type error (no owner for a mutable
     view of static data).

2. **Composite literals**
   - `var s @[]int = @[]int{1, 2, 3}` — the composite-literal
     construction itself allocates a managed backing and writes the
     values in; there's no prior const slice to widen from.
   - `var s @[]const int = @[]const int{1, 2, 3}` — const-typed
     composite literal; the backing is written at construction time
     (see open question below) and then sealed.

Neither of these is "widening a const slice to a non-const one" —
they're fresh construction. The rule is: **literals and composite
literals are the fresh-construction sites**, and they can target
either const or non-const without needing an external copy step.

Anything else (an existing `@[]const T` value → a `@[]T` target)
still requires an explicit `make_slice` + copy; the compiler
doesn't silently insert an allocation when the RHS is already a
first-class managed value.

**Allocation / init details** (when the rule fires):
- Backing is `rt.MakeManagedSlice(T, len)`.
- No zero-fill needed; the subsequent stores/memcpy overwrite all
  elements.
- `backing_len` matches the literal length; view `len` equals
  `backing_len`.

## Open question: composite literals with runtime elements

`@[]int{1, 2, 3}` is all-compile-time. `@[]int{1, 2, y}` (where `y`
is a runtime variable) is syntactically the same composite-literal
form but needs to evaluate `y` at allocation time. Two sub-questions:

**(a) Do we permit runtime-valued composite literals at all?** Go
permits them. Binate's grammar (`CompositeLit`) doesn't distinguish
const vs runtime element values, so the grammar already allows it.
The implementation just needs to emit a construction sequence that
evaluates each element and stores it. Low risk — recommend yes.

**(b) What about `@[]const int{1, 2, y}` — a const-typed composite
literal with runtime element values?** The slice is declared const,
meaning nobody can write to its elements through this handle after
construction. But the construction itself *is* writing `y` into the
backing. That's allowed iff we draw a line between "initial
construction" and "later mutation." C++ draws exactly this line
(`const` members get set in constructors via a member-init list;
after the object is constructed, they're immutable). I think Binate
should do the same — the composite literal IS the init, and init
can write.

Alternative: disallow runtime-valued const composite literals. Then
users would have to write `@[]int{1, 2, y}` (non-const) first and
convert — but const → non-const isn't free in the plan above, so
that's clunky.

Recommend: allow `@[]const T{...}` with runtime values. Treat the
composite-literal syntax as the sole "init-time write" path for
const targets. Add a conformance test making sure runtime element
evaluation happens before the slice is observable as const.

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
- Permitted string-literal assignment targets (per the literal-init
  copy rule above):
  - `[N]const char` / `[N]char` — array copy.
  - `@[]const char` / `*[]const char` — zero-copy borrow of static
    data (ideal; preferred form).
  - `@[]char` — literal-init copy: fresh allocation, memcpy the
    bytes in, the result is independent of rodata.
  - `*[]char` — **type error. No tolerance.** A raw slice is a
    borrow; borrowing static rodata with a mutable view is
    unsound and has no correct lowering. Every `var x *[]char =
    "..."` site in existing source must be migrated before Stage 2
    can land.
- Pre-Stage-2 migration (do this *before* flipping the default):
  - Audit every `var x *[]char = "..."` / parameter `*[]char` fed
    a literal / struct-field `*[]char` initialized from a literal.
    Roughly 1500 grep hits — most are parameter declarations and
    struct fields that'll pick up the const variant transparently.
  - For each site: if the slice is only read, change to
    `*[]const char`. If it's mutated, change to `@[]char` (and fix
    callers to pass a `@[]char` or a composite/string literal).
  - Propagate const through public API surfaces (cmd/bnc, pkg/loader,
    pkg/parser, etc.) — params that only read their input become
    `*[]const char` or `@[]const char`.
  - `var x @[]char = "..."` keeps compiling via the literal-init
    copy rule, so those sites don't block the migration. Audit
    post-flip for cases that should be `@[]const char` instead of
    allocating a copy.
- Bootstrap interpreter: its checker doesn't track const (treats it
  as a keyword it accepts without enforcement). Conformance tests
  that rely on Stage 2 behavior (const-enforced literal rules) add
  `.xfail.boot` markers rather than trying to update bootstrap.
- New conformance tests:
  - `var s @[]const char = "..."` (zero-copy borrow).
  - `var s @[]char = "..."` (literal-init copy — verify the copy is
    independent of the literal via a mutation test).
  - `var s *[]const char = "..."` (zero-copy raw-slice borrow).
  - Composite-literal init: `@[]int{1, 2, 3}`, `@[]const int{1, 2, 3}`.
  - Negative: `*[]char = "..."` errors.
  - Negative: `@[]char = existingConstSlice` errors (bare widening
    without a literal — need explicit copy).

**Validation**: Stage 2 only lands once `*[]char = literal` is
zero in the tree. Then flip the default and check all modes green.

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

- **Stage 2 migration blocker**: `*[]char = "..."` is NOT tolerated
  — it's a hard type error post-flip. Every site must be migrated
  (to `*[]const char` for read-only or `@[]char` for owned-mutable)
  *before* Stage 2 lands, otherwise the whole tree stops compiling.
  Budget several sessions for this pass; the 1500 grep hits include
  parameters, struct fields, and local var decls, many of which
  will propagate const transparently but each needs a read-through.
  `@[]char = "..."` is fine — the literal-init copy rule catches it.
- **Silent allocation from string literals**: `@[]char = "..."` now
  does a quiet alloc + memcpy. Visible only as a small perf/heap
  footprint, never a correctness issue. In hot paths users should
  prefer `@[]const char = "..."` (zero-copy borrow) or construct
  via `make_slice(char, n)` + element-wise copy where the size is
  data-dependent.
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
