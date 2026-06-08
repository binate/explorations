# Plan: length-0 ⟹ no backing (slice invariant)

Status: SPEC DECIDED 2026-06-08 (claude-notes.md "Nil slices" / "Length-0
⟹ no backing"; reverses the 2026-04-03 empty-view-vs-no-backing idea).
Enforcement IN PROGRESS. Motivated by, and a prerequisite for, extending
`present` to slices (`present(s) ⇔ len(s) > 0`) — see plan-same-builtin.md.

## Invariant

A length-0 slice ALWAYS has no backing: its representation is the
nil-equivalent `{null, 0}` (raw `*[]T`) / `{null, 0, null, 0}` (managed
`@[]T`). Empty and "nil" slices are indistinguishable. The
never-implemented `rt.HasBacking` is dropped.

Rationale: a length-0-with-backing slice can never be read, re-sliced
(subslicing is bounds-checked to `len`, gen_access.bn), or appended to
(no `append`), so its backing is permanently unreachable — yet a managed
one silently pins the allocation alive, which is observable via a raw
alias and a footgun. Eliminating the state removes the last nil-vs-empty
difference.

## Violators (where the invariant is broken today) — from recon + plan review

`make_slice(T, 0)` is ALREADY compliant (`rt.MakeManagedSlice` returns
`{nil,0,nil,0}`; asserted by conformance/093 + rt_test
`TestMakeManagedSliceZero`), and everything routing through it (VM
`BC_MAKE_SLICE`, native `emitMakeSlice`, managed composite literals)
inherits that. The violators are:

1. **Subslicing `s[lo:hi]` with `lo == hi`** — `EmitSliceExpr`
   (pkg/binate/ir/ir_ops.bn ~113) keeps `{data+lo, 0, refptr, backingLen}`
   and `gen_access.bn` (~371, `emitManagedSliceRefInc`) RefIncs it.
2. **Empty raw composite literal `*[]T{}`** — `genRawSliceLit`
   (gen_composite.bn ~181) builds a `[0]T`-backed header → `{ptr, 0}`.
   (Does NOT route through `make_slice`.)
3. **Empty string literals `""`** — `*[]readonly char = ""` and
   `@[]readonly char = ""` take the rodata path and emit a pointer to an
   empty rodata global → `{non-null, 0}`, on the x64, aarch64, AND LLVM
   backends (codegen/emit.bn `emitStringGlobal`; the native
   `*_rodata.bn`). The VM is already compliant. (conformance/088 exercises
   this; it passes only because it checks `len`, not the data word.)

## Fix

- **Subslice (`EmitSliceExpr`)** — when `newLen == 0`, yield
  `{null, 0[, null, 0]}`. Done **branchlessly** (mask data/refptr/
  backingLen by `newLen != 0` via `bit_cast`+AND, at pointer width):
  `EmitSliceExpr` is a `@Block` method without `GenContext`, so a branch
  would force block-splitting (the `ctx.CurBlock`-desync hazard). The
  existing RefInc then hits a null refptr → no-op (`OP_REFINC` nil-check
  confirmed inlined in all three backends); dtor sees null refptr / 0
  backingLen → no-op too. (Plan-review confirmed this approach.)
- **Empty literals + empty string/rodata** — short-circuit at the **IR
  level**: when the element list / byte sequence is empty, emit the
  canonical nil-equivalent (`OP_CONST_NIL` of the slice type) instead of a
  `[0]T`-backed header / rodata alias. One IR-gen change
  (`genRawSliceLit`, `genManagedSliceLit`, `EmitStringToChars`) fixes all
  four backends at once — no per-backend duplication.

## Tests

- Length-0 subslice `s[i:i]` / `s[len:]` → `{null,0,null,0}` AND the
  backing refcount is NOT bumped.
- Empty string literal `""` → **null** data word (not just `len 0`).
- A destructible-element subslice-to-empty (`@[]@Item` → `s[i:i]`) that
  falls out of scope AND a return variant — no refcount-fault at the
  managed dtor's null-backing path.
- `same(empty1, empty2) == true` (all empties unified).

## Follow-up

DONE (binate `29c9dc47`, conformance `667`): `present` extended to slices
(`present(s) ⇔ len(s) > 0`), func values, and pointers — see
plan-same-builtin.md.
