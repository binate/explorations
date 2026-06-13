# Plan: Global injection (cross-mode sentinel identity)

## Status — ✅ PROVEN (binate `d3896776` on `os-inject-wip`, pending landing)

All 12 `pkg/std/os` tests pass in `builder-comp-int` — `errors.Is` classification
AND `io.IsEOF` cross-mode identity. `errors`/`io` made native-only + injected
(functions + globals). vm/ir/loader/codegen unit tests + conformance
`builder-comp` 1404/0 + hygiene 13/13 green.

Two bugs found building it:
- **FQ-name mismatch.** A package's OWN global has a BARE `ir.Global.Name`
  (`"ConditionsUnmet"`), but bytecode cross-package reads key on the qualified
  `"pkg/std/errors.ConditionsUnmet"` — the emitter must qualify (matching
  `materializeGlobals`'s `qualifyCallName`). The bare name null-resolved →
  segfault in `vm.execMemoryOp` reading a 16-byte iface from address 0.
- **Native-only test target.** `runTests` skips native-only packages in its
  lowering loop — including the package UNDER test. Fix: a test-target exemption
  (`pkgIsTestTarget`) so a native-only package is injected only as a DEPENDENCY,
  but lowered to bytecode when it IS the `--test` target (so its own `Test*`
  run). Symmetric in the lowering skip + `injectStdlibExterns`.
  (`errors.Rooted` round-trips fine through function injection — no arg gap.)

**Remaining before landing / generalizing:**
- Native globals emitter (Chunk 4b): the native descriptor encoder still emits
  empty `Globals` (`common_pkg_descriptor.bn`); the LLVM backend — which the VM
  uses — is complete, so this is native-mode descriptor consistency only.
- Other-mode `os` xfails (`builder-comp-comp-int`, `builder-comp-int-int`): test
  whether injection works there; remove the xfails if it does.
- Generalize the inject list to all `pkg/std/**` + factor it into a file + a
  hygiene check that every `pkg/std/**` package is covered.
- Split `loader.bn` (now 501, just over the soft limit — `markBniExportedVars`).
- Full conformance (`builder-comp-int`, native modes).

## Problem

The bytecode VM injects compiled stdlib packages as native externs. When a
native-only package returns a sentinel `@Error` (e.g. `pkg/std/os` classifies
an errno to the native `errors.NotFound`), bytecode code that does
`errors.Is(err, errors.NotFound)` must compare against the **same** object —
`errors.Is` matches by pointer identity (`same`). Today `errors` is lowered to
bytecode, so the bytecode session allocates its **own** sentinel set via the
bytecode `errors.__init`, and the identity never matches. Failing tests:
`pkg/std/os` `TestOpenNotFoundClassified` / `TestOpenExclClassified` /
`TestOpenDirForWriteClassified` / `TestByteReadWrite` (the latter via
`io.IsEOF`-adjacent classification) in `builder-comp-int`.

Function injection (`RegisterPackageFunctions`) is functions-only —
`reflect.Package` is `{Name, Functions}`. We need **global injection**: a
bytecode reference to an exported global of a native-only package resolves to
that package's single native storage cell.

## Mechanism (symmetric with function injection)

The resolution seam already exists: a cross-package read of `P.Sentinel`
(where `P` is native-only, not lowered) is an `IsExtern` global, which
`materializeGlobals` **skips** allocating; the read lowers to
`lookupGlobalAddr("pkg/std/errors.NotFound")` against the package-level
`globalNames`/`globalAddrs` tables (`pkg/binate/vm/lower_data.bn`). So we
register {FQ-name → native address} into those tables and the read resolves —
no name transformation, no double-alloc.

- `reflect.bni`: add `GlobalInfo{Name, Addr}` + `Package.Globals *[]@GlobalInfo`.
- `_Package()` synthesis: emit `&<global>` per exported global — same
  relocation shape as `FunctionInfo.Value`, but addend **0** (a `var` cell has
  no static-managed 2-word header, unlike function-value handles).
- VM `RegisterPackageGlobals(vmInst, p)` mirrors `RegisterPackageFunctions`,
  appending each `{gi.Name, gi.Addr}` into `globalNames`/`globalAddrs` via a new
  `registerGlobalAddr` helper in `lower_data.bn`.

### Ordering / identity / lifetime — all clean
- Native `errors.__init` runs at **cmd/bni startup** (cmd/bni imports
  os→errors), before the inner VM is built. `injectStdlibExterns` runs *before*
  the per-module `LowerModule` loop, so the address is registered before any
  bytecode reads it.
- `IsExtern`-skip means the only registered entry for `pkg/std/errors.NotFound`
  is the injected native one — single first-match in the linear table.
- Sentinels stay alive via the native package globals; a bytecode read just
  loads the pointer (no RefInc); `errors.Is` does `same()` only.

## Resolved design decisions
1. **Exported-global tracking → IR flag (symmetric with functions).** Add
   `Exported bool` to `ir.Global` + `ir.ModuleGlobal`; extend the loader's
   `.bni`-membership marking to `DECL_VAR`; thread through `gen_module*.bn`.
   (Exported-global tracking does **not** exist today — `ir.Global` has only
   `IsExtern`, and `markBniExportedFuncs` marks only `DECL_FUNC`.)
2. **`errors` native-only** (`isNativeOnlyInVM += pkg/std/errors`) — single
   instance, like `os`. First pure-Binate (no `__c_call`) package made
   native-only; confirm nothing relies on running `errors` as bytecode.
3. **errors-first, then generalize** — minimal correct slice to prove the
   descriptor-ABI + VM path, not a quick-win dodge.
4. Keep `RegisterPackageGlobals`'s `@VM` param for symmetry (tables are
   package-level, so it is vestigial).

