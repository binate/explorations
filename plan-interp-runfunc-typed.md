# Plan: interp Inc 3 (typed/aggregate RunFunc) + Inc 4 (build-config + source-provider seam)

Status: **Stage A + Stage B LANDED (2026-08-15); Stage C + Inc 4 open.** Continuation of
the archived `done/plan-embeddable-interp.md` — its Inc 3 + Inc 4. Tracked in
`claude-todo.md` ("Embeddable-interp Inc 3 / Inc 4").

- **Stage A** — `vm.CallFuncAggregate` (host-driven exact-size aggregate/multi-return
  result copy, ≤64-byte window). Landed on `main` (`e8f1dadef`).
- **Stage B** — `Value` marshaling box + `RunFuncTyped` for **scalar / `@[]char`,
  single result**. Landed on `main` (`6dabbaa56`). Adversarially reviewed pre-land; 3
  findings fixed (sub-word bool-arg full-word-read; a single struct/array/slice result
  now rejected up front rather than nil-retbuf-crashing; the `CallFuncAggregate` >64-byte
  doc limitation). Refcount-balance tested; green in builder-comp + builder-comp-int.
  Name params are `*[]readonly char` to sidestep the `@[]char`-param call-site-copy leak
  (the MAJOR todo in `claude-todo.md`).
- **Remaining:** Stage C (struct / `@[]@[]char` / multi-return / interface-value results,
  incl. `(T, @Error)`) and Inc 4 (SetBuildConfig + source seam). Note a follow-up surfaced
  during Stage A: `CallFuncAggregate` only handles results ≤64 bytes (execFunc entry-frame
  cap) — lifting that is prerequisite for large Stage-C aggregates.

## Context

`pkg/binate/interp` is the embeddable whole-program interpreter (`@Interp`: `New` →
`AddBniPath`/`AddImplPath` → `LoadProgram` → `RunMain`/`RunFunc`). Today an embedder can only
*run `main`* and get a bare `int` back — `RunFunc(qual, args @[]int) int` takes integer args and
returns one scalar word. Inc 3 lets an embedder **call a named function with typed args and
receive typed / aggregate / multi-return results** — the piece a real host (test harness, wasm
worker, tool) needs. Scope: **full aggregate marshaling** — int/bool/`@[]char`/`@[]@[]char`/
struct/slice, both directions, plus multi-return, **including interface-value results (so the
canonical `(T, @errors.Error)` works)**. Func-value params/results are the one deferred edge.
Inc 4 then abstracts the two remaining host couplings — the baked-to-host build config and the
loader's direct filesystem reads — behind clean seams (a `SetBuildConfig` setter and a source
seam, default os-backed; **no** in-memory provider built yet, just the seam).

Marshaling is tractable because **rt is injected as one shared native instance**, so a managed
value is byte-identical across the compiled-host ↔ bytecode-VM boundary (`interp/externs.bn`,
`vm/vm.bn:15-24`). The whole problem is (a) knowing each param/return `@types.Type`, (b) laying
the words out per the VM ABI, and (c) getting refcount ownership right.

## Design overview

**`Value`** — a uniform host-side box for one marshaled value:
`type Value struct { Typ @types.Type; Bytes @[]uint8 }` where `Bytes` is the value's in-memory
image (`Typ.SizeOf()` bytes, **allocated to `Typ.AlignOf()`** so an 8-byte field on ILP32 is
aligned), and the `Value` **owns** any managed references inside it.
- Constructors: `IntValue(n)`, `Int64Value(n)`, `BoolValue(b)`, `StringValue(s @[]char)` (builds
  the 4-word `{Data,Len,Refptr,BackingLen}` image over an **owned** backing by default),
  `StringSliceValue(@[]@[]char)`, `BytesValue(typ, bytes)` for struct/slice.
- Accessors: `AsInt()`, `AsInt64()`, `AsBool()`, `AsString() @[]char`, `AsStringSlice()`,
  `Field(i)`, and (for iface results) `AsError() @errors.Error` / a raw iface-value reader.
- `Release()` — walks `Typ` and `rt.RefDec`s every managed reference in `Bytes`. Scalars /
  borrowed (`Refptr==nil`) / immortal (negative rc) views: no-op.

