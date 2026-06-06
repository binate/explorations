# Code-Red P2 — Plan 2: Refcount Axiom-5 discipline & @Iface/@func lifecycle — route all copy-sites through shared dispatchers

> One of four disjoint code-red P2 work plans (partition in `plan-code-red-p2.md`).
> Source-confirmed: each defect cites root cause, fix shape, files, and test status.
> Defect details are tracked in `claude-todo.md`; this plan is the execution view.

## Status (execution progress)

- **Step 1 — shared dispatchers**: LANDED (binate `f7432452`).
  `emitStoreManagedSlot` / `emitAcquireManagedScalar` / `registerManagedCallResult`
  (new `gen_store_slot.bn`) + `isFreshManagedValue` (`gen_refcount_pred.bn`), with
  unit tests. Additive — no caller yet. Decision recorded: the dispatcher uses the
  MOVE model (consumeTemp-if-fresh, else RefInc) uniformly across the four scalar
  kinds; observably refcount-equivalent at statement boundaries (where the matrix
  asserts), and matches what short-var single-bind and the INDEX arms already do.
- **Step 2 — defect 3 (call-result registration)**: in progress. Also found and
  fixed a cleanup-side counterpart the plan omitted: `emitTempCleanupBody` /
  `emitTempCleanupSince` lacked the `@func` arm, so registering a `@func` call
  result did nothing without it. Predicate fix broadened to `OP_CALL_FUNC_VALUE`
  too (not just `OP_CALL_IFACE_METHOD`). Tests: un-xfail `assign/blank/func-value`,
  new `discard/stmt` matrix form (bare-statement discard, all 5 types), new
  `601_iface_dispatch_result_discard`, 3 predicate unit tests.
- **Steps 3–6**: not started. Confirmed against the current tree (line numbers
  drifted; see below). Note: the slice-INDEX arm has the same §3.4 release-before-
  acquire defect as array/pointer (plan title scoped to array/pointer only) — fold
  it into step 4. Defect 2.5's mangler fix must NOT revert the `MethodParamsFlat`
  `@[]@types.Type` workaround in the same commit (the running BUILDER still has the
  bug; only a rebuilt bnc has the fix).

## Summary

All six defects are the SAME shape (plan-code-red.md §3.4): the Axiom-5 managed-value acquire/release invariant is hand-authored arm-by-arm at each copy/store/call-result site in pkg/binate/ir, so each new site is born missing a managed-kind arm (or the whole acquire) — silent leak/double-free/UAF that only surfaces when a mortal @T/@func/@Iface/aggregate flows through that exact untouched cell. The shared dispatchers emitManagedValueCopyRefInc / emitManagedValueRefDec already exist and are correct (gen_util_refcount.bn:18,35); the bugs are at the SITES that fail to call them (or hand-roll a partial four-way switch). Two defects are construction-side acquire gaps (short-var single-bind, for-range), one is call-result-temp registration (@func discard, plus the iface-method-dispatch leak), and two are name-collision/dtor-selection defects in the dtor-name builder (@[]@I → __dtor_ms_unknown; nested cross-package ms-of-ms element dtor undefined). The unifying fix is to introduce ONE emitStoreManagedSlot dispatcher + ONE registerManagedCallResult helper and route every site through them.

## Unifying strategy

YES — one structural fix addresses defects 1, 2, 3, 6, and the §3.4 INDEX-arm ordering defect at once. Introduce two new shared helpers in gen_util_refcount.bn and make them the ONLY way IR-gen writes a managed slot / consumes a managed call result:

