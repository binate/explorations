# Plan: Embeddable / reentrant VM — eliminate per-run global state

Status: **IN PROGRESS** (2026-06-16). v1 = increments 1–5 below
(reentrancy-only, single-target, interpreter-only, `@GenCtx`/`@Module`
split). Scope decisions ratified by the user 2026-06-16.
**Landed:** increment 1 (loader `loadingStack` → `@Loader`, binate
`bd18a73e`); increment 2 (vm-lowering 9 globals → `@VM`, binate
`b1b19ce1`); increment 3 (types ambient pointers → `@Checker`, **both**
Part A pkg-context fields and Part B `currentChecker` elimination, binate
`dd4b71e0`).

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
5. **IR-gen bulk: registries → `@Module`, transient context → `@GenCtx`.
   (XL, ~1–2 wk)** Sub-split:
   - **5a** Introduce `@GenCtx`; thread it carrying the existing globals as
     fields behind a shim (no behavior change).
   - **5b** Migrate the Module-content registries onto `@Module`.
   - **5c** Migrate generic-instantiation registries + import-alias maps +
     counters onto `@GenCtx`.
   - **5d** Delete the now-dead globals; `InitModule` → `NewGenCtx`.

**Deferred (eyes open):**

6. **(cross-target) `types.target` → `@Checker`, layout fns
   target-parameterized. (M)** Touches the predeclared-singleton init path
   (group D), so gated on the cross-target decision. Only if embedders need
   *different* targets in one process.
7. **(AOT compiler) `codegen` `@EmitContext` + native `@EmitterContext`.
   (L / M)** ~100+ codegen call sites; native is single-entry (`EmitObject`)
   so cheaper. Only if a *compiler* embedder is wanted.

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
- A new reentrancy regression test (once increment 2 lands): run two VM
  sessions in one process over programs that import the same package and
  define globals/vtables; assert the second is not contaminated by the first
  (the failure mode this whole plan exists to fix).
