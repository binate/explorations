# Plan: Primitives Implementing Interfaces

> **Status: DRAFT 2026-05-12.**  Design-open question pinned in
> `claude-todo.md` § "`print(42)` and friends".  Lays out three
> candidate options, evaluates them against Binate's existing
> design stance, and recommends Option 2.  Pending ratification.

## Context

Today, **methods cannot be declared on universe primitives**
(`int`, `uint8`, `bool`, `char`, etc.).  The receiver-resolution
pass in `pkg/types/check_decl_func.bn:resolveMethodReceiver`
explicitly rejects any receiver whose base type isn't
`TYP_NAMED`:

```
if sym.Type == nil || sym.Type.Kind != TYP_NAMED {
    addCheckError(c, d.Pos,
        "method receiver must be a named type (not an alias or builtin)")
    return nil
}
```

This rules out `func (x int) toString() *[]const char` and,
by extension, `impl int : Stringer { ... }`.

The consequence cascades into several places:

- **`println(42)` only works because `bootstrap.println` is a
  compiler builtin** that synthesizes per-type formatting at the
  call site.  Documented as a temporary hack
  (`feedback_println_hack.md`); slated for removal.
- A user-written `printIt(s *Stringer) { ... println(s.String()) }`
  cannot accept a literal `42`.  The user has to wrap with
  `type MyInt int` + `impl MyInt : Stringer`, then write
  `printIt(&MyInt(42))`.  Heavyweight ceremony for a basic case.
- **Constrained generics** (`Vec[int]`, `sort[int Comparable]`)
  cannot satisfy on primitives — the satisfaction lookup
  `impl int : Comparable` fails because the impl can't be
  written.  This is the blocker on `plan-generics.md` Slice 3
  ("constraint-satisfaction check") flagged in that plan's
  "Hard dependency" section.
- Future `Map[K, V]`, `Set[T]`, `Eq` / `Hash` / `Less` style
  interfaces all hit the same wall.

This plan resolves the design-open question in `claude-todo.md`
so that generics + the broader interface story can move
forward.

## Constraints we have to preserve

These came out of the existing design discussions and shouldn't
be relitigated here:

1. **No duck-typing.**  Type-T-satisfies-interface-I is
   answered by an explicit `impl` declaration (own or
   compiler-synthesized), not by a structural method-shape
   match.  Per `plan-interface-syntax-revision.md`.
2. **`impl` is a real declaration with real codegen.**  Each
   `(T, I)` pair produces a real vtable (`__ivt.bn_<recv_pkg>__
   <recv>__<iface_pkg>__<iface>`) per the cross-package
   convention.  Whether the impl was hand-written or
   compiler-synthesized doesn't change this.
3. **User packages cannot extend types from other packages.**
   Methods belong to the type's defining package; only the
   defining package can declare methods or impls for a type.
   (Interfaces are slightly different — `impl T : I` may live
   in any package per `plan-cross-package-interfaces.md`, but
   the methods themselves live with T.)
4. **Performance: zero-overhead in the monomorphized case.**
   Generic constraint-method calls resolve to direct calls to
   the impl's concrete method (per `plan-generics.md` §3).  No
   vtable indirection in monomorphized output.

## Options

### Option 1 — Language-blessed implicit interfaces

