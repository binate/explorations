# Plan: file-scoped imports (fix the CRITICAL package-scoped-imports bug)

Status: **LANDED** `cf0d1cad` (2026-06-18). Direction 1 /
option A from the CRITICAL "imports are package-scoped" entry in
`claude-todo.md`. Owner: this session (the other worker is on the spec).

**Done (worktree `work-2`, 3 commits on top of main):**
- Checker file-scoped imports + `loader.Package.Files` + the 12-file in-tree
  leak cleanup + a REPL pending-receiver fix.
- IR-gen file-scoped imports for the current module (per-decl alias overlay).
- `.bni`-prepended decls resolve imports via the merged set (`declImportFile`).

**Validation (all green):** builder-comp conformance **1495/0**, builder-comp-comp
(gen2 self-host) **1495/0**, builder-comp unit **45/0**; new conformance tests
830 (same-alias → 1,2, was 1,1), 831 (cross-file leak → clean reject), 832
(implicit same-last-segment → 100,200) pass under builder-comp, gen2, VM, and
native-aarch64. Fixes facets **A, B, C** for NON-GENERIC decls. Facets **D**
(alias-vs-decl redeclaration, `973e82f7`) and **H** (import-cycle detection,
`a4d8a907`) LANDED. **GENERIC instantiation: qualified-TYPE facet FIXED**
(`d2a9ff20`) — a generic body's package-qualified field/method TYPES now resolve
against the generic's defining-file imports (conformance 841); the only remaining
piece is the transitive-extern declaration for cross-package func CALLS in a
generic body (part c; conformance 837 is now a NATIVE-only xfail — VM passes).
See the CRITICAL entry in `claude-todo.md`.  This was the "Generics" risk area
flagged below that the original file-scoped work missed.
KNOWN RESIDUAL: a same-alias collision in a package-level `var x = dep.Foo()`
across files (package-init lowers under the merged overlay) — narrow; §Steps/3.

---

(Original plan below.)

Goal: a qualifier `pkg.X` in file F resolves against **only F's imports**, in
both the type checker and IR-gen (they must agree — opposite-winner split). Fixes
facets A (visibility leak), B (wrong-package incl. types → memory corruption),
C (implicit same-last-segment). Facet D (alias-vs-decl redeclaration) and the
(H) cycle-detection fix are separate follow-ups.

## Core idea

Mirror Go's `fileScope (imports) → packageScope (decls) → universe`. Resolution
everywhere is `c.Scope.Lookup(name)` → `SYM_PKG`, so if each file's imports live
in a **fileScope** (child of packageScope) and `c.Scope` is set to that fileScope
while processing F's decls, all ~10 qualifier-resolution sites become file-scoped
*with no change to them*. The only collector change: package-symbol `define*`
must target `c.PackageScope` (not `c.Scope`, which is now the fileScope).

## Why option A (Package.Files)

`merged.Imports` is deduped, so it can't reconstruct per-file imports. The
checker needs each file's own imports keyed by file. `loader.Package.Files`
(added, holds the pre-merge impl ASTs, each with undeduped `Imports` + `Pos.File`)
plus `pkg.BNI` (for `.bni`-prepended decls) provide this. IR-gen already has
`pushFileImports`/`popFileImports` for *imported* packages; the gap was the
*current* module — `Package.Files` fills it.

## Steps

### 1. Loader — `Package.Files` ✅ DONE
- `loader.bni`: add `Files @[]@ast.File` to `Package`.
- `loader.bn`: `pkg.Files = files` (was discarded).

### 2. Checker — file-scoped `checkPackageImpl`
- **Signature:** `CheckPackage(path, merged, files @[]@ast.File, bni @ast.File)`
  and likewise `CheckPackageDecls`. Update 7 call sites (cmd/bnc, cmd/bni,
  cmd/bnlint, repl/session, repl/mid_session_import, tests) to pass
  `pkg.Files, pkg.BNI`. (Keep `merged` — it carries the `.bni` prepend +
  Exported marks that the loader already applied; `files`/`bni` are only for
  per-file import provenance.)
- **`importsFor(file)` map:** build file-path → that file's `ImportSpec`s from
  `files` (keyed by each file's `Pos.File`) and `bni` (its own `Pos.File`).
- **`pushFileScope(packageScope, imports)`:** push a child scope, `registerImports`
  that file's imports into it. (Reuse `registerImports`, but it must take an
  import list / file rather than read `merged`.)