(1) `emitStoreManagedSlot(ctx, b, slotPtr, val, slotTyp, isInit) @Block` — encapsulates the full Axiom-5 sequence in ONE place: acquire-new (consumeTemp-if-fresh-else-RefInc for the four managed scalars via the existing isFresh* predicates; emitStructCopy for needsStructCopy aggregates), then (unless isInit) release-old via emitManagedValueRefDec / emitStructDtor of the prior occupant — and crucially acquire-BEFORE-release to fix the self-alias UAF. This replaces the hand-rolled four-way switches at: gen_short_var.bn:84-117 (single-bind, add the missing needsStructCopy arm — defect 1), gen_flow.bn:147-149 (for-range, currently ZERO acquire — defect 2), gen_control.bn:66-107 (IDENT, already acquire-before-release — becomes the reference shape), and gen_control.bn:275-326 / 341-392 (array/pointer INDEX arms, currently RELEASE-before-acquire — the §3.4 ordering defect, fixed by routing through the dispatcher). The composite/array/mslice literal element loops (gen_composite.bn) and gen_return.bn retain loop also collapse onto emitStoreManagedSlot's acquire half (isInit=true, no old value).

(2) `registerManagedCallResult(ctx, result, resultTyp)` — enumerates ALL five managed result kinds (managed-ptr, managed-slice, @func, @Iface, struct) in ONE place, registering the cleanup temp and setting StmtGrewSP for the address-aggregate / multi-word cases. Replaces the partial blocks at gen_call.bn:274-291 (genCall), gen_call.bn:350-364 (genFuncValueCallWithFn), gen_method.bn:221-243 (genMethodCall) — all three currently OMIT the isManagedFuncValueType arm (defect 3) — and adds the entirely-missing registration in gen_iface.bn:106-107 (genInterfaceMethodCall registers NOTHING — the §3.6 iface-dispatch-result leak). For genInterfaceMethodCall the result must also be classified fresh: isFreshManagedIfaceValue (gen_refcount_pred.bn:170) and isFreshManagedFuncValue (gen_refcount_pred.bn:152) must add OP_CALL_IFACE_METHOD so copy-sites treat it as owned (not borrowed → extra RefInc).

After this, adding a managed kind is a one-line change in the two helpers that propagates to every site. The two dtor-name defects (4, 5) are a separate but adjacent mangling-injectivity fix in gen_dtor.bn / gen_dtor_emit.bn — not on the emitStoreManagedSlot path, but they belong here because they are the dtor-SIDE of the same @Iface/@func managed-slice lifecycle.

## Defects

### 2.1 Short-var single-bind `x := s` of a managed struct-by-value skips emitStructCopy → double-free

**CRITICAL · CONFIRMED**

- **Root cause**: gen_short_var.bn single-bind arm (lines 83-117) has isManagedPtrType/isManagedSliceType/isManagedFuncValueType/isManagedIfaceValueType acquire arms but NO needsStructCopy arm. A managed struct/array aggregate RHS is stored raw via b.EmitStore(ptr, val) at line 116 with no emitStructCopy follow-up, so the copy's managed fields are not RefInc'd. defineVar (line 117) registers x as a managed local whose scope-exit dtor RefDec's those fields — so both src and x RefDec the same field → double-free. The multi-bind arm (gen_short_var.bn:41-43) and var-init both call emitStructCopy; only single-bind is the gap. Confirmed by reading lines 55-118.
- **Fix shape**: Add the missing aggregate arm to the single-bind path. Structurally: route the store through the new emitStoreManagedSlot(ctx, b, ptr, val, typ, isInit=true) so the needsStructCopy acquire is emitted for free (mirrors var-init). Minimal point fix if not unifying: after EmitAlloc at line 115, `if needsStructCopy(typ) { emitStructCopy(ctx.Func, b, ptr, typ) }`.
- **Files**: `pkg/binate/ir/gen_short_var.bn (genShortVar single-bind arm, lines 83-118)`; `pkg/binate/ir/gen_util_refcount.bn (new emitStoreManagedSlot)`
- **Tests**: covered: conformance/matrix/refcount/short-var/ident/managed-struct.bn — xfailed in all 6 default modes + native aa64/x64/x64_darwin + arm32 lanes (verified the xfail files exist). Asserts rc stays 1 after `tgt := src` vs balanced 2.

### 2.2 `for v in coll` over a managed-element collection over-releases the bound value → double-free

