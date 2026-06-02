# Top-Level Consts of Non-Integer Types — Plan

Generalize the partial fix in binate `7b0f77a3` (string-typed top-level
consts work via `OP_CONST_STRING` + `OP_RODATA_SLICE` lowering) to cover
every type bnc currently accepts as a const initializer.  The current
silent-zero-placeholder behavior across all non-int cases is the
broader form of the bug fixed for strings.

See `claude-todo.md` for the partial-fix context.

## Scope

What's broken today (post-string-fix):

| Type | Failure mode |
|---|---|
| `bool` | Loud — read emits i64, branch site expects i1 |
| `float32` / `float64` | Silent — reads as 0.0 |
| `[N]T` (array literal const) | Loud — `arr[i]` extractvalue on i64 |
| `T struct` (struct literal const) | Silent — all-zero struct |
| `*[]const T` / `@[]const T` (composite-literal slice) | Loud — len/index extractvalue on i64 |
| `*T` / `@T` (pointer-to-value consts) | Not probed; three sub-cases (see below) |

All share the same root cause: `genConst` (gen_const.bn) + the importer
registration in `gen_import.bn::registerImportFile` go through
`evalConstExpr`, which is integer-only.  Non-int initializers are
silently dropped; read sites (EXPR_IDENT, qualified EXPR_SELECTOR)
fall through to the int-zero placeholder.

## Phase A — Scalar non-int (bool, float)

Mechanically the same shape as the string fix.

**ModuleConst surface** extends from the current
`{Name, Val, StrVal, IsStr, Typ}` to also carry boolean + float values:

    type ModuleConst struct {
        Name    @[]char
        Val     int          // int / iota
        StrVal  @[]char       // string literal
        BoolVal bool          // bool literal
        FltText @[]char       // float literal (raw text — same shape as OP_CONST_FLOAT)
        Kind    int           // enum: CONST_INT / CONST_STR / CONST_BOOL / CONST_FLT
        Typ     @types.Type
    }

(`Kind` replaces the `IsStr` flag — cleaner once there are >2 cases.
Migration: existing `IsStr` callers test `Kind == CONST_STR` instead.)

**Producer changes** in `pkg/binate/ir/gen_const.bn::genConst` and
`pkg/binate/ir/gen_import.bn::registerImportFile` recognize:

- `EXPR_BOOL_LIT` → `Kind = CONST_BOOL`, populate `BoolVal`
- `EXPR_FLOAT_LIT` → `Kind = CONST_FLT`, populate `FltText` with the
  raw literal text (matching how `EmitConstFloat` consumes it — see
  `pkg/binate/ir/ir.bn:208`)

**Read-site dispatch** in `gen_expr.bn::EXPR_IDENT` and
`gen_selector.bn::EXPR_SELECTOR` qualified case:

    switch moduleConsts[i].Kind {
    case CONST_STR: return b.EmitConstString(...) + EmitStringToChars
    case CONST_BOOL: return b.EmitConstBool(moduleConsts[i].BoolVal)
    case CONST_FLT: return b.EmitConstFloat(moduleConsts[i].FltText, moduleConsts[i].Typ)
    default: return b.EmitConstInt(moduleConsts[i].Val, ctyp)
    }

**Tests**: unit-test the producer + dispatch in
`pkg/binate/ir/gen_const_test.bn`.  Conformance tests covering
in-package + cross-package reads for each scalar non-int type.

Estimated diff size: comparable to the string fix (~80 lines).

## Phase B — Composite types: design call

Three options for arrays, structs, slices, managed-slices, and the
pointer cases below.  Pick one before implementing.

### Option (b1) — re-emit the initializer AST at each read site

Store the const's value expression (`@ast.Expr`) in ModuleConst.
At each read, call the existing IR-gen path for that expression as
if it were inlined at the read site.

Pro: minimal IR-gen rework — leverages the existing composite-literal
machinery (`genCompositeLit`, slice / struct / array literal handlers).

Con: duplicates the literal-emission code at every read.  For a
const-typed slice referenced from N call sites, N independent
heap-or-rodata copies / allocations land in the binary.  Cross-package
const reads via the EXPR_SELECTOR path re-emit too, multiplying the
duplication across packages.

Probably an OK first cut but obviously not the long-term answer.

