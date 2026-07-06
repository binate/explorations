# Plan: Methods on Generic Types + Parameterized-Receiver Impls

**Status:** detailed execution plan (2026-07-06), expanded from the 2026-07-05
roadmap against a recon of the actual compiler (worktree HEAD `7ea33056`) **and
revised after an adversarial review** that corrected the Phase 3/4 architecture (see
the change log at the end of §2). The **design is settled** (spec §12.1
`gen.method.generic-recv`, `gen.impl.generic-recv`, `gen.no-conditional-impls`; §11.3;
§10.1/§10.4; `claude-notes.md` DECIDED note). This document is the implementation
roadmap at edit-site granularity; it does **not** re-litigate design. Paths are under
`pkg/binate/` unless noted; `.bni` interface files sit beside their dirs.

## 0. Implementation progress (2026-07-06)

Work is on the `work-2` worktree branch (not yet landed; Phases 1–3 land together).

- **Phase 0 — RESOLVED: candidate A.** `preRegisterTypeNames` (`check_decl.bn:291`)
  creates a `MakeNamedType(d.Name)` placeholder (`TYP_NAMED` with a method slot) for
  every type decl, including generic ones; abstract methods attach there, and both
  the body pass and impl coverage find them via `LookupMethod`. Verified facts that
  drive the rest: the instantiation model is **name-based re-resolution** from the
  AST; `substituteTypeParams` (`check_generic.bn:222`) matches type params **by
  `TpIndex`** (owner-agnostic); `Type.Identical` for `TYP_TYPE_PARAM` compares
  `(TpOwner, TpIndex)`; abstract field access in a method body works via the same
  machinery as an existing generic free function.
- **Phase 1 — DONE (commit `18e89b27`).** The checker accepts + body-checks
  `func (it *Cursor[T]) Next() (T,bool)`. Binders keyed to the type decl
  (owner=genDecl) so they're Identical across method/impl/type. Abstract method
  attached to the placeholder with binders on `FuncType.TypeParams`. Also fixed **two
  pre-existing passes** that resolved a generic-type method's signature without the
  binders (`undefined: T`): the func sizing check (`check_decl.bn`) and the REPL
  pending-dep capture (`check_pending.bn`) now skip generic-type methods, like they
  skip generic functions. New file `check_decl_func_generic.bn`.
- **Phase 2 — DONE (commit `b5137809`).** The checker accepts `impl *Cursor[T] :
  Iterator[T]` and checks coverage **abstractly** against the placeholder's method
  set (the receiver binder and the method binder are Identical by (owner,index), so
  `Iterator[T].Next` matches `Cursor[T].Next`). `Impl` gained a `Placeholder` field
  (set only for generic-receiver impls; the satisfaction pass looks methods up there
  because `RecvType.ReceiverBaseNamed()` is the empty instantiation shell). Shared
  helpers `resolveGenericReceiverDecl` + `installReceiverBinders`.

- **Phase 3 — DONE (checker satisfaction; commit `64d1b22f`).** Two parts:
  (a) `populateInstantiatedStruct` substitutes the placeholder's abstract methods onto
  each instantiation, so `Box[int]` carries `Get() int` and `b.Get()` resolves via the
  normal concrete method path. Required extending `substituteTypeParams` to recurse
  into `TYP_FUNC` (it previously left func params/results untouched).
  (b) `genericImplSatisfies` (types_assignable.bn) makes a concrete instantiation
  satisfy the interface its generic type impls, by substituting the impl's receiver
  binders with the instantiation's concrete args — injected into both interface-value
  assignability scans (raw + managed). So `impl @Cursor[T] : Iter[T]` makes every
  `@Cursor[τ]` assignable to `@Iter[τ]` with no per-instantiation impl record (chose
  substitution-aware matching over eager on-demand `@Impl` synthesis — avoids the
  ordering hazard, since these checks run in pass 2 after collection).

