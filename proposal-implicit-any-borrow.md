# Proposal: implicit borrow when constructing a raw interface value from a value (`proposal-implicit-any-borrow`)

Status: **RATIFIED — Draft, pending implementation** (2026-07-17; spec `docs f8cdd0a`).
A language-semantics change (relaxes a Stable rule, `iface.construct.no-implicit`).
Motivated by `fmt.Print`; the "boxing" half of the decided `...*any` fmt direction
(`claude-notes.md:252`). Pairs with `proposal-slice-type-identity` (the *recovery* half).
Adversarially reviewed (SOUND-WITH-MUST-FIXES; all fixes folded in). The two open
questions were resolved by adopting the recommendations: **all raw `*Iface`** (not
`*any`-only), and the rvalue form permitted at **argument + `var`/`:=`-init** positions.
Spec'd as Draft in `docs/spec` (§11.4 `iface.construct.value-borrow`); a `bnlint` rule
for the escaping-borrow visibility gap (§4) remains a companion implementation item.

> **What the review changed.** The earlier draft claimed the implicit form is "identical to
> explicit `&`, so allowed uniformly in all positions." Review found that is true only for
> an **addressable (lvalue)** source. For a **non-addressable (rvalue)** source there is no
> `&`-form to be identical to — the compiler materializes a temporary, which is released at
> statement end (§18.4 `mem.temporary`) — so the rvalue form is **unsound when it stores
> into a pre-existing location** (`someStruct.field = 42`, `arr[i] = 42`, `return`). The
> proposal is now split into a fully-general **lvalue** half and a **scope-restricted
> rvalue** half.

## 1. Problem

The spec deliberately forbids constructing an interface value from a non-pointer source:

> `iface.construct.no-implicit` (§11.4, **Stable**) — Constructing an interface value from
> a non-interface source admits **no implicit conversions** — no implicit copy, no implicit
> address-of, no implicit `box`. The source must already be pointer-shaped (`*T`/`@T`)…
> *Rationale: an interface value can outlive its source, so the language refuses to
> silently capture a reference or copy.*

