# Plan: Named distinct scalar types in IR-gen (`type F float32`)

Status: IMPLEMENTED + LANDED (2026-06-07) — binate `b43a0057` (Phase 1:
LLVM + shared type/IR-gen), `5b64b44a` (Phase 2: VM), `0ca49975`
(Phase 3: native aa64/x64).  All phases landed together on main; full
conformance green on every locally-runnable mode (builder-comp,
builder-comp-int, native aa64, native x64-darwin) plus named_scalar
green on the gen2/gen3 LLVM and VM-variant modes; e2e --verify-ir
clean.  Conformance 646-652 are the permanent positive tests.  The
file-length follow-up is also done (binate `873d4720` split
aarch64_ops.bn / aarch64_emit.bn — which the peels pushed over the
soft limit — into aarch64_refcount.bn / aarch64_rodata.bn).  Remaining:
named-scalar coverage on the arm32 / x64-linux modes is unverified
locally (no qemu / Linux toolchain) but shares the verified LLVM / x64
backends, so CI covers it.

## Problem

A **named distinct scalar type** — `type F float32`, `type Celsius int`
(NOT an alias `type F = float32`) — is mis-handled by IR-gen. Two
symptoms, one root cause:

1. **Named-float values mis-lower.** `var x F32 = 1.5; println(cast(int,
   x))` where `type F32 float32` prints `4609434218613702656` (the IEEE
   double bit-pattern of 1.5) instead of `1`. The value is stored/treated
   as a 64-bit int, and `cast(int, x)` bit-reinterprets instead of
   `fptosi`.
2. **Method calls on named distinct scalars fail to link.** `type Celsius
   int; func (c Celsius) Doubled() int {...}; var t Celsius = 21;
   t.Doubled()` → `error: use of undefined value
   '@bn_pkg__builtins__lang__int__Doubled'`. The method is *defined*
   keyed on the receiver's source name (`Celsius`), but the *call* looks
   it up via the receiver value's resolved type — which IR-gen has
   collapsed to `int` — so it searches the universe package (`lang`) for
   `int.Doubled` and finds nothing. **This already breaks named-INT
   methods, not just float.**

`044_distinct_type.bn` (named-int *values*, no methods) passes only by
luck: the int fallback (see root cause) happens to equal int's real type.

## Root cause

The CHECKER models a named distinct type as `TYP_NAMED` with `.Underlying`
set (`check_decl.bn` ~233: `MakeNamedType(d.Name); named.Underlying =
underlying`). The types-level predicates already peel it correctly
(`IsFloat`/`IsInteger` in `pkg/binate/types` check `Underlying` when
`Kind == TYP_NAMED`).

IR-gen, however, **never produces a `TYP_NAMED` for a named distinct
non-struct type**. `resolveTypeExpr` (`pkg/binate/ir/gen_util.bn`):
- handles aliases via `lookupTypeAlias` (which returns the *underlying*,
  correct — aliases are not distinct),