- **Restructure `checkPackageImpl`:**
  1. push packageScope; `c.PackageScope = packageScope`; import own `.bni` syms.
  2. **package-wide passes** (`c.Scope = packageScope`, no imports needed):
     `checkDuplicateDecls(merged.Decls)` (cross-file dup — extracted here, see
     below), `preRegisterTypeNames(merged.Decls)`, `resolveBuiltinScalarTypeDecls`
     (unqualified only).
  3. **consts** — file-scoped value resolution (see below).
  4. **Phase 1 (signatures), per file group:** for each (file F, decls) from
     `groupByFile(merged.Decls)`: `c.Scope = pushFileScope(F)`; `collectDeclsBody`
     (defines → `c.PackageScope`); restore `c.Scope = packageScope`.
  5. if checkBodies: `checkAllImplsSatisfaction` (package-wide).
  6. **Phase 2 (bodies), per file group:** same per-file scope, `checkDecls`.
  7. register package scope; restore saved scope/flags.
- **`groupByFile(merged.Decls)`:** merged is already contiguous by file (`.bni`
  decls first, then each file's decls in order), so group by `Pos.File` runs in
  one pass. `.bni`-sourced decls (Pos.File ends `.bni`) use `bni`'s imports.
- **define-target sweep:** in the package-level collectors only —
  `collectDeclsBody` (defineFunc/defineVar), `collectTypeDecl` (defineType×N),
  `collectMethodDecl`, `collectInterfaceDecl`, `collectImplDecl`, and const
  resolution — change `c.Scope` → `c.PackageScope` for **define** calls. Keep
  `c.Scope` for **lookups/resolveTypeExpr** (so qualifiers see the fileScope).
  SAFE because in every non-multi-file path (single-file `Check`, REPL
  `CheckMainPersistent`/`CheckDeclInScope`) `c.PackageScope == c.Scope`. Do NOT
  touch local collectors (`checkVarDecl`/`checkShortVarDecl`) — locals stay in
  `c.Scope`.
- **dup extraction:** move `checkDuplicateDecls` out of `collectDeclsBody` (it
  runs per-file there now) into the callers that pass the whole list:
  `checkPackageImpl` (over `merged.Decls`, package-wide) and single-file `Check`
  (over `file.Decls`). CheckDeclInScope passes a single decl (dup over 1 = no-op;
  REPL redef handled by AllowRedef). Verify `checkDuplicateDecls` group recursion.
- **const file-scoping:** `resolveConstByName` resolves a const's value; set
  `c.Scope = fileScope(constDecl.Pos.File)` around that value eval so a
  `const C = pkg.X` resolves `pkg` against C's file. The const *set* stays
  package-wide (cross-file ordering via ConstResolving/ConstResolved). defineConst
  → `c.PackageScope`.

### 3. IR-gen — file-scoped current module
- Make `resolveImportPkg` consult the **current file's** imports, not the global
  `importAliasNames`/`importAliasPaths`. IR-gen already has
  `pushFileImports`/`popFileImports` (used per imported package); drive the
  current module's generation per-file using `pkg.Files` so each file's decls
  lower under that file's alias→path map. Must agree with the checker.
- Verify the GeneratePackage path and the const-fold (`gen_const_fold.bn`) +
  selector (`gen_selector.bn`) + call (`gen_call.bn`) sites that call
  `buildQualName`/`resolveImportPkg`.

### 4. Tests + all modes
- Conformance multi-package tests: visibility-leak → `.error`; same-alias-diff-pkg
  → correct output (1,2); implicit same-last-segment (facet C) → correct;
  cross-file forward-ref regression (type/func/const in file A used in file B and
  vice versa) → compiles + correct.
- Verify builder-comp, builder-comp-int (VM), builder-comp-comp (gen2), native
  (aa64), and unit tests for every changed package (types, loader, ir, codegen).

## Risk areas (test deliberately)
- **REPL/tentative/pending** (CheckDeclInScope, TentativeMode) — the define-target
  change and any scope assumptions; the REPL relies on `c.PackageScope == c.Scope`.
- **Generics** (`GenericTypeDecls`/`GenericTypeDeclPkgs`) — instantiation resolves
  under a scope; ensure file context is right for cross-package generic use.
- **Impls/interfaces** — `collectImplDecl`/`collectInterfaceDecl` define targets;
  impl-satisfaction runs package-wide between phases.
- **Consts** referencing qualified imports across files (interacts with the
  transitive-`.bni`-const CRITICAL item — keep checker/IR-gen in lockstep).
- **`.bni`-prepended decls** resolving under `bni`'s imports, not a `.bn` file's.
- **Single-file packages** (one file, or `.bni`-only) — degenerate to one scope.

## Landing
Loader (step 1) can land as its own green commit. Steps 2+3 must land **together**
(checker-only would make the checker resolve B correctly while IR-gen still
miscompiles — a new disagreement), with the conformance tests, as one coherent
change (or a tightly-sequenced pair kept green). Bring the full diff + mode
results to the user before any cherry-pick.