**`RunFuncTyped(pkgPath @[]char, funcName @[]char, args @[]Value) (@[]Value, @[]@[]char)`** —
`pkgPath` is the **full** import path (e.g. `"pkg/std/strconv"`). Both the VM lookup key and the
checker lookup use that full path — the VM keys functions by the full-path-qualified name since
the full-path flip (`vm/lower.bn:32,431`; `cmd/bni/main.bn:322-328`), which is exactly
`Checker.PackageType`'s key, so **no** short-segment derivation. Flow:
1. `sig := it.Checker.PackageType(pkgPath, funcName)` → `TYP_FUNC` (`.Params`, `.Results`).
2. **Run package init once** if not already: call `main.__init_all` when present (mirror
   `RunFunc`, `interp.bn:246-249`) so a called function sees initialized globals.
3. Marshal each `args[i]` into VM arg slots per its `Typ` via `vm/lower_slots.argSlots`: a scalar
   is one slot (an int64/uint64/float64 on ILP32 is **two** slots — pack per `argSlots`); an
   aggregate (`@[]char`/struct/slice/iface, `IsByvalParam`/`IsAggregateArg`) rides **one
   by-address slot** = `&Value.Bytes[0]` (matches `vm/lower_call.bn:173-176`).
4. Call via the new VM aggregate accessor (below) with a retbuf sized `AggregateReturnSize(.Results)`.
5. Rebuild `@[]Value` from `.Results`: void → empty; scalar → the returned word (an aggregate/
   packed-tuple return, incl. a bare int64, routes through the retbuf, not the truncating scalar
   path); aggregate / multi-return → split the retbuf per `@VMFunc.ResultOffsets[i]`/`ResultSizes[i]`
   into per-result `Value`s owning their +1 refs.
6. Run-fault → return the fault message as the error slice (mirror `RunMain`, `interp.bn:222-231`);
   do **not** unpack a retbuf on fault.

Reuse: `types.PackageType` (`types/checker.bn:39`), `types.SizeOf`/`AlignOf`/`FieldOffset`/
`StructLayout` + the `MSLICE_FIELD_*`/`SLICE_FIELD_*`/iface offset accessors + `ManagedHeaderSize`/
`ManagedRefcountOffset` (`types.bni:754-816`), `types.AggregateReturnSize` (`abi_return.bn:188`),
`rt.RefInc`/`rt.RefDec`/`rt.ManagedSlice` (`rt.bni`), and the `os.Args`/`captureEnv` templates for
building a `@[]@[]char` (`startup/args_main.bn:39-98`).

## Stage A — enabling plumbing (VM accessor + checker retention + resolution)

