# Plan: check each generic body per concrete instantiation

**Status:** design chosen by the user 2026-09-28 ("B"), not started; the proposed spec rule was reviewed 2026-09-28 (§11), decisions pending.  Part of `plan-constant-evaluator.md` ("Per-instantiation checking"); fixes the claude-todo entries "A generic struct's `[sizeof(T)]` field has the same length in every instantiation" and "Polymorphic recursion in a generic function crashes the compiler".  The text below is the design as drafted by the 2026-09-28 design workflow (read-only code mapping; nothing built); paths are relative to `pkg/binate/`.  Its "decisions" section is still open.

I only read code; nothing was built or run. Paths are relative to `/Users/vtl/binate/temp-binate-4/pkg/binate/`.

## 0. Principle: bindings only ever touch clones

**Invariant:** anything resolved or checked with a type parameter bound to a concrete type works on a per-instantiation **clone** of the AST it reads. The original generic AST only ever carries annotations that do not depend on the bindings.

That one rule covers every per-node stamp:

- **Checker stamps:**
  - `Expr.ResolvedTypeID` (check_expr.bn:22)
  - `Decl.ConstID` (check_constval.bn:169)
  - `TypeExpr.LenVal/LenKnown` (resolve_type.bn:90, check_expr_composite.bn:58)
  - `Element.KeyVal/KeyKnown` (check_expr_composite.bn:234)
  - `Decl.Captures` (check_capture.bn:161)
- **IR-gen stamp:** `d.Name` on a function literal's `DeclRef` (gen_func_lit.bn:83).

I recommend cloning over per-instance side tables keyed by node. Side tables would add a second index to all six fields and to every future stamp, and IR-gen's 39 `ExprType` sites would all need to know which instance they are in. With clones, IR-gen reads a clone's annotations exactly as it reads an ordinary function's.

## 1. Data structures

**New `ast/ast_clone.bn` (~300 lines, plus tests):**
- `CloneFuncDecl`, `CloneTypeExpr`, `CloneExpr`, `CloneStmt`.
- A deep copy of Params, Results, Body, TypeRef, and the `DeclRef` of nested function literals.
- The clone **resets** `ResolvedTypeID`, `ConstID`, `LenKnown`, `KeyKnown` and `Captures`.

**New `check/check_instances.bn`:**
```
type FuncInstance struct {
    Decl   @ast.Decl          // generic decl (method: the method decl)
    Args   @[]@types.Type     // concrete, no TYP_TYPE_PARAM anywhere
    Clone  @ast.Decl          // CloneFuncDecl(Decl)
    Sig    @types.Type        // re-resolved from Clone under bindings
    Recv   @types.Type        // generic-type instance, for methods
    Site   token.Pos          // first instantiation site
    Parent @FuncInstance      // chain, for diagnostics / depth
    Depth  int
    Checked bool
}
type GenericHome struct { Decl @ast.Decl; Scope @Scope; Pkg @[]char; BodyErrs bool }
```

**Checker state (`checker_state.bn`, currently 415 lines):**
- `FuncInsts` (vec, looked up by decl plus `IdenticalStrict` args, like `lookupCachedInstantiationEntry` at check_generic_type.bn:424)
- `InstQueue`
- `GenericHomes`
- `CurInst`

This is the only file near the 500-line cap, so the new fields may need a split.

**`GenericInstantiation`** gains:
- `InstTypeRef @ast.TypeExpr`, a clone for populate;
- `MethodsQueued bool`.

**Accessors for IR-gen (`check/check_instances.bn`):**
- `(c) FuncInstance(d, args) @ast.Decl`
- `(c) MethodInstance(recvInst, m) @ast.Decl`
- `(c) InstanceTypeRef(d, args) @ast.TypeExpr`

## 2. Algorithm

**Recording the home scope.**
- `checkFuncDecl` (check_decl_func.bn:385-406) records a `GenericHome` for each generic function or method whose body it checks. The home is the scope it runs in plus `curPkgPath`.
- `BodyErrs` is true if the abstract check reported any error in that body.
- The body check is factored out into `checkFuncBody(c, d, ft)` so the instance pass can reuse it.

**Instance records (check_generic.bn:22-89).** `instantiateGenericFunc` changes as follows:
- If every argument is concrete (a new `types.ContainsTypeParam`), it gets or creates the `FuncInstance` and returns `inst.Sig`.
- It sets `Parent = c.CurInst` and enqueues the instance.
- The signature comes from re-resolving the **clone's** Params/Results in the home scope with the binders bound to the arguments (`defineType`), not from `substituteTypeParams`. This fixes problem (1): `[sizeof(T)]uint8` becomes `[8]uint8`.
- `copyImportedGenericMethods` (check_generic_backfill.bn:79-113) already works this way. `copyPlaceholderMethods` (:44-55) for local generics switches to the same path, which removes the local-vs-imported difference.
- The same routing applies to function values `&F[T]` (check_addr.bn:70-91), method values, and `defer`.

**Generic types.**
- `populateInstantiatedStruct` / `populateInstantiatedInterface` (check_generic_populate.bn:40-98, 132-171) resolve `entry.InstTypeRef`, a clone. This also removes the stale-`LenVal` hazard (§6a in the second map).
- When a concrete instance is created, the first time only, each method is enqueued as a `FuncInstance` with the receiver's binders bound to the arguments.