**CRITICAL · CONFIRMED**

- **Root cause**: genForIn (gen_flow.bn:137-149) loads each element as a BORROW (EmitLoad / EmitSliceGet at lines 141/143, no RefInc), stores it raw into valPtr (EmitStore line 148), then defineVar (line 149) registers v as a managed scope var. Scope cleanup (emitDecForScopeVars, gen_util_refcount.bn:395) RefDec's v at scope end — but nothing ever acquired it, so it's 0 acquires / 1 release per iteration → at the collection's destruction the element is double-freed. Also: the blank `_` value name is registered as a phantom managed scope var (line 149 unconditionally defineVar's stmt.ForVal.Name). Confirmed by reading gen_flow.bn:93-178; ForKey is always int so it's safe.
- **Fix shape**: The bind must acquire the loaded element before defining v. Route the valPtr store through emitStoreManagedSlot(ctx, b, valPtr, elem, elemTyp, isInit=true) so the consumeTemp-if-fresh-else-RefInc / emitStructCopy acquire is applied uniformly across all five managed kinds. Skip the defineVar registration entirely (or define as a non-owning borrow not added to ctx.Vars) for a blank `_` value name. Covers `for i, v`, array collections, and managed-slice collections identically.
- **Files**: `pkg/binate/ir/gen_flow.bn (genForIn, lines 136-156)`; `pkg/binate/ir/gen_util_refcount.bn (new emitStoreManagedSlot)`
- **Tests**: covered: conformance/matrix/refcount/for-range-value/value/managed-ptr.bn — xfailed in all 6 default modes + native + arm32 lanes (verified). GAP: the for-range-value/value axis currently has ONLY managed-ptr; needs managed-slice / func-value / iface / managed-struct cells added to pin all five kinds through the dispatcher.

### 2.3 Discarded @func-returning call result leaks (no cleanup-temp registration) + iface-method-dispatch result leaks

**MAJOR · CONFIRMED**

- **Root cause**: genCall (gen_call.bn:274-291), genFuncValueCallWithFn (gen_call.bn:350-364), and genMethodCall (gen_method.bn:221-243) register @T/@[]T/@Iface/struct results as cleanup temps but have NO isManagedFuncValueType(resultTyp) arm and no StmtGrewSP for it — so a @func-returning call whose result is discarded never RefDec's its closure data (leak) and the VM never reclaims the SP growth. Separately, genInterfaceMethodCall (gen_iface.bn:63-108) returns b.EmitCallIfaceMethod(...) at lines 106-107 with NO registerTemp / StmtGrewSP for ANY managed result kind → §3.6 CRITICAL iface-dispatch-result leak. Both stem from per-site hand-authored registration blocks. Also isFreshManagedFuncValue (gen_refcount_pred.bn:152) and isFreshManagedIfaceValue (gen_refcount_pred.bn:170) omit OP_CALL_IFACE_METHOD, so a copy-site treats the dispatch result as a borrow and applies an extra RefInc.
- **Fix shape**: Introduce registerManagedCallResult(ctx, result, resultTyp) enumerating all five managed kinds (incl. @func with StmtGrewSP) and call it at ALL FOUR call sites (genCall, genFuncValueCallWithFn, genMethodCall, genInterfaceMethodCall). Add OP_CALL_IFACE_METHOD to isFreshManagedFuncValue and isFreshManagedIfaceValue so the dispatch result is classified as an owned producer.
- **Files**: `pkg/binate/ir/gen_call.bn (genCall 274-291, genFuncValueCallWithFn 350-364)`; `pkg/binate/ir/gen_method.bn (genMethodCall 221-243)`; `pkg/binate/ir/gen_iface.bn (genInterfaceMethodCall 106-107)`; `pkg/binate/ir/gen_refcount_pred.bn (isFreshManagedFuncValue 152, isFreshManagedIfaceValue 170)`; `pkg/binate/ir/gen_util_refcount.bn (new registerManagedCallResult)`
- **Tests**: covered (partial): conformance/matrix/refcount/assign/blank/func-value.bn xfailed all modes (xfail reason names this exact root cause — `_ = wrap(src)` leaks). GAP: NO test pins the iface-method-dispatch-result leak as a balance test (575 exercises cur=cur.next() but checks only summed values per §3.6); needs a new conformance/matrix cell for a discarded @Iface-returning iface-method call, and a bare-statement (not `_=`) @func-call discard.

