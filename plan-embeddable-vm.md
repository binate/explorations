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
- **5c — transient context → `@GenCtx`.** import-alias map (incl. the REPL's
  `Save/RestoreAliasMapState` — move intact), generic registries + type-param
  bindings, the two counters. **M.**
- **5d — `moduleInterfaces` + pending-dtors + `methodValueWrappers` →
  `@Module`; delete the globals; `verifyIR` decision; land the end-to-end
  two-session reentrancy test** (the plan-mandated test — see Verification —
  which couldn't pass until ir state went per-instance). **M** + the
  cross-package piece below.

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
