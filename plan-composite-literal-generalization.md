# Plan: Generalize Composite Literals + Unify String Literals

## Context

Today composite-literal support is uneven:

| form              | parse | check | ir gen | codegen |
|-------------------|-------|-------|--------|---------|
| `Point{x, y}`     | ✓     | ✓     | ✓      | ✓       |
| `pkg.T{...}`      | ✓     | ✓     | ✓      | ✓       |
| `[N]T{1,2,3}`     | ✓     | ✓     | ✓      | ✓       |
| `@[]T{1,2,3}`     | ✗     | —     | —      | —       |
| `*[]T{1,2,3}`     | ✗     | —     | —      | —       |
| `"abcde"` → `*[]const char` / `@[]char` / ... | ✓ (special) | ✓ via TYP_STRING | ✓ OP_STRING_TO_CHARS | ✓ (special) |
| `"abcde"` → `[N]char` / `[N]const char` | ✓ | ✗ | ✗ | ✗ (OP_STRING_TO_ARRAY declared, never emitted) |

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

## Phase 1 — full Stage 2c (string in array slots)

**Goal**: `var s [N]const char = "abc..."` works. `var s [N]char =
"..."` also works. This is the minimum for using string data in
value-type slots (struct fields, stack buffers).

### 1.1 Type checker

In `AssignableTo`, add a rule: `string → [N]char` / `[N]const char` is
allowed when `N == len(literal)`. Since the string literal's AST node
carries the source text, the checker can compute the length at
check-time and compare.

Unknown: currently `AssignableTo` takes types, not expressions. The
length isn't in `TypString`. Two options:

- (a) Compute the length at the call site (in `checkAssignStmt` and
  `checkVarDecl`), inspecting the RHS AST node and passing the
  length separately.
- (b) Give string literals a more specific type — `[N]const char` —
  the moment they're checked, as the plan's "natural type" suggests.

(a) is less disruptive; (b) is the correct long-term shape. Start
with (b), it's a pre-requisite for Phase 2.

### 1.2 `checkExprInner` for `EXPR_STRING_LIT`

Currently:
```binate
if e.Kind == ast.EXPR_STRING_LIT {
    return TypString()
}
```

Change to:
```binate
if e.Kind == ast.EXPR_STRING_LIT {
    // Natural type: [N]const char where N = unescaped literal length.
    var n int = unescapedStringLen(e.StrVal)
    return MakeArrayType(MakeConstType(TypChar()), n)
}
```

`TYP_STRING` stays in the type system (for `var s string` declarations
and the `string` universe-scope alias), but literals no longer use
it. Instead, everywhere a literal flows:

- To a `string`-typed slot: `[N]const char → string` via explicit alias.
  Since we kept `string` as a type, need one new rule: `[N]const char`
  is assignable to `string`. (Or: make `string` a type alias for
  `@[]const char` and eliminate `TYP_STRING`. Cleanest but bigger.)
- To a `*[]const char` / `@[]const char`: decay to slice. The existing
  array-to-slice decay machinery should handle it, or we add a
  `[N]const char → *[]const char` rule.
- To `@[]char`: the Stage 2b implicit-copy rule fires, keyed on the
  source being an array with const-char elements now instead of
  `TYP_STRING`.
- To `[N]char` / `[N]const char`: new rule — element-wise decay (zero
  cost at codegen; same bytes).

### 1.3 IR gen

Wire up the array path for string literals. Today `OP_STRING_TO_ARRAY`
is declared but never emitted:

- In `gen_stmt.bn` / `gen_control.bn` / `gen_selector.bn`, wherever
  `EmitStringToChars` fires today, also check: if the target is
  `TYP_ARRAY` with char element, emit `EmitStringToArray` instead.

### 1.4 Codegen

Implement `OP_STRING_TO_ARRAY`: load the `[N x i8]` rodata constant
into the target alloca via memcpy (or `store` if small, TBD). The op
already exists so adding the emit is a small patch.

### 1.5 VM

