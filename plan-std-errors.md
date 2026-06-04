# Plan: `present` builtin + `pkg/std/errors`

Status: COMPLETE (shipped; errors package landed, conformance 545/577).
Kept for design rationale — the `present`/empty-not-nil decision and the
`Error` interface shape, plus still-open deferrals.

Designs a Go-style error interface for Binate — and the small language
enabler it requires. Settled through discussion 2026-06-02/03.

## Why this needs a language change first

Errors-as-values is already the decided policy (`claude-notes.md:240`,
`differences-with-go.md:93`): no exceptions, errors returned last in a
tuple, conventions "expected to emerge (like Go's error interface)."
This plan fills that intentionally-left blank.

But the natural shape — `func f(…) (T, @Error)` with "no error" as the
empty interface value, checked at the call site — is **not expressible
today**: an interface value cannot be tested for emptiness. Probed on
current main:

- `var e @Iface = nil` → *"cannot assign nil to @Iface"*
- `e == nil` / `e != nil` → *"mismatched types @Iface and nil"*

(Managed/raw *pointers* nil fine; this is specific to interface
*values*, which are 2-word `{data, vtable}` aggregates.)

### Resolution: present/empty, NOT nil

`nil` for interface values would be the wrong fix on two counts:

1. **It contradicts a reaffirmed decision.** `claude-notes.md:1141`
   ("Nil slices — DECIDED, reaffirmed 2026-04-03"): `nil` is for pointer
   types only; aggregates (slices) use emptiness / **introspection
   (e.g. `rt.HasBacking`)**, never nil — *explicitly* to avoid Go's
   nil-vs-empty confusion. An interface value is a 2-word aggregate, the
   same family as a managed-slice, so it should follow the same rule.
2. **For interfaces, `== nil` is not just confusing but dishonest** —
   Go's *typed nil*: boxing a nil `*T` into an interface yields a value
   that is `!= nil` (the slot is filled with a real `*T`-impl whose data
   is null). "Is the slot filled?" and "is the inner value nil?" are
   different questions; `== nil` conflates them.

So we add a **presence test**, mirroring `rt.HasBacking` for slices,
that honestly answers "is this interface slot filled?" — independent of
whatever the inner value is.

## Part 0 — the `present` builtin (the enabler)

A new builtin `present(iv) bool`. `iv` is an interface value (`@Iface`
or `*Iface`). Returns true iff the slot holds a concrete impl —
**tested on the vtable word being non-null**. The empty interface value
is simply the zero value (`var e @Error` — already constructible and
returnable today; `var e @Error; return e` compiles). There is no
`nil` for interface values and no `empty()` — you write `present(e)` /
`!present(e)`.

Honesty by construction: boxing a nil `*T` sets the vtable, so
`present(iv)` is **true** (there is a `T`-impl here; its data happens to
be null). The "is the inner pointer nil?" question, if ever needed, is
separate (ask the impl).

A builtin (not an `rt` function like `HasBacking`) because interface
values have no `len`-equivalent for the pervasive "is there one?" check
— `if present(err)` is written constantly, should be ambient, and reads
like `len`.

This is a self-contained language feature usable by any `(T, @Iface)`
API, not just errors.

## Part 1 — `pkg/std/errors`

Tier-1 standards-library package (parallel-tree layout):

```
ifaces/stdlib/pkg/std/errors.bni                    package "pkg/std/errors"
impls/stdlib/common/pkg/std/errors/errors.bn        package "pkg/std/errors"
```
No build-flag change — the existing `-I ifaces/stdlib` /
`-L impls/stdlib/common` roots cover it. Consumers `import "pkg/std/errors"`
and reference `@errors.Error` / `errors.New` / `errors.Wrap`.

### Interface (in `errors.bni`, exported)

```binate
interface Error {
	Error()  @[]char     // message for this layer (+ chain, by convention)
	Unwrap() @Error      // the wrapped cause; an empty @Error for a leaf
}

func New(msg @[]char) @Error
func Wrap(cause @Error, msg @[]char) @Error
```

Rationale for the shape:

- **`Error() @[]char`** (owned), mirroring `Stringer.String() @[]char`;
  a message is rendered/built, so it's in the allocating bucket.
- **Mandatory `Unwrap()` in the base interface** (Rust's
  `Error::source()` model, not Go's optional-via-RTTI one). Optional
  wrapping needs type-assertion — the very RTTI we're deferring — so the
  *only* way to have wrapping pre-RTTI is to bake the accessor in. More
  importantly it's the right call regardless: without a *standard*
  mandatory accessor, a package that gets a dependency's error must
  either swallow it, re-stringify it (forcing the caller to parse text —
  always wrong), or pass it through raw (leaking the dependency).
  Mandatory `Unwrap()` makes "add context, preserve cause, stay
  programmatically walkable" the path of least resistance. Go could only
  add it late and therefore optional (1.13); we don't have that
  constraint.
- **Single parent (`Unwrap() @Error`), permanently** — not
  `@[]@Error`. Context-chaining (one parent, the common case) and
  multi-error *aggregation* (join) are different concerns; only the
  first belongs in the base interface. If aggregation is ever wanted it
  is a **sibling** opt-in (`interface MultiError { Causes() @[]@Error }`)
  that a future RTTI-based `Is`/`As` checks for — no change to `Error`.
  (Binate already has the accumulate-a-list pattern for "collect many
  independent errors.") So `@[]@Error` in the base buys nothing against
  future change and taxes the common case.
- **Location `pkg/std/errors`, not tier-0 `pkg/builtins/lang`.** `lang`
  holds interfaces the *language machinery consumes* (`Stringer`←`print`,
  `Comparable`←dispatch); `Error` has no language consumer, so on that
  principle it's a convention, not a primitive. Keeping the interface +
  `New`/`Wrap` cohesively in one `errors` package beats Go's
  builtin-here / helpers-there split (which only exists because Go made
  `error` a compiler builtin). The import + `@errors.Error` qualifier is
  the same honest cost as any other cross-package type.

### Implementation notes

- `wrappedError.Error()` builds `"msg: " + cause.Error()` fresh via
  `make_slice` (the `strconv.Format*` idiom — tier-1 packages avoid
  `pkg/binate/buf`, which is tier-2). Auto-prepend matches
  `fmt.Errorf("…: %w")`.
- The `cause @Error` field of `wrappedError` is a managed-iface struct
  field; its dtor cleanup is already supported.
- `New` / `Wrap` box a heap-allocated concrete error into `@Error`
  (the `368_iface_managed` boxing form).

## Deferred (explicitly out of v1)

- `Errorf`/formatting — no varargs/`fmt` yet; callers build messages
  with `make_slice` (or `strconv`).
- `errors.Is` / `errors.As` — need RTTI / interface-value identity
  comparison.
- Multi-cause sibling interface (`MultiError`) — only if a real need
  appears; additive, never a change to `Error`.
- A generic `Zero[@Error]()` ergonomic constructor for the empty value
  — once generics make it worthwhile; `var e @Error` suffices for now.