### 2.4 `@[]@I` constructed via a slice LITERAL leaks elements — __dtor_ms_unknown name collision

**MAJOR · CONFIRMED**

- **Root cause**: dtorTypeSuffix (gen_dtor.bn:26-64) builds the dtor-function name suffix but has NO case for TYP_INTERFACE_VALUE_MANAGED (nor TYP_MANAGED_FUNC_VALUE). So an @[]@I value's dtor name falls through to the `unknown` fallback (line 62): `__dtor_ms_unknown`. The managed-slice dtor BODY generator (genManagedSliceDtor, gen_dtor_emit_bodies.bn:193-198) DOES correctly walk @Iface elements — so the defect is dtor-NAME SELECTION, not the body. Two consequences: (a) the name is non-injective — every distinct unhandled element kind (@[]@I, @[]@func, etc.) collapses to the SAME __dtor_ms_unknown symbol, so whichever body is generated first wins and the others get the wrong dtor; (b) the construction-side acquire is actually correct (genManagedSliceLit, gen_composite.bn:343-348 handles @Iface elements), so the leak is purely that the slice's scope-exit RefDec selects a dtor that doesn't walk the elements (the todo's NULL-dtor observation). NeedsDestruction()==true for TYP_INTERFACE_VALUE_MANAGED (types_query.bn:386), so the element-walk WOULD run if the name resolved to the right body.
- **Fix shape**: Add TYP_INTERFACE_VALUE_MANAGED and TYP_MANAGED_FUNC_VALUE arms to dtorTypeSuffix (gen_dtor.bn) producing distinct, injective suffixes (e.g. `iv_<ifaceQualifiedName>` and `fv_<sig-hash>`), so @[]@I gets a unique __dtor_ms_iv_<I> whose generated body walks the iface elements. Verify ensureMsDtor (gen_dtor_emit.bn:148) then generates the correct per-iface body. This is a mangling-injectivity fix (plan-code-red.md §3.12 family) surfacing in the dtor path.
- **Files**: `pkg/binate/ir/gen_dtor.bn (dtorTypeSuffix, lines 26-64)`; `pkg/binate/ir/gen_dtor_emit_bodies.bn (genManagedSliceDtor — verify iface arm reached)`
- **Tests**: GAP: no balance test exists for an @[]@I slice LITERAL drop. conformance/440_iv_in_slice_mgd.bn exists but uses element-ASSIGN and is itself flagged compiles-but-segfaults (known-incomplete @[]@I, §P.4). Needs a new conformance/matrix/mslice-lit or regressions cell: `var s @[]@Foo = @[]@Foo{makeFoo()}` dropped at scope exit, assert wrapped @Counter rc returns to baseline.

### 2.5 `@[]@[]@T` STRUCT FIELD references an undefined nested cross-package element dtor (symbol mismatch)

**MAJOR · CONFIRMED**

