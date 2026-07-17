# Proposal: implicit borrow when constructing a raw interface value from a value (`proposal-implicit-any-borrow`)

Status: **PROPOSAL — under review, not yet ratified.** A language-semantics change
(relaxes a Stable rule, `iface.construct.no-implicit`). Motivated by `fmt.Print`; the
enabling "boxing" half of the decided `...*any` fmt direction (`claude-notes.md:252`).
Pairs with `proposal-slice-type-identity` (which is the *recovery* half).

## 1. Problem

The spec deliberately forbids constructing an interface value from a non-pointer source:

> `iface.construct.no-implicit` (§11.4, **Stable**) — Constructing an interface value from
> a non-interface source admits **no implicit conversions** — no implicit copy, no implicit
> address-of, no implicit `box`. The source must already be pointer-shaped (`*T`/`@T`)…
> *Rationale: an interface value can outlive its source, so the language refuses to
> silently capture a reference or copy.*
>
> `iface.construct.box` — A **value**-typed source must be made pointer-shaped first:
> write `&t` (a `*T` borrow) to construct a `*Iface`, or `box(t)` (heap-allocs a managed
> copy) to construct a `@Iface`.

So `fmt.Print("hi", 42)` is illegal — you must write `fmt.Print(&s1, &s2)`, which (a)
cannot express a literal or an expression result at all (they aren't addressable) and (b)
is ceremony on every call. This nearly defeats a `...*any` `fmt`. Relaxing it is a
language change, not an implementation gap (verified: the Stable rule explicitly forbids
it, with an explicit safety rationale).

## 2. Proposal

Permit an **implicit borrow** when constructing a **raw** interface value (`*Iface`,
including `*any`) from a source value that satisfies the interface:

- **Addressable source (lvalue)** — insert the `&` automatically: `MyOptions{Any:
  mything}` ≡ `MyOptions{Any: &mything}`. Lifetime is the source variable's; using the
  interface value past the source's lifetime is the ordinary raw-borrow contract
  (`mem.raw-uaf`, §18.7). This is *literally auto-inserting the `&` that `iface.construct.
  box` already tells you to write* — no new lifetime behavior.
- **Non-addressable source (literal / expression / call result)** — materialize the value
  into a **temporary co-scoped with the position it is bound into**: statement-scoped as a
  bare argument (`fmt.Print(42)`), binding-scoped when stored (`o := MyOptions{Any: 42}`
  desugars to `var __t = 42; o := MyOptions{Any: &__t}`, `__t` in `o`'s block). Ordinary
  temporary-lifetime extension (as C++/Rust do); **stack-only, no heap**.

**Applies in all positions, uniformly** — arguments, variadic spread, struct/array/slice
literals, `var` init, assignment, field/element stores. There is **no** positional
(argument-only) restriction: the implicit `&` must be legal wherever the explicit `&t`
is, else extracting a sub-expression to a local (`x := Struct{f: v}; foo(x)`) would
change legality — a refactoring hazard with no safety benefit.

### 2.1 Managed (`@Iface`) construction is NOT relaxed
Constructing a `@Iface`/`@any` from a value still requires explicit **`box()`** — because
making a value survive as a *managed* interface needs a **heap** allocation, and an
*implicit* one would violate the allocation-transparency principle (allocation is
source-determined; no hidden heap). Only the **raw** borrow (stack temp / `&lvalue`) is
made implicit. This split is forced and clean: raw = implicit stack borrow; managed =
explicit heap `box`.

### 2.2 The escape boundary
A stack temp (or an `&lvalue`) cannot outlive its frame/scope, so a constructed `*any`
that **escapes** — `return`ed, or stored into a location that outlives the temp/source
(an outer-scope variable, a global, a longer-lived field) — dangles. This is unavoidable
and not special to this proposal: it is exactly "you cannot `return &local`." Escaping
requires **`box()`** (heap) to be sound; otherwise it is raw-borrow UB (§18.7), diagnosed
only to the extent raw-borrow escapes are diagnosed generally (i.e. not, absent escape
analysis, which Binate rejects).

## 3. Why this is sound / proportionate

1. **No new danger over the status quo.** The explicit `&mything` this replaces is already
   legal everywhere and already dangles on escape/retention. The implicit form has the
   *identical* borrow, lifetime, and escape profile — it only removes the requirement to
   write `&`, and (for non-lvalues) auto-materializes a co-scoped temp.
2. **Zero heap.** Raw `*any` boxing is a stack temp or an `&lvalue`; allocation
   transparency is preserved (the managed/`@any` path stays explicit `box`, §2.1).
3. **Sibling of the managed→raw borrow.** value→`*any` is just an implicit borrow of the
   same kind as `@T → *T` (§8.4 `conv.managed-to-raw`), which is likewise allowed in all
   positions today. They should share one policy — see §5.
4. **Enables the ratified `...*any` fmt.** `fmt.Print("hi", 42)` needs value boxing at the
   call site; this is the only zero-heap way to get it (alternatives in §6).

## 4. Precise spec changes proposed (for the eventual ratified update)

- **`iface.construct.no-implicit` / `iface.construct.box` (§11.4)** — relax to permit the
  implicit borrow for a **raw** `*Iface` source value: auto-`&` for an addressable source,
  auto-materialized co-scoped temp for a non-addressable one; state the co-scoping and the
  escape→`box()` boundary. `@Iface` construction from a value **still requires `box()`**.
- **`conv.assignable` (§8.1)** — reconcile case 7 (S satisfies the interface / D is
  `*any`) with the relaxed construction: a value `S` satisfying interface `I` is assignable
  to `*I`/`*any` via the implicit borrow (the current text is overridden by
  `iface.construct.no-implicit`; this removes that override for the raw case).
- **Memory model cross-ref (§18)** — the constructed `*Iface` is a raw borrow governed by
  `mem.raw-uaf`; the auto-temp's lifetime is its binding scope.

## 5. Interaction with `proposal-restrict-implicit-raw-conversion`

§8.4 flags a Provisional proposal to **restrict** the implicit managed→raw borrow to
"genuine borrowing positions (argument passing)" and require an explicit `cast` to *store*
a raw borrow. value→`*any` is the **same kind of borrow**, so it must share that decision.
Note the tension this proposal surfaces: **restricting borrows in storing positions
reintroduces the `x := expr; f(x)` refactoring hazard** (extract-a-local would need an
explicit `&`/`cast` the inline form didn't). That is an argument *against* the storing
restriction, for both conversions. Recommendation: decide the storing-position policy
**once, for both**, and lean permissive (allowed everywhere, escape-is-UB — today's
model).

## 6. Alternatives considered (and why rejected)

- **Keep the rule; `fmt` uses `&`/`box`.** Cannot print literals/expressions at all;
  ceremony on every call. Guts the `...*any` design.
- **`...@any` + implicit `box`.** Safe (managed keeps the value alive), but a **heap
  allocation per argument** — violates allocation transparency and the zero-overhead
  rationale for choosing raw `*any`.
- **Keep `print`/`println` as compiler builtins.** No language change, but abandons the
  decided library-`fmt` direction (the builtins are explicitly transitional).
- **Variadic-`...*any`-only implicit boxing.** Narrower blast radius, but a syntactic
  special-case, and the user chose "general."
- **Argument-position-only (my earlier suggestion).** Rejected: creates the
  extract-a-local refactoring hazard (§2, §5) with no safety gain (the implicit `&` is as
  safe as the explicit one, which is allowed everywhere).

## 7. Open questions for ratification

1. **Scope: `*any` only, or all raw `*Iface`?** The auto-`&`/auto-temp logic is
   interface-agnostic — nothing about `any` is special. Relaxing `iface.construct.
   no-implicit` for **all** raw `*Iface` is cleaner than an `any`-only carve-out;
   `any`-only is the more conservative first step. Recommend **all raw `*Iface`**.
2. **The auto-temp + co-scoping mechanism** for non-lvalue sources is the one genuinely new
   thing — a hidden stack local with a binding-scoped lifetime. It's stack (not heap), so
   it doesn't breach the *heap*-allocation-transparency rule, but it *is* an implicit
   materialization. Acceptable, or should non-lvalue sources still require explicit `box`/a
   named temp (leaving only the auto-`&`-for-lvalues half)? (The latter would keep
   `fmt.Print(42)` illegal, so it largely defeats the motivation — noted for completeness.)
3. **Escape handling.** Accept UB-on-escape (no escape analysis, consistent with raw
   borrows) vs. a diagnostic requiring `box()` on a detectably-escaping construction (needs
   analysis Binate otherwise avoids). Recommend **UB-on-escape** for consistency.
4. **Storing-position policy** — decide jointly with `proposal-restrict-implicit-raw-
   conversion` (§5).

## 8. Relationship to `proposal-slice-type-identity`

Complementary and both needed for end-to-end `fmt.Print("hi", 42)`: **this** proposal gets
a value *into* a `*any` at the call site (boxing); **slice-type-identity** gets it back
*out* in `fmt`'s type switch (`case *[]readonly char:` / `case int:` recovery). Neither
suffices alone.
