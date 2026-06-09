# Code-Red-2 Follow-up — bugs NOT covered by the original CR-2 plans

A follow-up to `plan-code-red-2.md` / `plan-cr2-{1,2,3}.md`, covering the open
defects the CR-2 five-class taxonomy did not (the backend ABI/value tail, a
`gen_selector` index-path sibling of Plan-1 Defect 1, a few front-end checker/
const/REPL gaps), plus a cross-package refcount-balance **coverage** sweep.

Every candidate was **source-confirmed against the live tree** while authoring
this plan — and because the assigned workers are fixing fast, **most candidates
were already RESOLVED mid-audit.** What remains is small. Defect-of-record stays
in `claude-todo.md`.

## Confirmation pass — already RESOLVED (move these to `claude-todo-done.md`)

| Bug | Status | Landed |
|---|---|---|
| Native variadic float `__c_call` arg mis-passed | RESOLVED, both natives | `56f09bc6` (SysV `AL=nsrn` + AAPCS64-darwin variadic-stack rule) |
| VM funcval-return-as-arg nil vtable | RESOLVED | `e337e413` (`isVMAddressAggregate` single-return copy-back in `lowerReturn`) |
| Native float consts/returns (`541` reads 0) | RESOLVED | `5281b138` + `cc6d0e9b` (AAPCS64 D0 float-return) + `1285683e` (runtime link) |
| Float-literal converter 1 ULP low | RESOLVED | `58570970` (`ParseFloatLitToBits` via `strconv.ParseFloat`, exact round bit) |
| Named signed-sub-word MIN/-1 divide escapes the guard | RESOLVED in behavior | `b43a0057` (named-distinct-scalar landing; `widenType` preserves named width+sign) — **needs a regression test only** (Plan B) |

Also confirmed RESOLVED from the `2083` audit's extern-var list: cross-pkg
managed-PTR extern value-copy/field-write (`559`/`561`, native-aa64 stale xfails
cleared `c4036777`) and managed-slice extern value-copy balance (`592`).

## The open work — three disjoint sub-plans

Disjoint by subsystem, extending the CR-2 concurrency model:

| Plan | Owns | Open defects |
|---|---|---|
| **A** | `pkg/binate/native` + `pkg/binate/vm` | `box(<scalar>)` native no-emit; dispatch-conflict should be a HARD ERROR (vm) |
| **B** | `pkg/binate/ir` + `pkg/binate/types` + `pkg/binate/repl` | `iota`-in-expressions fold; func-literal flavour inference; REPL parked-member iota; + the named-divide regression test (nested-array field-access moved → plan-cr2-1 Round-2) |
| **C** | `conformance/` only (test) | cross-package managed refcount-balance sweep (C1–C9) |

> **Reassignment note:** the dispatch-conflict diagnostic was originally scoped to
> Plan B (front-end), but its detection site is `pkg/binate/vm/lower.bn` — so it
> moves to **Plan A** (the vm owner). Plan B declined it rather than reach across
> the package boundary.

---

## Plan A — Backend tail (`pkg/binate/native` + `pkg/binate/vm` only)

### Defect: `box(<scalar>)` is unimplemented on both native backends — silent no-emit → garbage
- **Symptom**: `box(i)` for a bare scalar register (not an `OP_ALLOC`, not an aggregate) compiles on LLVM and runs on the VM, but on aa64 + x64 `emitBox` falls to its `else` arm and emits **nothing** — the `OP_BOX` result is undefined → garbage managed pointer. MINOR-severity silent wrong-code.
- **Root cause (confirmed)**: the scalar `else` arm is a deliberate bare `return` on both backends — `pkg/binate/native/aarch64/aarch64_emit.bn:94-98` ("Scalar / unhandled — no emit") and `pkg/binate/native/x64/x64_managed.bn:134-137` ("Scalar / unhandled — silent return"). Only `OP_ALLOC` and `IsAggregateTyp` sources are handled.
- **Fix shape**: implement the scalar arm mirroring the LLVM path (`pkg/binate/codegen/emit_helpers.bn:425-451`): spill the scalar into a frame slot, pass its address (X0/RDI) + size (X1/RSI), `BL`/`CALL` `rt.Box`. Reuse the `OP_ALLOC`-arm frame-slot machinery; hoist the spill slot to the entry block (cf. LLVM `emitBoxAllocDecl`) so a `box()` in a loop doesn't leak native stack per iteration.
- **Files**: `native/aarch64/aarch64_emit.bn` `emitBox`; `native/x64/x64_managed.bn` `emitBox`.
- **Test**: a `conformance` cell `box(i)` returning the boxed value (currently no coverage); runs all modes.

