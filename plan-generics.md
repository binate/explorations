# Plan: Generics

> **Status: DRAFT 2026-05-06.**  Pre-implementation; pins the
> open questions so implementation can start cleanly when the
> interface track lands satisfaction checks (the foundational
> dependency).  Cross-references the existing decisions in
> `claude-notes.md` § "Generics — DECIDED".

## Context

Generics have been on the language plan since the early design
notes — they're the answer to "no built-in maps, no `append`, no
generic `Vec[T]`" (see `claude-notes.md` § "Maps as a library
concern", § "Growable collections").  The high-level design is
already pinned in `claude-notes.md` § "Generics — DECIDED":

- Type parameters on functions, structs, and interfaces.
- Constraints expressed as interface names; named combined
  interfaces for multi-constraint cases (no `+` operator).
- No type inference — always spell out type args at the
  instantiation site.
- Monomorphization — each instantiation produces specialized
  IR / code.  No vtable dispatch in monomorphized output.
- Generic body checked once against the constraint;
  instantiation only verifies that the concrete type satisfies
  the constraint.
- No generic methods on types.  Generic free functions instead.
- No conditional impls in v1.  Specific instantiations can
  carry their own `impl` declarations.
- Cross-package generics: bodies emitted in `.bni` so consumer
  packages can instantiate (C++ template-in-header model).

This plan extends those decisions in the only place that needed
clarification — **how constraint satisfaction works in a non-
duck-typing language** — and pins the remaining open questions
(instantiation cache key, mangled-name scheme, type-param
storage in `@Type`, recursive generics, `.bni` body emission).

## Ratified decisions

### 1. Constraint satisfaction is explicit, via `impl`

Binate doesn't duck-type at any other layer (see
`plan-interface-syntax-revision.md`); it doesn't here either.
A type T satisfies an interface I iff there is an `impl T : I`
declaration visible at the instantiation site.  At
instantiation `f[T](...)`, the type checker:

1. Looks up the constraint interface `I` from the generic's
   declaration.
2. For each type argument T paired with constraint I, looks up
   `impl T : I` in scope.  Hit → satisfied.  Miss → instantiation
   error pointing at the missing impl declaration.

The method-shape match (does `impl T : I` actually provide the
methods I declares with the right signatures?) was **already
verified at impl-collection time**, when the impl was originally
declared.  At instantiation we just confirm the impl exists; we
don't re-check shapes.

### 2. Stdlib provides primitive impls

For built-in types — `int`, `uint8`, `bool`, etc. — the standard
library ships canonical impls:

```
impl int : Comparable    { func less(a, b int) bool { return a < b } }
impl int : Hashable      { func hash(x int) uint { ... } }
impl int : Stringer      { func toString(x int) @[]char { ... } }
impl uint8 : Comparable  { ... }
... etc.
```

A one-time central cost.  Without this, `Vec[int]` etc. would be
unusable for the obvious case and the language is dead on
arrival for concrete-collection generics.  Custom user types
follow the same pattern: `impl MyType : Comparable { ... }`
when the user wants `Vec[MyType]` etc. to satisfy a
constraint.

Open: which canonical interfaces ship in v1?  Likely
`Comparable`, `Hashable`, `Stringer`.  `Equatable` may collapse
into `Comparable` (a < b implies != suffices for equality).
Pinned in a follow-up `plan-stdlib-interfaces.md`.

### 3. Constraint-method calls monomorphize to direct calls

Inside the generic body, a call through a constraint
(`t.less(other)` where `t : T : Comparable`) compiles to:

- At body-check time: the call is type-checked against the
  constraint interface.  `Comparable.less(*const T, *const T)
  bool` is the abstract signature.  The body sees the abstract
  result (`bool`) and continues.  No dispatch decision yet.
- At IR-gen / instantiation time: the monomorphizer rewrites
  the call to the concrete method named in `impl T : I`.  E.g.,
  `t.less(other)` with T=int becomes a direct call to the
  `less` function from `impl int : Comparable`.  No vtable, no
  indirection.

