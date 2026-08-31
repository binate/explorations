# Plan: (2b) Within-package function inliner

Status: REVISED after adversarial review (2026-08-31). Awaiting user go-ahead.
Grounded in an IR structure recon; file:line citations below trace to it.
Todo: `claude-todo.md` → "(2b) Within-package function inliner — 🟡 OPEN".

## Adversarial review — incorporated

The review confirmed the core is sound — the refcount spine ("faithful clone needs no
reconstruction") **holds** across all four arg flavors (borrowed/fresh `@T`, `@Iface`
move-model), because every caller-side refcount decision is already materialized in the
caller IR before the pass runs, and the callee is self-balancing per side. Its risk is
therefore entirely **mechanical completeness**, not refcount semantics. Changes made:

- **The verifier is NOT a safety net (HIGH).** The draft leaned on `verify.bn` to catch
  remap/ID mistakes. False: `VerifyFunc` (`verify.bn:96-160`) checks only
  block/terminator well-formedness and `Block1/Block2` successor identity — **not** ID
  uniqueness/density, and never inspects `Args`/`Phis.Val`/`Phis.Block`/`PadBlock`. And
  nothing runs `VerifyModule` after `RunOptPasses` (every call site — `compile.bn:143,
  273`, `main.bn:352`, `test.bn:211`, `vm/lower.bn:188` — omits it). A missed ID
  renumber or reference remap is a **redefinition build-break on LLVM** (`%<ID>` alias)
  but **SILENT on the VM/native** (two live instrs share a frame/register slot →
  clobber → wrong value; `vm/lower_func.bn:143,153,164`). So the inliner must ship its
  **own post-condition assertion** (below), and renumber **only `ID >= 0` clones**
  (void instrs keep `ID = -1`; renumbering them corrupts the ID space).
- **Inc 1 result filter → SCALAR-only (HIGH).** "≤1 result" silently admits a single
  **aggregate** (struct/array) return, which flows through sret/retbuf on native
  (`x64_emit_func.bn:127`) and a per-ID region on the VM (`lower_func.bn:141-165`) — not
  trivial. Inc 1 is tightened to scalar (`!isAggregateLoadTyp && !needsStructCopy`, ≤1
  machine word); single-aggregate returns get their own later increment with copy-back
  tests.
- **Use-replacement is WHOLE-FUNCTION (MEDIUM).** The caller's end-of-statement
  `OP_REFDEC` of the call result may sit in a **later block** than the call. Replacing
  uses of the `OP_CALL` result object must sweep **all** caller blocks (and retarget
  that RefDec), or a managed result leaks silently. Stated + tested explicitly.
- **Clear `ParamIndex` on cloned param allocas (LOW).** `ParamIndex` is **debug-info
  only** (sole consumer `emit_debug.bn:263` `emitDbgDeclare`) — NOT param-register
  binding (native binds via `f.Params`/`p.ID`, `x64_emit_func.bn:124-154`; the earlier
  worry about register binding is refuted). But a cloned param-slot alloca that keeps
  `ParamIndex = i+1` emits a bogus `arg: N` DWARF (an inlined local shown as the
  *caller's* argument N) at `-O1 -g` for **managed** params (scalar slots get promoted
  away, so only managed `@T`/`@[]T` param slots survive to expose it). Fix: on every
  cloned param-slot alloca, clear `ParamIndex = 0`, `IsByvalParamRef = false`, and drop
  the param name.

Review confirmations kept: refcount spine across all arg flavors (§ correctness spine
1); Inc 1 is feasible and non-empty (`add`, plain-`@T` accessors are single-block, leaf,
no-fault); "leaf = no `OP_CALL*`" is correct (RefInc/RefDec are single void instrs, not
calls; managed-slice/iface/func-value/struct dtors DO emit calls/branches and so
self-exclude from Inc 1); fault pads are moot for Inc 1 (no-fault callees); name-match,
orphaned-call-pad, and linkage (callee not deleted) are all safe for Inc 1.

## Goal

