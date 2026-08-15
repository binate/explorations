# Plan: interp Inc 3 (typed/aggregate RunFunc) + Inc 4 (build-config + source-provider seam)

Status: **NOT STARTED** (design). Continuation of the archived
`done/plan-embeddable-interp.md` — its Inc 3 + Inc 4. Tracked in `claude-todo.md`
("Embeddable-interp Inc 3 / Inc 4").

## Context

`pkg/binate/interp` is the embeddable whole-program interpreter (`@Interp`: `New` →
`AddBniPath`/`AddImplPath` → `LoadProgram` → `RunMain`/`RunFunc`). Today an embedder can
only *run `main`* and get a bare `int` back — `RunFunc(qual, args @[]int) int` takes integer
args and returns one scalar word. Inc 3 gives an embedder the ability to **call a named
function with typed args and receive typed / aggregate / multi-return results** — the piece a
real host (test harness, wasm worker, tool) needs. Scope chosen: **full aggregate marshaling**
(int/bool/`@[]char`/`@[]@[]char`/struct/slice, both directions, plus multi-return). Inc 4 then
abstracts the two remaining host couplings — the baked-to-host build config and the loader's
direct filesystem reads — behind clean seams (a `SetBuildConfig` setter and a `SourceProvider`
interface, default os-backed; **no** in-memory provider built yet, just the seam).

Marshaling is tractable because **rt is injected as one shared native instance**, so a managed
value is byte-identical across the compiled-host ↔ bytecode-VM boundary (`interp/externs.bn`,
`vm/vm.bn:15-24`). The whole problem is (a) knowing each param/return `@types.Type`, (b) laying
the words out per the VM ABI, and (c) getting refcount ownership right.

## Design overview

**`Value`** — a uniform host-side box for one marshaled value:
`type Value struct { Typ @types.Type; Bytes @[]uint8 }` where `Bytes` is the value's in-memory
image (`Typ.SizeOf()` bytes) and the `Value` **owns** any managed references inside it.
- Constructors for the common shapes: `IntValue(n)`, `BoolValue(b)`, `StringValue(s @[]char)`
  (builds the 4-word `{Data,Len,Refptr,BackingLen}` image over an owned backing by default),
  `StringSliceValue(@[]@[]char)`, and a generic `BytesValue(typ, bytes)` for struct/slice.
- Accessors: `AsInt()`, `AsBool()`, `AsString() @[]char`, `AsStringSlice()`, `Field(i)`.
- `Release()` — walks `Typ` and `rt.RefDec`s every managed reference in `Bytes` (results arrive
  +1-owned; the caller Releases when done). Scalars / borrowed / immortal views: no-op.

**`RunFuncTyped(pkgPath @[]char, funcName @[]char, args @[]Value) (@[]Value, @[]@[]char)`** —
explicit `pkgPath` + bare `funcName` (avoids the VM-short-name ↔ checker-full-path ambiguity;
the VM qual is derived as `lastSegment(pkgPath) + "." + funcName`). Flow:
1. `sig := it.Checker.PackageType(pkgPath, funcName)` → `TYP_FUNC` (`.Params`, `.Results`).
2. Marshal each `args[i]` into VM arg slots per its `Typ` (`vm/lower_slots.argSlots`): a scalar
   is one word; an aggregate (`@[]char`/struct/slice, `IsByvalParam`/`IsAggregateArg`) rides
   **one by-address slot** = the address of the `Value`'s `Bytes` (matches `vm/lower_call.bn`).
3. Call via the new VM aggregate accessor (below).
4. Rebuild `@[]Value` from `.Results`: void → empty; scalar → the returned word; aggregate /
   multi-return → split the retbuf per `@VMFunc.ResultOffsets[i]`/`ResultSizes[i]` into a
   per-result `Value` owning its +1 refs.
5. Run-fault → return it as the error slice (mirror `RunMain`, `interp.bn:222-231`).