So `fmt.Print("hi", 42)` is illegal — you must write `fmt.Print(&s1, &s2)`, which cannot
express a literal or an expression result (they aren't addressable) and is ceremony on
every call. This nearly defeats a `...*any` `fmt`. Relaxing it is a language change, not an
impl gap (verified: the Stable rule explicitly forbids it, with a safety rationale).

## 2. Proposal

The relaxation applies **only to a value-typed (non-pointer-shaped) source**. A `*T`/`@T`
source is **unchanged**: it continues under `iface.construct.managed` (§11.4) — a `@T`
constructs a raw `*Iface` as a **borrow, no reference taken**; no temp, no refcount change.
So `MyOptions{Any: someManagedT}` (`someManagedT : @T`) is the existing borrow, **not** a
copy or a refcount drop. The two sub-cases below concern only genuinely value-typed sources.

### 2a. Addressable value (lvalue) — auto-`&`, all positions
For an lvalue source (`mything`, a field, an element) insert the `&` automatically:
`MyOptions{Any: mything}` ≡ `MyOptions{Any: &mything}`. **Byte-identical to the explicit
`&`** the spec already tells you to write — same borrow, same lifetime, same escape
contract (`mem.raw-uaf`, §18.7). **Allowed in all positions** wherever `&mything` is (i.e.
everywhere). This is a strict **sibling of the managed→raw borrow** `@T→*T` (§8.4): both
borrow a named, lifetime-backed thing. This half alone resolves the reported ergonomic wart
(`x := MyOptions{Any: mything}; foo(x)` compiles, matching the inline form).

### 2b. Non-addressable value (rvalue) — auto-temp, scope-restricted
For an rvalue source (a literal, an expression, a call result) there is **no `&`-form** (you
cannot write `&42`); the compiler must **materialize** the value into a temporary. A
temporary is released **at the end of its statement** (§18.4 `mem.temporary`), so this form
is sound **only where the borrow cannot outlive that temporary** — where the temp is
statement-transient or co-scopes with a fresh binding:

- **Argument positions** (incl. variadic spread) — `fmt.Print(42)`: statement-transient, the
  callee runs within the statement. ✓
- **`var` / `:=` initialization** — `o := MyOptions{Any: 42}` desugars to `var __t = 42; o
  := MyOptions{Any: &__t}`, `__t` co-scoped with `o`. ✓

It is **rejected** (requires an explicit `box(t)`, or a named longer-lived local) in
positions that **store into a pre-existing location** outliving the statement:

- **assignment** — `someStruct.field = 42`;
- **field / element store** — `arr[i] = 42`, an `@[]*any` element;
- **`return`** of the constructed value.

There is no binding to co-scope to and no stack lifetime matching the target's, so the temp
would dangle at the semicolon — a direct violation of §18.4. So there **is** a positional
distinction, but only for the rvalue sub-case, and it is **soundness-forced**, not the
arbitrary argument-only restriction the earlier draft weighed. It does **not** hit the
motivating cases: lvalue works everywhere (2a), and rvalue `var`-init works (2b), so the
extract-a-local refactoring is preserved for both. Only *storing a fresh value-borrow into a
pre-existing, longer-lived location* is rejected — genuinely unsound and rare.

### 2c. Managed (`@Iface`) construction is NOT relaxed
Constructing a `@Iface`/`@any` from a value still requires explicit **`box()`** — making a
value survive as a *managed* interface needs a **heap** allocation, and an implicit one
would violate allocation transparency (allocation is source-determined; no hidden heap).
Only the **raw** borrow (stack temp / `&lvalue`) becomes implicit.

## 3. Value semantics: construction BORROWS, it does not copy

Unlike Go's `interface{}` (which **copies**), construction here **borrows**. For a transient
formatter argument this is invisible. But for a **stored** `*any` it means the interface
observes **later mutations of the source**: `o := MyOptions{Any: x}; x = 5; use(o.Any)` sees
`5`, not the construction-time value — a real divergence a Go programmer will not expect.
This is a second reason the rvalue form is confined to transient/init positions (§2b), and
it **must be stated plainly** in any spec text. A caller wanting a snapshot uses `box(x)`
(managed, copied, escape-safe).

## 4. Escape, and the visibility/lint gap

A constructed `*Iface` is a raw borrow; escaping its temp's/source's lifetime (returned, or
stored into a longer-lived location) dangles — the same as `return &local`. For the rvalue
form (2b) escape is **rejected at the checker** in `return`/stored positions, closing most
of the surface. For the **lvalue** form (2a) an escape is still expressible (`return
SomeIface{Any: localVar}`) and is ordinary raw-borrow UB (§18.7) — **but it is now an
*invisible* borrow**: no `&` appears at the site, so it reads like value construction, and
existing raw-escape lints (`bnlint`'s `raw-slice-return`, which key on a visible borrow) miss
it. This removes exactly the guard §11.4's original rationale installed. **So this proposal
must add a `bnlint` rule** flagging a raw interface value constructed from a local that
escapes (implicit or explicit) — restoring the visibility, without which the relaxation is a
new silent-UB surface.

## 5. Why this is sound / proportionate

1. **Lvalue (2a): genuinely identical to the explicit `&`** — same borrow/lifetime/escape;
   a strict sibling of `@T→*T`. Sound wherever `&mything` is (everywhere).
2. **Rvalue (2b): a NEW construct with no explicit-`&` analogue** (there is no `&42`). Its
   only honest baseline is `box(42)` (managed, heap, value-copy, escape-safe); against that
   it **trades escape-safety and copy-semantics for zero-heap**. Its soundness is **not**
   "identical to explicit `&`" (that earlier claim is retracted) — it rests on the
   **restricted scope** of §2b, confined to positions where the temp provably outlives the
   borrow.
3. **Zero heap** for the raw path (stack temp / `&lvalue`); `@any` stays explicit `box`
   (§2c), so allocation transparency holds.
4. **Enables the ratified `...*any` fmt** — `fmt.Print("hi", 42)` (all rvalues at argument
   positions) is exactly the sound case; it is the only zero-heap way to get it.

## 6. Relationship to `proposal-restrict-implicit-raw-conversion`

§8.4 flags a Provisional proposal to restrict the implicit managed→raw borrow to genuine
borrowing positions. The two halves relate differently:

- **Lvalue (2a) IS the same kind of borrow as `@T→*T`** (both borrow a named,
  lifetime-backed thing) and should share whatever storing-position policy that proposal
  sets.
- **Rvalue (2b) is NOT** — `@T→*T` borrows an *existing owned allocation* (backed by a
  refcount the programmer can extend to cover a store); the rvalue temp is backed by *nothing
  but a stack frame*. A "store is fine, escape is UB" policy defensible for `@T→*T` (an owner
  can keep the store alive) is **unsound for the rvalue case** (the store dangles
  unconditionally). So 2b's restrictive storing rule is **independent** of the `@T→*T`
  policy and required regardless. (The earlier draft's "decide once for both, lean
  permissive," and its "restricting stores reintroduces the `x := expr; f(x)` refactoring
  hazard," are both retracted: the extract-local case is preserved by 2a/2b-init; the only
  rejected case is rvalue-store-into-pre-existing, which is soundness-forced.)

## 7. Precise spec changes proposed (for the eventual ratified update)

- **`iface.construct.no-implicit` / `iface.construct.box` (§11.4)** — relax to permit the
  implicit borrow for a **raw** `*Iface` **value** source: auto-`&` for an addressable
  source in any position (2a); auto-materialized temp for a non-addressable source **only in
  argument and `var`/`:=`-init positions**, rejected in assignment / field-store /
  element-store / `return` (2b). State the borrow-not-copy semantics (§3) and that `@Iface`
  from a value still requires `box()` (2c). `*T`/`@T` sources stay on
  `iface.construct.managed` unchanged.
- **`conv.assignable` (§8.1)** — reconcile case 7 with the relaxed construction: a
  **value** `S` satisfying interface `I` is assignable to a **raw** `*I`/`*any` via the
  implicit borrow, subject to 2a/2b. Tighten case 7 to say raw-only (it must not read as also
  permitting value→`@any`, which §2c forbids). (Case 7 is genuinely gated by
  `iface.construct.no-implicit` today, per §8.1's own deferral Note to Ch.11 — so this is a
  real relaxation, not an impl gap.)
- **`bnlint` rule** — flag a raw interface value constructed from a local that escapes (§4).
- **Memory-model cross-ref (§18)** — the constructed `*Iface` is a raw borrow (`mem.raw-uaf`);
  the auto-temp is a statement/binding-scoped temporary (§18.4).

## 8. Decisions & remaining follow-ups

**RESOLVED at ratification (2026-07-17):**
1. **Scope** — **all raw `*Iface`** (not `*any`-only). The auto-`&`/auto-temp logic is
   interface-agnostic; nothing about `any` is special.
2. **Rvalue scope (2b)** — **argument + `var`/`:=`-init** (not argument-only). Preserves the
   literal extract-local (`o := MyOptions{Any: 42}; foo(o)`); the escape-if-the-bound-var-is-
   returned hole is the same one lvalue-var-init has, mitigated by the §4 `bnlint` rule.

**Follow-ups for implementation:**
3. **The auto-temp mechanism (2b)** is the one genuinely new thing — a compiler-materialized
   stack local with a binding/statement-scoped lifetime. Stack (not heap), so no
   *heap*-transparency breach, but it is an implicit materialization. Acceptable, or should
   even rvalue sources require explicit `box`/a named temp (leaving only 2a + transient-arg
   rvalue)? The latter largely re-cripples `fmt.Print(42)`, so noted for completeness.
4. **`bnlint` escape rule (§4)** — confirm scope (all escaping raw-iface-from-local, implicit
   and explicit).
5. **Storing-position policy for 2a** — decide jointly with
   `proposal-restrict-implicit-raw-conversion` (§6).

## 9. Alternatives considered (and why rejected)

- **Keep the rule; `fmt` uses `&`/`box`.** Cannot print literals/expressions; ceremony on
  every call. Guts the `...*any` design.
- **`...@any` + implicit `box`.** Safe, but a **heap allocation per argument** — violates
  allocation transparency and the zero-overhead rationale for choosing raw `*any`.
- **Keep `print`/`println` as compiler builtins.** No language change, but abandons the
  decided library-`fmt` direction (the builtins are explicitly transitional).
- **Variadic-`...*any`-only implicit boxing.** Narrower blast radius, but a syntactic
  special-case; the driver chose "general."

## 10. Relationship to `proposal-slice-type-identity`

Complementary and both needed for end-to-end `fmt.Print("hi", 42)`: **this** proposal gets a
value *into* a `*any` at the call site (boxing); **slice-type-identity** gets it back *out* in
`fmt`'s type switch (`case *[]readonly char:` / `case int:` recovery). Neither suffices alone.