**Remaining:**
- **Phase 3c — constraint-path satisfaction.** `typeSatisfiesConstraint`
  (`check_generic.bn`) has its OWN `c.Impls` scan using concrete `t.Identical(rec.RecvType)`;
  it does not yet handle a generic-receiver impl, so `func f[I Iter[int]](it I)`
  instantiated with `Cursor[int]` isn't accepted. Its receiver-kind semantics differ
  from assignability (constraint satisfaction vs `receiverAssignable`), so it needs a
  tailored substitution-aware branch. Secondary to the interface-value use.
- **Method-population ordering hazard** (noted in the code): a type instantiated before
  its methods are collected gets an incomplete method set; concrete instantiations
  arise in pass 2 after collection, so it hasn't bitten, but a lazy/post-collection
  population would harden it.
- **Phase 4 — IR-gen: LOCAL case DONE; cross-package IN PROGRESS.** The feature now
  runs for same-package generic types across every host mode.
  - **4.1 (commit `5d135bac`)** — skip generic-receiver methods in the concrete-method
    passes. Key correction to the plan: a generic-receiver method has **empty
    `d.TypeParams`** (binders come from the receiver), so the existing
    `len(d.TypeParams) > 0` skip does NOT catch it — left alone it resolves the receiver
    with `T` unbound (→ int) and mints a malformed `<pkg>..<Method>` name. New helper
    `receiverGenericInstBase` (mirrors the checker's `receiverBaseInstantiation`) keys
    the skip on the *receiver* shape; new file `gen_generic_method.bn`.
  - **4.3 (commit `0a700cd2`)** — emit method bodies per instantiation. New GenCtx
    registry (`GenericMethodDecls/…Pkgs/…Files`); `ensureInstantiatedStruct` calls
    `ensureInstantiatedMethods` when it registers a `(decl, args)` struct. Each method
    lowers like a generic function (subst context + receiver→Params[0] + genFunc), named
    `<struct-inst>.<method>` — exactly the key the direct-call site derives, so body and
    call agree. Conformance 514/516/522 (pointer/managed recv, mutating-param method,
    bound-`T` return, multi-param `Pair[K,V]`).
  - **4.2 (commit `854618af`)** — on-demand `ImplInfo` at the boxing site
    (`wrapAsIfaceValue`), mirroring `ensureAnyImplInfo`: when the receiver name carries
    the `__bn_inst__` marker, `ensureGenericImplInfo` mints the (recv-inst, iface-inst)
    row + ancestor closure via `makeOwnMethodsImplInfo`. `MethodFuncs` match the emitted
    bodies; downstream vtable/SatEntry/TypeInfo is name-keyed (no parallel emit).
    Conformance 447 (single-return vtable dispatch) and 448 (`Next() (T,bool)` through
    `@Iter[int]` — the managed-iface multi-return ABI shape).
  - **4.4 — cross-mode verified:** `builder-comp` (x64 LLVM), `builder-comp-int` (VM),
    `builder-comp-comp` (self-compile) all green for 447/448/514/516/522. `arm32_linux`
    cannot **execute** locally (no `qemu-arm` on this macOS host) but **cross-compiles**
    cleanly with correct ILP32 IR: 3-slot vtable, `{i32,i1}` multi-return, `align 4`.
    Execution deferred to CI. My changes add no target-specific code (they reuse
    `makeOwnMethodsImplInfo` → the same target-parameterized RTTI/vtable machinery
    non-generic impls use), so no new target-width hazard.
  - **Cross-package — IN PROGRESS.** Discovered while writing the cross-package test
    (449). Three gaps: (1) parser — `.bni` body-parsing gate keyed on `len(typeParams)`,
    so a generic-receiver method body wasn't parsed (**FIXED**, commit `af9906a3`,
    `recvIsGenericInstantiation`); (2) checker — imported generic types are stashed in
    `GenericTypeDecls` with **no placeholder**, so my Phase-3 method population (reads
    `placeholder.Methods`) finds nothing → `undefined: Get` in the consumer (needs a
    generic-type-method registry keyed by (pkg, typeName), populated from imported
    `.bni` too, consulted by `populateInstantiatedStruct`); (3) IR-gen — stash imported
    generic-method decls (`gen_register_import`/`RegisterGenericDecls`) into
    `GenericMethodDecls`. (2)+(3) remain. Conformance 449 is the WIP driver.