Reuse: `types.PackageType` (`types/checker.bn:39`), `types.SizeOf`/`AlignOf`/`FieldOffset`/
`StructLayout` + the `MSLICE_FIELD_*`/`SLICE_FIELD_*` offset accessors + `ManagedHeaderSize`/
`ManagedRefcountOffset` (`types.bni:754-816`), `types.AggregateReturnSize` (`abi_return.bn:188`),
`rt.RefInc`/`rt.RefDec`/`rt.ManagedSlice` (`rt.bni`), and the `os.Args`/`captureEnv` templates
for building a `@[]@[]char` (`startup/args_main.bn:39-98`).

## Stage A — enabling plumbing (VM accessor + checker retention + name→type resolution)

- **Retain the checker.** Add `Checker @types.Checker` to the `Interp` struct (`interp.bn:20-36`);
  `LoadProgram` stores the checker it already builds (`interp.bn:143-145`) instead of dropping it.
- **New VM entry point** in `pkg/binate/vm/` + `pkg/binate/vm.bni` (near `CallFunc`, `vm.bni:979`),
  e.g. `CallFuncAggregate(name *[]readonly char, args @[]int, retbuf *uint8, retbufSize int) int`.
  It is `execFunc` up to the return, then — instead of the size-blind, 64-byte-capped top-level
  relocation (`vm_exec_helpers.bn:49-62`) — copies **exactly `retbufSize` bytes** from the
  aggregate/packed-tuple address into `retbuf`, exactly as `TrampolineAggregate` does for a single
  result (`vm.bn:230-231`) **but generalized to multi-return** (lift the single-result restriction
  at `vm.bn:218-220`; `packMultiReturn` already writes the tuple image and `ResultSizes/Offsets/
  MultiWord` already describe it — `vm_exec_return.bn:24-62`, `lower_func_helpers.bn:12-67`). Scalar
  and void returns keep the existing scalar path (no retbuf). **Defer** iface-value results (they
  need `ResultIfaceVtOffsets` vtable substitution, populated only for single-result — `vm.bn:237`).
- **Name→type helper** (interp): given `pkgPath`, derive the VM qual (`lastSegment(pkgPath)+"."+
  funcName`) and fetch the signature via `Checker.PackageType(pkgPath, funcName)`. Scope to
  package-level functions first (methods `pkg.Type.Method` are a follow-up).
- Tests: a VM-level test that a function returning a struct and a `(int,@errors.Error)`
  multi-return lands correct exact bytes in a caller retbuf.

## Stage B — `Value` type + scalar/string marshaling + `RunFuncTyped`

- New file `pkg/binate/interp/value.bn` (+ decls in `pkg/binate/interp.bni`): the `Value` struct,
  constructors/accessors for int/bool/`@[]char`, and `Release()`.
- `RunFuncTyped` (in `interp.bn` beside `RunFunc`): the flow above, initially handling scalar and
  `@[]char` params/results and multi-return of those.
- Refcount contract enforced: **do not** RefInc on receipt (results are +1-owned); **do not**
  RefDec passed args (callee already balanced its param copy); `Value.Release()` is the only
  RefDec path for results.
- Tests: call `func add(a,b int) int`, `func echo(s @[]char) @[]char`, `func split() (@[]char,
  @errors.Error)` — asserting values AND refcount balance (no leak/double-free).

## Stage C — struct / raw-slice / `@[]@[]char` args+results + multi-return completeness

- Extend `Value` marshal/unmarshal to `TYP_STRUCT` (walk `StructLayout`), `TYP_SLICE`,
  `TYP_MANAGED_SLICE` element recursion (`@[]@[]char`), and full multi-return tuple split.
- Ownership walk in `Release()` recurses managed fields/elements.
- **Documented deferral within the "full" scope:** interface-value and func-value params/results
  (they require the cross-mode vtable substitution `vm_iface_crossmode.bn` / `substituteVtWords`).
  Filed as a follow-up todo, not built here — an eyes-open scope edge, not a silent gap.