This keeps the perf and storage benefits of monomorphization:
the instantiated body has the same shape as a hand-written
non-generic version operating on T directly.

### 4. Bare `any` skips the satisfaction check

The implicit universal interface `any` (per `claude-notes.md`
§ "any") has no methods.  A type parameter `[T any]`:

- Always satisfies the constraint (every type "implements"
  empty-method-set `any`).
- Cannot have constraint-method calls in the generic body
  (there are no methods to call).
- Useful for purely-structural generics: containers that store
  T values and shuffle them around, comparison/equality
  delegated to the caller (or to a separate function-value
  parameter).

This is the simplest constraint and the natural starting point
for the implementation — Slice 1 of the work plan below
operates entirely in the `any`-only world.

### 5. No `.bni` shipping decision yet for `impl T : I`

The interface track has decided that `impl` declarations live in
`.bn` source (not `.bni`) — they're an *implementation* concern.
For non-generic code, this is fine: an impl is a vtable +
methods, both materialized at compile time, and the `.bni`
records the type and the interface; downstream callers don't
need the impl declaration itself to use the interface.

For generics, the consumer monomorphizing `f[MyType]()` needs
to know whether `impl MyType : I` exists.  Two paths:

- **Path A**: emit a per-package "impl manifest" alongside `.bni`
  listing every `impl T : I` declared in the package.  Consumers
  read this when type-checking instantiations.
- **Path B**: emit `impl` declarations into `.bni` directly,
  treating them as part of the public surface.

Neither is hard.  Path A is more orthogonal (separates "what
types exist" from "what impls exist").  Path B keeps the
single-file public surface.  Pinned to *Open question* below.

## Open questions

These need pinning before the slice that touches them; not
blocking earlier slices.

### Q1 — Instantiation cache key

Each unique `(generic_decl, type_args_tuple)` produces one
specialized IR func.  The cache key needs:

- Identity-based comparison on `@Type` records for primitive
  types (`int`, `bool`, etc. — singletons; pointer equality).
- Structural comparison for derived types (`*int` and `*int`
  built independently must hash to the same key).
- Stable across the lifetime of the type-checker session.

Likely answer: a canonical-form key derived from the type's
mangled-name fragment (see Q2).  Two types with the same
mangled fragment instantiate the same generic to the same
specialization.

### Q2 — Mangled-name scheme

Specialized IR funcs need unique, stable names.  E.g.:

```
func sort[T Comparable](items *[]T)
   instantiated as sort[int]
   →  bn_pkg__sort__int
```

For complex type args:

```
sort[*Point]                  →  bn_pkg__sort__ptr_pkg__Point
sort[@[]int]                  →  bn_pkg__sort__mslc_int
sort[Pair[int, *[]uint8]]     →  bn_pkg__sort__pkg__Pair__int__slc_uint8
```

Open: scheme for separators, escape rules for nested generics.
Likely follow the existing mangler's convention (`__` as the
core separator, prefixes like `ptr_`, `slc_`, `mslc_`, `arr_N_`
for derived types).

### Q3 — Type-param storage in `@Type`

A type parameter `T` is a placeholder that resolves to a
concrete `@Type` at instantiation.  Open:

- New `TYP_TYPE_PARAM` kind?  Or reuse `TYP_NAMED` with a flag?
- Where does the binding context live (which generic decl T
  belongs to, plus the constraint interface)?
- During body-checking, `T` appears in field types, param
  types, return types — every site that consults type identity
  needs to compare type-param instances correctly (same param
  decl → identical; different decls → distinct, even if both
  are named `T`).

Likely: new `TYP_TYPE_PARAM` kind with fields `Name`, `Owner`
(pointer to the generic decl's symbol), `Index` (position in
the type-param list), `Constraint` (`@Type` for the interface
constraint, or nil for `any`).

### Q4 — Recursive / self-referential generics