## 1. What we are building

Let a generic type carry methods and satisfy interfaces — the missing piece that
makes `interface Iterator[T]` / `Container[T]` *implementable* (today declarable-only),
unblocking the natural `Iterator`/`Iterable` shapes for `pkg/stdx/containers`. Design:

- **Method on a generic type:** `func (it *Cursor[T]) Next() (T, bool)` — the
  receiver's `[T]` **binds** the type's parameters as fresh names; constraints are
  **inherited** from the type declaration (not restated); count == arity; no method
  type parameter of its own.
- **Parameterized-receiver impl:** `impl *Cursor[T] : Iterator[T]` — binds `T`;
  coverage checked **abstractly** at the impl decl; the concrete vtable + satisfaction
  are materialized **per monomorphized instantiation**.
- **Still forbidden:** method-level type params (`gen.no-generic-methods`) and
  specific-instantiation / conditional impls (`impl Cursor[int] : …`,
  `gen.no-conditional-impls`).
- **Dispatch:** constraint-path calls stay direct (no vtable); the interface-value
  path builds a `(Cursor[int], Iterator[int])` vtable. No run-time generic dispatch.

## 2. Current state (recon + review-corrected)

**Accurate & verified (do not redo):**

- **No parser/grammar change.** `func (it *Cursor[T]) Next()` and
  `impl *Cursor[T] : Iterator[T]` already parse to `TEXPR_POINTER→TEXPR_INSTANTIATE`
  (`parse_func.bn:15`, `parse_decl.bn:104`, `parse_type.bn:352`); the interface ref
  `Iterator[T]` parses too (`parse_decl.bn:146`). The parser does not distinguish
  `[T]` from `[int]`.
- **The syntactic block is `resolveMethodReceiver`** (`check_decl_func.bn:209`): it
  peels only POINTER/MANAGED/CONST/PAREN (:212-221) and errors on a non-`TEXPR_NAMED`
  base (:222); a `TEXPR_INSTANTIATE` receiver falls through. `T` inside would hit
  `errUndefined` (`resolve_type.bn:170`).
