# Plan: Embeddable / reentrant VM — eliminate per-run global state

Status: **IN PROGRESS** (2026-06-16). v1 = increments 1–5 below
(reentrancy-only, single-target, interpreter-only, `@GenCtx`/`@Module`
split). Scope decisions ratified by the user 2026-06-16.
**Landed:** increment 1 (loader `loadingStack` → `@Loader`, binate
`bd18a73e`); increment 2 (vm-lowering 9 globals → `@VM`, binate
`b1b19ce1`); increment 3 (types ambient pointers → `@Checker`, **both**
Part A pkg-context fields and Part B `currentChecker` elimination, binate
`dd4b71e0`); increment 4 (IR-gen checker threading → `@Module.Checker` +
`@GenContext.Checker`, kill `ir.SetChecker`/`ir.currentChecker`, binate
`3ef73b24`); increment **5a-1** (IR-gen `@GenCtx{Mod}` scaffold +
`@GenContext.Gc`; `currentModule` global → `gc.Mod`; internal to
`pkg/binate/ir`, no API change, binate `4f203611`); increment **5a-2a**
(`@Func.ModulePkgPath` + `@Module.PkgPath` carriers; emit/init qualify
sites stop reading the `currentModulePkgPath` global, binate `820ea94d`);
increment **5a-2b** (thread `gc.PkgPath` through `resolveTypeExpr`/`lookup*`/
the registration entry points; delete `currentModulePkgPath`; `NewFunc`/
`NewExternFunc` take `pkgPath`; behavior-preserving, both backends green,
binate `3fd61d56`); increment **5b** (module-content registries
moduleConsts/Structs/Funcs/Globals/TypeAliases → `@Module` fields;
element types moved to `ir.bni`; the cross-package pre-passes gained a
`mod @Module` param, threaded through `cmd/bnc`/`cmd/bni`/`repl`, binate
`8a4ddb6a`). **`pkg/binate/ir` now has no per-run module/path package-global
AND no per-run content registry.** What's left for v1: the ir-gen *transient
context* (5c import-alias map + generic registries + counters → `@GenCtx`)
and *interfaces/dtors* (5d). These are mostly field migrations since `gc` is
threaded everywhere — but note 5b proved NOT a "cheap" migration: moving a
registry that the cross-package pre-passes write means threading `@Module`
into those exported entry points and their cmd/repl callers; expect 5c/5d to
carry similar (smaller) ripple where a moved global is written by a pre-pass.

**Inc 1–3 adversarial review (2026-06-16):** verdict — the three are
correct, complete, behavior-preserving refactors (no regressions). It
surfaced two **pre-existing** latent bugs (not introduced by 1–3), now fixed:
- vm `LowerOneFunc`/`LowerOneFuncShadow` didn't re-establish `modulePkgName`,
  so a REPL mid-session import (`LowerModule(importedPkg)` then
  `LowerOneFunc(mainMod)`) mis-qualified the prompt function's bare
  same-package call targets — binate `dee61e09`.
- types `CheckMainPersistent` didn't set `curPkgShort`, so REPL-defined
  interfaces got an empty `Pkg` — binate `142d150f`.
The review also confirmed the headline "reentrant" claim is correctly
scoped to **after inc 5**: `ir.currentChecker` (inc 4) and the ~26 ir-gen
globals (inc 5) are still shared, so a second full compile-and-run in one
process still cross-talks until those land. The plan-mandated end-to-end
two-session reentrancy test cannot pass until inc 4/5 are done, so it
**ships with inc 5** (see Verification below).

This is the larger change that
[`plan-repl-embeddable.md`](plan-repl-embeddable.md) explicitly deferred:
its decision #6 ("single live session per process; the `ir`
process-globals stay as-is") and its "multi-session embedding" out-of-scope
item point here. It also subsumes the `claude-todo.md` entry **"REPL:
remove process-global session state (multi-session blocker)"** — that
entry's `ir` half is now owned by this plan (its line numbers there are
stale as of 2026-06-02; the verified ones are below).

Companion docs: [`plan-repl-embeddable.md`](plan-repl-embeddable.md) (the
push-driven REPL engine that wants this), [`plan-wasm-browser.md`](plan-wasm-browser.md)
(a downstream host), [`ir-backend-guidelines.md`](ir-backend-guidelines.md)
(the target-parameterization rule that the global `target` violates).

---

## Why

The interpreter pipeline — `lex → parse → loader → typecheck → IR-gen →
VM-lower → execute` — keeps **per-run mutable state in package-level
globals** across the loader, types, ir, and vm-lowering stages. A host
that wants two independent executors in one process (two REPL sessions, a
replay/test harness, an IDE or plugin embedder) gets no isolation: the
second run reads and clobbers the first's ambient state.

This breaks with **zero concurrency — just sequential re-runs** (all
verified in tree, 2026-06-16):

- **`loader.loadingStack`** (`loader/loader.bn:107`) is appended
  (`:120`) and reordered (`:141`) but **never reset to empty between
  `NewLoader` calls** → a second program importing a package the first
  already loaded can hit a phantom circular-import error.
- **VM-lowering registries** — `globalNames`/`globalAddrs`
  (`vm/lower_data.bn:50/53`) and `vtableInj{Names,Addrs,Shims}`
  (`vm/vtable_inject.bn:19/23/28`) — **append across modules with no
  non-test reset** (appends at `lower_data.bn:82/102/123`,
  `vtable_inject.bn:40`) → a second `vm.NewVM()` resolves global/vtable
  lookups against the first session's leftovers.
- **`ir.currentModule`/`ir.currentChecker`** (`ir/gen.bn:179/188`) are
  process-global → interleaved IR-gen overwrites each other's context.

The good news: the natural homes already exist as per-instance objects —
`type VM struct` (`vm.bni:532`), `type Loader struct` (`loader.bni:26`),
`@Module` (ir), `@Checker` (types). The state mostly just lives in the
wrong place (package scope) and is threaded implicitly instead of
explicitly. `ir.InitModule` (`ir/gen_module.bn:15`) already resets the ir
globals per compilation — proof they are per-run state hoisted to globals,
not true singletons.

The global `target` (`types/scope.bn:57`) is a second, sharper problem:
it is a process-global layout config, which is a direct violation of the
IR/backend target-parameterization rule (`ir-backend-guidelines.md`), not
just an embeddability issue.

---

## v1 scope (ratified 2026-06-16)

| Question | v1 answer |
|---|---|
| Reentrancy only, or full thread-safety? | **Reentrancy only.** Single-threaded; two *sequential* sessions in one process must not corrupt each other. No locking; the singleton lazy-init race and concurrent-`EmitModule` hazards are out of scope. |
| Cross-target in one process? | **No — single target.** `types.target` and the predeclared-type singletons stay process-shared (immutable after init). `target` stays a *latent* guideline violation; the fix (increment 6) is deferred. |
| Interpreter, or AOT compiler too? | **Interpreter only.** The 17 `codegen/*` + `native/*` globals are off the `cmd/bni` path (verified: `cmd/bni/main.bn` imports `ir/loader/types/vm`, not `codegen`/`native`) and are deferred (increment 7). |
| `@GenCtx` vs fold-everything-onto-`@Module`? | **Split.** Registries that are arguably IR-Module content → `@Module`; pure transient compilation context → a new `@GenCtx`. |

