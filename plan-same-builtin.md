# Plan: `same` builtin — reference identity

Status: DESIGN SETTLED (discussion 2026-06-07/08). Implementation in
progress. Follow-ups: `errors.Is` (on `same`), then `io.IsEOF` (on
`errors.Is`); `present()`-for-all-types is a separate, later pass (to be
discussed in detail first).

## Why

`==`/`!=` is now rejected on the multi-word reference types — slices,
interface values, func values (see the `==`-comparability work). That
removes the *footgun-y operator spelling*, but reference **identity** is
still a real, needed capability — most concretely, sentinel detection
("is this error THE `io.EOF` object?"), which `errors.Is` is built on.
`present(iv)` answers "is the slot filled", NOT "is it *this* referent".
So we add an explicitly-named identity test rather than re-admitting `==`.

`same` is to identity what `present` is to emptiness: a clearly-named
builtin that does the thing `==` would mislead about. The name says
"the same one" (identity), not "equal-looking" — deliberately avoiding
`identical` (which colloquially evokes *identical copies*, i.e. distinct
but equal — the wrong idea).

## Semantics

`same(a, b) bool` — true iff `a` and `b` denote the **same underlying
thing**. Per type:

| Type | meaning | words compared |
|---|---|---|
| pointer `*T` / `@T` | same address | the 1 pointer word |
| interface value `*Iface` / `@Iface` | same boxed object | `{data, vtable}` (both) |
| slice `*[]T` / `@[]T` | same view | `{data, len}` (the view; first 2 words) |

`same` is the properly-lowered word-wise comparison `==` *would* have done
on these types, spelled to signal identity instead of value-equality.

- Interface value: both words. `data==data && vtable==vtable`. Handles
  typed-nil correctly — two nil-boxed values of *different* concrete types
  have equal-null `data` but different `vtable` → correctly NOT same.
- Slice: `data==data && len==len`. Two slices over equal contents at
  different addresses → not same; two views of the same region → same.
  Same `(data,len)` ⇒ same allocation ⇒ same backing, so the managed
  slice's bookkeeping words need no separate comparison.
- Pointer: the single pointer word. Redundant with `==` (which is already
  address-equality on pointers, claude-notes.md:898) — included anyway so
  `same` is the *uniform* "same underlying thing" operator across all
  reference/view types, with `==` remaining the idiomatic shorter pointer
  spelling.

## Two-argument rule

Both operands must have the **same static type**. No untyped operand; no
cross-type comparison.

- This structurally defeats Go's untyped-nil footgun: interface values
  have no nil (the empty value is a typed zero, tested with `present`), so
  `same(err, nil)` does not type-check — there is no untyped nil to pass,
  and no "bare operand that adapts to the other side".
- `same(rawSlice, managedSlice)` is rejected (different types); convert
  first (managed→raw is trivial — same leading two words).

## Excluded types — and why

- **Func values (`*func` / `@func`)** — NO. The same logical function has
  no canonical func-value: method values, monomorphized copies, and the
  cross-mode reps differ (a compiled `f := Foo` is `{vtable→shim,
  data→nil}`; a VM-side value is `{vtable→trampoline, data→VMClosureRec{…
  vm_func_idx …}}` — different two-word values for the same `Foo`).
  Guaranteeing function identity also *precludes optimizations*, which is
  why languages decline to define it. (Separate, larger open question: may
  one `Foo` exist as both a VM func and a native func, or must native
  funcs be injected so there is a single impl? Hard to enforce, worst for
  builtin-ish funcs; does not gate `same`.)
- **Scalars / structs / arrays** — NO. Value types: no referent. Scalars
  compare with `==`; structs/arrays use `==` once fieldwise/elementwise
  equality lands (currently "not yet implemented").

## Implementation (parallels `present`)

`same` is a new builtin keyword (cf. `present`):

1. **token + lexer**: `SAME` token, `"same"` keyword; audit that nothing
   in the tree uses `same` as an identifier first.
2. **parser**: `same(a, b)` — a 2-arg builtin call.
3. **checker** (`check_builtin.bn`): arity 2; operands the same static
   type; that type an eligible reference/view kind (pointer/managed-ptr,
   raw/managed interface value, raw/managed slice); reject func values,
   value types, and type mismatches with specific diagnostics; yield
   `bool`. Reuses the resolve-through-named/alias/readonly logic.
4. **IR lowering** (cf. `EmitIfacePresent`): word-wise extract + `OP_EQ` +
   `&&`, sized by the type (1 word ptr / 2 words iface / 2-word slice
   view). Built from existing primitives → every backend lowers it free,
   and it stays BUILDER-compilable.
5. **tests**: checker accept/reject per type and the same-type rule;
   runtime conformance cells (same vs distinct referent) for pointer,
   interface value, and slice.

BUILDER note: the new keyword does not burden the pinned BUILDER as long
as `cmd/bnc`'s own tree does not *use* `same`.

## Follow-ups (separate passes)

- `errors.Is(err, target) bool` — walk the mandatory `Unwrap()` chain,
  `same(cur, target)` per layer. (plan-std-errors.md deferred `errors.Is`
  pending exactly this interface-value identity test.)
- `io.IsEOF(err) bool` = `errors.Is(err, io.EOF)`.
- `present()` for all sensible types — the emptiness sibling; its own
  pass, discussed in detail before starting.