An IR-level optimization pass that clones a small **same-package** callee's body into
the caller at a direct `OP_CALL` site, eliminating the call. This is the core
native↔clang codegen-gap closer: clang inlines small same-package callees within a TU
(`charsEqual`→`findSymbol`, every tiny accessor); the native backend inlines nothing.
Unlike (2a)'s bounds-check inlining (which helps both backends equally, so doesn't
move the *ratio*), inlining is something clang already does and native doesn't — so it
**narrows the native↔clang ratio** on real code, and it's generic (helps every program
regardless of algorithmic quality, exactly like clang).

## Design overview

- **A new IR pass** `inlineCalls(m)`, added as the **first** entry in `RunOptPasses`
  (`opt.bn:21-36`), **before `promoteScalars`** — so inlined bodies then get mem2reg +
  load-forwarding + BCE like any other code. Gated at `level >= 1` (bnc -O1+), like the
  rest. Per-module, on the finalized module with **all package funcs present**
  (`compile.bn:273`), so every callee is resolvable.
- **Callee resolution:** an `OP_CALL`'s callee is a name string in `Instr.StrVal`
  (`ir_ops_flow.bn:75-83`); `LookupModuleFunc(m, call.StrVal)` (`gen_iv_thunk.bn:74-83`)
  returns the `@Func`. A locally-defined func has `IsExtern == false` and
  `len(Blocks) > 0`; externs (no body) are not inlinable. Only `OP_CALL` (direct,
  by-name) qualifies — `OP_CALL_INDIRECT` / `OP_CALL_FUNC_VALUE` / `OP_CALL_HANDLE` /
  `OP_CALL_IFACE_METHOD` / `OP_C_CALL` dispatch indirectly and name no static `@Func`.
- **Mechanism:** at a chosen call site, clone the callee's instrs (and, for multi-block,
  its blocks + fault pads) into the caller, substituting the caller's argument objects
  for the callee's params, routing the callee's return value(s) to where the `OP_CALL`
  result was used, and deleting the `OP_CALL`.

## The core correctness spine

1. **Refcounting needs NO reconstruction.** Refcounts are fully explicit in the IR as
   `OP_REFINC`/`OP_REFDEC` void instrs (`load_forward.bn:30-32`), and the call ABI is
   self-balancing per side: callee entry-RefIncs each managed param (`gen_func.bn:186`)
   and scope-exit-RefDecs it (`gen_return.bn:151-205`), plus an Axiom-3 RefInc of each
   managed *return value* to hand the caller an owned ref; the caller already has an
   end-of-statement `OP_REFDEC` of the call **result** in the IR
   (`registerManagedCallResult`, `gen_call.bn:398`). A **faithful clone** of the callee
   body — preserving its entry-RefIncs, exit-RefDecs, and return-RefIncs **verbatim** —
   composes with the caller's existing result-RefDec to stay refcount-exact. The
   `@Iface` move-model (no entry RefInc, `gen_func.bn:199-207`) is likewise captured by
   faithful cloning. **This is the single biggest de-risk: no bespoke refcount logic.**
2. **ID renumbering — value-producing clones only.** SSA IDs are per-function-dense
   (`instr.ID` in `[0, f.NextID)`); the LLVM backend emits `%<ID>` and the VM sizes
   register/alloca arrays as `make_slice(int, f.NextID)` indexed by ID
   (`vm/lower_func.bn:119`). Merging two funcs' ID spaces collides, so **every
   value-producing clone (`ID >= 0`) gets a fresh `nextID(callerFunc)`** — and **void
   clones keep `ID = -1`** (renumbering a void instr makes it look value-producing and
   corrupts the dense ID space the downstream passes index by). Because operands are
   **object references, not IDs** (`verify.bn:32-34`; `Args @[]@Instr`, `ir.bni:581`),
   renumbering is a per-clone `clone.ID = nextID(caller)` with **no operand rewrite** —
   the object→object remap (below) handles data flow.
3. **Object→object remap covering EVERY reference field.** Build a map `callee-Instr →
   clone-Instr` (and `callee-Block → clone-Block`). Remap each clone's `Args[]`,
   `Block1`, `Block2`, `Phis[].Block`, `Phis[].Val`, and the **bespoke `PadBlock`**.
   The existing `rewriteUsesInBlocks` (`mem2reg.bn:368`) remaps only `Args`/`Phis.Val`
   by ID-index — insufficient (misses successors + pads); the inliner needs a broader
   object-keyed remap. Params are special (below).