- **`gen.no-generic-methods` (method's own `[U]`) stays** at `collectMethodDecl`
  (`check_decl_func.bn:137`); keep `TestCheckGenericMethodRejected`
  (`check_decl_func_test.bn:625`).
- **The §12.4 *constraint-check* half is closed** — `checkInstantiationConstraints`
  (`check_generic_type.bn:97`, called at :185) enforces type-arg constraints at
  struct/interface instantiation (conformance `spec/12-generics/034,038,039`). **But
  the *impl-satisfaction* half the feature needs is NOT part of it and does not exist
  yet** — so "§12.4 closed" is only half-true; the spec (`12-…md:136-143`) explicitly
  notes `gen.impl.generic-recv` makes the remaining half load-bearing.
- **Non-generic type satisfying a generic interface already works**
  (`impl @IntBox : Container[int]`; conformance 451/452/453/454/597/768/769) — orthogonal,
  no work. The new case is specifically a **generic-type receiver**.
- **Instantiation-ready, no change:** mangling (proven by
  `mangle_test.bn:150 TestFuncNameMethodOnInstantiation`), and the *name-keyed*
  downstream — `CollectSatEntries` (`data_satentry.bn:45`), `CollectTypeInfoDescs`
  (`data_typeinfo.bn:92`, note it reads `RecvTyp.SizeOf()` so `RecvTyp` must be the
  populated instantiated struct), VM `lowerImplVtables` (`vm/lower.bn:248`) — all fall
  out **once a concrete `ImplInfo` row exists with `RecvTypeName="Cursor__bn_inst__int"`
  and a laid-out `RecvTyp`**. Instantiated-struct **dtors** are emitted by
  `generateDtors` (`gen_dtor_emit.bn:72-87`, `IsLinkOnce`) as
  `__dtor_Cursor__bn_inst__int`, and `implDtorSlotSym` (`emit_impls.bn:329`) fills
  vtable slot 0 from that — **provided the instantiated struct is registered before
  `generateDtors` runs**.
- **No type-assertion path exists at all** (no `TypeAssert` AST node); the SatEntry
  *reader* is future work. Interface-value **construction** uses `findImplVtableName`
  (`gen_iface_vtable.bn:30`) — the path the feature needs — so the missing reader is
  genuinely out of scope.
- **BUILDER:** parser accepts the syntax, but the pinned `bnc-0.0.10` checker rejects
  the receiver form → **nothing in `cmd/bnc`'s tree may use the feature until a BUILDER
  bump**; `pkg/stdx/containers` (outside bnc's tree) may adopt once shipped.

**The crux the review corrected — a parameterized impl is ABSTRACT, materialized
ON-DEMAND:**

`collectImplsFromDecl` runs **once per impl decl at module-collection time**
(`gen_module.bn:322`), before any instantiation is known. `impl *Cursor[T]:Iterator[T]`
has an abstract binder `T`; the concrete family (`Cursor[int]`, `Cursor[string]`, …) is
only known where each `@Iterator[X] = c` boxing site or constraint call is lowered. So
the per-instantiation `ImplInfo`/vtable/satisfaction **cannot** be produced statically
in `collectImplsFromDecl` (the mistake in the first draft). The compiler already has
the right precedent: **`ensureAnyImplInfo` (`gen_iface.bn:243-249`) synthesizes a
missing `(T, any)` `ImplInfo` on demand at the boxing site.** The parameterized impl
must reuse that model — synthesize the concrete `ImplInfo` (and the checker-side
concrete `@Impl` for assignability) keyed on the concrete receiver known at the
box/constraint-call site.

**Two concrete-method passes that will mis-handle a generic-type method (must skip
it):** `gen_module.bn:370/433` (`methodSig` + `genMethod`) and
`gen_register_import.bn:119` both take the `d.Recv != nil` non-generic branch and would
resolve `*Cursor[T]` with `T` unbound → garbage receiver + malformed name. Generic-type
methods must be skipped there (as generic funcs are), their bodies emitted only via the
on-demand driver.

**Net:** the work is ~3 checker changes + on-demand concrete-`@Impl` synthesis
(checker, for assignability) + on-demand `ImplInfo` synthesis + net-new
receiver-promoting method-body emission (IR-gen) + 2 skip sites. No parser, mangler,
vtable-emit, SatEntry-emit, or VM-lowering edits.

*Change log vs. first draft:* moved per-instantiation `ImplInfo`/satisfaction from
static `collectImplsFromDecl` to on-demand synthesis (mirroring `ensureAnyImplInfo`);
added the body-check `FuncType.TypeParams` requirement; added the two skip sites; added
the dtor-registration ordering dependency; corrected the fabricated
`vtable_inject.bn` PointerSize citation (real width sites use `IntSize` in
`data_typeinfo`/`data_satentry`); narrowed the §12.4 claim; flagged the binder-shadowing
asymmetry; added the imported-`.bni` registration site and the arm32 test.

## 3. Implementation phases (edit-site level)

**Greenness / landing:** land **Phases 1–3 together** (one green commit or a tight
series), not as three independently-green commits — accepting the syntax (Phase 1)
without the on-demand emission (Phase 4) is inert *only* because nothing in-tree uses
it and no runtime test is added before Phase 4; any use in between type-checks but
mis-compiles. Phase 4 turns the feature on and lands with the Phase 5 conformance
tests. Checker-before-IR-gen ordering is sound (IR-gen consumes `InstDecl`/`InstArgs`
and the instantiated method set).

### Phase 0 — Representation decision (settle before coding)

**(a) Where do a generic type's abstract methods live?** They must be reachable by
**three** consumers: `checkFuncDecl`'s body pass (`named.LookupMethod`,
`check_decl_func.bn:344`), `checkImplCoversInterface` (`named.LookupMethod`,
`check_impl.bn:137`), and per-instantiation substitution
(`populateInstantiatedStruct`). Two candidates:

- **Candidate A (preferred if feasible):** `AddMethod` the abstract method onto the
  generic decl's **abstract base named `Type`**, with the method's
  `FuncType.TypeParams` set to the receiver binders. Then both `LookupMethod` sites
  work unchanged, and `checkFuncDecl`'s re-install loop (:368-370, which iterates
  `ft.TypeParams`) puts `T` in scope for the body. **Verify first** that a generic
  type decl has an attachable abstract base `Type` with a `.Methods` slot (check
  `preRegisterTypeNames` `check_decl.bn:294` and `resolveNamedTypeExpr` on a bare
  generic name); if it does, this is the least-code path.
