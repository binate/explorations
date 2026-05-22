# Plan: Generics

> **Status: COMPLETE 2026-05-21.**  Slices 1–7 fully
> implemented: generic functions, structs, and interfaces all
> work both same-package and cross-package, including
> constraint-method dispatch via interface inheritance and
> instantiated-interface vtable dispatch.  Q7 pinned 2026-05-21
> (source-text bodies in `.bni`).
>
> **Original design notes (2026-05-06; AMENDED 2026-05-12).**
> Pre-implementation; pins the open questions so implementation
> can start cleanly when the interface track lands satisfaction
> checks (the foundational dependency).  Cross-references the
> existing decisions in `claude-notes.md` § "Generics — DECIDED".
>
> **2026-05-12 amendment:** the design depends on a separately-
> open question — *can primitives implement interfaces?* — that
> needs resolution before constrained generics
> (`[T Comparable]` etc.) can target `int` / `bool` / etc.  See
> the new "Hard dependency: primitives-implement-interfaces"
> section below.  Cross-package interfaces (Slices 2.6–2.9) and
> interface extension (Slices E.1–E.3) have since landed; notes
> below incorporate them.

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

## Hard dependency: primitives-implement-interfaces

> Added 2026-05-12 after surveying the broader interface track.

The constraint-satisfaction model below presumes
`impl int : Comparable`, `impl uint8 : Hashable`, etc. are
declarable.  **Under current language rules they are not** —
methods can only be declared on `TYP_NAMED` receivers, and
universe primitives like `int` aren't named types.  This is
flagged in `claude-todo.md` § "`print(42)` and friends: how do
primitives implement interfaces? — DESIGN OPEN" with two
candidate options:

1. **Language-blessed implicit interfaces.**  Add `Comparable`,
   `Hashable`, `Stringer`, etc. to the small closed set of
   compiler-synthesized impls (the same mechanism that makes
   every type implement `any`).  Every primitive gets a real
   vtable; the language defines the canonical formatting /
   comparison / hashing story for each.
2. **Stdlib carve-out.**  Allow a designated package
   (`pkg/std` or similar) to declare `func (x int) less(...)
   bool` even though `int` is a universe type.  The carve-out
   exists only for the language's own stdlib; user packages
   still can't extend `int`.

Until one of these (or some other resolution) lands, **the
constrained-generics path can't satisfy on primitives**, which
removes the motivating use case (`Vec[int]`,
`sort[int](xs)`, `Map[*[]const char, int]`).  The unconstrained
path (`[T any]`) is unaffected — generic containers that store
T values and shuffle them around without consulting any T
methods can still ship.

Implication for slice ordering (see "Implementation work"
below): Slices 1, 2, 4, 5 (`[T any]` end-to-end + generic
structs) can land in parallel with the primitives-impl design
call.  Slice 3 (constraint check) is blocked on the design
call landing AND on the interface track shipping
satisfaction-lookup primitives.

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

**Cross-package, since 2026-05-07:** the satisfaction lookup
naturally crosses packages now that
`plan-cross-package-interfaces.md` (Slices 2.6–2.9) has landed
— `impl T : I` may live in any package that has both T and I
in scope, and the type-checker already shares one `Checker`
across packages.  Generic instantiations get the
cross-package impl-visibility check for free.

**Inherited methods, since 2026-05-? (Slices E.1–E.3):**
`interface X : I1, I2 { ... }` is now a thing.  Constraint
satisfaction must consult I's *full* method set (own +
inherited transitively, via the `FullMethods` machinery from
Slice E.2), not just I.Methods directly.  Same applies to
constraint-method calls in the generic body — `t.foo()` may
resolve to a method I inherits from I's parent.

### 2. Primitives carry canonical impls

For built-in types — `int`, `uint8`, `bool`, etc. — there must
be canonical impls satisfying the standard constraints:

```
impl int : Comparable    { func less(a, b int) bool { return a < b } }
impl int : Hashable      { func hash(x int) uint { ... } }
impl int : Stringer      { func toString(x int) @[]char { ... } }
impl uint8 : Comparable  { ... }
... etc.
```