- **Retain the checker.** Add `Checker @types.Checker` to the `Interp` struct (`interp.bn:20-36`);
  `LoadProgram` stores the checker it already builds (`interp.bn:143-145`) instead of dropping it.
  (Safe: it's a read-only object with no per-run teardown.)
- **New VM entry point** in `pkg/binate/vm/` + `pkg/binate/vm.bni` (near `CallFunc`, `vm.bni:979`),
  `CallFuncAggregate(name *[]readonly char, args @[]int, retbuf *uint8, retbufSize int) int`. It is
  a **new** function (does NOT modify `TrampolineAggregate` — leave its func-value-dispatch
  single-result guard `vm.bn:218-220` alone): reset fault/interrupt status on entry like `CallFunc`
  (`vm.bn:273-280`); run `execFunc`; on `Status == VM_STATUS_FAULTED` return without touching the
  retbuf; otherwise, **before** `vm.SP` unwinds, copy **exactly `retbufSize`** bytes from the live
  aggregate/packed-tuple address into `retbuf` — the top-level frame returns the address WITHOUT
  popping and the image is still live at/above `vm.SP` (`vm_exec.bn:135-141`,
  `vm_exec_return.bn:24-61`), so this reads live memory, and copying the exact size sidesteps the
  64-byte cap (`vm_exec_helpers.bn:55`). Scalar/void returns keep the existing scalar path.
- **Name→type helper** (interp): `PackageType(pkgPath, funcName)` for the signature; the VM key is
  `pkgPath + "." + funcName`. Package-level functions first (methods `pkg.Type.Method` are a follow-up).
- Tests (VM): `CallFuncAggregate` exact-bytes for a struct return and a `(int, int)` multi-return.

## Stage B — `Value` type + scalar/string marshaling + `RunFuncTyped`

- New file `pkg/binate/interp/value.bn` (+ decls in `pkg/binate/interp.bni`): the `Value` struct,
  constructors/accessors for int/int64/bool/`@[]char`, `Release()`.
- `RunFuncTyped` (in `interp.bn` beside `RunFunc`): the flow above, handling scalar + `@[]char`
  params/results and multi-return of those.
- Refcount contract enforced (see below).
- Tests: `func add(a,b int) int`, `func echo(s @[]char) @[]char`, `func pair() (@[]char, int)`
  — asserting values AND refcount balance (no leak/double-free). (Uses `(@[]char, int)`, NOT
  `(T, @Error)`, because interface results land in Stage C.)

## Stage C — struct / `@[]@[]char` / multi-return / interface-value results

Broken (per an ABI/refcount recon, `wf_66c80e60`) into five independently-landable
sub-increments.  Land order **SI-1 → SI-2 → SI-3 → SI-4 → SI-5**; SI-4 is a pure VM
metadata fix independent of the interp increments but a prerequisite for SI-5.

The correctness spine is the **lockstep invariant**: `supportedMarshalType` (what
RunFuncTyped admits) must equal *exactly* the set `releaseImage` can fully free —
never wider (over-wide = leak or garbage read).  A returned aggregate arrives with
each managed field/element already **+1-owned** by the host (evidence:
`conformance/matrix/abi/managed-struct-return*`, no xfail, comp+int) — never RefInc
on receipt; `Release()` RefDecs each once.

- **SI-1 — single by-value struct result/param — ✅ LANDED (`347526d86`).**
  `Release()` → recursive `releaseImage` mirroring the compiler's struct/managed-slice
  dtors; `supportedMarshalType` struct arm (`SizeOf ≤ 64`, fields recursively supported).
  An adversarial review folded in three lockstep fixes: (1) the scalar arm was leaking a
  managed single-word (`@T`/`@func`/`@Iface`) treated as a scalar — now rejects
  `NeedsDestruction`; (2) the managed-slice arm admitted a managed-pointer element
  (`@[]@Node`) whose elements it never freed — now gated on the element's
  `NeedsDestruction`; (3) a small (`≤` word) struct is coerced in-register but classified
  by-address, so a small anonymous-struct result read an empty retbuf and aborted —
  `supportedResultType`/`supportedParamType` now require a by-address type to agree with
  `IsAggregateReturnTyp`/`IsAggregateArg`.
- **SI-2 — nested-managed recursion (`@[]@[]char`, structs of nested managed-slices) — ✅ LANDED (`1ae8cf0d0`).**
  Extend `releaseImage`'s managed-slice arm to recurse elements *guarded by
  `Refcount(backing)==1`* (only the sole owner frees elements; iterate the backing
  `Refptr..+BackingLen*stride`, RefDec the outer backing LAST); relax the element gate to
  `supportedMarshalType(Elem)`.  Host-releasable iff **no `@T`/`@func`/`@Iface` appears
  anywhere inside** (those need the cross-mode dtor handle — SI-5); `@[]@[]char` bottoms
  out in nil-dtor char backings, so it qualifies.
- **SI-3 — multi-return tuple split.** Lift the `len(Results) > 1` reject; split the retbuf
  per a recomputed `MakeStructType("", results).FieldOffset(i)` (byte-identical to the
  packer, no new VM accessor); add a total `AggregateReturnSize ≤ 64` gate.
- **SI-4 — VM: populate `ResultIfaceVtOffsets` for multi-return** (`lower_func_helpers.bn`
  `populateResultMetadata`: fold `collectIfaceVtOffsets` over every result shifted by
  `ResultOffsets[i]`, not just `Results[0]`).  Pure VM metadata fix; single-result behavior
  unchanged; `call_aggregate.bn`'s existing `substituteVtWords` guard then covers the whole
  retbuf.  Prerequisite for SI-5.
- **SI-5 — interp interface-value result (`(T, @errors.Error)`) — GATED.** The marshaling is
  mechanical (add the iface kinds to `isByAddressType`/`supportedResultType`, RefDec the data
  ptr once), but two design questions block it: **(#1)** releasing a managed iface value needs
  its dtor, which lives in the (SI-4-substituted) native handle-vtable — a func-value handle
  re-entering `execFunc`, not a raw fn pointer; **(#2)** there is no host-facing single-iface-
  method-call API to invoke `.Error()` at all.  Both need the host↔handle call convention
  designed first — surface to the user before coding SI-5.  Also: `supportedMarshalType` is
  shared by params and results, but an iface **param** is move-model (callee does no entry
  RefInc, does exit RefDec) — SI-5 must split param-vs-result admission there, not silently
  widen both.
- **Deferred edge (func-value only):** func-value params/results (rare; need closure/vtable
  handling) — tracked follow-up, not a silent gap.

## Inc 4 — build-config setter + source seam (after Inc 3)

- **`@Interp.SetBuildConfig(cfg @buildcfg.BuildConfig)`** — override the host default `New` bakes
  (`interp.bn:85`). Just writes the existing `it.Ldr.BuildConfig` field; BUILDER-safe; one method.
- **Source seam over the loader's FS reads** (`loader/loader_load.bn` `os.Stat`/`os.ReadDir`;
  `loader/loader_util.bn:93` `readFileBytes` → `os.Open`). **`pkg/binate/loader` IS in bnc's
  BUILDER-compiled tree** — so at Inc-4 time, **first test whether the current BUILDER compiles an
  interface in loader** (`scripts/fetch-builder.sh --tool bnc` on a snippet). If yes, a small
  `SourceProvider` interface (`Read(path)→(@[]uint8,bool)`; `Stat(path)→(exists,isDir)`;
  `List(dir)→@[]@[]char` of entry names, dot-prefixed skipped — matching the loader's `.IsDir()`/
  `.Name()` uses at `loader_load.bn:35-70`) with an **os-backed default impl**. If BUILDER rejects
  interfaces in loader, use a `{fn-values, ctx *uint8}` struct seam instead (the shape the VM
  trampolines already use). Either way: default = os-backed, zero behavior change, **no** in-memory
  provider built — just the injection point for `plan-wasm-browser.md`.

## Refcount / ownership contract (the correctness spine)

- **Result** managed values arrive **+1, ownership transferred** → the `Value` owns them; caller
  calls `Release()`. Never RefInc a received result.
- **Non-iface managed args** (`@T`/`@[]T`/`@func` by the standard param model): the callee
  entry-RefIncs and scope-exit-RefDecs its param copy (`ir/gen_func.bn:186-208`), so the arg's
  backing is net-zero across the call and the host's `Value` is untouched — do **not** RefInc on
  pass or RefDec after. An owned `StringValue` arg is safe (rc 1→2→1 across the call).
- **Interface-value args are move-model** (`ir/gen_func.bn:200-207`): the callee does **no** entry
  RefInc but **does** exit-RefDec. So for an iface arg the host must RefInc before the call (or
  treat the `Value` as moved-out) — the Stage-C iface-arg path handles this explicitly; the
  general "callee balances it" rule does NOT apply to iface args.
- Borrowed (`Refptr==nil`) string views (argv-style) must outlive the VM call; `StringValue`
  defaults to an **owned** backing (`captureEnv` style) for safety; a borrowing constructor is opt-in.
- Retbuf lifetime: `CallFuncAggregate` copies out before `vm.SP` unwinds; the interp copies retbuf
  → `Value.Bytes` immediately, so no post-return scratch hazard.

## BUILDER / layering notes

`pkg/binate/interp` and `pkg/binate/vm` are NOT in bnc's BUILDER tree (they ride cmd/bni, built by
the fresh bnc) → full language, no BUILDER constraint. `pkg/binate/types` IS BUILDER-compiled but
this work only **consumes** existing types APIs. **`pkg/binate/loader` IS BUILDER-compiled** — the
Inc-4 source seam must be BUILDER-verified (above). If a needed types/loader addition turns out
BUILDER-unsafe, that's flagged and reworked at the time.

## Verification

- Unit tests per stage via `scripts/unittest/run.sh builder-comp pkg/binate/vm pkg/binate/interp`
  (compiled) and `builder-comp-int` (VM interpreting itself). New tests:
  - VM: `CallFuncAggregate` exact-bytes for a struct return and a multi-return.
  - interp: `RunFuncTyped` scalar/string/multi-return + `(T, @Error)` (Stage C); a **refcount-balance**
    test (live-object count net-zero after `Release()`), following `matrix/dispatch-refcount`.
- Hygiene: `scripts/hygiene/run.sh` (file-length — split `value.bn` if it grows; bnfmt).
- Each stage independently green and cherry-pickable; each landed commit gets a minimal adversarial
  review focused on the refcount hazards: return leak, spurious RefInc, iface-arg over-decrement,
  borrowed-view UAF, retbuf truncation.

## Risks

1. **Refcount ownership** (leak / double-free-abort) — the explicit contract above (esp. the iface
   move exception) + a per-stage refcount-balance test + adversarial review.
2. **Multi-return iface vtable substitution** (Stage C) — the one genuinely new VM-metadata
   extension; covered by the `(T,@Error)` test on both compiled and `-int` modes.
3. **VM accessor** — new code, doesn't touch `TrampolineAggregate`; run the existing vm suite to
   confirm no regression.
4. **Inc 4 loader seam** — BUILDER-gated; verify the interface empirically or fall back to `{fn,ctx}`.