Mirror the codegen path in `pkg/vm/lower_instr.bn`. Probably maps to
`BC_LOAD_STR` + a memcpy opcode. (Check whether BC_LOAD_STR currently
handles the array case at all.)

### 1.6 Tests

Conformance:
- `var s [5]const char = "abcde"` — positive, read back each byte.
- `var s [5]char = "hello"`, mutate `s[0]`, read back — exercises the
  mutable-array branch.
- Struct field `type Name struct { name [16]char; len int }`,
  initialize `Name{..."bob", 3}` — real-world shape.
- Negative: `var s [3]char = "abcde"` — length mismatch, type error.

Unit:
- Checker: `AssignableTo([N]const char, [N]char)` element-wise decay,
  length mismatch rejected, non-string RHS unaffected.
- IR: `OP_STRING_TO_ARRAY` is emitted at array-init sites.

## Phase 2 — managed-slice composite literals

**Goal**: `@[]int{1, 2, 3}` works. `@[]const int{1, 2, 3}` also works
(per-encounter allocation, sealed as const through the returned
handle; cf. `plan-const-type-modifier.md:138-169`).

### 2.1 Parser

Extend the expression grammar to parse `@[]T{...}` and `[]T{...}` as
composite literals. Today only identifiers, qualified names, and
`[N]T{...}` arrays have this path — `@` and `*` are prefixes for
other expressions, so we need:

- When `parsePrimaryExpr` sees `@`, peek ahead for `[]`; if it sees
  `@[]T`, allow the followup `{` to trigger `parseCompositeLitBody`.
  Same for `*[]T`.
- Reuse `parseType` to get the element type, then check for `{`.

Watch out: `@[]T{...}` in some expression contexts could ambiguate
with `@Foo{...}` (managed pointer to struct literal, if that syntax
ever lands). For now `@[]` is unambiguous because `[` can only mean
slice-sugar.

### 2.2 Type checker

Add a branch to `checkCompositeLit` for `TEXPR_MANAGED_SLICE`:

- Each element must be assignable to the element type.
- Result type is `@[]T` as declared.

For `TEXPR_SLICE` (raw slice):

- Element must be const (`*[]const T{...}` allowed, `*[]T{...}`
  rejected per the design table).
- Element-assignability checks as usual.
- Result type is `*[]const T`.

### 2.3 IR gen

New `genManagedSliceLit`:
- All-const elements + target is `@[]const T`: emit a static
  `%BnManagedSlice` global (same shape as today's string-literal
  rodata) and load it. Zero runtime allocation.
- Otherwise: `OP_MAKE_SLICE(T, N)` to allocate the backing, then
  `OP_GET_ELEM_PTR + OP_STORE` per element. Result is fresh,
  refcount=1.

New `genRawSliceLit` (const-element only):
- All-const elements: emit a static `[N x T]` rodata global and a
  paired `%BnSlice` header; load the slice.
- Mixed: stack-allocate a `[N]T` buffer (same as `[N]T{...}` today),
  then emit a `%BnSlice` view into it. Codegen already knows how to
  build a `%BnSlice` from `{data, len}`.

### 2.4 Codegen

Nothing new for the `MakeManagedSlice + stores` and stack-backing
cases — already-implemented ops handle the lowering. The static-
rodata-global path for all-const literals reuses the infrastructure
that already emits `@.str.N.ms` for string literals; generalize the
global-emitter to take any element type, not just i8.

### 2.5 VM

Same — reuses existing `BC_MAKE_SLICE` + store paths.

### 2.6 Tests

Conformance:
- `@[]int{1, 2, 3}` — positive; `println(s[0])` through `s[2]`,
  `println(len(s))`.
- `@[]int{1, 2, y}` with `y := 42` — mixed compile-time + runtime
  values.
- `@[]const int{1, 2, 3}` — all-const; sealed; writing `s[0] = x`
  must be rejected (already covered by Stage 1 rules, just
  re-exercised).
- `*[]const int{1, 2, 3}` — all-const raw slice; bytes live in
  rodata.
- `*[]const int{1, 2, y}` — mixed raw slice; backing is stack, slice
  valid for the enclosing scope. Attempting to return the slice or
  escape it should be rejected by the existing raw-slice-lifetime
  rules.