Without this, `Vec[int]` is dead on arrival.  Custom user types
follow the same pattern: `impl MyType : Comparable { ... }`
when the user wants `Vec[MyType]` to satisfy a constraint.

> **CONTINGENT** on the primitives-implement-interfaces design
> call (see "Hard dependency" section above).  As written, the
> code above is *not currently legal Binate* — `impl int : ...`
> requires either compiler-synthesized impls (option 1) or a
> stdlib carve-out (option 2).  The shape of the impl
> declaration may differ once the design is pinned (e.g.,
> language-blessed implicit interfaces wouldn't have visible
> `impl` declarations at all — the compiler synthesizes them).

Open: which canonical interfaces ship in v1?  Likely
`Comparable`, `Hashable`, `Stringer`.  `Equatable` may collapse
into `Comparable` (a < b implies != suffices for equality).
Pinned in a follow-up `plan-stdlib-interfaces.md` once the
primitives-impl design call resolves.

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

### 5. Cross-package impl visibility — already solved

> Updated 2026-05-12 after `plan-cross-package-interfaces.md`
> shipped (Slices 2.6–2.9, 2026-05-07).

The non-generic case is now done: `impl T : I` may live in
any package that has both T and I in scope (transitively
through imports), the type-checker shares one `Checker` across
packages, and vtable symbols are canonical on `(Pkg(T),
Pkg(I))` so any two TUs that reference the same `(R, I)` pair
agree.  Generic instantiations consult `c.Impls` the same way
non-generic interface-value construction does — no new
visibility machinery needed for the type-check side of
satisfaction.

The remaining concern is **generic body shipping** in `.bni`:
a consumer monomorphizing `f[MyType]()` needs the *body* of
`f` (since instantiation rewrites it), not just the impl
visibility.  See Q7 below.

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

### Q7 — `.bni` body emission — DECIDED 2026-05-21

> Scope narrowed 2026-05-12: impl visibility is solved by the
> cross-package interfaces machinery (`plan-cross-package-
> interfaces.md`, landed 2026-05-07).  This question now only
> covers the **generic-body** side.

**Decision**: source-text bodies in `.bni`.  Generic decls
(`func f[T any]`, `type Vec[T any] struct {...}`,
`interface Container[T any] {...}`) — and only those — carry
their full source body in the `.bni`.  Non-generic decls stay
signature-only.

**Rationale**: monomorphization requires the body at the
consumer; the body has to be visible regardless.  Inline-source
is the smallest extension to the current `.bni` format — no new
serialization surface — and the analog of C++ template-header
files: templated definitions in the public header, non-templated
in the implementation TU.  Binary-only distribution remains
viable for everything except generics.

Considered alternatives:

- **Serialized canonical AST/IR sidecar.**  Smaller wire format
  in principle but requires a writer / reader pair, a stable
  encoding, and a versioning story for cross-version `.bni`
  skew.  Worth revisiting if `.bni` size becomes a problem or
  if package distribution needs to decouple from `.bn` source.
- **Reparse the producer's `.bn` source on demand.**  Cheap to
  implement but couples consumers to the producer's source
  layout — incompatible with binary-only distribution.

**Versioning**: out of scope for v1 — consumer compiles against
whatever `.bni` it sees.  Skew detection is a packaging concern
that lands separately once package distribution exists.

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

### Slice 1 — Parser + AST for generic functions — LANDED `e8139ea`

- Parser accepts `func f[T any, U any](x T, y U) T` and
  `name[T1, T2]` in expression position.
- AST: new TypeParam struct, `Decl.TypeParams` field, new
  `EXPR_INSTANTIATE_OR_INDEX` kind (covers any `name[...]`
  shape, supersedes the prior `EXPR_INDEX`), new
  `TEXPR_TYPE_PARAM` kind (declared; produced by the type-
  checker, not the parser).
- All `any` constraint only at this slice.

### Slice 2 — Type-checker for `[T any]` generic functions — LANDED
- **2a** (`7bf3385`): TYP_TYPE_PARAM kind; generic-decl body
  type-checks against a per-decl type-param scope;
  resolveFuncDeclType + checkFuncDecl install and re-install
  the scope around signature / body checks; IR-gen skips
  generic decls.  Calls to generics without explicit type args
  rejected ("no inference").