- **Root cause**: For a struct field of type @[]@[]@T where T is cross-package (e.g. pkg/binate/types.Type): generateNonStructDtors (gen_dtor_emit.bn:78-86) calls ensureMsDtor(m, @[]@[]@T). ensureMsDtor (gen_dtor_emit.bn:148-160) recurses on the inner managed-slice element @[]@T and generates a LOCAL inner dtor whose name comes from dtorNameForType (line 149) — UNQUALIFIED `__dtor_ms_mp_Type` (mangled bn___dtor_ms_mp_Type). But the OUTER dtor's body (genManagedSliceDtor, gen_dtor_emit_bodies.bn:200-201, else arm) emits an element-cleanup CALL via qualifiedDtorNameForType(@[]@T), which (gen_dtor.bn:147-158, innermost struct is cross-package) produces the PACKAGE-QUALIFIED `pkg/binate/types.__dtor_ms_mp_Type` (mangled bn_pkg__binate__types____dtor_ms_mp_Type). The two are DIFFERENT symbols → the qualified reference is never defined → clang `use of undefined value`. This is the same cross-package symbol-identity gate already applied to nested cross-package STRUCT dtors (gen_dtor_emit.bn:119-126, declare-extern-and-skip) but NOT applied to the nested ms-of-ms element dtor.
- **Fix shape**: Make ensureMsDtor (and the genManagedSliceDtor element-call) agree on ONE symbol for a cross-package inner element dtor: when the inner element's innermost struct is cross-package, declare it extern (declareExternDtor with the qualifiedDtorNameForType name) and DO NOT generate a local duplicate — mirroring the struct-field gate at gen_dtor_emit.bn:119-126. Equivalently, route both the recursive-generate name and the body-call name through qualifiedDtorNameForType so they cannot diverge. Same class as defect 4 (mangling injectivity).
- **Files**: `pkg/binate/ir/gen_dtor_emit.bn (ensureMsDtor 148-160; the cross-package gate from 119-126 to generalize)`; `pkg/binate/ir/gen_dtor_emit_bodies.bn (genManagedSliceDtor element-call at 200-201)`; `pkg/binate/ir/gen_dtor.bn (qualifiedDtorNameForType 134-160)`
- **Tests**: GAP: no test — the in-tree trigger was worked around by switching ModuleInterface to a flat @[]@Type encoding (MethodParamsFlat). Needs a unit test (or conformance) declaring a struct with a @[]@[]@T field where T is a cross-package managed struct, asserting the nested element dtor symbol is defined/extern-declared (not a dangling reference).

### 2.6 Managed-interface-value (@Iface) refcount lifecycle — FAMILY of leaks + 1 UAF (mostly landed; coverage + dispatcher-routing residual)

**MAJOR · in-progress**

- **Root cause**: The three originally-filed @Iface lifecycle holes are now WIRED in the current tree (verified by reading source): (1) UAF return-a-named-local-@Iface — gen_return.bn:160-166 now has the isFreshManagedIfaceValue consumeTemp-else-RefInc retain arm; (2) discarded iface temp leak — emitTempCleanupBody (gen_util_refcount.bn:307-309) now RefDec's iface temps, and genCall registers them (gen_call.bn:282-286); (3) reassign-@Iface-local leak — gen_control.bn:87-94 now RefDec's the old @Iface before storing. NeedsDestruction→true for TYP_INTERFACE_VALUE_MANAGED (types_query.bn:386). The RESIDUAL @Iface lifecycle work that belongs to THIS plan is: (a) the iface-method-dispatch result leak (folded into defect 3 — gen_iface.bn registers nothing); (b) routing the scalar-@Iface copy arms (currently hand-rolled identically in gen_short_var / gen_control IDENT/deref/SELECTOR/INDEX / gen_composite / gen_return) through the single emitStoreManagedSlot so they cannot re-diverge; (c) the b2 lifecycle-matrix coverage gap (construction×consumption depth, esp. cast-from-impl and the native↔VM trampoline) is unbuilt.
- **Fix shape**: No new lifecycle bug to fix here beyond defect 3; the action is CONSOLIDATION + COVERAGE. Replace the per-arm hand-rolled @Iface (and @func) acquire/release switches with calls to emitStoreManagedSlot (the unifying dispatcher) so the symmetry between acquire-sites and the __copy_/__dtor_ generators is structural, not hand-maintained. Then build the (b2) lifecycle matrix: managed-kind (@Iface / @[]@I / captured-@func) × construction (make / literal / cast-from-impl / capture) × consumption (call-method / index / range / pass / return / discard) × backend, each a refcount-balance assertion with a mortal source. Params/args MUST stay MOVE-model for @Iface/@func (no entry RefInc; caller consumeTemp/RefInc the arg) — the VM passes the 2-word value on transient SP that the call reclaims, so the copy model crashes (370/383 in -int). Do NOT regress that.
- **Files**: `pkg/binate/ir/gen_util_refcount.bn (new emitStoreManagedSlot — consolidation target)`; `pkg/binate/ir/gen_control.bn (IDENT/deref/SELECTOR/INDEX arms — route through dispatcher)`; `pkg/binate/ir/gen_short_var.bn, gen_composite.bn, gen_return.bn (acquire half)`; `conformance/matrix/refcount (new b2 lifecycle cells)`
- **Tests**: covered (lifecycle core): conformance/matrix/refcount has @Iface cells across var-init/assign/multi-assign/composite-lit/mslice-lit/array-lit forms; 370/383/473/520/521/545/546 pin the original leaks/UAF. GAP: the (b2) lifecycle matrix (construction×consumption depth, cast-from-impl, native↔VM trampoline for captured-@func) is NOT built — this is the stated coverage gap. Also the @[]@I literal-element leak (defect 4) is part of this family's long tail.