The compiler synthesizes impls for a small closed set of
language-defined interfaces against every type (including
primitives).  Builds on the existing `any` precedent
(`claude-notes.md` § "Built-in implicit interfaces": *"a small,
closed, language-defined set of interfaces implicitly
implemented by all types"*).

Mechanics:

- The language spec names the blessed set: e.g., `Stringer`,
  `Comparable`, `Hashable`, `Equatable`.  Their declarations
  live in a special package the compiler always loads.
- For every type T the compiler encounters, it auto-emits the
  vtables for `(T, I)` for each blessed I — when actually used
  (lazy emission, same as today's per-(T, I) emission).
- The actual method bodies for primitive impls (`int.less`,
  `int.hash`, `bool.toString`, etc.) are written somewhere
  central — likely in the same special package.
- For composite types (structs, arrays, slices), the compiler
  must answer "what does `Comparable` mean for `Point { x, y
  int }`?"  Two sub-options:
  - **1a**: composite types don't auto-implement; only primitives
    do.  Breaks the uniformity claim.  User writes
    `impl Point : Comparable` if needed.
  - **1b**: auto-derive recursively (lexicographic field
    ordering for `Comparable`, structural for `Equatable`,
    composed hash for `Hashable`).  Awkward — what's the
    canonical ordering on `*[]int`?  On a func-value field?
    Many composite types have no natural answer.

- User override: a user-written `impl T : Comparable` for a
  user-defined T overrides the auto-derive for that T.  For a
  primitive T, the user can't override (it'd violate the
  no-extending-foreign-types rule).

**Pros**:
- Uniform across types — no "this works on `int` but not on
  `Point`" surprises (assuming 1b).
- Direct extension of the `any` precedent.
- `printIt(s *Stringer)` accepts every value uniformly.
- Trivial syntax for the user — they write the call site,
  the language guarantees the impl exists.

**Cons**:
- Auto-derive (1b) opens a long tail of design questions:
  comparison on `func` values, on `*Iface`, on cyclic
  references, on managed-slice contents.  Each blessed
  interface needs a "what does this mean for every type
  shape" answer.
- Punting on auto-derive (1a) breaks the uniformity that
  motivates the option in the first place.
- Adds a new "language-blessed interface" concept distinct
  from user interfaces.  The compiler treats them specially
  (synthesizes impls); tooling has to know about them
  separately.
- The blessed set is closed at the language level — adding a
  new one (e.g., `Numeric`) requires a language-spec change.

### Option 2 — Stdlib carve-out for methods on universe types

A designated package (`pkg/std` is the natural candidate;
exact name TBD) is allowed to declare methods on universe
primitives even though the primitives aren't named in that
package.  No new compiler-synthesized impls — the stdlib hand-
writes them.

Mechanics:

- The type-checker's `resolveMethodReceiver` adds an exception:
  if the current package is the carve-out package AND the
  receiver is a universe primitive, the method is accepted.
  Likely flag this as `c.AllowUniverseRecv` (set by the
  package-load driver based on package identity), parallel to
  the existing `c.AllowRedef`.
- The carve-out package writes:
  ```
  func (a int) less(b int) bool { return a < b }
  func (x int) hash() uint { ... }
  func (x int) toString() @[]char { ... }

  impl int : Comparable
  impl int : Hashable
  impl int : Stringer
  ```
- All other packages reject methods on universe types as today.
- Vtable codegen / cross-package interface mangling already
  handles the case naturally (recv-pkg == carve-out pkg, iface-
  pkg wherever the interface is declared).

**Pros**:
- Aligns with Binate's "explicit > implicit" stance everywhere
  else (explicit `impl`, no auto-deref, no duck-typing).  The
  impls are still hand-written and visible; only the
  *who-can-declare* rule is relaxed.
- No new "implicit interface" concept — interfaces remain
  uniform; the carve-out is just a localized rule.
- No auto-derive problem — composite types with no natural
  comparison just don't get a `Comparable` impl, and the
  generic code that needs one has to take a comparison
  function-value parameter (the ordinary fallback).
- Stdlib looks like normal Binate code; tooling sees the impls
  the same way it sees user impls.
- Adding a new stdlib-blessed interface is just a stdlib
  source change — no language-spec edit.

**Cons**:
- Requires a designated-package mechanism in the type checker
  / loader.  Small piece of new infrastructure.
- User code can't impl their own interface for primitives
  without going through the stdlib carve-out package.
  Mitigated by: (a) the stdlib *can* impl any interface
  defined anywhere (`impl int : MyInterface` is allowed since
  `impl` may live anywhere per
  `plan-cross-package-interfaces.md`); but (b) the *methods*
  required by `MyInterface` can only be added to `int` from
  the carve-out package.  In practice the user writes their
  own type wrapper if they need a primitive-shaped value with
  custom methods.
- Composite types with no stdlib-provided comparison don't
  satisfy `Comparable`.  Same workaround as in Option 1a (user
  writes their own impl, or uses a function-value-comparator
  generic instead).

### Option 3 — Make primitives `TYP_NAMED`

Restructure the type system so universe primitives are
`TYP_NAMED` types with built-in underlying kinds.  Then methods
on primitives just work via the normal path — no carve-out, no
synthesis.

Mechanics:

- `predeclaredInt`, `predeclaredBool`, etc. (in
  `pkg/types/types.bn`) constructed as `TYP_NAMED` wrapping
  inner `TYP_INT` / `TYP_BOOL` / etc.
- All primitive-type identity checks (`t == TypInt()`,
  `t.Kind == TYP_INT`) audited and migrated to walk through
  the `TYP_NAMED` wrapper.
- The same `cannot extend types from another package` rule
  still bars user packages from declaring methods on `int` —
  the universe is "owned" by the language, not by user code.
  So a stdlib carve-out is still needed if anyone other than
  the language is going to declare methods on `int`.

**Pros**:
- Removes the special case in `resolveMethodReceiver` —
  primitives are just named types.

**Cons**:
- Touches every site that compares against primitive type
  identity.  Many sites; each is a small change but the
  aggregate is large.
- Doesn't actually solve the user-extends-primitives problem
  on its own — still needs a carve-out (Option 2's mechanism)
  to let any package declare methods on `int`.  So Option 3 is
  *prerequisite work* for Option 2, not an alternative.
- Risk of subtle regressions in the type checker /
  layout / IR-gen during the audit.

**Verdict**: Option 3 is invasive without delivering the
goal.  Skip unless we discover a more compelling reason to
do the named-primitive restructure.

## Recommendation

**Option 2 (stdlib carve-out).**  Reasons:

1. Aligns with Binate's design stance everywhere else
   (explicit > implicit; no duck-typing; impl is a real
   declaration).
2. No auto-derive problem — Option 1's biggest open question
   doesn't exist here.
3. Smallest surface in the type checker — a single
   `AllowUniverseRecv` flag and a package-identity check.
4. Stdlib code is normal Binate code — tooling, error
   messages, navigation all work the same as for user impls.
5. The closed-set argument that motivates Option 1 is a soft
   one once you commit to "stdlib provides the canonical
   primitive impls" — the closure is just whatever the stdlib
   ships, and stdlib evolution is a normal release-cycle
   concern, not a language-spec concern.

The two real costs of Option 2 — composite types not auto-
satisfying `Comparable`, and user packages not directly
extending primitives — are both mitigable in normal ways:
function-value comparators for custom orderings, type
wrappers for custom primitive-shaped values.  These are the
same mitigations one would reach for in Option 1a anyway.

## Implementation work

Sized for a single coherent landing once ratified.

### Slice 1 — Type-checker carve-out

- Add `Checker.AllowUniverseRecv` flag (bool).
- Loader / package-load driver sets the flag when entering
  the carve-out package.
- `resolveMethodReceiver` adds the exception: if the receiver
  base resolves to a universe primitive AND
  `c.AllowUniverseRecv` is set, accept the method.  Method
  registration goes through `AddMethod` on the primitive's
  `@Type` record (which today doesn't carry a method set —
  we extend it to do so).
- Open: the universe-type singletons (`predeclaredInt` etc.)
  are shared across the whole compiler session.  Methods
  added to them are visible everywhere, which is what we
  want.  Verify that test-isolation doesn't accidentally
  duplicate the singleton across runs.

### Slice 2 — Stdlib package: canonical primitive impls

- Pick the carve-out package name (`pkg/std`?  `pkg/builtin`?
  Consider: `pkg/builtin/testing` is already a thing).
- Define the canonical interfaces: `Comparable`, `Hashable`,
  `Stringer`, `Equatable`.  Likely in `pkg/std/iface.bni`
  (or split across files).
- Write the impls for each primitive: `int`, `int8`,
  `int16`, ..., `uint`, ..., `bool`, `char`, `byte`,
  `float32`, `float64`.

### Slice 3 — println rewrite

Once `*Stringer` accepts primitives uniformly, the
`bootstrap.println` builtin can be rewritten as a generic
(or a regular function over `...*Stringer` once raw-interface
variadics land — `claude-notes.md` § "Variadic params").
Removes one of the long-standing temporary hacks.

This slice depends on either generics landing (`plan-
generics.md` Slice 4) or raw-interface variadics landing
(currently undeclared on the timeline).  May be deferred
beyond the carve-out work itself.

## Open questions

- **Carve-out package name.**  `pkg/std`?  `pkg/builtin`?
  `pkg/lang`?  Doesn't materially affect the design but pins
  ergonomics.
- **Auto-import.**  Should the carve-out package be implicitly
  imported (like `bootstrap`)?  Probably yes — otherwise every
  user file that uses `Comparable` etc. needs an `import
  "pkg/std"`.  Auto-import wires the canonical impls into
  scope without ceremony.
- **Interface declaration location.**  The interfaces
  themselves (`Comparable` etc.) — do they live in the
  carve-out package alongside the impls, or in a separate
  always-imported `pkg/iface`?  Putting them in the carve-out
  package is simpler; splitting allows the carve-out to be a
  pure-impl package and the interface decls to be a slim
  always-imported namespace.
- **Composite type story.**  When a struct type wants to
  satisfy `Comparable`, the user writes the impl.  Open: any
  helper macros / language affordances for the common case
  (lexicographic-by-field)?  Probably defer — see if the
  pain is real once generics ship.
- **Float comparison.**  `Comparable` on `float32` /
  `float64` runs into NaN-ordering questions.  Stdlib needs
  to pick a convention (e.g., NaN sorts after everything;
  matches IEEE total ordering).  Pin in Slice 2.

## Cross-references

- `claude-todo.md` § "`print(42)` and friends: how do
  primitives implement interfaces? — DESIGN OPEN" — original
  problem statement and the two options recap.
- `claude-notes.md` § "Built-in implicit interfaces" — the
  `any` precedent that Option 1 builds on.
- `plan-generics.md` § "Hard dependency: primitives-implement-
  interfaces" — generics is the largest downstream consumer.
- `plan-interface-syntax-revision.md` — `impl T : I` syntax
  and the no-duck-typing stance.
- `plan-cross-package-interfaces.md` — `impl` may live in any
  package; the methods on T live with T's defining package.
- `feedback_println_hack.md` — `bootstrap.println` is a
  temporary hack that this plan unblocks the removal of.
