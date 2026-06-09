# Option B — fully-qualify struct names (cross-package mangler-collision class-killer)

Resume doc (survives context compaction). Worktree: `temp-binate-2` (branch
`work-2`). Owner: the Plan-2/CR-2 author session (Plan-1 is done, so editing
`pkg/binate/types` + `pkg/binate/ir` is clear). User chose the **FULL migration**.

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

## Status
- **Step 1 DONE** — committed `9d1037b6` on work-2 (NOT landed). Split
  `Type.TypeName()` (display) vs `Type.QualifiedTypeName()` (mangling/identity),
  both currently `typeNameImpl(false)` (verbatim) = no-op. Switched the
  mangling/identity callers to `QualifiedTypeName`: `check_generic`
  (`mangleInstantiatedName`), `gen_generic.bn:81`, `check_decl.bn:282-283/318`
  (alias + struct-field identity), `check_decl_func.bn:63/65/92/94` (.bn/.bni
  sig compare). Added dormant `displayLeafName` (strips before the first `.`).
- **Step 2 DONE** — committed `dfd9bbad` on work-2 (NOT landed). `TypeName` now
  `typeNameImpl(true)` (short display). No-op for real programs (names still
  bare). Unit test `types_query_test.bn TestTypeNameDisplayVsQualified` pins the
  split on a constructed qualified-name struct.
- **Steps 3 + 4 REMAINING.**

These commits are on work-2 only; land the whole migration as a batch once 3+4
are done & green (needs per-instance cherry-pick approval).

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