## Sequencing

Order to minimize churn and keep the tree green at each step:

1. FIRST land the two shared helpers in gen_util_refcount.bn: emitStoreManagedSlot (acquire-before-release Axiom-5 sequence over all 5 kinds) and registerManagedCallResult (all 5 result kinds + StmtGrewSP). Add unit tests for both in gen_util_refcount_test.bn asserting the emitted op sequence. These are additive — no behavior change yet.

2. Defect 3 (call-result registration) — wire registerManagedCallResult into genCall, genFuncValueCallWithFn, genMethodCall, genInterfaceMethodCall; add OP_CALL_IFACE_METHOD to the two isFresh* predicates. This un-xfails assign/blank/func-value across all modes and closes the iface-dispatch leak. Independent, smallest blast radius — do early to bank a confirmed green.

3. Defect 1 (short-var single-bind) and Defect 2 (for-range) — route their stores through emitStoreManagedSlot. Un-xfails short-var/ident/managed-struct and for-range-value/value/managed-ptr. Add the missing for-range type cells (slice/func/iface/managed-struct) at the same time.

4. The §3.4 INDEX-arm ordering defect — route gen_control.bn array/pointer INDEX arms through emitStoreManagedSlot (which acquires before releases), making them match the IDENT arm. Pin with a self-alias `a[i] = a[i]` regression test.

5. Defect 6 consolidation — migrate the remaining hand-rolled IDENT/deref/SELECTOR copy arms onto emitStoreManagedSlot. Pure refactor; existing matrix cells guard it. Then build the (b2) lifecycle matrix.

6. Defects 4 and 5 (dtor-name injectivity) — independent of the emitStoreManagedSlot path, do as a pair (both add suffix/qualification cases). Defect 4 (dtorTypeSuffix iface/func arms) then Defect 5 (cross-package nested ms-of-ms dtor symbol agreement). Add the missing tests for each.

Dependencies: steps 3-5 depend on step 1 (the dispatcher). Step 2 depends on step 1 only for registerManagedCallResult. Steps 4/5/6-dtor are independent of 1-3 and can proceed in parallel by a different worker IF the gen_dtor*.bn files are not also touched by this plan's other steps (they are not).

## New tests needed