4. **The inliner owns its post-condition check — the verifier does NOT catch this.**
   `verify.bn` validates block/terminator well-formedness and `Block1/Block2` successor
   identity only; it never checks ID uniqueness/density or `Args`/`Phis`/`PadBlock`, and
   is not run after opt (see review section). So a missed renumber/remap miscompiles
   **silently** on the VM/native. The inliner therefore runs its OWN assertion on each
   merged func: (a) every `ID >= 0` instr has a **unique** ID `< f.NextID`; (b) every
   reference (`Args[]`, `Phis[].Val`, `Phis[].Block`, `Block1`, `Block2`, `PadBlock`)
   resolves to an instr/block **of the merged func**; (c) the standard well-formedness
   `verify.bn` already checks (one terminator/block, in-func successors). This is the
   real safety net; block/CFG mutation itself is precedented (`pruneUnreachableBlocks`,
   `mem2reg.bn:444`). (A durable extension of `verify.bn` with the ID/operand-scope
   check is a reasonable follow-up so future passes get the same backstop.)
5. **Mutate `blk.Instrs` directly, NEVER `addInstr`.** Opt passes rebuild `blk.Instrs`
   as a fresh slice and leave `InstrsVec` stale; calling `addInstr` on an optimized
   block resets `Instrs = InstrsVec.Items()` and resurrects removed instrs
   (`opt.bn:70-74`). The inliner follows the opt-pass discipline (fresh `kept` slices).

## Parameter substitution (the slot+placeholder shape)

A callee param is NOT an `OP_PARAM` value. IR-gen materializes each param as an
`OP_ALLOC` slot + an `OP_STORE` of a **standalone placeholder Instr** (`ID = Param.ID`,
`Op = OP_CONST_INT`, `make(Instr)` not in any block) into the slot; all body uses load
from the slot (`gen_func.bn:148-171`). Two faithful options:
- **(chosen) Clone-and-substitute:** clone the param `OP_ALLOC` + `OP_STORE` into the
  caller, but replace the cloned store's stored value (the placeholder) with the
  **caller's argument object**. Cloned body loads then read the caller's arg through
  the cloned slot; mem2reg (running next) promotes the slot away. Faithful to the ABI
  (the callee's entry param-RefInc, cloned, RefIncs the arg — matching the borrow
  model). The placeholder is reachable only as that store's operand, so the remap
  special-cases `placeholder(Param.ID) → arg`. **Every reference to the placeholder must
  be gone before mem2reg runs** (it keys `rewriteUsesInBlocks` by ID-index, and the
  placeholder's `ID = Param.ID` overlaps the caller's low ID range — a surviving
  reference would be mis-rewritten).
- **Clear the param markers on the cloned slot alloca.** The callee param `OP_ALLOC`
  carries `ParamIndex = i+1`, `IsByvalParamRef`, and a param name (`gen_func.bn:154`).
  On the clone, set `ParamIndex = 0`, `IsByvalParamRef = false`, drop the name — so the
  inlined local is emitted as an ordinary local, not the caller's "argument N". This is
  **debug-info only** (`ParamIndex`'s sole consumer is `emit_debug.bn:263`
  `emitDbgDeclare`; native/VM bind params via `f.Params`/`p.ID`, never `ParamIndex`), so
  it bites only at `-O1 -g` and only for **managed** param slots (scalar slots get
  promoted away by mem2reg before debug emission). Still, clearing it is cheap and
  correct.

## Return → continuation

- **Single-block, single-return-site callee (Inc 1):** no CFG surgery. Splice the
  cloned body instrs (minus the `OP_RETURN`) into the caller **before** the `OP_CALL`;
  replace **every use, in every caller block,** of the `OP_CALL` result object with the
  clone of the return's value (`OP_RETURN.Args[0]`); delete the `OP_CALL`. The
  use-replacement MUST be **whole-function**: the caller's end-of-statement `OP_REFDEC`
  of the call result (`registerManagedCallResult`, `gen_call.bn:398`) can sit in a
  **later block** than the call — a block-local sweep would leave it pointing at the
  deleted result → silent leak. Retargeted onto the cloned return value, it balances
  against the callee's cloned Axiom-3 return-RefInc (`gen_return.bn:151-205`).