### Defect: dispatch conflict (extern registered + Binate body) silently shadows — should be a HARD ERROR
- **Symptom**: a name with BOTH an extern registration and a Binate body silently uses the Binate body, shadowing the extern with no diagnostic.
- **Root cause (confirmed)**: `pkg/binate/vm/lower.bn` `LowerModule` (~`:187-194`) lowers each non-extern func and `AddFunc`s it **without** checking `vm.LookupExtern(vmf.Name) >= 0` (`LookupExtern` at `vm.bn:335`).
- **Fix shape**: before `AddFunc`, `if vm.LookupExtern(vmf.Name) >= 0 { <hard error: dispatch conflict> }`. (Decide whether the same check belongs at the codegen/native registration sites — but those are other plans' files; the VM site is the confirmed one here.)
- **Files**: `vm/lower.bn` `LowerModule`.
- **Test**: a negative conformance/unit test: a package that both registers an extern and defines the same name → expect the conflict error.

### Disjointness (A)
Owns `pkg/binate/native/{aarch64,x64,common}` and `pkg/binate/vm` only. The three RESOLVED tail defects already live in this same island (`native/{x64,aarch64}`, `vm/lower_instr_helpers.bn`), so no escape. Consumes nothing from Plans B/C.

---

## Plan B — Front-end tail (`pkg/binate/ir` + `pkg/binate/types` + `pkg/binate/repl` only)

### Moved → `plan-cr2-1-frontend.md` Round-2 (nested-array element field access)
The nested-array managed-POINTER field-read (`a[i][j].field`, `a [N][M]@Struct` → literal 0) is the **managed-ptr-read facet** of the broader Round-2 Plan-1 defect "`[N][M]Struct` value-struct field access reads 0 and writes NOWHERE" — the **same root cause** (`getIndexElemType` doesn't recurse a nested-index base, `gen_access.bn`), which also breaks the value-struct variant and the **write** path. Both are front-end (`ir`) ownership, so the **single fix-of-record lives in plan-cr2-1 Round-2**; fix all variants (managed-ptr read, value-struct read, write) together, and have the test cover the `[N][M]@Box` read as one cell of that sweep. No separate item here.

### Defect: checker does not fold `iota` in expressions — bit-flag const compile-time values stay plain-iota
- **Symptom**: `const ( B0 int = 1 << iota; B1; B2 )` — the compile-time *values* of `B1`/`B2` stay plain `iota` (`1,2`) instead of the folded `1<<iota` (`2,4`) when read as compile-time constants (array dims, other const exprs).
- **Root cause**: `pkg/binate/types/check_expr.bn` `checkIdent` iota arm folds bare `iota` but not `iota` inside a binary expression.
- **Fix shape**: fold `iota` within const expressions during the checker's const evaluation (the same evaluator that handles `1 << iota` for runtime values must feed the compile-time value table).
- **Files**: `types/check_expr.bn` (+ the const-fold helpers if shared).
- **Test**: a conformance cell using a bit-flag const as an array dimension / in a const expr.

### Defect: bare func literal in assignment position doesn't infer its managed/raw flavour from the LHS
- **Symptom**: `var f @func() = func() { ... }` resolves the literal as `*func()` (raw) rather than the LHS `@func()` flavour, so the FV-hint isn't applied at a plain assignment (the call-arg / return positions DO apply it — B.3b of plan-function-values-phase-2).
- **Root cause**: `pkg/binate/types/check_stmt.bn` `checkAssignStmt` simple-assign loop doesn't install the LHS type as the function-value hint when checking the RHS literal.
- **Fix shape**: thread `checkExprWithFVHint(rhs, lhsType)` through the simple-assign loop (mirror the call-arg/return sites).
- **Files**: `types/check_stmt.bn` `checkAssignStmt`.
- **Test**: a conformance cell `var f @func() int; f = func() int {…}` (lifetime/flavour correct).

### Defect: REPL parked-member iota-repeat (the `447` adversarial-review leftover, REPL-only)
- **Symptom**: in a REPL `const ( … )` group, a bare member after a *parked* (forward-ref-blocked) explicit member gets plain `iota` instead of repeating the parked member's initializer once it resolves.
- **Root cause (confirmed)**: `genConstGroup` doesn't carry `prevExpr`/`prevTyp` across the parked `continue`; `GenConstMember` (the REPL retry) has no iota-repeat contract.
- **Fix shape**: (1) update `prevExpr`/`prevTyp` from the parked member before `continue`; (2) carry the preceding-member effective expr+type on the parked member's `types.PendingDecl` so the REPL retry (`repl/decl.bn` `GenConstMember(rd, m, iotaIdx)`) repeats it. All in owned packages (`ir/gen_const.bn`, `ir/gen_repl.bn`, `repl/decl.bn`, `types.PendingDecl`).
- **Files**: `ir/gen_const.bn`, `ir/gen_repl.bn`, `repl/decl.bn`, `types` (`PendingDecl` fields).
- **Test**: a REPL/repl-unit test (`repl/decl_test.bn`) — define `fwd` on a later prompt; assert the bare member repeats the parked expression, not plain iota.

### Test-only: named signed-sub-word MIN/-1 divide (RESOLVED in behavior by `b43a0057`)
- Add the missing regression: a `conformance` error-cell `type I8 int8; cast(I8,-128) / cast(I8,-1)` → "integer overflow" panic (cover int8/16/32 named variants), plus a `gen_binary` unit test asserting `widenType(I8,I8)` keeps `typeWidth==8`, `signed==true`. Mark the todo entry RESOLVED with the `b43a0057` cite.

### Disjointness (B)
Owns `pkg/binate/{ir,types,repl}` front-end gen only. Touches no `codegen`/`native`/`vm` file. The only cross-package surface is adding fields to `types.PendingDecl` (also owned). The two out-of-package candidates were declined: the float-literal round-bit is RESOLVED and lives in `native/common`; the dispatch-conflict diagnostic lives in `vm/lower.bn` → Plan A.

---

## Plan C — Cross-package managed refcount-balance coverage sweep (`conformance/` only)

From the `2083` audit: these cross-package managed scenarios **work functionally
but have no refcount-balance assertion**, so a leak (rc stays elevated) or an
extra RefInc/RefDec would slip through. This is a coverage sweep — like the CR-2
matrices, it is expected to *surface* latent refcount bugs, not just add green
cells. Pattern: `rt.Refcount(p)` before/after, mirroring `586`/`592`/`130`.

| Cell | Asserts (refcount returns to baseline crossing a package boundary) | Functional precedent |
|---|---|---|
| **C1** `cross_pkg_managed_slice_elem_store_balance` | `store.S[i] = @v` (extern `@[]@Node`) — element store balances AND the overwritten element is RefDec'd | `558` |
| **C2** `cross_pkg_managed_arg_balance` | `store.Consume(a @Node)` — caller RefInc + callee-scope RefDec net zero | `337`, `595` |
| **C3** `cross_pkg_managed_return_balance` | `store.New() @Node` — return-move arrives rc==1 (no double-retain leak) | `157`/`576`, `554` |
| **C4** `cross_pkg_managed_struct_field_store_balance` | `root.Child = c` (field type cross-pkg `@Node`) — store balances, struct dtor RefDecs the field | `556`, `062`/`270` |
| **C5** `cross_pkg_iface_construct_balance` | `var iv @shape.Shape = h` (imported impl) — box RefIncs receiver, box-drop RefDecs | `382`/`585`, `554`/`567` |
| **C6** `cross_pkg_iface_return_balance` | `shape.Make() @Shape` — iface return arrives rc==1 | `576` |
| **C7** `cross_pkg_generic_typearg_balance` | `genlib.Append[@pkg.T](…)` — managed type-arg lifecycle balances | `497`/`464` |
| **C8** `cross_pkg_extern_field_write` (functional) | field write through an imported value-struct / raw-ptr var (the `561` analogue) | `561` |
| **C9** `cross_pkg_extern_addr_rvalue` (functional) | `&pkg.X` (imported scalar var) — the `551` analogue for imports (**blocked by `551`**) | — |

**Mechanics**: all use the directory form (`main.bn` + `expected` + `pkg/…`),
mirroring `586`/`592`. Pick the next free contiguous numbers **at landing time**
and re-run `scripts/hygiene/run.sh` (`conformance-test-numbers`) on every rebase
— concurrent workers move the frontier. Every cell that imports
`pkg/builtins/rt` **alongside** another `pkg/…` must carry an
`.xfail.builder-comp-int-int` (the pre-existing `136`/`383` int-int rt-loader
bug; balance verified in the other 5 default modes + native; functional behavior
covered mode-agnostically by the precedent) — single-package balance tests
(`554`/`556`/`567`/`595`/`130`) need no such xfail because the loader bug only
bites when `rt` is one of ≥2 imported packages.

### Disjointness (C)
Edits only `conformance/` test files — collides with no compiler-source plan. If
a cell turns red, the *fix* is filed and routed to whichever source plan owns it
(A or B or the CR-2 plans), keeping C purely additive.

---

## Sequencing

Land small, cherry-pick early (stay-close-to-main). Plan A (2 small fixes) and
Plan B (4 fixes + 1 test) are independent and parallel; Plan C is test-only and
can run alongside both — its red cells, if any, feed back as new defects. First
housekeeping step: move the five RESOLVED entries above to
`claude-todo-done.md`.