- for-range-value/value matrix: add managed-slice, func-value, iface, managed-struct cells (currently only managed-ptr) — pins defect 2 across all 5 kinds through the dispatcher
- A self-alias INDEX regression test: `a[i] = a[i]` / `p[i] = p[i]` for a managed @T element, asserting no UAF — pins the §3.4 RefDec-before-RefInc ordering defect (gen_control.bn:275-310, 341-376)
- A discarded @func-returning call as a BARE STATEMENT (not `_ = ...`): `wrap(src)` standalone — complements assign/blank/func-value (which uses `_=`); both should restore baseline rc
- An @Iface-returning iface-method-dispatch result discarded: `iface.makesFoo()` as a statement, assert wrapped @Counter rc returns to baseline — pins the §3.6 genInterfaceMethodCall leak (no existing balance test; 575 only sums values)
- @[]@I slice-LITERAL drop balance test: `var s @[]@Foo = @[]@Foo{makeFoo()}` at scope exit, assert wrapped tracer rc returns to baseline — pins defect 4 (distinct from element-assign 440)
- A cross-package @[]@[]@T struct-field dtor test (unit or conformance): struct with a @[]@[]@types.Type-shaped field, assert the nested element dtor symbol is defined or extern-declared (no dangling reference) — pins defect 5
- The (b2) lifecycle matrix: managed-kind (@Iface / @[]@I / captured-@func) × construction (make / literal / cast-from-impl / capture) × consumption (call-method / index / range / pass / return / discard) × backend, refcount-balance with a mortal source — the named coverage gap, esp. the native↔VM trampoline path for captured-@func (Class 7) which the refcount matrix does not exercise
- Unit tests in gen_util_refcount_test.bn for the two new dispatchers (emitStoreManagedSlot acquire-before-release ordering for each of the 5 kinds + isInit skip-old; registerManagedCallResult registers all 5 kinds and sets StmtGrewSP for address-aggregates)

## Coordination with other plans

Files this plan OWNS and edits heavily — other plans should avoid: pkg/binate/ir/{gen_util_refcount.bn, gen_short_var.bn, gen_flow.bn, gen_control.bn, gen_call.bn, gen_method.bn, gen_refcount_pred.bn, gen_dtor.bn, gen_dtor_emit.bn, gen_dtor_emit_bodies.bn, gen_composite.bn, gen_return.bn} and the conformance/matrix/refcount tree.

OVERLAP RISKS with the OTHER three plans:
- gen_iface.bn (genInterfaceMethodCall) is edited here (registerManagedCallResult) AND is central to the §3.6 interface plan. COORDINATE: the §3.6 plan owns the iface TYPE-RESOLUTION / 2-word-ABF / vtable-dtor side of gen_iface.bn; THIS plan touches only the call-RESULT registration at gen_iface.bn:106-107 + the isFresh* predicate additions. Split the edit: lifecycle (here) vs resolution/ABI (§3.6). The isFreshManagedIfaceValue OP_CALL_IFACE_METHOD addition (gen_refcount_pred.bn:170) is a lifecycle concern and belongs here.
- gen_dtor.bn / gen_dtor_emit.bn mangling (defects 4, 5) overlaps the §3.12 mangling-injectivity plan if one exists. The dtor-NAME suffix injectivity (dtorTypeSuffix) and cross-package dtor-symbol agreement (qualifiedDtorNameForType / ensureMsDtor) are shared with any name-mangling plan. COORDINATE: if a mangling plan owns gen_dtor.bn naming, hand it defects 4 & 5; otherwise they stay here (they are the dtor-side of THIS plan's @Iface/@func managed-slice lifecycle). Do NOT let both plans edit dtorTypeSuffix concurrently.
- The closures/@func plan (§3.5) edits gen_func_lit.bn (emitCaptureRefInc) and the VM func-value path — DISJOINT from this plan's files EXCEPT both touch the isFresh*/managed-@func classification in gen_refcount_pred.bn. Coordinate on gen_refcount_pred.bn: keep the predicate additions (OP_CALL_IFACE_METHOD here; any capture-related there) in separate small commits.
- The new emitStoreManagedSlot / registerManagedCallResult helpers in gen_util_refcount.bn are NEW symbols — no other plan should add same-named helpers. Land them first so other plans can build on them rather than re-hand-rolling.
- BUILDER constraint: ALL these files are in cmd/bnc's transitive tree (pkg/binate/ir), so every edit MUST stay BUILDER-compilable (no interfaces/generics/closures/floats in the new helpers; use the existing @[]@Instr / explicit-loop style).