- **Candidate B (fallback):** a decl-keyed `GenericTypeMethods : declPtr → @[]@Method`
  side-map, plus new abstract-receiver branches in **both** `checkFuncDecl` (body
  lookup) and `checkImplCoversInterface` (coverage lookup). More edit sites; only if A
  is infeasible.

Whichever: the method's `FuncType.TypeParams` MUST carry the receiver binders (or the
body pass can't scope `T` — `MakeFuncType` doesn't set them today).

**(b) On-demand concrete materialization** is the architecture for satisfaction (both
checker assignability and IR-gen vtable), mirroring `ensureAnyImplInfo`. Decide the
cache key (concrete `RecvType` + `Iface` names) and the two trigger sites (checker:
`canAssignToManagedInterfaceValue`; IR-gen: the boxing site `wrapAsIfaceValue` +
the constraint-call lowering).

### Phase 1 — Checker: a method on a generic type

Edit sites — `check_decl_func.bn`:
1. `resolveMethodReceiver` (:209): extend the peel loop to a `TEXPR_INSTANTIATE` base;
   resolve `Base` to the generic decl (`lookupGenericTypeDeclPkg`,
   `check_generic_type.bn:325`); verify each `TypeArg` is a bare binder identifier of
   the correct arity; install the binders as `TYP_TYPE_PARAM` (mirror
   `installTypeParamScope`, `resolve_type.bn:305`) with **constraints inherited from
   `decl.TypeParams[i].Constraint`** (`resolveTypeParamConstraint`, :322).
2. Build the method `FuncType` with `FuncType.TypeParams = binders` and register it so
   `checkFuncDecl` finds it (per Phase 0(a)). The body pass then re-installs the
   binders (:368-370) and body calls like `t.Compare(…)` type-check via the existing
   `tryTypeParamMethodCall` (`check_method.bn:98`).
3. Keep the method-own-`[U]` rejection (:137). Diagnostics: binder-count ≠ arity; a
   `TypeArg` that resolves to a concrete type (specific-instantiation — see the Risk on
   shadowing); non-identifier `TypeArg`.

Land with checker-unit positives (method on `Cursor[T]`; `Box[T Orderable]` body calls
`T.Compare`) and negatives (binder-count; concrete-arg receiver; method `[U]`).

### Phase 2 — Checker: parameterized-receiver impl (abstract coverage)

Edit sites — `check_impl.bn`:
1. `collectImplDecl` (:20): the current `resolveTypeExpr(c, d.TypeRef)` at :25 fails
   with `T` unbound — so **bind the receiver binders first** (Phase-1 helper), then
   form the abstract `*Cursor[T]` receiver and resolve the interface refs (`Iterator[T]`,
   `T` now in scope). Reject a concrete-arg receiver (`impl Cursor[int]`) — a *new*
   rejection with no existing test; note the boundary against the valid
   `impl IntPair : Pair[int]` form (conformance 035): the difference is a **generic
   type** used as the receiver base vs. a concrete type.