- **2b** (`91e0b62`): `f[T1, ...](args)` at the call site.
  instantiateGenericFunc, typeArgFromExpr (EXPR_IDENT + `*T`),
  substituteTypeParams over composite types.  Rejection paths
  pinned (wrong arity, arg-type mismatch, unknown type-arg).

### Slice 3 — Constraint-satisfaction check — LANDED `6614bdd`
- Constraints lift from `any` to interface names.  Storage:
  `Type.TpConstraint` on TYP_TYPE_PARAM.
- typeSatisfiesConstraint walks `c.Impls` and matches via
  Identical-on-receiver + implCoversInterface; transitive
  satisfaction through interface inheritance (`Slice 4b`,
  `77be365`) — `impl int : Orderable` satisfies
  `T : Comparable` because Orderable extends Comparable.
- Body method-call dispatch on TYP_TYPE_PARAM receivers routes
  through the constraint interface's method set
  (tryTypeParamMethodCall); Self in the interface signature
  substitutes to the type-param itself.

### Slice 4 — IR-gen monomorphization — LANDED
- **4a** (`ad2a26c`): per-(generic, type-args) IR func emission
  via a substitution context that resolveTypeExpr consults;
  call-site dispatch routes `f[int](...)` to the mangled name.
  Conformance 431 / 432 / 433 cover identity, multi-instantiate
  dedup, and local-T / multi-param.
- **4b** (`77be365`): IR-gen "just works" for constraint-method
  calls inside specialized bodies — once the type-checker
  accepts the constraint via inheritance, IR-gen's substituted
  body sees `int.Compare(int)` which dispatches through the
  existing primitives-impl-interfaces work.  Conformance 434
  exercises `cmp[int]` and `hashOf[uint]` end-to-end via
  pkg/std's Orderable / Hashable impls.

### Slice 5 — Generic structs — LANDED
- **5a** (`75042d6`): parser + AST for `type List[T any] struct
  { ... }` and `List[int]` in type position (TEXPR_INSTANTIATE
  + `TypeExpr.TypeArgs`).  Type-checker / IR-gen stub the
  TEXPR_INSTANTIATE branch.
- **5b** (`39b3461`): type-checker for generic struct
  instantiation.  GenericTypeDecls registry, per-(decl, args)
  GenericInstantiations cache (Q1), resolveTypeInstantiation
  + buildInstantiatedStruct.  Substitution descends into
  pointer / slice / array element types.
- **5c** (`aa5fb38`): IR-gen for generic struct instantiation.
  Per-(decl, args) ModuleStruct registration via
  ensureInstantiatedStruct; fields resolve with the type-param
  substitution context active.  Conformance 436 / 437 cover
  field read/write and Pair[int, int] through a function arg.

### Slice 6 — Generic interfaces — LANDED
- **6a** (`ae4c4d6`): parser + AST for `interface Container[T
  any] { ... }`.  Type-checker rejected initially.
- **6b** (`279769f`): type-checker accepts generic interface
  decls; resolveTypeInstantiation handles interface heads via
  buildInstantiatedInterface (substitutes T in method
  signatures, builds TYP_INTERFACE).  Per-(decl, args) cache
  shared with structs.  *Container[int]* / @Container[int]
  type-check.
- **6c-min** (`c06c724`): IR-gen skips generic interface decls
  in collectInterfaceFromDecl so the bare decl is harmless.
- **6c-mid** (`f49a95f`): parseInterfaceRef accepts
  `Container[int]` in impl iface refs and extension parent
  lists.  Type-checker (from 6b) builds the instantiated
  TYP_INTERFACE.
