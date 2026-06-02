# Top-Level Consts of Non-Integer Types — Plan

Generalize the partial fix in binate `7b0f77a3` (string-typed top-level
consts now lower correctly via `OP_CONST_STRING` + `OP_RODATA_SLICE`)
to cover every type Binate accepts as a `const` declaration.  The
current silent-zero-placeholder behavior for non-int cases is the
broader form of the bug fixed for strings.

See `claude-todo.md` for the partial-fix context.

## Binate's `const` semantics

`const` in Binate marks an **immutable variable** — i.e., the variable
itself can't be reassigned (see `claude-notes.md`, "Compile-time
constants" / "Const on variable declarations").  It does not require
the initializer to be reducible to an immediate at every read site.
So:

- `const X int = 5` — `X` can't be reassigned.  Reads may be const-
  folded to the immediate `5` as an optimization.
- `const X Point = Point{1, 2}` — `X` is a struct whose value is
  fixed at module-initialization time and can't be reassigned.
- `const P *int = &G` — `P` holds the address of `G` and can't be
  reassigned to point elsewhere.  Whether `*P` is mutable depends
  on the const-ness of the pointee per the "const-in-types" rules.

The IR-gen bug today is that bnc's const-handling assumes
"reducible-to-immediate" — only int (and now string) actually
lowers correctly; every other type silently falls through to
`EmitConstInt(0, TypInt())`.

## Scope

What's broken today (post-string-fix):

| Type | Failure mode |
|---|---|
| `bool` | Loud — read emits i64, branch site expects i1 |
| `float32` / `float64` | Silent — reads as 0.0 |
| `[N]T` (array literal const) | Loud — `arr[i]` extractvalue on i64 |
| `T struct` (struct literal const) | Silent — all-zero struct |
| `*[]const T` / `@[]const T` (composite-literal slice) | Loud — len/index extractvalue on i64 |
| `*T` / `@T` (pointer-to-value consts) | Not probed; sub-cases below |

All share the same root cause: `genConst` (gen_const.bn) + the
importer-side registration in `gen_import.bn::registerImportFile`
go through `evalConstExpr`, which only knows how to fold integers.
Non-int initializers are silently dropped at registration time;
read sites (`EXPR_IDENT` in gen_expr.bn, qualified `EXPR_SELECTOR`
in gen_selector.bn) find nothing in `moduleConsts` and emit
`EmitConstInt(0, TypInt())` as a fallback.

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

**Producer changes** in `gen_const.bn::genConst` and
`gen_import.bn::registerImportFile` recognize:

- `EXPR_BOOL_LIT` → `Kind = CONST_BOOL`, populate `BoolVal`
- `EXPR_FLOAT_LIT` → `Kind = CONST_FLT`, populate `FltText` with the
  raw literal text (matching how `EmitConstFloat` consumes it — see
  `pkg/binate/ir/ir.bn:208`)

**Read-site dispatch** in `gen_expr.bn::EXPR_IDENT` and
`gen_selector.bn::EXPR_SELECTOR` qualified case:

    switch moduleConsts[i].Kind {
    case CONST_STR:  return b.EmitConstString(...) + EmitStringToChars
    case CONST_BOOL: return b.EmitConstBool(moduleConsts[i].BoolVal)
    case CONST_FLT:  return b.EmitConstFloat(moduleConsts[i].FltText, moduleConsts[i].Typ)
    default:         return b.EmitConstInt(moduleConsts[i].Val, ctyp)
    }

**Tests**: unit-test the producer + dispatch in
`pkg/binate/ir/gen_const_test.bn`.  Conformance tests covering
in-package + cross-package reads for each scalar non-int type.

Estimated diff size: comparable to the string fix (~80 lines).

## Phase B — Composite-typed consts: lower as initialized globals

Per the const-semantics summary above, `const X T = compositeLit`
declares an immutable variable whose value is whatever the literal
expression yields at module-init time.  The natural lowering is the
same shape that already handles `var X T = compositeLit`:
**initialized-once globals**, with const-ness enforced at the
language layer by the type checker.

### IR-gen route

Producers (`genConst`, `registerImportFile`) recognize composite-
literal init shapes and route the decl through `moduleGlobals`
instead of `moduleConsts`.  The literal is the global's initializer;
the global's mangled symbol exposes it cross-package.

Read sites:
- In-package: `EXPR_IDENT` already falls back to `lookupVar` first,
  which checks `moduleGlobals` — an entry there resolves naturally.