**v1 = increments 1–5.** Increment 6 (cross-target) and 7 (AOT compiler)
are deferred with eyes open, not silently dropped.

After 1–5, the **interpreter is sequentially reentrant** (two VM sessions
in one process, no cross-talk) for a single target.

---

## The globals (verified inventory, 2026-06-16)

82 package-level `var`s across `pkg/binate`. Categorized:

### Group A — per-compilation mutable state that MUST be threaded (the blocker)

**A1 · ir-generation — ~26 globals (the bulk).** `ir/gen.bn` plus
`ir/gen_method_value.bn`, `ir/gen_iface_registry.bn`, `ir/verify.bn`.
Reset between compilations by `InitModule` (`gen_module.bn:15`),
`resetFuncLitState` (`gen_func_lit.bn`), `resetImplState` (`gen_impl.bn`).
Home: **`@Module` (registries) + new `@GenCtx` (transient context)**.
Effort **XL**.

| Global | gen.bn line | Reads (approx) | Role |
|---|---|---|---|
| `currentModule` | 179 | ~88 | in-progress `@Module` pointer |
| `currentModulePkgPath` | 172 | ~66 | module pkg path (name qualification) |
| `currentChecker` | 188 | ~23 | the types `@Checker` (set via `ir.SetChecker`) |
| `moduleConsts` | 53 | ~85 | const decls |
| `moduleStructs` | 78 | ~71 | struct defs (field layout) |
| `moduleFuncs` | 66 | ~44 | func sigs (multi-return lookups) |
| `moduleGlobals` | 100 | ~36 | top-level vars |
| `moduleTypeAliases` | 110 | ~21 | type aliases |
| `currentImportAlias` | 137 | ~35 | import alias being registered |
| `importAliasNames` / `importAliasPaths` | 147 / 150 | ~11 / ~7 | alias→full-path map (mangling) |
| `pendingMsDtors` (+`Names`) | 113 / 126 | ~19 / ~7 | managed-slice dtors to emit |
| `pendingStructDtors` (+`Names`) | 117 / 131 | ~19 / ~6 | struct/array dtors+copies to emit |
| `genericDecls` / `…Pkgs` | 232 / 239 | — | generic-fn instantiation registry |
| `genericTypeDecls` / `…Pkgs` | 246 / 250 | — | generic-type instantiation registry |
| `genericIfaceDecls` / `…Pkgs` | 258 / 262 | — | generic-iface instantiation registry |
| `emittedInstantiations` | 225 | — | dedup of emitted instantiations |
| `currentTypeParamNames` / `…Types` | 213 / 217 | — | active type-param binding |
| `anonStructCounter` | 134 | — | anon-struct name counter |
| `funcLitCounter` | 202 | — | func-literal name counter |

Plus: `moduleInterfaces` (registry; in the iface/registry files), the
`methodValueWrappers` list (`gen_method_value.bn`, reset routes through
`resetFuncLitState`→`resetMethodValueState`), and `verifyIR` (`verify.bn`,
a per-run flag set via `ir.SetVerifyIR`).

**A2 · vm-lowering — 9 globals (interpreter-unique; highest priority).**
`vm/lower_data.bn` (`modulePkgName:37`, `moduleStrings:41`, `curNames:44`,
`curStrings:47`, `globalNames:50`, `globalAddrs:53`),
`vm/vtable_inject.bn` (`vtableInjNames:19`, `vtableInjAddrs:23`,
`vtableInjShims:28`). Home: **existing `@VM`**. Effort **M**.
*Lifetime tiers matter:* `globalNames`/`globalAddrs` + `vtableInj*` are
meant to persist for a whole VM session (→ long-lived `VM` fields);
`curNames`/`curStrings`/`modulePkgName`/`moduleStrings` are per-module /
per-function scratch (→ reset per `LowerModule`/`lowerFunc`, or a small
`@LowerCtx`).

**A3 · loader — 1 global.** `loadingStack` (`loader/loader.bn:107`).
Home: **existing `@Loader`**. Effort **S**. (Smallest, highest
value/effort — fixes the confirmed sequential circular-import false-positive.)

### Group B — global target config (also a guideline violation)

`types.target` (`types/scope.bn:57`; set by `initTarget:60` /
`SetTarget:80`; `target.IntSize` at `:74`). Read by `SizeOf`/`AlignOf`/
`FieldOffset` **and** by `ensureInit` (`types/types.bn:65`), which freezes
`predeclaredInt`'s width from the target (`types.bn:77`). Home: **`@Checker`
+ target-parameterized layout fns**. Effort **M**. **Deferred to increment
6** (single-target v1 keeps it shared).

### Group C — ambient checker/package pointers

`currentChecker` (`types/types_query.bn:12`), `currentPkgShort` (`:40`),
`currentPkgPath` (`:65`). Already call-chain-reentrant via save/restore
(`With*`/`Restore*`), so they only matter under *true concurrency*, not
sequential re-runs. Home: **`@Checker` fields**. Effort **S–M**.
(`ir.currentChecker` in A1 mirrors this via `ir.SetChecker`; both must end
up routing through the one threaded `@Checker`.)

### Group D — effectively-immutable singletons & const tables (leave shared in v1)

`predeclared*` (`types/types.bn:18–58`: the int/uint/float widths, bool,
void, nil, untyped, self) + `typesInitialized` (`:62`); `version.Version`
(`version/version.bn`); the native `regmap`/`names` tables. Lazy-init-once,
never mutated after. **Safe to share across instances iff the target is
identical** — which v1 guarantees. The catch: the int/word-width singletons
freeze their `Width` from group B's `target` at first init, so "leave
shared" is sound only single-target. (If cross-target ever lands, these
become per-`Checker` — see increment 6 / Risks.)

### Group E — compiler-only (off the interpreter path; deferred)

11 `codegen/*` (`emit.bn` `retSeq`/`tmpSeq`/`currentFuncUsesSret`/
`emitDebugInfo`/`modulePkgName`/`moduleStructDefs`, plus `emit_impls.bn`/
`emit_debug_types.bn`/`emit_util.bn`) + 6 `native/{x64,aarch64}/*`
(regmaps, names, `aarch64_refcount` seqs, obj-format flags). All AOT-only —
`cmd/bni` does not import `codegen`/`native`. Real reentrancy hazards for a
*compiler* embedder, but **out of scope for interpreter-first v1**
(increment 7). `debug.verbose` (`debug/debug.bn`) is off-path and
accessor-isolated — leave process-global.

---

## Threading strategy (per must-thread cluster)

**A3 loader → `@Loader` (S).** Move `loadingStack` to a `Loader` field;
`push`/`pop`/`isLoading` take the `Loader` receiver.