## Steps (proof slice → generalization)
1. **Exported-global tracking** (IR flag): `Exported` on `ir.Global` +
   `ir.ModuleGlobal`; `markBniExportedVars` (or extend `markBniExportedFuncs`)
   for `DECL_VAR` in `.bni`; set in `gen_module.bn` / `gen_module_single.bn`.
2. **reflect.bni**: `GlobalInfo` + `Package.Globals`.
3. **Descriptor encoders** grow to the 3-slice payload (`{ptr,int}×3`): LLVM
   `codegen/emit_pkg_descriptor.bn` + native
   `native/common/common_pkg_descriptor.bn` (the two must stay in lockstep with
   `reflect.bni`).
4. **Globals-table emitters**: LLVM sibling of `emit_pkg_functions.bn` + native
   sibling of `common_pkg_functions.bn` + both per-arch drivers (x64/aarch64).
   Addr relocation addend 0.
5. **VM** `RegisterPackageGlobals` + `registerGlobalAddr` helper.
6. **cmd/bni** wiring: `errors` into `isNativeOnlyInVM`; `import "pkg/std/errors"`;
   `RegisterPackageFunctions` + `RegisterPackageGlobals` for errors (and os
   globals) in `injectStdlibExterns`.
7. **Prove**: `cmd/bni --test pkg/std/os` → the classification tests go green;
   then run conformance incl. nested `builder-comp-int-int` (cross-process
   sentinel identity).
8. **Generalize**: extend the inject list to all `pkg/std/**`, factor the list
   into a separate file, add a hygiene check that every `pkg/std/**` package is
   covered.

Fast inner-loop check before the descriptor-encoder work: a `pkg/binate/vm`
unit test that registers a fake `GlobalInfo` and asserts `lookupGlobalAddr`
returns it.

## Prerequisite (landed on `os-inject-wip`, pending main)
- `12a359dd` — compiled-vtable iface method/dtor dispatch + `_call_shim_pair`
  X0:X1 primitive. Required for compiled iface values (incl. `@Error`) to cross
  into the VM at all.