## Inc 4 — build-config setter + source-provider seam (after Inc 3)

- **`@Interp.SetBuildConfig(cfg @buildcfg.BuildConfig)`** — override the host default that `New`
  bakes (`interp.bn:85`, `it.Ldr.BuildConfig = buildcfg.ConfigForTarget("")`). One method + a
  before-`LoadProgram` note (mirrors `SetEmitNilChecks`).
- **`SourceProvider` interface** — abstract the loader's only filesystem coupling
  (`loader/loader_load.bn` `os.Stat`/`os.ReadDir`; `loader/loader_util.bn:93` `readFileBytes` →
  `os.Open`). Introduce a small interface (`Read(path) (@[]uint8, bool)` + `List(dir)` + `Stat`),
  give the loader a provider field defaulting to an **os-backed impl** (zero behavior change), and
  route those three call sites through it. **No in-memory provider built** — just the seam, so
  `plan-wasm-browser.md` can inject one later. `SetBuildConfig` + the provider default keep every
  existing caller (cmd/bni, cmd/bnc via loader) unchanged.

## Refcount / ownership contract (the correctness spine)

- Result managed values arrive **+1, ownership transferred** → the `Value` owns them; caller calls
  `Release()`. Never RefInc a received result.
- Passed-in managed args: host keeps its own reference; **never** RefDec an arg after the call.
- Borrowed (`Refptr == nil`) string views (argv-style) must outlive the VM call — `StringValue`
  documents the owned-vs-borrowed choice; default to an **owned** backing (`captureEnv` style) for
  safety unless the caller opts into borrowing.
- Retbuf lifetime: the new accessor copies out **before** `vm.SP` unwinds (like `TrampolineAggregate`),
  so there is no post-return scratch hazard; the interp copies retbuf → `Value.Bytes` immediately.

## BUILDER / layering notes

`pkg/binate/interp` and `pkg/binate/vm` are **not** in bnc's BUILDER-compiled tree (they ride
cmd/bni, built by the fresh bnc) → full language available, no BUILDER constraint. `pkg/binate/types`
IS BUILDER-compiled, but this work only **consumes** existing types APIs (PackageType, SizeOf,
offset accessors, AggregateReturnSize) — no types change is anticipated. If any turns out missing,
it becomes a small BUILDER-safe types addition (flagged if it arises).

## Verification

- Unit tests per stage via `scripts/unittest/run.sh builder-comp pkg/binate/vm pkg/binate/interp`
  (compiled) and `builder-comp-int` (the VM interpreting itself). New tests:
  - VM: `CallFuncAggregate` exact-bytes for a struct return and a `(int,@errors.Error)` multi-return.
  - interp: `RunFuncTyped` for scalar/string/multi-return; a **refcount-balance** test (capture the
    live-object count before/after and assert net-zero after `Release()`), following the
    `matrix/dispatch-refcount` pattern.
- Hygiene: `scripts/hygiene/run.sh` (file-length — split `value.bn` if it grows; bnfmt).
- Each stage is independently green and cherry-pickable (the increment discipline of the source plan).
- Each landed commit gets a minimal adversarial review, focused on the refcount hazards the research
  flagged: return leak, spurious RefInc, arg over-decrement, borrowed-view UAF, retbuf truncation.

## Risks

1. **Refcount ownership** is the top risk (leak / double-free-abort). Mitigated by the explicit
   contract above + a per-stage refcount-balance test + adversarial review.
2. **VM accessor multi-return generalization** — must extend `TrampolineAggregate`'s single-result
   assumption without regressing the existing single-aggregate / extern paths; covered by a VM unit
   test and by running the existing vm suite.
3. **Interface/func-value marshaling** deferred (needs vtable substitution) — scoped out with a
   tracked follow-up, not silently dropped.
4. **Name resolution** — the explicit `pkgPath`+`funcName` API sidesteps short-name ambiguity;
   methods deferred to a follow-up.