**A2 vm-lowering → `@VM` (M).** Move the 9 globals to `VM`. Session-lifetime
ones (`globalNames`/`globalAddrs`, `vtableInj*`) live directly on `VM`;
per-module/function scratch (`curNames` etc.) either reset on `VM` at
`LowerModule`/`lowerFunc` entry or move to a small `@LowerCtx` threaded
through `lowerFunc`/`lowerInstr`. Thread `self @VM` through
`LowerModule`/`LowerOneFunc`/`lowerFunc`/`lowerInstr`/`materializeGlobals`/
`register*Addr`/`lookup*Addr`/`lookupShimVtable`. Deletes the manual resets
in `lower_data_test.bn`.

**C types ambient pointers → `@Checker` (S–M).** Add `currentChecker`/
`currentPkgShort`/`currentPkgPath` as `Checker` fields; move the existing
`With*`/`Restore*` save/restore onto the receiver; readers
(`types_assignable.bn`, `check_interface.bn`, `bni_scope.bn`,
`check_generic_type.bn`, `check_decl.bn`, …) take the `@Checker`.

**A1 ir checker threading → drop `ir.SetChecker`/`ir.currentChecker` (M).**
Replace the global + setter with a threaded `@Checker`. Isolated as its own
increment because it changes the `cmd/bnc`/`cmd/bni`/REPL/codegen-test API
surface in lockstep. Preserve the nil-checker test fallback in `genExpr`.

**A1 ir bulk → `@Module` + new `@GenCtx` (XL).** Registries that are IR-Module
content (`moduleConsts/Funcs/Structs/Globals/TypeAliases`, `moduleInterfaces`,
pending dtor/copy lists, `methodValueWrappers`) fold onto `@Module`. Transient
context (`currentModule`, `currentModulePkgPath`, generic-instantiation
registries, import-alias maps, the counters) goes on a new `@GenCtx` created
per `GeneratePackage` and threaded through `genExpr`/`genStmt`/
`resolveTypeExpr`/`lookupFunc*`/`genCall`/`genFunc`/`NewFunc`/etc. `InitModule`
becomes `NewGenCtx`.

---

## Increments (each green & cherry-pickable; interpreter-embeddability first)

> Per the repo landing discipline: each increment lands independently,
> keeps every conformance mode green, and is small enough to cherry-pick.
> Increment 5 **must** sub-split (5a–5d) to stay landable.

1. **Loader: `loadingStack` → `@Loader` field. (S, ~½ day)** Fixes the
   confirmed sequential circular-import false-positive. Lowest risk.
   **LANDED** binate `bd18a73e` — unexported `@Loader` field +
   `isLoading`/`pushLoading`/`popLoading` as methods; reentrancy unit
   test `TestLoaderLoadingStackIsolation`.
2. **VM-lowering: 9 globals → `@VM` (+ optional `@LowerCtx`). (M, ~2–3 days)**
   Closes the interpreter-unique corruption; deletes the test-file manual
   resets. After this, two sequential VM sessions are isolated.
   **LANDED** binate `b1b19ce1` — all 9 on `@VM` (no `@LowerCtx` needed;
   `vm` was already threaded through the lowering helpers), per-instance
   isolation unit tests (`TestGlobalAddrPerInstanceIsolation`,
   `TestVtableInjectPerInstanceIsolation`). Verified: full
   `builder-comp-int` conformance 1457/0, int-int smoke over the touched
   paths 8/0.
3. **Types ambient pointers → `@Checker`. (S–M, ~1–2 days)** Removes the
   package-scope hazard; preps 4–5.
   **LANDED** binate `dd4b71e0` — did BOTH parts (user chose "both now"):
   Part A `currentPkgShort`/`currentPkgPath` → `@Checker` fields
   `curPkgShort`/`curPkgPath` (all 7 readers already had `c`); Part B
   eliminated `currentChecker` by threading `@Checker` through
   `AssignableTo` + its two interface-value helpers. Part B turned out
   bounded (not L): all 19 `AssignableTo` callers were in-checker (no
   cascade) and the 59 standalone unit-test calls pass `nil` (= today's
   `currentChecker == nil`). `WithChecker`/`RestoreChecker`/`WithPkgShort`/
   `RestorePkgShort` removed. Net −71 lines; behavior-preserving. Verified:
   full `builder-comp` 1474/0, gen2 iface smoke 6/0, types units green.
   **Group C is now fully eliminated — `pkg/binate/types` has no per-run
   ambient globals.**