2. `checkImplSatisfaction`/`checkImplCoversInterface` (:91/:132): coverage must look
   the receiver's methods up abstractly. Per Phase 0(a): if Candidate A, `LookupMethod`
   on the abstract base already returns the abstract methods (no change here); if
   Candidate B, add the abstract-receiver branch. Match with `methodSigSatisfies`
   (:165) holding `T` abstract (same binder instances on both sides);
   `substituteSelf` (`check_self.bn:22`) unchanged. Object-safety of a `Self`-using
   iface method rides existing machinery.

Land with checker-unit positives/negatives.

### Phase 3 — Checker: per-instantiation method set + on-demand satisfaction

1. `populateInstantiatedStruct` (`check_generic_type.bn:429`): after fields, substitute
   the abstract methods (binder→`InstArgs`) and `AddMethod` onto the instantiated
   `TYP_NAMED` (mirror `populateInstantiatedInterface:295`). Now
   `LookupMethod("Next")` on `Cursor[int]` returns `Next() (int,bool)` — the
   constraint-path direct call resolves (`check_method.bn:87-95`).
2. **On-demand concrete `@Impl` synthesis for assignability.** `var it @Iterator[int]
   = c` reaches `canAssignToManagedInterfaceValue` (`types_assignable.bn:329`), which
   iterates `c.Impls` requiring `implSatisfiesInterface` (concrete Pkg/Name match,
   :400) **and** `receiverAssignable` (`srcBase.Identical(dstBase)`,
   `check_method.bn:376`). An abstract impl matches neither. So when a parameterized
   impl exists and `Cursor[int]`/`Iterator[int]` are the concrete pair, **synthesize a
   concrete `@Impl{*Cursor[int] : Iterator[int]}` and register it** (append to
   `c.Impls`, deduped) so both predicates match. The `Impl` struct (`types.bni:1054`)
   has no `InstDecl`/`InstArgs` — this needs the substituted concrete `RecvType` +
   `Interfaces`. **Ordering hazard:** assignability is checked mid-typecheck while
   instantiations are discovered lazily; a `Cursor[int]` first mentioned only as an
   `@Iterator[int]` target may have no record yet — the synthesis must fire from the
   assignability check itself (on-demand), not rely on prior discovery.

Land with checker-unit tests (constraint-path call resolves; `Cursor[int]` assignable
to `@Iterator[int]`).

### Phase 4 — IR-gen / codegen / VM: make it run

1. **Skip generic-type methods in the concrete-method passes** —
   `gen_module.bn:370` (`methodSig`) and `:433` (`genMethod`), and
   `gen_register_import.bn:119`: a decl with `d.Recv != nil` whose receiver is a
   `TEXPR_INSTANTIATE` must be skipped (like generic funcs), else it emits a garbage
   receiver + malformed name. (This is why Phase 4 must ship with the on-demand
   emitter, not after it.)
2. **On-demand `ImplInfo` synthesis** at the box/constraint-call site, mirroring
   `ensureAnyImplInfo` (`gen_iface.bn:243-249`): when `@Iterator[int]` is built from a
   `Cursor[int]` (or a constraint call needs it), synthesize
   `ImplInfo{RecvPkg, RecvTypeName:"Cursor__bn_inst__int", RecvTyp:<the
   ensureInstantiatedStruct result>, IfaceName:"Iterator__bn_inst__int",
   DtorFuncName:"…__dtor_Cursor__bn_inst__int", …}` and register it (deduped,
   per-consumer, `IsLinkOnce`). Downstream (vtable, SatEntry, TypeInfo, VM lowering) is
   then automatic. **Guarantee** `ensureInstantiatedStruct` (`gen_generic.bn:418`) has
   run for `Cursor[int]` (so `RecvTyp` is laid out for `TypeInfo.SizeOf` and the dtor
   is registered before `generateDtors`, `gen_module.bn` end).