### Option (b2) — demote composite-typed top-level consts to global vars in IR-gen

Type-checker still enforces const-ness (no writes allowed at source
level).  But IR-gen routes such consts through `moduleGlobals`
instead of `moduleConsts`, with the literal as initializer.  Each
read goes through `lookupVar` → `EmitLoad` (or `EmitGetFieldPtr` for
struct-field selectors etc.), same as any global.

Pro: single allocation / rodata copy per const, regardless of read
count.  Cross-package reads load through the global's mangled symbol.
Matches how Go handles "var" but applies const-checking semantics.

Con: deviates from the "const is compile-time-constant" mental model
— composite consts effectively become initialized-once globals.
Comparison-with-zero / dead-code-elimination passes can't const-fold
through them at LLVM time (loading from rodata isn't as transparent
as an immediate).

### Option (b3) — restrict composite types in the type-checker (recommended)

Reject top-level const declarations whose declared type is composite
(struct, array, slice, managed-slice).  Matches Go's `const` rule:
consts are compile-time-constant values of basic types only.  The
broader-acceptance was a type-checker oversight, not an intentional
language feature — bnc never had IR-gen for these and the front end
mistakenly let them through.

Pro: simpler language semantics, matches a well-known precedent,
keeps the IR-gen const machinery focused on truly-constant scalars.
The use cases composite consts would address (named static tables,
configuration) are already served by top-level `var` with an
initializer — except for compile-time constness, which can be
emulated by package convention (Go does this too).

Con: breaks any existing in-tree consts that currently silently
mis-compile but happen to "work" because nothing reads the zeros.
Per the probe, no current in-tree composite-typed consts exist —
this restriction would only forbid hypothetical future ones.

**Recommendation**: (b3).  Cleanest end state; matches Go; the
type-checker change is small (extend the assignability check in
`checkConstDecl` to also require the declared type be a non-composite).
A new conformance test pinning the rejection completes coverage.

## Phase C — Pointer-typed consts

Three sub-cases that don't reduce to "composite type, reject" or
"scalar, emit primitive":

### C1: const-pointer to a static global

    var G int = 42
    const P *int = &G

`P` is a compile-time-constant POINTER VALUE: the address of `G`'s
storage.  Sensible only if the type checker can verify `&G` is in
fact a static address (a top-level var, not a function-local).

If allowed, IR-gen emits `P` as a global initialized with the
mangled-symbol address of `G`.  Reads `EmitLoad(P, *int)` and follow
through normally.

### C2: const-pointer to a literal address (immediate)

    const NULL *int = nil

Already-known shape (nil is a const).  Should "just work" via the
existing nil-init path; verify it does, add a regression test.

### C3: const-pointer to a string-literal-style rodata blob

    const HelloPtr *const uint8 = "hello"  // hypothetical?

Probably not actually supported by the type system; `"hello"` has
natural type `[N]const char` which decays to `*[]const char`, not
`*const uint8`.  If the type checker accepts it, treat it like the
string-fixed path.

### Recommendation for pointer consts

Allow C1 (const-pointer to top-level var) and C2 (nil) explicitly;
reject everything else.  C1 needs a type-checker hook that walks
the initializer to confirm `&G` is a static address.  C2 is
mechanical.

## Suggested implementation order

1. **Phase A** (bool, float): land first.  Mechanical, ~80 lines,
   matches the string fix shape.  Closes most of the "loud" cases.
2. **Phase B** (composite restriction — option b3): commit the
   type-checker change + conformance rejection test.  Trivial diff;
   resolves the "silent-zero struct" + "loud-extractvalue array /
   slice" failure modes at the front-end.
3. **Phase C** (pointer consts): smaller scope, can land anytime
   after A; C2 (nil) probably already works and just needs a
   regression test.

Phase A is concrete enough to start; Phase B's type-checker move
deserves a quick sanity-check on whether anything in-tree already
declares a composite-typed const (today's probe says no).  Phase C
can be deferred until a real use case surfaces.

## Out of scope

- Generalized constant-evaluator for arbitrary const expressions
  (e.g., `const X = f(42)` where `f` is a pure function).  Go doesn't
  do this either; the simpler model is "consts are literals plus
  the trivial arithmetic combinations already in `evalConstExpr`."
- Const-folding through function calls, type conversions involving
  runtime work, etc.  Out of scope.
