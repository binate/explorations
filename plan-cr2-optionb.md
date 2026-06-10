# Option B — fully-qualify struct names (cross-package mangler-collision class-killer)

Resume doc (survives context compaction). Worktree: `temp-binate-2` (branch
`work-2`). Owner: the Plan-2/CR-2 author session (Plan-1 is done, so editing
`pkg/binate/types` + `pkg/binate/ir` is clear). User chose the **FULL migration**.

## Adversarial review of the CR-2 batch — DONE (2026-06-10)

The pre-step-3 adversarial review ran (56-agent find→cross-examine) and is fully
remediated: it surfaced a CRITICAL (X2 — R2-3's negative-offset panic false-fired
on an iface-value upcast to an unrelated zero-method interface; root-caused as a
checker duck-typing hole, fixed via `isUniverseAny` + `@Iface→*Iface` decay, binate
`4ac123da`) and a MAJOR silent miscompile (B1/X3 — bare const-group member dropped
its inherited narrow type, binate `b9d6d807`), plus four cheap minors (binate
`e16d53bc`). All landed. Remaining open items are pre-existing / user-owned
(X3-highbit signed sign-bit, B2 named func-values) — see `claude-todo.md`.

## Goal
Make a struct's `Type.Name` **fully path-qualified at definition**
(`pkg/binate/loader.Package`) so codegen mangling is unambiguous regardless of
which module is compiling, eliminating the cross-package struct-name mangler
collision class at its root (the `reflect.Package` vs a module's own `Package`
bug, plus the latent `genMethodValue` value-receiver leak). Mirrors the
already-shipped **function-name** migration (`qualifyForCurrentModule` +
normalized `lookupFunc*`; "storage always qualified"). Symbol output is
byte-identical → **no ABI/relink break**; `modulePkgName` becomes a no-op for
named structs (still needed for anonymous `__anon_`/`__closure_`/multi-return
tuple structs, which MUST stay bare + per-module).

## Status — ALL STEPS DONE on `work-2` (NOT yet landed), verified across all modes (2026-06-10)

`work-2` carries the full migration as 5 commits atop local main:
- **Step 1** `e0e0d011` — split `Type.TypeName()` (display) vs `QualifiedTypeName()`
  (mangling/identity); mangling/identity callers switched to `QualifiedTypeName`.
- **Step 2** `7fd19e6c` — `TypeName` short-formats (`displayLeafName`); no-op while
  names are still bare.
- **Step 3** `dc1e5241` — checker qualifies struct `Type.Name` at definition:
  `currentPkgPath` + `WithPkgPath`/`RestorePkgPath` + empty-safe `QualifyName` (==
  IR `buildQualName`/`qualifyForCurrentModule` byte-for-byte); set at all 4 entry
  points (Check / checkPackageImpl / LoadPackageInterface / `CheckMainPersistent`
  persistently for the REPL); qualified `check_decl`/`bni_scope`/`check_generic`
  struct producers; display reads routed through `.TypeName()` in
  `check_impl`/`check_decl_func`.
- **Split** `d6b0e3c0` — extracted type-name formatting into `type_name.bn`
  (`types_query.bn` length cap).
- **Step 4** `6e15d8bb` — IR own-module struct registration qualified (gen_module /
  _single / self_types / repl), `lookupStructIdx` qualify-if-bare + bare fallback
  for synthetic anon/closure/tuple structs, `RegisterSelfTypes` sets its own pkg
  path (runs before `currentModulePkgPath` is set), `dtorTypeSuffix`/`emit_debug`
  leaf-name fixes, `qualifiedReflectPackageType` removed (reflect.Package now
  qualified by the checker).