4. **IR-gen checker threading: kill `ir.SetChecker`/`ir.currentChecker`.
   (M, ~2–3 days)** Isolated API-surface churn (`cmd/bnc`/`cmd/bni`/REPL/
   codegen tests in lockstep).
   **LANDED** binate `3ef73b24`. Carrier (user choice): a `Checker` field on
   `@Module` (set by the gen drivers right after `InitModule`, before import
   registration) + `@GenContext.Checker` (copied at each `make(GenContext)`);
   module-level/leaf helpers take a `@types.Checker` param. Bigger than the
   "M" estimate — 44 files (ripples through exported IR-gen API:
   `GeneratePackage`/`GenModule`/`RegisterSelfTypes`/`GenSyntheticFunc`),
   net-zero logic. **Subtlety:** `m.Checker` must be set *before*
   `registerPkgImports` — import registration emits imported pkgs' `_Package`
   accessors via `reflectPackageStructType(m.Checker)`; the old global was set
   up front, so setting it only inside `GeneratePackage` was too late (caught
   via a `_Package` link error in the vm/repl unit build). Verified:
   builder-comp 1482/0, builder-comp-int 1467/0, gen2 self-host smoke 8/0.
   **Adversarial review follow-up (binate `95b3592b`):** the review found one
   more instance of the same ordering bug — the cmd/bnc *test runner* called
   `registerTestRunnerImports` (a 3rd import-registration entry the original
   fix missed) before setting `mainMod.Checker`, silently skipping imported
   `_Package` accessors. Fixed + added `TestImportPackageAccessorRequiresChecker`
   pinning the BEFORE/AFTER contract, + refreshed 4 stale `currentChecker`/
   `SetChecker` comments. (Two-independent-checker test → inc 5's 5a;
   end-to-end two-session test → inc 5's 5d.)
5. **IR-gen bulk: registries → `@Module`, transient context → `@GenCtx`.
   (XL)** Detailed scope below (2026-06-17).

**Deferred (eyes open):**

6. **(cross-target) `types.target` → `@Checker`, layout fns
   target-parameterized. (M)** Touches the predeclared-singleton init path
   (group D), so gated on the cross-target decision. Only if embedders need
   *different* targets in one process.
7. **(AOT compiler) `codegen` `@EmitContext` + native `@EmitterContext`.
   (L / M)** ~100+ codegen call sites; native is single-entry (`EmitObject`)
   so cheaper. Only if a *compiler* embedder is wanted.

---

## Increment 5 — detailed sub-split (scoped 2026-06-17)

**The last v1 piece.** 28 ir-gen package-globals remain. Sizing: they're
touched by a *bounded* set of functions — **3–29 distinct functions each,
~50–70 unioned** — concentrated in registry helpers (`lookupConst`,
`lookupStructIdx`, `generateDtors`, `ensureInstantiated`, the `Iface*`
queries), NOT the ~800 raw token sites. So the cost is threading a carrier
through ~50–70 functions + callers (≈3–4× inc 4): XL but tractable, same
shape as inc 4 (which built the `@Module`/`@GenContext` threading scaffold
this reuses).

**Carrier** (per the v1 `@GenCtx`/`@Module` split decision):
- New **`@GenCtx`** (one per `GeneratePackage`/`GenModule`/REPL session):
  transient gen context — `Mod @Module` (replaces `currentModule`),
  `PkgPath` (`currentModulePkgPath`), the import-alias map, the generic-
  instantiation registries, the counters.
- **`@Module` fields** for module-content registries: `Consts`, `Funcs`,
  `Structs`, `Globals`, `TypeAliases`, `Interfaces`, the pending-dtor lists,
  `methodValueWrappers`.
- **Threading** reuses inc 4's scaffold: add `Gc @GenCtx` to `@GenContext`
  (set at `make(GenContext)`, like `.Checker`). Per-function gen reads
  `ctx.Gc.X` / `ctx.Gc.Mod.X`; module-level functions take a `@GenCtx`/
  `@Module` param.

**Classification of the 28** (read/write counts in parens):
- → `@Module` content fields: moduleConsts(125), moduleStructs(95),
  moduleFuncs(69), moduleInterfaces(54 ⚠️), moduleGlobals(47),
  moduleTypeAliases(33), pendingStructDtors(25)/pendingMsDtors(25) (+Names),
  methodValueWrappers(7).
- → `@GenCtx` transient fields: currentModule(18, →`Gc.Mod`),
  currentModulePkgPath(76), currentImportAlias(47), importAliasNames(16)/
  Paths(12), genericDecls(17)/Pkgs + genericType/IfaceDecls(+Pkgs),
  currentTypeParamNames(15)/Types(14), emittedInstantiations(12),
  anonStructCounter(5), funcLitCounter(11).
- Leave / decide-at-end: `verifyIR` (a `SetVerifyIR` debug/CI process-global,
  3 fns) — like `debug.verbose`; move to `@GenCtx` or leave.

**Sub-steps (each green, cherry-pickable):**

  5a splits into 5a-1 / 5a-2 — implementation found `currentModulePkgPath`'s
  blast radius is much larger than `currentModule`'s: it is read by
  `qualifyForCurrentModule` *and* `NewFunc`/`NewExternFunc`, and
  `qualifyForCurrentModule` is itself called inside the hot `@Block` emit
  methods (`EmitCall`/`EmitFuncValue`/`EmitFuncValueWithData`/`EmitFuncHandle`),
  whose hundreds of call sites would explode a naive "thread `gc` everywhere"
  diff. So the two globals land separately.

- **5a-1 — `@GenCtx{Mod}` scaffold + `currentModule`→`gc.Mod`. LANDED binate
  `4f203611`.** Defines `@GenCtx` (transient per-compilation context) +
  `@GenContext.Gc` back-ref; `NewGenCtx(m)` is created internally by
  `GeneratePackage`/`GenModule` and the REPL's `GenDecl`/`GenSyntheticFunc`, so
  **no public-API / driver / REPL signature changes** — the whole change is
  internal to `pkg/binate/ir`. `genFunc`/`genMethod`/`genFuncWithPrependedParams`/
  `ensureInstantiated`/`synthMethodValueWrapper`/`registerCurrentModulePackageAccessor`
  take `gc`; context-light helpers (`genFuncLit`, `genCallInstantiate`,
  `wrapAsIfaceValue`) read `ctx.Gc.Mod`. `setCurrentModule`/`resetImplState`
  removed; `resetFuncLitState` dropped its module-capture. Added
  `TestGenCtxPerCompilationModuleIsolation` (two compilations keep their lifted
  func-lits + Checkers separate). Behavior-preserving (`gc.Mod` is the same
  `@Module` the global held). Verified: gen1 builds (BUILDER-compilable),
  554 `ir` tests + `vm`/`codegen`/`repl` smoke green, hygiene 14/14. **M–L.**
  5a-2 itself splits into 5a-2a / 5a-2b — recon found `resolveTypeExpr` (the
  central, **recursive** type-expr→`Type` resolver) is called from ~60 sites
  across ~26 functions, most lacking `ctx`/`gc` (`evalConstExpr`, `methodSig`,
  `collectInterfaceFromDecl`, `ensureInstantiated{Struct,Interface}`,
  `register*Import*`, `typeDeclEntryType`, …); since it calls `lookupStructIdx`
  (which qualifies with the path), eliminating the global is an XL recursive
  threading pass through it + the `lookup*` family. So the carrier introduction
  (decoupling the emit/init sites that already hold a `@Func`/`@Module`) lands
  first, then the XL `gc`-threading pass.

- **5a-2a — carrier fields + retire global from emit/init sites. LANDED binate
  `820ea94d`.** `@Func.ModulePkgPath` (cached at `NewFunc`/`NewExternFunc` from
  the global) so the four `@Block` emit methods (`EmitCall`/`EmitFuncValue`/
  `EmitFuncValueWithData`/`EmitFuncHandle`) qualify via `b.Func.ModulePkgPath` —
  **zero call-site ripple** to the hundreds of emit sites; `@Module.PkgPath`
  (set by `GeneratePackage`/`GenModule`) so the `@Module` init helpers
  (`EmitInitDispatcher`/`EmitMainEntry`/`HasPackageInit`) qualify via `m.PkgPath`
  (also more correct — can't drift like the ambient global). `qualifyForPkgPath`
  is the package-path-parameterized core. Behavior-preserving; `NewFunc` still
  reads the global for `f.Name`. Verified: gen1 builds, `ir`+`vm`+`codegen`+
  `repl` units green, hygiene 14/14, **builder-comp 1490/0 + builder-comp-int
  1475/0**. **M.**
- **5a-2b — thread `gc.PkgPath` through `resolveTypeExpr` + the `lookup*`
  family + ~26 callers; `NewFunc`/`NewExternFunc` take `pkgPath`; delete
  `currentModulePkgPath`. LANDED binate `3fd61d56`.** Added a `PkgPath` field
  to `@GenCtx` (phase-scoped, NOT always `gc.Mod.PkgPath`: GeneratePackage/
  GenModule seed from the module path, RegisterSelfTypes from the .bni path,
  the import pre-passes leave it empty) — each entry point creates its own gc
  internally, so **no driver/REPL API change**. The dtor/copy/thunk emitters
  thread the module path so their bodies' bare `dtorNameForType` callees still
  qualify. `NewFunc`/`NewExternFunc`'s only non-test callers are in `ir`; the
  `ir.NewFunc` callers in `codegen`/`vm`/`native` are test-only (pass `""`).
  Behavior-preserving (gc.PkgPath == the global's value at every site).
  Surfaced + fixed 4 test failures (lookups that must match a *qualified*
  registration needed a real `gc.PkgPath`). 89 files. Verified: gen1 builds,
  `ir`+`vm`+`codegen`+`repl`+`native` units 8/0, hygiene 14/14, **builder-comp
  1492/0 + builder-comp-int 1477/0**. **XL.**
  **`pkg/binate/ir` now has no per-run module/path package-global** —
  `currentModule` (5a-1) and `currentModulePkgPath` are both gone; only the
  registries (5b) + transient context (5c) + interfaces/dtors (5d) remain.
- **5b — module-content registries → `@Module`. LANDED binate `8a4ddb6a`.**
  moduleConsts/Structs/Funcs/Globals/TypeAliases → `@Module.Consts`/`Structs`/
  `FuncSigs`/`GlobalVars`/`TypeAliases` (distinct from the IR-output Funcs/
  Globals/Types). Element types (ModuleConst/FuncSig/ModuleStruct/ModuleGlobal/
  ModuleTypeAlias) moved to `ir.bni` (now field types of the exported
  `@Module`). Done as ONE landing (did not split 5b1/5b2 — the registries are
  co-registered in shared functions, so a split would double-edit them).
  **Bigger than the "cheap field migration" the status para implied:** the
  cross-package pre-passes (`RegisterStructTypes`, `RegisterAllInterfaces`,
  `RegisterSelfTypes`) had no `@Module` in hand but WRITE the now-per-module
  tables (and their `resolveTypeExpr` reads `gc.Mod.Structs`, which nil-derefs
  on a bare `NewGenCtx(nil)`), so all three gained a `mod @Module` param and
  their ~12 call sites across `cmd/bnc`/`cmd/bni`/`repl` thread it through
  (`registerAllStructTypes` gained a `mod` param). `InitModule` no longer
  clears the five tables (a fresh module is born empty). No qualification
  logic changed — only storage moved — so the 5a-2b qualified-lookup failure
  class can't recur; test breakage was all nil-`Mod` derefs from
  `NewGenCtx(nil)`/bare `make(GenContext)` reaching newly-per-module reads.
  Landing hit a substantial rebase conflict with the concurrent file-scoped-
  imports change (`cf0d1cad`, which added a `files @[]@ast.File` 4th param to
  `GeneratePackage`); resolved by keeping both sides, re-verified, re-approved.
  Verified: gen1 self-hosts; ir+codegen+vm+native+native/common+repl units;
  hygiene 14/14; conformance builder-comp 1498/0, builder-comp-int 1483/0.
  **Adversarially reviewed** (4-lens workflow: correctness / completeness /
  coverage / API+BUILDER-compat) — found 5a+5b correct and complete (no code
  defect; acyclic refcount graph; no external field-aliasing; BUILDER-compat
  preserved). One real test-coverage gap + cosmetic debt fixed in a review
  follow-up (binate `f1b58f90`): `TestGenCtxRegistryIsolation` (two modules'
  five tables are independent — pins what cmd/bnc's multi-package loop
  relies on now that InitModule no longer clears a shared global),
  `TestRegisterStructTypesPopulatesImportingModule`,
  `TestNewFuncCachesModulePkgPath`; dropped the dead `if gc.Mod != nil`
  guards in genFuncLit/synthMethodValueWrapper; swept stale
  currentModulePkgPath/moduleX comments incl. NewFunc's doc.
- **5c — transient context → `@GenCtx`.** import-alias map (incl. the REPL's
  `Save/RestoreAliasMapState` — move intact), generic registries + type-param
  bindings, the two counters. **L** (scoped 2026-06-18; user chose the
  per-compilation-`@GenCtx` route — option A below).
  - **Recon finding (settles the home).** `@GenCtx` is currently created
    fresh per entry-point call (11 `NewGenCtx` sites) and its `PkgPath` is
    phase-scoped (5a-2b). Most "transient" globals are written in one pass
    and read in another: the import-alias map (`importAliasNames/Paths`) is
    written by `RecordImportPath` in `RegisterImports`/`RegisterImport` and
    read by `resolveImportPkg` during `GeneratePackage` body-gen (and the
    REPL mutates+snapshots it across prompts); `genericDecls`/`TypeDecls`/
    `IfaceDecls`(+`Pkgs`) are written in `RegisterImport` AND `GeneratePackage`
    and read at instantiation; `anonStructCounter`/`funcLitCounter`/
    `emittedInstantiations` accumulate across the whole compilation. Only
    `currentImportAlias` + `currentTypeParamNames/Types` are truly per-pass
    (set+restored within one `gc`). So a per-CALL `@GenCtx` can't hold the
    per-compilation state.
  - **Decision: option A — make `@GenCtx` per-COMPILATION (thread one gc).**
    The caller (cmd/bnc, cmd/bni, repl) creates one `gc` per compilation and
    threads it through `RegisterImports`/`RegisterImport`/`RegisterStructTypes`/
    `RegisterAllInterfaces`/`RegisterSelfTypes`/`GeneratePackage` (and the REPL
    keeps a persistent `s.MainGc` for `GenDecl`/`GenConstMember`/
    `GenSyntheticFunc`). All 14 globals → `@GenCtx` fields; `@Module` stays
    output-focused. Matches the documented "per-compilation" intent of
    `@GenCtx` and keeps gen-scratch off `@Module`.
  - **Sub-split (each green + landable):**
    - **5c-1 — LANDED (main `2f67127c`, 2026-06-18).** Made
      `@GenCtx` per-compilation — exported `GenCtx` + `NewGenCtx` from `ir.bni`;
      changed the 9 entry points to take `gc @GenCtx` instead of `mod @Module`
      (use `gc.Mod` internally; `GenModule` stays self-contained); threaded one
      gc per compilation through cmd/bnc, cmd/bni, repl (per-package in the
      build loops; persistent `replSession.MainGc` for the prompt path).
      Globals UNCHANGED → behavior-neutral plumbing. Per-phase `PkgPath`:
      registration passes leave it empty (since `m.PkgPath` is unset
      pre-`GeneratePackage`); `RegisterSelfTypes` sets the .bni path with an
      explicit save/restore (the shared gc must not retain it); `GeneratePackage`
      sets `gc.PkgPath = m.PkgPath` after resolving the module path. Verified:
      gen1 self-host build, units (ir 558 / repl 65 / vm 186 / codegen 236 /
      native×3 / loader 53), hygiene 14/14, conformance LLVM 1501/0 + VM 1486/0.
      (One test-fixture gap surfaced + fixed: `setupReplState` built a session
      without `MainGc`, which `GenDecl(d, s.MainGc)` nil-deref'd; now threads
      one gc like production.)
    - **5c-2**: move the 14 globals → `@GenCtx` fields.  Split by independent
      state-group (each green + landable); the alias-map group turned out far
      larger than the others (see the cascade note below).
      - **5c-2c — LANDED (main `8e2c3259`, 2026-06-19).** Counters
        `AnonStructCounter` + `FuncLitCounter`.  All 6 access sites already had
        gc / ctx.Gc (resolveTypeExpr, genFuncLit, genMethodValue);
        resetFuncLitState takes gc; InitModule's anonStructCounter reset
        dropped (fresh NewGenCtx zero-inits).  Verified gen1 + ir/repl/vm units
        + hygiene + conformance LLVM 1502/0 + VM 1487/0 (with 5c-2b).
      - **5c-2b — LANDED (main `69e70593`, 2026-06-19).** Generic registries
        (`GenericDecls`/`Pkgs` ×3), `EmittedInstantiations`, and the type-param
        substitution ctx `CurrentTypeParamNames`/`Types`.  Threaded gc into the
        4 read helpers (`instantiationAlreadyEmitted`,
        `lookupGenericDeclPkg`/`TypeDeclPkg`/`IfaceDeclPkg`) + their callers
        (genCall via ctx.Gc).  Test: `genFromSourcePkgGc` exposes the gen gc so
        registry-inspecting tests read the same context the gen wrote into
        (the test-rework note below, realized for this group).
      - **5c-2a — alias map (`AliasNames`/`Paths` + `CurrentImportAlias` +
        `Save/RestoreAliasMapState`).  NOT a quick "move intact" — RESHAPED &
        MERGED WITH 5d.**  Recon (2026-06-19) found `resolveImportPkg` (reads
        the alias map) is called by **`buildQualName`** (31 sites) and the
        **interface-dispatch helper chain** `lookupModuleInterfaceIndex` (14) →
        `resolveModuleInterface` (9) → `canonicalIfacePkg`/`Name`, which ALSO
        read `moduleInterfaces` (5d's global, 51 refs).  So de-globalizing the
        alias map threads gc through the same ~100+-site chain 5d needs.
        **Decision (user, 2026-06-19): "plumb once, move both"** — one
        behavior-neutral commit threads gc through the shared chain
        (`buildQualName`, `resolveImportPkg`, `lookupModuleInterfaceIndex`,
        `resolveModuleInterface`, `canonicalIface*`, push/pop/Save/Restore/
        Record + the REPL ripple via the exported API), then move the alias map
        AND `moduleInterfaces` onto carriers on that foundation.  Folded into 5d
        below (needs its own recon + scoping before starting).
      - **Test-rework note (general):** the ir/repl/vm unit tests pervasively
        pass a *fresh* `NewGenCtx(m)` inline at each entry-point call AND each
        `lookup*` (they worked only because the state was global).  Each group's
        move must thread ONE shared gc across any register-then-lookup test
        sequence (done for 5c-2b via `genFromSourcePkgGc`); the alias-map move
        will need the same for its tests.
- **5d (now also subsumes 5c-2a's alias map) — alias map + `moduleInterfaces`
  + pending-dtors + `methodValueWrappers` → carriers; delete the globals;
  `verifyIR` decision; land the end-to-end two-session reentrancy test.**
  **Carrier decided (user, 2026-06-19): both the alias map AND
  `moduleInterfaces` go on `@Module`, threading `@Module` (NOT `gc`) through
  the shared chain.**  Recon settled it: `resolveImportPkg` (alias map) is on
  the BACKEND call path — `Iface*` exported queries → `resolveModuleInterface`
  → `lookupModuleInterfaceIndex` → `resolveImportPkg` — and the VM / codegen /
  native backends hold `@Module`, not `gc` (at backend time the pkg args are
  full paths so the resolve is a no-op, but the call still happens).  Gen-time
  callers reach `@Module` via `gc.Mod` / `ctx.Gc.Mod`; `moduleInterfaces` is
  per-package (cleared each `InitModule`) and consumed by `vm.LowerModule(mod)`
  within that package's scope.  So `@Module` is the coherent home for both;
  threading `gc` would force the backends to synthesize a gc.  (This overrides
  the earlier "@GenCtx for the alias map" note above.)
  - **5d-1 — plumb-once (behavior-neutral).** Add `m @Module` to the whole
    chain: `buildQualName`, `resolveImportPkg`, `lookupModuleInterfaceIndex`,
    `resolveModuleInterface`, `canonicalIfacePkg`/`Name`, `findInterfaceMethod*`,
    the `Iface*` exported queries (`IfaceFullVtableSize`/`IfaceParentPkgs`/
    `IfaceParentNames`/`IfaceOwnMethodNames` + ancestor/`appendAncestors`/
    `parentSlotOffsetFromBase`), `pushFileImports`/`popFileImports`/
    `SaveAliasMapState`/`RestoreAliasMapState`/`RecordImportPath`,
    `overlayFileImports`.  Update ALL callers (gen → `gc.Mod`/`ctx.Gc.Mod`/`m`;
    backends → `mod`; FIX `codegen/emit_impls.bn:vtableSlotCountForInfo` which
    lacks `mod` — thread it from its caller).  Globals untouched → identical
    behavior.  ~150 sites, ~18 files (ir + ir.bni + vm + codegen + native/x64 +
    native/aarch64 + repl).  `LookupVtableSlotName` already takes `@Module`.
    - **5d-1a — LANDED (main `1f761d11`, 2026-06-19).** Threaded `m @Module`
      through the 17 interface-chain readers + the 3 deeper-cascade funcs
      (`makeOwnMethodsImplInfo`, `ifaceValueTypesAgree`, `vtableSlotCountForInfo`);
      globals untouched (`m` unused until 5d-2).  Two corrections to the map
      below: (1) **9** ir.bni exports, not 8 — `EmitIfaceUpcast` (a `@Block`
      method, the sole remaining `IfaceParentSlotOffset` caller after the native
      precompute) also took `m`, threaded from its two gen callers via
      `ctx.Gc.Mod`.  (2) The "`vtableSlotCountForInfo`'s callers all have `m`"
      claim was WRONG: the LLVM `emitInstr` dispatcher has no module but sizes an
      OP_IFACE_VALUE vtable via `vtableSlotCount`→`vtableSlotCountForInfo`→
      `ir.IfaceFullVtableSize`, so `m` threads `emit.bn`→`emitFuncDbg`→
      `emitInstr`→`vtableSlotCount` (codegen must know which module it emits;
      behavior-neutral).  28 files, +171/−162.  Verified: gen1 self-host; units
      ir 558 / codegen 236 / vm 186 / native x64 228 · common 133 · aarch64 136 /
      repl 65, 0 failed; hygiene 14/14; conformance iface smoke 14/14 in
      builder-comp + builder-comp-int.  Next: 5d-1b (alias chain).
    - **5d-1b — LANDED (main `a76cc579`, 2026-06-19).** Threaded `m @Module`
      through the 8 alias-map functions (`resolveImportPkg`, `buildQualName`,
      `pushFileImports`, `popFileImports`, `SaveAliasMapState`,
      `RestoreAliasMapState`, `RecordImportPath`, `overlayFileImports`) + their
      77 ir call sites (gen → `gc.Mod`/`ctx.Gc.Mod`; the 8 funcs' internal
      cross-calls → `m`).  Flat cascade — every caller already held `gc`/`ctx`/`m`
      (no deeper threading); fanned the 77 edits across a 16-agent workflow
      (one per ir file, disjoint), verified centrally.  REPL ripple: the 3
      exported funcs (`Save`/`Restore`/`RecordImportPath`) are called only from
      `repl/mid_session_import.bn`, now passing `s.MainMod` (the persistent
      session module whose alias map outlives the per-package `InitModule`
      wipes).  `currentImportAlias` stays a global here (5d-2 moves it; readers
      hold `gc`).  Globals untouched (`m` unused until 5d-2).  ir.bni: 3 exports
      updated.  21 files, +105/−98.  Verified: gen1 self-host; units ir 558 /
      codegen 236 / vm 186 / native x64 228 · common 133 · aarch64 136 / repl 65,
      0 failed; hygiene 14/14; conformance cross-pkg/alias/generic smoke 15/15 in
      builder-comp + builder-comp-int.  Next: 5d-2 (move alias map +
      `moduleInterfaces` + `currentImportAlias` onto `@Module`; the reentrancy gain).
    - **Cascade map (build-fix recon 2026-06-19; def-changes attempted then
      reverted to keep the tree clean — redo from this map).**  Two coupled
      sub-cascades (decoupled by the 5d-1a interface-chain / 5d-1b alias-chain
      split: 5d-1a does NOT touch `resolveImportPkg`, so the interface chain
      only needs `m` for `moduleInterfaces`):
      - **5d-1a interface-chain reader funcs to thread `m @Module` (17, not 16
        — `IfaceMethodCount` was missed first pass):** `findInterfaceMethod`,
        `findInterfaceMethodFromBase`, `resolveModuleInterface`,
        `canonicalIfaceName`, `canonicalIfacePkg`, `lookupModuleInterfaceIndex`,
        `lookupModuleInterface`, `IfaceMethodCount`, `IfaceOwnMethodNames`,
        `IfaceParentPkgs`, `IfaceParentNames`, `IfaceFullVtableSize`,
        `IfaceAncestorClosure`, `IfaceAncestorClosurePkgs`, `appendAncestors`,
        `IfaceParentSlotOffset`, `parentSlotOffsetFromBase`.  ir.bni exports to
        update: `IfaceMethodCount`, `IfaceOwnMethodNames`, `IfaceParentPkgs`,
        `IfaceParentNames`, `IfaceFullVtableSize`, `IfaceAncestorClosure`,
        `IfaceAncestorClosurePkgs`, `IfaceParentSlotOffset` (8).  Threading the
        17 defs surfaced **76 caller errors** via the gen1 build (which covers
        ir + codegen + native, NOT vm/repl — those need separate builds to
        surface their callers).  Per-context arg: gen funcs → `gc.Mod` (gen_iface,
        gen_impl, gen_generic, gen_module:RegisterAllInterfaces) / `ctx.Gc.Mod`
        (gen_iface_dispatch:99) / `m` (internal cross-calls among the 17 in
        gen_iface_extends, gen_iface_registry, gen_iface_dispatch, gen_iface_vtable);
        backends → `mod` (codegen/emit_impls, emit_funcvals_dtor, emit_iface_upcast,
        native/x64 x64_iface+x64_funcvalue+x64_dispatch, native/aarch64 likewise).
      - **Deeper-cascade funcs that lack any module param (thread `m`, then fix
        THEIR callers):** `makeOwnMethodsImplInfo` (gen_impl; 1 caller
        `collectImplsFromDecl`→`gc.Mod`); `ifaceValueTypesAgree` (gen_type_resolve;
        callers gen_stmt.bn:323, gen_util.bn:219 + 3 in gen_type_resolve_test.bn);
        `vtableSlotCountForInfo` (codegen/emit_impls; 5 callers in that file,
        which have `m`); `collectImportedImplsFromDecl` already has `mod`.
      - **NATIVE WRINKLE — RESOLVED (prep landed: main `4ee0a07a`, 2026-06-19,
        option (b)).** `EmitIfaceUpcast` now precomputes the parent-slot offset
        (`@Instr.IfaceUpcastSlotOffset`) at IR-gen time; the three compiled
        backends read the field, so `IfaceParentSlotOffset` is no longer called
        from any backend — it's now invoked ONLY from `EmitIfaceUpcast` (inside
        ir, which has the registry).  Net for 5d-1a: drop `IfaceParentSlotOffset`
        and `parentSlotOffsetFromBase` from the BACKEND-threading list — they
        stay internal to ir and thread normally with the chain via `gc.Mod`.
        The native `emitInstr` hot-path no longer touches the registry, so no
        `@Module` threading through the native instruction-dispatch chain.
        (codegen's `vtableSlotCountForInfo` was always milder — its 5 callers
        are all in emit_impls.bn which has `m`.)
  - **5d-2 — move both states.** Split into 5d-2a (alias map) + 5d-2b
    (interfaces) to keep each behavior-changing landing small and isolate the
    `moduleInterfaces` lifetime risk.
    - **5d-2a — LANDED (main `098a8504`, 2026-06-19).** Moved the import-alias
      map (`ImportAliasNames`/`ImportAliasPaths`) + `CurrentImportAlias` off the
      package-globals onto `@Module` fields; flipped the 8 alias-map funcs to
      `m.ImportAlias*` and the ~20 `currentImportAlias` sites to
      `gc.Mod`/`ctx.Gc.Mod.CurrentImportAlias` (all callers held `gc`/`ctx` from
      5d-1b); dropped `InitModule`'s alias reset (a fresh `NewModule` is born
      empty); deleted the 3 globals.  Behavior-preserving single-session.  14
      files, +108/−107.  Verified: gen1 self-host; units ir 558 / codegen 236 /
      vm 186 / native x64 228 · common 133 · aarch64 136 / repl 65, 0 failed;
      hygiene 14/14 (15 post-rebase); conformance cross-pkg/alias/generic 15/15
      in builder-comp + builder-comp-int.  **Finding:** with the global gone, the
      REPL `mid_session_import.bn` `Save`/`RestoreAliasMapState` bracket is now
      vestigial (it snapshots `s.MainMod`'s OWN map around a loop that builds
      separate per-package modules — a no-op; `RecordImportPath(s.MainMod,…)` is
      what matters).  Kept as-is in 5d-2a (only the stale "InitModule wipes the
      global" comments corrected); **removal folded into 5d-2b** (user, 2026-06-19).
    - **5d-2b — TODO.** Move `moduleInterfaces` → `@Module.Interfaces`: move the
      `ModuleInterface` struct to `ir.bni` (field of exported `@Module`, like 5b
      did for ModuleConst/etc.); flip the ~47 refs (readers have `m` from 5d-1a;
      writers `collectInterfaceFromDecl`/`ensureInstantiatedInterface`/
      `collectInterfaceParents` have `gc`, `registerUniverseAny` needs `m`); seed
      universe `any` per-module by calling `registerUniverseAny(m)` from
      `NewModule` (so every module is born with `any`); drop `InitModule`'s
      `moduleInterfaces` reset + `registerUniverseAny()` call; delete the global.
      Lifetime: each `@Module.Interfaces` accumulates its imports' + own
      interfaces during that package's compile; the VM lowers module M via M's
      `@Module` (passes `m`), so it sees M's set — per-package isolation becomes
      automatic.  ALSO remove the now-vestigial REPL Save/Restore bracket (keep
      only `RecordImportPath`).  The reentrancy gain for interfaces.
  - **5d-3 — remainder.** pending-dtors (`pendingMsDtors`/`StructDtors`+`Names`)
    + `methodValueWrappers` → `@Module`; delete remaining globals; `verifyIR`
    decision; land the end-to-end two-session reentrancy test.
  - **Review follow-ups (adversarial review 2026-06-19; fold into 5d):**
    (a) The 5c-2c counter move was a latent-bug FIX in the REPL, not strictly
    neutral: with the old globals a mid-session `import` reset the shared
    `anonStructCounter`/`funcLitCounter`, so a later prompt's anon-struct /
    funclit could re-issue `__anon_0` / `main.__funclit_0` and collide in the
    persistent `s.MainMod`; per-`s.MainGc` counters can't.  Add a REPL
    regression test (mid-session import, then a prompt using a funclit or anon
    struct — assert no duplicate name) — fits the 5d REPL alias-path rework.
    (b) `resetFuncLitState` still wipes the package-global `methodValueWrappers`
    on every `GeneratePackage` incl. a mid-session import's `pkgGc` — clobbers
    session-synthesized wrappers; resolved when 5d-3 moves `methodValueWrappers`
    onto `@Module` (then `resetFuncLitState`'s doc-comment becomes fully true).

**Wrinkles / risks (flagged):**
1. **`moduleInterfaces` is cross-package & lifetime-subtle (the main risk).**
   vm's `lowerImplVtables` queries it by `(pkg,name)` via exported
   `ir.IfaceFullVtableSize`/`IfaceParentPkgs`/`IfaceParentNames`/
   `IfaceOwnMethodNames`/`LookupVtableSlotName` (verified — vm/lower.bn:263-305),
   *after* all modules are gen'd; yet `InitModule` resets it per-module, so it
   behaves as a **per-compilation accumulating registry** (imports register
   into it too), not clean per-`@Module` content. Migrating it means deciding
   its true home (likely a compilation-level registry on `@GenCtx`, not
   `@Module`) and threading the carrier through the exported `Iface*` queries
   → **rippling into vm's call sites** (the only part of inc 5 that escapes
   `pkg/binate/ir`). **Do a focused mini-recon at the start of 5d before
   committing to a home.**
2. **REPL alias-map save/restore** (`Save/RestoreAliasMapState`, used by
   `evalReplImport`) must move onto `@GenCtx` intact.
3. **BUILDER constraint** — `pkg/ir` is in `cmd/bnc`'s tree; `@GenCtx` +
   threading must stay BUILDER-compilable (structs/fields/params only — fine).
4. **`genFromSourcePkg`'s manual global-reset** (ir_test.bn) disappears once
   the registries are per-`@Module`.

**Open question (defer to 5d mini-recon):** `moduleInterfaces`' home —
`@Module` (and thread it through the exported `Iface*` queries into vm) vs a
compilation-level registry on `@GenCtx`.

---

## Risks

- **BUILDER constraint (hard gate, increments 3–6).** `pkg/{ir,types,loader}`
  are in `cmd/bnc`'s BUILDER-compiled tree. The threading refactor must stay
  within what the pinned BUILDER accepts (no new syntax/features, no
  closures, etc.) or the gen1 build breaks. Verify any new construct against
  the current BUILDER before adopting it.
- **Increment-5 call-site churn.** Read counts in the dozens-to-~90
  (`currentModule` ~88, `currentModulePkgPath` ~66, `moduleConsts` ~85);
  every IR-gen helper grows a `@GenCtx`/`@Checker` param. High rebase-conflict
  exposure against "stay close to main" — the 5a–5d sub-split is what keeps
  each landing green and small. **Do not attempt 5 as one commit.**
- **VM lifetime-tier mis-assignment.** `globalNames`/`globalAddrs` +
  `vtableInj*` are *meant* to accumulate across modules within one VM
  session — they must land on the long-lived `VM`, not a per-`LowerModule`
  context, or cross-module global resolution breaks. Conversely `curNames`
  is per-function and must reset per `lowerFunc`.
- **`SetChecker`/`SetVerifyIR` API removal (increment 4).** Called from
  `cmd/bnc`, `cmd/bni`, REPL, and codegen tests; all change in lockstep.
  Preserve/migrate the nil-checker `genExpr` fallback.
- **Singleton refcount/leak risk — only if D goes per-instance (increment
  6).** The predeclared `@Type`s are refcounted and currently "live forever"
  as process globals. Per-context means RefDec at teardown, with pervasive
  references; getting canonical-ref ownership right under the never-leak
  rule is the subtlest part. **Recommendation: keep them shared-immutable
  unless cross-target forces otherwise.**
- **Alias-map save/restore (REPL interaction).** `plan-repl-embeddable.md`
  relies on `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`.
  When the alias map moves onto `@GenCtx` (5c), that bracketing must move
  with it intact, or the REPL's per-import alias state corrupts.

---

## To confirm during implementation (data was uncertain)

- Whether the existing function-local `GenContext` can be extended to carry
  the new per-compilation context, or a separate `@GenCtx` is cleaner — look
  at `GenContext`'s current shape before 5a.
- Exact reset wiring of `methodValueWrappers`
  (`gen_method_value.bn`→`resetMethodValueState`) when it moves.
- Whether `moduleInterfaces` is one global or split across the iface/registry
  files (the inventory named it but it was not in the `gen.bn` var list).

---

## Verification (per increment)

- Unit: `./scripts/unittest/run.sh builder-comp <changed pkgs>` — and per
  the smoke-every-changed-package rule, every package whose files changed
  (ir touches `ir`; types touches `types`; vm touches `vm`).
- BUILDER subset: `scripts/build-bnc.sh` after any `ir`/`types`/`loader`
  change (confirms the threaded code stays BUILDER-compilable).
- Conformance: full default modes green (`builder-comp`, `…-int`,
  `…-int-int`, `…-comp`, `…-comp-int`, `…-comp-comp`); the int modes are the
  ones that exercise the VM-lowering and reentrancy paths.
- **End-to-end two-session reentrancy test — ships with increment 5.** Run a
  full compile→check→ir-gen→vm-lower→execute cycle twice in one process over
  programs that import the same package and define globals/vtables/interfaces;
  assert the second session is not contaminated by the first (the failure mode
  this whole plan exists to fix). It **cannot pass before inc 4/5** (ir-gen +
  `ir.currentChecker` are still shared), which is itself the signal that inc
  1–3 alone don't deliver full-pipeline reentrancy — so it lands together with
  inc 5, not earlier. The per-subsystem isolation already has unit coverage:
  `loader_test.TestLoaderLoadingStackIsolation`,
  `vm/lower_data_test.TestGlobalAddrPerInstanceIsolation`,
  `vm/vtable_inject_test.TestVtableInjectPerInstanceIsolation`.