3. **Emit the method bodies per instantiation (net-new).** Combine
   receiver-promotion-to-`Params[0]` (as `genMethod`, `gen_method.bn:114`) + the
   type-param substitution context (as `ensureInstantiated`, `gen_generic.bn:57-82`) +
   the **method-shape name** `<pkg>.Cursor__bn_inst__int.Next` (via
   `methodFuncName`, NOT `instantiationMangledName` which omits the `.Next` segment) +
   **method `FuncSig` registration under the `.Next` key** (else `genMethodCall`'s
   `lookupFuncParams`/`lookupFuncExists`, `gen_method.bn:172/219`, default to
   `TypInt()` → silent wrong-code). `genFunc` ignores `Recv`, so the receiver
   promotion must be explicit. Fire this from the on-demand path (2) so a `Cursor[int]`
   use pulls in its methods, deduped via `EmittedInstantiations`.
4. **Verify both dispatch paths + cross-mode.** Constraint-path lowers to a direct
   `EmitCall` to the mangled method (`gen_method.bn:268`, `recv.Typ` already concrete
   post-substitution — no call-site `TYP_TYPE_PARAM` handling). Interface-value path
   lowers to `EmitCallIfaceMethod` reading the vtable slot
   (`gen_iface_dispatch.bn:72`). VM re-lowers the per-instantiation vtable
   (`vm/lower.bn:248`); generic-inst impls stay out of the native pkg descriptor
   (`recvTypeIsGenericInst`). **Target-width:** confirm the RTTI word-size math
   (`data_typeinfo.bn`/`data_satentry.bn` use `types.GetTarget().IntSize`) and the VM
   vtable-extent path use the target word size, not a literal — verify the exact site
   during implementation (do not cite a fixed line here).

### Phase 5 — Tests (land with Phase 4)

- **Positive (conformance, all modes/backends):** `Cursor[T].Next` method;
  `impl *Cursor[T] : Iterator[T]`; use via constraint (direct dispatch); use via
  `@Iterator[int]` value (vtable dispatch); **a dedicated `Next() (T, bool)` through
  `@Iterator[int]` test** — this is precisely a *managed-iface multi-return*, the shape
  the ABI matrix stresses (`conformance/matrix/abi/managed-iface-multi-return*/`) and
  the single most likely backend/arm32 failure point; multi-param `HashMap[K,V]`;
  constraint **inheritance** (`Box[T Orderable]` calls `T.Compare`); interface
  **extends** (`Iterator[T] : Foo[T]`); cross-package parameterized impl (drives
  imported-generic method+dtor emission).
- **Negative (checker, Phases 1–2):** method-level `[U]`; binder-count ≠ arity;
  specific-instantiation `impl Cursor[int]`.
- Run the full unit matrix + conformance modes **including
  `builder-comp_arm32_linux`** (the managed-iface-multi-return path is target-sensitive).

## 4. Key risks / correctness invariants

- **On-demand, not static** — the per-instantiation `ImplInfo` and concrete `@Impl` are
  synthesized where the concrete type is known (box/constraint/assignability sites),
  mirroring `ensureAnyImplInfo`. A static `collectImplsFromDecl` row is wrong (no
  concrete arg there). This is the single biggest correctness point.
- **Body-check reachability** — the method `FuncType.TypeParams` must carry the receiver
  binders AND the FuncType must be `LookupMethod`-reachable, or the body never
  type-checks (or `T` is undefined).
- **Concrete `@Impl` for assignability** — `implSatisfiesInterface`/`receiverAssignable`
  are concrete-only; an abstract impl won't make `Cursor[int]` assignable to
  `@Iterator[int]`. Synthesize the concrete record.
- **Skip sites** — keep generic-type methods out of `gen_module.bn:370/433` and
  `gen_register_import.bn:119`.
- **dtor / struct-registration ordering** — the instantiated struct must be registered
  (`ensureInstantiatedStruct`) before `generateDtors`, so vtable slot 0 (the dtor
  handle) and `TypeInfo.SizeOf` resolve.
