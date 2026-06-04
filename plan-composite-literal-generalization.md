# Plan: Generalize Composite Literals + Unify String Literals

Status: COMPLETE (shipped). Kept for design rationale — the const-ness-of-element
semantics and the "strings as sugar, not a special op" decision are the sole
written record. Per-phase parser/checker/IR/codegen/VM execution detail has been
removed as spent.

## Context

Composite-literal support used to be uneven: `Point{x,y}`, `pkg.T{...}`, and
`[N]T{1,2,3}` were fully supported, but `@[]T{1,2,3}` and `*[]T{1,2,3}` did not
parse, and a fixed-size char array could not be constructed from a string
literal (e.g. `[5]const char = "abcde"` — called out in the const plan, Stage 2c,
but never wired end-to-end).

Two problems:

1. Users can't write `@[]T{1, 2, 3}` or construct a fixed-size char
   array from a string literal like `[5]const char = "abcde"`. The
   second one is called out in the const plan (Stage 2c) but was
   never wired end-to-end.
2. String literals have their own bespoke IR op (`OP_STRING_TO_CHARS`
   / `OP_STRING_TO_ARRAY`) with special codegen. In a world where
   slice and array literals all go through a general mechanism,
   strings should be syntactic sugar for that mechanism, not a
   parallel pipeline.

## Target design

A composite literal's checker + IR-gen path is selected by the
shape of the target type. The const-ness of the element is
load-bearing for the raw-slice case:

| target type          | all elements const? | result                                                                                 |
|----------------------|---------------------|----------------------------------------------------------------------------------------|
| struct `T{...}`      | n/a                 | allocate struct on stack, store fields (as today)                                      |
| `[N]T{...}`          | n/a                 | allocate array on stack, store elements (as today)                                     |
| `@[]T{...}`          | yes or mixed        | call `MakeManagedSlice(sizeof(T), N)`, store elements, return managed-slice            |
| `@[]const T{...}`    | all-const           | MAY alias a static `%BnManagedSlice` global (rodata); zero allocation                  |
| `@[]const T{...}`    | mixed               | `MakeManagedSlice + stores`, sealed as const through the returned handle               |
| `*[]const T{...}`    | all-const           | zero-copy borrow of rodata — static `%BnSlice` global                                  |
| `*[]const T{...}`    | mixed               | stack-allocated `[N]T` backing + `%BnSlice` view; lifetime = enclosing scope           |
| `*[]T{...}`          | any                 | **REJECTED** — a mutable raw slice of what? rodata is read-only; stack is scope-bound  |
| `"abcde"` (no explicit target) | —       | natural type `[N]const char`; default `@[]const char` when it has to decay             |
| `"abcde"` → `[N]char` / `[N]const char`      | — | decay to array literal `[N]{'a', 'b', 'c', 'd', 'e'}`                                  |
| `"abcde"` → `@[]char`                        | — | implicit alloc+copy (Stage 2b, already landed)                                         |
| `"abcde"` → `*[]const char` / `@[]const char`| — | zero-copy alias (as today)                                                             |

The "all elements const" check is syntactic: every `e.Elems[i].Value`
must be a compile-time-constant expression (int literal, bool literal,
string literal, composite literal of all-const elements, etc.). If
any element requires runtime evaluation, the literal is "mixed" and
takes the non-rodata lowering path.

Why const changes the raw-slice story:
- `*[]T{...}` would need a mutable backing. Rodata is read-only and
  stack is scope-bound — neither is safe to hand out as a general
  raw slice. Rejected.
- `*[]const T{...}` is a read-only view. Rodata is fine (safe to
  alias), and stack is fine *as long as* the slice doesn't outlive
  the enclosing scope. The checker relies on raw-slice-lifetime
  rules (already in the language — raw slices borrow, you can't
  return them from functions) to keep this honest.

The unification: when the target is `[N]char` / `[N]const char`, the
checker treats the string literal *as if* the user wrote
`[N]char{'a', 'b', ...}`, and the same machinery handles it. Once
that works end-to-end, strings stop being special at the IR level.

## Phases (shipped)

- **Phase 1 — string in array slots (full Stage 2c).** `var s [N]const char =
  "..."` and `var s [N]char = "..."` work. String literals now have the natural
  type `[N]const char` (computed from the unescaped literal length at check
  time), rather than `TYP_STRING`. `TYP_STRING` stays in the type system (for
  `var s string` and the universe-scope `string` alias), but literals no longer
  use it.
- **Phase 2 — managed-slice / raw-slice composite literals.** `@[]T{...}`,
  `@[]const T{...}`, and `*[]const T{...}` work; `*[]T{...}` is rejected per the
  design table.
- **Phase 3 — unify strings as sugar.** String literals are treated uniformly
  through the general composite-literal machinery rather than via dedicated
  ops (see the Phase 3 design note below).

### Phase 3 design note: peephole, not a special op

The rodata static-global constant is retained for every string literal, but the
IR ops are the general `OP_MAKE_SLICE + OP_STORE` (or array-lit) sequence.
Codegen **recognizes the pattern** — consecutive stores of compile-time bytes
into a fresh `MakeManagedSlice` — and replaces it with the rodata-alias form
when legal. That's a peephole, not a special op.

The rodata-optimization peephole is gated by:
- All elements are compile-time constants (byte literals from the
  original string).
- Target type is `@[]const char` or `*[]const char` (mutability
  would otherwise require an owned copy).

Benefit: one less special case across four layers (`pkg/codegen`, `pkg/vm`,
`pkg/ir`); cleaner spec. **Non-goal: performance.** The peephole preserves the
rodata-alias where it existed before, so perf doesn't regress.

## Open questions

- Keep `string` as a distinct named type, or make it an alias for
  `@[]const char`? The plan leans toward keeping it (so users can
  write `var s string` and mean the default shape), but consider
  whether it has any semantic meaning today beyond "@[]const char
  with a fancy name."
- `[...]T{1, 2, 3}` (length-inferred array) — already parsed (`[...]`
  marker in the AST). Not covered above. Check whether the checker /
  IR gen already handle it.
- How does `print("hello")` work with the new natural type? `print`
  is variadic and today sees `TypString`; post-phase-1 it would see
  `[N]const char`. Need to make sure the print-char-slice detection
  (`isCharSliceType`) still fires. (It should — arrays of char aren't
  char-slices, but the print path likely handles strings separately.
  Check `pkg/ir/gen_expr.bn` print logic.)
