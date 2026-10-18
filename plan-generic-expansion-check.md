# Plan: a static check for infinite generic instantiation

Tracking: claude-todo.md, "Infinite generic instantiation is caught only by depth bounds — a chain that branches
hangs the compiler; add a static expansion check" (work-7).  The linear case landed as binate `8bbfe8777`.

## The problem

§12.3 `gen.mono.instances`: the instantiations a program names — each one it writes with concrete arguments, and
what those name through fields, every method's signature and body, parameterized impls, interface methods and
parents — must be a finite set; an infinite one is a compile-time error.  A generic struct whose fields alone reach
ever-growing instantiations is rejected at its declaration, instantiated or not.  An implementation may bound a
chain of instantiations at 128 or more.

Today the checker finds infinite sets only by depth bounds:
- building an instance (populate) eagerly instantiates what its fields and its methods' signatures name, nesting
  until `GenericInstDepth` reaches 128 ("nested too deeply (cyclic or unbounded type argument?)");
- per-instance body checks go through the drain, whose chains stop at `MAX_INSTANCE_DEPTH` (128), depth-first, and
  the first overflow stops the drain.

That has three defects:
1. **Hang on a branching family.**  `func (b *Box[V]) A() Box[Opt[V]]` plus `B() Box[Res[V]]`, or fields
   `a *T2[Opt[X]]; b *T2[Res[X]]`: a build explores ~2^127 instances before the depth bound.
2. **Uninstantiated generic rejected.**  A generic whose methods' signatures name ever-growing instances is rejected
   at its declaration even when the program never instantiates it: checking its own methods (with type parameters
   abstract) builds `Box[Opt[V]]`, whose methods build `Box[Opt[Opt[V]]]`, … to the bound.  The spec names no
   instance there.
3. **Order-dependent chain bound.**  Build nesting adds to a body chain's depth, so a long finite chain is accepted
   or rejected depending on which naming comes first.  The static check does not address this one: an instance keeps
   the chain depth of its first naming in the drain.  §12.3 allows the bound of 128 on such a chain; making the
   verdict independent of naming order is a separate question (not part of this plan).

Bodies need no static check: they are not built eagerly, and the drain's depth-first walk with its stop at the first
overflow ends a branching family through bodies after one 128-long path.

## The static check

A graph over generic declarations' type parameters, from what each declaration *writes* (syntax, resolved by
name in the declaration's scope — no instantiation needed):
- a generic struct's fields, and a non-struct generic type's underlying type;
- each method's signature (receiver binders are the type's parameters by position);
- a generic interface's method signatures and parents;
- a generic alias's target (its uses are as if the target were written there).

For each `H[a₁ … aₙ]` written in G's declaration, and each parameter Pᵢ of G occurring in aⱼ: an edge
(G, i) → (H, j), weight 0 when aⱼ is Pᵢ itself, 1 when Pᵢ is nested inside a type constructor in aⱼ.  An edge is a
*field* edge when written in fields / the underlying type (or through an alias used there), a *method* edge
otherwise.  A cycle of positive weight is an infinite family (the Go type checker's monomorphization check uses
the same graph).

Verdicts:
- a positive cycle through field edges only: reject each generic on it at its declaration (§12.3), whether
  instantiated or not — this replaces today's depth-bound detection of that rule;
- a positive cycle (any edges) reachable from generic G: G is *expanding* — naming a concrete instance of G is an
  error at the naming ("the instantiations G[A] names are infinite: G[A] names G[Opt[A]], …"), and the instance is
  not built (TypError, no cascade).

The graph is computed once per generic, lazily, on the first instantiation of it (or of anything reaching it), over
the declarations reachable from it; all generic declarations are collected by then (instantiation during collection
already relies on that for fields).

## The open design question: abstract instances of an expanding generic

Checking G's own declarations builds abstract instances (`Box[Opt[V]]` in `Wrap`'s signature and body).  Building
one derives its methods' signatures, which build the next level: defect 2, and defect 1 for a branching family even
with no concrete instance.  With the static verdict in hand there are two ways to stop that:

- **(A) Derive an instance's methods only when they are needed, for every instance** (on the first method lookup or
  method-set query).  Removes eager derivation generally (also less work for instances whose methods are never
  used), but every reader of an instance's method table — about 38 sites in the checker, plus IR-gen, the VM and
  lint — has to go through the derivation, and IR-gen must not see an underived instance.  Large and wide.
- **(B) Only for an expanding generic: do not derive the methods of an abstract instance built while deriving
  another instance's methods** (it is a step of the infinite family), deriving them on demand only at the few
  checker sites that look a method up on a type the program wrote (selectors, impl satisfaction).  Concrete
  instances of an expanding generic are never built (above), so IR-gen never meets one.  Small and contained, at
  the cost of a special case.

Recommendation: (B) — the static check makes every concrete case an error, so the special case is confined to
abstract checking, where nothing is emitted.  **Decided 2026-10-18 (user): "(B) Targeted, expanding only".**  Not
returning the error type for such an instance instead: that would accept invalid code in the generic's own bodies
(`return b.Wrap()` from a method declared `int`), which the abstract check must still reject.

## Tests

- conformance: the branching method family (instantiated → error at the naming; uninstantiated → accepted), the
  branching field family (rejected at the declaration, instantiated or not), the long finite chain accepted in either
  naming order, the existing 081 / 284 / 120 / 765;
- unit: the graph and its weights (bare parameter vs nested; alias targets; receiver binders renamed), and that a
  cycle through a method edge does not reject an uninstantiated generic.