- Negative: `*[]int{1, 2, 3}` — rejected with "raw-slice literal
  requires const elements; use `*[]const int{...}`, `@[]int{...}`,
  or `[N]int{...}[:]`".
- Negative: type mismatch on an element (`@[]int{1, "x"}`).

Unit:
- Checker: accept `@[]T{...}`, `@[]const T{...}`, `*[]const T{...}`;
  reject `*[]T{...}` with the error above.
- IR: `@[]T{...}` lowers to `MakeManagedSlice + N stores`; all-const
  `@[]const T{...}` lowers to a static-global load;
  `*[]const T{...}` lowers to rodata-global-alias (all-const) or
  stack-backing (mixed).

## Phase 3 — unify strings as sugar

**Goal**: delete `OP_STRING_TO_CHARS` and `OP_STRING_TO_ARRAY` as
special ops. String literals become a lexer/parser-level sugar for
`[N]const char{'a', 'b', ...}`.

### Approach

Once Phase 1 sets EXPR_STRING_LIT's natural type to `[N]const char`,
the checker / IR-gen can treat it uniformly:

- `var s [N]char = "..."` — already uses `EmitStringToArray`; swap to
  a normal array-literal lowering (N `EmitConstInt + EmitStore`
  sequence). But for long literals the emitted IR would be huge; keep
  a memcpy-from-rodata codegen optimization.
- `var s @[]char = "..."` — already implicit copy (Stage 2b); change
  it to the same path as `@[]char{'a', 'b', ...}` — a
  `MakeManagedSlice` + stores. Rodata-memcpy optimization preserved
  as a special case for all-compile-time-const char elements.
- `var s @[]const char = "..."` — today aliases static global. Keep
  this as an optimization of `@[]const char{'a', 'b', ...}` where all
  elements are compile-time constants.
- `var s *[]const char = "..."` — today extracts 2 words from static
  global. Keep as optimization.

Concretely: retain the rodata static-global constant for every string
literal (the `@.str.N` allocation), but have the IR ops be the general
`OP_MAKE_SLICE + OP_STORE` sequence. Codegen can **recognize the
pattern** (consecutive stores of compile-time bytes into a fresh
`MakeManagedSlice`) and replace it with the rodata-alias form when
legal (const-element case only). That's a peephole, not a special op.

### Scope / risk

- Deleting the special ops is disruptive: `pkg/codegen`, `pkg/vm`, and
  `pkg/ir` all have the ops wired.
- The rodata-optimization peephole is new and has to be gated by:
  - All elements are compile-time constants (byte literals from the
    original string).
  - Target type is `@[]const char` or `*[]const char` (mutability
    would otherwise require an owned copy).
- Benefit: one less special case across four layers. Cleaner spec.
- Non-goal: performance. The peephole should preserve the rodata-
  alias where it exists today, so perf doesn't regress.

### Staging

This is the biggest change. Defer until Phase 1 + 2 land and we have
confidence in the general machinery. At that point:

- 3.1 — introduce the peephole (rodata-alias for const-char literals)
  as a pure optimization, alongside the existing ops. No behavior
  change.
- 3.2 — switch IR gen for string literals over to the general path
  (emit `OP_MAKE_SLICE + stores` or `array-lit` instead of
  `OP_STRING_TO_CHARS`/`_ARRAY`). Existing tests must still pass.
- 3.3 — delete `OP_STRING_TO_CHARS`, `OP_STRING_TO_ARRAY`, and their
  codegen / VM paths. Remove `EmitStringToChars` / `EmitStringToArray`.

## Scope summary

| phase | effort (sessions) | prerequisite | user-visible change |
|-------|-------------------|--------------|---------------------|
| 1     | ~1                | none         | `[N]char = "..."` works |
| 2     | ~1-2              | none (can be done in parallel with 1) | `@[]T{...}` works |
| 3     | ~2-3              | 1 + 2 done   | none (internal cleanup) |

Recommend landing 1 + 2 first, then reviewing whether 3 is worth the
churn.

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