- **Multi-block callee (Inc 2):** split the caller block at the `OP_CALL` — instrs
  after the call move into a fresh **continuation** block (append to `f.Blocks`); the
  pre-call part ends with an `OP_JUMP` to the cloned callee entry; each cloned
  `OP_RETURN` becomes an `OP_JUMP` to the continuation. One return site → the result
  value flows directly; **multiple return sites (Inc 4)** → an `OP_PHI` at the
  continuation head merges the per-return values (predecessors = the cloned return
  blocks).
- **Multi-value (tuple) return (Inc 5):** the caller reads components via `OP_EXTRACT`
  of the call result (`gen_call.bn:305`, `ir_ops_flow.bn:366`). Reconstruct the tuple
  (or rewrite each `OP_EXTRACT` to the corresponding cloned return component).

## Fault pads (VM correctness — Inc 3)

Compiled backends **ignore** `Func.FaultPads`; the VM uses them for recoverable-fault
unwinding, and the VM **runs the opt passes** (`vm/lower.bn:188`), so pads matter. A
faulting op (bounds/div/shift/nil check) carries `PadBlock *Block` into the *callee's*
FaultPads, whose pad RefDecs the callee's live managed set and ends `OP_UNWIND_RETURN`.
Inlining a callee with faulting ops must: clone the callee's `FaultPads` into the
caller's `FaultPads`, remap each cloned faulting op's `PadBlock` to the cloned pad, and
reconcile the cloned pads' `OP_UNWIND_RETURN` with the caller frame (an unwind from the
inlined region must relay through the caller's own call-site pad, not "return" from the
caller). **This `OP_UNWIND_RETURN` reconciliation is the subtlest part** — deferred to
its own increment; until then, callees containing faulting ops are **not** inlined.

## Increment roadmap (each independently landable + green, like 2a)

- **Inc 1 — core machinery + simplest case.** Build `CopyInstr` + the object→object
  block/instr cloner + the remap (Args/Block1/Block2/Phis/PadBlock) + ID renumber
  (`ID >= 0` only) + param substitution (with the marker-clearing above) + the
  **post-condition assertion** (spine 4). Inline only callees that are: same-package,
  non-extern, **single basic block** whose terminator is `OP_RETURN`, **leaf** (no
  `OP_CALL*` in body — no recursion possible, no nested inlining), **no faulting ops**
  (no `PadBlock` instr — sidesteps pad cloning), **exactly ≤1 result that is a SCALAR**
  (`!isAggregateLoadTyp && !needsStructCopy`, ≤1 machine word — a single struct/array
  return rides sret/retbuf and is deferred), not the caller itself, params-count ==
  args-count, and **under a size threshold**. Covers tiny scalar accessors (`return
  a+b`, `return p.field` for scalar fields, `return p.next` for `@T`). Whole-function
  use-replacement. Validates clone/substitute/use-replacement/refcount-preservation
  with zero CFG surgery.
- **Inc 2 — multi-block callees** (CFG clone + caller-block split + return→continuation
  for a single return site). Still leaf, no faulting ops, ≤1 result.
- **Inc 3 — fault-pad cloning** (`PadBlock` remap + `OP_UNWIND_RETURN` reconciliation),
  unlocking callees with bounds/div/shift/nil checks (e.g. `charsEqual`).