- Cross-package qualified `EXPR_SELECTOR`: same fallback path —
  global symbol resolution via the existing imported-pkg-globals
  machinery (verify whether tier-2 cross-pkg-var-read paths in
  `gen_import.bn` already cover this; add if not).

For the lowering of immutable composite data, the existing
`OP_RODATA_SLICE` / `OP_RODATA_MSLICE` pattern (currently emitted
for string literals through `EmitStringToChars`) generalizes:
emit the initializer as a static rodata blob, load / view at each
read.  Where the pointee type isn't const (`const A @[]int` —
mutable elements via a fixed managed-slice), allocate-once at
module-init.  Extending from string-only to arbitrary slice /
struct / array literals is incremental work on top of the
existing infrastructure.

### Const-ness enforcement

The type checker is the source of truth for "no writes through the
const."  Today this works for the int / string cases — `X = ...` is
rejected.  Verify the same path handles composite consts:

- `X = newValue` — reassignment-of-the-variable rejection.
- `X[i] = newValue` — reject if `X`'s const-ness propagates through
  indexing per the const-in-types rules.
- `X.F = newValue` — same: depends on whether const-on-X
  propagates to its fields.

These rules already exist for `*const T` / `*[]const T` etc. value
types; mostly a test-coverage exercise to pin them for the
`const X T` form.

### When the const-fold-at-read-site path also applies

Even with the initialized-global lowering, scalar consts (int, bool,
char, float, string) are additionally const-folded at every read
site — produces immediate-style LLVM IR that the LLVM optimizer
can DCE through.  That's what Phase A delivers and what the string-
fix already does.  Phase B's "initialized global" lowering is the
**correctness floor** for types that can't reduce to an immediate;
the immediate path is the **optimization** for types that can.

A future refinement: small composites with statically-knowable
values (e.g. `const P Point = Point{1, 2}` — 2-field 16-byte
struct) could be const-folded at read sites too, producing better
LLVM IR than load-from-global.  Not in scope for the initial fix
— the goal is correctness.

## Phase C — Pointer-typed consts

`const P *T = &G` (or `const P @T = ...`) is a const-pointer:
the pointer variable itself can't be reassigned, the pointee's
mutability depends on whether T is const-qualified.

### C1: const-pointer to a static global

    var G int = 42
    const P *int = &G

`P` holds the address of `G`'s storage; the address is fixed at
module-link time.  IR-gen emits `P` as an initialized global whose
value is the mangled-symbol address of `G`.  Reads load `P`'s
value normally; `*P` writes are allowed because `int` isn't
const-qualified.

The type checker needs a hook to verify the initializer is a
static-address expression (`&G` where G is a top-level var, or
similar) — the read-only-at-module-init invariant requires the
address itself to be link-time-fixed.

### C2: const-pointer to nil

    const NULL *int = nil

Mechanical — nil-init is already a known shape.  Verify the
existing nil-handling path makes `EXPR_IDENT` for this const
resolve correctly; add a regression test.

### C3: managed-pointer consts

    const X @Box = make(Box)  // or other allocator expressions

Probably needs design work — `make()` allocates at runtime, so
this is more like "initialize once at module-init, reuse forever"
than "compile-time constant."  Treat as a special case of
Phase B's initialized-global lowering, with refcounting at
module-init plus a final RefDec at module-shutdown (or "never
released" — managed-pointer consts effectively pin their
allocations for the lifetime of the program).

### Recommendation

Cover C1 + C2 in the first cut; defer C3's refcount-lifetime
design until a use case surfaces.

## Suggested implementation order

1. **Phase A** (bool, float): mechanical, ~80 lines, matches the
   string fix shape.  Closes the loud-LLVM-mismatch bool case +
   the silent-zero float case.
2. **Phase B** (composites): route composite-typed consts through
   `moduleGlobals` with initialized-once lowering; add type-checker
   coverage for write-rejection sub-cases.  Larger scope; needs
   careful testing across array / struct / slice / managed-slice
   variants.
3. **Phase C** (pointer consts): C1 (const-pointer to static
   global) + C2 (nil).  C3 (managed-pointer consts) deferred.

Phase A is concrete enough to start.  Phase B and C can land
independently once the producer-side `Kind`-dispatch is in place.

## Out of scope

- Generalized constant-evaluator for arbitrary const expressions
  (e.g., `const X int = f(42)` where `f` is a pure function).
  The simpler model — "consts are literals plus the trivial
  arithmetic combinations already in `evalConstExpr`" — is enough.
- Const-folding through function calls, type conversions involving
  runtime work, etc.
