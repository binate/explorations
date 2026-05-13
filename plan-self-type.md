# Plan: `Self` Type in Interface Declarations

> **Status: DRAFT 2026-05-12.**  Implementation plan for the
> `Self` type ratified in `claude-notes.md` § "`Self` type in
> interface declarations — DECIDED 2026-05-12".  Four slices,
> each independently landable.

## Context

`Self` is a reserved type identifier valid only inside
interface declarations.  Inside `interface I { ... }`, `Self`
refers to the eventual implementing type.  At impl-collection
time (`impl T : I { ... }`), each occurrence of `Self` in I's
signature is substituted with T.

Full design pinned in `claude-notes.md` § "`Self` type in
interface declarations — DECIDED 2026-05-12".  Resolved
decisions relevant to implementation:

- `Self` allowed in interface-method argument and result
  positions, including composites (`*Self`, `@Self`,
  `*[]Self`, `@[]Self`, tuple results containing `Self`).
- `Self` NOT allowed in receiver positions (the receiver is
  always the implementing type implicitly).
- `Self` NOT allowed in interface-extension parent lists.
- `Self`-using methods are **rejected** when called through an
  interface value (Rust's "object-safe" trait restriction).
  Callable only through generic constraints where T is
  statically known.
- `Self` in struct types (`type Foo struct { next *Self }`)
  is **deferred** — sugar that's not motivated yet.

## Slices

### Slice S.1 — Parser + AST

- Add a new `TEXPR_SELF` kind to `pkg/ast.bni` for type
  expressions, or reuse `TEXPR_NAMED` with a flag.  Lean
  toward `TEXPR_SELF` — cleaner pattern matching downstream,
  no risk of `Self` accidentally colliding with a user type
  named `Self` (which we'll reject at the type checker, but
  the AST should distinguish).
- Lexer: `Self` becomes a reserved word.  Verify it's not
  already in use anywhere in the existing self-hosted code
  (it shouldn't be — `Self` capitalized is unusual).
- Parser: accept `Self` in type-expression positions.  No
  context restriction at parse time — let the type checker
  enforce "only inside interface bodies."  This matches how
  other context-sensitive type errors are surfaced today.
- Tests: parser unit tests for `Self` as a type-expression
  shape and inside composites (`*Self`, `@[]Self`).

**Estimated size**: ~50 lines + tests.

### Slice S.2 — Type checker: interface decl + impl collection

- When type-checking an interface declaration's methods:
  - Validate `Self` appears only in argument / result
    positions of method signatures (reject in receivers,
    parent lists, anywhere else).
  - For each method, compute and stash a `UsesSelf` bool —
    true iff `Self` appears anywhere in the method's
    non-receiver signature.  Used by Slice S.3.
- When type-checking an `impl T : I { ... }` declaration:
  - For each method I requires, build the "expected signature"
    by substituting T for every `Self` occurrence in I's
    method signature.
  - Validate the impl's method declaration matches the
    expected signature.  Existing impl-vs-interface signature
    check just needs to consume the substituted signature
    instead of the raw one.
- New helper: `substituteSelf(sig @Type, recv @Type) @Type` —
  walks the FuncType, replacing every `TYP_SELF` occurrence
  with `recv`.  Recurses into composites (`*Self`,
  `@[]Self`, etc.).
- A `TYP_SELF` kind on `@Type` parallels `TEXPR_SELF` on the
  AST side.  Carries no payload — identity is enough.
- Tests: interface with Self in arg, in result, in `*Self`,
  in `@[]Self`; impl matching; impl mismatch (wrong type
  substituted); impl missing methods; Self-in-receiver
  (rejected at decl); Self-in-extension-parents (rejected).

**Estimated size**: ~200 lines + tests.

### Slice S.3 — Type checker: interface-value call rejection

- At method-call-on-interface-value sites, after resolving
  the method (via the existing iv-dispatch path that already
  walks inherited methods per
  `plan-interface-embedding.md`):
  - If the resolved method's `UsesSelf` flag is true, emit
    a clean error: "method `Foo` uses `Self` and cannot be
    called through an interface value; use a generic
    constraint instead."
- At method-call-on-type-param-value sites (post-generics):
  - Substitute the constraint's `Self` with the type param,
    then the type param with its instantiated concrete type
    at monomorphization.  No interaction with this slice —
    generics will hook into S.2's `substituteSelf` helper.
- Tests: positive call through iv (non-Self method, works);
  negative call through iv (Self method, rejected with the
  pinned error message); positive call through generic
  constraint (defer until generics — covered in
  `plan-generics.md` Slice 3 tests).

**Estimated size**: ~80 lines + tests.

### Slice S.4 — Conformance + docs

- Conformance tests: end-to-end programs using `Self`
  through iv (positive non-Self path) and through impl-
  collection (validation works).  Negative conformance:
  iv call to Self-using method (rejection at type check).
- Update `claude-notes.md` § "Interfaces" to mention `Self`
  in the canonical-design list (currently the section only
  lists `any` as a built-in implicit; Self is a separate
  concept and deserves its own bullet in the design
  summary).
- Update `bootstrap-subset.md` if `Self` is excluded from
  the bnc-tree subset (it likely is, since self-hosted code
  doesn't need it).

**Estimated size**: ~100 lines mostly tests + doc updates.

## Slicing order rationale

- S.1 first because every later slice depends on the AST
  shape stabilizing.
- S.2 next because impl validation is the immediate value-
  add — once it lands, the upcoming `pkg/std` work can write
  `interface Comparable { Compare(other Self) int }` and
  start drafting impls without waiting for the iv-rejection
  story.
- S.3 next because the iv-rejection is the safety bound — we
  shouldn't let users call Self-methods through iv even
  briefly (they'd hit confusing dispatch errors).
- S.4 last; sweeps up edge cases and docs.

Each slice should be one commit, each one keeping conformance
and unit tests green.

## Implementation notes / open questions

1. **`TYP_SELF` identity.**  Across one interface declaration,
   all `Self` references denote the same type-parameter
   slot.  Across two interface declarations (e.g., when
   `interface Hashable : Comparable` extends Comparable),
   each interface's `Self` refers to the implementing type
   of *that* interface — but since both must be implemented
   by the same T, they're effectively the same.  Likely no
   cross-interface bookkeeping needed; `TYP_SELF` is a
   singleton resolved per-impl-context.

2. **Substitution under aliases.**  `interface MyComparable
   = Comparable` — the alias inherits Comparable's Self.
   Substitution walks through alias chains.  Already handled
   by `ResolveAlias` patterns in the type checker, but
   verify the Self-substitution helper does too.

3. **Composite types with managed/raw modifiers.**  `*Self`
   on impl-collection becomes `*int` for T=int; `@Self`
   becomes `@int`; `@[]Self` becomes `@[]int`.  Recursion
   is straightforward; pin in S.2 tests.

4. **Error message wording.**  "method `Foo` uses `Self` and
   cannot be called through an interface value" — confirm
   wording with one consumer use case.  Alternative:
   "method `Foo` is not object-safe; use a generic
   constraint" — Rust-style but unfamiliar to readers
   without Rust background.  Lean toward the explanatory
   form.

5. **Interaction with the bootstrap.**  The Go bootstrap
   interpreter doesn't support interfaces fully (interfaces
   are part of the self-hosted-only feature set per
   `bootstrap-subset.md`).  Self lands in the self-hosted
   toolchain only; no bootstrap impact.

## Cross-references

- `claude-notes.md` § "`Self` type in interface declarations
  — DECIDED 2026-05-12" — canonical spec.
- `claude-todo-done.md` § "`Self` type in interface
  declarations — RATIFIED 2026-05-12" — ratification record.
- `plan-primitives-impl-interfaces.md` — pkg/std consumes
  Self in `Comparable` / `Orderable` / `Hashable`.
- `plan-generics.md` Slice 3 — constraint check substitutes
  Self via the same `substituteSelf` helper introduced in
  S.2 here.
- `plan-interface-embedding.md` — extension and Self interact
  cleanly (Hashable : Comparable; Self resolves consistently
  across extension).
- `plan-interface-syntax-revision.md` — the no-duck-typing
  / explicit-impl stance Self builds on.