- **Inc 4 — multiple return sites** (`OP_PHI` merge at the continuation).
- **Inc 5 — multi-value (tuple) return** (`OP_EXTRACT` rewrite / tuple reconstruction).
- **Inc 6 — non-leaf callees + recursion/cycle guard + heuristic tuning + code-growth
  bound + benchmark.** Relax "leaf": allow callees that call others, with a cycle guard
  (don't inline along a call-graph cycle; bound total inlined size per caller). This is
  where the real charsEqual→findSymbol win lands and where code-bloat control matters.

Each increment ships with unit tests + conformance validation; we land per-increment
with an adversarial review (the 2a cadence), and the user decides how far to take it.

## Inlining heuristics (which calls)

- Direct `OP_CALL` to a same-package non-extern func with a body.
- Callee size under a threshold (count instrs across blocks; start conservative, e.g.
  ≤ ~15–20 instrs, tune in Inc 6). Prefer single-use callees (whole-callee win) but not
  required.
- Not recursive / not on a call-graph cycle (Inc 1: leaf-only makes this automatic).
- Not a closure / no captures (`IsClosure`, `NumCaptureParams`) — defer (captures add a
  hidden env param).
- Bound total code growth per caller (Inc 6) so inlining doesn't explode code size.
- Skip callees flagged for separate emission where inlining would break linkage
  semantics (weak_odr dtors/copies, cexport, generic-instantiation edge cases) — audit
  in Inc 6; Inc 1's leaf+size+single-block filter already excludes most.

## Testing

- **IR unit tests** (`pkg/binate/ir`): build a module with a small callee + a caller
  with an `OP_CALL`, run `inlineCalls`, assert the `OP_CALL` is gone, the body is
  spliced, IDs are unique/dense, and `verify` passes. A **refcount-balance** test: a
  managed-arg inlined call must preserve the exact `OP_REFINC`/`OP_REFDEC` set (no
  leak, no double-free) — diff the managed-op multiset against the non-inlined lowering.
- **Codegen tests**: compile a program with a tiny helper at -O1, assert the helper's
  call is gone from the emitted output and the result is correct.
- **Conformance at -O1**: behavior preserved (the pass only runs at level ≥ 1, not the
  standard build, so this is validated via the bnc -O1 path + VM -O1). CRITICAL: the
  refcount-balance and fault-semantics must hold on the **VM** path (it runs the pass).
- **Benchmark** (Inc 6): bnc compiling bnc, native `-O0`-built (native `-O2`-built is
  blocked by the tracked MAJOR SIGSEGV), before/after, to quantify the native win —
  same clean A/B method as the (2a) benchmark.

## Risks (post-review status)

1. **Refcount exactness under clone-and-substitute — CONFIRMED-SAFE by review** across
   borrowed/fresh `@T` and `@Iface` move-model, and for return-own-param and
   return-fresh-`@T`. The spine holds; residual risk is mechanical completeness (2–4),
   not refcount semantics. Still ship the refcount-balance tests (including result-RefDec
   in a different block than the call).
2. **The param placeholder** (`ID = Param.ID`, standalone `OP_CONST_INT` not in any
   block) must have EVERY reference removed by the substitution before mem2reg (its ID
   overlaps the caller's low range; the ID-indexed `rewriteUsesInBlocks` would mis-rewrite
   a survivor). The post-condition assertion (spine 4) is what proves this.
3. **ID renumbering + object-remap completeness — the #1 silent-miscompile risk.** A
   missed `ID >= 0` renumber (or a missed `Args`/`Phis.Val`/`Phis.Block`/`Block1`/
   `Block2`/`PadBlock` remap) collides/dangles → **silent** wrong-code on VM/native
   (loud only on LLVM). NOT caught by `verify.bn` (checks none of these) and nothing runs
   verify after opt. Mitigation: the inliner's own post-condition assertion (spine 4),
   run in the pass on every merged func. This is the single most important safeguard.
4. **`PadBlock` remap** — touched by NO existing rewrite; the object→object remap and the
   post-condition assertion must both cover it (Inc 1 has no pads, but the machinery must
   be pad-aware from the start so Inc 3 composes).
5. **`OP_UNWIND_RETURN` reconciliation** (Inc 3) — the subtlest; a cloned callee pad's
   unwind must relay through the caller frame, not return from the caller. Deferred, but
   the review should sanity-check the deferral boundary (Inc 1–2 exclude faulting ops).
6. **Interaction with mem2reg/BCE ordering.** Inlining runs first; the merged func must
   be well-formed enough for mem2reg (dense IDs off the updated `f.NextID`, valid phis,
   `pruneUnreachableBlocks`-compatible CFG). Confirm mem2reg re-computes dominance and
   sizes tables off the post-inline `f.NextID`.
7. **Code growth / compile-time.** Inlining grows the caller; unbounded it explodes code
   size and compile time (and could worsen, not help). Inc 1's size+leaf+single-block
   filter bounds it; Inc 6 needs a real growth budget.
8. **`OP_CALL` fault pad orphaned by deletion.** The deleted call's own `attachFaultPad`
   pad becomes unreferenced; per `opt.bn:76-83` an unreferenced pad is simply never
   branched to (safe). Confirm no dangling `PadBlock` back-ref survives.
