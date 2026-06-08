# Plan: `same` builtin — reference identity

Status: IMPLEMENTED + LANDED — the whole `same → errors.Is → io.IsEOF`
chain is on main: `same` (binate `e7c1b7fc`, conformance `661_same_ref`),
`errors.Is` (binate `1f87b905`, `662_errors_is`), `io.IsEOF` (binate
`5282563b`, `663_io_iseof`) — all green in builder-comp / -int / -comp.
Remaining: `present()`-for-all-sensible-types is a separate, later pass
(to be discussed in detail first).

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
  slice's bookkeeping words need no separate comparison. Under the
  length-0 ⟹ no-backing invariant (claude-notes.md, 2026-06-08), every
  empty slice is the canonical `{null, 0}`, so all empty slices are
  `same` by construction — they denote the same underlying thing (no
  backing).
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

## Follow-ups

- DONE — `errors.Is(err, target) bool` (binate `1f87b905`): walks the
  mandatory `Unwrap()` chain, `same(cur, target)` per layer. (This is the
  `errors.Is` plan-std-errors.md deferred pending interface-value
  identity.)
- DONE — `io.IsEOF(err) bool` = `errors.Is(err, io.EOF)` (binate
  `5282563b`).
- DONE — `present()` for all sensible types (binate `29c9dc47`,
  conformance `667`): interface values (already), function values
  (vtable set — no `== nil` for them), pointers (non-null), slices
  (`len > 0`). Value types rejected. Prerequisite was the length-0 ⟹
  no-backing invariant (plan-len0-no-backing.md), so empty ⟺ `len 0`.