```
type List[T any] struct { head @Node[T] }
type Node[T any] struct { value T; next @Node[T] }
type Tree[T any] struct { left @Tree[T]; right @Tree[T] }
```

Type-param scope inside the struct decl needs to resolve
forward references the same way pre-registered named types do
today (see `pkg/types/check_decl.bn` `preRegisterTypeNames`).
Likely answer: pre-register generic type names with their
type-param arity, then resolve fields in pass 2 with the
type-param scope installed.  Tree-style self-recursion is the
existing struct self-ref case extended to "the generic decl is
in its own type-param scope."

### Q5 — Instantiation-site syntax disambiguation

```
sort[int](xs)                    // unambiguous — int is a type
sort[T](xs)                      // T could be a type param OR a value
arr[i]                            // value indexing — never instantiation
```

The parser sees `name [ ... ]` and needs to decide
type-arg-list vs index expression.  Disambiguation is
contextual: if `name` resolves to a generic decl, treat `[...]`
as type args; if it resolves to a value with a slice/array
type, treat `[...]` as index.  This is a type-checker pass
that the parser delays to.  Likely answer: a single
`EXPR_INSTANTIATE_OR_INDEX` AST node that the type-checker
disambiguates, mirroring how `obj.M` is disambiguated between
method call and package-qualified function call today (see
`pkg/ir/gen_method.bn` `isMethodCallSel`).

### Q6 — Generic interfaces

```
type Container[T any] interface {
    get(index int) T
    put(index int, value T)
}
```

Each `(interface_decl, type_args)` produces a distinct
interface "shape" — the method set is parameterized by T.
Vtable layout: same per-instantiation, but the vtable is
per-(impl, *concrete-instantiation*) rather than per-(impl,
interface).  Open: how vtable globals are named when the
interface itself is generic.  Likely deferred to Slice 6 below.

### Q7 — `.bni` body emission

(See ratified-decision §5 — Path A vs Path B.)  Pinning needs:

- Encoding format for generic bodies in `.bni` (currently
  signature-only).
- How `impl T : I` declarations are surfaced — manifest file
  or in-band in `.bni`.
- Whether *all* `.bn` source comes along (simpler) or just the
  generic declarations (smaller `.bni` files).

### Q8 — Bootstrap subset interaction

The Go bootstrap interpreter doesn't support generics, and
self-hosted code that the bootstrap runs (i.e., the self-hosted
compiler/interpreter sources under `binate/`) must stay in the
bootstrap subset (per `bootstrap-subset.md`).

So generics land in the *language as compiled by the self-hosted
toolchain*, not in the bootstrap-runnable subset.  No
backporting to the Go interpreter.  User code targeting the
self-hosted toolchain gets generics.  The self-hosted compiler's
own source code remains generics-free, continuing to use
concrete-types-per-combination workarounds (per
`bootstrap-subset.md` § "Generics").  Eventually the bootstrap
constraint goes away (when the language is fully self-hosted
and the bootstrap is retired), and the self-hosted compiler can
itself use generics — but that's a separate future migration.

## Implementation work — slices

Each slice is independently shippable, ordered by dependency.

### Slice 1 — Parser + AST for generic functions

- Lex/parse `func f[T any, U any](x T, y U) T` shape.
- New AST nodes: `TypeParam` (Name, Constraint TypeExpr),
  `TypeParamList` on `Decl`.
- New `TEXPR_TYPE_PARAM` kind for type-param references
  inside types (`T` in `*[]T`).
- Parse `f[int, *Point](x, y)` instantiation expressions.
- New AST node: `EXPR_INSTANTIATE_OR_INDEX` (single node;
  disambiguated downstream by the type checker).
- All `any` constraint only — constraint parsing is just an
  identifier lookup.

No type-checker / IR-gen work in this slice.  Tests: parser
unit tests covering new shapes plus rejection cases (missing
`]`, no constraint after `,`, etc.).

### Slice 2 — Type-checker for `[T any]` generic functions