- **Binder-vs-concrete shadowing asymmetry (design-confirm)** — the rule "a bracket
  name that resolves to a type ⇒ specific-instantiation ⇒ reject" differs from function
  type-params, which `defineType` binders **unconditionally** (shadowing a same-named
  top-level type). So `type T struct{}` at package scope would make
  `func (c *Cursor[T]) …` a rejected specific-instantiation. The spec chose
  reject-on-resolve; **flag this asymmetry explicitly** and confirm it's intended (it is
  collection-order-sensitive under `c.Scope`).
- **Two registration sites** — `GenericTypeMethods`/abstract methods must be populated
  for **imported** generic types (`bni_scope.bn:49`) too, not only local
  (`check_decl.bn:294`), for cross-package parameterized impls.
- **Vtable/SatEntry ride existing machinery** — produce the concrete `ImplInfo`; do not
  add a parallel emit path (drift risk).
- **Method-level type params stay forbidden**; **§12.4 impl-satisfaction half is new**
  (constraint-check half is closed — build on it).
- **BUILDER** — keep the feature out of `cmd/bnc`'s tree until a BUILDER bump.

## 5. Spec / doc updates

- **Narrow, don't blanket-close, the §12.4 note**
  (`docs/spec/12-generics-and-enumerations.md:126-145`): the *constraint* check at
  struct/interface instantiation is enforced (`checkInstantiationConstraints`; tests
  034/038/039); the *impl-satisfaction* half is delivered by this feature.
- **Repo-wide sweep of the stale gap wording** — `plan-stdx-containers.md:336-342` also
  describes `gen.satisfy.struct-iface-unchecked` as open (now false); fix it too. Grep
  the repo for `struct-iface-unchecked` / "only at generic-function instantiation"
  before claiming the sweep done.
- On landing: narrow `gen.no-generic-methods`; mark `gen.method.generic-recv` /
  `gen.impl.generic-recv` implemented; update `plan-stdx-containers.md` §7 + the
  container `.bni` breadcrumbs to the shipped `impl *Cursor[T] : Iterator[T]` migration.

## 6. Cross-references

- Spec: §12.1 (`gen.no-generic-methods`, `gen.method.generic-recv`,
  `gen.impl.generic-recv`, `gen.no-conditional-impls`), §11.3, §10.1/§10.4, §12.3
  (`gen.mono`/`gen.mono.constraint-call`), §12.4 `gen.satisfy` (doc partly stale — §5),
  §7.13.14 `type.layout.satisfaction` / §11.12 `iface.rtti`.
- Design: `claude-notes.md` "Methods on generic types … DECIDED 2026-07-05".
- Edit-site map (checker): `check_decl_func.bn:{137,209,344,368}`,
  `check_impl.bn:{20,25,91,132,137,165}`, `check_generic_type.bn:{97,143,261,325,429}`,
  `check_method.bn:{87,98,363,376}`, `types_assignable.bn:{329,400}`,
  `resolve_type.bn:{170,305,322}`, `check_self.bn:22`, `check_decl.bn:294` +
  `bni_scope.bn:49`.
- Edit-site map (IR-gen): `gen_module.bn:{322,370,433}`, `gen_register_import.bn:119`,
  `gen_iface.bn:{243}` (`ensureAnyImplInfo` precedent), `gen_iface_vtable.bn:30`
  (`findImplVtableName`), `gen_method.bn:{36,101,114,172,219,268}`,
  `gen_impl.bn:{42,50,107}`, `gen_impl_recvname.bn:39`, `gen_generic.bn:{57,418}`,
  `gen_dtor_emit.bn:72`, `gen_iface_dispatch.bn:72`.
- "No change" (verified): `mangle/*`, `codegen/emit_impls.bn` (vtable emit),
  `ir/data_satentry.bn`, `ir/data_typeinfo.bn`, `vm/lower.bn` — all name-keyed off the
  `ImplInfo` row.