**Draining the queue.**
- `drainInstances(c)` runs at the end of `CheckPackage` (checker.bn:175), and in the REPL's check entry points.
- For each instance: skip it if its home is missing (bnlint's `CheckPackageDecls` skipped the body) or `BodyErrs` is set, since the abstract check already failed.
- Otherwise:
  1. save `Scope`, `curPkgPath`, `FuncRet`, `InFunc`, `Iota`, the capture frames and `CurInst`;
  2. set `c.Scope` to the home scope and `pushScope`;
  3. define the binders as the concrete arguments;
  4. run `checkFuncBody(c, inst.Clone, inst.Sig)`;
  5. restore everything.
- Transitive closure needs no edge graph. Inside an instance body, `G[T]` resolves `T` to `int64` and reaches `instantiateGenericFunc` with concrete arguments, which enqueues `G[int64]`.
- Draining at the end of each package is enough, because a root can only name generics from already-checked packages, or from the same package, whose declarations have all been checked by then. The second map says the fixpoint must run once after every package; that is not needed here.

**Recursion.**
- `Depth = Parent.Depth + 1`, with the same cap of 128 as `GenericInstDepth` (check_generic_type.bn:232).
- Past the cap, report at the root site: "instantiation F[@@…int] nested too deeply (polymorphic recursion?)", with the chain.
- IR-gen then only emits recorded instances, which also closes its unbounded recursion.

**What this catches.** Everything that depends on the instantiation is now checked concretely, by the same code that checks non-generic code:
- constants: `recordConst` reports `ERR_OVERFLOW` and division by zero;
- array lengths: `evalArrayLen`;
- composite-literal counts;
- `bit_cast` sizes: `checkBitCastShapes` stops sizing T as a pointer;
- cast validity: check_cast_safe.bn:26-32 no longer skips concrete types;
- `requireSizedType`.

## 3. Checking the generic body itself (abstract mode)

- `DEPENDENT` keeps meaning "checked per instantiation". The instance pass is what actually does that.
- Dependent arrays:
  - `substituteTypeParams` (check_generic.bn:322) must keep `ArrayLenDependent`. It now only substitutes abstract-to-abstract, plus impl matching.
  - `Identical` (types_identical.bn:37,154) must never treat a dependent array as equal to a concrete one.
  - In an abstract body, assignability between arrays with the same element type where either length is dependent is **deferred**, since the instance check decides it.
  - `checkArrayLit` skips the count check when the length is dependent (check_expr_composite.bn:209-213).
- Fix §6b of the second map: replace `containsByValueTypeParam` and `arrayLenDependent` with one `layoutDependsOnTypeParam` that also walks struct fields and generic instances. Otherwise `sizeof(Box[T])` is recorded as OK with the placeholder 0.
- Impl matching with concrete arguments (types_assignable_iface.bn:257,262) still substitutes. If a receiver contains a dependent array, report an explicit error rather than produce `[0]`.

## 4. Error reporting

- **Where.** At the node in the generic source, with the context appended: `… (in F[int64], instantiated at main.bn:12:5 via G[int64])`. The root site is the one that matters, because the user may not have written the generic.
- **De-duplication.** One report per (source position, base message). The first instance is named, plus "and N more". For example, `sizeof(T)*100` overflowing for int32 and int64 is reported once.
- **Divergence.** A monomorphic check can reject something the constraint-based check accepted. Possible causes: constraint-method calls resolved on the concrete type, receiver smoothing, T bound to a `readonly` or interface-value type.
  - Diagnostics in the known per-instantiation classes (section 2) are user errors.
  - Any other diagnostic is reported as `internal error: instantiation F[X] fails a check its generic body passed: <msg>`, so it fails loudly and gets fixed rather than being accepted.

## 5. IR-gen: it only reads

- **Function bodies.** `ensureInstantiated` (gen_generic.bn:62-149) uses `c.FuncInstance(d, args)` as its synthetic decl's Params/Results/Body. A missing record is an internal compiler error (ICE). `ensureMethodsForInstName` (gen_generic_method.bn:95-124) does the same with `MethodInstance`.
- **Constants.**
  - `exprConstIR` (gen_constval.bn:95-108): a `DEPENDENT` value can no longer reach it, so the `evalConstIR` fallback is deleted.
  - `constFromInitializer` (:234-280) keeps only the recorded `DeclConst`.
  - `constEvalFailure` becomes a positioned ICE.
- **Array lengths.** The `evalConstIR` branch in gen_type_resolve.bn:235-251 is deleted; the clone's `LenKnown` is always set.
- **Switch cases.** The silent fallback that evaluated a case at run time (gen_flow.bn:378-391) goes.
- **Generic struct fields.** `ensureInstantiatedStruct` (gen_generic_type_inst.bn:215-224) resolves `c.InstanceTypeRef(d, args)`.
- **Other panics.** Those at gen_builtin.bn:75-78, 102-106 and 138-143, and gen_cast_value.bn:39-53, become ICEs.
- **`irgenEnv`.** Delete it if no unchecked path is left. The REPL (gen_repl.bn:173) and imported constants still need checking.
- **Kept for now.** `CurrentTypeParamNames/Types`. The clone's type expressions still say `T`. Removing this needs a resolved-type slot on `TypeExpr`, which would be a separate step.

## 6. What is deleted

- IR-gen's evaluation of `DEPENDENT` values: gen_constval.bn, gen_type_resolve.bn and the gen_flow.bn fallback. Roughly −120 lines.
- `substituteTypeParams` as the way concrete signatures are produced, both for functions and in `copyPlaceholderMethods`.
- The separate `requireSizedType` loop at check_generic.bn:82-87, which is folded into the instance check.
- IR-gen's re-resolution of generic struct fields from the shared AST.

## 7. Size, cost and risks

**Size.**
- New: about 750 lines (clone about 300, instances about 250, tests about 200).
- Changed: about 20 files, roughly +500/−250.
- Conformance tests: about 10.

**Cost.**
- One clone plus one body check per distinct (decl, args). IR-gen already emits one body per instance, so the order of growth is the same.
- Checker time grows with generic code × instances. Measure it on the gen1 self-compile, in user CPU time.
- The clones and their `ExprTypes` slots stay alive until IR-gen.

**Risks.**
1. **The clone can miss a field.** Mitigation: a unit test that clones every conformance program's generic declarations and compares printed ASTs.
2. **The abstract and monomorphic checks can disagree** (§4). Before landing commit 5, run the full conformance suite plus gen1 with instance checking enabled.
3. **REPL `TentativeMode`.** Instance errors must go to `TentativeErrors` and must not mark an instance as checked.
4. **Mixed type identities.** The checker's instance `types.Type`s must not end up mixed with IR-gen's own. Section 5 avoids this by having IR-gen resolve the clones, not import the checker's types.

## 8. Commit sequence

Each commit leaves the tree green.

1. `ast` clone functions and tests. No behaviour change.
2. Build a repro for §6a (`Buf[int32]` plus `Buf[int64]`); if it reproduces, add the conformance test, xfail markers and a todo. Then switch populate and imported-method signatures to clones (`InstTypeRef`), and make IR-gen's struct/interface instantiation use them.
3. Fix §6b: `layoutDependsOnTypeParam`, with a `sizeof(Box[T])` test.
4. `FuncInstance` records and signature re-resolution; keep the dependent flag and fix identity; defer dependent assignability in abstract bodies; guard the literal count. This fixes problem (1). Tests: `F[int64]` with `[8]uint8` accepted, `F[int32]` with `[8]uint8` rejected.
5. Queue, drain, depth cap, error context and de-duplication, and queuing generic-type methods. This fixes problem (2). Tests: overflow in one instance, division by a dependent zero, count in one instance, polymorphic recursion, de-duplication, chained instantiation.
6. IR-gen emits the clones; delete the `DEPENDENT` paths; missing records and constant failures become ICEs.
7. `bit_cast` and cast checks in abstract bodies defer to the instance check; the IR-gen panics become ICEs.

## 9. Decisions you need to make

These change language semantics, so the spec rule in (a) needs your approval.

- **(a) Which errors and which instances.** A per-instantiation error rejects the program, for every concrete instantiation the program names, including **all** methods of each named generic-type instance, whether or not IR-gen would emit them. This needs a new rule in spec §12.3 (for example `gen.mono.check`).
- **(b) Dependent array lengths in an abstract body.** Either defer them, so errors appear only for instances that have them and a generic that is never instantiated is not diagnosed, or require equality that can be proven structurally.
- **(c) Dependent array-literal keys.** These are currently rejected (check_expr_composite.bn:228). They could now be allowed. I would leave them rejected unless you decide otherwise.

## 10. Where the maps are wrong or this approach is hard

- **No fixpoint after all packages.** The first map says one is needed; section 2 shows why draining per package is enough.
- **Array types need no length expression or scope.** The second map suggests adding them (its §5.1-2). Concrete signatures are re-resolved from clones instead, so they are not needed.
- **Per-node side tables fit this approach badly** (irgen-mono map §5.5) because there are six stamp sites; that is why section 0 uses clones.
- **The hard part is divergence.** Checking a body monomorphically is stricter in ways the constraint-based definition never promised; §4's ICE classification is there to catch it.
- **bnlint never checks imported generic bodies**, so instances of those generics are skipped under bnlint.

## 11. Review of the proposed spec rule (2026-09-28)

User decisions so far: design B ("B"); dependent array lengths in an abstract body are deferred to the
instances ("Defer sounds right"); dependent array-literal keys stay rejected for now (language-feature todo
in claude-todo.md).  The proposed rule was:

> `gen.mono.check` (Constraint) — Besides being checked against its type parameters' constraints, a generic
> function or method body is checked once for each instantiation the program names, with its type
> parameters bound to the type arguments: each generic function instantiation named in checked code
> (including in another instantiation's body), and each method of each generic type instantiation named
> there. An error such an instantiation has is a compile-time error. Instantiations nested beyond an
> implementation-defined depth are an error.

The user: "I think it works, but I'd get a focused review of it too".  A two-lens review (spec wording;
implementability) found:

**Spec / language (decisions needed):**
1. "An error such an instantiation has" is too broad.  Re-checking a whole body per instance applies
   site-dependent rules at the wrong site: `gen.satisfy` ("impl visible at the instantiation site") inside
   another package's generic (`F[K]` in L naming `G[K]`, K's impl in main, invisible from L); the same for
   `iface.construct.visible-impl` and `type.opaque` (an opaque type laid out in its own package).  Proposed
   fix: only **dependent** constructs are re-checked per instance — those whose check needs a value
   computed from a type parameter (`sizeof` / `alignof` of such a type, constants and array lengths using
   them, and the rules that consume such a length or size: array identity / assignability, literal counts,
   `bit_cast` size equality); every other rule is decided once by the abstract check.  Names resolve where
   the generic is declared; a type argument's impls, visibility and opacity are those at the root
   instantiation site; an inner instantiation with parameter-built arguments satisfies its constraint through
   the enclosing parameter's bound.
2. Cast validity that depends on T's KIND (`cast(int64, t)` with `T any`) — today check_cast_safe.bn skips
   every cast involving T and IR-gen panics on a bad instance.  Checking it per instance makes the operations
   allowed on T depend on the concrete T, against `expr.compare.typeparam` ("a type parameter is never one
   of the concrete types … regardless of its interface constraints").  Decide: abstract (constraint-based)
   or per instance.  Size-based checks fit the dependent model; kind-based ones need a separate decision.
   Also needed in the spec: `sizeof(T)` in a generic body is a constant fixed per instantiation (§6/§15).
3. Dependent array types are compared outside bodies too: signatures (already decided to be checked per
   instantiation), struct fields, method signatures, interface method signatures, and parameterized-impl
   coverage (`interface I[T any] { Get() [sizeof(T)]uint8 }` vs `impl Box[T] : I[T]` returning
   `[alignof(T)]uint8`: accepted today, wrong for a struct T).  Either every declaration of each named
   instance (signature, body, fields, method signatures, each parameterized impl's coverage) is checked per
   instance, or dependent lengths are never abstractly identical.
4. Branching on the size of T becomes impossible: `if sizeof(T) == 4 { bit_cast(T, load32(…)) } else {
   bit_cast(T, load64(…)) }` fails one branch's `bit_cast` size check for every T, and naming `Atomic[int32]`
   checks all methods.  Binate has no compile-time `if`.  Decide: an accepted limitation (document the
   pointer-cast idiom), or a rule that a branch whose condition is constant-false for the instance is not
   checked per instance.
5. "Named" needs an inductive definition (fields, alias / defined-type right-hand sides, nested type
   arguments, constraints, `.bni` files, method-instance bodies, instantiations written inside generics
   that are never instantiated).  Binate has no type-argument inference (`gen.instantiate`).
6. Depth: the spec has no translation limits.  Proposed: "the set of instantiations a program names shall be
   finite" (rejects polymorphic recursion through functions and types), plus an implementation limit
   catalogued in Ch.21 with a guaranteed minimum (128), measured as the shortest chain from a root.
7. All methods of a named type instance: the language-level reason is that a method on `Box[T]` is promised
   for every T (`gen.no-conditional-impls`), and vtable reachability is whole-program (impls in any
   package), not that IR-gen emits them all.  Consequence: generic bodies in a `.bni` are part of the API.
8. Error position: the recorded user decision (plan-constant-evaluator.md) is "reporting at the
   instantiation (with the generic body's position)"; §4 above puts the primary position in the body.
   Follow the user's decision.
9. A generic never named with concrete arguments is not checked for dependent errors, even ones that fail
   for every T — say so explicitly.

**Decided (user, 2026-09-28: "So are you going to do the spec updates or not?", taken as approving the
recommendations):** (1) only dependent constructs are checked per instantiation; names resolve at the
declaration, impls / visibility / opacity at the root instantiation site, inner instantiations satisfy
constraints through the enclosing bounds; (2) casts involving a type parameter are checked per instantiation
(the implemented deferral, conformance 1122 / 1123 / 1217 / 1220); (3) every declaration of each named
instance is checked (signature, body, fields, method signatures, parameterized impls); (4) branching on
`sizeof(T)` stays a limitation (language-feature todo filed); (5) the named set is finite, plus an
implementation limit of at least 128 in Ch.21; (6) diagnostics at the root instantiation, naming the
generic's position and the chain; (7, user 2026-09-28: "Nah, we should make it work") a type assertion /
type-switch case whose target names a type parameter is dependent (spec `iface.assert.typeparam`): a bare
`T` takes its recovery kind from the type argument (`T = @U` → `x.(@U)`, `T = U` → value copy), a written
kind applies as written; today such an assertion is rejected at the declaration, so implementing it means
deferring the target check in abstract bodies, checking it per instance, and lowering it per instance in
IR-gen.  Also from the spec-text review: uses needing a type parameter's layout (by-value var / field /
param / result, `make`, `make_slice`) are dependent (the opaque-type gate), an impl's interface-list
constraints are satisfied through the type's parameter constraints (checked once), and a new
`conf.implementation.limits` backs the chain bound.  Spec text written, not yet
landed.

**Implementation (design changes, no decision needed):**
- Blocker: interpreted mode (`bni`, the `-int` modes, `bni --test`, the REPL) and bnlint never check the
  bodies of `.bni`-loaded generics (injected stdlib packages have no `CheckPackage`), yet IR-gen
  monomorphizes them.  Record a home for every generic declaration with a body in
  `LoadPackageInterface`; check it abstractly and per instance only when something names it.
- The main program is checked with `c.Check(mainFile)`, not `CheckPackage`: add an explicit final drain
  step to every driver (bnc, bni, interp, bnlint, REPL); IR-gen asserts the queue is empty.
- Generic type instances created by substitution (`userFacing=false`), during `.bni` surface building or
  declaration collection, are real instances: queue their methods on every concrete, non-probe creation,
  and enumerate the method set at drain time (methods declared later / backfilled / at a later REPL prompt).
- REPL: keep a `Failed` flag on an instance and report again at each new naming site; de-duplicate errors
  per checking call, not globally; drop or re-arm instances created in `TentativeMode` whose declaration
  parks.
- Depth: queued methods set `Parent` too (recursion through methods); process depth-first or stop at the
  first overflow (fan-out `F[@T](); F[*T]()` is exponential breadth-first).
- Key IR-gen's lookups by an instance record stamped on the naming node, not by recomputed argument types;
  a missing home is an ICE, not a skip.
- Skip instances whose constraint check (or containing type instance's) failed, to avoid cascades.
- Deferring dependent lengths must also cover function-type identity, the instance cache key, and impl
  matching.
- The checker's per-instance population of struct fields / interface method signatures should be under the
  rule too; interface instances that only IR-gen mints (generic impl rows, imported impls) are unchecked.
- Cost: ~64 generic functions, ~15 generic types, ~140 distinct instantiations in `pkg/` + `cmd/`; small.

## Appendix: how the checker handles generics today (mapping, 2026-09-28)

The checker type-checks each generic body once, with the type parameters left abstract. The only place it does work per instantiation is generic types. For generic functions it has no record of instantiations: each call site substitutes the signature and moves on. The full set of instantiations, including transitive ones, is currently worked out only by IR-gen, lazily, as it emits bodies.

## 1. Generic function and method bodies are checked once, abstractly

- **Type parameters are introduced as abstract types.** `resolveFuncDeclType` pushes a scope and calls `installTypeParamScope` (check/resolve_type.bn:251-301). Each parameter becomes a `TYP_TYPE_PARAM` whose identity is (owner decl, index) (resolve_type.bn:316-326; types/types_identical.bn:22-30). These are stored on `ft.TypeParams`.
- **One body check.** `checkDecls` calls `checkFuncDecl` once per decl (check_decl_pass2.bn:25-27). `checkFuncDecl` puts the same `TYP_TYPE_PARAM` objects back in scope and checks the body a single time (check_decl_func.bn:385-406). No other code checks a declaration's body: `checkBlockStmt(c, d.Body)` appears only at check_decl_func.bn:406 and check_func_lit.bn:70.
- **Methods on a generic type work the same way.** `collectGenericMethodDecl` installs the receiver binders, owned by the type's decl (check_decl_func_generic.bn:95-105, 112-128). It attaches the abstract method to the type's placeholder with `TypeParams = binders`. `lookupMethodFuncType` fetches it from the placeholder for the body pass (check_decl_func.bn:288-302). A method cannot declare its own type parameters (check_decl_func.bn:139-142).
- **Per-expression records hold one value per AST node.** `checkExpr` sets `e.ResolvedTypeID = registerExprType(...)` and calls `recordConst` (check_expr.bn:21-23). `registerExprType` appends a new slot each time (checker.bn:76-99). Checking a body a second time would overwrite the IDs, and IR-gen reads them (cmd/bnc/compile.bn:26-30; irgen/gen_constval.bn:95-103). This rules out simply re-checking bodies per instantiation.
- **Dependent constants report nothing.** `recordConst` reports only `ERR_*` statuses (check_constval.bn:220-225). `DEPENDENT` passes through silently. `checkerEnv.Type` returns `DEPENDENT` for type-parameter layouts (check_constval.bn:51-53). `evalArrayLen` returns `(0, false, dependent=true)` (eval_const_int.bn:42-50).

## 2. Calls and explicit instantiation

- **No type-argument inference.** A generic function called without brackets is an error (check_expr.bn:283-291). `F[int64]` is an `EXPR_INSTANTIATE_OR_INDEX` node. `checkInstantiateOrIndex` sends it to `instantiateGenericFunc` for both local and `pkg.F` heads (check_expr_access.bn:21-42). `F[int64](x)` goes through `checkCallExpr`: `checkExpr(e.X)` returns the substituted FUNC, which has no `TypeParams`, and the arguments bind against it as for an ordinary function (check_expr.bn:276, 344-351).
- **Type arguments are resolved** by `typeArgFromExpr` (check_generic.bn:176-262).
- **Order inside `instantiateGenericFunc`** (check_generic.bn:22-89):
  1. resolve all the arguments;
  2. check constraints against the substituted constraint (lines 48-56);
  3. substitute params and results with `substituteTypeParams`;
  4. run `requireSizedType` on each substituted type (lines 82-87).
- **Nothing about a function instantiation is recorded.** There is no cache, no list and no per-call record. The only trace is the instantiated FUNC type stored on the node's `ResolvedTypeID`.
- **Root cause of problem (1).** `substituteTypeParams` rebuilds an array as `MakeArrayType(subst(elem), t.ArrayLen)` (check_generic.bn:322-323). That copies the placeholder length 0 and drops `ArrayLenDependent`. `types.Type` has no field for the length expression (types.bni:113-121), so substitution has nothing it could re-evaluate. The result is `[0]uint8`, hence "cannot assign [8]uint8 to [0]uint8".
- **Array identity ignores the dependent flag.** `Identical` compares only `ArrayLen` and the element (types/types_identical.bn:37, 154). Inside the abstract body, every dependent array is therefore identical to `[0]E` and to every other dependent array with the same element type.
- **Composite literals use the placeholder length.** They check element count against `at.ArrayLen` with no dependent guard (check_expr_composite.bn:209-213, 238). That causes "too many elements".

## 3. Can the checker know the full set of instantiations?

Not today. Nothing records instantiation edges, and constraints on nested calls are proven abstractly: a forwarded `T` satisfies a constraint through its own bound (check_generic.bn:115-122). The existing design assumes that checking a body once covers every instantiation. `DEPENDENT` constants are the first thing that breaks that assumption.

The pieces needed for a closure are partly there:
- The callee decl can be recovered from `ft.TypeParams[i].TpOwner`, which is a bit-cast of the decl (resolve_type.bn:321).
- The argument types at each site in a generic body are known, as `TYP_TYPE_PARAM`s of the enclosing decl.
- Generic *type* instantiations, both abstract and concrete, are all in `c.GenericInstantiations` (check_generic_type.bn:253-258). That list also contains speculative probes (lines 171-180) and does not record which body each came from.

A closure would need three things:
1. a per-decl record of edges, made at `checkInstantiateOrIndex` / `instantiateGenericFunc` for generic calls and for function values like `&F[T]` (check_addr.bn:70-91);
2. uses of generic types, since IR-gen emits every method of a generic type wherever a value of that instantiation has a method called on it or is boxed (irgen/gen_generic_method.bn:88-125);
3. concrete roots, closed by a fixpoint that runs after every package is checked.

In bnc, a single Checker fully checks every loaded package in dependency order (cmd/bnc/compile.bn:32-42). Generic function bodies from a `.bni` are prepended into `merged` (loader/loader_load.bn:249-274), so they get checked in their own package's pass. But a dependency's bodies are checked *before* the consumer's call sites exist, so the fixpoint cannot run inside `CheckPackage`. It has to run once at the end, or be driven lazily from call sites.

bnlint checks non-target packages with `CheckPackageDecls`, which skips bodies (cmd/bnlint/main.bn:285-287; check/checker.bn:187-197, 251-267). Under bnlint, imported generic bodies are never checked.

Today the set is found only by IR-gen. `ensureInstantiated` generates each callee body under `CurrentTypeParamTypes`, discovering nested instantiations recursively (irgen/gen_generic.bn:62-126). `ensureInstantiatedMethods` does the same for methods.

## 4. Generic methods and imported generics

- **Local generic type.** Method signatures are copied onto each instantiation by `copyPlaceholderMethods` → `substituteTypeParams` (check_generic_backfill.bn:44-55), at populate time and again in `backfillInstantiationMethods` (lines 125-133; check_decl.bn:127). They have the same `[0]` / dropped-flag problem as generic functions.
- **Imported generic type.** Method decls are stashed at `.bni` load (bni_scope.bn:141-151). `copyImportedGenericMethods` re-resolves each signature from the AST with the binders bound to the concrete arguments (check_generic_backfill.bn:79-113). A `[sizeof(T)]` parameter therefore gets its real length there. So local and imported generic methods behave differently.
- **Imported generic function.** Its signature is resolved abstractly by `resolveFuncDeclType` (bni_scope.bn:152-154) and substituted at the call. Its body is checked only in its own package's pass, as above.

## 5. Existing per-instantiation work in the checker

- **Generic function call sites:** constraint satisfaction and `requireSizedType` on the substituted signature (check_generic.bn:48-87). This is the "sizing enforced per concrete instantiation" that check_decl.bn:175-181 refers to.
- **Generic types: bodies are fully re-resolved per (decl, args)** by `instantiateGenericDeclWithArgs`, then `populateInstantiatedStruct` / `populateInstantiatedInterface`, with the type parameters bound directly to the concrete arguments (check_generic_type.bn:181-290; check_generic_populate.bn:40-98, 132-171).
  - A `[sizeof(T)]` field of `Buf[int64]` is evaluated concretely by `evalArrayLen`.
  - Constant errors in it are reported by the checker (eval_const_int.bn:54-56), but at the dimension's position in the generic decl, and deduplicated by position.
  - Constraints are checked at user-facing sites; during collection they are deferred and replayed later (check_generic_type.bn:188-227; check_deferred_constraints.bn:9-31; check_decl.bn:127-131).
- **Generic impl matching:** `genericImplSatisfies` substitutes the impl's binders with the instantiation's arguments (types_assignable_iface.bn:240-267).
- **Nesting bound:** `GenericInstDepth` caps nesting for types only (check_generic_type.bn:232-236).

## Separate hazard, likely but not verified: a probable miscompile

`resolveTypeExpr` writes `te.LenVal` / `te.LenKnown` onto the shared AST node whenever the length is known (resolve_type.bn:88-91). Populating a generic struct with a concrete `T` (and resolving an imported generic method's signature, check_generic_backfill.bn:106-107) therefore overwrites the same field-type node once per instantiation; the last one wins. The dependent case, `Buf[T]`, never clears it.

IR-gen rebuilds each generic struct's fields from that AST (irgen/gen_generic_type_inst.bn:217-221) and trusts `LenKnown` (irgen/gen_type_resolve.bn:227-234). So `Buf[int32]` and `Buf[int64]` in the same program would likely share one length. This follows from reading the code; I did not build or run anything to confirm it.

## What I could not determine

- Whether the stale-`LenVal` hazard actually fires.
- How `tryMethodCall` routes calls on instantiated generic types in every case (check_method.bn:19, 76-82 not fully traced).
- Whether IR-gen substitutes or re-resolves the checker's recorded `ExprType`s inside monomorphized bodies (irgen side not traced).
- REPL / interp behaviour: session.bn:191 and check.bn:27-75 call `CheckPackage`, but I did not trace how they handle generics.

## Appendix: how IR-gen monomorphizes today (mapping, 2026-09-28)

# How IR-gen monomorphizes generics, and what a checker-side instantiation pass would need

This report is from reading the code only: nothing was built or run. Paths are relative to `pkg/binate/` in the `temp-binate-4` worktree.

## 1. Discovery and binding

- **No worklist.** Instantiations are found lazily, when IR-gen emits a use site. The call sites are:
  - `genCall` sees an `EXPR_INSTANTIATE_OR_INDEX` head and calls `genCallInstantiate` (`irgen/gen_call.bn:82-113`, `irgen/gen_generic.bn:391-417`).
  - Function values go through `genericFuncInstanceName` (`gen_generic.bn:341-375`), reached from `gen_decl_stmt.bn:131`, `gen_short_var.bn:102` and `gen_util.bn:185`.
  - Also: defer (`gen_defer_build.bn:37`) and method-value receivers (`gen_method_value_recv.bn:250`).
- **Types.** A `TEXPR_INSTANTIATE` in any type expression calls `ensureInstantiatedStruct` (`gen_type_resolve.bn:147-186`, `gen_generic_type_inst.bn:149-239`). Interfaces use `ensureInstantiatedInterface` (`gen_iface.bn:125`, `gen_impl.bn:170`, `gen_iface_registry.bn:186`, `gen_impl_imported.bn:111`).
- **Methods of a generic type** are emitted only when something uses the instance, and then all of them at once. The triggers are a method call, method value, boxing or defer (`gen_method.bn:232`, `gen_method_value.bn:151`, `gen_iface.bn:371`, `gen_util.bn:126,209`, `gen_defer_build.bn:164`). Each goes through `ensureMethodsForInstName`, which emits every method (`gen_generic_method.bn:95-124`). Boxing also creates the generic impl's vtable rows (`gen_iface.bn:372`, `gen_generic_impl.bn:47-145`).
- **Transitive instantiations come from recursion.** `ensureInstantiated` calls `genFunc` on a synthetic decl while the caller's own emission is suspended (`gen_generic.bn:62-149`). Nested type arguments are resolved under the current bindings (`typeArgExprToType` → `resolveTypeExpr`, `gen_generic.bn:191-196`).
- **Dedup** is by mangled name per module (`gc.EmittedInstantiations`, `gen_generic.bn:63-67,153-158`). I found no depth cap, so polymorphic recursion (`F[T]` calling `F[@T]`) would appear to recurse without bound.
- **Binding** uses the parallel lists `gc.CurrentTypeParamNames` / `gc.CurrentTypeParamTypes`, saved and restored around each emission (`gen_generic.bn:73-86,146-147`; `gen_generic_method.bn:151-166`; `gen_generic_type_inst.bn:183-196`; `gen_generic_impl.bn:91-103`). `resolveTypeExpr`'s `TEXPR_NAMED` branch substitutes a bare name that matches a parameter (`gen_type_resolve.bn:91-97`). The defining package and file imports are overlaid as well (`gen_generic.bn:109-118`).

## 2. What IR-gen reuses from the checker, and what it re-derives

**Reused.** The body is emitted with the single, abstract set of checker results: `genFunc(gc, gc.Mod.Checker, synth)` at `gen_generic.bn:116`.
- `ExprType(ResolvedTypeID)`, at 39 sites. These types still contain `TYP_TYPE_PARAM`, so IR-gen only uses them for shape, kind and literal information. Examples: untyped-literal folding (`gen_binary.bn:31`, `gen_util.bn:236`), callee function-value shape (`gen_call.bn:186,222`), `gen_slice.bn:50`, `gen_method.bn:167`.
- Constants:
  - `Checker.ConstValue`, unless it is DEPENDENT (`gen_constval.bn:95-108`).
  - `Checker.DeclConst` (`gen_constval.bn:237-247`).
  - The `IsConstStamp` type stamps (`stampedConstValue`, `gen_constval.bn:203-224`).
  - `irgenEnv.Len` reads the checker's abstract type (`gen_constval.bn:55-71`).
- AST stamps: `TypeExpr.LenVal`/`LenKnown`, trusted without re-evaluation (`gen_type_resolve.bn:227-234`), and `Elem.KeyVal`/`KeyKnown` (`gen_composite.bn:221-222`).

**Re-derived for each instantiation.** Every type expression is re-resolved under the bindings:
- parameters (`irResolveParamType`, `gen_type_resolve.bn:62`; the comment at `:57-58` says IR-gen does not use the checker's function type)
- results, locals and cast targets
- `sizeof`/`alignof` operands (`gen_builtin.bn:254-263`)
- struct fields (`gen_generic_type_inst.bn:215-224`)
- unstamped array lengths, and DEPENDENT constants.

## 3. Where DEPENDENT constants are evaluated, and what happens on error

| Site | Evaluation | On error |
|---|---|---|
| Array dimension with no stamp, `gen_type_resolve.bn:235-251` | `evalConstIR` | `constEvalFailure` panic |
| Local const in a body (`gen_decl_stmt.bn:43` → `genConst` `gen_const.bn:101` → `constFromInitializer` `gen_constval.bn:234-280`) | DeclConst is DEPENDENT, so it falls through to `evalConstIR` (`:270`) | panic (`:278`); a POISONED DeclConst panics too (`:245-246`) |
| Switch case, `gen_flow.bn:378-391` | `exprConstIR` | **Silent:** the case falls back to `genExpr`, i.e. evaluated at run time |
| Array-literal key, `gen_composite.bn:225-232` | `exprConstIR` (only when not `KeyKnown`) | **Silent:** the index stays `nextIdx`. In practice unreachable for DEPENDENT keys, because the checker already rejects them (`check_expr_composite.bn:228-229`) |

- `constEvalFailure` ignores `at`, so the panic carries no position (`gen_constval.bn:287-294`).
- The other `constFromInitializer` callers (`gen_const.bn:164,166`, `gen_import_const.bn:34,36`, `gen_import_consts.bn:30`, `gen_register_import.bn:189`, `gen_repl.bn:173`) handle package-level or imported consts, which cannot be DEPENDENT.
- **A DEPENDENT constant in an ordinary expression is not evaluated as a constant.** `sizeof` produces a `uint` constant (`gen_builtin.bn:257`, checker `check_builtin.bn:244`), and nothing else in `genExpr` consults `ConstValue`. So `cast(uint8, sizeof(T) * 100)` used as an expression appears to be lowered as run-time arithmetic that wraps silently. A DEPENDENT zero divisor becomes a run-time trap, not a compile error. Only the const-declaration and array-length paths panic.

## 4. Root cause of problem (1), in the checker

- `substituteTypeParams` rebuilds an array with `MakeArrayType(sub(elem), t.ArrayLen)` (`check/check_generic.bn:322-324`). That copies the placeholder `0` and drops `ArrayLenDependent`. `types.Type` keeps no link to the length expression (`types.bni:118-122`), so substitution cannot recompute it. `F[int64]`'s parameter therefore becomes `[0]uint8`.
- `checkArrayLit` compares against `at.ArrayLen`, which is `0` (`check_expr_composite.bn:209-213,238`), hence "too many elements".
- Type identity compares only `ArrayLen` (`types_identical.bn:37,154`), so `[sizeof(T)]` and `[alignof(T)]` compare as identical in the abstract.
- **Related latent bug (from reading; not verified by running):** `populateInstantiatedStruct` re-resolves field type expressions with the concrete arguments bound (`check_generic_populate.bn:150-161`). `resolveTypeExpr` then stamps `te.LenVal`/`LenKnown` onto the shared generic AST (`resolve_type.bn:88-91`). IR-gen trusts that stamp (`gen_type_resolve.bn:227-234`), so `S[int8]` and `S[int64]` with a `[sizeof(T)]` field would both get the length from whichever instantiation the checker populated last. `populateInstantiatedInterface` (`:62`) appears to have the same exposure.

## 5. Could the instantiation set be computed before IR-gen?

**Yes, in principle.**
- Type arguments are always explicit; the spec has no type inference (`docs/spec/12-generics-and-enumerations.md:101`).
- bnc runs one checker over every loaded package plus main (`cmd/bnc/compile.bn:31-42`), so it sees every root.
- Generic bodies in a `.bni` are merged into the defining package's decls (`loader/loader_load.bn:247`) and checked once, abstractly (`check_decl_func.bn:387-389`).
- The checker already has the pieces: `typeArgFromExpr`, `substituteTypeParams`, the type-instantiation cache `c.GenericInstantiations` (`check_generic_type.bn:257`, which includes abstract arguments), a depth cap (`GenericInstDepth` 128, `:208-212`), and a precedent for resolving under concrete bindings (`populateInstantiatedStruct`).

**What the checker lacks today:**
1. Any record of generic *function* instantiations. `instantiateGenericFunc` only substitutes the signature (`check_generic.bn:22-89`), and its callers `check_expr_access.bn:28,38` keep nothing.
2. Which generic body each (possibly abstract) instantiation occurs in. That is needed to compute the transitive closure from the concrete roots.
3. IR-gen's rule for which methods get emitted (only those of an instance that is used, and then all of them). Checking every method of every reached type instance would be a superset. Whether an error in the method of a type that is never used should reject the program is a question for you.
4. A recursion bound for function instantiations.
5. **Most important:** a way to check a body per instantiation without overwriting the annotations that the one shared AST carries. `checkExpr` re-registers `ResolvedTypeID` on every check (`check_expr.bn:22`; `registerExprType` resets `ConstVals`/`ConstStats`, `checker.bn:94-98`). `recordDeclConst` overwrites by `ConstID` (`check_constval.bn:159-170`). The `LenVal` and `KeyVal` stamps are last-writer-wins. So the pass must evaluate only, keep per-instantiation side tables, or work on a cloned body.

Things the checker does *not* need: mangled names, per-module deduplication, `IsLinkOnce`, alias-map overlays.

## 6. Existing IR-gen panics of the same class (errors only one instantiation has)

- **`bit_cast` size mismatch:** `gen_builtin.bn:138-143`. From reading: the definition-time check `checkBitCastShapes` (`check_c_interop.bn:316-330`) sizes `TYP_TYPE_PARAM` as a pointer (`types/layout.bn:166`) and a dependent array as `0` (`:142`). It therefore appears to both wrongly reject some valid `bit_cast`s and let invalid ones through to this panic.
- **cast/unsafe_cast through a type parameter:**
  - cast narrowing from an interface: `gen_builtin.bn:75-78`
  - unsafe_cast between two interface values: `:102-106`
  - widening to an interface that produced no value: `:47`, `:114`

  The checker skips all of these when a type parameter is involved (`check_cast_safe.bn:26-32`).
- **Cast shape checks:** mismatched aggregate/scalar kinds at `gen_cast_value.bn:39-44`; same-kind slice/array with different element sizes at `:50-53`.
- **Constant evaluation:** `constEvalFailure` (`gen_constval.bn:287`), and the host-int overflow panic in `constval/eval.bn:161`.
- **Possibly the same class** (they say "the checker should have rejected it"; I did not confirm a type-parameter route): `gen_binary.bn:304,314`, `gen_eq_aggregate.bn:137`, `gen_iface.bn:310,359`.

## Not determined

- Whether the struct-field `LenVal` stamp bug actually reproduces.
- The REPL's incremental path (`gen_repl.bn`): how a checker-side pass would work there.
- Whether duplicate switch cases or out-of-range constant indices / shift counts that are DEPENDENT are meant to be compile errors per instantiation. I found no duplicate-case check in either `check/` or `irgen/`.
- Whether any of the "possibly" panics above can actually be reached through a type parameter.