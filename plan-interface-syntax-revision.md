# Plan: Interface Syntax Revision

> **Status: DRAFT** — proposed but not ratified. Several open
> questions noted inline. The current "Interfaces — IN PROGRESS"
> entry in `claude-notes.md` reflects the *previous* design; once
> the questions here are settled, that section should be updated
> to match.

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

## Decisions to ratify

### 1. Raw / managed interface syntax

```
*Stringer    // raw interface value: (raw ptr to data, vtable ptr)
@Stringer    // managed interface value: (managed ptr to data, vtable ptr)
*const Stringer  // const raw interface value
```

Bare `Stringer` is *not* a usable type expression. It only appears
inside `*Stringer` / `@Stringer`, in `impl T : Stringer` decls, and
on the LHS of an interface alias.

Pointer-to-interface-value follows the same precedent the slice
migration set:

| Meaning | Syntax |
|---|---|
| Raw interface value | `*Stringer` |
| Managed interface value | `@Stringer` |
| Raw ptr to raw interface value | `**Stringer` |
| Raw ptr to managed interface value | `*@Stringer` |
| Managed ptr to managed interface value | `@(@Stringer)` |

### 2. Top-level `interface` declaration form

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

### 3. No anonymous interfaces

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

### 4. Interface aliases

Two candidate syntaxes — open question, see below.

```
// Option A (preferred per design discussion):
interface MyStringer = Stringer

// Option B:
type MyStringer = Stringer
```

The case for **Option A** (`interface X = Y`):
- Keeps `type` strictly about types. Interfaces aren't types in this
  model, so they shouldn't ride along on the type-alias mechanism.
- Mirrors `interface X { ... }` for the declaration form.
- Reads at-a-glance: this declaration creates an interface name.

The case for **Option B** (`type X = Y`):
- Reuses the existing alias mechanism. Less new syntax.
- Type aliases already work for any named type (scalars, structs);
  extending to interfaces is "natural."
- Counter to A: the alias *target* is the interface name, which is
  arguably a "type-like name" even if not strictly a type.

**Open question — to resolve before ratification.** Project bias is
toward A (consistency with "interfaces are not types"); leaving the
final call to the implementer/reviewer.

Either way: the alias is always nominal-equivalent. `MyStringer`
and `Stringer` are the same interface; `impl T : MyStringer` is
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

### 6. `any` interface — UNCHANGED

`any` is the implicit universal interface. After the syntax shift,
the usable forms become `*any` and `@any`:

- `*any` — raw "pointer to anything." Type erasure escape hatch
  for callers that own the underlying data.
- `@any` — managed "pointer to anything." Type-erased managed value
  with refcount.

Same semantics as before; just spelled differently.

## Implementation work

### Type-checker changes

- Drop the `type X interface { ... }` parse path; replace with the
  `interface X { ... }` top-level declaration.
- Drop the anonymous-interface type expression path entirely.
- Update interface-value type spelling: bare interface name is no
  longer a type; only `*Iface` / `@Iface` (and pointer/const
  variants).
- Add interface-alias parse + resolution per the decision in §4.

### Codegen / runtime — UNCHANGED

The vtable-based dispatch story doesn't change. Layout is still
2-word `(data_ptr, vtable_ptr)`. Vtables remain per-(impl, interface)
static globals. Method calls through interface values remain vtable
indirect calls. None of this work is affected by the syntax
revision.

### Boxing — UNCHANGED in spirit

The existing boxing rule applies, with names updated:

- `*Stringer` (raw): compiler implicitly boxes a stack-local copy of
  the data when the source is a value type. Zero-cost; the raw
  contract is "caller keeps data alive."
- `@Stringer` (managed): explicit `box(value)` required when the
  source is a value type. No hidden heap allocations.

### Migration — for already-shipped code

There is no shipped interface code (interfaces aren't implemented
yet — only IN PROGRESS in claude-notes). So the revision is a pre-
implementation plan revision, not a code migration. Once
interfaces land for real, they land in the revised form directly.

## Open questions

- **§4 alias syntax** (Option A vs. Option B). Default to Option A;
  consider B if the implementer finds it cleaner during integration.
- **`type X = Y` for non-interface named types** — does Binate
  currently support both `type X = Y` (alias) and `type X Y`
  (newtype)? Need to confirm before §4 lands. (This plan only
  handles the *interface* alias form; newtype-of-interface is
  explicitly excluded.)
- **Receiver smoothing at boxing site** — when boxing a value type
  into a `*Stringer` and the `impl` declares a managed receiver
  (`impl @T : Stringer`), is the auto-box implicit? Probably not —
  raw interface values can't auto-promote to managed. Worth pinning
  down a concrete example or two when implementing.
- **`*const Stringer` semantics** — does this read as "raw pointer
  to a const interface value" (i.e., the interface value can't be
  reassigned) or as "raw pointer to a value through a const-receiver
  impl"? Need to clarify before implementation.

## Cross-references

- `claude-notes.md` § "Interfaces" — current (pre-revision) design.
  Will need updating once this plan ratifies.
- `claude-discussion-detailed-notes.md` § 6 — full design history.
- `plan-function-values.md` — function values reuse the vtable
  machinery from this plan but are independent at the *frontend*
  level (function values are structural, not user-declared
  interfaces).
- `claude-todo.md` — "Interface types" entry.
