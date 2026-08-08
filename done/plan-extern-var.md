# Plan: `.bni` extern-var support

> **Status: COMPLETE (2026-06-04).** Scalar, raw/readonly-slice, and
> managed-slice extern vars work end-to-end (read + write) across all 6
> default modes. All three tracked managed/ptr edge cases are now
> resolved: `&globalScalar` as a compiled value (`551`, binate
> `99655f4e`), field-write through an imported ptr var (`561`, binate
> `733d4485`), and the cross-pkg managed-ptr value-copy crash (`559`,
> fixed by recent main work — `559` covers aliasing in all modes and the
> new `586` verifies refcount balance, binate `66aef4c1`).

## Why

`const` is scalar-only (plan-const-readonly steps 6–7 removed
string/composite consts), so an exported `var` is the only way to expose
a string (or any non-scalar) constant across packages. A top-level
`var X T` in a `.bni` is an extern declaration; the package's `.bn`
provides the definition; other packages read/write it as `pkg.X`.

## Core mechanism: dotted-name reuse

Mirrors the exported-func path. The cross-package reference carries the
DEFINING package's dotted qualname (`buildQualName`, e.g. `pkg/x.Count`).
`mangle.GlobalName` detects the dot and routes to `writeBnDotted`, which
folds the embedded path out and IGNORES the passed `modulePkgName` — so
the importer's `external global` declaration and its read/write
references mangle to the owner's symbol (`bn_pkg__x__Count`), matching the
owner's `= global` definition. This is the same escape hatch `FuncName`
uses for cross-pkg calls, so NO `emit_util` change is needed and there is
no silent-wrong-symbol risk.

## Layers

1. **loader** — none needed: importers read the `.bni` directly
   (RegisterImport + bni_scope); the defining `.bn` provides the symbol.
2. **bni_scope** (`types/bni_scope.bn`) — a `.bni` `var X T` (explicit
   type) → `defineVar` (`SYM_VAR`), so importers resolve `pkg.X` and the
   defining package can cross-check its `.bn`.
3. **checker** (`types/check_decl.bn`) — `checkBniVarMatch`: the `.bn`
   definition's type must equal the `.bni` declaration's (else an
   importer, reading the `.bni` type, misreads the storage).
4. **ir.Global** (`ir.bni`, `ir/gen.bn`) — `IsExtern` flag on `Global` +
   `ModuleGlobal`.
5. **gen_import** — registers an imported var into `moduleGlobals` under
   its dotted qualname (`IsExtern`); `gen_module` materializes it as an
   `external global` decl, not a definition.
6. **gen_func / gen_selector** — `lookupImportedGlobalPtr` /
   `lookupImportedGlobalRead` (read) + `genImportedVarLvalue` (write /
   address-of lvalue), all keyed on the dotted name.
7. **emit** (`codegen/emit.bn`) — `@<sym> = external global <ty>` for
   `IsExtern` globals (suppress the initializer).
8. **VM** (`vm/lower_data.bn`) — `materializeGlobals` keys globals by
   their package-qualified name (`mangle.QualifyName`) and ACCUMULATES
   across modules (vs the old per-module overwrite); `lookupGlobalAddr`
   qualifies its lookup the same way; extern (`IsExtern`) globals are not
   allocated by the importer (the owner owns the storage).

## Slices (as landed)

- **Slice 1** — read (scalar/raw), compiled; int modes xfailed.
- **Slice 2** — VM cross-package globals keying; un-xfails the int modes.
- **Slice 3** — writes (`pkg.X = v`). `&pkg.Var` is blocked by the
  pre-existing `&globalScalar` compiled bug (`551`).
- **gap fix** — reject `pkg.C = v` (assignment to a qualified const), the
  sibling of deferral-3's `&pkg.C` rejection (shared `resolveQualifiedSym`).
- **Slice 4** — managed: managed-slice works fully (read, len, element
  write, value-copy with shared backing + refcount); managed-ptr field
  read works; managed-ptr value-copy (`559`) and ptr-field-write (`561`)
  are tracked.

## Scope decisions (user, 2026-06-03)

- IN: scalar, raw/readonly-slice, managed (`@T` / `@[]T`); read + write.
- `version.Version` export DEFERRED to a separate `version.bn` redesign
  (see the version-redesign entry in `claude-todo.md`). No bnc-tree
  consumer is wired (that would force a BUILDER bump); conformance tests
  are the validation.

## Tests

`conformance/548` (read scalar+raw), `549` (`.bni`/`.bn` type mismatch),
`552` (write + read-modify-write), `557` (assign-to-const reject), `558`
(managed-slice + managed-ptr field). Tracked xfail repros: `551`, `559`,
`561`. Unit: `bni_scope_test` (SYM_VAR), `check_decl_test` (qualified
read/assign/addr-of guards), `lower_test` (globals-accumulation
isolation).
