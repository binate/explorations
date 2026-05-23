# Plan: Interface Syntax Revision

> **Status: RATIFIED 2026-05-01; IMPLEMENTED 2026-05-23.**
> All ratified decisions §1–§6 are live in the compiler (see
> per-section LANDED markers below).  §6 (universe `any`) closed
> end-to-end across type-checker, IR-gen, codegen, and VM, with
> iv→`any` upcast wired via an offset-0 OP_IFACE_UPCAST that
> reuses the source vtable wholesale.  Conformance coverage:
> 470–478 + 472 (.error).  The "Interfaces — IN PROGRESS" entry
> in `claude-notes.md` still reflects the *previous* design and
> should be updated to match this plan.

## Context

The existing interface design (see `claude-notes.md` § "Interfaces"
and `claude-discussion-detailed-notes.md` § 6) commits to:

- Explicit, declared interfaces with separate `impl` declarations.
- Methods defined Go-style outside `impl` blocks.
- Vtable-based dispatch.
- Interface values follow the managed/raw pattern: bare `Stringer`
  is the raw form, `@Stringer` is the managed form.
- Both forms are 2-word `(data_ptr, vtable_ptr)` value types.

The shape worked when raw slices were `[]T` and managed were `@[]T`:
the bare form was the "default raw" version. After the slice
migration to `*[]T` (raw) / `@[]T` (managed) — done because the
old shape repeatedly led to people unwittingly using unmanaged
slices and shipping use-after-free bugs — the same risk applies to
interfaces. People will reach for raw `Stringer` thinking it's the
safe default, capture an interface value past its data's lifetime,
and ship a UAF.

This plan revises the interface syntax to match: raw interfaces are
`*Stringer`, managed are `@Stringer`, and bare `Stringer` is no
longer a usable type — it's just a *referenceable name for an
interface*, not a type expression.

## Ratified decisions

### 1. Raw / managed interface syntax — LANDED

```
*Stringer    // raw interface value: (raw ptr to data, vtable ptr)
@Stringer    // managed interface value: (managed ptr to data, vtable ptr)
```

Bare `Stringer` is *not* a usable type expression. It only appears
inside `*Stringer` / `@Stringer`, in `impl T : Stringer` decls, and
on the LHS of an interface alias.

