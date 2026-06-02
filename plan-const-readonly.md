# Const / Var / Readonly — Language Cleanup Plan

Separate the two concepts currently sharing the `const` keyword into
their own forms:

- **`const`** — a compile-time constant.  No storage, no address.
  Each use is replaced by the value.  Scalar types only.
- **`var`** — a variable with storage.  Mutability of the *value at
  that storage* depends on whether the type carries the `readonly`
  modifier.
- **`readonly T`** — a type modifier marking "the value at this
  storage location can't be written through this access path."
  Replaces today's type-level `const T`.

Supersedes `plan-const-nonint.md` — the framing there assumed today's
single-`const` scheme and proposed extending IR-gen to lower more types
under that one keyword.  This plan restructures the surface instead;
the IR-gen story falls out cleanly from the new per-form split.

## The problem

Today's `const` covers two genuinely-different concepts:

1. **Compile-time constant** (immediate-replace at use sites): integer
   literals + iota groups + simple arithmetic, today.  No storage.
2. **Immutable variable** (storage that can't be written): the only
   way to express "an addressable, initialized-once value" today
   uses `const X T = ...` — which IR-gen then mis-handles because it
   assumes form (1) for everything.

Separately, the type system has a `const T` MODIFIER (`*const int`,
`*[]const char`, etc.) that says "the value at this storage is
read-only through this access path."  This is a third use of the
same keyword.

The conflation surfaced via the bug fixed in binate `7b0f77a3`:
extending `const`-as-decl-prefix to string-typed initializers worked
mechanically but blurred the line further — strings have rodata
storage (they aren't immediate), so the fix was extending `const`
in the wrong direction.  Restricting `const` to scalars + adding a
clean immutable-variable shape (`var X readonly T`) gets the surface
unambiguous.

## Design

### `const` — compile-time constants

Syntax:

    const NAME = LITERAL_EXPR              // untyped
    const NAME TYPE = LITERAL_EXPR         // typed
    const ( NAME [TYPE] = LITERAL_EXPR; ... )   // group with iota

Rules:

- **Scalar types only.**  Allowed: `int`, `uint`, the sized
  integer types (`int8`/`int16`/`...`/`uint8`/`...`), `char`,
  `bool`, `float32`, `float64`.  Disallowed: anything else —
  strings, slices, arrays, structs, pointers, managed pointers,
  function-value types, interface-value types.
- **Must have a value.**  Iota groups count: bare names in a group
  inherit the previous spec's expression with iota re-evaluated.
- **Initializer must be const-foldable.**  Today's `evalConstExpr`
  scope plus the bool / float extensions (Phase A of the original
  plan): literal, iota, reference to other const, arithmetic /
  bitwise / comparison ops on the same.
- **Visibility by location**: `.bni` → exported; `.bn` →
  package-private.  Not allowed in both files for the same name.
- **No extern form.**  There's no `const X int` (no value) — consts
  must always carry a value (group inheritance counts).  Consts
  resolve at every use site; there's no symbol to link.
- **No address.**  `&X` for a const X is a type-checker error.

### `var` — variables with storage

Syntax:

    var NAME TYPE                         // declaration (no init)
    var NAME TYPE = EXPR                  // declaration + initializer
    var NAME = EXPR                       // type inferred

Rules:

- **Storage location: always `.bn`.**  The variable's bytes live in
  the package's compilation unit.
- **`.bni`-side `var X T`** is an **extern declaration**: "this
  var exists in this package's `.bn` and is exported."  Must NOT
  carry an initializer.  Implies a matching `.bn` decl exists.
- **Default-init allowed.**  `var X T` (no `=`) is always legal at
  the language level — the storage is zero-initialized.
- **Mutability of the value** is governed by whether `T` carries a
  `readonly` modifier (see next section).  `var X int` — value at
  X's storage is writable.  `var X readonly int` — value at X's
  storage is read-only.
- **`&X` is legal.**  Result type respects `readonly`:
  `&X` for a `readonly T`-typed X yields `*readonly T`.
- **`.bni` and `.bn` decls must agree** on the full type including
  `readonly` modifiers.  Mismatch is a type-checker error.

### `readonly T` — type modifier

Replaces today's `const T` type modifier.  Pure rename; semantics
unchanged.  Left-to-right reading, each modifier applies to the
thing immediately to its right:

- `*readonly int` — pointer to readonly int (data can't change)
- `readonly *int` — pointer that itself is readonly; pointee is
  free (the access path doesn't permit reassigning the pointer)
- `readonly *readonly int` — both
- `*[]readonly *int` — slice of readonly-pointer-to-int

Equivalences in the new world:

| Old form (today) | New form |
|---|---|
| `const X int = 5` (with intent: compile-time const) | `const X int = 5` |
| `const X Point = Point{...}` (with intent: immutable global) | `var X readonly Point = Point{...}` |
| `const X *[]const char = "literal"` | `var X readonly *[]readonly char = "literal"` |
| `*const int` (type modifier) | `*readonly int` |
| `const *int` (type modifier) | `readonly *int` |

## Linter rule

A `readonly` global var with no initializer is almost always a bug
(it's zero-forever, can never be set, and nothing can ever write
to it).  Linter warns.  The rule does NOT apply to non-readonly
`var X T` without init — those are valid default-zero-initialized
mutable globals.

## Type-checker enforcement

- **`checkConstDecl`**: reject if the resolved type isn't scalar.
  Add a new conformance test pinning the rejection (e.g.,
  `const X *[]const char = "x"` → "const declarations require a
  scalar type; consider `var X readonly *[]readonly char = ...`").
- **`checkVarDecl`** in `.bni` context: reject if an initializer is
  present.
- **`checkVarDecl`** matching between `.bni` and `.bn`: require the
  full types to match including readonly modifiers.
- **`&X`** for X of kind SYM_CONST: error.

## IR-gen

The const/var split makes the lowering decisions unambiguous —
each form has one path.

- **`const` form** — all reads emit an immediate.  The
  `EmitConstInt` / `EmitConstBool` / `EmitConstChar` /
  `EmitConstFloat` lowering paths already exist; the producer side
  registers each scalar const's value in `moduleConsts` and read
  sites dispatch on `Kind` (Phase A of the original plan, scoped
  to scalars).
- **`var` form** — already lowers as a `moduleGlobals` entry with
  an initializer (today's behavior for `var X T = expr`).  Reads
  go through `lookupVar` + `EmitLoad`.  The initializer can be:
  - A literal expression (composite or scalar).
  - `readonly` modifier on the type → eligible for rodata placement.
  - Non-readonly → initialized data.

**Revert** the string-typed-const work that landed during the
investigation:

- `7b0f77a3` (binate) — `pkg/binate/ir` IR-gen extension for string
  consts.
- `a000855a` — the unit + conformance tests for that fix.
- `dd79b103` — the `pkg/binate/version` package itself, which will
  re-land in the new shape (`var Version readonly *[]readonly char
  = "..."` with the matching `.bni` extern decl).

The underlying machinery they used (`OP_CONST_STRING` +
`EmitStringToChars` + `OP_RODATA_SLICE` / `OP_RODATA_MSLICE`) stays;
it's reachable from the `var` paths that handle string-literal-
initialized globals.

## Type-modifier rename

Tree-wide mechanical change: every `const T` in type-expression
positions becomes `readonly T`.  Touches:

- `.bni` and `.bn` source files throughout the tree.
- `pkg/binate/parser` — keyword recognition.  Either accept both
  during a deprecation window, or hard-cut to `readonly` and
  expect everyone to update in lockstep.  Hard-cut is simpler;
  the tree is small.
- `pkg/binate/types` — the `TYP_CONST` internal type-kind can
  keep its name (it's an internal representation, not the surface
  syntax) or rename to `TYP_READONLY` for consistency.  Either is
  fine; pick one to avoid confusing readers.
- Spec doc (`claude-notes.md`).

## Source migration

Two production sites need migration to the new shape:

1. **`pkg/binate/version`** (binate `dd79b103`): rewrite Version as

       // version.bni
       var Version readonly *[]readonly char

       // version/version.bn
       var Version readonly *[]readonly char = "bnc-0.0.6-pre"

   The `Format()` impl already reads `Version` — under the
   new IR-gen, that read goes through the var-global path.

2. **`conformance/522_cross_pkg_const_string`** (binate
   `a000855a`): goes away when the bnc fix reverts.  The
   covering case becomes "exported readonly var of slice-of-
   readonly-char type" — write a new conformance test for that
   shape if the migration didn't otherwise cover it.

The `const T` type-modifier rename touches **every** `*const T` /
`*[]const T` / `@[]const T` / etc. in the tree.  Mechanical.

## Phasing — bisectable commit order

1. **Spec doc update** (`claude-notes.md`): describe `const`, `var`,
   `readonly T` as the new model.  Land first as documentation; no
   compiler change yet.
2. **Parser: add `readonly` keyword**.  Accept it as the
   type-modifier surface alongside today's `const` modifier.  No
   semantic change; cmd/bnc-source itself doesn't use `readonly`
   yet, so the current BUILDER still parses cmd/bnc-source fine.
3. **Cut bnc-0.0.6 + bump `BUILDER_VERSION`**.  Required between
   steps 2 and 3 of the source-side phasing — step 3 puts
   `readonly` into source files, but the current BUILDER's
   parser doesn't know the keyword (it predates step 2's parser
   change).  The release captures step 2's parser into a new
   BUILDER binary; the bump makes that BUILDER active for
   subsequent steps' compilation.  Follow `release-process.md`'s
   seven-step cut.
4. **Tree-wide sed**: rename every type-modifier `const` to
   `readonly` in source files.  Lands as one mechanical commit;
   verifies the new-BUILDER parser accepts `readonly` everywhere.
5. **Parser / type-checker: remove `const` type-modifier**.  After
   step 4, no source uses it; remove the parser branch and any
   leftover error messages.  Optionally cut another release here
   so BUILDER no longer accepts the legacy syntax — quality-of-life,
   not strictly required.
6. **Type checker: scalar-only `const` decl + .bni/.bn agreement +
   .bni-no-initializer rule + readonly-match enforcement**.  Add
   conformance tests pinning each rejection.
7. **Revert the string-typed-const IR-gen extension** (binate
   `7b0f77a3` + `a000855a`).  After step 6 the string-typed
   `const` declarations they served are now rejected at type-check
   time; the IR-gen path is dead.
8. **Migrate `pkg/binate/version`** to `var readonly` form.  After
   this the build is fully green under the new rules.
9. **Linter rule**: warn on uninitialized `readonly` global vars.
   Lands last so the rule doesn't fire spuriously during the
   migration.

Each step is independently testable and revertible.  Step 3 is the
only one that's hard-blocked on a release — every other step is
either pure source-only or tolerates the prior BUILDER.

## Touched docs / TODOs

- `claude-notes.md`: rewrite "Compile-time constants" / "Const on
  variable declarations" / "Const in types" sections to reflect
  the split.
- `plan-const-nonint.md`: supersede.  The "Phase A bool/float"
  story survives (still applies to the `const` form, scalar-
  extension only).  "Phase B composites" goes away — composites
  are now `var readonly`, and the IR-gen path for that is the
  existing var-with-literal-initializer machinery extended (see
  `plan-const-nonint.md` Phase B's "lower as initialized globals"
  for the lowering details, which still apply).  "Phase C
  pointer consts" likewise — pointer consts become `var readonly
  *T`, handled by the var path.
- `plan-version-info.md`: Phase 3 (const-in-.bni-vs-.bn semantics)
  is resolved by this plan.  Remove that section or shrink it to
  a pointer here.
- `claude-todo.md`: update the non-int-const entry to note the
  string-fix revert + this plan as the resolution.
- **CLI-flag-override proposal** (separately tracked): retarget
  from consts to vars per the design call.  Compiler flags affect
  a var's initial-data; the package containing the var's decl
  owns the flag-driven initializer.  Removes the separate-builds
  inconsistency concern (a var has one source of truth — its
  declaring package's compilation unit; importers don't see the
  init expression, only the symbol).

## Out of scope

- Generalized constant evaluator (function calls in const
  expressions, runtime-typed conversions, etc.).
- Optimization passes that would inline small `var readonly T`s
  as immediates (a downstream perf concern, not correctness).
- Refcount-lifetime design for `var X readonly @T = make(T)` —
  module-init alloc + lifetime-of-program retention.  Defer
  until a concrete use case appears.
