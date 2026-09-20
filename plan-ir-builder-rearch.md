# Plan: IR-builder re-architecture (split `ir` → `ir` + `irbuild` + `iropt`)

**Status:** IN PROGRESS (claimed 2026-09-19, work-3/session). User-greenlit 2026-09-19.

## Goal

Shrink `pkg/binate/ir.bni` below the 1000-line file-length cap by making `ir`
a **pure IR data-model package**. Today `ir` is an 8,356-line grab-bag: data
model + the whole `Emit*` construction API + every optimizer pass + verifier +
RTTI/data emission. Its public interface `ir.bni` is 1214 lines and cannot drop
below ~1100 without moving the `Emit*` API out — which method-locality forbids
doing as methods, so the API becomes free functions in a new package.

This is the only path below ir's ~1100 floor (see `claude-todo.md` ratchet entry
for the audits establishing that floor).

## Target architecture

Three packages, acyclic: `iropt` → `irbuild` → `ir` (and `irgen` → `irbuild`,
`irgen` → `ir`).

- **`ir`** — pure data model + read-only queries/extraction. The 9 structs
  (`Module`/`Func`/`Block`/`Instr`/`Global`/`Param`/`PhiEntry`/`StringConst`/
  `IRViolation`); the struct constructors (`NewModule`, `AddFunc`, `AddGlobal`,
  `AddTypeDef`, `NewFunc`, `NewExternFunc`, `AddBlock`, `AddFaultPad`,
  `NewParam`, `NewParamRef`) — these manipulate struct fields directly, they do
  NOT allocate instr IDs or append instrs; the read-only queries
  (`ir_iface_query.bn`, `IsTerminator`, `PeelToUnderlying`,
  `ManagedStructLeafEligible`, module-init queries `HasMainFunc`/`IsMainPackage`/
  `HasPackageInit`/`PackageInitName`, `LookupModuleFunc`, `CEntryTargetFunc`,
  `RegisterFuncSig`/`FuncSigIndexLookup`, `RegisterPending*Dtor`,
  `registerUniverseAny`); the RTTI/data **extraction** collectors
  (`data_typeinfo.bn`, `data_satentry.bn`, `data_ifaceid.bn`, `strings.bn` —
  all read-only, 0 emit, consumed by every backend); the read-only analyses
  `loops.bn` (`ComputeLoopDepths`, consumed by `native/common`) ; and the
  read-only verifier `verify.bn`. Keeps its current imports only.
- **`irbuild`** (imports `ir`) — the IR-construction API as **free functions**:
  all 75 `@Block.Emit*` (converted `func (b @Block) EmitX(…)` →
  `func EmitX(b @Block, …)`) + the construction primitives `NewInstr`,
  `MakeArgs1`/`MakeArgs2`, and the newly-public `AllocValueID` (was private
  `nextID`) / `NewVoidInstr` (was private `newVoidInstr`); the free-function
  emit helpers `EmitManagedSliceRefDec`/`EmitManagedPtrRefDec`; and the
  package-private `addInstr` / `nilDedupPreservingOp` / `stripConstForIR`.
- **`iropt`** (imports `ir` + `irbuild`) — everything that transforms or emits:
  all opt passes (`opt.bn`, `inline_*`, `mem2reg*`, `sroa*` [pass parts],
  `load_forward.bn`, `bce_loop.bn`, `ir_phi_elim.bn` [`EliminatePhis`],
  `dom.bn`); the 4 `@Module` module-init **emitters** (`EmitInitDispatcher`,
  `EmitBnInit`, `EmitMainEntry`, `EmitBnEntry`); `emitBuildSatRegistryCall`.

### Why this is acyclic (recon F)

`ir` imports only leaf/support packages (`buf`, `irdata`, `iropcode`, `irsym`,
`irutil`, `mangle`, `stringutils`, `types`, std/stdx). None of them will ever
import `irbuild`/`iropt`, so the `irbuild → ir` edge introduces no cycle. The
data model has **no reverse entanglement** (recon D): no constructor or query
calls any `Emit*` or build helper.

## Decisions (settled; flag any you'd change)

1. **`verify.bn` → `irbuild`** (in Phase 2, with emit; stays in `ir` through
   Phase 1). `verify_test` builds fixtures via emit AND calls the private
   `addInstr`/`newVoidInstr`, so it must live in whichever package owns those —
   `irbuild`. `irgen` imports `irbuild` anyway (for emit), so `VerifyFuncOrAbort`
   coming from `irbuild` adds NO new edge (and avoids `irgen → iropt`).
2. **`dom.bn` → `iropt`** (its only consumers are opt passes; co-locate). Its
   exports (`DomInfo`/`ComputeDom`/`Dominates`/`IteratedDF`) leave `ir.bni`.