- handles named structs via `lookupStructIdx` (IR-gen represents named
  structs as `TYP_STRUCT` with `.Name`, per `gen_method.bn`'s comment),
- but for a named distinct **scalar** falls through to `return
  types.TypInt()` (the bottom fallback).

So `Celsius`/`F32` resolve to `int` in IR-gen — losing both the correct
scalar layout (float→int) and the named identity (`Celsius`→`int`, so
`baseNamedTypeName` returns `"int"` and method dispatch misfires).

Type-alias registration is gated on `d.IsAlias` in SEVEN places, so named
distinct types are never registered as anything; resolveTypeExpr can only
hit the fallback:
- `gen_module.bn:271` — `GeneratePackage` (the CURRENT package; the one
  that matters for a normal compile — an earlier probe that only changed
  `gen_module.bn:145` had no effect because of this).
- `gen_module.bn:145` — `RegisterStructTypes` (transitive deps).
- `gen_import.bn:218` — `RegisterImports` (direct imports).
- `gen_register_import.bn:75` — `RegisterImport` (singular; test-only, no
  production caller, but keep consistent).
- `gen_repl.bn:161` — REPL session decls.
- `gen_self_types.bn:60` — self-hosted-types registration.
- (`gen_iface_registry.bn:128` is interface aliases — unrelated.)

## Approach

Make IR-gen represent a named distinct non-struct type as `TYP_NAMED` with
`.Underlying` populated (mirroring the checker), and peel `TYP_NAMED` to
its underlying in the Kind-based LLVM-emission/lowering functions. Aliases
keep collapsing to their underlying (no distinct identity, methods
forbidden — `324_err_method_alias`). This preserves the named identity for
method dispatch (`baseNamedTypeName` already handles `TYP_NAMED`) while
giving correct scalar layout.

Rejected alternative: representing a named scalar as `TYP_FLOAT`/`TYP_INT`
with `.Name` set (mirroring named structs). It gives correct layout
without peeling, but `baseNamedTypeName`/`isNamedTypeKind` can't then
distinguish a named distinct scalar from the universe primitive of the
same kind (both carry a Name), so method dispatch stays broken. `TYP_NAMED`
with a distinct Kind is unambiguous.

## Steps

1. **Register named distinct non-struct types with their identity.** At
   the registration sites above, when `d.Kind == DECL_TYPE && d.TypeRef !=
   nil && d.TypeRef.Kind != ast.TEXPR_STRUCT`:
   - if `d.IsAlias`: register `ta.Typ = resolveTypeExpr(d.TypeRef)` (the
     underlying — current behavior).
   - else (named distinct): register `ta.Typ = MakeNamedType(qualName)`
     with `.Underlying = resolveTypeExpr(d.TypeRef)`. (Confirm
     `types.MakeNamedType` is usable from IR-gen and sets `.Name`; set
     `.Underlying` explicitly as the checker does.)
   Add the `lookupTypeAlias(...) == nil` dedup guard everywhere (some
   sites lack it). Keep one helper to build the entry so all sites agree.
   - Open question to resolve during impl: the `.Name` used for a named
     distinct type must be what `baseNamedTypeName`/method-lookup expects.
     Methods register under `recvTypeName(d.Recv.Type)` (the *source*
     name, e.g. `Celsius`) via `methodQualName` (`gen_method.bn`). Ensure
     the `TYP_NAMED.Name` that flows to a value's `.Typ` matches what the
     method-lookup key uses (source name vs qualified name). Check
     `genMethodCall` → `baseNamedTypeName(recv.Typ)` →
     `buildMethodQualName(pkg, typeName, method)` and align.

2. **resolveTypeExpr** (`gen_util.bn`): for a `TEXPR_NAMED` that isn't a
   builtin/struct/alias, it now finds the named-distinct entry via
   `lookupTypeAlias` and returns the `TYP_NAMED`. The `TypInt()` fallback
   should remain only for genuinely-unresolvable names (and ideally be
   tightened, but that's out of scope).

3. **Peel `TYP_NAMED` in the Kind-based lowering functions** (they check
   `Kind ==` directly, bypassing the `IsFloat`/`IsInteger` predicates that
   already peel):
   - `llvmType` (`emit_types.bn`): add `if t.Kind == TYP_NAMED &&
     t.Underlying != nil { return llvmType(t.Underlying) }` near the top
     (alongside the `TYP_READONLY` peel). Currently named types fall
     through to `intLL()`.
   - `typeBits` (`emit_types.bn`): same peel.
   - `typeWidth` (`gen_binary.bn`, IR side, used by `ensureWidth`): same
     peel.
   - `emitCast` (`emit_ops.bn`): peel src/dst (or rely on `llvmType`/
     classification once those peel) so `srcIsFloat`/`dstIsFloat`/widths
     are computed from the underlying. (My earlier reverted probe added a
     `castUnwrapNamed` here — reasonable, but it's moot until step 1 makes
     the type actually `TYP_NAMED` with `.Underlying`; with steps 1-2 the
     type arrives as `TYP_NAMED` and this peel becomes load-bearing.)

4. **Audit every other `Kind == TYP_FLOAT / TYP_INT / TYP_BOOL` (and
   pointer/aggregate) check** that a named scalar value now reaches, in
   BOTH `pkg/binate/codegen` (LLVM) and the other backends:
   - `pkg/binate/vm` (bytecode): its own value-kind/width handling for
     loads, stores, casts, arithmetic.
   - `pkg/binate/native/{aarch64,x64,common}`: cast/width/float-vs-int
     instruction selection.
   Prefer routing these through the `IsFloat`/`IsInteger`/`SizeOf`/`AlignOf`
   predicates (which peel) or a shared `unwrapNamed` helper rather than
   ad-hoc `Kind ==` checks. This audit is the bulk of the work and the
   main regression risk.

5. **Method dispatch**: with step 1, a `Celsius` value's `.Typ` is
   `TYP_NAMED("Celsius", int)`. `baseNamedTypeName` returns `"Celsius"`
   (it already handles `TYP_NAMED`), so the call resolves to the
   `Celsius.Doubled` symbol. Verify `methodSig`'s
   `resolveTypeExpr(d.Recv.Type)` (the receiver param type) and the
   def-side mangling agree with the call-side lookup.

## Risks / things to watch

- **The Kind-check audit (step 4)** is broad. A named scalar now flows as
  `TYP_NAMED` to places that previously saw a concrete `TYP_FLOAT`/`TYP_INT`
  (because of the fallback or because named scalars were never exercised).
  Each must peel. Missing one = a new mis-lowering. Lean on conformance
  across all backends to catch these.
- **Aliases vs named distinct** must stay distinguished: `type X = int`
  collapses (no methods, no identity); `type X int` keeps identity. The
  `d.IsAlias` branch in step 1 is the gate.
- **Cross-package** named distinct types (`pkg.F`): the import
  registration sites (gen_import / gen_register_import) must build the
  `TYP_NAMED` with the qualified name so cross-package method dispatch and
  layout work.
- **Struct fields / params / returns / slices of named scalar types**:
  `[]Celsius`, `struct { t Temp }`, `func() F32` — confirm element/field/
  result layout peels correctly (SizeOf/AlignOf/llvmType).
- **`SizeOf`/`AlignOf`/`FieldOffset`** in `pkg/binate/types` (the shared
  layout contract): verify they peel `TYP_NAMED` (they likely do via the
  same `Underlying` pattern as `IsFloat`, but confirm — a wrong size here
  corrupts every aggregate containing a named scalar).
- BUILDER-compilability: all touched files in `cmd/bnc`'s tree
  (pkg/binate/{ir,types,codegen,...}) must stay within BUILDER 0.0.7
  (no closures/generics-in-new-code beyond what's already used; basic
  if/var/struct/for). The peels are trivial and BUILDER-safe.

## Testing plan

The whole reason this gap went unnoticed: the only positive test for
named distinct types, `044_distinct_type.bn`, exercises the **degenerate**
case — a named-**int** *value*, no methods — which passes by luck because
IR-gen's fallback for an unresolved named type IS `int`. The feature was
never tested on a non-`int` underlying or beyond plain value use, so two
whole capabilities (named-float layout, method dispatch) silently didn't
work.

The fix MUST ship with **positive conformance tests in the regular suite**
(compile-run-compare-stdout, NOT xfail/regression markers once green) that
cover the feature as a matrix of *underlying kind* × *operation*. The
guiding principle: **never let a named-distinct test rest on the `int`
underlying alone** — `int`-over-`int` is the one case that works without
the feature. Every test should use at least one underlying that is NOT the
default `int` (float32, float64, a sized/unsigned int, bool, char) so a
regression to the fallback is caught.

### Coverage matrix

Underlying kinds to cover (the columns): `float32`, `float64`, `int8` /
`uint8` (width + sign), `bool`, `char`. (`int` and `int64` are the
"works-by-luck" baseline — include them only as a control, not as the sole
coverage of any operation.)

Operations on a named distinct scalar (the rows), each asserting correct
runtime output:
1. declare + initialize (`var x F = lit`), read back via `cast`.
2. cast to and from the underlying (`cast(int, x)`, `cast(F, n)`); for
   float, cast that actually loses precision (1.5 → int 1) so an
   int-mislowering is visible.
3. arithmetic (`+ - * /`) — the result must compute at the underlying's
   kind/width, not int (e.g. named-float division `7.0/2.0` → 3.5, not 3).
4. comparison (`< == >`).
5. assignment and compound assignment (`x = y`, `x += y`).
6. as a function parameter and return (incl. multi-return).
7. as a struct field — read and write.
8. as an array element and a managed-slice element — index read and write.
9. `const` of the named type (`const C F = 1.5`).
10. **methods** — value receiver AND pointer receiver, on a named-int and
    a named-float type; plain `x.M()` calls. (This is the dispatch case
    that is wholly broken today.)
11. a named scalar satisfying an interface (named-scalar method set used
    through an iface value), if that composition is supported.
12. cross-package: an exported named distinct type used (value + method)
    from an importing package.

### Concrete conformance tests to add (numbers chosen at impl time)

Group the matrix into a handful of focused programs (one concern each, so
a failure points somewhere):
- `NNN_named_scalar_float` — float32 + float64: decl/init, cast (with
  precision loss), `+ - * /`, comparison. Would have caught symptom #1.
- `NNN_named_scalar_methods` — value- and pointer-receiver methods on a
  named-int AND a named-float type; direct calls. Would have caught
  symptom #2 (and the latent named-int method breakage).
- `NNN_named_scalar_aggregate` — named scalar as struct field, array
  element, and managed-slice element (read + write each).
- `NNN_named_scalar_func` — named scalar as param, single return, and
  multi-return; pass through a chain.
- `NNN_named_scalar_sized_int` — `type B uint8` / `type S int8`: width and
  sign behavior (e.g. wrap / sign-extend on cast), distinct from `int`.
- `NNN_named_scalar_const` — `const C F = 1.5` (named-float const) used in
  an expression.
- `NNN_named_scalar_xpkg/` (multi-pkg) — imported named distinct type:
  value use + method call across the package boundary.
- Keep `044_distinct_type` as the named-int-value control.

These run automatically in **every conformance mode** (builder-comp,
…-int, …-comp, native aa64 / x64-darwin, arm32) — which is exactly the
multi-backend coverage the step-4 audit needs: a named-float method that
works on LLVM but not the VM or aa64 will fail its cell.

### Workflow (Bug Discovery Protocol)

Add the positive tests FIRST, before the fix. They will fail on the
unfixed compiler, so mark each with `.xfail.<mode>` for the modes where it
fails (likely all). Implement the fix, then DELETE the xfail markers so
they become permanent positive tests in the regular suite. The end state
is positive coverage, not regression markers — the xfail is only the
during-development bookkeeping that keeps the suite green mid-fix.

### Unit tests (faster, pin the mechanism)

- `pkg/binate/ir`: `resolveTypeExpr` on a registered named distinct scalar
  returns `TYP_NAMED` with `.Underlying` set to the right scalar (not
  `TypInt()`); an alias still returns the bare underlying.
- `pkg/binate/codegen`: `llvmType` / `typeBits` on a `TYP_NAMED(float32)`
  yield `"float"` / `32` (peel), and `emitCast` classifies a named-float
  src/dst as float.
- `pkg/binate/types`: confirm `SizeOf`/`AlignOf` already peel `TYP_NAMED`
  (add a guard test if not).

### Regression sweep

Because this is a central type-resolution change, run the FULL conformance
matrix (all default modes + native) and all unit-test packages before
landing each phase — not just the new tests.

## Phasing (suggested commits)

1. Registration + `resolveTypeExpr` → `TYP_NAMED` for named distinct
   non-struct types (the 5-7 sites via a shared helper) + `llvmType`/
   `typeBits`/`typeWidth`/`emitCast` peel. Get the LLVM named-float +
   method-call tests green.
2. Backend audit: VM, then native (aa64/x64) — one commit each, each with
   its conformance modes green.
3. Aggregate/cross-package coverage + any audited Kind-check fixes.

Land each through local main only with explicit per-instance approval;
re-run hygiene after every landing rebase (conformance numbers).