There is **no** `*const Stringer` / `@const Stringer` form. By the
slice analogy: `*const []T` isn't a thing either — `const` qualifies
the *element type* in `*[]const T`, not the slice value-tuple. For
interfaces there's no analogous element-slot for the const to bind
to (the data ptr inside the interface value points to a single T
of dynamic type), so the spelling has no place to put the const
qualifier. Const-restricted dispatch falls out of the impl side
(impls with const receivers are callable on a const view of T;
impls with mutating receivers aren't), not from a type-expression
qualifier on the interface value.

Pointer-to-interface-value follows the same precedent the slice
migration set:

| Meaning | Syntax |
|---|---|
| Raw interface value | `*Stringer` |
| Managed interface value | `@Stringer` |
| Raw ptr to raw interface value | `**Stringer` |
| Raw ptr to managed interface value | `*@Stringer` |
| Managed ptr to managed interface value | `@(@Stringer)` |

### 2. Top-level `interface` declaration form — LANDED

```
interface Stringer {
    toString() *[]const char
}
```

Replaces `type Stringer interface { ... }`. Reasons:

- Bare `Stringer` is no longer a usable type, so `type X = Y` doesn't
  cleanly fit (it'd be aliasing a thing-that's-not-a-type).
- Interfaces inherently need referenceable names (`impl T : I` won't
  work otherwise).
- Drops the awkward `type X interface { ... }` syntax that mixed
  type-alias mechanics with named-interface declaration.

The `type` keyword stays general for *types* — scalars, structs,
future enums. Interfaces are not types in this model; they're
named contracts that types satisfy.

### 3. No anonymous interfaces — LANDED

Drop `interface { ... }` as a type expression entirely.

Existing design allowed anonymous interfaces in some places (most
naturally: generic constraints, function parameter types). Under
the revised model, anonymous interfaces become awkward:

- `*interface { ... }` and `@interface { ... }` would be the only
  ways to use an anonymous interface as a type — the syntax reads
  poorly and doesn't compose.
- `impl T : interface { ... }` is technically expressible but mixes
  the named-and-declared model with structural matching.
- Generic constraints can always use named interfaces; the cost of
  naming is small and improves readability.
- Function parameter types: same — name the interface.

Drop them. One rule: interfaces are always declared, top-level,
and named.

### 4. Interface aliases — LANDED

```
interface MyStringer = Stringer
```

`type X = Y` aliases *type names*: Y must be a type expression.
Bare `Stringer` is **not** a type expression in this model, so
`type MyStringer = Stringer` is a type error. The dedicated form
`interface MyStringer = Stringer` bridges that gap for interface
names specifically.

What `type X = Y` *can* still do, since `@Stringer` and `*Stringer`
are full type expressions:
- `type S = @Stringer` — alias of the managed-interface-value type.
- `type S = *Stringer` — alias of the raw-interface-value type.
- `type S @Stringer` — newtype whose underlying is the managed-
  interface-value type. Distinct type, not an alias.

The interface alias is always nominal-equivalent. `MyStringer` and
`Stringer` are the same interface; `impl T : MyStringer` is
indistinguishable from `impl T : Stringer`. There is *no* newtype-
style "make this a distinct interface that happens to share the
shape" form — declare a fresh interface if you want that.

### 5. Five receiver kinds — UNCHANGED

The existing five-receiver-kind table from `claude-discussion-
detailed-notes.md` § 6.5 stays:

1. const value
2. const raw pointer
3. const managed pointer
4. raw pointer
5. managed pointer

Auto-conversion at impl-satisfies-interface call sites follows the
existing safe-direction-only rules.

### 6. `any` interface — LANDED 2026-05-23

`any` is the implicit universal interface. After the syntax shift,
the usable forms become `*any` and `@any`:

- `*any` — raw "pointer to anything." Type erasure escape hatch
  for callers that own the underlying data.
- `@any` — managed "pointer to anything." Type-erased managed value
  with refcount.

Same semantics as before; just spelled differently.

## Implementation work

### Type-checker changes — LANDED (audit 2026-05-22)

- ✅ Drop the `type X interface { ... }` parse path; replace with the
  `interface X { ... }` top-level declaration. (`pkg/parser/parse_decl.bn:35`)
- ✅ Drop the anonymous-interface type expression path entirely.
  (No `TEXPR_INTERFACE` AST kind.)
- ✅ Update interface-value type spelling: bare interface name is
  no longer a type; only `*Iface` / `@Iface`. (`pkg/types/resolve_type.bn:30-50`,
  `errInterfaceNotAType`; test 348.)
- 🤔 Reject `type X = Iface` (bare interface name on alias RHS) —
  flows through `resolveTypeExpr`'s bare-interface error path, but
  there's no dedicated negative test. Add a one-line test.
- ✅ Add interface-alias parse + resolution (`interface X = Y`).
  (`pkg/parser/parse_decl.bn:55-60`, `pkg/types/check_interface.bn:51-65`;
  test 369.)
- ✅ **`any` / `*any` / `@any`** — LANDED 2026-05-23.  Universe
  `any` registered as empty-method-set TYP_INTERFACE in both
  `pkg/types` (defineInterface in universeScope) and `pkg/ir`
  (registerUniverseAny seeded at InitModule).  `wrapAsIfaceValue`
  synthesizes per-(T, any) ImplInfo on demand; the existing
  vtable-emission path produces `__ivt.bn_<T_pkg>__<T>__any` as
  `[1 x i8*]` with T's dtor in slot 0.  iv→`any` upcast goes
  through OP_IFACE_UPCAST with offset 0 (IfaceParentSlotOffset
  short-circuits when target is universe `any`); the VM mirrors
  this by detecting `__any` in the encoded suffix and reusing
  srcVtIdxPlus instead of looking up a target vtable.  Conformance
  tests 470–478 (+ 472.error) cover construction, dispatch
  rejection on bare `any`, managed-field dtor invocation, multi-
  type dedup, arg-position coercion, and the raw-iv→@any
  lifetime-safety rejection.

### Codegen / runtime — UNCHANGED

The vtable-based dispatch story doesn't change. Layout is still
2-word `(data_ptr, vtable_ptr)`. Vtables remain per-(impl, interface)
static globals. Method calls through interface values remain vtable
indirect calls. None of this work is affected by the syntax
revision.

### Construction-site conversions — explicit only

When constructing an interface value from a non-interface source,
**no implicit conversions** happen — no implicit copies, no implicit
address-takes, no implicit boxes. The user writes the conversion
that crosses the lifetime boundary.

This contrasts with method-call receiver smoothing (e.g., `t.Foo()`
auto-takes `&t` for a `*const T` receiver), which is safe because
the receiver's lifetime is bounded by the call. An interface value,
once constructed, can outlive the source — so the same smoothing
would silently extend lifetimes.

| Source value | Into `*Iface` | Into `@Iface` |
|---|---|---|
| `t : T` (value) | Require explicit `&t` (then routes if impl matches `*T` / `*const T` / value receiver) | Reject — write `box(t)` to get `@T`, then `@T → @Iface` |
| `&t` (raw ptr from explicit address-of) | Direct, if impl matches | Reject — `*T` can't promote to `@T` |
| `t : *T` | Direct, if impl matches | Reject — `*T` can't promote to `@T` |
| `t : @T` | Direct (managed acts as raw data ptr) | Direct, if impl matches |
| `box(v)` → `@T` | (degenerate — round-trip, not common) | Direct, if impl matches |

Receiver-kind preference (informational, not a hard rule):
- `*T` and `*const T` are the common cases — caller guarantees the
  receiver's lifetime during the method call.
- `@T` receivers are for impls that need to *retain* the receiver
  (register it elsewhere, hand it off across boundaries, etc.).
- Value (`T`, `const T`) receivers operate on a copy.

### Migration — for already-shipped code

There is no shipped interface code (interfaces aren't implemented
yet — only IN PROGRESS in claude-notes). So the revision is a pre-
implementation plan revision, not a code migration. Once
interfaces land for real, they land in the revised form directly.

## Ratification notes (2026-05-01)

The four open questions raised in the original draft were resolved
as follows:

- **§4 alias syntax**: Option A. `type X = Y` aliases type names and
  Y must be a type expression; bare `Stringer` isn't one. The
  dedicated `interface X = Y` form covers interface-name aliasing.
- **`type X = Y` vs `type X Y` support**: confirmed. Binate's parser
  already handles both forms uniformly (`parseTypeSpec` in
  `pkg/parser/parse_decl.bn`), with `struct { ... }` being just an
  anonymous type expression on the RHS — not a special case.
- **Receiver smoothing at construction sites**: no implicit
  conversions. See "Construction-site conversions — explicit only"
  above for the full table and rationale (lifetime crosses a
  boundary, unlike method-call smoothing).
- **`*const Stringer`**: dropped — not a thing. By analogy with
  `*const []T` (also not a thing), `const` has no natural slot in
  the interface-value spelling. Const-restricted dispatch is
  expressed at the impl level (const receivers), not at the
  interface-value-type level.

## Cross-references

- `claude-notes.md` § "Interfaces" — currently reflects the
  pre-revision design and needs to be updated to match this plan.
  TODO once Phase 2 (bare-name-as-type-expression) lands.
- `claude-discussion-detailed-notes.md` § 6 — full design history.
- `plan-function-values.md` — function values reuse the vtable
  machinery from this plan but are independent at the *frontend*
  level (function values are structural, not user-declared
  interfaces).
- `claude-todo.md` — "Interface types" entry.