3. **`loops.bn` → `iropt`.** Its test builds fixtures via emit (so it cannot
   stay in package `ir` once emit → `irbuild`), and `native/common` — the only
   external consumer of `ComputeLoopDepths` — ALREADY imports `iropt` for
   `EliminatePhis`, so moving `loops` adds no new cross-package edge.
4. **`NewParamRef` stays in `ir`** — a trivial operand constructor (no ID alloc,
   no block append), fits the data model.
5. **Rename on export:** `nextID` → `AllocValueID(f)` (avoids confusion with the
   `Func.NextID` field), `newVoidInstr` → `NewVoidInstr(op)`. Both move to
   `irbuild` and become public (called cross-package by `iropt` passes).
6. **`native/common` + `vm` import `iropt`** (for `EliminatePhis` / `RunOptPasses`
   run at lowering). Acceptable layering — backends legitimately run IR passes
   at lowering time.

## Mixed files that must be SPLIT (not moved wholesale) — recon A

- `ir.bn` (467): constructors → `ir`; the 13 `Emit*` + build helpers → `irbuild`.
- `ir_ops.bn` (447): ~22 `Emit*` → `irbuild`; `PeelToUnderlying`, `IsTerminator` → `ir`.
- `ir_util_shared.bn` (286): `EmitManaged*RefDec` → `irbuild`;
  `registerUniverseAny`, `LookupModuleFunc`, `RegisterPending*Dtor` → `ir`.
- `module_init_emit.bn` (264): 4 `@Module` emitters → `iropt`; 4 queries → `ir`.
- `data_satregistry.bn` (70): `emitBuildSatRegistryCall` → `iropt`;
  `CEntryTargetFunc`, `findFuncNamed` → `ir`.
- `sroa.bn` (418): pass → `iropt`; `ManagedStructLeafEligible` (consumed by
  `irgen`) → `ir`.

## Phasing (each phase independently landable + green)

**Phase 1 — extract `iropt`. ✅ DONE (landed `1acae8ad6`, 2026-09-19).** Full
builder-comp conformance 3040/0/9; adversarial review confirmed zero logic
changes. `ir.bni` 1214 → 1206.

**Phase 1 (original) —** Move the opt passes + their tests + the 4
module-init emitters + `emitBuildSatRegistryCall` + `dom.bn` out of `ir` into
`iropt`, splitting the mixed files above. In this phase `iropt` calls `ir`'s
**public `Emit*` methods** and `ir.NewInstr`/etc. (still methods/primitives in
`ir`), so the edge is just `iropt → ir`. Re-point drivers: `RunOptPasses`
(cmd/bnc ×4, vm/lower), `EliminatePhis` (native/common, vm/lower_func), the
module-init emitters (cmd/bnc ×5, interp ×3). Opt tests (which build fixtures via
emit) move with their code — legal because `iropt` will import `irbuild` in
Phase 2, and in Phase 1 they call `ir`'s public emit methods. `ir.bni` loses the
opt entry points (modest shrink).

**Phase 2 — extract `irbuild`.** With opt already out of `ir`, move the `Emit*`
definitions + construction primitives (`NewInstr`/`MakeArgs`/`AllocValueID`/
`NewVoidInstr`/`addInstr`) from `ir` into `irbuild`, converting emit methods to
free functions. Re-point every emit + primitive call site: `irgen` (925 emit +
`NewInstr`/`MakeArgs`), `iropt` (passes' `NewInstr`/`AllocValueID`/`NewVoidInstr`/
`MakeArgs` + tests' emit), the module-init emitters (now in `iropt`, their block
emit → `irbuild.`). `iropt` gains `import irbuild`. `ir.bni` drops ~330 → ~880,
under the 1000 cap. `ir` is now pure data model + queries + verify + extraction.

## Churn estimate

- Emit call sites to re-point in Phase 2: `irgen` 925, `iropt` (moved-in) ~109,
  module-init callers 8. Mechanical `X.EmitY(` → `irbuild.EmitY(X` transform.
- Phase 1 relocation: ~20 pass files + 17 test files (~5000 lines) requalified
  bare `ir` types → `ir.` (same mechanical requalification as the `irgen`
  extraction already done — reuse `qualify.py`).

## Risks

- **Test entanglement (recon G):** opt tests build fixtures via emit, so they
  cannot stay in package `ir` once emit → `irbuild` (cycle). They MUST move to
  `iropt` with their code (Phase 1). Emit-definition tests move to `irbuild`
  (Phase 2).
- **Requalification correctness:** the `irgen` extraction surfaced qualifier
  pitfalls (local-var/util name collisions like `dtorName`; multi-line sig
  removal). Reuse the hardened `qualify.py` + the wrapped-sig line-based removal.
- **Backend layering:** `native/common`/`vm` → `iropt` (decision 6). If
  undesired, `EliminatePhis` could stay in `ir` (it is a transform, so it would
  then need `ir`-resident construction primitives — pulling primitives back into
  `ir`, which conflicts with Phase 2). Chosen: accept the `iropt` edge.