**Byte-identical / green:** builder-comp 1330/0, builder-comp-comp self-host
1330/0, builder-comp-int 1300/0, native aa64 1299/0; native x64-darwin 1326/3
(the 3 failures — 526, 569, capturing-closure-multi-return — are pre-existing,
predate this work). Units mangle/types/ir/codegen/vm green; hygiene green. The
mangler's dot-awareness keeps symbols identical (`mangle_test
TestStructNameCrossPkg`). Own-module ALIAS registration left bare (separate
table, consistent register+lookup; validated by the full suite); closure structs
left bare (per-package). **Land the 5-commit batch (per-instance approval).**

## Step 3 — qualify struct .Name at the checker (the functional fix)
1. `types_query.bn`: add `currentPkgPath @[]char` (full path) global +
   `WithPkgPath`/`RestorePkgPath`, mirroring the existing
   `currentPkgShort`/`WithPkgShort`. (Keep `currentPkgShort` — interfaces use
   it via `MakeInterfaceType`, out of scope.)
2. `checker.bn`: set `currentPkgPath` from the FULL path at all 3 entry points
   (today only the short segment is threaded): `Check` → `stripQuotes(file.PkgName)`;
   `CheckPackage`/`CheckPackageDecls` → `path` arg; `LoadPackageInterface` → `path` arg.
3. Add an idempotent `QualifyName(pkgPath, name)` helper (in types, or inline):
   if `name` already contains `.`, return it; else `pkgPath + "." + name`. Must
   match the IR's `buildQualName(fullpath, name)` byte-for-byte.
4. Qualify the struct producers: `check_decl.bn:242` (`st.Name = QualifyName(...)`),
   `bni_scope.bn:238` (`resolved.Name = QualifyName(...)`), and the generic
   instantiation base in `check_generic.bn` (~211, qualify the base before
   `mangleInstantiatedName`).
5. Fix type-name DISPLAY reads that read `.Name` directly (would print the path):
   `check_impl.bn:169/184/200` (`named.Name` → `named.TypeName()`); audit
   `check_decl_func.bn:137` (`named.Name`) and `:217` (`base.Name`) — switch to
   `TypeName()` if they're type names. (Scope keys, field/method/decl/iface names,
   and `Identical`'s `.Name` compare are all FINE — `Identical` becomes more
   correct, no longer false-matching cross-pkg same-name structs.)
6. After step 3 the `reflect.Package` leak is fixed via the checker (PackageType
   now returns a qualified type) — `qualifiedReflectPackageType` becomes
   removable (do it in step 4 to keep the diff coherent).

## Step 4 — qualify IR own-module storage + normalize lookups (consistency)
LANDED-AS-ONE-ATOMIC-STEP (lockstep, highest blast radius):
- Qualify own-module struct registration via `buildQualName(currentModulePkgPath,
  d.Name)` (currentModulePkgPath is already the full path): `gen_module.bn:236/238`,
  `gen_module_single.bn:50/52`, `gen_self_types.bn:28/30`, `gen_repl.bn:184`,
  and own-module alias paths.
- Flip EVERY own-module lookup to the qualified key IN THE SAME COMMIT:
  `lookupStructIdx` (gen.bn:351 — make it qualify-if-bare, mirroring
  `lookupFunc*`), and callers `gen_util.bn:285`, `gen_module.bn:234/308`,
  `gen_module_single.bn:48/98`, `gen_self_types.bn:22`, `gen_func_lit.bn:330`,
  `gen_composite.bn:33`. A missed lookup → bare lookup misses qualified
  registration → wrong/undefined type.
- Companion: `gen_copy_emit.bn`/`gen_dtor_emit.bn` local-vs-foreign classification
  (`hasDot` heuristic → `dotPrefix==modPkg`); `gen_dtor.bn:36-38 dtorTypeSuffix`
  emit only `dotSuffix(name)`; route local copy/dtor through the existing
  `qualifiedDtorNameForType`/`qualifiedCopyNameForType` dot-splitters. Closure
  struct (`gen_func_lit.bn:242` → `emit_funcvals_closure.bn:43`) may stay bare
  (per-package private) — confirm def+ref both mangle with the same modulePkgName.
- Remove `qualifiedReflectPackageType` (gen_import.bn ~166-188 + call ~336);
  verify 525/532 still pass.

## Testing (each step)
Full conformance across ALL chains (`conformance/run.sh`): `builder-comp`,
`builder-comp-int`, `builder-comp-comp`(/-comp-comp/-comp-comp-comp), native
`aa64` + `x64_darwin` (x64-linux + arm32 not locally runnable). Unit:
`mangle types ir codegen vm`. Watch: 525/532 (reflect descriptor), 270/062
(cross-pkg structs), generics, err-text 221/236/549. Confirm symbol output
byte-identical (mangle_test). Then add the **dedup-mismatch guard** (separate
follow-up, user OK'd abort/panic as a codegen precondition assert).

## Key facts
- `QualifiedTypeName` = full (mangling/identity); `TypeName` = short display
  (strips before the first `.`; pkg paths use `/` not `.`, so first `.` is the
  separator). `displayLeafName` already implemented.
- Checker qualified name MUST equal IR `buildQualName(fullpath, name)` so a
  leaked checker type mangles to the same symbol the defining package emits.