- New `TYP_TYPE_PARAM` kind on `@Type` (Q3 above; lock the
  shape here).
- Generic-decl scope: type params defined as type symbols
  inside the decl's body scope only.
- Body check: with type-param scope installed, the function
  body type-checks once.  References to `T` resolve to the
  type-param symbol.
- Instantiation site: disambiguate `EXPR_INSTANTIATE_OR_INDEX`
  by resolving the head identifier; if generic, instantiate.
  Substitute type args for type params, produce the
  fully-resolved `@Type`.
- Identity-based comparison for type params (Q3).
- Per-(generic, type-args-tuple) instantiation cache (Q1).

Tests: body-checks for valid generics (containers, identity
funcs), rejection of constraint-method calls (`t.less(other)`
fails — no methods on `any`).

### Slice 3 — Constraint-satisfaction check

**Depends on the interface track landing**: per-impl vtable
infra (Slice 2.5+ of `plan-interface-syntax-revision.md`) and
satisfaction-lookup machinery.

- Constraint parsing — accept `[T Comparable]` etc.
  (interface name in constraint slot).
- At instantiation, look up `impl T : I` for each (T_arg, I)
  pair.  Miss → clean error pointing at the missing impl.
- Body check: constraint-method calls (`t.less(other)`)
  type-check against the constraint interface's method set.
- All else: same machinery as Slice 2.

Tests: cross-product of (typed constraint, satisfying type,
non-satisfying type, missing impl).  Stdlib primitive impls
land here as a pre-req.

### Slice 4 — IR-gen monomorphization

- For each unique instantiation, emit one specialized IR func
  via a per-instantiation `genFunc` pass that substitutes
  type params for concrete types in the AST → IR walk.
- Mangled name per Q2.
- Rewrite constraint-method calls (`t.less(other)`) to direct
  calls on the concrete `impl T : I` method — IR-gen knows the
  impl from Slice 3's bookkeeping.
- VM / codegen: no changes — they see ordinary IR funcs.

Tests: end-to-end (parse → check → IR-gen → VM exec) for
identity, generic Vec push/pop, sort by Comparable.

### Slice 5 — Generic structs

- Parser: `type List[T any] struct { ... }`.
- Type-checker: pre-register generic type names with arity
  (Q4); resolve fields in type-param scope.
- Instantiation: `List[int]` produces a distinct `@Type` with
  T-substituted fields; layout computed from the substituted
  fields.
- IR-gen: per-(struct, type-args) struct registration;
  per-(struct, type-args) `__dtor_<mangled>` and
  `__copy_<mangled>` helpers (mirrors the existing
  managed-field struct emission).

Tests: generic Vec, Pair, Tree, plus self-recursive
List/Node round-trip.

### Slice 6 — Generic interfaces

- Parser: `type Container[T any] interface { ... }`.
- Per-instantiation interface type + vtable.
- Vtable global naming for `(impl, *generic-instantiation*)`
  (Q6).

Tests: `Container[int]`-shape interface, impl, dispatch.

### Slice 7 — Cross-package generics (`.bni` bodies)

- Pin Q7 (Path A vs Path B).
- Encode generic bodies in `.bni` (or sibling manifest).
- Encode `impl T : I` declarations alongside.
- Consumer instantiates by reading the body and
  monomorphizing locally.

Tests: cross-package `Vec[int]`, `sort[T]` instantiation.

## Cross-references

- `claude-notes.md` § "Generics — DECIDED" — high-level design,
  the source of the ratified decisions above.
- `plan-interface-syntax-revision.md` — interfaces are the
  foundational dependency; constraint satisfaction reuses
  the impl-collection machinery from there.
- `bootstrap-subset.md` § "Generics" — the constraint that
  self-hosted source stays generics-free until the bootstrap
  retires.
- `claude-discussion-detailed-notes.md` § "Generics" — full
  design history.
- `claude-todo.md` — "Generics" entry once this plan is
  ratified.