- **6c-full** (`bac6909`): IR-gen vtable layout + dispatch for
  instantiated interfaces.  genericIfaceDecls registry parallel
  to genericTypeDecls; ensureInstantiatedInterface materializes
  a per-(decl, args) ModuleInterface entry under the bare
  mangled name (`Container__bn_inst__int`) with Pkg stored
  separately; isInterfaceTypeExpr / ifaceTypeForName recognize
  TEXPR_INSTANTIATE-on-generic-iface; collectImplsFromDecl
  handles TEXPR_INSTANTIATE iface refs.  Per-module iface
  registry was split out to gen_iface_registry.bn to keep
  gen_impl.bn under the file-length soft cap.  Conformance
  451 / 452 / 453 cover raw, managed, and multi-instantiation
  dispatch.  A follow-on (post-`bac6909`) extended
  collectInterfaceFromDecl to handle TEXPR_INSTANTIATE *parent*
  refs (`interface Sub : Container[int]`) so the concat-vtable
  layout and ancestor-closure walk reach the right (R, I) pair;
  conformance 454 (multi-method) and 455 (parent-instantiation +
  upcast) cover this.

### Slice 7 — Cross-package generics (`.bni` bodies) — LANDED

Q7 pinned 2026-05-21: source-text bodies in `.bni` for generic
decls only (see Q7 section).

- **7a — cross-package generic functions** (`b670a15`):
  parser keeps body for generic decls in `.bni` mode; loader
  merger includes generic .bni decls into the merged AST;
  IR-gen registers imported generic decls under the import
  alias via parallel `genericDeclPkgs`; gen_call.bn +
  type-checker `checkInstantiateOrIndex` recognize
  `pkg.f[T](...)` (SELECTOR head) and route to monomorphization.
  Conformance 460 / 461 / 462 (`Id[int]`, multi-arg
  `pair[int, bool]`, pointer-arg `Id[*int]`) pass boot-comp-comp.
- **7b — cross-package generic structs** (this commit):
  type-checker's bni_scope.bn skips generic type decls in
  Pass 1 placeholder registration and stashes them in
  `c.GenericTypeDecls` / `GenericTypeDeclPkgs` keyed on the
  pkg short-name; `resolveTypeInstantiation` accepts
  pkg-qualified heads and routes via
  `lookupGenericTypeDeclPkg`.  IR-gen `gen_util.bn`'s
  `TEXPR_INSTANTIATE` branch accepts the qualified head too.
  Conformance 463 (`veclib.Pair[int]`) covers.
- **7c — cross-package generic interfaces** (this commit):
  fell out of 7b for free — the type-checker
  `resolveTypeInstantiation` change handles both struct and
  iface heads; IR-gen `isInterfaceTypeExpr` /
  `ifaceTypeForName` / `collectImplsFromDecl` /
  `collectInterfaceFromDecl` parent-walk accept qualified
  generic-iface refs.  Conformance 464
  (`impl IntBox : iflib.Container[int]`) covers.

All five conformance tests (460–464) are xfail.boot-comp because
the pinned `bnc-0.0.1` builder predates the body-in-`.bni` rule;
they pass boot-comp-comp / boot-comp-int / boot-comp-comp-int /
boot-comp-comp-comp.

Note: impl visibility is already cross-package per Slices
2.6–2.9 of `plan-cross-package-interfaces.md` — Slice 7 only
added the generic-body side.

## Cross-references

- `claude-notes.md` § "Generics — DECIDED" — high-level design,
  the source of the ratified decisions above.
- `plan-interface-syntax-revision.md` — interfaces are the
  foundational dependency; constraint satisfaction reuses
  the impl-collection machinery from there.
- `plan-cross-package-interfaces.md` — landed 2026-05-07;
  generic instantiations get cross-package impl visibility
  for free (see §5 above).
- `plan-interface-embedding.md` — Slices E.1–E.3 landed;
  constraint satisfaction must consult inherited methods via
  the `FullMethods` machinery (see §1 above).
- `claude-todo.md` § "`print(42)` and friends: how do
  primitives implement interfaces? — DESIGN OPEN" — the
  blocker on constrained generics targeting primitives (see
  "Hard dependency" section above).
- `bootstrap-subset.md` § "Generics" — the constraint that
  self-hosted source stays generics-free until the bootstrap
  retires.
- `claude-discussion-detailed-notes.md` § "Generics" — full
  design history.
- `claude-todo.md` — "Generics" entry once this plan is
  ratified.
