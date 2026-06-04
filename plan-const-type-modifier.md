# Plan: `const` Type Modifier

Status: SHIPPED. The const type modifier landed as `TYP_CONST` in
pkg/types (later renamed `readonly`). This doc is kept as the design
record for the assignability lattice, the literal-init copy rule, and
the per-encounter composite-literal allocation + UB-on-shared-rodata
decision — durable rationale not captured in code.

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
implicitly mutates string-literal-derived slices.

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

## Composite literals: per-encounter allocation

`@[]int{1, 2, 3}` and `@[]int{1, 2, y}` (where `y` is runtime) have
the **same** runtime behavior: each time the composite literal is
encountered, the compiler emits `rt.MakeManagedSlice(int, 3)` and
stores the three element values into the fresh backing. No shared
global. Reaching the same source expression twice (e.g. in a loop)
allocates twice. This keeps the semantics simple and regular —
"composite literal = fresh construction, always."

`@[]const int{1, 2, 3}` and `@[]const int{1, 2, y}` work the same
way: a fresh managed-slice is allocated and initialized at the
literal's location, then sealed as const through the returned
handle. The "const" part is about what you can do with the handle
afterwards, not about when the init happens. The composite-literal
syntax IS the one init-time write path for const-typed targets.

### Shared static storage is an optimization

The compiler is *permitted* to detect composite literals with
all-compile-time-constant element values and lower them to a shared
static global (allocated once, at program load, stable address).
This is the optimization that makes `"hello"` free. But the
language spec does not require it, and programs may observe the
difference: `&a[0] == &b[0]` where `a, b` are both
`@[]const char{"hello"}` (or equivalent) is `true` under the
optimization and `false` without it.

We accept this as **undefined behavior** in the language spec:
comparing raw pointers produced by separate composite-literal
evaluations yields implementation-defined values, and programs
relying on either outcome are ill-formed.

This is the same contract Binate has tacitly adopted elsewhere
(e.g., refcounting move optimizations are already observable
through `rt.Refcount(...)` in a way the spec doesn't nail down).
Rather than chase an observable-behavior-parity guarantee that
would preclude optimizations, we accept UB at the few
opt-observable seams and trust programmers to not rely on the
details.

This means string literals — `[N]const char` arrays with all-const
byte values — become just a special case of the general rule: the
compiler emits them as shared static globals, which is observable
but falls under the UB above. Today's `OP_STRING_TO_CHARS` static
`%BnManagedSlice` global already exploits exactly this.

There's a broader policy question here — "what other observable
optimizations does Binate permit, with UB as the escape hatch?" —
that goes beyond this plan. See TODO in `claude-todo.md`.

### Summary of init behavior

| Form | Allocation |
|------|------------|
| `"hello"` (string literal, rodata) | Shared global (optimization; UB to rely on address) |
| `@[]const char{"hello"}` (hypothetical, all-const) | Compiler MAY share; UB to rely on |
| `@[]int{1, 2, 3}` (all-const elements) | Fresh per encounter; compiler MAY share |
| `@[]int{1, 2, y}` (runtime element) | Fresh per encounter; cannot share |
| `@[]const int{1, 2, y}` | Fresh per encounter, sealed as const |

## String literals

The spec (`claude-notes.md:325`,
`claude-discussion-detailed-notes.md:910`) says:

- Natural type: `[N]const char` (the literal stored in rodata).
- Default type: `@[]const char` (managed-slice view into static data;
  the managed header is cheap because "static" is refcount-exempt).
- `*[]const char` is also permitted (raw slice view).

This is the step with the most fallout. Originally:

- The type checker returned `TypString()` (a distinct `TYP_STRING`
  singleton) for string literals.
- Source code widely declared `var s *[]char = "..."` and
  `var s @[]char = "..."` — roughly 1500 grep matches in the self-host
  tree.

With the implicit-copy rule above, the migration story is:

- `var s @[]char = "..."` keeps compiling — literal is
  `@[]const char`, the implicit allocate+copy rule fires, `s` gets a
  fresh owned copy. Behavior is *more* correct than before (which
  aliased static data under some paths).
- `var s *[]char = "..."` becomes a type error. Users must change to
  `*[]const char` (if the slice is only read) or migrate to `@[]char`
  / `@[]const char`. A raw slice is a borrow; borrowing static rodata
  with a mutable view is unsound and has no correct lowering. Every
  such site in existing source must be migrated.
- `var s @[]const char = "..."` is the preferred form and is free
  (no allocation).

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
smaller. `cast` always allows const-drop as part of the conversion,
even when combined with a width/pointer-target change (so
`cast(*int, p)` where `p` is `*const int` does the
pointer-target-change and the const-drop in one step).

`bit_cast` is unchanged (already allowed to reinterpret anything).

## Scope

In scope:
- `TYP_CONST` kind + AST, parser, type-checker, assignability.
- Enforcement of const at assignment sites.
- `cast` drops const.
- String-literal default-type change.

Out of scope (noted for later):
- Const method receivers (depends on methods). Once the methods /
  interfaces feature lands (separate plan), extend the receiver-type
  handling to include `*const T`. Parsing already allows const
  receiver types.
- Const on function return types as a distinct enforcement path
  (covered trivially by assignability; methods may want more).
- Deep const / const-as-immutability (Binate's const is shallow,
  per design).
- Runtime enforcement (const is a compile-time concept only).

## Risks & open questions

- **String-literal migration blocker**: `*[]char = "..."` is NOT
  tolerated — it's a hard type error post-flip. Every site must be
  migrated (to `*[]const char` for read-only or `@[]char` for
  owned-mutable) *before* the string-literal default flips, otherwise
  the whole tree stops compiling. The ~1500 grep hits include
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
- **Composite literals**: `[4]const int{1, 2, 3, 4}` — the elements
  aren't modifiable post-init, but init itself is how the array
  receives its values. No enforcement issue, but worth a test.
